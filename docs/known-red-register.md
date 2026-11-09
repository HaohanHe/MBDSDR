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

