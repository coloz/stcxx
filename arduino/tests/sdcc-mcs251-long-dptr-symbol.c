typedef unsigned char uint8_t;

extern uint8_t stc_test_send_data(void *context, uint8_t count,
                                  uint8_t *buffer);

static uint8_t
u8x8_write_byte_to_16gr_device_xx(void *context, uint8_t value)
{
  static uint8_t buf[4];
  static uint8_t map[4] = {0, 0x0f, 0xf0, 0xff};

  buf[3] = map[value & 3];
  value >>= 2;
  buf[2] = map[value & 3];
  value >>= 2;
  buf[1] = map[value & 3];
  value >>= 2;
  buf[0] = map[value & 3];
  return stc_test_send_data(context, 4, buf);
}

uint8_t
stc_test_long_dptr_symbol(void *context, uint8_t value)
{
  return u8x8_write_byte_to_16gr_device_xx(context, value);
}
