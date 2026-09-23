# 第二轮深度代码审查 — `mbdsdr_ai/hal.py`（Hardware Abstraction Layer）

- 审查文件：`mbdsdr_ai/hal.py`，690 行
- 对照文件：`mbdsdr_ai/sdr_backend.py`（1197 行，真正在用的 SDR 后端）、`mbdsdr_ai/gimbal.py`、`mbdsdr_ai/pose.py`、`mbdsdr_ai/gnss_monitor.py`、`mbdsdr_ai/sdr_tools.py`
- 审查方式：只读静态阅读 + 全仓 grep 交叉验证

---

## 0. 一句话结论

**`hal.py` 是一个"看起来很全、实际上没接通硬件"的第二层抽象壳。** 它宣称统一接入 RTL-SDR / HackRF / PlutoSDR / BladeRF / LimeSDR / USRP / Airspy / SDRplay / 示波器 / 信号源 / 频谱仪，但：

- 唯一的真实 SDR 驱动路径（SoapySDR）**从未创建过 RX/TX Stream**，`read_rx()` 在设备已连接时也永远走 mock 噪声分支；
- `set_frequency / set_sample_rate / set_gain / supports_tx` 全部因 `SoapySDR` 符号作用域错误而**静默失败**；
- 项目里真正跑采样闭环的是 `sdr_backend.py` 的 `pyrtlsdr` / `libhackrf` / `uhd` 直绑后端，**根本不经过 `hal.py`**；
- 没有 Gimbal、没有 GNSS 接收机（NMEA 串口）、没有天线控制器、没有 udev/DBus 热插拔、没有 Crostini/USB 权限处理——文档字符串里承诺的一半设备在代码里不存在。

---

## 1. HAL 架构：实际抽象了哪些硬件

`hal.py` 内部类清单（`hal.py:36-690`）：

| 类 | 行号 | 角色 | 真实性 |
|---|---|---|---|
| `DeviceCapability` (Enum) | `hal.py:36` | 能力标签 | 数据类，纯枚举 |
| `DeviceInfo` (dataclass) | `hal.py:49` | 设备信息 | 数据类 |
| `SDRBackendBase` (ABC) | `hal.py:66` | 抽象基类 | 接口定义 |
| `SoapySDRBackend` | `hal.py:128` | 声称统一 SoapySDR 驱动 | **半残，见 §3** |
| `MockSDRBackend` | `hal.py:335` | 无硬件模拟 | 真实可用，但与 `sdr_backend.MockSDRBackend` 重名 |
| `InstrumentBackend` | `hal.py:397` | SCPI/VISA 仪器 | 真实可用但有 bug，见 §3.6 |
| `detect_embedded_platform()` | `hal.py:525` | 平台探测 | 真实可用 |
| `HardwareManager` | `hal.py:576` | "单例"管理器 | **非单例，状态不共享**，见 §3.5 |

**hal.py 完全没有覆盖**以下审查范围里提到的设备（代码 grep 确认）：

| 设备类别 | 审查范围内预期 | hal.py 实际 | 真正位置 |
|---|---|---|---|
| Gimbal 云台/旋转器 | 应有 | **无任何类/方法** | `gimbal.py`（RotctldClient / BoardPwm / Manual） |
| GNSS 接收机（NMEA） | 应有 | **无任何串口驱动** | `pose.py`（`GPSData` 仅数据结构）+ `sdr_tools._get_gps`（`sdr_tools.py:3457` 直接返回硬编码模拟字符串） |
| 天线控制器 | 应有 | **无** | 无独立模块，散落在 `gimbal.py` |
| 发射机独立抽象 | 应有 | 仅 `write_tx()` 在 SoapySDR/Mock 里 | 真正 TX 在 `sdr_backend.HackRFBackend` |
| 示波器/信号源/频谱仪 | 应有 | `InstrumentBackend` 一个类全包 | 同 hal.py:397 |

> **[占位]** 模块 docstring（`hal.py:1-24`）承诺"统一 SDR 硬件接入…同时支持仪器接入"，但审查点里列的 Gimbal / GNSS / 天线控制器在本文件中**没有任何代码痕迹**。它们被放到了独立模块里，hal.py 并未把它们统一进来——命名上叫"HAL"，实际只覆盖了 SDR + 仪器两类。

---

## 2. 与 `sdr_backend.py` 的关系：平行宇宙，互不通信

### 2.1 两套并行的后端抽象

| 维度 | `hal.py` | `sdr_backend.py` |
|---|---|---|
| 基类 | `SDRBackendBase(ABC)` (`hal.py:66`) | `SDRBackend` (`sdr_backend.py:72`) |
| 设备描述 | `DeviceInfo` dataclass | `SDRDevice` dataclass + `SDRStatus` |
| 真实 RTL-SDR | 仅通过 SoapySDR 间接 | `RTLSDRBackend` 直接绑 `pyrtlsdr`（`sdr_backend.py:392`），含 ppm/direct_sampling/bias_tee/rtl_tcp |
| 真实 HackRF | 仅通过 SoapySDR 间接 | `HackRFBackend` 直绑 `libhackrf`（`sdr_backend.py:760`） |
| 真实 USRP | 仅通过 SoapySDR 间接 | `USRPBackend` 直绑 `uhd`（`sdr_backend.py:845`） |
| 自研 ai-sdr Mini | **完全没有** | `AISDRMiniBackend` WebSocket/MCP（`sdr_backend.py:579`） |
| IQ 文件回放 | **完全没有** | `FileIQBackend`（`sdr_backend.py:935`） |
| 录制到磁盘 | **完全没有** | `start_recording`/`_recording_loop` cu8/cf32/cs16/wav/csv + sidecar JSON（`sdr_backend.py:171-321`） |
| 管理器 | `HardwareManager`（`hal.py:576`） | `SDRBackendManager`（`sdr_backend.py:1078`） |

**结论：`hal.py` 和 `sdr_backend.py` 是两套互不相干的设备管理体系。** 上层实际用哪一套？

- `mbdsdr_ai/__init__.py:39` 从 `sdr_backend` 导出 `SDRBackendManager / RTLSDRBackend / HackRFBackend / USRPBackend / AISDRMiniBackend / FileIQBackend / MockSDRBackend`。
- 真正的采样、解调、录制、FT8/ADS-B/NOAA 等链路全部通过 `SDRBackendManager.get_active().read_samples()` 走 `sdr_backend.py`。
- `hal.HardwareManager` 只被 `sdr_tools.py` 里 5 个 AI 工具函数引用（`sdr_tools.py:4744/4789/4812/4845/4878/4905`），即 `sdr_list_hardware / sdr_connect_hardware / sdr_transmit_cw / platform_info / instrument_list / instrument_query`。

### 2.2 命名冲突

- `MockSDRBackend` 在两个文件里**同名不同类**：
  - `hal.py:335` 继承 `SDRBackendBase`，`supports_tx()=True`；
  - `sdr_backend.py:324` 继承 `SDRBackend`，`supports_tx=False`，并实现完整录制/状态/RSSI。
- `mbdsdr_ai/__init__.py:39` 导出的是 `sdr_backend.MockSDRBackend`。任何 `from mbdsdr_ai.hal import MockSDRBackend` 拿到的是另一个类，极易误用。
  - **[建议]** 重命名 `hal.MockSDRBackend` 为 `HalMockBackend` 或在 `hal.py` 里直接复用 `sdr_backend` 的实现。

---

## 3. 真 Bug 清单

### 3.1 [真bug] SoapySDR 符号作用域错误，所有调谐命令静默失败

`hal.py:221-242` `connect()` 内 `import SoapySDR` 是**函数局部导入**：

```python
def connect(self, device_str: str = "") -> bool:
    try:
        import SoapySDR          # ← 局部 import
        ...
        self._device = SoapySDR.Device(args_str)
```

但后续方法在模块/实例作用域里直接引用 `SoapySDR.SOAPY_SDR_RX`：

- `hal.py:257` `self._device.setFrequency(SoapySDR.SOAPY_SDR_RX, 0, freq_hz)`
- `hal.py:265` `self._device.setSampleRate(SoapySDR.SOAPY_SDR_RX, 0, rate_hz)`
- `hal.py:274,276` `self._device.setGain(SoapySDR.SOAPY_SDR_RX, 0, ...)`
- `hal.py:317` `self._device.hasTxChannel(SoapySDR.SOAPY_SDR_TX, 0)`

Python 的局部 import 不会泄漏到方法闭包之外。`connect()` 返回后，`SoapySDR` 这个名字在模块全局命名空间里**不存在**。任何一次 `set_frequency()` 调用都会在求值 `SoapySDR.SOAPY_SDR_RX` 时抛 `NameError`，被 `except Exception` 吞掉，仅打一条 warning。

**后果**：即使 SoapySDR 真连上了 HackRF/Pluto，调频率、采样率、增益全部 no-op，设备永远停在默认上电状态。这是 hal.py 硬件闭环断裂的第一个硬伤。

**修复方向**：把 `import SoapySDR` 提到模块顶部（带 try/except 延迟），或在每个方法内重复局部 import，或在 `connect()` 里 `self._soapy = SoapySDR` 保存模块引用。

### 3.2 [真bug] 从未 setupStream，`read_rx()` 永远返回假噪声

`hal.py:71-77` `__init__` 里 `self._rx_stream = None`、`self._tx_stream = None`。全文件 grep 确认：

```
$ grep -n "setupStream|activateStream|deactivateStream|closeStream" hal.py
（无任何匹配）
```

`connect()`（`hal.py:221-242`）只做了 `self._device = SoapySDR.Device(args_str)`，**从未调用 `self._device.setupStream(SOAPY_SDR_RX, ...)` / `activateStream()`**。因此：

- `self._rx_stream` 永远是 `None`；
- `read_rx()`（`hal.py:280-295`）进入 `if self._device and self._rx_stream:` 时条件为假；
- 直接落到 `hal.py:292-295` 的"模拟数据"分支，返回高斯噪声。

**后果**：审查重点 #6 "RTL-SDR 从 hal→sdr_backend→采样→解调完整链路是否真的通"——**走 hal.py 这条路完全不通**。即使用户在 hal 里 connect 了真 RTL-SDR，采到的也是 Python 生成的 0.01 标准差白噪声，没有任何真实 RF。这是最严重的"假闭环"。

### 3.3 [真bug] `read_rx` 里缓冲数组构造错误

`hal.py:284`：

```python
buff = np.array([np.complex64] * num_samples)
```

这构造的是一个 `dtype=object` 的一维数组，每个元素是 `np.complex64` 这个**类型对象本身**，不是 complex64 数据缓冲。即便 §3.2 的 stream 被创建，`self._rx_stream.read(buff, ...)` 也会因 dtype 错误失败。正确写法应为 `np.zeros(num_samples, dtype=np.complex64)`。

### 3.4 [真bug] `hasTxChannel` 不是 SoapySDR Python API

`hal.py:317`：

```python
return self._device.hasTxChannel(SoapySDR.SOAPY_SDR_TX, 0)
```

SoapySDR Python 绑定里查询通道数的标准方法是 `getNumRxChannels()` / `getNumTxChannels()`，没有 `hasTxChannel()`。这行调用一定抛 `AttributeError`，被 `except Exception: return False`（`hal.py:318-319`）吞掉。

**后果**：`supports_tx()` 在任何真实 TX-capable 设备上都返回 `False`；`HardwareManager.connect_sdr()`（`hal.py:655-659`）返回的 `tx` 字段永远是 `False`；`transmit_cw/transmit_modulated`（`hal.py:669-690`）第一道闸门就拒绝执行。**hal.py 的 TX 路径也是断的。**

### 3.5 [真bug] `HardwareManager` 不是单例，跨工具调用状态全部丢失

docstring 自称"硬件管理器（单例）"（`hal.py:572-580`），但 `__init__` 每次都新建空状态：

```python
class HardwareManager:
    def __init__(self):
        self._soapy_backend = None
        self._active_backend = None
```

而 `sdr_tools.py` 里每个 AI 工具都**新建一个实例**：

- `sdr_tools.py:4751` `mgr = HardwareManager()`（list_hardware）
- `sdr_tools.py:4797` `mgr = HardwareManager()`（connect_hardware）
- `sdr_tools.py:4823` `mgr = HardwareManager()`（transmit_cw）

时序：
1. AI 调 `sdr_connect_hardware(device="hackrf")` → 实例 A 里 `self._active_backend = SoapySDRBackend(已连接)`；
2. AI 接着调 `sdr_transmit_cw(freq=...)` → 新建实例 B，`B._active_backend = None`；
3. `hal.py:4826` `if not backend: return "错误: 未连接任何SDR设备…"`。

**后果**：`sdr_transmit_cw` 在任何真实连接之后都必然报"未连接"。CW 发射工具对用户完全不可用。这是典型的"文档说单例，代码没写 `__new__`/模块级缓存"。

### 3.6 [真bug] `InstrumentBackend.read_waveform` 格式自相矛盾

`hal.py:505-518`：

```python
self._resource.write(f":WAV:SOUR CHAN{channel}")
self._resource.write(":WAV:FORM ASC")          # ← 设为 ASCII
raw = self._resource.query_binary_values(":WAV:DATA?", datatype='B')  # ← 却按二进制读
```

ASC 模式下 `:WAV:DATA?` 返回的是 ASCII 文本（如 "+1.234E-01,-4.567E-02,..."），必须用 `query()` 拿到字符串再 split；`query_binary_values(..., datatype='B')` 期望 IEEE 488.2 二进制块 `#<nbytes><len><data>`，对 ASCII 响应会返回空或乱码。

**修复**：二选一——要么改成 `:WAV:FORM BYTE`（或 `WORD`/`REAL`）再 `query_binary_values`，要么 `:WAV:FORM ASC` + `query().split(',')` 转 float。

### 3.7 [真bug] `disconnect()` 不关闭 SoapySDR Stream，资源泄漏

`hal.py:244-251`：

```python
def disconnect(self):
    if self._rx_stream:
        self._rx_stream = None      # ← 丢引用，未 closeStream/deactivateStream
    if self._device:
        self._device = None         # ← 丢引用，未 SoapySDR.Device(...) 显式 close
    self._running = False
```

虽然 §3.2 里 stream 本来就没建，这条 bug 当前不会触发；但一旦 §3.2 修好，这里必须补 `deactivateStream` + `closeStream` + `self._device.close()`，否则重连会爆 USB 资源占用。

### 3.8 [真bug] `list_devices()` 在 SoapySDR 未装时伪造"已发现"设备

`hal.py:198-215`：

```python
except ImportError:
    logger.info("SoapySDR 未安装，列出已知设备类型")
    for driver, desc in self.DRIVER_MAP.items():
        ...
        devices.append(DeviceInfo(..., is_available=False, ...))
```

逻辑本身 OK（`is_available=False`）。但 `HardwareManager.list_all_devices()` 在 `hal.py:614-624` 又把 `MockSDRBackend` 强制 `available=True` 加进去；而 SoapySDR 未安装的真实场景下，AI 看到的设备列表里会同时出现 "HackRF One (未连接)"、"RTL-SDR (未连接)"、"Mock (可用)"——容易误导用户以为系统识别到了 11 种硬件，实际一个都没枚举。

**[建议]** 在未安装 SoapySDR 时不枚举 `DRIVER_MAP`，或加一个明显的 "（驱动未安装，仅显示已知型号）" 前缀。

### 3.9 [真bug] `transmit_cw` 直接访问私有属性

`hal.py:674-676`：

```python
n = int(self._active_backend._sample_rate * duration_sec)
t = np.arange(n) / self._active_backend._sample_rate
iq = amplitude * np.exp(2j * np.pi * 0 * t)   # 2j*pi*0*t 恒为 0
```

- 直接读 `_sample_rate` 私有字段；
- `exp(2j*pi*0*t) ≡ 1`，注释说"零中频 CW"逻辑对，但写成 `np.ones(n, dtype=np.complex64)` 更清晰；
- 更重要的是：此函数依赖 `_active_backend`，而 §3.5 里 `_active_backend` 在跨调用时永远是 None，函数实际不可达。

---

## 4. 空壳 / 占位识别

### 4.1 [空壳] `SoapySDRBackend` 的"统一后端"承诺

`hal.py:5-12` docstring 承诺统一接入 RTL-SDR / HackRF / PlutoSDR / BladeRF / LimeSDR / USRP / Airspy / SDRplay。但代码里：

- 没有任何厂商特化的调谐器设置（PLL 带宽、LNA/PGA/VGA 分档、带宽预选、BIAS tee）；
- 没有 Stream 建立（§3.2）；
- 没有重连/超时；
- 没有 USB 错误码翻译（LIBUSB_ERROR_IO / ACCESS 等）。

实际效果：能 `enumerate()` 列出设备名，但一连接就再也读不到真数据。**这是个"枚举壳"，不是"驱动壳"。**

### 4.2 [占位] `InstrumentBackend` 的未安装分支

`hal.py:440-449`：

```python
except ImportError:
    logger.info("pyvisa 未安装，列出已知仪器类型")
    for vendor, name in self.VENDORS.items():
        instruments.append({
            "address": f"TCPIP::{vendor}.local::INSTR",   # ← 假地址
            ...
            "available": False,
        })
```

`TCPIP::keysight.local::INSTR` 这种 mDNS 名字根本不可解析，纯粹是为了让列表不为空。**[占位]**。

### 4.3 [空壳] GNSS / Gimbal / 天线控制器在 hal.py 中的缺席

见 §1 表。审查范围里点名的三类外设：

- **Gimbal**：`hal.py` 0 行代码，真正实现在 `gimbal.py`（RotctldClient/BoardPwm/Manual，含闭环 RSSI 扫描）；
- **GNSS 接收机**：`hal.py` 0 行代码；`sdr_tools._get_gps`（`sdr_tools.py:3457-3458`）直接 `return "GPS 定位（需自研 ai-sdr Mini 设备连接）…模拟数据: 43.82°N, 125.32°E…"`——**硬编码假坐标字符串**；`pose.py` 里只有 `GPSData` 数据结构和融合算法，没有串口/NMEA 读取线程；
- **天线控制器**：无独立模块。

**[空壳]**：HAL 名字暗示"硬件统一接入"，实际只覆盖了 SDR + SCPI 仪器两类，其余硬件由散点模块自行处理，没有进 HAL。

### 4.4 [占位] `DeviceCapability` 枚举未被使用

`hal.py:36-46` 定义了 `RX/TX/FULL_DUPLEX/HF/VHF/UHF/SHF/GUI/EMBEDDED`，但全文件 grep 不到任何 `DeviceCapability.XXX` 的引用。`DeviceInfo.capabilities` 字段（`hal.py:61`）是 `List[str]`，直接塞字符串列表，根本没用这个枚举。**死代码**。

---

## 5. 设备热插拔 / Crostini USB 透传

### 5.1 热插拔：完全没有

全仓 grep `udev|dbus|hotplug|libusb|/dev/bus` 在 `mbdsdr_ai/` 下**零命中**。

- 没有 `pyudev` 监听、没有 `DBus.SystemBus()`、没有 `udevadm monitor` 子进程；
- `HardwareManager` 只在构造时枚举一次设备（`hal.py:592-642`），之后不再刷新；
- `SoapySDRBackend.connect()` 失败后直接返回 False，没有事件回调通知上层；
- `sdr_backend.py` 同样没有热插拔，RTL-SDR 拔掉后 `read_samples()` 在下一次调用才会异常，没有快速重连。

**[建议]** 至少在 `read_rx` 抛 `LIBUSB_ERROR_NO_DEVICE` 时触发一次重枚举 + 事件。

### 5.2 Crostini / Chromebook USB 透传：完全没有

审查点 #5 专门问"是否有针对 Crostini 环境的特殊处理、USB 权限问题"。

- `detect_embedded_platform()`（`hal.py:525-569`）只检测 Raspberry Pi（`/proc/cpuinfo` BCM）、Jetson（`/proc/device-tree/model`）、Windows ARM、macOS ARM。
- **没有任何 `crostini` / `CHROMEOS` / `/dev/.cros_milestone` / `vm_kernel` 检测**。
- 没有对 `udev` 规则缺失、`dialout` 组权限、`chromebook usb-shared` 提示的处理。
- 当用户在 Chromebook Crostini 里没在"文件→共享 USB 设备"里勾选 RTL-SDR 时，`SoapySDR.Device.enumerate()` 返回空，hal.py 只会打一条 "未找到任何 SDR 设备"（`hal.py:230`），不会提示"请先在 ChromeOS 端共享 USB"。

**[建议]**：在 `detect_embedded_platform()` 加一个 `is_crostini` 判断（读 `/etc/os-release` 里 `CHROMEOS_RELEASE` 或 `/dev/.cros_milestone`），并在 `list_devices()` 返回空时输出 Crostini 专属排查提示。

---

## 6. 错误处理与优雅降级

| 场景 | 行为 | 评价 |
|---|---|---|
| SoapySDR 未安装 | `list_devices` 回退到 `DRIVER_MAP` 已知型号列表（`is_available=False`） | OK |
| `connect()` 失败 | 返回 False，`HardwareManager.connect_sdr` 自动降级到 MockSDRBackend（`hal.py:661-664`） | **合理** |
| `set_frequency/rate/gain` 失败 | try/except 包一层 warning，不抛 | **过度沉默**（配合 §3.1 的 NameError，用户看到一条 warning 就以为调谐成功了） |
| `read_rx` 读失败 | 吞异常，返回噪声（`hal.py:289-295`） | **危险**：真设备已连但 stream 没建时，静默返回噪声，上层解调出来的"信号"全是假数据 |
| `InstrumentBackend` 未装 pyvisa | 返回假的 `TCPIP::keysight.local::INSTR` 列表 | 误导 |
| `transmit_cw` 不支持 TX | 返回 False，工具函数打印"不支持TX" | OK，但因 §3.4 `supports_tx` 永远 False，永远走这一支 |

**[建议]**：
1. `read_rx` 在 `self._device is not None and self._rx_stream is None` 时应 `logger.error` 并抛异常，而不是静默回噪声；
2. `set_frequency` 失败应返回 bool 并在工具层报红，而不是 warning。

---

## 7. RTL-SDR 真实硬件闭环走查（审查重点 #6）

按调用链追踪"RTL-SDR 插上 USB → 采到 IQ → 进解调"：

| 跳 | 文件:行 | 是否真通 |
|---|---|---|
| 1. 枚举 USB RTL-SDR | `sdr_backend.RTLSDRBackend.list_devices` (`sdr_backend.py:430-452`)，用 `RtlSdr.get_device_count()` | **真通** |
| 2. 打开设备 | `sdr_backend.RTLSDRBackend.connect` (`sdr_backend.py:454-475`)，`RtlSdr(index)` | **真通**（依赖 pyrtlsdr+librtlsdr） |
| 3. 设频率/增益/ppm/direct_sampling | `sdr_backend.py:486-559` 直接写 `self._sdr.center_freq / .gain / .freq_correction / .set_direct_sampling` | **真通** |
| 4. 读 IQ | `sdr_backend.py:571-576` `self._sdr.read_samples(num_samples)` | **真通** |
| 5. 上层解调 | `demod.py / wfm_stereo_lite.py / ft8_decode.py / adsb.py` 等消费 `SDRBackendManager.get_active().read_samples()` | **真通** |
| —— 但走 `hal.py` 这条路呢？ | | |
| 1'. 枚举 | `hal.SoapySDRBackend.list_devices` (`hal.py:161-219`)，`SoapySDR.Device.enumerate()` | 取决于是否装了 `SoapyRTLSDR`，多数用户没装 |
| 2'. 连接 | `hal.py:221-242` | 连得上 |
| 3'. 设频率 | `hal.py:253-259` → **NameError（§3.1）** | **断** |
| 4'. 建 Stream | **代码里根本没有**（§3.2） | **断** |
| 5'. 读 IQ | `hal.py:280-295` → 永远走 mock 噪声 | **断** |

**结论**：真实 RTL-SDR 闭环走的是 `sdr_backend.py`，**不经过 hal.py**。hal.py 里的 SoapySDR 路径是一条"枚举得到、连上成功、调谐静默失败、采样永远假"的死循环。

---

## 8. 其他小问题

- **[建议] `hal.py:193`** `sample_rates=[2e6, 4e6, 8e6, 10e6, 20e6]` 对所有 Soapy 设备一刀切。RTL-SDR 实际最大 ~3.2 MHz，HackRF 才到 20 MHz。应该按 driver 查表。
- **[建议] `hal.py:232`** `args_str = str(results[0])` 直接 str() Kwargs，不同 SoapySDR 版本的 `__str__` 输出格式可能不被 `Device(...)` 反向接受。建议直接 `SoapySDR.Device(results[0])` 传 dict。
- **[建议] `hal.py:689`** `np.max(np.abs(iq_samples))` 在空数组上会抛 `RuntimeWarning` 并产生 nan。
- **[建议] `hal.py:46`** `DeviceCapability.GUI` / `EMBEDDED` 与设备本身能力无关，属于平台属性，放在设备能力枚举里语义不对。
- **[建议] `hal.py:63`** `DeviceInfo.description` 在 SoapySDR 真实枚举时被 `manufacturer` 字段填了一段带括号的字符串（`hal.py:190`），与 `description` 字段重复。
- **[占位]** `hal.py:145` `redpitaya` 在 `DRIVER_MAP` 里，但 `RANGE_MAP`（`hal.py:150-159`）里没有 `redpitaya`，枚举时 `rx_range=(0,0)`。
- **[占位]** `hal.py:146` `netsdr` / `audio` 同样不在 `RANGE_MAP`。

---

## 9. 风险评级与优先级

| # | 问题 | 等级 | 建议处理时机 |
|---|---|---|---|
| 3.2 | 从未 setupStream，read_rx 永远假数据 | **P0** | 立刻修，否则"真硬件闭环"是谎言 |
| 3.1 | SoapySDR 局部 import 导致调谐全部 NameError | **P0** | 立刻修 |
| 3.5 | HardwareManager 非单例，TX 工具跨调用必失败 | **P0** | 立刻修（模块级缓存或 `__new__`） |
| 3.4 | `hasTxChannel` 不是 SoapySDR API，TX 能力永远 False | **P1** | 修 3.1 时一起改成 `getNumTxChannels()>0` |
| 3.3 | `np.array([np.complex64]*n)` 缓冲错误 | **P1** | 修 3.2 时一起改 |
| 3.6 | read_waveform ASC/二进制矛盾 | **P2** | 仪器功能本来就少人用 |
| 5.1/5.2 | 无热插拔、无 Crostini 检测 | **P2** | Chromebook 用户体验问题 |
| 4.3 | GNSS/Gimbal 未进 HAL，`_get_gps` 硬编码坐标 | **P1** | 架构债，需统一 |
| 2.2 | 双 MockSDRBackend 命名冲突 | **P3** | 重命名 |
| 4.4 | DeviceCapability 死枚举 | **P3** | 删除或用上 |

---

## 10. 给主审的结论

`hal.py` 是一个"对外宣称统一硬件抽象、对内两条平行宇宙"的模块。真正在生产链路上跑的是 `sdr_backend.py` 的直绑驱动（pyrtlsdr / libhackrf / uhd / WebSocket-MCP）；`hal.py` 只服务 5 个 AI 工具，而且这 5 个工具里：

- `sdr_list_hardware` 能列名字，但列不出真实连接状态（因为没装 SoapySDR 时列的是假数据，装了时 stream 又没建）；
- `sdr_connect_hardware` 连接结果在工具调用结束后丢失；
- `sdr_transmit_cw` 因非单例 + `hasTxChannel` bug **永远失败**；
- `instrument_query` / `instrument_list` 是唯一能跑通的部分（前提是装了 pyvisa-py 且有真仪器）。

**建议**：要么把 `hal.py` 真正实现（补 setupStream / 模块级 import / 单例），要么直接把它降级为"设备枚举展示层"，明确告诉上层"实际采样请走 sdr_backend.SDRBackendManager"，避免 AI 工具误把 hal 当真实后端用。
