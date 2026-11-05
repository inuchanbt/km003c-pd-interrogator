/* SPDX-License-Identifier: MIT
 * Minimal headers for offline freestanding probes, not a board SDK. */
#ifndef NDEBUG
#error Freestanding probes require -DNDEBUG; host tests retain upstream assertions.
#endif
#define assert(expression) ((void)0)
