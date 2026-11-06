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

static uint32_t u32(volatile const uint8_t *p) {
    return u16(p) | (uint32_t)u16(p + 2) << 16;
}

bool kpd207_pps_selected(void) {
    volatile const uint8_t *p = (volatile const uint8_t *)0x20000004u;
    unsigned selected = p[3] >> 4;
    if (p[0] != 0 || !selected || selected > 7) return false;
    /* The original SPR list moves to +0xf0 while the native policy is in EPR. */
    bool epr = (p[0x3d5] & 8) != 0;
    uint16_t header = u16(p + (epr ? 0x10c : 0x4e));
    return (header & 0x801f) == 1 && selected <= (header >> 12 & 7) &&
        u32(p + (epr ? 0xf0 : 0xa8) + (selected - 1) * 4) >> 28 == 12;
}

/* The native Request path copies the wire RDO to +0x118. +0x114 is
 * the target RDO, which can change before the policy engine sends it. */
uint32_t kpd207_pps_contract(void) {
    volatile const uint8_t *p = (volatile const uint8_t *)0x20000004u;
    unsigned selected = p[3] >> 4;
    uint8_t phase = p[0x29], flags = p[0x3d5];
    if (p[0] != 0 || phase != 0x24 || flags & 8 || !selected || selected > 7)
        return 0;
    uint16_t header = u16(p + 0x4e);
    if ((header & 0x801f) != 1 || selected > (header >> 12 & 7)) return 0;
    const volatile uint8_t *entry = p + 0xa8 + (selected - 1) * 4;
    uint32_t pdo = u32(entry), rdo = u32(p + 0x118);
    unsigned voltage = (rdo >> 9) & 2047, current = rdo & 127;
    if (pdo >> 28 != 12 || rdo >> 28 != selected ||
        voltage < ((pdo >> 8) & 255) * 5 || voltage > ((pdo >> 17) & 255) * 5 ||
        !current || current > (pdo & 127)) return 0;
    if (p[0] != 0 || p[0x29] != phase || p[0x3d5] != flags ||
        p[3] >> 4 != selected || u16(p + 0x4e) != header || u32(entry) != pdo ||
        u32(p + 0x114) != rdo || u32(p + 0x118) != rdo) return 0;
    return rdo;
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
static void records(struct kpd207_info *i, uint32_t now) {
    volatile const uint8_t *p = (volatile const uint8_t *)0x20000004u;
    if (p[0x3d9] & 1) return;
    uint16_t total = u16(p + 0x780), position = u16(p + 0x77c);
    if (position >= 320) return;
    if (!i->ring_ready) {
        i->ring_total = total; i->ring_position = position; i->ring_ready = true; return;
    }
    unsigned delta = (uint16_t)(total - i->ring_total);
    if (!delta) return;
    if (delta > 320) {
        kpd207_pps_finish(&i->manual_pps, KPD_INVALID);
        kpd207_info_resettle(i, KPD_INVALID);
        i->ring_total = total; i->ring_position = position; return;
    }
    if ((i->ring_position + delta) % 320 != position) return; /* In-progress writer. */
    uint8_t bytes[320];
    unsigned cursor = i->ring_position;
    for (unsigned n = 0; n < delta; ++n) {
        bytes[n] = p[0x63a + cursor];
        if (++cursor == 320) cursor = 0;
    }
    if ((p[0x3d9] & 1) || total != u16(p + 0x780) || position != u16(p + 0x77c)) return;
    i->ring_total = total; i->ring_position = position;
    if (!kpd207_info_records(i, bytes, delta, now)) {
        kpd207_pps_finish(&i->manual_pps, KPD_INVALID);
        kpd207_info_stop(i, KPD_INVALID);
    }
}

static void enqueue(const uint8_t *command, unsigned length) {
    /* Same asynchronous enqueue as stock 'pd data='. Native owns the PHY. */
    ((void (*)(const uint8_t *, unsigned))(uintptr_t)0x3f669)(command, length);
}

__attribute__((noinline))
void kpd207_information_tick(struct kpd207_info *i, const struct kpd207_capture *c, uint32_t now) {
    volatile const uint8_t *p = (volatile const uint8_t *)0x20000004u;
    struct kpd207_pps *manual = &i->manual_pps;
    uint8_t phase = p[0x29];
    if (!phase || p[0] != 0) {
        kpd207_pps_finish(manual, KPD_RESET);
        if (i->attached) kpd207_info_stop(i, KPD_RESET);
        kpd207_info_hold(i, c, now);
        /* Arm a new attachment without clearing the retained completed result. */
        i->stage = KPD207_INFO_IDLE;
        i->ring_ready = false; records(i, now);
        return;
    }
    if (!i->attached && i->stage == KPD207_INFO_IDLE) {
        kpd207_info_begin(i);
        i->cable_emulation = p[3] & 7;
    }
    if (manual->stage != KPD207_PPS_IDLE && manual->stage != KPD207_PPS_DONE &&
        ((p[0x3d5] & 8) || p[3] >> 4 != manual->rdo >> 28 ||
         u32(p + 0x114) != manual->rdo || u32(p + 0x118) != manual->rdo))
        kpd207_pps_finish(manual, KPD_RESET);
    records(i, now);
    uint8_t link = kpd207_info_link(c, phase, (p[0x3d5] & 8) != 0);
    unsigned selected = p[3] >> 4;
    bool pps = false;
    if (selected && selected <= 11) {
        const volatile uint8_t *raw = p + 0xa8 + (selected - 1) * 4;
        uint32_t pdo = (uint32_t)u16(raw) | (uint32_t)u16(raw + 2) << 16;
        pps = (pdo >> 28) == 12; /* Actual selected native PDO is PPS APDO. */
    }
    bool busy = i->stage == KPD207_INFO_TX || i->stage == KPD207_INFO_REPLY;
    if (manual->stage == KPD207_PPS_IDLE || manual->stage == KPD207_PPS_DONE || busy)
        kpd207_info_tick(i, link, pps, now);
    /* Native reset may receive new SPR-only PDOs before it reaches idle.
     * Completed information keeps the PDOs and ID from completion time. */
    kpd207_info_hold(i, c, now);
    if (manual->stage == KPD207_PPS_COMMAND) {
        if ((uint32_t)(now - manual->since) >= 2500) kpd207_pps_finish(manual, KPD_TX_FAILED);
        else if (!busy && !(p[0x27] & 5) && kpd207_pps_contract() == manual->rdo) {
            const uint8_t command[] = {0, 0x94, 0};
            manual->stage = KPD207_PPS_TX;
            manual->since = now;
            enqueue(command, sizeof(command));
        }
        return;
    }
    if (manual->stage == KPD207_PPS_TX || manual->stage == KPD207_PPS_REPLY) {
        if ((uint32_t)(now - manual->since) >= 1200)
            kpd207_pps_finish(manual, manual->stage == KPD207_PPS_TX ? KPD_TX_FAILED : KPD_NO_RESPONSE);
        return;
    }
    if (link == KPD207_LINK_EPR_PENDING) link = KPD207_LINK_SPR;
    if (link != i->settle_link || i->stage != KPD207_INFO_COMMAND ||
        p[0] != 0 || p[0x29] != 0x24 || (p[0x27] & 5)) return;
    uint8_t command[7];
    unsigned length = kpd207_info_command(i, command);
    if (!length) return;
    kpd207_info_sent(i, now);
    enqueue(command, length);
}
