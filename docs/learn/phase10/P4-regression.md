# P4-回归复查：第八/九阶段改动对照

> 基线：HEAD = `d709e68`（phase9）。本批对照 phase7(`b6c1002`)→phase9(`d709e68`)
> 的改动文件，逐项映射到测试并复跑。
> 稳定性问题（tests/ 大套件）见 [`P4-stability.md`](./P4-stability.md)。

## 0. 复跑环境与总基线

- C++：`LD_LIBRARY_PATH=<Qt6.8.2/lib> QT_QPA_PLATFORM=offscreen ctest --test-dir cpp/build`
  → **100% tests passed, 0 failed out of 94**（real 210.59s）。**94 基线不破。**
- Flutter：`mobile/` flutter test **298 passed**、flutter analyze **No issues**（本批未动 Flutter）。
- Python：experiments/tests 34、mbdsdr_ai/tests 78+7skip、tools/onboarding 24、
  tools/hw_selfcheck 14 均 PASS；根 `tests/` 的确定性增益回归已修（见 §2.5）。

## 1. 改动文件 → 测试 → 复跑结果 → 结论

| # | 改动 file:line（phase7→9 新增/改动） | 对应测试 | 复跑结果 | 结论 |
|---|---|---|---|---|
| 1 | `cpp/src/ui/shortcuts_catalog.h:35` `shortcutCatalog()` 目录；`:54 nudgeFreqHz`、`:60 nudgeBandwidthHz`、`:66 cycleStepIndex` | `cpp/tests/test_shortcut_catalog.cpp` → ctest **#92 shortcut_catalog**；`test_shortcuts.cpp` → **#20 shortcuts** | #92 Passed 0.23s；#20 Passed 26.31s | 通过。快捷键目录与频率/带宽/步进退进纯函数行为正确 |
| 2 | `cpp/src/ui/shortcuts_catalog.h:78 stepGainDb`（增益档步进）+ 增益档 | `test_tuner_gain_table.cpp` → ctest **#46 tuner_gain_table**；`test_gain_control_model.cpp` → **#51 gain_control_model** | #46 Passed 0.12s；#51 Passed 0.11s | 通过。增益表取值/步进与控制模型一致 |
| 3 | `cpp/src/ui/status_format.h:19 fmtStripSampleRate`、`:25 fmtStripVfoFreq`、`:31 fmtStripGain`、`:38 fmtStripSource`、`:45 fmtStripSquelch`、`:50 fmtStripRssi`、`:55 fmtStripSnr`（状态栏 ~1Hz 格式化抽成纯函数） | `cpp/tests/test_status_format.cpp` → ctest **#93 status_format** | #93 Passed 0.23s | 通过。状态栏各字段格式化与旧内联拼接等价 |
| 4 | `cpp/src/ui/spacetime_format.h:66 spTileDevice`、`:78 spTileSignal`、`:100 spTileGnss`、`:116 spLineTimeSource`、`:137 spLineDoppler`（时空四格/行纯格式化） | `cpp/tests/test_spacetime_format.cpp` → ctest **#94 spacetime_format** | #94 Passed 0.22s | 通过。时空设备/信号/GNSS/时间源/目标/多普勒格式化正确，无硬件走空态 |
| 5 | `cpp/src/ui/main_window.cpp:87` include spacetime_format.h；`:696-713` 时空页 GNSS 串口栏（设备/波特率/连接/状态） | ctest **#94 spacetime_format**（纯逻辑）+ 全量 ctest 中各 `ui_screenshot_*`（含 offscreen 构建 MainWindow） | #94 Passed；全量 **94/94** 全过 | 通过。时空视图接线可在 offscreen 构建，空态诚实 |
| 6 | 多 VFO：`test_multi_vfo.cpp` / `test_vfo_audible.cpp`（多 VFO 面板与可听 VFO） | ctest **#41 multi_vfo**、**#52 vfo_audible** | #41 Passed 1.39s；#52 Passed 0.71s | 通过。多 VFO 与可听 VFO 选择行为正确 |
| 7 | 热插拔：`test_engine_hotplug.cpp`（设备插拔/重连） | ctest **#7 engine_hotplug** | #7 Passed 6.24s | 通过。引擎层热插拔/设备存在通知状态机正确 |

>  targeted 9 项（#7/#20/#41/#46/#51/#52/#92/#93/#94）复跑 **9/9 Passed**；
>  全量 ctest **94/94 Passed**，无回归。

## 2. 侧栏：Python 增益档回归（同批发现并修）

| 改动 file:line | 对应测试 | 复跑结果 | 结论 |
|---|---|---|---|
| `mbdsdr_ai/sdr_backend.py:1361 _maybe_apply_first_gain_midpoint()`；`:1359 _UNKNOWN_TUNER_FALLBACK_GAIN_DB=25.4`（空增益表回退安全中点） | `tests/test_benchmark_essence_defaults.py` T1 组（`test_rtl_first_gain_midpoint` / `..._no_override_when_user_set` / `..._empty_table_no_crash`） | 修前：`test_rtl_first_gain_empty_table_no_crash` **FAILED**（assert 25.4==0.0）；修后整文件 **13 passed** | 用例断言过时，已对齐文档化回退行为（详见 P4-stability §3.4） |

## 3. 结论

- 第八/九阶段改动（快捷键目录 / 状态栏格式化 / 时空格式化与接线 / 增益档 / 多 VFO /
  热插拔）逐项复跑，**对应 ctest 关键项全绿，全量 94/94 不破**。
- 本批新发现的唯一确定性失败（增益档空表断言）已安全修复并补对齐，未触动产品逻辑。
- 唯一未闭环项：根 `tests/` 大套件的顺序相关墙钟超时/偶发失败，定性为测试基建扩展性
  问题，处理建议见 P4-stability §5（**待后续批次**）。
