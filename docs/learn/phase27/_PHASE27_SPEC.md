# 第二十七阶段实现规格：生产路径接线收尾（HTTP 端点 + ScanActivityLink 引擎级）

> 基线：HEAD = 73a9e49（已推送），ctest 118 注册/116 过（e2e_smoke/device_ui 环境性红不计）、flutter 325。
>
> 勘察事实：main_window.{h,cpp} 无 ControlHub/HttpControlServer 引用（Phase25 遗留真缺口）；mobile settings_service 已有 controlHubHost/Port 键（默认空/8080，:127-128/:192-193）；engine 已有 scanBand(:97)/spectrumReady(SpectrumFrame) 信号(:337) 可喂 ScanActivityLink。

## 1. HTTP 端点生产接线（独占 ui/main_window + main.cpp + mobile/settings_service + 集成测试）
- GUI 构造后启动 HTTP 服务（默认 127.0.0.1:50732，QSettings/env 可配，tokens 具名常量；MainWindow 持 ControlHub 实例构造后 startDefault）；退出优雅关闭（stop+join）；
- 端口被占/绑定失败→诚实警告横幅（不崩溃、可重试/换端口）；
- mobile settings_service 补 host/port 默认值（如 127.0.0.1/50732，可与桌面一致），核实设置页分区接线；
- **端到端集成测试**（真实走 HTTP，云内 loopback）：起引擎+HTTP 服务 → GET /status 真实字段 → POST set_frequency 引擎真实改变 → 三 decoder 只读（空态诚实）。

## 2. ScanActivityLink 引擎级接线（独占 dsp/spectrum_engine + 引擎级测试）
- 引擎频谱/RSSI 流 → ScanActivityLink 喂入（无硬件诚实空态；合成可测）；
- 扫频发现活动 → onRetune 接引擎真实 setCenterFreq → 触发录制/解码（复用 Phase26 工具化命令的语义，引擎侧数据流接线）；
- 引擎级 ctest：合成信号走引擎→扫描→命中→驻留→解调断言（非仅 dsp 级）。

## 质量门 / 红线
- 干净室 MIT、不复制 GPL；通用非专用；不硬编码（端口 tokens）；无硬件诚实空态、合成仅测试夹具；活动参数禁入代码。
- 全量构建全部目标 + 全量 ctest（118 基线+新增，环境性红如实标注）；flutter 325 不回归 + analyze 0；只暂存相关文件（禁 add -A）；**不 commit/push**；如实报告 file:line、全量输出、未解决项。
