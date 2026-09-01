/*
 * Minimal MCS251 big-endian regression for narrowing a 32-bit array index to
 * the target's 24-bit flat address.  This is deliberately independent of
 * Arduino and LLVM-CBE: the spelling below is the reduced form emitted by the
 * bridge for an i32 getelementptr index.
 *
 * For length == 1 the store must target buffer[1].  A broken MCS251 backend
 * loads the first three bytes of the big-endian uint32_t (00 00 00) instead
 * of the low three bytes (00 00 01), and therefore targets buffer[0].
 */

typedef unsigned char uint8_t;
typedef unsigned int uint16_t;
typedef unsigned long uint32_t;
typedef signed long int32_t;

struct mcs251_u32_index_probe {
    uint8_t buffer[48];
    uint32_t length;
};

void
mcs251_store_at_u32_low24(struct mcs251_u32_index_probe *probe,
                         uint8_t value)
{
    probe->buffer[(int32_t)(((uint32_t)probe->length) & 16777215UL)] = value;
}

struct mcs251_u16_narrow_probe {
    uint16_t value;
};

uint8_t
mcs251_load_u16_low8(struct mcs251_u16_narrow_probe *probe)
{
    return (uint8_t)probe->value;
}
