# Phase63 书签组折叠/展开（在已落地的分组视图上补交互）

> 范围：在 phase63 已落地的书签分组视图（`bookmark-group-view.md`）之上，补
> **组 header 点击折叠/展开该组全部书签行**的交互。
> 全程 offscreen/CLI（`QT_QPA_PLATFORM=offscreen`），未操控 GUI；零 mock
> （数据全部来自真实 `BookmarkManager`）；干净室实现；不 git add/commit/push。

## 三候选侦察判定表

本次任务先核实三个候选是否为真缺口，再只落地真缺口。

| 候选 | 判定 | 证据（file:line） |
|---|---|---|
| 1. 书签按组折叠/展开 | **真缺口（本期落地）** | 分组显示已在：section header「组名 (N)」`main_window.cpp:3411-3431`、store 按 (group,freq) 预排序 `:3402`、视觉行→store 映射 `:bmStoreIndexAtVisualRow`（原 :3455）、诚实空态「暂无书签」`:3382-3392`、表单分组编辑 `:1587`。但 bmTable_ 只接了 `cellDoubleClicked`（双击调谐，原 :1658），**没有任何 `cellClicked`/折叠态成员**——点组 header 不收起该组行。 |
| 2. 瀑布/中心 固定/跟随 | **非缺口** | `SpectrumDisplay::followTunedFrequency` `spectrum_display.cpp:740-751` 调 `ui::followCenterAfterTune` `spectrum_tune.h:188-206`：VFO 越出视口 10% 边距才 pan 跟随，已在屏内则不动。已三处接线：键盘微调 `main_window.cpp:2956`、画布拖动 `spectrum_display.cpp:1602`、`followTunedFrequency` 自身。「拖动冻结基线」指 dB 自动量程：`setAutoRangeOn(false)` `spectrum_display.cpp:558-574` 在关闭自动时把当前屏上 floor/ceil 冻结为新手动基线（:561 注释原文）。 |
| 3. 峰值表点击调谐 | **非缺口** | `SpectrumWidget::peakTuned` 信号已接 `main_window.cpp:3087-3094`：点载波行把选中 VFO 经 `vfoSetOffset` 调到该真实峰值频率，与画布拖动同一条 in-band 路径。 |

结论：仅候选 1 为真缺口，本期落地候选 1。

## 机制：折叠 = 隐藏行，不删数据

分组视图本来就是「每个 group 先渲染一个 header 行，再渲染该组 N 个数据行」。
折叠交互只在这两层之间加一个开关：

- 新增折叠态成员 `QSet<QString> bmCollapsedGroups_`（`main_window.h`）。键是 group 的
  **原始存储串**（空默认组 `""`，渲染时显示成「默认」）。该集合在窗口生命周期内跨
  `refreshBmTable()` 持久（选「刷新后折叠态保持」而非诚实复位）——改组名/删空组后，
  残留键只是不再匹配任何渲染组，无害。
- section header 行加两个渲染约定：
  - 文本前缀 `▾ `（展开）/ `▸ `（折叠），如 `▾ VHF (2)` / `▸ VHF (2)`；
  - **`(N)` 永远是 store 里该组的真实总数**——折叠只是不渲染数据行，store 一行没删，
    所以计数不撒谎、不写「已收起」。
  - header 行在 `Qt::UserRole+1`（文件作用域枚举 `kBmGroupRole`）上带原始 group 串，
    供 toggle 反查属于哪个组（`Qt::UserRole` 仍是分区标记 -1，不动）。
- `refreshBmTable()` 遍历 `groups()` 时，若该组在折叠集合里：**只渲染 header 行，
  整体跳过该组数据行的 insertRow**。`storeIdx` 仍按真实 list() 顺序推进，所以幸存行
  的 `Qt::UserRole` store 索引永不漂移。
- 新增 `connect(bmTable_, &QTableWidget::cellClicked, ...)`：只对 col-0 `UserRole==-1` 的
  header 行生效，读 `kBmGroupRole` 拿到组串，在 `bmCollapsedGroups_` 里 toggle 一下再
  `refreshBmTable()`。数据行 / 空态行（UserRole≥0 / -2）直接 no-op。

### 诚实可见性优先：新增/编辑落入折叠组时自动展开

- 添加书签（`bmAddBtn_`、`scanSaveBmBtn_` 命中存书）后、`refreshBmTable()` 前，
  `bmCollapsedGroups_.remove(bm.group)` —— 新行必须在屏上可见，其他组的折叠态保持。
- 编辑改组（`bmEditBtn_`）后同样 `remove(bm.group)`，把书签迁到的目标组展开，
  保证改后那行看得见。
- 删除不特殊处理：删空的组从 `groups()` 消失，其残留折叠键无害。

## 现状核实（落地前 file:line）

- 分组 header：`main_window.cpp` `refreshBmTable()` 内「组名 (N)」`main_window.cpp:3411-3431`。
- store 预排序：`BookmarkManager::list()` 按 (group, freq)（`bookmark_manager.cpp`），
  渲染按 `groups()` → `byGroup(g)` 切片，storeIdx 0..N-1 对齐。
- 视觉→store 映射：`bmStoreIndexAtVisualRow()` 读 col-0 `Qt::UserRole`，header=-1 / 空态=-2 / 数据=store 下标。
- 既有交互：添加 `:1618`、编辑 `:1629`、删除 `:1644`、命中存书 `:1654`、双击调谐 `:1664`。
- 落地前 bmTable_ 唯一的行信号是 `cellDoubleClicked`，无 `cellClicked`、无折叠成员。

## 测试（扩既有 test_ui_integration，零新 CMake 目标）

新增槽 `bmGroupCollapseToggleHidesRowsKeepsCountAndNewRowVisible()`，断言：

1. 种子 3 组（默认1/AIR1/VHF2）→ 3 header + 4 数据 = 7 行；
2. 对 VHF header 发真实 `cellClicked` → 折叠：行数 7→5，header 翻成 `▸ VHF (2)`，
   **计数仍是真实 (2)**、store count 仍为 4（折叠不删数据），VHF 两行（store idx 2/3）
   从表上消失，默认/AIR 行不动；
3. 再点同一 header → 展开恢复 7 行、header 回 `▾`、两行 store 索引复原；
4. 再次折叠 VHF，然后走真实 `bmAddBtn_` 路径（modal 对话框自动填「分组=VHF」）：
   VHF 自动展开回 `▾ VHF (3)`、新行 on-screen 可见，默认/AIR header 不动。

既有 `bmGroupedViewSectionsEmptyStateAndIndexMapping` 的 header 精确断言同步更新为带
`▾ ` 前缀（展开态默认）。结果：`test_ui_integration` 23 passed / 0 failed；
`test_bookmark` 3 passed / 0 failed。

## 快照（ui_shot_narrow 门控，offscreen）

- 既有 `MBD_BMKGROUP=1` 种子 3 组真实书签；新增 `MBD_BMKCOLLAPSE=VHF` 门控：
  经真实 `cellClicked` 路径收起 VHF 组并 `scrollToBottom()` 把折叠头带进视口。
- 拍 960×900 / 1920×1000（`MBD_TAB=扫描/书签`），两档都清晰可见 `▸ VHF (2)`：
  右向三角=收起、计数保持真实总数。Read 核查：折叠行文字无叠字、无裁切
  （右侧「带宽」列头在窄窗下的截断是既有 5 列固定布局，非本次引入）。

## 纪律自查

- 真实数据源、诚实空态、零 mock；无新文案字符串进入 tokens（▸/▾ 是 UI 指示字形，
  组名/计数格式沿用既有）。
- 未动 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*`；未 git add/commit/push。
- 改动仅职责所需文件：`main_window.{h,cpp}`、`tests/test_ui_integration.cpp`、
  `tests/ui_screenshot_narrow.cpp`。
