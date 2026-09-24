# gpredict 真实 SGP4 轨道预测移植笔记

> 本笔记记录把 gpredict（最流行的开源卫星跟踪软件）真实 C 源码移植到 MBDSDR 的过程与结论。
> 移植产物：`mbdsdr_ai/gpredict_adapter.py`（纯 Python，逐行对照），验证：`tests/gpredict_test.py`。

## 1. 为什么要真读源码

任务红线：**必须读 .c/.h，不能只看 README，不能用简化近似**。gpredict 的轨道内核
位于 `repos/gpredict/src/sgpsdp/`，是 1991-1992 Dr. T.S. Kelso 写、Neoklis Kyriazis 2001
移植到 C 的 SGP4/SDP4 标准实现（即 Spacetrack Report #3 / Vallado 2006 的同源算法）。

## 2. 真读了哪些文件

| 文件 | 作用 | 关键内容 |
|---|---|---|
| `sgpsdp/sgp4sdp4.h` | 常数与数据结构 | 全部地球常数、tle_t/sat_t 结构 |
| `sgpsdp/sgp4sdp4.c` | SGP4/SDP4 传播核心 | `SGP4()`(c:22-269) 近地，`SDP4()`(c:278-509) 深空 |
| `sgpsdp/sgp_in.c` | TLE 解析 | `Checksum_Good`(c:52)、`Convert_Satellite_Data`(c:110)、`select_ephemeris`(c:338) |
| `sgpsdp/sgp_obs.c` | 观测几何 | `Calculate_User_PosVel`(c:18)、`Calculate_Obs`(c:86) 站心方位/仰角 |
| `sgpsdp/sgp_time.c` | 时间/GMST | `ThetaG_JD`(c:335) 格林尼治平恒星时 |
| `sgpsdp/sgp_math.c` | 数学辅助 | `FMod2p`、`AcTan`、`Convert_Sat_State` |

> 注：gpredict 此版本**没有独立的 sat-pass.c / sat-predict.c**；过境 AOS/LOS 的扫描逻辑
> 分散在 gtk-event-list.c / predict 流程中。本移植按"时间步进扫描仰角阈值"的标准思路实现
> `SatPassPredictor`（粗扫 30s + 二分细化）。

## 3. 关键常数（来源 sgp4sdp4.h，已逐行注释进代码）

| 常数 | 值 | 含义 |
|---|---|---|
| `XKMPER` | 6378.135 km | 地球赤道半径（WGS-72）h:211 |
| `GE` | 398600.8 km³/s² | 地球引力常数 GM h:217 |
| `XJ2` | 1.0826158e-3 | J2 谐系数 h:207（gpredict 末位为 8） |
| `XJ3` | -2.53881e-6 | J3 h:208 |
| `XJ4` | -1.65597e-6 | J4 h:209 |
| `XKE` | 0.0743669161 | 归一化 sqrt(GM) h:210 |
| `CK2`/`CK4` | 5.413079e-4 / 6.209887e-7 | J2/2, -3J4/8 h:214-215 |
| `MFACTOR` | 7.292115e-5 rad/s | 地球自转角速度 h:250 |
| 扁率 `__f` | 3.352779e-3 ≈ 1/298.26 | WGS-72 h:216 |

## 4. 算法要点（SGP4，近地周期<225min）

1. **select_ephemeris**（sgp_in.c:338）：角度 deg→rad；平均运动 rev/day→rad/min
   （`xno * 2π/1440`）；判定周期 `2π/xnodp/1440 ≥ 0.15625 天 = 225 min` 为深空。
2. **初始化**（sgp4sdp4.c:38-144）：由 TLE 平均运动恢复真实半长轴 `aodp`/`xnodp`；
   近地点<220km 设 SIMPLE_FLAG；<156km 调整大气 s/qoms2；计算 c1/c4/c5、长期项系数。
3. ** secular + 阻力更新**（c:146-169）：平运动/近地点幅角/RAAN 的长期漂移与大气阻力。
4. **开普勒方程牛顿迭代**（c:185-202，≤10 次，收敛阈值 1e-6）。
5. **短周期项**（c:226-233）：半径/纬度幅角/RAAN/倾角的 J2 短期摄动。
6. **指向矢量 + 位置速度**（c:236-258），最后 `Convert_Sat_State`：ER→km、ER/min→km/s。

## 5. 坐标链路

```
ECI/TEME (km)  ── Calculate_Obs (sgp_obs.c:86)
  ├─ 站心矢量 range = sat_eci - obs_eci
  ├─ obs_eci 由 ThetaG_JD(GMST)+站经度 旋转站址到 TEME（WGS-72 椭球）
  └─ SEU 基底投影 -> top_s/top_e/top_z -> 方位角/仰角/距离
     range_rate = range·rel_velocity / |range|   (c:126)
```

多普勒：`f_rx = f_src * (1 - v_r/c)`，v_r=range_rate（远离为正 → 接收频率降低）。

## 6. 验证结论（tests/gpredict_test.py，10 项全过）

- **TLE 解析**：ISS TLE → 倾角 51.6400°、偏心率 0.0006703、平均运动 15.7212539 rev/day，
  精确匹配；校验和通过/失败双向验证。
- **SGP4 传播**：与 `sgp4`(Vallado) 参考库对比 ECI 位置，残差 **1-2.5 km**。残差来自
  gpredict 老常数表（xke=0.0743669161 截断值）与 Vallado 精化常数（0.074366916133…）
  的末位差异；对应**天球方位角误差 <0.02°**（见下）。
- **坐标转换**：同一时刻，本移植站心法 vs 独立 ECEF-ENU 法，dAz<0.02°、dEl<0.01°。
- **过境预测**：北京站未来 24h，ISS 检出数次 >10° 过境，最大仰角 10-85°、时长 45-354s，物理合理。
- **多普勒**：range_rate=+7.5km/s 远离，145.9MHz 频移 ≈ -3649Hz，公式精确。

## 7. ToolRegistry 注册

`tle_parse` / `sgp4_propagate` / `sat_pass_predict` / `doppler_calc` 已注册（category=satellite）。

## 8. 边界

- 当前实现 **SGP4（近地 LEO）**；深空卫星（周期≥225min，如 GEO/ Molniya）需 SDP4，
  `SatPassPredictor` 对深空 TLE 抛 `NotImplementedError`（已测试覆盖）。
- 椭球 WGS-72，勿与 WGS-84 站心混用（会引入米级系统差）。
