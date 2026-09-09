/* Keep a large relocatable addend intact when an address is rematerialized.
   This is valid array pointer arithmetic rather than a synthetic pointer
   outside the declared object. */
static __xdata unsigned char remat_storage[70016UL];

unsigned char __xdata *
remat_positive_large (void)
{
    return &remat_storage[70000UL];
}
