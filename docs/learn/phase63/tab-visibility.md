# Phase63 右栏 tab 显隐（SDR++ 模块显隐机制的轻量等价）

> 范围：右栏 `rightTabs_`（QTabWidget）面板 tab 可关闭、可恢复、可持久化。
> 全程 offscreen/CLI，未操控 GUI；零 mock；活动参数零硬编码；干净室实现，未抄 SDR++ 源码。

## 为什么做这个

SDR++ 的"模块显隐"（用户在模块列表里关掉不用的面板、随时恢复）是它保持窄主界面清爽
的核心机制。MBDSDR 右栏目前堆了 ~15 个面板 tab（解码组：CW/ADS-B/寻呼/数据/m17/VOR；
观测组：星座/天空/扫描书签；系统组：录制库/AI/电台……），窄窗口下标签栏滚动后很多面板
日常根本用不到，却始终占着一个 tab 位。本阶段把"关闭不用的面板 tab、随时恢复"这一
轻量等价机制补上。

## 为什么不做自由 dock 拖拽（如实记录）

完整 SDR++ 式自由停靠（dock 浮动/拖放/多列停靠）是架构性大改：需要引入 QDockWidget
体系、重写右栏布局所有权、处理 splitter/dock 的几何协商、并重新接线所有"按 index 引用
面板"的逻辑。当前 MBDSDR 的 tab 已经按**功能域分组**（解码 / 观测 / 系统，disabled 分组头
tab 组织），用户要的其实只是"把不用的那几个藏起来"，而不是"把面板摆来摆去"。
所以本期只做**显隐**，不做自由布局——需求匹配、改动面小、index 语义零破坏。

## 实现（全部在 cpp/src/ui/main_window.{h,cpp}）

### 显隐机制（main_window.cpp:2069, 3885）

- `rightTabs_->setTabsClosable(true)`（main_window.cpp:2069）：每个真实面板 tab 出现 ×。
- `tabCloseRequested(idx)` → `MainWindow::onRightTabCloseRequested(idx)`（main_window.cpp:3885）：
  **只隐藏，不 removeTab**——`QTabWidget::setTabVisible(idx, false)`（Qt 6.8 原生 API）。
  - **index 稳定性**：widget 指针、tab 顺序、count 全部不变；解码器面板数据流、
    bookmarkManager、`setCurrentWidget(parentWidget)` 跳转（录制库/星座等）按指针引用，
    天然不受影响。`currentChanged→scheduleSave` 的既有接线一行未动。
- disabled 分组头 tab（"解码"/"观测"/"系统"）在 setTabsClosable 后逐个
  `tabBar()->setTabButton(i, QTabBar::RightSide, nullptr)`（main_window.cpp:2073-2076）
  摘掉 ×——分组头不是面板，不可关。

### 恢复入口（main_window.cpp:2084, 3900）

两条等价路径，都调 `showAllRightTabs()`（main_window.cpp:3900）：

1. 右栏标签栏**右键上下文菜单**："显示全部面板"（无可恢复项时 action 置灰）。
   右键点在标签栏空白处即可，即使只剩一个 tab 也永远可达。
2. 右栏顶部扁平小按钮 `rightTabRestoreBtn_`（objectName `rightTabRestoreBtn_`→按钮自身
   名 `rightTabRestoreBtn`）：**仅当 ≥1 个面板被隐藏时才出现**（诚实空态，否则不占位），
   一键恢复全部。

### 诚实空态 / 安全（main_window.cpp:3891-3894）

- 关闭前数一遍"可见的真实面板"（enabled 且 visible），若只剩 1 个则**拒绝再关**——
  右栏永远至少留一个可见面板，不会出现全空右栏。
- 恢复入口（右键菜单 + 按钮）因此也永远可达。

### 持久化（main_window.cpp:3967-3976, 4290-4310）

- 关闭列表按 **tab 文本**存 `QSettings("MBDSDR","MBDSDR")` 的 `ui/hiddenRightTabs`
  （QStringList）。按文本存是因为运行期 tab 顺序本来就不变，但跨版本加/删 tab 后
  按 index 存会错位；文本匹配不上就自然忽略（诚实降级）。
- 写入位置在既有 `saveUiState()` 的 "Layout / tabs / FFT" 段，与 `ui/rightTabIndex`
  并列；防抖 500ms 定时器 + 析构 flush 都是既有机制，本期零改动。
- 启动 `restoreUiState()` 先按文本把对应 tab `setTabVisible(false)`，再 clamp 恢复
  `ui/rightTabIndex`；若记住的当前页后来被用户关了，自动落到第一个可见面板
  （跳过 disabled 分组头）（main_window.cpp:4290-4310）。

## 测试（cpp/tests/test_ui_integration.cpp，17/17 通过）

新增三个槽，全部走真实 MainWindow + 真实信号路径（`QMetaObject::invokeMethod(tabs,
"tabCloseRequested", ...)`，等价于用户点 ×）：

- `rightTabCloseHidesTabKeepsIndexStable`：关闭"数据"tab → `isTabVisible`=false、
  **count 不变、该页 widget 指针不变、左右邻居 widget 指针不变**；恢复按钮出现；
  `showAllRightTabs()` 后复现且按钮消失。
- `rightTabVisibilityPersistsRoundTrip`：窗口1 关闭"数据"→ 析构 flush 后
  `ui/hiddenRightTabs` 含"数据"；窗口2 启动时该 tab 起始即隐藏（真实往返）。
- `rightTabCloseLastVisibleRefused`：关到只剩 1 个可见面板后再关它 → 拒绝，
  该 tab 仍可见。

## 快照（docs/learn/phase63/ui-shot-*-hidetab.png）

`ui_shot_narrow` 新增 env 门控 `MBD_HIDETAB=<tab名>`（cpp/tests/ui_screenshot_narrow.cpp:59-71），
经真实 `tabCloseRequested` 路径关闭指定 tab，默认页三档：

- `ui-shot-640-hidetab.png`（MBD_W=640 被窗口最小宽 clamp 到 960，harness 既有行为）
- `ui-shot-960-hidetab.png`（960 档）
- `ui-shot-1920-hidetab.png`（1920 档）

读图核查：分组头"解码"无 ×（正确）；CW/ADS-B/寻呼/m17 等真实 tab 均带 ×；"数据"
已从标签栏消失；右栏顶部"显示全部面板"按钮出现且文字完整；0 裁切、0 叠字；
splitter 三栏比例正常（960 档 198/556/198，1920 档 420/1072/420）。

## 改动文件清单

- `cpp/src/ui/main_window.h`：`showAllRightTabs()` 公共入口、两个私有槽、
  `rightTabRestoreBtn_` 成员。
- `cpp/src/ui/main_window.cpp`：构造段接线（:2063-2106）、三槽实现（:3885-3914）、
  持久化写（:3967-3976）、启动恢复（:4290-4310）。
- `cpp/tests/test_ui_integration.cpp`：三个新槽。
- `cpp/tests/ui_screenshot_narrow.cpp`：`MBD_HIDETAB` 门控。
- `docs/learn/phase63/tab-visibility.md`（本文）+ 三张快照 PNG。
