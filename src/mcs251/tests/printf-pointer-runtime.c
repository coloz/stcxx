#include <stdio.h>
#include <string.h>

__sfr __at (0x99) SBUF;

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

void
main (void)
{
    char text[12];
    int length;

    length = sprintf (text, "%p", (void *)0x123456UL);
    print_result (length == 8 && strcmp (text, "0x123456") == 0);
    for (;;)
        {
        }
}
