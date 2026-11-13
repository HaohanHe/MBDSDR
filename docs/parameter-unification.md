# 参数统一账本（Parameter Unification Ledger）

> 本篇结论未经 commit 取证，引用前须复核（判据见 `scripts/verify_claims.py`；补齐四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 后可摘除本行）。

> **本文件是「同一物理量多套数」的唯一事实源。**
> 各组改完只改本表「状态」列，并在文末「变更记录」追加一行；**不许在别处另写一份清单**。
> 参数取值/裁定背景仍记在 `docs/hardcoded-parameters.md`，本文件只维护"是否统一、谁来改、还剩什么风险"。

- 基线：`HEAD 16e9917`（2026-11-05 复核）；建账基线 `5ece441`
- 核对人：参数治理线
- 列口径：
  - **涉及位置**：`文件:符号`，不写行号（行号会随重构漂移）。
  - **是否统一**：只看"同一物理量是否只有一个语义 + 一个来源"。
  - **统一到哪个值**：写具体数字/档位；跨量纲允许不同，但必须写明量纲。
  - **剩余风险**：面向用户的错误现象，一句话，不写"待统一"。
  - **验证方法**：一句话，能直接落成一条测试或一次手测。

## 引用口径：`verify_claims` 两组数不可混引

> `scripts/verify_claims.py` 读的是**磁盘上的 `docs/*.md`**，不是 git 对象；因此同一时刻的同一个 `HEAD` 会出两组数：

| 口径 | 怎么取 | 用途 |
|---|---|---|
| **HEAD 提交态** | `git show HEAD:docs/<file>` 写入临时目录，再让校验器 `--docs` 指向该快照 | 可提交、可引用的唯一口径 |
| **工作区在途** | 直接跑 `docs/` | 只用于自查未提交 hunk，不得对外引用 |

- 引用规则：任何引用必须写成 `HEAD <短哈希>` + 四元组分布（总数 / 可证实 / 过期 / 无 hash），**不许裸报一个总数**；两种口径**不可混引、不可相加、不可拿工作区数替代 HEAD 数**。数字随 HEAD 漂移，引用前按当次 HEAD 重跑。
- 同一 HEAD 两组数为什么不同：工作区里未提交的 hunk 会给断言行补上四元组，这些行从「无 hash」迁到「可证实」；同时未提交的删改会让断言行总数变化。实测对应关系：

| HEAD | 提交态（总数 / 可证实 / 过期 / 无 hash） | 工作区在途 | 闭合关系 |
|---|---|---|---|
| `b0a440e3` | 164 / 58 / 17 / 89 | 128 / 70 / 13 / 45 | 不是两个 HEAD，是同一 HEAD 的两种读法：58→70（+12）为未提交 hunk 补的 hash；89→45（−44）= 12 行迁入可证实 + 32 行被删/改写（只做总量闭合，未留逐行快照）；17→13（−4）同为删改；总数 −36 闭合 |
| `d731bdeb` | 140 / 58 / 12 / 70 | 128 / 70 / 12 / 46 | 仍 +12 可证实（同一批未提交 hunk）；无 hash −24 = 12 行迁出 + 12 行被删（PRD 12→2、evidence-standard 1→0、settings-inventory 15→14、reference-mechanisms 15→14、COORDINATION 20→21） |

- 判定规则本身不受口径影响：`CLAIM_RE`、三步校验、`EXCLUDE_FILES` 与 `docs/claim-waivers.md` 白名单均未改动；这里只区分「读哪份文档」。
- 证据留存：工作区 `scripts/verify_claims.py` 有一笔未提交的 `_hashes` 放宽（+21/−5，纯数字 hash 经 `cat-file` 试解析后也收）。该改动存续期间实测：**同一 HEAD 快照上，HEAD 提交版（严格）与工作区版（放宽）逐条比对 0 差异**（`b0a440e3`：164/58/17/89 两版相同；`d731bdeb`：140/58/12/70 两版相同）。该改动由机制线裁决（带回归测试提交或撤回），本线不碰。

## 状态枚举

| 状态 | 含义 |
|---|---|
| 未统一 | 两个及以上位置仍在用不同语义/不同值，且已确认会造成用户可见差异 |
| 统一（收敛态） | 只有一个语义与来源，且有测试或对拍证据锁住 |
| 已派单 | 已指定负责线与落点，等待改动入库 |
| 有意分开 | 数值不同但语义/职能不同，已登记理由，**不许合并** |

## 清零表（12 组）

> 证据等级 E4：本段数字未验证，引用前须复核（判据见 docs/evidence-standard.md）。

| 组 | 涉及位置（文件:符号） | 是否统一 | 统一到哪个值 | 负责线 | 剩余风险（面向用户的错误现象） | 验证方法 | 状态 |
|---|---|---|---|---|---|---|---|
| 1 静噪 | `desktop/receive_pipeline.py:DemodWorker.run`（`_cfg.squelch_db`→`BaselineSquelch.set_threshold`→`margin_threshold_db()`）；`mbdsdr_ai/squelch.py:DEFAULT_SQUELCH_DBFS/SQUELCH_MIN_DBFS/SQUELCH_MAX_DBFS/margin_threshold_db`；`desktop/config_schema.py:squelch_default_db`；`desktop/control_panel.py:squelch_changed`；`desktop/main_window.py:_squelch_db/_replay_squelch_gate`；`mbdsdr_ai/sdr_backend.py:set_squelch/SQUELCH_APPLIED_BY_VALUES`；`mbdsdr_ai/receive_chain.py:_make_squelch/set_squelch`（`BaselineSquelch`）；`cpp/src/core/tokens.h:kSquelchMinDb/kSquelchMaxDb/kSquelchDefaultDb/kSquelchAutoMarginDb`；`cpp/src/dsp/spectrum_engine.cpp:setSquelchThreshold`；`cpp/src/control/control_hub.cpp:cmdSetSquelch/cmdGetSquelchStatus`；`cpp/src/ui/main_window.cpp:squelchAutoBtn_/squelchSlider_`；`mbdsdr_ai/analog_demod.py:_MODE_DEFAULTS/squelch_db`（信道 IQ 域，有意分开） | 部分（桌面/后端余量语义归并到 `BaselineSquelch`（取证 `0d6d465` — `mbdsdr_ai/receive_chain.py:80` — `BaselineSquelch`）；原 4 处拦点全落地 `0d6d465`+`e501555`；控制层仍剩 1 处绝对门可绕过 auto） | 桌面/`BaselineSquelch`/`ReceiveChain`/`sdr_backend` 全走余量刻度（`-100→0`、`-50→6`、`-20→16` dB）；C++ 音频 RMS 域绝对 dBFS 门，靠 `domain` 守卫与余量值隔离；`analog_demod` 保持信道 IQ 域绝对（`am/fm -40`、`wfm -30`、`usb/lsb/cw -60`，初值 `-150`），不与桌面滑杆互通 | 操作逻辑线（`desktop/main_window.py`、`desktop/receive_pipeline.py`）；后端线（`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/receive_chain.py`、`mbdsdr_ai/analog_demod.py`）；C++ 内核线（`cpp/src/core/tokens.h`、`cpp/src/dsp/spectrum_engine.cpp`、`cpp/src/control/control_hub.cpp`、`cpp/src/ui/main_window.cpp`） | 已无“同一滑杆数字两端反语义”路径：`sdr_backend.set_squelch` 只记余量（`applied_by=desktop-margin`，不填绝对门）；`receive_chain` 解调前绝对 RMS 门已删，改 `BaselineSquelch`；`cmdSetSquelch` 对 `domain=margin/auto` 且带 `threshold_db` 直接报错不改门限；C++ 勾“自动门限”会显式 `setSquelchAuto(true)` 并禁用滑杆。**剩余 1 处**：legacy 绝对门命令 `cpp/src/control/control_hub.cpp:cmdSetSquelchThreshold`（以及 `cmdSetSquelch` 未带 `domain` 但带 `threshold_db`）仍调用 `cpp/src/dsp/spectrum_engine.cpp:setSquelchThreshold`，后者静默 `squelchAuto_.store(false)`，而 `cpp/src/ui/main_window.cpp:squelchAutoBtn_/squelchSlider_` 仍显示“自动”已勾、滑杆仍禁用 → UI 与内核状态分叉。`analog_demod` 仍是信道 IQ 域绝对门（AI 无头 `demod_analog` 工具，有意分开，界面不暴露） | 跑 `tests/test_squelch_domain.py`（`0d6d465` 新增）；再对同一段 88.1M OTA IQ，桌面 `-50`/`-20` 各一次，开门帧占比应随余量升高而下降；C++ `get_squelch_status` 应回 `none/cpp-auto/cpp-manual` | 部分统一（拦点还剩 1 处：legacy 绝对门命令绕过 auto 状态，UI 不跟随） |
| 2 过境仰角 | `mbdsdr_ai/sat_passes.py:VISIBLE_MIN_ALT_DEG/PASS_MIN_ALT_DEG/PASS_COARSE_STEP_S/PASS_FINE_STEP_S`；`desktop/sat_track_panel.py:_MIN_ELEVATION_DEG`；`desktop/new_spacetime_panel.py:_pass_visibility_gate_deg/_pass_min_elevation_deg/_pass_show_low/refresh_passes`；`desktop/rf_sky_view.py:_refresh_passes`；`desktop/settings.py:effective_pass_min_elevation_deg/effective_pass_visibility_gate_deg/effective_show_low_elevation_passes/effective_pass_window_hours`；`desktop/config_schema.py:pass_min_elevation_deg/pass_window_hours/show_low_elevation_passes` | 部分（5/10 数值有意分开；`effective_pass_*` 四个入口均已接上，`pass_window_hours` 于 `886244d` 接线） | 可见/跟踪基准 **5°**；列表默认 **10°**，`show_low` 打开后门限降到 5° 并给低仰角标注；窗口默认 **24 h**、范围 1–72；仰角默认 10°、范围 0–45 | 卫星线（`sat_passes.py`、`new_spacetime_panel.py`、`rf_sky_view.py`、`sat_track_panel.py`）；设置线（`settings.py`、`config_schema.py`） | 已无写死窗口：两处均读 `effective_pass_window_hours`（实测 6 h=1 条、24 h=5 条、1 h=0 条）；仰角门两侧亦无分叉（`rf_sky_view` 与 `new_spacetime_panel` 同读 `effective_*`，固定夹具 5°=6 条 / 10°=5 条） | 设 `pass_window_hours=1/6/72` 各调一次 `refresh_passes`，条数应随窗口变化（`tests/test_pass_window_hours.py`） | 统一（窗口+仰角门；取证 `886244d` — `desktop/new_spacetime_panel.py:275` — `effective_pass_window_hours`） |
| 3 峰值 | `desktop/spectrum_widget.py:_PEAK_REL_DB_DEFAULT/_PEAK_ABS_FLOOR_DBFS/_PEAK_MIN_SEEN_FRAMES/_PEAK_MATCH_BINS/_PEAK_MAX_MISS_FRAMES/_PEAK_MAX_LABELS`；`desktop/config_schema.py:peak_threshold_db`；`cpp/src/core/tokens.h:kPeakRelDb/kPeakThresholdDefault/kPeakAbsFloorDbfs/kPeakMatchBins/kPeakMinSeenFrames/kPeakMaxMissFrames`；`cpp/src/ui/spectrum_display.cpp`（显示）；`cpp/src/ui/calibration_dialog.cpp`（校准） | 是（显示检测器统一并在测试里锁死；校准门**有意分开**） | 显示 `rel 6.0 dB + abs -100 dBFS + 3 帧确认 + 5 bin 匹配 + 10 帧遗忘`；校准参考门 `15.0 dB` | 频谱线（`desktop/spectrum_widget.py`、`cpp/src/ui/spectrum_display.cpp`、`cpp/src/ui/calibration_dialog.cpp`） | 若合并成 15：显示侧漏掉 median+8 dB 的持续载波（数学上进不了候选）；若合并成 6：校准参考门掉到 6 dB，噪声尖峰被当校准参考，频偏估计被噪声拉走 | 跑 `tests/test_hardcoded_parameter_registry.py` 的显示门/校准门分锁断言；把两个常量各改 1 dB，确认只红对应一条 | 统一（校准门有意分开；取证 `5ece4417` — `desktop/spectrum_widget.py:181` — `_PEAK_REL_DB_DEFAULT`） |
| 4 采样率 | `desktop/config_schema.py:sample_rate_hz`（2.4 M，exposed=False，`4ef3a15`）；`desktop/settings.py:DEFAULTS`；`desktop/constants.py:DEFAULT_SAMPLE_RATE_HZ`（2.4 M）；`mbdsdr_ai/sdr_backend.py:DEFAULT_SAMPLE_RATE_HZ/_FALLBACK_SAMPLE_RATE_HZ/RTLSDRBackend.SAMPLE_RATES`；`desktop/control_panel.py:sample_rate_combo`（能力驱动；旧前端常量 `SAMPLE_RATES` 已删除）；`desktop/main_window.py:_connect_backend/_fill_sr_combo/_on_sample_rate_changed`（能力驱动 + 逐档回读）；`cpp/src/core/tokens.h:kDefaultSampleRateHz/kSampleRatesHz`；`cpp/src/dsp/device_capabilities.cpp:kCandidateRatesHz`（11 档，2026-11-06）；`cpp/src/dsp/rtl_sdr_ops.h:getSampleRate`/`rtl_sdr_source.cpp:pushSampleRate`；`cpp/src/control/control_hub.cpp:cmdSetSampleRate`；`cpp/src/ui/settings_dialog.cpp:srCombo_`；`mobile/lib/app/tokens.dart:AppTokens.sampleRatesHz`；`mbdsdr_ai/device_capabilities.py:sample_rate_options/sample_rate_options_for_backend`；`tests/test_sample_rate_options.py` | 默认值**统一**（取证 `6ed9ee7` — `desktop/constants.py:25` — `DEFAULT_SAMPLE_RATE_HZ = 2_400_000`）；控制面板与连接对话框可见档位**均按后端能力生成**；移动端档位改读后端字段、缺失即「未知」并禁用，`AppTokens.sampleRatesHz` 已标历史默认（无人读取，C.11） | 运行/设置/C++ 默认均 **2.4 MS/s**；C++ AI/控制面档位 `1.024/2.048/2.4/3.2`（**4 档，刻意**）；C++ 设备候选表 **11 档 `250k…3.2M`**（已补 2.16M、2.800M→2.880M）；Python 档位 11 档 `250k…3.2M` | 设置线（`config_schema.py`、`settings.py`、`control_panel.py`、`main_window.py`）；后端线（`sdr_backend.py`）；C++ 内核线（`tokens.h`、`device_capabilities.cpp`、`rtl_sdr_source.cpp`、`control_hub.cpp`） | 旧 `desktop_settings.json` 里仍是 2.048 M：新默认 2.4 只对新建/无键配置生效，老用户启动后下拉显示 2.048、状态栏 2.4，**差 352 kHz** 直到手动切一次；`main_window` import 兜底项文案写 "2.048 MS/s" 却绑 `DEFAULT_SAMPLE_RATE_HZ=2.4 M`，显示与下发值不一致；AI/控制面 4 档 vs 桌面候选 11 档是**刻意分层**（理由见 C.5“7 档逐档去向”），不外扩 AI 面；无回读后端如实标「采样率=请求值，硬件读回不可用」（见 limitations-register 附 M.4） | 新装与旧配置各启动一次，同时打印下拉 `currentData()`、状态栏实际采样率、`DEFAULT_SAMPLE_RATE_HZ`，三值应相等；11 档逐档下发回读=请求（`tests/test_sample_rate_options.py`）；C++ 反证 `test_control_hub.cpp:setSampleRateRejectsOutOfTier`、`test_rtl_sdr_tune.cpp:sampleRateRcFailureIsVisible/sampleRateReadbackMismatchIsExposed/sampleRateReadbackUnavailableIsHonest`、`test_device_capabilities.cpp`（2160/2880 存在、2800 不存在） | 部分统一（默认值 2.4M 统一；控制面板与连接对话框可见档位均按后端能力生成 + 逐档回读/显式拒绝；真机 12 档实测 11 一致 + 1 吸附被抓到；剩移动端接线（内核已下发 `sample_rate_options_hz`，Dart 尚未解析），逐条见 C.11） |
| 5 步进 | `desktop/config_schema.py:tuning_step_hz`；`desktop/control_panel.py:step_combo/step_changed`；`desktop/main_window.py:_on_step_changed`；`desktop/settings_panel.py:_apply/_on_step_from_control/_apply_initial_state`；`desktop/spectrum_widget.py:_PlotState.snap_interval/set_snap_interval/wheelEvent/keyPressEvent/prepare_snap`；`cpp/src/core/tokens.h:kFreqCoarseStepHz`（死值，原 `kFreqFineStepHz`）；`cpp/src/ui/main_window.cpp:stepCombo_/scanStepCombo_` | Python 四处**是**（同一 `snap_interval`）；跨语言**否** | 默认 **10 kHz**，六档 `10/100/1k/10k/100k/1M`；滚轮 1×、Shift ×10、Alt ×0.1；←→ 1×、↑↓ ×0.1；吸附网格 1×；引擎落点=吸附后绝对值 | 设置线（`config_schema.py`、`control_panel.py`、`settings_panel.py`）；频谱线（`spectrum_widget.py`）；C++ UI/内核线（`cpp/src/ui/main_window.cpp`、`tokens.h`） | 默认 10 kHz 下四处一致（差 0）；若下拉改了而吸附没跟上：下拉写 100 kHz、按一下只跳 **10 kHz**（应跳的 1/10）且落点吸回 10 kHz 网格；反向错位一次跳 **1 MHz（10×）**；C++ `kFreqCoarseStepHz=10000.0` 仍**零消费点**，而 Python ↑↓ 实际是 0.1×snap（默认 1 kHz）——真接进细调会错 **10 倍**；C++ 步进 7 档含 1 Hz、Python 6 档最低 10 Hz（1 Hz 配置进来回默认 10 kHz，差 10,000 倍） | `tests/test_settings_schema.py::test_tuning_step_reaches_spectrum_and_combo` + `tests/test_wheel_step_gear.py` 四例；另需一条"下拉=滚轮步长=网格=落点"的端到端断言（目前缺） | 统一（Python 四处；取证 `beba92e9` — `desktop/spectrum_widget.py:1119` — `snap_interval`）；跨语言未统一 |
| 6 FFT 点数 | `desktop/spectrum_widget.py:SpectrumDataGenerator.FFT_SIZES`（3 档）/`set_fft_size`（档外回落最近档 + warning）、`SpectrumPanel._fft_combo`；`desktop/config_schema.py:fft_size`（3 档）；`desktop/settings_panel.py:_apply_spectrum`（回落时同步下拉并提示）；`cpp/src/ui/settings_dialog.cpp:fftCombo_`（3 档）；`cpp/src/ai/tool_schema.cpp:set_fft_params`（enum 1024/2048/4096） | 是（设置/工具条/C++/AI 同一组 3 档；旧值回落 4096 并提示） | 合法集合 `1024/2048/4096`，默认 2048；`8192/16384` 一律回落最近档 **4096**；@2.4 MS/s bin 宽差 **439.453125 Hz → 0** | 频谱线（`spectrum_widget.py`）；设置线（`config_schema.py`、`settings_panel.py`）；C++ UI/AI 线（`settings_dialog.cpp`、`tool_schema.cpp`） | 旧配置存 8192/16384 时启动回落 4096：bin 宽从 146.5 变 585.9 Hz，用户会觉得“分辨率变粗”；状态栏明示“内核不支持，已回落最近档 4096”，不会把界面显示成不存在的档位 | 设 8192 读回实际 4096 且状态栏有回落提示；两端 `sr/fft` 均应为 585.938 Hz（差 0） | 统一（取证 `e883a62b` — `desktop/spectrum_widget.py:218` — `FFT_SIZES`） |
| 7 dB 量程 | `desktop/spectrum_widget.py:_PlotState.db_min/db_max/_DB_AXIS_MIN/_DB_AXIS_MAX/apply_db_range`；`cpp/src/core/tokens.h:kDbLowerDefault/kDbViewUpperDefault/kDbCeilingMax`；`cpp/src/ui/spectrum_widget.cpp`/`settings_dialog.cpp` | 默认视窗**统一**，夹取边界仍否 | Python 默认 **-100..-20**，轴夹取 **-160..0**，最小 span 10；C++ 默认视窗 **-100..-20**，显示天花板硬上限 **0 dBFS**（`kDbCeilingMax`，与默认视窗上限分开） | 频谱线（`spectrum_widget.py`）；C++ UI 线（`tokens.h`、`settings_dialog.cpp`） | 默认视窗 -50 dBFS 在两端都可见；可拖边界仍不同：Python **-160..0** vs C++ **-140..20**（下界差 20 dB、上界差 20 dB），拖到底时同一录音会出现"Python 已贴底、C++ 仍有 20 dB 余量"的观感差 | 同一 IQ 段两端各画一次，读回 `db_min/db_max`，看 -50 dBFS bin 是否在图内；再把两端轴拖到下界，比较同一 bin 的可视性 | 默认视窗统一（取证 `61465706` — `cpp/src/core/tokens.h:335` — `kDbLowerDefault`）；夹取边界未统一 |
| 8 瀑布深度 | `desktop/spectrum_widget.py:WF_DEPTH_CHOICES/WF_DEPTH_DEFAULT_ROWS/set_waterfall_depth/set_waterfall_depth_rows`；`desktop/config_schema.py:waterfall_depth_rows`；`cpp/src/core/tokens.h:kWaterfallDepthDefault/kWaterfallDepthChoices`；`cpp/src/ui/spectrum_widget.cpp:setWaterfallDepth`；`cpp/src/ai/agent_tools.cpp` | 是（两侧同一六档枚举；档外值回 256 并 warning，不夹取） | 默认 **256**；合法集合 `{128,256,512,1024,2048,4096}`；档外值（如 384）回落 256 | 频谱线（`spectrum_widget.py`）；设置线（`config_schema.py`）；C++ UI/AI 线（`spectrum_widget.cpp`、`agent_tools.cpp`） | 旧配置/旧脚本写 **384** 时被回落到 256：回看行数少 **128 行（1.5 倍）**，用户以为设成 384 实际只有 256；日志有 warning，但界面不主动提示 | 设 384 读回实际深度应为 256 且有一条 warning；AI 调 `rows=384` 与 GUI 同口径 | 统一（取证 `d4b2a807` — `desktop/spectrum_widget.py:169` — `WF_DEPTH_CHOICES`） |
| 9 无数据哨兵 | `desktop/spectrum_widget.py:_resample_max`（空段填 `np.nan`）；`cpp/src/dsp/spectrum_engine.h:rssiDbfs_`（`quiet_NaN`）；`cpp/src/core/tokens.h:kPeakAbsFloorDbfs`（-100.0f，峰值绝对门，不是哨兵）；`desktop/status_panel.py:_LEVEL_MIN` 与 `mbdsdr_ai/fm_scan.py`、`mbdsdr_ai/new_spacetime_amr.py:_NO_SIGNAL_POWER_DB`（-120.0 无信号电平） | 频谱哨兵**统一**（`8d940b7`，两侧均 NaN；取证 `8d940b7` — `cpp/src/dsp/spectrum_engine.h:902` — `quiet_NaN`）；"无信号电平"仍 4 处 -120.0 | 频谱哨兵 Python/C++ 均为 **NaN**；无信号电平 **-120.0 dBFS**；峰值绝对门 **-100.0 dBFS** 是另一个量 | 频谱线（`spectrum_widget.py`）；C++ 内核线（`spectrum_engine.h`）；状态栏/扫描/AMR 线（`status_panel.py`、`fm_scan.py`、`new_spacetime_amr.py`） | 频谱侧已不会把"没采到"画成 -120 dBFS（历史差 **20.0 dB** 已清零）；但状态栏/扫描/AMR 仍用 -120.0 表示"无信号"，与 -100.0 绝对门差 **20.0 dB**——用户看到 -120 时分不清"没信号"与"低于峰值门" | 构造空段读回 `NaN`；再读 `status_panel._LEVEL_MIN` 与 `kPeakAbsFloorDbfs`，两点仍差 20.0 dB | 统一（频谱哨兵）；电平下限未统一（-120.0：`status_panel._LEVEL_MIN`、`fm_scan.py`、`new_spacetime_amr.py:_NO_SIGNAL_POWER_DB`、`control_panel.py` 电平夹取） |
| 10 过境采样步长 | `mbdsdr_ai/sat_passes.py:PASS_COARSE_STEP_S/PASS_FINE_STEP_S/_COARSE_STEP_S/_FINE_STEP_S`；`desktop/rf_sky_view.py:PASS_COARSE_STEP_S/PASS_FINE_STEP_S`（引用 `sat_passes`）/`_refresh_passes`；`desktop/new_spacetime_panel.py:refresh_passes`（`step_sec=PASS_COARSE_STEP_S`） | 是（`4faddb2` 两端收敛到同一常量；历史 300/30 已删） | **60 s 粗扫 + 2 s 细采样**（两侧同一常量） | 卫星线（`sat_passes.py`、`rf_sky_view.py`、`new_spacetime_panel.py`） | 已无 300/30 路径（历史差：粗扫 **240 s（5×）**、细采样 **28 s（15×）**）；窗口亦已两侧读设置（`886244d`，`PASS_WIN_HOURS` 已删） | 同一 TLE/站点/时刻比对两面板峰值仰角与 AOS/LOS：实测旧 300/30 峰值差 **13.1 s / 0.40°**、新 60/2 **0.9 s / 0.004°**（`tests/test_pass_step_alignment.py`，已入库 `4faddb2`；取证 `4faddb2` — `desktop/rf_sky_view.py:125` — `_SAT_PASS_COARSE_STEP_S`） | 统一 |
| 11 状态提示时长 | `desktop/main_window.py` 的 `statusBar().showMessage(...)` 共 111 处 | 否 | 字面毫秒 88 处：`2 s`×1、`3 s`×15、`4 s`×25、`5 s`×16、`6 s`×13、`8 s`×14、`10 s`×3、`15 s`×1；16 处单参走 Qt `timeout=0`；7 处传变量 | 操作逻辑线（`desktop/main_window.py`） | 8 档时长：同一类"已保存"在 A 处 4 s、B 处 8 s；2 s 的错误提示抬头前就消失；15 s 那条压住随后 1–3 s 的新提示；16 处单参消息常驻状态栏，断连后仍可能挂"已连接" | 扫全部 `showMessage` 时长并断言只落在一个登记档位集合（如 2/4/8 s），未登记值或单参常驻一律红 | 未统一 |
| 12 窗宽/高最小尺寸 | `scripts/ui_narrow_breakdown.py`（**建议唯一事实源**：以脚本输出为准）；`desktop/main_window.py:setMinimumSize`（1000×600）；`cpp/src/core/tokens.h:kMainMinW/kMainMinH`（960×600） | 否（双指纹两套数） | 验收口径：**两者都须 ≤1360×728**；记录值：offscreen **1625×729**、真屏 **1237×799**；`1726/736` 已判“未复现、不得据此验收” | 操作逻辑线（`scripts/ui_narrow_breakdown.py`、`desktop/main_window.py`）；C++ UI 线（`tokens.h`） | 1366×768 真机高度超 **71 px（799 > 728）**；按 offscreen 指纹宽度超 **265 px（1625 > 1360）**——同一窗口两套指纹两套数，取哪套都能得出相反验收结论 | 同一 HEAD、同一 DPR 跑 `scripts/ui_narrow_breakdown.py`，off screen 与真屏两份输出都断言 ≤1360×728 | 未统一（两套指纹两套数，验收口径已定：两者都须 ≤1360×728） |

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
| 11 | `waterfall_palette` | sdrpp | sdrpp/soft | `desktop/spectrum_widget.py:SpectrumPanel._on_palette_changed` → `_PlotState.wf_colors` | 是 |
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
> 已读 `effective_pass_*`（实测 5° vs 10° 列表 7 条 vs 6 条）；第 4 行采样率默认值为 2.4 MS/s
> （`6ed9ee7`/`4ef3a15`）；第 6 行 FFT 设置页/工具条/C++/AI 已同收 1024/2048/4096（旧值回落 4096）；
> 第 8 行瀑布深度已是六档枚举（非任意 128–4096）。**上表 35 项均已接线**（第 25 行于 `886244d` 补齐）。

### 附录 A-1 红表：有键无生产消费点（0 项；`pass_window_hours` 已于 `886244d` 接线，保留记录）

| # | 键 | 证据（文件:符号） | 为什么没接上 | 处置（已定：接线，不删键） | 派单 |
|---|---|---|---|---|---|
| 25 | `pass_window_hours` | 历史：两处写死 24.0；现已两处读 `effective_pass_window_hours` | 读取入口先落地、消费点没接 | 已补消费点（`886244d`）：6 h 与 24 h 实测 **1 条 vs 5 条**；**未从 `config_schema` 删键** | 卫星过境线（已完成） |

### 附录 A-2 代码侧复核（参数治理线，2026-11-05）

复核方法：逐键反查“改值后真正读它”的生产符号；`settings_panel._apply` 只算转发，不算消费点。
结论：**复核当时 34/35 有生产消费点**；`886244d` 接线后 **35/35**；共改 3 行（#24、#25、表格结论）。

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

> 证据等级 E4：本段数字未验证，引用前须复核（判据见 docs/evidence-standard.md）。

| # | 物理量 | A 位置与值 | B 位置与值 | 实测差值 | 后果 | 现状态 |
|---|---|---|---|---|---|---|
| B1 | 采样率 | `desktop/main_window.py` 采样率兜底项文案 `"2.048 MS/s"`（`addItem` 绑 `DEFAULT_SAMPLE_RATE_HZ`） | `desktop/constants.py:DEFAULT_SAMPLE_RATE_HZ` 与 `mbdsdr_ai/sdr_backend.py:DEFAULT_SAMPLE_RATE_HZ` = **2,400,000 Hz**；C++ `tokens.h:kSampleRatesHz` 含 2.048e6/2.4e6 | **352,000 Hz（17.19%，1.172×）**；档位 11 vs 4，差 **7 档** | 该项显示 2.048 实际下发 2.4；旧配置 2.048 与状态栏 2.4 并存直到手动切一次 | 默认值统一（取证 `6ed9ee7` — `desktop/constants.py:25` — `DEFAULT_SAMPLE_RATE_HZ = 2_400_000`）；文案与档位集合未统一 |
| B2 | 静噪 | `mbdsdr_ai/squelch.py:margin_threshold_db`（-50→**6.0 dB 余量**；-20→16.0） | `cpp/src/dsp/spectrum_engine.cpp:setSquelchThreshold`（**绝对 dBFS**；-50→-20） | 同一滑杆 -50→-20：桌面余量 **+10.0 dB（更严）**，C++ 绝对门 **+30.0 dB（更松）** → 响应差 **40.0 dB 且方向相反** | 历史语义相反；现由 `domain` 守卫隔离，余量值不再直落绝对门 | 部分统一（原四拦点全落地；剩 legacy 绝对门命令绕过 auto 1 处；上述 40 dB 为历史差值） |
| B3 | 峰值门 | `desktop/spectrum_widget.py:_PEAK_REL_DB_DEFAULT` = **6.0 dB**（C++ 显示 `tokens::kPeakRelDb` 同值） | `cpp/src/ui/calibration_dialog.cpp` 用 `tokens::kPeakThresholdDefault` = **15.0 dB** | **9.0 dB（2.5×）** | 合并任一侧都会坏：6→15 漏持续弱载波；15→6 噪声尖峰当校准参考 | 有意分开（`b8bc514`/`5ece441`） |
| B4 | 过境采样 | `mbdsdr_ai/sat_passes.py:PASS_COARSE_STEP_S` = **60.0**、`PASS_FINE_STEP_S` = **2.0** | `desktop/rf_sky_view.py` HEAD 已引用同常量；历史为 300/30（`4faddb2` 前） | 历史：粗扫 **240 s（5×）**、细采样 **28 s（15×）**；现在 **0/0** | 历史两面板峰值时刻最多偏 15 s；实测（ISS 39.9°N/116.4°E，24h）：旧 300/30 峰值差 **13.1 s / 0.40°**，新 60/2 **0.9 s / 0.004°**；新时空面板 AOS/LOS 对同源参考差 **+29.8 s / +39.6 s**（≤1 粗扫步长） | 统一（取证 `4faddb2` — `desktop/rf_sky_view.py:125` — `_SAT_PASS_COARSE_STEP_S`） |
| B5 | 频率步进死值 | `cpp/src/core/tokens.h:kFreqCoarseStepHz` = **10000.0**（全仓零消费点） | `desktop/spectrum_widget.py:_PlotState.keyPressEvent` ↑↓ = `snap_interval*0.1`，默认 **1,000 Hz** | **9,000 Hz（10×）**；←→ 整格 10,000 → 差 **0** | token 若接进细调会错 10 倍；C++ 7 档含 1 Hz vs Python 6 档最低 10 Hz（差 10,000×） | 跨语言未统一 |
| B6 | FFT 档位 | `desktop/spectrum_widget.py:SpectrumDataGenerator.FFT_SIZES`（1024/2048/4096，**3 档**） | `cpp/src/ui/settings_dialog.cpp:fftCombo_` 与 `cpp/src/ai/tool_schema.cpp:set_fft_params`（同 3 档） | 现 **0**；历史 8192/16384 vs 4096 → bin 宽 **439.453125 Hz（4×）** | 旧值 8192/16384 回落最近档 4096 并提示 | 统一（档外回落最近档，bin 宽差 439.453125 → 0；取证 `e883a62b` — `desktop/spectrum_widget.py:218` — `FFT_SIZES`） |
| B7 | 瀑布行数 | `desktop/spectrum_widget.py:WF_DEPTH_CHOICES`（6 档） | `cpp/src/core/tokens.h:kWaterfallDepthChoices`（同 6 档）与 `agent_tools.cpp` | 现在 **0**；历史 384 vs 256 = **128 行（1.5×）** | 档外值 384 回落 256，回看时长少 1.5 倍 | 统一（取证 `d4b2a807` — `desktop/spectrum_widget.py:169` — `WF_DEPTH_CHOICES`） |
| B8 | 无数据哨兵 | `desktop/spectrum_widget.py:_resample_max` = **`np.nan`**（`8d940b7` 前为 -120.0） | `cpp/src/dsp/spectrum_engine.h:rssiDbfs_` = **`quiet_NaN`**（同 commit 前为 -100.0f） | 现在 **0 dB**（两侧同为 NaN）；历史 **20.0 dB** | 频谱侧已能区分“没采到”与“弱信号”；但状态栏/扫描/AMR 仍用 -120.0 当“无信号”，与 -100.0 绝对门差 20.0 dB | 统一（哨兵；取证 `8d940b7` — `cpp/src/dsp/spectrum_engine.h:902` — `quiet_NaN`）；`-120.0` 电平常量未统一 |

## 附录 C：组 4 采样率档位（方案 C 已裁定，实现未落；参数治理线 2026-11-05）

> **11-05 快照，已过时；结论以 C.1 表（11 档，含 2.16M / 2.880M）为准。**

> **裁定**：采纳 **方案 C（C++ 4 档 + 250k = 5 档）**，否决 A。
> **否决 A 的两条**：① A 把 **250k → 1024k（落回 774,000 Hz）**，砍掉唯一低端窄带档——librtlsdr 合法区间是 `(225k,300k]` 且 300k 有死区，250k 是唯一低档；用户设了窄带却被规则拽到 1.024M，带宽 ×4。② 1.536M → 1.024M 是 512k 的大跳。用户明确意图被改写，不能靠回落规则遮过去。
> **否决 B 的理由同样写清**：1536/1792/1920/2160/2560/2880 六档都落在 librtlsdr 合法区间 900k–3.2M，技术上可用；为了对齐一个 C++ 枚举而把设备本来能做的事从产品里删掉，方向是反的。**B 不现在拍板**：先出代价表（C.5），由用户决定是否补。
> **本轮不动实现**；组 4 状态列保持“档位集合未统一（11 vs 4）”，C 落地前不得改“统一（收敛态）”。

### C.0 口径订正（两处，防误述）

1. **落回不是“静默改写”**：`mbdsdr_ai/sdr_backend.py:_apply_sample_rate` 对“吸附到离散档”记 `logger.info`，对“死区/越界钳位”记 `logger.warning`；桌面恢复路径 `desktop/control_panel.py:set_sample_rate` 会把下拉回填到最近档（`blockSignals`），状态栏读 `backend.get_sample_rate()`（实际生效值）。**但“用户能看见”不等于“用户能接受”**——可见变化（日志/下拉/状态栏）只说明没有被无声吞掉，不构成对改写的认可。另有一条同频项：`desktop/main_window.py:_on_sample_rate_changed` 手动切换**不检查 `set_sample_rate()` 的 bool 返回值**，只在异常分支弹状态栏；后端返回 `False` 但未抛异常时，下拉停在用户所选项、状态栏回读真实值。该条当时登记在 C.3；**2026-11-06 已落地**（取证 `70429a02` — `desktop/main_window.py:8186` — `该采样率未被设备接受（保持`（基准 `70429a02`），见 C.10.1）。
2. **“C++ 4 档”只是枚举面，不是 C++ 全部**：C++ 实际有 3 套数（见 C.1）。设备下拉由 `cpp/src/dsp/device_capabilities.cpp:buildSampleRateOptions` 从 `kCandidateRatesHz`（11 档）按设备量程过滤，**250k 早就在 C++ 设备下拉里**；`cpp/src/core/tokens.h:kSampleRatesHz` 的 4 档只约束 AI 工具枚举与设置对话框首选值。因此“C++ 缺 7 档”描述的是枚举面与首选值面，不是实时设备下拉面。

### C.1 现状：采样率档位共 6 处，其中 C++ 内部就 3 套

| 出现点（文件:符号） | 档数 | 集合 | 与其他处的差 |
|---|---|---|---|
| `mbdsdr_ai/sdr_backend.py:RTLSDRBackend.SAMPLE_RATES` | 11 | 0.25 / 1.024 / 1.536 / 1.792 / 1.92 / 2.048 / 2.16 / 2.4 / 2.56 / 2.88 / 3.2 M | 桌面下拉的实际来源 |
| `desktop/control_panel.py:_CapabilitySampleRateCombo/_populate_sample_rate_options` 与 `desktop/main_window.py:_fill_sr_combo` | 0（**不持有档位表**） | 展开/选中设备时经 `mbdsdr_ai/device_capabilities.py:sample_rate_options_for_backend`（控制面板）或 `sample_rate_options(device)`（连接对话框）动态取「后端表 ∩ 设备量程」；后端无离散表只给量程两端，量程未知给单项「未知」并禁用 | 与 `sdr_backend` 0 差（无副本；`tests/test_sample_rate_single_source.py:test_desktop_holds_no_local_tier_copy` 断言 `desktop/*.py` 不含 `SAMPLE_RATES`） |
| `cpp/src/core/tokens.h:kSampleRatesHz` | 4 | 1.024 / 2.048 / 2.4 / 3.2 M | 缺 7 档（250k/1536/1792/1920/2160/2560/2880） |
| `cpp/src/dsp/device_capabilities.cpp:kCandidateRatesHz` | 11 | 0.25 / 1.024 / 1.536 / 1.792 / 1.92 / 2.048 / **2.16** / 2.4 / 2.56 / **2.88** / 3.2 M | 与桌面 11 档 0 差（2026-11-06 已补 2.16M；2.8M 档改为 2.88M——依据真机读回：请求 2,800,000 被设备吸附到 2,880,000，候选表只列设备实际给出的值，见附 M.4 L60） |
| `cpp/src/ui/settings_dialog.cpp:srCombo_`（局部 `kRates` 字面量） | 4 | 1.024 / 2.048 / 2.4 / 3.2 M | 与 `tokens` 0 差，但**未引用 tokens，单写一份** |
| `mobile/lib/app/tokens.dart:AppTokens.sampleRatesHz` | 4（死表：已 `@Deprecated`、无人读取） | 1.024 / 2.048 / 2.4 / 3.2 M | 与 C++ tokens 0 差，但这是**本地常量表**；`spectrum_page.dart:762-770` 档位缺失即「未知」并禁用、越集值 `value=null`，**已无 `.first` 兜底**；`sample_rate_options_hz` 尚未解析（C.12） |

> 结论修正：旧提法“Python 11 vs C++ 4”只覆盖枚举面。**真实分叉有三类**：① 枚举/首选 4 档 vs 桌面 11 档；② C++ 设备下拉 11 档 vs 桌面 11 档（2026-11-06 已 0 差）；③ 移动端 4 档。方案 C 的 5 档集合要同时收口这三类。

> **越界改动登记（范围外，单独记账；不计入 F1/F2/F3）**：`dd72ba1c` 声明的范围只有
> F1（档位校验）、F2（写入失败可见）、F3（硬件读回），但同一笔还改了候选档位表：
> `cpp/src/dsp/device_capabilities.cpp:43` 插入 `2160000.0`、`:46` 把 `2800000.0`
> 改为 `2880000.0`，并在 `cpp/tests/test_device_capabilities.cpp:117-122` 补三条断言。
> 超出声明的三条范围，且没有独立提交与独立验收。值保留，但理由必须按实测：
> **2.8 不是 2.88 的另一种写法，而是设备从不给出的虚构档**（`2800000.0` 由
> `30214fac` 引入）；真机请求 `2,800,000` 读回 `2,880,000`（+80,000 Hz，E3，见
> `limitations-register` 附 M.4 L60），故候选表只列设备实际给出值。原注释把它
> 写成「同一速率的两种拼写」，不成立，已替换为上述实测依据。

### C.2 方案 C 的 5 档集合与旧值回落映射（`{250k, 1024k, 2048k, 2400k, 3200k}`）

| 旧值 | 新值 | 差值（Hz） | 依据 |
|---|---|---|---|
| 250,000 | 250,000 | **0** | C 保留该档（A 会去掉） |
| 1,024,000 | 1,024,000 | 0 | 在集合内 |
| 1,536,000 | 1,024,000 | **512,000** | 与 1.024/2.048 等距（各 512k），平局取低 |
| 1,792,000 | 2,048,000 | **256,000** | 距 1.024=768k、距 2.048=256k |
| 1,920,000 | 2,048,000 | **128,000** | 距 2.048=128k（最近） |
| 2,048,000 | 2,048,000 | 0 | 在集合内 |
| 2,160,000 | 2,048,000 | **112,000** | 距 2.048=112k、距 2.4=240k |
| 2,400,000 | 2,400,000 | 0 | 在集合内 |
| 2,560,000 | 2,400,000 | **160,000** | 距 2.4=160k、距 3.2=640k |
| 2,800,000（2026-11-06 前 C++ 设备下拉有；实为设备从不给出的虚构档，请求即被吸附到 2.88M） | 2,400,000 | **400,000** | 距 2.4=400k、距 3.2=400k，**也是平局取低** |
| 2,880,000 | 3,200,000 | **320,000** | 距 3.2=320k、距 2.4=480k |
| 3,200,000 | 3,200,000 | 0 | 在集合内 |

复核结论：与旧 A 表那组数字逐条一致（256,000 / 128,000 / 112,000 / 160,000 / 320,000 全对上）；新增两条 A 表没有的：250k 不再回落（0），2.800M 回落 2.4M（400,000，平局取低）。
规则与实现口径（与 FFT 8192→4096 同做法）：不在新集合 → `RTLSDRBackend.nearest_sample_rate`（欧氏距离、平局取低）→ `logging.warning("sample_rate_hz=%s 不在支持档位，已回落 %s", requested, applied)` → 下拉回填最近档 → **实际生效值写回配置**；采样率回落还要重建接收流水线（`main_window._on_sample_rate_changed` 已有该路径），提示走状态栏一次性消息。

### C.3 同步面清单（改任一处，其余必须同频；去重共 28 个出现点：代码 17 / 测试 7 / 文档 4）

> 计数口径（2026-11-06 按 `HEAD 63bf5874` 重数）：只算「自己持有一份档位值 / 顺序 / 默认值」的点，按 `文件:符号` 去重；纯消费点（按表遍历、比对、格式化、UI 绑定变量）不计。**下表「数量」列 = 本行基准 + 归属本行的位置**（跨行不重复），五行相加 = 28。
> 旧标题写「23（15/4/4）」、旧五行「数量」相加 26：26 是**逐行「其余位置」相加**，其中第 2、3 行共享同一批 8 个 C++ 位置被重复计了一次——这正是标题与表不闭合的原因；现已按去重重算，五行相加等于标题总数。
> 分项：只数「档位集合」时是 **24（代码 13 / 测试 7 / 文档 4）**，另 **4 个默认值点**（`cpp/src/core/tokens.h:kDefaultSampleRateHz`、`mbdsdr_ai/sdr_backend.py:DEFAULT_SAMPLE_RATE_HZ`、`desktop/constants.py:DEFAULT_SAMPLE_RATE_HZ`、`desktop/config_schema.py` 回退字面量）单列，合计 28。
> 旧标题「23（15/4/4）」按 HEAD 无法复现，差额来自：漏算三份自持副本（`mbdsdr_ai/device_manager.py:_RTL_SRATES`、`mbdsdr_ai/osmosdr_source.py:RTL_SAMPLE_RATES_HZ`、`scripts/sample_rate_readback_check.py:RTL_TIERS`）；旧 15 另含两个消费点（`cpp/src/control/control_hub.cpp:cmdSetSampleRate`、`desktop/control_panel.py:sample_rate_combo`）；测试 4 也漏了 `cpp/tests/test_control_hub.cpp`、`tests/test_settings_schema.py`、`tests/test_desktop_constants.py`。

| 若改这里（基准） | 归属本行的位置（含基准；跨行不重复） | 数量 |
|---|---|---|
| `mbdsdr_ai/sdr_backend.py:RTLSDRBackend.SAMPLE_RATES`（11 档） | `mbdsdr_ai/device_capabilities.py:_BACKEND_RATE_TABLE`；`mbdsdr_ai/rtlsdr_params.py:SUPPORTED_SAMPLE_RATES`（10 档，故意不同）；`mbdsdr_ai/device_manager.py:_RTL_SRATES`（11 档）；`mbdsdr_ai/osmosdr_source.py:RTL_SAMPLE_RATES_HZ`（9 档，另一套集）；`scripts/sample_rate_readback_check.py:RTL_TIERS`（11 档脚本副本）；`tests/test_sample_rate_options.py`。桌面**不持副本**：`desktop/control_panel.py`/`main_window.py` 经 `sample_rate_options_for_backend` 动态取表（属消费点，不计入数量） | 7 |
| `cpp/src/core/tokens.h:kSampleRatesHz`（4 档白名单） + `cpp/src/dsp/device_capabilities.cpp:kCandidateRatesHz`（11 档候选） | 白名单 ⊆ 候选，**同一批 C++ 位置只归本行一次**（旧表把 `kCandidateRatesHz` 单列一行，导致这 8 个位置重复计）：`cpp/src/ui/settings_dialog.cpp:srCombo_`（局部 4 档 `kRates[]`，应改为引用 tokens）；`cpp/src/ai/tool_schema.cpp`（enum + `1024000 / 2048000 / 2400000 / 3200000` 描述字面量）；`cpp/src/ai/agent_tools.cpp:execSetSampleRate`（错误文案四档字面量）；`cpp/src/ui/main_window.cpp`（`currentIndex()==0 → 2` 硬编码索引，见风险）；`cpp/tests/test_device_capabilities.cpp`；`cpp/tests/test_tool_schema.cpp`；`cpp/tests/test_control_hub.cpp`（`sample_rate_options_hz` 三态）；`tests/test_sample_rate_single_source.py`（四处逐项相等 + `module_panel` 派生）。`cpp/src/control/control_hub.cpp:cmdSetSampleRate` 是按表比对，属消费点，不计入数量 | 10 |
| `mobile/lib/app/tokens.dart:AppTokens.sampleRatesHz`（4 档死表） | `mobile/lib/pages/spectrum_page.dart:761-770`（下拉由 `controller.sampleRateOptionsHz` 生成，缺失即「未知」并禁用，**无 `.first` 兜底**）；`mobile/lib/widgets/device_info_card.dart`（仅显示实际值，不计数）；移动端分派测试随 C.12 新增。旧表把后两项算进「数量」，实为 2 个消费点 → 本行只计基准自身 | 1 |
| `desktop/config_schema.py:sample_rate_hz` / `desktop/constants.py:DEFAULT_SAMPLE_RATE_HZ` | 默认值是**另一物理量**（只影响首选值，不影响档位集合）：`cpp/src/core/tokens.h:kDefaultSampleRateHz`；`mbdsdr_ai/sdr_backend.py:DEFAULT_SAMPLE_RATE_HZ`；`tests/test_settings_schema.py`；`tests/test_desktop_constants.py`；`desktop/settings.py:DEFAULTS` 为消费点，不计数 | 6 |
| 文档同步面（上述任一处改动后） | `docs/hardcoded-parameters.md`（§10-3 / A10）；`docs/redundancy-register.md`（§B B1–B8）；`docs/limitations-register.md`（L49 / L60）；`docs/settings-inventory.md`（§18 / `:767`）；本账本自身（C.1 / C.9 / C.10 / C.11 / C.12） | 4 |

补充事实：手动切换路径的 bool 返回未校验（`desktop/main_window.py:_on_sample_rate_changed` 调 `set_sample_rate` 不判返回值）——这是 C.0 第 1 条的同频项，落地时必须一并补“返回 False 也走状态栏可见错误并回填实际档”，否则会出现“下拉停在 250k、实际仍是 1.024M”的新分叉（2026-11-06 已落地：`70429a02` — `desktop/main_window.py:8186` — `该采样率未被设备接受（保持`（基准 `70429a02`），见 C.10.1）。
`desktop/settings_panel.py` **没有**独立采样率档位表（grep `sample_rate` 无命中），只经 `control_panel`/设置下发 → 同步面为 0，但它的文案若引用档位需跟随；`cpp/src/core/app_settings.*` **没有** sampleRate 键（grep 无命中），C++ 不持久化采样率，故 C 的旧配置迁移只在 Python 侧发生，C++ 侧按 `kDefaultSampleRateHz` 起档。
**文案审计线口径（别撞车）**：桌面 `desktop/constants.py:37`（符号 `format_sample_rate`）已统一 `MS/s`、3 位小数（取证 `d0b585a0` — `desktop/constants.py:33` — `0.250 MS/s`，其中 `:33` 是该提交当时的历史行号；行号随重构漂，定位以符号名为准；回归锁 `ebc897fa` — `tests/test_copy_units_audit.py:28` — `0.250 MS/s`）；移动端 `d543c87d` — `mobile/lib/pages/spectrum_page.dart:389` — `toStringAsFixed(3)} MS/s`（同字面量亦在 `mobile/lib/widgets/device_info_card.dart:104`）。新增 250k 时**必须显示 `0.250 MS/s`，不得写 `250 kHz`**；`docs/copy-audit.md` 的 U-03 已是这条口径，文档里不要另立单位。

**`device_panel` 采样率输入为什么保留 Hz（取舍记账）**：该输入框是**下发给后端**的原始量，后端接口与 `.cf32` 帧格式都以 Hz 计；若把它改成 `MS/s` 输入框，`2048000` 这类整数会被 3 位小数截断，而采样率在调谐/抽取链路上是要精确到 Hz 的参数。折中：**输入保持 Hz，旁边给等价值**——`desktop/device_panel.py:_update_sr_equiv`，输入 `2048000` 显示 `= 2.048 MS/s`，空/非数字/非正显示 `—`；等价值只走 `format_sample_rate`（与状态栏/内核同一读数口径），**不改写输入框、不进连接载荷**。回归：`tests/test_settings_semantics.py::TestE2bSampleRateHzInputWithMsPerSEquiv`。

### C.4 250k 证据分级（细则见 `docs/limitations-register.md` 附 M）

- **驱动依据（已核实）**：librtlsdr 合法区间 `(225k, 300k]`（`sdr_backend.py:_clamp_sample_rate`，来源 librtlsdr.c 区间判定），250k 是低段唯一档；`cpp/src/dsp/device_capabilities.cpp:kRtlsdrSrMinHz = 250000.0` 与 `kCandidateRatesHz` 首项 250k、`cpp/tests/test_device_capabilities.cpp` 已断言 `opts.contains(250000.0)`——C++ 设备下拉与 C++ 测试都已在用 250k。
- **真机依据（未核实）**：仓内没有 250k 真机读回/丢样证据；`tests/fake_rtl.py` 接受任意 rate，不校验区间，**测试通过不代表硬件通过**。
- **处置（已裁定）**：250k 不做默认值；测试不得假定 `set_sample_rate(250_000)` 一定成功；失败要优雅回落 1024k 并给可读错误（不静默吞）。

### C.5 方案 B 的真实代价表（只出表，不实现）

| 项目 | 要动什么（文件:符号） | 核实状态 | 工作量区间 | 风险 |
|---|---|---|---|---|
| 档位枚举 | `cpp/src/core/tokens.h:kSampleRatesHz` 4→11；`cpp/src/ai/tool_schema.cpp` enum + 描述；`cpp/src/ai/agent_tools.cpp:execSetSampleRate` 校验文案；`cpp/tests/test_tool_schema.cpp` 钉住四档 | **已核实**（四处都在，测试钉死描述文本） | 0.5–1 人日 | 描述/枚举不同步时 AI 工具先红；`test_tool_schema` 需成对更新 |
| 设置索引 | `cpp/src/ui/settings_dialog.cpp:srCombo_` 局部 `kRates`（4 档）；`rx/sampleRate` 保存/恢复走“最近距离循环”不依赖索引（**已核实**） | 索引风险**已核实**：保存/恢复很安全 | 0.5 人日 | 若改成引用 tokens 表则零风险；不改则两份数继续漂 |
| 实时设备下拉（取证 `dd72ba1c` — `cpp/src/dsp/device_capabilities.cpp:43` — `2160000.0`） | `cpp/src/dsp/device_capabilities.cpp:kCandidateRatesHz`（11 档）——**已落地**：补 2.160M，2.800M 改正为 2.880M | **已完成**（`test_device_capabilities` 钉住 2160/2880 存在、2800 不存在） | — | 2.8M 不是可独立下发的速率点（真机请求 2.8M 读回 2.88M）；候选表按设备实际给出值列 2.88M，不再两档并存 |
| 扫描带宽 | `dsp::ScanConfig`（`cpp/src/ui/main_window.cpp` 扫描区、`cpp/src/control/control_hub.cpp:cmdStartScanLink`）只吃起止/步进，**不读采样率** | **已核实**：加档不改扫描逻辑 | 0 | 仅 `start_scan_link` 的半窗固定 200 kHz，与采样率无关（见下条 C 专属风险） |
| 瀑布最小宽度 | `cpp/src/core/tokens.h:kWaterfallMinH=48` 是**高度**；`kSpectrumMinW=480`、`kComboMinW=120` 与档位无关；瀑布列宽随 FFT/窗口自适应 | **已核实**：无按档位变化的最小宽度约束 | 0 | 无新增风险；不要把“最小宽度”当档位预算 |
| FFT 预算 | `cpp/src/dsp/spectrum_engine.cpp:setFftSize` 只收 1024/2048/4096；帧窗 = FFT size，**不随采样率变** | **已核实**：FFT 与采样率解耦 | 0 | 加档后 bin 宽 = sr/N 自然变化（1.536M/4096≈375 Hz…），非缺陷 |
| 序列化/快照（取证 `dd72ba1c` — `cpp/src/control/control_hub.cpp:1024` — `sample_rate_readback_available`） | `cpp/src/control/control_hub.cpp:cmdSetSampleRate`（2026-11-06 起档位校验，档外拒绝）；`get_status` 新增 `sample_rate_readback_available` / `sample_rate_status` / `sample_rate_error`；`cpp/src/core/app_settings.*` 无 sampleRate 键 | **已落地** | 0 | 无持久化迁移；AI 面与控制面现在同口径（都按 `tokens::kSampleRatesHz` 逐档比对、档外拒绝） |
| 桌面/移动端同步 | 桌面 11 档已含 B 的 6 档（**已核实**，无需加）；移动端 4 档需 +6（`mobile/lib/app/tokens.dart` + 下拉 + 测试） | 桌面**已核实**，移动端未核（本轮未跑 flutter） | 1–1.5 人日 | 移动端已无本地表兜底（`spectrum_page.dart:762-770` 缺失即「未知」并禁用）；改档只需解析 `sample_rate_options_hz` 并删除死表（C.12） |
| 设备真实性 | 6 档在 librtlsdr 合法区间内（**已核实**），但 1536/1792/1920/2560/2880 无仓内真机证据；2160 连 SDR++ 来源也没有 | **未核实** | 需真机逐档读回 | 把未核实做成产品承诺；最坏表现为设了档位但驱动拒绝/丢样 |

附带风险（C 与 B 都踩）：`cpp/src/ui/main_window.cpp` ADS-B 分支 `if (srCombo_->currentIndex() == 0) srCombo_->setCurrentIndex(2)` 是**按索引写死**，注释却写“slowest rate → 2.4 MS/s、≥2.048 MS/s 即可”。候选表升序（0=250k、1=1.024M、2=1.536M），所以现在：① 处于 **250k** 时切 ADS-B 落到 **1.536M（低于 2.048M）**；② 处于 **1.024M（index 1）** 时**根本不触发跳档**（旧逻辑只认 index 0，旧表 index 0 恰是 1.024M，新增 250k 后语义漂移）。**已核实（读码）**，属存量缺陷，两方案落地前都必须改成按值查找（找 `>= 2.048M` 的最近档），不能靠索引。

### C.6 锁测试口径：跨端“集合相等”已废止，改为“可见 ⊆ 后端可接受”

**裁定**：档位由后端真实能力决定，前端不许有自己的常量表。因此不再要求桌面/C++/移动端集合相等（它们各自服务不同后端），改为**对每个后端**断言：

> 界面可见档位 ⊆ 该后端可接受档位集合（`tests/test_sample_rate_options.py::assert_visible_subset`）。

实现落点：`mbdsdr_ai/device_capabilities.py:sample_rate_options` / `sample_rate_options_for_backend` 取后端档位表（RTL `RTLSDRBackend.SAMPLE_RATES`、HackRF `hackrf_params.SUPPORTED_SAMPLE_RATES`、Lime `limesuite_params.SUPPORTED_SAMPLE_RATES`），范围已知但无离散表的后端只给声明范围两端，范围未知返回 `None`（界面显示“未知（后端未报告采样率范围）”，不编档位）。`desktop/control_panel.py:sample_rate_combo` 改为 `_CapabilitySampleRateCombo`，每次展开前按活跃后端刷新；`SAMPLE_RATES` 常量已删除。

**旧口径实跑（正是废止它的证据；集合相等在两处本来就红）：**

```
=== 解析结果 ===
desktop/control_panel SAMPLE_RATES      : 11 档 {0.25M, 1.024M, 1.536M, 1.792M, 1.92M, 2.048M, 2.16M, 2.4M, 2.56M, 2.88M, 3.2M}
cpp tokens::kSampleRatesHz             : 4 档 {1.024M, 2.048M, 2.4M, 3.2M}
cpp device_capabilities 设备下拉候选     : 10 档 {0.25M, 1.024M, 1.536M, 1.792M, 1.92M, 2.048M, 2.4M, 2.56M, 2.8M, 3.2M}
cpp settings_dialog 首选采样率           : 4 档 {1.024M, 2.048M, 2.4M, 3.2M}
mobile AppTokens.sampleRatesHz          : 4 档 {1.024M, 2.048M, 2.4M, 3.2M}

[RED  ] 桌面 11 档 == tokens 4 档: 11 vs 4
        左独有={0.25M, 1.536M, 1.792M, 1.92M, 2.16M, 2.56M, 2.88M}  右独有={}
[RED  ] 桌面 11 档 == C++ 设备下拉候选 10 档: 11 vs 10
        左独有={2.16M, 2.88M}  右独有={2.8M}
[GREEN] C++ 首选采样率 4 档 == tokens 4 档: 4 vs 4
[GREEN] 移动端 4 档 == tokens 4 档: 4 vs 4

=== 反证 1：临时往 tokens 加回一档 1.536M，必须红 ===
[RED  ] 桌面 11 档 == tokens+1536k 5 档: 11 vs 5
        左独有={0.25M, 1.792M, 1.92M, 2.16M, 2.56M, 2.88M}  右独有={}

=== 反证 2：临时往桌面下拉加一档 1.400M，必须红 ===
[RED  ] 桌面+1.4M 12 档 == C++ 设备下拉候选 10 档: 12 vs 10
        左独有={1.4M, 2.16M, 2.88M}  右独有={2.8M}
```

说明：上述输出是 `dd72ba1c` **之前**的实跑（当时设备候选表 10 档、含虚构档 2.8M）；该组差异已于 2026-11-06 清零（取证 `dd72ba1c` — `cpp/src/dsp/device_capabilities.cpp:43` — `2160000.0`；候选表 11 档与桌面 0 差，见 C.1/C.9）。反证两条都红，且失败信息给出独有档位——**临时加回一档不会被绿吞掉**。脚本是仓外一次性探针（`d:\mbdsdr\param_gov_probe.py`），跑完即删，不进仓库。

### C.7 7 档在 C++ 内核下的逐档结论（2026-11-05 记录；现状见 C.9）

先给路径事实（**以下三条为 `dd72ba1c` 前的状态**；2026-11-06 已落地（取证 `dd72ba1c` — `cpp/src/control/control_hub.cpp:510` — `采样率不在能力档内`），现状见 C.9 的「三条生效路径」表）：
- **AI 工具路径**：`cpp/src/ai/tool_schema.cpp` 枚举 + `cpp/src/ai/agent_tools.cpp:execSetSampleRate` 用 `tokens::kSampleRatesHz` 逐个比较，不在表内直接返回错误文本“采样率不在能力档内（1024000/2048000/2400000/3200000），收到 %1——未生效”。
- **控制面路径（`dd72ba1c` 前）**：`cpp/src/control/control_hub.cpp:cmdSetSampleRate` 只校验“是数字”，**不校验档位表**，直接 `engine_->onSetSampleRate(sr)`；**现状**：按 `tokens::kSampleRatesHz` 逐档比对、档外显式拒绝（取证 `dd72ba1c` — `cpp/src/control/control_hub.cpp:510` — `采样率不在能力档内`）。
- **RTL 落地（`dd72ba1c` 前）**：`cpp/src/dsp/rtl_sdr_source.cpp:setSampleRate` 调 `rtlsdr_set_sample_rate` 后**不检查返回值、也没有 `rtlsdr_get_sample_rate` 读回**（`cpp/src/dsp/rtl_sdr_ops.h` 无该函数指针）；引擎按请求值下发。**现状**：`cpp/src/dsp/rtl_sdr_source.cpp:pushSampleRate` 检查返回码、`cpp/src/dsp/rtl_sdr_ops.h:getSampleRate` 接 `rtlsdr_get_sample_rate` 读回，不一致同时报请求/读回两值（取证 `dd72ba1c` — `cpp/src/dsp/rtl_sdr_source.cpp:505` — `sampleRateRc_`；`cpp/src/dsp/rtl_sdr_source.cpp:522-523` — `getSampleRate`）。
- **实时下拉（`dd72ba1c` 前）**：`cpp/src/ui/main_window.cpp` 用 `dsp::buildSampleRateOptions` 从 `kCandidateRatesHz`（0.25/1.024/1.536/1.792/1.92/2.048/2.4/2.56/**2.8**/3.2M）按设备量程过滤；设置对话框 `cpp/src/ui/settings_dialog.cpp:srCombo_` 仍是 4 档。**现状**：候选表 11 档（补 2.16M、2.8M→2.88M，取证 `dd72ba1c` — `cpp/src/dsp/device_capabilities.cpp:43` — `2160000.0`），设置对话框仍 4 档（未改）。

| 档位 | 后果类型（C++，**2026-11-05 改前**） | AI 工具 | control_hub 下发（改前） | 实时设备下拉（改前） | 设置对话框 |
|---|---|---|---|---|---|
| 250k | **静默接受**（DSP 按请求值；硬件实际未读回） | 拒（不在 4 档） | 接受 | 有 | 无 |
| 1536k | **静默接受**（同上） | 拒 | 接受 | 有 | 无 |
| 1792k | **静默接受**（同上） | 拒 | 接受 | 有 | 无 |
| 1920k | **静默接受**（同上） | 拒 | 接受 | 有 | 无 |
| 2160k | **无 UI 入口 + 静默接受（仅 API）**：候选表没有 2.16M | 拒 | 接受 | **无** | 无 |
| 2560k | **静默接受**（同上） | 拒 | 接受 | 有 | 无 |
| 2880k | **无 UI 入口 + 静默接受（仅 API）**：候选表是 2.8M，不是 2.88M | 拒 | 接受 | **无** | 无 |

“被夹到最近档”在这 7 档上**不发生**：Python `nearest_sample_rate` 只在值不在表内时吸附，7 档都在 `RTLSDRBackend.SAMPLE_RATES` 里；C++ 没有吸附逻辑（RTL 驱动自身的吸附由 `rtlsdr_get_sample_rate` 抓出）。**改前分布**：AI 一律拒（7/7）；控制面一律静默接受（7/7）；UI 可见 5/7（2160、2880 无入口）；硬件实际采样率没有任何一侧读回。**现状（2026-11-06，`dd72ba1c`）**：AI 拒（7/7，4 档白名单）；控制面**档外拒绝**（7/7，同 4 档白名单，`cpp/src/control/control_hub.cpp:cmdSetSampleRate`）；实时下拉 7/7 有入口（候选表 11 档）；读回可用，读回≠请求同时报两值（`cpp/src/dsp/rtl_sdr_source.cpp:pushSampleRate`）。

### C.8 改前/改后可见档位矩阵（后端 × 可见集合 × 实际生效值）

| 后端 | 改前可见（11 档前端常量） | 改后可见（后端能力） | 选中后实际生效值 | 证据状态 |
|---|---|---|---|---|
| 桌面 RTL（`RTLSDRBackend`） | 11 档：250k/1.024/1.536/1.792/1.92/2.048/2.16/2.4/2.56/2.88/3.2M | 同 11 档（来自 `RTLSDRBackend.SAMPLE_RATES`，非前端副本） | `status.sample_rate_hz` 记录**实际生效值**（`_resolve_applied_sample_rate`：11 档最近邻吸附 + 合法性钳位）；RTL 读回走 `rtlsdr_get_sample_rate` | 档位表与读回路径已核实；真机 12 档实测（11 一致 + 1 吸附，E3，工件未入库、不可复跑） |
| 桌面 HackRF（Soapy 探测范围） | 11 档 RTL 表（**错误：HackRF 不一定接受**） | `hackrf_params.SUPPORTED_SAMPLE_RATES` ∩ 探测范围 | Soapy 驱动返回值（适配器按驱动读回） | 探测范围来自 `getSampleRateRange`；真机未核实 |
| 桌面 Lime（Soapy） | 11 档 RTL 表 | `limesuite_params.SUPPORTED_SAMPLE_RATES` ∩ 范围 | 同上 | 表已核实；真机未核实 |
| 桌面 rtl_tcp | 11 档 | 11 档（RTL 声明区间，协议无查询） | **对端决定**，本端不读回 | 声明已核实；对端未核实 |
| 桌面 未知范围后端 | 11 档（**假档位**） | **无档位表**，下拉显示“未知（后端未报告采样率范围）”并禁用 | 沿用后端默认，状态栏可见 | 已实现 |
| C++ 实时设备下拉 | 10 档候选（2.8M 而非 2.88M；**2026-11-06 已为 11 档**，见 C.1/C.9） | 11 档（同 `kCandidateRatesHz`，按能力过滤） | 驱动读回（`rtlsdr_get_sample_rate`），不一致同时报请求/读回两值 | 已核实（读码 + 真机 12 档） |
| C++ 设置对话框 | 4 档 | 4 档（**剩余：仍是自写常量表**） | 引擎请求值 | 已核实（未改） |
| C++ AI 工具 | 4 档 | 4 档 | 拒档外值 | 已核实 |
| 移动端 | 4 档（`AppTokens.sampleRatesHz`，死表） | 单项「未知」并禁用（**未解析 `sample_rate_options_hz`**，C.12） | 服务端返回值 | 消费侧已改（取证 `754f64e6` — `mobile/lib/pages/spectrum_page.dart:761` — `绝不用 AppTokens.sampleRatesHz`）；字段未解析（C.12） |

**本轮已落/未落**：
- 已落：`mbdsdr_ai/device_capabilities.py` 新增能力取档；`desktop/control_panel.py` 删除 `SAMPLE_RATES` 常量，改为下拉展开时按活跃后端刷新；新增 `tests/test_sample_rate_options.py`（7 条）。
- 未落（有明确阻塞）：~~`desktop/main_window.py` 的连接对话框 `sr_combo`~~（**2026-11-06 已落地**：取证 `70429a02` — `desktop/main_window.py:8186` — `该采样率未被设备接受（保持`（基准 `70429a02`），见 C.10.1）；移动端 `AppTokens.sampleRatesHz`（内核**已下发** `sample_rate_options_hz`，Dart 未解析，见 C.12）；C++ 设置对话框 4 档表（未改，属内核侧，不在本轮前端收口范围）。
- 反证实跑（临时注入不可接受档后复位）：

```
FAILED tests/test_sample_rate_options.py::test_rtlsdr_visible_subset_of_backend_rates
AssertionError: 可见档位不在后端可接受集合: [1400000]
assert not [1400000]
1 failed in 0.83s
```

复位后：`python -m pytest tests/test_sample_rate_options.py tests/test_settings_schema.py -q` → **79 passed**。

### C.9 内核真值取证与 7 档逐档去向（收口轮，2026-11-05）

**内核支持集合原文**（`cpp/src/core/tokens.h:287`）：

```cpp
inline const std::initializer_list<double> kSampleRatesHz = {1.024e6, 2.048e6, 2.4e6, 3.2e6};
```

**设备候选集合原文**（`cpp/src/dsp/device_capabilities.cpp:36-48`，按设备量程过滤见同文件 `:131-135`）：

```cpp
constexpr double kCandidateRatesHz[] = {
    250000.0, 1024000.0, 1536000.0, 1792000.0, 1920000.0, 2048000.0,
    2160000.0, 2400000.0, 2560000.0, 2880000.0, 3200000.0,
};
```

**三条"生效路径"实测/读码结论（各不相同，不许合并）**：

| 路径 | 代码位置 | 档位校验 | 真实生效 |
|---|---|---|---|
| AI 工具 | `cpp/src/ai/agent_tools.cpp:3342-3354` | **有**，逐个比对 `tokens::kSampleRatesHz`；档外返回「采样率不在能力档内（1024000/2048000/2400000/3200000），收到 %1——未生效」 | 档内才 `engine->onSetSampleRate` |
| 控制面 | `cpp/src/control/control_hub.cpp:cmdSetSampleRate` | **有**（2026-11-06 补）：同样逐档比对 `tokens::kSampleRatesHz`，档外返回同口径错误串（档位表由 tokens 生成，故现在写全 4 档 + 收到值 + 未生效），不再回填请求值 | 档内才 `engine_->onSetSampleRate` |
| RTL 落地 | `cpp/src/dsp/rtl_sdr_source.cpp:pushSampleRate` | **有**（2026-11-06 补）：检查 `set_sample_rate` 返回码；rc≠0 → 保留原采样率、记 `sampleRateError_` 并由 `SpectrumEngine` 发 `sourceError`；rc=0 且驱动读回不一致 → 报「请求值/读回」两值 | 读回：`rtl_sdr_ops.h:getSampleRate`（`rtlsdr_get_sample_rate`）真实可用；无该入口时状态串写「采样率=请求值，硬件读回不可用」 |

**7 档逐档去向**（差集 = `{250k, 1536k, 1792k, 1920k, 2160k, 2560k, 2880k}`）：

| 档位 | 内核 tokens 4 档 | 内核候选表 | C++ AI | control_hub | C++ 实时下拉 | 桌面 Python 后端 | 处置（2026-11-06 定稿） |
|---|---|---|---|---|---|---|---|
| 250k | 无 | 有 | 拒（4 档） | **档外拒绝** | 有 | RTL 合法（librtlsdr `225001…300000`）；HackRF 最小 2 MHz **不支持** | **内核支持、AI 故意不暴露**：仅低段 RTL 可用，AI 侧无低段专用模式/带宽预设配套；保留桌面手选 |
| 1536k | 无 | 有 | 拒（4 档） | **档外拒绝** | 有 | RTL 合法 | **内核支持、AI 故意不暴露**：AI 只暴露 4 个已验证常用档，避免把未做真机逐档读回的档位变成可下发承诺（C.9） |
| 1792k | 无 | 有 | 拒（4 档） | **档外拒绝** | 有 | RTL 合法 | 同 1536k |
| 1920k | 无 | 有 | 拒（4 档） | **档外拒绝** | 有 | RTL 合法 | 同 1536k |
| 2160k | 无 | **有**（2026-11-06 补） | 拒（4 档） | **档外拒绝** | **有** | RTL 合法（2.16 MS/s） | **内核支持、AI 故意不暴露**（同 1536k）；候选表/下拉已补，桌面可手选 |
| 2560k | 无 | 有 | 拒（4 档） | **档外拒绝** | 有 | RTL 合法 | 同 1536k |
| 2880k | 无 | **有**（2026-11-06 补；2.8M 是设备从不给出的虚构档，真机请求即吸附到 2.88M） | 拒（4 档） | **档外拒绝** | **有** | RTL 合法（2.88 MS/s） | 同 1536k；档位表只列设备实际读回值 |

结论：**7 档全部有明确去向**——5 档（250k/1536/1792/1920/2560）"内核支持、AI 故意不暴露"，理由如上（4 档已进内核候选表与实时下拉，可由桌面手选；AI 面保持 4 档已验证集）；2160/2880 已补进候选表（2.8M 按真机读回改为 2.88M），AI 面同样不暴露。没有任何一档是"被夹到最近档"，也没有一档悬空。

**测试不变式（已落，`tests/test_sample_rate_options.py`）**：

1. `可见档位 ⊆ 该后端可接受档位集合`（`assert_visible_subset`），对 RTL / HackRF / 无表连续后端 / 未知范围各跑一遍；
2. 未知范围不造表：返回 `None`，控制面板显示不可选说明项；
3. 反证：注入 `1,400,000` → `1 failed`，复位后 `79 passed`（另相关子集 `93 passed`、含文案审计三文件 `100 passed`）。
4. **已落（2026-11-06）**："每档选一次、回读值=请求值或显式拒绝" 已由 `70429a02` 接线（取证 `70429a02` — `desktop/main_window.py:8186` — `该采样率未被设备接受（保持`（基准 `70429a02`）），用例在 `tests/test_sample_rate_options.py`（逐档下发、拒绝回填且不落盘、回读≠请求报实际值）。

**已落/已定稿（2026-11-06）**：C++ 控制面档位校验（档外显式拒绝，4 档口径）已落；2160/2880 进候选表、2800→2880 统一为 2880；AI 面对 250k/1536/1792/1920/2560/2160/2880 维持不暴露（理由见上表）；RTL `set_sample_rate` 返回码检查与 `rtlsdr_get_sample_rate` 读回已落。仍剩：移动端 `AppTokens.sampleRatesHz` → 未接能力字段（改法见 C.10.3）；每档真机逐档读回证据仍缺（C.9）。

### C.10 逐档回读实测与剩余两项（2026-11-05 第二轮）

**C.10.1 `main_window` 接线已落**（`ux-basis` 已释放，工作区干净后才动）：

| 位置 | 改动 |
|---|---|
| 连接对话框 `sr_combo` | 删除 `RTLSDRBackend.SAMPLE_RATES` 直灌，改 `_fill_sr_combo(device)`：有量程走 `sample_rate_options(device)`，无量程给单项「未知（…将用默认 2.400 MS/s）」且 `data=None`；`_update_param_state()` 内随设备切换重建 |
| `_on_sample_rate_changed`（非录音） | 检查 `set_sample_rate` 返回值：`False` → 状态栏「该采样率未被设备接受（保持 X）」并回填下拉、**不持久化**；回读≠请求 → 「设备按 Y 生效（请求 X）」并回填；相等才继续重建流水线 |
| `_on_sample_rate_changed`（录音中） | 同样检查返回值（`False` 抛入原有回滚分支）；段头与 `sample_rate_hz` 持久化改用**回读值** |
| `mbdsdr_ai/sdr_backend.py` | 新增 `_resolve_applied_sample_rate()` 钩子，`status.sample_rate_hz` 记**实际生效值**；RTL 覆写为「11 档最近邻吸附 + 合法性钳位」 |

**C.10.2 逐档实测（RTL + `tests/fake_rtl.py`，脚本 `D:\mbdsdr\.tmp\measure_sample_rate.py`，不进仓）**

内核 4 档 + 候选 11 档 + 桌面可见 11 档（并集 11 档），另加档外探针 2.800M 共 12 个探测点，逐档 `set_sample_rate` → 读回 → 读假棒硬件侧实收值：

| 请求 Hz | 返回 | get_sample_rate | 硬件侧实收 | 判定 |
|---|---|---|---|---|
| 250,000 | True | 250,000 | 250,000 | 一致 |
| 1,024,000 | True | 1,024,000 | 1,024,000 | 一致（内核档） |
| 1,536,000 | True | 1,536,000 | 1,536,000 | 一致 |
| 1,792,000 | True | 1,792,000 | 1,792,000 | 一致 |
| 1,920,000 | True | 1,920,000 | 1,920,000 | 一致 |
| 2,048,000 | True | 2,048,000 | 2,048,000 | 一致（内核档） |
| 2,160,000 | True | 2,160,000 | 2,160,000 | 一致 |
| 2,400,000 | True | 2,400,000 | 2,400,000 | 一致（内核档） |
| 2,560,000 | True | 2,560,000 | 2,560,000 | 一致 |
| 2,800,000 | True | **2,880,000** | **2,880,000** | **被吸附（改前回读谎报 2,800,000）** |
| 2,880,000 | True | 2,880,000 | 2,880,000 | 一致 |
| 3,200,000 | True | 3,200,000 | 3,200,000 | 一致（内核档） |

结论：12 档里 **11 档请求=回读=硬件实收**，1 档（2.800M，非任何一端可见档）被吸附到 2.880M，改后回读如实报 2.880M 并由界面提示「请求 2.800 MS/s → 实际 2.880 MS/s」。改前该档回读是 2.800M，属"静默接受后 DSP 按别的率跑"的 Python 侧实例——本轮已修（取证 `70429a02` — `desktop/main_window.py:8186` — `该采样率未被设备接受（保持`（基准 `70429a02`）；测试 `70429a02` — `tests/test_sample_rate_options.py:222` — `未被设备接受`）。

**新增测试**（`tests/test_sample_rate_options.py`，共 12 条）：11 档逐档回读一致 + 硬件侧一致；档外 2.800M 回读=2.880M；未连接返回 `False`；主窗口槽逐档下发后 `_record_sr`/持久化均为生效值；后端拒绝时 `_record_sr` 不变、下拉回填、状态栏含「未被设备接受」、**不写配置**。实测 `12 passed`（8.77s）。

**C.10.3 移动端改法（只出方案，本轮未改 Dart）**

移动端走 rtl_tcp 私有协议（`mobile/lib/services/rtl_tcp_client.dart:171`），协议本身没有"查询档位"命令，因此能力必须先由服务端下发：

| 改动文件 | 端 | 内容 | 状态 |
|---|---|---|---|
| 取证：`f0a19376` — `cpp/src/control/control_hub.cpp:1083` — `sample_rate_options_hz`；`cpp/src/control/control_hub.cpp`（`cmdGetCapabilities`） | 桌面内核 | 增加 `sample_rate_options_hz: [..]` 与 `sample_rate_options_status`（实际取值 `no_device`/`range_unknown`/`no_tier_in_range`/`ok`，比原方案多一档「有量程但无候选档」） | 已落地（本轮） |
| `docs/protocol*.md`（控制面消息表） | 协议 | 登记上述两个可选字段；旧客户端忽略即可 | 待登记（grep `sample_rate_options_hz` 在 `docs/` 只命中本账本） |
| `mobile/lib/services/control_hub_client.dart` | 移动 | **新增** `fetchCapabilities()`（现有只有 `fetchStatus`/`fetchPocsagMessages`/`fetchM17Calls`/`fetchVorRadial`，无 capabilities 请求），解析 `sample_rate_options_hz` / `sample_rate_options_status`，缺省 `unknown` | 待移动端实现（C.12） |
| `mobile/lib/services/radio_controller.dart:233/323/819` | 移动 | `_sampleRateOptionsHz`（`:233` 声明、`:323` getter、`:819` 读取，**当前全文件无赋值**）→ 在 capabilities 成功回调里赋值；`setSampleRateHz` 校验并抛明确错误 | 待移动端实现（C.12） |
| `mobile/lib/pages/spectrum_page.dart:762-770` | 移动 | 下拉项由 `sampleRateOptionsHz` 生成；未知则禁用并显示「未知」（现状已是这样，缺的是上游赋值） | 待移动端实现（C.12） |
| `mobile/lib/widgets/device_info_card.dart:104` | 移动 | 文案不改（仍是实际值），仅注释来源 | 待移动端实现（C.12） |
| `mobile/lib/app/tokens.dart:216-227` | 移动 | 删除 `AppTokens.sampleRatesHz`（已 `@Deprecated`、无人读取） | 待移动端实现（C.12） |

影响端：桌面（新增字段，纯加性）、移动（解析+下拉+校验）、rtl_tcp 直连路径（协议无能力查询 → 只能是 unknown，不得造假档位）。

**组 4 状态维持「部分统一」**：桌面两处已能力化；内核侧四项见 C.9/C.11（档位面 `cpp/src/control/control_hub.cpp:1083` — `sample_rate_options_hz`）；剩移动端一项（内核字段已下发，Dart 未解析）与协议文档登记（C.10.3）。

### C.11 组 4 三项剩余逐条状态（2026-11-06）

| # | 项 | 状态 | 证据 / 阻塞 | 提交 |
|---|---|---|---|---|
| 1（取证 `7bea149a` — `desktop/main_window.py:73` — `fill_sample_rate_combo`（基准 `a2be554a`）） | 连接对话框 `sr_combo` 能力化 + 逐档回读/显式拒绝 | **已落地** | 删除 11 档直灌 → `fill_sample_rate_combo(device)` 按设备能力生成；`_on_sample_rate_changed` 检查 `set_sample_rate` 返回值与回读值：`False` → 状态栏「未被设备接受」+ 回填下拉 + 不落盘；回读≠请求 → 报实际值；`sdr_backend._resolve_applied_sample_rate` 让 `status.sample_rate_hz` 记生效值 | `70429a02` |
| 2 | 移动端 / 桌面档位来源 | **内核侧已落地（移动端待接线）** | **核对现状**（2026-11-06；取证 `f0a19376` — `cpp/src/control/control_hub.cpp:1083` — `sample_rate_options_hz`）：① 档位面：`cmdGetCapabilities` **已调用** `dsp::buildSampleRateOptions(caps)`（`cpp/src/control/control_hub.cpp:1080`），输出 `sample_rate_options_hz`（`:1083`）与 `sample_rate_options_status`（`:1084-1085`，取值 `no_device`/`range_unknown`/`no_tier_in_range`/`ok`，空数组绝不冒充「零档位」）；量程字段仍为 `sample_rate_min_hz`/`sample_rate_max_hz`（`:1075-1076`）。② 回读面：`cmdGetStatus` 报回读 `readback_sample_rate_hz`（`:1016`）与 `sample_rate_readback_available`/`sample_rate_status`/`sample_rate_error`（`:1024-1027`，取证 `dd72ba1c` — `cpp/src/control/control_hub.cpp:1024` — `sample_rate_readback_available`）。**真值口径**：「实际生效值」的真值 = 设备回读（已上报）；「哪些档可选」的真值 = `sample_rate_options_hz`（已上报，按设备量程过滤）。桌面 `RTLSDRBackend.SAMPLE_RATES` 11 档与 C++ `kCandidateRatesHz` 11 档是同源静态拷贝、不随设备变化；RTL 合法区间 225001–3200000 覆盖全部 11 档，故对 RTL **当前无差**，差只出现在「连接前（桌面已列、设备候选为空）」与非 RTL / 量程受限设备。Dart 消费侧已改：档位缺失即「未知」并禁用、`ai_tools` 未报告即失败、`AppTokens.sampleRatesHz` 标 `@Deprecated`，但尚未解析新字段（未接线） | `754f64e6`（Dart 消费侧）；`f0a19376`（档位列表已下发） |
| 3 | 逐档回读端到端证据 | **已落地（真机 + 注入双证）**（取证 `754f64e6` — `scripts/sample_rate_readback_check.py:156` — `真机不可复跑，以下为注入验证`） | 新增 `scripts/sample_rate_readback_check.py`：逐档 `set_sample_rate` → 读回 → 判「一致/不一致/被拒」。真机首轮 `LibUSBError LIBUSB_ERROR_IO (-1)` 不可用；复跑成功 12 档：11 一致 + **1 不一致（2.800M→2.880M，+80,000 Hz，硬件/驱动实际吸附被检查抓到）**。另注入三态自证：honest 12/12 一致、clamp 同上一致/不一致分布、reject 11 一致 + 1 被拒（2.800M 拒绝后保持上一档 2.560M）；注入模式会打印「真机不可复跑，以下为注入验证」 | `754f64e6` |

- 2026-11-11 命名统一后更新：第 1 项四元组按现名 `fill_sample_rate_combo` 重写，证据取改名提交 `7bea149a`（同时删 `_fill_sr_combo`、加 `fill_sample_rate_combo`）。该行后来被改过签名（现 blame `a2be554a`），按 §7.7 基准取该行 blame；行号随并行提交漂移时同步重定位（本次 `:53 → :55`）。

**结论维持「部分统一」**：第 2 项内核侧已补（取证 `f0a19376` — `cpp/src/control/control_hub.cpp:1083` — `sample_rate_options_hz`；回读面取证 `dd72ba1c` — `cpp/src/control/control_hub.cpp:1024` — `sample_rate_readback_available`）：`cmdGetCapabilities` 现已调用 `dsp::buildSampleRateOptions(caps)`（`cpp/src/control/control_hub.cpp:1080`），输出 `sample_rate_options_hz`（`:1083`）与 `sample_rate_options_status`（`:1084-1085`，取值 `no_device` / `range_unknown` / `no_tier_in_range` / `ok`，即空数组绝不冒充「零档位」）；`cmdGetStatus` 的 `readback_sample_rate_hz` / `sample_rate_readback_available` / `sample_rate_status` / `sample_rate_error` 不变。第 1、3 项已落地（`70429a02` — `desktop/main_window.py:8186` — `该采样率未被设备接受（保持`（基准 `70429a02`）；`754f64e6` — `scripts/sample_rate_readback_check.py:156` — `真机不可复跑，以下为注入验证`；第 3 项真机复跑 12 档 11 一致 + 1 吸附被抓到）。**移动端仍未接线**（Dart 消费侧按「字段缺失 → 未知并禁用」写着，未真正读新字段），故不写「统一（收敛态）」。本机无 Flutter 工具链，Dart 改动未跑 `flutter analyze` / `flutter test`，未经验证。另 C++ 侧四项（AI 4 档接线、2160/2880 拒绝路径、2800 vs 2880、硬件读回）已由 `dd72ba1c` 落地；上面「回读可观测」即来自该笔。

**三份档位表的用途分工（不是矛盾，供重复治理线登记时分开写）**

| 表 | 档数 | 用途 | 谁读 |
|---|---|---|---|
| `cpp/src/core/tokens.h:kSampleRatesHz` | 4 | **可下发白名单**：档外显式拒绝 | `cpp/src/ai/agent_tools.cpp:execSetSampleRate`；控制面同口径校验已落（`dd72ba1c`，C.9） |
| `cpp/src/dsp/device_capabilities.cpp:kCandidateRatesHz` | 11 | **设备候选探测表**：按 librtlsdr 合法区间过滤后供下拉手选，不是下发白名单 | C++ 设备下拉（`device_capabilities`） |
| `mbdsdr_ai/sdr_backend.py:RTLSDRBackend.SAMPLE_RATES` | 11 | 桌面后端能力表：控制面板/连接对话框的可见档位来源 | `desktop/control_panel.py`、`desktop/main_window.py`、`mbdsdr_ai/device_capabilities.py` |
| 移动端档位表 | 0（已删） | 改读控制面 `sample_rate_options_hz`，缺失即「未知」并禁用 | 字段已下发（本轮，C.11 第 2 项内核侧）；移动端未解析（待办见 C.12） |

4 档 ≠ 11 档不是「同一物理量两套数」：前者是**允许 AI/控制面下发的正向列表**，后者是**可让用户手选的候选集合**，关系是「白名单 ⊆ 候选」，不是「应相等」。重复治理线登记时请按这两类用途分开，勿合并成一条矛盾。

**证据级别（供 `limitations-register` 附 M.4 L60 引用；本文件不改那份文档）**

- 工件：`scripts/sample_rate_readback_check.py`（已入库，`754f64e6`）；命令 `python scripts/sample_rate_readback_check.py --mode real`（`--mode honest|clamp|reject` 为注入）。
- 真机 12 档 `set→get`：时点 **2026-11-06 01:05–01:10**（结果入账 `7898c689`）。原始 stdout 当时落 `D:\mbdsdr\.tmp\rb_real.txt`，按临时文件当删纪律**已删除、未入库**（无 SHA256）→ **E3 不可复跑**（按 `docs/evidence-standard.md`：只能作历史线索，引用须带「当时有证据、现不可复跑」）。
- E3 的当前不可复跑原因：首轮 `LIBUSB_ERROR_IO (-1)`、复跑 `LIBUSB_ERROR_ACCESS (-3)`（设备被占用/权限不足）。恢复到 E2 需：释放 RTL 独占 + USB 访问权限，并把 stdout 存 `.log` 入库并钉 SHA256。
- 注入三态（`--mode honest|clamp|reject`）：**E2 可复跑**（无需硬件，确定性）。期望：honest 12 一致；clamp 11 一致 + 1 不一致（2.800M→2.880M，+80,000 Hz，退出码 1）；reject 11 一致 + 1 被拒（保持 2.560M）。
- 数字口径：「12 档 11 一致 + 1 不一致」当前只能按 E3 引用；「clamp 必红」按 E2 引用。


### C.12 待办：移动端解析 `sample_rate_options_hz`（内核已下发 / 客户端未解析）

**这是一条独立待办，不是组 4 状态列的脚注。** 组 4 现处「部分统一」，唯一未收口项即本条。

| 项 | 内容 |
|---|---|
| 现状（2026-11-06 已核实） | **内核已下发**：`cmdGetCapabilities` 调用 `dsp::buildSampleRateOptions(caps)`（`cpp/src/control/control_hub.cpp:1080`），输出 `sample_rate_options_hz`（`:1083`）与 `sample_rate_options_status`（`:1084-1085`，取证 `f0a19376` — `cpp/src/control/control_hub.cpp:1083` — `sample_rate_options_hz`）。**客户端未解析**：`mobile/lib/services/radio_controller.dart:_sampleRateOptionsHz` 只在声明（`:233`）、getter（`:323`）、页面读取（`:819`）出现，**全文件无赋值语句**，故 `spectrum_page.dart:762-770` 恒走「未知（设备未报告采样率范围）」并禁用下拉；`AppTokens.sampleRatesHz` 虽已 `@Deprecated`（`mobile/lib/app/tokens.dart:216-227`），死表仍在（无人读取；`spectrum_page` 已无 `.first` 兜底，越集值 `value=null`） |
| 责任线 | 移动端线（`mobile/`） |
| 完成判据 | 见下方「C.12 验收（可执行）」5 条，每条给出读哪一行、期望值、无 Flutter 工具链时的静态验证命令 |
| 阻塞 / 未验证 | 本机无 Flutter 工具链，Dart 改动未跑 `flutter analyze` / `flutter test`；完成前组 4 不得写「统一（收敛态）」 |

#### C.12 验收（可执行）

| # | 读哪一行 / 改哪一处 | 期望 | 无 Flutter 时的静态验证 |
|---|---|---|---|
| 1 | `mobile/lib/services/control_hub_client.dart` 新增 `Future<SampleRateCapability?> fetchCapabilities()`，请求 `get_capabilities`，读 `result['sample_rate_options_hz']`（`List<dynamic>`→`List<double>?`）与 `result['sample_rate_options_status']`（`String`） | 两个字段都有读取；不把缺失当空数组 | `Select-String -Path mobile/lib/services/control_hub_client.dart -Pattern "sample_rate_options_hz"` 与 `"sample_rate_options_status"` 各 ≥1 命中；`-Pattern "sampleRatesHz"` 0 命中 |
| 2 | `mobile/lib/services/radio_controller.dart:233` 的 `_sampleRateOptionsHz` 在 capabilities 成功回调里被**赋值**（`_sampleRateOptionsHz = caps.optionsHz;`），`:323` getter 原样返回 | 全文件至少 1 处赋值（当前 0 处） | `Select-String -Path mobile/lib/services/radio_controller.dart -Pattern "_sampleRateOptionsHz\s*="` ≥1；`-Pattern "get sampleRateOptionsHz => _sampleRateOptionsHz"` = 1 |
| 3 | `mobile/lib/pages/spectrum_page.dart:762-770` 分派：`status == 'ok'` 且数组非空 → 生成下拉项；`no_device` / `range_unknown` / `no_tier_in_range` / `unknown` → `null` 或空，走 `options == null \|\| options.isEmpty` 分支（文案「未知（设备未报告采样率范围）」+ `onChanged: null`） | 四态字符串都在源码里；`ok` 且空数组**也**不得当作「零档位」可用 | `Select-String -Path mobile/lib -Pattern "no_device\|range_unknown\|no_tier_in_range" -Recurse` ≥3 命中；`Select-String -Path mobile/lib/pages/spectrum_page.dart -Pattern "options == null \|\| options.isEmpty"` = 1 |
| 4 | `mobile/lib/app/tokens.dart:216-227` 删除 `AppTokens.sampleRatesHz` 整块 | 死表归零 | `Select-String -Path mobile/lib -Pattern "sampleRatesHz" -Recurse` **0 命中** |
| 5 | 新增 `mobile/test/sample_rate_options_test.dart`：`ok` 数组逐项等于 `[250000, 1024000, 1536000, 1792000, 1920000, 2048000, 2160000, 2400000, 2560000, 2880000, 3200000]`（11 项、升序），另断言三态各 1 例 | 11 项逐项相等；2.8M 不在数组内 | 无 Flutter 时：用 Python 从该 Dart 文件抽出数组字面量，与 `cpp/src/dsp/device_capabilities.cpp:kCandidateRatesHz` 逐项比对（复用 `tests/test_sample_rate_single_source.py:_cxx_tiers` 的解析）；有 Flutter 时：`flutter test mobile/test/sample_rate_options_test.dart` + `flutter analyze` |

## 变更记录

> **归属注记（2026-11-05）**：`ba238bc5` 中约 78 行（附录 D.1–D.5，见下方
> `### D.1` 起及其变更记录行）**实质作者是审阅线**，不是参数治理线。
> 成因：审阅线对该文件的 claim 心跳停跳后被判 `expired_steal`，参数治理线在
> 未检查工作区的情况下接管并整文件提交，把对方未提交内容并入本笔。
> **内容未作任何改动**；按「不改历史」规则未回滚、未改写提交，仅在此加注记。
> 后续归属以此注记为准。

> 证据等级 E4：本段数字未验证，引用前须复核（判据见 docs/evidence-standard.md）。

| 日期 | 基线 | 变更 | 操作人 |
|---|---|---|---|
| 2026-11-05 | `5ece441` | 建账：11 组逐条落表，状态按当时实测填写 | 参数治理线 |
| 2026-11-05 | `605e89d` | 附录 A：35 个暴露项消费点自检（全部有消费点） | 设置线 |
| 2026-11-05 | `3c4b7ad` | 复核附录 A（34/35 有消费点，红表 1 项 `pass_window_hours`；改 #24/#25）；更新组 2/4/6/8/9/10 状态；新增附录 B（8 组跨端差值实测） | 参数治理线 |
| 2026-11-05 | `16e9917` | `pass_window_hours` 定为接线不删键（#25 改“待接线”，派卫星线补两处消费点）；复验静噪四拦点全落地（`0d6d465`+`e501555`），组 1 状态改为“统一（收敛态）”；B6 写明唯一事实源与改哪一侧 | 参数治理线 |
| 2026-11-05 | `886244d` | #25 接线完成：两处过境列表读 `effective_pass_window_hours`（删 `PASS_WIN_HOURS`）；实测 6 h vs 24 h = **1 条 vs 5 条**，1 h = 0 条；A-1 红表清零（35/35 有消费点）；B4 补步长实测数字；另 `4faddb2` 修正 `_gmst_rad` 漏项（AOS 由偏 **16110 s** 回到 **+29.8 s**） | 卫星过境线 |
| 2026-11-05 | `33dacd3` | 组 4 追加附录 C 档位方案提案（11 vs 4 逐档对照、A/B/C 三方案代价、旧配置就近回落与 FFT 同规则）；组 4 状态加“方案提案见附录 C（待裁）” | 参数治理线 |
| 2026-11-05 | `749e390` | 附录 C 定稿：裁定采纳 C（5 档）否决 A；查清 C++ 内部 3 套数（tokens 4 / 设备下拉 10 / 设置首选 4）与 2.800M vs 2.880M 差 80,000 Hz；5 档回落表（含 2.800M 平局取低 400,000）；同步面 23 处；B 代价表；锁测试口径与反证输出 | 参数治理线 |
| 2026-11-05 | 本笔 | 附录 D：`2095 / 3804 / 1787` 作废（口径不明，不得引用）；现行口径 1303 / 2914；三栏归因 + 宽通道开关实验 | 审阅线 |
| 2026-11-05 | 本笔 | 组 4 改为能力驱动（裁定 B）：删 `control_panel.SAMPLE_RATES` 前端常量；新增 `device_capabilities.sample_rate_options/_for_backend`（RTL/HackRF/Lime 档位表 ∩ 范围；无表只给范围两端；范围未知返回 `None`，界面显示“未知…后端未报告采样率范围”）；`tests/test_sample_rate_options.py` 7 条（可见 ⊆ 后端可接受）+ 反证注入 1,400,000 → `1 failed`、复位 `79 passed`；C.6 跨端“集合相等”废止，改“可见 ⊆ 可接受”；C.7 7 档逐档后果（AI 拒 7/7、控制面静默接受 7/7、UI 可见 5/7、无“被夹到最近档”）；C.8 改前/改后矩阵；文案审计 U-03 源文本钉扎（`format_sample_rate(_rate)`）保持原样未放宽；相关桌面子集复跑 93 passed（另 `test_copy_units_audit` 等三文件 100 passed） | 参数治理线 |
| 2026-11-05 | 本笔 | C.9 收口取证：`tokens.h:287` 内核 4 档原文（1.024/2.048/2.4/3.2 MS/s）、`device_capabilities.cpp:36-46` 候选 10 档原文、三条路径分写（AI 有档位校验并明确拒；`control_hub.cpp:494-502` 无校验、静默接受；`rtl_sdr_source.cpp:445-450` 不检查返回值、无硬件读回）；7 档逐档去向：5 档"支持但 AI 未接线"、2 档（2160/2880）内核不支持；组 4 状态仍为部分统一（不能写"统一（收敛态）"：连接对话框/移动端/回落提示未收口）；派单内核线与 ux-basis | 参数治理线 |
| 2026-11-05 | 本笔 | 附录 D 追补 D.1–D.5：关 4:1 max-pool 同 2048 栅格复跑（py_only 1283 / cpp_only 2796；C++ 三栏 75.1/8.0/16.9）；原 704 仅解释 60（8.5%），判定 max-pool 非主因；Python 门限 6→3 dB 再解释 33/85；C++ 关 NMS、门限 3 dB 两次消融可归因 0（top-12/跟踪位移）；最终 559（79.4% of 704）/ 440（15.7% of 2796），机制未证实 | 审阅线 |
| 2026-11-05 | 本笔 | 附录 D：D.1–D.5 归属注记（实质作者审阅线，claim 心跳超时后被并入 `ba238bc5`）；新增 D.6 定义对拍 8 条（文件:行 + 原文）、D.7 30 条单条判定：本地极大子定义 **4/30=13.3%**，完整定义链 **30/30=100%**；同栅格 2796 口径未归因 473 改判「定义差异（宽通道合并 + 跟踪保持）」、该栏无事件；原 704 口径 **559（79.4%）仍挂账**（交集未映射） | 审阅线 |
| 2026-11-05 | 本笔 | C.10 收口第二轮：`ux-basis` 释放后接管 `main_window.py`（接手前 `git status` 干净）——连接对话框 `sr_combo` 改 `_fill_sr_combo(device)` 能力驱动、`_on_sample_rate_changed` 加返回值检查 + 回读比对 + 拒绝时回填且不持久化；`sdr_backend.py` 新增 `_resolve_applied_sample_rate` 钩子（RTL 覆写为吸附后值），修掉 2.800M 回读谎报请求值；逐档实测 12 档 = 11 一致 + 1 吸附（2.800M→2.880M，回读已如实）；测试 12 条（含主窗口槽逐档 + 拒绝路径），`12 passed`；C.10.3 给移动端协议字段改法（7 文件，待内核线）；组 4 仍「部分统一」 | 参数治理线 |
| 2026-11-06 | `754f64e6` | 组 4 三项收口：①`sr_combo` 能力化 + 逐档回读/显式拒绝（`70429a02`）；②移动端消费侧改「档位缺失 → 未知并禁用」，`AppTokens.sampleRatesHz` 降为历史默认（`754f64e6`，协议字段仍待内核 `sample_rate_options_hz`）；③`scripts/sample_rate_readback_check.py` 逐档回读（honest 12/12 一致、clamp 抓到 2.800M→2.880M 不一致、reject 1 被拒；真机 `LibUSBError -1` 不可复跑，为注入验证）；组 4 维持「部分统一」；另写「4 档白名单 vs 11 档候选」用途分工，供重复治理线登记 | 参数治理线 |
| 2026-11-06 | 本笔 | C.11 第 3 项补真机复跑：12 档 11 一致 + 1 不一致（2.800M→2.880M，+80,000 Hz）；首轮 LibUSBError -1 不可用已如实保留；组 4 仍「部分统一」 | 参数治理线 |
| 2026-11-06 | 本笔 | C++ 内核 F1/F2/F3 落地（审阅线）：①`cmdSetSampleRate` 按 `tokens::kSampleRatesHz` 逐档比对、档外显式拒绝（与 `agent_tools.cpp` 同口径，不再回填请求值）；②`rtl_sdr_source.cpp:pushSampleRate` 检查 `set_sample_rate` 返回码，rc≠0 保留原采样率并上报 `sourceError`，`start()` 直接失败；③`rtl_sdr_ops.h:getSampleRate` 接 `rtlsdr_get_sample_rate` 读回，不一致报请求/读回两值，无读回入口时状态串「采样率=请求值，硬件读回不可用」；`get_status` 增 `sample_rate_readback_available`/`sample_rate_status`/`sample_rate_error`；④候选表 10→11 档（补 2.160M、2.800M→2.880M，差 80,000 Hz 清零）；7 档差集全部定稿「内核支持、AI 故意不暴露」；反证测试 6 条 + Python 14 passed | 审阅线 |
| 2026-11-06 | 本笔 | 核对 control_hub 现状：只有回读（readback_sample_rate_hz / sample_rate_readback_available / sample_rate_status / sample_rate_error）与量程（sample_rate_min_hz / sample_rate_max_hz），无 sample_rate_options_hz；buildSampleRateOptions 存在但未接进 get_capabilities。组 4 第 2 项维持「阻塞」；C.11 补证据级别（真机 12 档 = E3，注入三态 = E2）。**（该结论已被 `f0a19376` 推翻：`sample_rate_options_hz` 已下发，见 C.11 第 2 项；此行仅作历史记录）** | 参数治理线 |
| 2026-11-06 | 本笔 | 修订组 4 旧口径残留：`cmdGetCapabilities` 档位面（取证 `f0a19376` — `cpp/src/control/control_hub.cpp:1083` — `sample_rate_options_hz`）——调用 `dsp::buildSampleRateOptions(caps)` 于 `cpp/src/control/control_hub.cpp:1080`，字段 `:1083`、状态 `:1084-1085`；C.11 第 2 项与结论行补 `path:line`；C.0/C.1/C.3/C.7/C.8/C.10.2 的「10 档 / 候选表 2.8M / 控制面不校验 / 无读回 / 待内核下发」按 `dd72ba1c` 后事实改写或标注「改前」；组 4 状态列改为「剩移动端接线（内核已下发，Dart 尚未解析）」 | 采样率真值线 |
| 2026-11-06 | 本笔 | 新增档位单一真值源机检 `tests/test_sample_rate_single_source.py`：C++ `kCandidateRatesHz` / Python `RTLSDRBackend.SAMPLE_RATES` / 桌面 `sample_rate_options_for_backend` / 本文 C.1 行四处逐项相等（含顺序），不等即打印两侧 11 档清单与差集；2.8M 吸附单独断言（四处均无 2.800M、请求 2,800,000 → 假棒实收 2,880,000，差 80,000，C++ 注释与 C.10.2 实测行同口径）；变异反证删 C.1 的 `**2.16**` → `1 failed`（仅 C++ 有 2160000），复位 `5 passed`。C.1 第 2 行由旧 `SAMPLE_RATES` 副本改为动态取表；C.12 新增「移动端解析 `sample_rate_options_hz`」独立待办（内核已下发 / 客户端未解析） | 采样率真值线 |
| 2026-11-06 | 本笔 | C.7 路径事实行补四元组（`dd72ba1c` — `cpp/src/control/control_hub.cpp:510` — `采样率不在能力档内`），`git show --name-only dd72ba1c` 触及该文件、`git log -S` 命中同笔；该行由「未证实（文档过期）」转为「可证实」 | 采样率真值线 |
| 2026-11-06 | 本笔 | 新增「引用口径」规则：`verify_claims` 引用须写成 `HEAD <短哈希>` + 四元组分布，HEAD 提交态与工作区在途不可混引；记 `b0a440e3`（164/58/17/89 vs 128/70/13/45）与 `d731bdeb`（140/58/12/70 vs 128/70/12/46）两组数对应关系；留存「严格版 vs 工作区放宽版逐条 0 差异」实测，放宽改动交机制线裁决 | 采样率真值线 |

## 附录 D：C++/Python 峰值独有口径（作废数 / 现行数）

> **归属注记（2026-11-05）**：本附录 D.1–D.5 的实质作者是**审阅线**。该段因审阅线
> 对本文档的 claim 心跳超时被判 `expired_steal`，被参数治理线在 `ba238bc5` 中
> 整文件提交时并入，内容未改动。按「不改历史」规则未回滚、未改写提交；
> 归属以本条及文末「变更记录」注记为准。

> **作废：`2095 / 3804 / 1787` 三个数口径不明，已作废，不得引用。**
> 这三个数在 HEAD 上无法复现，仓库内也查不到生成脚本与定义。任何文档、
> 汇报、提交说明不得再出现「C++ 独有 2095 / Python 独有 3804 / 1787 个频率
> C++ 整段没出现过」。

现行口径：2026-11-05，生产代码（Python `SpectrumDataGenerator` 512 显示 bin、
C++ `dsp::detectPeaks` 2048 bin + `rescanPeaks`），全量 256 帧 88.1 MHz，
`tests/fixtures/real_fm_88m1/capture.sigmf-data`，FFT 2048；配对 = 同帧号且
`|Δf| ≤ 500 Hz`（一个 C++ bin）。

| 指标 | 实测 |
|---|---|
| Python 显示峰（≤6/帧） | 1423 条 |
| C++ 显示峰（≤12/帧） | 3034 条 |
| Python 独有 | **1303**（91.5%） |
| C++ 独有 | **2914**（96.0%） |
| 去重频率值 | Python **181** / C++ **407** |

归因分三栏，**不得把实测与推断相加当作「已解释」**：

| 侧 | 实测归因 | 推断归因 | 未归因 |
|---|---|---|---|
| Python 独有（1303） | 1272（97.6%） | 31（2.4%，R_region：C++ 同格无 raw 候选，仅代码结构推断） | 该栏无事件（1272+31=1303） |
| C++ 独有（2914） | 1778（61.0%） | 432（14.8%，R_region 中窄带候选只落在相邻 512 格，单元边界/池化推断） | 704（24.2%，R_region 中 ±1 格内无任何窄带候选；可能原因：4:1 max-pool 抹掉 2048 局部极大、C++ NMS/门限边缘，**本轮已实测排除 max-pool 为主因，见 D.2**） |

宽通道开关实验（C++ 独有 R_region 1670 条）：关掉 Python 宽通道后，
**534 条（32.0%）在原 512 格出现窄带候选 → 转入实测**；432 条（25.9%）
仅相邻格出现候选；704 条（42.2%）同格/邻格均无候选。剩余 1136 条仍属推断/未归因，
不构成解释。

门限基准差：两端都是本帧中位数 + 6 dB + 绝对 -100 dBFS，没有滑动窗基准；
因栅格不同的中位数差实测 max **0.0294 dB** / mean **0.0042 dB**，跨不过门限，
该桶为 0。

### D.1 关 4:1 max-pool，同 2048 栅格复跑（2026-11-05）

> 目的：把「Python 512 显示 bin vs C++ 2048 bin」的栅格差从账上拿掉，同栅格对账。
> 做法：`SpectrumDataGenerator(num_bins=2048)`（`num_bins` 设 2048 = 关闭 4:1 池化），
> FFT 2048、Nuttall 窗、avg 1；C++ 侧原本就是 2048 bin 的 `dsp::detectPeaks`。
> 同一 256 帧、同一配对口径（同帧号 + `|Δf| ≤ 500 Hz`）。

| 指标 | 512 口径（上一轮） | 2048 同栅格（本轮） |
|---|---|---|
| Python 显示峰（≤6/帧） | 1423 | 1521 |
| C++ 显示峰（≤12/帧） | 3034 | 3034 |
| Python 独有 | 1303 | **1283** |
| C++ 独有 | 2914 | **2796** |

Python 独有三栏（基数 1283）：实测 **1250（97.4%）** / 推断 **33（2.6%，R_region）** /
未归因：**该栏无事件**（六个实测桶覆盖全部 1283）。

C++ 独有（基数 2796）六桶：C_cap 96、T_frame 1503、G_grid 135、R_region 1000、
S_state 62、D_merge 0；R_region 用宽通道关闭实验拆为同格 303 / 仅邻格 224 / 同格邻格均无 473。
三栏如下（**每栏占比都以 2796 为基数**）：

| C++ 独有（2796）分栏 | 条数 | 占 2796 |
|---|---|---|
| 实测（C_cap 96 + T_frame 1503 + G_grid 135 + S_state 62 + D_merge 0 + 宽通道同格 303） | 2099 | 75.1% |
| 推断（R_region 仅相邻 2048 格有窄带候选） | 224 | 8.0% |
| 未归因（R_region 同格/邻格均无窄带候选） | **473** | **16.9%** |

### D.2 主因判定：max-pool 不是主因（实测）

把上一轮原 704 条未归因**逐条结转**到同栅格复跑：

- 关掉 4:1 max-pool 后，704 条中仅 **60 条（8.5%）** 能在 Python pre-cap/显示口径找到
  ≤500 Hz 候选（其中显示可见 27 条，3.8%）；**644 条（91.5%）仍无任何 ≤500 Hz 候选**。
- 未归因 704 → 644，只降 8.5%，**不显著**。

实测结论：**4:1 max-pool 不是这 704 条的主因**，它只能解释 60 条。剩余 644 条继续拆（D.3）。

### D.3 关掉什么 / 掉了多少 / 剩多少：Python 门限 6 → 3 dB（同栅格）

- **关掉什么**：Python 窄带相对门 `median + 6 dB` → `median + 3 dB`（C++ 仍为 6 dB）。
- **掉了多少**：同栅格基数 2796 下，未归因 473 → 新增可配对 **33 条（7.0% of 473）**；
  原 704 结转集 644 → 新增可配对 **85 条（13.2% of 644）**。
- **剩多少**：同栅格口径 **440 条（15.7% of 2796）**；原 704 口径 **559 条（79.4% of 704）**。

### D.4 关掉什么 / 掉了多少 / 剩多少：C++ NMS 与门限两次消融

两次消融的输入都是 D.1 的 473 条同栅格未归因（基数 2796）。

| 消融 | 关掉什么 | 掉了多少条 | 剩多少 |
|---|---|---|---|
| 关 NMS | `dsp::detectPeaks` 的 `minSep = n/100`（2048 → 20 bin）改为 `1` bin（等同关闭） | 显示不再出现 351 条（74.2%），其中仅 82 条（23.4% of 351）仍在 raw/matured ≤500 Hz | 未归因仍 **473（16.9% of 2796）** |
| 门限降 3 dB | C++ `thresholdDb` 6 → 3 dB | 显示不再出现 82 条（17.3%），其中仅 16 条（19.5% of 82）仍在 raw/matured ≤500 Hz | 未归因仍 **473（16.9% of 2796）** |

**可归因均为 0 条**：关 NMS 本应让 raw 峰只增不减，门限降低同理；显示层 top-12 截断
与 5 bin 跟踪匹配被邻峰抢占，会让旧轨迹不再成熟/被挤出，属显示与跟踪副作用，
不能证明这些峰原先被 C++ 检出的机制是 NMS 或门限。因此这两次消融对 473 条
**不构成解释**（实测否证，不是「已解释」）。

### D.5 消融汇总与最终未归因

| 消融项 | 影响条数 | 剩余未归因 | 证据强度 |
|---|---|---|---|
| 关 Python 宽通道（512 口径，上一轮） | 534（32.0% of R_region 1670）转实测 | 704（24.2% of 2914） | 实测 |
| 关 4:1 max-pool（同 2048 栅格） | 原 704 中 **60（8.5%）** 可配对 | 原 704 剩 **644（91.5% of 704）** | 实测 |
| Python 门限 6 → 3 dB（同栅格） | 同栅格 **33（7.0% of 473）**；原 704 结转集 **85（13.2% of 644）** | 同栅格 **440（15.7% of 2796）**；原 704 **559（79.4% of 704）** | 实测 |
| 关 C++ NMS 20 bin | 可归因 **0 条**（351 条退出显示，其中 82 条仍在 raw/matured，属 top-12/跟踪位移） | 473（16.9% of 2796） | 实测（否证） |
| C++ 门限 6 → 3 dB | 可归因 **0 条**（82 条退出显示，其中 16 条仍在 raw/matured，属同上位移） | 473（16.9% of 2796） | 实测（否证） |

最终：原 704 口径 **未归因 559 条（79.4%），机制未证实**；
同栅格 2796 口径 **未归因 440 条（15.7%），机制未证实**。
两个基数不同，**百分比不得相加或互换基数**。

### D.6 定义对拍：两边的本地极大 / 双通道 / 显示定义（2026-11-05）

> 本轮只取证，不改代码。逐行原文如下（行号取自 HEAD）。

| # | 定义点 | C++ 原文（文件:行） | Python 原文（文件:行） | 对 C++ 独有方向的作用 |
|---|---|---|---|---|
| 1 | 检测域 / 池化顺序 | `cpp/src/dsp/peak_detector.cpp:30-32`：`binHz = fs/(n-1)`，直接在 2048 原始帧上找峰，无池化 | `desktop/spectrum_widget.py:535-536`：`self.spectrum = self._resample_max(power_db)`（生产 4:1 → 512）；同栅格 2048 时 `:563-564` `if len(spec) == self.num_bins: return spec` | 基线两侧不同域；同栅格复跑已消除 |
| 2 | 局部极大与平台边缘 | `peak_detector.cpp:40-41`：`v > dbfs[i-1] && v >= dbfs[i+1]`（平台取**左缘**） | `spectrum_widget.py:729-731`：`spectrum >= left && spectrum > right`（平台取**右缘**） | 平台宽 ≥2 bin 时代表点差 1..w-1 bin；1 bin=500.24 Hz，已超 500 Hz 配对容差 |
| 3 | 门限基准与比较 | `peak_detector.cpp:21-24`：中位取 `sorted[n/2]`（偶数取上中位），`:40-41` 用严格 `v > levelGate` | `spectrum_widget.py:717`：`float(np.median(ss))`（偶数取均值）；`:729-731` 用非严格 `spectrum >= thresh` | 基准差实测 max 0.0294 dB / mean 0.0042 dB；只在恰好压门时翻转（方向多为 Python 独有） |
| 4 | 宽通道 / 平滑合并 | 无宽通道；只有原始局部极大 | `spectrum_widget.py:734-738` 平滑窗 `win=max(3,(n//64)*2+1)`；`:750-756` 相邻宽峰合并；`:760-777` 支撑区内窄峰被一个宽峰代表吸收 | Python 可把窄峰整条丢弃/换成远处的宽峰代表；C++ 无此路径。**本轮主因之一** |
| 5 | 合并 / NMS 半径 | `peak_detector.cpp:49-50`：`minSep = max(1, n/100)`（2048 → 20 bin）；`:54-61` 最高优先贪心抑制 | `spectrum_widget.py:782-787`：窄峰只做相邻 1 bin 去重；宽峰用 `:752` 的 `pad` 窗口合并 | C++ 抑制 20 bin 内次高峰，Python 保留 → 只解释 Python 独有，**不能解释 C++ 独有** |
| 6 | 成熟 / 遗忘 / 显示门 | `cpp/src/ui/spectrum_display.cpp:1131-1141`：只收 `seen >= kPeakMinSeenFrames` 的 matured，按 dB 取 top-`kMaxPeakCount`(12)，**不按本帧中位数重门控**；`:1121-1126` 未命中只 `missed++`，`missed < kPeakMaxMissFrames`(10) 保留最后 freq/db | `spectrum_widget.py:630-653`：同一套 `seen>=3 / missed<10 / ±5 bin`；`:669-680` 读时按**本帧** `floor+rel` 重门控并 `[:6]` | C++ 可把**本帧无 raw** 的陈旧频率继续显示最多 9 帧；Python 轨迹会被匹配拉走、或被读时门控/前 6 截断。**本轮主因之二** |
| 7 | 代表频率公式 | `peak_detector.cpp:30-32`：`fLow + fs/(n-1) * i`（左缘栅格） | `spectrum_widget.py:579-583`：`fLow + fs*(i+0.5)/n`（中心栅格） | 系统性偏 ~250 Hz，<500 Hz；相邻 bin 恰好 500.24 Hz 超容差 |
| 8 | NaN / SNR 定义 | `peak_detector.cpp:21-23` 排序与比较不剔 NaN；`PeakInfo` 无 SNR 字段 | `spectrum_widget.py:711-714` `finite` 掩码；`:675` `snr = db - floor`（db−本帧中位数） | NaN 帧上 C++ 候选可能被比较抑制、Python 跳过；SNR 只在 Python 侧存在 |

### D.7 30 条定义级判定：改名 / 挂账

> 原六桶脚本是 scratch，已删；本轮按同一定义重建（`cpp_only=2796`，
> C_cap 96 / T_frame 1519 / G_grid 121 / S_state 59 / R_region 1001；
> R_region 再分同格 306 / 邻格 211 / 硬核 **484**）。重建硬核与原 473 差
> **+11 条（+2.3%）**，抽样来自重建集；差异不改变下面 30 条的判定。
> 抽样：按出现帧均匀取 30 条。实际侧恒为「仅 C++ 显示」。

| 判定层 | 预测规则 | 吻合 | 说明 |
|---|---|---|---|
| 本地极大 / 窄带子定义 | 在 C++ 最后一次 raw 命中帧 kk，看 Python **窄带**规则是否同 bin 成立；同 bin → 预测「两侧都有」，否则「仅 C++」 | **4/30 = 13.3%** | 26/30 在 kk 窄带有同 bin 峰，预测两侧都有、实际仅 C++ → 不吻合。**本地极大值不是主因** |
| 双通道完整检测定义 | 在 kk 看 Python `_detect_peak_bins` **完整输出**（宽+窄合并后）是否同 bin 有峰；无 → 预测「仅 C++」 | **27/30 = 90.0%** | 27 条完整输出在 ±20 bin 内都没有峰（宽通道吸收/代表点远移）；3 条同 bin 有峰 |
| 完整定义链（含第 6 条成熟/保持） | 再叠加 `seen>=3` 与 C++ 9 帧陈旧保持：显示帧 k 上 C++ 无 raw 仍在榜，Python 该帧 500 Hz 内无轨迹（479/484 无轨迹，5 条 seen=1/2） | **30/30 = 100%** | 全量硬核 **484/484** 在显示帧 k 都没有 C++ raw，属陈旧保持；3 条同 bin 峰只 seen=1/2，未成熟 |

**结论（实测，非推断）**：

1. 吻合率（完整定义链）**30/30 = 100% ≥ 90%** → 允许改名：同栅格 2796 口径的
   「未归因 473」改判为 **「定义差异（宽通道合并 + 跟踪保持）」**；该口径未归因
   **该栏无事件**（不写 0%）。
2. **机制不落在本地极大值**：只用第 2 条窄带子定义时吻合 **4/30 = 13.3%**。
   主因是第 4 条（Python 宽通道吸收窄峰）与第 6 条（C++ 9 帧陈旧保持 vs Python
   轨迹未成熟/被移走/读时门控）；第 5 条 NMS 只能解释 Python 独有，不解释本桶。
3. **原 704 口径不改判**：473 与 704 的交集未逐条映射，**未归因 559 条（79.4%），
   机制未证实**；不得拿 2796 口径的 100% 去清零 704 口径。
4. 证据强度：30 条单条级实测 + 硬核全量 484 条 stale 统计 + 全量 2796 配对计数，
   均为**实测**；「宽通道吸收」与「陈旧保持」的机制链路由第 4/6 条原文与
   `track@kk` 状态直接支撑，不靠推断。

---

## 附录 E、文案审计线补证（2026-11-06）

### E.1 GNSS `*_dbm` 字段名：保留名、明写单位（不改协议）

- **判定**：保留字段名。数据源是 SDR 归一化 PSD（`|I/Q|²/Hz`）的相对比值，单位随后端（dBFS 或 dBµV），**不是 dBm**，不能按绝对功率换算。改名会破坏 `workflow_engine` 对 `rssi_by_azimuth` / `peak_az` 的回填与现有消费方，故按「字段名兼容 + 展示层明写」处理。
- **展示口径**：平均功率 / 噪声基底 / 峰值功率 / 峰值RSSI 一律标「相对读数，非 dBm」。
- 四元组：
  - `84446a8d` — `mbdsdr_ai/gnss_monitor.py:49` — `平均功率（相对读数 dB，非 dBm）`
  - `84446a8d` — `mbdsdr_ai/gnss_monitor.py:50` — `噪声基底（相对读数 dB，非 dBm）`
  - `84446a8d` — `mbdsdr_ai/gnss_monitor.py:53` — `峰值功率（相对读数 dB，非 dBm）`
  - `84446a8d` — `mbdsdr_ai/sdr_tools.py:6047` — `平均功率: {result.power_dbm:.1f} dB（相对读数，非 dBm）`
  - `84446a8d` — `mbdsdr_ai/sdr_tools.py:6132` — `峰值RSSI: {result['peak_rssi_dbm']}（相对读数 dB，非 dBm）`

### E.2 C++ RSSI 注释与头文件初值对齐

- `4cdabbb5` — `cpp/src/dsp/spectrum_engine.cpp:1748` — `header keeps rssiDbfs_ at NaN (unknown), not a -100 dBFS quiet default.`
- 头文件事实：`cpp/src/dsp/spectrum_engine.h:892` 的 `rssiDbfs_{quiet_NaN()}` 是哨兵，未出首块数据前为 NaN；改注释不动代码。

### E.3 本线文档断言回查（`python scripts/verify_claims.py`）

- 本文件行 192（采样率口径）与行 378（2.800M 吸附回读）的四元组已就地补齐。
- 本文件行 206 / 210（C++ 档位与回读结论）：**未证实（无 commit）**。所引改动仍在工作区（`cpp/src/dsp/device_capabilities.cpp`、`cpp/src/control/control_hub.cpp` 为 M），且这两行正被参数治理线在途改写；本线不代改他人 hunk，待该线提交后由其补四元组。
