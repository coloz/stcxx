#!/usr/bin/env bash
# Cross-build Windows tools; target runtime archives are built separately by
# the native Linux compiler from the same source, then tested on Windows.
# WSL dependencies: g++-mingw-w64-x86-64-posix, libz-mingw-w64-dev,
# libboost-dev, build-essential, bison and flex.
set -euo pipefail
[[ $# == 1 && $1 == /* && $1 != / && ! -e $1 ]] || {
  echo 'usage: build-windows-sdcc-wsl.sh ABSOLUTE_NEW_BUILD_DIR' >&2
  exit 2
}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
source_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
build_dir=$1
for command in x86_64-w64-mingw32-gcc x86_64-w64-mingw32-g++ make bison flex; do
  command -v "$command" >/dev/null || { echo "missing: $command" >&2; exit 2; }
done
test -d /usr/include/boost || { echo 'missing Boost headers' >&2; exit 2; }
printf '#include <zlib.h>\n' | x86_64-w64-mingw32-gcc -x c -fsyntax-only - || {
  echo 'missing Windows zlib headers (libz-mingw-w64-dev)' >&2
  exit 2
}
mkdir -p "${build_dir}/host-include"
# Add only Boost to the cross compiler include path, never Linux libc headers.
cp -a /usr/include/boost "${build_dir}/host-include/"
cd "${build_dir}"
printf 'RUNNING\n' > status.txt
trap 'printf "FAIL\n" > status.txt' ERR
export SOURCE_DATE_EPOCH=1788134400
export LC_ALL=C TZ=UTC
CC=x86_64-w64-mingw32-gcc CXX=x86_64-w64-mingw32-g++ \
AR=x86_64-w64-mingw32-ar RANLIB=x86_64-w64-mingw32-ranlib \
STRIP=x86_64-w64-mingw32-strip CC_FOR_BUILD=gcc \
CFLAGS="-std=gnu17 -O2 -ffile-prefix-map=${source_root}=." \
CXXFLAGS="-O2 -ffile-prefix-map=${source_root}=." \
CPPFLAGS="-I${build_dir}/host-include" \
sdccconf_h_dir_separator='\\' \
LDFLAGS='-static -static-libgcc -static-libstdc++ -Wl,--no-insert-timestamp' \
"${source_root}/configure" \
  --host=x86_64-w64-mingw32 --build=x86_64-pc-linux-gnu \
  --enable-mcs251-port --prefix=/sdcc --datarootdir=/sdcc \
  include_dir_suffix=include non_free_include_dir_suffix=non-free/include \
  lib_dir_suffix=lib non_free_lib_dir_suffix=non-free/lib \
  --disable-device-lib \
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
make -j2 > build.log 2>&1
# GNU libtool interprets plain -static as static libtool libraries only.
# Its -all-static option also prevents external zlib/winpthread DLL imports.
for name in sdar sdnm sdobjcopy sdranlib; do
  rm -- "support/sdbinutils/binutils/${name}.exe"
done
make -C support/sdbinutils/binutils \
  'LDFLAGS=-all-static -static-libgcc -static-libstdc++ -Wl,--no-insert-timestamp -Wl,--stack,12582912' \
  sdar.exe sdnm.exe sdobjcopy.exe sdranlib.exe > static-binutils.log 2>&1
printf 'PASS\n' > status.txt
printf 'WINDOWS_SDCC_BUILD=%s\n' "${build_dir}"
