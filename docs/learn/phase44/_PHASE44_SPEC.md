# 第四十四阶段实现规格：arm64 交叉编译真正做出来

> 基线：HEAD = 7d89c5a（已推送）。云端工具链闭环已由用户验证，**禁止再走"三路径全断"结论**。
> 资产（~/.local，不入库）：
> - 交叉工具链 `~/.local/arm-cross/arm-gnu-toolchain-13.3.rel1-x86_64-aarch64-none-linux-gnu/`（GCC 13.3，sysroot = `$TC/aarch64-none-linux-gnu/libc`，aarch64 glibc）
> - qemu `~/.local/arm-cross/usr/bin/qemu-aarch64`（静态 PIE；用法 `qemu-aarch64 -L <sysroot> <arm64程序>`）
> - 闭环已验证：hello world 交叉编译→qemu 退出码 42

## 块 1：CMake 工具链文件
- 写 `cpp/cmake/aarch64-linux-gnu.cmake`：CMAKE_C/CXX_COMPILER 指向工具链、CMAKE_FIND_ROOT_PATH=sysroot、CMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER / LIBRARY,INCLUDE=ONLY；
- `cmake -S cpp -B cpp/build-arm64 -DCMAKE_TOOLCHAIN_FILE=cpp/cmake/aarch64-linux-gnu.cmake` 验证配置（对照 cpp/CMakeLists.txt:43-64 CMAKE_SYSTEM_PROCESSOR 分支，应识别 aarch64）。

## 块 2：非 Qt 核心交叉编译 + qemu 测试
- 编译不依赖 Qt 的核心库/测试目标（DSP、协议解码、纯算法测试）；`qemu-aarch64 -L sysroot` 逐个跑，记录真实通过数（qemu 输出留证，不推测）。

## 块 3：arm64 Qt6 sysroot（攻坚）
- 从 Debian/Ubuntu arm64 仓库（deb.debian.org/debian-ports 或 ports.ubuntu.com）下载 qt6-base-dev 及依赖 .deb（ar x + tar xf data.tar 解压到 `~/.local/arm-cross/sysroot-qt/`），同方式取 librtlsdr arm64；
- CMAKE_PREFIX_PATH 指向 sysroot 交叉编译 Qt 目标（mbdsdr/UI 测试），qemu 跑 offscreen UI 测试（QT_QPA_PLATFORM=offscreen）；
- 下载/解压包数量与缺口如实记录；**Qt 凑不齐则非 Qt 真实结果先交付，Qt 缺口逐项列出（包名/缺失原因）**。

## 块 4：回填文档
- 更新 `docs/learn/phase30/arm64-verification-notes.md` 6 项未验证清单（配置输出/编译/ctest 通过数/依赖齐全性——逐项真实结果或剩余卡点）；
- CMake 工具链文件入库（可复用交付物）；更新 `docs/CHROMEOS_CROSTINI_SDR.md §9` 标注"云端已交叉编译验证"。

## 质量门 / 红线
- 8GB OOM：交叉编译 -j2（或单目标），如实记录；
- 测试结果必须真实（qemu 实跑日志全量，不推测"应该能过"）；诚实空态；无比赛字样；活动参数禁入；不预置 TLE；
- 工具链/qemu/sysroot 在 ~/.local 不入库（体积大），入库只有 toolchain file + 文档；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、arm64 测试实际通过数（qemu 输出全量）、Qt sysroot 真实状态、未解决项。
