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
using FunctionReference = int (&)(int);
struct FunctionReferenceHolder {
  FunctionReference reference;
  unsigned char tag;
};
static_assert(sizeof(void *) == 3 && alignof(void *) == 1,
              "generic data pointer layout drift");
static_assert(sizeof(FunctionPointer) == 2 && alignof(FunctionPointer) == 1,
              "function pointer layout drift");
static_assert(sizeof(FunctionReferenceHolder) == 3 &&
                  alignof(FunctionReferenceHolder) == 1,
              "function reference storage layout drift");

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

struct LeftOffsetBase {
  int left;
};

struct RightOffsetBase {
  int right;
  int right_method() const;
};

struct MultipleDerived : LeftOffsetBase, RightOffsetBase {
  int tail;
};

struct VRoot {
  int root;
  virtual ~VRoot();
  virtual int value() const;
};

struct VLeft : virtual VRoot {
  int left;
};

struct VRight : virtual VRoot {
  int right;
};

struct VDiamond : VLeft, VRight {
  int own;
  int value() const override;
};

using RightDataMember = int RightOffsetBase::*;
using DerivedDataMember = int MultipleDerived::*;
using RightMethodMember = int (RightOffsetBase::*)() const;
using DerivedMethodMember = int (MultipleDerived::*)() const;

static_assert(sizeof(LeftOffsetBase) == 2 && sizeof(RightOffsetBase) == 2,
              "multiple-inheritance base layout drift");

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
int RightOffsetBase::right_method() const { return right; }
VRoot::~VRoot() {}
int VRoot::value() const { return root; }
int VDiamond::value() const { return root + left + right + own; }

DataMember observed_data_member = &Base::bias;
MethodMember observed_plain_member = &Base::plain;
MethodMember observed_virtual_member = &Base::eval;
DerivedDataMember observed_right_data_as_derived =
    static_cast<DerivedDataMember>(&RightOffsetBase::right);
DerivedMethodMember observed_right_method_as_derived =
    static_cast<DerivedMethodMember>(&RightOffsetBase::right_method);

static int free_add_one(int value) { return value + 1; }

extern "C" FunctionPointer bridge_function_pointer(FunctionPointer pointer) {
  return pointer;
}

extern "C" int bridge_indirect(FunctionPointer pointer, int value) {
  return pointer(value);
}

extern "C" int bridge_function_reference(FunctionReference function,
                                           int value) {
  return function(value);
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

extern "C" DerivedDataMember
bridge_data_member_base_to_derived(RightDataMember member) {
  return static_cast<DerivedDataMember>(member);
}

extern "C" RightDataMember
bridge_data_member_derived_to_base(DerivedDataMember member) {
  return static_cast<RightDataMember>(member);
}

extern "C" DerivedMethodMember
bridge_method_member_base_to_derived(RightMethodMember member) {
  return static_cast<DerivedMethodMember>(member);
}

extern "C" RightMethodMember
bridge_method_member_derived_to_base(DerivedMethodMember member) {
  return static_cast<RightMethodMember>(member);
}

extern "C" VRoot *bridge_virtual_base_cast(VDiamond *object) {
  return object;
}

extern "C" void *bridge_offset_to_top(VRight *object) {
  return dynamic_cast<void *>(object);
}

extern "C" int bridge_virtual_root_call(const VRoot *object) {
  return object->value();
}

extern "C" void bridge_virtual_global_delete(VRoot *object) {
  ::delete object;
}

extern "C" __PTRDIFF_TYPE__ bridge_pointer_difference(const char *end,
                                                         const char *begin) {
  return end - begin;
}

extern "C" __PTRDIFF_TYPE__
bridge_scaled_pointer_difference(const long *end, const long *begin) {
  return end - begin;
}

extern "C" void bridge_pointer_difference_store(__PTRDIFF_TYPE__ *output,
                                                   const char *end,
                                                   const char *begin) {
  *output = end - begin;
}

extern "C" bool bridge_pointer_difference_is_four(const char *base) {
  return &base[6] - &base[2] == 4;
}

extern "C" __PTRDIFF_TYPE__
bridge_pointer_difference_negative(const char *base) {
  return &base[2] - &base[6];
}

extern "C" __PTRDIFF_TYPE__
bridge_pointer_difference_consume(__PTRDIFF_TYPE__ value);

extern "C" __PTRDIFF_TYPE__
bridge_pointer_difference_argument(const char *end, const char *begin) {
  return bridge_pointer_difference_consume(end - begin);
}

typedef __SIZE_TYPE__ ProbeSize;
void *operator new[](ProbeSize);
void operator delete[](void *) noexcept;

struct ArrayCookieProbe {
  int value;
  ~ArrayCookieProbe();
};

extern "C" ArrayCookieProbe *bridge_array_new(ProbeSize count) {
  return new ArrayCookieProbe[count];
}

extern "C" void bridge_array_delete(ArrayCookieProbe *array) {
  delete[] array;
}
