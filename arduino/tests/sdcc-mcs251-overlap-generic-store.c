/*
 * Minimal MCS251 regression for a destructive register-tuple overlap in
 * multi-byte addition.  The C++ bridge uses this shape for String::replace:
 * obtain the length field at object + 5, then form buffer + index and store a
 * byte through the resulting generic pointer.
 *
 * A broken allocation can put the object pointer in logical bytes
 * [r7, r6, r5] and object + 5 in [r5, r4, r3].  Writing the result's low byte
 * to r5 before reading the source's high byte changes the address.  The
 * backend must defer destination writes (or otherwise preserve the source)
 * until every source byte has been consumed.
 */

typedef unsigned char uint8_t;
typedef unsigned int uint16_t;

struct mcs251_string_shape {
    void *buffer;
    uint16_t capacity;
    uint16_t length;
};

void
mcs251_replace_byte(void *object, uint8_t find, uint8_t replacement)
{
    void *object_storage;       /* Address-exposed local. */
    uint8_t find_storage;       /* Address-exposed local. */
    uint8_t replacement_storage;/* Address-exposed local. */
    uint16_t index_storage;     /* Address-exposed local. */
    void *live_object;
    uint16_t index_for_compare;
    uint16_t length;
    void *buffer_for_load;
    uint16_t index_for_load;
    uint8_t loaded;
    uint8_t wanted;
    uint8_t value;
    void *buffer_for_store;
    uint16_t index_for_store;
    uint16_t index_for_increment;

    object_storage = object;
    find_storage = find;
    replacement_storage = replacement;
    live_object = object_storage;
    index_storage = 0;
    goto loop_header;

    do {
loop_header:
        index_for_compare = index_storage;
        length = *(uint16_t *)&
            (((struct mcs251_string_shape *)live_object)->length);
        if (index_for_compare < length)
            goto loop_body;
        else
            goto loop_done;

loop_body:
        buffer_for_load = *(void **)&
            (((struct mcs251_string_shape *)live_object)->buffer);
        index_for_load = index_storage;
        loaded = ((uint8_t *)buffer_for_load)
            [(long)(((unsigned long)(uint16_t)index_for_load) & 16777215UL)];
        wanted = find_storage;
        if (loaded == wanted)
            goto replace;
        else
            goto increment;

replace:
        value = replacement_storage;
        buffer_for_store = *(void **)&
            (((struct mcs251_string_shape *)live_object)->buffer);
        index_for_store = index_storage;
        ((uint8_t *)buffer_for_store)
            [(long)(((unsigned long)(uint16_t)index_for_store) & 16777215UL)] =
                value;
        goto increment;

increment:
        index_for_increment = index_storage;
        index_storage = index_for_increment + 1;
        goto loop_header;
    } while (1);

loop_done:
    return;
}
