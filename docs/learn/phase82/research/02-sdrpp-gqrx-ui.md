# 参考对标研究 02 — SDR++ 与 gqrx 的 UI 观感对标（Spectrum / Waterfall / 工具栏 / 调谐 / 模块面板）

> 研究报告（只读，未修改 `/workspace/MBDSDR` 任何代码）
> 研究对象：`/workspace/reference/SDRPlusPlus`（SDR++ v0.x，C++/Dear ImGui/OpenGL）、`/workspace/reference/gqrx`（Qt6，与 MBDSDR 同工具链）
> 参照物：`/workspace/MBDSDR`（C++17/Qt6，三栏 QSplitter + 自绘 SpectrumDisplay）
> 生成日期：2026-10-26
> 报告类型：**UI 观感对标**（比例 / 配色 / 控件摆放 / 交互手势），不是抄代码清单。所有引用带 `相对路径:行号`。

---

## 结论 TL;DR

1. **两家的 Spectrum/Waterfall 都是「瀑布占大头」，但实现体感完全不同**：SDR++ 的瀑布是一张 **OpenGL 纹理**（`glTexImage2D`，双线性过滤），色图查表用 **1,000,000 级**的插值 LUT（`SDRPlusPlus/core/src/gui/widgets/waterfall.h:11`），因此**没有像素颗粒感**；gqrx 的瀑布是 `QImage` 逐像素 `setPixel()`（`gqrx/src/qtgui/plotter.cpp:1455`），色图只有 **256 级**。MBDSDR 的 `buildLut256()`（`cpp/src/ui/spectrum_display.cpp:415-437`）目前也是 256 级 + 逐像素着色，**观感介于两者之间偏 gqrx**。
2. **配色语义上有硬差异**：SDR++ 暗色主题把 **Spectrum trace 色**和 **瀑布底色**都做成 theme JSON 里的独立字段（`root/res/themes/dark.json:15` 的 `"PlotLines": "#66E5FFFF"`、`"WaterfallBackground": "#000000FF"`），即**频谱线是青色、瀑布背景是纯黑**——这是 SDR++ 「仪器感」的两大来源。gqrx 则相反：trace 色由 FFT dock 的色板挑选器给（默认白，`gqrx/src/qtgui/dockfft.cpp:80-85`），而绘图区背景是固定的深棕灰 `0xFF1F1D1D`（`gqrx/src/qtgui/plotter.cpp:59`）。**MBDSDR 现在 trace 用白色（`tokens::kTextAlphaPrimary`）、瀑布底色走色图 0 端点的 `#000000`（`tokens.h:613`），跟 SDR++ 的「青线黑底」只差 trace 一个色相**。
3. **工具栏的常驻/折叠策略分歧**：SDR++ 把 **显示参数全部塞进左栏的 `Display` 折叠面板**（FFT 大小/速率/窗/色图/水位，`SDRPlusPlus/core/src/gui/menus/display.cpp:137-245`），顶栏只留「菜单 / 播放 / 音量 / 大频率 / 调谐模式 / SNR」6 个常驻项（`SDRPlusPlus/core/src/gui/main_window.cpp:335-430`）；gqrx 把 FFT/瀑布/色图放进右侧 `DockFft`（`gqrx/src/qtgui/dockfft.ui`），顶栏几乎不放控件；MBDSDR 把 **FFT/窗/平均/衰减/余晖/色图/dB 全部铺进频谱上方的 FlowLayout**（`cpp/src/ui/spectrum_widget.cpp:118-330`），控件密度明显高于两者，是观感差异的主因之一。
4. **调谐交互三家共识：大频率数字可逐位操作**。SDR++ 的 `FrequencySelect`（`SDRPlusPlus/core/src/gui/widgets/frequency_select.cpp:93-220`）与 gqrx 的 `CFreqCtrl`（`gqrx/src/qtgui/freqctrl.cpp:367-430`）都是**逐位（digit）编辑**：上半/下半格点击增减、滚轮按位步进、右键清零、支持键盘输入与剪贴板。MBDSDR 的大频率数字是静态显示 + 顶部输入框，**缺少「点位即编辑」的机手感**——这是三个项目里最明显的交互差距。
5. **模块/页签的形态差异是设计哲学差异**：SDR++ 的左栏是**可拖拽排序的 `CollapsingHeader` 列表，每个模块自带一个「启用复选框」**（`SDRPlusPlus/core/src/gui/widgets/menu.cpp:74-120`），右栏只有 60px 的垂直缩放条 + Zoom/Min/Max 滑条（`SDRPlusPlus/core/src/gui/main_window.cpp:470-665`）；gqrx 用 `QDockWidget` + `tabifyDockWidget` 做「右栏三合一 tab」（`gqrx/src/applications/gqrx/mainwindow.cpp:190-199`）；MBDSDR 用固定 `QTabWidget`（`cpp/src/ui/main_window.cpp:1355-1368`，14 个页签，部分置灰）。**MBDSDR 的页签数量远超 gqrx（5 个 dock）**，观感上更「工具箱」，SDR++/gqrx 更「仪表盘」。

---

## 1. SDR++：瀑布/频谱 UI（观感对标）

### 1.1 频谱 trace：线宽 1.0、主题色、无抗锯齿、带阴影

SDR++ 的 trace 不是折线一次绘制，而是**逐像素 `AddLine` + 竖向阴影**：

```cpp
// SDRPlusPlus/core/src/gui/widgets/waterfall.cpp:163-172
for (int i = 1; i < fftLines; i++) {
    double aPos = ...; double bPos = ...;
    window->DrawList->AddLine(..., trace, 1.0);        // 主 trace，线宽 1.0
    window->DrawList->AddLine(..., shadow, 1.0);       // trace→底部的竖向阴影
}
```

- **线宽固定 1.0**（`waterfall.cpp:170-172`、`183-184`），不随 UI 缩放变化（只在 `style::uiScale` 缩放线段端点坐标）。
- **颜色来自 theme 的 `PlotLines`**：`ImU32 trace = ImGui::GetColorU32(ImGuiCol_PlotLines)`（`waterfall.cpp:128`），Dark 主题是青色 `#66E5FFFF`（`root/res/themes/dark.json:22`）。
- **Max-hold / 余晖线单独一色**：`FFTHoldColor`，Dark 下是纯黄 `#FFFF00FF`（`dark.json:10`）。
- **抗锯齿**：ImGui `DrawList::AddLine` 默认顶点着色、无 AA（除非 `ImDrawListFlags_AntiAliasedLines`），SDR++ 未显式开——**观感是「锐利但有台阶」的仪器风格**，不是柔和矢量风格。
- **阴影 trick**（`waterfall.cpp:171-172`）：trace 每个采样点向下画一条到底部的半透明竖线，形成「曲线下方被填充」的体感，这是 SDR++ 频谱「立体」的关键细节，MBDSDR 目前只有 `drawPolyline` + ghost（`cpp/src/ui/spectrum_display.cpp:1120-1124`），无此填充。

### 1.2 瀑布：色图映射、分辨率与纹理过滤

**色图是一等公民，且是「文件驱动」的**：

- 14 个 JSON 色图资源：`root/res/colormaps/{classic,classic_green,electric,gqrx,greyscale,inferno,magma,plasma,smoke,temper_colors,turbo,viridis,vivid,websdr}.json`。每个文件 `{"name":…, "author":…, "map":["#RRGGBB", …]}`（`SDRPlusPlus/core/src/gui/colormaps.cpp:16-45`）。
- **色图作者信息会显示在 UI 上**（`display.cpp:239` `ImGui::Text("Color map Author: %s", ...)`），社区贡献可见性好。
- 映射公式（`waterfall.cpp:616-619`）：`pixel = clamp(db, wfMin, wfMax)` 归一到 `[0,1]` → `waterfallPallet[(int)(pixel*(WATERFALL_RESOLUTION-1))]`。`WATERFALL_RESOLUTION = 1000000`（`waterfall.h:11`），**LUT 1M 级，理论上无色带**。
- LUT 构建对色图控制点做线性插值（`waterfall.cpp:944-975`）：`updatePallette()` / `updatePalletteFromArray()` 双实现，后者吃 JSON 数组。
- **纹理过滤是 `GL_LINEAR`**（`waterfall.cpp:707-708`）：缩放时瀑布会「糊」出平滑过渡，这是 SDR++ 瀑布观感的第二个关键点。
- 新增一行的高效路径是 `memmove` 整块下移 + 顶部写一行（`waterfall.cpp:897-905`），与 MBDSDR 的 ring buffer 思路一致。

**布局比例**：

- `FFTAreaHeight` 默认 **300px**（`waterfall.cpp:98`），其中 `fftHeight = FFTAreaHeight - 50`（`waterfall.cpp:100`，50 是 freq 刻度条）；瀑布高度是 `widgetSize.y - fftHeight - 50 - 2`（`waterfall.cpp:730-731`）。
- **用户可拖拽 FFT 与瀑布的分界**（`waterfall.cpp:263, 284-286, 350-355`），clamp 到 `[150, widgetSize.y-50]`，拖动时画一条 `SeparatorActive` 高亮线。
- 布局三段式（`waterfall.cpp:790-795`）：`fftAreaMin`（左 50px dB 轴 gutter）→ `freqArea`（40px 高的频率刻度条）→ `wfMin`（瀑布）。**瀑布紧贴 freq 刻度条**，无空隙；MBDSDR 的 `kDispAreaGap = 0`（`cpp/src/core/tokens.h:659`）已经是同一设计。
- **右栏 60px 是垂直控件条**（`main_window.cpp:470-496`）：`Zoom` / `Max` / `Min` 三个 `VSliderFloat`（每个 20×150px），滑条上方有文字标签。这是 SDR++ 的招牌布局——**调显示范围的滑条不在工具栏上，而在画布右缘竖排**。

### 1.3 顶部工具栏：常驻 vs 折叠

顶栏顺序（`main_window.cpp:333-430`）：

| 顺序 | 控件 | 位置 | 说明 |
|---|---|---|---|
| 1 | 菜单按钮（汉堡） | `main_window.cpp:341-350` | 切换左栏显隐（`showMenu`），快捷键 `Menu` |
| 2 | 播放/停止 | `main_window.cpp:356-382` | 快捷键 `End`；播放时图标切换 |
| 3 | 音量滑条 | `main_window.cpp:385` | `showVolumeSlider(..., 248*uiScale, ...)`，随选中 VFO |
| 4 | **大频率数字** | `main_window.cpp:388-389` | `gui::freqSelect.draw()`，12 位大字 |
| 5 | 调谐模式切换（中心/普通） | `main_window.cpp:391-414` | 改变 `VFOMoveSingleClick` |
| 6 | SNR 表 | `main_window.cpp:416-428` | 100-300px 弹性宽 |
| 7 | Logo 按钮 | `main_window.cpp:432-445` | 点开 Credits |

**所有 FFT/窗/平均/色图都不在顶栏**，而是左栏 `Display` 折叠面板（`display.cpp:137-245`）：`Show Waterfall` / `Full Waterfall Update` / `Lock Menu Order` / `FFT Hold(+speed)` / `FFT Smoothing(+speed)` / `SNR Smoothing(+speed)` / `High-DPI Scaling` / `FFT Framerate` / `FFT Size`（1024…524288，`display.cpp:50-59`）/ `FFT Window`（Rectangular/Blackman/Nuttall，`display.cpp:34-38`）/ `Color Map`。

**观察**：SDR++ 把「调显示」的控件分成三档——
- **最高频**（Zoom/Max/Min）→ 画布右缘竖滑条（常驻）；
- **中频**（FFT 大小/窗/色图）→ 左栏 Display 折叠区（单击展开）；
- **低频**（UI 缩放/锁定菜单序）→ 同一折叠区底部。

### 1.4 调谐交互：VFO 拖拽 / 滚轮步进 / 大频率数字

**大频率数字是可编辑控件**（`frequency_select.cpp:93-220`）：

- 12 位数字，每 3 位插一个小数点（`frequency_select.cpp:113-118`）。
- **每格分上下两半**：上半红色半透明高亮（`IM_COL32(255,0,0,75)`）点击 = +1，下半蓝色（`IM_COL32(0,0,255,75)`）点击 = -1（`frequency_select.cpp:143-153`）。
- 右键 / `Delete` / `Enter` = **该位及低位清零**（`frequency_select.cpp:156-161`）。
- 键盘：`↑/↓` 增减当前位、`←/→` 或 `Backspace` 移动光标（光标用 `backend::setMouseScreenPos` 把鼠标搬到目标位，`frequency_select.cpp:88-91`）。
- **直接敲数字键写入**（`frequency_select.cpp:173-182`），自动右移。
- **滚轮按位步进**（`frequency_select.cpp:184-189`）。
- **Ctrl+C / Ctrl+V 复制粘贴频率**（`frequency_select.cpp:194-220`），粘贴走 `hrfreq::fromString`。
- 前导 0 用 `TextDisabled` 色显示（`frequency_select.cpp:117-123`），视觉上频率位数随值变化。

**VFO / 画布手势**（`waterfall.cpp:249-520`）：

- **FFT 区 / 瀑布区拖拽** = 移动选中 VFO 到鼠标频率，按 `snapInterval` 吸附（`waterfall.cpp:463-474`）；`VFOMoveSingleClick` 开启时单击即调（中心调谐模式）。
- **频率刻度条拖拽** = 平移视窗，越界时联动改中心频率（`waterfall.cpp:375-404`）。
- **VFO 边框拖拽** = 改带宽，clamp 到 `[minBandwidth, maxBandwidth]`，`REF_CENTER` 时 ×2（`waterfall.cpp:358-370`）。
- **滚轮（FFT/瀑布区）**：默认按 `snapInterval` 步进；**Shift = 10 倍步进，Alt = 0.1 倍**（`main_window.cpp:533-543`）；无选中 VFO 时按视窗带宽/20 平移（`main_window.cpp:544-546`）。
- **滚轮（频率刻度条）** = 平移视窗 `viewBandwidth/20`/格（`waterfall.cpp:410-435`）。
- **方向键**：悬停画布时按 VFO `snapInterval` 步进（`main_window.cpp:509-523`）。
- **PageUp/PageDown**：循环切换选中 VFO（`waterfall.cpp:495-520`）。
- **悬停 tooltip**：显示 VFO 名；按住 `Ctrl` 显示频率/带宽/锁定/Strength(dBFS)/SNR(dB)（`waterfall.cpp:480-493`）。
- VFO 在瀑布上的竖线：**选中红 `IM_COL32(255,0,0,255)`，未选中黄 `IM_COL32(255,255,0,255)`**（`waterfall.cpp:224`）。

### 1.5 模块系统对 UI 的影响

- 左栏不是固定菜单，而是 `Menu::registerEntry(name, drawHandler, ctx, inst)` 注册的**动态折叠面板列表**（`menu.cpp:9-21`）。核心固定注册 7 项：`Source` / `Sinks` / `Band Plan` / `Display` / `Theme` / `VFO Color` / `Module Manager`（`main_window.cpp:73-79`）。
- **模块实例自带启用开关**：`item.inst != NULL` 时在折叠标题右侧画一个复选框，勾选调用 `inst->enable()/disable()`（`menu.cpp:74-76, 103-115`）——模块的「上/下线」是一等 UI 操作，不是隐藏在设置里。
- **面板顺序可拖拽**，`Lock Menu Order` 锁定后拖拽显示 tooltip `Menu Order Locked!`（`menu.cpp:88-98`）；顺序持久化到 `menuElements`（`main_window.cpp:482-487`）。
- `Module Manager` 用 ImGui Table 列出实例（Name / Type / 删除按钮）+ 底部「新增实例」行（`module_manager.cpp:41-105`），删除有二次确认（`GenericDialog`，`module_manager.cpp:66-70`）。
- **右栏几乎没有模块面板**——右栏固定 60px 只放 Zoom/Max/Min（`main_window.cpp:470-496`）。所有模块 UI 都在左栏折叠面板里。这是 SDR++ 与 MBDSDR「右栏 14 页签」最大的结构差异。

### 1.6 SDR++ 观感的三个「非显眼」细节

这部分容易被忽略，但决定了「一眼像不像 SDR++」：

1. **快捷键与控件一一对应**：`Menu` 键开合左栏（`main_window.cpp:336`）、`End` 键播放/停止（`main_window.cpp:350`）、`Home` 键开合瀑布（`display.cpp:118` `setWaterfallShown`）、`Esc` 关 Credits（`main_window.cpp:447`）、`←/→` 步进、`PgUp/PgDn` 切 VFO（`waterfall.cpp:495`）。**所有快捷键都是「一个动作一个键」，没有组合键序列**，上手成本极低。
2. **缩放语义是「相对视窗」不是「绝对像素」**：右缘 Zoom 滑条把 `bw∈[0,1]` 映射成 `1000Hz … wholeBandwidth`，且用 `factor = bw²` 非线性映射（`main_window.cpp:625-645`）——**低 zoom 端更细**，符合「找台先粗调后细调」的心理模型。
3. **控件尺寸全部乘 `style::uiScale`**：`btnSize(30*uiScale, 30*uiScale)`（`main_window.cpp:333`）、`wfSliderSize(20*uiScale, 150*uiScale)`（`main_window.cpp:627`），UI 缩放 100/200/300/400%（`display.cpp:96-101`）后布局不塌。MBDSDR 的 `tokens::scaled()`（`tokens.h:33-34`）是同一思路，**这点两家已经对齐**。

---

## 2. gqrx：Qt6 观感对照（同工具链）

### 2.1 QMainWindow 布局：dock 可停靠 vs 固定三栏

- 中央 widget 是 `QWidget + QVBoxLayout`：上部 `QFrame`（放 `CFreqCtrl` + `CMeter` + marker 行），下部 `CPlotter` 占满剩余（`gqrx/src/applications/gqrx/mainwindow.ui:26-355`）。
- **右栏是 dock 区**，5 个 `QDockWidget`：`uiDockInputCtl`（输入/增益）/ `uiDockRxOpt`（解调/静噪）/ `uiDockFft`（FFT/瀑布）/ `uiDockAudio`（音频）/ `uiDockRDS`（RDS）（`mainwindow.cpp:190-199`）。
- **用 `tabifyDockWidget` 合并成两组 tab**：`InputCtl | RxOpt | Fft` 一组（`mainwindow.cpp:193-194`，默认 raise `RxOpt`）、`Audio | RDS` 一组（`mainwindow.cpp:199`）。底部 dock 只有 `Bookmarks`（默认隐藏，`mainwindow.cpp:202-204`）。
- **`setCorner()` 把四个窗口角落指派给邻近 dock 区**（`mainwindow.cpp:180-183`），避免角落空白。
- dock 显隐状态存 QSettings：`restoreState(m_settings->value("gui/state", saveState()))`（`mainwindow.cpp:542`）。
- **View 菜单自动获得每个 dock 的 `toggleViewAction()`**（`mainwindow.cpp:207-215`）——dock 显隐是菜单项，不是工具栏按钮。

**对比 MBDSDR**：`QSplitter(Qt::Horizontal)` 三栏，stretch 1:3:1（`cpp/src/ui/main_window.cpp:2659-2662`），初始 280/800/280（`tokens.h:787-789`）。gqrx 的 dock 可拖出为浮动窗、可 tabify；MBDSDR 的右栏 `QTabWidget` 固定 14 页签（`main_window.cpp:1355-1368`）。**gqrx 的 tabify（3+2 两组）观感上比 14 个平铺页签更「聚类」**。

### 2.2 频谱控件：`QFrame` + `QPainter` 自绘，不是 QGraphicsView

`CPlotter` 继承 `QFrame`（`gqrx/src/qtgui/plotter.h:25`），**三张离屏 pixmap/image**：

| 缓冲 | 用途 | 更新频率 | 代码 |
|---|---|---|---|
| `m_OverlayPixmap` | 网格/轴/书签/带框（静态层） | 只在 resize/参数变时重绘 | `plotter.cpp:272`、`drawOverlay():1952` |
| `m_2DPixmap` | 频谱 trace（pandapter） | 每帧 `draw()` | `plotter.cpp:273` |
| `m_WaterfallImage` | 瀑布 `QImage::Format_RGB32` | 每帧滚动一行 | `plotter.cpp:274`、`paintEvent():1086` |

`paintEvent` 只是把三者 blit 上去（`plotter.cpp:1086-1121`），**瀑布用「offset 环形 + 两段 drawImage」避免整块 memmove**（`plotter.cpp:1113-1120`）。

- **DPR 感知**：`paintEvent` 里 `qRound(width/m_DPR)`（`plotter.cpp:1099`），overlay 的画笔宽度用 `m_DPR`（`plotter.cpp:2042, 2085`）。
- **trace 线宽刻意不缩放 DPR**：`// NOT scaling to DPR due to performance`（`plotter.cpp:1666-1675`），`drawPolyline(m_maxLineBuf, npts)`。
- **峰值检测标记带黑描边**：`QPen peakPen(m_MainLineCol, m_DPR)` + `QPen peakShadowPen(Qt::black, m_DPR)`（`plotter.cpp:1771-1778`）。
- `PLOTTER_UPDATE_LIMIT_MS 16`（`plotter.h:21`）限制刷新率 ≤62.5Hz。

**结论**：gqrx 的自绘模型与 MBDSDR 的 `SpectrumDisplay::paintEvent`（`cpp/src/ui/spectrum_display.cpp:1061`）**同构**——都是 `QPainter` 离屏合成。差异在 gqrx 把「静态网格层」单独缓存（`m_OverlayPixmap`），MBDSDR 目前每帧全量重绘网格（`spectrum_display.cpp:1079-1084`）。

### 2.3 配色：gqrx 的暗色怎么做的，与 SDR++ 的差异

**gqrx 没有主题系统**，颜色是 `#define` 常量 + FFT dock 的色板挑选器：

```cpp
// gqrx/src/qtgui/plotter.cpp:59-65
#define PLOTTER_BGD_COLOR           0xFF1F1D1D   // 绘图区深棕灰
#define PLOTTER_GRID_COLOR          0x80606060   // 网格半透明灰
#define PLOTTER_TEXT_COLOR          0xFFDADADA
#define PLOTTER_CENTER_LINE_COLOR   0x80CCDDFF   // 中心线淡蓝
#define PLOTTER_FILTER_LINE_COLOR   0xB0FF6060   // 滤波边红
#define PLOTTER_FILTER_BOX_COLOR    0x28FFFFFF   // 滤波带白 15% α
#define PLOTTER_MARKER_COLOR        0xB080FF80   // 标记绿
```

- **trace 色可换**：`DockFft` 的 `QtColorPicker` 预置 6 色（White/Yellow/Green/Pink/Blue/Cyan，`dockfft.cpp:80-85`），默认白。
- **瀑布色图 7 种**：`Gqrx / Viridis / Turbo / Plasma / Wht Cmp / Wht Hot / Blk Hot`（`dockfft.cpp:87-93`），默认 `"gqrx"`（`dockfft.cpp:42`），实现在 `setWfColormap()`（`plotter.cpp:2924-2992`）：gqrx 经典图是**手工 5 段分段函数**（黑→深蓝→青→黄→红，`plotter.cpp:2932-2949`），viridis/turbo/plasma 是 256×3 硬编码数组（`plotter.cpp:2664` 起）。
- **瀑布像素取色是反向索引**：`m_ColorTbl[255 - cidx]`（`plotter.cpp:1455`），`cidx` 由 dB 线性映射（`plotter.cpp:1454`）。
- 默认 dB 范围 `-120..-20`（`plotter.cpp:53-54`、`dockfft.cpp:31-32`）。

**与 SDR++ 的对比**：

| 维度 | SDR++ Dark | gqrx |
|---|---|---|
| 主题机制 | JSON theme，5 套（`root/res/themes/*.json`） | 无，`#define` 常量 |
| trace 色 | theme `PlotLines` = 青 `#66E5FF` | 用户色板，默认白 |
| 瀑布背景 | theme `WaterfallBackground` = 纯黑 | 色图 0 端点（gqrx 图 = 黑） |
| 余晖/Max 线色 | theme `FFTHoldColor` = 纯黄 | `m_MainLineCol` 同 trace 色 |
| 格线色 | `IM_COL32(50,50,50,255)` 硬编码（`waterfall.cpp:139`） | `PLOTTER_GRID_COLOR` 半透明灰 |
| UI 面板底色 | theme `WindowBg` = `#0F0F0F` | Qt palette（随系统） |

**对 MBDSDR 的直接启示**：SDR++ 的「青 trace + 纯黑瀑布底 + 黄余晖」是**刻意的三色分工**（trace=信号、hold=历史、waterfall=能量）；gqrx 是「白 trace + 黑底」的中性方案。MBDSDR 的 `tokens::kAccent = "#7CC4FF"`（`cpp/src/core/tokens.h:85`）已经接近 SDR++ 的青，但目前它被注释为「只用于 VFO band box / S 表填充」（`tokens.h:80-84`），**trace 本身仍是白色**。

### 2.4 静噪/增益/解调控件的摆放与分组

`DockRxOpt`（`gqrx/src/qtgui/dockrxopt.ui`）自上而下：

1. **`CFreqCtrl filterFreq`**（滤波器中心微调，`dockrxopt.ui:44`）；
2. **rxFreqLabel + hwFreq**（显示 RX 频率与硬件频率，`dockrxopt.ui:75-94`）；
3. **gridLayout 核心区**（`dockrxopt.ui:115`）：`sqlLabel` + `sqlSpinBox` + `autoSquelchButton` + `resetSquelchButton`（静噪）、`modeSelector`（解调模式 ComboBox，`dockrxopt.ui:176`）、`filterCombo`（模式相关滤波器预设，`dockrxopt.ui:229`）、`filterShapeCombo`（滤波形状，`dockrxopt.ui:441`）、`agcPresetCombo` + `agcButton`（AGC 预设 + AGC 选项对话框，`dockrxopt.ui:142, 394`）、`modeButton`（解调器选项对话框，`dockrxopt.ui:328`）、`nb1Button/nb2Button`（噪声抑制双开关，`dockrxopt.ui:480, 514`）+ `nbOptButton`（NB 选项，`dockrxopt.ui:201`）；
4. **`freqSpinBox`**（步进频率，`dockrxopt.ui:560`）。

`DockInputCtl`（`gqrx/src/qtgui/dockinputctl.ui`）：`LNB LO` 旋钮（`lnbSpinBox`，`dockinputctl.ui:52-62`）、硬件 AGC 开关、增益控件。

**分组逻辑**：**「解调/滤波/静噪」一组（RxOpt）、「硬件/增益/LNB」一组（InputCtl）、「显示/FFT/瀑布」一组（Fft）**。对话框（AGC 选项、NB 选项、解调选项）用按钮 + 弹窗收纳低频参数。

**对比 MBDSDR**：左栏 `QGroupBox` 序列是「源与连接 / 设备信息 / SpyServer / 网络音频 / 频率 / 接收参数 / 多 VFO / 静噪 / ANR / 音频 / 工具 / 录制」（`cpp/src/ui/main_window.cpp:317-1162`）——**MBDSDR 是「面板平铺」，gqrx 是「dock 分组 + 对话框收纳」**。gqrx 的「低频参数进弹窗」能让主面板保持 5-8 行高度。

### 2.5 多 VFO / 书签 / 扫描 的 UI 形态

- **Bookmarks 是独立 dock（默认隐藏）**：`DockBookmarks` 放在底部 dock 区（`mainwindow.cpp:202`），由 `Bookmarks` 单例管理数据（`mainwindow.cpp:77`），书签变更信号触发 plotter overlay 重绘（`mainwindow.cpp:315`）。
- **书签直接画在频谱 overlay 上**：`drawOverlay()` 里 `Bookmarks::Get().getBookmarksInRange(...)` 取视窗内书签，**多层级标签排布**（`nLevels = h / (levelHeight + slant)`，`plotter.cpp:1986-2002`）避免重叠，每条画「斜线 + 名称 + 虚线竖线」（`plotter.cpp:2042-2057`），标签色 `tag.GetColor()` α=100，文字 α=255。
- **DXC Spots 同层绘制**（`plotter.cpp:1993-2005`），与书签混排。
- **Marker A/B**：顶栏 `markerFrame` 里 `setMarkerButtonA/B` + `clearMarkerButtonA/B` + `markerLabelA/B` + `deltaFreqLabel`（`mainwindow.ui:178-310`），标签宽度固定防跳动（`mainwindow.cpp:98-103`）；`{shift|ctrl}-click` 在频谱上自动设 AB marker（`plotter.cpp:648-700`）。
- **扫描（scanner）**：gqrx 主体没有内置扫描页签；扫描相关是 `actionSched`（Sched，可勾选，`mainwindow.ui:535`）与远程控制。**书签/扫描是「数据 + overlay + dock 列表」三件套，不是页签矩阵**。

**对比 MBDSDR**：右栏有「扫描/书签」页签（`cpp/src/ui/main_window.cpp:2209`）+ `bookmark_manager.cpp` + `scan_session.cpp`。gqrx 的做法是把书签**渲染进频谱画布**（多层级标签），不只是列表——**观感上「信号与书签同框」是 gqrx 的强项**。

### 2.6 gqrx 观感的三个「非显眼」细节

1. **每个 dock 都有专属快捷键**：`Ctrl+J` 输入、`Ctrl+R` 解调、`Ctrl+F` FFT、`Ctrl+A` 音频、`Ctrl+B` 书签、`Ctrl+T` 工具栏（`mainwindow.cpp:159-164`）。**dock 显隐通过 `toggleViewAction()` 自动同步到 View 菜单**（`mainwindow.cpp:211-215`），不需要手写菜单项。
2. **固定宽度标签防跳动**：`markerLabelA/B`、`deltaFreqLabel` 用 `QFontMetrics` 量出最坏情况文本宽度后 `setFixedWidth`（`mainwindow.cpp:96-103`）——**数值变化时布局不抖**。MBDSDR 的 S 表/读数区可参考。
3. **滚轮带累积量防丢步**：`m_CumWheelDelta += delta`，`numSteps = numDegrees/15`，不满 1 步直接 return（`freqctrl.cpp:420-427`）——高分辨率触控板快速滚动时不会「跳台」。SDR++ 的 `wheel` 是即时处理，无累积（`main_window.cpp:531`），gqrx 这点更稳。

---

## 3. 三家横向对照（观感速查）

### 3.1 瀑布/谱线布局比例示意（谁占大头）

三家都是「瀑布占大头」，但默认比例与调节方式不同：

```svg
<svg viewBox="0 0 900 320" xmlns="http://www.w3.org/2000/svg" font-family="monospace">
  <style>
    .ttl { fill:#7CC4FF; font-size:13px; font-weight:bold }
    .lbl { fill:#dcdcdc; font-size:11px }
    .sm  { fill:#9a9a9a; font-size:10px }
    .ok  { fill:#7CE8A0; font-size:10px }
    .warn{ fill:#FFB86B; font-size:10px }
  </style>
  <!-- 三列 -->
  <!-- SDR++ -->
  <g transform="translate(20,20)">
    <text class="ttl" x="0" y="0">SDR++ (ImGui+GL)</text>
    <rect x="0" y="12" width="240" height="60" fill="#66E5FF" opacity="0.18" stroke="#66E5FF"/>
    <text class="sm" x="6" y="34">Spectrum trace (FFTArea 300px)</text>
    <text class="sm" x="6" y="50">可拖分界线 clamp[150, h-50]</text>
    <rect x="0" y="72" width="240" height="14" fill="#000000" stroke="#4a4a4a"/>
    <text class="sm" x="6" y="83">freq 刻度条 40px（紧贴）</text>
    <rect x="0" y="86" width="240" height="120" fill="#000000" stroke="#4a4a4a"/>
    <text class="sm" x="6" y="112">Waterfall = GL 纹理 (GL_LINEAR)</text>
    <text class="sm" x="6" y="128">LUT 1M 级 · 无色带</text>
    <rect x="240" y="12" width="18" height="194" fill="#2a2a2a" stroke="#4a4a4a"/>
    <text class="ok" x="262" y="100" transform="rotate(90 262 100)">右缘竖滑条 Zoom/Max/Min</text>
    <text class="warn" x="0" y="224">比例 ≈ 30:70，拖分界线</text>
  </g>
  <!-- gqrx -->
  <g transform="translate(330,20)">
    <text class="ttl" x="0" y="0">gqrx (QFrame+QPainter)</text>
    <rect x="0" y="12" width="240" height="72" fill="#FFFFFF" opacity="0.18" stroke="#FFFFFF"/>
    <text class="sm" x="6" y="34">Pandapter (m_Percent2DScreen=35)</text>
    <text class="sm" x="6" y="50">静态 overlay 层缓存</text>
    <text class="sm" x="6" y="66">trace 色可换（默认白）</text>
    <rect x="0" y="84" width="240" height="122" fill="#1F1D1D" stroke="#4a4a4a"/>
    <text class="sm" x="6" y="112">Waterfall = QImage (256 色 LUT)</text>
    <text class="sm" x="6" y="128">setPixel 逐像素 · 有色带风险</text>
    <text class="sm" x="6" y="144">色图 7 项（gqrx 经典 = 分段）</text>
    <text class="warn" x="0" y="224">比例 35:65，setPercent2DScreen 无 UI</text>
  </g>
  <!-- MBDSDR -->
  <g transform="translate(640,20)">
    <text class="ttl" x="0" y="0">MBDSDR (现状)</text>
    <rect x="0" y="12" width="240" height="48" fill="#FFFFFF" opacity="0.18" stroke="#FFFFFF"/>
    <text class="sm" x="6" y="34">Spectrum trace (kDefaultSpecFraction=0.25)</text>
    <text class="sm" x="6" y="50">trace 白 · 无下方填充</text>
    <rect x="0" y="60" width="240" height="12" fill="#0a0c0e" stroke="#4a4a4a"/>
    <text class="sm" x="6" y="70">freq 条带 22px（紧贴）</text>
    <rect x="0" y="72" width="240" height="134" fill="#000000" stroke="#4a4a4a"/>
    <text class="sm" x="6" y="100">Waterfall = ring QImage (256 LUT)</text>
    <text class="sm" x="6" y="116">色图 3 套 token 停靠点</text>
    <text class="sm" x="6" y="132">垂直 divider 可拖（对齐 SDR++）</text>
    <text class="ok" x="0" y="224">比例 25:75，已对齐 SDR++「瀑布大头」</text>
  </g>
</svg>
```

### 3.2 三色分工对比（trace / 余晖 / 瀑布底）

```svg
<svg viewBox="0 0 900 220" xmlns="http://www.w3.org/2000/svg" font-family="monospace">
  <style>
    .ttl { fill:#7CC4FF; font-size:13px; font-weight:bold }
    .lbl { fill:#dcdcdc; font-size:11px }
    .sm  { fill:#9a9a9a; font-size:10px }
  </style>
  <!-- SDR++ -->
  <g transform="translate(20,10)">
    <text class="ttl" x="0" y="0">SDR++ Dark</text>
    <rect x="0" y="10" width="120" height="20" fill="#66E5FF"/>
    <text class="sm" x="130" y="25">trace · PlotLines #66E5FF</text>
    <rect x="0" y="34" width="120" height="20" fill="#FFFF00"/>
    <text class="sm" x="130" y="49">Max/余晖 · FFTHoldColor #FFFF00</text>
    <rect x="0" y="58" width="120" height="20" fill="#000000" stroke="#4a4a4a"/>
    <text class="sm" x="130" y="73">瀑布底 · WaterfallBackground #000000</text>
    <text class="sm" x="0" y="100">三色分工：信号 / 历史 / 能量</text>
    <text class="sm" x="0" y="116">格线 #323232 硬编码 (waterfall.cpp:139)</text>
    <text class="sm" x="0" y="132">VFO 选中红 · 未选中黄 (waterfall.cpp:224)</text>
  </g>
  <!-- gqrx -->
  <g transform="translate(330,10)">
    <text class="ttl" x="0" y="0">gqrx</text>
    <rect x="0" y="10" width="120" height="20" fill="#FFFFFF"/>
    <text class="sm" x="130" y="25">trace · 用户色板默认白</text>
    <rect x="0" y="34" width="120" height="20" fill="#FFFFFF" opacity="0.55"/>
    <text class="sm" x="130" y="49">Max/余晖 · 同 trace 色</text>
    <rect x="0" y="58" width="120" height="20" fill="#1F1D1D"/>
    <text class="sm" x="130" y="73">绘图区底 · PLOTTER_BGD #1F1D1D</text>
    <text class="sm" x="0" y="100">两色分工：trace 高亮、底中性</text>
    <text class="sm" x="0" y="116">网格 #606060 α=0x80 · 中心线 #CCDDFF α=0x80</text>
    <text class="sm" x="0" y="132">滤波带红 #FF6060 α=0xB0 · 标记绿 #80FF80 α=0xB0</text>
  </g>
  <!-- MBDSDR -->
  <g transform="translate(640,10)">
    <text class="ttl" x="0" y="0">MBDSDR 现状</text>
    <rect x="0" y="10" width="120" height="20" fill="#FFFFFF"/>
    <text class="sm" x="130" y="25">trace · 白 (kTextAlphaPrimary)</text>
    <rect x="0" y="34" width="120" height="20" fill="#9a9a9a"/>
    <text class="sm" x="130" y="49">余晖/Min · 灰 (kTextAlphaTertiary2)</text>
    <rect x="0" y="58" width="120" height="20" fill="#000000" stroke="#4a4a4a"/>
    <text class="sm" x="130" y="73">瀑布底 · 色图 0 端点 #000000</text>
    <text class="sm" x="0" y="100">三色分工未拉开（trace/hold 同冷色系）</text>
    <text class="sm" x="0" y="116">kAccent #7CC4FF 已有但只用于 VFO band / S 表</text>
    <text class="sm" x="0" y="132">→ ★1/★2 直接对齐 SDR++ 的「青/黄/黑」分工</text>
  </g>
</svg>
```

### 3.3 关键指标对照表

| 维度 | SDR++ | gqrx | MBDSDR（现状） |
|---|---|---|---|
| 渲染栈 | ImGui DrawList + OpenGL 纹理 | QFrame + QPainter（3 层离屏） | QFrame + QPainter（每帧全绘） |
| 瀑布载体 | GL 纹理，`GL_LINEAR` | `QImage` 逐像素 | ring `QImage` 逐像素 |
| 色图 LUT | 1,000,000 级插值 | 256 级 | 256 级（`buildLut256`） |
| 色图来源 | 14 个 JSON 文件（含作者） | 7 项硬编码（3 个 256×3 数组） | 3 套 token 停靠点 + 自定义 |
| trace 线宽 | 1.0 固定 | `m_DPR`（峰值描边）/ 1.0（主 trace） | 1.2（`spectrum_display.cpp:1123`） |
| trace 色 | theme `PlotLines` 青 `#66E5FF` | 用户色板默认白 | 白（`kTextAlphaPrimary`） |
| Max/余晖色 | theme `FFTHoldColor` 黄 | 同 trace 色 | `kTextAlphaTertiary2` 灰 |
| trace 下方填充 | 有（逐点竖向阴影线） | 可选 `m_FftFill` | 无 |
| 瀑布/谱线比例 | 默认 FFT 300px，可拖 | `m_Percent2DScreen = 35`（谱线占 35%） | `kDefaultSpecFraction = 0.25`（谱线占 25%） |
| 比例调节 | 拖分界线（clamp 150…） | 未见直接拖拽（`setPercent2DScreen` API） | 拖 divider（`Grab::Divider`） |
| dB 范围控件 | 右缘竖滑条 Max/Min | FFT dock（`setPandapterRange` 信号） | 顶栏 QSpinBox 对 |
| Zoom 控件 | 右缘竖滑条 | FFT dock `fftZoomChanged` + Ctrl+滚轮 | Ctrl+滚轮（`wheelEvent`） |
| 顶部常驻 | 菜单/播放/音量/大频率/调谐模式/SNR/Logo | freqCtrl(280×40) + sMeter(150×40) + marker 行 | 频谱工具栏 FlowLayout（FFT/窗/平均/…） |
| FFT 参数位置 | 左栏 Display 折叠区 | 右栏 DockFft | 频谱上方工具栏 + ⚙高级折叠 |
| 大频率数字 | 12 位逐位可编辑 | `CFreqCtrl` 逐位可编辑 | 大字显示 + 输入框 |
| 滚轮步进 | snapInterval，Shift=10×，Alt=0.1× | 位权 incval，累积 15°=1 步 | 步进调谐（`wheelEvent:1798`） |
| VFO 拖拽 | 拖 body=调谐，拖边=带宽 | 拖 CENTER/LEFT/RIGHT（滤波） | 拖 body/edge（`Grab::VfoBody/VfoEdge*`） |
| 面板形态 | 左栏折叠面板（可拖序）+ 右缘 60px 滑条 | 右侧 dock（3+2 tabify）+ 底部 dock | 三栏 QSplitter + 右栏 14 页签 |
| 主题 | 5 套 JSON（Dark/Light/…） | 无（#define 常量） | `buildDarkQss()` 暗色生成器 |
| 书签呈现 | 无内置书签 | overlay 多层级标签 + 底部 dock | 画布参考线 + 扫描/书签页签 |

---

## 4. 给 MBDSDR 的落地建议（10 条）

> 已实现的不重复提：暗色系（`643a12d`）、大频率数字（`32e7f14`）、瀑布紧贴谱线（`c6088c1`）。
> 每条 = 做什么 + 为什么 + 落点。

### ★1 trace 改用「青色主笔 + 下方半透明竖向填充」

- **做**：把频谱主 trace 颜色从白色换成 `tokens::kAccent`（`#7CC4FF`），并为每个采样点补一条到底边的半透明竖线（α 约 40/255），形成曲线下填充。
- **为什么**：SDR++ 的「青线 + 填充」是它仪器感的第一来源（`waterfall.cpp:128` 取 `PlotLines` 青色；`waterfall.cpp:171-172` 画竖向阴影），gqrx 的白线在暗底上更「平」。填充还能弱化 trace 锯齿，弥补不开抗锯齿的颗粒感。
- **落点**：`cpp/src/ui/spectrum_display.cpp:1123-1124`（`drawPolyline` 处），色值经 `cpp/src/core/tokens.h:85` 的 `kAccent`；填充色新增一个 token（如 `kTraceFill`）放 `tokens.h:116-130` 的 persistence 段旁边。

### ★2 Max-hold / 余晖线改独立暖色（黄），与主 trace 分色

- **做**：峰值保持线用纯黄 `#FFE14D`，余晖 ghost 用同色相但 α 更低；不要再用 `kTextAlphaTertiary2` 灰。
- **为什么**：SDR++ 用 theme 里的 `FFTHoldColor: #FFFF00FF`（`root/res/themes/dark.json:10`）与 `PlotLines` 青色刻意拉开色相，让「历史」与「当下」一眼可分；MBDSDR 现在 hold 线是灰的（`spectrum_display.cpp:1126-1127`），在暗底上存在感弱于主 trace。
- **落点**：`cpp/src/core/tokens.h:116-130`（persistence 段）加 `kHoldColor`；`cpp/src/ui/spectrum_display.cpp:1126-1137` 的 `minLine`/`holdLine` 画笔。

### ★3 瀑布色图 LUT 从 256 级升到 2048 级，并给纹理加线性过滤等效

- **做**：`buildLut256()` 扩展为 `buildLut(n)`（n=2048），`colourForDb()` 直接索引；着色时用 `QImage` 缩放或对相邻行做双线性混合，避免放大时出现色带台阶。
- **为什么**：SDR++ 的 LUT 是 1M 级（`waterfall.h:11`）+ `GL_LINEAR`（`waterfall.cpp:707-708`），放大后无色带；gqrx 256 级 + `setPixel` 在大跨度 dB 时有可见台阶。MBDSDR 的 dB 范围可到 -120…0（120dB），256 级 = 每级 0.47dB，色带风险真实存在。
- **落点**：`cpp/src/ui/spectrum_display.cpp:415-437`（`rebuildColormap`/`buildLut256`）、`cpp/src/ui/spectrum_render.h`（纯函数层，可加单测）。

### ★4 色图资源文件化（JSON），色图名 + 作者显示在工具栏 tooltip

- **做**：把 `tokens.h:613-645` 的三套 `kWaterfallStops*` 改为 `res/colormaps/*.json`（`name` / `author` / `map[]`），运行时扫描加载；`paletteCombo_` 从文件列表生成，tooltip 显示 `色图名 · 作者`。
- **为什么**：SDR++ 的 14 个 JSON 色图 + 作者显示（`colormaps.cpp:16-45`、`display.cpp:239`）让用户/贡献者能直接换色图不用重编译；MBDSDR 现在加色图必须改 token + 重编（`tokens.h:613`、`spectrum_display.cpp:422-424`）。
- **落点**：新增 `cpp/src/ui/colormap_loader.{h,cpp}`；`cpp/src/ui/spectrum_widget.cpp:490-504`（`paletteCombo_`）；token 侧保留默认 fallback。

### ★5 大频率数字支持「逐位点击编辑 + 滚轮按位步进 + 粘贴」

- **做**：大频率数字按 12 位切热区，每格上/下半点击 ±1（该位权重），滚轮按位步进，右键清该位以下，`Ctrl+V` 粘贴频率文本；前导 0 用弱化色。
- **为什么**：这是 SDR++（`frequency_select.cpp:143-189`）与 gqrx（`freqctrl.cpp:367-430`）共同的「收音机手感」，MBDSDR 目前只能靠输入框/滚轮整步，无法「点百位改百位」。前导 0 弱化（`frequency_select.cpp:117-123`）也让频率位数一眼可辨。
- **落点**：大频率数字控件（`cpp/src/ui/main_window.cpp` 大频率数字所在区域，`32e7f14` 引入）+ `cpp/src/ui/spectrum_tune.h`（吸附/步进已有 pure helpers，可复用）。

### ★6 顶栏常驻控件收缩为 5 项，FFT/窗/平均/色图移入「⚙ 显示」折叠区

- **做**：频谱工具栏第一行只留 `播放/停止`、大频率、`⚙显示` 折叠钮、dB Min/Max、Zoom；`FFT 大小 / 窗 / 平均 / 衰减 / 余晖 / 色图 / 深度 / 滚动速度` 移入折叠面板（`advToggle` 已存在，`spectrum_widget.cpp:122-127`）。
- **为什么**：SDR++ 顶栏 6 项（`main_window.cpp:335-430`）、gqrx 顶栏 2 个大控件（`mainwindow.ui:103-141`），都把中频参数下沉；MBDSDR 现在 FlowLayout 平铺 20+ 控件（`spectrum_widget.cpp:118-330`），窄窗换行后观感杂乱。dB/Zoom 是高频手势，保留常驻（SDR++ 把它们放右缘竖滑条，`main_window.cpp:625-660`，说明「显示范围 = 最高频操作」）。
- **落点**：`cpp/src/ui/spectrum_widget.cpp:118-330`（`topRow` 组装）、`advToggle` 逻辑同文件 `122-127`。

### ★7 右栏 14 页签聚类成 3 组（tabify 语义），低频页签进「更多」

- **做**：按 gqrx 的 `tabifyDockWidget` 语义把右栏页签聚成 3 组：`接收（电台/CW/FT8/…）`、`数据（ADS-B/POCSAG/M17/…）`、`工具（扫描/录制库/AI/VNA/…）`；每组用 `QTabWidget` 嵌套或 `QToolBox`，当前置灰的页签合并进「更多」菜单。
- **为什么**：gqrx 5 个 dock 聚成 2 组 tab（`mainwindow.cpp:193-199`）即可覆盖全功能，观感是「仪表盘」；MBDSDR 14 个平铺页签（`main_window.cpp:1355-1368, 1392-2591`）在 280px 右栏里每页签标题被 `ElideRight` 截断（`main_window.cpp:1358`），观感像工具箱列表。分组后每行可放 2-3 个图标化页签。
- **落点**：`cpp/src/ui/main_window.cpp:1355-1368`（`rightTabs_` 组装）、`1392-2591`（14 处 `addTab`）。

### ★8 书签渲染进频谱 overlay（多层级标签），不只放右栏列表

- **做**：在 trace 区顶部按 gqrx 的多层级算法画书签标签（`levelHeight = fontHeight+5`，`nLevels = h/(levelHeight+slant)`，重叠则换层），每条画「斜线 + 名称 + 虚线竖线」；标签色用书签自身色，α=100。
- **为什么**：gqrx 的 `drawOverlay()`（`plotter.cpp:1986-2057`）让书签与信号同框，「看见信号就看见名字」；MBDSDR 现在书签只是一条静默参考线（`tokens.h:167-178`、`spectrum_display.cpp:1153-1154`），不带名签，用户必须切到「扫描/书签」页签（`main_window.cpp:2209`）才能对应。
- **落点**：`cpp/src/ui/spectrum_display.cpp:1150-1160`（bookmark 竖线处）+ `cpp/src/ui/bookmark_manager.cpp`（取视窗内书签）；层级算法可放 `cpp/src/ui/spectrum_render.h` 做纯函数。

### ★9 网格层离屏缓存（overlay pixmap），只在参数变化时重绘

- **做**：把 dB 格线、频率刻度、轴标签绘到一张 `QPixmap`，仅当视窗/高度/dB 范围/格线密度变化时重绘；每帧只 `drawPixmap` + 画 trace/瀑布。
- **为什么**：gqrx 的 `m_OverlayPixmap` + `drawOverlay()`（`plotter.cpp:272`、`1952`）是它在低性能机器上跑得动的原因之一；MBDSDR 的 `paintEvent` 每帧全量重绘网格（`spectrum_display.cpp:1079-1084`），高刷新率下 CPU 有浪费。
- **落点**：`cpp/src/ui/spectrum_display.cpp:1061-1084`（`paintEvent` 开头）、`269`（`resizeEvent` 触发重建）。

### ★10 滚轮步进加 Shift/Alt 修饰（×10 / ×0.1），并显示瞬时 tooltip

- **做**：滚轮调谐时 `Shift` = `snapInterval×10`、`Alt` = `snapInterval×0.1`；滚轮/拖拽调谐时在画布上显示「频率 + 步进」的临时 tooltip，2s 后消失。
- **为什么**：SDR++ 的 Shift/Alt 分档（`main_window.cpp:533-543`）让用户在「快进台」与「细调」间无级切换，是三项交互里学习成本最低、收益最高的一条；gqrx 的 `CFreqCtrl` 滚轮带累积量（`freqctrl.cpp:413-430`）防止快速滚轮丢步。MBDSDR 现在滚轮只有单一步进（`spectrum_display.cpp:1798` 附近）。
- **落点**：`cpp/src/ui/spectrum_display.cpp:1768-1810`（`wheelEvent`）；tooltip 用 `QToolTip::showText` 或画布内绘制，落 `mouseMoveEvent`（`1605`）。

---

## 5. 未查明的开放问题

诚实列出，没有编造：

1. **SDR++ 的具体版本/commit 未确认**。任务书写「v0.x」，仓库根目录无版本标签读取；本报告的行号以当前 `/workspace/reference/SDRPlusPlus` 工作树为准，若参考版本变化需复核。
2. **SDR++ 抗锯齿状态未从渲染端确认**。只看到 `AddLine(..., 1.0)` 未显式设 `ImDrawListFlags_AntiAliasedLines`；Dear ImGui 的 AA 行为依赖 `ImGuiIO`/DrawList flags，**ImGui 默认值本报告未读到**（`imgui.h` 不在参考树内），因此「无 AA」是基于调用点的推断而非直接证据。
3. **gqrx 的「扫描」UI 形态未找到独立页签**。只找到 `actionSched`（`mainwindow.ui:535`，可勾选）与远程控制接口；**scanner 的完整 UI 本报告未读到**，可能在 `gqrx/src/applications/gqrx/` 的其他类或外部脚本，需另查。
4. **gqrx 增益控件的具体控件类型未读全**。`dockinputctl.ui` 只 grep 到 `LNB LO` 与硬件 AGC 开关，**LNA/IF 增益滑条的具体控件名未确认**（可能是动态生成的 `gainSlider` 列表），建议落地★6 时补看 `dockinputctl.cpp`。
5. **gqrx 主题/暗色是否有 QSS 未确认**。`mainwindow.cpp`/`main.cpp` grep 无 `setStyleSheet`/`QPalette`，**gqrx 的「暗色」很可能只是绘图区 `PLOTTER_BGD_COLOR` + 系统 Qt 主题**，面板部分可能跟随系统亮/暗。MBDSDR 的 `buildDarkQss()` 机制比 gqrx 更彻底，这点是 MBDSDR 领先，不是差距。
6. **MBDSDR 大频率数字当前控件的确切类名/文件未在本报告中定位**（任务书提到 `32e7f14` 大频率数字已实现，但未给出文件）；★5 落地前需先定位该控件。
7. **SDR++ 的 `snapInterval` 如何按 VFO 模式确定未深读**（`waterfall.cpp:463` 只看到 `round(off/selVfo->snapInterval)`）；★10 的「Alt=0.1×」在小 snapInterval 时可能低于 1Hz，需要按 MBDSDR 的 `spectrum_tune.h` 吸附粒度做下限保护。
8. **色图 JSON 的 14 个文件本报告只读了 `classic.json` 全文 + `dark.json`/`light.json`**；`turbo.json`/`viridis.json` 的具体停靠点数量未逐个统计（SDR++ 的 `viridis.json` 可能与 MBDSDR `kWaterfallStopsViridis` 的 5 停靠点精度不同）。
9. **gqrx 的 `m_Percent2DScreen` 是否有 UI 入口未确认**（只看到 `setPercent2DScreen()` API，`plotter.h:187`）；可能只在配置文件里改，**「用户可拖谱线/瀑布比例」在 gqrx 可能不是手势**（SDR++ 是拖分界线，`waterfall.cpp:350-355`）。
10. **三家的 HiDPI 行为未在真机验证**。SDR++ 用 `style::uiScale`（100/200/300/400%，`display.cpp:96-101`）、gqrx 用 `m_DPR`、MBDSDR 用 `tokens::scaleFactor()`（`tokens.h:24-34`）；本报告是源码级对比，**未做截图对比**。

---

## 附录：本报告用到的关键文件索引

**SDR++（`/workspace/reference/SDRPlusPlus`）**

| 文件 | 关键行 | 内容 |
|---|---|---|
| `core/src/gui/main_window.cpp` | 73-79 / 333-430 / 470-496 / 509-560 / 625-665 | 菜单注册 / 顶栏 6 项 / 三栏 Columns / 滚轮与方向键 / 右缘 Zoom-Max-Min 竖滑条 |
| `core/src/gui/widgets/waterfall.cpp` | 93-113 / 120-215 / 249-520 / 598-630 / 700-800 / 870-980 / 944-975 | 默认参数 / drawFFT 逐像素 trace+阴影 / processInputs 拖拽手势 / updateWaterfallFb 色图映射 / onResize 三段布局 / pushFFT 滚动 / LUT 插值 |
| `core/src/gui/widgets/waterfall.h` | 11 / 236 | `WATERFALL_RESOLUTION 1000000` / `waterfallPallet[]` |
| `core/src/gui/widgets/frequency_select.cpp` | 24-91 / 93-220 / 222-256 | 位热区布局 / 逐位点击-键盘-滚轮-剪贴板 / setFrequency |
| `core/src/gui/colormaps.cpp` | 16-45 | JSON 色图加载（name/author/map[]） |
| `core/src/gui/menus/display.cpp` | 34-59 / 137-245 | FFT 窗/大小选项表 / Display 面板控件全集 |
| `core/src/gui/menus/theme.cpp` | 10-40 | theme 加载与 `ScaleAllSizes(uiScale)` |
| `core/src/gui/menus/module_manager.cpp` | 41-105 | 模块实例表 + 新增行 + 删除确认 |
| `core/src/gui/widgets/menu.cpp` | 9-21 / 74-120 / 130-170 | registerEntry / 模块启用复选框 / 拖拽排序 + 锁定 |
| `root/res/themes/dark.json` | 10 / 22 / 55 | `FFTHoldColor` 黄 / `PlotLines` 青 / `WaterfallBackground` 黑 |
| `root/res/themes/light.json` | 10 / 22 | `FFTHoldColor` 橙 / `PlotLines` 蓝 |
| `root/res/colormaps/classic.json` | 1-19 | 15 控制点经典色图（黑→蓝→白→黄→红→暗红） |
| `core/src/module.h` | 33-60 / 82-88 | `ModuleInfo_t` / `createInstance` / enable-disable API |

**gqrx（`/workspace/reference/gqrx`）**

| 文件 | 关键行 | 内容 |
|---|---|---|
| `src/applications/gqrx/mainwindow.cpp` | 96-109 / 180-215 / 223-310 / 542 | freqCtrl/sMeter 初始化 / dock 布局 + tabify + View 菜单 / plotter 信号连接全集 / restoreState |
| `src/applications/gqrx/mainwindow.ui` | 26-355 / 430-455 / 455-535 | 中央布局（freqCtrl 280×40 / sMeter 150×40 / marker 行 / plotter）/ mainToolBar / actionDSP 等 |
| `src/qtgui/plotter.h` | 12-21 / 25 / 143-146 / 187 / 260-275 | 常量 / `class CPlotter : public QFrame` / eWaterfallMode / `setPercent2DScreen` / 三缓冲成员 |
| `src/qtgui/plotter.cpp` | 50-65 / 88-115 / 158 / 1003-1085 / 1086-1121 / 1126-1700 / 1425-1465 / 1655-1790 / 1952-2240 / 2664 / 2924-2992 | 颜色 #define / 构造 / `m_Percent2DScreen=35` / resizeEvent 三缓冲 / paintEvent blit / draw() trace / 瀑布 setPixel+`255-cidx` / 峰值黑描边 / drawOverlay 书签多层级 + 网格 / viridis 数组 / setWfColormap 7 项 |
| `src/qtgui/freqctrl.cpp` | 38-70 / 344-430 | DIGIT_SIZE_PERCENT / 位编辑状态 / mouseMove-Press / wheelEvent 累积 15°=1 步 |
| `src/qtgui/dockfft.cpp` | 31-42 / 75-93 / 110-220 | 默认 dB -120..-20 / 色板 6 色 + 色图 7 项 / fftRate-fftSize-wfSpan 读写 |
| `src/qtgui/dockrxopt.ui` | 44-700 | filterFreq / sqlLabel+sqlSpinBox+autoSquelch / modeSelector / filterCombo+filterShapeCombo / agcPresetCombo+agcButton / nb1+nb2+nbOptButton |
| `src/qtgui/dockinputctl.ui` | 52-99 | LNB LO / 硬件 AGC |
| `src/qtgui/meter.h` / `meter.cpp` | 37-49 / 73-92 | `class CMeter : public QFrame` / setLevel/paintEvent |

**MBDSDR（`/workspace/MBDSDR`，只读对照）**

| 文件 | 关键行 | 内容 |
|---|---|---|
| `cpp/src/core/tokens.h` | 24-34 / 80-90 / 116-130 / 165-180 / 333-334 / 600-668 / 683-684 / 696-723 / 787-789 | scaleFactor / kAccent 用途 / persistence token / bookmark token / kZoomMin-Max / 瀑布色图停靠点 + 布局 token / wf 深度选项 / VFO handle / 三栏初始尺寸 |
| `cpp/src/ui/spectrum_display.h` | 3-20 / 67-95 / 157-200 / 250-263 / 325-341 / 396-431 | 统一画布布局注释 / waterfallRect-dividerY / cursor+palette API / specFraction / 信号 + 事件 / Grab 状态机 |
| `cpp/src/ui/spectrum_display.cpp` | 48-67 / 269 / 400-437 / 1061-1130 / 1150-1240 / 1518-1745 / 1768-1810 | specFraction fallback / resizeEvent / ring 重绘 + rebuildColormap + colourForDb / paintEvent trace+ghost / bookmark-cursor-mirror 竖线 / mousePress-Move-Release Grab / wheelEvent Ctrl=zoom, strip=pan, trace=tune |
| `cpp/src/ui/spectrum_widget.cpp` | 32 / 118-330 / 480-510 | FlowLayout 会换行 / topRow 控件全集 / paletteCombo + wfDepth |
| `cpp/src/ui/main_window.cpp` | 304-317 / 1163-1221 / 1355-1368 / 1392-2591 / 2659-2662 / 3082-3093 | 三栏 QSplitter 组装 / centerTabs / rightTabs 14 页签 / addTab 逐个 / stretch 1:3:1 / VFO 信号连接 |
| `cpp/src/ui/spectrum_render.h` | 全文 | 纯瀑布算术（decimate/lut/ticks），★3/★8 的单测落点 |
| `cpp/src/ui/spectrum_tune.h` | 全文 | 吸附/步进 pure helpers，★5/★10 可复用 |
