/*
 * MCS251 is big-endian.  When register allocation keeps only the low byte of
 * a scalar loaded through a pointer, the memory access must start at the low
 * byte of the wider object.  This local-array shape selects the far-pointer
 * lowering used by the LLVM-CBE IPv6 parser.
 */

typedef unsigned char uint8_t;
typedef unsigned int uint16_t;
typedef unsigned long uint32_t;

extern void mcs251_fill_words(uint16_t *words);

uint8_t
mcs251_load_far_u16_low8(uint8_t index)
{
    uint16_t words[8];
    uint16_t value;

    mcs251_fill_words(words);
    value = *(uint16_t *)&words
        [(long)(((uint32_t)(uint8_t)index) & 16777215UL)];
    return (uint8_t)value;
}
