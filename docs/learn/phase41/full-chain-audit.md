# Phase41 块1：全链路对账报告（full-chain-audit）

> 执行时间：2026-10-05（UTC+8）　执行环境：云端 4 核 / 7.9GB（OOM 约束 -j2）
> 基线对照：HEAD = 58d9f62。本块职责 = 补全量构建 → 四端全量验证 → 各 Phase 交付声明逐项核对 → 出不实项清单。
> 红线遵守：数字全部为实跑实际值；不实项不掩盖；无比赛字样；活动参数未入通用代码；未 commit/push；仅写本目录文件。

---

## 一、全链路数字总表（实跑实际值）

### 1. C++（cpp/build，Qt offscreen）

| 项 | 基线声明 | 本次实测 | 结论 |
|---|---|---|---|
| 全量构建 | qt_audio_sink.h 触发 ~30 目标重编被预算截断 | `make -j2` 续建至 100% Built target，0 error（仅 QCheckBox::stateChanged deprecated 警告，非错误） | ✅ 补构建完成 |
| ctest 注册总数 | 127 基线 + startup_robustness 新增 | **128 注册**（ctest -N Total Tests: 128） | ✅ 127+1 精确匹配 |
| ctest 通过 | 127 全过 | **127 Passed** | ✅ |
| e2e_smoke | 环境性诚实 SKIP（SKIP_RETURN_CODE 77） | *****Skipped 0.43 sec**（"The following tests did not run: 17 - e2e_smoke (Skipped)"） | ✅ 预期 Skipped |
| startup_robustness | Phase40 新增（真二进制 --snapshot 三无环境） | **Passed 2.58 sec** | ✅ 新增项真实运行 |
| 失败 | 0 | **0 failed**（"100% tests passed, 0 tests failed out of 128"） | ✅ |
| 墙钟 | — | Total Test time (real) = 72.14 sec（mainwindow=21.87s/8，slow=103.15s/18 标签口径） | — |

**ctest 关键证据行**（scratch/p41_ctest.log）：
```
128/128 Test  #17: e2e_smoke ........................***Skipped   0.43 sec
100% tests passed, 0 tests failed out of 128
The following tests did not run:
	 17 - e2e_smoke (Skipped)
```

### 2. Flutter（mobile/）

| 项 | 基线声明 | 本次实测 | 结论 |
|---|---|---|---|
| flutter test | 351 | **351 passed**（"All tests passed!"，+351） | ✅ 精确匹配 |
| flutter analyze | 0 | **No issues found! (ran in 33.4s)** | ✅ 0 问题 |

### 3. Python（pytest，五根目录）

任务点名五域：experiments / onboarding / hw_selfcheck / diag_wizard / acceptance_run。本次逐根实跑（QT_QPA_PLATFORM=offscreen）：

| 根目录（实际路径） | 本次实测 | 结论 |
|---|---|---|
| experiments/tests | **34 passed** in 14.31s | ✅ |
| tools/onboarding | **37 passed** in 24.17s | ✅ |
| tools/hw_selfcheck | **14 passed** in 0.12s | ✅ |
| tools/test_diag_wizard.py（diag_wizard） | **18 passed** in 0.35s | ✅ |
| tools/test_acceptance_run.py（acceptance_run） | **42 passed** in 5.18s | ✅ **42 基线精确命中此根** |

> 口径说明：commit 历史反复出现的 "pytest 42/42" 基线 = `tools/test_acceptance_run.py` 这一根（42 passed），并非五根合计。五根合计为 34+37+14+18+42 = 145 passed。本块如实区分两者，不混为一谈。

### 4. APK（mobile/）

| 项 | 状态 |
|---|---|
| 历史产物 | `mobile/build/app/outputs/flutter-apk/app-debug.apk` 存在，163,837,755 字节，2026-10-02 08:21 构建；sha1 = 3e7e0dc65fcad1b90e4690ec588f4977080ab8b7 |
| 本次新构建 | **环境性失败**：`flutter build apk --debug` 报错 "Java version 11.0.32 incompatible with Gradle 9.3.1"（Gradle 9.3.1 需 JDK 17+，系统仅有 `/usr/lib/jvm/java-11-openjdk-amd64`，无 JDK 17） |
| 结论 | APK 编译现状 = 历史 debug 包可用；云端无法复建新包（JDK 版本墙），如实记录，未伪造构建成功 |

---

## 二、各 Phase 交付声明逐项核对（已做 vs 实际存在）

核对方法：对照 `docs/learn/phase{25..40}/_PHASE*_SPEC.md` 及对应 commit message，grep 源码/测试/文档落点，核对"声明做了"是否"实际存在"。

### 命中且属实（代码/测试/文档真实存在）

| Phase | 关键声明 | 实测落点 | 结论 |
|---|---|---|---|
| P25 | ControlHub HTTP JSON 端点 + 移动端远程解码面板 | `cpp/src/control/control_http_server.{h,cpp}`；`mobile/lib/widgets/remote_decoder_panel.dart`；端口 tokens.h:1006 = 50732 | ✅ |
| P26 | 工具化 14→35、写门 gate | `cpp/tests/test_tool_registry.cpp` kAllCxxTools 实测 **35 个**（含 get_m17_calls，正则需含数字才数全）；kExpectedWriteTools = 22 | ✅ |
| P27 | HTTP 生产接线 + ScanActivityLink 引擎级 | main_window.cpp:3012 new HttpControlServer + startDefault()；`cpp/src/dsp/scan_link.{h,cpp}` | ✅ |
| P30 | Chromebook 文档重写 + CMake arm64 探测 | `docs/CHROMEOS_CROSTINI_SDR.md` 269 行；CMakeLists.txt:52-64 CMAKE_SYSTEM_PROCESSOR arm64 分支 | ✅ |
| P31 | Agent 能力文档/错误恢复 + 学习笔记 | `cpp/src/ai/tool_schema.{h,cpp}`；phase31/ 四篇笔记（context-compaction/multi-session/streaming/tool-calling-boundary） | ✅ |
| P32 | 429/503 状态码透传 + 移动端会话页 | llm_client.cpp:214 HttpStatusCodeAttribute→r.httpStatus；`mobile/lib/services/chat_session_store.dart` | ✅ |
| P33 | refs.bib 19 处 cite 接线 + LaTeX 全流程 | **权威文件 `docs/learn/phase7/paper/main.tex`：19 处 \cite、12 唯一 key、bibliographystyle/bibliography 已解注释（321-322 行）**；refs.bib 12 条目；main.pdf 571KB；build-log.txt；CITATION-MAP.md | ✅ |
| P34 | acceptance_run.sh --event + 接收端结论 | `tools/acceptance_run.sh` --event/--event-modes/--freq-sstv/--freq-ssdv；`tools/acceptance_lib.py:91 resolve_event_modes`；recv-chain-conclusion.md/event-checklist.md/event-rx-runbook.md | ✅ |
| P35 | SDR++ 五模块笔记 + AGC 前瞻/maxHold 衰减 | phase35/ 五篇笔记 + gap-table + counterexamples；agc.cpp:44 块级前瞻；spectrum_display.cpp:342 kMaxHoldDecayDb | ✅ |
| P36 | GNU Radio 五机制笔记 + 窗口 RMS 归一化/AGC reset 快捕 | phase36/ 五篇笔记 + gap-table + counterexamples；power_spectrum.cpp:128-132 unit-RMS；agc.cpp:59-62 reset 首块均值快捕 | ✅ |
| P37 | UI 裸数审计 token 化 + 三态截图 | phase37/ui-audit.md + mobile-audit.md；screenshots/ 12 张（standard/short/hidpi 各 4）；tokens.h 326 处 constexpr | ✅ |
| P38 | 性能基准 + C++ 无 AMR 声明 | phase38/bench-baseline.md + test-stability.md；grep `\bamr\b` cpp/src/ **零命中**，与"无 AMR"声明一致 | ✅ |
| P39 | 移动端能力矩阵 + G1/G7/G6 真差距补做 | phase39/capability-matrix.md + architectural-diffs.md；ai_client.dart:436 compactHistory；chat_session_store.dart:234 renameSession；coordinates.dart:136 dopplerShiftFromRangeRateHz | ✅ |
| P40 | qt_audio_sink 重试节流 + startup_robustness + ci.yml | qt_audio_sink.cpp:25 kNoDeviceRetryMs=2000；CMakeLists.txt:587 startup_robustness；.github/workflows/ci.yml timeout-minutes:60 + requirements.txt | ✅ |

### 不实项清单（发现的问题，按严重度排序）

> 经逐项核对，**未发现"声称做了但代码/测试/文档完全不存在"的虚构交付**。以下为需如实标注的不一致/缺口：

| # | 类型 | 说明 | 严重度 |
|---|---|---|---|
| 1 | 文档缺口 | **Phase24 / Phase28 / Phase29 无 `docs/learn/phaseXX/` 交付报告目录**。但其代码交付物实际存在（P24 setNetworkAudioSink 在 spectrum_engine.cpp:646；P28/29 ai_session_store + ai_context.cpp:76 compactContext）。即"代码在、报告缺"，非虚构，但缺交付声明文档可核 | 低 |
| 2 | 打包副本失同步 | `paper_submission_package/sources/main.tex` 是**过期副本**：0 处 \cite、bibliography 仍被注释（182-183 行）。权威文件 `docs/learn/phase7/paper/main.tex` 才是 19 处 cite 已接线的正确版本。投稿包副本未与权威版同步，若误用副本会误判 P33 未完成 | 中 |
| 3 | 口径澄清（非不实） | 任务/历史 commit 的 "pytest 42 基线" 实指 `tools/test_acceptance_run.py` 单根 42 passed，而非五根合计（145）。报告需区分，避免对账时误读 | 低 |
| 4 | APK 环境墙 | 历史 app-debug.apk 可用，但云端因 JDK 11/Gradle 9.3.1 版本不匹配无法复建新包（非代码缺陷） | 低（环境性） |

---

## 三、核对方法（可复现）

1. **C++ 构建**：`cd cpp/build && make -j2`（OOM 约束，续建 Phase40 被截断的 qt_audio_sink.h 触发重编）。
2. **ctest**：`QT_QPA_PLATFORM=offscreen ctest --output-on-failure -j2`（PATH/LD_LIBRARY_PATH 注入 Qt6.8.2 gcc_64）。基线 = 127 全过 + startup_robustness 新增 + e2e_smoke SKIP_RETURN_CODE 77 Skipped。
3. **Flutter**：`cd mobile && flutter test`（基线 351）+ `flutter analyze`（基线 0）。
4. **pytest**：逐根 `python3 -m pytest -q <root>`（QT_QPA_PLATFORM=offscreen），五根 = experiments/tests、tools/onboarding、tools/hw_selfcheck、tools/test_diag_wizard.py、tools/test_acceptance_run.py。
5. **交付声明核对**：`grep` 各 SPEC/commit 声称的符号/文件落点于 cpp/src、cpp/tests、mobile/lib、docs/learn/phaseXX，确认"声明=存在"；对工具计数、cite 计数等用含数字字符的正则避免漏匹配（get_m17_calls、19 处 cite 均由此数全）。

## 四、未解决项 / 遗留

- **APK 复建**：需 JDK 17 环境（云端仅 JDK 11）；历史 debug 包已存在可用。
- **Phase24/28/29 交付报告缺失**：代码已落地但无 docs/learn 报告目录可核，如需闭环应补报告（非本块职责）。
- **投稿包 main.tex 副本失同步**：`paper_submission_package/sources/main.tex` 应与 `docs/learn/phase7/paper/main.tex` 权威版同步。
- 本块未改动任何功能代码；未 commit/push；仅新增本文件。
