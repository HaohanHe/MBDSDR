# FFT 点数可配置 —— 现状核实与真缺口锁定（phase63）

HEAD = `3f1f7fb`。本轮是"干净室机制深化"：先侦察核实三候选，只当真缺口落地。

## 0. 一句话结论

**主落地候选「FFT 大小可配置」在 HEAD 上已经完整落地过（Phase43，commit `7d89c5a`），不是缺口。**
任务书给的侦察关键词 `fftSizeCombo` / `点数` 零命中是命名假阴性——实际控件名是 `fftCombo_`、标签是 `"FFT"`（`spectrum_widget.cpp:115-138`）。本轮没有重造 UI，而是：
1. 把已存在的端到端链路逐环核实并写证据；
2. 补上此前**零覆盖**的测试断言（持久化往返 + 非法回退 + 改点数瀑布环诚实重建）；
3. 用快照门控种子端到端证明持久化恢复路径；
4. 对 center/span、解调器音量给出书面评估（不落地）。

---

## 1. 机制（FFT 点数如何工作）

| 环节 | 位置 | 行为 |
|---|---|---|
| 引擎接受档位 | `src/dsp/spectrum_engine.cpp:159-161` | `setFftSize(int n)`：仅 `n∈{1024,2048,4096}` 才 `fftSize_.store(n)`，其余**静默忽略**（不报错、不改值）。 |
| 引擎默认值 | `src/dsp/spectrum_engine.h:558` | `std::atomic<int> fftSize_{2048};` |
| 引擎读回 | `src/dsp/spectrum_engine.h:50` | `int fftSize() const { return fftSize_.load(); }` |
| 引擎每帧使用 | `spectrum_engine.cpp:1201,1278-1289` | 每轮 `n = fftSize_.load()`；`specN = min(n, iq.size())` 向下取整到 2 的幂；`frame.dbfs.resize(specN)`、`frame.fftSize = specN` 随帧发出。**帧自带真实 bin 数。** |
| UI 下拉 | `src/ui/spectrum_widget.cpp:115-138` | 标签 `"FFT"`，`fftCombo_` items `{1024,2048,4096}`，默认 index 1（2048）；`currentIndexChanged → fftSizeRequested(1024/2048/4096)`。 |
| 控件档位映射 | `spectrum_widget.cpp:725-737` | `fftSizeValue()` / `setFftSizeValue(n)`：`kSizes[]={1024,2048,4096}`；**无匹配档位时 idx 恒为 1 → 2048**（非法值诚实回退）。 |
| 信号→引擎接线 | `src/ui/main_window.cpp:2302-2303` | `connect(spectrum_, &SpectrumWidget::fftSizeRequested, engine_, &SpectrumEngine::setFftSize);` |
| 持久化-写 | `main_window.cpp:4168` | `s.setValue("rx/fftSize", spectrum_->fftSizeValue());` |
| 持久化-恢复下拉 | `main_window.cpp:4532` | `spectrum_->setFftSizeValue(s.value("rx/fftSize", 2048).toInt());` |
| 启动下发引擎 | `main_window.cpp:4598` | `engine_->setFftSize(spectrum_->fftSizeValue());` |
| 瀑布环诚实重建 | `src/ui/spectrum_display.cpp:483-484` | `const int bins = frame.dbfs.size(); if (bins != bins_) allocateRing(bins);` |
| 环重建语义 | `spectrum_display.cpp:308-329` | `ringDb_` 按新 bins 重分配、`ringCount_=0`、`ringHead_=0`；`maxHold_`/`minHold_` 同步按新宽度 `assign`。**旧历史被丢弃，从不按新分辨率重采样/拉伸**。 |

**setter 时序**：`setFftSize` 是原子 store，不走 mailbox（对比 `setAverageMode` 等走 `applyControlCommandsLocked` 的命令队列）。UI 线程直接 store，引擎下一轮循环读到新值；无需等 mailbox drain。

### 诚实语义（与任务书要求逐条对照）

- **档位标签数值真实**：`1024/2048/4096` 即帧 `dbfs.size()`，无插值。
- **非法持久值回默认**：`setFftSizeValue(8192)` 无匹配 → 下拉停在 2048（`spectrum_widget.cpp:730-737`）；引擎侧 `setFftSize(8192)` 本身也忽略（`spectrum_engine.cpp:159-161`）。双保险。
- **改点数的瀑布历史**：相邻帧 bin 数变化触发 `allocateRing`，旧行**诚实丢弃**（ringCount 归零），新帧从干净环重建。**没有**把 2048 行缩放成 1024 列这种造假。这与历轮 `wfDepth` 改深度时丢历史的设计取向一致（`waterfallDepthChangeRebuildsRing` 先例）。
- **空态**：无真实设备时引擎走离线 TestSignalSource，帧仍是真实合成 IQ 算出的谱；UI 侧"未连接/未开启"为诚实空态，无 mock 数据。

---

## 2. 三候选判定表

| 候选 | 判定 | 证据（file:line） |
|---|---|---|
| **1. FFT 大小可配置** | **非缺口（已完整落地）** | UI 下拉 `spectrum_widget.cpp:115-138`；接线 `main_window.cpp:2302-2303`；持久化 `main_window.cpp:4168` 写 / `:4532` 恢复 / `:4598` 启动下发；引擎 setter+默认 `spectrum_engine.cpp:159-161` + `spectrum_engine.h:558`；非法回退 `spectrum_widget.cpp:730-737`。侦察关键词 `fftSizeCombo`/`点数` 零命中系命名假阴性。 |
| **2. center / span 快捷面** | **评估项（span 无数值面，交互已存在，本轮不落地）** | center：主调谐 `freqSpin_` + 带边框拖拽 + Ctrl/滚轮 zoom。span：**无独立数值输入/显示**（`grep spanCombo/跨度` 零命中）；span 是 zoom 的纯派生量 `spectrum_display.cpp:239` `spanHz = frameFsHz_ / zoomFactor_`，zoom 交互在 `:1681-1685`（滚轮）与 `:1691` 附近（一格=span/20）。见 §4。 |
| **3. 解调器参数 UI 面** | **squelch 非缺口；音量已存在于设置对话框（非主工具条）** | squelch：`main_window.cpp:692-701` `squelchSlider_` 区间取 `tokens::kSquelchMinDb/MaxDb`，valueChanged 在 `:2406`。音量：`settings_dialog.cpp:85-88` `volumeSlider_` 0-100 默认 80，持久化 `:129/:148` `"rx/volume"`，接线到 `main_window.cpp:2800` `engine_->audioOutput()->setVolume(dlg.volume()/100.0)`，启动恢复 `:2232`。见 §4。 |

---

## 3. 本轮落地（真缺口 = 测试覆盖，非功能）

功能本身不缺，缺的是**断言**。此前 `fftSize` 的引擎级往返仅 `test_engine_integration.cpp:54,58`（默认 2048 / set 4096 读回），而**持久化往返、非法回退、改点数瀑布环诚实重建三条此前零覆盖**。本轮扩既有 QTest 目标，不新增 CMake 目标：

| 新测试 | 文件 | 断言 |
|---|---|---|
| `fftSizePersistsAndRestoresRoundTrip` | `tests/test_ui_integration.cpp` | 真 MainWindow：窗口1 切到 1024 → 落盘 `"rx/fftSize"`；窗口2 新启动下拉=1024 且 `engine->fftSize()==1024`（引擎读回）。 |
| `fftSizeIllegalPersistedFallsBack` | `tests/test_ui_integration.cpp` | 持久化 `"rx/fftSize"=8192` → 下拉与引擎都诚实回到 2048。 |
| `fftSizeChangeRebuildsWaterfallRingHonestly` | `tests/test_spectrum_display.cpp` | 2048 bin 累积 30 行 → 喂 1024 bin 帧 → `waterfallRowCountForTest()` 归 1（旧历史丢弃）、`maxHoldEnvelopeForTest().size()==1024`（新宽度）；再累积到 30 行。 |

**构建与真实计数**（`cpp/build` 增量，`QT_QPA_PLATFORM=offscreen`，离线 TestSignalSource，零硬件）：
- `test_spectrum_display`：**41 passed, 0 failed**（原 40 + 新 1）。
- `test_ui_integration`：**25 passed, 0 failed**（原 23 + 新 2）。
- 两个新 MainWindow 测试的 QINFO/QWARN（`[RtlSdrSource] no ops bound`、控制 HTTP 回环提示）为离线预期输出，非失败。

**快照门控**（先例 `MBD_MHDECAY`/`MBD_WFPAUSE`）：`tests/ui_screenshot_narrow.cpp` 新增 `MBD_FFTSIZE=1024/2048/4096` 种子块（校验档位，非法值不写入），在 MainWindow 恢复前把 `"rx/fftSize"` 写进 throwaway QSettings——证明真实持久化恢复路径，而非事后 poke。产物：
- `docs/learn/phase63/fftsize-1024-960.png`（960×640）
- `docs/learn/phase63/fftsize-1024-1920.png`（1920×900）

**Read 核查结论**：两图 FFT 工具条均清晰显示 `"FFT [1024 ▼]"`（种子值端到端恢复生效）；工具条整行排布、0 裁切、0 叠字；频谱峰 98.50 MHz 与峰值表为真实测试信号数据；左右栏"未连接/未开启"为诚实空态。

---

## 4. 评估（不落地，留后续轮）

### 4.1 center / span
- **center**：已有主调谐 `freqSpin_` 数值输入 + 频谱带边框拖拽 + Ctrl/滚轮 zoom，交互闭环充分。
- **span**：无独立数值输入/只读显示。技术上 span = `frameFsHz_ / zoomFactor_`（`spectrum_display.cpp:239`），是 zoom 的纯派生；用户通过滚轮/拖拽一格一格改 span，已能到达任意 zoom。
- **价值判断**：补一个"span MHz 只读显示"是低成本、诚实的可读性增强（直接显示当前可视带宽）；但补"span 数值输入框"会引入与 zoom 互推的状态同步问题，SDR++ 主界面也只显示 span 而不提供独立数值输入。**建议后续轮只加只读 span 数值显示框，不加独立输入。** 本轮按指令不落地。

### 4.2 解调器音量
- **squelch**：已存在（`main_window.cpp:692-701`），参数走 tokens，非缺口。
- **音量**：已存在，但藏在 设置对话框 → 音频"主音量"滑杆（`settings_dialog.cpp:85-88`，0-100），持久化并接线到 `audioOutput()->setVolume`（`main_window.cpp:2800`）。**主解调面板上没有快捷音量滑杆。**
- **价值判断**：对照 SDR++，主界面确实有常显音量/AF 滑杆。把设置对话框里的"主音量"提升为解调面板上的常显滑杆是合理的交互增强，但它属于"便利性"而非"缺失能力"（能力已存在且持久化）。本轮不落地；若后续轮做，应复用同一 `"rx/volume"` 键与 `audioOutput()->setVolume`，不要新造第二套音量状态。

---

## 5. 改动文件清单

仅测试与快照门控，未改任何生产源码：
- `cpp/tests/test_spectrum_display.cpp`（+1 测试）
- `cpp/tests/test_ui_integration.cpp`（+1 include、+2 测试）
- `cpp/tests/ui_screenshot_narrow.cpp`（+`MBD_FFTSIZE` 种子块）
- 新增快照：`docs/learn/phase63/fftsize-1024-{960,1920}.png`
- 本文档：`docs/learn/phase63/fft-size-config.md`

未动：生产 UI/引擎源码、`cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*`；未 `git add/commit/push`。

## 6. 诚实未完成项
- 未给 `"rx/fftSize"` 建 `tokens.h` 具名 key（现存 key，非本轮新键；且改名会让老用户持久值失效，故保留原字符串，仅文档说明）。
- span 只读显示框、解调面板常显音量滑杆均为评估结论，未落地（按指令）。
- 真机 RTL-SDR 联调未做（本轮全程 offscreen + 离线 TestSignalSource）。
