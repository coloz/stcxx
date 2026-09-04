#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)

base_commit=b09075b6a93e6afe10645181e3aeff041ea37f87
sdcc_patch="${repo_root}/arduino/patches/sdcc-mcs251-isr-context.patch"
sdcc_combined_patch="${repo_root}/arduino/patches/sdcc-mcs251-arduino-cpp.patch"
clang_patch="${repo_root}/arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch"
cbe_patch="${repo_root}/arduino/patches/llvm-cbe-83f1bea-stc-sdcc.patch"

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

check_normalized_sha() {
  local expected=$1
  local path=$2
  local actual
  actual=$(sed 's/\r$//' "${path}" | sha256sum | awk '{print $1}')
  test "${actual}" = "${expected}" || {
    echo "Normalized SHA-256 mismatch: ${path}: ${actual}" >&2
    exit 3
  }
}

check_sha 46156f7ae915487cd31dd94a99934d05706db591bcf2942253e8248b2bf60b25 "${sdcc_patch}"
check_sha 684114dd748396fa9967f18b177944a50800cd5621386a8387e2af24c7aa08f5 "${sdcc_combined_patch}"
check_sha 97f1fd8a824e583456e3335935125a712b05f2b25613c6a352e2c7b3b8c68dcc "${clang_patch}"
check_sha 6a3e793f69ffdde98495935103545eebbeebdecb5edc3b853c341639199802ac "${cbe_patch}"
check_sha b7a1b7b0af7b9c7596af6bd46e36d11321926eaa66a7a7dc957ab0a1375ee4b0 "${repo_root}/arduino/sources/clang-20.1.8.src.tar.xz"
check_sha 3319203cfd1172bbac50f06fa68e318af84dcb5d65353310c0586354069d6634 "${repo_root}/arduino/sources/cmake-20.1.8.src.tar.xz"
check_sha 28397cca4f7c1c73e83df05e72ae0fdd2e122a122c2d7f915416f8a2a0275575 "${repo_root}/arduino/bridge/audit_and_adapt.py"
check_sha 9980df2cd3d8805b33cd6aa1266984658a7e7f0676a24d47fbb6f6a82b0e7bb0 "${repo_root}/arduino/bridge/adapt.py"
check_sha 1b98790a11db215d0e3fa625d0d202b662873f527db8b6c0b19bb794ff2ee180 "${repo_root}/arduino/tests/check-bridge-adapter.py"
check_sha 7c1af48a2c4569dc69ede3593aec6ac71b9b491b74c68837d3e79fc19e9c035a "${repo_root}/arduino/tests/check-stc-cpp-targets.py"
check_sha e3a9500f1196c3c96348035e1ddd988955939d996df3eea04d1c66344348808f "${repo_root}/arduino/tests/probe/mcs51_target_info_probe.cpp"
check_sha 3950bf3b179580c3181270b2a7ae6ca3be28bc21ff2b75e98870a58876e0af4b "${repo_root}/arduino/tests/probe/target_info_probe.cpp"
check_sha cf1eb3509bac8598e6887a27550fa7aeaf79b6b6984d034845196c8a3629f6e8 "${repo_root}/arduino/tests/probe/stock_msp430_probe.cpp"
check_sha f87cb48a95d6dae43ffba6cfbde9a73298e28feca254f8176b7cc48fadbd2c81 "${repo_root}/arduino/tests/cbe/run-stc-regressions.sh"
check_sha 8a3b5b8202f3ae5bcfcd2809ff09d7a6044be64b832d48b20bd7e82e30e51603 "${repo_root}/arduino/tests/cbe/i24-stc-mcs251.ll"
check_sha 1fe27783b4e939a590381b125b13c9785a871b7d11df5ad342554947c5d9c66c "${repo_root}/arduino/tests/cbe/i24-generic.ll"
check_sha 2666306180926ad124a70952146b8e8a01844daa9490277ec92755d2bb691946 "${repo_root}/arduino/tests/cbe/aggregate-pointer-cast.ll"
check_sha a58a33ce9438c59b50fcdae39aa4ca9164dd566d8c4e112376d2965322344f57 "${repo_root}/arduino/tests/cbe/zero-sized-global-const.ll"

check_normalized_sha f317ced88d01c724ae9aa173d9821fb13c029d8993722eb0bb185bb2a0058fdc "${repo_root}/toolchain/llvm-project/clang/include/clang/Basic/TargetInfo.h"
check_normalized_sha 0b272f62782f8d6cb52ccdf1f17682e2958ec1cf4f91a75e63d1f2bfe5997175 "${repo_root}/toolchain/llvm-project/clang/lib/AST/ASTContext.cpp"
check_normalized_sha 17b7eca39815b2993220362ed583a07db98f36e76a311052f0eb6f0e2dfad82c "${repo_root}/toolchain/llvm-project/clang/lib/AST/ItaniumCXXABI.cpp"
check_normalized_sha 8650ce2fb14da7f7f6d72e49297ed34a99843834bfc7d3b1cc082ef7c02eb83a "${repo_root}/toolchain/llvm-project/clang/lib/Basic/Targets/MSP430.cpp"
check_normalized_sha a9458b753247049fe2b3b9cab2b9c88eb0e24af87658f82d494281fc4ab2a38a "${repo_root}/toolchain/llvm-project/clang/lib/Basic/Targets/MSP430.h"
check_normalized_sha 5390b53f2a35534b5c65b0aa3c93c5d294de43922567718636e0930ff4f68c39 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/BackendUtil.cpp"
check_normalized_sha 16463dc026b78539a629e67dada35d2af233d1c6d93ea15220455b3803d3a899 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/ItaniumCXXABI.cpp"
check_normalized_sha 2e53d79ee3d3c23ee3f792c5d21fe0dcdf7e41f1214141a7cca0e927241f4f56 "${repo_root}/toolchain/llvm-project/clang/lib/Driver/ToolChains/Clang.cpp"
check_normalized_sha 4646930a0edf0d33845c9770af7c4fa8946d48822ec000b5bee3fff3e746045c "${repo_root}/toolchain/llvm-cbe/lib/Target/CBackend/CBackend.cpp"

git -C "${repo_root}" cat-file -e "${base_commit}^{commit}"
git -C "${repo_root}" apply --reverse --check "${sdcc_combined_patch}"
# Keep this check scoped to the authoritative tracked inputs.  An unscoped
# submodule diff asks Git to inspect the partial LLVM worktrees and can trigger
# a promisor fetch even though their exact patches are verified below.
git -C "${repo_root}" diff --check -- \
  arduino \
  src/mcs251/gen.c \
  src/mcs251/gen_lower.c.inc \
  src/SDCCpeeph.c \
  sdas/linksrc/lkmain.c \
  sdas/linksrc/lkmem.c
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
git -C "${repo_root}/toolchain/llvm-project" apply --reverse --check \
  --directory=clang "${clang_patch}"
git -C "${repo_root}/toolchain/llvm-cbe" apply --reverse --check \
  "${cbe_patch}"
echo "SDCC_BASE_COMMIT=${base_commit}"
echo "SDCC_PATCHED_GEN_BLOB=${patched_blob}"
echo "SDCC_PATCHED_GEN_LOWER_BLOB=${patched_lower_blob}"
echo "SDCC_PATCHED_PEEPH_BLOB=${patched_peeph_blob}"
echo "SDCC_PATCHED_LKMAIN_BLOB=${lkmain_blob}"
echo "SDCC_PATCHED_LKMEM_BLOB=${lkmem_blob}"
echo "LLVM_PROJECT_COMMIT=${llvm_commit}"
echo "LLVM_CBE_COMMIT=${cbe_commit}"
echo "SOURCE_LOCKS=PASS"
