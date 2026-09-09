# MCS251 assembler and linker regression tests

The encoding matrix, fixtures, and shell checks in this directory are restored
from `gevico/sdcc-c251` commit `b09075b6`. They cover 269 legal operand forms,
65 instruction families, both source and binary opcode maps, assembler errors,
and linked 24-bit addresses. They are target-specific tests, not SDCC's complete
generic regression suite.

From a configured build directory, run:

```sh
make -C sdas/as251 check
```

`check-audit.py` adds whole-Flash and native-relocation regressions. It accepts
explicit assembler and **sdldmcs251** paths, so old and candidate SDKs can be
compared without installing or replacing any published binaries:

```sh
python3 sdas/as251/tests/check-audit.py /path/to/sdas251 /path/to/sdldmcs251
```

Add `--work-dir /new/diagnostic/directory` to retain sources, object records,
linked listings, maps, and Intel HEX files. Successful cases check expected
machine bytes and verify that every emitted byte belongs to the non-overlapping
native allocation ledger. Negative cases check the expected diagnostic.

The additional checks cover:

- Full 24-bit addends for LCALL/ACALL, including targets past offset 0xFFFF.
- Page/region validation using the caller's final linked address, rather than
  the unrelocated assembler offset, and safe generic CALL relaxation.
- Native indexed MOV symbol relocation, signed addends, and displacement
  overflow/underflow; direct-page symbol overflow.
- Matching area attributes across translation units and ABS+OVR `.org`
  fragments at high Flash addresses.
- Startup fallthrough, explicit startup anchors, and whole-area packing around
  HOME for both 128 KiB and 246 KiB physical windows. An unfixed startup group
  can move above HOME when a large contiguous section needs the lower window.
- Multiple translation units, separate absolute islands, and a payload byte
  at the inclusive last physical address, 0xFFFFFF.
- Repeated contributions to one function section, including odd/even sizes:
  each nonempty contribution retains two-byte alignment, and internal padding
  is included in area bounds and size symbols without aligning other areas.

The candidate assembler uses MCS251-specific relocation qualifiers to retain
24-bit addends while still emitting the original two-byte address fields. The
candidate linker continues accepting older 16-bit-addend control records;
however, already-truncated old object addends cannot be reconstructed. Rebuild
objects affected by these bugs and use assembler and native linker from the
same candidate toolchain. Do not mix new object records with an older linker.
