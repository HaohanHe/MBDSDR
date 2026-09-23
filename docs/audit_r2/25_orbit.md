# 25 — orbit.py（卫星轨道力学 / SGP4）审查

- 文件：`mbdsdr_ai/orbit.py`（294 行）
- 审查方式：逐行 + 真实 ISS TLE 数值验证（sgp4 2.27）
- 结论速览：**不是空壳**——真的调用了 `sgp4` 库、真的拉 celestrak TLE、真的做 TEME→ECEF→ENU。但有 **1 个物理符号错误（Coriolis/坐标系变换速度项符号反了）**，导致多普勒视线速度存在 ~0.5 km/s 量级系统偏差；另有 **1 条并行的残废路径 `decoders.py`**（不做 GMST 旋转 + 速度硬编码 0），这才是"真机红"的主要来源。

---

## 0. 总体判定

| 审查点 | 结论 |
|---|---|
| 是否真用 sgp4 库 | ✅ 真用。`from sgp4.api import Satrec, jday`（orbit.py:23），`Satrec.twoline2rv` + `sat.sgp4(jd, fr)`（:119/:126） |
| TLE 解析（两行/三行） | ✅ celestrak 三行格式（名字+L1+L2）正确取 `lines[1],lines[2]`；本地缓存磁盘+内存双缓存。**未做校验和**（sgp4 内部不强制，可接受） |
| 过境预报 AOS/LOS | ⚠️ 有，但**无跨阈值插值**，30s 网格直接取首尾样本；见 [建议] |
| 最大仰角 / 中天 | ✅ 段内 `max(elevation)` 取中天（:267-268） |
| 地球遮蔽 | ✅ 隐式处理：远地侧卫星 `e_up<0` → 仰角<0 → 不构成 `>=min_elevation` 段。无独立"地影"判断，但几何上等价 |
| 多普勒一阶公式 | ✅ `f_shift = -f0·v_los/c`，远离为正 v_los → 红移，符号正确（:186/:274） |
| 相对论修正 | ✅ 不需要。v²/c²≈5e-10，137 MHz 下 ~0.07 Hz，SDR 可忽略 |
| Coriolis 修正 | ❌ **有，但符号反了**（见下） |
| 坐标变换 ECEF/ECI/ENU/经纬高 | ✅ 位置侧全对；❌ 速度侧符号错 |

---

## 1. [真bug] TEME→ECEF 速度变换的 Coriolis 项符号反了

**位置**：`orbit.py:136-142`

```python
vx = cg * v_teme[0] - sg * v_teme[1]
vy = sg * v_teme[0] + cg * v_teme[1]
# GMST 对时间的导数即地球自转角速度（rad/s），r 单位 km → ω×r 单位 km/s
omega_earth = 7.2921159e-5
vx += -omega_earth * r_ecef[1]      # ← 注释说"ω×r"，直接加
vy +=  omega_earth * r_ecef[0]
v_ecef = (vx, vy, v_teme[2])
```

**问题**：从惯性系（TEME）到旋转系（ECEF）的输运定理是

```
v_ECEF = Rz(-GMST)·v_TEME − (Ω × r_ECEF)
```

而代码写成了 `+ (Ω × r_ECEF)`。地球自转角速度 Ω=+ω ẑ（向东），Ω×r=(−ω·y, +ω·x, 0)，代码正是加了这一项。**应当减去**。

**数值验证（GEO 思想实验，最干净的判据）**：
对一颗地球静止卫星，在 ECEF 中应静止 v=0。设 r_ECEF=(42164,0,0)，把 TEME 惯性速度旋转到 ECEF 轴下得 R·v_TEME=(0, 3.07, 0) km/s（即 GEO 惯性切向速度）。

- orbit.py 算出：**v_ECEF = (0, 6.145, 0) km/s**（一个"静止"卫星居然以 6.1 km/s 向东跑）
- 正确应为：(0, −0.005, 0) ≈ 0

**ISS 实测**（TLE 历元后 5 分钟，r_ECEF≈(3664, 2280, 5238) km）：
- orbit.py v_ECEF = (−5.211, 5.943, 1.051)，|v|=7.974 km/s
- 正确 v_ECEF = (−4.879, 5.409, 1.051)，|v|=7.360 km/s
- 东西向速度差 **0.534 km/s = 2·ω·r_ECEF[0]**，正好是双倍错误项

**影响范围**：
- 只影响 `range_rate_kms`（:162）→ 多普勒频移。**不影响**仰角/方位/过境时刻（这些只用位置）。
- 多普勒系统偏差量级：0.3–0.7 km/s 投影到 LOS：
  - NOAA 137 MHz：**140–320 Hz** 偏置（典型多普勒 ±3 kHz 的 ~5–10%）
  - ISS/业余 437 MHz：**440–1020 Hz** 偏置
- 后果：`doppler_correction()`（:186）和 `predict_passes()` 的 `doppler_hz`（:274）给出的修正频率偏；用于自动调谐时会把 SDR 中心频率推偏数百 Hz。

**修复方向**（仅指出，不改代码）：把 :140-141 改为
```python
vx +=  omega_earth * r_ecef[1]   # - (Ω×r)_x
vy += -omega_earth * r_ecef[0]   # - (Ω×r)_y
```

---

## 2. [真bug/ cosmetic] TLE 历元切片错位，丢了年份

**位置**：`orbit.py:170`

```python
"epoch": line1[20:32].strip(),
```

TLE 第一行历元字段（1 起始列）是第 19–32 列 = 年份 2 位 + 年积日 `DDD.dddddddd`，对应 Python 0 索引切片 `line1[18:32]`。

实测 ISS TLE `"1 25544U 98067A   24123.54166667 ..."`：
- `line1[18:32]` = `"24123.54166667"`（正确，含年份 24）
- `line1[20:32]` = `"123.54166667"`（**丢了年份 "24"**）

仅影响展示字段 `epoch`，不参与传播计算。但用户看到的历元缺年份，会误判 TLE 新鲜度。

---

## 3. [空壳 / 并行残废路径] decoders.py 才是"真机红"主因

**位置**：`decoders.py:199`（已知问题确认）+ `decoders.py:150`

`orbit.py` 算出的 `range_rate_kms` **在 orbit.py 自己的链路里是真用了**：
- `orbit.py:162` 计算 → `:186` 多普勒 → `:274` 过境多普勒；
- `sdr_tools.py:3394` 也真用 `st["range_rate_kms"] / orbit.C_LIGHT`。

**但是**存在一条完全独立、且残废的并行路径 `decoders.py`：

```python
# decoders.py:147-150
e, r, v = satellite.sgp4(jd, fr)
...
sat_x, sat_y, sat_z = r  # km   ← 直接把 TEME 位置当 ECEF 用！
```

```python
# decoders.py:199
radial_velocity = 0  # km/s，简化为 0，实际需要速度向量
doppler = freq_hz * radial_velocity / c   # ← 恒等于 0
```

这条路径有两处硬伤：
1. **拿到 TEME 位置后完全不做 GMST 旋转**（orbit.py:130-134 那一套在 decoders.py 里整个缺失），注释自己都写了"完整实现需要考虑地球自转，这里用简化的球面几何"。卫星位置相对真实 ECEF 绕 z 轴转了一个 GMST 角，LOS 方向全错 → 仰角/方位/过境时刻全错。
2. **丢弃速度向量 `v`**，`radial_velocity` 硬编码 0 → 多普勒恒为 0。

**调用关系**：`new_spacetime.py:711-715` 用的是 `from .decoders import list_visible_satellites`（残废路径），而 `sdr_tools.py:3264/3296/3323` 用的是 `orbit.*`（真路径）。**两条路径并存且行为不一致**——这正好解释了"实验室绿、真机红"：单测/合成 TLE 在某条路径上通过，真机过境预报走的是 decoders.py 这条没旋转地球的路径。

---

## 4. [建议] 其他问题

### 4.1 [建议] AOS/LOS 无跨阈值插值
`predict_passes`（:237 `step=30s`）直接把"第一个 ≥min_elevation 样本"当 rise、"最后一个 ≥min_elevation 样本"当 set（:269-271）。30s 网格下：
- rise_time 最晚滞后真实 AOS 达 30s，set_time 最早提前 30s；
- `duration_s`（:287）最长被低估 ~60s。
- 建议在阈值相邻两样本间线性插值仰角求过零点。

### 4.2 [建议] 已在扫描窗口内升起的过境会被误记 rise
若 t0 时刻卫星已高于 `min_elevation`，首段 `seg[0]` 就是 t0，`rise_time` 不是真实 AOS。建议首段若无前导低于阈值样本则标记 "already in pass" 或丢弃。

### 4.3 [建议] altitude_km 用球半径而非椭球高
`orbit.py:169`：
```python
"altitude_km": sqrt(|r_ECEF|²) - WGS84_A
```
减的是赤道半径 6378.137 km，不是当地椭球面法向高。非赤道纬度处误差可达 ~10–21 km（极区最甚）。仅展示字段，不影响过境几何。

### 4.4 [建议] SSL 证书校验被关闭
`orbit.py:46-47`：
```python
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
```
拉 celestrak TLE 时不验证证书，存在 MITM 投毒 TLE 的风险（投毒 TLE 会直接带偏整段轨道）。建议恢复默认校验，或 pin celestrak 证书。

### 4.5 [建议] `jday` 导入未使用
`orbit.py:23` 导入 `jday` 但从未调用；:123 手写 `jd_utc = when/86400 + 2440587.5`。功能等价，但 decoders.py 用的是正规 `jday()`（:131-133），两边风格不一致。无功能 bug。

### 4.6 [建议] TEME→ECEF 简化未加极移/岁差章动
代码只做 Rz(-GMST) 旋转，未做 GAST（时差方程 ~15″）与极移（~0.3″）。对过境预报影响 ~0.5 km / <1s，可接受；但应在文档注明是简化 PEF 而非严格 ITRF。

---

## 5. 已验证正确的部分（避免误报）

- **GMST 公式**（:50-58）：J2000.0 实测 280.4606°，与标准值 280.46° 一致。
- **WGS84 经纬高→ECEF**（:61-71）：标准卯酉圈 N 公式，正确。
- **ECEF→ENU**（:155-157）：东/北/上三个旋转分量逐项核对，与标准站心旋转矩阵一致；仰角 `asin(e_up/dist)`、方位 `atan2(east,north)` 正确。
- **多普勒一阶公式**（:186/:274）：`f·v_los/c` 符号与"远离为正"约定一致；相对论项量级可忽略。
- **位置侧 TEME→ECEF 旋转**（:131-134）：Rz(-GMST) 矩阵方向正确（GEO/vernal-equinox 反推验证）。
- **TLE 缓存**（:80-98）：内存+磁盘双缓存，celestrak 三行格式校验 `startswith("1 "/"2 ")` 到位。

---

## 6. 发现汇总表

| # | 位置 | 级别 | 描述 |
|---|---|---|---|
| 1 | orbit.py:140-141 | **[真bug]** | TEME→ECEF 速度 Coriolis 项符号反（应减 Ω×r 实加），多普勒系统偏差 0.3–0.7 km/s / 140–1000 Hz |
| 2 | orbit.py:170 | [真bug/cosmetic] | epoch 切片 `[20:32]` 应为 `[18:32]`，丢 TLE 年份 |
| 3 | decoders.py:150 / :199 | [空壳/并行路径] | TEME 不做 GMST 旋转直接当 ECEF；径向速度硬编码 0。new_spacetime.py 走此路径，是"真机红"主因 |
| 4 | orbit.py:269-271 | [建议] | AOS/LOS 30s 网格无插值，时刻量化误差 ±30s |
| 5 | orbit.py:269 | [建议] | 窗口起始已在过境中的卫星 rise_time 误记为 t0 |
| 6 | orbit.py:169 | [建议] | altitude_km 减球半径而非椭球高，极区偏 ~20 km |
| 7 | orbit.py:46-47 | [建议/安全] | SSL 证书校验关闭，TLE 可被 MITM 投毒 |
| 8 | orbit.py:23 | [建议] | `jday` 导入未使用 |
| 9 | orbit.py:130 | [建议] | 仅 GMST 旋转，未做 GAST/极移（可接受，需注释） |

**优先修复顺序**：#3（残废并行路径，直接决定真机可用性）→ #1（多普勒符号）→ #4（AOS 插值精度）→ #2/#7。
