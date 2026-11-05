/* SPDX-License-Identifier: MIT. Minimal offline probe declarations. */
#include <stddef.h>
void *memcpy(void *dest, const void *src, size_t length);
void *memmove(void *dest, const void *src, size_t length);
void *memset(void *dest, int value, size_t length);
int memcmp(const void *left, const void *right, size_t length);
size_t strlen(const char *text);
char *strchr(const char *text, int character);
