/* SPDX-License-Identifier: MIT. Exact HW2.3/stock-2.0.7 read-only PD adapter. */
#include "acquire.h"
#include <string.h>
#define REG(address) (*(volatile uint32_t *)(uintptr_t)(address))
#define RNG 0x40041000u
#define FCG 0x40048000u
#define RNG_CLOCK (1u << 22)

static void clock_rng(bool enable) {
    /* Native FCG0 helper unlocks, changes this mask, then locks FCG0 again. */
    ((void (*)(uint32_t, uint32_t))(uintptr_t)0x970d)(RNG_CLOCK, enable);
}

static void random_tick(struct kpd207_capture *c, struct kpd207_random *r, uint32_t now) {
    if (r->phase >= 2) return;
    if (r->phase == 0) {
        /* Only own a peripheral which was clock-gated. Never borrow a running
         * native RNG, or initialize one during an active PD operation. */
        if (*(volatile const uint8_t *)0x2000002du != 0) return;
        if (!(REG(FCG) & RNG_CLOCK)) { r->phase = 3; return; }
        clock_rng(true);
        if (REG(FCG) & RNG_CLOCK) { r->phase = 3; return; }
        if (REG(RNG) != 0) { clock_rng(false); r->phase = 3; return; }
        r->mode = REG(RNG + 4);
        REG(RNG + 4) = 0x19; /* 256 noise shifts, reload enabled (vendor DDL). */
        REG(RNG) = 1; /* Enable before starting, as in the vendor DDL. */
        REG(RNG) = 3; /* No interrupt or busy wait. */
        r->phase = 1;
        r->since = now;
        return;
    }
    if (REG(RNG) & 2) {
        if ((uint32_t)(now - r->since) < 100) return;
        r->phase = 3;
    } else {
        uint32_t words[2] = {REG(RNG + 12), REG(RNG + 16)};
        if (++r->discarded <= 10) {
            /* Read and discard ten initial 64-bit results, as in vendor DDL. */
            REG(RNG) = 3;
            r->since = now;
            return;
        }
        r->phase = 3;
        if ((words[0] || words[1]) && (words[0] != UINT32_MAX || words[1] != UINT32_MAX)) {
            words[0] ^= 0x55555555u; words[1] ^= 0x55555555u;
            kpd207_capture_seed(c, (const uint8_t *)words);
            r->phase = 2;
        }
    }
    REG(RNG) = 0;
    REG(RNG + 4) = r->mode;
    bool restored = REG(RNG) == 0 && REG(RNG + 4) == r->mode;
    clock_rng(false);
    if (!restored || !(REG(FCG) & RNG_CLOCK)) {
        c->flags &= (uint8_t)~KPD207_SEEDED;
        r->phase = 3;
    }
}

static uint16_t u16(volatile const uint8_t *p) {
    return (uint16_t)p[0] | (uint16_t)p[1] << 8;
}

static void sample(struct kpd207_sample *s) {
    volatile const uint8_t *p = (volatile const uint8_t *)0x20000004u;
    memset(s, 0, sizeof(*s));
    uint8_t mode = p[0];
    s->phase = mode == 0 ? p[0x29] : 0;
    s->sequence[0] = p[0x3d6]; s->sequence[1] = p[0x3d7];
    if (mode != 0) return; /* Cable-only/other native modes are not PD sessions. */
    uint8_t flags = p[0x3d5];
    bool epr = (flags & 8) != 0;
    uint16_t header = u16(p + (epr ? 0x10c : 0x4e));
    unsigned n = (header >> 12) & 7;
    uint16_t extended = u16(p + 0x52);
    unsigned bytes = extended & 511;
    if ((header & 0x801f) != 1 || !n ||
        (epr && (bytes <= 28 || bytes > 44 || bytes % 4))) return;
    s->spr_length = (uint8_t)(n * 4);
    s->epr_length = epr ? (uint8_t)bytes : 0;
    for (unsigned i = 0; i < s->spr_length; ++i) s->spr[i] = p[(epr ? 0xf0 : 0xa8) + i];
    for (unsigned i = 0; i < s->epr_length; ++i) s->epr[i] = p[0xa8 + i];
    s->valid = mode == p[0] && s->phase == p[0x29] && flags == p[0x3d5] &&
        header == u16(p + (epr ? 0x10c : 0x4e)) && extended == u16(p + 0x52) &&
        s->sequence[0] == p[0x3d6] && s->sequence[1] == p[0x3d7];
}

void kpd207_acquire_tick(struct kpd207_capture *c, struct kpd207_random *r, uint32_t now) {
    random_tick(c, r, now);
    struct kpd207_sample observation;
    sample(&observation);
    /* Two equal observations at least 20 ms apart qualify the native cache.
     * No IRQ mask/scheduler lock is held, and QR work uses only the owned copy. */
    kpd207_capture_observe(c, &observation, now);
}

/* Observe the native logger without changing its USB consumer cursor. Its
 * cumulative byte count is uint16_t; our independent physical cursor survives
 * that rollover. A complete record is committed only when count and position
 * agree, and all metadata is rechecked after the bounded copy. */
static int read_ring(uint16_t *previous_total, uint16_t *previous_position,
                     bool *ready, uint8_t bytes[320]) {
    volatile const uint8_t *p = (volatile const uint8_t *)0x20000004u;
    if (p[0x3d9] & 1) return 0;
    uint16_t total = u16(p + 0x780), position = u16(p + 0x77c);
    if (position >= 320) return 0;
    if (!*ready) {
        *previous_total = total; *previous_position = position; *ready = true; return 0;
    }
    unsigned delta = (uint16_t)(total - *previous_total);
    if (!delta) return 0;
    if (delta > 320) {
        *previous_total = total; *previous_position = position; return -1;
    }
    if ((*previous_position + delta) % 320 != position) return 0; /* In-progress writer. */
    unsigned cursor = *previous_position;
    for (unsigned n = 0; n < delta; ++n) {
        bytes[n] = p[0x63a + cursor];
        if (++cursor == 320) cursor = 0;
    }
    if ((p[0x3d9] & 1) || total != u16(p + 0x780) || position != u16(p + 0x77c)) return 0;
    *previous_total = total; *previous_position = position;
    return (int)delta;
}

static void records(struct kpd207_info *i, uint32_t now) {
    uint8_t bytes[320];
    int delta = read_ring(&i->ring_total, &i->ring_position, &i->ring_ready, bytes);
    if (delta < 0 || (delta > 0 && !kpd207_info_records(i, bytes, (unsigned)delta, now)))
        kpd207_info_stop(i, KPD_INVALID);
}

__attribute__((noinline))
bool kpd207_cable_tick(struct kpd207_cable *cable, struct kpd207_capture *capture) {
    volatile const uint8_t *p = (volatile const uint8_t *)0x20000004u;
    /* Mode 5 is entered by the stock USB-C eMarker menu (0x2241c).
     * Baseline on entry so old PD or emulation records cannot seed a scan. */
    bool active = p[0] == 5;
    if (active != cable->active) {
        cable->active = active;
        cable->requested = cable->saved = false;
        cable->ring_ready = false;
    }
    if (!active || cable->saved) return false;

    uint8_t bytes[320];
    int delta = read_ring(&cable->ring_total, &cable->ring_position, &cable->ring_ready, bytes);
    if (delta < 0 || p[0] != 5) cable->requested = false;
    if (delta <= 0 || p[0] != 5) return false;
    const uint8_t *identity = NULL;
    unsigned length = 0;
    for (unsigned pos = 0; pos < (unsigned)delta;) {
        const uint8_t *record = bytes + pos;
        unsigned len = (record[0] & 63) + 1;
        if (len > (unsigned)delta - pos || len < 6) {
            cable->requested = false;
            return false;
        }
        pos += len;
        if (!(record[0] & 0x80) || len < 12 || record[5] != 1) continue; /* SOP′ */
        uint16_t header = u16(record + 6);
        unsigned count = (header >> 12) & 7;
        if (len != 8 + 4 * count || (header & 0x801f) != 15 || u16(record + 10) != 0xff00)
            continue;
        unsigned command = u16(record + 8) & 0x87df;
        if ((record[0] & 0x40) && count == 1 && command == 0x8001)
            cable->requested = true;
        else if (!(record[0] & 0x40) && cable->requested && command == 0x8041 && count >= 5) {
            unsigned product = (u16(record + 14) >> 11) & 7;
            if (product != 3 && product != 4) continue; /* Passive or active cable plug. */
            identity = record + 8;
            length = count * 4;
        }
    }
    if (!identity || !kpd207_capture_take_id(capture, cable->id)) return false;
    memcpy(cable->raw, identity, length);
    cable->length = (uint8_t)length;
    cable->saved = true;
    return true;
}

bool kpd207_cable_freeze(const struct kpd207_cable *cable, struct kpd_transfer *out) {
    if (!cable->length) return false;
    struct kpd_snapshot snapshot = {.origin = KPD_KM003C_CABLE, .capture_status = 1};
    memcpy(snapshot.id, cable->id, 8);
    snapshot.fields[KPD_CABLE_IDENTITY] = (struct kpd_field){cable->raw, cable->length, KPD_OBSERVED, true};
    return kpd_freeze(&snapshot, out);
}

__attribute__((noinline))
void kpd207_information_tick(struct kpd207_info *i, const struct kpd207_capture *c, uint32_t now) {
    volatile const uint8_t *p = (volatile const uint8_t *)0x20000004u;
    uint8_t phase = p[0x29];
    if (!phase || p[0] != 0) {
        if (i->attached) kpd207_info_stop(i, KPD_RESET);
        kpd207_info_hold(i, c);
        /* Arm a new attachment without clearing the retained completed result. */
        i->stage = KPD207_INFO_IDLE;
        i->ring_ready = false; records(i, now);
        return;
    }
    if (!i->attached && i->stage == KPD207_INFO_IDLE) {
        kpd207_info_begin(i);
        i->cable_emulation = p[3] & 7;
    }
    records(i, now);
    bool ready = phase == 0x24 && (c->flags & KPD207_CAPTURE_READY);
    unsigned selected = p[3] >> 4;
    bool pps = false;
    if (selected && selected <= 11) {
        const volatile uint8_t *raw = p + 0xa8 + (selected - 1) * 4;
        uint32_t pdo = (uint32_t)u16(raw) | (uint32_t)u16(raw + 2) << 16;
        pps = (pdo >> 28) == 12; /* Actual selected native PDO is PPS APDO. */
    }
    kpd207_info_tick(i, ready, pps, now);
    /* Native reset may receive new SPR-only PDOs before it reaches idle.
     * Completed information keeps the PDOs and ID from completion time. */
    kpd207_info_hold(i, c);
    if (!ready || i->stage != KPD207_INFO_COMMAND || (p[0x27] & 5)) return;
    uint8_t command[7];
    unsigned length = kpd207_info_command(i, command);
    if (!length) return;
    kpd207_info_sent(i, now);
    /* Same asynchronous enqueue used by stock 'pd data='. It copies the bytes
     * immediately; native policy/PHY retain MessageID, GoodCRC and bus ownership.
     * Do not call its raw PHY transmitter or alter a negotiated contract. */
    ((void (*)(const uint8_t *, unsigned))(uintptr_t)0x3f669)(command, length);
}
