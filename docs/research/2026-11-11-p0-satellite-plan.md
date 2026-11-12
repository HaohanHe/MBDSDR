# 卫星两条 P0 的可执行方案（判断口径先钉死）

> 证据状态：E4——本文件数字未逐条绑定原始件/复跑命令，引用前须复核（判据见 docs/evidence-standard.md）。
- 日期：2026-11-11
- 前置：[2026-11-11-gap-satellite.md](2026-11-11-gap-satellite.md)
- 状态：**方案，不改代码**。本文只钉判断口径、落点、反证与验收；实现另行提交。
- 事实校正：前一份报告的「四套清单交集 3 颗」为误写，逐名核对为 **交集 4 颗 / 并集 8 颗**，已回改原文。

---

## P0-1 过期星历无闸门

### 1.1 现状（三个必须承认的事实）

1. 数据层已经算出了结论，但没人用：`tle_integrity.freshness()` 返回 `pass_eligible` / `tracking_eligible`（[tle_integrity.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/tle_integrity.py#L289-L312)），`SatelliteTracker._annotate_entry()` 也把 `tle_tier` / `pass_eligible` / `accuracy_risk` 打进目录（[sat_tracker.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sat_tracker.py#L262-L274)），`current_position()` 还专门注释「过期 TLE 不可盲目跟踪」（[sat_tracker.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sat_tracker.py#L599-L604)）。
2. `desktop/` 侧对这些字段的读取次数为 **0**：启动只校验星名/下行频点/观测站/仰角（[main_window.py](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L12037-L12044)），到点即调谐（[main_window.py](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L12103-L12121)）。
3. 代价是可实测的：内置 TLE 历元 2026-09-25（46.6–47.1 天，全 `dead`），与磁盘缓存（历元 2026-11-09）在同一时刻算 ISS，仰角 **-11.3° vs -60.2°，差 48.9°**。

### 1.2 推荐：分档拦截，不做「确认后放行」

**推荐结论：`dead` 一律禁止写硬件（调谐 + 云台），只保留只读演示；`expired` 允许调谐但必须二次确认且禁止云台；`fresh/usable` 直连。**

理由（按权重）：

1. **确认框无法传达真实后果。** 47 天根数实测偏差 48.9°，这不是「精度差一点」，而是「指到天上去」。二次确认把不可解释的误差包装成用户可承担的选择，属于把风险转嫁。
2. **写硬件的动作不可撤销。** 调谐会打断用户正在听的频点、云台会按错误角度转动；相比之下「看不到过境列表」是可撤销的损失。
3. **用户「想追旧星」的诉求有正确的出口，而且比确认框更短。** 只要根数是新的（自己算的/别人给的/刚下载的），走导入或刷新即可跳档；真正没有新根数时，用户要的其实是「我只想手动听」，这由**只读演示**满足：照常算并显示将下发的频率与多普勒，只是不下发。
4. **分档而不是一刀切**，因为 `expired`（8–30 天）的误差随龄增长但仍可用于「先把频率对到大致位置」，而 `dead`（>30 天）连方向都不对。用统一的 `expired` 处理两档，会让闸门要么过松要么过紧。

反方意见（记录在案）：允许「确认后跟踪」能保住极端场景（用户拿不到新根数但想听个响）。本方案的回应是「只读演示 + 手动调谐」等价覆盖该场景，且不写硬件；若产品最终选择放行，闸门位置与反证设计不变，只需把 `dead.allow_tune` 从 `False` 改为 `confirm`。

### 1.3 四档 × 四操作判定表（唯一口径）

档位定义来自 `tle_integrity`：`FRESH_MAX_HOURS=24`、`USABLE_MAX_DAYS=7`、`EXPIRED_MAX_DAYS=30`、`DEAD_STALE_MAX_DAYS=90`（[tle_integrity.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/tle_integrity.py#L63-L67)、[age_band](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/tle_integrity.py#L108-L125)）。

| 档位 | 年龄 | 过境列表 | SDR 调谐（写硬件） | 云台/旋转器 | AI 工具写操作 | 界面表现 |
|---|---|---|---|---|---|---|
| `fresh` | ≤ 24 h | 列出 | 允许 | 允许 | 允许 | 正常，无标记 |
| `usable` | 1–7 d | 列出 | 允许 | 允许（附误差随龄增长的说明） | 允许 | 灰色小字「星历 3 天」 |
| `expired` | 7–30 d | 列出 + 标「过期 N 天」 | **需二次确认**（确认框必须写年龄与「指向可能偏差数度以上」） | **禁止** | 需二次确认 | 橙色标记 + 跟踪按钮旁黄色提示 |
| `dead` | 30–90 d | **默认不列**（需勾「显示失效星历」） | **禁止**（只读演示：显示将下发的频率，不写设备） | **禁止** | **拒绝** | 红色「星历失效 47 天」+ 「更新/导入 TLE」按钮 |
| `unknown` | 无历元 / 解析失败 / 未来历元 | 不列 | 禁止 | 禁止 | 拒绝 | 红色「星历不可用」 |

补充两条硬规则（不占新档位）：

- **运行中恶化**：跟踪过程中若刷新后档位掉到 `expired` 以下（例如用户点了更新但拉到的是更旧的数据），下一拍必须停止下发并提示，不等用户手动停。
- **身份不匹配**：`identity_status == "mismatch"`（对象名与 CATNR 冲突，44016/54234 事故形态）**无视年龄一律按 `dead` 处理**——错误的对象比过期的对象更危险。

### 1.4 闸门位置：规则只写一份，desktop 只读布尔

**结论：规则实现放 `mbdsdr_ai/tle_integrity.py`（纯函数），唯一对外入口放 `SatelliteTracker`，`desktop/` 侧零档位判断。**

```
tle_integrity.tracking_policy(fresh, identity_status) -> {
    level, allow_pass_list, allow_tune, allow_gimbal,
    require_confirm, command_allowed, label, reason, next_action
}
        │  （唯一实现，纯函数，可单测）
        ▼
SatelliteTracker.tracking_policy_for(name=None)      # 唯一对外入口
SatelliteTracker.current_position(...)["policy"]     # 随位置一起透出（已有透出字段的先例）
        │
        ▼
desktop/main_window.py：
  _start_satellite_tracking()  → 只读 policy["allow_tune"] / ["require_confirm"]
  _poll_satellite_track()      → 只读 policy["allow_tune"] / ["allow_gimbal"]
  _on_sky_gimbal_follow_toggled() → 只读 policy["allow_gimbal"]
```

为什么选这一层：

- **放 `tle_integrity`**：它是阈值与档位的唯一事实源，天然纯函数、无 Qt 依赖，能被 AI 工具链与桌面共用；放在这里不会出现「桌面一套、工具链一套」的第二份规则。
- **放 `SatelliteTracker` 作为对外入口**：`LoopSatTracker` 就是 `SatelliteTracker` 的别名（[main_window.py](file:///D:/mbdsdr/MBDSDR/desktop/main_window.py#L603)），面板、菜单、天空动作总线、AI 工具都汇到它；再往上放就会有一层拿不到目录与站址。
- **不放 `main_window` 调用点**：那是 UI 层，一旦规则写在这里，AI 工具链与未来的 CLI 就必须再写一遍，正是当前 G1 的形态。

关于「不许两个地方各判一次」的落实口径：

- 「判」= 规则实现。规则只有 `tracking_policy()` 一处；
- desktop 的两个调用点（启动 + 每拍）**只读同一个布尔**，不做任何 `tier ==` 比较；
- 守卫测试用行为断言钉住这一点：把 policy 打桩成 `allow_tune=False` 且 `level="fresh"`，桌面仍不得调谐——这证明桌面认的是 policy，不是自己判的档位（不用源码文本断言）。

### 1.5 反证设计（去掉闸门必须红）

新增 `tests/test_sat_tle_gate.py`，全部行为断言，不 mock 底层传播：

| # | 用例 | 断言（红/绿判据） |
|---|---|---|
| 1 | `test_dead_tle_must_not_command_tuning` | 目录塞 dead TLE → `_start_satellite_tracking` 后：`backend.set_frequency` 调用数 **0**、`_sat_track_timer.isActive()` **False**、状态栏含「星历失效」与「更新/导入 TLE」 |
| 2 | `test_dead_tle_must_not_command_gimbal` | 同上 + 云台跟随开启 → 云台指令数 **0**，且 `_gimbal_follow` 被复位 |
| 3 | `test_expired_tle_requires_confirm` | 确认框打桩「取消」→ 调谐 0；打桩「确定」→ 调谐 1 次、云台仍 0 |
| 4 | `test_running_downgrade_stops_next_tick` | 跟踪中把目录改成 dead → 下一拍后调谐停止、状态栏有提示、`_sat_track_timer` 停止 |
| 5 | `test_policy_matrix_table` | 四档 × 五操作的表驱动（纯函数级），`unknown` 与 `mismatch` 按 dead 处理 |
| 6 | `test_dead_tle_keeps_readonly_demo` | dead 时 `current_position()` 仍 `valid=True` 且给出预测频率，但 `policy["command_allowed"] is False` |
| 7 | `test_desktop_defers_to_policy` | policy 打桩 `allow_tune=False, level="fresh"` → 桌面不调谐（证明桌面不自行判档） |

**变异验证（必须留证据）**：临时注释掉 `_start_satellite_tracking` 里的 policy 检查 → 用例 1、2、4、7 变红；恢复后全绿。变异前后的命令与输出记入提交说明（不写入本文）。

### 1.6 P0-1 成本

| 项 | 规模 |
|---|---|
| 新增 | `tracking_policy()` 1 个纯函数；`SatelliteTracker` 1 个方法 + `current_position` 加 1 个键；1 个测试文件（约 7 例）；1 个确认框调用点 |
| 修改 | `main_window` 3 处（启动 / 每拍 / 云台开关）；`sat_track_panel` 1 处（按钮态与提示） |
| 不动 | 阈值、档位、缓存、AI 工具签名 |
| 风险 | `expired` 的确认框会成为高频打扰 → 用 `pass_window` 与「今天不再提示」降噪（产品决定项） |

---

## P0-2 四套清单 / 两套缓存

### 2.1 四套清单逐名核对（含加载函数与数量）

| 代号 | 来源文件 | 加载函数 / 表 | 数量 | 具体卫星 |
|---|---|---|---|---|
| **A** | [sat_tracker.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sat_tracker.py#L84-L115) | `_BUILTIN_TLES` → `__init__` 里 `load_tle()`（[L209-228](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sat_tracker.py#L209-L228)） | 6 | NOAA 15 / NOAA 18 / NOAA 19 / ISS / FUNCUBE-1 (AO-73) / RADFXSAT (FOX-1B) |
| **B** | [new_spacetime_tle.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/new_spacetime_tle.py#L29-L36) | `DEFAULT_SATELLITES` → `TLEManager.fetch(catnr)`（[L177-235](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/new_spacetime_tle.py#L177-L235)） | 6 | ISS / NOAA 19 / NOAA 18 / NOAA 15 / METEOR M2 / FENGYUN 3D |
| **C** | [orbit.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/orbit.py#L71-L78) | `BUILTIN_SATS` → `fetch_tle_record()`（读 `~/.mbdsdr/tle_cache`） | 6 | NOAA 15 / NOAA 18 / NOAA 19 / ISS / METEOR M2 / FENGYUN 3D |
| **D** | [rf_sky_view.py](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L1583-L1610) | `SATELLITE_META` → `_load_celestrak_tle_catalog()`；另有 `_load_user_tle_catalog()`（[L1459-1473](file:///D:/mbdsdr/MBDSDR/desktop/rf_sky_view.py#L1459-L1473)）扫 `~/.mbdsdr/tle/*.tle` | 6 + N | 同 C，另加用户目录（实测**该目录不存在，N=0**） |

### 2.2 集合图

```
        ┌──────────────── 并集 = 8 颗 ────────────────┐
        │                                             │
  A ────┼── NOAA 15, NOAA 18, NOAA 19, ISS ────┼──── C
  (6)   │        （四套的交集 = 4 颗）           │    (6)
        │                                             │
  └ AO-73, FOX-1B                        METEOR M2, FENGYUN 3D ┘
                                                      ▲
                                                      │  B 与 C 完全同集（6 颗）
                                                      │  D 与 C 完全同集（6 颗）+ 用户文件
```

- **交集 4**：NOAA 15 / NOAA 18 / NOAA 19 / ISS
- **A \ C**：AO-73、FOX-1B（面板能看到、新时空/AI/天空图看不到）
- **C \ A**：METEOR M2、FENGYUN 3D（新时空/AI 能看到、面板看不到）
- **B = C = D**（同一组 6 颗，三份硬编码）

### 2.3 两套（实为三处）TLE 数据载体

| 载体 | 位置 | 本机实测 | 谁读 |
|---|---|---|---|
| 内置根数 | 源码里的完整 TLE 行 | 历元 **2026-09-25**，6 颗全 `dead` | **A**（面板/跟踪） |
| 磁盘缓存 | `~/.mbdsdr/tle_cache/NNNNN.tle` | **8 个文件**，25544 历元 **2026-11-09**（新鲜），含 09-27 / 11-03 历史错号件 | **B**、**C**、**D** |
| 用户目录 | `~/.mbdsdr/tle/*.tle` | **不存在，0 文件**（无 UI 入口，只能手工放） | **D**（若有则优先） |

**机制性根因（比「两套缓存」更严重）**：面板的刷新**不落盘**。`SatelliteTracker` 全文只有 1 处 `open()` 且是读（[sat_tracker.py](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sat_tracker.py#L276-L287) 的 `load_satellites_from_file`），`update_tle_from_celestrak()`（[L289-310](file:///D:/mbdsdr/MBDSDR/mbdsdr_ai/sat_tracker.py#L289-L310)）只把新根数写进内存目录。所以：用户点一次「更新 TLE」→ 当次会话新鲜 → **重启回到 2026-09-25**。这正是「48.9° 反复出现」的复现路径，也是 G1 每天都会复发的原因。

### 2.4 危害证据（写进验收）

同一时刻（2026-11-11 12:28:55 UTC，39.9042°N / 116.4074°E）：

| 数据源 | ISS 仰角 | 方位 |
|---|---|---|
| 内置根数（面板/跟踪在用） | **-11.3°** | 57.2° |
| 磁盘缓存（AI/新时空/天空图在用） | **-60.2°** | 244.3° |

**差 48.9°**。这不是「两个界面数字不一致」的观感问题，而是「同一台设备上，一个入口告诉你抬头看北偏东，另一个告诉你卫星在南偏西」。

### 2.5 方案 A（推荐）：收敛到单一真值源

**目标形态**：一张注册表 + 一处根数缓存，其余全部是投影。

```
mbdsdr_ai/sat_catalog.py（新增，唯一真值源）
  ├─ REGISTRY: name, norad, kind(weather/amateur/…), downlink_hz, source
  └─ tle_store: 唯一缓存 ~/.mbdsdr/tle_cache/<norad>.tle（读写都在这）
        │
        ├─ sat_tracker（A）      → list/下拉：REGISTRY 投影；TLE：tle_store
        ├─ new_spacetime_tle（B）→ DEFAULT_SATELLITES 删除，改用 REGISTRY
        ├─ orbit（C）            → BUILTIN_SATS 删除，改用 REGISTRY + tle_store
        └─ rf_sky_view（D）      → SATELLITE_META 删除，改用 REGISTRY 投影
```

必须一起做的三件事（缺一不可）：

1. **删三份硬编码表**（A 的 `_BUILTIN_TLES` 保留为「无缓存时的最后兜底」，但只允许注册表引用它，且必须在 UI 标「内置兜底 · 2026-09-25」）；
2. **面板刷新落盘**：`update_tle_from_celestrak()` 成功后写 `tle_store`，否则重启即回退（当前根因）；
3. **清单从 6 → 8**：AO-73 / FOX-1B 与 METEOR M2 / FENGYUN 3D 都要在册。这是**用户可感知的变化**（下拉变长两组），需产品确认。

成本：**中**。改动 4 个消费方 + 1 个新模块 + 1 处落盘 + 3 处删表 = **9 个改动点**，跨 `mbdsdr_ai` 与 `desktop` 两层。风险集中在「谁的地盘谁改」：4 个消费方分属不同工作线时，注册表必须先合入、再逐线切换（不能并行改同一文件）。

### 2.6 方案 B（不收敛时的最小代价）：只加对账测试

保留四套清单，新增 `tests/test_sat_catalog_single_source.py`：

- 断言 **每一套清单 ⊆ 注册表**（注册表为唯一全集，8 颗）；
- 断言 **同一时刻三处渲染的星名字集相等**（面板下拉 / 新时空过境表出现的星 / 天空图目录）；
- 断言 **同一颗星在同一时刻四个入口的仰角极差 < 0.05°**（同一份根数必须给出同一答案），并保留本条反证：两套根数并存时极差 **> 40°**（实测 48.9°）。

成本：**小**（1 个测试文件，0 产品改动）。

**但它不解决 48.9°**——对账只会让「两个答案不一致」变成红灯，用户仍会看到两个答案。所以：

> **推荐 A，并把 B 的测试原样作为 A 的守卫**（A 落地后同一测试从「红灯」转「绿灯」，且 48.9° 的反证用例继续保留，防止有人把两套根数又塞回来）。

### 2.7 P0-2 成本对比

| | 方案 A 收敛 | 方案 B 只对账 |
|---|---|---|
| 产品改动 | 9 处（4 消费方 + 注册表 + 落盘 + 3 删表） | 0 |
| 是否消除 48.9° | **是** | 否（只报警） |
| 是否修掉「重启回退 dead」 | **是**（落盘） | 否 |
| 用户可感知变化 | 下拉 6 → 8 颗 | 无 |
| 风险 | 跨线改同一批文件，需串行 | 无 |
| 前置产品决定 | 最终清单取 8 颗是否同意 | 无 |

---

## P0-1 / P0-2 的耦合点（必须一起排期）

G1 的闸门会**放大** P0-2 的症状：一旦 `dead` 禁止跟踪，面板的 6 颗会在每次重启后全部变成不可跟踪（因为刷新不落盘）。所以：

- 若先做 P0-1：必须在同一批次里做 **2.5 的第 2 件（刷新落盘）**，否则闸门 = 每次开窗功能不可用；
- 若先做 P0-2：闸门缺失期间，用户仍会被 48.9° 的旧根数驱动硬件，风险持续。
- 最小可发布批次：**P0-1 的 1.4 + 1.3 表 + 2.5 第 2 件**（落盘），再做 P0-2 的 A 收敛。

---

## P1 一句话落点（本次不展开）

| 缺口 | 一句话落点 |
|---|---|
| 过境表只露 3 行 | `new_spacetime_panel.py:484` 的 96 px 最小高度改为随可用高度伸展，并把「今晚 / 最高仰角」做成两枚快捷筛选。 |
| 无排序筛选 | `passes_table` 开 `setSortingEnabled(True)` 不够（单元格是拼接文本），需先把 `TimelineEngine` 的数值字段挂到 `Qt.UserRole` 再按列排序。 |
| 跨日无日期 | `_fmt_hm`（`new_spacetime_panel.py:909`）在 rise/set 不同日时输出 `%m-%d %H:%M`，同日保持 `%H:%M`。 |
| AI 链路慢 52–182 倍 | `sat_passes` 把逐点 skyfield `at()` 换成批量 `at(t_array)`（或复用面板的 sgp4 直算路径），目标 72 h 6 星 < 2 s。 |
| 时区三套 | `orbit.predict_passes`（`orbit.py:616-621`）统一输出 `...Z`，本地时间只作为展示层选项，不参与工具返回值。 |

---

## 验收判据（可机检）

1. `pytest tests/test_sat_tle_gate.py -q` 全绿；变异（移除闸门）后用例 1/2/4/7 红——命令与输出留档。
2. `pytest tests/test_sat_catalog_single_source.py -q` 全绿；其中「同一时刻四入口仰角极差 < 0.05°」在收敛后成立，双源反证（> 40°）仍保留并通过。
3. 重启验证：点「更新 TLE」→ 退出 → 重开，面板 `tle_date` 必须等于更新时间（当前实测会回退到 2026-09-25）。
4. 下拉项数 = 注册表数量（8），且 AO-73 / FOX-1B / METEOR M2 / FENGYUN 3D 四个名字同时可见。
5. dead 档：跟踪按钮置灰 + tooltip 写明年龄与「更新/导入 TLE」，不存在任何可绕过按钮的入口（菜单、天空动作总线、AI 工具各验一次）。
