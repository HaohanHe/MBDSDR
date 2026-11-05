# 冗余登记表（Redundancy Register）

> 范围：`D:\mbdsdr\MBDSDR`（Python/PyQt6 桌面 + C++/Qt 内核 + Flutter 移动端）
> 建表基线：`HEAD 5915277c`；删除提交 `c14ac51e`、`1c0464b6`；合并提交 `f529ba62`
> 判据：**每条结论必须有调用方证据（全仓命中数）；命中 0 才可称"无调用方"**。
> 本表只负责"是否冗余、如何处置、风险是什么"；**同一物理量的取值裁定仍以
> [`docs/parameter-unification.md`](parameter-unification.md) 为唯一事实源**，两表冲突时以参数账本为准并回改本表。
> 本表为**单一 owner 文件**（冗余线）；其它线路的登记行经 `D:\mbdsdr\.tmp\` 导出后并入此处，不另开第二份清单。

## 0. 结论速览

共登记 **40 项**，分四类：

| 类型 | 项数 | 已处置（删除/合并） | 必须保留 | 待裁定 |
|---|---|---|---|---|
| 重复实现 | 11 | 1（采样率格式化，**已合并** `f529ba62`） | 10（频谱双实现、频率分精度、错误码标签/下一步/常量、ACARS、dBFS/dBµV、路径引导） | 0 |
| 多真值源 | 11 | 0 | 10（采样率白名单/候选/后端表/AI 参考表/回退/移动端/硬件吸附档/默认值分层/配置路径/工具目录快照） | 1（`module_panel` 5 档） |
| 死代码 | 12 | 2（4 个函数 `c14ac51e`；2 个纯函数 `1c0464b6`） | 9（三录音模块、C++ 同名模块、仅测试模块、未接线方法、触屏控件、两条动态调用误报、测试钉住函数） | 1（约 32 个 library-only 模块） |
| 死分支/死配置 | 6 | 0 | 6（ACARS 死分支、`not_implemented`、零消费常量、全部在用设置键、无死键结论、legacy 别名） | 0 |

**一句话结论**：真正能动的只有 2 组死函数（已删 6 个）和 1 组逐字相同的格式化函数（已合并）；
其余"看起来重复"的，要么是**两种用途**（采样率白名单 vs 候选探测表），要么是
**两个独立 App 各自的实现**（C++ Qt 与 Python PyQt6 无共享运行时），要么是
**动态调用/泛化读取导致 grep 假阴性**，都不得按"简洁"合并。

---

## 1. 取证方法与边界

### 1.1 已执行的取证

```powershell
# 全仓文本命中（含 desktop / mbdsdr_ai / cpp / mobile / scripts / tests / tools / experiments / docs）
git grep -n --no-color "<symbol>"
rg -c "<symbol>" D:\mbdsdr\MBDSDR
```

- 统计范围显式包含 `scripts/`、`tests/`、`docs/`、`cpp/`、`mobile/`、`tools/`、`experiments/`。
- 删除前后各跑同一组测试并对比通过项数（见 §4）。
- 采样率真机事实来自另一条线的实测记录（见 §3）。

### 1.2 明确说"可能漏"的动态调用形态

grep 命中 0 **不等于**没有调用方。本仓库已确认存在以下会漏检的形态，凡涉及都已人工核实：

| 形态 | 实例 | 结论 |
|---|---|---|
| `getattr(self, f"_preset_{suffix}")()` | `desktop/dock_layout_manager.py:225` 拼名调用 `_preset_focus/_preset_analysis/_preset_grid`（`464/478/497`） | 静态零引用，**不是死代码** |
| `__import__(mod, fromlist=...)` + `getattr(obj, attr)` | `mbdsdr_ai/device_capabilities.py:_backend_rate_table` 按 driver 名动态取后端档位表 | 档位表来源不是硬编码 |
| schema 泛化读取 | `item.key[3:]` 剥离 `ai_` 前缀写入 `ai_config`，消费 `ai_max_context_tokens/ai_max_output_tokens/ai_timeout` | 字面量 0 命中，**键在用** |
| Qt `connect` 字符串/`QMetaObject` | 未见字符串形态 connect；槽均以方法引用连接 | 未发现漏检 |
| 字符串拼模块名 / importlib | `mbdsdr_ai` 无 `pkgutil` 自动发现、无 `register_tool_registry` 扫描 | 仅测试模块不会被动态注册 |

> 另：`desktop/module_panel.py` 里 `recorder` 的两处命中（`17`、`327` 行）是
> **sink 下拉框文案**，不是 import，不计作调用方。

---

## 2. 登记表

### A. 重复实现

| # | 冗余项 | 类型 | 证据（位置 + 命中数） | 处置 | 风险 |
|---|---|---|---|---|---|
| A1 | C++ `detectPeaks` 与 Python `find_peaks_with_snr/_detect_peak_bins` 各一套峰值检测 | 重复实现（跨端） | C++ `cpp/src/dsp/peak_detector.cpp:11`，全仓 9 处（生产 1：`cpp/src/ui/spectrum_display.cpp:1094`；测试 2 文件；声明+注释）。Python `desktop/spectrum_widget.py:655`（生产 2 处：`:613 advance_peak_tracks` 帧跟踪、`:1752` 标签生成）、`:697`（`:628` 调用 + 3 测试文件，27 处） | **保留双实现，不合并**。两套定义差异 8 条已由 `docs/parameter-unification.md` D.6/D.7 取证（`:578-599`） | 合并会改用户可见峰值标签：Python 独有宽通道吸收、C++ 独有 9 帧陈旧保持；D.7 实测完整定义链 30/30 一致但显示帧 484/484 陈旧保持——**同一输入两端标签不同** |
| A2 | `find_peaks()` docstring 称"双通道"但实际委托单通道 | 陈旧注释 | `desktop/spectrum_widget.py:682-694`；调用方仅 3 个测试文件（`test_spectrum_scale_peak_range.py`、`test_dsp_perf_optimizations.py`、`test_no_sim_regression.py`），生产 0 | 保留函数，**只登记注释陈旧**（本次不改代码） | 下一个人按注释判断"已有宽通道合并"会误删 `_detect_peak_bins` 的宽通道逻辑，改变可见结果 |
| A3 | `desktop/constants.format_sample_rate` 与 `mbdsdr_ai/sdr_tools._fmt_sample_rate` 逐字相同 | 重复实现（同字面量） | 原位置 `desktop/constants.py:30-45`、`mbdsdr_ai/sdr_tools.py:3057`。全仓 57 处 / 9 文件；测试 `tests/test_copy_units_audit.py:27-34` 与 `:299-304` 两边逐字断言 | **已合并**（`f529ba62`）：唯一实现在 `mbdsdr_ai/sdr_backend.format_sample_rate`，`sdr_tools._fmt_sample_rate` 改为别名，`desktop/constants` 直接复用（`format_sample_rate is sdr_backend.format_sample_rate` 成立） | 低。`desktop/constants` 保留 mbdsdr_ai 缺失时的**安全回退**（与 `DEFAULT_SAMPLE_RATE_HZ` 同一模式），回退副本仅打包兜底，不是第二真值源 |
| A4 | 频率格式化 5 份不同小数位 | 重复实现（不同字面量） | `desktop/status_panel.py:82`（≥1 MHz 6 位 / kHz 3 位）；`desktop/vfo_panel.py:43`（MHz 3 位 / kHz 1 位）；`desktop/rf_sky_view.py:1147`（1 位）；`mbdsdr_ai/device_capabilities.py:68 format_hz`（自适应 2/1/0 位）；`cpp/src/ui/status_format.h:19/26/36`。命名命中 18 处 / 5 文件 | **保留，禁止合并** | 合并会改显示精度：状态栏 6 位是给频偏读数的，VFO 3 位是给调谐盘位的；统一成任一种都会丢精度或产生无意义尾零 |
| A5 | 路径根/`sys.path` 引导重复 | 重复实现（同功能） | `_REPO_ROOT`/`REPO_ROOT=` 70 处 / 52 文件；`sys.path.insert(...parents/dirname)` 103 处 / 100 文件（生产约 13 个：`desktop/main.py`、`main_window.py`、`module_panel.py`、`mcp_worker.py` 等） | **保留**（脚本可独立执行，引导必须就地）；如需收敛，另开"启动引导"专项 | 强行抽公共函数会让脚本在"仓库外被拷走"时全部失效 |
| A6 | 错误码语义源 | 部分收敛（不是单纯冗余） | `mbdsdr_ai/error_catalog.py:698 ERRORS` 是码表/标题/详情/处置的单一源；`desktop/error_remedy.py:38` 派生 `REMEDY_BY_CODE`（`:42`）、`mapping_gaps()`（`:117`）、`overlaps()`（`:125`）；`docs/error-catalog.md` 为镜像，`tests/test_error_catalog_guard.py:225-226` 防漂移 | **保留 error_catalog 为唯一语义源**；但解码原因码的短标签与"下一步"另有两份拷贝（见 A7/A8），原"已完全收敛"判定**过宽，已改判** | 同一码两处措辞不同：catalog「未知数字模式」vs 状态栏「未知模式（能力表未登记）」 |
| A7 | `desktop/status_panel.py:185 DECODE_ERROR_LABELS` | 多真值源（局部，账本 R8） | 至少 11 条短标签；与 `mbdsdr_ai/error_catalog.py:226-249` 的 `unknown_mode/missing_dependency/missing_sample/unsupported_format/decode_error/not_implemented` 同码不同文案 | **保留**（界面短标签）；建议后续从 catalog 派生短标题，本轮不改 | 新错误码只改 catalog 时，状态栏仍显示旧短文案或退回裸码 |
| A8 | `desktop/troubleshooting.py:249 CODE_NEXT_STEPS` | 多真值源（局部，账本 R8） | 至少 8 条逐码"下一步"；与 catalog 每个码第 4 参数（remedy）在 `insufficient_samples`/`invalid_wav`/`no_frame_recovered` 等重叠 | **保留分层，禁止直接合并** | 同一码两处建议不同步（catalog 说"重录一整帧"，排障页说"≥5s"），用户照哪条都可能失败 |
| A9 | 解码错误码常量 3 套 | 重复定义（账本 R9） | `mbdsdr_ai/decode_router.py:51-61`、`mbdsdr_ai/decode_pipeline.py:54-64`、`mbdsdr_ai/decoders.py:871/1037` 裸串；catalog 另有同一批码 | **保留待合并**（需先确认无外部脚本按名导入 `ERR_*`/`REASON_*`） | 漏改一处会让路由与管线对同一输入判定分裂（一处接受、一处拒绝） |
| A10 | ACARS 双实现（账本 R3） | 重复实现（已收敛） | `mbdsdr_ai/acars_protocol.py` 13 命中；`mbdsdr_ai/acars_decoder.py` 33 命中；`mbdsdr_ai/decoder_registry.py` 为唯一入口；`tests/test_acars_consistency.py` 对拍 | **保留，走公开入口** | 绕过入口直调自带状态机会出现自环 CRC `crc_ok=False`，与统一入口结果不一致 |
| A11 | dBFS / dBµV 两套格式化（账本 R7） | 重复实现（不同量纲） | `desktop/status_panel.py:49/62`；`desktop/main_window.py:87 _fmt_dbfs`（0 生产调用、13 测试引用）；`:105` 1 调用 | 保留量纲两份；`_fmt_dbfs` 另见 C11 | 把 dBµV 当 dBFS 合并会差约 107 dB（50Ω/75Ω 基准不同） |

### B. 多真值源：采样率档位逐份清单

> 采样率是本次最容易被误判成"冗余"的一组。**逐份列字面量与消费方**，不做合并。

| # | 表 / 位置 | 字面量 | 消费方（命中） | 判定 |
|---|---|---|---|---|
| B1 | C++ 可下发白名单 `cpp/src/core/tokens.h:287 kSampleRatesHz` | `1.024 / 2.048 / 2.4 / 3.2 MHz`（4 档） | `cpp/src/control/control_hub.cpp:503/507`（档外显式拒绝） | **保留 = 控制面白名单**。与 B2 是"白名单 ⊆ 候选"关系，**不许合并** |
| B2 | C++ 设备候选探测表 `cpp/src/dsp/device_capabilities.cpp:36-48 kCandidateRatesHz` | `250k / 1024k / 1536k / 1792k / 1920k / 2048k / 2160k / 2400k / 2560k / 2880k / 3200k`（11 档） | `:128 for (const double r : kCandidateRatesHz)` 按设备实际范围过滤 | **保留 = 候选探测表**。`:46` 已是 `2_880_000`：真机请求 `2,800,000` 被设备吸附到 `2,880,000`（读回 +80,000 Hz，E3，见 `limitations-register` 附 M.4 L60），候选表只列设备实际给出值 |
| B3 | Python 后端档位 `mbdsdr_ai/sdr_backend.py:1442 RTLSDRBackend.SAMPLE_RATES` | 与 B2 **同 11 档**（含 `2_880_000`） | `mbdsdr_ai/device_capabilities.py:198` 动态 `__import__` 取表 → `sample_rate_options()`；`desktop/control_panel.py:1253` 间接消费 | **保留**：Python 侧后端自报档位，无 C++ 运行时共享，不能"派生自 C++" |
| B4 | Python AI 参考表 `mbdsdr_ai/rtlsdr_params.py:139-150 SUPPORTED_SAMPLE_RATES` | `250k / 1024k / 1536k / 1800k / 1920k / 2000k / 2048k / 2400k / 2560k / 3200k`（**10 档，与 B2 不同**） | **生产消费方存在**：`mbdsdr_ai/tool_registry.py:2583`、`:2618` 作为 AI 工具 `recommended_rates_hz` 输出；测试 `tests/rtlsdr_params_test.py:101` | **保留 = AI 工具推荐表（故意不同）**：含 1.8M/2.0M 常用收音机档，缺 1.792M/2.16M/2.88M。它不驱动任何 UI 下拉，**不参与"可见档位"** |
| B5 | 桌面对面板硬编码 `desktop/module_panel.py:305-308` | `1.024 / 1.44 / 2.048 / 2.4 / 3.2 MHz`（5 档） | `:395 selected_sample_rate_hz()` → `:437 dev["_sample_rate"]`；接线 `desktop/main_window.py:1581 connect`、`:7011 _on_module_device_selected` | **待裁定**：唯一会改变用户可见选项的一份，且 **1.44 MHz 不在任何内核表**（B1/B2/B3/B4 均无）。按纪律"热点文件被占只出清单"，`module_panel.py` 当前无 claim，但改动会改可见行为 → **本次只登记不动手**，建议改为读 `sample_rate_options()` |
| B6 | 桌面默认值回退 `desktop/constants.py:16-20` | `DEFAULT_SAMPLE_RATE_HZ`（优先导入内核值，失败回退 `2_400_000`） | 全桌面共享 | **保留**（安全回退，与内核基准一致，不是新真值） |
| B7 | 移动端历史默认 `mobile/lib/app/tokens.dart:222-227 sampleRatesHz` | 4 档，已标 `@Deprecated('历史默认，不被任何人读取')` | 全仓 Swift/Dart 代码命中 **0**；仅 `mobile/lib/pages/spectrum_page.dart:761` 一句注释 | **保留**：删除条件写在 `tokens.dart:218-220` = "确认无外部引用且旧版本回滚窗口关闭"。回滚窗口无法从仓库核实 → 不删 |
| B8 | 真机硬件吸附档 `2,800,000 → 读回 2,880,000` | — | 另一条线真机逐档实测：**11 档一致，仅 2800k 档驱动返回成功但静默吸附到 2880k（+80 kHz）**；`rtl_sdr_source` 侧 `rtlsdr_get_sample_rate` 读回已接上 | **不是冗余，不许删**。任何把 2.88M 当"多余档位"删掉的判断都是错的；该档的价值是"必须靠读回才发现"的负样本 |

### C. 死代码 / 无人调用

| # | 冗余项 | 类型 | 证据（位置 + 命中数） | 处置 | 风险 |
|---|---|---|---|---|---|
| C1 | Python `mbdsdr_ai/recording_library.py` | 死代码（桌面） | `desktop/` 命中 **0**；`tests/` 6 个文件 import（`test_recording_library.py`、`_retention`、`_migration`、`_stats_audio`、`_concurrency`、`test_e2e_operation_scenarios.py`） | **不删、不接线**（承载库行为与测试） | 删除会让 6 个测试失去被测对象；接线属产品决定。登记为 **library-only** |
| C2 | Python `mbdsdr_ai/recorder.py` | 死代码（桌面） | `desktop/` import 命中 0（仅 `module_panel.py:17/327` 两处下拉文案）；消费方：`mbdsdr_ai/recorder_enhanced.py:28`、`scripts/real_hw_acceptance.py:434`、`scripts/hw_acceptance/ring4_recording_playback.py:61`、5 个 recording_library 测试 + `test_tx_session.py` / `test_tx_no_hardware.py` / `test_playback.py` / `mbdsdr_ai/tests/test_recorder_bitdepth.py` / `test_transmit_doppler.py` | **不删** | 桌面直接写盘绕过它（见 C3），删了脚本/live 验收先断 |
| C3 | 桌面"直接写盘 + `baseband_io.load_iq` 回放" vs `recorder`/`playback` 并行链路 | 重复实现（功能重叠、行为不同） | 桌面直写：`desktop/main_window.py:6468`、`:6598`（`baseband_io.write_sidecar`）；桌面回放：`:8093-8098`（`baseband_io.load_iq`）。`playback` 真实消费方：`tools/onboarding/onboard.py:471`、`experiments/common/datasource.py:101`、`experiments/exp_ota_run.py:198`、`experiments/demo_spacetime.py:49`、`tools/onboarding/test_onboarding.py:142` + 多测试 | **保留两条，登记为并行实现**；不合并、不接线 | `playback` **不是死代码**（原计划判"三模块全死"已改判）。两条链路对元数据/SigMF 处理不同，合并会改桌面回放行为 |
| C4 | C++ `cpp/src/ui/recording_library.cpp` | 同名不同物 | 被 `cpp/src/ui/main_window.h` 引用；`cpp/CMakeLists.txt` 构建；测试 `cpp/tests/test_recording_library.cpp`、`test_playback_loopback.cpp` | **保留** | 与 Python C1 同名但无任何关系；按名字判冗余会误删在用的 C++ UI 模块 |
| C5 | 4 个全仓零调用函数：`themes.get_monospace_font()`、`shortcuts.registered_keys()`、`mode_registry.display_names()`、`device_capabilities.unknown_values()` | 死代码 | 删除前全仓代码命中 0（含字符串/`getattr` 形态）；删除后仅 `docs/audit_r2/43_ui_status_themes_mcp.md:81-84/279`、`FINAL_REPORT_R2.md:393` 这些**历史审计报告**提及 | **已删除**，提交 `c14ac51e`（4 files, 26 deletions） | 无。删除前后测试 91→91（见 §4） |
| C6 | 约 32 个 `mbdsdr_ai` 模块仅被自身测试引用（`aigui_demo`、`background_decoder`、`block_stream`、`channel_analyzer`、`flowgraph_runtime`、`gain_calibration`、`gps_tracking`、`hal_enhanced`、`multi_user`、`nanovna_v2_proto`、`protocol_analyzer`、`remote_control_v2`、`spacetime_predict`、`spectrum_accumulator`、`tc_cltu`、`webspectra`、`ysf_demod`、`janvna2_proto`、`marker_math`、`mimo_v2`、`module_graph`、`module_system`、`feature_manager`、`frequency_manager_v2`、`sat_calibration`、`position_service`、`recorder_enhanced`、`optional_deps`、`sdrpp_decoders`、`lrpt_image`、`mobile_server` 等） | 死代码（library-only） | 每个模块全仓非测试、非自身包内命中 0；无 `register_tool_registry`、无 `pkgutil` 扫描 | **保留，需产品裁定** | 删一个模块会让对应测试整批消失，通过项数必然下降（违反"通过项数不许下降"）；且可能是对外 API 面 |
| C7 | 8 个桌面面板零调用公开方法：`set_disconnected`、`set_observer_ready`、`is_anr_enabled`、`submit_frame`、`set_default_params`、`attach_host`、`clear_objects`、`last_capability_report` | 疑似死代码 | 全仓调用命中 0；均定义在 `desktop/` 面板类内 | **保留** | 更像"功能未接线"而非冗余；删除等于消灭待接的接口，且可能被未来 `connect` 使用 |
| C8 | `desktop/touch_helpers.py:214/236` 两个触屏控件类 | 死代码 | 零实例化（全仓 `TouchFriendlyScrollArea(` / `TouchFriendlyListWidget(` 命中 0） | **只登记，不删** | 文档红线明确"不碰触屏（touch_manager/touch_helpers）"，属他人领域 |
| C9 | `dock_layout_manager._preset_focus/_preset_analysis/_preset_grid` | 死代码（**误报**） | 静态零引用，但 `:225 getattr(self, f"_preset_{method_suffix}")()` 动态调用（定义 `:464/478/497`） | **保留** | 按静态 grep 删掉会让"恢复预设布局"直接 `AttributeError` |
| C10 | `ai_max_context_tokens / ai_max_output_tokens / ai_timeout` | 死配置（**误报**） | 字面量命中 0；经 schema 泛化 `item.key[3:]` 写入 `ai_config` 后被消费 | **保留** | 按字面量判死会删掉 AI 设置项 |

### D. 死分支 / 死配置

| # | 冗余项 | 类型 | 证据（位置 + 命中数） | 处置 | 风险 |
|---|---|---|---|---|---|
| D1 | ACARS `unsupported_format` 分支 | 死分支 | `scripts/decoder_capability_report.py:436` 已登记；能力表声明但代码不可达 | **保留** | 删掉后能力表与实现继续漂移且无记录；应修能力表而非删分支（属解码线） |
| D2 | `not_implemented` 结果 | 死分支（仅 1 条可达） | 全仓仅 ft8 路径可达（能力表其余模式无调用） | **保留** | 与 D1 同源，删了会吞掉"未实现"的诚实回报 |
| D3 | C++ `kFreqCoarseStepHz` | 死配置（零消费） | `cpp/src/core/tokens.h` 定义；消费点 0（`docs/parameter-unification.md` 第 5 组已登记）；Python 实际用 `snap_interval` | **保留并沿参数账本处理** | 真接进细调会与 Python 的 0.1×snap 差 10 倍（账本已登记）；本专项不动 |
| D4 | C++ `kSettingsKey*` 设置键 | 死配置（曾疑似） | 35 个全部 ≥2 处使用（定义 + 读/写） | **保留**，判定**无死键** | 无 |
| D5 | Python 配置 schema 键无人读 | 死配置（曾疑似） | 未发现确认死键；字面量 0 的 3 个 `ai_*` 键已由 C10 判为泛化读取 | **保留** | 无 |
| D6 | `_LEGACY_KEY_ALIAS` | 死配置（必须保留） | 旧配置兼容别名 | **保留，不许动** | 删掉会让老用户配置键失效（静默丢设置） |

---

## 3. 因新事实而改变判定的项（对比原计划）

> 这一节专门记录"原计划怎么判 → 事实是什么 → 现在怎么判"，避免下一个人再走一遍误判。

| 项 | 原计划 | 新事实（另一条线实测） | 现判定 |
|---|---|---|---|
| C++ 采样率 4 档 vs 11 档 | 判为"多真值源，应收敛成一份内核真值 + 其余派生" | 4 档 = **可下发白名单**（`tokens.h:287`，档外显式拒绝，`control_hub` 刚补校验）；11 档 = **设备候选探测表**（`device_capabilities.cpp:36`）。关系是 **白名单 ⊆ 候选**，不是同一物理量的两套真值 | **不合并**；登记表逐份写明用途（B1/B2），避免下一个人再判成冗余 |
| 2.8M 档 | 曾按"候选表里多余档位"考虑删除 | 真机逐档回读：11 档一致，**仅请求 2,800,000 时驱动返回成功但读回 2,880,000（+80,000 Hz）** | **不删，且必须保留**（B8）。它的价值是"硬件静默吸附、只能靠读回发现"的证据；`rtl_sdr_source` 侧 `rtlsdr_get_sample_rate` 读回已接上，"读数≠请求即报警"会覆盖它 |
| `recording_library` / `recorder` / `playback` | 三模块"全仓无桌面调用方"，按死代码处理 | `recording_library`：桌面 0、6 测试；`recorder`：桌面 0，但脚本/验收/测试在用；`playback`：**不是死代码**（`tools/onboarding/onboard.py:471`、`experiments/common/datasource.py:101`、`exp_ota_run.py:198` 在用），桌面回放只是**绕过它**直接走 `baseband_io.load_iq` | **三个都不删、不接线**；登记为 **library-only**（C1/C2）+ **两条并行实现（功能重叠、行为不同）**（C3） |
| 桌面录音/回放 | 视为"直接调用 recorder/playback" | 桌面直写 `baseband_io.write_sidecar`（`main_window.py:6468/6598`），回放 `baseband_io.load_iq`（`:8093-8098`），从未 import 两模块 | 从"调用方证明"改为"并行实现登记"，不删任何一侧 |

**被这四条事实否掉的原计划动作**：① 采样率表收敛；② 删除 2800k/2880k；③ 三录音模块当死代码清理；④ 把桌面录音判成"未走统一入口需改接"。以上四项本轮全部**不做**。

---

## 4. 已执行的删除与反证

### 4.1 删除内容（提交 `c14ac51e`）

| 文件 | 删除 | 原位置 |
|---|---|---|
| `desktop/themes.py` | `get_monospace_font()` 15 行 | 文件末尾 |
| `desktop/shortcuts.py` | `registered_keys()` 3 行 | 文件末尾 |
| `desktop/mode_registry.py` | `display_names(category)` 5 行（`mode_names()` 保留） | 原 203-205 |
| `mbdsdr_ai/device_capabilities.py` | `unknown_values(report)` 3 行 | 文件末尾 |

`git show --stat c14ac51e`：`4 files changed, 26 deletions(-)`

### 4.2 删除反证（通过项数不许下降）

```powershell
python -m pytest tests/test_theme_switch.py tests/test_theme_tokens.py \
  tests/test_shortcuts_guard.py tests/test_shortcut_focus_priority.py \
  tests/test_mode_registry.py tests/test_mode_switch_preset.py \
  tests/test_device_capability_probe.py tests/test_sample_rate_options.py -q
```

| 时点 | 结果 |
|---|---|
| 删除前 | **91 passed**, 4 warnings |
| 删除后 | **91 passed**, 4 warnings |

另：`tests/test_hardcoded_parameter_registry.py` 单独运行 **26 passed**（峰值定义对拍未受影响）。

### 4.3 吞错守卫（棘轮）越线核查

`tests/test_swallow_guard.py::test_repo_ratchet_within_baseline` 失败，**不是本线越线**：
越线文件都是他线已提交、但未同步刷新 `docs/code-guards-baseline.json` 的存量/新增文件；
本线改动文件（`desktop/constants.py`、`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/sdr_tools.py`、
`desktop/config_schema.py`、`tests/test_desktop_constants.py`）**不在清单内**，也没有新增 `try/except`。

| 越线文件 | 越线计数 | 归属 |
|---|---|---|
| `mbdsdr_ai/reconnection.py` | WARN 3 > 基线 2（+1） | 他线（HEAD 已提交） |
| `scripts/hw_acceptance/common.py` | ERROR 1 > 基线 0（+1） | 他线（HEAD 已提交） |
| `scripts/hw_acceptance/manual_usb_cycle.py` | 基线无此文件（2 ERROR / 4 WARN） | 他线新增（HEAD 已提交） |
| `scripts/perf_probe.py` | 基线无此文件（1 ERROR / 2 WARN） | 他线新增（HEAD 已提交） |
| `tests/test_link_state_visibility.py` | 基线无此文件（1 ERROR） | 他线新增（HEAD 已提交） |
| `desktop/main_window.py` | 首轮 ERROR 228 > 227，次轮消失 | 他线**未提交**改动，随其保存波动 |

总量 `1293~1294 > 基线 1284`（+9~+10）。**不得为此上调基线**；基线刷新归造成越线的线路，
本线只做登记。此前"未提交的新文件"表述不准确，已按上表更正为"已提交但基线未更新 + 一条未提交波动"。

---

## 5. 待裁定项：本线推荐 + 理由

| 项 | 推荐 | 理由 | 采纳后的验证 |
|---|---|---|---|
| B5 `desktop/module_panel.py:305-308` 的 1.44 MHz 档 | **删除硬编码 5 档，改读 `sample_rate_options()` 能力驱动**；1.44M 只有产品明确需要时才补进 C++ 候选表 + 后端 `SAMPLE_RATES` 并做真机读回 | 1.44M 不在 B1/B2/B3/B4 任何一张表，是唯一"前端自造档位"：连接后校验可能被 `control_hub` 白名单拒绝，或设备根本不支持。能力驱动后可见档=设备真实可锁档，并天然继承 2800/2880 吸附读回 | 同一 RTL-SDR 连接后，下拉项集合 == `sample_rate_options(device)`；截图基线确认可见项变化 |
| C6 约 32 个仅测试引用模块 | **不删，按三类归档**：①对外 API（`webspectra`、`mobile_server`、`remote_control_v2`、`protocol_analyzer`）→ 保留 + 补外部入口说明；②纯实验（`aigui_demo`、`flowgraph_runtime`、`spacetime_predict`、`mimo_v2`、`module_graph`、`module_system`）→ 移 `experiments/` 或标 `@experimental`；③领域解码（`ysf_demod`、`tc_cltu`、`lrpt_image`、`sdrpp_decoders`）→ 保留待能力表接线 | 删除会整批减少测试（违反"通过项不许下降"）；这些模块承载协议解析/错误码，真正问题是"未接线"而非"重复" | 归档只改目录/标记，测试数与 import 路径不变 |
| C7 8 个零调用面板方法 | **保留并贴标签，分两批**：`set_disconnected`、`set_observer_ready`、`last_capability_report`、`is_anr_enabled` → 下迭代**接线**（连接状态/观测者/能力报告是已有功能缺口）；`submit_frame`、`set_default_params`、`attach_host`、`clear_objects` → 两个版本内无调用方再删 | 它们是接口不是重复实现，删掉等于抹掉待接功能；先标记既能避免误删，也不会谎报"已清理" | 接线后每个方法至少 1 个生产调用；未接线项保持"待接线"标签 |
| A3 采样率格式化合并 | **已完成**（`f529ba62`） | 逐字相同，方向"内核→桌面"；桌面保留打包安全回退 | `constants.format_sample_rate is sdr_backend.format_sample_rate`；53 passed |
| C3 桌面写盘 vs `recorder`/`playback` | 不删不接线（录音/回放线持线） | 两条链路产物不同（裸 IQ vs 带 meta） | 由该线决定 |
| A1 频谱双实现 | 维持双实现 | 两个独立 App，8 条定义差异会改可见峰值 | 统一前须跑 D.7 对拍 |

---

## 6. 账本线登记行并入（R1–R20）

> 来源：`D:\mbdsdr\.tmp\account_redundancy_rows.md`（账本线导出 20 行，不是 16 行）。
> 去重规则：同项合并到本表一行；冲突以可复核证据为准（见 6.3）。本表是唯一清单，导出文件不再维护。

### 6.1 去重映射

| 账本行 | 本表落点 | 并入方式 |
|---|---|---|
| R1 频谱双实现 | A1 | 已一致，补 Python 生产调用 `:613` |
| R2 4 档 vs 11 档 | B1 + B2 | 已一致 |
| R3 ACARS 双实现 | **A10（新增）** | 新登 |
| R4 录音库同名 | C1 + C4 | 已一致 |
| R5 桌面录制/回放旁路 | C3 | 行号按当前树校正为 `:6405/:6468/:8105` |
| R6 频率格式化 4 套 | A4 | **冲突**：本表证据为 5 份；裁定见 6.3 |
| R7 dBFS/dBµV 两套 | **A11（新增）** | 新登 |
| R8 错误码文案 3 套 | A6（改判）+ **A7/A8（新增）** | 拆分，见 6.3 |
| R9 错误码常量 3 套 | **A9（新增）** | 新登 |
| R10 `~/.mbdsdr` 路径 | **B10（新增）** | 新登 |
| R11 移动端工具目录快照 | **B11（新增）** | 新登 |
| R12 默认采样率 2.4/2.048 | **B9（新增）** | 新登 |
| R13 Python 录音库/recorder | C1 + C2 | 已一致 |
| R14 `_fmt_dbfs` | **C11（新增）** | 新登 |
| R15 ACARS `unsupported_format` | D1 | 已一致 |
| R16 `not_implemented` | D2 | 已一致 |
| R17 `ai_*` 三键 | C10 | 已一致 |
| R18 `_LEGACY_KEY_ALIAS` | D6 | 已一致 |
| R19 2800k→2880000 | B8 | 已一致，补提交 `dd72ba1c` |
| R20 config_schema 两函数 | **C12（新增，已删）** | 已删 `1c0464b6` |

### 6.2 新增登记行

| # | 冗余项 | 类型 | 证据（位置 + 命中数） | 处置 | 风险 |
|---|---|---|---|---|---|
| B9 | 默认采样率 2.4M vs 2.048M | 多真值源（有意分层，账本 R12） | `cpp/src/core/tokens.h:289-301` 明确 2.4M 为刻意选择、不与 Python 2.048 统一；`mbdsdr_ai/sdr_backend.py` 默认 2.4M | **保留**（设计选择） | 统一会改瀑布宽度与抽取链 |
| B10 | `~/.mbdsdr` 配置目录解析分散 | 多真值源（账本 R10） | `git grep -lF ".mbdsdr" -- "*.py"` = **91 个 tracked 文件**；`expanduser` = 69 个文件（含测试与文档串） | 保留，**禁止直接改落点**；需先出迁移/兼容方案 | 符号链接/漫游目录/OneDrive 重定向下解析不同；直接改会让老用户配置"消失" |
| B11 | 移动端工具目录快照 | 过期多真值源（账本 R11） | `mobile/lib/app/tool_catalog.dart:6/37` 注释称桌面 90 个工具；真源为 `cpp/src/ai/tool_schema.cpp:registeredToolSpecs` | 保留为只读参考，改由生成器刷新 | 数字不一致时会展示不存在的工具或漏掉新工具 |
| C11 | `desktop/main_window.py:87 _fmt_dbfs` | 死代码（测试钉住，账本 R14） | 生产调用 0（文件内仅定义行）；`tests/test_copy_units_audit.py` **13 处**引用 | **保留**（删除必删断言，违反"通过项不许下降"） | 删函数即测试失败；要删须同批改断言并单独提交 |
| C12 | `desktop/config_schema.keys_by_storage` / `is_known` | 死代码（账本 R20） | 全仓仅定义行命中；唯一疑似命中是测试函数名 `test_tx_channels_zero_is_known_no_tx`（子串）；无 `__all__`/getattr/importlib | **已删除** `1c0464b6`（8 deletions）；`test_settings_schema + test_desktop_constants` **131→131** | 无；仓库外按名导入的脚本会受影响（已登记） |

### 6.3 冲突裁定（以证据为准）

| 冲突项 | 账本口径 | 本表复核证据 | 裁定 |
|---|---|---|---|
| R6 频率格式化"4 套、可合并" | 4 套，前置为视觉冻结解除 | 实际 5 份：状态栏（≥1MHz 6 位）、VFO（3 位）、`rf_sky_view`（1 位）、`device_capabilities.format_hz`（自适应）、C++ `status_format.h` 三函数；精度不同是功能差异 | **保留分精度，不计入"安全合并"**；只有"视觉冻结解除 + 重跑截图基线"后才可议 |
| R8 错误码"3 套" vs A6"已收敛" | 3 套文案，`error_catalog` 为语义源 | `error_catalog` 含标题/详情/remedy；`status_panel` 短标签与 `troubleshooting` 下一步是**独立文案**，同码不同措辞 | A6 改判为"**部分收敛**"；拆出 A7/A8，保留但登记漂移风险 |
| R1 Python 生产调用 `:613` vs A1 `:1752` | 账本记 `:613` | `:613 advance_peak_tracks` 与 `:1752` 标签生成都在用；`:628` 调 `_detect_peak_bins` | 两者都对，A1 补成**生产 2 处** |
| R19 2800k | 账本已记 2,880,000 | `cpp/src/dsp/device_capabilities.cpp:46` 已为 `2_880_000`，注释"was 2.8 MS/s, same physical quantity, -80 kHz"（`dd72ba1c`） | B8 维持：档位表写 **2880k**；2800k 只作真机实验请求值 |

---

## 7. 变更记录

| 日期 | 提交 | 变更 |
|---|---|---|
| 2026-11-06 | `c14ac51e` | 删除 4 个全仓零调用死函数（themes/shortcuts/mode_registry/device_capabilities），测试 91→91 |
| 2026-11-06 | `f529ba62` | 采样率显示口径上收内核（`sdr_backend.format_sample_rate` 唯一实现），`sdr_tools`/`constants` 改为派生；53 passed（+1，无下降） |
| 2026-11-06 | `1c0464b6` | 删除 `config_schema.keys_by_storage`/`is_known`（零调用），131→131 |
| 2026-11-06 | （本表更新提交） | 并入账本线 R1–R20（去重后 +10 行，共 40 项）；改判 A6（部分收敛）、A3（已合并）、R6（保留分精度）；修正 §4.3 吞错守卫归属 |
