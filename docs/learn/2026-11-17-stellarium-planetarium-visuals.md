# Stellarium 星象仪视觉机制对照与最小改动建议

- 日期：2026-11-17
- 参考实现：`d:\mbdsdr\reference\stellarium`，commit `e84ca6d`（2026-10-25）
- 本仓库：`f12187b4`，对照文件 `desktop/rf_sky_view.py`
- 许可：Stellarium 为 GPL。本文只记录**机制、公式与常量**，不复制其代码/纹理；所有落地建议都用我们自己的 QPainter 代码重写。
- 结论先行：真正拉开"星象仪/普通星空"差距的是**地平附近**，不是天顶。按性价比排序的三条改动见 §3：① 天空渐变锚定地平（修根因，~30 行）；② 近地平雾带（观感增量最大，~55 行）；③ 星点随 FOV 放大（~20 行）。

---

## §1 Stellarium 机制摘要

### 1.1 地平线 / 地景（landscape）

| 机制 | 实现位置 | 关键参数 / 事实 |
|---|---|---|
| 默认地景 | `src/core/modules/LandscapeMgr.cpp:380`；`landscapes/guereins/landscape.ini:5-33` | 列表第一项 `guereins` 即默认（`default_cfg.ini.cmake:308` 也写死 `landscape_name = guereins`）。画法是 `old_style`：8 面柱面全景贴图 `tex0..tex7` + 地面贴图 `guereinsb.png` + 装饰山脊 `decor_alt_angle=40°`，不是一次性全景球 |
| 地景开关 | `src/core/modules/LandscapeMgr.cpp:861`、`1112-1126` | `landscape/flag_landscape`（兼容旧键 `flag_ground`）默认 `true`；`setFlagLandscape(false)` 时整层不画，天空露出 |
| 地平线以下遮挡 | `src/core/modules/Landscape.cpp:873-877`（ground 着色器） | 屏幕全屏四边形逐像素反投影，`viewDir.z > 0`（地平以上）直接 `discard`；所以"地面"是一块严格以 alt=0 为边界的遮挡体，不是贴图边框 |
| 地面亮度 | `src/core/modules/Landscape.cpp:1219`、`1234-1238` | `vshift = sin(ground_angle_shift)`；绘制亮度 = `landscapeBrightness * landscapeTint`，透明度 = `(1-landscapeTransparency)*landFader`，默认 `transparency=0`（`LandscapeMgr.cpp:872-873`） |
| 雾带几何 | `src/core/modules/Landscape.cpp:1066-1105`；`landscapes/guereins/landscape.ini:35-36` | 从 `fog_angle_shift = -3°` 起向上 `fog_alt_angle = 20°` 的柱面带，实际覆盖 **-3°..约 +17°**（`calibrated` 时高度 `tan(17°)-tan(-3°) ≈ 0.358`；`guereins` 未启用 calibrated，shader 用 `sin` 近似，顶部落到约 +16.2°，见 `1080-1090` 与 `887-905`） |
| 雾带着色 | `src/core/modules/Landscape.cpp:881-905`（fog 着色器） | 方位归一化 `s = az/2π`，高度 `t = (tanAlt - vshift)/fogCylinderHeight`，`t<0` 或 `t>1` 丢弃；颜色/透明度全部来自 `fog.png` 的纵向渐变 |
| 雾带混合与亮度 | `src/core/modules/Landscape.cpp:1086`、`1096` | **加法混合 `GL_ONE, GL_ONE`**；`brightness = landFader * fogFader * (0.1 + 0.1*landscapeBrightness)`，即满亮也只有 0.1~0.2，很克制 |
| 绘制顺序 | `src/core/modules/Landscape.cpp:1040-1046` | ground/decor → **fog** → 自发光层；雾画在地面之后，所以会把剪影底边"提亮"，不是被剪影盖住 |
| 默认 ini 缺省 | `src/core/modules/Landscape.cpp:539-540` | 没有 fog 配置时 `fogAltAngle=0`、`fogAngleShift=0`；即"雾带"默认高度为 0，只有地景 ini 显式给了才出现 |
| 地景地平线 | `src/core/modules/Landscape.cpp:323-341`；`2224-2225` | 只画 `horizonPolygon` 的边界；`guereins` 是 `old_style` 且无 horizon 多边形 → **默认没有地景地平线**。网格模块的 `viewing/flag_horizon_line` 默认也是 `false`（`default_cfg.ini.cmake:256`、`GridLinesMgr.cpp:1949`） |
| 夜间地景亮度 | `src/core/modules/LandscapeMgr.cpp:369-370`、`839-840`；`default_cfg.ini.cmake:228-229` | 机制存在（`minimal_brightness=0.10`），但 `flag_minimal_brightness=false`，默认不启用；夜里靠大气光照（太阳 > -8° 才亮）和别墅灯光贴图 |

**结论**：Stellarium 的"地平线感"= 严格的几何遮挡（ground quad）+ 约 20° 高（-3°..+17°）、加法混合、亮度 0.1~0.2 的雾带。地景本身是纹理全景，我们无法照搬，但**雾带的几何与混合方式与纹理无关**，是最值得抄的部分。

### 1.2 大气 / 天空渐变（Atmosphere / StelSkyDrawer）

| 机制 | 实现位置 | 关键参数 / 事实 |
|---|---|---|
| 默认大气模型 | `src/core/modules/LandscapeMgr.cpp:63-69`、`737-749` | `landscape/atmosphere_model` 缺省 = `lightweight`；`preetham` / `showmysky` 是备选 |
| 天空绘制时机 | `src/core/modules/LandscapeMgr.cpp:683-684` | 先画大气，再画地景；即天空是独立一层，不是窗口背景色 |
| 大气输入 | `src/core/modules/LandscapeMgr.cpp:535-539`、`801` | 温度 15 °C、相对湿度 40%、当前消光系数；大气层淡入淡出 `0.5 s` |
| 高度方向拉伸 | `src/core/modules/AtmosphereLightweight.cpp:363-368` | 纹理坐标 `elevTC = sqrt(|elev|/(π/2))`：**地平附近分配更多采样精度**，因为亮度变化几乎都集中在那里 |
| 白天地平亮度 | `src/core/modules/AtmospherePreetham.cpp:345-359`、`AtmosphereLightweight.cpp:366` | **没有单独的"地平亮度"常量**：由太阳高度、浑浊度 5、消光 0.13 共同决定；地平方向 airmass≈31.7×天顶，散射路径长所以更亮。可直接借鉴的是"以仰角/airmass 为权重"的形状，而不是某个 RGB 值 |
| 加法混合 | `src/core/modules/AtmosphereLightweight.cpp:767`；`AtmospherePreetham.cpp:405` | 大气也是 `ONE, ONE`，叠在星空/背景上（所以地平越亮，低空星越淡） |
| 平均亮度 / 夜天底 | `src/core/modules/AtmosphereLightweight.cpp:712-713`；`AtmospherePreetham.cpp:364`、`370` | 天空亮度 = 模型值 + `0.0001`（假定星背景）+ `lightPollutionLuminance`。注意后两项是**均匀项**，夜间天顶-地平差主要来自大气模型本身 + 雾带，不是简单线性渐变 |
| 光害默认 | `src/core/StelSkyDrawer.cpp:108`；`default_cfg.ini.cmake:88` | `init_light_pollution_luminance` 缺省取 Bortle 3；`init_bortle_scale=2` |
| 浑浊度 | `src/core/StelSkyDrawer.cpp:127`；`AtmospherePreetham.cpp:304-307` | `turbidity` 默认 5，喂给模型前夹到 `[2, 16]` |
| 消光系数 | `src/core/StelSkyDrawer.cpp:111` | `landscape/atmospheric_extinction_coefficient` 默认 **0.13 mag/airmass** |
| 大气质量公式 | `src/core/RefractionExtinction.cpp:35-61` | 地平以下 `cosZ < -0.035`（约 -2°）按模式处理；`apparent_z` 走 Rozenberg `1/(cosZ+0.025·e^(-11cosZ))`，几何高度走 Young 1994：`nom/denum` 三次多项式。地平 airmass≈31.7，天顶=1.0 |
| 消光落点 | `src/core/RefractionExtinction.cpp:95-98`；`StelSkyDrawer.cpp:383` | `extinctionMagnitude = airmass(altAz.z) * ext_coeff`，加进有效星等；亮度换算统一走 `-0.92103·(mag+12.12331)+lnfovFactor`（`0.92103 = ln10/2.5`） |
| 地平以下消光策略 | `src/core/RefractionExtinction.hpp:44-53`、`RefractionExtinction.cpp:35-48`；`src/core/StelSkyDrawer.cpp:113-118`；`default_cfg.ini.cmake:284` | 三种模式：`zero`（0）、`mirror`（镜像到地平以上）、`max`（返回 42）；代码缺省 `zero`，发布配置显式设 `mirror` |
| 夜间天顶-地平亮度差 | 见上 | Stellarium 夜间并不是"屏幕从上到下变亮"；地平亮感来自大气散射模型 + 独立雾带，天顶处仍保留星背景 + 均匀光害 |

### 1.3 恒星渲染

| 机制 | 实现位置 | 关键参数 / 公式 |
|---|---|---|
| 屏幕换算常数 | `src/core/StelSkyDrawer.cpp:46-49` | `EYE_RESOLUTION = 0.25`，`MAX_LINEAR_RADIUS = 8 px` |
| FOV 因子 | `src/core/StelSkyDrawer.cpp:298-304` | `powFactor = (60/max(0.7,fov))^0.8`；`lnfovFactor = ln((1/50·2025000·3600)/(fov²·EYE_RES²·powFactor·1.4))` → 亮度随 `fov^-1.2` 增长 |
| 半径总缩放 | `src/core/StelSkyDrawer.cpp:307` | `starLinearScale = (35·2·absScale)^(1.40·0.5·relScale)`，默认 `19.569`（`:69`） |
| 星等→亮度 | `src/core/StelSkyDrawer.cpp:383` | `lnLum = -0.92103·(mag + 12.12331) + lnfovFactor` |
| 星等→半径 | `src/core/StelSkyDrawer.cpp:404-439` | `radius = adaptLuminanceScaledLn(lnLum, relScale·0.70) · starLinearScale`；`radius<0.3` 不可见；`radius<1.2` 时固定 1.2 px、亮度补 `r³/1.728`；`radius>8` 后 `8+sqrt(1+r-8)-1` 压缩 |
| 等效观感 | 由上两条推导 | 半径大致 ∝ `fov^-0.84`：从 60° 拉到 10°，亮星半径约 ×4.7（直到 8 px 上限）。**这是"放大后星星会变大"的来源** |
| 闪烁 | `src/core/StelSkyDrawer.cpp:92-93`、`504`；`src/core/modules/ZoneArray.cpp:547-559`；`default_cfg.ini.cmake:68-69` | 默认开，`star_twinkle_amount = 0.2`；每帧随机 `tw = 1 - twinkleFactor·amount·rand`，乘在亮度上；`twinkleFactor = min(1, 1-0.9·sinAlt)`，天顶保留 0.1、地平最强 |
| 亮星大光晕 | `src/core/StelSkyDrawer.cpp:508-518` | 半径超过约 `(8+5)px × screenScale` 时额外画 150 px 精灵贴图；这是纹理，跳过 |
| 极限星等 | `src/core/StelSkyDrawer.cpp:97`；`default_cfg.ini.cmake:74` | FOV 适配上限默认 90°，用于压缩星等映射 |

### 1.4 银河（MilkyWay）

| 机制 | 实现位置 | 关键参数 / 事实 |
|---|---|---|
| 是否默认显示 | `src/core/modules/MilkyWay.cpp:84`；`default_cfg.ini.cmake:288-290` | 发布配置 `flag_milky_way = true`；代码无配置时 `toBool()` 为 false。**默认是显示的** |
| 数据依赖 | `src/core/modules/MilkyWay.cpp:80` | `textures/milkyway.png`，全屏反投影采样，无该纹理即无银河 |
| 亮度模型 | `src/core/modules/MilkyWay.cpp:331-343` | 面亮度 `12 + 0.15·bortleIntensity` mag/arcmin²；`aLum = min(0.38, adapt(lum)·2)`；再乘 `intensity`（默认 1） |
| 大气压制 | `src/core/modules/MilkyWay.cpp:353-366` | `atmFactor >= 0.35`，最后 `c *= atmFactor²`；晨光/月光会显著压暗银河 |
| 消光 | `src/core/modules/MilkyWay.cpp:374-376`、`276` | `extinctionFactor = 0.3^airmass · (1.1 - 0.1·bortle)`（刻意用 0.3 而非真实 0.4，"looks more realistic"） |
| FOV 淡出 | `src/core/modules/MilkyWay.cpp:143-148`、`55-60` | `intensityFovScale = clamp((fov-0.25)/(2.5-0.25), 0, 1)`：**FOV≤0.25° 不可见，FOV≥2.5° 全显**（即只在极度拉近时淡出，宽视场不受影响） |
| 混合 | `src/core/modules/MilkyWay.cpp:391-392` | 加法 `ONE, ONE` |

### 1.5 地平线以下（地面）与"关闭地景"

- 遮挡：ground 全屏 quad，`viewDir.z > 0` 丢弃（`Landscape.cpp:873-877`），因此地平以下的天体被真实遮住；天体本身仍参与绘制，不靠"alt<0 不画"。
- 消光：地平以下用 `undergroundExtinctionMode`，发布配置为 `mirror`（`default_cfg.ini.cmake:284`），代码缺省 `zero`（`StelSkyDrawer.cpp:113-118`）。
- 关闭地景：`LandscapeMgr::setFlagLandscape`（`LandscapeMgr.cpp:1112-1126`）只关地景层；大气仍画，天空在地平以下由大气模型镜像填充（`AtmospherePreetham.cpp:338-344`）。
- 我们的行为：恒星在 `alt<0` 截断（`rf_sky_view.py:4949-4951`），地景多边形从地平填到屏幕底（`rf_sky_view.py:5877-5894`）；关掉地景后地平以下就是背景渐变。功能等价，无需改。

---

## §2 我们已有什么 / 缺什么

| 项 | Stellarium | 我们（file:line） | 差距 |
|---|---|---|---|
| 天空渐变锚点 | 按地平坐标计算，随视线移动 | `_sky_gradient` 逐 stop 把 `pos` 反算成仰角 `alt = 90*(1-pos)`，用 `_sky_to_screen_finite` 投到屏幕 y 后再建 `QLinearGradient`（`rf_sky_view.py:4684-4710`；投影失败或 y 跨度 < 8px 时回退铺满整屏） | 与 Stellarium 同口径；§3.1 提案已完成 |
| 近地平雾带 | `-3°..+17°` 柱面、加法、亮度 0.1~0.2（`Landscape.cpp:1066-1105`） | `_draw_horizon_haze`：0–18° 六条等仰角带、加法混合、逐段四边形（四角都在屏内才画），受 `_show_atmosphere` 管（`rf_sky_view.py:5948-6030`） | §3.2 提案已完成；与 Stellarium 的差异只剩「不向 -3° 延伸」（地面本来就盖住，无收益） |
| 地景遮挡 | ground quad 严格 z>0 丢弃 | 三层噪声剪影从地平填到屏底（`5844-5894`），`_show_landscape` 关层 | 等价；我们的是装饰性剪影（`_LANDSCAPE_LAYERS`，`1763-1767`），不参与拾取，可接受 |
| 地景 FOV 淡出 | 不按 FOV 淡出，只按透明度设置 | `_landscape_alpha` 在 `fov<20°` 淡出，`fov≈1°` 完全消失（`1820-1831`） | 差异：紧 FOV 时没有地面。为避免粗噪声糊成黑块，暂保留；有雾带后可再讨论 |
| 地平线 | 默认不画地平线（`default_cfg:256`；old_style 无 horizon polygon） | 永远画一条 1px 灰线，夜间 alpha=58（`5765-5792`） | 我们多了一条硬线；夜间它会把星空横向割开。可作为雾带的配套降级项 |
| 消光 | Young 1994 airmass × k=0.13，地平 airmass≈31.7 | `Δm = min(3.5, k/sin alt)`，`k=0.2`，alt 下限 0.5°（`mbdsdr_ai/skyengine/stars.py:231-248`） | 量级一致；10° 处我们 1.15 mag、Stellarium 0.72 mag，略偏暗。既有测试锚定 k=0.2，本轮不建议动 |
| 星点大小 | `computeRCMag` 含 `lnfovFactor`，半径 ∝ `fov^-0.84`，8px 上限 | `_star_visual` / `_star_visual_many` 乘 `_star_fov_gain(fov)`（`rf_sky_view.py:4883-4894`：参考 60° 时 1.0、指数 0.45、上限 2.4×）；`star_draw` 本身仍只吃星等（`stars.py:197-228`） | 已有 FOV 项，但比 Stellarium 的 0.84 收窄且封顶 2.4×——刻意保守：离屏 A/B（15° 视场）1.87× vs 2.4× 观感差 < 1px，再放大只会让亮星辉光糊成团 |
| 小星补偿/压缩 | `<1.2px` 补 `r³/1.728`、`>8px` 开方压缩 | 同款（`stars.py:189-190`、`219-223`） | 已对齐 |
| 亮星辉光 | 150px 精灵贴图 | `QRadialGradient`，半径 3.6r，alpha 120/64/26（`4965-4980`） | 无纹理可用，现有近似可接受 |
| 闪烁 | 默认开，amount 0.2，随仰角抑制 | 无 | 缺；但做了会破坏像素稳定测试，见 §4 |
| 银河 | 默认显示，纹理 + 消光 + FOV 淡出 | 没有数据，图层键里明确不提供（`3236-3240`） | 需要纹理资源，本轮明确不做 |
| 大气模型 | 物理模型（lightweight/preetham），加法叠在星空上 | 6 段经验暮光表（`174-202`）+ 太阳方位暖辉（`4685-4721`） | 经验模型可以接受；缺的是"近地平"这一维 |
| 图层开关 | atmosphere 同时管雾、闪烁、大气 | `_show_atmosphere` 管太阳暖辉（`4600`）与雾带（`5959`，首行 `if not self._show_atmosphere: return`）；背景渐变仍恒画 | 雾/辉光已归位；背景渐变不受该开关管的差异保留（关大气不等于关天光） |

**两个可量化的偏差（可直接写进验收）**

1. 星象仪入口（`alt=25, fov=90`）真实地平在 73% 高度，但渐变把 `pos=1.0` 的地平色放在 100% 高度（真实 alt≈-20°）；所以**画面里那条地平线拿到的是 ~0.73 处的颜色**，比色表暗一档。IDE 默认 `alt=90, fov=120` 时更极端：底边约 alt 30°，整片天空用了上半段色阶。
2. 在 `_draw_landscape` 里，`horizon_y` 上方 0.14×窗口高是唯一的近地平增亮，且只在夜间、只在地景剪影后面；`_show_landscape=False` 或白天时，近地平没有任何独立增亮。

> **复核（2026-11-17 晚，对照工作区代码）**：上面两条是**改动前**的实测口径，保留作对照。
> 现状：① 渐变按仰角锚定（`_sky_gradient`，`rf_sky_view.py:4684`）——色表 `pos=1.0` 落在真实地平；
> ② 近地平增亮由 `_draw_horizon_haze`（`:5948`）独立提供，与地景开关、昼夜都解耦。
> 验收判据见 `tests/test_rf_sky_view_real.py`（地平 stops 命中 / 雾带只在地平上）与
> `docs/learn/2026-11-17-sky-zoom-clutter-audit.md`（中心墨迹）。

---

## §3 三条按性价比排序的最小改动

统一前提：不引入新数据文件、不引入新依赖；只用 `QPainter` / `QLinearGradient` / `QPainterPath`，复用现有 `_sky_to_screen_finite`、`_sky_to_screen_many`、`_screen_points_many`、`_twilight_stops`、`tokens()`。

> **执行状态（2026-11-17 晚复核）**：3.1 / 3.2 / 3.3 均已进代码（位置见 §2 表）；
> 3.3 的指数按实测收窄到 0.45、封顶 2.4×（理由见该行）。下面保留原提案文本，作为改动依据与验收口径。

### 3.1 天空渐变锚定地平（改 `_paint_sky_background`，约 30 行）

**目标**：把"渐变位置"从窗口高度改成真实仰角。看天顶、看地平、缩放，同一仰角永远是同一种天光色。

**改法**（`rf_sky_view.py:4671-4680`）：

1. 取当前视轴方位 `az = self.view_state.center_az`。
2. 遍历 `_twilight_stops(self._sky_brightness)` 的 6 个 `(pos, color)`；`pos` 的语义是"天顶 0 → 地平 1"，对应仰角 `alt = 90*(1-pos)`。
3. 对每个 alt 调 `self._sky_to_screen_finite(az, alt)`；失败（天顶在相机背后/投影非有限）就跳过该 stop，保留端点色由 Qt 自然延展。
4. 收集 `(y, color)`，按 y 升序排列；若少于 2 个或 y 跨度 < 8px，**原样回退**到旧的 `QLinearGradient(0,0,0,self.height())`（保证极端视角不崩）。
5. 否则 `grad = QLinearGradient(0, y_first, 0, y_last)`，第 i 个 stop 的位置 `(y_i - y_first)/(y_last - y_first)`，`drawRect(self.rect())` 不变。

这样：星象仪入口 `alt=25, fov=90` 时，`pos=1.0` 的 `#2E4254/#DCE7EC` 精确落在约 73% 高度的真实地平线上；IDE 默认 `alt=90, fov=120` 时，屏幕中心是 alt 90（天顶色），屏幕底边是 alt≈30（30° 的天光色），horizon 色被正确推到屏幕外，不再"提前提亮"。高于投影上边界的天顶段由 Qt 用最上端 stop 的颜色延展；在 `fov=90, alt=25` 下这段最多差一个 stop（顶边 alt≈70，却延展 alt≈58.5 的颜色），肉眼几乎不可辨。

**注意**：全天模式（stereographic、`center_alt` 较高）下，等仰角圈不是以屏幕中心为圆心的圆，竖直锚定仍只是近似；如果要把全天模式也做对，可把同一份 `(alt,color)` 表改成 8 条 0–90° 的**等仰角多边形带**逐条填充（用 `_screen_points_many`，投影无关），成本再加约 25 行、每帧约 8×61 次投影。先做竖直锚定即可。

**离屏验证**（沿用 `tests/test_sky_landscape_layers.py:76-101` 的 `grab()` 口径）：

- 构造 `RFSkyView()`，`resize(800,600)`，`set_observer(40,116,0)`，`_sky_brightness=0.0/1.0`。
- 主用例：`view.enter_horizon_view()`（alt 25 / fov 90，星象仪真实构图）；对照用例：`view_state.look_at(0, 90)` + `view_state.fov_deg = 120`（IDE 默认）。
- 用 `_sky_to_screen_finite(view_state.center_az, 0)` 求 `horizon_y`；断言 `horizon_y` 处的像素与 `_twilight_stops(b)[-1]` 的通道差 ≤ 8（锚定后应精确命中该 stop）。改代码前先存一张同视角基线图，改后断言该点亮度比基线高（预期约一档，夜/昼都成立）。
- 再断言 `horizon_y` 上下各采样点随"更接近地平"单调变亮；IDE 默认视角下，`img.pixelColor(w//2, h-4)` 的亮度仍 < `_twilight_stops(b)[-1]` 且 > 顶部（保住 `tests/test_rf_sky_view_real.py:269-278` 与 `tests/test_main_window.py:390-400` 的既有判据）。

**风险**：低。最坏情况是某个 stop 投影失败导致色阶少一段；已用"<2 个则回退"兜底。性能是每帧 6 次标量投影，可忽略。潜在测试影响：只改背景像素，`test_atmosphere_glow` 直接画在已知暗底上、不读背景；`test_main_window` 的地景分支只看"中段亮于地面"，不受影响。

### 3.2 近地平雾带（新增 `_draw_horizon_haze`，约 55 行；替换 5819-5842 的旧夜光带）

**目标**：补上 Stellarium 的 fog —— 从地平向上到约 18° 的一圈柔和亮带，白天偏白、晨昏偏暖、深夜保留很弱的暖灰光害；顺带把贴近地平线的星点"洗淡"，消除"星星锐到地平线再整齐消失"的塑料感。Stellarium 的 fog 会延伸到 -3°（贴住地面），我们只画 alt≥0：地面本来就会盖住，向下延伸没有收益。

**改法**：

1. 新方法 `_draw_horizon_haze(self, painter)`，`if not self._show_atmosphere: return`。
2. 仰角带用 6 条，覆盖 `[(0,2),(2,4),(4,6),(6,9),(9,13),(13,18)]`；每条用 `_screen_points_many(az_seq, alt)` 取上下两条边（az 0..360 步长 6°，共 61 点）。**不要把所有点塞进一个 QPainterPath**：透视下相机背后的方位会返回巨大有限值或 NaN（`_screen_points_many` 不做钳位，钳位只在 `_sky_to_screen_finite`），整圈路径会斜穿画面。沿用 `_draw_landscape` 的 `_on_screen` 过滤思路（`5880-5893`），但改成逐段四边形：对相邻两列方位 i、i+1，四个角 `(bottom_i, bottom_{i+1}, top_{i+1}, top_i)` 全部 `self._on_screen(p)` 才 `drawPolygon`，否则跳过该段。全天模式整圈都在屏内，自然连成完整圆环；地平视角只画可见弧段。360 个四边形/帧，成本可忽略。
3. 颜色：`stops = _twilight_stops(self._sky_brightness)`，取 `horizon = QColor(stops[-1][1])`；`night = QColor(tokens().COLORS["sky_night_glow"])`（`#6B5B48`，现有夜光带同源）。最终色 `mix(horizon.lighter(106), night.lighter(112), 0.55*(1-b))`，`b = self._sky_brightness`。
4. 透明度：先算 `twi = smoothstep(-18,-2,sun_alt)`（复用 `5819-5830` 的写法），`peak = 12 + 60*twi`，每条 alpha = `int(peak * fac)`，`fac = (0.55, 0.42, 0.30, 0.20, 0.12, 0.06)`。白天 `twi=1`，最强一条 alpha≈40，近地平加法提亮；深夜只剩 alpha≈7 的暖灰底光——这是刻意保留的"光害"下限。注意 `5819-5822` 的既有注释警告过"固定 alpha=170 的灰带=灰墙"，所以下限必须这么低；实测若仍像灰墙，直接把 `12` 改成 `0`，只保留暮光/白天的带。
5. 混合：`CompositionMode_Plus`（与 Stellarium `ONE,ONE` 一致，也与 `_draw_atmosphere_glow` 一致）。
6. 顺序：`paintEvent`（`4588-4605`）保持"地平线在地景之前"的既有契约（`5766-5769`：山脊可盖住基准线），只在 `4605` 之后、网格（`4607`）之前插入雾带：background → stars/labels/dso → atmosphere glow → `_draw_horizon_line` → `_draw_landscape` → **`_draw_horizon_haze`** → 网格。Stellarium 的雾在 ground 之后（`Landscape.cpp:1040-1046`），会把剪影底边提亮，所以雾带必须在 `_draw_landscape` 之后；放在网格之前，避免把网格线一起洗淡。
7. 删除 `_draw_landscape` 里 `5819-5842` 的旧夜光带（它画在剪影之前、高度按像素、只夜间），避免双份地平辉光。

**配套一行（可选，但建议同批做）**：`_draw_horizon_line` 的夜间 alpha 从 `58`（`rf_sky_view.py:5775`）降到 ~28，或直接跟 `_show_grid` 联动。Stellarium 默认根本不画地平线（`default_cfg:256`），我们保留几何基准即可，不必让它抢戏。

**离屏验证**：

- `b=1.0` 与 `b=0.0` 各渲染一次，`view.enter_horizon_view()`（fov 90）。
- 用 `_sky_to_screen_finite(center_az, 0)` 找 `horizon_y`，比较 `pixelColor(cx, int(horizon_y)-2)` 与 `pixelColor(cx, int(horizon_y)-int(0.18*h))`：两次都应"越靠近地平越亮"，亮度差 > 4。
- 固定同一点 `int(horizon_y)-2`，对比 `_show_atmosphere` 开/关：开启时应比关闭亮 > 4（证明增量来自雾带，而不是背景渐变本身）。
- 再验证没有把地景整体洗白：`pixelColor(cx, int(horizon_y) + 40)` 与渲染前的同点颜色差 ≤ 6（雾带主要在 0° 以上；如果实现成上下对称，白天会把地面提亮，需要收窄到 alt>0）。

**风险**：中低。主要风险是白天近地平加法饱和成纯白、以及 6 条带出现色阶。前者用 `peak=12+60*twi` 起步、实测后调；后者把条数加到 10~12 即可（仍是线性成本）。全天模式下多边形带天然跟随等仰角圈，比任何径向/线性渐变都正确，这是本方案相对"画一条渐变矩形"的优势。

**落地记录（2026-11-17）**：

- `desktop/rf_sky_view.py` 新增 `RFSkyView._sky_gradient()`，`_paint_sky_background` 改为
  `QBrush(self._sky_gradient(_twilight_stops(self._sky_brightness)))`。逐 stop 用
  `_sky_to_screen_finite(center_az, 90*(1-pos))` 投到屏幕 y：视轴背面/超屏的 stop
  由单点投影的 4×最小边钳位丢弃，Qt 用端点色延展；有效点 < 2 或纵向跨度 < 8px
  时回退到旧的 `0→height` 全屏渐变（极端视角不崩）。
- 新增 `RFSkyView._draw_horizon_haze()`，6 条等仰角带 `0–2/2–4/4–6/6–9/9–13/13–18°`，
  系数 `0.55/0.42/0.30/0.20/0.12/0.06`，`CompositionMode_Plus`。峰值
  `10 + 62*max(twi, 0.55*b)`：没有太阳数据时白天也按亮度给雾。逐段四边形，
  四角都过 `_on_screen` 才画（相机背后的 NaN 不会把整圈连成斜穿画面的路径）。
  调用点在 `paintEvent` 的地景之后、网格之前。
- `_draw_landscape` 里旧夜光带（像素高度、固定 alpha 上限 170、只在剪影之前）
  整块删除，只留一行注释指向新方法。
- 地平线描边 alpha 暂不动（58/110）：它现在是几何基准，雾带画在它上方，不冲突。

**离屏实测**（800×600，`set_observer(40,116)` + `enter_horizon_view()`，关闭网格/地景）：

| 场景 | 采样点 | 结果 |
| --- | --- | --- |
| b=0.0 地平色锚定 | `horizon_y-1` | 实得 (45,65,84) vs 期望 (46,66,84)，差 **1** |
| b=1.0 地平色锚定 | `horizon_y-1` | 实得 (219,230,236) vs 期望 (220,231,236)，差 **1** |
| b=0.0 梯度方向 | `horizon_y-2` vs `-120` | 亮度 61.1 > 41.6 |
| b=1.0 梯度方向 | 同上 | 亮度 227.0 > 187.4 |
| 雾带增量（夜） | `horizon_y-2` 开/关对比 | **+2.0** 亮度 |
| 雾带增量（昼） | 同上 | **+21.5** 亮度 |
| 地面不被洗白 | `horizon_y+60` 开/关对比 | Δ=**0** |

用例落在 `tests/test_rf_sky_view_real.py`：`TestSkyGradientAnchoredToHorizon`
（参数化 b=0/1 + 梯度方向）与 `TestHorizonHaze`（只在地平以上、随太阳高度
而非固定 alpha）。该文件整跑 **22 passed**。

### 3.3 星点随 FOV 放大（改 `_star_visual` / `_star_visual_many`，约 20 行）

**目标**：补上 Stellarium `lnfovFactor` 的观感结果——拉近后星点变大、亮星晕开；拉远（≥60°）保持现状，不改变已有宽视场截图。

**改法**：

1. 类常量：`STAR_FOV_REF_DEG = 60.0`、`STAR_FOV_GAIN_MAX = 2.4`、`STAR_FOV_GAIN_EXP = 0.45`。
2. 新静态方法 `_star_fov_gain(fov_deg)`：`min(2.4, max(1.0, (60/max(0.5,fov)) ** 0.45))`。参考值取 60° 是为了让既有的 90°/120° 构图逐像素不变；指数 0.45 也比 Stellarium 的等效 0.84 保守，避免我们 8px 上限/辉光模型下亮星过大。若要更接近 Stellarium 的 `maxAdaptFov=90` 手感，把参考值改成 90（则 fov=90 时 gain=1、fov=20 时 1.97、fov=5 时封顶 2.4），但会改变 `enter_horizon_view()` 的默认截图，需同步复核。
3. `_star_visual(..., fov_deg: Optional[float] = None)` 与 `_star_visual_many(..., fov_deg=None)`：`gain = 1.0 if fov_deg is None else self._star_fov_gain(fov_deg)`，把 4836 的 `STAR_RENDER_GAIN` 换成 `STAR_RENDER_GAIN * gain`（`r>0` 才乘；`r<=0` 不能因乘增益复活，沿用 4884-4885 的写法）。
4. 两个调用点必须同时改，保持"画得出就点得中"：`_draw_stars` 的 `4918-4919` 与拾取 `_find_body_at` 的 `7074-7075`，都传 `self.view_state.fov_deg`。
5. 默认参数 `None` 让所有只传星等的既有纯函数测试维持原值，不需要改测试。

**离屏验证**：

- 纯函数：`_star_visual(-1.46, 6.5, 3, fov_deg=60)[0] < _star_visual(-1.46, 6.5, 3, fov_deg=10)[0]`，且后者 ≤ 前者×2.4+ε；`fov_deg=None` 与 60° 结果一致。
- 一致性：把视线中心对准一颗已知亮星，`fov=5` 渲染后在"该星屏幕位置 + 1 个绘制半径内"调 `_find_body_at`，应命中；在"3 倍半径外"应不命中（拾取的 `limit = max(tolerance, r_pick)`，`7078`，所以两处必须同步吃同一个 gain，不能只改绘制）。

**风险**：低。影响半径、标签偏移（`pos.x()+r+2`，`4996`）和拾取容差；三者都来自同一份 `r`，同步变化即可。辉光半径 `3.6r` 在 2.4× 增益下最大约 21px，可接受。

**落地记录（2026-11-17）**：

- `desktop/rf_sky_view.py` 类常量 `STAR_FOV_REF_DEG=60.0` / `STAR_FOV_GAIN_MAX=2.4` /
  `STAR_FOV_GAIN_EXP=0.45` 与 `_star_fov_gain()` 类方法（`None`/非法值 → 1.0）。
- `_star_visual(..., fov_deg=None)` 与 `_star_visual_many(..., fov_deg=None)` 都乘同一份
  `gain`（`r>0` 才乘，隐藏星不因乘增益复活）。
- 两个调用点同时传 `view_state.fov_deg`：`_draw_stars` 的 `_star_visual_many` 与
  `_find_body_at` 的 `_star_visual`。
- 用例：`tests/test_planet_brightness_model.py::TestStarFovGain`
  （60/90/120/180° 恒为 1、10° > 2、0.5°/0° 封顶、`None`=1；
  120/30/5° 下标量版与向量版半径/alpha 逐点一致、5° > 120°）。
  该文件整跑 **14 passed / 4 xfailed**（K01–K04 未动）。
- 离屏出图（仓库外脚本，便于肉眼比对）：
  `d:\mbdsdr\output\sky_stellarium_2026-11-17\01_horizon_night.png`（深夜地平）、
  `02_horizon_twilight.png`（太阳 -5°）、`03_zoom_5deg.png`（5° 放大）。
  01/02 可见地平上方一条窄亮带、地面剪影未被洗白；03 中心除方位网格线外
  没有点簇，星点为正常 3–4px 圆点。

### 3.4 统一离屏验证基线

所有验证都在 `QT_QPA_PLATFORM=offscreen` 下做，不启动窗口：

```python
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = r"d:\mbdsdr\MBDSDR"
sys.path[:0] = [ROOT, os.path.join(ROOT, "desktop")]
from PySide6.QtWidgets import QApplication
from rf_sky_view import RFSkyView

app = QApplication.instance() or QApplication([])
v = RFSkyView(); v.resize(800, 600); v.set_observer(40.0, 116.0, 0.0)
v.enter_horizon_view()            # alt 25 / fov 90，与星象仪入口一致
v._sky_brightness = 1.0           # 0.0 = 夜，1.0 = 昼
img = v.grab().toImage()          # 与 tests/test_rf_sky_view_real.py:274 同口径
# img.pixelColor(x, y)
```

- 视图构造/像素采样沿用 `tests/test_sky_landscape_layers.py:76-101` 与 `tests/test_rf_sky_view_real.py:269-278`；
- 采样时固定 `_sky_brightness`、`_sun`、`view_state.look_at(...)`、`fov_deg`，不要读墙钟；
- 断言只锚定"相对关系"（越靠近地平越亮、开关前后差、FOV 单调），不要写死具体 RGB，避免色表微调就红。

---

## §4 明确不做（本轮）

1. **银河**：需要 `milkyway.png` 这类全天天图纹理（Stellarium `MilkyWay.cpp:80`），且我们图层键里已明确"无数据·不提供"（`rf_sky_view.py:3236-3240`）。不伪造"像银河的装饰"。
2. **纹理型地景/雾/大光晕**：`guereins*.png`、`fog.png`、`groundTex`、150px 星晕精灵（`Landscape.cpp:1102`、`StelSkyDrawer.cpp:508-518`）都是资源依赖，全部跳过；现有程序化剪影与径向渐变保留。
3. **大气物理模型 / ShowMySky / Preetham 网格**：需要数据文件与 GPU 管线（`AtmospherePreetham.cpp:161-223`、`LandscapeMgr.cpp:737-760`），不引入。继续用 6 段经验表 + 加法暖辉。
4. **逐波长消光/恒星变红**：需要光谱或分通道消光表；我们只有 B-V 着色，地平处"变红"会变成整片染色，不做。
5. **亮度自适应/色调映射**：Stellarium 的 `adaptLuminanceScaledLn` 是 HDR 管线的一部分（`StelSkyDrawer.cpp:406`），我们 8-bit 直出，不模拟。
6. **星点闪烁**：默认实现是每帧随机乘亮度（`StelSkyDrawer.cpp:504`），会让离屏像素断言不稳定、1 Hz 刷新下也像"故障闪烁"。如果确实想要"活的星空"，应另开需求，用低频（0.2~0.5 Hz）确定性正弦 + 仅亮星、仅地平附近，且必须有可关闭开关；本轮不做。
7. **折射对地景的抬升**：Stellarium 地景/地平用 `RefractionOff`（`Landscape.cpp:1078`、`1220`），我们的地平基准同样固定在 alt=0（`rf_sky_view.py:5765`），行为已一致，不做。

---

## 附：一句话结论

我们缺的不是"更多星星/更多网格"，而是**地平附近的光**：天空颜色没有跟着仰角走，且地平线上没有雾带。先把这两件事按 §3.1、§3.2 落地，再补 §3.3 的 FOV 星点缩放，画面就会从"贴了星空壁纸的圆盘"变成"站在地面上看天"。
