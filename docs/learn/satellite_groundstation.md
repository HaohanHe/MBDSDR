# 卫星地面站自动化：r2cloud / satnogs / SDRangel 插件 — 学习笔记

> 仓库：`repos/r2cloud`（Apache-2.0, dennasherbrezon）、`repos/gr-satnogs`/`repos/satnogs-client`（AGPLv3, satnogs.org，已迁 GitLab）、`repos/sdrangel`（GPLv3, F4EXB）
> 笔记日期：2026-09-25
> 目的：把三个卫星地面站项目的"调度/自动化/特色插件"能力移植到 MBDSDR，输出三个适配器：
> `mbdsdr_ai/r2cloud_adapter.py`、`mbdsdr_ai/satnogs_adapter.py`、`mbdsdr_ai/sdrangel_plugins.py`。
> 轨道传播/坐标转换直接复用 `mbdsdr_ai/gpredict_adapter.py`（SGP4/TLE），不重复造轮子。

---

## 1. r2cloud：卫星接收任务调度/录制

### 1.1 整体调度链路

```
TLE 列表 + AntennaConfiguration(经纬度/海拔/最小仰角)
        │
        ▼
ObservationFactory.createSchedule(antenna, date, transmitter)
        │  对每颗卫星:
        │   1. TLEPropagator.selectExtrapolator(...)  ← 轨道传播（本项目复用 gpredict SGP4）
        │   2. PredictOreKit.calculateSchedule(...)     ← 扫 AOS/LOS
        ▼
List<ObservationRequest>  (startMillis/endMillis/frequency/...)
        │
        ▼
Schedule.assignTasksToSlot(obsId, ScheduledObservation)
        │  SequentialTimetable 拒绝重叠时段
        ▼
ScheduledObservation (Future<?> 录制任务 + RotatorService 旋转任务)
        │
        ▼
ReceiverTask → Device(RtlSdrDevice/PlutoSdrDevice/AirspyDevice)
        │  录 IQ → SigMF
        ▼
Observation.status: RECEIVING_DATA → RECEIVED → DECODED → UPLOADED
```

### 1.2 关键源码点

| 概念 | 源码位置 | 要点 |
|---|---|---|
| 观测请求字段 | `model/ObservationRequest.java:7-17` | `id, startTimeMillis, endTimeMillis, satelliteId, transmitterId, tle, groundStation(GeodeticPoint), frequency, centerBandFrequency` |
| 仰角穿越事件 | `predict/MinElevationHandler.java:12-28` | `increasing=true` 记 AOS start；`increasing=false` 记 LOS end |
| 过境时长裁剪 | `satellite/ObservationFactory.java:24-26` | `MAX_OBSERVATION_MILLIS=15min`、`MIN_OBSERVATION_MILLIS=4min`；过长切段、过短丢弃 |
| 任务 ID | `ObservationFactory.java:75` | `id = startMillis + "-" + transmitterId` |
| 时间槽 | `satellite/TimeSlot.java:5-20` | `{frequency, start, end}` |
| 顺序时间表 | `satellite/SequentialTimetable.java:11-56` | 按时间排序；`addFully` 拒绝重叠，`addPartially` 容差裁剪 |
| 录制元数据 | `model/Observation.java:18-46` | SigMF：`sampleRate/frequency/rawPath/sigmfMetaURL/dataFormat/status` |
| 状态机 | `model/ObservationStatus.java` | `RECEIVING_DATA, RECEIVED, DECODED, UPLOADED, FAILED` |

### 1.3 MBDSDR 移植要点

- **`R2CloudStation`**：`name/lat_deg/lon_deg/alt_m/min_elevation_deg`，对应 r2cloud `AntennaConfiguration` + Orekit `GeodeticPoint`。
- **`R2CloudScheduler.schedule_satellite()`**：
  1. 调 `gpredict_adapter.SatPassPredictor.predict_passes()` 扫 AOS/LOS（等价 `MinElevationHandler` 的仰角穿越）。
  2. 按 15 min 切段、4 min 丢弃（对齐 `ObservationFactory.java:52-63`）。
  3. 每个任务注册到 `SequentialTimetable` 去重。
- **`build_recording_metadata()`**：输出 SigMF JSON（`global.core:sample_rate/frequency`、`captures`、`annotations` 里塞 r2cloud 扩展字段）。

---

## 2. satnogs-client + gr-satnogs：地面站观测流水线

### 2.1 观测流程状态机

```
satnogs-network (云端)
   │  GET /api/observations/next/?ground_station=<id>
   ▼
satnogs-client observer.py
   IDLE
    │ start
    ▼
RECEIVING_DATA ── gr-satnogs flow graph 启动
    │
    ▼
DOPPLER_CORRECTING ── doppler_correction 块每 dt 秒调 NCO
    │
    ▼
RECORDING ── iq_file_sink 写原始 IQ
    │
    ▼
DECODING ── fsk_demod / morse_debug / lrpt_decode
    │
    ▼
UPLOADING ── POST /api/observations/finished/<id>/
    │
    ▼
DONE
```

### 2.2 网络调度协议（离线模拟）

真实 satnogs-client 与云端交互（来源 `satnogsclient/network/tasks.py`）：

- `GET {base}/api/observations/next/?ground_station=<id>` → 200 JSON：
  ```json
  {"id": 12345, "start": "...", "end": "...",
   "observation_frequency": 437500000,
   "transmitter_uuid": "...", "mode_id": 1, "baud": 9600}
  ```
- `POST {base}/api/observations/finished/<id>/` 上报 `{approved, status, observation_frequency}`。

MBDSDR 离线版 `SatnogsNetworkClient`：本地 `_queue` 模拟下发明细，`request_next()` / `report_finished()` 保留同样的 JSON 字段名。

### 2.3 多普勒校正（gr-satnogs）

来源 `gr-satnogs/lib/doppler_correction/doppler_correction_impl.cc`：
- 每 `dt` 秒用 TLE 算卫星视线速度 `v_r`，NCO 把中心频率移到 `f_rx = f_tx*(1 - v_r/c)`。
- `v_r > 0`（远离）→ 接收频率降低；`v_r < 0`（接近）→ 升高。

MBDSDR 移植：`DopplerCorrector.build_curve()` 在过境期间以 1 s 步长采样整条频率曲线；`nco_freq_at(t)` 线性插值供 NCO 调用。测试里与解析公式 `f*(1-v_r/c)` 对比误差 **< 1 Hz**。

### 2.4 解调模式枚举

| 模式 | 典型带宽 | 典型波特率 | 用途 |
|---|---|---|---|
| CW | 500 Hz | 20 Bd | 莫尔斯遥测 |
| AFSK | 3 kHz | 1200 Bd | AX.25 气象卫星 |
| FSK | 9.6 kHz | 9600 Bd | 通用数传 |
| GMSK | 9.6 kHz | 9600/48000 Bd | 现代 LEO 数传 |
| LRPT | 80 kHz | 62500 sym/s | NOAA/METEOR 图像 |
| APT | 4.16 kHz | 1200 Bd | NOAA 模拟传真 |

---

## 3. SDRangel 特色插件扩展

> 注意：当前 checkout 的 sdrangel 主分支已把 `plugins/channelrx/ogn` 与 `plugins/feature/firedetector` 迁出；
> `plugins/feature/remotecontrol` 仍在。OGN/FireDetector 按任务书给定的真实位域参数实现，
> RemoteControl 直接读 `remotecontrolsettings.h`。

### 3.1 OGN (Open Glider Network) / FLARM 接收器

- 真实链路：**868.200 MHz** 2-FSK 9600 baud（CC1101），接收滑翔机/轻型飞机的 FLARM 位置报告。
- 任务书给定 V6 帧位域：
  - `protocol_version` 4 bit = 6
  - `aircraft_id` **24 bit**（ICAO-风格，无符号）
  - `latitude` **17 bit** 有符号定点：`deg = v/2^17*180 - 90`
  - `longitude` **17 bit** 有符号定点：`deg = v/2^17*360 - 180`
  - `altitude_m` 12 bit、`speed_kts` 10 bit、`heading_deg` 9 bit
- MBDSDR：`encode_flarm_frame()` / `decode_flarm_frame()` 大端位打包/解包，往返误差 < 0.01°。

### 3.2 FireDetector 森林火灾检测

- 思路：对热成像网格做 **温度阈值分割**（默认 320 K ≈ 47 °C）→ **距离聚类**（默认 5 km 半径，简化 DBSCAN 单链）→ 输出簇质心 + 最高温 + 簇大小。
- MBDSDR：`FireDetector.detect(points)` 输入 `[(lat, lon, temp_K), ...]`，返回 `FireCluster` 列表。

### 3.3 RemoteControl 远程控制协议

来源 `plugins/feature/remotecontrol/remotecontrolsettings.h:28-90`：
- `RemoteControlControl { m_id, m_labelLeft, m_labelRight }`
- `RemoteControlSensor  { m_id, m_labelLeft, m_labelRight, m_format, m_plot }`
- `RemoteControlDevice  { m_protocol, m_label, controls[], sensors[] }`
- 与后端（TP-Link Kasa / Home Assistant / VISA）通信用 `QJsonObject`。

MBDSDR 实现一个 JSON 命令/状态信封：
```jsonc
// 命令
{"id":1, "device":"R0", "command":"set_center_frequency",
 "args": {"center_frequency": 145000000}}
// 响应
{"id":1, "device":"R0", "ok": true,
 "status": {"center_frequency":145000000, "sample_rate":1024000,
             "rx_frequency":145000000.0, "running":false}}
```
支持命令：`set_center_frequency / set_sample_rate / set_doppler / start_rx / stop_rx / query_status`。

---

## 4. 交付物清单

| 文件 | 作用 |
|---|---|
| `mbdsdr_ai/r2cloud_adapter.py` | 调度器 + 观测站配置 + SigMF 录制元数据；`register_r2cloud_tools` |
| `mbdsdr_ai/satnogs_adapter.py` | 观测流水线 + DopplerCorrector + DemodMode 枚举 + 网络调度；`register_satnogs_tools` |
| `mbdsdr_ai/sdrangel_plugins.py` | OGN FLARM 解析 + FireDetector 聚类 + RemoteControl JSON；`register_sdrangel_plugins_tools` |
| `tests/sat_groundstation_roundtrip.py` | 5 项端到端往返 + 注册集成 |

硬约束遵守：
- 未修改 `tool_registry.py`、`agent.py`、`sdrangel_adapter.py`。
- 轨道传播复用 `gpredict_adapter.py`（SGP4/TLE/坐标转换），未重写。
- 纯 NumPy，每个 `register_xxx_tools(registry)` 注册 ≥ 2 个工具，handler 返回 `ToolResult`。

## 5. 验证结果

```
$ python3 tests/sat_groundstation_roundtrip.py
== 1. r2cloud 调度器            ... 18 checks PASS
== 2. satnogs 多普勒校正        ...  5 checks PASS (误差 0.0000 Hz < 1 Hz)
== 3. OGN FLARM V6 帧解析       ...  7 checks PASS
== 4. FireDetector 热点聚类     ...  4 checks PASS
== 5. RemoteControl JSON 往返   ...  6 checks PASS
== 6. register_*_tools 集成     ...  5 checks PASS
==== 结果: 45 passed, 0 failed ====
```
