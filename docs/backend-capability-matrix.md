# SDR 后端能力对照表（生成物，勿手改）

> 生成：`python scripts/gen_backend_capability_matrix.py --write`  
> 对账：`python scripts/gen_backend_capability_matrix.py --check`（发版前由 `scripts/release_check.py` 的 gen_reconcile 段自动跑）

六项真值**直接调用各后端实例的 `supports_gain` / `supports_agc` / `supports_ppm` / `supports_offset_tuning`**，本页不另写一份判据；区间取实例的 `device.sample_rate_range` / `device.max_gain` 与`capability_status`（声明=declared / 探测=probed / 未声明=unknown）。

探测上下文：**未连接的离线实例**（构造不打开硬件、不联网），共 10 个后端类；其中 AGC 4 个、增益 8 个、PPM 2 个、Offset 0 个真支持。

## 1. 能力矩阵

| 后端类 | 说明 | AGC | 增益 | PPM | Offset | 采样率区间 | 增益区间 |
|---|---|---|---|---|---|---|---|
| `AISDRMiniBackend` | 自研 ai-sdr Mini（SI4732 + ESP32，经 MCP/WebSocket） | 否 | 是 | 否 | 否 | 48 kHz – 48 kHz（声明） | 0 – 12 dB（声明） |
| `FileIQBackend` | IQ 文件离线回放（用户显式选择的录制文件） | 否 | 否 | 否 | 否 | 1 Hz – 61.44 MHz（未声明） | 0 – 0 dB（无增益控制） |
| `HackRFBackend` | HackRF One（libhackrf / 原生） | 否 | 是 | 否 | 否 | 2 MHz – 20 MHz（声明） | 0 – 40 dB（声明） |
| `NetworkIQBackend` | 裸 IQ 网络流（sdrpp network_sink 兼容） | 否 | 否 | 否 | 否 | 1 Hz – 1 GHz（声明） | 0 – 0 dB（无增益控制） |
| `PlutoSDRBackend` | PlutoSDR / ADALM-PLUTO（libiio 或 SoapyPlutoSDR） | 是 | 是 | 否 | 否 | 521 kHz – 61.44 MHz（声明） | 0 – 73 dB（声明） |
| `RTLSDRBackend` | RTL-SDR 原生 USB（rtl_tcp 亦复用本类的 host/port 模式） | 是 | 是 | 是 | 否 | 225.001 kHz – 3.2 MHz（声明） | 0 – 49.6 dB（声明） |
| `RtlTcpBackend` | rtl_tcp 网络源（自研 RtlTcpClient，协议 0x05 系） | 是 | 是 | 是 | 否 | 225.001 kHz – 3.2 MHz（声明） | 0 – 49.6 dB（声明） |
| `SoapySDRBackend` | SoapySDR 通用后端（未专用化的驱动走这里） | 是 | 是 | 否 | 否 | 1 kHz – 61.44 MHz（声明） | 未连接：连接后由驱动探测（当前声明 0 dB） |
| `SpyServerBackend` | SpyServer 网络源（Airspy 系服务端） | 否 | 是 | 否 | 否 | 225.001 kHz – 3.2 MHz（声明） | 0 – 49.6 dB（声明） |
| `USRPBackend` | USRP（UHD） | 否 | 是 | 否 | 否 | 100 kHz – 56 MHz（声明） | 0 – 76 dB（声明） |

## 2. 判据只有一个地方

**本页不复制判据**。AGC / 增益 / PPM / Offset 的判定分别是 `mbdsdr_ai/sdr_backend.py` 里的 `SDRBackend.supports_agc()` / `supports_gain()` / `supports_ppm()` / `supports_offset_tuning()`，表里每一格都是对**该类实例**调用这些方法得到的返回值。想确认某格为什么是「是/否」，请直接看对应方法，不要以本页文字为准。

`RTLSDRBackend` 的 Offset 依赖已连接的驱动句柄（`hasattr(sdr, 'set_offset_tuning')`），未连接实例恒为「否」；连接后由盒子/驱动实际能力决定。

## 3. 未纳入本表的「后端」

这些类名字像后端，但**不是 `SDRBackend` 子类**，没有 `supports_*`；如果哪天它们接进统一后端接口，应当补上 `supports_*` 再自动进入本表。

| 类 | 原因 |
|---|---|
| `mbdsdr_ai.bladerf_params.BladeRFBackend` | 不是 SDRBackend 子类，没有 supports_*；增益按 bladerf_set_gain 固定下发，AGC / PPM / Offset 均未实现 |
| `mbdsdr_ai.limesuite_params.LimeSDRBackend` | 不是 SDRBackend 子类，没有 supports_*；增益按 LMS_SetGaindB 固定下发，AGC / PPM / Offset 均未实现 |
| `StubBackend` | 生产代码里没有这个类；只有 `tests/`、`scripts/repro/` 里的测试桩（各自自定义，不参与设备能力判定） |

## 4. 怎么用这张表

界面上某项控件是否可用，以所连后端实例的 `supports_*` 返回值为准（`desktop/control_panel.py` 的 `set_device_capabilities` 按它收紧控件）；本表是**逐类**的静态对照，用于回答「这台设备/这个后端理论上能不能做」。实例级的实际结果（例如 RTLSDR 的 Offset、Soapy 的增益区间）在连接后才确定，表里已如实标注依赖条件。
