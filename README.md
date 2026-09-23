# MBDSDR

**AI 定义无线电（AI-Defined Radio）**

MB = 呼号 BI4MIB，BDS = 北斗，SDR = 软件无线电。

给现有 SDR 开源项目"植入 AI 脑机接口"：人是中心，AI 自主感知、找台、选制式、指挥人转动天线或换巴伦，而不是固定按钮套壳。传统 SDR 能力全部工具化，对外经 MCP 被通用 agent 调用，对内由模型 tool call 驱动；模型可在沙箱里改真实项目代码、快照一键回滚。

## 架构

```
AI 认知层 (tool-use 引擎, 可流式对话)
   └─ MCP 工具层 (240+ 工具, 对外 MCP / 对内 tool call)
        └─ 信号处理层 (解调/解码/降噪/谱分析, 纯 numpy + 成熟开源算法)
             └─ 硬件抽象层 (RTL-SDR / SoapySDR / 自研 ai-sdr / 文件回放)
```

## 主要能力

- 模拟解调：AM / FM(窄带/广播) / USB / LSB / CW
- 数字制式：FT8 / FST4 / AFSK / SSTV / ADS-B / RDS / NOAA APT / APRS
- 新时空：卫星过境预报（TLE 联网拉取）、多普勒、天空图、GNSS 授时
- 纯 DSP 工具：频谱峰扫描找台、静噪门控、STFT 谱减降噪(ANR)、星座图数据、EVM、信号质量
- 自进化：模型在沙箱改代码、快照回滚、真实文件写入

## 入口

| 入口 | 命令 |
|---|---|
| MCP server (stdio) | `python mbdsdr_ai_mcp_server.py` |
| CLI | `python mbdsdr_sim_server.py --cli list_tools` |
| 桌面 (PySide6) | `python desktop/main.py --sim` |
| 集成测试 | `python3 tests/test_full_integration.py` |

## 配置

API key 不写入仓库，从设置 `~/.mbdsdr/config.json`（权限 600）配置：

```json
{ "api_key": "...", "base_url": "https://api.siliconflow.cn/v1", "model": "..." }
```

## 硬件

- 实验先用 RTL-SDR（RTL2832U）做接收闭环
- 自研板 **ai-sdr**（ESP32-S3 + IMU + GNSS + 磁力计，SMA 接口）

## 许可

GPL-3.0。第三方开源组件的声明见单独的开源软件使用协议文档。
