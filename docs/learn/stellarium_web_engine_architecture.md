# Stellarium Web Engine 架构研究报告

> **声明**：本报告仅做架构与数学/接口设计学习，**不移植 web-engine 代码**。我们是 PySide6 原生桌面项目，目标是借鉴其坐标变换管线、模块注册机制、属性化 API 设计，而非引入 WebAssembly/浏览器套壳。

- 研究对象：`stellarium-web-engine`（C 内核 + Emscripten/WASM + Vue 前端）
- 对比对象：`repos/stellarium`（Stellarium C++ 桌面版）
- 研究日期：2026-09-25

---

## 1. 整体架构（文字架构图）

```
┌──────────────────────────────────────────────────────────────────────┐
│  浏览器 / HTML 页面（Vue.js + Vuetify，apps/web-frontend）          │
│                                                                      │
│   Vue 组件 (App.vue, gui.vue, ...)                                   │
│        │  直接读写 SweObj 属性（响应式）                              │
│        ▼                                                             │
│   Module.core / Module.observer / Module.getObj(...)                │
│   Module.convertFrame / Module.lookAt / Module.pointAndLock          │
│   Module.onValueChanged(callback)  ←── C 端 module_changed 信号      │
└──────────────────────────────┬───────────────────────────────────────┘
                               │  Emscripten cwrap / ccall / addFunction
                               │  (JS ↔ C 边界，全在 WASM 内存内)
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  WebAssembly 模块 (stellarium-web-engine.wasm + .js glue)           │
│  由 SConstruct 用 emcc 编译，MODULARIZE=1 EXPORT_NAME=StelWebEngine │
│  (SConstruct:133)                                                    │
│                                                                      │
│  src/js/pre.js   — 运行时初始化、帧常量、convertFrame/lookAt 封装    │
│  src/js/obj.js   — SweObj 类：把 C 对象动态暴露为 JS 属性/方法        │
│  src/js/canvas.js — requestAnimationFrame 主循环、鼠标/触摸转发       │
│  src/js/geojson.js — GeoJSON 图层对象封装                            │
└──────────────────────────────┬───────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────┐
│  C 内核（src/，gnu11，零依赖到浏览器，全部编译进 WASM）               │
│                                                                      │
│  core_t (core.c)  ── 全局状态、主循环、渲染调度                       │
│    ├── observer_t (observer.c) ── 时间/位置/旋转矩阵缓存             │
│    ├── modules/  ★ 所有天体模块（stars/planets/satellites/...）      │
│    │     每个模块 = 一个 obj_klass_t，OBJ_REGISTER 自注册             │
│    ├── frames.c  ── 9 参考系变换管线（ASTROM→ICRF→CIRS→...→VIEW）    │
│    ├── projection.c + projections/ ── 球面→屏幕投影                   │
│    ├── algos/    ── 纯天文算法（erfa 之上的补充）                     │
│    ├── painter.c / render_gl.c ── OpenGL 2.0 绘制抽象                │
│    └── ext_src/erfa/ ── ERFA（SOFA 开源版）坐标/时间基准             │
│                                                                      │
│  数据来源：addDataSource({url,key}) 从 HTTP 拉取瓦片/星表/TLE         │
│           （stars/、skycultures/、dso/、surveys/、tle_satellite...）  │
└──────────────────────────────────────────────────────────────────────┘
```

**关键交互方式**：不是 HTTP JSON API，而是 **WebAssembly 内存内直接调用**。前端通过 `cwrap`/`ccall` 调 C 函数，C 对象的属性通过 `obj_call_json_str` 以 JSON 字符串双向序列化（`src/js/obj.js:306-323`）。C 端属性变化时通过 `addFunction` 注册的回调通知 JS（`src/js/obj.js:390-401`），JS 再把它映射成 Vuex 响应式树（`sw_helpers.js:42-46`）。

---

## 2. C 内核 ↔ 前端：绑定机制细节

### 2.1 构建产物
- 构建命令：`make js` → `emscons scons -j8 mode=release`（`Makefile:5-6`）
- 编译选项：`-s MODULARIZE=1 -s EXPORT_NAME=StelWebEngine`，`--pre-js src/js/pre.js,obj.js,geojson.js,canvas.js`（`SConstruct:133-147`）
- 导出的运行时方法：`ccall/cwrap/addFunction/setValue/getValue/UTF8ToString` 等（`SConstruct:111-129`）
- 渲染后端：WebGL2（`-s USE_WEBGL2=1`，`SConstruct:143`），C 端用 OpenGL ES 2.0 风格 API

### 2.2 启动流程
1. 页面加载 `stellarium-web-engine.js`，调用 `StelWebEngine({wasmFile, canvas, onReady})`
2. WASM 加载完 → `Module.onRuntimeInitialized`（`pre.js:16`）→ 创建 GL context → `_core_init(0,0,1)`（`pre.js:43`）
3. `core_init` 创建 `core` 对象，遍历所有 `OBJ_MODULE` 类并实例化为 core 的子节点（`core.c:244-248`）
4. `onReady(stel)` 回调里：`stel.core.stars.addDataSource({url})` 等挂载数据（`simple-html:212-222`）
5. `canvas.js` 启动 `requestAnimationFrame` 循环：每帧调 `_core_update()` + `_core_render(w,h,dpr)`（`canvas.js:45-46`）

### 2.3 前端如何"接收和绘制天体"
**前端不绘制天体，C 内核直接画到 WebGL canvas。** Vue 只负责：
- DOM 层面的 UI 按钮、对话框、信息卡片
- 通过 `stel.onValueChanged(path, value)` 把 C 端属性变化同步进 Vuex（`sw_helpers.js:42-46`）
- 选中物体后用 `obj.getInfo('radec'/'vmag')` 查数据，自己格式化 RA/Dec/Az/Alt 文本（`simple-html:248-313`）

天体的点、线、多边形、天空图全部由 C 端 `render_gl.c`（70KB）通过 painter 抽象直接提交 GL 命令。这与"前端拿 JSON 数据自己画"完全不同——**渲染主权在 C 内核**。

---

## 3. 核心模块清单（src/ 下每个 .c 文件职责）

### 3.1 顶层核心
| 文件 | 职责 |
|---|---|
| `core.c` (34KB) | 全局核心对象 `core_t`：主循环 `core_update`/`core_render`、模块调度、FOV/时间动画、星等→像素亮度换算、眼睛自适应 tonemapper |
| `core.h` | core_t 结构与全局 API（`core_get_module`、`core_search`、`core_lookat`、`core_set_time` 等） |
| `obj.c`/`obj.h` | 基类 `obj_t`：引用计数、vtable `obj_klass_t`、属性系统 `attribute_t`、`obj_call_json` |
| `module.c`/`module.h` | 模块树：`module_add`/`module_remove`/`module_list_objs`/`module_get_tree`/全局变更监听 |
| `frames.c`/`frames.h` | **9 参考系变换管线**（见第 4 节） |
| `observer.c`/`observer.h` | 观测者状态：经纬度/海拔/时间/指向，预计算所有 3×3 旋转矩阵 |
| `projection.c`/`projection.h` | 投影抽象基类：`projection_t`、`project_to_win`/`unproject` |
| `painter.c`/`painter.h` | 绘制状态机：颜色/线宽/clip/裁剪球，封装 GL |
| `render_gl.c` (70KB) | 真正的 WebGL 渲染：点精灵、线、四边形、文字、tonemap 后处理 |
| `navigation.c` | 鼠标拖动→yaw/pitch 惯性运动 |
| `events.c` | 日出日落/相位事件计算 |
| `telescope.c` | 望远镜光力/放大率模型 |
| `tonemapper.c` | 相机响应曲线（LUT） |
| `skybrightness.c` | 天空亮度模型（Bortle 等级） |
| `hips.c`/`hips.h` | HiPS 层级瓦片加载（天空 survey 切片） |
| `line_mesh.c`/`libtess2.c` | 线段网格、多边形三角化 |
| `areas.c` | 屏幕可点击区域注册（点选拾取） |
| `labels.c`/`labels.h` | 3D 标签排队/避让 |
| `layer.c` | 用户自定义图层（`createLayer`） |
| `designation.c` | 天体编号解析/清理 |
| `mpc.c` | MPC 小行星轨道根数解析 |
| `sgp4.cpp`/`sgp4.h` | SGP4 卫星轨道（封装 ext_src/sgp4） |
| `swe.c`/`swe.h` | Swiss Ephemeris 封装（行星位置） |
| `eph-file.c` | JPL DE 星历文件读取 |
| `system.c`/`system.h` | 平台抽象（时间、目录、翻译回调） |
| `assets.c` | 编译进二进制的资源（字体/着色器/符号图） |
| `symbols.c` | 星座符号 |
| `skyculture.c` | 天空文化（星座命名/图像） |
| `otypes.c` | Simbad 天体类型码 |
| `uv_map.c` | UV 坐标映射 |
| `geojson_parser.c` | GeoJSON 解析 |
| `events.c` | 事件 |
| `tests.c` | 单元测试框架 |

### 3.2 src/algos/（纯天文算法，只依赖 erfa）
| 文件 | 职责 |
|---|---|
| `algos.h` | 算法总头文件 |
| `healpix.c` | **HEALPix 网格化**：nest/xyf 互转、像素→向量、邻域、包围帽（`algos.h:18-69`） |
| `moon.c` | 月球位置半解析理论（`moon_pos`，`algos.h:80`） |
| `pluto.c` | 冥王星位置（`pluto_pos`，`algos.h:93`） |
| `deltat.c` | ΔT 计算（`deltat`，`algos.h:103`） |
| `utctt.c` | UTC↔TT 时间换算 |
| `refraction.c` | 大气折射（`refraction`/`refraction_inv`/`refraction_prepare`，`algos.h:152-169`） |
| `orbit.c` | 开普勒轨道：`orbit_compute_pv`/`orbit_elements_from_pv`（`algos.h:243-283`） |
| `bv_to_rgb.c` | B-V 色指数→RGB |
| `l1.c` | 伽利略卫星 L1.2 理论 |
| `tass17.c` | 土星卫星 TASS1.7 模型 |
| `gust86.c` | 天王星卫星 GUST86 模型 |
| `satrings.c` | 土星环朝向 |
| `cst-boundaries.c` | 星座边界数据 |
| `format.c` | 角度/时间/距离格式化 |

### 3.3 src/modules/（自注册模块，每个一个 obj_klass_t）
`atmosphere.c`（大气）、`cardinal.c`（方位角 N/E/S/W）、`circle.c`、`comets.c`（彗星）、`constellations.c`（星座线/图）、`coordinates.c`、`debug.c`、`drag_selection.c`、`dso.c`（深空天体）、`dss.c`（DSS 巡天图）、`geojson.c`、`landscape.c`（地面景观）、`lines.c`（坐标网格）、`labels.c`、`meteors.c`（流星）、`milkyway.c`（银河）、`minorplanets.c`（小行星）、`movements.c`、`photos.c`、`planets.c`（行星，52KB）、`pointer.c`、`satellites.c`（人造卫星，TLE/SGP4）、`skycultures.c`（天空文化）、`stars.c`（恒星，HiPS 瓦片）。

### 3.4 src/projections/
`proj_perspective.c`、`proj_stereographic.c`、`proj_mercator.c`、`proj_hammer.c`、`proj_mollweide.c`（`projection.h:28-36`）。

---

## 4. 坐标变换实现（frames.c 变换链）

### 4.1 九个参考系（`frames.h:89-99`）
| 值 | 名称 | 含义 |
|---|---|---|
| 0 | FRAME_ASTROM | 天体测量方向（视差/自行已改正，地心 GCRS 自然方向） |
| 1 | FRAME_ICRF | ICRS/J2000 赤道（星表基准） |
| 2 | FRAME_CIRS | 地球中间参考系（CIO 基） |
| 3 | FRAME_JNOW | 真赤道真春分点（经典 JNow） |
| 4 | FRAME_OBSERVED_GEOM | 几何地平（无折射） |
| 5 | FRAME_OBSERVED | 视地平（含大气折射） |
| 6 | FRAME_MOUNT | 支架坐标系（地平仪/赤道仪） |
| 7 | FRAME_VIEW | 视口方向（相机指向） |
| 8 | FRAME_ECLIPTIC | 黄道 |

### 4.2 正向变换链（`frames.c:55-122` `convert_frame_forward`）
按帧编号从小到大逐级施加，每步是一个 3×3 矩阵乘法或折射修正：

```
ASTROM(0)
  │  astrometric_to_apparent()  ← frames.c:60-62
  │    含光行差 eraAb + 太阳光线偏折 eraLdsun（frames.c:265-287）
  ▼
ICRF(1) ──(bias-precession-nutation bpn 矩阵)──▶ CIRS(2)
  │                                       frames.c:64-67
  │                                       astrom->bpn 来自 eraPnm06a（observer.c:205）
  ▼
CIRS(2) ──(方程的 origins eo，绕 Z 轴转 -eo)──▶ JNOW(3)
  │                                       frames.c:69-79
  ▼
CIRS(2) ──(ri2h：地球自转 + 极移 + 纬度)──▶ OBSERVED_GEOM(4)
  │                                       frames.c:88-92
  │                                       ri2h 构造见 observer.c:68-80
  ▼
OBSERVED_GEOM(4) ──(refraction 大气折射)──▶ OBSERVED(5)
  │                                       frames.c:95-111
  ▼
OBSERVED(5) ──(ro2m 支架旋转)──▶ MOUNT(6)
  │                                       frames.c:114-117
  ▼
OBSERVED(5) ──(ro2v 视口旋转)──▶ VIEW(7)
                                          frames.c:120-121
```

**设计要点**：
- 正向 `dest > origin` 走 `convert_frame_forward`，反向 `dest < origin` 走 `convert_frame_backward`（`frames.c:207-211`），反向是矩阵转置/逆的逆序，折射用 `refraction_inv`（`frames.c:140-156`）。
- ECLIPTIC(8) 是旁路：先转到 ICRF 再乘 `ri2e`/`re2i`（`frames.c:195-205`）。
- 4D 向量版 `convert_framev4`（`frames.c:218-229`）用第 4 分量 `w` 表示是否无穷远：`w==0` 是遥远恒星（归一化），`w==1` 是太阳系天体（带距离 AU）。这与 ERFA 的约定一致。

### 4.3 observer.c 中矩阵预计算（`observer.c:29-104`）
- `ri2h`（CIRS→水平）= 地球自转 `era Era00` 绕 Z 转 + 极移矩阵 + 纬度 -φ+π/2 绕 Y 转 + X 翻转（`observer.c:68-80`）
- `ro2v`（观测→视口）= `r2gl`（Z-up→Y-up OpenGL 约定）× `rdir`（roll/pitch/yaw）× 支架矩阵（`observer.c:51-59`）
- `rc2v`（ICRF→视口，无折射）= `bpn转置 × ri2h × ro2v`（`observer.c:91-93`）
- 整个 observer 状态用 XOR hash 缓存（`observer.c:106-127`），时间/位置没变就跳过重算；fast 模式只在 1 天内做线性外推（`observer.c:145-190`）。

### 4.4 与 Stellarium C++ 的对应
- C++ 的 `StelCore::FrameType`（`StelCore.hpp:78-92`）是扁平枚举：`FrameJ2000`≈ICRF、`FrameEquinoxEqu`≈JNOW、`FrameAltAz`≈OBSERVED_GEOM、`FrameHeliocentricEclipticJ2000`≈ECLIPTIC。
- web-engine 把 C++ 里散落在 `StelCore::matVsop87/...` 的旋转矩阵显式收敛成 observer.c 里 11 个具名矩阵（`observer.h:92-108`），并把折射从"模式开关"升级成独立的 FRAME_OBSERVED_GEOM→FRAME_OBSERVED 一步。这是更干净的管线化设计。

---

## 5. API 设计模式总结

### 5.1 对象系统：C 风格的动态属性绑定（核心亮点）
web-engine 不用固定 C ABI 导出函数表，而是在 C 里建了一个**带类型的动态属性树**，再通过 JSON 桥暴露给 JS：

- 每个类是一个静态 `obj_klass_t` 结构体（vtable），含 `init/render/update/get_info/list/add_data_source` 等函数指针（`obj.h:107-174`）。
- 每个属性在类的 `.attributes` 数组里声明，用 `PROPERTY(name, TYPE, MEMBER(...))` 宏映射到 C 结构体偏移（`obj.h:250,262`）。例如 observer：
  ```c
  PROPERTY(longitude, TYPE_ANGLE, MEMBER(observer_t, elong)),   // observer.c:339
  PROPERTY(utc, TYPE_MJD, MEMBER(observer_t, utc), .on_changed = on_utc_changed), // observer.c:344
  ```
- 类通过 `OBJ_REGISTER(klass)` 用 gcc `__attribute__((constructor))` 自注册到全局链表（`obj.h:498-501`）；core_init 遍历所有 `OBJ_MODULE` 标记的类并实例化（`core.c:244-248`）。
- 类型系统用 X-macro 定义（`obj_info.h:22-44`）：基础类型 `FLOAT/INT/BOOL/STRING/V3/V4`，语义类型 `ANGLE/MJD/MAG/DIST/OBJ/COLOR`。这让 JS 端能知道"这个数是角度还是米"并自动格式化。

### 5.2 JS 端 SweObj 代理（`src/js/obj.js`）
- WASM 就绪后，遍历 C 对象的属性表，用 `Object.defineProperty` 把每个属性变成 JS getter/setter（`obj.js:73-79`）。
- 读写都走 `obj_call_json_str(objPtr, attrName, jsonArgs)`（`obj.js:306-323`）：JS 把参数 `JSON.stringify`，C 端解析 JSON、操作属性、再把结果 JSON 序列化返回。
- 子对象也通过 defineProperty 暴露（`obj.js:88-94`），所以 `stel.core.stars.visible = true` 这种链式访问是真实的 C 属性树遍历。
- 全局路径 API：`Module.getValue("core.observer.longitude")` / `Module.setValue(path, value)`（`obj.js:412-430`），点分路径自动在 core 树里导航。
- 变更通知：C 端 `module_changed(obj, attr)` → 全局回调 `onObjChanged` → JS 遍历监听器（`obj.js:390-401`）。前端把它接成 Vuex 响应式（`sw_helpers.js:42-46`）。

### 5.3 查询接口分类（实际可用 API）

**Sky/天体查询**
- `stel.getObj("NAME Sun")` / `core_search`（`core.c:1001-1009`）：按编号搜天体
- `obj.getInfo('radec'|'vmag'|'distance'|'phase'|'radius')`（`obj_info.h:66-77`）：查天体信息
- `obj.getInfo('RADEC', obs)` 返回 4D 向量，再 `stel.convertFrame(obs,'ICRF','OBSERVED',v)` 转地平坐标（`simple-html:291-298`）
- `obj.computeVisibility({startTime,endTime})` 计算升降（`obj.js:252-269`，封装 `_compute_event`）
- `core.stars.listObjs(obs, maxMag, filter)` 枚举恒星（`obj.js:213-225`）
- `stel.core.search` / `target-search.vue` 组件对接远程 skysource API

**Satellite 查询**
- `core.satellites.addDataSource({url: tle_jsonl_gz, key:'jsonl/sat'})`（`satellites.c:78-86`）
- 每颗卫星是一个 `tle_satellite` 类实例，内部持 `sgp4_elsetrec_t`（`satellites.c:24-44`）
- JS 端 `stel.getObj("NORAD 25544")` 取 ISS，然后和普通天体一样 `getInfo('radec')`

**Time 查询**
- `stel.core.observer.utc = mjd` / `.tt = mjd`（`observer.c:342-345`，改 utc 自动同步 tt，反之亦然）
- `stel.core_set_time(tt_mjd, duration)` 带动画（`core.c:940-964`）
- `stel.core.time_speed = 0..N` 时间流速（`core.c:1047`）
- `stel.calendar({start,end,onEvent})` 批量算历法事件（`pre.js:189-238`）

**视角/导航**
- `stel.lookAt([x,y,z], sec)` / `stel.pointAndLock(obj, sec)` / `stel.zoomTo(fov, sec)`（`pre.js:358-395`）
- `stel.core.fov` / `.projection` / `.selection` / `.lock` 都是可读写属性（`core.c:1017-1048`）

**数据挂载**
- `module.addDataSource({url, key})` 是统一入口：每个模块自己决定怎么解析（`module.h:64`）。恒星/行星/彗星/卫星/景观/天空文化全用这一个方法挂 URL。

### 5.4 坐标投影 API
- `stel.convertFrame(obs, originFrame, destFrame, v4)` 是公开坐标变换入口（`pre.js:331-345`），frame 名用字符串 `'ICRF'|'JNOW'|'OBSERVED'|...`（`pre.js:293-304`）。
- 辅助数学函数直接挂在 Module 上：`c2s/s2c/anp/anpm`（`pre.js:264-291`）、`a2tf/a2af` 角度格式化（`pre.js:119-156`）。
- 投影本身在 C 端是 `projection_t` 多态类（`projection.h:56-86`），前端不直接调，只通过 `core.projection = PROJ_STEREOGRAPHIC` 切换。

---

## 6. 前端渲染方式

- **Canvas + WebGL2**：一个全屏 `<canvas id="stel-canvas">`（`App.vue:61`，`simple-html:110`），C 内核通过 Emscripten 的 GL 绑定直接绘制。
- Vue 组件全部是 DOM 覆盖层（工具栏、对话框、信息卡片），z-index 让 canvas 在最底（`App.vue:336` `#stel-canvas{z-index:-10}`）。
- 每帧：`core_update()` 更新所有模块状态 → `core_render(w,h,dpr)` 遍历模块树调 `obj_render`（`core.c:572-574`）。模块的 render 方法通过 painter 提交 GL 命令（点精灵画星、三角带画星座线、纹理四边形画 DSS/银河）。
- **HiPS 瓦片**：stars 和 dss/milkyway 模块用 HiPS 层级瓦片（`healpix.c`），`traverse_surface` 递归裁剪可见瓦片（internals.md:369-427），按需 HTTP 拉取，这就是"瓦片"机制。
- 选中拾取：`core_on_mouse` → `areas_lookup` + `labels_get_obj_at`（`core.c:135-146`），C 端维护屏幕可点区域表。

---

## 7. npm install / build 实际结果（真实记录）

环境：Node v22.23.2 / npm 10.9.8。

### 7.1 第一次 `npm install`（在 apps/web-frontend/）
**失败**，ERESOLVE 依赖冲突：
```
Found: vue@3.0.0  (package.json 声明 "^vue": "^3.0.0")
Could not resolve: peer vue@"^2.5.17" from vue2-leaflet@2.7.0
```
原因：`package.json` 把 vue 写成 `^3.0.0`，但整个代码库（`new Vue(...)`、vue-router@3、vuex@3、vuetify@2、vue2-leaflet）都是 **Vue 2** 写法。这是仓库自身的版本声明不一致。

### 7.2 `npm install --legacy-peer-deps`
**成功**，安装 1725 个包（大量 deprecation 警告，但装好）。此时 node_modules/vue 是 3.5.43。

### 7.3 第一次 `npm run build`
**失败**：
```
TypeError: Cannot read properties of undefined (reading 'extend')
  at vuetify/es5/mixins/binds-attrs/index.js:30
```
Vuetify 2.x 用 `Vue.extend`，在 Vue 3 下不存在。

### 7.4 诊断修复（仅为诊断，不改 package.json）
`npm install vue@2.7.16 --legacy-peer-deps --no-save` 后再 build。

### 7.5 第二次 `npm run build`
**Vue 源码编译通过**（sass deprecation 警告若干），但最终失败：
```
These dependencies were not found:
* @/assets/js/stellarium-web-engine.js
* @/assets/js/stellarium-web-engine.wasm
```

### 7.6 卡点结论
**WASM 桥接产物缺失**。`src/assets/js/` 目录只有一个 README，真正的 `stellarium-web-engine.js` / `.wasm` 必须由 `make js`（即 `emscons scons`）从 C 源码编译，依赖 **Emscripten SDK**（环境里 `which emcc` 为空，`EMSCRIPTEN_TOOL_PATH` 未设置）。SConstruct 第 98 行硬断言 `os.environ['EMSCRIPTEN_TOOL_PATH']` 必须存在。

**即：前端 Vue 代码本身可以构建，但没有预编译的 WASM 就跑不起来。** 要完整运行需要先装 Emscripten SDK（emsdk），`source emsdk_env.sh`，再 `make js`。我们不做这一步——因为本任务只学架构，不要求跑起来。

---

## 8. 对我们 PySide6 原生桌面项目的建议

> 前提：我们是 PySide6/Qt 原生桌面，**不引入 WebAssembly、不套浏览器**。以下只借鉴数学与接口设计思想。

### 8.1 强烈建议借鉴

1. **参考系管线化为单向矩阵链**（frames.c 模式）
   - 不要学 C++ StelCore 把矩阵散落在各个类里。学 web-engine：定义 7~9 个具名枚举帧，在 observer 里一次性预算所有相邻帧的 3×3 旋转矩阵，坐标变换就是"按帧编号排序，逐级乘矩阵 + 折射"。
   - 在 Python 里对应：一个 `Observer` 数据类持有 `np.ndarray` 矩阵（bpn/ri2h/ro2v/ro2m），一个 `convert_frame(obs, origin, dest, vec)` 纯函数。4D 齐次向量（第 4 分量表无穷远）的设计也值得照搬。

2. **ERFA 作为坐标/时间底座**
   - web-engine 几乎所有高精度变换都调 ERFA（`eraPnm06a`/`eraAper13`/`eraEors`/`eraLdsun`/`eraAb`），自己只补 ERFA 没有的月球/冥王星/卫星理论。
   - 我们在 Python 侧可直接用 `pyerfa`（ERFA 的 Python 绑定）或 `astropy`，不要自己写岁差章动。

3. **动态属性树 + 类型标注（obj_klass_t 模式）**
   - 这是最值得抄的接口设计：每个模块声明一个属性表（名字→类型→结构体偏移/setter），外部用 `core.observer.longitude = x` 或 `core.set_value("stars.visible", True)` 访问，属性变化自动发信号。
   - PySide6 里完美对应：用 `Q_PROPERTY` 声明带类型的属性 + `pyqtSignal` 通知。比 web-engine 的 JSON 桥更原生、零序列化开销。
   - 语义类型（ANGLE/MJD/MAG/DIST）的思路值得保留：让 UI 层知道一个 float 是角度还是米，自动用度分秒格式化。

4. **模块自注册机制**
   - `OBJ_REGISTER` 用 constructor 属性把模块挂到全局链表，core 启动时自动实例化所有 `OBJ_MODULE`。
   - Python 里对应：用 entry point 装饰器或基类 `__subclasses__()` 自动发现模块，避免 main 里手写一大串 `Stars(); Planets(); ...`。新增模块只需写一个类文件，不改 core。

5. **统一的 `addDataSource(url, key)` 数据挂载接口**
   - 所有模块（星表/行星/彗星/卫星 TLE/景观）都用同一个方法挂数据源 URL，模块自己决定解析。这让前端/脚本层面对所有天体类型用同一套加载范式。
   - 我们在 PySide6 里可以定义一个 `Module.add_data_source(source: DataSource)` 协议，每个模块实现 `load_mpc_file(path)` / `load_tle(text)` / `load_hips_url(base)`。

6. **observer 状态 hash 缓存**
   - observer.c 用 XOR hash 判断时间/位置是否变化，没变就跳过重算；fast 模式 1 天内做线性外推。这对桌面端 60fps 渲染很有用——拖动视角时不需要重算岁差章动。

7. **星等→像素亮度的统一函数**
   - `core_get_point_for_mag`（core.c:406）把视星等映射到屏幕半径+亮度，Bortle 等级、眼睛自适应 tonemapper 都走这一个函数。所有星点/卫星/小行星共用同一套亮度模型，视觉一致。

### 8.2 不必照搬

- **WebAssembly 桥**：我们是原生 Python，直接函数调用即可，不要学 JSON 字符串序列化。
- **C 里直接 WebGL 渲染**：我们用 QOpenGLWidget/Qt RHI，painter/render_gl 那套 GL 命令封装没必要抄；但 painter 的"状态对象（颜色/线宽/clip）传给模块 render"思路可以借鉴成一个 `PaintContext` dataclass。
- **HiPS 瓦片 HTTP 拉取**：桌面端数据可本地打包，不必做懒加载瓦片；但如果要支持 Gaia 十亿星表，HEALPix 网格化分块（`healpix.c`）的数学仍然有用。
- **Vue 响应式同步层**：Qt 的信号槽已经做了这件事。

### 8.3 可以直接搬的纯数学/算法
- `algos/refraction.c`：大气折射（Bennett 公式，refa/refb 预计算）
- `algos/orbit.c`：开普勒轨道根数→PV（小行星/彗星通用）
- `algos/moon.c`、`pluto.c`：半解析位置
- `algos/healpix.c`：HEALPix 坐标变换（若要做大星表分块）
- `algos/deltat.c`、`utctt.c`：时间系统
- SGP4（ext_src/sgp4）：人造卫星 TLE 轨道，Python 有 `sgp4` 库可直接用

---

## 9. 源码引用索引（≥15 处）

1. `frames.h:89-99` — 9 个参考系枚举定义
2. `frames.c:55-122` — `convert_frame_forward` 正向变换链
3. `frames.c:64-67` — ICRS→CIRS 用 bpn 矩阵
4. `frames.c:88-92` — CIRS→OBSERVED_GEOM 用 ri2h
5. `frames.c:95-111` — OBSERVED_GEOM→OBSERVED 折射
6. `frames.c:218-229` — `convert_framev4` 用 w 分量表无穷远
7. `frames.c:265-287` — `astrometric_to_apparent` 光行差+光线偏折
8. `observer.c:29-104` — `update_matrices` 预计算所有旋转矩阵
9. `observer.c:68-80` — ri2h 构造（地球自转+极移+纬度）
10. `observer.c:244-274` — `observer_update` hash 缓存 + fast/full 双模式
11. `observer.c:332-356` — observer_klass 属性表（longitude/utc/pitch/yaw）
12. `core.c:214-255` — `core_init` 创建核心并自注册模块
13. `core.c:244-248` — 遍历 OBJ_MODULE 实例化子模块
14. `core.c:286-347` — `core_update` 主循环
15. `core.c:520-601` — `core_render` 遍历模块渲染
16. `core.c:1011-1052` — core_klass 属性表（fov/projection/selection/lock...）
17. `core.c:406-439` — `core_get_point_for_mag` 星等→像素
18. `obj.h:107-174` — `obj_klass_t` vtable 结构
19. `obj.h:196-206` — `obj_t` 基类（klass/ref/id/children）
20. `obj.h:498-501` — `OBJ_REGISTER` constructor 自注册宏
21. `module.h:48-51` — `module_list_objs` 枚举天体接口
22. `pre.js:82-90` — FRAME_* 常量暴露给 JS
23. `pre.js:331-345` — `convertFrame` JS API
24. `obj.js:55-96` — SweObj 动态属性代理
25. `obj.js:306-323` — `_call` 走 `obj_call_json_str` JSON 桥
26. `obj.js:412-430` — `getValue/setValue` 点分路径 API
27. `obj.js:390-401` — C→JS 全局变更监听
28. `canvas.js:45-46` — 每帧 core_update/core_render
29. `SConstruct:133-148` — Emscripten 编译 flags（MODULARIZE/USE_WEBGL2）
30. `simple-html:200-222` — StelWebEngine 初始化 + addDataSource 用法
31. `sw_helpers.js:32-54` — `initStelWebEngine` + onValueChanged 接 Vuex
32. `sw_helpers.js:515-533` — `getTimeAfterSunset` 用 convertFrame 算太阳高度
33. `satellites.c:78-86` — `satellites_add_data_source` 接受 TLE jsonl
34. `algos.h:18-69` — HEALPix 算法接口
35. `projection.h:28-36` — 投影类型枚举（Perspective/Stereographic/Mercator...）
36. `StelCore.hpp:78-92` — C++ FrameType 对比
37. `doc/internals.md:326-358` — paint_quad 坐标变换链文档
38. `App.vue:224-272` — 前端 wasm import + 数据源挂载

---

## 10. 一句话总结

stellarium-web-engine 把天文计算做成了一个**编译进 WASM 的、C 写的、带动态属性树的对象系统**：9 参考系矩阵管线 + ERFA 底座 + 自注册模块 + JSON 属性桥。它的前端不计算、不画天体，只通过 `core.<module>.<attr>` 这条属性树和 C 内核交互。我们在 PySide6 里应吸收的是**参考系管线化、ERFA 底座、Q_PROPERTY 动态属性树、模块自注册、统一数据源接口**这五件事，而不是它的 WASM/WebGL 外壳。
