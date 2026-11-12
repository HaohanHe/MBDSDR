# SPDX-License-Identifier: MIT
"""后端写入返回 False 时，桌面层不得当作成功（假成功护栏）。

覆盖四条：
  #8 `_on_apply_demod_requested`：模式切换失败时，最终文案必须带失败原因，
     且不得同时出现「已应用解调」；
  #6 `_on_agc_changed`：AGC 写入 False/异常必须可见，且不落盘、不改 UI；
  #7 `_on_vfo_bandwidth_changed`：带宽写入 False/异常必须可见，不落盘、不改 UI；
  #1–#5 连接后恢复参数：失败项汇总成「连接后参数未全部生效：<项>」，
     采样率被拒时下拉必须显示设备实际生效值，不假装已生效。

每条都是行为断言；把对应旧实现改回去即红（各自 docstring 里写了反证点）。
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


class _Backend:
    """可控失败的后端桩：每个写入都返回 bool，并在 status.error 留原因。"""

    def __init__(self, *, fail=(), actual_sr=2_400_000.0, actual_gain=20.0):
        self.fail = set(fail)
        self.error = ""
        self.actual_sr = float(actual_sr)
        self.actual_gain = float(actual_gain)
        self.calls = []

    def _done(self, label, what, value=None):
        self.calls.append((label, value))
        if label in self.fail:
            self.error = f"设备拒绝{what}"
            return False
        self.error = ""
        return True

    def set_demod(self, mode):
        return self._done("set_demod", "解调模式切换", mode)

    def set_agc(self, enabled):
        return self._done("set_agc", "AGC 设置", enabled)

    def set_bandwidth(self, bw):
        return self._done("set_bandwidth", "带宽设置", float(bw))

    def set_sample_rate(self, sr):
        return self._done("set_sample_rate", "采样率设置", float(sr))

    def set_frequency(self, hz):
        return self._done("set_frequency", "频率设置", float(hz))

    def set_ppm(self, ppm):
        return self._done("set_ppm", "PPM 设置", int(ppm))

    def set_offset_tuning(self, on):
        return self._done("set_offset_tuning", "offset tuning 设置", bool(on))

    def set_gain(self, db):
        return self._done("set_gain", "增益设置", float(db))

    def get_sample_rate(self):
        return self.actual_sr

    def get_frequency(self):
        return 98e6

    def get_gain(self):
        return self.actual_gain

    def get_status(self):
        return types.SimpleNamespace(error=self.error)


@pytest.fixture(scope="module")
def window(isolated_profile):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    w._active_sdr_backend = None


# ---------------------------------------------------------------- #8 假应用
def test_apply_demod_failure_never_reads_as_success(window):
    """模式切换失败 → 最终文案含失败原因，且不得出现「已应用解调」。

    反证：把 `_on_apply_demod_requested` 末尾那句无条件
    `showMessage("已应用解调：…")` 加回去 → 本用例红。
    """
    w = window
    be = _Backend(fail={"set_demod"})
    w._active_sdr_backend = be
    w.statusBar().clearMessage()

    w._on_apply_demod_requested("WFM", 0)

    msg = w.statusBar().currentMessage()
    assert "设备拒绝解调模式切换" in msg, "失败原因没有出现在最终文案里"
    assert "已应用解调" not in msg, "失败后仍出现成功字样（假应用）"


def test_apply_demod_success_still_reports_success(window):
    """成功路径保留原文案（防修 #8 时把成功也吞了）。"""
    w = window
    be = _Backend()
    w._active_sdr_backend = be
    w.statusBar().clearMessage()

    w._on_apply_demod_requested("AM", 0)

    assert "已应用解调" in w.statusBar().currentMessage()
    assert "未生效" not in w.statusBar().currentMessage()


# ---------------------------------------------------------------- #6 AGC
def test_agc_false_is_visible_and_not_persisted(window):
    """AGC 写入返回 False → 状态栏人话；配置不落新值；开关回到原态。

    反证：把 `_on_agc_changed` 改回「先落盘再无视返回值」→ 本用例红。
    """
    w = window
    be = _Backend(fail={"set_agc"})
    w._active_sdr_backend = be
    w.settings.set("agc_enabled", True)
    w.control_panel.set_agc(True)
    w.statusBar().clearMessage()

    w._on_agc_changed(False)

    msg = w.statusBar().currentMessage()
    assert "设备拒绝AGC 设置" in msg, "AGC 写入失败没有任何可见提示"
    assert bool(w.settings.get("agc_enabled")) is True, "失败值被落盘"
    assert w.control_panel.agc_check.isChecked() is True, "开关停在新值=假装已生效"


# ---------------------------------------------------------------- #7 VFO 带宽
def test_vfo_bandwidth_false_is_visible_and_not_persisted(window):
    """带宽写入返回 False → 状态栏人话；不落盘；本地带宽与频谱高亮不动。

    反证：把 `_on_vfo_bandwidth_changed` 改回「先改 UI/落盘、后端异常 pass」→ 红。
    """
    w = window
    be = _Backend(fail={"set_bandwidth"})
    w._active_sdr_backend = be
    before_bw = float(w._vfo_bw) if w._vfo_bw else None
    w.settings.set("bandwidth_hz", before_bw or 0.0)
    w.statusBar().clearMessage()

    w._on_vfo_bandwidth_changed(2_400.0)

    msg = w.statusBar().currentMessage()
    assert "设备拒绝带宽设置" in msg, "带宽写入失败没有任何可见提示"
    assert w._vfo_bw == before_bw, "失败后本地带宽被改成新值（UI 假装已生效）"
    assert float(w.settings.get("bandwidth_hz") or 0.0) == (before_bw or 0.0), \
        "失败值被落盘"


# ---------------------------------------------------------------- #1–#5 连接参数
def test_connect_param_failure_is_visible_and_ui_not_faked(window):
    """恢复采样率被设备拒绝 → 状态栏列明未生效项；下拉显示真实生效值。

    反证：把 `_apply_connect_params` 改回「每项 try/except 各自吞掉 False」→ 红。
    """
    w = window
    be = _Backend(fail={"set_sample_rate"}, actual_sr=2_400_000.0)
    w.statusBar().clearMessage()

    failures = w._apply_connect_params(
        be, sample_rate=3_200_000.0, bandwidth=0.0, freq=98e6,
        ppm=0, offset=False, agc=True, gain=20.0)

    assert failures == ["采样率"], f"未生效项清单不对: {failures}"
    msg = w.statusBar().currentMessage()
    assert "连接后参数未全部生效" in msg and "采样率" in msg

    actual = w.control_panel.sample_rate_combo.currentData()
    assert float(actual) == 2_400_000.0, \
        f"采样率下拉仍显示请求值而不是设备实际值: {actual}"


# ---------------------------------------------------------------- 增益成功读回
def test_gain_success_shows_device_value_keeps_user_intent(window):
    """请求 49 dB、设备夹到 33 dB → 标签显示 33 dB，配置仍是 49（用户意图）。

    反证：删掉成功分支里的 `_show_real_gain(backend)` → 标签停在 49，红。
    """
    w = window
    be = _Backend(actual_gain=33.0)
    w._active_sdr_backend = be
    w.control_panel.set_sdr_connected(True)
    w.control_panel.gain_slider.setValue(49)
    w.settings.set("gain_db", 20.0)
    w.statusBar().clearMessage()

    w._on_gain_changed(49)

    assert "未生效" not in w.statusBar().currentMessage(), "成功路径误报失败"
    assert w.control_panel.gain_val.text() == "33.0 dB", \
        "标签仍显示请求值 49 dB（后端夹值被藏起来）"
    assert float(w.settings.get("gain_db")) == 49.0, \
        "配置被写成设备值，用户意图丢失"


# ------------------------------------------------ 应用解调 / 书签：带宽必须真下发
def test_apply_demod_applies_bandwidth_to_backend(window):
    """「应用解调」带的带宽必须真的写到设备，不是只改界面。

    旧实现里这行是 `hasattr(self, "_on_vfo_bandwidth_changed_ui")` 守卫的死
    代码（全文件无该方法），带宽永不下发。这行本身删掉也「看不出区别」——
    因为并没有异常，所以这里钉行为而不是钉源码文本：
    反证：把任意一处改回 `hasattr(self, "_on_vfo_bandwidth_changed_ui")` 的
    假调用 → 后端收不到 set_bandwidth，本用例红。
    """
    w = window
    be = _Backend()
    w._active_sdr_backend = be
    w.settings.set("bandwidth_hz", 0.0)
    w.statusBar().clearMessage()

    w._on_apply_demod_requested("NFM", 12_000)

    bw_calls = [v for (label, v) in be.calls if label == "set_bandwidth"]
    assert bw_calls == [12_000.0], f"带宽没有真下发: 实际后端调用 {be.calls}"
    assert float(w._vfo_bw) == 12_000.0, "本地带宽没跟上设备实际值"
    assert float(w.settings.get("bandwidth_hz")) == 12_000.0, "带宽没有落盘"
    assert "已应用解调" in w.statusBar().currentMessage()


def test_apply_demod_bandwidth_failure_not_reported_as_success(window):
    """模式切换成功但带宽失败 → 不得整句报「已应用解调」，带宽不落盘。

    反证：把 `bw_applied` 判定删掉（无条件写「已应用解调」）→ 本用例红。
    """
    w = window
    be = _Backend(fail={"set_bandwidth"})
    w._active_sdr_backend = be
    w.settings.set("bandwidth_hz", 2_400.0)
    w.statusBar().clearMessage()

    w._on_apply_demod_requested("NFM", 3_000)

    msg = w.statusBar().currentMessage()
    assert "已应用解调" not in msg, "带宽失败仍报「已应用解调」（半成功冒充成功）"
    assert "带宽未生效" in msg and "设备拒绝带宽设置" in msg
    assert float(w.settings.get("bandwidth_hz")) == 2_400.0, "失败带宽被落盘"
    assert float(w._vfo_bw) != 3_000.0, "失败带宽被写进本地 VFO"


def test_bookmark_tune_applies_bandwidth(window):
    """书签（频率+模式+带宽）点击后，带宽同样必须真下发。

    反证：把 `_on_bookmark_tune_requested` 里那行改回 `hasattr(...)` 假调用 → 红。
    """
    w = window
    be = _Backend()
    w._active_sdr_backend = be
    w.settings.set("bandwidth_hz", 0.0)
    w.statusBar().clearMessage()

    w._on_bookmark_tune_requested(145_500_000.0, "FM", 15_000)

    bw_calls = [v for (label, v) in be.calls if label == "set_bandwidth"]
    assert bw_calls == [15_000.0], f"书签带宽没有真下发: {be.calls}"
    assert float(w.settings.get("bandwidth_hz")) == 15_000.0


# ---------------------------------------------------------------- 卫星点选调谐
def test_sky_satellite_tune_false_keeps_label_and_reports(window):
    """点选卫星自动调谐失败 → tuned 不置位：标签不动，状态栏给原因。

    反证：把 `if ok is False` 分支删掉（恢复无条件 tuned=True）→ 本用例红。
    """
    w = window
    be = _Backend(fail={"set_frequency"})
    w._active_sdr_backend = be
    w.statusBar().clearMessage()
    old_label = w.freq_label.text()

    obj = types.SimpleNamespace(
        obj_type="satellite", name="NOAA 19", azimuth_deg=10.0,
        elevation_deg=20.0, frequency_hz=137_500_000.0)
    w._on_sky_object_clicked(obj)

    assert w.freq_label.text() == old_label, "调谐失败后频率标签被改成目标频率"
    msg = w.statusBar().currentMessage()
    assert "未调谐频率" in msg, "失败后没有任何未调谐提示"
    assert "设备拒绝频率设置" in msg, "失败原因没有显示"


# ------------------------------------------------- 能力探测：支持 ≠ 有 set_* 方法
class _NoCapabilityBackend(_Backend):
    """有 set_* 方法、但明确报告「没有该能力」的后端桩。

    真实后端里对应的就是继承基类空操作（FileIQ/NetworkIQ 的增益/AGC）：
    方法存在且返回 True，但其实什么也没有写进硬件。
    """

    def supports_gain(self):
        return False

    def supports_agc(self):
        return False

    def supports_ppm(self):
        return False

    def supports_offset_tuning(self):
        return False


def _bare_device():
    from mbdsdr_ai.sdr_backend import SDRDevice
    return SDRDevice(
        device_type="file", device_id="test", name="测试设备",
        frequency_range=(0.0, 0.0), sample_rate_range=(0.0, 48e6),
        max_gain=49.0)


def test_base_backend_does_not_fake_gain_or_agc_success():
    """继承基类空操作的后端：set_gain/set_agc 必须 False，且不留假读数。

    反证：把基类 ``_apply_gain``/``_apply_agc`` 的默认「返回 True 的空操作」
    当成功（旧实现）→ 两个 set_* 都返回 True、status 被写成从未下发的值，
    本用例红。
    """
    from mbdsdr_ai.sdr_backend import SDRBackend

    class _NoApply(SDRBackend):
        pass

    be = _NoApply(_bare_device())
    be.status.connected = True
    be.status.gain_db = 12.0
    be.status.agc_enabled = False

    assert be.supports_gain() is False, "继承空操作却被判成支持增益"
    assert be.supports_agc() is False, "继承空操作却被判成支持 AGC"

    assert be.set_gain(30.0) is False, "不支持增益却报成功（假成功）"
    assert be.status.gain_db == 12.0, "增益读数被写成从未下发的请求值"
    assert "不支持" in be.status.error

    assert be.set_agc(True) is False, "不支持 AGC 却报成功（假成功）"
    assert be.status.agc_enabled is False
    assert "不支持" in be.status.error


def test_connect_skips_unsupported_capabilities_and_disables_controls(window):
    """设备明说没有 AGC/增益/PPM/Offset → 不计失败、界面说明、控件置灰。

    反证：把 ``_apply_connect_params`` 的 ``_supports`` 改回「有 set_* 就
    为真」（恒真）→ 四项都会被下发、状态栏不再出现「本设备不支持」、控件
    保持可点，本用例红。
    """
    w = window
    be = _NoCapabilityBackend()
    w.control_panel.set_sdr_connected(True)
    w.statusBar().clearMessage()

    failures = w._apply_connect_params(
        be, sample_rate=2_400_000.0, bandwidth=0.0, freq=98e6,
        ppm=0, offset=False, agc=False, gain=20.0)

    assert failures == [], f"「不支持」被记成了「未生效」: {failures}"
    msg = w.statusBar().currentMessage()
    assert "本设备不支持" in msg, f"界面没有说明能力缺失: {msg!r}"
    for label in ("AGC", "增益", "PPM", "Offset Tuning"):
        assert label in msg, f"{label} 未在说明里点名: {msg!r}"

    wrote = {label for (label, _v) in be.calls}
    assert wrote.isdisjoint(
        {"set_gain", "set_agc", "set_ppm", "set_offset_tuning"}), \
        f"不支持的能力仍被下发: {be.calls}"

    cp = w.control_panel
    assert not cp.agc_check.isEnabled(), "AGC 控件仍可点，没有反映能力"
    assert not cp.gain_slider.isEnabled(), "增益滑杆仍可点，没有反映能力"
    assert not cp.ppm_spin.isEnabled(), "PPM 输入仍可编辑，没有反映能力"
    assert not cp.offset_check.isEnabled(), "Offset 开关仍可点，没有反映能力"
    assert "本设备不支持" in cp.agc_check.toolTip()
    assert cp.agc_check.isChecked() is False, "不支持的 AGC 停在勾选态"


def test_unsupported_agc_is_explained_not_blamed_on_device(window):
    """点 AGC 但设备没有该能力 → 说「不支持」，不报「可能设备坏了」。

    反证：删掉 ``_on_agc_changed`` 里的能力探测（回到无条件
    ``_apply_hw_action``）→ 文案变成「设置 AGC 未生效：…可能原因：SDR 已
    断开…」，本用例红。
    """
    w = window
    be = _NoCapabilityBackend()
    w._active_sdr_backend = be
    w.control_panel.set_sdr_connected(True)
    w.control_panel.set_agc(True)
    w.statusBar().clearMessage()

    w._on_agc_changed(False)

    msg = w.statusBar().currentMessage()
    assert "不支持 AGC" in msg, f"没有说清是能力缺失: {msg!r}"
    assert "未生效" not in msg, "把「没这个能力」说成了「这次没做成」"
    assert w.control_panel.agc_check.isChecked() is False, "开关没退回真实态"
    assert ("set_agc", False) not in be.calls, "不支持的能力仍然被下发"


def test_unsupported_gain_is_skipped_and_not_persisted(window):
    """拖增益但设备没有该能力 → 说明跳过、不落盘、读数回设备值。

    反证：删掉 ``_on_gain_changed`` 里的能力探测 → 会下发并落盘 49 dB，
    本用例红。
    """
    w = window
    be = _NoCapabilityBackend(actual_gain=20.0)
    w._active_sdr_backend = be
    w.control_panel.set_sdr_connected(True)
    w.settings.set("gain_db", 20.0)
    w.statusBar().clearMessage()

    w._on_gain_changed(49)

    msg = w.statusBar().currentMessage()
    assert "不支持增益" in msg, f"没有说清是能力缺失: {msg!r}"
    assert "未生效" not in msg, "把「没这个能力」说成了「这次没做成」"
    assert ("set_gain", 49.0) not in be.calls, "不支持的能力仍然被下发"
    assert float(w.settings.get("gain_db")) == 20.0, "不支持的请求值被落盘"
    assert w.control_panel.gain_val.text() == "20.0 dB", \
        "读数没有回到设备真实值"
