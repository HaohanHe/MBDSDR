# Stellarium Web Engine — 交互数学与源码笔记（为移植 rf_sky_view.py 准备）

> 实测环境：headless chromium + CDP 驱动 `stellarium-web-engine.html`（wasm 本地构建）。
> 上游源码根：`stellarium-web-engine/src/`（下文行号均相对于此目录）。
> 同时纳入用户在 stellarium-web.org 亲自实测的交互数据（见末尾"用户实测 UI 规格"）。

---

## 0. 构建坑（本地 build 缺漏，必须打补丁）

本地 `build/stellarium-web-engine.js` 的 `assignWasmExports` 只把 wasm 导出挂到闭包变量，**漏挂了 `Module._free` / `Module._malloc`**（`build/stellarium-web-engine.js` 单行压缩，对应源码 `pre.js:37-38` 附近）。而 JS 包装层（`a2tf`/`_call`/`convertFrame`/`lookAt`）调用的是 `Module._free`/`Module._malloc`，导致：

- `onRuntimeInitialized` 执行到 `Module.observer = Module.core.observer`（pre.js:45）时，`SweObj._call` 内部 `Module._free(cret)` 抛 `TypeError: Module._free is not a function`，onReady 永不触发。

CDP 侧补丁（`Page.addScriptToEvaluateOnNewDocument`，在 `StelWebEngine(opts)` 调用前拦截 opts）：
```js
opts.translateFn = null;                 // 关掉翻译桥，避免它调 Module._malloc
opts._free = function(ptr){};             // no-op（少量泄漏可接受）
opts.onReady = function(mod){
    window.__stel = mod;
    mod.setFont = function(){ return Promise.resolve(); };  // 跳过字体 fetch+_malloc 写堆
    return origReady.apply(this, arguments);
};
```
打补丁后 onReady 正常触发，星空/地景/大气渲染正常。**移植 rf_sky_view.py 不涉及 wasm，仅作记录。**

---

## 1. 投影：方位/仰角 → 屏幕坐标

### 1.1 坐标系约定（`src/js/pre.js` + `src/frames.h`）
- 天球方向用单位 3D 向量 `v=[x,y,z]`。
- `c2s(v)`（pre.js:264）→ `[theta, phi]`：
  - `theta = atan2(y, x)`（方位角，0~2π）
  - `phi   = atan2(z, sqrt(x²+y²))`（仰角，-π/2~π/2）
- `s2c(theta, phi)`（pre.js:274）→ `[cosθ·cosφ, sinθ·cosφ, sinφ]`
- `anp(a)` 归一化到 `[0,2π)`；`anpm(a)` 归一化到 `[-π,π)`（pre.js:280-291）。
- 实测：`c2s([1,0,0])=[0,0]`，`s2c(0,0)=[1,0,0]`，`anp(-1)=5.283`。

### 1.2 视图投影（`src/projections/proj_perspective.c`）
地面观星用 **透视投影（pinhole camera）**：
- `project()`/`backward()` 是恒等（proj_perspective.c:20-30）——天球→相机的旋转变换在 frame 链里做。
- FOV 分配（proj_perspective.c:32-42）：`fovy` 是垂直视场；宽屏时 `fovx = 2·atan(tan(fovy/2)·aspect)`，窄屏时 `fovy = 2·atan(tan(fov/2)/aspect)`。
- 投影矩阵（proj_perspective.c:44-48）：`mat4_inf_perspective(mat, fovy_deg, aspect, clip_near=5·DM2AU)`，无限远视锥。

### 1.3 屏幕坐标变换（`src/projection.c:60-98`）
`project_to_win(proj, view[3], out[3])`：
```
p = project(view);            // 透视下恒等
p = proj.mat · [p, 1]         // 4x4 投影矩阵
p /= p[3]                     // 透视除法
out[0] = ( p[0] + 1)/2 · W    // NDC → 窗口像素
out[1] = (-p[1] + 1)/2 · H    // 注意 y 翻转
out[2] = ( p[2] + 1)/2
```
`unproject`（projection.c:86-98）是逆运算：屏幕像素 → NDC → `mat^-1` → view 向量。

**rf_sky_view.py 对应实现**：
```python
def azalt_to_xy(az, alt, fov_deg, w, h, view_az, view_alt):
    # 1) az/alt -> 方向向量（local ENU：x=东? 此处用 SWE 约定 z=天顶）
    # 2) 绕 yaw/pitch 旋转到 view 坐标系
    # 3) pinhole:  x_ndc = x/z · f, y_ndc = y/z · f
    # 4) 像素: px=(x_ndc+1)/2*W, py=(1-y_ndc)/2*H
    f = 1.0 / math.tan(math.radians(fov_deg)/2)
    ...
```

---

## 2. 星表渲染：星等 → 亮度/尺寸

### 2.1 照度公式（`src/core.c:696-708`）
```
E(lux) = 10.7646e4 / R2AS² · 10^(-0.4·vmag)
```
其中 `R2AS = 180/π·3600`（弧度→角秒）。即 Pogson 定律：每等差 2.512 倍照度。

### 2.2 点尺寸与亮度（`src/core.c:369-430`，`core_get_point_for_mag`）
```
lum_app = mag_to_lum_apparent(vmag, surf=0)   # 见 core.c:719
ld      = tonemapper_map(lum_app)             # 人眼适应/色调映射
r       = s_linear · ld^(s_relative/2)         # 屏幕半径（像素）
```
- `s_linear = (star_linear_scale + 3/11 - bortle/11) · star_scale_screen_factor`（core.c:375）
- `s_relative = star_relative_scale`（实测默认 1.1）
- 实测默认：`star_linear_scale=0.8, star_relative_scale=1.1, bortle_index=3, exposure_scale=2`。
- 太暗的星 `r < r_skip` 直接不画；`r < r_min` 时亮度按 `((r-r_skip)/(r_min-r_skip))²` 压暗（core.c:427-429）。
- 颜色由 `bv_to_rgb(bv)`（色指数 B-V 定色温，stars.c:693）。
- 星表是 HiPS `.eph` 瓦片（`test-skydata/stars/Norder*/Dir*/Npix*.eph`），按视场 order 加载，`max_vmag=7.0`（properties 文件）。

**rf_sky_view.py 对应**：
```python
def star_size_lum(vmag, bortle=3):
    E = 10.7646e4 / (206265**2) * 10**(-0.4*vmag)
    # tonemapper 简化：gamma
    ld = max(0, E**0.4)   # 近似
    r = 0.8 * (3/11 - bortle/11 + 3/11) * ld**0.55
    return r, ld
```

---

## 3. 网格绘制（方位/赤道）

`src/modules/lines.c`：
- 方位网格 `id="azimuthal"` → `frame=FRAME_OBSERVED`（地平坐标），lines.c:97-99。
- 赤道网格 `id="equatorial"` → `frame=FRAME_ICRF`（J2000 赤道），lines.c:105-107。
- 网格按经纬度线画：方位网格 = 等方位射线 + 等高度圈；赤道网格 = 等赤经射线（时圈）+ 等赤纬圈。
- 实测 API：`core.lines.azimuthal.visible = true/false`；`core.lines.equatorial.visible = true/false`。
- 网格线沿大圆细分绘制（`line_mesh.c`），与投影函数一致，所以透视下是曲线。

**rf_sky_view.py**：方位网格每 30° 一条方位线（从 N=0° 起顺时针），每 15°/30° 一条等高圈；赤道网格每 1h 时圈、每 10° 赤纬圈，经 FRAME 变换后投到屏幕。

---

## 4. 地平线 / 地景

- `core.landscapes.visible` 开关（实测）。
- 地景是 Guereins 乡村场景（`test-skydata/landscapes/guereins`），作为贴在地面的多边形/全景投影。
- 地平线以上画天空，以下被地景遮罩；大气渐变只在仰角>0 区域累积。

---

## 5. 大气（Preetham 解析模型）

`src/modules/atmosphere.c:69-126`，是 Preetham 天空亮度解析近似（不是完整散射积分）：
- `thetaS = acos(sun_pos[2])` 太阳天顶角（atmosphere.c:81）。
- 用一组关于 `thetaS`（及大气浑浊度 T）的三次多项式算天顶/地平亮度系数 `zx, zy`（atmosphere.c:87-95）。
- 任意方向天空亮度用 Perez 分布函数 `F(lam, theta, gamma)`（atmosphere.c:60-67），`gamma` 是该方向与太阳夹角。
- 景观亮度 `landscape_lum = smoothstep(0, 0.5, sun_alt) · 5000`（atmosphere.c:118）。
- 光污染 `= max(0, 0.0004·(bortle-1)^2.1)`（atmosphere.c:124）。
- 太阳在地平线下时切换到 night sky 近似。

**rf_sky_view.py 简化**：白天按太阳仰角做地平线渐变（蓝→橙），夜间纯黑+银河带；不必上完整 Preetham，先用线性渐变。

---

## 6. 时间内核

- `observer.tt` 是 **MJD（儒略日，天）**。实测 `tt≈61309.58`，对应 `new Date((tt+2400000.5-2440587.5)*86400000)` = 2026-09-26。
- 转换（pre.js:92-98）：
  ```
  MJD2date(v) = (v + 2400000.5 - 2440587.5) · 86400000  ms
  date2MJD(d) = d/86400000 - 2400000.5 + 2440587.5
  ```
- 时间流动（navigation.c:51-55）：`tt += dt · time_speed / 86400`，即 `time_speed` 单位是"秒/现实秒"，`/86400` 转成天。实测 `time_speed=0`（暂停），设大值星空快速转动。
- 坐标链（`src/frames.c:55-180`）：`ICRF(J2000) → CIRS → JNOW → OBSERVED_GEOM → OBSERVED(地平) → MOUNT → VIEW`，每步乘一个旋转矩阵（岁差/章动/光行差/周日自转/大气折射）。周日自转由 GMST（地方恒星时）驱动，ERFA 库 `eraApco`/`eraPnm06a` 完成（observer.c:205-221）。
- `convertFrame(obs, 'ICRF','OBSERVED', v4)`（pre.js:331）做完整坐标转换，`c2s` 再取角坐标。

**rf_sky_view.py**：自己实现 GMST：
```python
def gmst(jd_ut1):
    t = (jd_ut1 - 2451545.0)/36525.0
    return 280.46061837 + 360.987700536*(jd_ut1-2451545.0) + 0.000387933*t**2 - t**3/38710000
```
本地恒星时 = GMST + 经度。赤道坐标 → 地平坐标：先由 LST-RA 得时角 H，再
```
sin(alt) = sin(dec)·sin(lat) + cos(dec)·cos(lat)·cos(H)
cos(az) = (sin(dec) - sin(alt)·sin(lat)) / (cos(alt)·cos(lat))
```

---

## 7. 搜索 / 选中 / moveTo 缓动

### 7.1 搜索（`src/core.c:1001-1009`）
`core_search(query)` 遍历所有模块，每个模块 `listObjs` 过滤名字匹配，返回第一个命中。JS 侧 `stel.getObj("Sirius")` → `new SweObj(core_search("Sirius"))`。
实测：
- `getObj("Sirius")` → "Sirius" ✓
- `getObj("Polaris")`/`"Vega"` ✓
- `getObj("NORAD 25544")` → "NAME ISS" ✓（卫星）
- `getObj("Moon")`/`"Mars"` 在本次离线数据下返回 null（SSO 行星数据未加载/在地平线下），但天空里 Mars 标签仍渲染。

### 7.2 选中 + 居中（`src/core.c:860-897`）
- `core_lookat(pos, duration)`：把当前 yaw/pitch 转成四元数 `src_q = Rz(yaw)·Ry(-pitch)`，目标方位 `dst_q = Rz(az)·Ry(-alt)`，记录起止时间（core.c:873-884）。
- `core_point_and_lock(obj, duration)`：先 `obj_set_attr(core,"selection"=obj)`，再取目标 OBSERVED 位置调 lookat，并设 `move_to_lock=true`（core.c:888-897）。
- 缓动（navigation.c:89-104）：每帧 `t = smoothstep(src_time, dst_time, clock)`，然后 `quat_slerp(src_q, dst_q, t, q)` 插值，再把 q·[1,0,0] 转回 yaw/pitch。选中后持续跟踪目标运动（navigation.c:116-123）。
- JS：`stel.pointAndLock(obj, 1.5)`（pre.js:377），`stel.lookAt([x,y,z], dur)`（pre.js:358）。

---

## 8. 缩放 / 拖拽

### 8.1 缩放（`src/navigation.c:149-175`）
- 滚轮：`core_on_zoom(k,x,y)` → `core.zoom` 累积；每帧 `fov *= (1 + 0.05·(-zoom))^(dt·60)`（navigation.c:167-168），`ZOOM_FACTOR=0.05`。
- `stel.zoomTo(fov_rad, dur)`：`core_zoomto`（core.c:900），用 `smoothstep` 插值 fov。实测 `zoomTo(20°)` 后 `core.fov≈0.349 rad=20°` ✓。
- 范围：`CORE_MIN_FOV = 1/3600 °`（core.h:28），透视最大 180°，UI 最大 120°。
- 用户实测 FOV：120° → 37.2° → 98.7°，右上角常驻 "FOV xx.x°"。

### 8.2 拖拽平移（`src/modules/movements.c:25-61`）
- `screen_to_mount`：把屏幕像素 `unproject` 成 view 方向，再 `convertFrame(VIEW→MOUNT)`。
- `on_pan`：按下时存 `start_pos`；拖动中把当前屏幕点 unproject 成 mount 方向，两者各自 `vec3_to_sphe` 成 (az,alt)：
  ```
  yaw   += start_az - current_az
  pitch += start_alt - current_alt
  pitch  = clamp(pitch, -π/2, +π/2)
  ```
  这是"抓住天空拖动"（被拖的天点保持在光标下）。
- CDP 侧用 `Input.dispatchMouseEvent` 序列模拟 mousedown/mousemove/mouseup。

---

## 9. stel API 实测清单（完整列表）

顶层 `stel`（= emscripten Module）：
- 数学：`c2s, s2c, anp, anpm, a2tf, a2af, convertFrame, ccall, cwrap`
- 导航：`lookAt([x,y,z],dur)`, `pointAndLock(obj,dur)`, `zoomTo(fov_rad,dur)`, `getObj(name)`, `getModule(path)`
- 事件：`on('click',cb)`, `change(cb)`, `onValueChanged(cb)`
- 其他：`setFont`, `createObj`, `calendar`, `otypeToStr`, `designationCleanup`, `MJD2date`, `date2MJD`

`stel.core` 子对象：
- 状态：`observer, fov, selection, lock, time_speed, projection, fps, progressbars, zoom, mount_frame`
- 渲染参数：`bortle_index, display_limit_mag, star_linear_scale, star_relative_scale, exposure_scale, flip_view_vertical/horizontal`
- 模块开关：`atmosphere.visible, landscapes.visible, milkyway.visible, dss.visible, dsos.visible, stars.visible, constellations.visible, cardinals.visible`
- 网格：`lines.azimuthal.visible, lines.equatorial.visible, lines.meridian.visible, lines.ecliptic`
- 数据源：`stars, planets, moons, comets, minor_planets, satellites, dsos, skycultures`

实测值：`fov=0.873rad(50°)`, `bortle=3`, `star_linear_scale=0.8`, `star_relative_scale=1.1`, `exposure_scale=2`, `time_speed=0`, `observer.lat=0.437rad(25.07°)`, `lon=2.121rad(121.5°)`。

---

## 10. 用户实测 UI 规格（stellarium-web.org，照搬到 rf_sky_view.py）

来自用户亲自在浏览器操作 01-06 截图 + 口述：

### 10.1 渲染
- WebGL 天空，分级星点（星等→亮度/大小）、银河带纹理、亮星/行星文字标签（Vega/Capella/Sirius/Canopus/Alpha Centauri/Uranus/Mars）、近地平线大气渐变、地景剪影、方位罗盘 N 红色。

### 10.2 缩放
- 滚轮上=放大（FOV 减小）、下=缩小；实测 FOV 120°→37.2°→98.7°。右上角常驻 "FOV xx.x°"。
- 选中天体后出现圆形 +/- 缩放钮和链接（居中跟踪）钮。

### 10.3 搜索下拉（重点照做）
- 顶部 Search 输入 "iss" → 分类结果下拉，每条带类型图标 + 两行（名称/类型）。
- 实测顺序：
  1. **ISS (International Space Station)** — Space Station（空间站图标）
  2. **ISS (NAUKA)** — Artificial Satellite（卫星图标）
  3. **IS Sge / IS Sco / IS Sct / IS Ser / IS Sgr** — Variable/Long-Period Variable Star（恒星图标）
- 即搜索结果按"空间站/人造卫星/恒星"分类、图标区分。

### 10.4 选中信息卡字段（重点，照此实现）
点 ISS 后居中并在天空打 "International Space Station" 标记，左上角信息卡：
- 标题：**NORAD 25544**，副标题 **Space Station**（带类型图标）
- **Also known as:** ISS · ISS (ZARYA) · International Space Station · COSPAR 1998-067A
- **Magnitude** 99.00（不可见/未算出时给占位，**不编**）
- **Distance** 11890.93 km
- **Ra/Dec** 08h 41m 22.2s  -40°33'10.7"
- **Az/Alt** 132°17'04.8"  -63°39'03.7"（Alt 为负 = 在地平线下）
- **Visibility:** Rise ..:..  Set ..:..

**rf_sky_view.py 卫星信息卡照此**：NORAD/COSPAR/别名、距离、Ra/Dec、Az/Alt、升落时间；天体在地平线下时 Alt 显负值、Magnitude 无值显占位，绝不造假。
