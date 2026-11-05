# 冗余登记表（Redundancy Register）

> 范围：`D:\mbdsdr\MBDSDR`（Python/PyQt6 桌面 + C++/Qt 内核 + Flutter 移动端）
> 建表基线：`HEAD 5915277c`；本表首个删除提交 `c14ac51e`
> 判据：**每条结论必须有调用方证据（全仓命中数）；命中 0 才可称"无调用方"**。
> 本表只负责"是否冗余、如何处置、风险是什么"；**同一物理量的取值裁定仍以
> [`docs/parameter-unification.md`](parameter-unification.md) 为唯一事实源**，两表冲突时以参数账本为准并回改本表。

## 0. 结论速览

共登记 **30 项**，分四类：

| 类型 | 项数 | 可安全删除/合并 | 必须保留 | 待裁定 |
|---|---|---|---|---|
| 重复实现 | 6 | 1（采样率格式化，建议合并，尚未动手） | 3（频谱双实现、频率格式化、错误码派生） | 2（路径引导、面板未接线） |
| 多真值源 | 8 | 0 | 7（采样率白名单/候选/后端表/AI 参考表/回退/移动端/硬件吸附档） | 1（`module_panel` 5 档） |
| 死代码 | 9 | 1（4 个函数，**已删除** `c14ac51e`） | 8（三录音模块、C++ 同名模块、仅测试模块、未接线方法、触屏控件、两条动态调用误报） | 0 |
| 死分支/死配置 | 7 | 0 | 7（ACARS 死分支、`not_implemented`、零消费常量、全部在用设置键、无死键结论、legacy 别名） | 0 |

**一句话结论**：真正能动的只有 1 组死函数（已删）和 1 组逐字相同的格式化函数（建议合并）；
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
| A1 | C++ `detectPeaks` 与 Python `find_peaks_with_snr/_detect_peak_bins` 各一套峰值检测 | 重复实现（跨端） | C++ `cpp/src/dsp/peak_detector.cpp:11`，全仓 9 处（生产 1：`cpp/src/ui/spectrum_display.cpp:1094`；测试 2 文件；声明+注释）。Python `desktop/spectrum_widget.py:655`（生产调用 `:1752`）、`:697`（内部 2 处 + 3 测试文件，27 处） | **保留双实现，不合并**。两套定义差异 8 条已由 `docs/parameter-unification.md` D.6/D.7 取证（`:578-599`） | 合并会改用户可见峰值标签：Python 独有宽通道吸收、C++ 独有 9 帧陈旧保持；D.7 实测完整定义链 30/30 一致但显示帧 484/484 陈旧保持——**同一输入两端标签不同** |
| A2 | `find_peaks()` docstring 称"双通道"但实际委托单通道 | 陈旧注释 | `desktop/spectrum_widget.py:682-694`；调用方仅 3 个测试文件（`test_spectrum_scale_peak_range.py`、`test_dsp_perf_optimizations.py`、`test_no_sim_regression.py`），生产 0 | 保留函数，**只登记注释陈旧**（本次不改代码） | 下一个人按注释判断"已有宽通道合并"会误删 `_detect_peak_bins` 的宽通道逻辑，改变可见结果 |
| A3 | `desktop/constants.format_sample_rate` 与 `mbdsdr_ai/sdr_tools._fmt_sample_rate` 逐字相同 | 重复实现（同字面量） | `desktop/constants.py:30-45`；`mbdsdr_ai/sdr_tools.py:3057` 起。全仓 57 处 / 9 文件；测试 `tests/test_copy_units_audit.py:27-34` 与 `:299-304` 两边逐字断言 | **建议合并**（`sdr_tools` 复用 `constants`，或反之），需单独提交；**本次未动手** | 低。唯一风险：`desktop` 单独打包时 `constants` 已带回退值，`sdr_tools` 反向依赖 `desktop` 会造成内核包反向耦合——合并方向必须"内核→桌面"，不能"桌面→内核" |
| A4 | 频率格式化 5 份不同小数位 | 重复实现（不同字面量） | `desktop/status_panel.py:82`（≥1 MHz 6 位 / kHz 3 位）；`desktop/vfo_panel.py:43`（MHz 3 位 / kHz 1 位）；`desktop/rf_sky_view.py:1147`（1 位）；`mbdsdr_ai/device_capabilities.py:68 format_hz`（自适应 2/1/0 位）；`cpp/src/ui/status_format.h:19/26/36`。命名命中 18 处 / 5 文件 | **保留，禁止合并** | 合并会改显示精度：状态栏 6 位是给频偏读数的，VFO 3 位是给调谐盘位的；统一成任一种都会丢精度或产生无意义尾零 |
| A5 | 路径根/`sys.path` 引导重复 | 重复实现（同功能） | `_REPO_ROOT`/`REPO_ROOT=` 70 处 / 52 文件；`sys.path.insert(...parents/dirname)` 103 处 / 100 文件（生产约 13 个：`desktop/main.py`、`main_window.py`、`module_panel.py`、`mcp_worker.py` 等） | **保留**（脚本可独立执行，引导必须就地）；如需收敛，另开"启动引导"专项 | 强行抽公共函数会让脚本在"仓库外被拷走"时全部失效 |
| A6 | 错误码映射 | 重复实现（曾疑似） | `mbdsdr_ai/error_catalog.py:698 ERRORS` 为单一源；`desktop/error_remedy.py:38` 导入并派生 `REMEDY_BY_CODE`（`:42`）、`mapping_gaps()`（`:117`）、`overlaps()`（`:125`）；`docs/error-catalog.md` 为镜像，`tests/test_error_catalog_guard.py:225-226` 防漂移 | **保留**，判定**不是多真值源** | 无。已收敛且有守卫 |

### B. 多真值源：采样率档位逐份清单

> 采样率是本次最容易被误判成"冗余"的一组。**逐份列字面量与消费方**，不做合并。

| # | 表 / 位置 | 字面量 | 消费方（命中） | 判定 |
|---|---|---|---|---|
| B1 | C++ 可下发白名单 `cpp/src/core/tokens.h:287 kSampleRatesHz` | `1.024 / 2.048 / 2.4 / 3.2 MHz`（4 档） | `cpp/src/control/control_hub.cpp:503/507`（档外显式拒绝） | **保留 = 控制面白名单**。与 B2 是"白名单 ⊆ 候选"关系，**不许合并** |
| B2 | C++ 设备候选探测表 `cpp/src/dsp/device_capabilities.cpp:36-48 kCandidateRatesHz` | `250k / 1024k / 1536k / 1792k / 1920k / 2048k / 2160k / 2400k / 2560k / 2880k / 3200k`（11 档） | `:128 for (const double r : kCandidateRatesHz)` 按设备实际范围过滤 | **保留 = 候选探测表**。`:46` 已是 `2_880_000` 并注"was 2.8 MS/s, same physical quantity, -80 kHz" |
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

### 4.3 不能算作本次回归的失败

`tests/test_swallow_guard.py` 报 `1 failed, 50 passed`，原因是他线**未提交的新文件**
`tests/test_link_state_visibility.py` 使总量 `1292 > 基线 1284（新增 8）`；本次删除的
4 个函数均无 `try/except`，与失败无关。**不得为此上调基线**。

---

## 5. 待裁定 / 后续

| 项 | 需要谁定 | 建议 |
|---|---|---|
| B5 `desktop/module_panel.py` 5 档（含 1.44M） | 模块面板线 + 参数治理线 | 改为 `sample_rate_options()` 能力驱动；1.44M 若保留须在内核表登记理由。**本轮未改动** |
| A3 采样率格式化合并 | 桌面/内核界面线 | 合并方向"内核→桌面"，单独提交；合并后保留 `tests/test_copy_units_audit.py` 两侧断言 |
| C6 约 32 个仅测试模块 | 产品 + 各领域线 | 逐模块决定"对外 API / 移出核心包 / 归档"；**不得批量删** |
| C7 8 个未接线方法 | 各面板线 | 逐项确认是"待接线"还是"废弃接口" |
| C3 桌面写盘 vs `recorder`/`playback` | 录音/回放线（另一会话持线） | **不要在本表驱动下改动**；由该线决定统一或并存 |
| A1 频谱双实现 | 频谱线 | 维持双实现；若要统一，必须先跑 D.7 对拍并明确接受可见变化 |

---

## 6. 变更记录

| 日期 | 提交 | 变更 |
|---|---|---|
| 2026-11-06 | `c14ac51e` | 删除 4 个全仓零调用死函数（themes/shortcuts/mode_registry/device_capabilities），测试 91→91 |
| 2026-11-06 | （本表提交） | 建立冗余登记表 30 项；并入"采样率双用途、2.8M 硬件吸附、录音三模块非全死、桌面并行链路"四条事实，改判 4 项原计划动作 |
