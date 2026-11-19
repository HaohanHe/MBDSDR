# 本机 QGIS 源码学习（只读调研）

> **本文为当时状态快照**：记录当日工作状态与结论，未逐条复核；
> 不作现状依据。共 5 条未复核；需要现状请查 `docs/COORDINATION.md`、
> `docs/known-red-register.md` 与 checklist。

- 调研日期：2026-11-17
- 项目根：`d:\mbdsdr\MBDSDR`
- 约束：只读。未修改任何源码/测试，未运行 pytest，未启动 GUI，未安装任何东西，未联网。
- 行号说明：QGIS 行号对应本机这份源码快照；项目文件行号在调研期间可能被其它任务改动而漂移，定位请以**函数名 + 引用的代码片段**为准。

---

## 1. 本机 QGIS 实况

### 1.1 `C:\Program Files\QGIS 4.0.3` 是空壳，不能运行

`C:\Program Files\QGIS 4.0.3` 下**只有 3 个文件**（命令：`Get-ChildItem ... -Recurse -File`）：

| 文件 | 大小 | 说明 |
|---|---|---|
| `bin\setup.bat` | 150 B | 调用 `%~dp0osgeo4w-setup.exe`，但该 exe **不存在** |
| `etc\preremove-conf.bat` | 285 B | 卸载清理脚本 |
| `var\log\postinstall.log` | 48271 B | 记录 2026/06/28 曾安装成功，但当前目录已被清空 |

因此：

- **不存在** `apps\qgis`、`bin\qgis-bin.exe`、`qgis_core.dll`、`python-qgis.bat`、`include\`（头文件）、`share\`（资源）。
- `python -c "import qgis"` → `ModuleNotFoundError: No module named 'qgis'`（命令输出）。
- `where qgis` → 无结果。
- 结论：**本机没有可运行的 QGIS / PyQGIS**。用户声称的 "4.0.3 含 bin/apps/qgis/include/PyQGIS" 与实际磁盘状态不符，属**未证实/已证伪**。

### 1.2 唯一可读的 QGIS 源码树

实际源码在 `D:\qgis-src-34415\QGIS-final-3_44_15`（命令输出）：

```
top:            python  resources  src
count_cpp_h:    5974          # *.cpp + *.h 文件数
has_CMakeLists: False
has_qgsconfig:  0             # 没有构建期生成的版本头
python_core:    additions  auto_additions  auto_generated  class_map.yaml  contextmanagers.py ...
```

- 只有 `src/`、`python/`、`resources/` 三个顶层目录，**没有顶层 CMakeLists、没有构建产物、没有 `qgsconfig.h`**。
- 因此：
  - 版本号**只能从目录名推断为 3.44.15**；构建期版本、编译选项、PROJ/GEOS 具体版本均**未证实**。
  - `python/core` 只有 `.sip` 定义与生成脚本，**没有编译好的 PyQGIS 扩展**，所以不能 `import qgis`。
- 可读范围：C++ 源码（`src/core`、`src/app`、`src/gui` 等）+ SIP 绑定定义。这足够做**机制级**学习，但不足以复现任何运行行为（"能读不能跑"）。

### 1.3 Python 侧几何库现状（命令输出）

| 库 | 状态 | 与本任务的关系 |
|---|---|---|
| `pyproj` | **已安装**，3.7.2 | 项目 `pyproject.toml` 未声明；可作为将来可选依赖 |
| `shapely` | **未安装**（ImportError） | 对应 QGIS 的 GEOS 裁剪/prepared geometry |
| `geographiclib` | **未安装**（ImportError） | 对应 QGIS 的 `QgsDistanceArea` 椭球测地线 |

项目自身依赖声明（`pyproject.toml:20`、`29-33`、`52`）：许可证 **MIT**；硬依赖仅 `numpy / scipy / requests`；`desktop = ["PySide6"]`。**没有** pyproj/shapely/geographiclib。这一点决定了第 4、5 节的取舍。

---

## 2. 逐问题对照表

### 2.0 总表

| # | 我们的痛点 | QGIS 的做法（一句话） | 主要证据 | 可搬性 |
|---|---|---|---|---|
| P1 | 视图裁剪：宽 FOV 下几千点逐点 Python 投影 | 两级 culling：地图范围→图层 CRS 的启用了 filter rect 的 `QgsFeatureRequest` → provider/R-tree 预筛 → GEOS prepared clip → 逐 feature 绘制 | `qgsmaprendererjob.cpp:376-470`、`qgsvectorlayerrenderer.cpp:361`、`525-574` | 思路可搬，代码不可搬；我们已有等价实现 |
| P2 | 分块渲染 | **矢量不分块**；只按图层并行/分 part。**光栅**才用 `QgsRasterIterator` 2000px 分块；**点云**用八叉树 + 屏幕误差 LOD + point budget | `qgsmaprendererparalleljob.cpp:52-106`、`qgsrasteriterator.h:238-241`、`qgspointcloudlayerrenderer.cpp:250-380` | 部分可搬（LOD/预算），"矢量分块"不可搬 |
| P3 | 极点/投影奇点退化 | **没有"留空极点"先例**；靠 ① 有效域裁剪 ② 逐点异常跳过 ③ 线裁剪到地图矩形 ④ 线数上限 ⑤ 间距像素门 | `qgslayoutitemmapgrid.cpp:48`、`1729-1900`、`2640-2665`、`qgsdecorationgrid.cpp:370-452` | 可搬（裁剪/门限），"留空"是本项目自创 |
| P4 | 经纬网/测地线/大圆 | `QgsCoordinateTransform`（PROJ）+ `QgsDistanceArea`（GeographicLib 椭球） | `qgscoordinatetransform.cpp:820-880`、`qgsdistancearea.cpp:337/441/480/518/687/710` | 机制可搬，库与椭球口径不可搬 |
| P5 | 大量点要素绘制批处理 | 逐 feature 调 `renderFeature`（符号级别批处理，不是点级别） | `qgsvectorlayerrenderer.cpp:574`、`qgssymbol.cpp:180-210` | **我们已更优**（分桶 `drawPoints`），不要反向抄 |

### 2.1 P1 视图裁剪

**QGIS 数据流（带行号）**

1. `QgsMapRendererJob::reprojectToLayerExtent()` — `src/core/maprenderer/qgsmaprendererjob.cpp:376`
   - 反子午线处理常量 `SPLIT_COORD = 180.0`（`:394`）；
   - 20% 面积容差判定是否"绕地球一圈"（`:414-417`），是则退化为 `(-180,-90,180,90)`（`:424`）；
   - 命中 ±180 时用 `(-180,180)` 覆盖（`:447-449`）。
2. `prepareJobs` 先 `r1.grow(mSettings.extentBuffer())` 再调上一步 — `qgsmaprendererjob.cpp:593-600`（`:1141` 同样逻辑）。即**先给地图范围加缓冲**，再做 CRS 变换，避免边缘符号被裁掉。
3. `QgsVectorLayerRenderer` 把该范围塞进请求 — `src/core/vector/qgsvectorlayerrenderer.cpp:361`：`.setFilterRect(requestExtent)`；provider 只返回与 bbox 相交的要素。
4. 复杂几何用 **prepared geometry** 做精确裁剪 — `qgsvectorlayerrenderer.cpp:525-529`：`QgsGeometry::createGeometryEngine(...)` + `clipEngine->prepareGeometry()`；`698-702` 同理。
5. 逐要素提交 — `qgsvectorlayerrenderer.cpp:574`：`renderer->renderFeature(fet, context, -1, featureIsSelected, drawMarker)`。
6. 两条裁剪路径（请求级几何预筛 + painter clip region）的封装见 `src/core/qgsmapclippingutils.cpp:60-150`。
7. 线段级快速裁剪算法：`QgsClipper::clipLineSegment`（Sobkow 1987），声明 `src/core/qgsclipper.h:162`，内联实现在 `:777`。
8. bbox 查询用 R-tree：`src/core/qgsspatialindex.cpp:483-525`（libspatialindex）；GEOS 侧 `qgsgeos.cpp:285-355`（`GEOSPrepare` / `GEOSClipByRect`）。
9. 请求语义（filter rect 是 bbox 语义，不是精确裁剪）写在 `src/core/qgsfeaturerequest.h:286-362`。

**对照我们的现状**：`mbdsdr_ai/skyengine/star_catalog.py` 的 `select_for_view` 已经是同构实现——赤纬带 `searchsorted`（`:478-487`，等价于按 bbox 做区间预筛）→ 星等预筛（`:491-496`）→ 批量矩阵变换（`:498-508`）→ 地平门 + 视锥 `dot >= cos_r`（`:516-518`）→ 消光/LOD（`:527-533`）→ `max_count` 预算（`:537-543`）。我们没有 R-tree，但星表是**静态有序数组**，`searchsorted` 的 O(log n) 比 R-tree 更适合，**不需要换**。

**最小可搬改法**：无。这条痛点已被现有实现覆盖；若将来要加 DSO/变源等非均匀目录，再考虑按赤纬分带 + 每带排序（同样不需要 R-tree）。

**风险**：照搬 `QgsFeatureRequest` 的"bbox 预筛 + 精确裁剪"两级结构会在 Python 里多做一次几何求交，而我们视锥判定本身是解析式向量点积，更快。**不要引入 shapely 做视锥裁剪**。

### 2.2 P2 分块渲染

**QGIS 并没有对矢量做瓦片化渲染。** 证据：

- `QgsMapRendererParallelJob` 的并行单位是**图层**，不是瓦片：`src/core/maprenderer/qgsmaprendererparalleljob.cpp:52-106`（构造/启动）、`:344-403`（按 layer 分发线程）。
- `QgsMapRendererStagedRenderJob` 按**图层逐 part** 输出（供逐块导出/打印），共享同一个 painter，可复用图层 cache：`src/core/maprenderer/qgsmaprendererstagedrenderjob.cpp:36-200`。
- 整层图像缓存键 = 图层 id + extent + mapToPixel，命中直接贴图：`src/core/maprenderer/qgsmaprenderercache.cpp:84-140`；旧图平移/裁剪复用见 `:220-280`（**旋转则失效**）。
- 真正按块读的是**光栅**：`src/core/raster/qgsrasteriterator.h:238-241`，`DEFAULT_MAXIMUM_TILE_WIDTH/HEIGHT = 2000`；接口 `startRasterRead` / `readNextRasterPart`。
- **点云**用八叉树节点 + 屏幕误差阈值 + point budget：`src/core/pointcloud/qgspointcloudlayerrenderer.cpp:250-380`（节点 bbox 剔除、误差逐级减半、预算），递归遍历见 `:795-855`。

**对照我们的现状**：

- 星点已经是"点级批处理"：`desktop/rf_sky_view.py:4888 _draw_stars`，分桶键 `(色//40, alpha//48, 直径2px)`（`:4984-4987`），每桶一次 `drawPoints`（`:5003-5005`）。这比 QGIS 的"逐 feature 调 symbol"更省状态切换。
- 轨迹仍是逐段 `QPen` + `drawLine`（`rf_sky_view.py:7908 _draw_trajectories`、`:7956 _draw_heatmap` 每格 `drawEllipse`）。

**最小可搬改法**：把 `_draw_trajectories`（`rf_sky_view.py:7908-7954`）的逐段 pen 改成"按 (颜色, alpha 量化, 宽度) 分桶 → 单条 `QPainterPath` → 每桶一次 `drawPath`"，与星点分桶同一套思路；`_draw_heatmap`（`:7956-7986`）同理按 alpha 分 2-3 桶。这只是把已有的 `dot_buckets` 模式复制到相邻两处，不引入新依赖。

**风险**：轨迹颜色若逐段连续渐变，量化会产生色带；必须先确认颜色离散度（可只对 alpha 分桶、颜色保持逐段）。**未证实**收益，需先做计时。
**后续实测（2026-11-17，见 §5 P5）**：真实喂入只有 11 点/星，轨迹整层 <0.1 ms，**不构成瓶颈，本条不做**；只有人为 300 点×5 的压力场景才 29.6 ms。热力图另修了「paintEvent 从未调用」的真 bug。

### 2.3 P3 极点/投影奇点退化

**QGIS 没有"留空极点"的既定做法。** 证据：

- `QgsLayoutItemMapGrid` 全文检索 `pole` / `singular` **没有代码命中**（唯一命中是版权邮箱 `sourcepole`）。
- 它实际用的是五道防线：

| 防线 | 证据 | 作用 |
|---|---|---|
| 线数上限 | `qgslayoutitemmapgrid.cpp:48` `#define MAX_GRID_LINES 1000`；循环条件 `:1571/1594/1671/1695/1756/1834` | 极点附近经线密集时**直接停止生成**，不会生成上万条 |
| 源坐标 densify | `xGridLinesCrsTransform` / `yGridLinesCrsTransform`（`:1729` / `:1808`）把源线采样成多段再逐点变换 | 让弯曲的经纬线在屏幕上是曲线而非直弦 |
| 逐点异常跳过 | `:1770-1773`、`:1848-1852` 的 `try { t.transform(...) } catch (QgsCsException) { continue; }` | **投影奇点/无效域的点被静默丢弃**，该线在此断开 |
| 反子午线拆分 | `:1834` 的 `crosses180 && !crossed180` | 跨 ±180 的线拆开画 |
| 裁剪到地图矩形 | `trimLinesToMap`（`:2640`），调用点 `:1788`、`:1861`；实现用 `QgsGeometry::intersection` 求交并可能拆成多段 | 只保留落在图框内的部分 |

- `QgsDecorationGrid`（`src/app/decorations/qgsdecorationgrid.cpp:370-452`）额外有：先用 `mapSettings.visiblePolygon()` 裁剪，再判断线间距 `< 5px` 就整层跳过，最后 `clipByRect`。
- `visiblePolygon()` 把 extent 四角反投影成多边形：`src/core/qgsmapsettings.cpp:421-460`。

**对照我们的现状与真实结论**：

- 我们 `desktop/rf_sky_view.py:5900 _draw_az_grid` 的做法是**主动留空极点**：`n_alt = 21`（`:5912`），`alt_km = np.linspace(0.0, 84.0, n_alt)`（`:5913`），即最高只画到 `alt=84°`，天顶 6° 留空。这是 QGIS 里**不存在**的本地策略。
- 赤道网格同理：`rf_sky_view.py:5964-5986 _ring_path` 逐点 `_sky_to_screen_finite`，`None`/离屏即 `first=True` 断线（`:5977-5981`）——这等价于 QGIS 的"逐点异常跳过"。赤纬圈列表 `range(-60, 91, 30)`（`:5989`）含 `dec=90`，该环退化为 25 个重合点，无害（已在 `docs/learn/2026-11-17-sky-zoom-clutter-audit.md` 记录）。
- RA 时圈 `range(-80, 81, 10)`（`:5997`）在极区留空 10°。

**发现的新退化（本次调研新增）**：极点留空是**固定角度**，不随 FOV 收缩。`sky_interaction.py:71` `DEFAULT_MIN_FOV_DEG = 30.0`，最窄视场 30°。按 `AEP` 的"屏幕半径 = 角距离"，alt=84° 环相对天顶的角距 6°，在 700px 视口（半宽 350px）、FOV=30° 时，最小屏幕半径约

```
r_px = 350 * (6 / 15) = 140 px
```

即**整个 280px 直径的圆盘是空的**，占满屏高度 40%。这不是投影 bug，而是"固定留空"与"最小 FOV"叠加的观感退化。

**最小可搬改法（不真改）**：把留空角改成随 FOV 收缩，例如

`rf_sky_view.py:5913` → `cap = min(6.0, 0.05 * fov_deg)`，`alt_km = np.linspace(0.0, 90.0 - cap, n_alt)`

同样给 `:5997` 的 `range(-80, 81, 10)` 换成按 `90 - cap` 生成。目标是保持一个**固定像素级**的空洞（约 35px，180/π 换算），而不是固定角度。既保留 `test_azimuth_grid_does_not_converge_at_zenith` 想守的"不在天顶汇成一点"，又在窄视场下不再浪费 40% 画面。

**风险**：`n_alt=21` 固定时，端点从 84° 挪到接近 90° 会让相邻采样间距变大，线在极区可能偏折；建议同时把 `n_alt` 提到 31 或按 `cap` 自适应。**未证实**观感收益，需离屏渲染对比（见第 5 节 P3 判据）。

### 2.4 P4 投影变换

**QGIS 用 PROJ，且是批量 + 哨兵值。**

- 单点/批量签名：`src/core/proj/qgscoordinatetransform.h:205-264`（`transform`、`transformBoundingBox`）、`:300-390`（`transformInPlace`、`transformCoords`）。
- 批量核心：`src/core/proj/qgscoordinatetransform.cpp:971-1035`，`proj_trans_generic(...)` 一次变换整条数组；随后 `!std::isfinite(x[i]) || !std::isfinite(y[i])` 扫描（`:721`、`:1020` 附近），把非有限点标记出来。
- bbox 变换：`DENSIFY_POINTS = 30`（`:820`），`proj_trans_bounds(..., DENSIFY_POINTS)`（`:821-823`）；失败或非有限则回退/重试（`:826-849`）。
- 无效域语义：PROJ 返回 `HUGE_VAL`，QGIS 把它当作"该点不可表示"，调用方跳过。

**对照我们**：`mbdsdr_ai/celestial_geometry.py`

- 基类 `_BaseProjection.project_vec_many`（`:582-593`）是**逐点 Python 循环**，注释明确"默认逐点复用 `_forward`，不另写一套几何"。
- `StereographicProjection` 有解析批量式（`:641-654`），非有限用 `FAR_SENTINEL = 1e6`（`:612`）而不是 NaN——这与 QGIS 的 `HUGE_VAL` 哨兵语义一致。
- `OrthographicProjection`（`:657-694`）**没有** `project_vec_many`，走基类循环；且 `_forward`（`:669-672`）**没有背面判断**：

```python
def _forward(self, v):
    r = math.sqrt(v[0]*v[0] + v[1]*v[1] + v[2]*v[2])
    h = 1.0 / r
    return v[0] * h, v[1] * h
```

  docstring（`:660`）写"背面 `v[2]>0` 不可见"，但代码不看 `v[2]`。实测 `project_vec([0,0,1]) = (0,0)`，即**对跖点被折到屏幕正中**。`_backward`（`:674-680`）反而做了 `dq>1` 的截断，正反不一致。
- `AzimuthalEquidistantProjection`（`:697-729`）也没有批量覆盖，走基类逐点循环（`:711-721` 的 `_forward` 用 `inf` 做中心奇点哨兵，语义正确）。

**微基准（本机实测，命令为只读 `python -c`）**：

| 项目 | 逐点 | 解析批量 | 加速 |
|---|---|---|---|
| AEP 投影 1500 点 | 1.22 ms | 0.04 ms | ~27× |
| `vec_from_radec` 378 点 | 0.430 ms | 0.012 ms | ~35× |

**结论（重要）**：投影本身在 1500 点量级只占 **约 1–2 ms/帧**。`_paint_ms_ema` 实测 57 ms/帧（700×700、120° FOV）**不能归因于逐点投影**；主因需仪器确认（**未证实**，候选：QPainter 提交、标签布局、多层叠加、无静态层缓存）。因此"给 AEP 加批量"是**正确的工程卫生**，但不是性能救命稻草。

**最小可搬改法（不真改）**：

1. `celestial_geometry.py:669-672`：Ortho `_forward` 加背面/有效域判断，返回非有限哨兵（与 Stereo 的 `FAR_SENTINEL` 同风格），例如 `if v[2] > 0: return inf, inf`。
2. `celestial_geometry.py:657` 类内新增 `project_vec_many`（照 `StereographicProjection:641-654` 的写法：`arr[:,2] > 0 → NaN/inf` 掩码）。
3. `celestial_geometry.py:697` AEP 同样加解析批量（`h = hypot(x,y); f = arctan2(h,-z)/h`，`h<=eps` 分流中心/背面）。
4. 调用方 `desktop/rf_sky_view.py:4502 _sky_to_screen_finite` 已把非有限映射为 `None`，所以只要投影返回 inf/NaN，绘制端**现有逻辑就能正确丢弃**，无需改绘制层。

**风险**：Ortho 加背面掩码后，若某层依赖"对跖点折回中心"的旧行为（几乎不可能，但需 grep 确认无调用方断言 `(0,0)`），会改变测试。属于**正确性修复**，优先于性能。

### 2.5 P5 大量点要素的绘制批处理

- QGIS 的批处理粒度是**符号（symbol）**，不是点：每个 feature 调一次 `renderer->renderFeature`（`qgsvectorlayerrenderer.cpp:574`、`:898`），由 `QgsSymbol` 内部把 marker 缓存成 QImage 再贴（`src/core/symbology/qgssymbol.cpp:180-210`，先按 layer extent 裁剪，`ApplyClipAfterReprojection` 时改到变换后裁剪，clip rect 放大 10% 防边缘丢符号）。
- 我们的星点直接把 `QPointF` 列表喂给 `drawPoints`（`rf_sky_view.py:5003-5005`），**状态切换次数按桶数算（几十次），不是按点数算**。
- 结论：这一项**我们已经优于 QGIS 的通用路径**。唯一可借的是"clip rect 放大 10%"这条**边界符号不丢**的经验，对应我们 `rf_sky_view.py:5472 _on_screen(pos, margin=4.0)` 的 margin 思路——已经等价。

---

## 3. 大圆与测地线：QGIS vs `mbdsdr_ai/celestial_geometry.py`

### 3.1 QGIS 侧：GeographicLib 椭球测地线

| 能力 | 文件:行 | 核心调用 |
|---|---|---|
| 椭球距离/方位 | `src/core/qgsdistancearea.cpp:337`、`:379` | `geod_inverse(geod, lat1, lon1, lat2, lon2, &dist, &az1, &az2)` |
| 按方位+距离求点 | `qgsdistancearea.cpp:441` | `geod_direct(...)` |
| 大圆/测地线插值 | `qgsdistancearea.cpp:687`、`:710` | `geod_inverseline(&line, ...)` + `geod_position(&line, d, &lat, &lon, &t)` |
| 反子午线交点纬度 | `qgsdistancearea.cpp:446-527` | `latitudeGeodesicCrossesAntimeridian()`：用 `geod_inverseline` 建线，二分 `geod_position` 求与 ±180 的交点纬度，返回 `fractionAlongLine` |
| 插值时的日界线处理 | `qgsdistancearea.cpp:594`、`:719` | 遇反子午线时**精确插入边界点**，使折线在 ±180 处闭合而不是横穿 |
| 椭球对象创建 | `qgsdistancearea.cpp:861-874` | `geod_geodesic`（GeographicLib C API） |

关键点：**QGIS 的"大圆"是椭球测地线**，不是球面大圆；只有在椭球被显式设成球时两者才重合（`willUseEllipsoid` / `setEllipsoid`，`qgsdistancearea.cpp:74-115`）。

### 3.2 我们侧：纯球面，无测地线

`mbdsdr_ai/celestial_geometry.py` 全文**没有** `geodesic` / `ellipsoid` / Vincenty / slerp / 大圆插值函数（grep 无命中）。现有相关能力只有：

- `vec_from_radec` / `radec_from_vec` / `vec_from_azalt` / `azalt_from_vec`（球面单位向量互转）。
- `azalt_from_vec_many`（批量）。
- 三种投影。

"大圆"在我们代码里实际是**两条端点直接 `lineTo` 的直弦**：

- 星座连线：`desktop/rf_sky_view.py:5373 _constel_path(segments, points)` → `lineTo`。
- 赤道网格：`rf_sky_view.py:5964-5986` 的 `_ring_path` 也是逐点 `lineTo`（但它的采样点本来就密，等效于 densify）。

卫星侧走的是另一条链，且口径明确不同：

- `mbdsdr_ai/ground_track.py:1-17` 文件头声明：传播用 SGP4 TEME（km），星下点用 **WGS-72 椭球**，覆盖圈半径用 **WGS-72 赤道半径的球面几何**（`λ = acos(Re/(Re+h)·cosε) − ε`，`r = λ·Re`），并明确"禁止接 WGS-84 米制链"。
- `DEFAULT_TRACK_STEP_S = 30.0`、`DEFAULT_TRACK_STEP_MAX_CHORD_KM = 0.10`（`ground_track.py:38-45`）；30 s 步长的实测最大弦误差 0.0916 km，判据见 `tests/test_ground_track_step_bound.py`。

### 3.3 差异量化（决定要不要引入 GeographicLib）

| 场景 | QGIS 做法 | 我们的误差 | 结论 |
|---|---|---|---|
| 星座连线 | 若用测地线插值，椭球 vs 球面差 < 0.5% | 直弦 vs 大圆：段长 25.6°（IAU 最长段）在 350px 半径下，40° alt 处 Stereo 2.84px / AEP 3.24px；20° alt 处 7.9px | 常规段 ≤ 3–8px，**不值得**做椭球；只有 >40° 长弧才值得球面 slerp densify |
| 卫星地面轨迹 | 测地线插值 | 球面短弧 + 30 s 弦误差 0.0916 km | 200 km 线宽下 < 1px，**不需要**椭球 |
| 覆盖圈 | 测地线圆 | 球面 `λ` 公式；2000 km 半径与椭球差约 0.3% ≈ 6 km | 700px 世界地图上约 0.1px，**不需要**椭球 |
| 反子午线接缝 | 精确插入 ±180 边界点 | `split_at_dateline`（`ground_track.py:112`）只按跳变切段，**不插边界点** | **这是真问题**，见下 |

### 3.4 唯一值得抄的机制：反子午线边界点

QGIS 的 `latitudeGeodesicCrossesAntimeridian`（`qgsdistancearea.cpp:446-527`）+ 插值处插入边界点（`:594`、`:719`）解决的是：折线在 ±180 处**不该留下缺口，也不该横穿整张地图**。

我们有三处对应缺陷：

1. **`mbdsdr_ai/ground_track.py:112 split_at_dateline`**：只做
   ```python
   if current and abs(point.lon_deg - current[-1].lon_deg) > 180.0:
       segments.append(current); current = []
   ```
   **不插入 ±180 的边界点**，因此每段终点停在实际经度（可能离 ±180 还有几度），地图上表现为跨日界线处最多约 200 km 的缺口（30 s × 7.66 km/s 量级）。
2. **`desktop/sat_ground_map.py:433-448 _split_lonlat`**：切段逻辑相同，但结尾 `:446-448`
   ```python
   if current:
       current.append(current[0])
       chunks.append(current)
   ```
   **无论 `closed` 是否为真，都把最后一段的首点补到末尾**。后果：
   - 开放折线（轨迹）的最后一段被强行闭合，画出多余回边；
   - 闭合环（footprint）跨日界线时，中间 chunk **从不闭合**、最后 chunk 用**自己的首点**闭合（不是环首点），填充/描边会在墨卡托全图上拉出横跨地图的假边或错误填充。
3. **`desktop/osm_map.py:1056-1070 lonlat_to_world`**：已把经度归一化，`world_width = tile_size * 2**zoom` 已知，具备做 ±world 绕绘的条件，但目前未被 `sat_ground_map` 用于跨日界线的连续绘制。

**最小可搬改法（不真改）**：在 `ground_track.py:112` 的切段处，按 QGIS 二分思路计算与 ±180 的交点经纬度并**各插入一个点**（球面线性插值即可，误差在 200 km 尺度 < 1px）；`sat_ground_map.py:446-448` 改成 `if closed: current.append(current[0])`，并对中间 chunk 用首/末点补齐边界。

**风险**：插入边界点会改变 `segments` 的点数与既有测试断言；需同步更新 `tests/` 里对轨迹点数的期望。属于**行为变更**，要先加"接缝连续性"判据再改。

---

## 4. 不能抄的东西与许可说明

QGIS 是 **GPL-2.0-or-later**。证据（文件头原文）：

- `src/core/qgsdistancearea.cpp:9-12`："This program is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License ... either version 2 of the License, or (at your option) any later version."
- `src/core/qgsclipper.h` 同样带 GPL 头。

本项目是 **MIT**（`pyproject.toml:20`）。**MIT 项目不能把 GPL 代码复制进来**——那会让整个分发物受 GPL 传染。本次调研只记录**机制与算法思想**，未复制任何 QGIS 代码文本。

### 4.1 不能抄清单

| 东西 | 为什么不能抄 | 我们能做的替代 |
|---|---|---|
| QGIS C++ 源码（任何 `.cpp/.h` 片段） | GPL-2.0+ 传染性；MIT 项目不可并入 | 只按文献/机制重写；必要时按公开算法（Sobkow 1987、Haversine、GeographicLib 论文）独立实现 |
| `QgsCoordinateTransform` / PROJ 绑定 | C++ + PROJ 运行时依赖；本机 QGIS 不可运行；PyQGIS 不可 import | 已有 `pyproj` 3.7.2 可用（本机实测），但**未在 pyproject 声明**，要用需先加 optional 依赖 |
| `QgsDistanceArea` / GeographicLib 椭球链 | 额外依赖 + 与本项目"WGS-72 球面"口径冲突（`ground_track.py:1-17` 明令禁止接 WGS-84 米制链） | 球面公式已足够（误差 < 1px）；只在反子午线插点这一处借鉴**思路** |
| GEOS prepared geometry / `QgsGeometryEngine` | C++ GEOS；Python 侧 shapely 未安装 | 我们的视锥是解析式点积，本就不需要几何引擎 |
| libspatialindex R-tree | 额外 C 依赖；星表是静态有序数组，`searchsorted` 更优 | 保持 `star_catalog.py:478-487` 的赤纬带二分 |
| "矢量瓦片分块渲染" | **QGIS 自己就没有**（见 §2.2）；前提不成立 | 若将来真要分块，按屏幕四叉树 + 每块独立 `QImage`，不要假借 QGIS 名义 |
| `QgsMapRendererCache` 的旧图裁剪复用 | 依赖 `QgsMapToPixel` 的**仿射**假设（平移/缩放可复用，旋转失效，`qgsmaprenderercache.cpp:220-280`）；我们的星图是球面投影，**非仿射** | 只可做"FOV/中心不变时整帧复用"，不能做局部平移裁剪 |
| `MAX_GRID_LINES = 1000` 直接照搬 | 那是**平面地图经纬线**的防爆上限；我们的网格线数是固定的（`n_alt=21`、12 条射线、24 条时圈），没有爆量风险 | 不需要；我们的风险是极点汇合，不是线数爆炸 |
| 逐 feature `renderFeature` 架构 | 比我们现有的 `drawPoints` 分桶慢（§2.5） | **反向**：保持分桶，不做逐要素 |

### 4.2 "性能前提不成立"的三条

1. **"几千点逐点 Python 投影 = 57 ms 卡顿主因"**：实测投影只占 1–2 ms（§2.4 微基准）。不要把优化预算押在批量投影上。
2. **"QGIS 有矢量分块渲染可以抄"**：QGIS 矢量不分块（§2.2）。分块只存在于光栅和点云。
3. **"引入椭球测地线能显著改善画质"**：星座直弦误差 ≤ 8px、覆盖圈椭球差 ≈ 0.1px（§3.3）。收益低于引入依赖的成本。

---

## 5. 建议的下一步（按收益排序，每条可独立验证）

> 每条都给出"改哪里 / 怎么独立验证 / 预期"。
> **执行状态（2026-11-17 续）**：P1 / P2 已落地并有回归用例；P3 经复核**否决**
> （与「放大后中心不许聚线」红线冲突，见下）；P5 已实测，**P4 依实测否决**
> （真实喂入每颗 11 点，轨迹不是瓶颈；压力场景才有 29 ms，见 §5 P5 表）。
> **P6 已落地**：新增 `scripts/export_geojson.py`，把内置台址图层写成
> RFC 7946 文件（实测最初 7 要素，台址扩表后为 **9 要素**，见
> `docs/COORDINATION.md` §42）；本机仍无可运行的 QGIS 本体，故「QGIS 里
> 加载」这一步未实测，见 `docs/COORDINATION.md` §39。
> **P6 续（2026-11-17）**：同一脚本增加 `ground-track` 与 `footprint` 两个
> TLE 图层——轨迹按日界线切段，覆盖圈跨线时退化为分段边界；每条要素带
> TLE 历元与 NORAD 编号。实测 ISS：轨迹 2 段 / 183 点，覆盖圈 Polygon
> 73 顶点 + 星下点，见 §40。

**P1（最高）— 修 Ortho 背面折叠 + 补三种投影的批量路径** ✅ 已落地
- 位置：`mbdsdr_ai/celestial_geometry.py:669-691`（`v[2]>0` → inf / 批量 NaN）、`:582`（基类逐点批量）、`:641`/`:681`/`:750`（Stereo / Ortho / **AEP** 解析批量；AEP 原先漏了，走了基类逐点循环，2026-11-17 续补）、Perspective 同形。
- 验证（已入仓）：`tests/test_astronomy.py::TestOrthographicBackFace`——
  ①`project_vec([0,0,1])` 两个分量都是 inf（旧行为是折回 (0,0)）；
  ②`project_vec_many` 对背面给 NaN、对正面给 (0,0)；
  ③四种投影批量与逐点逐元素一致（1e-12）；
  ④`test_aep_batch_center_and_antipode`：AEP 中心（天顶）→ (0,0)、对跖点 → inf，且与逐点同口径。`python -m pytest tests/test_astronomy.py -q` → 34 passed。

**P2 — 日界线接缝：插入 ±180 边界点 + 修正闭合逻辑** ✅ 已落地
- 位置：`mbdsdr_ai/ground_track.py:112-135`（切段时按球面线性插值补 ±180 边界点）、`desktop/sat_ground_map.py:433+`（`_split_lonlat` 不再无条件追加首点，`closed` 才补）。
- 验证（已入仓）：`tests/test_ground_track.py::test_split_function_breaks_on_lon_jump`
  （切段后两段各 2 点，端点经度恰为 ±180，边界点纬度/高度/时刻为线性插值）；
  `tests/test_sat_ground_map.py::test_split_lonlat_does_not_draw_fake_edge_across_map`
  （跨日界线覆盖圈切成两段，每段 bbox 宽度 < 半张地图，无横穿假边）。
- 结果：跨日界线的 ~200 km 缺口与「开放折线被强行闭合」的假回边都已消除。

**P3 — 极点留空角随 FOV 自适应** ❌ 否决（2026-11-17 复核）
- 原提案 `cap = min(6.0, 0.05 * fov_deg)` 在窄视场下会把射线端点推到 89.75°，
  12 条射线在天顶附近重新聚成一朵——这正是红线「放大后中心一堆火星/交叉」
  的成因。固定 6° 留空（`desktop/rf_sky_view.py:6012`）是**刻意**的，且由
  `test_azimuth_grid_does_not_converge_at_zenith` 与
  `docs/learn/2026-11-17-sky-zoom-clutter-audit.md` 的中心墨迹判据锁定。
- 若将来确实要做「窄视场省画面」，正确方向是**屏幕空间恒定的留空半径**
  （例如 `max(6°, k * fov)`，k≈0.12），而不是让角留空随 FOV 缩小；必须先补
  「FOV=5° 中心 40×40 墨迹 = 0」回归再改。

**P4 — 轨迹/热力图分桶批处理** ❌ 依 P5 实测否决（不做）
- 位置：`desktop/rf_sky_view.py:7908 _draw_trajectories`、`:7956 _draw_heatmap`。
- 实测前提不成立：`SatelliteTracker` 每颗星只喂 **11 点**（未来 10 分钟、60 s 一点，
  `rf_sky_view.py:9800-9816`），快转重算也是同一步长（`:2554-2567`）。
  真实默认场景整帧 16.2 ms，`_draw_trajectories` 连前 8 名都进不去（<0.1 ms）。
  只有人为压力场景（5 星 × 300 点）才到 29.6 ms——那是 1500 次 `drawLine`，
  不是本应用的数据形态。
- 结论：不为不存在的负载改绘制路径（避免量化 alpha 造成色带与视觉回归）。
  若将来真画整圈过境轨迹（>100 点/星），再按 §2.2 的分桶方案做，并先补计时。
- 顺带修的真 bug：`_draw_heatmap` 此前**从未被 paintEvent 调用**（`set_heatmap`
  只设标志，画面永远空白）。现已在 `paintEvent` 按 `_show_heatmap` 调用，
  并补 `_on_screen(pos, 15.0)` 视口剔除；回归
  `tests/test_sky_layer_toggles.py::TestHeatmapDrawWiring`。

**P5 — 先测 57 ms 的真实分布（诊断，不优化）** ✅ 已执行（2026-11-17）
- 方法：离屏 700×700、默认 120° 透视、按 `paintEvent` 真实开关条件逐层计时，
  每场景 30 帧取中位（脚本在仓外 `d:\mbdsdr\scratch\qgis_p5_layer_timing.py`）。
- 结果（整帧 / 最大单层）：

| 场景 | 整帧中位 | 最大单层 |
|---|---|---|
| A 真实默认（1 星 × 11 点，赤道网格关） | **16.2 ms** | `_draw_stars` 4.8 ms（30%） |
| B 打开赤道网格 | **24.3 ms** | `_draw_eq_grid` 8.4 ms（35%） |
| C 压力（5 星 × 300 点） | **44.9 ms** | `_draw_trajectories` 29.6 ms（66%） |

- 结论：旧「57 ms 卡顿」在离屏复现不出（16 ms）；默认场景主成本是星点绘制与
  星座线，两者都已批量/分桶。赤道网格是可选层，**开启时**代价 8.4 ms，
  源于 18 条点线（`Qt.DotLine`）描边，不是 Python 投影；改成分桶/合路径收益
  未证实，且会改变交叉点叠加观感，本批不动。
- 明确不做：给轨迹/热力图分桶（P4）、给 `_draw_stars` 再做二次优化。

**明确不做**
- 不引入 GEOS/shapely/libspatialindex/GeographicLib。
- 不做矢量瓦片分块。
- 不照搬 `QgsMapRendererCache` 的局部平移复用（投影非仿射）。
- 不把星座连线改成椭球测地线（收益 < 8px，成本是新增依赖 + 口径冲突）。

---

## 附：本文证据来源一览

| 类型 | 位置 |
|---|---|
| QGIS 安装实况 | `C:\Program Files\QGIS 4.0.3` 仅 3 个文件；`import qgis` 失败 |
| QGIS 源码树 | `D:\qgis-src-34415\QGIS-final-3_44_15`；5974 个 cpp/h；无 CMakeLists、无 qgsconfig.h |
| 裁剪链 | `qgsmaprendererjob.cpp:376-470,593-600`；`qgsvectorlayerrenderer.cpp:361,525-529,574`；`qgsmapclippingutils.cpp:60-150` |
| 分块 | `qgsmaprendererparalleljob.cpp:52-106,344-403`；`qgsrasteriterator.h:238-241`；`qgspointcloudlayerrenderer.cpp:250-380` |
| 网格极点 | `qgslayoutitemmapgrid.cpp:48,1729-1900,2640-2665`；`qgsdecorationgrid.cpp:370-452` |
| 投影/测地线 | `qgscoordinatetransform.cpp:721,820-849,971-1035`；`qgsdistancearea.cpp:337,441,446-527,594,687-719` |
| 项目侧 | `celestial_geometry.py:582-593,641-654,657-694,697-729`；`rf_sky_view.py:4502,4888,4984-5005,5373,5900-5999,7670,7714`；`ground_track.py:38-45,112-123`；`sat_ground_map.py:433-448`；`star_catalog.py:448-570` |
| 许可/依赖 | `qgsdistancearea.cpp:9-12`；`pyproject.toml:20,29-33,52` |
