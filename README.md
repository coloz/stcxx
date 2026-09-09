# STCXX（STC++）

STCXX 是面向 STC 8051 / 251 微控制器的 C/C++ 工具链源码项目，目录名为 `stcxx`。它将修改后的 Clang、LLVM-CBE、Arduino 适配层与 SDCC 的 MCS-51 / MCS-251 后端组合，用于生成单片机固件。

C++ 支持处于实验阶段，采用 freestanding 运行环境。编译、链接成功只说明工具链完成了对应处理；具体芯片的启动、外设和烧录行为需要分别验证。

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

2026-09-06 起，配套平台已移除 `STC8A8K64S4A12` 和 `STC32F12K54`，当前支持范围为 20 个型号、23 个执行配置；`stc-cli` 同步移除这两款的型号和 magic ID 记录。移除依据见[型号生命周期核查](../arduino-stc51/docs/variant-lifecycle.md)。本仓库提供通用 MCS-51 / MCS-251 编译后端，没有这两款的专用型号配置；其余在用型号继续使用这些后端。

MCS-251 使用专用 `sdldmcs251` 链接模式，支持项目中的扩展栈布局。两个目标的 ABI、运行库和目标文件不能混用。MCS-251 运行库提供 `small`、`small-stack-auto`、`large`、`large-stack-auto` 四组配置。

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

主要入口为：

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

`out/` 发布的是 SDCC。Clang 和 LLVM-CBE 仍位于各自构建目录；Arduino C++ 使用它们时需保留其二进制及动态库依赖。

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

板卡菜单、运行时、启动代码和 `.cpp` 编译入口位于 [arduino-stc51](../arduino-stc51/README.md)。当前配方使用 `gnu++11` 和实验性的 `cppcore=enabled` / 12 MHz 配置，由 Core 选择 MCS-51 或 MCS-251 ABI。

Arduino WSL 适配层支持以下环境变量。路径值使用 WSL 路径，并确保变量在启动流水线的环境中可见：

| 变量 | 用途 |
| --- | --- |
| `STCXX_TOOLCHAIN_ROOT` | STCXX 根目录；SDCC 默认从其 `out/bin/sdcc` 读取 |
| `STCXX_SDCC` | 显式指定 SDCC 包装入口 |
| `STCXX_CLANG`、`STCXX_LLVM_CBE` | 指定带 STC 补丁的 Clang、LLVM-CBE |
| `STCXX_LLVM_LINK`、`STCXX_OPT`、`STCXX_LLVM_DIS` | 指定 LLVM 20 配套命令 |
| `STCXX_WSL_DISTRO` | Windows 包装层使用的 WSL 发行版，默认 `Ubuntu` |

Arduino 侧另有 `tools/cpp-cli/toolchain-lock.json`，会校验工具、共享库和适配脚本的哈希。自行重建后，路径和产物身份均需与该配置核对；只修改路径不会改变哈希要求。

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
