/* SPDX-License-Identifier: MIT
 * Exact stock-2.0.7/HW2.3 LVGL ABI. All storage belongs to the native heap.
 * Export only the observer's owned, stable PDO result, never idle cache bytes.
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
#define STOCK_KEYPAD_READ 0x24607u

typedef uint8_t (*signal_cb)(void *, uint8_t, void *);
typedef bool (*keypad_cb)(void *, void *);
struct image_descriptor { uint32_t header, size; const uint8_t *pixels; };
struct screen_data {
    struct kpd_transfer transfer;
    struct kpd_workspace work;
    uint8_t pixels[8 + 240 * 30];
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
    const char chars[] = "0123456789ACDEILNOPRSTUVX/GW ";
    const uint16_t glyphs[] = {0x7b6f,0x2c97,0x73e7,0x73cf,0x5bc9,0x79cf,0x79ef,0x7292,0x7bef,0x7bcf,
        0x2bed,0x7927,0x6b6e,0x79a7,0x7497,0x4927,0x5ffd,0x7b6f,0x7be4,0x6bad,0x79cf,0x7492,0x5b6f,0x5b6a,0x5aad,0x12a4,0x797f,0x5bfd,0};
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

static bool render(struct screen_state *s) {
    struct screen_data *d = s->data;
    uint8_t row[480];
    memset(d->pixels, 0, sizeof(d->pixels));
    memset(d->pixels, 255, 4); /* Native 32-bit palette: opaque white, black */
    d->pixels[7] = 255;
    if (!d->transfer.ready) {
        bool reading = s->info.attached && s->info.stage != KPD207_INFO_DONE;
        text(d, reading ? "READING PD" : s->random.phase == 3 ? "ID ERROR" : "NO DATA", 42, 80, 4);
        text(d, reading ? "PLEASE WAIT" : "RUN PD TEST", 34, 120, 4);
        text(d, "EXIT CLOSE", 42, 170, 4);
        return true;
    }
    if (!kpd_encode_page(&d->transfer, s->page, &d->work)) return false;
    for (unsigned y = 0; y < 240; ++y) {
        if (!kpd_qr_row(&d->work, y, row)) return false;
        for (unsigned x = 0; x < 240; ++x)
            if (row[x * 2] == 0)
                d->pixels[8 + y * 30 + x / 8] |= (uint8_t)(0x80u >> (x % 8));
    }
    /* Up to 61 pages for the bounded maximum schema, outside quiet zone. */
    char label[] = "SAVED 01/01";
    label[6] = (char)('0' + s->page / 10); label[7] = (char)('0' + s->page % 10);
    label[9] = (char)('0' + d->transfer.pages / 10); label[10] = (char)('0' + d->transfer.pages % 10);
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

static void open_result(struct screen_state *s, void *layer) {
    if (!s->input) return;
    s->old_group = *(void **)((uint8_t *)s->input + 0x5c);
    s->data = mem_alloc(sizeof(*s->data));
    if (!s->data) return;
    s->page = 1;
    memset(&s->data->transfer, 0, sizeof(s->data->transfer));
    if (!s->info.attached || s->info.stage == KPD207_INFO_DONE)
        (void)kpd207_info_freeze(&s->capture, &s->info, &s->data->transfer);
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
    kpd207_information_tick(&s->info, &s->capture, now);
    if (s->data && !s->data->transfer.ready &&
        (!s->info.attached || s->info.stage == KPD207_INFO_DONE) &&
        kpd207_info_freeze(&s->capture, &s->info, &s->data->transfer)) {
        if (render(s)) obj_invalidate(s->image);
        else close_screen(s);
    }
    if (s->pending) {
        uint8_t action = s->pending;
        s->pending = 0;
        if (action == 4 && !s->data) { open_result(s, layer); return; }
        if (!s->data) return;
        if (action == 3) { close_screen(s); return; }
        if (!s->data->transfer.ready) return;
        unsigned pages = s->data->transfer.pages;
        s->page = action == 1 ? s->page % pages + 1 : (s->page + pages - 2) % pages + 1;
        if (render(s)) obj_invalidate(s->image);
        else close_screen(s);
    }
}
