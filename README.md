# MBDSDR

AI 定义无线电平台 —— 给现有 SDR 开源项目植入 AI 脑机接口。

MB = 呼号 BI4MIB + BDS（北斗）+ SDR（软件定义无线电）。

## 这是什么

MBDSDR 不是又一个 SDR 应用，而是一个 **AI 控制层 + 工具化信号处理层**：

- **AI 是中心**：人用自然语言对话，AI 自主调度工具完成找台、找干扰、解调、卫星过境预测、自动 SSTV/FT8/ADS-B/RDS/APT 解码等任务。
- **能力全部工具化**：传统 SDR 能力（频谱、解调、解码、录制、轨道预测）都封装成可被模型调用的工具；对外走 MCP 协议，对内走 tool call。
- **成熟开源项目大量复用**：信号处理层不重复造轮子，集成 wsjtx/direwolf/dump1090/noaa-apt 等成熟开源实现。
- **自编程 + 快照回滚**：模型可在沙箱内改真实项目代码，一键快照回滚，社区 commit 需专家审查。

## 快速开始

```bash
# 依赖
pip install numpy scipy pyrtlsdr sounddevice

# 命令行：列出全部工具
python mbdsdr_ai_mcp_server.py --cli list_tools

# 调用一个工具
python mbdsdr_ai_mcp_server.py --cli call_tool sdr_spectrum_analyze '{"frequency_hz": 100000000}'

# 桌面端（仿真模式，无硬件也能跑）
python desktop/main.py --sim

# MCP stdio 服务（供外部通用 agent 接入）
python mbdsdr_ai_mcp_server.py
```

## 硬件

- **RTL-SDR（RTL2832U + R820T/FC0012）**：低成本接收，USB 直连。
- **自研板 ai-sdr**：ESP32-S3 + 射频前端 + IMU + GNSS，支持 Wi-Fi 回传与 OTA。
- **未来兼容**：HackRF / PlutoSDR（带发射）、嵌入式频谱仪、串口电台。

API key 从桌面端设置里配置，不硬编码。

## 文档

- 工具清单与调用示例：`docs/tools.md`
- 硬件接线表：`docs/wiring.md`
- 自进化与沙箱：`docs/evolution.md`

## 开源协议

GPL-3.0。第三方开源组件的使用声明见 `docs/open-source-notices.md`。
