# 第三十七阶段实现规格：UI 弹性再收口（反硬编码专项）

> 基线：HEAD = 66b81c8（已推送），ctest 127/127、flutter 342、pytest 42/42。
> 设计语言：tokens.h（301 常量）/ AppTokens 弹性派生禁裸数、触控≥44（kTouchMinDim）、4pt 栅格、字重 500、强调色 #919cac；statusHint 空态统一。
> 用户红线：硬编码=反面（"主动性在用户"）；上次"固定44"被抓过——审计要彻底。Figma 参照禁推 GitHub，本轮只代码审计不重做视觉。
> OOM 约束：8GB cgroup，增量重编单目标，如实记录。

## Wave 1（两域并行审计+落地）
- **A：cpp/src/ui 裸数审计 + token 化落地**：正则扫 QSS/布局/尺寸魔法数字（排除 token 定义自身与算法必需常数）；audit 表（位置|裸数|语义|应替换 token|判定：替换/具名化/保留+理由）；判定"替换"改走 tokens.h 具名常量、"具名化"加 kXxx 注释常量、保留注明理由（DSP 物理常数等）；不改变功能行为。
- **B：mobile/lib 审计 + AppTokens 对齐**：同类裸数扫 mobile/lib（排除 tokens.dart 自身）；AppTokens 对齐；flutter test 342 不回归 + analyze 0。

## Wave 2：三态验证截图
- offscreen 截三态：标准窗口 / 窄窗 820×640 / 高DPI QT_SCALE_FACTOR=1.5；无叠字无裁切；关键面板截图 + 自查结论；触控目标抽查（按钮/滑块/列表行 ≥44px 逻辑高）。
- ctest 127/127 全量不回归（增量重编受影响目标，其余沿用基线，如实记录）。

## 质量门 / 红线
- 不改变功能行为；无假数据；无比赛字样；活动参数禁入；只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、替换统计、测试全量输出、未解决项。
