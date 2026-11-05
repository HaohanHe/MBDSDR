# 桌面失败回填补丁计划：PPM / Offset / 带宽三处控件收口

> 生成：2026-11-06 操作逻辑线（**只出计划，不改代码**）
> 基线：HEAD `cd1dd18d`（采样率 / AGC / 增益三处已闭环，本计划收剩余三处）
> 目标文件：`desktop/control_panel.py`（新增两个入口）+ `desktop/main_window.py`（三处调用）
> **占用**：`desktop/control_panel.py` 当前由参数治理线持有（布局线 `set_mode`
> 修 bug）。实施前先查 `D:\mbdsdr\.locks\claims.json`；若对方仍在改，由对方
> 出同样粒度的 patch，本线只负责 `main_window` 侧调用与测试。

## 1. 现状（`cd1dd18d` 之后仍存在的三处）

| 操作 | 失败信号 | 控件当前停在哪 | 结论 |
|---|---|---|---|
| PPM | `set_ppm` 返回 `False` / 抛异常（`_apply_hw_action`，`main_window.py:5209-5222`） | `control_panel.ppm_spin` 停在用户刚输入的请求值 | ✗ 显示从未生效的数 |
| Offset Tuning | `set_offset_tuning` 返回 `False` / 抛异常（`main_window.py:5224-5238`） | `offset_check` 停在用户刚切的新状态 | ✗ 显示从未生效的数 |
| VFO 带宽 | `set_bandwidth` 返回 `False` / 抛异常（`main_window.py:5240-5258`） | `vfo_bw_combo` 停在用户刚选的新档（`_vfo_bw` 主窗口侧已是旧值） | ✗ 控件与真值分叉 |

三处共同点：失败时状态栏**已经**由 `_apply_hw_action` 写成「XX 未生效：…」，
所以问题只在下游——控件没有跟着回填，用户看到的是失败请求值。这与
`7bea149a` / `cd1dd18d` 修掉的采样率、AGC、增益是同一形态。

**没有回读接口**：`mbdsdr_ai/sdr_backend.py` 与 `rtltcp_client.py` 只有
`set_ppm` / `set_offset_tuning`，没有 `get_ppm` / `get_offset_tuning`。
所以这两项的「设备真实值」不可观测，只能显示**最后一次确认生效的请求值**，
并明确标注来源（见 §3 回填值策略）。

## 2. `control_panel` 需要提供的入口

### 2.1 新增 `set_ppm_applied(value: int) -> bool`

```python
def set_ppm_applied(self, value: int) -> bool:
    """回填最后一次确认生效的 PPM 值；blockSignals，不发射 ppm_changed。"""
```

- `ppm_spin` 范围 `[-1000, 1000]`（`control_panel.py:609-616`），越界值夹取后
  返回 `False`（视为不可渲染，调用方保留原显示）。
- 写入后把 `ppm_spin.setToolTip(...)` 标成「设备读数/最后生效值」；若它与
  用户最近一次请求不同，追加上一句「请求 X ppm 未生效」——与
  `set_gain_applied`（`control_panel.py:1333-1360`）的 hint 口径一致。
- 不写配置、不落盘（失败值不许变成下次连接的用户意图）。

### 2.2 新增 `set_offset_tuning_applied(enabled: bool) -> bool`

```python
def set_offset_tuning_applied(self, enabled: bool) -> bool:
    """回填最后一次确认生效的 Offset Tuning 状态；blockSignals。"""
```

- 直接 `offset_check.setChecked(bool(enabled))` + tooltip 说明；恒返回 `True`
  （布尔没有「不可渲染」形态）。

### 2.3 带宽复用既有 `set_vfo_bandwidth(bw_hz)` —— 不新增入口

`control_panel.py:1496-1528` 已能「按最近档显示 + 标 ⚠」，失败回填直接调它：
用主窗口的旧值 `self._vfo_bw`（只在后端成功后更新，见
`main_window.py:5259`）即可；不需要 `set_vfo_bandwidth_applied`。

## 3. `main_window` 侧调用点与回填值策略

### 3.1 新增两个「最后已知生效值」状态位

- `self._last_ppm_applied: int | None`
- `self._last_offset_applied: bool | None`

初始化：`_connect_backend` 在 `_apply_connect_params(...)`
（`main_window.py:3873-3876`）返回 `failures` 之后写：

| 连接时该项 | 写入值 |
|---|---|
| 不在 `failures` | 本次连接传入的 `ppm` / `offset_tuning` |
| 在 `failures` | 连接默认（PPM `0` / Offset `False`）——设备从未确认过任何值 |

连接默认来源：`_connect_dialog` 的 `ppm_spin` 初值 0、`offset_check` 初值
`False`（`main_window.py:3995-4000`、`4103`）。**不新增设置键**：现状这两项
不落盘，计划也不引入持久化（避免把失败值带进下次连接）。

### 3.2 PPM（`_on_ppm_changed`，`main_window.py:5209`）

```
ok = self._apply_hw_action("设置 PPM", lambda: set_ppm(int(ppm)))
if not ok:
    revert = self._last_ppm_applied if ... is not None else 0
    self.control_panel.set_ppm_applied(revert)
    return
self._last_ppm_applied = int(ppm)
```

失败值不更新 `_last_ppm_applied`，也不写 `settings`。

### 3.3 Offset（`_on_offset_tuning_changed`，`main_window.py:5224`）

```
ok = self._apply_hw_action("Offset tuning", lambda: set_ot(bool(enabled)))
if not ok:
    revert = self._last_offset_applied if ... is not None else False
    self.control_panel.set_offset_tuning_applied(revert)
    return
self._last_offset_applied = bool(enabled)
```

### 3.4 带宽（`_on_vfo_bandwidth_changed`，`main_window.py:5240`）

在 `return False` 之前加一行回填（`_vfo_bw` 此刻仍是上次成功值）：

```
if not self._apply_hw_action("设置带宽", lambda: backend.set_bandwidth(bw)):
    self.control_panel.set_vfo_bandwidth(self._vfo_bw)   # 下拉回到最后生效档
    return False
```

注意 `_on_apply_demod_requested`（`main_window.py:7681-7697`）依赖本函数返回
`False` 来决定能否说「已应用解调」，回填不得改变返回值语义。

## 4. 行为测试与反证设计（每项一条，改回旧行为必须红）

文件名（合并时新建）：`tests/test_hw_rollback_ppm_offset_bandwidth.py`

1. **PPM 拒绝**：`backend.set_ppm` 返回 `False` + `status.error`；
   先 `control_panel.ppm_spin.setValue(25)` 并让
   `_last_ppm_applied = 25`，再 `setValue(50)` 触发 `_on_ppm_changed(50)`。
   断言 `ppm_spin.value() == 25`。
   **反证**：删掉 `set_ppm_applied(revert)` 调用 → 控件停在 50 → 必红。
2. **Offset 拒绝**：`set_offset_tuning` 返回 `False`；
   先让 `offset_check` 处于已生效的 `True`，用户切到 `False`。
   断言 `offset_check.isChecked() is True`。
   **反证**：删回填 → 停在 `False` → 必红。
3. **带宽拒绝**：`set_bandwidth` 返回 `False`；
   主窗口 `_vfo_bw = 12_500.0`，下拉由用户选到 `180_000.0`（档位需在
   `vfo_bw_combo` 内）。断言 `cp.vfo_bw_combo.currentData() == 12_500.0`
   且 `_on_vfo_bandwidth_changed` 返回 `False`。
   **反证**：删回填 → 停在 180 kHz → 必红。

三条都要求「状态栏已有失败文案」——防止只回填控件、失败仍不可见：
断言 `statusBar().currentMessage()` 含「未生效」。

## 5. 实施顺序（占用释放后）

1. 参数治理线 / 本线（谁拿到 `control_panel.py` 写权限）先落 §2 两个入口；
   同一提交内加 `control_panel` 侧最小单测（blockSignals 不发射信号）。
2. 本线落 §3 三处 `main_window` 调用 + §4 三条用例。
3. 三处同时改回旧行为跑一次，必须 `3 failed`；恢复后 `3 passed`。
4. `main_window.py` 之外不插队改任何文件（`control_panel` 改动仅限 §2 两个
   方法，不与布局线 `set_mode` 修复同笔提交）。

## 6. 已知边界（不在本补丁范围）

- `_on_ppm_changed` / `_on_offset_tuning_changed` 在**无后端**时直接
  `return`（`main_window.py:5215-5216`、`5231-5232`）：控件停在用户值但
  没有状态栏提示。这不是「失败谎报成功」，是「无设备时静默」；是否补提示
  需先定义未连接态语义（与 `set_sdr_connected(False)` 置灰策略配套），单列。
- 两项都没有回读接口，本计划只保证「不显示未生效值」，不保证「显示设备
  真值」——后者需要后端新增 `get_ppm` / `get_offset_tuning`。
