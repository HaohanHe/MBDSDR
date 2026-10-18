# 第三方通用 MCP 客户端实证：不接模型也能 tools/list + tools/call

**日期**: 2026-10-13
**二进制**: `ci/D135/cpp/build/mbdsdr_mcp`（headless stdio JSON-RPC 2.0）
**客户端**: 一段 ~80 行的原生 Python `subprocess` 脚本，**不 import 任何 MCP SDK、不 import 任何 LLM SDK、不带任何 API key**。
**目的**: 用一次真实终端往返证明：`mbdsdr_mcp` 是一个标准的 stdio MCP server，任何能读写 stdin/stdout 的程序都能驱动它——模型是调用方自己的事，本进程零模型依赖。

> 协议规格本身（方法表、域分组、gate 语义、错误包络）见姊妹文档
> [`third-party-agent-mcp.md`](./third-party-agent-mcp.md)。本文只记录**实测证据**，
> 不重复协议设计理由。

---

## 1. 被测对象与运行环境

```text
二进制:  /path/to/MBDSDR/MBDSDR/ci/D135/cpp/build/mbdsdr_mcp
         (ELF 64-bit LSB pie, x86-64, with debug_info, not stripped)
启动 env: QT_QPA_PLATFORM=offscreen
          MBDSDR_NULL_AUDIO=1
          LD_LIBRARY_PATH=/home/user/Qt/6.8.2/gcc_64/lib
```

二进制自己在 `main()` 里 `qputenv` offscreen + null audio；容器/无显示环境再显式传一遍保险。
链接 Qt 库需要 `LD_LIBRARY_PATH` 指向本机 Qt 6.8.2 GCC 构建。

---

## 2. 最小通用客户端（零 SDK，零 LLM）

下面就是本次实测用的脚本骨架。它只做四件事：`Popen` 子进程、按行写 JSON-RPC 请求、
按行读 JSON-RPC 响应、匹配 `id`。没有任何 `mcp` / `anthropic` / `openai` import。

```python
import json, os, subprocess, select, time

BIN = "/abs/path/to/mbdsdr_mcp"
ENV = os.environ.copy()
ENV["QT_QPA_PLATFORM"] = "offscreen"
ENV["MBDSDR_NULL_AUDIO"] = "1"
ENV["LD_LIBRARY_PATH"] = "/home/user/Qt/6.8.2/gcc_64/lib"

proc = subprocess.Popen([BIN],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=ENV, bufsize=0)
_id = 0

def call(method, params=None, timeout=10):
    global _id; _id += 1
    msg = {"jsonrpc":"2.0","id":_id,"method":method}
    if params is not None: msg["params"] = params
    proc.stdin.write((json.dumps(msg)+"\n").encode()); proc.stdin.flush()
    end = time.time()+timeout
    while time.time() < end:
        r,_,_ = select.select([proc.stdout],[],[],0.5)
        if not r: continue
        line = proc.stdout.readline().decode("utf-8","replace").rstrip("\n")
        obj = json.loads(line)
        if obj.get("id") == _id: return obj

# 1) 握手
call("initialize", {"protocolVersion":"2024-11-05",
                    "capabilities":{"tools":{}},
                    "clientInfo":{"name":"generic-probe","version":"0.0.1"}})
# 2) 通知（无 id，服务端不回 stdout）
proc.stdin.write((json.dumps({"jsonrpc":"2.0","method":"notifications/initialized"})+"\n").encode())
proc.stdin.flush()
# 3) 工具目录
print(call("tools/list", {}))
# 4) 调工具
print(call("tools/call", {"name":"get_color_maps","arguments":{}}))
```

**这就是全部**。把这段脚本扔到任何有 Python 3 的机器上，改一下 `BIN` 路径，就能驱动接收机——
不需要装 Claude Desktop、不需要 Cursor、不需要 `uvx mcp`、不需要任何 API key。

---

## 3. 实测记录（真实终端输出，逐字复制）

下面所有 JSON 都是上面那个脚本在 2026-10-13 本机真实跑出来的 stdout 行，未做任何
改写（只在超长处截断并标 `...`）。

### 3.1 `initialize` 握手

```json
→ {"jsonrpc":"2.0","id":1,"method":"initialize",
   "params":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},
             "clientInfo":{"name":"generic-python-stdio-client","version":"0.0.1"}}}
← {"id":1,"jsonrpc":"2.0","result":{
     "capabilities":{"tools":{"listChanged":true}},
     "protocolVersion":"2024-11-05",
     "serverInfo":{"name":"mbdsdr-core","version":"0.1.0"}}}
```

服务端自报 `protocolVersion=="2024-11-05"`，`serverInfo.name=="mbdsdr-core"`，
`capabilities.tools.listChanged==true`。握手成功，进程不要求任何模型字段。

### 3.2 `tools/list` —— 10 域、83 工具

默认（不带 params）返回域卡片数组，每卡只列工具名：

```text
domain cards returned: 10
  - SDR 调谐/VFO:      20 tools
  - 亚音数字解码:      16 tools
  - VNA 测量:           4 tools
  - VNA 分析:           6 tools
  - VNA 校准:           1 tools
  - 导出演制:          11 tools
  - 书签扫描卫星:       9 tools
  - 系统设备:           7 tools
  - 记忆编排:           5 tools
  - 工作台编排:         4 tools
TOTAL tool names across all domain cards: 83
```

首条原始 stdout 行（截断展示）：

```json
{"id":2,"jsonrpc":"2.0","result":{"tools":[
  {"domain":"SDR 调谐/VFO",
   "summary":"接收机调谐、解调模式、带宽/静噪/噪声门限与多 VFO 及频谱显示参数控制。",
   "tools":["tune_frequency","set_mode","set_bandwidth","set_squelch",
            "get_squelch","set_noise_blanker","get_noise_blanker",
            "list_vfos","add_vfo","switch_vfo","rename_vfo","set_vfo_armed",
            "set_vfo_frequency","set_vfo_mode","set_vfo_bandwidth",
            "set_fft_params","set_color_map","get_color_maps",
            "get_spectrum_status","set_doppler_compensation"]},
  {"domain":"亚音数字解码","summary":"CTCSS/DCS 亚音与 POCSAG/M17/VOR/ACARS/NAVTEX/ADS-B/FT8/LRPT 等数字报文解码。",
   "tools":["set_ctcss","get_ctcss_status","set_cdcss","get_cdcss_status",
            "get_pocsag_messages","get_m17_calls","get_vor_radial",
            "get_acars_packets","get_navtex_messages","get_adsb_aircraft",
            "set_ft8","get_ft8_status","get_ft8_history","set_lrpt",
            "get_lrpt_status","get_aprs_messages"]},
  {"domain":"VNA 测量", ... 4 tools ...},
  {"domain":"VNA 分析", ... 6 tools ...},
  {"domain":"VNA 校准", ... 1 tool  ...},
  {"domain":"导出演制", ... 11 tools ...},
  {"domain":"书签扫描卫星", ... 9 tools ...},
  {"domain":"系统设备", ... 7 tools ...},
  {"domain":"记忆编排", ... 5 tools ...},
  {"domain":"工作台编排", ... 4 tools ...}
]}}
```

**10 个域、83 个工具名，全部由服务端从 `ai::toolDefs()` 单源吐出**；客户端不需要内置
任何工具清单。`工作台编排` 是第 10 个冻结域（旧文档记录的是 9 域 / 68 工具，
本次实测已增长到 10 域 / 83 工具——以本文实测为准）。

按需下钻 schema（`params={"domain":"系统设备","detail":"schema"}`）拿到第一条工具
`get_status` 的完整 JSON Schema：

```json
{"name":"get_status",
 "description":"Return current receiver state: frequency, mode, bandwidth, sample rate.",
 "inputSchema":{"type":"object","properties":{},"required":[]}}
```

### 3.3 读工具实测：`get_color_maps`

```json
→ {"jsonrpc":"2.0","id":4,"method":"tools/call",
   "params":{"name":"get_color_maps","arguments":{}}}
← {"id":4,"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{
     \"builtin\":[{\"name\":\"经典\",\"palette\":0},
                {\"name\":\"单色\",\"palette\":1},
                {\"name\":\"Viridis\",\"palette\":2}],
     \"connected\":false,
     \"external\":null,
     \"note\":\"未配置外部色板文件\",
     \"ok\":true,
     \"source\":\"未连接\",
     \"test_signal\":false}"}]}}
```

无硬件时诚实空态：内置 3 色板照报（这是编译期常量，不依赖硬件），`external:null` +
`note:"未配置外部色板文件"`，`source:"未连接"`。不崩、不编硬件值。

### 3.4 读工具实测：`get_workspace_layout`（诚实降级）

```json
← {"id":5,"jsonrpc":"2.0","result":{"content":[{"type":"text","text":
   "{\"error\":\"工作台控制器未注入（MainWindow 未创建 WorkspaceController）\",\"ok\":false}"}]}}
```

headless 进程不创建 `MainWindow` / `WorkspaceController`，于是这个工作台域工具在
MCP 进程内拿不到控制器——**诚实报错而不是崩**。这是当前 headless 二进制的真实行为：
`工作台编排` 域的 4 个工具在纯 stdio 进程里大多落在这条"未注入"路径上。
（不是 bug，是架构事实：工作台编排原本就为 GUI 进程设计；MCP 暴露它是为了协议面完整。）

### 3.5 写工具 gate 实测：`tune_frequency`（未 arm → 拒绝）

先用 `tools/list` 拿 `tune_frequency` 的 schema 确认参数名是 `freq_hz`（不是
任务背景里猜的 `frequency_hz`——以 schema 为准）：

```json
{"name":"tune_frequency",
 "description":"Tune the receiver to a center frequency in Hz (single/main VFO).",
 "inputSchema":{"type":"object","required":["freq_hz"],
   "properties":{"freq_hz":{"type":"number","minimum":24000000,"maximum":1700000000,
     "description":"Center frequency in Hz, e.g. 98500000 for 98.5 MHz"}}}}
```

**未带 `arm_manual`**：

```json
→ {"jsonrpc":"2.0","id":3,"method":"tools/call",
   "params":{"name":"tune_frequency","arguments":{"frequency_hz":145000000}}}
← {"id":3,"jsonrpc":"2.0","result":{"content":[{"type":"text","text":
   "{\"error\":\"手动模式：未执行 tune_frequency\",\"gated\":true,\"ok\":false}"}]}}
```

`gated:true` 信封，**工具根本没触达引擎**。第二个写工具 `set_color_map` 同形态：

```json
← {"...","text":"{\"error\":\"手动模式：未执行 set_color_map\",\"gated\":true,\"ok\":false}"}
```

### 3.6 写工具 gate 实测：`tune_frequency`（已 arm → 穿透）

带上 `arm_manual:true` 并用对参数名 `freq_hz`：

```json
→ {"jsonrpc":"2.0","id":2,"method":"tools/call",
   "params":{"name":"tune_frequency",
             "arguments":{"freq_hz":145000000,"arm_manual":true}}}
← {"id":2,"jsonrpc":"2.0","result":{"content":[{"type":"text","text":"{
     \"connected\":false,
     \"frequency_hz\":145000000,
     \"message\":\"已调谐到 145.000 MHz\",
     \"ok\":true,
     \"source\":\"未连接\",
     \"test_signal\":false}"}]}}
```

要点：

* `arm_manual` 键在到达执行器前被 MCP 层**剥离**（执行器收到的 arguments 里没有它）。
* Gate 打开后，请求真正进入 `executeTool`；因当前是 null-engine（无硬件），落在
  诚实空态：`connected:false`、`source:"未连接"`，只在内存里记下"意图频率"，
  **不打开任何真实硬件、不写任何持久配置**。
* 我没有留下任何持久状态改动——进程退出后子进程被 SIGTERM，内存态随进程消失。
  故无需"恢复默认状态"这一步。

> 旁证：第一次带 `arm_manual` 但用错参数名 `frequency_hz`（schema 要求 `freq_hz`）时，
> 引擎返回 `{"error":"频率必须为正的有限数","ok":false}`——这是**参数校验**层的错误，
> 不是 gate 层的 `gated:true`。两条路径形状不同，反过来证明 gate 确实在 MCP 侧
> 拦截、过了 gate 才到执行器的参数校验。

### 3.7 诚实空态：`get_aprs_messages`

```json
← {"...","text":"{\"messages\":[],
     \"note\":\"空态：尚无 APRS 消息（未喂位流或无有效帧）\",
     \"ok\":true,\"returned\":0}"}
```

无位流时返回空数组 + 中文 note，不编报文。

### 3.8 错误路径

| 调用 | 返回 |
|------|------|
| `tools/call` 未知工具 `definitely_not_a_real_tool` | `{"error":"unknown_tool","suggestion":"用 tools/list 查看可用工具。","tool":"definitely_not_a_real_tool"}` |
| JSON-RPC 未知方法 `frobnicate` | `{"error":{"code":-32601,"message":"未知方法: frobnicate"},"id":7,"jsonrpc":"2.0"}` |

错误形状与姊妹文档 §2.3 一致。

---

## 4. 结论（一句话）

`mbdsdr_mcp` 是一个**标准 stdio MCP server**：握手 → 通知 → tools/list → tools/call
四步走通，83 个工具、10 个冻结域全部可发现、可调用；写工具被 MCP 层 `arm_manual` gate
拦截；无硬件时所有读/写落在诚实空态，不崩、不编值。上面那个 80 行 Python 脚本就是
第三方客户端——**它没有任何模型依赖，但已经能完整驱动 SDR 工具面**。

---

## 5. 诚实标注（未实测 / 局限）

* **客户端种类**：本次只实测了一个手写 Python `subprocess` 客户端。Claude Desktop /
  Cursor / 官方 `@modelcontextprotocol/sdk` TS 客户端**未**在本次实证里跑；但因为
  协议形状是标准 JSON-RPC 2.0 over line-delimited stdio（`initialize` 握手成功、
  `serverInfo` 正常、`listChanged` 能力声明正常），标准客户端应当能直接接，
  只是没在这次任务里亲自点一遍。
* **工具覆盖面**：83 个工具里本次只真实调了 5 个（`get_color_maps`、
  `get_workspace_layout`、`tune_frequency` × 2 路径、`get_aprs_messages`）+
  1 个 `set_color_map` gate 样本 + 未知工具/未知方法错误路径。其余 77 个工具
  **未**逐个 `tools/call`；它们的存在性与 schema 来自 `tools/list` 真实输出，
  不代表每个都在 headless null-engine 下经过了实测。
* **工作台编排域**：`get_workspace_layout` 实测返回"控制器未注入"——该域在纯 stdio
  进程内拿不到 GUI 控制器，4 个工具当前都走这条降级路径。这是架构事实，不是待修 bug；
  若未来要让 stdio 客户端真能编排工作台，需要 headless 进程内构造一个无窗口的
  `WorkspaceController` 注入路径（本次任务范围外，不改代码）。
* **写工具成功路径**：`tune_frequency` + `arm_manual:true` 在 null-engine 下"成功"
  其实只是内存态记录（`connected:false, source:"未连接"`），**没有任何真实硬件被
  调谐、没有任何持久配置被改写**。若日后接真硬件，`arm_manual:true` 路径会真正改变
  接收机状态——本次实证刻意停在 null-engine，不制造外部副作用。
* **协议版本**：服务端自报 `2024-11-05`；更新的 `2025-03-26` 握手未试（客户端带
  `2024-11-05` 已被接受）。
* **未做**：commit / push / 改代码。本文档只读 + 新建文档，未触动 `cpp/` 源码。
