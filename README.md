# STCXX（STC++）

STCXX 是面向 STC 8051 / 251 微控制器的 C/C++ 工具链源码项目，目录名为 `stcxx`。它将修改后的 Clang、LLVM-CBE、Arduino 适配层与 SDCC 的 MCS-51 / MCS-251 后端组合，用于生成单片机固件。

C++ 支持处于实验阶段，采用 freestanding 运行环境。编译、链接成功只说明工具链完成了对应处理；具体芯片的启动、外设和烧录行为需要分别验证。

当前配套 arduino-stc51 的生产发布工作仅面向 Windows 和 macOS。WSL 工具保留为 Windows C++ 的必要依赖，Linux 独立宿主不纳入本次适配和验收；详见 [Arduino 平台说明](../arduino-stc51/README.md)。

## 项目分工

| 项目 | 负责内容 |
| --- | --- |
| **stcxx** | C/C++ 前端、目标 ABI、SDCC 编译器、汇编器、链接器和运行库 |
| [arduino-stc51](../arduino-stc51/README.md) | Arduino Core、芯片 variants、引脚/外设 API、板卡配置及固件编译配方 |
| [stc-cli](../stc-cli/README.md) | 固件地址/容量校验、串口 ISP、擦除与写入 |

建议三个项目并列放置：

```text
stc51/
├── stcxx/
├── arduino-stc51/
└── stc-cli/
```

底层可执行工具仍叫 `clang`、`llvm-cbe`、`sdcc`、`sdas8051`、`sdas251`、`sdld` 和 `sdldmcs251`。`stcxx` 是工具链项目名称。

## 编译流程与目标

```text
C 源文件 → SDCC → 汇编/链接 → HEX
C++ → patched Clang → LLVM IR → LLVM-CBE → C 适配/审计 → SDCC → 汇编/链接 → HEX
```

C++ 流水线核对 triple、数据布局和运行时 ABI，再将 LLVM-CBE 输出适配为 SDCC 可接受的 C。Clang 的 STC 配置负责生成 LLVM IR；最终指令选择和固件由 SDCC 及其配套工具完成。

| 执行目标 | SDCC 参数 | Clang IR triple | 数据布局 |
| --- | --- | --- | --- |
| MCS-51 | `-mmcs51` | `msp430-stc51-none-eabi` | 小端；16 位 `int` / `size_t`，24 位通用数据指针，16 位函数指针 |
| MCS-251 | `-mmcs251` | `msp430-stc-none-eabi` | 大端；16 位 `int`、32 位 `size_t`，24 位数据/函数指针 |

MCS-51 适用于 STC8、Ai8 等 8051 执行模式，MCS-251 适用于相应 STC32 执行模式。AI8051U 等双模式芯片必须让编译配置、链接地址和执行模式保持一致。具体 Flash 起点、容量和内存布局由 `arduino-stc51/variants/*/variant.json` 与板卡配置决定。

2026-09-13 起，配套 Arduino 平台仅保留 10 个 MCS251 型号及其固定执行目标，型号清单见 [devices.json](../arduino-stc51/tools/variants/devices.json)。本仓库继续提供通用 MCS-51 / MCS-251 编译后端，`stc-cli` 也独立保留其支持的 STC8、Ai8 和限定 STC16 烧录能力；三个项目的支持范围应分别理解。

MCS-251 使用专用 `sdldmcs251` 链接模式，支持项目中的扩展栈布局。两个目标的 ABI、运行库和目标文件不能混用。MCS-251 运行库提供 `small`、`small-stack-auto`、`large`、`large-stack-auto` 四组配置。

当前 MCS-251 源码将通用指针的字节读写直接生成为 `mov a,@dpx` / `mov @dpx,a`，保留完整的 24 位地址。旧目标文件所需的 `__gptrget` / `__gptrput` 运行库入口继续提供；`check-runtime-qemu` 中的 `runtime-main.c` 显式调用这些入口，验证高地址 RAM 读写和 Flash 读取。MCS-51 继续使用原有地址空间分派。源码更新与已安装的参考工具链分别记录在版本锁中，更新源码不会自动替换 `out`。

## 源码与版本

| 路径 | 用途 |
| --- | --- |
| `src/`、`device/`、`sdas/`、`support/` | SDCC、运行库、汇编/链接与配套工具 |
| `toolchain/llvm-project/`、`toolchain/llvm-cbe/` | LLVM / Clang 与 LLVM-CBE 子模块 |
| `arduino/patches/` | Clang、LLVM-CBE 和 SDCC 修改补丁 |
| `arduino/bridge/` | LLVM IR / C 审计和适配器 |
| `arduino/scripts/` | 源码准备、构建和发布入口 |
| `arduino/sources/` | 构建使用的 Clang / CMake 源码压缩包 |
| `arduino/toolchain-lock.json` | 源码、补丁、ABI 与参考产物身份 |
| `out/` | 生成的可移动 SDCC 工具包 |
| `doc/` | SDCC 与 MCS-251 文档 |

当前主要来源为 Clang **20.1.8**、LLVM-CBE 提交 **83f1bea**、SDCC 基线提交 **b09075b6**。完整版本与哈希以 [toolchain-lock.json](arduino/toolchain-lock.json) 为准。保留 `.git`、子模块、补丁和源码压缩包；构建脚本使用它们核对来源和准备补丁。

2026-09-09 对比上游并修复的代码生成、内存分配、运行库和重定位问题，见
[编译器审计记录](doc/mcs251/compiler-audit-20260909.md)。专用编译器、汇编器和诊断回归已恢复到构建检查流程。

## 构建与安装

完整流水线的现有脚本面向 Linux / WSL。Windows 用户应在 WSL 中执行以下 Bash 命令，生成的工具也是 Linux 可执行文件；其他宿主平台需要单独适配完整流水线。

需要 C/C++ 宿主编译器、GNU Make、Bison、Flex、Boost 开发头文件，以及 Git、Python 3、CMake、Ninja、patch、tar、xz、`sha256sum`。Ubuntu / Debian 的基础工具可这样准备：

```sh
sudo apt-get update
sudo apt-get install build-essential bison flex libboost-dev \
  libreadline-dev texinfo zlib1g-dev libzstd-dev \
  git python3 cmake ninja-build patch xz-utils
```

Clang / LLVM-CBE 阶段还需要适用于当前发行版的 **LLVM 20 开发环境**。脚本通过 `llvm-config-20 --cmakedir` 找到 LLVM。Arduino C++ 集成还调用 `llvm-link-20`、`opt-20` 和 `llvm-dis-20`。

```sh
llvm-config-20 --version
llvm-config-20 --cmakedir
```

以下以 `D:\Git\stc51\stcxx` 对应的 WSL 路径为例：

```sh
cd /mnt/d/Git/stc51/stcxx
git submodule update --init --recursive
export STC_TOOLCHAIN_BUILD_JOBS=4
bash arduino/scripts/build-wsl.sh /var/tmp/stcxx-build all
```

构建目录必须是**尚不存在的绝对路径**。脚本自动准备和核对源码，然后构建三个阶段。`STC_TOOLCHAIN_BUILD_JOBS` 控制 Clang / LLVM-CBE 并行度；当前 SDCC 阶段使用 `make -j1`。

另有用于 Linux 分发候选的独立前端构建入口。它直接读取受锁的 Clang/CMake
源码归档和 CBE 提交，在新目录中应用补丁并核对修改后源码，不改写子模块工作树：

```sh
python3 arduino/scripts/build-linux-frontend.py --build-root /var/tmp/stcxx-frontend --jobs 4
python3 -m unittest discover -s arduino/scripts -p 'test_*.py' -v
```

该入口要求 Linux x86_64、GCC/G++、LLVM **20.1.8** 开发环境、CMake **3.20+**、
Ninja、Git、patch 和 Python **3.9+**。可用 `--cmake /absolute/path/to/cmake`
选择独立构建工具。`SOURCE_DATE_EPOCH` 默认固定为 `1788134400`。
`build-report.json` 保存输入摘要、构建工具、命令、日志和成功产物的身份；源码
快照与日志保留在构建目录中。该入口生成待验证的 Clang/CBE 构建，分发依赖、
路径迁移、ABI 和实际目标运行仍须另外验收；使用此入口不自动替换 `out`。

`build-wsl.sh` 的主要产物入口为：

```text
/var/tmp/stcxx-build/clang/bin/clang
/var/tmp/stcxx-build/llvm-cbe/tools/llvm-cbe/llvm-cbe
/var/tmp/stcxx-build/sdcc/bin/sdcc
```

合法阶段为 `clang`、`llvm-cbe`、`sdcc` 和 `all`。只构建 SDCC 时仍会执行统一的源码准备与检查：

```sh
bash arduino/scripts/build-wsl.sh /var/tmp/stcxx-sdcc-build sdcc
```

将已构建的 SDCC 发布到新的输出目录：

```sh
bash arduino/scripts/publish-sdcc-out-wsl.sh \
  /var/tmp/stcxx-build/sdcc /mnt/d/Git/stc51/stcxx/out
```

发布目录必须尚不存在。已有 `out/` 可直接使用；重新发布可选择另一个新目录，并在集成配置中指定它。

工具包包含 `bin/` 入口、`libexec/` 编译器/预处理器、`share/sdcc/` 头文件/运行库和 `MANIFEST.sha256`。使用时保留整个目录，尤其是预处理器的 `cc1` 和目标运行库。

```sh
./out/bin/sdcc --version
./out/bin/sdcc --print-search-dirs
```

`out/` 发布的是 SDCC。完整 C++ 前端可使用以下独立打包入口；它要求同一构建环境中的
`ldd`、`readelf`、Debian 包数据库与版权文本，以及按来源校验过的 `patchelf`：

```sh
python3 arduino/scripts/package-linux-frontend.py \
  --build-root /var/tmp/stcxx-frontend \
  --output /var/tmp/stcxx-frontend-package \
  --patchelf /absolute/path/to/patchelf \
  --patchelf-sha256 "$VERIFIED_PATCHELF_SHA256"
python3 arduino/scripts/verify-linux-frontend.py \
  --package /var/tmp/stcxx-frontend-package \
  --output /var/tmp/stcxx-frontend-verification
```

输出目录必须尚不存在，`VERIFIED_PATCHELF_SHA256` 应来自已验证的工具来源。
打包器核对成功构建记录和完整源码快照，只修改复制后的工具；收集五个前端工具实际
使用的非 glibc 共享库、Clang 资源头文件与版权文本，并用相对 RPATH 查找包内依赖。
包中包含稳定的 `build-inputs/build-recipe.json`、`candidate-provenance.json` 和完整
`MANIFEST.sha256`。构建时间、进程号和并行日志摘要保留在输出目录旁的
`<目录名>.build-attestation.json`，通过构建配方和包清单摘要关联，避免把每次构建
必然变化的审计记录放进分发归档；该文件也拒绝覆盖。ELF 符号版本上限
设为 GLIBC 2.31 / GLIBCXX 3.4.28；超过上限或存在缺失依赖时拒绝通过。

验证入口检查完整清单、迁移后的依赖与资源目录，执行 MCS51/MCS251 的 O0、Oz
编译、链接、优化、IR 反汇编及 CBE 输出探针，同时检查指针宽度、成员指针、结构体
返回和不支持的直接机器码输出。还应在隐藏原构建目录、未安装 LLVM 的独立宿主根
目录中重新运行。此处 PASS 只覆盖前端，SDCC、Arduino 固件、外设真机及从源码独立
重建的可复现性仍须分别验收。Arduino 集成可通过 `STCXX_CPP_TOOLS_ROOT` 指定
包含 `bin/`、`lib/` 的完整前端目录，并使用与候选产物摘要一致的工具锁。

## 使用

### C 源文件

下面将用户自己的 `firmware.c` 编译为目标文件，链接时还需选择具体芯片的内存布局：

```sh
./out/bin/sdcc -mmcs51 --model-large -I./out/share/sdcc/include \
  -I./out/share/sdcc/include/mcs51 -c firmware.c -o firmware-mcs51.rel
./out/bin/sdcc -mmcs251 --model-large --stack-auto -I./out/share/sdcc/include \
  -I./out/share/sdcc/include/mcs51 -c firmware.c -o firmware-mcs251.rel
```

`--std=c17`、`--std=gnu11`、`--std=gnu17` 等语言模式由 SDCC 前端处理，GNU 模式仅支持已实现的扩展子集。构建脚本中的 `CFLAGS=-std=gnu17` 控制宿主编译器编译 SDCC 自身的方言。

发布包的头文件和运行库位于 `share/sdcc/`。独立使用时检查 `--print-search-dirs`，并按需要用 `-I` 与 `-L` 指定这些目录；例如 MCS-251 large / stack-auto 对应 `share/sdcc/lib/mcs251-large-stack-auto`。Arduino 适配层负责传入其所需路径。

### Arduino C++

板卡菜单、运行时、启动代码和 `.cpp` 编译入口位于 [arduino-stc51](../arduino-stc51/README.md)。当前配方使用 `gnu++11` 和实验性的 `cppcore=enabled` 配置，当前所有板项固定使用 MCS251 ABI；全部型号提供 12 MHz，部分型号另有已列出的时钟配置。

Arduino WSL 适配层支持以下环境变量。路径值使用 WSL 路径，并确保变量在启动流水线的环境中可见：

| 变量 | 用途 |
| --- | --- |
| `STCXX_TOOLCHAIN_ROOT` | STCXX 根目录；SDCC 默认从其 `out/bin/sdcc` 读取 |
| `STCXX_SDCC` | 显式指定 SDCC 包装入口 |
| `STCXX_CLANG`、`STCXX_LLVM_CBE` | 指定带 STC 补丁的 Clang、LLVM-CBE |
| `STCXX_LLVM_LINK`、`STCXX_OPT`、`STCXX_LLVM_DIS` | 指定 LLVM 20 配套命令 |
| `STCXX_WSL_DISTRO` | Windows 包装层使用的 WSL 发行版，默认 `Ubuntu` |

Arduino 侧另有 `tools/cpp-cli/toolchain-lock.json`，会校验工具、共享库和适配脚本的哈希。自行重建后，路径和产物身份均需与该配置核对；只修改路径不会改变哈希要求。

独立适配器可与实际 Arduino 构建的生成 C 进行逐字节比较。在生成这些产物的 Linux/WSL 环境运行以下命令；`--bridge` 可重复指定，每个目录应包含成功构建的 `manifest.json`、IR、原始 C 和存储清单：

```sh
python3 arduino/scripts/check-sdk-adapter-output.py \
  --bridge /path/to/arduino-build/stcxx \
  --output /path/to/new-comparison
```

该检查核对参考产物摘要，保留适配器、输入、输出及失败日志；输入被修改或输出不一致时失败。它只证明指定 MCS251 样例的输出一致，不能替代 MCS51 行为回归、历史源码恢复或固件运行验收。

独立 MCS51 指针回归使用实际 Clang、LLVM-CBE、SDCC 和 STC8G1K08A QEMU：

```sh
python3 arduino/scripts/check-mcs51-pointers.py \
  --frontend /path/to/stcxx-frontend \
  --sdcc-root /path/to/sdcc-package \
  --qemu /path/to/qemu-system-mcs51 \
  --output /path/to/new-pointer-regression
```

六组测试覆盖 O0/Oz 的跨 C/C++ 普通回调、函数指针返回、成员指针的多继承调整、空值和虚调用；每组核对 8 项运行结果。成员指针用例还改变前置代码长度，分别验证两种链接基址奇偶性。测试保留源码、工具、生成 C、汇编、最终地址和 UART 的摘要；不代表 Arduino 已支持 MCS51 或已完成真机验收。

自行连接独立适配器时，须将 `arduino/bridge/align-member-functions.py` 接入 SDCC 输出汇编与最终链接之间：先用 `align --local-parity even` 处理原始汇编，汇编并链接，再用 `verify` 检查重定位后的 `.rst`。只有退出码 3 才按原审计重试 `align --local-parity odd`，重新汇编、链接和验证；其他非零退出码必须停止。不能仅检查局部汇编地址：成员函数指针使用最低位区分虚调用，最终函数地址必须为偶数。上述回归脚本提供完整调用顺序。

MCS251 32 位乘法运行库有独立的源码验证入口：

```sh
python3 src/mcs251/tests/check-mullong-runtime.py \
  --toolchain /path/to/installed-sdcc \
  --qemu /path/to/qemu-system-mcs251 \
  --output /path/to/new-mullong-evidence
```

它显式编译 `device/lib/_mullong.c`，在 small/large、带/不带 stack-auto 的四种配置下分别链接并执行 617 组边界和固定随机输入，每组比较直接乘法及嵌套表达式。输入通过 volatile 变量传递，最终汇编必须实际调用运行库；完整 UART 必须为 `PASS\n`。增加 `--baseline-source /path/to/old/_mullong.c` 后，还会比较代码大小，并核对四种 MCS51 配置和 MCS251 small 非栈配置的 REL 是否逐字节不变；MCS51 部分是对象身份检查，不是运行测试。

检查 Linux 分发包内的实际乘法运行库：

```sh
python3 src/mcs251/tests/check-mullong-package.py \
  --toolchain /path/to/installed-sdcc \
  --qemu /path/to/qemu-system-mcs251 \
  --output /path/to/new-package-evidence
```

这个入口按编译器正常库搜索路径链接，核对 map 中实际选中的 `liblong.lib` 和 `_mullong.rel`，然后执行四种 MCS251 配置。增加 `--reference-toolchain /path/to/r4-sdcc` 可逐一检查归档成员：small 非栈配置保持相同，另外三种配置仅乘法对象变化。报告绑定完整包、辅助脚本、固件、链接图及原始输出；失败固件也保存摘要。

Linux r5 已通过包内四配置运行、故意错误运行库的拒绝检查，以及固定 Debian 环境中的独立源码重建，二进制归档逐字节一致。三份优化后的运行库已导入本地 `out`，编译器和链接器继续使用此前相同源码构建的原文件；来源锁明确记录这次运行库更新。Arduino SDK 补丁及工具锁同步为新候选，旧 Linux26 安装保留原基线。完整 Arduino 库矩阵、其他宿主平台和硬件发布验收仍须单独完成。

MCS251 的后续堆容量改进将初始化及唯一状态移至 `_heap_init.c`，`malloc.c` 按需链接；私有 `mcs251/heap.h` 约束三字节指针、六字节块头和三字节数据偏移。未调用分配函数的程序仍可初始化和检查堆，耗尽标志保持独立于空闲链表头。MCS51 及其他目标继续使用原实现。

```sh
python3 src/mcs251/tests/check-heap-split-runtime.py \
  --toolchain /path/to/sdcc --qemu /path/to/qemu-system-mcs251 \
  --baseline-source /path/to/preserved/malloc.c --output /new/heap-source-results
python3 src/mcs251/tests/check-heap-split-package.py \
  --toolchain /path/to/r6-sdcc --reference-toolchain /path/to/r5-sdcc \
  --qemu /path/to/qemu-system-mcs251 --output /new/heap-package-results
```

源码入口通过隔离归档比较四种 MCS251 内存/栈配置；指定旧源码时另核对四组 MCS51 对象。包级入口不添加源码、对象或库覆盖，验证正常查找路径、初始化独立链接、自定义堆、默认 1 KB 堆、耗尽后再次分配、calloc/realloc 数据保持及释放恢复，并逐成员比较运行库。所有结果绑定输入、链接图和实际 QEMU 输出；源码测试不代替分发包验收。

本地 r6 归档已通过上述四种配置的 16 个包级程序，并在固定离线 Debian 环境中从独立源码材料重建出逐字节相同的归档。默认 `out` 已导入四份 `libsdcc.lib`，Arduino SDK 的补丁、构建脚本和工具锁已同步；其他编译器及运行库文件保留原有来源记录。独立 Arduino 安装中的 8 个堆遥测/相序固件通过 QEMU 和离线校验；4 个 Stepper/SPI 容量用例均减少 1,064 字节，其中 AI34K16 / Stepper 仍超限。完整当前矩阵及硬件验收尚未完成。

Windows r6 的两次隔离交叉构建已得到逐字节相同的 13 个 PE 工具；Windows 发布脚本现包含六种 MCS51、四种 MCS251 配置的全部 60 份运行库。维护的 Windows 堆检查分为本机编译和 WSL 执行两个阶段。在原生 Windows PowerShell 中运行：

```powershell
python -O src/mcs251/tests/check-windows-heap-package.py compile `
  --toolchain 'D:/packages/sdcc-mcs251' --output 'D:/evidence/windows heap compile'
```

编译阶段要求完整 `MANIFEST.sha256`，核对实际链接的运行库及堆对象；包、源码、输出路径均可含空格。它检查 16 个 MCS251 固件和六种 MCS51 配置，成功状态仅为 `PASS_COMPILE`。随后在 WSL 中执行相同固件：

```sh
python3 -O src/mcs251/tests/check-windows-heap-package.py execute \
  --compiled '/mnt/d/evidence/windows heap compile/report.json' \
  --qemu /path/to/qemu-system-mcs251 --output '/mnt/d/evidence/windows heap execute'
python3 -O src/mcs251/tests/check-windows-heap-package-rejection.py \
  --compiled '/mnt/d/evidence/windows heap compile/report.json' \
  --qemu /path/to/qemu-system-mcs251 --output '/mnt/d/evidence/windows heap rejection'
```

两个阶段之间保留原编译目录、包和输入文件；Windows 驱动器默认映射至 WSL 的 `/mnt`，自定义挂载父目录可用 `--drive-root` 指定。执行阶段核对全部用例、输入、固件和链接图摘要，16 个 MCS251 程序均输出精确 `PASS\n` 才通过。反例入口用独立副本检查缺失/重复用例、固件或链接图修改、包及脚本身份变化，要求在启动 QEMU 前拒绝。每次使用新的输出目录，报告保留失败。MCS51 仅完成编译链接，huge 探针使用 C8051F120 PSBANK 声明；这些检查不代表其他芯片、完整 Arduino 矩阵或硬件验收。

`arduino/scripts/publish-windows-sdcc.py` 完成暂存后，用以下入口生成完整清单约束的 ZIP，并检查损坏输入及文件时间对归档的影响：

```sh
python3 -O arduino/scripts/archive-windows-sdcc.py \
  --package /path/to/staged/sdcc-mcs251 --output /new/windows-archive
python3 -O arduino/scripts/check-windows-archive.py \
  --package /path/to/staged/sdcc-mcs251 \
  --reference-archive /new/windows-archive/sdcc-mcs251-windows-x86_64.zip \
  --output /new/windows-archive-regression
```

归档入口固定文件顺序、UTC 时间及权限元数据，并回读核对完整清单、CRC 和每个文件摘要；拒绝不完整暂存、未登记文件、路径冲突及缺失模型。报告位于 ZIP 外，记录 Python/zlib 版本和输入身份。暂存来源记录保持原文，所以独立构建、暂存须使用相同的容器内路径和固定压缩环境，不能仅重复压缩同一目录来证明源码重建。

本地 r6 两组独立 Windows 工具、Linux 运行库产物已生成逐字节相同的 Windows ZIP：14,589,557 字节，SHA-256 为 `5b7cd96aa4c20152a95e90ca014ac368ee639e4a8fb43f0c1cc8dcf4aea07484`。归档在 Windows 实际解压后再次通过 16 个 MCS251 编译及 QEMU 程序和六种 MCS51 编译链接检查。对应 Windows 构建材料和依赖源码、完整 Arduino 矩阵、公开发布流程接入及硬件验收仍须完成；仓库原 Windows CI 发布流程尚未切换到此候选归档入口。

C++ 运行环境不包含异常、RTTI、线程局部存储或完整的宿主标准库。局部静态对象采用非线程安全初始化策略；变参、复杂聚合/位域及 weak/COMDAT 等边界以当前适配器和项目文档为准，不能从 Clang 接受语法推断完整固件支持。

### 固件校验与烧录

由 Arduino 或正确的芯片链接配置生成 HEX 后，交给 `stc-cli`：

```sh
stc-cli validate --expect STC8G1K08 --file firmware.hex
stc-cli flash --port /dev/ttyUSB0 --expect STC8G1K08 --file firmware.hex
```

型号、串口和固件应对应实际硬件。实验型号、无法读取型号 ID 的协议及执行模式选项见 [stc-cli 使用说明](../stc-cli/README.md)。工具链构建不执行硬件烧录。

## 文档与许可证

- [C/C++ 工具链设计和 ABI](ARDUINO_CPP_TOOLCHAIN.md)
- [MCS-251 后端](doc/mcs251/README.md)及[指令集覆盖](doc/mcs251/instruction-set.md)
- [GNU C / Zephyr 兼容性边界](doc/mcs251/zephyr-gnu-compatibility.md)
- [SDCC 简体中文手册](doc/zh-CN/README.md)
- [SDCC 原始文档与组件许可说明](doc/README.txt)

项目保留各上游组件的许可证。SDCC 相关说明见 [COPYING](COPYING)，LLVM / Clang 与 LLVM-CBE 分别遵循其源码目录中的许可证；具体文件和运行库中的许可声明应随分发保留。
