# Stellarium 交互模型研究报告

> 研究对象:Stellarium C++ 源码(`repos/stellarium/`),用于指导我们 PySide6 桌面
> 天空图(`desktop/rf_sky_view.py`)的交互控制模块重写。
> 交付物:`mbdsdr_ai/sky_interaction.py`(独立重实现,GPL-3.0 参考)。
>
> 所有行号基于本工作区检出的 Stellarium 版本。

---

## 0. 总览:交互分层

Stellarium 把"天空交互"拆成三层,我们的重实现也照此分层:

| 层 | Stellarium 类 | 职责 | 我们的对应物 |
|---|---|---|---|
| 视角状态机 | `StelMovementMgr` | 中心指向、FOV、平移/缩放/goto 动画 | `ViewState` |
| 投影/反投影 | `StelProjector(+Classes)` | 3D 天球向量 ↔ 屏幕像素 | `celestial_geometry` 投影 + `screen_to_sky`/`sky_to_screen` |
| 对象拾取 | `StelObjectMgr` | 屏幕坐标 → 反投影 → 最近天体 | `pick_object` |
| 事件分发 | `StelMainView` → `StelMovementMgr::handleMouse*` | 鼠标/滚轮事件 | `SkyInteractionHandler` |

关键设计思想:**一切交互最终都归结为修改"视图中心方向"和"FOV"两个状态量**。
拖拽、滚轮、goto、键盘方向键都只是改这两个量的不同方式。渲染器只读这两个量。

---

## 1. 视角控制数学(pan / zoom / goto)

### 1.1 状态量

`StelMovementMgr` 持有 `viewDirectionJ2000`(中心指向的 3D 单位向量)和
`currentFov`(当前视场角,度)。见 `src/core/StelMovementMgr.hpp:504-507`:

```cpp
double currentFov; // The current FOV in degrees
double minFov;     // Minimum FOV in degrees
double maxFov;     // Maximum FOV in degrees. Depends on projection.
```

默认范围在构造函数里设定(`src/core/StelMovementMgr.cpp:84-85`):

```cpp
, minFov(0.001389)   // ≈ 5 角秒(望远镜模式)
, maxFov(100.)
```

`userMaxFov` 默认 360°,真正上限还受投影类型限制(`StelMovementMgr.cpp:172,1786-1798`)。
**我们的 SDR 天空图不需要望远镜级缩放,把范围夹到 30°~180°**(`sky_interaction.py`
的 `DEFAULT_MIN_FOV_DEG`/`DEFAULT_MAX_FOV_DEG`)。

### 1.2 pan(平移天球)

`StelMovementMgr::panView(deltaAz, deltaAlt)`(`src/core/StelMovementMgr.cpp:1576`):

```cpp
StelUtils::rectToSphe(&azVision, &altVision, j2000ToMountFrame(viewDirectionJ2000));
if (fabs(deltaAz)>1e-10)  azVision  -= deltaAz;     // :1627
if (fabs(deltaAlt)>1e-10) altVision += deltaAlt;    // :1631
// 仰角夹取,防止天顶/天底方位角奇异:
if (altVision >  M_PI_2) altVision =  M_PI_2 - 0.000001;   // :1634
if (altVision < -M_PI_2) altVision = -M_PI_2 + 0.000001;   // :1635
StelUtils::spheToRect(azVision, altVision, tmp);
setViewDirectionJ2000(mountFrameToJ2000(tmp));              // :1644
```

要点:
- 平移就是**直接改中心指向的 az/alt**,不是改屏幕偏移。
- 仰角必须夹到 ±(90°-ε),否则在天顶处方位角定义退化(万向锁)。
- 过天顶时它还会从 up 向量反推方位角(`:1609-1623`)。

我们的 `ViewState.pan()`(`sky_interaction.py`)照搬这三点:
`center_az -= delta_az; center_alt += delta_alt;` 再夹取。

### 1.3 拖拽平移(dragView)

`StelMovementMgr::dragView(x1, y1, x2, y2)`(`src/core/StelMovementMgr.cpp:1659`)是
"用手指抓着天球走"的核心。它**不是直接按像素位移换算角度**,而是反投影前后两个
屏幕点到天球,求天球上的角位移:

```cpp
prj->unProject(x2, y2, tempvec2);     // :1679
prj->unProject(x1, y1, tempvec1);     // :1680
StelUtils::rectToSphe(&az1, &alt1, j2000ToMountFrame(tempvec1));
StelUtils::rectToSphe(&az2, &alt2, j2000ToMountFrame(tempvec2));
panView(az2 - az1, alt1 - alt2);     // :1684 注意 alt1-alt2(y 翻转)
```

我们的 `SkyInteractionHandler.mouseMoveEvent` 完全对应:用 `screen_to_sky` 反投影
前后两点,求 `daz = az2-az1`、`dalt = alt1-alt2`,再 `view_state.pan(daz, dalt)`。

### 1.4 zoom(缩放 FOV)

滚轮缩放见 `StelMovementMgr::handleMouseWheel`(`src/core/StelMovementMgr.cpp:537`):

```cpp
const double numSteps = (event->angleDelta().x() + event->angleDelta().y()) / 120.;  // :544
...
const double zoomFactor = exp(-mouseZoomSpeed * numSteps / 60.);   // :580
const float zoomDuration = 0.2f;
zoomTo(getAimFov() * zoomFactor, zoomDuration);                      // :582
```

`mouseZoomSpeed` 默认 30(`StelMovementMgr.cpp:98,159`)。**关键点:缩放是
乘法/指数的**(`fov *= exp(...)`),不是加法。这样在 1° 和 180° FOV 下滚轮手感
一致(对数缩放)。`zoomTo` 只是设一个目标 FOV 并开动画(`:1756`):

```cpp
void StelMovementMgr::zoomTo(double aim_fov, float zoomDuration) {
    zoomMove.setTarget(currentFov, aim_fov, zoomDuration);
    flagAutoZoom = true;
}
```

`setFov` 做夹取(`src/core/StelMovementMgr.hpp:466-474`):

```cpp
void setFov(double f) {
    ...
    currentFov = qBound(minFov, f, maxFov);
    emit currentFovChanged(currentFov);
}
```

我们的 `ViewState.zoom(num_steps)` 照搬 `exp(-speed*num/60)` 与夹取。

### 1.5 goto(跳转到天体)

`moveToAltAzi`(`src/core/StelMovementMgr.cpp:1472`)记录起点/终点方向,然后在
`updateVisionVector`(`:1119`)里逐帧插值。缓动函数在 `:1203-1214`:

```cpp
const float smooth = 4.f;
c = std::atan(smooth*2.f*move.coef - smooth) / std::atan(smooth)/2 + 0.5;  // :1213
```

这是一个 smoothstep 类缓动(两端慢、中间快)。插值本身在 `(ra,dec)` 球面坐标里做
线性混合,并做最短路径解卷绕(`:1238-1247`):

```cpp
if (ra_aim - ra_start >  M_PI) ra_aim -= 2.*M_PI;   // 不绕远路
if (ra_aim - ra_start < -M_PI) ra_aim += 2.*M_PI;
const double de_now = de_aim*c + de_start*(1.-c);
const double ra_now = ra_aim*c + ra_start*(1.-c);
```

我们当前 `ViewState.look_at()` 是**瞬时跳转**(无动画)。建议后续照 `:1213` 的
缓动加一个 QPropertyAnimation/QVariantAnimation 做平滑 goto。

---

## 2. 点选机制:屏幕坐标 → 反投影 → 最近天体

### 2.1 反投影(unProject)

`StelProjector::unProject(x, y, v)`(`src/core/StelProjector.cpp:604`):

```cpp
bool StelProjector::unProject(double x, double y, Vec3d &v) const {
    v[0] = (x - viewportCenter[0]) / pixelPerRad;   // :606
    v[1] = (y - viewportCenter[1]) / pixelPerRad;   // :607
    v[2] = 0;
    const bool rval = backward(v);                  // :609 投影反函数
    modelViewTransform->backward(v);                // :615 逆视图旋转
    return rval;
}
```

三步:**像素 → 归一化坐标(减中心、除 pixelPerRad)→ 投影反函数 `backward` →
逆模型视图变换**。这正是我们 `screen_to_sky()` 的结构:

1. `x_n = -(py-cy)/ppr; y_n = (px-cx)/ppr`
2. `projection.unproject_vec(x_n, y_n)` → 视图空间向量
3. `v = R_view.T @ v_view`(逆视图旋转)

`pixelPerRad` 的定义在 `StelProjector.cpp:172`:

```cpp
pixelPerRad = 0.5f * viewportFovDiameter / fovToViewScalingFactor(M_PIf/360.f * params.fov);
```

对等距方位投影,`fovToViewScalingFactor(fov/2) = fov/2`(弧度),故
`pixelPerRad = min_dim / deg2rad(fov)`——我们 `_pixel_per_rad()` 照此实现。

### 2.2 拾取搜索半径

`StelObjectMgr` 构造函数(`src/core/StelObjectMgr.cpp:36`):

```cpp
StelObjectMgr::StelObjectMgr() : ..., searchRadiusPixel(25.), distanceWeight(1.f)
```

默认 **25 像素**直径的搜索圆。在 `cleverFind(core, v)`(`:461`)里,它把像素半径
按当前 FOV 换算成角距(`:466`):

```cpp
const double fov_around = core->getMovementMgr()->getCurrentFov()
    / qMin(prj->getViewportWidth(), prj->getViewportHeight())
    * searchRadiusPixel * ...devicePixelsPerPixel;
```

然后遍历所有天体模块 `searchAround(v, fov_around)` 收集候选,再在候选里选
"屏幕距离 + 星等优先级"最小者(`:494-505`):

```cpp
float distance = sqrt((xpos-winpos[0])² + (ypos-winpos[1])²) * distanceWeight;
float priority = obj->getSelectPriority(core);
if (distance + priority < best_object_value) { best_object_value = ...; sobj = obj; }
```

### 2.3 屏幕坐标点选入口

`StelObjectMgr::findAndSelect(core, x, y)`(`:454`)→ `cleverFind(core, x, y)`(`:516`):

```cpp
StelObjectP StelObjectMgr::cleverFind(const StelCore* core, int x, int y) const {
    Vec3d v;
    const StelProjectorP prj = core->getProjection(StelCore::FrameJ2000);
    if (prj->unProject(x, y, v)) {                 // :520 反投影
        // Nick Fedoseev patch: 折射校正补偿
        Vec3d win; prj->project(v, win);
        const double dx = x - win.v[0], dy = y - win.v[1];
        prj->unProject(x+dx, y+dy, v);            // :527 二次反投影修正
        return cleverFind(core, v);                // :529
    }
    return StelObjectP();
}
```

我们的 `pick_object(az, alt, objects, pick_radius_deg=2°)` 是这一段的简化版:
直接用球面余弦定理算角距离,取半径内最近者。对 SDR 卫星(数量少、位置已知)
不需要 `searchAround` 空间索引,暴力遍历足够。

---

## 3. 鼠标事件分发流

Stellarium 的鼠标事件不是在 `StelMainView` 里直接处理,而是转发给
`StelMovementMgr::handleMouseClicks`(`src/core/StelMovementMgr.cpp:614`)。
核心逻辑:

- **按下**:记录起点 `previousX/previousY`(`:660` 附近)。
- **拖动**:若位移超过 `dragTriggerDistance`(默认 4px,`:130`),进入拖拽,调 `dragView`。
- **释放**(`:664`):
  - 若 `hasDragged` → 结束拖拽(时间拖拽模式下还会算惯性时间率,`:670-697`)。
  - 若**没拖动** → 视为一次点击 → `findAndSelect`(`:714`/`:716`):
    ```cpp
    objectMgr->findAndSelect(core, eventPosX, eventPosY,
        modifiers.testFlag(Qt::ControlModifier) ? AddToSelection : ReplaceSelection);
    ```
- **中键释放**(`:731-738`):若已选中天体,`moveToObject(...)` 居中跟踪它。

我们的 `SkyInteractionHandler` 照搬:
- `DRAG_THRESHOLD_PX = 4.0`(对照 `:130`)。
- `mousePressEvent` 记起点;`mouseMoveEvent` 超阈值才 `dragView`;
  `mouseReleaseEvent` 未拖动则 `pick_object`;`mouseDoubleClickEvent` → `look_at`。

---

## 4. 时间拖动

Stellarium 有个很巧妙的"按住天空拖时间"交互。在 `dragView`(`:1661-1674`)里,
若处于 `dragTimeMode`:

```cpp
prj->unProject(x2,y2, v2);  prj->unProject(x1,y1, v1);
v1[2]=0; v1.normalize();  v2[2]=0; v2.normalize();
double angle = (v2 ^ v1)[2];                       // 两向量叉积 z 分量 = 旋转角
double deltaDay = angle/(2.*M_PI)*core->getLocalSiderealDayLength();
core->setJD(core->getJD() + deltaDay);             // 转天球 = 转时间
```

物理含义:恒星视运动是天球绕北天极周日旋转;水平拖天球 = 转地球 = 拨时间。
释放时根据拖拽轨迹算一个惯性时间率(`:682-693`)。

滚轮配修饰键也能拨时间(`:546-577`):Ctrl+滚轮 = 分钟,Ctrl+Shift = 小时,
Ctrl+Alt = 天,Ctrl+Alt+Shift = 年。

**对我们的建议**:我们的天空图卫星位置由 SGP4 实时算,时间轴主要是"回看过境"。
可借鉴:水平拖拽(按住某修饰键)= 拨动时间轴,卫星随之移动。当前先不实现,留接口。

---

## 5. 选中高亮(视觉反馈)

选中后 Stellarium 的反馈:
- `StelObjectMgr::setSelectedObject` 发 `selectedObjectChanged` 信号
  (`src/core/StelObjectMgr.cpp:549` 起)。
- 选中标记(十字光环)由 `StelSkyDrawer` 绘制,脚本可 `setSelectedObjectMarkerVisible`
  控制(`StelMainScriptAPI.hpp:573`)。
- 中键选中对象会自动 `moveToObject` 居中并开 tracking(`StelMovementMgr.cpp:735-736`)。

我们的 `SkyInteractionHandler.on_object_picked(obj)` 是钩子,宿主 widget 可在里面
画光环/信息面板;`on_view_changed()` 触发重绘。

---

## 6. Stellarium 脚本 API 方法清单

`StelMainScriptAPI`(`src/scripting/StelMainScriptAPI.hpp`)暴露给脚本的视角/选时/
选对象相关方法(行号为声明行):

| 方法 | 行号 | 作用 |
|---|---|---|
| `setJDay(double)` / `getJDay()` | :100 / :103 | 设置/读取儒略日 |
| `setDate(str, spec, isDT)` | :135 | 按 ISO/相对量设时间 |
| `setTimeRate(double)` / `getTimeRate()` | :198 / :201 | 时间流速(倍速,负=倒放) |
| `setRealTime()` | :209 | 回到真实时间 |
| `selectObjectByName(name, pointer)` | :254 | 按英文名选天体(""=取消) |
| `getObjectInfo(name)` | :334 | 取天体位置/星等等字典 |
| `getSelectedObjectInfo()` | :338 | 取当前选中对象信息 |
| `getViewAltitudeAngle()` | :413 | 当前视心仰角(度) |
| `getViewAzimuthAngle()` | :418 | 当前视心方位角(度) |
| `getViewRaAngle()` / `getViewDecAngle()` | :423 / :428 | 视心赤经/赤纬 |
| `moveToObject(name, duration, shift)` | :442 | 跳转并跟踪天体 |
| `moveToSelectedObject(duration)` | :447 | 跳转到已选对象 |
| `moveToAltAzi(alt, azi, duration)` | :455 | 跳转到指定地平坐标 |
| `moveToRaDec(ra, dec, duration)` | :463 | 跳转到赤道坐标 |
| `moveToRaDecJ2000(ra, dec, duration)` | :470 | 跳转到 J2000 坐标 |
| `setObserverLocation(lon, lat, alt, ...)` | :497 | 设观测站位置 |
| `getScreenXYFromAltAzi(alt, azi)` | :787 | **地平坐标 → 屏幕像素**(正投影) |
| `setProjectionMode(id)` | :636 | 切换投影(Perspective/Stereographic/Fisheye/Orthographic…) |
| `setSelectedObjectMarkerVisible(bool)` | :573 | 选中光环开关 |
| `goHome()` | :982 | 回到默认视角 |
| `clear(state)` | :408 | 预设视图(natural/starchart/deepspace/galactic) |

> 注意 `:787` 的 `getScreenXYFromAltAzi` 是官方提供的"正投影"脚本接口——
> 这印证了我们把 `sky_to_screen` 作为一等函数的设计。

---

## 7. 对我们 PySide6 天空图的交互建议

1. **状态中心化**:只保留 `ViewState(center_az, center_alt, fov_deg)`,渲染只读它。
   所有交互(拖/滚/双击/键盘/未来的 API)都改这三个量,不引入屏幕偏移量
   (`_pan_x/_pan_y`)作为第二状态源——否则会和中心指向打架。当前
   `rf_sky_view.py` 里 `_zoom/_pan_x/_pan_y/_rotation` 是两套状态,建议逐步收敛到
   `ViewState`。
2. **缩放用乘法指数**(`fov *= exp(-k*steps/60)`),不要用加法,否则低倍下太灵、
   高倍下太钝。范围 30°~180°。
3. **拖拽必须走反投影**(反投影前后两点求角位移),不要直接 `像素Δ→角度Δ`。
   后者在缩放后手感会错。
4. **拾取用角距离**而非像素距离:把点击点反投影成 (az,alt),遍历卫星找
   `angular_distance < 2°` 的。卫星数量小,暴力遍历即可,不必上空间索引。
5. **点击 vs 拖拽用 4px 阈值区分**(对照 `dragTriggerDistance`),否则轻点一下就误触发平移。
6. **仰角夹到 ±(90°-ε)**,避免看天顶时方位角跳变。
7. **goto 加缓动**:双击居中/选卫星居中时,照 `atan(4(2t-4))/atan(4)/2+0.5`
   (`StelMovementMgr.cpp:1213`)做 1~2 秒平滑动画,别瞬移。
8. **时间拖拽留接口**:未来可做"水平拖=拨时间轴,卫星随 SGP4 移动",
   参考 `dragView` 的 `dragTimeMode` 分支(`:1661`)。
9. **选中反馈**:点中卫星后画光环 + 右下角信息面板(az/el/频率/信号强度),
   对应 `setSelectedObjectMarkerVisible`。

---

## 8. 源码引用汇总(≥10 处)

1. `src/core/StelMovementMgr.cpp:1576` — `panView(deltaAz, deltaAlt)` 平移数学。
2. `src/core/StelMovementMgr.cpp:1626-1635` — az/alt 更新与仰角夹取(防天顶奇异)。
3. `src/core/StelMovementMgr.cpp:1659` — `dragView` 反投影两点求角位移拖拽。
4. `src/core/StelMovementMgr.cpp:1684` — `panView(az2-az1, alt1-alt2)` 的 y 翻转方向。
5. `src/core/StelMovementMgr.cpp:537` — `handleMouseWheel` 滚轮事件入口。
6. `src/core/StelMovementMgr.cpp:580-582` — `exp(-mouseZoomSpeed*numSteps/60)` 指数缩放。
7. `src/core/StelMovementMgr.cpp:84-85,172` — minFov/maxFov 默认范围。
8. `src/core/StelMovementMgr.hpp:466-474` — `setFov` 的 `qBound(minFov,f,maxFov)` 夹取。
9. `src/core/StelMovementMgr.cpp:1472` — `moveToAltAzi` goto 起点/终点记录。
10. `src/core/StelMovementMgr.cpp:1213` — goto 缓动函数 `atan(smooth(2c-4))/atan(4)/2+0.5`。
11. `src/core/StelMovementMgr.cpp:1238-1247` — goto 最短路径解卷绕与球面线性插值。
12. `src/core/StelMovementMgr.cpp:614` — `handleMouseClicks` 鼠标事件分发。
13. `src/core/StelMovementMgr.cpp:130` — `dragTriggerDistance=4` 拖拽阈值。
14. `src/core/StelMovementMgr.cpp:714-716` — 点击释放时 `findAndSelect`。
15. `src/core/StelMovementMgr.cpp:1661-1674` — `dragTimeMode` 拖天球拨时间。
16. `src/core/StelProjector.cpp:604-616` — `unProject` 反投影三步(像素→归一化→backward→逆模型视图)。
17. `src/core/StelProjector.cpp:172` — `pixelPerRad` 与 FOV 的关系。
18. `src/core/StelObjectMgr.cpp:36` — `searchRadiusPixel=25` 拾取半径。
19. `src/core/StelObjectMgr.cpp:461-508` — `cleverFind(v)` 候选收集与"距离+优先级"选择。
20. `src/core/StelObjectMgr.cpp:516-532` — `cleverFind(x,y)` 屏幕反投影入口。
21. `src/scripting/StelMainScriptAPI.hpp:442` — `moveToObject` 脚本 API。
22. `src/scripting/StelMainScriptAPI.hpp:455` — `moveToAltAzi` 脚本 API。
23. `src/scripting/StelMainScriptAPI.hpp:787` — `getScreenXYFromAltAzi` 正投影脚本接口。

---

## 9. 验收对照

- [x] `python3 -c "from mbdsdr_ai.sky_interaction import *; print('ok')"` 通过。
- [x] 正/反投影往返误差 < 1 像素(实测 0.000000 px,纯数学可逆)。
- [x] 本报告源码引用 23 处(≥10)。
- [x] `sky_interaction.py` 代码内标注 Stellarium 源文件:行号 ≥3 处(实际 15+ 处)。
- [x] FOV 范围夹取 30°~180°;拖拽阈值 4px;指数缩放。
