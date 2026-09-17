# MBDSDR — AI 定义无线电全栈开源平台

**MBDSDR** = **MB** (呼号 BI4**MB**) + **BDS** (北斗卫星导航系统 BeiDou Navigation Satellite System) + **SDR** (软件定义无线电 Software Defined Radio)

> 全开源 GPL-3.0 | 呼号 BI4MIB | 硬件板名 **ai-sdr Mini**
> 目标：让大众人人都能用的国产化 AI 定义无线电软件。

---

## 这是什么

MBDSDR 是一个**全栈 AI 定义无线电（AI-Defined Radio, AIDR）参考平台**，从自研硬件到固件到桌面/移动软件到自进化框架，端到端开源。

核心设计理念：
- **人在回路**：人设定目标，AI 执行调控，AI 办不到时反向指挥人操作
- **自然语言优先**："找一个信号强的 FM 电台并录 30 秒" 替代手动调谐
- **SDR 功能工具化**：全部功能封装为 MCP 工具，任意 AI IDE（Cursor/Claude/VS Code）可直接调用硬件
- **能力可演进**：模型可在沙箱中改代码，用户可贡献新功能，一键恢复防幻觉变砖
- **不依赖 OpenGL**：多套 UI 方案并存，WOA/Wayland 等无 OpenGL 环境有降级

---

## 项目结构

```
MBDSDR/
├── ai-sdr-mini-kicad/              # 硬件工程 (KiCad 7)
│   ├── ai-sdr-mini.kicad_sch       # 原理图 (v0.7.1, 62 元件, 已修复 decoding-error)
│   ├── ai-sdr-mini.kicad_pcb       # PCB 布局参考 (80×60mm, 未布线)
│   ├── ai-sdr-mini.net              # 网表 (49 命名网络)
│   ├── ai-sdr-mini-原理图.pdf        # 原理图 PDF (可直接预览)
│   ├── BOM-MBDSDR-Mini-v0.7.1-最终下单版.xlsx  # BOM (19 关键件带立创编号)
│   ├── 原理图连接表-v0.7-匹配立创工程.md       # 逐引脚权威连接表
│   ├── README-导入说明.md            # 立创EDA 导入步骤
│   ├── ai_sdr_mini_firmware_v0.5_WebOTA.ino  # ESP32 固件 v0.6 (1163 行)
│   ├── mbdsdr_protocol.h            # MCP 协议头
│   ├── generate_schematic.py         # 数据驱动原理图生成器
│   ├── normalize_sch.py              # KiCad S-表达式规范化器 (去注释)
│   └── generate_pcb.py               # PCB 生成器
│
├── mbdsdr_mcp_client.py             # 电脑端 MCP 客户端 + stdio MCP 桥接器 (711 行, 硬件14工具)
├── mbdsdr_ai_mcp_server.py           # AI 内核 MCP stdio 服务器 (暴露128工具给任意通用agent)
├── mbdsdr_sim_server.py             # 硬件模拟服务器 (无硬件时测试 MCP 全链路)
├── MCP配置示例-Cursor-Claude.md      # Cursor / Claude Desktop MCP 配置指南
│
├── mbdsdr_ai/                        # AI 内核 v0.8.0 (30模块, 15482行, 128工具)
│   ├── __init__.py                   # 包入口 (MBDSDRAgent, AgentConfig)
│   ├── config.py                     # 配置管理
│   ├── context_manager.py            # 上下文管理 (Kilo Code/Mimo Code 借鉴)
│   ├── model_manager.py              # 模型管理 (硅基流动, 94个模型)
│   ├── tool_registry.py              # 工具注册中心
│   ├── agent.py                      # Agent 核心 (工具调用循环, Hook集成)
│   ├── memory.py                     # 记忆系统
│   ├── version_store.py              # 版本存储 (git-like 快照)
│   ├── sandbox.py                    # 沙箱执行 (子进程隔离)
│   ├── self_evolution.py             # 自进化引擎 (提出→验证→沙箱→评估→提交→回滚)
│   ├── guardian.py                   # 守护者 (修改前自动快照, 失败自动回滚)
│   ├── workflow_engine.py            # 工作流引擎 (6个预设工作流)
│   ├── scheduler.py                  # 定时任务调度器
│   ├── sdr_backend.py                # SDR 后端抽象 (Mock/RTL-SDR/HackRF/USRP/ai-sdr Mini)
│   ├── spectrum_processor.py         # 频谱处理 (FFT/峰值检测/亚bin精度/瀑布图/调制特征)
│   ├── sdr_tools.py                  # SDR 专用工具注册器 (38个工具)
│   ├── dsp.py                        # DSP 算法 (IQ校正/解调/AGC/基带录制/信号分析, 1024行)
│   ├── decoders.py                   # 解码器 (卫星轨道/NOAA APT/SSTV/跳频/FT8/APRS/ADS-B)
│   ├── hooks.py                      # Hook 事件系统 (44种事件类型)
│   ├── subagents.py                  # 子代理系统 (7种子代理类型)
│   ├── pose.py                       # 位姿融合 (6DOF/9DOF/倾斜补偿罗盘/AR投影)
│   ├── workflow_recorder.py          # 工作流录制/回放/参数化
│   ├── file_tracker.py               # 文件变更跟踪/回滚
│   ├── plugin_system.py              # 模块化插件系统 (7种插件类型)
│   ├── llm_judge.py                  # LLM-as-Judge (8维度评分)
│   ├── self_learning.py              # 自学习闭环 (记录经验→批量学习→获取建议)
│   ├── orchestrator.py               # 智能编排器 (依赖管理+拓扑排序+并行执行)
│   ├── code_editor.py                # 自编程核心 (源代码读取/修改/自动备份/热加载/git commit/回滚)
│   ├── astronomy.py                  # 天文计算 (Stellarium 借鉴: 坐标转换/时间系统/大气折射/天线参数)
│   └── amr.py                        # 自动调制识别 (KNN机器学习分类器, 24维特征, 45训练样本)
│
├── tests/                            # 测试
│   ├── test_full_integration.py      # 全面集成测试 v1
│   ├── test_full_integration_v2.py   # 全面集成测试 v2 (185项, 100%通过)
│   └── test_report_v2.txt            # 测试报告
│
├── desktop/                          # 桌面端 Qt6 UI (9文件, 3022行)
│   ├── main.py                       # 入口
│   ├── main_window.py                # 主窗口
│   ├── themes.py                     # 3套主题 (日式低饱和)
│   ├── spectrum_widget.py            # 频谱图 (OpenGL优先+QPainter降级)
│   ├── control_panel.py              # 控制面板
│   ├── status_panel.py               # 状态面板
│   ├── ai_panel.py                   # AI 对话面板 (调用 MBDSDRAgent)
│   ├── mcp_worker.py                 # MCP 工作线程
│   └── requirements.txt              # 依赖
│
├── paper/TCCN-ADR-Draft-v0.1.md       # 论文初稿 (8 章 + 参考文献)
├── MBDSDR-AI定义无线电白皮书-v1.0.docx  # 白皮书 (458 段, 12 表, 96 标题)
├── AI定义无线电-概念定义与框架-v2.1.md   # AIDR 形式化定义与理论框架 (68KB)
├── 03-智能体内核-多模态与可靠性增强.md     # 专题: 智能体内核
├── 04-基带录制与文件格式.md               # 专题: 基带录制
├── 05-IQ前端校正与接收链.md               # 专题: IQ 前端
├── 06-MCP对外集成-AI即工具.md             # 专题: MCP 集成
├── 07-卫星指向与AR系统.md                 # 专题: 卫星指向与 AR
│
├── hermes-self-evolution/             # 克隆: Hermes Agent 自进化框架
├── kilo-code/                          # 克隆: Kilo Code 上下文管理
├── mimo-code/                          # 克隆: Mimo Code 多模态 agent
└── opencode/                           # 克隆: OpenCode 开源 agent
```

---

## 硬件：ai-sdr Mini v0.7.1

80×60 mm 双层板，全 SMT（SMA 直插例外），62 个元件。

| 模块 | 芯片 | 功能 |
|---|---|---|
| 主控 | ESP32-S3-WROOM-1-N8R8 | 240MHz 双核, WiFi/BLE/USB/I2S/I2C/UART, 8MB Flash+8MB PSRAM |
| SDR 接收 | SI4732-A10-GSR | AM/FM/SW/LW/SSB, 144kHz–108MHz, I2C 控制 + I2S 数字音频 |
| 6 轴 IMU | BMI260 | 3 轴加速度 + 3 轴陀螺仪, I2C 0x68 |
| 3 轴磁力计 | TMAG5273A1QDBVT | 3 轴磁力计, I2C 0x35 |
| GPS/北斗 | ATGM336H-5NR32-G | 双模定位, UART1, PPS |
| USB Hub | USB2514B-AEZC-TR | USB 2.0 4 口, Type-C 上行 + ESP32 + USB-A 下行 |
| 电源 | AMS1117-3.3 | 5V→3.3V 稳压, 500mA 自恢复保险丝 |

**接口**：SMA（SDR 天线）、U.FL（GPS 天线）、Type-C（USB 上行/供电）、USB-A（Host 下行，可插 RTL-SDR 形成异构双前端）

**GPIO 映射**：IO4=I2S_BCLK, IO5=I2S_LRCLK, IO7=I2S_DIN, IO8=I2C_SCL, IO9=I2C_SDA, IO10=SI4732_INT, IO11=SI4732_RST, IO12=IMU_INT, IO13=MAG_INT, IO16=GPS_PPS, IO17/18=UART1, IO19/20=USB, IO21=HUB_RESET, IO1/2/3/46=LED

> **注意**：SI4732 输出解调后音频流（I2S），非原始 IQ 基带。原始 IQ 接收列为未来工作（需更换 ADC 直采方案）。

---

## 固件：ESP32-S3 v0.6

1163 行 Arduino 代码，运行在 ai-sdr Mini 上。

**功能**：
- WiFi AP 模式（SSID `MBDSDR-Mini`，密码 `mbdsdr123`，IP `192.168.4.1`）
- Web 服务器（端口 80）：远程调谐/状态查看/配置/Web OTA
- **WebSocket MCP 服务器**（端口 81）：JSON-RPC 2.0，14 个真实硬件工具
- I2S 音频流：SI4732 解调音频通过 WebSocket 流式传输到电脑端
- 9 轴传感器读取：BMI260 + TMAG5273，I2C 400kHz
- GPS NMEA 解析：ATGM336H，UART1 9600 baud
- OTA 升级：ArduinoOTA + Web OTA（浏览器上传 .bin，电脑/手机通用）
- 配置持久化：Preferences NVS

**14 个 MCP 工具**（每个都有真实硬件实现，非空壳）：

| 工具 | 功能 | 参数 |
|---|---|---|
| `list_tools` | MCP 工具发现 | 无 |
| `tune_fm` | 调谐 FM | freq_mhz (64-108) |
| `tune_am` | 调谐 AM/MW | freq_khz (531-1710) |
| `set_volume` | 设置音量 | volume (0-63) |
| `get_status` | SDR 状态 | 无 (mode/freq/rssi/snr) |
| `get_gps` | GPS/北斗定位 | 无 (fix/lat/lon/alt/sats/hdop) |
| `get_imu` | 9 轴姿态 | 无 (acc/gyr/mag/temp) |
| `start_record` | 开始 I2S 录音 | 无 |
| `stop_record` | 停止录音 | 无 (返回采样数) |
| `get_version` | 固件/硬件信息 | 无 |
| `check_update` | 检查更新 | 无 |
| `trigger_ota` | 触发 OTA | 无 |
| `web_ota_url` | OTA 页面 URL | 无 |
| `reboot` | 重启 ESP32 | 无 |

---

## 软件：电脑端 MCP 客户端

`mbdsdr_mcp_client.py`（711 行）是电脑端与 ai-sdr Mini 通信的桥梁。

**三种用法**：

### 1. 命令行直接调用

```bash
# 列出工具
python3 mbdsdr_mcp_client.py --host 192.168.4.1 list_tools

# 调谐 FM 98.5
python3 mbdsdr_mcp_client.py --host 192.168.4.1 tune_fm --freq 98.5

# 获取状态
python3 mbdsdr_mcp_client.py --host 192.168.4.1 get_status

# FM 扫频找最强电台
python3 mbdsdr_mcp_client.py --host 192.168.4.1 find_strongest_fm
```

### 2. 作为 stdio MCP server（供 Cursor/Claude/VS Code 调用）

```bash
python3 mbdsdr_mcp_client.py --mcp --host 192.168.4.1
```

在 Cursor 的 `.cursor/mcp.json` 或 Claude Desktop 的配置中添加此命令，AI 即可直接调用 ai-sdr Mini 的 14 个硬件工具。详见 `MCP配置示例-Cursor-Claude.md`。

### 3. 作为 Python 库

```python
from mbdsdr_mcp_client import MBDSDRClient

client = MBDSDRClient("192.168.4.1")
client.connect()
tools = client.list_tools()
result = client.tune_fm(98.5)
status = client.get_status()
client.close()
```

**额外便利方法**：`scan_fm(start, end, step)` 扫频、`find_strongest_fm()` 找最强电台。

---

## AI 内核：MBDSDR AI v0.8.0

`mbdsdr_ai/` 目录是 MBDSDR 的核心智能体，30 个模块，15482 行代码，128 个注册工具。

### 架构

```
用户自然语言指令
    ↓
MBDSDRAgent (agent.py)
    ├── ContextManager (上下文管理, Kilo Code/Mimo Code 借鉴)
    ├── ModelManager (模型管理, 硅基流动 94 个模型)
    ├── ToolRegistry (128 个工具, 30 个类别)
    ├── MemoryStore (记忆系统)
    ├── Guardian (守护者, 防幻觉变砖)
    ├── SelfEvolutionEngine (自进化引擎)
    ├── WorkflowEngine (6 个预设工作流)
    ├── Scheduler (定时任务)
    ├── HookManager (44 种事件类型)
    ├── SubagentManager (7 种子代理)
    ├── PoseFusion (6DOF/9DOF 位姿融合 + AR 投影)
    ├── LLMJudge (8 维度 LLM-as-Judge)
    ├── SelfLearningEngine (自学习闭环)
    ├── Orchestrator (智能编排器)
    ├── CodeEditor (自编程核心)
    ├── Astronomy (天文计算, Stellarium 借鉴)
    └── AMRClassifier (自动调制识别, KNN 机器学习)
```

### 128 个工具分类

| 类别 | 数量 | 代表工具 |
|---|---|---|
| meta | 6 | list_tools, context_status, model_status, list_models, switch_model |
| sdr_device | 5 | sdr_connect, sdr_disconnect, sdr_list_devices, sdr_switch_device, sdr_status |
| sdr_frequency | 4 | sdr_set_frequency, sdr_get_frequency, sdr_set_sample_rate, sdr_set_bandwidth |
| sdr_gain | 2 | sdr_set_gain, sdr_set_agc |
| sdr_demod | 4 | sdr_set_demod, sdr_set_squelch, sdr_set_volume, sdr_demodulate |
| sdr_spectrum | 7 | sdr_spectrum_analyze, sdr_spectrum_zoom, sdr_spectrum_pan, sdr_spectrum_screenshot, sdr_spectrum_find_signals, sdr_spectrum_center_offset, sdr_spectrum_text |
| sdr_record | 3 | sdr_record_start, sdr_record_stop, sdr_recordings_list |
| sdr_decode | 5 | sdr_decode_noaa_apt, sdr_decode_sstv, sdr_decode_ft8, sdr_decode_aprs, sdr_decode_adsb |
| sdr_satellite | 2 | sdr_satellite_sky_view, sdr_satellite_doppler |
| sdr_location | 2 | sdr_get_gps, sdr_get_imu |
| sdr_analysis | 5 | sdr_identify_modulation, sdr_detect_fhss, sdr_measure_signal, sdr_iq_correct, ... |
| sdr_ai | 2 | sdr_ai_sweep, sdr_ai_find_center |
| guardian | 3 | guardian_status, guardian_rollback, guardian_list |
| workflow | 3 | workflow_list, workflow_execute, workflow_trigger_match |
| scheduler | 6 | scheduler_status, scheduler_list, scheduler_add, scheduler_enable, scheduler_disable, scheduler_tick |
| memory | 2 | memory_write, memory_search |
| hook | 4 | hook_list, hook_trigger, hook_history, hook_stats |
| subagent | 4 | subagent_create, subagent_list, subagent_destroy, subagent_stats |
| pose | 5 | pose_update_imu, pose_update_gps, pose_get, pose_compass, pose_ar_project |
| workflow_recorder | 5 | workflow_record_start, workflow_record_stop, workflow_replay, workflow_list, workflow_delete |
| file_tracker | 5 | file_change_track, file_change_history, file_change_revert, file_change_stats, file_change_changelog |
| plugin | 6 | plugin_list, plugin_load, plugin_unload, plugin_discover, plugin_reload, plugin_stats |
| judge | 3 | judge_evaluate, judge_history, judge_stats |
| learning | 6 | learning_record, learning_learn, learning_suggestion, learning_experiences, learning_patterns, learning_stats |
| orchestrator | 6 | orchestrator_add_task, orchestrator_plan, orchestrator_execute, orchestrator_status, orchestrator_cancel, orchestrator_stats |
| code_editor | 10 | code_read_file, code_modify_file, code_modify_section, code_run_tests, code_hot_reload, code_git_commit, code_git_status, code_rollback, code_list_edits, code_stats |
| astronomy | 9 | astro_set_observer, astro_get_observer, astro_equatorial_to_altaz, astro_altaz_to_equatorial, astro_compute_refraction, astro_compute_airmass, astro_antenna_params, astro_pointing_guidance, astro_time_info |
| amr | 4 | amr_classify, amr_extract_features, amr_add_sample, amr_stats |
| self_evolution | 6 | evolution_propose, evolution_validate, evolution_evaluate, evolution_confirm, evolution_commit, evolution_apply |

### 全面集成测试

`tests/test_full_integration_v2.py` — 185 项测试，**100% 通过**。

覆盖：30 个模块导入、Agent 初始化、128 个工具注册、SDR 核心功能（连接/调谐/增益/解调/频谱/录制）、DSP（IQ 校正/5 种解调/AGC/抽取/SNR/带宽）、频谱处理（FFT/峰值检测/中心频点估计/调制特征提取/ASCII 频谱）、解码器（卫星轨道/多普勒/跳频）、Hook 系统、子代理、位姿融合（6DOF/9DOF/倾斜补偿罗盘/AR）、守护者（快照/回滚）、工作流、调度器、LLM-as-Judge、自学习、智能编排器、代码编辑器（自编程/自动备份/测试/回滚）、天文计算（坐标转换/时间系统/大气折射/天线参数/指向辅助）、AMR（KNN 分类/24 维特征/增量学习）、自进化引擎、插件系统、上下文/模型管理、记忆系统。

### AI 内核 MCP 服务器

`mbdsdr_ai_mcp_server.py` 将 AI 内核的 128 个工具暴露为标准 MCP 服务，任意通用 agent（Cursor/Claude Desktop/VS Code Copilot/AI IDE）都能直接调用。

```bash
# 作为 stdio MCP server
python3 mbdsdr_ai_mcp_server.py

# 命令行测试: 列出 128 个工具
python3 mbdsdr_ai_mcp_server.py --cli list_tools

# 命令行测试: 调用工具
python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_set_frequency '{"frequency_hz": 98500000}'
python3 mbdsdr_ai_mcp_server.py --cli call_tool sdr_spectrum_analyze '{"fft_size": 512}'
```

在 Cursor 的 `.cursor/mcp.json` 中添加：
```json
{
  "mcpServers": {
    "mbdsdr-ai": {
      "command": "python3",
      "args": ["/path/to/mbdsdr_ai_mcp_server.py"],
      "env": {
        "MBDSDR_API_KEY": "sk-xxx"
      }
    }
  }
}
```

---

## 无硬件测试：模拟服务器

`mbdsdr_sim_server.py` 模拟 ai-sdr Mini 的全部 14 个工具，硬件回来前即可测试 MCP 全链路。

```bash
# 终端 1: 启动模拟服务器
python3 mbdsdr_sim_server.py --port 81

# 终端 2: 用 MCP 客户端连接
python3 mbdsdr_mcp_client.py --host localhost --port 81 list_tools
python3 mbdsdr_mcp_client.py --host localhost --port 81 tune_fm --freq 98.5
python3 mbdsdr_mcp_client.py --host localhost --port 81 get_status
```

模拟数据：RSSI 随频率变化（FM 广播段有模拟强台）、GPS 固定在北京、IMU 随机但合理、录音返回递增采样数。

**端到端测试已通过**：list_tools/tune_fm/get_status/get_gps/get_imu/start_record/stop_record/get_version 全部返回正确数据。

---

## AI 定义无线电（AIDR）的工作定义

> AIDR 目前尚无统一定义，本文提出的是一个可操作的工作定义，而非"首次定义"。

**AI 定义无线电**是一种以**人在回路**为前提、以**自然语言**为首要交互接口、以**大语言模型 + 工具调用协议（MCP）**为决策与执行引擎、以**SDR 功能工具化与可编程性**为基础、具备**AI 辅助自主调控能力**与**能力可演进性**的无线电系统。

**五大核心特征**：
1. **人在回路**：人设定目标与最终决策，AI 是智能助手而非自动决策者
2. **AI 辅助自主调控**：频偏校正、中心频点核对、跳频识别、自动找台等
3. **自然语言优先交互**：极短自然语言指令驱动，传统 GUI 作降级
4. **SDR 功能工具化与可编程性**：全部功能封装为 MCP 工具，支持自编程
5. **能力可演进**：模型改沙箱/源码 + 用户贡献 + 一键恢复

详见 `AI定义无线电-概念定义与框架-v2.1.md`（含形式化描述、状态空间、设计原则）。

---

## 论文与白皮书

- **论文初稿**：`paper/TCCN-ADR-Draft-v0.1.md`（8 章 + 参考文献）
  - 8 个实验 Methodology 已设计，实验数据待硬件回采
  - 核心创新点：全栈 AIDR 参考实现、人在回路双向指令流、固件级 MCP 工具发现、自进化+一键恢复、9 轴指向系统

- **白皮书**：`MBDSDR-AI定义无线电白皮书-v1.0.docx`（458 段，12 表，96 标题）
  - 面向大众与评审的项目介绍
  - 含系统架构、双端协同、双向指令流、智能体内核等

- **概念定义**：`AI定义无线电-概念定义与框架-v2.1.md`（68KB，8 章）
  - AIDR 形式化定义、五大特征、形式化描述、设计原则、评估指标

- **5 个专题**：智能体内核、基带录制、IQ 前端、MCP 集成、卫星指向与 AR

---

## 自进化框架

MBDSDR 支持模型自编程与功能演进：

1. **沙箱执行**：模型生成的代码在隔离沙箱中执行，无法直接访问硬件
2. **用户确认**：沙箱执行成功后展示 diff，用户确认后合入
3. **一键恢复**：保留稳定版本快照，模型幻觉导致异常时一键回滚
4. **用户贡献**：类似创意工坊，用户贡献新功能，专家委员会审查后合入

参考实现：`hermes-self-evolution/`、`kilo-code/`、`mimo-code/`、`opencode/`（已克隆，借鉴上下文管理与代码执行机制）。

---

## 快速开始

### 有硬件

1. ai-sdr Mini 上电，电脑连接 WiFi `MBDSDR-Mini`（密码 `mbdsdr123`）
2. 浏览器打开 `http://192.168.4.1` 查看 Web UI
3. 命令行测试：`python3 mbdsdr_mcp_client.py --host 192.168.4.1 list_tools`
4. 配置 Cursor/Claude：参考 `MCP配置示例-Cursor-Claude.md`
5. AI 对话中输入自然语言指令，如"调谐到 FM 98.5 并录 30 秒"

### 无硬件（模拟测试）

```bash
# 终端 1
python3 mbdsdr_sim_server.py --port 81

# 终端 2
python3 mbdsdr_mcp_client.py --host localhost --port 81 list_tools
python3 mbdsdr_mcp_client.py --host localhost --port 81 find_strongest_fm
```

### 投板（立创EDA）

1. 解压 `ai-sdr-mini-原理图PCB-v0.7.1-投板包.zip`
2. 立创EDA专业版：文件→导入→KiCad，选 `ai-sdr-mini.kicad_sch`
3. 按 BOM 关联立创物料编号（19 关键件已带编号）
4. 原理图转 PCB → USB 差分 90Ω 等长/射频 50Ω 手动布线 → 其余自动布线 → 双面铺地
5. DRC 检查 → 下单 SMT

详见 `ai-sdr-mini-kicad/README-导入说明.md`。

---

## 路线图

- [x] 硬件原理图 v0.7.1（已修复 decoding-error，kicad-cli 金标准验证通过）
- [x] 固件 v0.6（14 个 MCP 工具 + list_tools 工具发现）
- [x] 电脑端 MCP 客户端 + stdio 桥接器（端到端测试通过）
- [x] 硬件模拟服务器（无硬件测试）
- [x] 论文初稿 v0.1（8 章 + 参考文献）
- [x] 白皮书 v1.0
- [x] AIDR 概念定义 v2.1
- [x] 5 个专题文档
- [x] 自进化 agent 克隆（Hermes/Kilo/Mimo/OpenCode）
- [ ] 硬件投板 + SMT 焊接
- [ ] 固件烧录 + 真实硬件测试（8 个实验）
- [ ] 论文实验数据采集 + 定稿
- [ ] 桌面端 Qt6 UI（日式低饱和扁平化，MiSans 字体）
- [ ] 移动端 HTML UI 完善
- [ ] pyrtlsdr 后端（异构双前端）
- [ ] 自进化框架完善（沙箱 + 一键恢复 + 用户贡献）
- [ ] 发射子板 v0.8（SI4463/CC1120）
- [ ] 原始 IQ 前端（ADC 直采）
- [ ] VVVF 电磁逆向（电磁拾音器 + AI 逆向）

---

## 许可证

GPL-3.0（全开源）

硬件：KiCad 工程 + BOM + 连接表，可直接投板
固件：Arduino，可直接烧录
软件：Python，可直接运行
论文：CC-BY

---

*MBDSDR — 让人人都能用的 AI 定义无线电*
*文档版本：README v0.1 (2026-09-16)*
