/* SPDX-License-Identifier: MIT. Pure capture lifecycle, no hardware/heap/IRQ. */
#include "capture.h"
#include <string.h>

static bool equal(const void *a, const void *b, size_t n) {
    const uint8_t *x = a, *y = b;
    for (size_t i = 0; i < n; ++i) if (x[i] != y[i]) return false;
    return true;
}

void kpd207_capture_seed(struct kpd207_capture *c, const uint8_t seed[8]) {
    memcpy(c->next_id, seed, 8);
    c->flags |= KPD207_SEEDED;
}

bool kpd207_capture_take_id(struct kpd207_capture *c, uint8_t id[8]) {
    if (!(c->flags & KPD207_SEEDED)) return false;
    memcpy(id, c->next_id, 8);
    /* Random boot seed plus a counter. Disable allocation on wrap. */
    for (unsigned i = 8; i != 0; --i) {
        if (++c->next_id[i - 1] != 0) break;
        if (i == 1) c->flags &= (uint8_t)~KPD207_SEEDED;
    }
    return true;
}

void kpd207_capture_observe(struct kpd207_capture *c, const struct kpd207_sample *s, uint32_t now) {
    uint16_t sequence = (uint16_t)s->sequence[0] | (uint16_t)s->sequence[1] << 8;
    if (!(c->flags & KPD207_INITIALIZED)) {
        c->flags |= KPD207_INITIALIZED;
        c->idle_sequence = sequence;
        c->previous_phase = s->phase;
        return; /* Never adopt a cache already present when the observer starts. */
    }
    if (s->phase == 0) {
        c->idle_sequence = sequence;
        c->flags &= (uint8_t)~KPD207_CANDIDATE;
    } else {
        if (c->previous_phase == 0)
            c->flags &= (uint8_t)~KPD207_CAPTURE_READY;
        if (s->phase != 0x24 || !s->valid || !sequence || sequence == c->idle_sequence ||
            !(c->flags & KPD207_SEEDED) || s->spr_length == 0 || s->spr_length > 28 ||
            s->spr_length % 4 || s->epr_length > 44 || s->epr_length % 4) {
            c->flags &= (uint8_t)~KPD207_CANDIDATE;
        } else if ((c->flags & KPD207_CAPTURE_READY) &&
                   equal(s->sequence, c->result.sequence, 2)) {
            /* Native protocol transitions can edit cache/flags without a new
             * Source_Capabilities receive. Keep the owned result in that case. */
            c->flags &= (uint8_t)~KPD207_CANDIDATE;
        } else if (!(c->flags & KPD207_CANDIDATE) || !equal(s, &c->candidate, sizeof(*s))) {
            c->candidate = *s;
            c->candidate_since = now;
            c->flags |= KPD207_CANDIDATE;
        } else if ((uint32_t)(now - c->candidate_since) >= 20) {
            if (!(c->flags & KPD207_CAPTURE_READY) ||
                !equal(&s->spr_length, &c->result.spr_length, sizeof(*s) - 4)) {
                c->result = *s;
                (void)kpd207_capture_take_id(c, c->id);
                c->flags |= KPD207_CAPTURE_READY;
            }
            /* Consume even an identical-payload receive, without changing ID. */
            memcpy(c->result.sequence, s->sequence, 2);
        }
    }
    c->previous_phase = s->phase;
}

bool kpd207_capture_freeze(const struct kpd207_capture *c, struct kpd_transfer *transfer) {
    if (!(c->flags & KPD207_CAPTURE_READY)) return false;
    struct kpd_snapshot snapshot = {0};
    memcpy(snapshot.id, c->id, 8);
    snapshot.fields[KPD_SPR_PDOS] = (struct kpd_field){c->result.spr, c->result.spr_length, KPD_OBSERVED, true};
    if (c->result.epr_length)
        snapshot.fields[KPD_EPR_PDOS] = (struct kpd_field){c->result.epr, c->result.epr_length, KPD_OBSERVED, true};
    return kpd_freeze(&snapshot, transfer);
}
