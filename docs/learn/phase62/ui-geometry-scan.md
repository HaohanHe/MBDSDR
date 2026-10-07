# Phase62 · UI 几何裸数值全量走查（cpp/src/ui/ 只读续轮）

- 仓库 HEAD：`e0f9242`；全程只读（grep/Read），**零代码改动**，未 git add/commit/push。
- 上一轮（`ui-flexibility-check.md`）已修复左栏裁切并确认 `main_window.cpp` 几何无新增裸数值；
  本轮把覆盖面扩大到 `cpp/src/ui/` **全部 46 个源文件**做全量扫描，不再抽查。
- 判定基准（沿用上轮口径）：
  - **OK 不列**：走 `tokens::scaled()` 或具名 token（`tokens::k*`）；`width()/height()` 派生值；
    颜色 rgba/QColor 字面量；零值布局语义（`setContentsMargins(0,0,0,0)`、`setSpacing(0)`）。
  - **列清单**：未走 scaled/具名 token 的几何裸字面量（固定像素宽/高/边距/间距/字号；
    QSS 内 padding/margin/font-size 字面量）。

## 1. 扫描范围

`cpp/src/ui/` 全部源（.cpp/.h）：

```
about_dialog, activity_log, aircraft_tracker, bookmark_manager,
calibration_dialog, constellation_view, data_text_panel, elevation_plot,
gain_control_model, m17_panel, main_window(+h), map_projection,
pocsag_panel, radio_panel, recording_library, settings_dialog,
shortcuts_catalog, shortcuts_dialog, sky_view, s_meter,
spacetime_format, spectrum_display(+h), spectrum_render.h,
spectrum_tune.h, spectrum_widget(+h), status_format.h, task_steps_view,
vor_panel, weather_panel, world_view(+h), coastline_data.h
```

扫描的几何 API 族（逐族 grep + 人工复核上下文）：

| 族 | 模式 |
|---|---|
| 尺寸钉死 | `setFixedWidth/Height/Size`、`setMinimum/Maximum(Width/Height/Size)` |
| 边距/间距 | `setContentsMargins`、`setSpacing`、`addSpacing`、`addStretch`、`QMargins` |
| 尺寸对象 | `QSize(`、`QRect(`、`QPoint(`、`QPointF(`、`resize(` |
| 表格/标签栏 | `setColumnWidth`、`setRowHeight`、`setMinimumSectionSize`、`setDefaultSectionSize`、`setIconSize`、`setGridSize` |
| 字号 | `setPixelSize`、`setPointSize(F)`、`QFont(...)` |
| QSS 字面量 | 全部 `setStyleSheet` 调用逐处人工复核 padding/margin/font-size/border-radius |
| 绘制 | `drawText/drawLine/drawEllipse/drawRoundedRect` 坐标与 `QPen(..., 宽)` |

## 2. 命中清单（裸字面量，需 token 化项）

### 2.1 未走 scaled/具名 token 的几何裸字面量

**无。** 逐族扫描结果：

- `setFixedWidth/Height/Size(<数字>)`：**0 命中**。
- `setMinimumWidth(<数字>)`：4 处全部是 `setMinimumWidth(0)`（提示标签允许收缩，
  `pocsag_panel.cpp:50`、`m17_panel.cpp:70`、`data_text_panel.cpp:51`、`vor_panel.cpp:44`）
  —— 属布局语义（允许 elide），不动。
- `setMaximumWidth(<数字>)`：仅 `main_window.cpp:3667-3668` 折叠动画 `setMaximumWidth(0)`
  —— 布局语义（收起侧栏），不动。
- `setContentsMargins(<非零>)`：**0 处裸值**；所有非零边距均为
  `tokens::scaled(tokens::kSpacing*)` / `scaled(kPanelPad*)` / `scaled(kSpectrumPad)`。
  `setContentsMargins(0,0,0,0)` 共 20 处（面板卡内行布局清零），属布局语义，不动。
- `setSpacing(<非零>)` / `addSpacing(<非零>)`：全部走 `scaled(kSpacing*)`。
- `QSize(<裸数>)`：仅 `main_window.cpp:3370 setIconSize(QSize(0,0))`（无图标语义）与
  `:3379 setSizeHint(QSize(0, rowH))`（rowH=`scaled(kVfoRowH)`）—— 均非几何裸值。
- 表格列宽/行高：`setColumnWidth`/`setRowHeight` **0 命中**；
  `setMinimumSectionSize(scaled(36))` 3 处（pocsag/m17/data_text）已走 scaled。
- 字号：全部 `p.font()` 继承或 `setPointSize(F)(tokens::kFont*Pt)`；无裸 pt/px 字号。
- QSS `setStyleSheet`：全部 30+ 处逐处复核 —— 颜色-only 或 padding/margin/radius 全部由
  `tokens::kPadBanner`、`tokens::kRadiusSmall`、`tokens::kSpacing*` 代入（生成时 `S(px)` 缩放）。
  无裸 `padding:Npx`/`font-size:Npt` 字面量。

### 2.2 观察类（**非违规**，沿用上轮"scaled() 即 OK"口径不列清单；仅记录供下一轮参考）

`tokens::scaled(<行内裸数>)` 一族 DPI 弹性正常、但数字未提升为具名 token。集中在：

| 位置 | 行内 scaled 数 | 语义 |
|---|---|---|
| `main_window.cpp:163` | `scaled(1280), scaled(800)` | 初始窗口尺寸 |
| `main_window.cpp:1919` | `scaled(280), scaled(800), scaled(280)` | 中部栏内 splitter 初始份额 |
| `main_window.cpp:4481` | `scaled(260)` | RDS 长文本 elide 宽 |
| `s_meter.cpp:47` | `scaled(220)` | S-meter 首选宽 |
| `pocsag/m17/data_text_panel` ×3 | `scaled(36)` | 表头最小列宽（同一值重复 3 次） |
| 仪器画布微几何 | `scaled(2/3/4/5/6/7/12/14/20/22/26/44/60/64/90/110/140/160)` 等 | world_view / sky_view / vor_panel / elevation_plot / spectrum_display 内的标签内缩、圆点半径、引导线 |
| 画笔宽度 | `QPen(..., 1.2)`（spectrum_display.cpp:910 频带中心刻线）、`QPen(..., 2.0)`（vor_panel.cpp:155 方位指针）、其余 1.0 发丝线 | 描边语义，非 DPI 几何 |

> 判定：这一族**符合本轮判定基准**（已走 scaled()，OK 不列）。
> tokens.h 设计哲学是"业务代码只引用具名常量"，若下一轮要做收尾，优先把
> `1280×800` 初始窗、`280/800/280` splitter 份额、`scaled(36)`×3、`scaled(220)` S-meter 宽
> 提升为具名 token；仪器画布微几何数量大且多为一次性调参点，建议不动。

## 3. 关键面板抽查观察（只描述，不改）

### 3.1 频谱工具条（`spectrum_widget.cpp:112-354`）

- Row1 用自定义 `FlowLayout`，横竖间距均为 `scaled(kSpacingS)=4`；窄窗自动折行第二排，
  不压垮 combo（Phase42 窄窗修复路径仍在）。
- combo 全部 `AdjustToContents` + tooltip 回显全值，无固定宽度字面量。
- Row2 瀑布控制排间距 `scaled(kSpacingS)`、分隔 `addSpacing(scaled(kSpacingM)=8)`。
- 峰值表 `setFixedHeight(scaled(kPeakTableH=96))`，列 ResizeToContents + 末列拉伸。
- **观察：间距落在 4pt 栅格（S/M），与 Figma 弹性语言一致；无新增裸数。**

### 3.2 瀑布 / 统一画布（`spectrum_display.cpp` + `tokens.h:kDisp*`）

- 频谱曲线、共享频率条、瀑布左右起线与宽度由 `kDispLeftInset=50 / kDispRightInset=12`
  统一锚定，1px 发丝间隔 `kDispAreaGap=1`，可拖分割线命中半高 `kDividerHitHalfH=4`。
- 最小高 `kSpecAreaMinH=120 / kWfAreaMinH=60`，曲线/瀑布高度比 `kDefaultSpecFraction=0.5`。
- 内部标签微偏移（`scaled(2/4/12/18)` 等）属观察类（见 2.2），DPI 弹性正常。
- **观察：轴线对齐是构造级保证（同一 left inset），层级（曲线→1px→频率条→分割线→瀑布）
  与 Figma 信息层级一致。**

### 3.3 数据表格（`data_text_panel.cpp`，另 pocsag/m17 同构）

- 面板边距 `m=scaled(kSpacingM)=8`、行距 `scaled(kSpacingS)=4`，表头 `monoInfo` 样式。
- 四列全部 `Stretch`（窄栏正文列自然 elide），表头最小列宽 `scaled(36)`。
- "清空"按钮 `setMinimumHeight(scaled(kTouchMinDim=44))`，文字-only 按钮命中区达标。
- **观察：边距/行距在 4pt 栅格上，触控目标 ≥44 逻辑 px，与弹性语言一致。**

### 3.4 状态栏（`main_window.cpp:1933-1962`）

- 全部读数 label 统一 `objectName("dockHint")`（QSS 内 aux pt 字体、次要色），
  无任何内边距/字号字面量；S-meter 为 permanent widget，高 `kSMeterH=34`。
- 状态染色（扫描/录音）只用 `color + font-weight`（`kInteract/kDanger/kWeightSemi`）。
- **观察：状态栏是纯文本流 + 一个仪表，层级安静（次要色），符合"状态栏是安静信息"的 Figma 原则。**

### 3.5 右标签栏（`main_window.cpp:925-932`）

- `setUsesScrollButtons(true)` + `ElideRight` + `sizePolicy(Ignored, Preferred)`
  （上轮 phase62 修复点，本轮复核仍在位）。
- Tab 内边距来自 QSS 生成器 `%padMV%/%padLH% = scaled(kSpacingM/L)`，
  tab 命中高 `%touch% = scaled(kTouchMin)`。
- 分组头 tab 走 `QTabBar::tab:disabled`（QSS 内次要色/500 字重），无几何字面量。
- **观察：窄窗下标签条滚动收进 ~198px、全屏 420px 全可见，弹性行为与上轮快照一致。**

## 4. 结论

- **需 token 化项列表：无新增。** `cpp/src/ui/` 全量扫描未发现未走
  `tokens::scaled()`/具名 token 的几何裸字面量；上轮修复后左栏/右栏路径保持干净。
- 观察类（scaled() 行内裸数族，见 2.2）不构成本轮违规；若下一轮做收尾，建议优先提升
  `初始窗 1280×800`、`中部 splitter 280/800/280`、`表头 minSection 36`（×3 重复）、
  `S-meter 宽 220` 这 4 组为具名 token。
- 硬约束自查：零代码改动（仅新增本文件）；活动参数零硬编码；无预置 TLE/呼号；
  全文无竞赛类字样；措辞中立。
