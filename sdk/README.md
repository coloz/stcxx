# STCXX standalone C/C++ toolchain

The native `bin/stcxx` (`bin/stcxx.exe` on Windows) compiles freestanding
C11 and C++11 programs for the ten MCS251 chip profiles listed by
`stcxx --list-chips`. Keep the entire package together. No Arduino installation,
Python, shell interpreter, or source checkout is needed to compile firmware.

```sh
bin/stcxx --chip stc32g8k64 main.cpp peripheral.c -o firmware.hex
bin/stcxx --chip stc32g8k64 -c main.cpp -o main.o
bin/stcxx --chip stc32g8k64 main.o peripheral.c -o firmware.hex
```

Use `int main(void)` as the application entry. Startup initializes the
board-sized heap and runs global constructors before calling the application.
Returning from main enters an idle loop. Initialization of clocks, GPIO,
interrupts and other peripherals is the application's responsibility; no
Arduino `init`, `setup`, `loop`, timer or serial service is installed.
`F_CPU` describes the profile's default 12 MHz clock; it does not configure it.

`--interrupts declarations.h` includes SDCC interrupt-handler declarations in
the native startup translation unit, where SDCC generates the vector table.
Implement those handlers in a `.c` file. C++ calls native C functions through
`extern "C"` declarations. Use SDCC's device headers in native C; the C++
frontend does not accept SDCC SFR syntax.

Options include `-I`, `-D`, `-O0`, `-Oz`, `-L`, `-l`, and `--verbose`.
The default C++ optimization is Oz. ABI settings and reserved SDK macros are
not user overrides. `--toolchain` selects a package containing `frontend/`
and `sdcc/`; `STCXX_TOOLS_ROOT` is its environment equivalent. `--sdk` or
`STCXX_SDK_ROOT` selects the SDK. Installed tools discover both directories
relative to the executable. SDCC currently requires paths without whitespace.

The `.o` is accompanied by `.rel`, `.stcxx.*` and dependency sidecars. Preserve
them together when compiling and linking separately; a C++ `.o` alone is not
a native object library. Arduino's `archive` command packs all required
sidecars into a transportable library. Ordinary SDCC native libraries are
accepted using `.lib`, `.a`, `-L` and `-l`.

Each link keeps its compiler intermediates in a `stcxx-build-*` directory
beside the output. `firmware.build.json` records that directory and the HEX
hash; `firmware.stcxx/` holds optimized IR, adapted C and link audits. SDCC also
emits `.map` and `.mem` reports. A compilation or link failure removes the previous HEX and
success manifest. These intermediates can be removed after inspection.

The supplied runtime includes constructors, new/delete, allocator telemetry,
math and a small standard-header subset. Exceptions, RTTI, thread-safe static
initialization, global destructor execution, hosted FILE I/O and a complete
desktop C++ standard library are not supplied. The existing STC runtime ABI
identity is retained for compatibility.

C++ stdio uses native SDCC formatting. To attach console I/O, define
`int stcxx_console_write(unsigned char)` and `int stcxx_console_read(void)`
in a native C file. Write returns the emitted byte or EOF; read returns a byte
or EOF. Default hooks return EOF. `snprintf` needs no console initialization.
The declarations are in `runtime/include/stcxx_console.h`; include them from
native C using `#include "include/stcxx_console.h"` with the SDK's runtime root
on the include path. Arduino supplies its own UART adapter using the same hooks.

Runtime sources live in `runtime/src`, and Clang's freestanding headers live in
`runtime/include`. The native C compiler must not search `runtime/include`:
its `math.h`, `ctype.h`, etc. must come from SDCC. Native runtime sources include
specific shared ABI headers by relative path. `runtime-files.json` schema 2
owns the inventory and maps it to Arduino's `cores/STC/runtime`; all runtime
changes are made here first. Rebuild both host drivers after updating this layout.

The CBE adapter preserves requested native `<math.h>` declarations for dynamic
math calls such as `sqrt`. `runtime/src/stcxx_copysign.c` supplies the binary32
libcall LLVM can introduce when optimizing `truncf`; it stays native C to avoid
being optimized back into a call to itself. The implementation preserves
signed zero, subnormal values, infinities and NaN payloads.

All chip profiles remain experimental. A successful compile verifies compiler
and link contracts, not peripheral behavior or physical-board qualification.

Source ownership: `stcxx/compiler` maintains the shared native driver and
`stcxx/sdk` maintains this SDK. Arduino consumes the same Rust crate and keeps
checked SDK copies for platform packaging. `frontend/` and `sdcc/` retain
their upstream licenses; `share/stcxx/LICENSES` contains driver notices.
