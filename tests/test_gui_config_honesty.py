#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
GUI 配置落盘诚实性测试
======================

红线（AGENTS.md §2）：持久化绝不把演示值冒充真实状态。

回归钉扎：_save_gui_config 曾用
    getattr(control_panel, '_current_freq_fm', tokens().AI["demo_freq_mhz"])
而 `_current_freq_fm` 是幽灵属性名（全仓零定义，真实属性是 `_freq_hz`），
导致每次保存都把演示频率 98.5 当成真实频率落盘。同理 volume 兜底 30、
`_show_waterfall` 也是幽灵名。取不到真实状态时必须**不写键**，而不是编造。

运行: QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_gui_config_honesty.py -v
"""
import json
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _fake_self(tmp_path, cp=None, spectrum=None):
    """构造调用 _save_gui_config 所需的最小 self。"""
    return SimpleNamespace(
        width=lambda: 800,
        height=lambda: 600,
        x=lambda: 10,
        y=lambda: 20,
        isMaximized=lambda: False,
        _current_theme="default",
        _observer_lat=None,
        _observer_lon=None,
        _gnss_port="",
        _gnss_baudrate=9600,
        control_panel=cp,
        spectrum=spectrum,
        _ntrip_dialog=None,
        _read_ntrip_from_disk=lambda: None,
    )


def _save_and_read(stub, tmp_path, monkeypatch):
    from main_window import MainWindow
    cfg_dir = tmp_path / ".mbdsdr"
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(cfg_dir))
    os.makedirs(cfg_dir, exist_ok=True)
    MainWindow._save_gui_config(stub)
    cfg_file = cfg_dir / "gui_config.json"
    assert cfg_file.exists(), "配置文件必须写出"
    with open(cfg_file, encoding="utf-8") as f:
        return json.load(f)


def test_real_freq_written_from_real_attribute(qapp, tmp_path, monkeypatch):
    """真实 _freq_hz 必须换算成 MHz 落盘。"""
    cp = SimpleNamespace(_freq_hz=101.7e6, _current_mode="FM",
                         volume_slider=SimpleNamespace(value=lambda: 42))
    out = _save_and_read(_fake_self(tmp_path, cp=cp), tmp_path, monkeypatch)
    assert out.get("current_freq_mhz") == pytest.approx(101.7), \
        f"必须写真实频率 101.7，实际: {out.get('current_freq_mhz')}"
    assert out.get("current_mode") == "FM"
    assert out.get("volume") == 42


def test_demo_freq_never_written(qapp, tmp_path, monkeypatch):
    """无 control_panel 时绝不落演示频率 98.5（幽灵属性回归）。"""
    out = _save_and_read(_fake_self(tmp_path, cp=None), tmp_path, monkeypatch)
    assert "current_freq_mhz" not in out, \
        f"缺真实来源必须不写键，实际写了: {out.get('current_freq_mhz')}"
    assert "current_mode" not in out
    assert "volume" not in out
    # 显式钉死：演示值不得以任何形式出现在文件里
    dumped = json.dumps(out)
    assert "98.5" not in dumped, "演示频率 98.5 绝不能出现在落盘配置中"


def test_freq_none_omits_key(qapp, tmp_path, monkeypatch):
    """_freq_hz 为 None（断连、从未调谐）时必须省略键，不写 0/演示值。"""
    cp = SimpleNamespace(_freq_hz=None, _current_mode=None,
                         volume_slider=SimpleNamespace(value=lambda: 30))
    out = _save_and_read(_fake_self(tmp_path, cp=cp), tmp_path, monkeypatch)
    assert "current_freq_mhz" not in out, \
        f"未调谐时不得写频率键，实际: {out.get('current_freq_mhz')}"
    assert "current_mode" not in out


def test_waterfall_reads_real_state(qapp, tmp_path, monkeypatch):
    """瀑布可见性必须从 _state.show_waterfall 真实读取。"""
    spectrum = SimpleNamespace(_state=SimpleNamespace(show_waterfall=False))
    out = _save_and_read(_fake_self(tmp_path, spectrum=spectrum), tmp_path, monkeypatch)
    assert out.get("show_waterfall") is False, \
        f"必须写真实瀑布状态 False，实际: {out.get('show_waterfall')}"
