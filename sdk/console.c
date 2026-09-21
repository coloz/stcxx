#include <stdio.h>

/* Override these hooks in a native C translation unit to connect a UART.
 * Kept in a separate archive member so an application's definitions win. */
int stcxx_console_write(unsigned char value)
{
    (void)value;
    return EOF;
}

int stcxx_console_read(void)
{
    return EOF;
}
