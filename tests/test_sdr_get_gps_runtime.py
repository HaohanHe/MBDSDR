# SPDX-License-Identifier: MIT
"""AI 侧 GPS 工具必须读实时运行时快照，并如实带出卫星数/HDOP/天线告警。

背景（2026-11-22 真机）：桌面每秒把真实 GNSS fix 发布到
``runtime_sources``，但 ``sdr_get_gps`` 旧实现只查 ``mgr.gnss.status()``，
而 ``SDRBackendManager`` 根本没有 ``gnss`` 属性——AI 永远读不到实时定位。
本文件钉住新口径：快照优先、legacy 兜底、无数据如实报未连接。
"""
from __future__ import annotations

import time

from mbdsdr_ai import runtime_sources as rs_mod
from mbdsdr_ai.runtime_sources import RuntimeSources
from mbdsdr_ai.sdr_tools import _get_gps


class _FakeSources:
    def __init__(self, gnss):
        self._gnss = gnss

    def snapshot(self):
        return {"gnss": self._gnss}


def _use(monkeypatch, gnss):
    monkeypatch.setattr(
        rs_mod, "get_runtime_sources", lambda: _FakeSources(gnss))


def test_real_fix_with_sats_hdop_and_antenna_warning(monkeypatch):
    _use(monkeypatch, {
        "connected": True, "fix": True,
        "lat_deg": 1.2345678, "lon_deg": 2.3456789,
        "satellites": 9, "hdop": 1.3, "antenna": "open",
    })
    out = _get_gps(None)
    assert "卫星 9 颗" in out
    assert "HDOP 1.3" in out
    assert "天线开路" in out


def test_connected_but_no_fix_is_honest(monkeypatch):
    _use(monkeypatch, {"connected": True, "fix": False})
    assert "搜索中" in _get_gps(None)


def test_not_connected_says_so(monkeypatch):
    _use(monkeypatch, {"connected": False, "fix": False})
    assert "未连接" in _get_gps(None)


def test_legacy_mgr_gnss_fallback(monkeypatch):
    _use(monkeypatch, {"connected": False, "fix": False})

    class _Gnss:
        def status(self):
            return {"fix": True, "lat": 3.0, "lon": 4.0,
                    "numsats": 5, "hdop": 0.9}

    class _Mgr:
        gnss = _Gnss()

    out = _get_gps(_Mgr())
    assert "卫星 5 颗" in out
    assert "HDOP 0.9" in out


def test_record_fix_keeps_satellites_and_hdop():
    rs = RuntimeSources()
    rs.record_fix({
        "source": "real", "latitude": 1.0, "longitude": 2.0,
        "satellites": 9, "hdop": 1.3, "timestamp": time.time(),
    })
    gnss = rs.snapshot()["gnss"]
    assert gnss["satellites"] == 9
    assert gnss["hdop"] == 1.3
