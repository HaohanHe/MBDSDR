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
