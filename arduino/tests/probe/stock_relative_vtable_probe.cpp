#if defined(__STC_MCS51__) || defined(__STC_MCS251__) || \
    defined(__STC_CLANG_IR_ONLY__)
#error "ordinary x86_64 must not inherit an STC IR-only profile"
#endif

struct RelativeBase {
  virtual ~RelativeBase();
  virtual int value() const;
};

struct RelativeDerived final : RelativeBase {
  ~RelativeDerived() override;
  int value() const override;
};

RelativeBase::~RelativeBase() {}
int RelativeBase::value() const { return 1; }
RelativeDerived::~RelativeDerived() {}
int RelativeDerived::value() const { return 2; }

extern "C" int relative_virtual_call(const RelativeBase *object) {
  return object->value();
}

extern "C" void relative_global_delete(RelativeBase *object) {
  ::delete object;
}
