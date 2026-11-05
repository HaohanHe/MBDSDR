# 第二批 UX 修复实施方案（可执行版）

- 编写日期：2026-11-05
- 基线提交：`fee6455`（本文所有行号均以该 HEAD 的 `git show HEAD:<文件>` 快照为准；与 `1b2f42d` 的行号逐项核对一致）
- **行号漂移警告：`desktop/main_window.py` 仍有他线未提交改动（写作时 306 增 / 89 删，且期间还在增长）。每次落地前必须重新 `git status` + 重新定位函数，不得直接按本文行号盲改。**
- 本文只写方案，不含任何代码改动。

## 执行纪律（每张卡都适用）

1. 动手前 `git status --porcelain` 确认目标文件干净；不干净立即停下报告，不叠在别人的半成品上。
2. 一次只做一张卡，单独提交；提交用 pathspec 只带目标文件 + 对应测试：
   `git commit -m "..." -- desktop/main_window.py tests/test_xxx.py`
3. 先跑测试拿“修前必红”，再改代码，再跑同一条测试拿“修后必绿”。
4. 不推送、不改历史、不整份重写 `docs/limitations-register.md`（只许追加）。

## 总览

| 卡 | UX-ID | 标题 | 目标文件 | 写作时是否干净 |
|---|---|---|---|---|
| 1 | UX-01 | 点卫星无设备也报“已自动调谐” | `desktop/main_window.py` | 否（操作逻辑线占用） |
| 2 | UX-02 | 调谐/增益/模式失败仍写 UI、仍落盘 | `desktop/main_window.py` | 否（同上） |
| 3 | UX-16 | 重连回填 `set_frequency(0)` 假频率 | `desktop/main_window.py` | 否（同上） |
| 4 | UX-08 | AI 拒绝天空动作无可见反馈 | `desktop/main_window.py` | 否（同上） |
| 5 | UX-04 | 录音中关窗不确认、不告知保存路径 | `desktop/main_window.py` | 否（同上） |
| 6 | UX-09/10 | 无设备横幅术语堆叠/被裁切；连接框无设备仍可“确定” | `desktop/main_window.py` | 否（同上） |
| 7 | UX-07/12 | 星象仪退出入口不醒目、无录音指示 | `desktop/main_window.py` | 否（同上） |
| 8 | UX-06/17/18 | 删书签无确认；误调谐不可撤销；内置书签无标注 | `desktop/control_panel.py` + `desktop/main_window.py` | `control_panel.py` 是（`main_window.py` 否） |

> 条目清单口径修正：你给的条目 1 括号里写的是“AI 拒绝天空动作无反馈”，实际是卡 4 `_request_sky_action`（UX-08）；卡 1 `_on_sky_object_clicked` 是 UX-01“无设备也报已自动调谐”。本文按真实行为拆分，两个都保留，不合并。

---

## 卡 1｜UX-01：点卫星时“已调谐”只许在真实成功后出现

**目标文件 / 行号**
- `desktop/main_window.py:7460-7508`（`_on_sky_object_clicked` 整体；函数结束于 7508，下一函数从 7510 开始）。
- 行号可能因他线提交漂移，落地前重测。

**现在的错误行为（一句）**
没有 SDR（`_active_sdr_backend is None`）时点卫星，照样把频率标签改成 145.8 MHz，并提示“已跟踪 ISS (ZARYA) … - 已自动调谐”。

**改成什么**
1. 状态字段：以 `self._active_sdr_backend` 为唯一“是否真的下发”判据；`_worker` 的 `sdr_set_frequency` 只是异步旁路，不能作为成功证据。
2. 执行顺序改为：先建天线指向（纯界面），再尝试真实调谐；**只有 `backend.set_frequency()` 返回不抛异常，才写** `freq_label`、`status_freq`、`_vfo_center_hz`、`_backend_center_hz`、`_update_main_vfo_offset()`、`control_panel.set_frequency_hz()`，并 emit worker 工具。
3. 无后端 / 下发抛异常时：**不写频率控件、不改控制面板、不 emit 工具**，只保留天线指向；状态栏文案（原文）：
   - 有频率但没调成：
     `已指向 {name} (AZ {az:.1f}°, EL {el:.1f}°) · 未调谐频率：{原因}。下一步：连接 SDR 后重新点此卫星。`
   - 卫星本身没配下行频率：
     `已指向 {name} (AZ {az:.1f}°, EL {el:.1f}°) · 该卫星没有配置下行频率，未调谐。`
   - 真正成功：
     `已指向 {name} (AZ {az:.1f}°, EL {el:.1f}°) · 已调谐到 {freq_mhz:.3f} MHz`
4. 禁止再出现“已跟踪”措辞：此处没有启动跟踪环路，只是画了指向符号；跟踪状态由 `_sat_track_timer` 单独表达。

**谁读谁写**
- 写：`_on_sky_object_clicked` 写 `freq_label/status_freq/_vfo_center_hz/_backend_center_hz`。
- 读：`control_panel.set_frequency_hz()` 是只存内部值、连接时才刷新显示的恢复 setter，可继续调用，但只许在成功分支调用。

**反证（修前必红）**
- 测试文件：`tests/test_sky_tune_honesty.py`（新建；模块级复用 MainWindow，照 `tests/test_ui_flow_guards.py` 的 fixture 写法）。
- 断言：
```python
w._active_sdr_backend = None
w._worker = None
w.freq_label.setText("-- MHz")
obj = SkyObject("ISS (ZARYA)", 45.0, 30.0, frequency_hz=145.8e6)
w._on_sky_object_clicked(obj)
msg = w.statusBar().currentMessage()
assert "已自动调谐" not in msg
assert "未调谐" in msg
assert w.freq_label.text() == "-- MHz"
assert w.control_panel.freq_display.frequency() is None
```
- 为什么能抓住假成功：当前代码无条件执行 7477-7478 的 `freq_label.setText("145.8 MHz")` 与 7499-7502 的“已自动调谐”，即使 `_active_sdr_backend is None`。上述四条断言在当前代码上全部为红。
- 补一条成功分支，防止“把好路径也一起删掉”：
```python
class _GoodBackend:
    def __init__(self): self.freqs = []
    def set_frequency(self, hz): self.freqs.append(float(hz))
w._active_sdr_backend = _GoodBackend()
w._on_sky_object_clicked(obj)
assert w._active_sdr_backend.freqs == [145.8e6]
assert "已调谐到 145.800 MHz" in w.statusBar().currentMessage()
```

**归属**
- `desktop/main_window.py`：写作时**不干净**（操作逻辑线在改，`git diff --stat` 70+/19-）。等交干净再动。

---

## 卡 2｜UX-02：调谐 / 增益 / 模式失败不提交 UI、不落配置

**目标文件 / 行号**
- `desktop/main_window.py:3898-3915`（`_on_tune_fm`）
- `desktop/main_window.py:3917-3926`（`_on_tune_am`）
- `desktop/main_window.py:3928-3960`（`_on_tune_sdr`）
- `desktop/main_window.py:3985-4002`（`_on_gain_changed`）
- `desktop/main_window.py:4072-4123`（`_on_mode_changed`）
- 行号可能漂移，落地前重测。

**现在的错误行为（一句）**
五个槽函数都先吞掉 `set_frequency/set_demod/set_gain` 的异常，随后照常改标签、改模式、`settings.set(...)`——硬件没动，界面和下次启动的配置却都当成功了。

**改成什么**
1. 新增两个私有小工具（同文件、紧邻这批槽函数）：
   - `_apply_hw_or_report(action: str, fn) -> bool`：调 `fn()`；异常时状态栏三段式并返回 False，不抛。
   - 三段式文案原文：
     `{动作}未生效：{异常类名}: {异常消息}。可能原因：SDR 已断开、被其他程序占用或驱动异常。下一步：点工具栏「连接」重连后重试；界面与已保存配置保持上次成功值。`
     其中动作为 `调谐` / `设置增益` / `切换解调模式`。
2. `_on_tune_fm`：先 `set_frequency(freq*1e6)`；失败直接 return，**不**改 `spectrum` 中心、不写两个标签、不改 VFO 中心。成功后再执行现有 3902、3910-3915。
3. `_on_tune_am`：同法；失败 return，成功保持现有行为（此路径本来不写主标签，不额外扩写）。
4. `_on_tune_sdr`：先 `set_frequency`，再 `set_demod`；任一失败 → 回滚控制面板显示并 return，**不**执行 3933-3960 的任何 UI 写、不 `settings.set("frequency_hz"/"demod_mode")`。回滚用上一次成功真值：`_backend_center_hz`（为 0 时显示 `-- MHz`）与 `_demod_cfg.mode`，通过 `control_panel.set_frequency_hz()` / `control_panel.set_mode()`（两者都不发信号）。
5. `_on_gain_changed`：先 `set_gain`；失败 return，不 `settings.set("gain_db")`；成功才落盘。
6. `_on_mode_changed`：先 `set_demod`；失败时 `control_panel.set_mode(self._demod_cfg.mode)` 把下拉回弹（不发信号），不更新 `_demod_cfg.mode`、不推 VFO 带宽、不写 `settings.set("demod_mode")`、不改 `dock_mode_label`；成功才走 4088-4123。
7. `_worker.request_tool.emit(...)` 统一挪到成功后，避免本机失败而 AI 侧工具照样执行。

**谁读谁写**
- `_backend_center_hz` / `_demod_cfg.mode` 定义为“最后一次真实成功的真值”，只允许在成功分支写。
- `control_panel._freq_hz` 是面板自己的真值；失败后必须用 `set_frequency_hz(last_good)` 把它拉回，否则面板显示新频率、硬件还是旧频率。

**反证（修前必红）**
- 测试文件：`tests/test_tune_failure_no_commit.py`（新建）。
- 断言（`_BadBackend.set_frequency/set_demod/set_gain` 全部抛 `RuntimeError("device unplugged")`）：
```python
w.settings.set("frequency_hz", 100e6)
w.settings.set("demod_mode", "AM")
w.settings.set("gain_db", 20.0)
w._on_tune_sdr(145.8e6, "FM")
assert w.freq_label.text() != "145.800 MHz"          # 当前会变成 145.800
assert w.settings.get("frequency_hz") == 100e6       # 当前被写成 145.8e6
assert w.settings.get("demod_mode") == "AM"          # 当前被写成 FM
msg = w.statusBar().currentMessage()
assert "调谐未生效" in msg and "下一步" in msg        # 当前状态栏为空
assert w.control_panel.freq_display.frequency() != 145.8e6  # 当前面板已被推新频率

w._on_gain_changed(49)
assert w.settings.get("gain_db") == 20.0             # 当前被写成 49

w._on_mode_changed("USB")
assert w.settings.get("demod_mode") == "AM"          # 当前被写成 USB
assert w.control_panel.mode_combo.currentText() == "AM"  # 当前停在 USB
```
- 为什么能抓住假成功：当前 3935-3958、3993-4002、4083-4118 的异常全被 `except: pass` 吞掉，后面的 `settings.set` 与控件写无条件执行；断言分别钉住“标签 / 配置 / 面板显示 / 状态栏”四个假成功面。

**归属**
- `desktop/main_window.py`：不干净，等交干净。

---

## 卡 3｜UX-16：去掉重连时的 `set_frequency(0)` 假频率

**目标文件 / 行号**
- `desktop/main_window.py:3664-3676`（`_panels_set_sdr_connected` 内的频率回填分支，位于整个方法 3636-3690 中段）。
- 行号可能漂移，落地前重测。

**现在的错误行为（一句）**
连接后如果控制面板频率仍是无真值占位，代码调用 `fd.set_frequency(0)`，用户看到 `0.000 MHz`，等于凭空造了一个“正在接收 0 Hz”的假频率。

**改成什么**
1. 删除 3672-3674 的 `elif ... fd.set_frequency(0)` 分支，只保留 `if not connected: fd.set_frequency(None)`。
2. 连接后不再主动填任何值；频率显示保持 `-- MHz`，直到 `set_frequency_hz()` 收到真实调谐结果。
3. 若产品希望连接后显示设备真实中心频率，正确做法是从 `_active_sdr_backend.get_frequency()` 读；**没有真实读数就保持占位**，不得用 0 顶替。

**谁读谁写**
- `FreqDisplay.set_frequency(None)` 的语义是“未知”；`set_frequency(0)` 的语义是“0 Hz 真实值”。此处只能写前者。

**反证（修前必红）**
- 测试文件：`tests/test_panel_connect_placeholder.py`（新建）。
- 断言：
```python
w.control_panel._freq_hz = None
w._panels_set_sdr_connected(True)
assert w.control_panel.freq_display.frequency() is None   # 当前返回 0
```
- 为什么能抓住假成功：当前代码走到 `has_frequency() == False` 分支后写 0，`frequency()` 返回 `0`，断言必红；改后仍是 `None`。

**归属**
- `desktop/main_window.py`：不干净，等交干净。

---

## 卡 4｜UX-08：AI 拒绝天空动作时，用户要看得见原因和下一步

**目标文件 / 行号**
- `desktop/main_window.py:1625-1636`（`_request_sky_action`）
- 配套只读：`_reset_sky_pending_ui` 1638-1646、`_on_sky_action_resolved` 1648-1663。
- 行号可能漂移，落地前重测。

**现在的错误行为（一句）**
`panel.request_sky_action(context)` 返回 False（未配置 AI / 正忙 / 拒绝）时，只把按钮回弹，状态栏和天空页一个字都没有；用户以为点了没反应。

**改成什么**
1. `ok=False` 时，除 `_reset_sky_pending_ui(action)` 外，追加可见反馈：
   - 状态栏 8 秒文案原文：
     `{跟踪/云台跟随}未启动：AI 没有接受这次请求（可能未配置 API Key、正忙或需要先确认硬件）。下一步：打开右侧「AI」面板查看原因后重试，或在天空页手动操作。`
   - 若当前在星象仪形态（`_app_mode == "planetarium"`），把同一句交给现有 `_on_planetarium_ai_reply(text)`（1792-1803，它负责 `setText` + `setVisible(True)` + 定位覆盖卡），保证全屏时也看得见。
2. `panel is None` 分支（1628-1632）保留现有状态栏文案，保证两种失败都说话。
3. 不新增控件；复用状态栏 + 已有 AI 覆盖卡。

**谁读谁写**
- 写：`_request_sky_action` 只写状态栏与覆盖卡文本，不写 `_sat_track_timer` / `_gimbal_follow`（真实状态仍由 `_reset_sky_pending_ui` 对齐）。

**反证（修前必红）**
- 测试文件：`tests/test_sky_action_visibility.py`（新建）。
- 断言：
```python
w.ai_panel.request_sky_action = lambda ctx: False
w._request_sky_action("track", {"satellite": "ISS"})
assert "跟踪未启动" in w.statusBar().currentMessage()
assert "下一步" in w.statusBar().currentMessage()
assert w._sky_track_btn.isChecked() is False
```
- 为什么能抓住静默：当前 `ok=False` 分支只调 `_reset_sky_pending_ui`，`currentMessage()` 为空，第一条断言必红。
- 全屏可见再补一条：先 `_enter_planetarium()`，再触发拒绝，断言 `w._planetarium_ai_card.isVisible()` 且文本含“未启动”。

**归属**
- `desktop/main_window.py`：不干净，等交干净。
- 只调用 `rf_sky_view` 的既有接口（`set_antenna` / `enter_horizon_view`），不改该文件（该文件当前也被他线占用）。

---

## 卡 5｜UX-04：录音中关窗必须确认，并明确保存路径

**目标文件 / 行号**
- `desktop/main_window.py:7597-7632`（`closeEvent`）
- 配套只读：`_apply_recording` 5480-5487、`_stop_recording` 5578-5650（真正落盘 + sidecar）。
- 行号可能漂移，落地前重测。

**现在的错误行为（一句）**
`closeEvent` 不看 `_recording`，直接走 `settings.flush → _disconnect → _teardown_connection`；录音被顺手停掉并保存，但用户既没被问过，也不知道存到了哪。

**改成什么**
1. 在 `closeEvent` 最前面（7600 之前）插入录音保护：
```python
if getattr(self, "_recording", False):
    path = getattr(self, "_record_file_path", "") or "（文件名尚未生成）"
    ret = QMessageBox.question(
        self, "正在录音",
        f"正在录音。关闭窗口会停止录音并保存到：\n{path}\n\n"
        "要停止并保存后退出吗？选「取消」可以继续录音。",
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel)
    if ret != QMessageBox.StandardButton.Save:
        event.ignore()
        return
    try:
        self._apply_recording(False)
    except Exception as e:
        QMessageBox.warning(
            self, "录音保存失败",
            f"停止录音时出错：{e}。可能原因：磁盘已满或文件被占用。"
            "下一步：先不要关闭窗口，检查磁盘/权限后点「停止录音」重试。")
        event.ignore()
        return
```
2. 只有选择“停止并保存”且保存无异常才继续原有退出流程；否则 `event.ignore()`，窗口保持原样。
3. 保存成功后再 `settings.flush()`，避免“配置先落盘、录音没存上”的误导顺序。

**谁读谁写**
- 读 `_recording`、`_record_file_path`；写只能通过 `_apply_recording(False)`（内部走 `_stop_recording`，负责 `.iq` + sidecar）。

**反证（修前必红）**
- 测试文件：`tests/test_close_recording_guard.py`（新建）。
- 断言（`monkeypatch.setattr(main_window.QMessageBox, "question", fake)`）：
```python
w._recording = True
w._record_file_path = "/tmp/rec.iq"
event = QCloseEvent()
w.closeEvent(event)
assert asked, "修前 closeEvent 从不询问"        # 当前 asked == []
assert w._recording is True                    # 取消后必须还在录
assert event.isAccepted() is False             # 当前无条件 accept
```
- 为什么能抓住静默：当前 7597-7632 没有任何 `QMessageBox` 调用，`event.accept()` 在最后无条件执行，测试第一条就红。
- 保存分支再补：让 `fake` 返回 `Save`，monkeypatch `w._stop_recording` 记录调用；断言调用 1 次、`event.isAccepted()` 为 True。

**归属**
- `desktop/main_window.py`：不干净，等交干净。

---

## 卡 6｜UX-09 + UX-10：无设备横幅说人话且不裁切；连接框无设备禁用“确定”

**目标文件 / 行号**
- 横幅初始化：`desktop/main_window.py:1199-1208`
- 无设备文案两处：`3052-3059`、`3067-3071`
- 横幅显示：`_show_no_rtl_hint` 3086-3112
- 连接对话框：`_connect_dialog` 3280-3439（按钮 3380-3383、参数使能 3385-3401、刷新 3403-3430、接受后 3434-3439）
- 行号可能漂移，落地前重测。

**现在的错误行为（一句）**
横幅把“Zadig WinUSB / SoapySDR(SoapyRTLSDR) / rtl_tcp / pyrtlsdr 为可选 GPL-3.0”一次堆满且不换行，在 290px 左栏被裁掉；连接框在没有任何本机设备时默认选中“远程 rtl_tcp”，点“确定”仍可继续，无设备的真实状态没有就地说明。

**改成什么**
1. 新增模块级常量（文件顶部常量区）：
```python
_NO_SDR_HINT = (
    "没找到 SDR 设备。可能原因：USB 没插好、缺驱动，或被其他程序占用。"
    "下一步：接好设备后点工具栏「连接」重试；RTL-SDR 需先用 Zadig 安装 WinUSB 驱动。")
```
2. 两处 3056-3058 / 3068-3070 改为调用 `self._show_no_rtl_hint(_NO_SDR_HINT)`，不再拼接长术语串。
3. 横幅初始化 1199-1208：初文改为
   `正在查找 SDR 设备…没有设备也可以先看天空模式和 AI 对话。`
   并加 `self._no_device_hint.setWordWrap(True)`；样式保留，不加新控件。
4. 连接框设备列表规则（`3280-3432`）：
   - 有本机设备：设备项在前，**默认选中第一台真实设备**；远程 rtl_tcp 放最后。
   - 无本机设备：第一项放 `"未发现 SDR 设备（请接好 USB/安装驱动后点「刷新设备列表」）"`（`data=None`、禁用、默认选中该项）；远程 rtl_tcp 仍作为可选项。
   - `_update_param_state()` 里同时设置 `ok_btn.setEnabled(combo.currentData() is not None)`；`ok_btn = buttons.button(QDialogButtonBox.Ok)`。
   - 新增对话框内就地结果标签 `QLabel`，`objectName="deviceResultLabel"`：有设备时 `检测到 {n} 台 SDR 设备`，无设备时 `未发现 SDR 设备（检查 USB/驱动后点刷新）`；刷新时更新它，不要再把结果写主窗口状态栏（模态框背后的字看不到）。
   - 兜底 3437-3439 不再静默：`data is None` 时 `QMessageBox.information(self, "未选择设备", "当前没有可连接的 SDR 设备。请接好 USB/安装驱动后重新打开此窗口。")`。
5. 远程 rtl_tcp 仍是合法选项，选中它时“确定”可点（它会接着问 host:port）；这不是“无设备假连接”。

**谁读谁写**
- `combo.currentData()` 是唯一选择真值；`ok_btn` 只读它。
- `deviceResultLabel` 只由 `_reload_devices` 和初始化写。

**反证（修前必红）**
- 测试文件：`tests/test_no_device_onboarding.py`（新建）。
- 横幅断言（monkeypatch `enumerate_all_sdr_devices` 返回 `[]`，调 `_auto_enumerate_and_connect()`）：
```python
assert w._no_device_hint.wordWrap() is True          # 当前 False
text = w._no_device_hint.text()
assert "没找到 SDR 设备" in text                     # 当前是“未找到 RTL-SDR…”
assert "下一步" in text                              # 当前没有
assert "GPL-3.0" not in text                         # 当前有术语堆叠
```
- 连接框断言（monkeypatch 枚举返回 `[]`；`monkeypatch.setattr(main_window.QDialog, "exec", lambda self: QDialog.Rejected)`；调用后 `dlg = w.findChild(QDialog)`）：
```python
box = dlg.findChild(QDialogButtonBox)
assert box.button(QDialogButtonBox.Ok).isEnabled() is False   # 当前 True（默认选中远程）
lbl = dlg.findChild(QLabel, "deviceResultLabel")
assert lbl is not None and "未发现 SDR 设备" in lbl.text()      # 当前没有该标签
```
- 为什么能抓住假可用：当前无设备时下拉默认选中远程 `_RTL_TCP_SPECIAL`，`currentData()` 非 None，OK 始终可点，且没有任何就地状态标签；两条断言均红。

**归属**
- `desktop/main_window.py`：不干净，等交干净。
- `desktop/device_panel.py` 不改（刷新就地反馈已存在，UX-14 复核结论）。

---

## 卡 7｜UX-07 + UX-12：星象仪要有醒目的退出入口和录音指示

**目标文件 / 行号**
- `desktop/main_window.py:1684-1764`（`_build_planetarium_page`；右侧窄条 1723-1737）
- `1837-1867`（`_enter_planetarium`）、`1869-1899`（`_exit_planetarium`）
- 录音计时：`4532-4534`（`_update_record_time`）、`5480-5487`（`_apply_recording`）
- 行号可能漂移，落地前重测。

**现在的错误行为（一句）**
星象仪全屏时唯一的退出入口是右侧 36px 窄条上一个写着 `IDE` 的小按钮；录音中切进来，画面上没有任何“正在录音”的字样，用户不知道录音还在继续、也不知道怎么停。

**改成什么**
1. 退出入口（改 1723-1737，不新建窗口/浮层）：
   - 窄条宽度从 `tokens().SIZE["collapse_strip"]`（36）改为 132；
   - 按钮文本原文 `"退出星象仪\n(Esc)"`，`objectName="planetariumExit"`，`setMinimumHeight(44)`，`setToolTip("切回 IDE 形态；按 Esc 同样退出")`；
   - 存为 `self._planetarium_exit_btn`，仍连 `set_app_mode("ide")`。
2. 录音指示（同一窄条，放在退出按钮上方）：
   - 新增 `QLabel`，`objectName="planetariumRecIndicator"`，存 `self._planetarium_rec_label`，默认 `hide()`；
   - 文本原文 `● 录音中 {mm:ss}`，颜色用 `tokens().COLORS["danger"]`（当前值 `#B85C5C`）；
   - 新增 `_sync_planetarium_recording()`：读 `self._recording` 与 `self._record_seconds`，录音中 `show()` 并刷新文本，否则 `hide()`；
   - 调用点：`_update_record_time`（4533 后加一行）、`_start_recording` 成功后、`_stop_recording` 收尾、`_apply_recording` 两个分支、`_enter_planetarium` 末尾（进形态即同步，避免“进来后才开始录”漏显）。
3. 退出时隐藏指示，不影响 `_recording` 真值；录音继续由控制面板/工具栏控制。

**谁读谁写**
- 唯一真值仍是 `self._recording` 与 `self._record_seconds`；指示标签只读不写，绝不自己起第二套计时。

**反证（修前必红）**
- 测试文件：`tests/test_planetarium_status.py`（新建）。
- 断言：
```python
w._recording = True
w._record_seconds = 42
w._enter_planetarium()
assert "退出星象仪" in w._planetarium_exit_btn.text()      # 当前按钮文本是 "IDE"
assert w._planetarium_exit_btn.minimumHeight() >= 44     # 当前窄条 36px
assert w._planetarium_rec_label.isHidden() is False      # 当前无此属性
assert "录音中 00:42" in w._planetarium_rec_label.text()
w._recording = False
w._sync_planetarium_recording()
assert w._planetarium_rec_label.isHidden() is True
```
- 为什么能抓住“看不见”：当前 1730-1734 只建了 `QPushButton("IDE")` 且无任何 `_recording` 引用；前四条断言在当前代码上直接 AttributeError / 文本不符，红。
- 用 `isHidden()` 而非 `isVisible()`：offscreen 下父窗口未必 show，`isVisible()` 不可靠。

**归属**
- `desktop/main_window.py`：不干净，等交干净。
- 只使用 `rf_sky_view` 已有接口，不改该文件。

---

## 卡 8｜UX-06 + UX-17 + UX-18：删书签确认、误调谐撤销、内置书签标注

> 这张卡跨两个文件，必须拆成 **8a → 8b 两次提交**，否则违反“pathspec 只该文件”。

### 8a｜`desktop/control_panel.py`（写作时干净）

**目标文件 / 行号**
- `desktop/control_panel.py:907-932`（`_refresh_bookmarks`）
- `934-940`（`load_bookmarks_from_list`）
- `962-973`（`_on_bookmark_del`）
- `997-1009`（`_on_bookmark_selected`）
- `679-693`（`_set_freq_hz`，用于记录上次成功频率）
- `806-808`（`_apply_step`，无真值时直接返回）
- `1125-1140`（`set_frequency_hz`，撤销回填入口）
- `277-360`（`_build_ui` 内的步进按钮区，新增撤销按钮）
- 行号可能漂移，落地前重测。

**现在的错误行为（一句）**
点删除按钮立刻删内存并落盘，不确认；误调谐后没有“回到上次频率”的入口；内置的“航空应急频率 121.500”和用户书签长得一模一样，且能被删（内存删掉、下次启动又回来）。

**改成什么**
1. 书签数据结构从 3 元组扩为 4 元组 `(freq_hz, name, mode, builtin: bool)`：
   - `load_bookmarks_from_list` 接受 4 元组（为兼容旧调用，3 元组按 `builtin=False` 归一化）；
   - `_refresh_bookmarks` 解包 4 项，显示名后追加 `（内置）`（原文，全角括号）；
   - `_on_bookmark_selected` 解包 4 项；
   - `_on_bookmark_add` 追加时写 `(hz, name, mode, False)`。
2. 删除确认（`_on_bookmark_del`）：
   - `builtin=True`：不弹删除确认，直接 `QMessageBox.information(self, "内置书签", f"「{name}」是内置频率，不能删除。下一步：用「收藏当前频率」保存你自己的频率。")`，列表不变、不落盘。
   - 用户书签：`QMessageBox.question(self, "删除书签", f"删除书签「{name}」？此操作不能撤销。", Yes|No, No)`；只有 Yes 才从内存删并 `_remove_bookmark_persisted(name)`。
3. 误调谐撤销（只保留一级）：
   - `__init__` 增 `self._last_good_freq_hz: Optional[float] = None`；
   - `_set_freq_hz` 在覆盖 `_freq_hz` 之前，若旧值非 None 则记入 `_last_good_freq_hz`；
   - 新增 `undo_tune()`：`_last_good_freq_hz` 为 None 时直接返回；否则取旧值、先把 `_last_good_freq_hz = None`，再 `_set_freq_hz(old)`（正常发调谐信号，不绕过硬件）；
   - 步进按钮区新增 `QPushButton("↶ 上次频率")`，`objectName="undoTuneBtn"`，连 `undo_tune`，仅在 `_last_good_freq_hz is not None` 时启用；随 `set_sdr_connected` 一起置灰。
4. `_apply_step`（806-808）补一行：`if self._freq_hz is None: return`，杜绝无真值时 TypeError（这是 8a 顺带护栏，独立于 main_window）。

**谁读谁写**
- `_last_good_freq_hz` 只由 `_set_freq_hz` 写、`undo_tune` 读并清空。
- `builtin` 只来自 `FrequencyManager`（`mbdsdr_ai/frequency_manager.py:36`），面板只读不改。

**反证（修前必红）**
- 测试文件：`tests/test_bookmark_panel_safety.py`（新建）。
- 断言：
```python
cp._bookmarks = [(121.5e6, "航空应急频率 121.500", "AM", True)]
cp._refresh_bookmarks()
assert "（内置）" in cp.bookmark_combo.itemText(0)      # 当前 4 元组解包直接 ValueError
cp.bookmark_combo.setCurrentIndex(0)
cp._on_bookmark_del()
# 修前：不确认、直接删
assert asked == [], "修前删除从不确认"                 # 当前 asked == []
assert len(cp._bookmarks) == 1                        # 当前变 0
```
- 撤销：
```python
cp._set_freq_hz(100e6)
cp._set_freq_hz(145.8e6)
cp.undo_tune()                                        # 当前无此方法
assert cp._freq_hz == 100e6
assert cp.undo_btn.isEnabled() is False
```
- 为什么能抓住“不可撤销/静默删除”：当前 963-973 没有 `QMessageBox`，删除即从 `_bookmarks` 移除并落盘；`asked`/列表长度两条断言必红。4 元组在当前 `_refresh_bookmarks` 的 `for i, (hz, name, mode)` 解包处直接报 ValueError，也是红。

### 8b｜`desktop/main_window.py`（写作时不干净，等交干净）

**目标文件 / 行号**
- `desktop/main_window.py:1292-1302`（启动时把 `FrequencyManager` 灌进控制面板）
- `4257-4272`（`_on_bookmark_added`，内存表新增）
- `4274-4283`（`_sync_spectrum_bookmarks`）
- `4284-4297`（`_on_bookmark_activated`）
- 行号可能漂移，落地前重测。

**改成什么**
1. 1295-1297 改为 4 元组：`[(b.freq_hz, b.name, b.mode, bool(b.builtin)) for b in self._freq_mgr.list() if b.freq_hz > 0]`；`self._bookmarks` 同步存 4 元组。
2. `_on_bookmark_added` 追加 `(freq_hz, name, mode, False)`。
3. `_sync_spectrum_bookmarks` / `_on_bookmark_activated` 解包 4 项；频谱竖旗仍只吃 `(hz, name)`，不显示“内置”标注，避免改 `spectrum_widget.py`（他线文件）。
4. 与 8a 的 4 元组协议必须同时上线；**先提交 8a，再提交 8b**，否则启动时 4 元组进 3 元组解包会崩。

**反证（修前必红）**
- 测试文件：`tests/test_bookmark_builtin_label.py`（新建；只测 MainWindow→ControlPanel 的传递）。
- 断言（用真实 `FrequencyManager`，`_default_bookmarks()` 含内置 121.500）：
```python
w = MainWindow()
items = [w.control_panel.bookmark_combo.itemText(i)
         for i in range(w.control_panel.bookmark_combo.count())]
assert any("航空应急频率 121.500（内置）" in t for t in items)   # 当前无“（内置）”
assert w.control_panel._bookmarks[0][3] is True                 # 当前只有 3 元组 / IndexError
```
- 为什么能抓住“假内置”：当前 1295-1296 只传 `(freq, name, mode)`，`builtin` 在 main_window 边界被丢掉，面板无从标注；两条断言在当前代码上都红。

**归属**
- `desktop/control_panel.py`：写作时干净（无 `M`），但同属参数治理线；落地前再确认一次。
- `desktop/main_window.py`：不干净，等交干净。
- `mbdsdr_ai/frequency_manager.py`：只读，不改（`builtin` 字段与 `remove()` 的“内置不可删”语义已存在）。

**取舍（已定稿）**
- 现状：内置项在列表里标「（内置）」且不可删；点删除弹「内置书签」说明不能删除，并给下一步（调到目标频点后用「收藏当前频率」建自定义书签）。落地提交 `50d9548`。
- 为什么不做「可删 + 可恢复」：`FrequencyManager.remove()` 只删 `builtin=False`，`_save_user()` 只写自定义项；要真删内置必须新增持久化的 `hidden_builtin` 集合与配置字段，跨 `mbdsdr_ai/frequency_manager.py` + 设置清单两条线，超出 8b 边界。
- 将来要做的条件：频率库线把 `hidden_builtin`（读写 `bookmarks.json`）与「恢复全部内置」入口定为正式 API，并在 `docs/settings-inventory.md` 登记该字段后，8b 再回改删除分支；在那之前维持“不可删 + 给下一步”。

---

## 落地顺序与提交边界（预演）

等 `main_window.py` 交干净后，严格按 1→4 先做前四张卡，每张一提交：

| 顺序 | 改动 | pathspec |
|---|---|---|
| 1 | 卡 1 | `git commit -m "fix(ux): 天空点卫星无设备不再报已调谐" -- desktop/main_window.py tests/test_sky_tune_honesty.py` |
| 2 | 卡 2 | `git commit -m "fix(ux): 调谐/增益/模式失败不落盘不写假频率" -- desktop/main_window.py tests/test_tune_failure_no_commit.py` |
| 3 | 卡 3 | `git commit -m "fix(ux): 重连不再回填 0 Hz 假频率" -- desktop/main_window.py tests/test_panel_connect_placeholder.py` |
| 4 | 卡 4 | `git commit -m "fix(ux): AI 拒绝天空动作给出可见原因与下一步" -- desktop/main_window.py tests/test_sky_action_visibility.py` |
| 5 | 卡 8a | `git commit -m "fix(ux): 删书签确认 + 内置标注 + 上次频率撤销" -- desktop/control_panel.py tests/test_bookmark_panel_safety.py` |
| 6 | 卡 5 | `git commit -m "fix(ux): 录音中关窗先确认并写明保存路径" -- desktop/main_window.py tests/test_close_recording_guard.py` |
| 7 | 卡 6 | `git commit -m "fix(ux): 无设备引导说人话 + 连接框禁用无效确定" -- desktop/main_window.py tests/test_no_device_onboarding.py` |
| 8 | 卡 7 | `git commit -m "fix(ux): 星象仪醒目退出入口与录音指示" -- desktop/main_window.py tests/test_planetarium_status.py` |
| 9 | 卡 8b | `git commit -m "fix(ux): 内置书签标注贯通到主窗口" -- desktop/main_window.py tests/test_bookmark_builtin_label.py` |

测试统一前置（Windows PowerShell）：
```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest tests/test_sky_tune_honesty.py -v
```

## 与其它线的冲突点（点名文件）

1. **`desktop/main_window.py`** —— 卡 1、2、3、4、5、6、7、8b 全部落在这里。写作时该文件有未提交改动（操作逻辑线）；这是最大冲突源。任何一张卡落地前必须重测行号。
2. **`desktop/control_panel.py`** —— 卡 8a 在这里。写作时干净，但参数治理线刚交回；若它再次提交断连文案（UX-05）或步进区布局，8a 的 `_refresh_bookmarks`/`_build_ui` 行号与 4 元组协议会正面冲突。
3. **`desktop/rf_sky_view.py`** —— 写作时仍有未提交改动。卡 4/7 只调用 `set_antenna`、`enter_horizon_view`，**不编辑**；若该线改了天空页布局，卡 4 的覆盖卡位置与卡 7 的入口可见性需要复核。
4. **`desktop/settings_panel.py` / `desktop/settings.py`** —— 写作时均不干净。卡 5 只调用 `settings.flush()`，不改实现；若该线改了 flush 时序，关窗保存顺序要复核。
5. **`mbdsdr_ai/frequency_manager.py`** —— 卡 8 只读 `Bookmark.builtin` 与 `remove()` 语义，不改；若其它线改 `list()` 返回类型，8a/8b 的 4 元组转换需复核。

## 本文不包含（避免范围蔓延）

- UX-05（控制面板断连仍显示 FM / “未录音”）：属上一批 `control_panel.py` 残留，本批条目清单未列；若 8a 落地时该文件仍归我，可另开一张卡补。
- UX-11（连接过程无进度）、UX-13（音频失败缺下一步）、UX-14（拔设备全局提示）：未在本批条目清单中，另行批准后再写卡。
