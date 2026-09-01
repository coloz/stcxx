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
  STC address-space, 24-bit integer and aggregate lowering fixes are retained
  in `arduino/patches/llvm-cbe-83f1bea-stc-sdcc.patch`.
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
bash arduino/scripts/check-sources-wsl.sh
bash arduino/scripts/build-wsl.sh /var/tmp/sdcc-c251-arduino-build all
bash arduino/scripts/self-test-wsl.sh /var/tmp/sdcc-c251-arduino-build
```

`check-sources-wsl.sh` validates the patched source state, so it is not the
first command for a clean checkout. Run `prepare-sources-wsl.sh` first; it
canonicalizes only the nine patch-target files to LF, verifies the locked
source commits and patch hashes, and applies both patches with ordinary
`git apply` checks. The source checker then verifies the normalized SHA-256 of
all eight changed Clang files and the changed LLVM-CBE file. `build-wsl.sh`
also invokes prepare and check itself, so the explicit first two commands are
useful as a quick source-only preflight rather than an additional requirement.

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

## C++ target profiles and qualification boundary

Both Clang STC frontend profiles are implemented and experimentally qualified
through the Arduino compile/link pipeline. They emit locked LLVM IR for the
audited LLVM-CBE and adapter path; neither profile is a native LLVM machine
backend, and Clang assembly/object output remains deliberately fail-closed.

- MCS51 uses `msp430-stc51-none-eabi` with the little-endian layout recorded in
  `arduino/toolchain-lock.json`: 16-bit `size_t`, 32-bit `ptrdiff_t`, a tagged
  24-bit generic data pointer, and a 16-bit code/function pointer. Ordinary
  function pointers, data-member pointers, and member-function pointers occupy
  2, 2, and 4 bytes respectively. Indirect member calls are emitted in program
  address space 1 and are audited before C lowering.
- MCS251 uses `msp430-stc-none-eabi` with the locked big-endian 24-bit pointer
  layout: 32-bit `size_t`/`ptrdiff_t` and three-byte generic, code and ordinary
  function pointers. Data-member and member-function pointers occupy 3 and 6
  bytes respectively; their CodeGen integer components are explicitly i24.

The regression suite checks both triples and layouts, ordinary function
pointers, data-member and nonvirtual/virtual member-function pointer forms,
null comparisons, MCS51 program-address-space calls, and the 2/2/4 versus
3/3/6 AST/IR sizes. LLVM-CBE regressions separately lock STC-only
`_BitInt(24)` lowering, unchanged generic i24 behavior, aggregate pointer casts,
and const-correct zero-sized globals. Final SDCC compile/link, capacity and
runtime qualification belongs to the Arduino Core evidence rather than raw
LLVM-CBE output.

Function-local statics use Clang's non-threadsafe one-byte direct guard under
the Core's `-fno-threadsafe-statics` policy. This is not a claim that
`__cxa_guard_*` provides thread-safe initialization. Exceptions, RTTI, TLS and
a hosted `libstdc++` are not supplied. Varargs, complex aggregate/bitfield and
weak/COMDAT edge cases remain fail-closed or unqualified. This project is an
experimental freestanding Arduino toolchain, not a general desktop C++
compiler.
