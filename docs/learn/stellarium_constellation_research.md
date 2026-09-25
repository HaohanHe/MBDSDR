# Stellarium 星座 / 星空 / 黄道坐标系统源码研究报告

> 研究对象：Stellarium C++ 源码（基于 Gaia DR3 / HIP 星表版本，星表历元 J2015.5）
> 研究方式：纯源码精读，不编译不运行
> 源码根目录：`repos/stellarium/`

---

## 0. 总览：Stellarium 的天球是怎么组织的

Stellarium 把整个天球当作一个 **三维单位球（`Vec3d`，观察者位于原点）** 来处理，而不是直接用 RA/Dec 经纬度。所有天体（恒星、行星、星座线、赤道、黄道、地平线）最终都被表示为单位球上的一个或多个三维方向矢量，再通过一个 **投影矩阵链（model-view transform）** 映射到屏幕。

整条渲染管线的核心抽象是：

```
恒星 J2000 方向 Vec3d
   → 自行传播（dyrs 年）
   → 岁差/章动矩阵（J2000 → 当日赤道）
   → 坐标架变换（赤道 → 地平/黄道/银道）
   → 观察者旋转（指向、俯仰）
   → 透视/球投影（fisheye/perspective）
   → 屏幕 2D 像素
```

关键类的分工：

| 类 | 职责 |
|---|---|
| `StarMgr` | 加载、索引、查询星表（`src/core/modules/StarMgr.cpp`） |
| `ZoneArray` / `ZoneData` / `Star1/2/3` | 二进制星表格式与按三角区分块存储（`ZoneArray.cpp`、`Star.hpp`） |
| `StelCore` | 时间、坐标架、所有变换矩阵（`src/core/StelCore.cpp`） |
| `Planet` | 地球自转/岁差/章动矩阵（`src/core/modules/Planet.cpp`） |
| `AsterismMgr` / `Asterism` | 星座线（现代版"星群"）加载与绘制（`AsterismMgr.cpp`、`Asterism.cpp`） |
| `ConstellationMgr` | 旧版星座管理（艺术图、边界、标签） |
| `GridLinesMgr` / `SkyLine` | 赤道、黄道、地平线、子午线等参考线（`GridLinesMgr.cpp`） |
| `StelSkyDrawer` | 星等 → 屏幕亮度/半径、B-V → 颜色（`StelSkyDrawer.cpp`） |

---

## 1. 星表数据格式与加载流程

### 1.1 用的是什么星表

现代版本 Stellarium 默认星表目录是 `stars/hip_gaia3/`，基底是 **Hipparcos（HIP）+ Gaia DR3** 交叉数据。配置文件 `stars/hip_gaia3/defaultStarsConfig.json` 按星等把星表分成多个 `.cat` 文件：

| 文件 | 星等范围 (mag) | 记录类型 | 大小 |
|---|---|---|---|
| `stars_0_0v0_21.cat` | -2.0 ~ 6.0 | Star1（亮星，完整 6 维天体测量） | 0.2 MB |
| `stars_1_0v0_16.cat` | 6.0 ~ 7.5 | Star2 | 1.0 MB |
| `stars_2_0v0_17.cat` | 7.5 ~ 9.0 | Star2 | 6.5 MB |
| `stars_3_0v0_10.cat` | 9.0 ~ 10.5 | Star3（暗星，紧凑） | 12.8 MB |
| `stars_4_…_stars_8_…` | 10.5 ~ 18.0 | 可选下载扩展包 | 数百 MB ~ GB |

> 来源：`stars/hip_gaia3/defaultStarsConfig.json`（`magRange` 字段）。

也就是说：**默认随程序分发的亮星星等上限约 10.5 等**；更暗的星（直到 18 等）按需从 SourceForge 下载。

星表历元是固定的：

```cpp
#define STAR_CATALOG_JDEPOCH 2457389.0   // Star.hpp:34
```

即 JD 2457389.0 ≈ **J2015.5**（Gaia DR2 历元）。所有自行都从这个历元起算。

### 1.2 .cat 二进制文件头

每个 `.cat` 文件以一段固定 28 字节头开头，由 `ZoneArray::create()` 读取（`ZoneArray.cpp:110-133`）：

| 偏移 | 字段 | 类型 | 含义 |
|---|---|---|---|
| 0 | magic | uint32 | 文件魔数，判别字节序 |
| 4 | type | uint32 | 0=Star1(HIP), 1=Star2, 2=Star3 |
| 8 | major | uint32 | 主版本 |
| 12 | minor | uint32 | 次版本 |
| 16 | level | uint32 | 大地测量网格（geodesic grid）细分层级 |
| 20 | mag_min | uint32 | 该文件最亮星等（毫星等） |
| 24 | epochJD | float | 历元 JD，必须等于 2457389.0 |

魔数定义在 `ZoneArray.hpp:56-58`：

```cpp
#define FILE_MAGIC            0x835f040a
#define FILE_MAGIC_OTHER_ENDIAN 0x0a045f83   // 需要字节交换
#define FILE_MAGIC_NATIVE       0x835f040b
```

读完头后，按 `type` 构造 `HipZoneArray`（type=0）或 `SpecialZoneArray<Star2/Star3>`（type=1/2），见 `ZoneArray.cpp:191-226`。

### 1.3 恒星记录的三种紧凑结构

这是 Stellarium 星表设计最精彩的部分——**按亮度分级压缩**。亮星给全量数据，暗星尽量省字节，使数亿颗暗星能常驻内存。定义在 `Star.hpp:452-628`：

**Star1（亮星，48 字节，`Star.hpp:452-530`，`static_assert(sizeof(Star1)==48)`）**

```cpp
qint64  gaia_id;   // 8 字节 Gaia DR3 ID
qint32  x0,x1,x2;  // 4+4+4 字节，单位球直角坐标（内部天体测量单位，÷2e9）
qint32  dx0,dx1,dx2; // 自行，单位 µas/yr（÷1000 → mas/yr）
qint16  b_v;       // B-V 色指数，毫星等（÷1000 → mag）
qint16  vmag;      // V 星等，毫星等
quint16 plx;       // 视差，单位 20 µas（×0.02 → mas）
quint16 plx_err;   // 视差误差
qint16  rv;        // 径向速度，100 m/s
quint16 spInt;     // 光谱类型索引
quint8  objtype;   // 天体类型
quint8  hip[3];    // 24 位打包：17 位 HIP + 5 位复合子星编号
```

**Star2（中等星，32 字节，`Star.hpp:532-579`）**：坐标直接存 RA/Dec（mas），有 pmra/pmdec，但没有第三维、没有 RV。

**Star3（暗星，16 字节，`Star.hpp:581-628`）**：RA/Dec 各用 3 字节（0.1 角秒精度，Dec 加 +90° 偏置），B-V 用 1 字节（0.05 mag 步长），V 星等用 1 字节（0.02 mag 步长，偏置 -16 mag），**无自行、无视差**。

```cpp
// Star3 星等解码（Star.hpp:614）
double getMag() const { return qFromLittleEndian(d.vmag) * 20 + 16000; } // 毫星等
// 即 vmag_byte*0.02 + 16.0 mag
```

### 1.4 按三角区分块（Geodesic Grid）

天球被 `StelGeodesicGrid` 剖分成三角形格子。亮星放在层级低（格子大、数量少）的 ZoneArray，暗星放在层级高（格子小、数量多）的 ZoneArray。`ZoneData` 结构（`ZoneData.hpp:37-51`）就是"一个三角形里的恒星数组头"。

加载顺序在 `StarMgr::loadData()`（`StarMgr.cpp:579-601`）：
1. 读 `defaultStarsConfig.json`，逐个 `checkAndLoadCatalog()`；
2. MD5 校验（`StarMgr.cpp:510-516`）；
3. `ZoneArray::create()` mmap 进内存；
4. `updateHipIndex()` 建立 HIP 号 → 恒星的 O(1) 查找表（`ZoneArray.cpp:270`），数组大小 `NR_OF_HIP=120416`（`ZoneArray.hpp:55`）。

星座线里写的就是 HIP 号，运行时通过 `starMgr->searchHP(HIP)` 反查到恒星对象（`Asterism.cpp:213`）。

---

## 2. 星座系统

现代 Stellarium 把"星座线"重构为 **Asterism（星群）**，数据放在每个 skyculture 子目录的 `index.json` 里。以 `skycultures/modern/index.json` 为例。

### 2.1 JSON 结构

顶层结构：

```jsonc
{
  "id": "modern",
  "region": "World",
  "asterisms": [ ... ],   // 星群/辅助线（春三角、夏季大三角等）
  "constellations": [ ... ]  // 88 个正式星座
}
```

单个星座条目（`skycultures/modern/index.json:474-487`）：

```jsonc
{
  "id": "CON modern Aql",
  "lines": [[98036, 97649, 97278], [97649, 95501, 97804], ...],
  "image": {
    "file": "illustrations/aquila.png",
    "size": [512, 512],
    "anchors": [
      {"pos": [163, 232], "hip": 97649},
      {"pos": [385, 131], "hip": 93244},
      {"pos": [397, 397], "hip": 93805}
    ]
  },
  "common_name": {"english": "Eagle", "native": "Aquila", "context": "IAU constellation name"}
}
```

字段说明：

- **`id`**：格式 `CON <文化名> <缩写>`，解析在 `Asterism.cpp:63-82`。
- **`lines`**：多段折线数组，每个元素是一串 **HIP 星号**（或 Gaia DR3 大整数 ID 的字符串形式）。`lines: [[a,b,c],[d,e]]` 表示 a→b→c 一条折线，d→e 另一条。
- **`common_name`**：`english` / `native` / `pronounce` / `IPA` / `transliteration` / `context`，解析在 `Asterism.cpp:130-139`。
- **`is_ray_helper: true`**：辅助延长线（如北斗斗柄延长找北极星），单独着色（`Asterism.cpp:143`）。
- **`image.anchors`**：星座艺术图的"锚点"——把 PNG 上的像素坐标钉到 HIP 星上，Stellarium 据此把整张贴图扭曲贴合到天球面上。
- **`label_offset` / `label_positions`**：标签位置微调（`Asterism.cpp:305-338`）。

### 2.2 加载与解析流程

`AsterismMgr::setSkyCulture()` 遍历 `skyCulture.asterisms`，对每条调 `Asterism::read()`（`Asterism.cpp:58`）：

1. 解析 `id`、`common_name`；
2. 取 `lines` 数组（`Asterism.cpp:140`）；
3. 对每个点：若 `HIP ≤ NR_OF_HIP(=120416)` 则 `searchHP(HIP)`，否则当作 Gaia DR3 ID 调 `searchGaia()`（`Asterism.cpp:210-213`）；
4. **把折线展开成线段对**：中间点重复一次（`Asterism.cpp:285-286`），使 `asterism[]` 数组里每两个元素构成一段线段；
5. 标签位置 = 所有顶点方向矢量的平均（`Asterism.cpp:298-302`）。

### 2.3 绘制：画在天球上的大圆弧

星座线不是屏幕直线，而是 **天球大圆弧（great circle arc）**。`Asterism::drawOptim()`（`Asterism.cpp:360-394`）：

```cpp
for (i = 0; i < asterism.size()/2; ++i) {
    star1 = asterism[2*i]  ->getJ2000EquatorialPos(core);
    star2 = asterism[2*i+1]->getJ2000EquatorialPos(core);
    sPainter.drawGreatCircleArc(star1, star2, &viewportHalfspace);
}
```

也就是说：每段线取两端恒星的 J2000 单位矢量，在球面上插值出大圆弧，再投影到屏幕。这样连线永远贴着天球表面，不会"穿进"天球内部。

### 2.4 其他 skyculture 文件

- `constellation_boundaries.dat`：星座边界多边形（如 `skycultures/chinese/`），文本格式，每行一段边界弧。
- `star_names.fab` / `common_star_names.fab`：恒星专有名词（拜耳命名、中国星官名等）。
- `illustrations/*.png`：星座艺术图。

---

## 3. 黄道坐标：赤道 ↔ 黄道变换

### 3.1 J2000 固定黄道（VSOP87 黄道）

Stellarium 把 J2000 平黄道（VSOP87 基准）到 J2000 赤道（ICRS）的变换 **硬编码为常量矩阵**，见 `StelCore.cpp:58-59`：

```cpp
const Mat4d StelCore::matJ2000ToVsop87(
    Mat4d::xrotation(-23.4392803055555555556*M_PI_180)
  * Mat4d::zrotation( 0.0000275*M_PI_180));
const Mat4d StelCore::matVsop87ToJ2000(matJ2000ToVsop87.transpose());
```

即黄赤交角（obliquity）

$$\varepsilon_0 = 23.4392803^\circ \approx 23^\circ 26' 21''$$

外加一个 0.0000275° 的极小绕 z 旋转（**frame bias，参考架偏差**，约 0.1 角秒量级）。

### 3.2 变换公式推导

取右手直角坐标系：
- $+x$ 指向春分点（白羊座第一点，$\lambda=0,\beta=0$）；
- $+y$ 在赤道面内指向 RA=6h；
- $+z$ 指向北天极。

黄道坐标系 $(\lambda,\beta)$ 与赤道坐标系 $(\alpha,\delta)$ 都是以春分点为 $+x$，区别仅在于 **极轴绕 $x$ 轴倾斜了 $\varepsilon$**。因此二者之间只差一个绕 $x$ 轴的旋转。

对单位矢量 $\mathbf{r}=(\cos\beta\cos\lambda,\ \cos\beta\sin\lambda,\ \sin\beta)$，标准天文约定下黄道 → 赤道为：

$$
\begin{pmatrix} x_{\rm eq} \\ y_{\rm eq} \\ z_{\rm eq} \end{pmatrix}
=
R_x(\varepsilon)
\begin{pmatrix} x_{\rm ecl} \\ y_{\rm ecl} \\ z_{\rm ecl} \end{pmatrix}
=
\begin{pmatrix}
1 & 0 & 0 \\
0 & \cos\varepsilon & -\sin\varepsilon \\
0 & \sin\varepsilon & \cos\varepsilon
\end{pmatrix}
\begin{pmatrix} x_{\rm ecl} \\ y_{\rm ecl} \\ z_{\rm ecl} \end{pmatrix}
$$

展开成经典公式（Meeus, *Astronomical Algorithms* 式 13.3）：

$$
\tan\alpha = \frac{\sin\lambda\cos\varepsilon - \tan\beta\sin\varepsilon}{\cos\lambda}
$$

$$
\sin\delta = \sin\beta\cos\varepsilon + \cos\beta\sin\lambda\sin\varepsilon
$$

验证：夏至点 $\lambda=90^\circ,\beta=0$ → $\sin\delta=\sin\varepsilon$，即 $\delta=\varepsilon$，正确。

逆变换（赤道 → 黄道）就是把 $\varepsilon$ 换成 $-\varepsilon$，或直接取矩阵转置：

$$
\mathbf{r}_{\rm ecl} = R_x(-\varepsilon)\,\mathbf{r}_{\rm eq}
$$

> 注：Stellarium 的 `Mat4d::xrotation(θ)` 实现（`VecMath.hpp:1417-1426`）矩阵元素符号与标准教材约定相反，因此源码里写的是 `xrotation(-ε)`；几何本质就是"绕春分点轴旋转黄赤交角"，报告中公式按标准教材约定书写。

### 3.3 当日黄道（Ecliptic of Date）

J2000 黄道是固定的；但地球自转轴在进动，**当日平黄道** 的交角 $\varepsilon_A$ 随时间缓慢变化。Stellarium 用 Vondrúk/Capitaine/Wallace 的 IAU 2006 岁差模型计算：

```cpp
double eps_A, chi_A, omega_A, psi_A;
getPrecessionAnglesVondrak(JDE, &eps_A, &chi_A, &omega_A, &psi_A);
```

当日赤道（Equinox of Date）相对 J2000 的岁差矩阵（`Planet.cpp:3124`）：

$$
P = R_z(\chi_A)\, R_x(-\omega_A)\, R_z(-\psi_A)\, R_x(\varepsilon_0)
$$

其中 $\psi_A$（黄经岁差）、$\omega_A$（黄道极与当日天极夹角）、$\chi_A$（春分点最终旋转角）由 `getPrecessionAnglesVondrak()` 按 JDE 计算（`precession.h:43`），模型有效范围 ±20 万年。

---

## 4. 天球参考线绘制

所有参考线都抽象成 `SkyLine` 对象，由 `GridLinesMgr` 管理。每条线在 `setLineType()` 里绑定一个 **坐标架（FrameType）**，见 `GridLinesMgr.cpp:692-792`：

| 线 | FrameType | 源码位置 |
|---|---|---|
| 子午线 Meridian | `FrameAltAz`（地平坐标架） | `GridLinesMgr.cpp:696-699` |
| J2000 黄道 | `FrameObservercentricEclipticJ2000` | `GridLinesMgr.cpp:700-703` |
| 当日黄道 | `FrameObservercentricEclipticOfDate` | `GridLinesMgr.cpp:704-707` |
| J2000 天赤道 | `FrameJ2000` | `GridLinesMgr.cpp:713-716` |
| 当日天赤道 | `FrameEquinoxEqu` | `GridLinesMgr.cpp:717-720` |
| 地平线 Horizon | `FrameAltAz` | `GridLinesMgr.cpp:734-737` |
| 银道赤道 | `FrameGalactic` | `GridLinesMgr.cpp:738-741` |
| 二分圈/二至圈 | `FrameEquinoxEqu` | `GridLinesMgr.cpp:763-770` |

绘制原理（`GridLinesMgr.cpp:816-833`）：
- **大圆类**（赤道、黄道、地平线、子午线、银道）：在对应坐标架里取纬度 = 0 的整圆，通过 `sPainter.drawGreatCircleArc()` 分段画出；
- **小圆类**（极距圈、拱极圈、地球本影/半影）：用 `sPainter.drawSmallCircleArc()`，给定一个极轴和角距；
- 坐标架本身由 `StelCore::getProjection(frameType, refMode)` 提供的 model-view 矩阵完成，绘制代码不需要自己算 RA/Dec。

关键点：**参考线不是手写经纬度采样点，而是"在某个坐标架里画一个纬圈 0°"**。换坐标架（J2000 ↔ 当日、赤道 ↔ 黄道 ↔ 地平）就自动得到对应的线。

---

## 5. 恒星位置计算：自行、岁差、章动

### 5.1 自行（Proper Motion）

星表把恒星位置冻结在历元 `J2015.5`。要显示到任意时刻 $T$，先算距历元的年数 $\Delta t = (T - t_0)/365.25$。

对绝大多数 Star2/Star3 恒星（只有切向自行，无第三维），用最简单的线性近似（`Star.hpp:171-174`）：

```cpp
StelUtils::spheToRect(
    getX0() + dyrs*getDx0()*MAS2RAD,   // RA + μ_ra*Δt
    getX1() + dyrs*getDx1()*MAS2RAD,   // Dec + μ_dec*Δt
    pos);
```

即

$$
\alpha(T) = \alpha_0 + \mu_{\alpha*}\,\Delta t,\qquad
\delta(T) = \delta_0 + \mu_\delta\,\Delta t
$$

其中 $\mu$ 单位 mas/yr，`MAS2RAD` 把毫角秒转弧度。

对 Star1 亮星（有完整 6 维：3 维位置 + 3 维自行 + 视差 + 径向速度），用完整的线性空间运动模型（`Star.hpp:149-170`）：

$$
\mathbf{u} = \frac{\mathbf{r}_0(1+\dot\rho_0\Delta t) + \boldsymbol{\mu}_\perp \Delta t}
{\sqrt{1 + 2\dot\rho_0\Delta t + (|\boldsymbol{\mu}_\perp|^2+\dot\rho_0^2)\Delta t^2}}
$$

其中 $\dot\rho_0 = v_r\,\varpi/(\mathrm{AU}/\mathrm{yr})$ 是径向速度引起的距离变化率。这个模型由 Anthony Brown 的天体测量教程推导，能在几千年跨度内保持精度。

### 5.2 岁差（Precession）

恒星自行只改恒星相对 ICRS 的位置；**岁差改的是赤道/春分点本身**。J2000 → 当日赤道用 §3.3 的 $P$ 矩阵（`Planet.cpp:3124`）。

### 5.3 章动（Nutation）

地球自转轴还有周期约 18.6 年的微小摆动（白道升交点引起），振幅约 9.2 角秒。Stellarium 用 IAU 2000B 简表（`precession.h:69`），计算两个角：
- $\Delta\psi$（黄经章动）
- $\Delta\varepsilon$（交角章动）

章动矩阵（`Planet.cpp:3129`）：

$$
N = R_x(-\varepsilon_A-\Delta\varepsilon)\, R_z(-\Delta\psi)\, R_x(\varepsilon_A)
$$

最终 J2000 → 真赤道（含章动）：

$$
\mathbf{r}_{\rm 真赤道} = N \cdot P \cdot \mathbf{r}_{\rm J2000}
$$

章动可在设置里开关（`astro/flag_nutation`，默认开，`StelCore.cpp:152`）。

> 量级提醒：自行通常每年角秒级（天狼星 ~1.3"/yr），百年仅 ~1.3 角分；章动最大 9.2 角秒。对"画星座连线"这类视觉需求，**几十年内可忽略自行，章动完全可忽略**。

---

## 6. 星等与亮度：V 星等 → 屏幕半径/颜色

### 6.1 星等到亮度

点光源（恒星）的对数亮度（`StelSkyDrawer.cpp:381-383`）：

```cpp
float StelSkyDrawer::pointSourceMagToLnLuminance(float mag) const {
    return -0.92103f*(mag + 12.12331f) + lnfovFactor;
}
```

因为 $-0.4\ln 10 \approx -0.92103$，这就是标准 Pogson 关系

$$
L \propto 10^{-0.4\,m}
\quad\Longleftrightarrow\quad
\ln L = -0.4\ln 10\,(m + m_0) + \text{常数}
$$

即星等每暗 5 等，亮度降为 1/100。常数 12.12331 是零点标定。

### 6.2 亮度到屏幕半径

`computeRCMag()`（`StelSkyDrawer.cpp:404-437`）：
1. 把 $\ln L$ 经人眼适应曲线 `eye->adaptLuminanceScaledLn()` 映射；
2. 乘以 FOV 相关缩放（`starLinearScale`，`StelSkyDrawer.cpp:307`）；
3. 半径 < 0.3 像素就不画（`StelSkyDrawer.cpp:409`）；
4. 小星（半径 < 1.2）亮度按 $r^3/1.728$ 补偿（`StelSkyDrawer.cpp:420`）；
5. 大星（>1.2 像素）半径做平方根压缩，避免织女星大到糊屏。

### 6.3 B-V 到颜色

星表里存的是色指数 $B-V$。`Star.hpp:53-56` 把它量化成 0~127 的索引，再查 `StelSkyDrawer::colorTable[128]`（`StelSkyDrawer.cpp:808`）得到 RGB。色卡逻辑：
- 蓝白巨星（B-V 负）→ 偏蓝（R 低 B 高）；
- 太阳（B-V≈0.62）→ 白黄；
- 红巨星（B-V 正）→ 橙红。

---

## 7. 对我们项目的建议：最小可行方案（MVP）

如果要在自己的天空图上画恒星 + 星座线，**不需要照搬 Stellarium 的全套天体测量**。按需求分级：

### 7.1 绝对最小可行（一天工作量）

1. **星表**：直接用 Yale Bright Star Catalog（~9000 颗，亮于 6.5 等），或从 Stellarium 的 `stars_0_0v0_21.cat` 里只取 Star1 记录（HIP、RA、Dec、V 星等、B-V）。字段偏移见 §1.3。
2. **坐标**：直接用 **J2000 赤道坐标**（RA/Dec，弧度），不做岁差/自行/章动——对"今天看星座"视觉误差 < 1 角分，肉眼无感。
3. **投影**：用等距柱状（equirectangular）或方位角投影把天球摊到 2D 画布。
4. **画星**：屏幕半径 $r = k\cdot 10^{-0.4\,m}$（Pogson 律），颜色用简化的 B-V→RGB 查表（8~16 项足够）。
5. **星座线**：直接复用 Stellarium `skycultures/modern/index.json` 里的 `lines`（HIP 号数组），按 HIP 号在星表里查 RA/Dec，两点之间在投影面上连线即可。
6. **黄道**：画一条 $\beta=0$ 的大圆。在赤道坐标下，把黄道等分成 360 个点，每个点 $(\lambda,0)$ 用 §3.2 的 $R_x(\varepsilon_0)$ 转成 RA/Dec，再投影。$\varepsilon_0=23.43928°$。

### 7.2 中等增强

- 加 **地平坐标转换**：需要观察者纬度 $\phi$、地方恒星时 $\theta_L$，把赤道 (RA,Dec) 转成 (Az,Alt)：
  $$
  \sin h = \sin\phi\sin\delta + \cos\phi\cos\delta\cos H,\quad H=\theta_L-\alpha
  $$
- 加 **星等限幅**：FOV 越大、屏幕越暗，能显示的极限星等越高（参考 `computeLimitMagnitude`，`StelSkyDrawer.cpp:316`）。
- 加 **星座边界**：用 `constellation_boundaries.dat` 的多边形。

### 7.3 高精度才需要

- 岁差（IAU 2006 / Vondrak）、章动（IAU 2000B）、自行——只有做"公元前 3000 年"或"公元 10000 年"的古天文/未来星空时才必须。
- 完整 6 维天体测量、视差、径向速度——只对太阳系附近恒星或演示性动画有用。

### 7.4 关键取舍

| 需求 | 推荐方案 |
|---|---|
| 只画今天的星座连线 | J2000 坐标 + index.json + Pogson 半径 |
| 要随时间转天 | 加地方恒星时旋转 + 观察者纬度 |
| 要显示几千年前 | 加岁差矩阵 $P$（Vondrak 四参数） |
| 要暗星到 10 等 | 直接 mmap Stellarium 的 `.cat` 文件，复用 Star1/2/3 二进制布局 |

---

## 8. 源码引用索引（≥10 处）

1. 星表历元常量：`src/core/modules/Star.hpp:34` — `#define STAR_CATALOG_JDEPOCH 2457389.0`
2. Star1 48 字节记录布局：`src/core/modules/Star.hpp:452-530`
3. Star2 32 字节记录布局：`src/core/modules/Star.hpp:532-579`
4. Star3 16 字节记录布局与星等解码：`src/core/modules/Star.hpp:581-628`（尤其 614 行）
5. .cat 文件头读取与魔数判断：`src/core/modules/ZoneArray.cpp:110-187`
6. 文件魔数与 HIP 总数：`src/core/modules/ZoneArray.hpp:55-59`
7. 星表分级加载循环：`src/core/modules/StarMgr.cpp:579-601`
8. 黄赤交角硬编码矩阵：`src/core/StelCore.cpp:58`
9. IAU 2006 岁差矩阵组装：`src/core/modules/Planet.cpp:3120-3131`
10. IAU 2000B 章动角计算声明：`src/core/planetsephems/precession.h:61-69`
11. 星座线 JSON 解析与 HIP 反查：`src/core/modules/Asterism.cpp:140, 210-213`
12. 星座大圆弧绘制：`src/core/modules/Asterism.cpp:379-393`
13. 参考线坐标架绑定（赤道/黄道/地平）：`src/core/modules/GridLinesMgr.cpp:696-737`
14. Pogson 星等到对数亮度：`src/core/StelSkyDrawer.cpp:381-383`
15. B-V 颜色表：`src/core/StelSkyDrawer.cpp:808`
16. 线性自行传播（2D）：`src/core/modules/Star.hpp:171-174`
17. 完整 6 维空间运动模型：`src/core/modules/Star.hpp:149-170`
18. 星座艺术图锚点格式：`skycultures/modern/index.json:477-485`
19. 星等分区配置：`stars/hip_gaia3/defaultStarsConfig.json`（`magRange` 数组）
20. 旋转矩阵定义约定：`src/core/VecMath.hpp:1417-1450`

---

## 9. 一句话总结

Stellarium 的天球 = **按三角区分块的紧凑二进制星表（J2015.5 历元）** + **单位球矢量** + **一串 4×4 旋转矩阵（自行→岁差→章动→坐标架→观察者→投影）**；星座线就是 JSON 里一串 HIP 号连成的大圆弧；黄道就是绕春分点轴旋转 $\varepsilon_0=23.43928°$ 得到的大圆；星等按 Pogson 律 $L\propto 10^{-0.4m}$ 映射到屏幕半径。
