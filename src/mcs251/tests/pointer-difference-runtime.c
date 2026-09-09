#include <stddef.h>

__sfr __at (0x99) SBUF;

#if defined(__SDCC_mcs251)
#define XDATA_SPAN 70000UL
#define XDATA_EXTENT 70016UL
#else
#define XDATA_SPAN 32768UL
#define XDATA_EXTENT 32784UL
#endif

/* This backing object gives both element types a real array provenance.  The
   test only forms and subtracts pointers; it never accesses these bytes.  In
   particular, the MCS-51 QEMU board maps only 1 KiB of physical XRAM, so this
   is an ABI/address-arithmetic regression, not a board-memory qualification. */
static __xdata union
{
    unsigned char bytes[XDATA_EXTENT];
    unsigned int words[XDATA_EXTENT / sizeof (unsigned int)];
} xdata_storage;
static __idata unsigned char idata_bytes[64];

_Static_assert (sizeof (unsigned int) == 2,
                "pointer scaling fixture assumes a two-byte unsigned int");

static ptrdiff_t
xdata_byte_difference (const unsigned char __xdata *left,
                       const unsigned char __xdata *right)
  __attribute__ ((noinline));

static ptrdiff_t
xdata_word_difference (const unsigned int __xdata *left,
                       const unsigned int __xdata *right)
  __attribute__ ((noinline));

static ptrdiff_t
idata_byte_difference (const unsigned char __idata *left,
                       const unsigned char __idata *right)
  __attribute__ ((noinline));

static ptrdiff_t
xdata_byte_difference (const unsigned char __xdata *left,
                       const unsigned char __xdata *right)
{
    return left - right;
}

static ptrdiff_t
xdata_word_difference (const unsigned int __xdata *left,
                       const unsigned int __xdata *right)
{
    return left - right;
}

static ptrdiff_t
idata_byte_difference (const unsigned char __idata *left,
                       const unsigned char __idata *right)
{
    return left - right;
}

unsigned char
__sdcc_external_startup (void)
{
    return 0;
}

static void
print_result (unsigned char failure)
{
    static const char hex[] = "0123456789abcdef";
    const char *text;

    if (failure)
        {
            SBUF = 'E';
            SBUF = hex[failure >> 4];
            SBUF = hex[failure & 0x0f];
            SBUF = '\n';
        }
    text = failure ? "FAIL\n" : "PASS\n";
    while (*text)
        SBUF = *text++;
}

void
main (void)
{
    unsigned char failure = 0;

    if (xdata_byte_difference (&xdata_storage.bytes[32767UL],
                               xdata_storage.bytes) !=
        32767L)
        failure |= 0x01;
    if (xdata_byte_difference (xdata_storage.bytes,
                               &xdata_storage.bytes[32767UL]) !=
        -32767L)
        failure |= 0x02;
    if (xdata_byte_difference (&xdata_storage.bytes[32768UL],
                               xdata_storage.bytes) !=
        32768L)
        failure |= 0x04;
    if (xdata_byte_difference (xdata_storage.bytes,
                               &xdata_storage.bytes[32768UL]) !=
        -32768L)
        failure |= 0x08;
    if (xdata_byte_difference (&xdata_storage.bytes[XDATA_SPAN],
                               xdata_storage.bytes) != (ptrdiff_t)XDATA_SPAN)
        failure |= 0x10;
    if (xdata_byte_difference (xdata_storage.bytes,
                               &xdata_storage.bytes[XDATA_SPAN]) !=
                               -(ptrdiff_t)XDATA_SPAN)
        failure |= 0x20;
    if (xdata_word_difference (
          &xdata_storage.words[XDATA_SPAN / sizeof (unsigned int)],
          xdata_storage.words) !=
          (ptrdiff_t)(XDATA_SPAN / 2) ||
        xdata_word_difference (
          xdata_storage.words,
          &xdata_storage.words[XDATA_SPAN / sizeof (unsigned int)]) !=
          -(ptrdiff_t)(XDATA_SPAN / 2))
        failure |= 0x40;
    if (idata_byte_difference (&idata_bytes[55], &idata_bytes[5]) != 50L ||
        idata_byte_difference (&idata_bytes[5], &idata_bytes[55]) != -50L)
        failure |= 0x80;

    print_result (failure);
    for (;;)
        {
        }
}
