# SpyServer 协议学习笔记（SDR++ 服务端 / Airspy SpyServer）

> 干净室研究。只读两个**客户端**实现：
> - 主参考：`repos/sdrpp/source_modules/spyserver_source/src/`（`spyserver_protocol.h`、`spyserver_client.cpp/.h`、`main.cpp`）。
> - 交叉核对：`repos/sdrangel/plugins/samplesource/remotetcpinput/`（`spyserver.h`、`remotetcpinputtcphandler.cpp`）——SDRangel 独立重写了同一套线格式。
>
> 本笔记**没有**读服务端（ops/osprey / Airspy 官方 `spyserver` 二进制）源码。所有「服务端行为」都是从客户端发包/收包逻辑**反推**的；凡反推处均标注「⚠️推断」，有两处以上独立客户端互相印证的标「✅双客户端互证」。照实记录协议字节布局，不逐字复制源码。

---

## 0. 一句话结论

SpyServer 是 Airspy 为 SDR#/Airspy 做的**远程 IQ 推流协议**：一条 TCP 长连接，客户端发「HELLO + 若干 SET_SETTING」，服务端回「设备信息 + 状态同步」后按帧持续推 IQ 采样。线格式是**裸 C 结构体位串、全小端（little-endian）、无校验、无应用层流控**。默认端口 **5555**。

---

## 1. 数据源与可信度分级

| 来源 | 角色 | 用到什么 |
|---|---|---|
| `repos/sdrpp/.../spyserver_source/src/spyserver_protocol.h` | 协议常量/结构定义（头文件版权属 Youssef Touil，Ryzerth 校正） | 所有枚举、结构体内存布局 |
| `repos/sdrpp/.../spyserver_source/src/spyserver_client.cpp` | 客户端收发逻辑 | 握手字节序、IQ 解码、高低 16 位打包 |
| `repos/sdrpp/.../spyserver_source/src/main.cpp` | SDR++ 模块 UI/启停流程 | 端口默认值、设置下发顺序、采样率推导 |
| `repos/sdrangel/.../remotetcpinput/spyserver.h` | 第二套独立客户端定义 | 独立确认小端编码、结构字数、命令号 |
| `repos/sdrangel/.../remotetcpinputtcphandler.cpp` | 第二套独立客户端收发 | 独立确认握手帧字节、服务端问候顺序 |
| Airspy 官方用户文档 / rtl-sdr.com 教程 | 公开资料 | 仅佐证默认端口 5555、多客户端架构；**无**公开字节级规范 |

**关于上游文档**：Airspy 官方 `spyserver` 以二进制分发，公开资料（airspy.com/directory、rtl-sdr.com 教程、Reeve/NU5D 配置教程）只讲怎么配 `bind_port=5555`、`maximum clients=100`，**没有**公开的字节级协议规范。因此本笔记的字段布局主要由上述两个开源客户端互证得出，而非官方协议文档。

---

## 2. 传输层、端口、字节序

| 项 | 值 | 依据 |
|---|---|---|
| 传输层 | 单条 **TCP** 长连接（全双工） | `client.cpp:166` `net::connect(host, port)` |
| 默认端口 | **5555** | `main.cpp:298`（`int port = 5555`）、`main.cpp:318`（配置默认） |
| 字节序 | **小端 little-endian** | ✅双客户端互证，见下 |
| 对齐/填充 | 结构体内 `uint32` 数组，天然 4 对齐；线上按字段顺序紧凑排列 | `spyserver_protocol.h` 各 struct |
| 长度前缀 | 每个帧都有 BodySize 前缀，无分隔符、无转义 | `CommandHeader`/`MessageHeader` |

**字节序为何确定是小端**：
- sdrpp 直接把 C struct 指针强转成 buffer 整块发，全程**没有任何 ntohl/htonl/bswap**（在该模块内 grep 不到任何字节交换），跑在 x86 小端上——即线序=主机序=小端。⚠️这本身是「在小端主机上直拷」的推断。
- SDRangel 显式编码：`spyserver.h:65-71` `encodeUInt32` 把 `p[0]` 写成最低字节、`p[3]` 写成最高字节；`extractUInt32` 反之。✅这是**显式的小端序列化**，独立印证。

> 实现提醒：自己写服务端时，**不要**用网络序（大端）。所有 u32 一律按小端落盘。

---

## 3. 帧总览（两种帧头）

协议里有两个方向、两种帧头，都是 4 字节×N 的小端 u32 数组。

### 3.1 命令帧（客户端 → 服务端）
`spyserver_protocol.h:105-108` `SpyServerCommandHeader`：

| 偏移 | 字段 | 类型 | 说明 |
|---|---|---|---|
| 0 | CommandType | u32 LE | 命令号（见 §4） |
| 4 | BodySize | u32 LE | 紧随其后 body 字节数 |
| 8… | body | 字节流 | 命令参数 |

组包代码：`client.cpp:70-76`（先写 header 两字段，再 memcpy body）。

### 3.2 消息帧（服务端 → 客户端）
`spyserver_protocol.h:115-121` `SpyServerMessageHeader`（共 20 字节）：

| 偏移 | 字段 | 类型 | 说明 |
|---|---|---|---|
| 0 | ProtocolID | u32 LE | ⚠️客户端**不校验**，值见 §8 |
| 4 | MessageType | u32 LE | 低 16 位=消息类型；高 16 位=标志（IQ 流里是增益 dB） |
| 8 | StreamType | u32 LE | 流类型（0 状态/1 IQ/2 AF/4 FFT），客户端不解析 |
| 12 | SequenceNumber | u32 LE | 序号，客户端不解析 |
| 16 | BodySize | u32 LE | body 字节数 |
| 20… | body | 字节流 | 消息体 |

> SDRangel 把后三个非关心字段直接命名 `m_unused1/2`（`spyserver.h:30-36`），即它只读 `m_message`（偏移 4）和 `m_size`（偏移 16）。✅印证客户端确实不关心 ProtocolID/StreamType/SequenceNumber。

MessageType 的高低位拆分在 `client.cpp:124-125`：
```
mtype   = MessageType & 0xFFFF        // 消息类型
mflags  = (MessageType >> 16) & 0xFFFF // IQ 流里当增益（dB，整数）用
```

---

## 4. 命令帧：客户端 → 服务端

### 4.1 命令号枚举
`spyserver_protocol.h:33-37` `SpyServerCommandType`：

| CommandType | 名称 | body 内容 |
|---|---|---|
| 0 | `SPYSERVER_CMD_HELLO` | 协议版本 + 客户端名（见 §5） |
| 2 | `SPYSERVER_CMD_SET_SETTING` | 一个 `{Setting, Value}` 对（8 字节） |
| 3 | `SPYSERVER_CMD_PING` | ⚠️枚举存在，但**两个客户端都没发过**；body 布局未确认 |

> 注意：**没有**独立的「设置采样率」「设置带宽」命令号。所有调参都复用命令号 2（SET_SETTING），靠 body 里的 Setting 子号区分。SDRangel 的 `enum Command { setStreamingMode=0, ... }`（`spyserver.h:12-19`）名字有误导——那其实是 **Setting 子号**，它统一用命令号 2 发出去（`remotetcpinputtcphandler.cpp:486` `encodeUInt32(&request[0], 2)`）。✅互证。

### 4.2 SET_SETTING 的 body：`{Setting, Value}`
`spyserver_protocol.h:110-113` `SpyServerSettingTarget`（8 字节）：

| 偏移 | 字段 | 类型 |
|---|---|---|
| 0 | Setting | u32 LE（子号，下表） |
| 4 | Value | u32 LE |

Setting 子号枚举 `spyserver_protocol.h:39-55`：

| Setting | 名称 | Value 含义 | 谁在用 |
|---|---|---|---|
| 0 | STREAMING_MODE | 流模式位掩码（见下） | sdrpp `main.cpp:134`；SDRangel `:519` 发 1 |
| 1 | STREAMING_ENABLED | 0/1，推流开关 | sdrpp `client.cpp:30,35` |
| 2 | GAIN | 增益档位 index（0..MaximumGainIndex） | sdrpp `main.cpp:135` |
| 100 (0x64) | IQ_FORMAT | IQ 采样格式：1=UInt8,2=Int16,3=Int24,4=Float,5=DINT4 | sdrpp `main.cpp:131`；SDRangel `:497-513` |
| 101 (0x65) | IQ_FREQUENCY | 中心频率（Hz，u32） | sdrpp `main.cpp:133,156` |
| 102 (0x66) | IQ_DECIMATION | 抽取级数（=log2 抽取比，见 §6） | sdrpp `main.cpp:132` |
| 103 (0x67) | IQ_DIGITAL_GAIN | 数字增益（浮点按 u32 传？⚠️见 §8） | sdrpp `main.cpp:136` |
| 200 (0xC8) | FFT_FORMAT | FFT 采样格式 | 仅枚举，sdrpp 未下发 |
| 201 (0xC9) | FFT_FREQUENCY | FFT 中心频率 | 仅枚举 |
| 202 (0xCA) | FFT_DECIMATION | FFT 抽取级数 | 仅枚举 |
| 203 (0xCB) | FFT_DB_OFFSET | FFT dB 偏移（范围 0..100，`protocol.h:24`） | 仅枚举 |
| 204 (0xCC) | FFT_DB_RANGE | FFT dB 量程（10..150，`protocol.h:22-23`） | 仅枚举 |
| 205 (0xCD) | FFT_DISPLAY_PIXELS | FFT 像素点数（100..32768，`protocol.h:20-21`） | 仅枚举 |

STREAMING_MODE 位掩码 `spyserver_protocol.h:64-70`：
IQ=1, AF=2, FFT=4（来自 `StreamType`）。常用值：`IQ_ONLY=1`。

> SDR++ 实际下发顺序（`main.cpp:130-137`，点开始播放时）：
> 1. IQ_FORMAT
> 2. IQ_DECIMATION
> 3. IQ_FREQUENCY
> 4. STREAMING_MODE = IQ_ONLY(1)
> 5. GAIN
> 6. IQ_DIGITAL_GAIN
> 7. `startStream()` → STREAMING_ENABLED=1

---

## 5. HELLO 握手帧（客户端 → 服务端，连接后第一个包）

`spyserver_protocol.h:101-103` `SpyServerClientHandshake` body 前 4 字节是协议版本，后面紧跟**不带 NUL 的客户端名字符串**。

sdrpp `client.cpp:78-89`：body = `u32 ProtocolVersion` + `appName` 原始字节（用 `appName.size()`，**不含结尾 '\0'**）。sdrpp 传的名字是 `"SDR++"`（`client.cpp:17`）。

线格式（小端）：

| 偏移 | 字段 | 字节 | 说明 |
|---|---|---|---|
| 0 | CommandType | 4 LE | = 0 (HELLO) |
| 4 | BodySize | 4 LE | = 4 + 名字长度 |
| 8 | ProtocolVersion | 4 LE | = `0x020006A4`（`protocol.h:16`，即 `(2<<24)\|(0<<16)\|1700` = 33556132） |
| 12 | appName | N | ASCII，无 NUL 结束符 |

实例：
- sdrpp 发 `"SDR++"`（5 字节）→ BodySize=9，整帧 17 字节。
- SDRangel 发 `"SDRangel"`（9 字节）→ BodySize=13，整帧 21 字节。✅组包见 `remotetcpinputtcphandler.cpp:469-478`。

> ⚠️版本号协商：客户端发完 HELLO 就开始等服务端消息，**不校验服务端回包里的 ProtocolID**。版本不匹配时服务端是否会主动断开，客户端代码看不出来（⚠️服务端行为，未确认）。

---

## 6. 采样率协商（重点：没有独立的「设采样率」命令）

这是最容易写错的一点。**采样率不是单独设的**，而是由「设备最大采样率 ÷ 2^抽取级数」推导。

- 服务端在设备信息里给 `MaximumSampleRate` 和 `DecimationStageCount`、`MinimumIQDecimation`（见 §7.1）。
- 客户端枚举可选采样率：`for i = MinimumIQDecimation .. DecimationStageCount: sr = MaximumSampleRate / 2^i`（`main.cpp:272-277`）。
- 客户端把**绝对级数** `i` 通过 SET_SETTING(IQ_DECIMATION, i) 发给服务端（`main.cpp:132`：`srId + devInfo.MinimumIQDecimation`）。
- SDRangel 注释直说「Protocol only seems to allow changing decimation」（`remotetcpinputtcphandler.cpp:546-547`），采样率随抽取级数隐式决定。✅互证。

> 即：服务端收到 IQ_DECIMATION=k 后，应以 `MaximumSampleRate >> k` 出 IQ 流。客户端本地再用这个值告诉 DSP「输入采样率是多少」（`main.cpp:281-282`），网络上并不交换这个数值。

**带宽**：设备信息里有 `MaximumBandwidth`，但 sdrpp 客户端**没有**下发带宽的命令（Setting 表里无带宽子号）。⚠️带宽由服务端配置/抽取级数隐式决定，客户端只读展示。

---

## 7. 消息帧：服务端 → 客户端

### 7.1 设备信息消息（MessageType=0，DEVICE_INFO）
`spyserver_protocol.h:123-136` `SpyServerDeviceInfo`，**12 个 u32 = 48 字节** body。

| 偏移 | 字段 | 类型 | 含义 |
|---|---|---|---|
| 0 | DeviceType | u32 | 1=AirspyOne, 2=AirspyHF, 3=RTLSDR（`protocol.h:26-31`） |
| 4 | DeviceSerial | u32 | 设备序列号 |
| 8 | MaximumSampleRate | u32 | 设备最大采样率（Hz） |
| 12 | MaximumBandwidth | u32 | 最大带宽 |
| 16 | DecimationStageCount | u32 | 最大抽取级数 |
| 20 | GainStageCount | u32 | 增益级数 |
| 24 | MaximumGainIndex | u32 | 最大增益档（RTLSDR 常 0..29） |
| 28 | MinimumFrequency | u32 | 最低频率 |
| 32 | MaximumFrequency | u32 | 最高频率 |
| 36 | Resolution | u32 | 采样分辨率（位） |
| 40 | MinimumIQDecimation | u32 | 最小抽取级数（常 0；配了 maximum_bandwidth 时 >0） |
| 44 | ForcedIQFormat | u32 | 强制 IQ 格式（0=不强制） |

> SDRangel 的 `Device` struct 也是 12 个 u32，并把带宽/增益级数/ForcedIQFormat 标成 unused（`spyserver.h:38-51`），注释里给了典型值：Airspy HF 抽取 8 级、Airspy 11 级、RTL(SDR) 9 级；最大增益 HF=8 / Airspy=21 / E4000=14 / R820=29。✅字段数互证。

### 7.2 客户端同步消息（MessageType=1，CLIENT_SYNC）
`spyserver_protocol.h:138-148` `SpyServerClientSync`，**9 个 u32 = 36 字节** body。服务端在握手后、以及任何参数变化时推给客户端，用于多客户端对齐谁在控、当前参数是多少。

| 偏移 | 字段 | 含义 |
|---|---|---|
| 0 | CanControl | 本客户端是否拥有控制权 |
| 4 | Gain | 当前增益档 |
| 8 | DeviceCenterFrequency | 设备硬件中心频率 |
| 12 | IQCenterFrequency | IQ 中心频率 |
| 16 | FFTCenterFrequency | FFT 中心频率 |
| 20 | MinimumIQCenterFrequency | |
| 24 | MaximumIQCenterFrequency | |
| 28 | MinimumFFTCenterFrequency | |
| 32 | MaximumFFTCenterFrequency | |

> ⚠️sdrpp 客户端**根本没解析**这条消息（`dataHandler` 里没有 type==1 的分支），收了就丢弃。SDRangel 才解析它（`spyserver.h:53-63`，只读前 4 个；`remotetcpinputtcphandler.cpp:1437-1472`）。重要：SDRangel 把「收到第一条 State(1) 消息」当作握手完成信号，然后才发自己的设置并开始推流（`remotetcpinputtcphandler.cpp:1360-1368`）。

### 7.3 其他消息类型
`spyserver_protocol.h:81-99` `SpyServerMessageType`：

| 低16位 MessageType | 名称 | body |
|---|---|---|
| 0 | DEVICE_INFO | 48 字节（§7.1） |
| 1 | CLIENT_SYNC | 36 字节（§7.2） |
| 2 | PONG | PING 的回应（客户端未发 PING，故用不到） |
| 3 | READ_SETTING | 读设置回应（客户端未用） |
| 100 (0x64) | UINT8_IQ | 8bit IQ 帧（§9） |
| 101 (0x65) | INT16_IQ | 16bit IQ 帧 |
| 102 (0x66) | INT24_IQ | 24bit IQ（sdrpp 报「不支持」`client.cpp:152-155`） |
| 103 (0x67) | FLOAT_IQ | float32 IQ 帧 |
| 200..203 | UINT8/INT16/INT24/FLOAT_AF | 音频流（本项目不用） |
| 300 (0x12C) | DINT4_FFT | FFT 流（压缩？） |
| 301 (0x12D) | UINT8_FFT | FFT 流 |

> FFT 流（300/301）：两个客户端**都没有解析 FFT body 的实际像素布局**。⚠️只确认「存在这两个消息号」，FFT  payload 的逐字节格式未从客户端得到，**不要猜**。

---

## 8. IQ 数据流格式

### 8.1 帧结构
推流后服务端反复发消息帧：MessageType=100/101/103，BodySize=这一帧的 IQ 字节数，body 是连续采样。客户端按 `BodySize` 一次性读够（`client.cpp:116`）。

### 8.2 采样在 body 里的排列（I/Q 交错）
- **UInt8 (type 100)**：每个复数样本 2 字节，顺序 `[I0, Q0, I1, Q1, …]`。是**偏移二进制（offset-binary）无符号**：解码要 `-128` 转有符号。代码 `client.cpp:141-142`：`(byte - 128) * scale`。
- **Int16 (type 101)**：每个复数样本 4 字节，小端 `[I0_lo,I0_hi, Q0_lo,Q0_hi, …]`，有符号。代码 `client.cpp:146-151`。
- **Float (type 103)**：每个复数样本 8 字节，两个 little-endian float32（I,Q）。代码 `client.cpp:156-161`。
- 每帧样本数 = `BodySize / (每样本字节数)`（`client.cpp:137,147,157`）。SDRangel 也按 `bytesPerIQPair = 2*bits/8` 切（`remotetcpinputtcphandler.cpp:1519`）。✅互证。

### 8.3 增益怎么带（MessageType 高 16 位）
服务端在 `MessageType` 的**高 16 位**塞了一个整数增益（dB）。客户端：
```
gain = 10 ^ (mflags / 20.0)     // client.cpp:138,148,158
```
然后 UInt8 额外除 128、Int16 除 32768 归一化到浮点（`client.cpp:139,149`）。

> ⚠️这是 sdrpp 客户端的解读；SDRangel 直接忽略高 16 位、用自己固定的归一化（`spyserver.h` 里没读 mflags）。所以「高 16 位=增益 dB」这一条**只有 sdrpp 一家在用**，SDRangel 不依赖它。干净室实现里建议照 sdrpp 发，但知道另一家不看它。

### 8.4 流控 / 缓冲
- 客户端读缓冲上限 `SPYSERVER_MAX_MESSAGE_BODY_SIZE = 1 MiB`（`protocol.h:19`，`client.cpp:10`）。
- **没有**应用层 ACK、窗口、信用机制；服务端一直推，靠 **TCP 背压**（接收方不读则服务端阻塞）。⚠️从客户端代码反推——它读完一帧就立刻 `readAsync` 下一帧头（`client.cpp:163`），从不回发流量控制报文。

---

## 9. 握手与状态机

连接建立后的时序（综合两客户端）：

```
Client                                   Server
  |--- TCP connect --------------------->|   :5555
  |--- HELLO(0): ver=0x020006A4,name -->|
  |                                      |
  |<-- MsgHeader + DEVICE_INFO(0) body ---|   48 字节
  |<-- MsgHeader + CLIENT_SYNC(1) body --|   36 字节（SDRangel 据此触发配置）
  |                                      |
  |--- SET_SETTING: IQ_FORMAT ---------->|
  |--- SET_SETTING: IQ_DECIMATION ------>|
  |--- SET_SETTING: IQ_FREQUENCY ------->|
  |--- SET_SETTING: STREAMING_MODE=1 --->|
  |--- SET_SETTING: GAIN --------------->|
  |--- SET_SETTING: IQ_DIGITAL_GAIN --->|
  |--- SET_SETTING: STREAMING_ENABLED=1->|   开始推流
  |<-- MsgHeader + UINT8/INT16/FLOAT_IQ -|   循环推 IQ 帧
  |<-- MsgHeader + CLIENT_SYNC(1) -------|   (参数变化时)
  |--- SET_SETTING: STREAMING_ENABLED=0->|   停止推流（不断开 TCP）
```

- **超时**：sdrpp 连上后只对「等到 DEVICE_INFO」设了 **3000 ms** 超时（`main.cpp:248` `waitForDevInfo(3000)`，条件变量实现见 `client.cpp:63-68`）。超时只是打日志「SpyServer didn't respond with device information」，**并不主动断开**（`main.cpp:248-250`）。⚠️后续没有 keepalive/空闲超时。
- **失败处理**：TCP 读返回 ≤0 时打印 `Disconnected` 并结束回调（`client.cpp:117-120`）。
- **设备句柄/上下文**：协议**无会话 ID、无句柄概念**——TCP 连接本身即上下文。多客户端是服务端对每条 TCP 各开一份上下文（公开资料称 `maximum clients=100`）。`CanControl`(CLIENT_SYNC 字段) 表示这条连接能否下发设置。

---

## 10. 干净室最小服务端：必须支持的命令子集 + 精确字节布局

目标：让 sdrpp/SDRangel 客户端能连上、拿到设备信息、开推流、收到 IQ。下面是**最小闭环**需要实现的服务端侧行为（均为小端 u32）。

### 10.1 必须能读（客户端→服务端）

| # | 帧 | 精确字节 | 服务端该做什么 |
|---|---|---|---|
| R1 | HELLO | `u32 cmd=0` + `u32 bodyLen` + `u32 ver(0x020006A4)` + `name[]` | 校验/忽略版本与名字；随后主动回 D1+D2 |
| R2 | SET_SETTING | `u32 cmd=2` + `u32 bodyLen=8` + `u32 setting` + `u32 value` | 按下表 setting 子号处理 |

SET_SETTING 里**最小闭环只需认这几个子号**：

| setting | value | 服务端动作 |
|---|---|---|
| 100 IQ_FORMAT | 1/2/4 | 决定后续推哪种 IQ 帧（UInt8/Int16/Float） |
| 102 IQ_DECIMATION | k | 抽取 2^k，输出采样率=MaxSR>>k |
| 101 IQ_FREQUENCY | Hz | 调谐中心频率 |
| 0 STREAMING_MODE | 1 | 只推 IQ（本项目先不做 FFT/AF） |
| 1 STREAMING_ENABLED | 1 | 开始推 IQ；0 = 停止推流（保持连接） |
| 2 GAIN | index | 设增益（最小实现可先存着不生效） |

> 可选：103 IQ_DIGITAL_GAIN（最小闭环可先忽略）。

### 10.2 必须能写（服务端→客户端）

| # | 消息 | 精确字节布局 |
|---|---|---|
| D1 | DEVICE_INFO | `u32 protoID` + `u32 msgType=0` + `u32 streamType=0` + `u32 seq=0` + `u32 bodySize=48` + **48 字节设备信息**（§7.1 表，12 个 u32） |
| D2 | CLIENT_SYNC | `u32 protoID` + `u32 msgType=1` + `u32 streamType=0` + `u32 seq` + `u32 bodySize=36` + **36 字节**（§7.2 表，9 个 u32；CanControl 可填 1） |
| D3 | IQ 帧 | `u32 protoID` + `u32 msgType=(gain<<16)\|101` + `u32 streamType=1` + `u32 seq++` + `u32 bodySize` + **交错 I/Q body**（§8） |

> 所有多字节字段一律**小端**。`protoID`（消息头第 0 个 u32）客户端不校验——⚠️填什么未确认（可填协议版本 `0x020006A4` 试试，但客户端不看）。`streamType` 客户端不解析，填 0/1 均可。

最小闭环字节量：握手读 17~21 字节；回 20+48 与 20+36；然后按客户端设置循环推 `20 + body` 的 IQ 帧。

---

## 11. 可落地功能清单（MBDSDR）

### 11.1 最小闭环（MVP，必做）
1. TCP 监听 :5555。
2. 读 HELLO（忽略版本/名字）→ 回 DEVICE_INFO(0) + CLIENT_SYNC(1)。
3. 循环读命令帧，认 SET_SETTING(2)：至少处理 IQ_FORMAT / IQ_DECIMATION / IQ_FREQUENCY / STREAMING_MODE / STREAMING_ENABLED。
4. 收到 STREAMING_ENABLED=1 后，按所选格式把本机 SDR 的 IQ 打成 `MsgHeader + 交错 I/Q` 帧持续推；=0 则停推不断连。
5. 全小端序列化；iq 格式先做 Int16(101) 或 UInt8(100) 一种即可被 sdrpp 接收。

### 11.2 扩展项（后续）
- **FFT 流**：支持 FFT_* 设置子号（200~205）+ 推 300/301 消息。⚠️但 FFT body 逐字节格式未从客户端确认，需要再抓包或找服务端资料，**暂不凭猜测实现**。
- **多客户端**：每 TCP 一上下文，借 CLIENT_SYNC 的 `CanControl` 仲裁（同时只一个可调谐）。
- **GAIN / IQ_DIGITAL_GAIN 真正生效**，以及把 MessageType 高 16 位的增益 dB 填对（兼容 sdrpp 解码）。
- **PING/PONG(3/2)** 保活。
- AF 音频流（200~203）——本项目不需要。

---

## 12. 诚实标注：哪些是推断，哪些有依据

**有双客户端互证 / 源码直接依据（高可信）：**
- 默认端口 5555、TCP 长连接（`main.cpp:298,318`；公开教程佐证）。
- 小端字节序（SDRangel 显式 `encodeUInt32`，`spyserver.h:65-71`；sdrpp 无字节交换直拷）。
- 命令头/消息头字段顺序与宽度、各枚举值（两客户端枚举一致）。
- HELLO = cmd0 + 版本 u32 + 无 NUL 名字（`client.cpp:78-89`；`tcphandler.cpp:469-478`）。
- SET_SETTING = cmd2 + 8 字节 `{setting,value}`（两客户端一致）。
- DEVICE_INFO 12 u32、CLIENT_SYNC 9 u32（两 struct 字数一致）。
- IQ body 为 I/Q 交错，UInt8 是 offset-binary 需 -128（`client.cpp:136-145`）。
- 采样率经 IQ_DECIMATION 隐式推导（`main.cpp:272-277` + SDRangel 注释）。

**⚠️从客户端行为反推、服务端侧未直接确认：**
- 消息头第 0 个 u32 `ProtocolID` 服务端到底填什么（客户端不校验，两客户端都不读它）。
- PING(3) 的 body 布局（客户端从不发，无法观测）。
- IQ_DIGITAL_GAIN 的 Value 是定点还是浮点——sdrpp 把 `computeDigitalGain` 算出的浮点直接赋给 `uint32_t Value` 字段（`client.cpp:136`），类型其实是错配，线上实际字节含义存疑。
- 高 16 位=增益 dB 仅 sdrpp 一家解读，SDRangel 忽略；不是被双方共同依赖的行为。
- 版本不匹配时服务端是否主动断开；除 3s 等设备信息外有无 keepalive。
- FFT 流（300/301）body 的逐字节格式——**未确认，不实现**。

**明确没有的东西（别多做）：**
- 没有独立的「设置采样率」「设置带宽」命令。
- 没有应用层流控/ACK。
- 没有会话句柄/会话 ID。
