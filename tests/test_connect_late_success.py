# SPDX-License-Identifier: MIT
"""B2：连接超时之后后台才成功 —— UI 终态必须按硬件事实补回来。

复现：后台 ``connect()`` 比等待上限慢（本机 USB 打开 + 调谐器探测偶发超时）。
旧实现把超时当失败：写「连接失败」，且在后台仍卡在 ``connect()`` 时调
``backend.disconnect()``（抢同一句柄），标签回「未连接」；后台随后返回成功，
没有任何消费者，界面永远回不到已连接。再点连接则设备被自己占着，报 ACCESS(-3)。

判据：
1. 超时只算「结果未知」：不写「连接失败」、不 disconnect、状态栏给出下一步
   （等它自动补上 / 收尾结束后重试）；
2. 迟到成功回来时，状态条与设备面板**一起**修正——只修一处就是两个真值源；
3. 已被用户新意图作废的迟到结果，由回投方自己关闭后端，UI 不动。
"""
import os
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

#: 迟到结果回投靠跨线程 signal，测试里要持续泵事件才有机会派发。
_PUMP_SECONDS = 5.0


class _SlowBackend:
    """connect() 比等待上限慢的后端；只做记录，不碰硬件。"""

    def __init__(self, delay: float = 0.4, ok: bool = True):
        self.device = SimpleNamespace(name="FakeSDR", max_gain=0.0)
        self.status = SimpleNamespace(error="")
        self.delay = delay
        self.ok = ok
        self.disconnected = 0

    def connect(self) -> bool:
        time.sleep(self.delay)
        return self.ok

    def disconnect(self) -> None:
        self.disconnected += 1

    def get_status(self):
        return self.status


@pytest.fixture(scope="module")
def qapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def mw(qapp):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001
        pass
    w.close()


def _stub_heavy(mw, monkeypatch):
    """把连接成功后与硬件强绑定的环节换成 no-op，只留 UI 挂接。"""
    for name in ("_apply_connect_params", "_start_iq_streams",
                 "_sync_adsb_tuner_limit", "_backfill_real_rf_readings",
                 "_refresh_tx_readout", "_refresh_tx_button_state"):
        monkeypatch.setattr(mw, name, lambda *a, **k: None, raising=False)


def _wait_until(predicate, seconds: float = _PUMP_SECONDS) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_timeout_is_unknown_then_late_success_fixes_status_and_panels(
        mw, monkeypatch):
    """超时不是失败；后台成功回来后状态条与设备面板一起变成已连接。"""
    monkeypatch.setattr(type(mw), "_CONNECT_WAIT_TIMEOUT_MS", 100, raising=False)
    _stub_heavy(mw, monkeypatch)
    mw._active_sdr_backend = None
    backend = _SlowBackend(delay=0.4, ok=True)

    assert mw._connect_backend(backend) is False, "超时同步返回 False（本次没有结论）"

    msg = mw.statusBar().currentMessage()
    assert "结果未知" in msg, msg
    assert "自动补上" in msg and "重试" in msg, "必须给出明确下一步"
    assert backend.disconnected == 0, \
        "超时不许当场 disconnect：后台仍卡在 connect()，会抢同一句柄"

    assert _wait_until(lambda: mw._active_sdr_backend is backend), \
        "后台成功后必须由回投把后端挂上，否则界面永远停在未连接"

    # ① 状态条修正
    late_msg = mw.statusBar().currentMessage()
    assert "已连接" in late_msg and "补上" in late_msg, late_msg
    # ② 设备面板同一份真值（只修一处就是两个真值源）
    assert mw.status_conn.text().startswith("FakeSDR"), mw.status_conn.text()
    assert "FakeSDR" in mw.conn_label.text()
    assert mw.weather_panel._sdr_connected is True
    assert mw.connect_btn.isEnabled() is False
    assert mw.disconnect_btn.isEnabled() is True
    assert backend.disconnected == 0, "已经连上的后端不许被收尾路径关掉"


def test_late_success_after_user_disconnect_closes_backend(mw, monkeypatch):
    """用户已断开：迟到的成功由回投方关闭后端，UI 一律不动。"""
    monkeypatch.setattr(type(mw), "_CONNECT_WAIT_TIMEOUT_MS", 80, raising=False)
    _stub_heavy(mw, monkeypatch)
    mw._active_sdr_backend = None
    backend = _SlowBackend(delay=0.35, ok=True)

    assert mw._connect_backend(backend) is False
    mw._teardown_connection()  # 用户新意图：作废在途尝试

    assert _wait_until(lambda: backend.disconnected >= 1), \
        "作废的迟到成功必须被关闭，不能把设备留着占口"
    assert mw._active_sdr_backend is None, "用户已断开，UI 不许被迟到结果拽回已连接"
    assert backend.disconnected == 1
