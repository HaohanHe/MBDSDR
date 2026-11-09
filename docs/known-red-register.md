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

## K05 — 全量长序列里的顺序依赖红（2026-11-09 登记，根因未定位）

- **发现方式**：`tools/evidence/test_suites.py --suite both`（两条套件各自
  单子进程、`MBDSDR_TEST_LOCK=0`、`-p no:randomly`、无并发、期间不改源码）。
  kernel 在 75.1% 出现 2 个 F；desktop 在 1800 s 超时前跑到 56%
  （3214/5581），累计 13 个 F。
- **分类结果**：把两条套件的 15 个失败 nodeid 放进一个无并发进程单独复跑
  → `15 passed`（496 s，日志 `output/auth_nodes_rerun.log`）。
  即**单跑全绿、固定顺序全量红**。上午把它们归为「并发 / 时序假红」，
  其中「并发」这一半不成立：本轮无并发照样红。
- **涉及用例**（按全量日志序号映射，收集顺序见 `output/kernel_collect.txt`
  与 `output/tests_collect2.txt`；序号与 collect 存在 1~2 条偏移，
  以实际 `-rf` 汇总为准）：

  | 套件 | 用例（文件粒度） |
  |---|---|
  | kernel | `test_star_catalog_packaging.py`（2 条） |
  | desktop | `test_adsb_any_fs.py`（2）、`test_data_licenses.py`（2）、`test_fake_rtl_chain.py`（4）、`test_help_path_truthfulness.py`（1）、`test_hold_snr.py`（1）、`test_layout_persist_split_brain.py`（1）、`test_mcp_client_bridge.py`（1）、`test_no_sim_regression.py`（1） |

- **与 K01–K04 的区别**：K01–K04 是测试装置失效，用 `xfail(strict=True)`
  钉住；K05 是**跨用例全局状态泄漏**，`xfail` 会掩盖问题（单独跑是绿的，
  strict 会变 XPASS 反而更乱），所以只登记、不加标记。
- **转绿条件**：找出留下状态的污染源用例（二分法：固定目标用例 +
  逐步缩短它前面的用例前缀，直到目标转绿），在 `tests/conftest.py` 加
  function 级 autouse 复位，或在产品侧取消该单例的跨用例可见性；
  然后完整跑一遍 `tests/` 全量并交出带汇总行的日志。
- **为什么现在不修**：完整 desktop 全量在本机约 55 分钟，二分定位需要
  多轮全量前缀运行；本轮主线是证据包的可复现性，先如实登记。
- **对外口径**：不得声称 `tests/` 全绿。可出示的是 kernel 单组
  `2827 passed / 5 skipped / 0 failed`（汇总行完整、`counts_trustworthy=True`）
  与 15 节点干净复跑 `15 passed`。
