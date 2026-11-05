/* SPDX-License-Identifier: MIT
 * Standalone link/size probe. No vectors, metadata, USB, startup or board code.
 * The resulting ELF is NOT a flashable KM003C application. */
#include "kpd.h"

static struct kpd_snapshot snapshot;
static struct kpd_transfer transfer;
static struct kpd_workspace workspace;
static uint8_t row[480], packed[301];
volatile bool kpd_sizecheck_result;

void kpd_sizecheck(void) {
    /* Keep all public entry points reachable when linking with --gc-sections. */
    bool ok = kpd_freeze(&snapshot, &transfer);
    ok &= kpd_page_url(&transfer, 1, workspace.url);
    ok &= kpd_encode_page(&transfer, 1, &workspace);
    ok &= kpd_qr_pack(&workspace, packed);
    ok &= kpd_qr_row(&workspace, 0, row);
    kpd_sizecheck_result = ok;
}
