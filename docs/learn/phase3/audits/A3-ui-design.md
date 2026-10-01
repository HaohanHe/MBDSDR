# A3 UI 设计语言审计 + 触屏/窄窗/多窗口适配复查（只读）

> 范围：phase3 §3 红线（A3，只读、不写代码、不动 git、一切走 token、禁魔法像素）。
> 证据基线：Figma 解压预览 4 张 jpg（`cpp/scratch/figma_unzip/preview/`）；桌面 `cpp/src/ui/`（A1 已拥有 `spectrum_widget.{h,cpp}`/`spectrum_display.{h,cpp}`，本报告不重复审渲染器内部几何，仅取其设计语言/适配相关行）；Flutter `mobile/lib/pages/` + `mobile/lib/widgets/` + `mobile/lib/app/tokens.dart`。
> 拿不准标「推断」；原文未给标「原文未给」。所有 file:line 均已实地核读。

---

## 0. Figma 4 张预览图的判读（页面身份 + 设计语言）

| 文件（preview/） | 判读为 | 关键画面 |
|---|---|---|
| `24c86848-….jpg`（69 KB，全彩） | **车机主屏三栏实景** | 顶部状态栏（20:36 / 468km 预估 / P R N D / 消息·wifi·手机图标）；三张圆角卡：左=车辆俯视渲染、中=深色地图导航（白色粗路线 + 车标箭头）、右=音乐卡（方形专辑图 "STARBOY"、标题 Starboy/The Weeknd、进度条、星形/上一首/播放/下一首/随机一排图标键）；底部常驻 dock（主页 / 23.5° / 4 个应用方块 / now-playing 迷你卡 / 播放键 / 音量喇叭） |
| `3623285a-….jpg`（38 KB，灰度线框） | **控制面板线框（旋钮矩阵）** | 左深色栏（下拉 + 大方块显示 + 小按钮 + 细分隔线 + 一大两小三个圆）；右侧顶部 4 个 tab 块 → 一条**宽滑杆** → 3 个小方块 → **3 行 × 5 列大圆形旋钮**（每圆下方一个小标签块） |
| `740d16bb-….jpg`（98 KB，全彩地图） | **全屏导航页** | 整张深色地图铺满，左上搜索条 + 一张信息卡（内含图标行：大图标 + 小图标 + 4 个方块），左下两张方卡；同一顶部状态栏 + 底部 dock |
| `edce26a6-….jpg`（35 KB，灰度线框） | **数据/状态列表页** | 左列 7 行「小方块图标 + 文字行」；中部堆叠卡片列表带进度条（**两条进度条中各有一段浅蓝灰高亮**——这是全套稿唯一的彩色）；**右侧一个五/六边形雷达图**（径向多边形，对应 SDR 信号/卫星极坐标） |

### 0.1 从图上提取的设计语言要素（逐项）

| 要素 | 图上特征（证据） |
|---|---|
| **主背景** | 近黑（约 #0a0a0d 级），纯深色模式，4 张图无任何浅色界面 |
| **卡片/面** | 比背景亮一档的**深灰实心卡**（24c86848 三张卡），圆角大（1900px 幅宽下目测圆角约 20–28px），卡间距宽（gutter 约 24–32px 节奏），卡内留足 padding |
| **唯一彩色** | edce26a6 进度条高亮段 = **低饱和蓝灰（≈#919cac）**；其余全部灰度（白字 / 灰字 / 深灰面），无品牌亮蓝、无高饱和强调 |
| **文字** | 主文字纯白/近白，次要文字中灰；字号层级清晰（时间/标题大、标签小），无纯黑 |
| **控件形态** | 3623285a：**大圆形旋钮**（直径约为行高 1.5–2 倍，触控友好）、宽滑杆、图标-only 传输键、圆角；底部 dock 图标为圆角方块 |
| **整体气质** | 极度克制、扁平、几乎无投影/无纹理/无渐变；高对比但低饱和；Apple/小米车机式「去装饰」信息密度 |
| **栅格节奏** | 横向三等分（24c86848）/ 旋钮 5 列等距（3623285a），纵向卡块间距一致；推断为 4pt 倍数栅格 |

**与 #919cac 的对照结论**：Figma 把「彩色」极度收敛——整屏只有进度条高亮这一处低饱和蓝灰。当前两端却把 **亮蓝 #7CC4FF** 当成主交互色（Tab 选中字、状态栏指示、专注按钮激活、分隔条把手、Tooltip 边框、画布轨迹线）。这是本审计最大的设计语言偏差（详见收敛清单 P0-1）。

---

## 1. 设计语言提取表（Figma 特征 → 两端现状 file:line → 差距）

| 元素 | Figma 图上特征 | 桌面端现状（file:line） | 移动端现状（file:line） | 差距 |
|---|---|---|---|---|
| 主背景 | 近黑 | `tokens.h:40 kBgMain="#080a0c"`、`:41 kBgBar="#000000"` | `tokens.dart:16 bgMain=0xFF080A0C`、`:17 bgBar=0xFF000000` | ✅ 基本一致 |
| 卡片面 | 深灰实心卡、大圆角、宽 gutter | `tokens.h:45-47 kCard1/2/3=白叠层4.5/7.5/10%`、`:49 kCardEdge`；QSS `panelCard` 圆角 `kRadiusCard=10`（`tokens.h:137`） | `tokens.dart:21-24 card1/2/3`、`:45 radiusCard=10`、`theme.dart:45-53 CardTheme` | ⚠️ 实现为「白叠层」而非 Figma「深灰实心」（视觉等价，可接受）；**卡圆角 10 明显小于 Figma 目测 20–28**，大面板观感偏「网页卡」而非「车机卡」 |
| 主强调色 | 唯一彩色=低饱和蓝灰 #919cac | `tokens.h:82 kAccent="#7CC4FF"`（亮蓝）；`kSelectedFill=rgba(145,156,172,0.16)`（`:97`）才是 #919cac | `tokens.dart:36 accent=0xFF7CC4FF`（亮蓝）；`selectedFill=0x24919CAC`（`:79`） | ❌ **主交互色用了亮蓝而非低饱和蓝灰**（P0-1） |
| 选中态填充 | 低饱和蓝灰块 | `tokens.h:97 kSelectedFill` 0.16 alpha；QSS `QTabBar::tab:selected` 却用 `kAccent` 字色（`tokens.h:645`） | `tokens.dart:79 selectedFill=0x24`（=0.141 alpha） | ⚠️ 填充本身正确，但 **Tab 选中字仍亮蓝**；且 **alpha 两端漂移 0.16 vs 0.141**（P1-3） |
| 文字层级 | 大标题/中正文/小标签 | `tokens.h:537-541` title14pt/body11pt/aux9.5pt/display22pt | `tokens.dart:224-271` appTitle17/section14/body13.5/aux12/freqReadout26 | ✅ 有层级、有 token；⚠️ 两端字号基线不同（pt vs logical px），跨端观感「谁大谁小」未对齐（P2） |
| 字重 | Figma 只用常规/中 | `tokens.h:546-548` 400/500/600 | `tokens.dart:65-71` w400/w500/w600 | ✅ 一致（均禁 700+） |
| 圆角节奏 | 大卡 20–28、按钮圆 | `tokens.h:134-155` panel24/card10/small4/pill999 | `tokens.dart:44-50` panel24/card10/small4/pill999 | ⚠️ card=10 偏小（同卡片差距）；panel=24 与 Figma 大卡接近 ✓ |
| 控件形态 | 大圆形旋钮/宽滑杆/图标键 | 桌面用 QComboBox/QSpinBox/QSlider（常规 Qt 控件），无圆形旋钮；`tokens.h:153 kRadiusCircle="50%"` 仅 token | `spectrum_page.dart:508 SegmentedButton`、`:592 Slider`、`:610 IconButton`；无大圆形旋钮 | ⚠️ Figma 的「大圆形触控旋钮」形态两端都**未实现**（车机旋钮美学缺失）；现状为常规条/滑块（推断：可作为 P2 增强，非致命） |
| 栅格/间距 | 4pt、gutter 24–32 | `tokens.h:162-166` S4/M8/L16/XL24/XXL32 | `tokens.dart:53-61` 同 4/8/16/24/32 | ✅ 一致 |
| 触控尺寸 | 车机大触控 | `tokens.h:173 kTouchMin=44`、`:514 kControlH=26`（视觉密度） | `tokens.dart:91 touchMin=44` | ⚠️ token 有，但**落实有缺口**（见适配复查） |
| 多窗口/常驻 dock | 顶部状态栏+底部 dock 常驻 | 桌面为单 QMainWindow + 顶部 topBar（`main_window.cpp:156`）+ QStatusBar | 移动为 AppBar + BottomNav/Rail | ✅ 概念对应 |

---

## 2. 收敛清单（按优先级；每项：现状 file:line → 目标 → 改法建议 → 建议 widget 测试）

### P0（与 Figma 根本偏差 / 适配硬伤）

**P0-1 主交互色过饱和：亮蓝 #7CC4FF 应收敛为低饱和蓝灰体系**
- 现状（亮蓝被当主交互色）：
  - 定义：`cpp/src/core/tokens.h:82 kAccent="#7CC4FF"`；`mobile/lib/app/tokens.dart:36 accent=0xFF7CC4FF`
  - 使用点（桌面）：QSS `QTabBar::tab:selected{color:accent;font-weight:600}`（`tokens.h:645`）；状态栏 `sbWatch_/sbScan_` 用 accent+600（`main_window.cpp:1673,1675`）；专注按钮激活态（`main_window.cpp:3136-3137`）；分隔条把手 `splitterHandleRgba()=rgba(124,196,255,0.35)`（`tokens.h:85`）；Tooltip 边框 accent（QSS `tokens.h:735`）；画布轨迹线 `constellation_view.cpp:200,262`、`elevation_plot.cpp:142,175`；VFO 默认色 fallback 字面 `"#7CC4FF"`（`main_window.cpp:3437`）。
- 目标：交互高亮（Tab 选中字、激活态、把手、Tooltip 边）收敛到 **#919cac 低饱和蓝灰**及其明度阶（hover 提亮 / press 压暗，均仍低饱和）；亮蓝 **仅保留为数据轨迹/仪器画布的专用色**（它在示波器语境下有信号语义，可留，但退出 UI 装饰角色）。
- 改法建议：在 tokens 侧新增「交互蓝灰」三态（base #919cac / hover / press），把 QSS 与各使用点从 `kAccent` 切到新 token；亮蓝改名/复用为 `kTraceColor` 类仪器语义 token；`main_window.cpp:3437` 字面 `"#7CC4FF"` 改为引用 token（禁字面）。
- 建议测试：widget 测试截图比对——Tab 选中态、专注激活态、分隔条把手应呈低饱和蓝灰而非亮天蓝；并断言 UI 装饰色不再出现 `#7CC4FF`（仅画布轨迹例外）。

**P0-2 桌面主窗口无最小尺寸，窄窗可被拖到内容溢出**
- 现状：`main_window.cpp:144 resize(tokens::scaled(1280), tokens::scaled(800))` 之后**全文无 setMinimumSize/setMinimumWidth**（已 grep 核实）。三栏 `QSplitter`（`main_window.cpp:202`，`setChildrenCollapsible(false)` `:204`）按 1:3:1 / 280:800:280（`:1654-1657`）分配；中心频谱有 `kSpectrumMinW=480/H=320`（`tokens.h:462-463`），但**主窗口本身没有下限**。
- 目标：参照 Figma 车机「弹性但有底线」，给主窗口设一个 token 化的最小尺寸（建议 kMainMinW≈960、kMainMinH≈600，4pt 栅格取整），保证三栏在极端窄窗下仍可读、不水平溢出。
- 改法建议：在 tokens.h 新增 `kMainMinW/kMainMinH`，构造函数 `setMinimumSize(tokens::scaled(kMainMinW), tokens::scaled(kMainMinH))`。左栏已是 `QScrollArea{widgetResizable, 横向滚动关}`（窄窗纵向可滚 ✓）、右栏 QTabWidget 已 `setUsesScrollButtons+ElideRight`（`:740-741` ✓），故最小尺寸定了即可，无需额外重排。
- 建议测试：widget 测试把主窗口 resize 到（960,600）与（720,480）两档，断言不出现横向滚动条/控件裁切/重叠；并断言 `minimumSize()` 等于 token 派生值。

### P1（明显偏差）

**P1-1 桌面多实例无守卫，可同时开多窗共享硬件**
- 现状：`cpp/src/main.cpp:72 QApplication app(...)`、`:89 mbdsdr::MainWindow win;`（已核实），**无 QLockFile / 单实例 socket**。两次启动会得到两个 MainWindow、各自 `new dsp::SpectrumEngine`（`main_window.cpp:148`），对同一 RTL-SDR 句柄/端口形成竞争。
- 目标：单实例守护（车机式「一个应用一个屏」）。
- 改法建议：`main.cpp` 启动时 `QLockFile`（或本地 socket）持锁；第二实例触发时把既有窗口 raise/activate 并退出（不重复开 engine）。锁 key 走常量、不写死到代码逻辑里。
- 建议测试：进程级测试（起两次进程），断言第二实例不创建第二个 engine、立即退出；无法单测时至少在报告标注「需手动/集成测试」。

**P1-2 移动 hero 频率读数触控区 < 44px**
- 现状：`mobile/lib/pages/spectrum_page.dart:498-504` 用 `GestureDetector(onTap:_promptFrequency, child: Text(freqReadout))`——触控热区只有 Text 自身包围盒（freqReadout 26pt ≈ 高 36px，`:265`），**低于 touchMin=44**。
- 目标：hero 数字点击区 ≥44px 高（车机大触控）。
- 改法建议：外层包 `ConstrainedBox(constraints: BoxConstraints(minHeight: AppTokens.touchMin))` + 居中；或换 `InkWell`（带涟漪反馈，与 theme splash 一致）。
- 建议测试：widget 测试对该区域 hit test 一个 44×44 矩形，断言命中。

**P1-3 跨端选中态 token 漂移（同一张设计稿两副面孔）**
- 现状：选中填充 alpha 桌面 `kSelectedFill=rgba(145,156,172,0.16)`（`tokens.h:97`）vs 移动 `selectedFill=0x24919CAC`（=36/255=**0.141**，`tokens.dart:79`）；选中字桌面 `kSelectedText="#d7dee8"`（`tokens.h:98`）vs 移动 `selectedText=0xFFECEAE6`（`tokens.dart:82`）。
- 目标：两端同一 Figma 低饱和蓝灰，alpha 与字色一致。
- 改法建议：对齐到同一数值（建议 alpha=0.16、字色统一 `#d7dee8` 或 `#ECEAE6` 二选一）。
- 建议测试：常量断言测试——两端 `selectedFill` 的 R/G/B 三通道相等、alpha 差 <0.01。

**P1-4 底部导航 6 个目的地，手机窄屏拥挤**
- 现状：`mobile/lib/app/home_shell.dart:69-101` `_barItems` 共 6 项（频谱/天空/AI/录音/活动/设置），窄屏（<720）走 `BottomNavigationBar`（`:219-223`）。Material 3 底栏建议 ≤5 项；6 项在手机宽度下每图标+标签被压窄。
- 目标：窄屏 ≤5 项，或把「录音/活动」合并进二级页。
- 改法建议：窄屏底栏只放 5 个高频项，把「活动」收进「录音」页或 AppBar 入口；宽屏 Rail 可保留 6 项（Rail 不拥挤）。
- 建议测试：widget 测试 360 宽屏断言底栏项数 ≤5、不换行不裁切。

### P2（token 清理 / 一致性，不改变观感但守红线）

| 项 | 现状 file:line | 改法（一律走 token，禁魔法像素） |
|---|---|---|
| 桌面 combo 最小宽未缩放 | `main_window.cpp:1485 aiSessionCombo_->setMinimumWidth(120)`（**裸 120，未 scaled()**，DPI 盲） | 新增/复用 token（如 kComboMinW），`setMinimumWidth(tokens::scaled(tokens::kComboMinW))` |
| 桌面 VFO 默认色字面 | `main_window.cpp:3437 ...,"#7CC4FF"` fallback | 改为 `QString::fromUtf8(tokens::kAccent)`（随 P0-1 一并迁到新交互色 token） |
| 桌面 topBar 边距字面 | `main_window.cpp:146 setContentsMargins(scaled(16),0,scaled(16),0)`（用字面 16 而非 `kSpacingL`） | 换 `tokens::kSpacingL`（数值同为 16，消除「字面」） |
| 桌面仰角图最小高字面 | `main_window.cpp:892 setMinimumHeight(scaled(120))` | 抽 `kElevPlotMinH` token（已 scaled，但仍应命名） |
| 移动控制面板宽度 clamp 字面 | `spectrum_page.dart:101-102 panelW=(maxWidth*0.30).clamp(240.0,340.0)` | 把 240/340/0.30 抽成 AppTokens 常量（如 kControlPanelMinW/MaxW/Ratio） |
| 移动音量%列宽字面 | `settings_page.dart:208 SizedBox(width:44,…)` | 44 恰等 touchMin，但语义是「数字列宽」，抽 `kVolPctColW` |
| 移动按钮内进度圈字面 | `settings_page.dart:418-419 SizedBox(width:16,height:16,…)` | 复用 `AppTokens.iconSizeInline`（14）或抽 token；16 虽在栅格上但仍属字面 |
| 移动设备信息标签列宽字面 | `device_info_card.dart:154 SizedBox(width:56,…)` | 抽 `kInfoLabelColW`（56 在 4pt 栅格上，但需命名） |
| 移动任务编号列宽 off-grid | `task_progress.dart:72 SizedBox(width:22,…)`（**22/4=5.5，off 4pt 栅格**） | 改到 token（如 24=XL 或 16=L），消除 off-grid 魔法数 |
| 跨端余晖/峰值回落数值漂移 | 余晖衰减 桌面 low0.78/high0.93（`tokens.h:110-111`）vs 移动 0.70/0.88（`tokens.dart:143-146`）；S-meter 峰值回落 桌面 12 dB/s（`tokens.h:332`）vs 移动 4 dB/s（`tokens.dart:161`） | **A1 域**，此处仅登记跨端观感不一致：同一信号在两端余晖长短/峰值回落快慢不同，建议 A1 一并对齐到同一 token 数值 |
| 跨端字号基线 | 桌面 pt（`tokens.h:537-541`）vs 移动 logical px（`tokens.dart:224-271`） | 推断：跨端「同层级字号绝对大小」未建立换算表；建议补一份 pt↔px 对照常量，避免两端标题/正文观感脱节 |

---

## 3. 触屏 / 窄窗 / 多窗口适配复查清单（现状 → 问题 → 建议）

### 3.1 桌面端

| # | 现状（file:line） | 问题 | 建议 |
|---|---|---|---|
| D1 | 主窗口 `resize(1280,800)` 但**无 setMinimumSize**（`main_window.cpp:144`；已 grep 全文确认） | 用户可把窗口拖到远小于内容，三栏被挤压、中心频谱低于其 480×320 最小、横向溢出/裁切 | 设 token 化最小尺寸（见 P0-2） |
| D2 | 左栏 `QScrollArea{widgetResizable, 横向滚动关}`（`main_window.cpp:~208`） | ✅ 窄窗纵向可滚，良好 | 保持 |
| D3 | 右栏 `QTabWidget{usesScrollButtons, ElideRight}`（`main_window.cpp:740-741`） | ✅ tab 过多时可滚动/省略，良好 | 保持 |
| D4 | 三栏 `setChildrenCollapsible(false)`（`main_window.cpp:204`） | ✅ 面板不会被拖到 0，良好 | 保持 |
| D5 | 多实例：`main.cpp:72/89` 无 QLockFile | ❌ 可多开、共享同一 RTL-SDR 句柄竞争（见 P1-1） | 加单实例锁 |
| D6 | 文本按钮/列表行已 `setMinimumHeight(scaled(kTouchMin))`（如 `main_window.cpp:497,806-816,1355,1379-1380,1487,1504,1583-1586,1618-1621`） | ✅ 多数交互件已落到 44 触控 | 保持；仅 `:1485` 的 120 未 scaled 需修（P2） |
| D7 | 顶部 topBar `setFixedHeight(kTopbarH=56)`（`main_window.cpp:156`，token `tokens.h:171`） | ✅ token 化，良好 | 保持 |

### 3.2 Flutter 移动端

| # | 现状（file:line） | 问题 | 建议 |
|---|---|---|---|
| F1 | `LayoutBuilder` 宽断点：≥720 走 NavigationRail、≥900 展开、<720 走 BottomNav（`home_shell.dart:190-226`） | ✅ 横竖屏/平板自动按宽度切换，弹性良好 | 保持 |
| F2 | 频谱页 `wide=maxWidth>=600`：宽屏 Row（左控制面板 30% clamp 240–340）/窄屏 Column（`spectrum_page.dart:100-145`） | ✅ 横屏自动左右分栏；但 panelW 的 240/340 为字面（P2） | 抽 token |
| F3 | hero 频率读数 GestureDetector 热区=文字高 ≈36px（`spectrum_page.dart:498-504`） | ❌ 低于 44 触控（见 P1-2） | 包 44 高约束/InkWell |
| F4 | IconButton 默认 48（`spectrum_page.dart:610,734,780`）、Slider 默认 M3 触控高（`:592,620,652`） | ✅ 达标 | 保持 |
| F5 | SegmentedButton 选中块用 selectedFill 低饱和蓝灰（`spectrum_page.dart:508-525,685-703`） | ✅ 符合 Figma；⚠️ M3 SegmentedButton 默认高 ≈32–48（推断：以实际 widget 为准），建议在测试中断言行高 ≥44 | 补 widget 断言 |
| F6 | 底部导航 6 项（`home_shell.dart:69-101`） | ❌ 窄屏拥挤（见 P1-4） | 窄屏收敛到 5 项 |
| F7 | 平板/超大屏：`panelW` 最大只到 340（`spectrum_page.dart:102`） | ⚠️ 推断：超宽屏（如横屏平板 ≥1000）控制面板仍锁在 340，右侧频谱被拉得过宽、控件区偏窄；建议面板随宽度弹性但设上下限 | panelW 改弹性比例 + token 上下限 |
| F8 | 全端无裸 hex、控件颜色均取 AppTokens（grep 已核实 `mobile/lib` 除 tokens/theme 外无 `0xFF……`） | ✅ 良好 | 保持 |

---

## 4. 自检记录

- [x] 只读：全程仅 Read/Grep/Bash(只读 grep/sed/ls) + 新建 `audits/` 目录与本报告；**未改任何 .cpp/.dart、未动 git**。
- [x] 证据：所有「现状」断言均带 `file:line`；Figma 特征带「图上」描述；拿不准处（SegmentedButton 行高、panelW 超宽屏、字号 pt↔px 换算）已标「推断」。
- [x] 红线对照：收敛建议一律「抽 token / 引用 token」，**未建议任何魔法像素**；未出现比赛/competition 字样；spectrum_widget/spectrum_display 渲染器几何（A1 域）未越界重复审，仅在 P2 登记其跨端数值漂移。
- [x] 范围核对：4 张 preview 全部判读页面身份；桌面 `src/ui/` 主要页面（main_window 骨架/右栏 tabs/topBar/statusBar/dialog 入口）+ 仪表控件（s_meter/compass_dial 相关）已读；Flutter `pages/`（spectrum/settings/sky/chat/recordings/activity）+ `widgets/`（s_meter/task_progress/device_info_card/empty_state 等）+ `app/tokens.dart`、`theme.dart`、`home_shell.dart` 已读。
- [x] 已知未尽事项（诚实标注）：
  - 桌面各 dialog（settings_dialog/about_dialog/shortcuts_dialog）内部控件尺寸未逐行展开（体量小、走 QSS 统一，推断风险低）；
  - Flutter 其余页（chat_page/recordings_page/activity_log_page）仅 grep 魔法数，未逐行读布局——已确认无裸 hex、魔法数命中少；
  - SegmentedButton 实际触控行高需以运行时 widget 为准（已标推断并列为测试建议）。
