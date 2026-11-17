# SPDX-License-Identifier: MIT
"""指向 HUD 上的运行时数据源文案（Task 83）。

守四件事：

1. SDR 在线 + GNSS 在线：一行文案含「已连接」「GNSS」；
2. SDR 断开 + 仅手动站址：含「未连接」「手动」，且不出现「GNSS」冒充；
3. 不传 ``runtime_sources``：HUD 不新增键，输出与旧版逐字一致；
4. 快照读取异常：HUD 刷新与绘制照常，不抛。
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from mbdsdr_ai.sky_hud import build_hud  # noqa: E402

SIZE = (800.0, 600.0)
VIEW = {"center_az": 120.0, "center_alt": 45.0, "fov_deg": 2.3,
        "rotation_deg": 0.0, "min_fov": 0.5}
POSE = {"valid": True, "az_deg": 120.0, "alt_deg": 45.0,
        "roll_deg": 0.0, "fov_deg": 2.3}


def _online_snapshot():
    return {
        "sdr": {"connected": True, "device": "RTL-SDR", "reason": ""},
        "gnss": {"connected": True, "fix": True, "time_source": "nmea",
                 "precision": "NMEA 整秒授时", "site_source": "gnss"},
        "time": {"source": "gnss", "time_source": "nmea"},
        "site": {"source": "gnss", "lat_deg": 48.1, "lon_deg": 11.5},
    }


def _manual_snapshot():
    return {
        "sdr": {"connected": False, "device": None,
                "reason": "SDR 未连接（无可用后端）"},
        "gnss": {"connected": False, "fix": False, "time_source": "none",
                 "precision": "本机时钟", "site_source": "manual"},
        "time": {"source": "system", "time_source": "none"},
        "site": {"source": "manual", "lat_deg": 0.0, "lon_deg": 0.0},
    }


def test_online_sdr_and_gnss_are_reported_as_connected():
    hud = build_hud(POSE, [], "L3", VIEW, SIZE,
                    runtime_sources=_online_snapshot())
    text = hud["sources_text"]
    assert "已连接" in text
    assert "GNSS" in text
    # 原始结构原样保留，详细码值只在这里出现。
    assert hud["sources"]["gnss"]["time_source"] == "nmea"
    for abbr in ("pps", "nmea", "az", "alt"):
        assert abbr not in text.lower()


def test_offline_sdr_with_manual_site_does_not_claim_gnss():
    hud = build_hud(POSE, [], "L3", VIEW, SIZE,
                    runtime_sources=_manual_snapshot())
    text = hud["sources_text"]
    assert "未连接" in text
    assert "手动" in text
    assert "GNSS" not in text


def test_without_runtime_sources_no_new_keys_and_output_unchanged():
    base = build_hud(POSE, [], "L3", VIEW, SIZE)
    explicit_none = build_hud(POSE, [], "L3", VIEW, SIZE,
                              runtime_sources=None)
    assert "sources" not in base
    assert "sources_text" not in base
    assert base == explicit_none


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


def test_runtime_sources_reach_the_view_hud(qapp, monkeypatch):
    import mbdsdr_ai.runtime_sources as runtime_sources
    from rf_sky_view import RFSkyView

    class _FakeSources:
        def snapshot(self):
            return _online_snapshot()

    monkeypatch.setattr(runtime_sources, "get_runtime_sources",
                        lambda: _FakeSources())
    v = RFSkyView()
    try:
        v.set_sky_pointing_mode(True)
        assert v.update_sky_pose(POSE, [], "L3") is True
        assert "已连接" in v.sky_hud["sources_text"]
        v.grab()
    finally:
        v.close()


def test_snapshot_failure_does_not_break_hud(qapp, monkeypatch):
    import mbdsdr_ai.runtime_sources as runtime_sources
    from rf_sky_view import RFSkyView

    def _boom():
        raise RuntimeError("快照不可用")

    monkeypatch.setattr(runtime_sources, "get_runtime_sources", _boom)
    v = RFSkyView()
    try:
        v.set_sky_pointing_mode(True)
        assert v.update_sky_pose(POSE, [], "L3") is True
        hud = v.sky_hud
        assert hud is not None and hud["hint"] == "已锁定"
        assert "sources_text" not in hud
        v.grab()
    finally:
        v.close()
