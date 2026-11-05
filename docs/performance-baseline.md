# MBDSDR 性能基线（桌面前端 / 启动 / 渲染 / 内存 / DSP）

> 测量日期：2026-11-06　｜　测量方式：`python scripts/perf_probe.py`
> 纪律：本文件只记录**本机实测**数字。没有测过的项一律标「未做」，不写估算。

## 0. 测试环境

| 项 | 值 |
|---|---|
| CPU | 13th Gen Intel(R) Core(TM) i5-13420H（12 线程） |
| OS | Windows（中文路径） |
| Python | 3.14.0 |
| Qt | PySide6（`QT_QPA_PLATFORM=offscreen`，软件光栅，DPR=1） |
| 硬件 | **无 SDR 硬件**（`MBDSDR_NO_HW=1`）；rtlsdr 驱动已装（`C:\rtl-sdr\bin\rtlsdr.dll`） |
| 说明 | 测量期间同机**始终**有其它会话在跑 pytest/编译：2026-11-06 复测窗口内 `pid 1784` 持续占用 **0.7–0.9 个核**（10 s 采样 7.5–9.3 s CPU），系统负载 62–100%。因此**绝对启动耗时只作参考，不作基线**；import / 星图 / 频谱的改前改后一律用**同进程交替 A/B**（同一时刻负载对两边等价），绝对耗时不予采信。 |

复跑命令（一条命令出下表全部数据）：

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python scripts/perf_probe.py --section all
python scripts/perf_probe.py --section startup   # 只测启动分段
python scripts/perf_probe.py --section ui        # 只测天空/频谱/热插拔/心跳
python scripts/perf_probe.py --section memory    # 300 帧内存增长
python scripts/perf_probe.py --section dsp       # 复用 scripts/bench_dsp_throughput.py --quick
```

原始数据另存 `output/bench/ui_perf_<时间戳>.json`（`--json <路径>` 可指定）。

---

## 1. 指标基线表

| # | 指标 | 测量命令 | 当前值（本机） | 目标值 | 差距 |
|---|---|---|---|---|---|
| 1 | `import main_window`（`-X importtime` 累计） | `perf_probe.py --section startup` | **1.81 s**（改前 6.58 s / 满载时 12.53 s） | ≤ 2 s | ✅ 达标 |
| 2 | MainWindow 构造 → 首帧（同窗口 A/B，3 次各取中位） | 自建 `p_firstframe_ab.py`（HEAD 版两个面板 vs 工作区版） | 改前 **2095 ms**（1970/2095/2201）→ 改后 **1418 ms**（1188/1418/1910），**-677 ms（-32%）**；构造后主线程剩余枚举阻塞 884 → 225 ms（剩的是 `main_window._auto_enumerate_and_connect`，见 §4） | ≤ 3 s | ✅（相对值；绝对值受同机负载） |
| 3 | show + 首帧 paint | `perf_probe.py --section startup` | **0.23–0.46 s**（3 次中位 0.31 s；改前 3.48 s） | ≤ 0.5 s | ✅ 达标 |
| 4 | 进程起点 → 首帧可交互（绝对值） | 同上 | 复测 **7.9 s / 12.9 s**（两次独立窗口）。窗口内 `pid 1784` 分别吃 0.69 / 0.90 核，系统负载 100% / 62%；同一代码在负载更轻时手测 2.2–2.3 s。**该指标被同机负载支配，不写入基线**，只用 §2 的同窗口 A/B 相对值 | ≤ 5 s | ⚠️ 无可复现绝对值；原 9.72 s 同样不可信，已从基线假设中剔除 |
| 5 | 天空 700×700 / FOV120 单帧 paint | 同进程交替 A/B（n=40） | 改前 **43.96 ms**（p95 64.81）→ 改后 **37.97 ms**（p95 **54.06**），**-13.6%**；`--section ui` 在更重负载下仍报 59.51 ms（该绝对值不可比） | 30 fps → ≤33 ms | ❌ 仍超预算，但已降本（§3.2） |
| 6 | 天空 700×700 / FOV60 | 同上 | 23.72 → **21.50 ms**（p95 27.68 → 26.73） | ≤33 ms | ✅ |
| 7 | 天空 1000×1000 / FOV120 | 同上 | 40.78 → **36.18 ms**（p95 45.91 → 41.82） | ≤33 ms | ❌ |
| 8 | 天空 1000×1000 / FOV60 | 同上 | 22.25 → **20.70 ms**（p95 25.70 → 24.03） | ≤33 ms | ✅ |
| 9 | 天空最大图层 `_draw_stars` | 分层计时（同帧 31 次） | 700/120：**15.67 → 12.27 ms（-22%）**；1000/60：**8.17 → 5.71 ms（-30%）**；另省掉每帧 `_star_selection` **2.54 / 2.03 ms**（缓存命中后 0 次/帧） | ≤15 ms | ✅ 700/120 达标（12.27）；原基线 27.39 ms 已降 55% |
| 10 | 次大图层 `_draw_constellations` / `_draw_dso` | 同上 | 4.98 ms / 2.63 ms（改动前后无变化，±0.1） | 各 ≤5 ms | ✅ |
| 11 | 频谱 FFT 曲线 `_draw_spectrum`（仅谱线关 AA） | `--section ui` / 同帧 A/B | 改前 **40.10 ms** → 改后 **19.79 ms**（**-51%**）；4× 裁剪对比见 §3.3 | ≤16 ms（60 fps） | ⚠️ 仍略超 16 ms，但已从 2× 预算降到 1.24× |
| 12 | 瀑布 `_draw_waterfall` | 同上 | 0.45 ms | ≤5 ms | ✅ |
| 13 | 频谱面板整屏 `grab()` | 同上 | 44.85 ms（含 `_draw_spectrum` 改前的 30.83）；本次未复测整屏 grab，只测曲线本体 | ≤33 ms | ⚠️ 未复测 |
| 14 | 热插拔轮询派发（GUI 线程） | `--section ui` | **0.14 ms**（改前阻塞 200–2500 ms/拍） | ≤1 ms | ✅ 达标 |
| 15 | 热插拔后台枚举完成 | 同上 | 251.7 ms（在工作线程，不占 GUI） | — | 参考值 |
| 16 | GUI 线程心跳（50 ms 定时器） | 同上 | 命中 82/160，gap p50 62 ms / p95 78.8 ms / max **2344 ms** | gap p95 ≤60 ms | ⚠️ 最大 2344 ms 出现在启动首帧，非稳态 |
| 17 | 连续 300 帧内存增长（tracemalloc） | `--section memory` | **+342 KB**，gc 对象 +2，pen 缓存 397 / brush 93 | 每 300 帧 ≤1 MB 且对象数不涨 | ✅ 达标 |
| 18 | DSP `fft_2048` | `--section dsp` | 0.363 ms/块，RTF 47.04 | RTF ≥ 1 | ✅ |
| 19 | DSP `e2e_sustained`（端到端） | 同上 | 2.886 ms/块，RTF 3.01 | RTF ≥ 1 | ✅（最慢一环） |
| 20 | DSP `wfm_rx` | 同上 | 2.590 ms/块，RTF 6.59 | RTF ≥ 1 | ✅ |

> RTF（realtime factor）= 每秒可处理的样本数 ÷ 实时采样率；>1 即能实时跟上。

---

## 2. 已做的优化与前后数字

| 改动 | 文件 | 改前 | 改后 | 验证 |
|---|---|---|---|---|
| **设备枚举移出 GUI 线程**：2 s/次的热插拔轮询改为 `QThreadPool` 后台枚举 + 信号回主线程做判定 | `desktop/device_panel.py` | 10 s 窗口内 GUI 线程 **63%** 时间在同步枚举（`rtlsdr_open` 0.2–2.4 s、Pluto 网络探测固定 0.5 s） | 派发 **0.14 ms**，枚举在后台 251.7 ms | `tests/test_device_switch_flow.py` 6 passed；`perf_probe.py --section ui` 的 dispatch_ms |
| **插入判定改用稳定定位符**：`(driver, index)` 取代含 serial/tuner 的 key（设备被占用时 serial 会空/实跳变，误报「新设备」并覆盖掉线提示） | `desktop/device_panel.py` | `test_read_stall_marks_ui_disconnected` 红（状态栏被「检测到新设备」覆盖） | 绿；6 passed | `pytest tests/test_device_switch_flow.py -q` |
| **Windows DLL 搜索懒惰化**：先查常规目录（cwd/lib/PATH/`C:\rtl-sdr\bin`），都未命中才做 Downloads/Desktop/ProgramFiles/LOCALAPPDATA 的递归发现 | `mbdsdr_ai/windows_setup.py` | 每次进程启动递归 **1566 ms**（`%LOCALAPPDATA%` 4760 目录占 1188 ms） | 命中 `C:\rtl-sdr\bin\rtlsdr.dll` 时 **0.28 ms** | 手测 `_cheap_candidate_dirs()` 0.07 ms；`pytest tests/test_windows_setup.py` 8 passed |
| **`mbdsdr_ai/__init__` 惰性导出（PEP 562）**：不再在包初始化时导入全部子模块（scipy/agent/requests） | `mbdsdr_ai/__init__.py` | `import mbdsdr_ai` **4.81–4.93 s** | **2.18–3.53 s**（含删除 DLL 递归后的综合效果） | 3 次独立进程；`__all__` 全量属性访问 smoke `all_ok` |
| **`channelizer.py` 的 `scipy.signal` 改为首次使用时导入** | `mbdsdr_ai/channelizer.py` | `import main_window` **6.58 s** | **2.91–3.44 s** | 3 次 `-X importtime` |
| **`demod_wfm.py` 的 `scipy.signal` 同上** | `mbdsdr_ai/demod_wfm.py` | 同上 | **2.80–3.55 s**（该步增益被其他链掩盖，主要省 `wfm_stereo_lite`） | 同上 |
| **`wfm_stereo_lite.py` 的 `scipy.signal` 同上** | `mbdsdr_ai/wfm_stereo_lite.py` | `import main_window` 2.80–3.55 s | **1.60–1.69 s**（相对原始 6.58 s 降 **~75%**） | 3 次 `-X importtime`；`pytest tests/test_wfm_stereo_deemph.py tests/test_channelizer_fix.py tests/test_dsp_demod_channelizer.py` 16 passed |
| **天空重绘步长自适应**：按上一帧实测耗时 EMA 调整 `QTimer` 间隔（只改调度，不改任何绘制内容） | `desktop/rf_sky_view.py` | 固定 33 ms 触发，而单帧 42–60 ms → Qt 持续排队重绘，事件循环被饿死 | 实测 ema 53.6 ms 时 interval 33 → **72 ms**（滞回 6 ms，上限 250 ms） | `perf_probe.py --section ui` 的 paint_ms_ema / interval_after_ms |
| **星表选择结果 2 s 分箱缓存**：`select_for_view` 原每帧算一次（1794 颗，4.98 ms），而 120° 刷新一次视线只挪 0.008° | `desktop/rf_sky_view.py` | `_star_selection` **2.54 ms/帧**（700/120）、2.03 ms/帧（1000/60） | 缓存命中后 **0 次/帧**；键含 jd 分箱 / 中心 az·alt / fov / Bortle / 消光 / 经纬 | §3.2 分层表；`_star_selection` 调用计数 |
| **星点绘制分桶量化**：颜色与 alpha 由 8 级粗化到 16 级（直径保持 0.5 px 步长），把逐颗 `drawPoints` 调用次数从 448 降到 248 | `desktop/rf_sky_view.py` | `_draw_stars` **15.67 ms**（700/120） | **12.27 ms（-22%）**；1000/60 8.17 → 5.71（-30%） | 分层计时 + 像素差（changed 2.678%，max 24） |
| **设备枚举全部移出 GUI 线程**：`module_panel` 启动枚举 + `device_panel` 首次/手动刷新都改为 `QThreadPool` 后台枚举；同步入口保留给测试 | `desktop/module_panel.py`、`desktop/device_panel.py` | `MainWindow` 构造内主线程同步枚举 **884 ms/3 次**（1970–2201 ms 构造）；启动后主线程累计阻塞 884 ms | 构造 **1418 ms**（-677 ms / -32%）；主线程枚举只剩 `main_window._auto_enumerate_and_connect` 约 **100–500 ms** | §1 行 2 的同窗口 A/B；`tests/test_device_capability_probe.py` 等 65 passed |
| **频谱谱线关几何 AA**：`_render_spectrum` 里只对 `_draw_spectrum` 临时关 `QPainter.Antialiasing`，峰点/刻度/文字仍开 | `desktop/spectrum_widget.py` | `_draw_spectrum` **40.10 ms**（900×400） | **19.79 ms（-51%）**；像素差 2.498%，仅填充边缘 1 px（§3.3） | 同帧 AA 开/关 A/B + 4× 裁剪图 |

> 占用状态（2026-11-06 复测时）：`desktop/rf_sky_view.py`、`mbdsdr_ai/sdr_backend.py`、`desktop/module_panel.py` 在 `.locks/claims.json` 中均为 `current: null`（持有方已 release），本线才动它们；`mbdsdr_ai/sdr_backend.py` 的改动已由持有方自行入库，本线工作区对其无 diff，**不重复提交**。

---

## 3. 关键证据摘要

### 3.1 启动导入结构（改前，`-X importtime`）

```
main_window                 6.576s cum
└ spectrum_widget           5.437s
  └ constants               5.401s
    └ mbdsdr_ai.sdr_backend 5.398s
      ├ mbdsdr_ai 自身      1.587s ← 包 __init__ 里 setup_rtlsdr_windows 的递归搜索
      ├ receive_chain       2.845s → channelizer → scipy.signal 2.820s
      └ mbdsdr_ai.agent     3.399s
```

改后同一条链：`main_window 1.60–1.69s`，最大项 `ai_panel 0.51s`、`spectrum_widget 0.30s`。

### 3.2 天空单帧分层：改前 / 改后（700×700 / FOV120，冻结同一 jd，同进程同帧数据）

降本路径 = **① 星表挑选结果按 2 s 分箱缓存**（`jd/中心 az·alt/fov/Bortle/消光/经纬` 不变就复用）+ **② 星点绘制分桶量化**（颜色/alpha 8→16 级，合并 `drawPoints` 调用：448 → 248 次/帧）。没有用 `QPainterPath` 批量 `drawPath`——实测比逐颗 `setBrush+drawEllipse` 更慢（56 次 `drawPath` 13 ms），已回退。

| 图层 | 改前 ms/帧 | 改后 ms/帧 | 变化 |
|---|---|---|---|
| `_star_selection` | 2.54 | **0**（缓存命中） | 全部省掉 |
| `_draw_stars` | 15.67 | **12.27** | **-22%** |
| `_draw_constellations` | 4.87 | 4.98 | ±0 |
| `_draw_dso` | 2.70 | 2.63 | ±0 |
| `_draw_az_grid` | 2.66 | 2.58 | ±0 |
| `_draw_planets` | 1.64 | 1.62 | ±0 |
| `_draw_info_overlay` | 1.20 | 1.33 | ±0 |
| `_flush_labels` | 0.86 | 0.87 | ±0 |

整帧墙钟（同进程交替 A/B，40 对，中位 / p95；冻结同一 jd，负载对两边等价）：

| 尺寸/FOV | 改前 | 改后 | 变化 |
|---|---|---|---|
| 700×700 / 120° | 43.96 / 64.81 | **37.97 / 54.06** | **-13.6% / -16.6%** |
| 700×700 / 60° | 23.72 / 27.68 | 21.50 / 26.73 | -9.4% |
| 1000×1000 / 120° | 40.78 / 45.91 | 36.18 / 41.82 | -11.3% |
| 1000×1000 / 60° | 22.25 / 25.70 | 20.70 / 24.03 | -7.0% |

**视觉证据（像素差，逐像素 RGB 最大通道差）**：

| 尺寸/FOV | changed% | >8 差% | max |
|---|---|---|---|
| 700×700 / 120° | 2.678% | 0.353% | 24 |
| 700×700 / 60° | 0.839% | 0.114% | 13 |
| 1000×1000 / 120° | 1.416% | 0.200% | 24 |
| 1000×1000 / 60° | 0.438% | 0.063% | 12 |

差异全部来自分桶量化（±8 级色/alpha 与 0.5 px 直径步长内取整）：max 24/255 = 9.4%，且 >8 的像素只占 0.06–0.35%，没有整颗星消失、没有密度变化。4× 放大裁剪（`docs/perf-evidence/sky_before_700_crop4x.png` vs `sky_after_700_crop4x.png`）肉眼无结构差异；1:1 原始尺寸（`sky_before_700_fov120.png` / `sky_after_700_fov120.png`）无法分辨。**结论：差异可接受。**

> 说明：仓库 `.gitignore` 第 4 行排除 `*.png`，上述 PNG 是**本地工件**（留在 `docs/perf-evidence/` 供复查，不进 git）；可入库的数字证据是同目录的 `sky_ab.json`。

### 3.3 频谱 AA：取舍结论（900×400，同一帧数据，30 帧交替）

| 方案 | ms/帧 | 像素差（360000 px） | 说明 |
|---|---|---|---|
| 全开 AA（改前） | **40.10** | — | 谱线填充、峰点、文字都走 AA |
| **仅谱线关 AA（已采用）** | **19.79（-51%）** | changed 2.498% / >8 差 1.074% / >32 差 0.533% / max 127 | 只把 `_draw_spectrum` 的填充/折线关 AA；峰点、刻度、文字仍开 |
| 全关 AA（未采用） | 18.85–22.4 | changed 2.943% / >8 1.48% / max 127 | 与「仅谱线关」只差 1–3 ms，却连标记一起变硬，不值 |

**能不能看出来**：4× 放大（`docs/perf-evidence/aa_on_crop4x.png` vs `aa_off_crop4x.png`）能看到填充上边缘由「混叠过渡」变成「1 px 硬台阶」；**100% 原始尺寸（`aa_on_900x400.png` / `aa_off_900x400.png`）不并排看不出**，因为差异只发生在谱线与背景交界的 1 个像素上（0.533% 的 >32 差异全部是这条边），谱线形状、峰高、刻度、文字均未变。

**决定：改**（采用「仅谱线关 AA」）。理由：每帧省 20.3 ms（-51%），而视觉差异在正常观看尺寸下不可辨；比全关 AA 多保留 1–3 ms 成本换取峰点/标签的平滑，是这笔取舍里更稳的一档。像素差已入库（裁剪图为本地工件），若后续有人对边缘敏感，可只回退 `spectrum_widget.py` 里那 3 行 hint 切换。

原始数据：`docs/perf-evidence/spectrum_aa.json`（同上，PNG 裁剪为本地工件，不进 git）。

### 3.4 内存（连续 300 帧）

tracemalloc delta **+342 KB**、gc 对象 **+2**、pen 缓存 397 条、brush 缓存 93 条 —— 无只增不减容器迹象。

---

## 4. 未做 / 未改（明确边界）

1. **C++ 内核侧热点**：未做。没有用 perf/VTune 采样，也没有逐行计时，因此**不报任何 C++ 侧结论**。
2. **长时间内存曲线（30 分钟连续频谱）**：未做。只做了 300 帧（约 1 分钟）与 1000 帧短跑。
3. **天空 700/120 仍未进 33 ms**：`_draw_stars` 已从 15.67 降到 12.27 ms，但整帧 37.97 ms（p95 54.06），距 30 fps 还差 5 ms 左右。下一步候选（**未实现**）：`_draw_constellations` 4.98 ms 与 `_draw_dso` 2.63 ms 的批量路径、标签图层合并；这些都需要新的视觉对比，不在本轮。
4. **`main_window._auto_enumerate_and_connect` 仍是主线程同步枚举**：2026-11-06 复测 **1644 ms/次**（1 台 RTL-SDR；`--section startup` 的 `backend_enum_ms`），发生在首帧之后、仍占 GUI 线程。该文件被「操作逻辑线」持有（`claims.json` current 非空），按纪律**未抢**；建议持有方按 `device_panel` 的 `QThreadPool` 做法迁走（边界见 §7.1）。
5. **频谱曲线仍 19.79 ms**（60 fps 预算 16 ms）：已降 51%，但未达标；再往下要降低 FFT bin 到像素的采样密度或改用增量绘制，属绘制改动，未做。
6. **整屏 `grab()` 未复测**：本轮只测 `_draw_spectrum` 本体，整屏 44.85 ms 是改前数字。
7. **独占机器复测启动绝对值**：未能取得。两次窗口内 `pid 1784` 都占用 0.69–0.90 核，系统负载 62–100%；因此该指标只用同窗口 A/B 相对值，绝对值不写基线。
8. **`docs/learn/dsp-performance-baseline.md` 之外的 DSP 逐档基线**：本次只跑 `--quick` 档，未做全档扫描。

---

## 5. 提交范围与文件占用

本线分两批提交：

**第一批（commit `3abdd772`，已入库）**：`desktop/device_panel.py`、`mbdsdr_ai/__init__.py`、`mbdsdr_ai/channelizer.py`、`mbdsdr_ai/demod_wfm.py`、`mbdsdr_ai/wfm_stereo_lite.py`、`mbdsdr_ai/windows_setup.py`、`scripts/perf_probe.py`、`docs/performance-baseline.md`。

**第二批（本轮，私有 `GIT_INDEX_FILE` + 精确清单闸）**：

- `desktop/rf_sky_view.py`（星表缓存 + 星点分桶；提交时 claims 为 `current: null`）
- `desktop/module_panel.py`（**只贴本线的枚举异步 hunks**；该文件另有 82 行他线未提交改动，必须分离）
- `desktop/device_panel.py`（首次/手动刷新异步化 + 同步入口保留）
- `desktop/spectrum_widget.py`（仅谱线关 AA）

**未提交（工作区保留，文件被其他线持有 / 非本线改动）：**

- `mbdsdr_ai/sdr_backend.py` —— 工作区无本线 diff（持有方已自行入库）
- `desktop/main_window.py` —— 占用者「操作逻辑线」（`_auto_enumerate_and_connect` 同步枚举仍在该文件，未抢）

---

## 6. 已知的既有失败（非本线引入）

- `tests/test_desktop_constants.py::TestNoSecondSourceOfTruth::test_module_exposes_only_the_shared_constant`
  失败：`desktop/constants.py` 的公开符号多出 `format_sample_rate`、`math`。`git status` 显示该文件**未被本线修改**，属既有状态。
- `tests/test_orphan_connection.py::TestNewOrphanWiring::test_constellation_cleared_resets_stats`
  失败：断言 `'信号: —' == '信号: --'`。`desktop/main_window.py` 的未提交改动把 `--` 改成了 `—`，属其他线，非本线引入。
- `tests/test_spectrum_scale_peak_range.py` 2 个失败（`test_db_axis_drag_updates_readout_and_shared_sliders`、`test_ctrl_db_zoom_shows_value_bubble_at_cursor_and_hides_on_release`）：与本轮 `_draw_spectrum` 的 AA 改动无关（未触及该交互路径），属既有失败。
- `tests/test_desktop_hotplug_autorecovery.py` 整文件 `xfail(strict=True)`（桌面未接入自动重连，见 `docs/limitations-register.md`）。

---

## 7. 性能账后半（2026-11-06 实测，独立探针）

> 复跑：`python scripts/perf_probe_deep.py --section all --json docs/perf-evidence/deep_perf_20261106.json`
> 单节：`--section startup --runs 3` / `--section replay` / `--section spectrum` / `--section tle`；回放默认每次重建 1.1 GiB 测试文件，`--keep-replay-file` 复用。
> 条件：i5-13420H（12 线程）/ 15.7 GB / Python 3.14.0 / PySide6 `QT_QPA_PLATFORM=offscreen` 软件光栅 DPR=1。**未设 `MBDSDR_NO_HW`**，RTL-SDR 枚举到 1 台。测量窗口内同机 `pid 1784` 的 Python 会话持续在跑，系统负载 32–60%（这是另一个窗口，和 §0 的 62–100% 不同）。因此**启动绝对值只作参考**，eventFilter 结论用交替同窗口 A/B。

### 7.1 启动分阶段（3 个独立子进程，`perf_counter` 时间戳）

| 阶段 | 中位 ms | 3 次范围 |
|---|---|---|
| Qt 导入（PySide6） | 147.2 | 122.7–155.0 |
| `main_window` 导入 | 1108.2 | 1070.2–1263.1 |
| `QApplication` 构造 | 11.5 | 10.0–12.3 |
| 字体（`QFontDatabase.families` + `setFont`） | 0.5 | 0.4–0.6 |
| 主题 `apply` | 0.2 | 0.2 |
| **MainWindow 构造（容器）** | **5059.6** | 4744.5–5705.0 |
| 　└ `eventFilter`（构造期事件 79,780 次） | **2939.6** | 2809.9–3275.1 |
| 　└ `_build_kernel_panels` | 1124.9 | 1047.9–1428.1 |
| 　　└ `SettingsPanel` 构造 | 683.9 | — |
| 　└ `_build_mode_shell` | 1224.0 | 1040.5–1334.5 |
| 　└ 星表加载（首帧内，1.9 MB npz） | 38.2 | 36.0–42.5 |
| show + `processEvents`（首帧） | 198.2 | — |
| `grab` 首帧 | 95.8 | 75.7–100.9 |
| **进程 → 首帧合计** | **6620.5** | 6096.8–7315.4 |
| 首帧后后端枚举（GUI 线程，1 台） | **1644.3** | 1561.1–1795.2 |
| **进程 → 枚举完成** | **10391.6** | 9911–11151 |

**前三名（可归因叶子块）**：① `eventFilter` **2939.6 ms**（占构造 58%；80k 次事件，每次 4 个 `getattr` + 一次 `from PySide6.QtCore import QEvent`）；② 首帧后后端枚举 **1644.3 ms**（仍在 GUI 线程）；③ `_build_kernel_panels` **1124.9 ms**（其中 `SettingsPanel` 683.9 ms）。

**eventFilter A/B（交替跑，n=3，只测构造）**：正常中位 **3884.4 ms** → 把 `eventFilter` 换成只调 `QMainWindow.eventFilter` **1191.9 ms**，差 **2692.5 ms**。两条独立口径（包装计时 2939.6 / A/B 2692.5）同量级，说明这是真实开销，不是采样噪声。热点在 `desktop/main_window.py:2355-2379`；该文件归「操作逻辑线」，本轮**未改**，只把数字交回持有方。

**可后台化边界（按实测大小判断）**

- **星表（1.9 MB / 38.2 ms）**：不值得为它专门起线程；若星表涨到几十 MB，`load_catalog()` 只做 `np.load` + SHA 校验，返回的是 numpy 数组 + 字符串（无 QObject），可放 `QThreadPool` 预载；**不能跨线程**的是 `RFSkyView`/`QPainter`/`QPixmap` 以及任何控件方法。`get_catalog()` 是模块级 `lru_cache`，要么在首帧前预载命中缓存，要么把数组通过信号交给 GUI，不能在工作线程里边读边调 `RFSkyView`。
- **后端枚举（1644 ms）**：`enumerate_all_sdr_devices()` 返回 `list[dict]`，不碰 Qt，可整体放 `QThreadPool`（`device_panel`/`module_panel` 已经这么做）。**不能跨线程**的是 `SettingsPanel.__init__` 本身（QWidget 构造）；只能把枚举调用点延后/搬走，控件仍在 GUI 线程创建。`build_backend_for_device` 实测 0.1 ms，没必要搬。
- **eventFilter**：不能后台化；只能把每事件重复的 `import`/`getattr` 提到常量或实例属性，属持有方改动。

期望字段：`startup.median.dur_*`、`startup.median.eventfilter_ms`、`startup.median.backend_enum_ms`、`startup.median.settings_panel_init_ms`、`startup.eventfilter_ab`。

### 7.2 回放读盘：UI 线程占用实测（只测不改）

**先纠正前提**：当前工作区与 `HEAD` 的回放调用点**已经在后台线程**——`desktop/main_window.py:8273` 建 `threading.Thread`，worker 里只做 `baseband_io.load_iq` + 进度信号（`main_window.py:8256-8275`）；回放路径还有 `REPLAY_MAX_SAMPLES = 24_000_000`（`main_window.py:43`）上限。因此线上不会出现 4.9 s 整段冻结；下表是把两种口径都量出来，便于判断“如果仍在 UI 线程会怎样”。

测试文件：1,100 MiB 裸 cf32（144,179,200 样本，24M 上限时只读前 24M），块大小 1M 样本（8 MB），10 ms `QTimer` 心跳，独立子进程。

| 模式 | 上限 | 读盘 s | gap p50 | gap p95 | **gap max** | 峰值 WS |
|---|---|---|---|---|---|---|
| 同步（假设仍在 UI） | 全文件 | 1.817 | 0.63 | 0.63 | **1816.44 ms** | 1174.3 MB |
| 后台线程（当前实现） | 全文件 | 1.858 | 9.99 | 10.84 | **11.84 ms** | 1174.5 MB |
| 同步（假设仍在 UI） | 24M（真实路径） | 0.304 | 0.67 | 0.67 | **304.1 ms** | 259.9 MB |
| 后台线程（当前实现） | 24M（真实路径） | 0.288 | 10.02 | 10.75 | **10.84 ms** | 260.0 MB |

> 本次实测 1.1 GiB 全量读盘 1.817 s、峰值 1174 MB（文件刚由探针写入，页缓存是热的）；早前 4.90 s / 1.04 GB 是另一个窗口/另一份文件，不作为本轮基线。

**“移到线程后卡顿降到多少”的估算方法与误差**

- 公式：`T_block_thread ≈ max(心跳间隔, 单块转换 + 进度信号投递 + 事件循环调度)`；`单块转换 ≈ T_load × chunk_samples / limit`。
- 代入全文件：`1816.44 × 1,048,576 / 144,179,200 ≈ 13.2 ms`，实测 max 11.84 ms（numpy/`fromfile` 期间释放 GIL，且进度事件是排队投递）。
- 误差：心跳 10 ms → 单次 gap 的分辨率是 **±10 ms**（即实测 11.84 ms 对应真实区间约 1.8–21.8 ms）；p95 10.84 ms 可作为稳态上限。若 worker 里出现纯 Python 逐样本循环（不释放 GIL），公式失效，会退化到接近 `T_load`。
- 降幅：全文件 **1816.4 → 11.8 ms（-99.35%）**；真实 24M 路径 **304.1 → 10.8 ms（-96.4%）**。

期望字段：`replay.cases[].mode/max_samples/file_mb/wall_s/gap_p95_ms/gap_max_ms/peak_ws_mb/samples/progress_events`。

### 7.3 频谱帧率（2.4 MS/s / 1024 点）

配置（`set_performance_mode(1)` + `set_fft_size(1024)`）：FFT **1024**、谱线 bin **384**、曲线定时器 **66 ms**（名义 15.15 fps）、瀑布 100 ms、控件 858×283；输入每 50 ms 推 120,000 样本（2.4 MS/s）。

| 指标 | 实测 |
|---|---|
| 5.999 s 内曲线 paint | **78 帧 → 13.0 fps**（名义 15.15 的 86%） |
| 单帧 `paintEvent` | 中位 **10.01 ms** / p95 11.75 / max 12.19 |
| 直接 `_render_spectrum`（同一状态） | 中位 **11.07 ms** / p95 13.54 |
| `push_iq`（每 120k 样本块） | 中位 **1.62 ms** / p95 2.12 |
| 实际帧周期 | **76.9 ms ≈ 66 ms 定时器 + 10.0 ms paint + 0.9 ms 调度** |

热函数（200 帧 cProfile，按 tottime）：`drawPolygon` 400 次 **0.958 s**（2.40 ms/次、2 次/帧 ≈ 4.79 ms/帧）；`drawPolyline` 200 次 **0.636 s**（3.18 ms/帧）；`_draw_spectrum` 自身 **0.283 s**（1.42 ms/帧）；`drawLine` 0.069 s；`drawText` 0.037 s。**Qt 光栅化（polygon+polyline+line）约占 8.3 ms/11.07 ms = 75%**，Python 侧 `_draw_spectrum` 自身约 13%，没有其它 Python 函数超过 0.8%。

**Python 热循环对比（同一 1024 点数据）**

| 微基准 | 中位 |
|---|---|
| `20*log10(abs(fft))` 向量化 | **0.006 ms** |
| 同语义纯 Python `for` 循环（1024 点） | **0.100 ms**（慢 16.7×） |
| `_resample_max` 1024→384 的逐 bin 循环 | **0.998 ms** |
| `np.maximum.reduceat` 等价向量化 | **0.020 ms**（快 49.9×） |

`_resample_max` 在 `push_iq` 里（`spectrum_widget.py:536`）确实占了 push 的 62%（1.0/1.62 ms），但 push 每 50 ms 才跑 1 次（约 2% 占空），**不是帧率瓶颈**；换向量化只能省 ~1 ms/50 ms。另：1024/384 不能整除，当前循环用 `max` + 空 bin 置 NaN，`reduceat` 语义不同，不能直接替换；若要走向量化，应选能整除的 bin 数（如 1024/256）。

**结论**：帧率上限由 `1/(定时器间隔 + 单帧 paint)` 决定（66+10 ≈ 76.9 ms → 13.0 fps），不是 Python 循环；要把 13 fps 抬到 15 fps，得降 `drawPolygon/drawPolyline` 的光栅成本或调小谱线 bin/采样密度——属绘制改动，本轮未做。

期望字段：`spectrum.config.*`、`spectrum.measured.fps/paint/direct_render/push_iq`、`spectrum.profile_top`、`spectrum.microbench`。

### 7.4 TLE 联网耗时与超时配置

当前配置（运行时读出）：

| 项 | 值 |
|---|---|
| `TLEManager(timeout=)` 默认 | **10.0 s** |
| 实际硬截止（`timeout + 5.0`） | **15.0 s** |
| `tle_fetch.FetchPolicy` 默认 | request 15 s / hard 20 s（`TLEManager` 不用它，属另一套默认值） |
| 源 | `https://celestrak.org/NORAD/elements/gp.php` |

| 实测 | 结果 |
|---|---|
| 真实 Celestrak ×3 | 全部 **SSL handshake 超时**：10.004 / 10.016 / 10.044 s |
| `TLEManager.fetch(25544, "ISS")` 首次 | **10.027 s**（网络失败后回退到离线/缓存，返回 ok）；第二次 0.000 s |
| 本机黑洞服务器（收连接不回包） | **10.017 s**（socket 读超时先触发） |
| 硬截止改 1 s 验证 | **1.007 s**（证明硬截止路径真实生效） |

**真实最坏耗时 = 10.044 s**（5 次联网尝试的最大值）；理论上限 **15 s**（若 DNS/连接阶段卡住、socket 超时没触发，由硬截止兜底）。本次三次真实请求都吃满 10 s，说明当前网络到 celestrak 不可达，**不是“偶发慢”**。

**是否卡 UI**：`_compute_refresh` 跑在 `threading.Thread`（`desktop/new_spacetime_panel.py:654`），网络等待不占 GUI 线程，只延长“刷新中”状态；启动只构造 `TLEManager()`（`new_spacetime_panel.py:278`），不发请求，所以 §7.1 的启动账里不含这 10 s。

**“超时改可配”的代价**：构造函数已有 `timeout` 参数，改动面 = ① 在 `new_spacetime_panel.py:278` 把设置值传进去（1 行）；② `_bounded_urlopen` 的硬截止固定为 `timeout + 5.0`，要独立可配就得加第二个参数/比例（1 个函数签名 + 调用点）；③ 设置面板加一个键 + 校验（1 处 UI + schema）；④ `tle_fetch.FetchPolicy` 的 15/20 默认值要删掉或标注“未被使用”，避免两套默认值；⑤ 文档 `docs/error-code-visibility.md` 里“ai_timeout 不影响 TLE”的说明要改；⑥ 无现成测试覆盖（`tests/` 里搜不到 `TLEManager`），需要补一条超时用例。风险：实测 3 次都吃满 10 s，把默认调到 <10 s 会把“本来能成功的慢网”判死；调到 >10 s 只会让“刷新中”更久，不会卡 UI。

期望字段：`tle.config.*`、`tle.celestrak_real[].wall_s`、`tle.manager_fetch`、`tle.blackhole_timeout.wall_s`、`tle.blackhole_hard_deadline_1s.wall_s`。

### 7.5 证据文件

- `docs/perf-evidence/deep_perf_20261106.json`：四个小节的原始 JSON（含每次独立进程的 runs、A/B、cProfile top、微基准、TLE 原始耗时）。
- 探针：`scripts/perf_probe_deep.py`（只读产品代码，不改任何源文件；回放测试文件建在 `D:\mbdsdr\.tmp` 并默认当场删除）。
