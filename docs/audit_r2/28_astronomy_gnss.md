# R2 深度审查：astronomy.py / gnss_monitor.py

- 审查范围：`mbdsdr_ai/astronomy.py`（634 行）、`mbdsdr_ai/gnss_monitor.py`（357 行）
- 交叉参照：`mbdsdr_ai/orbit.py`、`mbdsdr_ai/new_spacetime.py`、`mbdsdr_ai/sdr_tools.py`
- 审查日期：2026-09-24

---

## 0. 一句话结论

- `astronomy.py` 的**球面三角/GMST/折射/大气质量**是真公式且大体正确，但**太阳/月亮/行星位置、升落时间、卫星过境预测**要么缺失要么是硬编码占位；其中 `predict_satellite_pass` 是**孤儿代码**，没有任何模块调用。
- `gnss_monitor.py` 名不副实：它**不是 GNSS 接收机监测**，而是 **L 频段干扰频谱监视**；没有 NMEA 解析、没有卫星状态、没有伪距/载波相位、**没有 PPP/RTK 算法**。真实的 NMEA RMC 解析器在 `new_spacetime.py:151`。
- **PPP-RTK 融合只是一个枚举标签**（`PNTSource.PPP_RTK`），`PNTFusionEngine._fuse()` 本质是对已经解算好的 (lat, lon) 做逆方差加权平均，没有 RTCM、没有 SP3/CLK 精密星历、没有整周模糊度固定、没有 EKF。
- **"实验室绿、真机红"已坐实**：`sdr_tools.py:4633/4681` 的 GNSS 监测调用方全部喂合成 `np.random.randn` IQ + 固定 3–5 kHz 注入 CW，从不走 `backend.read_samples()`。
- **真实位置来源 = 硬编码字符串**：`sdr_tools.py:3457` `_get_gps()` 直接返回 `"43.82°N, 125.32°E, 海拔 250m, 12 星, HDOP 0.8"`，gnss_monitor 不提供任何位置。

---

## 1. astronomy.py 逐项发现

### 1.1 数学正确性（已手推核对）

| 项 | 位置 | 结论 |
|---|---|---|
| Unix↔JD 常数 2440587.5 | `astronomy.py:186,191` | ✅ 正确（Unix 纪元 1970-01-01 00:00 UTC = JD 2440587.5） |
| JD→MJD 偏移 2400000.5 | `astronomy.py:196,201` | ✅ 正确 |
| GMST IAU 系数 `67310.54841 + (876600·3600 + 8640184.812866)·T + 0.093104·T² − 6.2e-6·T³` | `astronomy.py:214-219` | ✅ 正确；与 `orbit.py:53-56` **逐字重复** |
| 时角 HA = LST − RA | `astronomy.py:273` | ✅ 正确（HA 向西为正） |
| sin(alt) 球面三角 | `astronomy.py:277-282` | ✅ 正确 |
| cos(az) 反解分支 | `astronomy.py:287-294` | ✅ 推导正确（acos∈[0,π]，sin_az<0 时翻到 2π） |
| 折射 Saemundsson `1.02/tan(h+10.3/(h+5.11))` | `astronomy.py:398` | ✅ 标准式；h=0 时 ≈29′，符合 Saemundsson 口径 |
| 低仰角分支 `1.0/tan(h+7.31/(h+4.4))` | `astronomy.py:401` | ✅ Bennett 低仰角变体 |
| 气象修正 `(P/P0)·(283.15/T_K)` | `astronomy.py:404-407` | ✅ 标准 |
| Kasten-Young 大气质量 | `astronomy.py:424-427` | ✅ 正确（第二括号内 h 用度，代码用 `alt_deg`） |
| 天线增益 η(πD/λ)² | `astronomy.py:151` | ✅ |
| HPBW ≈ 70·λ/D | `astronomy.py:158` | ✅ 工程近似 |

### 1.2 [真bug] 反向折射修正方向近似

- `astronomy.py:336-339`：`altaz_to_equatorial` 用**视仰角** `coord.alt_deg` 直接减去 `compute_refraction(coord.alt_deg, obs)`。折射公式本身是非线性的（h 越小修正越大），用视仰角代替真实仰角做迭代初值在 |alt|<15° 时会引入 ~0.1–0.3′ 误差。对指向应用可接受，但应至少做一次不动点迭代。
- 行号：`astronomy.py:336-339`

### 1.3 [空壳] `predict_satellite_pass` 是硬编码占位 + 孤儿

这是本模块最严重的问题：

```python
# astronomy.py:542
alt_approx = 45.0  # 占位值
```

- `astronomy.py:534-535`：`obs_x = observer.longitude_deg` / `obs_y = observer.latitude_deg` 算了但**从未使用**。
- `astronomy.py:542`：仰角被硬编码为 45°，与卫星位置、观测者位置、时刻都无关。
- `astronomy.py:550,564`：`rise_az=0.0`、`set_az=180.0` 也是硬编码。
- 由于 `alt_approx=45 ≥ min_alt_deg=10` 恒成立，时间循环**永远不会进入 else 分支**，即一个 24 h 窗口会被合并成"一次过境"，`set_time` 永远落在窗口末尾，`max_alt` 永远是 45。
- **孤儿**：全仓 grep `predict_satellite_pass` 的实际调用点都指向 `new_spacetime.py:369`（经 `decoders.compute_satellite_position`）和 `orbit.py:223 predict_passes`，**没有任何地方 import astronomy.predict_satellite_pass**。它是死代码。
- 行号：`astronomy.py:487-571`

### 1.4 [空壳] 模块宣称的能力一半不存在

模块 docstring（`astronomy.py:1-20`）列出：

- ✅ J2000↔地平↔银道↔黄道 坐标框架 — 只实现了 J2000↔地平；**银道、黄道转换函数缺失**（`FrameType.GALACTIC/ECLIPTIC` 枚举在 `astronomy.py:54-55` 定义了但没有对应转换函数）。
- ❌ 太阳位置、月亮位置、行星位置 — **完全没有**。没有 VSOP87、没有 ELP/ lunar theory、没有 Kepler 二体解。
- ❌ 天体升/落/中天时间 — **完全没有**（只有上面那个坏掉的卫星过境函数）。
- ❌ 与 stellarium-web-engine 的关系 — 仓库根目录 `stellarium-web-engine/` 是一份**第三方参考源码副本**，`tools/compute-ephemeris.py` 里用了 `ephem`/`skyfield`，但 `mbdsdr_ai/astronomy.py` 是**纯自实现**，没有 import ephem/skyfield；`optional_deps.py:25` 探测到 skyfield 也只是探测，没被 astronomy 使用。"对照 Stellarium"是文案，不是依赖。

### 1.5 [建议] 与 orbit.py 的 GMST 重复

- `astronomy.py:204-223` 与 `orbit.py:50-58` 是**同一个公式的两份拷贝**（连常数 67310.54841、876600·3600、8640184.812866 都一样）。
- 数值上目前**不冲突**（公式一致），但未来修一处忘另一处就会分裂。建议抽到一个 `time_sys.py`。

### 1.6 [真bug] agent.py 接线把 beamwidth 写死成 None

- `agent.py:2537`：
  ```python
  AntennaParams(beamwidth_deg=args.get("beamwidth_deg",5)) if False else None
  ```
  `if False` 是恒假分支，导致用户传的 `beamwidth_deg` 被丢弃，`compute_pointing_guidance` 永远走 `astronomy.py:606` 的硬编码 `beamwidth = 5.0`。
- 行号：`agent.py:2537`（调用点）+ `astronomy.py:606`（兜底默认值）

### 1.7 测试为何放过坏代码

- `tests/test_full_integration.py:760-766` 对赤道→地平的断言是 `altaz.alt_deg is not None` —— 只查"不崩"，不查数值。
- 没有已知恒星（如 Vega/Sirius）的 benchmark 对比，没有 altaz↔equatorial 往返一致性测试，没有 GMST 对照 IERS 公报的测试。
- 这就是 `predict_satellite_pass` 硬编码 45° 还能进 main 的原因。

---

## 2. gnss_monitor.py 逐项发现

### 2.1 [空壳] 模块名与实际功能不符

- 模块 docstring（`gnss_monitor.py:1-20`）自述"GNSS 频带干扰监测与分类"。
- 实际内容：**对一段 IQ 做 FFT、找超门限 bin、按占用 bin 数粗分 CW/NB/WB**。
- 它**完全不做**：
  - NMEA 语句解析（无 `$GxRMC/GGA/GSV/GSA` 处理）；
  - 卫星 PRN 跟踪、C/N₀ 估计；
  - 伪距、载波相位、多普勒测量；
  - 定位解算（最小二乘/Kalman）；
  - PPP-RTK。
- 真正的 NMEA 解析在 `new_spacetime.py:151 parse_gnss_rmc`，且**只支持 RMC**，不支持 GGA（定位质量/卫星数/HDOP）、GSV（卫星仰角方位）。

### 2.2 [占位] PPP-RTK 融合

- `new_spacetime.py:484`：`PPP_RTK = "ppp_rtk"` 只是枚举值。
- `new_spacetime.py:529-591 PNTFusionEngine._fuse()`：把每个源上报的 `(latitude, longitude, altitude_m, accuracy_m)` 做 `Σw·p / Σw`，`w=1/σ²`。
- **没有任何 PPP/RTK 内核**：
  - 无 RTCM 3.x 解码（1005/1077/1087/1097/1107/1230 等消息）；
  - 无 SP3/CLK 精密星历加载；
  - 无载波相位观测；
  - 无整周模糊度固定（LAMBDA）；
  - 无对流层（Saastamoinen/GPT）、电离层（Klobuchar/双频）建模；
  - 无 EKF/UKF；
  - 甚至没有"多个源"的来源——`GNSSDataSource`（`new_spacetime.py:659-678`）的 `connect()` 只是 `self.connected = True`，`get_data()` 返回 `{"satellites": self.satellites, "fix_type": self.fix_type}`，而 `self.satellites` 永远是 0、`fix_type` 永远是 `"none"`。
- 结论：PPP-RTK 在本项目是一个**营销名词**，不是算法。

### 2.3 [真bug] FFT 功率谱未校准，字段名叫 dBm 但不是 dBm

- `gnss_monitor.py:202-204`：
  ```python
  fft_data = np.fft.fftshift(np.fft.fft(iq_samples[:nfft], nfft))
  psd = np.abs(fft_data) ** 2 / (nfft * sample_rate_hz)
  psd_db = 10 * np.log10(psd + 1e-12)
  ```
  - 注释（`gnss_monitor.py:195`）写"Welch 方法"，**实际是单次 FFT 周期图**——没有分段、没有加窗（矩形窗）、没有平均。Welch 需要 `nperseg/noverlap`。
  - `psd` 单位是 `|raw I/Q counts|²/Hz`，没有经过接收链路增益、LNA、ADC 满幅校准。字段名 `power_dbm`/`noise_floor_dbm`/`peak_power_dbm`（`gnss_monitor.py:46-50`）**全部名不副实**——它们是相对 dB，不是 dBm。
  - `+1e-12` 地板在 psd 极小时会主导结果，导致 dB 值被压到 -120 dB 附近，看起来像"校准过的本底"。
- 行号：`gnss_monitor.py:195, 202-204`

### 2.4 [真bug] CHIRP / PULSE 枚举是死值

- `gnss_monitor.py:35-36`：定义了 `CHIRP`、`PULSE`。
- `gnss_monitor.py:161-172` 的分类逻辑只会返回 `CW / NARROWBAND / WIDEBAND / UNKNOWN / NONE`。
- 注释 `gnss_monitor.py:158` 自己承认："Chirp：需要时间维度信息，这里用频谱展宽近似"——但"近似"代码根本没写。
- 后果：`generate_interference_alerts`（`gnss_monitor.py:295-302`）的 else 分支只会落到"未知干扰"，永远不会提示"chirp/脉冲"。

### 2.5 [建议] 分类门限对矩形窗泄漏不鲁棒

- `gnss_monitor.py:161`：`occupied_bins <= 2` 判 CW。
- 矩形窗 FFT 的真实 CW 即使不做任何泄漏控制，主瓣也占 ~2 bin，但旁瓣会拖到 3–5 bin；加了 0.05 幅度注入 CW 时极易被推到 3 bin，从而被误判成窄带。
- 建议：加 Hann 窗后再判，或者用"超门限 bin 的连续区间内峰值集中度"而不是绝对 bin 数。

### 2.6 [建议] 质心测向在 dB 域做加权

- `gnss_monitor.py:341-343`：
  ```python
  weights = np.maximum(0, rssi - np.median(rssi))
  refined_angle = np.average(angles, weights=weights)
  ```
  RSSI 是 dBm（对数域），加权平均应在 mW（线性域）做：`weights = 10**(rssi/10) − median_linear`。当前做法在峰很陡时会高估旁瓣贡献。

### 2.7 [真bug] "实验室绿、真机红"——调用方全部喂合成数据

`gnss_monitor` 的唯一生产调用方在 `sdr_tools.py`：

- `sdr_tools.py:4630-4640` `_gnss_monitor_band`：
  ```python
  iq = (np.random.randn(n_samples) + 1j*np.random.randn(n_samples))/sqrt(2)*0.01
  interference = 0.05*np.exp(2j*np.pi*5000*t)
  iq = iq + interference
  result = monitor_gnss_band(iq, center_freq, sample_rate, band_name)
  ```
  **从不调用 `backend.read_samples()`**。对照同文件 `_detect_fhss`（`sdr_tools.py:3786`）是真读硬件的。
- `sdr_tools.py:4679-4686` `_gnss_monitor_all`：每个频带都重新生成高斯噪声，仅 L1 注入一个 3 kHz 偏移 CW。
- 真机场景后果：
  1. RTL-SDR 在 1575.42 MHz 看到的是 -130 dBm 的扩谱 GPS 信号 + 前端热噪；单次 4096-pt 周期图**看不到 GPS 信号本身**（被噪声平均掉），分类器会把任何真实的窄带 CW 当成"窄带干扰"，把噪声涨落当成"无干扰"；
  2. 没有增益校准，INR dB 在真机上不可复现；
  3. 真实 chirp（如 Starlink 信标）需要时间-频率二维图，单次 FFT 根本抓不到——这正是模块注释里承认的盲区。

### 2.8 与 sdr_tools._get_gps 的关系（已知问题确认）

- `sdr_tools.py:3457-3458`：
  ```python
  def _get_gps(mgr):
      return "GPS 定位（需自研 ai-sdr Mini 设备连接）\n当前为模拟后端，实际数据需连接 ATGM336H 模块\n模拟数据: 43.82°N, 125.32°E, 海拔 250m, 12 星, HDOP 0.8"
  ```
- **确认：这是硬编码字符串，不读串口、不解析 NMEA。** 全仓搜 `ATGM336H` 只在 `agent.py:1780` 的工具描述和 `pose.py:11` 的注释里出现，没有驱动。
- `gnss_monitor.py` 本身**不输出位置**（它是干扰监视），所以"gnss_monitor 能否提供真实位置"答案是：**它根本不打算提供**。项目里唯一的位置来源就是这个硬编码字符串 + `new_spacetime.PNTFusionEngine` 由 LLM/工具调用手填的 (lat, lon)。
- 测试里 `Observer(latitude_deg=43.88, longitude_deg=125.32, height_m=250)`（`tests/test_full_integration.py:752`）和硬编码 GPS 坐标高度重合——整个项目被锚定在长春坐标，其他地点未经验证。

---

## 3. 与 orbit.py / new_spacetime.py 的关系

| 能力 | astronomy.py | orbit.py | new_spacetime.py |
|---|---|---|---|
| GMST | `jd_to_gmst:204` ✅ | `_gmst_days:50` ✅（重复公式） | — |
| ECI→ECEF→ENU | ❌（无） | `compute_satellite_state:101` ✅（真 sgp4 TEME→ECEF） | 委托 `decoders.compute_satellite_position` |
| 卫星过境预测 | `predict_satellite_pass:487` ❌硬编码 45° | `predict_passes:223` ✅ | `predict_satellite_pass:369` ✅（活的） |
| SatellitePass dataclass | `:462`（孤儿） | — | `:338`（活的） |
| 赤道↔地平 | ✅ 自实现 | 站心 ENU 直接出 alt/az | — |
| 大气折射 | ✅ Saemundsson | — | — |
| NMEA RMC 解析 | ❌ | — | `parse_gnss_rmc:151` ✅（仅 RMC） |
| PNT 融合 | — | — | `PNTFusionEngine:529`（加权均值，非 PPP-RTK） |
| GNSS 数据源插件 | — | — | `GNSSDataSource:659`（空壳 connect） |

**冲突点**：
- 同一概念 `SatellitePass` 有两份不同形状的 dataclass（`astronomy.py:462` vs `new_spacetime.py:338`），字段名一个叫 `max_alt_deg`、一个叫 `max_elevation`，序列化格式不兼容。
- 同一函数名 `predict_satellite_pass` 两份实现（一个坏一个好），调用方全部绑到 new_spacetime 那份，astronomy 那份是死代码。
- GMST 公式两份拷贝，数值当前一致但易漂移。

---

## 4. 严重度汇总

| # | 严重度 | 位置 | 问题 |
|---|---|---|---|
| 1 | [空壳/真bug] | `astronomy.py:542` | `predict_satellite_pass` 硬编码 alt=45°，rise/set az 硬编码，且无调用方（孤儿） |
| 2 | [空壳] | `gnss_monitor.py` 整体 | 不是 GNSS 监测，是干扰频谱监视；无 NMEA/无定位/无 PPP-RTK |
| 3 | [占位] | `new_spacetime.py:484,529` | PPP-RTK 只是枚举标签；`_fuse` 是位置加权均值，无 RTCM/精密星历/模糊度固定/EKF |
| 4 | [真bug] | `sdr_tools.py:4633,4681` | GNSS 监测喂合成 IQ，从不读硬件——"实验室绿、真机红" |
| 5 | [真bug] | `sdr_tools.py:3457` | `_get_gps` 返回硬编码长春坐标字符串，无 ATGM336H 驱动 |
| 6 | [真bug] | `gnss_monitor.py:195,202-204` | 注释称 Welch 实为单次 FFT 周期图；字段叫 dBm 实为相对 dB，未校准 |
| 7 | [空壳] | `gnss_monitor.py:35-36` | CHIRP/PULSE 枚举定义了但分类器永远不返回它们 |
| 8 | [真bug] | `agent.py:2537` | `AntennaParams(...) if False else None` 死分支，beamwidth_deg 参数被吞 |
| 9 | [建议] | `astronomy.py:204` vs `orbit.py:50` | GMST 公式重复两份，易漂移 |
| 10 | [建议] | `astronomy.py:462` vs `new_spacetime.py:338` | SatellitePass 双 dataclass，字段不兼容 |
| 11 | [建议] | `gnss_monitor.py:341` | 质心测向在 dB 域加权，应转线性 mW |
| 12 | [空壳] | `new_spacetime.py:659` | `GNSSDataSource.connect()` 不打开串口，`get_data()` 返回固定 0 颗星/none |
| 13 | [建议] | `tests/test_full_integration.py:760` | 天文测试只查"不崩"，无数值 benchmark，导致 #1 漏网 |

---

## 5. 给 R3 / 修复方的建议（不修改代码，仅指路）

1. **删除或重写** `astronomy.predict_satellite_pass`：要么委托给 `orbit.compute_satellite_state`，要么直接删（调用方已经全在 new_spacetime）。
2. **把 `gnss_monitor.py` 改名**为 `gnss_interference_monitor.py`，或者在模块 docstring 顶部明确"本模块不解算位置、不解析 NMEA"。
3. **PPP-RTK**：要么真的接 `rtklib-py` / `rtklibexplorer`，要么把 `PNTSource.PPP_RTK` 从枚举里拿掉，避免对外宣称有此能力。
4. **真机数据路径**：`_gnss_monitor_band` 应该接 `backend.read_samples()`，并在 Welch 前加 Hann 窗 + 链路增益校准。
5. **GMST 抽公共模块**：`time_sys.py` 同时给 astronomy 和 orbit 用。
6. **测试加数值 benchmark**：至少加一条"已知恒星 Vega 在给定时刻的 alt/az 对照 Skyfield/Stellarium 误差 < 0.1°"的用例。
