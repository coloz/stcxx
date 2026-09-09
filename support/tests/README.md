# MCS251 toolchain regressions

`check-mcs251-near-store.py --sdcc <sdcc>` compiles and assembles DATA and
IDATA indirect stores using the actual compiler and assembler.

`check-mcs251-dr-immediate.py --assembler <sdas251> --linker <sdldmcs251>`
assembles and links `MOV SPX,#__start__stack - 1` with the linker definition
`__start__stack=0x0100`, then checks the resulting HEX bytes. The required
encoding is `7E F8 00 FF` (SPX = `000000FF`). Before the fix, the unresolved
addend `-1` selected `7E FC 00 FF` (SPX = `FFFF00FF`). The optional
`--baseline <old-sdas251>` verifies that the old tool reproduces this defect.
The regression also covers positive and negative absolute immediate boundaries
and rejects absolute values whose high word cannot be encoded by this MOV.

Relocatable DR immediates use the zero-filled high-word encoding and must
resolve to `0..0xFFFF`. This repair does not add linker range validation:
the existing word relocation still truncates an out-of-range final value.
Resolving negative or wider constants through external symbols remains
unsupported; use a known absolute value or explicitly load the high word.

Both checks run in the `mcs51-family` CI workflow. They test generated machine
code and do not establish the cause of any separate hardware symptom.
