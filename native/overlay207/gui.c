/* SPDX-License-Identifier: MIT
 * Exact stock-2.0.7/HW2.3 LVGL ABI. All storage belongs to the native heap.
 * Export the observer's owned results from native PD and e-Marker scans.
 */
#include "kpd.h"
#include "acquire.h"
#include <string.h>

#define FN(address, result, ...) ((result (*)(__VA_ARGS__))(uintptr_t)((address) | 1u))
#define mem_alloc FN(0x32a94, void *, size_t)
#define mem_free FN(0x32b80, void, void *)
#define sys_layer FN(0x2a6fe, void *, void *)
#define obj_create FN(0x346b8, void *, void *, void *)
#define obj_del FN(0x34a0a, int, void *)
#define obj_ext FN(0x34f7a, void *, void *)
#define obj_allocate_ext FN(0x34552, void *, void *, size_t)
#define obj_child FN(0x34cd0, void *, void *, void *)
#define obj_hidden FN(0x35a8e, void, void *, bool)
#define obj_signal FN(0x3501a, signal_cb, void *)
#define obj_set_signal FN(0x35bfe, void, void *, signal_cb)
#define obj_invalidate FN(0x35658, void, void *)
#define img_create FN(0x2f728, void *, void *, void *)
#define img_set_src FN(0x3057e, void, void *, const void *)
#define img_get_src FN(0x30574, void *, void *)
#define group_create FN(0x2f218, void *, void)
#define group_delete FN(0x2f250, void, void *)
#define group_add FN(0x2f1c0, void, void *, void *)
#define group_focused FN(0x2f384, const uint8_t *, void *)
#define indev_next FN(0x30ab4, void *, void *)
#define indev_set_group FN(0x30c9c, void, void *, void *)
#define tick_get FN(0x3c0d0, uint32_t, void)
#define stock_modal FN(0x33a72, void *, void)
#define STOCK_KEYPAD_READ 0x24607u

typedef uint8_t (*signal_cb)(void *, uint8_t, void *);
typedef bool (*keypad_cb)(void *, void *);
struct image_descriptor { uint32_t header, size; const uint8_t *pixels; };
enum { IMAGE_WIDTH = 240, IMAGE_ROW = 30, FULL_HEIGHT = 240, PPS_HEIGHT = 192 };
enum { GLYPH_WIDTH = 3, GLYPH_HEIGHT = 5, QR_LABEL_SCALE = 2, QR_LABEL_BOTTOM_MARGIN = 2 };
typedef char page_url[KPD_URL_MAX + 1];
struct cable_result {
    void *table;
    uint32_t vdos[4];
    uint32_t svids;
    uint8_t id[8];
    bool saved;
};
struct screen_state {
    /* Image source is also the callback's route to this state. */
    struct image_descriptor descriptor;
    signal_cb anchor_signal, image_signal;
    void *image, *input, *old_group, *group;
    uint8_t *data;
    uint8_t page, pending, pages, origin;
    keypad_cb keypad_read;
    uint32_t chord_since;
    bool chord, chord_fired;
    struct kpd207_capture capture;
    struct kpd207_random random;
    struct kpd207_info info;
    struct cable_result cable;
    bool prefer_cable;
    uint8_t pps_arrow;
    bool pps_arrow_forwarded;
    bool pps_chord, pps_waiting, pps_unavailable;
    uint32_t pps_arrow_since;
    uint32_t pps_wait_since;
};
_Static_assert(sizeof(struct image_descriptor) == 12, "ARM32 image descriptor");
_Static_assert(offsetof(struct screen_state, capture) == 56 &&
               offsetof(struct screen_state, random) == 236 && offsetof(struct screen_state, info) == 248,
               "Update the private live-state inspector when the GUI ABI changes");

static uint8_t anchor_signal(void *obj, uint8_t sign, void *param) {
    struct screen_state *s = obj_ext(obj);
    return s->anchor_signal(obj, sign, param);
}

static struct screen_state *find_state(void *layer) {
    for (void *obj = obj_child(layer, NULL); obj; obj = obj_child(layer, obj))
        if (obj_signal(obj) == anchor_signal) return obj_ext(obj);
    return NULL;
}

static bool pps_trigger_focused(const struct screen_state *s) {
    void *modal = stock_modal();
    if (!modal || (uintptr_t)obj_signal(modal) != 0x3c9f9u) return false;
    const uint8_t *ext = obj_ext(modal);
    if (!ext || *(const uint32_t *)(ext + 0x10) != 0x3f0d5u) return false;
    const uint8_t *focus = group_focused(*(void **)((uint8_t *)s->input + 0x5c));
    if (!focus) return false;
    uint32_t event = *(const uint32_t *)(focus + 0x18);
    if (event != 0x1b51bu && event != 0x1b381u) return false; /* Slider or its Back button. */
    const uint8_t *owner = *(const uint8_t *const *)(focus + 0x4c);
    if (!owner) return false;
    const uint8_t *settings = *(const uint8_t *const *)(owner + 4);
    if (!settings) return false;
    const char *range = *(const char *const *)(settings + 8);
    /* Stock PPS has a decimal minimum and an ampere limit. SPR AVS uses
     * integer minima and EPR AVS uses watts. The label survives PD resets. */
    if (!range || range[0] < '0' || range[0] > '9' || !range[1] ||
        (range[1] != '.' && range[2] != '.')) return false;
    for (unsigned n = 0; n < 32 && range[n]; ++n)
        if (range[n] == 'A') return true;
    return false;
}

static bool wants_pps(const struct screen_state *s) {
    return kpd207_pps_selected() || pps_trigger_focused(s);
}

static void defer_pps_arrow(struct screen_state *s, void *data, bool left_up, bool right_up,
                            uint32_t now) {
    uint8_t *pressed = (uint8_t *)data + 14;
    uint32_t *key = (uint32_t *)((uint8_t *)data + 4);
    if (left_up && right_up) {
        /* A short single tap still gets one press on release. */
        if (s->pps_arrow && !s->pps_arrow_forwarded && !*pressed) {
            *key = s->pps_arrow;
            *pressed = 1;
        }
        s->pps_arrow = 0;
        return;
    }
    if (*pressed && *key != 19 && *key != 20) {
        s->pps_arrow = 0;
        return;
    }
    uint8_t arrow = left_up ? 19 : 20;
    if (s->pps_arrow != arrow) {
        if (!*pressed || !wants_pps(s)) {
            s->pps_arrow = 0;
            return;
        }
        s->pps_arrow = arrow;
        s->pps_arrow_since = now;
        s->pps_arrow_forwarded = false;
    }
    /* LVGL can immediately act on the first arrow in the trigger slider.
     * Give the second arrow time to arrive before forwarding that press. */
    if ((uint32_t)(now - s->pps_arrow_since) < 150) *pressed = 0;
    else if (*pressed) s->pps_arrow_forwarded = true;
}

static bool keypad_read(void *driver, void *data) {
    struct screen_state *s = find_state(sys_layer(NULL));
    keypad_cb original = (keypad_cb)(uintptr_t)STOCK_KEYPAD_READ;
    if (s && s->input == driver) original = s->keypad_read;
    bool more = original(driver, data);
    if (!s || s->input != driver) return more;
    /* The stock reader uses these same GPIOB bit-band aliases, active low.
     * Hold both arrows for 750 ms; suppress the chord through full release.
     * No pin configuration, native user_data, or original key mapping changes. */
    bool left_up = *(volatile const uint32_t *)0x42a70220u != 0;
    bool right_up = *(volatile const uint32_t *)0x42a70224u != 0;
    uint32_t now = tick_get();
    if (!s->data && !left_up && !right_up && !s->chord) {
        s->chord = true;
        s->chord_fired = false;
        s->chord_since = now;
        s->pps_chord = s->pps_arrow != 0 || wants_pps(s);
        s->pps_arrow = 0;
    }
    if (s->chord) {
        if (!left_up && !right_up && !s->chord_fired &&
            (uint32_t)(now - s->chord_since) >= 750) {
            s->pending = 4;
            s->chord_fired = true;
        }
        ((uint8_t *)data)[14] = 0; /* Native lv_indev_data_t.state = released */
        if (left_up && right_up) s->chord = false;
    } else if (!s->data) defer_pps_arrow(s, data, left_up, right_up, now);
    return more;
}

static uint8_t image_signal(void *obj, uint8_t sign, void *param) {
    struct screen_state *s = img_get_src(obj);
    if (sign == 24 && param) { /* Native lv_group_send_data: LV_SIGNAL_CONTROL */
        uint32_t key = *(const uint32_t *)param;
        if (key == 27) s->pending = 3;
        else if (key == 19 || key == 10) s->pending = 1;
        else if (key == 20) s->pending = 2;
        return 1; /* LV_RES_OK */
    }
    return s->image_signal(obj, sign, param);
}

static unsigned image_height(const struct screen_state *s) {
    return s->origin == KPD_KM003C_PPS ? PPS_HEIGHT : FULL_HEIGHT;
}

static size_t image_size(const struct screen_state *s) {
    return 8 + image_height(s) * IMAGE_ROW;
}

static void *screen_payload(struct screen_state *s) {
    /* Preserve the full-size transfer offset used by the private inspector. */
    return s->data + image_size(s) + 4;
}

static void pixel(uint8_t *pixels, unsigned x, unsigned y) {
    pixels[8 + y * IMAGE_ROW + x / 8] |= (uint8_t)(0x80u >> (x % 8));
}

static void text(struct screen_state *s, const char *str, unsigned x, unsigned y, unsigned scale) {
    /* Three columns/five rows, top bit first. Code-native bitmap text. */
    const char chars[] = "0123456789ACDEILNOPRSTUVX/GWB ";
    const uint16_t glyphs[] = {0x7b6f,0x2c97,0x73e7,0x73cf,0x5bc9,0x79cf,0x79ef,0x7292,0x7bef,0x7bcf,
        0x2bed,0x7927,0x6b6e,0x79a7,0x7497,0x4927,0x5ffd,0x7b6f,0x7be4,0x6bad,0x79cf,0x7492,0x5b6f,0x5b6a,0x5aad,0x12a4,0x797f,0x5bfd,0x6bae,0};
    for (unsigned c = 0; str[c]; ++c) {
        unsigned g = 0;
        while (chars[g] && chars[g] != str[c]) ++g;
        if (!chars[g]) continue;
        for (unsigned yy = 0; yy < GLYPH_HEIGHT * scale; ++yy)
            for (unsigned xx = 0; xx < GLYPH_WIDTH * scale; ++xx)
                if (glyphs[g] & (1u << (GLYPH_WIDTH * GLYPH_HEIGHT - 1 - yy / scale * GLYPH_WIDTH - xx / scale))) {
                    unsigned px = x + c * (GLYPH_WIDTH + 1) * scale + xx, py = y + yy;
                    if (px < IMAGE_WIDTH && py < image_height(s)) pixel(s->data, px, py);
                }
    }
}

static bool cable_mode(void) {
    return *(const volatile uint8_t *)0x20000004u == 5;
}

static bool render(struct screen_state *s, uint32_t now) {
    struct kpd_workspace work;
    uint8_t row[480];
    memset(s->data, 0, image_size(s));
    memset(s->data, 255, 4); /* Native 32-bit palette: opaque white, black */
    s->data[7] = 255;
    if (!s->pages) {
        bool reading = s->info.attached && s->info.stage != KPD207_INFO_DONE;
        bool pps = s->origin == KPD_KM003C_PPS;
        bool epr = !s->origin && kpd207_info_waiting_epr(&s->info, &s->capture, now);
        text(s, pps ? (s->pps_unavailable ? "NO PPS" : "READING PPS") :
            epr ? "READING EPR" : reading ? "READING PD" :
            s->random.phase == 3 ? "ID ERROR" : "NO DATA", 32, 80, 4);
        text(s, pps && s->pps_unavailable ? "SET PPS" :
            reading || epr || pps ? "PLEASE WAIT" : cable_mode() ? "RUN CABLE TEST" : "RUN PD TEST", 10, 120, 4);
        text(s, "EXIT CLOSE", 42, 170, 4);
        return true;
    }
    if (s->origin) {
        const page_url *urls = screen_payload(s);
        memcpy(work.url, urls[s->page - 1], sizeof(work.url));
        if (!kpd_encode_url(&work)) return false;
    } else if (!kpd_encode_page(screen_payload(s), s->page, &work)) return false;
    /* Sample one pixel per module from the existing four-pixel raster.
     * The smaller PPS image uses three pixels per module and a full quiet zone. */
    unsigned scale = s->origin == KPD_KM003C_PPS ? 3 : 4;
    unsigned left = (IMAGE_WIDTH - KPD_QR_SIZE * scale) / 2;
    unsigned label_top = image_height(s) - GLYPH_HEIGHT * QR_LABEL_SCALE - QR_LABEL_BOTTOM_MARGIN;
    /* Center above the label, leaving four blank modules on each side. */
    unsigned top = (label_top - KPD_QR_SIZE * scale) / 2;
    for (unsigned y = 0; y < KPD_QR_SIZE; ++y) {
        if (!kpd_qr_row(&work, 22 + 4 * y, row)) return false;
        for (unsigned x = 0; x < KPD_QR_SIZE; ++x) {
            if (row[(22 + 4 * x) * 2]) continue;
            for (unsigned yy = 0; yy < scale; ++yy)
                for (unsigned xx = 0; xx < scale; ++xx)
                    pixel(s->data, left + x * scale + xx, top + y * scale + yy);
        }
    }
    /* Up to 61 pages for the bounded maximum schema, outside quiet zone. */
    char label[12];
    memcpy(label, s->origin == KPD_KM003C_PPS ? "PPS   01/01" :
        s->origin == KPD_KM003C_CABLE ? "CABLE 01/01" : "SAVED 01/01", sizeof(label));
    label[6] = (char)('0' + s->page / 10); label[7] = (char)('0' + s->page % 10);
    label[9] = (char)('0' + s->pages / 10); label[10] = (char)('0' + s->pages % 10);
    unsigned label_width = ((sizeof(label) - 1) * (GLYPH_WIDTH + 1) - 1) * QR_LABEL_SCALE;
    text(s, label, (IMAGE_WIDTH - label_width) / 2, label_top, QR_LABEL_SCALE);
    return true;
}

static void close_screen(struct screen_state *s) {
    /* Restore ownership before deleting the object/group. No deletion in signals. */
    if (s->input) indev_set_group(s->input, s->old_group);
    if (s->image) obj_del(s->image);
    if (s->group) group_delete(s->group);
    if (s->data) mem_free(s->data);
    s->image = s->group = NULL;
    s->data = NULL;
}

static bool read_hex(const char *text, unsigned digits, uint32_t *out) {
    uint32_t word = 0;
    for (unsigned i = 0; i < digits; ++i) {
        unsigned ch = (uint8_t)text[i];
        unsigned digit;
        if (ch >= '0' && ch <= '9') digit = ch - '0';
        else {
            ch |= 32u;
            if (ch < 'a' || ch > 'f') return false;
            digit = ch - 'a' + 10;
        }
        word = word << 4 | digit;
    }
    *out = word;
    return true;
}

static bool read_vdo(const char *text, uint32_t *out) {
    return text && text[0] == '0' && text[1] == 'x' && read_hex(text + 2, 8, out) && !text[10];
}

static bool same_text(const char *a, const char *b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
}

static uint32_t read_svids(const char *const *cells, unsigned rows) {
    for (unsigned row = 0; row < rows; ++row) {
        const char *label = cells[row * 2], *value = cells[row * 2 + 1];
        if (!label || !same_text(label + 1, "SVID") || !value) continue;
        ++value; /* Skip LVGL cell formatting. Stock text is "0x1234 abcd". */
        uint32_t first, second;
        if (value[0] == '0' && value[1] == 'x' && read_hex(value + 2, 4, &first) &&
            value[6] == ' ' && read_hex(value + 7, 4, &second) && !value[11])
            return first << 16 | second;
    }
    return 0;
}

static void *cable_table(void) {
    /* The stock result page owns a complete copy after native PD RAM is cleared.
     * Identify that exact page by its event callback, then use its table type. */
    void *modal = stock_modal();
    if (!modal || (uintptr_t)obj_signal(modal) != 0x3c9f9u) return NULL;
    void *page = *(void **)obj_ext(modal);
    if (!page || *(const uint32_t *)((uint8_t *)page + 0x18) != 0x1db7fu) return NULL;
    void *scroll = ((void **)obj_ext(page))[1];
    if (!scroll) return NULL;
    void *table = obj_child(scroll, NULL);
    return table && (uintptr_t)obj_signal(table) == 0x38f39u ? table : NULL;
}

static void observe_cable(struct screen_state *s) {
    void *table = cable_mode() ? cable_table() : NULL;
    if (table == s->cable.table && (!table || s->cable.saved)) return;
    s->cable.table = table;
    s->cable.saved = false;
    if (!table) return;
    const uint16_t *ext = obj_ext(table);
    unsigned rows = ext[1];
    const char *const *cells = *(const char *const *const *)(ext + 2);
    if (ext[0] != 2 || rows < 4 || rows > 32 || !cells) return;
    /* Optional rows can move the four Identity words. A cell's first byte
     * is LVGL formatting, and its remaining bytes are the stock text. */
    static const char *const labels[] = {"IdHead", "CertStat", "Product", "Cable"};
    for (unsigned row = 0; row + 4 <= rows; ++row) {
        uint32_t vdos[4];
        unsigned n = 0;
        for (; n < 4; ++n) {
            const char *label = cells[(row + n) * 2];
            const char *value = cells[(row + n) * 2 + 1];
            if (!label || !same_text(label + 1, labels[n]) || !value || !read_vdo(value + 1, &vdos[n]))
                break;
        }
        if (n != 4) continue;
        unsigned type = (vdos[0] >> 27) & 7;
        uint32_t svids = 0;
        if ((type != 3 && type != 4) || !vdos[3]) {
            /* Some cable scans retain SVIDs but leave all four Identity cells empty. */
            if (vdos[0] || vdos[1] || vdos[2] || vdos[3]) return;
            svids = read_svids(cells, rows);
            if (!svids) return;
        }
        if (!kpd207_capture_take_id(&s->capture, s->cable.id)) return;
        memcpy(s->cable.vdos, vdos, sizeof(vdos));
        s->cable.svids = svids;
        s->cable.saved = true;
        s->prefer_cable = true;
        return;
    }
}

__attribute__((noinline))
static bool freeze_compact(struct screen_state *s) {
    /* Keep only two URLs on the heap alongside the native result screen. */
    page_url *urls = screen_payload(s);
    struct kpd_transfer transfer;
    if (s->origin == KPD_KM003C_PPS) {
        if (!kpd207_pps_freeze(&s->info.manual_pps, &transfer)) return false;
    } else {
        struct kpd_snapshot snapshot = {.origin = KPD_KM003C_CABLE, .capture_status = 1};
        uint32_t words[5];
        memcpy(words, s->cable.vdos, sizeof(s->cable.vdos));
        words[4] = s->cable.svids;
        memcpy(snapshot.id, s->cable.id, 8);
        snapshot.fields[KPD_CABLE_IDENTITY] = (struct kpd_field){
            (const uint8_t *)words, words[4] ? sizeof(words) : sizeof(s->cable.vdos),
            words[4] ? KPD_PARTIAL : KPD_OBSERVED, true};
        if (!kpd_freeze(&snapshot, &transfer)) return false;
    }
    if (transfer.pages != 2 ||
        !kpd_page_url(&transfer, 1, urls[0]) ||
        !kpd_page_url(&transfer, 2, urls[1])) return false;
    s->pages = transfer.pages;
    return true;
}

static bool freeze_result(struct screen_state *s, uint32_t now) {
    if (s->origin == KPD_KM003C_PPS)
        return !s->pps_waiting && !s->pps_unavailable && freeze_compact(s);
    if (s->info.attached && s->info.stage != KPD207_INFO_DONE) return false;
    if (s->origin == KPD_KM003C_CABLE) {
        if (!s->prefer_cable || (cable_mode() && !s->cable.saved)) return false;
        return freeze_compact(s);
    }
    if (kpd207_info_waiting_epr(&s->info, &s->capture, now)) return false;
    struct kpd_transfer *transfer = screen_payload(s);
    if (!kpd207_info_freeze(&s->capture, &s->info, transfer)) return false;
    s->pages = transfer->pages;
    return true;
}

static void start_pps(struct screen_state *s, uint32_t now) {
    struct kpd207_pps *manual = &s->info.manual_pps;
    if (manual->stage != KPD207_PPS_IDLE && manual->stage != KPD207_PPS_DONE) {
        /* A previous opening can still have a request in flight. */
        s->pps_waiting = false;
        return;
    }
    uint32_t rdo = kpd207_pps_contract();
    if (rdo && kpd207_capture_take_id(&s->capture, manual->id)) {
        manual->rdo = rdo;
        manual->since = now;
        manual->stage = KPD207_PPS_COMMAND;
        s->pps_waiting = false;
    } else if ((uint32_t)(now - s->pps_wait_since) >= 2500) {
        s->pps_waiting = false;
        s->pps_unavailable = true;
        if (render(s, now)) obj_invalidate(s->image);
        else close_screen(s);
    }
}

static void open_result(struct screen_state *s, void *layer, uint32_t now) {
    if (!s->input) return;
    s->old_group = *(void **)((uint8_t *)s->input + 0x5c);
    bool pps = s->pps_chord || wants_pps(s);
    s->origin = pps ? KPD_KM003C_PPS :
        (cable_mode() || (s->prefer_cable && !s->info.attached)) ? KPD_KM003C_CABLE : KPD_KM003C;
    s->data = mem_alloc(image_size(s) + 4 + (s->origin ? 2 * sizeof(page_url) : sizeof(struct kpd_transfer)));
    if (!s->data) return;
    memset(s->data + image_size(s), 0, 4);
    s->page = 1;
    s->pages = 0;
    s->pps_waiting = pps;
    s->pps_unavailable = false;
    s->pps_wait_since = now;
    if (!s->origin) memset(screen_payload(s), 0, sizeof(struct kpd_transfer));
    if (!pps) (void)freeze_result(s, now);
    if (!render(s, now)) goto failed;
    s->descriptor.header = 7u | (IMAGE_WIDTH << 10) | (image_height(s) << 21); /* INDEXED_1BIT */
    s->descriptor.size = image_size(s);
    s->descriptor.pixels = s->data;
    s->group = group_create();
    if (!s->group) goto failed;
    s->image = img_create(layer, NULL);
    if (!s->image) goto failed;
    img_set_src(s->image, &s->descriptor);
    s->image_signal = obj_signal(s->image);
    obj_set_signal(s->image, image_signal);
    group_add(s->group, s->image);
    indev_set_group(s->input, s->group);
    if (pps) start_pps(s, now);
    return;
failed:
    close_screen(s);
}

void kpd207_gui_screen_tick(void) {
    void *layer = sys_layer(NULL);
    if (!layer) return;
    struct screen_state *s = find_state(layer);
    if (!s) {
        void *anchor = obj_create(layer, NULL);
        if (!anchor) return;
        s = obj_allocate_ext(anchor, sizeof(*s));
        if (!s) { obj_del(anchor); return; }
        memset(s, 0, sizeof(*s));
        s->anchor_signal = obj_signal(anchor);
        obj_hidden(anchor, true);
        obj_set_signal(anchor, anchor_signal);
        for (void *input = indev_next(NULL); input; input = indev_next(input)) {
            if (*(const uint8_t *)input != 2) continue;
            keypad_cb *reader = (keypad_cb *)((uint8_t *)input + 4);
            if ((uintptr_t)*reader != STOCK_KEYPAD_READ) return;
            s->input = input;
            s->keypad_read = *reader;
            *reader = keypad_read;
            break;
        }
    }
    uint32_t now = tick_get();
    kpd207_acquire_tick(&s->capture, &s->random, now);
    bool held = s->info.frozen;
    kpd207_information_tick(&s->info, &s->capture, now);
    if (!held && s->info.frozen) s->prefer_cable = false;
    observe_cable(s);
    if (s->data && s->pps_waiting) start_pps(s, now);
    if (s->data && !s->pages && freeze_result(s, now)) {
        if (render(s, now)) obj_invalidate(s->image);
        else close_screen(s);
    }
    if (s->pending) {
        uint8_t action = s->pending;
        s->pending = 0;
        if (action == 4 && !s->data) { open_result(s, layer, now); return; }
        if (!s->data) return;
        if (action == 3) { close_screen(s); return; }
        if (!s->pages) return;
        unsigned pages = s->pages;
        s->page = action == 1 ? s->page % pages + 1 : (s->page + pages - 2) % pages + 1;
        if (render(s, now)) obj_invalidate(s->image);
        else close_screen(s);
    }
}
