# 交接单：P0-01 执行层闸门（`_poll_satellite_track` 下发调谐前读 policy）

> 来源：`desktop/main_window.py` 当前由 `启动工单线/W25-续` 持有（claim 到
> 2026-11-11 23:30），本条改动落在同一个文件的 `_poll_satellite_track`，
> 不抢文件，改由持锁方应用，或等其 release 后由本线应用。
> 测试已写好并在当前树上跑红（3 failed / 8 passed），红因正是缺这段。

## 一、缺陷（实测，非推断）

```
python scripts/repro/2026-11-11-p0-01-dead-tle-tracking-gate.py
→ 实测 B：policy=deny + 仰角 45° 跑一拍 _poll_satellite_track
  实际：_on_tune_sdr 调用 1 次 [(145800000.0, 'FM')]
  期望：0 次
  反证对照（显式读 policy.allow_tune 的等价实现）：0 次
结论：复现 P0-01 —— 数据层已给 allow_tune=False，执行层照做。
```

开始闸门（提交 `07167624`）只挡「能不能进入跟踪」；跟踪中途星历被刷新 /
身份复核变 dead 时，500 ms 轮询仍会下发调谐。

## 二、改法（一处 hunk，仅 `_poll_satellite_track`）

将：

```python
        tune_hz = comp.rx_freq()
        if tune_hz is None:
            tune_hz = float(self._loop_sat_tracker.downlink_freq_hz())
        try:
            self._on_tune_sdr(float(tune_hz), self._sat_track_mode)
        except Exception:
            pass
```

改为：

```python
        tune_hz = comp.rx_freq()
        if tune_hz is None:
            tune_hz = float(self._loop_sat_tracker.downlink_freq_hz())
        # 执行层闸门（P0-01）：本次位置结果自带 policy（= tracking_policy_for()
        # 同一份），allow_tune=False 就绝不下发。开始时的闸门只管「能否进入」，
        # 跟踪中途星历可能被刷新/身份复核，故每一拍下发前都要再读一次；
        # 这里不比较 tle_tier/age_days——档位只在 tle_integrity 一处判定。
        tune_policy = pos.get("policy") or {}
        if tune_policy.get("allow_tune"):
            self._sat_tune_blocked_reason = ""
            try:
                self._on_tune_sdr(float(tune_hz), self._sat_track_mode)
            except Exception:
                pass
        else:
            blocked = (
                f"{tune_policy.get('label') or '星历不可用'}"
                f"（{tune_policy.get('reason') or '无可用星历'}）"
                f" → {tune_policy.get('next_action') or '请更新星历'}")
            if blocked != getattr(self, "_sat_tune_blocked_reason", ""):
                self._sat_tune_blocked_reason = blocked
                self.statusBar().showMessage(
                    f"未下发调谐：{blocked}", MSG_IMPORTANT)
```

要点：

- policy 取 `pos["policy"]`：`SatelliteTracker.current_position()` 的两条
  返回路径（含 empty）都带 `"policy": self.tracking_policy_for()`，与
  数据层同一份，不在桌面另取一次。
- 缺 `policy`（桩/异常）按禁止处理（fail-closed，与 `no_data` 同口径）。
- 去重：同一句原因只写一次状态栏，避免 500 ms 刷屏。

## 三、验收（应用后必须两条都绿）

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python -m pytest tests/test_sat_tracking_gate.py -q      # 期望 11 passed
python scripts\repro\2026-11-11-p0-01-dead-tle-tracking-gate.py   # 期望 EXIT=1（未复现）
```

测试覆盖（已在 `tests/test_sat_tracking_gate.py` 追加）：

- `dead` / `unknown` → 下发 0 次，状态栏含「未下发调谐」+ label + next_action；
- `usable` / `fresh` → 下发 1 次，频率 145.8 MHz；
- `expired` → 下发 1 次（tune 策略本就是 `confirm`，确认点在开始闸门；
  云台另由 `allow_gimbal=False` 禁掉）。

## 四、待裁定

产品口径「`expired` 也应 0 次下发」与 `TRACKING_MODES["expired"]["tune"] =
"confirm"`（→ `allow_tune=True`）冲突。若要硬禁，应改
`mbdsdr_ai/tle_integrity.py` 的档位表（单一事实源），并同步
`tests/test_tle_staleness_ui.py` 与面板提示「需确认后调谐（云台禁用）」，
不在桌面层另写判据。
