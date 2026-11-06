/* SPDX-License-Identifier: MIT */
#ifndef KPD207_INFO_H
#define KPD207_INFO_H
#include "capture.h"

struct kpd207_info_field {
    uint8_t raw[KPD_RAW_MAX];
    uint16_t length, total, chunks;
    uint8_t status, present;
};
enum { KPD207_PPS_IDLE, KPD207_PPS_COMMAND, KPD207_PPS_TX, KPD207_PPS_REPLY, KPD207_PPS_DONE };
struct kpd207_pps {
    uint32_t rdo, since;
    uint8_t id[8], reply[4], status, stage;
};
/* GUI-owned: the PD/PHY tasks never retain a pointer to this object. */
struct kpd207_info {
    struct kpd207_info_field fields[KPD_FIELDS - 2];
    uint32_t since;
    uint16_t ring_total, ring_position;
    uint8_t stage, step, current, capture_status;
    bool ring_ready, attached, pps, started;
    /* Private diagnostic only, never included in the transfer schema. */
    uint8_t tx_length, tx_wire[30];
    uint8_t cable_emulation; /* Native setting at PD start; 0=disabled. */
    struct kpd207_sample pdos;
    uint8_t id[8];
    bool frozen;
    uint8_t settle_link;
    uint32_t startup_since;
    struct kpd207_pps manual_pps;
};
_Static_assert(sizeof(struct kpd207_info_field) == 268 && offsetof(struct kpd207_info, pdos) == 2192 &&
               sizeof(struct kpd207_info) == 2308,
               "Information state ABI used by private inspectors");
enum { KPD207_INFO_IDLE, KPD207_INFO_SETTLE, KPD207_INFO_COMMAND,
       KPD207_INFO_TX, KPD207_INFO_REPLY, KPD207_INFO_GUARD, KPD207_INFO_DONE,
       KPD207_INFO_WAIT_PDO };
enum { KPD207_LINK_WAIT, KPD207_LINK_SPR, KPD207_LINK_EPR_PENDING, KPD207_LINK_EPR };
void kpd207_info_begin(struct kpd207_info *);
void kpd207_info_stop(struct kpd207_info *, uint8_t status);
void kpd207_info_resettle(struct kpd207_info *, uint8_t status);
uint8_t kpd207_info_link(const struct kpd207_capture *, uint8_t phase, bool native_epr);
void kpd207_info_tick(struct kpd207_info *, uint8_t link, bool pps, uint32_t now);
/* Native console's three-byte prefix (SOP + input PD header), then payload. */
unsigned kpd207_info_command(const struct kpd207_info *, uint8_t out[7]);
void kpd207_info_sent(struct kpd207_info *, uint32_t now);
void kpd207_info_hold(struct kpd207_info *, const struct kpd207_capture *, uint32_t now);
bool kpd207_info_waiting_epr(const struct kpd207_info *, const struct kpd207_capture *, uint32_t now);
bool kpd207_info_records(struct kpd207_info *, const uint8_t *, unsigned, uint32_t now);
bool kpd207_info_freeze(const struct kpd207_capture *, const struct kpd207_info *, struct kpd_transfer *);
void kpd207_pps_finish(struct kpd207_pps *, uint8_t status);
bool kpd207_pps_freeze(const struct kpd207_pps *, struct kpd_transfer *);
#endif
