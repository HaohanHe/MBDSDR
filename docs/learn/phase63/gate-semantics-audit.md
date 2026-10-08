# 写工具 Gate 三通道语义一致性抽查（只读审计 + 落档）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），审计基线 HEAD = `7ad9a8b`（main）。
- 交付方式：**只读落档**。仅新增本文件；跑了既有测试二进制（offscreen），未改任何 `cpp/src/**`、未改任何测试、未 `git add/commit/push`。
- 环境：`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，复用 `cpp/build` 既有二进制（未增量重编译）。
- 上轮基线：`docs/learn/phase63/tool-golden-47-check.md`（HEAD `6a7ad7b`，106/106 金集绿）。本轮聚焦 **Agent / ControlHub / HTTP 三通道写工具 gate 的语义一致性**，不重跑全量 47，只跑与 gate 直接相关的三个金集（§1 实测）。

## 0. 结论摘要

- **三通道 gate 语义同构：写拦 `{ok:false,gated:true,error}`、读放行、引擎零改动。CH↔HTTP 字节级逐字透传（零漂移）；Agent 与 CH 共享同一信封三元组，error 文案按通道区分属有意设计。**
- **ControlHub gate 是单点判定**：全 `cpp/src` 唯一 gate 谓词为 `control_hub.cpp:360`；43 个写命令无一 handler 自带第二处 gate，`write` 标志与 handler 同行声明，表与门不会漂移。
- **HTTP 无独立 gate 判定**：`POST /command` 一律 `hub_->execute()` 委托（`control_http_server.cpp:331`），gate 自动继承，注释明示"we just relay them"（:330）。
- **差异判定：零新缺陷 → 零待修**（明细见 §5）。仅有一条"引擎未 attach 时门序"的架构性观察（§5.3），不修。
- 红线扫描：**CLEAN**（`ghp_` 0 真实 token；`competition/比赛/赛事` 在 `cpp/src/ai`、`cpp/src/control` 0 命中）。

### 0.1 金集实跑（gate 相关三套件，本次独立执行）

| 金集测试 | 真实 passed | failed | 关键 gate 槽结果 |
|---|---|---|---|
| `test_agent` | **30** | **0** | `manualMode_gateSpotCheckAllWrites` PASS（29 写全 gated / 18 读全放行 / 后端 16 字段 byte 不变）、`noiseBlankerLandReadbackAndGate` PASS |
| `test_control_hub` | **27** | **0** | `readAlwaysAllowedWriteGateBothStates`、`exportIqSegment_gatedWhenWriteGateClosed`、`phase26WritesAreGatedAndBadArgsHonest`（含 set_squelch/set_noise_blanker）、`set_vfo_armed` 门关闭不落地 全部 PASS |
| `test_control_http` | **14** | **0** | `gateClosedRefusesWritePost`（tune 拦 + 频率不变）、`postCommandNoiseBlankerRoute`（门关闭写不落地）全部 PASS |
| **合计** | **71** | **0** | — |

> 运行期告警仅 `no librtlsdr ops bound; stub start()`（offscreen 无硬件，QINFO）与 HTTP 回环横幅（QWARN），均非 FAIL，不影响断言。

## 1. Agent 侧 gate 基线

- **判定点**：`cpp/src/ai/llm_worker.cpp:113-123` `LLMWorker::dispatchToolCall()`。
  - :119 `if (manualMode && isWriteTool(name)) return gatedToolResult(name);` —— **在进 `executeTool` 之前直接返回**，引擎零触碰（:116-118 注释明示 "Skip executeTool entirely (no engine touch)"）。
- **写/读分类**：`cpp/src/ai/agent_tools.cpp:1337-1343` `isWriteTool()` —— 直接读声明表 `registeredToolSpecs()` 的 `s.write` 字段，**不另建并行写集合**；未知/读返回 false（永不误拦）。
- **拦截信封**：`cpp/src/ai/agent_tools.cpp:24-30` `gatedToolResult()` = `{"ok":false,"gated":true,"error":"手动模式：未执行 <name>"}`（紧凑 JSON）。
- **开关**：`manualMode_`（AI 会话级，`Agent::setManualMode` 持久化到 QSettings `aiManualMode`，见 `test_agent.cpp:210-234`）。
- **引擎零改动证据**：`test_agent.cpp:674-779` `manualMode_gateSpotCheckAllWrites` —— 遍历声明表本身（不硬编码名单），29 写逐字断言 `"gated":true` + `"ok":false` + "手动模式：未执行 <name>"；18 读全部不含 `"gated"`；跑完后频率/模式/带宽/VFO/静噪/noise_blanker/录制/FFT/QSettings ppm/色板 **16 个观测字段 byte-for-byte 不变**（:763-778）。`QCOMPARE(writes,29)/(reads,18)` 冻结。

## 2. ControlHub 侧 gate

### 2.1 单点判定（全 src 唯一）

- **命令表**：`cpp/src/control/control_hub.cpp:81-158` `ControlHub::table()`。每行 `{name, write, &handler}`，**`write` 标志与 handler 在同一行声明**（:76-80 注释：dispatch 表与门永不漂移）。
  - 写命令行：:84-127，grep `, true,` **实测 43 行**；读命令行：:129-155，grep `, false,` **实测 23 行**。合计 66。
- **唯一 gate 谓词**：`control_hub.cpp:360`（execute() 第 3 步）：
  ```cpp
  if (row->write && !writeEnabled_.load()) {
      QJsonObject o = gatedResult(command);   // :283-289
      emit commandExecuted(command, false);
      return compact(o);
  }
  ```
- **覆盖方式 = 单点**，不是逐 handler。全 `cpp/src` grep `writeEnabled_.load()/row->write` 仅命中 :360（+ `control_hub.h:103` 只读 getter）。**43 个写 handler 无一自带第二处 gate**——它们假设门已在 execute() 放行，直接动引擎（例：`cmdSetNoiseBlanker` :1265-1273、`cmdSetSquelch` :1233-1253 体内无任何 gate 复查）。
- **拦截信封**：`control_hub.cpp:283-289` `gatedResult()` = `{"ok":false,"gated":true,"error":"写入被禁止（write gate 关闭）：未执行 <command>"}`。
- **开关**：`writeEnabled_` 原子量，`setWriteEnabled(bool)`（`control_hub.h:102`），默认 `tokens::kControlHubWriteEnabledDefault = true`（`tokens.h:1050`；类内初始化 `{true}` control_hub.h:149）。

### 2.2 抽查 5 个写命令（manualMode/gated 语义）

| 命令 | CH 表行 | handler 行 | 门关闭时是否 `{ok:false,gated:true}` | 证据 |
|---|---|---|---|---|
| `tune` | :84 | `cmdTune` :408 | **是**（经单点 :360） | HTTP 实测 `test_control_http:205-234` |
| `set_vfo_frequency` | **CH 无此名** | — | 命名漂移：CH 行是 `vfo_set_freq` :104 → `cmdVfoSetFreq` :694 | 上轮已登记漂移对（见 §5.4） |
| `start_recording` | :92 | `cmdStartRecording` :530 | **是**（经单点 :360） | 表上 `write=true`，同属 43 单点覆盖 |
| `set_squelch` | :114 | `cmdSetSquelch` :1233 | **是** | `test_control_hub:756` 门关闭循环逐个 gated:true |
| `set_noise_blanker` | :115 | `cmdSetNoiseBlanker` :1265 | **是** | `test_control_hub:757` 门关闭 gated:true；门开后真落引擎（:537-552） |

> 说明：5 个抽查目标里 `set_vfo_frequency` 在 CH/HTTP 通道不存在（CH 叫 `vfo_set_freq`），属已登记的 4 对命名漂移之一，非本轮新缺陷。其余 4 个全部同语义拦截。

## 3. HTTP 侧 gate

### 3.1 POST /command 统一委托（无独立 gate）

- `cpp/src/control/control_http_server.cpp:306-332`：`POST /command` → 解析 body 取 `tool`/`args` → **:331 `return hub_->execute(tool.toString(), args).toUtf8();`**。
- **无任何 per-route handler、无任何 HTTP 层 gate 判定**。:329-330 注释明示："Unknown command / bad args / **gate-closed** are ALL honest results from execute() itself (ok:false, gated:true, error:...); **we just relay them**."
- grep HTTP 层 `gated/writeEnabled` 仅命中 3 处注释（:275/:305/:330），**0 处独立判定**。→ **gate 语义从 ControlHub 逐字节继承**，CH 信封字符串原样透传。
- GET 只读快照（:284-303 `/status`、pocsag/m17/vor/acars/navtex）同样走 `hub_->execute()`，读命令天然不被门拦。

### 3.2 三个代表性写命令 HTTP 实测（既有二进制）

| 路由 | 用例 | 门关闭实测 | 证据 |
|---|---|---|---|
| `tune` (W) | `gateClosedRefusesWritePost` | HTTP 200 但 body `ok:false`+`gated:true`，GET /status 频率与基线逐分不差 | `test_control_http:217-228` |
| `set_noise_blanker` (W) | `postCommandNoiseBlankerRoute` | 门关闭 `gated:true`，且引擎 `noiseBlankerEnabled()` 仍为 true（未落地） | `test_control_http:533-540` |
| `get_status`/读 (R) | 同槽 | 门关闭时读快照仍 `ok:true`（读永不拦） | `test_control_http:230-233`、`test_control_hub:763-764` |

## 4. 新增工具三通道 gate 语义对齐表

> noise_blanker 对为**桌面三通道（Agent/CH/HTTP）**独有；squelch 对为**跨平台（桌面三通道 + mobile）**。mobile 仅 10 工具集，无 noise_blanker。

### 4.1 `set_noise_blanker`(W) / `get_noise_blanker_status`(R) —— 桌面三通道

| 通道 | 写 set_noise_blanker 门关闭 | 读 get_noise_blanker_status | 证据 file:line |
|---|---|---|---|
| Agent | gated:true（manualMode） | 不被拦、回读真实 enabled | 写表 `tool_schema.cpp` `write=true`；`test_agent:816-818`；读回 `:893-907` 槽 `noiseBlankerLandReadbackAndGate` |
| ControlHub | 单点 :360 拦，信封 :283 | 表行 :151 `write=false` 直接放行，`cmdGetNoiseBlankerStatus` :1275 | 表行 写:115 / 读:151；`test_control_hub:757` |
| HTTP | POST /command → execute 透传 gated:true，引擎不动 | 同委托，读回 enabled | `control_http_server.cpp:331`；`test_control_http:532-540` |

### 4.2 `set_squelch`(W) / `get_squelch_status`(R) —— 桌面三通道 + mobile

| 通道 | 写 set_squelch 门关闭 | 读 get_squelch_status | 证据 file:line |
|---|---|---|---|
| Agent | gated:true | 不被拦 | `test_agent:708`(args)、:743 读放行 |
| ControlHub | 单点 :360 拦 | 表行 :150 `write=false` 放行，`cmdGetSquelchStatus` :1255 | 表行 写:114 / 读:150；`test_control_hub:756` |
| HTTP | POST /command 透传 | 同委托 | `control_http_server.cpp:331` |
| mobile（Dart） | `mutatingTools` 硬编码集**含 set_squelch**(:486)，manualMode 时包成 `_gated` stub | 读工具不在 mutate 集，原 executor 放行 | `mobile/lib/app/ai_tools.dart:35-40`(_gated 信封)、`:479-496`(wrapper)、读回字段 :354-358 |

mobile `_gated` 信封（:35-40）= `{"ok":false,"gated":true,"error":"手动模式：未执行 <name>","hint":"当前为手动模式…"}` —— 与桌面 Agent 信封**三元组同构**，多一个 `hint` 展示字段（超集，不破坏语义）。

## 5. 差异判定清单

**零新缺陷 → 零待修。** 逐条：

1. **CH ↔ HTTP：零差异。** HTTP 层无独立 gate，POST /command 委托 `hub_->execute()`（:331），CH 单点门 + 信封字符串逐字节透传。架构上不可能漂移。
2. **Agent ↔ CH：信封同构，文案按通道区分（有意，非漂移）。** 三者共享 `{ok:false, gated:true, error}` 三元组；error 前缀 Agent="手动模式：未执行" / CH="写入被禁止（write gate 关闭）：未执行"——人读文案告诉操作员是哪道门拦的，机器可断言的 `ok/gated` 键一致。
3. **【架构性不修】门序观察：引擎未 attach 时 CH 先报"无引擎"。**
   - 位置：`control_hub.cpp` execute() 顺序 = 查命令(:345) → **无引擎检查(:353)** → **写门(:360)**。即"无引擎 + 门关闭 + 已知写命令"时返回 `{ok:false,error:"无引擎连接…"}`，**不带 `gated:true`**。
   - Agent 侧 `dispatchToolCall` 在 :119 先返 gate，引擎空指针路径在其后。
   - **判定：不修。** 两者都是"不碰硬件的诚实拒绝"；仅在"系统自身无引擎"的错误态下，是否带 `gated:true` 有别。此时告诉操作员"无引擎"比"门关闭"更贴近真因，属合理门序。正常运行引擎恒 attach，不影响任何在线语义。
4. **【已登记，非新缺陷】`set_vfo_frequency` 命名漂移。** CH 表无此名，对应 `vfo_set_freq`（:104）。与上轮 `tool-golden-47-check.md §2.1` 登记的 4 对漂移一致（`set_vfo_frequency↔vfo_set_freq`），已被金集冻结，不重复展开。
5. **mobile 门 = 硬编码 7 动作 allowlist**（`ai_tools.dart:479-487`），桌面 = 数据驱动 29 写。两者服务不同工具面（mobile 仅 10 工具），`set_squelch` 已正确在 mutate 集内、`get_squelch_status` 正确放行——非缺陷。

## 6. 红线扫描

| 项 | 范围 | 结果 |
|---|---|---|
| `ghp_`（GitHub token 泄露） | `cpp/src/**`、`mobile/lib/**`、`docs/learn/phase63/**` | **0 真实 token** |
| `competition` / `比赛` / `赛事`（竞技措辞） | `cpp/src/ai/**`、`cpp/src/control/**` | **0 命中** |

## 7. 诚实未完成项与边界

- **二进制↔源码一致性**：本轮复用 `cpp/build` 既有二进制（未重编译）。HEAD `7ad9a8b` 是**纯文档提交**（仅改 `docs/learn/phase63/` 两个 md，零 `cpp/src` 改动），故二进制编译的 cpp/src 与 HEAD 完全一致；该一致性由"开跑前后我自己的 git status 仅新增本 md"佐证。
- **HTTP 66 命令自动覆盖为架构断言**：统一委托、无 per-route handler，测试只钉代表路由（tune / noise_blanker / vfo_armed / capabilities / recording_state），未枚举跑全 66 条——与上轮口径一致。
- **观察到他会话在途改动（已按纪律不触碰）**：本次审计中途，工作树陆续出现一批**非我所改**的已跟踪文件修改——`cpp/src/core/tokens.h`、`cpp/src/ui/spectrum_display.*`、`cpp/src/ui/spectrum_widget.*`、`cpp/tests/test_spectrum_display.cpp` 等（开跑前 `git status` 并无这些 M，且数量在审计过程中持续增加，印证是活跃并发会话）。我未编辑、未 revert、未 stage、未 commit 任何一个，原样留待其所属会话收尾。本审计的全部源码结论基于我读取时的快照行号；这些在途文件与 gate 三通道无关（gate 集中在 `ai/llm_worker.cpp`、`ai/agent_tools.cpp`、`control/control_hub.cpp`、`control/control_http_server.cpp`，均不在上述被改文件内）。
- 未触碰 `cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp` 任何隔离文件；未执行任何 `git add/commit/push`。
