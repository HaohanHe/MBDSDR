# R2 深度审查：sdr_backend.py 流式采样与后端层（后半部分 600–1196 行）

> 审查范围：`mbdsdr_ai/sdr_backend.py` 第 600–1196 行
> 审查日期：2026-09-24
> 审查人：R2 子 agent（流式采样 / IQ 管道 / 模拟·文件·SpyServer 后端）

---

## 0. 后端真实性总览

| 后端 | 类名 | 状态 | 说明 |
|---|---|---|---|
| 模拟 SDR | `MockSDRBackend` (L324) | **真实实现** | 生成合成 IQ，但有相位不连续 bug |
| RTL-SDR 本地 | `RTLSDRBackend` (L392) | **真实实现** | pyrtlsdr 直连，功能较完整 |
| rtl_tcp 网络 | `RTLSDRBackend(host=…)` (L456) | **真实实现** | 走 pyrtlsdr 的 TcpClient |
| ai-sdr Mini | `AISDRMiniBackend` (L579) | **真实但音频-only** | SI4732 不出 IQ，`read_samples` 恒返回 None |
| HackRF One | `HackRFBackend` (L760) | **半占位** | API 粗糙，缺 AGC/带宽/LNA-VGA 分控 |
| USRP (UHD) | `USRPBackend` (L845) | **半占位** | 每次 recv 重建 streamer，缺 AGC/带宽/正确关闭 |
| IQ 文件回放 | `FileIQBackend` (L935) | **真实实现** | 多格式加载、循环、seek，但无变速/流式读盘 |
| **SpyServer 客户端** | — | **不存在** | 设计文档提及，代码中无任何类 |

---

## 1. 流式采样：read_samples 实现、丢样、时间戳

### [真bug] 1.1 录制线程与主显示线程竞争同一硬件 FIFO，样本被撕裂

`_recording_loop` (L236) 在后台线程里循环调用 `self.read_samples(chunk_size)`。与此同时，上层频谱/解调工具（`sdr_tools.py` 多处，如 L2298/L2458/L3715）也在主线程调用 `read_samples`。

两个线程并发从同一个 pyrtlsdr / hackrf 句柄读数据，pyrtlsdr 的 `read_samples()` 每次从 USB bulk buffer 取一块，两个消费者各自取走的块是交替的、不可预测的。后果：
- 录制文件中的 IQ 流不是连续时间序列，中间被插入了"另一线程抢走的块"；
- 频谱/瀑布图也会拿到丢了一半样本的流。

**这不是"加锁"就能简单修好的**——正确做法需要一个中央采样泵线程，读一次后 fan-out 到多个消费者队列（SDR++ 的 architecture 即如此）。当前架构没有这个组件。

- 位置：`sdr_backend.py:236`（录制线程调 `read_samples`）；消费者竞争见 `sdr_tools.py:2298,2458,3715`

### [真bug] 1.2 模拟后端每次 read_samples 相位从零开始，缓冲区边界处相位跳变

`MockSDRBackend.read_samples` 每调用一次都重新生成时间轴：

```python
# L362
t = np.arange(num_samples) / self.status.sample_rate_hz
carrier = self._signal_level * np.exp(1j * 2 * np.pi * freq_offset * t)
```

`np.arange` 每次都从 0 开始，因此相邻两次调用之间载波相位不连续。FM 模式同理（L371 `np.cumsum(modulating)` 每次从 0 累积）。对逐块 FFT 频谱影响不大，但对需要相位连续的解调（FM/SSB/数字模式）会在缓冲区边界产生 click/相位跳变。正确做法应在 `self` 上维护 `_phase_acc` 并在每次调用末尾累加。

- 位置：`sdr_backend.py:362,366,371-372`

### [建议] 1.3 无 per-sample 时间戳，无丢样/溢出上报

`read_samples` 的返回值是纯 `np.ndarray`，不带 (timestamp, sample_count) 元组。基类 `SDRBackend` 也没有 `last_seq / overflow_count` 等字段。对以下场景无法支撑：
- 数字模式（ADS-B / FT8 / AIS）需要精确采样时间戳做帧同步；
- USB buffer 溢出时无法检测（pyrtlsdr 内部 overflow 会静默丢样本）。

`SDRStatus` 只有一个累计 `samples_read`（L68），无增量溢出计数。

- 位置：`sdr_backend.py:161-163`（基类接口），`L571-576`（RTL 实现）

### [建议] 1.4 RTLSDR read_samples 不做返回长度校验

```python
# L574
samples = self._sdr.read_samples(num_samples)
self._samples_read += num_samples   # ← 用请求数而非实际返回数
```

pyrtlsdr 的 `read_samples(n)` 文档承诺返回 n 个，但 rtl_tcp 模式下网络抖动可能导致短读。这里按请求数累加 `_samples_read`，若实际短读则计数虚高。对比 USRP 后端（L928）用的是 `len(samples[0])`，更稳健。

- 位置：`sdr_backend.py:575`

---

## 2. IQ 处理管道：DC 偏移 / IQ 不平衡 / AGC 是否真接入

### [真bug] 2.1 DC 偏移校正与 IQ 不平衡校正**没有接入**流式 read_samples 管道

代码里确实有完整的校正算法：
- `DCBlocker` 类（`dsp.py:23`，一阶 IIR，流式有状态）；
- `IQCalibrator` 类（`dsp.py:66`，协方差白化）；
- `front_end()` 函数（`dsp.py:189`，一站式 DC→IQ→抽取）。

但它们**只被一个一次性工具 `_iq_correct` 调用**（`sdr_tools.py:3634-3656`），即用户手动触发一次"采 16384 样本→校正→打印诊断"。这个校正**不持久化、不进入后续 read_samples 流**。

实际影响：所有真实解调/解码工具直接消费 `backend.read_samples()` 原始 IQ，零中频 SDR 的 DC 尖峰（中心频率处）和 I/Q 镜像（边带泄漏）原样进入解调链。这意味着：
- 中心频率附近有本振泄漏尖峰，FM/AM 解调时直流附近有持续噪声；
- SSB/CW 下边带会泄漏上边带镜像。

对比 SDR++：DC removal 和 IQ balance 是在主采样链上常驻开关，采样后立即处理再分发到各模块。本项目缺这一层。

- 位置：`sdr_backend.py:571-576`（RTL read_samples 原样返回，无校正）；`dsp.py:189-222`（front_end 存在但未接入）；`sdr_tools.py:3623-3694`（仅一次性工具）

### [占位] 2.2 Mock 后端的 AGC 是假的——启用时增益恒为 1.0

```python
# L378
gain = 10 ** (self.status.gain_db / 20.0) if not self.status.agc_enabled else 1.0
```

AGC 开启时增益直接设为 1.0，没有任何基于信号功率的自动调节。`rssi_db`/`snr_db` 每块都算（L386-387）但从不反馈到增益环。这是占位实现，不是真 AGC。

- 位置：`sdr_backend.py:378`

### [空壳] 2.3 HackRF / USRP 的 AGC 未实现硬件侧下发

基类 `set_agc`（L132-136）只改 `self.status.agc_enabled` 标志位。`RTLSDRBackend` 覆写了 `set_agc` 真正调硬件（L510-521）。但：
- `HackRFBackend`（L760-842）**没有覆写 set_agc**——HackRF One 本身也没有硬件 AGC，需要软件 AGC，但代码里完全没有；
- `USRPBackend`（L845-932）**也没有覆写 set_agc**——UHD 有 `set_rx_agc(True)`，未调用。

- 位置：`sdr_backend.py:760-842`（HackRF 无 set_agc）；`L845-932`（USRP 无 set_agc）

### [空壳] 2.4 HackRF / USRP 的 set_bandwidth 未下发硬件

基类 `set_bandwidth`（L138-142）只改状态。RTL 后端覆写了（L523-531）。HackRF 和 USRP 均未覆写——调谐器带宽永远是默认值，用户设带宽无效。

- 位置：`sdr_backend.py:760-842`；`L845-932`

---

## 3. 模拟后端：合成 IQ 信号模型

### 评估：信号模型基本正确，但有缺陷

`MockSDRBackend`（L324-389）生成的信号包含：
1. **中心载波 + 1kHz 频偏**（L365-366）：`exp(j·2π·1000·t)`——正确；
2. **FM 调制**（L369-372）：调制信号 `0.5·sin(2π·1000·t)`，相位 `2π·Δf·cumsum(m)/fs`——这是 FM 的正确离散近似（相位 = 2π·Δf·∫m dt）；
3. **复高斯噪声**（L375）：`randn + j·randn`——正确；
4. **RSSI/SNR 计算**（L384-387）：基于信号/噪声功率——公式正确。

### [真bug] 3.1 相位不连续（同 1.2）

每次 read_samples 都从 t=0 重新开始，相邻缓冲区之间载波相位跳变。见 1.2。

### [建议] 3.2 信号模型过于简单，无法测试数字模式

只有单音载波 + 单音 FM。没有：
- 带通噪声（模拟真实天线底噪分布在不同频段）；
- 多信号共存（一个强 FM + 一个弱 AM + 一个 CW）；
- 频率漂移（晶振 ppm 漂移导致载波随时间滑动）；
- DC 偏移 + IQ 不平衡的模拟（用于测试 2.1 中提到的校正链——但校正链本身没接入，所以也没法测）。

对"无硬件测试解码器"这个用途，FT8/ADS-B/AX.25 等数字解码器无法用 mock 后端验证——它不产生这些波形。项目里 FileIQBackend 回放真实录制是更实际的无硬件测试路径。

- 位置：`sdr_backend.py:357-389`

---

## 4. 文件回放后端

### 4.1 支持的格式（真实实现）

`FileIQBackend._load`（L973-1013）支持：
- `.npy`（complex 一维数组）
- `.cu8/.u8/.bin`（unsigned 8-bit 交错，rtlsdr 原生）
- `.cfile/.cf32/.iq/.fc32`（float32 交错，GNU Radio 格式）
- `.cs16/.s16/.sc16`（int16 交错）

sidecar JSON 兼容两种命名（`file.json` 和 `file.ext.json`，L977-981），与录制端写出的 sidecar 键名对齐（L285-306）。**录制→回放闭环是通的。**

### 4.2 循环播放：支持

`loop=True`（L951 默认），read_samples 末尾跨接 tail+head（L1043-1047）。边界处理逻辑经过逐用例推演是正确的。

### [建议] 4.3 无速度控制 / 无实时 pacing

`read_samples`（L1030-1052）纯粹从内存切片返回，没有任何 `time.sleep` 或基于实际耗时的速率控制。调用方要多少就给多少，速度取决于 CPU。

后果：
- 如果上层想"像真实 SDR 一样实时播放"（例如边播边听音频），必须自己在外层做 pacing；
- 当前实现对"快速离线跑解码器"是友好的（越快越好），但对"模拟实时设备"不友好。

SDR++ 的 IQ file source 有 "speed" 滑块（0.5x/1x/2x/max）。本项目没有。

- 位置：`sdr_backend.py:1030-1052`

### [建议] 4.4 整文件加载进内存，无流式读盘

`_load` 一次性 `np.fromfile` 整个文件到 `self._samples`（L997, L1002, L1006）。对 1 分钟 2.4MHz complex128 ≈ 2.3 GB，内存占用巨大。没有 `np.memmap` 或分块读取。对短录音（<10s）没问题，对长录音不现实。

- 位置：`sdr_backend.py:995-1008`

### [建议] 4.5 read_samples 不必要的 .copy()

```python
# L1040
out = self._samples[self._cursor:end].copy()
```

非跨边界路径下，`.copy()` 强制拷贝。如果调用方只读不改，返回 view 即可省一次内存带宽拷贝。跨边界路径（L1046 `np.concatenate`）本来就必须新建数组。可把 L1040 改为不拷贝，但需在文档中注明"返回只读 view"。

- 位置：`sdr_backend.py:1040`

---

## 5. SpyServer 客户端

### [空壳/缺失] 5.1 SpyServer 客户端完全未实现

设计文档（`AI定义无线电-概念定义与框架-v2.1.md:724`）写了"网络 SDR（rtl_tcp/**spyserver**）"，但代码中：
- 没有 `SpyServerBackend` 类；
- 没有任何 SpyServer 协议握手（SpyServer 二进制协议：首字节版本+端口+增益标记，后续流式 IQ + 稀疏控制信令）；
- `RTLSDRBackend(host=…)` 走的是 **rtl_tcp 协议**（pyrtlsdr 的 `RtlSdrTcpClient`），这与 SpyServer 协议完全不同。

rtl_tcp 和 SpyServer 是两个不兼容的网络协议：
- rtl_tcp：简单 TCP 字节流，单客户端，控制指令走同一 socket；
- SpyServer（SDR++ 生态）：多客户端共享，二进制握手协议，支持增益/频率/采样率动态调整，有稀疏控制信道。

**结论：项目对标 SDR++ 的 SpyServer 这一块是 0% 实现。** 只有 rtl_tcp 作为部分替代。

- 位置：`sdr_backend.py` 全文无 SpyServer 类；设计文档提及见 `AI定义无线电-概念定义与框架-v2.1.md:724`

---

## 6. 多设备支持

### [空壳] 6.1 SDRBackendManager 只支持单 active 设备，"异构双前端同时使用"名不副实

`SDRBackendManager`（L1078-1196）维护 `self.backends: Dict[str, SDRBackend]` 字典，可以注册多个后端实例。但：
- `self.active_backend` 是**单引用**（L1088）；
- `switch_device`（L1165-1172）先 `disconnect()` 旧设备再 `connect()` 新设备——**同一时刻只有一个设备处于 connected 状态**；
- 没有"同时从两个设备 read_samples"的 API（没有 `read_from(backend_id)` 多源聚合）；
- `open_iq_file`（L1184-1188）打开文件回放时也会 disconnect 当前设备。

类 docstring（L1083）写"支持异构双前端（RTL-SDR + ai-sdr Mini 同时使用）"，但代码层面**没有任何双前端并发采集机制**。要同时用两个设备，上层必须自己持有两个 backend 实例并各自调 read_samples——manager 不帮你做这件事。

- 位置：`sdr_backend.py:1083`（docstring 声称）vs `L1165-1172`（switch 先 disconnect）

---

## 7. 性能问题

### [建议] 7.1 录制循环的 dtype 反复升级 + column_stack 多余拷贝

```python
# L245
z = np.asarray(samples, dtype=np.complex128) * gain
# L247
np.column_stack([z.real, z.imag]).astype(np.float32).tofile(fh)
```

- pyrtlsdr 返回的是 complex64（float32），这里先升到 complex128（float64），再乘 gain，再拆成 float32 写盘——中间多了一次 float64 往返；
- `np.column_stack([z.real, z.imag])` 创建一个中间 (N,2) float64 数组，再 `.astype(float32)` 再创建一个 (N,2) float32 数组。可以直接 `np.c_[z.real.astype(np.float32), z.imag.astype(np.float32)]` 或用 `z.view(np.float32).reshape(-1,2)`。

对 2.4MS/s 持续录制，这些多余拷贝每秒钟产生数十 MB 的临时数组，增加 GC 压力。

- 位置：`sdr_backend.py:245-256`

### [建议] 7.2 录制 decimation 用裸切片，无抗混叠滤波

```python
# L242
if decimation > 1:
    samples = samples[::decimation]
```

这是裸抽取（bare decimation），没有先低通。如果 decimation=4（2.4M→600k），高于 300kHz 的能量会混叠到基带。`dsp.py:154` 有正确的 `decimate()` 函数（Hanning 窗 sinc 低通 + 抽取），但录制循环没用它。

- 位置：`sdr_backend.py:241-242`；对比正确实现 `dsp.py:154-167`

### [建议] 7.3 DCBlocker 用 Python for 循环逐样本

```python
# dsp.py:53-56
for n in range(len(x)):
    y[n] = x[n] - x_prev + r * y_prev
    x_prev = x[n]
    y_prev = y[n]
```

一阶 IIR 完全可以用 `scipy.signal.lfilter` 或 `np.convolve` 向量化，Python for 循环在 2.4MS/s 下每块 16384 次迭代，是流式路径上的热点。不过这个类当前不在主路径上（见 2.1），所以实际性能影响暂未显现。

- 位置：`dsp.py:53-56`

### [建议] 7.4 USRP 每次 read_samples 重建 streamer

```python
# L925-926
samples = self._usrp.recv_num_samps(num_samples, self.status.frequency_hz,
                                     self.status.sample_rate_hz, [0], 0.1)
```

`recv_num_samps` 是 UHD 的便捷方法，每次调用内部创建 RxStreamer → tune → recv → destroy streamer。正确做法是在 `connect()` 时创建一次 `self._rx_stream = self._usrp.rx_streamer(...)`，之后反复复用。当前实现每 16384 样本就重建一次 streamer，开销巨大且会在 tune 间隙丢样本。

- 位置：`sdr_backend.py:921-932`

### GIL 评估

- pyrtlsdr 的 `read_samples` 在 C 层读 USB，**释放 GIL**，Python 侧 numpy 计算持有 GIL 但都是短时操作；
- 录制线程（L209-279）主要做文件 I/O（`tofile`）和 numpy 运算，文件写盘也释放 GIL；
- 没有明显的 GIL 死锁或长时间持锁问题。主要并发问题是**逻辑竞争**（1.1），不是 GIL。

---

## 8. 其他 bug

### [真bug] 8.1 AISDRMiniBackend._refresh_status 写入了不存在的字段名

```python
# L693
self.status.rssi = float(result["rssi"])     # SDRStatus 字段是 rssi_db，不是 rssi
# L695
self.status.snr = float(result["snr"])        # 应为 snr_db
```

`SDRStatus` 数据类定义的字段是 `rssi_db`（L62）和 `snr_db`（L63）。这里写成 `self.status.rssi` 和 `self.status.snr`，在普通 Python 对象上动态新增了两个属性，但 `get_status()` 返回的 `self.status` 里 `rssi_db`/`snr_db` 仍然是默认值 -100/0。**设备上报的 RSSI/SNR 被静默丢弃。**

- 位置：`sdr_backend.py:693,695`

### [真bug] 8.2 AISDRMiniBackend._refresh_status 音量范围不匹配

```python
# L697
self.status.volume = int(result["volume"])
```

`SDRStatus.volume` 定义为 0.0–1.0 float（L61），`set_volume`（L157-159）也 clamp 到 [0,1]。但 SI4732 的音量是 0–63（见 `set_volume` L723-725 注释）。这里直接 `int(result["volume"])` 写入 0–63 的值，会导致上层 UI 看到 volume=42.0（远超 1.0）。

- 位置：`sdr_backend.py:697`；对比 `L723-730`（正确的 set_volume 做了 clamp）

### [真bug] 8.3 USRPBackend.disconnect 未真正关闭设备

```python
# L881-889
def disconnect(self):
    if self._rx_stream:
        self._rx_stream = None    # 只是置 None，没 close
    if self._usrp:
        self._usrp = None         # 只是置 None，没 close
```

没有调用 `self._usrp.close()` 或释放 streamer 的 `stop()`/`close()`。UHD 设备句柄和 USB/网络资源不会被释放，重复 connect/disconnect 会泄漏。对比 RTLSDRBackend.disconnect（L477-484）正确调用了 `self._sdr.close()`。

- 位置：`sdr_backend.py:881-889`

### [建议] 8.4 HackRF gain 接口与硬件不符

```python
# L829
self._hackrf.gain = int(gain_db)
```

HackRF One 有**独立的 LNA gain（0–40 dB，8 dB 步进）和 VGA gain（0–62 dB，2 dB 步进）**，没有单一 `gain` 属性。Python `hackrf` 包的 API 是 `hackrf.lna_gain = ...` 和 `hackrf.vga_gain = ...`。当前赋值大概率抛异常被 L830 吞掉，gain 设置实际无效。

- 位置：`sdr_backend.py:824-832`

### [建议] 8.5 AISDRMiniBackend._send_mcp 的消息 ID 可能碰撞

```python
# L613
"id": int(time.time() * 1000) % 100000,
```

毫秒时间戳 mod 100000，在 100ms 窗口内的并发请求会拿到相同 ID。虽然有 `_ws_lock` 串行化（L610），但如果 ESP32 端异步推送消息，响应匹配可能错乱。建议用单调递增计数器。

- 位置：`sdr_backend.py:613`

---

## 9. 总结评分

| 审查项 | 评级 | 关键问题 |
|---|---|---|
| 流式 read_samples | ⚠️ | 录制/显示竞争样本；无时间戳；mock 相位不连续 |
| DC/IQ/AGC 管道 | ❌ | 校正算法存在但未接入流式；HackRF/USRP AGC 空壳 |
| 模拟后端 | ⚠️ | 信号模型基本正确但相位不连续、信号种类单一 |
| 文件回放 | ✅ | 闭环可用，但无变速、全量入内存 |
| SpyServer 客户端 | ❌ | 完全未实现，仅设计文档提及 |
| 多设备 | ⚠️ | 可注册多后端，但单 active，无双流并发 |
| 性能 | ⚠️ | 录制 dtype 冗余拷贝；USRP streamer 重建；decimation 无抗混叠 |

**最严重的三个问题：**
1. **[真bug] 录制与显示线程竞争同一 SDR 句柄**（1.1）——录制文件数据不连续；
2. **[真bug] DC/IQ 校正未接入流式管道**（2.1）——所有解调链路都在吃未校正的零中频 IQ；
3. **[空壳] SpyServer 客户端完全缺失**（5.1）——对标 SDR++ 的核心网络 SDR 能力为 0。
