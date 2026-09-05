#ifndef __MSP430__
#error "the ordinary MSP430 target identity is missing"
#endif

#if defined(__STC_MCS51__) || defined(__STC_MCS251__) || \
    defined(__STC_CLANG_IR_ONLY__)
#error "ordinary MSP430 must not inherit an STC IR-only profile"
#endif

static_assert(sizeof(void *) == 2, "ordinary MSP430 pointer width drift");
static_assert(sizeof(__SIZE_TYPE__) == 2, "ordinary MSP430 size_t drift");
static_assert(sizeof(__PTRDIFF_TYPE__) == 2,
              "ordinary MSP430 ptrdiff_t drift");

struct StockVirtual {
  int payload;
  virtual ~StockVirtual();
  virtual int value() const;
};

struct StockDerived final : StockVirtual {
  int value() const override;
};

StockVirtual::~StockVirtual() {}
int StockVirtual::value() const { return payload; }
int StockDerived::value() const { return payload + 1; }

extern "C" int stock_msp430_probe(int value, void *pointer) {
  return value + (pointer != nullptr);
}

extern "C" int stock_msp430_virtual(const StockVirtual *object) {
  return object->value();
}

extern "C" __PTRDIFF_TYPE__
stock_msp430_pointer_difference(const char *end, const char *begin) {
  return end - begin;
}
