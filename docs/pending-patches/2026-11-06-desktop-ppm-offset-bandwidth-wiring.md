# 桌面失败回填补丁计划：PPM / Offset / 带宽三处控件收口

> 生成：2026-11-06 操作逻辑线（**只出计划，不改代码**）
> 基线：HEAD `cd1dd18d`（采样率 / AGC / 增益三处已闭环，本计划收剩余三处）
> 目标文件：`desktop/control_panel.py`（新增两个入口）+ `desktop/main_window.py`（三处调用）
> **占用**：`desktop/control_panel.py` 已由硬编码线交接给本线（它三次查证：
> `claims.json` 无该文件条目、`release` 返回 `RELEASE DENIED`、只提交
> `docs/hardcoded-parameters.md`）。本线以本会话标识 claim 后直接实施，不等。

## 1. 现状（`cd1dd18d` 之后仍存在的三处）

| 操作 | 失败信号 | 控件当前停在哪 | 结论 |
|---|---|---|---|
| PPM | `set_ppm` 返回 `False` / 抛异常（`_apply_hw_action`，`main_window.py:5215-5228`） | `control_panel.ppm_spin` 停在用户刚输入的请求值 | ✗ 显示从未生效的数 |
| Offset Tuning | `set_offset_tuning` 返回 `False` / 抛异常（`main_window.py:5231-5244`） | `offset_check` 停在用户刚切的新状态 | ✗ 显示从未生效的数 |
| VFO 带宽 | `set_bandwidth` 返回 `False` / 抛异常（`main_window.py:5247-5264`） | `vfo_bw_combo` 停在用户刚选的新档（`_vfo_bw` 主窗口侧已是旧值） | ✗ 控件与真值分叉 |

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

初始化：`__init__` 里置 `None`（`main_window.py:809` 附近，与 `_vfo_bw` 同处），
`_apply_connect_params`（`main_window.py:4640`）里按 `_run(...)` 的结果写：

| 连接时该项 | 写入值 |
|---|---|
| `_run` 成功 | 本次连接传入的 `ppm` / `offset_tuning` |
| `_run` 失败（返回 None） | 连接默认（PPM `0` / Offset `False`）——设备从未确认过任何值 |

连接默认来源：`_connect_dialog` 的 `ppm_spin` 初值 0、`offset_check` 初值
`False`（`main_window.py:3995-4000`、`4103`）。**不新增设置键**：现状这两项
不落盘，计划也不引入持久化（避免把失败值带进下次连接）。

### 3.2 PPM（`_on_ppm_changed`，`main_window.py:5215`）

```
ok = self._apply_hw_action("设置 PPM", lambda: set_ppm(int(ppm)))
if not ok:
    revert = self._last_ppm_applied if ... is not None else 0
    self.control_panel.set_ppm_applied(revert)
    return
self._last_ppm_applied = int(ppm)
```

失败值不更新 `_last_ppm_applied`，也不写 `settings`。

### 3.3 Offset（`_on_offset_tuning_changed`，`main_window.py:5231`）

```
ok = self._apply_hw_action("Offset tuning", lambda: set_ot(bool(enabled)))
if not ok:
    revert = self._last_offset_applied if ... is not None else False
    self.control_panel.set_offset_tuning_applied(revert)
    return
self._last_offset_applied = bool(enabled)
```

### 3.4 带宽（`_on_vfo_bandwidth_changed`，`main_window.py:5247`）

回填值 = **设备真实带宽**；`get_bandwidth()` 不可用或失败 → 最后已知生效值
`_vfo_bw`（与采样率 `main_window.py:5134-5139` 的「回读失败用最后已知值」同一策略）。
新增私有助手：

```
def _device_bandwidth_or_last_known(self) -> float:
    backend = self._active_sdr_backend
    get_bw = getattr(backend, "get_bandwidth", None)   # 只有 BladeRF/rtl_tcp 提供
    if callable(get_bw):
        try:
            v = float(get_bw())
            if math.isfinite(v) and v > 0:
                return v
        except Exception: ...日志...
    return float(self._vfo_bw)
```

在 `return False` 之前回填（`_vfo_bw` 此刻仍是上次成功值）：

```
if not self._apply_hw_action("设置带宽", lambda: backend.set_bandwidth(bw)):
    self.control_panel.set_vfo_bandwidth(self._device_bandwidth_or_last_known())
    return False
```

注意 `_on_apply_demod_requested`（`main_window.py:7681`）依赖本函数返回
`False` 来决定能否说「已应用解调」，回填不得改变返回值语义。

## 4. 行为测试与反证设计（每项一条，改回旧行为必须红）

落点：并入既有 `tests/test_hw_rollback_shows_device_truth.py`（该文件的
「阻塞两处」以真实用例收口，不留占位注释）。

1. **PPM 拒绝**：`backend.set_ppm` 返回 `False` + `status.error`；
   先 `control_panel.ppm_spin.setValue(25)` 并让
   `_last_ppm_applied = 25`，再 `setValue(50)` 触发 `_on_ppm_changed(50)`。
   断言 `ppm_spin.value() == 25`。
   **反证**：删掉 `set_ppm_applied(revert)` 调用 → 控件停在 50 → 必红。
2. **Offset 拒绝**：`set_offset_tuning` 返回 `False`；
   先让 `offset_check` 处于已生效的 `True`，用户切到 `False`。
   断言 `offset_check.isChecked() is True`。
   **反证**：删回填 → 停在 `False` → 必红。
3. **带宽拒绝**：`set_bandwidth` 返回 `False`；后端 `get_bandwidth()` 返回
   `12_500.0`（设备真实值）；下拉由用户选到 `80_000.0`。断言
   `cp.vfo_bw_combo.currentData() == 12_500.0` 且本函数返回 `False`。
   **反证 A**：删回填 → 停在 80 kHz → 必红；
   **反证 B**：把回填值改回用户请求 `bw` → 停在 80 kHz → 必红。
4. **带宽回读也失败**：后端无 `get_bandwidth`（或抛异常）+ `_vfo_bw = 12_500.0`；
   断言下拉退回 `12_500.0`（最后已知值），不回落到 0/请求值。

四条都要求「状态栏已有失败文案」——防止只回填控件、失败仍不可见：
断言 `statusBar().currentMessage()` 含「未生效」。

## 5. 实施顺序

1. 本线先落 §2 两个入口（`control_panel.py`，已由硬编码线交接）。
2. 本线落 §3 三处 `main_window` 调用 + §4 四条用例（并入
   `tests/test_hw_rollback_shows_device_truth.py`）。
3. 每处改回旧行为跑一次必红；恢复后全绿。
4. `control_panel.py` 的改动仅限 §2 两个方法与本文件既有回填方法，不与
   布局线 `set_mode` 修复同笔提交。

## 6. 已知边界（不在本补丁范围）

- `_on_ppm_changed` / `_on_offset_tuning_changed` 在**无后端**时直接
  `return`（`main_window.py:5221-5222`、`5237-5238`）：控件停在用户值但
  没有状态栏提示。这不是「失败谎报成功」，是「无设备时静默」；是否补提示
  需先定义未连接态语义（与 `set_sdr_connected(False)` 置灰策略配套），单列。
- 两项都没有回读接口，本计划只保证「不显示未生效值」，不保证「显示设备
  真值」——后者需要后端新增 `get_ppm` / `get_offset_tuning`。
