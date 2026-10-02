# Q3 学习笔记：Stellarium 天空渲染 / 交互

> 上游：[Stellarium](https://www.stellarium.org/)（GPLv2+）。本笔记只学机制，不照代码。
> **落地状态：学习完成，未落地（理由见 ④）。**

## ① 上游真实做法（file:line）

### 1.1 投影：StelProjector 体系
`repos/stellarium/src/core/StelProjector.cpp`：

- **模型视图变换**（:30-54, `Mat4dTransform`）：4×4 矩阵把天球方向向量从 AltAz 系变换到世界系；backward 用转置（正交矩阵性质）省求逆。
- **视场→像素**（:172）：`pixelPerRad = 0.5 * viewportDiameter / fovToViewScalingFactor(fov)`，FOV 决定缩放。
- **投影类**：`StelProjectorClasses.cpp` 实现多种投影——透视图（perspective）、等距方位（azimuthal equidistant）、立体投影、球面镜等。
- **反投影**：屏幕 (x,y) → 天球方向向量，用于点选/拖拽。

### 1.2 交互：StelMovementMgr
`repos/stellarium/src/core/StelMovementMgr.cpp`：

- **FOV 范围**（:171-173）：`minFov=0.001389°`（=5 角秒，望远镜级），`maxFov=360°`（全天球）。
- **滚轮缩放**（:537-582）：`zoomTo(aimFov * zoomFactor, duration)`，指数式缩放。
- **拖拽平移**（:343-399, `handleMouseMoves`）：把屏幕像素位移换算成 AltAz 角位移 `panView(deltaAz, deltaAlt)`。
- **Goto 动画**（:1119-1175, `updateVisionVector`）：自动移动时用插值系数 `move.coef += speed*dt`，在 start→aim 之间平滑过渡；支持锁定目标天体（卫星/行星）自动追踪。
- **Mount 模式**（:1134-1152）：AltAzimuthal / EquinoxEquatorial / Galactic / Supergalactic 四种天球坐标系切换。
- **速度随 FOV 缩放**（:1060）：`depl = keyMoveSpeed * dt * currentFov`——放大时平移变慢（符合直觉）。

### 1.3 渲染：StelSkyDrawer
`repos/stellarium/src/core/StelSkyDrawer.cpp`（1182 行）：
- 星等→点大小/亮度映射（Hip 星表）。
- 大气散射（`atmosphere/` 目录）。
- 天空图层叠加（`StelSkyImageTile`：月相、星座线、深空天体图）。
- 折射/消光修正（`RefractionExtinction.cpp`）。

## ② 我方现状（file:line）

| 能力 | 我方位置 | 现状 |
|---|---|---|
| 天球投影 | `mbdsdr_ai/celestial_geometry.py`（`AzimuthalEquidistantProjection`） | ✅ 等距方位投影（全天球） |
| 视角状态 | `mbdsdr_ai/sky_interaction.py:78-`（`ViewState`） | ✅ center_az/center_alt/fov_deg |
| 平移 | `sky_interaction.py` `panView(dAz, dAlt)` | ✅ 已有 |
| 滚轮缩放 | `sky_interaction.py` `handleMouseWheel` | ✅ FOV 夹取 30°~180° |
| Goto 动画 | `sky_interaction.py` `moveToAltAzi` | ✅ 已有 |
| 反投影点选 | `sky_interaction.py` `unProject` / `pick_object` | ✅ 已有 |
| 卫星追踪 | `mbdsdr_ai/sat_tracker.py`、`mbdsdr_ai/sgp4.cpp` | ✅ SGP4 轨道预报 +过境预测 |
| 多 Mount 模式 | 无 | ❌ 仅 AltAz，无赤道/银河坐标系切换 |
| 望远镜级 FOV | 无 | ❌ 限制 30°~180°（SDR 全天空图不需要望远镜级） |
| 大气散射/折射 | `mbdsdr_ai/atmosphere.py`（部分） | ⚠️ 有折射模型但未接天空图渲染 |
| GPU/OpenGL 渲染 | 无（桌面端用 Qt/PySide 2D 绘制） | ❌ 不做 GPU 渲染 |

## ③ 差距判定

Stellarium 是一个**全功能桌面星图**（望远镜导星、赤道仪控制、Hip 星表、大气渲染），复杂度以万行计。我方的 `sky_interaction.py` 是 **SDR 全天空射频视图**（30°~180° FOV，极坐标，画卫星位置和 RF 热点），定位不同：

1. **我们不需要望远镜级缩放**：SDR 天线指向通常 30°~180° 波束宽，不需要从 360° 一路缩到 5 角秒。
2. **我们不需要 GPU 星点渲染**：我们画的是卫星轨道/过境点，不是几千颗 HIP 星表星点。
3. **核心交互模型已一致**：pan/zoom/goto/unproject/pick 都有，且数学独立实现。
4. **缺的**：赤道/银河坐标系切换、平滑 goto 插值动画（我方是即时跳转）、大气消光——这些对 SDR 指向辅助是"锦上添花"而非必需。

## ④ 落地建议

**未落地理由**：
- 我方 `sky_interaction.py` 已覆盖 SDR 天空图所需的 pan/zoom/goto/点选交互。
- Stellarium 的核心复杂度在 GPU 星点渲染、大气散射、望远镜导星——这些与 MBDSDR 的"射频天空视图"定位不匹配。
- 真要落地也只能是"加赤道坐标切换"这种小特性，但当前桌面端和移动端 UI 都用 AltAz 极坐标，改坐标系需要联动 UI，成本大于收益。

**后续若需要**：
- 在 `ViewState` 加 `mount_frame` 枚举（AltAz/RA-Dec），`build_view_rotation` 按 frame 选旋转矩阵。
- Goto 动画：把 `moveToAltAzi` 改成 N 帧线性/缓动插值（当前是即时跳变）。
- 大气消光：已有 `atmosphere.py`，可在画卫星仰角时按仰角做亮度衰减。
