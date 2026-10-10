# NanoVNA 第②轮·线 A：C++ VNA 状态层 + 三通道工具

> HEAD=b312abc 起。本轮在 C++ 侧建 NanoVNA 干净室客户端 + 状态层，并接三通道工具
> （`set_vna_sweep`/`get_vna_data`/`get_vna_status`）。**不做 UI tab（线 B 另一 agent 做，未碰 main_window）**。
> 协议事实学自公开上游（ttrftech/NanoVNA 固件、nanovna-saver）的机制（命令字/数据格式/提示符时序），
> 实现为本项目独立编写（clean-room），不复制任何上游代码；射频为通用工程公式。

## 1. 传输层选择

- **优先复用结论**：仓内 `cpp/src/gnss/serial_transport.{h,cpp}`（termios，fail-soft）是**只读 NMEA** 后端
  （`ITransport` 无 `write()`，read-only）。NanoVNA 文本协议需要「写命令 + 读行直到提示符」双向流，
  故 gnss 传输无法直接复用写路径。
- **做法**：新建 `cpp/src/vna/nanovna_client.{h,cpp}`，自带 `VnaTransport` 抽象接口
  （open/close/isOpen/write/readLine/resetInputBuffer）+ 内置 termios 真实后端
  （115200 8N1，`cfmakeraw`，VMIN=0/VTIME=1 读超时，fail-soft open()==false，非 Unix stub）。
  测试用自造 `ReplayTransport` 注入脚本化协议文本，无硬件即可确定性回放。
- 协议：USB CDC 115200 8N1；命令以 `\r` 结尾；回包行 `\r\n`；一轮响应以 `ch>` 提示符收尾；
  `data N` 每行一对 "re im" 复数（与 Python `nanovna_client.py` 同源，C++ 自写不抄）。

## 2. 射频纯函数（自写，通用工程公式）

`cpp/src/vna/nanovna_client.cpp`：
- `returnLossDb(S11) = -20 log10|S11|`（|g|=0 → +inf）
- `vswr(S11) = (1+|g|)/(1-|g|)`（|g|≥1 → +inf；|g|=0 → 1.0）
- `s11ToImpedance = Z0·(1+S11)/(1-S11)`，Z0=50Ω
- `s21GainDb = 20 log10|S21|`；`s21PhaseDeg = deg(atan2(im,re))`

## 3. 状态层 file:line

- `cpp/src/dsp/spectrum_engine.h`：include `vna/nanovna_client.h`；`vna::NanoVnaClient vna_;` 成员（默认空 ctor → 诚实空态）；
  读回 `vnaClient()`（读写两个重载）。
- **给线 B UI 的读回入口**：`engine.vnaClient()` → `isConnected()/model()/version()/calStatus()/hasSweep()/
  sweepStartHz()/sweepStopHz()/sweepPoints()/readFrequencies()/readData(0|1)`。

## 4. 三通道 file:line

| 通道 | set_vna_sweep（写） | get_vna_data（读） | get_vna_status（读） |
|---|---|---|---|
| tool_schema.cpp | 710 区（start_hz/stop_hz/points 必填） | 736 区 | 746 区 |
| agent_tools.cpp | `execSetVnaSweep` | `execGetVnaData` | `execGetVnaStatus` |
| agent_tools.cpp dispatch | 紧邻 lrpt dispatch 表 | 同左 | 同左 |
| control_hub.cpp 写表 | `{set_vna_sweep,true,cmdSetVnaSweep}` | — | — |
| control_hub.cpp 读表 | — | `{get_vna_data,false,cmdGetVnaData}` | `{get_vna_status,false,cmdGetVnaStatus}` |
| control_hub.cpp handler | `cmdSetVnaSweep` | `cmdGetVnaData` | `cmdGetVnaStatus` |
| control_hub.h | 三 handler 声明 | 同左 | 同左 |
| HTTP | POST /command 透传（无新路由） | 同左 | 同左 |

诚实语义：无设备时 get 字段/数组为空（`connected=false` + note）；set_vna_sweep 校验缺参/越界（ok:false），
合法但无设备 → `ok:true, applied:false, connected:false`（不伪造连接）；写工具手动模式 gate 拦截。

## 5. 确定性测试

- **test_nanovna_client**（新二进制，CMake 注册 `nanovna_client`）：**7 passed, 0 failed**。
  自造 ReplayTransport 脚本化协议文本 → 握手解析（model/version trim）、命令序列严格比对、
  sweep 校验（stop≤start / points≤0 在发命令前拒绝）、frequencies/data 解析、cal 空格切分、
  RF 已知值（短路 S11=-1→VSWR inf/RL 0dB；匹配 S11=0→VSWR 1；|g|=0.5→VSWR 3；Z=50Ω；S21=1→0dB；(1,1)→45°）、
  无传输注入诚实空态。
- **test_control_hub**：+`vnaSweepValidatesHonestEmptyAndGate`（33→**34 passed**）。

## 6. 金集计数（offscreen 真实 passed）

| 二进制 | 本轮 |
|---|---|
| test_tool_registry | **8 passed, 0 failed**（schema 55→58；kAllCxxTools/kExpectedWriteTools/kFlutterUngatedReadTools/smoke 同步） |
| test_tool_schema | **10 passed, 0 failed**（specs.size/headerCount 55→58，description map +3） |
| test_agent | **38 passed, 0 failed**（tools 55→58；写 33→34、读 22→24） |
| test_control_hub | **34 passed, 0 failed**（+VNA 槽） |
| test_control_http | **15 passed, 0 failed**（既有 lrpt 透传断言覆盖；VNA 走同一透传路径） |
| test_ai_real_link | **17 passed, 0 failed**（supported 名单 +3：set_vna_sweep/get_vna_data/get_vna_status） |

## 7. mobile catalog 55→58

`tool_catalog.dart` +3 条目；`tools_catalog_test.dart` 55→58（length/toSet/头部"桌面端共 58 个"）；
page 注释 55→58；buildRadioTools 保持 10（移动端无 VNA 解调/串口链，桌面独有，诚实标注）。

## 8. 诚实未完成项

- **真实串口连接未接线**：`vna_` 默认空 transport（无 open）。线 B UI 需补「选择串口 → `createSerialTransport(dev)` → `vna_.connect()` 握手」的接线与轮询取数；本轮只提供客户端与工具面。
- 未做 UI tab（线 B）；未接 `data 1`(S21) 的派生曲线输出到 get_vna_data（当前 get_vna_data 只回 S11 派生，S21 留线 B 按 UI 需求扩展）。
- phase31 `agent-tool-documentation.md`：预编译 regen 脚本不可重编译（scratch 隔离），本轮**未手工补段**，留线 B UI 收尾后一并补 set_vna_sweep/get_vna_data/get_vna_status 三段。
