# -*- coding: utf-8 -*-
# SPDX-License-Identifier: MIT
"""写回失败后的真值一致性：没写回的值不许在任何路径上「活过来」。

裁定（2026-11-14）：写回失败采用「保留显示值 + 状态行明示」，不回滚。代价
是界面上留着一个「看起来是真的、实际不会生效」的数，所以必须补一条边界：

  它不许被任何路径回填进 store，也不许在下次连接时被当成期望值。

本文件只钉这一条，不重复 ``tests/test_device_panel_session_lock.py`` 已覆盖
的置灰/回显/幂等。判据是**重新打开面板**：新面板从 store 渲染，显示的是
store 里的值；如果那条被拒的文本以任何方式回了库，这里就会红。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
for _p in (_ROOT, _DESKTOP):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import connect_request_store as store  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_store(isolated_profile):
    store.reset()
    yield
    store.reset()


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def _dev(serial: str = "A-0001", label: str = "RTL-A") -> dict:
    return {"driver": "rtlsdr", "label": label, "serial": serial,
            "device_args": {"index": 0}}


@pytest.fixture
def make_panel(monkeypatch):
    """按需构造面板；构造时禁掉真枚举，全部用喂进来的设备列表。"""
    _app()
    import desktop.device_panel as dp
    monkeypatch.setattr(dp.DevicePanel, "refresh_devices_async",
                        lambda self: None)
    panels = []

    def _make():
        widget = dp.DevicePanel()
        panels.append(widget)
        return widget

    yield _make
    for widget in panels:
        widget.close()


def _open_session_with_bad_sr(make_panel):
    """开一个会话，把采样率控件改成无法解析的文本，再断开。

    返回（面板, 设备身份键, 断开后的状态行）。
    """
    dev = _dev()
    panel = make_panel()
    panel._apply_devices([dev])
    key = panel._identities[0].key
    # 会话前的真值：2048000 / 30 / 0。
    store.update(key, sample_rate_hz=2048000, gain_db=30, ppm=0)
    panel._render_request(0)

    initial = store.ConnectRequest(sample_rate_hz=2048000, gain_db=30, ppm=0)
    panel.set_connected(True, "RTL-A", device=dev, initial=initial)
    assert panel.sr_edit.isEnabled() is False, "会话期必须置灰"

    # 置灰只拦用户输入，setText 仍会生效——这正是写回失败的现实入口。
    panel.sr_edit.setText("not-a-number")
    panel.set_connected(False)
    return panel, key, panel.status_label.text()


def test_rejected_writeback_never_reaches_store(make_panel):
    """断开时采样率解析失败 → store 三个字段都保持会话前的值。"""
    _panel, key, status = _open_session_with_bad_sr(make_panel)

    assert "未写回" in status, f"状态行必须明示未写回，实际：{status!r}"
    req = store.request_for(key)
    assert req.sample_rate_hz == 2048000, "被拒的文本不得回填进 store"
    assert req.gain_db == 30
    assert req.ppm == 0
    assert store.is_occupied(key) is False, "断开后会话占用必须归还"


def test_reopened_panel_renders_store_value_not_rejected_text(make_panel):
    """重新打开面板：显示 store 的值，而不是上次没写回的那串文本。"""
    _panel, key, _status = _open_session_with_bad_sr(make_panel)

    fresh = make_panel()
    fresh._apply_devices([_dev()])
    assert fresh._identities[0].key == key
    # 渲染走 format_sample_rate（会把 2048000 写成 2.048e+06），所以按数值比，
    # 不比字面量；关键判据是「不是那串被拒的文本」。
    shown = fresh.sr_edit.text()
    assert "not-a-number" not in shown
    assert float(shown) == pytest.approx(2048000.0), (
        "新面板必须从 store 渲染；若显示被拒的文本，说明它以某种路径活过来了。"
        f"实际：{shown!r}")
    assert fresh.gain_edit.text() == "30"
    assert fresh.ppm_edit.text() == "0"
