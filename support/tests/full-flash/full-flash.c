/*
 * MCS251 full-Flash scatter-link runtime fixture.
 *
 * The runner compiles this translation unit with --function-sections and
 * --data-sections, discovers the generated area names from the assembly, and
 * pins the four call targets on opposite sides of HOME.  Large, independently
 * placeable const objects make the linked image exceed the former contiguous
 * link limit while still fitting in the physical device window.
 */

#ifndef FULL_FLASH_BLOB_COUNT
#define FULL_FLASH_BLOB_COUNT 2
#endif

#ifndef FULL_FLASH_BLOB_BYTES
#define FULL_FLASH_BLOB_BYTES 60000UL
#endif

#ifndef FULL_FLASH_BLOB0_BYTES
#define FULL_FLASH_BLOB0_BYTES FULL_FLASH_BLOB_BYTES
#endif
#ifndef FULL_FLASH_BLOB1_BYTES
#define FULL_FLASH_BLOB1_BYTES FULL_FLASH_BLOB_BYTES
#endif
#ifndef FULL_FLASH_BLOB2_BYTES
#define FULL_FLASH_BLOB2_BYTES FULL_FLASH_BLOB_BYTES
#endif
#ifndef FULL_FLASH_BLOB3_BYTES
#define FULL_FLASH_BLOB3_BYTES FULL_FLASH_BLOB_BYTES
#endif

#ifndef FULL_FLASH_SWITCH_BOUNDARY_TEST
#define FULL_FLASH_SWITCH_BOUNDARY_TEST 0
#endif

#if FULL_FLASH_BLOB_COUNT < 1 || FULL_FLASH_BLOB_COUNT > 4
#error FULL_FLASH_BLOB_COUNT must be between 1 and 4
#endif

__sfr __at (0x99) SBUF;

typedef unsigned char (*full_flash_function) (unsigned char);

volatile __xdata unsigned char startup_initialized = 0x5a;
volatile __xdata unsigned char startup_cleared;
volatile __xdata unsigned char dispatch_index;
volatile __xdata unsigned int blob_index;
full_flash_function volatile __data dispatched_operation;

/* The absolute top byte proves that 0x1000000 is an exclusive window end and
 * that occupied CABS fragments participate in the allocator ledger. */
const volatile __code __at (0xffffff) unsigned char full_flash_last_byte = 0x5a;

unsigned char full_flash_low_leaf (unsigned char value)
  __attribute__ ((noinline));
unsigned char full_flash_high_leaf (unsigned char value)
  __attribute__ ((noinline));
unsigned char full_flash_low_direct (unsigned char value)
  __attribute__ ((noinline));
unsigned char full_flash_high_direct (unsigned char value)
  __attribute__ ((noinline));
#if FULL_FLASH_SWITCH_BOUNDARY_TEST
unsigned char full_flash_dense_switch (unsigned char selector)
  __attribute__ ((noinline));
unsigned char full_flash_ejmp_switch (unsigned char selector)
  __attribute__ ((noinline));
#endif

unsigned char
full_flash_low_leaf (unsigned char value)
{
  return value + 3;
}

unsigned char
full_flash_high_leaf (unsigned char value)
{
  return value ^ 0x5a;
}

/* This low-area function makes a direct ECALL into the upper HOME window. */
unsigned char
full_flash_low_direct (unsigned char value)
{
  return full_flash_high_leaf (value) + 1;
}

/* This high-area function makes a direct ECALL back below HOME. */
unsigned char
full_flash_high_direct (unsigned char value)
{
  return full_flash_low_leaf (value) + 2;
}

#if FULL_FLASH_SWITCH_BOUNDARY_TEST
/* With --opt-code-size, more than seven destinations select the compact
 * three-component address tables.  The runner pins the complete function 16
 * bytes before a 64 KiB boundary, forcing those tables across the boundary. */
unsigned char
full_flash_dense_switch (unsigned char selector)
{
  volatile unsigned char runtime_selector = selector;

  switch (runtime_selector)
    {
    case 0: return 0x30;
    case 1: return 0x31;
    case 2: return 0x32;
    case 3: return 0x33;
    case 4: return 0x34;
    case 5: return 0x35;
    case 6: return 0x36;
    case 7: return 0x37;
    case 8: return 0x38;
    case 9: return 0x39;
    case 10: return 0x3a;
    case 11: return 0x3b;
    case 12: return 0x3c;
    case 13: return 0x3d;
    case 14: return 0x3e;
    case 15: return 0x3f;
    case 16: return 0x40;
    case 17: return 0x41;
    case 18: return 0x42;
    case 19: return 0x43;
    default: return 0xee;
    }
}

/* Six destinations select an inline EJMP table.  This function is pinned 16
 * bytes before a different 64 KiB boundary. */
unsigned char
full_flash_ejmp_switch (unsigned char selector)
{
  volatile unsigned char runtime_selector = selector;

  switch (runtime_selector)
    {
    case 0: return 0x70;
    case 1: return 0x71;
    case 2: return 0x72;
    case 3: return 0x73;
    case 4: return 0x74;
    case 5: return 0x75;
    default: return 0xef;
    }
}
#endif

/* A code-resident constructor-style dispatch table exercises 24-bit function
 * relocations and indirect ECALLs across HOME. */
const __code full_flash_function full_flash_ctor_table[2] = {
  full_flash_low_direct,
  full_flash_high_direct,
};

/* Sparse designated initializers require the final byte to be present in the
 * Intel HEX image, so the runner can prove the complete section was placed. */
const __code unsigned char full_flash_blob0[FULL_FLASH_BLOB0_BYTES] = {
  [0] = 0x10,
  [FULL_FLASH_BLOB0_BYTES - 1] = 0xa0,
};

#if FULL_FLASH_BLOB_COUNT >= 2
const __code unsigned char full_flash_blob1[FULL_FLASH_BLOB1_BYTES] = {
  [0] = 0x11,
  [FULL_FLASH_BLOB1_BYTES - 1] = 0xa1,
};
#endif

#if FULL_FLASH_BLOB_COUNT >= 3
const __code unsigned char full_flash_blob2[FULL_FLASH_BLOB2_BYTES] = {
  [0] = 0x12,
  [FULL_FLASH_BLOB2_BYTES - 1] = 0xa2,
};
#endif

#if FULL_FLASH_BLOB_COUNT >= 4
const __code unsigned char full_flash_blob3[FULL_FLASH_BLOB3_BYTES] = {
  [0] = 0x13,
  [FULL_FLASH_BLOB3_BYTES - 1] = 0xa3,
};
#endif

unsigned char
__sdcc_external_startup (void)
{
  /* Startup must subsequently restore/clear these through XINIT/XSEG. */
  startup_initialized = 0;
  startup_cleared = 0xa5;
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
  unsigned char passed = 1;

  passed &= startup_initialized == 0x5a;
  passed &= startup_cleared == 0;
  passed &= full_flash_last_byte == 0x5a;

  /* Direct calls in both directions across the HOME island. */
  passed &= full_flash_low_direct (0x12) == 0x49;
  passed &= full_flash_high_direct (0x20) == 0x25;

#if FULL_FLASH_SWITCH_BOUNDARY_TEST
  passed &= full_flash_dense_switch (0) == 0x30;
  passed &= full_flash_dense_switch (10) == 0x3a;
  passed &= full_flash_dense_switch (19) == 0x43;
  passed &= full_flash_dense_switch (20) == 0xee;
  passed &= full_flash_ejmp_switch (0) == 0x70;
  passed &= full_flash_ejmp_switch (5) == 0x75;
  passed &= full_flash_ejmp_switch (6) == 0xef;
#endif

  /* Read both 24-bit entries from code space and call them indirectly. */
  dispatch_index = 0;
  dispatched_operation = full_flash_ctor_table[dispatch_index];
  passed &= dispatched_operation (0x12) == 0x49;
  dispatch_index = 1;
  dispatched_operation = full_flash_ctor_table[dispatch_index];
  passed &= dispatched_operation (0x20) == 0x25;

  /* Volatile runtime indices prevent compile-time substitution of the code
   * reads.  In particular, the final access carries through all 24 address
   * bits when a packed blob ends in the upper Flash region. */
  blob_index = 0;
  passed &= full_flash_blob0[blob_index] == 0x10;
  blob_index = FULL_FLASH_BLOB0_BYTES - 1;
  passed &= full_flash_blob0[blob_index] == 0xa0;
#if FULL_FLASH_BLOB_COUNT >= 2
  blob_index = 0;
  passed &= full_flash_blob1[blob_index] == 0x11;
  blob_index = FULL_FLASH_BLOB1_BYTES - 1;
  passed &= full_flash_blob1[blob_index] == 0xa1;
#endif
#if FULL_FLASH_BLOB_COUNT >= 3
  blob_index = 0;
  passed &= full_flash_blob2[blob_index] == 0x12;
  blob_index = FULL_FLASH_BLOB2_BYTES - 1;
  passed &= full_flash_blob2[blob_index] == 0xa2;
#endif
#if FULL_FLASH_BLOB_COUNT >= 4
  blob_index = 0;
  passed &= full_flash_blob3[blob_index] == 0x13;
  blob_index = FULL_FLASH_BLOB3_BYTES - 1;
  passed &= full_flash_blob3[blob_index] == 0xa3;
#endif

  print_result (passed);
  for (;;)
    {
    }
}
