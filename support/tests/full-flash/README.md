# MCS251 full-Flash regression

`run-full-flash.py` is a self-contained compile/link test for the opt-in
MCS251 per-function/per-constant areas and the bounded scatter allocator.
It builds two synthetic images:

- STC32G12K128: `0xFE0000:0x1000000`, with 120,000 payload bytes;
- STC32G144K246: `0xFC2800:0x1000000`, with 241,000 payload bytes.

Both images retain HOME at `0xFF0000` and GSINIT0 at the Flash floor.  The
runner discovers hashed area names from compiler assembly, fixes direct-call
targets on both sides of HOME, and verifies the linker's untruncated Code
Window ledger, the ordinary map, and every Intel HEX byte.  An absolute,
volatile code sentinel at `0xFFFFFF` verifies the inclusive physical top byte,
the exclusive `0x1000000` window end, and CABS-fragment accounting.  It also checks
24-bit direct and indirect calls, a relocated function-pointer table,
runtime-indexed first/last const reads, XINIT/XSEG startup, and K246 switch
tables that straddle the `0xFD0000` and `0xFE0000` boundaries.

Negative tests cover the old code-size limit, overlap with HOME, a fixed area
outside Flash, an off-by-one exclusive window end, and a section that cannot
fit any contiguous free interval.
Compiling without the new options must still emit the traditional `CSEG` and
`CONST` areas.

Run the compile/link checks from the source tree:

```sh
python3 support/tests/full-flash/run-full-flash.py \
  --sdcc sdcc-build/bin/sdcc \
  --work-dir sdcc-build/full-flash-regression
```

Add the pinned MCS251 QEMU to execute both images:

```sh
python3 support/tests/full-flash/run-full-flash.py \
  --sdcc sdcc-build/bin/sdcc \
  --qemu /path/to/qemu-system-mcs251 \
  --work-dir sdcc-build/full-flash-regression
```

The runner writes `full-flash-report.json` under the work directory.  QEMU is
optional so ordinary compiler CI can enforce all static and negative checks
without downloading the emulator.
