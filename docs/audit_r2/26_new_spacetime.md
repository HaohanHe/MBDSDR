# R2 深度审查：`mbdsdr_ai/new_spacetime.py`（728 行）

- 审查范围：`/home/user/Doubao/chats/38438160041798146/mbdsdr_ai/new_spacetime.py`
- 审查方式：只读静态审查 + 全仓 grep 调用方 + 对照 `orbit.py` / `astronomy.py` / `decoders.py` 真实代码
- 审查日期：2026-09-24

---

## 0. 模块定位结论（先说人话）

**这个文件不是"时空参考系变换"模块，也不是孤儿代码——它是一个被 MCP 工具层真实调用的"授时 + 大圆 GIS + 卫星过境薄封装 + 玩具 PNT 融合"模块，但它名字里承诺的"坐标变换/ECEF/ECI/ENU/TAI/UT1/四元数"一行都没有。**

| 审查重点问题 | 实际答案 |
|---|---|
| 是真实时空参考系变换吗？ | **否**。本文件 0 行 ECEF/ECI/ENU/四元数/欧拉角变换。真实变换在 `orbit.py:61-71,130-159`（TEME→ECEF→ENU）和 `astronomy.py:248-299`（赤道→地平） |
| UTC/TAI/GPS/UT1 转换？ | 只有 UTC↔GPS-TOW（`new_spacetime.py:140-146`）。**无 TAI、无 UT1、无 EOP、无闰秒表**（硬编码 `leap_seconds=18`，line 66） |
| 坐标变换正确吗？ | 本文件不做坐标变换；它委托给 `decoders.compute_satellite_position`，而该函数**故意省略地球自转 GMST 旋转**（见 §3-3） |
| 与 orbit.py 关系？ | **重复实现且结果互相矛盾**：同一 MCP 工具层同时暴露 orbit 版（正确）和 new_spacetime 版（错误）两套卫星工具 |
| 与 astronomy.py 关系？ | astronomy 的赤道↔地平变换（恒星用）是真的，但它自己的 `predict_satellite_pass`（`astronomy.py:487-571`）仰角写死 45°，是占位 |
| 实验室绿、真机红？ | **根因在此模块的依赖链上**：`new_spacetime.predict_satellite_pass` → `decoders.compute_satellite_position`（无 GMST 旋转 + 多普勒硬编码 0），合成坐标跑得通，真实过境预测系统性偏差 |
| 是否被真实调用？ | **是**。调用方：`desktop/main_window.py:705`、`mbdsdr_ai/sdr_tools.py:4308-4601`（约 15 个 MCP 工具包装）。不是孤儿 |

---

## 1. 调用方盘点（grep 实证）

`new_spacetime` 符号被以下位置真实 import：

- `desktop/main_window.py:705` — `from mbdsdr_ai.new_spacetime import get_time_info`，GUI 天空图每 30 秒刷授时
- `mbdsdr_ai/sdr_tools.py` — MCP 工具注册区 `category="new_spacetime"`（line 1459–1632 共 12 个工具），实现区 line 4308–4601：
  - `time_get_info` / `time_ntp_sync` / `gnss_system_info` / `gnss_parse_rmc`
  - `gis_distance` / `gis_bearing` / `gis_destination`
  - `pnt_get_state` / `pnt_update_source`
  - `satellite_predict_pass` / `satellite_predict_all` / `sky_view_visible`
- `mbdsdr_ai/openapi_integration.py:132` — 仅文案提示，无实际调用

**结论：模块被真实使用，删除会破坏 GUI 和 ~15 个 MCP 工具。但"被调用"不等于"算法正确"。**

---

## 2. 时间系统审查

### 2.1 [真bug] NTP 时间戳丢掉亚秒部分，授时精度 ±0.5 s
`new_spacetime.py:95-100`：
```python
t2 = struct.unpack('!12I', data)[8]   # 只取整数秒（Receive timestamp seconds）
t3 = struct.unpack('!12I', data)[10]  # 只取整数秒（Transmit timestamp seconds）
t2 -= 2208988800
t3 -= 2208988800
```
- NTP 包 48 字节中，索引 [8]/[10] 是**整数秒**，亚秒小数在索引 [9]/[11]（32-bit fraction）。代码完全丢弃小数。
- 后果：`offset = ((t2-t1)+(t3-t4))/2` 量化误差最大 ±0.5 s。一个自称"授时"的模块，输出的 `clock_offset_ms` 却可能差出 ±500,000 ms，GUI 上显示的"本地时钟偏差"毫无意义。
- 对照：`t1`/`t4` 用 `time.time()`（带亚秒），服务器侧被截断，误差不对称。
- 修复方向：`!12I` 改解 48 字节为秒+小数组合，`t2 = sec + frac/2**32`。

### 2.2 [真bug] GPS 周号被丢弃，下游永远显示"第 0 周"
`new_spacetime.py:144-146`：
```python
gps_epoch = datetime(1980, 1, 6, tzinfo=timezone.utc)
gps_seconds = (gps_datetime - gps_epoch).total_seconds()
info.gps_time = gps_seconds % 604800  # 只存周内秒，周号没存
```
- `TimeInfo.gps_time` 只返回 TOW（0–604799），GPS 周号（自 1980 起 mod 1024）被扔掉。
- 下游两处"补算"周号：
  - `mbdsdr_ai/sdr_tools.py:4322`：`gps_week = int(info.gps_time // 604800)`
  - `desktop/main_window.py:716`：`gps_week = int(info.gps_time // 604800)`
- `gps_time` 已经 mod 过 604800，`// 604800` **恒等于 0**。用户看到的 GPS 永远是"第 0 周"。
- 同时无 GPS 周 rollover（2019-04-06 已滚过一次）处理。

### 2.3 [占位] 闰秒硬编码、无 TAI/UT1
- `new_spacetime.py:66`：`leap_seconds: int = 18`。2017-01-01 起 GPS-UTC=18 s，当前正确；但下次闰秒插入时不会自动更新，也无 IERS 文件读取。
- TAI = GPS + 19 s（常数）——文件中完全没有。
- UT1、极移、EOP 旋转矩阵——完全没有。`orbit.py:50 _gmst_days` 直接用 UTC 当 UT1（`orbit.py:123` `jd_utc = when/86400+2440587.5` 后喂给 `_gmst_days(jd_utc)`），系统级 UTC-UT1 偏差（毫秒级）两处都未建模。对 LEO 仰角影响 ~0.01°，可接受但应注明。

### 2.4 RMC 解析（`new_spacetime.py:151-234`）
- 字段顺序与 NMEA-0183 RMC 标准一致（time/status/lat/hlon/lon/hemi/spd/course/date），§167-220 解析正确。
- [建议] line 164 白名单 `('$GNRMC','$GPRMC','$GARMC')` 缺 `$GLRMC`（GLONASS）、`$GBRMC`（BDS）；`$GARMC` 实为 Galileo，可保留。
- [建议] line 210-211 闰秒秒（second=60.0）会抛 `ValueError` 被吞成 `utc_time=None`，真机 NTP 跳秒时刻静默失步，无日志。

---

## 3. 卫星位置 / 坐标变换审查（本模块真正的雷区）

### 3.1 调用链全貌
```
MCP: satellite_predict_pass / satellite_predict_all / sky_view_visible
  └─ new_spacetime.predict_satellite_pass (line 369) / predict_all_passes (455) / compute_visible_satellite_count (707)
       └─ decoders.compute_satellite_position (decoders.py:115)
            ├─ sgp4 传播得 TEME (ECI) 位置 r
            ├─ 观测点按球面公式摆到 ECEF (decoders.py:160-162)
            ├─ 直接 r_eci - r_obs_ecef 求差  ← 不转 ECEF！
            ├─ 仰角/方位按"法向量"和错误的 ENU 基向量算
            └─ radial_velocity = 0  ← 多普勒恒为 0
```

### 3.2 [真bug] 观测点放在 ECI 系里，缺 GMST 旋转（"实验室绿、真机红"根因）
`decoders.py:145-167`（被 `new_spacetime.py:405` 调用）：
```python
# 计算卫星位置（ECI 坐标系，单位 km）
e, r, v = satellite.sgp4(jd, fr)          # r 是 TEME/ECI
...
# 简化：将 ECI 转换为观测点的仰角/方位角
# （完整实现需要考虑地球自转，这里用简化的球面几何）   ← 作者自承
obs_x = (earth_radius + ...) * cos(lat)*cos(lon)  # 这是 ECEF 坐标！
...
dx = sat_x - obs_x   # ECI - ECEF，量纲同系但参考系差一个绕 z 轴旋转角 GMST
```
- sgp4 输出是 TEME（惯性系），观测点 `obs_x/y/z` 是 ECEF（地固系），两者之间必须绕 z 轴旋转 `-GMST`。代码注释明说"完整实现需要考虑地球自转，这里用简化的球面几何"。
- 后果：算出的仰角/方位随时间漂移，速率 = 地球自转角速度（15.041°/h）。同一颗卫星，中午算和午夜算结果差 ~180°。合成测试（给一个固定时刻、检查函数不崩、返回浮点）全绿；真机对着 NOAA 过境时方位角对不上。
- **对照正确实现**：`orbit.py:130-142` 明确做了 `cg, sg = cos(-gmst), sin(-gmst)` 的 TEME→ECEF 旋转，并对速度加了 Coriolis 项 `vx += -omega*r_y; vy += omega*r_x`。同一仓库里正确的轮子就在 `orbit.py`，`new_spacetime.py` 却没用它。

### 3.3 [真bug] ENU 基向量写错一个符号
`decoders.py:184-189`：
```python
east = -math.sin(obs_lon_rad)
north = -math.sin(obs_lat_rad)*math.cos(obs_lon_rad)
up    =  math.cos(obs_lat_rad)*math.cos(obs_lon_rad)
east_comp  = rx*east + ry*(-math.cos(obs_lon_rad)) + rz*0
north_comp = rx*north + ry*(-math.sin(obs_lat)*math.sin(obs_lon)) + rz*math.cos(obs_lat)
```
- 正确的东向基向量应为 `e_E = (-sinλ, cosλ, 0)`，投影到视线 `r` 上应得 `-rx·sinλ + ry·cosλ`。
- 代码写成 `-rx·sinλ - ry·cosλ`，`ry` 项符号反了。即使补上 GMST 旋转，方位角仍会错。
- `north_comp` 那行（line 189）反而是对的；`up` 仰角（line 172-181）用的法向量 `n=(cosφcosλ, cosφsinλ, sinφ)` 本身正确，但它被投影到了 ECI 卫星矢量上，仍受 §3.2 污染。

### 3.4 [真bug] 多普勒恒为 0，UI 却照常打印
`decoders.py:199-202`：
```python
radial_velocity = 0  # km/s，简化为 0，实际需要速度向量
doppler = freq_hz * radial_velocity / c
```
- 而 `new_spacetime.py:430-432`：
  ```python
  if frequency_hz > 0 and hasattr(pos, 'doppler_hz'):
      if abs(pos.doppler_hz) > pass_obj.max_doppler_hz:
          pass_obj.max_doppler_hz = abs(pos.doppler_hz)
  ```
- `pos.doppler_hz` 永远是 0 → `SatellitePass.max_doppler_hz` 永远是 0.0 → `summary()`（line 365）打印"最大多普勒 ±0 Hz"。
- 模块 docstring（line 10）承诺"轨迹计算"、`SatellitePass` 字段（line 350）承诺 `max_doppler_hz`，全部是 0。
- **对照**：`orbit.py:160-162,186` 用真实视线速度 `range_rate` 算多普勒，是对的。

### 3.5 [占位] decoders 的内置 TLE 是编出来的轨道
`decoders.py:38-50`（被 `new_spacetime.py:378` import 的 `BUILTIN_TLE`）：六颗卫星升交点赤经全部 `100.0000`、近地点幅角全部 `90.0000`、平近点角全部 `270.0000`，偏心率全 `0.0010000`。这是占位 TLE，不是真实根数。celestrak 拉取失败时（`decoders.py:76-86 _resolve_tle` 的 fallback），sgp4 仍能传播、不报错、输出"看起来合理"的浮点数——这是合成测试全绿、真机方位对不上的第二重原因。

### 3.6 [建议] 同一 MCP 层同时暴露两套互相矛盾的卫星工具
`sdr_tools.py` 注册了两组名字相近的工具：

| 工具名 | 注册行 | 后端 | 正确性 |
|---|---|---|---|
| `sdr_satellite_sky_view` | line 883 | `orbit.visible_satellites` | 正确（GMST 旋转 + 真实速度） |
| `sdr_satellite_doppler` | line 900 | `orbit.doppler_correction` | 正确 |
| `sdr_satellite_passes` | line 917 | `orbit.predict_passes` | 正确 |
| `satellite_predict_pass` | line 1583 | `new_spacetime.predict_satellite_pass` → decoders | **错误（无 GMST，多普勒 0）** |
| `satellite_predict_all` | line 1602 | 同上 | **错误** |
| `sky_view_visible` | line 1620 | `new_spacetime.compute_visible_satellite_count` → decoders | **错误** |

LLM agent 可能按名字任选其一，得到两个互相矛盾的过境时刻表。**建议**：把 `new_spacetime.predict_satellite_pass / predict_all_passes / compute_visible_satellite_count` 三个函数改为内部直接转调 `orbit.py`，删除对 decoders 卫星几何的依赖。

### 3.7 [建议] 三个同名 `SatellitePass` 数据类
- `new_spacetime.py:338 class SatellitePass`
- `decoders.py:102 class SatellitePass`
- `astronomy.py:463 class SatellitePass`
三者字段完全不同（datetime / 裸浮点 / dict），import 时极易踩雷。

---

## 4. GIS / PNT 审查

### 4.1 大圆距离/方位/墨卡托（line 258-330）——基本正确
- `haversine_distance`（line 258-272）：标准 Haversine，平均半径 6371.0088 km，正确。
- `bearing_between`（line 275-287）：标准公式 `atan2(sinΔλ·cosφ2, cosφ1·sinφ2 - sinφ1·cosφ2·cosΔλ)`，正确。
- `destination_point`（line 290-308）：标准大圆航法，正确。
- `web_mercator_project`（line 311-320）：`asinh(tan)` 即标准 Web Mercator，数学正确。

### 4.2 [空壳] `web_mercator_project`、`format_dms` 全仓无人调用
- grep 全仓 `web_mercator_project` / `format_dms`：除定义处（line 311, 323）外零引用。
- 是写了但没接线的死代码。

### 4.3 [真bug] PNT 融合在度空间做加权平均，反经线错乱
`new_spacetime.py:575-577`：
```python
self.state.latitude  = sum(p[0]*w ...) / total_w
self.state.longitude = sum(p[1]*w ...) / total_w
self.state.altitude_m = sum(p[2]*w ...) / total_w
```
- 两个源分别在经度 179.9° 和 -179.9°（白令海峡两侧），加权平均结果是 0°（几内亚湾），而不是 180°。跨日界线场景下融合位置会瞬移半个地球。
- 正确做法：把经纬度转 ECEF 单位矢量加权平均后再反投影回经纬高。
- 同段 line 579 `accuracy_m = 1/sqrt(total_w)` 本身是逆方差合成公式，数值正确；前提是位置正确。

### 4.4 [空壳] `DataSourcePlugin` 及两个子类全仓无人使用
- grep `DataSourcePlugin|SDRDataSource|GNSSDataSource`：除 `new_spacetime.py:598-678` 自身外零引用。
- `SDRDataSource.get_data()`（line 648-656）返回静态字典，不产任何 IQ；`GNSSDataSource.get_data()`（line 671-678）返回 `satellites=0, fix_type="none"`，且 `self.satellites` 从未被任何真实 GNSS 读取路径更新。
- 是 docstring 里"可插拔数据源接口"的设计占位，没有实现也没有接线。

---

## 5. 与 orbit.py / astronomy.py 的重复矩阵

| 功能 | new_spacetime.py | orbit.py | astronomy.py | decoders.py |
|---|---|---|---|---|
| NTP 授时 | ✅ line 73 | — | — | — |
| RMC 解析 | ✅ line 151 | — | — | — |
| GPS 周内秒 | ✅ line 140 | — | — | — |
| 大地→ECEF | ❌ | ✅ line 61（WGS84） | — | ❌（球面近似 line 160） |
| GMST | ❌ | ✅ line 50 | ✅ line 204 | ❌ |
| TEME→ECEF | ❌（委托错） | ✅ line 130 | — | ❌（自承省略） |
| ENU→仰角方位 | ❌（委托错） | ✅ line 155 | ✅（赤道→地平，line 248） | ❌（符号错） |
| 视线速度多普勒 | ❌（恒 0） | ✅ line 160,186 | — | ❌（经验近似 line 252） |
| 过境预测 | ✅ line 369（薄封装） | ✅ line 223 | ⚠️ line 487（仰角写死 45°） | — |
| 大圆距离/方位 | ✅ line 258-287 | — | — | — |
| 赤道→地平 | ❌ | — | ✅ line 248（恒星用，正确） | — |

**结论**：坐标变换的"真活"散在 `orbit.py`（卫星）和 `astronomy.py`（恒星）两个文件里；`new_spacetime.py` 既没有自己实现，也没有转调正确的那个，而是转调了 `decoders.py` 里作者自承"简化"的版本。

---

## 6. 问题清单汇总（按严重度）

### [真bug]
1. **`new_spacetime.py:95-100`** — NTP 时间戳丢亚秒小数，授时偏差量化误差 ±0.5 s。
2. **`new_spacetime.py:146` + `sdr_tools.py:4322` + `desktop/main_window.py:716`** — GPS 周号被丢弃，下游 `gps_time // 604800` 恒为 0，UI 永远显示"第 0 周"。
3. **`new_spacetime.py:378,405` → `decoders.py:145-167`** — 卫星 ECI 位置直接减 ECEF 观测点，缺 GMST 旋转，仰角/方位随时间漂移 15°/h。**这是"实验室绿、真机红"的核心根因。**
4. **`decoders.py:184-189`** — ENU 东向基向量 `ry` 分量符号写反，方位角二次错误。
5. **`new_spacetime.py:430-432` → `decoders.py:199`** — `radial_velocity=0` 硬编码，`max_doppler_hz` 永远 0，UI 照常打印"±0 Hz"。
6. **`new_spacetime.py:575-577`** — PNT 融合在度空间加权平均，跨日界线位置错乱。

### [空壳]
7. **`new_spacetime.py:598-629` `DataSourcePlugin`** — 零子类、零实例化。
8. **`new_spacetime.py:632-656` `SDRDataSource`** — 零使用，`get_data` 返回静态字典。
9. **`new_spacetime.py:659-678` `GNSSDataSource`** — 零使用，卫星数/定位类型从未被真实 GNSS 读取路径更新。

### [占位]
10. **`new_spacetime.py:311-320` `web_mercator_project`** — 全仓无人调用。
11. **`new_spacetime.py:323-330` `format_dms`** — 全仓无人调用。
12. **`decoders.py:38-50` `BUILTIN_TLE`**（被本文件 line 378 依赖）— 六颗卫星轨道面元素完全雷同，是编出来的占位根数；离线 fallback 时输出貌似合理但物理错误。
13. **`new_spacetime.py:66`** — 闰秒硬编码 18，无自动更新；TAI/UT1/EOP 完全缺失。

### [建议]
14. 模块改道：`predict_satellite_pass / predict_all_passes / compute_visible_satellite_count` 三个函数改为内部转调 `orbit.py`（已经是正确实现），不要再过 decoders。
15. MCP 工具去重：`sdr_tools.py` 同时暴露 orbit 版（883/900/917）和 new_spacetime 版（1583/1602/1620）卫星工具，结果互相矛盾，建议合并为一组。
16. `SatellitePass` 类三处重名（new_spacetime:338 / decoders:102 / astronomy:463），应统一。
17. RMC talker 白名单补 `$GLRMC/$GBRMC`；闰秒秒 60.0 应记日志而非静默吞掉。
18. 若坚持保留本模块的"新时空"名号，应真正把 `orbit.py` 的 ECEF/ENU/GMST 变换和 TAI/GPS/UTC 时间系统收敛进来；否则建议改名为 `ntp_gis_pnt.py`，避免误导。

---

## 7. 一句话结论

`new_spacetime.py` 不是孤儿，GUI 和 15 个 MCP 工具真实在用；但它名字承诺的"时空参考系变换"一行没有，真正的坐标变换在 `orbit.py`（正确）和 `astronomy.py`（恒星用）。它的卫星过境核心薄封装了 `decoders.py` 里作者自承"省略地球自转、多普勒硬编码 0"的简化版，这就是合成坐标测试全绿、真实 GNSS 过境方位对不上的直接原因。修法不是重写，而是把这三个卫星函数改道到同仓库已经写对的 `orbit.py`，并补上 NTP 亚秒、GPS 周号、PNT 反经线三个真 bug。
