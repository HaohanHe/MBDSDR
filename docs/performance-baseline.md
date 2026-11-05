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
4. **`main_window._auto_enumerate_and_connect` 仍是主线程同步枚举**：2026-11-06 复测 **1543.5 ms/次**（1 台 RTL-SDR；`--section startup` 的 `backend_enum_ms`），发生在首帧之后、仍占 GUI 线程。该文件被「操作逻辑线」持有（`claims.json` current 非空），按纪律**未抢**；建议持有方按 `device_panel` 的 `QThreadPool` 做法迁走（边界见 §7.1）。
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
| Qt 导入（PySide6） | 146.7 | 130.1–149.8 |
| `main_window` 导入 | 910.6 | 906.3–984.9 |
| `QApplication` 构造 | 12.9 | 11.0–12.9 |
| 字体（`QFontDatabase.families` + `setFont`） | 0.4 | 0.4–0.9 |
| 主题 `apply` | 0.2 | 0.2 |
| **MainWindow 构造（容器）** | **4314.3** | 4054.2–4483.1 |
| 　└ `eventFilter`（构造期事件 80,522 次） | **2566.4** | 2475.2–2666.8 |
| 　└ `_build_kernel_panels` | 1138.8 | 1072.2–1206.5 |
| 　　└ `SettingsPanel` 构造 | 625.0 | 616.2–719.0 |
| 　└ `_build_mode_shell` | 802.8 | 792.2–986.1 |
| 　└ 星表加载（首帧内，1.9 MB npz） | 33.4 | 32.1–35.0 |
| show + `processEvents`（首帧） | 160.5 | 148.6–188.3 |
| `grab` 首帧 | 45.3 | 43.0–62.7 |
| **进程 → 首帧合计** | **5590.3** | 5410.5–5785.3 |
| 首帧后后端枚举（GUI 线程，1 台） | **1543.5** | 1474.3–1606.7 |
| **进程 → 枚举完成** | **9333.3** | 9163.6–9562.4 |

**前三名（可归因叶子块）**：① `eventFilter` **2566.4 ms**（占构造 59%；80.5k 次事件，每次 4 个 `getattr` + 一次 `from PySide6.QtCore import QEvent`）；② 首帧后后端枚举 **1543.5 ms**（仍在 GUI 线程）；③ `_build_kernel_panels` **1138.8 ms**（其中 `SettingsPanel` 625.0 ms）。

**eventFilter A/B（交替跑，n=3，只测构造）**：正常中位 **4198.4 ms** → 把 `eventFilter` 换成只调 `QMainWindow.eventFilter` **1180.9 ms**，差 **3017.5 ms**。两条独立口径（包装计时 2566.4 / A/B 3017.5）同量级，说明这是真实开销，不是采样噪声。热点在 `desktop/main_window.py:2355-2379`；该文件归「操作逻辑线」，**本节是改前快照**。落库状态按 2026-11-06 实际核对，分两条：

- **已落库**（eventFilter 早退分派 + 启动枚举/连接后台化）：`f4422ca9081f739299cc33d0ef16eb0802f04895` — `desktop/main_window.py:2367` — `def eventFilter(self, obj, event):  # noqa: N802 (Qt 命名)`；`desktop/main_window.py:3488` — `def _auto_enumerate_and_connect_async(self)`。`git show --name-only f4422ca9` 含 `desktop/main_window.py`，见 §8.1/§8.2。
- **未落地 / 计划中**：`_start_iq_streams` 的 844–1013 ms。本轮只完成归因（GUI 线程首次 `import scipy.signal` 1016.5 ms，见 §8.6），不得读作「已优化」。

**可后台化边界（按实测大小判断）**

- **星表（1.9 MB / 33.4 ms）**：不值得为它专门起线程；若星表涨到几十 MB，`load_catalog()` 只做 `np.load` + SHA 校验，返回的是 numpy 数组 + 字符串（无 QObject），可放 `QThreadPool` 预载；**不能跨线程**的是 `RFSkyView`/`QPainter`/`QPixmap` 以及任何控件方法。`get_catalog()` 是模块级 `lru_cache`，要么在首帧前预载命中缓存，要么把数组通过信号交给 GUI，不能在工作线程里边读边调 `RFSkyView`。
- **后端枚举（1543.5 ms）**：`enumerate_all_sdr_devices()` 返回 `list[dict]`，不碰 Qt，可整体放 `QThreadPool`（`device_panel`/`module_panel` 已经这么做）。**不能跨线程**的是 `SettingsPanel.__init__` 本身（QWidget 构造）；只能把枚举调用点延后/搬走，控件仍在 GUI 线程创建。`build_backend_for_device` 实测 0.1 ms，没必要搬。
- **eventFilter**：不能后台化；只能把每事件重复的 `import`/`getattr` 提到常量或实例属性，属持有方改动。

期望字段：`startup.median.dur_*`、`startup.median.eventfilter_ms`、`startup.median.backend_enum_ms`、`startup.median.settings_panel_init_ms`、`startup.eventfilter_ab`。

### 7.2 回放读盘：UI 线程占用实测（只测不改）

**先纠正前提**：当前工作区与 `HEAD` 的回放调用点**已经在后台线程**——`desktop/main_window.py:8273` 建 `threading.Thread`，worker 里只做 `baseband_io.load_iq` + 进度信号（`main_window.py:8256-8275`）；回放路径还有 `REPLAY_MAX_SAMPLES = 24_000_000`（`main_window.py:43`）上限。因此线上不会出现 4.9 s 整段冻结；下表是把两种口径都量出来，便于判断“如果仍在 UI 线程会怎样”。

测试文件：1,100 MiB 裸 cf32（144,179,200 样本，24M 上限时只读前 24M），块大小 1M 样本（8 MB），10 ms `QTimer` 心跳，独立子进程。

| 模式 | 上限 | 读盘 s | gap p50 | gap p95 | **gap max** | 峰值 WS |
|---|---|---|---|---|---|---|
| 同步（假设仍在 UI） | 全文件 | 2.070 | 0.52 | 0.80 | **2069.70 ms** | 1174.3 MB |
| 后台线程（当前实现） | 全文件 | 1.758 | 10.05 | 11.13 | **12.70 ms** | 1174.3 MB |
| 同步（假设仍在 UI） | 24M（真实路径） | 0.257 | 0.54 | 0.54 | **256.4 ms** | 259.6 MB |
| 后台线程（当前实现） | 24M（真实路径） | 0.266 | 10.01 | 10.76 | **11.17 ms** | 259.5 MB |

> 本次实测 1.1 GiB 全量读盘 2.070 s、峰值 1174 MB（文件刚由探针写入，页缓存是热的）；早前 4.90 s / 1.04 GB 是另一个窗口/另一份文件，不作为本轮基线。

**“移到线程后卡顿降到多少”的估算方法与误差**

- 公式：`T_block_thread ≈ max(心跳间隔, 单块转换 + 进度信号投递 + 事件循环调度)`；`单块转换 ≈ T_load × chunk_samples / limit`。
- 代入全文件：`2069.70 × 1,048,576 / 144,179,200 ≈ 15.05 ms`，实测 max 12.70 ms（numpy/`fromfile` 期间释放 GIL，且进度事件是排队投递）。
- 误差：心跳 10 ms → 单次 gap 的分辨率是 **±10 ms**（即实测 12.70 ms 对应真实区间约 2.7–22.7 ms）；p95 11.13 ms 可作为稳态上限。若 worker 里出现纯 Python 逐样本循环（不释放 GIL），公式失效，会退化到接近 `T_load`。
- 降幅：全文件 **2069.7 → 12.7 ms（-99.39%）**；真实 24M 路径 **256.4 → 11.2 ms（-95.6%）**。

期望字段：`replay.cases[].mode/max_samples/file_mb/wall_s/gap_p95_ms/gap_max_ms/peak_ws_mb/samples/progress_events`。

### 7.3 频谱帧率（2.4 MS/s / 1024 点）

配置（`set_performance_mode(1)` + `set_fft_size(1024)`）：FFT **1024**、谱线 bin **384**、曲线定时器 **66 ms**（名义 15.15 fps）、瀑布 100 ms、控件 858×283；输入每 50 ms 推 120,000 样本（2.4 MS/s）。

| 指标 | 实测 |
|---|---|
| 6.001 s 内曲线 paint | **78 帧 → 13.0 fps**（名义 15.15 的 86%） |
| 单帧 `paintEvent` | 中位 **8.26 ms** / p95 9.80 / max 10.79 |
| 直接 `_render_spectrum`（同一状态） | 中位 **10.67 ms** / p95 12.10 |
| `push_iq`（每 120k 样本块） | 中位 **1.22 ms** / p95 1.72 |
| 实际帧周期 | **76.9 ms ≈ 66 ms 定时器 + 8.3 ms paint + 2.6 ms 调度/喂数** |

热函数（200 帧 cProfile，按 tottime）：`drawPolygon` 400 次 **0.846 s**（2.12 ms/次、2 次/帧 ≈ 4.23 ms/帧）；`drawPolyline` 200 次 **0.574 s**（2.87 ms/帧）；`_draw_spectrum` 自身 **0.258 s**（1.29 ms/帧）；`drawLine` 1600 次 0.066 s（0.41 ms/帧）；`drawText` 0.035 s；`horizontalAdvance` 0.035 s。**Qt 光栅化（polygon+polyline+line+text）约占 7.7 ms/10.67 ms = 72%**，Python 侧 `_draw_spectrum` 自身约 12%，没有其它 Python 函数超过 0.5%。

**Python 热循环对比（同一 1024 点数据）**

| 微基准 | 中位 |
|---|---|
| `20*log10(abs(fft))` 向量化 | **0.011 ms** |
| 同语义纯 Python `for` 循环（1024 点） | **0.178 ms**（慢 16.2×） |
| `_resample_max` 1024→384 的逐 bin 循环 | **0.983 ms** |
| `np.maximum.reduceat` 等价向量化 | **0.017 ms**（快 57.8×） |

`_resample_max` 在 `push_iq` 里（`spectrum_widget.py:536`）确实占了 push 的 80%（0.98/1.22 ms），但 push 每 50 ms 才跑 1 次（约 2% 占空），**不是帧率瓶颈**；换向量化只能省 ~1 ms/50 ms。另：1024/384 不能整除，当前循环用 `max` + 空 bin 置 NaN，`reduceat` 语义不同，不能直接替换；若要走向量化，应选能整除的 bin 数（如 1024/256）。

**结论**：帧率上限由 `1/(定时器间隔 + 单帧 paint + 调度)` 决定（66 + 8.3 + 2.6 ≈ 76.9 ms → 13.0 fps），不是 Python 循环；要把 13 fps 抬到 15 fps，得降 `drawPolygon/drawPolyline` 的光栅成本或调小谱线 bin/采样密度——属绘制改动，本轮未做。

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
| 真实 Celestrak ×3 | 全部 **SSL handshake 超时**：10.031 / 10.012 / 10.011 s |
| `TLEManager.fetch(25544, "ISS")` 首次 | **10.009 s**（网络失败后回退到离线/缓存，返回 ok）；第二次 0.000 s |
| 本机黑洞服务器（收连接不回包） | **10.010 s**（socket 读超时先触发） |
| 硬截止改 1 s 验证 | **1.011 s**（证明硬截止路径真实生效） |

**真实最坏耗时 = 10.031 s**（5 次联网尝试的最大值）；理论上限 **15 s**（若 DNS/连接阶段卡住、socket 超时没触发，由硬截止兜底）。本次三次真实请求都吃满 10 s，说明当前网络到 celestrak 不可达，**不是“偶发慢”**。

**是否卡 UI**：`_compute_refresh` 跑在 `threading.Thread`（`desktop/new_spacetime_panel.py:654`），网络等待不占 GUI 线程，只延长“刷新中”状态；启动只构造 `TLEManager()`（`new_spacetime_panel.py:278`），不发请求，所以 §7.1 的启动账里不含这 10 s。

**“超时改可配”的代价**：构造函数已有 `timeout` 参数，改动面 = ① 在 `new_spacetime_panel.py:278` 把设置值传进去（1 行）；② `_bounded_urlopen` 的硬截止固定为 `timeout + 5.0`，要独立可配就得加第二个参数/比例（1 个函数签名 + 调用点）；③ 设置面板加一个键 + 校验（1 处 UI + schema）；④ `tle_fetch.FetchPolicy` 的 15/20 默认值要删掉或标注“未被使用”，避免两套默认值；⑤ 文档 `docs/error-code-visibility.md` 里“ai_timeout 不影响 TLE”的说明要改；⑥ 无现成测试覆盖（`tests/` 里搜不到 `TLEManager`），需要补一条超时用例。风险：实测 3 次都吃满 10 s，把默认调到 <10 s 会把“本来能成功的慢网”判死；调到 >10 s 只会让“刷新中”更久，不会卡 UI。

期望字段：`tle.config.*`、`tle.celestrak_real[].wall_s`、`tle.manager_fetch`、`tle.blackhole_timeout.wall_s`、`tle.blackhole_hard_deadline_1s.wall_s`。

### 7.5 证据文件

- `docs/perf-evidence/deep_perf_20261106.json`：四个小节的原始 JSON（含每次独立进程的 runs、A/B、cProfile top、微基准、TLE 原始耗时）。
- 探针：`scripts/perf_probe_deep.py`（只读产品代码，不改任何源文件；回放测试文件建在 `D:\mbdsdr\.tmp` 并默认当场删除）。

---

## 8. 性能账第三刀（2026-11-06：eventFilter 分型减负 / 启动枚举后台化 / 帧周期拆账 / TLE 超时裁定）

> 复跑：`python scripts/perf_probe_deep.py --section startup --runs 3 --json docs/perf-evidence/deep_perf_third_20261106.json`
> 　　`$env:SPECTRUM_PRECISE=1; python scripts/perf_probe_deep.py --section spectrum`（PreciseTimer 对照）
> 条件：同 §7（i5-13420H / 12 线程 / 15.7 GB / Python 3.14.0 / PySide6 offscreen 软件光栅 DPR=1 / 未设 `MBDSDR_NO_HW`，枚举到 1 台 RTL-SDR）。**同机仍有其它会话在跑测**，所以所有改前/改后一律**交替子进程 A/B**，绝对值只作趋势参考。
> 本节数字与 §7.1 冲突时**以本节为准**：§7.1 是改前快照，且其中「首帧后枚举 1543.5 ms」的归因已被拆解纠正（见 8.2）。

### 8.1 eventFilter：按事件类型分型 + A/B + 行为回归

**口径先对齐（2026-11-06 05:20 补测）**

§8 初稿把「改后口径的分型表（78,040 次 / 280.4 ms）」和「改前→改后 A/B 省下 2028 ms」并列，这两个数**不同口径、不能互相校验**：280.4 ms 是**只统计新实现**的一次运行，2028 ms 是**新旧两套实现**在同一会话交替跑出的构造差。现已补跑同口径对照：旧/新实现**交替**各 3 次（同一台机、同一次探针会话、同一 `STARTUP_EF_PROFILE` 计时方式），每类取中位；A/B 为同一批子进程。

| 事件类型 | 旧 次数 | 旧 累计 ms | 旧 单次 µs | 新 次数 | 新 累计 ms | 新 单次 µs |
|---|---:|---:|---:|---:|---:|---:|
| `PaletteChange` | 19,510 | 520.3 | 26.67 | 19,510 | 109.6 | 5.62 |
| `FontChange` | 19,379 | 513.2 | 26.48 | 19,379 | 108.2 | 5.58 |
| `StyleChange` | 12,247 | 335.5 | 27.39 | 12,247 | 58.8 | 4.80 |
| `DynamicPropertyChange` | 4,188 | 134.6 | 32.14 | 4,188 | 24.2 | 5.78 |
| `ChildAdded` | 2,855 | 89.7 | 31.42 | 2,855 | 13.1 | 4.59 |
| `Create` | 1,876 | 57.5 | 30.65 | 1,876 | 7.1 | 3.78 |
| `Polish` | 1,887 | 56.8 | 30.10 | 1,887 | 8.8 | 4.66 |
| `ChildPolished` | 1,442 | 42.1 | 29.20 | 1,442 | 6.7 | 4.65 |
| `Timer` | 1,066 | 18.5 | 17.36 | 1,068 | 18.5 | 17.32 |
| **合计（全部类型）** | **78,066** | **1955.2** | **25.04** | **78,099** | **425.4** | **5.45** |

上表 9 类覆盖 82.6% 调用量、90.4% 计时；其余是 `ParentChange`/`Destroy`/`ActionChanged`/`MouseTrackingChange` 等一次性类型，两次会话的归属不稳定（同一次启动里此消彼长），不拿来对比。**同口径结论：旧 25.04 → 新 5.45 µs/次（-78.2%），eventFilter 段 1955.2 → 425.4 ms（-78.2%）。** 该表与 A/B 的 1991.5 ms 同量级（分型只覆盖过滤器本身，A/B 覆盖整段构造）；表内绝对值含 `QEvent.Type(...).name` 的采样开销（约 +1 µs/次），所以**不要拿 425.4 ms 与 §8 旧稿的 280.4 ms 比较**——那是另一次运行、同一实现的非采样统计，差异来自机器负载（见下）。

前三类样式传播事件 51,136 次（65.5% 调用量、69.5% 旧耗时），单次旧 26.5–27.4 µs、新 4.8–5.6 µs。真正贵的是 `Timer`（17.3 µs，两种实现都一样——它要真的进基类派发，本改动对它无效）。

**三个构造基准数字为什么打架（4314.3 / 3459.2 / 1431.2）**

| 数字 | 来源 | 同代码复测 | 归因 |
|---|---|---|---|
| 4314.3 ms | §7.1 中位（4054.2–4483.1），04:10 前后 | — | **机器负载**：当时打包线在跑 PyInstaller（`claims.json` 有 15 条过期 claim、`.tmp` 下 `pyi-build2..14`），同会话导入也偏慢 |
| 3459.2 ms | §8 初稿 A/B 的「旧实现」中位，04:50 | 3555.4 ms（**+2.8%**） | 与本节同代码，差 2.8% = 正常抖动 |
| 1431.2 ms | §8 初稿 A/B 的「新实现」中位，04:50 | 1563.9 ms（**+9.6%**） | 同上；new 段本身更短，相对抖动更大 |
| 3555.4 / 1563.9 ms | 本节同口径 A/B（05:20） | — | 同一会话交替，**唯一可比的改前/改后数** |

即：4314.3 → 3459.2 的 855 ms **不是谁的代码改动**（3459.2 与现在的 3555.4 是同一份 HEAD 代码，只差 2.8%）；它是 04:10 那轮被并行打包构建挤出来的。**结论：跨会话的绝对构造时间只能看趋势，改前/改后只认同一会话交替 A/B。**

**减负做了什么（`desktop/main_window.py` `eventFilter`）**

1. `from PySide6.QtCore import QEvent` 从**每次调用内**提到模块顶部（旧实现 7.8 万次调用 = 7.8 万次 `import` 语句）；
2. `event.type()` 从**最多 6 次**降为 **1 次**，先按类型分派；
3. 旧实现无论什么事件都要做 **4 次 `getattr` + 4 次身份比较**；新实现只在 `Resize` / `Enter` / `Leave` 分支里做身份比较，样式类事件直接落到 `super().eventFilter()` 的早退路径；`Enter`/`Leave` 合并为一次 `etype == QEvent.Enter or etype == QEvent.Leave`。

**A/B（同会话交替子进程，n=3，只测 `MainWindow.__init__` 到构造结束）**

| | 第 1 次 | 第 2 次 | 第 3 次 | 中位 |
|---|---:|---:|---:|---:|
| 改前（按 HEAD 原样重建的旧 `eventFilter`） | 3555.4 | 4292.2 | 3199.3 | **3555.4 ms** |
| 改后 | 2226.4 | 1563.9 | 1541.5 | **1563.9 ms** |
| 差值 | | | | **-1991.5 ms（-56.0%）** |

上一会话同法复测为 3459.2 → 1431.2（-2028.0 ms / -58.6%）；两次会话的差值比例 -56.0% / -58.6% 一致，绝对值随负载浮动 ±10%。**只摘掉 eventFilter（置空为只调基类）现在只省 315.5 ms（1552.9 → 1237.4），即新实现下过滤器只剩构造总时间的约 20%**。

**哪些类型不能减、为什么**

- `PaletteChange`/`FontChange`/`StyleChange`（52k 次）**不能从过滤器里摘掉**：`MainWindow` 装的是**应用级**过滤器（`main_window.py:858` `app.installEventFilter(self)`），这三类是 Qt 在 `setStyleSheet`/`setFont`/`setPalette` 后向每个控件递归派发的传播通知。过滤器必须收到并**放行**（返回 `False`），拦截=主题/字体/样式不生效。要少收只能卸掉应用级过滤器，但 `ShortcutOverride`（Ctrl+C 在文本框内放行复制）和 `Resize` 重排都依赖它——所以这 52k 次的成本只剩「过滤器入口本身」，已压到 PySide6 虚函数调用的地板（~3 µs）。
- `Timer`/`MetaCall`/`Paint` **不能减**：它们不是「多余通知」，是应用自己的定时器、跨线程信号和首帧前真实绘制；早退路径已经只做一次类型比较。
- `ChildAdded`/`Polish`/`Create`/`ChildPolished`/`Move` 是控件树构建的固有事件，发生在 `_build_kernel_panels`/`_build_mode_shell` 内部；删掉等于不建控件。

**行为回归（9 个测试文件，一次跑完：`67 passed, 1 xfailed, 2 warnings in 38.27 s`）**

| 依赖事件路径的功能 | 测试文件 | 结果 |
|---|---|---|
| 快捷键注册表/冲突/菜单绑定（11 项） | `tests/test_shortcuts_guard.py` | 11 PASSED（含 `test_no_raw_key_literals_outside_registry`、`test_every_registered_id_is_used_by_main_window`） |
| Esc 关闭浮层/退出天象仪（3 项） | `tests/test_shortcuts_guard.py` | PASSED |
| Ctrl+C 焦点链（4 项） | `tests/test_ctrl_c_focus_guard.py` | 4 PASSED（`test_ctrl_c_goes_to_text_widget_not_global_action` 等） |
| 快捷键焦点优先级（A 键不劫持频谱 AM，4 项） | `tests/test_shortcut_focus_priority.py` | 4 PASSED |
| 触摸模式（17 项：单例/尺寸/强制触摸/捏合/设置往返） | `tests/test_touch_adaptation.py` | 17 PASSED |
| 关闭确认（录音中关窗：取消保留/保存后退出/保存失败留在窗口，3 项） | `tests/test_close_recording_guard.py` | 3 PASSED |
| 设备列表填充/切换/失败回滚/断开清理（6 项） | `tests/test_device_switch_flow.py` | 6 PASSED |
| 无设备空态（横幅文案/连接对话框禁用/刷新进度，4 项 + 3 项） | `tests/test_no_device_onboarding.py`、`tests/test_desktop_ui_responsiveness.py::TestAutoEnumerateNoDevice` | 7 PASSED |
| 断连/重连（手动路径，6 项；热插拔自动恢复 1 项） | `tests/test_desktop_ui_responsiveness.py::TestCleanDisconnect`、`tests/test_desktop_hotplug_autorecovery.py` | 6 PASSED；热插拔 **XFAIL（strict）**，原因是产品尚未接入自动重连（`tests/test_desktop_hotplug_autorecovery.py:68-72` 自带说明，与本改动无关） |

期望字段：`startup.ef_profile`（类型 → [次数, 累计 ms]）、`startup.ef_opt_ab.median_old_ms/median_new_ms/delta_ms/delta_pct`、`startup.median.eventfilter_ms/eventfilter_calls`。

### 8.2 首帧后枚举 + 硬件连接后台化

**claim 结果**：动手前 `python scripts/file_claim.py status` 里 `desktop/main_window.py` **未被占用**（原持有方已释放），可以拿；拿到后按 UX 线要求**立即释放**，本线改动留在工作树、等 UX 线落库后再提交（未夹带他线改动，见 §8.5）。

**先纠正 §7.1 的前提**：「首帧后后端枚举 **1543.5 ms**」不是一次 `enumerate_all_sdr_devices()` 的耗时，而是「首帧 → 启动自动连接结束」的整段 span。按相位时间戳拆开（3 次）：

| 组成 | 实测（3 次） | 说明 |
|---|---|---|
| `enumerate_all_sdr_devices()`（启动路径这次） | **74 / 85 / 114 ms** | `enum_worker_start → build_backend_start` |
| `build_backend_for_device()` | 0.1 ms | 只 new 对象 |
| `backend.connect()`（硬件） | 18.7 / 1057.4 / 1083.7 ms | USB 打开 + 调谐器探测，**硬件/驱动态波动很大** |
| UI 挂接 + `_start_iq_streams` | 844.0 / 928.8（中位）/ 1013.5 ms | 构建流水线 + 启动音频，仍在 GUI |

另外 `startup.median.backend_enum_ms = 954.4` **不是单次枚举**，而是进程内**所有枚举调用之和**（`device_panel` 首帧后刷新、`module_panel` 后台刷新、启动路径都算在内），每次 ~0.8–1.3 s；不要拿它当启动单次成本。

**改动**：`_auto_enumerate_and_connect_async()` 起 `threading.Thread`（`auto-sdr-enum`，daemon），后台依次做 **enumerate → `_pick_auto_device` → `build_backend_for_device` → `backend.connect()`**（全程不碰控件），结果经 `_auto_enum_ready`（`Qt.QueuedConnection`）回 GUI；GUI 侧只做标签/状态切换 + `_start_iq_streams`。同步版 `_auto_enumerate_and_connect()` 保留原语义，测试/直接调用路径不变。

**A/B（交替子进程，n=3，其中 1 次当轮没连上设备，不计入连接类指标）**

GUI 线程「忙」区间定义：同步版 `enum_worker_start → connect_backend_end`（枚举+connect+挂接都在 GUI）；异步版 `auto_ready_start → connect_backend_end`（只有挂接在 GUI）。

| | 第 2 次 | 第 3 次 | 中位 | 变化 |
|---|---:|---:|---:|---|
| 改前（同步，GUI 内联） | 2242.4 | 3041.8 | **2642.1 ms** | |
| 改后（异步） | 1099.2 | 913.4 | **1006.3 ms** | **-1635.8 ms（-61.9%）** |
| 心跳最大间隔（旁证，低估） | 1219.4 / 1734.4 | 1108.4 / 919.2 | 1476.9 → 1013.8 ms | -463.1 ms（-31.3%） |

> 心跳只作旁证：`_connect_backend` 内部会调 `QApplication.processEvents()`，被它服务的心跳会低估阻塞，所以主指标用相位时间戳。

**残余大头**：改后 GUI 仍占 **1006 ms**，其中 `_start_iq_streams` **844.0–1013.5 ms（中位 928.8）**，占 92%。本轮已拆账：真因是 GUI 线程首次 `import scipy.signal`（`VFO.__init__` 触发，空载 1016.5 ms），**不是** pipeline 构建或音频设备所有权；方案与 A/B 见 §8.6，**未落库**。

**已补：自动连接期间的状态栏提示（2026-11-06）**：`_auto_enumerate_and_connect_async()` 在起线程前写 `statusBar().showMessage("正在连接设备…", MSG_NORMAL)`（非阻塞；成功/失败/无设备提示会覆盖它）。手动连接路径不变。测试钉住：`tests/test_desktop_ui_responsiveness.py::TestAutoEnumerateNoDevice::test_async_auto_connect_shows_status_hint`（1 passed；后台 worker 运行期间断言 `statusBar().currentMessage()` 含「正在连接设备」）。**落库状态**：该 hunk 与 `_auto_enumerate_and_connect_async` 同属 `desktop/main_window.py`，本轮检查时该文件被 `操作逻辑线` 持有（2026-11-06 05:10:31 起，pid 33008，60 min TTL；05:18 取证剩余 53.7 min），按纪律不夺取、不夹带；hunk 与测试留在工作树，待该文件释放后落库（提交边界见 §8.5）。

**行为验证**：设备列表填充/切换/失败回滚 = `test_device_switch_flow` 6 PASSED；断开清理 = `TestCleanDisconnect` 3 PASSED；无设备空态 = `TestAutoEnumerateNoDevice` 3 + `test_no_device_onboarding` 4 PASSED；自动连接/热插拔 = 热插拔严格 `XFAIL`（产品未接入自动重连，非本改动引入）。合计见 8.1 的 67 passed。

期望字段：`startup.enum_bg_ab_detail.sync[]/async[].enum_worker_ms/connect_backend_ms/start_streams_ms`、`startup.enum_bg_ab_detail.*[].auto_ready_ms`；GUI 忙区间用 `startup.enum_bg_ab_hb_max.old_ms[]` 与 `.new_ms[]` 里的 `enum_worker_start`/`auto_ready_start` → `connect_backend_end` 相减得到（原始相位戳都在证据 JSON 里）。

### 8.3 帧周期拆账：13.16 fps 的 13.2 ms 去哪了

配置同 §7.3（2.4 MS/s、FFT 1024、384 bin、曲线定时器 66 ms、控件 858×283、每 50 ms 推 120,000 样本）。

| 项 | 实测 | 来源 |
|---|---:|---|
| 6.003 s 内 paint | 79 帧 → **13.16 fps**（名义 15.15） | `measured.paints/elapsed_s/fps` |
| 定时器实际间隔 | p50 **79.20** / p95 82.02 / max 85.82 ms（n=76） | `timer_gap` |
| 单帧 paint | p50 **8.19** / p95 10.89 / max 11.68 ms | `paint` |
| 定时器到 paint 的延迟 | p50 **0.58** / p95 2.18 ms | `paint_latency_from_timeout` |
| 喂数摊薄 | **1.31 ms/帧**（`push_iq` 1.05 ms × 95 次 / 76 帧） | `push_iq` |
| 事件循环其它占用 | **3.44 ms/帧**（600 个 10 ms 心跳 gap 累计超出 261.5 ms） | `heartbeat_blocked_ms_per_frame` |
| 其中 eventFilter（首帧后） | **0.25–0.43 ms/帧**（890–1,497 事件/s × 3.59 µs） | `ef_after_show_*` |

**拆账（以定时器为准，避免与心跳重复计数）**

```
79.20 ms（实测周期）
= 66.00（interval）
+ 0.58（timeout → paint 开始）
+ 8.19（paint）
+ 4.43（paint 结束 → 下一次 timeout 重排：喂数、面板/星表定时器、eventFilter、投递）
```

4.43 ms 有两条独立旁证：心跳阻塞 3.44 ms/帧（会与 paint 部分重叠，只能当下界）+ 可点名子项 `push_iq` 1.31 + eventFilter 0.25–0.43 ≈ 1.56–1.74 ms。**结论：13.2 ms 全部可归因，没有「约 10 ms 去向不明」**；若按「要达成 15.15 fps 需要周期回到 66 ms」来算，缺口是 13.2 ms，其中 **62% 是 paint**。

**PreciseTimer 对照（排除「Qt 定时器精度」这条解释）**

| | CoarseTimer（默认） | PreciseTimer | 差 |
|---|---:|---:|---:|
| 定时器间隔 p50 | 79.20 | 78.82 | **-0.38 ms** |
| 单帧 paint p50 | 8.19 | 8.57 | +0.38 ms |
| fps | 13.16 | 13.00 | -0.16 |

换 `Qt.PreciseTimer` 没有改善（-0.38 ms 落在单次运行噪声内，fps 反而低 0.16）。根因是 **QTimer 重复定时器的语义**：下一次 timeout 从本次回调返回后重新计时，周期 = `interval + handler`，不会补偿已消耗的 13.2 ms。这是「结构性扣除」而非「漂移」，也不是 CoarseTimer 的 5% 粒度（若真是粒度问题，Precise 应显著改善）。

**裁定**

| 类别 | 结论 |
|---|---|
| 可改善 | paint 8.19 ms（其中 Qt 光栅 `drawPolygon`+`drawPolyline`+`drawLine`+`drawText` 约占 72%，见 §7.3）；事件循环 4.43 ms 里的面板/星表定时器可再合并 |
| 固有（不可消除，但可补偿） | `interval` 66 ms 本身 + 0.58 ms 派发延迟 + 重复定时器不补偿 handler 时间 |
| 唯一能把「实测 fps」拉回 15.15 的杠杆 | 把性能档曲线定时器 66 → **约 53 ms**（66 − 13.2），代价是每秒多渲染约 2 帧、光栅占空比 +16%。属产品取舍（帧率 vs CPU），本轮**未改** |

期望字段：`spectrum.measured.fps/paint/timer_gap/paint_gap/paint_latency_from_timeout/heartbeat_gap*/timer_gap_median_minus_interval_ms/timer_type`；对照跑 `spectrum_precise.measured.*`。

### 8.4 决策记录：TLE 超时**不做成可配**

| 项 | 内容 |
|---|---|
| 决策 | 维持 `TLEManager(timeout=10.0 s)` + 硬截止 `timeout + 5.0 = 15.0 s`，**不**暴露为用户可配项 |
| 日期 | 2026-11-06 |
| 状态 | 已裁定（本条取代 §7.4 末尾「代价」段的悬置表述） |
| 依据（实测） | 真实 Celestrak 联网 3 次全部 SSL handshake 超时，**10.031 / 10.012 / 10.011 s 吃满**；黑洞服务器 10.010 s；`TLEManager.fetch` 首次 10.009 s；把硬截止改 1 s 验证 1.011 s（证明硬截止路径真实生效）。最坏真实耗时 **10.031 s**，理论上限 15 s |
| 理由 1：六步代价 | ① `desktop/new_spacetime_panel.py:278` 要把设置值传进 `TLEManager(timeout=)`（1 行）；② `_bounded_urlopen` 的硬截止写死 `timeout + 5.0`，要独立可配就得加参数/比例（1 个函数签名 + 全部调用点）；③ 设置面板加键 + 校验 + schema（1 处 UI）；④ `mbdsdr_ai/tle_fetch.py` 的 `FetchPolicy`（request 15 s / hard 20 s）与 `TLEManager` 是两套默认值，删或标注未使用，否则口径分叉；⑤ `docs/error-code-visibility.md` 里「ai_timeout 不影响 TLE」的说明要改；⑥ 现有测试对 `TLEManager` 零覆盖，需新增超时用例（联网与黑洞两种） |
| 理由 2：误杀慢网 | 实测三次都吃满 10 s ⇒「慢网」是常态而不是异常。默认值调到 <10 s 会把本来能成功的慢网判死（误杀），且失败提示会与「网络不可达」混淆；调到 >10 s 只延长「刷新中」的显示时长，不产生任何收益（网络等待在后台线程，不卡 UI） |
| 理由 3：收益为零 | 可配的唯一用途是「让用户更快放弃」。当前放弃成本只是状态栏/面板停在「刷新中」，没有阻塞任何交互；为此引入 6 步改动面不划算 |
| 若日后要做：最小改动面 | 必须项 **①+②**（把 timeout 与硬截止成对传进去，避免「软超时 5 s / 硬截止仍 15 s」的错配）；配套项 ③④⑤⑥ 不做就是口径分叉/文档说谎。默认值建议保持 10 s，可配范围下限不得 <5 s（低于实测最坏 10.03 s 必然误杀） |
| 复验 | `python scripts/perf_probe_deep.py --section tle`；期望字段 `tle.config.*`、`tle.celestrak_real[].wall_s`、`tle.blackhole_timeout.wall_s`、`tle.blackhole_hard_deadline_1s.wall_s` |

### 8.5 证据文件与提交边界

- `docs/perf-evidence/deep_perf_third_20261106.json`：本轮三份原始输出的合并快照——`startup`（EF 分型、旧/新 EF A/B、sync/async 枚举 A/B、全部相位时间戳）、`spectrum`（CoarseTimer）、`spectrum_precise`（PreciseTimer 对照）。顶层带 `conditions`/`commands`。
- 合并源（临时产物，已删除）：`.tmp\startup_v3.json` + `.tmp\spectrum_v3.json` + `.tmp\spectrum_precise.json`；探针脚本自身的 A/B 开关是 `STARTUP_EF_PROFILE` / `STARTUP_OLD_EF` / `STARTUP_SYNC_ENUM` / `SPECTRUM_PRECISE`。
- 提交边界：`desktop/main_window.py` 只提交本线 9 个 hunk（Qt 导入、`_auto_enum_ready` 信号、`__init__` 线程字段、启动定时器目标、`eventFilter`、`_auto_enumerate_and_connect*` 系列、`_connect_backend(already_connected=)`）；**同文件里他人未落库的 hunk 不进本次提交**（用私有 `GIT_INDEX_FILE` + `check_commit_scope.py --staged --expect` 精确核对）。
- 本轮（2026-11-06 05:xx）新增证据：`startup`（`scipy_prewarm_ab`、`_build_pipeline` 子调用拆账）、`spectrum` 66/53 交替与 300 s 长跑、`streams`（同进程 3 次 VFO/pipe 构造）。合并源 `.tmp\startup_v8.json` + `.tmp\sp66_0/1.json` + `.tmp\sp53_0/1.json` + `.tmp\spectrum_53_long.json` + `.tmp\streams_v1.json`（合并后删除）。
- 本轮 `desktop/main_window.py` 的状态栏提示 hunk **未落库**：文件被 `操作逻辑线` 持有（2026-11-06 05:10:31 起，pid 33008，60 min TTL），按纪律不夺取；`tests/test_desktop_ui_responsiveness.py` 的新用例同样留在工作树（依赖该 hunk，单独提交会让干净检出变红）。

### 8.6 下一刀：`_start_iq_streams` 844–1013 ms 的真因（未落库）

**结论先行**：这笔时间不是 pipeline 构建，也不是音频设备所有权问题。拆账后 **92–99.9% 落在 `VFO.__init__`**，而 `VFO.__init__` 慢的唯一原因是 **GUI 线程第一次 `import scipy.signal`（1016.5 ms）**。

**同会话拆账（真机，`--section startup --runs 1`）**

| 子项 | 未预热（本次） | 预热 `scipy.signal` 后 | 旧观测（§8.2） | 说明 |
|---|---:|---:|---:|---|
| `_start_iq_streams` 整段 | 1214.0 ms | **119.5 ms** | 844.0–1013.5 | 含声卡打开 |
| `AudioPlayer.start()` | 99.8 ms | 116.3 ms | — | 真实声卡打开；`MBDSDR_NULL_AUDIO` 下约 60 ms |
| `_build_pipeline()` | 1114.2 ms | **3.1 ms** | — | 差值 1111.1 ms |
| 　└ `VFO.__init__` | **1112.9 ms** | **1.1 ms** | — | 占 build 的 99.9% |
| 　└ `ReceivePipeline.__init__` | 0.3 ms | 0.4 ms | — | 创建 4 个 QThread + 5 个乒乓流 |
| 　└ `ReceivePipeline.start()` | 0.6 ms | 0.9 ms | — | 只启动线程，不 join |
| 　└ `_update_main_vfo_offset` | 0.0 ms | 0.1 ms | — | `push_offset` |
| `import scipy.signal`（预热线程） | — | 1381.7 ms（后台） | — | 空载微基准 1016.5 ms |

旁证一（同一进程内 3 次 VFO 构造，`.tmp\streams_v1.json`）：1264.8 → 0.6 → 0.5 ms。若慢在 pipeline/线程，三次应该同量级；只有第一次慢，说明是**一次性导入**。
旁证二（空载微基准）：`import scipy.signal` 1016.5 ms、首次 `firwin` 0.3 ms、预热后 `VFO(...)` 0.5 ms、后续 0.38–0.45 ms。
旁证三（同会话预热 A/B）：`hb_max_ms` 1298.6 → 209.9 ms，连接阶段的 1 s 阻塞随预热消失。

**方案（按收益/风险排序）**

| 方案 | 做法 | 收益 | 风险/成本 | 结论 |
|---|---|---|---|---|
| **A（推荐）** | 启动早期起一个 daemon 线程只做 `import scipy.signal`，与枚举/连接并行（`desktop/main.py` 或启动定时器处，1 处 + 1 线程） | `_start_iq_streams` -90.2%、`_build_pipeline` -99.7%、GUI 忙区间 -1.09 s | 纯模块导入，**不碰任何 QObject/信号**；失败 `ImportError` 直接忽略，VFO 首次构造回落到懒加载，行为与现状等价 | 落库候选 |
| B（不推荐） | 把 `ReceivePipeline` 构建搬 worker 线程 | 预热后只剩 3.1 ms | `ReceivePipeline` 是 `QObject`，`__init__` 里创建的 4 个 QThread 随父对象亲和；搬到 worker 后 `start()/shutdown()` 必须在 worker 线程调用，GUI 侧持有的 `self._pipeline` 变成跨线程对象。要安全就得把 pipeline 留在 GUI、只回传纯 numpy 的 `taps`/`resamp_window`（信号 object），失败回滚 = 保持懒加载；断开清理顺序仍为 `_health_timer.stop()` → `reader.shutdown()` → `splitter.stop()` → 下游 shutdown → `wait(2000)` → 解绑 VFO → `audio.stop()` | 风险/收益倒挂 |
| C（不推荐） | 把 `import scipy.signal` 提到 `dsp.py` 模块顶层 | 无 | 1 s 只是从「连接后」移到「导入时」，首帧更晚，总启动不变 | 用户感知更差 |

**音频设备的所有权**：`AudioPlayer` 是普通类（不是 `QObject`），`start()` 内部只有 `sounddevice.OutputStream` + `stream.start()`，回调线程不碰 Qt；理论上可在 worker 调用并把 `ok` 写回 GUI。但预热后它只剩 99.8–116.3 ms（`MBDSDR_NULL_AUDIO=1` 下约 60 ms），且现有失败分支已经是「返回 False → 状态栏说明」，后台化收益小、要新增一条回写信号，**本轮不做**。

**A/B 数字（同会话交替子进程）**

| | 未预热 | 预热 `scipy.signal` | 变化 |
|---|---:|---:|---:|
| `_start_iq_streams` | 1214.0 ms | **119.5 ms** | **-1094.5 ms（-90.2%）** |
| `_build_pipeline` | 1114.2 ms | **3.1 ms** | **-1111.1 ms（-99.7%）** |
| 心跳最大间隔 | 1298.6 ms | **209.9 ms** | -1088.7 ms |

> 口径：预热在子进程 `MainWindow()` 构造前执行，等价于「预热线程已先完成」。真实方案里这 1–1.4 s 在后台线程与枚举（1.1–1.4 s）/connect（0.02–1.1 s）并行，不占 GUI；绝对值随机器负载浮动（本轮同机仍有其它会话跑测），**只认同一会话交替差值**。

**行为回归（本项未落库，沿用现有基线）**

| 路径 | 测试 | 结果 |
|---|---|---|
| 设备切换/失败回滚 | `tests/test_device_switch_flow.py` | 6 PASSED |
| 断开清理 | `tests/test_desktop_ui_responsiveness.py::TestCleanDisconnect` | 3 PASSED |
| 无设备空态 | `TestAutoEnumerateNoDevice` + `tests/test_no_device_onboarding.py` | 7 PASSED |
| 录音/回放启动 | `tests/test_record_streaming.py` + `tests/test_playback.py` + `tests/test_replay_load_async.py` | **16 passed**（本轮实测） |
| 自动连接状态栏提示 | `TestAutoEnumerateNoDevice::test_async_auto_connect_shows_status_hint` | 1 passed |

**claim**：`desktop/main_window.py` 在 05:10:31 起由 `操作逻辑线` 持有（pid 33008，60 min TTL；05:18 取证剩余 53.7 min），本轮不夺取、不夹带；方案 A 若落在 `desktop/main.py`，该文件的 claim 已过期（05:18 取证 EXPIRED +30.7 min），落库前需先按 `scripts/file_claim.py claim` 走一遍。

### 8.7 53 ms 取舍：先测后定，默认值不动

**复跑**：`$env:SPECTRUM_INTERVAL_MS=66` 或 `53`；`python scripts/perf_probe_deep.py --section spectrum`；长跑加 `$env:SPECTRUM_DURATION_MS=300000`。

**交替 A/B（66 / 53 各 2 次，取中位）**

| | 66 ms | 53 ms | 差 |
|---|---:|---:|---:|
| fps | 13.10 | **16.34** | +3.24 |
| CPU%（进程） | 18.1% | **22.15%** | +4.05 pt（+22.4%） |
| paint p50 | 8.43 ms | 7.96 ms | -0.47 ms |
| paint p95 | 9.86 ms | 9.76 ms | -0.10 ms |
| 心跳阻塞/帧 | 3.56 ms | 2.80 ms | -0.76 ms |
| 实测周期 | 79.20 ms | 61.09 ms | -18.11 ms |

**连续 5 分钟（53 ms，`spectrum_53_long`）**：300.002 s / 4802 帧 = **16.01 fps**，CPU 22.5%，paint p50 8.59 / p95 10.23 / max 17.85 ms。按 60 s 分桶：fps **16.02 / 16.00 / 16.00 / 16.00 / 16.00**，paint 中位 **8.45 / 8.16 / 9.30 / 8.69 / 8.55 ms** → 无退化、无爬升、无内存/定时器漂移迹象。

**结论**

1. **13.16 fps 是定时器语义决定的**（周期 = interval + handler，见 §8.3）；53 ms 把周期压到 61 ms，帧率过冲到 16.3，超过名义 15.15 fps。
2. 若目标是 15.15 fps（周期 66 ms），按实测线性关系 `周期 ≈ interval + 8（paint）+ 3（其余）`，应取 **约 56 ms**，不是 53 ms。53 ms 多出的 3 fps 换来 CPU +22.4%，**单位帧成本反而变差**（paint 只从 8.43 降到 7.96）。
3. **默认值本轮不改**（仍是 66 ms）。不建议把 53 ms 做成无条件默认。
4. 若日后做「性能档自适应」，门槛用 **实测 paint 中位 ≤ 6 ms** 才切到 56 ms（目标 15 fps）；本机 paint 中位 8.59 ms（5 分钟）→ **不满足**，应保持 66 ms。用 53 ms 作为自适应档也需同时满足「paint 中位 ≤ 6 ms」且「CPU 预算允许 +22%」，否则收益是虚的。
