# MBDSDR · AI 定义无线电

AI-defined Software-Defined Radio：把 SDR 接收/解调/解码/卫星过境/AR 指向全部能力封装成
LLM 可调用工具，由智能体编排成一键工作流。

- 智能体内核（Python，工具注册/工作流/调度/位姿/解码）：见 [`mbdsdr_ai/README.md`](mbdsdr_ai/README.md)
- 论文实验管线、C++ 真机看门狗、移动端 Flutter building blocks：见 `docs/learn/` 各阶段文档
- 许可证：MIT

> 云开发环境无真实硬件（无 USB/串口/声卡）；依赖设备的工具返回诚实空态，不造假数据。
> 当前清扫状态见 `mbdsdr_ai/README.md` §2（已摘除项 / 诚实标注项 / 接真项）。
