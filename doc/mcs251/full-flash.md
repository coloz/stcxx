# STC32G full-Flash linking

The experimental MCS251 port is based on `gevico/sdcc-c251`, pinned to
`b09075b6a93e6afe10645181e3aeff041ea37f87`. Its 24-bit code pointers and
ECALL/EJMP instructions already support both sides of the STC32G reset area.
This extension adds placement, not software bank switching or a new ABI.
It also corrects a pre-existing code-read bug: compatibility `MOVC @A+DPTR`
uses the current PC region, ignoring DPXL. General `__code` reads now use flat
`MOV A,@DPX`; both switch-table forms carry address arithmetic across 64 KiB
boundaries and use flat reads/indirect EJMP.

| Device | Physical CODE interval (end exclusive) | Reset HOME | Capacity |
| --- | --- | --- | --- |
| STC32G12K128 | `0xFE0000:0x1000000` | `0xFF0000` | 131072 bytes |
| STC32G144K246 | `0xFC2800:0x1000000` | `0xFF0000` | 251904 bytes |

The previous 64/182 KiB ordinary-code limits stopped below HOME. The new mode
reserves only actual nonempty fixed areas, including reset/interrupt vectors;
ordinary code and constants may occupy the remainder of the upper 64 KiB.
Vectors, startup, runtime code and constants still count against total Flash.

## Driver options

Compile **every application translation unit** with the MCS251-only options
`--function-sections --data-sections`. The compiler emits bounded-length,
translation-unit-qualified `CSEG_F_*` and `CONST_D_*` areas. Explicit custom
`--codeseg` and `--constseg` names retain their existing placement semantics.
Startup and XINIT data-copy sequences are not split.

For example, link K128 objects with:

```sh
sdcc -mmcs251 --model-large --stack-auto \
  --code-loc 0xff0000 --code-size 131072 \
  -Wl-bGSINIT0=0xfe0000 \
  -Wl--code-window=0xfe0000:0x1000000 application.rel -o application.ihx
```

Use `0xfc2800` and `251904` for K246. Keep board-specific RAM, stack and runtime
library flags from the existing build. The end of `--code-window` is exclusive;
it may be `0x1000000`, but no emitted byte may have that address. The option
requires the MCS251 linker in 24-bit (`-r`) mode, which the driver selects.

Without the new options, legacy placement and generated section names are
unchanged. A rebuilt compiler **and** linker are required; changing only an
Arduino `upload.maximum_size` property is not sufficient. This source change
does not update an already downloaded native Windows/macOS/Linux tool package.

## Placement and validation

The linker reserves fixed/absolute CODE intervals, keeps GSINIT0 through GSFINAL
contiguous in startup order, and places remaining named areas into free
intervals. It retries in descending size order if input-order placement fails.
Function-area bases are even so the C++ member-function-pointer discriminator
remains valid; the bridge independently audits final relocated addresses.

A function, jump table, constant object or XINIT area is never split in the
middle. An individual object must fit a contiguous free interval; alignment
and fragmentation can therefore leave unused bytes. This is not a permanent
64 KiB reservation. Explicit assembly with short/page-limited instructions must
still satisfy those instructions' normal relocation constraints.

Invalid bounds, overlapping reservations, out-of-range fixed areas and areas
which cannot fit cause link failure before output. The map includes a
`Code Window:` header and full-name `Code Window Area:` records for every
occupied interval (individual fragments for absolute areas). Arduino's
`segmented_home` image validator requires this ledger and checks it against
the HEX image, including reset and interrupt placement.

## Local build and regressions

`arduino/scripts/build-sdcc-worktree-wsl.sh ABSOLUTE_NEW_BUILD_DIR` builds the
current source into a new directory without replacing a published toolchain.
It needs the same GCC, Boost, Flex, Bison and other dependencies as the regular
SDCC build; `CPPFLAGS` may point at an existing dependency installation.

The fixtures under `support/tests/full-flash/` verify real linked payloads
beyond both previous limits, cross-HOME direct/indirect calls, data and startup
relocations, the top Flash byte, invalid layouts, and unchanged legacy mode.
QEMU execution is optional for environments that only provide the compiler.
Simulator results do not substitute for physical-board validation.
