#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 SDCC_BUILD_DIR [ABSOLUTE_NEW_OUT_DIR]" >&2
  exit 2
}

test $# -ge 1 && test $# -le 2 || usage
build_dir=$1
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
out_dir=${2:-${repo_root}/out}

case "${build_dir}" in /*) ;; *) echo "build dir must be absolute" >&2; exit 2;; esac
case "${out_dir}" in /*) ;; *) echo "out dir must be absolute" >&2; exit 2;; esac
test "${out_dir}" != / || { echo "refusing out dir /" >&2; exit 2; }
test ! -e "${out_dir}" || { echo "out dir already exists: ${out_dir}" >&2; exit 2; }

required=(
  "${build_dir}/src/sdcc"
  "${build_dir}/bin/sdas251"
  "${build_dir}/bin/sdas8051"
  "${build_dir}/bin/sdld"
  "${build_dir}/bin/sdldmcs251"
  "${build_dir}/support/cpp/gcc/cpp"
  "${build_dir}/support/cpp/gcc/cc1"
  "${build_dir}/support/sdbinutils/binutils/sdar"
  "${build_dir}/support/sdbinutils/binutils/sdnm"
  "${build_dir}/support/sdbinutils/binutils/sdobjcopy"
  "${build_dir}/support/sdbinutils/binutils/sdranlib"
  "${build_dir}/device/lib/build/mcs251-large-stack-auto/mcs251.lib"
)
for path in "${required[@]}"; do
  test -x "${path}" || test -s "${path}" || { echo "missing build artifact: ${path}" >&2; exit 3; }
done

pending_dir="${out_dir}.pending"
test ! -e "${pending_dir}" || { echo "pending dir already exists: ${pending_dir}" >&2; exit 2; }
cleanup() { rm -rf -- "${pending_dir}"; }
trap cleanup EXIT

install -d "${pending_dir}/bin" "${pending_dir}/libexec" \
  "${pending_dir}/share/sdcc/include" "${pending_dir}/share/sdcc/lib"
install -m 0755 "${build_dir}/src/sdcc" "${pending_dir}/libexec/sdcc"
install -m 0755 "${build_dir}/support/cpp/gcc/cpp" "${pending_dir}/libexec/sdcpp"
install -m 0755 "${build_dir}/support/cpp/gcc/cc1" "${pending_dir}/libexec/cc1"
install -m 0755 "${repo_root}/arduino/wrappers/sdcc" "${pending_dir}/bin/sdcc"
install -m 0755 "${repo_root}/arduino/wrappers/sdcpp" "${pending_dir}/bin/sdcpp"

for name in sdas251 sdas8051 sdld sdldmcs251 packihx makebin as2gbmap; do
  install -m 0755 "${build_dir}/bin/${name}" "${pending_dir}/bin/${name}"
done
for name in sdar sdnm sdobjcopy sdranlib; do
  install -m 0755 "${build_dir}/support/sdbinutils/binutils/${name}" \
    "${pending_dir}/bin/${name}"
done

cp -a "${repo_root}/device/include/." "${pending_dir}/share/sdcc/include/"
for model_dir in "${build_dir}"/device/lib/build/*; do
  test -d "${model_dir}" || continue
  model=$(basename -- "${model_dir}")
  install -d "${pending_dir}/share/sdcc/lib/${model}"
  cp -a "${model_dir}"/*.lib "${pending_dir}/share/sdcc/lib/${model}/"
done

install -m 0644 "${repo_root}/arduino/toolchain-lock.json" \
  "${pending_dir}/toolchain-lock.json"
(
  cd "${pending_dir}"
  find bin libexec share -type f -print0 | sort -z | xargs -0 sha256sum > MANIFEST.sha256
)

mv -- "${pending_dir}" "${out_dir}"
trap - EXIT
"${out_dir}/bin/sdcc" --version | head -n 2
"${out_dir}/bin/sdcc" -mmcs251 --model-large --stack-auto --print-search-dirs
sha256sum "${out_dir}/bin/sdcc" "${out_dir}/libexec/sdcc" \
  "${out_dir}/bin/sdld" "${out_dir}/bin/sdldmcs251"
echo "SDCC_OUT=${out_dir}"
echo "PUBLISH_SDCC_OUT=PASS"
