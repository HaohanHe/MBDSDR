# 03 · 分层架构与下层内核接缝

## 1. 为什么分层

原型曾是一个 1333 行的 HTML：文案、绘制、状态、数据、对话全挤在一段脚本里，改一句文案要翻整页，加一种语言要改上百处。产品化后按**下层 → 中层 → 上层**拆开，跨层只走命名空间与事件总线：

```
┌──────────────────────────────────────────────────────────────┐
│ 上层 · 视图与交互                                              │
│   30-sky.js       天球 / 摄像机 / 信息卡                       │
│   40-workbench.js 专业模式 / 频谱瀑布 / 状态栏                  │
│   50-dialog.js    模型协商 / 对话触发 / 诚实回执                │
│   70-docs.js      文档中心 / Markdown 渲染                     │
├──────────────────────────────────────────────────────────────┤
│ 中层 · 领域状态与文案                                          │
│   20-i18n.js      三语词典 / 运行时替换 / 关键词识别             │
│   60-app.js       引导 / 形态 / 时钟 / 工具栏 / 能力矩阵 / 帧循环│
├──────────────────────────────────────────────────────────────┤
│ 下层 · 状态、数学、数据与内核适配                               │
│   00-core.js      状态 / 事件总线 / 持久化 / 投影 / 契约常量      │
│   10-data.js      目录 / 事实源生成的能力位 / 三种数据源适配器    │
├──────────────────────────────────────────────────────────────┤
│ 真实内核（仓库内，本工程不实现）                                │
│   mbdsdr_ai/tool_registry.py（MCP 工具面）· C++ headless MCP     │
│   rtl_tcp / RTL-SDR · Celestrak TLE · SGP4 · 协议栈             │
└──────────────────────────────────────────────────────────────┘
```

所有模块是 classic script + IIFE，共享 `window.MBDSDR` 一个命名空间；顶层不新增全局变量，因此 `file://` 直接打开可用（不需要 ES module 的 CORS 豁免）。

## 2. 模块契约

| 模块 | 暴露 | 依赖 |
|---|---|---|
| 00-core | `$ $$ on off emit store persist state math CONTRACT assertContract` | 无 |
| 10-data | `SATS CAPS TOOLS SCENARIOS data native detectSource` | 00 |
| 20-i18n | `i18n.{t,has,keys,setLocale,applyStatic,refreshDynamic,isOrbitRequest,label}` | 00 |
| 30-sky | `sky.{draw,resize,makeSky,lockSat,unlock,retranslate,now,view}` | 00/10/20 |
| 40-workbench | `workbench.{draw,resetChat,renderStatus}` | 00/10/20/30 |
| 50-dialog | `dialog.{open,respond,sendAI,finish}` | 00/10/20 |
| 70-docs | `docs.{init,open,close,cycleLocale,refresh}` | 00/20 + 生成数据 |
| 60-app | `app.{applyForm,renderCaps,tickClock,syncLangButtons}` | 全部 |

跨层事件（`MB.emit`）：

| 事件 | 触发者 | 订阅者 |
|---|---|---|
| `locale:changed` | 20-i18n | 60-app（重绘全部动态层）、40、50（经 `i18n:refresh`） |
| `i18n:refresh` | 20-i18n | 40（对话/状态栏）、50（清空流） |
| `caps:changed` | 60-app | 预留给内核桥 |
| `form:changed` / `sat:locked` / `sat:unlocked` / `source:changed` | 60 / 30 / 10 | 预留 |

## 3. 状态与持久化

`state` 是唯一真值源：`form`、`locale`、`docLocale`、`cam`、`time`、`sky`、`caps`、`site`、`source`、`flow`。

- `localStorage` 键前缀 `mbdsdr:`；隐私模式抛异常时自动降级内存态。
- 持久化项：形态、语言、天空图层开关、能力开关、站址。摄像机姿态与模拟时间**不**持久化（每次打开都是干净的一帧）。
- `site.trusted=false` 是默认值：站址未校准时，界面拒绝给出可信的锁定/过境结论并如实提示。

## 4. 下层数据源契约（UI ↔ 内核的唯一接缝）

任何数据源必须实现 8 个方法（`00-core.js` 的 `CONTRACT`，`assertContract` 装配时校验）：

| 方法 | 语义 | 离线诚实空态 | ControlHub 数据源 |
|---|---|---|---|
| `sourceName()` | 来源名（i18n key） | `bridge.offline` | `bridge.online` |
| `isLive()` | 是否接真内核 | `false` | 需启用 + `fetch` 可用 |
| `listSatellites()` | 卫星目录 | 仓库内置目录（`sat_tracker.py`） | 同左（通道未提供目录） |
| `getTle(norad)` | TLE 文本 | `null`（**不伪造**） | `null`（通道未提供） |
| `getPasses(norad)` | 过境窗口 | `[]` | `[]`（站址未校准不做预报） |
| `getCapabilities()` | 当前在位能力 | 按 `caps` 过滤 | 同左 |
| `readSpectrum()` | 真实谱行 | `null` | `read_spectrum` 回执；拿不到就空态 |
| `invokeTool(name,args)` | 工具调用（Promise） | `not_connected` 信封 | `POST /command` 原样透传回执 |

### ControlHub：真实通道（事实源 `cpp/src/core/tokens.h`）

- 端口常量 `tokens::kControlHttpDefaultPort`，环境变量 `MBDSDR_CONTROL_HTTP_PORT`，
  设置键 `control/httpPort`；**只绑 `127.0.0.1`、无鉴权**，跨机走 SSH 隧道。
- `GET /` 发现文档、`GET /status` 状态、`POST /command` 下发，body `{"tool":"...","args":{...}}`；
  响应即 `ControlHub::execute()` 的结果，`ok:false` / `gated:true` 都是诚实结果，不静默改写。
- 另有 MCP `tools/list` / `tools/call`（`mbdsdr-core`）与 Headless `SDRController`；
  三通道（Agent / ControlHub / HTTP）行为一致。
- 前端启用方式：`dist/index.html?hub=1` 或 `window.MBDSDR_USE_HUB`；Qt/WebEngine 也可注入
  `window.mbdsdrBridge`（`invoke` / `satellites` / `readSpectrum`）。`file://` 直连回环可能被
  CORS 拦，正式部署建议由引擎同源托管。

### 阶段 B：Python 侧运行时桥接（`mbdsdr_ai/web_bridge.py`）

阶段 A 只有前端契约，没有运行时。阶段 B 在仓库 Python 侧新增**独立**适配层：
不 import Qt、不碰 `desktop/main_window.py`（该文件是操作逻辑线在途改动）。

- `WebBridge` 把 `ToolRegistry` 的 `list_tools` / `call` 映射成 `MB.CONTRACT`
  的 8 个方法；`as_bridge_dict()` 给出 `window.mbdsdrBridge` 的可注入形状
  （产品 `BridgeSource` 用的是 `invoke` 而不是 `invokeTool`，已逐字对齐）。
- `manifest()` 是可对拍的 JSON 契约；`tests/test_web_bridge.py` 用 16 条用例把
  Python 契约与 JS `MB.CONTRACT`、`repo-facts.json` 的门字段钉在一起。
- **写工具绝不走裸 `registry.call()`**：内核的门在 `mcp_server.call_tool` 层，
  `ToolRegistry.call()` 本身没有门。缺 `confirm_token` / `tx_token` →
  `manual_gate_required`；即便带 token，也需 `confirm_channel`（应接到带门的
  `mcp_server.call_tool`）转发，未接线则恒 gated。
- 站址 `site_trusted=False` 时过境恒空；卫星 / TLE / 频谱无 provider 时诚实空值；
  `isLive()` 只表示桥已接注册表，**不代表设备在线**。
- **未做**：宿主注入（QWebChannel / JS 全局）、`confirm_channel` 接
  `mcp_server.call_tool`、真实硬件逐工具实测。见 `manifest().notVerified`。

### 写工具确认门（两道门，前端一道都不签发）

写工具名模式来自 `mbdsdr_ai/tool_registry.py: WRITE_TOOL_NAME_PATTERNS`（判「是否写工具」，
不判「是否已获确认」）。门的事实由 `build/sync_from_repo.py` 从内核代码抽进 `MB.REPO.gates`：

| 通道 | 门归谁 | 未获确认的回执 | 前端职责 |
|---|---|---|---|
| `ControlHub` C++ | 宿主写开关 `writeEnabled_`（`row->write && !writeEnabled_.load()`） | `{ok:false, gated:true}` + 「写入被禁止（write gate 关闭）」 | 剥离自报 `arm_manual`，其余 args 原样 POST，如实回显 `gated` |
| Python 内核 / MCP | 外部确认通道签发的一次性 `confirm_token`（别名 `tx_token`） | `manual_gate_required` | 只透传已签发 token；无 token 不下发、返回 `gated` 信封 |

**模型自报 `arm_manual` 明确无效**（内核 `mcp_server.py` 只记录出现过，绝不作为放行依据）。
前端不签发 token、不打开宿主开关、不自判放行。`gated:true` 是「未获确认」，UI 显示
「gated · 非失败」，与执行失败 / 设备故障严格区分。

### 工具名 → 内核落点（全部为代码里真实存在的工具）

| 前端 / 通道工具名 | 内核侧落点 |
|---|---|
| `list_devices` / `connect` / `get_status` | `SDRController` 设备与服务面、`mcp_server.py` 系统设备域 |
| `set_frequency` / `read_spectrum` | 接收参数 / DSP 面，频谱来自真 IQ 的窗函数 FFT |
| `get_satellite_pass` / `start_recording` | 书签扫描卫星域 / 导出演制域 |
| `sat_link_identify` / `sat_link_tuning` | 卫星链路域（`data/sat_links.json`） |

失败语义沿用仓库诚实回执：离线 `not_connected`、未过门 `gated`、异常不吞不改写成"未安装"。
完整事实源表见 [09-repo-alignment.md](09-repo-alignment.md)。

## 5. 渲染管线（星象仪 · 时空主导）

数学与坐标链：`14-astro-frame.js`（仓库 `celestial_geometry.py` 口径）负责岁差/章动/GMST，
`31-celestial.js` 负责视图空间与投影；数据来自 `12-sky-catalog.js`（[星表口径](10-sky-catalog.md)）。
星位固定 J2000，**一帧都不动**；变的只有视角、视场角、模拟时刻。

1. **时间与坐标框架**：唯一缺省 `simUnix()`（TimeEngine 契约）。
   `J2000(ICRS) → IAU2006 岁差 P → IAU2000B 简化章动 N → Capitaine GMST → LST → 地平`，
   每帧合成一个 3×3 总矩阵 `M = Rf · QᵀNPQ`（`Q` 把仓库赤道基换到前端标准 ICRS 基），
   恒星、星座、DSO、已转 J2000 的卫星轨迹共用它；太阳是 of-date 位置，走只含 `Rf` 的
   `dateToHorizon`，不重复施加岁差。跨语言金标准见 `build/gen_astro_golden.py` /
   `tests/astro_selftest.js`（对拍 3.3e-16），`not_verified` 见 `10-sky-catalog.md`。
   周日视运动仍是一整个天球共用一个 LST 的**刚性旋转**，不给单星独立角速度。
2. **视角/视场**：`ViewState{center_az, center_alt, fov_deg}`，缩放改 `fov_deg`，不做相机推拉。
   倍率五档来自 `desktop/rf_sky_view.py`（事实源同步）。
3. **投影两级，同一套视图空间**：相机在球心，`(forward, right, up)` → 视图空间 `(X,Y,Z)`。
   - `fov ≤ 88°`：针孔透视 `x = X/Z·f`，`f = (min(W,H)/2)/tan(fov/2)` → 人眼 / 相机成像；
   - `fov > 88°`：立体投影 `r = 2·tan(θ/2)` → 鱼眼，能看全天球。
   地平线在透视下是**直线**（大圆在针孔相机里的正确像），在立体投影下是**圆弧**；
   两者都是球面几何的正确投影，不是"把天空摊平的横向长条"。
   机制参考 Stellarium Web Engine 的 `projection.c`、`proj_perspective.c`、
   `proj_stereographic.c`（AGPL，只学数学，不引代码）。
4. **全屏，无圆形遮罩**：裁剪只靠窗口矩形（对应原项目 `project_to_win` 的 `window_size`）。
   **天空是球、屏幕是平面**——"天圆"说的是球面几何（地平是大圆、星位钉在球面上），
   不是把天空塞进一个圆圈里画。
5. **图层顺序**（每帧一次）：夜空底 → 方位/赤道网格（球面环采样）→
   **全天星**（HYG v4.1 25,792 颗，按 LST 重投影，同一套可见门）→
   星座连线（27 座）/ 中文星官 → DSO 类型符号 → 大气辉光（沿地平弧加法混合）→
   **地圆**：逐屏幕列二分求 `alt(y) = h(az)` 分界，边界以下整体填充
   （地景绕 360° 方位铺满、随视角动、不随时间转）→ 地平大圆与地形上沿描边 →
   方位字 → 卫星白点环标 → 夜视滤镜 → 时空 HUD。
6. **银河**：**无数据·不提供**，不画任何装饰光带（原仓库 `limitations-register.md` L25/L28）。
7. **入场「展开」**：视场由 160° 收束到 100° + 暗适应（显示门限由 2 等放开到当前 FOV 门限），
   星按真实星等依次浮现——不是星从中心飞散。
8. **可见判据（同一套门）**：视锥/地平门 + 白天整层消隐（布尔日落门 −0.833°）+ 大气消光 +
   极限星等（**内核事实源**：`skyengine/stars.py` Bortle 裸眼基线 + `star_catalog.py`
   FOV 渲染 LOD 增益，`14-astro-frame.js` 逐式镜像、`gen_limit_mag_golden.py` +
   `limit_mag_selftest.js` 跨语言钉扎 1e-12 等；前端 depthCap = 星表截断 7.5）
   **减晨昏连续惩罚**（**内核事实源**：`skyengine/twilight.py::twilight_limit_penalty_mag`
   = max(0, 21.6 − 分段线性天顶亮度)，`14-astro-frame.js` 逐式镜像、`gen_twilight_golden.py` +
   `twilight_selftest.js` 跨语言钉扎 1e-12 等；与桌面 `rf_sky_view.py:4246` 同式 base − penalty）。
   布尔白天门保留（太阳高于 −0.833° 整层不画），低于该阈值后不再一档到底，由惩罚连续压暗
   （晨昏各约 1 小时的真实过程）。LOD 增益非望远镜口径增益；晨昏惩罚是量级近似文献曲线、
   未辐射标定（±1 等、天顶等效、无方位依赖、无月光），只用于显示层；完整天空亮度→极限星等
   光度学模型（月相/实际标定天光表面亮度）仍未做（内核 limitations L24）；入场暗适应淡入是 Web 呈现层。
9. **选中指针**：2D 逐帧重投影，2 秒呼吸；摄像机锁星后天球在背后转、星在画面中心静止。

反投影 `unproject()` 支持 **grab-the-sky**（按下点钉在光标下）与 **zoom-to-cursor**。

## 6. 构建与校验

- `build/sync_from_repo.py`：**只读抽取原仓库代码事实源**（域/工具/令牌/端口/卫星/倍率）。
- `build/sync_sky_catalog.py`：**只读解析仓库 141 命名星 + 27 座连线 + 117 DSO，并读取下载的 HYG v4.1 全天星表**（25,792 颗，V≤7.5），生成 `12-sky-catalog.js` 与 `10-sky-catalog.md`。缺 HYG 源 CSV 直接报错，拒绝退化成假全天。
- `build/fetch_hyg.py`：用 git 稀疏克隆取回 HYG v4.1（HTTP 大文件会被网关掐断，git 协议稳定），记录 SHA256。
- `build/scaffold.py`：从 v1 原型抽取 CSS/HTML 骨架，注入 `desktop/tokens.py` 的设计令牌变量，插入语言钮与文档中心容器。
- `build/build.py`：串接事实源同步 → 骨架 → 编译 `docs/**/*.md` 为 `src/70-docs-data.js` → 拼 CSS + 全部 JS → 自包含 `dist/index.html`。
- `tests/verify_product.py`：JS 语法、DOM 引用、三语覆盖、定轨铁律、事实源一致性、红线守卫（真实地名/能力数字/规划标注/诚实空态）。
- `tests/verify_runtime.js`：mock DOM/Canvas 下实跑两种绘制路径与语言切换，抓初始化顺序错误。

新增能力位：先改**原仓库的事实源**（或 `sync_from_repo.py` 的译名表）→ 跑 `build/build.py`（自动同步 + 生成 wiki）→ 跑校验。**不要直接改 `10-data.js` 的 `CAPS`**——它由 JSON 生成。
