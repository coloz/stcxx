/* Taking the address of a subscripted pointer lvalue must preserve the
 * pointer's address space.  The redundant and canonical spellings below
 * are semantically identical; keep both so the frontend cannot silently
 * narrow a generic/code/xdata pointer while decorating &base[index]. */

__sfr __at (0x99) SBUF;

typedef unsigned char byte;

const __code byte generic_roundtrip_code_bytes[] =
  {0x13, 0x57, 0x9b, 0xdf};
volatile __xdata byte generic_roundtrip_xdata[8];

byte generic_load_redundant (const byte *base, unsigned int index)
  __attribute__ ((noinline));
byte generic_load_canonical (const byte *base, unsigned int index)
  __attribute__ ((noinline));
void generic_store_redundant (byte *base, unsigned int index, byte value)
  __attribute__ ((noinline));
void generic_store_canonical (byte *base, unsigned int index, byte value)
  __attribute__ ((noinline));

byte xdata_load_redundant (const __xdata byte *base, unsigned int index)
  __attribute__ ((noinline));
byte xdata_load_canonical (const __xdata byte *base, unsigned int index)
  __attribute__ ((noinline));
void xdata_store_redundant (__xdata byte *base, unsigned int index,
                            byte value) __attribute__ ((noinline));
void xdata_store_canonical (__xdata byte *base, unsigned int index,
                            byte value) __attribute__ ((noinline));

byte code_load_redundant (const __code byte *base, unsigned int index)
  __attribute__ ((noinline));
byte code_load_canonical (const __code byte *base, unsigned int index)
  __attribute__ ((noinline));

byte
generic_load_redundant (const byte *base, unsigned int index)
{
  return *(const byte *)(&((const byte *)base)[index]);
}

byte
generic_load_canonical (const byte *base, unsigned int index)
{
  return ((const byte *)base)[index];
}

void
generic_store_redundant (byte *base, unsigned int index, byte value)
{
  *(byte *)(&((byte *)base)[index]) = value;
}

void
generic_store_canonical (byte *base, unsigned int index, byte value)
{
  ((byte *)base)[index] = value;
}

byte
xdata_load_redundant (const __xdata byte *base, unsigned int index)
{
  return *(const __xdata byte *)(&((const __xdata byte *)base)[index]);
}

byte
xdata_load_canonical (const __xdata byte *base, unsigned int index)
{
  return ((const __xdata byte *)base)[index];
}

void
xdata_store_redundant (__xdata byte *base, unsigned int index, byte value)
{
  *(__xdata byte *)(&((__xdata byte *)base)[index]) = value;
}

void
xdata_store_canonical (__xdata byte *base, unsigned int index, byte value)
{
  ((__xdata byte *)base)[index] = value;
}

byte
code_load_redundant (const __code byte *base, unsigned int index)
{
  return *(const __code byte *)(&((const __code byte *)base)[index]);
}

byte
code_load_canonical (const __code byte *base, unsigned int index)
{
  return ((const __code byte *)base)[index];
}

unsigned char
__sdcc_external_startup (void)
{
  return 0;
}

void
main (void)
{
  volatile __xdata byte *high =
    generic_roundtrip_xdata;
  byte failed = 0;

  high[0] = 0x21;
  high[1] = 0x43;
  high[2] = 0x65;
  high[3] = 0x87;
  high[4] = 0x00;
  high[5] = 0x00;
  high[6] = 0x00;
  high[7] = 0x00;

  failed |= generic_load_redundant ((const byte *)high, 1) ^ 0x43;
  failed |= generic_load_canonical ((const byte *)high, 2) ^ 0x65;
  failed |= xdata_load_redundant ((const __xdata byte *)high, 2) ^ 0x65;
  failed |= xdata_load_canonical ((const __xdata byte *)high, 3) ^ 0x87;
  failed |= code_load_redundant (generic_roundtrip_code_bytes, 1) ^ 0x57;
  failed |= code_load_canonical (generic_roundtrip_code_bytes, 2) ^ 0x9b;
  failed |= generic_load_redundant (
    (const byte *)generic_roundtrip_code_bytes, 3) ^ 0xdf;

  generic_store_redundant ((byte *)high, 4, 0xa1);
  generic_store_canonical ((byte *)high, 5, 0xb2);
  xdata_store_redundant ((__xdata byte *)high, 6, 0xc3);
  xdata_store_canonical ((__xdata byte *)high, 7, 0xd4);
  failed |= high[4] ^ 0xa1;
  failed |= high[5] ^ 0xb2;
  failed |= high[6] ^ 0xc3;
  failed |= high[7] ^ 0xd4;

  if (!failed)
    {
      SBUF = 'P';
      SBUF = 'A';
      SBUF = 'S';
      SBUF = 'S';
    }
  else
    {
      SBUF = 'F';
      SBUF = 'A';
      SBUF = 'I';
      SBUF = 'L';
    }
  SBUF = '\n';

  for (;;)
    {
    }
}
