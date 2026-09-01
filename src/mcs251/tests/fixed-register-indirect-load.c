typedef unsigned long fixed_indirect_fn (void *, unsigned char);

extern void fixed_indirect_error (void *, unsigned int);

static unsigned long
fixed_indirect_add (unsigned long left, unsigned long right)
{
  return left + right;
}

unsigned long
mcs251_fixed_register_indirect_load (void *arg_object, void *arg_buffer,
                                     unsigned long arg_size)
{
  unsigned long result_storage;
  void *object_storage;
  void *buffer_storage;
  unsigned long size_storage;
  unsigned long index_storage;
  void *object;
  void *null_check;
  unsigned long index_for_compare;
  unsigned long size_for_compare;
  void *buffer;
  unsigned long index;
  unsigned char byte;
  void *vtable;
  void *write_slot;
  unsigned long written;
  unsigned long index_for_increment;
  unsigned long result;
  unsigned long returned;

  object_storage = arg_object;
  buffer_storage = arg_buffer;
  size_storage = arg_size;
  object = object_storage;
  index_storage = 0;
  null_check = buffer_storage;
  if (null_check == (void *)0)
    goto null_buffer;
  else
    goto loop_header;

null_buffer:
  result_storage = 0;
  goto return_result;

  do
    {
loop_header:
      index_for_compare = index_storage;
      size_for_compare = size_storage;
      if (index_for_compare < size_for_compare)
        goto loop_body;
      else
        goto loop_done;

loop_body:
      buffer = buffer_storage;
      index = index_storage;
      byte = *(unsigned char *)&((unsigned char *)buffer)
        [(long)(index & 0x00fffffful)];
      vtable = *(void **)object;
      write_slot = *(void **)vtable;
      written = ((fixed_indirect_fn *)(void *)write_slot)(object, byte);
      if (written == 0)
        goto write_failed;
      else
        goto write_succeeded;

write_succeeded:
      index_for_increment = index_storage;
      index_storage = fixed_indirect_add (index_for_increment, 1);
      goto loop_header;
    }
  while (1);

write_failed:
  fixed_indirect_error (object, 1);
  goto loop_done;

loop_done:
  result = index_storage;
  result_storage = result;
  goto return_result;

return_result:
  returned = result_storage;
  return returned;
}
