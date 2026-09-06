#!/usr/bin/env bash
set -euo pipefail

test $# -le 1 || { echo "usage: $0 [OUT_DIR]" >&2; exit 2; }
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
out_dir=${1:-${repo_root}/out}
sdcc="${out_dir}/bin/sdcc"

test -x "${sdcc}" || { echo "missing fixed compiler: ${sdcc}" >&2; exit 2; }
"${sdcc}" --version | grep -F 'mcs51/mcs251'
for name in sdcc sdcpp sdas8051 sdas251 sdld sdldmcs251 sdar; do
  test -x "${out_dir}/bin/${name}" || {
    echo "missing packaged tool: ${name}" >&2; exit 3;
  }
done
for name in sdcc sdcpp cc1; do
  test -x "${out_dir}/libexec/${name}" || {
    echo "missing compiler implementation: ${name}" >&2; exit 3;
  }
done
for model in small small-stack-auto large large-stack-auto \
  mcs251-small mcs251-small-stack-auto mcs251-large mcs251-large-stack-auto; do
  test -d "${out_dir}/share/sdcc/lib/${model}" || {
    echo "missing runtime model: ${model}" >&2; exit 3;
  }
done
"${sdcc}" --print-search-dirs
(cd "${out_dir}" && sha256sum -c MANIFEST.sha256)
echo "FIXED_OUT_INTEGRITY=PASS"
