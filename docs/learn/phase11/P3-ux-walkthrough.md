# P3 用户视角走查报告（phase11）

> 方法：以"第一次打开软件的新人，无任何说明书，5 分钟内能否从空屏走到一个可用调谐/解码/卫星视图"为脚本，逐屏走查**真实代码**（不是 mock）。
> 基线：HEAD `469e48e`。验证：`QT_QPA_PLATFORM=offscreen ctest` = **95/95 全绿**（基线 94 + 本轮新增 `spectrum_tune`）。
> 环境诚实：云 VM 无硬件/无 GNSS/无音频设备，所有"无硬件"路径即新人开箱路径；真硬件行为标注为「真机待验」。
> 口径：行号为改动后；函数名锚点不受行号漂移影响。

---

## 0. 走查路线与总印象

启动 →（默认即"频谱"tab）→ 看到一条正在滚动的测试信号频谱 + 一条诚实横幅 → 调中心频率 → 选解调模式 → 看峰值表/状态栏 → 切到"时空视图"tab。

**总印象**：空态诚实（测试信号标注"非硬件"、无 GNSS 不画假点、system 时钟明确告警）做得好；频谱调谐的"手感"在本轮之前有一个**真实卡点：单击频谱不调谐**（裸区 release 只发 viewChanged，新人点了没反应最困惑）。本轮已修。

---

## 1. 逐屏走查

### 屏 A：启动 / 首屏（频谱 tab）
- **能做什么**：开箱即有滚动频谱（引擎默认 offline test source）；顶部工具条可切 FFT/窗/平均/Max/余晖/dB/门限；底部峰值表；分隔条上下拖（trace/瀑布比例）。
- **代码锚点**：`centerTabs_->addTab(spectrum_, "频谱")`（main_window.cpp:734）；引擎默认测试源、`testLabel_` 横幅"测试信号（非硬件）· NOT HARDWARE"（spectrum_widget.cpp:362-363）；状态栏默认 "MBDSDR C++"（main_window.cpp:1742）。
- **卡点**：
  1. 新人不知道"这是假信号、接真硬件点哪里"——`connectBtn_`（main_window.cpp:259）在源面板，但首屏无引导。
  2. 无硬件枚举时 hot-plug 面板整体不编译（`#ifdef HAVE_RTLSDR`，main_window.cpp:1823），新人在无 librtlsdr 的构建里看不到任何"设备"概念。
- **改进建议**：空态加一行可关闭引导条（文案见 P1-清单）。

### 屏 B：空态 / 连接
- **能做什么**：`onSourceChanged` 诚实切换——未连接显示"RTL-SDR 未连接，使用测试信号"（main_window.cpp:3664-3666），状态栏源名追加"（非硬件）"（`fmtStripSource`，status_format.h:38）。
- **卡点**：点"连接"在无设备时的行为不可见（真机走 rtl_tcp/RTL 握手，云内无法验）。
- **改进建议**：连接失败/无设备时状态栏已有 showMessage 路径（main_window.cpp:2214/2221），建议新人也能从空态直接看到"未找到设备"提示。

### 屏 C：调谐（本轮重点）
- **能做什么**：
  - 直接在 `freqSpin_` 输 MHz（范围 clamp 诚实回显，main_window.cpp:2592-2598）；
  - 频谱**拖拽**调谐（Grab::Tune，spectrum_display.cpp mouseMoveEvent）；
  - **滚轮**按步进档调谐（plain wheel → `frequencyChanged`），Ctrl+wheel 绕光标缩放；
  - `←/→` 细调、Shift 细调、`PgUp/PgDn` 切步进档（cycleStepIndex wrap，shortcuts_catalog.h）；
  - `+/−` 切增益档；**双击**居中（mouseDoubleClickEvent:999）。
- **卡点（本轮已修）**：
  - **单击频谱不调谐**：旧 `mouseReleaseEvent` 对 `Grab::Tune` 只 `emit viewChanged()`，裸区（无 VFO 框）点一下什么都不发生；只有拖动才调。新人"点一个峰"的直觉落空。→ 本轮改为：近静止（<3px manhattan）单击跳到点击频率（`tuneSettleFreq`）。
  - **滚轮步进不吸附**：旧 wheel 直接 `dialFreqHz_ ± step`，多次后在非整档上漂移。→ 本轮改为吸附到 0 锚定 step 网格（`snapFreqToStep`）。
- **改进建议（P2）**：加"在点击处直接放固定 marker"手势（当前只能先调谐到视心中点再点"标记"按钮，spectrum_widget.cpp:103-112）。

### 屏 D：解码
- **能做什么**：解调 `AM/NFM/WFM/USB/LSB/CW/BPSK/QPSK/ADS-B`（main_window.cpp:437）；模式切换自动带带宽预设（`bandwidthOnModeSwitch`，main_window.cpp:1931-1940）；峰值表双击调谐居中（spectrum_widget.cpp:282-289）；状态栏 RSSI/SNR/静噪/S-meter。
- **卡点**：峰值表"未检测到信号"空态（spectrum_widget.cpp:338）无下一步引导。
- **改进建议**：空态文案补"把中心频率调到有峰处 / 或接真实硬件"。

### 屏 E：卫星 / 时空视图
- **能做什么**："时空视图"tab 四格（设备/信号/解码/GNSS）+ 三行（当前目标/时间源/多普勒），全部走 `spacetime_format.h` 纯函数；无硬件时诚实空态（system 时间源 Warn 告警"本机时钟，非 GNSS 授时"）。
- **代码锚点**：`refreshSpacetimeView()`（main_window.cpp:4788-4843）；时间源判定复用 `updateClockBiasLabel` 同一口径（4818-4827，绝不把 system 升 gnss）。
- **卡点**：
  - 新人不知道"当前接收目标：无"之后如何捕获过境——需去"天空"tab 选过境，时空 tab 无跳转引导。
  - 解码格恒为"无解码"（帧计数接线为 0，main_window.cpp:4798 注释已诚实标注）。
- **改进建议（P1）**：时空 tab 空态加一句"前往『天空』tab 选择过境并捕获"。

### 屏 F：设置
- **能做什么**：`QSettings` 持久化（步进/步进档/增益/瀑布速度/调色板/固定标记），500ms 防抖写盘（main_window.cpp:2613-2616）；快捷键目录对话框（shortcuts_dialog.cpp 渲染自 `shortcutCatalog()`，单一事实源不漂移）。
- **卡点**：快捷键在 AI 输入框打字时也全局触发（phase8 已记，未改上下文策略）。

---

## 2. 可执行体验改进清单（分级）

### P0 必须（本轮已落地 ✅）
| # | 问题 | 现状 file:line | 本轮实现 | 测试 |
|---|---|---|---|---|
| P0-1 | 单击频谱裸区不调谐，直觉落空 | `spectrum_display.cpp` 旧 `mouseReleaseEvent` Grab::Tune 只发 viewChanged | 近静止单击→跳到点击频率（`spectrum_tune.h::tuneSettleFreq`）；拖拽保持偏移模型（release 幂等） | 新增 widget 用例 `bareClickTunesDialWithoutVfo` |
| P0-2 | 状态栏 VFO 频率两条路径文本不一致：遥测走纯函数、spinbox 回写内联 `.arg` | `main_window.cpp` freqSpin `valueChanged` 旧 `QString("%1 MHz").arg(...)` | 改走 `ui::fmtStripVfoFreq(mhz*1e6)` | `status_format` 既有用例 + 接线统一 |

### P1 应做
| # | 问题 | 状态 |
|---|---|---|
| P1-1 | 滚轮步进多次后在非整档漂移 | ✅ 本轮已落地：`snapFreqToStep` 吸附 0 锚定 step 网格（`spectrum_tune.h`；wheelEvent 接线） |
| P1-2 | 边缘拖拽带宽 clamp / 分隔条弹性 clamp 内联在事件体，无法单测 | ✅ 本轮已落地：`bandwidthAfterEdgeDrag` / `clampTraceHeight` 抽纯函数，事件体委托 |
| P1-3 | 静噪 OFF 文案内联硬编码 | ✅ 本轮已落地：`squelchCheck` 分支改走 `ui::fmtStripSquelch(false,false)` |
| P1-4 | 首屏空态无"测试信号/如何接硬件"引导 | ⏳ 未做（需新增引导条文案；云内可验文案但要动 UI 布局，列入下一轮） |
| P1-5 | 时空 tab"目标：无"无跳转引导（如何捕获过境） | ⏳ 未做（建议加一句引导文案） |

### P2 可选（沿用历史，不重复造）
- P2-1 右键/ctrl+click 在点击处直接加固定 marker（当前只能用"标记"按钮在视心中点加）。
- P2-2 滚轮调瀑布余晖（phase8 已记未做，避免破坏既有滚轮契约）。
- P2-3 快捷键在 AI 输入框聚焦时不抢键（phase8 已记）。

---

## 3. 第八~十阶段改动体验一致性复查

| 阶段 | 交付 | 本轮复查结论 |
|---|---|---|
| **phase8（桌面打磨）** | 快捷键目录单一事实源 `shortcuts_catalog.h`、对话框渲染目录、箭头调频对称修复、状态栏纯函数 `status_format.h` | ✅ 目录与实际接线（VFO 切换等）未漂移；**发现并补掉两条残留**：freqSpin 回写 VFO 内联 `.arg`、静噪 OFF 内联硬编码——这两处正是"纯函数未全覆盖"的漏网，本轮补齐后状态栏文本只有一个事实源。 |
| **phase9（时空演示）** | "时空视图"tab + `spacetime_format.h` 纯函数、诚实空态、system 绝不升 gnss | ✅ `refreshSpacetimeView` 全部经由纯函数；时间源判定与 `updateClockBiasLabel` 同一口径（main_window.cpp:4818-4827）；空态"未连接（非硬件）"与 `status_format.h` 的非硬件标注口径一致，无第二套文案。 |
| **phase10（时空产品化）** | 时空卡片语义、多普勒补偿三态 | ✅ 多普勒补偿 armed&&无目标 → Warn 状态不一致（`spLineDoppler`），与走查"空态不报警红、状态不一致才琥珀"的体验一致。 |

**一致性结论**：第八~十阶段确立的"诚实空态 + 纯函数单一事实源"原则，本轮不仅守住，还把漏网的两条状态栏内联残留收编进同一套纯函数体系。

---

## 4. 本轮落地项（file:line）

| 项 | 现状（改前） | 实现 |
|---|---|---|
| 交互纯函数 | （新增） | `cpp/src/ui/spectrum_tune.h`：`freqAtPixel/tuneFreqAfterDrag/tuneSettleFreq/snapFreqToStep/bandwidthAfterEdgeDrag/clampTraceHeight` |
| 单击调谐 | `spectrum_display.cpp` mouseReleaseEvent Grab::Tune 只发 viewChanged | 委托 `tuneSettleFreq`，近静止单击跳到点击频率 |
| 拖拽调谐 | mouseMoveEvent Grab::Tune 内联偏移 | 委托 `tuneFreqAfterDrag` |
| VFO 框 settle | mouseReleaseEvent VfoBody 内联 manhattan<3 | 委托 `tuneSettleFreq`（行为不变） |
| 边缘带宽拖拽 | VfoEdge 内联 abs+clampd | 委托 `bandwidthAfterEdgeDrag` |
| 分隔条弹性 | Divider 内联 clampd | 委托 `clampTraceHeight` |
| 滚轮 snap | wheelEvent plain 直接 ±step | 过 `snapFreqToStep` |
| 状态栏 VFO | `main_window.cpp` freqSpin valueChanged 内联 `.arg` | 走 `ui::fmtStripVfoFreq` |
| 静噪 OFF | `main_window.cpp` squelchCheck 内联"静噪 OFF" | 走 `ui::fmtStripSquelch` |

## 5. 测试结果

```
QT_QPA_PLATFORM=offscreen ctest   →  100% tests passed, 0 failed out of 95
```
- 基线 94 不破；新增 `spectrum_tune`（7 纯函数槽）；`spectrum_interaction` 增 `bareClickTunesDialWithoutVfo`。
- 全量构建 0 错误（仅既有 QCheckBox::stateChanged deprecated 警告，与本轮无关）。
- 注：基线观察到 `device_ui`/`engine_hotplug` 在 `-j4` 下曾因 rtl_tcp drop 检测时序/并发出现环境性抖动；本轮全量 95 全绿时未复现。

## 6. 未完成项（诚实披露）

- **P1-4/P1-5 引导条**：首屏/时空 tab 空态引导文案未做（需动 UI 布局，下一轮）。
- **真机待验（云内无硬件）**：单击调谐/滚轮 snap 在真实 RTL 上的手感；`+/−` 在离散增益表上的档位吸附；时空 tab 真 GNSS 授时翻绿。
- **P2**：右键加 marker、滚轮调余晖、输入框聚焦不抢键——沿用历史待办，未做。
