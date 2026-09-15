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

- `toolchain/llvm-project/clang`: vendored Clang 20.1.8 source. Its STC IR target patch
  is `arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch`.
- `toolchain/llvm-cbe`: vendored LLVM-CBE from commit `83f1bea66c7415c701925470a2f7596b37153197`.
  Its STC address-space and C lowering changes are in
  `arduino/patches/llvm-cbe-83f1bea-stc-sdcc.patch`.
- The repository root contains SDCC based on `gevico/sdcc-c251` commit
  `b09075b6a93e6afe10645181e3aeff041ea37f87`. The combined production patch is
  `arduino/patches/sdcc-mcs251-arduino-cpp.patch`.
- `arduino/bridge` holds the IR audit and LLVM-CBE-to-SDCC C adapter.
- `arduino/toolchain-lock.json` records source, patch, ABI and reference-tool
  identities. `arduino/sources` retains the locked Clang/CMake source archives.
- Both frontend source directories are ordinary tracked files with STC patches
  already applied. LLVM includes `clang/`, `cmake/` and root files; LLVM-CBE is
  complete. `toolchain/source-manifest.json` records every imported file's
  exact SHA-256, Git mode and upstream provenance. No submodule checkout or
  upstream Git objects are needed. Licenses are retained in the source trees.

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

The source preparation step verifies the vendored inventory and exact file
hashes without changing the checkout. The source checker also verifies retained
patches. Native Linux/macOS frontend builds reconstruct an upstream CBE archive
by reversing the STC patch in a private copy and checking every upstream file
hash, then use the existing patch/build pipeline. This needs no nested Git
repository. Clang still builds from the locked Clang/CMake source archives.

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

## Native ARM64 Mac frontend build

`arduino/scripts/build-macos-frontend.py` builds the locked Clang 20.1.8 and
LLVM-CBE sources on an ARM64 Mac using Apple Clang, the selected Apple SDK,
LLVM 20.1.8 development files, CMake and Ninja. The build directory must be
new. For Homebrew dependencies installed without global links:

```sh
mkdir -p .build
PATH="/opt/homebrew/opt/cmake/bin:/opt/homebrew/opt/ninja/bin:$PATH" \
  python3 arduino/scripts/build-macos-frontend.py \
  --build-root "$PWD/.build/macos-frontend" --jobs 4
python3 arduino/scripts/verify-macos-frontend-build.py \
  --build "$PWD/.build/macos-frontend" \
  --output "$PWD/.build/macos-frontend-verification"
```

The verified Homebrew LLVM dependency requires macOS 15, so the frontend
defaults to deployment target 15.0. Changing that flag alone cannot establish
compatibility with an older OS. The build records SDK/compiler identities,
source patch checks, Mach-O dependencies and a before/after LLVM file inventory.
The verifier exercises both STC profiles at O0/Oz and executes generated C
return-identity checks on the Mac. These have passed on macOS 15.7.1 ARM64 with
Apple SDK 15.5. These build probes alone do not qualify Arduino or physical MCU
execution. Packaging and relocation checks have also passed on that native host:

```sh
python3 arduino/scripts/package-macos-frontend.py \
  --build "$PWD/.build/macos-frontend" --output "$PWD/.build/macos-package"
python3 arduino/scripts/verify-macos-frontend.py \
  --package "/absolute/relocated package" --manifest-sha256 EXPECTED_SHA256 \
  --output "$PWD/.build/macos-relocation-check" \
  --deny-input-prefix "$PWD/.build/macos-frontend" \
  --deny-input-prefix /opt/homebrew/Cellar/llvm@20/20.1.8 \
  --deny-input-prefix /opt/homebrew/Cellar/zstd/1.5.7_1
```

Copy the complete staged package to the relocation path first. Obtain the
expected manifest digest from the separate packager audit. The verifier checks
the full inventory before running tools; each denial is tested against an
otherwise readable source file. It executes the ABI and return-identity probes
under that sandbox. Only copied Mach-O files are rewritten to private
`@loader_path` dependencies and ad-hoc signed; all `LC_RPATH` entries are removed.
Two complete source builds in different directories on that Mac produced
identical Clang/CBE binaries and resource headers. After normalizing build-root
prefixes in the packaged recipe and provenance, both builds also produced the
same 54,382,979-byte frontend archive. Separate audits retain the actual build
paths, original build reports and packaging commands. Both packages passed
sandbox probes denying access to both source builds and the Homebrew dependencies.
The [Mac frontend archiver](arduino/scripts/archive-macos-frontend.py) verifies
the package manifest and records the archive digest for each candidate.
This does not establish Developer ID signing, notarization, Intel Mac or
lowest-supported-OS execution.

Use Python 3.11+ and the maintained archiver after native verification:

```sh
python3 arduino/scripts/archive-macos-frontend.py \
  --package "$PWD/.build/macos-package" --manifest-sha256 EXPECTED_SHA256 \
  --output "$PWD/.build/stcxx-frontend.tar.bz2"
```

The archiver requires the pinned complete manifest and passing ARM64 package
provenance. It rejects changed, missing or added files, symlinks and an existing
output; it checks the resulting archive against its inputs. `--output -` writes
only archive bytes to stdout and the successful JSON result to stderr, allowing
transfer without another remote disk copy. A receiver must require exit code 0
and verify the reported SHA-256 before accepting the stream. On the Mac, this
entry point passed six tests and reproduced the same archive as both source
builds. Linux also passed all six; Windows passed five with the symlink test
skipped because symlink privileges were not assumed.
