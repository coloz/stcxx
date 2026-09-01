extern "C" int stock_msp430_probe(int value, void *pointer) {
  return value + (pointer != nullptr);
}
