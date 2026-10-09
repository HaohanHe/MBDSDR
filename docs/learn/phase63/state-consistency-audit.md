# Phase63 — MBDSDR engine↔UI 状态一致性抽查（只读审计 + 落档）

- **HEAD**: `e9d4440`（审计前后未变，`git diff --stat` 为空）
- **仓库根**: `/home/user/Doubao/chats/38438160041798146/MBDSDR`
- **范围**: 3 个写工具 + 1 个回读工具的「工具→engine→UI」状态链路。聚焦：工具改状态后 UI 是否如实反映（无陈旧值、无假成功），以及 `sbMode_/sbSr_` 的回读真实源。
- **方法**: 全程只读源码 + offscreen 跑既有测试二进制（`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，`env -i` clean env，`QT_QPA_PLATFORM=offscreen`，未设 `MBDSDR_TEST_SOURCE`）。未改源码、未 git add/commit/push、未 mock、未操控 GUI、未用 `/tmp` 做自有 scratch。

---

## 0. 命名核对（Agent 注册名 ↔ CH 命令表名 ↔ engine setter）

| 抽查项 | Agent 注册名（executor） | CH 命令名（handler） | engine setter |
|---|---|---|---|
| 中心频率 | `tune_frequency`（`agent_tools.cpp:74`） | `tune`（`control_hub.cpp:408`） | `onSetCenterFreq`（`spectrum_engine.cpp:211`） |
| 解调模式 | `set_mode`（`agent_tools.cpp:87`） | `set_mode`（`control_hub.cpp:443`） | `setDemodMode`（`spectrum_engine.cpp:244`） |
| 信道带宽 | `set_bandwidth`（`agent_tools.cpp:203`） | `set_bandwidth`（`control_hub.cpp:453`） | `setBandwidth`（`spectrum_engine.cpp:692`） |
| 回读 | `get_status`（`agent_tools.cpp:216`） | `get_status`（`control_hub.cpp:790` / `:811`） | `centerFreq()` / `demodMode()` / `bandwidth()` |

---

## 1. 工具→engine→UI 全链路对照表

### 1.1 执行路径与异步模型

三个写工具**全部走 engine 邮箱异步应用**（`PendingControls` 结构体，`spectrum_engine.cpp:1113` `applyControlCommandsLocked`）。调用方线程只入队（`pending_.dXxx = true`），engine 线程下一个 loop tick 才落到 `source_` / `vfoManager_`。这意味着：

- 工具返回时，engine 真实硬件状态尚未生效（仅 pending 生效）；
- UI 不在工具返回的同一帧刷新，而是等 engine 线程发射信号。

| 工具 | Agent 层 | CH 层 | engine 邮箱应用点（`applyControlCommandsLocked`） |
|---|---|---|---|
| tune_frequency / tune | schema min/max=[24M,1700M]（`tool_schema.cpp:76-77`），executor 直接 `engine->onSetCenterFreq(f)`（`agent_tools.cpp:78`） | `std::clamp(f,kFreqMin,kFreqMax)` + 回带 `clamped` 标志（`control_hub.cpp:412-417`） | `:1124-1130`：`source_->setCenterFreq(p.centerFreqHz)` + 选中 VFO 跟随 |
| set_mode | schema enum={AM,NFM,WFM,USB,LSB,CW}（`tool_schema.cpp:92`），executor 直接 `engine->setDemodMode(m)`（`agent_tools.cpp:91`） | `needMode` 白名单={AM,NFM,WFM,USB,LSB,CW,POCSAG,m17,VOR,ACARS,NAVTEX} + toUpper（`control_hub.cpp:316-324`） | `:1139-1147`：`vfoManager_.setMode(sel,m)` → `demodMode_=sel->mode` |
| set_bandwidth | schema enum=7 预设（`tool_schema.cpp:156-159`），executor 直接 `engine->setBandwidth(bw)`（`agent_tools.cpp:207`） | `needDbl` 仅查"是数字"，**无范围**（`control_hub.cpp:454-456`） | `:1148-1154`：`bandwidth_=p.bandwidthHz`（无条件）+ `vfoManager_.setBandwidth(sel,hz)`（hz≤0 返回 false） |

### 1.2 UI 回读链（信号驱动，非轮询）

engine 线程在 `applyControlCommandsLocked` 末尾，若 `vfoChanged` 则 `publishVfoSnapshotLocked()` + `emit vfoListChanged()`（`spectrum_engine.cpp:1180`）。UI 侧唯一的全量刷新入口：

```
connect(engine_, &SpectrumEngine::vfoListChanged,
        this, [this](){ refreshVfoUi(); scheduleSave(); },
        Qt::QueuedConnection);                       // main_window.cpp:2502-2504
```

`refreshVfoUi()`（`main_window.cpp:3823`）每帧读**新鲜快照** `vfoMarkers_ = engine_->vfoMarkers()`（`:3824`，非缓存），然后回写：

| UI 字段 | 回写点 | 数据源 | 块信号？ |
|---|---|---|---|
| `freqSpin_`（频率 spinbox，单位 MHz） | `:3867-3869` | `sel->freqHz`（=命令值，因为 `:1128` 把选中 VFO 直接设为 pending center） | 是（blockSignals） |
| `demodCombo_`（解调下拉） | `:3871-3874` | `demodCombo_->findText(sel->mode)` 命中才 setCurrentIndex | 是 |
| `bwCombo_`（带宽下拉） | `:3876-3878` | `nearestBwPresetIndex(sel->bandwidthHz)` | 是 |
| `currentBwHz_`（内部缓存） | `:3880` | `sel->bandwidthHz` | — |
| `sbMode_`（状态条 mode） | `:3881` | `sel->mode`（engine 快照） | — |
| `sbVfo_`（状态条 VFO） | `:3882` | `sel->name` + `sel->freqHz` | — |
| 频谱带边框（band-edge box） | `:3825` → `spectrum_->setVfoMarkers(vfoMarkers_)` | 每个 marker 的 `freqHz/bandwidthHz`（`spectrum_display.cpp:713-731` 直接画） | — |

另有一条 **1 Hz 硬件回读信号** `sourceTelemetry`（`spectrum_engine.cpp:1456-1458`，每 1000ms 发射一次）→ 槽 `onSourceTelemetry`（`main_window.cpp:4807`）：

| UI 字段 | 回写点 | 数据源 |
|---|---|---|
| `sbSr_`（采样率） | `:4817` | `source_->sampleRate()`（真实 LO 读回，非 spinbox 请求） |
| `sbVfo_`（频率，**第二次覆写**） | `:4819` | `source_->centerFreq()`（真实 LO 读回） |
| `sbGain_` | `:4829` | `source_->gain()`（驱动取整后的值） |
| `sbSdr_`（连接态） | `:4831` | `source_->name()/isConnected()` |
| 调谐历史 | `:4824` | 同上真实 center |

### 1.3 时效性结论

| 字段 | 驱动方式 | 改后 UI 何时更新 | 陈旧窗口 |
|---|---|---|---|
| `freqSpin_` | vfoListChanged（命令值） | 下一个 engine loop tick（ms 级）+ QueuedConnection | 无（命令值即时回显） |
| `demodCombo_` / `sbMode_` | vfoListChanged | 同上 | 无 |
| `bwCombo_` / 频谱带边框 | vfoListChanged | 同上 | 无 |
| `sbVfo_`（频率状态条） | **双源**：vfoListChanged 先写命令值，1Hz telemetry 后覆写为真实 LO | 命令值即时；真实 LO ≤ 1s | 硬件 snap/clamp 时 ≤ 1s（架构性，见 §2 D2） |
| `sbSr_`（采样率状态条） | 1Hz telemetry 为主；用户改 srCombo 时本地即时写（`:2414`） | 真实值 ≤ 1s | ≤ 1s |

**没有"工具改完 UI 完全不刷新"的字段。** 三个写工具都触发 `vfoListChanged`，UI 全量刷新入口被正确接线。

### 1.4 回读真实源结论（重点核实 `sbMode_` / `sbSr_`）

- **`sbMode_`**：构造期 `:2187` 一次性写 `demodCombo_->currentText()` 作为初值；**此后唯一回写点是 `refreshVfoUi` `:3881`，数据源是 engine VFO 快照 `sel->mode`，不是 demodCombo 本地文本**。即：用户担心的"sbMode 显示 combo 当前文本漂移"只发生在构造期一次性，运行期 sbMode 始终是 engine 真值。**结论：无 combo 漂移。**
- **`sbSr_`**：用户改 srCombo 时本地即时写 `srCombo_->currentText()`（`:2414`）做乐观回显；但 1Hz `onSourceTelemetry` 会用 `source_->sampleRate()` 真实读回覆写（`:4817`）。若驱动 snap 到相邻档位，≤1s 内状态条会自动纠正。**结论：硬件真值最终获胜，无长期漂移。**
- **`get_status` 回读**：`centerFreq()` 在 pending 存在时**立即返回命令值**（`spectrum_engine.cpp:168-175`，注释明确这是设计），`demodMode()`/`bandwidth()` 直接返回 engine 缓存 `demodMode_`/`bandwidth_`。即 get_status 读的是 **engine 缓存/pending**，不是 UI combo 本地值。这与 UI vfoListChanged 用的是同一快照源，二者一致。

---

## 2. 差异判定清单

### D1（该修，最小方案）—— CH `set_bandwidth` 传 hz≤0 时，engine `bandwidth_` 被污染但 UI 不跟随

**现象**：CH 客户端发 `set_bandwidth {bandwidth_hz: 0}` 或负值。`control_hub.cpp:455` `needDbl` 只校验"是数字"，放行到 `engine_->setBandwidth(bw)`（`control_hub.cpp:456`）。engine `setBandwidth`（`spectrum_engine.cpp:692-699`）**无非正/非有限守卫**（对比 `onSetCenterFreq` `:215` 有 `if (!(f>0.0)||!isfinite(f)) return;`），直接入队。邮箱应用 `:1148-1154`：

```cpp
bandwidth_ = p.bandwidthHz;                                    // 无条件污染 engine 缓存
vfoManager_.setBandwidth(vfoManager_.selectedId(), p.bandwidthHz); // hz<=0 内部 return false
```

`VfoManager::setBandwidth`（`vfo_manager.cpp:373-379`）对 `hz<=0.0` 返回 false，**VFO 信道的 `bandwidthHz` 保持旧值**。后果：

- `get_status` 读 `engine->bandwidth()` = 被污染的 0/负值；
- 工具回包 `o["bandwidth_hz"]=bw` 也是 0/负值；
- 但 UI `bwCombo_`、`vfoList` 行、频谱带边框仍显示**旧带宽**（因为 VFO 信道没改）。

→ **get_status 与 UI 不一致**：工具/回读说 0，UI 显示旧值。Agent 路径因 schema enum=7 预设（`tool_schema.cpp:156-159`）天然不触发；**仅 CH/HTTP 路径可触发**。

**最小修复**（一行，位置 `cpp/src/dsp/spectrum_engine.cpp:692`）：

```cpp
void SpectrumEngine::setBandwidth(double hz) {
    if (!(hz > 0.0) || !std::isfinite(hz)) return;   // 镜像 onSetCenterFreq :215
    QMutexLocker lk(&ctrlMutex_);
    pending_.bandwidthHz = hz;
    pending_.dBandwidth = true;
    applyIfIdle();
}
```

这样 hz≤0 在 engine 层被丢弃，CH handler 已返回 ok（建议 CH 层也同步加范围校验以返回 ok:false，但 engine 守卫是最终防线，一行即可堵住 UI/回读不一致）。

### D2（架构性不修，已注释说明）—— `freqSpin_` 与 `sbVfo_` 的 ≤1s 收敛窗口

`freqSpin_` 经 vfoListChanged 写**命令值**（`sel->freqHz`，`:3868`）；`sbVfo_` 经 1Hz telemetry 写**真实 LO 读回**（`source_->centerFreq()`，`:4819`）。真实硬件驱动可能 snap/clamp LO（如 RTL-SDR 频率量化、偏移调谐跨 capture 边才回退 LO），此时 spinbox 与状态条频率在 ≤1s 内可能不一致。

- 代码注释已明确这是设计：`spectrum_engine.cpp:164-176`（pending 优先）、`:1446-1449`（1Hz 硬件读回，非 spinbox 请求）、`main_window.cpp:2275-2277`（spinbox 路径与 telemetry 路径共用同一 formatter）。
- 要消除窗口需把 telemetry 从 1Hz 提到每 tick，代价是 UI 信号洪水 + 状态条闪烁。**不修，属异步邮箱架构的固有延迟。**

### D3（架构性不修，已知工具面覆盖差）—— 三通道 mode 白名单不一致

| 通道 | 可达 mode |
|---|---|
| UI demodCombo_（`main_window.cpp:589-590`） | AM,NFM,WFM,USB,LSB,CW,BPSK,QPSK,ADS-B,POCSAG,m17,VOR,ACARS,NAVTEX（14 项） |
| Agent set_mode schema（`tool_schema.cpp:92`） | AM,NFM,WFM,USB,LSB,CW（6 项） |
| CH set_mode needMode（`tokens.h:1099-1102`） | AM,NFM,WFM,USB,LSB,CW,POCSAG,m17,VOR,ACARS,NAVTEX（11 项） |

BPSK/QPSK/ADS-B 只能经 UI 或 vfo 工具到达；Agent/CH set_mode 对它们诚实报错（schema enum 拒绝 / needMode 拒绝）。这不是状态不一致（UI 在被其他路径设置时仍正确渲染），而是工具面覆盖差。**不修**，已在 `tool-count-sync.md` / `mobile-desktop-tool-diff.md` 记录。

### D4（观察项，不构成缺陷）—— 写工具回包回显请求值而非 post-call engine 读回

Agent `execTuneFrequency` 回包 `o["frequency_hz"]=f`（请求值，`agent_tools.cpp:81`），`execSetMode` 回 `o["mode"]=m`（`:94`），`execSetBandwidth` 回 `o["bandwidth_hz"]=bw`（`:210`）。由于邮箱异步，工具返回时 engine 硬件尚未生效。但：

- schema/needMode/needDbl 已挡住绝大多数非法请求；
- 客户端要确认真实值应再调 `get_status`（读 engine 缓存/pending）；
- 回包与 get_status 读同一 engine 缓存，二者自洽。

**不修**，与异步邮箱模型一致。

---

## 3. 金集实跑（offscreen，clean env）

环境：`env -i PATH=/usr/bin:/bin HOME=$HOME LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib QT_QPA_PLATFORM=offscreen`，未设 `MBDSDR_TEST_SOURCE`，直接跑 `cpp/build/` 既有二进制。

| 二进制 | 结果 |
|---|---|
| `build/test_agent` | **31 passed, 0 failed, 0 skipped**（616ms） |
| `build/test_control_hub` | **27 passed, 0 failed, 0 skipped**（1349ms） |
| `build/test_control_http` | **14 passed, 0 failed, 0 skipped**（38ms） |
| `build/test_ui_integration` | **20 passed, 0 failed, 0 skipped**（5189ms） |
| **合计** | **92 passed, 0 failed** |

注：既有测试二进制自身会向 `/tmp` 写合成 IQ 与录制文件（如 `/tmp/mbdsdr_cal_e2e_*.cf32`），这是测试二进制既有行为，本轮未新增、未改动；自有 scratch 未用 `/tmp`。

---

## 4. 红线扫描

- **源码未改**：`git diff --stat HEAD -- cpp/src` 为空；工作树仅有本轮开始前已存在的 4 个未跟踪 scratch 文件（`cpp/scratch/gated_render_snapshot.cpp`、`cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp`），均未触碰。
- **无真实 token**：`grep -rnE "ghp_[A-Za-z0-9]{20}|sk-[A-Za-z0-9]{20}" cpp/src/ai cpp/src/control` 0 命中。
- **营销竞技措辞**：`grep -rin "比赛|competition|赛事" cpp/src/ai cpp/src/control` 0 命中（仅本审计档与既往 phase63 档的自查段落提及"0 命中"）。
- **未 mock、未操控 GUI**：offscreen 纯跑既有二进制，未启动真实 GUI 交互。
- **未 git add/commit/push**。

---

## 5. 诚实未完成项

1. **D1 未实跑复现**：本轮只读，未改 `setBandwidth` 守卫、未写新测试用例去实发 `set_bandwidth{bandwidth_hz:0}` 观察 UI/回读分歧。D1 结论基于源码静态推演（`control_hub.cpp:454-456` 放行 → `spectrum_engine.cpp:692-699` 无守卫 → `:1148-1154` 无条件污染 `bandwidth_` 而 `vfo_manager.cpp:375` 拒绝）。建议下一轮加一条 CH 层 `set_bandwidth hz=0` 的回归用例验证。
2. **真实硬件 LO snap 未观测**：D2 的 ≤1s 收敛窗口在 RTL-SDR/rtl_tcp 真实源上的实际表现未在本轮 offscreen 环境观测（offscreen 跑的是合成测试信号源，LO 不 snap）。结论基于代码注释与异步模型推演。
3. **`refreshVfoUi` 高频触发的开销**：三个写工具都触发 `vfoListChanged`，多 VFO 场景下 QueuedConnection 刷新频率未做 perf 测量。本轮只读审计，未加埋点。
