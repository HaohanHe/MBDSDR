# MBDSDR 离线行为清单（MBDSDR_OFFLINE 全链路审计）

> 审计日期：2026-11-04 · 范围：Python 侧（`mbdsdr_ai/`，不含 `desktop/` 渲染层）
> 复现命令：`MBDSDR_OFFLINE=1 python -m pytest mbdsdr_ai/tests/test_offline_behavior_audit.py -q`

## 判定口径

`MBDSDR_OFFLINE=1`（也接受 `true/yes/on`，大小写不敏感）时，每个会对外联网的功能必须二选一：

- **A 本地可用**：有本地数据（缓存/内置/静态表），照常返回并**标注来源与时效**（离线标记、TLE 历元新鲜度、历表精度、时间源）；
- **B 明确拒绝**：不联网、不返回假数据，错误信封写明「离线 + 缺什么 + 还能用什么」。

禁止第三类行为：静默返回空 / 假装成功（`success=True` 但数据是错的/空的）/ 无限重试 / 无截止阻塞（DNS 黑洞挂死）。
本清单的每条都在强制离线 + 「网络桩一被调用就失败」的条件下有自动化测试。

## 一、互联网功能（自动发起）

| # | 功能 | 入口 | 离线行为 | 数据时效 / 缺什么 | 证据（测试） |
|---|------|------|----------|-------------------|--------------|
| 1 | 单星 TLE 拉取 | `tle_fetch.TleFetcher.fetch_record` → `orbit.fetch_tle_record` | **A** 有缓存即用，`offline=True`、`offline_label="离线·缓存"`、`refresh_status="cache"`；**B** 无缓存抛 `TLEUnavailableError` | 缓存写盘时间 + TLE 历元：`freshness_tier` 为 fresh/usable/expired/**dead**；`dead` 与 `pass_eligible=False` 明示不可精确过境 | `test_tle_fetch_lifecycle.py`（离线 2 条）、`test_offline_behavior_audit.py::test_offline_tle_single_*` |
| 2 | 群组 TLE 拉取（`GROUP=...`） | `new_spacetime_tle.TLEManager.fetch_group` → `TleFetcher.fetch_group` | **B** 明确拒绝，`last_group_error` 写清「离线 + 群组无本地缓存」，单星请走缓存路径 | 群组无缓存索引（设计如此） | `test_tle_fetch_lifecycle.py` 群组 6 条、`test_offline_tle_group_*` |
| 3 | 内置卫星目录刷新（Celestrak active.txt） | `sat_tracker.SatelliteTracker.update_tle_from_celestrak` | **B** 不联网，`last_refresh_error="离线模式…未联网刷新（沿用旧 TLE）"` | 沿用内置真实 TLE，`BUILTIN_TLE_DATE=2026-09-25`，过期后条目会标 dead；在线刷新有「连接/读 + 墙钟硬截止（含 DNS）」 | `tests/test_sat_tracker.py`、`test_offline_sat_tracker_refresh_keeps_builtin_tle` |
| 4 | 高精度星历 `de421.bsp` | `solar_system.download_de421` / `_EphemerisBackend` | **A** 本地 bsp 存在直接复用；**B** 无 bsp 时不启动下载线程，`last_download_error` 写明缺 `de421.bsp` | 回退本地 VSOP87：太阳 ~1″、月球 ~10″（`backend_name="vsop87_astropy[回退]"` + `precision_note`）；bsp 为亚角秒 | `test_offline_ephemeris_*`（3 条） |
| 5 | 模型列表 `GET /models` | `model_manager.ModelManager.fetch_models` | **A** 返回内置模型列表，`_last_error` 标注「离线 + 使用内置列表」，`/models` 命令会显示该注 | 内置列表静态、无时效问题 | `test_offline_model_list_uses_builtin_and_labels` |
| 6 | 模型 Chat / 流式 Chat | `model_manager.chat` / `chat_stream` | **B** 立即拒绝：`success=False`、`offline=True`，只记 1 次调用，不进入重试循环 | 本地无模型可替代 | `test_offline_chat_refuses_without_retry`、`test_offline_chat_stream_refuses` |
| 7 | ISS 实时位置 | `openapi_integration.get_iss_position` | **B** `success=False, offline=True` + 说明 | open-notify 无本地缓存 | `test_offline_openapi_features_refuse` |
| 8 | 在轨人数 | `openapi_integration.get_people_in_space` | **B** 同上 | 同上 | 同上 |
| 9 | 实时天气 | `openapi_integration.get_weather` | **B** 同上 | open-meteo 无本地缓存 | 同上 |
| 10 | 附近飞机 | `openapi_integration.get_aircraft_nearby` | **B** 同上 | OpenSky 无本地缓存 | 同上 |
| 11 | NTP 授时 | `time_sync.ntp_offset` / `new_spacetime.get_ntp_time` / `new_spacetime_timeline` | **B** 拒绝：`reachable=False, offline=True` 或抛 `NTPError`；上层回退系统时钟并标 `source="system"` / `synchronized=False` | 时间源明确标注「未校准」；Agent 的 `time_sync_ntp` 工具不再把不可达包成 `success=True` | `test_offline_ntp_offset_refuses`、`test_offline_new_spacetime_ntp_raises_explicitly`、`test_offline_time_info_falls_back_to_system_clock`、`test_offline_ntp_tool_reports_failure` |
| 12 | `web_fetch_url`（Agent 联网取资料） | `agent.MBDSDRAgent._register_web_tools` | **B** 拒绝；在线路径改为硬截止（15s 请求 + 20s 墙钟，覆盖 DNS） | 无本地缓存可降级 | `test_offline_web_fetch_tool_refuses` |
| 13 | `git_clone_repo` | 同上 | **B** 拒绝，提示工作区 `repos/` 已有仓库可直接用 | 无本地副本时缺源码 | `test_offline_git_clone_tool_refuses` |
| 14 | 地图瓦片 | Python 侧**无瓦片请求**（瓦片渲染由 `desktop/` 图层负责，不在本次 Python 审计范围） | N/A | — | 全仓 `mbdsdr_ai/` 无 tile/wmts/osm/mapbox URL |

## 二、本机 / 局域网设备链路（不受 MBDSDR_OFFLINE 约束）

`MBDSDR_OFFLINE` 的语义是「公网离线」，以下链路由用户显式配置且指向本机/局域网/自建服务，**不拦截**；断链时各自返回连接错误（均带 timeout）：

| 功能 | 入口 | 说明 |
|------|------|------|
| RTL-TCP | `rtltcp_client.py` | 用户填 host:port，`create_connection` 带 timeout |
| SpyServer | `spyserver_client.py` | 同上 |
| ESP32 / 板载 SDR（WebSocket） | `sdr_backend.py` | 局域网 WebSocket，timeout 3s |
| 云台（TCP / 板载通道） | `gimbal.py` | 用户配置的 host:port |
| GNSS 接收机（TCP） | `serial_gnss.py` | 设备链路 |
| NTRIP 差分源 | `rtklib_adapter.py` | 用户指定的 caster |
| MCP 服务器 | `mcp_client.py` | 用户配置（默认本机） |
| 本地 Web / 手机端 / 远程控制服务 | `web/server.py`、`mobile_server.py`、`remote_control*.py` | 入站服务，离线时仍可本机访问 |

## 三、本次审计发现并修复的违反项（9 项）

| # | 违反项（修前） | 类别 | 修法 |
|---|----------------|------|------|
| 1 | `web_fetch_url` 离线仍联网、DNS 阶段无硬截止 | B 缺失 + 阻塞 | 加离线门；`_bounded_urlopen`（20s 墙钟） |
| 2 | `git_clone_repo` 离线仍执行 git clone | B 缺失 | 加离线门（在线仍有 120s timeout） |
| 3 | `time_sync_ntp` 工具把不可达包成 `success=True` | 假装成功 | `reachable=False` 时返回 `ToolResult(False, …)` |
| 4 | `fetch_models` 离线仍请求 /models，且静默回退内置列表 | B 缺失 + 静默 | 离线直接返回内置列表并在 `_last_error`/`/models` 标注 |
| 5 | `chat` / `chat_stream` 离线仍按 `max_retries` 重试 | 无限(有界)重试 | 离线立即拒绝，只计 1 次失败 |
| 6 | ISS / 太空人 / 天气 / 飞机 4 个 API 无离线门（DNS 可挂） | B 缺失 | 统一 `_offline_refusal()` 信封 |
| 7 | `de421.bsp` 离线仍启动后台下载线程 | B 缺失 + 阻塞 | 离线不启动线程，写 `last_download_error`，保留本地回退 |
| 8 | NTP（`time_sync` / `new_spacetime`）无离线门 | B 缺失 | 离线返回明确错误；上层回退系统时钟并标注来源 |
| 9 | `new_spacetime_tle._http_get` 超时不覆盖 DNS（可能挂死） | 无截止阻塞 | 改走 `orbit._bounded_urlopen`（请求超时 + 墙钟硬截止） |

另有 P12 自登记两缺口同批收口：`TleFetcher.fetch_group` 接入同源最小间隔 + 指数退避 + 健康度（`GROUP_SCOPE_KEY` 与单星共用账本）；`sat_tracker` 刷新改为 `(connect, read)` 元组 + daemon 线程 join 硬截止，DNS 卡死也能在上限内返回明确「超时」。
