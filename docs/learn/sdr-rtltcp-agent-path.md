# rtl_tcp 网络源的 agent 通路（MBDSDR_RTL_TCP）

> 2026-10-13 真机在环（RTL2832U + FC0012，官方 Osmocom rtl-sdr 20261011 Windows
> 构建，本机 Zadig/WinUSB 已绑）。目标：让外部 agent（MCP 客户端）能像枚举 USB
> 棒一样枚举并驱动 rtl_tcp 网络源——补上"RTLSDRBackend(host=...) 存在于 backend
> 层，但枚举/控制器层够不着"的最后一公里。

## 问题

rtl_tcp 网络接入此前只存在于 `RTLSDRBackend(host=...)`（backend 层）。但
controller/设备枚举走的是另外两条路，都与它无关：

- `SDRBackendManager._discover()` —— 只探 pyrtlsdr/HackRF/UHD/PlutoSDR；
- `enumerate_all_sdr_devices()` + `build_backend_for_device()` ——
  SoapySDR/pyrtlsdr/osmosdr 全家，没有网络源条目；
- `controller.connect()` -> `DeviceManager.list_devices()/open()` ——
  pyrtlsdr/SoapySDR 两个 probe，OpenDevice 包 pyrtlsdr 风格句柄。

结果：一个装不上 librtlsdr 的机器（Windows 沙箱、容器、WOA、远端共享棒），
agent 侧 `list_devices` 永远只有 debug 信号源。

## 方案：env 驱动的显式登记（零硬编码）

新环境变量 `MBDSDR_RTL_TCP="host[:port][,host[:port]...]"`（默认端口 1234），
在三个层同时生效，语义一致（坏条目 debug 跳过、enumerate 不联网、connect 才握手）：

| 层 | 改动 | 文件 |
|---|---|---|
| 后端注册表 | `_discover()` 追加 env 登记 | sdr_backend.py |
| 设备枚举/构造 | enumerate 步骤6 + `build_backend_for_device` 分支 | sdr_backend.py |
| 控制器链 | `DeviceInfo.endpoint` + `_probe_rtltcp/_open_rtltcp` | device_manager.py |
| pyrtlsdr 风格适配 | `RtlTcpDeviceAdapter.read_bytes()` 直通原始 CU8 | rtltcp_client.py |

`device_id` 统一形态：`rtl_tcp_{host}_{port}`（RTLSDRBackend 原有命名）。

## 真机验证（agent -> MCP -> rtl_tcp -> 棒子）

```
list_devices  -> [{"name": "rtl_tcp 127.0.0.1:1234", "driver": "rtltcp", ...}]
connect       -> {"connected": true}
set_frequency -> {"ok": true}   (hz=435274000)
set_demod     -> {"ok": true}   (mode=FM)
set_gain      -> {"ok": true}   (db=20)
get_status    -> {"connected": true, "device": "rtl_tcp 127.0.0.1:1234",
                  "frequency_hz": 435274000.0, "demod": "FM", "gain_db": 20.0}
```

射频侧交叉印证：同机 NanoVNA-H 测得 RH770 天线 430 段谐振 ≈419.5MHz（手持），
SDR 端 435.25MHz 中心 Welch PSD 见到 9 个真实窄带载波、435.274 解调语音带
RMS 0.49（空频点噪声底 0.09）。

## 测试

`mbdsdr_ai/tests/test_sdr_rtltcp_registration.py`（12 例）：
env 解析（默认端口/多源/坏条目跳过）、`_discover` 登记/无 env 诚实空态、
`enumerate_all_sdr_devices`+`build_backend_for_device` 往返、
DeviceManager probe/open、以及**活体**用例（rtl_tcp 在跑时真连真读 8192 IQ）。
无 env 时活体用例 honest skip，不当假连接。

## 已知边界（诚实登记）

- rtl_tcp 是单客户端协议（listen backlog=1）：同一源同一时刻只允许一个
  OpenDevice；后端断开后需 2-3s 冷却才能重连。
- enumerate 不联网：登记的服务没起来时 `connect` 由后端诚实报"连接失败"。
- ppm 频漂校正（FC0012 晶振 20-50ppm）的 **agent 侧校准环已落地**
  （见下节"频率校准（ppm）"）；真机侧仍需一次"已知信标 ↔ 实测谱峰"的
  现场标定来验证，未在真机上跑出真实 ppm 数字之前，不声称已校准。

## 频率校准（ppm）

RTL2832U + FC0012 晶振典型有 20~50ppm 误差（435MHz 上约 ±22kHz），
绝对频率读数不可信。校准思路与 kalibrate 同源（docs/learn/kalibrate.md：
用 GSM FCCH 当已知参考反推晶振误差），只是参考源换成手上现成的已知载波
（FM 整点台 / 授时台 / 卫星信标），且全部封装为 agent 可调的工具。

### 校准环（agent 三步）

1. **调到已知频率参考**：把一个已知标称频率的载波调到中心
   （如 FM 广播整点台、卫星信标），调谐后等频谱稳定。
2. **读取实际谱峰**：用现有工具读出真实峰频率——
   `sdr_spectrum_center_offset`（亚 bin 抛物线插值）/ `sdr_measure_signal`
   / `sdr_cfo_correct`（相位相干时 Kay 精估到亚 Hz）。
3. **estimate → apply**：
   - `estimate_ppm_offset(expected_hz, measured_hz)` → 原始 ppm 误差
     （纯函数，不触硬件）；
   - `set_ppm_correction(ppm, arm_manual=true)` → 经活跃后端的频率校正
     入口下发：RTLSDRBackend 本地 USB 走 librtlsdr ctypes；rtl_tcp 网络源
     经 `RtlTcpDeviceAdapter.freq_correction` setter 下发协议 0x05 命令
     （`mbdsdr_ai/rtltcp_client.py`）；未连接时后端先保存、reconnect 再发。

符号口径照抄 kalibrate（`offset.cc:125`：新校正 = 旧校正 − 误差ppm）：
`estimate_ppm_offset` 同时给出原始误差与"建议下发值"（=原始误差取负），
agent 无需自己推断符号。钳位口径：超 ±200ppm 的值仍按 limit 应用
（廉价棒典型 20~50ppm、kalibrate 门限 ~40kHz@270833 ≈ 148ppm，见
`mbdsdr_ai/ppm_calibration.py`），但结果里标记"可疑"。

### 诚实边界

- **无参考不可校准**：`expected_hz<=0`（或 NaN/inf）时
  `estimate_ppm` 直接抛 `ValueError`，工具返回失败信封——
  绝不返回 ppm=0 冒充"已校准"。没有已知信标的场景，任何 ppm 数字都是编的。
- **钳位≠造假，但必须告知**：超范围值照常下发，同时回传 `clamped=True`
  和"可疑"提示；看到它应当先核对参考频率与谱峰，而不是当成校准结果。
- **无运行时入口不假装成功**：后端没有频率校正入口（如 IQ 回放源）时
  `set_ppm_correction` 诚实报"当前后端不支持在线 ppm 校正（pyrtlsdr
  原生路径需在连接时传 ppm）"，返回失败信封。
- **写操作防误触**：`set_ppm_correction` 是写工具，带 `arm_manual`
  手动门（与 `mbdsdr_ai/core/mcp_server.py` 写工具 gate 同语义）：
  未显式 `arm_manual=true` 不执行。
- **单测不伪造硬件**：`mbdsdr_ai/tests/test_ppm_calibration.py` 里的
  分发路径用替身后端（只记录调用）钉扎，无设备用例钉诚实错误信封。
