# Phase60 — ACARS + NAVTEX 全链集成（云端重建）

方向：把两个 packet-text 解码器（ACARS / NAVTEX）从"隔离核心"集成到完整接收链
（VfoManager 解调 → 引擎快照 emit → UI 数据面板 → 三通道只读工具），并保留每个
失效边界的诚实数字。本阶段不写论文文本，只做厚项目本体。

## 交付内容（file:line，以本提交 HEAD 为准）

### 解码器核心（子 agent 交付，本提交引入，独立复跑验证）
- `cpp/src/dsp/acars_decoder.h`：`kAcars*` 具名常量 :40-53（2400 bd、±600 Hz、
  0x7F 前导、SYN/BOT/ETX/EOT、kMinPreambleBytes=3、kMaxFrameBytes=256）
- `cpp/src/dsp/acars_decoder.cpp`：`crc16`（CCITT-FALSE poly 0x1021 init 0xFFFF，
  BOT…ETX 含）:13；Hunt/Collect 帧状态机 :115/:145-157；`emitPacket` :83-110
  （CRC 失败帧仍 emit 且 `crcOk=false`，不丢弃）
- `cpp/src/dsp/navtex_decoder.h`：具名常量 :31-46（100 bd、±85 Hz、ITA2 codes、
  kNavtexPhasingMinChars=20）；non-copyable（浅拷贝防护）
- `cpp/src/dsp/navtex_decoder.cpp`：5-bit 字符对齐搜索 :163-200（Nuttall 群延时
  致比特偏移 0-4 bit，扫最长 {30,15} 导频 run）；SITOR-B 去交织 + diversity
  诚实计数 :242-269（不匹配仅 `++diversityErrors_`，不静默修正）；帧状态机
  :280-380

### 集成（本 segment）
- `cpp/src/dsp/vfo_manager.h/.cpp`：ACARS 分支 :179（ifTarget=48000、chBw=12500，
  `AcarsDecoder(ifRate)`）；NAVTEX 分支 :204（ifTarget=12000、chBw=600，
  `NavtexDecoder()` + `setSampleRate(ifRate)`）；process() 数据分支扩 4 模式 :465；
  readouts acarsPackets/navtexMessages :580/:585；clearDigitalOutputs 扩 reset :610
- `cpp/src/dsp/spectrum_engine.h/.cpp`：访问器 :241-242/:1008-1015；signals
  acarsPacketsChanged/navtexMessagesChanged :462-463；计数 lastAcarsCount_/
  lastNavtexCount_ :605-606；run() 快照 emit（VOR 块后）
- `cpp/src/ui/data_text_panel.h/.cpp`：4 列表格 + 诚实空态 + 清空信号（rowCount 读出口）
- `cpp/src/ui/main_window.h/.cpp`：模式下拉加 "ACARS","NAVTEX" :493；「数据」tab
  :994-996；QueuedConnection 连接 :2364-2380
- `cpp/src/core/bandwidth_preset.h`：kBwAcarsHz=12500 / kBwNavtexHz=600 :63-64 +
  默认带宽映射 :82-83；`cpp/src/core/tokens.h`：kControlHubModes 加两模式 :992
- `cpp/CMakeLists.txt`：`mbdsdr_packet` 静态库 :241-246（acars+navtex+fsk+demod+agc
  编译一次 + 目录级 link_libraries 注入，避免约 30 个显式测试目标逐目标重编解码器）；
  data_text_panel.cpp 入主 UI 源 :111 与 test_digital_panel_ui；test_acars_decode
  :855、test_navtex_decode :865

### 三通道只读工具（38 → 40）
- Agent：`get_acars_packets` / `get_navtex_messages`（executors
  `agent_tools.cpp:463/:494`，分发行 :1020/:1021；schema `tool_schema.cpp:344/:362`，
  write=false、可选 channel_id、缺省当前选中信道）
- ControlHub：命令 + 声明 + 分发表（`control_hub.h:218-219`、`control_hub.cpp:139-140/
  :978/:1009`）；HTTP GET `/acars_packets`、`/navtex_messages` 委托 ControlHub
  （`control_http_server.cpp:264/:266` 端点清单、:298/:301 路由）
- 无解码结果返回空列表（诚实空态，不编造）

## 真实验证数字（本 segment 独立复跑）

### ACARS（48 kHz / 2400 bd，40 帧/SNR 点，AWGN，固定种子）
| SNR(dB) | emitted | crcOK | crcBAD |
|---|---|---|---|
| 20-10 | 40 | 40 | 0 |
| 8 | 39 | 38 | 1 |
| 6 | 33 | 20 | 13 |
| 4 | 13 | 1 | 12 |
| 2 | 2 | 0 | 2 |
| 0 | 0 | 0 | 0 |

诚实边界：≥80% CRC-OK 保持到 **8 dB**；~6 dB 起崩解；0 dB 静默。
`crc16("123456789")==0x29B1`；篡改消息体 1 bit → crcOk=false（仍 emit）；
纯高斯噪声 200k 样本 → 0 包；idle 200k → 0 包。

### NAVTEX（8 kHz / 100 bd ±85 Hz，20 trial/点）
| SNR(dB) | decoded | diversity-pass | phasing-ok |
|---|---|---|---|
| 12-8 | 20/20 | 20/20 | 20/20 |
| 7 | 16/20 | 11/20 | 16/20 |
| 6 | 6/20 | 1/20 | 6/20 |
| 5 | 1/20 | 0/20 | 1/20 |
| ≤4 | 0/20 | 0/20 | 0/20 |

诚实边界：**6 dB** 首次跌破 50%；diversity 不纠错只计数；噪声/静音 8 s → 0 报文。

### 回归（本 segment 已构建目标实跑）
- test_agent 23/23、test_tool_registry 8/8（计数 40）、test_tool_schema 10/10、
  test_ai_real_link 17/17（40 工具真实执行）、test_digital_panel_ui 13/13（含
  data_text_panel）——全部 0 failed。
- mbdsdr 主程序重链（strings 含新工具 13 处），`--help` 正常。

## 红线
- 射频频率（ACARS 131.525 MHz、NAVTEX 518/490 kHz）仅存在于 docs，代码零硬编码。
- 不预置 TLE/呼号/电台；诚实空态（0 包/0 报文/空列表）禁 mock。
- 干净室 MIT：协议常量来自公开标准（ARINC 618、ITU-R M.540、CCIR 476、ITA2），
  未复制任何 GPL 源码。
- 全仓无「比赛/competition」字样；工具三通道一致；新工具为只读（write=false），
  写工具手动 gate 规则未变。

## 诚实未完成项
1. 全量 ctest 133 在云端构建尾期尚未跑完（本次先落关键目标回归证据；全量待续跑）。
2. NAVTEX 集成分支喂 12 kHz 采样率，而单测在 8 kHz 验证（12 kHz 场景未独立验证，
   属待验证项）。
3. ACARS/NAVTEX 真机过境/实收 IQ 未验证（离线合成 IQ 全链验证通过）。
4. 解码器 TX 侧帧尾补少量空闲符号（Mueller-Muller 有限缓冲尾效），真实流式无此问题。
