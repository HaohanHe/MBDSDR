# MBDSDR — AI 定义无线电

> Multi-Band Defined Software Defined Radio
> 业余无线电呼号：BI4MIB
> 许可证：GPL-3.0

MBDSDR 是一个以 AI 为核心驱动的软件无线电平台，覆盖 SDR 接收、信号分析与解调、北斗/GNSS 定位、九轴 IMU 姿态感知、卫星轨道计算与射频天空视图。支持桌面端（PySide6）、移动端与 MCP 对外集成。

## 核心特性

- **AI 定义无线电**：智能体内核驱动调谐、扫频、制式识别、解调与决策，支持工具调用与上下文记忆。
- **多频段 SDR 接收**：兼容 RTL2832U 等主流 SDR 硬件，支持 FM/AM/SSB/CW 等模拟制式及 FT8/AX.25/SSTV 等数字制式。
- **北斗/GNSS 定位**：接入真实 GNSS 数据；无设备时如实显示"未连接/无数据"，绝不伪造定位。
- **九轴 IMU 姿态**：接入真实 IMU 传感器；无设备时如实显示"未连接/无数据"。
- **射频天空视图**：基于真实 celestrak TLE + sgp4 轨道计算的卫星实时位置与过境轨迹。
- **MCP 对外集成**：任意 AI IDE 可通过 MCP 协议直接控制 SDR 硬件。

## 目录结构

```
desktop/          PySide6 桌面客户端
mbdsdr_ai/        AI 内核 + DSP 信号处理
mobile/           移动端（手机传感器 GNSS/IMU 接入）
tests/            集成测试与单元测试
scripts/          辅助脚本
```

## 快速开始

### 依赖安装

```bash
pip install -r requirements.txt
pip install -r desktop/requirements.txt
```

### 桌面端启动

```bash
# 普通启动（带控制台日志）
cd desktop
python main.py

# 模拟模式（无硬件）
python main.py --sim

# 连接真实硬件
python main.py --host 192.168.4.1 --port 81
```

### 无控制台启动（普通用户）

- **Windows**：双击 `desktop/run_gui.bat`（使用 pythonw，不弹黑框）
- **Linux/Mac**：`./desktop/run_gui.sh`（后台启动）
- **跨平台**：`pythonw desktop/run_gui.pyw`

### 打包为独立可执行文件

```bash
# Windows
desktop/build_windows.bat

# Linux
./desktop/build_linux.sh
```

输出位于 `desktop/dist/MBDSDR/`。

## 数据真实性原则

MBDSDR 严格区分真实数据与模拟数据：

- **无硬件连接时**：所有传感器面板（GNSS、IMU）显示"未连接/无数据"，绝不显示示例值或零值伪装成正常读数。
- **模拟模式**：允许合成数据用于 UI 演示，但每个相关面板和读数都有醒目的"模拟"角标或前缀。
- **天空视图**：无观测站坐标（GNSS 未连接且未手动配置）时显示空状态，不绘制假卫星或假热力图。
- **卫星轨道**：使用真实 celestrak TLE 数据 + sgp4 算法，不使用硬编码占位 TLE。

## 配置

API Key 等敏感信息仅存储于 `~/.mbdsdr/config.json`（已被 .gitignore 覆盖），绝不硬编码或提交到仓库。

## 许可证

GPL-3.0
