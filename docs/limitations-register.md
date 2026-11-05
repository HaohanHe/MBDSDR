# 限制总账（Limitations Register）

> 维护口径：**P11 集成体检（2026-11-04）汇总**。本表只回答一个问题：
> 这个能力是「真验过」，还是「只用合成信号/假后端自测过」，还是「根本没验」。
>
> 三档定义：
> - **真验过**：有真机、真实样本或第三方真值背书（可写出证据文件/命令）。
> - **仅自测**：只有本仓合成 fixture、假后端或单测自洽，不构成外部验证。
> - **未验证**：本轮环境物理上做不到（无硬件/无过境/无 key/无网络/待用户决策）。
>
> 纪律：未验证项不得在汇报或文档里写成「已验证」。数字均为本轮实测快照，
> 会随提交浮动；以各条「复跑条件」里的命令重新取数为准。

## 0. 一页判读

| 档位 | 条目 |
| --- | --- |
| 真验过 | RTL-SDR（FC0012）七环验收 R1–R5/R7；NanoVNA 真机 SOLT；FT8 外采 WSJT-X 官方样本；ADS-B 外采 dump1090 全量清单；LRPT 外采 TU Berlin 实录；GPS C/A IS-GPS-200E 矢量；DVB-S2 BCH/LDPC KAT |
| 仅自测 | 全量工具面与 schema 一致性；AI 上下文预算/工具裁剪/并发取消/错误恢复；录音库 P9 全系列；TLE 编号三方校验；发射门/状态机/半双工互斥（假后端）；天空/云台编排；端到端场景 A/B/C（假模型 + 合成 IQ/音频 + 时间拨盘，跨模块数值自洽） |
| 未验证 | 真实发射（PlutoSDR）；1090 MHz ADS-B；云台/rotator 闭环；真过境 APRS/NOAA-APT/Meteor OTA；真实 LLM 联调（工具选择/流式取消/token 计数）；celestrak/NTP/open API 实时网络；arm64 Chromebook 真机；听感验收 |

## 1. 待真机（有代码、无硬件）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L1 | 发射链路真发（CW/IQ/回放发射/TC 上行） | 发射线 `2aa912c`/`6094b87`/`322aba4`/`899882c`；`tests/test_tx_no_hardware.py` 用假后端 | 仅自测 | PlutoSDR + 衰减器/假负载 + 一台接收机；设 `MBDSDR_TX_BANDS` 与 `MBDSDR_TX_TOKEN`，先失败路径（未 arm/错 token/超频段）再真发 |
| L2 | 云台/rotator 闭环（`gimbal_track_satellite`、`sky_gimbal_follow`、`gimbal_rssi_sweep`） | 天空/云台线；`sdr_tools._gimbal_rssi_sweep`（`agent.sdr_manager.get_active()` 先例） | 仅自测 | 真 rotator（串口 RPRT）+ 真实过境；先验 `gimbal_read_pose` 回读一致，再验跟踪 |
| L3 | ADS-B 1090 MHz 接收 | `docs/real_hw_acceptance_20261103.md` 环 6 = UNVERIFIABLE（FC0012 上限约 948.6 MHz） | 未验证 | 换 R820T/R820T2 棒，`python scripts/real_hw_acceptance.py --ring 6` |
| L4 | 听感验收（链内 AGC、长时音高漂移） | 同上文档「不可验声明」；本轮全程 `MBDSDR_NULL_AUDIO=1` | 未验证 | 用户放行声卡后人工听；数据面（导频/RMS/频谱含量）已另行验证 |
| L5 | NanoVNA 命令细节（`vbat`/`freqoffset`/`edelay`/`averages`） | `docs/learn/nanovna/protocol-study.md` 逐行标「待真机验证」；SOLT 已于 2026-10-23 真机验过 | 部分真验 | 真机逐条发命令并记录回显；单位/上限语义以实测为准 |
| L6 | 物理 arm64 Chromebook 启动/USB 直收/rtl_tcp | `docs/CHROMEOS_CROSTINI_SDR.md`（qemu 交叉编译已过，物理机未验） | 未验证 | 真机启动 + `QT_QPA_PLATFORM=offscreen` ctest + USB 直收 |

## 2. 待真样本（有链路、无真实信号）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L7 | APRS / NOAA-APT / Meteor LRPT OTA 解码 | `docs/real_hw_acceptance_20261103.md` 环 6 | 未验证（文件级真录另有验证） | 等在轨过境/本地台活动，`python scripts/real_hw_acceptance.py --ring 6 --aprs-seconds 120` |
| L8 | FT8 本机 OTA 自采 | `docs/learn/capability-map.md`（外采真值 5/14 命中，本机 OTA 记录为零） | 部分真验（外采真值） | 自架天线录一段 OTA wav 接 `tests/test_ft8_wsjtx_golden.py` 同链 |
| L9 | LRPT Gardner 环修复（默认关） | 协作台账「LRPT 队列#9」（实验已测未入库；`mbdsdr_ai/lrpt_modem.py` 属他线在途，未代改） | 仅自测 | 用真实 Meteor 过境样本跑开关对照；先等该文件不在途 |
| L10 | 录音库迁移/回收站/损坏索引 | 录音库线 `b55420b`/`2de82f3`/`f902ba7`/`613665e`/`c4b1b36`/`8a3940d` | 仅自测（合成库 + 临时目录） | 复制一份真实录音库副本（不碰原件）跑迁移预演与回收站路径 |
| L11 | JAMX01 活动真过境 SSTV/SSDV | `docs/learn/phase34/event-rx-runbook.md`（多处【待真机确认】） | 未验证（合成级联 IQ 已出图） | 活动窗口真过境；SSTV 制式/行数与 SSDV 包流现场核对 |

## 3. 待真模型（有 agent、无真实 LLM）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L12 | token 预算与上下文压缩 | AI 线 `9ba99c3`（P7.3）；`context_manager.estimate_tokens` 在 tiktoken 缺失时按启发式估算 | 仅自测 | 装 `tiktoken` + 真模型，对同会话同时记录估算值与 usage 实际值 |
| L13 | 工具选择准确性 40/40 语料 | AI 线 `4da1944`（P7.1）；语料为自建；P12 已补离线因果探针 `tests/test_ai_tool_selection_semantics.py`（规则替身证明提示词/候选集改变选择，非真模型） | 仅自测 | 真模型跑 `scripts/ai_intent_regression.py`；语料是否够用待用户拍板 |
| L14 | 流式取消 / 错误恢复不谎报 | AI 线 `121b0b1`（P7.2）、`a7cfe3a`（P7.4）；测试用假模型 | 仅自测 | 真 provider 触发流中断/取消/超时，核对终态与计费留痕 |
| L15 | `tool_choice` 的 required/none/指定函数名 | `docs/learn/model-tool-calling/04-sf-chat-completions-api.md` 第 329 行（文档未覆盖） | 未验证 | 有 key 后逐个取值联调；默认只当 `auto` 用 |

## 4. 待网络（有代码、无外网）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L16 | TLE 编号-名称-来源三方校验 | P10-1 `f7f1d27`；离线只用内置/回退 TLE | 仅自测 | 联网拉 celestrak 实时 TLE，跑校验并记录 epoch/source |
| L17 | NTP / open API（ISS、天气、航班）/ web_fetch / 外部 MCP 客户端 | `docs/learn/capability-map.md` §外部依赖销账表（失败行为已核对，真实网络未验） | 仅自测（失败路径） | 联网逐项调用；失败路径由 `tests/test_external_api_honesty.py` 桩测已锁 |

## 5. 待用户拍板（行为与范围）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L18 | `arm_manual` 人工门策略（是否允许会话 token 自动 arm） | 发射线 `322aba4`；注册表 94 个写工具走门 | 仅自测（策略层） | 用户给出部署策略后再调 `tx_guard` 默认值 |
| L19 | 发射频段/合规策略（`MBDSDR_TX_BANDS` 未设时一律拒发） | `tx_guard.policy_from_env` | 仅自测 | 用户确认目标频段与合规边界 |
| L20 | 工具面是否全量暴露给模型（预算裁剪上限） | AI 线 `4da1944`；`tool_budget` 裁剪后 ≤8192 token | 仅自测 | 用户决定保留/隐藏分类；真模型侧看误选率 |
| L21 | MCP 面范围（控制器面 82 个 vs AI 注册表 495 个） | `mbdsdr_ai/core/mcp_server.py`、`docs/mcp_tools.md`；注册表 meta `mcp` 分类 = 0 | 设计分裂，未拍板 | 用户决定是否把 AI 工具镜像进 MCP；当前只在 Agent 面可用 |
| L22 | 本轮集成验收红线（哪些红必须清零才合并） | P11 任务书 | 待确认 | 用户对「环境红/真红」的处置口径 |
| L23 | 两个「天空可见」工具时间基准分叉：`sky_view_visible` 走 TimeEngine，`sdr_satellite_sky_view` 走墙钟 | P12 实测：把 TimeEngine 拨到 NOAA 19 仰角 23.3° 的时刻，前者列出该星、后者不列（`orbit.visible_satellites` 无 `timestamp` 参数）；与别线 P11-6/7/8 的时间基准统一同域 | 口径差，未拍板 | 待时间基准线定口径后，给 `orbit.visible_satellites` 加 `timestamp` 并把 `_satellite_sky_view` 接上 `get_time_engine().now_unix()`；复跑同一拨盘对照两工具 |

## 6. 天空绘制的模型简化（口径已明示，不冒充标准模型）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L24 | 白天恒星消隐：只有「太阳高度整层开关」，**没有**天空亮度→极限星等模型 | `desktop/rf_sky_view.py` 的 `STAR_DAYLIGHT_SUN_ALT_DEG` / `stars_hidden_by_daylight`；`tests/test_sky_daylight_stars.py` | 保守近似：太阳高度 ≥ -0.833°（日落标准定义 = 34′ 大气折射 + 16′ 日面半径）时恒星整层不画、不可点选，状态卡明示「白天·无恒星」。不做暮光渐变，也不按太阳高度/光害反推白天可见极限星等。因此白天画面**不可**解读为「该时刻肉眼可见的星等下限」 | 若要真模型：接入公开天空亮度模型（Krischunas & Schaefer / Schaefer 1989）换算极限星等，替换整层开关并同步改该测试与状态卡文案；在此之前不得把白天画面当作亮度判据 |
| L25 | **没有银河图层**：仓库内无银河亮度/尘埃带数据 | `grep -i "milky\|银河\|nebula\|星云" desktop/` 无命中；星表仅 ~250 颗亮星，密度不足以合成银河带 | 按「不编造数据」原则，`rf_sky_view` **不提供**银河图层（不画装饰性光带）。因此宽 FOV 夜空没有银河表现，属**已知能力缺口**，不是渲染错误 | 若引入公开低分辨率银河亮度贴图（须先核对来源与授权，例如 Gaia 派生图 / 公开域 ESO 图），再加图层、许可说明与测试；在此之前不要以"像银河"的渐变色带代替 |
| L26 | 深空天体图层是「精简星表 + 类型符号」，不是形态或表面亮度还原 | `dso.BUILTIN_CATALOG`（Messier 110 + 7 个常用亮 NGC；坐标/星等/视大小/类型来自 OpenNGC `NGC.csv` + `addendum.csv`（CC BY-SA 4.0），英文常用名取 `Common names` 列（36/117），中文名为 Wikidata 标签（29）与自译（5）共 34/117，缺失字段一律 `null`；旧版 Stellarium/GPL 来源数据已废弃，不再引用，源文件 SHA 见 `mbdsdr_ai/data/dso_catalog.json` 的 meta）；`desktop/rf_sky_view.py::_draw_dso`；`tests/test_sky_dso_layer.py`、`mbdsdr_ai/tests/test_dso_catalog.py` | 数据层有 6 类渲染类型（`galaxy`/`cluster`/`nebula`/`supernova_remnant`，以及新增的 `cluster_nebula`/`double_star`）；视图当前只实现前 4 类符号，新增两类落到星云虚线菱形 fallback，属已知视图缺口（本轮只改数据，不改视图）。符号为星系椭圆 / 星团点环 / 星云虚线菱形 / 超遗双圆；尺寸口径是**「真实视大小 + 超限时限幅并标注」**：角分→像素按投影采样，真实视半径 ≤ 视口短边 40% 时按真实视大小画；只有超出这一阈值的条目才截到 40%（`DSO_CLAMP_VIEW_FRAC`），并画虚线外框 +「已限幅」角标，拾取半径与画出的符号同源（`_dso_symbol_geometry`，不许「符号小、命中大」）。亮度取视星等并过与恒星同一套 Bortle 亮度门、同一地平门、同一白天门。不模拟表面亮度分布、不做曝光/滤镜效果，也不还原旋臂、尘埃带、电离区形态 | 若要形态：接入 DSS/POSS 图像或形状参数目录（位置角/轴比），并补授权与对拍；在此之前符号只作"类型提示"，不可当外观判据。若调整限幅阈值：同步改 `_dso_symbol_geometry` 与 `tests/test_sky_dso_layer.py` 的限幅/拾取用例，别让文档与实现打架 |
| L27 | 太阳地平门按**视高度 + 上缘**（旧的「圆心夹到地平」已删除） | `desktop/rf_sky_view.py::_sun_screen_geom`、`display_apparent_alt_deg`；`tests/test_sky_sun.py`、`tests/test_sky_refraction.py` | 太阳先经显示层折射得视高度，再判「上缘是否在地平上」：几何 -0.833°（标准日落）→ 视高度约 -0.213°，圆心画在地平线**下方**、上缘刚贴地平；不再用 `max(0.0, alt)` 把圆心整体抬高。视直径来自 `solar_system.get_sun_position`（随日地距离变），信息卡视星等 -26.74 仍是**标准常数**、非历表计算。地平门与恒星/行星/月亮/DSO 同一条「视高度 < 0 不画」，无夹取特例 | 若调整折射口径：同步改 `DISPLAY_REFRACTION_*`、`tests/test_sky_refraction.py` 与 `tests/test_sky_sun.py`；不得把圆心夹取加回来 |
| L28 | **银河图层接入所需（当前仍未接入，L25 的交接单）** | `desktop/rf_sky_view.py::GALAXY_LAYER_NOTE`（状态卡如实标注「无数据·不提供」）；`tests/test_sky_dso_layer.py::test_status_card_states_no_galaxy_layer` | 数据形式三选一：① **等积柱状图**（HEALPix 或经纬格网，每格平均表面亮度 mag/arcmin²）——与现有球面投影链最贴合、逐格投影即可，代价是窄 FOV 下格子分辨率不足；② **低分辨率亮度贴图**（等距圆柱/银河带纹理，1–2°/格）——实现最快，但透视投影需重采样、天极附近畸变；③ **星表密度合成**（用真实星表按密度/亮度积分渲染）——最贵，要海量星表与亮度模型，超出本仓「精简星表」预算。**来源与授权**：只用公开域/可再分发的数据（如 ESO 公开图、Gaia 派生合成、NASA/GSFC 银河图），逐项核对许可并写入 NOTICE 与本总账；不得用 GPL 或版权不明素材，也不得自绘「像银河」的装饰带。**接入点**：`desktop/rf_sky_view.py` 绘制层，顺序 `_draw_stars` → `_draw_constellations` → **银河** → `_draw_dso`；坐标链 `vec_from_radec` + `j2000_to_altaz`（与恒星同一链，不另建）；可见性门与恒星同一套（白天 `stars_hidden_by_daylight()` 整层不画，地平线下不画）；性能按视场裁剪可见网格块，不做全天空逐格。**交接测试**：固定时刻合成小网格 fixture，断言投影落点与亮度梯度方向（不做像素级金样），并保留授权出处字符串 | 若用户拍板接入：先定数据形式与许可，再补绘制层/测试/状态卡文案（把「无数据·不提供」替换为实际来源与口径），最后更新 L25/L28 |
| L29 | 快转（×60/×300/×1800）一致性是**按模拟时刻重算 + 诚实提示**，不是「延平滑」 | `desktop/rf_sky_view.py`：`LOCK_SMOOTH_K/LOCK_SMOOTH_MAX_STEP_DEG=0.25°/LOCK_FAST_SLIDE_STEP_DEG=25°`、`TRAJ_MIN/MAX_STALE_S`、`PASS_REFRESH_SIM_S=300s/PASS_REFRESH_WALL_S=1s`；`tests/test_sky_fast_time.py`（15 条） | 三处口径：① 锁定靶位每 tick 现算真 SGP4（无缓存）；|rate|>1 且单帧目标位移 >0.25° 时由 25% 指数平滑改为**逐帧直贴**，并一次性提示「×N 快转下单帧位移 X°，平滑跟随跟不上，已改为逐帧直贴」；位移 >25°/帧再加「画面按帧跳进，建议降速观察」——**不静默改手感，也不让滞后/追赶造成抽搐**。② 轨迹带 `anchor_unix`，快转下锚点落后当前模拟时刻 >max(10s, 2 帧模拟推进)（cap 120s）即用同一 TLE/坐标链重算未来 10 分钟；无 TLE 可重算则撤线（不画过期曲线）；1x 与不带锚点的旧调用语义不变。③ 过境面板峰值由 5 分钟粗采样 + 邻域 30 秒细化，行内 `peak_unix` 精确、`rise` 显示到**秒**（分钟取整会让近天顶过境与天空差出数度，实测 NOAA19 87° 峰整分钟处低 11.9°）；快转下按模拟时间差 ≥300s 重算（墙钟下限 1s 防每帧扫 24h），已过峰值由视图侧按 `peak_unix` 过滤，不再冒充「未来过境」。**已知量化边界**：×1800 时每帧模拟 60s，直贴也只能按帧跳进（这是倍率本身的表现）；面板基准在墙钟下限内最多滞后 ~30 分钟模拟时间（过期行已过滤，消失的行属诚实缺口而非错误）；轨迹重算频率受 33ms 帧节拍约束 | 若调整阈值：同步改 `rf_sky_view.py` 常量与 `tests/test_sky_fast_time.py`（含「1x 仍是 25% 平滑」「无 TLE 撤线」「秒级显示」三条钉死用例）；若要更频繁的面板重算，先量 `_refresh_passes` 单次成本再定 `PASS_REFRESH_WALL_S` |
| L30 | 屏幕读数：**单一格式化实现 + 显示值对拍 + 防误导舍入** | `desktop/rf_sky_view.py` 的 `_format_number/format_angle_deg/format_azimuth/format_distance_km/format_distance_au/format_magnitude/format_arcsec/format_arcmin/format_percent/format_utc_time/format_local_time`；`tests/test_sky_readouts.py`（21 条） | 所有界面数值只经这一处格式化：角度写 `°`、方位同时给 16 方位名（`210.3° SW`）、距离写 `km` / `AU · 万·亿 km`、星等写 `mag`、角径写 `″/′`；时间行标签写明 **UTC**，状态卡另给本地时刻与 `UTC±HH:MM` 偏移（偏移由 tzinfo 现算，夏令时切换自动正确）。舍入策略：非零但按显示精度会舍成 0 的量显示 `<0.1°`（负值 `>-0.1°`），百分数在 0/100 边界自动多给一位（`99.6%` 不显示 `100%`，`0.04%` 不显示 `0.0%`），角径至少 1 位小数（`1.4″` 不显示 `1″`）。对拍口径：同一时刻/站址、与后端同源（SGP4/历表/星表/过境峰值）独立算一遍，再把**显示串解析回来**按「半格显示精度」比对；卫星标签、对象文案、信息卡、过境面板、状态卡、时间标签逐项覆盖。**边界**：本视图不显示卫星自身视星等（无光度模型，信息卡如实 `—`）；本地行用系统时区，若运行环境 TZ 配置错误则本地行随系统错（UTC 行不受影响） | 新增读数必须走同一组 `format_*` 并补对拍用例；改显示精度时同步改 `tests/test_sky_readouts.py` 的半格容差与「一个显示单位可见」用例；校验格式化单点实现：`strftime(` 仅允许 3 处（UTC/UTC datetime/local）、度分秒模板仅 1 处 |
| L31 | 摄像机锁定与多普勒跟踪**双环并行**的状态一致性（四种组合 / 顺序 / 换目标 / 站址失效 / 诚实边界） | `desktop/rf_sky_view.py`：`loop_state_lines/track_target_name`、HUD 两行常驻状态、信息卡新增「视角 / 调谐」两行、`_track_notice`、`set_tracking_checked` 的诚实回写、`_on_station_lost`、`_update_tune_button` 的换星/失效分支；`tests/test_sky_dual_loops.py` | **确定语义**：① 两个环语义独立：勾选态、目标、解锁条件互不连带；只开一个时 HUD/状态卡必须写「仅视角，未在调谐」或「未锁定（仅调谐）」，不允许界面看起来两个都在跑。② 开环顺序无关：先视角后调谐、先调谐后视角的最终状态与状态行一致（只有用户可见提示的先后差）。③ 换选另一颗星：**摄像机跟随新选中并重锁**；调谐跟踪**停止且不偷偷换频**，提示「目标由 A 切换到 B，需重新开启跟踪」，云台跟随随跟踪停。反选（`on_object_picked(None)`）/站址失效（`set_observer(None…)`）：两个环都收口，摄像机解锁并说明原因（反选 / 观测站失效）。④ 诚实边界：调谐环启动失败（无 SDR / 启动被拒）或过境自动结束时，主窗口回写 `set_tracking_checked(False)`；若摄像机仍锁定，必须提示「调谐跟踪已停止（或未启动）…摄像机锁定仍保持（仅视角跟随，不会自动换频）」，且 HUD 调谐行显示「未开启」。反向同理：摄像机锁定被拒（轨道数据/站址不可信）时跟踪保持勾选，状态行显示「摄像机: 未锁定（仅调谐）」。**附修**：HUD 通知原用不存在的 `COLORS["warning"]` 键，任何通知一显示就 `KeyError`（潜伏崩溃）；本轮改用 `COLORS["danger"]`。**边界**：外部停止原因由主窗口判定，视图只能知道「原本勾选→被回写 False」，故文案用「已停止（或未启动）」而不臆测具体硬件原因；云台跟随是跟踪环的从属，不构成第三个独立环 | 新增任何跳过/停止路径必须走 `set_tracking_checked(False)` 或 `_stop_track_ui()`，否则会出现「看起来都在跑」的单边假状态；改 HUD/状态卡时同步改 `tests/test_sky_dual_loops.py` 的四种组合、顺序无关、换星停跟踪、反选/站址失效、外部回写五组断言；新增调色板键先查 `tokens.COLORS` 实际键名，勿用未定义键 |
| L32 | 时间控制与摄像机锁定的交互语义（倍率切换 / 一次性跳时 / 暂停 / 倒放） | `desktop/rf_sky_view.py`：`notify_time_jump/_pending_time_jump`、`_apply_camera_lock` 的跳转分支、`_set_rate` 暂停先钉时刻、`_update_label` 的「实时 / ⏸ 暂停 / ×N 模拟时间 / 倒放 ×N 模拟时间」；`tests/test_sky_time_lock.py` | **确定语义**：① 倍率切换只改 rate、不重置模拟时刻，锁定不断；|rate|>1 时逐帧直贴，切档首帧只吸收切档前的 1x 平滑残差（单向，不上冲、不回弹），之后画面位移 = 目标真实位移；回 1x 时 `_refresh_fast_time_positions` 复位直贴状态，恢复 25% 指数平滑（运动目标稳态滞后 ≈ 3 倍单帧位移，LEO 30fps 约 0.1–0.15°，已按实测修正旧注释里「<0.02°」的口径）。② 一次性跳时（面板 ±10m / 跳到此刻 / 引擎 set_unix）统一走 `notify_time_jump`：目标仍在地平线上 → 继续锁定并**直接贴住**新时刻位置，提示「时间跳转 X：…仍在天空…已按新时刻直接贴住并继续锁定」；目标已落地/轨道失效 → **诚实解锁**并提示「时间跳转 X 后…地平线下/数据失效」；未锁定则无副作用。③ 倍率是**模拟时间推进速度**、不是画面帧率：×1 标「· 实时」，暂停标「⏸ 暂停」，快转标「· ×N 模拟时间」，倒放标「· 倒放 ×N 模拟时间」；时间标签与各倍率按钮 tooltip 均写明「与画面帧率无关」。④ 暂停：跟随墙钟态（`_sim_jd=None`）下先 `set_unix(now)` 钉住再 `pause()`，否则 `tick` 早退导致「⏸ 点了没反应」（本轮修复）。倒放：引擎支持负 rate，锁定与轨迹按当前模拟时刻重算成立（独立 SGP4 对拍 <0.05°）；**界面不提供倒放按钮**（不留点了没反应的控件），仅在外部把引擎设为负倍率时标签如实显示。**边界**：×1800 每帧模拟 60s，直贴也只能按帧跳进（倍率本身的表现，提示文案已说明）；1x 平滑滞后是 25% 语义的固有量，不是跟踪失败 | 若调整 `LOCK_SMOOTH_K`/跳转语义：同步改 `tests/test_sky_time_lock.py`（切档首帧只许吸收残差、回 1x 走 0.025°/0.1°、落地必解锁、暂停冻结时刻四条）与 `tests/test_sky_fast_time.py`；新增一次性跳时入口必须调用 `notify_time_jump`，否则目标落地会假跟踪 |
| L33 | goto 动画改为**最短大圆向量插值**（不再 az/alt 各自线性） | `mbdsdr_ai/sky_interaction.py`：`GotoAnimation.__post_init__` 的 `_v_from/_v_to`、`_slerp_unit()`、`step()` 的终点精确落位；`mbdsdr_ai/tests/test_goto_animation.py`、`tests/test_sky_goto_path.py` | 中心指向走单位向量 SLERP（最短大圆），fov 仍线性；OutCubic 200ms 时长与首段手感不变（1x 与 ×1800 都按墙钟驱动）。近天顶不再先俯冲再爬升，跨 0°/360° 不走 340° 远路，相邻帧路径连续；终点用 `target_az % 360 / target_alt` 精确落位，不走 SLERP 浮点尾差。对跖点（θ≈180°）大圆不唯一：取一条确定垂面路径，保证有限/连续（不做「物理正确」的随机选择）。**边界**：`rotation` 不参与插值（既有语义）；`_az_delta` 仍保留为最短弧展示量，但实际路径由向量插值决定 | 调时长/缓动同步改 `DEFAULT_GOTO_DURATION_MS` 与两个测试文件（中点 0.875、跨 0° 路径长=直连、近天顶不俯冲、终点精确）；不要把 az/alt 线性插值加回来 |
| L34 | 卫星受晒/地影**只做渲染侧**，判据由后端提供 | `desktop/rf_sky_view.py`：`SkyObject.sunlit`（True/False/None）+ `sunlit_status`（后端 `Illumination.status` 原文）、`_draw_objects` 卫星分支（仅本影压暗本体 + 暗色虚线描边）、状态卡「受晒」行、`sunlit_state_label()/sunlit_is_eclipsed()`；`tests/test_sky_sunlit_state.py` | **接入现状**：`mbdsdr_ai` 里实时轨道快照（`orbit._state_from_satrec` / 天空图卫星快照）**没有** `sunlit` 字段；后端另有在途模块 `mbdsdr_ai/sat_illumination.py`（`Illumination.status/sunlit`，圆锥影同 gpredict `sat-vis.c`，半影→`sunlit=None`，文档 `docs/sat-illumination.md` 明确「渲染层只依赖 status/basis，unknown 必须显示未知」），但尚未写入本视图消费的快照。**本线口径**：只消费 `SkyObject.sunlit/sunlit_status`；`status=sunlit`→「受晒」、`umbra`→「地影」+ 暗色描边、`penumbra`→「半影（部分受照）」（**不画阴影**）、`unknown`/`None`→「未知（无受晒数据）」且**不画阴影**；非卫星→「—」。**绝不**在本视图自算太阳/地球阴影几何，也不把默认值当受晒。**待后端**：轨道快照同时输出 `sunlit: Optional[bool]` 与 `status: str`（`_build_selected_info` 已透传两字段）。**边界**：地影描边是状态提示，不表示亮度/信号衰减；后端未接时全为「未知」，属已知能力缺口 | 后端快照接入：只改快照构造处透传两字段，本线无需再改绘制；若后端改字段名，同步改 `SkyObject` 与 `_build_selected_info`；若后端只给 status 不给 sunlit，`sunlit_state_label()` 的 status 优先级已覆盖；不得用几何近似在渲染层补算 |


| L35 | 大气折射**只在显示层**，且只作用于天球对象；卫星/指向保持几何 | `desktop/rf_sky_view.py`：`display_refraction_delta_deg/display_apparent_alt_deg/format_alt_pair`、`RFSkyView._display_alt_deg/_format_alt_pair/set_refraction_params`；绘制点：`_draw_stars/_draw_dso/_draw_planets/_draw_constellations/_draw_eq_grid/_sun_screen_geom/_moon_screen_frame/_find_body_at`；`tests/test_sky_refraction.py`（9 条） | **口径**：Stellarium `RefractionExtinction.cpp:154-184` 同款分段式（> -3.54° Saemundsson 全式，-5.0°～-3.54° 线性过渡，≤ -5° 归零），默认 **1013.25 hPa / 10 °C**（Stellarium 构造默认 1013/10，差异 <0.03%，显示精度不可见）；可用 `set_refraction_params` 覆盖。**反证**：几何 0.5° → 视 0.918°（旧无折射差 0.418°≈25′）；几何 -0.5° → 视 +0.063°（"尚未升起却可见"）；0° → 0.485°。**读数**：天体信息卡显式「几何 X / 视(折射) Y」，卫星卡与标签显式「几何」，状态卡太阳高度标「视」，过境面板标「峰值(几何)」，天线行标「指向(几何)」——同一数字绝不跨口径。**取舍**：日出日落时刻仍用**几何 -0.833°**（Meeus 标准已含 34′ 折射 + 16′ 视半径，再叠显示折射即重复计一次）；实测 2026-03-20 40°N/116°E：标准 22:18:30 UTC，视高度上缘判据 22:18:10 UTC（差 20s，同事件），改用视高度圆心 0° 为 22:19:50 UTC（晚 80s），改用几何 0° 为 22:22:50 UTC（晚 260s）。卫星/过境/天线/GNSS/热力图仍是几何：这些是指向/跟踪数据，gpredict 对卫星指向也显式忽略折射（`sat-vis.c` 注释 + 本仓 `docs/learn/gpredict.md`），不能让画面位置与指向指令分叉 | 若接实时气象：调用 `set_refraction_params(气压, 温度)` 并在状态卡注明来源；改公式/默认值同步改 `DISPLAY_REFRACTION_*` 与 `tests/test_sky_refraction.py`；不要把折射写回 `SkyObject.elevation_deg`、`self._sun["alt"]` 或任何后端快照 |
| L36 | **没有真实地景遮挡**：地景是装饰性示意剪影，不是实测地形 | `desktop/rf_sky_view.py::_LANDSCAPE_LAYERS/_landscape_alt_profile/_draw_landscape`；地平基准 `HORIZON_ALT_DEG=0` + `_draw_horizon_line`；`tests/test_sky_landscape_layers.py`、`tests/test_sky_refraction.py::test_horizon_is_exactly_alt_zero` | **现状**：`_LANDSCAPE_LAYERS` 是固定值噪声轮廓（远山基线 1.5° + 起伏、树/房层更高），纯装饰；只在绘制层覆盖像素，**不参与天体可见性、不参与拾取、不来自观测站地形数据**。地平基准严格是 alt=0 的几何地平线（`_draw_horizon_line`，画在地景之前，山脊可盖住它但不移动基准），方位盘 N/E/S/W + 每 30° 数字以该线为基准。地平线下残点仍按视高度门剔除（几何 < 约 -0.57° 不画），不回退 | 若要做真实遮挡：接入观测站地平线剖面（DEM 或用户导入的 `.hr`/高度角表），在**绘制+拾取**同一判据里按方位插值遮挡高度，并明示数据来源与分辨率；在此之前不得把示意剪影写成"地形遮挡"，也不得为它接拾取逻辑 |
| L37 | DSO 未知字段在拾取路径曾**静默丢尽全部候选** | `desktop/rf_sky_view.py::_find_body_at`（修复前 `float(e.magnitude)` / `float(e.size_arcmin)`）；`tests/test_sky_dso_layer.py` | 换源后 7 条星等为 `null`。旧代码在 `try/except Exception: pass` 内执行 `float(None)`，抛 `TypeError` 后整段 DSO 候选构造被跳过：**所有 DSO 既不画也不可点，且无报错、无日志**。修复改为 `dso_magnitude(e)` / `dso_size_arcmin(e)`，未知值以 `None` 透传（轴比同理由 `dso_axis_ratio` 返回 `None`）。**规则（比事故本身更重要）**：`except Exception: pass` 只允许用于明确的外部边界降级（第三方库/网络/硬件），且必须同时满足 ① 记录降级原因（日志或计数）；② 有测试覆盖降级路径。编程错误（`TypeError`/`AttributeError`/`KeyError` 等）一律不得被 catch-all 吞掉——宁可让测试红，也不许静默丢功能 | 新增目录字段读取必须走 `dso_*` 访问器；`try/except Exception` 出现处必须能回答「降级后用户看到什么、哪里记录、哪条测试覆盖」；缺字段用例（M31 正常 + M22/M40 未知）必须进图层测试 |

## 7. 工具面参数模糊测试（P13 快照，账本 `docs/tool-param-fuzz.md`）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L38 | 10 万字符文本输入在纯 Python 文本调制/解调器上超出 60s 预算 | `scripts/tool_param_fuzz.py`；`docs/tool-param-fuzz.md`；涉及 `feldhell_decode`、`psk31_decode`、`rtty_decode`、`bell103_modulate_text` | 仅自测（隔离进程）：用例 `long_string` 60s 内不返回。输入本身合法，只是量级过大，属吞吐型 DoS，不是死锁或静默接受 | `python scripts/tool_param_fuzz.py --only feldhell_decode --no-write`；修法是给这 4 个工具补 `maxLength`（如 4096）或入口按字符数拒绝并返回 `param_out_of_range` |
| L39 | `pcm_psk_pm_modulate` 合法基线本身约 15.5s | `scripts/tool_param_fuzz.py`；`docs/tool-param-fuzz.md` | 仅自测：默认 10s 单次上限会把 `extra_unknown_keys`（160 bit、1.024 MS/s、1.2 kbaud 合法基线）记成超时，放宽到 60s 可返回，属纯 Python PM 调制吞吐问题，不是挂死 | `python scripts/tool_param_fuzz.py --only pcm_psk_pm_modulate --timeout 60 --no-write`；隔离复测以 60s 预算覆盖 |
| L40 | handler 内部异常仍以 `exception:<类型>` 形式返回（结构化，但非固定码） | 工具注册表兜底；`docs/tool-param-fuzz.md` | 仅自测：少数工具对语义非法的枚举/几何参数未在 schema 声明 `enum`/`minimum`，非法值进入 handler 抛 `ValueError`/`IndexError`，被兜住返回 `exception:<类型>` + 中文原因。满足「不崩溃、失败结构化」，但错误码随异常类型变化 | 给 `amr_add_sample.label`、`mfsk_decode.mode` 等补 `enum` 并补非法枚举值回归测试 |
| L41 | 单点观测/单源快拍类数组仍可能在 handler 内抛 `IndexError` | 工具注册表兜底；`docs/tool-param-fuzz.md` | 仅自测：`spp_locate` 等传入单点数组时几何解算抛 `IndexError`，注册表兜住并返回结构化失败，但「至少需要 2 点」的约束没有写进 schema | 对这些工具的数组参数补 `minItems: 2`（或 3）后复跑 `scripts/tool_param_fuzz.py` |
| L42 | **C++ 桌面发射路径无多普勒预偏；仅接收侧 `vfoSetOffset`** | `cpp/src/ui/main_window.cpp`（无 TX 侧调谐/预偏调用）；对照 `mbdsdr_ai/sdr_tools.py::resolve_tx_doppler`（`sdr_transmit_cw` 与 `sdr_transmit_iq` 均已走 `doppler_link.tx_freq`）、`mbdsdr_ai/tc_uplink.py`（TC 上行缺省按跟踪状态自动预偏）；`mbdsdr_ai/tests/test_transmit_doppler.py` | **严重但不阻断**（用户判定倾向）。桌面形态发射不会自动预偏：145.8 MHz 下 ISS 视向速度 ~2 km/s 约偏 0.97 kHz、极限可到 ~1.5 kHz，对方可能收不到而用户以为在发射；比赛等场景外发射使用频率低，但一旦使用就是错频。**影响**：桌面形态发射需人工手动预偏，或改走 Python 工具面 `sdr_transmit_cw`/`sdr_transmit_iq`/`tc_uplink_transmit`（已自动预偏并在读数明示 `applied/mode/delta`）。C++ 接收侧 `vfoSetOffset` 不受影响 | C++ 侧在 TX 调谐处接同一预测源（或明确禁止 C++ TX 并路由到 Python 工具面）；**前提：若 C++ 侧加入 TX 预偏，必须先引入独立的 `tx_backend_comp_active`，否则接收侧的 `backend_comp_active` 闸会误关 Python TX 预偏**（当前无生产者，不加死代码）；验收用已知频偏反证：发波频率 − 标称 == 预测 Δ（接近压低），显式关预偏严格等于标称；Python 侧 `test_transmit_doppler.py` 可作对拍基线 |

## 附 A：工具面总账（本轮实测快照）

- 总数：**495**；分类：**75**；`available` 非 True：**0**；`handler` 缺失：**0**；
  `write=True`：**94**；注册表 meta `category="mcp"`：**0**。
- schema/handler 一致性：`scripts/ai_tool_consistency.py` 扫描 **495/495** 可读源码，
  结构性错误 **0**、漂移线索 **0**（P11 修复后）。
- sky 相关 8 个：`orbit_sky_plot`、`sdr_satellite_sky_view`、`sky_capability_report`、
  `sky_capability_state`、`sky_gimbal_follow`、`sky_track_satellite`、
  `sky_tune_satellite`、`sky_view_visible`。
- `tx` 前缀工具：**0**。发射能力不按 `tx_` 命名，实际入口是
  `sdr_transmit_cw`、`sdr_transmit_iq`、`tc_uplink_transmit`、`radio_ptt`。
- 不可用原因：注册表层面无（`available` 全 True）；**运行时**受硬件/样本/模型/网络限制，
  见第 1–4 节。
- MCP 面：`MCPToolSet` 暴露 **82** 个控制器方法面工具，不具备上述 495 个 AI 工具，
  也不含 sky/云台工具（见 L21）。
- 分类分布（快照，按数量降序）：satellite 44、decode 22、sdr_spectrum 16、sdr 15、
  orbit 15、sdr_decode 14、ham_modes 13、sdr_analysis 12、new_spacetime 12、
  digital_voice 12、gnss 11、digital_modes 10、sdr_device 10、code_editor 10、
  dsp 9、sdr_frequency 9、astronomy 9、acquisition 9、sdr_ui 9、signal_analysis 8、
  sdr_network 8、radio_control 8、spectrum 8，其余 52 类各 1–7 个。

复跑：

```bash
python scripts/ai_tool_consistency.py
python -c "from mbdsdr_ai.core.mcp_server import MCPToolSet; print(len(MCPToolSet().list_tools()))"
```

## 附 B：本轮环境特有测试事实

- `mbdsdr_ai/tests`：1 failed（`test_dtmf_handler_shape_guard`，**真红**，非环境：
  P7.2 注册层参数校验先于 handler，旧断言只认 handler 文案；已按新契约修正）。
- `tests/` A 组：1708 passed、1 skipped（约 10 分 50 秒）。
- `tests/` B 组：2 failed、1521 passed、3 skipped（约 12 分 28 秒）。两红：
  ① `test_costas_track_bad_psk_order_is_honest`——与 dtmf 同因（P7.2 注册层
     enum 校验先于 handler），**真红、已修**；
  ② `test_mainwindow_real_hardware_loop`——`LIBUSB_ERROR_ACCESS` 抢设备，
    单跑该文件 1 passed，判**环境特有**（A/B 两个套件并行共用同一 RTL-SDR）。
- `experiments/tests`：34 passed（17.22 秒）。
- `tools/onboarding` + `tools/hw_selfcheck`：54 passed、4 skipped（5.39 秒）。
- skip 口径：无硬件（RTL-TCP/设备）、可选依赖缺失（SatDump/reedsolo/zstandard）、
  无网络（真实 TLE），均为诚实 skip，不计失败。
- CI：`gh` 未安装；GitHub API 部分可用。最新 run `f7f1d27` 进行中；上一 run `b6c4cd9`
  结论：cpp/flutter/两个 tools/experiments 成功，tests A/B 被新推送取消，
  `mbdsdr_ai/tests` 失败（同一 `test_dtmf_handler_shape_guard`）。更早 run 均被并发取消除。

## 附 C：交互覆盖审计遗留（`26ed6c8` 快照）

- 全量结果：遍历 **130** 个动作（QAction 36 / QAbstractButton 94；enabled 104 /
  visible 60）；真死 0、假活 1、抛异常 0、置灰无理由 0；诚实禁用 26、未实时触发 13、
  输入控件 2。逐条证据见 `docs/interaction-audit.md`。
- 唯一假活「工具 / 停止卫星跟踪」：无跟踪任务时 `_stop_satellite_tracking` 在
  `desktop/main_window.py:6584-6590` 主动早退（刻意不刷「已停止」假提示），触发后
  状态栏/弹窗/控件状态均无变化。正确修法是按 `_sat_track_timer/gimbal_follow` 置灰
  菜单项；`main_window.py` 归操作逻辑线，已转交（登记表的假活白名单与置灰理由随之
  更新），本线不复测、不碰该文件。
- 两条「幂等刷新」白名单：`DevicePanel 刷新`（`device_panel._refresh_devices`）与
  `对话框:选择 SDR 设备` 刷新（`main_window._reload_devices`）。设备重枚举成功但
  集合未变时界面零变化，不是死按钮；本轮有 2 条未复现 warning（随设备枚举结果变化）。
  后一条已由操作逻辑线补状态栏结果提示；`desktop/device_panel.py` 那条同归操作
  逻辑线。
- 守卫：`tests/test_ui_interaction_guard.py` 遍历式断言（新增动作无接收者即红；
  白名单必须指向真实控件且带理由），本轮 3 passed。
- 复跑：`MBDSDR_OFFLINE=1 MBDSDR_NO_HW=1 python scripts/ui_interaction_audit.py`
  （刷新 `docs/interaction-audit.md`）；`python -m pytest tests/test_ui_interaction_guard.py -q`。

## 附 D：L34 受晒接线复核（2026-11-05）

- **后端已接入，L34「待后端」一句已过期**：`mbdsdr_ai/orbit.py::_state_from_satrec`
  （`fd3574d`「卫星状态快照透传受晒三态，统一过境口径」）已输出
  `sunlit` / `sunlit_status` / `illumination_model` / `boundary_uncertainty_s`，
  与 `mbdsdr_ai/sat_passes.py::_is_sunlit_geometric` 共用
  `sat_illumination.evaluate_illumination` 唯一口径（不再各算一套）。
- **视图侧只消费快照字段，不自算几何**：`desktop/rf_sky_view.py` 的
  `_sat_positions` 原样透传 `state.get("sunlit"/"sunlit_status"/"boundary_uncertainty_s")`；
  状态文案优先级 status > sunlit：`umbra`/`sunlit=False` → 「地影」并画压暗 + 暗色虚线描边；
  `penumbra` → 「半影（部分受照）」且**不画阴影**；`unknown`/`None` → 「未知（无受晒数据）」
  且**不画阴影**；非卫星 → 「—」。
- **如实缺口**：`illumination_model` 未进 `SkyObject`（渲染不需要该字符串；
  若将来要在卡上展示模型名，需加字段并透传，属可选扩展，不是数据造假）。
  `boundary_uncertainty_s` 只在 `SkyObject`/`_build_selected_info` 保留、不渲染，
  用于解释刚进/出影时的三态跳变。
- **证据**：`tests/test_sky_sunlit_state.py` 9 条；`mbdsdr_ai/tests/test_orbit_sunlit_snapshot.py`。
  2026-11-05 全量 `pytest tests -k sky`：443 passed / 1 failed；唯一红是跨线的
  `tests/test_main_window.py::TestPlanetariumSkyVisibility::test_planetarium_sky_paints_not_black_void`
  （持久化 `three_column_sizes` 使天空视图变高，底边落在 `ground_near_night` 地景层，
  与受晒无关，本线不碰 `main_window.py`）。

## 附 E：发射可见性与真机未验证（2026-11-05，追加）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L43 | **三后端发射全部 `tx_verified=False`**：HackRF / USRP / Pluto 都是「驱动级已实现、真机未验证」。PTT 按下前必须显示「本后端发射：驱动级已实现 / 真机未验证」，不得显示「支持发射」 | `SDRDevice.tx_verified`（`mbdsdr_ai/sdr_backend.py`）；`backend.tx_capability()` 四态 `declared/implemented/ready/verified`；`tests/test_backend_tx_capability.py::test_tx_capability_marks_all_three_unverified`；UI 契约见 `docs/doppler-compensation.md` §3.8.1 | 仅自测（mock 驱动 + 假后端真调 `write_tx`，无真机发波） | 每后端各备一台真机 + 衰减器/假负载 + 接收机，按 §3.8.1 先在 PTT 前显示「真机未验证」，再真发并核对频点/波形；通过后才可把该后端 `tx_verified` 置 True |
| L44 | **SoapySDR 通用路径的「把能说成不能」**：`supports_tx()` 恒为 False（本类无 `write_tx`），而 HAL `getNumTxChannels()` 对 HackRF/USRP/Pluto 常 >0。桌面现有 PTT 文案按 `supports_tx()` 显示「当前设备不支持发射（后端没有 TX 通道）」，会把「本路径未接写流」误报成「硬件不能发」 | `mbdsdr_ai/sdr_backend.py::SoapySDRBackend.tx_capability()` 新增 `driver_tx_channels` / `driver_capable` / `blocked_reason` / `notice`；`tests/test_backend_tx_capability.py::test_soapy_generic_reports_driver_capable_third_state`；旧文案在 `desktop/main_window.py`（属操作逻辑线，本线不改） | 后端已能给出第三种状态「驱动级可发 / 本路径未接入（不可发射）」；**UI 仍显示旧文案**，属已知假信息缺口 | 操作逻辑线把 PTT 置灰文案改读 `tx_capability()` 的 `blocked_reason/notice`（按 `docs/doppler-compensation.md` §3.8.2 判定顺序与文案表），用 Soapy 路径接一台能发的 HackRF/USRP 复测：显示「驱动级可发 / 本路径未接入」，而不是「没有 TX 通道」 |

## 附 F：真机发射验证清单（准备件，未执行；2026-11-05 追加）

> **本附录只写"怎么验、什么算过、谁能改状态"。当前三个后端仍是
> `tx_verified=False`，任何人（包括 AI/代理）不得自行改成 True。**

### F.1 通用安全前置（每后端都适用）

1. **必须接假负载或 ≥30 dB 衰减器 + 屏蔽**，禁止裸接天线；确有法规频段要求
   （业余执照/ISM/屏蔽箱）时以当地法规为准，拿不准就不发。
2. **最小功率起测**：发射增益置最低档，先短时长（≤5 s）单音，确认频点后再加。
3. 发射前确认目标频段无人使用；测试频点优先选 435 MHz（业余）或 915 MHz
   （ISM，按地区裁定），避开 GPS/航空/应急频段。
4. 全程有人值守；出现异常（驻波/过热/杂散超限）立即停发并断开。
5. 每次只验一个后端，不同时上电；记录设备序列号。

### F.2 每后端最小步骤与判据

| 后端 | 最小验证步骤 | 通过判据（全部满足才算过） |
| --- | --- | --- |
| **HackRF One** | ① 假负载 + 50 Ω 衰减；② 频率 435.000 MHz、采样率 2 MS/s、增益最低档；③ 走本仓 `write_tx` 发 3–5 s 单音（基带 +10 kHz 偏移）；④ 用第二台 SDR/频谱仪在同轴取样口观察；⑤ 再发一段 1 kHz 方波 IQ，确认波形结构；⑥ 记录 `status.error`、`last_tx_error`、`tx_error_count` | 中心频点落在 435.000 ±0.005 MHz；偏移单音位置与基带设置一致；谐波/杂散 ≤ −40 dBc 且不落在受保护频段；`write_tx` 返回 True、`last_tx_error` 为空、无 underrun；重复 3 次结果一致 |
| **USRP（UHD）** | ① 同 F.1；② `tx_gain` 最低、435 MHz、1 MS/s；③ `get_tx_stream` + `TXMetadata` 发 3–5 s 单音（含 EOF 收尾）；④ 频谱仪核对频点/功率/杂散；⑤ 记录 `TXMetadata` 返回与流关闭耗时 | 频点与功率符合设置（功率以实测为准，不采信标称）；EOF 正常收尾且无挂起；杂散 ≤ −40 dBc；无 `U`/`O` 溢出告警；重复 3 次一致 |
| **PlutoSDR** | ① 同 F.1；② `tx_attenuation` 最大（最低功率）、915 MHz（按地区）、2 MS/s；③ `Buffer.write/push` 发 3–5 s 单音；④ 频谱仪核对；⑤ 拔插复位后重跑一次，确认状态不残留 | 频点 915 ±0.005 MHz（或所选频点 ±5 kHz）；`push` 返回成功、无 `-EBUSY`/欠载；复位后 `tx_capability()` 回到未连接态且 `verified` 仍为 False（说明未被硬编码）；重复 3 次一致 |
| **Soapy 通用路径** | 本路径**未实现** `write_tx`，不列入"可发射验证"；只验第三态显示：接一台能发的 HackRF/USRP，确认 `tx_capability()` 给出 `driver_capable=true`、`blocked_reason=driver_capable_path_not_implemented` | UI 显示「驱动级可发 / 本路径未接入（不可发射）」且按钮置灰；不得显示「没有 TX 通道」。**此项通过也不得把 `tx_verified` 置 True**（本路径仍不能发） |

### F.3 「未验证」在界面上如何持续可见

- **常驻**：PTT 按下前显示 §3.8.1/§3.8.2 的「本后端发射：驱动级已实现 /
  真机未验证」，按钮可点但带黄色警示；不是 tooltip、不是按失败后弹一次。
- **字段驱动**：文案只读 `backend.tx_capability()["verified"]`（源头
  `SDRDevice.tx_verified`），UI 不得缓存成常量、不得按设备型号猜。
- **状态面**：`get_tx_gate().snapshot()` 与 `tx_capability()` 随每次连接重算，
  断开后回到「未知/未连接」，不会保留上次的"已验证"字样。
- **登记面**：本条 L43 在验证完成前不得删除或改成「已验证」；`verified=false`
  时任何界面/文档不得出现「支持发射」「已验证」用于三后端。

### F.4 谁能把 `tx_verified` 改成 True（硬门槛）

1. **只有实际执行 F.2 且拿到实测记录的真人**可以提交改动；AI/代理/自动化
   任务**一律不许**改这个字段，也不许用"测试通过"替代真机证据。
2. 提交必须同时附：日期、设备型号 + 序列号、频点/功率/衰减配置、频谱截图或
   数据文件路径、3 次重复结果、执行人 + 复核人；记录写入
   `docs/real_hw_acceptance_*.md`（新建当次文档，不改旧记录）。
3. 改动范围：`mbdsdr_ai/sdr_backend.py` 对应后端的 `SDRDevice.tx_verified=True`
   （只改已验证的那一个后端），同步更新 `tests/test_backend_tx_capability.py`
   的断言与 L43；两者必须同一提交。
4. 复核人未签字前，字段保持 False；验收记录缺失即视为未验证。

## 附 G：TX 审计日志落地策略（2026-11-05 追加）

> **结论**：发射审计默认落 `D:\mbdsdr\logs\tx_audit.jsonl`（不写 C 盘用户目录）；
> 超限**删最旧**而不是停写（停写会让 TX 变成不可发射）；每次真实发射另落一条
> 结果记录；任何密钥在落盘前打码。fail-closed 仍只作用于"发射前"的签发/放行，
> 发射后的结果记录失败不阻断（射频已发出，事后记录无法撤回）。

### G.1 默认路径与优先级

| 顺序 | 来源 | 值 |
|---|---|---|
| 1 | 环境变量 `MBDSDR_TX_AUDIT_PATH` | 原样使用（测试/受信任脚本显式指定） |
| 2 | 设置项 `tx_audit_path`（`~/.mbdsdr/config.json`） | 原样使用（支持 `~` 展开） |
| 3 | 内置（Windows 且有 D 盘） | `D:\mbdsdr\logs\tx_audit.jsonl` |
| 4 | 内置（无 D 盘） | `<第一个非 C 盘>:\mbdsdr\logs\tx_audit.jsonl`（D 优先） |
| 5 | 内置（机器只有 C 盘） | `<程序目录>\logs\tx_audit.jsonl` + 告警（仍**不是** C 盘用户目录） |

测试断言：`tests/test_tx_audit.py::test_default_path_is_not_c_drive`——盘符 ≠ `C:`，
且路径不在 `~`（用户目录）下。

### G.2 轮转与保留（超限删最旧，绝不停写）

| 设置项（磁盘键） | 默认 | 范围 | 单位 | 含义 |
|---|---|---|---|---|
| `tx_audit_max_mb` | 50 | 1–1024 | MB | 单个文件到上限即轮转 |
| `tx_audit_keep_files` | 10 | 2–100 | 个 | 当前文件 + 轮转备份的总数 |
| `tx_audit_retention_days` | 365 | 1–3650 | 天 | 备份最长保留天数（与份数取先到者） |

- **超限行为**：写前发现当前文件 ≥ 上限 → 删掉最旧的 `.N` → 依次后移 →
  当前文件改名 `.1` → 继续写。份数或天数任一超限都只删最旧；当前活动文件永不删除。
- **为什么必须删最旧**：若改成"超限停写"，审计写失败会按 fail-closed 拒绝签发/
  放行，等于 TX 永久不可发射；删最旧只损失最老的历史，保住发射可用性。
- **量级依据**：一次发射约 3 条记录（`armed` / `allowed` / `tx_ok`），单条约
  600 B；按 1,000 次发射/天估算 ≈ 1.8 MB/天 ≈ 650 MB/年。50 MB × 10 份 ≈
  500 MB，覆盖约 9 个月且磁盘占用有硬上界；峰值 5 次发射/秒时单文件约 4.6 小时
  写满，但只影响最旧文件，不停止写入。
- 测试：`test_rotation_deletes_oldest_and_keeps_newest`（`.3` 不存在、最新
  `tool-59` 完整、最旧 `tool-0` 已删）；`test_rotation_by_size`（轮转后仍只留
  token 前 8 位）。

### G.3 每次发射记什么

每次**过门之后**的真实发射尝试追加一条 `result=tx_ok` / `tx_failed`：

| 字段 | 内容 |
|---|---|
| `ts_local` / `ts_utc` | 本地与 UTC 时间（毫秒） |
| `tool` | 工具名（`TxSession` 记 `TxSession.transmit`） |
| `freq_hz` | 发射中心频率（Hz） |
| `args_summary` | 参数摘要（已剔除门参数，≤ 240 字符） |
| `token_prefix` | token 前 8 位；签发来源看同一次发射的 `armed` 记录 |
| `result` / `code` / `reason` | 成功或失败原因（如 `tx_write_failed`） |
| `source` | `desktop` / `test` / `script` |

**密钥红线**：`api_key` / `access_key` / `token` / `secret` / `password` /
`authorization` 等键名整值打码为 `***`；自由文本中的 `key=value`、
`Authorization: Bearer …`、`sk-…` 形态同样打码。测试：
`test_secrets_are_redacted_in_audit_file`、`test_args_summary_redacts_secret_named_keys`。

### G.4 交给设置线的可照抄条目（本线不直接改 `desktop/config_schema.py`）

> 说明：schema 键名以 `ai_` 开头时，设置面板按 `key[3:]` 映射到
> `~/.mbdsdr/config.json` 的 `AgentConfig` 字段（见
> `desktop/settings_panel.py:499` 的 `_disk_key`）。故下面四项都加 `ai_` 前缀。

```python
    # 「存储与日志」组：TX 发射审计（后端线 2026-11-05 拟定，设置线照抄）
    SettingItem(
        key="ai_tx_audit_path", group="存储与日志", kind="text",
        label="发射审计日志路径", default="",
        storage="ai_config",
        help=("每次发射（签发/放行/结果）的审计 JSONL 存到这里。留空 = 自动选"
              "非 C 盘：D:\\mbdsdr\\logs\\tx_audit.jsonl（无 D 盘则程序所在盘/"
              "程序目录 logs，绝不写 C 盘用户目录）。改路径只影响新记录，"
              "旧文件不动。"),
        source="mbdsdr_ai/tx_guard.py:default_audit_path（默认 D:\\mbdsdr\\logs）",
    ),
    SettingItem(
        key="ai_tx_audit_max_mb", group="存储与日志", kind="int",
        label="发射审计单文件上限", default=50, minimum=1, maximum=1024, step=1,
        storage="ai_config",
        help=("单个审计文件到多少 MB 就轮转。默认 50 MB：按 1,000 次发射/天、"
              "每次约 3 条、单条约 600 B 估算约 1.8 MB/天（约 650 MB/年）。"
              "超限删除最旧文件，不会停止写入（停写会导致无法发射）。"),
        source="mbdsdr_ai/tx_guard.py:TX_AUDIT_MAX_MB=50",
    ),
    SettingItem(
        key="ai_tx_audit_keep_files", group="存储与日志", kind="int",
        label="发射审计保留份数", default=10, minimum=2, maximum=100, step=1,
        storage="ai_config",
        help=("当前文件 + 轮转备份的总个数。默认 10（当前 + 9 份备份，配 50 MB "
              "上限共约 500 MB）。超限删除最旧，当前活动文件永不删除。"),
        source="mbdsdr_ai/tx_guard.py:TX_AUDIT_KEEP_FILES=10",
    ),
    SettingItem(
        key="ai_tx_audit_retention_days", group="存储与日志", kind="int",
        label="发射审计保留天数", default=365, minimum=1, maximum=3650, step=1,
        storage="ai_config",
        help=("备份文件超过多少天自动删除（与保留份数取先到者）。默认 365 天。"
              "当前活动文件不受此限，永不因天数删除。"),
        source="mbdsdr_ai/tx_guard.py:TX_AUDIT_RETENTION_DAYS=365",
    ),
```

对应 `mbdsdr_ai/config.py` 已加同名字段（`tx_audit_path=""`、
`tx_audit_max_mb=50`、`tx_audit_keep_files=10`、
`tx_audit_retention_days=365`），并支持 `MBDSDR_TX_AUDIT_MAX_MB` /
`MBDSDR_TX_AUDIT_KEEP_FILES` / `MBDSDR_TX_AUDIT_RETENTION_DAYS` 环境变量覆盖。

### G.5 已知边界（诚实记录）

- **只有 C 盘的机器**没有合法"非 C 盘"位置；此时退到 `<程序目录>\logs`（不是
  C 盘用户目录）并告警。`test_default_path_is_not_c_drive` 的"盘符 ≠ C:"断言
  只在存在 D 盘的本机成立，CI/纯 C 盘机器上会命中该边界。
- **轮转删旧失败**（最旧文件被其它进程锁住）时仍按 fail-closed 拒绝本次写入并
  限流告警；这是"删最旧"不可用时的安全降级，不是常态。
- **发射后的结果记录**写失败不阻断发射，只在 `tx_audit_status()` 的
  `write_failures` / `last_error` 如实计数；与"发射前"的 fail-closed 语义不同。
- 测试数字：`tests/test_tx_audit.py` 21 passed（含默认盘符、轮转删最旧、
  密钥打码、不可写路径 fail-closed、发射结果成功/失败）；关联回归
  `tests/test_tx_session.py` + `tests/test_tx_rx_exclusive.py` +
  `tests/test_config_key_migration.py` 共 31 passed。

## 附 H：静噪「同一数字、两种语义」处置（2026-11-05，参数治理线追加）

> **结论：采纳方案 ②**（禁止把桌面滑杆值直传 C++；C++ 内核只走自动门，
> 桌面滑杆只管 Python 解调链，界面明示当前生效的是哪一套）。
> 理由：不制造第二套语义、不改任何检测器数学、不假装统一；改动只在 4 个边界点。

### H.1 问题（已实测，非推测）

| 位置 | 数字 `-50` 的含义 | 往 `-20` 拧的后果 |
|---|---|---|
| 桌面 `BaselineSquelch` / `margin_threshold_db()` | 比该模式噪声基线高 **6 dB 余量** | 余量 16 dB，**更少放行** |
| C++ `SpectrumEngine::setSquelchThreshold` | 48 kHz 音频块 RMS **绝对 -50 dBFS** | 绝对门抬高到 -20，**几乎全开** |
| `mbdsdr_ai/receive_chain.py:Squelch(threshold_db=...)` | 同样是 **RMS dBFS 绝对门**（默认 -50） | 同样更开放门 |
| `mbdsdr_ai/analog_demod.py:_MODE_DEFAULTS` | IF 功率绝对门（am/fm -40、wfm -30、ssb -60） | 与滑杆完全无关 |

两条隐蔽通路会让语义串线，**且都是静默的**：

1. `mbdsdr_ai/sdr_backend.py:set_squelch` 现在只做 `status.squelch_db = db`，接收链重建时
   `receive_chain` 把这个值直接塞进 `Squelch(threshold_db=...)` → 桌面余量 `-50`
   被当成绝对 `-50 dBFS`。agent 工具 `sdr_set_squelch`、`remote_control_v2`
   的 `set_squelch` 都走这条。
2. `cpp/src/dsp/spectrum_engine.cpp:setSquelchThreshold` 里
   `squelchAuto_.store(false)` —— 任何一次手动下发都会**关掉自动门**。所以
   "把滑杆值发给 C++"这件事本身就会顺手破坏 C++ 的自动跟踪。

### H.2 三选一（代价与后果）

| 方案 | 改什么 | 后果 | 工作量 |
|---|---|---|---|
| ① C++ 改余量口径 | `cpp/src/dsp/spectrum_engine.cpp` 的 auto 推导改成 `floor + margin` 并让手动门也解释为余量；`cpp/src/dsp/squelch.cpp/h` 比较语义；C++ 滑杆文案；agent schema；C++ 单测 + 真机/录音回归 | 两端语义真统一，桌面滑杆可同时控 C++；但 **改变了 C++ 现有静噪行为**（口径变化），所有依赖绝对 dBFS 门限的扫描/校准/agent 调用都要重验 | 中-大：4–6 个 C++ 文件 + 两套测试 + 真机回归 |
| ② 禁止直传 + C++ 只走自动门（**采纳**） | 4 个边界点（见 H.3），加 `applied_by` 字段与界面文案；**不动任何检测器数学** | 桌面滑杆不再影响 C++ 内核静噪；C++ 用自己的滑杆/自动门（底噪 + 8 dB）。用户需要在界面上看到"当前生效：桌面余量 / 内核自动" | 小：3 个 Python 文件 + 2 个 C++ 文件 + 文案 |
| ③ 保留两套 + 只标注 `applied_by` | 只加字段和显示 | 若不阻断直传，`setSquelchThreshold` 仍会静默关掉 C++ 自动门 → 界面标注的是**假状态**；所以 ③ 必须包含 ② 的阻断才有意义，单独的 ③ 不可取 | 最小，但不可单独使用 |

### H.3 方案 ② 的具体落点

**字段（Python 侧，`mbdsdr_ai/sdr_backend.py:SDRStatus`）**

- `squelch_db`：语义固定为**桌面余量刻度**（-100..-20），不再解释为绝对 dBFS。
- 新增 `squelch_applied_by: str`：`desktop-margin` / `cpp-auto` / `cpp-manual` / `none`。
  沿用 `doppler_compensation.py` 已有的 `applied_by` 字段惯例。
- 新增 `squelch_threshold_dbfs: float`：**只有真正下发绝对门时才填**，且必须由
  `margin_threshold_db(squelch_db)` + 实测底噪推导，不允许等于滑杆原值。

**拦点（按数据流顺序，四处缺一不可）**

1. `mbdsdr_ai/sdr_backend.py:set_squelch`：写 `squelch_db` 与
   `squelch_applied_by="desktop-margin"`；**不再**把原值传给 `ReceiveChain` 的绝对门。
2. `mbdsdr_ai/receive_chain.py` 构造静噪：改读余量换算后的门限，或直接换用
   `BaselineSquelch`（与 `receive_pipeline` 同一个实现）。两者只能选一套，避免第三条语义。
3. `cpp/src/control/control_hub.cpp:cmdSetSquelch`：请求里增加 `domain`
   （`margin` / `dbfs`）。`domain=margin` 或 `auto=true` 时**不下发**
   `threshold_db` 给 `setSquelchThreshold`，只允许 `auto`；返回 `applied_by="cpp-auto"`。
   `cmdGetSquelchStatus` 同步返回 `applied_by` 与 `threshold_db_domain`。
4. `cpp/src/ui/main_window.cpp`：`squelchAutoBtn_` 勾选时把 `squelchSlider_`
   设为不可拖，`squelchValue_` 文案改为"自动：底噪 + 8 dB"；只有取消勾选才允许手动。
   防"拖一下就静默关自动"。

**怎么让用户看见**

- `desktop/status_panel.py` 读 `applied_by` 显示两种文案之一：
  "静噪 -50（6 dB 余量，桌面生效）" / "静噪 自动（底噪 + 8 dB，C++ 内核）"。
- `desktop/control_panel.py` 滑杆 tooltip 明说"此值只作用于桌面解调链；
  C++ 内核静噪自动跟踪底噪"。
- `mbdsdr_ai/sdr_tools.py:sdr_set_squelch` 描述改成"余量刻度（-100≈0 dB、
  -50≈6 dB、-20≈16 dB）"，返回值带上 `applied_by`，不再宣称直接设 C++ 门限。

**反证测试写法（四条，任一失败即方案 ② 失效）**

1. 新增 `tests/test_squelch_applied_by.py`：fake backend 记录"是否收到绝对门"。
   滑杆 -50→-20 后断言 `status.squelch_db == -20`、`applied_by == "desktop-margin"`、
   绝对门下发记录为空、`squelch_threshold_dbfs` 只可能等于"底噪 + 16"而不是 -20。
2. 新增 `tests/test_receive_chain_squelch_domain.py`：同一段噪声 IQ 用 -50 与 -20
   各跑一次 `ReceiveChain`，断言 -20 的**开门帧数严格更少**（今天会更开放门，
   这条会红，正是要修的）。
3. C++ 侧在现有静噪测试里加：`cmdSetSquelch({auto:true, threshold_db:-20})` 之后
   `get_squelch_status()` 的 `auto` 仍为 true、`threshold_db` 不等于 -20、
   `applied_by == "cpp-auto"`；同时保留"显式手动"用例断言 `setSquelchThreshold`
   后 `squelchAuto()` 为 false（旧语义只允许走手动路径）。
4. 扩展 `tests/test_hardcoded_parameter_registry.py::test_squelch_has_one_domain_and_one_constant_set`：
   断言 `SDRStatus.squelch_db` 的说明含"余量"、`Squelch.threshold_db` 的说明含"dBFS"，
   防止未来又把两个字段合并成一个。

### H.4 方案 ② 明确放弃的能力（必须写清，不能假装没有）

- 桌面滑杆**不再控制** C++ 内核静噪；C++ 用户要用它自己的滑杆/自动门。
- 同一台机器同时开桌面与 C++ 时，两端的静噪门限**可以不同**且都合法；
  这属于"两套实现各自的本地参数"，不是同一物理量的两次表达。
- 若以后产品要求"一个滑杆同时控两端"，必须回到方案 ①，不能在 ② 上叠补丁。

### H.5 与清零表的对应

- 本条目对应 `docs/parameter-unification.md` 第 1 组；方案 ② 落地后该组状态改为
  `已统一`（按"桌面一条链、C++ 一条链、界面明示"的口径），并在变更记录追加一行。
- `receive_pipeline` 基线未动：当前 17 ERROR / 3 WARN / 1 INFO，`tests/test_swallow_guard.py` 21 passed。
  （2026-11-05 操作逻辑线 A 笔收口后为 **13 ERROR / 1 WARN / 1 INFO**，棘轮只降未升。）

## 附 I：中断/强杀退出 0xC0000005 与残留线程（2026-11-05，操作逻辑线追加）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L45 | **事件循环运行中被中断（等价 Ctrl+C）退出码 0xC0000005，并伴随残留线程**；正常关窗不受影响 | 探针（`%TEMP%\kilo\mw_exit_probe*.py`，offscreen + `MBDSDR_NO_HW=1/MBDSDR_OFFLINE=1/MBDSDR_NULL_AUDIO=1`）：① `MainWindow()` 构造 → `close()` → 正常退出：退出码 **0**、`threading.enumerate()` 只剩 MainThread；② `app.exec()` 中 `_thread.interrupt_main()`：进程**卡在 `app.exec()`**，5 秒后探针看门狗打印 `MainThread` + 自己的守护线程，退出码 **-1073741819 = 0xC0000005**；`faulthandler` 栈显示 `KeyboardInterrupt` 抛在 `desktop/main_window.py::eventFilter`（Python 回调）里并越过 Qt C++ 边界 | **存量，非本轮引入**：同一步骤在 `bbc46e1`（`b74810f^`，我动 `main_window.py` 之前的提交）复现出**完全相同**的退出码与栈。属既有 PySide6 崩溃家族（Python 异常越界进入 Qt，`pyside6.abi3.dll` 访问违例）：`KeyboardInterrupt` 是 `BaseException`，Qt 事件分发不会吞它，出栈即崩。**结论句**：**关窗正常退出是干净的；强制中断/强杀不干净，原因是存量 PySide6 回调边界崩溃，与本轮接收链/PTT 改动无关。** | 若要在中断时干净退出：在 Qt 入口回调（至少 `eventFilter`、`QTimer` 槽）外层捕获 `BaseException` 并转成 `app.quit()`，或安装 `signal.SIGINT` 处理器只置停止标志（不在 C++ 回调里抛 Python 异常）；改完需用同探针复跑断言退出码 0 且无残留线程。**在此之前不得把「强杀会 0xC0000005」当成新回归** |

**残留线程归因（实测+代码普查）**：产品代码里所有 14 处 `threading.Thread(` 创建点均为 `daemon=True`（`audio_device_manager.py:321`、`core/controller.py:568/796/865/1310`、`device_manager.py:242/244/642`、`mcp_client.py:304/307`、`remote_control.py:190`、`usb_watchdog.py:172`、`reconnection.py:355`、`scheduler.py:343`、`dsp.py:1294`）；正常构造/关闭路径实测无任何残留线程。因此复现里出现的 `Thread-1` **不是产品的非守护线程**：`DemodWorker` 与 rtlsdr 回调都是 native/QThread 线程，不出现在 `threading.enumerate()`；该 `Thread-1` 只能是复现脚本/宿主 harness 自己创建且未 join 的线程。也就是说，**不存在「产品非守护线程没 join 导致退不出」这一条**；卡住的原因是 Qt 事件循环没有返回（中断没被转成 `app.quit()`），而不是 Python 线程阻塞解释器退出。


## 附 J：历表月相照度反转（已修，2026-11-05，天空线追加）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L46 | **`solar_system` 三个后端把「日月距角」当成「月处相位角」直接套 `0.5*(1+cos)`，照度整条反转**：2026-11-09 13:30 UTC 新月（距角 6.05°）历表给 `illum=0.997`，渲染成满月；满月时刻给 0.003 | 天空线：旧矩阵「蛾眉」格真实距角 121.8–143.1°（物理是凸月）却被标成 0.10–0.24；同一旧 `phase_angle_deg` 字段实际存的是距角，字段名与语义也错位 | **已修**：换算收敛到 `moon_illum_from_elongation` 一处（`illum = 0.5*(1-cos 距角)`，月处相位角 `≈180°-距角`），skyfield/jplephem/astropy 三后端共用；渲染层 `cos α = 2·illum-1` 未改。顺带修好 jplephem 段方向（`spk[center, target]` 曾写反，后端整条不可用） | `pytest tests/test_moon_illumination_real_ephemeris.py`：新月 ≤0.02、满月 ≥0.98、上下弦 0.5±0.03、单调递增、skyfield/jplephem 差 ≤0.01、新月亮区面积 ≤0.02（旧值 0.997 必红）；astropy 未安装按环境 skip |

> 口径说明（月相「细牙不可辨」）：新月 24 格实测 `illum 0.002–0.011`、**亮区面积 0.000–0.005**。意思是渲染**确实画了**亮区，但面积 <0.01（满盘约 10⁴ px² 时仅数 px²），视觉上不可辨——**不是「渲染没画」**。判词一律按「面积 <0.01 导致不可辨」表述。

## 附 K：历表行星照度三角形退化（已修，2026-11-05，天空线追加）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L47 | **`get_planet_position` 把行星日心距与地心距都填成 `pos.distance_au`**，日-行星-地球三角形退化为 `cos χ = R/(2Δ)`：内行星下合余弦被夹到 1（金星下合报告 1.000，物理应 0.006），外行星恒报 0.53–0.87 的假「半亮」（木星方照 0.549，实应 0.991） | 天空线：DE421 逐日扫描地心距角极值定位的真实时刻（2026-10-25 金星下合、2027-05-26 火星方照等）；外行星「合日」被误当新月处理（物理上仍接近满相，最小照度在方照） | **已修**：三边分别取日心距 / 地心距 / 日地距，唯一换算入口 `planet_illum_from_geometry`（`illum = 0.5*(1+cos χ)`），三后端共用；退化三角形直接报错，不静默兜底。天空视图经 `astronomy/planets.py`（Meeus，本就用真实日心距）未受此缺陷影响；该模块现也不再自写余弦定理，改为调用同一入口（交叉校验 9 时刻最大照度差 2.43×10⁻⁵；另加 4 时刻 × 4 行星的模型精度带约束：`|Δcos χ| ≤ 5×10⁻⁴`、`|Δillum| ≤ 3×10⁻⁴`，实测最坏 2.900×10⁻⁴ / 1.450×10⁻⁴，均出现在 mercury @ 2026-10-25。**1×10⁻⁶ 不可达**——公式已单源，剩余差只来自 Meeus ch.31 与 DE421 的星历精度；谁把任一侧改出该带宽，测试即红） | `pytest tests/test_planet_illumination_real_ephemeris.py`：金星下合 ≤0.02、上合 ≥0.99 且严格 <1，水星上下合两极端，火星冲日 ≥0.99 / 方照 <0.95，木星土星方照 ≥0.98 且与 1 的差 ≥0.002（证明不是夹值），三后端同刻差 ≤0.01（astropy 未安装按环境 skip） |

## 附 L：频谱面板几何漂移（既有，冻结期不修；2026-11-05，可发现性线追加）

| 编号 | 条目 | 来源线 / 证据 | 现状 | 复跑条件 |
| --- | --- | --- | --- | --- |
| L48 | **频谱面板比基线矮 12px：320×228 vs 320×240，宽高比 1.4035 vs 1.3333** | 可发现性线：`pytest tests/test_ui_layout_locks.py -k "1000x600 or 1600x900"` 与整文件顺序跑均复现；断言原文 `AssertionError: spectrum 几何漂移: 当前 320x228 基线 320x240（Δw=+0 Δh=-12，像素容差 ±2px；宽高比 1.4035 vs 1.3333 Δ=+0.0702，比例容差 ±0.0194）`；同尺寸的 `adsb_empty` / `freq_readout` 均通过 | **未修**：既有布局问题，2026-11-05 UI 冻结期（等 index.html / Figma 基准对照）不修，基准图不动 | `pytest tests/test_ui_layout_locks.py -k spectrum`；修好后应回到 320×240，不得靠改 `tests/baselines/` 掩盖 |

