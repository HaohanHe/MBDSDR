# Phase63 · 调谐历史（最近频率快速回跳）+ 频率单位格式化收口

- 仓库 HEAD：`8dfcaa1`；全程 offscreen（`QT_QPA_PLATFORM=offscreen`，
  `LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），未操控 GUI，未 git add/commit/push。
- 数据源：只接真实回读；无历史时如实显示"无调谐记录"；零 mock。
- 活动参数零硬编码；红线扫描（`competition/比赛/ghp_`）CLEAN；GPL 措辞中立，项目 MIT。

## 0. 机制来源（只学机制，不抄 GPL）

参考机制：主流 SDR 软件（SDR++ 参考源码仅用于理解交互范式）在主频率显示旁提供
"recently tuned / quick-recall"——把用户真正调谐过的中心频率按"最近在前"列成短表，
点选即回跳，并跨会话持久化。本机制落地时只取"最近在前短表 + 回跳 + 持久化"的交互范式，
数据通道用本仓自己已有的 **~1 Hz 硬件回读 telemetry**，未复制任何 GPL 源码或布局。

关键设计决策（诚实优先）：
- **数据通道选 `onSourceTelemetry` 的 `centerHz` 回读**，而不是拦截 9 处 `engine_->onSetCenterFreq`
  调用点（`main_window.cpp` 共 9 处，见 §2）。回读是唯一"非侵入、且确认调谐已真正落地"的汇聚点；
  spinbox 直改、频谱拖拽、书签套用、峰值回跳、扫描器驻留都流经它。
- **去重 + 上限**：`TuneHistory::maybePush` 对新值与最新项做 `kTuneDedupHz=1.0 Hz` 阈值去重，
  并在全表内把"复访旧频率"移到最前（不产生重复行），上限 `kTuneHistoryMax=12`。
- **空态诚实**：无持久化历史且尚未收到回读时，combo 单项"无调谐记录"且 disabled，不预置任何演示频率。

## 1. 三候选判定清单

| 候选 | 判定 | 证据（file:line） |
|---|---|---|
| 1. RX 面板 VFO 级汇总行（采样率/带宽/解码状态） | **非缺口** | 接收参数组 `gRx` 已含 采样率 `srCombo_`(`main_window.cpp:548`)、增益、解调 `demodCombo_`(:574)、带宽 `bwCombo_`(:588)、抽取、声道徽章 `channelBadge_`(:608 区)；常驻状态条 `sbMode_/sbSr_/sbVfo_/sbGain_/sbRssi_/sbSnr_/sbSquelch_/sbGnss_/sbAudio_`(`main_window.cpp:2127-2152`) 由 `onSourceTelemetry`(:4697) 喂真实回读。初判"grep 无命中"是因为汇总以"表单组 + 常驻状态条"形态存在，而非字面"VFO 汇总行"。 |
| 2. 频谱水印/频率标签格式化收口（kHz/MHz 自动单位与精度） | **部分缺口 → 本轮收口** | 现状散落：`main_window.cpp:582-584`（带宽 combo 内联 3 分支）、`spectrum_display.cpp:937-938`（Δ 频率 2 分支，kHz `f,2`、缺 Hz 分支，与带宽 combo 精度不一致）、`status_format.h:20/26`（MS/s f,3、VFO MHz f,3）、`spectrum_widget.cpp:485/518-521`（峰值表/覆盖层，刻意精度）。本轮抽出具名 `formatFrequencyAutoHz` 并接入两处直接同义点；频谱轴/Δ/峰值表属刻意精度，保留并记录。 |
| 3. 调谐历史/最近频率快速回跳 | **真缺口 → 本轮端到端落地** | 初判零命中（全仓 grep `tune history/recent freq` 仅 `main_window.cpp:3046` 一句无关注释）。本轮新增 header-only `TuneHistory` + 频率组"最近" combo + QSettings 持久化。 |

## 2. 现状核实（落地前）

- 中心频率唯一真实汇聚点：`onSourceTelemetry(name, connected, centerHz, sampleRateHz, gainDb)`
  回读 `centerHz`（`main_window.cpp:4697-4722`）。
- `engine_->onSetCenterFreq` 共 9 处调用点：`:410,:1489,:1647,:2248,:2300,:2531,:2909,:3438,:4432`。
  不改这 9 处，避免触碰他会话在途改动；统一在回读点记录。
- 持久化范式：`saveUiState()`(`:4009`) 写 QSettings，`scheduleSave()`(`:4118`) 500 ms 防抖；
  恢复走 `restoreUiState()`，键 `rx/centerFreq` 等。本轮沿用同一范式。
- token 范式：几何/颜色/字号全部 `tokens::scaled()` + 具名 token；新增值进 `tokens.h`，不裸写。

## 3. 落地 file:line 清单

**新增 token（`cpp/src/core/tokens.h`）**
- `kTuneHistoryMax = 12`、`kTuneDedupHz = 1.0`（`:670-671`）
- `kSettingsKeyTuneHistory = "rx/tuneHistoryHz"`（`:755`）

**具名格式化器（`cpp/src/ui/status_format.h`）**
- `formatFrequencyAutoHz(double hz)`（`:35`）：MHz/kHz/Hz 自动单位，MHz/kHz 用默认（去尾零）精度、Hz 用 `f,0`；`hz<=0 → "--"`。与被它替换的带宽 combo 标签**逐字节一致**。

**新增纯逻辑头（`cpp/src/ui/tune_history.h`）**
- `ui::TuneHistory`：`maybePush` 去重/移前/封顶、`entries/isEmpty/size/labelAt`、`toVariantList/fromVariantList`。
  header-only、无 Qt 控件态，直接单测（同 `status_format.h` 范式）。

**MainWindow 接线（`cpp/src/ui/main_window.{h,cpp}`）**
- `main_window.h:15` include；`:380` `tuneHistCombo_`、`:382` `tuneHistory_`、`:383` `refreshTuneHistoryCombo()`。
- 频率组"最近" combo：`main_window.cpp:543`（objectName `tuneHistCombo`，宽走 `scaled(kComboMinW)`）。
- 带宽 combo 收口：`main_window.cpp:592` 改用 `ui::formatFrequencyAutoHz`（删除内联 3 分支）。
- 激活回跳：`main_window.cpp:2260-2272`（`activated` → 读 itemData Hz → 走正常 `freqSpin_->setValue` 调谐路径）；同处初始化 `refreshTuneHistoryCombo()` 出诚实空态。
- 回读记录：`main_window.cpp:4745-4748`（`maybePush(centerHz)` 命中才重绘 + `scheduleSave()`）。
- 持久化：写 `main_window.cpp:4017`；读 `main_window.cpp:4313-4315`。
- `refreshTuneHistoryCombo()` 实现：`main_window.cpp:4127`（空表 → 单项"无调谐记录"且 disabled；填表时 blockSignals，避免重绘触发 `activated`）。

## 4. 测试真实计数

- `test_status_format`（扩既有目标，未新增 CMake 目标）：**12 passed, 0 failed**
  （原 8 + 新增 `freqAuto`/`tuneHistoryDedup`/`tuneHistoryCap`/`tuneHistoryRoundtrip` 4 项）。
- `test_ui_integration`（扩既有目标）：整套件 **19 passed, 0 failed**
  （新增 `tuneHistoryEmptyStateLoadsAndJumps`：断言空态禁用、持久化 2 行加载 most-recent-first、`activated(0)` 把 spinbox 从 98.5 回跳到 100.0 MHz）。
- 构建：`cmake --build build -j4` 增量，exit 0；`ui_shot_narrow` 重建 exit 0。

## 5. 快照核查（env 门控，复用 `ui_screenshot_narrow.cpp`）

新增门控 `MBD_TUNEHIST`（`ui_screenshot_narrow.cpp:35-52`，构造 MainWindow 前向 throwaway QSettings 播种）：
- `MBD_TUNEHIST=1` → 播种 `{100,98.5,137,121.5} MHz`；`MBD_TUNEHIST=empty` → 删键出空态。
- 用既有 `MBD_SCROLL=tuneHistCombo` 把"最近"行滚入左栏视口。

| 图 | 结论 |
|---|---|
| `tunehist-1920.png`（1920×900） | 频率组三行完整：中心频率 98.500 / 步进 10 kHz / **最近 100 MHz**；0 裁切 0 叠字。 |
| `tunehist-960.png`（960×720） | 同上完整；0 裁切 0 叠字。 |
| `tunehist-640.png`（窗口被抬到 764，子地板） | 左栏约 100 px 宽，combo 数值横向裁切为**既有窄窗行为**（`freqSpin/步进` 同样裁切）；标签"中心频率/步进/最近"齐全、无叠字。640 被 `kMainMinW=960` 钳制属既有行为。 |
| `tunehist-empty-1920.png` | 最近行显示灰色 disabled "无调谐记录"，诚实空态。 |

（PNG 被 .gitignore 部分忽略，仅落盘 `docs/learn/phase63/`。）

## 6. 红线与纪律自查

- 改动文件：`tokens.h`、`status_format.h`、`tune_history.h`(新)、`main_window.{h,cpp}`、
  `test_status_format.cpp`、`test_ui_integration.cpp`、`ui_screenshot_narrow.cpp`。
- 红线扫描 `competition|比赛|ghp_`：**CLEAN（0 命中）**。
- 未触碰 `cpp/tests/ui_diag_freeze.cpp`（隔离文件）；未改他会话在途的 `mobile/*.dart`、
  `docs/.../mobile-desktop-tool-diff.md`；未 git add/commit/push。

## 7. 诚实未完成项

- 频谱 Δ 频率格式化 `spectrum_display.cpp:937-938`（kHz `f,2`、缺 Hz 分支）与带宽 combo 精度仍有
  差异，本轮**未**改——它是"游标 Δ 偏移"专用精度，改它会动游标读数显示，超出本次收口范围，留待后续。
- "最近" combo 目前是下拉选择式；未做键盘快捷键回跳（如 Alt+下拉出），可后续在 shortcuts_catalog 增补。
- 真机 RTL-SDR 回读下的历史累积未在硬件上验证（offscreen 环境）；逻辑已由回读通道单测覆盖。
