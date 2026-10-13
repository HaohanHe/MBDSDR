# SDR × LLM / MCP 生态对照（feature-parity）

> 核实日期：**2026-10-13**。本文对照三个公开参照项：sandraschi/sdr-mcp（RTL-SDR 专用 MCP server）、GNU Radio MCP server 生态（社区多个实现）、RadioMaster（arXiv 2606.01862，多智能体信号生成论文）。
> MBDSDR 基线：83 工具三通道（schema / executor / hub）+ headless MCP 10 域（SDR 调谐/VFO、亚音数字解码、VNA 测量、VNA 分析、VNA 校准、导出演制、书签扫描卫星、系统设备、记忆编排、工作台编排）；零 LLM 依赖（stdio JSON-RPC 通用 client 即可用）；写工具 `arm_manual` 手动 gate；诚实空态纪律。
> 纪律：GPL 项目仅对照机制描述，不抄代码；本文不做优劣评判，只登记能力形状与来源；检索不到/存疑项如实标注。

---

## 1. 参照项目一栏

### 1.1 sandraschi/sdr-mcp

| 项 | 值 | 来源 |
|---|---|---|
| 仓库 | https://github.com/sandraschi/sdr-mcp | Glama 镜像页确认 |
| 许可 | **MIT** | Glama 页明示 |
| 语言/栈 | Python 3.12 + asyncio；FastMCP 3.4 / MCP 2.14+；pyrtlsdr；numpy/scipy FFT；websockets；React 19 + Vite + Tailwind 前端 | Glama 页 Tech Stack 表 |
| 接入方式 | stdio（`sdr-mcp serve`，Claude Desktop）+ streamable HTTP + 自带 React dashboard（`http://127.0.0.1:11118/`） | Glama 页 Quick Start |
| 工具面形状 | **17 工具**（`src/sdr_mcp/server.py`，FastMCP portmanteau 风格）；分硬件控制 / 频谱分析 / GNU Radio 解调 sidecar / 频率数据库 / WebSocket 流五块 | Glama 页 Project Structure + Features |
| 核心能力 | RTL-SDR 自动探测；mock IQ 模式（无 dongle 也能跑 FFT/waterfall/WebSocket）；频率 24 MHz–1.766 GHz；增益 auto/manual；实时 IQ 捕获；2048-pt FFT + Hamming 窗；峰值检测；100 行 waterfall 历史；FM 解调走 **Docker 化 GNU Radio + rtl_tcp** sidecar（MCP 工具 `sdr_gnuradio` start/stop/status）；11 个预存台站（LW/MW/SW/VHF）；radio-browser.info 在线搜台（25k+）；WebSocket 实时频谱广播 + 远程 freq/gain 控制 | Glama 页 Features |
| 平台取向 | **Windows-first**（WinUSB/Zadig、fleet 启动器、MCPB manifest）；Linux 核心 Python 理论可跑但 dashboard 脚本只针对 Windows | Glama 页 Hardware 段 |
| 生态位 | "USB dongle + AI 对话 + web 仪表板" 的入门玩具向 MCP；旁挂 AetherLink SDR MCP（N-Erickson，RTL-SDR/HackRF 协议解码） | Glama Related Servers |

### 1.2 GNU Radio MCP server（社区多实现，非官方）

> GNU Radio 官方（gnuradio.org）截至 2026-10-13 **未发布官方 MCP server**；ControlPort 走 Apache Thrift（见 gnuradio.org doxygen page_ctrlport），与 MCP 无关。下列均为社区实现，形状差异很大，分列。

#### 1.2a pranav-d33 / gnuradio-mcp-server（PyPI）

| 项 | 值 | 来源 |
|---|---|---|
| 包 | https://pypi.org/project/gnuradio-mcp-server/ （v0.1.1，发布 2026-03-24，作者 Pranav Dhiran） | PyPI 页 |
| 许可 | **MIT** | PyPI 页 |
| 语言/栈 | Python ≥3.10；MCP stdio / streamable-http（`GNURADIO_TRANSPORT`） | PyPI Tools Reference + Env Vars |
| 架构 | LLM ↔ MCP server ↔ **XML-RPC**（控制流，指向 GNU Radio Companion flowgraph 内嵌 XMLRPC Server port）+ **ZMQ SUB**（IQ 数据流，要求 flowgraph 里 ZMQ PUB Sink 输出 complex64） | PyPI Architecture 图 |
| 工具面形状 | **12 工具**：`gnuradio_flowgraph_start/stop/status`、`gnuradio_set_center_frequency/sample_rate/gain/variable`、`gnuradio_get_variable/list_variables`、`gnuradio_capture_samples`（返回 power summary）、`gnuradio_get_spectrum_snapshot`（FFT/PSD + peak）、`gnuradio_detect_signals`（频段扫描找活动信号） | PyPI Tools Reference 表 |
| 硬件抽象 | env `GNURADIO_HARDWARE` ∈ {hackrf, rtlsdr, usrp, simulation}；频率上限/增益上下限/采样率上下限全部 env 可调；采样率变更需重启 flowgraph（已知限制，工具内告警） | PyPI Env Vars + Edge Cases |
| 生态位 | "让 LLM 遥控一个已经在跑的 GNU Radio flowgraph"——**不生成 flowgraph，只调运行中的变量**；需要用户先在 GRC 里画好图并启用 XMLRPC + ZMQ PUB | PyPI Quick Start |

#### 1.2b Dollarhyde / gr-mcp

| 项 | 值 | 来源 |
|---|---|---|
| 仓库 | https://github.com/Dollarhyde/gr-mcp （经 mcprepository.com 镜像核实） | mcprepository.com/Dollarhyde/gr-mcp |
| 许可 | **MIT** | 页首明示 |
| 语言/栈 | Python 3.10+；stdio / SSE（`--transport sse --port 8000`）；测试过 Claude / Codex / llama.cpp 本地模型 | 页 Usage |
| 工具面形状 | **~20 工具**，按 5 类：①硬件探测 `detect_sdr_hardware`（UHD/SoapySDR/HackRF/RTL-SDR）；②flowgraph 管理 `create/load/save/get_structure/list/set_active_flowgraph`；③块操作 `add_block/remove_block/set_parameter/connect_blocks/disconnect_blocks`；④验证与代码生成 `validate_flowgraph/generate_python_code`；⑤布局 `organize_flowgraph_layout`；⑥模板 `list_templates/instantiate_template`（内置 fm_receiver / spectrum_analyzer / bpsk_transceiver） | 页 Available Tools 表 |
| 核心机制 | **类型系统预检**：连接前校验 GNU Radio block 的 type 与 connection rules；块数据库提供精确参数规格；`validate_flowgraph` 在代码生成前抓错；`save_flowgraph` 自动按信号流组织 .grc 布局 | 页 Statement of Need + Examples |
| 生态位 | "让 LLM 用对话生成 .grc 文件"——**不碰活硬件**，产物是 .grc / Python 代码，用户再开 gnuradio-companion 跑 | 页 Examples（save 到 .grc 路径） |

#### 1.2c yoelbassin / gnuradioMCP

| 项 | 值 | 来源 |
|---|---|---|
| 仓库 | https://github.com/yoelbassin/gnuradioMCP | UBOS MCP Marketplace 页（最后更新 2025-04-28） |
| 许可 | 未在 UBOS 页明示（Glama/LobeHub 标记 "Unvalidated"） | UBOS + LobeHub 页 |
| 语言/栈 | Python ≥3.13，UV 环境管理；需系统装 GNU Radio | UBOS Getting Started + LobeHub 描述 |
| 生态位 | 与 Dollarhyde/gr-mcp 同类——程序化/AI 驱动生成 GNU Radio flowgraph；**未独立精读其 README**，工具面形状待核 | UBOS 页摘要 |

### 1.3 RadioMaster（arXiv 2606.01862）

| 项 | 值 | 来源 |
|---|---|---|
| 论文 | https://arxiv.org/abs/2606.01862 （v1 2026-06-01，v2 2026-07-28；cs.MA / cs.AI / cs.NI） | arXiv abs 页 |
| 标题 | **RadioMaster: Multi-Agent System for Autonomous Radio Signal Generation** | arXiv abs 页 |
| 作者 | Jiazhen Lei, Yuxin Sha, Tianze Cao, Sihan Wang, Bingbing Wang, Zeming Yang, Fengyuan Zhu, Xiaohua Tian（Xiaohua Tian 属上海交大可推断，**论文页未明示单位**，不展开） | arXiv abs 页作者列表 |
| 许可 | CC-BY-4.0（view license 标识） | arXiv abs 页底 |
| 核心主张 | 把用户意图 → 协议规划 → 基带合成 → 硬件配置 串成一条链，端到端成功率在裸 LLM 下趋近于零；RadioMaster 用三个支柱解决：**RadioWiki**（领域知识检索 grounding，压幻觉）、**RadioAgent**（把脆弱流水线拆成可独立执行/局部恢复的 stage）、**RadioEmulator**（闭环物理层验证 gating 部署）；配套 **RadioBench**（首个自治信号生成 benchmark） | arXiv Abstract |
| 方向 | **TX / 信号生成**（"verified emissions transmitted over the air"），不是 RX / 观测；宣称配置时间最多降 28×，configuration viability 与 signal fidelity 超 SOTA baseline | arXiv Abstract |
| 与 MCP 关系 | **摘要未提及 MCP**；这是一个多智能体研究框架，不是公开工具/SDK；代码是否开源、是否走 MCP 协议，abs 页未给——**未能核实** | arXiv abs 页 |

---

## 2. Feature-parity 表

### 2.1 他们有什么、MBDSDR 没有（逐项登记评估）

| # | 能力 | 出处 | 价值 | 体量 | 是否值得抄机制 | MBDSDR 现状 |
|---|---|---|---|---|---|---|
| T1 | **radio-browser.info 在线搜台**（25k+ 台站，按频率/位置查） | sdr-mcp Frequency Database | 中（爱好者向，与 VNA/解码主线弱相关） | 小（HTTP API 包装，~1 工具） | 🟡 可选；若做书签扫描卫星域的"台站发现"子能力可借鉴 API 形态，但不必现在加 | 书签扫描卫星域已有 bookmark + satellite TLE，未接在线台站库 |
| T2 | **Mock IQ 模式**（无 dongle 时合成 IQ 跑 FFT/waterfall/WebSocket） | sdr-mcp `SDR_MCP_MOCK=auto` | 中（CI / 无硬件 demo） | 小 | ✅ 机制可借：MBDSDR 已有诚实空态纪律，但**合成 IQ 源**用于演示/测试与空态是两回事；若 headless MCP 需要"无硬件也能跑通调谐 demo"可参考 | 空态纪律 = 诚实报"无设备"，不合成假数据 |
| T3 | **Docker 化 GNU Radio sidecar**（rtl_tcp → GNU Radio 容器跑 FM 解调，MCP 工具 start/stop/status） | sdr-mcp `sdr_gnuradio` | 低-中 | 中（容器编排 + 进程管理） | ❌ 不抄：MBDSDR 自带解调链（am/fm/wfm_stereo/digital_demod），再挂 GNU Radio 容器是重复造轮子；机制上"sidecar 进程生命周期 MCP 化"可借鉴但无场景 | 解调链在 cpp 内闭环 |
| T4 | **React Web Dashboard**（浏览器里看 spectrum/waterfall/FM audio，WebSocket 推流） | sdr-mcp `web_sota/` | 中（远程/移动端查看） | 大（React 19 + Vite + WebSocket + canvas 渲染） | 🟡 机制可借：MBDSDR 有 AIGUI 工作台编排（Flutter 桌面），但**浏览器查看面**是空白；若未来要做手机浏览器远程看 waterfall，WebSocket 推频谱 + canvas 降采样是成熟范式 | AIGUI 是桌面 Flutter，无 web 查看面 |
| T5 | **XML-RPC 遥控运行中 flowgraph**（不生成图，只调活变量） | gnuradio-mcp-server 架构 | 低（MBDSDR 不跑 GNU Radio flowgraph） | — | ❌ 不适用：MBDSDR 自己就是 flowgraph 宿主，不需要外挂 XML-RPC | 自身即 flowgraph |
| T6 | **ZMQ SUB 收 complex64 IQ**（flowgraph 里 ZMQ PUB Sink → MCP server） | gnuradio-mcp-server 架构 | 低（同上） | — | ❌ 不适用 | 同上 |
| T7 | **flowgraph 类型系统预检**（连接前校验 block type / connection rules / 参数规格） | Dollarhyde/gr-mcp `validate_flowgraph` + block DB | 中（"LLM 生成代码前先静态校验"的范式） | 中（需建 block 元数据库） | 🟡 机制可借：MBDSDR 的 83 工具 schema 本身就是"工具级类型预检"；但**flowgraph 级**（多工具串联的 DAG 校验）未做。AIGUI 工作台编排若未来允许 LLM 排工具链，可借鉴"连接前校验 type 兼容" | 单工具 schema 有，工具间 DAG 校验无 |
| T8 | **flowgraph 模板库 + 参数化实例化**（fm_receiver / spectrum_analyzer / bpsk_transceiver，`${param}` 替换） | Dollarhyde/gr-mcp `list_templates/instantiate_template` | 中-高 | 小 | ✅ 值得借：MBDSDR 有书签扫描卫星 + 工作台编排，但**预设场景模板**（一键"扫 433MHz ISM""一键追 NOAA 过境"）未沉淀成模板工具族；机制上"命名模板 + 参数槽"很轻 | 书签/卫星是原子工具，无模板层 |
| T9 | **.grc 文件自动布局**（按信号流 auto-arrange block 位置） | Dollarhyde/gr-mcp `organize_flowgraph_layout` | 低（MBDSDR 无 .grc） | — | ❌ 不适用（不同格式） | — |
| T10 | **`detect_signals` 频段扫描**（sweep 一段频率，报告活动信号） | gnuradio-mcp-server 工具 12 | 中 | 小 | 🟡 MBDSDR 有书签扫描，但"给定频段自动扫 + 报告峰"作为单工具是否已覆盖需对照 headless MCP 书签扫描卫星域；若未覆盖可加 | 书签扫描域存在，具体 sweep 工具形状待核 |
| T11 | **多智能体领域知识 grounding（RadioWiki）** | RadioMaster 支柱一 | 中（对 LLM 压幻觉有效） | 大（需建领域知识库 + RAG） | 🟡 机制可借但成本高：MBDSDR 是**零 LLM 依赖**设计，RadioWiki 是给 LLM 加外挂知识——哲学相反；若未来 headless MCP 接 LLM client 时，"工具描述里内嵌领域常识"是轻量替代 | 工具 schema 描述自含常识，无外挂 KB |
| T12 | **stage 级局部恢复**（流水线拆成独立可重跑 stage） | RadioMaster 支柱二 | 高（工程范式） | 中 | ✅ 机制可借：MBDSDR 的 checkpoint-writer（SQLite FTS5 记忆族）已经是"可恢复"思路；但**操作级补偿/回滚**（类似 Sagas）未做。VNA 校准若中途失败，能否回滚到上一个 checkpoint？这是 RadioAgent 范式的直接启示 | 有 checkpoint-writer，无操作级补偿事务 |
| T13 | **闭环物理层验证 gating**（RadioEmulator：TX 前先仿真验证） | RadioMaster 支柱三 | 高（安全/正确性） | 大（需仿真器） | 🟡 MBDSDR 是 RX 为主 + VNA，不涉及 TX 发射；但 VNA 校准完成后"先跑已知标准件验证再放行测量"是同形的闭环 gating——MBDSDR 是否有"校准后验证件"工具？待核 | VNA 校准域存在，验证件工具形状待核 |
| T14 | **RadioBench 式 benchmark**（自治信号生成评测集） | RadioMaster | 低（学术） | — | ❌ 不适用（MBDSDR 不是研究框架） | — |
| T15 | **SSE transport**（除 stdio 外开 HTTP/SSE 端口给 client） | Dollarhyde/gr-mcp `--transport sse` | 中 | 小 | ✅ MBDSDR headless MCP 若已支持 stdio；streamable-http / SSE 远程接入是否已有？若无可补（pyproject 一行 MCP server 配置即可） | headless MCP 10 域 transport 形状待核 |

### 2.2 MBDSDR 有什么、他们没有（反向登记）

| # | 能力 | MBDSDR 形状 | 为什么他们没有 |
|---|---|---|---|
| R1 | **VNA 8 分析面**（谐振 / 匹配 / TDR / LC / 滤波器 / Touchstone 导出等） | VNA 测量 + VNA 分析 + VNA 校准 + 导出演制 四域 | sdr-mcp 是 RTL-SDR 频谱玩具；gnuradio-mcp 是 flowgraph 遥控；gr-mcp 是 .grc 生成；RadioMaster 是 TX 生成——**没有任何一个做矢量网络分析**。Touchstone 导出 / TDR / LC 估算在 SDR×LLM 生态里是空白 |
| R2 | **AIGUI 工作台编排**（`arrange_workspace` 等 4 工具） | 工作台编排域 | sdr-mcp 有 React dashboard 但那是**展示面**，不是"LLM 主动排布工作台布局"的工具面；gr-mcp 的 `organize_flowgraph_layout` 是排 .grc 块位置，不是排 UI 工作区 |
| R3 | **卫星 TLE 过境预测** | 书签扫描卫星域 | sdr-mcp 有台站库但不接 TLE / 轨道预报；gnuradio-mcp 完全不碰卫星；RadioMaster 是 TX 信号生成不碰过境 |
| R4 | **SQLite FTS5 记忆工具族 + checkpoint-writer** | 记忆编排域 | sdr-mcp 无持久记忆；gnuradio-mcp 无；gr-mcp 只写 .grc 文件；RadioMaster 的 RadioAgent 是 LLM 内部 state，不是外部化可审计的工具族 |
| R5 | **83 工具三通道**（schema / executor / hub 分离） | 全项目 | sdr-mcp 17 工具、gnuradio-mcp-server 12 工具、gr-mcp ~20 工具——MBDSDR 工具面数量级领先；且三通道分离（schema 单一事实源 + executor dispatch + hub 聚合）在参照项里无同形物（他们都是单文件 FastMCP server 一把梭） |
| R6 | **零 LLM 依赖 headless**（stdio JSON-RPC 通用 client 即可用，不绑 Claude/Cursor） | headless MCP 10 域 | sdr-mcp README 首屏就写 "Works with Cursor, Claude Desktop"；gnuradio-mcp 配 Claude Desktop JSON；gr-mcp 配 Claude Code / Codex——**全部假设 client 是 LLM**。MBDSDR 反向：headless 模式下任何 JSON-RPC client（含纯脚本、含未来其他 agent）都能用 |
| R7 | **写工具 `arm_manual` 手动 gate** | 写工具族 | sdr-mcp / gnuradio-mcp / gr-mcp 的写工具（set_freq / set_gain / flowgraph_stop）**无手动 arm 机制**——LLM 可直接调。MBDSDR 要求写操作先 arm 再 fire，是安全姿态差异 |
| R8 | **诚实空态纪律**（无硬件时不报假数据） | 系统设备域 | sdr-mcp 反其道：无 dongle 时**自动合成 mock IQ** 让 demo 跑通；gnuradio-mcp 需要用户先在 GRC 开图；gr-mcp 不碰硬件。MBDSDR 的"无设备就诚实报空"在 SDR×LLM 生态里是少数派 |
| R9 | **解码器族深度**（FT8 历史列表 / ADS-B 快照 / APRS / AX.25 等） | 亚音数字解码域 | sdr-mcp 只做 FM 音频；gnuradio-mcp 不解码（只抓 IQ + FFT）；gr-mcp 不解码；RadioMaster 是 TX 不是 RX 解码。多协议解码工具族是 MBDSDR 独有 |

---

## 3. 结论（诚实）

### 3.1 形状差异本质

- **sandraschi/sdr-mcp** = "RTL-SDR 入门玩具 + React dashboard"，17 工具，MIT，Windows-first，价值在"零门槛让 AI 玩 dongle"。它的 mock IQ 模式和 web dashboard 是 MBDSDR 可借鉴的两个机制点（T2 / T4），但它的工具面深度（频谱/FM 而已）远不及 MBDSDR。
- **GNU Radio MCP server 生态** = 三个不同取向的社区实现：
  - pranav-d33/gnuradio-mcp-server：遥控**运行中** flowgraph（XML-RPC + ZMQ），12 工具；
  - Dollarhyde/gr-mcp：**生成** .grc / Python 代码，~20 工具，类型预检 + 模板；
  - yoelbassin/gnuradioMCP：与 gr-mcp 同类，未独立精读。
  他们都把 GNU Radio 当宿主，MCP server 只是"LLM ↔ GNU Radio"的薄桥。MBDSDR **自身就是宿主**（cpp 内 DSP + Flutter UI + headless MCP），所以这三者的"桥接机制"对 MBDSDR 大多不适用；可借鉴的是**类型预检范式（T7）**和**模板库（T8）**。
- **RadioMaster** = 学术论文（CC-BY），多智能体 TX 信号生成，**不是工具/SDK**，abs 页未提 MCP。它的三个支柱里，"stage 级局部恢复（T12）"和"闭环验证 gating（T13）"是工程范式启示，与 MBDSDR 的 checkpoint-writer / VNA 校准形成对话；但 RadioWiki（T11）的"外挂领域 KB 压幻觉"与 MBDSDR"零 LLM 依赖、工具 schema 自含常识"的设计哲学相反，不抄。

### 3.2 MBDSDR 的独有面（不吹，只登记）

VNA 8 分析面、AIGUI 工作台编排、卫星 TLE、SQLite FTS5 记忆族、83 工具三通道、零 LLM 依赖 headless、写工具 arm_manual、诚实空态、多协议解码器族——这九项（R1–R9）在三个参照项里均未出现同形物。其中 R1（VNA）和 R9（解码器族）是**能力空白**，不是"他们做得比我们浅"而是"他们没做这个领域"。

### 3.3 值得后续跟进的机制候选（按优先级）

1. **T8 模板库**：把"扫 433MHz ISM""追 NOAA 过境""VNA 校准全流程"沉淀成命名模板 + 参数槽工具族——成本小、用户价值高。
2. **T12 操作级补偿事务**：在现有 checkpoint-writer 之上，加"VNA 校准中途失败回滚到上一个已知好 checkpoint"——RadioAgent 范式的直接落地。
3. **T7 工具链 DAG 预检**：AIGUI 工作台编排若允许 LLM 排多工具链，加"连接前校验输出/输入 schema 兼容"。
4. **T15 SSE / streamable-http transport**：headless MCP 若只有 stdio，补远程接入。
5. **T4 Web 查看面**：长期候选——手机浏览器看 waterfall，但当前 Flutter 桌面 + headless 已覆盖主要场景，不急。

### 3.4 诚实未完成项（未能核实 / 存疑）

| 项 | 状态 |
|---|---|
| yoelbassin/gnuradioMCP 的工具面形状 | **未独立精读 README**，仅经 UBOS / LobeHub 镜像页摘要登记为"flowgraph 生成类"；许可未明示 |
| RadioMaster 是否开源 / 是否走 MCP 协议 | arXiv abs 页未给代码仓库链接；**未能核实**。本文只对照其论文提出的三个支柱范式，不假设其实现细节 |
| RadioMaster 作者单位 | abs 页未明示；Xiaohua Tian 属上海交大是外部知识，本文不展开 |
| sdr-mcp 的 17 个工具**逐一名单** | Glama 页只说"17 tools"未列名；`MCP_SERVER.md`（仓库 docs/）未精读。本文按 Features 五块归类，不逐一登记 |
| gnuradio-mcp-server 的 `gnuradio_detect_signals` 算法细节 | PyPI 页只说"sweep a frequency range and detect active signals"，未说阈值/窗/重叠——**未精读源码** |
| MBDSDR headless MCP 书签扫描卫星域是否已有"sweep 单工具"（T10 对照点） | 本文基于任务书给的 10 域名称推断，未读 MBDSDR 实际工具清单源码；**待后续对照 tool_schema 核实** |
| MBDSDR headless MCP transport 是否已含 SSE/streamable-http（T15 对照点） | 同上，未读源码 |
| GNU Radio 官方是否计划出 MCP server | gnuradio.org 官网未检索到相关公告；**截至 2026-10-13 未见官方 MCP server** |

---

## 4. 来源清单

- sandraschi/sdr-mcp Glama 页：https://glama.ai/mcp/servers/sandraschi/sdr-mcp （核实 2026-10-13）
- gnuradio-mcp-server PyPI 页：https://pypi.org/project/gnuradio-mcp-server/ （核实 2026-10-13）
- Dollarhyde/gr-mcp mcprepository 页：https://mcprepository.com/Dollarhyde/gr-mcp （核实 2026-10-13）
- yoelbassin/gnuradioMCP UBOS 页：https://ubos.tech/mcp/gnuradio/overview/ （核实 2026-10-13，未精读 README）
- RadioMaster arXiv abs 页：https://arxiv.org/abs/2606.01862 （核实 2026-10-13，v2 2026-07-28）
- GNU Radio ControlPort（Thrift，非 MCP）：https://www.gnuradio.org/doc/doxygen-3.7.8/page_ctrlport.html （旁证"官方无 MCP"）
