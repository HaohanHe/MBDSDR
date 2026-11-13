# SPDX-License-Identifier: MIT
"""过境刷新必须在后台线程：GUI 线程不联网、重入被拒、有可见「更新中…」。

修前：``refresh_passes`` 在 UI 线程同步 fetch（每颗星 5 s 超时 × 10 颗），
请求期间窗口冻结，按钮只置灰没有任何文字反馈。
"""
import os
import sys
import threading
import time
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def panel(qapp):
    from desktop.new_spacetime_panel import NewSpacetimePanel

    p = NewSpacetimePanel()
    yield p
    p.close()


def _tle_entry():
    from mbdsdr_ai.new_spacetime_tle import TLEEntry

    return TLEEntry(name=ISS_NAME, line1=ISS_L1, line2=ISS_L2, catnr=25544)


def _wait_until(qapp, predicate, timeout=8.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_refresh_runs_off_gui_thread_and_shows_loading(panel, monkeypatch, qapp):
    from desktop import new_spacetime_panel as nsp

    panel.set_observer(39.9, 116.4, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    main_name = threading.current_thread().name
    seen = []

    def _fetch(*_a, **_k):
        seen.append(threading.current_thread().name)
        time.sleep(0.15)
        return _tle_entry()

    monkeypatch.setattr(panel._tle_mgr, "fetch", _fetch)

    panel.refresh_btn.click()
    assert panel.refresh_btn.text() == "更新中…", "加载期间必须有可见反馈"
    assert panel.refresh_btn.isEnabled() is False

    # 重入：正在跑时再点/再调都必须被拒绝
    panel.refresh_btn.click()
    panel.refresh_passes_async()

    assert _wait_until(
        qapp,
        lambda: panel.refresh_btn.isEnabled()
        and panel.refresh_btn.text() == "刷新过境"), "刷新未在超时内收尾"

    assert seen, "没有发起 TLE 请求"
    assert seen[0] != main_name, (
        f"TLE 抓取仍跑在 GUI 线程 {seen[0]!r}：窗口会冻结")
    assert len(seen) == 1, f"重入未被拒绝，fetch 调了 {len(seen)} 次"


def test_failure_in_worker_is_visible_and_button_recovers(panel, monkeypatch, qapp):
    from desktop import new_spacetime_panel as nsp

    panel.set_observer(39.9, 116.4, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])

    def _boom(*_a, **_k):
        raise RuntimeError("模拟抓取线程崩溃")

    monkeypatch.setattr(panel._tle_mgr, "fetch", _boom)

    panel.refresh_btn.click()
    assert _wait_until(qapp, lambda: panel.refresh_btn.isEnabled(),
                       timeout=5.0), "异常后按钮未恢复"

    assert not panel.passes_hint.isHidden()
    text = panel.passes_hint.text()
    assert text.startswith("刷新失败：RuntimeError"), (
        f"后台异常没有带回可见原因：{text!r}")


def test_sync_api_still_refreshes_for_direct_callers(panel, monkeypatch):
    """设置面板应用后直接调用 refresh_passes 仍需同步生效。"""
    from desktop import new_spacetime_panel as nsp
    from mbdsdr_ai.new_spacetime import get_time_engine

    panel.set_observer(0.0, 0.0, 0.0)
    monkeypatch.setattr(nsp, "DEFAULT_SATELLITES", [(ISS_NAME, 25544)])
    monkeypatch.setattr(panel._tle_mgr, "fetch", lambda *_a, **_k: _tle_entry())
    get_time_engine().set_unix(
        datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc).timestamp())

    panel.refresh_passes()

    assert panel.passes_table.rowCount() == 2, "同步入口不再产出过境"
    assert panel.refresh_btn.isEnabled() is True
    assert panel.refresh_btn.text() == "刷新过境"
