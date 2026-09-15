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
check_sha 310d5d53f3cf246ea34b18bad44a868cb7dcd8f55faab317f5505da30747ef2d "${sdcc_combined_patch}"
check_sha 7b94616120e68ea3ade6b21daf4d1da42d67672e624165e4482722fbf70f30fa "${repo_root}/src/mcs251/Makefile.in"
check_sha f8fda423712d808dd087d4e789b1e824911cde62d738078bf9325a898d8476c0 "${clang_patch}"
check_sha eb9687ce401a3f95c53c424f8af7c1c1a4ceca76be734812148ef18e6948fdf7 "${cbe_patch}"
check_sha b7a1b7b0af7b9c7596af6bd46e36d11321926eaa66a7a7dc957ab0a1375ee4b0 "${repo_root}/arduino/sources/clang-20.1.8.src.tar.xz"
check_sha 3319203cfd1172bbac50f06fa68e318af84dcb5d65353310c0586354069d6634 "${repo_root}/arduino/sources/cmake-20.1.8.src.tar.xz"
check_sha faf8c44e22c09037d29f9332941c6dcf8d8a4236148fd94020ca0264c5665ad5 "${repo_root}/arduino/bridge/audit_and_adapt.py"
check_sha b068f8b82be25672db548b322d6849a4d5ba51997a60015d878d11682d3a290c "${repo_root}/arduino/bridge/test_native_callback_abi.py"
check_sha 86d8218ed23b829e4a3be3ba4a9893e78a7d996142c494271893e77e0fb8432d "${repo_root}/arduino/bridge/adapt.py"
check_sha 2a54f89fc32801a33170375e3704f7ceffbcedd9c5fb7f2d2ba49a87a432adac "${repo_root}/arduino/bridge/align-member-functions.py"
check_sha 94f07c9821e14e7d57a5043d77c8894e87c8098b2afe1f7d0f5043a57d848f5c "${repo_root}/arduino/bridge/test_mcs51_pointers.py"
check_sha b41381ad0273cb938f489c0c567607d9c50c41cf2df360655c1dd412280e2b2e "${repo_root}/arduino/bridge/test_member_alignment.py"
check_sha ce4b11c3a40310b38e4461a5c66204c581f5868c58759597360e6606b5ab2d29 "${repo_root}/arduino/scripts/check-mcs51-pointers.py"
check_sha 44d9b17a5b92b816a99f24102cae00232b81c8d0c39dd880462083ef67b719dd "${repo_root}/arduino/bridge/fixtures/mcs51-pointers/members.cpp"
check_sha dc46bd49852002e4a9fc992863d9ba3f14eaea27b3d84a4b7d02249fe1cc0313 "${repo_root}/arduino/bridge/fixtures/mcs51-pointers/native.c"
check_sha c304295fe89c3f1f7680c8c5797c56bdcb7d5206a595bd9b94366007d4492f6f "${repo_root}/arduino/bridge/fixtures/mcs51-pointers/scalar.cpp"
check_sha d89d1a430676e178979310c856f7e331a6c18bb2f640d4afa9bef2810ba8aa50 "${repo_root}/arduino/scripts/build-wsl.sh"
check_sha f5ef46e92a1118224cacfe08d69b4a0e4a95d97ed5bc2677361b5c1cebcab710 "${repo_root}/arduino/scripts/prepare-sources-wsl.sh"
check_sha d0d88b0d94f1ac74a709c57115c67f89afe65e96e64c9b51efd83ddc40928cd6 "${repo_root}/arduino/wrappers/sdcc"
check_sha 8bf66efb6f3e56ff21354e8d7a26a7b701a8c3b19466ab2e3951bd2aaac16cf2 "${repo_root}/arduino/wrappers/sdcpp"
check_sha 973bde145e6c221a335c1996f126faf8bebb4a76c0047308788c3a3891d6edb7 "${repo_root}/arduino/scripts/test_tool_wrappers.py"
check_sha 6a2647c7f465cf06ebe77c5d578155c8702202c035476006ce26c6f91eb3c1d4 "${repo_root}/src/mcs251/tests/check-mullong-runtime.py"
check_sha 2f588794315a2a28fef3ca96bf63b2eb4fa48817a8cc32b4443fb14f0d65a373 "${repo_root}/src/mcs251/tests/check-heap-split-runtime.py"
check_sha ac328da4cfa8fceef8b3136ee64bf3b809d9ef2e334c95d7053165588d7ab738 "${repo_root}/arduino/scripts/vendor_sources.py"
bash -n "${repo_root}/arduino/wrappers/sdcc"
bash -n "${repo_root}/arduino/wrappers/sdcpp"

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
check_normalized_sha 8e9f6ce7faf7e3f9863dac4f0198332b68e95630c072dccc77e2b81463447613 "${repo_root}/toolchain/llvm-cbe/lib/Target/CBackend/CBackend.cpp"
check_normalized_sha 5609713403d6043ef43aa8ea7c5bf99b65abda094a4fb97411d458027cd68f01 "${repo_root}/toolchain/llvm-cbe/lib/Target/CBackend/CBackend.h"

git -C "${repo_root}" cat-file -e "${base_commit}^{commit}"
git -C "${repo_root}" apply --reverse --check "${sdcc_combined_patch}"
# Keep whitespace checks scoped to project-owned production inputs.
# Vendored upstream fixtures retain their exact bytes and are hashed below.
git -C "${repo_root}" diff --check -- \
  Makefile.in \
  arduino \
  src/mcs251/gen.c \
  src/mcs251/gen_lower.c.inc \
  src/SDCCpeeph.c \
  src/SDCClrange.c \
  src/SDCCast.c src/SDCCicode.c \
  src/SDCCglobl.h src/SDCCglue.c src/SDCCutil.c src/SDCCutil.h \
  src/mcs251/main.c sdas/as251/mcs251mch.c \
  sdas/linksrc/aslink.h sdas/linksrc/lkarea.c sdas/linksrc/lkdata.c \
  sdas/linksrc/lkmain.c \
  sdas/linksrc/lkmem.c
patched_blob=$(git -C "${repo_root}" hash-object src/mcs251/gen.c)
test "${patched_blob}" = d115d7f822ba71308dcdc11570302d0321d7996a || {
  echo "patched src/mcs251/gen.c blob mismatch: ${patched_blob}" >&2
  exit 3
}
patched_lower_blob=$(git -C "${repo_root}" hash-object src/mcs251/gen_lower.c.inc)
test "${patched_lower_blob}" = 8e6b2df1593ca8ffe76d72cfd8290158cf832128 || {
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
test "${lkmain_blob}" = 801cf817f23c7c3e5c49ba6bb6813847c76da7ed || {
  echo "patched sdas/linksrc/lkmain.c blob mismatch: ${lkmain_blob}" >&2
  exit 3
}
lkmem_blob=$(git -C "${repo_root}" hash-object sdas/linksrc/lkmem.c)
test "${lkmem_blob}" = fcb4cabe3a0a41074176e82daed4973b31b6fc0b || {
  echo "patched sdas/linksrc/lkmem.c blob mismatch: ${lkmem_blob}" >&2
  exit 3
}

# Pin the allocator, section emitters and assembler together.  A patch hunk
# reverse-check alone would not detect unrelated edits elsewhere in a file.
while read -r expected path; do
  actual=$(git -C "${repo_root}" hash-object "${path}")
  test "${actual}" = "${expected}" || {
    echo "patched ${path} blob mismatch: ${actual}" >&2
    exit 3
  }
done <<'BLOBS'
570a7f5650147f8b101157ee2b4ec02b8a7be274 device/lib/mcs251/heap.h
0dd3c49a5fc1cc6171d3c8a2033d554d6b89b83b device/lib/_heap_init.c
26bfcc08a1ab2f956bedaefef9e63d5d5866f40c device/lib/Makefile.in
789fa5c1822eded79e1cbee66b3cf22cb2a7d5df device/lib/_mullong.c
b4591f49bf63b7ffaa32a5abf87d0b0637b7bcc3 src/SDCCbtree.cc
b86ce6bc3ecce7c9bdba2ac12b546e6718e84388 device/lib/_modslonglong.c
9eb146c49ad5328c7d7bbd135e7408128f56590f device/lib/_divslonglong.c
c543267976b60eb622e5c67d79c58f42bcce664e device/lib/_modslong.c
33d1b6018a54bc766ab2185d0a389557b0ef64aa device/lib/_divslong.c
71ea8604b4d91ed8662b388ec1de6d666b020666 device/lib/_modsint.c
cba37d57dfb0cee1df8f38af6d0817fa60a0e867 device/lib/_divsint.c
6b1538adfa806e99be39f90aaeefdc124a7b0bc8 configure
ffdc0025da10fc9cbab5aced7090c92043820d41 configure.ac
a5f6f9423e499ea0496525457d4d8f55fab004de device/lib/_heap.c
bc6f24625f7ed0959bef5c2fa18b056cbaaf786f device/lib/malloc.c
12ee44afb7a42c6c1c46cf3936b3ab80215c80df device/lib/mcs251/Makefile.in
25aa5b6e6856d0ef9b51fe9b2ba217066f244fa8 device/lib/printf_large.c
a11359ab357008f551701758e32a38e75d18b306 device/lib/realloc.c
ee6cc46fa74cb74be4de4f73042e239a0681a95f Makefile.in
cbeb576649cabfa235753a813b2d648178ea9a0e sdas/as251/Makefile.in
cfca80fb5eea7ec8448161f7268a36208ad3e42d sdas/as251/mcs251adr.c
6887d35c9a9fef68d1b89829bb3288229411600f sdas/as251/mcs251mch.c
7a73984719e558b91a80a1fabc0559cec31e3cfa sdas/asxxsrc/asmain.c
90c9ed86de98d0a7920401365867b15d34ce45a3 sdas/asxxsrc/asout.c
0ba69f0f085331a6ae4c304e5fa45b76c2a1722d sdas/asxxsrc/asxxxx.h
ac34eacbf1f23af972304a29c39d14d3ac50bea2 sdas/linksrc/aslink.h
354b8affaed80bf2adb3c2c1215a0982396eed9a sdas/linksrc/lkarea.c
b7a42b6dec36d090795ec48585ecb8177caeeb99 sdas/linksrc/lkdata.c
801cf817f23c7c3e5c49ba6bb6813847c76da7ed sdas/linksrc/lkmain.c
fcb4cabe3a0a41074176e82daed4973b31b6fc0b sdas/linksrc/lkmem.c
68c5b11d3b27f264c31d97b48ca71ebf22b2bc8a sdas/linksrc/lkrloc3.c
f1370f1cd9b0b47851c3ac0cbc1b433f8ed637bc sdas/linksrc/Makefile.in
b2b88125933af4fa0d78c7534eec0fa1fbae40e1 sdas/linksrc/sdld.c
12bf896a08e8aaddb506c5b29924150b991462d6 sdas/linksrc/sdld.h
d115d7f822ba71308dcdc11570302d0321d7996a src/mcs251/gen.c
8e6b2df1593ca8ffe76d72cfd8290158cf832128 src/mcs251/gen_lower.c.inc
85b60f9dc09db1fef2644539f32329b5503453ff src/mcs251/main.c
9807964724593c3af433a04d1cd5e05b1f6108d1 src/mcs251/Makefile.in
afb0451327cdfb017dbac2d22f580da46cdd0fb7 src/mcs251/peep.c
1305232acf9156742195c8e1604c8130aec24a06 src/mcs251/peeph-mcs251.def
6ccb869b0ec48c9041299dc2c88ae69e61a43756 src/SDCCast.c
e7531b7eb1478450367472c5cc4f45bf25fa9156 src/SDCCglobl.h
274de8451a4e35c4574d133104b8e2145a3ff5f5 src/SDCCglue.c
290086c758d25b6f180350b52207f44a2d44460c src/SDCCicode.c
b14e27bc67892534c060ec2ea0dea79025ed0cab src/SDCClrange.c
b399be423c74460dbed8ed5acd27604c42b032d5 src/SDCCmain.c
74c37082698baca89bd8b2257b23e196387c124e src/SDCCpeeph.c
066ef682622a9b7b6a0e14c3bbf2164e04c79508 src/SDCCutil.c
0aae87361efa24414249d5132f38519080da4387 src/SDCCutil.h
BLOBS

# The restored baseline fixtures are also inputs to the audit, even when they
# need no patch hunk. Detect later fixture drift as well as production drift.
while read -r expected path; do
  actual=$(git -C "${repo_root}" hash-object "${path}")
  test "${actual}" = "${expected}" || {
    echo "fixture ${path} blob mismatch: ${actual}" >&2
    exit 3
  }
done <<'FIXTURES'
2c76de88a118322b05e77525877ce7dcc1df64d4 src/mcs251/tests/cppopt-check.py
e247f24c69caa1d7e5384894f6c92281813dc2b0 src/mcs251/tests/cppopt-assign-overlap.c
15f81afd4ddbed4156db50bd2f1e20b9feeb8966 src/mcs251/tests/cpp100-pointer-init.c
bb53e8af4ae4bf60be89eaa18424ad735cab6f45 src/mcs251/tests/cpp100-control-flow.c
58a89a5adcfaecb5539eb1060f6fc03e2b418adf src/mcs251/tests/cpp100-regressions.c
c75e8a6d3e8c284862bc1a6c6516134272064596 src/mcs251/tests/cpp100-check.py
1a743d6d9b7569a2557dcd58458d7494e26c2ed9 src/mcs251/tests/check-signed-divmod-runtime.py
7739cc256e11ebe2ef0b1c69750bdffd37b17129 sdas/as251/tests/binary-arithmetic-addressing.asm
6b7d49fbe691fff8951ef4434e865768770bf558 sdas/as251/tests/binary-arithmetic-addressing.expected
193edc6079e19dfe561ed05ac890558211395091 sdas/as251/tests/binary-bits.asm
fce0dc45ac58645ba81836e5cc8bd1d050dc3c2c sdas/as251/tests/binary-bits.expected
e2ed59c3351fd66b496cc7e878d31a6c18cc27ff sdas/as251/tests/binary-branch-boundaries.asm
3cbd304e2e3cd062ec49e79de90c902d0d30d59b sdas/as251/tests/binary-branch-boundaries.expected
67dd2a79b5b71417c41b5a2f9f04a051189e4301 sdas/as251/tests/binary-branch-jg.asm
5cccf3221f0bb7cf4703b2308b381a745886d5e5 sdas/as251/tests/binary-branch-jg.expected
a1b238a5a4286b7d517f8f56a0932c0e0c7a51bb sdas/as251/tests/binary-control-flow.asm
cb291bd361c70476e9dc02b1eb3effd0c0012f8e sdas/as251/tests/binary-control-flow.expected
fdbecd4e6653b228c979cc7a68a4b63df9e8cdd3 sdas/as251/tests/binary-inc-mul.asm
108dfbeaf7527e5485cf6e040411afdce3af553f sdas/as251/tests/binary-inc-mul.expected
81a67baba83ecb52d269c2773638ad76570949e7 sdas/as251/tests/binary-logic.asm
62ccabf698bc50f3eeef249bd926faf777cdef8a sdas/as251/tests/binary-logic.expected
480ced4998b7bd93e878eae641eccf530baa517a sdas/as251/tests/binary-move.asm
216838efb5c43f8041a7302ad14eff25442f52b2 sdas/as251/tests/binary-move.expected
d32a00b2c84aea2893e12caeb87cc2e7bf6fa69c sdas/as251/tests/binary-stack-system.asm
ecbae4bfa2a2c5bb037fa9f9820d27a32430ccd2 sdas/as251/tests/binary-stack-system.expected
e3d0e65132be16d9ce887185cd8e665e9a4e02fc sdas/as251/tests/check-audit.py
377813774da65279e4876f18452567977bd00485 sdas/as251/tests/check-coverage.sh
39d4e749e186b44b25a0931cbd59f59128580185 sdas/as251/tests/check-encoding.sh
cef9e3caa1f91ec87545eaa93c1d60b609a1737e sdas/as251/tests/check-errors.sh
65671f7e4e20a5f545e498c818185cfaa2600e65 sdas/as251/tests/check-instruction-forms.py
1d997e67057b73f3c5ae53742ba00f25987ad70f sdas/as251/tests/check-link.sh
70227c7859d00fe71befff00c52aa672956deb63 sdas/as251/tests/errors/acall-page.asm
c7fa7a1d795ba52a0b8e186af4326569c6b0c4ab sdas/as251/tests/errors/bit-number.asm
d8757c744d948bfe01ba84b269062d20e15afa8b sdas/as251/tests/errors/branch-range.asm
e3fcd8cf0666042e838385d0cb711b7275dcd386 sdas/as251/tests/errors/displacement-range.asm
ec8d21c6328ea7be9b6bf9bfa092cfb5dacb8138 sdas/as251/tests/errors/inc-step.asm
0dae418d6fd70cd6c74ecf9d71e52d086d233a25 sdas/as251/tests/errors/indirect-width.asm
28764a3aa97e82b98a9cfbb877cc533b5710fbbb sdas/as251/tests/errors/lcall-region.asm
f624d7546aadf6063204500043e05c3505d76d54 sdas/as251/tests/errors/push-range.asm
9594c1fe8f61c87ca51687c56d2045065f746981 sdas/as251/tests/errors/register-number.asm
0728bbba820d058b6d3b3c6c193555bec12175eb sdas/as251/tests/errors/register-width.asm
5710655677c0e6a50b1740631add48ce6d984509 sdas/as251/tests/errors/unsupported-width.asm
4610a134bc90eb94d2e78b6fa125c83ec35fb297 sdas/as251/tests/generate-instruction-forms.py
4a03fd676e1d4b9f1cc4291b09068b0a5a8c690b sdas/as251/tests/instruction-aliases.txt
41d66c8bc1c2c8b57583b473207e3b9c2f5dfc02 sdas/as251/tests/instruction-families.txt
609730576476957f44de52d1131ef6984d2bf6ab sdas/as251/tests/instruction-forms.tsv
6b74efd4b0dbcd46fc4fa427cc91a8b735de4409 sdas/as251/tests/link/branch-range.lk
eb731f019e80eb4457b0ed7b63e15f58838b8fbb sdas/as251/tests/link/cross-a.asm
42bc382ca60cea30e845740b9ee25426f8a084f9 sdas/as251/tests/link/cross-b.asm
1e49e0122a926d4dbb604d7e24b8236c334ebd88 sdas/as251/tests/link/cross-page.lk
2c41499133485a0625d002d9dbc61e0320f3e0a4 sdas/as251/tests/link/cross-region.lk
731d26ff8e6425aff5b31ae95b092e88040407dc sdas/as251/tests/link/link.lk
13521ec2465419918239b73a153f78efe34eef6e sdas/as251/tests/link/linked.expected.ihx
8368875516f991ee6dc51954fb1ce9237fc1b6bc sdas/as251/tests/link/page-a.asm
d15ca52b76c8e8208b3fd1c4def4431cbc0587a0 sdas/as251/tests/link/page-b.asm
9a651d0cae9b8f4aef8088810dbee7574ec2c1b9 sdas/as251/tests/link/reloc-a.asm
91fe0ff9ae1f93b331c74c192c982c4ba58a80bb sdas/as251/tests/link/reloc-b.asm
211364561e8caa480817426eaad0efa0c6c03ec2 sdas/as251/tests/mode-switch.asm
b42dc23cf64a5404e009d4f94640a7a059e25849 sdas/as251/tests/mode-switch.expected
d154a9d0291684cffec2c725889800cdb3794de8 sdas/as251/tests/README.md
82e7343af77303b321b30bceffaa637209bb1b20 sdas/as251/tests/source-arithmetic.asm
4739b8e006a7f7d28c0ef997406316a051272fad sdas/as251/tests/source-arithmetic.expected
52fac8c69ec2216fcaf4e9ed6142dfe3eb634f7a sdas/as251/tests/source-smoke.asm
60fbc2ae2b38fe97b708a0972e25085261be172a sdas/as251/tests/source-smoke.expected
148730e2eee65990dc2a268e3b99e249855039e0 src/mcs251/tests/abi-regression-runtime.c
95d7029ee70d5a6a8d619d1be9528e0d4a86f5e8 src/mcs251/tests/aggregate-initializer.c
52f94480f50e6e47435239b8f668dfec25ac716b src/mcs251/tests/aggregate-return.c
198bb014d89c68f17112f502cbf6e07eaca0b0f7 src/mcs251/tests/call-24bit.c
af97927f44659c617b050e98fd6c2048a65982d6 src/mcs251/tests/check-backend-isolation.py
19f44e78f35faadf3e4f8918d6863db0adc2efed src/mcs251/tests/check-c23-struct-redefinitions.py
c6283034bd3db9d84710d876fbb2cfe648c13233 src/mcs251/tests/check-codegen.sh
1210c3e904741ea30ba06ca7761e787c399f9905 src/mcs251/tests/check-compound-literal-codegen.py
6da424f83a86d57b454dad6cdd4acc94a1cb105e src/mcs251/tests/check-diagnostics.py
8ce113c716d0f91075035d35f0871eed44406337 src/mcs251/tests/check-driver.sh
15444bc1eb60c9d3605ff79ea60afc39ff459058 src/mcs251/tests/check-elf-output.py
f55c569f0dd77aef3c8101e9b78ba6c523f1ed39 src/mcs251/tests/check-endianness.py
e1c8caf3a0607306dcdba85458b9e97118ea66d5 src/mcs251/tests/check-gdbstub.py
5bd722c548025cc72a9ab44012184e49a1f6e0cd src/mcs251/tests/check-generic-address-roundtrip.py
1205e33a4f0d25b3958d063a9a323706d5e91801 src/mcs251/tests/check-gnu-extensions.py
917dc912187d479e2d180137c8bb254d26b8f623 src/mcs251/tests/check-language-modes.py
252f33f92226a69f821813106d36de7728a17073 src/mcs251/tests/check-mcs51-qemu.py
506529be4cb21595bfa214c2291c65e562eabaf3 src/mcs251/tests/check-native-isa-selection.py
79704c6bbeeb61196a7d99db2c4b3f9e39ccba57 src/mcs251/tests/check-qemu.py
507d69321be9e07d7c34b009536d36f33a8db524 src/mcs251/tests/check-register-allocation.py
eb8c7af4a54119aadbceb188510ddce5889e2ab3 src/mcs251/tests/check-stack-word-peephole.py
f6d1634ba5561169c6317487413314cef40cf405 src/mcs251/tests/check-static-inline.py
fdc7f6158086f454d29a0007b35c09371f1d89f8 src/mcs251/tests/check-target-macros.py
117d19221acd99c12f48553849a4a419be619926 src/mcs251/tests/check-wide-pointer-arithmetic.py
c4824fdce1d09074dfb235c028e5942599fa29e7 src/mcs251/tests/check-wide-shift-jump-table.py
302ab858c7fc5c8887578933238a26c4e6c24d1e src/mcs251/tests/compatible-block-extern-struct.c
9d75436ec0b8434cff9e8c493e84213cba00cff0 src/mcs251/tests/compatible-c23-struct.c
ddc7aa9963033d6616650934bfb284b85ae533b0 src/mcs251/tests/compatible-extern-array.c
9e3ae67eec1d840ada82d97b2f401e57d1dfd01b src/mcs251/tests/compiler-smoke.c
0454db0293c11a8a9cf58a10f18a14088a3923a8 src/mcs251/tests/compound-literal-argument.c
7eba45329569cf3b20ac590972a97919be304414 src/mcs251/tests/conditional-compound-literal.c
79f06bf097967ad6fbfed3a571bffc8d60dbb0ea src/mcs251/tests/crt0.asm
73e6dfd942e58d834de0a3cd4bc3ace71698b9a4 src/mcs251/tests/debug-smoke.c
06c108cf339288b07716f61cf6aa86b606afc5aa src/mcs251/tests/elf-output-probe.c
51e2278c605e06cfbc5a5a59cb3a66cb3a88b5db src/mcs251/tests/endianness-layout.c
c9dd4d1363aa53471de8f4eecd7e5d75f85b18d7 src/mcs251/tests/endianness-runtime.c
4e0715892c41037509a24c530a57645abbec4a09 src/mcs251/tests/far-callee.c
cf79d0e50986466c83b75e8431113ebe1e6baaf8 src/mcs251/tests/far-main.c
ee4303752573349d81fa5ea2d50c757fc5fa8dea src/mcs251/tests/far-symbol-dptr-cache-runtime.c
3f5d082227a6bce46447e778fcf3b187500d0f90 src/mcs251/tests/generic-address-roundtrip-runtime.c
01b2acbfb066a0faa053966ef1943c33d7713b46 src/mcs251/tests/gnu-attribute-invalid.c
f8313d76c638a261e7036fab5bb5b7f6471359b9 src/mcs251/tests/gnu-attributes.c
de438990623ff630b2e8cd40b8e7e85d8eca8791 src/mcs251/tests/gnu-auto-type.c
36621cb2d244f6f1cdb9c34bbb0e8f659df0945f src/mcs251/tests/gnu-auto-type-declarator.c
67016d7d90b0054d43e46f8c18c3ebc6d9808c13 src/mcs251/tests/gnu-auto-type-invalid.c
389cfb961de825a14dea89de52f47d60d305df1b src/mcs251/tests/gnu-auto-type-multiple.c
a56846f8c8e038d50cfe780c9953b27b6ea26831 src/mcs251/tests/gnu-bit-builtins.c
24ee2414a6de98b1aae5427037f5e9c17f23493d src/mcs251/tests/gnu-bit-builtins-invalid.c
a625afa45faeaebd3682ce7c8f5d4bf6ea4e3bc8 src/mcs251/tests/gnu-bit-builtins-runtime.c
f57dc0fb91e5477c741c1575221ca6b50801bdf7 src/mcs251/tests/gnu-builtin-constant-p.c
64f710dc57f4379a8fa8fa1938ca56f47dd84c08 src/mcs251/tests/gnu-builtin-constant-p-invalid.c
512b452730851c77b0089592e0743967bcf70c34 src/mcs251/tests/gnu-builtin-expect.c
f8b57615a9df5c2fe80e7895ac17afdf3c99456a src/mcs251/tests/gnu-builtin-expect-invalid.c
636aeecf6e2e6d7d482fab1a113c98482d2ffa20 src/mcs251/tests/gnu-byte-swap-builtins.c
2102e1458921ce936eb8628e301465ed000b6ba9 src/mcs251/tests/gnu-byte-swap-builtins-invalid.c
903c4c94cb8cabfa4366cea7543fa233a56ecc8a src/mcs251/tests/gnu-byte-swap-builtins-runtime.c
e60a86cfcdad4c1a2b819d4141234a02e4a62e55 src/mcs251/tests/gnu-case-ranges.c
3d531bfb10297955f4b75419b3a1bd39fcaf443e src/mcs251/tests/gnu-empty-aggregate.c
bd1ff5703b42e9c0e339977fe64504e348654460 src/mcs251/tests/gnu-empty-declaration.c
5c8fa58d9b8924ec207b7da3766dfb988e53720e src/mcs251/tests/gnu-extension-keyword.c
5a48f7f48286adafa40d153b898daa95a40ff1b2 src/mcs251/tests/gnu-has-builtin.c
59169accb53dadc60d0a79f3e63434473d8c533e src/mcs251/tests/gnu-keyword-aliases.c
d2b11d31d7da059d0f4099f3729cdefa010999c4 src/mcs251/tests/gnu-overflow-builtins.c
8f7fc0651d89099afc8c567ff7daf15065456c70 src/mcs251/tests/gnu-overflow-builtins-invalid.c
ec7ea3f7908593500649ebcf3631d928b4e6ff00 src/mcs251/tests/gnu-overflow-builtins-runtime.c
4e45642d0f2a68ec9013a50cdbff7d3179fde6dc src/mcs251/tests/gnu-statement-expressions.c
b685a7064a7b28c21aa22da0ffa7e15906f63d49 src/mcs251/tests/gnu-typed-overflow-builtins.c
fd69eeb247a1094b6fd524a69d706da15ba17ec9 src/mcs251/tests/gnu-typed-overflow-builtins-invalid.c
dd5291af84477b03bf75cb907cfe72ac79c96a3c src/mcs251/tests/gnu-typed-overflow-builtins-runtime.c
320f65215e72534d50c78fec8f7e34853775c0be src/mcs251/tests/gnu-types-compatible.c
e5843097b849ebc03ff159a11f6b9ab758dabaab src/mcs251/tests/gnu-types-compatible-invalid.c
a2cbd66976aa95209d0259c2f4540bbe73ea2c69 src/mcs251/tests/heap-size-abi-consumer.c
3728a1ffa77bac1b53797d440e30d388c8c6ec29 src/mcs251/tests/incompatible-c23-struct.c
77fb7b8279046e5d2858dea4f69c0f1148ac9ea8 src/mcs251/tests/incompatible-extern.c
cce4ef81966037909cf4667b3c6429a0aa59da0f src/mcs251/tests/incompatible-shadowed-struct.c
b7d9a48d2368f5015e2f152e368f5fe72dad5719 src/mcs251/tests/interrupt-vector.c
3e422eb4294a99c633fa96a7a1b11862f17acd6c src/mcs251/tests/invalid-wide-bitfields.c
f32874948061f51ba01f3f0c68d92f14841f3577 src/mcs251/tests/language-mode.c
17747534775047709bb611840c502ae07923db31 src/mcs251/tests/large-switch.c
2a0d0d083d3850e47c00623d2a54676d53aa3972 src/mcs251/tests/legacy-heap-object.c
4e296c4195e630bb122e5d5476735b0d9940dcaa src/mcs251/tests/longlong-runtime.c
53f7d946e85864ef6b886a8d1a46862b29d89e70 src/mcs251/tests/malloc-exhaustion-runtime.c
0c922a6ac66b197f8f510242c179a7f5cfe080eb src/mcs251/tests/mcs51-regression.c
8dd263d05bab5d9f11083039cd738e775bc4ad6e src/mcs251/tests/mcs51-regression.sha256
c0fa3ea63e2e9360e938d5d874b0734d63a10b4b src/mcs251/tests/memory-model.c
c34de39f562acb80712efea8525367440e0b7cee src/mcs251/tests/memory-model.h
b7bf334f116be7e81ccb9a4586c354d659042179 src/mcs251/tests/memory-model-runtime.c
b7ba6ca694d57c6e9f8f87fd04e300fa2d5ee255 src/mcs251/tests/mixed-width-arithmetic-runtime.c
b622fdaa1e321b4c8de4d7724c5d1ab51753ec33 src/mcs251/tests/native-extension.c
739566e9402207581d262b84e0a017ee6d6c1b92 src/mcs251/tests/native-optimization.c
0c79b7999e332195f9fc4a5684e2c7a6ae867d22 src/mcs251/tests/official/case-support.h
3fcf653ba84ef604343d3ee4c562471cc54d3f56 src/mcs251/tests/official/check-official-examples.py
28f9a69b2594f5eb07f2cfaffd637a65d3ba600b src/mcs251/tests/official/counter01.c
a69641e5956d594a860fcbf78599d1da367b8fce src/mcs251/tests/official/dsp32.c
3f2996c7c3e03f2a47dc42ee25c55a8b23cb5405 src/mcs251/tests/official/gpio-interrupt.c
c2872c9d6cf0fd93f3cc58fee6b618bdc61f15eb src/mcs251/tests/official/gpio-modes.c
bf43fcce3546fb2d7c6ffc428e979040a57a9cc7 src/mcs251/tests/official/memory.c
3ce992f44dd3be25679640899cdcc1f4b3f01dc4 src/mcs251/tests/official/README.md
e4d20ca60af8945ad7af728a43f9348d99e9eb35 src/mcs251/tests/official/rom-checksum.c
cc2b001a4ba47e05695a33a19bd3a949894fbc75 src/mcs251/tests/official/stc32g-qemu.h
595bb417728be5fb34580018cf11ada143168eb4 src/mcs251/tests/official/tfpu.c
c0a92b11f5f487a4873fc8e066806e84f8ffe13f src/mcs251/tests/official/timer01.c
52d198a576bfc7eb78fae507c344d60681635750 src/mcs251/tests/official/timer-mode2-gate.c
175be818f8afe7e78af799ff0cb02b43170e5831 src/mcs251/tests/official/uart1-echo.c
0ae310ca52fc96b3bce03d6367dd4422772ce4df src/mcs251/tests/optimization-runtime.c
4296dd580821561ceddbbf653f32671aba7c8bbe src/mcs251/tests/pcall-dptr-cache-runtime.c
adc19529f7026397937fd616673a7c1f2efa0fb0 src/mcs251/tests/pointer-difference-runtime.c
9391c7e4fe8ff11a606aa6c13dc57d9d284dd6a7 src/mcs251/tests/printf-pointer-runtime.c
8af1ca5a748e61ca291d1d958c37297c6927191e src/mcs251/tests/qemu_trace.py
1e9ee2a432437e4b081b9d3a4e10a23853e28b97 src/mcs251/tests/register-pressure.c
f68ad06cee64baa2fc82a40e649927f9e5d87fb0 src/mcs251/tests/rematerialized-address-offsets.c
269aae28c2748adc177fab1a21eb7310051716b3 src/mcs251/tests/runtime-main.c
d02054efc625e341e40dba3e0d539f7e5b8f55c1 src/mcs251/tests/setjmp-spx-runtime.c
97fb8b3710b16d9587c95a5d9aea486f1e8dd6b7 src/mcs251/tests/size-type-runtime.c
cb8212607d3a189a9e6cd51d46220bfa5ba288eb src/mcs251/tests/stack-word-peephole.c
4d6be5a53c09cbb6be4a37e3295ec8b5e65a2872 src/mcs251/tests/startup-memory.c
34c9ee1cd9d3c21f0ebad47e8caba207b57a63d1 src/mcs251/tests/statement-expression-runtime.c
bef640da970528354736d0b5ddd49aaacf47df61 src/mcs251/tests/static-inline-emission.c
5b18bef0fb36637789dd98dfbcbf1098ce1df7b6 support/regression/collate-results.py
c323bc37b989fa6422b5137a1853e764969a8291 support/regression/compact-results.py
40f241038a8e22217a8c02afdc9e83ff84cdf30a support/tests/check-mcs251-dr-immediate.py
ecea569da325281cd0f300ae408bcc988c7aeae3 support/tests/check-mcs251-near-store.py
c8602f7557394ebd008a9ba5da5f166c25cf376e support/tests/compiler-audit/check-large-objects.py
c01cf6bf13b4b01af4f075bb13cb4a0c1799edee support/tests/compiler-audit/check-runtime-build-gate.py
28c484fe9d6378fc68af7e7f5b8c9084923d1cab support/tests/compiler-audit/check-section-identity.py
eab9cbb28d452d150859350a53c80fd99c4a3706 support/tests/compiler-audit/large-objects.c
99aa271476af7d3c8561f9faebd1a8138c122a6a support/tests/full-flash/full-flash.c
7dbe331bf4be9ec4f20ce25cc93e647940415699 support/tests/full-flash/legacy-probe.c
55859b9d1d3385f11dc4055d8a57c7ddcf6ef545 support/tests/full-flash/README.md
610192801203194369f050bce7a3e6a14e2f565f support/tests/full-flash/run-full-flash.py
b4b505f5f02c636decb6b9061ee69089f75c4698 support/tests/mcs251-near-store.c
ca95555867b4cb3d07dc5500ac2a09b86c803249 support/tests/README.md
ae821ba34e82d9bc15c6eb6c87fccd1180ec8e35 support/valdiag/Makefile.in
31b8d495617126382075d91ddce69ca856cd6367 support/valdiag/tests/_Optional.c
483f0fbb79fe80d426f7cc9a9533e4d9b7afb068 support/valdiag/tests/_Optional-draft-2026-04-17.c
c1e9f32ec688de389a92b4d71faff1b451d842ab support/valdiag/tests/arraybounds.c
9591c88469ea1f93146bb95bfdefcde42b9f9519 support/valdiag/tests/bug-1952.c
e79d6c0051a015250741b505dfba59e9e427e0c8 support/valdiag/tests/bug-2240.c
12aa166f8d5bdabcd5e863aa079b0f4abda81fca support/valdiag/tests/bug-2773.c
5a2722a8ca6e77c683edd473c75a2f85adf3e143 support/valdiag/tests/bug-2798.c
eb0cfdff320017a4b758826f6f7e1d246033978b support/valdiag/tests/bug-2859.c
6eeb680c9a35fdb50b689f8ea785173107744fc8 support/valdiag/tests/bug-2940.c
d85429b2cce2b5054a9d2d9218b26fd3b21dfbc9 support/valdiag/tests/bug-2984.c
11a8ac80596b14c4ba247b05133fe88b52bc842e support/valdiag/tests/bug-3009.c
46d871674ad041c5e9dab7be1feb1a6c879d96b8 support/valdiag/tests/bug-3010.c
429a3b1f499bcbeee6669e43d66e364f735bab75 support/valdiag/tests/bug-3011.c
f1514c9e38acfc3efe94daa6244cb99d7d4e09f4 support/valdiag/tests/bug-3012.c
d45385dee4c51ef129c997a823c9772d31fbe161 support/valdiag/tests/bug-3014.c
a75a17701b08543595c1eb8be78ced765a8d304d support/valdiag/tests/bug-3031.c
0f06fd5d9ac43854532aa82876ea62c87d323e25 support/valdiag/tests/bug-3043.c
9fccd60177ee5e582284c2b8fa9c6e4ab5b055cd support/valdiag/tests/bug-3060.c
50c028a00f44385650c18a121fa2182aa3d21fc0 support/valdiag/tests/bug-3228.c
46afc41421b6011adc713f2043490f570dadeeb4 support/valdiag/tests/bug-3389.c
c6fcd6fb1f2d89a502d1339c6ce8a9e25c650c15 support/valdiag/tests/bug-3457.c
4043fa9a3df51b0bbd9e95db930e01a671a81b41 support/valdiag/tests/bug-3464.c
22b11c5a0106682e077d567bb7b958086323ff14 support/valdiag/tests/bug-3603.c
6159d650dd67df0f311d47c77f7de5288421d8bc support/valdiag/tests/bug-3773.c
b43cb4bca88d2d78cf3a69af03058e24e72a3eb3 support/valdiag/tests/bug-3791.c
650f920dde8bf48fa17c3675c2444a417a83523c support/valdiag/tests/bug-3801.c
6c37c631f411d04f862e1afdf6a74695de74f212 support/valdiag/tests/bug-3805.c
303bcf021ab54914e6509d02068ca8a126998857 support/valdiag/tests/bug-3889.c
f5c81ffb3f8d81cdac435dbe27944c9aa12d9bb7 support/valdiag/tests/bug-3890.c
1a129ab213c7716a7053da366abcef9ecf42813a support/valdiag/tests/bug-3891.c
57783de7da95ec1cbade60291dc2b133e3c806fe support/valdiag/tests/bug-3920.c
40219674ecadb57845f718bdc5e9a8882f212026 support/valdiag/tests/bug-3940.c
e33d9e5f044e9a40f1003106c2579fc9ff2793ca support/valdiag/tests/bug-3953.c
b12bba3cfb6050d467bb08927412f7a30b29fce3 support/valdiag/tests/bug-3964.c
c085090ac73d85a223cc8e263f1c6c88f5da1e22 support/valdiag/tests/bug-3969.c
0d3056fde23d83ea5434d3a50cc18a147c75c6ce support/valdiag/tests/bug-3970.c
f8dd77644b6861ec2d4e34bd1feb88838808663a support/valdiag/tests/bug-895992.c
27df1236dd5cd47c572b77464c7f3d03580fe3d3 support/valdiag/tests/bug-971834.c
32d7b49c35671f8a9308a4821aa525a13dcc0542 support/valdiag/tests/callingconvention.c
b584550114ae17f20aff8cbd4eee89e1a8899224 support/valdiag/tests/cflow.c
508208140bce671b03867ec64c3ed9c6c5adbf17 support/valdiag/tests/const.c
77631e0a770e5e1172c635e5c588b229a99b5bae support/valdiag/tests/constantRange.c
bb8bd5543001455dbcb69639462c68308e5d04ed support/valdiag/tests/conststringlit.c
51ce7ca5ecc21f3cc74afc43bdf0a5425d9a2927 support/valdiag/tests/declafterstmt.c
6558f1338dbcaef47861f606d2ff6f7b6f9f6b3d support/valdiag/tests/enum.c
58e85e54fc18eb46c483638dea40ade0072bf91f support/valdiag/tests/funcdec.c
f9e871fd05c75ae44b45625e11bcb8c459a2124b support/valdiag/tests/generic.c
39ecbd32dd3739873d71cab5b9207309d7077838 support/valdiag/tests/overflow.c
16d71b138a91e64b1e29a358a338eb38d20a10aa support/valdiag/tests/pointers.c
c6dac5c04fa39343ed1e0c24c27c6b43e8e61d94 support/valdiag/tests/primtypes.c
0c756acd08c604e617170e6c4f9b95c86bc8d37d support/valdiag/tests/register.c
a2080b3f2bddbba5244caf34b5a9ae22895b9476 support/valdiag/tests/restrict.c
01721eb0b1632663ba2911a4318c730a2f7ffce3 support/valdiag/tests/static_assert.c
342c0f1b4e8812f86f454c6778259a90810c311d support/valdiag/tests/strings.c
8a832e902453570b195bc4be548d3903f8e56774 support/valdiag/tests/struct.c
9f1c8e3db054670a4ce00593fcf12e94523ca1d7 support/valdiag/tests/structflexiblearray.c
5966440cf1470f64224e40775092f86a416c6d13 support/valdiag/tests/switch.c
8b8e6b7d5c5dc5fda03e9d8d9cc6743df681325e support/valdiag/tests/tentdecl.c
a24cbcc8bbb67717adc85031f960516ced4f49cc support/valdiag/tests/typedef.c
8d69ae5f85e7f0ceeda6ff340db3f27703026314 support/valdiag/tests/typed-enum.c
23fc5bdaeceab6c37cdf82a288d72f2046f75bba support/valdiag/tests/undefinedc23.c
855db75d8fb064b86983bb4cf82223d1e8b3cfbe support/valdiag/tests/universal.c
e690973af18cb1214fa299fb3aa6304daff645cd support/valdiag/tests/universal2.c
43b1aa8764523cb218670d61f081b492ad18a782 support/valdiag/tests/varg.c
63ed34642f055428a31e1c0f9540abfd5fa364f1 support/valdiag/valdiag.py
FIXTURES

for script in "${repo_root}"/arduino/scripts/*.sh; do
  bash -n "${script}"
done

python3 "${script_dir}/vendor_sources.py" --root "${repo_root}"
git -C "${repo_root}" apply --reverse --check \
  --directory=toolchain/llvm-project/clang "${clang_patch}"
git -C "${repo_root}" apply --reverse --check \
  --directory=toolchain/llvm-cbe "${cbe_patch}"
echo "SDCC_BASE_COMMIT=${base_commit}"
echo "SDCC_PATCHED_GEN_BLOB=${patched_blob}"
echo "SDCC_PATCHED_GEN_LOWER_BLOB=${patched_lower_blob}"
echo "SDCC_PATCHED_PEEPH_BLOB=${patched_peeph_blob}"
echo "SDCC_PATCHED_LRANGE_BLOB=${patched_lrange_blob}"
echo "SDCC_PATCHED_LKMAIN_BLOB=${lkmain_blob}"
echo "SDCC_PATCHED_LKMEM_BLOB=${lkmem_blob}"
check_sha 34dc1a1a8ff0908c6dba0932ca08962f04d9135958dfd3869256da4177b003c8 "${repo_root}/arduino/bridge/native-storage.py"
echo "SOURCE_LOCKS=PASS"
