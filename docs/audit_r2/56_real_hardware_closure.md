# R2 审查 #56 — RTL-SDR 真实硬件闭环 & Chromebook Crostini USB 透传

> 审查范围：`mbdsdr_ai/sdr_backend.py`、`mbdsdr_ai/hal.py`、`mbdsdr_ai/sdr_tools.py`、`desktop/mcp_worker.py`、`desktop/spectrum_widget.py`、`desktop/main_window.py`、`requirements.txt`、`docs/CHROMEOS_CROSTINI_SDR.md`、`scripts/chromebook_setup.sh`、`tests/`。
> 结论先行：**从 MBDSDR 桌面 UI 出发、经过 AI 内核到真实 RTL-SDR 棒的完整链路目前是断的。** RTLSDRBackend 本身能独立工作（被 `scripts/rtl_selfcheck.py` 直接调用时），但它没有被桌面端、也没有被 hal.py 的 SoapySDR 路径真正接上；桌面频谱 100% 是合成高斯峰；hal.py 的 SoapySDR 流从未 setupStream；两个 HardwareManager 实例不共享状态。Crostini 文档是齐全的，但运行时代码零环境检测、零 USB 权限提示。

---

## 1. RTL-SDR 完整闭环链路图（标注每环节状态）

```
┌──────────────────────────────────────────────────────────────────────────┐
│ 桌面 UI (desktop/)                                                         │
├──────────────────────────────────────────────────────────────────────────┤
│ ControlPanel                                                              │
│  .tune_sdr_requested(Hz, mode)  ──────────► main_window._on_tune_sdr       │
│                                            │                              │
│                                            ▼                              │
│ main_window.py:516   _worker.call_tool("tune_sdr", {...})    [断][真bug]  │
│ main_window.py:748   _worker.call_tool("sdr_set_frequency",..) [断][真bug]│
│                                            │                              │
│                                            ▼ (跨线程直调)                │
│ MCPWorker (QThread)                                                       │
│  mcp_worker.py:256 method_map = {tune_fm, tune_am, set_volume,           │
│                                   start_record, stop_record,             │
│                                   get_version, reboot, list_tools}       │
│         ★ 没有 "tune_sdr"、没有 "sdr_set_frequency"、没有 "sdr_connect"  │
│         ★ 整个 Worker 只连 ws://192.168.4.1:81 (ai-sdr Mini SI4732),     │
│           完全不引用 SDRBackendManager / RTLSDRBackend / pyrtlsdr         │
│                                            │                              │
│                                            ▼                              │
│  SpectrumDataGenerator (spectrum_widget.py:30)                            │
│   硬编码 5 个 FM 电台高斯峰 + 噪声, IQ 接口"预留"但从未接   [断][空壳]   │
└──────────────────────────────────────────────────────────────────────────┘
                          │ (无数据上行路径)
                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ AI 内核 / MCP 工具层 (mbdsdr_ai/sdr_tools.py)                             │
├──────────────────────────────────────────────────────────────────────────┤
│ register_sdr_tools()                                                      │
│  agent.sdr_manager = SDRBackendManager()        ← 真实后端入口            │
│  sdr_connect / sdr_set_frequency / sdr_set_sample_rate / ...             │
│     │                                                                      │
│     ├─ sdr_tools.py:324-326  set_frequency 被调用两次, 结果二次判定[bug]│
│     ├─ 越界返回 False 时 content 统一说"超出范围或未连接"     [建议]      │
│     ▼                                                                      │
│  ── 另一条平行的 hal.py 工具链 ──                                          │
│  sdr_list_hardware / sdr_connect_hardware / sdr_transmit_cw /             │
│  platform_info / instrument_*                                              │
│     │ 每次调用都 mgr = HardwareManager()  [真bug] 非单例                   │
│     │ connect 与 transmit_cw 是两个不同的 mgr 实例                       │
│     ▼                                                                      │
└──────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ 后端抽象 (sdr_backend.py)                                                 │
├──────────────────────────────────────────────────────────────────────────┤
│ SDRBackendManager._discover()  (sdr_backend.py:1091)                    │
│   try: RtlSdr(0); close(); 注册 RTLSDRBackend(0)                         │
│   except: pass            ← 无棒/无 pyrtlsdr/权限拒绝 一律静默 [真bug]   │
│   默认 active = MockSDRBackend  ← 即使插了棒默认也是模拟                  │
│                                                                           │
│ RTLSDRBackend.connect()  (sdr_backend.py:454)                             │
│   from rtlsdr import RtlSdr; self._sdr = RtlSdr(idx)                      │
│   ★ 不回读 hardware.center_freq / sample_rate                              │
│   ★ 不把 status.sample_rate_hz 与 librtlsdr 实际采样率对账 [真bug]       │
│   ★ 不读 tuner 的真实增益范围 / 带宽                                       │
│                                                                           │
│ base.set_frequency()    (sdr_backend.py:101)  只检声明范围, 不回读       │
│ base.set_sample_rate()  (sdr_backend.py:113)  ★★★ 不检 range 一致性     │
│                                              2.4 MHz 写下去后棒可能       │
│                                              自动到 2.0/2.048 MHz         │
│                                                                           │
│ RTLSDRBackend.read_samples() (sdr_backend.py:571)                        │
│   self._sdr.read_samples(n)   ★ 无 try/except, USB 拔出→裸异常 [真bug]  │
└──────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ hal.py SoapySDR 路径 (平行宇宙, 不与上面互通)                              │
├──────────────────────────────────────────────────────────────────────────┤
│ SoapySDRBackend.connect() (hal.py:221)                                   │
│   import SoapySDR; self._device = SoapySDR.Device(args)                  │
│                                                                           │
│ SoapySDRBackend.set_frequency() (hal.py:253)                             │
│   self._device.setFrequency(SoapySDR.SOAPY_SDR_RX, ...)                   │
│   ★ SoapySDR 仅在 connect() 局部 import, 这里 NameError [真bug]          │
│   ★ 被 bare except 吞掉, 只打 warning                                     │
│                                                                           │
│ self._rx_stream                                                            │
│   ★★★ 全文件从未 setupStream(), read_rx 永远走"模拟噪声"分支 [空壳]    │
│   read_rx() (hal.py:280)  even when _device is set, _rx_stream is None   │
│                                                                           │
│ HardwareManager (hal.py:576)  非单例, 无 __new__ / 模块级实例            │
└──────────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│ OS / USB / ChromeOS                                                        │
├──────────────────────────────────────────────────────────────────────────┤
│ requirements.txt  ★★★ 没有 pyrtlsdr!                                       │
│   硬件依赖段只注释了 pyserial / SoapySDR (requirements.txt:22-23)          │
│   pip install -r requirements.txt 不会装 pyrtlsdr [真bug]                 │
│                                                                           │
│ detect_embedded_platform() (hal.py:525)                                   │
│   检测树莓派/Jetson/Win-ARM/macOS-ARM                                     │
│   ★ 没有 Crostini / ChromeOS 分支 [空壳]                                  │
│                                                                           │
│ 文档层: docs/CHROMEOS_CROSTINI_SDR.md (138 行) + scripts/chromebook_setup.sh │
│   文档齐全, 但运行时无任何环境检测/错误提示         [建议-文档有码无]     │
└──────────────────────────────────────────────────────────────────────────┘
```

图例：**[真bug]** = 代码逻辑错误，真实硬件必现；**[空壳]** = 接口/路径存在但未实现；**[占位]** = 注释/TODO 承诺；**[建议]** = 体验/鲁棒性问题。

---

## 2. 逐环节发现清单

### 2.1 设备枚举与连接

| # | 标签 | 位置 | 发现 |
|---|---|---|---|
| F1 | [真bug] | `mbdsdr_ai/sdr_backend.py:1098-1105` | `SDRBackendManager._discover()` 用 `RtlSdr(0)` 探测，任何异常（无 pyrtlsdr、无设备、权限拒绝、设备被占用、Crostini 未透传）都被 `except Exception: pass` 吞掉。用户看不到"为什么没看到棒"，只知道默认 active 是 Mock。 |
| F2 | [真bug] | `requirements.txt:21-23` | 硬件依赖段**没有 `pyrtlsdr`**。注释里只提 `pyserial` 和 `SoapySDR`。`pip install -r requirements.txt` 之后 `from rtlsdr import RtlSdr` 直接 ImportError，被 F1 静默吞掉。文档 `docs/CHROMEOS_CROSTINI_SDR.md:74` 不得不让用户手动 `pip3 install pyrtlsdr`。 |
| F3 | [真bug] | `mbdsdr_ai/sdr_backend.py:454-475` | `RTLSDRBackend.connect()` 连上后**不回读** `center_freq`、`sample_rate`、`tuner_gains`、`bandwidth`。`SDRStatus` 保留 dataclass 默认值（100 MHz / 2.4 MHz / gain=0 / AGC=True）。一旦硬件实际值不同，`sdr_status` 工具报给 AI 的就是错的。 |
| F4 | [真bug] | `mbdsdr_ai/sdr_backend.py:113-117` | 基类 `set_sample_rate()` **不校验** `device.sample_rate_range`（声明了 250 kHz–3.2 MHz 但从不使用）。RTL-SDR 实际只接受离散档位，用户传 2.4 MHz 时 librtlsdr 可能落到 2.0/2.048 MHz，但 `status.sample_rate_hz` 仍记 2.4 MHz，后续 FFT/解调全部按错误采样率换算频率。 |
| F5 | [真bug] | `mbdsdr_ai/sdr_backend.py:101-108` | 基类 `set_frequency()` 只检查声明的 `frequency_range`，不回读硬件实际中心频率。R820T 有调谐边界、ppm 校偏后实际 freq ≠ 请求 freq，状态完全失真。 |
| F6 | [空壳] | `mbdsdr_ai/sdr_backend.py:1098-1105` | HackRF/USRP 的"探测"用 `import hackrf` / `import uhd` 成功就算可用，**从不真正打开设备**（注释 line 1111 也承认"不实际连接，只注册"）。注册出的条目是不可用状态。 |
| F7 | [建议] | `mbdsdr_ai/sdr_backend.py:1129` | 默认 `active_backend = mock`。即使枚举到 RTL-SDR 也不自动激活，用户/AI 必须显式 `sdr_connect(device_id="rtl_0")`。这与文档"插上棒就能用"的暗示不符。 |

### 2.2 频率/采样率/增益设置工具层

| # | 标签 | 位置 | 发现 |
|---|---|---|---|
| F8 | [真bug] | `mbdsdr_ai/sdr_tools.py:324-326` | `sdr_set_frequency` handler 里**调用了两次** `set_frequency(args["frequency_hz"])`：一次取 success，一次三元表达式取 content。如果第一次成功但第二次因状态变化返回 False（例如 decimation 中途、锁相失败），content 会显示"频率设置失败（超出设备范围或未连接）"但 success=True。 |
| F9 | [真bug] | `mbdsdr_ai/sdr_tools.py:349-351` | `sdr_set_sample_rate` 同样两次调用。 |
| F10 | [真bug] | `mbdsdr_ai/sdr_tools.py:416` | `sdr_set_demod` 同样两次调用 `set_demod`。 |
| F11 | [真bug] | `mbdsdr_ai/sdr_backend.py:486-491` | `RTLSDRBackend.set_frequency` 写 `self._sdr.center_freq = freq_hz` 不 try/except。librtlsdr 在频率不被支持时可能 raise；一旦 raise，`super().set_frequency()` 已经把 `status.frequency_hz` 更新了（line 107），硬件却没收到，状态与硬件错位。 |
| F12 | [真bug] | `mbdsdr_ai/sdr_backend.py:493-498` | `set_sample_rate` 同样：基类先改状态，再写硬件，硬件失败时状态不回滚。 |
| F13 | [建议] | `mbdsdr_ai/sdr_tools.py:326` | 越界/未连接时 content 固定为"超出设备范围或未连接"，不区分原因，AI 无法据此做修复动作。 |

### 2.3 采样 → 解调 → 显示

| # | 标签 | 位置 | 发现 |
|---|---|---|---|
| F14 | [真bug] | `mbdsdr_ai/sdr_backend.py:571-576` | `RTLSDRBackend.read_samples()` **没有 try/except**。USB 拔出、EPIPE、IO 错误会直接向上抛。录制线程 `_recording_loop` (line 236) 只在最外层 `except Exception` 打一行 `录制线程错误`，不重连、不通知、不降级。 |
| F15 | [空壳] | `desktop/spectrum_widget.py:30-83` | `SpectrumDataGenerator` 硬编码 5 个 FM 电台（98.5/97.4/100/95.5/101.8 MHz）的高斯峰，叠加随机噪声。文件头注释 line 6-7 明说"由于 SI4732 不出原始 IQ，频谱数据由 RSSI 扫频/模拟生成，预留真实 IQ 数据接口"。**没有任何 `set_iq_samples()` / `push_spectrum()` 方法**，真实 IQ 永远进不来。 |
| F16 | [空壳] | `desktop/mcp_worker.py` 全文 | Worker 只服务 ai-sdr Mini（SI4732 over WebSocket）。`method_map` (line 256-265) 只有 8 个方法，全部是 SI4732 的 tune_fm/tune_am/set_volume/录音。**完全没有引用 SDRBackendManager、RTLSDRBackend、spectrum_processor**。桌面端无法通过 Worker 取到任何 RTL-SDR IQ。 |
| F17 | [真bug] | `desktop/main_window.py:516-517` | `_on_tune_sdr` 调 `self._worker.call_tool("tune_sdr", {"freq_hz":..., "mode":...})`。`mcp_worker.py:267-270` 的 method_map **没有 "tune_sdr"**，走 else 分支返回 `{"error": "未知工具: tune_sdr"}`。用户在控制面板点"航空 118.000 AM"预设 → 完全没反应。 |
| F18 | [真bug] | `desktop/main_window.py:748` | 卫星跟踪联动调谐调 `self._worker.call_tool("sdr_set_frequency", ...)`。同样不在 method_map 里 → "未知工具"。外面还包了 `try/except: pass`（line 749-750），错误被吞，UI 显示"已自动调谐"（line 761）但硬件根本没动。 |
| F19 | [真bug] | `desktop/mcp_worker.py:233-249` + `desktop/main_window.py:505,511,516,531,537,544,573,585,748` | `call_tool` 是 `@Slot`，但所有调用点都是从 UI 线程**直接** `self._worker.call_tool(...)`，没有走信号-槽的 queued connection。Worker 已经 `moveToThread`，直接跨线程调用 `self.client`（websocket）是 Qt 禁忌，会随机崩、随机数据竞争。 |
| F20 | [空壳] | `mbdsdr_ai/hal.py:280-295` | `SoapySDRBackend.read_rx()`：`self._rx_stream` 永远是 `None`（全文件无 `setupStream` 调用），所以即使 `_device` 已连，也走到 line 292-295 的"模拟数据"分支返回白噪声。**SoapySDR 路径下真实 IQ 永远不会被读出来。** |
| F21 | [真bug] | `mbdsdr_ai/hal.py:284` | `buff = np.array([np.complex64] * num_samples)` 构造的是 **dtype=object** 的一维数组（每个元素是类型对象），不是 complex64 样本缓冲。即使 `_rx_stream` 被建起来，`read(buff, ...)` 也会立即 TypeError。 |
| F22 | [真bug] | `mbdsdr_ai/hal.py:253-259, 261-267, 269-278` | `set_frequency/set_sample_rate/set_gain` 内部引用 `SoapySDR.SOAPY_SDR_RX`，但 `SoapySDR` 只在 `connect()` (line 224) 局部 import。connect 成功之后，任何后续调谐都会触发 `NameError`，被 `except Exception` 吞掉只打 warning。**连接成功后第一发调谐就失败。** |

### 2.4 双 SDR Manager / 非单例

| # | 标签 | 位置 | 发现 |
|---|---|---|---|
| F23 | [真bug] | `mbdsdr_ai/hal.py:576-587` | `HardwareManager` 没有 `__new__` / 没有模块级 `get_instance()` / 没有 lru_cache。每次 `HardwareManager()` 都是全新对象，`_active_backend = None`。 |
| F24 | [真bug] | `mbdsdr_ai/sdr_tools.py:4797` | `_sdr_connect_hardware` 里 `mgr = HardwareManager(); mgr.connect_sdr(device)`。连接状态存在这个临时 mgr 的 `_active_backend` 里，函数返回后 mgr 被 GC。 |
| F25 | [真bug] | `mbdsdr_ai/sdr_tools.py:4823-4827` | `_sdr_transmit_cw` 里**又** `mgr = HardwareManager()`，然后 `mgr.get_active_backend()` 必然返回 None → 直接返回 `"错误: 未连接任何SDR设备，请先调用 sdr_connect_hardware"`。即使上一步 connect 明明成功过。tool_param_smoke_result.json:405 已经把这个错误串当"预期"记录下来了——等于团队已默认它是坏的。 |
| F26 | [真bug] | `mbdsdr_ai/sdr_tools.py:58-59` vs `:4797` | 系统里同时存在两个硬件管理器：`agent.sdr_manager = SDRBackendManager()`（sdr_backend.py:1078）和 `HardwareManager`（hal.py:576）。前者管 sdr_connect/sdr_set_frequency/sdr_spectrum_* 等 ~30 个工具；后者管 sdr_list_hardware/sdr_connect_hardware/sdr_transmit_cw。两者状态完全独立，AI 调了 sdr_connect 再调 sdr_connect_hardware 会得到两套互不认识的"已连接"。 |

### 2.5 数据去向（采样后做什么）

| # | 标签 | 位置 | 发现 |
|---|---|---|---|
| F27 | [空壳] | `mbdsdr_ai/sdr_backend.py:209-279` | 录制线程 `_recording_loop` 是唯一真正消费 IQ 的地方：边采边写盘（cu8/cf32/cs16/wav/csv）。**没有任何实时路径把 IQ 推到频谱 widget / 解调 pipeline 给桌面显示**。MCP 工具 `sdr_spectrum_analyze` (sdr_tools.py:2856-2876) 是被动一次性 `read_samples(2048)` 算个 FFT 文本返回，不是流式。 |
| F28 | [建议] | `mbdsdr_ai/sdr_backend.py:693` | `AISDRMiniBackend._refresh_status` 写 `self.status.rssi = float(result["rssi"])` —— 但 `SDRStatus` dataclass 字段叫 `rssi_db`（sdr_backend.py:62）。这里给对象动态加了个 `rssi` 属性，`_format_status` 读 `rssi_db` 永远是默认 -100。 |
| F29 | [空壳] | `mbdsdr_ai/sdr_backend.py:755-757` | `AISDRMiniBackend.read_samples` 永远返回 None。AI 如果 active backend 是 ai-sdr Mini 又调 `sdr_spectrum_analyze`，会拿到 None / 崩。 |

### 2.6 错误恢复 / 热插拔

| # | 标签 | 位置 | 发现 |
|---|---|---|---|
| F30 | [真bug] | `mbdsdr_ai/sdr_backend.py:473-475` | `connect()` 的 `except Exception` 只把 `status.connected=False` 返回 False，不区分"无设备"vs"权限拒绝"vs"设备忙"vs"USB 拔出"，上层无法做差异化提示。 |
| F31 | [空壳] | 全仓 | `grep -ri "hot.*plug\|usb.*disconnect\|libusb.*error\|EPIPE"` 在 mbdsdr_ai/ 下零命中。没有 udev monitor、没有 USB 拔出事件回调、没有 read_samples 失败后自动重连。 |
| F32 | [建议] | `mbdsdr_ai/sdr_backend.py:267-268` | 录制线程异常只 `print(f"录制线程错误: {e}")`，不进 status、不发事件、不通知 AI，文件尾侧却照常写（line 278），用户以为录完了其实断流。 |

---

## 3. Chromebook / Crostini 专项

### 3.1 文档层（✅ 相对完善）
- `docs/CHROMEOS_CROSTINI_SDR.md`（138 行）：完整覆盖开 Linux 环境、USB 透传勾选、`rtl_test` 验证、权限组、rtl_tcp 兜底、桌面 GUI、故障速查表。
- `scripts/chromebook_setup.sh`：一键装依赖 + 最后打印"请在 Chrome 端勾选 USB"的人工步骤（line 83-101）。
- `scripts/rtl_selfcheck.py`：分阶段硬件自检脚本，专门给 Crostini 用户跑。

### 3.2 运行时代码（❌ 几乎为零）

| # | 标签 | 位置 | 发现 |
|---|---|---|---|
| F33 | [空壳] | `mbdsdr_ai/hal.py:525-569` | `detect_embedded_platform()` 检测树莓派/Jetson/Windows-ARM/macOS-ARM，**完全没有 Crostini/ChromeOS 分支**（不读 `/etc/os-release` 的 `CHROMEOS_RELEASE`、不检查 `/dev/.cros_workload_manager`、不看 `/proc/version` 里 "termina"）。`scripts/chromebook_setup.sh:29` 自己做了这个检测，但 Python 侧没有。 |
| F34 | [真bug] | `mbdsdr_ai/sdr_backend.py:1098-1105` | Crostini 用户没在"文件→共享 USB"里勾选 RTL-SDR 时，`RtlSdr(0)` 抛 `OSError`/`USBError`，被静默吞掉。用户看到的只有"列表里没有 RTL-SDR"，**没有任何提示"你可能没在 ChromeOS 端共享 USB 设备"**。文档第 131 行故障表说"lsusb 看不到棒 = 回第 2 步勾选"，但代码里没有把这个映射做出来。 |
| F35 | [真bug] | `requirements.txt:22-23` | 如 F2 所述，Crostini 文档让用户 `pip3 install -r requirements.txt` 后再手动补 `pyrtlsdr`，否则 `sdr_connect` 永远走不到真实棒。 |
| F36 | [建议] | `mbdsdr_ai/sdr_backend.py:431-452` | `list_devices()` 是静态方法，枚举失败只返回 `[]`，不带错误原因。建议在空列表时附带"常见原因：未装 pyrtlsdr / 未透传 USB / 设备被 dvb 驱动占用"。 |
| F37 | [建议] | 桌面端 | Crostini 上 GUI 走 X/Wayland 转发，频谱 widget 用 OpenGL 可能花屏（docs 第 137 行提到），但代码里 `HAS_OPENGL` 检测（spectrum_widget.py:18-23）只在 import 失败时降级，不检测"OpenGL 可用但 Crostini 软件渲染崩"。 |

---

## 4. 测试与真实硬件证据

### 4.1 tests/ 现状

| 文件 | 结论 |
|---|---|
| `tests/tool_selftest.py:3` | 文件头明说："在没有真实硬件（无 RTL-SDR 连接）的默认环境下，遍历全部已注册工具"。**没有硬件 gated 的 pytest fixture**，没有 `@pytest.mark.hardware`，没有 `RtlSdr.get_device_count()` 条件 skip。 |
| `tests/test_report.txt` | 228/228 通过、0 跳过、129 秒。全部基于 MockSDRBackend / MockSDRBackend(hal.py)。**不证明任何真实硬件路径能跑通。** |
| `tests/test_full_integration*.py` | 有 `skip()` 方法但 grep 不到任何 `RtlSdr` / `get_device_count` / 真实棒检测，skip 也是基于 mock 环境的逻辑分支，不是硬件缺失 skip。 |
| `tests/tool_param_smoke_result.json:393-405` | 把 `sdr_connect_hardware` 的 `"错误: 未连接任何SDR设备，请先调用 sdr_connect_hardware"` 当成"预期 friendly"记录——等于已经承认 F25 是坏的。 |

### 4.2 真实硬件产出物
- 仓库根目录有 `real_sstv_out.png`、`real_sstv.wav`、`real_r36_dc*.png`、`real_robot36*.png`、`real_sstv_robot36.png` 等真实接收产物。
- 但这些产物对应的路径（SSTV/robot36）走的是 `sdr_tools.py` 里的 `_wfm_stereo_tool` / `_noaa_apt_decode_tool` 等一次性 read_samples 解码工具，**不是桌面端实时频谱闭环**。
- `experiments/` 下没有 `rtl_selfcheck/` 目录（文档第 83 行承诺的输出位置），说明自检脚本可能从未在交付环境跑过、或产物未入库。

### 4.3 Mock vs Real 接口一致性
- `MockSDRBackend.read_samples` 返回 `complex128` 连续数组；`RTLSDRBackend.read_samples` 返回 pyrtlsdr 原生 `complex64`。上游 `spectrum_processor` / 解调代码如果按 complex128 强转，性能浪费 2 倍但功能正常；如果按 `dtype.kind` 分支，可能在 Mock 上过、在 real 上走错分支。
- `MockSDRBackend.connect()` 永远 True；`RTLSDRBackend.connect()` 失败时上层 `mgr.get_active()` 仍返回默认 mock，UI 显示"已连接: 模拟 SDR"（sdr_tools.py:241）——用户分不清自己连的是真棒还是模拟。
- `AISDRMiniBackend.read_samples` 返回 None（F29），与基类"返回复数数组"的契约不一致，所有 `backend.read_samples(n)` 的调用方都没判 None 就直接 FFT。

---

## 5. 闭环判定：哪些环节真的能跑通

| 链路环节 | 状态 | 说明 |
|---|---|---|
| 枚举 USB RTL-SDR | ⚠️ 半自动 | `RTLSDRBackend.list_devices()` 能列出，但 `SDRBackendManager._discover` 吞错误、默认不激活。 |
| 连接设备 | ⚠️ 能连但状态错 | `connect()` 能打开棒，但不回读真实 freq/sr/gain。 |
| 设频率 | ❌ 状态漂移 | 基类改状态在先、写硬件在后、失败不回滚、不回读。 |
| 设采样率 | ❌ 不校验档位 | 2.4 MHz 可能被棒舍入到 2.0/2.048 MHz，状态仍写 2.4。 |
| 设增益/AGC | ✅ 基本可用 | pyrtlsdr 封装较薄，异常被 try/except 吞掉但状态大体一致。 |
| 读 IQ 样本 | ⚠️ 单次可用 | `read_samples(n)` 一次性调用能拿到 complex64；长时间流没有异常保护。 |
| 一次性 FFT/解码 | ✅ 可用 | `sdr_spectrum_analyze` / `_noaa_apt_decode_tool` / `_wfm_stereo_tool` 等用一次性 read_samples 是能出图的（根目录 real_*.png 为证）。 |
| 录制写盘 | ⚠️ 半可用 | 录制线程边采边写，但 USB 断流只 print 不通知。 |
| 桌面实时频谱显示 | ❌ 完全断 | `SpectrumDataGenerator` 100% 合成；`MCPWorker` 根本不接 RTL-SDR。 |
| 桌面调谐真实棒 | ❌ 完全断 | `tune_sdr` / `sdr_set_frequency` 不在 worker method_map。 |
| hal.py SoapySDR 路径 | ❌ 完全断 | NameError + 无 setupStream + 非单例。 |
| Crostini 环境检测 | ❌ 无 | 文档齐全但运行时零检测。 |
| USB 热插拔/错误恢复 | ❌ 无 | 无 udev、无 EPIPE 处理、无自动重连。 |

**一句话结论**：MBDSDR 的 RTL-SDR 能力目前是"**脚本可跑、UI 不通、hal 路径空转**"。`scripts/rtl_selfcheck.py` 和根目录的 `real_sstv_*.png` 证明开发者手上的棒能采到信号；但从桌面按钮 → AI 工具 → 真实棒 → 实时频谱这条用户路径，没有任何一段是通的。

---

## 6. 优先级建议（仅基于本次审查）

| 优先级 | 项 | 动作 |
|---|---|---|
| P0 | F2/F35 | `requirements.txt` 加 `pyrtlsdr>=0.3.3` 到硬件段（或标记 extra）。 |
| P0 | F17/F18 | mcp_worker method_map 补 `tune_sdr` / `sdr_set_frequency`，或干脆让桌面直接持有 SDRBackendManager 而不是绕 MCPWorker。 |
| P0 | F25 | `HardwareManager` 改单例（模块级 `_instance`），或 sdr_tools 缓存它。 |
| P0 | F22 | hal.py 模块级 `import SoapySDR`（或在 connect 失败时把模块引用挂到 self）。 |
| P0 | F20/F21 | hal.py 加 `setupStream`，或直接删掉 SoapySDR 路径避免误导。 |
| P1 | F3/F4/F11/F12 | connect 后回读 hardware.center_freq / sample_rate；setter 用 try/except 包住并在失败时回滚状态。 |
| P1 | F14 | read_samples 加异常 → 置 `status.connected=False` + 事件。 |
| P1 | F33/F34 | `detect_embedded_platform` 加 Crostini 分支；list_devices 空时给"未透传 USB"提示。 |
| P2 | F15 | SpectrumDataGenerator 加 `push_iq(complex_array, sample_rate)` 接口，由录制/采样子线程喂数。 |
| P2 | F8/F9/F10 | handler 里 set_frequency 只调一次，结果存变量再用。 |
| P2 | F19 | 桌面调 worker 改走 Signal/Slot queued connection，不要直调。 |
