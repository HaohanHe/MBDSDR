# SPDX-License-Identifier: MIT
"""AI 粗指向工具读本机实时 GSV（不靠模型手填卫星表）。

背景（2026-11-22）：``gnss_sky_fit`` 原本只能由模型把 GSV 帧当参数传进来，
而模型拿不到串口。桌面每次轮询把 GSV/GSA 发布到 ``runtime_sources``，
工具在省略参数时读最近一帧（10 秒内），过期就明确拒绝。
"""
from __future__ import annotations

from mbdsdr_ai import runtime_sources as rs_mod
from mbdsdr_ai.gnss_tools import sky_fit_from_args
from mbdsdr_ai.runtime_sources import RuntimeSources

_FRAMES = [
    {"talker": "GP", "sats": [
        {"id": 1, "elevation": 10.0, "azimuth": 0.0, "snr_db": 40.0},
        {"id": 2, "elevation": 40.0, "azimuth": 90.0, "snr_db": 30.0},
        {"id": 3, "elevation": 70.0, "azimuth": 180.0, "snr_db": 20.0},
    ]},
]


class _FakeSources:
    def __init__(self, block):
        self._block = block

    def snapshot(self):
        return {"gnss_sats": self._block}


def test_record_gsv_visible_in_snapshot():
    rs = RuntimeSources()
    rs.record_gsv(_FRAMES)
    block = rs.snapshot()["gnss_sats"]
    assert block["frames"] == _FRAMES
    assert block["age_s"] is not None and block["age_s"] >= 0


def test_record_gsv_accepts_talker_mapping():
    rs = RuntimeSources()
    rs.record_gsv({"GP": _FRAMES[0]})
    assert rs.snapshot()["gnss_sats"]["frames"] == [_FRAMES[0]]


def test_stale_gsv_is_not_exposed():
    rs = RuntimeSources()
    rs.record_gsv(_FRAMES, now=1000.0)
    block = rs.snapshot(now_utc=1011.0)["gnss_sats"]
    assert block["frames"] is None
    assert "过期" in block["reason"]


def test_sky_fit_uses_live_gsv_when_args_empty(monkeypatch):
    monkeypatch.setattr(
        rs_mod, "get_runtime_sources", lambda: _FakeSources({"frames": _FRAMES}))
    out = sky_fit_from_args({})
    assert out["input_source"] == "live_gsv"
    assert out["satellite_count"] == 3


def test_sky_fit_without_any_input_refuses(monkeypatch):
    monkeypatch.setattr(
        rs_mod, "get_runtime_sources", lambda: _FakeSources({"frames": None}))
    out = sky_fit_from_args({})
    assert out["ok"] is False
    assert out["reason"] == "no_satellites"
    assert "GSV" in out["hint"]


def test_explicit_frames_still_take_priority(monkeypatch):
    monkeypatch.setattr(
        rs_mod, "get_runtime_sources", lambda: _FakeSources({"frames": []}))
    out = sky_fit_from_args({"frames": _FRAMES})
    assert out["input_source"] == "gsv_frames"
    assert out["satellite_count"] == 3
