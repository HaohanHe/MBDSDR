# Phase63 自动峰值表（核心）+ min-hold 显示模式（次要）干净室落地

> 范围：在已有的跨帧峰跟踪（`dsp::detectPeaks` + `SpectrumDisplay::rescanPeaks` +
> `peaksUpdated`）之上，补齐两块 SDR++ 机制——(1) 面向用户的**自动峰值表**（含
> 强度 top-N、与调谐点的有符号 Δ、点击行直接调谐）；(2) **min-hold** 显示包络。
> 全程 offscreen/CLI，未操控 GUI；只接真实帧 / 诚实空态，零 mock；活动参数走具名
> 常量（token 或本地 `constexpr`），不裸数值；干净室学 SDR++ 机制、不抄其源码。

## 现状复用（动手前已侦察）

- 峰检测已存在：`dsp::detectPeaks`（`src/dsp/peak_detector.cpp`）按谱中位数估计噪声底、
  局部极大值 + 绝对底门限、贪心非极大抑制（~1% N）、-3 dB 带宽，结果按响度降序。
- 跨帧成熟跟踪已存在：`SpectrumDisplay::rescanPeaks`（`spectrum_display.cpp`）把新鲜检测
  与 `tracked_` 做 `kPeakMatchBins` 匹配、`kPeakMinSeenFrames`(3) 帧成熟、
  `kPeakMaxMissFrames`(10) 帧遗忘；成熟峰在 trace 上画 ▼ 三角。
- 峰值表面板骨架已存在：`SpectrumWidget::peakTable_`（`spectrum_widget.cpp`）4 列表格、
  `rebuildPeakTable`、双击 `tuneAndCenter`（仅平移视图，**未真正调 VFO**）。

## 本次缺口与落地

### 1. 自动峰值表（核心）

| 关注点 | 位置 | 说明 |
|---|---|---|
| top-N 强度截断 | `spectrum_display.cpp:56` `kMaxPeakCount=12`；`:662` `mat.resize(kMaxPeakCount)` | 成熟峰先按 dbfs 降序（`:660`）再截断到最强 12 个，弱杂波不淹没面板 |
| 具名 API | `spectrum_display.h:57` `struct PeakEntry{freqHz,dbfs,deltaHz}`；`:193` `peaks()`；`:195` `tunedFrequencyHz()` | 纯读背成熟检测；`deltaHz = freqHz - dialFreqHz_`（相对当前调谐点的有符号偏移），实现见 `spectrum_display.cpp:683` |
| 首帧诚实 emit | `spectrum_display.h:384` `peaksAnnounced_`；`spectrum_display.cpp:676` | 首帧（哪怕空峰）也推一次 `peaksUpdated`，空带才会显示"无信号峰值"行而非空白表格 |
| 表头/列 | `spectrum_widget.cpp:403` `# / 频率(MHz) / 强度(dBFS) / Δ(kHz)` | Δ 列用 `PeakEntry.deltaHz/1e3`，带 `+/-` 号 |
| 空态 | `spectrum_widget.cpp:~462` | 空峰 → 单行跨列居中"无信号峰值（纯噪声）"，不伪造行 |
| 点击行调谐 | `spectrum_widget.cpp:500` `activatePeakRow` → `:510 emit peakTuned(hz)`；接线 `:414/:416`（单击+双击） | 画布先 `tuneAndCenter` 自居中，再发容器信号；`peakTuned` 声明于 `spectrum_widget.h` |
| main_window 接线 | `main_window.cpp:2989` | `peakTuned` 镜像 `frequencyChanged`：同步 freqSpin + `engine->vfoSetOffset(selectedVfoId, hz)`（in-band 滑点，LO 仅在捕获边缘重调，由引擎决定） |

### 2. min-hold 显示模式（次要）

- 缓冲：`spectrum_display.h:377` `minHold_` / `:378` `minHoldOn_`，对称 `maxHold_`。
- 每帧取小并保持（**无 decay**，与 max-hold 的 `kMaxHoldDecayDb` 对称但相反）：
  `spectrum_display.cpp:361` `minHold_[i] = min(fresh, held)`，首帧 `+inf` 播种
  （`allocateRing`，`:~210`），更安静的帧把它拉低、更响的帧不再抬升。
- API：`spectrum_display.h:110` `setMinHoldEnabled`、`:112` `minHoldEnvelopeForTest()`、
  `clearMinHold()`；实现 `spectrum_display.cpp:471`。
- 绘制：`spectrum_display.cpp:734` 构建 `minLine`、`:769` 叠加一条淡青色（`kCursorAColor`
  + `kCursorLineAlpha`，全走既有 token、不裸色值）包络线，与中性 max-hold 线同时可开。
- 工具栏：`spectrum_widget.cpp:179` "Min" 复选框，对称 "Max"（`:164`），转发 `:582`。

## 诚实性 / 不硬编码

- 峰值、Δ、强度全部来自真实 `frame_.dbfs` 检测；纯噪声/无帧 → 空表 + "无信号峰值"行。
- 新参数仅两个：`kMaxPeakCount=12`（本地 `constexpr`）、min-hold 复用既有 token 配色；
  未新增裸数值 UI。
- 快照 harness 的合成帧注入由 `MBD_PEAKSHOT=1` 门控（`ui_screenshot_narrow.cpp`），
  默认关闭，不影响其它既有截图。

## 测试（真实计数，offscreen）

| 目标 | 结果 | 覆盖 |
|---|---|---|
| `test_spectrum_display` | 23 passed（原 19 + 新 4） | 多音峰误差<半 bin、纯噪声空表、Δ=相对调谐点、top-N 截断 |
| `test_spectrum_minhold`（新） | 5 passed | 首帧播种、更静帧拉低、无衰减保持最小值 |
| `test_spectrum_peaktbl`（新） | 5 passed | 双音成行、单击行→`peakTuned` 带真实频率、空带诚实提示 |
| `test_spectrum_maxhold` | 5 passed | 回归 |
| `test_peak_detector` | 4 passed | 回归 |

## 快照结论

`ui_shot_narrow` 在 `MBD_PEAKSHOT=1` 下喂双音合成帧：峰值表面板在默认"频谱"页可见，
两行 `98.145 / -22.0 / -354.6`、`98.897 / -38.0 / +396.9`（Δ 相对 98.5 MHz 调谐点），
640(960) 与 1920 两档下 **0 裁切、0 叠字**；"Min" 复选框与 "Max" 并排可见。
