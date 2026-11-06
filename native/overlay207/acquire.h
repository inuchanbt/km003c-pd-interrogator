/* SPDX-License-Identifier: MIT */
#ifndef KPD207_ACQUIRE_H
#define KPD207_ACQUIRE_H
#include "capture.h"
#include "info.h"
struct kpd207_random {
    uint32_t since, mode;
    uint8_t phase, discarded;
};
/* Native exact-build adapter. All polling is bounded to one step per GUI tick. */
void kpd207_acquire_tick(struct kpd207_capture *, struct kpd207_random *, uint32_t now);
void kpd207_information_tick(struct kpd207_info *, const struct kpd207_capture *, uint32_t now);
uint32_t kpd207_pps_contract(void);
bool kpd207_pps_selected(void);
#endif
