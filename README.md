# MBDSDR

**全栈开源的 AI 定义无线电（AI-Defined Radio）平台。**

MBDSDR 把软件定义无线电的全部能力封装为可被 AI 调用的标准工具：操作者用一句自然语言即可完成调谐、扫频、信号搜索、解调、基带录制与卫星指向，同时保留完整的专业手动操作。项目包含 AI 认知内核、跨平台桌面与移动端软件、可自制的接收硬件、ESP32 固件，以及 MCP 对外接口，让任意支持 MCP 的 AI IDE 或通用智能体都能把整套 SDR 当作工具调用。

> **命名**：**M**B 取自业余无线电台呼号 **BI4MIB**，**BDS** 为北斗卫星导航系统（BeiDou Navigation Satellite System），**SDR** 为软件定义无线电（Software Defined Radio）。

---

## 核心理念

- **人是中心，AI 承担重复劳动。** AI 不替代操作者的判断，而是把扫频找台、频偏校正、制式判别、参数调整、多步录制等繁琐流程自动化；关键动作仍由人确认。
- **能力是技能，不是写死的按钮。** 「找干扰源」「识别跳频」「核对中心频点」「解码 SSTV/FT8」等不是固定功能键，而是由对话触发、由模型编排底层 SDR 工具完成的技能；同一套工具可以组合出未预设的新任务。
- **AI 即工具，工具也服务 AI。** 全部 SDR 能力通过 [MCP（Model Context Protocol）](https://modelcontextprotocol.io/) 暴露。本软件既内置 AI 内核，也能作为 MCP 服务器被 Cursor、Claude Desktop、Cline、Kilo Code、OpenCode 等任意通用智能体调用。
- **可自编程、可回滚。** 作为开源软件，AI 可在沙箱内修改真实工程代码、生成新的解调或分析模块；所有改动带版本快照，可一键恢复，避免模型误改导致软件「变砖」。
- **跨平台、可降级。** 桌面（Windows / macOS / Linux，含 ARM）与移动端均可运行；图形渲染在 OpenGL 不可用时自动降级到 QPainter 软件渲染，不依赖单一图形栈。

---

## 功能特性

### AI 认知内核（`mbdsdr_ai/`，30 个模块）

- **128 个 MCP 工具**：接收机控制、频谱观测与缩放、信号搜索、基带录制、离线文件分析、解调、姿态与卫星指向、设备管理。
- **上下文管理**：按真实 token 预算压缩、上下文长度可配置、可查询模型上下文长度，参考 Kilo Code / Hermes / OpenCode 等通用智能体设计。
- **多模型、多供应商**：运行时拉取模型列表、切换模型、配置参数；不绑定特定厂商。建议先用能力较弱、成本较低的模型验证工具调用的健壮性，再迁移到更强模型。
- **可靠的工具调用闭环**：设备未插入、云台未连接等状态会作为工具可用性实时上报给模型，而不是抛出错误中断；针对「模型只输出 JSON 却不发起调用」「工具缺失」等弱模型常见问题做了专门处理。
- **记忆、子智能体、编排器、工作流、钩子（hooks）、插件系统**。
- **沙箱自编程**：危险调用拦截（`os`/`subprocess`/`socket`/`eval`/`exec`/文件写入）、守护者进程、版本快照与一键回滚。
- **多模态**：频谱图可截图送入视觉模型判读。

### SDR 接收与处理

- **多后端**：模拟后端（Mock，无硬件即可运行全部流程）、RTL-SDR、HackRF、USRP，以及自研 **ai-sdr Mini**（WebSocket JSON-RPC）。
- FM / AM / SSB 等解调，频谱分析与缩放，信号搜索，基带录制（WAV / CSV，采样率、位深、录制粒度可选），离线基带文件分析。
- 姿态、罗盘与卫星指向：IMU + 磁力计 + GNSS + 天文计算，配合天空视图辅助天线指向。

### 界面

- **Qt6 桌面端（PySide6）**：频谱部件、卫星天空视图、AI 对话与设置面板、状态与控制面板。
- **移动端网页**（`mbdsdr-mobile.html`）：手机与电脑双端协同，手机可作为指向/罗盘辅助与远程终端。
- 低饱和、扁平、高可读的设计语言，优先保证稳定性、可读性与操作效率。

### 配套硬件 ai-sdr Mini

- **ESP32-S3** 主控 + **SI4732** 窄带接收前端 + **USB Hub 外接通用 RTL-SDR dongle** 的异构双前端：窄带广播/对讲由 SI4732 处理，宽带接收交给外接 RTL-SDR。
- 板载 **BMI260 IMU + TMAG5273 磁力计 + ATGM336H 北斗/GNSS**，用于罗盘、姿态与卫星指向。
- USB 供电，**WebOTA 无线升级**，WebSocket JSON-RPC 与上位机通信。
- 提供 KiCad 工程、BOM、原理图连接表与生成/校验脚本，可在嘉立创直接投板贴片。

---

## 项目结构

```
MBDSDR/
├── mbdsdr_ai/                     # AI 认知内核
│   ├── agent.py                   #   智能体主循环
│   ├── context_manager.py         #   上下文压缩 / token 预算
│   ├── model_manager.py           #   模型列表 / 切换 / 多 provider
│   ├── tool_registry.py           #   工具注册与可靠调用
│   ├── sdr_tools.py               #   SDR 工具集（128 工具）
│   ├── sdr_backend.py             #   Mock/RTL-SDR/HackRF/USRP/ai-sdr Mini
│   ├── sandbox.py / guardian.py / version_store.py   # 沙箱自编程与回滚
│   ├── orchestrator.py / subagents.py / workflow_engine.py / hooks.py
│   ├── dsp.py / decoders.py / spectrum_processor.py  # 解调与频谱
│   ├── pose.py / astronomy.py     #   姿态、罗盘、卫星天空图
│   └── self_evolution.py / self_learning.py / memory.py / plugin_system.py
├── desktop/                       # Qt6(PySide6) 桌面端
│   ├── main.py                    #   入口（--sim 模拟 / --host 连真机）
│   ├── spectrum_widget.py         #   频谱（QPainter 软件渲染降级）
│   ├── rf_sky_view.py             #   卫星天空视图
│   ├── ai_panel.py                #   AI 对话与 API 设置
│   └── control_panel.py / status_panel.py / themes.py / mcp_worker.py
├── tests/                         # 集成测试与测试报告
├── ai-sdr-mini-kicad/             # 硬件：KiCad 工程 + Arduino 固件
│   ├── ai-sdr-mini.kicad_pcb      #   PCB 工程
│   ├── ai-sdr-mini.kicad_sch      #   原理图工程
│   ├── ai-sdr-mini.kicad_pro / .net / bom.xml
│   ├── ai_sdr_mini_firmware_v0.5_WebOTA.ino   # ESP32 固件（WebOTA）
│   ├── mbdsdr_protocol.h          #   上下位机通信协议
│   ├── BOM*.csv / 原理图连接表*.md / README-导入说明.md
│   └── generate_*.py / *_check.py / validate_*.py   # 工程生成与校验
├── mbdsdr_ai_mcp_server.py        # AI 内核 MCP stdio 服务器（128 工具）
├── mbdsdr_mcp_client.py           # ai-sdr Mini 硬件 MCP 桥接
├── mbdsdr_sim_server.py           # 无硬件时的设备模拟器
├── mbdsdr-mobile.html             # 移动端网页
├── mcp_server.example.json        # MCP 接入示例
└── 03~07-*.md、AI定义无线电-*.md    # 架构与设计文档
```

---

## 快速开始

环境要求：Python 3.10+。

```bash
# 1. 获取源码
git clone https://github.com/HaohanHe/MBDSDR.git
cd MBDSDR

# 2. 安装核心依赖（AI 内核 / MCP / 硬件桥接 / 模拟器）
python -m pip install -r requirements.txt

# 3a. 没有硬件时，先启动设备模拟器
python mbdsdr_sim_server.py --port 81
# 3b. 另开一个终端，列出硬件工具，验证全链路
python mbdsdr_mcp_client.py --host localhost --port 81 list_tools

# 4. 启动 AI 内核 MCP 服务器（stdio，供 AI IDE 接入）
python mbdsdr_ai_mcp_server.py
# 命令行自测
python mbdsdr_ai_mcp_server.py --cli list_tools
python mbdsdr_ai_mcp_server.py --cli call_tool sdr_set_frequency '{"frequency_hz": 98500000}'

# 5. 桌面图形界面（依赖较重，单独安装）
python -m pip install -r desktop/requirements.txt
cd desktop
python main.py --sim          # 模拟模式
python main.py --host 192.168.4.1 --port 81   # 连接真实 ai-sdr Mini
```

---

## 接入 AI IDE / 通用智能体（MCP）

MBDSDR AI 内核是标准 stdio MCP 服务器（JSON-RPC 2.0，每行一个 JSON）。在支持 MCP 的客户端中添加：

```json
{
  "mcpServers": {
    "mbdsdr": {
      "command": "python3",
      "args": ["/你的路径/MBDSDR/mbdsdr_ai_mcp_server.py"],
      "env": {
        "MBDSDR_API_BASE": "https://api.siliconflow.cn/v1",
        "MBDSDR_MODEL": "Qwen/Qwen3.6-35B-A3B"
      }
    }
  }
}
```

把 ai-sdr Mini 硬件能力接入 MCP（硬件需先联网，默认 IP `192.168.4.1`）：

```bash
python mbdsdr_mcp_client.py --mcp --host 192.168.4.1
```

暴露的工具按域分组：频谱感知（`spectrum_get_view` / `find_signals` / `analyze_range`）、接收机控制（`ui_set_frequency` / `ui_set_gain`）、多模态（`ui_screenshot` / `vision_inspect_spectrum`）、基带录制（`ui_start_recording` / `ui_stop_recording` / `baseband_analyze_file`）、设备状态（`get_status` / `ui_capabilities`）等。写操作带状态检查点，可回滚；工具返回 `ok=false` 时 MCP 置 `isError=true`，智能体可据此自纠或换路。

---

## 配置（LLM API）

密钥**只从本机读取，不写入代码、不进入仓库**。优先级：命令行参数 > 配置文件 > 环境变量 > 内置默认。

| 来源 / 变量 | 作用 |
|---|---|
| `~/.mbdsdr/config.json`（权限 `0600`） | 桌面端设置对话框写入，含 `api_key` / `api_base` / `model` |
| `MBDSDR_API_KEY` | LLM API 密钥 |
| `MBDSDR_API_BASE` | API 端点，默认 `https://api.siliconflow.cn/v1` |
| `MBDSDR_MODEL` | 模型名（示例 `Qwen/Qwen3.6-35B-A3B`，可换成任意兼容 OpenAI 接口的模型） |
| `MBDSDR_DESKTOP_URL` | 桌面本地服务地址，默认 `http://127.0.0.1:7878` |

---

## 硬件 ai-sdr Mini

| 部件 | 型号 | 说明 |
|---|---|---|
| 主控 | ESP32-S3-WROOM-1-N8R8 | WiFi、WebOTA、USB |
| USB Hub | USB2514B | 外接 RTL-SDR dongle 与 USB-A 口 |
| 窄带前端 | SI4732-A10-GSR | FM / AM / SW 接收 |
| 宽带接收 | 外接 RTL2832U + FC0012/R820T2 dongle | 经 USB Hub 接入，MCX 转 SMA |
| IMU | BMI260 | 6 轴姿态 |
| 磁力计 | TMAG5273A1 | 电子罗盘 / 指向 |
| 卫星定位 | ATGM336H-5NR32-G | 北斗/GNSS |
| 电源 | AMS1117-3.3 | USB 5V 转 3.3V |

完整位号、立创物料编号、接线与 PCB 布局规则以 `ai-sdr-mini-kicad/` 内的 BOM、原理图连接表为准。

**固件烧录**：Arduino IDE 安装 ESP32 开发板包，选择 ESP32S3 Dev Module，打开 `ai-sdr-mini-kicad/ai_sdr_mini_firmware_v0.5_WebOTA.ino`（同目录需有 `mbdsdr_protocol.h`），选端口首次烧录；此后可通过 WebOTA 无线更新，桌面或手机端即可完成升级。

---

## 测试

```bash
python -m pytest tests/ -q
```

当前集成测试覆盖 MCP stdio 全链路（initialize / tools/list / tools/call）、五种 SDR 后端、沙箱安全拦截与回滚、上下文管理与工具注册，共 185 项，结果见 `tests/test_report_v2.txt`。

---

## 路线图（规划中，尚未完成）

以下为明确的演进方向，属于在研技能而非已交付承诺：

- 干扰源自动测向：全向天线先探测、再引导八木天线与云台定向。
- VVVF 逆变器电磁拾音信号的录制与逆向分析。
- 6DOF 增强现实与 3DOF 罗盘指向，手机与电脑双端协同、AI 反向引导操作者架设/切换天线。
- 更多数字模式（FT8、SSTV 等）的自动识别与解码、跳频信号识别与参数提取、解调方案的自动检索。
- 基带录制格式与采样粒度的进一步扩展、多 USB 设备与虚拟网卡接入。

---

## 许可证

本项目以 **GNU General Public License v3.0（GPL-3.0）** 开源，完整协议见 [LICENSE](LICENSE)。欢迎在协议条款下使用、修改与分发。
