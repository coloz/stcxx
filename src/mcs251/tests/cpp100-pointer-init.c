/* A 24-bit MCS-251 pointer is a flat big-endian address, not the
 * MCS-51 little-endian address followed by an address-space tag.
 * CBE represents Itanium virtual-base offsets as pointer constants. */
#include <stdint.h>
__sfr __at(0x99) SBUF;
#define P(x) ((void *)(uintptr_t)(x))
static void *const pointers[] = {
  P(0), P(1), P(2), P(3), P(7), P(15), P(31), P(127), P(255), P(256),
  P(257), P(65535), P(65536), P(0x7fffffffUL), P(0x80000000UL),
  P(-1L), P(0x12345678UL), P(0x89abcdefUL), P(0x01020304UL), P(-65535L)
};
static void hex(uint32_t x) {
  unsigned char i;
  for(i=0;i<8;++i) { unsigned char b=x>>28; SBUF=b<10?'0'+b:'A'+b-10; x<<=4; }
  SBUF='\n';
}
void main(void) {
  volatile unsigned char i;
  for(i=0;i<20;++i) hex((uintptr_t)pointers[i]);
  SBUF='D';SBUF='O';SBUF='N';SBUF='E';SBUF='\n';
  for(;;){}
}
