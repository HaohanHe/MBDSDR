# 47 金集全量对账（只读审计 + 落档）

- 仓库：MBDSDR（C++ Qt 桌面 SDR），对账基线 HEAD = `6a7ad7b`（main）
- 交付方式：**只读落档**。仅新增本文件；跑了既有测试二进制（offscreen），未改任何 `cpp/src/**`、未改任何测试、未 `git add/commit/push`。
- 环境：`QT_QPA_PLATFORM=offscreen`，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，复用 `cpp/build` 既有二进制（未增量重编译）。
- 工作树跑完测试后复核：与开跑前完全一致（仅三条既有未跟踪文件：`cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp`，均未触碰）。
- 上轮基线：`docs/learn/phase63/tool-three-channel-47.md`（HEAD `9efbdcb`，三通道一致结论）。本轮在其上做**金集二进制实跑 + 六源计数交叉**。

## 0. 结论摘要

- **六个金集测试全部 PASS：合计 106 passed / 0 failed**（明细见 §1）。
- **六源计数自洽：零不一致 → 零待修**（明细见 §2、§3）。
- 红线扫描：**CLEAN**（`ghp_` 0 真实 token；`competition/比赛/赛事` 在 `cpp/src/ai`、`cpp/src/control` 0 命中）。

## 1. 六个金集测试真实计数表

运行方式：逐个执行 `cpp/build/test_*` 二进制，offscreen，取 QtTest 自带 Totals。

| 金集测试 | 功能槽 | init/cleanup | 真实 passed | failed | 关键断言口径（读源码确认） |
|---|---|---|---|---|---|
| `test_tool_schema` | 8 | 2 | **10** | **0** | `schemaShape`：`specs.size()==47`、`defs.size()==specs.size()`、defs[i] name/description 与 spec 逐字；`sevenToolsNameDescriptionMatch`：47 对 name+描述逐字硬编码（含 noise_blanker 对、Phase61 VFO 三工具）；`generateToolDocumentationCoversAllTools`：遍历 specs、`headerCount==47`、每块含 `[write`/`[read` 与 `gated` 标记 |
| `test_tool_registry` | 6 | 2 | **8** | **0** | `completeness_everySchemaHasExecutor`：`schemas.size()==47`、executors==schemas；`completeness_noOrphanExecutor`：schemas==`kAllCxxTools`(47 冻结集)；`writeReadSplit_registryMatchesContract`：actualWrite==`kExpectedWriteTools`(**29**)、isWriteTool 抽钉；`readOnlySet_parityWithFlutter`：readOnly==`kFlutterUngatedReadTools`(**18**)；`notARegression_smoke`：`defs.size()==47`、首行 tune_frequency/末行 get_recording_state |
| `test_ai_real_link` | 15 | 2 | **17** | **0** | `toolCount_registryEqualsExecution`：registered.size()==supported.size() 且集合逐名相等（47==47）；isWriteTool 抽钉 29 写/18 读（含 set_noise_blanker 写、get_noise_blanker_status 非写）；`phase26_tools_callableReturnJson`：23 个 Phase26 新工具 dispatch 列表逐个无"未知工具"；`phase26_badArgsHonestError`：缺参/错型 ok:false + noise_blanker set/get 回读 |
| `test_agent` | 28 | 2 | **30** | **0** | `manualMode_gateSpotCheckAllWrites`：**遍历声明表本身**（不硬编码名单），manualMode=true 下 29 写全部 `gated:true,ok:false` 且逐字"手动模式：未执行 <name>"，18 读全部不被拦（predict_passes 诚实 ok:false 为例外、非门拦）；`QCOMPARE(writes,29)/QCOMPARE(reads,18)`；后端 16 个观测字段（频率/模式/带宽/VFO/静噪/noise_blanker/录制/FFT/QSettings ppm/色板）byte-for-byte 不变 |
| `test_control_hub` | 25 | 2 | **27** | **0** | `commandTableIsClassified`：遍历命令表抽钉分类——`tune`写、`get_frequency`读、`start_recording`写、`get_status`读、`clear_digital_outputs`写、`get_pocsag/m17/vor`三快照读；`phase26WritesAreGatedAndBadArgsHonest`：8 个 Phase26 写命令（含 set_noise_blanker）写门关闭即拦、写门开后 set_color_map 真落 QSettings；`noiseBlankerSetLandAndStatusReadsBack`：{on:true} 翻引擎、缺 on/"yes"串 ok:false |
| `test_control_http` | 12 | 2 | **14** | **0** | `postCommandTuneLandsInEngine`：POST /command tune 真落地、GET /status 回读新频率；`gateClosedRefusesWritePost`：写门关闭 tune 拦且引擎不动；`postCommandSetVfoArmedLandsInEngine`/`postCommandCapabilitiesAndRecordingStateRoute`/`postCommandNoiseBlankerRoute`：三类代表路由走统一委托落地；`unknownPathAndBadRequestsAreHonest`：未知命令 200+ok:false、缺 tool 字段/畸形 JSON 400 |
| **合计** | **94** | **12** | **106** | **0** | — |

> 运行期唯一告警：`qt.multimedia.symbolsresolver: Couldn't resolve pipewire-0.3 symbols`（offscreen 环境 Qt multimedia 缺 pipewire），QINFO 级、非 FAIL，不影响任何断言。

### 1.1 HTTP"66 命令自动覆盖"的架构证据（源码级）

- `cpp/src/control/control_http_server.cpp:306-331`：`POST /command` → 取 body `tool`/`args` → 直接 `hub_->execute(tool, args)`。**无任何 per-route handler**；CH 表 66 行命令因此经同一委托自动可达（写门关闭时写命令在 `ControlHub::execute()` 内被 `gatedResult` 拦）。
- 测试钉住的是该统一委托模式（tune 落地 / vfo_armed / capabilities / recording_state / noise_blanker 五个代表路由 + 未知命令诚实错），**未逐条跑 66 命令**——与上轮口径一致，属架构断言而非枚举测试。

## 2. 六源计数自洽表

> 每源计数均为本次独立 grep/跑表实测（非抄上轮文档）。

| # | 源 | 位置 | 总规模 | 写 | 读 | 与其它源的关系 |
|---|---|---|---|---|---|---|
| 1 | Agent spec（声明表） | `cpp/src/ai/tool_schema.cpp::registeredToolSpecs()`（:63-958） | **47**（grep `s.name=` 精确计数 47；剔除一个参数名 `hrs.name="hours_ahead"` 假阳性） | **29**（显式 `s.write=true` 29） | **18**（显式 false 8 + 默认 `bool write=false` 10） | 单一事实源；写/读标志是门的唯一依据 |
| 2 | dispatch（执行表） | `cpp/src/ai/agent_tools.cpp::dispatchTable()`（:1280） | **47**（grep 行名 47） | — | — | 与源 1 **集合逐名相等**（`diff catalog vs dispatch` 为空），无缺行、无孤儿 executor |
| 3 | ControlHub 命令表 | `cpp/src/control/control_hub.cpp::table()`（:81-260） | **66**（grep 行 66） | **43**（`, true,` 43 行） | **23**（`, false,` 23 行） | 见 §2.1 拆分关系 |
| 4 | mobile 已接入集 | `mobile/lib/app/ai_tools.dart::buildRadioTools()` | **10**（grep `name:'...'` 去重 10） | **7** | **3** | 见 §2.2 |
| 5 | mobile catalog 快照 | `mobile/lib/app/tool_catalog.dart` | **47**（grep name 去重 47） | 29/18（注释明示，按注册顺序逐字） | 同左 | 与源 1/2 **集合逐名相等**（diff 空），为只读参考快照 |
| 6 | 三通道基线表 | `docs/learn/phase63/tool-three-channel-47.md` | 47 / 47 / 66 | 29·—·43 | 18·—·23 | 与源 1/2/3 全部吻合；本轮实测复核无漂移 |

### 2.1 源 1（47 Agent）↔ 源 3（66 CH）可达性拆分（实测）

- **同名同标志 41**：`comm -12 dispatch CH` = 41 行。
- **命名漂移 4 对**（能力/标志等价，上轮已登记架构性不修）：`tune_frequency`↔`tune`、`set_vfo_frequency`↔`vfo_set_freq`、`set_vfo_mode`↔`vfo_set_mode`、`set_vfo_bandwidth`↔`vfo_set_bandwidth`。
- → **45/47 Agent 工具经 CH（及 HTTP /command）可达**。
- **Agent 独有、无 HTTP 通道 2**：`calibrate_frequency`(R)、`apply_frequency_correction`(W)（桌面 AI 校准对，`tool_schema.cpp` 注释明示非 headless 设计意图）。
- **CH 独有 21**（66 − 41 同名 − 4 漂移目标 = 21）：增益/AGC/静音/遥测等底层原语，有意不暴露 LLM。
- 校验：41 + 4 漂移 + 2 独有 = 47 ✓；41 + 4 漂移目标 + 21 独有 = 66 ✓。

### 2.2 源 4（mobile 10）↔ 源 1（desktop 47）关系（实测）

- **∩ 7**（跨平台共享，写/读标志两端一致）：`set_mode`(W)、`set_squelch`(W)、`start_recording`(W)、`stop_recording`(W)、`get_status`(R)、`predict_passes`(R)、`get_squelch_status`(R)。
- **mobile 独有 3**：`set_frequency`、`set_gain`、`set_sample_rate`——移动端 rtl_tcp 通道的硬件原语，desktop 侧对应 CH 命令（`tune`/`set_gain`/`set_sample_rate`）但不进 LLM 47 工具集，符合"底层原语不暴露 AI"的同一设计。
- 校验：7 共享 + 3 mobile 独有 = 10 ✓；7 共享集全部落在 desktop 47 内 ✓。

## 3. 差异判定

**零不一致 → 零待修。**

- 六个金集二进制实跑全绿（106/0），其钉死的 47=29+18、CH 66=43+23、dispatch==spec==catalog 47 集合均与本次独立 grep 实测逐名一致。
- 源 1↔3 的命名漂移 4 对、Agent 独有 2、CH 独有 21，均为上轮（`tool-three-channel-47.md` §2）已登记且被金集冻结的**架构性设计差异**，本轮复核未新增、未扩大，不构成新缺陷，不重复展开。
- 未发现任何需要改码的真缺陷（无标志错、无 dispatch 缺行/孤儿、无 HTTP 不可达的已暴露工具）。

## 4. 红线扫描

| 项 | 范围 | 结果 |
|---|---|---|
| `ghp_`（GitHub token 泄露） | `cpp/src/**`、`mobile/lib/**`、`docs/learn/phase63/**` | **0 真实 token**。仅旧档（`tune-history.md` 等）把该词作为"红线扫描关键词"自述命中，源码 0 命中 |
| `competition` / `比赛` / `赛事`（营销竞技措辞） | `cpp/src/ai/**`、`cpp/src/control/**`、本轮基线档 | **0 命中**（仅旧档自查段落提及"未引入"） |

## 5. 诚实未完成项与边界

- 测试二进制为 `cpp/build` 既有产物（对应 HEAD `6a7ad7b`），本轮**未增量重编译**；二进制与源码的一致性由"working tree 跑完前后 `git status` 不变 + 全绿"间接保证。
- HTTP 侧"66 命令自动覆盖"为**架构断言**（统一委托、无 per-route handler），测试只钉代表路由，未枚举跑 66 条命令——与上轮口径一致。
- offscreen 环境无 pipewire（multimedia 符号解析告警）：仅 QINFO，未影响断言；若后续需 audio 链路测试可在有 pipewire 环境复核，但与本轮 47 工具对账无关。
- 未触碰 `cpp/tests/ui_diag_freeze.cpp` 等任何在途/隔离文件；未执行任何 `git add/commit/push`。
