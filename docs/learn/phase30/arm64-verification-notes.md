# Phase30 块2/块3：arm64 构建路径验证记录

> 范围：`cpp/CMakeLists.txt` 新增 `CMAKE_SYSTEM_PROCESSOR` 架构探测（块2）+ arm64 构建能力探测（块3）。
> 本文只记**本会话真实验证到的事实**；未验证项明确标注，不写"应该能跑"。
> 块1 独占文档 `docs/CHROMEOS_CROSTINI_SDR.md` 第 9 节已预留「验证记录（占位）」，本文是该占位对应的 B 块能力探测底稿；真机结果出现后应回填到块1 第 9 节。

## 0. 结论（先说人话）

- **云端无法实跑 arm64 构建**：本云环境是 x86_64，无 qemu-aarch64、无 aarch64 交叉工具链、无容器、无 arm binfmt，且系统目录只读、无法 `apt install` 补齐。
- **已真实验证**：①新架构探测块在 x86_64 host 上配置干净、打印正确；②探测分支逻辑在本机 cmake 3.22.1 上对 x86_64 / aarch64 / arm64 / 未知输入全部走对分支、零报错。
- **未验证（禁止当作已通过）**：arm64 真机上的 `cmake` 配置输出、arm64 产物编译、arm64 ctest、arm64 上 `qt6-base-dev`/`librtlsdr-dev` 是否齐全。

## 1. 云端环境探测（本会话实测命令与输出）

```text
$ uname -a
Linux vefaas-26kyfsh9-...-amd64 ... x86_64 x86_64 x86_64 GNU/Linux
$ uname -m
x86_64
$ cmake --version            -> cmake version 3.22.1
$ g++ --version              -> g++ (Ubuntu 11.4.0) 11.4.0
$ nproc                      -> 4
$ ls /usr/bin/qemu*          -> NO /usr/bin/qemu*
$ command -v qemu-aarch64 qemu-aarch64-static aarch64-linux-gnu-g++ aarch64-linux-gnu-gcc docker podman
                             -> 全部 (absent)
$ dpkg -l | grep -E 'qemu-user|gcc-aarch64|binutils-aarch64|cross'
                             -> 仅命中通用包 cmake；无 qemu-user / 无 aarch64 交叉链
$ ls /proc/sys/fs/binfmt_misc/ | grep -i arm
                             -> no arm binfmt
$ docker info / podman info  -> 无 daemon（docker/podman 均未安装）
$ test -w /usr               -> /usr READ-ONLY（有 sudo 但系统目录只读，无法 apt 安装补工具链）
```

Qt6 现状（x86 host）：`/home/user/Qt/6.8.2/gcc_64`（Qt 6.8.2，动态库）。
现有构建目录 `cpp/build` 的缓存：`RTLSDR_INCLUDE_DIR=NOTFOUND`、`RTLSDR_LIBRARY=NOTFOUND`（即 stub 形态），`CMAKE_BUILD_TYPE=Release`。

## 2. 新架构探测块（块2）—— 真实落点与验证

改动只在 `cpp/CMakeLists.txt`，**未碰** `src/` 代码、未改 librtlsdr stub / Qt6 `find_package` 逻辑、无架构专属源码路径。

落点：`cpp/CMakeLists.txt:43-64`（位于 librtlsdr fallback 探测之后、`set(SOURCES ...)` 之前）。
逻辑：`CMAKE_SYSTEM_PROCESSOR` 归一化 → `x86_64/AMD64→x86_64`、`aarch64/arm64/armv8l→arm64`、其余→`other`；只 `message(STATUS)` 打印，**不 `message(FATAL_ERROR)`、不改编译/链接规则**。arm64 分支额外打印两条依赖提示（`qt6-base-dev librtlsdr-dev cmake`）与 stub 兜底说明。

**验证 A —— x86 host 实配置（本会话 `cmake ..` 输出摘录）：**
```text
-- MBDSDR build platform: CMAKE_SYSTEM_PROCESSOR=x86_64 (x86_64)
-- RTL-SDR support: DISABLED (librtlsdr not found; stub compiled)
-- MBDSDR C++ build type: Release
-- Qt6 version: 6.8.2
-- Target: mbdsdr (dynamic Qt6 linking -- system Qt6 is shared)
```
即：x86 host 正确落到 x86_64 标签、**不**打印 arm64 提示；RTL-SDR stub 行为与改动前完全一致。

**验证 B —— 分支逻辑单测（本机 cmake 3.22.1，用独立片段复刻同一分支、逐输入喂值）：**
```text
x86_64  -> (x86_64)            （无 arm64 提示）
AMD64   -> (x86_64)            （无 arm64 提示）
aarch64 -> (arm64) + 两条 arm64 依赖提示
arm64   -> (arm64) + 两条 arm64 依赖提示
armv8l  -> (arm64) + 两条 arm64 依赖提示
loongarch64 -> (other (loongarch64))   （无 arm64 提示）
""      -> (other ())                  （无 arm64 提示，零报错）
```
所有输入 `cmake` 配置退出码均为 0，无 `CMake Error`。

> 注意一个真机上要盯的点：Linux 发行版（含 Debian/Ubuntu arm64）的 `CMAKE_SYSTEM_PROCESSOR` 打印的是 **`aarch64`**（GNU 三元组 CPU 名），而 Debian 包架构名叫 `arm64`。探测块已把 `aarch64` 归入 arm64 标签，真机配置时应看到 `CMAKE_SYSTEM_PROCESSOR=aarch64 (arm64)`——这是预期，不是异常。

## 3. 为什么云端跑不了 arm64（卡点明细）

要在 x86 云上跑 arm64，三条路都断：

| 路径 | 需要的东西 | 本环境 |
|---|---|---|
| 用户态模拟执行 | `qemu-user`/`qemu-aarch64-static` + arm 根文件系统 + binfmt | `/usr/bin/qemu*` 空、无 arm binfmt，且 `/usr` 只读装不了 |
| 交叉编译 | `gcc-aarch64-linux-gnu`/`binutils-aarch64` + arm64 Qt6/RTLSDR 开发包 | `aarch64-linux-gnu-g++` 缺失；无 arm64 版 Qt6 |
| 容器拉 arm64 镜像 | docker/podman + 多架构镜像支持 | docker/podman 均未安装、无 daemon |

补齐上述任一都需要 `sudo apt install`，而 `/usr` 只读——故**云端放弃实跑**，不伪造构建通过。

## 4. 真机 / 真机容器验证步骤（出现 arm64 机器时照此执行并回填）

与块1 `docs/CHROMEOS_CROSTINI_SDR.md` 第 9 节「真机验证动作」一致，另加 ctest 与探测块观察点：

```bash
uname -m                      # 期望 aarch64，确认容器真在 ARM 上
sudo apt update
sudo apt install -y git cmake build-essential pkg-config qt6-base-dev librtlsdr-dev rtl-sdr
cd MBDSDR/cpp
cmake -B build -S .
#   重点观察三行：
#   MBDSDR build platform: CMAKE_SYSTEM_PROCESSOR=aarch64 (arm64)
#   MBDSDR arm64 deps (native): sudo apt install qt6-base-dev librtlsdr-dev cmake
#   RTL-SDR support: ENABLED 或 DISABLED (librtlsdr not found; stub compiled)
cmake --build build -j"$(nproc)"
QT_QPA_PLATFORM=offscreen ctest --output-on-failure
./build/mbdsdr
```

回填项（填回块1 第 9 节「验证记录」占位）：实际 `uname -m`、`cmake` 完整输出、`cmake --build` 是否有架构相关报错、`ctest` 通过数、`mbdsdr` 启动与 USB/rtl_tcp 表现、arm64 仓库里 `qt6-base-dev`/`librtlsdr-dev` 是否齐全。

## 5. 已知边界（代码层面确定，非推测）

- `librtlsdr` 缺失不会让 arm64 构建失败：CMake 找不到库即编 no-RTL 软件 stub，产物可启动但打不开本机 USB 棒（与 x86_64 同逻辑，本次改动未改变它）。
- `cpp/CMakeLists.txt` 仍**无架构专属代码路径**——arm64 与 x86_64 走同一套构建逻辑；探测块只打印信息，不分支编译。
- 高采样率（~2.4Msps 以上）在 ARM 小核/USB 带宽紧张时可能丢包，属硬件/带宽问题，需真机观察，CMake 无法解决。

## 6. 本次未验证项清单（如实）

- [ ] arm64 真机 `cmake -B build -S .` 完整配置输出
- [ ] arm64 产物 `cmake --build` 全量编译（是否有架构相关告警/报错）
- [ ] arm64 上 `ctest` 全量通过数（x86_64 基线 123/123 见回归记录）
- [ ] arm64 Debian 仓库 `qt6-base-dev` / `librtlsdr-dev` 齐全性与版本
- [ ] arm64 上 `mbdsdr` 启动、USB 直收、rtl_tcp 取流实机表现
