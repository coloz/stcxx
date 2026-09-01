#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)

base_commit=b09075b6a93e6afe10645181e3aeff041ea37f87
sdcc_patch="${repo_root}/arduino/patches/sdcc-mcs251-isr-context.patch"
sdcc_combined_patch="${repo_root}/arduino/patches/sdcc-mcs251-arduino-cpp.patch"
clang_patch="${repo_root}/arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch"

check_sha() {
  local expected=$1
  local path=$2
  local actual
  actual=$(sha256sum "${path}" | awk '{print $1}')
  test "${actual}" = "${expected}" || {
    echo "SHA-256 mismatch: ${path}: ${actual}" >&2
    exit 3
  }
}

check_sha 46156f7ae915487cd31dd94a99934d05706db591bcf2942253e8248b2bf60b25 "${sdcc_patch}"
check_sha 684114dd748396fa9967f18b177944a50800cd5621386a8387e2af24c7aa08f5 "${sdcc_combined_patch}"
check_sha 42a6a91ba0f8803c22d266862a5e34293929a142314511d760dfc51a177e6715 "${clang_patch}"
check_sha b7a1b7b0af7b9c7596af6bd46e36d11321926eaa66a7a7dc957ab0a1375ee4b0 "${repo_root}/arduino/sources/clang-20.1.8.src.tar.xz"
check_sha 3319203cfd1172bbac50f06fa68e318af84dcb5d65353310c0586354069d6634 "${repo_root}/arduino/sources/cmake-20.1.8.src.tar.xz"
check_sha 03843d144e0786a1eb5443e79b6ef4852c86de36b8927e27f27f75095dc0d41d "${repo_root}/arduino/bridge/audit_and_adapt.py"
check_sha 611ec7be522cfa052e2c190151630e020be5fd73ef5aa8dc903103dfb591031c "${repo_root}/arduino/bridge/adapt.py"
check_sha c809a13ae0a5611850165a8dec56fbe5dd634dee6c2ee8bafebd16d8f31aa006 "${repo_root}/arduino/tests/check-bridge-adapter.py"

git -C "${repo_root}" cat-file -e "${base_commit}^{commit}"
git -C "${repo_root}" apply --reverse --check "${sdcc_combined_patch}"
git -C "${repo_root}" diff --check
patched_blob=$(git -C "${repo_root}" hash-object src/mcs251/gen.c)
test "${patched_blob}" = 61aeb1ca96b0a7b81b6c5aa2bd77fd413745cae0 || {
  echo "patched src/mcs251/gen.c blob mismatch: ${patched_blob}" >&2
  exit 3
}
patched_lower_blob=$(git -C "${repo_root}" hash-object src/mcs251/gen_lower.c.inc)
test "${patched_lower_blob}" = f7d78a4691338b931baef9b81e5aaf0033cbf5a3 || {
  echo "patched src/mcs251/gen_lower.c.inc blob mismatch: ${patched_lower_blob}" >&2
  exit 3
}
patched_peeph_blob=$(git -C "${repo_root}" hash-object src/SDCCpeeph.c)
test "${patched_peeph_blob}" = 74c37082698baca89bd8b2257b23e196387c124e || {
  echo "patched src/SDCCpeeph.c blob mismatch: ${patched_peeph_blob}" >&2
  exit 3
}
lkmain_blob=$(git -C "${repo_root}" hash-object sdas/linksrc/lkmain.c)
test "${lkmain_blob}" = ad605515eebec9bb3d8138e0d393b3dfde00287a || {
  echo "patched sdas/linksrc/lkmain.c blob mismatch: ${lkmain_blob}" >&2
  exit 3
}
lkmem_blob=$(git -C "${repo_root}" hash-object sdas/linksrc/lkmem.c)
test "${lkmem_blob}" = fcb4cabe3a0a41074176e82daed4973b31b6fc0b || {
  echo "patched sdas/linksrc/lkmem.c blob mismatch: ${lkmem_blob}" >&2
  exit 3
}

for script in "${repo_root}"/arduino/scripts/*.sh; do
  bash -n "${script}"
done

llvm_commit=$(git -C "${repo_root}/toolchain/llvm-project" rev-parse HEAD)
test "${llvm_commit}" = 87f0227cb60147a26a1eeb4fb06e3b505e9c7261
cbe_commit=$(git -C "${repo_root}/toolchain/llvm-cbe" rev-parse HEAD)
test "${cbe_commit}" = 83f1bea66c7415c701925470a2f7596b37153197
patch --dry-run --reverse --batch \
  -d "${repo_root}/toolchain/llvm-project/clang" -p1 \
  < "${clang_patch}" >/dev/null

echo "SDCC_BASE_COMMIT=${base_commit}"
echo "SDCC_PATCHED_GEN_BLOB=${patched_blob}"
echo "SDCC_PATCHED_GEN_LOWER_BLOB=${patched_lower_blob}"
echo "SDCC_PATCHED_PEEPH_BLOB=${patched_peeph_blob}"
echo "SDCC_PATCHED_LKMAIN_BLOB=${lkmain_blob}"
echo "SDCC_PATCHED_LKMEM_BLOB=${lkmem_blob}"
echo "LLVM_PROJECT_COMMIT=${llvm_commit}"
echo "LLVM_CBE_COMMIT=${cbe_commit}"
echo "SOURCE_LOCKS=PASS"
