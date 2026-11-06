/* SPDX-License-Identifier: MIT */
#ifndef KPD207_CAPTURE_H
#define KPD207_CAPTURE_H
#include "kpd.h"

/* Read-only observation of the exact-build cache. valid requires bounded raw
 * lengths and matching metadata before/after the copy. No native pointers. */
struct kpd207_sample {
    uint8_t phase, sequence[2], valid, spr_length, epr_length;
    uint8_t spr[28], epr[44];
};
enum { KPD207_INITIALIZED = 1, KPD207_CANDIDATE = 2,
       KPD207_CAPTURE_READY = 4, KPD207_SEEDED = 8 };
struct kpd207_capture {
    struct kpd207_sample candidate, result;
    uint8_t id[8], next_id[8];
    uint32_t candidate_since;
    uint16_t idle_sequence;
    uint8_t previous_phase, flags;
};
_Static_assert(sizeof(struct kpd207_sample) == 78, "Capture sample ABI");
_Static_assert(sizeof(struct kpd207_capture) == 180, "Capture state ABI");
void kpd207_capture_seed(struct kpd207_capture *, const uint8_t seed[8]);
bool kpd207_capture_take_id(struct kpd207_capture *, uint8_t id[8]);
void kpd207_capture_observe(struct kpd207_capture *, const struct kpd207_sample *, uint32_t now);
bool kpd207_capture_freeze(const struct kpd207_capture *, struct kpd_transfer *);
#endif
