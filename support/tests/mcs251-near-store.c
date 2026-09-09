/* Regression: a live fixed-register byte used to produce MOV @Ri,R13.
 * Compile with TEST_MEMORY=__data and __idata, then run the assembler.
 * Volatile RAM preserves the register pressure in the comparison loop.
 */
typedef unsigned char u8;
typedef unsigned int u16;
static volatile u8 TEST_MEMORY __at(0x40) result[24];
static const u8 __code patterns[3] = {255, 170, 85};

void reproduce(void)
{
    volatile u8 __xdata *ram = (volatile u8 __xdata *)0x020000UL;
    u8 pattern_index, value, pattern, result_index;
    u16 offset, count;
    for (pattern_index = 0; pattern_index < 3; ++pattern_index) {
        result_index = pattern_index * 8;
        pattern = patterns[pattern_index];
        for (offset = 0; offset < 256; ++offset) ram[offset] = pattern;
        count = 0;
        for (offset = 0; offset < 256; ++offset) {
            value = ram[offset];
            if (value != pattern) {
                if (!count) result[result_index + 5] = value;
                ++count;
                result[result_index + 6] |= pattern ^ value;
                if ((offset & 63) == 7) ++result[result_index + 7];
            }
        }
    }
}
