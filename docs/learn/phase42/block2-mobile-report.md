# Phase42 块2 · mobile 域（纯 Dart + 测试）交付报告

> 生成：2026-10-05。范围：open-items E 类 G2–G5。红线：只写 mobile/lib/ + mobile/test/ + 本目录；不碰 cpp/（A 域，只读对齐）。

## 基线与终态
- 基线 `flutter test`：**351 passed**；`flutter analyze`：**No issues found**。
- 终态 `flutter test`：**362 passed**（351 + 新增 11）；`flutter analyze`：**No issues found (0)**。
- 未 commit / 未 push；未 `git add -A`（仅暂存本块 mobile 文件）。

---

## 逐项判定

### G2 录制回放接真 —— 判定：**布线已完整，补可见性/启用逻辑测试**
- 现状确认（只读）：
  - `mobile/lib/app/home_shell.dart:177-266` —— `RecordingStore` + `FilePlayer` 已注入；
    `onPlay` 走 `store.recordingsDir()` + `player.startFile(...)`，`onStop` 走 `player.stopFile()`，
    播完经 `player.onState`（`completed`）清 `_playing`；任一缺失（导航单测）→ onPlay/onStop=null 诚实空态。
  - `mobile/lib/pages/recordings_page.dart:209-229` —— `canPlay = onPlay!=null && onStop!=null && wavFileName`；
    `isPlaying` 按 `startedAtEpochMs + frequencyHz` 切 play/stop 图标；未注入时整行 onTap=null（不假按钮）。
- 补做：无代码改动（布线已就绪）。新增 3 条测试（`mobile/test/recordings_page_test.dart:160-236`）：
  1. 注入回调 + wavFileName → play_arrow 图标，点按回调 onPlay；
  2. playing 指向本条 → stop_circle_outlined，点按回调 onStop；
  3. 有 wavFileName 但未注入回调 → 不渲染假播放按钮、ListTile.onTap=null。

### G3 双游标测频差 —— 判定：**补只读双 marker 叠加（画布叠加，不动 DSP）**
- 桌面协议（只读对齐 `cpp/src/ui/spectrum_widget.cpp:123-142`）：`placeCursorA/B` 放视窗中心、可拖、显示 A/B 频差。
- 移动实现（纯 Dart 画布）：
  - `mobile/lib/widgets/spectrum_display.dart`：新增只读 `cursorAHz/cursorBHz`（可空）；
    `_SpectrumPainter` 在扫宽内画 A=绿(success)/B=品红 竖线 + 顶部 A/B 标签；两线同框画「Δ xx」读数盒。
    新增纯函数 `formatCursorDiff()`（Hz/kHz/MHz 分档）。**不调谐、不改 DSP、不持久化**。
  - `mobile/lib/pages/spectrum_page.dart`：`_SpectrumPageState` 持 `_cursorAHz/_cursorBHz`（本地态，不持久化）；
    控制面板加「测频差游标」行（A / B / 清除三键，未连接禁用，触控≥44 IconButton）。
- 测试（`mobile/test/cursor_calipers_test.dart`，3 条）：formatCursorDiff 分档；
  带 A/B 渲染不抛异常；点 A/B/清除全程 `setFrequencyHz` 调用 0 次（证明只读不调 VFO）。

### G4 扫频面板复核 —— 判定：**四项本就具备，复核通过；另修一处窄面板溢出**
- 四项核对：
  - 开始：`spectrum_page.dart:_promptScan` → `controller.startScan(...)`（真实调谐+真实电平量测）。
  - 停止：扫描中 LinearProgressIndicator + 「停止」→ `controller.stopScan()`（`radio_controller.dart:674` 原子标志提前退出）。
  - 命中列表：命中经 `onSignalActivity(source:'scan')` → 活动日志，`activity_log.dart:42` sourceLabel='扫描'。
  - 书签跳频：书签 InputChip onPressed → `setFrequencyHz`+`setMode`（既有 `bookmark_tap_test.dart` 覆盖）。
- 补做：
  - 新增 `mobile/test/radio_scan_panel_test.dart`（2 条）：扫描中渲染进度+停止且点停止真实调 stopScan；
    未扫描显示「范围扫描」入口、点开始弹参数对话框。
  - 顺带修复**既存隐患**：扫描中状态行 `Text('扫描中 … MHz · N%')` 在窄面板（宽屏布局 240px 面板宽）
    横向溢出 160px（仅 scanning=true 触发，基线测试未暴露）。已包 `Flexible`+ellipsis（`spectrum_page.dart:930`）。

### G5 AI 工具补面 —— 判定：**加只读工具清单页（35 工具，名称/说明/只读或写）**
- 桌面 35 工具清单：`cpp/src/ai/tool_schema.cpp:registeredToolSpecs()`（只读对齐，未改 cpp）。
- 实现：
  - `mobile/lib/app/tool_catalog.dart`：`kDesktopToolCatalog`（35 条 name/description/write 只读快照）+
    `kMobileImplementedToolNames`（与移动端 buildRadioTools 同名子集 = set_mode/start_recording/stop_recording/get_status/predict_passes）。
  - `mobile/lib/pages/tools_catalog_page.dart`：只读清单页；每行 名称(mono)/说明/read|write 图标；
    同名者打「已接入」，其余标 read/write。顶部诚实说明：这是桌面参考目录，不冒充移动端已有能力。
  - 入口：`chat_page.dart` 会话栏加 `build_circle_outlined` → push 本页（不发请求、不构造 AiTool）。
- 测试（`mobile/test/tools_catalog_test.dart`，3 条）：恰 35 条/名唯一/说明非空；已接入子集为目录真名；页渲染+已接入标记不冒充。

---

## 文件清单（本块新增/修改，均在 mobile/）
修改：
- `mobile/lib/pages/chat_page.dart`（G5 入口 + import）
- `mobile/lib/pages/spectrum_page.dart`（G3 游标状态/按钮/接线；G4 扫描行 Flexible 修溢出）
- `mobile/lib/widgets/spectrum_display.dart`（G3 cursorAHz/cursorBHz + painter + formatCursorDiff）
- `mobile/test/recordings_page_test.dart`（G2 +3）
新增：
- `mobile/lib/app/tool_catalog.dart`、`mobile/lib/pages/tools_catalog_page.dart`
- `mobile/test/cursor_calipers_test.dart`、`mobile/test/radio_scan_panel_test.dart`、`mobile/test/tools_catalog_test.dart`

## 未解决项 / 诚实说明
- G3 游标为**放置式只读测量**（点 A/B 落视窗中心 + 清除），未做桌面端的「拖拽游标连续移动」——
  移动端画布手势已被「点按调 VFO / 拖固定标记」占用，为避免误触调谐，本期只做放置+Δf 读数（符合任务「只读双 marker 显示」）。
- G4 仅复核任务列的四项（开始/停止/命中列表/书签跳频）；open-items E1 提到的「方向·暂停·命中停留·只扫书签·命中存书签」
  属更完整扫描器增强，不在本块范围，仍待 Wave2 排期。
- G5 为**只读参考目录**，未把桌面 30 个桌面独有工具接成可调用 AiTool（G5 任务明确为只读清单入口）；
  移动端实际可调用工具仍为 buildRadioTools 的 8 个。
- cpp/ 下若干 `M`（llm_worker/s_meter/spectrum_widget/tokens）为本会话前既有工作区状态，本块**未触碰**（仅 Read 对齐）。
