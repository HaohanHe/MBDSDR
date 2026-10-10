# 仪器上手门槛消融：Agent 对话内「一句话测驻波」

> 产品内真实功能（非独立 demo）。用户在 AI 对话框直接问天线驻波，App 经真实
> NanoVNA 三通道工具执行，按真实串口状态驱动回复；未连接/未校准给真实可达引导。
> 全仓 MIT；Replay 仅作测试夹具（标注合成），产品路径面对真实串口、不内置假数据。

## 1. 链路（file:line）

- UI 输入：`main_window.cpp` aiInput_ → sendBtn → `agent_->sendMessage()`（见线 B 笔记）。
- 离线确定性路径：未配 API Key 时 `Agent::localCommand(input)`（`cpp/src/ai/agent.cpp`）。
  新增「驻波/VSWR/天线」意图分支：识别即调 `runVswrOnramp(engine, input)`。
- 编排：`cpp/src/ai/vna_onramp.cpp runVswrOnramp`：
  1. `executeTool("get_vna_status")`（真实工具，留审计轨迹）；
  2. 连通性以 `engine->vnaClient().isConnected()` 为准（注：该工具 JSON 的 `connected`
     字段被 addSourceFields 覆盖为 SDR 源连接，不可直接信）；
  3. 已连接则 `readCalStatus()` 主动刷新校准，无 `cal'ed` 即未校准；
  4. 否则 `planSweep` 围绕目标频率 → `set_vna_sweep` → `get_vna_data` → 在
     freqs/vswr 平行数组取目标最近点 → `interpretVswr` 判定 → `renderReport` 回复。
- 纯函数：`cpp/src/ai/vna_onramp.{h,cpp}`——`isVswrIntent/parseTargetMhz/classifyVswr/
  planSweep/nearestIndex/interpretVswr/renderReport`。阈值 `kVswrGoodMax=1.5 / kVswrOkMax=2.0`。

## 2. 四分支行为

| 状态 | 触发 | 回复要点 |
|---|---|---|
| disconnected | 未插 USB | 引导：插 PORT 1/S11、VID:PID 0483:5740、Linux dialout 组/udev |
| uncalibrated | 已连无 cal | 提示先做 OSL（open/short/load）校准 |
| good | VSWR<1.5 | 读数 + 匹配良好 |
| ok | 1.5–2.0 | 可用，提示接头/环境 |
| bad | >2.0 | 驻波偏高，给排查顺序 |

目标频率从问题解析（如 438.5MHz），不硬编码默认；无频率则反问。

## 3. 测试

`cpp/tests/test_vna_onramp.cpp`：自造 ReplayTransport 注入 `engine.vnaClient().attachTransport()`，
覆盖四分支 + 目标频点选取 + 纯函数阈值 + 无频率反问。**11 passed**。
`test_agent` 38 passed（无回归）。回放数据为合成协议文本，非真实测量。

## 4. 待办（下一批，依赖线 B vna_rf）

- 晶体谐振 fr/Q、TDR 电缆长度/损耗、LC 等「一句话」意图，待线 B 的
  `analyze_vna_resonance`/`vna_tdr_cable` 工具落地后，在 localCommand 经 executeTool 接入。
- VNA tab 数值读数（fr/Q/TDR）与 Smith/Polar/TDR 曲线留后续批次。
- 真机未在环：connectSerial 握手/取数待用户插 H/H4 回传。

## 5. CMake 待加入清单（交统一编辑）

- core 源：`src/ai/vna_onramp.cpp`（紧随 `src/ai/agent_tools.cpp` 后）。
- 测试目标：`test_vna_onramp tests/test_vna_onramp.cpp`，链 `mbdsdr_core` +
  `Qt6::Test/Core/Network/Widgets/Multimedia/Concurrent`，`add_test(NAME vna_onramp ...)`。
  （线 B 已在统一 CMake 接好 vna_onramp.cpp 与 test_vna_onramp。）

## 6. 统一 VNA 意图入口（本批扩展）

`isVnaOnrampIntent` 取代单一驻波分支，`runVswrOnramp` 内部分发 `VnaIntent`：

- **驻波/匹配**：现有四分支（接线/OSL/good/ok/bad）。
- **谐振/晶体**：有标称频率 → 窄扫 ±10kHz → `analyze_vna_resonance` → fr/Q/BW/ESR/串并点；
  无标称 → 请用户给出标称频率。
- **TDR 电缆**：识别电缆类型取速度因子（常量表 RG-58/213≈0.66、RG-8X≈0.78、LMR-400≈0.85）；
  未指定 → 列选项不硬编码假设；指定后 `vna_tdr_cable` → 电缆长度/故障距离。
- **L/C**：目标频点由 S11 虚部经 `vna::reactanceToHenries/Farads` 派生（感性 nH/容性 pF）。

诚实空态贯穿：未连接/未校准/无效分析（点数不足、span=0、无反射峰）均给真实引导，不伪造。
测试：`test_vna_onramp` 15 passed；`test_agent` 38 passed 无回归。

## 7. 本批未做（诚实标注）

- VNA tab 数值读数区与三档新快照：线 B 并行改 vna_panel.*，为避免同树冲突本批未动 UI，
  下一批 localCommand 稳定后再接。
- Smith/Polar/滤波器一句话意图与曲线：待 analyze_vna_filter 交付后下轮。
- 真机未在环，待用户插 H/H4 回传。
