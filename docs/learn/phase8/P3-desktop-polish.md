# P3 桌面体验打磨（phase8）

> 验收对照：**超越 SDR++ / GNU Radio / SatDump**。本轮只做"真实现、云内可验"的部分：
> ① 快捷键一致性 + 新增高频快捷键 + 纯映射逻辑测试；② 状态栏格式化抽纯函数 + 测试。
> 频谱交互手感（候选 1）在上一轮已落地并被 `test_spectrum_interaction` 覆盖，本轮**不重造**，仅复核（见末节）。
>
> 验证基线：`QT_QPA_PLATFORM=offscreen ctest` = **93/93 全绿**（原 91 基线不破 + 新增 `shortcut_catalog`、`status_format` 2 项；重写 `shortcuts` 由空壳变为 5 用例真断言）。
> 红线：只写 `cpp/`、`docs/learn/phase8/`；CMake 末尾追加，未改既有测试注册；MIT 干净室；未 commit/push。

---

## 项 1：快捷键覆盖与一致性（含一次真实手感 bug 修复）

### 现状（改前 file:line）

| 问题 | 位置 |
|---|---|
| 对话框硬编码 5 行，与实际接线脱节 | `cpp/src/ui/shortcuts_dialog.cpp:16-22`（旧） |
| 实际接线 12 处 QShortcut，对话框漏报 **Ctrl+Tab / Ctrl+Shift+Tab / Ctrl+1..9**（VFO 切换） | `cpp/src/ui/main_window.cpp:2406-2466`（旧） |
| `test_shortcuts.cpp` 是空壳：只 `QVERIFY(true)`，从未真正断言接线 | `cpp/tests/test_shortcuts.cpp`（旧） |
| 箭头调频以 `freqSpin_->value()` 为基准——但 spinbox 只被频谱拖拽路径回写，**不被箭头快捷键回写**：按两次 → 再按两次 ← 回不到原点 | `cpp/src/ui/main_window.cpp:2407-2417`（旧） |

### 上游做法（GPL/其他协议，只学机制）

- **SDR++** `repos/sdrpp/core/src/gui/main_window.cpp:580-597`：滚轮在有选中 VFO 时按 `snapInterval` 步进调谐，Shift ×10 / Alt ×0.1；无 VFO 时才是视图缩放。我们上一轮已把"plain wheel 步进 / Ctrl+wheel 缩放"对齐（`test_spectrum_interaction.cpp:67-85`）。
- **GQRX**（Qt/GPLv3，机制参考）`repos/gqrx/src/qtgui/dockaudio.cpp:71-72`：`Key_Plus / Key_Minus` 步进增益；同文件 :69-70 `Key_R` 录音、`Key_M` 静音。

### 实现

1. **单一事实源目录**：新增 header-only `cpp/src/ui/shortcuts_catalog.h`。
   - `shortcutCatalog()`：9 行声明（键 + 中文说明），对话框与接线共用。
   - 纯函数：`nudgeFreqHz()`、`nudgeBandwidthHz()`、`cycleStepIndex()`（wrap）、`stepGainDb()`（离散表挑最近合法档 / 空表 ±2dB 夹 [0,50]）。
2. **对话框改为渲染目录**：`cpp/src/ui/shortcuts_dialog.cpp` 全部行渲染自 `shortcutCatalog()`，从此不可能再漂移；VFO 切换快捷键首次出现在对话框里。
3. **箭头调频手感修复**：`cpp/src/ui/main_window.cpp:2405-2430` 抽 `retuneNudge(deltaHz)`——以 `engine_->centerFreq()`（真实调谐读数）为基准步进，并同步回写 `freqSpin_`。现在 →←↔ 严格对称，Shift 细调 ±step/10。
4. **新增高频快捷键**：`cpp/src/ui/main_window.cpp:2481-2518`
   - `+ / -`：增益加/减一档（GQRX 约定）。离散 RTL 表 → `stepGainDb()` 挑下一/上一合法档；空表（rtl_tcp/离线/文件源）→ 连续滑条 ±2dB。
   - `PgUp / PgDown`：调频步进档位增大/减小，`cycleStepIndex()` 带 wrap。
5. **测试访问器**：`main_window.h` 声明、`main_window.cpp:3876-3882` 定义 `harnessStepIndex()` / `harnessGainDb()`（沿用既有 harness 模式，只读、不开设备）。

### 确定性测试

- `cpp/tests/test_shortcut_catalog.cpp`（新，9 断言）：目录非空/无重复键；`nudgeFreqHz` 细调=step/10；`nudgeBandwidthHz` 上下钳位；`cycleStepIndex` wrap（含越界输入、空表）；`stepGainDb` 连续夹顶/底 + 离散乱序表挑档/钉在端点。
- `cpp/tests/test_shortcuts.cpp`（重写，5 用例真断言）：offscreen 下真发 QKeyEvent——
  - → 抬高中心频率，← 严格回退（对称修复的回归断言）；
  - Shift+→ 细调 <200kHz 且可回退；
  - PgUp 步进档位 +1、再 +1；PgDown wrap -1；
  - `+` 增益滑条 +2、再 +4。
  - 关键环境细节：offscreen 下 `WindowShortcut` 只在窗口真正 active 时派发，且引擎测试源安装是异步的——`arm()` 先泵事件等 `centerFreq()>1MHz`、再最后 `activateWindow/setFocus`（`tests/test_shortcuts.cpp:17-27`），消除启动时序抖动。

---

## 项 2：状态栏信息密度——格式化抽纯函数

### 现状（改前 file:line）

遥测/连接/声卡/增益/VFO 的文本是内联在槽函数里的 `QString.arg()` 串，只能靠完整 offscreen MainWindow 间接碰到：

- `cpp/src/ui/main_window.cpp:3662-3672`（旧 `onSourceTelemetry`）：采样率/VFO/增益/源名四个 `.arg()` 串；
- `:3632-3633`（旧 `onRssiLevel`）、`:3652-3653`（旧 `onSnrLevel`）；
- `:3863-3866`（旧 `onSquelchState`）。

### 上游做法

SDR++（ImGui）状态条同样内联拼装；GNU Radio Companion 无对应物。此项的价值不在"学上游"，而在把**诚实空态**（`--` / `（非硬件）` / `静噪 OFF`）从 UI 槽里抽成可单测的纯函数。

### 实现

- 新增 header-only `cpp/src/ui/status_format.h`：`fmtStripSampleRate / fmtStripVfoFreq / fmtStripGain / fmtStripSource / fmtStripSquelch / fmtStripRssi / fmtStripSnr`，全部无 Qt 控件状态；NaN/非正值 → 诚实 `--`；未连接源自动追加 `（非硬件）`。
- 接线替换：`main_window.cpp:3716-3721`（遥测）、`:3686`（RSSI）、`:3702` 附近（SNR）、`:3922`（静噪）改为调用纯函数，显示文本与改前逐字节一致。

### 确定性测试

- `cpp/tests/test_status_format.cpp`（新，7 用例）：2.4e6→`2.400 MS/s`、98.5e6→`98.500 MHz`、0/负→`--`、`增益 25.0 dB`、`RTL0` vs `Test Signal（非硬件）`、静噪 OFF/OPEN/CLOSED 三态、RSSI/SNR 的 NaN→`--`。

---

## CMake

仅在 `cpp/CMakeLists.txt` **末尾追加**两个测试目标（`shortcut_catalog`、`status_format`，均 header-only、只链 Qt6::Test/Core/Gui）；既有 91 个测试注册一行未动。

## 测试结果

```
QT_QPA_PLATFORM=offscreen ctest
100% tests passed, 0 tests failed out of 93
```

（91 基线 +2 新增；`shortcuts` 由空壳变为 5 真用例。无 offscreen 环境时 xcb 会因缺 libxcb-cursor0 报"no Qt platform plugin"——必须带 offscreen 跑。）

## 未做项（诚实披露）

- **瀑布余晖时间滚轮调节**：候选 1 提到的"滚轮调余晖"未做。现有交互（拖拽调频 / Ctrl+wheel 绕光标缩放 / plain wheel 步进 / 条带拖拽平移 / 双击居中 / 分隔条拖拽）已在 `test_spectrum_interaction.cpp` 5 用例契约内，为避免破坏既有交互契约，本轮不加新滚轮手势。
- **水印/瀑布滚动流畅性、多窗口面板布局**：未动。
- **快捷键不抢输入框焦点**：既有箭头/Space 是 WindowShortcut 全局派发（历史行为），AI 输入框打字时也会触发——本轮保持一致、未改上下文策略，列入后续。

## 真机待验项（云内无硬件）

- `+/-` 在真实 RTL-SDR 离散增益表上的档位吸附手感（表逻辑已纯测）。
- 声卡真实出声 / 静音切换听感；`sbAudio_` 声卡健康状态在真实欠载场景下的文本。
- VFO 实战拖拽调谐、Ctrl+Tab 多 VFO 切换手感。
- 箭头调频在真实 rtl_tcp 源上的 LO 回边行为。
