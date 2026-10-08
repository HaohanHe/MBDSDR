# Phase63 书签分组视图（BookmarkManager.group 的纯显示层落地）

> 范围：书签表从平铺列表升级为按 `BookmarkManager.group` 分区渲染。
> 全程 offscreen/CLI，未操控 GUI；零 mock（数据全部来自真实 BookmarkManager）；
> 活动参数零硬编码（分区样式走 tokens）；干净室实现，未抄 SDR++ 源码；
> 不 git add/commit/push。

## 为什么做这个

存储层早已就绪：`Bookmark.group` 字段、`groups()`（含空组 ""）、`byGroup(g)`、
`update()` 改组、`list()` 按 (group, freq) 排序都在 bookmark_manager.{h,cpp} 里
（测试 test_bookmark.cpp 已全覆盖）。但 UI 书签表一直是平铺列表——每行一书签，
"分组"只是第 5 列的文字，组与组之间没有视觉分区。本期只补**显示层**：
按 `groups()` 分区渲染，真实数据，不改存储语义、不改索引语义。

## 方案冻结：QTableWidget 分区行，不换控件

候选两条路：

1. **QTreeWidget 顶层=组、子项=书签**：控件类型整体替换，objectName、selection
   行为、双击信号签名全变，现有 `findChild<QTableWidget*>("bmTable")` 接线与
   test_ui_integration 的既有断言全部要重写——改动面最大。
2. **QTableWidget 分区行（本期选用）**：控件不动、5 列结构不动、objectName
   `bmTable` 不动；视觉行里插入"分区标题行"，**分区行不参与索引**，书签数据行
   把存储索引写进 `Qt::UserRole`，交互槽经换算读取。

选 2 的理由：既有交互（添加/编辑/删除/双击调谐）全部按"视觉行 → 存储索引"的
唯一换算函数过一遍，存储索引语义（`list()` 下标）零漂移；测试侧只需把
"按行号 invoke"改成"按 UserRole 找视觉行"。

## 实现（全部在 cpp/src/ui/main_window.{h,cpp}）

### 分区渲染（main_window.cpp:3335 `refreshBmTable()`）

- **空库诚实空态**（main_window.cpp:3345）：`list()` 为空时只插一行
  "暂无书签"，居中、`ItemIsEnabled`（不可选不可编）、UserRole = -2，
  前景走 `tokens::rgbaA(tokens::kTextAlphaTertiary)`。绝不预置任何电台。
- **非空库**：遍历 `bookmarkManager_->groups()`（升序，含 ""），每个组：
  - 先插一行**分区标题行**：文本 `组名 (N)`，空组渲染成 `默认 (N)`（N 来自
    `byGroup(g).size()`）；`ItemIsEnabled` 静态分隔、UserRole = -1；
    背景 `tokens::card1()`、前景 `QColor(kTextSecondary)`、字重
    `QFont::DemiBold`（= tokens::kWeightSemi 600）。
  - 再按 `byGroup(g)` 顺序插该书签的 5 列数据行（名称/频率/模式/带宽/分组列
    文案与旧平铺完全一致，空组在分组列仍显示"默认"）。
- **索引对齐**（main_window.cpp:3355-3360 注释）：`list()` 本就按
  (group, freq) 排序，所以 groups()→byGroup() 切片顺序拼起来就是 list() 全序；
  遍历时游标 `storeIdx` 从 0 递增，数据行 col0 的 `Qt::UserRole` 直接写
  该存储下标——零查找、零错位。

### 视觉行 → 存储索引换算（main_window.cpp:3414 `bmStoreIndexAtVisualRow`）

```cpp
int MainWindow::bmStoreIndexAtVisualRow(int visualRow) const;
// 读 col0 项 UserRole：>=0 即存储索引；分区行(-1)/空态行(-2)/越界 → -1
```

三个既有交互槽全部改为经它换算（接线一行不多、语义不变）：

- 编辑（main_window.cpp:1608）：`currentRow()` → 换算 → `list().at(row)` /
  `update(row, bm)`；
- 删除（main_window.cpp:1618）：`currentRow()` → 换算 → `removeAt(row)`；
- 双击调谐（main_window.cpp:1644）：视觉行 → 换算 → 取 `list().at(idx)` 调谐。
  双击分区标题行 = 诚实 no-op（测试断言不调谐）。

声明在 main_window.h:727。

## 测试（cpp/tests/test_ui_integration.cpp）

新增槽 `bmGroupedViewSectionsEmptyStateAndIndexMapping`：

1. 清空真实 BookmarkManager → 表恰 1 行"暂无书签"，UserRole=-2；
2. 注入 3 组 4 条（默认/AIR/VHF）→ 表恰 7 行（3 分区头 + 4 数据行）；
   分区头文本 `默认 (1)`/`AIR (1)`/`VHF (2)`、UserRole=-1；数据行 UserRole
   = 0/1/2/3 与 list() 下标一一对应；
3. 分组视图下双击视觉行 5（存储 idx=2，144.8MHz）→ 引擎真调到该频；
   双击分区头行（视觉 4）→ 频率不动；
4. `update(2, …group="")` 把 144.8 挪进默认组 → 分区重建为
   `默认 (2)`/`AIR (1)`/`VHF (1)`，行总数仍 7，UserRole 按新 list() 重排。

既有槽 `scanHitSaveBookmarkThenJump` 同步修正：双击调用前先在表里按
UserRole==存储下标 找到视觉行（旧代码直接拿存储下标当视觉行，平铺时代成立、
分组后必须换算）。

真实计数：`test_ui_integration` 18 passed / 0 failed；`test_bookmark` 3 passed。

## 快照（cpp/tests/ui_screenshot_narrow.cpp，offscreen）

`MBD_BMKGROUP=1` 门控：向真实 BookmarkManager 注入 3 组 5 条（写入重定向的
throwaway QSettings，不碰用户持久数据），走真实 `refreshScanBookmarksUi()`
刷新表。`MBD_TAB=扫描/书签` 切到书签页。产物在 scratch/phase63/：

- bmgroup_640.png：请求 640 宽 → 实际渲染 764。生产地板 `kMainMinW=960`
  （tokens.h:219）会钳制 resize；harness 新增逃生口——**仅当显式 MBD_W 低于
  地板**时 `setMinimumSize(0,0)`（截图 harness 专用，生产地板不动）。764 已是
  布局协商出的内容最小宽：低于生产地板后左右栏被水平裁切（分区行顺序仍正确，
  文字被栏宽截断）——这正是生产地板存在的理由，快照如实记录。
- bmgroup_960.png：生产最小宽。分区头 `默认 (1)`/`AIR (2)`/`VHF (2)` + 数据行
  完整可读，0 裁切（分区头文字）、0 叠字；右栏窄导致 5 列中后列被栏宽裁切，
  属既有窄栏行为，非本期引入。
- bmgroup_1920.png：全宽。同结构、列更宽，分区头底色（kCard1 微光）与数据行
  层次分明，0 裁切 0 叠字。

## 候选判定（如实记录：哪些不是缺口，本期不做）

- **瀑布深度 UI 配置**：`kWaterfallHistoryLines=256` 已是 token
  （tokens.h:496），`SpectrumDisplay` 启动即取
  （spectrum_display.cpp:202）。深度本身有配置源，只是不暴露到设置面板——
  这是"未暴露"而非"无实现"，本期不动。
- **瀑布 y 轴 freeze / auto-range**：dB 轴 auto-range 已实现
  （spectrum_display.cpp:364-492，`autoRangeOn_` 按真实滑窗峰值自动刻度），
  上一期又补了时间轴秒级刻度（8d8642c）。y 轴纵览无缺口，本期不做。
- **QTreeWidget 化 / 拖拽重组分组**：见上"方案冻结"——需求只是分区可视，
  不引入控件替换与拖拽体系。
- **持久化分组排序/收藏**：`group` 字符串本身已随书签 JSON 持久化；排序由
  `(group, freq)` 全序天然给出，无需额外字段。

## 改动文件清单

- cpp/src/ui/main_window.h:727（`bmStoreIndexAtVisualRow` 声明）
- cpp/src/ui/main_window.cpp:3335-3424（`refreshBmTable` 分区重写 + 换算函数）、
  1608/1618/1644（编辑/删除/双击经换算）
- cpp/tests/test_ui_integration.cpp（新槽 + 既有槽双击行换算修正）
- cpp/tests/ui_screenshot_narrow.cpp（`MBD_BMKGROUP` 注入门控 + 低宽逃生口）
- docs/learn/phase63/bookmark-group-view.md（本文）
