/* An indirect call is a DPX clobber boundary.  In particular, the second
 * access to the same rematerializable far pointer must reload DPX even when
 * the first access made it eligible for the code-generator pointer cache. */

__sfr __at (0x99) SBUF;

typedef void (*callback_t) (void);

static void
touch_other_far_address (void) __attribute__ ((noinline));

static unsigned char
read_around_indirect_call (callback_t callback)
  __attribute__ ((noinline));

static void
touch_other_far_address (void)
{
  *(volatile __xdata unsigned char *)0x010041UL = 0xa5;
}

static unsigned char
read_around_indirect_call (callback_t callback)
{
  unsigned char before;
  unsigned char after;

  before = *(volatile __xdata unsigned char *)0x010020UL;
  callback ();
  after = *(volatile __xdata unsigned char *)0x010020UL;

  return (before ^ 0x5a) | (after ^ 0x5a);
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
  *(volatile __xdata unsigned char *)0x010041UL = 0x00;
  failed = read_around_indirect_call (touch_other_far_address);

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
