# Phase63 · 干净室机制深化：三候选核实与接收链路四态徽章落地

- 仓库 HEAD：`f14cade`；全程 CLI/offscreen（`QT_QPA_PLATFORM=offscreen`，
  `LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib`），未操控 GUI；只接真实数据源、诚实空态、零 mock。
- 红线：未 `git add/commit/push`；未动未跟踪隔离文件 `cpp/tests/ui_diag_freeze.cpp`；
  mobile/ 改动属并行 agent，本会话只改 cpp + phase31 文档。

## 一、三候选判定表

| 候选 | 判定 | 证据（file:line） |
|---|---|---|
| 1. 游标 Δ 精度收口（统一走具名格式化） | **非缺口 · 保留并记录** | 现状 Δ 读数 `spectrum_display.cpp:936-938` 固定 `f,3 MHz / f,2 kHz`，无 Hz 分支；共享具名格式化器 `status_format.h:35-40 formatFrequencyAutoHz`（MHz/kHz/Hz 三段、默认精度去尾零、`hz<=0 → "--"`）。**统一会改读数**：① 边界取整 dHz=999 时现状 `0.999,f2="1.00 kHz"` vs 具名 `"999 Hz"`；② 现状无 Hz 分支，dHz=500 显示 `"0.50 kHz"`，具名会变 `"500 Hz"`；③ Δ=0（两游标重合，是**真实**读数）会被具名器误判 `"--"`。固定精度还能让拖拽中 Δ 框宽度稳定。故保留现状，不改数值语义。 |
| 2. view/specFraction 产品侧默认值 | **非缺口 · 已完整** | 默认 `tokens.h:554 kDefaultSpecFraction=0.5`、键 `:555`；读取 `spectrum_display.cpp:61-68 loadSpecFraction()`：缺键（QVariant 无效→toDouble ok=false）与非有限值 `!std::isfinite(d)` 均诚实回退默认，再 `clampd(d,0.1,0.9)`（`:49-50 kSpecFracMin/Max`）；写侧 `:1399` 持久化的 `traceShare_` 已由 `:1300 ui::clampTraceHeight` 钳在合法带。测试 `test_spectrum_display.cpp:530-538`（缺键→默认）、`:727-748`（`"bogus-share"`→默认；`5.0`→钳到 [0.1,0.9]）、`:691` 往返持久化。 |
| 3. 接收链路状态机四态展示 | **真缺口 · 已最小落地** | 此前状态被摊到三个自由文本控件：连接按钮「连接/连接中…/断开」`main_window.cpp:317`、sourceBanner_ `:267`、statusLabel_ `:195`。**connecting 期间只有按钮变「连接中…」**（`:2664`），sourceBanner_/statusLabel_ 仍显示上一帧陈旧文本；无单一 Idle/Connecting/Running/Error 徽章。真实信号齐备可驱动：`sourceChanged/ sourceDropped/ sourceError/ sourceTelemetry`（接线 `:2178-2202`；引擎发射 `spectrum_engine.cpp:430/437/450/464/474`）。 |

## 二、落地（候选 3）

一个**单一来源**的四态徽章，由真实引擎信号/真实点击驱动，不猜测状态：

- 枚举 `ConnState{Idle,Connecting,Running,Error}` + 成员 `connStateBadge_` / `connState_`：
  `main_window.h`（枚举紧邻 `hotplugDropped_`，徽章指针紧邻 `connectBtn_`）。
- 徽章控件：`main_window.cpp` gSrc「源与连接」组内，`connectBtn_` 之后、`rssiLabel_` 之前；
  `objectName="connStateBadge"`，wordWrap，初始 = 诚实 idle。
- 唯一设值点 `MainWindow::setConnState()`（`main_window.cpp` onSourceChanged 之前）：
  几何/字号走 QSS `QLabel#connStateBadge`（`tokens.h` buildDarkQss，复用 `%fontAux%/%radSmall%/%padMV%`）；
  四态配色全部用具名 token——Idle=`kSelectedFill`+`kTextSecondary`、Connecting=`kWarning`、
  Running=`kSuccess`、Error=`kDanger`，前景 `kTextPrimary`。无裸色/裸字号。
- 真实驱动接线：
  - 点击连接 `main_window.cpp:2671` 后 → `Connecting`（与既有「连接中…」同一点，先 singleShot(0) 前已绘制）；
  - `onSourceChanged(connected=true)` → `Running,<name>`；合成/空闲源 → `Idle`（来源由既有合成 pill 负责）；
  - `onSourceError(msg)` → `Error,<真实 reason>`；`onSourceDropped()` → `Error,设备断开`；
  - **诚实保留**：失败后的 fallback `sourceChanged(false)` 因既有 `connectErrorShown_/hotplugDropped_` 提前 return，不会把 Error 刷回 Idle（与 banner 同一套旗标）。

### 测试真实计数
- 扩既有 `test_ui_integration`（未新增 CMake 目标）：新增
  `receiveLinkBadgeFourStatesDrivenByRealSignals()`，经 `harnessSourceChanged/SourceError/SourceDropped`
  转发到**同一真实槽**。断言：boot=空闲、Running 带真名、Error 带真实 reason、
  fallback `sourceChanged(false)` 保留 Error、重连回 Running、断开回 Idle、合成源不冒充 Running。
- 运行结果：`Totals: 20 passed, 0 failed`（落地前 19 → 现 20，新增 1 个真实用例）。

### 快照结论（env 门控复用 ui_screenshot_narrow.cpp，MBD_CONNSTATE 通道）
- 新增 `MBD_CONNSTATE=running/error/dropped/connecting` 通道，在 grab 前注入（避免引擎 run() 头
  `sourceChanged` 覆盖）；off 默认即诚实 boot idle。
- 640（被 `kMainMinW=960` 钳制，实得 764×997）/ 960 / 1920 各拍 idle：960、1920 徽章无裁切无叠字；
  764 窄轨下徽章与同级标签（RSSI/sourceBanner）同被导轨右缘裁切，属既有窄轨行为，不新增叠字。
- 另拍 960 的 running（绿 pill「已连接 · RTL-SDR」+ 按钮翻「断开」）、error（红 pill「错误 · 连接被拒绝 (refused)」）、
  connecting（琥珀 pill「连接中…」）逐图 Read 核查，四态配色/文本一致、无裁切叠字。
- 落盘 `docs/learn/phase63/connstate-{idle,running,error,connecting}-*.png`（PNG 仅落盘，按约定不入 git）。

## 三、phase31 工具文档同源重生成（45 → 47）

- 产物 `docs/learn/phase31/agent-tool-documentation.md` 本是 cpp 侧 `ai::generateToolDocumentation()`
  （`tool_schema.cpp:959`）的输出快照，头部停在「45 个工具」。
- 生成方式（一次性小程序，未加 CMake/ctest）：`cpp/scratch/regen_tool_doc.cpp`，
  `#include "ai/tool_schema.h"`，直接调 `mbdsdr::ai::generateToolDocumentation()` 写盘；
  以 `g++` 链接已构建的 `cpp/build/libmbdsdr_core.a` + Qt6Core。
- 结果：头部 `45 → 47 个工具`，正文 `grep -c '^## ' = 47`；diff 仅①计数行②在 `get_squelch_status`
  之后插入 `set_noise_blanker`(write) 与 `get_noise_blanker_status`(read) 两块，字段/格式与旧文件逐行一致。
- 另一并行 agent 只改 mobile/ 不碰 cpp/build，本会话独占 cpp 增量构建。

## 四、改动文件清单（cpp 职责内）
- `cpp/src/core/tokens.h`：`QLabel#connStateBadge` QSS 规则（+8）。
- `cpp/src/ui/main_window.h`：枚举/成员/`setConnState`/harness 访问与转发（+24）。
- `cpp/src/ui/main_window.cpp`：徽章创建、`setConnState()`、四处信号接线（+62）。
- `cpp/tests/test_ui_integration.cpp`：新增四态用例（+58）。
- `cpp/tests/ui_screenshot_narrow.cpp`：`MBD_CONNSTATE` 通道（+16）。
- `docs/learn/phase31/agent-tool-documentation.md`：重生成 45→47（+12）。
- `cpp/scratch/regen_tool_doc{.cpp,}`：一次性重生成工具（scratch，不入版本）。

## 五、红线自查与未完成项
- 红线：无 `git add/commit/push`；未动 `ui_diag_freeze.cpp`；无「比赛/competition」字样；GPL 中立（项目 MIT）；
  活动参数只进 docs；颜色/字号/几何全走 `tokens::` 具名值与 `tokens::scaled()`/QSS。
- 诚实未完成：connecting 态的 offscreen 实测依赖阻塞 socket，无法在线保持，故 4 态中 connecting 仅快照佐证、
  未进 QTest 断言（其渲染复用同一 pill/token 路径）；764 钳宽下徽章右缘裁切为既有窄轨行为，未单独加宽导轨。
