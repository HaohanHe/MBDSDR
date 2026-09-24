# 多普勒定轨算法与开源实现参考

> 主题：从多普勒频移测量反推低轨卫星（或月球卫星）轨道。
> 覆盖观测模型 → 动力学/STM → 估计方法（RLS vs EKF）→ 开源参考实现 → LRO 特殊性 → 课件函数映射。
> 公式用 LaTeX；源码引用标注 `项目 文件:行号`。仓库根 `repos/`。

---

## 1. 观测模型：多普勒频移 → 伪距率

### 1.1 基本关系

设卫星发射标称频率 $f_t$，地面站接收频率 $f_r$。一维径向速度下的非相对论多普勒频移为

$$
f_d = f_r - f_t \approx -f_t\,\frac{\dot{\rho}}{c},
$$

其中 $\rho = \lVert \mathbf r_s - \mathbf r_r \rVert$ 是站星几何距离，$\dot{\rho}$ 是距离变化率（径向速度，站指向星为正），$c = 299792.458\;\mathrm{km/s}$。

**gpredict 的实现完全对应此式**（`repos/gpredict/src/gtk-rig-ctrl.c:305,312`）：

```c
ctrl->dd = -satfreq * (ctrl->target->range_rate / 299792.4580); // 下行接收 Hz
ctrl->du =  satfreq * (ctrl->target->range_rate / 299792.4580); // 上行发射 Hz
```

注意下行取负号：地面站接收时，若卫星靠近（$\dot\rho<0$），接收频升高，本振应下调 $|f_d|$；上行转发则取正。

### 1.2 距离变化率的矢量表达

设卫星在地固系 ECEF 位置/速度为 $\mathbf r_s,\dot{\mathbf r}_s$，测站为 $\mathbf r_r,\dot{\mathbf r}_r$（测站随地球自转有速度）。视线单位矢量

$$
\hat{\mathbf e} = \frac{\mathbf r_s - \mathbf r_r}{\rho}.
$$

径向速度（伪距率）为相对速度在视线方向的投影：

$$
\dot{\rho} = \frac{(\dot{\mathbf r}_s - \dot{\mathbf r}_r)\cdot(\mathbf r_s - \mathbf r_r)}{\rho}
           = \hat{\mathbf e}\cdot(\dot{\mathbf r}_s - \dot{\mathbf r}_r).
$$

**gpredict `sgp_obs.c:126` 实现**：

```c
obs_set->range_rate = Dot(&range, &rgvel) / range.w;
```

其中 `range = pos->x - obs_pos.x`（ECI 站星差矢量），`rgvel` 为相对速度。`range.w` 即 $\rho$。这与上式逐符号对应。

### 1.3 从测得的 $f_d$ 反演 $\dot\rho$

实际测得的是接收频率 $f_r$（或相对已知信标频率的拍频），扣除卫星钟偏 $\dot b$（频率漂移，单位 Hz/s 对应的钟速）与传播延迟后：

$$
\dot\rho_{\text{meas}} = -c\left(\frac{f_r - f_t}{f_t} - \dot b\right).
$$

这里 $\dot b$ 是卫星钟相对地面站钟的频率漂移项（钟速，无量纲）。单次多普勒只能给出 $\dot\rho$，不能直接给出 $\rho$，所以多普勒定轨是**伪距率（range-rate）定轨**而非伪距定轨。

### 1.4 相对论修正（如涉及）

对于 LEO（~7 km/s），二阶相对论项量级：

- 横向多普勒（时间膨胀）：$f \to f\sqrt{1-v^2/c^2} \approx f(1-v^2/2c^2)$，LEO 约 $v^2/2c^2 \sim 3\times10^{-10}$，对应 $f_t=400\,\mathrm{MHz}$ 时约 $0.12\,\mathrm{Hz}$。
- 引力红移（地球势差）：$gz/c^2$，LEO 约 $4\times10^{-10}$，同量级。

二者在 LEO 多普勒定轨中通常并入钟速 $\dot b$ 一并估计，**不单独建模**。gpredict / RTKLIB 均不显式加相对论修正，靠 $R$ 矩阵吸收。对于月球卫星（LRO），引力红移来自地-月势差，量级仍 $10^{-10}$，同样可吸收进钟模型。

---

## 2. 动力学模型

### 2.1 ECEF 二体传播

状态矢量取 ECEF 下 6 维：

$$
\mathbf x = [\mathbf r^\top,\; \dot{\mathbf r}^\top]^\top \in \mathbb R^6.
$$

二体运动方程（在地惯性系 ECI 下最干净，因为地球引力中心在 ECI 不动）：

$$
\ddot{\mathbf r}_{\mathrm{ECI}} = -\frac{\mu}{r^3}\mathbf r_{\mathrm{ECI}} + \mathbf a_{\mathrm{pert}},
$$

$\mu = GM_\oplus \approx 3.986004418\times10^{14}\,\mathrm{m^3/s^2}$。$\mathbf a_{\mathrm{pert}}$ 含 $J_2$ 项：

$$
\mathbf a_{J_2} = \frac{3}{2}J_2\frac{\mu R_\oplus^2}{r^5}
\begin{bmatrix}
x(5z^2/r^2 - 1)\\
y(5z^2/r^2 - 1)\\
z(5z^2/r^2 - 3)
\end{bmatrix}.
$$

### 2.2 坐标系变换 ECI ↔ ECEF（GMST）

地球自转角速度 $\omega_\oplus = 7.292115\times10^{-5}\,\mathrm{rad/s}$（**gpredict `sgp4sdp4.h:250` `#define mfactor 7.292115E-5`**，SGP4 内部用于 ECI→ECEF 与站速度计算）。

绕 z 轴旋转 GMST 角 $\theta_g(t)$：

$$
\begin{bmatrix}X\\Y\\Z\end{bmatrix}_{\mathrm{ECEF}}
= R_z(-\theta_g)\begin{bmatrix}X\\Y\\Z\end{bmatrix}_{\mathrm{ECI}},
\quad
R_z(\theta)=\begin{bmatrix}\cos\theta&\sin\theta&0\\-\sin\theta&\cos\theta&0\\0&0&1\end{bmatrix}.
$$

**ECEF 下的速度必须含 Coriolis/输运项**。若 $\dot{\mathbf r}_{\mathrm{ECI}}$ 已知，ECEF 速度为

$$
\dot{\mathbf r}_{\mathrm{ECEF}} = R_z(-\theta_g)\dot{\mathbf r}_{\mathrm{ECI}} - \boldsymbol\omega_\oplus\times\mathbf r_{\mathrm{ECEF}},
$$

其中 $\boldsymbol\omega_\oplus = [0,0,\omega_\oplus]^\top$。最后一项即地球自转带来的牵连速度（Coriolis/输运项），**漏写会导致径向速度 $\dot\rho$ 产生 $\omega_\oplus r$ 量级（LEO 约 300–400 m/s 投影后）的系统误差**，定轨直接发散。gpredict `sgp_obs.c` 注释明确：“The velocity calculation assumes the geodetic …”（`sgp_obs.c:16`），即在算 `rgvel` 时把测站自转速度也减了进去。

### 2.3 状态转移矩阵 STM

线性化动力学：

$$
\dot{\mathbf x} = \mathbf f(\mathbf x),\qquad
\mathbf F(t,t_0) = \frac{\partial \mathbf f}{\partial \mathbf x}.
$$

对二体 $-\mu\mathbf r/r^3$，偏导（ECI 下）：

$$
\mathbf F =
\begin{bmatrix}\mathbf 0_3 & \mathbf I_3\\ \mathbf G & \mathbf 0_3\end{bmatrix},
\quad
\mathbf G = \frac{\mu}{r^5}\left(3\mathbf r\mathbf r^\top - r^2\mathbf I_3\right)
+ \text{$J_2$ 偏导}.
$$

STM $\Phi(t,t_0)$ 满足

$$
\dot{\Phi} = \mathbf F(t)\Phi,\quad \Phi(t_0,t_0)=\mathbf I_6,
$$

数值上与状态一起积分（同时变分方程）：

$$
\frac{d}{dt}\begin{bmatrix}\mathbf r\\\dot{\mathbf r}\\\Phi\end{bmatrix}
= \begin{bmatrix}\dot{\mathbf r}\\-\mu\mathbf r/r^3+\cdots\\\mathbf F\Phi\end{bmatrix}.
$$

课件里的 `propagate_ecef_state_and_stm` 就是干这个：输入参考历元 $\mathbf x_0$ 与当前 STM，输出到 $t$ 的 $\mathbf x(t)$ 与 $\Phi(t,t_0)$。

---

## 3. 估计方法对比

### 3.1 RLS（递推最小二乘，参考历元法）

**原理**：选定一个参考历元 $t_0$ 的状态 $\mathbf x_0$ 为待估参数，把所有观测都“拉回”到 $t_0$ 做批量/递推最小二乘。第 $i$ 个观测时刻 $t_i$ 的预测由 STM 线性外推：

$$
\hat{\mathbf x}(t_i) = \Phi(t_i,t_0)\,\mathbf x_0.
$$

观测残差（伪距率）：

$$
y_i = \dot\rho_{\text{meas},i} - h\big(\Phi(t_i,t_0)\mathbf x_0\big).
$$

雅可比：

$$
\mathbf H_i = \frac{\partial \dot\rho}{\partial \mathbf x_0}
= \frac{\partial \dot\rho}{\partial \mathbf x(t_i)}\Phi(t_i,t_0).
$$

正规方程（高斯-牛顿）：

$$
\Delta\mathbf x_0 = \left(\sum_i \mathbf H_i^\top \mathbf R_i^{-1}\mathbf H_i\right)^{-1}\sum_i \mathbf H_i^\top \mathbf R_i^{-1} y_i.
$$

**优缺点**：
- ✅ 一次解出整段轨道，无过程噪声 $\mathbf Q$ 调参问题；
- ✅ 对一段弧段内动力学模型误差不敏感（批量吸收）；
- ❌ 非线性时需迭代（参考历元 RLS = 迭代最小二乘）；
- ❌ 不能实时递推出状态协方差随时间的演化，多弧段拼接麻烦。

### 3.2 EKF（扩展卡尔曼滤波）

**预测步**：

$$
\hat{\mathbf x}_{k|k-1} = \mathbf f(\hat{\mathbf x}_{k-1|k-1},\Delta t),\qquad
\mathbf P_{k|k-1} = \Phi_{k,k-1}\mathbf P_{k-1|k-1}\Phi_{k,k-1}^\top + \mathbf Q.
$$

**更新步**（标准形式，与 RTKLIB `filter_()` 完全对应）：

$$
\mathbf K = \mathbf P_{k|k-1}\mathbf H^\top\big(\mathbf H\mathbf P_{k|k-1}\mathbf H^\top + \mathbf R\big)^{-1},
$$

$$
\hat{\mathbf x}_{k|k} = \hat{\mathbf x}_{k|k-1} + \mathbf K\,\nu,\qquad
\mathbf P_{k|k} = (\mathbf I - \mathbf K\mathbf H)\mathbf P_{k|k-1}.
$$

其中 $\nu = \dot\rho_{\text{meas}} - h(\hat{\mathbf x}_{k|k-1})$ 是新息。

**RTKLIB 实现**（`repos/RTKLIB/src/rtkcmn.c` `filter_()`）逐行对应：

```c
matcpy(Q,R,m,m);                       // Q = R (测量噪声)
matmul("NN",n,m,n,1.0,P,H,0.0,F);      // F = P H
matmul("TN",m,m,n,1.0,H,F,1.0,Q);      // Q = H' P H + R
matinv(Q,m);                            // Q^-1
matmul("NN",n,m,m,1.0,F,Q,0.0,K);      // K = P H Q^-1
matmul("NN",n,1,m,1.0,K,v,1.0,xp);     // xp = x + K v
matmul("NT",n,n,m,-1.0,K,H,1.0,I);     // I - K H
matmul("NN",n,n,n,1.0,I,P,0.0,Pp);     // Pp = (I-KH) P
```

**$\mathbf Q/\mathbf R 选择**：
- $\mathbf R$（测量噪声）：由多普勒测量带宽与 SNR 决定，典型伪距率噪声 $\sigma_{\dot\rho}\sim 0.05$–$0.3\,\mathrm{m/s}$。RTKLIB 里按卫星高度角/信噪比加权。
- $\mathbf Q$（过程噪声）：吸收动力学模型误差（未建模的 $J_3$、大气阻力、太阳光压），对 LEO 常用分段白噪声或指数相关噪声；位置分量 $\sigma_q\sim 10^{-3}$–$10^{-2}\,\mathrm{m/s^{2}}$ 量级。

### 3.3 适用场景对比

| 维度 | RLS（参考历元） | EKF |
|---|---|---|
| 数据 | 整段弧批量 | 逐点递推 |
| 实时性 | 差（需整段） | 好 |
| 模型误差 | 批量吸收，稳 | 靠 Q 吸收，调参敏感 |
| 协方差 | 一次给出 | 随时间演化 |
| 初值敏感 | 迭代修正 | 初值差易发散 |
| 适用 | 后处理定轨、短弧精化 | 实时跟踪、导航 |

---

## 4. 开源参考

### 4.1 gpredict：SGP4 + 多普勒

- SGP4/SDP4 实现：`repos/gpredict/src/sgpsdp/sgp4sdp4.c`，常数 `pi=3.14159265...`、`mfactor=7.292115E-5`（`sgp4sdp4.h:201,250`）。
- 观测计算：`sgp_obs.c:96-126` 算 ECI 站星差 `range`、相对速度 `rgvel`、方位/俯仰（topocentric）、`range_rate = Dot(range,rgvel)/range.w`。
- 多普勒给电台：`gtk-rig-ctrl.c:305,312`（见 §1.1）。
- **局限**：SGP4 是对近地地球卫星的简化解析模型，靠 TLE 两行根数驱动，不含月球星历。

### 4.2 RTKLIB：卡尔曼滤波

- 标准 EKF 更新 `filter()`/`filter_()`（`rtkcmn.c:1082` 起），即 §3.2 的代码级参考。
- 状态转移在 `rtkpos.c` 的 `udstate*` 系列；过程噪声 `Q` 在 `rcv/std_model` 按分量设置。
- **借鉴点**：RTKLIB 把“位置/速度/钟差/钟速”放进同一状态向量，钟速 $\dot b$ 与伪距率观测直接耦合——这正是多普勒定轨要复用的结构。

### 4.3 GNU Radio：多普勒跟踪块

- `gr-analog/lib/pll_freqdet_cf_impl.cc`：PLL 频率检测器，输出瞬时频偏，用于实时捕获大多普勒频移。
- `gr-digital/lib/costas_loop_cc_impl.cc`：Costas 环做残余载波相位跟踪（对应接收链里的 `pll_bw` 参数，见 `satellite_rx_deepdive.md`）。
- 二者级联：PLL 粗捕多普勒（kHz–几十 kHz），Costas 精跟符号相位。SatDump pipeline 里的 `pll_bw=0.005~0.02` 即 Costas 环带宽。

### 4.4 poliastro / astropy / skyfield：轨道动力学

- poliastro（MIT）：纯 Python 天体动力学，解析/数值传播（Kepler、Lambert）、坐标变换、`Orbit.from_classical`、`Ephem`。["https://docs.poliastro.space/en/latest/"]
- astropy.coordinates：TEME↔GCRS 转换、JPL 星历（DE430）取月球/太阳位置。["https://docs.astropy.org/en/latest/coordinates/solarsystem.html"]
- skyfield：完整 TLE/SGP4 + JPL 星历，可直接取月球位置矢量。

---

## 5. LRO 特殊性：为什么不能用 TLE/SGP4

1. **SGP4 是地球中心引力场模型**，只拟合地球扁率 $J_2$、大气阻力、日月引力的长期平均项，且假设地球质心固定。LRO 绕月飞，**主引力源是月球（$\mu_\text{Moon}\approx4.905\times10^{12}\,\mathrm{m^3/s^2}$），不是地球**，SGP4 的 $J_2$、大气阻力项完全错误，TLE 对月球卫星无定义。
2. LRO 名义轨道（NASA/ILRS 官方参数）：["https://ilrs.gsfc.nasa.gov/missions/satellite_missions/past_missions/lrol_support.html","https://www.eoportal.org/satellite-missions/lro","https://www.lpi.usra.edu/leag/documents/03LunarRoboticStatusGarvin.pdf"]
   - 高度：**约 50 km 圆轨道**（相对月面），高度控制 ±20 km；
   - 周期：**约 113 分钟**；
   - 倾角：**约 90°（极轨，相对月球赤道）**，每年漂移约 0.5°；
   - 每圈最多约 48 分钟地影/月掩。
3. **正确参考轨道构造**：用 JPL 月球星历（DE430）经 skyfield/astropy 取月球在地心惯性系的位置 $\mathbf r_\text{Moon}(t)$，再在**月心惯性系（J2000 月心）**下用二体 + 月球重力场（GRAIL）积分 LRO 轨道，最后叠加月球公转得到地心位置。不能拿地球 TLE。
4. 由于月球距地 ~38 万 km，地面站看到的 LRO 多普勒主要由**月球公转（~1 km/s）+ LRO 绕月（~1.5 km/s）**合成，径向变化比 LEO 复杂得多；定轨时状态向量要在月心系积分，观测方程再投影到地面站视线。

---

## 6. 课件函数映射

| 课件函数 | 输入 | 输出 | 算法要点 |
|---|---|---|---|
| `read_data` | 原始多普勒记录文件路径 | 时间戳序列 + $f_d$ 序列 + 元数据 | 解析二进制/CSV，按时间排序，剔除异常点 |
| `select_observations` | 全部观测、仰角掩码、SNR 阈值 | 选中的观测子集 | 仰角 $>5°$、SNR 阈值、剔除月掩/地影段（LRO） |
| `calibrate_observation_time` | UTC 时间戳 | 校准后发射/接收时刻 | 扣除信号传播时延 $\rho/c$，区分发射时刻 $t_t$ 与接收时刻 $t_r$；钟差标定 |
| `propagate_ecef_state_and_stm` | 参考历元状态 $\mathbf x_0$、STM、目标时刻 $t$ | $\mathbf x(t)$、$\Phi(t,t_0)$ | §2：ECI 二体+$J_2$ 积分，含 Coriolis 项转 ECEF，STM 与状态联立积分 |
| `pseudorange_rate_and_jacobian` | 预测状态 $\mathbf x(t)$、测站位置 | $\dot\rho_{\text{pred}}$、雅可比 $\partial\dot\rho/\partial\mathbf x$ | §1.2：$\dot\rho=\hat{\mathbf e}\cdot(\dot{\mathbf r}_s-\dot{\mathbf r}_r)$；雅可比为视线方向对位置/速度的偏导 |
| `reference_epoch_rls_update` | 选中观测、当前 $\mathbf x_0$ 估计 | 更新后的 $\mathbf x_0$、协方差 | §3.1：高斯-牛顿迭代，$\Delta\mathbf x_0=(\sum\mathbf H_i^\top\mathbf R_i^{-1}\mathbf H_i)^{-1}\sum\mathbf H_i^\top\mathbf R_i^{-1}y_i$ |

**数据流**：`read_data → select_observations → calibrate_observation_time →`（每观测）`propagate_ecef_state_and_stm → pseudorange_rate_and_jacobian →` `reference_epoch_rls_update`（迭代直到收敛）。

---

## 7. 速查：关键常数与公式

| 量 | 值 | 来源 |
|---|---|---|
| 光速 $c$ | 299792.458 km/s | gpredict `gtk-rig-ctrl.c:305` |
| 地球自转角速度 $\omega_\oplus$ | 7.292115×10⁻⁵ rad/s | gpredict `sgp4sdp4.h:250` |
| 地球引力常数 $\mu_\oplus$ | 3.986004418×10¹⁴ m³/s² | WGS84/EGM96 |
| 月球引力常数 $\mu_\text{Moon}$ | 4.9048695×10¹² m³/s² | DE430 |
| LRO 轨道 | 50 km 圆极轨，~113 min，i≈90° | ILRS/eoportal |
| 多普勒公式 | $f_d=-f_t\dot\rho/c$ | `gtk-rig-ctrl.c:305` |
| 伪距率 | $\dot\rho=\hat{\mathbf e}\cdot(\dot{\mathbf r}_s-\dot{\mathbf r}_r)$ | `sgp_obs.c:126` |
| EKF 更新 | $\mathbf K=\mathbf P\mathbf H^\top(\mathbf H\mathbf P\mathbf H^\top+\mathbf R)^{-1}$ | RTKLIB `rtkcmn.c` filter_() |

---

## 8. 参考来源

- gpredict SGP4/多普勒：本仓库 `repos/gpredict/src/sgpsdp/`、`src/gtk-rig-ctrl.c`。
- RTKLIB EKF：本仓库 `repos/RTKLIB/src/rtkcmn.c`。
- poliastro 天体动力学库。["https://docs.poliastro.space/en/latest/"]
- astropy 坐标与 JPL 星历。["https://docs.astropy.org/en/latest/coordinates/solarsystem.html"]
- LRO 轨道参数（ILRS / eoportal / LPI）。["https://ilrs.gsfc.nasa.gov/missions/satellite_missions/past_missions/lrol_support.html","https://www.eoportal.org/satellite-missions/lro"]
