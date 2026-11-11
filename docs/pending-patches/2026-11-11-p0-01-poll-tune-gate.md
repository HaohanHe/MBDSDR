# 交接单：P0-01 执行层闸门（`_poll_satellite_track` 下发调谐前读 policy）

> **状态：已应用（2026-11-11 22:39，提交 `d1fcba5b`，两个文件：
> `desktop/main_window.py` + `tests/test_sat_tracking_gate.py`）。**
> 本单保留作判据与去重键的说明；不要重复应用。
> 来源：`desktop/main_window.py` 当时由 `启动工单线/W25-续` 持有（claim 到
> 2026-11-11 23:30），本条改动落在同一个文件的 `_poll_satellite_track`，
> 不抢文件，改由持锁方应用，或等其 release 后由本线应用。
> 实测：2026-11-11 22:37 claim 成功（TTL 10 min），22:39:02 release。

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
        # 口径（D-7 方案 A）：dead/unknown 才是 0 次；expired 是「确认后 1 次」
        # （TRACKING_MODES["expired"]["tune"] == "confirm"），别把 expired 当 dead。
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
- **口径（D-7 方案 A）**：`dead`/`unknown` 才是 0 次；`expired` 是
  **「确认后 1 次」**（`TRACKING_MODES["expired"]["tune"] == "confirm"`，
  `allow_tune=True`、`allow_gimbal=False`）。确认点在开始闸门，轮询不重复弹窗，
  也**不得**把 expired 当 dead 硬禁——改档位表会连带面板文案与
  `tests/test_tle_staleness_ui.py` 一起动。
- 原因文案（定稿，三要素固定顺序）：
  `未下发调谐：{label}（{reason}） → {next_action}`
  - `label` 例：`星历失效` / `星历不可用`（身份不符时 `星历身份不符（按失效处理）`）；
  - `reason` 例：`TLE 已失效（365 天）…`（来自 `band_note`，带年龄）；
  - `next_action` 例：`更新或导入 TLE 后重试`。
- 去重键 = **拼好的整句 `blocked`**（label+reason+next_action 原文）：
  - 同一条过境内星历不变 → 只在第一拍写一次状态栏，不刷屏；
  - 年龄按天取整（`_age_text`：<1 天留一位小数，否则整数），至多跨天时变一次；
  - 档位切换（dead → unknown）或 TLE 更新后 reason/next_action 变化 →
    自然重发一次，不需要额外时间戳。
  - 恢复调谐时把 `_sat_tune_blocked_reason` 置空，下次被禁还能再提示。

## 三、验收（应用后必须两条都绿）

```powershell
$env:QT_QPA_PLATFORM="offscreen"
python -m pytest tests/test_sat_tracking_gate.py -q
# 期望 10 passed + 2 xfailed → 修好后必须变成 12 passed 且删掉 xfail（strict 下 XPASS 即红）
python scripts\repro\2026-11-11-p0-01-dead-tle-tracking-gate.py
# 期望打印「显式读 policy = 0 次 / 实际 = 0 次」，EXIT=1（未复现）
```

测试覆盖（已在 `tests/test_sat_tracking_gate.py` 追加）：

- `dead` / `unknown` → 下发 0 次，状态栏含「未下发调谐」+ label + next_action；
- `usable` / `fresh` → 下发 1 次，频率 145.8 MHz；
- `expired` → 下发 1 次（tune 策略本就是 `confirm`，确认点在开始闸门；
  云台另由 `allow_gimbal=False` 禁掉）。

## 四、裁定（2026-11-11，已拍板）

**`expired` 按 D-7 方案 A：是「需确认」不是「禁止」。**
`TRACKING_MODES["expired"]["tune"] = "confirm"` → `allow_tune=True`、
`allow_gimbal=False` 就是最终口径：确认后允许 1 次调谐，云台仍禁。
不改 `tle_integrity` 档位表；面板提示「需确认后调谐（云台禁用）」、
`next_action`「确认星历年龄后可调谐；云台与自动工具写操作仍被禁止」与
`tests/test_tle_staleness_ui.py` 三处已逐字一致，无需联动修改。

**dead/unknown 才是 0 次。** 下一个人若把 expired 当 dead 硬禁，先回看本节。

## 五、追加（待应用）：调谐异常不得静默

> `desktop/main_window.py` 于 2026-11-11 22:39:19 起由 `主题线/A-prime` 持有
> （TTL 40 min），本线已 release，不回头抢。下列 hunk 待持锁方应用，
> 或在 release 后由任意线按本单落地；**改动只在此处，不新增其他逻辑**。

原代码把下发异常整段吞掉：

```python
            try:
                self._on_tune_sdr(float(tune_hz), self._sat_track_mode)
            except Exception:
                pass
```

改为（状态栏留痕 + 同因去重 + debug 日志，与 `_sat_tune_blocked_reason`
同一套路；成功时清空去重键，下次失败还能再提示）：

```python
            try:
                self._on_tune_sdr(float(tune_hz), self._sat_track_mode)
                self._sat_tune_failed_reason = ""
            except Exception as exc:  # noqa: BLE001 - 必须留痕，不许静默
                reason = f"{type(exc).__name__}: {exc}"
                logger.debug("卫星跟踪调谐下发失败：%s", reason)
                if reason != getattr(self, "_sat_tune_failed_reason", ""):
                    self._sat_tune_failed_reason = reason
                    self.statusBar().showMessage(
                        f"调谐下发失败：{reason}", MSG_IMPORTANT)
```

`logger` 为本模块既有的 `logging.getLogger`（`_tracking_policy_for` 已在用）。

验收：`python -m pytest tests/test_sat_tracking_gate.py
tests/test_ui_hotpath_honesty.py -q` 全绿，且新用例可补一条
「`_on_tune_sdr` 抛异常 → 状态栏含『调谐下发失败』+ 异常类型」。
