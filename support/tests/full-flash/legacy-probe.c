/* The new section modes are opt-in.  Without them these definitions must
 * continue to use the traditional CSEG and CONST areas. */

__sfr __at (0x99) SBUF;

const __code unsigned char full_flash_legacy_constant[3] = {
  0x12, 0x34, 0x56,
};

unsigned char
full_flash_legacy_function (unsigned char value)
{
  return value + full_flash_legacy_constant[1];
}

unsigned char
__sdcc_external_startup (void)
{
  return 0;
}

void
main (void)
{
  if (full_flash_legacy_function (0x0c) == 0x40)
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
