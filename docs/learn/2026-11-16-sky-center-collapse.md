# 星图"中心一堆火星 + 一根垂线带个点"——真身与修法（2026-11-16）

> 用户原话："为什么我一放大以后，它默认中间都是火星？总是有一根垂下来的线中间有个点，然后出现就火星很多。"
> 本文是这条反馈的机械定位记录。结论：**不是星点，也不是火星**，是"背向相机的点被投影兜到屏幕正中"。

## 1. 症状的几何本质

星图用的是透视投影（`sky_to_screen(az, alt, view_state, projection, size)`）：

- 与视轴夹角 < 90° 的天区 → 有限坐标；
- 夹角 = 90° → 无穷远；
- 夹角 > 90°（视轴背面）→ `inf / nan`。

旧实现 `RFSkyView._sky_to_screen()` 对非有限坐标统一"兜底到屏幕中心"：

```python
if not (math.isfinite(px) and math.isfinite(py)):
    return QPointF(w / 2.0, h / 2.0)   # ← 病灶
```

于是**所有视轴背面的对象都堆在同一个像素上**。视场越小（放大越狠），背向天区占比越大，
中心堆得越密。这就是"一放大，中间全是火星"。

只有 `_sky_to_screen_finite()` 会返回 `None`，调用方必须自己决定"不画"。
2026-11-16 之前的审计只覆盖了 `_draw_objects`（契约 3），其余六层仍在用旧函数。

## 2. 实测：谁在往中心堆

探针 `monkeypatch _sky_to_screen` 记录"返回中心"的调用栈（视轴朝南 alt=20、FOV=8°、
12 颗卫星 + 6 条轨迹 + 19 颗 GNSS + 天线）：

| 调用方 | 每帧堆到中心的点数 | 用户看到的 |
| --- | --- | --- |
| `_draw_trajectories` | 27 | 虚线从四处汇聚到中心一点（"垂下来的线"） |
| `_draw_gnss_satellites` | 5 | 中心一圈小圆环（"火星"） |
| `_draw_antenna_beam` | 2 | 中心一块退化灰面 |
| `_draw_planets` | 1 | 一颗"假行星"贴在正中 |
| `_draw_antenna_pointer` | 1 | 中心十字 + 圆点（"中间有个点"） |
| `_draw_celestial_bodies` 晨昏辉光 | 0~1 | 背向太阳时整屏假霞光 |

修后同一探针：**全部为 0**。

## 3. 修法（六处 + 一条统一口径）

统一口径：**覆盖层一律用 `_sky_to_screen_finite()`；返回 `None` 就不画。**
宁缺，不造假。

1. `_draw_trajectories`：逐点 finite 投影，`None` 处**断开**成独立段，绝不跨背向连线；
   新增 `_draw_traj_run()` 保留原端部淡出。
2. `_draw_gnss_satellites`：`None` → `continue`。
3. `_draw_antenna_beam`：四角任一 `None` → 整块不画（否则退化成中心灰块）。
4. `_draw_antenna_pointer`：`None` → 不画（这就是"中心十字 + 点"）。
5. `_draw_planets`：`None` → 跳过（否则 `_on_screen(中心)` 恒真，行星贴中心）。
6. `_draw_atmosphere_glow` / 星座连线 / 选中环：同样改 finite；选中环只跳过环，信息卡照出。

## 4. 回归守卫（tests/test_sky_offscreen_overlay.py）

新增 4 条反证型用例，全部离屏渲染后数"墨迹"：

- `test_trajectory_behind_camera_draws_nothing`（背向 0 墨迹 / 正面有墨迹）
- `test_gnss_behind_camera_draws_nothing`
- `test_antenna_behind_camera_draws_nothing`（指针与波束都算）
- `test_sun_glow_behind_camera_draws_nothing`

判据是"画布上有没有墨"，不是"函数返回什么"——这样即使以后换投影实现，
只要还会往中心造假点，测试就会红。

## 5. 对照图

- 修前：`docs/evidence/2026-11-16-sky-center-collapse/before-zoom-center-pile.png`
- 修后：`docs/evidence/2026-11-16-sky-center-collapse/after-zoom-center-clean.png`

同一站址、同一时刻、同一数据（12 卫星 / 8 轨迹 / 25 GNSS / 天线 alt55）、
视图朝南 alt=20、FOV=8°。修前可见虚线汇聚 + 中心标签堆叠 + 中心圆点；
修后中心干净，只剩正常的方位网格虚线。

## 6. 剩余的正常元素（不要再误判成 bug）

- **穿过中心的竖直虚线**：方位网格的 180° 子午线，属 `_draw_az_grid`，可在视图菜单关网格。
- **地平圈附近聚集的星点**：大气消光/折射带，属真实星空。
- **视野边缘被裁断的星座连线**：`_clip_segment_to_rect` 的正常结果。

## 7. 参考实现（只学机制，不抄代码）

- Stellarium `src/core/StelSkyDrawer.cpp`：点光源先做屏幕裁剪再进顶点缓冲，
  背向对象不会进入绘制队列——与本仓"finite 判定后再画"是同一思路。
- Stellarium `SolarSystem.cpp` 的 marker 可见性同样先判"是否在视场内"。
- 许可证提醒：Stellarium 为 GPL-2.0，仅可参考机制，不得复制代码/数据进发布物。
