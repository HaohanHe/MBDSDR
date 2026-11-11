# 卫星过境与追踪 · 只读缺口调研

- 日期：2026-11-11
- 范围：`desktop/sat_track_panel.py`、`desktop/new_spacetime_panel.py`、`desktop/rf_sky_view.py`、`desktop/main_window.py`（卫星段）、`mbdsdr_ai/{sat_passes,new_spacetime_timeline,new_spacetime_tle,tle_integrity,sat_tracker,orbit,sat_links}.py`
- 性质：**只读**。未改动任何产品代码，未写用户配置，全程 `MBDSDR_OFFLINE=1`，无联网请求。
- 观测站：39.9042°N / 116.4074°E / 50 m（探针参数，非用户配置）
- 时间基准：2026-11-11 12:28:55 UTC

## 0. 结论摘要

1. **星历已死，界面照跑。** 6 颗内置卫星的 TLE 历元全部为 2026-09-25，年龄 46.6–47.1 天，`tle_integrity` 判 `dead`、`pass_eligible=False`、`tracking_eligible=False`；但两者在 `desktop/` 侧**零消费**——过境表照列 23 条、跟踪按钮照常可点、500 ms 环照常下发调谐。同一时刻用内置 TLE 与磁盘缓存 TLE 算 ISS，仰角 **-11.3° vs -60.2°（差 48.9°）**。
2. **一个产品四套卫星清单、两套 TLE 缓存。** 面板 6 颗（含 AO-73/FOX-1B）、新时空 6 颗（含 METEOR M2/FENGYUN 3D）、天空图目录 6 颗、AI 工具链 6 颗；**交集 4 颗**（NOAA 15/18/19 + ISS），并集 8 颗。缓存目录 `~/.mbdsdr/tle_cache`（8 个文件，含 2026-09-27 旧件）与用户目录 `~/.mbdsdr/tle`（实测不存在）互不感知。（初版误写「交集 3 颗」，已按四份源码表逐名核对更正。）
3. **两套过境算法差 52–182 倍。** 面板 `TimelineEngine` 23 条 / **41 ms**（60 s 网格、无细扫，自认峰值偏差 ≤30 s、升落 ≤58 s、峰值仰角 ≤7.6°）；AI 链路 `sat_passes` 23 条 / **2164 ms**（三轮 2437/7468/8795 ms），72 h 6 星约 **18–21 s**，工具调用极易超时。
4. **时区三套口径。** 面板写 UTC 但单元格只有 `%H:%M`；`sat_passes.summary()` 带 `Z`；`orbit.predict_passes()` 用 `time.localtime` 返回本地时间且**无时区标注**。AI 说「21:00 过境」、面板写「13:00」，差 8 小时。
5. **过境表是只读陈列。** 2 列、23 行、最小高度 96 px（≈3 行可见）、无排序、无筛选、无倒计时、点击无行为、跨 UTC 日界不显示日期（实测 1/23 条跨日）。

## 1. 实测基线数字

| 指标 | 实测值 | 取证方式 |
|---|---|---|
| 下拉可选卫星数 | **6** | `sat_tracker._BUILTIN_TLES` |
| 此刻可见卫星（el ≥ 5°） | **0 / 6** | skyfield 逐星 alt/az |
| 过境表列数 | **2** | `new_spacetime_panel.py:471-473` |
| 过境表行数（24 h / 峰值门 10°） | **23** | 面板同参数重算 |
| 过境表行数（24 h / 可见门 5°） | **28** | 同上 |
| 过境表排序能力 | **无**（全仓无 `setSortingEnabled`） | grep 全仓 |
| 过境表可见行数 | **≈3 行**（`setMinimumHeight(96)`，右栏 300 px） | `new_spacetime_panel.py:484` |
| 一次预报耗时（面板口径） | **41 ms** / 23 条 | TimelineEngine 同参重算 |
| 一次预报耗时（AI 口径） | **2164 ms** 单轮；三轮 2437 / 7468 / 8795 ms | `sat_passes.predict_passes` |
| 72 h 6 星（AI 口径） | **≈18–21 s** | 实测 20901 ms |
| TLE 更新入口 | **3 手动 + 1 自动** | 详见 G10 |
| 内置 TLE 年龄 | **46.6–47.1 天**，`tier=dead` ×6 | `tle_integrity.freshness` |
| 卫星清单套数 | **4**，交集 4 颗 / 并集 8 颗 | 四份源码表 |
| TLE 缓存 | 2 个目录，`tle_cache` 8 文件（含 09-27 旧件） | 文件系统实测 |
| 同一时刻 ISS 仰角（内置 vs 缓存 TLE） | **-11.3° vs -60.2°（差 48.9°）** | 双源对拍 |
| 跨 UTC 日界过境 | **1 / 23** | 逐条比对 rise/set 日期 |
| 跟踪轮询周期 | **500 ms** | `main_window.py:12105` |
| 天空图卫星刷新周期 | **5000 ms**，且只刷授时 | `main_window.py:12340-12350` |

## 2. 缺口明细

### G1 [P0] 过期星历不做闸门：dead TLE 仍可过境预报与闭环跟踪
- 取证：`mbdsdr_ai/tle_integrity.py:289,311-312`（`pass_eligible`/`tracking_eligible` 产出）；`desktop/main_window.py:12037-12044`（启动只校验星名/频点/观测站/仰角，**无星历新鲜度校验**）；`desktop/new_spacetime_timeline.py` 过境计算不过滤 freshness。
- 现象：实测 6 颗全部 `dead`，过境表仍列 23 条，跟踪按钮可用；`tracking_eligible` 在整个 `desktop/` 下无任何读取点。
- 影响：用户照表追星，实测同一时刻 ISS 指向差 48.9°，等于追不到；云台会按错误角度转。
- 严重度：**P0**｜成本：**小**（新增一次 `tracking_eligible` 判定 + 明确文案）｜产品决定：**需要**（过期时「禁止启动」还是「警告后允许」）

### G2 [P0] 四套卫星清单 / 两套 TLE 缓存，同机不同答案
- 取证：`mbdsdr_ai/sat_tracker.py`（NOAA15/18/19、ISS、AO-73、FOX-1B）｜`mbdsdr_ai/new_spacetime_tle.py:29-36`（ISS/NOAA19/18/15、METEOR M2、FENGYUN 3D）｜`mbdsdr_ai/orbit.py:71-78`（NOAA15/18/19、ISS、METEOR M2、FENGYUN 3D）｜`desktop/rf_sky_view.py:1583-1590`（同上 6 颗）
- 现象：面板下拉看不到 METEOR M2 / FENGYUN 3D；新时空面板看不到 AO-73 / FOX-1B；AI 工具用 `~/.mbdsdr/tle_cache`（25544 历元 2026-11-09），面板用内置 2026-09-25。
- 影响：同一台机器三个入口给出不同星、不同过境、不同 az/el，用户无法判断哪个可信；也是 G1 的根因。
- 严重度：**P0**｜成本：**中**（统一为单目录 + 单缓存）｜产品决定：**需要**（最终清单以谁为准）

### G3 [P1] 过境算法两套并行，速度与精度都不在同一档
- 取证：`mbdsdr_ai/sat_passes.py:87-112`（60 s 粗扫 + 2 s 细扫 + 二分 0.5 s）；`mbdsdr_ai/new_spacetime_timeline.py:274-395`（同网格、无细扫）；`desktop/new_spacetime_panel.py:809-813`（自认偏差声明）；`desktop/new_spacetime_panel.py:764`（UI 直接用 `PASS_COARSE_STEP_S`）
- 现象：同参 23 条：41 ms vs 2164 ms；AI 口径 72 h 达 18–21 s。
- 影响：面板精度已知劣于 AI 口径，但界面没有任何「精度档」说明；AI 调用易触发超时/重试。
- 严重度：**P1**｜成本：**中**（面板后台细扫 + 精度标注）｜产品决定：**需要**（面板是否值得多花 2 s 换 30 s 精度）

### G4 [P1] 时区三套口径，AI 与界面差 8 小时
- 取证：`desktop/new_spacetime_panel.py:472-473`（表头 UTC）、`:909-925`（`_fmt_hm` 只 `%H:%M`）；`mbdsdr_ai/sat_passes.py:163-185`（`%H:%M:%SZ`）；`mbdsdr_ai/orbit.py:616-621`（`time.localtime`，无标注）
- 现象：AI 工具链返回「21:00:05」，同一过境面板显示「13:13」。
- 影响：用户按 AI 给的时间守听必错过；且无任何「本地/UTC」切换设置（`desktop/config_schema.py:441-466` 仅 3 个卫星项）。
- 严重度：**P1**｜成本：**小**（统一 UTC + 加 `Z`/日期；本地时间作为可选显示）｜产品决定：**需要**（默认 UTC 还是本地）

### G5 [P1] 过境表无排序、无筛选、无点击、无倒计时
- 取证：`desktop/new_spacetime_panel.py:471-487`（`QTableWidget(0, 2)`，无 `setSortingEnabled`，`setMinimumHeight(96)`）；`:459-465`（筛选规则只以文字说明，无控件）
- 现象：23 条只能按时间顺序滚动；「下一次最值得看的一次」需要人眼逐行比。
- 影响：核心决策（今晚追哪颗）成本高；窄屏 96 px 只露 3 行。
- 严重度：**P1**｜成本：**小-中**｜产品决定：**需要**（是否引入「最近/最高仰角」排序与「今晚」快捷）

### G6 [P1] 跨 UTC 日界不显示日期
- 取证：`desktop/new_spacetime_panel.py:909-925`（单元格 `%H:%M`，仅 tooltip `%m-%d %H:%M`）
- 现象：实测 23 条中 1 条跨日，单元格呈现为「23:58 → 00:03」。
- 影响：用户无法判断落在哪一天，尤其窗口 72 h 时更严重。
- 严重度：**P1**｜成本：**小**｜产品决定：否（属缺陷修正）

### G7 [P1] TLE 自动刷新判据（≥2 天）与新鲜度阈值（24 h）不一致
- 取证：`desktop/main_window.py:11909-11929`（`>= 2 天` 才启动 1500 ms 单发刷新）；`mbdsdr_ai/tle_integrity.py:63-67`（fresh ≤24 h、usable ≤7 d）
- 现象：TLE 已判「过期」仍要再等一天才自动拉取；且刷新失败后仅状态栏一句，下次启动再次尝试。
- 影响：长期离线用户每次开窗都看到 dead 星历；自动刷新形同虚设。
- 严重度：**P1**｜成本：**小**｜产品决定：否

### G8 [P1] 跟踪环 500 ms 不校验「是否真在过境」
- 取证：`desktop/main_window.py:12103-12113`（定时器 500 ms）、`:12094-12101`（备份/恢复频率模式，气象星强制 WFM）
- 现象：`_start_satellite_tracking` 只写「仰角 <5° 时停止」的约束，实测「此刻 el=0、可见 0 颗」时按钮仍可启动并立刻调谐一次（`:12121`）。
- 影响：无效调谐会打断用户正在听的其他频点（虽会恢复，但存在空窗）。
- 严重度：**P1**｜成本：**小**｜产品决定：**需要**（未过境时是否允许「预跟踪」）

### G9 [P1] 天空图有多星绘制能力，但没有「多星一览」的数据入口
- 取证：`desktop/rf_sky_view.py:8831-8878`（遍历目录逐星算 az/el + 10 min 轨迹 + 颜色区分 amateur）、`:2680-2705`（目录结构含 kind/alias/epoch）；`desktop/main_window.py:12340-12350`（周期任务**只刷授时**，卫星位置由 `SatelliteTracker` 单星驱动）
- 现象：渲染层支持多星，运行期目录来自 `rf_sky_view.py:1459-1473`（`~/.mbdsdr/tle/*.tle`）或 `:1583-1590` 的 6 颗；与跟踪目标不同源。
- 影响：用户看不到「此刻天上还有哪几颗」，选星只能退回下拉框；gpredict 式 sky-glance 体验缺失。
- 严重度：**P1**｜成本：**中**｜产品决定：**需要**（天空图是否作为主选星入口）

### G10 [P2] TLE 更新 3 个手动入口 + 1 个自动，失败后无「出路」
- 取证：面板按钮 `desktop/sat_track_panel.py:165-178`；菜单 `desktop/main_window.py:1499-1520`；新时空「刷新过境」`desktop/new_spacetime_panel.py:454`；启动自动 `desktop/main_window.py:11917-11936`；结果回贴 `:11945-11985`
- 现象：失败只落状态栏一句 `tle_refresh_status_text`；无「导入本地 TLE」对话框，只有目录约定（`rf_sky_view.py:1459-1473`）且提示文案未提（`new_spacetime_panel.py:490-492` 写「需要联网更新 TLE」）。
- 影响：离线/被墙用户无自助出路，只能接受 dead 星历。
- 严重度：**P2**｜成本：**中**｜产品决定：**需要**

### G11 [P2] 无卫星分组（业余/气象/导航/科学）
- 取证：`mbdsdr_ai/orbit.py:416-419,489-491`（`satellite_type` 仅 `all`/`weather`/`amateur`，且 `amateur` 等价于「名字含 ISS」）；UI 无分组控件
- 现象：现有 6 颗里 4 气象、1 ISS、1 业余；导航/科学分类枚举存在但永远为空。
- 影响：清单一扩就退化成一条长下拉；无法按任务类型选星。
- 严重度：**P2**｜成本：**小**｜产品决定：**需要**（分组维度定义）

### G12 [P2] 无「下次过境还有多久」与提醒
- 取证：全仓无 `next_pass`/倒计时实现（仅天空图 `set_upcoming_passes` 列表，`rf_sky_view.py:2835-2851` 上限 12 条、`:7794-7798` 只显示 8 行）
- 影响：追星必须盯表；错过就是错过。
- 严重度：**P2**｜成本：**中**｜产品决定：**需要**（是否做通知/响铃）

### G13 [P2] 长窗口结果无组织
- 取证：`desktop/config_schema.py:452-457`（窗口 1–72 h）；实测 72 h / 10° 6 星 73 条
- 现象：表格无分页、无「只看今晚/只看仰角>30°」快捷，73 行在 96 px 视口里滚动。
- 严重度：**P2**｜成本：**小**｜产品决定：**是**

### G14 [P2] AI 工具链过境耗时不可控
- 取证：`mbdsdr_ai/sat_passes.py:259-268`（`hours` 可达 72）、`:87-112`（细扫 2 s 逐点、skyfield 单点 `at()` 开销大）
- 现象：72 h 6 星 ≈18–21 s；对比面板同数据 41 ms–百毫秒级。
- 影响：AI 侧易超时、重试、阻塞对话；也解释了为何面板另写一套。
- 严重度：**P2**｜成本：**中**（批量 `at()` 向量化或复用 sgp4 直算）｜产品决定：否

### G15 [P3] 缓存孤儿与旧件不清理
- 取证：`~/.mbdsdr/tle_cache` 实测 8 个文件，含 2026-09-27（44016）、2026-11-03（54234）历史错号件；`mbdsdr_ai/orbit.py:80-81`
- 影响：换号事故（44016/54234）的残留数据仍在盘上，一旦目录回退会再次被当有效缓存。
- 严重度：**P3**｜成本：**小**｜产品决定：否

### G16 [P3] 面板提示语与真实可用路径不一致
- 取证：`desktop/new_spacetime_panel.py:490-492`（「需要联网更新 TLE」）vs `desktop/rf_sky_view.py:1459-1473`（离线目录可用）
- 影响：离线用户被引导到唯一不可用的路径。
- 严重度：**P3**｜成本：**小**｜产品决定：否

## 3. 已修项（本轮实测确认在位，不计入缺口）

- **TLE 陈旧度呈现**：`desktop/sat_track_panel.py:338-355` 已接收 `age_days/tier`，输出「TLE 日期: 2026-09-25 · 失效」+ 颜色 + `band_note` tooltip；`desktop/sat_track_panel.py:310-329` 把档位后缀拼进下拉项；`desktop/main_window.py:11987-12015` 取目录**最老**一颗的口径。提交 `b6bc856c`。
- 结论：**呈现已闭环，闸门仍未闭环**（见 G1/G7）——文案已告知「失效」，但「失效仍可跟踪」没有对应动作。

## 4. 复现命令

```bash
cd D:/mbdsdr/MBDSDR
# 环境：MBDSDR_OFFLINE=1，避免任何联网
python D:/mbdsdr/output/tmp/probe_final.py   # 探针脚本，取证后即删
```

探针逐项对应：可见星数 / TLE 年龄与档位 / 24 h 与 72 h 条数 / 两套算法耗时 / 跨日条数 / 双源 ISS 仰角差。

## 5. 按严重度排序（20 行内）

| # | 严重度 | 缺口 | 关键证据 | 成本 | 产品决定 |
|---|---|---|---|---|---|
| 1 | P0 | 过期星历无闸门，dead 仍预报+跟踪 | `main_window.py:12037-12044` | 小 | 需要 |
| 2 | P0 | 四套清单/两套缓存，同机不同答案 | `new_spacetime_tle.py:29-36`、`orbit.py:71-78` | 中 | 需要 |
| 3 | P1 | 两套过境算法差 52–182 倍且无精度标注 | `sat_passes.py:87-112`、`new_spacetime_panel.py:809-813` | 中 | 需要 |
| 4 | P1 | 时区三套口径，AI 与 UI 差 8 小时 | `orbit.py:616-621` | 小 | 需要 |
| 5 | P1 | 过境表无排序/筛选/点击/倒计时 | `new_spacetime_panel.py:471-487` | 小-中 | 需要 |
| 6 | P1 | 跨 UTC 日界无日期 | `new_spacetime_panel.py:909-925` | 小 | 否定 |
| 7 | P1 | 自动刷新 2 天 vs 新鲜度 24 h 不一致 | `main_window.py:11909-11929` | 小 | 否定 |
| 8 | P1 | 跟踪环 500 ms 不校验可见性 | `main_window.py:12103-12113` | 小 | 需要 |
| 9 | P1 | 天空图无多星一览数据入口 | `main_window.py:12340-12350` | 中 | 需要 |
| 10 | P2 | TLE 更新无离线导入出路 | `new_spacetime_panel.py:490-492` | 中 | 需要 |
| 11 | P2 | 无卫星分组（导航/科学枚举恒空） | `orbit.py:416-419` | 小 | 需要 |
| 12 | P2 | 无下次过境倒计时/提醒 | `rf_sky_view.py:2835-2851` | 中 | 需要 |
| 13 | P2 | 72 h 73 条无分页/无快捷筛选 | `config_schema.py:452-457` | 小 | 需要 |
| 14 | P2 | AI 链路 72 h 18–21 s，易超时 | `sat_passes.py:87-112` | 中 | 否定 |
| 15 | P3 | 缓存孤儿（44016/54234 旧件） | `orbit.py:80-81` | 小 | 否定 |
| 16 | P3 | 空态提示只指向联网路径 | `new_spacetime_panel.py:490-492` | 小 | 否定 |

> 注：第 15/16 行的「小」成本指清理与文案，但第 15 行需先确认目录回退路径已不复用旧号，属 Rust 式「先证后删」。
