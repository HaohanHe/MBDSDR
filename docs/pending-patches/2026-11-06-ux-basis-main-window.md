# 待落库清单：回放后台读盘 / 假调谐回滚 / TLE 后台更新（ux-basis，2026-11-06）

> 本文只保留**还在我手上、等文件释放后自己落库**的部分，以及每条的反证。
> 需要别人并进他们正在改的文件的两项，已经拆成独立对接说明：
>
> - **P1（回放后台读盘）** → [2026-11-06-handoff-p1-replay-main-window.md](2026-11-06-handoff-p1-replay-main-window.md)（交 `操作逻辑线`）
> - **P2-C（control_panel 未连接增益「—」）** → [2026-11-06-handoff-p2c-control-panel-gain.md](2026-11-06-handoff-p2c-control-panel-gain.md)（交 `设置架构线`）
> - 基线红台账 → [2026-11-06-baseline-red-owners.md](2026-11-06-baseline-red-owners.md)

## 文件归属与当前状态

| 目标文件 | 状态（2026-11-06 取证） | 处理 |
|---|---|---|
| `desktop/main_window.py` | P1 由 `操作逻辑线` 落库（`f47927b0`）；P2-A/B/D/E + P3 由 ux-basis 落库（**`fe94e278`**） | P1：`tests/test_replay_load_async.py` 3 passed；P2/P3：`tests/test_main_window_hw_truthfulness.py` 7 passed，5 条变异反证全部真红；落库前合跑 31 passed |
| `desktop/control_panel.py` | 已无人 claim；**P2-C 尚未落库**（03:35 取证，`set_gain_applied` 仍不存在） | **本次不改**。P2-C 仍按 handoff-p2c 交 `设置架构线`；P2-D 已用 hasattr 守卫，P2-C 落库后自动生效 |
| `mbdsdr_ai/baseband_io.py` | 已落库（ux-basis） | 分块流式读 |
| `desktop/module_panel.py` | 已落库（ux-basis） | `set_sdr_connected` / `set_rf_params` / 未连接禁用 + 「—」/ sink 置灰「仅记录」 |
| `desktop/new_spacetime_panel.py` | 已落库（ux-basis） | 过境刷新后台线程 + 重入 + 「更新中…」 |
| `tests/test_ui_wiring_audit.py` | 已随 `脆断言清理线` 提交 `86fdbd0b` 入库（本线编辑时该文件已被其 claim，时序失误已记录） | 4 条基线红修完（明细见台账文档） |

已落库前置（`load_iq` 新接口与实测数字）：见 P1 对接说明 §1，这里不再抄第二份，
避免两处口径分叉。

---

## P2 假调谐 / 假 Sink / 拖动失败回滚（`main_window.py`）

> 行号均为基线 `d1616f9c` 的参考值；`main_window.py` 正被他人编辑，落库时按
> **函数名 + 代码锚点**定位，不要按行号硬改。

### P2-A `_on_module_tune`（基线 4530，锚点：`"""模块面板里改了频率 → 同步到频谱中心频。"""`）

现状：只改界面，不写硬件——没连后端时看起来"调谐成功"。

```python
    @Slot(float)
    def _on_module_tune(self, freq_hz: float):
        """模块面板改频率 → 真实调谐；失败回滚，绝不只改界面。"""
        backend = self._active_sdr_backend
        if backend is not None:
            if not self._apply_hw_action(
                    "调谐", lambda: backend.set_frequency(freq_hz)):
                self._revert_tune_ui()
                mp = getattr(self, "module_panel", None)
                last = float(getattr(self, "_backend_center_hz", 0.0) or 0.0)
                if mp is not None and last > 0:
                    mp.set_rf_params(freq_hz=last)   # 拉回最后一次真实值
                return
        self.spectrum.set_center_freq(freq_hz / 1e6)
        self.freq_label.setText(f"{freq_hz / 1e6:.3f} MHz")
        self.status_freq.setText(f"频率: {freq_hz / 1e6:.3f} MHz")
        self._vfo_center_hz = freq_hz
        self._backend_center_hz = freq_hz
        self._update_main_vfo_offset()
        self._schedule_tx_readout()
```

### P2-B `_on_spectrum_freq_changed`（基线 5199，锚点：`except Exception:` + `pass` 紧跟 `set_frequency(freq * 1e6)`）

把静默吞错换成回滚：

```python
        if self._active_sdr_backend is not None:
            if not self._apply_hw_action(
                    "调谐", lambda: self._active_sdr_backend.set_frequency(
                        freq * 1e6)):
                self._revert_tune_ui()
                return      # 之后的状态栏/VFO 一律不动，避免显示成功值
```

### P2-D 连接态与真实读数回填（基线 4137 `_panels_set_sdr_connected` / 3640 增益范围收放）

```python
        # _panels_set_sdr_connected 末尾
        try:
            mp = getattr(self, "module_panel", None)
            if mp is not None:
                # tune_wired=True 只能在 P2-A 落库后传；否则频率控件保持禁用
                mp.set_sdr_connected(connected, tune_wired=connected)
        except Exception:
            pass
```

```python
        # 连接成功处（backend.set_gain 成功之后，不要放在"下发尝试"之前）
        try:
            mp = getattr(self, "module_panel", None)
            freq_hz = float(getattr(backend, "center_freq", 0.0)
                            or getattr(self, "_backend_center_hz", 0.0) or 0.0)
            gain_fn = getattr(backend, "get_gain", None)
            gain_db = float(gain_fn()) if callable(gain_fn) else None
            if mp is not None:
                mp.set_rf_params(freq_hz=freq_hz or None, gain_db=gain_db)
            cp = getattr(self, "control_panel", None)
            if cp is not None and gain_db is not None:
                cp.set_gain_applied(gain_db)   # P2-C 提供的方法，见 handoff-p2c
        except Exception as e:  # noqa: BLE001
            logger.warning("真实频率/增益回填失败（界面保持「—」）: %s", e)
```

拿不到真实读数就**不调**回填方法，界面继续「—」，不许用 0 / 默认 20 dB 凑数。

增益下发失败回滚（`_on_gain_changed`，基线 4553）：`_apply_hw_action` 返回 False 时
`return` 之前，把标签拉回真实值：

```python
            if not self._apply_hw_action("设置增益", lambda: backend.set_gain(float(db))):
                try:
                    cp = getattr(self, "control_panel", None)
                    if cp is not None:
                        cp.set_gain_applied(float(backend.get_gain()))
                except Exception:
                    pass
                return
```

### P2-E Sink（基线 7025）

模块面板已置灰并标「仅记录」；`_on_module_sink_changed` 若仍可能被直接调用，
把状态栏文案改成 `f"输出 sink: {sink}（仅记录，后端未切换）"`。
真正接入后端 sink 切换后再恢复下拉并删掉「仅记录」。

---

## P3 TLE 更新移后台（`_on_update_sat_tle`，基线 8409）

`update_tle_from_celestrak()` 是同步网络调用（单星超时 5 s、内置最多 10 颗，
最坏 50 s），现在直接跑在 GUI 线程。改法与已落库的过境刷新同构：

```python
    def _on_update_sat_tle(self):
        if self._tle_update_thread is not None and self._tle_update_thread.is_alive():
            self.statusBar().showMessage("TLE 正在更新中…，请稍候", MSG_NORMAL)
            return
        if self._loop_sat_tracker is None:
            self._init_loop_sat_catalog()
        if self._loop_sat_tracker is None:
            return
        self.statusBar().showMessage("正在更新 TLE…", MSG_NORMAL)
        tracker = self._loop_sat_tracker

        def _work():
            n, failure = 0, None
            try:
                n = tracker.update_tle_from_celestrak()
                failure = tracker.last_refresh_failure
            except Exception as e:  # noqa: BLE001
                failure = {"code": "tle_update_exception", "label": "TLE 更新异常",
                           "reason": str(e), "action": "查看日志后重试"}
            try:
                self._tle_update_result.emit((n, failure))
            except RuntimeError:
                pass

        self._tle_update_thread = threading.Thread(
            target=_work, name="tle-update", daemon=True)
        self._tle_update_thread.start()

    def _on_tle_update_result(self, payload):
        n, failure = payload
        names = [s["name"] for s in self._loop_sat_tracker.list_satellites()]
        self.sat_track_panel.populate_satellites(names)
        self.sat_track_panel.set_tle_date(self._loop_sat_tracker.tle_date)
        from mbdsdr_ai.tle_fetch import tle_refresh_status_text
        msg = tle_refresh_status_text(n, failure)
        self.statusBar().showMessage(
            msg, MSG_NORMAL if (n > 0 and not failure) else MSG_IMPORTANT)
```

配套：类体加 `_tle_update_result = Signal(object)`；`__init__` 初始化
`self._tle_update_thread = None` 并 `connect(..., Qt.QueuedConnection)`；
关窗清理时 `self._tle_update_cancel` 略——TLE 请求本身有 5 s 单星超时，
daemon 线程 + `RuntimeError` 兜底已足够，**不要**为它再造一个取消机制。

`tle_refresh_status_text(n, failure)` 按 `.get` 读取失败对象；用字典最稳
（若 `TleFetchFailure` 可构造，字段名以 `mbdsdr_ai/tle_fetch.py` 为准）。

---

## 反证（每条都要"改回旧行为 → 指定用例红"）

### 已落库部分（实测过的，保留记录）

| 守卫用例 | 变异（改回旧行为） | 实测结果 |
|---|---|---|
| `tests/test_baseband_streaming.py::test_cf32_is_read_in_bounded_chunks` | `chunk = 1 << 40`（整文件一次 `np.fromfile`） | 3 failed |
| `tests/test_module_panel_truthful_controls.py::test_connected_without_wiring_keeps_tune_disabled` | 忽略 `tune_wired`，改用连接态启用频率 | 1 failed |
| `tests/test_spacetime_refresh_background.py::test_refresh_runs_off_gui_thread_and_shows_loading` | 按钮改回同步 `refresh_passes` | 1 failed |
| `tests/test_ui_wiring_audit.py::test_no_new_dead_interactive_widgets` | 扫描器去掉 `setMenu` 识别 | 1 failed（实测） |
| `tests/test_ui_wiring_audit.py::test_allowlist_entries_still_unconnected` | 恢复任一过期白名单条目 | 1 failed（实测） |
| `tests/test_ui_wiring_audit.py::test_delete_writes_to_store` | 确认桩改回 `QMessageBox.No` | 1 failed（实测） |

### 落库时实测（`tests/test_main_window_hw_truthfulness.py`，2026-11-06）

| 项 | 用例 | 变异（改回旧行为） | 实测结果 |
|---|---|---|---|
| P2-A | `test_module_tune_failure_rolls_back` | `backend = None`（只改界面，不下发硬件） | 1 failed（RED） |
| P2-B | `test_spectrum_drag_failure_reverts_ui` | 改回 `try/except: pass` 静默吞错 | 1 failed（RED） |
| P2-D | `test_backfill_only_real_values` | 回填时 `gain_db=0.0 if None else gain_db`（编造 0） | 1 failed（RED） |
| P2-E | `test_sink_status_says_record_only` | 文案改回 `f"输出 sink: {sink}"` | 1 failed（RED） |
| P3 | `test_tle_update_runs_off_gui_thread` | 线程 `start()` 改直接 `_work()`（同步） | 1 failed（RED） |
| P1 | `test_replay_load_does_not_block_gui`（已随 `f47927b0`） | `_start_replay_load` 改回同步 `load_iq` | 见 handoff-p1 §5 |

落库时与本文的两处必要偏差：

1. **P2-D 抽成 `_backfill_real_rf_readings()`**（避免在连接成功路径里再叠一层
   try/except；调用点仍在 `backend.set_gain` 之后）。`set_gain_applied` 用
   `hasattr` 守卫——P2-C 未落库时控制面板保持「—」，模块面板仍能回填。
2. **P3 适配既有 `tests/test_tle_failure_visibility.py`**：该用例拿 `_Holder`
   假对象直接调 `_on_update_sat_tle`，后台化后改为"worker 收 payload → join →
   手动回 `_on_tle_update_result`"模拟 QueuedConnection；断言一字未改。
   该文件无他人 claim（改动前已查）。

写测试时的坑：offscreen 下 `QTimer.singleShot(0)` 不触发（用 `processEvents()` 轮询）；
`tests/conftest.py` 会把 `QMessageBox` / `QFileDialog` 静态方法拦成"取消"/空路径，
需要弹窗或选文件的用例必须显式 monkeypatch。

---

## 落地顺序（2026-11-06 已完成 1–4；5 待回执）

1. `main_window.py` 释放 → 先 P2-A / P2-B（同一函数族，改完跑 P2 用例）；**已完成**
2. 再 P2-D（必须晚于 P2-A，`tune_wired=connected` 才有意义）；**已完成**
3. P2-E 文案一分钟；P3 与 P1 互不依赖，可并行；**已完成**
4. P1 按 handoff 说明落库（它是唯一能消掉 4.9 s 冻结的一项）；**已由 `操作逻辑线` 落库**
5. P2-C 等 `设置架构线` 回执，按 handoff-p2c 验收；
6. 回归：
   `python -m pytest tests/test_baseband_streaming.py tests/test_module_panel_truthful_controls.py tests/test_spacetime_refresh_background.py tests/test_ui_wiring_audit.py -q`
   （当前：12 passed + 11 passed）。
