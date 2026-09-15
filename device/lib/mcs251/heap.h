/* Private MCS251 allocator ABI, shared by malloc and heap initialization.
 * The free/realloc implementations use the same two-pointer header layout.
 * SPDX-License-Identifier: GPL-2.0-or-later WITH SDCC-exception-2.0
 */
#ifndef SDCC_MCS251_HEAP_H
#define SDCC_MCS251_HEAP_H

#include <stddef.h>
#define HEAPSPACE __xdata
typedef struct header HEAPSPACE header_t;
struct header
{
    header_t *next;
    header_t *next_free;
};

typedef char __sdcc_heap_pointer_width[sizeof(header_t *) == 3 ? 1 : -1];
typedef char __sdcc_heap_header_width[sizeof(struct header) == 6 ? 1 : -1];
typedef char __sdcc_heap_payload_offset[offsetof(struct header, next_free) == 3 ? 1 : -1];

extern header_t *HEAPSPACE __sdcc_heap_free;
/* A null free-list head may denote exhaustion; it is not an init flag. */
extern unsigned char __sdcc_heap_initialized;
void __sdcc_heap_init(void);

#endif
