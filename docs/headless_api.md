# MBDSDR Core — Headless API 文档

`mbdsdr_ai/core/` 是 Headless-first 的统一纯 Python 控制面。GUI（`desktop/`）、
CLI、Web、MCP 都调用同一个 `SDRController`。本包**绝不 import PySide6/Qt**。

## 设计原则

- 所有业务逻辑在 `SDRController`；前端只是消费者。
- 无硬件 / 无后端时：数据方法返回 `None`，状态方法显式 `connected=False`，
  绝不伪造数据。
- 可选内核模块（skyfield / psutil / sounddevice / SoapySDR…）一律 `try/except`
  守卫，缺失时返回 `{"error": "not_available"}` 而非崩溃。
- 调试信号源 `connect("debug")` 会被显式标记为"调试信号源（合成信号，非真实设备）"。

## 快速开始

```python
from mbdsdr_ai.core import SDRController, SDRUserError

ctrl = SDRController()                 # 无后端，connected=False
ctrl.list_devices()                   # []（外加 debug 项）
ctrl.connect("debug")                 # 显式调试信号源（也可传真实设备序列号）
ctrl.set_frequency(98_500_000)
ctrl.set_demod("WFM")
spec = ctrl.read_spectrum(1024)      # np.ndarray（未连接时 None）
ctrl.shutdown()
```

## 错误类型（`core/errors.py`）

所有可预期错误继承 `SDRUserError`，字段：`message`（用户友好）、
`suggestion`（可操作建议）、`technical_detail`（开发者细节）、`code`（机器码）。

| 子类 | code | 触发场景 |
|---|---|---|
| `DriverNotFoundError` | `driver_not_found` | pyrtlsdr / SoapySDR 未安装 |
| `DeviceNotFoundError` | `device_not_found` | 找不到指定设备 / 设备已拔出 |
| `DeviceBusyError` | `device_busy` | 设备被其它进程占用 |
| `USBDisconnectedError` | `usb_disconnected` | 读样本中设备拔出 |
| `NoAudioOutputError` | `no_audio_output` | 无可用音频输出 |
| `SampleRateNotSupportedError` | `sample_rate_not_supported` | 采样率不被设备支持 |
| `FrequencyOutOfRangeError` | `frequency_out_of_range` | 频率超出设备范围 |
| `DecoderNotAvailableError` | `decoder_not_available` | 解码器未注册/缺依赖 |

## `SDRController` 方法一览

### 设备
| 方法 | 签名 | 返回 / 说明 |
|---|---|---|
| `list_devices()` | `-> List[DeviceInfo]` | 枚举设备；无硬件 `[]`，恒含 debug 项 |
| `connect(device_id)` | `-> bool` | `device_id="debug"` 用调试源；否则按序列号匹配真实设备。抛 `DeviceNotFoundError`/`DriverNotFoundError` |
| `disconnect()` | `-> None` | 断开并停掉所有派生资源 |
| `is_connected()` | `-> bool` | 连接状态 |

### 接收参数
| 方法 | 签名 |
|---|---|
| `set_frequency(hz)` | `-> None` |
| `get_frequency()` | `-> float` |
| `set_demod(mode)` | `-> None`（AM/FM/WFM/NFM/USB/LSB/CW，非法抛 `SDRUserError`）|
| `get_demod()` | `-> str` |
| `set_bandwidth(hz)` | `-> None` |
| `set_gain(db)` | `-> None` |
| `set_gain_stage(stage, db)` | `-> None` |
| `set_sample_rate(sr)` | `-> None`（不支持抛 `SampleRateNotSupportedError`）|
| `set_squelch(db)` | `-> None` |

### 音频
| 方法 | 签名 |
|---|---|
| `start_audio()` | `-> bool`（无音频硬件返回 False，不崩）|
| `stop_audio()` | `-> None` |
| `set_audio_output(index)` | `-> bool` |
| `list_audio_outputs()` | `-> List[AudioDeviceInfo]` |
| `set_volume(db)` | `-> None` |
| `set_mute(bool)` | `-> None` |

### DSP
| 方法 | 签名 |
|---|---|
| `read_spectrum(nfft=1024)` | `-> Optional[np.ndarray]`（dB 功率谱；未连接 None）|
| `read_iq(n=8192)` | `-> Optional[np.ndarray]`（complex64）|
| `read_audio(n=1024)` | `-> Optional[np.ndarray]`（float32 单声道）|
| `start_analyze(modulation=None)` | `-> dict`（调制识别结果；无后端 `{"error":"not_connected"}`）|

### 扫描
| 方法 | 签名 |
|---|---|
| `start_scan(start, stop, step)` | `-> str`（返回 `ScanHandle`）|
| `stop_scan(handle)` | `-> None` |
| `get_scan_results(handle)` | `-> List[dict]`（活动段：start/end/peak freq、peak_db、kind）|

### 解码
| 方法 | 签名 |
|---|---|
| `start_decoder(decoder_type, params=None)` | `-> str`（adsb/aprs/noaa_apt/meteor；未知抛 `DecoderNotAvailableError`）|
| `stop_decoder(handle)` | `-> None` |
| `get_decoder_messages(handle)` | `-> List[dict]`（取走并清空队列；无报文 `[]`）|

### 卫星
| 方法 | 签名 |
|---|---|
| `list_satellites()` | `-> List[SatInfo]`（name/norad/downlink_mhz）|
| `get_satellite_pass(norad)` | `-> dict`（PassInfo；无过境 `{"error":"no_pass"}`）|
| `tune_satellite(norad)` | `-> dict`（标称/调谐频率 + 多普勒，并下发 set_frequency）|
| `set_ground_station(lat, lon, alt=0)` | `-> None` |

### 书签
| 方法 | 签名 |
|---|---|
| `add_bookmark(freq, name, mode="FM", group="")` | `-> dict` |
| `list_bookmarks(group=None)` | `-> List[dict]` |
| `delete_bookmark(id)` | `-> bool`（id = 频率 Hz）|
| `find_nearest_bookmark(freq)` | `-> Optional[dict]` |
| `import_bookmarks(path)` | `-> int`（导入条数，GQRX CSV）|
| `export_bookmarks(path)` | `-> str`（写出路径）|

### 录制
| 方法 | 签名 |
|---|---|
| `start_recording(path)` | `-> dict`（SigMF cf32；未连接 `{"error":"not_connected"}`）|
| `stop_recording()` | `-> dict`（samples/meta_path）|
| `get_recording_status()` | `-> dict` |

### 服务
| 方法 | 签名 |
|---|---|
| `start_remote_control(port=7356)` | `-> dict`（GQRX rigctl 风格 TCP）|
| `stop_remote_control()` | `-> None` |
| `start_web_server(port=8000)` | `-> dict` |
| `stop_web_server()` | `-> None` |
| `get_service_status()` | `-> dict` |

### ANR / 降噪
| 方法 | 签名 |
|---|---|
| `set_anr_enabled(bool)` | `-> None` |
| `set_anr_strength(level)` | `-> None`（0–10）|
| `learn_noise_floor()` | `-> dict` |

### 多 VFO
| 方法 | 签名 |
|---|---|
| `add_vfo(freq, mode="FM", bw=12500)` | `-> str`（vfo_id）|
| `remove_vfo(vfo_id)` | `-> None` |
| `set_primary_vfo(vfo_id)` | `-> bool` |
| `list_vfos()` | `-> List[VfoInfo]` |

### 系统
| 方法 | 签名 |
|---|---|
| `get_status()` | `-> SystemStatus`（连接/设备/音频/CPU/内存/服务）|
| `shutdown()` | `-> None`（优雅关闭全部资源）|

## 数据结构

- `AudioDeviceInfo(index, name, channels, default_samplerate)`
- `VfoInfo(vfo_id, center_hz, mode, bw_hz, primary, muted)`
- `SatInfo(name, norad, downlink_mhz)`
- `PassInfo(name, rise_utc, set_utc, max_alt_time_utc, max_alt_deg, duration_s, rise_az_deg, set_az_deg)`
- `SystemStatus(connected, device, is_debug_source, frequency_hz, sample_rate_hz, demod, gain_db, audio_running, recording, remote_control, web_server, cpu_percent, mem_percent)`

均带 `to_dict()`，可直接 JSON 序列化。
