# 交接单：SoapySDR 连接后把采样率范围登记为 probed（让采样率下拉能回填）

> 提出：2026-11-11，设备缺口修复线。
> 落点：`mbdsdr_ai/sdr_backend.py` `SoapySDRBackend.readback_hw_state()`
> （约 `:3797-3819`）。
> 状态：**已落地**。原 claim（roadmap-W72W77）过期且 `no_inflight=PASS`，
> 本线接管后实施；配套用例见 `tests/test_soapy_readback_truth.py`
> 的两条 `test_sample_rate_range_probe_*`。

## 一、缺陷

`readback_hw_state()` 已把 `getSampleRateRange()` 的真实范围写进
`self.device.sample_rate_range`，但**没有登记来源**。于是
`device_capabilities.sample_rate_options_for_backend()` 仍按
`capability_status["sample_rate_range"]` 缺失处理 → 返回 `None`：

- 连接前：下拉显示「档位未知，连接设备后探测」（正确）；
- 连接后：硬件范围已经拿到，下拉仍停在「档位未知（设备未标注来源）」，
  **无法回填**（新的假空态）。

## 二、方案（最小改动）

把 `readback_hw_state()` 里合并的 `try` 拆开，采样率单独用
`_range_tuple_strict()`（查不到就不改、也不标 probed）：

```python
        # 用硬件真实范围覆盖构造时的占位范围
        try:
            fr = self._sdr.getFrequencyRange(self._rx, self._chan)
            self.device.frequency_range = self._range_tuple(
                fr, self.device.frequency_range)
        except Exception:
            pass
        try:
            sr = self._sdr.getSampleRateRange(self._rx, self._chan)
            rng = self._range_tuple_strict(sr)
            if rng is not None:
                self.device.sample_rate_range = rng
                # 真的从驱动查到了范围：来源登记为 probed，界面才允许据此
                # 生成可见档位（缺省=unknown，绝不回退成 declared）。
                self.device.capability_status["sample_rate_range"] = "probed"
        except Exception:
            pass
        try:
            gr = self._sdr.getGainRange(self._rx, self._chan)
            self.device.max_gain = float(gr.maximum())
        except Exception:
            pass
```

不做的：不要顺手把 `freq_range`/`gain_range` 也标 probed（本单只收采样率，
避免把未验证的能力面一起放开）。

## 三、验证

在 `tests/test_soapy_readback_truth.py` 补两条 Soapy 集成用例（已落地）：

1. 构造 `SoapySDRBackend(device_info={})`（枚举只给身份、`capability_status`
   全 unknown）；
2. 注入假 `_sdr`：`getSampleRateRange()` 返回 `[(2e6, 8e6)]`；
3. 调 `readback_hw_state()`；
4. 断言 `device.capability_status["sample_rate_range"] == "probed"`；
5. 再调 `device_capabilities.sample_rate_options_report(backend)`：
   `state == "known"`、`options == [2_000_000, 8_000_000]`（连续可调后端只给
   两端，见 `sample_rate_options` 规则）；
6. 反证：把 `getSampleRateRange` 改成抛异常 → 状态仍是 `unattributed`
   （范围占位值还在，但没有来源），不得出现 MS/s 档位。

## 四、界面侧已就位（无需改 `main_window.py`）

`desktop/control_panel.py` 的 `ControlPanel.set_connected(True)` 已调用
`refresh_sample_rate_options_from_active_backend()`；主窗口
`_panels_set_sdr_connected(True)` 在 `_active_sdr_backend = backend` 之后调用
它，所以 `connect()` 里 `readback_hw_state()` 一旦登记 probed，下拉立即从
「档位未知，连接设备后探测」变为真实档位。
