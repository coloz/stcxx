#include <stdint.h>

volatile uint16_t result;

int main(void)
{
    result = 42u;
    for (;;) { }
}
