# SPDX-License-Identifier: MIT
"""链路六状态可见性回归（无硬件，offscreen）。

钉住用户要求：断线到恢复的每一步都必须在界面上看得见，不能只给最终
「已断开」，也不能在静默期继续显示「已连接」（假状态）。

状态与用户可见文案的对应（本文件即验收表）：

====================================  ==========================================
状态                                  用户必须看到
====================================  ==========================================
连接中                                「连接中 <设备>」
已连接                                「<设备名>」
静默超时（后端 connected 但无数据）      「数据流中断 <n>s（连接仍在）」
升级断开（后端 disconnected/failed）    「未连接」+「设备已断开：<原因>（错误码…）」
自动重连中                             桌面端未实现（不伪造；见 test_no_auto_reconnect_policy）
重连成功 / 放弃                        同上（只能手动点「连接」）
====================================  ==========================================

反证方式：删掉 `_on_health_tick` 里的 stale 分支（让它继续按掉线收尾），
`test_stale_is_visible_not_disconnected` 与 `test_silent_gap_visible_within_2s`
立刻变红。
"""
import os
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


class _StubStatus:
    def __init__(self, connected=True, error="", age=None, link_state="",
                 error_category="", error_code="", buffer_level=""):
        self.connected = connected
        self.error = error
        self.last_data_age_s = age
        self.link_state = link_state
        self.error_category = error_category
        self.error_code = error_code
        self.buffer_level = buffer_level


class _StubBackend:
    """判掉线所需的最小后端接口 + 连接中状态探针。"""

    def __init__(self, **status_kw):
        self.status = _StubStatus(**status_kw)
        self.disconnect_calls = 0
        self.connect_calls = 0
        self.on_connect = None

    def get_status(self):
        return self.status

    def connect(self):
        self.connect_calls += 1
        if self.on_connect is not None:
            self.on_connect()
        return True

    def disconnect(self):
        self.disconnect_calls += 1
        self.status.connected = False

    @property
    def device(self):
        return SimpleNamespace(name="FakeSDR")


class _FakePipeline:
    def __init__(self):
        self.shutdown_calls = 0

    def shutdown(self):
        self.shutdown_calls += 1


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    import main_window as mw
    w = mw.MainWindow()
    QTest.qWait(30)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        pass


def _prepare(w, backend):
    w._teardown_connection()
    w.statusBar().clearMessage()
    w._read_fail_signals = 0
    w._backend_lost_handled = False
    w._active_sdr_backend = backend
    w._pipeline = _FakePipeline()
    w._health_grace_until = 0.0
    w._health_has_data = True
    w.status_conn.setText("FakeSDR")
    w.conn_label.setText("  状态: FakeSDR  ")
    return w


def _text(w):
    return w.status_conn.text() + " | " + w.statusBar().currentMessage()


def test_connecting_state_is_visible_during_blocking_connect(window):
    """连接是同步阻塞调用：进入 connect() 之前就必须先把「连接中」写上。"""
    seen = {}

    def _probe():
        seen["label"] = window.status_conn.text()

    b = _StubBackend(connected=False)
    b.on_connect = _probe
    ok = window._connect_backend(b)
    assert ok is True
    assert "连接中" in seen.get("label", ""), (
        f"connect() 期间用户看不到连接中，实际标签={seen.get('label')!r}")
    assert window.status_conn.text() == "FakeSDR"


def test_stale_is_visible_not_disconnected(window):
    """后端说 stale（连接仍在、5s 无数据）：不能拆连接，也不能显示已连接。"""
    b = _StubBackend(connected=True,
                     error="超过 5s 未收到 IQ 数据（心跳缺失），连接仍在但数据流已中断",
                     age=5.2, link_state="stale",
                     error_category="idle", error_code="link_half_open")
    w = _prepare(window, b)
    w._on_health_tick()
    assert w._active_sdr_backend is b, "stale 是连接仍在的等待态，不能拆连接"
    txt = _text(w)
    assert "数据流中断" in txt, txt
    assert "连接仍在" in txt, txt
    assert "link_half_open" in txt, f"错误码没有上浮：{txt}"
    assert w.status_conn.text() != "未连接", f"stale 不能显示未连接：{txt}"


def test_silent_gap_visible_within_2s(window):
    """后端还没到 5s stale、但 UI 已 2s 没真实数据：必须降级可见，不许假在线。"""
    b = _StubBackend(connected=True, error="", age=2.4,
                     link_state="connected")
    w = _prepare(window, b)
    w._on_health_tick()
    assert w._active_sdr_backend is b, "2s 空档只降级可见，不得直接断开"
    txt = _text(w)
    assert "数据流中断" in txt, txt
    assert "连接仍在" in txt, txt


def test_disconnected_escalation_reports_code(window):
    """后端 10s 硬判 disconnected：拆连接 + 错误码上浮 + 明说不会自动重连。"""
    b = _StubBackend(connected=False,
                     error="读取 IQ 失败：连接已被对端重置",
                     age=10.1, link_state="disconnected",
                     error_category="reset", error_code="link_reset")
    w = _prepare(window, b)
    w._on_health_tick()
    assert w._active_sdr_backend is None
    assert w.status_conn.text() == "未连接"
    msg = w.statusBar().currentMessage()
    assert msg.startswith("设备已断开："), msg
    assert "link_reset" in msg, msg
    assert "不会自动重连" in msg, msg
    assert w.connect_btn.isEnabled() is True, "断开后必须让用户能手动重连"


def test_recovery_restores_connected_label(window):
    """数据恢复（后端回到 connected）：标签必须从降级态还原，不能卡在中断文案。"""
    b = _StubBackend(connected=True, error="", age=5.2,
                     link_state="stale", error_category="idle")
    w = _prepare(window, b)
    w._on_health_tick()
    assert "数据流中断" in w.status_conn.text()
    b.status = _StubStatus(connected=True, error="", age=0.2,
                           link_state="connected")
    w._on_health_tick()
    assert w._active_sdr_backend is b
    assert w.status_conn.text() == "FakeSDR", w.status_conn.text()


def test_buffer_overflow_code_is_visible_without_disconnect(window):
    """环形缓冲积压 high：数据还在流，只提示 buffer_overflow，不拆连接。

    提示面在底坞状态位（`status_processing`）而不是状态栏最后一行：
    2026-11-10 用户实指状态栏再占一行会挤掉界面，代码改走 tooltip。
    本用例钉的是「积压可见 + 错误码可查 + 不拆连接」三件事。
    """
    b = _StubBackend(connected=True, error="", age=0.3,
                     link_state="connected", buffer_level="high",
                     error_code="buffer_overflow")
    w = _prepare(window, b)
    w._on_health_tick()
    assert w._active_sdr_backend is b, "缓冲积压不是断链，不能拆连接"
    assert "缓冲积压" in w.status_processing.text(), (
        f"积压必须在底坞状态位可见，实际：{w.status_processing.text()!r}")
    assert "buffer_overflow" in w.status_processing.toolTip(), (
        f"错误码必须可查（tooltip），实际：{w.status_processing.toolTip()!r}")
    assert w.status_conn.text() == "FakeSDR", "数据仍在流，不能降级成中断"


def test_no_auto_reconnect_policy(window):
    """桌面端当前没有自动重连：断开后不得伪造「正在重连/重连成功」。

    这条是现状钉死，不是愿望：若以后接入 ReconnectionManager，
    必须同步改文案与这条用例，并把三场景实测补上。
    """
    b = _StubBackend(connected=False, error="连接已断开",
                     age=10.0, link_state="disconnected",
                     error_category="reset", error_code="link_reset")
    w = _prepare(window, b)
    w._on_health_tick()
    assert w._active_sdr_backend is None
    QTest.qWait(1200)
    assert w._active_sdr_backend is None, "桌面端不应偷偷自动重连（当前策略=手动）"
    msg = w.statusBar().currentMessage()
    assert "正在重连" not in msg, f"没有自动重连却显示正在重连=假状态：{msg}"


def test_doppler_chip_tooltip_follows_target_loss(window):
    """多普勒芯片：目标失效且无 reason 时，tooltip 必须变成「无补偿」。

    旧实现只在 ``st.reason`` 非空时重设 tooltip，过境结束/目标被清（最常见的
    失效路径，reason 为空）时，芯片已显示「多普勒 --」，悬停却还是上一颗星的
    「…点击关闭补偿（调谐回标称）」——指向一个不存在的补偿。
    """
    st_valid = SimpleNamespace(
        target_name="ISS", valid=True, doppler_hz=1200.0,
        downlink_hz=145_800_000.0, corrected_freq_hz=145_801_200.0,
        enabled=True, reason="")
    window._update_doppler_chip(SimpleNamespace(state=st_valid))
    assert "ISS" in window.doppler_chip.toolTip(), (
        f"前置条件不成立：有效目标时 tooltip 未带目标名：{window.doppler_chip.toolTip()!r}")

    st_lost = SimpleNamespace(
        target_name="", valid=False, doppler_hz=None,
        downlink_hz=145_800_000.0, corrected_freq_hz=None,
        enabled=False, reason="")
    window._update_doppler_chip(SimpleNamespace(state=st_lost))

    assert window.doppler_chip.text() == "多普勒 --"
    tip = window.doppler_chip.toolTip()
    assert "无有效目标" in tip and "点击关闭补偿" not in tip, (
        f"目标失效后 tooltip 仍是旧的补偿说明：{tip!r}")
