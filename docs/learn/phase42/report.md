# Phase42 块1+3（cpp 域 + transport 终态）交付报告

> 生成：2026-10-05。口径：file:line 取自本轮回改后实测；三态复验截图为本轮 `MainWindow::grab()` 真图；凡未跑完的项如实标注，不预估。

## 1. 窄窗三处修复（D1，cpp/src/ui/）

根因（Phase37 §1.2 复现）：频谱顶部工具条 `spectrum_widget.cpp` 的 `topRow` 是**单行 QHBoxLayout**，挤了 ~22 个控件；960 窄窗中心列仅 ~680px，单行放不下 → QComboBox 被压成"箭头盒"（2048→"2"）、按钮文字挤成单字、S-meter 10 个标签均分窄格贴连。

> 注：任务书 lead-in 写 main_window.cpp，但三处实际落在 `cpp/src/ui/spectrum_widget.cpp`（工具条）与 `cpp/src/ui/s_meter.cpp`（S-meter），均在红线允许的 `cpp/src/ui/` 内。

### 1.1 工具条弹性换行（spectrum_widget.cpp）
- 新增匿名命名空间内嵌 `FlowLayout`（`spectrum_widget.cpp:31-99`）：宽屏单行（与原 QHBoxLayout 视觉一致），窄屏自动折到第二行，不压控件。无新文件、未动 CMakeLists。
- `topRow` 由 `QHBoxLayout` 改为 `FlowLayout`（`spectrum_widget.cpp:116-118`），间距取 `tokens::scaled(kSpacingS)`。
- 移除 4 处 `topRow->addSpacing(kSpacingM)`（FlowLayout 不支持 addSpacing；行间统一由 hSpace 控制）。
- **下拉值截断**：FFT/窗/平均三个下拉加 `setSizeAdjustPolicy(QComboBox::AdjustToContents)`（按最长项定宽，闭合盒不再塌成"2"/"H"/"0"）+ 实时 `currentTextChanged → setToolTip`（`spectrum_widget.cpp:120-160`）。
- **游标 A/B/清游标挤靠**：FlowLayout 折行后，"游标A 游标B 清游标"在窄窗落到第二行独立排布，文字完整可读。
- `infoLabel_` 由 `addWidget(w,1)`（stretch）改 `addWidget(w)`（FlowLayout 无 stretch，mono 读数作为末项，功能不变）。

### 1.2 S-meter 刻度弹性稀疏化（s_meter.cpp）
- `s_meter.cpp:80-100`：按 `stride = ceil((labelWidth + minGap)/cellW)` 对内部标签稀疏化；宽表 stride=1（S0..S9 全显），窄表 stride=2/3（S0 S2 S4 S6 S9），端点 S9 恒显且距上一内部标签 ≥ stride。刻度细线保持密集。
- 新具名常量 `kSMeterTickLabelGap = 4`（`tokens.h:395-400`），走 `scaled()`。
- `sizeHint()` 的 `scaled(34)` 对齐为 `scaled(kSMeterH)`（纯 token 化，零视觉变化）。

## 2. task_progress / remote_decoder 归属复核（任务 #2）

- `mobile/lib/widgets/task_progress.dart:72` `width: 22` —— **Flutter/B 域文件**，cpp 侧无同名。
- `mobile/lib/widgets/remote_decoder_panel.dart:363` `height: 110` —— **Flutter/B 域文件**，cpp 侧无同名。
- 结论：二者确属 mobile（B 域），按红线**未触碰**，移交 B 域处理。非"架构差异不修"，是域归属。

## 3. transport 终态收口（C1，cpp/src/ai/）

### 3.1 重试预算耗尽终态文案（llm_worker.cpp）
- `doChat` 重试循环新增 `retriesUsed` / `budgetExhausted` 跟踪（`llm_worker.cpp:200-224`）：429/503 等可重试错误打满 `kAiMaxTransientRetries(=3)` 后，标记预算耗尽。
- `formatChatError(partial, error, retriesExhausted=0)`（`llm_worker.cpp:84-101` + 头文件默认参）：预算耗尽时追加 `（服务端繁忙，已自动重试 N 次后放弃）`；原始 transport error 自带 HTTP 码（如 `HTTP 429: rate limit`）。终态带码 + 重试次数，不再裸露 transport 错误。
- 终端错误（400/401/403/no-key/parse）首试即终态，N=0，文案不变。

### 3.2 无 key PENDING 文案一致性检查（llm_worker / agent / main_window）
三处语义一致，无矛盾，**未改文案**（改动会破坏测试）：
| 表面 | 文案 | 位置 |
|---|---|---|
| agent 状态栏 | `未配置 API Key — 仅本地指令` | `agent.cpp:59` |
| agent 对话兜底 | `未配置 API Key，仅支持频率（如 98.5）…等本地指令。` | `agent.cpp:88` |
| main_window AI 页占位 | `AI 助手将在这里接入（需在设置中配置 API Key）` / `已配置 API Key — AI 功能接入中` | `main_window.cpp:1701,2482-2483` |
| llm_worker/llm_client 无 key 原始错 | `API key not configured`（被 `LLM 请求失败：` 前缀） | `llm_client.cpp:146` |
- 备注：`API key not configured` 为**测试钉住字符串**（`test_ai_tool_loop.cpp:560` classifyLlmError 断言），不可改；与中文用户面文案语义一致（无 key→设置里配 key/仅本地指令）。

## 4. 三态复验截图（docs/learn/phase42/screenshots/）

`QT_QPA_PLATFORM=offscreen`，`MainWindow::grab()` 真图，无假数据。

| 截图 | 尺寸 | 结论 |
|---|---|---|
| BEFORE_narrow.png | 960×640 | 改前：FFT/窗/平均塌成箭头盒、游标按钮挤成单字、（S-meter 空态） |
| AFTER_narrow.png | 960×640 | 改后：工具条折两行，FFT **2048**/窗 **Hann**/平均 **Off** 全显；游标A/B/清游标独立成行可读，无叠字无裁切 |
| AFTER_standard.png | 1280×800 | 仍**单行**舒展，与改前一致，无回归 |
| AFTER_hidpi.png | 1920×1200 (dpr1.5) | 单行锐利，无叠字无裁切 |
| AFTER_narrow_devices.png | 960×640 真测试信号 | S-meter 刻度稀疏为 **S0 S2 S4 S6 S9**，无 S1S2 连排 |
| AFTER_std_devices.png | 1280×800 | S-meter 恢复全刻度 **S0..S9** 正常分隔 |

三态小结：标准/高DPI 单行舒展无回归；窄窗折行 + 下拉内容宽 + S-meter 稀疏化后**无叠字、无裁切、无连排**。

## 5. ctest 回归

- **直接受影响目标 `test_ai_tool_loop`：19/19 全过**（含 `retryBudget_exhaustedOn429` / `terminalOn401_noRetryWithKeyHint` / `noKey_honestPending_noTransport` / `chatErrorSignalNotFinished`），证明 transport 文案改动不回归。
- `mbdsdr`、`ui_shot_narrow`、`ui_shot_r27`、`ui_shot_devices` 均 ` -j2` 增量构建通过并跑通截图。
- **全量 ctest（127+startup_robustness = 128）未跑完**：因 `tokens.h` 是广含头文件，本轮改动触发全量 TUs 重编，`-j2` 全量构建在预算内未编完即被停止。**全量 ctest 全绿状态未亲跑确认**（如实标注）。

## 6. 判"架构差异不修"项与理由

- **无 key 英文原始串 `API key not configured` 保留英文**：`test_ai_tool_loop.cpp:560` 钉住该串做 classifyLlmError 断言；中文化会破测试。非偷懒，是测试契约。
- **mobile 两处字面量（task_progress.dart:72 / remote_decoder_panel.dart:363）不改**：确属 B 域 Flutter 文件，红线禁碰 mobile，移交 B。

## 7. 未解决项 / 遗留

- **全量 ctest 128 全量输出未跑**（§5）：需在本机把全量构建跑完后 `QT_QPA_PLATFORM=offscreen ctest --timeout 120` 复跑，预期 128/128（基线 126→128 含 startup_robustness）。
- 工具条分组间距由原 `addSpacing(M=8)` 统一为 `hSpace(S=4)`：折行/紧凑语义下可接受，非叠字裁切缺陷；如产品要恢复分组呼吸感，可后续给 FlowLayout 加分组分隔钩子。
- D1 其余子项（820 目标宽不可达、行内 26px 控件补 44px 命中区）不在本块范围，仍登记于 phase41/open-items.md。

## 8. 红线自查

- 弹性优先，新裸数仅 `kSMeterTickLabelGap`（tokens.h 具名常量，走 scaled）；未预置活动数据；诚实空态（S-meter 无设备/频谱空）未 mock；无比赛字样；活动参数未入。
- 只写 `cpp/src/ui/s_meter.cpp`、`cpp/src/ui/spectrum_widget.cpp`、`cpp/src/ai/llm_worker.{h,cpp}`、`cpp/src/core/tokens.h`、`docs/learn/phase42/`；未碰 `mobile/`。
- 仅暂存本人文件，未 `add -A`；**未 commit/push**。
