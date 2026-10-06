# Phase 54 块3：接收能力工具化——桌面 C++ 三通道核对

HEAD = 704e035。构建目录 cpp/build，Qt 6.8.2，offscreen。所有 file:line 为本机核实，不虚构。

## 架构事实核对（含对任务背景的诚实修正）

任务背景称"桌面 C++ 没有 PLL/AFC/notch/resync/软判决"。**审计后须修正一处**：

- C++ 引擎**有**一个活的 Costas 载波恢复环 + AGC + Mueller-Muller 定时，位于
  `cpp/src/dsp/digital_demod.h`（Costas loop: 第 14、120-125 行；AGC: 第 13、118 行；
  MM 定时: 第 19、64 行；差分译码: 第 20、96-105 行；硬判决: 第 17、109 行）。
  它经 `cpp/src/dsp/vfo_manager.cpp:48` `std::make_unique<DigitalDemod>(cfg)` 真接入引擎链。
- **但它是自动内循环 DSP，不是用户可控开关**：选 BPSK/QPSK 模式即自动锁定，无"开/关"控件，
  也没有对应工具。用户面对的"接收能力开关"在 C++ 侧确实只有 DopplerStepLimiter。
- Python 链（`mbdsdr_ai/ssdv_phy.py`）另有：自适应 AFC（93-110 行）、二阶 PLL（161 行）、
  notch（93 行）；`coarse_sync.py` 粗同步；`ccsds_rx.py` Viterbi/CCSDS。这是 SSDV/图像接收路径，
  与 C++ BPSK 遥测路径是两条独立基带链。

结论：背景的意图（"C++ 用户可控接收开关只有多普勒"）成立，但"桌面 C++ 没有 PLL"表述不准——
C++ 有 Costas PLL，只是它是自动 DSP 而非工具化开关。

---

## C++ 真实接收控制三通道对照

三通道分发都作用于 `dsp::SpectrumEngine*`（ControlHub 持 `engine_`；executeTool 签名
`(name, args, SpectrumEngine*)`；HTTP POST /command 委托 ControlHub）。多普勒补偿活在
MainWindow sky-tab，**不被三通道触达**。

| 接收控制 | C++ 位置 | ControlHub | HTTP JSON | Agent 工具 | 备注 |
|---|---|---|---|---|---|
| **多普勒自动补偿** | `main_window.cpp:1207` dopplerCompChk_；`sat_capture.h:135` DopplerStepLimiter | ✗ | ✗ | ✗ | UI 实时 1Hz 循环，需 station+捕获过境；三通道够不到 MainWindow 状态 |
| Costas PLL/AGC/MM 定时（自动 DSP） | `vfo_manager.cpp:48` → `digital_demod.h` | n/a | n/a | n/a | 自动内循环，无开关可暴露 |
| 静噪 | `spectrum_engine.h:145-146` setSquelch* | ✓ set_squelch_enabled/threshold | ✓（POST /command） | ✓ set_squelch | 三通道齐 |
| 中心频率 | `spectrum_engine.h:141` onSetCenterFreq | ✓ tune | ✓ | ✓ tune_frequency | 三通道齐 |
| 解调模式 | `spectrum_engine.h:144` setDemodMode | ✓ set_mode | ✓ | ✓ set_mode | 三通道齐 |
| 带宽 | `spectrum_engine.h:147` setBandwidth | ✓ set_bandwidth | ✓ | ✓ set_bandwidth | 三通道齐 |
| ANR 自动降噪 | `spectrum_engine.h:240` setAnrEnabled | ✓ set_anr | ✓ | ✗（不在 35） | ControlHub 有，Agent 精选子集未含 |
| Tuner/RTL AGC | `spectrum_engine.h:285-286` setRtlAgc/setTunerAgc | ✓ set_tuner_agc/set_rtl_agc | ✓ | ✗ | 同上 |
| 增益 | `spectrum_engine.h:143` onSetGain | ✓ set_gain | ✓ | ✗ | 同上 |
| PPM/频率校正 | `spectrum_engine.h:291` setPpm | （经 tune） | ✓ | ✓ calibrate_frequency / apply_frequency_correction | Agent 工具体 |

### 补了什么 / 什么本就有
- **本就有**：静噪、中心频率、解调模式、带宽三通道齐；ANR/AGC/增益在 ControlHub（HTTP 经
  POST /command 同样可达）。
- **未补新工具**：
  1. 多普勒补偿——三通道执行器只接 `SpectrumEngine*`，够不到 MainWindow 的
     `stationSet_/capturedIdx_/dopplerLimiter_` 与 1Hz `updateLiveSatellite` 循环。补它需要新建
     跨层桥接（engine→MainWindow），该桥不存在；任务红线"不虚构跨语言桥接"同理，不造伪桥。
     其效果（补偿后 VFO 重调）已通过 get_status 的中心频率可观测。
  2. Costas PLL——自动内循环，无用户开关，无可暴露的"控制"。
  3. ANR/AGC/增益——ControlHub 已有（HTTP POST /command 可达）；Agent 35 工具是 AI 助手精选
     子集，设备级开关不强行塞进 agent 面是设计取舍，非缺陷。

### 手动门控对齐
write 门控从声明式 spec（ToolSchemaSpec::write）读取（`agent_tools.cpp:897` isWriteTool），
无并行硬编码集合。多普勒补偿不是工具，不涉及门控；现有 write 工具门控路径不变。

---

## Python-only 能力边界（如实标注）

以下是 onboard Python 链开关，经 CLI / Python Agent 调用，**桌面 ControlHub/HTTP 不暴露，
C++ 侧无对应实现**：

| 能力 | Python 位置 | 说明 |
|---|---|---|
| 自适应 AFC | `mbdsdr_ai/ssdv_phy.py:93-110` | 滑窗平方环频偏估计+限幅冻结 |
| 二阶 PLL | `mbdsdr_ai/ssdv_phy.py:161` | 载波相位跟踪 |
| notch 陷波 | `mbdsdr_ai/ssdv_phy.py:93` | 具名门限常量 |
| 粗同步 | `mbdsdr_ai/coarse_sync.py` | 帧同步 |
| 软判决/Viterbi/CCSDS | `mbdsdr_ai/ccsds_rx.py` | 译码链 |

边界：这些是 SSDV/图像接收路径的基带恢复，跑在 Python demodulator 里；桌面三通道
（ControlHub/HTTP/Agent function-calling）**不暴露、不桥接**它们。C++ 侧唯一的用户可控
接收开关是多普勒补偿（且仅限 UI，未工具化）。

---

## 工具计数（如实）
- C++ 桌面 Agent 工具数：**35**（未变）。
- 本轮未为真实 C++ 控制新增工具（理由见上：多普勒需跨层桥接、PLL 自动 DSP、ANR/AGC 已在
  ControlHub）。计数保持基线。

## ctest 全量回归（offscreen）
```
100% tests passed, 0 tests failed out of 129
128 passed + e2e_smoke Skipped (SKIP_RETURN_CODE 77)
```
基线 129 不回归。本轮未改 C++ 源码（仅审计只读），ctest 为现状复测。

## 未解决项
- 多普勒补偿若要进三通道，需新建 engine→MainWindow 的显式桥接（暴露 dopplerLimiter_ 状态与
  rearm/requestReturnToZero），这是跨层架构改动，本轮未做（避免虚构桥接）。
- Costas PLL 的锁定状态（carrierLocked/EVM）目前仅在星座图视图本地计算（constellation_view.cpp:149），
  未经 get_spectrum_status 透出；若需工具化只读状态，需在 engine 加访问器（本轮未做）。
