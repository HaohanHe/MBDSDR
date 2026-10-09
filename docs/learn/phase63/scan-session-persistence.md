# Phase 63 · 扫频会话持久化（scan-session-persistence）

HEAD 落地：`b1812c2`。全程 offscreen（`QT_QPA_PLATFORM=offscreen`，
`LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），未操控真实 GUI。
只接真实数据源：保存的命中来自真实 `FrequencyScanner::hits()`；安静频带如实存 0 命中。
无 mock、无内置演示电台。

## 1. 三候选判定表

| 候选 | 判定 | 证据（file:line） |
|---|---|---|
| ① 扫频会话保存/加载 | **真缺口 → 已落地** | 扫描参数控件齐全：`main_window.cpp:1389-1447`（起始/终止/步进/驻留/门限/方向/命中停留/消失延时/固定停留/只扫书签）；命中→书签一次性按钮：`main_window.cpp:1471-1474`，处理器 `:1660-1674`（仅命中态存当前命中为书签）；但全仓库 grep `saveSession/loadSession/.mbdscan/scan session` **为空**——参数快照+命中列表无落盘/恢复。命中数据已由 `dsp::FrequencyScanner::hits()` 累积（`frequency_scanner.h:88`，`ScanHit{freqHz,levelDb}` `:40-43`），缺的只是把它连同参数序列化。 |
| ② 带宽/滤波器直选 | **非缺口** | 带宽预设直选已有：`main_window.cpp:599-609`（`bwCombo_` 由共享表 `kBwComboPresetsHz` `:148-150` 构建，顺序不漂移）；模式→带宽联动 `:2396`。滤波器种类直选：SDR++ 亦以带宽选择为主、种类非标配 UI 面，对照 `docs/learn/sdrpp.md`。不补。 |
| ③ 瀑布深度指示 | **评估，本轮不落地** | 深度行数直选已有并持久化：`spectrum_widget.cpp:459-480`（`depthCombo_`，写 `QSettings(kSettingsKeyWfDepth)`）。状态栏无"深度/秒数"读数——秒数需采样率换算、价值低；行数 combo 本身可见。记为后续方案，本轮不落地。 |

## 2. 落地机制

新增纯逻辑层（数据 + JSON 编解码，无 QWidget、无 radio），镜像既有 `bookmark_manager`
（数据落 `mbdsdr_core`，可被无 GUI 单测链接）：

- `src/ui/scan_session.h` / `src/ui/scan_session.cpp`
  - `ui::ScanSession`：完整参数快照（startMHz/stopMHz/stepIndex/dwellMs/thresholdDb/
    dirIndex/holdIndex/lingerMs/holdMs/bmOnly）+ 接收态（mode/bwHz）+ 历史命中列表
    `QList<ScanSessionHit>{freqHz,levelDb,mode,bwHz}`。
  - `scanSessionToJson()` / `scanSessionFromJson()`：紧凑 JSON；**严格校验、绝不静默愈合**——
    非对象 / `kind` 不符 / 必填字段缺失或类型错误 / `hits` 非数组 / 命中条目类型错，
    一律返回 `false` 并填人类可读 `err`（调用方原样上屏）。
  - `scanSessionSaveFile()` / `scanSessionLoadFile()`：UTF-8（无 BOM）写/读，读失败即拒绝。
  - 磁盘格式：单 JSON 对象，扩展名 `.mbdscan`，`"kind":"scan-session"`。

MainWindow 接线（`频率扫描` 组内，零新 groupbox/tab）：

- 第二行按钮：`保存会话` / `加载会话`（`main_window.cpp:1488-1495`，
  objectName `scanSaveSessionBtn` / `scanLoadSessionBtn`）。
- 诚实摘要标签 `scanSessionLabel_`（`:1500-1503`）：初始为空（无加载=无声明）。
- 保存处理器 `:1788-1832`：快照同 `开始` 按钮读的同一批控件；命中来自
  `scanner_->hits()`（安静频带=0 命中）；文件对话框默认落到 `engine_->recordingDir()`，
  诚实命名 `scan-session-<yyyyMMdd-HHmmss>.mbdscan`；失败 `QMessageBox` 如实报错。
- 加载按钮 `:1834-1845` → `MainWindow::loadScanSessionFromFile()` `:3702-3746`：
  - 扫描非 Idle 时**诚实拒绝**（`:3707-3709`，"请先停止再加载"）；
  - 恢复 spin/combo/check 原值（越界索引安全跳过）；恢复解调模式 + 最近带宽预设；
  - 摘要如实写 "已加载会话：参数已恢复 · 历史命中 N 个（**非本次扫描结果**）"
    （`:3739-3744`）——加载命中**绝不注入** `scanner_`、绝不点亮实时"命中"徽章。

头文件成员：`main_window.h:330-332`；公开加载入口 `:134`（供快照/测试复用）。

## 3. 测试（扩展既有 test_scanner，未新增 CMake 目标）

`tests/test_scanner.cpp`（链接 `mbdsdr_core` + `Qt6::Test/Core`，offscreen）新增两槽：

- `scanSessionRoundTrip()`：非默认参数 + 2 命中 → 写真实 `.mbdscan`（QTemporaryDir）
  → 读回，逐字段全等（含每命中 freq/level/mode/bw）；另测 0 命中安静会话 round-trip。
- `scanSessionRejectsCorrupt()`：6 组损坏输入（非 JSON / 顶层数组 / 错 kind /
  数字字段被手改成字符串 / `hits` 非数组 / 磁盘文件不存在）全部 `false` 且 `err` 非空。

**offscreen 实测计数：`Totals: 11 passed, 0 failed, 0 skipped`**
（8 既有扫描状态机用例 + 2 新会话用例 + init/cleanup）。

## 4. UI 快照（ui_screenshot_narrow 门控，先例 MBD_BMKCOLLAPSE）

门控 `MBD_SCANSESSION=1` + `MBD_TAB=扫描/书签`（`tests/ui_screenshot_narrow.cpp`）：
写一份真实 `.mbdscan` 再经生产路径 `loadScanSessionFromFile` 加载。

- `scan-session-960.png` / `scan-session-1920.png`（`docs/learn/phase63/`）。
- 1920（右轨 420px）：参数全部正确恢复（起始 118 / 终止 137 / 步进 100 kHz / 驻留 400 ms /
  门限 -45 dBFS / 消失延时 1200 / 固定停留 2000）；`保存会话`/`加载会话` 并排完整可见；
  诚实标签 "历史命中 3 个（非本次扫描结果）" 完整可读；**0 裁切、0 叠字**。
- 960（右轨 198px）：会话按钮与标签同样可见可读。上部既有扫描组在极窄右轨的控件挤压
  与无门控基线逐像素一致——属既有窄轨渲染行为，**非本次改动引入**。

## 5. 改动文件清单

- 新增 `cpp/src/ui/scan_session.h`、`cpp/src/ui/scan_session.cpp`
- `cpp/src/ui/main_window.cpp`（按钮行/标签/两处理器/`loadScanSessionFromFile`）
- `cpp/src/ui/main_window.h`（成员 + 公开入口）
- `cpp/CMakeLists.txt`（core 源 `:90`、头 `:172`）
- `cpp/tests/test_scanner.cpp`（2 新槽 + include/namespace alias）
- `cpp/tests/ui_screenshot_narrow.cpp`（`MBD_SCANSESSION` 门控）
- 本 docs + 两张快照 PNG。

未跟踪隔离文件 `cpp/tests/ui_diag_freeze.cpp`、`cpp/scratch/*` 未动未入库。
未执行任何 git add/commit/push。
