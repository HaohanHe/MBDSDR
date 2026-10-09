# Phase63 — MBDSDR 47 工具数量完整性核对（桌面 spec ↔ mobile catalog ↔ 计数引用处 ↔ 金集实跑）

- 审计 HEAD：`3f1f7fb330782e0d062d73c6d5e1366d00ed6a95`
- 审计口径：只读为主；修复仅限明确"该修"且最小；禁 git add/commit/push；禁 mock；offscreen 跑既有二进制，`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`，`QT_QPA_PLATFORM=offscreen`，`env -u MBDSDR_TEST_SOURCE`。
- 工作树未跟踪文件（`cpp/scratch/gated_render_snapshot.cpp`、`cpp/scratch/regen_tool_doc*`、`cpp/tests/ui_diag_freeze.cpp`）未触碰。

---

## 0. 结论速览

| 维度 | 结果 |
|---|---|
| 桌面 `registeredToolSpecs()` 实际工具数 | **47**（`s.name=` 48 行 − 1 个参数名 `hours_ahead` 假阳性 = 47） |
| mobile `kDesktopToolCatalog` 条目数 | **47**，与桌面**集合逐名相等**（diff 空） |
| mobile 写/读分类声明 | 29 写 + 18 读，与源码 `write: true/false` grep 一致 |
| mobile `buildRadioTools()` 实际接入 | 10 个（7 与桌面同名 + 3 个移动原语 `set_frequency`/`set_gain`/`set_sample_rate`） |
| 计数引用处（47 / 65 / 85） | **无漂移**；所有引用均指向当前真值 |
| 金集实跑 | 8 + 34 + 29 + 14 = **85 passed / 0 failed**（与历轮 85 金集完全一致） |
| 红线扫描 | 无 `competition/比赛` 字样；无 `/tmp` 硬编码；`MBDSDR_TEST_SOURCE` 仅 `spectrum_engine` 合法读取，本轮已 `-u` 清掉 |
| 本轮修复 | **0**（无需修复；全部"该修"项历轮已修，本轮仅复核确认） |

---

## 1. 桌面 47 工具枚举（`cpp/src/ai/tool_schema.cpp::registeredToolSpecs()`）

按 `s.name =` 出现顺序（剔除 `hrs.name = "hours_ahead"` 这是 `predict_passes` 的参数，不是工具）：

| # | 工具名 | 行号 | write |
|--:|---|--:|:-:|
| 1 | `tune_frequency` | 69 | W |
| 2 | `set_mode` | 86 | W |
| 3 | `start_recording` | 101 | W |
| 4 | `stop_recording` | 110 | W |
| 5 | `scan_band` | 119 | W |
| 6 | `set_bandwidth` | 149 | W |
| 7 | `get_status` | 170 | R |
| 8 | `predict_passes` | 181 | R |
| 9 | `calibrate_frequency` | 221 | R |
| 10 | `apply_frequency_correction` | 260 | W |
| 11 | `get_pocsag_messages` | 294 | R |
| 12 | `get_m17_calls` | 311 | R |
| 13 | `get_vor_radial` | 329 | R |
| 14 | `get_acars_packets` | 347 | R |
| 15 | `get_navtex_messages` | 365 | R |
| 16 | `export_iq_segment` | 388 | W |
| 17 | `set_network_audio_sink` | 425 | W |
| 18 | `get_network_audio_status` | 463 | R |
| 19 | `start_scan_link` | 473 | W |
| 20 | `stop_scan_link` | 492 | W |
| 21 | `get_scan_link_status` | 502 | R |
| 22 | `set_squelch` | 512 | W |
| 23 | `get_squelch_status` | 539 | R |
| 24 | `set_noise_blanker` | 549 | W |
| 25 | `get_noise_blanker_status` | 565 | R |
| 26 | `list_bookmarks` | 575 | R |
| 27 | `add_bookmark` | 587 | W |
| 28 | `tune_to_bookmark` | 626 | W |
| 29 | `delete_bookmark` | 642 | W |
| 30 | `list_vfos` | 658 | R |
| 31 | `add_vfo` | 667 | W |
| 32 | `switch_vfo` | 677 | W |
| 33 | `rename_vfo` | 693 | W |
| 34 | `set_vfo_armed` | 714 | W |
| 35 | `set_vfo_frequency` | 738 | W |
| 36 | `set_vfo_mode` | 760 | W |
| 37 | `set_vfo_bandwidth` | 782 | W |
| 38 | `list_recordings` | 804 | R |
| 39 | `delete_recording` | 813 | W |
| 40 | `export_recording` | 830 | W |
| 41 | `set_fft_params` | 855 | W |
| 42 | `set_color_map` | 887 | W |
| 43 | `get_spectrum_status` | 904 | R |
| 44 | `set_doppler_compensation` | 917 | W |
| 45 | `connect_network_source` | 938 | W |
| 46 | `get_capabilities` | 966 | R |
| 47 | `get_recording_state` | 981 | R |

计数复核：`grep -cE 's\.name = "' cpp/src/ai/tool_schema.cpp` = 48；剔除 `hours_ahead` = **47**。写 29 / 读 18（与 mobile catalog 头注 `:37` 声明一致）。

---

## 2. mobile catalog 对照（`mobile/lib/app/tool_catalog.dart`）

### 2.1 集合对照

```
diff <(grep s.name= tool_schema.cpp | 剔 hours_ahead | sort) \
     <(grep "name: '" tool_catalog.dart | sort)
# DIFF_EMPTY
```

**桌面 47 与 mobile catalog 47 条目逐名相等，无缺、无幽灵、无多。**

### 2.2 mobile catalog 写/读分类

- `write: true` grep 计数 = **29**
- `write: false` grep 计数 = **18**
- 与桌面 `kExpectedWriteTools`（`cpp/tests/test_tool_registry.cpp:67-85`，29 写集合）和 `kFlutterUngatedReadTools`（`:104-114`，18 读集合）逐名一致——`test_tool_registry::writeReadSplit_registryMatchesContract` 与 `readOnlySet_parityWithFlutter` 双双 PASS 已交叉钉死。

### 2.3 mobile 实际接入子集（`mobile/lib/app/ai_tools.dart::buildRadioTools()`）

| mobile 实际工具名 | 桌面 47 中是否同名 | 备注 |
|---|:-:|---|
| `set_mode` | ✓ | |
| `start_recording` | ✓ | |
| `stop_recording` | ✓ | |
| `set_squelch` | ✓ | |
| `get_squelch_status` | ✓ | |
| `get_status` | ✓ | |
| `predict_passes` | ✓ | |
| `set_frequency` | ✗ | 移动原语，桌面对应 CH `tune`，不进 LLM 47 |
| `set_gain` | ✗ | 移动原语，桌面对应 CH `set_gain` |
| `set_sample_rate` | ✗ | 移动原语，桌面对应 CH `set_sample_rate` |

- `kMobileImplementedToolNames`（`tool_catalog.dart:243-251`）恰为上表 7 个同名项——与 `buildRadioTools()` 实际输出重叠一致。
- 3 个移动原语不入 47：`tool_catalog.dart:240-242` 头注已明示"移动端另有的 set_frequency/set_gain/set_sample_rate 命名与桌面不同（桌面为 tune_frequency 等），不在这里冒充对齐"。
- **差异判定**：架构性不修。这是 mobile 裁剪面 vs 桌面完整面分层，文档已明示；不计入 47 对照漂移。

---

## 3. 计数引用处核对（47 / 65 / 85 / scheme 数）

| 引用位置 | 声明数字 | 真值 | 结论 |
|---|---:|---:|:-:|
| `cpp/tests/test_tool_registry.cpp:139` | `expected 47 tools` | 47 | ✓ |
| `mobile/lib/app/tool_catalog.dart:6,36,37` | 47 / 29 写 + 18 读 | 47 / 29+18 | ✓ |
| `mobile/lib/pages/tools_catalog_page.dart:1` | 对照桌面 47 | 47 | ✓ |
| `mobile/test/tools_catalog_test.dart:16,18,40` | `kDesktopToolCatalog.length == 47` | 47 | ✓ |
| `docs/learn/phase31/agent-tool-documentation.md:1` | 自动生成，47 个工具 | `grep -cE '^## '` = 47 | ✓（历轮由 `generateToolDocumentation()` 重生成覆盖） |
| `docs/learn/phase63/ch-agent-param-contract.md:25,147` | 47 工具 / CH table() 65 条 | spec=47；CH grep=65 | ✓ |
| `docs/learn/phase63/tool-three-channel-47.md` | 47 = 29+18；金集=47 | 实测一致 | ✓ |
| `docs/learn/phase63/tool-count-sync.md` | 历轮 35→47 重排记录 | 当前已落到 47 | ✓（历史档，无需改） |
| `control_hub.cpp` 命令表（:84-155） | — | grep = **65**（43 写 + 22 读） | ✓ |
| `control_http_server.cpp` 路由 | — | 8 条（GET /、/status、/pocsag_messages、/m17_calls、/vor_radial、/acars_packets、/navtex_messages、POST /command）；POST /command 统一委托 CH 65 条 | ✓ |
| 历轮"85"金集 | registry 8 + agent 34 + hub 29 + http 14 = 85 | 本轮实跑 8+34+29+14 = **85** | ✓ |
| 旧档中"HTTP 66 命令"（`error-semantics-audit.md:119` 等） | 66 | CH 实际 65；该档同句已注明"prompt 估 ~66，差 1 是因为 vfo_add 与 add_vfo 是两行同 handler" | ✓（已知近似，非漂移） |

**无硬编码数字漂移**：未发现 `35` / `45` 残留引用（`grep -rnE '(35|45)\s*(个|工具|Agent)' mobile/lib mobile/test cpp/src cpp/tests` 空）。

---

## 4. 金集实跑（offscreen / clean env）

```
LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib
QT_QPA_PLATFORM=offscreen
env -u MBDSDR_TEST_SOURCE
```

| 二进制 | passed | failed | skipped | 备注 |
|---|---:|---:|---:|---|
| `test_tool_registry` | **8** | 0 | 0 | 含 `completeness_everySchemaHasExecutor` / `completeness_noOrphanExecutor` / `writeReadSplit_registryMatchesContract` / `readOnlySet_parityWithFlutter` |
| `test_agent` | **34** | 0 | 0 | 含 `manualMode_gateSpotCheckAllWrites`、`phase63BilateralAliasContract`、`nullEngineReturnsHonestErrorEnvelope` |
| `test_control_hub` | **29** | 0 | 0 | 含 `phase63BilateralAliasContract`、`vfoArmedLandAndReadbackSameState` |
| `test_control_http` | **14** | 0 | 0 | 含 loopback banner QWARN（正常安全提示，非失败） |
| **合计** | **85** | **0** | **0** | 与历轮金集 85 完全一致 |

QINFO/QWARN 全为已知良性：pipewire-0.3 符号解析缺失（offscreen 无多媒体会话）、`librtlsdr ops bound; stub start() returns false`（无硬件）、HTTP 端点仅绑 127.0.0.1 安全横幅。

---

## 5. 红线扫描

- `grep -rniE '(competition|比赛)' cpp/src mobile/lib` → 空。
- `grep -rnE '/tmp/' cpp/src` → 空（`ai_session_store.cpp` 走 `QDir::tempPath()` 是 Qt API，非硬编码路径，且不在本轮改动范围）。
- `MBDSDR_TEST_SOURCE` 仅 `cpp/src/dsp/spectrum_engine.cpp:40,45,67` 与 `.h:341,716` 合法读取；本轮执行已 `env -u` 清除。
- 未执行 `git add` / `commit` / `push`；未触碰未跟踪隔离文件。

---

## 6. 差异判定清单

| 项 | 判定 | 原因 |
|---|---|---|
| mobile catalog 缺工具 | 无 | diff 空，47↔47 逐名相等 |
| mobile catalog 幽灵条目 | 无 | 无桌面外的工具名 |
| mobile 3 个命名原语（set_frequency/set_gain/set_sample_rate） | 架构性不修 | 移动 rtl_tcp 硬件原语，桌面对应 CH 命令名；`tool_catalog.dart:240-242` 已明示，不冒充对齐 47 |
| 旧档"HTTP 66"近似 | 架构性不修 | 同句已注明 prompt 估 ~66、CH 实际 65（`vfo_add`/`add_vfo` 同 handler 两行） |
| 35/45 旧计数残留 | 无 | grep 空；历轮 `tool-count-sync.md` 已重排到 47 |
| phase31 自动生成文档 | 无需改 | 头部 47、`^## ` 计数 47，已重生成覆盖 |
| 本轮修复 | **0 处** | 全部"该修"项历轮已修，本轮仅独立复核 |

---

## 7. 诚实未完成项

- **Dart 未跑 `flutter analyze` / `flutter test`**：云端无 Flutter SDK，`mobile/test/tools_catalog_test.dart` 的 47 断言本轮仅做静态 grep 复核（与 `tool-count-sync.md:72` 历轮未完成项一致）；待 SDK 环境补跑。
- **未做增量构建**：本轮只读核对，直接用 `cpp/build` 既有二进制（构建于 2026-10-09 18:10-18:13，与 HEAD 同期）；未跑 `cmake --build`。
- **HTTP 路由数 8 未在任何文档明示**：当前文档只说"POST /command 委托 CH 65"，GET 只读路由 7 条未单独计数；本轮不新增文档断言（避免越权扩面），仅在本审计档记录。
