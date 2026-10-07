# Phase62 · UI 弹性化自查 + 左栏右缘裁切修复（窄窗 / 全屏 offscreen 快照）

- 仓库 HEAD：`e85a88a`；全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`），未操控 GUI。
- 快照目标：`cpp/tests/ui_screenshot_narrow.cpp` → `ui_shot_narrow`（窗口尺寸已参数化为
  `MBD_W` / `MBD_H` 环境变量，缺省 820×640，向后兼容）。
- 本轮**修复了上一轮记录的"共因真 bug"**（左栏/右栏被钉死 140px、左栏 4 处右缘裁切），
  并完成同轮 UI 弹性走查。

## 修复前后对照（splitter 三栏实测宽度，日志直读）

`scaleFactor=1.000`：

| 请求窗 | 修复前 左/中/右 | 修复后 左/中/右 |
|---|---|---|
| MBD_W=1920 × 1080 | 140 / 1632 / 140 | **420 / 1072 / 420**（=0.22/0.56/0.22） |
| MBD_W=960 × 640   | 140 / 672 / 140 | **198 / 556 / 198** |
| MBD_W=640 × 640（被 `kMainMinW=960` 钳到 960×640） | 140 / 672 / 140 | **198 / 556 / 198** |

左栏内容最小需求 ≈ margins(24) + `kFreqSpinMinW=140` = 164px；修复后左栏
≥198px（960 档）/ 420px（1920 档），均 >164，裁切消除。

## 根因（已核实）

`cpp/src/ui/main_window.cpp`：

1. `restoreUiState()`（构造期调用）末尾 `setFocusMode(focus, /*animate=*/false)`，
   focus 默认 false → 走"恢复侧栏"else 分支。
2. 首启时 `leftRailW_/rightRailW_==0`，else 分支用**构造当时的 splitter 宽度**
   （≈636px，窗口尚未 show）× `tokens::kRatioLeft(0.22)` 算出 ≈140，对左右栏
   `setMaximumWidth(140)`。此后窗口拉大到 1920，该 maximumWidth 上限不解除，
   左右栏永久钉死 140px → 左栏内容（最小 164px）右缘被裁 4 处。
3. 走查中额外发现：右栏 `rightTabs_`（QTabWidget，标签可滚动）的
   `minimumSizeHint≈428px`（按"显示全部标签"估算），即使解除 maximumWidth 钉死，
   在 960 窄窗下 splitter 仍优先满足右栏 428、中心 556，把左栏挤到 62px。

## 修复方案（file:line，均在 main_window.cpp）

### 1. `setFocusMode` 恢复分支（:3704-3727）——不再钉死 stale maximumWidth

```cpp
// 改前（首启，leftRailW_==0）：
if (!animate) {
    left->setMaximumWidth(lw);   // lw = 构造期 stale total×0.22 ≈ 140 → 永久钉死
    right->setMaximumWidth(rw);
}
// 改后：
if (!animate) {
    if (leftRailW_ > 0 && rightRailW_ > 0) {
        left->setMaximumWidth(lw);   // 用户拖过的记录宽：按原样钉回（语义不变）
        right->setMaximumWidth(rw);
    } else {
        left->setMaximumWidth(QWIDGETSIZE_MAX);   // 首启：不钉 stale 值
        right->setMaximumWidth(QWIDGETSIZE_MAX);
    }
}
```
- 用户拖过 splitter 后 `leftRailW_>0` 的路径**完全不变**。
- 首启路径不在这里 `setSizes`：构造期 total 是 stale 值，实测会与子控件 sizeHint
  打架（右标签栏被弹到 728px），故留待 post-show 用真实宽重算（见第 3 点）。

### 2. 折叠动画起始值（:3683-3689）——用刚记录的真实栏宽

```cpp
// 改前：collapse(left, focusAnimL_, left->maximumWidth());
// 改后：
collapse(left,  focusAnimL_,  leftRailW_  > 0 ? leftRailW_  : left->maximumWidth());
collapse(right, focusAnimR_, rightRailW_ > 0 ? rightRailW_ : right->maximumWidth());
```
首启未钉死时 `maximumWidth()` 是巨大默认值，动画会"干坐到最后"。`:3659/:3660` 已在
折叠前把 `leftRailW_=left->width()` 记为真实可见宽；其余既有路径
`maximumWidth()==记录宽`，故此改动是 bit-for-bit 无变化，仅修复首启动画起点。
动画时长/缓动/`setVisible(false)` 收尾语义均未动。

### 3. `restoreUiState` 首启延迟套比例（:3916-3924）

```cpp
if (mainSplitter_ && splitterState.isEmpty()) {
    QTimer::singleShot(0, this, [this]() { resetSplitterRatios(); });
}
```
仅在**无保存 splitter 几何**（真·首启）时，于窗口拿到真实尺寸后的下一个事件循环，
复用既有 `resetSplitterRatios()`（= `setSizes({total×0.22, center, total×0.22})`）
套一次 0.22/0.56/0.22。有保存几何的用户走 `restoreState`，个人布局不被覆盖。

### 4. 右栏 sizePolicy（:930）——让可滚动标签栏允许收缩

```cpp
rightTabs_->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
```
右标签条已 `setUsesScrollButtons(true)`，本就该滚动；之前其 `minimumSizeHint≈428`
在窄窗下饿死左栏。改为 Ignored 后右栏 minHint 降到 ≈20（只留滚动按钮），窄窗下
右栏收缩到设计的 198px（标签滚动显示），左栏不再被挤。

## 快照（真实 offscreen PNG，已覆盖旧图）

| 档 | 请求尺寸 | 实际抓帧 | 路径 |
|---|---|---|---|
| 窄窗 | MBD_W=640 | **960×640**（`kMainMinW=960` 钳制） | `docs/learn/phase62/ui-shot-640.png` |
| 全屏 | MBD_W=1920 | 1920×1080 | `docs/learn/phase62/ui-shot-1920.png` |

## 逐面板核查结论（修复后）

### 960 档（实际 960×640）

| 面板 | 结论 |
|---|---|
| 顶栏 | 无叠字、无裁切 |
| 左栏 · 源与连接 / RSSI | "RSSI: -- dBFS"、"高级 ▶"按钮**完整**（原"高"标签裁切消除） |
| 左栏 · 设备信息 | "设备 RTL-SDR 未连接"**完整**（原裁成"RTL-SD"消除） |
| 左栏 · SpyServer / 频率 / 接收参数 | "开启 SpyServer"、"端口 5555"、"中心频率 98.500 MHz"、"步进 10 kHz"、"强制单声道"按钮右缘**全部完整**（原"98.500 M"、"强制单声"裁切消除） |
| 中部 · 标签页 / 控制排 / 频谱 + 瀑布 / 参数表格 | 控制排两行排开无叠字；频谱网格 -0…-100 可读；表头 #/频率(MHz)/强度(dBFS)/带宽(kHz) 无挤压 |
| 右栏 · 解码标签条 | "解码 CW A… ◀▶" 标签滚动显示，无裁切（窄窗下收缩到 198px 属预期） |
| 底部 · 三步上手条 / 状态栏 | 无叠字，可读 |

### 1920 档（1920×1080）

| 面板 | 结论 |
|---|---|
| 左栏（420px） | 全部控件一行排开，"RTL-SDR 未连接"/"98.500 MHz"/"高级 ▶"/"强制单声道"**完整无裁切** |
| 右栏（420px） | "解码 CW A… 寻呼 数据 m17 VOR ◀▶" 全部标签可见 |
| 中部（1072px） | 控制排一行排开无叠字，频谱/瀑布/表格正常 |
| 专注模式（`ui_shot_focus` 1280×800 复拍） | 两侧栏收起、频谱占满全宽、专注按钮高亮——折叠/展开动画路径回归正常 |

**结论：三档快照 0 裁切、0 叠字、左栏不再贴死 140px。**

## 固定值 token 化清单（本轮走查）

按要求抽查频谱工具条、瀑布、数据表格、状态栏等关键面板的间距/层级一致性：
- 裸 `setFixedWidth/Height`、裸 `setColumnWidth`、裸字号、裸 padding/margin 字面量：**无新增**
  （`main_window.cpp` 内几何统一走 `tokens::scaled()` / 具名 token；`levelBar_` 的
  gradient/radius 已走 `kCard1/kAccent/kRadiusSmall`）。
- 本轮未新增具名 token：修复走的是布局语义（解除 stale maximumWidth 钉死 + 延迟套比例
  + 右栏 sizePolicy），不是把裸 140 换成另一个裸数，故无需新增 `kSidebarMinW/MaxW`。
- 唯一视觉相关改动如实说明：右栏 `sizePolicy→Ignored` 后，窄窗下右标签条从"全标签铺开
  428px"变为"滚动显示 198px"——这正是窄窗弹性设计意图（标签条本就支持滚动），全屏下
  右栏仍为 420px、全标签可见，视觉不变。

## 改动文件清单

1. `cpp/src/ui/main_window.cpp`：
   - `:930` 右栏 `rightTabs_->setSizePolicy(Ignored, Preferred)`；
   - `:3683-3689` 折叠动画起点改用记录的真实栏宽；
   - `:3704-3727` 首启恢复分支不再 `setMaximumWidth(stale≈140)`，改 `QWIDGETSIZE_MAX`；
   - `:3916-3924` 首启（无保存 splitter 几何）延迟一事件循环套 `resetSplitterRatios()`。
2. `docs/learn/phase62/ui-flexibility-check.md`：本文件，更新修复方案与核查结论。
3. `docs/learn/phase62/ui-shot-640.png` / `ui-shot-1920.png`：覆盖为修复后快照。

未改 `tokens.h`（无需新增 token）；未碰 CMake；未 git add/commit/push；活动参数零硬编码；
无预置 TLE/呼号；无"比赛/competition"字样；GPL 措辞中立。

## 测试计数与回读

- 未新增 ctest：`ui_shot_narrow` 抓帧前已直读并打印 splitter 三栏实际宽度（数字回读：
  左栏 ≥198px 且随窗口 960→1920 从 198→420 更新），等价于"splitter 状态回读"核查。
- 仓库现有 `add_executable(ui_shot_*)` 28 个；`ctest` 共 134 项（本轮未改测试源码，计数不变）。
- 折叠/展开动画路径回归：`ui_shot_focus`（1280×800）复拍确认两侧栏收起、频谱全宽。
