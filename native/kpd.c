/* SPDX-License-Identifier: MIT */
#include "kpd.h"
#include "vendor/qrcodegen/qrcodegen.h"

_Static_assert(sizeof(KPD_IMPORT_URL) - 1 == 56, "URL budget changed");
_Static_assert(KPD_QR_BUFFER == qrcodegen_BUFFER_LEN_FOR_VERSION(8), "QR buffer");
_Static_assert((KPD_BINARY_MAX + KPD_CHUNK - 1) / KPD_CHUNK <= 64, "page count");

static uint32_t crc32(const uint8_t *data, size_t length) {
    uint32_t crc = UINT32_MAX;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ ((0u - (crc & 1u)) & UINT32_C(0xedb88320));
    }
    return ~crc;
}

bool kpd_freeze(const struct kpd_snapshot *snapshot, struct kpd_transfer *out) {
    if (out == NULL) return false;
    out->ready = false;
    out->length = 0;
    out->pages = 0;
    out->crc = 0;
    if (snapshot == NULL || snapshot->origin > KPD_KM003C_PPS || snapshot->capture_status > 4)
        return false;
    for (unsigned i = 0; i < KPD_FIELDS; ++i) {
        const struct kpd_field *f = &snapshot->fields[i];
        if (f->status > KPD_EMULATION_ENABLED || f->length > KPD_RAW_MAX ||
            (!f->present && f->length != 0) || (f->length != 0 && f->raw == NULL))
            return false;
        /* PDO/VDO fields must be whole objects, as in the host model. */
        if ((i == KPD_SPR_PDOS || i == KPD_EPR_PDOS ||
             i == KPD_CABLE_IDENTITY || i == KPD_SOURCE_IDENTITY) && f->length % 4 != 0)
            return false;
    }
    uint8_t *p = out->binary;
    *p++ = 'K'; *p++ = 'P'; *p++ = 'D'; *p++ = '1';
    for (unsigned i = 0; i < 8; ++i) *p++ = snapshot->id[i];
    *p++ = snapshot->origin;
    *p++ = snapshot->capture_status;
    for (unsigned i = 0; i < KPD_FIELDS; ++i) {
        const struct kpd_field *f = &snapshot->fields[i];
        *p++ = (uint8_t)(i + 1);
        *p++ = f->status;
        *p++ = (uint8_t)f->present;
        *p++ = (uint8_t)f->length;
        *p++ = (uint8_t)(f->length >> 8);
        for (unsigned j = 0; j < f->length; ++j) *p++ = f->raw[j];
    }
    out->length = (uint16_t)(p - out->binary);
    out->crc = crc32(out->binary, out->length);
    out->pages = (uint8_t)((out->length + KPD_CHUNK - 1) / KPD_CHUNK);
    out->ready = true;
    return true;
}

bool kpd_page_url(const struct kpd_transfer *t, unsigned page,
                  char out[KPD_URL_MAX + 1]) {
    static const char hex[] = "0123456789abcdef";
    static const char upper[] = "0123456789ABCDEF";
    static const char b64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    if (out == NULL) return false;
    out[0] = '\0';
    if (t == NULL || !t->ready || t->length < 64 || t->length > KPD_BINARY_MAX ||
        t->pages != (t->length + KPD_CHUNK - 1) / KPD_CHUNK ||
        page == 0 || page > t->pages) return false;
    char *p = out;
    const char *prefix = KPD_IMPORT_URL "KPD1:";
    while (*prefix != '\0') *p++ = *prefix++;
    for (unsigned i = 4; i < 12; ++i) {
        *p++ = hex[t->binary[i] >> 4];
        *p++ = hex[t->binary[i] & 15];
    }
    *p++ = ':';
    *p++ = (char)('0' + page / 10); *p++ = (char)('0' + page % 10);
    *p++ = '/';
    *p++ = (char)('0' + t->pages / 10); *p++ = (char)('0' + t->pages % 10);
    *p++ = ':';
    for (unsigned i = 0; i < 8; ++i) *p++ = upper[(t->crc >> (28 - 4 * i)) & 15];
    *p++ = ':';
    unsigned start = (page - 1) * KPD_CHUNK;
    unsigned end = start + KPD_CHUNK;
    if (end > t->length) end = t->length;
    uint32_t bits = 0;
    unsigned count = 0;
    for (unsigned i = start; i < end; ++i) {
        bits = (bits << 8) | t->binary[i];
        count += 8;
        while (count >= 6) {
            count -= 6;
            *p++ = b64[(bits >> count) & 63];
        }
    }
    if (count != 0) *p++ = b64[(bits << (6 - count)) & 63];
    *p = '\0';
    return true;
}

bool kpd_encode_page(const struct kpd_transfer *t, unsigned page,
                     struct kpd_workspace *work) {
    if (work == NULL) return false;
    work->ready = false;
    if (!kpd_page_url(t, page, work->url)) return false;
    return kpd_encode_url(work);
}

bool kpd_encode_url(struct kpd_workspace *work) {
    if (work == NULL) return false;
    work->ready = false;
    size_t length = 0;
    while (length < sizeof(work->url) && work->url[length] != '\0') {
        work->temp[length] = (uint8_t)work->url[length];
        ++length;
    }
    if (length == 0 || length == sizeof(work->url)) return false;
    work->ready = qrcodegen_encodeBinary(work->temp, length, work->qr,
        qrcodegen_Ecc_MEDIUM, 8, 8, qrcodegen_Mask_AUTO, false);
    return work->ready;
}

static bool qr_valid(const struct kpd_workspace *work) {
    return work != NULL && work->ready && qrcodegen_getSize(work->qr) == KPD_QR_SIZE;
}

bool kpd_qr_pack(const struct kpd_workspace *work, uint8_t out[KPD_QR_PACKED]) {
    if (out == NULL || !qr_valid(work)) return false;
    for (unsigned i = 0; i < KPD_QR_PACKED; ++i) out[i] = 0;
    for (int y = 0; y < KPD_QR_SIZE; ++y)
        for (int x = 0; x < KPD_QR_SIZE; ++x) {
            unsigned bit = (unsigned)(y * KPD_QR_SIZE + x);
            if (qrcodegen_getModule(work->qr, x, y)) out[bit / 8] |= (uint8_t)(128u >> (bit % 8));
        }
    return true;
}

bool kpd_qr_row(const struct kpd_workspace *work, unsigned y,
                uint8_t out[KPD_SCREEN_SIZE * 2]) {
    if (out == NULL || y >= KPD_SCREEN_SIZE || !qr_valid(work)) return false;
    for (unsigned x = 0; x < KPD_SCREEN_SIZE; ++x) {
        bool dark = x >= 22 && x < 218 && y >= 22 && y < 218 &&
            qrcodegen_getModule(work->qr, (int)((x - 22) / 4), (int)((y - 22) / 4));
        out[x * 2] = out[x * 2 + 1] = dark ? 0 : 255;
    }
    return true;
}
