#include <stdint.h>
#include <new>

extern "C" uint16_t native_add(uint16_t left, uint16_t right);
volatile uint16_t result;

template<unsigned N> struct Counter {
    uint16_t value;
    Counter() : value(native_add(N, 0u)) { }
    void increment() { value = native_add(value, 1u); }
};

Counter<41> counter;

int main(void)
{
    counter.increment();
    uint16_t *allocated = new (std::nothrow) uint16_t(counter.value);
    result = allocated ? *allocated : 0u;
    delete allocated;
    for (;;) { }
}
