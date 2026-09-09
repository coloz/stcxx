/* MCS251 objects must retain their 24-bit extent in assembler reservations. */
#ifndef OBJECT_SIZE
#define OBJECT_SIZE 65536UL
#endif

#if defined(TEST_CODE)
const __code unsigned char large_object[OBJECT_SIZE];
#elif defined(TEST_INITIALIZED)
__xdata unsigned char large_object[OBJECT_SIZE] = { 0x5a };
#else
__xdata unsigned char large_object[OBJECT_SIZE];
#endif

unsigned long object_size(void)
{
    return sizeof large_object;
}

void main(void)
{
    for (;;) {}
}
