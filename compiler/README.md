# Shared STCXX driver

This MIT-licensed Rust crate owns the native C/C++ compilation pipeline,
LLVM-CBE adaptation, ABI audits, archive handling, linking and packaging.
`src/main.rs` exposes the standalone `stcxx` command. Arduino's native adapter
depends on this crate through Cargo, adapts build-system artifacts and recipes,
and calls the same compiler implementation in process through `run`.

From the STCXX repository root:

```sh
node scripts/build-driver.mjs
cargo test --manifest-path compiler/Cargo.toml --locked --offline
```

The build script runs Cargo inside this directory so `.cargo/config.toml`
applies, including the static Windows C runtime. It also collects dependency
license notices required for packaging. Compiler components are built through
the existing component build scripts; the driver consumes their locked native
artifacts without rebuilding Clang or SDCC.

`standalone.rs` implements source/object input, SDK discovery, chip selection
and standalone startup. `driver.rs`, `link.rs` and `adapter.rs` implement the
common compiler pipeline. The legacy `--platform` interface remains stable for
Arduino recipes. The only lifecycle difference at the linker roots is
`int main(void)` versus Arduino's `setup/loop`; ABI, storage and constructor
validation are shared.

Arduino's prebuild hook starts a fresh toolchain verification session. Jobs in
that build share a successful full SHA-256 check under an operating system file
lock, checking expected digests, resolved paths and file metadata before reuse.
Changed inputs invalidate the receipt; the next build resets it. Standalone
commands without a prepared session continue to verify all toolchain hashes.
The session lock requires Rust 1.89 or later.

Runtime and host-lock changes are made in `../sdk` first. Synchronize the
Arduino package copies with `node ../arduino-mcs251/scripts/sync-stcxx-sdk.mjs`
from the STCXX root, and use `--check` in validation. Chip compiler settings
must match Arduino's peripheral-oriented device catalog.
