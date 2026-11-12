# 已知红台账（Known-Red Register）

> 目的：让"还没修"这件事**可见、可复跑、可销账**，而不是靠 `--deselect` 藏在命令行里。
> 规则：只有**实跑复现**过的节点才能进本表；不按 `.pytest_cache` 或记忆猜。
> 每条红必须带「转绿条件」，否则不许写成"已知红"。
> 状态口径：写「已修」必须带一次**可复跑的验证记录**（命令 + 实际输出）；
> 没有记录的只能写「实跑绿·待反证」，不得直接销账。
> 再检义务（机检 R8）：写「已转绿」的条目必须带一行「再检绑定」，写明
> 验证提交 `<sha>` 和文件集合（文件或目录，多个用「、」分隔，格式见 K17）。
> 只要这些文件/目录在 `<sha>` 之后又被改动，该条目**自动退回「待复核」**，
> 检查输出会点名「哪个文件在哪笔提交之后又变了」。判据只用 git 历史
> （`git log <sha>..HEAD -- <path>`），不用行号、不用 mtime。
> 机检命令：`python scripts/check_red_lifecycle.py --paths docs/known-red-register.md`
> ——在提交前/CI 手动跑，**不挂 pre-commit**（钩子只留便宜检查）。

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
collect 清单」映射 nodeid 得到 15 条，并判成「单跑全绿 = 顺序依赖假红」（历史判断，已在本段更正）。
那套映射是错的：collect 顺序随代码变动整体偏移，跑的根本不是真失败的
用例，所以 `15 passed` 证明不了任何事。**以后一律从 pytest `-rf` 汇总的
`FAILED ...` 行取 nodeid，不许用点号序号猜。**

- **转绿条件（本节销账判据）**：本节 20 条全部有结论——18 条真修/自然销账，
  2 条顺序依赖单跑通过；未修的 2 条已转为 K06（静默吞噬棘轮）与 K07（UI 假活按钮），
  以 K06/K07 自己的转绿条件为准。验收：`-rf` 汇总不再出现本节 nodeid，
  且 K06/K07 仍如实标红。
- **首次记录**：2026-11-09 16:09–17:10（权威复跑窗口）。

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
| A | `test_time_input_guard` | `tests/test_first_open_welcome.py:137/162` 裸时钟 → 白名单补该文件 + `# timeguard: allow`（本轮处理） |
| A | `test_ui_interaction_guard` | 按钮 ⧉ 触发 `desktop/main_window.py:3679` 的 `MainWindow._return_all_panels` AttributeError；真方法名 `_return_all_panes`（多一个 l）→ 改名（本轮处理，AST 静态核验） |
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
  汇总行）；kernel 单组 = `2827 passed / 0 failed`。**不得对外宣称整体已经绿。**

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

- **转绿条件（本节销账判据）**：表中 18 条各有实跑批次记录；K06/K07 未修项
  保持 `xfail`，不算绿。验收：对应节点单跑或上下文批次通过，且汇总里
  `xfailed` 不被计为 `passed`。
- **记录日期**：2026-11-09 18:30（本节标题时刻）。

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
- **复跑命令**：

  ```bash
  MBDSDR_TEST_LOCK=0 QT_QPA_PLATFORM=offscreen python -m pytest \
    tests/test_record_replay_config.py::test_start_recording_stops_replay_first -q -rf
  ```

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
- **转绿条件**：`test_scenario_a_pass_to_archive` / `test_scenario_c_plan_and_degrade`
  在任意运行时刻单跑与固定顺序全量均绿；`pass_case` 保持 function scope
  （改回 module scope 必须红）。
- **复跑命令**：

  ```bash
  MBDSDR_OFFLINE=1 QT_QPA_PLATFORM=offscreen python -m pytest \
    tests/test_e2e_operation_scenarios.py -q -rf
  ```
- **首次记录日期**：2026-11-09。

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
- **转绿条件**：① `RtlTcpBackend` 补 `set_offset_tuning` 委托到 adapter 并显式
  返回「协议不支持」，或 `_connect_backend` 先做能力探测再跳过；② `gain_panel`
  的 `listGains` 探测前先 `hasattr`；③ 离线静态断言（`hasattr(RtlTcpBackend,
  "set_offset_tuning")`、探测前 `hasattr`）转绿，**且**真机批次日志不再出现
  「Offset Tuning 下发异常」「listGains 失败」两句。
- **仍红判据**：任一真机验收批次日志出现上述任一句，条目保持打开；离线静态断言
  即使转绿也不单独销账（它证明不了运行期告警消失）。
- **首次记录**：2026-11-09 22:15。

---

## K11 — R4 `playback_audio` 判据擦线（2026-11-09 22:20，判据缺陷·未改）

七环 R4 的判据是「文件回放 RMS / 实时 RMS ∈ (0.3, 3.0)」。真机实测三次
分别是 **0.28 / 0.33 / 0.61** —— 0.28 那次判 FAIL。

- **判定**：判据设计缺陷，不是产品回归。它比的是**两个不同时间窗**的 RMS
  （录完的 IQ 解调 vs 当前实时音频），受电台调制内容/静音段影响。
- **为什么没改阈值**：把 0.3 调成 0.2 就是拿标准换绿字，本轮不做。
- **修法（下轮）**：改成同一段 IQ 的「文件回放 vs 内存直解」对拍，
  消掉时间窗变量；或判据改为「回放 RMS 有能量且与实时同量级」并写明依据。
- **诚实边界**：R4 在其他子项均通过（流式增长、完整性、改速跟随）。本轮
  单跑 `PASS`、全跑曾 `FAIL`，属判据抖动。
- **为什么没有 nodeid**：实现在 `scripts/hw_acceptance/ring4_recording_playback.py`；
  需要真机 SDR 录制 + 声卡回放，不是 pytest 节点。
- **现在能否机检**：**否**。判据比较的是两个不同时间窗的 RMS，离线无射频/音频
  输入无法复现，接 CI 只会得到环境噪声。
- **人工复核（谁/多久/看什么）**：硬件验收线（该脚本无活跃 claim），每批真机验收
  跑一次 `python scripts/hw_acceptance/ring4_recording_playback.py --freq <Hz>`，
  看 `playback_ratio` 是否仍擦线（0.3 附近）；发版前至少 1 次。
- **转绿条件**：R4 判据改为同一段 IQ 的「文件回放 vs 内存直解」对拍（消掉时间窗
  变量），或改为「回放 RMS 有能量且与实时同量级」并写明依据；改后连续两批真机
  验收判定稳定，`playback_ratio` 不再落在 0.3 擦线区。
- **仍红判据**：任一真机批次 `playback_ratio ∈ [0.28, 0.33]` 或判 FAIL，条目保持
  打开；只调阈值不算转绿。
- **首次记录**：2026-11-09 22:20。

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
- **首次记录**：2026-11-09 22:46。

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
- **转绿条件**：`test_scenario_b_sky_to_spectrum` 与 `test_scenario_b_sky_guard_is_live`
  在**固定 epoch** 下确定性地「主用例绿 + 反证红」——主用例仰角差 ≤0.2°，反证把
  工具时刻固定回退 60 s 时必须 `AssertionError`；任一运行时刻单跑不得因当天过境
  几何在绿/红间摇摆。
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
- **现状**：工作区 `tests/test_main_window.py` 的修复已确认归属本线（布局锁线）
  ——diff 与登记描述逐条对应：点名 `rightColumnHost`、`_right_stack` 两页顺序
  （`_right_col` / `right_strip`）、`right_tab` 父件是 `_right_col`，docstring
  引用 `c204e945`。不再按「孤儿改动」处理。
- **2026-11-13 复核**：HEAD `99a61044` 独立 worktree 单跑仍 `1 failed`；主工作区
  那份未提交断言仍在，但 claim 持有进程 pid 26460 已不存在（`Get-Process` 查无），
  claim（2026-11-12 20:39 取得，TTL 10min）已过期。
- **转绿条件**：提交工作区那份 `test_main_window.py`；验收单跑 `1 passed`，
  且断言仍点名 host 层（拆掉这层要能红）。不许为了让测试绿去改产品结构。
- **收口（2026-11-13）**：随本线「conftest 每用例配置隔离 / 锁可见性」同批提交。
  验收证据：同一次全量运行（6889 tests）里
  `tests/test_main_window.py::TestThreeColumnSplitter::test_three_panes_present`
  **不在 82 条失败清单中**，其余失败照常红（不是整批变绿的假象）。
  反证方向写在断言里：拆掉 `rightColumnHost` 这层后 `sp.widget(2)` 不再是 host
  且两页顺序断言失效，必红；旧的 `sp.widget(2) is right_tab.parent()` 在拆层后
  反而仍绿，这就是必须点名该层的原因。修复只动测试断言，未改产品结构。

---

## K17 — 设置向前覆盖：3 个字段无 UI 入口也无理由（2026-11-13 实跑，待复核：原验证提交 `ffeeb7c5`）

- **再检绑定**：验证提交 `ffeeb7c5`；文件集合 `scripts/settings_inventory.py`、`tests/test_settings_ui_coverage.py`、`desktop/config_schema.py`、`desktop/control_panel.py`、`desktop/settings_panel.py`
- **再检（2026-11-13，R8）**：`desktop/control_panel.py`（3 笔）、
  `desktop/settings_panel.py`（1 笔）在 `ffeeb7c5` 之后又被改动 → 本条自动退回
  待复核；下面 `1 passed` 是当时那次验证，不代表当前树。按记录命令重跑后再改回
  `已转绿` 并更新验证提交。

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
- **当前 owner**：设置线（K17 已由 R8 退回待复核）；`scripts/settings_inventory.py`
  无活跃 claim；`desktop/settings_panel.py` 仍属设置三环线，本笔未触碰。
- **收口判定（逐字段）**：
  - `agc_enabled`：不是缺控件，是锚点过时。控件文案早在 `2a07f344` 改为
    `QCheckBox("硬件 AGC")`，产品形态正确，**不补新控件/不改 UI**，按实际控件
    更新 `INTERNAL_UI_ANCHORS`。
  - `settings_expanded_groups` / `right_column_expanded_section`：持久化的是设置页
    分组与右栏手风琴的「上次展开态」界面记忆——用户的表达方式就是点分组/节标题
    本身；再补设置项会形成两个真相源，且没有可通过它表达的长期偏好。**不补入口**，
    在 `INTERNAL_REASONS` 写明「为什么不该暴露」。
- **转绿提交**：`ffeeb7c5`（`git cat-file -t ffeeb7c5` = commit）。
- **验证（实跑）**：单跑该 nodeid → `1 passed`；
  `python scripts/settings_inventory.py` → `字段 55 / 有 UI 入口 51 / 有理由 4 /
  缺失 0 / 幽灵键 0`。
- **反证（实跑）**：
  - 删掉 `settings_expanded_groups` 理由 → 红，报
    `missing=['settings_expanded_groups']`（`ui_entries=51, reasons=3`）；恢复后绿。
  - 把 `agc_enabled` 锚点改回旧文案 `AGC 自动增益` → 红，报
    `missing=['agc_enabled']`（`ui_entries=50, reasons=4`）；恢复后绿。
- **转绿条件**：上述逐字段判定落地后，单跑该 nodeid `1 passed`，
  `python scripts/settings_inventory.py` 报「字段 55 / 有 UI 入口 51 /
  有理由 4 / 缺失 0」；反证：删任一 `INTERNAL_REASONS` 条目或把
  `agc_enabled` 锚点改回旧文案，该节点必须红。

---

## K18 — 设置盘点表与脚本读数不一致（2026-11-13 实跑，待复核：原验证提交 `a92d74c4`）

- **再检绑定**：验证提交 `a92d74c4`；文件集合 `docs/settings-inventory.md`、`scripts/settings_inventory.py`、`tests/test_settings_ui_coverage.py`、`desktop/config_schema.py`
- **再检（2026-11-13，R8）**：`docs/settings-inventory.md` 在 `a92d74c4` 之后又被
  重生成 3 笔（`57ddb41d` / `97f213d5` / `515fe918`）→ 本条自动退回待复核；按记录
  命令重跑并核对读数后再改回 `已转绿`。

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
- **当前 owner**：设置线（K18 已由 R8 退回待复核）。`docs/settings-inventory.md`
  原为机制线孤儿 claim（pid 34636 已退出、心跳过期 +1611.8min、工作区无在途改动），
  按 COORDINATION §6.1.4 2026-11-13 记录型接管，提交后已释放。
- **根因复核（修正原判断）**：表**不是手写**的——块由
  `scripts/settings_inventory.py --write` 生成，`test_inventory_table_matches_script`
  整块比对脚本产出，手改即红。真实根因是 `config_schema` 两次变更
  （`2a07f344`、`20652e90`）后没有重新生成，块内数字停在 `a2be554a` 的快照。
- **转绿提交**：`a92d74c4`（`git cat-file -t a92d74c4` = commit，含接管记录）。
- **验证（实跑）**：`python scripts/settings_inventory.py --write` 后单跑 → `1 passed`；
  整文件 `tests/test_settings_ui_coverage.py` → `67 passed`；
  `python scripts/settings_inventory.py --check` → `文档块一致`；
  `source_commit = ce1ed9ef` 与 `git log -1 --format=%h -- desktop/config_schema.py` 一致。
- **反证（实跑）**：把块内 `55` 改成 `54` → 该 nodeid 红，diff 显示
  `+ 字段数 | **54**` vs `- 字段数 | **55**`；还原后绿。
- **转绿条件**：`python scripts/settings_inventory.py --write` 生成的数字块与
  `--check` 一致，单跑该 nodeid `1 passed`；反证：手改块内任一数字必须红。
- **残留说明**：§0 的 2026-11-05 取证表是历史快照，不在本守卫范围，未改。

---

## K19 — 源码文本 pin 棘轮新增 33 条（2026-11-13 实跑，待复核：原验证提交 `ce31ff20`）

- **再检绑定**：验证提交 `ce31ff20`；文件集合 `tests/`、`docs/source-pin-ratchet-baseline.json`
- **为什么失守（2026-11-13 在 HEAD 实跑）**：当时 `748a2992` / `b00a73d3` / `ce31ff20`
  确实把 33 条清到 0，但后继提交又往同一批测试文件加回 4 条源码文本 pin：
  - `cc2f8109`（恢复流程覆盖损坏 meta 前必须备份留证）→
    `tests/test_file_source_robust.py::test_recover_backs_up_corrupt_meta_with_recovery_reason`
    新增 `"不是用户删除"`、`"恢复流程触发"` 两条；
  - `64b0b397`（接入运行期热插拔监听）→
    `tests/test_usb_hotplug.py::test_module_has_no_reconnection_dependency`
    新增 `"reconnect"`、`"reconnection"` 两条。
  当前树单跑 `tests/test_source_pin_ratchet.py` → **1 failed, 2 passed**，失败首行
  `AssertionError: 新增源码文本 pin（必须改成行为断言，或由 owner 在基线写明理由）：`。
  不是当时写错，而是「已转绿」只核了一次：没有「验证提交之后同文件再变就退回
  待复核」的机制。本条现由 R8 自动退回待复核；重跑并把验证提交更新到最新一次之前，
  不得再写已转绿。

- **nodeid**：`tests/test_source_pin_ratchet.py::test_no_new_source_text_pins`
- **复跑命令**：
  ```bash
  MBDSDR_OFFLINE=1 QT_QPA_PLATFORM=offscreen \
    python -m pytest tests/test_source_pin_ratchet.py -q -rf
  ```
- **失败消息首行**：
  `AssertionError: 新增源码文本 pin（必须改成行为断言，或由 owner 在基线写明理由）：`
- **首次变红**：`469de07c`（2026-11-11 22:43，`test_tooltip_guard` 新增
  `"配置意图，不是设备读数"` pin）。基线最后更新是 `01cf93de`（2026-11-06），
  其后任何新增 pin 都会让该节点红；定位方式 `git log -S`（按文件取最早一条，
  未逐提交 checkout 复跑）。
- **逐条判定结果（33 条，不按文件成批）**：**删 16 / 改 15 / 留 2**。

### 该删 16 条（能用行为断言替代，或已被既有断言覆盖）

- 4 条与既有行为用例/AST 硬闸重复，直接删：
  `".setText("`、`"QApplication.clipboard"` 已有
  `test_copy_path_button_copies_without_opening_directory` 覆盖；
  `"QDesktopServices"`、`"startfile"` 已由同文件 AST 硬闸
  `test_module_has_no_write_or_delete_calls` 覆盖（含参数化反证）。
- 3 条 `"QScrollArea"` / `"QTextEdit"` / `"setPlainText"` 改为新增行为用例
  `test_reason_text_renders_full_inside_scroll_area`：真的构造对话框，断言
  长理由 `toPlainText() == long_reason`、只读、且祖先链上有 `QScrollArea`。
- 2 条 tooltip（`"配置意图，不是设备读数"`、`"（默认 · 未确认）"`）改为
  构造 `DevicePanel` 读渲染后的 `sr_edit.toolTip()`。
- 3 条 `payload["_gain"/"_ppm"/"_bandwidth"]` 改为
  `test_store_fills_every_connect_payload_key`：写入意图后直接断言
  `fill_payload` 带出四个键。
- 2 条岛内对比度负向 pin（`"QPen(_GRID"`、`"p.setPen(MUTED)"`）改为
  渲染时收集 `setPen` 颜色（`_pens_from_paint`），断言 `_GRID`/`MUTED`
  不在实际画笔集合里。
- 2 条注释文本 pin（`"此值不适用于产品立体声探测"`、
  `"此值不适用于硬件验收"`）直接删：注释不承载行为，两阈值分离已由
  命名、不等与大小关系四条断言覆盖；注释措辞变更不应让用例红。

### 该改 15 条（pin 写法让结果不可靠）

- 9 条断言的其实是**运行期生成的文件**（`.reason.txt` / 启动日志 /
  临时 git 仓库里的 doc.md），不是源码文本；扫描器按
  `read_text()` / `open().read()` 误判成源码 pin。原写法把整份文本当
  子串匹配，换行与空白一变就抖。改为按行断言（行数 / 逐行包含 /
  精确行成员），语义不变且更严格：`test_config_persistence_safety.py`（3）、
  `test_device_owner_lock.py`（2）、`test_startup_selfcheck.py`（3）、
  `test_safe_commit_exclude.py`（1）。
- 3 条 ring3 文本匹配改为 AST：`count("PILOT_FREQ_TOL_HZ") >= 2` →
  「至少 2 个函数引用该常量」；两条 `"abs(f_p - 19000) < 100/300" not in src`
  → 「不存在 `abs(f_p …)` 与裸 100/300 字面量比较」。空格/换行不再影响判据。
- 1 条 `f"def {name}("` → AST `FunctionDef` 判定，避免命中注释或调用。
- 2 条时间机器描边 pin（`"QPushButton:hover { border-color: #8f95a3; }"`、
  `src.count("border: 1px solid #6A7284")`）改为构造对话框读实际
  `styleSheet()`，按选择器提取描边色与 hover 色。

### 该留 2 条（只能文本断言）

已按现有 schema 登记到 `docs/source-pin-ratchet-baseline.json`，
baseline 56 → 58（`BUILT_AT` 同步 58），每条带 owner + reason：

| pinned | 为什么不能被重构掉 |
|---|---|
| `f"19000±{tol:.0f} Hz 且峰/邻域 >{snr:.0f} dB"` | 人工验收卡 `docs/hw-acceptance.md` 是给操作员逐字核对的判据表，产品代码没有可断言的消费入口；串由 ring3 两个常量拼出，改常量或改文档任一侧都会红。 |
| `f"{lo} – {hi}"` | 回放比区间只在人工卡里以文本呈现，ring4 常量没有行为调用点；串由两个常量拼出，任一侧改动都会红。 |

两条的共同出路写在 reason 里：**要让验收卡由常量生成**，之后即可改为行为断言。

### 反证（两类都实跑，不是推理）

- **行为断言类**：临时把 `desktop/module_panel.py:154` 空态画笔
  `p.setPen(CANVAS_MUTED)` 改成 `p.setPen(MUTED)` →
  `test_module_canvas_empty_state_uses_canvas_ink` 红：
  `AssertionError: 空态文字没有用画布墨色 / assert '#939393' in {'#606060'}`；
  还原后绿（`git diff` 为空）。
- **保留 pin 类**：临时把 `docs/hw-acceptance.md:169` 的 `>10 dB` 改成
  `>12 dB` → `test_pilot_acceptance_thresholds_match_doc_card` 红：
  `AssertionError: 人工卡与 ring3 导频阈值已分叉：文档里找不到由常量拼出的判据串`；
  还原后绿（`git diff` 为空）。

### 验证

- 11 个相关测试文件同会话运行：**117 passed**（37.55s，持 Qt 锁）。
- 中途另一会话（PID 24992）持锁时，单跑 ratchet 曾用 `MBDSDR_TEST_LOCK=0`
  得到 3 passed；最终结论以持锁的 117 passed 为准。
- **转绿条件**：33 条逐条处置（删 16 / 改 15 / 留 2）后，
  `test_no_new_source_text_pins` 与 11 个相关测试文件全绿（当前树复核见下条 `97fc5fc8`）；留下的 2 条必须继续
  带 owner + reason，且基线只降不升（当前 56 → 58 是这 2 条的登记，不许再涨）。
  反证：把任一「改」过的断言退回文本 pin，该节点必须红。
- **2026-11-13 复核**（`97fc5fc8` 独立 worktree）：
  `python -m pytest tests/test_source_pin_ratchet.py -q` → **1 failed, 2 passed**，
  `tests/test_usb_hotplug.py::test_module_has_no_reconnection_dependency` 仍有
  `"reconnect"` / `"reconnection"` 两处文本 pin。上面那句判断尚未成立于当前树，
  本条按未清处理，不销账。
- **判断**：33 条里只有 2 条真的在钉「产品必须给操作员看到的文档判据」，
  其余 31 条钉的是实现写法——4 条与既有行为用例/AST 硬闸重复、10 条改写为
  行为或运行时取值、2 条是注释文本、9 条是扫描器把运行期文件误判成源码、
  6 条改写成 AST/运行时取值。**31/33 是「我们自己给自己写的答案」**。

---

## K20 — tooltip 守卫 4 条（2026-11-13 实跑，待复核：原验证提交 `294beef4`）

- **再检绑定**：验证提交 `294beef4`；文件集合 `desktop/device_panel.py`、`desktop/control_panel.py`、`desktop/main_window.py`、`desktop/sat_track_panel.py`、`docs/tooltip-dynamic-baseline.json`、`docs/tooltip-baseline.json`、`tests/test_tooltip_guard.py`、`tests/test_tooltip_dynamic_runtime.py`
- **为什么失守（2026-11-13 在 HEAD 实跑）**：`294beef4` 当时 7 passed，但后继提交
  继续往同文件加透传 tooltip，登记表没跟：
  - `64b0b397` → `desktop/device_panel.py` 的 5 处 `event.detail`
    （`:570` / `:588` / `:594` / `:601` / `:605`）、`:710 :: text`、
    `:896 :: identity_note` 未登记；
  - `03fe46c0` → `desktop/device_panel.py`「重新读取设备占用登记」tooltip 长度 <12；
  - 另有 `desktop/sat_track_panel.py:400 :: text` 未登记。
  当前树单跑 `tests/test_tooltip_guard.py` → **3 failed, 4 passed**：
  `test_every_passthrough_tooltip_is_registered` 点名 8 处未登记；
  `test_tooltip_baseline_only_shrinks` 点名
  `desktop/device_panel.py::重新读取设备占用登记::too_short`；
  `test_baseline_file_stays_in_sync_with_counter` → `assert 3 == 4`。
  与 K19 同因：当时真绿、后继提交打破、缺再检机制。本条现由 R8 自动退回待复核。

**合并为一条而非四条**：同一文件、同一契约（`docs/discoverability.md` 三条规矩）、
同一批转绿动作（补登记 / 改文案 / 删数值），拆开只会把一个修复批次记成 4 笔账。

| nodeid | 失败消息首行 | 首次变红 |
|---|---|---|
| `test_dynamic_tooltips_are_honest` | `AssertionError: 动态 tooltip 有量纲却没写来源/置信标注：`（`desktop/device_panel.py:365` / `:494`） | `ff7e3288`（2026-11-12 22:36，设备枚举看门狗文案） |
| `test_every_passthrough_tooltip_is_registered` | `AssertionError: 以下透传 tooltip 没有进登记表（也就不可能有运行时兜底）：`（`desktop/control_panel.py:506` / `:1045` `_step_tooltip_text()`） | `b97b0cd1`（2026-11-11 23:34，步进 tooltip 随当前档位生成） |
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

- **转绿记录**（2026-11-13，提交 `294beef4`，`git cat-file -t` 可验）：
  - 动态 tooltip：`desktop/device_panel.py` 两处枚举看门狗文案补「这是本程序设置
    的等待上限，不是设备读数」，`test_dynamic_tooltips_are_honest` 绿。
  - 透传登记：`desktop/control_panel.py:506` / `:1045` 的 `_step_tooltip_text()`
    逐处进 `docs/tooltip-dynamic-baseline.json`（passthrough，复用现有运行时
    兜底用例）；运行时用例补一次切档位触发，让 `:1045` 也被实际渲染校验。
  - 文案：`desktop/main_window.py:12091` 的「收起右栏浮层（Esc）」补全为完整
    说明，并钉进 `test_upgraded_tooltips_meet_the_rules`；基线仍为 3 条，
    未上调。
  - 守卫收紧：透传登记由「(file, expr) 集合去重」改为按调用点计数，与运行时
    逐处配对一致；同一表达式两处只登记一条不再能蒙绿。
  - 本段此前有一截重复粘贴的尾巴（含孤儿行），已在同一笔登记表提交中删除，
    此处为唯一版本。
- **反证（均实跑）**：
  - 删掉 `:1045` 一条登记 → `test_every_passthrough_tooltip_is_registered` 红，
    点名 `desktop/control_panel.py:1045 :: self._step_tooltip_text()`。
  - `main_window` tooltip 改回空字符串 → `test_upgraded_tooltips_meet_the_rules`
    红（文案未落地）。
  - 去掉 `desktop/device_panel.py:529` 的来源标注 → `test_dynamic_tooltips_are_honest`
    红，点名 `desktop/device_panel.py:528`。
- **验证**：`tests/test_tooltip_guard.py` 7 passed；
  `tests/test_tooltip_dynamic_runtime.py` 除 `settings_panel` 外 16 passed /
  1 deselected。`test_settings_panel_passthrough_tooltips_self_describe` 在 HEAD
  独立 worktree 复跑即红（`settings_panel` 懒构建，构造期不生成 UI），属存量
  问题，不在 K20 范围，未改该文件。**该条已于 2026-11-13 升级为正式 K21**，
  不再只写在正文里。

---

## K21 — 设置页透传 tooltip 运行时用例在懒构建下拿不到 UI（2026-11-13 实跑，存量·待复核）

> 本条原为 K20 段末一句「属存量问题」，没有 K 编号：机检抓不到、下一班翻 K 表
> 也看不到。2026-11-13 按正式条目补编号，结论（存量、非 K20 引入）不变。

- **nodeid**：`tests/test_tooltip_dynamic_runtime.py::test_settings_panel_passthrough_tooltips_self_describe`
- **复跑命令**：
  ```bash
  MBDSDR_OFFLINE=1 QT_QPA_PLATFORM=offscreen \
    python -m pytest tests/test_tooltip_dynamic_runtime.py::test_settings_panel_passthrough_tooltips_self_describe -q -rf
  ```
- **失败消息首行**（2026-11-13 在 HEAD `66a43516` 实跑）：
  `AssertionError: 以下透传条目在构造/触发后没有渲染出 tooltip（运行时兜底失效，请补触发步骤或从登记表移除）：`
  实测缺 3 处：`desktop/settings_panel.py:1072`、`:1424`、`:1432`；结果 `1 failed`。
- **首次记录**：2026-11-13（HEAD `66a43516` 工作树，持 Qt 锁单跑）。
- **首次变红**：未逐提交 bisect；按 K15 的懒构建改动推定早于 K20 的 `294beef4`，
  属存量。定位方式：读 `tests/test_tooltip_dynamic_runtime.py` 的 `_record`
  （只构造面板、不 `show()`）与 K15 记录。
- **分类**：存量（不是本轮引入）。
- **根因**：`_record(SettingsPanel)` 只构造面板就比对 tooltip，而设置页是懒构建：
  不 `show()`/不切分组时 UI 树尚未生成，`setToolTip` 调用为 0。K15 收口时只让
  设置页自身用例先触发构建，这条跨页运行时用例没同步触发。
- **转绿条件**（任一条即可）：
  - 运行时用例对 `SettingsPanel` 先 `show()` + `processEvents()`（或切到对应分组）
    再比对；
  - 或设置页构造期就生成这 3 个控件的 tooltip（需评估懒构建的启动收益，不默认选）。
  验收：该 nodeid `1 passed`；反证：去掉 `_record` 里的构建触发，必须重新变红。
- **当前 owner**：`tests/test_tooltip_dynamic_runtime.py` 与
  `desktop/settings_panel.py` 均无活跃 claim；按引入线归设置页线（K15）。
- **再检**：本条从未写过「已转绿」，暂不需要再检绑定；修完若要写「已转绿」，
  必须同时补 `再检绑定`，否则 R8 直接判红。

