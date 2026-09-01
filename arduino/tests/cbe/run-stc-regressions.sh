#!/usr/bin/env bash
set -euo pipefail

test $# -eq 3 || {
  echo "usage: $0 LLVM_CBE SDCC ABSOLUTE_OUTPUT" >&2
  exit 2
}
cbe=$1
sdcc=$2
output=$3
case "${output}" in /*) ;; *) echo "output must be absolute" >&2; exit 2 ;; esac
script_dir="$(cd "$(dirname "$0")" && pwd)"
host_cc="${CC:-cc}"
sdcc_root="$(dirname "$(dirname "${sdcc}")")"
sdcc_include_root="${STCXX_SDCC_INCLUDE_ROOT:-${sdcc_root}/share/sdcc/include}"
mkdir -p "${output}"

"${cbe}" "${script_dir}/i24-stc-mcs251.ll" -o "${output}/i24-stc.c"
"${cbe}" "${script_dir}/i24-generic.ll" -o "${output}/i24-generic.c"
"${cbe}" "${script_dir}/aggregate-pointer-cast.ll" \
  -o "${output}/aggregate-pointer-cast.c"
"${cbe}" "${script_dir}/zero-sized-global-const.ll" \
  -o "${output}/zero-sized-global-const.c"

grep -Fq 'unsigned _BitInt(24) field0;' "${output}/i24-stc.c"
grep -Fq 'unsigned _BitInt(24) field1;' "${output}/i24-stc.c"
if grep -Fq '_BitInt(24)' "${output}/i24-generic.c"; then
  echo 'generic llvm-cbe i24 behavior changed' >&2
  exit 1
fi
grep -Fq 'uint32_t field0;' "${output}/i24-generic.c"
grep -Fq 'uint32_t field1;' "${output}/i24-generic.c"
grep -Fq '((struct l_struct_struct_OC_stcxx_guard*)&storage)->field1' \
  "${output}/aggregate-pointer-cast.c"
grep -Fq '((const void*)&constant_storage)' \
  "${output}/zero-sized-global-const.c"
grep -Fq '((void*)&mutable_storage)' \
  "${output}/zero-sized-global-const.c"

"${host_cc}" -std=c99 -c "${output}/i24-generic.c" \
  -o "${output}/i24-generic.o"
for source in i24-stc aggregate-pointer-cast zero-sized-global-const; do
  "${sdcc}" -mmcs251 --model-large --stack-auto --std-sdcc11 \
    --less-pedantic \
    "-I${sdcc_include_root}" \
    "-I${sdcc_include_root}/mcs51" \
    -c "${output}/${source}.c" -o "${output}/${source}.rel" \
    2>"${output}/${source}.stderr"
done
if grep -Fq 'warning 357:' "${output}/zero-sized-global-const.stderr"; then
  echo 'const zero-sized global still emits warning 357' >&2
  exit 1
fi
test -s "${output}/i24-generic.o"
test -s "${output}/i24-stc.rel"
test -s "${output}/aggregate-pointer-cast.rel"
test -s "${output}/zero-sized-global-const.rel"
echo 'STCXX_CBE_REGRESSIONS=PASS'
