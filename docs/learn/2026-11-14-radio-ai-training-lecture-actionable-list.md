# 《无线电与人工智能应用培训课》可落地清单

- 日期：2026-11-14
- 原文：`c:\Users\xxhzb\.trae-cn\attachments\5c895893-116e-4563-be37-e33c081d48bd_98e64baa-bd36-4cde-a2f1-86cf5a6f2a95_无线电与人工智能应用....txt`（1410 行，00:00–59:56）
- 范围：通读全文；只读检索本仓库 `mbdsdr_ai/`、`desktop/`、`docs/`、`mobile/`。
- 性质：**只出文档，不改产品代码**。本清单经批准后才进入实现批。

## 结论先行

1. 原文是 60 分钟课堂录音的自动转写稿，不是教材或技术文档：没有章节标题、公式、图表、参考文献，且多处口误/ASR 错字。
2. 它能提供的是「方向校准 + 现场经验」，**不能作为算法规格或指标基线**。
3. 对照本仓库，原文涉及的技术点绝大多数已有等价或更强实现；**没有必须新增的算法能力**。
4. 真正可落地的只有：2 条文档/文案级修正（VNA 地网前提、频段传播提示）、2 条可选能力/可视化（Doppler-时间曲线、观测→定轨入口，后者需产品确认）。
5. 另有 10 类内容明确不适用，列在 §3，防止后来人照着讲座重做。

---

## 0. 取证与判定口径

### 0.1 原文形态（重要）

原文是单说话人的课堂录音转写，按「说话人1 mm:ss」逐句切分，不是逐字稿。全文没有：

- 章节号/小节标题；
- 公式推导、图表、实验数据表；
- 参考文献或可复现的实验条件。

因此本文不能写「书里第 X 章」；所有引用只能落到**时间戳**。本文的 §0–§7 是整理者按话题切换划分的段落，**不是原文标题**。

### 0.2 引用规则

1. 原文引用格式：`§N mm:ss–mm:ss`。时间戳是唯一原文锚点，§N 只用于快速定位。
2. 项目引用格式：`文件::符号名`。**符号名为主，行号只作辅助**（代码会漂移）。
3. 原文里的口误/ASR 错字保留原词，并在 §0.4 给出纠错；影响结论的，单独列入 §4 未核实。
4. 同一能力在本仓库有多个入口时，以「唯一的底层入口」为准记录，不把 UI 重复实现当成新能力。

### 0.3 判定口径

| 判定 | 含义 |
| --- | --- |
| **已有等价物** | 本仓库已有同样能力；列符号名，防止后来人重做。 |
| **已有更强** | 本仓库不仅覆盖，还多了边界/失败/审计处理。 |
| **该加** | 确实缺，且用户能感知；本轮只记录，不实现。 |
| **该改** | 已有能力有缺口（文档/文案/入口），改动小。 |
| **待定** | 机制成立，但触发条件或产品决策未定。 |
| **不适用** | 架构前提不同、依赖不成立、原文无方法，或本仓库更强。 |
| **未核实** | 原文没给细节、转写不可信、或本轮没读到对应符号；禁止当结论用。 |

### 0.4 转写纠错表（只列影响引用的）

| 原文写法 | 应为/疑似 | 处理 |
| --- | --- | --- |
| `SPG4`（49:15） | SGP4 | 按 SGP4 引用。 |
| `MBDSBR`（50:54） | MBDSDR | 按 MBDSDR 引用。 |
| `PPRDK`（26:02） | PPP-RTK | 按 PPP-RTK 引用。 |
| `斯特拉利六梦`（30:26） | Stellarium（疑似） | 只作「星图软件」理解，不引产品名。 |
| `HTTP`（12:15–12:39） | rtl_tcp / 本地 USB 服务 | 原文表述错误，§3 单列。 |
| `一秒几百公里`（48:44） | LEO 约 7.5 km/s | 原文量级错误，不引用该数字。 |
| `50 伏 50 赫兹`（17:04–17:07） | 市电 220V/50Hz | 原文口误，不引用。 |
| `波长是 50 兆赫兹，除以 300`（21:22–21:29） | λ=300/f(MHz)，50MHz→6m | 结论对、说法颠倒；按公式引用。 |
| `进噪`（45:30、46:12） | 静噪（squelch） | ASR 同音；引用时保留「进噪」并注明即静噪。 |
| `RU`（40:32） | RRU（射频拉远单元） | 按 RRU 理解。 |
| `秋威百`/`莱拉克`/`狼个K`/`咪猫`/`BK480` | 卫星/模型/芯片名不确定 | 列入 §4 未核实，不引用。 |

---

## 1. 资料结构

### 1.1 全篇脉络（整理者分段，非原文标题）

| 段 | 时间 | 主题 | 主要内容 | 技术密度 |
| --- | --- | --- | --- | --- |
| §0 | 00:00–08:24 | 开场与个人经历 | 讲者方向（时空无线电 + AI）、经历线（AI/SLAM → 社会工作 → 影视传媒 → 无人机 → 无线电 → 北斗 → 卫星）、课程动机 | 低 |
| §1 | 08:24–11:00 | 课程主线与 SDR 基础 | 四条主线；无线电 200 年（CW→FSK）；SDR = 芯片 + 软件，把射频变成电脑可处理的数据 | 中 |
| §2 | 11:00–17:25 | SDR 硬件、频谱操作、调制、频段传播 | RTL2832U 电视棒、SMA/馈线/RH770、频谱起止/增益、Raw 录制、AM/FM/DSB/CW/LSB/USB、带宽匹配、短波/高频传播 | 中高 |
| §3 | 17:25–25:21 | VNA、驻波/地网、天线类型、多径 | NanoVNA 三件校准、相对测量、地网使谐振 900→400MHz、λ=300/f、八木/抛物面/螺旋/GP/FPV、罐头馈源、多径相消 | 高 |
| §4 | 25:21–38:39 | TLE、卫星、时空化界面、GNSS 模块、GIS | 有 TLE 即可操作卫星、TLE 枯燥需 GUI、星空 + 地球自转 + 时间轴、ISS rise/set、GNSS 串口模块、板卡成本、「不要为了北斗而北斗」 | 中高 |
| §5 | 38:39–42:38 | 时间轴、RTK/CORS、PPP-RTK vs LEOPNT、专网 | 给空间加时间线、RTK=板卡+数传电台（厘米级）、基站 RRU/BBU + 授时蘑菇头 + CORS、专网厂商 | 中 |
| §6 | 42:38–50:54 | 手台、静噪、多普勒、SGP4、长时间观测 | 单工/半双工/全双工、409MHz、啸叫、机内噪声与静噪、CTCSS/DTMF、Cantenna、多普勒修正、SGP4 定轨、单点无价值 | 高 |
| §7 | 50:54–59:56 | MBDSDR 项目、产品哲学、Tool Call/Agent、MoE | 项目命名与硬件（6 SMA/ESP32/Flutter）、AI 是辅助不是绊脚石、Tool Call/Agent、工程思维+提问、MoE 背景 | 中低 |

### 1.2 主要论点摘要

1. **工程叙事**：讲者用个人经历说明「走过的每一步都算数」「我是谁由行动决定」。这是动机与教学叙事，不是技术论证，不能当需求依据（§0 00:00–08:24）。
2. **SDR 基础**：传统电台是晶体管/电子管，SDR 用芯片 + 软件把信号归拢成电脑能理解的数据；频谱软件管起止频率、增益、录制；不同调制方式原理相近，只是解调方式不同（§1 08:24–11:00、§6 42:38–44:15）。
3. **传播与天线**：频率越低衍射越好，频率越高越容易出现多径；高频适合视距/卫星通信；VNA 是相对测量，必须先用三件校准件（原文按「绝缘/短接/50Ω 负载」描述，即开路/短路/负载）；地网会显著改变天线谐振（原文实测 900→400MHz）；抛物面把信号聚到焦点，罐头可当馈源（§2 15:27–16:21、§3 17:27–23:50）。
4. **空间与界面**：获取 TLE 即可对卫星操作；TLE 本身是枯燥数字，需要 GUI/时空化界面转译；星图 + 地球自转 + 可拖动时间轴，是「给空间加时间线」；ISS 界面显示 rise/set 并跟随卫星（§4 25:23–34:07、§5 38:39）。
5. **PNT/RTK**：RTK = 板卡 + 数传电台（440/433 ISM），可到厘米级；基站有 RRU/BBU、小蘑菇头授时、大蘑菇头 CORS；讲者做 LEOPNT，不认可 PPP-RTK 方向；「不要为了北斗而北斗」（§5 39:00–41:00、§4 37:52）。
6. **信号与定轨**：单工/半双工/全双工是链路方向概念；静噪切掉机内随机解调噪声，但反应延迟会造成起始「歘」声；卫星靠近多普勒增大、远离减小，必须修正；定轨需要 SGP4 + 预测模型；单点数据无价值，长时间观测的变化点才有价值（§6 44:15–50:54）。
7. **AI 与产品**：项目名 MBDSDR（软件定义 + 人工智能定义 + 时空定义无线电）；AI 是辅助人完成梦想的工具，不是绊脚石；人只需工程思维、认知、与真实世界交互；AI 用法从「文本框贴代码」演进到 Tool Call / Agent；DeepSeek 的 MoE 是多专家模型（§7 51:19–58:11）。

### 1.3 资料质量评估

- **技术密度分布**：§3（VNA/天线）与 §6（静噪/多普勒/定轨）最高；§2/§4 次之；§0/§7 以叙事和产品理念为主。
- **可引用性**：只能引「时间戳 + 原话大意」，不能引具体公式、指标、实验条件；原文没有给出可复现参数。
- **最值得读的三段**：
  1. §6 45:47–50:54：机内噪声/静噪、多普勒、SGP4、长时间观测，和本仓库的 `squelch`、`doppler_link`、`orbit_determination` 直接对应。
  2. §3 17:27–23:50：VNA 校准与天线/馈源，和本仓库的 `vna_cal`、`vna_pipeline`、`acquisition` 直接对应。
  3. §4 30:19–34:07：时空化界面与 ISS rise/set，和本仓库的 `TimeEngine`、`TimelineEngine`、`sat_passes` 直接对应。
- **不建议直接引用的**：所有具体器件型号、卫星名、模型名、价格、距离/速度数字；转写不稳定，且与本仓库的技术判断无关。

---

## 2. 能用在我们项目上的

> 编号 A/B/C 只用于本文检索，不代表优先级。每条固定给出：原文出处 → 原文说的什么 → 项目位置（文件 + 符号）→ 该加/该改 → 理由。

### 2.1 时空：时间基准、频率-时间、定位与轨迹

#### A1 TLE → SGP4 → 过境预测

- **原文**：§4 25:23「搭卫星比较简单，只需要获取 TLE……获取 TLE 数据后就可以对卫星进行操作」；§6 49:15「卫星定轨，我们需要 SGP4，我们需要预测模型」。
- **原文说的什么**：TLE 是卫星操作的入口；定轨/预测依赖 SGP4 一类的传播模型。没有给出 TLE 校验、误差分级或过境门限。
- **项目位置**：
  - `mbdsdr_ai/orbit.py::compute_satellite_state`、`_state_from_satrec`、`tle_source_status`、`refresh_tle_round`、`fetch_tle`
  - `mbdsdr_ai/sat_passes.py::predict_passes`、`SatellitePass`、`PASS_BISECT_TOL_S`、`VISIBLE_MIN_ALT_DEG`、`PASS_MIN_ALT_DEG`
  - `mbdsdr_ai/tle_integrity.py::age_band`、`freshness`、`accuracy`、`verify_identity`、`tracking_policy`、`band_note`
  - `desktop/sat_track_panel.py`（实时方位/仰角/多普勒）
- **该加/该改**：无。
- **理由**：原文只到「有 TLE 就能操作」的入门级简化；本仓库额外做了 TLE 年龄分级（fresh/usable/expired/stale/dead）、名称/对象身份校验、精度分级、跟踪策略、可见门限 5° 与排班门限 10° 分离、AOS/LOS 二分 0.5s 收敛（`PASS_BISECT_TOL_S`）。能力已覆盖且更严格。

#### A2 多普勒修正

- **原文**：§6 48:28–49:43「卫星飞得更快……需要进行多普勒修正，时刻了解它」「卫星靠近接收点，它的这个增大，远离接收点，它的这个偏移就减少」。
- **原文说的什么**：多普勒是频率随相对运动变化；必须按卫星位置实时修正接收频率。没有给修正算法、失败处理或收发差异。
- **项目位置**：
  - `mbdsdr_ai/satellite/tracker.py::SatelliteTracker.doppler_at`
  - `mbdsdr_ai/doppler_link.py::DopplerLinkCompensator`
  - `mbdsdr_ai/receive_chain.py::bind_doppler_source`
  - `mbdsdr_ai/sdr_tools.py::resolve_tx_doppler`
  - `desktop/sat_track_panel.py`（实时多普勒、校正后接收频率）
- **该加/该改**：无。
- **理由**：原文只说「要修正」；本仓库已区分 RX/TX 多普勒、收发互斥、失败回退与诚实标记（`DopplerLinkCompensator`），并且有统一的 `bind_doppler_source` 入口。属于「已有更强」，不要另起一套修正逻辑。

#### A3 时空化界面：空间 + 可拖动时间轴

- **原文**：§4 30:19「我做的方向叫时空化界面」、30:22「把那个时间往后拉」、31:25–31:31「这个线是不是在默默地在动……它对地球自转」；§5 38:39「给空间加上时间线，即加上一个可以拖动的时间轴」。
- **原文说的什么**：界面不只是 GUI，而是「空间 + 时间」；星图/卫星位置随时间（含地球自转）变化，时间轴可拖动。
- **项目位置**：
  - `mbdsdr_ai/new_spacetime.py::TimeEngine`、`get_time_info`、`TimeInfo`
  - `desktop/time_machine.py::TimeMachineDialog`
  - `mbdsdr_ai/new_spacetime_timeline.py::TimelineEngine`、`TimeSyncStatus`、`SatellitePass`
  - `desktop/new_spacetime_panel.py`（过境时间线 + 授时 + 频率-轨道图）
  - `desktop/rf_sky_view.py::local_sidereal_time_deg`（同一时刻基准算恒星时）
- **该加/该改**：无。
- **理由**：本仓库已有全局 `TimeEngine`、可跳转的 `TimeMachineDialog`、随 GMST 旋转的星图、过境时间线与授时状态。`rf_sky_view` 还专门把 UTC/本地/恒星时/JD 收敛到同一时刻基准，避免「同一张卡四个时间各算各的」；这比原文的口头描述更严格。

#### A4 过境 rise/set/max 展示与跟随

- **原文**：§4 34:02–34:07「有过境时间，有 rise 升起时间，set 这个下落时间……它是追随着它走的」。
- **原文说的什么**：卫星界面要显示 AOS/LOS，并随卫星移动。
- **项目位置**：
  - `mbdsdr_ai/sat_passes.py::SatellitePass`（`rise_time`、`set_time`、`max_alt_time`、`max_alt`、`rise_az`、`set_az`、`duration`、`sunlit`）
  - `desktop/sat_track_panel.py::PolarPassPlot`（方位-仰角极图）
- **该加/该改**：无。
- **理由**：原文只展示 rise/set；本仓库还有中天时刻/最大仰角、方位、时长、日照判断和极图。已有等价物且信息更全。

#### A5 「单点无价值，长时间观测才形成轨道信息」

- **原文**：§6 50:42–50:54「单一的点的数据是没有任何价值的……通过这些变化的点，我们就可以知道它的这个轨道的位置」（血压比喻从 50:20 左右开始）。
- **原文说的什么**：单次多普勒测量不能定轨；必须用一段时间的观测序列，靠变化趋势反推轨道。
- **项目位置**：
  - `mbdsdr_ai/orbit_determination.py::extract_doppler_observations`
  - `mbdsdr_ai/orbit_determination.py::leosat_state_from_tle`（TLE → ECEF 位置/速度）
  - `mbdsdr_ai/orbit_determination.py::doppler_orbit_determine`
  - `mbdsdr_ai/orbit_determination.py::ExtendedKalmanFilter`
  - CLI 工具面：`mbdsdr_ai/core/cli.py` 的 `register_tool_registry` 逃生门可枚举到该模块工具
- **该加/该改**：**已完成链路（P3，暂不给 UI）**——`tests/test_orbit_determination_chain.py` 把 `leosat_state_from_tle` → `extract_doppler_observations` → `doppler_orbit_determine` 串成可复跑链（20 s 合成 IQ，99 观测，估计偏离 TLE 真值 2.2 km）；两次跑逐位一致，空/单点观测抛错。桌面端仍无入口，入口形状待链路稳定后另议。
- **理由**：原文的方法论与 `doppler_orbit_determine(observations, init_state, ...)` 的输入契约完全一致——它要的就是「时间戳 + 多普勒频移」序列；代码甚至实现了「观测为空/仅 1 点直接拒绝，绝不运行 predict/update，避免输出假轨道」。能力已存在，但普通用户不可达。
- **落地边界**：不是只加一个按钮。`doppler_orbit_determine` 还要求 7 维 ECEF `init_state`（可用 `orbit_determination.leosat_state_from_tle` 得到 ECEF 位置/速度，再补 1 维钟差）和站点坐标；观测可由 `extract_doppler_observations(iq, fs, ...)` 从 IQ 提取。入口要把这三段串起来，并强制显示残差/收敛状态，不能只给一个坐标。
- **边界**：本轮不实现；如果产品不需要面向用户展示定轨，维持现状即可。

#### A6 频率-时间关系（Doppler-vs-time）可视化

- **原文**：§6 48:28–49:43（火车远去频率变低、卫星靠近增大远离减小）；§6 50:20–50:54（长时间观测的变化点才有价值）。
- **原文说的什么**：多普勒本质是「频率随时间变化」；只看一个瞬时值没有意义，要看一条随时间变化的曲线。
- **项目位置**：
  - `mbdsdr_ai/sat_passes.py::compute_doppler_curve`、`DopplerPoint`
  - `mbdsdr_ai/satnogs_adapter.py::DopplerCorrector.build_curve`、`DopplerPoint`（已改为委托 `DopplerSampler`，不再自算）
  - `desktop/sat_track_panel.py`：目前只有瞬时多普勒（`dop_label`）、最大多普勒提示和 `PolarPassPlot` 方位-仰角极图，**没有频率-时间曲线**
- **该加/该改**：**进行中（P2）**——前置的「单一来源」已落地：`satnogs_adapter.DopplerCorrector.build_curve` 已委托 `sat_passes.DopplerSampler`，删除自带的 SGP4 曲线；`compute_doppler_curve` 也改用同一采样器。过境详情的 `f_d(t)` 图尚待接线（`desktop/main_window.py` 有活跃 claim，不能扫别人的在途改动）。
- **边界**：多普勒曲线现在只有 `sat_passes.DopplerSampler` 一份实现（`tests/test_doppler_single_source.py` 按 1e-9 校验两条路径逐点一致）；接 UI 时直接用 `compute_doppler_curve`，不要为了画图再造一份。
- **理由**：这是原文反复强调、而本仓库桌面端一个明显缺失的「时空」可视化。它同时是用户理解 A2 多普勒修正和 A5 长观测定轨的最短路径。
- **风险**：纯可视化；需避开视觉冻结期，且不得改多普勒计算口径。

#### A7 时间基准与授时链路

- **原文**：§4 35:30–36:17（GNSS 模块走串口，RX/TX/GND/VCC）；§5 40:28–40:40（基站小蘑菇头用于授时，北斗有 PNT 授时；大蘑菇头是 CORS）；§6 48:44「进行多普勒修正，时刻了解它」。
- **原文说的什么**：定位/授时是空间应用的基础；GNSS 模块通过串口输出；基站用 PNT 授时，CORS 提供差分服务。没有给时间源优先级、跳变处理或闰秒处理。
- **项目位置**：
  - `mbdsdr_ai/new_spacetime.py::TimeEngine`、`get_time_info`
  - `mbdsdr_ai/time_basis.py::ClockGuard`、`utc_to_gps_seconds`、`gps_seconds_to_utc`、`tle_epoch_utc`、`validate_pass_window`
  - `mbdsdr_ai/serial_gnss.py::NMEAParser`、`SerialGNSSReader`、`NTRIPClient`
  - `mbdsdr_ai/gnss_timestamps.py::CaptureTime`、`resolve_capture_datetime`
- **该加/该改**：无。
- **理由**：本仓库已有时间源状态（GNSS/NTP/未同步）、采集时刻落盘、UTC/闰秒/GPS 秒换算、TLE 历元校验、系统时钟回跳检测（`ClockGuard`）和过境窗口合法性校验（`validate_pass_window`）。原文的「授时」只是概念，本仓库已是可执行的时间基准层。

#### A8 星下点轨迹与 GIS

- **原文**：§4 34:02–34:07（ISS 跟随）；§5 38:16–38:48「现在北斗的其他应用，比如 GIS……高德、百度就是 GIS 的一种」「给空间加上时间线」。
- **原文说的什么**：空间对象要落到地图上，并随时间变化；GIS 是空间 + 时间线的载体。
- **项目位置**：
  - `mbdsdr_ai/ground_track.py::ground_track`、`satellite_subpoint`、`satellite_footprint`、`split_at_dateline`
  - `desktop/osm_map.py::TileCache`、`tiles_for_bounds`、`lonlat_to_world`
  - `desktop/sat_ground_map.py`
- **该加/该改**：无。
- **理由**：星下点、覆盖圆、跨日期线切分、OSM 瓦片缓存都已具备；原文只是展示 ISS 跟随时提了一句 GIS，不构成新需求。

#### A9 「帧时刻」：SDR 解调出的卫星帧时间

- **原文**：§7 54:49–54:54「这些天上卫星的帧时刻，我用 SDR 解调出来的」。
- **原文说的什么**：讲者展示的界面里，卫星帧带有「帧时刻」；但没有说这个时刻是帧内时间码、采集 UTC，还是解调后的样本序号。
- **项目位置**：
  - `mbdsdr_ai/frame_layout_tool.py::field_extract`、`crc_check`（帧字段/CRC）
  - `mbdsdr_ai/gnss_timestamps.py::CaptureTime`、`resolve_capture_datetime`（采集 UTC）
  - `mbdsdr_ai/decode_router.py`（解码路由）
- **该加/该改**：**未核实/待定**。本轮没有找到「解码帧 + UTC 时刻」的统一字段；也不能确定原文指的是哪一种「帧时刻」。
- **理由**：如果只是「录制文件的采集时刻」，`CaptureTime` 已覆盖；如果是「帧内时间码 + 样本序号 → UTC」，需要先确认 `decode_router` 是否保留 sample index 与 capture time，再决定是否补字段。**不要凭原文一句话直接改解码输出。**

### 2.2 信号处理链路

#### B1 调制方式与带宽匹配

- **原文**：§2 14:25「带宽设错了，应该用的是 VHF/UHF 的 FM，即 NFM 与 WFM 的带宽是不一样的，容易把它蹭爆」；14:34–14:37「AM 是调幅，调频 FM，还有 DSB、CW（即电报），以及 LSB 和 USB」。
- **原文说的什么**：不同调制方式需要不同带宽；带宽设错会削顶/失真。没有给每模式默认带宽或预设策略。
- **项目位置**：
  - `desktop/mode_registry.py::DemodMode`、`demodulate`、`default_bandwidth`、`bandwidth_presets`、`next_bandwidth_preset`、`audio_cutoff`
  - `desktop/receive_pipeline.py::DemodWorker`
- **该加/该改**：无。
- **理由**：本仓库已有每模式默认带宽、三档预设循环、音频截止和统一解调入口；原文只是提醒「别设错」，我们已把「正确带宽」做成模式注册表的一部分。

#### B2 IQ/Raw 录制

- **原文**：§2 14:13–14:25「这里有个录制界面……录制这个界面后，后期就是 Raw，即 Raw 原始数据」。
- **原文说的什么**：SDR 可以录制原始 IQ，供后期处理。没有给文件格式、元数据或回放契约。
- **项目位置**：
  - `desktop/receive_pipeline.py::RecordTap`
  - `mbdsdr_ai/baseband_io.py::save_iq`、`load_iq`、`write_sidecar`
  - `mbdsdr_ai/gnss_timestamps.py::CaptureTime`
- **该加/该改**：无。
- **理由**：本仓库已有 IQ 落盘/回放、SigMF sidecar、采集时刻，以及多格式读写；比原文的「Raw」描述完整得多。

#### B3 机内噪声、静噪与反应延迟

- **原文**：§6 45:30–46:38「这个东西，歘一声就中了……这个动静不源自于外部，而是源自机内电子在电路内互相撞击产生的随机解调噪音」「这个噪音是无法解决的……拿一段正常的测试信号来消除它」「就是使用进噪（原文如此，即静噪），就是听到刷啦刷啦声时直接切掉」「因为他需要反应一会儿，然后……这个『嘚儿当』一声，导致了啸叫初始的这么一个情况」。
- **原文说的什么**：静噪的噪声基线来自机内随机噪声，无法消除，只能用正常测试信号校准；静噪门控有反应延迟，会在起始/结束产生「歘」声。
- **项目位置**：
  - `mbdsdr_ai/squelch.py::BaselineSquelch`（基线校准、`calibrating`/`calibration_failed` 可见状态）
  - `mbdsdr_ai/squelch.py::AutoSquelch`、`NoiseSquelch`、`Squelch`（attack/decay/hang、`confirm_frames`）
  - `desktop/receive_pipeline.py::DemodWorker`
- **该加/该改**：无。
- **理由**：原文的「用正常测试信号校准」对应 `BaselineSquelch` 的基线标定；「反应延迟导致歘声」对应 attack/decay/hang + 2 帧确认。本仓库已把这些做成显式状态，校准失败不会静默套默认值。已有更强。

#### B4 频段传播：低频衍射、高频多径、视距

- **原文**：§2 15:27–16:21「频率越低，衍射效果越好；频率越高越完蛋，越会出现多径效应」「频率足够高的话，适合做远距离通信，比如卫星通信」；§3 25:02–25:12「打到这儿以后，互相抵消，这叫多径效应」「这就是降噪耳机的原理」。
- **原文说的什么**：低频绕射好、高频易多径；高频适合视距/卫星。原文只有定性规则，没有菲涅尔区、链路预算或频率-传播模型。
- **项目位置**：
  - `mbdsdr_ai/acquisition.py::_fe_link_budget`（注册名 `fe_link_budget`：天线增益 → 指向损失 → 馈线 → LNA → 噪声系数 → SNR）
  - `mbdsdr_ai/acquisition.py::_fe_diagnose`（注册名 `fe_diagnose`：症状 → 诊断建议）
  - `mbdsdr_ai/anr_iq.py::ANR`（自适应噪声抑制，不是多径建模）
- **该加/该改**：**已改（P1）**——`fe_diagnose` 不再写死的「频段-传播」文案，改为实时快照驱动：`mbdsdr_ai/space_weather.py` 并发采集 NOAA SWPC 的 Kp / F10.7 / 太阳黑子数 / GOES X 射线（D 层吸收驱动量）与 Open-Meteo 本地天气，每个值带观测时刻、采集时刻、来源 URL；整次采集落盘为可回放快照（`~/.mbdsdr/space_weather/`）。`space_weather.assess` 是只读快照的纯函数，必需项缺失即「当前传播条件未知（原因）」，**不设默认平静兜底**。`fe_diagnose` 新增 `propagation`（回放快照）与 `live` 参数。
- **理由**：写死的静日文案等于拿一句话冒充当天的传播条件；观测值 + 时刻 + 来源才配叫「传播提示」。判据见 `tests/test_space_weather_propagation.py`：高/低 Kp 建议不同、源失败显示未知、同一快照回放两次一致；反证：把缺值分支改成「默认平静」后 5 条测试转红。
- **边界**：原文的「多径 = 降噪耳机」是类比不严格（ANC 是反相相消，多径是信道传播）；**不要据此新建「多径 ANC」模块**，见 §3。

#### B5 VNA 校准与「地网改变谐振」

- **原文**：§3 17:27–18:48（NanoVNA 三件校准：原文按「绝缘/短接/50Ω 负载」描述，即开路/短路/负载；相对测量，打信号收回波）；§3 20:22–20:28「我给它加了一下地网以后，它的频率下来了，来到了 400 兆」；§3 22:13「频率越高越不需要接地，频率越低它越需要接地」。
- **原文说的什么**：VNA 测的是相对量，必须先校准；同一根天线加地网后谐振会显著移动（原文现场那根天线从 900MHz 到 400MHz）；低频更需要接地。
- **项目位置**：
  - `mbdsdr_ai/vna_cal.py::solve_error_box`、`apply_calibration`、`OpenStandard`/`ShortStandard`/`LoadStandard`
  - `mbdsdr_ai/vna_pipeline.py::analyze_one_port`（VSWR/谐振点/TDR）、`vna_pipeline.py::analyze_two_port`
  - `mbdsdr_ai/nanovna_client.py::vswr`；`mbdsdr_ai/tdr_modes.py::tdr_impulse_response`、`tdr_step_response`
  - `mbdsdr_ai/vna_match.py::lc_match`、`reactance_to_component`（LC 匹配）
  - `docs/learn/nanovna/vna-workflow.md`
- **该加/该改**：**已改（P1，文档）**——`docs/learn/nanovna/vna-workflow.md` 已新增「测量前提：接地/地网条件必须一致」一节：同一被测件在不同接地/地网条件下谐振会显著移动；校准件与 DUT 必须同一接地条件；比较测量固定「是否接大地、地网/配重、馈线长度、天线姿态」四项；报告要写明前提。原文 900→400 MHz 作为示例写入，并标注不可外推。
- **理由**：工具能力已远强于原文（SOL 误差盒、TDR、匹配），但文档没有写「接地条件会改变谐振」这个操作前提。原文提供了一次真实观察，正好补上这个坑。
- **边界**：900→400MHz 是特定天线/特定条件下的观察，**不能当普适数值**；文档中要标注「示例，不可外推」。

#### B6 抛物面、Cantenna 与馈源几何

- **原文**：§3 22:17–23:50「1 米 8、40 公斤的锅」「中间锅的那个点叫反射面，聚集到上面像罐头似的东西，它充当高频头的天线」；§6 47:18–47:45（自制 Cantenna：RF 同轴铜线插入罐子、剪刀剪、台钻打孔）。
- **原文说的什么**：抛物面把信号聚到焦点，罐头可当馈源；自制 Cantenna 需要控制罐长、探针位置等几何。
- **项目位置**：
  - `mbdsdr_ai/acquisition.py::_cantenna_design`（注册名 `cantenna_design`：TE11 截止频率、波导波长、罐长、探针位置）
  - `mbdsdr_ai/acquisition.py::_dish_gain`（注册名 `dish_gain`：增益、波束宽度、F/D）
  - `mbdsdr_ai/acquisition.py::_pointing_tolerance`（注册名 `pointing_tolerance`：指向偏差 → 增益损失）
  - `mbdsdr_ai/acquisition.py::_geo_pointing`（注册名 `geo_pointing`：同步轨道卫星方位/仰角/斜距/时延）
- **该加/该改**：无。
- **理由**：原文的「40 公斤、5 楼扛下来」是操作叙事；本仓库已把 Cantenna 几何、抛物面增益、指向容差、链路预算做成可计算工具。已有更强，不需要照搬。

### 2.3 AI 侧工具调用与失败处理

#### C1 Tool Call：JSON 请求工具

- **原文**：§7 55:22–55:46「自从 24 年以后出现了一个非常神奇的东西，叫 Tool Call……你需要什么，需要这个写入工具，那么它通过 JSON 请求一个写入」。
- **原文说的什么**：模型不直接操作世界，而是通过 JSON 请求工具；工具执行结果再回到模型。没有讲 schema、权限、并发、失败分类。
- **项目位置**：
  - `mbdsdr_ai/tool_registry.py::ToolRegistry`、`ToolRegistry.register`、`ToolCallLog`
  - `mbdsdr_ai/tool_results.py::ToolResult`、`tool_success`
  - `mbdsdr_ai/agent.py`（工具调用循环）
  - `desktop/ai_panel.py`
- **该加/该改**：无。
- **理由**：本仓库已有工具注册、JSON schema、`parallel_safe` 标记、调用日志、预算/权限/沙箱；原文只到「JSON 请求工具」的概念层。已有更强。

#### C2 Agent：多步工具调用

- **原文**：§7 55:46「这个就是 Agent」。
- **原文说的什么**：Agent 是会调用工具的模型；没有讲规划、重试、终止条件或人工确认。
- **项目位置**：
  - `mbdsdr_ai/agent.py`
  - `desktop/local_agent_bridge.py::LocalAgentBridge`
  - `mbdsdr_ai/subagents.py`
  - `desktop/ai_panel.py`
- **该加/该改**：无。
- **理由**：本仓库已有主 Agent、子 Agent、本地 Agent 桥接、工具预算与策略。原文只有一句命名，不构成新方法。

#### C3 失败处理：原文没有，本仓库已有更强

- **原文**：§7 55:22–55:56 只讲 Tool Call/Agent，**没有讲失败处理**；没有失败分类、重试、用户可执行动作、终止条件。
- **原文说的什么**：（无。这一条是为了明确「原文没有可对齐的内容」，避免后来人误以为漏读。）
- **项目位置**：
  - `mbdsdr_ai/error_catalog.py::ErrorSpec`、`retryable`、`suggestion`、`user_message`
  - `mbdsdr_ai/agent.py` 的 `_execute_tool`（retryable 重试 + 取消安全点）、nextStep 注入、重复调用守卫
  - `mbdsdr_ai/tool_budget.py`、`mbdsdr_ai/tool_policy.py`、`mbdsdr_ai/tx_guard.py`、`mbdsdr_ai/hooks.py`
- **该加/该改**：无。
- **理由**：本仓库的失败处理已经覆盖「错误分类 → 是否可重试 → 给用户下一步动作 → 预算/权限/发射守卫」。原文没有提供任何可借鉴的失败处理细节；**不要为了『对齐原文』而改动现有实现**。

---

## 3. 不适用的（防止后来人重做）

| # | 原文 | 内容 | 为什么不适合我们 | 我们已有/正确做法 |
| --- | --- | --- | --- | --- |
| N1 | §5 39:00–40:47 | PPP-RTK vs LEOPNT 的路线判断；讲者做 LEOPNT，不认可 PPP-RTK | 原文只有方向性评价，没有算法、数据、指标体系；不能作为实现依据。且讲者明确不认可该方向。 | 本仓库已有 `mbdsdr_ai/rtk_solver.py`（`RTKObs`/`RTKFloatSolver` 浮点解）、`rtcm3_decoder.py`、`ntrip_panel.py`、`serial_gnss.NTRIPClient`；整数固定（LAMBDA）未实现。若要补齐，需另找资料，不能靠这份讲座。 |
| N2 | §4 33:25–35:28 | 时空道宇 400MHz 短报文、千帆/星链商业判断 | 星座运营与商业航天判断，不是软件方法；本仓库不做星上载荷/星座运营。 | 与本仓库产品边界无关；不要把「400MHz 短报文」写进接收链路需求。 |
| N3 | §7 51:22 | 自研 PCB：6 个 SMA、USB-A、ESP32、Flutter | 这是讲者的硬件形态；本仓库桌面端是 PySide6 + C++，移动端才是 Flutter。把 6 SMA/ESP32 套到桌面软件架构上是错误前提。 | `mobile/` 已有 Flutter 端；硬件 BOM/PCB 在仓库根部文档中独立维护。不要为「对齐讲座」合并两套栈。 |
| N4 | §2 12:15–12:39 | 「SDR 的通信是通过 HTTP……建立一个虚拟的网络和服务器」 | 表述错误：RTL-SDR 是 USB 设备，常走 rtl_tcp 二进制 TCP 或本地服务，不是 HTTP。照搬会做出错误的网络层。 | `mbdsdr_ai/rtltcp_client.py::RtlTcpClient` 是正确实现；另有 `spyserver_client.py`、`web_bridge.py`。不要按 HTTP 改。 |
| N5 | §3 25:12 | 「多径互相抵消，这就是降噪耳机的原理」 | 类比不严格：ANC 是反相相消，多径是信道传播；两者不能互相替代。照搬会造出错误的「多径 ANC」模块。 | `mbdsdr_ai/anr_iq.py::ANR` 是自适应噪声抑制，不是多径建模；多径/链路预算在 `mbdsdr_ai/acquisition.py::_fe_link_budget`（注册名 `fe_link_budget`）的 SNR/噪声框架里处理。 |
| N6 | §7 53:49–54:03 | DeepSeek 的 MoE（多专家模型） | 模型内部架构，由模型提供方决定；本仓库不做本地专家路由/训练。 | `mbdsdr_ai/model_manager.py` 负责模型列表/选择；不要为「MoE」新增路由层。 |
| N7 | §6 44:15–46:58 | 单工/半双工/全双工、409MHz 公众频段、手台、CTCSS 哑音码 | 面向手持电台/中继接入的操作经验；本仓库以接收/观测与卫星应用为主，不做中继台接入。 | DTMF 已有 `mbdsdr_ai/multimon_decoders.py::DTMFDecoder`（工具 `dtmf_decode`）；CTCSS 未实现，但不应因原文提到就加。发射路径受 `mbdsdr_ai/tx_guard.py` 约束。 |
| N8 | §7 52:07–52:48 | 「考试是从漫长人生取一个点，不合理；AI 能看全过程」 | 教育评价隐喻，不是软件需求；无法机检，写成规范会变成空泛条目。 | 可作为产品叙事/课程材料，不进入工程规范。 |
| N9 | §7 55:04–55:17 | 23 年的 AI 编程玩法：把数据贴进文本框，再把代码复制回来 | 已被 Tool Call/Agent 取代；本仓库已有完整 Agent 工具链。 | 见 C1/C2；不要回退到「贴代码」工作流。 |
| N10 | §7 59:27–59:56 | 「不需要游戏本，CPU + 大内存就行」 | 个人采购建议，不是产品需求或性能契约。 | 性能/资源约束应来自实测与目标机器基线，不来自讲座。 |

---

## 4. 未核实

| # | 原文 | 疑点 | 处理 |
| --- | --- | --- | --- |
| U1 | §3 24:11 | 「液态可控实时调焦天线」专利 | 原文只提名字，没有结构、指标、实现；无法判断与本仓库 `acquisition`/`vna` 的关系。不作结论。 |
| U2 | §1 10:13 | 「基带录制那些都没有用」 | 与 14:13 自己演示 Raw 录制矛盾；属于口语否定，不可采信。本仓库 `baseband_io.save_iq` 有完整实现。 |
| U3 | §7 54:49 | 「帧时刻」到底是帧内时间码、采集 UTC，还是样本序号 | 原文无方法细节；本轮也未在 `decode_router` 找到统一字段。需先确认输出契约，再决定是否加。 |
| U4 | §7 57:03 | 「Tree、Coder、Code X」等 Agent 名称 | ASR 不稳定（疑似 Trae/Cursor/Codex），不作为事实引用。 |
| U5 | §2 15:58–16:17 | 「秋威百」「莱拉克」「紫丁香二号」等卫星名 | 转写不确定；不影响技术结论，不作为卫星清单使用。 |
| U6 | §6 43:48 | 「BK480」芯片可刷成 SDR | 型号与能力未核实；不作为硬件选型依据。 |
| U7 | §2 17:10–17:15 | 「屈服效应」/「5 伏打一下手起泡」 | 术语与因果都不清楚；不引用。 |
| U8 | §6 48:44 | 「卫星一秒钟几百公里」 | 量级错误（LEO 约 7.5 km/s）；不引用该数字。 |

---

## 5. 建议落地顺序

| 优先级 | 条目 | 动作 | 前置条件 | 是否改代码 |
| --- | --- | --- | --- | --- |
| P0 | — | 无必须新增项 | — | 否 |
| P1 | B5 | 在 `docs/learn/nanovna/vna-workflow.md` 增加「接地/地网条件必须一致」的测量前提 | 文档 owner 确认；不改工具代码 | 否（仅文档） |
| P1 | B4 | 在 `fe_diagnose` 的症状-建议表加「频段-传播」提示 | 产品确认文案；不改算法 | 是（文案级） |
| P2 | A6 | 过境详情增加 Doppler-vs-time 曲线 | 避开视觉冻结期；复用 `sat_passes.compute_doppler_curve` | 是（可视化） |
| P3 | A5 | 桌面端增加「观测记录 → 定轨」入口 | 产品确认是否需要面向用户展示定轨；需显示残差/收敛 | 是（入口/UI） |
| 不做 | A1–A4、A7–A8、B1–B3、B6、C1–C3 | 已有等价物/更强 | — | 否 |
| 不做 | §3 表中 N1–N10 | 明确不适用 | — | 否 |

补充说明：

- 本轮**没有修改任何产品代码**；P1 的 `acquisition.py` 文案与 B5 文档也需另开任务并按仓库规则 claim 文件。
- A5/A6 如果落地，必须遵守现有「唯一底层入口」原则：复用 `sat_passes.compute_doppler_curve` / `orbit_determination.doppler_orbit_determine`，不在 UI 里重写多普勒或定轨算法。
- A6 的曲线不得改变多普勒正负号/频率口径；A5 的入口不得把单点观测喂给定轨（`doppler_orbit_determine` 已有 <2 点拒绝，要保留）。

---

## 6. 引用索引（时间戳 → 主题）

| 时间戳 | 主题 | 本文条目 |
| --- | --- | --- |
| 10:31–10:37 | SDR 定义：射频 → 电脑可处理的数据 | §1.2-2 |
| 12:15–12:39 | 「HTTP」表述 | N4 / §0.4 |
| 14:13–14:25 | Raw 录制、带宽设错 | B1/B2 |
| 14:34–14:37 | AM/FM/DSB/CW/LSB/USB | B1 |
| 15:27–16:21 | 低频衍射、高频多径、卫星频段 | B4 |
| 17:27–18:48 | VNA 三件校准、相对测量 | B5 |
| 19:12–20:28 | 现场天线谐振、地网 900→400MHz | B5 |
| 21:19–21:29 | λ=300/f(MHz) | §0.4 |
| 22:17–23:50 | 抛物面、焦点、罐头馈源 | B6 |
| 25:02–25:12 | 多径相消、「降噪耳机」 | B4/N5 |
| 25:23 | 有 TLE 即可操作卫星 | A1 |
| 30:19–31:31 | 时空化界面、时间轴、地球自转 | A3 |
| 33:25–33:44 | 时空道宇 400MHz 短报文 | N2 |
| 34:02–34:07 | ISS rise/set、跟随 | A4/A8 |
| 35:30–36:17 | GNSS 串口模块 | A7 |
| 37:52 | 「不要为了北斗而北斗」 | §1.2-5 |
| 38:39 | 给空间加时间线 | A3 |
| 39:00–40:47 | PPP-RTK vs LEOPNT、RTK/CORS | N1/A7 |
| 40:28–40:40 | 授时蘑菇头、CORS | A7 |
| 44:15–46:58 | 单工/半双工、静噪、CTCSS/DTMF | B3/N7 |
| 45:30–46:38 | 机内噪声、静噪反应延迟 | B3 |
| 47:18–47:45 | 自制 Cantenna | B6 |
| 48:28–49:43 | 多普勒修正 | A2/A6 |
| 49:15 | SGP4/预测模型 | A1 |
| 50:20–50:54 | 单点无价值、长时间观测（血压比喻） | A5/A6 |
| 50:54–51:22 | MBDSDR 命名与三种定义 | §1.2-7 |
| 51:22 | 6 SMA/ESP32/Flutter | N3 |
| 52:07–52:48 | 考试隐喻 | N8 |
| 53:49–54:03 | MoE | N6 |
| 54:49–54:54 | 帧时刻 | A9 |
| 55:22–55:56 | Tool Call/Agent | C1/C2/C3 |
| 57:46–58:11 | 工程思维 + 提问 | N8 |
| 59:27–59:56 | 硬件采购建议 | N10 |
