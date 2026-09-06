# STCXX C/C++ toolchain architecture

STCXX is the source project for the experimental STC 8051/251 freestanding
C/C++ toolchain. Installation and usage are described in [README.md](README.md).
The checkout is named `stcxx`; the underlying executables retain their names.

```text
C++ -> patched Clang -> LLVM IR -> LLVM-CBE -> audited C adapter -> SDCC
    -> ASxxxx assembler/linker -> Intel HEX firmware
```

Plain C goes directly through SDCC. Arduino supplies board configuration,
startup code, the runtime and the final chip-specific linker layout.

## Sources and identity

- `toolchain/llvm-project/clang`: Clang 20.1.8 source. Its STC IR target patch
  is `arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch`.
- `toolchain/llvm-cbe`: LLVM-CBE at commit `83f1bea66c7415c701925470a2f7596b37153197`.
  Its STC address-space and C lowering changes are in
  `arduino/patches/llvm-cbe-83f1bea-stc-sdcc.patch`.
- The repository root contains SDCC based on `gevico/sdcc-c251` commit
  `b09075b6a93e6afe10645181e3aeff041ea37f87`. The combined production patch is
  `arduino/patches/sdcc-mcs251-arduino-cpp.patch`.
- `arduino/bridge` holds the IR audit and LLVM-CBE-to-SDCC C adapter.
- `arduino/toolchain-lock.json` records source, patch, ABI and reference-tool
  identities. `arduino/sources` retains the locked Clang/CMake source archives.

The source checker verifies production inputs and patch application. Project
regression fixtures are not part of the source build contract. Source hashes,
package inventory and manifest verification do not establish runtime or
hardware qualification.

## Build and package layout

The current full build scripts run in Linux/WSL and require LLVM 20 development
files. Use a new absolute build directory:

```sh
bash arduino/scripts/build-wsl.sh /var/tmp/stcxx-build all
```

The stages `clang`, `llvm-cbe`, `sdcc` and `all` are accepted. The script
prepares and checks source state itself. To inspect source preparation
separately, run `prepare-sources-wsl.sh` before `check-sources-wsl.sh`.

The source preparation step normalizes line endings only in the explicitly
listed patch-target files, verifies locked commits and patch hashes, and
applies the Clang/LLVM-CBE patches using ordinary Git checks.

Publish the SDCC build into a new output directory:

```sh
bash arduino/scripts/publish-sdcc-out-wsl.sh /var/tmp/stcxx-build/sdcc /path/to/new-out
bash arduino/scripts/check-out-wsl.sh /path/to/new-out
```

`check-out-wsl.sh` checks tool startup, required executables, runtime-library
directories and `MANIFEST.sha256`. It does not compile or execute firmware.
The convenience `bootstrap-sdcc-out-wsl.sh` builds SDCC, publishes it and runs
these package integrity checks.

The default package entry points are `out/bin/sdcc` and
`out/bin/sdldmcs251`. The package includes the real compiler, the preprocessor
and its `cc1`, assemblers, linkers, headers and both target runtime libraries.
Keep the complete package. Clang and LLVM-CBE remain in their separate build
directories and are selected through the Arduino integration's tool paths.

## Target ABI profiles

The patched Clang STC profiles emit LLVM IR for the locked adapter pipeline.
They are not native LLVM machine backends; final target code comes from SDCC.

| Property | MCS-51 | MCS-251 |
| --- | --- | --- |
| Triple | `msp430-stc51-none-eabi` | `msp430-stc-none-eabi` |
| Byte order | Little-endian | Big-endian |
| `int` | 16 bits | 16 bits |
| `size_t` | 16 bits | 32 bits |
| `ptrdiff_t` | 32 bits | 32 bits |
| Generic data pointer | Tagged 24-bit pointer | Flat 24-bit pointer |
| Function pointer | 16 bits, program address space 1 | 24 bits |
| Data-member pointer | 2 bytes | 3 bytes |
| Member-function pointer | 4 bytes | 6 bytes |

Each profile has a distinct runtime ABI identity symbol in the lock file.
Objects, libraries and runtime code must match the selected profile. The
MCS-251 linker mode `sdldmcs251` also supports the project's 16-bit SPX/SSEG
stack layout; the legacy MCS-51 layout retains its own IRAM constraints.

## C++ boundary

The Arduino integration selects `gnu++11`, disables exceptions, RTTI,
thread-safe statics and static destructors, and provides its embedded runtime.
Function-local static initialization uses a non-threadsafe direct guard.
TLS and a hosted `libstdc++` are not supplied.

The adapter checks target layout, ABI anchors, constructors and supported IR
before accepting the generated C. Complex aggregate/bitfield operations,
varargs and weak/COMDAT edge cases remain subject to these checks; accepting
C++ syntax in Clang alone does not establish a working firmware ABI.

Chip-specific Flash addresses, stack/heap limits and startup behavior belong
to [arduino-stc51](../arduino-stc51/README.md). The existing C++ board profiles
are experimental. Firmware validation and UART ISP are supplied separately
by [stc-cli](../stc-cli/README.md).
