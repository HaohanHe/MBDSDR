# MBDSDR

MBDSDR 是一个开源的 **AI 定义无线电（AI-Defined Radio, ADR）**平台：在成熟的软件定义无线电（SDR）能力之上，构建一层由大模型驱动的工具认知引擎，把射频接收、解调、测量、卫星跟踪、综合定位授时等能力全部封装为可被 AI 调用的工具。人始终是决策中心，AI 承担频偏校正、找台、找干扰源、跳频识别、模式识别、数据录制等重复性工作。

名称由作者业余无线电呼号 **BI4MIB**（取 MB）、**BDS**（北斗卫星导航）与 **SDR**（软件定义无线电）组合而成。

## 核心理念

- **技能由对话触发，而不是固定按钮。** “找干扰源”“解析这段信号”“这颗卫星什么时候过顶”是模型在对话中编排的技能，传统 SDR 能力则是技能可调用的工具。
- **对外是 MCP，对内是 tool call。** 同一套能力既通过 Model Context Protocol 暴露给外部通用 Agent 与 AI IDE，也在自研内核内部以工具调用方式被模型编排。
- **传统 SDR 是工具，AI 是编排者。** 信号处理链路尽量复用成熟开源实现，自研重心在 AI 控制层、工具层与多源融合。
- **手自一体、能力可插拔。** 每一项自动化都保留手动通路；硬件、解调模块、定位源、Agent 均可插拔。

## 主要能力

- **AI 工具认知引擎**：自然语言意图到工具选择、参数填充、多步编排与结果汇总；上下文按真实 token 预算压缩，支持子代理与长任务。
- **SDR 全流程工具化（约 195 个工具）**：设备管理、频率/增益/带宽/解调控制、频谱分析与缩放平移、AM/FM/SSB/CW/FSK/PSK/QAM/OFDM 调制识别、摩尔斯电码、AX.25/APRS、FT8、SSTV、NOAA APT 气象云图、ADS-B、基带录制（WAV/CSV/IQ，可选采样率与位深）等。
- **多硬件抽象层（HAL）**：RTL-SDR 等接收棒、PlutoSDR/HackRF 等收发设备、串口电台与旋转器、带 SDR 模块的频谱仪/示波器类仪器、定向天线云台，统一枚举为“工具存在但当前是否可用”，未连接时向模型如实报告而不是伪装成功。
- **干扰源定位闭环**：全向天线先发现干扰，再引导切换八木等定向天线、由云台/旋转器逐方位扫描 RSSI，质心法估算方位并做镜像校验。
- **综合 PNT（新时空）多源融合**：GNSS、LEO-PNT、PPP-RTK、IMU、WiFi/蓝牙/UWB/蜂窝、NTP 授时等定位源以逆方差加权融合，单一源受扰时平滑降级；支持 RMC 解析、卫星过境预测、多普勒计算与射频天空图。
- **卫星与空间业务**：基于 SGP4 的过境预测、方位/仰角轨迹、多普勒频偏修正、APT/LRIT 等接收参数查询与离线处理编排。
- **受控自进化**：模型可在沙箱中提出并修改真实项目代码，经评估、快照提交后生效，支持一键回滚，防止幻觉导致不可用；默认关闭，需显式开启。
- **双端协同**：桌面端承担重处理与多窗口显示，移动端可作为传感器、指向器与远程指令终端；当需要人工架设在八木/抛物面天线上、切换巴伦或调整姿态时，AI 反向向人下发可执行指引。
- **原生桌面应用**：基于 Qt（PySide6）的桌面程序，频谱与天空图默认使用 QPainter 软件渲染，在 OpenGL 不可用的环境（如部分 Windows on ARM、Wayland 精简环境）可自动降级，不是浏览器套壳。

## 目录结构

```
mbdsdr_ai/               AI 内核、DSP 链路、工具实现与硬件抽象（约 44 个模块）
mbdsdr_ai_mcp_server.py  MCP stdio 服务器入口
mbdsdr_mcp_client.py     MCP 客户端示例
mbdsdr_sim_server.py     设备模拟器（无硬件时开发/测试）
desktop/                 PySide6 原生桌面端（频谱、天空图、AI 面板、控制面板）
experiments/             可复现实验脚本
tests/                   工具体检、冒烟与集成测试
ai-sdr-mini-kicad/       自研 ai-sdr-mini 前端硬件的 KiCad 工程、固件与连接表
requirements.txt         核心依赖（桌面依赖见 desktop/requirements.txt）
```

## 快速开始

需要 Python 3.10 及以上。

```bash
python -m pip install -r requirements.txt
```

无硬件时可先用设备模拟器验证完整链路：

```bash
python mbdsdr_sim_server.py
```

命令行直接调用工具（人类可读输出）：

```bash
python mbdsdr_ai_mcp_server.py --cli list_tools
python mbdsdr_ai_mcp_server.py --cli call_tool sdr_satellite_sky_view '{"latitude": 43.8868, "longitude": 125.3245}'
```

启动原生桌面端（模拟模式，无需 SDR）：

```bash
python -m pip install -r desktop/requirements.txt
python desktop/main.py --sim
```

## 作为 MCP 服务器接入通用 Agent

MBDSDR 通过标准输入输出提供 Model Context Protocol 服务，可被支持 MCP 的通用 Agent、AI IDE 与自动化框架调用。配置示例见 `mcp_server.example.json`，命令为：

```bash
python mbdsdr_ai_mcp_server.py
```

模型连接后应先调用 `list_tools` 发现能力；工具在设备未连接时会返回明确的“当前不可用”状态，模型据此决定是引导连接硬件还是改用模拟/离线通路。

## 可复现实验与测试

- `tests/tool_health_check.py`：全部工具无参批量体检，分类统计真实出参、缺参、需硬件、占位与崩溃。
- `tests/tool_param_smoke.py`：按工具 JSON Schema 自动构造参数后真实调用，覆盖需要传参的工具。
- `tests/smoke_live_tools.py`：核心链路一键冒烟。
- `experiments/exp_pnt_fusion.py`：综合 PNT 多源融合蒙特卡洛实验。
- `experiments/exp_ax25_performance.py`：AX.25/AFSK 解调在不同采样率与信噪比下的性能矩阵。
- `experiments/exp_weak_model_toolcall.py`：弱能力模型经工具认知引擎完成选择与参数填充的实测（需要自备模型 API）。

模型 API 密钥、地址与模型名通过本机配置文件设置，不写入仓库、不进入日志与提交。

## 自研前端硬件 ai-sdr-mini

`ai-sdr-mini-kicad/` 包含自研前端板的 KiCad 工程、物料清单、连接表与 Arduino 固件：集成主控、USB 扩展、收音/射频前端、IMU、磁力传感与 GNSS 模块，预留 Wi-Fi 与天线指向能力，可固定在八木或抛物面天线上参与方位测量与双端协同。该硬件为可选前端，MBDSDR 软件不依赖它即可配合通用 USB SDR 运行。

## 无线电合规提醒

接收与分析受当地无线电管理规定约束；任何发射功能仅可在执照许可的频率、功率与业务范围内，或在屏蔽环境与实验信号源上使用。默认配置面向接收、监听与离线分析。

## 开源协议

本项目以 GNU General Public License v3.0 开源，详见 [LICENSE](LICENSE)。所集成或借鉴的第三方开源组件，其权利与义务归各自权利人，相关声明单独整理。
