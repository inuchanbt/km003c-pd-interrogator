/* SPDX-License-Identifier: MIT. Minimal freestanding C runtime, no allocation. */
#include "kpd.h"
#include <string.h>

void *memcpy(void *dest, const void *src, size_t length) {
    uint8_t *d = dest;
    const uint8_t *s = src;
    while (length-- != 0) *d++ = *s++;
    return dest;
}
void *memmove(void *dest, const void *src, size_t length) {
    uint8_t *d = dest;
    const uint8_t *s = src;
    if ((uintptr_t)d < (uintptr_t)s) return memcpy(dest, src, length);
    while (length != 0) { --length; d[length] = s[length]; }
    return dest;
}
void *memset(void *dest, int value, size_t length) {
    uint8_t *d = dest;
    while (length-- != 0) *d++ = (uint8_t)value;
    return dest;
}
void __aeabi_memclr4(void *dest, size_t length) { (void)memset(dest, 0, length); }
size_t strlen(const char *text) {
    size_t length = 0;
    while (text[length] != '\0') ++length;
    return length;
}
char *strchr(const char *text, int character) {
    do {
        if (*text == (char)character) return (char *)text;
    } while (*text++ != '\0');
    return NULL;
}
