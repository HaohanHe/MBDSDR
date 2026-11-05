# 参数统一账本（Parameter Unification Ledger）

> **本文件是「同一物理量多套数」的唯一事实源。**
> 各组改完只改本表「状态」列，并在文末「变更记录」追加一行；**不许在别处另写一份清单**。
> 参数取值/裁定背景仍记在 `docs/hardcoded-parameters.md`，本文件只维护"是否统一、谁来改、还剩什么风险"。

- 基线：`HEAD 16e9917`（2026-11-05 复核）；建账基线 `5ece441`
- 核对人：参数治理线
- 列口径：
  - **涉及位置**：`文件:符号`，不写行号（行号会随重构漂移）。
  - **是否已统一**：只看"同一物理量是否只有一个语义 + 一个来源"。
  - **统一到哪个值**：写具体数字/档位；跨量纲允许不同，但必须写明量纲。
  - **剩余风险**：面向用户的错误现象，一句话，不写"待统一"。
  - **验证方法**：一句话，能直接落成一条测试或一次手测。

## 状态枚举

| 状态 | 含义 |
|---|---|
| 未统一 | 两个及以上位置仍在用不同语义/不同值，且已确认会造成用户可见差异 |
| 已统一 | 只有一个语义与来源，且有测试或对拍证据锁住 |
| 已派单 | 已指定负责线与落点，等待改动入库 |
| 有意分开 | 数值不同但语义/职能不同，已登记理由，**不许合并** |

## 清零表（11 组）

| 组 | 涉及位置（文件:符号） | 是否已统一 | 统一到哪个值 | 负责线 | 剩余风险（面向用户的错误现象） | 验证方法 | 状态 |
|---|---|---|---|---|---|---|---|
| 1 静噪 | `desktop/receive_pipeline.py:DemodWorker.run`（`_cfg.squelch_db`→`BaselineSquelch.set_threshold`→`margin_threshold_db()`）；`mbdsdr_ai/squelch.py:DEFAULT_SQUELCH_DBFS/SQUELCH_MIN_DBFS/SQUELCH_MAX_DBFS/margin_threshold_db`；`desktop/config_schema.py:squelch_default_db`；`desktop/control_panel.py:squelch_changed`；`desktop/main_window.py:_squelch_db/_replay_squelch_gate`；`mbdsdr_ai/sdr_backend.py:set_squelch/SQUELCH_APPLIED_BY_VALUES`；`mbdsdr_ai/receive_chain.py:_make_squelch/set_squelch`（`BaselineSquelch`）；`cpp/src/core/tokens.h:kSquelchMinDb/kSquelchMaxDb/kSquelchDefaultDb/kSquelchAutoMarginDb`；`cpp/src/dsp/spectrum_engine.cpp:setSquelchThreshold`；`cpp/src/control/control_hub.cpp:cmdSetSquelch/cmdGetSquelchStatus`；`cpp/src/ui/main_window.cpp:squelchAutoBtn_/squelchSlider_`；`mbdsdr_ai/analog_demod.py:_MODE_DEFAULTS/squelch_db`（信道 IQ 域，有意分开） | 是（桌面/后端统一余量语义；四个拦点全落地：`0d6d465` + `e501555`） | 桌面/`BaselineSquelch`/`ReceiveChain`/`sdr_backend` 全走余量刻度（`-100→0`、`-50→6`、`-20→16` dB）；C++ 音频 RMS 域绝对 dBFS 门，靠 `domain` 守卫与余量值隔离；`analog_demod` 保持信道 IQ 域绝对（`am/fm -40`、`wfm -30`、`usb/lsb/cw -60`，初值 `-150`），不与桌面滑杆互通 | 操作逻辑线（`desktop/main_window.py`、`desktop/receive_pipeline.py`）；后端线（`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/receive_chain.py`、`mbdsdr_ai/analog_demod.py`）；C++ 内核线（`cpp/src/core/tokens.h`、`cpp/src/dsp/spectrum_engine.cpp`、`cpp/src/control/control_hub.cpp`、`cpp/src/ui/main_window.cpp`） | 已无“同一滑杆数字两端反语义”路径：`sdr_backend.set_squelch` 只记余量（`applied_by=desktop-margin`，不填绝对门）；`receive_chain` 解调前绝对 RMS 门已删，改 `BaselineSquelch`；`cmdSetSquelch` 对 `domain=margin/auto` 且带 `threshold_db` 直接报错不改门限；C++ 勾“自动门限”会显式 `setSquelchAuto(true)` 并禁用滑杆。残留仅 `analog_demod`（AI 无头 `demod_analog` 工具）仍是信道 IQ 域绝对门，界面不暴露 | 跑 `tests/test_squelch_domain.py`（`0d6d465` 新增）；再对同一段 88.1M OTA IQ，桌面 `-50`/`-20` 各一次，开门帧占比应随余量升高而下降；C++ `get_squelch_status` 应回 `none/cpp-auto/cpp-manual` | 已统一（余量语义，C++ 侧 domain 守卫） |
| 2 过境仰角 | `mbdsdr_ai/sat_passes.py:VISIBLE_MIN_ALT_DEG/PASS_MIN_ALT_DEG/PASS_COARSE_STEP_S/PASS_FINE_STEP_S`；`desktop/sat_track_panel.py:_MIN_ELEVATION_DEG`；`desktop/new_spacetime_panel.py:_pass_visibility_gate_deg/_pass_min_elevation_deg/_pass_show_low/refresh_passes`；`desktop/rf_sky_view.py:_refresh_passes`；`desktop/settings.py:effective_pass_min_elevation_deg/effective_pass_visibility_gate_deg/effective_show_low_elevation_passes/effective_pass_window_hours`；`desktop/config_schema.py:pass_min_elevation_deg/pass_window_hours/show_low_elevation_passes` | 部分（5/10 数值有意分开；`effective_pass_*` 四个入口均已接上，`pass_window_hours` 于 `886244d` 接线） | 可见/跟踪基准 **5°**；列表默认 **10°**，`show_low` 打开后门限降到 5° 并给低仰角标注；窗口默认 **24 h**、范围 1–72；仰角默认 10°、范围 0–45 | 卫星线（`sat_passes.py`、`new_spacetime_panel.py`、`rf_sky_view.py`、`sat_track_panel.py`）；设置线（`settings.py`、`config_schema.py`） | 已无写死窗口：两处均读 `effective_pass_window_hours`（实测 6 h=1 条、24 h=5 条、1 h=0 条）；仰角门两侧亦无分叉（`rf_sky_view` 与 `new_spacetime_panel` 同读 `effective_*`，固定夹具 5°=6 条 / 10°=5 条） | 设 `pass_window_hours=1/6/72` 各调一次 `refresh_passes`，条数应随窗口变化（`tests/test_pass_window_hours.py`） | 已统一（窗口+仰角门） |
| 3 峰值 | `desktop/spectrum_widget.py:_PEAK_REL_DB_DEFAULT/_PEAK_ABS_FLOOR_DBFS/_PEAK_MIN_SEEN_FRAMES/_PEAK_MATCH_BINS/_PEAK_MAX_MISS_FRAMES/_PEAK_MAX_LABELS`；`desktop/config_schema.py:peak_threshold_db`；`cpp/src/core/tokens.h:kPeakRelDb/kPeakThresholdDefault/kPeakAbsFloorDbfs/kPeakMatchBins/kPeakMinSeenFrames/kPeakMaxMissFrames`；`cpp/src/ui/spectrum_display.cpp`（显示）；`cpp/src/ui/calibration_dialog.cpp`（校准） | 是（显示检测器已统一并在测试里锁死；校准门**有意分开**） | 显示 `rel 6.0 dB + abs -100 dBFS + 3 帧确认 + 5 bin 匹配 + 10 帧遗忘`；校准参考门 `15.0 dB` | 频谱线（`desktop/spectrum_widget.py`、`cpp/src/ui/spectrum_display.cpp`、`cpp/src/ui/calibration_dialog.cpp`） | 若合并成 15：显示侧漏掉 median+8 dB 的持续载波（数学上进不了候选）；若合并成 6：校准参考门掉到 6 dB，噪声尖峰被当校准参考，频偏估计被噪声拉走 | 跑 `tests/test_hardcoded_parameter_registry.py` 的显示门/校准门分锁断言；把两个常量各改 1 dB，确认只红对应一条 | 已统一（校准门有意分开） |
| 4 采样率 | `desktop/config_schema.py:sample_rate_hz`（2.4 M，exposed=False，`4ef3a15`）；`desktop/settings.py:DEFAULTS`；`desktop/constants.py:DEFAULT_SAMPLE_RATE_HZ`（2.4 M）；`mbdsdr_ai/sdr_backend.py:DEFAULT_SAMPLE_RATE_HZ/_FALLBACK_SAMPLE_RATE_HZ/RTLSDRBackend.SAMPLE_RATES`；`desktop/control_panel.py:SAMPLE_RATES/sample_rate_combo`；`desktop/main_window.py:_connect_backend/采样率兜底项`；`cpp/src/core/tokens.h:kDefaultSampleRateHz/kSampleRatesHz` | 默认值**已统一**；档位集合**未统一**（11 vs 4）；存量配置迁移未清 | 运行/设置/C++ 默认均 **2.4 MS/s**；C++ 档位 `1.024/2.048/2.4/3.2`（4 档）；Python 档位 11 档 `250k…3.2M` | 设置线（`config_schema.py`、`settings.py`、`control_panel.py`、`main_window.py`）；后端线（`sdr_backend.py`）；C++ 内核线（`tokens.h`） | 旧 `desktop_settings.json` 里仍是 2.048 M：新默认 2.4 只对新建/无键配置生效，老用户启动后下拉显示 2.048、状态栏 2.4，**差 352 kHz** 直到手动切一次；`main_window` import 兜底项文案写 "2.048 MS/s" 却绑 `DEFAULT_SAMPLE_RATE_HZ=2.4 M`，显示与下发值不一致；C++ 4 档 vs Python 11 档，同一配置在两端可选项差 **7 档** | 新装与旧配置各启动一次，同时打印下拉 `currentData()`、状态栏实际采样率、`DEFAULT_SAMPLE_RATE_HZ`，三值应相等 | 已统一（默认值）；档位集合未统一 |
| 5 步进 | `desktop/config_schema.py:tuning_step_hz`；`desktop/control_panel.py:step_combo/step_changed`；`desktop/main_window.py:_on_step_changed`；`desktop/settings_panel.py:_apply/_on_step_from_control/_apply_initial_state`；`desktop/spectrum_widget.py:_PlotState.snap_interval/set_snap_interval/wheelEvent/keyPressEvent/prepare_snap`；`cpp/src/core/tokens.h:kFreqCoarseStepHz`（死值，原 `kFreqFineStepHz`）；`cpp/src/ui/main_window.cpp:stepCombo_/scanStepCombo_` | Python 四处**是**（同一 `snap_interval`）；跨语言**否** | 默认 **10 kHz**，六档 `10/100/1k/10k/100k/1M`；滚轮 1×、Shift ×10、Alt ×0.1；←→ 1×、↑↓ ×0.1；吸附网格 1×；引擎落点=吸附后绝对值 | 设置线（`config_schema.py`、`control_panel.py`、`settings_panel.py`）；频谱线（`spectrum_widget.py`）；C++ UI/内核线（`cpp/src/ui/main_window.cpp`、`tokens.h`） | 默认 10 kHz 下四处一致（差 0）；若下拉改了而吸附没跟上：下拉写 100 kHz、按一下只跳 **10 kHz**（应跳的 1/10）且落点吸回 10 kHz 网格；反向错位一次跳 **1 MHz（10×）**；C++ `kFreqCoarseStepHz=10000.0` 仍**零消费点**，而 Python ↑↓ 实际是 0.1×snap（默认 1 kHz）——真接进细调会错 **10 倍**；C++ 步进 7 档含 1 Hz、Python 6 档最低 10 Hz（1 Hz 配置进来回默认 10 kHz，差 10,000 倍） | `tests/test_settings_schema.py::test_tuning_step_reaches_spectrum_and_combo` + `tests/test_wheel_step_gear.py` 四例；另需一条"下拉=滚轮步长=网格=落点"的端到端断言（目前缺） | 已统一（Python 四处）；跨语言未统一 |
| 6 FFT 点数 | `desktop/spectrum_widget.py:SpectrumDataGenerator.FFT_SIZES`（5 档 1024…16384）、`SpectrumPanel._fft_combo`（列全部 FFT_SIZES）；`desktop/config_schema.py:fft_size`（3 档）；`desktop/settings_panel.py:_apply_spectrum`（按设置推 `set_fft_size`）；`cpp/src/ui/settings_dialog.cpp:fftCombo_`（3 档）；`cpp/src/ai/tool_schema.cpp:set_fft_params`（enum 1024/2048/4096） | 否（设置页已收 3 档（`605e89d`）；频谱工具条仍 5 档；C++/AI 3 档） | 设置/C++/AI：`1024/2048/4096`；工具条 `FFT_SIZES` 仍含 `8192/16384`；默认 2048 三处一致 | 频谱线（`spectrum_widget.py`）；设置线（`config_schema.py`、`settings_panel.py`）；C++ UI/AI 线（`settings_dialog.cpp`、`tool_schema.cpp`） | 设置页只能存 3 档，但频谱工具条仍能选 8192/16384：@2.4 MS/s 的 bin 宽从 585.9 Hz 变 146.5 Hz（4096→16384，差 4 倍）；C++ 读到非法档退回 2048，分辨率再降 8 倍；AI 工具对 8192 明确报错拒绝，GUI 能设、AI 不能设 | 设置页选 4096 后到工具条再选 8192，两端读回 `sr/fft` bin 宽（应 585.9 vs 146.5 Hz） | 未统一 |
| 7 dB 量程 | `desktop/spectrum_widget.py:_PlotState.db_min/db_max/_DB_AXIS_MIN/_DB_AXIS_MAX/apply_db_range`；`cpp/src/core/tokens.h:kDbLowerDefault/kDbUpperDefault`；`cpp/src/ui/spectrum_widget.cpp`/`settings_dialog.cpp` | 否 | Python 默认 **-100..-20**，轴夹取 **-160..0**，最小 span 10；C++ 默认 **-45..0** | 频谱线（`spectrum_widget.py`）；C++ UI 线（`tokens.h`、`settings_dialog.cpp`） | 同一段 IQ，`-50 dBFS` 信号在 Python 是轴中间、在 C++ 已低于 -45 下界被夹到底边 → 同一录音两端口径"正常谱线"vs"贴底看不出"，用户误判无信号；可拖动态范围差 115 dB | 同一 IQ 段两端各画一次，读回 `db_min/db_max`，看 -50 dBFS bin 是否在图内 | 未统一 |
| 8 瀑布深度 | `desktop/spectrum_widget.py:WF_DEPTH_CHOICES/WF_DEPTH_DEFAULT_ROWS/set_waterfall_depth/set_waterfall_depth_rows`；`desktop/config_schema.py:waterfall_depth_rows`；`cpp/src/core/tokens.h:kWaterfallDepthDefault/kWaterfallDepthChoices`；`cpp/src/ui/spectrum_widget.cpp:setWaterfallDepth`；`cpp/src/ai/agent_tools.cpp` | 是（两侧同一六档枚举；档外值回 256 并 warning，不夹取） | 默认 **256**；合法集合 `{128,256,512,1024,2048,4096}`；档外值（如 384）回落 256 | 频谱线（`spectrum_widget.py`）；设置线（`config_schema.py`）；C++ UI/AI 线（`spectrum_widget.cpp`、`agent_tools.cpp`） | 旧配置/旧脚本写 **384** 时被回落到 256：回看行数少 **128 行（1.5 倍）**，用户以为设成 384 实际只有 256；日志有 warning，但界面不主动提示 | 设 384 读回实际深度应为 256 且有一条 warning；AI 调 `rows=384` 与 GUI 同口径 | 已统一 |
| 9 无数据哨兵 | `desktop/spectrum_widget.py:_resample_max`（空段填 `np.nan`）；`cpp/src/dsp/spectrum_engine.h:rssiDbfs_`（`quiet_NaN`）；`cpp/src/core/tokens.h:kPeakAbsFloorDbfs`（-100.0f，峰值绝对门，不是哨兵）；`desktop/status_panel.py:_LEVEL_MIN` 与 `mbdsdr_ai/fm_scan.py`、`mbdsdr_ai/new_spacetime_amr.py:_NO_SIGNAL_POWER_DB`（-120.0 无信号电平） | 频谱哨兵**已统一**（`8d940b7`，两侧均 NaN）；"无信号电平"仍 4 处 -120.0 | 频谱哨兵 Python/C++ 均为 **NaN**；无信号电平 **-120.0 dBFS**；峰值绝对门 **-100.0 dBFS** 是另一个量 | 频谱线（`spectrum_widget.py`）；C++ 内核线（`spectrum_engine.h`）；状态栏/扫描/AMR 线（`status_panel.py`、`fm_scan.py`、`new_spacetime_amr.py`） | 频谱侧已不会把"没采到"画成 -120 dBFS（历史差 **20.0 dB** 已清零）；但状态栏/扫描/AMR 仍用 -120.0 表示"无信号"，与 -100.0 绝对门差 **20.0 dB**——用户看到 -120 时分不清"没信号"与"低于峰值门" | 构造空段读回 `NaN`；再读 `status_panel._LEVEL_MIN` 与 `kPeakAbsFloorDbfs`，两点仍差 20.0 dB | 已统一（频谱哨兵）；电平常量未统一 |
| 10 过境采样步长 | `mbdsdr_ai/sat_passes.py:PASS_COARSE_STEP_S/PASS_FINE_STEP_S/_COARSE_STEP_S/_FINE_STEP_S`；`desktop/rf_sky_view.py:PASS_COARSE_STEP_S/PASS_FINE_STEP_S`（引用 `sat_passes`）/`_refresh_passes`；`desktop/new_spacetime_panel.py:refresh_passes`（`step_sec=PASS_COARSE_STEP_S`） | 是（`4faddb2` 两端收敛到同一常量；历史 300/30 已删） | **60 s 粗扫 + 2 s 细采样**（两侧同一常量） | 卫星线（`sat_passes.py`、`rf_sky_view.py`、`new_spacetime_panel.py`） | 已无 300/30 路径（历史差：粗扫 **240 s（5×）**、细采样 **28 s（15×）**）；窗口亦已两侧读设置（`886244d`，`PASS_WIN_HOURS` 已删） | 同一 TLE/站点/时刻比对两面板峰值仰角与 AOS/LOS：实测旧 300/30 峰值差 **13.1 s / 0.40°**、新 60/2 **0.9 s / 0.004°**（`tests/test_pass_step_alignment.py`，已入库 `4faddb2`） | 已统一 |
| 11 状态提示时长 | `desktop/main_window.py` 的 `statusBar().showMessage(...)` 共 111 处 | 否 | 字面毫秒 88 处：`2 s`×1、`3 s`×15、`4 s`×25、`5 s`×16、`6 s`×13、`8 s`×14、`10 s`×3、`15 s`×1；16 处单参走 Qt `timeout=0`；7 处传变量 | 操作逻辑线（`desktop/main_window.py`） | 8 档时长：同一类"已保存"在 A 处 4 s、B 处 8 s；2 s 的错误提示抬头前就消失；15 s 那条压住随后 1–3 s 的新提示；16 处单参消息常驻状态栏，断连后仍可能挂"已连接" | 扫全部 `showMessage` 时长并断言只落在一个登记档位集合（如 2/4/8 s），未登记值或单参常驻一律红 | 未统一 |

## 附录 A：35 个设置暴露项的消费点自检（设置线，2026-11-05）

口径：只列 `config_schema.EXPOSED_ITEMS`（35 项）。“消费点”指**改值后真正读它的生产代码**
（文件:符号），不是面板的转发函数本身。设置线初检结论“35 项全部有生产消费点”经代码侧逐项复核后
**修正为 34 项有、1 项待接线**（红表见附录 A-1）；该 1 项已于 `886244d` 接线，现为 **35/35**。

| # | 键 | 默认 | 范围/枚举 | 消费点（文件:符号） | 即时生效 |
|---|---|---|---|---|---|
| 1 | `ui_language` | zh | zh/en/ja | `desktop/main.py:117` → `l10n.install_translator(Qt)` | 否，重启（行内已标注） |
| 2 | `touch_mode` | auto | auto/on/off | `desktop/main_window.py:_set_touch_mode` → `touch_manager` | 是 |
| 3 | `theme` | default | 5 枚举 | `desktop/main_window.py:_apply_theme` → `themes` | 是 |
| 4 | `show_waterfall` | True | bool | `desktop/spectrum_widget.py:_PlotState.show_waterfall` → `toggle_waterfall/_draw_waterfall` | 是 |
| 5 | `sky_pressure_hpa` | 1013.25 | 800–1100 | `desktop/rf_sky_view.py:set_refraction_params` → 折射修正 | 是 |
| 6 | `sky_temperature_c` | 10.0 | -50–50 | 同上（`set_refraction_params(temperature_c=…)`） | 是 |
| 7 | `bortle` | 3 | 1–9 | `desktop/rf_sky_view.py:set_bortle` → `star_catalog.select_for_view` 极限星等 | 是 |
| 8 | `fft_size` | 2048 | 1024/2048/4096 | `desktop/spectrum_widget.py:SpectrumDataGenerator.set_fft_size` → FFT | 是 |
| 9 | `fft_window` | Nuttall | 6 枚举 | `desktop/spectrum_widget.py:SpectrumDataGenerator.set_window` | 是 |
| 10 | `fft_avg_frames` | 1 | 1/4/8/16 | `desktop/spectrum_widget.py:SpectrumDataGenerator.set_avg_frames` | 是 |
| 11 | `waterfall_palette` | default | default/sdrpp | `desktop/spectrum_widget.py:SpectrumPanel._on_palette_changed` → `_PlotState.wf_colors` | 是 |
| 12 | `peak_threshold_db` | 6.0 | 1–30 | `desktop/spectrum_widget.py:_PlotState.peak_rel_db` → `find_peaks_with_snr` | 是 |
| 13 | `show_peaks` | True | bool | `desktop/spectrum_widget.py:_PlotState.show_peaks` → 峰值绘制 | 是 |
| 14 | `waterfall_smooth` | False | bool | `desktop/spectrum_widget.py:_on_wf_smooth_toggled` → `generator.wf_smooth` | 是 |
| 15 | `show_bookmarks` | True | bool | `desktop/spectrum_widget.py:_PlotState.show_bookmarks` → `_draw_bookmarks` | 是 |
| 16 | `show_bandplan` | True | bool | `desktop/spectrum_widget.py:_PlotState.show_bandplan` → 频段彩带 | 是 |
| 17 | `squelch_default_db` | -50 | -100–-20 | `control_panel` 滑杆 → `main_window._on_squelch_changed` → `mbdsdr_ai/squelch.py:margin_threshold_db` | 是 |
| 18 | `tuning_step_hz` | 10000 | 6 枚举 | `desktop/main_window.py:_on_step_changed` → `spectrum_widget.py:set_snap_interval` | 是 |
| 19 | `waterfall_depth_rows` | 256 | 128/256/512/1024/2048/4096 | `desktop/spectrum_widget.py:SpectrumDataGenerator.set_waterfall_depth` → `_wf_buf` | 是 |
| 20 | `sky_extinction_k` | 0.2 | 0.0–0.5 | `desktop/rf_sky_view.py:set_extinction`（别名 `set_extinction_k`）→ `select_for_view(extinction_k=…)` + 逐星 `extinction_delta_mag` | 是 |
| 21 | `record_dir` | 空 | 文本路径 | `desktop/settings.py:ensure_record_dir` → `main_window.py:6062` 录音启动 | 是（只影响新录音） |
| 22 | `observer_lat` | None | -90–90 | `desktop/main_window.py:_apply_observer_location` → `SatelliteTracker(..., lat, lon)` / 天空图 | 是 |
| 23 | `observer_lon` | None | -180–180 | 同上 | 是 |
| 24 | `pass_min_elevation_deg` | 10.0 | 0–45 | `desktop/new_spacetime_panel.py:refresh_passes` → `effective_pass_min_elevation_deg`；列表筛选门实际走 `effective_pass_visibility_gate_deg` → `compute_passes(min_elevation=…)`，本值只在 `_populate_passes` 标注低仰角 | 是（下次刷新） |
| 25 | `pass_window_hours` | 24 | 1–72 | `desktop/new_spacetime_panel.py:refresh_passes` → `hours_ahead=float(self._pass_window_hours)`；`desktop/rf_sky_view.py:_refresh_passes` → `win_s=float(window_hours)*3600.0`；两处均读 `settings.effective_pass_window_hours`（`886244d`，`PASS_WIN_HOURS` 已删） | 是（下次刷新） |
| 26 | `show_low_elevation_passes` | False | bool | `desktop/new_spacetime_panel.py:refresh_passes` → `effective_pass_visibility_gate_deg` / 低仰角标注 | 是（下次刷新） |
| 27 | `ai_temperature` | 0.7 | 0.0–2.0 | `desktop/settings_panel.py:_store_value` 写 `config.json` 并推 `ai_panel.agent.config`；读侧 `mbdsdr_ai/config.py:AgentConfig` | 是（已建 agent 时） |
| 28 | `ai_top_p` | 0.9 | 0.0–1.0 | 同上 | 是 |
| 29 | `ai_max_output_tokens` | 2048 | 128–16384 | 同上 | 是 |
| 30 | `ai_max_context_tokens` | 8192 | 1024–131072 | 同上 | 是 |
| 31 | `ai_timeout` | 60 | 5–300 | 同上 | 是 |
| 32 | `rc_enabled` | False | bool | `desktop/settings_panel.py:_start_rc/_stop_rc` → `mbdsdr_ai/remote_control.py:RemoteControl.start` | 是 |
| 33 | `rc_port` | 7356 | 1–65535 | `desktop/settings_panel.py:_start_rc` → `RemoteControl(port=…)` | 是（重启该服务后） |
| 34 | `web_enabled` | False | bool | `desktop/settings_panel.py:_start_web/_stop_web` → `mbdsdr_ai/web/server.py:WebServer.start` | 是 |
| 35 | `web_port` | 8080 | 1–65535 | `desktop/settings_panel.py:_start_web` → `WebServer(port=…)` | 是（重启该服务后） |

> 基线变化（已入库）：第 2 行“过境仰角”不再零消费——`new_spacetime_panel.refresh_passes`
> 已读 `effective_pass_*`（实测 5° vs 10° 列表 7 条 vs 6 条）；第 4 行采样率默认值已统一 2.4 MS/s
> （`6ed9ee7`/`4ef3a15`）；第 6 行设置页 FFT 已收 1024/2048/4096（`605e89d`），但频谱工具条仍 5 档；
> 第 8 行瀑布深度已是六档枚举（非任意 128–4096）。**上表 35 项均已接线**（第 25 行于 `886244d` 补齐）。

### 附录 A-1 红表：有键无生产消费点（0 项；`pass_window_hours` 已于 `886244d` 接线，保留记录）

| # | 键 | 证据（文件:符号） | 为什么没接上 | 处置（已定：接线，不删键） | 派单 |
|---|---|---|---|---|---|
| 25 | `pass_window_hours` | 历史：两处写死 24.0；现已两处读 `effective_pass_window_hours` | 读取入口先落地、消费点没接 | 已补消费点（`886244d`）：6 h 与 24 h 实测 **1 条 vs 5 条**；**未从 `config_schema` 删键** | 卫星过境线（已完成） |

### 附录 A-2 代码侧复核（参数治理线，2026-11-05）

复核方法：逐键反查“改值后真正读它”的生产符号；`settings_panel._apply` 只算转发，不算消费点。
结论：**34/35 有生产消费点**；共改 3 行（#24、#25、表格结论）。

| 行 | 原写法 | 改为 | 依据 |
|---|---|---|---|
| 24 | `effective_pass_min_elevation_deg → compute_passes(min_elevation=…)` | 筛选门实为 `effective_pass_visibility_gate_deg`；`effective_pass_min_elevation_deg` 只在 `_populate_passes` 标注低仰角 | `desktop/new_spacetime_panel.py:refresh_passes/_populate_passes` |
| 25 | “有消费点，即时生效（下次刷新）” | **无生产消费点（待接线，已派卫星线）** | 同上 + `desktop/rf_sky_view.py:_refresh_passes`（`PASS_WIN_HOURS=24.0`）；全仓 `effective_pass_window_hours` 仅定义/测试 |
| 结论 | “35 项全部有生产消费点” | 复核时为 34 项有、1 项待接线；`886244d` 接线后 **35/35** | 同上 |

其余 32 行抽样复核通过：`ui_language`→`main.py:install_translator`；`touch_mode`→`main_window._set_touch_mode`；
`theme`→`_apply_theme`；`show_waterfall`→`SpectrumPanel.toggle_waterfall`；`sky_pressure_hpa`/`sky_temperature_c`→
`rf_sky_view.set_refraction_params`；`bortle`/`sky_extinction_k`→`set_bortle`/`set_extinction`；`fft_*`→
`settings_panel._apply_spectrum`→`SpectrumDataGenerator.set_fft_size/set_window/set_avg_frames`；`waterfall_palette`→
`SpectrumPanel._on_palette_changed`；`peak_threshold_db`/`show_peaks`/`show_bookmarks`/`show_bandplan`/`waterfall_smooth`→
`_PlotState` 字段与 `_on_wf_smooth_toggled`；`squelch_default_db`→`main_window._on_squelch_changed`；`tuning_step_hz`→
`_on_step_changed`+`set_snap_interval`；`waterfall_depth_rows`→`set_waterfall_depth_rows`；`record_dir`→
`settings.ensure_record_dir`；`observer_lat/lon`→`_apply_observer_location`；`ai_*`→`_store_value` 推 `agent.config`；
`rc_*`/`web_*`→`_start_rc`/`_start_web`。

## 附录 B：本轮 8 组跨端差值实测（参数治理线，2026-11-05）

口径：差值为算术差，倍数按较大/较小；全部由常量或状态机直接读数/计算，不含估算。

| # | 物理量 | A 位置与值 | B 位置与值 | 实测差值 | 后果 | 现状态 |
|---|---|---|---|---|---|---|
| B1 | 采样率 | `desktop/main_window.py` 采样率兜底项文案 `"2.048 MS/s"`（`addItem` 绑 `DEFAULT_SAMPLE_RATE_HZ`） | `desktop/constants.py:DEFAULT_SAMPLE_RATE_HZ` 与 `mbdsdr_ai/sdr_backend.py:DEFAULT_SAMPLE_RATE_HZ` = **2,400,000 Hz**；C++ `tokens.h:kSampleRatesHz` 含 2.048e6/2.4e6 | **352,000 Hz（17.19%，1.172×）**；档位 11 vs 4，差 **7 档** | 该项显示 2.048 实际下发 2.4；旧配置 2.048 与状态栏 2.4 并存直到手动切一次 | 默认值已统一（`6ed9ee7`）；文案与档位集合未统一 |
| B2 | 静噪 | `mbdsdr_ai/squelch.py:margin_threshold_db`（-50→**6.0 dB 余量**；-20→16.0） | `cpp/src/dsp/spectrum_engine.cpp:setSquelchThreshold`（**绝对 dBFS**；-50→-20） | 同一滑杆 -50→-20：桌面余量 **+10.0 dB（更严）**，C++ 绝对门 **+30.0 dB（更松）** → 响应差 **40.0 dB 且方向相反** | 历史语义相反；现由 `domain` 守卫隔离，余量值不再直落绝对门 | 已统一（`0d6d465` + `e501555`，四个拦点全落地；上述 40 dB 为历史差值） |
| B3 | 峰值门 | `desktop/spectrum_widget.py:_PEAK_REL_DB_DEFAULT` = **6.0 dB**（C++ 显示 `tokens::kPeakRelDb` 同值） | `cpp/src/ui/calibration_dialog.cpp` 用 `tokens::kPeakThresholdDefault` = **15.0 dB** | **9.0 dB（2.5×）** | 合并任一侧都会坏：6→15 漏持续弱载波；15→6 噪声尖峰当校准参考 | 有意分开（`b8bc514`/`5ece441`） |
| B4 | 过境采样 | `mbdsdr_ai/sat_passes.py:PASS_COARSE_STEP_S` = **60.0**、`PASS_FINE_STEP_S` = **2.0** | `desktop/rf_sky_view.py` HEAD 已引用同常量；历史为 300/30（`4faddb2` 前） | 历史：粗扫 **240 s（5×）**、细采样 **28 s（15×）**；现在 **0/0** | 历史两面板峰值时刻最多偏 15 s；实测（ISS 39.9°N/116.4°E，24h）：旧 300/30 峰值差 **13.1 s / 0.40°**，新 60/2 **0.9 s / 0.004°**；新时空面板 AOS/LOS 对同源参考差 **+29.8 s / +39.6 s**（≤1 粗扫步长） | 已统一（`4faddb2`） |
| B5 | 频率步进死值 | `cpp/src/core/tokens.h:kFreqCoarseStepHz` = **10000.0**（全仓零消费点） | `desktop/spectrum_widget.py:_PlotState.keyPressEvent` ↑↓ = `snap_interval*0.1`，默认 **1,000 Hz** | **9,000 Hz（10×）**；←→ 整格 10,000 → 差 **0** | token 若接进细调会错 10 倍；C++ 7 档含 1 Hz vs Python 6 档最低 10 Hz（差 10,000×） | 跨语言未统一 |
| B6 | FFT 档位 | `desktop/spectrum_widget.py:SpectrumDataGenerator.FFT_SIZES`（1024/2048/4096/8192/16384，**5 档**） | `cpp/src/ui/settings_dialog.cpp:fftCombo_` 与 `cpp/src/ai/tool_schema.cpp:set_fft_params`（1024/2048/4096，**3 档**） | 档位差 **2 档**；@2.4 MS/s bin 宽 **146.48 vs 585.94 Hz**，差 **439.45 Hz（4×）** | 工具条能选 8192/16384，C++ 读非法档退 2048（再降 8×）；AI 明确拒绝 | 未统一。唯一事实源 = `config_schema.fft_size` 的 **3 档**（C++/AI 已同）；**频谱线改**：`FFT_SIZES` 删掉 8192/16384 并同步 `_fft_combo`，不补 2 档 |
| B7 | 瀑布行数 | `desktop/spectrum_widget.py:WF_DEPTH_CHOICES`（6 档） | `cpp/src/core/tokens.h:kWaterfallDepthChoices`（同 6 档）与 `agent_tools.cpp` | 现在 **0**；历史 384 vs 256 = **128 行（1.5×）** | 档外值 384 回落 256，回看时长少 1.5 倍 | 已统一 |
| B8 | 无数据哨兵 | `desktop/spectrum_widget.py:_resample_max` = **`np.nan`**（`8d940b7` 前为 -120.0） | `cpp/src/dsp/spectrum_engine.h:rssiDbfs_` = **`quiet_NaN`**（同 commit 前为 -100.0f） | 现在 **0 dB**（两侧同为 NaN）；历史 **20.0 dB** | 频谱侧已能区分“没采到”与“弱信号”；但状态栏/扫描/AMR 仍用 -120.0 当“无信号”，与 -100.0 绝对门差 20.0 dB | 已统一（哨兵）；`-120.0` 电平常量未统一 |

## 变更记录

| 日期 | 基线 | 变更 | 操作人 |
|---|---|---|---|
| 2026-11-05 | `5ece441` | 建账：11 组逐条落表，状态按当时实测填写 | 参数治理线 |
| 2026-11-05 | `605e89d` | 附录 A：35 个暴露项消费点自检（全部有消费点） | 设置线 |
| 2026-11-05 | `3c4b7ad` | 复核附录 A（34/35 有消费点，红表 1 项 `pass_window_hours`；改 #24/#25）；更新组 2/4/6/8/9/10 状态；新增附录 B（8 组跨端差值实测） | 参数治理线 |
| 2026-11-05 | `16e9917` | `pass_window_hours` 定为接线不删键（#25 改“待接线”，派卫星线补两处消费点）；复验静噪四拦点全落地（`0d6d465`+`e501555`），组 1 改“已统一”；B6 写明唯一事实源与改哪一侧 | 参数治理线 |
| 2026-11-05 | `886244d` | #25 接线完成：两处过境列表读 `effective_pass_window_hours`（删 `PASS_WIN_HOURS`）；实测 6 h vs 24 h = **1 条 vs 5 条**，1 h = 0 条；A-1 红表清零（35/35 有消费点）；B4 补步长实测数字；另 `4faddb2` 修正 `_gmst_rad` 漏项（AOS 由偏 **16110 s** 回到 **+29.8 s**） | 卫星过境线 |
