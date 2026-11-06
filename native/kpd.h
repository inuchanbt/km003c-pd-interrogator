/* SPDX-License-Identifier: MIT */
#ifndef KPD_H
#define KPD_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define KPD_FIELDS 10
#define KPD_RAW_MAX 260
#define KPD_BINARY_MAX 2664
#define KPD_URL_MAX 152
#define KPD_CHUNK 44
#define KPD_QR_SIZE 49
#define KPD_QR_PACKED 301
#define KPD_QR_BUFFER 302
#define KPD_SCREEN_SIZE 240
#define KPD_IMPORT_URL "https://inuchanbt.github.io/km003c-pd-interrogator/?v=4#"

enum kpd_origin { KPD_KM003C, KPD_CH32H417, KPD_DEMO, KPD_KM003C_CABLE, KPD_KM003C_PPS };

enum kpd_status {
    KPD_NOT_REQUESTED, KPD_PASS, KPD_OBSERVED, KPD_UNSUPPORTED,
    KPD_NO_RESPONSE, KPD_TX_FAILED, KPD_REJECTED, KPD_BUSY,
    KPD_RESET, KPD_INVALID, KPD_PARTIAL, KPD_UNKNOWN, KPD_EMULATION_ENABLED
};

enum kpd_field_id {
    KPD_SPR_PDOS, KPD_EPR_PDOS, KPD_REVISION, KPD_SOURCE_CAP_EXT,
    KPD_SOURCE_INFO, KPD_SOURCE_STATUS, KPD_PPS_STATUS, KPD_MANUFACTURER,
    KPD_CABLE_IDENTITY, KPD_SOURCE_IDENTITY
};

/* Raw bytes are PD wire order, not host-endian uint32_t objects.
 * Present=true and length=0 explicitly represents an empty response.
 * The caller holds its capture lock while kpd_freeze() copies these views. */
struct kpd_field {
    const uint8_t *raw;
    uint16_t length;
    uint8_t status;
    bool present;
};

struct kpd_snapshot {
    uint8_t id[8];
    uint8_t origin;          /* enum kpd_origin */
    uint8_t capture_status;  /* unknown/completed/failed/interrupted/imported */
    struct kpd_field fields[KPD_FIELDS]; /* tag order defined in QR-FORMAT.md */
};

/* Owns a frozen copy. PD callbacks can resume as soon as freeze returns.
 * Treat as immutable until the QR screen is closed. Not a PD task object. */
struct kpd_transfer {
    uint8_t binary[KPD_BINARY_MAX];
    uint32_t crc;
    uint16_t length;
    uint8_t pages;
    bool ready;
};

/* Caller-owned scratch; no heap, clock, USB, flash or display access. */
struct kpd_workspace {
    char url[KPD_URL_MAX + 1];
    uint8_t temp[KPD_QR_BUFFER];
    uint8_t qr[KPD_QR_BUFFER]; /* Nayuki format; use kpd_qr_pack for host format */
    bool ready;
};

bool kpd_freeze(const struct kpd_snapshot *snapshot, struct kpd_transfer *out);
/* Pages are one-based. Failure clears url; buffers must not alias transfer. */
bool kpd_page_url(const struct kpd_transfer *transfer, unsigned page,
                  char out[KPD_URL_MAX + 1]);
/* Called by GUI worker outside the capture lock and outside PD callbacks. */
bool kpd_encode_page(const struct kpd_transfer *transfer, unsigned page,
                     struct kpd_workspace *work);
/* Encode the bounded, NUL-terminated URL already in work->url. */
bool kpd_encode_url(struct kpd_workspace *work);
bool kpd_qr_pack(const struct kpd_workspace *work, uint8_t out[KPD_QR_PACKED]);
/* One full RGB565 row in wire byte order, including quiet zone and margins.
 * GUI integration must use the stock display/bus ownership mechanism. */
bool kpd_qr_row(const struct kpd_workspace *work, unsigned y,
                uint8_t out[KPD_SCREEN_SIZE * 2]);

#endif
