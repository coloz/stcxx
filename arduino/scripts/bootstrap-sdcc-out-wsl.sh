#!/usr/bin/env bash
set -euo pipefail

test $# -ge 1 && test $# -le 2 || {
  echo "usage: $0 ABSOLUTE_NEW_BUILD_ROOT [ABSOLUTE_NEW_OUT_DIR]" >&2
  exit 2
}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
build_root=$1
out_dir=${2:-${repo_root}/out}

"${script_dir}/build-wsl.sh" "${build_root}" sdcc
"${script_dir}/publish-sdcc-out-wsl.sh" "${build_root}/sdcc" "${out_dir}"
"${script_dir}/check-out-wsl.sh" "${out_dir}"
echo "BOOTSTRAP_SDCC_OUT=PASS"
