#include <stdint.h>

static uint16_t add16(uint16_t left, uint16_t right)
{
  return (uint16_t)(left + right);
}

int main(void)
{
  volatile uint16_t value = add16(0x1200u, 0x0034u);
  return value == 0x1234u ? 0 : 1;
}
