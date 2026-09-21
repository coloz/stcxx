#include "cpp/stcxx_runtime.h"

void __stcxx_heap_init(void);
int __stcxx_user_main(void);

int main(void)
{
    /* SDCC initializes static storage and SPX before entering this function. */
    __stcxx_heap_init();
    __stcxx_run_global_ctors();
    (void)__stcxx_user_main();
    for (;;) { }
}
