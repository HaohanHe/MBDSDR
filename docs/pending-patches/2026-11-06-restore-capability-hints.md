# 待落库：数值型配置恢复项的「被替换必须可见」补丁（设置架构线，2026-11-06）

## 为什么在 pending-patches

| 目标文件 | 状态 | 处理 |
|---|---|---|
| `desktop/control_panel.py` | 采样率真值线已 `RELEASE OK`（仅只读核查） | 已提交 `abc52bd8`（与两个测试文件同一笔） |
| `tests/test_settings_robustness_extras.py` | 设置架构线 | 已提交 `abc52bd8`（robustness 12 passed，含 6 项新增 + 变异反证） |
| `tests/test_ui_stability_resources.py` | 设置架构线 | 已提交 `abc52bd8`（NaN 增益断言改为按不变式比较数值） |
| `docs/settings-inventory.md` | 设置架构线 | 已提交 `90f76caa`（§18.9 审计表 + §18.10 判定口径，纯文档） |

## 工作区现有改动（`desktop/control_panel.py`，勿回退）

若 `git diff -- desktop/control_panel.py` 看到以下七处，都是本补丁内容：

1. 模块级 `_format_frequency_hz()`（提示文案的人读频率口径）；
2. `_build_ui` 记录三处静态 tooltip：`_freq_input_base_tip` / `_vfo_bw_base_tip` / `_gain_base_tip`；
3. `_set_freq_hz`：用户手动调谐后还原 `freq_input` 静态说明（清除恢复期提示）；
4. `set_frequency_hz`：全局 [10 kHz, 6 GHz] 钳位时 tooltip 追加「⚠ 配置请求 X 低于/高于全局调谐范围 […]，已按边界 Y 显示（配置文件未改）」+ `logger.warning`；未钳位还原；
5. `set_gain`：未连接 [0, 49] dB 钳位时同款 ⚠/warning；`set_gain_range`：`setRange` 前后 `blockSignals`（防 QSlider 夹值经 `_on_gain_changed` 写回配置）、夹值时 ⚠/warning、静态说明随设备能力更新；
6. `set_vfo_bandwidth`：NaN 直接返回（不伪造成 500 Hz）；最近档替换时 ⚠/warning；精确命中还原；
7. `_on_gain_changed` / `_on_vfo_bw_changed` / `set_mode`：用户操作或模式切换后清除恢复期提示。

采样率（`set_sample_rate`）上一轮已修，本补丁不重复。

## 落库时怎么验

```bash
python -m pytest tests/test_settings_robustness_extras.py -q
python -m pytest tests/test_settings_ui_coverage.py tests/test_settings_convergence.py \
  tests/test_settings_schema.py tests/test_settings_semantics.py -q
python scripts/settings_inventory.py --check
```

本地实测（2026-11-06）：robustness 12 passed；五套件 237 passed；`--check`「文档块一致」。

## 与其它线的边界

- `desktop/main_window.py`（操作逻辑线持有）：本轮**未改**。频率连接下发的设备能力校验、`status_sr` 判定口径见
  `docs/settings-inventory.md` §18.10，按那里的补丁口径落库即可。
- 若 `采样率真值线` 正在改同文件的采样率显示：本补丁只动了「恢复 setter + 提示还原」，
  与 `set_sample_rate` 的实现互不覆盖；合并时保留双方的 `logger.warning` 与 tooltip 文本即可。
