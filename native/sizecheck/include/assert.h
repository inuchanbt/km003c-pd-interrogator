/* SPDX-License-Identifier: MIT
 * Minimal headers for an offline, freestanding SIZE CHECK, not a board SDK. */
#ifndef NDEBUG
#error This size check requires -DNDEBUG; host tests retain upstream assertions.
#endif
#define assert(expression) ((void)0)
