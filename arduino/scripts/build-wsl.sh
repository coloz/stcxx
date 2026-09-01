#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 ABSOLUTE_NEW_BUILD_ROOT [clang|llvm-cbe|sdcc|all]" >&2
  exit 2
}

test $# -ge 1 && test $# -le 2 || usage
build_root=$1
stage=${2:-all}
case "${build_root}" in
  /*) ;;
  *) echo "build root must be absolute: ${build_root}" >&2; exit 2 ;;
esac
case "${stage}" in clang|llvm-cbe|sdcc|all) ;; *) usage ;; esac
test "${build_root}" != / || { echo "refusing build root /" >&2; exit 2; }
test ! -e "${build_root}" || {
  echo "build root already exists; use a fresh path: ${build_root}" >&2
  exit 2
}

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
for command in git sha256sum cmake ninja make python3 patch tar; do
  command -v "${command}" >/dev/null || { echo "missing command: ${command}" >&2; exit 2; }
done

"${script_dir}/check-sources-wsl.sh"
mkdir -p "${build_root}"

build_clang() {
  command -v llvm-config-20 >/dev/null || { echo "missing llvm-config-20" >&2; exit 2; }
  mkdir -p "${build_root}/source/clang" "${build_root}/source/cmake"
  tar -xf "${repo_root}/arduino/sources/clang-20.1.8.src.tar.xz" \
    -C "${build_root}/source/clang" --strip-components=1
  tar -xf "${repo_root}/arduino/sources/cmake-20.1.8.src.tar.xz" \
    -C "${build_root}/source/cmake" --strip-components=1
  patch --dry-run --batch -d "${build_root}/source/clang" -p1 \
    < "${repo_root}/arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch"
  patch --batch -d "${build_root}/source/clang" -p1 \
    < "${repo_root}/arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch"
  cmake_args=(
    -S "${build_root}/source/clang"
    -B "${build_root}/clang"
    -G Ninja
    -DLLVM_DIR="$(llvm-config-20 --cmakedir)"
    -DCMAKE_BUILD_TYPE=Release
    -DLLVM_INCLUDE_TESTS=OFF
    -DCLANG_INCLUDE_TESTS=OFF
    -DCLANG_INCLUDE_DOCS=OFF
    -DCLANG_ENABLE_STATIC_ANALYZER=OFF
    -DCLANG_ENABLE_ARCMT=OFF
  )
  if test -f /usr/lib/x86_64-linux-gnu/libzstd.so.1; then
    cmake_args+=(
      -DSTC_ZSTD_RUNTIME=/usr/lib/x86_64-linux-gnu/libzstd.so.1
      -DCMAKE_PROJECT_TOP_LEVEL_INCLUDES="${repo_root}/arduino/cmake/BootstrapZstd.cmake"
    )
  fi
  cmake "${cmake_args[@]}"
  cmake --build "${build_root}/clang" --target clang --parallel
}

build_llvm_cbe() {
  command -v llvm-config-20 >/dev/null || { echo "missing llvm-config-20" >&2; exit 2; }
  cmake -S "${repo_root}/toolchain/llvm-cbe" -B "${build_root}/llvm-cbe" -G Ninja \
    -DLLVM_DIR="$(llvm-config-20 --cmakedir)" -DCMAKE_BUILD_TYPE=Release
  cmake --build "${build_root}/llvm-cbe" --target llvm-cbe --parallel
}

build_sdcc() {
  mkdir -p "${build_root}/sdcc"
  cd "${build_root}/sdcc"
  CFLAGS="-std=gnu17 -O2 -ffile-prefix-map=${repo_root}=." \
  CXXFLAGS="-O2 -ffile-prefix-map=${repo_root}=." \
  "${repo_root}/configure" \
    --enable-mcs251-port \
    --prefix=/sdcc-mcs251 \
    --datarootdir=/sdcc-mcs251 \
    'docdir=${datarootdir}/doc' \
    include_dir_suffix=include \
    non_free_include_dir_suffix=non-free/include \
    lib_dir_suffix=lib \
    non_free_lib_dir_suffix=non-free/lib \
    --disable-z80-port --disable-z180-port --disable-r2k-port \
    --disable-r2ka-port --disable-r3ka-port --disable-r4k-port \
    --disable-r5k-port --disable-r6k-port --disable-sm83-port \
    --disable-tlcs90-port --disable-ez80-port --disable-z80n-port \
    --disable-r800-port --disable-ds390-port --disable-ds400-port \
    --disable-pic14-port --disable-pic16-port --disable-hc08-port \
    --disable-s08-port --disable-stm8-port --disable-pdk13-port \
    --disable-pdk14-port --disable-pdk15-port --disable-mos6502-port \
    --disable-mos65c02-port --disable-f8-port --disable-f8l-port \
    --disable-ucsim --disable-sdcdb --disable-non-free
  make -j1
}

case "${stage}" in
  clang) build_clang ;;
  llvm-cbe) build_llvm_cbe ;;
  sdcc) build_sdcc ;;
  all) build_clang; build_llvm_cbe; build_sdcc ;;
esac

test ! -x "${build_root}/clang/bin/clang" || sha256sum "${build_root}/clang/bin/clang"
test ! -x "${build_root}/llvm-cbe/tools/llvm-cbe/llvm-cbe" || \
  sha256sum "${build_root}/llvm-cbe/tools/llvm-cbe/llvm-cbe"
test ! -x "${build_root}/sdcc/bin/sdcc" || {
  "${build_root}/sdcc/bin/sdcc" --version | head -n 1
  sha256sum "${build_root}/sdcc/bin/sdcc" "${build_root}/sdcc/src/sdcc"
}
echo "BUILD_STAGE=${stage}"
echo "BUILD_ROOT=${build_root}"
echo "TOOLCHAIN_BUILD=PASS"
