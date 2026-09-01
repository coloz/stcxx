#!/usr/bin/env bash
set -euo pipefail

test $# -le 1 || { echo "usage: $0 [OUT_DIR]" >&2; exit 2; }
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
out_dir=${1:-${repo_root}/out}
sdcc="${out_dir}/bin/sdcc"

test -x "${sdcc}" || { echo "missing fixed compiler: ${sdcc}" >&2; exit 2; }
"${sdcc}" --version | grep -F 'mcs51/mcs251'
python3 "${repo_root}/arduino/tests/check-extended-stack.py" \
  --sdcc "${sdcc}" \
  --mcs251-runtime-lib "${out_dir}/share/sdcc/lib/mcs251-large-stack-auto" \
  --mcs51-runtime-lib "${out_dir}/share/sdcc/lib/large-stack-auto" \
  --source "${repo_root}/arduino/tests/extended-stack-smoke.c" \
  --work-dir "${out_dir}/self-test/extended-stack"
(cd "${out_dir}" && sha256sum -c MANIFEST.sha256)
echo "FIXED_OUT_SELF_TEST=PASS"
