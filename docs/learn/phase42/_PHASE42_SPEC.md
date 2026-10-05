# 第四十二阶段实现规格：UI 剩余缺口清扫 + 移动端能力补全

> 基线：HEAD = 6025ac2（已推送），ctest 127+startup_robustness、flutter 351、pytest 145。
> 来源：open-items.md 18 条总账的 D 类（UI 2）+ E 类（移动 4）+ C 类（transport 1）。
> 守则：弹性优先（禁新硬编码/禁裸数）；不预置任何 TLE/星历/活动数据；诚实空态不 mock；无比赛字样；OOM 约束增量构建。

## 块 1+3（A：cpp 域）
- **compass_dial sky-token 组**：mobile 侧 compass_dial 画布字面量（fontSize 8.5 等）——注意任务原文将 compass_dial 归桌面 sky-token 组，实为 mobile 文件；按实际归属处理（若在 mobile 则归 B 或 A 协调，以文件实际位置为准）；
- **窄窗 960px 三处修复**（工具条下拉截断/游标按钮挤靠/S-meter 刻度连排，main_window.cpp）：弹性布局而非固定尺寸，不改变功能行为；三态截图复验（标准/窄窗/高DPI）；
- **task_progress/remote_decoder 非 4pt 固定尺寸复核**（保守保留或 token 化，给理由）；
- **transport 终态收口**：429/503 重试上限后 chatError 文案复核（重试上限后给什么文案）、无 key 时 PENDING_ONLINE_RUN 类文案一致性（llm_worker/agent/main_window）。

## 块 2（B：mobile 域，纯 Dart + 测试）
- **G2 录制回放接真**：recordings_page FilePlayer 布线确认/补齐（状态与按钮可见性）；
- **G3 双游标**：桌面有 marker 协议则移动补只读双 marker 显示，或诚实说明架构差异（如实判定）；
- **G4 扫频面板复核**：radio_scan 完整性（开始/停止/命中列表/书签跳频），缺则补；
- **G5 工具补面**：AI 工具列表移动端入口（无则加只读工具清单页或诚实说明）。

## 质量门 / 红线
- 弹性优先禁裸数；不预置活动数据；诚实空态不 mock；无比赛字样；活动参数禁入；
- ctest 127 不回归（全量输出）、flutter 351 + analyze 0（新测试全量实际计数）、pytest 145 不回归；
- OOM 约束增量构建；只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、全量输出、判"架构差异不修"项与理由、未解决项。
