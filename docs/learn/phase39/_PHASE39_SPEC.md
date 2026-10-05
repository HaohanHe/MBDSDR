# 第三十九阶段实现规格：移动端全面对齐

> 基线：HEAD = 5a5a8ef（已推送），ctest 127/127、flutter 342、pytest 42/42。
> 移动端已有：control_hub_client（5 端点）、remote_decoder_panel、sky_page、radio_scan/activity_log/recording、AI 会话页（Phase32）、AGC 开关、app_tokens.dart。
> 桌面：35 Agent 工具、ControlHub HTTP（6 端点）、多 VFO、三 decoder、扫频+书签、静噪自动门限、双游标、录制分段+回放、时空视图、AI 多会话+压缩+流式。

## Wave 1：能力差清点（块1+块3）
- 桌面 vs 移动全功能矩阵（能力 | 桌面 | 移动现状 | 判定：真差距/架构性差异/已对齐 | 证据 file:line），覆盖：解码面板、扫频/书签、静噪、多 VFO、双游标、录制/回放、时空视图、AI 会话、工具调用、状态显示；
- 架构性差异文档化（差异内容/原因/是否需桥接及条件：VFO 差异、IQ 录制差异 WAV vs SigMF、增益档表不推送等），不硬造桥接 → docs/learn/phase39/{capability-matrix.md, architectural-diffs.md}。

## Wave 2：真差距补做（块2，每项带测试）
- 以清点表裁决为准（候选：遥控解码面板与桌面 HTTP 端点对齐、扫频活动日志来源接真、书签跳频应用模式/带宽等）；全走 AppTokens 弹性（禁裸数）、触控≥44；flutter test 342 不回归 + analyze 0（新测试全量输出实际计数）。

## 质量门 / 红线
- 诚实空态（无设备/无 key/无真值不 mock）；无假数据；无比赛字样；活动参数禁入；OOM 约束增量构建（若动 C++ 则 ctest 127 全量输出）；只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、测试全量输出、未解决项。
