# OpenWebRX / SDRangel 源码精读 —— Web 流式接口与插件架构移植笔记

> 上游仓库：
> - OpenWebRX https://github.com/jketterl/openwebrx （本地 `repos/openwebrx/`）
> - SDRangel https://github.com/f4exb/sdrangel （本地 `repos/sdrangel/`）
>
> 目标：把"多客户端共享一个 SDR 后端 + FFT/音频 WebSocket 推送 + 插件动态加载"三条机制移植到
> `mbdsdr_ai/web/` 与 `mbdsdr_ai/plugin_manager.py`。下文所有引用均为 `file:line`。

---

## 1. OpenWebRX 架构总览

OpenWebRX 是单进程、多客户端、服务端 DSP 的 Web SDR。进程模型：

- HTTP 入口：`owrx/http.py:179` `RequestHandler(BaseHTTPRequestHandler)`，按方法分发到 Router
  （`owrx/http.py:85` `Router`，`StaticRoute`/`RegexRoute`，`owrx/http.py:64,73`）。
- WebSocket 升级：路由 `/ws/` → `owrx/controllers/websocket.py:6` `WebSocketController.indexAction()`
  直接 `WebSocketConnection(self.handler, HandshakeMessageHandler()).handle()`，在 HTTP 线程里接管 socket。
- 业务连接：握手字符串 `"SERVER DE CLIENT ... type=receiver"`（`owrx/connection.py:493`）后把
  连接对象切换为 `OpenWebRxReceiverClient`（`owrx/connection.py:502`）。
- SDR 后端：`owrx/sdr.py::SdrService` 持有一个或多个 `SdrSource`；多个 client 通过
  `sdrSource.addClient()` 挂到同一个源上，共享同一段 IQ buffer（`owrx/fft.py:47`、`owrx/connection.py:334`）。

关键启示：**一个物理 SDR 被 N 个 Web 客户端共享**，服务端按客户端做 DSP（解调/音频），但
瀑布 FFT 只算一份、广播给所有人（见 §3）。

---

## 2. 多客户端共享后端（owrx/connection.py）

### 2.1 客户端注册表（限流 + 广播）
`owrx/client.py:13` `ClientRegistry`：
- `addClient()` `owrx/client.py:34`：超过 `max_clients` 抛 `TooManyClientsException`
  （`owrx/client.py:9`），拒绝新连接。
- `removeClient()` `owrx/client.py:44`，增删后 `broadcast()` 把在线人数推给所有 client。
- 单例 + 锁：`owrx/client.py:15` `creationLock`。

### 2.2 单连接写出队列（背压）
`owrx/connection.py:30` `Client`：
- 每连接一个 `Queue(100)`（`owrx/connection.py:33`）+ 专用出队线程
  `mp_passthru`（`owrx/connection.py:35`）。DSP 线程调 `mp_send()` 非阻塞入队；队列满
  （`Full`）直接 `close(error=True)`（`owrx/connection.py:82`）——**慢客户端被踢，不阻塞后端**。
- `send()`（`owrx/connection.py:55`）走 `WebSocketConnection.send`；JSON dict 自动序列化
  （`owrx/websocket.py:119`）。

### 2.3 共享 SDR 源
`owrx/connection.py:116` `OpenWebRxReceiverClient`：
- `setSdr()` `owrx/connection.py:309`：从 `SdrService.getFirstSource()` 拿同一个源，
  `sdr.addClient(self)` 注册自己为事件客户。
- `getDsp()` `owrx/connection.py:366`：**每个 client 懒加载自己的 `DspManager`**（解调/音频是
  per-client 的，因为每个人调不同频点/模式），但 FFT 频谱是共享的（见 §3）。
- 消息协议：客户端文本帧 JSON `{"type": "dspcontrol", "action":"start", "params":{...}}`
  （`owrx/connection.py:273`），服务端回推 `{"type":"config"/"smeter"/"...", "value":...}`。

### 2.4 移植映射
我们的 `SpectrumStreamer` 对应 §2.1/§2.2：一份 FFT、多订阅者队列、满则踢订阅者。
`WebServer` 对应 §2.3：后端为 None 时所有端点显式 `{"connected": false}`，不造假。

---

## 3. 瀑布流 FFT（owrx/fft.py + csdr/chain/fft.py）

### 3.1 FFT 计算管线
`owrx/fft.py:13` `SpectrumThread`（一个 SDR 全局一份）：
- 参数栈：`samp_rate / fft_size / fft_fps / fft_voverlap_factor / fft_compression`
  （`owrx/fft.py:21-27`）。
- 实际 DSP 链在 `csdr/chain/fft.py:24` `FftChain`：
  1. `Fft(size)` —— 加窗 FFT（`csdr/chain/fft.py:38`）；
  2. `LogPower` / `LogAveragePower` —— 幅度转 dB、可配置平均次数（`csdr/chain/fft.py:8`）；
  3. `FftSwap` —— 把 0Hz 搬到中间（`csdr/chain/fft.py:40`）；
  4. 可选 `FftAdpcm` —— ADPCM 压缩（`csdr/chain/fft.py:42,78`）。
- fps 与块大小换算：`csdr/chain/fft.py:88` `_updateParameters()`：
  `fftAverages = round(samp_rate / fft_size / fps / (1 - voverlap))`；
  `blockSize = samp_rate / fps`（无平均）或 `samp_rate/fps/fftAverages`。

### 3.2 推送
- `SdrSource.writeSpectrumData()` 把 FFT 帧写到所有 spectrum client
  （`owrx/fft.py:73` 的 pump 回调）。
- client 侧 `owrx/connection.py:372` `write_spectrum_data()`：
  **前缀一个字节 `0x01`** 后二进制发送；音频是 `0x02`（`owrx/connection.py:375`），
  次级 FFT `0x03`（`owrx/connection.py:395`），HD 音频 `0x04`（`owrx/connection.py:378`）。
  前端按首字节区分流类型。

### 3.3 压缩
默认 `fft_compression="adpcm"`、`audio_compression="adpcm"`（`owrx/config/defaults.py:17-18`）。
OpenWebRX 用 ADPCM 把 dB 曲线压成差分字节。我们的移植版用更简单、确定性更好的
**块最大值降采样 + uint8 量化**（见 `mbdsdr_ai/web/streamer.py`），峰值不丢。

### 3.4 WebSocket 帧实现（owrx/websocket.py）
- 握手：`owrx/websocket.py:76-84`，`Sec-WebSocket-Key + GUID 258EAFA5-...` SHA1→base64。
- 发帧：`get_header()` `owrx/websocket.py:91`，支持 7/16/64 位长度扩展；server→client 不掩码。
- 收帧：`read_loop()` `owrx/websocket.py:194`，select + interrupt pipe；客户端帧必须解掩码
  （`owrx/websocket.py:221`）；ping→pong（`owrx/websocket.py:235,296`）。
- 发送串行化：`sendLock`（`owrx/websocket.py:66,140`），10s select 超时即关连接。

---

## 4. SDRangel 插件系统

### 4.1 插件接口
`sdrbase/plugin/plugininterface.h:23` `PluginDescriptor`：`hardwareId / displayedName /
version / copyright / website / licenseIsGPL / sourceCodeURL`。
`PluginInterface`（`plugininterface.h:44` 起）通过 `getPluginDescriptor()` 暴露描述，并在
注册期回调 `PluginAPI`：`registerRxChannel / registerTxChannel / registerSampleSource /
registerSampleSink / registerFeature`（见 `pluginmanager.h:73-79`）。

### 4.2 插件管理器
`sdrbase/plugin/pluginmanager.h:44` `PluginManager`：
- `loadPlugins(pluginsSubDir)` 扫目录；核心循环在
  `sdrbase/plugin/pluginmanager.cpp:222` `loadPluginsDir()`：
  1. 遍历目录 `entryList`（`pluginmanager.cpp:227`）；
  2. `QPluginLoader(path).load()`，失败 `qWarning` 后 `continue`（**坏插件不致命**，
     `pluginmanager.cpp:239-242`）；
  3. `qobject_cast<PluginInterface*>(loader.instance())`，转型失败跳过
     （`pluginmanager.cpp:246-251`）；
  4. 加入 `m_plugins` 列表（`pluginmanager.cpp:256`）。
- 按类型分注册表：`m_rxChannelRegistrations / m_sampleSourceRegistrations /
  m_featureRegistrations ...`（`pluginmanager.h:135-143`），
  `listRxChannels() / getChannelPluginInterface(uri)` 供 UI 枚举（`pluginmanager.h:89-96`）。

### 4.3 设备枚举
`sdrbase/device/deviceenumerator.h:41-44`：`enumerateRxDevices/TxDevices/MIMODevices/
enumerateAllDevices(PluginManager*)`。设备插件（`plugins/samplesource/rtlsdrinput/` 等）
在被加载后由 DeviceEnumerator 回调枚举物理设备，结果进 `SamplingDeviceRegistrations`。

### 4.4 移植映射
我们的 `Plugin` 基类对应 `PluginInterface` + `PluginDescriptor`；`plugin.json` 对应
`PluginDescriptor`；`PluginManager.discover/load/get/list` 对应 `loadPluginsDir` +
分类型注册表；坏插件跳过对应 `pluginmanager.cpp:241/249`。
**增强**：SDRangel 的 `.so` 改一次要重启；我们用 `importlib` + 模块缓存失效实现
Python 插件热重载（`reload()`）。

---

## 5. 我们的增强（相对上游）

1. **AI 标注通道**：`SpectrumStreamer` 维护 `annotations` 列表，外部 AI/分类器可注入
   `{"freq_hz", "label", "suggested_mode", "confidence"}`，随 `/api/spectrum` 和 WS 帧下发，
   前端在瀑布上画标签（OpenWebRX 无此概念）。
2. **Python 插件热加载**：`PluginManager.reload(name)` 删 `sys.modules` 缓存后重新 import，
   改代码不重启。
3. **GQRX 远程控制兼容**：`/api/status` 与未来 `/api/control` 走 GQRX `Tune`/`Mode` 文本命令
   语义（参考 `docs/learn/gqrx.md`），便于复用现有 GQRX 客户端。

---

## 6. 红线对照

- 无后端时 `GET /api/status → {"connected": false}`、`GET /api/spectrum → {"connected": false}`，
  WS 连接可建立但只发心跳/空帧，**不生成假 IQ/假瀑布**。
- 只新建 `mbdsdr_ai/web/`（`__init__.py / server.py / streamer.py`）、
  `mbdsdr_ai/plugin_manager.py`、`tests/test_web_server.py / test_streamer.py /
  test_plugin_manager.py`；不改任何现有文件。
