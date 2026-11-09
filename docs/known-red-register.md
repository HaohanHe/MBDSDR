# 已知红台账（Known-Red Register）

> 目的：让"还没修"这件事**可见、可复跑、可销账**，而不是靠 `--deselect` 藏在命令行里。
> 规则：只有**实跑复现**过的节点才能进本表；不按 `.pytest_cache` 或记忆猜。
> 每条红必须带「转绿条件」，否则不许写成"已知红"。

字段说明：ID / nodeid / 首次记录 / 分类 / 症状 / 根因 / 转绿条件 / 复跑命令。
分类只有两种：**存量**（不是本轮引入）、**自引入**（本轮改动导致，必须当轮修）。

---

## K01–K04：星等门探针插件失效（4 条，存量）

**统一复跑命令**

```bash
MBDSDR_TEST_LOCK=0 QT_QPA_PLATFORM=offscreen \
  python -m pytest tests/test_planet_brightness_model.py -q -rf
```

| ID | nodeid |
|---|---|
| K01 | `tests/test_planet_brightness_model.py::TestPickGateMatchesDrawGate::test_faint_star_inside_limit_is_pickable` |
| K02 | `tests/test_planet_brightness_model.py::TestStarExtinctionKeepsDrawAndPickInSync::test_faint_star_extinguished_near_horizon_draws_nothing` |
| K03 | `tests/test_planet_brightness_model.py::TestStarExtinctionKeepsDrawAndPickInSync::test_same_star_high_up_draws_and_is_pickable` |
| K04 | `tests/test_planet_brightness_model.py::TestStarExtinctionKeepsDrawAndPickInSync::test_visible_star_near_horizon_stays_pickable` |

- **首次记录**：2026-11-09（`51c01a57` 之后实跑确认；更早的 11-08 记录里这几条被笼统写成"7 条存量红"，没点名）
- **分类**：存量。**不是产品缺陷**——拾取返回 `HYG104742` / `Capella` 是真实存在的星，方向正确。
- **症状**：断言期望 `PickProbeStar`（合成探针），实际拿到真星表里的星：
  - K03 → `HYG104742`（RA 319.31°, Dec 55.80°, vmag 6.0）
  - K04 → `Capella`（RA 79.17°, Dec 46.00°, vmag 0.08）
  - K02 → 期望"近地平不画"，实测画出 25 个亮像素（真星被画了）
- **根因**（已核到行号）：
  1. 探针注入用 `monkeypatch.setattr(rf_sky_view._constel, "merged_star_index", ...)`
     （`tests/test_planet_brightness_model.py:244`、`:348`）；
  2. 但 `RFSkyView._star_selection()`
     （`desktop/rf_sky_view.py:4175`）走的是
     `mbdsdr_ai.skyengine.star_catalog.select_for_view(...)`，
     与 `merged_star_index` **完全不在同一条链上**——这个 monkeypatch 从写出来
     那天起就是空操作；
  3. `_new_view()` 的 `show()` + `processEvents` 已用真星表算过一次
     `self._star_sel` 并缓存；绘制层与拾取层共用这份缓存（这正是设计意图），
     所以两层都读真星。
  4. 2026-11-09 已尝试清缓存（`v._star_sel = None`，两处），**必要但不充分**：
     清掉后重算仍读真星表，四条依旧红。该行保留并注明了原因。
- **转绿条件**（任一条即可）：
  - 把 monkeypatch 目标改成 `mbdsdr_ai.skyengine.star_catalog.select_for_view`，
    返回只含探针星的 `StarSelection`；或
  - 直接 patch 实例方法 `v._star_selection`，返回同样的 fake；
  - 并在 patch 后清 `v._star_sel` / `v._star_sel_key`，确保缓存不复活旧集合。
  - 验收：`--runxfail` 后四条转绿；不再出现 `HYG104742` / `Capella`。
- **为什么现在不修**：修它要构造完整的 `StarSelection`（19 个 numpy 字段）并重新
  核对 4 条断言里的像素门限，属于独立一小批；本轮主线是地点面板，按用户决定
  先点名 + `xfail(strict=True)`，防止"半修当全修"。
- **锁定方式**：四条已加 `@pytest.mark.xfail(strict=True, reason="KNOWN-RED-K01…K04: ...")`。
  修好后 strict 会把它变成 `XPASS` → 红，强制销账删标记。

---

## 维护约定

1. 新增红：先实跑拿 nodeid，再进表，再加 `xfail(strict=True, reason="KNOWN-RED-Kxx: …")`。
2. 修好：删 `xfail` 标记 + 在本表对应行写"已销账 + 提交 hash"，不留"曾经红过"的模糊表述。
3. 账目不许膨胀：每周检查一次条目数，只增不减说明没人修。
4. 不许用 `--deselect` 替代本表。命令行里排除的东西，必须在表里有对应行。

---

## K05 — desktop 全量确定性红（2026-11-09 16:09–17:10 权威复跑）

**本节取代上午那版 K05，上午的结论作废。** 上午用「日志点号序号 →
collect 清单」映射 nodeid 得到 15 条，并判成「单跑全绿 = 顺序依赖假红」。
那套映射是错的：collect 顺序随代码变动整体偏移，跑的根本不是真失败的
用例，所以 `15 passed` 证明不了任何事。**以后一律从 pytest `-rf` 汇总的
`FAILED ...` 行取 nodeid，不许用点号序号猜。**

### 权威结果（汇总行完整、数字可信）

```
python3 tools/evidence/test_suites.py --suite desktop --desktop-timeout 5400
→ 20 failed, 5553 passed, 4 skipped, 5 xfailed, 6 subtests in 2269.29s (37:49)
   exit 1 · counts_trustworthy=True · FAIL
```
日志 `output/evidence/bundle_desktop/test_suites/desktop.log`。
`git diff 60271ce3..ebefda93 --stat` 显示另一会话只改了 `web/product/`
的星表数据，没碰 `desktop/` 或 `tests/` —— 不是 astro 改动带出来的，
也不是 Qt 争用。

### 分类：20 条放进一个无并发进程复跑 → 18 failed / 2 passed

**顺序依赖 2 条**（单跑转绿）：
`test_record_replay_config::test_start_recording_stops_replay_first`、
`test_settings_corruption_recovery::test_two_processes_can_write_concurrently_without_losing_fields`。

**确定性真红 18 条**（单跑仍红），按根因分三组：

| 组 | 用例 | 症状 / 转绿方向 |
|---|---|---|
| A 契约基线 | `test_settings_ui_coverage::test_inventory_table_matches_script` | 数字表与脚本漂移 → 官方 `settings_inventory.py --write`（本轮处理） |
| A | `test_typing_annotation_guard` | `scripts/ui_interaction_audit.py` 漏导 `Optional`（CI 3.12 会 NameError）→ 补 import（本轮处理） |
| A | `test_time_input_guard` | `test_first_open_welcome.py:137/162` 裸时钟 → 白名单补该文件 + `# timeguard: allow`（本轮处理） |
| A | `test_ui_interaction_guard` | 按钮 ⧉ 触发 `MainWindow._return_all_panels` AttributeError；真方法名 `_return_all_panes`（多一个 l）→ 改名（本轮处理，AST 静态核验） |
| A | `test_source_pin_ratchet::test_no_new_source_text_pins` | 5 条存量新增 pin（`test_config_persistence_safety` / `test_iq_modulation_consistency` / `test_pipeline_iq_bus_wiring`×2 / `test_rtltcp_honest_write`）。基线只减不增，须改成行为断言 |
| A | `test_swallow_guard::test_repo_ratchet_within_baseline` | `desktop/adsb_map_panel.py` WARN 4 > 基线 1，空 `pass` 来自 `692fb3ce`（11-08）。补实现或登记理由 |
| A | `test_settings_ui_coverage::test_forward_every_field_has_ui_entry_or_reason` | `record_dir_inside_program_ack` 无 UI 入口/理由；该键由运行时对话框写入，需定夺补设置控件还是写 `INTERNAL_REASONS` |
| A | `test_help_path_truthfulness::test_doc_render_points_still_exist` | `desktop/ai_panel.py:584` 已不是渲染点，现落在 696/698/699/701/1159/1201 → 更新文档第三节渲染点行号 |
| B 文案契约 | `test_tooltip_guard` / `test_no_device_onboarding` | 录制采样率 tooltip 文案未落地；无设备横幅改成「还没接 SDR · 插上设备点连接」后不再直说「没找到设备」 |
| C 产品行为 | `test_headless_smoke`（98300000 vs 98500000）、`test_material_task_chains`（`doppler_to_frequency` 参数不合法）、`test_landscape_alpha`（assert 0 > 0）、`test_sky_dso_layer`（状态卡缺「本视图无银河图层」）、`test_sky_dual_loops`（156 != 156）、`test_sky_sun`（'恒星' == 'Star'）、`test_sky_sunlit_state`×2（受晒行应为「—」） | 需逐条判断是产品行为改变该更新断言，还是产品回归；不许直接改断言刷绿 |

另处理一条**本线自己引入**的 ratchet 噪声：`tests/test_station_sync.py` 的
三个键名字面量被扫描器当成源码文本 pin，收敛成模块级常量 + 集合交集后
该文件的 pin 归零；但 `test_source_pin_ratchet` 仍因上面 5 条存量而红。

### 验证与口径

- 本轮处理 4 条全量红，`typing` / `timeguard` / `inventory 表` /
  `station_sync` 与 `test_first_open_welcome` 已按文件复跑通过
  （85 passed 批次 6 passed 单文件）；`ui_interaction_guard` 单进程会挂
  （6 分钟无输出，主动停止），用 AST 核验方法名与定义，未单独复跑。
- **37 分钟全量没有重跑**，不宣称修复后的新数字。
- 对外口径：`tests/` 全量 = `20 failed / 5553 passed`（修复前快照，带完整
  汇总行）；kernel 单组 = `2827 passed / 0 failed`。**不得说全绿。**

---

## K06 — 静默吞噬棘轮基线越线（2026-11-09 18:20 实测，存量·未修）

**统一复跑命令**

```bash
python -m pytest tests/test_swallow_guard.py::test_repo_ratchet_within_baseline --runxfail -q
python -m pytest tests/test_swallow_guard.py -q -rf
```

- **分类**：存量，**113 处一处没修**。不是环境/并发假红。
- **症状**：`docs/code-guards-baseline.json` 是 2026-11-06 的快照
  （`total=1284` / 245 文件）；本轮实测 `total=1397`，40+ 文件越线，含
  `desktop/main_window.py` ERROR 239>227 / WARN 107>96、
  `mbdsdr_ai/agent.py` ERROR 14>11 / WARN 47>40，以及守卫建表之后新增的
  文件（`capability_drawer.py` 6 WARN、`local_agent_bridge.py` 7 WARN、
  `location_panel.py` 4 WARN、`time_machine.py` 2 WARN、`session_log.py`
  5 WARN、`web_bridge_mcp.py` 2 WARN 等）被判「基线中没有该文件」。
- **根因**：守卫按「新文件出现静默 catch-all 即红」设计；11-06 之后仓库
  持续新增 UI/agent 代码，其中确有 `except Exception: pass` 与无记录降级。
  **这些是真实的静默吞噬，不是误报**——按项目 P13 口径属于待修缺陷。
- **为什么不重定标**：`check_ratchet` 与测试注释明令「不允许修改
  `docs/code-guards-baseline.json` 来抬基线」。本轮既不抬基线，也不放宽
  断言、不删用例；把 113 处变成绿等于把未完成说成完成。
- **转绿条件**（须逐处做完，再删 `xfail` 标记）：每处要么补日志/状态字段
  让扫描器判 `RECORDED`，要么证明是外部边界后按白名单登记
  `reason/user_visible/test/trigger/review_by`；新文件全部降到 0 或登记。
  验收：`--runxfail` 后该条转绿，且 `total` 只减不增。
- **锁定方式**：`@pytest.mark.xfail(strict=True, reason="KNOWN-RED-K06: …")`。
  修好后 strict 会变 `XPASS` → 红，强制销账删标记。
- **诚实边界**：对外说「desktop 全量」时必须同时说明 K06 的 113 处存量
  静默吞噬**未修**，只是按已知红登记；`xfailed` 不等于绿。

---

## K05 收口后的销账记录（2026-11-09 18:30）

本轮把 K05 的 20 条里 **18 条真修/自然销账**，逐条依据见提交信息与
`output/goal_completion_audit.md`：

| 组 | 处理 |
|---|---|
| 产品真 bug（4） | 扫频结束还原设备中心频率（controller.py）；`range_rate_mps` 负值豁免（tool_registry.py）；太阳卡 `kind_label` 回归英文 `Star`；Windows 并发写锁 `open` 重试降级（settings.py） |
| 测试装置漂移（4） | 地景探针把视轴压到地平线；状态卡像素裁剪按逐行实测基线重算（银河 208 / 视角 154 / 调谐 172 / 受晒 190），断言表达式一字未改 |
| 文案契约（2） | tooltip 追平产品 4 处（MS/s 单位、Caster、频率控件）；无设备横幅按「短句在横幅 + 三段式在 tooltip」的页面极简分层重写断言 |
| 契约基线（5） | 5 条源码文本 pin 改成行为断言（键集合、code object 全局引用、真实帧入口发布/喂帧、假 client 下发失败）；文档渲染点行号按实测重钉；`record_dir_inside_program_ack` 写明无 UI 入口的理由并重生成盘点表 |
| 前轮落盘（4） | typing / timeguard / inventory 表 / `_return_all_panes` 菜单拼写 |
| 顺序依赖（1） | `test_start_recording_stops_replay_first` 隔离复跑自然转绿 |
| 未修转登记（2） | K06 静默吞噬棘轮 113 处（xfail 锁定，一处没修）；K07 UI 交互守卫 9 个假活按钮（xfail 锁定；挂起根因已真修） |

**验证**：无并发进程下实跑的轻量批次 `30 passed`（产品/测试装置）、
`13 passed + 1 随后重生成`（文案/文档/盘点）、`14 passed`（pin 退役）、
`3 passed / 1 xfailed`（UI 守卫全文件，28.84s 从挂死变成跑完）。
**第一次 35:54 全量重跑**：`1 failed, 5571 passed, 4 skipped, 7 xfailed,
6 subtests`，`counts_trustworthy=True`，日志
`output/evidence/bundle_desktop_rerun/test_suites/desktop.log`；
唯一剩余节点是跨线程竞态（见 K08），K06/K07 以 `xfailed` 计入，**不算绿**。
此后又修了 K08（`11 passed` 局部复验）。**第二次 35:35 全量**：
`2 failed, 5571 passed, 4 skipped, 7 xfailed, 6 subtests`，
`counts_trustworthy=True`；K08 节点转绿，但冒出两条墙钟时间依赖（K09）。
`bundle_desktop_final/test_suites/desktop.log` 是**修复 K09 之前**的权威
快照；K09 局部 `4 passed`，第三次全量待跑，不拿旧数字冒充新结果。

---

## K08 — 录音/回放跨线程竞态（2026-11-09 20:00，真 bug·本轮修）

- **首次记录**：2026-11-09 全量重跑的 `-rf` 汇总：
  `FAILED tests/test_record_replay_config.py::test_start_recording_stops_replay_first`
  （`1 failed / 5571 passed` 里的唯一一条）。K05 曾把它归为「顺序依赖、
  单跑转绿」，那是**没找到竞态窗口**时的粗判，本节取代该结论。
- **症状**：`assert (False is True)`（`window._recording`），日志伴随
  `录音中止：录音流句柄丢失（本段未成功打开）（已写 0 字节 …）`。
  单跑、整文件跑都绿，只在 5571 例固定顺序里红。
- **根因**（`desktop/main_window.py`，改动前）：`_start_recording()` 先把
  `self._recording = True`，随后才调 `_open_record_segment()` 建文件句柄；
  而 `_on_pipeline_record_iq` / `_on_pipeline_frame_ready` 的写者来自
  **独立 RecordTap 线程**，会撞进「已开录但句柄仍是 None」的窗口，
  被 `_record_write_chunk` 判成句柄丢失 → `_report_record_abort` →
  `_stop_recording()` 把整场状态复位。线程调度决定是否撞上，所以
  单跑不复现。
- **处置**（改产品，不动断言）：
  ① `_open_record_segment()` 成功之后才置 `_recording=True` 并同步天空层，
  让写者看到的「录音态」与「句柄就绪」不再有缝；
  ② `_start_recording()` 开头清「录音态真、句柄为空」的半开残骸；
  ③ 测试加 autouse 装置隔离（module-scope 窗口跨用例复用，前序会留
  定时器/回放状态），**所有断言一字未改**；
  ④ 新增反证 `test_start_recording_recovers_from_half_open_state`，
  手工构造半开态后开录必须成功。
- **验证**：`tests/test_record_replay_config.py` + `test_headless_smoke.py`
  → `11 passed`（12.46s）；加装置隔离后该竞态在局部可稳定复现，修前
  `1 failed / 10 passed`、修后 `11 passed`，反证成立。
- **转绿条件**：全量固定顺序重跑该节点转绿，且汇总行 `0 failed`。
  第二次 35:35 全量里该节点**已转绿**（两条红换成 K09），K08 销账。

---

## K09 — 过境预测的墙钟窗口漂移（2026-11-09 21:05，测试装置·本轮修）

- **首次记录**：第二次全量 `-rf` 汇总：
  `FAILED tests/test_e2e_operation_scenarios.py::test_scenario_a_pass_to_archive`
  与 `::test_scenario_c_plan_and_degrade`
  （`2 failed, 5571 passed, 4 skipped, 7 xfailed in 2135.46s`）。
- **症状**：`报告里没有目标过境的中天（20:39:57±3s）`；工具报告里
  「预测起点」是 `20:40:01Z / 20:40:08Z`（本地），只剩后续过境。
- **根因**：`tests/test_e2e_operation_scenarios.py:94` 的 `pass_case` 是
  **module scope**，在本文件第一次用到时缓存了一份「当前起 24h」的
  NOAA 19 过境（中天 20:39:57）；场景 A/C 在 25 分钟后才调
  `sdr_satellite_passes`，工具从**那时的墙钟**重新起算 24h，那场已经
  滑出窗口。第一次全量在 19:17 跑到该用例，间隔短，所以绿。
  **不是产品回归**，是测试装置把墙钟相对量缓存成了会话级常量。
- **处置**：`pass_case` 改为 function scope（每例重算 24h 预测，约 0.2s），
  并在 docstring 写明为什么不能缓存；`_isolate_global_time` 的 autouse
  复位保持不变。**断言一字未改**。
- **验证**：`tests/test_e2e_operation_scenarios.py` → `4 passed`（13.42s），
  运行时已是 21:1x，与失败时的 20:40 不同时段，窗口错开场景覆盖到。
- **诚实边界**：修在第二次全量**之后**，局部验证不等于全量验证。
  第三次全量固定顺序重跑才是当前代码的权威数字；本轮不再追逐更多轮次，
  若仍红就逐条登记、不循环刷跑。

---

## K07 — UI 交互守卫的假活清单（2026-11-09 19:00，存量·部分修）

**统一复跑命令**

```bash
MBDSDR_TEST_LOCK=0 QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_ui_interaction_guard.py -q -rf --runxfail
```

### 已真修：守卫挂死（本轮 P0）

`main_window.py:3679` 的 `menu.exec(QPoint)` 在 offscreen 下进模态事件循环
永不返回：faulthandler 抓栈于 `「按钮 ⧉」` 后 6 分钟零输出，守卫等于没跑完。
`QMenu.exec` 是 Shiboken 重载，Python 侧给 `QMenu.exec` 赋打桩函数拦不住
（实测无效，已撤销那段假桩）。**产品侧改成非模态 `menu.popup(同位置)`**：
同样弹出、同样触发 action、返回值本就没用。修后全文件 28.84s 跑完。
反证：`test_float_menu_is_nonmodal` 钉住 popup 必须被调用。

### 未修：9 个按钮被判「假活」

```
按钮 ⧉ @ MainWindow
按钮 本后端发射：未连接设备（能力未知） @ MainWindow
按钮 更改地点… @ newSpacetimePanel
按钮 刷新过境 @ newSpacetimePanel
按钮 ● 模型未配置·本地协商 · 工具 496/496 · 任务 0 · 事件 0  ▾ @ AIPanel
按钮 📍 地点 @ MainWindow
按钮 ⤢ / ⤶ @ floatingPanel
按钮 回到现在 @ 对话框:时间机器
```

- **分类**：存量（本轮新增 UI 与既有异步动作混合），**9 处未修**。
- **症状**：`receivers()` 非 0，但审计真触发后 `_diff` 看不到可观察变化
  （状态栏/对话框/窗口/信号）。其中一部分可能是**幂等动作**（如
  「回到现在」当前已在现在）或**异步刷新**（刷新过境），另一部分可能是
  本线新增 UI 的「点了没反应」——用户红线明确禁止假开关，**不许把清单
  整体塞进白名单了事**。
- **根因方向**：逐条读槽实现；真没反馈的补产品反馈（状态栏/对话框/可见
  状态变化），确实是幂等/异步的才按 `FALSE_ALIVE_WHITELIST` 写明理由。
- **转绿条件**：9 条逐条判定并落地后 `--runxfail` 转绿；删 `xfail` 标记。
- **锁定方式**：`@pytest.mark.xfail(strict=True, reason="KNOWN-RED-K07: …")`。
- **诚实边界**：`test_ui_interaction_guard.py` 现在是
  `3 passed / 1 xfailed`，**不是全绿**；守卫覆盖仍在（能跑完、能报清单）。

