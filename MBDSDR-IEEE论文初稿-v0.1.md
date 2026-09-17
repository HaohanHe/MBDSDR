# MBDSDR: A Full-Stack AI-Defined Radio Reference Platform with Human-in-the-Loop, Firmware-Level MCP, and Evolvable Capability

**作者（待补充）**　呼号 BI4MIB　全开源 GPL-3.0
*本初稿为 v0.1，实验数据待硬件回采后填入；形式化定义与系统架构已固化。*

---

## 摘要

软件定义无线电（SDR）将信号处理从硬件移至软件，极大地降低了无线电创新的门槛；然而，现有 SDR 软件（GNU Radio、SDR++、SatDump 等）仍以**固定功能按钮 + 手动参数配置**为主要交互范式，用户必须具备信号处理专业知识才能完成"找台—解调—记录—分析"的完整链路。与此同时，大语言模型（LLM）与工具调用协议（MCP）的成熟为"以自然语言驱动无线电"提供了新的可能，但已有工作（如 GR-MCP）仅将 LLM 作为现有 SDR 软件的包装层，缺乏从硬件到固件再到软件的全栈协同，也缺乏人在回路（Human-in-the-Loop）的双向指令机制。

本文提出 **AI 定义无线电（AI-Defined Radio, AIDR）** 的一个工作定义，并给出首个全栈参考实现 **MBDSDR**：包含自研硬件 **ai-sdr Mini**（ESP32-S3 + SI4732 + 9 轴 IMU/磁力计 + GPS/北斗，80×60 mm 全 SMT）、固件级 MCP over WebSocket（14 个真实硬件工具，含工具发现 `list_tools`）、桌面/移动双端协同软件、以及支持模型自编程与一键恢复的自进化框架。MBDSDR 的核心设计是**人在回路 + 双向指令流**：人以自然语言给出目标（"找一个 FM 电台并录音"），AI 调用 MCP 工具执行；当 AI 无法独立完成（如天线指向、硬件切换）时，反向向人推送操作指引。我们设计了 8 个实验来验证系统的可用性、工具调用可靠性与 AI 辅助调控能力，并讨论了自进化、9 轴指向、跨端协同等创新点与局限性。MBDSDR 全开源（GPL-3.0），旨在为 AI 定义无线电提供一个可复现、可扩展的研究与教学平台。

**关键词**：软件定义无线电；AI 定义无线电；大语言模型；模型上下文协议；人在回路；自进化；ESP32；SI4732

---

## I. 引言

### A. 背景与动机

软件定义无线电（Software-Defined Radio, SDR）通过通用硬件（ADC/DAC + FPGA/CPU）替代专用射频芯片，将调制解调、滤波、解码等信号处理功能移至软件实现。过去二十年，SDR 从军用领域走向消费级，RTL-SDR、HackRF、USRP 等硬件与 GNU Radio、SDR++、SatDump 等软件构成了繁荣的开源生态。

然而，现有 SDR 软件的交互范式仍停留在"按钮 + 滑块 + 手动参数输入"阶段。一个典型的"接收 FM 广播并录音"任务需要用户手动完成：选择设备→设置中心频率→设置采样率→选择解调模式→调整增益→连接音频输出→启动录音。对于非专业用户（Ham 新手、学生、应急通信人员），这一链路的认知负荷极高。

与此同时，大语言模型（Large Language Model, LLM）在自然语言理解、推理与工具调用（Tool Calling）方面取得了突破性进展。2024 年底 Anthropic 开源的模型上下文协议（Model Context Protocol, MCP）为 LLM 与外部工具的标准化交互提供了统一接口，迅速被 Linux 基金会 Agentic AI 基金会接纳。将 LLM + MCP 引入 SDR，使得"以自然语言驱动无线电"成为可能：用户说"帮我找一个信号强的 FM 电台并录 30 秒"，AI 自动完成频率扫描、信号强度评估、调谐、解调、录音的全链路。

### B. 现有工作的不足

已有探索将 AI 引入 SDR，但存在三个层面的不足：

1. **软件包装层，缺乏全栈协同**：GR-MCP（2025）将 GNU Radio 的 block 库与 flowgraph 管理暴露给 LLM，但它只是 GNU Radio 的 MCP 包装层，AI 无法直接访问硬件寄存器、固件状态与传感器数据；硬件与软件之间的协同仍需用户手动桥接。

2. **AI 单向执行，缺乏人在回路**：现有 AI-SDR 工作（如 LLM-xApp 的 PRB 切片、MX-AI 的 O-RAN 多 agent 控制）将 AI 视为自动决策者，假设 AI 能独立完成全部任务。但在真实 SDR 场景中，许多操作（天线指向、硬件切换、物理连接）AI 无法独立完成，需要人的物理介入。缺乏"AI 办不到时反向指挥人"的双向指令机制。

3. **功能固定，缺乏可演进性**：现有 SDR 软件的功能在编译时固定，用户无法通过自然语言扩展新的解调方式、新的协议解析或新的自动化流程。虽然 GNU Radio 支持可视化编程，但这要求用户具备 flowgraph 设计能力，而非"描述需求→自动生成"。

### C. 本文贡献

针对上述不足，本文的贡献如下：

1. **提出 AI 定义无线电（AIDR）的一个工作定义**：明确 AIDR 与 SDR、认知无线电（CR）、AI 辅助 SDR 的概念边界，给出五大核心特征（人在回路、AI 辅助自主调控、自然语言优先交互、SDR 功能工具化与可编程性、能力可演进）与形式化描述。需强调，AIDR 目前尚无统一定义，本文提出的是一个可操作的工作定义而非"首次定义"。

2. **给出首个全栈 AIDR 参考实现 MBDSDR**：从自研硬件 ai-sdr Mini（ESP32-S3 + SI4732 + 9 轴 IMU/磁力计 + GPS/北斗），到固件级 MCP over WebSocket（14 个真实硬件工具），到桌面/移动双端协同软件，再到自进化框架，形成端到端可复现的研究平台。

3. **设计人在回路的双向指令流机制**：人以自然语言给目标，AI 调用 MCP 工具执行；AI 无法独立完成时反向向人推送操作指引（如"请将八木天线指向东南方向"）。这一机制在卫星接收、干扰源定位等需要物理操作的场景中尤为关键。

4. **实现固件级 MCP 工具发现与可靠调用**：在资源受限的 ESP32 上实现 JSON-RPC 2.0 over WebSocket 的 MCP 服务，包含 `list_tools` 工具发现机制，确保 AI（尤其是弱模型）能先发现工具再调用，降低"只输出 JSON 不 call"的失败率。

5. **设计 8 个实验验证系统能力**：覆盖 AI 短波扫频找台、自动制式识别、自进化弱信号接收、MCP 工具真接硬件、人机协同天线指向、解调音频 AI 分析、跨端 Web UI、GP 天线干扰源粗定位，给出详细 Methodology 与评估指标。

---

## II. 相关工作

### A. AI 与软件定义无线电

2026 年 3 月发表于 IEEE 的综述 **"Artificial Intelligence in Software Defined Radio: A Survey"** 系统梳理了 AI 赋能 SDR 的两大领域：（1）认知无线电（Cognitive Radio, CR）——从经典的能量检测/循环平稳特征检测/特征值检测，到 AI/ML 驱动的深度强化学习频谱接入、语义通信集成与对抗鲁棒性；（2）SDR 使能无线电的网络安全——干扰、欺骗、窃听的检测与防御。该综述指出，AI 在 SDR 中的应用仍以"特定任务的模型训练"为主，缺乏以自然语言为统一接口的通用交互层。

在自动调制识别（AMR）方向，半监督 + 域适应方法被用于无线 IoT 频谱监控，解决了深度学习依赖高质量标注数据的问题。在频谱感知方向，机器学习方法（从浅层神经网络到深度学习）被用于频谱占用预测，利用频谱数据的时空相关性提升预测精度。生成式 AI（VAE、GAN、扩散模型）被用于无线电频谱认知，通过数据分布拟合与生成补全提升频谱认知的精度与鲁棒性。

### B. 认知无线电与 AI 辅助 SDR

认知无线电（Mitola, 1999）的核心是"感知—学习—自适应"闭环，强调无线电设备能感知频谱环境并动态调整参数。AI 辅助 SDR（AI-Assisted SDR）则将 ML/DL 模型嵌入 SDR 信号处理链的特定环节（如调制识别、信号检测、信道估计）。

本文提出的 AIDR 与二者的本质区别在于：CR 和 AI 辅助 SDR 仍是"算法驱动"——模型在编译/训练时固定，用户通过参数配置调用；而 AIDR 是"语言驱动 + 工具调用"——用户以自然语言描述目标，LLM 通过 MCP 协议动态调用 SDR 功能工具，AI 具备一定的自主调控能力（频偏校正、中心频点核对、跳频识别），但人始终在回路中保留最终决策权。

### C. MCP 与 LLM 工具调用在无线通信中的应用

模型上下文协议（MCP）由 Anthropic 于 2024 年 11 月开源，2025 年 12 月捐赠给 Linux 基金会 Agentic AI 基金会，基于 JSON-RPC 2.0，支持 stdio、HTTP/SSE、Streamable HTTP 传输。MCP 的核心价值是**工具发现与标准化调用**：MCP 服务器自动暴露可用工具列表，AI 客户端无需逐一手写 Schema 即可发现并调用工具。

在无线通信领域，MCP 的应用刚刚起步：

- **GR-MCP**（2025）：将 GNU Radio 的 block 库、flowgraph 管理与代码生成暴露给 LLM，支持自然语言驱动的 flowgraph 创建，已在 Claude、Codex 与本地模型上测试。GR-MCP 是 MCP + SDR 的先驱工作，但它仅包装 GNU Radio 软件层，AI 无法直接访问硬件与固件。

- **MCP-based Internet of Experts (IoX)**（arXiv 2505.01834, 2025）：提出基于 MCP 的"专家网络"框架，为 LLM 配备无线环境感知推理能力，每个轻量级专家模型解决一个确定性任务（如信号检测、参数估计）。IoX 关注模型协作，而非全栈硬件-软件协同。

- **MX-AI**（arXiv 2508.09197, 2025）：面向 Open AI-RAN 的多 agent 可观测与控制平台，在 5G OAI 测试床上部署 5 个 agent（GPT-4、Llama-3 等），支持实时网络监控与控制。MX-AI 面向运营商级 O-RAN，与消费级 SDR 的场景与架构不同。

- **LLM-xApp**（2025）：使用 GPT-3.5 通过提示工程实现 O-RAN 的 PRB 切片，在仿真环境中验证。

- **Programmatic Tool Calling (PTC)**（Anthropic, 2024；LangChain 开源, 2025）：将 MCP 工具调用与代码执行结合，agent 在沙箱中编写代码编排工具，大数据量结果在模型上下文外处理，可节省高达 98% 的 token。PTC 对 SDR 场景中的频谱缩放、基带数据处理等大数据工具调用设计有直接参考价值。

本文的 MBDSDR 与上述工作的区别在于：（1）全栈自研（硬件+固件+软件），AI 通过固件级 MCP 直接访问硬件寄存器与传感器，而非仅包装现有软件；（2）人在回路双向指令流，AI 办不到时反向指挥人；（3）自进化框架，支持模型改沙箱/源码与用户投稿，功能不固定。

---

## III. AI 定义无线电的形式化定义

### A. 工作定义

**定义（AI 定义无线电，AI-Defined Radio, AIDR）**：AI 定义无线电是一种以**人在回路（Human-in-the-Loop）**为前提、以**自然语言**为首要交互接口、以**大语言模型 + 工具调用协议（MCP）**为决策与执行引擎、以**SDR 功能工具化与可编程性**为基础、具备**AI 辅助自主调控能力**与**能力可演进性**的无线电系统。

在 AIDR 中，人始终是目标的设定者与最终决策者；AI 承担"理解目标→规划步骤→调用工具→执行调控→反馈结果"的自动化链路，并在无法独立完成时向人请求物理操作或决策确认。

### B. 五大核心特征

**特征一：人在回路（Human-in-the-Loop）**。AIDR 不是"AI 完全自主无线电"。人设定目标（"接收 14.070 MHz 的 FT8 信号"）、保留最终决策权（是否录音、是否发射、是否切换天线），并在 AI 无法独立完成时提供物理操作（转动天线、插拔设备）。AI 的角色是"智能助手"而非"自动决策者"。

**特征二：AI 辅助自主调控（AI-Assisted Autonomous Tuning）**。在人设定的目标范围内，AI 具备一定的自主调控能力：自动校正频偏、核对中心频点、识别跳频图案、搜索可用频率、调整增益与解调参数。这些调控是"辅助性"的——AI 执行后向人反馈结果，人可随时干预或回滚。

**特征三：自然语言优先交互（Natural-Language-First Interaction）**。AIDR 的首要交互方式是自然语言（文本或语音），而非按钮与滑块。用户以极短的自然语言指令（"录 30 秒 FM 98.5"、"找干扰源"、"解析这段 SSTV"）驱动系统。传统 GUI 作为降级与精确控制手段保留，但不是首要接口。

**特征四：SDR 功能工具化与可编程性（SDR-as-Tools & Programmability）**。这是 AIDR 与传统 SDR 的本质差异化。AIDR 将 SDR 的全部功能（调谐、解调、录音、扫频、解码、传感器读取）封装为 MCP 工具，AI 可动态发现与调用；同时，系统具备可编程性——用户可通过自然语言描述新功能，AI 生成代码并在沙箱中执行，经确认后合入主系统（自进化）。

**特征五：能力可演进（Evolvable Capability）**。AIDR 的功能不固定。通过自进化框架，模型可在沙箱中修改算法与代码，用户可投稿新功能（类似创意工坊），经专家委员会审查后合入版本。系统提供一键恢复机制，防止模型幻觉导致"变砖"。

### C. 形式化描述

**系统状态空间**：AIDR 的状态可表示为 $S = (H, F, U, C)$，其中：
- $H$：硬件状态（设备连接、中心频率、采样率、增益、解调模式、传感器读数）；
- $F$：固件状态（WiFi 连接、MCP 会话、录音状态、OTA 状态）；
- $U$：用户上下文（当前目标、历史指令、偏好、物理操作能力）；
- $C$：AI 上下文（对话历史、工具调用记录、推理状态、自进化沙箱状态）。

**状态转移**：AIDR 的运行是一个人在回路的状态转移过程：
1. 人以自然语言输入目标 $g$；
2. AI 解析 $g$，规划工具调用序列 $[t_1, t_2, ..., t_n]$；
3. AI 通过 MCP 调用工具 $t_i$，工具执行并返回结果 $r_i$；
4. AI 根据 $r_i$ 更新状态 $S$，决定下一步（继续调用、向人反馈、请求人操作）；
5. 若 AI 无法独立完成（需物理操作或决策确认），向人推送操作指引 $o$；
6. 人执行操作或给出决策，系统继续运行。

**人在回路的形式化**：定义函数 $\text{HumanAction}(o) \rightarrow a$，其中 $o$ 是 AI 推送的操作指引，$a$ 是人执行的物理操作或决策。AIDR 的状态转移依赖于 $\text{HumanAction}$ 的返回值，这意味着 AI 的自主性是"有界"的——边界由人的物理操作能力与决策权决定。

**工具化的形式化**：AIDR 的全部 SDR 功能封装为工具集 $T = \{t_1, t_2, ..., t_m\}$，每个工具 $t_i = (name_i, desc_i, params_i, impl_i)$。AI 通过 `list_tools` 发现 $T$，通过 MCP 调用 $impl_i$。工具集 $T$ 不是固定的：自进化框架可添加新工具 $t_{m+1}$，形成 $T' = T \cup \{t_{m+1}\}$。

---

## IV. MBDSDR 系统架构

### A. 总体架构

MBDSDR 采用**四层全栈架构**：

```
┌─────────────────────────────────────────────────┐
│  应用层 (Application)                            │
│  桌面端 (Qt6/PySide6) · 移动端 (HTML/WebSocket) │
│  自然语言交互 · 频谱可视化 · 录音管理 · AI 对话  │
├─────────────────────────────────────────────────┤
│  AI 层 (AI Engine)                               │
│  LLM (硅基流动/本地) · MCP 客户端 · 自进化框架   │
│  工具发现 · 规划推理 · 沙箱执行 · 一键恢复        │
├─────────────────────────────────────────────────┤
│  固件层 (Firmware)                               │
│  ESP32-S3 · MCP over WebSocket · 14 个硬件工具  │
│  SI4732 驱动 · I2S 音频流 · 9 轴 IMU · GPS     │
├─────────────────────────────────────────────────┤
│  硬件层 (Hardware)                               │
│  ai-sdr Mini · ESP32-S3 + SI4732 + BMI260      │
│  + TMAG5273 + ATGM336H + USB2514B + AMS1117    │
│  80×60 mm · 全 SMT · SMA + U.FL · Type-C + USB-A│
└─────────────────────────────────────────────────┘
```

### B. 硬件：ai-sdr Mini

ai-sdr Mini 是 MBDSDR 的自研硬件参考板，v0.7.1 版本规格：

- **主控**：ESP32-S3-WROOM-1-N8R8（240MHz 双核 Xtensa LX7，512KB SRAM，8MB Flash + 8MB PSRAM，WiFi/BLE/USB OTG/I2S/I2C/UART/ADC/PWM）
- **SDR 接收**：SI4732-A10-GSR（AM/FM/SW/LW/SSB 接收，I2C 控制 + I2S 数字音频输出，144kHz–108MHz，支持 50Ω 天线输入）
- **9 轴姿态**：BMI260（6 轴 IMU：3 轴加速度 + 3 轴陀螺仪，I2C 0x68）+ TMAG5273A1QDBVT（3 轴磁力计，I2C 0x35）
- **定位**：ATGM336H-5NR32-G（GPS/北斗双模，UART1，支持 PPS）
- **USB**：USB2514B-AEZC-TR（USB 2.0 4 口 Hub，Type-C 上行，ESP32 + USB-A 下行）
- **电源**：AMS1117-3.3（Type-C 5V 输入，500mA 自恢复保险丝）
- **接口**：SMA（SDR 天线）、U.FL（GPS 天线）、Type-C（USB 上行/供电）、USB-A（Host 下行，可插 RTL-SDR 形成异构双前端）
- **尺寸**：80×60 mm，双层板，全 SMT（SMA 直插例外），62 个元件
- **GPIO 映射**：IO4=I2S_BCLK、IO5=I2S_LRCLK、IO7=I2S_DIN、IO8=I2C_SCL、IO9=I2C_SDA、IO10=SI4732_INT、IO11=SI4732_RST、IO12=IMU_INT、IO13=MAG_INT、IO16=GPS_PPS、IO17=UART1_RX、IO18=UART1_TX、IO19/20=USB_D-/D+、IO21=HUB_RESET、IO1/2/3/46=LED1-4

**设计要点**：
- SI4732 输出**解调后音频流**（I2S），非原始 IQ 基带——这是 SI4732 芯片的物理限制，论文中须严谨表述；原始 IQ 接收列为未来工作（需更换 ADC 直采方案）。
- USB-A 下行口在 Type-C 插电脑时可插 RTL-SDR，形成"SI4732 广播段 + RTL-SDR 宽频段"的异构双前端；ESP32 独立运行时 Hub 下行口不能驱动另一 USB 设备（供电限制）。
- 9 轴 IMU + GPS 支持天线指向测量与干扰源粗定位，这是 MBDSDR 区别于纯接收 SDR 的重要特征。

### C. 固件：MCP over WebSocket

固件运行在 ESP32-S3 上，v0.6 版本实现了：

- **WiFi AP 模式**：SSID `MBDSDR-Mini`，密码 `mbdsdr123`，IP `192.168.4.1`
- **Web 服务器**（端口 80）：远程调谐/状态查看/配置/Web OTA 升级页面
- **WebSocket MCP 服务器**（端口 81）：JSON-RPC 2.0 协议，AI 可调用全部 14 个工具
- **I2S 音频流**：SI4732 解调后音频通过 WebSocket 流式传输到电脑端，电脑端存盘为 WAV
- **9 轴传感器读取**：BMI260 + TMAG5273，I2C 400kHz 轮询（中断脚已接好，待启用）
- **GPS NMEA 解析**：ATGM336H，UART1 9600 baud
- **OTA 升级**：ArduinoOTA + Web OTA（浏览器上传固件 .bin），电脑/手机通用
- **配置持久化**：Preferences NVS

**14 个 MCP 工具**（每个都有真实硬件实现，非空壳）：

| 工具名 | 功能 | 参数 |
|---|---|---|
| `tune_fm` | 调谐 FM 广播 | freq_mhz (float 64-108) |
| `tune_am` | 调谐 AM/MW 广播 | freq_khz (int 531-1710) |
| `set_volume` | 设置音量 | volume (int 0-63) |
| `get_status` | 获取 SDR 状态 | 无（返回 mode/freq/rssi/snr） |
| `get_gps` | 获取 GPS/北斗定位 | 无（返回 fix/lat/lon/alt/sats/hdop） |
| `get_imu` | 获取 9 轴姿态 | 无（返回 acc/gyr/mag/temp） |
| `start_record` | 开始 I2S 基带录音 | 无（WAV 头通过 WebSocket 推送） |
| `stop_record` | 停止录音 | 无（返回采样数） |
| `get_version` | 获取固件/硬件信息 | 无 |
| `check_update` | 检查固件更新 | 无（返回 GitHub release/OTA 地址） |
| `trigger_ota` | 触发 OTA 升级 | 无（返回 Web OTA 地址/espota 命令） |
| `web_ota_url` | 获取 OTA 页面 URL | 无 |
| `reboot` | 重启 ESP32 | 无 |
| `list_tools` | 列出全部可用工具 | 无（MCP 工具发现） |

**MCP 工具发现机制**：`list_tools` 是 MCP 协议的核心。AI 连接后首先调用 `list_tools`，获取全部工具的名称、描述与参数 Schema，然后根据用户目标选择调用。这一机制显著降低了弱模型（如 Qwen3.6-35B-A3B）的工具调用失败率——弱模型常出现"只输出 JSON 不 call"的问题，`list_tools` 让模型先"看到"工具再决策。

### D. 软件：桌面/移动双端协同

MBDSDR 采用**双端协同架构**，类比"手机导航 + 车机导航"：

- **电脑端**（Qt6/PySide6）：大脑与工作台，负责算力密集型任务（频谱分析、解调、解码、AI 推理、录音存盘、自进化沙箱）。提供传统 GUI（频谱图、瀑布图、控制面板）作为降级与精确控制手段，同时内置 AI 对话界面与 MCP 客户端。
- **移动端**（HTML/WebSocket，手机浏览器访问 `192.168.4.1`）：眼睛与手，负责便携场景下的状态查看、远程调谐、传感器读取（手机自身 IMU/GPS 可辅助天线指向）、AI 对话。移动端无需安装 App，浏览器即客户端。
- **双端协同**：电脑端与移动端通过 WiFi 连接同一 ai-sdr Mini，共享设备状态与 MCP 会话。用户可在手机上发起"找干扰源"任务，电脑端负责频谱分析与定位计算，手机端实时显示方向指引并利用自身 IMU 辅助天线指向测量。

### E. 双向指令流：人给目标，AI 给操作

MBDSDR 的核心交互机制是**双向指令流**：

1. **人→AI（目标指令）**：人以自然语言给出目标，如"帮我接收 14.070 MHz 的 FT8 信号并解码"、"找一下这个频段的干扰源"、"录 30 秒当前电台"。
2. **AI→工具（执行指令）**：AI 解析目标，规划 MCP 工具调用序列，通过 WebSocket 调用固件工具执行。
3. **AI→人（操作指引）**：当 AI 无法独立完成时（如天线指向、硬件切换、物理连接），AI 向人推送操作指引，如"请将八木天线指向东南方向（方位角 135°），当前 IMU 测得方位角 90°"、"请将 USB-A 口的 RTL-SDR 切换到 1090 MHz"。
4. **人→系统（物理操作）**：人执行操作，系统通过传感器（IMU/GPS）或设备状态自动检测操作完成，继续运行。

这一机制在卫星接收、干扰源定位、应急通信等需要物理操作的场景中尤为关键，也是 MBDSDR 区别于纯软件 AI-SDR 的核心创新。

---

## V. 关键技术与实现

### A. 固件级 MCP 的可靠调用

MCP 协议通常运行在桌面/服务器环境（Python/Node.js），资源充足。在 ESP32-S3（512KB SRAM）上实现 MCP 需要解决三个问题：

1. **JSON 解析内存效率**：使用 ArduinoJson 静态分配，避免堆碎片；每个 MCP 请求/响应限制在 4KB 以内。
2. **WebSocket 并发**：WebSocketsServer 库支持多客户端，但 ai-sdr Mini 设计为单用户主连接 + 多观察者，主连接独占 MCP 调用权。
3. **工具调用超时与错误处理**：每个工具调用设置 5 秒超时，超时返回 JSON-RPC error（code -32603）；设备未连接（如 SI4732 未响应）时返回明确错误信息，而非静默失败。

**弱模型兼容性设计**：考虑到用户可能使用弱模型（如 Qwen3.6-35B-A3B）调用 MCP，固件做了三项兼容设计：
- `list_tools` 返回的工具描述使用简单自然语言，避免专业术语；
- 参数解析容错：`tune_fm` 接受 `freq_mhz`、`freq`、`frequency` 多种参数名；
- 错误信息可操作：返回 "Method not found" 时附带可用工具列表片段。

### B. 自进化框架与一键恢复

MBDSDR 的自进化框架允许模型在沙箱中修改算法与代码，实现"功能不固定"：

1. **沙箱执行**：模型生成的代码在隔离的 Python 沙箱中执行，无法直接访问硬件寄存器与主系统文件；沙箱提供受限的 MCP 工具调用接口。
2. **用户确认**：沙箱执行成功后，向用户展示 diff 与执行结果，用户确认后合入主系统。
3. **一键恢复**：系统保留上一个稳定版本的快照（Git commit 或文件备份），若模型幻觉导致系统异常，用户可一键回滚到稳定版本，防止"变砖"。
4. **用户投稿**：用户可投稿新功能（类似创意工坊），经专家委员会审查后合入版本。这一机制不写入白皮书，是项目的运营玩法。

自进化框架借鉴了 Hermes Agent、Kilo Code、Mimo Code、OpenCode 等通用 agent 的上下文管理与代码执行机制，但针对 SDR 场景做了硬件访问限制与工具调用沙箱化。

### C. 9 轴姿态与 GPS 指向系统

ai-sdr Mini 集成 BMI260（6 轴 IMU）+ TMAG5273（3 轴磁力计）+ ATGM336H（GPS/北斗），构成 9 轴姿态 + 定位系统，支持：

- **天线指向测量**：IMU 测得俯仰角/横滚角，磁力计测得方位角，GPS 测得位置。用户将 ai-sdr Mini 固定在八木天线或锅天线上（怪手/扎带），系统实时显示天线指向，辅助卫星/干扰源定位。
- **干扰源粗定位**：利用 GP 天线（全向）先探测干扰存在，再切换八木天线（定向）+ 云台进行指向扫描，通过信号强度与方向角的对应关系粗定位干扰源。这是 AI 辅助的"找干扰源"技能的硬件基础。
- **AR 指向增强**：手机端可将天线指向与卫星轨道/干扰源位置叠加显示，实现 6DOF AR 指向辅助（3DOF 视觉 + 罗盘增强）。

### D. 异构双前端

ai-sdr Mini 的 USB-A 下行口在 Type-C 插电脑时可插 RTL-SDR，形成异构双前端：

- **SI4732 前端**：144kHz–108MHz，AM/FM/SW/LW/SSB 解调后音频输出，适合广播段接收。
- **RTL-SDR 前端**：24MHz–1.7GHz（取决于调谐器），原始 IQ 输出，适合宽频段扫描、ADS-B（1090MHz）、气象卫星等。

电脑端软件通过 pyrtlsdr 库驱动 RTL-SDR，通过 MCP over WebSocket 驱动 ai-sdr Mini 的 SI4732，两个前端统一在 AI 工具调用框架下。AI 可根据用户目标自动选择前端（"听 FM"→SI4732，"看 ADS-B"→RTL-SDR）。

---

## VI. 实验设计

本节给出 8 个实验的详细 Methodology。实验数据待硬件回采后填入，当前为可复现的实验设计。

### 实验 1：AI 短波扫频找台

**目标**：验证 AI 能通过自然语言指令自动完成短波扫频与电台发现。
**方法**：用户说"在 31m 短波段（9.4–9.9MHz）找一个信号最强的电台并接收"。AI 调用 `tune_am` 以 5kHz 步进扫描，记录每个频点的 RSSI，选择 RSSI 最高的频点调谐并输出音频。
**评估指标**：扫频完成时间、找到电台的 RSSI、与手动调谐的一致性、用户主观满意度（1-10 分）。
**对照**：用户手动扫频找台所需时间与满意度。

### 实验 2：自动制式识别

**目标**：验证 AI 能自动识别信号制式（AM/FM/SSB/CW）并切换解调模式。
**方法**：AI 在扫频过程中分析信号特征（带宽、调制谱、语音活动检测），自动判断制式并调用 `tune_am`/`tune_fm` 切换。对于 SI4732 不支持的制式（如 FT8、SSTV），AI 提示用户切换到 RTL-SDR 前端或使用电脑端解码软件。
**评估指标**：制式识别准确率、切换延迟、误判率。

### 实验 3：自进化弱信号接收

**目标**：验证自进化框架能让 AI 自动优化弱信号接收参数。
**方法**：在弱信号场景（SNR < 10dB），AI 尝试调整增益、解调带宽、噪声抑制参数，在沙箱中生成优化代码并执行，对比优化前后的 SNR 与可懂度。
**评估指标**：优化后 SNR 提升量、可懂度主观评分、自进化代码的安全性（沙箱逃逸率=0）。

### 实验 4：MCP 工具真接硬件（核心创新点验证）

**目标**：验证固件级 MCP 工具能被 AI 真实调用并控制硬件。
**方法**：使用弱模型（Qwen3.6-35B-A3B）与强模型（如 GPT-4o）分别连接 ai-sdr Mini 的 MCP 服务，执行 14 个工具的全部调用，记录成功率、延迟、失败模式。特别关注弱模型的"只输出 JSON 不 call"失败率在 `list_tools` 机制下的改善。
**评估指标**：工具调用成功率（强模型/弱模型分别统计）、平均延迟、`list_tools` 对弱模型成功率的提升幅度、设备未插时的错误提示可操作性。
**预期结果**：强模型成功率 >95%，弱模型在 `list_tools` 辅助下成功率 >80%（无 `list_tools` 时约 50-60%）。

### 实验 5：人机协同天线指向

**目标**：验证双向指令流在卫星接收场景中的有效性。
**方法**：用户说"接收 NOAA-19 气象卫星（137.1MHz）"。AI 计算卫星当前方位角/仰角，向用户推送"请将八木天线指向方位角 X°、仰角 Y°"。用户转动天线，ai-sdr Mini 的 IMU 实时测量指向，AI 持续反馈"当前方位角 X-10°，请向右转动"，直到对准。
**评估指标**：对准所需时间、对准精度（方位角误差）、用户主观操作负荷（NASA TLX 量表）。
**对照**：用户手动查星历 + 手动对准所需时间与负荷。

### 实验 6：解调音频 AI 分析

**目标**：验证 AI 能对解调后音频进行语义分析（语音转文字、SSTV 解码、FT8 解码、VVVF 逆向）。
**方法**：AI 调用 `start_record` 录制 30 秒解调音频，通过 WebSocket 传输到电脑端，AI 对音频进行分析：语音→转文字，SSTV→解码图像，FT8→解码消息，VVVF（地铁牵引逆变器电磁声）→逆向推断程序。
**评估指标**：分析准确率、处理延迟、用户满意度。

### 实验 7：跨端手机 Web UI

**目标**：验证移动端 HTML/WebSocket UI 的可用性与响应性。
**方法**：用户用手机浏览器连接 `192.168.4.1`，执行调谐、录音、传感器读取、AI 对话等操作，记录响应延迟与 UI 渲染帧率。测试手机横屏/竖屏/折叠屏适配。
**评估指标**：操作响应延迟（<500ms 为合格）、UI 帧率（>30fps）、折叠屏/横屏适配完整性。

### 实验 8：GP 天线干扰源粗定位

**目标**：验证 AI 辅助的干扰源粗定位能力。
**方法**：用户说"找一下 433MHz 附近的干扰源"。AI 先用 GP 全向天线探测干扰存在与强度，然后提示用户切换八木定向天线 + 云台，进行 360° 指向扫描，AI 根据信号强度-方向角曲线粗定位干扰源方位，结合 GPS 位置给出干扰源可能区域。
**评估指标**：定位方位角误差、定位所需时间、用户操作负荷。

---

## VII. 讨论与创新点

### A. 创新点总结

1. **全栈 AIDR 参考实现**：首个从自研硬件（ai-sdr Mini）到固件级 MCP（14 个真实工具）到双端软件再到自进化框架的端到端 AIDR 平台。已有工作（GR-MCP、MCP-IoX、MX-AI）要么仅包装现有软件，要么面向运营商 O-RAN，缺乏消费级全栈实现。

2. **人在回路双向指令流**：AI 办不到时反向向人推送操作指引，这一机制在卫星接收、干扰源定位等需要物理操作的场景中至关重要。已有 AI-SDR 工作假设 AI 能独立完成全部任务，忽视了物理操作的必要性。

3. **固件级 MCP 工具发现与弱模型兼容**：在资源受限的 ESP32 上实现 MCP 服务，包含 `list_tools` 工具发现机制与弱模型兼容设计（容错参数解析、可操作错误信息），显著降低弱模型的工具调用失败率。

4. **自进化 + 一键恢复**：支持模型在沙箱中改代码、用户投稿、专家委员会审查合入，同时保留稳定版本快照防幻觉变砖。这使得 AIDR 的功能不固定，可随用户需求演进。

5. **9 轴姿态 + GPS 指向系统**：将 IMU/磁力计/GPS 集成到 SDR 硬件，支持天线指向测量、干扰源粗定位、AR 指向增强，这是纯接收 SDR 不具备的能力。

### B. 与已有工作的对比

| 维度 | GR-MCP (2025) | MCP-IoX (2025) | MX-AI (2025) | MBDSDR (本文) |
|---|---|---|---|---|
| 硬件 | 依赖通用 SDR | 无硬件 | 5G OAI 测试床 | 自研 ai-sdr Mini |
| MCP 层级 | 软件层（GNU Radio） | 模型层（专家网络） | 网络层（O-RAN） | 固件层（ESP32 WebSocket） |
| 人在回路 | 无 | 无 | 有限 | 双向指令流 |
| 自进化 | 无 | 无 | 无 | 沙箱+一键恢复 |
| 9 轴指向 | 无 | 无 | 无 | BMI260+TMAG+GPS |
| 目标场景 | 通用 SDR 编程 | 无线推理 | 运营商网络 | 消费级/教学/应急 |
| 开源 | 是 | 是（论文） | 是（论文） | 是（GPL-3.0，全栈） |

### C. 设计权衡

- **SI4732 vs 原始 IQ**：SI4732 成本低（约 14 元）、驱动成熟、输出解调后音频，但不输出原始 IQ。对于广播段接收与 AI 音频分析已足够，原始 IQ 接收列为未来工作（需更换 ADC 直采方案，成本显著上升）。
- **ESP32-S3 vs 更高性能 MCU**：ESP32-S3 成本低（约 30 元）、WiFi/BLE/USB/I2S 集成度高，但 512KB SRAM 限制了 MCP 并发与本地 AI 推理。AI 推理放在电脑端/云端，ESP32 仅负责硬件控制与 MCP 服务，这一分工是合理的。
- **全 SMT vs 可插拔模块**：全 SMT 降低成本与体积，但用户无法自行更换芯片。对于参考平台，全 SMT 可接受；未来可推出可插拔模块版本。

---

## VIII. 局限性与未来工作

### A. 局限性

1. **SI4732 不输出原始 IQ**：当前硬件只能输出解调后音频流，无法进行原始基带信号处理（如自定义解调、深度学习物理层）。这限制了实验 2（制式识别）与实验 6（音频分析）的深度。
2. **ESP32 资源限制**：512KB SRAM 限制了 MCP 并发连接数与本地处理能力；WiFi AP 模式下传输距离有限（约 10-20m）。
3. **AI 推理依赖外部模型**：当前 AI 推理依赖硅基流动 API 或电脑端本地模型，ai-sdr Mini 本身不具备本地 AI 推理能力。在无网络/无电脑场景下，AI 功能不可用。
4. **实验数据待采集**：本文的 8 个实验当前为 Methodology 设计，实验数据待硬件回采后填入。
5. **发射能力缺失**：当前硬件仅支持接收，不支持发射。发射能力（SI1063/SI4463/CC1120 等）列为 v0.8 扩展子板。

### B. 未来工作

1. **原始 IQ 前端**：更换为 ADC 直采方案（如 RTL2832U + R820T2，或更高性能的 AD9361），支持原始 IQ 基带接收与自定义解调。
2. **发射子板 v0.8**：集成 SI4463/CC1120 等发射芯片，支持 Sub-GHz 发射，扩展到双向通信与中继场景。
3. **本地小模型推理**：在 ESP32-S3 或更高性能 MCU（如 ESP32-P4、RK3566）上部署轻量级 LLM（如 Qwen2.5-0.5B），实现无网络/无电脑场景下的本地 AI 辅助。
4. **自进化社区运营**：建立用户投稿与专家委员会审查机制，形成"创意工坊"式的功能演进生态。
5. **VVVF 电磁逆向**：利用电磁拾音器 + ai-sdr Mini 录制地铁/列车牵引逆变器（VVVF）的电磁声，AI 逆向推断控制程序，这是一个有趣的跨学科应用。
6. **MCP 生态扩展**：将 ai-sdr Mini 的 MCP 服务接入通用 AI IDE（如 Cursor、VS Code Copilot）与通用 agent 框架（如 LangChain、AutoGPT），让任意 AI 工具都能调用 SDR 硬件。
7. **多设备协同**：多个 ai-sdr Mini 节点通过 WiFi 组网，形成分布式频谱监测网络，支持干扰源三角定位与大范围频谱地图。

---

## 参考文献

[1] Artificial Intelligence in Software Defined Radio: A Survey, IEEE Xplore, 2026. https://xplorestaging.ieee.org/document/11456026

[2] Bridging AI and Radio: An Engineer's Guide to the GNU Radio MCP Server, Skywork, 2025. https://skywork.ai/skypage/en/ai-radio-engineer-guide/1981601791052148736

[3] GR-MCP: A Model Context Protocol Server for GNU Radio, HiMCP, 2025. https://himcp.ai/server/gr-mcp

[4] Model Context Protocol-based Internet of Experts For Wireless Environment-aware LLM Agents, arXiv:2505.01834, 2025.

[5] MX-AI: Agentic Observability and Control Platform for Open and AI-RAN, arXiv:2508.09197, 2025.

[6] LLM-xApp: PRB Slicing by Prompting in O-RAN, 2025.

[7] Programmatic Tool Calling: Efficient Agent Pattern with MCP and Code Execution, Anthropic, 2024; LangChain Community, 2025.

[8] Semi-supervised-based automatic modulation classification with domain adaptation for wireless IoT spectrum monitoring, Frontiers in Physics, 2023.

[9] Machine learning-based spectrum occupancy prediction: a comprehensive survey, Frontiers in Communications and Networks, 2025.

[10] Deep Learning Frameworks for Cognitive Radio Networks: Review and Open Research Challenges, arXiv:2410.23949, 2024.

[11] Advances in Machine Learning-Driven Cognitive Radio for Wireless Networks: A Survey, IEEE Communications Surveys & Tutorials, 2024.

[12] 生成式人工智能赋能无线电频谱认知：进展与挑战，国防科技大学学报，2026.

[13] Artificial Intelligence-Based Spectrum Sensing for Beyond 5G and 6G Cognitive Radio Networks: A Comprehensive Survey, 2025.

[14] Integrated Radio Sensing Capabilities for 6G Networks: AI/ML Perspective, arXiv:2507.14856, 2025.

[15] MCP: the Universal Connector for Building Smarter, Modular AI Agents, InfoQ, 2025.

[16] Mitola J., "Cognitive radio: making software radios more personal," IEEE Personal Communications, 1999.

[17] GNU Radio, https://www.gnuradio.org/

[18] SDR++, https://github.com/AlexandreRouma/SDRPlusPlus

[19] SatDump, https://github.com/SatDump/SatDump

[20] SI4732-A10 Data Short, Skyworks Solutions.

[21] ESP32-S3 Datasheet, Espressif Systems.

[22] BMI260 Datasheet, Bosch Sensortec.

[23] TMAG5273 Datasheet, Texas Instruments.

[24] ATGM336H-5NR32-G User Manual, ATGMicro.

[25] USB2514B Datasheet, Microchip Technology.

---

## 附录 A：ai-sdr Mini v0.7.1 BOM（关键件）

| 位号 | 型号 | 立创编号 | 功能 |
|---|---|---|---|
| U1 | USB2514B-AEZC-TR | C16251 | USB 2.0 Hub |
| U2 | SI4732-A10-GSR | C2155558 | AM/FM/SW/LW/SSB 接收 |
| U3 | TMAG5273A1QDBVT | C5220660 | 3 轴磁力计 |
| U4 | ESP32-S3-WROOM-1-N8R8 | C2913201 | 主控 |
| U5 | BMI260 | C7544197 | 6 轴 IMU |
| U6 | ATGM336H-5NR32-G | C49233943 | GPS/北斗 |
| U7 | AMS1117-3.3 | C6186 | 稳压 |
| J1 | KH-TYPE-C-6P | C709355 | Type-C 母座 |
| USB1 | USB-234-BCW | C720528 | USB-A 母座 |
| RF1 | BWSMA-KE-Z001 | C496549 | SMA 天线座 |
| RF2 | HMT-U.FL-R-SMT | C53122033 | U.FL 天线座 |
| SW1 | TS-1101-C-W | C318938 | 复位按键 |
| X1 | FC-135 32.768kHz | C32346 | SI4732 时钟晶振 |
| X2 | 3225 24MHz 12pF | C5181479 | USB Hub 时钟晶振 |
| LED | LTST-C190YKT | C125097 | 黄色 LED ×4 |
| F1 | SMD0805P050TF | C20978 | 500mA 自恢复保险丝 |

共 62 个元件（含无源件），80×60 mm 双层板，全 SMT（SMA 直插例外）。

---

## 附录 B：固件 MCP 协议示例

**连接**：WebSocket `ws://192.168.4.1:81`

**工具发现请求**：
```json
{"jsonrpc":"2.0","id":1,"method":"list_tools","params":{}}
```

**工具发现响应**（节选）：
```json
{"jsonrpc":"2.0","id":1,"result":[
  {"name":"tune_fm","desc":"调谐FM广播, freq_mhz单位MHz","params":{"freq_mhz":"float 64-108"}},
  {"name":"get_status","desc":"获取当前SDR状态(模式/频率/RSSI/SNR)","params":{}},
  ...
]}
```

**调谐请求**：
```json
{"jsonrpc":"2.0","id":2,"method":"tune_fm","params":{"freq_mhz":98.5}}
```

**调谐响应**：
```json
{"jsonrpc":"2.0","id":2,"result":{"ok":true,"freq_mhz":98.5}}
```

---

*文档版本：v0.1（2026-09-16）　作者：BI4MIB　全开源 GPL-3.0*
*实验数据待硬件回采后填入 v0.2*
