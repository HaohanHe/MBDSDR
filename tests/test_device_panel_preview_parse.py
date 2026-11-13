# SPDX-License-Identifier: MIT
"""设备面板的 store 预览必须与输入框、等价值标签走同一份采样率解析。

旧行为：``_push_request_from_editors`` 只认纯 Hz（裸 ``float``）。用户填
``2.048M`` 时等价值标签显示 ``= 2.048 MS/s``、连接时 ``parse_sample_rate``
按 2.048 MHz 下发，但 store 里的意图值仍是上一次的采样率——别的面板读摘要
就看到与输入框不一致的数。修复是复用 ``parse_sample_rate``。

反证：把该处改回裸 ``float(...)``，``test_unit_suffix_updates_store_preview``
必红。
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


def _dev() -> dict:
    return {"driver": "rtlsdr", "label": "RTL-A", "serial": "A-0001",
            "device_args": {"index": 0}}


@pytest.fixture
def panel(monkeypatch):
    _app()
    import desktop.device_panel as dp

    # 构造面板会异步枚举外部设备：本文件用手喂列表，禁掉真枚举。
    monkeypatch.setattr(dp.DevicePanel, "refresh_devices_async",
                        lambda self: None)
    widget = dp.DevicePanel()
    widget._apply_devices([_dev()])
    yield widget
    widget.close()


def _key(panel) -> str:
    return panel._identities[0].key


def test_unit_suffix_updates_store_preview(panel):
    """用户填 2.048M：等价值与 store 预览必须是同一个数。"""
    key = _key(panel)
    store.update(key, sample_rate_hz=1_024_000.0)
    panel.sr_edit.setText("2.048M")
    assert "2.048" in panel.sr_equiv.text(), "等价值标签应先识别该写法"

    panel._push_request_from_editors()

    assert store.intent_for(key).sample_rate_hz == pytest.approx(2.048e6), (
        "预览必须按统一解析更新，不能保留旧采样率")


def test_bare_hz_still_updates_store(panel):
    """纯数字按 Hz：复用解析不许破坏原有写法。"""
    key = _key(panel)
    panel.sr_edit.setText("2048000")

    panel._push_request_from_editors()

    assert store.intent_for(key).sample_rate_hz == pytest.approx(2.048e6)


def test_unparsable_text_keeps_previous_value(panel):
    """解析不了的值不写：保持原值，不塞 0 或假值。"""
    key = _key(panel)
    store.update(key, sample_rate_hz=1_024_000.0)
    panel.sr_edit.setText("abc")

    panel._push_request_from_editors()

    assert store.intent_for(key).sample_rate_hz == pytest.approx(1.024e6)
