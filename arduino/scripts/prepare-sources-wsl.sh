#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "${script_dir}/../.." && pwd)
clang_root="${repo_root}/toolchain/llvm-project/clang"
clang_patch="${repo_root}/arduino/patches/clang-20.1.8-stcsdcc-ir-only.patch"

expected_llvm_commit=87f0227cb60147a26a1eeb4fb06e3b505e9c7261
expected_patch_sha=42a6a91ba0f8803c22d266862a5e34293929a142314511d760dfc51a177e6715

test -d "${clang_root}"
test -f "${clang_root}/lib/Basic/Targets/MSP430.cpp"
test -f "${clang_patch}"

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

if patch --dry-run --reverse --batch -d "${clang_root}" -p1 < "${clang_patch}" >/dev/null 2>&1; then
  echo "CLANG_STC_PATCH=ALREADY_APPLIED"
else
  patch --dry-run --batch -d "${clang_root}" -p1 < "${clang_patch}"
  patch --batch -d "${clang_root}" -p1 < "${clang_patch}"
fi
patch --dry-run --reverse --batch -d "${clang_root}" -p1 < "${clang_patch}" >/dev/null

echo "CLANG_SOURCE_COMMIT=${actual_commit}"
echo "CLANG_STC_PATCH_SHA256=${actual_patch_sha}"
echo "PREPARE_SOURCES=PASS"
