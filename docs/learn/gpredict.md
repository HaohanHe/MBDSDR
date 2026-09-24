# Gpredict 卫星轨道与指向算法设计笔记

> 源码快照：`repos/gpredict/`（csete/gpredict，depth=1）。
> 关键目录：`src/sgpsdp/`（SGP4/SDP4 内核，Dr. T.S. Kelso → N. Kyriazis → A. Csete 移植版）、
> `src/predict-tools.c`、`src/sat-vis.c`、`src/orbit-tools.c`、`src/time-tools.c`。
> 说明：`libsgp4`（github.com/csete/libsgp4）克隆失败（仓库不可达），但 gpredict 自身
> `src/sgpsdp/` 已是完整的 SGP4/SDP4 实现（与 Vallado 参考实现同源），足够对照。
> 本笔记所有行号均基于上述快照。

---

## 0. 总体调用链

一次预测 / 实时指向的调用栈（自顶向下）：

```
predict_calc(sat, qth, t)                     predict-tools.c:56
  ├─ tsince = (jul_utc - jul_epoch)*xmnpda    predict-tools.c:69   [分钟]
  ├─ SGP4(sat, tsince)  或  SDP4(sat, tsince) predict-tools.c:72-75
  │     └─ 首次调用时跑初始化块                sgp4sdp4.c:38-144 (SGP4)
  │                                           sgp4sdp4.c:294-382 (SDP4)
  ├─ Convert_Sat_State(&pos, &vel)            predict-tools.c:77
  │     └─ pos *= xkmper; vel *= xkmper*xmnpda/secday   sgp_math.c:214-218
  ├─ Calculate_Obs(jd, &pos, &vel, &obs_geod, &obs_set) predict-tools.c:82
  │     ├─ Calculate_User_PosVel(jd, &obs_geod, &obs_pos, &obs_vel)  sgp_obs.c:18
  │     │     └─ theta = ThetaG_JD(jd) + lon   sgp_obs.c:25   [LMST]
  │     ├─ range = sat_pos - obs_pos          sgp_obs.c:96-98
  │     ├─ rgvel = sat_vel - obs_vel          sgp_obs.c:100-102
  │     ├─ ECI → SEU（South/East/Up）          sgp_obs.c:110-114
  │     ├─ azim = atan(-top_e/top_s) + 象限修正 sgp_obs.c:115-119
  │     ├─ el   = asin(top_z / |range|)        sgp_obs.c:120
  │     └─ range_rate = dot(range, rgvel)/|range|  sgp_obs.c:126
  └─ Calculate_LatLonAlt(jd, &pos, &sat_geod) predict-tools.c:83
        └─ lon = atan2(y,x) - ThetaG_JD(jd)   sgp_obs.c:51-52
```

注意：gpredict **不**在卫星端做 TEME→ECEF 旋转，而是把地面站位置旋转到 TEME/ECI 系，
然后直接在 ECI 系里做站心变换（method (b)）。这与 MBDSDR `orbit.py` 的 method (a)
（卫星端旋转 -GMST）是等价的两种路线，后面第 3 节展开。

---

## 1. SGP4 真实实现

### 1.1 物理常数（硬编码 WGS-72，无 whichconst 运行时切换）

`src/sgpsdp/sgp4sdp4.h:200-252`：

| 宏 | 值 | 含义 | 行号 |
|---|---|---|---|
| `de2ra` | 1.74532925E-2 | 度→弧度 | h:200 |
| `pi` | 3.1415926535898 | π | h:201 |
| `twopi` | 6.2831853071796 | 2π | h:204 |
| `xj2` | 1.0826158E-3 | J2 谐系数 | h:207 |
| `xj3` | -2.53881E-6 | J3 | h:208 |
| `xj4` | -1.65597E-6 | J4 | h:209 |
| `xke` | 7.43669161E-2 | √(μ)/(xkmper^{3/2})，单位 rad/min（WGS-72） | h:210 |
| `xkmper` | 6.378135E3 | 地球赤道半径 km（**WGS-72**，不是 WGS-84 的 6378.137） | h:211 |
| `xmnpda` | 1.44E3 | 每天分钟数 = 1440 | h:212 |
| `ae` | 1.0 | 归一化单位（地球半径） | h:213 |
| `ck2` | 5.413079E-4 | J2·ae²/2（WGS-72） | h:214 |
| `ck4` | 6.209887E-7 | -J4·ae⁴/4（WGS-72） | h:215 |
| `__f` | 3.352779E-3 | 扁率 1/298.26（**WGS-72**） | h:216 |
| `ge` | 3.986008E5 | μ = GM（WGS-72，km³/s²） | h:217 |
| `__s__` | 1.012229 | SGP4 大气模型 s 参数 | h:218 |
| `qoms2t` | 1.880279E-09 | SGP4 q0-s0 项 | h:219 |
| `secday` | 8.6400E4 | 每天秒数 = 86400 | h:220 |
| `omega_E` | 1.0027379 | 恒星日/太阳日比 | h:221 |
| `omega_ER` | 6.3003879 | ω_E·2π/(1440·π/180)？ 见 ThetaG | h:222 |
| `mfactor` | 7.292115E-5 | 地球自转角速度 rad/s（站速用） | h:250 |
| `thdt` | 4.3752691E-3 |  deep-space 用地球转角（rad/min） | h:248 |

**要点**：gpredict 内核**写死 WGS-72**，没有 Vallado C++ 版的 `whichconst=wgs72/wgs84/wgs72old`
分支。Python `sgp4` 库（MBDSDR 用的）默认也是 WGS-72（legacy），二者一致；但 MBDSDR
在地面站用 WGS-84 椭球（`orbit.py:26-29`），与 SGP4 内部 WGS-72 混用，会引入 ~0.1 km 量级
的系统差（详见第 5 节）。

### 1.2 TLE 解析字段

`src/sgpsdp/sgp_in.c:110-230` `Convert_Satellite_Data()`：

| TLE 列偏移（0-based） | 长度 | 字段 | 写入 | 行号 |
|---|---|---|---|---|
| 2-6 | 5 | NORAD 编号 | `tle->catnr` | sgp_in.c:115-117 |
| 9-16 | 8 | 国际编号 | `tle->idesg` | sgp_in.c:120-121 |
| 18-31 | 14 | Epoch YYDDD.FFFFFFFF | `tle->epoch` | sgp_in.c:126-136 |
| 18-19 | 2 | Epoch 年份（+2000） | `tle->epoch_year` | sgp_in.c:145-147 |
| 20-22 | 3 | Epoch 年积日 | `tle->epoch_day` | sgp_in.c:150-152 |
| 23-31 | 9 | 日小数 | `tle->epoch_fod` | sgp_in.c:155-158 |
| 33-42 | 10 | 平均运动一阶导 \(\dot n\) | `tle->xndt2o` | sgp_in.c:162-164 |
| 44-51 | 7 | 平均运动二阶导（带 E+exp） | `tle->xndd6o` | sgp_in.c:167-173 |
| 53-60 | 8 | B* 阻力（带 E+exp） | `tle->bstar` | sgp_in.c:178-184 |
| 64-67 | 4 | 元组数 | `tle->elset` | sgp_in.c:187-189 |
| 77-84 | 8 | 倾角 i（度） | `tle->xincl` | sgp_in.c:192-194 |
| 86-93 | 8 | RAAN Ω（度） | `tle->xnodeo` | sgp_in.c:197-199 |
| 95-102 | 7 | 偏心率 e（隐含前导小数点） | `tle->eo` | sgp_in.c:202-208 |
| 103-110 | 8 | 近地点幅角 ω（度） | `tle->omegao` | sgp_in.c:211-213 |
| 112-119 | 8 | 平近点角 M（度） | `tle->xmo` | sgp_in.c:216-218 |
| 121-132 | 10 | 平均运动 n（rev/day） | `tle->xno` | sgp_in.c:221-223 |
| 133-137 | 5 | 纪元时圈数 | `tle->revnum` | sgp_in.c:226-228 |

**单位换算**在 `select_ephemeris()` `sgp_in.c:338-376`：
- 角度 → 弧度：`xnodeo/omegao/xmo/xincl *= de2ra`（sgp_in.c:343-346）
- 平均运动：`xno = xno * (2π/1440²) * 1440 = xno * 2π/1440`（rev/day → rad/min），见 sgp_in.c:347-351
- `xndt2o *= (2π/1440²)`（sgp_in.c:352）
- `xndd6o *= (2π/1440²)/1440`（sgp_in.c:353）
- `bstar /= ae`（sgp_in.c:354）
- 深空间判定：`twopi/xnodp/xmnpda >= 0.15625`（周期 ≥ 225 min）置 `DEEP_SPACE_EPHEM_FLAG`（sgp_in.c:370-373）

校验和：`Checksum_Good()` sgp_in.c:52-77（前 68 字符数字求和，'-' 算 1，mod 10 与第 69 位比对）。

### 1.3 SGP4 初始化（首次调用时惰性执行）

入口：`SGP4(sat, tsince)` `sgp4sdp4.c:22`。当 `SGP4_INITIALIZED_FLAG` 未置位时跑初始化块
（sgp4sdp4.c:38-144）。**没有独立的 `sgp4init()` 函数**——初始化与传播合并在 SGP4() 内。

关键步骤：
- 由 `xno` 反演半长轴 `a1 = (xke/xno)^(2/3)`（sgp4sdp4.c:44）
- J2 长期项修正得到 `xnodp`（原始平均运动）和 `aodp`（原始半长轴）：sgp4sdp4.c:51-55
- 近地点 < 220 km 置 `SIMPLE_FLAG`（线性 drag 截断）：sgp4sdp4.c:61-64
- 近地点 < 156 km 调整 `s4`、`qoms24`（sgp4sdp4.c:68-78）
- 计算 drag 系数 `c1, c4, c5`（sgp4sdp4.c:88-105）
- 长期漂移率 `xmdot/omgdot/xnodot`（sgp4sdp4.c:110-118）

### 1.4 传播：`SGP4(sat, tsince)` 返回 ECI 位置速度

传播主体（sgp4sdp4.c:146-258）：
1. 长期项更新：`xmdf = xmo + xmdot*tsince` 等（sgp4sdp4.c:147-153）
2. drag 修正 `tempa/tempe/templ`（sgp4sdp4.c:154-169）
3. `a = aodp*tempa², e = eo - tempe, xn = xke/a^(3/2)`（sgp4sdp4.c:171-175）
4. 长周期项 `axn, aynl, xlt`（sgp4sdp4.c:178-183）
5. **开普勒方程牛顿迭代**（最多 10 次，容差 1e-6）：sgp4sdp4.c:189-202
6. 短周期项修正 `rk, uk, xnodek, xinck, rdotk, rfdotk`（sgp4sdp4.c:227-233）
7. 姿态向量 → ECI 位置速度：
   ```
   sat->pos = (rk*ux, rk*uy, rk*uz)           sgp4sdp4.c:253-255
   sat->vel = (rdotk*ux+rfdotk*vx, ...)        sgp4sdp4.c:256-258
   ```
   **单位：地球半径 + 地球半径/分钟**（归一化），调用方必须 `Convert_Sat_State()` 转 km/km/s
   （sgp_math.c:214-218：pos ×=xkmper，vel ×=xkmper·xmnpda/secday = xkmper/60）。

深空间卫星走 `SDP4()` sgp4sdp4.c:279，额外调用 `Deep(dpinit/dpsec/dpper, sat)` 加日月引力与
共振项（sgp4sdp4.c:514-1016）。入口选择在 `predict-tools.c:72-75`。

---

## 2. 时间系统

### 2.1 儒略日

- `Julian_Date_of_Year(year)` sgp_time.c:282-300：返回该年 1 月 0.0 日的 JD
  （Meeus 公式：`jdoy = 365.25*(year-1) + 30.6001*14 + 1720994.5 + B`）。
- `Julian_Date_of_Epoch(epoch)` sgp_time.c:31-45：把 TLE 的 `YYDDD.FFFFFFFF` 解析成年+年积日+日小数，
  两千年规则：YY<57 → 20YY，否则 19YY（sgp_time.c:37-41）。
- `Julian_Date(struct tm*)` sgp_time.c:158-167：年 + DOY + 日小数。
- 实时取当前 UTC JD：`UTC_Calendar_Now()` sgp_time.c:351-360 → `get_current_daynum()` time-tools.c:50-63。

**MBDSDR 对照**：`orbit.py:121` `jd_utc = when/86400 + 2440587.5`（Unix 纪元 1970-01-01 0h = JD 2440587.5），
与 gpredict 的 `Date_Time` sgp_time.c:180 `(julian_date - 2440587.5)*86400` 互为逆运算，一致。

### 2.2 GMST（格林尼治平恒星时）

gpredict 有两个 GMST 函数，**用途不同**：

**(a) `ThetaG(epoch, deep_arg)` sgp_time.c:308-333** —— 仅用于 deep-space 初始化时
算 `dps.thgr`（sgp4sdp4.c:531）。它先按 IAU 1982 公式算了一遍 GMST（sgp_time.c:326-328），
**随后立即被覆盖**（sgp_time.c:330）：
```c
deep_arg->ds50 = jd - 2433281.5 + UT;          // 距 1950.0 的天数
_ThetaG = FMod2p(6.3003880987 * ds50 + 1.72944494);
```
即 deep-space 用的是 Kelso 简化式 `GMST_rad = 6.3003880987·(days since 1950.0) + 1.72944494`。
这是 Vallado 教材里的近似，与 IAU 公式差几个角秒量级，对 deep-space 长周期项足够。

**(b) `ThetaG_JD(jd)` sgp_time.c:335-348** —— **真正用于观测/星下点**的 GMST：
```c
UT  = Frac(jd + 0.5);          // 日小数（sgp_time.c:341）
jd  = jd - UT;                 // 剥成 0h JD            （sgp_time.c:342）
TU  = (jd - 2451545.0)/36525;  // J2000 起算世纪数      （sgp_time.c:343）
GMST = 24110.54841
     + TU*(8640184.812866 + TU*(0.093104 - TU*6.2E-6));  // sgp_time.c:344
GMST = Modulus(GMST + secday*omega_E*UT, secday);        // sgp_time.c:345
return twopi*GMST/secday;                                 // sgp_time.c:347
```
这是 **IAU 1982 展开式（未折叠形式）**：TU 只取 0h 部分，UT 项单独加 `86400·1.0027379·UT`。
调用点：
- `Calculate_User_PosVel` sgp_obs.c:25（求 LMST = GMST + 站经度）
- `Calculate_LatLonAlt` sgp_obs.c:52（求星下点经度 = atan2(y,x) − GMST）

### 2.3 UTC → UT1 修正

**gpredict 不做 ΔUT1 修正**。TLE 历元和预测时刻都直接当 UTC 用（predict-tools.c:69
`tsince = (jul_utc - jul_epoch)*xmnpda`）。`Delta_ET()` sgp_time.c:265-276 只给太阳位置用
（ET−UT 的经验拟合，26.465 + 0.747622·(year−1950) + …），不进 GMST。

**MBDSDR 对照**：`orbit.py:128` 直接把 `jd_utc` 传给 `_gmst_days(jd_ut1)`（参数名叫 ut1 但实参是 utc），
同样不做 UTC→UT1。两者精度一致（ΔUT1 目前 ~0.1 s，对应 GMST 误差 ~1.5"）。

---

## 3. 坐标变换

### 3.1 ECI（TEME）→ ECEF

gpredict **不做这一步**。它走 method (b)：把地面站位置从 ECEF 旋转到 TEME。

`Calculate_User_PosVel` sgp_obs.c:18-38：
```c
geodetic->theta = FMod2p(ThetaG_JD(_time) + geodetic->lon);  // LMST  sgp_obs.c:25
c  = 1/sqrt(1 + __f*(__f - 2)*sin²lat);                      // sgp_obs.c:27  WGS-72
sq = (1-__f)² * c;                                           // sgp_obs.c:28
achcp = (xkmper*c + alt)*cos(lat);                           // sgp_obs.c:29
obs_pos->x = achcp*cos(theta);                               // sgp_obs.c:30
obs_pos->y = achcp*sin(theta);                               // sgp_obs.c:31
obs_pos->z = (xkmper*sq + alt)*sin(lat);                     // sgp_obs.c:32
obs_vel->x = -mfactor*obs_pos->y;                            // sgp_obs.c:33  Ω×r
obs_vel->y =  mfactor*obs_pos->x;                            // sgp_obs.c:34
obs_vel->z = 0;                                              // sgp_obs.c:35
```
注意：`theta = GMST + lon`，所以 obs_pos 是在 TEME/ECI 系下表达的地面站位置——ECEF 绕 z 轴
转 +GMST 就到 TEME。

MBDSDR 走 method (a)：`orbit.py:128-132` 把卫星 TEME 绕 z 轴转 **−GMST**：
```python
cg, sg = cos(-gmst), sin(-gmst)
rx = cg*r[0] - sg*r[1]; ry = sg*r[0] + cg*r[1]; rz = r[2]
```
与 gpredict 路线相反但数学等价。

### 3.2 ECEF → ENU（站心坐标）

gpredict 在 TEME 系里做 SEU（South-East-Up），不是 ENU。`Calculate_Obs` sgp_obs.c:106-114：
```c
sin_lat, cos_lat = sin/cos(geodetic->lat);
sin_theta, cos_theta = sin/cos(geodetic->theta);   // theta=LMST
top_s = sin_lat*cos_theta*range.x + sin_lat*sin_theta*range.y - cos_lat*range.z;  // 南
top_e = -sin_theta*range.x + cos_theta*range.y;                                    // 东
top_z = cos_lat*cos_theta*range.x + cos_lat*sin_theta*range.y + sin_lat*range.z;   // 上
```
这是标准的 ECI→topocentric SEU 旋转矩阵（Vallado 式 2-29）。

MBDSDR 在 ECEF 系里做 ENU（orbit.py:153-155）：
```python
e_east  = -sin(b)*rx + cos(b)*ry
e_north = -sin(l)*cos(b)*rx - sin(l)*sin(b)*ry + cos(l)*rz
e_up    =  cos(l)*cos(b)*rx + cos(l)*sin(b)*ry + sin(l)*rz
```
把 gpredict 的 top_s 换成 top_north = -top_s，二者一致。

### 3.3 仰角 / 方位角

gpredict sgp_obs.c:115-122：
```c
azim = atan(-top_e / top_s);          // sgp_obs.c:115
if (top_s > 0)  azim += pi;           // sgp_obs.c:116-117  南半平面加 180°
if (azim < 0)   azim += twopi;        // sgp_obs.c:118-119
el = ArcSin(top_z / range.w);         // sgp_obs.c:120
```
等价于 `az = atan2(E, N) = atan2(E, -S)`。`ArcSin` 在 sgp_math.c:50-56 做了 |x|≥1 截断。

MBDSDR orbit.py:156-157：
```python
elevation = asin(e_up/dist)
azimuth   = (degrees(atan2(e_east, e_north)) + 360) % 360
```
与 gpredict 完全等价（MBDSDR 用 N/E，gpredict 用 S/E）。

**大气折射**：gpredict 的折射修正在 sgp_obs.c:131-132 被**注释掉**了，实际返回真实几何仰角。
MBDSDR `orbit.py` 也不加折射；`astronomy.py:374-410` 的 Saemundsson 折射只用于恒星 Alt/Az，
不进卫星指向。这是一致的（卫星指向一般不加折射，~0.5° 在 horizon，10° 以上 < 0.1°）。

### 3.4 视线速度 / Doppler

gpredict sgp_obs.c:126：
```c
obs_set->range_rate = Dot(&range, &rgvel) / range.w;
```
其中 `rgvel = sat_vel_TEME − obs_vel_TEME`（sgp_obs.c:100-102）。**gpredict 在 TEME 系里直接
做差，不显式加 Coriolis**——因为地面站速度已经是 TEME 下的 Ω×r_obs（sgp_obs.c:33-35）。

MBDSDR 在 ECEF 系里做（orbit.py:133-140）：
```python
vx = cg*v_teme[0] - sg*v_teme[1]      # R(-GMST)·v_TEME
vy = sg*v_teme[0] + cg*v_teme[1]
vx += omega_earth * r_ecef[1]         # - (Ω×r)_x = +ω·y
vy += -omega_earth * r_ecef[0]       # - (Ω×r)_y = -ω·x
```
这是输运定理 `v_ECEF = R·v_TEME − Ω×r_ECEF`，符号正确（Ω×r = (−ωy, ωx, 0)，减它就是 +(ωy, −ωx)）。
两种路线数学等价。

**Doppler 公式**：gpredict gtk-sat-list.c:629：
```c
doppler = -100.0e6 * (sat->range_rate / 299792.4580);   // Hz @ 100 MHz
```
MBDSDR orbit.py:184：
```python
shift = -nominal_freq_hz * v_los / C_LIGHT
```
完全一致（远离为正 → 接收频率降低，故负号）。

---

## 4. 过境预测算法

### 4.1 几何可见性预筛

`has_aos(sat, qth)` orbit-tools.c:134-171：粗判卫星是否可能被本站看到。
- GEO 或已衰减 → FALSE（orbit-tools.c:146-147）
- 半长轴 `sma = 331.25·exp(log(1440/meanmo)·2/3)`（orbit-tools.c:160）
- 远地点高度 `apogee = sma·(1+e) − xkmper`（orbit-tools.c:161）
- 可见条件：`acos(xkmper/(apogee+xkmper)) + |inc| > |qth_lat|`（orbit-tools.c:163）
  即地平线以下的最大可达纬度要超过站纬度。

### 4.2 AOS 搜索：粗扫 + 细扫牛顿迭代

`find_aos(sat, qth, start, maxdt)` predict-tools.c:129-206：
- 若当前已在 pass 内（el>0），先 `find_los` 跳到本 pass 结束（predict-tools.c:141-142）
- **粗扫**（el < −1°）：predict-tools.c:156-160
  ```c
  t -= 0.00035 * (sat->el * ((sat->alt/8400.0) + 0.46) - 2.0);
  ```
  步长按仰角高度自适应（约 0.5 min 量级），向后扫到仰角 −1° 以内。
- **细扫**（|el| > 0.005°）：predict-tools.c:172-174
  ```c
  t -= sat->el * sqrt(sat->alt) / 530000.0;
  ```
  线性反推零点，直到 |el| < 0.005°（约 18" 精度）。

### 4.3 LOS 搜索

`find_los` predict-tools.c:226-314：
- 粗扫（el ≥ 1°）：`t += cos((el−1°))·sqrt(alt)/25000`（predict-tools.c:256）
- 细扫：`t += el·sqrt(alt)/502500`（predict-tools.c:263）
- **下降沿判定**：el 接近 0 时，回退 1 秒（`t − 1/86400`）看 el 是否更大，若更大说明正在下落，
  记录 lostime（predict-tools.c:270-277）。这一步区分 AOS（上升沿）和 LOS（下降沿）。

### 4.4 单次 pass 采样与最大仰角

`get_pass_engine` predict-tools.c:462-645：
- 找 los/aos（predict-tools.c:495-496）
- 步长 `step = dt / PRED_NUM_ENTRIES`，且不小于分辨率 `tres = PRED_RESOLUTION/86400`（predict-tools.c:486, 519-525）
- 从 aos 到 los 等步长采样，每步存 `pass_detail_t`（predict-tools.c:546-616）
- 记录 `max_el`、`tca`（最高点时刻）、`maxel_az`（predict-tools.c:607-612）
- 若 `max_el < min_el`，跳到 `los + 0.014` 天（约 20 min）继续找下一次（predict-tools.c:635）

### 4.5 轨道圈数

predict-tools.c:106-110：
```c
sat->orbit = floor((xno*1440/2π + age*bstar) * age + (xmo+omegao)/2π)
           - floor((xmo+omegao)/2π) + revnum;
```
即：平均运动 × 时间 + drag 修正后的累计圈数，减去 epoch 时圈数小数部分，加上 TLE 的 revnum。

### 4.6 星下点 / footprint

- `Calculate_LatLonAlt` sgp_obs.c:45-69：迭代解大地纬度（Bowring 公式，sgp_obs.c:57-63）
- footprint predict-tools.c:104：`12756.33·acos(xkmper/(xkmper+alt))`（地球直径 × 地心角）。

---

## 5. 可迁移到 MBDSDR 的点（逐条对照）

下面对照 `mbdsdr_ai/orbit.py`、`astronomy.py`、`new_spacetime.py`、`gimbal.py`。

### 5.1 GMST

**gpredict 做法**（sgp_time.c:335-348）：未折叠 IAU 式，`TU` 剥成 0h，UT 项单独加
`secday·omega_E·UT`。

**MBDSDR `orbit.py:48-56` `_gmst_days`**：
```python
t = (jd_ut1 - 2451545.0)/36525.0
gmst_sec = (67310.54841 + (876600*3600 + 8640184.812866)*t
            + 0.093104*t*t - 6.2e-6*t*t*t)
```
这是 Vallado **折叠式**（把 `secday·omega_E` 折进线性系数：876600·3600 = 36525·86400）。
与 gpredict 数学等价，**没有错**。基础常数 67310.54841 s 对应 J2000 正午 GMST，正确。

**`astronomy.py:204-223` `jd_to_gmst`**：与 orbit.py 同一折叠式，一致。

**真正的小问题**：
- (a) `orbit.py:128` 实参传的是 `jd_utc`，形参名叫 `jd_ut1`——**没做 UTC→UT1 修正**。
  gpredict 也没做（见 2.3），所以这是与 gpredict **同级**的简化，不是 bug；但若要冲
  亚角秒级，需要加 ΔUT1 表。
- (b) `astronomy.py:222` `(gmst_sec % 86400.0)/86400.0*2π` 与 orbit.py:55
  `radians((gmst_sec % 86400.0)/240.0)` 等价（86400/240=360，×π/180 = ×2π/360）。OK。

### 5.2 Coriolis / TEME→ECEF 速度

**MBDSDR `orbit.py:133-140`**：
```python
vx += omega_earth * r_ecef[1]     # +ω·y
vy += -omega_earth * r_ecef[0]    # -ω·x
```
对照 gpredict 路线 (b)（sgp_obs.c:33-35，站速 = Ω×r_obs = (−ωy, ωx, 0)，然后 `rgvel = v_sat − v_obs`）。
MBDSDR 路线 (a)：`v_ECEF = R·v_TEME − Ω×r_ECEF = R·v_TEME + (ωy, −ωx, 0)`。
**符号正确**。

**但有两个细节问题**：
- (a) `omega_earth` 在 orbit.py:30 定义为 `7.292115146706979e-5`（WGS-84），而 orbit.py:137
  又硬编码 `7.2921159e-5`。两个值差 ~1e-12，可忽略，但应统一。gpredict 用 `mfactor = 7.292115E-5`
  （sgp4sdp4.h:250）。
- (b) MBDSDR 用 WGS-84 的 ω_e，但 SGP4 内部用 WGS-72 的 xke（隐含 μ 和 R）。严格说 Coriolis 项
  应该用与 SGP4 一致的 ω；差异 ~1e-12 rad/s，对 range_rate 的影响 < 1e-6 km/s，可忽略。

### 5.3 坐标变换

**MBDSDR `orbit.py:59-69` `geodetic_to_ecef`**：WGS-84 椭球（a=6378.137, f=1/298.257223563）。
**gpredict `sgp_obs.c:27-32`**：WGS-72 椭球（xkmper=6378.135, __f=3.352779e-3=1/298.26）。

**这是 MBDSDR 与 gpredict 最大的系统性差异**：SGP4 传播器内部用 WGS-72 常数（xke、xkmper、
ck2/ck4），MBDSDR 在地面站端用 WGS-84。两者的地心半径差 2 m、扁率差 ~8e-8，导致站心向量
有 ~米级偏差。gpredict 全程 WGS-72 一致。

**修复建议**：要么地面站也用 WGS-72（xkmper=6378.135, f=1/298.26），要么把 Python sgp4 切到
WGS-84 模式（`Satrec` 构造时传 `wgs84`）。当前 MBDSDR 是"WGS-72 轨道 + WGS-84 站"混用。

**ENU 旋转**（orbit.py:153-155）对照 gpredict sgp_obs.c:110-114：数学等价，正确。
Azimuth `atan2(e, n)`（orbit.py:157）对照 gpredict `atan(-e/s) + π` 修正（sgp_obs.c:115-119）：等价。

### 5.4 仰角/方位

- **MBDSDR orbit.py:156-157**：`asin(up/dist)`、`atan2(e,n)`——正确，与 gpredict 一致。
- **`astronomy.py:248-317` `equatorial_to_altaz`**：球面三角公式（sin alt = sinφ sinδ + cosφ cosδ cosH）
  是对**惯性系赤道坐标**（Ra/Dec）转地平的，不是给卫星 TEME 矢量用的。这段代码在
  `predict_satellite_pass`（astronomy.py:487-571）里**没真用**——它硬编码 `alt_approx = 45.0`
  （astronomy.py:542）当占位值，是死代码/未完成实现。真正的卫星预测走 orbit.py。
  **建议**：要么删了 astronomy.py:487-571，要么让它调 orbit.py:compute_satellite_state。

### 5.5 过境预测

**gpredict**（predict-tools.c:129-314）：粗扫 + 细扫牛顿迭代，自适应步长，|el| < 0.005° 精度，
LOS 还回退 1 s 判定下降沿。

**MBDSDR orbit.py:221-292 `predict_passes`**：固定 30 s 步长扫描（orbit.py:235），靠
`above[i]` 布尔串找连续段。问题：
- (a) 30 s 步长对 NOAA 过境（仰角变化率 ~0.5°/s）会把 AOS/LOS 时刻定位误差 ±15 s，
  对应仰角误差 ~7°——**严重**。gpredict 细扫到 0.005°。
- (b) 没有下降沿判定，直接取连续段的首尾（orbit.py:267-269），在步长边缘会偏。
- (c) 没有 `has_aos` 几何预筛（orbit-tools.c:134-171），对 GEO / 已衰减卫星空扫 24 h。
- (d) 步长不能自适应（gpredict 按 sqrt(alt) 缩放，predict-tools.c:172）。

**`new_spacetime.py:369-452` `predict_satellite_pass`**：30 s 步长（new_spacetime.py:389），
同样问题；且它依赖 `decoders.compute_satellite_position`（外部模块），本文件内看不到坐标变换。

**建议迁移**：
1. 粗扫阶段：仰角 < −1° 时用 `t -= 0.00035*(el*(alt/8400+0.46) - 2.0)`（predict-tools.c:158）。
2. 细扫阶段：`t -= el*sqrt(alt)/530000`（predict-tools.c:172），直到 |el| < 0.005°。
3. LOS 判定：el≈0 时回退 1 s 看 el 是否变大（predict-tools.c:270-277）。
4. 加 `has_aos` 预筛（orbit-tools.c:156-166）：`acos(R/(apogee+R)) + |inc| > |lat|`。

### 5.6 其他

- **轨道圈数**：gpredict predict-tools.c:106-110 有公式，MBDSDR 完全没算。若需要显示圈数可直接搬。
- **footprint**：gpredict predict-tools.c:104 `12756.33·acos(R/(R+alt))`，MBDSDR 没算。
- **`new_spacetime.py:31` `EARTH_RADIUS_KM = 6371.0088`**：这是平均半径，只用于 haversine 地面距离，
  不参与轨道几何，没问题。但不要把它误用到 SGP4 上。
- **`gimbal.py:408-427` `az_el_to_rotator_angles`**：极轴式座架变换是简化近似，公式量级对，
  但严格的 polar mount 变换需要完整旋转矩阵。AZ/EL 直接透传（mount_tilt=0）那一支是对的。
- **Doppler**：两边公式一致（见 3.4），无差异。

---

## 附：关键文件索引

| 主题 | 文件:行 |
|---|---|
| SGP4 初始化+传播 | `src/sgpsdp/sgp4sdp4.c:22-269` |
| SDP4 深空间 | `src/sgpsdp/sgp4sdp4.c:279-509` |
| Deep 日月/共振 | `src/sgpsdp/sgp4sdp4.c:514-1016` |
| TLE 解析 | `src/sgpsdp/sgp_in.c:110-230` |
| 单位换算/深空间判定 | `src/sgpsdp/sgp_in.c:338-376` |
| 常数表 | `src/sgpsdp/sgp4sdp4.h:200-252` |
| 儒略日 | `src/sgpsdp/sgp_time.c:31-300` |
| GMST（deep-space 用） | `src/sgpsdp/sgp_time.c:308-333` |
| GMST（观测用） | `src/sgpsdp/sgp_time.c:335-348` |
| 站位置 TEME | `src/sgpsdp/sgp_obs.c:18-38` |
| 星下点 | `src/sgpsdp/sgp_obs.c:45-69` |
| 站心 Az/El | `src/sgpsdp/sgp_obs.c:86-140` |
| 状态归一化 km | `src/sgpsdp/sgp_math.c:214-218` |
| predict_calc 主驱动 | `src/predict-tools.c:56-111` |
| find_aos | `src/predict-tools.c:129-206` |
| find_los | `src/predict-tools.c:226-314` |
| get_pass_engine | `src/predict-tools.c:462-645` |
| has_aos 几何预筛 | `src/orbit-tools.c:134-171` |
| Doppler @100MHz | `src/gtk-sat-list.c:629` |
| 当前 UTC JD | `src/time-tools.c:50-63` |
| 可见性（卫星是否被照） | `src/sat-vis.c:56-106` |
