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
| 说明 | 测量期间同机还有其它会话在跑 pytest/编译，**绝对启动耗时会被放大**；import 的改前/改后是各 3 次独立进程各 3 轮取得，相对比值可信 |

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
| 2 | 进程起点 → MainWindow 构造完成 | 同上 | 9.38 s（含同机负载；空闲时 cProfile 实测构造本体 2.97 s） | ≤ 3 s | ⚠️ 受同机负载干扰，需独占机器复测 |
| 3 | show + 首帧 paint | 同上 | **0.34 s**（改前 3.48 s） | ≤ 0.5 s | ✅ 达标 |
| 4 | 进程起点 → 首帧可交互 | 同上 | 9.72 s（改前 23.54 s，两次都在同机负载下） | ≤ 5 s | ⚠️ 待独占机器复测 |
| 5 | 天空 700×700 / FOV120 单帧 paint | `--section ui` | **59.51 ms**（median，p95 77.1，n=25）；同机空闲时另测 42.12 ms | 30 fps → ≤33 ms | ❌ 超预算，未解决（仅改调度） |
| 6 | 天空 700×700 / FOV60 | 同上 | 44.73 ms | ≤33 ms | ❌ |
| 7 | 天空 1000×1000 / FOV120 | 同上 | 63.96 ms | ≤33 ms | ❌ |
| 8 | 天空 1000×1000 / FOV60 | 同上 | 40.43 ms | ≤33 ms | ❌ |
| 9 | 天空最大图层 `_draw_stars` | 同上（分层） | **27.39 ms**（占 700px/FOV120 帧 46%） | ≤15 ms | ❌ |
| 10 | 次大图层 `_draw_constellations` / `_draw_dso` | 同上 | 6.36 ms / 4.36 ms | 各 ≤5 ms | ❌ constellations 略超 |
| 11 | 频谱 FFT 曲线 `_draw_spectrum` | `--section ui` | **30.83 ms**（p95 46.89） | ≤16 ms（60 fps） | ❌ 需关/降 AA，视觉有差异，**未改** |
| 12 | 瀑布 `_draw_waterfall` | 同上 | 0.45 ms | ≤5 ms | ✅ |
| 13 | 频谱面板整屏 `grab()` | 同上 | 44.85 ms | ≤33 ms | ❌ |
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

> `mbdsdr_ai/sdr_backend.py`（调谐器类型探测 30 s 缓存 + 无 libiio 时跳过 Pluto TCP 探测）与 `desktop/rf_sky_view.py` 的改动**已在工作区但未提交**——这两个文件当时被其它线持有（见 §5）。

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

### 3.2 天空单帧分层（700×700 / FOV120，中位 42.12 ms，n=25）

| 图层 | ms |
|---|---|
| `_draw_stars` | 17.73 |
| `_draw_constellations` | 4.93 |
| `_draw_dso` | 3.51 |
| `_draw_landscape` | 3.27 |
| `_draw_az_grid` | 2.81 |
| `_draw_planets` | 1.73 |
| 其余（标签/罗盘/地平线/背景等） | 合计 5.96 |
| 残差（无法归因到单个方法） | 2.04 |

同机负载下用 `perf_probe.py --section ui` 复测：700px/FOV120 中位 59.51 ms、`_draw_stars` 27.39 ms。

### 3.3 频谱 AA 开/关（900×400，n=25）

| AA | 中位 ms | 像素差（360000 像素） |
|---|---|---|
| 开 | 39.96 | — |
| 关 | 18.85 | 9059 像素（2.5%），max 通道差 255，>8 有 4533 |

⇒ 关 AA 快 ~2.1 倍，但画面明显变化，**按「不为快牺牲效果」的纪律未改**。

### 3.4 内存（连续 300 帧）

tracemalloc delta **+342 KB**、gc 对象 **+2**、pen 缓存 397 条、brush 缓存 93 条 —— 无只增不减容器迹象。

---

## 4. 未做（明确未测 / 未改）

1. **C++ 内核侧热点**：未做。没有用 perf/VTune 采样，也没有逐行计时，因此**不报任何 C++ 侧结论**。
2. **长时间内存曲线（30 分钟连续频谱）**：未做。只做了 300 帧（约 1 分钟）与 1000 帧短跑。
3. **`_draw_stars` 本身的降本**：未做。当前只改了调度（自适应步长），单帧 42–60 ms 的绘制成本仍在；批量绘制/减少 DSO 命中测试属于绘制改动，需要单独的视觉对比。
4. **启动期 `enumerate_all_sdr_devices` 仍在 GUI 线程同步跑**（cProfile：`MainWindow.__init__` 内 1.141 s，来自 `module_panel.refresh_devices`）：未做异步化。`device_panel` 的轮询路径已异步，但首次构造仍是同步。
5. **频谱 AA 关闭**：未做（见 3.3 的像素差）。
6. **独占机器复测启动绝对值**：未做。本次测量期间同机有其他会话跑测试，绝对耗时偏高。
7. **`docs/learn/dsp-performance-baseline.md` 之外的 DSP 逐档基线**：本次只跑 `--quick` 档，未做全档扫描。

---

## 5. 提交范围与文件占用

本线提交（私有 `GIT_INDEX_FILE`，只打本线 hunks）：

- `desktop/device_panel.py`
- `mbdsdr_ai/__init__.py`
- `mbdsdr_ai/channelizer.py`
- `mbdsdr_ai/demod_wfm.py`
- `mbdsdr_ai/wfm_stereo_lite.py`
- `mbdsdr_ai/windows_setup.py`
- `scripts/perf_probe.py`
- `docs/performance-baseline.md`

**未提交（工作区保留改动，文件被其他线持有）：**

- `desktop/rf_sky_view.py` —— 占用者「月相线」（heartbeat 0.1 min 前）
- `mbdsdr_ai/sdr_backend.py` —— 占用者「接入层健壮性线」（heartbeat 31.6 min 前，TTL 120 min 内视为持有）

---

## 6. 已知的既有失败（非本线引入）

- `tests/test_desktop_constants.py::TestNoSecondSourceOfTruth::test_module_exposes_only_the_shared_constant`
  失败：`desktop/constants.py` 的公开符号多出 `format_sample_rate`、`math`。`git status` 显示该文件**未被本线修改**，属既有状态。
- `tests/test_orphan_connection.py::TestNewOrphanWiring::test_constellation_cleared_resets_stats`
  失败：断言 `'信号: —' == '信号: --'`。`desktop/main_window.py` **未被本线修改**（属其他线的未提交改动），非本线引入。
- `tests/test_spectrum_scale_peak_range.py` 2 个失败（`test_db_axis_drag_updates_readout_and_shared_sliders`、`test_ctrl_db_zoom_shows_value_bubble_at_cursor_and_hides_on_release`）：`desktop/spectrum_widget.py` 未被本线修改，属既有失败。
- `tests/test_desktop_hotplug_autorecovery.py` 整文件 `xfail(strict=True)`（桌面未接入自动重连，见 `docs/limitations-register.md`）。
