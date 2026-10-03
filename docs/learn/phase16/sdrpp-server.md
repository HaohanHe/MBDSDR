# Wave1-B 精读笔记：SDR++ 无头/远程服务平台（最高优先）

> 阶段：Phase16 · Wave1 Batch B（无头/远程控制 + 模块间通信）。
> 精读日期：2026-10-03。基线 ctest 104/104。
> **许可证边界**：`repos/sdrpp/**` 为 **GPLv3**（`repos/sdrpp/license` 首行 `GNU GENERAL PUBLIC LICENSE Version 3`）。本笔记只摘录机制性短片段并标注出处，**不复制代码、不把 GPL 源码入库**；MBDSDR 自有代码为 MIT，干净室重写。
> 本笔记只写事实与设计要点，不改生产代码、不 commit/push。

---

## 0. TL;DR（最重要的三个反直觉结论）

1. **SDR++ 自带的 `server.cpp` 不是一个"遥控器"，而是一个"远程 IQ 探头"。** 它在 server 模式下**只加载文件名含 `source` 的模块**（`server.cpp:101`、`server.cpp:121`），不加载任何解码/录音/扫描模块；它把源采到的原始/压缩基带 IQ 推给客户端，**调谐/模式/带宽/静噪/录音/扫描/VFO 中，只有"调中心频率"是它自己协议里的一条命令**。其余能力（模式/带宽/静噪/CTCSS/录音）根本不在这条网络线上，而是走另一条**进程内总线 `module_com`**，再由 `rigctl_server`（Hamlib rigctld）/`frequency_manager` 等模块翻译成远程协议。
2. **它的"无头 UI"是把整套 ImGui 控件树序列化后搬到远端渲染**（`SmGui::DrawList` + `COMMAND_GET_UI`/`COMMAND_UI_ACTION`）。即所谓 headless 仍然在服务端跑完整 GUI 控件树，只是不画到屏幕、而是画成字节流传给客户端——**与 MBDSDR 想要的"不经 QWidget 直接操控真实引擎"正好相反**。
3. **MBDSDR 自己的 `SpectrumEngine` 其实已经有了一套比 SDR++ server 更干净、更全的控制槽**（gain/squelch/VFO/AGC/采样率全都有，`spectrum_engine.h:82-163`），但 C++ AI 工具注册表（`agent_tools.cpp:236-245`）只暴露了 8 个，远程面（`spyserver_server.h:37-49`）只有 tune/gain/queryInfo 三个回调。**Wave2 的活主要是"把已有槽位接线到一个与 GUI 解耦的命令分发层"，而不是新发明 DSP。**

---

## 1. 真读：file:line + 注释性短片段（GPLv3，仅机制摘录）

### 1.1 线协议结构（`core/src/server_protocol.h`，GPLv3）

```cpp
// server_protocol.h:6
#define SERVER_MAX_PACKET_SIZE  (STREAM_BUFFER_SIZE * sizeof(dsp::complex_t) * 2)

// server_protocol.h:9-18  包类型（网络字节流的第一字段）
enum PacketType {
    PACKET_TYPE_COMMAND,            // C2S 命令
    PACKET_TYPE_COMMAND_ACK,        // S2C 命令应答
    PACKET_TYPE_BASEBAND,           // S2C 原始 IQ
    PACKET_TYPE_BASEBAND_COMPRESSED,// S2C zstd 压缩 IQ
    PACKET_TYPE_VFO,                // 已声明但 server.cpp 从未发送（预留/未完成）
    PACKET_TYPE_FFT,                // 同上，预留
    PACKET_TYPE_ERROR
};

// server_protocol.h:20-34  命令字。0x00.. 为 C2S，0x80.. 为 S2C
enum Command {
    COMMAND_GET_UI = 0x00,   // C2S 拉取整棵控件树
    COMMAND_UI_ACTION,       // C2S 点/改某个控件（真正的"万能控制"通道）
    COMMAND_START,            // C2S 启动源
    COMMAND_STOP,             // C2S 停止源
    COMMAND_SET_FREQUENCY,    // C2S 调中心频率（唯一直接动 DSP 的 C2S 写命令）
    COMMAND_GET_SAMPLERATE,   // C2S【已声明但 commandHandler 未实现 → 落到 ERROR_INVALID_COMMAND】
    COMMAND_SET_SAMPLE_TYPE,  // C2S 设 IQ 采样位宽（I8/I16）
    COMMAND_SET_COMPRESSION,  // C2S 开/关 zstd
    // Server to client
    COMMAND_SET_SAMPLERATE = 0x80,  // S2C 主动通知采样率变化
    COMMAND_DISCONNECT              // S2C 拒绝/踢线
};

// server_protocol.h:36-41
enum Error { ERROR_NONE=0, ERROR_INVALID_PACKET, ERROR_INVALID_COMMAND, ERROR_INVALID_ARGUMENT };

// server_protocol.h:43-52  #pragma pack(push,1) —— 定长二进制头，无 JSON
struct PacketHeader { uint32_t type; uint32_t size; };   // type=PacketType, size=整包字节数
struct CommandHeader { uint32_t cmd; };                  // cmd=Command
```

**要点**：协议是**紧凑二进制（`#pragma pack(1)`），不是 JSON**。每包 = `PacketHeader{type,u32 size}` + （命令包再跟 `CommandHeader{cmd,u32}`）+ 负载。`size` 是整包长度，客户端靠它分帧。

### 1.2 服务端启动 / 监听 / 会话（`core/src/server.cpp`，GPLv3）

```cpp
// server.cpp:88   headless 仍初始化 SmGui（但以 server=true 模式，不建窗口）
SmGui::init(true);

// server.cpp:93-106   只加载“source”模块——这是“远程 IQ 探头”定性的铁证
for (const auto& file : std::filesystem::directory_iterator(modulesDir)) {
    ...
    if (fn.find("source") == std::string::npos) { continue; }   // :101  非 source 一律不加载
    core::moduleManager.loadModule(path);
}
// :113-125 对 config 里额外列出的模块做同样的 "source" 名字过滤（:121）
```

```cpp
// server.cpp:152-158   监听 + 单主会话 + 空转主循环
std::string host = (std::string)core::args["addr"];
int port = (int)core::args["port"];
listener = net::listen(host, port);          // :154  阻塞式 listener 句柄
listener->acceptAsync(_clientHandler, NULL); // :155  异步 accept
while(1) { std::this_thread::sleep_for(std::chrono::milliseconds(100)); }  // :158  无线程/事件循环，纯 sleep
```

```cpp
// server.cpp:163-201  _clientHandler：单客户端互斥会话
if (client && client->isOpen()) {            // :165  已有客户端 → 拒绝
    // :169-175  回一包 COMMAND_DISCONNECT 给新连接
    tmp_phdr->type = PACKET_TYPE_COMMAND; tmp_chdr->cmd = COMMAND_DISCONNECT;
    conn->write(tmp_phdr->size, buf);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    conn->close();
    listener->acceptAsync(_clientHandler, NULL);   // :183  继续 accept 下一个
    return;
}
client = std::move(conn);                                            // :188  独占持线
client->readAsync(sizeof(PacketHeader), rbuf, _packetHandler, NULL);// :189  先异步读 8 字节包头
sigpath::sourceManager.stop();                                       // :192  新会话复位源
comp.setPCMType(dsp::compression::PCM_TYPE_I16); compression = false; // :193-194
sendSampleRate(sampleRate);                                         // :196  主动推当前采样率
listener->acceptAsync(_clientHandler, NULL);                        // :200  再 accept（但被 :165 挡住）
```

**会话模型**：**全局单客户端**。`net::Conn client` 是进程级唯一连接（`server.cpp:19`）；第二个来的连接会被立即发 `DISCONNECT` 后关闭。没有多会话、没有鉴权、没有 TLS（注释 `:166` 里 IP 还是 `"TODO"`）。

### 1.3 分帧与命令分发（`server.cpp:203-305`，GPLv3）

```cpp
// server.cpp:203-227  _packetHandler：先读定长包头，再按需补齐 body
PacketHeader* hdr = (PacketHeader*)buf;
int goal = hdr->size - sizeof(PacketHeader);
while (len < goal) {                                  // :210  阻塞读满剩余 body
    read = client->read(goal - len, &buf[sizeof(PacketHeader) + len]);
    if (read < 0) { return; };
    len += read;
}
if (hdr->type == PACKET_TYPE_COMMAND && hdr->size >= sizeof(PacketHeader)+sizeof(CommandHeader)) {
    CommandHeader* chdr = (CommandHeader*)&buf[sizeof(PacketHeader)];
    commandHandler((Command)chdr->cmd, &buf[sizeof(PacketHeader)+sizeof(CommandHeader)],
                   hdr->size - sizeof(PacketHeader) - sizeof(CommandHeader));  // :219
} else { sendError(ERROR_INVALID_PACKET); }                          // :222
client->readAsync(sizeof(PacketHeader), rbuf, _packetHandler, NULL);  // :226  继续读下一包
```

```cpp
// server.cpp:249-305  commandHandler —— 这就是“网络远程命令全集”本体
if      (cmd == COMMAND_GET_UI)            { sendUI(COMMAND_GET_UI, "", dummyElem); }          // :250
else if (cmd == COMMAND_UI_ACTION && len>=3) {                                                   // :253
    bool sendback = data[i++]; len--;
    SmGui::DrawListElem diffId, diffValue;
    SmGui::DrawList::loadItem(diffId, &data[i], len);    // :261  反序列化“控件 id”
    SmGui::DrawList::loadItem(diffValue, &data[i], len); // :269  反序列化“控件新值”
    if (sendback) sendUI(COMMAND_UI_ACTION, diffId.str, diffValue);   // :275-277 回读新 UI
    else          renderUI(NULL, diffId.str, diffValue);              // :278-280 直接改控件
}
else if (cmd == COMMAND_START)   { sigpath::sourceManager.start(); running = true; }            // :282
else if (cmd == COMMAND_STOP)    { sigpath::sourceManager.stop();  running = false; }            // :286
else if (cmd == COMMAND_SET_FREQUENCY && len == 8) {                                             // :290
    sigpath::sourceManager.tune(*(double*)data);   // 8 字节 = double
    sendCommandAck(COMMAND_SET_FREQUENCY, 0);       // :292  显式 ACK
}
else if (cmd == COMMAND_SET_SAMPLE_TYPE && len == 1) {                                            // :294
    comp.setPCMType((dsp::compression::PCMType)*(uint8_t*)data);
}
else if (cmd == COMMAND_SET_COMPRESSION && len == 1) { compression = *(uint8_t*)data; }           // :298
else { flog::error("Invalid Command ..."); sendError(ERROR_INVALID_COMMAND); }                    // :301-304
```

### 1.4 基带 IQ 上行流（`server.cpp:229-243`，GPLv3）

```cpp
// server.cpp:229-243  _testServerHandler：DSP 链末尾的 sink 回调，把 IQ 打包上行
if (compression) {
    bb_pkt_hdr->type = PACKET_TYPE_BASEBAND_COMPRESSED;
    bb_pkt_hdr->size = sizeof(PacketHeader) +
        ZSTD_compressCCtx(cctx, &bbuf[sizeof(PacketHeader)],
                          SERVER_MAX_PACKET_SIZE-sizeof(PacketHeader), data, count, 1);  // :233
} else {
    bb_pkt_hdr->type = PACKET_TYPE_BASEBAND;   // :236  原始 I8/I16 PCM
    bb_pkt_hdr->size = sizeof(PacketHeader) + count;
    memcpy(&bbuf[sizeof(PacketHeader)], data, count);
}
if (client && client->isOpen()) client->write(bb_pkt_hdr->size, bbuf);  // :242
```

> 注：这里的 IQ 是**已被 `SampleStreamCompressor` 压成 I8/I16 PCM 的基带采样**，不是浮点复数；压缩是可选的 zstd。`backend.h`（仅 12 行：`init/beginFrame/render/renderLoop/end`）是桌面 GL/GLFW 窗口后端，**server 模式根本不走它**（server 自己 `while(1) sleep`，`server.cpp:158`）。

### 1.5 模块间通信总线 `module_com`（`module_com.{h,cpp}`，GPLv3）

```cpp
// module_com.h:6-10  一个“具名接口” = 模块名 + 函数指针 + ctx
struct ModuleComInterface {
    std::string moduleName;
    void* ctx;
    void (*handler)(int code, void* in, void* out, void* ctx);
};
// module_com.h:20-22  全局唯一表，带递归锁
std::recursive_mutex mtx;
std::map<std::string, ModuleComInterface> interfaces;
```

```cpp
// module_com.cpp:4-16   注册：name 必须全局唯一，否则报错返回 false
bool ModuleComManager::registerInterface(std::string moduleName, std::string name,
        void (*handler)(int code, void* in, void* out, void* ctx), void* ctx) {
    std::lock_guard<std::recursive_mutex> lck(mtx);
    if (interfaces.find(name) != interfaces.end()) { ...; return false; }
    interfaces[name] = {moduleName, handler, ctx};  return true;
}
// module_com.cpp:43-52  调用：进程内同步直调，无序列化、无网络、无队列
bool ModuleComManager::callInterface(std::string name, int code, void* in, void* out) {
    std::lock_guard<std::recursive_mutex> lck(mtx);
    if (interfaces.find(name) == interfaces.end()) { ...; return false; }
    ModuleComInterface iface = interfaces[name];
    iface.handler(code, in, out, iface.ctx);   // :50  同步回调
    return true;
}
```

全局实例：`core/src/core.cpp:38  ModuleComManager modComManager;`，声明于 `core/src/core.h:10`（`SDRPP_EXPORT`）。调用方统一写 `core::modComManager.callInterface(...)`。

---

## 2. 机制总结

### 2.1 服务端如何监听 / 接受会话

| 环节 | 事实 | file:line |
|---|---|---|
| 入口 | `server::main()` 独立入口（不是 GUI main），打 `===== SERVER MODE =====` | `server.cpp:49-50` |
| DSP 预热 | `SampleStreamCompressor` + `Handler<uint8_t>` sink 接 `dummyInput`，`comp.start()/hnd.start()` | `server.cpp:53-59` |
| 模块加载 | 只加载文件名含 `source` 的模块（**解码器/录音/扫描一律不加载**） | `server.cpp:101,121` |
| 选源 | 从 config 读 `source`，`sigpath::sourceManager.selectSource(...)` | `server.cpp:148-149` |
| 监听 | `net::listen(host,port)` + `acceptAsync(_clientHandler)` | `server.cpp:154-155` |
| 会话 | **进程级单客户端** `net::Conn client`；第二连接发 `COMMAND_DISCONNECT` 后关闭 | `server.cpp:19,165-185` |
| 新会话复位 | 停源、复位 PCM 为 I16、关压缩、主动 `sendSampleRate` | `server.cpp:192-196` |
| 主循环 | 无线事件循环，纯 `sleep_for(100ms)` 空转；IO 全靠 `readAsync`/回调 | `server.cpp:158` |

### 2.2 协议如何编解码（结论：紧凑二进制，非 JSON）

- **帧格式**：`PacketHeader{ u32 type, u32 size }`（packed，8 字节）开头；命令包其后紧跟 `CommandHeader{ u32 cmd }`（4 字节）；再后是负载。`size` = 整包字节数，用于分帧。`server_protocol.h:43-52`。
- **分帧**：先异步读 8 字节包头 → 按 `size` 阻塞读满 body → 派发 → 再 `readAsync` 下一包。`server.cpp:189,203-227`。
- **负载类型即编码**：
  - `PACKET_TYPE_COMMAND`：body 是 `CommandHeader` + 二进制参数（如频率是裸 `double` 8 字节，`server.cpp:290`）。
  - `PACKET_TYPE_BASEBAND[_COMPRESSED]`：body 是 I8/I16 PCM，可选 zstd 压缩（`server.cpp:229-243`）。
  - **UI 控件状态**不是 JSON，而是 `SmGui::DrawList` 的自序列化字节流（`DrawList::loadItem/store`，`server.cpp:261,269,349`）。
- **应答**：写命令用 `sendCommandAck`（`PACKET_TYPE_COMMAND_ACK`，`server.cpp:383-386`）；错误用单字节 `Error` 放进 `PACKET_TYPE_ERROR`（`server.cpp:355-359`）。

### 2.3 完整远程命令集（逐条 file:line，读 vs 写语义）

#### A. 网络线 `server.cpp` 命令（C2S / S2C）

| 命令 | 方向 | 参数/负载 | 返回 | 读/写语义 | file:line |
|---|---|---|---|---|---|
| `COMMAND_GET_UI` (0x00) | C→S | 无 | `COMMAND_ACK` 内整棵控件树 drawlist | **读**（拉 UI 状态） | `server.cpp:250-252` |
| `COMMAND_UI_ACTION` | C→S | `u8 sendback` + DrawListElem(id) + DrawListElem(value) | 可选回新 UI；无显 ACK | **写**（改某个控件值，万能后门） | `server.cpp:253-281` |
| `COMMAND_START` | C→S | 无 | 无显 ACK | **写**（启动源 `sourceManager.start()`） | `server.cpp:282-285` |
| `COMMAND_STOP` | C→S | 无 | 无显 ACK | **写**（停源） | `server.cpp:286-289` |
| `COMMAND_SET_FREQUENCY` | C→S | 裸 `double`（恰好 8 字节，`len==8`） | `COMMAND_ACK` | **写**（`sourceManager.tune(double)`） | `server.cpp:290-293` |
| `COMMAND_GET_SAMPLERATE` | C→S | （声明了） | **未实现 → 落到 `ERROR_INVALID_COMMAND`** | 读（空壳） | 枚举 `server_protocol.h:27`；无 handler |
| `COMMAND_SET_SAMPLE_TYPE` | C→S | `u8`（PCMType I8/I16） | 无 | **写**（压缩器位宽） | `server.cpp:294-297` |
| `COMMAND_SET_COMPRESSION` | C→S | `u8`（0/1） | 无 | **写**（zstd 开关） | `server.cpp:298-300` |
| `COMMAND_SET_SAMPLERATE` (0x80) | S→C | 裸 `double` | — | 服务端**主动推**采样率 | `server.cpp:361-364` |
| `COMMAND_DISCONNECT` (0x81) | S→C | 无 | — | 踢线/拒绝 | `server.cpp:174` |
| `PACKET_TYPE_ERROR` | S→C | `u8 Error` | — | 异常通道 | `server.cpp:355-359` |

> **关键结论**：在 SDR++ 自带网络线上，**调谐=唯一直接 DSP 写命令；模式/带宽/增益/静噪/录音/扫描/VFO 一条都没有**。它们要么靠 `UI_ACTION` 去戳 source 模块的控件（增益/采样率这些**源**参数），要么压根不在 server 进程里（解码在客户端）。

#### B. 进程内 `module_com` 命令（radio 解码控制面）

radio 模块按 **VFO 名**注册一个接口（`decoder_modules/radio/src/radio_module.h:134` `registerInterface("radio", name, moduleInterfaceHandler, this)`）。命令码见 `radio_interface.h:3-16`，分派见 `radio_module.h:780-842`：

| 命令码 | 入参 in | 出参 out | 语义 / gate | file:line |
|---|---|---|---|---|
| `RADIO_IFACE_CMD_GET_MODE` | 忽略 | `int*`（demodID） | **读** | `radio_module.h:787-790` |
| `RADIO_IFACE_CMD_SET_MODE` | `int*`(DemodID) | 忽略 | **写**，gate：`in && _this->enabled` | `radio_module.h:791-794` |
| `RADIO_IFACE_CMD_GET_BANDWIDTH` | 忽略 | `float*` | **读** | `:795-798` |
| `RADIO_IFACE_CMD_SET_BANDWIDTH` | `float*` | 忽略 | **写**，gate：`enabled && !bandwidthLocked` | `:799-803`（锁在 `:801`） |
| `RADIO_IFACE_CMD_GET_SQUELCH_MODE` | 忽略 | `SquelchMode*` | **读** | `:804-807` |
| `RADIO_IFACE_CMD_SET_SQUELCH_MODE` | `SquelchMode*` | 忽略 | **写**，gate `enabled` | `:808-811` |
| `RADIO_IFACE_CMD_GET_SQUELCH_LEVEL` | 忽略 | `float*` dB | **读** | `:812-815` |
| `RADIO_IFACE_CMD_SET_SQUELCH_LEVEL` | `float*` | 忽略 | **写**，gate `enabled` | `:816-819` |
| `RADIO_IFACE_CMD_GET_CTCSS_TONE` | 忽略 | `CTCSSTone*` | **读** | `:820-823` |
| `RADIO_IFACE_CMD_SET_CTCSS_TONE` | `CTCSSTone*` | 忽略 | **写**，gate `enabled` | `:824-827` |
| `RADIO_IFACE_CMD_GET_HIGHPASS` | 忽略 | `bool*` | **读** | `:828-831` |
| `RADIO_IFACE_CMD_SET_HIGHPASS` | `bool*` | 忽略 | **写**，gate `enabled` | `:832-835` |

模式枚举 `radio_interface.h:18-27`：`NFM/WFM/AM/DSB/USB/CW/LSB/RAW`。

recorder 接口（`misc_modules/recorder/src/main.cpp:563-580`）：`RECORDER_IFACE_CMD_GET_MODE/SET_MODE/START/STOP`（`:566/570/575/578`），由 `frequency_manager`、`rigctl_server`、`meteor` 等通过 `callInterface(name, RECORDER_IFACE_CMD_START, NULL, NULL)` 驱动（`rigctl_server/src/main.cpp:556,578`）。

#### C. 真正的"远程遥控器"是 rigctl_server（Hamlib rigctld 桥）

SDR++ 自己的网络线不做调谐/模式/VFO，但 `misc_modules/rigctl_server` 讲 Hamlib rigctld 文本协议，再翻译成上面的 `module_com` 调用：

| rigctld 命令 | 含义 | 内部动作 | file:line |
|---|---|---|---|
| `F` / `\set_freq` | 设频率 | （源调谐） | `rigctl_server/.../main.cpp:388` |
| `f` / `\get_freq` | 读频率 | — | `:411` |
| `M` / `\set_mode` | 设模式+带宽 | `callInterface(vfo, RADIO_IFACE_CMD_SET_MODE/SET_BANDWIDTH)` | `:427,471-473` |
| `m` / `\get_mode` | 读模式 | `GET_MODE` | `:479,486` |
| `V` / `\set_vfo` | 切 VFO | — | `:501` |
| `v` / `\get_vfo` | 读 VFO | — | `:521` |

> 即 SDR++ 的"无头全操控"实际是三段拼起来的：**二进制 IQ 流（server.cpp）+ rigctld 文本遥控（rigctl_server）+ 进程内 module_com 总线**。三者解耦，各管一摊。

### 2.4 module_com 模块间通信机制（小结）

- **本质**：进程内、同步、函数指针式"服务定位器"。`name → {moduleName, ctx, handler(code,in,out,ctx)}`。
- **路由键**：接口名。radio 用 **VFO 名**（多 VFO 各自注册一个），recorder 用 recorder 实例名。
- **读写约定**：GET_* 只填 `out`（in 传 NULL）；SET_* 只吃 `in`（out 传 NULL），且 handler 内部 gate `_this->enabled`（`radio_module.h:791,799,...`）——**这就是"读写分离 + 写可 gate"的现成范本**。
- **线程**：整表一把 `std::recursive_mutex`（`module_com.h:21`），调用是同步直调；跨线程安全由这把锁兜底，但 handler 本身在调用者线程执行。
- **发现方式**：`interfaceExists(name)` + `getModuleName(name)` 探活（如 `frequency_manager/main.cpp:114-115` 先确认是 "radio" 再 SET）。

---

## 3. MBDSDR 现状对照（读真实代码，file:line）

### 3.1 C++ AI 工具注册表（`cpp/src/ai/agent_tools.cpp`，MIT）

分发表 `dispatchTable()`（`agent_tools.cpp:235-247`）共 **8 个工具**，全部直接打 `SpectrumEngine*`：

| 工具名 | executor | 打到引擎的槽 | 读/写 | file:line |
|---|---|---|---|---|
| `tune_frequency` | `execTuneFrequency` | `engine->onSetCenterFreq(f)` | 写 | `:66-76`（调用 `:69`） |
| `set_mode` | `execSetMode` | `engine->setDemodMode(m)` | 写 | `:78-88`（`:81`） |
| `start_recording` | `execStartRecording` | `engine->startRecording()`（带回 path） | 写 | `:90-107`（`:96`） |
| `stop_recording` | `execStopRecording` | `engine->stopRecording()` | 写 | `:109-117`（`:111`） |
| `scan_band` | `execScanBand` | `engine->scanBand(low,high,step,&peak)` | 写（扫频会改频率，见 schema 注释） | `:119-148`（`:125`） |
| `set_bandwidth` | `execSetBandwidth` | `engine->setBandwidth(bw)` | 写 | `:150-160`（`:153`） |
| `get_status` | `execGetStatus` | 读 `centerFreq()/demodMode()/bandwidth()` | 读 | `:162-183`（`:173-175`） |
| `predict_passes` | `execPredictPasses` | 不动无线电（TLE 预测） | 读 | `:185-223` |

执行入口 `executeTool(name,args,engine)`（`:276-287`）：按名字线性查 `dispatchTable`，命中则调 `d.exec(args,engine,src)`，未命中返回 `"未知工具: ..."`（`:286`，诚实报错）。

### 3.2 读写 gate（声明式单一真源）

- 写门判定 `isWriteTool(name)`（`agent_tools.cpp:250-256`）：**不另维护一份写集合**，而是去查声明式 schema `ToolSchemaSpec::write`；未知名字默认 `false`（永不 gate）。
- 声明表 `registeredToolSpecs()`（`tool_schema.cpp:56-205`）逐工具标 `s.write = true/false`：
  - `tune_frequency` write（`:64`），`freq_hz` 用 `tokens::kFreqMinHz/kFreqMaxHz` 做 min/max（`:69-70`）。
  - `set_mode` write（`:81`），enum `{AM,NFM,WFM,USB,LSB,CW}`（`:85`）。
  - `start_recording`/`stop_recording` write（`:96,105`），无参。
  - `scan_band` write（`:114`，注释明说"扫频会改变频率"），low/high 用频率 token 边界（`:119-127`），step 下限本地常量 `kScanStepMinHz=1.0`（`:17,133`）。
  - `set_bandwidth` write（`:144`），带宽用具名 preset enum `kBwAm/Nfm/Ssb/Cw/Digital/Wfm/Adsb Hz`（`:151-154`）。
  - `get_status`（`:161-166`）、`predict_passes`（`:172-202`）**不写 write 字段 → 默认 false = 只读**。
- gate 输出 `gatedToolResult(name)`（`agent_tools.cpp:16-22`）：固定返回 `{ok:false, gated:true, error:"手动模式：未执行 X"}`。
- **强制点在 `llm_worker.cpp:17-24`**：
  ```cpp
  // llm_worker.cpp:21-24
  if (manualMode && isWriteTool(name)) {
      return gatedToolResult(name);   // 手动模式下写工具一律不碰引擎
  }
  return executeTool(name, args, engine);
  ```
  `manualMode_` 由 `Agent::setManualMode` 持久化到 QSettings 并下发 worker（`agent.cpp:42-46`）。

### 3.3 引擎控制槽（`cpp/src/dsp/spectrum_engine.h`，MIT）

`SpectrumEngine : public QThread`（`:42`），控制接口集中在 `public slots:`（`:81`）。**已存在但尚未接到任何远程/AI 命令面的槽**（这是 Wave2 接线的目标）：

| 引擎槽（已存在） | 作用 | 现状是否暴露给远程/AI | file:line |
|---|---|---|---|
| `onSetCenterFreq(double)` | 调中心频率 | ✅ 已接（tune_frequency） | `:82` |
| `onSetSampleRate(double)` | 设采样率 | ❌ 未暴露 | `:83` |
| `onSetGain(double)` | 设增益 dB | ❌ AI 未暴露；仅 SpyServer tuner | `:84` |
| `setDemodMode(QString)` | 设模式 | ✅ 已接 | `:85` |
| `setSquelchThreshold(float)` | 静噪阈值 | ❌ 未暴露 | `:86` |
| `setSquelchEnabled(bool)` | 静噪开关 | ❌ 未暴露 | `:87` |
| `setBandwidth(double)` | 带宽 | ✅ 已接 | `:88` |
| `startRecording()/stopRecording()` | 录 IQ | ✅ 已接 | `:89-91` |
| `scanBand(low,high,step,&peak)` | 扫频段 | ✅ 已接 | `:75-76` |
| `sourceCapabilities()` / `availableGainsDb()` | 能力/增益步进回读 | ⚠️ 仅 UI/工具内嵌 sourceInfo | `:117,123` |
| `vfoAdd/vfoRemove/vfoSelect/vfoSetFreq/vfoSetOffset/vfoSetBandwidth/vfoSetMode` | 多 VFO | ❌ 完全未暴露 | `:145-160` |
| `vfoMarkers()/selectedVfoId()` | VFO 状态回读 | ❌ 未暴露 | `:162-163` |
| `setTunerAgc/setRtlAgc/setDirectSampling/setOffsetTuning` | 前端 AGC/直采 | ❌ 未暴露 | `:206-212` |
| `sourceTelemetry(name,connected,centerHz,srHz,gainDb)` 信号 | ~1Hz 真实回读（含增益） | ⚠️ 仅 UI 订阅，未做命令式回读 | `:271-272` |
| `squelchState(bool)` / `recordingStateChanged(bool,path)` 信号 | 静噪/录音状态推送 | ⚠️ 仅 UI | `:286-287` |

**线程/解耦事实**：`spectrum_engine.h` 不 include 任何 `QWidget`/`dialog`/`mainwindow` 头（已 grep 验证），控制槽是纯 C++ slot，引擎自身是 `QThread`。即"不经 QWidget 直接调引擎槽"在技术上已成立。

### 3.4 现有远程面：`spyserver_server`（MIT）

MBDSDR 没有照搬 SDR++ 的二进制线，而是实现了 **SpyServer 协议**的服务端（`cpp/src/dsp/spyserver_server.{h,cpp}`）。它对客户端暴露的控制回调只有 `SpyServerTuner`（`spyserver_server.h:37-49`）：

```cpp
// spyserver_server.h:37-49
struct SpyServerTuner {
    std::function<void(double hz)>            setCenterFreq;  // :39  客户端遥控调谐
    std::function<void(double db)>            setGain;        // :41  客户端遥控增益
    std::function<void(double& maxSr, double& minF, double& maxF,
                       double& center, double& gainDb)> queryInfo; // :46-48 握手/重同步时读真实状态
};
```
服务端能力（`spyserver_server.h:61-67`）：`start(port)/stop/isListening/port/clientCount`（单客户端 0/1，`:66`）；命令分派 `dispatchCommand(cmd,body)` / `onSetSetting(setting,value)`（`:100-101`）；IQ 推送 `feedIQ(...)`（`:78`）经 `iqTapRequired(bool)` 信号反向请求引擎开 tap。**注释明说回调"由 UI 接到 SpectrumEngine 槽"（`:34-36`），且全部跑在 GUI 线程（`:36,71`）**——这就是要被无头层拆掉的"经过 GUI 接线"那一环。

---

## 4. 差距判定表 + 「MBDSDR 无头控制层」干净室设计要点

### 4.1 差距判定表

| 能力 | SDR++ 机制（file:line） | MBDSDR 现状（file:line） | 判定 |
|---|---|---|---|
| 监听/单会话 | `net::listen`+`acceptAsync`，单客户端拒绝 `DISCONNECT` `server.cpp:154-201` | `SpyServerServer::start/stop/clientCount` 单客户端 `spyserver_server.h:61-67` | **已实现且真实**（协议不同，能力等价） |
| 线协议 | packed 二进制头 `server_protocol.h:43-52` | SpyServer 协议（20 字节消息头） `spyserver_server.h:103` | **已实现且真实**（选了事实标准 SpyServer，优于自造） |
| IQ 上行 | PCM I8/I16 + zstd `server.cpp:229-243` | `feedIQ` 抽 + decimate `spyserver_server.h:78`，引擎 `iqTapRequested_` `spectrum_engine.h:248` | **已实现且真实** |
| 调谐 | 网络命令 `SET_FREQUENCY` `server.cpp:290-293` | 工具 `tune_frequency`→`onSetCenterFreq` `agent_tools.cpp:69`；SpyServer `setCenterFreq` `spyserver_server.h:39` | **已实现且真实**（两条路径都有） |
| 模式 | module_com `SET_MODE` `radio_module.h:791` + rigctld `M` `rigctl:427` | 工具 `set_mode`→`setDemodMode` `agent_tools.cpp:81`；引擎槽在 `:85` | **已实现且真实**（但仅 AI 路径，远程线没有） |
| 带宽 | module_com `SET_BANDWIDTH` `radio_module.h:799` | 工具 `set_bandwidth`→`setBandwidth` `agent_tools.cpp:153`；槽 `:88` | **已实现且真实**（仅 AI 路径） |
| 增益 | source 模块控件（UI_ACTION）；rigctld 无直接增益命令 | 引擎 `onSetGain` `:84` + SpyServer `setGain` `spyserver_server.h:41`；**AI 工具面无 set_gain** | **缺深度**：引擎/远程有，AI/命令面缺 |
| 静噪 | module_com `SET_SQUELCH_*` `radio_module.h:808,816` | 引擎 `setSquelchThreshold/Enabled` `:86-87`；**无任何远程/AI 工具** | **未实现（命令面）**：引擎槽已在，没接线 |
| 录音 | module_com `RECORDER_IFACE_CMD_START/STOP` `recorder:575/578` | 工具 start/stop_recording `agent_tools.cpp:96,111`；引擎 `:89-90` | **已实现且真实** |
| 扫描 | frequency_manager（定时/预置，非线命令） | 工具 `scan_band`→`scanBand` `agent_tools.cpp:125`；引擎 `:75` | **MBDSDR 更强**（已有真实扫峰） |
| VFO | module_com 按 VFO 名路由 `radio_module.h:134`；rigctld `V/v` `:501,521` | 引擎完整 VFO API `:145-163`；**无任何远程/AI 命令** | **未实现（命令面）**：引擎全套已在，没接线 |
| 状态回读 | `GET_UI` 拉控件树；rigctld `f/m/v` | 工具 `get_status` 聚合回读 `agent_tools.cpp:162-183`；引擎异步信号 `sourceTelemetry` `:271` | **已实现但浅**：单条 get_status 聚合，缺分项读 |
| 读写分离/写 gate | module_com GET(out)/SET(in) + `enabled` gate `radio_module.h:787-835` | `isWriteTool`+`gatedToolResult`+`manualMode` `agent_tools.cpp:16,250` / `llm_worker.cpp:21` | **已实现且真实**（思想一致，可复用） |
| 不经 GUI 操控 | SDR++ 反而是"把 GUI 序列化到远端" `server.cpp:250-353` | 引擎槽纯 C++/QThread、无 QWidget 依赖（已验证） | **MBDSDR 有更好的底子**：引擎本就可脱离 GUI |
| 鉴权/多客户端 | 无鉴权、单客户端（IP 还是 TODO `server.cpp:166`） | 单客户端、无鉴权 `spyserver_server.h:66` | 诚实空态：都没做，云内可后续加 |

### 4.2 「MBDSDR 无头控制层」干净室设计要点（供 Wave2 落地）

**定位**：在 `dsp::SpectrumEngine`（已存在、无 QWidget 依赖）之上，加一层**与 GUI 解耦的命令分发器**。它不是 SDR++ 的"远程 GUI"，而是一组**具名命令 → 直接打引擎槽**的窄接口；GUI、AI worker、SpyServer 服务端三者都只是它的"前端"。

**(1) 与 GUI 解耦（核心红线）**
- 新增一个纯 C++ 类（建议 `mbdsdr::headless::ControlHub` 或复用现有 `agent_tools` 的分发思想抽出），**只依赖 `SpectrumEngine*` 与数据结构，不 include 任何 `QWidget`/`main_window`/设置对话框**。
- 引擎槽本就是 `public slots:` 且引擎是 `QThread`；跨线程调用用 `QMetaObject::invokeMethod(engine, ..., Qt::QueuedConnection)` 投递到引擎 home 线程（参照 `agent.cpp:106` 对 worker 的做法），**不要**让 GUI 线程/AI 线程直接裸调会抢 `sourceMutex_` 的 VFO 方法（`spectrum_engine.h:141` 注释明示 VFO 方法拿 `sourceMutex_`）。
- SpyServer 现有的"由 UI 接线 tuner 回调"（`spyserver_server.h:34-36`）改为**直接 `ControlHub::setTunerCallbacks(...)`**，不再经 `main_window`。

**(2) 命令 → 真实引擎（接线点清单，全部已存在槽，勿重写 DSP）**

| 建议命令名 | 读写 | 直接接线到（已存在） | file:line |
|---|---|---|---|
| `tune` / `get_frequency` | 写/读 | `onSetCenterFreq` / `centerFreq()` | `:82` / `:54` |
| `set_mode` / `get_mode` | 写/读 | `setDemodMode` / `demodMode()` | `:85` / `:52` |
| `set_bandwidth` / `get_bandwidth` | 写/读 | `setBandwidth` / `bandwidth()` | `:88` / `:53` |
| `set_gain` / `get_gain` / `list_gains` | 写/读 | `onSetGain` / `sourceTelemetry` 回读 / `availableGainsDb()` | `:84` / `:271` / `:123` |
| `set_squelch` / `get_squelch` | 写/读 | `setSquelchEnabled`+`setSquelchThreshold` / `squelchState` 信号 | `:86-87` / `:286` |
| `start` / `stop`（源启停） | 写 | （对齐 SDR++ `COMMAND_START/STOP`） | 引擎侧确认 `tryConnectRtl/disconnectSource` `:77-78` |
| `start_recording` / `stop_recording` / `get_recording_state` | 写/读 | `startRecording/stopRecording` / `recordingStateChanged` 信号 | `:89-90` / `:287` |
| `scan_band` | 写（扫频） | `scanBand(low,high,step,&peak)` | `:75-76` |
| `vfo_add/vfo_remove/vfo_select/vfo_tune/vfo_set_mode/vfo_set_bandwidth` / `get_vfos` | 写/读 | `vfoAdd/.../vfoSetMode` / `vfoMarkers()+selectedVfoId()` | `:145-163` |
| `set_agc` / `set_sample_rate` | 写 | `setTunerAgc/setRtlAgc` / `onSetSampleRate` | `:208-209` / `:83` |
| `get_status`（聚合） | 读 | `centerFreq/demodMode/bandwidth` + `sourceCapabilities` | `:52-54,117` |

> 设计取舍：SDR++ 把"分项读"塞进 `GET_UI` 控件树或 rigctld `f/m/v`；MBDSDR 应**为每个写命令配一个对称的读命令**（`set_*`↔`get_*`），而不是只留一个聚合 `get_status`——这是比 SDR++ 更干净的地方，也便于 ctest 做"写后回读"断言。

**(3) 读写分离 / 写可 gate（复用已落地思想，勿另起一套）**
- 直接复用 `ToolSchemaSpec::write` 声明式单一真源（`tool_schema.cpp` 各 `s.write=`）+ `isWriteTool()`（`agent_tools.cpp:250-256`）+ `gatedToolResult()`（`:16-22`）。ControlHub 复用同一张表，避免"两份写集合漂移"。
- 手动/gated 模式下：写命令**不碰引擎**，返回结构化 `{ok:false, gated:true, error}`；读命令（含 `get_*`、`list_gains`、`get_status`、`predict_passes`）**永远放行**——与 SDR++ module_com"GET 只填 out、SET 查 enabled"（`radio_module.h:787,791`）同一哲学。
- 未知命令诚实报错（对齐 `executeTool` 的 `"未知工具: "`，`agent_tools.cpp:286`；SDR++ 对应 `ERROR_INVALID_COMMAND`，`server.cpp:303`）。

**(4) 状态回读优先用异步信号，别轮询**
- 引擎已有 ~1Hz `sourceTelemetry(name,connected,centerHz,srHz,gainDb)`（`spectrum_engine.h:271-272`）、`squelchState`、`recordingStateChanged`。无头层可订阅这些信号维护一份"最新真实状态快照"，`get_*` 命令读快照即可——**避免每条读命令都去抢 `sourceMutex_`**（引擎注释 `:121-122` 已为此预留 `capsMutex_` 快照思路）。

**(5) 边界与诚实空态（对齐 Phase16 质量门）**
- 频率/带宽参数继续走 `core/tokens.h` 具名常量 + 弹性派生（现状 `tool_schema.cpp:69-70` 已是这么做）。
- 增益步进无真实设备时 `availableGainsDb()` 返回空（`spectrum_engine.h:118-123` 已诚实空态），无头层不得编造增益表。
- 合成测试信号下，扫描/回读结果必须像 `agent_tools.cpp:142-146` 那样打 `synthetic:true` 标签。
- ctest（offscreen）覆盖：命令确定性执行、写后回读、未知命令报错、写门 gate；**只用合成信号夹具，不碰真机**。

**(6) 不要做的事（避免重蹈 SDR++ 的复杂）**
- 不要把 GUI 控件树序列化到线上（SDR++ `SmGui::DrawList` 路线）——那是为"远程看屏"服务，不是为"无头操控"服务。
- 不要自造第二个线协议——远程传输层继续用已存在的 SpyServer（IQ 流）；ControlHub 是**进程内命令面**，未来若要网络遥控文本/JSON 命令，再在 SpyServer 之外薄包一层即可，二者解耦。
- GPL 机制只学不抄：命名/结构/常量全自有（MIT），尤其不要把 `PacketHeader/CommandHeader` 字节布局或 `SmGui::DrawList` 格式照搬进 MBDSDR。

---

## 5. 未读透清单（如实）

1. **`SmGui::DrawList` 序列化细节未逐行读**（`core/src/gui/smgui.{h,cpp}`，约 34KB）。本文只从 `server.cpp:261/269/349` 的 `loadItem/store` 调用点推断 UI_ACTION 是"控件 id + 新值"的自序列化流；**每种控件（Combo/Slider/Checkbox）的具体字节编码未展开**。若 Wave2 需要兼容 SDR++ 客户端才需补读；MBDSDR 走自有命令面，不依赖它。
2. **`net::Conn/Listener`（`utils/networking.h`）未读**：TCP 还是 UDP、收发缓冲、超时（`server.cpp:206` 自己都注释 `TODO: ADD TIMEOUT`）未确认。
3. **`sigpath::sourceManager.tune()/start()/stop()/selectSource()` 的线程模型**未在本笔记展开（属 Wave1-A 信号路径批次），本笔记只记录 server 对它们的调用点。
4. **`rigctl_server` 的完整命令白名单**只摘了 `F/f/M/m/V/v` 六个（`main.cpp:388-521`），其余 rigctld 命令（如 `set_level` 增益、`set_vfo_b` 等）未逐条枚举。
5. **MBDSDR 侧 `spyserver_server.cpp` 的 `dispatchCommand/onSetSetting` 具体 setting 编号表**未逐行读（只读了 `spyserver_server.h` 接口）；Wave2 接线时需补读以确认 SpyServer 协议本身已支持哪些 setting、ControlHub 是否要在其上扩展。
6. **`llm_worker.cpp` 工具循环全貌**只读了 gate 那 8 行（`:17-24,130`）；参数校验（`arguments_validator`）与工具结果回灌细节未展开。

---

## 6. 精读文件清单（实际打开过）

**SDR++（GPLv3，仅学机制）**
- `repos/sdrpp/core/src/server.cpp`（全文 387 行）
- `repos/sdrpp/core/src/server.h`（全文 28 行）
- `repos/sdrpp/core/src/server_protocol.h`（全文 53 行）
- `repos/sdrpp/core/src/backend.h`（全文 12 行）
- `repos/sdrpp/core/src/module_com.cpp`（全文 52 行）
- `repos/sdrpp/core/src/module_com.h`（全文 23 行）
- 佐证：`decoder_modules/radio/src/radio_interface.h`、`radio_module.h:780-842`、`core.h:10`/`core.cpp:38`、`misc_modules/{recorder,frequency_manager,rigctl_server}/src/*.cpp`（grep 级）

**MBDSDR（MIT，自有代码）**
- `cpp/src/ai/agent_tools.cpp`（全文 290 行）
- `cpp/src/ai/tool_schema.cpp`（全文 208 行）
- `cpp/src/ai/agent.cpp`（全文 118 行）
- `cpp/src/ai/llm_worker.cpp`（gate 段 grep，`:17-24,130`）
- `cpp/src/dsp/spectrum_engine.h`（`:40-305` 控制槽/信号段）
- `cpp/src/dsp/spyserver_server.h`（`:30-84` tuner/接口段）
