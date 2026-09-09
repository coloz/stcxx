#!/usr/bin/env bash
# Build the current worktree into a new directory without replacing a release.
set -euo pipefail
[[ $# == 1 && $1 == /* && $1 != / && ! -e $1 ]] || {
  echo 'usage: build-sdcc-worktree-wsl.sh ABSOLUTE_NEW_BUILD_DIR' >&2
  exit 2
}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
build_dir=$1
mkdir -p "${build_dir}"
cd "${build_dir}"
CFLAGS="-std=gnu17 -O2 -ffile-prefix-map=${source_root}=." \
CXXFLAGS="-O2 -ffile-prefix-map=${source_root}=." \
CPPFLAGS="${CPPFLAGS:-}" \
"${source_root}/configure" \
  --enable-mcs251-port --prefix=/sdcc-mcs251 --datarootdir=/sdcc-mcs251 \
  include_dir_suffix=include non_free_include_dir_suffix=non-free/include \
  lib_dir_suffix=lib non_free_lib_dir_suffix=non-free/lib \
  --disable-z80-port --disable-z180-port --disable-r2k-port \
  --disable-r2ka-port --disable-r3ka-port --disable-r4k-port \
  --disable-r5k-port --disable-r6k-port --disable-sm83-port \
  --disable-tlcs90-port --disable-ez80-port --disable-z80n-port \
  --disable-r800-port --disable-ds390-port --disable-ds400-port \
  --disable-pic14-port --disable-pic16-port --disable-hc08-port \
  --disable-s08-port --disable-stm8-port --disable-pdk13-port \
  --disable-pdk14-port --disable-pdk15-port --disable-mos6502-port \
  --disable-mos65c02-port --disable-f8-port --disable-f8l-port \
  --disable-ucsim --disable-sdcdb --disable-non-free > configure.log 2>&1
# The SDCC runtime-library build shares intermediate outputs; keep its
# top-level build serial, like the pinned release build.
make -j1 > build.log 2>&1
printf 'SDCC_WORKTREE_BUILD=%s\n' "${build_dir}"
