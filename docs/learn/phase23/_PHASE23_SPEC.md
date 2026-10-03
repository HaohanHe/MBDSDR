# 第二十三阶段实现规格：卫星 SSTV/SSDV 活动备战（练兵场，还有 4 天）

> 基线：HEAD = 268104e（已推送），ctest 115/113、flutter 309。
> 活动：2026-10-08/09/10 卫星 SSTV/SSDV 接收（JAMX01、ASRTU-1，共 34 幅图）。**活动参数只进 docs/learn/phase14/ 文档，禁止进通用代码/注释/docstring/UI/默认值（用户红线）**。
>
> 勘察事实：docs/learn/phase14/ 已有 P1/P2b/P2 规格/P3-email-checklist/P3-event-params/P3-receive-guide/P4/SSDV_SSTV_SPEC/pass_predict.py+test；onboard.py 已实现 sstv 与 ssdv（fsphil 方言，_ssdv_feed_core :506 起，字节/包层以上 + mbdsdr_ai.ssdv_decoder）。

## A 块：SSDV 方言核实（最高优先；独占文档 + 联网检索）
三种方言不互通：① fsphil 经典（15 头、MCU 级 JPEG 重组，我方 ssdv_decoder.py 实现的是这种）；② SP5WWP（6 头、JPEG 直拼，C++ ssdv_packet.h 是这种）；③ habhub API V0。
- 联网检索（卫星官方页/团队页/赛事说明/相关文章、紫丁香 LilacSat 系列、ASRTU-1 官方渠道）找 JAMX01/ASRTU-1 实际 SSDV 格式声明或信号描述；
- 无法确证则如实写"未确证 + 三条候选路径"（先 fsphil 试解、失败自动尝试 SP5WWP 等）+ 验证动作（真实录制样本回放确定）；
- 结论写进 SSDV_SSTV_SPEC.md 补充节 + P3-receive-guide.md（只改文档）。

## B 块：端到端接收演练（独占 onboard 演练 + pass_predict + SOP 文档）
- **ssdv 方向补端到端**：已知合成 SSDV 包流→解码→JPEG，确认 `onboard --mode ssdv` 全链可出图（sstv 方向 real_sstv.wav 已验证，勿重复）；
- **过境预测真跑**：pass_predict.py + 联网拉 JAMX01/ASRTU-1（或同类 LEO）TLE→预测窗口→可执行接收计划；
- **《活动接收 SOP 定稿》**：每条命令（freq/mode/时长）+ 判定标准（解出图=成功）+ 常见失败对照表 + 邮件材料清单勾选（P3-email-checklist.md 已有）——只写活动文档。

## C 块：接收鲁棒性小修（独占 python 修复 + 测试；只修 A/B 暴露的真实问题）
- 已知疑点：sstv 首帧冷启动 FAIL 根因（疑 scipy 首次初始化）、ssdv 包排序去重健壮性、降级路径；
- 只修真问题、不扩面；每修带测试（pytest）。

## 质量门 / 红线
- 活动参数禁入通用代码；通用能力是沉淀非比赛专用；干净室不复制 GPL；无硬件诚实空态、合成仅测试夹具。
- A 只改文档（检索结论带来源 URL）；B 只改文档+跑通现有链路（发现链路真问题可报 C）；C 只改 python+测试。
- 如实报告 file:line、测试通过数（pytest/ctest 全量输出）、未解决项与未确证项（MainAgent 亲自核实后推送）。
