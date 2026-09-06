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
check_sha cf69ac0418f940e1ccc950ecff72d81e29a017847172747dc2cf3e31d26fabb3 "${sdcc_combined_patch}"
check_sha b24f23cb45d29b766ab9a894452ed274dbb301a7315aa04b169d112bc5d1183e "${repo_root}/src/mcs251/Makefile.in"
check_sha f8fda423712d808dd087d4e789b1e824911cde62d738078bf9325a898d8476c0 "${clang_patch}"
check_sha 0a332f0000aa9d335eb4c0b67bbd40b3020d9acf586c4279b5e8a0ecd2c3025f "${cbe_patch}"
check_sha b7a1b7b0af7b9c7596af6bd46e36d11321926eaa66a7a7dc957ab0a1375ee4b0 "${repo_root}/arduino/sources/clang-20.1.8.src.tar.xz"
check_sha 3319203cfd1172bbac50f06fa68e318af84dcb5d65353310c0586354069d6634 "${repo_root}/arduino/sources/cmake-20.1.8.src.tar.xz"
check_sha d5786ada45174a4da0fd6c01ac0e2b589533e87fd8090347a136e60459b1ad24 "${repo_root}/arduino/bridge/audit_and_adapt.py"
check_sha 57f696501cb3254ebc31a6f31f17262d1e4d5fa39be710cce8a04011cdae5e52 "${repo_root}/arduino/bridge/adapt.py"
check_sha d89d1a430676e178979310c856f7e331a6c18bb2f640d4afa9bef2810ba8aa50 "${repo_root}/arduino/scripts/build-wsl.sh"
check_sha fc6b85c57169f5c179f2e6f564792ec496655ee03c806db1df84d8b09e240c95 "${repo_root}/arduino/scripts/prepare-sources-wsl.sh"

check_normalized_sha 23ade7d95b1a7497f20b1bcfa8823c33ecb72087c1c0aa05dc52020a96aad289 "${repo_root}/toolchain/llvm-project/clang/include/clang/Basic/TargetInfo.h"
check_normalized_sha 83e17d363bfebaa63486d107b28c6b6cd531dd4fb307a75db6ae9fac9ae09ee3 "${repo_root}/toolchain/llvm-project/clang/lib/AST/ASTContext.cpp"
check_normalized_sha 4af3f218d0e4d3838b65acda2f0f400a659d297e6b2264dc3eed0e892abb59fa "${repo_root}/toolchain/llvm-project/clang/lib/AST/ItaniumCXXABI.cpp"
check_normalized_sha 8650ce2fb14da7f7f6d72e49297ed34a99843834bfc7d3b1cc082ef7c02eb83a "${repo_root}/toolchain/llvm-project/clang/lib/Basic/Targets/MSP430.cpp"
check_normalized_sha c66b7aa1ef9567806ac619c074b25ce6226cb6c69ae340ef864bc09e55906514 "${repo_root}/toolchain/llvm-project/clang/lib/Basic/Targets/MSP430.h"
check_normalized_sha 5390b53f2a35534b5c65b0aa3c93c5d294de43922567718636e0930ff4f68c39 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/BackendUtil.cpp"
check_normalized_sha bc9cec92964a15a0e7140168c045b8b7bca8ff11a858cf989d556f38bdd92893 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/CGCall.cpp"
check_normalized_sha 9e3eb4a610e6ff73c10dd45e4145b1d301d685ffc8517a8b6e0d9b39c11b2904 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/CGExprScalar.cpp"
check_normalized_sha a28dfce56a93f74a7511f1eef7e369d8a7e51245c80831a8b0092c14fa20fa53 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/CGVTables.cpp"
check_normalized_sha c7af4286b9acd14f5d81111ae708aa8abbd7b16b7c2131c4fe758b47f86d8d61 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/CodeGenModule.cpp"
check_normalized_sha b556fcf113c24ce29c7f303f0a19fcfebe93b421bf1e87b21ba8bae0cd6ccbf2 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/CodeGenModule.h"
check_normalized_sha 5da5e393f30104f121ac94bb131a70411b65ff99b7519bacb0ee2126d5e20cd1 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/CodeGenTypeCache.h"
check_normalized_sha 299449910e285455679d28db550a1f058904027b867e814d92869b199da9f3d5 "${repo_root}/toolchain/llvm-project/clang/lib/CodeGen/ItaniumCXXABI.cpp"
check_normalized_sha 2e53d79ee3d3c23ee3f792c5d21fe0dcdf7e41f1214141a7cca0e927241f4f56 "${repo_root}/toolchain/llvm-project/clang/lib/Driver/ToolChains/Clang.cpp"
check_normalized_sha 6772e3298c06f78a12bbf0bd6ef41bb087a5af6fd6f9ce35251614a559bb6d4e "${repo_root}/toolchain/llvm-cbe/lib/Target/CBackend/CBackend.cpp"
check_normalized_sha c4bb3e5141ade91bb86067d9df3e2a73810e3a6836aa43f0db8be15f41b4f7b0 "${repo_root}/toolchain/llvm-cbe/lib/Target/CBackend/CBackend.h"

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
  src/SDCClrange.c \
  sdas/linksrc/lkmain.c \
  sdas/linksrc/lkmem.c
patched_blob=$(git -C "${repo_root}" hash-object src/mcs251/gen.c)
test "${patched_blob}" = 61aeb1ca96b0a7b81b6c5aa2bd77fd413745cae0 || {
  echo "patched src/mcs251/gen.c blob mismatch: ${patched_blob}" >&2
  exit 3
}
patched_lower_blob=$(git -C "${repo_root}" hash-object src/mcs251/gen_lower.c.inc)
test "${patched_lower_blob}" = 026c645c3a63d83fa02bf6131d110e4659bc2b86 || {
  echo "patched src/mcs251/gen_lower.c.inc blob mismatch: ${patched_lower_blob}" >&2
  exit 3
}
patched_peeph_blob=$(git -C "${repo_root}" hash-object src/SDCCpeeph.c)
test "${patched_peeph_blob}" = 74c37082698baca89bd8b2257b23e196387c124e || {
  echo "patched src/SDCCpeeph.c blob mismatch: ${patched_peeph_blob}" >&2
  exit 3
}
patched_lrange_blob=$(git -C "${repo_root}" hash-object src/SDCClrange.c)
test "${patched_lrange_blob}" = b14e27bc67892534c060ec2ea0dea79025ed0cab || {
  echo "patched src/SDCClrange.c blob mismatch: ${patched_lrange_blob}" >&2
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
echo "SDCC_PATCHED_LRANGE_BLOB=${patched_lrange_blob}"
echo "SDCC_PATCHED_LKMAIN_BLOB=${lkmain_blob}"
echo "SDCC_PATCHED_LKMEM_BLOB=${lkmem_blob}"
echo "LLVM_PROJECT_COMMIT=${llvm_commit}"
echo "LLVM_CBE_COMMIT=${cbe_commit}"
echo "SOURCE_LOCKS=PASS"
