#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
clang_root="${repo_root}/toolchain/llvm-project/clang"
clang_patch="${repo_root}/arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch"
cbe_root="${repo_root}/toolchain/llvm-cbe"
cbe_patch="${repo_root}/arduino/patches/llvm-cbe-83f1bea-stc-sdcc.patch"

identity=$(python3 - "${repo_root}/arduino/toolchain-lock.json" <<'PY'
import json, re, sys
with open(sys.argv[1], encoding='utf-8') as source:
    lock = json.load(source)
values = [lock['clang']['commit'], lock['clang']['patch_sha256'],
          lock['llvm_cbe']['commit'], lock['llvm_cbe']['patch_sha256']]
for value, width in zip(values, (40, 64, 40, 64)):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{%d}' % width, value):
        raise SystemExit('Invalid source identity in toolchain-lock.json')
print(' '.join(values))
PY
)
read -r expected_llvm_commit expected_patch_sha expected_cbe_commit expected_cbe_patch_sha <<< "${identity}"

# Windows checkouts may materialize the patched files as CRLF.  Canonicalize
# only the files named by the locked patches before applying them so ordinary
# (non-whitespace-relaxed) git-apply checks remain deterministic in WSL.
clang_patch_files=(
  include/clang/Basic/TargetInfo.h
  lib/AST/ASTContext.cpp
  lib/AST/ItaniumCXXABI.cpp
  lib/Basic/Targets/MSP430.cpp
  lib/Basic/Targets/MSP430.h
  lib/CodeGen/BackendUtil.cpp
  lib/CodeGen/CGCall.cpp
  lib/CodeGen/CGExprScalar.cpp
  lib/CodeGen/CGVTables.cpp
  lib/CodeGen/CodeGenModule.cpp
  lib/CodeGen/CodeGenModule.h
  lib/CodeGen/CodeGenTypeCache.h
  lib/CodeGen/ItaniumCXXABI.cpp
  lib/Driver/ToolChains/Clang.cpp
)
for file in "${clang_patch_files[@]}"; do
  sed -i 's/\r$//' "${clang_root}/${file}"
done
for file in CBackend.cpp CBackend.h; do
  sed -i 's/\r$//' "${cbe_root}/lib/Target/CBackend/${file}"
done

test -d "${clang_root}"
test -f "${clang_root}/lib/Basic/Targets/MSP430.cpp"
test -f "${clang_patch}"
test -f "${cbe_root}/lib/Target/CBackend/CBackend.cpp"
test -f "${cbe_patch}"

actual_commit=$(git -C "${repo_root}/toolchain/llvm-project" rev-parse HEAD)
test "${actual_commit}" = "${expected_llvm_commit}" || {
  echo "LLVM source commit mismatch: ${actual_commit}" >&2
  exit 3
}
actual_patch_sha=$(sha256sum "${clang_patch}" | awk '{print $1}')
test "${actual_patch_sha}" = "${expected_patch_sha}" || {
  echo "Clang patch SHA-256 mismatch: ${actual_patch_sha}" >&2
  exit 3
}

if git -C "${repo_root}/toolchain/llvm-project" apply --reverse --check \
    --directory=clang "${clang_patch}" >/dev/null 2>&1; then
  echo "CLANG_STC_PATCH=ALREADY_APPLIED"
else
  git -C "${repo_root}/toolchain/llvm-project" apply --check \
    --directory=clang "${clang_patch}"
  git -C "${repo_root}/toolchain/llvm-project" apply \
    --directory=clang "${clang_patch}"
fi
git -C "${repo_root}/toolchain/llvm-project" apply --reverse --check \
  --directory=clang "${clang_patch}"

actual_cbe_commit=$(git -C "${cbe_root}" rev-parse HEAD)
test "${actual_cbe_commit}" = "${expected_cbe_commit}" || {
  echo "LLVM-CBE source commit mismatch: ${actual_cbe_commit}" >&2
  exit 3
}
actual_cbe_patch_sha=$(sha256sum "${cbe_patch}" | awk '{print $1}')
test "${actual_cbe_patch_sha}" = "${expected_cbe_patch_sha}" || {
  echo "LLVM-CBE patch SHA-256 mismatch: ${actual_cbe_patch_sha}" >&2
  exit 3
}
if git -C "${cbe_root}" apply --reverse --check \
    "${cbe_patch}" >/dev/null 2>&1; then
  echo "LLVM_CBE_STC_PATCH=ALREADY_APPLIED"
else
  git -C "${cbe_root}" apply --check "${cbe_patch}"
  git -C "${cbe_root}" apply "${cbe_patch}"
fi
git -C "${cbe_root}" apply --reverse --check "${cbe_patch}"

echo "CLANG_SOURCE_COMMIT=${actual_commit}"
echo "CLANG_STC_PATCH_SHA256=${actual_patch_sha}"
echo "LLVM_CBE_COMMIT=${actual_cbe_commit}"
echo "LLVM_CBE_STC_PATCH_SHA256=${actual_cbe_patch_sha}"
echo "PREPARE_SOURCES=PASS"
