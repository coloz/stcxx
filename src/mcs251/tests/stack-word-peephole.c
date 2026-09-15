/* Stable assembly probes for the MCS251 hardware-stack word peepholes.
   The optimizer must compare the complete hexadecimal displacements. */

unsigned int
false_load_wr6 (void) __naked
{
    __asm
        mov     a,@spx-0x003f
        mov     r7,a
        mov     a,@spx-0x004f
        mov     r6,a
        xrl     a,#0x5a
        mov     dpl,r6
        mov     dph,r7
        eret
    __endasm;
}

unsigned int
false_load_wr4 (void) __naked
{
    __asm
        mov     a,@spx-0x0039
        mov     r5,a
        mov     a,@spx-0x0040
        mov     r4,a
        xrl     a,#0x5a
        mov     dpl,r4
        mov     dph,r5
        eret
    __endasm;
}

unsigned int
false_load_wr2 (void) __naked
{
    __asm
        mov     a,@spx-0x003f
        mov     r3,a
        mov     a,@spx-0x004f
        mov     r2,a
        xrl     a,#0x5a
        mov     dpl,r2
        mov     dph,r3
        eret
    __endasm;
}

unsigned int
false_load_wr0 (void) __naked
{
    __asm
        mov     a,@spx-0x0039
        mov     r1,a
        mov     a,@spx-0x0040
        mov     r0,a
        xrl     a,#0x5a
        mov     dpl,r0
        mov     dph,r1
        eret
    __endasm;
}

unsigned int
true_load_wr4 (void) __naked
{
    __asm
        mov     a,@spx-0x003f
        mov     r5,a
        mov     a,@spx-0x0040
        mov     r4,a
        xrl     a,#0x5a
        mov     dpl,r4
        mov     dph,r5
        eret
    __endasm;
}

void
false_store_wr6 (void) __naked
{
    __asm
        mov     a,r7
        mov     @spx-0x003f,a
        mov     a,r6
        mov     @spx-0x004f,a
        eret
    __endasm;
}

void
false_store_wr4 (void) __naked
{
    __asm
        mov     a,r5
        mov     @spx-0x0039,a
        mov     a,r4
        mov     @spx-0x0040,a
        eret
    __endasm;
}

void
false_store_wr2 (void) __naked
{
    __asm
        mov     a,r3
        mov     @spx-0x003f,a
        mov     a,r2
        mov     @spx-0x004f,a
        eret
    __endasm;
}

void
false_store_wr0 (void) __naked
{
    __asm
        mov     a,r1
        mov     @spx-0x0039,a
        mov     a,r0
        mov     @spx-0x0040,a
        eret
    __endasm;
}

void
true_store_wr4 (void) __naked
{
    __asm
        mov     a,r5
        mov     @spx-0x003f,a
        mov     a,r4
        mov     @spx-0x0040,a
        xrl     a,#0x5a
        mov     dpl,a
        eret
    __endasm;
}
