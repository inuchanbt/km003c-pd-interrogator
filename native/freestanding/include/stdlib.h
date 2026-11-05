/* SPDX-License-Identifier: MIT. Minimal offline probe declarations. */
static inline int abs(int x) { return x < 0 ? -x : x; }
static inline long labs(long x) { return x < 0 ? -x : x; }
