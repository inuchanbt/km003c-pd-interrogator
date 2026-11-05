/* SPDX-License-Identifier: MIT */
#ifndef KPD207_INFO_H
#define KPD207_INFO_H
#include "capture.h"

struct kpd207_info_field {
    uint8_t raw[KPD_RAW_MAX];
    uint16_t length, total, chunks;
    uint8_t status, present;
};
/* GUI-owned: the PD/PHY tasks never retain a pointer to this object. */
struct kpd207_info {
    struct kpd207_info_field fields[KPD_FIELDS - 2];
    uint32_t since;
    uint16_t ring_total, ring_position;
    uint8_t stage, step, current, capture_status;
    bool ring_ready, attached, pps, started;
};
_Static_assert(sizeof(struct kpd207_info_field) == 268 && sizeof(struct kpd207_info) == 2160,
               "Information state ABI used by private inspectors");
enum { KPD207_INFO_IDLE, KPD207_INFO_SETTLE, KPD207_INFO_COMMAND,
       KPD207_INFO_TX, KPD207_INFO_REPLY, KPD207_INFO_GUARD, KPD207_INFO_DONE,
       KPD207_INFO_WAIT_PDO };
void kpd207_info_begin(struct kpd207_info *);
void kpd207_info_stop(struct kpd207_info *, uint8_t status);
void kpd207_info_tick(struct kpd207_info *, bool ready, bool pps, uint32_t now);
/* Native console's three-byte prefix (SOP + input PD header), then payload. */
unsigned kpd207_info_command(const struct kpd207_info *, uint8_t out[7]);
void kpd207_info_sent(struct kpd207_info *, uint32_t now);
bool kpd207_info_records(struct kpd207_info *, const uint8_t *, unsigned, uint32_t now);
bool kpd207_info_freeze(const struct kpd207_capture *, const struct kpd207_info *, struct kpd_transfer *);
#endif
