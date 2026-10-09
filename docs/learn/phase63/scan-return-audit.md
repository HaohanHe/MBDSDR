# Phase 63 — Scan Return Audit

## 机制

SDR++-style frequency scanner (`FrequencyScanner`, pure logic state machine) walks a
list of tunings (range step or bookmarks) while the engine feeds it real RSSI.
Previously: when the walk naturally reached the end of the sequence (non-loop,
non-pingpong), the scanner went Idle and the engine stayed tuned at the **last**
scan frequency — the user was left at the endpoint, not back where they were
listening before pressing Scan.

## 三候选判定表

| # | 候选 | 判定 | 证据 |
|---|------|------|------|
| 1 | 扫频自然完成回位到上次频率 | **缺口 → 已落地** | `frequency_scanner.cpp:127` advance() 走到尽头置 Idle，无回位；`main_window.cpp:3521-3523` tick 后仅 needTune 调谐，完成后停末点 |
| 2 | 瀑布拖拽调谐与时间轴拖拽冲突 | **非缺口** | `spectrum_display.cpp:1320-1324` stripRect→Pan（时间轴平移）；`:1326-1392` trace/falls 分区链：cursor→fixed marker→VFO→Tune；`:1327-1330` Shift+拖→Pan。分区互斥、优先级清晰 |
| 3 | FFT 平均/平滑档位 | **非缺口** | `spectrum_widget.cpp:155-162` avgCombo Off/Slow/Fast → `averageModeRequested(idx)`；`main_window.cpp:2298-2299` → `engine_->setAverageMode`；`spectrum_engine.cpp:1088-1089` → `powerSpectrum_.setAverage`。三路信号完整贯通 |

## 落地内容（候选 1）

### FrequencyScanner 层

- `frequency_scanner.h`：新增 `bool finishedNaturally_ = false;` 成员 + `bool finishedNaturally() const;` getter
- `frequency_scanner.cpp:62` start()：清 `finishedNaturally_ = false`
- `frequency_scanner.cpp:105` stop()：清 `finishedNaturally_ = false`（手动 stop 不回位）
- `frequency_scanner.cpp:128` advance()：走到尽头非 loop 时置 `finishedNaturally_ = true`

### MainWindow 层

- `main_window.h:301-305`：新增 `preScanFreqHz_` / `scanReturned_` / `scanReturnToHz_` 成员
- `main_window.cpp:1733-1736` 扫描启动：记录 `preScanFreqHz_ = engine_->centerFreq()`（真实 engine 当前频率）
- `main_window.cpp:3530-3539` scanTimerTick()：自然完成时 `engine_->onSetCenterFreq(preScanFreqHz_)` 一次性回位
- `main_window.cpp:3553-3556` scanStateLabel：`"完成·回 98.500 MHz"`（窄栏短文案）
- `main_window.cpp:3585-3590` sbScan_ 状态栏：`"扫描完成 · 已回 98.500 MHz"`（完整文案）

### 诚实边界

| 场景 | 行为 | 原因 |
|------|------|------|
| 自然完成（走到尽头，非 loop） | 自动回位 | `finishedNaturally_=true`，caller 检测后调谐 |
| 手动 stop 按钮 | 不回位，留在当前位置 | `stop()` 清 `finishedNaturally_=false` |
| Loop 模式 | 不回位（循环无"完成"语义） | `advance()` 回绕，永不走尽头分支 |
| PingPong 模式 | 不回位（来回反转） | `advance()` 在端点翻向，永不 Idle |
| 命中停驻中 | 不触发回位 | 仅在 state==Idle 且 finishedNaturally 时回位 |

## 测试

扩 `test_scanner.cpp` 新增 `finishedNaturallyFlag()` 测试槽，含四个子项：
- (a) 非 loop Up 走到尽头 → `finishedNaturally()==true`
- (b) 手动 stop → `finishedNaturally()==false`
- (c) loop 模式跑 300 tick → `finishedNaturally()==false`，仍 Scanning
- (d) PingPong 跑 300 tick → `finishedNaturally()==false`，仍 Scanning

**测试结果：9 passed, 0 failed**（原 8 + 新增 1）。

## 快照

门控 `MBD_SCANRETURN=1`（先例 MBD_PEAKSHOT/MBD_BMKCOLLAPSE），拍 960/1920：

- **960×640**：右栏"完成·回 98.500 MHz"完整可见；底部状态栏"扫描完成 · 已回 98.500 MHz"完整可见。0 裁切 0 叠字。
- **1920×800**：右栏完整显示扫描面板全部控件 + 状态文本；底部状态栏完整。0 裁切 0 叠字。

## 改动文件清单

| 文件 | 改动 |
|------|------|
| `cpp/src/dsp/frequency_scanner.h` | 加 `finishedNaturally_` 成员 + getter 声明 |
| `cpp/src/dsp/frequency_scanner.cpp` | start()/stop() 清标志，advance() 置标志，getter 实现 |
| `cpp/src/ui/main_window.h` | 加 `preScanFreqHz_`/`scanReturned_`/`scanReturnToHz_` 成员 |
| `cpp/src/ui/main_window.cpp` | 启动记录 pre-scan 频率；tick 后检测自然完成回位；状态标签文案；objectName |
| `cpp/tests/test_scanner.cpp` | 新增 `finishedNaturallyFlag()` 测试槽 |
| `cpp/tests/ui_screenshot_narrow.cpp` | MBD_SCANRETURN=1 门控 |
