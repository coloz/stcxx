/* Loading a named far object is a DPX clobber.  It must invalidate a cached
 * rematerializable pointer or a later dereference can reuse the named
 * object's address instead of reloading the pointer. */

__sfr __at (0x99) SBUF;

static volatile __xdata unsigned char intervening_far_object;

static unsigned char
read_around_far_symbol (void) __attribute__ ((noinline));

static unsigned char
read_around_far_symbol (void)
{
  unsigned char before;
  unsigned char middle;
  unsigned char after;

  before = *(volatile __xdata unsigned char *)0x010020UL;
  middle = intervening_far_object;
  after = *(volatile __xdata unsigned char *)0x010020UL;

  return (before ^ 0x5a) | (middle ^ 0xa5) | (after ^ 0x5a);
}

unsigned char
__sdcc_external_startup (void)
{
  return 0;
}

void
main (void)
{
  unsigned char failed;

  *(volatile __xdata unsigned char *)0x010020UL = 0x5a;
  intervening_far_object = 0xa5;
  failed = read_around_far_symbol ();

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
