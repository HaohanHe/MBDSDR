# SPDX-License-Identifier: MIT
"""设备会话占用期间，main_window 的四个 UI 槽走同一道写门。

判据（与 docs §六 一致）：

1. **拒绝 + 可读原因**：采样率/增益/PPM/VFO 带宽四个槽在会话中被拒时
   ``backend.set_*`` 零调用、store 意图原值、状态栏原因非空且写明
   怎么解除（「请先断开设备（结束会话）再修改」）。
2. **正常连接永不被拒**：连接载荷经 ``initial`` 一次性交给
   ``begin_session``（快照=本次生效值），连接流程不调 ``update``，
   因此不会撞上自己刚关上的门。
3. **占用态置灰**：``control_panel.set_session_occupied`` 置灰六个连接
   参数控件并写清原因；连接后的能力探测不得把它们重新点亮。
4. **AI 路径**：``sdr_set_gain`` 被拒时用冻结值回显，不把 AI 的请求值
   写进意图/滑杆。

反证：摘掉任一槽的门（照常下发）→ 对应用例里 ``backend.calls`` 非空、红。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

import connect_request_store as store  # noqa: E402

_KEY = "rtlsdr|serial:mw-session-gate"
_RELEASE_HINT = "请先断开设备（结束会话）再修改"


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def mw(qtapp):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001
        pass
    w.close()


class _RecordingBackend:
    """只登记调用、不碰硬件；所有写都返回成功。"""

    def __init__(self):
        self.calls = []

    def set_gain(self, db):
        self.calls.append(("gain", float(db)))
        return True

    def set_sample_rate(self, hz):
        self.calls.append(("sample_rate", float(hz)))
        return True

    def set_ppm(self, ppm):
        self.calls.append(("ppm", int(ppm)))
        return True

    def set_bandwidth(self, bw):
        self.calls.append(("bandwidth", float(bw)))
        return True

    def get_gain(self):
        return 20.0

    def get_sample_rate(self):
        return 2_400_000.0

    def get_bandwidth(self):
        return 180_000.0

    def get_status(self):
        return SimpleNamespace(error="设备拒绝")


def _open_session(mw, key=_KEY):
    """模拟「已连接并登记会话」：快照=连接载荷（不写意图）。"""
    mw.device_panel._current_dev_key = key
    store.begin_session(key, initial=store.ConnectRequest(
        sample_rate_hz=2_400_000.0,
        gain_db=20.0,
        ppm=5,
        bandwidth_hz=180_000.0))
    cp = mw.control_panel
    cp.set_sdr_connected(True)
    cp.set_sample_rate_options([1_024_000.0, 2_400_000.0, 3_200_000.0],
                               2_400_000.0)
    cp.set_gain(20.0)
    cp.set_ppm_applied(5)
    cp.set_vfo_bandwidth(180_000.0)
    cp.set_session_occupied(True, store.session_reject_reason(key))
    mw._record_sr = 2_400_000.0
    mw._vfo_bw = 180_000.0
    mw._recording = False
    mw._active_sdr_backend = _RecordingBackend()
    return cp


def _close_session(mw, key=_KEY):
    mw._active_sdr_backend = None
    mw.device_panel._current_dev_key = None
    store.end_session(key)
    mw.control_panel.set_sdr_connected(False)


def test_four_slots_rejected_without_touching_hardware(mw):
    """会话中四个槽全被拒：硬件零调用、意图原值、原因写清怎么解除。"""
    cp = _open_session(mw)
    backend = mw._active_sdr_backend
    try:
        mw._on_gain_changed(45)
        assert _RELEASE_HINT in mw.statusBar().currentMessage()

        mw._on_sample_rate_changed(3_200_000.0)
        assert _RELEASE_HINT in mw.statusBar().currentMessage()

        mw._on_ppm_changed(50)
        assert _RELEASE_HINT in mw.statusBar().currentMessage()

        ok = mw._on_vfo_bandwidth_changed(200_000.0)
        assert ok is False
        assert _RELEASE_HINT in mw.statusBar().currentMessage()

        # ① 硬件一个字节都没碰（反证：摘掉任一门，这里会出现对应调用）
        assert backend.calls == []
        # ② 意图保持原值（begin_session 只写快照，不写意图）
        intent = store.intent_for(_KEY)
        assert intent.sample_rate_hz is None
        assert intent.gain_db is None
        assert intent.ppm is None
        assert intent.bandwidth_hz is None
        # ③ 控件回到冻结的本次生效值，不停在被拒的新值上
        assert cp.gain_slider.value() == 20
        assert cp.sample_rate_combo.currentData() == pytest.approx(2_400_000.0)
        assert cp.ppm_spin.value() == 5
        assert cp.vfo_bw_combo.currentData() == pytest.approx(180_000.0)
        # 会话快照仍然是本次生效值
        snap = store.request_for(_KEY)
        assert (snap.sample_rate_hz, snap.gain_db, snap.ppm,
                snap.bandwidth_hz) == (2_400_000.0, 20.0, 5, 180_000.0)
    finally:
        _close_session(mw)


def test_normal_write_is_not_rejected_after_session(mw):
    """反证：门不是「永远拒写」——结束会话后同一路径正常下发。"""
    cp = _open_session(mw)
    _close_session(mw)
    assert store.is_occupied(_KEY) is False
    mw.device_panel._current_dev_key = _KEY
    backend = _RecordingBackend()
    mw._active_sdr_backend = backend
    cp.set_sdr_connected(True)
    # 清掉上一用例留下的拒绝提示：本用例要断言「这次没有拒绝」
    mw.statusBar().clearMessage()
    try:
        mw._on_gain_changed(30)
        mw._on_ppm_changed(7)

        assert backend.calls == [("gain", 30.0), ("ppm", 7)]
        assert store.intent_for(_KEY).gain_db == 30.0
        assert store.intent_for(_KEY).ppm == 7
        assert _RELEASE_HINT not in mw.statusBar().currentMessage()
    finally:
        mw._active_sdr_backend = None
        mw.device_panel._current_dev_key = None
        cp.set_sdr_connected(False)


def test_connect_payload_opens_session_before_gate(mw, monkeypatch):
    """连接载荷一次性交给 begin_session：连接流程不调 update，零拒绝。"""
    cp = mw.control_panel
    cp.set_sdr_connected(False)
    payload = store.ConnectRequest(sample_rate_hz=2_400_000.0,
                                   gain_db=20.0, ppm=3,
                                   bandwidth_hz=180_000.0)
    seen = {}
    real_dp = mw.device_panel

    def _fake_connected(connected, **kw):
        seen["connected"] = connected
        seen["initial"] = kw.get("initial")
        real_dp._current_dev_key = _KEY
        if connected:
            store.begin_session(_KEY, initial=kw.get("initial"))
        else:
            store.end_session(_KEY)
            real_dp._current_dev_key = None

    monkeypatch.setattr(real_dp, "set_connected", _fake_connected)
    try:
        mw._panels_set_sdr_connected(True, session_initial=payload)

        assert seen["connected"] is True
        assert seen["initial"] is payload, "连接载荷必须原样作为会话起点"
        assert store.is_occupied(_KEY) is True
        snap = store.request_for(_KEY)
        assert (snap.sample_rate_hz, snap.gain_db, snap.ppm,
                snap.bandwidth_hz) == (2_400_000.0, 20.0, 3, 180_000.0)
        # 会话已开 → 控制面板连接参数同时置灰（不再有可编辑假象）
        assert cp.gain_slider.isEnabled() is False

        mw._panels_set_sdr_connected(False)
        assert store.is_occupied(_KEY) is False
    finally:
        store.end_session(_KEY)
        real_dp._current_dev_key = None
        cp.set_sdr_connected(False)
        mw._panels_set_sdr_connected(False)


def test_frozen_controls_stay_disabled_after_capability_probe(mw):
    """占用态置灰 + 专属 tooltip；能力探测/解除占用后都不残留旧提示。"""
    cp = mw.control_panel
    cp.set_sdr_connected(True)
    reason = store.session_reject_reason("rtlsdr|serial:tip")
    cp.set_session_occupied(True, reason)

    def _frozen():
        return [getattr(cp, name) for name in cp._SESSION_FROZEN_WIDGETS]

    for w in _frozen():
        assert w.isEnabled() is False, f"{w!r} 占用期间必须置灰"
        assert _RELEASE_HINT in w.toolTip()

    # 连接后紧跟着的能力探测（_apply_connect_params 末尾）不得重新点亮
    cp.set_device_capabilities(gain=True, agc=True, ppm=True,
                               offset_tuning=True)
    for w in _frozen():
        assert w.isEnabled() is False, \
            f"{w!r} 被能力探测重新点亮：占用判据丢了"
        assert _RELEASE_HINT in w.toolTip()

    cp.set_session_occupied(False)
    for w in _frozen():
        assert w.isEnabled() is True
        assert _RELEASE_HINT not in w.toolTip(), \
            "解除占用后不得残留上一条状态的 tooltip"
    cp.set_sdr_connected(False)


def test_ai_gain_sync_rejected_uses_frozen_value(mw):
    """AI 改增益被拒：滑杆/意图用冻结值回显，不写 AI 的请求值。"""
    cp = _open_session(mw)
    try:
        mw._sync_ui_for_tool("sdr_set_gain", {"gain_db": 45.0})

        assert cp.gain_slider.value() == 20
        assert store.intent_for(_KEY).gain_db is None, \
            "被拒的 AI 请求不得写进意图（否则三块面板一起显示没生效的值）"
        assert _RELEASE_HINT in mw.statusBar().currentMessage()
        assert mw._active_sdr_backend.calls == []
    finally:
        _close_session(mw)
