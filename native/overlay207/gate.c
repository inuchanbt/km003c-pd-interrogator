/* SPDX-License-Identifier: MIT
 * Exact-build gate: executes once at stock GUI task entry, before its loop.
 * No mutable globals, allocation, MMIO, IRQ masking or dependence on tail code.
 * Keep this gate in the stock application sector, separate from the extension. */
#include <stddef.h>
#include <stdint.h>
#include "profile.h"

uintptr_t kpd207_choose_callback(void) {
    const volatile uint32_t *h = (const volatile uint32_t *)KPD207_TAIL_BASE;
    const volatile uint8_t *bytes = (const volatile uint8_t *)KPD207_TAIL_BASE;
    static const uint8_t stock_hash_prefix[16] = KPD207_HASH_PREFIX;
    /* Magic KPDX207\0, ABI 1, 64-byte header. Hash binds to the stock build. */
    if (h[0] != UINT32_C(0x5844504b) || h[1] != UINT32_C(0x00373032) || h[2] != 1 || h[15] != 0)
        return KPD207_STOCK_HANDLER;
    for (unsigned n = 0; n < 16; ++n)
        if (bytes[44 + n] != stock_hash_prefix[n]) return KPD207_STOCK_HANDLER;
    uint32_t length = h[3], entry = h[5];
    if (length < 66 || length > KPD207_TAIL_LIMIT - KPD207_TAIL_BASE || (length & 3u) != 0 ||
        (entry & 1u) == 0 || entry < KPD207_TAIL_BASE + 65 || entry >= KPD207_TAIL_BASE + length)
        return KPD207_STOCK_HANDLER;
    uint32_t crc = UINT32_MAX;
    for (uint32_t n = 0; n < length; ++n) {
        /* The checksum word is logically zero while calculating the CRC. */
        crc ^= n >= 16 && n < 20 ? 0 : bytes[n];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((0u - (crc & 1u)) & UINT32_C(0xedb88320));
    }
    return ~crc == h[4] ? entry : KPD207_STOCK_HANDLER;
}
