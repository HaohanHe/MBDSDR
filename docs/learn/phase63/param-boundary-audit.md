# Phase63 — MBDSDR 数值参数边界一致性抽查（只读审计 + 落档）

- **HEAD**: `9950cf8`（审计前后未变）
- **仓库根**: `/home/user/Doubao/chats/38438160041798146/MBDSDR`
- **范围**: 47 工具三通道（Agent / ControlHub / HTTP）中的 **6 个数值类工具**，聚焦「越界/非法参数在三通道是 clamp 还是拒绝」的一致性。
- **方法**: 全程只读源码 + offscreen 跑既有测试二进制（`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，clean env，未设 `MBDSDR_TEST_SOURCE`）。未改源码、未 git add/commit/push、未 mock、未操控 GUI。

---

## 0. 命名漂移核对（Agent 注册名 ↔ CH 命令表名）

先消歧：本批工具在两侧**并非同名**。以 Agent `dispatchTable()`（`cpp/src/ai/agent_tools.cpp:1280`）与 CH 命令表（`cpp/src/control/control_hub.cpp:84`）为准：

| 抽查项 | Agent 注册名（executor） | CH 命令名（handler） | 备注 |
|---|---|---|---|
| 中心频率调谐 | `tune_frequency`（`agent_tools.cpp:74` execTuneFrequency） | `tune`（`control_hub.cpp:408` cmdTune） | 不同名；Agent 参数 `freq_hz`，CH 同 |
| VFO 频率 | `set_vfo_frequency`（`agent_tools.cpp:940`） | `vfo_set_freq`（`control_hub.cpp:694`） | Agent 用 `index`（解析为 id），CH 直接用 `id` —— 参数键也漂移 |
| 信道带宽 | `set_bandwidth`（`agent_tools.cpp:203`） | `set_bandwidth`（`control_hub.cpp:453`） | 同名 |
| VFO 带宽 | `set_vfo_bandwidth`（`agent_tools.cpp:1003`） | `vfo_set_bandwidth`（`control_hub.cpp:719`） | |
| 增益 | **Agent 无此工具** | `set_gain`（`control_hub.cpp:431`） | 工具面缺口（见 §4 D5） |
| 采样率 | **Agent 无此工具** | `set_sample_rate`（`control_hub.cpp:421`） | 工具面缺口（见 §4 D5） |
| 噪声抑制(bool) | `set_noise_blanker`（`agent_tools.cpp:703`） | `set_noise_blanker`（`control_hub.cpp:1265`） | 同名 |

**边界常量**（`cpp/src/core/tokens.h`）：
- `kFreqMinHz = 24e6`（:257），`kFreqMaxHz = 1700e6`（:258）
- `kGainMinDb = 0.0`（:260），`kGainMaxDb = 49.6`（:261）
- `kSampleRatesHz = {1.024e6, 2.048e6, 2.4e6, 3.2e6}`（:263）—— **离散列表，非连续区间**
- 带宽预设（`cpp/src/core/bandwidth_preset.h`）：`kBwAmHz=9000 / kBwNfmHz=12500 / kBwSsbHz=2400 / kBwCwHz=500 / kBwDigitalHz=12000 / kBwWfmHz=200000 / kBwAdsbHz=2000000`

---

## 1. 三层边界语义对照表

图例：**拒绝** = `{ok:false,error}` 不下发；**clamp** = 收敛到合法区间并下发有效值；**静默接受** = 不做范围判断直接透传 engine；**类型校验** = 仅拒非预期 JSON 类型。

| 工具 | 合法区间 / 约束 | Agent 层 | ControlHub 层 | Engine 层（最终防线） |
|---|---|---|---|---|
| tune_frequency / tune | [24 MHz, 1700 MHz] | **拒绝**（schema min/max，`tool_schema.cpp:76-77`；校验器 `arguments_validator.cpp:108-119` 越界即拒）；executor 本体 `agent_tools.cpp:77-78` 不查范围 | **clamp**：`std::clamp(f, kFreqMinHz, kFreqMaxHz)`（`control_hub.cpp:412`），回带 `clamped` 标志（:417） | **丢弃 f≤0 / 非有限**（`spectrum_engine.cpp:215`）；**不** clamp 到 [24M,1700M] |
| set_bandwidth（顶层） | Agent=7 枚举预设；CH/Engine=任意正数 | **拒绝**（schema enum 仅 7 预设，`tool_schema.cpp:158-161`；非枚举值拒）；executor `agent_tools.cpp:206-207` 不查 | **静默接受**（`control_hub.cpp:455-456` 直接透传） | **无任何守卫**（`spectrum_engine.cpp:692-699`） |
| set_vfo_frequency / vfo_set_freq | 硬件频率范围（理论 [24M,1700M]） | **静默接受**：schema **无** freq min/max（`tool_schema.cpp:719-724`）；executor 仅查 VFO index 越界（`agent_tools.cpp:951-953`），freq 不查（:955） | **静默接受**：`needInt id` + `needDbl freq`，无范围（`control_hub.cpp:696-698`） | **无范围守卫**：`VfoManager::setFreq` 直接赋值（`vfo_manager.cpp:356-362`） |
| set_vfo_bandwidth / vfo_set_bandwidth | 带宽 > 0 | **拒绝 bw≤0**（`agent_tools.cpp:1012-1013`「必须为正数」） | **静默接受**（`control_hub.cpp:721-723` 直接透传） | `VfoManager::setBandwidth` **内部拒 hz≤0**（`vfo_manager.cpp:375`），但 `SpectrumEngine::vfoSetBandwidth` 返回 void **吞掉 bool**（`spectrum_engine.cpp:908-914`）→ CH 仍回 `ok:true` |
| set_gain | [0, 49.6] dB | （Agent 无此工具） | **clamp**：`std::clamp(g, kGainMinDb, kGainMaxDb)`（`control_hub.cpp:434`），回带 `clamped`（:439） | **无任何守卫**（`spectrum_engine.cpp:236-243` 直接入队） |
| set_sample_rate | Engine: (0, 32 MHz]；推荐 4 档离散 | （Agent 无此工具） | **静默接受**（`control_hub.cpp:423-424` 直接透传） | **丢弃 r≤0 / 非有限 / r>32e6**（`spectrum_engine.cpp:228`）；**不**按 `kSampleRatesHz` 4 档离散校验 |
| set_noise_blanker | `on` 必须 bool | **类型校验拒绝**非 bool（`agent_tools.cpp:706-707`） | **类型校验拒绝**非 bool（`needBool`，`control_hub.cpp:1267`） | 纯 bool 下发（`spectrum_engine.cpp:1085-1087`） |

### HTTP 通道语义

`control_http_server.cpp:306-331`：`POST /command` 只做**信封解析**（要求 `tool` 为字符串、`args` 为对象，否则 400），随后 `hub_->execute(tool, args)`（:331）**原样转发**。HTTP 层**无任何数值边界逻辑**——HTTP 的边界语义 = ControlHub 的边界语义（clamp/拒绝/静默完全继承自 CH handler）。

### Agent 校验的触发前提（重要注记）

Agent 的 schema 校验器 `validateArguments` **只在 LLM function-calling 循环里被调用**（`llm_worker.cpp:247`，失败即 `continue` 不触硬件）。**直接调用** `executeTool`（`agent_tools.cpp:1363`，如测试 / UI 旁路）**跳过 schema 校验**，executor 本体多数只做类型检查。因此上表「Agent 层拒绝」仅在走 LLM 循环时成立；直连 executor 路径会退化为与 CH 类似的「静默接受」。

---

## 2. 差异判定清单

| # | 差异 | 性质 | 判定 | 最小方案 / 理由 |
|---|---|---|---|---|
| **D1** | 同一中心频率 1.8 GHz：Agent `tune_frequency` **拒绝**（schema 越界）；CH `tune` **clamp 到 1700 MHz 并报 `clamped:true`** | 同物不同契约 | **架构性不修** | 两侧设计哲学不同：Agent schema 是给 LLM 的「自纠正」契约（拒绝→模型重试），CH 是给人机/HTTP 的「弹性硬件」契约（clamp+回报）。调用方不同，wire 行为有意分叉。**残留风险**：同一 1.8 GHz 请求经 Agent 报 `ok:false`、经 HTTP 报 `ok:true` 但落到 1700 MHz，有效值按通道不同——文档已记，不改码。 |
| **D2** | `bandwidth=8000`：Agent `set_bandwidth` **拒绝**（不在 7 枚举预设）；CH/HTTP **接受并下发** | 同物不同约束 | **架构性不修** | Agent enum 是给 LLM 的护栏（只允许命名预设），CH 保留任意数值给高级/脚本用户。属「LLM 受限、直连灵活」的有意分层。若要收敛，最小改动是把 Agent schema 从 enum 放宽为 range，但会失去 LLM 护栏价值——不改。 |
| **D3** | `vfo_set_bandwidth` 传 `hz≤0`：Agent **诚实报错** `ok:false`；CH **回 `ok:true` 但实际未下发**（engine 内部拒了、bool 被 void 吞掉） | **诚实契约缺陷** | **该修** | CH `cmdVfoSetBandwidth`（`control_hub.cpp:719`）在透传前补一道与 Agent 对齐的下限判断：`if (hz <= 0.0) return errResult("带宽必须为正数");`（对齐 `agent_tools.cpp:1012-1013`）。消除「CH 声称成功实则 no-op」的静默分歧。 |
| **D4** | Engine `onSetGain`（`spectrum_engine.cpp:236-243`）无守卫、`onSetCenterFreq`（:215）不 clamp [24M,1700M] | 纵深防御缺口 | **架构性不修** | 出厂安全路径上 CH 已 clamp（gain [0,49.6]、freq [24M,1700M]）；直连 engine 的调用方（UI/测试）为受信内部方。非三通道 bug，不在本轮范围改。 |
| **D5** | Agent 面无 `set_gain` / `set_sample_rate` 工具 | 工具面缺口（非边界不一致） | **架构性不修** | Agent 47 工具集有意未暴露增益/采样率旋钮（防止 LLM 乱调前端）；这是产品面裁剪，不是边界语义 bug。记录即可。 |

> 本轮**仅 D3 判定为「该修」**，且给出最小落点（CH 补一道 `hz<=0` 拒绝）。按只读纪律，本次**不落代码修复**，仅登记建议。

---

## 3. 金集实跑计数（offscreen，clean env）

环境：`env -u MBDSDR_TEST_SOURCE LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib QT_QPA_PLATFORM=offscreen`，执行于 `cpp/build` 既有二进制。

| 二进制 | passed | failed | skipped | 耗时 | 退出码 |
|---|---|---|---|---|---|
| `test_agent` | **30** | 0 | 0 | 751 ms | 0 |
| `test_control_hub` | **27** | 0 | 0 | 1440 ms | 0 |
| `test_control_http` | **14** | 0 | 0 | 72 ms | 0 |
| **合计** | **71** | **0** | **0** | — | — |

三金集全绿，无回归。（HTTP 用例中观测到回环端口绑定的 `QWARN` 横幅，属正常提示，非失败。）

---

## 4. 红线扫描结果

| 红线项 | 结果 |
|---|---|
| 改源码 | ✅ 未改。`git status --short` 仅 3 个**既有**未跟踪隔离文件：`cpp/scratch/regen_tool_doc`、`cpp/scratch/regen_tool_doc.cpp`、`cpp/tests/ui_diag_freeze.cpp`（未触碰，未误判并行会话在途改动） |
| git add / commit / push | ✅ 未执行 |
| 「比赛 / competition」字样（cpp src/tests） | ✅ 无命中 |
| mock / 操控 GUI | ✅ 未 mock；offscreen 纯跑既有测试二进制，未起 GUI |
| HEAD | ✅ 审计前后均为 `9950cf8` |

---

## 5. 诚实未完成项

1. **D3 仅登记、未修**：按只读纪律未落地代码修复；建议落点 `control_hub.cpp:719`，需后续写会话改码并补一条 CH `vfo_set_bandwidth` 传 `hz≤0` 的 `ok:false` 用例。
2. **未实测 engine 直连路径的越界行为**：Engine 层守卫结论（gain 无守卫 / center-freq 不 clamp）来自源码阅读，未在 offscreen 实际触发 NaN/超高频喂给 engine（避免污染既有 engine 状态）。
3. **Agent schema 校验 vs 直连 executor 的分叉**仅在 LLM 循环路径上验证为「拒绝」；直连 `executeTool` 的退化行为未单独跑用例，系源码推断（`llm_worker.cpp:247` / `agent_tools.cpp:1363`）。
4. **`kSampleRatesHz` 4 档离散**在三层均未被强制校验（Engine 只查 (0,32M]），是否需要按离散白名单收敛未在本轮判定为该修，留待采样率专项评估。
