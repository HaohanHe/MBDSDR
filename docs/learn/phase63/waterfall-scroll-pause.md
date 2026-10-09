# 瀑布滚动暂停/恢复（waterfall scroll pause）

HEAD=ae0effb 之后落地。三候选侦察判定后，仅候选 1 为真缺口并落地；候选 2、3 经证据核实为非缺口。

## 机制

瀑布滚动的既有链路：`setSpectrum(frame)` 每帧累加 `frameMod_`，到 `everyNthFrame_`
（1x/2x/4x，spectrum_widget.cpp `scrollCombo_`）就 `pushHistoryRow()` 把当前帧
`frame_.dbfs` 写入环形缓冲 `ringDb_`，`materialiseHistory()` 把环物化为
`history_`（行 0=最新），paintEvent 再把 `history_` 按可见频率窗裁剪绘制进瀑布区。

此前没有任何"暂停滚动/恢复"开关：滚动只能靠调慢 `scrollCombo_` 或停掉数据源。
SDR++ 瀑布有 scroll pause，本项目缺这个显式开关（真缺口）。

### 落地语义（诚实选择）

- **只冻结瀑布显示，不冻结测量**：暂停时 paintEvent 改画一张暂停瞬间采下的彩色快照
  `pausedHistory_`；频谱 trace 仍按实时帧继续更新（选择"显示冻结仅瀑布"），
  max/min-hold、余晖包络照常由真实帧推进。
- **真实数据不丢**：暂停期间 `setSpectrum` 照常把新帧推进 live 环 `ringDb_`，
  `ringCount_` 继续增长——没有假数据、没有"补帧"。恢复时丢弃快照，直接从 live 环
  继续画：暂停窗口的所有帧都在环里，时间轴诚实可回看。
- **时间轴刻度**：暂停期间刻度按快照行数 `pausedRingCount_` 计算（否则 live 刻度
  会在静止画面上往下走）；秒/行基准 `effectiveSecondsPerRow()` 仍用真实帧间隔
  EWMA，不停表。
- **颜色/量程**：暂停中改 palette / dB range / 关 auto-range / 换 colormap 时，
  快照随 `materialiseHistory()` 刷新——冻结图片的颜色刻度跟随用户选择，但行内容
  仍冻结。深度重建（`allocateRing`）同样刷新快照。

## 现状核实（落地前证据）

- 瀑布行推进门控：`spectrum_display.cpp:541-545`（`++frameMod_` → `pushHistoryRow()`）。
- 环与物化：`pushHistoryRow()` `spectrum_display.cpp:328`、
  `materialiseHistory()` `:337`。
- paintEvent 瀑布绘制：`spectrum_display.cpp:1154` 起；时间轴刻度 `computeTimeTicks()`
  `:412` 起。
- 工具条先例：`maxHoldChk`（spectrum_widget.cpp:165-169）、autoDbBtn 可检查按钮
  accent 高亮先例（spectrum_widget.cpp:316-354）、`wfRow` 瀑布行
  （spectrum_widget.cpp:419 起：瀑布/scrollCombo/paletteCombo/depthCombo）。
- 持久化先例：ctor 自读 `loadRequestedRingDepth/MaxHoldDecay/DbGridStep`
  （spectrum_display.cpp:84/109/132），QSettings key 集中在 tokens.h。

## 三候选判定表

| 候选 | 判定 | 证据 |
|---|---|---|
| 1. 瀑布滚动暂停/恢复 | **真缺口，已落地** | 落地前 grep `暂停/pause` 在 spectrum_widget/spectrum_display 无命中；现有"暂停"按钮均为扫描暂停（main_window.cpp:1464 `scanPauseBtn_`）与离线回放暂停（main_window.cpp:1840 `offAnaPauseBtn_`），与瀑布滚动无关 |
| 2. 频谱 trace 显示模式组合 | 非缺口 | `avgCombo` Off/Slow/Fast（spectrum_widget.cpp:155-163）→ `averageModeRequested` → main_window.cpp:2306 接引擎；`maxHoldChk`（:165）、`minHoldChk`（:218）为独立 QCheckBox、各自独立 toggled 连接，自由组合已成立 |
| 3. VFO 微调步进配置 | 非缺口 | `stepCombo_` 7 档 1Hz..1MHz（main_window.cpp:547-552）；`applyStep` 写 `currentStepHz_`+spinbox singleStep+`setStepHz`（main_window.cpp:2331-2338）；方向键 nudge `currentStepHz_`（:2998-3007）、PgUp/PgDn 循环步进（:3089）；`scheduleSave()` 持久化（:2343） |

## 落地 file:line

- tokens.h:640 新增 `kSettingsKeyWfScrollPaused = "view/wfScrollPaused"`（默认关，
  缺省/非 bool 诚实回落 off）。
- spectrum_display.h:192-193 `setScrollPaused(bool)` slot + `scrollPaused()`；
  :301-302 测试 seam `scrollPausedForTest()` / `pausedHistoryForTest()`；
  :538-540 私有态 `scrollPaused_ / pausedHistory_ / pausedRingCount_`。
- spectrum_display.cpp:146-163 `loadRequestedScrollPaused()`（仅 bool/"true"/"false"
  字符串诚实受理，其余回落 off）；:179 ctor 自读。
- spectrum_display.cpp:763-774 `setScrollPaused()`：开=采 `history_.copy()`+行数快照；
  关=丢快照。
- spectrum_display.cpp:423 时间轴行数用 `pausedRingCount_`（暂停时）。
- spectrum_display.cpp:1181/1191 paintEvent 源图切换 + 暂停时只走 drawImage 路径
  （快照是烤好的像素，不再走 live 环 block-max）。
- 暂停时颜色刻度跟随：allocateRing :328-331、setDbRange :611、setAutoRangeOn :633、
  setPalette :796、loadColormapFromJson :811。
- spectrum_widget.cpp:490-517 wfRow 内嵌可检查 QToolButton "暂停滚动"（objectName
  `wfPauseBtn`），checkable+accent 高亮镜像 autoDbBtn；toggle 写 QSettings + 下发
  canvas；ctor 先 setChecked（读持久值）后 connect，canvas ctor 亦自读。

## 测试（扩既有 test_spectrum_display，未新增 CMake 目标）

- `scrollPauseDefaultsOff`：无 key 时默认 off。
- `scrollPauseFreezesDisplayButKeepsBuffering`：停在 bin128 亮峰→暂停→再推 15 帧
  bin200 亮峰：环行数 30→45（数据仍入缓冲）；快照 row0 bin200 不变（显示冻结）；
  live history() row0 bin200 已变亮（缓冲继续记录）。
- `scrollPauseResumeContinuesWithoutReset`：恢复后快照置空、环行数不重置、继续推帧
  从 30→35；重复 toggle 幂等。
- `scrollPausePersistsRoundTrip`：true/false/垃圾串 → ctor 自读往返，垃圾诚实 off。

offscreen 真实计数：`spectrum_display` 40 passed / 0 failed；连带
`spectrum_*` 9 个 ctest 目标全过（interaction/autorange/maxhold/minhold/peaktbl/
tune/render）。

## 快照（ui_shot_narrow 门控 MBD_WFPAUSE=1）

- `wf-pause-960.png`（960x640）、`wf-pause-1920.png`（1920x900）：暂停滚动按钮可见、
  accent 高亮（checked 持久态），0 裁切 0 叠字。瀑布区黑屏为无信号源的诚实空态
  （本 harness 不接硬件），按钮位于"瀑布"行 256 行 之后。

## 诚实未完成项

- 暂停期间瀑布颜色刻度会跟随 palette/dB-range 选择刷新快照（行内容仍冻结）——
  有意为之，已注明；若用户希望连颜色也锁死在暂停瞬间，需另议。
- 竖直方向"回看历史时间轴"拖拽（滚回去看暂停窗口）不在本次范围：现有瀑布本就是
  环形窗，时间轴刻度已诚实，竖向前翻未做。
