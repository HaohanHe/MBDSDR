# 交接单：未连接时采样率必须标「默认·未确认」（方案 B）

> 提出：2026-11-11；落点：`desktop/control_panel.py:555-578`（`_build_ui` 的
> 采样率行）与 `:1540-1622`（档位预填/刷新）
> **状态：等待执行**。该文件当前由布局线持有未提交改动（`git status` 非空），
> 本线按纪律**不抢**；等 `git status --short -- desktop/control_panel.py` 为空
> 并 claim 后再动。

## 一、缺陷

未连接设备时，同一块面板里：

- `LNA` 显示 `—`、`频率` 显示 `—`（诚实空态）；
- 「采样率」却显示 `2.400 MS/s`——它是性能档默认值（`DEFAULT_SAMPLE_RATE_HZ`
  经 `_default_sample_rate_options()` 预填），不是任何设备确认过的能力。

用户会把它当成「当前设备就是这个采样率」。同一面板两套标准，属「看着真
其实是假的」。

## 二、方案（用户已裁定 B）

**初始档位文本改为 `2.400 MS/s（默认·未确认）`**，连接成功后由
`refresh_sample_rate_options_from_active_backend()` 重建为真实档位（纯
`format_sample_rate(rate)`，不带标注）。

不选 A（未连接显示 `—`）的理由：下拉框仍需要一个可展开的说明项；且预填
档位用于连接前的配置编辑，直接置空会让「未连接时改采样率」的偏好丢失。

### 落点 1：初始预填后加标注

`control_panel.py:577-578` 之后：

```python
        self._populate_sample_rate_options(
            self._default_sample_rate_options(), DEFAULT_SAMPLE_RATE_HZ)
        # 未连接时这一档只是性能档默认值，没有设备确认过。标出来，
        # 与同面板「LNA — / 频率 —」的诚实空态保持一致；连接后重建为纯档位。
        _def_idx = self.sample_rate_combo.findData(float(DEFAULT_SAMPLE_RATE_HZ))
        if _def_idx >= 0:
            self.sample_rate_combo.setItemText(
                _def_idx,
                f"{format_sample_rate(DEFAULT_SAMPLE_RATE_HZ)}（默认·未确认）")
```

### 落点 2：重建时自然清除

`_populate_sample_rate_options()` 每次 `combo.clear()` + `addItem(format_sample_rate(...))`，
标注不会被继承——连接后 `set_sample_rate_options()` 一进来就恢复纯文本，
无需额外的清除分支。

### 落点 3：tooltip 补一句口径

`sample_rate_combo` 的 tooltip（`:574-576`）追加：

> 未连接时显示「默认·未确认」的是性能档默认值，连接后按设备能力重建。

## 三、测试设计（待文件空闲后新建）

建议放 `tests/test_control_panel_sample_rate_default.py`（若已有 control_panel
测试文件则并入，避免重复 QApplication fixture）：

1. `test_unconnected_sample_rate_is_marked_default`
   - 新建 `ControlPanel()`；
   - `combo = panel.sample_rate_combo`；
   - 断言 `combo.currentData() == DEFAULT_SAMPLE_RATE_HZ`；
   - 断言 `"默认" in combo.currentText()` 且 `"未确认" in combo.currentText()`；
   - 断言同一面板 `sr_label` 仍为「采样率」、且 goal 对照项（LNA/频率）不是
     本用例职责，不做脆弱断言。
2. `test_connected_rebuild_clears_default_mark`
   - 先 `panel.set_sdr_connected(True)`，再
     `panel.set_sample_rate_options([1.024e6, 2.4e6], 2.4e6)`（或直接
     monkeypatch 一个 `get_sample_rate()` 返回 2.4e6 的假后端 + 调
     `refresh_sample_rate_options_from_active_backend()`）；
   - 断言所有 `itemText(i)` 都不含「默认」「未确认」；
   - 断言 `currentData() == 2.4e6`。
3. `test_unconnected_unknown_backend_keeps_honest_unknown`
   - `set_sample_rate_options(None)` → 文本含「未知」，`isEnabled()` 为 False
     （防标注改动破坏既有未知态）。

## 四、变异反证

- 删掉落点 1 的 `setItemText` → 用例 1 必红；
- 把标注写进 `_populate_sample_rate_options()` 的 `addItem`（即连接后也带
  标注）→ 用例 2 必红；
- 让 `_populate_sample_rate_options()` 在 `options=None` 时也预填默认档 →
  用例 3 必红。

## 五、验收

- `python -m pytest tests/test_control_panel_sample_rate_default.py -q` 全绿；
- 既有 control_panel / 面板集成用例不回归（布局线改动后的文件上复跑）；
- 目测：未连接时下拉显示 `2.400 MS/s（默认·未确认）`，连接后展开只显示
  `1.024 MS/s`、`2.400 MS/s` 等纯档位。
