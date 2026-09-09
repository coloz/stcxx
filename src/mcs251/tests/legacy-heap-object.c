/* This deliberately models the legacy custom-heap object layout.  New
   MCS251 malloc objects must reject it at link time instead of reading four
   bytes from the old two-byte size symbol. */
__xdata unsigned char __sdcc_heap[64];
const unsigned int __sdcc_heap_size = sizeof (__sdcc_heap);
