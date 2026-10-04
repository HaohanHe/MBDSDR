# 第三十阶段实现规格：Chromebook（ChromeOS/Crostini）适配收尾

> 基线：HEAD = e790612（已推送），ctest 123/123、flutter 331/analyze 0。
>
> 勘察事实：cpp/CMakeLists.txt 无 CMAKE_SYSTEM_PROCESSOR 分支；云端无 qemu-aarch64/aarch64 交叉工具链（`/usr/bin/qemu*` 空、dpkg qemu-user/gcc-aarch64 = 0，且系统目录只读禁 apt 安装）——arm64 构建验证只能做能力探测 + 如实记录卡点；docs/CHROMEOS_CROSTINI_SDR.md 137 行、9 处 PySide6/python desktop/main.py/rtl_selfcheck/--sim 过时引用（49e40bf 时代产物）。

## 1. 文档重写（独占 docs/CHROMEOS_CROSTINI_SDR.md）
- 部署主线改 C++ Qt6 时代：Crostini → USB 透传（VID 0bda/PID 2838）→ `sudo apt install qt6-base-dev librtlsdr-dev cmake` → cmake 构建 cpp/ → 运行 mbdsdr；
- ControlHub 远程通道一等公民：Chromebook 当远程控制端（浏览器/移动端 HTTP 127.0.0.1:50732 或局域网 IP），接收机跑桌面引擎，写清拓扑；
- rtl_tcp 兜底保留并升级：对接后端 host:port 配置，USB 透传不稳时用网络模式；
- 故障速查表更新（删 PySide6/--sim 过时项，补 C++ 构建常见失败）；
- ARM 单独一节：无验证现状、arm64 依赖清单、真机验证动作（uname -m→apt→cmake）、已知边界（librtlsdr 缺失自动 stub、高采样率驱动限制）——**如实标注"未实测"**。

## 2. CMake arm64 架构探测（独占 cpp/CMakeLists.txt，轻量）
- `CMAKE_SYSTEM_PROCESSOR` 分支：x86_64/arm64/其他 → 打印构建平台信息 + arm64 依赖检查提示；**不做架构专属代码路径**（保持通用）；现有 librtlsdr stub/Qt6 find_package 逻辑不动。

## 3. arm64 构建路径验证（B 块内做能力探测）
- 云端无 qemu/交叉工具链（已勘察）——如实记录"验证了什么/卡在哪/真机步骤"，写入文档与块1 ARM 节衔接；**禁止伪造构建通过**；若发现可用替代（如容器）再实跑并记录真实结果。

## 质量门 / 红线
- 干净室 MIT、不复制 GPL；通用非专用；无平台专属 ifdef 堆料；无假构建结果；无比赛字样；ctest 123 不回归、flutter 331 不回归（若动 mobile 则 analyze 0）；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、全量输出、未验证项。
