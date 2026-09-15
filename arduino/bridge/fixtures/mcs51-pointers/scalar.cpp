typedef unsigned char (*Callback)(unsigned char);
static_assert(sizeof(Callback) == 2 && sizeof(void*) == 3, "MCS51 code/data pointer ABI");
extern "C" {
void record(unsigned char);
unsigned char native_call(Callback, unsigned char);
Callback native_factory(unsigned char);
unsigned char native_roundtrip(Callback, unsigned char);
}
__attribute__((noinline)) static unsigned char plus3(unsigned char x) { return x + 3; }
__attribute__((noinline)) static unsigned char plus5(unsigned char x) { return x + 5; }
__attribute__((noinline)) static Callback choose(unsigned char x) { return x ? plus3 : plus5; }
static Callback volatile saved = plus3;
struct Holder { Callback function; unsigned char marker; };
static Holder holder = {plus5, 11};
extern "C" void setup() {
    Callback first = saved;
    record(first(4) == 7);
    record(choose(1)(7) == 10 && choose(0)(7) == 12);
    record(native_call(first, 9) == 12);
    record(native_roundtrip(plus5, 13) == 18);
    Callback incoming = native_factory(1);
    record(incoming(6) == 15);
    incoming = native_factory(0);
    record(incoming(8) == 19);
    record(holder.function(10) == 15 && holder.marker == 11);
    saved = incoming;
    record(saved(1) == 12);
}
extern "C" void loop() {}
extern "C" void __stcxx_run_global_ctors() {}
extern "C" void ABI_SYMBOL() {}
extern "C" void stcxx_runtime_panic(unsigned short) { record(0); for (;;) {} }
