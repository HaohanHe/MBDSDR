# 已知红台账（Known-Red Register）

> 目的：让"还没修"这件事**可见、可复跑、可销账**，而不是靠 `--deselect` 藏在命令行里。
> 规则：只有**实跑复现**过的节点才能进本表；不按 `.pytest_cache` 或记忆猜。
> 每条红必须带「转绿条件」，否则不许写成"已知红"。
> 状态口径：写「已修」必须带一次**可复跑的验证记录**（命令 + 实际输出）；
> 没有记录的只能写「实跑绿·待反证」，不得直接销账。

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
- **2026-11-13 复核**（HEAD `99a61044`，独立 worktree，`--runxfail` 单跑）：
  `total=1626 > 基线 1284（新增 342）`，仍红、仍未改基线；上面 `1397` 是
  2026-11-09 的读数，结论（未修、不抬基线）不变。

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
快照；K09 局部 `4 passed`。**K09 修复后的最终全量数字见 K09 节：
`5573 passed / 0 failed`（`bundle_desktop_final2`）。**

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
- **第三次全量（35:41，K09 修复后）**：`5573 passed, 4 skipped,
  7 xfailed, 6 subtests`，**0 failed**，exit 0，`counts_trustworthy=True`，
  日志 `bundle_desktop_final2/test_suites/desktop.log`。K09 节点转绿并销账；
  **这是当前代码 `fbd27f9f` 的唯一权威全量数字**。
- **诚实边界**：`7 xfailed` 里 K06（113 处静默吞噬）与 K07（九个假活
  按钮）一处没修，只是按已知红登记；**全量 PASS 不等于没有欠账**。
  kernel 组本轮未重跑（上次 `2827 passed` 属改动前），真机 RTL2832U /
  GNSS 不在位、硬件项由套件自己 skip，均继续 `not_verified`。

---

## K07 — UI 交互守卫的假活清单（2026-11-09 19:00 立；23:50 全额销账）

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
- **锁定方式**：曾用 `@pytest.mark.xfail(strict=True, reason="KNOWN-RED-K07: …")`。

### 2026-11-09 23:50 销账（9 条全清，xfail 已删）

真机插上后重跑守卫，先炸出**三处运行时崩溃**（比假活严重）：
ControlPanel 的 `−` / `+` / 「收藏当前频率」在「后端连接成功但没回读到频率」
时抛 `TypeError: NoneType + float`。根因是产品把「连接成功」当成「频率真值已知」。

**产品修复（6 处，全部真修）：**

1. `control_panel._sync_freq_actions_enabled()`：新增。`_freq_hz is None` 时
   ±步进/收藏保持置灰（tooltip 写明原因），`set_frequency_hz`/`_set_freq_hz`
   一拿到真值立刻放开。`set_sdr_connected` 末尾统一收口。
2. `_apply_step` / `_on_bookmark_add` 加 None 守卫（内部调用也不崩）。
3. `main_window._open_location_panel`：面板已开时补状态栏回执（幂等动作）。
4. `main_window._return_panel`：`info is None` 时提示「已在原位」；成功归位
   提示「已归位「X」」。
5. `main_window._hide_capability_drawer`：区分「已收起」与「本来就未打开」。
6. `time_machine._go_now`：区分「已在现在」与「已回到现在」。

**审计观察面补全（4 处，不是放宽标准）：**

| 补的维度 | 此前误判的按钮 |
|---|---|
| `QLineEdit.styleSheet` | 调谐（空输入变红是唯一反馈） |
| `QLabel.visible` | AI 状态条 ▾（只切一个 label 的可见性） |
| 点击后新出现的可见顶层窗口（`evidence["new_top"]` 此前**只读不写**） | 📍 地点（`QFrame(Qt.Tool)` 不是 QDialog）、更改地点…、⧉ 菜单、⤢ detach |
| 点击瞬间快照 | 刷新过境（后台线程太快，「更新中…」被下一轮事件循环撤掉） |

**反证（防止扩大观察面 = 放宽标准）：**
`test_probe_still_catches_noop_button` —— 注入一个接了槽但什么都不做的按钮，
断言它仍被判「无变化」：不弹窗、不写状态栏、不开对话框、无界面变化。

**结果**：`tests/test_ui_interaction_guard.py` → **5 passed**（无 xfail、无失败）。
置灰的 ± 步进/收藏登记进 `DISABLED_REASONS`，写明「拿到频率真值即启用」。

- **诚实边界**：销账的是「守卫能看见的假活」。守卫看不见的（比如状态变化
  只发生在别的进程/网络侧）不在覆盖内；异步按钮的中间态靠「点击瞬间快照」
  兜，若某按钮的反馈延迟超过一轮事件循环仍需单独复核。


---

## K10 — 真机 rtl_tcp 路径的两条能力告警（2026-11-09 22:15，存量·未修）

真机（RTL2832U + FC0012）经 `rtl_tcp 127.0.0.1:1234` 接入 GUI 时，连接本身
成功、频谱正常，但日志/状态栏出现两句：

```
连接后参数 Offset Tuning 下发异常: AttributeError:
    'RtlTcpBackend' object has no attribute 'set_offset_tuning'
连接后参数未全部生效: Offset Tuning
listGains 失败（设备可能不支持分档）: 'SDRDevice' object has no attribute 'listGains'
```

- **判定**：误报措辞，不是链路失败。rtl_tcp 协议本身没有 offset tuning
  命令（`RtlTcpDeviceAdapter.set_offset_tuning` 是显式 no-op），SoapySDR
  分档增益本机未安装。
- **为什么没修**：本轮不改产品代码以保住 `f9c3bb92` 的权威全量数字
  （`5573 passed / 0 failed`）。改 `RtlTcpBackend` 会让那个数字失效。
- **修法（下轮）**：`RtlTcpBackend` 补 `set_offset_tuning` 委托到 adapter
  并返回「协议不支持」，或让 `_connect_backend` 用能力探测跳过；
  `gain_panel` 的 `listGains` 探测前先 `hasattr`。
- **诚实边界**：真机 GUI 链路可用（帧推进 6575→7115、`finite_ratio=1.0`），
  但这两条告警会误导用户以为参数没生效。
- **为什么没有 nodeid**：K10 描述的是真机 GUI 连接后的日志/状态栏现象，不是断言；
  仓库没有对应的 pytest 节点，`RtlTcpBackend`（`mbdsdr_ai/rtltcp_client.py:655`）
  只在真机连接路径触发。
- **现在能否机检**：**否**。需要 RTL2832U + FC0012 真机与 `rtl_tcp` 服务；离线侧
  只能另立静态断言（`hasattr(RtlTcpBackend, "set_offset_tuning")`、`gain_panel`
  的 `listGains` 探测前 `hasattr`），但本条目记录的两句运行期告警仍无法离线复现
  ——静态断言即使建立也不等于本条目可机检。
- **人工复核（谁/多久/看什么）**：硬件验收线（`scripts/hw_acceptance/*`；串口入口
  `scripts/real_hw_acceptance.py` 当前 claim=打包线），每个真机验收批次 + 发版前
  各 1 次，连真机后看日志是否仍出现上面两句；出现即条目保持打开。

---

## K11 — R4 `playback_audio` 判据擦线（2026-11-09 22:20，判据缺陷·未改）

七环 R4 的判据是「文件回放 RMS / 实时 RMS ∈ (0.3, 3.0)」。真机实测三次
分别是 **0.28 / 0.33 / 0.61** —— 0.28 那次判 FAIL。

- **判定**：判据设计缺陷，不是产品回归。它比的是**两个不同时间窗**的 RMS
  （录完的 IQ 解调 vs 当前实时音频），受电台调制内容/静音段影响。
- **为什么没改阈值**：把 0.3 调成 0.2 就是拿标准换绿字，本轮不做。
- **修法（下轮）**：改成同一段 IQ 的「文件回放 vs 内存直解」对拍，
  消掉时间窗变量；或判据改为「回放 RMS 有能量且与实时同量级」并写明依据。
- **诚实边界**：R4 在其他子项全绿（流式增长、完整性、改速跟随）。本轮
  单跑 `PASS`、全跑曾 `FAIL`，属判据抖动。
- **为什么没有 nodeid**：实现在 `scripts/hw_acceptance/ring4_recording_playback.py`；
  需要真机 SDR 录制 + 声卡回放，不是 pytest 节点。
- **现在能否机检**：**否**。判据比较的是两个不同时间窗的 RMS，离线无射频/音频
  输入无法复现，接 CI 只会得到环境噪声。
- **人工复核（谁/多久/看什么）**：硬件验收线（该脚本无活跃 claim），每批真机验收
  跑一次 `python scripts/hw_acceptance/ring4_recording_playback.py --freq <Hz>`，
  看 `playback_ratio` 是否仍擦线（0.3 附近）；发版前至少 1 次。

---

## K12 — 门限边缘掠射过境漏检（2026-11-09 22:46，真差异·未修）

R5 用中性示例站（45°N/120°E、**200 m**）跑时，NOAA-19 两边过境集合不一致：

```
我方 24h 列表：5 场，首场 08:52:58（峰值 7.5°）
skyfield 参考：首场峰值 5.025°（贴 5° 门限的掠射场）
最近一场差 31991 s ≈ 8.9 h → 我方漏了那场掠射过境
```

- **判定**：真实差异（漏检），不是配对问题。同一坐标、同一 TLE、同一 24h
  窗口，我方没抓到峰值 5.025° 的那一场。
- **旁证**：旧默认站高度是 0 m 时该场低于门限、不出现；换成 `SAMPLE_SITE`
  的 200 m 后暴露。**之前 PASS 是因为高度被写成 0（近似真实站址），不是
  因为算得准。**
- **为什么没修**：`predict_passes` 的粗扫步长（60 s）+ 门限判定需要单独
  设计边缘捕获策略（二分收敛是否覆盖「峰值刚好过门限」的掠射场），
  涉及时刻精度回归面，本轮不动。
- **转绿条件**：我方列表含该场，且 `el_err_33591` / `rise_err_33591` 转绿；
  或证明参考侧门限判定有误并给出反证。
- **间歇性**：22:46 实测 FAIL（漏 5.025° 场）；22:36 与 22:36 之后的
  最终一轮 R5 判 PASS——24h 窗口随墙钟滑动，该掠射场不总是落在窗口内。
  **不许拿「最后跑那次 PASS」说这条不存在**；它是窗口依赖的偶发漏检。
- **诚实边界**：R5 PASS 的那几轮里其余 3 颗误差 0.01–0.05°、升起时刻
  1–5 s；FAIL 那轮同一批数字也是这个量级，差异只在 NOAA-19 这一场。
- **为什么没有 nodeid**：实现在 `scripts/hw_acceptance/ring5_satellite_passes.py`；
  不需射频硬件，但依赖 skyfield/TLE 与随墙钟滑动的 24h 窗口，仓库里没有固定
  时刻/TLE 的 pytest 节点。
- **现在能否机检**：**半可机检**。脚本可离线跑（`--lat/--lon/--alt/--out`），
  但窗口与 TLE 可变，直接接 CI 会抖动；当前不满足「可复跑」的稳定性要求。
- **人工复核（谁/多久/看什么）**：硬件验收线（该脚本无活跃 claim），每两周 1 次
  （发版前必跑），固定 TLE + 固定观测时刻跑
  `python scripts/hw_acceptance/ring5_satellite_passes.py --lat 45 --lon 120 --alt 200`，
  专看 NOAA-19 的 5.025° 掠射场是否在列；连续两轮不在列即保持红。

---

## K13 — 场景 B 天空视图仰角与 sgp4 直算差 0.23°（2026-11-09 23:05，已修·改为确定性用例）

```
tests/test_e2e_operation_scenarios.py::test_scenario_b_sky_to_spectrum
assert abs(elev - st["elevation"]) <= 0.2
  → elev=37.8（工具回执） vs st=38.0272（compute_satellite_state）
  → 差 0.227，超阈值 0.013
```

- **根因（2026-11-13 更正，推翻原「历表缓存 10 秒一刷」）**：该链路
  `sky_view_visible → new_spacetime.compute_visible_satellite_count →
  decoders.list_visible_satellites → decoders.compute_satellite_position`
  **没有任何缓存/刷新**，每次现算。真正的分叉是
  `decoders.compute_satellite_position` 用 `time.gmtime(timestamp)` 把时刻
  截到整秒；而 TimeEngine `set_unix(整数)` 后 `now_unix()` 因 JD 往返落在
  整秒下方约 18µs，截断后整整退回 **1 秒**，参考侧
  `orbit.compute_satellite_state(when=t_off)` 用精确时刻。差值 = 该几何下
  1 秒的仰角变化量：2026-11-13 当天首场 0.074°（绿），24h 内最大 0.2598°（红），
  历史 0.227° 是同一机制下更陡的一场。即「1 秒量化 × 当天过境几何」，
  不是「跳时刻后没等刷新」。
- **不是抖动（保留原文）**：连跑两次都红（7.86s / 7.92s），是当前时刻的确定性差异。
- **修复**：`decoders.compute_satellite_position` 改为直接用 Unix 秒算儒略日
  （`jd_utc = timestamp/86400 + 2440587.5`，`jd=int(jd_utc)`，`fr=jd_utc-jd`），
  保留亚秒，与 `orbit` 同一口径；删除 `time.gmtime` + `jday` 的整秒截断。
- **确定性反证**（固定偏移、无 sleep、不依赖运行时刻）：
  `tests/test_e2e_operation_scenarios.py::test_scenario_b_sky_guard_is_live`
  把工具物理时刻固定回退 60 s（等价「跳时刻后没跟上」），同一仰角判据必须
  `AssertionError`。
- **改前/改后（独立 worktree 实测）**：
  - 改前 worktree `d:\mbdsdr\_wt\k13-before`（HEAD `99a61044`）
    - 当日单跑：`test_scenario_b_sky_to_spectrum` **1 passed**（该场几何恰好
      只差 0.074°，说明原「绿」不代表修好）；
    - 固定 epoch `1794576350`（2026-11-13T13:25:50Z，当日 1s 仰角变化最大处，
      取本地 TLE 缓存算得）：工具 49.8272° vs 直算 50.0732°，差 **-0.2459°**
      → 判据**红**（工具走 TimeEngine 缺省时刻，参考侧显式同刻）。
  - 改后 worktree `d:\mbdsdr\_wt\kred-99a61044`
    - 同固定 epoch：差 **-0.0001°** → 绿；
    - `tests/test_e2e_operation_scenarios.py` 全文件 **5 passed**；
      K13 两条节点连跑 3 次均 `2 passed`。
- **复跑命令**：

  ```bash
  MBDSDR_OFFLINE=1 QT_QPA_PLATFORM=offscreen python -m pytest \
    tests/test_e2e_operation_scenarios.py::test_scenario_b_sky_to_spectrum \
    tests/test_e2e_operation_scenarios.py::test_scenario_b_sky_guard_is_live -q
  ```
- **诚实边界**：用例仍依赖「24h 内有一场 NOAA 19 过境 + 本地有可用 TLE」，
  无 TLE 时 skip（环境前置条件，与本红无关）；但判据本身不再随当天过境几何
  在绿/红之间摇摆——1 秒级时间差已消除（改前/改后同刻差 0.2459° → 0.0001°）。
- **状态**：已修（2026-11-13，基于 `99a61044` 的单笔提交含 `mbdsdr_ai/decoders.py`
  + `tests/test_e2e_operation_scenarios.py` + 本条目）。历史原文保留不改，
  仅追加本次结论。

---

## K14–K16 — 读数一致线交叉撞到的三条存量红（2026-11-12，均非本线引入）

**验证方式**：独立 worktree 检出 HEAD `859b7003`（不读工作区，避免「HEAD 红」与
「本地半截改动」混成一个现象），三条各单节点跑一次，均**确定性红**（非抖动）。
首次变红均用 `git log -S` 限定到具体文件定位，并用父提交内容核对「红之前是绿的」。

**统一复跑命令**

```bash
QT_QPA_PLATFORM=offscreen python -m pytest "<nodeid>" -q
```

| ID | nodeid | 首次变红 | 当前文件 owner |
|---|---|---|---|
| K14 | `tests/test_copy_units_audit.py::test_t02_help_uses_real_topbar_demod_labels` | `a287ef63`（2026-11-11 23:20） | `desktop/help_panel.py` 无活跃 claim；提交时持有者 NONE（无台账） |
| K15 | `tests/test_copy_units_audit.py::test_discoverability_ports_are_findable_from_settings_tab` | `3c23faa2`（2026-11-12 00:09） | `desktop/settings_panel.py` = 设置三环线（pid 3484，TTL 30min）；提交时持有者 性能线/6ae85f56 |
| K16 | `tests/test_main_window.py::TestThreeColumnSplitter::test_three_panes_present` | `c204e945`（2026-11-12 11:20） | `tests/test_main_window.py` = 布局锁线/59ca04d1（claim 已过期，pid 26460）；提交时持有者 右栏收窄A |

### K14 — 帮助页解调模式列表被改写，断言仍按旧全字匹配

- **失败消息首行**：`assert 'FM / WFM / NFM / AM / USB / LSB / CW' in _HELP`
- **分类**：存量（他线文案改动引入）。
- **根因**：`a287ef63` 在 `desktop/help_panel.py` 把
  `"（FM / WFM / NFM / AM / USB / LSB / CW）"` 改成
  `"（可切 WFM / NFM / AM / USB / LSB / CW）"`；该提交只改了
  `help_panel.py` / `troubleshooting.py` / `test_copy_entry_points.py`，
  没有改 `tests/test_copy_units_audit.py`。父提交 `a287ef63^` 的
  `help_panel.py` 仍含旧串，所以红是这一笔引入的，不是从最初就红。
- **转绿条件**（任一条，并说明为何不是放宽）：
  - 把 `FM` 重新写进该句（例如「模式」后补全 `FM / WFM / …` 的实际顶栏列表）；或
  - 断言改为从 `mode_registry` 取真实模式 label 拼串再比对 `_HELP`，
    避免再被文案改写打断。
  验收：该节点单跑 `1 passed`，且断言仍能抓到「列表少一个模式」的回归。
- **收口（2026-11-13）**：判定为**产品文案漏字**，修文案，断言不动。
  - 依据：`desktop/mode_registry.py:96` 注册了 `FM`（`_BUILTIN_MODES["FM"]`，
    频偏 75 kHz，解调函数 `mbdsdr_ai.dsp:fm_demod`）；
    `desktop/control_panel.py:159` 的 `_PANEL_MODE_ORDER` 首项就是 `FM`，
    `:462` 按「已注册」过滤后填进模式下拉；`desktop/main_window.py:1733-1734`
    的顶栏 `toolbar_mode_combo` 同一列表也含 `FM`。即 FM 是真可选的解调模式，
    `a287ef63` 只是把它从列举里漏掉，不是产品删了 FM，故修文案不改断言。
  - 改动：`desktop/help_panel.py:44` 的「（可切 …）」列表补回 `FM`。
  - 验证（均在独立 worktree 单跑该节点，不跑全量）：
    - HEAD `d7715275`（worktree `d:\mbdsdr\_wt\k14-head`）：
      `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_copy_units_audit.py::test_t02_help_uses_real_topbar_demod_labels -q`
      → `1 failed`（断言消息与上面登记的首行一致）；
    - `a287ef63^` = `4aa0514`（worktree `d:\mbdsdr\_wt\k14-parent`）同一命令
      → `1 passed`；
    - 改后同一 HEAD worktree 跑 K14 节点 +
      `tests/test_copy_entry_points.py::test_onboarding_entries_resolve_to_live_widgets`
      → `2 passed`（后者证明「FM」仍是真窗口控件树里点得到的原文）。
  - 提交：K14 收口提交 `a58dc94d`（2026-11-13，`git cat-file -t a58dc94d`
    已验证为 commit）。`git cat-file -t` 可验证的引用：`a287ef63`=commit、
    `4aa0514`=commit、`d7715275`=commit。

### K15 — 设置页惰性构建后，可发现性断言扫不到控件

- **失败消息首行**：`AssertionError: 服务开关与端口见右栏「服务设置」一节。`
- **分类**：存量（他线启动性能改动引入）。
- **根因**：`3c23faa2` 把 `SettingsPanel._build_ui()` 从 `__init__` 移到
  首次显示时构建（`_ui_built` / `showEvent`）；用例直接 `SettingsPanel()`
  不 `show()`，实测 `findChildren(QLabel)` 为 **0**（`QGroupBox` 也为 0），
  扫不到任何文案。父提交 `3c23faa2^` 的 `__init__` 里直接调 `_build_ui()`
  （两处命中），所以红是这一笔引入的。
- **转绿条件**：用例先触发一次构建（`panel.show()` 或显式调用面板的惰性构建
  入口）再扫文案；**不许删掉该断言**。验收：单跑 `1 passed`，且构造后不
  `show()` 时控件仍为零（惰性构建这个行为不能被改回去）。
- **注意**：工作区 `desktop/settings_panel.py` 有他线未提交修改，修复需与其
  合并后复核，别用旧 blob 覆盖。
- **收口（2026-11-13）**：只改用例，产品代码未动（工作区他线的
  `desktop/settings_panel.py` 改动未碰、未覆盖）。
  - 改动：`tests/test_copy_units_audit.py` 的用例在扫文案前对两个面板调用
    `panel.show()`，走产品自己的 `showEvent → _ensure_ui_built()` 显示入口，
    扫完再 `close()`；7 条 needle 断言一条没删、没放宽。
  - 为什么不是「把断言改成能过」：`show()` 只是复现用户真正打开设置页的路径；
    为证明用例仍依赖这条构建路径，做了行为级反证——在独立 worktree 里把
    `showEvent` 首行的 `self._ensure_ui_built()` 临时换成 `pass`，同一节点立刻
    回到 `1 failed`（仍是同一条断言消息）；恢复后 `1 passed`。若只是放宽断言，
    这条反证不会红。
  - 验证（独立 worktree 单跑该节点，不跑全量）：
    - HEAD `a58dc94d`（worktree `d:\mbdsdr\_wt\k15-head`）改前：
      `QT_QPA_PLATFORM=offscreen python -m pytest tests/test_copy_units_audit.py::test_discoverability_ports_are_findable_from_settings_tab -q`
      → `1 failed`（断言消息与上面登记的首行一致）；
    - `3c23faa2^` = `4d156159`（worktree `d:\mbdsdr\_wt\k15-parent`）同一命令
      → `1 passed`；
    - 改后同一 HEAD worktree：该节点 `1 passed`；另跑
      `tests/test_settings_panel_lazy.py` → `3 passed`（构造后不 `show()` 时控件
      仍为零，惰性构建契约没被改回去）。
  - 提交：K15 收口提交 `6789bc85`（2026-11-13，`git cat-file -t 6789bc85`
    已验证为 commit）。`git cat-file -t` 可验证的引用：`3c23faa2`=commit、
    `4d156159`=commit、`a58dc94d`=commit。

### K16 — 右栏收窄加了一层 host，splitter 断言仍按旧父件

- **失败消息首行**：
  `assert <QWidget name="rightColumnHost"> is <QWidget name="rightColumn">`
- **分类**：存量（他线布局改动引入）。
- **根因**：`c204e945` 给右栏加 `rightColumnHost`（QStackedLayout：0=展开态
  长列 / 1=48px 图标条），`_main_splitter` 第 2 栏从 `RightColumn` 变成
  `_right_stack_host`；该提交只改 `desktop/main_window.py`，未更新
  `tests/test_main_window.py`。父提交 `c204e945^` 里是
  `_main_splitter.addWidget(right_col)`，HEAD 里是 `addWidget(self._right_stack_host)`。
- **现状**：工作区 `tests/test_main_window.py` 已有未提交的新断言（点名
  `rightColumnHost`，docstring 引用 `c204e945`），即**修复在途未落库**；
  登记后由布局线提交即可销账。
- **2026-11-13 复核**：HEAD `99a61044` 独立 worktree 单跑仍 `1 failed`；主工作区
  那份未提交断言仍在，但 claim 持有进程 pid 26460 已不存在（`Get-Process` 查无），
  claim（2026-11-12 20:39 取得，TTL 10min）已过期。即「在途」应改判为
  **无人接手的孤儿改动**：需布局锁线确认后提交，或按接管规则另行处理。
- **转绿条件**：提交工作区那份 `test_main_window.py`；验收单跑 `1 passed`，
  且断言仍点名 host 层（拆掉这层要能红）。不许为了让测试绿去改产品结构。

---

## K17 — 设置向前覆盖：3 个字段无 UI 入口也无理由（2026-11-13 实跑，存量·未修）

- **nodeid**：`tests/test_settings_ui_coverage.py::test_forward_every_field_has_ui_entry_or_reason`
- **复跑命令**：
  ```bash
  MBDSDR_OFFLINE=1 QT_QPA_PLATFORM=offscreen \
    python -m pytest tests/test_settings_ui_coverage.py::test_forward_every_field_has_ui_entry_or_reason -q -rf
  ```
- **失败消息首行**：
  `AssertionError: 以下 schema 字段既没有 UI 入口也没有登记理由（配置漂移源头）：`
  实测 `Report(fields=55, ui_entries=50, reasons=2, missing=['agc_enabled',
  'settings_expanded_groups', 'right_column_expanded_section'])`。
- **首次记录**：2026-11-13（HEAD `99a61044`，独立 worktree
  `d:\mbdsdr\_wt\kred-99a61044` 单跑）。
- **首次变红**：`2a07f344`（2026-11-11 22:14「术语统一：扫频/书签」）。该提交把
  `desktop/control_panel.py` 的 `self.agc_check = QCheckBox("AGC 自动增益")` 改成
  `QCheckBox("硬件 AGC")`，而 `scripts/settings_inventory.py` 的 `agc_enabled` 锚点
  仍是旧文案 → UI 入口 51→50。另两键由 `20652e90`（2026-11-11 22:26「登记两个
  持久化键」）写入 `desktop/config_schema.py`，但无 UI 也未进 `INTERNAL_REASONS`。
  定位方式：`git log -S`（未逐提交 checkout 复跑）。
- **根因**：盘点锚点跟着产品文案走，文案改了锚点没跟；内部持久化键入库时没有
  同步登记「为什么没有 UI」。
- **当前 owner**：`desktop/config_schema.py`、`scripts/settings_inventory.py` 无活跃
  claim；`desktop/settings_panel.py` = 设置三环线；`docs/settings-inventory.md` =
  机制线。建议产品侧归 **设置三环线**，锚点同步归 **机制线**。
- **转绿条件**：单跑 `1 passed` 且 `measure().missing == []`；`agc_enabled` 要么按
  实际控件更新锚点（`硬件 AGC`），要么把控件文案改回；两个内部键在
  `INTERNAL_REASONS` 写明「为什么没有 UI」。反证验收：临时删掉 `control_panel`
  的 AGC 控件或删一条理由，该节点必须重新变红。

---

## K18 — 设置盘点表与脚本读数不一致（2026-11-13 实跑，存量·未修）

- **nodeid**：`tests/test_settings_ui_coverage.py::test_inventory_table_matches_script`
- **复跑命令**：
  ```bash
  MBDSDR_OFFLINE=1 QT_QPA_PLATFORM=offscreen \
    python -m pytest tests/test_settings_ui_coverage.py::test_inventory_table_matches_script -q -rf
  ```
- **失败消息首行**：
  `AssertionError: docs/settings-inventory.md 数字表与脚本盘点结果不一致：`
  文档块实际为 `53 字段 / 51 有 UI / 0 缺失 / source_commit a2be554a`；
  当前脚本盘点为 `55 / 50 / 3 / ce1ed9ef`。
- **首次变红**：`2a07f344`（2026-11-11 22:14，UI 入口 51→50、缺失 0→1），随后
  `20652e90`（22:26，字段 53→55、缺失 1→3）；表最后一次生成在 `a2be554a`
  （2026-11-11）。定位方式 `git log -S`。
- **当前 owner**：`docs/settings-inventory.md` = 机制线；`scripts/settings_inventory.py`
  无活跃 claim；产品字段缺失另归设置三环线（见 K17）。
- **转绿条件**：`python scripts/settings_inventory.py --write` 后单跑 `1 passed`，
  且 diff 只来自脚本生成、`source_commit` 等于
  `git log -1 --format=%h -- desktop/config_schema.py`。注意：K17 未修时也允许本条目
  先绿——表如实记录 `缺失=3` 即可，不许为了表绿去改盘点口径。

---

## K19 — 源码文本 pin 棘轮新增 33 条（2026-11-13 实跑，存量·未修）

- **nodeid**：`tests/test_source_pin_ratchet.py::test_no_new_source_text_pins`
- **复跑命令**：
  ```bash
  MBDSDR_OFFLINE=1 QT_QPA_PLATFORM=offscreen \
    python -m pytest tests/test_source_pin_ratchet.py::test_no_new_source_text_pins -q -rf
  ```
- **失败消息首行**：
  `AssertionError: 新增源码文本 pin（必须改成行为断言，或由 owner 在基线写明理由）：`
- **首次变红**：`469de07c`（2026-11-11 22:43，`test_tooltip_guard` 新增
  `"配置意图，不是设备读数"` pin）。基线最后更新是 `01cf93de`（2026-11-06），
  其后任何新增 pin 都会让该节点红；定位方式 `git log -S`（按文件取最早一条，
  未逐提交 checkout 复跑）。
- **新增 pin 清单（33 条 / 10 个文件）**：
  - `test_config_persistence_safety.py`（3）：`"损坏"`、`str(path)`、
    `text.count("\n")`
  - `test_connect_request_store.py`（3）：`payload["_bandwidth"]`、
    `payload["_gain"]`、`payload["_ppm"]`
  - `test_device_owner_lock.py`（2）：`"JSON 读取/解析失败"`、`str(_registry)`
  - `test_diagnostics_panel_pure_display.py`（7）：`".setText("`、
    `"QApplication.clipboard"`、`"QDesktopServices"`、`"startfile"`、
    `"QScrollArea"`、`"QTextEdit"`、`"setPlainText"`
  - `test_hw_acceptance_contract.py`（7）：`"abs(f_p - 19000) < 100"`、
    `"abs(f_p - 19000) < 300"`、`f"19000±{tol:.0f} Hz 且峰/邻域 >{snr:.0f} dB"`、
    `ring3_src.count("PILOT_FREQ_TOL_HZ")`、`f"{lo} – {hi}"`、
    `"此值不适用于产品立体声探测"`、`"此值不适用于硬件验收"`
  - `test_island_contrast_batch1.py`（2）：`"QPen(_GRID"`、`"p.setPen(MUTED)"`
  - `test_island_contrast_batch2.py`（2）：`"QPushButton:hover { border-color: #8f95a3; }"`、
    `src.count("border: 1px solid #6A7284")`
  - `test_safe_commit_exclude.py`（1）：`"line5 other"`
  - `test_startup_selfcheck.py`（3）：`"延后导入失败"`、`"怎么修"`、`"缺了会怎样"`
  - `test_tooltip_guard.py`（3）：`f"def {name}("`、`"配置意图，不是设备读数"`、
    `"（默认 · 未确认）"`
- **当前 owner**：`tests/test_source_pin_ratchet.py`、`docs/source-pin-ratchet-baseline.json`
  均无活跃 claim；各 pin 由对应测试线引入。建议由引入线改成行为断言，确实必须钉
  文本的由 **机制线** 在基线按现有 schema 登记 `reason`。
- **转绿条件**：单跑 `1 passed`；每条 pin 要么改成行为断言，要么在
  `docs/source-pin-ratchet-baseline.json` 写明理由。反证验收：再加一条未登记 pin，
  节点必须红。

---

## K20 — tooltip 守卫 4 条（2026-11-13 实跑，存量·未修）

**合并为一条而非四条**：同一文件、同一契约（`docs/discoverability.md` 三条规矩）、
同一批转绿动作（补登记 / 改文案 / 删数值），拆开只会把一个修复批次记成 4 笔账。

| nodeid | 失败消息首行 | 首次变红 |
|---|---|---|
| `test_dynamic_tooltips_are_honest` | `AssertionError: 动态 tooltip 有量纲却没写来源/置信标注：`（`device_panel.py:365` / `:494`） | `ff7e3288`（2026-11-12 22:36，设备枚举看门狗文案） |
| `test_every_passthrough_tooltip_is_registered` | `AssertionError: 以下透传 tooltip 没有进登记表（也就不可能有运行时兜底）：`（`control_panel.py:506` / `:1045` `_step_tooltip_text()`） | `b97b0cd1`（2026-11-11 23:34，步进 tooltip 随当前档位生成） |
| `test_tooltip_baseline_only_shrinks` | `AssertionError: 新增了不合规 tooltip（长度 <12 或与控件可见文字逐字相同）：`（`main_window.py::收起右栏浮层（Esc）::too_short`） | `c204e945`（2026-11-12 11:20，右栏浮层临时展开按钮） |
| `test_baseline_file_stays_in_sync_with_counter` | `AssertionError: assert 3 == 4`（基线 3 条 vs 当前 4 条） | `c204e945`（同上，出现第 4 条违规） |

- **复跑命令**：
  ```bash
  MBDSDR_OFFLINE=1 QT_QPA_PLATFORM=offscreen \
    python -m pytest tests/test_tooltip_guard.py -q -rf
  ```
- **当前 owner**：`desktop/device_panel.py`、`desktop/control_panel.py`、
  `desktop/main_window.py`、`docs/tooltip-baseline.json` 均无活跃 claim。按引入线：
  `ff7e3288` = 设备面板线、`b97b0cd1` = 控制面板线、`c204e945` = 右栏收窄线。
- **转绿条件**：4 条全部通过——动态 tooltip 补「未确认 / 配置意图 / 以设备为准」
  标注或删数值；透传 tooltip 进登记表；`收起右栏浮层（Esc）` 改写到 ≥12 字且不等于
  可见文字；基线只降不升（当前 3 条，不许把第 4 条塞进去）。验收：整文件
  `7 passed`；反证：恢复任一违规，对应节点必须红。
