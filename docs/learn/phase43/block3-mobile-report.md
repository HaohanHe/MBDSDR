# Phase43 块3 · mobile 域（扫频增强 Wave2）交付报告

> 生成：2026-10-05。范围：open-items 扫频 Wave2 —— 方向切换 / 暂停·恢复 / 命中停留时长配置。
> 红线：只写 `mobile/lib/` + `mobile/test/` + 本目录；不碰 `cpp/`（A 域，只读对齐）。

## 基线与终态
- 基线 `flutter test`：**362 passed**（Phase42 块2 终态）；`flutter analyze`：**No issues found**。
- 终态 `flutter test`：**366 passed**（362 + 新增 4）；`flutter analyze`：**No issues found (0)**。
- 未 commit / 未 push；未 `git add -A`（仅暂存本块 mobile 文件；README.md、cpp/ 既存改动本块未触碰）。

---

## 逐项落地

### 1) 方向切换（上行 / 下行扫频）
- 枚举：`mobile/lib/models/radio_state.dart:43` —— `enum ScanDirection { up, down }`，带中文 `label`（上行/下行）。
  桌面 `ScanDirection` 另有 `PingPong`（来回）；本块按任务「上行/下行」两档实现，不硬造来回回绕。
- 控制器：`mobile/lib/services/radio_controller.dart:670` ——
  `idx = direction == up ? i : (points - 1 - i)`：up 从 startHz 递增；down 从 endHz 递减。
  `startScan` 新增具名参 `ScanDirection direction = ScanDirection.up`（:656）。
- UI：`mobile/lib/pages/spectrum_page.dart:556` —— 扫描对话框内 `SegmentedButton<ScanDirection>`（上行/下行），
  本地 `dir` 状态经 `StatefulBuilder` 维护，开始时 `direction: dir`（:635）。

### 2) 暂停 / 恢复
- 接口：`mobile/lib/services/radio_controller.dart:133` `bool get scanPaused;`、
  `:154 pauseScan()`、`:157 resumeScan()`。
- 实现：`:728 pauseScan()` / `:737 resumeScan()` —— 置 `_scanPaused` + `Completer _scanResume` 冻结/唤醒；
  `:747 _scanFreezeGate()` 在点边界等待；`:754 _dwellInterruptible()` 把驻留切成
  `AppTokens.scanDwellSliceMs`(50ms) 小片逐片检查暂停/取消，使暂停**真正冻结驻留计时**而非等到下一点。
  `stopScan()`（:718 附近）同时 complete 暂停闸，避免循环悬挂。
- UI：`mobile/lib/pages/spectrum_page.dart:1008-1019` —— 扫描进行行加「暂停/恢复」`TextButton.icon`，
  按 `controller.scanPaused` 切图标（pause/play_arrow）与文案（暂停/恢复）；状态前缀切「已暂停」（:1000）。
  触控：`TextButton.icon` 与既有「停止」同档 Material 最小触控高（≥44，对齐 `AppTokens.touchMin`）。

### 3) 命中停留时长配置（AppTokens 弹性具名常量，禁裸数）
- 具名常量：`mobile/lib/app/tokens.dart`
  - `:253 scanDwellMsDefault = 300`（每步驻留默认）；
  - `:256 scanHitHoldMsDefault = 2000`（命中后默认停留）；
  - `:260 scanHitHoldMsOptions = [0,1000,2000,3000]`（命中停留可选项，0=立即继续）；
  - `:263 scanDwellSliceMs = 50`（可中断驻留切片）。
- 控制器：`startScan` 新增 `int hitHoldMs = 0`（:657）；命中后 `if (hitHoldMs > 0) await _dwellInterruptible(hitHoldMs)`（:699），
  该额外驻留同样可被暂停/取消打断。
- UI：`mobile/lib/pages/spectrum_page.dart:578-` —— 对话框内 `DropdownButton<int>` 命中停留下拉，
  选项遍历 `AppTokens.scanHitHoldMsOptions`（0 显示「立即继续」，否则「N s」），开始时 `hitHoldMs: holdMs`（:636）。
  原 `dwellMs: 300` 裸数改为 `AppTokens.scanDwellMsDefault`（:634 附近）。

---

## 测试（新增 4 条，均离线、云内可跑）
`mobile/test/radio_scan_test.dart`（真实 `RadioController` + 脚本化 `_FakeClient`，不建 Socket）：
1. **方向下行** —— `radio_scan_test.dart`「下行方向：从 endHz 向 startHz 递减调谐」：
   断言尾部 3 次真实调谐为 `[144100000,144050000,144000000]`（与默认 up 的递增序相反）。
2. **暂停/恢复** —— 「暂停：冻结调谐；恢复：从暂停处继续」：
   `pauseScan()` 后 200ms 内调谐计数**不变**（冻结）；`resumeScan()` 后 200ms 计数**增长**（继续）；
   结束复位 `scanning/scanPaused`。
3. **命中停留** —— 「命中停留：命中频点额外驻留 hitHoldMs，总耗时显著变长」：
   门限压到极低使每点都命中；同 2 点下 `hitHoldMs=200` 总耗时显著大于 `hitHoldMs=0`（带裕量断言）。
`mobile/test/radio_scan_panel_test.dart`（UI）：
4. **暂停/恢复按钮** —— 「『暂停/恢复』按钮随 scanPaused 切换」：
   点暂停→`scanPaused=true`、文案变「恢复」+ 状态「已暂停」；点恢复→回「暂停」。

接口扩展连带更新的 fake（补 `scanPaused/pauseScan/resumeScan` + `startScan` 两新参）：
`agc_switch_test.dart`、`bookmark_tap_test.dart`、`cursor_calipers_test.dart`、
`radio_recording_test.dart`、`spectrum_display_test.dart`（两处）、`spectrum_panel_audit_test.dart`。

---

## 桌面对齐说明（只读，未改 cpp/）
桌面扫频引擎 `cpp/src/dsp/frequency_scanner.{h,cpp}` **本就具备**方向 / 暂停 / 命中停留能力，本块为移动端对齐实现：

- **方向**：桌面 `enum ScanDirection { Up, Down, PingPong }`（`cpp/src/dsp/frequency_scanner.h:33`）；
  起点选择见 `frequency_scanner.cpp:72-81`（Down 从末项出发、`dirDelta_=-1`）；
  UI 接线 `cpp/src/ui/main_window.cpp:1288-1290`（方向下拉「向上/向下/来回」）、`:1500-1502`（写入 cfg.direction）。
  移动端对齐 Up/Down 两档（任务范围），未实现 PingPong 来回。
- **暂停/恢复**：桌面 `FrequencyScanner.pause()`/`resume()`（`frequency_scanner.cpp:88-97`，冻结一切计时、不调谐）；
  UI 按钮 `main_window.cpp:1326-1327`、`:1519-1526`（按 Scanning/Hit→pause，Paused→resume）。
  移动端 `pauseScan/resumeScan` 语义对齐：冻结当前频点调谐与驻留计时。
- **命中停留**：桌面 `enum HitHoldMode { UntilSignalGone, FixedMs }`（`frequency_scanner.h:35`），
  `holdMs=2000`（FixedMs 固定停留，`:56`）、`lingerMs=1000`（信号消失延时，`:55`）；
  命中后停留计时见 `frequency_scanner.cpp:192-196`。UI `main_window.cpp:1292-1306`（命中停留下拉 + 固定停留 spinbox）。
  移动端对齐 **FixedMs 固定停留**语义：命中后在该频点额外驻留 `hitHoldMs`，默认 2000ms（`scanHitHoldMsDefault`）。
  未实现 `UntilSignalGone`（信号消失延时 lingerMs）—— 任务范围只要求「命中停留时长配置」。
- **每步驻留**：桌面 `dwellMs=300`（`frequency_scanner.h:50`）；移动端 `scanDwellMsDefault=300` 同值对齐。

> 结论：桌面方向/暂停/命中停留均已具备，移动端按上述 file:line 语义对齐，**不硬造差异**；
> 桌面独有项（PingPong 来回、UntilSignalGone 消失延时、只扫书签、命中存书签）本块未实现，见下。

---

## 文件清单（本块新增/修改，均在 mobile/）
修改：
- `mobile/lib/models/radio_state.dart`（`ScanDirection` 枚举）
- `mobile/lib/app/tokens.dart`（扫频具名常量）
- `mobile/lib/services/radio_controller.dart`（接口+实现：方向/暂停/命中停留/可中断驻留）
- `mobile/lib/pages/spectrum_page.dart`（对话框方向+命中停留；扫描行暂停/恢复按钮）
- `mobile/test/radio_scan_test.dart`（+3 行为测试）、`mobile/test/radio_scan_panel_test.dart`（+1 UI 测试）
- 连带 fake 更新：`agc_switch_test.dart`、`bookmark_tap_test.dart`、`cursor_calipers_test.dart`、
  `radio_recording_test.dart`、`spectrum_display_test.dart`、`spectrum_panel_audit_test.dart`

## 未解决项 / 诚实说明
- **PingPong（来回）方向**：桌面有，移动端本期按任务只做 Up/Down；未做来回回绕。
- **UntilSignalGone（信号消失延时 lingerMs）**：桌面有，移动端只对齐 FixedMs 固定停留；未实现「直到信号消失再走」。
- **只扫书签 / 命中存书签**：open-items Wave2 列过这两项，但本块交付范围（方向/暂停/命中停留）未含，仍待后续排期。
- 命中停留为 **FixedMs 固定时长**（命中后不论信号是否仍在都停满 hitHoldMs 再走），与桌面 FixedMs 分支一致；
  未做桌面 SignalWatch 快攻慢释门控的平滑复现（移动端命中判定仍复用现有静噪门真实 RMS 电平）。
- `stepHz: 25000` 与对话框 ±0.5MHz 半窗为 Phase42 既有裸数，本块未在范围内重构（仅把本次新增的 dwell/hold 走 AppTokens）。
- cpp/ 下既存未跟踪构建目录（build_*/scratch/record）为本会话前工作区状态，本块**未触碰**（仅 Read 对齐）。
