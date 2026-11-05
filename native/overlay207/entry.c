/* SPDX-License-Identifier: MIT */
#include "kpd.h"
#include "profile.h"

void kpd207_gui_screen_tick(void);

void kpd207_gui_tick(void) {
    ((void (*)(void))(uintptr_t)KPD207_STOCK_HANDLER)();
    kpd207_gui_screen_tick();
}

struct kpd207_header {
    uint8_t magic[8];
    uint32_t abi, length, crc;
    void (*gui_tick)(void);
    bool (*freeze)(const struct kpd_snapshot *, struct kpd_transfer *);
    bool (*page_url)(const struct kpd_transfer *, unsigned, char *);
    bool (*encode_page)(const struct kpd_transfer *, unsigned, struct kpd_workspace *);
    bool (*qr_pack)(const struct kpd_workspace *, uint8_t *);
    bool (*qr_row)(const struct kpd_workspace *, unsigned, uint8_t *);
    uint8_t stock_hash_prefix[16];
    uint32_t reserved;
};
_Static_assert(sizeof(struct kpd207_header) == 64, "Requires the ARM32 ABI");

__attribute__((used, section(".kpd207.header")))
const struct kpd207_header kpd207_header = {
    {'K', 'P', 'D', 'X', '2', '0', '7', 0}, 1, 0, 0,
    kpd207_gui_tick, kpd_freeze, kpd_page_url, kpd_encode_page, kpd_qr_pack, kpd_qr_row,
    KPD207_HASH_PREFIX, 0
};
