# 第三十二阶段实现规格：Agent 层收官（三回补点）

> 基线：HEAD = 0d0364a（已推送），ctest 123/123、flutter 331/analyze 0。
>
> 勘察事实：main_window 已有 aiSessionCombo_ 切换器 + aiSessionStore_ + aiTransient_ 瞬态行 + tokens::kTouchMinDim（弹性）——块1 补全（新建/删除/流式行接线/chatError 保留/incomplete 角标）；llm_client 用 QNetworkAccessManager + reply->error()（:184）**未透传 HTTP 状态码**——块2 改造 transport；mobile 已有 chat_page.dart——块3 重构为带会话列表/流式行的 AI 会话页。

## 1. incomplete 生产接线（独占 cpp/src/ui/main_window.{h,cpp}）
- AI 面板会话栏补全：新建/删除会话按钮（弹性 token 布局、触控≥kTouchMinDim）、切换（已有 combo 复用）；
- 流式瞬态行：partialReady→aiTransient_ 显示、chatFinished 固化去重（已有范式复查）、chatError 保留已流式内容；
- incomplete 角标：会话列表显示「未完成」标记（store 原语已备，新消息自动清除）；
- 带 ctest（offscreen 生产 MainWindow：新建/切换/删除真实 JSON 落盘、瞬态行固化不重复、incomplete 显示与清除）。

## 2. 429/503 状态码分类（独占 cpp/src/ai/llm_client.{h,cpp} + llm_worker.{h,cpp} 分类处）
- transport 透传 HTTP 状态码（QNetworkRequest::HttpStatusCodeAttribute）到结果体；
- 七层错误恢复按真实状态码分类：429→退避、503→重试、401→密钥提示、400→终态如实、其他→如实报错；无 key 诚实 PENDING 不 mock；
- 带测试（本地 HTTP server 回 429/503/401/400 → 分类断言；与既有退避/重试逻辑衔接）。

## 3. 移动端 AI 会话页重构（独占 mobile/）
- 会话列表/新建/切换 + 流式行（partial 显示、完成固化去重、错误保留已流式内容）；
- 无 LLM key 诚实空态（不 mock 输出）；analyze 0 + flutter 331 基线不回归；
- 与桌面对齐（会话存储 JSON 形状一致可互操作或诚实标注差异）。

## 顺带（时间允许）
- docs/ 加 Agent 使用指南（35 工具能力清单 + 调用示例 + 错误恢复行为）。

## 质量门 / 红线
- 新 UI 全走 tokens 弹性（禁裸数）；触控≥44；诚实空态；无假数据；无比赛字样；活动参数禁入通用代码；干净室不复制 GPL；
- ctest 123 基线不回归（全量输出）；flutter 331 不回归 + analyze 0；
- 只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、测试全量输出、未解决项。
