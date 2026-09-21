#ifndef STCXX_STANDALONE_ENTRY_H
#define STCXX_STANDALONE_ENTRY_H
/* The SDK startup calls the application's int main(void) after constructors. */
#ifdef __cplusplus
extern "C" int __stcxx_user_main(void);
#else
int __stcxx_user_main(void);
#endif
#define main __stcxx_user_main
#endif
