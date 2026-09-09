#include <stdlib.h>

__sfr __at (0x99) SBUF;

extern const unsigned long __sdcc_heap_size32;

unsigned long
mcs251_heap_size_abi_probe (void)
{
    return __sdcc_heap_size32;
}

unsigned char
__sdcc_external_startup (void)
{
    return 0;
}

static void
print_result (unsigned char passed)
{
    const char *text = passed ? "PASS\n" : "FAIL\n";

    while (*text)
        SBUF = *text++;
}

static void
print_failure_mask (unsigned char failure)
{
    static const char hex[] = "0123456789abcdef";

    if (!failure)
        return;
    SBUF = 'E';
    SBUF = hex[failure >> 4];
    SBUF = hex[failure & 0x0f];
    SBUF = '\n';
}

void
main (void)
{
    unsigned char __xdata *allocation;
    void __xdata *overflow;
    void __xdata *reused;
    unsigned char failure = 0;

    if (mcs251_heap_size_abi_probe () != 70000UL)
        failure |= 0x01;

    allocation = malloc (69000UL);
    if (allocation)
        {
            allocation[0] = 0x12;
            allocation[65535UL] = 0x34;
            allocation[68999UL] = 0x56;
        }
    else
        failure |= 0x02;
    overflow = malloc (1024UL);
    if (overflow)
        failure |= 0x04;
    if (allocation && allocation[0] != 0x12)
        failure |= 0x08;
    if (allocation && allocation[65535UL] != 0x34)
        failure |= 0x10;
    if (allocation && allocation[68999UL] != 0x56)
        failure |= 0x20;
    free (allocation);
    reused = malloc (69000UL);
    if (!reused)
        failure |= 0x40;

    print_failure_mask (failure);
    print_result (!failure);
    for (;;)
        {
        }
}
