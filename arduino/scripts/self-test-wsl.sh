#!/usr/bin/env bash
set -euo pipefail

test $# -eq 1 || { echo "usage: $0 BUILD_ROOT" >&2; exit 2; }
build_root=$1
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
test -d "${build_root}" || { echo "missing build root: ${build_root}" >&2; exit 2; }

"${script_dir}/check-sources-wsl.sh"
pycache="${build_root}/pycache"
mkdir -p "${pycache}"
PYTHONPYCACHEPREFIX="${pycache}" python3 -m py_compile \
  "${repo_root}/arduino/bridge/audit_and_adapt.py" \
  "${repo_root}/arduino/bridge/adapt.py" \
  "${repo_root}/arduino/tests/check-bridge-adapter.py" \
  "${repo_root}/arduino/tests/check_sdcc_big_endian_narrowing.py"

python3 "${repo_root}/arduino/tests/check-bridge-adapter.py"

if test -x "${build_root}/clang/bin/clang"; then
  python3 "${repo_root}/arduino/tests/check-clang-target.py" \
    --clang "${build_root}/clang/bin/clang" \
    --clang-source "${build_root}/source/clang" \
    --output "${build_root}/clang-check"
fi

if test -x "${build_root}/llvm-cbe/tools/llvm-cbe/llvm-cbe"; then
  "${build_root}/llvm-cbe/tools/llvm-cbe/llvm-cbe" --version | head -n 2
fi

if test -x "${build_root}/sdcc/bin/sdcc"; then
  sdcc="${build_root}/sdcc/bin/sdcc"
  "${sdcc}" --version | grep -F 'mcs51/mcs251'
  make -C "${build_root}/sdcc/src/mcs251" check
  python3 "${repo_root}/arduino/tests/check_sdcc_big_endian_narrowing.py" \
    --sdcc "${sdcc}" \
    --source "${repo_root}/arduino/tests/sdcc-mcs251-u32-low24.c" \
    --far-source "${repo_root}/arduino/tests/sdcc-mcs251-be-memory-low-byte.c" \
    --overlap-source "${repo_root}/arduino/tests/sdcc-mcs251-overlap-generic-store.c"
  mkdir -p "${build_root}/long-dptr-symbol"
  "${sdcc}" -mmcs251 --model-large --stack-auto --std-sdcc11 \
    --opt-code-size -c \
    "${repo_root}/arduino/tests/sdcc-mcs251-long-dptr-symbol.c" \
    -o "${build_root}/long-dptr-symbol/long-dptr-symbol.rel"
  test -s "${build_root}/long-dptr-symbol/long-dptr-symbol.rel"
  mkdir -p "${build_root}/smoke"
  "${sdcc}" -mmcs51 --model-large "${repo_root}/arduino/tests/smoke.c" \
    -o "${build_root}/smoke/mcs51.ihx"
  "${sdcc}" -mmcs251 --model-large --stack-auto \
    "${repo_root}/arduino/tests/smoke.c" -o "${build_root}/smoke/mcs251.ihx"
  test -s "${build_root}/smoke/mcs51.ihx"
  test -s "${build_root}/smoke/mcs251.ihx"
fi

echo "TOOLCHAIN_SELF_TEST=PASS"
