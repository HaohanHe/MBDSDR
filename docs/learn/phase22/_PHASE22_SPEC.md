# 第二十二阶段实现规格：ControlHub 真机远程加固 + 移动端对齐

> 基线：HEAD = 3344d89（已推送），cpp ctest 114 用例/113 过（e2e_smoke 环境性红不计）；flutter 305 全绿 analyze 0。
>
> 勘察事实：
> - A：ControlHub::execute（control_hub.cpp:229）命令处理器为直接调用；get_status（:563-592）用 snap 快照（connected/available）；**spyserver_server 无任何 ControlHub 引用（未打通）**；ControlHub 线程模型待审计。
> - B：mobile 已有 lib/dsp/squelch.dart、lib/models/bookmark.dart、lib/services/radio_controller.dart、lib/app/ai_tools.dart、lib/pages/spectrum_page.dart；手动 gate、解码面板（POCSAG/m17/VOR）存在性待盘点。

## A 组：ControlHub 真机远程加固（独占 cpp/src/control/ + server 相关 + 相应测试）
1. **跨线程安全审计**：命令处理器是否全部在正确线程执行？跨线程直接调 engine 槽 → 补 QMetaObject::invokeMethod/queued 保证线程安全；加回归测试（多线程连续写命令无崩溃/无数据竞争）。
2. **真机远程链路**：rtl_tcp/rtl_sdr 断开-重连-热插拔下 get_status 的 connected/status/error_message 三字段诚实正确（断/连/丢三场景，含测试）。
3. **spyserver_server × ControlHub**：评估「ControlHub 命令经远程通道下发」可行性，如实决策（做/YAGNI+理由+回补条件）。

## B 组：移动端与桌面对齐（独占 mobile/lib/ + mobile/test/）
1. 先盘点差距清单（只做移动端真实存在且可验证的缺口）：书签跳频应用模式/带宽、静噪自动门限、解码面板（POCSAG/m17/VOR）——无对应 DSP 引擎则如实 YAGNI+理由，不伪造。
2. **AI 工具手动 gate 对齐桌面**：确认 mobile AI 工具写动作是否有 manualMode gate；没有则补（桌面 aiManualMode 已有），保持手自一体一致。
3. 每项带 Flutter 测试；analyze 0 保持；305 基线不回归。

## 质量门 / 红线
- A 给 ctest 全量输出（114 基线+新增全绿）；B 给 flutter test 全量 + analyze 0。
- 干净室 MIT、不复制 GPL；通用非专用；不硬编码/不预存；无硬件诚实空态、合成仅测试夹具。
- 只暂存相关文件（禁 add -A）；**不自行 push**。如实报告 file:line、测试实跑数（全量）、未解决项（MainAgent 全量复核）。
