extern "C" void record(unsigned char);
struct Left {
    unsigned char left;
    __attribute__((noinline)) int addLeft(int value) { return left + value; }
};
struct Right {
    unsigned char right;
    __attribute__((noinline)) int addRight(int value) { return right + value; }
};
struct Both : Left, Right {
    unsigned char own;
    __attribute__((noinline)) int addOwn(int value) { return own + value; }
};
typedef int (Both::*Member)(int);
static_assert(sizeof(Member) == 4 && sizeof(unsigned char Both::*) == 2, "MCS51 member pointer widths");
__attribute__((noinline)) int callMember(Both* object, Member member, int value) {
    return (object->*member)(value);
}
struct Base {
    unsigned char base;
    __attribute__((noinline)) virtual int calculate(int value) { return base + value; }
};
struct Derived : Base {
    __attribute__((noinline)) int calculate(int value) override { return base + value + 40; }
};
typedef int (Base::*VirtualMember)(int);
__attribute__((noinline)) int callVirtual(Base* object, VirtualMember member, int value) {
    return (object->*member)(value);
}
__attribute__((noinline)) int callOrdinaryVirtual(Base* object, int value) { return object->calculate(value); }
static Member volatile selected = static_cast<Member>(&Right::addRight);
extern "C" void setup() {
    Both object; object.left = 11; object.right = 17; object.own = 23;
    unsigned char Both::*data = &Right::right;
    record(object.*data == 17);
    record(callMember(&object, &Left::addLeft, 5) == 16);
    record(callMember(&object, selected, 7) == 24);
    record(callMember(&object, &Both::addOwn, 9) == 32);
    Member empty = nullptr;
    record(empty == nullptr && selected != nullptr);
    Derived derived; derived.base = 13;
    record(callOrdinaryVirtual(&derived, 3) == 56);
    record(callVirtual(&derived, &Base::calculate, 5) == 58);
    Base plain; plain.base = 19;
    record(callVirtual(&plain, &Base::calculate, 7) == 26);
}
extern "C" void loop() {}
extern "C" void __stcxx_run_global_ctors() {}
extern "C" void ABI_SYMBOL() {}
extern "C" void stcxx_runtime_panic(unsigned short) { record(0); for (;;) {} }
