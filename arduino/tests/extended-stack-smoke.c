static unsigned int add16(unsigned int left, unsigned int right)
{
  return (unsigned int)(left + right);
}

int main(void)
{
  volatile unsigned int value = add16(0x1200u, 0x0034u);
  return value == 0x1234u ? 0 : 1;
}
