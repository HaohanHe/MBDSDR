# Phase63 — MBDSDR 只读工具（get_*）语义一致性抽查（只读审计 + 落档）

- **HEAD**: `bda5e48`（审计前后均未变）
- **仓库根**: `/home/user/Doubao/chats/38438160041798146/MBDSDR`
- **范围**: 任务点名的 6 个只读项 `get_status / get_rssi / get_spectrum_status / get_noise_blanker_status / get_squelch_status / get_capabilities`，聚焦「无设备 / 断连 / 未初始化」三态下，Agent / ControlHub / HTTP 三通道的返回形状与字段一致性，并下钻引擎真实状态源。
- **方法**: 全程只读源码 + offscreen 跑既有测试二进制（`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，clean env，未设 `MBDSDR_TEST_SOURCE`）。未改源码、未 git add/commit/push、未 mock、未操控 GUI。

---

## 0. 命名漂移核对（Agent 注册名 ↔ CH 命令名 ↔ HTTP）

以 Agent `dispatchTable()`（`cpp/src/ai/agent_tools.cpp:1280`）与 CH 命令表（`cpp/src/control/control_hub.cpp:84`）为准：

| 抽查项 | Agent 注册名（executor） | CH 命令名（handler） | HTTP | 备注 |
|---|---|---|---|---|
| 状态 | `get_status`（`agent_tools.cpp:216` execGetStatus） | `get_status`（`control_hub.cpp:806` cmdGetStatus） | `GET /status` → `execute("get_status")`（`control_http_server.cpp:284-286`）；亦可 `POST /command` | **同名** |
| 信号强度 | **两侧均无此工具** | **两侧均无此工具** | 无 | 见 §0.1：`get_rssi` 不存在 |
| 频谱 | `get_spectrum_status`（`agent_tools.cpp:1153`） | `get_spectrum_status`（`control_hub.cpp:1487`） | 仅 `POST /command`（无专用 GET） | 同名，但**返回字段 key 漂移**（见 D2） |
| 噪声抑制 | `get_noise_blanker_status`（`agent_tools.cpp:720`） | `get_noise_blanker_status`（`control_hub.cpp:1282`） | 仅 `POST /command` | 同名同 key |
| 静噪 | `get_squelch_status`（`agent_tools.cpp:686`） | `get_squelch_status`（`control_hub.cpp:1262`） | 仅 `POST /command` | 同名同 key，但**值漂移**（见 D1） |
| 能力 | `get_capabilities`（`agent_tools.cpp:1220`） | `get_capabilities`（`control_hub.cpp:894`） | 仅 `POST /command` | 同名同 key |

### 0.1 `get_rssi` 不存在（重要前置事实）

全仓 grep `rssi`（`cpp/src`）证实：**没有任何 Agent executor、CH handler、HTTP 路由名为 `get_rssi`**。RSSI 仅以三条非工具路径存在：

1. 引擎 getter `SpectrumEngine::rssiDbfs()`（`spectrum_engine.h:75`，原子 `rssiDbfs_`）；
2. 引擎信号 `rssiLevel(float)`（`spectrum_engine.h:382`）→ UI `MainWindow::onRssiLevel`（`main_window.cpp:4764`）喂 S 表 / 状态条 / RSSI 趋势；
3. 断连 UI 用 `engine_ ? engine_->rssiDbfs() : -100.0f`（`main_window.cpp:3582`）。

CH 的遥测槽 `onTelemetry(name, connected, centerHz, sampleRateHz, gainDb)`（`control_hub.cpp:209`）**签名里根本没有 rssi 入参**，`TelemetrySnapshot`（`control_hub.h:67`）也无 rssi 字段；`cmdGetTelemetry`（`control_hub.cpp:858-878`）回包无 rssi key。**结论：RSSI 是 UI-信号域值，从未进工具回包面。** 本轮把它作为「工具面缺口」登记（见 D4），不编造一个不存在的工具去对照。

### 0.2 三通道信封约定（系统性差异，非 bug）

- **Agent**：executor 一律 `{ok:true, ...业务字段}`，并经 `addSourceFields()`（`agent_tools.cpp:58-62`）追加 `connected(bool)/test_signal(bool)/source(string)` 三字段；`get_status` 手工追加同样三字段（`:225-227`）。
- **CH**：handler 一律 `okBase()`=`{ok:true}`（`control_hub.cpp:270-274`）+ `o["command"]="<名>"`，**不**追加 connected/test_signal/source（除 get_status/get_telemetry/get_capabilities 自带）。
- **HTTP**：纯透传，`control_http_server.cpp:331` `POST /command` = `hub_->execute(tool,args)`，`GET /status`（`:286`）= `execute("get_status")`。HTTP **不改写字段、不加 envelope**，因此 **HTTP 返回形状 == CH 返回形状逐字节一致**。下文 HTTP 列不再单列，直接记「≡ CH」。

---

## 1. 引擎真实状态源（无设备 / 未初始化时的字段真值）

下钻 `cpp/src/dsp/spectrum_engine.{h,cpp}` 与 `device_capabilities.h`，取「fresh engine + NullSource + 从未流式」时各 getter 的真值（这是三态对照的锚点）：

| 引擎 getter | 无设备真值 | 出处 |
|---|---|---|
| `centerFreq()` | `0.0`（`source_` 空 → `source_->centerFreq()` 缺省 0，无 pending） | `spectrum_engine.cpp:174-175` |
| `demodMode()` | `"NFM"` | `spectrum_engine.h:568` |
| `bandwidth()` | `12500.0` | `spectrum_engine.h:569` |
| `fftSize()` | `2048` | `spectrum_engine.h:558` |
| `windowType()` / `averageMode()` | powerSpectrum 枚举默认 0 | `spectrum_engine.cpp:1096-1097` |
| `noiseBlankerEnabled()` | `false`（`noiseBlanker_.enabled_` 默认 false） | `spectrum_engine.cpp:1095`；`noise_blanker.h:20` |
| `squelchEnabled()` | `false`（`squelch_.mode()` 默认 `Mode::Off`） | `spectrum_engine.cpp:259-260`；`squelch.h:36` |
| `squelchOpen()` | `false`（`open_` 默认 false） | `spectrum_engine.cpp:262`；`squelch.h:37` |
| `squelchThresholdDb()` | `-50.0` | `spectrum_engine.h:623` |
| `squelchAuto()` | `false` | `spectrum_engine.h:626` |
| `rssiDbfs()` | `-100.0f`（**不是 NaN**；仅 run 循环产出一帧后才被真实值覆盖，`spectrum_engine.cpp:1414`） | `spectrum_engine.h:562` |
| `digitalLockStatus()` | carrierLocked=false / symbolLocked=false / evm=0.0 | `digital_demod.h:69-71` |
| `sourceCapabilities()`（无设备） | `noDeviceCapabilities()`：connected=**false**，deviceName=`"RTL-SDR 未连接"`，tunable/sample 范围全 `0.0`，gains 空，provenance=`"无真实设备连接"` | `device_capabilities.cpp:67-73`；`spectrum_engine.cpp:187` |
| `availableGainsDb()`（无设备） | 空 vector | `spectrum_engine.cpp:188` |
| `isTestSignalActive()`（无设备） | `false`（NullSource 不是 TestSignalSource） | `spectrum_engine.cpp:609-614` |
| CH `TelemetrySnapshot`（未流式） | available=**false**, connected=false, centerHz=0, sampleRateHz=0, gainDb=0, squelchOpen=false, recording=false, lastError=`""`, dropped=false | `control_hub.h:67-82`；`control_hub.cpp:190`（attach engine 时 snap 重置） |

> 三态映射：**未初始化** = fresh engine 未流式（`snap.available=false`）；**无设备** = NullSource 稳态（与未初始化在工具回包上等价，仅 provenance 措辞不同）；**断连** = 曾流式（`snap.available=true`）后设备丢失，`onSourceDropped()` 置 `dropped=true`（`control_hub.cpp:237-242`）。

---

## 2. 只读工具 × 三态 × 通道返回形状对照表

图例：✓=一致；△=信封/字段名差异但值同源；✗=值/形状实质分歧。`—`=该状态下不出该字段。HTTP ≡ CH（见 §0.2）。

### 2.1 get_status

| 字段 | 无设备/未初始化（Agent） | 无设备/未初始化（CH） | 断连（Agent） | 断连（CH / HTTP） |
|---|---|---|---|---|
| ok | true ✓ | true ✓ | true | true |
| command | —（Agent 无） | `"get_status"` | — | `"get_status"` |
| connected | **false**（=`caps.connected`） | **false**（=`snap.available && snap.connected`） | false（掉设备后 caps 重置为 noDevice） | **false**（`available=true && connected=false`） |
| test_signal | false ✓ | —（CH 无此字段） | false | — |
| source | `"未连接"` | `"无遥测（未连接硬件或引擎未运行）"`（`control_hub.cpp:827-828`） | `"未连接"` | `<上次硬件名>`（`snap.sourceName` 保留） |
| frequency_hz / mode / bandwidth_hz | 0.0 / `"NFM"` / 12500 ✓（同源 engine 取值） | 同左 ✓ | 同左（引擎配置不随链路变） | 同左 |
| carrier_locked / symbol_locked / evm_percent | false / false / 0.0 ✓ | 同左 ✓ | 同左 | 同左 |
| doppler_available / doppler_enabled | false / false ✓（无 UI 控制面） | 同左 ✓ | 同左 | 同左 |
| telemetry_available | —（Agent 无） | **false**（未流式） | — | **true**（曾流式） |
| status（五态） | —（**Agent 无此字段**） | **`"no_telemetry"`**（`control_hub.cpp:841`） | — | **`"dropped"`**（`:843`） |
| error_message | — | `""` | — | `<重连失败真实原因>`（`:849`） |
| readback_center_hz / sample_rate_hz / gain_db | — | —（`if(snap.available)` 跳过，`:850`） | — | ✓ 出现（上次硬件回读值） |
| summary | `"频率=0.000MHz 模式=NFM 带宽=12500Hz 连接=未连接"` | —（CH 无） | 同左措辞（连接=未连接） | — |

**判定**：△ 信封差异 + 缺五态。见 D3。

### 2.2 get_rssi（工具不存在）

三通道均无回包。引擎真值 `rssiDbfs()=-100.0f`（无设备），但不进任何工具回包。见 D4。

### 2.3 get_spectrum_status（配置型，三态取值不变）

| 字段 | Agent（任意态） | CH / HTTP（任意态） |
|---|---|---|
| ok / command | `{ok:true}` | `{ok:true, command:"get_spectrum_status"}` |
| fft_size | `2048` ✓ | `2048` ✓ |
| **window** | key = **`window_type`**（`agent_tools.cpp:1159`） | key = **`window`**（`control_hub.cpp:1491`） |
| **average** | key = **`average_mode`**（`agent_tools.cpp:1160`） | key = **`average`**（`control_hub.cpp:1492`） |
| connected/test_signal/source | 追加（`addSourceFields`） | 不追加 |

**判定**：✗ 字段 key 漂移（值同源 engine getter，均为枚举 int）。见 D2。

### 2.4 get_noise_blanker_status（开关型，三态取值不变）

| 字段 | Agent（任意态） | CH / HTTP（任意态） |
|---|---|---|
| ok / command | `{ok:true}` | `{ok:true, command:"get_noise_blanker_status"}` |
| enabled | `engine->noiseBlankerEnabled()` = **false** ✓ | `engine_->noiseBlankerEnabled()` = **false** ✓（同源） |
| connected/test_signal/source | 追加 | 不追加 |

**判定**：✓ 值完全一致（同一引擎 getter），仅信封差异。无分歧。

### 2.5 get_squelch_status

| 字段 | Agent（**任意态，硬编码**） | CH / HTTP（无设备/未初始化） | CH / HTTP（断连） |
|---|---|---|---|
| ok / command | `{ok:true}` | `{ok:true, command:"get_squelch_status"}` | 同左 |
| enabled | **`null`**（`agent_tools.cpp:691`） | **`false`**（`engine_->squelchEnabled()`，`control_hub.cpp:1265`） | `false`（DSP 配置不随链路变） |
| threshold_db | **`null`**（`:692`） | **`-50.0`**（`:1266`） | `-50.0` |
| auto | **`null`**（`:693`） | **`false`**（`:1267`） | `false` |
| open | **`null`**（`:694`） | **`false`**（`:1268`） | `false`（链路掉后 open 态） |
| note | `"引擎未暴露静噪实时读数接口（后端由 ControlHub 遥测提供）"` | — | — |
| connected/test_signal/source | 追加 | 不追加 | 不追加 |

**判定**：✗ **值实质分歧**——Agent 永远回 `null` 并声称「引擎未暴露读数接口」，但 CH 实际调用的 `engine_->squelchEnabled()/ThresholdDb()/Auto()/Open()` 在 `spectrum_engine.h:83-86` 是**公开 getter**。Agent 的注释已过时。见 D1。

### 2.6 get_capabilities

| 字段 | 无设备（Agent） | 无设备（CH / HTTP） | 断连（两侧） |
|---|---|---|---|
| ok / command | `{ok:true}` | `{ok:true, command:"get_capabilities"}` | 同左 |
| connected | `false` ✓ | `false` ✓ | `false`（caps 重置 noDevice）✓ |
| device_name | `"RTL-SDR 未连接"` ✓ | 同左 ✓ | 同左 |
| tunable_min/max_hz、sample_rate_min/max_hz | `0.0` ✓ | `0.0` ✓ | `0.0` |
| gains_db | `[]` ✓ | `[]` ✓ | `[]` |
| provenance | `"无真实设备连接"`（struct 原值；Agent 仅在 provenance 空时才改写「未连接」，`agent_tools.cpp:1231-1235`） | 同逻辑同值（`control_hub.cpp:914-919`）✓ | 同左 |
| connected/test_signal/source（信封） | 追加 | 不追加 | 同左 |

**判定**：✓ 值完全一致（两侧读同一 `sourceCapabilities()` + `availableGainsDb()`，provenance 判定逻辑逐行对齐），仅信封差异。无分歧。

---

## 3. 差异判定清单

| # | 差异 | 性质 | 判定 | 最小方案 / 理由 |
|---|---|---|---|---|
| **D1** | `get_squelch_status`：Agent 永远回 `enabled/threshold_db/auto/open = null` 并注释「引擎未暴露静噪读数接口」（`agent_tools.cpp:684-698`）；CH 回真实引擎值（`control_hub.cpp:1265-1268`） | **诚实契约缺陷（stale comment）** | **该修** | 引擎确有公开 getter（`spectrum_engine.h:83-86`），Agent 路径在引擎补齐 getter 后未跟进。最小方案：把 `execGetSquelchStatus` 的四个 `null` 换成 `engine->squelchEnabled()/squelchThresholdDb()/squelchAuto()/squelchOpen()`，删去过时 `note`，与 CH 对齐。消除「同名工具经 Agent 给 null、经 HTTP 给真实值」的静默分歧。 |
| **D2** | `get_spectrum_status` 回包 key 漂移：Agent `window_type/average_mode`（`agent_tools.cpp:1159-1160`）；CH `window/average`（`control_hub.cpp:1491-1492`） | 字段名不一致（值同源同枚举） | **架构性不修（记录）** | 两侧面向不同消费方：Agent schema 给 LLM（`window_type` 更可读），CH/HTTP 给 UI/脚本（短 key `window`）。值无损失（同一 `engine->windowType()/averageMode()`）。改 CH key 会破坏既有 HTTP 回包消费方，收益不抵风险。与历史命令名漂移（`set_vfo_frequency↔vfo_set_freq`）同类，登记不改码。 |
| **D3** | `get_status`：Agent 无五态 `status` / `telemetry_available` / `error_message` / `readback_*`，且 `connected` 取自 caps 快照；CH 有完整五态（`no_telemetry/connected/dropped/error/disconnected`，`control_hub.cpp:840-846`），`connected` 取自遥测 bool。Agent 有 `summary/test_signal`，CH 无 | 形状/契约分层 | **架构性不修（记录）** | Agent get_status 是给 LLM 的精简契约（connected 粗判 + 人话 summary）；CH get_status 是给 UI/HTTP 的完整诊断面（含 dropped/error 细分）。「断连 vs 从未连接」的区分刻意只在 CH 富化。属有意分层，不收敛。残留风险：LLM 经 Agent 无法区分 dropped 与未连接——文档已记。 |
| **D4** | 任务点名的 `get_rssi` 工具在 Agent / CH / HTTP 三通道均**不存在**；RSSI 仅 UI 信号域 | 工具面缺口（非不一致） | **架构性不修（记录）** | 不是「同物不同契约」，而是「被假设存在的工具不存在」。RSSI 不进 `TelemetrySnapshot`（`onTelemetry` 签名无 rssi，`control_hub.cpp:209`）。若要把 RSSI 纳入工具面，最小方案 = 遥测槽加 rssi 入参 + `TelemetrySnapshot` 加字段 + `cmdGetTelemetry`/`get_status` 回包加 `rssi_dbfs`，属新增特性，超出本轮只读审计范围，登记待立项。 |
| **D5** | 信封：Agent 追加 `connected/test_signal/source`，CH 追加 `command`，HTTP 全透传 | 系统性约定 | **架构性不修（记录）** | 两侧各自一贯的信封约定（§0.2），非本批工具的个体分歧。不改。 |

> 本轮**仅 D1 判定为「该修」**，最小落点 `agent_tools.cpp:686-698`（用真实引擎 getter 替换四个硬编码 `null`）。按只读纪律，本次**不落代码修复**，仅登记建议。

---

## 4. 金集实跑计数（offscreen，clean env）

环境：`env -u MBDSDR_TEST_SOURCE LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib QT_QPA_PLATFORM=offscreen`，执行于 `cpp/build` 既有二进制。

| 二进制 | passed | failed | skipped | 耗时 | 退出码 |
|---|---|---|---|---|---|
| `test_agent` | **30** | 0 | 0 | 801 ms | 0 |
| `test_control_hub` | **27** | 0 | 0 | 1561 ms | 0 |
| `test_control_http` | **14** | 0 | 0 | 82 ms | 0 |
| **合计** | **71** | **0** | **0** | — | — |

三金集全绿，无回归。HTTP 用例回环端口 `QWARN` 横幅为正常提示，非失败。

---

## 5. 红线扫描结果

| 红线项 | 结果 |
|---|---|
| 改源码 | ✅ 未改。`git status --short` 仍只有 3 个**既有**未跟踪隔离文件：`cpp/scratch/regen_tool_doc`、`cpp/scratch/regen_tool_doc.cpp`、`cpp/tests/ui_diag_freeze.cpp`（未触碰，未误判并行会话在途改动） |
| git add / commit / push | ✅ 未执行 |
| 「比赛 / competition」字样（`cpp/src` + `cpp/tests`） | ✅ 无命中（grep exit 0 空输出） |
| mock / 操控 GUI | ✅ 未 mock；offscreen 纯跑既有测试二进制，未起 GUI |
| HEAD | ✅ 审计前后均为 `bda5e48` |

---

## 6. 诚实未完成项

1. **D1 仅登记、未修**：按只读纪律未落地代码修复；建议落点 `agent_tools.cpp:686-698`，需后续写会话语句把四个 `null` 换成真实引擎 getter，并补一条「Agent get_squelch_status 回真实非 null 值」的对照用例（与 CH 对齐）。
2. **断连态（dropped）下 Agent `connected` 的 caps 重置时机**为源码推断（`updateCapsSnapshotLocked` 在 source 切换时置 `noDeviceCapabilities()`，`spectrum_engine.cpp:185-188`），未在 offscreen 实际跑「先流式→拔设备→读 Agent get_status」链路（测试二进制无 librtlsdr 真设备绑定，`[RtlSdrSource] no librtlsdr ops bound`）。
3. **`get_rssi` 缺口**未做「补工具」的方案设计，仅登记为待立项特性；本轮不新增工具。
4. **window/average 枚举的具体整数值**（`windowType()/averageMode()` 默认）未逐一查表，因两侧读同一 getter、值必然相等，枚举具体值对一致性判定无影响。
