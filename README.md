# MBDSDR — AI 定义无线电平台

MB = 呼号 BI4MIB ｜ BDS = 北斗 ｜ SDR = 软件无线电

把成熟开源 SDR 项目的信号处理能力工具化，外面由通用 AI agent 驱动：找台、找干扰、跳频识别、自动 SSTV / FT8 / ADS-B / RDS / NOAA 气象卫星解码等，都是对话触发的技能，而不是固定按钮。

- 协议：GPL-3.0
- 桌面端：原生 PySide6 + QPainter（非 Web 套壳）
- 移动端：Flutter 原生
- 对外 MCP、对内 tool call，能力可插拔

## 快速开始

```bash
pip install -r requirements.txt
# 桌面端（有控制台）
python desktop/main.py --sim        # 模拟模式
python desktop/main.py --host HOST # 连接硬件端
```

Windows 无控制台启动：双击 `desktop/run_gui.pyw`（不弹命令行黑框）。
打包成单文件可执行：`pyinstaller desktop/MBDSDR.spec`（console=False）。

MCP 服务：

```bash
python mbdsdr_ai_mcp_server.py     # stdio MCP
python -m mbdsdr_ai.mobile_server  # 手机端 WebSocket，端口 8765
```

## 目录

```
mbdsdr_ai/   AI 内核、工具注册、DSP 与各协议解码
desktop/     PySide6 桌面端
mobile/      Flutter 原生移动端
tests/       集成测试（python tests/test_full_integration.py）
docs/learn/  标杆开源项目源码学习笔记
```

## 数据真实性原则

- 没有接入真实设备时，界面只显示"未连接 / 无数据"，不显示任何看似正常的示例值。
- 模拟模式下，每个面板与读数都带 `[模拟]` 角标。
- 不预存地区性广播台——本地台用自动扫台（`sdr_fm_scan`）发现。

## 解码与信号处理

FT8 / FST4 / CW / SSTV / ADS-B / AX.25 / APRS / RDS / NOAA APT / METEOR LRPT / FM 立体声 / 星座 / 频谱。
算法参数对照真实开源实现，设计依据见 `docs/learn/`。

## 源码学习笔记

`docs/learn/` 下 10 份笔记，逐个项目 clone 后读源码整理（非 README 摘要）：
WSJT-X、SDR++、GNU Radio、SDRangel、direwolf、dump1090、rtl_433、noaa-apt/METEOR、Gpredict、GQRX。
每份标注真实常量与 `源文件:行号`，并对照本仓库对应模块说明。
