# RTKLIB 真实源码移植：时间系统 / 坐标 / RINEX / SPP / NTRIP

> 状态：**时间/坐标/RINEX/SPP 已真实工作并通过单测**；PPP/RTK 仅留骨架接口。
> 源码来源：`repos/RTKLIB`（https://github.com/tomojitakasu/RTKLIB，shallow clone，仅本地学习，不入库）。
> 许可：BSD-2-Clause（RTKLIB 原生许可）。本批为**逐行移植并注释来源**，未直接拷贝大段 C。

## 为什么接 RTKLIB

GNSS 解算里，RTKLIB 是业界标杆：单点定位(SPP)、RINEX 解析、NTRIP 差分、PPP/RTK 全有。
在接真实 GNSS 模块前，先把它的「时间系统 / 坐标系统 / 广播星历 / 最小二乘」核心算法
逐行搬到 Python，后续接 RTCM3 观测流时就有确定性的解算后端。

## 学到了什么（逐条对照源码）

### 1. 物理常数必须逐位一致
| 常数 | 值 | 来源 |
|---|---|---|
| 光速 c | `299792458.0` m/s | `rtklib.h:59 CLIGHT` |
| 地球自转角速度 ω | `7.2921151467e-5` rad/s | `rtklib.h:64 OMGE` |
| WGS84 长半轴 a | `6378137.0` m | `rtklib.h:66 RE_WGS84` |
| WGS84 扁率 f | `1/298.257223563` | `rtklib.h:67 FE_WGS84` |
| 地球引力常数 μ(GPS) | `3.9860050e14` | `ephemeris.c:64 MU_GPS` |
| μ(Galileo/BDS) | `3.986004418e14` | `ephemeris.c:66-67` |

改一个小数点都会让后续星历/定位漂掉，所以单测里直接 `assertEqual` 逐位比对。

### 2. 时间系统：GPS 时与 UTC 差闰秒
- GPS 时起点 **1980-01-06 00:00 GPST**（`rtkcmn.c:132 gpst0`），连续计秒、不跳闰秒。
- UTC 从 1981 年起共插入 **18 个闰秒**，现行 **UTC = GPST − 18 s**（`rtkcmn.c:136-156` leaps 表，首条 2017-01-01 起 -18）。
- `gpst2utc()`（`rtkcmn.c:1425`）：按生效时间倒序查表，第一个让 `t+ls >= 生效时刻` 的条目即当前闰秒。
- 单测：GPS 周0/周内秒0 = 1980-01-06 00:00:00 UTC（当时闰秒=0）；2024 年 UTC-GPST=-18。

### 3. 坐标系统：WGS84 椭球正反算
- `ecef2pos()`（`rtkcmn.c:1634`）：ECEF→经纬高用 **Bowring 迭代**。
  坑：C 源码里 `z = r[2] + v*e2*sinp`，每轮**用原始 r[2]** 而不是累加 z；
  第一次照写成 `z = z + ...` 直接发散死循环（单测超时才暴露）。
- `pos2ecef()`（`rtkcmn.c:1655`）：正反算往返误差 < 1 cm。
- `xyz2enu()`（`rtkcmn.c:1671`）：站心旋转阵；`satazel()`（`rtkcmn.c:3218`）
  `az=atan2(东,北)`、`el=asin(天)`。

### 4. 卫星位置：开普勒轨道
- `eph2pos()`（`ephemeris.c:181`）：由广播星历算卫星 ECEF 位置与钟差。
  - 平近点角 M → 偏近点角 E 用牛顿迭代（容忍 `1e-14`，最多 30 次，`ephemeris.c:79/88`）。
  - 摄动改正（cus/cuc/crs/crc/cis/cic 六谐波）→ 升交点经度 → 地固 XYZ。
  - 钟差含相对论改正 `−2√(μA)·e·sinE / c²`（`ephemeris.c:246`）。

### 5. SPP 单点定位：伪距最小二乘
核心残差方程（`pntpos.c:250`）：
```
v = P − (r + dtr − c·dts + dion + dtrp)
```
设计矩阵（`pntpos.c:253`）：`H = [−e_x, −e_y, −e_z, 1]`，状态 4 维 = X,Y,Z,接收机钟差。
迭代最小二乘 `dx = (HᵀH)⁻¹Hᵀv` 收敛即得位置。
- 几何距离 `geodist()`（`rtkcmn.c:3199`）含 **Sagnac 改正** `+ ω·(x_s·y_r − y_s·x_r)/c`。
- 电离层 Klobuchar 模型（`rtkcmn.c:3275 ionmodel`）已移植。
- 单测用「已知真值位置 + 自洽伪距」合成 6 星，无噪解算位置误差 < 1 cm。

### 6. RINEX 导航文件解析
- 一条星历记录 = 1 时钟行（PRN+时刻+af0/af1/af2）+ 7 数据行（每行 4 个 19 字符字段）。
- `str2num()`（`rtkcmn.c:1166`）：把 RINEX 里的 `D` 指数换成 `E` 再 `atof`。
- `decode_eph()`（`rinex.c:1005`）定义了 data[] 下标→星历字段映射（`A=SQR(data[10])`、`e=data[8]`…）。

### 7. NTRIP client
- `reqntrip_c()`（`stream.c:1293`）：`GET /<mountpoint> HTTP/1.0` + `User-Agent: NTRIP RTKLIB/..` + 可选 `Authorization: Basic`。
- caster 回 `ICY 200` / `HTTP/1.x 200` 后，整条 TCP 流即 RTCM3 字节。
- 与既有 `serial_gnss.NTRIPClient` 对齐并增强（请求头严格按 RTKLIB）。

## 移植对照

| RTKLIB 来源 | 用到的东西 | MBDSDR 文件 |
|---|---|---|
| `rtkcmn.c:1246/1261/1425/1442` | GPS周秒↔UTC、闰秒表 | `mbdsdr_ai/rtklib_adapter.py` `TimeSystem` |
| `rtkcmn.c:1634/1655/1686/3218` | ECEF↔LLH↔ENU、方位仰角 | `CoordinateConverter` |
| `ephemeris.c:181` | 广播星历→卫星位置/钟差 | `SatelliteOrbit.eph2pos` |
| `rtkcmn.c:3275` | Klobuchar 电离层 | `klobuchar_ion_delay` |
| `rinex.c:1005/1187/1166` | RINEX 导航/观测解析 | `RINEXParser` |
| `pntpos.c:250/253` | 伪距最小二乘 SPP | `SPPLocator` |
| `stream.c:1293` | NTRIP client | `NTRIPStream` |

## ToolRegistry 注册
- `gps_time_to_utc` / `ecef_to_llh` / `rinex_parse` / `spp_locate` / `ntrip_connect`
- 见 `tool_registry.py::register_rtklib_tools()`。

## 本批新增文件
- `mbdsdr_ai/rtklib_adapter.py` — 时间/坐标/RINEX/SPP/NTRIP 全部真实移植
- `tests/rtklib_test.py` — 16 项单测（时间/坐标/ENU/RINEX/SPP/常数），全绿
- `docs/learn/rtklib.md` — 本文件

## 后续（接真硬件后）
- RTCM3 解码（`rtcm3.c`）→ 观测数据接入 SPP；现在 SPP 接受外部卫星位置/伪距。
- PPP（`ppp.c`）/ RTK（`rtkpos.c`）目前仅留骨架，需要精密星历/钟差产品才动。
- 真实 RINEX obs 文件全字段解析（多频 C/L/SR 码）。
