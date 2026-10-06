# Phase 58 块2：rtl_tcp 网络 SDR 源桌面核查

HEAD = b5183f4。构建 cpp/build，Qt 6.8.2，offscreen。file:line 实测。

## 1. 桌面 rtl_tcp 源现状：本就有（完整实现）
`cpp/src/dsp/rtl_tcp_source.{h,cpp}`（338 行）已完整实现，无需补协议：
- **连接握手**：POSIX 阻塞 fd（非 QTcpSocket，避免跨线程 event loop 问题），`start()` 2s 连接超时（rtl_tcp_source.cpp:38-102）。
- **RTL0 握手解析**：`readDongleInfo()`（:114）解析 12 字节 `RTL0` + tuner_type(BE u32) + gain_count(BE u32)；无 header 则诚实报未知，不猜调谐器范围。
- **命令序列化**：`sendCmd(cmd, arg)`（:173）5 字节帧 = `[cmd u8][param u32 大端]`。命令字：0x01 set freq、0x02 set sample rate、0x03 gain mode、0x04 gain(0.1dB)。
- **IQ 数据流**：`readIQ()`（:198）解析交错 uint8 I/Q（offset 127）。
- **诚实失败**：连接失败 `isConnected()` false、`readIQ` 返回 0、`lastError()` 回传真实 socket 原因；不伪造 IQ。
- engine 入口：`SpectrumEngine::connectRtlTcp(host, port)`（spectrum_engine.cpp:417 附近）。UI 源类型下拉已有"rtl_tcp 远程"（main_window.cpp:274）。

## 2. 本地握手验证（协议测试，非伪造 SDR）
新增 `cpp/tests/test_rtl_tcp_protocol.cpp`（4 用例）：
- 起本地 POSIX loopback mock 服务端，accept 后发 RTL0 头、记录客户端发出的每 5 字节帧。
- `handshakeParsesRtl0`：连接成功、capabilities 解析出 tuner_type=5（R820T）。
- `commandFrameByteLayout`：逐字节断言——start() 先发 0x02(2400000) 再 0x01(98500000)；setCenterFreq(100e6) 发 0x01(100000000)；setGain(20) 发 0x03(0) 再 0x04(200)。
- `iqStreamDecoded`：byte→(b-127)/128 映射正确。
- `honestFailureOnRefused`：连无人监听端口 → start() false、isConnected false、lastError 非空。
- **边界写明**：这是测协议序列化/握手解析，mock 服务端是确定性记录器，不冒充真实 SDR、不伪造接收。

## 3. 移动端协议对齐（只读，未改 mobile）
`mobile/lib/services/rtl_tcp_client.dart` `buildCommand`（:58）：5 字节 `buf[0]=cmd, buf[1..4]=大端 param`——与 C++ `sendCmd` 字节布局**完全一致**。移动端命令字更多（0x05 PPM、0x06 IF gain、0x08 AGC、0x09 direct sampling），C++ 为核心子集（0x01-0x04）。未改 mobile。

## 4. 三通道工具
新增工具 `connect_network_source`（write，手动门控拦截）：
- **Agent**：spec（tool_schema.cpp，host 必填 string、port 可选 number 默认 1234）+ executor `execConnectNetworkSource`（agent_tools.cpp，调 engine->connectRtlTcp，失败回传真实 socket 原因）+ dispatch 行。
- **ControlHub**：命令行 kRows + `cmdConnectNetworkSource`（control_hub.cpp）。
- **HTTP**：POST /command 委托 ControlHub；GET /status 自动覆盖。
- 无网络源诚实空态：host 缺失返回错误；连接失败 engine 回传真实错误并回空态。

### 真实计数
C++ 桌面 Agent 工具数：**36 → 37**（新增 connect_network_source，write）。
手动门控：write=true 走既有 gatedToolResult。

## ctest 全量（offscreen）
```
100% tests passed, 0 tests failed out of 130
（129 基线 + 新增 rtl_tcp_protocol；e2e_smoke 仍 Skipped）
```

## 文件清单
- 新：`cpp/tests/test_rtl_tcp_protocol.cpp`
- 改：`cpp/src/ai/tool_schema.cpp`、`cpp/src/ai/agent_tools.cpp`
- 改：`cpp/src/control/control_hub.{h,cpp}`
- 改：`cpp/tests/test_tool_registry.cpp`、`cpp/tests/test_agent.cpp`
- 改：`cpp/CMakeLists.txt`
- 文档：`docs/learn/phase58/`

## 未解决项
- rtl_tcp 命令字 0x05/0x06/0x08/0x09（PPM/IF gain/AGC/direct sampling）C++ 客户端未实现（移动端有）；本轮按核心子集交付，如需对齐可后续补。
- 真实硬件 rtl_tcp 服务端（带 RTL-SDR）的端到端 IQ 流验证需联网/硬件，本轮仅本地协议端验证。
