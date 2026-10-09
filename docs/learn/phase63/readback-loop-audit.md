# Phase63 — 写工具→读工具状态回读闭环抽查（只读审计 + 落档）

- **HEAD**: `e1b296a`（审计前后未变；`git status --short` 仍仅任务开始前即存在的 4 个未跟踪 scratch 文件，tracked 零改动）
- **仓库根**: `/home/user/Doubao/chats/38438160041798146/MBDSDR`
- **范围**: 5 对「set_* 写 → get_* 读」闭环。聚焦：写回包是否真实反映引擎落定（无假成功字段、无陈旧值）；Agent 侧名 vs CH 侧名先查实际注册表。
- **方法**: 全程只读源码 + offscreen 跑既有测试二进制（`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，`env -i` clean env，`QT_QPA_PLATFORM=offscreen`，未设 `MBDSDR_TEST_SOURCE`）。未改源码、未 git add/commit/push、未 mock、未操控 GUI、未用 `/tmp`。
- **前置**: 47 工具 gate/错误语义/参数边界/只读语义/状态一致性已审计（D1/D3 已修；状态一致性档已覆盖工具→UI 链路）。本轮不重复，只做「写→读」往返本身。

---

## 0. 命名核对（Agent 注册名 ↔ CH 命令表名 ↔ engine setter）

| 抽查项 | Agent 注册名（executor） | CH 命令名（handler） | engine setter | 读侧 getter |
|---|---|---|---|---|
| 中心频率 | `tune_frequency`（`agent_tools.cpp:74`） | `tune`（`control_hub.cpp:408`） | `onSetCenterFreq`（`spectrum_engine.cpp:211`） | `centerFreq()`（`:164`） |
| 解调模式 | `set_mode`（`agent_tools.cpp:87`） | `set_mode`（`control_hub.cpp:443`） | `setDemodMode`（`spectrum_engine.cpp:244`） | `demodMode()`（`spectrum_engine.h:53`） |
| 信道带宽 | `set_bandwidth`（`agent_tools.cpp:203`） | `set_bandwidth`（`control_hub.cpp:453`） | `setBandwidth`（`spectrum_engine.cpp:692`） | `bandwidth()`（`spectrum_engine.h:55`） |
| 静噪 | `set_squelch`（`agent_tools.cpp:664`） | `set_squelch`（`control_hub.cpp:1240`） | `setSquelchEnabled/Threshold`（`:257-259`）、`setSquelchAuto`（`spectrum_engine.h:87`） | `squelchEnabled/ThresholdDb/Auto/Open` |
| 噪声抑制 | `set_noise_blanker`（`agent_tools.cpp:705`） | `set_noise_blanker`（`control_hub.cpp:1272`） | `setNoiseBlanker`（`spectrum_engine.cpp:1092`） | `noiseBlankerEnabled()`（`:1102`） |

读出口统一为 `get_status`（Agent `agent_tools.cpp:216` / CH `control_hub.cpp:806`）与后两个独立 `get_*_status`。

---

## 1. 异步邮箱模型前提（headless 下「立即读回 == 落定读回」的成立条件）

- 三个邮箱写（频率/模式/带宽）只入队 `PendingControls`，由 `applyControlCommandsLocked()`（`spectrum_engine.cpp:1106` 起）在 engine 线程落定。
- `applyIfIdle()`（`:1106-1112`）：**QThread 未运行（headless/测试）时本线程同步排空**；运行中则等 run loop 异步排空。本轮金集全部 `dsp::SpectrumEngine engine;` 栈对象、未 `start()`，故「立即读回」与「落定读回」在同一调用栈内等价——本节所有「立即读回」结论即 headless 可观察值。
- getter 投影不对称（关键）：
  - `centerFreq()`（`:164-177`）：**pending 立即上抛**（pendingHz>0 时直接返回命令值）；
  - `demodMode()` / `bandwidth()`：直读成员 `demodMode_` / `bandwidth_`，**无 pending 投影**。
  - 推论：运行中引擎线程（GUI）场景下，set_mode / set_bandwidth 后同一帧立即 get_status 可能读到至多一个 tick 的陈旧 mode/bandwidth；频率不会。这是架构性异步语义，headless 金集不可达，本轮仅记录、不算缺陷。

---

## 2. 五对闭环对照表

判定口径：写回包 vs 立即读回 vs 落定读回（headless 后两者相同）。「✓」=三者一致；「✗」=存在假成功/陈旧。

### 对 1：tune_frequency / tune → get_status.frequency_hz

| 场景 | 写回包 | 立即读回 | 一致性 |
|---|---|---|---|
| 有效频率（两端都通） | Agent：`ok:true, frequency_hz=f`；CH：同 + `clamped:false` | `centerFreq()` 上抛 pending = f | ✓ |
| 频率越出 `[24e6,1700e6]` | **Agent：无 clamp 无标志，原样回显 f**（schema 仅声明 min/max `tool_schema.cpp:76-80`，executor 不强制）；CH：`std::clamp` 后回带 `eff` + `clamped:true`（`control_hub.cpp:412-417`） | Agent：pending=f 原样上抛（读回与回显一致，但对硬件范围不诚实）；CH：读回=eff | Agent 回显与读回自洽，**但 vs CH 契约缺 clamped 标志**（见差异 D-1） |
| f≤0 / NaN | Agent：引擎 guard 静默丢弃（`spectrum_engine.cpp:214-215` `if(!(f>0)||!isfinite) return;`），**回包仍 `ok:true, frequency_hz=f`**；读回=旧频率（pending 从未置位） | 旧频率 | **✗ Agent 假成功（D-2）**。CH：`needDbl` 有限数校验前置（`control_hub.cpp:300`），NaN 诚实报错，不进入此分支 |

### 对 2：set_mode → get_status.mode

| 场景 | 写回包 | 立即读回 | 一致性 |
|---|---|---|---|
| 白名单模式（两端交集 AM/NFM/WFM/USB/LSB/CW） | Agent：原样回显（**不 toUpper**）；CH：toUpper + 回显 | headless 同步排空后 `demodMode_=sel->mode`（`spectrum_engine.cpp:1143-1146`） | ✓（大小写可能不一致：Agent 回显 `"nfm"` 而 CH 恒为 `"NFM"`） |
| 白名单外模式（如 `"XYZ"`） | Agent：**无白名单**，`ok:true, mode:"XYZ"`；CH：`needMode` 白名单 11 项（`control_hub.cpp:316-324`）→ 诚实报错「未知解调模式」 | Agent：`VfoManager::setMode` 接受任意串（`vfo_manager.cpp:364-372`），读回="XYZ" | 回显与读回自洽，但 **Agent vs CH 校验发散（D-3）** |
| 副作用 | set_mode 会把选中 VFO 带宽重置为模式默认（`vfo_manager.cpp:368` `defaultBandwidthForMode(mode)`），引擎侧随后 `bandwidth_=sel->bandwidthHz`（`:1144-1145`） | get_status.bandwidth_hz 静默跟随变化 | 非假成功（设计内联动），但调用方若先 set_bandwidth 再 set_mode 会发现带宽被改写——记录为已知行为 |

### 对 3：set_bandwidth → get_status.bandwidth_hz

| 场景 | 写回包 | 立即读回 | 一致性 |
|---|---|---|---|
| 正带宽 | 两端均回显 bw | headless 同步排空 `bandwidth_=p.bandwidthHz` | ✓ |
| bw≤0 / NaN | 两端：引擎 guard 静默丢弃（`spectrum_engine.cpp:694-696`），回包均 `ok:true, bandwidth_hz=bw`（含 0 / -500） | 读回=旧正带宽（`bandwidth_` 未动） | **回显≠读回**——但**已被测试钉死**：`test_control_hub.cpp:979-1029 topLevelSetBandwidthNonPositiveDoesNotDriveEngine` 显式断言回包 `bandwidth_hz==0/-500` 且引擎保持 8000，注释「echo, not applied」=「回执即回显」语义是**有意钉死**，非遗漏（见 D-4 判定：架构性/已钉死，不单方面改） |
| 带宽上界 | 两端**均无上限**：bw=1e9 原样落定存储 | 读回=1e9 | 回显与读回自洽；无超采样率 clamp 属边界留白（架构性不修） |

### 对 4：set_squelch → get_squelch_status

| 字段 | Agent 写路径 | CH 写路径 | 读回（两端同一组 getter） | 一致性 |
|---|---|---|---|---|
| `enabled` | `setSquelchEnabled` 同步（`agent_tools.cpp:672`） | 同 | `squelchEnabled()`=mode==Gate（`spectrum_engine.cpp:259`） | ✓（已由 `test_agent.cpp:838 squelchStatusRealReadback` 钉住 enabled 往返） |
| `threshold_db` | 同步 `setSquelchThreshold`（缓存 `squelchThreshold_`，顺带 disarm auto） | clamp 到 `[-100,-20]`（`tokens.h:316-317`）后回带 eff（`control_hub.cpp:1247-1251`） | `squelchThresholdDb()`=上次命令值 | ✓（两端各自自洽；Agent 不 clamp 是边界留白，非假成功） |
| `auto` | **仅回显，不落引擎**（`agent_tools.cpp:676-677` 注释「auto has no engine setter yet」——**注释已陈旧**：引擎 `spectrum_engine.h:87` 早就有 `setSquelchAuto`） | **真实落引擎**（`control_hub.cpp:1252` `engine_->setSquelchAuto(au)`） | `squelchAuto()`（原子读） | **✗ Agent 假成功（D-5）**：`set_squelch{auto:true}` 回包 `auto:true`，`get_squelch_status.auto` 永远是 false。CH 同参则读回=true |

### 对 5：set_noise_blanker → get_noise_blanker_status

| 场景 | 结论 |
|---|---|
| 完整往返 | 两端均要求 `on` 为必填 bool，缺失/非 bool 诚实报错（Agent `agent_tools.cpp:706-707` / CH `control_hub.cpp:1273`）；`setNoiseBlanker` 同步翻转（`spectrum_engine.cpp:1092`），getter 同步直读（`:1102`）。回包=读回=引擎真值。**干净闭环，无差异**；已由 `test_agent.cpp:787 noiseBlankerLandReadbackAndGate` 钉住（含 manual-mode gate 不翻引擎、缺参报错）。 |

---

## 3. 差异判定清单

| 编号 | 差异 | 判定 | 最小方案（仅记录，本轮不改） |
|---|---|---|---|
| D-1 | Agent `tune_frequency` 无范围 clamp、无 `clamped` 标志（CH 有） | **架构性不修**（契约分层：Agent 侧 schema `tool_schema.cpp:76-80` 已向 LLM 声明 [24M,1700M]，executor 信任 schema；CH 面向裸 HTTP 必须代码级防御。两层职责不同） | 如未来要消差，可在 executor 镜像 CH clamp——非本轮范围 |
| D-2 | Agent `tune_frequency(f≤0/NaN)` 回包假成功（ok:true + 回显，实际引擎丢弃） | **该修（小）** | `agent_tools.cpp:74 execTuneFrequency`：参照同文件 `execSetNoiseBlanker:706` 的必填校验风格，加 `if(!(f>0)||!std::isfinite(f)) return errResult("频率必须为正有限值");`（约 2 行），与引擎 guard `spectrum_engine.cpp:214` 对齐 |
| D-3 | Agent `set_mode` 无白名单（同文件 `execSetVfoMode:981-984` 已有现成白名单循环，CH `needMode:316-324` 也有） | **该修（小）** | `agent_tools.cpp:87 execSetMode`：复用 `tokens::kControlHubModes` 循环校验 + toUpper（代码已在本文件存在，纯搬运） |
| D-4 | `set_bandwidth` 非正值「回显 ok 但引擎不动」 | **架构性不修（已钉死）** | 钉死测试 `test_control_hub.cpp:979` 明确锁定该回执语义；唯一可做的非破坏性增强是**增量**加 `applied:false` 字段（不改变现有回显值），低优先级，留待后续 |
| D-5 | Agent `set_squelch.auto` 回显不落引擎，读回恒 false（注释「auto has no engine setter yet」陈旧） | **该修（3 行）** | `agent_tools.cpp:676-677`：照 CH `control_hub.cpp:1252` 补 `engine->setSquelchAuto(args.value("auto").toBool());`，并删/改陈旧注释 |

---

## 4. 金集实跑（offscreen 既有二进制，clean env）

| 二进制 | 命令 | 计数 |
|---|---|---|
| `test_agent` | `env -i ... QT_QPA_PLATFORM=offscreen LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib ./test_agent` | **31 passed, 0 failed, 0 skipped**（809ms） |
| `test_control_hub` | 同上 | **28 passed, 0 failed, 0 skipped**（1466ms） |
| `test_control_http` | 同上 | **14 passed, 0 failed, 0 skipped**（74ms） |

合计 **73/73/0**。未设 `MBDSDR_TEST_SOURCE`；未增量构建（直接跑 `cpp/build` 既有二进制，未用 `ci/` 持久目录、未用 `/tmp`）。

---

## 5. 红线扫描

- 工作树：审计前后 `git status --short` 完全一致（仅 4 个任务开始前即存在的未跟踪 scratch：`cpp/scratch/gated_render_snapshot.cpp`、`cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp`），**tracked 零改动，未误判/未回滚并行在途文件**。
- `比赛 / competition / 赛事 / ghp_` 在 `cpp/src/ai/**`、`cpp/src/control/**` 全量扫描：**0 命中**。
- 未 git add/commit/push；未 mock；未操控 GUI；自有 scratch 未落地（本档为唯一新增文件）。

## 6. 诚实未完成项

1. **D-2/D-3/D-5 仅定位未修复**（本轮纪律=只读）；最小方案行号已在上表给出，留待后续轮次动手。
2. **运行中引擎线程（GUI）下的「立即读回」未实测**：`demodMode()/bandwidth()` 无 pending 投影，理论上存在至多一个 tick 的陈旧窗；headless 同步排空模型不可达该分支，本轮仅静态推断，未在 GUI 下观测。
3. Agent `set_bandwidth` 非正值路径（对 3 的 Agent 侧）**无对应钉死测试**（CH 侧有 `topLevelSetBandwidthNonPositiveDoesNotDriveEngine`）；本轮未补测试。
4. `set_mode` 重置带宽为模式默认值的副作用（对 2）未评估是否有调用方依赖旧行为，仅记录。
