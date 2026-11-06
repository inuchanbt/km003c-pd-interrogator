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
#define indev_next FN(0x30ab4, void *, void *)
#define indev_set_group FN(0x30c9c, void, void *, void *)
#define tick_get FN(0x3c0d0, uint32_t, void)
#define stock_modal FN(0x33a72, void *, void)
#define STOCK_KEYPAD_READ 0x24607u

typedef uint8_t (*signal_cb)(void *, uint8_t, void *);
typedef bool (*keypad_cb)(void *, void *);
struct image_descriptor { uint32_t header, size; const uint8_t *pixels; };
struct screen_data {
    _Alignas(uint32_t) uint8_t pixels[8 + 240 * 30];
    uint8_t pages;
    bool cable;
};
struct power_screen { struct screen_data display; struct kpd_transfer transfer; };
struct cable_screen { struct screen_data display; char urls[2][KPD_URL_MAX + 1]; };
struct cable_result {
    void *table;
    uint32_t vdos[4];
    uint8_t id[8];
    bool saved;
};
struct screen_state {
    /* Image source is also the callback's route to this state. */
    struct image_descriptor descriptor;
    signal_cb anchor_signal, image_signal;
    void *image, *input, *old_group, *group;
    struct screen_data *data;
    uint8_t page, pending;
    keypad_cb keypad_read;
    uint32_t chord_since;
    bool chord, chord_fired;
    struct kpd207_capture capture;
    struct kpd207_random random;
    struct kpd207_info info;
    struct cable_result cable;
    bool prefer_cable;
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
    if (!s->data && !left_up && !right_up && !s->chord) {
        s->chord = true;
        s->chord_fired = false;
        s->chord_since = tick_get();
    }
    if (s->chord) {
        if (!left_up && !right_up && !s->chord_fired &&
            (uint32_t)(tick_get() - s->chord_since) >= 750) {
            s->pending = 4;
            s->chord_fired = true;
        }
        ((uint8_t *)data)[14] = 0; /* Native lv_indev_data_t.state = released */
        if (left_up && right_up) s->chord = false;
    }
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

static void text(struct screen_data *d, const char *str, unsigned x, unsigned y, unsigned scale) {
    /* Three columns/five rows, top bit first. Code-native bitmap text. */
    const char chars[] = "0123456789ACDEILNOPRSTUVX/GWB ";
    const uint16_t glyphs[] = {0x7b6f,0x2c97,0x73e7,0x73cf,0x5bc9,0x79cf,0x79ef,0x7292,0x7bef,0x7bcf,
        0x2bed,0x7927,0x6b6e,0x79a7,0x7497,0x4927,0x5ffd,0x7b6f,0x7be4,0x6bad,0x79cf,0x7492,0x5b6f,0x5b6a,0x5aad,0x12a4,0x797f,0x5bfd,0x6bae,0};
    for (unsigned c = 0; str[c]; ++c) {
        unsigned g = 0;
        while (chars[g] && chars[g] != str[c]) ++g;
        if (!chars[g]) continue;
        for (unsigned yy = 0; yy < 5 * scale; ++yy)
            for (unsigned xx = 0; xx < 3 * scale; ++xx)
                if (glyphs[g] & (1u << (14 - yy / scale * 3 - xx / scale))) {
                    unsigned px = x + c * 4 * scale + xx, py = y + yy;
                    if (px < 240 && py < 240)
                        d->pixels[8 + py * 30 + px / 8] |= (uint8_t)(0x80u >> (px % 8));
                }
    }
}

static bool cable_mode(void) {
    return *(const volatile uint8_t *)0x20000004u == 5;
}

static bool render(struct screen_state *s) {
    struct screen_data *d = s->data;
    struct kpd_workspace work;
    uint8_t row[480];
    memset(d->pixels, 0, sizeof(d->pixels));
    memset(d->pixels, 255, 4); /* Native 32-bit palette: opaque white, black */
    d->pixels[7] = 255;
    if (!d->pages) {
        bool reading = s->info.attached && s->info.stage != KPD207_INFO_DONE;
        text(d, reading ? "READING PD" : s->random.phase == 3 ? "ID ERROR" : "NO DATA", 42, 80, 4);
        text(d, reading ? "PLEASE WAIT" : cable_mode() ? "RUN CABLE TEST" : "RUN PD TEST", 10, 120, 4);
        text(d, "EXIT CLOSE", 42, 170, 4);
        return true;
    }
    if (d->cable) {
        const struct cable_screen *screen = (const struct cable_screen *)d;
        memcpy(work.url, screen->urls[s->page - 1], sizeof(work.url));
        if (!kpd_encode_url(&work)) return false;
    } else if (!kpd_encode_page(&((struct power_screen *)d)->transfer, s->page, &work)) return false;
    for (unsigned y = 0; y < 240; ++y) {
        if (!kpd_qr_row(&work, y, row)) return false;
        for (unsigned x = 0; x < 240; ++x)
            if (row[x * 2] == 0)
                d->pixels[8 + y * 30 + x / 8] |= (uint8_t)(0x80u >> (x % 8));
    }
    /* Up to 61 pages for the bounded maximum schema, outside quiet zone. */
    char label[12];
    memcpy(label, d->cable ? "CABLE 01/01" : "SAVED 01/01", sizeof(label));
    label[6] = (char)('0' + s->page / 10); label[7] = (char)('0' + s->page % 10);
    label[9] = (char)('0' + d->pages / 10); label[10] = (char)('0' + d->pages % 10);
    text(d, label, 98, 235, 1);
    return true;
}

static void close_screen(struct screen_state *s) {
    /* Restore ownership before deleting the object/group. No deletion in signals. */
    if (s->input) indev_set_group(s->input, s->old_group);
    if (s->image) obj_del(s->image);
    if (s->group) group_delete(s->group);
    if (s->data) mem_free(s->data);
    s->image = s->group = s->data = NULL;
}

static bool read_vdo(const char *text, uint32_t *out) {
    if (!text || text[0] != '0' || text[1] != 'x') return false;
    uint32_t word = 0;
    for (unsigned i = 2; i < 10; ++i) {
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
    if (text[10]) return false;
    *out = word;
    return true;
}

static bool same_text(const char *a, const char *b) {
    while (*a && *a == *b) { ++a; ++b; }
    return *a == *b;
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
    if (table == s->cable.table) return;
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
        if ((type != 3 && type != 4) || !vdos[3] ||
            !kpd207_capture_take_id(&s->capture, s->cable.id)) return;
        memcpy(s->cable.vdos, vdos, sizeof(vdos));
        s->cable.saved = true;
        s->prefer_cable = true;
        return;
    }
}

__attribute__((noinline))
static bool freeze_cable(struct screen_state *s) {
    /* Store only the two bounded URLs while the native result table is open. */
    struct cable_screen *screen = (struct cable_screen *)s->data;
    struct kpd_transfer transfer;
    struct kpd_snapshot snapshot = {.origin = KPD_KM003C_CABLE, .capture_status = 1};
    memcpy(snapshot.id, s->cable.id, 8);
    snapshot.fields[KPD_CABLE_IDENTITY] = (struct kpd_field){
        (const uint8_t *)s->cable.vdos, sizeof(s->cable.vdos), KPD_OBSERVED, true};
    if (!kpd_freeze(&snapshot, &transfer) || transfer.pages != 2 ||
        !kpd_page_url(&transfer, 1, screen->urls[0]) ||
        !kpd_page_url(&transfer, 2, screen->urls[1])) return false;
    screen->display.pages = transfer.pages;
    return true;
}

static bool freeze_result(struct screen_state *s) {
    if (s->info.attached && s->info.stage != KPD207_INFO_DONE) return false;
    if (s->data->cable) {
        if (!s->prefer_cable || (cable_mode() && !s->cable.saved)) return false;
        return freeze_cable(s);
    }
    struct kpd_transfer *transfer = &((struct power_screen *)s->data)->transfer;
    if (!kpd207_info_freeze(&s->capture, &s->info, transfer)) return false;
    s->data->pages = transfer->pages;
    return true;
}

static void open_result(struct screen_state *s, void *layer) {
    if (!s->input) return;
    s->old_group = *(void **)((uint8_t *)s->input + 0x5c);
    bool cable = cable_mode() || (s->prefer_cable && !s->info.attached);
    s->data = mem_alloc(cable ? sizeof(struct cable_screen) : sizeof(struct power_screen));
    if (!s->data) return;
    s->page = 1;
    s->data->pages = 0;
    s->data->cable = cable;
    if (!cable) memset(&((struct power_screen *)s->data)->transfer, 0, sizeof(struct kpd_transfer));
    (void)freeze_result(s);
    if (!render(s)) goto failed;
    s->descriptor.header = 7u | (240u << 10) | (240u << 21); /* INDEXED_1BIT */
    s->descriptor.size = sizeof(s->data->pixels);
    s->descriptor.pixels = s->data->pixels;
    s->group = group_create();
    if (!s->group) goto failed;
    s->image = img_create(layer, NULL);
    if (!s->image) goto failed;
    img_set_src(s->image, &s->descriptor);
    s->image_signal = obj_signal(s->image);
    obj_set_signal(s->image, image_signal);
    group_add(s->group, s->image);
    indev_set_group(s->input, s->group);
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
    if (s->data && !s->data->pages && freeze_result(s)) {
        if (render(s)) obj_invalidate(s->image);
        else close_screen(s);
    }
    if (s->pending) {
        uint8_t action = s->pending;
        s->pending = 0;
        if (action == 4 && !s->data) { open_result(s, layer); return; }
        if (!s->data) return;
        if (action == 3) { close_screen(s); return; }
        if (!s->data->pages) return;
        unsigned pages = s->data->pages;
        s->page = action == 1 ? s->page % pages + 1 : (s->page + pages - 2) % pages + 1;
        if (render(s)) obj_invalidate(s->image);
        else close_screen(s);
    }
}
