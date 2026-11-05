# 参数统一账本（Parameter Unification Ledger）

> **本文件是「同一物理量多套数」的唯一事实源。**
> 各组改完只改本表「状态」列，并在文末「变更记录」追加一行；**不许在别处另写一份清单**。
> 参数取值/裁定背景仍记在 `docs/hardcoded-parameters.md`，本文件只维护"是否统一、谁来改、还剩什么风险"。

- 基线：`HEAD 5ece441`（2026-11-05 核对）
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
| 1 静噪 | `desktop/receive_pipeline.py:DemodWorker.run`（`_cfg.squelch_db`→`BaselineSquelch.set_threshold`→`margin_threshold_db()`）；`mbdsdr_ai/squelch.py:DEFAULT_SQUELCH_DBFS/SQUELCH_MIN_DBFS/SQUELCH_MAX_DBFS/margin_threshold_db`；`desktop/config_schema.py:squelch_default_db`；`desktop/control_panel.py:squelch_changed`；`desktop/main_window.py:_squelch_db/_replay_squelch_gate`；`mbdsdr_ai/sdr_backend.py:set_squelch`；`mbdsdr_ai/receive_chain.py:Squelch(threshold_db=...)`；`cpp/src/core/tokens.h:kSquelchMinDb/kSquelchMaxDb/kSquelchDefaultDb/kSquelchAutoMarginDb`；`cpp/src/dsp/spectrum_engine.cpp:setSquelchThreshold`；`mbdsdr_ai/analog_demod.py:_MODE_DEFAULTS/squelch_db` | 否（桌面生产路径已统一；`receive_pipeline` 已回落基线 17 ERROR/3 WARN/1 INFO；C++ 另一量纲；`analog_demod` 仍 IF 域绝对） | 桌面 `-50` ≈ **+6 dB 余量**（`-100→0`、`-50→6`、`-20→16`）；C++ `-50` = 48 kHz 音频块 RMS **绝对 dBFS**；`analog_demod` am/fm `-40`、wfm `-30`、usb/lsb/cw `-60`，初值 `-150` | 操作逻辑线（`desktop/main_window.py`、`desktop/receive_pipeline.py`）；后端线（`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/receive_chain.py`、`mbdsdr_ai/analog_demod.py`）；C++ 内核线（`cpp/src/core/tokens.h`、`cpp/src/dsp/spectrum_engine.cpp`） | 桌面从 `-50` 拧到 `-20` 是余量 6→16 dB（更少放行），C++ 同数值是绝对门抬高（几乎全开）——**同一滑杆数字两端语义相反**；滑杆值经 `sdr_backend.set_squelch` 直落 `Squelch.threshold_db` 时会被当绝对门，且 C++ `setSquelchThreshold` 会静默 `squelchAuto_.store(false)` 关掉自动门 | 同一段 88.1M OTA IQ，桌面 `-50` 与 `-20` 各跑一次比开门帧占比（应随余量升高而下降）；再对 `ReceiveChain` 同一段各跑一次，现在方向相反 | 未统一（处置见 `docs/limitations-register.md` 附 H） |
| 2 过境仰角 | `mbdsdr_ai/sat_passes.py:VISIBLE_MIN_ALT_DEG/PASS_MIN_ALT_DEG`；`desktop/sat_track_panel.py:_MIN_ELEVATION_DEG`；`desktop/new_spacetime_panel.py:refresh_passes`；`desktop/rf_sky_view.py:_refresh_passes`；`desktop/settings.py:effective_pass_min_elevation_deg/effective_pass_visibility_gate_deg/effective_show_low_elevation_passes`；`desktop/config_schema.py:pass_min_elevation_deg/show_low_elevation_passes` | 否（5/10 数值有意分开；设置项**零消费点**） | 可见/跟踪 **5°**；列表/预测 **10°**；设置默认 10°、范围 0–45 | 卫星线（`sat_passes.py`、`new_spacetime_panel.py`、`rf_sky_view.py`、`sat_track_panel.py`）；设置线（`settings.py`、`config_schema.py`） | 用户把 `pass_min_elevation_deg` 设成 5° 并应用，`refresh_passes()` 仍按 `PASS_MIN_ALT_DEG=10°` 跑，**列表一条不变**，"已保存"是假的；`show_low_elevation_passes` 打开也永远列不出 5–10° 过境；卫星在 7° 时跟踪面板说"可见"、时空列表却不列它 | 设 5° 前后各调一次 `refresh_passes`，比对返回集合（现在完全一致即证明是死设置） | 未统一 |
| 3 峰值 | `desktop/spectrum_widget.py:_PEAK_REL_DB_DEFAULT/_PEAK_ABS_FLOOR_DBFS/_PEAK_MIN_SEEN_FRAMES/_PEAK_MATCH_BINS/_PEAK_MAX_MISS_FRAMES/_PEAK_MAX_LABELS`；`desktop/config_schema.py:peak_threshold_db`；`cpp/src/core/tokens.h:kPeakRelDb/kPeakThresholdDefault/kPeakAbsFloorDbfs/kPeakMatchBins/kPeakMinSeenFrames/kPeakMaxMissFrames`；`cpp/src/ui/spectrum_display.cpp`（显示）；`cpp/src/ui/calibration_dialog.cpp`（校准） | 是（显示检测器已统一并在测试里锁死；校准门**有意分开**） | 显示 `rel 6.0 dB + abs -100 dBFS + 3 帧确认 + 5 bin 匹配 + 10 帧遗忘`；校准参考门 `15.0 dB` | 频谱线（`desktop/spectrum_widget.py`、`cpp/src/ui/spectrum_display.cpp`、`cpp/src/ui/calibration_dialog.cpp`） | 若合并成 15：显示侧漏掉 median+8 dB 的持续载波（数学上进不了候选）；若合并成 6：校准参考门掉到 6 dB，噪声尖峰被当校准参考，频偏估计被噪声拉走 | 跑 `tests/test_hardcoded_parameter_registry.py` 的显示门/校准门分锁断言；把两个常量各改 1 dB，确认只红对应一条 | 已统一（校准门有意分开） |
| 4 采样率 | `desktop/config_schema.py:sample_rate_hz`（2.048 M，exposed=False）；`desktop/settings.py:DEFAULTS`；`desktop/constants.py:DEFAULT_SAMPLE_RATE_HZ`（2.4 M）；`mbdsdr_ai/sdr_backend.py:DEFAULT_SAMPLE_RATE_HZ/_FALLBACK_SAMPLE_RATE_HZ/RTLSDRBackend.SAMPLE_RATES`；`desktop/control_panel.py:SAMPLE_RATES/sample_rate_combo`；`desktop/main_window.py:_connect_backend/采样率兜底项`；`cpp/src/core/tokens.h:kDefaultSampleRateHz/kSampleRatesHz` | 否 | 运行默认 **2.4 MS/s**（constants/backend/C++）；设置回填 **2.048 MS/s**；C++ 档位 `1.024/2.048/2.4/3.2`；Python 档位 11 档 `250k…3.2M` | 设置线（`config_schema.py`、`settings.py`、`control_panel.py`、`main_window.py`）；后端线（`sdr_backend.py`）；C++ 内核线（`tokens.h`） | 启动自动连走 2.4 M，但设置回填把顶部下拉设成 2.048 M → **界面显示 2.048、设备实际 2.4，差 352 kHz**；用户不动下拉就永远不知道实际值，一动才真切到 2.048（谱宽立刻缩 352 kHz）；import 兜底项文案写 "2.048 MS/s" 却绑 2.4 M 数据 | 新装启动后同时打印下拉 `currentData()`、状态栏实际采样率、`DEFAULT_SAMPLE_RATE_HZ`，三值应相等 | 未统一 |
| 5 步进 | `desktop/config_schema.py:tuning_step_hz`；`desktop/control_panel.py:step_combo/step_changed`；`desktop/main_window.py:_on_step_changed`；`desktop/settings_panel.py:_apply/_on_step_from_control/_apply_initial_state`；`desktop/spectrum_widget.py:_PlotState.snap_interval/set_snap_interval/wheelEvent/keyPressEvent/prepare_snap`；`cpp/src/core/tokens.h:kFreqFineStepHz`（死值）；`cpp/src/ui/main_window.cpp:stepCombo_/scanStepCombo_` | Python 四处**是**（同一 `snap_interval`）；跨语言**否** | 默认 **10 kHz**，六档 `10/100/1k/10k/100k/1M`；滚轮 1×、Shift ×10、Alt ×0.1；←→ 1×、↑↓ ×0.1；吸附网格 1×；引擎落点=吸附后绝对值 | 设置线（`config_schema.py`、`control_panel.py`、`settings_panel.py`）；频谱线（`spectrum_widget.py`）；C++ UI/内核线（`cpp/src/ui/main_window.cpp`、`tokens.h`） | 默认 10 kHz 下四处一致（差 0）；若下拉改了而吸附没跟上：下拉写 100 kHz、按一下只跳 **10 kHz**（应跳的 1/10）且落点吸回 10 kHz 网格；反向错位一次跳 **1 MHz（10×）**；C++ `kFreqFineStepHz=10000.0` 零消费点（真接进细调会错 10 倍）；C++ 步进 7 档含 1 Hz、Python 6 档最低 10 Hz（1 Hz 进来回默认 10 kHz，差 10,000 倍） | `tests/test_settings_schema.py::test_tuning_step_reaches_spectrum_and_combo` + `tests/test_wheel_step_gear.py` 四例；另需一条"下拉=滚轮步长=网格=落点"的端到端断言（目前缺） | 已统一（Python 四处）；跨语言未统一 |
| 6 FFT 点数 | `desktop/spectrum_widget.py:SpectrumDataGenerator.FFT_SIZES`；`desktop/config_schema.py:fft_size`；`cpp/src/ui/settings_dialog.cpp:fftCombo_`；`cpp/src/ai/tool_schema.cpp` + `cpp/src/ai/agent_tools.cpp`（set_fft 只收三档） | 否 | 共有 `1024/2048/4096`；`8192/16384` 仅 Python；默认 2048 两端一致 | 频谱线（`spectrum_widget.py`）；C++ UI/AI 线（`settings_dialog.cpp`、`tool_schema.cpp`） | Python 存 8192，C++ 读非法档退回 2048，分辨率骤降 4×（16384 降 8×）；AI 工具对 8192 报错拒绝，GUI 能设、AI 不能设 | 配 `fft_size=8192` 两端开设置页比对选中项与 `sr/fft` bin 宽度 | 未统一 |
| 7 dB 量程 | `desktop/spectrum_widget.py:_PlotState.db_min/db_max/_DB_AXIS_MIN/_DB_AXIS_MAX/apply_db_range`；`cpp/src/core/tokens.h:kDbLowerDefault/kDbUpperDefault`；`cpp/src/ui/spectrum_widget.cpp`/`settings_dialog.cpp` | 否 | Python 默认 **-100..-20**，轴夹取 **-160..0**，最小 span 10；C++ 默认 **-45..0** | 频谱线（`spectrum_widget.py`）；C++ UI 线（`tokens.h`、`settings_dialog.cpp`） | 同一段 IQ，`-50 dBFS` 信号在 Python 是轴中间、在 C++ 已低于 -45 下界被夹到底边 → 同一录音两端口径"正常谱线"vs"贴底看不出"，用户误判无信号；可拖动态范围差 115 dB | 同一 IQ 段两端各画一次，读回 `db_min/db_max`，看 -50 dBFS bin 是否在图内 | 未统一 |
| 8 瀑布深度 | `desktop/spectrum_widget.py:WF_DEPTH_MIN_ROWS/WF_DEPTH_DEFAULT_ROWS/WF_DEPTH_MAX_ROWS/set_waterfall_depth`；`desktop/config_schema.py:waterfall_depth_rows`；`cpp/src/core/tokens.h:kWaterfallDepthDefault/kWaterfallDepthChoices/kWaterfallHistoryLines`；`cpp/src/ui/spectrum_widget.cpp:setWaterfallDepth`；`cpp/src/ai/agent_tools.cpp` | 否（默认与范围已统一；合法集合未统一） | 默认 **256**；范围 **128–4096**；C++/AI 只认 `{128,256,512,1024,2048,4096}`；Python 任意 128–4096 | 频谱线（`spectrum_widget.py`）；设置线（`config_schema.py`）；C++ UI/AI 线（`spectrum_widget.cpp`、`agent_tools.cpp`） | 设 **384 行**：Python 真按 384 渲染，C++ 回落 256 → 回看时长差 1.5 倍；AI 工具对 384 报错拒绝；`kWaterfallMinH=48` 是控件最小高度（像素），不是深度 | 设 384 两端读回实际深度；AI 调 `rows=384` 应报错而 GUI 生效 | 未统一 |
| 9 无数据哨兵 | `desktop/spectrum_widget.py:_resample_max`（空段填 `-120.0`）；`cpp/src/dsp/spectrum_engine.h:rssiDbfs_{-100.0f}`；`cpp/src/core/tokens.h:kPeakAbsFloorDbfs`；`cpp/src/ui/main_window.cpp:scanLinkTick` 的 `-100.0f` 兜底 | 否 | Python **-120.0 dB**；C++ **-100.0 dBFS** | 频谱线（`spectrum_widget.py`）；C++ 内核线（`spectrum_engine.h`、`ui/main_window.cpp`） | 用户把轴下限拉到 -160 后，真空段与真实 -120 dBFS 完全重合，"没采到"与"弱信号"无法区分；C++ 扫描链把"尚未出第一块数据"与真实 -100 dBFS 同读作 -100，真信号不会被停驻 | 构造空段/全 NaN 帧读回填充值；C++ 分别跑首块数据前与注入 -100 dBFS 恒值两次 tick | 未统一 |
| 10 过境采样步长 | `mbdsdr_ai/sat_passes.py:_COARSE_STEP_S/_FINE_STEP_S`（60/2）；`desktop/rf_sky_view.py:PASS_COARSE_STEP_S/PASS_FINE_STEP_S`（300/30）与 `_refresh_passes`；`desktop/new_spacetime_panel.py:refresh_passes`（`step_sec=60.0`）+ `_sync_timer`；`desktop/rf_sky_view.py:_timer` | 否（两套独立过境算法） | `sat_passes` **60 s 粗扫 + 2 s 细采样**；`rf_sky_view` **300 s 粗扫 + 30 s 峰值邻域细化** | 卫星线（`sat_passes.py`、`rf_sky_view.py`、`new_spacetime_panel.py`） | 同一次 6 分钟 LEO 过境，时空面板 2 s 采样、天空面板 30 s → 峰值时刻最多偏 15 s，按 0.2–0.5°/s 计峰值仰角差 **3–7.5°**、AOS/LOS 差数十秒；两个面板给出不同"下一次过境"，多普勒预置按哪个都失配 | 同一 TLE/站点/时刻，比对 `predict_passes` 与 `rf_sky_view._refresh_passes` 的峰值仰角与 AOS/LOS | 未统一 |
| 11 状态提示时长 | `desktop/main_window.py` 的 `statusBar().showMessage(...)` 共 111 处 | 否 | 字面毫秒 88 处：`2 s`×1、`3 s`×15、`4 s`×25、`5 s`×16、`6 s`×13、`8 s`×14、`10 s`×3、`15 s`×1；16 处单参走 Qt `timeout=0`；7 处传变量 | 操作逻辑线（`desktop/main_window.py`） | 8 档时长：同一类"已保存"在 A 处 4 s、B 处 8 s；2 s 的错误提示抬头前就消失；15 s 那条压住随后 1–3 s 的新提示；16 处单参消息常驻状态栏，断连后仍可能挂"已连接" | 扫全部 `showMessage` 时长并断言只落在一个登记档位集合（如 2/4/8 s），未登记值或单参常驻一律红 | 未统一 |

## 附录 A：35 个设置暴露项的消费点自检（设置线，2026-11-05）

口径：只列 `config_schema.EXPOSED_ITEMS`（35 项）。“消费点”指**改值后真正读它的生产代码**
（文件:符号），不是面板的转发函数本身。结论：**35 项全部有生产消费点，没有“只写不读”的键。**

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
| 24 | `pass_min_elevation_deg` | 10.0 | 0–45 | `desktop/new_spacetime_panel.py:refresh_passes` → `effective_pass_min_elevation_deg` → `compute_passes(min_elevation=…)` | 是（下次刷新） |
| 25 | `pass_window_hours` | 24 | 1–72 | `desktop/new_spacetime_panel.py:refresh_passes` → `effective_pass_window_hours` → `compute_passes(hours_ahead=…)` | 是（下次刷新） |
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

> 基线变化（本线已提交）：第 2 行“过境仰角”不再零消费——`new_spacetime_panel.refresh_passes`
> 已读 `effective_pass_*`；第 4 行采样率已统一 2.4 MS/s（`4ef3a15`）；第 6 行 FFT 已收紧
> 1024/2048/4096（`605e89d`）；第 8 行瀑布深度已是六档枚举（非任意 128–4096）。
> 上表之外没有“有键无消费”的暴露项，因此本轮无需删键或补消费点。

## 变更记录

| 日期 | 基线 | 变更 | 操作人 |
|---|---|---|---|
| 2026-11-05 | `5ece441` | 建账：11 组逐条落表，状态按当时实测填写 | 参数治理线 |
| 2026-11-05 | `605e89d` | 附录 A：35 个暴露项消费点自检（全部有消费点） | 设置线 |
