/* SPDX-License-Identifier: MIT. Bounded, pure information-request sequencer. */
#include "info.h"
#include <string.h>

static uint16_t u16(const uint8_t *p) { return (uint16_t)p[0] | (uint16_t)p[1] << 8; }
static struct kpd207_info_field *field(struct kpd207_info *i, unsigned n) { return &i->fields[n - 2]; }
static const uint8_t steps[] = {KPD_REVISION, KPD_SOURCE_CAP_EXT, KPD_SOURCE_STATUS,
    KPD_SOURCE_INFO, KPD_MANUFACTURER, KPD_SOURCE_IDENTITY, KPD_PPS_STATUS};
enum { WAIT_READY_MS = 6000, WAIT_EPR_MS = 4000, SETTLE_SPR_MS = 2000, SETTLE_EPR_MS = 250 };

static bool missing_epr(const struct kpd207_sample *p) {
    if (p->epr_length || p->spr_length < 4) return false;
    /* The first fixed 5 V Source PDO advertises EPR Mode Capable at bit 23. */
    uint32_t first = u16(p->spr) | (uint32_t)u16(p->spr + 2) << 16;
    return first >> 30 == 0 && ((first >> 10) & 1023) == 100 && (first & (1u << 23));
}

uint8_t kpd207_info_link(const struct kpd207_capture *c, uint8_t phase, bool native_epr) {
    if (phase != 0x24 || !(c->flags & KPD207_CAPTURE_READY) ||
        native_epr != (c->result.epr_length != 0)) return KPD207_LINK_WAIT;
    if (native_epr) return KPD207_LINK_EPR;
    return missing_epr(&c->result) ? KPD207_LINK_EPR_PENDING : KPD207_LINK_SPR;
}

void kpd207_info_begin(struct kpd207_info *i) {
    /* Ring cursors belong to the adapter, not the PD attachment lifecycle. */
    memset(i->fields, 0, sizeof(i->fields));
    i->stage = KPD207_INFO_IDLE; i->step = 0; i->current = 0;
    i->capture_status = 0; i->attached = true; i->started = false;
    i->frozen = false;
    i->cable_emulation = 0;
}

void kpd207_info_stop(struct kpd207_info *i, uint8_t status) {
    if (!i->attached) return;
    if (i->stage != KPD207_INFO_DONE) {
        if (i->stage == KPD207_INFO_TX || i->stage == KPD207_INFO_REPLY)
            field(i, i->current)->status = status;
        i->capture_status = i->started ? 3 : 0; /* interrupted / unknown */
        i->stage = KPD207_INFO_DONE;
    }
    i->attached = false;
}

static void select_step(struct kpd207_info *i, uint32_t now) {
    if (i->step == sizeof(steps) - 1 && !i->pps) ++i->step;
    if (i->step == sizeof(steps)) {
        i->stage = KPD207_INFO_DONE;
        i->capture_status = 1; /* Finished the applicable request list. */
    } else {
        i->current = steps[i->step]; i->stage = KPD207_INFO_COMMAND; i->since = now;
    }
}

void kpd207_info_tick(struct kpd207_info *i, uint8_t link, bool pps, uint32_t now) {
    if (!i->attached || i->stage == KPD207_INFO_DONE) return;
    if (i->stage == KPD207_INFO_IDLE) {
        i->stage = KPD207_INFO_WAIT_PDO;
        i->startup_since = now;
    }
    uint32_t elapsed = now - i->startup_since;
    /* Some SPR contracts advertise EPR without entering it. Allow a bounded
     * fallback if the native policy remains in Ready with only SPR PDOs. */
    if (link == KPD207_LINK_EPR_PENDING)
        link = elapsed >= WAIT_EPR_MS ? KPD207_LINK_SPR : KPD207_LINK_WAIT;
    if (!i->started && i->stage == KPD207_INFO_COMMAND && link != i->settle_link)
        i->stage = KPD207_INFO_WAIT_PDO;
    if (i->stage == KPD207_INFO_WAIT_PDO || i->stage == KPD207_INFO_SETTLE) {
        if ((link == KPD207_LINK_WAIT && elapsed >= WAIT_READY_MS) ||
            elapsed >= WAIT_READY_MS + SETTLE_SPR_MS) {
            i->stage = KPD207_INFO_DONE; i->capture_status = 2;
        } else if (link == KPD207_LINK_WAIT) {
            i->stage = KPD207_INFO_WAIT_PDO;
        } else if (i->stage == KPD207_INFO_WAIT_PDO || link != i->settle_link) {
            i->stage = KPD207_INFO_SETTLE; i->since = now; i->settle_link = link;
        } else if ((uint32_t)(now - i->since) >=
                   (link == KPD207_LINK_EPR ? SETTLE_EPR_MS : SETTLE_SPR_MS)) {
            i->pps = pps;
            select_step(i, now);
        }
    } else if (i->stage == KPD207_INFO_GUARD && (uint32_t)(now - i->since) >= 150) {
        ++i->step; select_step(i, now);
    } else if ((i->stage == KPD207_INFO_COMMAND || i->stage == KPD207_INFO_TX ||
                i->stage == KPD207_INFO_REPLY) && (uint32_t)(now - i->since) >= 1200) {
        struct kpd207_info_field *f = field(i, i->current);
        if (i->stage != KPD207_INFO_COMMAND)
            f->status = f->status == KPD_PARTIAL ? KPD_PARTIAL :
                i->stage == KPD207_INFO_REPLY ? KPD_NO_RESPONSE : KPD_TX_FAILED;
        /* Stop issuing requests after loss of the native TX or response path. */
        i->capture_status = 2; i->stage = KPD207_INFO_DONE;
    }
}

void kpd207_info_resettle(struct kpd207_info *i, uint8_t status) {
    if (!i->attached || i->stage == KPD207_INFO_DONE) return;
    if (i->started) kpd207_info_stop(i, status);
    else if (i->stage != KPD207_INFO_IDLE) i->stage = KPD207_INFO_WAIT_PDO;
}

static unsigned command(unsigned current, uint8_t out[7]) {
    memset(out, 0, 7);
    out[2] = 0; /* Native policy engine supplies negotiated roles/MessageID. */
    switch (current) {
    case KPD_REVISION: out[1] = 0x98; break;
    case KPD_SOURCE_CAP_EXT: out[1] = 0x91; break;
    case KPD_SOURCE_STATUS: out[1] = 0x92; break;
    case KPD_SOURCE_INFO: out[1] = 0x97; break;
    case KPD_PPS_STATUS: out[1] = 0x94; break;
    case KPD_MANUFACTURER:
        out[1] = 0x86; out[2] = 0x90; return 7; /* Target 0 / Ref 0, native padding. */
    case KPD_SOURCE_IDENTITY:
        out[1] = 0x8f; out[2] = 0x10;
        out[3] = 1; out[4] = 0xa8; out[5] = 0; out[6] = 0xff; return 7;
    default: return 0;
    }
    return 3;
}

unsigned kpd207_info_command(const struct kpd207_info *i, uint8_t out[7]) {
    return i->stage == KPD207_INFO_COMMAND ? command(i->current, out) : 0;
}

void kpd207_info_sent(struct kpd207_info *i, uint32_t now) {
    if (i->stage != KPD207_INFO_COMMAND) return;
    struct kpd207_info_field *f = field(i, i->current);
    memset(f, 0, sizeof(*f));
    f->status = KPD_UNKNOWN; i->stage = KPD207_INFO_TX; i->since = now; i->started = true;
}

static void finish(struct kpd207_info *i, unsigned name, uint8_t status, uint32_t now) {
    struct kpd207_info_field *f = field(i, name);
    if (name == KPD_CABLE_IDENTITY && status == KPD_OBSERVED &&
        (i->cable_emulation == 1 || i->cable_emulation == 2))
        status = KPD_EMULATION_ENABLED;
    bool requested = i->stage == KPD207_INFO_REPLY && i->current == name;
    if (!requested && status == KPD_OBSERVED && f->status == KPD_PASS) return;
    f->status = requested && status == KPD_OBSERVED ? KPD_PASS : status;
    if (requested && status == KPD_INVALID) {
        kpd207_info_stop(i, KPD_INVALID); i->capture_status = 2; return;
    }
    if (requested && status != KPD_PARTIAL) { i->stage = KPD207_INFO_GUARD; i->since = now; }
}

void kpd207_pps_finish(struct kpd207_pps *p, uint8_t status) {
    if (p->stage == KPD207_PPS_IDLE || p->stage == KPD207_PPS_DONE) return;
    p->status = status;
    p->stage = KPD207_PPS_DONE;
}

static void pps_packet(struct kpd207_pps *p, bool tx, uint16_t h, const uint8_t *data, uint32_t now) {
    unsigned count = (h >> 12) & 7, type = h & 31;
    if (p->stage != KPD207_PPS_TX && p->stage != KPD207_PPS_REPLY) return;
    if (!count && type == 13) { kpd207_pps_finish(p, KPD_RESET); return; }
    if (p->stage == KPD207_PPS_TX) {
        if (tx && (h & 0xf11f) == 20) {
            p->stage = KPD207_PPS_REPLY; p->since = now;
        }
        return;
    }
    if (tx || !(h & 0x100)) return;
    if ((h & 0x801f) == 0x800c) {
        if (count != 2 || (u16(data) & 0x7fff) != 4 || (data[5] & 0xf1)) {
            kpd207_pps_finish(p, KPD_INVALID); return;
        }
        memcpy(p->reply, data + 2, 4);
        kpd207_pps_finish(p, KPD_PASS);
    } else if (!count && type == 16) kpd207_pps_finish(p, KPD_UNSUPPORTED);
    else if (!count && type == 4) kpd207_pps_finish(p, KPD_REJECTED);
    else if (!count && type == 12) kpd207_pps_finish(p, KPD_BUSY);
}

static void packet(struct kpd207_info *i, bool tx, uint8_t sop, const uint8_t *w, unsigned n, uint32_t now) {
    if (n < 2 || sop > 1) return;
    uint16_t h = u16(w); unsigned count = (h >> 12) & 7, type = h & 31;
    if (n != 2 + count * 4) return;
    if (!sop) pps_packet(&i->manual_pps, tx, h, w + 2, now);
    if (!i->attached || i->stage == KPD207_INFO_DONE) return;
    if (sop == 0 && !count && type == 13) { kpd207_info_resettle(i, KPD_RESET); return; }
    const uint8_t *p = w + 2;
    if (tx) {
        if (sop == 1 && count == 1 && type == 15 && !(h & 0x8000) &&
            u16(p + 2) == 0xff00 && (u16(p) & 0x80df) == 0x8001) {
            struct kpd207_info_field *f = field(i, KPD_CABLE_IDENTITY);
            if (!f->present) f->status = KPD_NO_RESPONSE; /* Actual native cable request observed. */
        }
        if (sop || i->stage != KPD207_INFO_TX) return;
        uint8_t cmd[7]; unsigned len = command(i->current, cmd);
        if (!len || type != (cmd[1] & 31)) return;
        i->tx_length = (uint8_t)n; memcpy(i->tx_wire, w, n);
        bool good = i->current == KPD_MANUFACTURER ?
            (h & 0x8000) && count == 2 && !(u16(p) & 0x7e00) &&
                ((u16(p) & 511) == 2 || (u16(p) & 511) == 4) &&
                p[2] == 0 && p[3] == 0 && ((u16(p) & 511) != 4 || (p[4] == 0 && p[5] == 0)) :
            i->current == KPD_SOURCE_IDENTITY ? !(h & 0x8000) && count == 1 && memcmp(p, cmd + 3, 4) == 0 :
            !(h & 0x8000) && count == 0;
        if (!good) { kpd207_info_stop(i, KPD_TX_FAILED); i->capture_status = 2; return; }
        i->stage = KPD207_INFO_REPLY; i->since = now; return;
    }
    if (sop == 0 && !(h & 0x100)) return;
    if (!count) {
        uint8_t status = type == 4 ? KPD_REJECTED : type == 12 ? KPD_BUSY :
            type == 16 ? KPD_UNSUPPORTED : KPD_NOT_REQUESTED;
        if (!sop && status && i->stage == KPD207_INFO_REPLY) finish(i, i->current, status, now);
        return;
    }
    unsigned name = 0;
    if (h & 0x8000) {
        if (sop) return;
        switch (type) {
        case 1: name = KPD_SOURCE_CAP_EXT; break; case 2: name = KPD_SOURCE_STATUS; break;
        case 7: name = KPD_MANUFACTURER; break; case 12: name = KPD_PPS_STATUS; break;
        default: return;
        }
        unsigned ext = u16(p), total = ext & 511, number = (ext >> 11) & 15;
        bool chunked = (ext & 0x8000) != 0;
        if (ext & 0x400) return; /* Request_Chunk is not response data. */
        struct kpd207_info_field *f = field(i, name);
        if ((ext & 0x200) || !total || total > KPD_RAW_MAX || (!chunked && (number || total > 26)) ||
            number * 26 >= total) { finish(i, name, KPD_INVALID, now); return; }
        unsigned offset = chunked ? number * 26 : 0, needed = total - offset;
        if (needed > 26) needed = 26;
        if (count * 4 < needed + 2 || count * 4 - needed - 2 > 3) {
            finish(i, name, KPD_INVALID, now); return;
        }
        if (number == 0 && (!f->chunks || f->total != total || memcmp(f->raw, p + 2, needed))) {
            f->chunks = 0; f->total = (uint16_t)total; f->present = 0; f->length = 0;
        }
        if (f->total != total || (!f->chunks && number)) { finish(i, name, KPD_PARTIAL, now); return; }
        if ((f->chunks & (1u << number)) && memcmp(f->raw + offset, p + 2, needed)) {
            f->chunks = 0; finish(i, name, KPD_INVALID, now); return;
        }
        memcpy(f->raw + offset, p + 2, needed); f->chunks |= (uint16_t)(1u << number);
        if (f->chunks != (1u << ((total + 25) / 26)) - 1) { finish(i, name, KPD_PARTIAL, now); return; }
        unsigned minimum = name == KPD_SOURCE_CAP_EXT ? 24 : name == KPD_SOURCE_STATUS ? 7 : 4;
        f->length = (uint16_t)total; f->present = 1;
        finish(i, name, total >= minimum ? KPD_OBSERVED : KPD_INVALID, now);
    } else if (!sop && (type == 11 || type == 12)) {
        name = type == 11 ? KPD_SOURCE_INFO : KPD_REVISION;
        struct kpd207_info_field *f = field(i, name);
        if (count != 1) { finish(i, name, KPD_INVALID, now); return; }
        memcpy(f->raw, p, 4); f->length = 4; f->present = 1; finish(i, name, KPD_OBSERVED, now);
    } else if (type == 15 && u16(p + 2) == 0xff00 && (u16(p) & 0x801f) == 0x8001) {
        name = sop ? KPD_CABLE_IDENTITY : KPD_SOURCE_IDENTITY;
        unsigned reply = (p[0] >> 6) & 3;
        if (reply == 2 || reply == 3) { finish(i, name, reply == 2 ? KPD_UNSUPPORTED : KPD_BUSY, now); return; }
        if (reply != 1) return;
        struct kpd207_info_field *f = field(i, name);
        memcpy(f->raw, p, count * 4); f->length = (uint16_t)(count * 4); f->present = 1;
        finish(i, name, count >= (sop ? 5u : 4u) ? KPD_OBSERVED : KPD_INVALID, now);
    }
}

bool kpd207_info_records(struct kpd207_info *i, const uint8_t *b, unsigned n, uint32_t now) {
    unsigned pos = 0;
    while (pos < n) {
        uint8_t flag = b[pos]; unsigned len = (flag & 63) + 1;
        if (len > n - pos) return false;
        if ((flag & 0x80) && len >= 8 && len <= 36)
            packet(i, (flag & 0x40) != 0, b[pos + 5], b + pos + 6, len - 6, now);
        else if ((flag == 5 || flag == 0x45) && len == 6) {
            uint8_t code = b[pos + 5];
            if (code == 0x22 || code == 0x04 || code == 0x05)
                kpd207_pps_finish(&i->manual_pps, KPD_RESET);
            if (code == 0x22) kpd207_info_stop(i, KPD_RESET);
            else if (code == 0x04 || code == 0x05) kpd207_info_resettle(i, KPD_RESET);
        } else return false;
        pos += len;
    }
    return true;
}

bool kpd207_pps_freeze(const struct kpd207_pps *p, struct kpd_transfer *out) {
    if (p->stage != KPD207_PPS_DONE) return false;
    bool received = p->status == KPD_PASS;
    uint8_t raw[8];
    for (unsigned i = 0; i < 4; ++i) raw[i] = (uint8_t)(p->rdo >> (8 * i));
    if (received) memcpy(raw + 4, p->reply, 4);
    struct kpd_snapshot snapshot = {.origin = KPD_KM003C_PPS, .capture_status = 1};
    memcpy(snapshot.id, p->id, 8);
    snapshot.fields[KPD_PPS_STATUS] = (struct kpd_field){raw, received ? 8 : 4, p->status, true};
    return kpd_freeze(&snapshot, out);
}

void kpd207_info_hold(struct kpd207_info *i, const struct kpd207_capture *c, uint32_t now) {
    if (i->stage != KPD207_INFO_DONE || !(c->flags & KPD207_CAPTURE_READY)) return;
    if (i->frozen) {
        /* The native scan can reach EPR after the extra requests have stopped.
         * Promote only an active scan's stable EPR copy with the same SPR PDOs.
         * Keep the full copy when the native scan later returns to SPR. */
        if (i->pdos.epr_length || !c->result.epr_length || c->previous_phase != 0x24 ||
            i->pdos.spr_length != c->result.spr_length ||
            memcmp(i->pdos.spr, c->result.spr, i->pdos.spr_length)) return;
    }
    i->pdos = c->result;
    memcpy(i->id, c->id, 8);
    i->frozen = true;
    i->since = now; /* In DONE, since is the start of the QR's EPR grace period. */
}

bool kpd207_info_waiting_epr(const struct kpd207_info *i, const struct kpd207_capture *c, uint32_t now) {
    return i->frozen && c->previous_phase && missing_epr(&i->pdos) &&
        (uint32_t)(now - i->since) < 6000;
}

bool kpd207_info_freeze(const struct kpd207_capture *c, const struct kpd207_info *i, struct kpd_transfer *out) {
    if (!i->frozen && !(c->flags & KPD207_CAPTURE_READY)) return false;
    const struct kpd207_sample *p = i->frozen ? &i->pdos : &c->result;
    struct kpd_snapshot snapshot = {0};
    memcpy(snapshot.id, i->frozen ? i->id : c->id, 8); snapshot.capture_status = i->capture_status;
    snapshot.fields[KPD_SPR_PDOS] = (struct kpd_field){p->spr, p->spr_length, KPD_OBSERVED, true};
    if (p->epr_length)
        snapshot.fields[KPD_EPR_PDOS] = (struct kpd_field){p->epr, p->epr_length, KPD_OBSERVED, true};
    else if (missing_epr(p))
        snapshot.fields[KPD_EPR_PDOS].status = KPD_PARTIAL;
    for (unsigned n = 2; n < KPD_FIELDS; ++n) {
        const struct kpd207_info_field *f = &i->fields[n - 2];
        snapshot.fields[n] = (struct kpd_field){f->raw, f->length, f->status, f->present != 0};
    }
    return kpd_freeze(&snapshot, out);
}
