#include <stdlib.h>

__sfr __at (0x99) SBUF;

/* User data starts at the second target pointer in malloc's header.  This
   request consumes every byte of the allocator's 63-byte usable interval. */
#ifndef MCS251_EXTERNAL_TEST_HEAP
__xdata unsigned char __sdcc_heap[64];
#if defined(__SDCC_mcs251)
const size_t __sdcc_heap_size32 = sizeof (__sdcc_heap);
#else
const unsigned int __sdcc_heap_size = sizeof (__sdcc_heap);
#endif
#else
extern __xdata unsigned char __sdcc_heap[64];
#endif

#define EXHAUSTING_REQUEST \
    (sizeof (__sdcc_heap) - 1 - sizeof (void __xdata *))

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
finish (unsigned char failure)
{
    static const char hex[] = "0123456789abcdef";

    if (failure)
        {
            SBUF = 'E';
            SBUF = hex[failure >> 4];
            SBUF = hex[failure & 0x0f];
            SBUF = '\n';
        }
    print_result (!failure);
    for (;;)
        {
        }
}

void
main (void)
{
    void __xdata *first = malloc (EXHAUSTING_REQUEST);
    void __xdata *second = malloc (1);
    unsigned char __xdata *a;
    unsigned char __xdata *b;
    unsigned char __xdata *c;
    unsigned char __xdata *grown;
    unsigned char __xdata *merged;
    unsigned char failure = 0;

    if (!first)
        failure |= 0x01;
    if (second)
        failure |= 0x02;
    if (failure)
        finish (failure);

    ((unsigned char __xdata *)first)[0] = 0xa5;
    ((unsigned char __xdata *)first)[EXHAUSTING_REQUEST - 1] = 0x5a;
    grown = realloc (first, EXHAUSTING_REQUEST + 1);
    if (grown ||
        ((unsigned char __xdata *)first)[0] != 0xa5 ||
        ((unsigned char __xdata *)first)[EXHAUSTING_REQUEST - 1] != 0x5a)
        failure |= 0x04;
    if (failure)
        finish (failure);
    free (first);

    /* Exercise the split path repeatedly.  Distinct end-point canaries catch
       both exact aliases and partially overlapping blocks. */
    a = malloc (8);
    b = malloc (9);
    c = malloc (7);
    if (!a || !b || !c || a == b || a == c || b == c)
        failure |= 0x08;
    if (failure)
        finish (failure);

    a[0] = 0xa1;
    a[7] = 0xa7;
    b[0] = 0xb1;
    b[8] = 0xb8;
    c[0] = 0xc1;
    c[6] = 0xc6;
    if (a[0] != 0xa1 || a[7] != 0xa7 ||
        b[0] != 0xb1 || b[8] != 0xb8 ||
        c[0] != 0xc1 || c[6] != 0xc6)
        failure |= 0x10;

    /* Free the following block and grow a into it.  realloc must preserve
       the old payload and must not disturb the still-live third block. */
    free (b);
    grown = realloc (a, 15);
    if (!grown || grown[0] != 0xa1 || grown[7] != 0xa7 ||
        c[0] != 0xc1 || c[6] != 0xc6)
        failure |= 0x20;
    if (grown)
        {
            grown[8] = 0x18;
            grown[14] = 0x1e;
            if (grown[0] != 0xa1 || grown[7] != 0xa7 ||
                grown[8] != 0x18 || grown[14] != 0x1e ||
                c[0] != 0xc1 || c[6] != 0xc6)
                failure |= 0x20;
            free (grown);
        }
    else
        free (a);
    free (c);

    /* The three adjacent blocks and their tail must coalesce back into the
       original interval, which can once again be exhausted exactly. */
    merged = malloc (EXHAUSTING_REQUEST);
    second = malloc (1);
    if (!merged || second)
        failure |= 0x40;
    if (merged)
        {
            merged[0] = 0x3c;
            merged[EXHAUSTING_REQUEST - 1] = 0xc3;
            if (merged[0] != 0x3c ||
                merged[EXHAUSTING_REQUEST - 1] != 0xc3)
                failure |= 0x80;
        }

    finish (failure);
}
