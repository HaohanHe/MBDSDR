# 天文通 APK 拆解 × MBDSDR 差距 × 四方向推进（2026-11-17）

> **本文为当时状态快照**：记录当日工作状态与结论，未逐条复核；
> 不作现状依据。共 5 条未复核；需要现状请查 `docs/COORDINATION.md`、
> `docs/known-red-register.md` 与 checklist。

本文回答两件事：**天文通到底有什么**、**我们离它多远**；最后给出四个方向
（QGIS / 本地 DeepSeek / Hugging Face / 克隆项目）各自的下一步。
只做能力对照与工程判断，**不复制对方的数据文件与代码**（包内 assets 仅用于
读结构与计数，正文不搬运）。

## 0. 对象与取证方式

- 文件：`d:\mbdsdr\base(1).apk`（117 MB，2026-11-17 17:47）
- 身份：`package=com.twtapp`、`versionName=3.6.0`、`versionCode=3060000`、
  `minSdk=24`、`targetSdk=36`；`resources.arsc` 内含「天文通」
- 技术栈：Flutter（`libflutter.so` + `libapp.so` AOT，Impeller 关闭）+
  Rust 原生库 `librust_lib_tianwentong.so`；仅 arm64-v8a；5 个 dex
- 目录里**没有任何反编译产物**（无 apktool/jadx 输出、无 smali、无
  `apktool.yml`；本机也未安装 apktool/jadx/aapt）
- 取证：自写最小 AXML 解析器解 `AndroidManifest.xml`，输出存
  `docs/evidence/2026-11-17-tianwentong-apk/manifest.txt`（736 行）；
  assets 为明文，按 zip 目录统计

## 1. 天文通的能力地图（按包内证据）

| 组 | 能力 | 包内证据 |
|---|---|---|
| 星图 | 星表、深空、星座连线、传统星官、星座插画 | `star_chart_module/dist/default/data/catalog_stars_v2.bin`（HYG v4.2，115,570 颗，含自行/空间速度）、`deep_sky_objects_v1.bin`、`traditional_asterism_segments_v1.csv` |
| 星图 | 太阳系探索（行星/月球纹理、空间站模型、月面地名） | `modules/solar_system_explorer/solar_system_explorer.js`、`moon_locations.json`（Oceanus Procellarum 等）、JPL DE441 授权、Stellarium 月面纹理授权 |
| 星图 | 相机拍星空本地解算 | `assets/skysolve/{rust_patterns_narrow_v5,rust_stars,rust_tetra_gaia_10_45_v3}.bin`、`local_solve_gate_profile_v1.json` |
| 观测 | 流星雨事件表 | `assets/js/meteor_events.json`（924 条）、`all_meteor.json`、`camera.json` |
| 观测 | 极光预报 | `assets/js/aurora_forecast.js` |
| 环境 | 15 天天气 + 7Timer 天文天气 | `assets/images/weather/`、`assets/svg/`（云量/抬升指数/湿度/降水）、`climate/cg.bin` |
| 环境 | 观星地点库 | `curated_stargazing_spots.json`（79 点，覆盖 31 个省级行政区） |
| 体验 | 星空音效 | `assets/audio/`（14 个 m4a，按目标类型切换 + 环境音） |
| 入口 | 桌面小组件与天象提醒 | 6 个 AppWidget（月相/暗夜窗口/月历/行星/流星雨/下一次天象）、`CelestialReminderReceiver` |
| 商业化 | 登录/支付/广告 | `wxapi`、RevenueCat、Google Billing、腾讯 GDT、字节 Pangle、友盟 |

## 2. 与 MBDSDR 的逐项对照

| 能力 | 天文通 | MBDSDR 现状（证据） | 判定 |
|---|---|---|---|
| 星表深度 | 115,570 颗（HYG v4.2） | **83,479 颗到 9.0 等**（`skyengine/star_catalog.get_catalog().count`） | 接近；我们按 Bortle/消光做 LOD，不追全表 |
| 深空天体 | Messier + Caldwell + 亮 NGC | **117 条**（`astronomy/dso.py`），6 类符号、真实视大小限幅 | 数量少，但符号语义/限幅/拾取同源更细 |
| 星座/星官 | 连线 + 插画 + 传统星官段 | 连线 + 座名 + 星官注记（`skyengine/constellations.py`、`xingguan.py`） | 对齐；不画插画 |
| 太阳系 | 纹理、空间站模型、月面地名 | 行星/月球位置与视直径、月面反照率纹理 | 差月面地名与空间站模型 |
| 流星雨 | 924 条事件表 | **12 场主要流星雨 + 活动包络 + 星图辐射点图层**（`mbdsdr_ai/astronomy/meteor_showers.py`）；并可经 `sky_tonight` 返回「观测日期处于哪几场活动期」 | 从 0 到 1；事件表粒度仍差 |
| 极光 | 预报脚本 | `tonight._assess_aurora` 按 NOAA Kp + 地磁纬度经验门槛粗判「能不能看到」，缺测不猜；无预报曲线与时间轴 | 粗判已通，产品化（图表/提醒）仍缺 |
| 天气预报 | 15 天 + 7Timer 细分 | `weather_panel.py` 是**卫星云图解调**（GK2A/GOES/NOAA/Meteor/FY），无预报 | 缺预报（但方向不同：我们是收图，不是查天气） |
| 观星地点 | 79 个已验证暗夜点 | `location/places.py` 是地名解析（Nominatim / 离线 JSON）；`astronomy/observing_sites.py` 已有 4 个科研台址 + 3 个公众点（官方坐标） | 数量少，但每个点都有 `source_url` 与 `access`（research/public） |
| 相机星空解算 | 本地 skysolve | 无（有完整坐标链与星表，可做但未做） | 有意不做（PRD 取舍） |
| 星空音效 | 14 段 | 无 | 不跟（本产品是仪表盘，不是内容消费） |
| 小组件/提醒 | 6 个 AppWidget + 天象提醒 | 桌面端无；移动端未在本轮核查 | 待定（先不做） |
| 广告/内购 | 全量商业化 | 无 | 不跟 |

**结论**：差距不在「功能条数」。天文通是**消费级观星内容 App**；
MBDSDR 是**射电 + 星图的观测仪表盘**（PRD 明确不追渲染美术与内容生态）。
真正的缺口是三条闭环：
1. **今晚能不能看**：流星雨（已补）→ 极光/云量（缺）→ 观星点（缺）；
2. **看到的能不能记**：观测记录/计划与世界坐标导出（已有 GeoJSON 骨架）；
3. **设备与天空是否同一套真值**：星图 ↔ 天线 ↔ 卫星 ↔ 地图（已有，
   继续用单源治理，不新增第二套常量）。

## 3. 四方向现状与推进

「四不像」的根因是四条线各自有料、但没有共同的验收口径。每个方向只认
**可复现证据**（测试/命令/文件），不认口头完成度。

### 方向 1：QGIS / GIS

- 已落地（机制层，不是装 QGIS）：CRS 唯一真值（`mbdsdr_ai/gmst.py`）、
  时空区间语义（`spacetime_features.py`）、瓦片父级回退（`desktop/osm_map.py`）、
  非模态通知条（`notification_bar.py`）、设置项「显式默认」标记、
  GeoJSON 输出（`position_service.py`，坐标序 `[lon,lat]` 已测）。
- 本轮：只做核对，未新增代码（避免为「有动作」而改已验收模块）。
- 下一步（可验证）：把**卫星地面轨迹 / 观测点**统一走
  `position_service.geojson()` 导出，补一条「QGIS 打开后字段齐全」的契约测试；
  不引入 GDAL/PROJ 依赖。
- **已落地（2026-11-17）**：`ground_track.to_geojson()` 输出
  FeatureCollection，坐标序 `[lon,lat]`，日界线切段每段一条 LineString；
  证据 `tests/test_ground_track_geojson.py` 7 passed（含真实 ISS 轨迹与
  假线反证）。
- **已收口（2026-11-17）**：`tests/test_geojson_qgis_contract.py` 5 passed
  把三个导出面（地面轨迹 / 台址点 / 实时位置）拉到 RFC 7946 同一把尺子：
  坐标值域、严格 JSON（`allow_nan=False`）、各面必填属性。
  not_verified：本机 `C:\Program Files\QGIS 4.0.3` 只是 OSGeo4W 安装壳
  （无 qgis-bin/qgis_process），QGIS 本体加载未实测；`D:\qgis-src-34415\
  QGIS-final-3_44_15` 是 3.44.15 源码快照，可作读源码用。

### 方向 2：本地 DeepSeek

- 事实：**本机没有 DeepSeek 权重，也没有本地推理服务**；现有 LLM 是
  OpenAI 兼容客户端（`model_manager.py`，`base_url` 可指向 Ollama/vLLM/LM Studio），
  且 `MBDSDR_OFFLINE` 下会直拒。
- 下一步（可验证）：加一个**端点体检**（`/v1/models` 探测 + 明确状态
  `未启动 / 无模型 / 可用`），让「本地 DeepSeek 不可用」在 UI 上是一句
  可核对的结论而不是异常栈；模型权重到位后无需改业务代码。
- **已落地（2026-11-17）**：`mbdsdr_ai/local_ai_probe.py`
  （`probe_endpoint` / `format_probe`），状态含 offline / not_running /
  unauthorized / no_models / ready / error；离线模式不发起连接；
  证据 `tests/test_local_ai_probe.py` 13 passed。UI 接线已做（配置对话框
  「检测端点」+ 状态行）。
- **同日补真缺陷**：体检只能证明端口有人听，聊天仍会因「没有 Key」被拦。
  本地 Ollama / vLLM / LM Studio 默认无鉴权，故新增
  `model_manager.is_local_endpoint`，并在 `fetch_models` / 请求头 /
  `MBDSDRAgent.chat` / AI 面板配置四处放行「空 Key + 本机或内网端点」；
  云端端点缺 Key 行为不变，离线门优先。证据：
  `tests/test_local_endpoint_keyless.py` 26 passed（127.0.0.1 桩服务
  跑通 `/v1/models`、非流式 chat、SSE chat_stream，并断言无空
  `Authorization` 头）；AI/Agent 回归 11 文件 161 passed。
  仍 not_verified：本机无 DeepSeek 权重与推理服务，协议通不代表模型效果。

### 方向 3：Hugging Face（本地模型 / 分类器）

- 事实：主环境无 `huggingface_hub`/`transformers`，无权重缓存；分类器重训
  **缺真实多类 IQ 样本**（`docs/learn/2026-11-17-local-ai-inventory.md` §3.1）。
- 下一步（可验证）：先立**样本清单契约**（`sample_id/label/sample_rate/
  center_freq/iq_path/license` 的 JSONL 校验器 + 拒绝理由），再谈训练；
  没有真实样本前不伪造数据集、不训练「演示模型」。
- **已落地（2026-11-17）**：`mbdsdr_ai/iq_sample_manifest.py`
  （必需字段、唯一 ID、有限正数、license 必填、逐行拒绝不中断）；
  新增 `audit_manifest` 结构体检：空清单、单类别、同一 `iq_path`
  重复登记（跨类别即标签泄漏）、重复 `sample_id` 一律 `not_ready`，
  且明确「structural_ready 不代表样本足够、无切分信息无法证明无泄漏」；
  证据 `tests/test_iq_sample_manifest.py` **23 passed**。训练仍阻塞于真实样本。
- **同日补切分方案（2026-11-17）**：`plan_split(accepted, val_ratio,
  seed)` 以 `iq_path` 为最小单位做确定性切分（同一文件不跨训练/验证），
  类别内按文件数分验证集，跨进程可复现；标签冲突、单文件类别、重复
  `sample_id`、缺字段一律拒绝并清空结果，不交付半套切分；caveat 明说
  只排除「同一文件跨集合」这一种泄漏。证据：同文件 **33 passed**
  （新增 10 条切分用例）。本机仍无 HF 运行时与真实样本，不冒充已训练。

### 方向 4：克隆下来的项目（Stellarium / SDR++ / sdroxide / SatDump）

- 已吸收：sgp4→ENU 坐标链、显示层折射（Stellarium 分段式）、投影批量
  解析式（QGIS 对照）、SatDump `dataset.json` 选图、sdroxide/SDR++ 的
  解调与 UI 取舍（均有测试）。
- **本轮落地**：按 Stellarium `MeteorShowers` 的「静态辐射点 + 活动窗口」
  思路，独立实现 12 场流星雨目录与星图辐射点图层；
  门控与恒星/DSO 同一套（白天/地平/站点），文案只写「活动期 + 峰值 ZHR」，
  不编造「今晚每小时 N 颗」。
- **独立对照（2026-11-17）**：用本机 Stellarium 克隆的
  `MeteorShowers.json`（44 场）核对我们的 12 场，修正三处赤纬笔误
  （GEM 32→33、STA 13→15、URS 75→76），其余一致；只读核对，
  不复制其数据文件。证据：`tests/test_meteor_showers.py` 13 passed。

## 4. 下一轮顺序（按「用户能感知」排序）

1. **极光 / 云量 / 月光 / 流星雨「今夜可看性」**：**已落地（2026-11-17）**——
   `mbdsdr_ai/astronomy/tonight.py` + `sky_tonight` 工具；云量缺测时
   明确「不按晴天假设」，无快照时「未联网，不猜」；极光按 Kp 与地磁
   纬度经验门槛粗判（低纬外推，不做 Kp=9 假阳性）；月光用本地
   `de421.bsp` 算**当前时刻**的照亮比例与高度，分 none/weak/moderate/
   strong，明确「不覆盖整夜」；流星雨按 UTC 日期查 12 场静态年历，只给
   「在不在活动期 + 峰值 ZHR」，不折算「今晚每小时几颗」（`schema=2`）。
   证据：`tests/test_tonight_sky.py` + `tests/test_meteor_showers.py` +
   `tests/test_sky_tools.py` 92 passed；见 `docs/COORDINATION.md` §30、§33-E、§36。
2. **观星点**：**部分落地（2026-11-17）**——`mbdsdr_ai/astronomy/
   observing_sites.py` 内置 4 个有官方页面坐标的**科研台址**（兴隆、
   丽江高美古、阿里、冷湖赛什腾山），`access` 标 `research` 并明说
   「不对外开放」；另补 3 个**公众点**（国家天文台沙河科普基地、
   俄博梁雅丹/火星营地北 2 km、漠河北极村景区范围中心），坐标逐条来自
   官方页并记 `verified_on`；支持用户导入（缺 `source_url` 拒绝）与
   GeoJSON 导出；工具 `sky_observing_sites`。证据：
   `tests/test_observing_sites.py` 28 passed。
   **仍缺**：阿里暗夜公园官方页没给坐标，未收；继续扩充需逐个开官方页。
   漠河点是官方矩形范围的中心（非门址），海拔记 null，且 note 写明
   磁纬 44.4° 下极光仍需强地磁活动、夏季极昼无法观星。
3. **DeepSeek 端点体检** + **HF 样本清单校验器**（各一条测试即可验收）；
4. 卫星地面轨迹 / 台址 / 实时位置 GeoJSON 契约测试（QGIS 方向收口）：
   `tests/test_geojson_qgis_contract.py` 5 passed。
