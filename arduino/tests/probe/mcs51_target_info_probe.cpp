#ifndef __STC_MCS51__
#error "the synthetic STC MCS51 frontend profile is not active"
#endif

#ifndef __STC_CLANG_IR_ONLY__
#error "the IR-only safety marker is missing"
#endif

#ifndef __CHAR_UNSIGNED__
#error "plain char must be unsigned for the STC ABI"
#endif

#ifdef __STC_MCS251__
#error "the MCS51 profile must not expose the MCS251 identity"
#endif

#ifdef __MSP430__
#error "the synthetic STC profile must not impersonate MSP430 source"
#endif

static_assert(__CHAR_BIT__ == 8, "CHAR_BIT drift");
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__, "endianness drift");
static_assert((char)-1 > 0, "plain char is not unsigned");

static_assert(sizeof(bool) == 1 && alignof(bool) == 1, "bool layout drift");
static_assert(sizeof(char) == 1 && alignof(char) == 1, "char layout drift");
static_assert(sizeof(short) == 2 && alignof(short) == 1,
              "short layout drift");
static_assert(sizeof(int) == 2 && alignof(int) == 1, "int layout drift");
static_assert(sizeof(long) == 4 && alignof(long) == 1, "long layout drift");
static_assert(sizeof(long long) == 8 && alignof(long long) == 1,
              "long long layout drift");
static_assert(sizeof(__SIZE_TYPE__) == 2 && alignof(__SIZE_TYPE__) == 1,
              "size_t model drift");
static_assert(sizeof(__PTRDIFF_TYPE__) == 4 &&
                  alignof(__PTRDIFF_TYPE__) == 1,
              "ptrdiff_t model drift");

using FunctionPointer = int (*)(int);
static_assert(sizeof(void *) == 3 && alignof(void *) == 1,
              "generic data pointer layout drift");
static_assert(sizeof(FunctionPointer) == 2 && alignof(FunctionPointer) == 1,
              "function pointer layout drift");

static_assert(sizeof(float) == 4 && alignof(float) == 1,
              "float layout drift");
static_assert(sizeof(double) == 4 && alignof(double) == 1,
              "double layout drift");
static_assert(sizeof(long double) == 4 && alignof(long double) == 1,
              "long double layout drift");

struct ScalarRecord {
  char tag;
  int value;
  long wide;
  void *pointer;
};

static_assert(alignof(ScalarRecord) == 1, "aggregate alignment drift");
static_assert(sizeof(ScalarRecord) == 10, "aggregate padding drift");

struct Base {
  int bias;
  virtual int eval(int value) const;
  int plain(int value) const;
};

struct Derived final : Base {
  int extra;
  int eval(int value) const override;
};

using DataMember = int Base::*;
using MethodMember = int (Base::*)(int) const;

// MCS51 member pointers use the 16-bit program-pointer width.  Data pointers
// remain 24-bit generic pointers and ptrdiff_t remains 32-bit.
static_assert(sizeof(DataMember) == 2 && alignof(DataMember) == 1,
              "MCS51 data-member pointer layout drift");
static_assert(sizeof(MethodMember) == 4 && alignof(MethodMember) == 1,
              "MCS51 method-member pointer layout drift");

int Base::eval(int value) const { return value + bias; }
int Base::plain(int value) const { return value - bias; }
int Derived::eval(int value) const { return value + bias + extra; }

DataMember observed_data_member = &Base::bias;
MethodMember observed_plain_member = &Base::plain;
MethodMember observed_virtual_member = &Base::eval;

static int free_add_one(int value) { return value + 1; }

extern "C" FunctionPointer bridge_function_pointer(FunctionPointer pointer) {
  return pointer;
}

extern "C" int bridge_indirect(FunctionPointer pointer, int value) {
  return pointer(value);
}

extern "C" int bridge_virtual(const Base *base, int value) {
  return base->eval(value);
}

extern "C" DataMember bridge_data_member() { return &Base::bias; }

extern "C" MethodMember bridge_plain_member() { return &Base::plain; }

extern "C" MethodMember bridge_virtual_member() { return &Base::eval; }

extern "C" int bridge_data_member_apply(const Base *base, DataMember member) {
  return base->*member;
}

extern "C" int bridge_method_member_apply(const Base *base,
                                            MethodMember member, int value) {
  return (base->*member)(value);
}

extern "C" int bridge_function_pointer_call(FunctionPointer pointer,
                                              int value) {
  return pointer(value) + free_add_one(value);
}
