# 编译器审计：2026-09-09

## 对比基线与范围

本轮读取并对比 `gevico/sdcc-c251` 的最新 `main`
[`912a589d4080c9cd5c5c1faf871c62dd5023580d`](https://github.com/gevico/sdcc-c251/commit/912a589d4080c9cd5c5c1faf871c62dd5023580d)。
与项目使用的 `b09075b6a93e6afe10645181e3aeff041ea37f87` 相比，上游仅新增
`docs/index.html`，没有尚未合入的编译器、汇编器或运行库功能提交。

审计覆盖当前源码相对该基线的变化，并检查上游继承的实现：MCS251 代码生成、
24 位指针、对象布局、四种内存/栈模型、标准运行库、汇编重定位和 full-Flash
链接；共享代码同时检查 MCS51 非回归。不是仅比较版本号，也不是只增加容量配置。

## 已定位的问题与处理

| 问题 | 影响与处理 |
| --- | --- |
| 大对象预留被截成 16 位 | 65,536 字节 `__xdata` 数组生成 `.ds 0`；保留 MCS251 完整对象长度，检查 XSEG、XISEG/XINIT 和 RAM 越界。 |
| 取址再解引用丢失地址空间 | `*(&p[i])` 可被错误降为近地址读写，而直接 `p[i]` 正常；数组下标 lvalue 现在保留来源指针的输出类别和命名地址空间，供后续取址正确恢复 generic/code/xdata 指针。 |
| 指针差值中间结果固定为 16 位 | AST 和头文件已使用较宽 `ptrdiff_t`，但 iCode 仍用 `int`，导致大跨度正、负方向差值错误，并使 70,000 字节堆分配失败；中间结果改用同一 `newPtrDiffLink()` 类型规则。 |
| 混合宽度算术的临时存储重叠 | 24 位指针结果与 32 位偏移量共享 spill slot 时，大端字节布局使提前写入的结果覆盖尚未读取的输入；加减法按实际字节位置检测冲突，先算完再写回。普通小块 `malloc` 的 large 静态模型也受影响。 |
| 栈槽合并误读十六进制偏移 | 窥孔规则捕获偏移时丢失 `0x`，把 `003f`/`004f` 按十进制前缀读成 3/4，错误合并并读写不相邻的栈槽；修正规则保留完整数值前缀。在本次 `0x123456` 回归中，该继承缺陷使两种 stack-auto 模型的 `%p` 打印全零。 |
| 空格分隔汇编的寄存器读取被漏判 | `--peep-asm` 下，死值扫描只在找到 Tab 时查询寄存器读写，可能删除随后仍要读取的寄存器赋值；使空格与 Tab 采用一致的操作数扫描，避免把有效赋值误当死代码。 |
| 符号地址的常量偏移被截断 | `&array[70000]` 的重物化地址仅保留低 16 位；保留完整平坦地址 addend，静态门禁检查 `0x11170` 未变成 `0x1170`，运行测试另外覆盖大跨度指针差值的两个方向。 |
| DPX 缓存跨破坏点复用 | 间接调用或另一 far 对象访问后，读取同一固定地址可能访问错误位置；在建立完整寄存器破坏跟踪前，禁用这项缓存命中优化，每次完整加载 DPTR/DPXL。保留长符号操作数，其他优化仍启用。 |
| `%p` 只输出两个地址字节 | `0x123456` 被打印为 `0x1234`；MCS251 输出全部三个字节，其他目标的格式保持原有行为。 |
| 堆耗尽被误认作尚未初始化 | `malloc` / `realloc` 可能重新启用仍被占用的内存；独立记录初始化状态，覆盖耗尽、连续小块分配、合并重用、realloc 增长及失败时保留原块。 |
| 自定义 MCS251 heap 长度为 16 位 | 改用 32 位私有符号 `__sdcc_heap_size32`，避免大堆长度截断；不再静默接受旧的两字节堆长度定义。 |
| 短调用/跳转的重定位截断和过早范围判断 | 保留完整 24 位 addend，在最终地址上检查 2 KiB 页和 64 KiB 区域；不对位置未定的 CALL 做不安全缩短。 |
| indexed MOV 丢失符号位移 | 保留位移重定位并检查正负范围；同时补上 page-zero 符号操作数越界诊断。 |
| ABS/OVR 与段属性检查不完整 | 修复高地址 ABS+OVR 尺寸计算，拒绝同名段的冲突属性；保留链接器预建 SSEG 与编译器裸 SSEG 声明之间的既有兼容约定。 |
| 可容纳大段却报告空间不足 | 未显式固定的启动组作为连续整体参与分配重试；保留显式锚点与启动阶段顺序。 |
| 分段函数的跨 TU 身份与对齐 | 使用规范化源路径区分不同目录下的同名文件；同名 CSEG_F 段的每个非空贡献分别按两字节对齐，padding 计入容量。 |
| 运行库构建吞掉汇编失败 | 移除 MCS251 Makefile 的忽略错误前缀；用故意失败的汇编器检查两条构建路径确实失败。 |
| 上游专用回归被删除 | 恢复编译器、汇编器、诊断套件与检查入口；诊断失败现在返回非零，不再只写日志。 |

## 可重复验证

以已配置并构建的 `BUILD` 为例：

```sh
make -C BUILD/sdas/as251 check
make -C BUILD/src/mcs251 check
make -C BUILD/support/valdiag test-mcs251
python3 support/tests/compiler-audit/check-large-objects.py --sdcc BUILD/bin/sdcc
python3 support/tests/compiler-audit/check-section-identity.py --sdcc BUILD/bin/sdcc
python3 support/tests/compiler-audit/check-runtime-build-gate.py \
  --makefile BUILD/device/lib/mcs251/Makefile
make -C BUILD/src/mcs251 check-runtime-qemu QEMU_MCS251=/path/to/qemu-system-mcs251
make -C BUILD/src/mcs251 check-mcs51-qemu QEMU_MCS51=/path/to/qemu-system-mcs51
```

ELF 检查需要 `pyelftools`；CI 安装该依赖，避免把跳过当成已验证。
模拟器验证与外围设施检查分开：`check-runtime-qemu` 不要求 GDB 或全部 STC 外设模型。
`check-qemu` 仍保留这些额外检查入口。

## 最终本地验收结果

本轮最终编译器及十种模型的运行库已重新构建，并配套安装到 `stcxx/out`；
SDK 的堆提供者、链接前/后 ABI 检查、补丁镜像及候选工具链元数据同步更新。
下列结果绑定同一个最终编译器，不沿用中途候选的测试结论：

| 验证 | 结果 |
| --- | --- |
| 上游汇编器、代码生成、ELF 和新增静态回归 | 全部通过；ELF 检查实际执行，没有因缺少 pyelftools 跳过。 |
| 四种 MCS251 模型的诊断测试 | 2,469 项，0 失败。 |
| MCS251 默认必选 QEMU 运行矩阵 | 95 / 95，0 失败、0 跳过。 |
| MCS51 QEMU 非回归 | 22 / 22，0 失败、0 跳过；原基线汇编与 HEX 摘要不变。 |
| `%p` 四模型定向执行、十六进制栈槽与空格汇编门禁 | 全部通过；同时验证合法合并仍发生、不相邻栈槽不合并、活跃寄存器赋值不被删除。 |
| 大对象、规范化段身份、运行库错误传播、近地址写入、DR 重定位 | 全部通过，含预期拒绝的越界/构建失败用例。 |
| SDK 自定义堆 ABI | 两目标的真实堆对象分别验证 4 / 2 字节常量；24 项实际链接门禁正反例通过。 |
| K128 / K246 的 C 与 C++ full-Flash | 均完成编译、链接与 QEMU 执行；覆盖跨 HOME 常量、间接调用、构造/虚调用/成员函数指针与容量边界。 |
| K128 / K246 的 Arduino CLI 完整构建 | 两个干净安装目录均以 `cppcore=enabled,clock=12m` 编译并链接 Blink 成功，包含实际 SDK 堆提供者与最终链接 ABI 门禁；不是仅运行独立 C++ 探针。 |
| 源码及本地产物身份 | 38 项生产输入、255 项测试输入锁定；组合补丁正向/反向检查、发布目录全文件清单验证通过。 |

full-Flash 仍按实际映射布局使用：K128 为 `0xFE0000..0xFFFFFF`（128 KiB），
K246 为 `0xFC2800..0xFFFFFF`（246 KiB），两者 HOME 均在 `0xFF0000`。
并非把原普通程序窗口的起点保留不变、只增大长度。最终 C++ 压力镜像分别占用
103,579 / 227,889 字节代码空间，超过之前 64 / 182 KiB 窗口，且执行通过。

最终身份（SHA-256）：

- 编译器 ELF：`64c9a96111e94795841f0e05e4664d6c2c0d9eaa117a2ce996e1bc32cca3dda2`
- 源码锁：`127cbed44a13052360a403bde6495de9de08f0b6bb1614e1613681660105f595`
- 组合补丁：`fcb1342a77a412dbb8b0c8c6e8e4df5e1b59744790e63e40c32dd812c9472e12`

本轮原始本地证据曾保存在工作区 `.release-work/compiler-audit-20260909/`：
`final-build-checks/`、`mcs251-runtime-final.log`、
`mcs51-runtime-final/make-check-mcs51-qemu-final-binary-133346.log`、
`printf-pointer-final/`、`sdk-heap-final/`、`c-published-verified/`、
`cpp-published-verified/`、`arduino-final/` 及 `arduino-k128-rerun.log` /
`arduino-k246-rerun.log`。Arduino 首次测试被 PowerShell 5 的 stderr 重定向对
WSL 提示的处理打断；保留原日志，独立进程重跑后以退出码、产物及 ABI 门禁验收通过。
提交前按清理要求移除了该临时目录（包括 `previous-out/` 和
`pre-stack-fix-out/` 中的旧工具链副本）。正式回归测试、上述验收摘要和最终工具链
`stcxx/out` 保留；清理不等于再次运行测试，也不改变历史发布资格。
本报告与源码随三个项目的整理提交保存，没有发布远程工具包。

## 兼容性与尚未覆盖的边界

- 重新构建并配套使用编译器、汇编器、链接器及运行库。新重定位记录不能交给旧链接器。
- 使用自定义 MCS251 堆的程序应从本版 `_heap.c` 重新编译；旧的
  `__sdcc_heap_size` 两字节定义不能与新库混用。公共 `size_t`、数据指针和函数指针 ABI 不变。
- 超过 64 KiB 的 XDATA/堆测试验证的是编译器的平坦地址模型，不意味着具体 STC 芯片具有同等容量的 SRAM；实际链接仍须遵守该芯片的 RAM 边界。
- MCS51 的大跨度指针差值用例只计算数组内地址，不读写该大数组；所用 STC8G1K08A 模型只有 1 KiB XDATA，不能将这个地址算术诊断解释成大容量 RAM 运行测试。
- GNU 扩展仍是上游声明的子集；不是完整 GCC、Keil C251 或 OMF-251 兼容实现。
- Source/Binary 指令模式必须与硬件启动模式匹配；DSP32、TFPU 等外设不能从 CPU 指令集覆盖率推导支持程度。
- 本轮验证包含真实编译、链接和 QEMU 执行，不包含物理板卡烧录、时序或电气验证。
- 已下载的原生 Windows/macOS/Linux 工具包不会因源码变化自动升级；本地 WSL 候选与远程正式发布是两件事。
