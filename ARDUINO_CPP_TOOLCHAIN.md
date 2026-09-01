# Arduino STC51 experimental C++ toolchain

This directory is the standalone source project for the experimental
Arduino-STC51 C++ compiler pipeline.  It is rooted in the upstream
`gevico/sdcc-c251` Git history and keeps every source stage, patch and lock in
one place:

```text
patched Clang 20.1.8 (C++ -> locked STC LLVM IR)
  -> LLVM-CBE 83f1bea (LLVM IR -> portable C)
  -> fail-closed Arduino adapter
  -> patched SDCC b09075b6 (C -> MCS51/MCS251 image)
```

The project source lives at `D:\Git\sdcc-c251-arduino` on Windows and at
`/mnt/d/Git/sdcc-c251-arduino` in WSL.

## Source layout

- `toolchain/llvm-project/clang`: official LLVM/Clang `llvmorg-20.1.8`
  source.  The STC TargetInfo patch is in
  `arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch`.
- `toolchain/llvm-cbe`: `JuliaHubOSS/llvm-cbe` at the locked commit.
- the repository root: `gevico/sdcc-c251` at the locked base commit, with
  `arduino/patches/sdcc-mcs251-arduino-cpp.patch` applied.  This combined
  patch includes ISR context preservation and the MCS251 16-bit SPX/SSEG
  linker extension.
- `arduino/bridge`: the audited LLVM-CBE-to-SDCC source adapter used by the
  Arduino core integration.
- `arduino/toolchain-lock.json`: exact source, patch, ABI and reference-binary
  identities.

The official Clang/CMake release archives are retained in `arduino/sources`.
They are hash-locked and allow a clean build without trusting an old compiler
cache.  The expanded LLVM Git worktree exists for source inspection and source
provenance.

## Prepare, build and test in WSL

From the project root:

```sh
bash arduino/scripts/prepare-sources-wsl.sh
bash arduino/scripts/build-wsl.sh /var/tmp/sdcc-c251-arduino-build all
bash arduino/scripts/self-test-wsl.sh /var/tmp/sdcc-c251-arduino-build
```

The build script refuses an existing build directory.  Valid stages are
`clang`, `llvm-cbe`, `sdcc`, and `all`.  A standalone SDCC-only build is:

```sh
bash arduino/scripts/build-wsl.sh /var/tmp/sdcc-c251-arduino-sdcc sdcc
```

Its entry point is
`/var/tmp/sdcc-c251-arduino-sdcc/sdcc/bin/sdcc`; the real ELF is
`/var/tmp/sdcc-c251-arduino-sdcc/sdcc/src/sdcc`.

For the stable project-local entry used by Arduino, build, publish, and test
in one command (both targets must not already exist):

```sh
bash arduino/scripts/bootstrap-sdcc-out-wsl.sh \
  /var/tmp/sdcc-c251-arduino-clean-build \
  /mnt/d/Git/sdcc-c251-arduino/out
```

The fixed entry points are:

- `/mnt/d/Git/sdcc-c251-arduino/out/bin/sdcc`
- `/mnt/d/Git/sdcc-c251-arduino/out/bin/sdldmcs251`

`out` is a generated, relocatable bundle containing the real compiler,
preprocessor/`cc1`, assemblers, both linkers, headers and all MCS51/MCS251
runtime-library models.  It does not depend on the temporary build directory.
Run `bash arduino/scripts/check-out-wsl.sh` to verify its manifest and strict
stack regression.

## MCS251 extended-stack qualification

`sdldmcs251` is a distinct linker mode; legacy MCS51 continues to invoke
`sdld` and retains its 256-byte IRAM limit.  The regression checks exact
`SSEG`, `s_SSEG`, `l_SSEG`, `__start__stack`, `l_IRAM` and `.mem` SPX output
for 0x0800, 0x1000 and 0x4000 EDATA configurations.  It also proves an
out-of-range SSEG is rejected and that MCS51 does not inherit the extension.

## Qualification boundary

The patched SDCC backend builds both `-mmcs51` and `-mmcs251`.  The current
Clang STC frontend profile, however, is only the experimental big-endian
MCS251 IR profile (`msp430-stc-none-eabi`).  It is not a native LLVM machine
backend and deliberately blocks native Clang assembly/object output.

An independent little-endian MCS51 C++ frontend ABI is still required before
the full Arduino variant set can honestly be called C++-qualified.  In
particular, MCS51 needs 16-bit `size_t`, 32-bit `ptrdiff_t`, tagged 24-bit
generic data pointers and 16-bit function pointers; the MCS251 layout must not
be reused.  `arduino/toolchain-lock.json` records this boundary explicitly.

Exceptions, RTTI, TLS and a hosted `libstdc++` are not supplied.  This project
is an experimental freestanding Arduino toolchain, not a general desktop C++
compiler.
