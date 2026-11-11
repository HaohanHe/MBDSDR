# 交接单：W76 D-1 能力口径的界面接线（待持有线落地后接线）

> 来源：W76 策论线，提交 `450d452e`（`mbdsdr_ai/device_capabilities.py` 策略层）。
> 状态：**策略层已落库、可直接消费；界面消费点尚未接线**。
> 本单只写到「谁在哪一行怎么接」，不含策略改动；接线时**不要再改
> `device_capabilities.py` 的判定**（开关只有 probed 且 True 才 enabled）。
>
> 目标文件（当前被其他线改到一半，未抢）：
> `desktop/control_panel.py`、`desktop/device_panel.py`（必要时
> `desktop/main_window.py` 只做信号转发）。开工前先
> `python scripts/file_claim.py status` 确认空闲，且 `git status` 该路径为空。

## 0. 口径（照抄，别改语义）

**量程可以先信后验，开关必须验了才信。**

- 读数量程类（freq / sample_rate / gain / bandwidth）：允许展示驱动声明值，
  但必须带「未验证」；允许用户输入，越界或设置失败如实报错，**不静默、
  不夹取后当成功**。
- 开关类（bias_tee / agc / offset_tuning / direct_sampling）：只有
  `probed` 且标志为 True 才可点；声明/未知一律置灰；未知必须给
  「重新探测」入口。

## 1. 策略 API（唯一真值源）

`mbdsdr_ai/device_capabilities.py`：

| 符号 | 用途 |
|---|---|
| `SWITCH_KEYS` | `("bias_tee", "agc", "offset_tuning", "direct_sampling")` |
| `SWITCH_LABELS` | 界面中文名：偏置电压 / AGC / Offset 调谐 / 直采 |
| `SWITCH_ATTRS` | 设备对象上的布尔标志名（见下表） |
| `SWITCH_UNKNOWN_TOOLTIP` | **原文**「未检测到该能力，无法确认」 |
| `SWITCH_DECLARED_TOOLTIP` | 「仅驱动声明支持、未探测；开关类必须验了才信」 |
| `SWITCH_RESCAN_LABEL` | 「重新探测」 |
| `build_switch_report(device)` | 返回 `{key: SwitchCapability}`，一次取全四项 |
| `SwitchCapability` | `enabled`（唯一可点判据）/ `text` / `tooltip` / `can_rescan` / `state` |

`device` 可以是枚举 dict，也可以是已连接后端对象；也接受
`backend.device`。后端管理器快照已带来源字段（`mbdsdr_ai/sdr_backend.py:5422-5425`
的 `capability_status` / `capability_note`），不要自己从 `supports_*`
猜来源。

## 2. 控件 ↔ 能力对照

| 能力 key | 现有控件 | 点击/切换后调用的后端 API |
|---|---|---|
| `agc` | `control_panel.py:649` `self.agc_check`（「硬件 AGC」），信号 `agc_changed`（`control_panel.py:187`） | `set_agc(bool)`（`sdr_backend.py:574`） |
| `offset_tuning` | `control_panel.py:696` `self.offset_check`（「偏移调谐」），信号 `offset_tuning_changed`（`control_panel.py:190`） | `set_offset_tuning(bool)`（`sdr_backend.py:2617` / `rtltcp_client.py:866`） |
| `bias_tee` | **当前无控件**，需新增 `self.bias_tee_check = QCheckBox("偏置电压")`，位置建议紧跟 `offset_check`（同一 `rx_layout`），信号 `bias_tee_changed = Signal(bool)` | `set_bias_tee(bool)`（`sdr_backend.py:2598` / `rtltcp_client.py:852`） |
| `direct_sampling` | **当前无控件**，需新增（二选一：QCheckBox「直采」或档位 QComboBox），信号 `direct_sampling_changed` | `set_direct_sampling(branch="q")`（`sdr_backend.py:2581` / `rtltcp_client.py:857`） |

新增控件必须同时补 tooltip（遵守 `docs/discoverability.md` 三条规矩），
并在 `tests/test_tooltip_guard.py` 的基线外自证长度 ≥12、不与可见文字逐字相同。

## 3. 接线骨架（照此实现，判据全部来自 `SwitchCapability`）

```python
from mbdsdr_ai.device_capabilities import (
    build_switch_report, SWITCH_RESCAN_LABEL, SWITCH_UNKNOWN_TOOLTIP)

def _apply_switch_caps(self, device):
    caps = build_switch_report(device)
    for key, widget, rescan in (
            ("agc", self.agc_check, self.agc_rescan_btn),
            ("offset_tuning", self.offset_check, self.offset_rescan_btn),
            ("bias_tee", self.bias_tee_check, self.bias_rescan_btn),
            ("direct_sampling", self.direct_check, self.direct_rescan_btn)):
        cap = caps[key]
        widget.blockSignals(True)           # 刷新能力时不许把旧值回写后端
        widget.setEnabled(cap.enabled)
        widget.setToolTip(cap.tooltip or cap.text)
        widget.blockSignals(False)
        rescan.setText(SWITCH_RESCAN_LABEL)
        rescan.setVisible(cap.can_rescan)   # False 时整颗按钮隐藏，不留灰按钮
        rescan.setEnabled(cap.can_rescan)
        rescan.setToolTip("重新枚举/重连设备后再判断该能力")
```

触发时机（缺一不可）：

1. 设备下拉切换：`module_panel._on_device_selected`（`module_panel.py:566`）
   与 `control_panel` 选设备处，用 `self._devices[idx]` 调一次；
2. 连接成功 / 断开：`set_connected(...)` 后改用 `backend.device` 调一次
   （断开后回到设备 dict 的声明/未知状态）；
3. 每次点「重新探测」成功后（见下）再调一次。

## 4. unknown 的置灰与 tooltip（逐项验收点）

- `cap.enabled is False` → 控件 `setEnabled(False)`；**checkbox 已勾选时也要
  强制置灰**，不得因为「上次开过」而保持可点。
- 未知 tooltip 必须**逐字**为「未检测到该能力，无法确认」
  （直接用 `SWITCH_UNKNOWN_TOOLTIP`，不要自己拼）。
- 声明态（`state == "declared"`）同样置灰，tooltip 用
  `SWITCH_DECLARED_TOOLTIP`，控件文字可显示 `cap.text`（含「未验证」）。
- `cap.can_rescan is False`（已探测到不支持）→ 隐藏「重新探测」按钮；
  tooltip 用 `cap.tooltip`（「设备探测结果：不支持该能力」），不要指向
  一个永远探不出结果的按钮。
- 置灰 ≠ 静默：点击路径若仍被信号触发（例如程序回填），后端返回 False 时
  状态行要如实写「XX 设置失败/不支持」，不得忽略返回值。

## 5. 「重新探测」入口与触发 API

挂载位置：每个开关控件同一行右侧（建议 `QToolButton`，`text =
SWITCH_RESCAN_LABEL`），或四个开关共用一颗放在 rx 分组标题栏——二选一，
但要与 `can_rescan` 逐项联动（隐藏/显示）。

触发后走哪条 API（**现状没有单一 `probe_capabilities()`，别造**）：

- 未连接：`module_panel._on_refresh`（`module_panel.py:531` 附近，
  内部调 `enumerate_all_sdr_devices()`，`sdr_backend.py:5693`），刷新后
  重取 `self._devices[idx]` 的能力；
- 已连接：按 `main_window` 现有「断开→重连」路径重开设备，成功后重取
  `backend.device.capability_status`；
- 按钮点击后 1.2 s 文案反馈（可复用 `refresh_btn` 的 `QTimer.singleShot`
  写法，`module_panel.py:552-554`）。
- 若后续有人新增后端级 `probe_capabilities()`，**只替换这一处调用**，
  不要改本节以外的判据。

## 6. 量程类 declared 的「未验证」与越界报错落点

- 文案真值：`CapabilityField.text` 已自带「声明支持，未验证」，不要在 UI
  重新拼数字或去掉后缀。
- 展示点：
  - `module_panel.device_info`（`module_panel.py:564`，经 `summarize_device`）；
  - `device_panel.status_label` 的 tooltip（`device_panel.py:436`，经
    `summarize_device`）；
  - 采样率档位未确认标记：`control_panel._mark_sample_rate_unconfirmed`
    （`control_panel.py:1660-1684`，默认档「默认 · 未确认」，其余声明档
    「（未确认）」）。
- 越界/失败（保持现状、不要夹取）：
  - 采样率越界：`device_panel.py:483-497`，`QMessageBox`「采样率超出设备能力」；
  - 增益越界：`device_panel.py:505-518`，「增益超出设备能力」；
  - 范围未知：`status_label` 写「采样率/增益范围未知，无法校验；将以 X 下发」，
    这是**警告不是通过**，不得改成静默下发。
  - 后端 `set_*` 返回 False → 状态行必须出现失败文案；不要 `try/except: pass`。

## 7. 接线时必须补的测试（每条都要能「改回旧行为变红」）

1. 四个开关 `unknown` → `isEnabled() is False` 且 `toolTip()` 逐字等于
   「未检测到该能力，无法确认」，「重新探测」按钮可见；
2. `declared` → 仍置灰，tooltip 含「未验证」，重探按钮仍在；
3. `probed + True` → 可点；`probed + False` → 置灰且重探按钮**隐藏**；
4. 切换设备/连接成功后能力刷新一次（用 fake device / fake backend 断言
   `setEnabled` 变化）；
5. 量程 `declared` 在 `device_info` / `status_label` tooltip 里出现「未验证」；
6. 越界仍弹「超出设备能力」且**不**把输入夹到边界（断言输入框文本未被改）。

验收：上述测试 + `tests/test_device_capabilities_switch_policy.py` 全绿；
把任意一条置灰分支改回 `setEnabled(True)` 必须变红。

## 8. 明确不做

- 不在 UI 里重新实现一套能力判定（只消费 `build_switch_report`）；
- 不把 `unknown` 默认成 False/True 后放行；
- 不对越界值做静默夹取；
- 不在本单内改 `mbdsdr_ai/device_capabilities.py`（策略变更另开单）。
