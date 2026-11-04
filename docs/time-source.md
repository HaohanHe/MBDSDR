# 时间源统一（谁在什么场景用哪个时间源）

本仓最少四套时间源。**同一决策链只允许一个时间源**，禁止出现「过境预测用
模拟时刻、可见性用墙钟」这类混用。本表是审计口径，新增功能按此对齐。

## 1. 四套时间源

| 时间源 | 事实源 | 默认值 | 可覆盖入口 |
|---|---|---|---|
| 系统墙钟 | `time.time()` / `datetime.now(UTC)` | 操作系统时钟（可能被 NTP 校准过） | 只在接线/测量层使用；计算链不得直接引用 |
| NTP 校正 | `time_sync.ntp_offset()`、`new_spacetime.get_ntp_time()` | 不自动注入计算链，仅报告偏差 | `get_time_info(prefer_ntp=True)` 显示用；`MBDSDR_OFFLINE=1` 时拒绝 |
| TimeEngine 模拟时刻 | `new_spacetime.get_time_engine().now_unix()` | `_sim_jd=None` → 跟随系统时钟；`rate=1.0` | `set_jd / set_utc / set_unix / set_rate / set_time_now`；进程级单例（`reset_time_engine()` 仅测试用） |
| TLE 历元 | `tle_integrity.freshness()/accuracy()`，解析 TLE 第 1 行 `epoch` | 按 TLE 自带历元判断 | 刷新 TLE（`update_tle_from_celestrak` / `fetch_tle`） |

设计事实：**NTP 校时不会写回 TimeEngine**。NTP 只报告「系统时钟 vs NTP」的
偏差与模拟时间相对真实时间的穿越量（`simulation_clock_offset()`），计算链
永远取 TimeEngine，不取 NTP。

## 2. 场景 → 时间源（审计结果）

| # | 场景 / 决策链 | 入口（默认时间源） | 显式覆盖 | 证据 |
|---|---|---|---|---|
| 1 | 过境预测（skyfield 主链） | `sat_passes.predict_passes()`：`start_time=None` → TimeEngine | `start_time`（skyfield Time）或 `start_unix`（UTC 秒） | `mbdsdr_ai/sat_passes.py` |
| 2 | 过境预测（轨道内核/工具面） | `orbit.predict_passes()` / `_predict_passes_impl()` → TimeEngine | `start_unix` | `mbdsdr_ai/orbit.py` |
| 3 | 过境预测（单星跟踪器） | `satellite/tracker.PassPredictor.predict()` → TimeEngine | `t_start` | `mbdsdr_ai/satellite/tracker.py` |
| 4 | 下一次过境倒计时 | `PassPredictor.next_pass_countdown()` 与 `sat_tracker.next_pass()` → TimeEngine | `hours` 内的 `start_unix` | 同上 / `sat_tracker.py` |
| 5 | 控制器过境分支 | `core/controller` 显式取 `get_time_engine().now_unix()` 后传 `start_unix` | 调用方注入 | `mbdsdr_ai/core/controller.py` |
| 6 | 天文面过境/可见性 | `astronomy.predict_satellite_pass()`、`unix_to_jd()` → TimeEngine | `start_time` / `unix_time` | `mbdsdr_ai/astronomy.py` |
| 7 | 卫星实时位置/可见性 | `sat_tracker.current_position()`、`is_visible()` → TimeEngine | `at_unix`（UTC 秒） | `mbdsdr_ai/sat_tracker.py` |
| 8 | 卫星可见列表/状态（orbit 链） | `orbit.compute_satellite_state()`（`when=None`）→ TimeEngine | `when`（UTC 秒） | `mbdsdr_ai/orbit.py` |
| 9 | 解码器卫星位置 | `decoders.compute_satellite_position()`、`list_visible_satellites()` → TimeEngine | `timestamp` | `mbdsdr_ai/decoders.py` |
| 10 | 多普勒补偿 | `doppler_link.refresh()` / `arm_target()` → 跟踪器 TimeEngine | `pos=` 或 `at_unix=` | `mbdsdr_ai/doppler_link.py` |
| 11 | gpredict 兼容工具 | `tool_sgp4_propagate` / `tool_doppler_calc` / `tool_sat_pass_predict` → TimeEngine | 各自的 `unix_s` / `t0` 参数 | `mbdsdr_ai/gpredict_adapter.py` |
| 12 | 天文时间信息工具 | `agent.astro_time_info` → TimeEngine，输出 `time_source` | 无（只读展示） | `mbdsdr_ai/agent.py` |
| 13 | TLE 新鲜度 / 缓存年龄 / 失败冷却 | **墙钟域**：`tle_integrity.freshness()`、`new_spacetime_tle` 缓存、`_TLE_FAIL_UNTIL`（monotonic） | 无（不参与轨道计算） | `tle_integrity.py` / `new_spacetime_tle.py` |
| 14 | NTP 授时与时钟跳变守卫 | **墙钟域**：`time_sync.ntp_offset()`、`time_basis.get_clock_guard()` | `timeout` / `server`；离线拒绝 | `time_sync.py` / `time_basis.py` |
| 15 | 超时/硬截止/耗时统计 | **墙钟域**：`time.time()`、`time.monotonic()`、`datetime.now()` | 各函数 `timeout` / `deadline` 参数 | `sat_tracker._bounded_requests_get`、网络/设备层 |
| 16 | 文件命名 / 记录 ID / 调度 `run_at` | **墙钟域**：`int(time.time())`、`datetime.now(UTC)` | 调用方显式传入 | `decoders.py` 输出名、`scheduler.py` |
| 17 | 独立天图内核 | `skyengine.jtime.TimeKernel` 默认墙钟（当前未接入计算链） | `jd` 显式赋值 | `mbdsdr_ai/skyengine/jtime.py` |
| 18 | 卫星受晒/地影判定 | `sat_illumination.evaluate_from_position/from_tle()`：`at_unix=None` → 全局 TimeEngine | `at_unix` / `time_engine` 显式 | `mbdsdr_ai/sat_illumination.py` |

墙钟域（第 13–16 行）是**合法例外**：这些值描述「真实世界已过去多久」或
「本机文件命名」，与轨道/天象计算无关，不能改用模拟时刻。

## 3. 本轮修掉的违反项（14 条，涉及 11 个生产文件）

| 违反项 | 原行为 | 修复 |
|---|---|---|
| `sat_passes.predict_passes` | 缺省 `ts.now()`（墙钟） | 缺省 TimeEngine，新增 `start_unix` |
| `sat_consistency.check_next_pass_consistency` | 透传后仍走墙钟 | 新增 `start_unix` 透传 |
| `sat_tracker.current_position` | 缺省 `ts.now()` | 缺省 TimeEngine，新增 `at_unix` |
| `sat_tracker.next_pass` | 起点为墙钟 | 新增 `start_unix` |
| `core/controller` 过境分支 | 墙钟起算 | 显式取 TimeEngine 传 `start_unix` |
| `astronomy.predict_satellite_pass` | 缺省墙钟 | 缺省 TimeEngine |
| `astronomy.unix_to_jd()` | 无参缺省墙钟 | 无参缺省 TimeEngine |
| `gpredict_adapter` 三工具 | 缺省墙钟 | 统一 `_engine_unix()` |
| `agent.astro_time_info` | 墙钟，无来源标注 | TimeEngine + `time_source` 字段 |
| `orbit.compute_satellite_state` | 缺省墙钟（连带 `visible_satellites`/`doppler_correction`） | 缺省 TimeEngine |
| `decoders.compute_satellite_position` | 缺省墙钟（连带 `list_visible_satellites`） | 缺省 TimeEngine |
| `satellite/tracker` 三处 | 缺省墙钟 | 统一 `_engine_unix()` |
| `orbit._predict_passes_impl` | 缺省墙钟 | 缺省 TimeEngine |
| `doppler_link.refresh/arm_target` | 无法钉死时刻 | 新增 `at_unix` 透传 |

## 4. 测试时钟纪律

- 测试不得把裸 `time.time()` / `datetime.now()` / `datetime.utcnow()` /
  `date.today()` 直接当被测输入；必须用显式 `start_unix` / `at_unix` /
  固定 `Time` 注入。
- 机械护栏：`tests/test_time_input_guard.py` 用 AST 扫描 `tests/` 与
  `mbdsdr_ai/tests/`，命中「裸时钟直接出现在调用实参」即红。
- 合法例外只有两类：
  1. 先赋值成本地变量（测量/接线层）；
  2. 行尾 `# timeguard: allow`，且只允许出现在与墙钟本身比较的用例
     （NTP 校时、模拟时钟偏移、TimeEngine 跟随系统时钟、调度相对未来时刻）。
     `test_no_guard_marker_abuse` 会拦截滥用。
- 时钟钉死示例：`test_sat_consistency::test_wrapper_predicts_and_checks` 传
  `start_unix=START.utc_datetime().timestamp()`；`test_sat_tracker` 传
  `_FIXED_UNIX`（内置 TLE 历元附近 2026-09-26）。

## 5. 新增功能的检查清单

1. 计算依据是「模拟/观测时刻」→ 必须取 `get_time_engine().now_unix()`，
   并提供显式 UTC 秒覆盖参数（`start_unix` / `at_unix` / `when`）。
2. 计算依据是「真实世界经过时间/文件名/超时」→ 用墙钟，并在本表第 13–16
   行补充场景。
3. 一条决策链里只允许一个时间源；跨模块传时间时传 UTC 秒（`unix`），
   不在下游各取各的 `now()`。
4. 对应测试必须注入固定时刻；跑 `tests/test_time_input_guard.py` 必须绿。
