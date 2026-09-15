#include <stdint.h>
__sfr __at(0x99) SBUF;
typedef unsigned char (*Callback)(unsigned char);
static unsigned char checks, failed;
void record(unsigned char good) { ++checks; if (!good) ++failed; }
static unsigned char plus9(unsigned char x) { return x + 9; }
static unsigned char plus11(unsigned char x) { return x + 11; }
unsigned char native_call(Callback cb, unsigned char value) { return cb(value); }
Callback native_factory(unsigned char which) { return which ? plus9 : plus11; }
unsigned char native_roundtrip(Callback cb, unsigned char value) { Callback copy = cb; return copy(value); }
void setup(void); void __stcxx_run_global_ctors(void);
void main(void) {
    const char* text;
    __stcxx_run_global_ctors(); setup();
    text = (!failed && checks == 8) ? "PASS mcs51 " PROBE_NAME " pointers\n" : "FAIL mcs51 " PROBE_NAME " pointers\n";
    while (*text) SBUF = *text++;
    for (;;) {}
}
