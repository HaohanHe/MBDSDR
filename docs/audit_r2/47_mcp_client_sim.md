# 47 · MCP 客户端 & 仿真服务器 第二轮深度审查

- 审查范围：`mbdsdr_mcp_client.py`（712 行，约 25 KB）、`mbdsdr_sim_server.py`（395 行，约 15 KB）
- 交叉参照：`mbdsdr_ai/agent.py`、`mbdsdr_ai/sdr_backend.py`、`desktop/mcp_worker.py`、`mbdsdr_ai/tool_registry.py`
- 审查方式：只读，已通读两个文件全文并实际运行了关键路径的复现脚本
- 分类标记：**[真bug]** = 会跑错/跑崩；**[空壳]** = 只定义接口/文档没实现；**[占位]** = 为后续留的 stub；**[建议]** = 不影响正确性但该改

---

## 0. 一句话结论

`mbdsdr_mcp_client.py` 是一个**真实可跑的 WebSocket JSON-RPC 客户端 + NDJSON stdio 桥**，但它与仿真服务器之间存在**协议形状不一致**（list_tools 返回值），并且**与 agent 主链路完全脱钩**——agent.py 实际走的是 `sdr_backend.py:_send_mcp()` 的另一份重复实现。仿真服务器只模拟了 RSSI/GPS/IMU 的"数值"，**没有任何 IQ/音频/调制波形生成**，所谓"无硬件测试"只能测控制面，不能测信号面。

---

## 1. 与 agent.py / sdr_tools 的真实关系（先纠正文档叙事）

### 1.1 [真bug/架构] MBDSDRClient 没有被 agent 使用

- `agent.py:264` 定义了 `set_mcp_client(self, client)`，全仓 grep **零调用方**（只有定义处）。
- `agent.py:59` 的 `agent.register_mcp_tools(...)` 是被 `mbdsdr_ai_mcp_server.py` 用的，不是把 `MBDSDRClient` 接进来。
- 真正在 agent 进程里跑硬件 WebSocket 的是 `mbdsdr_ai/sdr_backend.py:605-623` 的 `_send_mcp()`——**另起炉灶重写了一遍** JSON-RPC over websocket，没有复用 `MBDSDRClient`：
  - 它自己 `websocket.create_connection`、自己 send/recv、自己 `id = int(time.time()*1000) % 100000`（撞号风险）、自己 `settimeout(2.0)`。
  - `MBDSDRClient` 只被两个地方实例化：`desktop/mcp_worker.py:181`（GUI 后台线程）和 `mbdsdr_mcp_client.py:322`（stdio 桥）。

**后果**：文档/README 里"agent 通过 MCP 客户端操控 SDR"的叙事与代码不符。两份实现各自演进，已经出现行为分叉（见 §2.3 freq 单位）。

### 1.2 [建议] 仿真服务器不在 agent 默认链路里

- `mbdsdr_sim_server.py` 只能通过手工 `python3 mbdsdr_sim_server.py --port 81` 起，再用 `--host localhost` 连。
- `desktop/mcp_worker.py:25` 自己内置了一份 `self.sim`（GUI 离线模式），**和 `mbdsdr_sim_server.py` 是两套不同的模拟实现**，数值/接口都不一样。
- agent 进程内没有"无硬件自动回退到 sim_server"的开关；要做无硬件集成测试，必须手动起独立进程。

---

## 2. `mbdsdr_mcp_client.py` 发现的问题

### 2.1 [真bug] `list_tools()` 返回形状与仿真服务器/桥接器三方不一致

- 客户端 `MBDSDRClient.list_tools()`（`mbdsdr_mcp_client.py:173-176`）：
  ```python
  resp = self.call("list_tools")
  return resp.get("result", [])     # 期望 result 直接是 list
  ```
- 仿真服务器 `handle_tool_call("list_tools", ...)`（`mbdsdr_sim_server.py:136-137`）：
  ```python
  return {"tools": TOOLS_LIST}      # result 是 dict，包了一层
  ```
- stdio 桥 `_handle_tools_list`（`mbdsdr_mcp_client.py:385-396`）拿到 `self._tools_cache = self.client.list_tools()` 后直接 `for tool in self._tools_cache:`。

实际复现（已跑）：`list_tools()` 返回 `{"tools": [...]}` dict，对 dict 迭代得到字符串 `"tools"`，下一行 `tool.get("name","")` 立刻 `AttributeError`，被 `except Exception`（:387）吞掉，向 Cursor/Claude 返回 `-32603 获取工具列表失败`。

**即：`--mcp` 桥接模式对接仿真服务器时，`tools/list` 直接报错。** 对接真实固件时形状又未知（固件可能返回 list 也可能返回 dict），客户端没有兼容处理。这是典型的"实验室绿"——作者本机可能手测时绕过了桥接器直接用 CLI，没跑通 Cursor 全链路。

### 2.2 [真bug] RPC 锁粒度错误：锁了 id 没锁 send/recv

- `mbdsdr_mcp_client.py:69` 声明 `self._lock = threading.Lock()`，但 `call()` 里只在 `_next_id()`（:107）用它。
- `self.ws.send(...)`（:141）和 `self.ws.recv()`（:151）**都在锁外**。
- `desktop/mcp_worker.py` 里 `_poll()` 线程周期调 `get_status/get_gps/get_imu`（:207-224），UI 线程通过 `call_tool` 也会调 `tune_fm` 等。两个线程并发 `call()` 时：
  - 线程 A send 后等 id=1；
  - 线程 B 抢在 A 之前 send id=2；
  - A 的 recv 循环读到 id=2 的响应，id 不匹配 → `continue` 丢弃；
  - B 读到 id=1 的响应，同样丢弃；
  - 双方都等到 timeout 抛 `TimeoutError`。
- `_lock` 本意是保护 ws 串行化，实际只保护了计数器，等于没锁。

### 2.3 [真bug] 频率单位协议在三份实现间对不上

- 仿真服务器 `tune_fm` 存 `state.freq = int(freq*100)`（`mbdsdr_sim_server.py:148`），即 **0.01 MHz** 单位（98.5 → 9850）。
- `MBDSDRClient` 自己的 CLI 只把 `freq` 当透传参数，从不解释，所以 CLI 层看不出问题。
- 但 `sdr_backend.py:691`：
  ```python
  self.status.frequency_hz = float(result["freq"]) * 1_000_000 if mode_name=="FM"
                             else float(result["freq"]) * 1000
  ```
  它假设 `freq` 是 **MHz / kHz**。复现：仿真返回 9850 → backend 算出 `9.85e9 Hz`（9.85 GHz），比真实频率大 100×。
- 这意味着即使仿真服务器"跑通了"，agent UI 上看到的频率也一定是错的。真机固件如果返回 Hz（更常见），backend 又会把 98.5e6 再乘 1e6。单位契约没人定义过。

### 2.4 [真bug] 连接管理：无心跳、无重连、`_connected` 状态机会粘死

- `connect()`（:76-94）：`if self._connected: return True`。一旦 ws 被对端关闭但本地没感知（ESP32 重启、WiFi 丢包），后续 `call()` 在 send 时才抛 `ConnectionError`，但**不会自动重连**。
- `StdioMCPServer._handle_tools_list` / `_handle_tools_call` 每次都调 `self.client.connect()`（:374, :440），可 `connect()` 看到 `_connected=True` 直接返回 True，**不会探测死连接**。
- 没有 ping/pong 心跳。WebSocket 层的 TCP 半开连接在 NAT/ESP32 上很常见，桥接器会一直返回"无法连接硬件"直到整个 stdio 进程被 Cursor 重启。
- `close()`（:96-104）把 `ws=None` 但没有锁，与 `call()` 并发时有 `AttributeError` 窗口。

### 2.5 [空壳] `start_record` / WAV 数据回流完全没实现

- 文档字符串（:217）和 CLI 输出（:651）都声称"录音已启动，WAV 数据通过 WebSocket 推送 (电脑端存盘)"。
- 实际 `call()` 的 recv 循环（:148-166）遇到 id 不匹配的消息直接 `continue` 丢弃。固件如果真的推 WAV 二进制帧，客户端**既不识别 binary frame、也不落盘、也不解码 WAV 头**。
- 仿真服务器侧 `start_record`（`mbdsdr_sim_server.py:218-223`）只把 `recording=True`、`rec_samples=0`，`tick()` 每秒加 48000 计数——**从不产生任何采样**。
- 结论：录音链路是纯控制面 mock，"AI 录制基带"在两端都没落地。

### 2.6 [真bug] `call()` 收到空帧 / 二进制帧会死循环

- :152 `if not raw: continue`。websocket-client 的 `recv()` 在对端关闭时返回 `""`，代码 `continue` 后继续在 deadline 循环里 `recv()`，立刻又拿到 `""`，忙转直到 timeout——白白烧一个超时周期，然后抛 TimeoutError 而不是 ConnectionError。
- 二进制帧（WAV chunk）会被 `json.loads(raw)` 抛异常，被 :163 `except Exception` 捕获 → 直接 `self._connected=False` 抛 ConnectionError。也就是说**一旦固件真的推一帧音频，客户端就判定连接断了**。

### 2.7 [建议] `_handle_tools_list` 的 JSON Schema 推断过于粗糙

- :407 `if any(kw in pdesc.lower() for kw in ["int","float","khz","mhz","volume"]): ptype="number"`——所有带这些关键字的参数都成 number，字符串参数（如果未来加）会被误判。
- :413 所有参数一律 `required.append(pname)`，没有可选参数概念。
- 对当前 14 个工具不致命，但工具一多就会生成错误的 inputSchema，Cursor/Claude 会按错误 schema 填参。

### 2.8 [建议] initialize 握手硬编码

- :358 `"protocolVersion": "2024-11-05"` 不读请求里 `params.protocolVersion`，也不回 `serverInfo.capabilities` 的具体协商结果。新版 MCP SDK 客户端会做版本检查，硬编码旧版可能被拒。
- 没有处理 `notifications/cancelled`、`resources/list`、`prompts/list` 等方法，未知方法统一回 `-32601`（:511）——可接受，但意味着这是一个"最小可用"桥，不是合规 MCP server。

### 2.9 [建议] 命令行 `scan_fm` 打印假设 RSSI 是 int

- :687 `print(f"  {r['freq_mhz']:6.1f} MHz  RSSI={r['rssi']:4d}  SNR={r['snr']}")`。真实固件若返回 float RSSI 会 `ValueError: Unknown format code 'd'`。仿真服务器返回 int 所以本机绿。

---

## 3. `mbdsdr_sim_server.py` 发现的问题

### 3.1 [真bug] `get_status` 里 RSSI 与 SNR 不一致

- :179-180 连续调 `state.calc_rssi()` 和 `state.calc_snr()`，而 `calc_snr()`（:107）**内部又调一次 `calc_rssi()`**。两次调用各带 `random.randint(-3,3)`，同一次 `get_status` 返回的 `rssi` 和 `snr` 可能不匹配（比如 rssi=50 但 snr 按 rssi=47 算）。
- 对"AI 看 SNR 判信号强度"这种消费方是噪声。

### 3.2 [真bug] 工具错误被错误地包装成 JSON-RPC error

- :315 `if "error" in result and method != "get_status":`
  - 对 `start_record` 重复录音，返回 `{"ok": False, "error": "已经在录音中"}`——被包成 JSON-RPC error。客户端 `MBDSDRClient.start_record()`（:218-219）走 `resp.get("result", resp.get("error", {}))`，能拿到 error dict，勉强可用。
  - 但 `tune_fm` 越界（:146）也返回 `{"error": ...}`，同样被包成 JSON-RPC error。CLI 打印时是 `{"code": -32603, "message": "freq_mhz 必须在 64-108 之间"}`——**丢失了"这是业务错误还是协议错误"的语义**。
  - `method != "get_status"` 这个特判毫无意义（get_status 永远不返回 error 键），是死代码。

### 3.3 [空壳] 没有 IQ / 音频 / 调制波形生成

- 整个 sim 里搜不到 `numpy`、`iq`、`samples`、`audio`、`wav` 写入、`sin`/`cos` 载波、`fm_mod`/`am_demod` 任何字样。
- `fm_stations` / `am_stations`（:65-74）只是一张 `{频率: RSSI}` 查表，加距离衰减 `station_rssi - int(dist*40)`（:92）。
- **不支持的调制模式**：除 FM 广播 / AM 中波两档旋钮位置外，SSB、CW、FT8、JT9、SSTV、WBFM 立体声、数字模式（FM/NBFM/P25/DMR）全部没有。
- `sdr_backend.py:595` 自己声明 `supports_iq=False`（SI4732 不出 IQ），sim 也跟着不出——但项目里 `syn_robot36.npy`、`syn_robot36.wav` 这些 SSTV 合成产物是由别的脚本（`experiments/`）离线生成的，**不是这个 sim server 出的**。
- 结论：sim server 只能测"调谐→读 RSSI"的控制面闭环，**不能测任何信号处理链路**。所谓"无硬件测试"覆盖面极窄。

### 3.4 [空壳] OTA / reboot / update 全部是静态字符串

- `get_version`（:237-249）硬编码 `v0.6-SIMULATION`。
- `check_update`（:251-260）硬编码 `update_available: False`、`latest_url` 指向 GitHub。
- `trigger_ota` / `web_ota_url`（:262-275）永远返回同一个 `http://192.168.4.1/update`。
- `reboot`（:277-278）注释自己写了"模拟服务器不重启"。
- 这些接口存在的意义是让 MCP 桥不报"未知方法"，**没有任何可验证行为**。

### 3.5 [真bug/安全] 默认监听 `0.0.0.0:81`，无任何访问控制

- :351 `--host` 默认 `0.0.0.0`。起 sim 后同网段任何人都能 `ws://<你的IP>:81` 连进来调 reboot/tune/set_volume。
- 没有 token、没有 origin 校验、没有 TLS（即使真实硬件也是 `ws://` 明文）。
- 真实硬件侧（`mbdsdr_mcp_client.py:74`）也是 `ws://{host}:{port}`，硬编码明文，文档里还写了 WiFi 密码 `mbdsdr123`（:91）。任何连上 `MBDSDR-Mini` AP 的人都能 reboot 设备。

### 3.6 [建议] `tick_loop` 与 `rec_samples` 是粗粒度近似

- :80 `self.rec_samples += 48000` 每秒加一次。真实录音是按采样中断流式产出的。
- `stop_record`（:229）`duration_sec = rec_samples/48000`，在 tick 还没跑的瞬间 stop 会得到 0 秒。
- 对 mock 可接受，但不要拿它做时序回归基准。

### 3.7 [建议] `websockets.serve` 的 lambda 闭包

- :378-381 `async with websockets.serve(lambda ws: handle_client(ws, state, args.verbose), ...)`。新版 websockets（≥11）handler 签名是 `(ws)`，OK；但旧版传 `(ws, path)`，lambda 会报错。`requirements.txt` 里没锁版本。

---

## 4. "实验室绿、真机红" 专项

| 现象 | 本机为何绿 | 真机/全链路为何红 |
|---|---|---|
| `--mcp` 桥接 `tools/list` | 作者手测时直接用 CLI `list_tools`（:619），不经过桥接器的 dict→list 迭代（§2.1） | Cursor/Claude 启动就调 `tools/list`，拿到 `-32603`，工具面板空 |
| agent 显示频率 | GUI 走 `mcp_worker.py`，worker 自己模拟一组字段；agent 走 `sdr_backend.py` | 真机/sim 返回 freq=9850，backend 乘 1e6 得 9.85 GHz（§2.3） |
| 并发调工具 | 单机脚本串行调用，不触发锁外 recv 交错（§2.2） | GUI 轮询 + 用户点按钮同时发生 → 双 timeout |
| 连接稳定性 | 测试时短连接，10s 内关掉 | ESP32 重启/WiFi 漫游后 `_connected` 粘 True，桥接器假死（§2.4） |
| sim 信号模型 | RSSI 查表输出一个 int，够 UI 画条线 | 真实信号有衰落/多径/邻道/镜像，sim 完全没有（§3.3） |
| 录音功能 | CLI 打印一行"录音已启动"就结束 | 固件真推 WAV 帧时客户端 `json.loads` 炸掉（§2.6） |

---

## 5. 空壳 / 占位清单

| 位置 | 内容 | 性质 |
|---|---|---|
| `mbdsdr_mcp_client.py:216-219` | `start_record()` 控制面 | **空壳**（无 WAV 回流） |
| `mbdsdr_mcp_client.py:221-224` | `stop_record()` 控制面 | **空壳** |
| `mbdsdr_mcp_client.py:303-309` 注释 | "论文最大创新点" | 占位叙事 |
| `mbdsdr_mcp_client.py:352-367` | MCP initialize 握手 | **占位**（硬编码版本，不协商） |
| `mbdsdr_mcp_client.py:481-520` | stdio 主循环 | **占位**（只支持 initialize/tools.list/tools.call/ping，不支持 resources/promots/logging） |
| `mbdsdr_sim_server.py:218-235` | 录音模拟 | **空壳**（只计数，不出采样） |
| `mbdsdr_sim_server.py:237-278` | version/update/OTA/reboot | **占位**（全静态字符串） |
| `mbdsdr_ai/agent.py:264-266` | `set_mcp_client()` | **死代码**（零调用方） |

---

## 6. 安全 & 性能 & 错误处理小结

**安全**
- 全链路 `ws://` 明文，无 TLS（§3.5）。
- 无认证 / 无 token / 无 origin 校验；reboot/OTA 任何客户端可调。
- 默认 WiFi 密码硬编码在错误提示里（`mbdsdr_mcp_client.py:91`）。

**性能**
- `call()` 每次都 `json.dumps` + 逐字节 `recv()`，对 14 个小工具可接受。
- `scan_fm`（:258-285）每频点 2 次 RPC（tune + status）+ `dwell_ms` 睡眠，20.5 MHz 扫一遍要 ~410 个往返，慢但合理。
- sim server 是 asyncio 单进程，无 CPU 密集负载，够用；但也正因如此它**根本没打算生成实时 IQ**——实时 IQ 需要 numpy/numba，这里一行都没有。

**错误处理**
- CLI 入口（:701-703）捕获 `ConnectionError/TimeoutError`，优雅退出，OK。
- stdio 桥（:471-479）把所有异常包成 `isError: True` 的 text content，符合 MCP 错误语义，OK。
- **但**：`call()` 在 send 失败时把 `_connected=False`（:143）却不重连；recv 异常也只置位不恢复。上层每次都要自己 try/except 重建 client，桥接器没有重连策略。

---

## 7. 优先修复建议（按 ROI 排序）

1. **[必修] 统一 list_tools 返回形状**：要么 sim 改成直接返回 list，要么 `MBDSDRClient.list_tools()` 做 `result.get("tools", result)` 兼容。否则 stdio 桥对接任何后端都裂。
2. **[必修] 把 `self._lock` 包住 send+recv 整段**（或用 `websocket-client` 的 `enableTrace` + 队列），解决 GUI 并发 timeout。
3. **[必修] 定义 freq 单位契约**：建议统一 Hz（int），sim 和 sdr_backend 同时改；现在的 0.01MHz / MHz 混用是定时炸弹。
4. **[必修] `call()` 检测空帧 → ConnectionError，并忽略/缓冲 binary frame**，不要一收到 WAV chunk 就断连。
5. **[应修] 加 ping 心跳 + 一次自动重连**（connect 失败/连接断开时重连 1 次）。
6. **[应修] sim server 把 host 默认改成 `127.0.0.1`**，文档提示要外部访问再显式 `--host 0.0.0.0`。
7. **[建议] 要么删 `agent.py:set_mcp_client` 死代码，要么让 `sdr_backend.py` 直接复用 `MBDSDRClient`**，消除两份重复实现。
8. **[建议] 明确写文档：当前 sim 只测控制面，信号面测试走 `experiments/` 离线脚本**，别让评审者误以为"无硬件测试覆盖了 SDR 链路"。
