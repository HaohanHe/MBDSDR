# 硬编码参数普查与裁定

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
在两个前端不可能一致。**裁定：不得静默统一成某一个值。**
需要先确定"这根滑杆到底标定哪个测量点"，再决定默认值与范围。
→ A 类待定项 A4，见 §4。`-150.0` 已有测试钉住
（`tests/test_benchmark_essence_defaults.py:204`），保持。

### 2.2 卫星"下一次过境"的最低仰角：5° 与 10° 🔴 P1

| 位置 | 值 | 角色 |
|---|---|---|
| `mbdsdr_ai/sat_tracker.py:115` | `DEFAULT_MIN_ELEVATION_DEG = 5.0` | 类级默认 |
| `mbdsdr_ai/sat_tracker.py:569-591` | `next_pass(min_alt=DEFAULT_MIN_ELEVATION_DEG)` | **把 5° 传进 `predict_passes`** |
| `mbdsdr_ai/sat_passes.py:214`（及 `:604`） | `min_alt: float = 10.0` | 预测器自身默认 |
| `desktop/rf_sky_view.py`（`PASS_MIN_ALT_DEG`） | `10.0` | 星象仪过境列表的**峰值仰角**门 |
| `desktop/new_spacetime_panel.py:524` | `5.0` | 时空面板过境时间线 |
| `mbdsdr_ai/core/controller.py:1072` | `min_alt=10.0` | 控制器 |
| `mbdsdr_ai/agent.py:1554` | `min_elevation=10` | AI 工具默认 |

这不是"两个合理默认"，而是**同一功能两条调用链拿到两个门**：
`tracker.next_pass()` 走 5°，直接 `predict_passes()` 走 10°。
结果：同一颗卫星、同一站址，"下一次过境"在星象仪/时空面板/控制器里可能给出不同条目。

**裁定：语义要拆成两个名字，不许一个名字两个数。**
- `VISIBLE_MIN_ELEVATION_DEG = 5.0`：**当前可见 / 跟踪退出**（低仰角信号仍可能可用，5° 合理）；
- `PASS_MIN_ELEVATION_DEG = 10.0`：**过境预测/列表的峰值仰角门**（太低的过境不值得工作，10° 合理）。
- `next_pass()` 应改用后者；`is_visible()` 继续用前者。

这会改变 `next_pass()` 的输出（5–10° 的过境会消失），属于**行为变更**，
按铁律本线不擅自改 → 报给决策人（§8 待裁定 1）。已有测试需同步补"5–10° 过境被过滤"的反证。

### 2.3 峰值判定：6 dB / 最多 8 个 vs 15 dB / 最多 12 个 🟠 P2

| 位置 | 值 | 语义 |
|---|---|---|
| `desktop/spectrum_widget.py:859` | `peak_rel_db = 6.0` | Python 峰阈值 |
| `desktop/spectrum_widget.py:442` | `max_peaks: int = 8` | Python 峰数量 |
| `desktop/config_schema.py:166` | `peak_threshold_db` 默认 6.0，范围 1–30 | 已经暴露（A） |
| `cpp/src/core/tokens.h:352-354` | 默认 `15.0`，范围 5–40，注释 "dB above median" | C++ 峰阈值 |
| `cpp/src/ui/spectrum_display.cpp:58` | `kMaxPeakCount = 12` | C++ 峰数量 |

同一功能、同一种谱，两个前端标出的峰不一样。注意两者基准也未必相同：
Python 是 `floor + rel_db`（`spectrum_widget.py:488`，floor 为分位数），
C++ 注释写 "above median"。
**裁定：阈值属 A（已暴露），默认值必须以同一基准重新标定后再统一；
数量上限属 C（收成常量即可，不暴露）。** 无权改 `spectrum_widget.py` → patch 建议 P-1（§6）。

### 2.4 其余"两套数"（次严重，P2/P3）

| # | 语义 | 两套值 | 位置 |
|---|---|---|---|
| D-1 | 默认采样率 | `2.048 MS/s` vs `2.4 MS/s` | `config_schema.py:260` vs `tokens.h:301` |
| D-2 | 调谐步进默认 | `10 kHz` vs `100 kHz` | `control_panel.py:323-328` vs `tokens.h:283` |
| D-3 | FFT 档位数量 | 5 档（含 512/8192/16384） vs 3 档 | `spectrum_widget.py:141,1977-1979` vs `spectrum_engine.cpp:163` |
| D-4 | dB 默认量程 | `-100..-20` vs `-45..0` | `spectrum_widget.py:916-917` + 默认值 vs `tokens.h:329-330` |
| D-5 | 瀑布行数 | 固定 `256` vs 可选 `128..4096` | `spectrum_widget.py:161` vs `tokens.h:698` |
| D-6 | 过境预测采样 | 粗 `60 s` / 细 `2 s` vs 粗 `300 s` / 细 `30 s` | `sat_passes.py:88-90` vs `rf_sky_view`（`PASS_COARSE_STEP_S`/`PASS_FINE_STEP_S`） |
| D-7 | "光谱空值"哨兵 | `-120.0` vs `-100.0` | `spectrum_widget.py:419` vs `tokens.h:355` / `spectrum_engine.cpp:1748` |
| D-8 | 状态提示时长 | `3000/4000/6000/8000 ms` 散落 20+ 处 | `main_window.py:2061,2490-2624` 等 |
| D-9 | AI 工具重试 | 2 次 / 0.4 s vs 3 次 / 1 s→8 s | `agent.py:4050-4061` vs `tokens.h:1335-1337` |
| D-10 | AI 上下文 | Python 8192（`max_context_tokens`） vs C++ `32768×0.75` | `config.py:20` / `config_schema.py:237` vs `tokens.h:1321-1322` |

D-9 **不是**同一语义：Python 是"工具调用可重试判定"，C++ 是"LLM 请求瞬时错误退避"。
两者都合理，只需在文档写清（已写）。其余 9 组是真·同一语义两套数。

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
| P-1 | `(1024,2048,4096,8192,16384)` | `spectrum_widget.py:141` | FFT 档位 | 下拉、生成器 | △ | A（已暴露） |
| P-2 | `2048` | `spectrum_widget.py:151`；`cpp/.../spectrum_engine.h:884` | 默认 FFT | 生成器 / C++ 引擎 | △（两侧同值） | A |
| P-3 | `(1,4,8,16)` / `1` | `spectrum_widget.py:142,152` | 平均帧档位/默认 | 生成器 | ✓ | A（已暴露） |
| P-4 | 6 种窗 / 默认 Nuttall | `spectrum_widget.py:139,150` | 窗函数 | 生成器 | ✓ | A（已暴露） |
| P-5 | `512` | `spectrum_widget.py:1977-1979`（性能档） | 低性能档 FFT | 性能模式 | ✗（不在 `FFT_SIZES`） | C |
| P-6 | `256` | `spectrum_widget.py:161` | 瀑布环形行数 | 绘制/LUT | △ | A（建议加档位） |
| P-7 | `0.7/0.3` | `spectrum_widget.py:389-391` | 瀑布 IIR 平滑 | 瀑布 | △（字面量） | C |
| P-8 | `-120.0` | `spectrum_widget.py:419` | 无数据哨兵 | 重采样/绘制 | ✗ | B |
| P-9 | `8` | `spectrum_widget.py:442` | 峰值数量上限 | 峰值表 | ✗ | C |
| P-10 | `6.0` | `spectrum_widget.py:859` | 峰值相对门限 | 峰值检测 | ✓（schema） | A（已暴露） |
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
| P-28 | `15.0 / 5 / 40 / -100.0` | `tokens.h:352-355` | C++ 峰门限域/绝对底 | 峰检测 | ✓ | A + B |
| P-29 | `5 / 3 / 10` | `tokens.h:356-358` | 峰跨帧匹配窗口/出现/遗忘帧数 | 峰跟踪 | ✓ | B |
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
| A5 | `squelch_default_db` | 静噪门限默认值 | **待定** | 待定 | dBFS | 频谱与解码 | 需先定标定测量点（见 2.1）；示意图"信号低于此值时静音"。 | 用户会真改，但**当前语义不清**，必须先统一物理量纲。 |
| A6 | `ai_tool_timeout_s` | AI 工具执行超时 | `120` | 10–600 | 秒 | AI | "单个工具（如扫频/抓取）最多等多久；慢设备可调大。" | 与请求超时 `ai_timeout` 不同层；已有代码常量 `agent.py:630`。 |
| A7 | `waterfall_depth_rows` | 瀑布历史行数 | `256` | 128–4096（2 的幂） | 行 | 频谱与解码 | "瀑布能回看多久；越大越吃内存。" | C++ 侧已可选（`tokens.h:698`），Python 固定 256，建议对齐。 |
| A8 | `sky_extinction_k` | 大气消光系数 | `0.2` | 0.0–0.5 | mag/airmass | 星象仪（高级） | "低仰角星光被大气削弱的程度；城郊 0.2–0.3，高山 0.15。" | 影响地平附近恒星的可见性判断；有物理定义的合理默认。**低优先**（Bortle 尚被锁，先解锁 Bortle 更值）。 |

> **A-备注（受阻项，不重复报）**：Bortle 等级、调谐吸附步进、dB 量程、录音目录
> 已在 `config_schema.py:301-316` 以 LOCKED 登记，理由均成立，本线复核通过。
> 其中 Bortle 的解锁条件唯一：`rf_sky_view` 提供公开 `set_bortle()` 并复用
> `stars.DEFAULT_BORTLE`（本线已把默认值收口到那一个常量）。

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
- 新增测试：`tests/test_hardcoded_parameter_registry.py`
  （15 passed / 1 skipped：跳过的是"文档尚未生成"的占位断言，见 6.3）。
- 未改 `rf_sky_view.py` 的 `_bortle = 3`（在途文件）。→ patch 建议 **P-2**。

**C-2 `DEFAULT_BORTLE`**：`stars` 与 `star_catalog` 的 6 处默认参数统一引用该常量，
值恒为 3，签名默认值不变（已验证 `inspect.signature` 行为等价）。

### 6.2 交给对应线的 patch 建议（本线不改）

| # | 文件 | 问题 | 建议 |
|---|---|---|---|
| P-1 | `desktop/spectrum_widget.py` | 峰值 `8` 个 / `6.0 dB` 与 C++ `12` / `15.0` 两套 | 把 `max_peaks` 提成模块常量 `MAX_PEAK_MARKERS`；与 C++ `kMaxPeakCount` 在文档登记为"有意差异：UI 密度不同"或统一为 12。阈值改读 `config_schema.peak_threshold_db`（已暴露）而不是构造默认 6.0 再被设置覆盖。 |
| P-2 | `desktop/rf_sky_view.py` | `_bortle = 3` 私有硬编码；`(4.5-mag)/9.0` 在同文件两处 | `from mbdsdr_ai.skyengine.stars import DEFAULT_BORTLE`；提供 `set_bortle()` 后即可解锁设置项。拾取评分提成一个 `_pick_score(mag)` 函数（两处同式）。 |
| P-3 | `desktop/main_window.py` | 状态提示时长 `3000/4000/6000/8000` 散落 20+ 处 | 提到 `desktop/constants.py` 的 `TOAST_SHORT_MS / TOAST_INFO_MS / TOAST_WARN_MS / TOAST_ERROR_MS` 四档；`desktop/constants.py` 已有互斥钩子测试，按同法登记。 |
| P-4 | `desktop/main_window.py` | 刷新周期 `50/150/1000/2000/5000 ms` 散落 | 同上，收成 `UI_FPS`、`BUS_COALESCE_MS`、`STATUS_REFRESH_MS`、`SKY_REFRESH_MS`。 |
| P-5 | `desktop/receive_pipeline.py` | `-80.0` 固定静噪默认 | 等 2.1 定标后再改为读配置；不要先统一数字。 |
| P-6 | `cpp/src/dsp/spectrum_engine.cpp` | `kChunk = 8192` 在两处函数里各写一次 | 提到文件级 `constexpr std::size_t kReadChunk`。 |
| P-7 | `mbdsdr_ai/skyengine/star_catalog.py` | `0.5 * log2(...)` 的斜率字面量 | 提成 `DETAIL_GAIN_SLOPE = 0.5`（与 `MAX_DETAIL_GAIN_MAG` 同段）。 |

### 6.3 暂缓（需要跨线决策，本线不动）

- **D-3 FFT 档位**：Python UI 会给 C++ 引擎不接受的 `8192/16384`。若同一 UI 可切后端，
  这是"选了不生效"的真 bug；若两个前端各自独立，则只需文档登记。
  建议 `spectrum_widget.py` 的档位从后端能力查询派生，而不是硬编码 5 档。
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
| **dB 默认下限** | `-100.0` | `-45`（`tokens.h:329`） | ✗ | 需按同一"噪底"实测重标 |
| **FFT 档位数量** | 5 | 3 | ✗ | UI 从能力派生（P-1 关联） |
| 默认 FFT | 2048 | 2048（`spectrum_engine.h:884`） | ✓ | 保持 |
| 静噪范围 | `-120..0` | `-100..-20` | ✗ | 见 2.1，待定 |
| 静噪默认 | `-80` | `-50` | ✗ | 见 2.1，待定 |
| 默认采样率 | 2.048 MS/s | 2.4 MS/s | ✗ | 需产品口径（D-1） |
| 默认调谐步进 | 10 kHz | 100 kHz | ✗ | 需产品口径（D-2） |
| 上下文窗口 | 8192 | 32768（×0.75 预算） | ✗（有意） | 按模型能力，登记 |

对拍测试：`tests/test_hardcoded_parameter_registry.py::test_cpp_tokens_match_documented_values`
把 C++ 侧 15 个 token 的**当前值**冻结；任何一侧改数先红，强制回到本表。

---

## 8. 待裁定（需要决策人拍板，本线不擅自改行为）

1. **过境最低仰角拆分**（2.2）：按 `VISIBLE=5°` / `PASS=10°` 两名拆分，
   `next_pass()` 改用 10°。这会**改变** `next_pass()` 输出（5–10° 过境消失）。
   需要确认：是否接受？若接受，本线可在下一单里改 `sat_tracker.py` 并补"5–10° 被过滤"测试。
2. **静噪标定**（2.1）：滑杆到底标定"全带宽 IQ 功率 / 信道 RMS / 音频 RMS"哪一个？
   建议：**音频 RMS**（与 C++ 一致，且与听感一致），范围统一 `-100..-20`，默认 `-50`；
   窄带路径改用它。但这是行为变更，需确认。
3. **默认采样率/步进**（D-1/D-2）：2.048 MS/s + 10 kHz vs 2.4 MS/s + 100 kHz，
   以哪个为准？建议 2.4 MS/s（C++ 注释称其为 RTL-SDR 晶振最干净的档）+ 10 kHz。
4. **峰值默认 6 dB 还是 15 dB**（2.3）：需确定两者基准是否同一（"相对 floor" vs "相对 median"）。

---

## 9. 已做 / 未做

已做（本线，可提交）：
- `mbdsdr_ai/skyengine/stars.py`、`star_catalog.py`：公式单一事实源（C-1、C-2）。
- `tests/test_hardcoded_parameter_registry.py`：单一来源断言 + B 类锁定值 + 跨语言对拍。
- 本文档。

未做（不属本线或需授权）：
- `desktop/main_window.py` / `rf_sky_view.py` / `spectrum_widget.py` 的任何改动 → 见 §6.2 patch。
- 任何会改变现有输出的统一（§8 四项）。
- 推送到远端。
