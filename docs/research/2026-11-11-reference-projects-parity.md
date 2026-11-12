# 参考项目对标清单（2026-11-11）

扫描口径：本机 `D:\`、`C:\Users\xxhzb` 下所有含 `.git` 的目录（深度 ≤3）＋已知参考根目录（`D:\mbdsdr\reference`、`D:\OpATA\参考`、`D:\agent\参考`、`D:\beifen`、`D:\glassback`、`D:\kejio`、`C:\Users\xxhzb\src`、`C:\Users\xxhzb\AndroidStudioProjects`）。
体量为**排除 `.git` / `node_modules` / `build` / `dist` / `third_party` 后的源码文件数与主要语言行数**（行数为可解码文本行计数，量级参考）。

## 0. 项目清单

### 0.1 MBDSDR 直属参考（`D:\mbdsdr\reference`）

| 项目 | 来源 | 语言/框架 | 文件数 | 代码行量级 | README/文档 | 克隆目的（有记录才写） |
|---|---|---|---|---|---|---|
| satdump | 解包快照 | C++ (Qt) | 4258 | ~88 万 | README + 108 篇 md | 无记载；卫星解码/产品链路对标 |
| onair | 解包快照 | C++ (Qt) | 5130 | ~83 万 | README + 268 篇 md | 无记载；Qt 无线电台工作台 UX |
| stellarium | git `Stellarium/stellarium` | C++ (Qt) | 6619 | ~71 万 | README + 83 篇 md | 无记载；天空渲染/星表 |
| stellarium-web-engine | git `Stellarium/stellarium-web-engine` | C / WASM | 818 | ~21 万 | README + 17 篇 md | 无记载；Web 天空引擎 |
| sdrpp | 解包快照 | C++ (ImGui) | 695 | ~18 万 | readme + 6 篇 md | 无记载；SDR 接收机交互 |
| gqrx | 解包快照 | C++ (Qt) | 213 | ~3.7 万 | README + 2 篇 md | 无记载；SDR 前端 |
| gpredict | 解包快照 | C (GTK) | 345 | ~6 万 | 无 README | 无记载；卫星轨道跟踪 |
| rtl_433 | 解包快照 | C | 602 | ~12 万 | README + 15 篇 md | 无记载；ISM 解码器库组织方式 |
| goestools | 解包快照 | C++ | 236 | ~1.6 万 | README + 13 篇 md | 无记载；气象卫星接收 |
| noaa-apt | 解包快照 | Rust | 189 | ~7.4 千 | README + 7 篇 md | 无记载；APT 解码 |
| xrit-rx | 解包快照 | Python | 32 | ~4.3 千 | README + 3 篇 md | 无记载；LRIT/HRIT 接收 |
| meteor_decode | git `dbdexter-dev/meteor_decode` | C/C++ | 50 | ~3.5 千 | README | 无记载；Meteor 解调 |
| meteor_demod | 解包快照 | C | 25 | ~1.7 千 | README | 无记载；Meteor 解调 |
| dvbs2_bch_external | 解包快照 | C/C++ | 108 | ~1.7 万 | bindings README | 无记载；DVB-S2 BCH 码表 |
| dvbs2_ldpc_external | 解包快照 | C/C++ | 15478 | ~299 万 | bindings README | 无记载；DVB-S2 LDPC 码表 |
| wspr_audio | 解包快照 | C/C++ | 15392 | ~296 万 | 内含 zig 工具链 README | 无记载；WSPR 音频解码（含完整工具链，体积虚高） |
| sigmf | 解包快照 | 规范 + Python | 24 | ~226 | README + 10 篇 md | 无记载；IQ 记录元数据标准 |
| openngc | 空壳 | - | 2 | 0 | 无 | 无记载；NGC/IC 星表（未取到内容） |
| wsjtx | 空壳 | Python(1) | 8 | ~121 | 无 | 无记载；WSJT-X 相关（仅残留脚本） |
| glasswork | git `kubai087/glasswork` | JS | 46 | ~3 千 | README + 9 篇 md | 无记载；智能眼镜侧项目 |
| deepseek-harness | git `deepseek-ai/deepseek-harness` | TS | 20583 | ~154 万 | README + 5063 篇 md | 无记载；Agent 编排/工具协议 |

### 0.2 其他根目录下的参考克隆

| 项目 | 路径 | 来源 | 语言 | 文件数 | 行量级 | README/文档 | 克隆目的 |
|---|---|---|---|---|---|---|---|
| Look4Sat | `D:\OpATA\参考\Look4Sat` | git `rt-bishop/Look4Sat` | Kotlin | 5666 | ~1.6 万 | README + 4 | 无记载；**卫星过境跟踪/预测（与 MBDSDR 卫星面板同域）** |
| Notally | `D:\OpATA\参考\Notally` | git `OmGodse/Notally` | Kotlin | 305 | ~8 千 | README + 3 | 无记载；极简笔记应用 |
| sherpa-onnx | `D:\OpATA\参考\sherpa-onnx` | git `k2-fsa/sherpa-onnx` | C++ | 5383 | ~33 万 | README + 165 | 无记载；端侧 ASR/TTS |
| searxng | `D:\OpATA\参考\searxng`、`D:\agent\参考\searxng` | git `searxng/searxng` | Python | 950 | ~5.5 万 | README.rst + 191 | 无记载；元搜索后端 |
| gelab-zero | `D:\OpATA\参考\gelab-zero` | git `stepfun-ai/gelab-zero` | Python | 81 | ~7 千 | README + 3 | 无记载；GUI Agent 基准 |
| gallery | `D:\OpATA\gallery` | git `google-ai-edge/gallery` | Kotlin | 480 | ~4.5 万 | README + 38 | 无记载；端侧模型 Demo 应用 |
| Opedrgent | `D:\OpATA\Opedrgent` | git `HaohanHe/Opedrgent` | Kotlin | 720 | ~14 万 | README + 45 | 自有项目 |
| hermes-agent | `D:\OpATA\参考\hermes-agent-2026.5.7` | 解包快照 | Python/TS | 3136 | ~80 万 | README + 931 | 无记载；Agent 框架 |
| kilocode | `D:\OpATA\参考\kilocode-main`、`D:\agent\参考\kilocode-7.2.49` | 解包快照 | TS | ~6900 | ~77 万 | README + 422 | 无记载；编码 Agent |
| opencode | `D:\agent\参考\opencode-1.14.46` | 解包快照 | TS | 4673 | ~38 万 | README + 106 | 无记载；终端编码 Agent |
| claude | `D:\OpATA\参考\claude` | 解包快照 | TS | 1903 | ~51 万 | 无 README | 无记载；Agent CLI |
| koog | `D:\OpATA\参考\koog-0.8.0` | 解包快照 | Kotlin | 2270 | ~25 万 | README + 219 | 无记载；Kotlin Agent 框架 |
| MiMo-Code | `D:\OpATA\参考\MiMo-Code-main` | 解包快照 | TS | 3942 | ~36 万 | README + 96 | 无记载 |
| ml-intern | `D:\OpATA\参考\ml-intern-main` | 解包快照 | Python | 188 | ~4.9 万 | README + 4 | 无记载 |
| open-webui | `D:\OpATA\参考\open-webui-0.9.5` | 解包快照 | Python/TS | 4967 | ~19 万 | README + 6 | 无记载 |
| qdrant | `D:\OpATA\参考\qdrant-1.17.1` | 解包快照 | Rust | 1563 | ~36 万 | README + 21 | 无记载；向量库 |
| meilisearch | `D:\OpATA\参考\meilisearch-1.43.0` | 解包快照 | Rust | 1755 | ~25 万 | README + 25 | 无记载；搜索引擎 |
| gpt_mobile | `D:\OpATA\参考\gpt_mobile-0.7.5` | 解包快照 | Kotlin | 301 | ~2 万 | README + 17 | 无记载 |
| librtlsdr | `C:\Users\xxhzb\src\librtlsdr` | git `steve-m/librtlsdr` | C | 81 | ~1.3 万 | 无 README | 无记载；RTL-SDR 驱动库 |
| libusb | `C:\Users\xxhzb\src\libusb` | 解包快照 | C | 96 | ~6 千 | README.txt | 无记载；USB 后端 |
| HamRadiotools | `C:\Users\xxhzb\AndroidStudioProjects\HamRadiotools` | git `HaohanHe/HamRadiotools` | Kotlin | 65 | ~2 千 | README | 自有项目；业余无线电工具 |
| MFPV50C | `D:\beifen\MFPV50C`、`D:\beifen\fpv-mod` | git `HaohanHe/MFPV50C` | Kotlin | 157 | ~1.6 万 | README + 11 | 自有项目；FPV |
| LocationSpoofer | `D:\glassback\LocationSpoofer` | git `meat-daemon-404/LocationSpoofer` | Kotlin | 288 | ~4.9 万 | README + 12 | 无记载；定位改写模块 |
| gym-pybullet-drones | `C:\Users\xxhzb\tools\ref-src\gym-pybullet-drones` | git | Python | - | - | README | 无记载；飞控仿真 |

> 统计说明：`dvbs2_ldpc_external`、`wspr_audio` 的超大行数主要来自内嵌的第三方工具链/码表，不能当作代码复杂度读。
> 未纳入：`D:\mbdsdr\.tmp\*`、`D:\output\wt-*` 等本仓库自身的临时 worktree；纯二进制解包目录（`D:\glassback\Glxss Live`、`眼镜手机投屏*` 等）。

### 0.3 「迪士尼斯」疑似指代

全盘按 `dis|dns|deis|denis|denys|dhis` 匹配目录名，**没有任何名字吻合的克隆项目**。音近候选仅两个，且都是 DVB-S2 码表实现，不是 demo 仓库：

- `D:\mbdsdr\reference\dvbs2_bch_external`（DVB-S2 BCH 编解码，C/C++）
- `D:\mbdsdr\reference\dvbs2_ldpc_external`（DVB-S2 LDPC 码表，C/C++）

若用户指的是某个「示例/demo 仓库」，本地没有同名克隆；需要用户给一个更准的词（例如 `DVBS2`、`SigMF` 或某个产品名）才能定位。

### 0.4 结论格式硬约束（2026-11-12）

凡写「我们没有 / 未实现 / 缺口 / 不校验 / 空档」这类**关于本项目的否定结论**，必须同时给出**当前 HEAD 的行号证据**：

- 证据格式固定为 `` `HEAD:<path>:<line>` `` ＋ 该行原文（可直接用 `git show HEAD:<path>` 复核），**行号与原文必须能对上**。
- 否定结论（「没有 X」）不能靠单行原文直接证明，必须给出**封闭集定义行**（schema / 类型定义 / dict 字面量 / 函数签名等枚举了允许字段的那一行）＋ 一句「X 不在该集合内」；给不出封闭集定义行的，一律标 **待查**。
- **给不出行号证据的条目不得留在「已核」**，必须改标「待查」。
- 本约束适用于 `docs/research/` 下所有参考项目对照文档；其它文件由各自负责人按同一口径补齐。

## 1. 学习次序（按与 MBDSDR 的相关度）

1. Look4Sat — 卫星过境预测与 UI 状态机（同域，代码量小）
2. gpredict — 轨道传播与多卫星调度（C/GTK，看数据流）
3. satdump — 解码产品链与插件注册
4. sdrpp — SDR 交互（频谱手势、预设、DSP 管线）
5. gqrx — Qt SDR 前端最小可用形态
6. stellarium-web-engine — 星表/渲染分层
7. onair — Qt 无线电台工作台信息架构
8. rtl_433 — 设备解码器的注册与表驱动
9. noaa-apt / xrit-rx / goestools / meteor_* — 单星解码器最小闭环
10. sigmf — IQ 记录元数据
11. deepseek-harness — Agent 工具协议（若要做 AI 助手面板）
12. 其余（Agent 框架、搜索/向量库）— 仅在需要对应能力时再看

每学完一个项目在本文件追加一节，格式固定：

```
### <项目>（<路径>）
- 机制：<具体机制名，一句话>
- 我们的落点：<file:line 或模块>
- 该不该搬：<搬 / 只借鉴思路 / 不搬>，一句话理由
- 搬的代价：<改动面、依赖、风险>
```

## 2. 逐项目结论

### 2.1 Look4Sat（`D:\OpATA\参考\Look4Sat`，Kotlin / Compose，GPL-3.0）

许可证先记一笔：GPL-3.0，按 `AGENTS.md` §3「只学机制、不抄代码」，本节的落点全部是**接口形状**，不搬实现。

> **裁定（2026-11-11，用户口径）**
> - A：排进数据补溯源，是数据活不是代码活（8 条逐条公开溯源；`invert` 本地无此概念）。
> - B：做，排 C 之后——C 与 B 动同一片绘制区域，同时改会撞。
> - C：立刻做，已派卫星线；约束=不许造第三个时间基准，UTC 只影响显示。
> - D、E：不搬（skyfield 自带缓存 / 形状已同构）。
>
> **现状（2026-11-12 对 HEAD 核）**：C 已落地（见机制 C）；B 未落地；A/D/E 无变化。

**机制 A：转发器成对模型（`core/domain/model/SatRadio.kt:24-36`）**
一个条目同时持有 `downlinkLow / downlinkHigh / downlinkMode / uplinkLow / uplinkHigh / uplinkMode / invert / isAlive`，UI 用 `selectedTransponderUuid` 做**单一选择态**（`feature/radar/RadarState.kt:34-42`），选中后 TX/RX 两个频率由同一对象派生。
- 我们的落点：`mbdsdr_ai/sat_links.py` + `mbdsdr_ai/data/sat_links.json`（现 8 颗星、每条只有一个 `freq_hz`；容差默认值见 `` `HEAD:mbdsdr_ai/sat_links.py:61` ``：`def identify_by_freq(freq_hz: float, tolerance_hz: float = 3000.0) -> List[Dict[str, Any]]:`）。
- 该不该搬：**搬字段形状，不搬上行**。带宽区间 `low/high` 能让「点星即听」自动给出合适的 FFT 带宽，也让 `identify_by_freq` 的容差从全局常量变成「按条目带宽」；`invert`（线性转发器倒置）**本地不存在**——封闭集证据 `` `HEAD:mbdsdr_ai/data/sat_links.json:14-15` ``：link 字面量字段只有 `direction/freq_hz/mode/baud/note`，无 `low/high/invert`；溯源只到卫星级（`:18` 的 `source`），未到每条带宽。上行仍只走 `tc_uplink` 的确认 token，不进表。
- 搬的代价：8 条数据要补 low/high（需公开文档逐条溯源，属数据工作量）；`identify_by_freq` 签名要接受每条的带宽；星空页链路卡片加一行带宽/极性标签。

**机制 B：极坐标雷达多星同盘（`feature/radar/RadarView.kt` + `RadarState.kt:57` `satTrack: List<OrbitalPos>`）**
一屏画出所有在跟踪卫星的当前方位/仰角点，并保留一串历史点作拖尾；顺带 `shouldUseCompass` / `shouldShowSweep` 两个开关把「罗盘刻度」和「扫描线」解耦。
- 我们的落点：`desktop/rf_sky_view.py:487`（方位盘标签生成）、`:8673`（`SatelliteTracker`）；`desktop/sat_track_panel.py` 只画当前一颗。
- **已核（2026-11-12，HEAD）**：天空图**已有多星同盘**——`desktop/rf_sky_view.py:8928` `for entry in self._catalog:` 逐星建 `SkyObject`，`:8959-8975` 每次 append，`:8991-8993` 每星带 10 分钟轨迹，`:8995` `set_objects(objs)` 一次画全部。原判断「换星就是换面板，看不到哪几颗同时在天上」是错的，只对专用雷达面板 `sat_track_panel` 成立（它按设计只画当前一颗）。
- 该不该搬：**不搬**（能力已在天空图落地）；专用雷达面板补多星属可选增强，不是缺口。
- 搬的代价：不适用（天空图已有多星）；专用雷达面板可选，成本为一个绘制循环。

**机制 C：过境倒计时状态（`RadarState.kt:51-56` `currentPass` / `isTimeAos` / `isUtc`）**
UI 只存「当前过境对象 + 现在处于 AOS 侧还是 LOS 侧 + 是否用 UTC 显示」，倒计时由 `aos/los - now` 派生，不另存秒数。
- 我们的落点（2026-11-12 对 HEAD 核，**已落地**）：`desktop/sat_track_panel.py:156-167`（倒计时状态 + 1 s 定时器）、`:281-318`（倒计时行 + `UTC 显示` 开关）、`:560-599`（`set_pass_provider`；主窗口 `desktop/main_window.py:1895` 接 `_provide_sat_passes`）；倒计时的 `now_unix` 由主窗口按同一时间引擎传入（`:478`、`:628`）。
- 该不该搬：**已搬**。与裁定一致：倒计时只做展示，时基仍只有 TimeEngine 一个。
- 搬的代价：已付讫（落地归卫星线）。

**机制 D：观测点三角缓存（`predict/OrbitalObject.kt:205-235`）**
按 `lat/lon/alt` 做键，只在观测点变化时重算 `sin/cos` 与地球扁率因子。
- 我们的落点：`mbdsdr_ai/sat_passes.py:481`、`:624`、`:777`（每次调用经 skyfield `Topos` 重建观察者）。
- 该不该搬：**不搬**。skyfield 内部已有自己的缓存，且我们是纯 Python 逐点调用，收益远小于引入第二份坐标实现的正确性风险。
- 搬的代价：不适用（记为「明确不搬」，避免下一个人重复调研）。

**机制 E：SSTV 模式表驱动的解码器（`core/domain/sstv/SstvModes.kt` 406 行 + `SstvDsp.kt` 203 行）**
模式定义（VIS 码、行数、像素时长、同步结构）与 DSP 解耦，新增模式只加表项。
- 我们的落点：`mbdsdr_ai/sstv_decoder.py:36` `SSTV_MODES`，已覆盖 Robot 36/72 与 PD 全系，形状与它同构。
- 该不该搬：**不搬**。已经是同一套表驱动做法，无增量。
- 搬的代价：不适用。

### 2.2 gpredict（`D:\mbdsdr\reference\gpredict`，C / GTK，GPL-3.0）

> **裁定（2026-11-11，用户口径）**
> - A：该搬，但**分批**——只先合并「天空图 5000 ms」与「跟踪面板 500 ms」两个定时器、共用同一帧 az/el/多普勒快照；星座图 100 ms / ADS-B 200 ms / 设备 2000 ms 先不动。落地归卫星线。
> - B：只借鉴「站点是显式对象、视图绑站点」，不搬多站点 UI。
> - C：不搬，写成「独立收敛佐证」——两套实现各自得出「可见门 ≠ 列表筛选门」，可作为 `VISIBLE_MIN_ALT_DEG=5.0` 与用户峰值门分开的对拍依据。
> - D：不搬（再造布局系统与削冗余直接冲突），写进文档是防下一个人重复提。
> - E：读回部分已同构不搬；「控制层自查 AOS/LOS」要核一次——判据若只在 UI 层，就是「读方参与控制决策」，得下移到 500 ms 闭环里。已派卫星线。
>
> **现状（2026-11-12 对 HEAD 核）**：A 已落地——两个定时器已合并为 `_frame_timer`（500 ms）；E 的判据经核在闭环里（见机制 E）。

**机制 A：单时钟 + 快照分发（`src/gtk-sat-module.c:706` `gtk_sat_module_timeout_cb` → `:628` `gtk_sat_module_update_sat` 遍历卫星表 → `:579-616` `update_child` 把同一个 `tstamp` 发给每个子视图）**
一个模块只挂一个 timeout；每 tick 先把所有卫星状态算一遍写进 hash table，再让列表/地图/极坐标/单星/事件表各自只读同一帧的 `tstamp` 绘图。视图自己不算时间。
- 我们的落点（2026-11-12 对 HEAD 核）：**已合并为单帧**——`desktop/main_window.py:844` `_FRAME_INTERVAL_MS = 500`、`:1213-1216` `_frame_timer`、`:13656-13668` `_on_frame_tick`；后者用 `_SKY_FRAME_EVERY = 10`（`:846`）把天空图节流到 5 s、跟踪每帧 500 ms，文档串写明「合并前是两个定时器（天空 5000 ms、跟踪 500 ms）」。仍各有独立时钟的是非卫星面板：`desktop/constellation_panel.py:577`（100 ms）、`desktop/adsb_map_panel.py:167`（200 ms）、`desktop/device_panel.py:160`（2000 ms 热插拔轮询）——它们不各自计算卫星 az/el。
- 该不该搬：**已搬**（按裁定分批，先合并的两个定时器已完成）。其余面板是否也接入同一拍，留待后续评估；它们与 az/el 无关，不属于「同一事实两个值」。
- 搬的代价：已付讫（并入卫星线改动）。

**机制 B：多地面站对象 + 模块显式绑定 QTH（`src/qth-data.c`、`src/sat-pref-qth.c` 851 行、`src/sat-pref-qth-editor.c` 722 行）**
QTH 是独立对象（经纬高 + 名称），每个卫星模块绑定其中一个；换站点只换绑定，不改计算。
- 我们的落点：`mbdsdr_ai/sat_passes.py:308` `GroundStation`（lat/lon/alt 单一数据类），由 `settings` 提供。
- 该不该搬：**只借鉴「站点是显式对象、视图绑站点」，不搬多站点 UI**。我们是单站点产品，多 QTH 属伪需求。
- 搬的代价：低。保持单实例，但把「用哪个站点」写进计算入口签名（现在已是显式传参，方向一致），不引入站点管理界面。

**机制 C：过境对象生命周期 + 「不按最小仰角过滤」的取当前过境（`src/predict-tools.c:364` `get_next_pass`、`:390` `get_next_passes`、`:413` `get_pass`、`:434` `get_pass_no_min_el`）**
`pass_t` 有显式 `copy_pass`/`free_pass`，预测入口区分「按 min_el 过滤」与「不过滤取当前过境」两套。
- 我们的落点：`mbdsdr_ai/sat_passes.py:420` `predict_passes`、`:832` `predict_upcoming_passes`；`VISIBLE_MIN_ALT_DEG = 5.0`（`:295`）与用户峰值筛选门 10° 在 `desktop/sat_track_panel.py:30-35`、`:418-420` 明确分开，且注释写了「25° 设置下卫星仍可跟踪，不能假装过境结束」。
- 该不该搬：**不搬**。它把 min_el 当参数在入口处分叉，口径反而更散；我们已经把两个门拆成两个常量并在 UI 上写明分工。这条的价值是**独立收敛的佐证**：两套实现各自得出「可见门 ≠ 列表筛选门」，可以在文档里当作对拍依据引用。
- 搬的代价：不适用。

**机制 D：模块网格布局（`src/gtk-sat-module.c:300` `create_view`、`:353` `get_grid_size`、`:375` `create_module_layout`）**
一个工作区里按 1×1 / 2×2 网格摆多个卫星视图，用户自选组合。
- 我们的落点：`desktop/` 的面板系统（`PanelRegistry` + 固定 tab/dock）。
- 该不该搬：**不搬**。再造一套布局系统与本轮「削冗余」直接冲突，现有面板系统已覆盖同一需求。
- 搬的代价：不适用（列出来是为了防止下一个人重复提这条）。

**机制 E：电台控制层的 set/get 成对读回 + 控制层自查 AOS/LOS（`src/gtk-rig-ctrl.c:80-88` `check_aos_los` / `set_freq_*` / `get_freq_*`）**
每次设频率后立刻读回比对；过境结束后由控制层自己停止下发，不依赖 UI 是否还在刷新。
- 我们的落点：`tc_uplink` 外部确认 token、`scripts/sample_rate_readback_check.py` 的读回校验；跟踪闭环在 `desktop/main_window.py:13395-13437` `_poll_satellite_track`。
- **已核（2026-11-12，HEAD）**：停止跟踪的判据**在闭环里**，不依赖面板是否还在画——`:13407` 注释「判据先全部算完，UI 刷新统一放到末尾」，`:13415-13422` 仰角 < 5° 时直接 `_stop_satellite_tracking()`，面板刷新在 `:13432-13437` 且被 try/except 隔离。与 gpredict 控制层自查同构，**不需改**；复核权仍归卫星线。
- 搬的代价：不适用。

#### 2.2.1 专项核查：时间基准 / 无设备 / 首次启动

**① 时间基准：他们做到了，而且是双钟分层**

- 机制：`src/gtk-sat-module.c:706` 每 tick 只取一次真实钟 `mod->rtNow = get_current_daynum()`（`:750`）；若 `throttle != 0`，用 `(rtNow - rtPrev)` 推进**显示钟** `mod->tmgCdnum`（`:753-757`）；随后 `:790-794` 把同一个 `tmgCdnum` 分发给全部视图，`:806-809` 再分发给 rig / rot 控制器。
- 视图层零取钟：`gtk-polar-view.c:917/982/1041` 全是 `polv->tstamp`；`gtk-sat-map.c:948` 是 `satmap->tstamp`；`gtk-sat-map-ground-track.c:104` 甚至把 `get_current_daynum()` 注释掉留作对照；过境入口 `predict-tools.c:369/396` 也接收外部传入的 now。
- 结论：**他们的三处（列表/过境表/极坐标）用的是同一个时刻源**，而且在源之上还分了「真实钟 → 显示钟」两层，`throttle` 是唯一换算点。
- 我们的状态：已经收敛到同类结构——`desktop/sat_track_panel.py:478`「传 None 时面板自己向同一时间引擎取一次——绝不用 `time.time()` 另造」、`:628`「失败返回 None——绝不回退 `time.time()`」；`desktop/rf_sky_view.py:7608` 注释写明「恒星时/JD 读 TimeEngine」。**我们没有模拟时钟层**（无时间旅行需求），但多了一层 `mbdsdr_ai/time_basis.py:215` `ClockGuard`（以 `monotonic` 为参考检测墙钟跳变），gpredict 没有这层。
- 该不该搬：**不搬分层**（无 throttle 需求），**只把「视图层禁止 `time.time()`」变成可 grep 的约束**（现在只写在注释里）。
- 代价：0 LOC，一条 CI grep + 一份豁免清单（`_camera_notice_until` 这类 UI 动画计时不算时间基准，需列白）。

**② 预设/书签：gpredict 不适用**

gpredict 没有频率预设或书签概念（它是跟踪器，不是接收机）；模块布局存在 `SAT_CFG_STR_OPEN_MODULES`（`src/mod-mgr.c:101`），是「上次打开了哪些模块」的字符串，不是可命名、可切换的预设。故本项不适用，见 §2.3 gqrx。

**③ 无设备：他们的第三档是「占用即置灰，退出占用才写回」**

- 机制：`src/gtk-rig-ctrl.c:1013-1031`，`engaged` 开关决定 `DevSel` / `DevSel2` 的 `set_sensitive`；**未占用时可改，占用期间置灰**。配置写回发生在退出占用的那一瞬 `setconfig(ctrl)`（`:1022`），另有进程退出时统一 `sat_cfg_save()`（`src/main.c:174`）。
- 对照我们的 G12（「随数据禁用」vs「始终可用」两档）：gpredict 给的是**第三档**——不是「没数据就不能改」，而是「**设备会话期间不许改，值仍保留，断开时写回**」。**已核（2026-11-12，HEAD）：我们没有第三档**——会话期间被切换的控件封闭集只有 `connect_btn` / `disconnect_btn` 两个（`HEAD:desktop/device_panel.py:499-506`：连接时 `connect_btn.setEnabled(False)` / `disconnect_btn.setEnabled(True)`，断开反之），采样率/增益不在该集合内，不存在「占用中只读、断开写回」。
- 该不该搬：**搬概念**。给「设备会话」加一个占用态：会话内配置控件只读但不丢值，断开时一次性写回并持久化。
- 代价：低。控件 `setEnabled` 一处开关 + 会话结束时的保存点；风险是「断开时写回」需要明确失败路径（写回失败要不要回滚到会话前的值），这一点 gpredict 没处理，我们得自己定。

**④ 首次启动：三条懒加载，其中一条我们已经做得更彻底**

- `src/main.c:131-156`：`sat_log_init()` → `sat_cfg_load()` → `first_time_check_run()` → `gpredict_app_create()` → `show_all()`，**窗口先出来再谈数据**。
- 延后项一：TLE 监控用 `g_timeout_add(600000, tle_mon_task, NULL)`（`:163`），首次刷新推迟 10 分钟，不在启动路径上。
- 延后项二：模块按配置懒创建（`src/mod-mgr.c:85-131` 只对保存过的 open module 调 `gtk_sat_module_new`），卫星树在模块构造时才 `gtk_sat_module_load_sats`（`gtk-sat-module.c:1056`）。
- 延后项三：**按可见性跳过整个 tick**（`gtk-sat-module.c:719-737`，docked 且 tab 不可见时 `needupdate = FALSE`，连卫星状态都不算）。
- 我们的对应：
  - 第三条（可见性跳过）**已同构且更彻底**——`desktop/rf_sky_view.py:2194/2254` 自适应渲染定时器，`:2211-2219` `showEvent`/`hideEvent` 直接停/启（注释写明「隐藏即停表、显示即恢复，不改变任何画面内容」）。
  - 第一条（TLE 定时刷新）**已核（2026-11-12，HEAD）**：我们**没有** 10 分钟周期任务；`desktop/main_window.py:13084-13103` 仅在 `tle_date` 距今天 ≥ 2 天时挂一次性 `_tle_boot_timer`（singleShot 1500 ms → `_on_update_sat_tle`），另有菜单 `:1605` 与面板「刷新星历」按钮 `:1891` 手动触发。与 gpredict 的 `g_timeout_add(600000, ...)` **不同构**，但也没有把 TLE 刷新放进启动路径。
  - 第二条（面板是否懒创建）**已核（2026-11-12，HEAD）**：我们**启动时全量构造，无懒创建**——`desktop/main_window.py:2246` `self._build_kernel_panels()` 在 `__init__` 内直接调用，`:10387` 该函数逐页 `new` 面板（`:10419` `self.bookmark_panel = BookmarkPanel()`、`:10508` 星座图、`:10520` 设备面板），卫星跟踪面板更早在 `:1888` `self.sat_track_panel = SatTrackPanel()`。与 gpredict 的懒创建相反。
- 该不该搬：第三条不搬（已有）；第一条不搬（按需刷新更适合我们，周期任务只增加网络与线程管理）；第二条已核（我们是全量构造）——要学懒创建属新优化，不在本轮。
- 代价：已确认是启动全量构造；改懒创建会牵动面板注册表与主题唯一出口，风险中等，不建议本轮动。

### 2.3 gqrx（`D:\mbdsdr\reference\gqrx`，C++/Qt + GNU Radio，GPL-3.0，213 文件 / 约 3.7 万行）

**机制 A：书签数据结构 = 频率 + 名字 + 调制 + 带宽 + 共享标签（`src/qtgui/bookmarks.h:35-96`）**

`TagInfo{name, color, active}`、`BookmarkInfo{frequency:qint64, name, modulation, bandwidth:qint64, tags:QList<TagInfo::sptr>}`；按频率排序（`operator<`）；标签用 `shared_ptr` **对象同一性**去重（`bookmarks.cpp:272` `pTag.get() == pTagToDelete.get()`），不是按字符串比。持久化是 `<cfg>/bookmarks.csv`（`bookmarks.cpp:55`），人可读可手改；`Bookmarks` 是单例，靠 `BookmarksChanged` / `TagListChanged` 两个信号广播（`bookmarks.cpp:142/203/283-295`）。`TagInfo.active` 是**筛选开关**，不属于单条书签。

**机制 B：激活链路是「三参数 + 增量改参」，不是整体重建（`dockbookmarks.cpp:86-107` → `mainwindow.cpp:313/2275-2301`）**

`QTableView.activated` → `DockBookmarks::activated`（`dockbookmarks.cpp:104`）→ `emit newBookmarkActivated(freq, modulation, bandwidth)`（`:107`）→ `MainWindow::onBookmarkActivated`（`mainwindow.cpp:2275`）→ `setNewFrequency(freq)` + `selectDemod(demod)`，然后**带宽不硬居中**，而是按当前滤波预设的锚点落位：`lo+hi==0` 居中、`lo>=0&&hi>=0` 抬上边、`lo<=0&&hi<=0` 压下边（`:2287-2301`）。
- 我们的落点：`desktop/bookmark_panel.py:63` `tune_requested = Signal(float, str, float)`、`main_window.py:10917` `_on_bookmark_tune_requested(freq_hz, mode, ...)` —— **面板路径与 gqrx 同构**（三参数 + 带宽）。
- **缺口（确凿，2026-11-12 对 HEAD 核，已按封闭集复核）**：内部书签元组是 **4 元组 `(freq_hz, name, mode, builtin)`，根本没有带宽字段**——写入 `main_window.py:8118-8119` 追加 `(float(freq_hz), str(name), str(mode), False)`，加载 `:2056-2057` 取 `(b.freq_hz, b.name, b.mode, bool(b.builtin))`，持久化走 `FrequencyManager.add(name, freq, mode=...)`（`:8123-8125`，无带宽参数）。因此 `:8159` 的 `_b` 是 `builtin`，不是带宽；旧表述「旗子路径丢带宽」不准确：**两条路径都没有带宽可丢**，缺的是数据模型字段，不是某条链路。`spectrum_widget.py:2542` 仍是单参数 `Signal(float)`。
- 该不该搬：**搬「三参数 + 锚点带宽」**。旗子路径也携带 (freq, mode, bandwidth)，复用 `_on_bookmark_tune_requested`，不要再留降级路径。
- 代价：低。信号签名加参数 + 合并槽函数，约 20–30 行；前置工作是把书签元组从 4 元组扩成 5 元组（带宽字段），并同步写入/加载/`FrequencyManager.add` 三处。

**机制 C：未选源时不进主界面（`src/applications/gqrx/main.cpp:157-167` + `mainwindow.cpp:372-396`）**

首次启动强制先弹 I/O 配置对话框；`configOk=false`（用户取消）则 `main.cpp:166` 返回 1，**主窗口根本不 show**。没有「空态」这一说——gqrx 的选择是「配置未完成就不算启动完成」。
- 对照我们 G12：「随数据禁用 / 始终可用」两档 + 空态文案。
- 该不该搬：**不搬这个门禁**。我们会话可随时换设备、面板有独立价值（标注、解码回放），关窗式门禁会把可用功能一起关掉。可借鉴的只有一点：**对话框取消必须是明确定义的结果**（退出码/状态），不能留下半初始化状态。
- 代价：不适用。

### 2.4 `time.time()` / `time.monotonic()` / `datetime.now()` 普查（`desktop/` 全部，HEAD 提交态）

只报数，不写规则。

- **总命中 102 处，覆盖 20 个文件。**
- 四类分布：**时间基准 10 / 动画·节流·去抖 46 / 日志·耗时统计 29 / 其他 17**。
- 文件分布：`main_window.py` 28、`rf_sky_view.py` 27、`osm_map.py` 11、`mcp_worker.py` 6、`receive_pipeline.py` 4、`settings.py` 4、`status_panel.py` 2、`sat_track_panel.py` 2、`satellite_image_panel.py` 2、`spectrum_widget.py` 2、`adsb_panel.py` 2、`iq_bus.py` 2、`ai_panel.py` 2，其余 8 个文件各 1。

**「时间基准」10 处分解**

| 位置 | 用途 | 判定 |
|---|---|---|
| `main_window.py:342` | 暮光计算取 `datetime.now(utc)` 定「今天」 | **真缺口**：时间旅行时暮光不跟 |
| `main_window.py:3196` | 引擎不可用 `return time.time()` | 兜底 |
| `rf_sky_view.py:3312` | `unix_to_jd(time.time())` 兜底 | 兜底 |
| `rf_sky_view.py:3322` | `_engine_unix()` 兜底 | 兜底 |
| `rf_sky_view.py:8579` | `now_unix()` 兜底 | 兜底 |
| `rf_sky_view.py:8808` | 日月计算取 now 兜底 | 兜底 |
| `rf_sky_view.py:7619` | 显示时钟 `utc_time` 兜底 | 显示兜底（低危） |
| `status_panel.py:678` | 状态栏时钟取墙钟 | **真缺口**：与引擎时刻不一致 |
| `time_machine.py:65` | 双钟层的「真实钟」 | **安全**（本来就该墙钟） |
| `new_spacetime_panel.py:350` | 时钟注入器默认实现 | **安全**（引擎本体） |

**「看起来像基准但其实安全」的其他命中**：`main_window.py:3216/3252`、`rf_sky_view.py:7245/7622` 是**偏差检测**，必须拿墙钟与引擎对照，本身不是基准；`sat_track_panel.py:478/628`、`rf_sky_view.py:7608` 是注释/文档串里的字样，不是调用；`mcp_worker.py` 6 处是 GPS/IMU 数据载荷自身的采集时间戳，属测量值；`iq_bus.py` 2 处是帧时延，用 monotonic 正确。

**结论**：「必须来自引擎却没来」的命中 **7 处**（`main_window.py:342`、`:3196`、`rf_sky_view.py:3312`、`:3322`、`:8579`、`:8808`、`status_panel.py:678`），其中 6 处是 `except` 兜底、1 处是暮光直接取墙钟。**不为 0，提案不撤回**；按本轮要求只报数、不落规则。

### 2.5 satdump（`D:\mbdsdr\reference\satdump`，C++ / Qt+ImGui，GPL-3.0）

> **裁定（2026-11-11，用户口径）**
> - C：**该搬**，已派人修 `satdump_integration.py`（先读 `dataset.json`、失败退回 glob），验收标准是「同一输入必得同一张图」。
> - E：只借鉴类型声明，给 handler 补 `inputs/outputs`、拼 steps 时校验前后层；**排 C 之后**。
> - A：不搬（层断裂跳过、推荐采样率、按星配置已同构）。
> - B：暂不搬，触发条件写死——**条目超 15 条或有第三方扩展需求时**，拆「一星一模块 + 聚合 import」，纯重构无行为变化。
> - D：不搬（ImGui 即时模式与 Qt 信号槽不同构）。
>
> **现状（2026-11-12 对 HEAD 核）**：C 已落地（见机制 C）；E 尚未落地；A/B/D 无变化。

**机制 A：管线即数据（`src-core/pipeline/pipeline.h:28-58`）**
`Pipeline.steps` 是 `{level, module, parameters, input_override}` 列表；模块用 `getInputTypes()` / `getOutputTypes()` 声明自己能吃什么、吐什么，`PipelinePreset` 给推荐采样率与频点，`LivePipelineCfg` 把实时拓扑分成 normal / server / client 三种。
- 我们的落点：`mbdsdr_ai/sat_pipeline_runner.py:28` `StageLevel`、`:181` `run_pipeline(input_level)` 做断点跳过；`mbdsdr_ai/sat_pipeline_params.py:69` `recommended_samplerate`、`:82` `SATELLITE_PIPELINES`。
- 该不该搬：**不搬，已同构**。level 断裂跳过、推荐采样率、按星配置三件事我们都有对应物。
- 搬的代价：不适用。

**机制 B：事件总线插件注册（`core/plugin.h` + `plugins/fengyun3_support/main.cpp:8-23`）**
58 个插件目录，每个 `main.cpp` 只有 18 行：声明 id、在 `RegisterModulesEvent` 上挂 handler、用 `REGISTER_MODULE_EXTERNAL` 注册模块；核心不持有任何卫星清单。
- 我们的落点：`mbdsdr_ai/sat_pipeline_params.py:82` `SATELLITE_PIPELINES` —— 单个大 dict，新增卫星要改同一个文件。
- 该不该搬：**暂不搬**。8 颗星的规模下改一个 dict 比拆成 8 个声明文件更省；事件总线在 Python 里也要额外引入一层间接。**触发条件**：卫星条目超过 15 条、或出现第三方扩展需求时再拆。
- 搬的代价：拆分为「一星一模块 + 聚合 import」，纯重构、无行为变化，届时风险低。

**机制 C：产品清单驱动产物发现（`pipeline/modules/products/module_products_processor.cpp:35-44`：`DataSet.load()` → 遍历 `products_list` → `loadProduct()` → `process_product_with_handler()`；`products/dataset.h` 是清单载体）**
下游处理器只按 `dataset.json` 里的清单逐项打开产品，从不靠文件名或扩展名猜。
- 我们的落点（2026-11-12 对 HEAD 核，**已落地**）：`mbdsdr_ai/satdump_integration.py:217` `_load_dataset_manifest`（`:224` 根清单、`:230` 递归查找）、`:336` 按 `products` 清单选图、`:401-424` 选图入口「优先读清单，旧版无清单再退回 glob」（`:420` 注释保留历史语义）。
- 该不该搬：**已搬**（按裁定验收标准「同一输入必得同一张图」落地）。
- 搬的代价：已付讫；glob 回退分支保留给旧版 SatDump 产物。

**机制 D：模块自带 UI（`drawUI()` 在每个 `ProcessingModule` 内，主程序只遍历 ImGui 窗口）**
- 我们的落点：Qt 面板体系，一个面板承载多个模块。
- 该不该搬：**不搬**。ImGui 即时模式与 Qt 信号槽模型不同构，硬搬会把 UI 状态散进处理模块。
- 搬的代价：不适用。

**机制 E：模块自描述 + 工厂（`getID()` / `getInstance()` / 输入输出类型声明）**
- 我们的落点：`mbdsdr_ai/sat_pipeline_runner.py:105` `register_stage_handler(module, fn)`，已有注册表，但**只做绑定，不做类型兼容校验**。
- 该不该搬：**只借鉴类型声明**。给 stage handler 加 `inputs/outputs` 元数据，`run_pipeline` 在拼 steps 时校验前后层是否接得上；目前 `level_index` 只用于断点跳过，接错层要到运行时才炸。
- 搬的代价：低-中。要逐条 handler 补两个字段；校验是纯增量，不影响现有调用。

### 2.6 sdrpp（`D:\mbdsdr\reference\sdrpp`，C++ / ImGui，GPL-3.0）

我们的 `desktop/spectrum_widget.py` 已经是 SDR++ 与 gqrx 手势模型的移植（文件内多处直接引用 `main_window.cpp` / `waterfall.cpp` 行号），所以本节不写「照抄」，只写**现在还差什么**与**手势冲突点在哪**。

**机制 A：几何层掐死窄条误判（`core/src/gui/widgets/waterfall.cpp:311-327`）**

```cpp
for (auto const& [name, _vfo] : vfos) {
    if (_vfo->bandwidthLocked) { continue; }
    if (_vfo->rectMax.x - _vfo->rectMin.x < 10) { continue; }   // ← 窄于 10 px 直接不做边沿手柄
```

- 我们的落点（2026-11-12 对 HEAD 核，**已落地**）：`desktop/spectrum_widget.py:3535` `_hit_vfo_at`，`edge = 5.0`（`:3545`），`:3553-3560` 已有窄带保护——`x1 - x0 <= 2 * edge` 时整条按 `body` 处理、不提供边沿手柄，命中范围仍是 ±edge，未额外放大。
- **冲突点①（已修）**：原缺口是「2.4 MHz span / 1000 px 下 2.7 kHz USB 只有约 1.1 px，左右手柄互相覆盖，body 永远进不去」。该判定已按规则落地；窄带 CW/USB 回归用例尚未补（记为待办，不是待查）。
- 该不该搬：**已搬**（只搬判定规则，未搬代码）。
- 搬的代价：已付讫。

**机制 B：带宽参考系 `reference`（`:313-327`，`REF_LOWER` / `REF_UPPER` / `REF_CENTER`）**
哪个手柄可拖由参考系决定，调带宽时固定另一侧，避免连续拖拽导致中心漂移。
- 我们的落点（2026-11-12 对 HEAD 核）：`:4198-4214` 只区分 `resize_l` / `resize_r`，中心按「拖哪侧就固定另一侧」即时重算；**没有持久化的参考系**——封闭集证据 `` `HEAD:mbdsdr_ai/vfo_manager.py:50-60` ``（`VfoState` 字段：`vfo_id/center_hz/bw_hz/mode/label/muted/squelch_level_db/squelch_enabled/gain_db/delta_lock/active`，无 `reference`）。
- 该不该搬：**搬概念，不必搬三态**。单次拖拽内我们的行为与 `REF_CENTER` 之外的两态一致；差异在「松手后再次拖同一条边」时，参考系是否延续。低优先——先记为「已知差异」。
- 搬的代价：低，但需要 VFO 模型加字段并落进配置序列化，属于模型改动，不建议与机制 A 同批。

**机制 C：视图平移 ≠ 硬件中心频率（`:411-412` 滚轮只动 `viewOffset`，撞到 `wholeBandwidth` 边缘才推 `centerFreq` 并置 `centerFreqMoved`）**
- 我们的落点：`:3381` `_rf_shift` 字段（Ctrl+拖 = 只平移视图、不改中心频率）、`:3912` 按下时取 `Qt.ControlModifier`、`:4216-4222` 松手按 `_rf_shift` 决定是否 `_emit_tuned`。
- **已同构，不搬**。差异只在语义载体：它用「鼠标在哪个轴」区分，我们用 Ctrl 修饰键。**写进文档是为了防止后续「统一手势」时把 `_rf_shift` 当冗余删掉**。
- 搬的代价：不适用。

**机制 D：方向键/滚轮共用一个平移步长，且以鼠标位置为前置（`:438-440`，`mouseInFreq` 才吃方向键，步长 `viewBandwidth/20`）**
- 我们的落点（2026-11-12 对 HEAD 核）：`:3755-3786` `keyPressEvent` 方向键步进**中心频率**（Shift×10 / Alt×0.1）；键位不再写裸键码，统一取自 `desktop/shortcuts.py:114-121`（`scope="spectrum"`），频谱侧的局部键码表在 `spectrum_widget.py:88`。
- **冲突点②（已收敛一半）**：同一按键在 SDR++ 是「平移视图」、在我们是「步进调谐」。这仍是**有意的产品分歧**（我们是电台工作台，步进是主操作），不建议统一。焦点策略已从「隐式依赖焦点」变成显式约定：`shortcuts.py:115` 写明「频谱获焦时生效」，用 `WidgetWithChildrenShortcut` 限定作用域（`shortcuts.py:27`）。仍存在的边界是：焦点不在谱面时按键不会落到频谱——这是约定，不是缺陷。
- 该不该搬：**不搬语义**；「前置条件显式化」已落地。
- 搬的代价：已付讫（键位收口归 shortcuts 线）。

**机制 E：松开鼠标时无条件清空全部手势态（`:283-294`，`IsMouseDown(Left)` 为假就一次性清 `fftResizeSelect / freqScaleSelect / vfoSelect / vfoBorderSelect / lastDrag`）**
- 我们的落点（2026-11-12 对 HEAD 核，**已落地**）：`desktop/spectrum_widget.py:4168` `mouseReleaseEvent` 在入口无条件调用 `_end_all_gestures()`（`:4152-4166`），先清 `_is_panning` / `_vfo_drag_mode` / `_rf_shift` / `_vfo_create_mode`，再由各分支做副作用。
- **冲突点③（已修）**：原缺口是「四条早退分支各清各的，任何分支加 `return` 都可能留下 `_is_panning=True`」；现在清态先于所有分支。
- 该不该搬：**已搬**（单一出口清理）。
- 搬的代价：已付讫。

**机制 F：dB 轴独立热区（我们 `:1320` `_DB_AXIS_DRAG_W = 40.0`，`:3662`/`:3889` 命中优先级最高）**
- **冲突点④**：谱面左侧 40 px 内不能调谐、不能拖 VFO。这是有意的取舍（窄窗口下 dB 轴要够宽），但**必须写死在文档里**，否则后续「用户体验优化」会把它当 bug 改掉，再引发反向投诉。
- 该不该搬：不适用（本地设计），仅登记为**不可随手改的约定**。

## 3. 已核 / 待查 清点（2026-11-12，对 HEAD）

口径：本节逐条列出上文出现过的「事实判断」类条目；纯裁定（不搬/暂不搬）不单独计入已核。计数以实际条目为准。

> **口径对账（102 vs 33）**：`102` 是 §2.4 时间源普查的**行级命中数**（10 时间基准 + 46 动画节流 + 29 日志耗时 + 17 其他 = 102，覆盖 20 个文件），与本节 33 条**结论条目**不是同一张表；§0 项目清单是 45 个项目（21 + 24）。文件历史只有两次提交：`fc5d0876` 新建 410 行、`2e35798a` +35/−14，均为文本替换，**没有机制/专项条目被删**。

### 3.1 已核（33 条）

**Look4Sat（5）**
1. 机制 C 过境倒计时 + UTC 开关 —— 已落地（`sat_track_panel.py:156-167/281-318/560-599`）。
2. 机制 B 多星同盘 —— **已落地在天空图**（`rf_sky_view.py:8928` 逐星建对象、`:8995` 一次画全部）；专用雷达面板仍按设计只画一颗。
3. 机制 A 数据形状 —— 8 颗星、每条单 `freq_hz`、`identify_by_freq` 默认 3000 Hz。
4. 机制 D 观测点 —— 每次调用经 skyfield `Topos` 重建（`sat_passes.py:481/624/777`）。
5. 机制 E SSTV 模式表 —— 与 Look4Sat 同构，无增量。

**gpredict（8）**
6. 机制 A 单时钟 —— 已落地为 `_frame_timer`（500 ms）+ `_on_frame_tick`（天空 10 帧节流）。
7. 机制 B 地面站 —— `GroundStation` 为显式对象、显式传参（`sat_passes.py:308`）。
8. 机制 C 两个门 —— `VISIBLE_MIN_ALT_DEG=5.0` 与用户峰值筛选门分开。
9. 机制 E 控制层自查 AOS/LOS —— 停止判据在 500 ms 闭环内（`main_window.py:13415-13422`）。
10. 专项① 时间基准 —— 单一时间引擎 + `ClockGuard`，视图层不另造。
11. 专项② 预设/书签 —— gpredict 无此概念，不适用。
12. 专项③ 无设备 —— 第三档「占用即置灰、退出占用写回」在 gpredict 成立；我方无此档（见第 33 条）。
13. 专项④-1 TLE 刷新 —— 无 10 分钟周期任务，仅陈旧时一次性 1.5 s 延迟刷新 + 手动刷新。

**gqrx（4）**
14. 机制 A 书签数据结构 —— 频率/名字/调制/带宽/共享标签 + CSV 明文。
15. 机制 B 面板激活路径 —— 三参数（freq/mode/bandwidth）同构。
16. 机制 B 旗子激活路径 —— 单参数 `Signal(float)`；且内部书签元组为 4 元组、无带宽字段（`main_window.py:8118-8119/2056-2057/8123-8125`）。
17. 机制 C 未选源 —— 配置未完成不进主界面，无空态。

**satdump（5）**
18. 机制 A 管线 —— 层断裂跳过/推荐采样率/按星配置已同构。
19. 机制 B 插件注册 —— 未拆；条目 8 < 15，触发条件未到（封闭集 `` `HEAD:mbdsdr_ai/sat_pipeline_params.py:82` ``：`SATELLITE_PIPELINES: Dict[str, SatellitePipeline] = {`）。
20. 机制 C 产品清单 —— 已落地（`dataset.json` 优先，glob 兜底）。
21. 机制 D 模块自带 UI —— 与 Qt 面板模型不同构。
22. 机制 E 类型声明 —— 未落地，`register_stage_handler` 仍只绑定不校验（`` `HEAD:mbdsdr_ai/sat_pipeline_runner.py:105` ``：`def register_stage_handler(module: str,`，签名无 `inputs/outputs`）。

**sdrpp（6）**
23. 机制 A 窄条保护 —— 已落地（`spectrum_widget.py:3553-3560`）。
24. 机制 B 带宽参考系 —— 未实现（封闭集 `` `HEAD:mbdsdr_ai/vfo_manager.py:50-60` ``，`VfoState` 无 `reference` 字段），记为已知差异。
25. 机制 C 视图平移 —— 已同构（`_rf_shift`，`:3381/3912/4216-4222`）。
26. 机制 D 方向键 —— 语义分歧保留；键位已收进 `shortcuts.py`（scope=spectrum），焦点为显式约定。
27. 机制 E 单一出口清态 —— 已落地（`_end_all_gestures()`，`:4152-4166/4168`）。
28. 机制 F dB 轴热区 —— `_DB_AXIS_DRAG_W=40.0`（`:1320`），登记为不可随手改约定。

**时间源普查（1）**
29. 102 处 / 20 文件；时间基准 10、动画节流 46、日志耗时 29、其他 17；真缺口 7 处。

**补充核验（4）**
30. gpredict 专项④-2 —— 面板启动全量构造、无懒创建（`main_window.py:2246/10387/1888`）。
31. gqrx 机制 B 带宽持久化 —— 元组 4 字段无带宽，`FrequencyManager.add` 无带宽参数，**带宽不持久化**（`main_window.py:8118-8119/2056-2057/8123-8125`）。
32. Look4Sat 机制 A `low/high` 与 `invert` —— link 字面量字段封闭集无 `low/high/invert`（`sat_links.json:14-15`）；`source` 只到卫星级（`:18`），逐条带宽溯源未做。
33. gpredict 专项③ 我方占用态 —— 会话期间被禁控件只有 `connect_btn`/`disconnect_btn`，**确无第三档**（`device_panel.py:499-506`）。

### 3.2 待查（0 条）

原 5 条待查已于 2026-11-12 全部结案（见 §3.4），当前无待查项。

### 3.3 待办（不计入上表）

- gpredict 专项③ 占用态（设备会话置灰 + 断开写回）—— 采纳与否待定。
- satdump E 类型声明（排 C 之后）。
- gqrx B 书签模型补带宽字段并合并旗子路径。
- sdrpp A 窄带 CW/USB 回归用例。

### 3.4 5 条待查的结案记录

| 原风险 | 条目 | 结论 | 依据 |
|---|---|---|---|
| 高 | Look4Sat B 多星同盘 | **已核：其实已落地**（天空图多星） | `rf_sky_view.py:8928/8995` |
| 高 | gpredict ③ 我方占用态空档 | **已核：确无第三档** | `device_panel.py:499-506` |
| 中 | gpredict ④-2 面板懒创建 | **已核：启动全量构造，非懒创建** | `main_window.py:2246/10387/1888` |
| 中 | gqrx B 带宽持久化 | **已核：未持久化（模型无该字段）** | `main_window.py:8118-8119/2056-2057/8123-8125` |
| 低 | Look4Sat A `low/high` + `invert` | **已核：字段不存在；逐条带宽溯源未做** | `sat_links.json:14-15/18` |
