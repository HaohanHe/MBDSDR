# 交接单：三条「环境噪声」的实证归因（2026-11-11 实测）

> 来源：W71–W77 策论线。等树接近静置后逐文件独跑，红点可稳定复现。
> 结论：**不是环境噪声，是三笔已落库改动各自漏更新的陈旧测试 + 1 条过期白名单**。
> 三个测试文件当前 `git status` 均为空（未被任何线占用），可直接接。
> 树未 100% 静置：`desktop/module_panel.py` 仍有他线未提交改动，但
> `git diff -- desktop/module_panel.py` 不涉及 `sample_rate` / `_fill_sample_rate`
> 路径，下列归因不受影响；接完后请复跑一次。

实测命令与结果（本机，2026-11-11）：

```
python -m pytest tests/test_real_software_wiring.py -q      -> 1 failed, 24 passed
python -m pytest tests/test_tooltip_guard.py -q             -> 1 failed, 2 passed
python -m pytest tests/test_ui_wiring_audit.py -q           -> 3 failed
```

---

## ① `tests/test_real_software_wiring.py:216`

- 用例：`test_sky_search_needs_station_notice`
- 红点：`assert "地面站" in view._camera_notice` → AssertionError
- 实际文案：`desktop/rf_sky_view.py:5102`
  「未设置观测地点：无法计算方位（设置 → 卫星与多普勒 → 观测地点纬度/经度）」
- 引入提交：`eb8515dc fix(sky-view): 观测地点指路改为设置页真实项名（B3）`
  —— 只改了 `desktop/rf_sky_view.py`（3+/2-），未同步本测试
- 持有线：**sky-view / B3 文案线**
- 建议修法（二选一，产品口径以 `eb8515dc` 为准）：
  1. 断言改成语义锚点：`assert "未设置观测地点" in view._camera_notice`；
  2. 或断言「观测地点」与设置入口名「卫星与多普勒」同时出现。
  不要把文案退回「地面站」——那会回退 B3 的指路改进。

## ② `tests/test_tooltip_guard.py:176`（期望清单第 `154` 行）

- 用例：`test_upgraded_tooltips_meet_the_rules`
- 红点：`AssertionError: 文案未落地：每秒采集的样本数；越高能看的带宽越宽，默认值通常够用`
- 实际文案：`desktop/device_panel.py:215`
  「每秒采集的样本数（配置意图，不是设备读数）：未连接时按性能档预填 …（默认 · 未确认），…」
- 引入提交：`d77d9b78 fix(sr): 采样率未确认默认值统一写成「默认 · 未确认」`
  —— 重写了 `device_panel.py` 的 SR tooltip；`78003d87` 恢复该文件时沿用新
  文案。两笔都未同步 `tests/test_tooltip_guard.py` 的逐字期望清单
- 持有线：**采样率文案线（d77d9b78 / 78003d87）**
- 建议修法：把第 `154` 行期望字符串**逐字**改为当前完整文案（含
  「默认 · 未确认」与性能档数字的拼接结果），保留第 165-167 行「追平产品
  文案、不放宽成子串」的注释；不要改成 `in` 子串判断。

## ③ `tests/test_ui_wiring_audit.py:84` 与 `:125`

- 用例：`test_sample_rate_combo_carries_hz`（`assert p.sr_combo.count() == 8`
  → 实际 1）与 `test_connect_payload_follows_widgets`
  （`2400000.0 == 1024000.0` 失败）
- 根因：测试注入的 fake device（第 80-83 行、第 112-115 行）只有
  `sample_rate_range`，**没有 `capability_status`**。自
  `16042e4b fix(cap): 能力来源缺省一律按未知，禁止回退成「按声明」` 起，
  `sample_rate_options()` 对无来源的设备返回 `None`；`module_panel`
  只保留一项说明项（`data=None`），`selected_sample_rate_hz()` 兜底
  2.4 MHz —— 两条断言因此一起红
- 引入提交：`16042e4b`（同笔更新了 `tests/test_sample_rate_options.py`、
  `tests/test_sample_rate_single_source.py`，漏了本文件）
- 持有线：**设备能力来源线（16042e4b）**
- 建议修法：两处 fake device 都补来源字段，例如
  `"capability_status": {"sample_rate_range": "declared"}`（声明即可，因为
  本用例验证的是「档位来自设备能力并挂 Hz」，不是探测真伪）；
  补后 8 档 / 1.024 MHz / 2.56 MHz 三条原断言应原样成立。
  不要放宽成「count >= 1」。

## ④ `tests/test_ui_wiring_audit.py:383`（白名单条目第 `235` 行）

- 用例：`test_allowlist_entries_still_unconnected`
- 红点：`白名单过期条目: [('new_spacetime_panel.py:self.passes_table',
  '该控件已有 connect，白名单条目应删除')]`
- 引入提交：`cb3250f4 feat(spacetime): 过境表加排序/筛选/行定位与「下一次
  过境」倒计时（W84-P1-15）` —— 给 `passes_table` 加了
  `itemSelectionChanged` 之类 connect，但忘了删白名单
- 持有线：**W84 过境表线（cb3250f4）**
- 建议修法：删除 `tests/test_ui_wiring_audit.py:235` 的
  `"new_spacetime_panel.py:self.passes_table": "只读过境表（NoEditTriggers）"`
  条；删后本用例应绿，且不需要新增例外。

---

## 接线顺序建议

四件事互不冲突，可分四笔小提交（每笔只含测试文件或对应源文件）：

1. ① 改 `test_real_software_wiring.py` 一条断言；
2. ② 改 `test_tooltip_guard.py` 一条期望；
3. ③ 补两处 fake device 的 `capability_status`；
4. ④ 删一条白名单。

每笔都要「改回旧断言变红、恢复变绿」留证；四笔落库后这把三条命令应全绿。
