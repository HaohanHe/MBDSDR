# 硬编码参数普查与裁定

> 本篇结论未经 commit 取证，引用前须复核（判据见 `scripts/verify_claims.py`；补齐四元组 `<结论> — <hash> — <path>:<line> — <改动后字面量>` 后可摘除本行）。

> 范围：Python 桌面端（`desktop/`）、Python 内核（`mbdsdr_ai/`）、C++ 桌面端（`cpp/src/`）。
> 目标不是"把数字都变成开关"，而是判定：**哪些该给用户、哪些该锁死、哪些该集中但不必暴露**。
> 纪律：收口必须证明口径未变；不许为了集中而改行为；跨语言不强行共享常量。

---

## 0. 方法与边界

- 只做**有语义**的数值（阈值/时长/上限/物理常量），不登记 UI 像素、数组字面量、TLE 文本、测试期望值。
- `desktop/main_window.py`、`desktop/rf_sky_view.py`、`desktop/spectrum_widget.py` 归其它线在途编辑。
  本线**只出清单与 patch 建议**，不改这三个文件；引用它们时给符号名，行号可能随在途编辑漂移。
- C++ 与 Python 是**两个进程**，没有共享常量机制。跨语言只做三件事：
  **各自定义 → 文档登记 → 测试对拍**（见 §7 与 `tests/test_hardcoded_parameter_registry.py`）。

---

## 1. 数量盘点

| 域 | 普查条目 | A 该暴露 | B 该锁死 | C 该集中 | 已集中 |
|---|---:|---:|---:|---:|---:|
| 天文 / 几何 / 星空渲染 | 20 | 2 | 9 | 9 | 6（本轮新增 2） |
| 频谱 / DSP | 34 | 8（多数已暴露） | 8 | 12 | 9 |
| 卫星 / 轨道 / 跟踪 | 16 | 2 | 6 | 5 | 8 |
| 交互 / 计时 | 20 | 0 | 3 | 12 | 5 |
| AI / LLM | 14 | 3（已暴露） | 6 | 4 | 6 |
| 存储 / 录制 | 6 | 3 | 2 | 1 | 1 |
| **合计** | **110** | **18** | **34** | **43** | **35** |

"已集中"指已有单一事实源（`desktop/config_schema.py`、`cpp/src/core/tokens.h`、
`desktop/constants.py`、`mbdsdr_ai/skyengine/stars.py`），但仍散落在消费点的按**部分集中**计。

结论一句话：**这个仓库的显露参数（频率/带宽/增益/主题）已经收得很好，
真正漏水的是"同一物理量在算法层、UI 层、另一前端各有一套数"——共发现 11 组。**

---

## 2. 最严重的「同一件事两套数」

按严重度排序。**这些不是"默认值偏好"，是同一语义在不同链路各自定义**。

### 2.1 静噪门限：同一根滑杆，五套数、两种物理量纲 🔴 P1

| 位置 | 值 | 语义 |
|---|---|---|
| `desktop/control_panel.py:582-583` | 滑杆范围 `-120..0`，默认 `-80` | 用户看到的门限 |
| `desktop/main_window.py:507` | `_squelch_db = -80.0` | UI 主窗口缓存 |
| `desktop/receive_pipeline.py:57` | `SharedDemodConfig(squelch_db=-80.0)` | 解调线程读的固定门 |
| `mbdsdr_ai/analog_demod.py:120-126` | 每模式 `-40/-30/-60` | `set_mode()` 会把门限重置成这些值 |
| `mbdsdr_ai/analog_demod.py:225` | `-150.0`（"= open"） | 引擎构造初值 |
| `cpp/src/core/tokens.h:375-377` | 范围 `-100..-20`，默认 `-50` | C++ 前端同名列 |

更关键的是**量纲不同**：

- Python WFM 固定门（`receive_pipeline.py:356-359`）比较的是
  `10*log10(mean(|iq|²))`——**全带宽原始 IQ 功率**；
- Python 窄带门（`analog_demod.py`）比较的是**信道滤波后的 RMS**；
- C++ 门（`tokens.h:368` 注释）比较的是 **48 kHz 解调音频块的 RMS**。

所以"−80 dBFS"在三个地方指的是三种不同的测量点。用户拖到 −80 看到的开合点，
在两个前端不可能一致。

**裁定（2026-11-05，决策人）：统一到「解调后音频 RMS」，范围 `-100..-20`，默认 `-50`。**
理由：绝对阈值不可能跨带宽一致（带宽翻倍，噪声功率 +3 dB），而解调后音频电平是
所有模式（含 WFM）共有的测量点。落地见 §10-1；`NarrowbandReceiver` 的 IF 域门控
是**另一个概念**（无头 gqrx 兼容路径，不被这根滑杆驱动），保留并显式注释。

**二次裁定（2026-11-05，决策人，取代上条的口径语义）**：上条把"一根杆"改成
"音频 RMS"后，FM 纯噪声实测 `-14.54 dBFS`、NFM 纯噪声 `+8.98 dBFS`，
固定 `-50` **永远不关**——一个不关的静噪等于没有静噪。所以滑杆保留
一个、刻度保留 `-100..-20`（默认 `-50`），但**值的语义改为"相对该模式噪声
基线的余量"**，不是绝对音频电平：

- 基线按调制族各自测：FM/WFM/NFM 与 AM 用解调后音频 300–5000 Hz 谱峰/谱中位，
  USB/LSB 用 300–3000 Hz，CW 用 300–5000 Hz；全部归一化成"存在度余量 dB"再比较；
- 基线**自动标定**（模式切换/增益变化/采样率变化时重测），标定失败必须有可见状态；
- 实测反证：FM 音频高通段（>3 kHz）噪声占比不可分（噪声 `-0.73` vs 弱 `-0.12` dB），
  NFM 音频 RMS 反向（噪声 `+8.98` > 弱可读 `+8.62` dBFS），因此统一用峰/中位
  （尺度无关，`+6 dB` 增益读数变化 `0.00 dB`）；
- **不得**把 FM 拉回 IQ 功率（那会退化成"一根杆两个测量点"）。

细化与验收矩阵见 §10-1。


### 2.2 卫星"下一次过境"的最低仰角：5° 与 10° 🔴 P1

| 位置 | 值 | 角色 |
|---|---|---|
| `mbdsdr_ai/sat_tracker.py:115` | `DEFAULT_MIN_ELEVATION_DEG = 5.0` | 类级默认 |
| `mbdsdr_ai/sat_tracker.py:569-591` | `next_pass(min_alt=DEFAULT_MIN_ELEVATION_DEG)` | **把 5° 传进 `predict_passes`** |
| `mbdsdr_ai/sat_passes.py:214`（及 `:604`） | `min_alt: float = 10.0` | 预测器自身默认 |
| `desktop/rf_sky_view.py`（`PASS_MIN_ALT_DEG`） | `10.0` | 星象仪过境列表的**峰值仰角**门 |
| `desktop/new_spacetime_panel.py:524` | `5.0` | 时空面板过境时间线 |
| `mbdsdr_ai/core/controller.py:990` | `get_satellite_pass(min_elevation_deg=None, window_hours=None)` | 控制器（显式入参，缺省回落 `PASS_MIN_ALT_DEG` / `PASS_WINDOW_HOURS`） |
| `mbdsdr_ai/agent.py:1554` | `min_elevation=10` | AI 工具默认 |

这不是"两个合理默认"，而是**同一功能两条调用链拿到两个门**：
`tracker.next_pass()` 走 5°，直接 `predict_passes()` 走 10°。
结果：同一颗卫星、同一站址，"下一次过境"在星象仪/时空面板/控制器里可能给出不同条目。

**裁定（2026-11-05，决策人）：拆成两个明确常量，`next_pass()` 默认必须等于它对外承诺的门。**
- `VISIBLE_MIN_ALT_DEG = 5.0`：当前可见 / 跟踪退出（低仰角信号仍可能可用）；
- `PASS_MIN_ALT_DEG = 10.0`：过境预测/列表的峰值仰角门。

落地见 §10-2：`next_pass()` 默认改为 10°，时空面板改引用常量，列表旁注明
「按峰值 ≥10° 筛选」。5–10° 的过境会从列表消失，这一条必须在 UI 写明；
"显示低仰角过境"开关作为 A 类项交设置线（A9）。


### 2.3 峰值判定：两侧统一检测器定义（rel 6 dB）✅ P-1 已裁定

| 位置 | 值 | 语义 |
|---|---|---|
| `desktop/spectrum_widget.py:1005` | `peak_rel_db = 6.0` | Python 峰阈值（**裁定值，保留**） |
| `desktop/spectrum_widget.py:550` | `max_peaks: int = 8` | Python 峰数量 |
| `desktop/config_schema.py:195-200` | `peak_threshold_db` 默认 6.0，范围 1–30 | 已经暴露（A）；`source` 指针仍写 `spectrum_widget.py:859`（行号漂移） |
| `cpp/src/core/tokens.h:365` | `kPeakRelDb = 6.0` | C++ **显示检测器**门限（新增常量；显示路径不再用 15） |
| `cpp/src/core/tokens.h:371` | `kPeakThresholdDefault = 15.0` | **频率校准参考门**（有意保留，注释明确禁止与显示门合并） |
| `cpp/src/core/tokens.h:372-377` | `kPeakThresholdMin = 5` / `kPeakThresholdMax = 40` / `kPeakAbsFloorDbfs = -100.0f` / `kPeakMatchBins = 5` / `kPeakMinSeenFrames = 3` / `kPeakMaxMissFrames = 10` | C++ 检测器其余定义（**裁定保留**） |
| `cpp/src/ui/spectrum_display.cpp:58` | `kMaxPeakCount = 12` | C++ 峰数量 |

同一功能、同一种谱，两个前端标出的峰不一样。

**基准核查（2026-11-05，本线）：两边基准是同一个。**
Python `_detect_peak_bins` 用 `floor = median(谱)`，门限 `floor + rel_db`
（`spectrum_widget.py:487-488`）；C++ 检测器注释明写
"relative to the spectral median / noise floor"（`tokens.h:351`）。
**同一基准，不是两个产品概念。** 落点：显示用
`kPeakRelDb`（`cpp/src/ui/spectrum_display.h:508`、`spectrum_widget.cpp:415`），
频率校准用 `kPeakThresholdDefault`（`cpp/src/ui/calibration_dialog.cpp:553`）——
两条门各自成立，不合并。

**裁定（2026-11-05，决策人，取代本节旧的"统一到 15 dB"）：统一检测器定义，
不是把数字凑一样：**

```
rel 6.0 dB / abs -100 dBFS / 3 帧确认 / 5 bin 匹配 / 10 帧遗忘
```

两条路径（Python `spectrum_widget.py` 与 C++ `spectrum_engine`/`spectrum_display`）
都按这一套定义；Python 的 `6.0` 保留（它就是裁定值），C++ 显示检测器改用
`kPeakRelDb = 6.0`（`tokens.h:365`）。C++ 的 `kPeakThresholdDefault = 15.0`
**不是显示门**，只留给"频率校准参考门"（要求拒绝纯噪声与埋在噪声里的音调，
`test_frequency_calibrator` 钉住），源码注释明确写明**不要合并两条门**。

**为什么"统一到 15 dB"被驳回（实测证据）**：频谱线用编译后的 C++ 实测——
15 dB 相对门叠加 `-100 dBFS` 绝对底后，等效门 ≈ **-85 dBFS**；而 `+8 dB`
弱载波的峰是 **-92 dBFS**，低于该等效门，**24 帧全 0 峰**，连候选峰都进不了。
15 dB 在这里不是"更保守"，是"弱信号隐身"。

**6 dB 的洪泛反证**：C++ 在 6 dB 下同一场景实测 **3043 vs 3029 峰次**
（差异 <0.5 %），没有洪泛。防抖真正靠的是跨帧 `3 帧确认` + `5 bin 匹配`
+ `10 帧遗忘`，不是抬阈值。

数量上限 8 vs 12 属显示密度，不是物理量（同一检测器下 CSS 列表能放几条），
收成常量并在文档登记"有意不同"。


### 2.4 其余"两套数"（次严重，P2/P3）

| # | 语义 | 两套值 | 位置 |
|---|---|---|---|
| D-1 | 默认采样率 | `2.048 MS/s` vs `2.4 MS/s` | `config_schema.py:260` vs `tokens.h:301` |
| D-2 | ~~调谐步进默认 10 kHz vs 100 kHz~~ **已撤回（见下）** | Python 10 kHz；C++ `kFreqCoarseStepHz = 10000` | `control_panel.py:323` vs `tokens.h:816` |
| D-3 | ~~FFT 档位数量~~ **已统一（2026-11-05，见下）** | 两端均三档 `1024/2048/4096`（Python `FFT_SIZES` == schema `fft_size.choices`） | `spectrum_widget.py`（`FFT_SIZES`） vs `config_schema.py:176` / `settings_dialog.cpp:250` |
| D-4 | dB 默认量程 | `-100..-20` vs `-45..0` | `spectrum_widget.py:916-917` + 默认值 vs `tokens.h:329-330` |
| D-5 | 瀑布行数 | 固定 `256` vs 可选 `128..4096` | `spectrum_widget.py:161` vs `tokens.h:698` |
| D-6 | 过境预测采样 | 粗 `60 s` / 细 `2 s` vs 粗 `300 s` / 细 `30 s` | `sat_passes.py:88-90` vs `rf_sky_view`（`PASS_COARSE_STEP_S`/`PASS_FINE_STEP_S`） |
| D-7 | "光谱空值"哨兵 | `-120.0` vs 无哨兵 | `spectrum_widget.py:419` vs C++ 用 `-100` 只作**安静带默认读数**（真实值） |
| D-7b | RTL 设备基线采样率 | 三套：`2.048` / `1.024` / `2.048` 硬编码 | `sdr_backend.py` / `osmosdr_source.py` / `rtltcp_client.py`。**已统一为 2.4 MS/s 首选**（§10-3） |
| D-8 | 状态提示时长 | `3000/4000/6000/8000 ms` 散落 20+ 处 | `main_window.py:2061,2490-2624` 等 |
| D-9 | AI 工具重试 | 2 次 / 0.4 s vs 3 次 / 1 s→8 s | `agent.py:4050-4061` vs `tokens.h:1335-1337` |
| D-10 | AI 上下文 | Python 8192（`max_context_tokens`） vs C++ `32768×0.75` | `config.py:20` / `config_schema.py:237` vs `tokens.h:1321-1322` |

D-9 **不是**同一语义：Python 是"工具调用可重试判定"，C++ 是"LLM 请求瞬时错误退避"。
两者都合理，只需在文档写清（已写）。其余 8 组是真·同一语义两套数。

**D-2 撤回（本线自我纠错）**：先把 `tokens.h:283 kFreqStepHz = 100e3` 当成"调谐步进"，
这是错的——它只被 `control_hub.cpp:1422` 用作 **`start_scan_link` 的扫描步进**。
C++ 真正的调谐步进是 `kFreqCoarseStepHz = 10000`（`tokens.h:816`），与 Python 的
10 kHz **本来就一致**，也与频谱吸附网格 10 kHz 一致。
→ **决策人裁定 3 的"调谐步进两套数"前提不成立，无需改动**（扫描步进 100 kHz 是另一语义，保留）。

**D-3 收口（2026-11-05，往少的收）**：`SpectrumDataGenerator.FFT_SIZES` 删除
8192/16384 两档，与 `config_schema.fft_size.choices` / C++ `settings_dialog` /
AI 工具的三档逐项相等（含顺序）。旧配置引用已删档位时回落最近档 **4096**
并记录原因（不静默改成 2048）。2.4 MS/s 下 bin 宽实测 **585.9375 Hz**；
删除前 16384 档为 **146.484 Hz**（差 **439.453 Hz** / 4 倍），删除后差值 = 0。

**D-7 结论（诚实性红线）**：Python 的 `-120.0` 落在可显示量程 `-160..0` **之内**，
所以"真实的 -120 dB 读数"和"无数据"长得一模一样，用户无法分辨。
正确做法是**哨兵与可测范围不相交**：Python 用 `NaN`/`None` 语义（渲染层画空白），
不得用 dB 数字；C++ 侧 `-100` 是"安静带的默认读数"（真实值），**不是哨兵**，
需在注释里明确这一区别。落地：`spectrum_widget.py` 在途 → patch P-8c。

**D-4 结论（dB 默认量程）**：先定基准——Python `db_min/db_max`
（`spectrum_widget.py:840-841`，默认 `-100..-20`）与 C++ `kDbLowerDefault/`
`kDbViewUpperDefault`（`tokens.h`，`-100..-20`）**都是 dBFS、都以满量程正弦
为 0 dBFS**，基准相同，不是静噪那种"测量点不同"的问题。默认视窗已按同一口径
对齐（2026-11-05 `65163cf6`）；显示能画到的最高值另由 `kDbCeilingMax = 0`
（满量程硬上限）承担，**不要**把它和默认视窗上限合成一个常量（合成会让显式
`setDbRange(-100, 0)` 被夹成 -20）。C++ 已有
`spectrum_display.cpp` 的 24 帧/10 dB 自动量程，Python 首次收到谱帧时
按噪底 + 目标余量设一次初值；两端的**夹取边界**（Python `-160..0`、C++
spin `-140..20`）才是 B 类锁死项，各写清理由。


---

## 3. 分域普查表

字段：`值 | 位置 | 语义 | 谁读它 | 裁定`。裁定：**A** 暴露 / **B** 锁死 / **C** 集中不暴露。
"已集中"列：✓ 单一事实源 / △ 部分 / ✗ 散落。

### 3.1 天文 / 几何 / 星空渲染

| # | 值 | 位置 | 语义 | 依赖 | 已集中 | 裁定 |
|---|---|---|---|---|---|---|
| S-1 | `6.5` | `skyengine/stars.py:156`（`NAKED_EYE_LIMIT_SCALE`） | 裸眼极限基线 | `star_draw` / `star_draw_many` / `star_catalog.limit_magnitude` | ✓（本轮收口） | B |
| S-2 | `0.35` | `stars.py:157` | 每档 Bortle 极限下降 | 同上 | ✓ | B |
| S-3 | `0.06` | `stars.py:158`（`BORTLE_DIM_SLOPE`） | 每档压暗比例 | 两个绘制路径 | ✓（本轮收口） | B |
| S-4 | `8.0` | `stars.py:160`（`MAG_SPAN_TO_FULL_BRIGHT`） | 星等→半径参考跨度 | `frac` 映射 | ✓ | B |
| S-5 | `3` | `stars.py:155`（`DEFAULT_BORTLE`） | 默认光害假设 | 4 处默认参数 | ✓（本轮收口，`rf_sky_view._bortle` 仍独立） | A（受阻，见 §4 备注） |
| S-6 | `1.2` / `1.728` | `stars.py:184-185` | 亚像素星补偿阈值与面积比 | `star_draw` / `star_draw_many` | ✓ | B |
| S-7 | `2.0` / `8.0` | `stars.py:189-190` | 辉光起始/满强度半径 | 同上 | ✓ | C |
| S-8 | `0.6` / `1.1` / `2.0` | `stars.py` `star_draw` 半径/亮度映射 | 视觉编码曲线 | 同上 | △（两路径镜像） | B |
| S-9 | `0.2` | `stars.py:227`（`EXTINCTION_K`） | 天顶消光 k（mag/airmass） | `extinction_delta_mag` | ✓ | A（低优先）/ B 待定 |
| S-10 | `3.5` | `stars.py:228` | 消光 Δm 上限 | 同上 | ✓ | B |
| S-11 | `0.5` | `stars.py:229` | 仰角下限（防除零） | 同上 | ✓ | B |
| S-12 | `90.0` | `stars.py:239` | 仰角夹取上限 | 同上 | △ | C |
| S-13 | `9.0` | `star_catalog.py:115`（`CATALOG_DEPTH_MAG`） | 星表物理深度 | `limit_magnitude` / `select_for_view` | ✓ | B |
| S-14 | `0.5` | `star_catalog.py:117` | 星表分档宽度 | 加载/查询 | ✓ | B |
| S-15 | `20000` | `star_catalog.py:119`（`DEFAULT_MAX_COUNT`） | 单帧默认上限 | `select_for_view` / build | ✓ | C |
| S-16 | `30.0` / `1.5` | `star_catalog.py:121-123` | 深度增益基准 FOV / 上限 | `detail_gain_mag` | ✓ | B |
| S-17 | `0.5`（log2 斜率） | `star_catalog.py:219` | 视场深度增益斜率 | 同上 | △（字面量） | C |
| S-18 | `0.35 + 0.65*bright` | `rf_sky_view.py` `_star_visual` | 星点透明度映射 | 绘制 | △ | C |
| S-19 | `(4.5 - mag)/9.0` | `rf_sky_view.py` `_find_body_at` 两处 | 点击拾取评分归一 | 拾取 | ✗（同文件两处） | C |
| S-20 | `_bortle = 3` | `rf_sky_view.py:1714`（在途漂移） | 光害假设唯一入口 | 星空绘制/拾取 | ✗ | A（受阻：无 setter） |

> S-5/S-20：`config_schema.py:301-305` 已把它登记为 LOCKED，理由"rf_sky_view 无公开 setter"。
> 本线确认该理由成立，**不重复报 A**；解锁条件见 `config_schema.py` 原文。

### 3.2 频谱 / DSP

| # | 值 | 位置 | 语义 | 依赖 | 已集中 | 裁定 |
|---|---|---|---|---|---|---|
| P-1 | `(1024,2048,4096)` | `spectrum_widget.py`（`FFT_SIZES`） | FFT 档位 | 下拉、生成器 | ✓ | A（已暴露；与 schema 逐项一致） |
| P-2 | `2048` | `spectrum_widget.py:151`；`cpp/.../spectrum_engine.h:884` | 默认 FFT | 生成器 / C++ 引擎 | △（两侧同值） | A |
| P-3 | `(1,4,8,16)` / `1` | `spectrum_widget.py:142,152` | 平均帧档位/默认 | 生成器 | ✓ | A（已暴露） |
| P-4 | 6 种窗 / 默认 Nuttall | `spectrum_widget.py:139,150` | 窗函数 | 生成器 | ✓ | A（已暴露） |
| P-5 | `512` | `spectrum_widget.py:1977-1979`（性能档） | 低性能档 FFT | 性能模式 | ✗（不在 `FFT_SIZES`） | C |
| P-6 | `256` | `spectrum_widget.py:161` | 瀑布环形行数 | 绘制/LUT | △ | A（建议加档位） |
| P-7 | `0.7/0.3` | `spectrum_widget.py:389-391` | 瀑布 IIR 平滑 | 瀑布 | △（字面量） | C |
| P-8 | `-120.0` | `spectrum_widget.py:511` | 无数据哨兵 | 重采样/绘制 | ✗ | B |
| P-9 | `8` | `spectrum_widget.py:550` | 峰值数量上限 | 峰值表 | ✗ | C |
| P-10 | `6.0` | `spectrum_widget.py:1005` | 峰值相对门限 | 峰值检测 | ✓（schema） | A（已暴露；**裁定值**，C++ 显示门同步为 6） |
| P-11 | `10_000.0` | `spectrum_widget.py:865` | 吸附网格 | 滚轮/箭头调谐 | ✓（步进控件写入） | A（已暴露，见 LOCKED） |
| P-12 | `-160.0` / `0.0` | `spectrum_widget.py:916-917` | dB 轴夹取上下限 | 全轴交互 | ✓ | B（跨语言差异 D-4） |
| P-13 | `10.0` | `spectrum_widget.py:918` | 最小 dB 跨度 | 拖拽/滚轮 | ✓ | B |
| P-14 | `40.0` | `spectrum_widget.py:913` | dB 轴拖拽热区宽 | 交互 | ✓ | C |
| P-15 | `(2048,512,50)/(1024,384,66)/(512,256,100)` | `spectrum_widget.py:1977-1979` | 性能三档（FFT/谱线/刷新） | 性能模式 | △ | C |
| P-16 | `16384` | `receive_pipeline.py:47` | 每次读块样点 | 读线程 | ✓ | B（与最大 FFT 绑定） |
| P-17 | `-80.0` | `receive_pipeline.py:57` | 默认固定静噪 | 解调门 | △ | A（待定，见 2.1） |
| P-18 | `60.0` | `receive_pipeline.py:267` | 音频 AGC 最大增益 | AGC | ✗ | C |
| P-19 | `250_000/150_000/50_000` 等 | `mbdsdr_ai/analog_demod.py:49-55` | 每模式 IF/默认带宽/最小带宽 | 窄带解调 | ✓ | B |
| P-20 | `-40/-30/-60` | `analog_demod.py:120-126` | 每模式静噪默认 | `set_mode` | ✓ | A（与 2.1 同题） |
| P-21 | `-150.0` | `analog_demod.py:225` | 构造初值（开启） | 门控 | ✓ | B（已有测试钉） |
| P-22 | `0.4` / `3000.0` / `5000.0` | `analog_demod.py:295-297` | 音频带宽比例/上限、FM 最大频偏 | 解调 | ✗ | B |
| P-23 | `18750..19250 / 15000 / 57000 / 5000` | `analog_demod.py:61-64` | WFM 导频/RDS | WFM | ✓ | B |
| P-24 | `1024 / 0.5 / 0.5 / 10.0` | `mbdsdr_ai/anr_iq.py:49-62` | ANR FFT/重叠/强度/噪声分位 | 降噪 | ✓ | C |
| P-25 | `-140/-20/-40/20` | `tokens.h:325-328` | dB SpinBox 允许域 | C++ UI | ✓ | B |
| P-26 | `-45 / 0` | `tokens.h:329-330` | C++ 默认 dB 量程 | C++ UI | ✓ | A（跨语言待定） |
| P-27 | `10/20/40` | `tokens.h:339-344` | dB 网格档位 | 刻度 | ✓ | A（已暴露） |
| P-28 | `6.0（显示门）/ 15.0（校准门）/ 5 / 40 / -100.0` | `tokens.h:365/371-374` | C++ 峰门限域/绝对底 | 峰检测 + 频率校准 | ✓ | A + B（显示门 `kPeakRelDb=6.0`；校准门 15 有意保留） |
| P-28b | `-50 / -100..-20 / 8.0` | `tokens.h:394-400` | C++ 音频 RMS 静噪默认/域/auto 余量 | 静噪 | ✓ | B（绝对 dBFS，与桌面余量不同量纲，§7 登记） |
| P-29 | `5 / 3 / 10` | `tokens.h:375-377` | 峰跨帧匹配窗口/出现/遗忘帧数 | 峰跟踪 | ✓ | B |
| P-30 | `12` | `spectrum_display.cpp:58` | C++ 峰数量上限 | 峰表 | ✓ | C |
| P-31 | `24 / 10 / 0.80 / 4.0 / 1.5` | `spectrum_display.cpp:32-45` | 自动量程窗口/步进/目标比例/余量/缓动 | 自动 dB | ✓ | C |
| P-32 | `2048` | `spectrum_render.h`（`kLutSize`） | 色图 LUT 档数 | 瀑布着色 | ✓ | C |
| P-33 | `8192` | `spectrum_engine.cpp:545,599` | 读块上限 | IQ 拉取 | △（两处字面量） | C |
| P-34 | `-100/-20/-50 / 8.0 / 0.20 / 0.005` | `tokens.h:375-385` | C++ 静噪范围/默认/自动裕量/噪声底系数 | 门控 | ✓ | A + B（见 2.1） |

### 3.3 卫星 / 轨道 / 跟踪

| # | 值 | 位置 | 语义 | 依赖 | 已集中 | 裁定 |
|---|---|---|---|---|---|---|
| O-1 | `6378.135 / 1/298.26 / 7.292115e-5` | `orbit.py:50-54` | WGS-72 椭球与自转 | SGP4/站心/多普勒 | ✓ | B |
| O-2 | `60 s / 2 s / 0.5 s` | `sat_passes.py:88-90` | 粗扫/细扫/二分容差 | `predict_passes` | ✓ | B |
| O-3 | `24.0` | `sat_passes.py:213,603`；`sat_tracker.py:570` | 预测时长默认 | 过境列表 | △（三处） | A |
| O-4 | `10.0` | `sat_passes.py:214,604` | 预测最低峰值仰角 | `predict_passes` | △ | A（见 2.2） |
| O-5 | `5.0` | `sat_tracker.py:115` | 可见/跟踪退出仰角 | `is_visible` / `next_pass` | ✓ | B（改名拆分） |
| O-6 | `5.0` | `desktop/sat_track_panel.py:30` | 跟踪退出阈值 | 跟踪面板 | △（与 O-5 同值） | B |
| O-7 | `10.0` | `rf_sky_view.py`（`PASS_MIN_ALT_DEG`） | 星象仪过境峰值门 | 过境列表 | ✗ | A（见 2.2） |
| O-8 | `5.0` | `new_spacetime_panel.py:524` | 时空面板过境门 | 时间线 | ✗ | A（见 2.2） |
| O-9 | `10.0` | `core/controller.py:1072` | 控制器过境门 | 控制器 | ✗ | A（见 2.2） |
| O-10 | `10` | `agent.py:1554` | AI 工具过境门 | AI | ✗ | A（见 2.2） |
| O-11 | `24.0 / 300.0 / 30.0 / 300.0 / 1.0 / 10000` | `rf_sky_view.py`（`PASS_*`） | 星象仪独立过境预测参数 | 星象仪 | ✓（本文件内） | C（与 O-2 口径不同，见 D-6） |
| O-12 | `0.25` | `rf_sky_view.py:1616`（`LOCK_SMOOTH_K`） | 摄像机锁定指数趋近系数 | 33 ms tick | ✓ | C |
| O-13 | `500 ms` | `main_window.py`（`_sat_track_timer`） | 闭环跟踪轮询 | 多普勒补偿 | ✗ | C |
| O-14 | `2000 ms` | `rf_sky_view.py:1893` | 调谐刷新节流 | 跟踪调谐 | ✗ | C |
| O-15 | 内置下行频率表 | `sat_tracker.py:61-77` | 各卫星默认频点 | 调谐 | ✓ | C |
| O-16 | `180.0 s` | `main_window.py`（`_approval_timeout_s`） | 无人应答=拒绝 | 发射授权 | ✓ | B（安全） |

### 3.4 交互 / 计时

| # | 值 | 位置 | 语义 | 依赖 | 已集中 | 裁定 |
|---|---|---|---|---|---|---|
| T-1 | `500 ms` | `settings.py:138` | 配置写盘防抖 | `DesktopSettings` | ✓ | C |
| T-2 | `700 ms` | `control_panel.py:774` | 非法输入红框恢复 | 频率框 | ✗ | C |
| T-3 | `1200 ms` | `module_panel.py:377` | 刷新按钮文案恢复 | 模块面板 | ✗ | C |
| T-4 | `60_000 ms` | `new_spacetime_panel.py:245` | 授时状态刷新 | 时空面板 | ✗ | C |
| T-5 | `2000 ms` | `device_panel.py:86` | 设备热插拔轮询 | 设备面板 | ✗ | C |
| T-6 | `1000 ms` | `ntrip_panel.py:115` | NTRIP 状态轮询 | NTRIP | ✗ | C |
| T-7 | `8.0 s` | `ntrip_panel.py:266` | HTTP 超时 | NTRIP | ✗ | C |
| T-8 | `1000 ms` | `mcp_worker.py:73` | MCP 轮询间隔 | 工具队列 | ✓ | C |
| T-9 | `5 s` | `mcp_worker.py:125` | MCP 连接超时 | 连接 | ✗ | C |
| T-10 | `5000 ms` | `main_window.py:623` | 天空视图重绘 | 星象仪 | ✗ | C |
| T-11 | `50 ms` | `main_window.py:630` | IQ 轮询（20 fps） | 频谱 | ✗ | C |
| T-12 | `1000 ms` | `main_window.py:637,643` | GNSS / UTC 刷新 | 状态栏 | ✗ | C |
| T-13 | `150 ms` | `main_window.py:1432,1502` | UI / 天空总线合并 | 跨面板 | ✗ | C |
| T-14 | `250 ms` | `main_window.py:2403` | 发射状态轮询 | 发射 | ✗ | C |
| T-15 | `3000/4000/6000/8000 ms` | `main_window.py:2061,2490-2624` 等 20+ 处 | 状态栏提示时长 | UX | ✗ | C（分层 4 档） |
| T-16 | `10000 ms` | `rf_sky_view.py`（过境重算定时器） | 过境后台重算 | 星象仪 | ✗ | C |
| T-17 | `33 ms` | `rf_sky_view.py:2128`（注释） | 星空 tick | 摄像机锁定 | ✗ | C |
| T-18 | `2 s` | `rf_sky_view.py:1893` | 调谐刷新 | 跟踪 | ✗ | C |
| T-19 | `0.4 s` | `agent.py:4061` | 工具重试间隔 | AI 工具 | ✗ | C |
| T-20 | `120 s` | `agent.py:630,2657` | 子进程/工作流超时 | AI | △ | A（建议暴露） |

### 3.5 AI / LLM

| # | 值 | 位置 | 语义 | 依赖 | 已集中 | 裁定 |
|---|---|---|---|---|---|---|
| A-1 | `8192` | `config.py:20,69`；`config_schema.py:237` | 上下文窗口 | 上下文管理 | ✓ | A（已暴露） |
| A-2 | `2048` | `config.py:21,70`；`config_schema.py:230` | 单次输出上限 | 生成 | ✓ | A（已暴露） |
| A-3 | `0.7 / 0.9 / 60` | `config.py:24-26`；`config_schema.py:214-247` | 温度 / top_p / 请求超时 | 采样 | ✓ | A（已暴露） |
| A-4 | `8192` | `ai_panel.py:322` | 面板展示的 max_tokens 兜底 | 展示 | △ | C |
| A-5 | `2 次 / 0.4 s` | `agent.py:4050-4061` | 工具可重试判定与退避 | 工具循环 | ✗ | C（与 C++ 语义不同） |
| A-6 | `600` | `agent.py:4227` | 摘要回复上限 | 摘要 | ✗ | C |
| A-7 | `4096` | `tokens.h:1312` | 思考预算 | C++ 请求 | ✓ | B |
| A-8 | `32768 × 0.75` | `tokens.h:1321-1322` | 上下文窗口/预算比例 | 上下文压缩 | ✓ | A（跨语言 D-10） |
| A-9 | `1200 / 400` | `tokens.h:1325-1329` | 工具输出截断阈值 | 上下文裁剪 | ✓ | C |
| A-10 | `3 次 / 1000 / 8000 ms` | `tokens.h:1335-1337` | 请求重试/退避 | LLM 客户端 | ✓ | B |
| A-11 | `4096` | `cpp/src/ai/llm_protocol.cpp:205` | 默认 maxTokens | 请求体 | ✓ | B |
| A-12 | `32768` | `tokens.h:1321` | 默认上下文窗口 | 同上 | ✓ | A |
| A-13 | `8192`（C++ 侧旧注释） | `cpp/src/ai/ai_context.h:17-20` | 说明"不再是硬编码" | 上下文 | ✓ | B（文档型） |
| A-14 | 记忆条数/文件上限 | `cpp/src/ai/memory_store.cpp`（未定值） | 记忆容量 | 记忆 | △ | C |

### 3.6 存储 / 录制

| # | 值 | 位置 | 语义 | 依赖 | 已集中 | 裁定 |
|---|---|---|---|---|---|---|
| R-1 | `1` | `recording_library.py:48` | 索引格式版本 | 加载/迁移 | ✓ | B |
| R-2 | `65536` | `recording_library.py:340` | 哈希/扫描分块 | 索引 | ✓ | C |
| R-3 | `None` | `recording_library.py:197-208` | 保留策略（字节/天数/条数） | 清理 | ✓ | A |
| R-4 | `~/mbdsdr_recordings` | `main_window.py:5519` | 录音目录 | 录制 | ✗ | A（受阻，见 LOCKED） |
| R-5 | 索引锁/回收站目录名 | `recording_library.py:48-56` | 文件布局 | 库 | ✓ | B |
| R-6 | 录制文件大小上限 | 未定义（无上限） | 磁盘保护 | 录制 | ✗ | A（建议新增） |

---

## 4. A 类：该暴露给用户（交设置线）

设置线已有 `desktop/config_schema.py` 作为唯一事实源（`exposed` / `LOCKED` 两套登记），
下列建议按该 schema 的字段格式给出。**已暴露的项不重复列**，只列缺口与需要改默认值的。

| # | 建议 key | 人话名 | 默认 | 范围 | 单位 | 分组 | 文案建议 | 为什么该暴露 |
|---|---|---|---|---|---|---|---|---|
| A1 | `pass_min_elevation_deg` | 过境筛选：最低峰值仰角 | `10` | 0–45 | ° | 卫星与多普勒 | "低于这个仰角的过境不列出：仰角越低，大气损耗和多普勒越大，可用时间也更短。" | 城市/山区用户的有效仰角差别很大；这是选过境的第一决策量。**同时解决 2.2 的 5°/10° 分裂。** |
| A2 | `pass_window_hours` | 过境预测时长 | `24` | 1–72 | 小时 | 卫星与多普勒 | "往后算多久的过境；调大更耗 CPU，但能提前安排通联。" | 业余卫星操作常提前数天排计划。 |
| A3 | `recording_retention_total_gb` | 录音库容量上限 | `不限（0）` | 0–2000，步进 5 | GB | 存储与日志 | "超过上限时按最旧优先清理；0 表示不自动清理。" | 磁盘是用户真实约束；`RetentionPolicy` 已支持，只是没默认值。 |
| A4 | `recording_retention_days` | 录音保留天数 | `不限（0）` | 0–3650 | 天 | 存储与日志 | "早于 N 天的录音可被清理；0 表示不按时间清理。" | 同上。 |
| A5 | `squelch_default_db` | 静噪门限默认值 | `-50` | `-100..-20` | 余量刻度（-50 ≈ 8 dB 余量） | 频谱与解码 | "静噪门限：相对该模式噪声基线的余量；数值越大越只放行强信号。基线自动标定。" | **已裁定并落库到内核常量**（§10-1）；设置线只需持久化 + 旧值迁移提示。 |
| A6 | `ai_tool_timeout_s` | AI 工具执行超时 | `120` | 10–600 | 秒 | AI | "单个工具（如扫频/抓取）最多等多久；慢设备可调大。" | 与请求超时 `ai_timeout` 不同层；已有代码常量 `agent.py:630`。 |
| A7 | `waterfall_depth_rows` | 瀑布历史行数 | `256` | 128–4096（2 的幂） | 行 | 频谱与解码 | "瀑布能回看多久；越大越吃内存。" | C++ 侧已可选（`tokens.h:698`），Python 固定 256，建议对齐。 |
| A8 | `sky_extinction_k` | 大气消光系数 | `0.2` | 0.0–0.5 | mag/airmass | 星象仪（高级） | "低仰角星光被大气削弱的程度；城郊 0.2–0.3，高山 0.15。" | 影响地平附近恒星的可见性判断；有物理定义的合理默认。**低优先**（Bortle 尚被锁，先解锁 Bortle 更值）。 |
| A9 | `show_low_elevation_passes` | 显示低仰角过境 | `关` | 布尔 | — | 卫星与多普勒 | "默认只列峰值 ≥10° 的过境；开启后连 5–10° 的一起显示（更适合固定天线/低噪环境）。" | 决策人要求：列表筛选必须可见且可放宽。与 A1 联动（A1 是门槛值，A9 是"是否显示门槛以下"）。 |
| A10 | `default_sample_rate_msps` | 默认采样率 | `2.4` | 设备支持档位 | MS/s | 设备与后端 | "启动时优先用 2.4 MS/s；设备不支持就自动取最接近的支持值，并在状态栏显示实际值。" | 决策人裁定 3。**内核默认与能力回落已落地**（§10-3）；设置在 schema 里只写首选值，实际值以后端状态为准，不要复制一份"默认 2.4"的字面量。 |
| A11 | `tuning_step_hz` | 调谐步进 | `10 kHz` | 10 Hz–1 MHz | Hz | 频谱与解码 | "与频谱吸附网格同值；滚轮/箭头按它走。" | 已一致（§D-2 撤回），只需把三步（视图吸附 / 工具面 / 默认档）确认共用一处。 |

> **A-备注（受阻项，不重复报）**：Bortle 等级、调谐吸附步进、dB 量程、录音目录
> 已在 `config_schema.py:301-316` 以 LOCKED 登记，理由均成立，本线复核通过。
> 其中 Bortle 的解锁条件唯一：`rf_sky_view` 提供公开 `set_bortle()` 并复用
> `stars.DEFAULT_BORTLE`（本线已把默认值收口到那一个常量）。

**A 类交付说明（给设置线，2026-11-05）**

- **A5 静噪**：默认值/范围**不要**在 schema 里再写一份字面量——从
  `mbdsdr_ai.squelch` 的 `DEFAULT_SQUELCH_DBFS / SQUELCH_MIN_DBFS /
  SQUELCH_MAX_DBFS` 取值；滑杆与 schema 因此永远同源。
  旧配置迁移必须有**一次可见提示**（P-8d），不许静默改意思；同时把
  `main_window._squelch_db` 的 `-80` 换掉（P-8a），否则显示与行为继续分裂。
- **A9 低仰角开关**：默认关；开启后列表的筛选门从 `PASS_MIN_ALT_DEG`
  降到 `VISIBLE_MIN_ALT_DEG`（5°）。文案必须带"默认按峰值 ≥10°"，
  与列表旁的标签同字面量来源（`sat_passes` 常量）。
- **A10 采样率**：schema 默认写 `2.4`，但**实际值以设备能力回落结果为准**，
  状态栏显示实际生效采样率；不要出现"设置 2.4 / 后端 2.048"的分裂（P-8b）。
- **A11 调谐步进**：**更正**——C++ 调谐步进本就是 10 kHz（§D-2 撤回），
  与 Python、与吸附网格三方一致，本项无代码缺口；若做设置项，默认值同样只
  在 schema 定义一处，滑杆/箭头/吸附都读它。

---

## 5. B 类：该锁死（并说明为什么不能暴露）

已加注释或测试钉住的项，测试见 `tests/test_hardcoded_parameter_registry.py`。

| # | 参数 | 为什么不能暴露 |
|---|---|---|
| B1 | 裸眼极限公式（6.5 / 0.35 / 0.06 / 8.0） | 它不是"偏好"，是可见性门。三个消费点（绘制、拾取、星表 LOD）必须同进同出；一旦可配，用户会把"点不中"当成 bug。且 6.5/0.35 对应 Stellarium 同一语义，偏离即失去外部参照。 |
| B2 | 消光模型 `k=0.2, Δm≤3.5, alt≥0.5°` | 物理模型假设 + 数值奇点保护。可配会让"低仰角星突然消失/突然出现"无法复现；0.5° 下限是防 `sin(alt)→0`。 |
| B3 | `CATALOG_DEPTH_MAG=9.0`、`MAG_BIN_WIDTH=0.5` | 星表数据的**物理边界**（bsc5p 最暗 ~9 等）。放大只会让 `limit_magnitude` 声称星表没有的深度，即"画不出却宣称能看到"。 |
| B4 | `DETAIL_REF_FOV_DEG=30`、`MAX_DETAIL_GAIN_MAG=1.5` | 与 Bortle 门叠加。单改任一个都会让同一片天空星数跳变，且无法归因（用户会以为"缩放坏了"）。 |
| B5 | WGS-72 椭球与 `ω_e` | 科学正确性：SGP4 内核用 WGS-72，站心必须同椭球，否则米级系统差；已写在 `orbit.py:14-15` 注释。 |
| B6 | 过境采样步长 `60 s / 2 s / 0.5 s` | 是精度—耗时契约。用户改大→AOS/LOS 时间漂移；改小→24h×N 星预测卡死 UI。不是旋钮。 |
| B7 | 可见/跟踪退出仰角 `5°` | 低仰角信号仍可能可用，提前退出会丢通联；可配会诱导用户"调到 0°"然后困惑于噪声。它与"过境列表峰值门"是两回事，需**改名**而非暴露。 |
| B8 | `READ_BLOCK_SAMPLES=16384` | 与最大 FFT 档绑定（`receive_pipeline.py:46` 注释）；改成非 2 的幂或小于 FFT 会静默降低频谱分辨率。 |
| B9 | 光谱空值哨兵 `-120.0` / C++ `-100.0` | 哨兵不是数据。可配会被误当成"显示下限"，导致把"无数据"画成"很弱的信号"（该仓库反复强调不许造假噪声）。 |
| B10 | dB 轴夹取域（Python `-160..0` / C++ `-140..20`） | 它是数值域边界，不是偏好。C++ 的 `+20 dBFS` 已超"满量程正弦=0 dBFS"的物理解释，需要 C++ 侧先确认归一化基准再谈统一；暴露只会让越界值进入 LUT。 |
| B11 | AI 工具输出截断 `1200/400`、重试 `3/1000/8000`、思考预算 `4096` | 协议层常量：与上下文预算和供应商限流绑定。暴露会让用户把"上下文爆了"归因为"模型不行"。 |
| B12 | 录音索引 `format_version=1` | 格式契约。可配=允许写出旧版本读不懂的索引，直接丢条目。 |
| B13 | 发射授权超时 `180 s` | 安全语义："无人应答=拒绝"。可配会被用来绕过安全网关。 |
| B14 | WFM 导频/RDS 常数、每模式 IF/带宽表 | 标准定义（19 kHz 导频、57 kHz RDS、50 µs 去加重）。偏离即解不出立体声/ RDS。 |

> 另有 8 条"半 B 类"：C++ `kPeakMatchBins/Seen/Miss`、自动量程 24/0.8/4/1.5、
> `kSquelchNfAlpha*`、`connection` 类。它们属于算法内部状态机，暴露没有可解释语义，
> 归 C（集中即可）。

---

## 6. C 类：该集中但不必暴露（本轮已收口 + patch 建议）

### 6.1 本轮已完成的收口（口径未变）

**C-1 裸眼极限星等公式：3 份副本 → 1 个函数**

- 收口前：`stars.py:168`（标量绘制）、`stars.py:270`（向量镜像）、
  `star_catalog.py:201`（星表 LOD）各自写了一遍 `6.5 - 0.35*max(0,int(bortle)-3)`。
  `star_catalog.py` 的 docstring 还写着"与 `stars.star_draw` 完全同一公式"——
  这类"注释承诺单一来源、代码却是副本"最容易漂移。
- 收口后：`stars.naked_eye_limit_mag()` 为唯一实现；`star_catalog` 直接
  `from .stars import naked_eye_limit_mag`（**同一函数对象**，不是同式副本），
  并在 `tests` 里断言 `is` 关系。
- 同时收口：`bortle_dim_factor()`（`0.06` 压暗系数）、
  `MAG_SPAN_TO_FULL_BRIGHT`（`8.0`，原在标量/向量各写一次）、
  `DEFAULT_BORTLE`（原来 6 处签名写 `bortle: int = 3`）。

**口径未变证据**（差分探针 `output/_probe_star_limit_equiv.py`，
用 `git show HEAD:mbdsdr_ai/skyengine/stars.py` 加载旧实现，与本工作树逐点比较）：

```
bortle ∈ {-3,1,2,3,4,5,6,7,8,9,12}
mag    ∈ [-8.0, 14.0]，步长 0.001
limit_diff=0  many_diff=0  scalar_diff=0
total_points=242011
star_catalog.naked_eye_limit_mag is stars.naked_eye_limit_mag: True
```

- `star_draw` / `star_draw_many` 在 **242,011 个 (mag, bortle) 点**上
  返回三元组**逐点 bit-identical**（`np.array_equal`，0 容差）。
- 新增测试：`tests/test_hardcoded_parameter_registry.py`（19 passed；
  含本轮新增的静噪单一常量、过境仰角两常量、跨语言 token 冻结用例）。
- 未改 `rf_sky_view.py` 的 `_bortle = 3`（在途文件）。→ patch 建议 **P-2**。

**C-2 `DEFAULT_BORTLE`**：`stars` 与 `star_catalog` 的 6 处默认参数统一引用该常量，
值恒为 3，签名默认值不变（已验证 `inspect.signature` 行为等价）。

### 6.2 交给对应线的 patch 建议（本线不改）

| # | 文件 | 问题 | 建议 |
|---|---|---|---|
| P-1 | `cpp/src/core/tokens.h`（频谱线已改） | Python `6.0` 与 C++ 原 `15.0` 两套显示峰阈值；原"统一到 15 dB"已被实测驳回 | **裁定：统一检测器定义 `rel 6.0 / abs -100 dBFS / 3 帧确认 / 5 bin 匹配 / 10 帧遗忘`**，两条路径都按这套。Python `6.0` 保留；C++ 新增显示门 `kPeakRelDb = 6.0`（`tokens.h:365`，源码注释已写入 24/24 帧 0 峰、3043 vs 3029 无洪泛证据）。`kPeakThresholdDefault = 15.0` 仍留给**频率校准参考门**，两条门职责不同、禁止合并。数量 `8` vs `12` 登记为"有意差异：UI 显示密度"，不属物理量。 |
| P-2 | `desktop/rf_sky_view.py` | `_bortle = 3` 私有硬编码；`(4.5-mag)/9.0` 在同文件两处 | `from mbdsdr_ai.skyengine.stars import DEFAULT_BORTLE`；提供 `set_bortle()` 后即可解锁设置项。拾取评分提成一个 `_pick_score(mag)` 函数（两处同式）。 |
| P-3 | `desktop/main_window.py` | 状态提示时长 `3000/4000/6000/8000` 散落 20+ 处 | 提到 `desktop/constants.py` 的 `TOAST_SHORT_MS / TOAST_INFO_MS / TOAST_WARN_MS / TOAST_ERROR_MS` 四档；`desktop/constants.py` 已有互斥钩子测试，按同法登记。 |
| P-4 | `desktop/main_window.py` | 刷新周期 `50/150/1000/2000/5000 ms` 散落 | 同上，收成 `UI_FPS`、`BUS_COALESCE_MS`、`STATUS_REFRESH_MS`、`SKY_REFRESH_MS`。 |
| P-5 | `desktop/receive_pipeline.py` | `-80.0` 固定静噪默认 | **已完成**（§10-1 二次裁定）：改为 `DEFAULT_SQUELCH_DBFS`，WFM 与窄带统一走 `BaselineSquelch`（相对该模式噪声基线的余量，不回 IQ 功率）。 |
| P-6 | `cpp/src/dsp/spectrum_engine.cpp` | `kChunk = 8192` 在两处函数里各写一次 | 提到文件级 `constexpr std::size_t kReadChunk`。 |
| P-7 | `mbdsdr_ai/skyengine/star_catalog.py` | `0.5 * log2(...)` 的斜率字面量 | 提成 `DETAIL_GAIN_SLOPE = 0.5`（与 `MAX_DETAIL_GAIN_MAG` 同段）。 |
| P-2b | `desktop/rf_sky_view.py` | 私有 `PASS_MIN_ALT_DEG = 10.0` 字面量 | 改为 `from mbdsdr_ai.sat_passes import PASS_MIN_ALT_DEG`；与 `sat_track_panel` / `new_spacetime_panel` 共用同一常量。星象仪的 `PASS_COARSE_STEP_S/FINE_STEP_S` 属 D-6，**不要**顺手合并。 |
| P-2c | `mbdsdr_ai/sat_consistency.py:141` | `check_next_pass_consistency(min_alt=5.0)` 仍把 5 传进默认 10 的 `predict_passes` | 这正是裁定 2 禁止的形态（"下一次过境"却用可见门）。改默认 `PASS_MIN_ALT_DEG`，或明确改名为"从 5° 起算的下一次过境"并加注释。**诊断工具口径需与 `next_pass()` 一致，否则对拍结论不可信。** |
| P-8a | `desktop/main_window.py` | `_squelch_db = -80.0`（约 507 行）是权威初值，滑杆已显示 -50 | 改为 `DEFAULT_SQUELCH_DBFS`（一行）。**必须与 P-8d 迁移提示同批**：否则旧配置读入后仍按 -80 门控，UI 显示与行为继续分裂。 |
| P-8b | `mbdsdr_ai/sdr_backend.py` + `osmosdr_source.py` + `rtltcp_client.py` | RTL 基线三套（2.048 / 1.024 / 2.048 硬编码） | **已完成**（§10-3）：统一为 2.4 MS/s 首选 + `nearest_sample_rate()` 回落 + 状态回报实际值；三处不再各写字面量。相关测试期望已成对更新（100 passed）。 |
| P-8c | `desktop/spectrum_widget.py:419` | 无数据哨兵 `-120.0` 落在可显示量程内 | 改为 `float("nan")`（绘制/重采样对 NaN 显式判空）。C++ `rssiDbfs_{-100.0f}` 是**真实安静读数**，不得当哨兵；若要哨兵请用量程外的 `-999` 并注释。 |
| P-8d | 设置线（`desktop/config_schema.py`） | 旧配置静噪值按新口径解释 | 读入旧 key 时若值落在 `-120..-101`（旧量纲域外沿）或明显偏离新域，弹一次提示："此静噪值按旧量纲（IQ 功率）保存，现在滑杆表示'相对该模式噪声基线的余量'；请复核。"**不许静默改意思**。 |

### 6.3 暂缓（需要跨线决策，本线不动）

- ~~**D-3 FFT 档位**~~ **已收口（2026-11-05）**：`FFT_SIZES` 已删掉 8192/16384，
  与 schema / C++ / AI 三档逐项一致；旧值回落最近档 4096 并记录原因
  （§2.4 D-3）。
- **D-6 过境预测两套采样**：`rf_sky_view` 为 UI 流畅用 300 s/30 s，
  `sat_passes` 为精度用 60 s/2 s。**不许合并成一个数**——正确做法是
  `sat_passes` 暴露 `coarse_step_s` 参数，星象仪显式传 300，并在文档写清
  "星象仪采用低精度档"。当前星象仪那套是私有实现，建议后续改为调用
  `predict_passes(coarse_step_s=300)`，保证只有一个算法。
- **D-10 AI 上下文**：Python 8192 / C++ 24576 是**不同模型**的合理假设
  （Qwen2.5-7B 32k vs 本地小模型）。不建议强行统一，登记为"按模型能力取默认"。

---

## 7. 跨语言对齐（Python ↔ C++）

不共享常量。两侧各自定义，文档登记，测试对拍。

| 语义 | Python | C++ | 是否一致 | 处置 |
|---|---|---|---|---|
| **dB 轴下限** | `-160.0`（`spectrum_widget.py:916`） | `-140`（`tokens.h:325`） | ✗ | 登记；C++ 侧归一化基准待确认（B10） |
| **dB 轴上限** | `0.0`（`spectrum_widget.py:917`） | `20`（`tokens.h:328`） | ✗ | 同上 |
| **dB 默认下限** | `-100.0`（`spectrum_widget.py:840`） | `-45`（`tokens.h:329`） | ✗（基准相同） | 视窗默认交自动量程（§2.4 D-4 结论）；夹取边界各自锁死 |
| **FFT 档位数量** | 5 | 3 | ✗ | UI 从能力派生（P-1 关联） |
| 默认 FFT | 2048 | 2048（`spectrum_engine.h:884`） | ✓ | 保持 |
| 静噪范围 | `-100..-20`（`squelch.py`） | `-100..-20`（`tokens.h:394-395`） | ✓（量纲不同：C++ 仍是绝对 dB） | 见 2.1 二次裁定 |
| 静噪默认 | `-50`（`squelch.py`） | `-50`（`tokens.h:396`） | ✓ | 见 2.1 二次裁定 |
| **峰值门限基准** | `median + rel_db`（`spectrum_widget.py:487-488`） | `dB above median`（`tokens.h:365`） | ✓（同一基准） | 统一检测器定义（P-1） |
| 峰值门限值 | `6.0`（裁定值） | `6.0`（`kPeakRelDb`，显示门；校准门 15 另计） | ✓ | 裁定 `rel 6.0 / abs -100 / 3 帧 / 5 bin / 10 帧`；15 dB 实测 24 帧 0 峰 |
| 默认采样率 | 2.4 MS/s | 2.4 MS/s（`tokens.h:301`） | ✓ | 已统一；两端都按设备能力回落 |
| 默认调谐步进 | 10 kHz | 10 kHz（`kFreqCoarseStepHz`） | ✓（D-2 撤回） | 保持；100 kHz 是扫描步进 |
| 无数据哨兵 | `-120.0`（待改 NaN） | 无哨兵（`-100` 是真实读数） | ✗ → 待 P-8c | 哨兵必须落在量程外 |
| 上下文窗口 | 8192 | 32768（×0.75 预算） | ✗（有意） | 按模型能力，登记 |

对拍测试：`tests/test_hardcoded_parameter_registry.py::test_cpp_tokens_match_documented_values`
把 C++ 侧 15 个 token 的**当前值**冻结；任何一侧改数先红，强制回到本表。
新增的显示门 `kPeakRelDb=6.0` **尚未纳入冻结表**（`kPeakThresholdDefault`
仍冻结为 15.0，校准门未变，所以不会红）——建议频谱线提交 `tokens.h` 时
同批把 `kPeakRelDb` 加进映射，否则这把锁看不到显示门的改动。

---

## 8. 裁定状态（2026-11-05 决策人已拍）

| # | 议题 | 状态 |
|---|---|---|
| 1 | 静噪门限统一到「相对该模式噪声基线的余量」（刻度 `-100..-20`，默认 `-50` ≈ 6 dB 余量） | **已落地**（§10-1）。剩余：`main_window` 初值 `-80` 需 1 行 patch（P-8a），`gain_db` 写入需设置线接 1 行。 |
| 2 | 过境仰角拆成 `VISIBLE=5°` / `PASS=10°`，`next_pass()` 用 10° | **已落地**（§10-2）。剩余：`rf_sky_view` 的 `PASS_MIN_ALT_DEG` 改引用常量（P-2b）。 |
| 3 | 默认采样率 2.4 MS/s（按设备能力回落） | **已落地**（§10-3）：内核默认 2.4M + `nearest_sample_rate()` 回落 + 状态回报实际值；三套 RTL 基线收口；`test` 100 passed。 |
| 3b | 调谐步进统一 10 kHz | **前提不成立，撤回**（§D-2）：C++ 调谐步进本就是 `kFreqCoarseStepHz=10 kHz`；100 kHz 是扫描步进。 |
| 4 | 峰值 6 dB vs 15 dB | **裁定：统一检测器定义 `rel 6.0 / abs -100 dBFS / 3 帧 / 5 bin / 10 帧`**；Python 6.0 保留，C++ 显示门新增 `kPeakRelDb=6.0`（校准参考门 15 保留不合并）。15 dB + 绝对底 = -85 dBFS，+8 dB 弱载波 -92 dBFS → 24 帧 0 峰，故不取 15。 |
| 5 | "无数据"哨兵 | **结论：改用 NaN/None，禁止用落在量程内的 dB 数**；改 Python 侧 → P-8c。C++ `-100` 是真实安静读数，加注释区分。 |


---

## 9. 已做 / 未做

已做（本线，可提交）：
- `mbdsdr_ai/skyengine/stars.py`、`star_catalog.py`：公式单一事实源（C-1、C-2）。
- `mbdsdr_ai/squelch.py` + `desktop/receive_pipeline.py` +
  `desktop/control_panel.py`：
  静噪统一到「相对该模式噪声基线的余量」+ 自动标定 + 失败可见（§10-1 二次裁定）。
- `mbdsdr_ai/sat_passes.py` + `sat_tracker.py` + `desktop/sat_track_panel.py` +
  `desktop/new_spacetime_panel.py` + `mbdsdr_ai/core/controller.py`：
  过境仰角两常量 + `next_pass()` 默认 10° + 列表筛选提示（§10-2）。
- `mbdsdr_ai/sdr_backend.py` + `desktop/constants.py` +
  `mbdsdr_ai/osmosdr_source.py` + `mbdsdr_ai/rtltcp_client.py`：
  默认采样率统一 2.4 MS/s + 设备能力回落 + 三套 RTL 基线收口（§10-3）。
  测试期望成对更新：7 个受影响套件 100 passed。
- `tests/test_hardcoded_parameter_registry.py`：单一来源断言 + B 类锁定值 +
  跨语言对拍 + 静噪/仰角常量断言（19 passed）。
- 本文档。

未做（不属本线或需授权）：
- `desktop/main_window.py` / `rf_sky_view.py` / `spectrum_widget.py` /
  `cpp/src/core/tokens.h` 的任何改动 → §6.2 patch
  （P-1（含新增显示门 `kPeakRelDb=6.0`）、P-2/P-2b/P-2c、P-8a、P-8c）。
- 旧配置静噪迁移提示：设置线 `config_schema.py` → P-8d。
- 推送到远端（本线全程不推）。

---

## 10. 落地记录：口径变了什么 / 没变什么

### 10-1 静噪重做：从「绝对音频 RMS」到「相对噪声基线余量」

**背景（决策人反证）**：上一版把滑杆统一到解调后音频 RMS 后，纯噪声下固定
`-50` 永远不关（FM 纯噪声 `-14.54 dBFS`、NFM `+8.98 dBFS`）。一个不关的静噪
等于没有静噪，所以「音频 RMS」不能当跨调制族的同一标定量。

**口径变了（有意改变行为，非等价收口）**

| 项 | 改前（首版） | 改后（二次裁定） |
|---|---|---|
| 滑杆语义 | 绝对音频 RMS（dBFS） | **相对该模式噪声基线的余量**（刻度不变：`-100..-20`，默认 `-50`） |
| 刻度换算 | 直接比较 dBFS | `-100→0 dB`、`-50→6 dB`、`-20→16 dB`（`margin_threshold_db`） |
| 噪声基线 | 无（固定门限）/ `AutoSquelch` 跟踪 | `BaselineSquelch` 自动标定（`calib_frames=8` 帧中位数） |
| 各模式测法 | 全部音频 RMS | 带内**谱峰/谱中位**：FM/WFM/NFM/AM 用 300–5000 Hz，USB/LSB 用 300–3000 Hz，CW 用 300–5000 Hz |
| 标定时机 | 无 | 模式切换、`SharedDemodConfig.gain_db` 变化、管线重建（采样率变化） |
| 失败状态 | 无 | `calibration_failed` + `squelch_state` 信号 + `log.warning`（不套默认值） |
| WFM | 全带宽 IQ 功率 / NoiseSquelch | 与窄带同一套音频存在度口径（**不回 IQ 功率**） |
| UI 文案 | "静噪(音频)"、dBFS | "静噪门限"，写明"相对噪声基线余量 / 基线自动标定" |

**为什么不用裁定里举例的两条测法**（实测反证，不是口味）：

| 候选 | 噪声 | 弱可读 | 结论 |
|---|---|---|---|
| FM 音频高通段（>3 kHz）占比 | `-0.73 dB` | `-0.12 dB` | 不可分（差 0.6 dB） |
| NFM 音频 RMS | `+8.98 dBFS` | `+8.62 dBFS` | **反向**（噪声比弱信号还高） |
| 带内谱峰/谱中位 | `+5.6~19.6 dB` | 余量 `+6.9~+41.9 dB` | 可用，且尺度无关 |

**验收矩阵实测**（`tests/test_squelch_baseline.py`，默认 `-50`＝6 dB 余量；
合成源 48 kHz、块长 384 样本；FM 弱可读取 20 dB CNR——10 dB 时 FM 解调音频
本身不可读，余量仅 `+4.5 dB`）：

| 模式 | 纯噪声（开门帧占比） | 弱可读（开门帧占比） | 强信号（开门帧占比） | 弱可读余量读数 1×→2×（+6 dB） |
|---|---|---|---|---|
| AM | **0 %** | 100 % | 100 % | `+14.53 → +14.53`（Δ`0.00`） |
| USB | **0 %** | 100 % | 100 % | `+18.96 → +18.96`（Δ`0.00`） |
| LSB | **0 %** | 100 % | 100 % | `+19.09 → +19.09`（Δ`0.00`） |
| NFM | **0 %** | 100 % | 100 % | `+41.86 → +41.86`（Δ`0.00`） |
| FM | **0 %** | 75 %（`+7.42` 余量，边缘可读） | 100 % | `+7.42 → +7.42`（Δ`0.00`） |
| CW | **0 %** | 100 % | 100 % | `+19.37 → +19.37`（Δ`0.00`） |

- 纯噪声那条（含 FM）全部 **0 %**：反证由红转绿（旧口径 FM 纯噪声判"开"，
  新口径判"关"）。
- 单帧毛刺用「连续 2 帧确认」压掉；弱可读帧间抖动由生产环境的 250 ms hang 保持。
- 增益 +6 dB：6 模式读数变化均 `0.00 dB`（存在度是比值，尺度无关），
  保持首版那档指标；`gain_db` 变化仍触发重标定（设置线写入时生效）。

**口径没变**

- 音频链（解调 → 门控 → AGC → `audio_tap` → `player.write`）顺序与内容未动；
- `mbdsdr_ai/analog_demod.py` 的无头 IF 域门控（`_MODE_DEFAULTS` 的
  `-40/-30/-60`、构造初值 `-150.0`）**保留不动**——gqrx 兼容路径的另一个测量点；
- C++ 侧 `kSquelchMinDb/-100`、`kSquelchMaxDb/-20`、`kSquelchDefaultDb/-50`
  **未改**（C++ 仍是绝对 dB 域，量纲差异已登记 §7）；对拍测试继续冻结它们。

**行为差异说明（必须让用户知道）**：同一段信号，旧值 `-80` 在新口径下
不再等价；迁移仅做"读入 + 提示"，不自动换算（IQ 功率↔音频域存在度的差值
取决于增益/带宽/解调器，无法可靠换算）。→ P-8d。

### 10-2 过境仰角：`VISIBLE_MIN_ALT_DEG=5` / `PASS_MIN_ALT_DEG=10`

**口径变了**

| 项 | 改前 | 改后 |
|---|---|---|
| `next_pass()` 默认 | `DEFAULT_MIN_ELEVATION_DEG`（5°） | `PASS_MIN_ALT_DEG`（10°） |
| 5–10° 过境 | 会出现在"下一次过境" | **不再返回**（峰值 <10°） |
| AOS/LOS 定义 | 对 `next_pass` 调用按 5° 穿越 | 按 10° 穿越（过境时长相应变短） |
| 时空面板列表 | 无说明 | 标题旁"列表按峰值 ≥10° 筛选（更低的不列出；可在设置开启低仰角）" |
| 空状态文案 | "24 小时内无过境" | "24 小时内无过境（峰值仰角 ≥10°）" |
| 控制器 | 固定 10° / 24 h 字面量 | **已改为显式入参注入**：`get_satellite_pass(min_elevation_deg=None, window_hours=None)`，缺省源 `sat_passes.PASS_MIN_ALT_DEG` / `sat_passes.PASS_WINDOW_HOURS=24.0`，controller 内已无 10/24 字面量 |

证据：`mbdsdr_ai/sat_passes.py:101-102`；`predict_passes:226` 与批量
`:616` 默认 `PASS_MIN_ALT_DEG`；`sat_tracker.py:118`
`DEFAULT_MIN_ELEVATION_DEG = VISIBLE_MIN_ALT_DEG`、`next_pass` 默认
`PASS_MIN_ALT_DEG`；`desktop/new_spacetime_panel.py` 筛选标签 +
`min_elevation=PASS_MIN_ALT_DEG`；`mbdsdr_ai/core/controller.py:990`
（显式入参，controller 内无 10/24 字面量）。

外泄面是 **MCP schema**（`min_elevation_deg` / `window_hours` 入参 + 返回值里
的生效值）与**验收脚本**，**不是桌面 UI**。实测：5°/24h = 6 条、10°/24h = 5 条、
10°/72h = 14 条。

**口径没变**

- 可见性/跟踪退出仍以 5° 为准（`DEFAULT_MIN_ELEVATION_DEG = 5`）；
- `predict_passes` 的粗扫 60 s / 细扫 2 s / 二分容差、轨道传播全部未动；
- 显式传 `min_alt=5` 的调用方行为不变（只是默认值变了）；
- 星象仪 `PASS_MIN_ALT_DEG=10.0` 数值不变（仍是私有字面量 → P-2b）。

**残留（已登记，未改）**：`mbdsdr_ai/sat_consistency.py:141`
`check_next_pass_consistency(min_alt=5.0)` 仍是"5 传进默认 10"的形态。
→ P-2c。诊断工具不先对齐，它对"下一次过境"的对拍结论就不可信。

### 10-3 默认采样率：已落地 2.4 MS/s + 能力回落

**口径变了**

| 项 | 改前 | 改后 |
|---|---|---|
| 内核统一默认 | `2_048_000`（"1.024M 整数倍"设计选择） | `2_400_000`（`sdr_backend.DEFAULT_SAMPLE_RATE_HZ`，且注释改为"首选，不是保证"） |
| desktop fallback | `2_048_000` | `2_400_000`（仅 mbdsdr_ai 缺失时的安全回退） |
| `osmosdr_source` RTL 默认 | `1_024_000` | `2_400_000`（第三套 RTL 基线收口） |
| `RtlTcpBackend` 默认 | `2_048_000` 硬编码 | 引用 `DEFAULT_SAMPLE_RATE_HZ`，不再另写字面量 |
| 设备不支持时 | 无回落说明 | `nearest_sample_rate()` 取最接近支持档；`status.sample_rate_hz` 回报**实际值** |

**口径没变**

- 各后端自己的"甜点"默认未动（HackRF 8M、USRP 1M、Pluto 2M，测试仍钉住）；
- `SAMPLE_RATES` 11 档明细未动（2.4M 本来就在档内，RTL 全系支持，故通常不触发回落）；
- 数字链路（ADS-B 等）按自己声明的采样率工作，不读这个默认；
- 调谐步进：**本来就没有缺口**——Python 10 kHz、C++ `kFreqCoarseStepHz=10000`、
  频谱吸附 `snap_interval=10_000` 三方一致（D-2 撤回）；`kFreqStepHz=100 kHz`
  是 `start_scan_link` 的扫描步进，另一个概念，未动。

**验证**：`tests/test_desktop_constants.py`、`test_benchmark_essence_defaults.py`、
`test_fake_rtl_chain.py`、`test_rtltcp_client.py`、`osmosdr_source_test.py`、
`test_startup_sequence.py`、`test_retune_transition.py` → **100 passed**。
其中 `FakeRtlSdr` 的出厂默认仍是 2.048M（模拟真实设备的出厂态），
恰好证明"默认请求 2.4M、设备状态如实回报"两件事分开。

### 10-4 峰值判定：统一检测器定义（`rel 6.0 / abs -100 / 3 / 5 / 10`）

- 核查证据：Python `floor=np.median(谱)` + `thresh=floor+rel_db`
  （`spectrum_widget.py:487-488`）；C++ 注释 "dB above median"
  （`tokens.h:365`）。**同一基准**，不是两个产品概念。
- **裁定（取代旧的"统一到 15 dB"）**：统一**检测器定义**，不是把数字凑一样——
  `rel 6.0 dB / abs -100 dBFS / 3 帧确认 / 5 bin 匹配 / 10 帧遗忘`，两条路径
  （Python 与 C++）都按这套。Python `6.0` 保留；C++ 显示门改用
  `kPeakRelDb = 6.0`（`tokens.h:365`）。C++ 的 `kPeakThresholdDefault = 15.0`（`:371`）是
  **频率校准参考门**，职责不同，有意保留、禁止合并。
- **驳回 15 dB 的实测证据（频谱线，编译后 C++）**：15 dB 相对门叠加
  `-100 dBFS` 绝对底后等效门 ≈ `-85 dBFS`；`+8 dB` 弱载波的峰为
  `-92 dBFS`，**24/24 帧 0 峰**，连候选峰都进不了。15 dB 不是更严格，是弱信号隐身。
- **6 dB 无洪泛反证**：C++ 同一场景 6 dB 下 **3043 vs 3029 峰次**
  （差异 <0.5 %）；防抖靠 3 帧确认 / 5 bin 匹配 / 10 帧遗忘。
- 数量 8 vs 12 属显示密度，登记为"有意不同"，不是物理量。
- 本线未改代码（`spectrum_widget.py` / `tokens.h` 归频谱线）→ P-1；
  本线只负责把结论与证据写进本文档，且**不保留"应改 15"的过期裁定**。

### 10-5 "无数据"哨兵：结论 NaN/None，禁止量程内魔法数

- Python `-120.0`（`spectrum_widget.py:419`）落在可显示 `-160..0` 内，
  真实 `-120 dB` 与"无数据"无法分辨 → 诚实性红线。
- 结论：Python 改 `NaN`（渲染层 `isfinite` 判空已有基础：
  `np.where(np.isfinite(spec), ...)`），**不再用 dB 数字**。
- C++ `rssiDbfs_{-100.0f}` 是安静带默认**读数**（真实值），不是哨兵；
  若未来需要哨兵，必须落在量程外（如 `-999`）并注释。
- 本线未改（在途）→ P-8c。

### 10-6 dB 默认量程：结论"基准相同，默认视窗交自动量程"

- Python `-100..-20` 与 C++ `-45..0` 都是 dBFS（满量程正弦 = 0 dBFS），
  差异是视窗跨度/位置，不是测量点分歧（详见 §2.4 D-4 结论）。
- 不强行统一成一个数；默认视窗应由自动量程/首帧噪底决定，
  两端各自锁死**夹取边界**（Python `-160..0`、C++ spin `-140..20`）。
- 本线零改动。

### 10-7 验证

- `python -m py_compile` 通过（改动文件全量）。
- `pytest tests/test_hardcoded_parameter_registry.py tests/test_satellite_tracker.py
  tests/test_sat_tracker.py tests/test_new_spacetime_timeline.py
  tests/test_audio_chain.py -q` → **69 passed**；
  注册表单文件 **19 passed**。
- 采样率变更套件（7 个文件）**100 passed**（§10-3）；`FakeRtlSdr` 返回值仍
  如实反映设备出厂态 2.048M。
- 差分探针（星等收口）`242,011` 点 `array_equal` 全等（§6.1）——那是
  **唯一**一处"口径未变"的集中，本轮 10-1/10-2 是**有意改口径**，
  不用等价性伪装。静噪改口的影响用差分读数给出：同一合成源 `gain ×2` 时
  旧 WFM/窄带读数各涨 `+6.02 dB`；二次裁定后的存在度余量 6 模式均为
  `0.00 dB`；旧口径纯噪声两端差 `21.64 dB`（≈ 10log10(2.048M/15k)），
  新口径不再随带宽改变含义。静噪验收矩阵见 §10-1（6 模式 × 3 场景全绿）。

### 10-8 「五套数」残留核查（2026-11-05 二次核）

**结论**：PyQt 桌面**生产路径（`ReceivePipeline` + `BaselineSquelch`）已无**固定
绝对 dBFS 阈值；桌面仅剩一处**旧单线程演示路径**（`main_window.py:5535`，已知 P-8a）。
其余绝对 dBFS 门全部在**另一条引擎 / AI 后端**里，各有自己的测量点，不属于
"一根滑杆两个测量点"。逐处原文与处置：

| # | 文件:行 | 原文（节选） | 量纲 / 归属 | 处置 |
|---|---|---|---|---|
| 1 | `mbdsdr_ai/analog_demod.py:123-131` | `"am" ... sql_db=-40.0` / `"wfm" ... sql_db=-30.0` / `"usb"/"lsb"/"cw" ... sql_db=-60.0` | 信道 **IQ 功率 dBFS**（无头 gqrx 兼容路径，AGC 前） | **保留**（另一概念，不受桌面滑杆驱动）；量纲注释已改为"桌面滑杆 = 相对噪声基线余量"（`:119-122`） |
| 2 | `mbdsdr_ai/analog_demod.py:230` | `self.squelch_db = -150.0` | 同上；构造初值 = 常开 | **保留**（测试钉 `tests/test_benchmark_essence_defaults.py:204`） |
| 3 | `mbdsdr_ai/analog_demod.py:236` | `self.last_signal_power_db = -150.0` | 同上；首块功率估计占位 | **保留** |
| 4 | `mbdsdr_ai/analog_demod.py:365-367` | `if not self._sql_open and self.last_signal_power_db >= self.squelch_db:` / `elif ... < self.squelch_db - self._sql_hyst_db:` | 同上；IF 域滞回门 | **保留**；不读桌面 `SharedDemodConfig.squelch_db` |
| 5 | `mbdsdr_ai/analog_demod.py:458-459` | `chain.set_squelch_db(-150.0)` | 同上；一次性工具调用全开 | **保留** |
| 6 | `cpp/src/core/tokens.h:394-396` | `kSquelchMinDb=-100` / `kSquelchMaxDb=-20` / `kSquelchDefaultDb=-50`，注释 "AUDIO-BLOCK RMS dBFS domain" | C++ 48 kHz **解调音频块 RMS 绝对 dBFS** | **保留**；auto 门 = 同域噪声底 + `kSquelchAutoMarginDb=8.0`（`spectrum_engine.cpp:1892-1896`），是"绝对底+余量"，与 Python 滑杆余量不同量纲（§7 已登记） |
| 7 | `cpp/src/dsp/spectrum_engine.h:951` | `float squelchThreshold_ = -50.0f;` | 同上；token 默认值的缓存镜像 | **保留** |
| 8 | `desktop/main_window.py:567` | `self._squelch_db: float = -80.0` | 旧单线程演示路径的**绝对 IQ dBFS** 门 | **P-8a**（操作逻辑线，本线不动） |
| 9 | `desktop/main_window.py:5534-5535` | `dbfs = 10.0 * math.log10(p + 1e-12)` / `if dbfs < self._squelch_db:` | 同上（全带宽 IQ 功率） | **P-8a**；多线程 `ReceivePipeline` 路径已改余量 |
| 10 | `mbdsdr_ai/receive_chain.py:153` + `:239-243` | `squelch_db: float = -150.0` → `Squelch(threshold_db=self._squelch_db, ... sample_rate=if_sr)` | AI 后端（`sdr_backend.py:159/646`）**IF RMS 绝对 dBFS** | **保留**；另一后端链，工具面 `sdr_tools.py:654` 描述 "静噪阈值，单位 dB。-100=关闭静噪" |
| 11 | `mbdsdr_ai/gated_recorder.py:252-257` | `AutoSquelch(margin_db=self.cfg["margin"], ...)` / `NoiseSquelch(...)` | 音频域**自跟踪噪声底 + 余量** / 频谱形态判据 | **保留**；不是固定绝对阈值 |
| 12 | `mbdsdr_ai/agent.py:1796-1813` | `th = float(args.get("threshold_db", -50.0))`，描述 "门控阈值 dBFS" | `sdr_squelch_gate` 对**传入 IQ** 按块 RSSI 判门 | **保留**；独立工具，不驱动桌面静噪 |
| 13 | `mbdsdr_ai/core/controller.py:212/447` | `self._squelch_db: float = -150.0` / `self._squelch_db = float(db)` | 只写不读的死参数 | **待清**（删除或接到真实链路；不属本笔） |
| 14 | `desktop/status_panel.py:601` | `self.rx_squelch.setText(f"{squelch:.0f} dBFS")` | 读的是 `backend.status.squelch_db`（`sdr_backend.py:159`，绝对 dBFS），**不是**滑杆余量 | **保留**（来源与单位一致）；不得把滑杆值喂进这里 |
| 15 | `desktop/main_window.py:4145` | `# 本地声卡门控：信号低于此 dBFS 时静音` | 注释仍写绝对 dBFS；写入值已是余量刻度 | **随 P-8a 改注释**（行为由 `receive_pipeline` 的 `BaselineSquelch` 执行） |

**只是"被调用"但语义已是新口径的入口（从哪读）**：

- `desktop/control_panel.py:597`（`setValue(int(DEFAULT_SQUELCH_DBFS))`）与 `:866`
  （`squelch_changed.emit(float(dbfs))`）：滑杆只发刻度值；
- `desktop/main_window.py:4148-4155` → `SharedDemodConfig.squelch_db`；
- `desktop/receive_pipeline.py:62/287-296`：读 `_cfg.squelch_db` →
  `BaselineSquelch.set_threshold()` → `margin_threshold_db()`，**没有任何地方再与绝对 dBFS 比较**；
- `desktop/config_schema.py:195-200`：`peak_threshold_db` 默认 6.0 / 范围 1–30
  与 `spectrum_widget.py:1005` 同值（`source` 指针行号待设置线修正）；
- `desktop/config_schema.py:225-230`：`squelch_default_db` 的 default/min/max
  从 `mbdsdr_ai/squelch.py` 常量派生（无重复字面量）。

因此"五套数"在**桌面生产路径**上的残留为 **0**；旧单线程路径 1 处挂在 **P-8a**，
表中 1-7、10-14 是**明确登记的另一些测量点/死参数**，处置如上，不冒充已收口。

### 10-9 给设置线的 `gain_db` 接线（一行，可照抄）

- **谁在读**：`desktop/receive_pipeline.py` 的 `DemodWorker.run()`
  （`:365-368`）每帧比较 `float(self._cfg.gain_db)` 与 `_last_gain_db`；
  一旦变化即 `self.recalibrate()` → `BaselineSquelch.reset()` 重新标定
  （标定期 8 帧保持开门，不会因换档误静音）。
- **谁该写**：设置线，在 `desktop/main_window.py` 的
  `_on_gain_changed(self, db: int)`（`:4131`）里写。
- **写在哪**：增益**真实成功**之后（即 `settings.set("gain_db", float(db))`
  之后、函数末尾；勿放在 `_apply_hw_action` 的早退分支之前）。
- **一行代码**：

```python
        if self._demod_cfg is not None:
            self._demod_cfg.gain_db = float(db)
```

- **期望行为**：除"静噪基线重标"外无任何行为变化；换增益后的下一帧开始
  8 帧标定，随后按新噪声底算余量。若暂未接线，存在度指标尺度无关
  （+6 dB 增益 Δ0.00 dB，`tests/test_squelch_baseline.py::test_gain_step_does_not_move_reading`），
  读数不会漂；但基线跟踪会把旧档残余底带进新档，接线后自动消除。
- **可选（启动种子）**：`main_window.py:583` 构造 `SharedDemodConfig` 时补
  `gain_db=float(self.settings.get("gain_db", 0.0))`，避免启动后第一次改增益
  "凭空"触发一次重标。

