# SPDX-License-Identifier: MIT
"""predict_upcoming_passes(max_sats) 按「卫星数」截取，而非按「过境条数」。

契约（docstring）：最多返回多少颗卫星的过境，取仰角峰值最高的前 N 颗。
旧实现把全部过境条目按峰值排序后 collected[:max_sats] —— 一颗多过境
卫星会占满配额，且选中卫星的其余过境被静默丢弃。
monkeypatch predict_passes 注入 canned 过境（消费方即 sat_passes 模块
自身），不依赖 SGP4/墙钟，精确复现并供反证。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai import sat_passes as sp  # noqa: E402
from mbdsdr_ai.sat_passes import (  # noqa: E402
    GroundStation, SatellitePass, predict_upcoming_passes,
)

STATION = GroundStation(lat_deg=43.9, lon_deg=125.3, alt_m=50.0)


def _tle(title, norad):
    """真实形状 TLE（改编号/标题；_parse_tle 只查前缀与长度）。"""
    return [
        title,
        f"1 {norad}U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897",
        f"2 {norad}  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530",
    ]


def _pass(ts, name, rise_min, max_alt, dur_min=10.0):
    rise = ts.utc(2008, 9, 20, 6, 0, 0) + rise_min / 1440.0
    return SatellitePass(
        rise_time=rise,
        set_time=rise + dur_min / 1440.0,
        max_alt_time=rise + dur_min / 2880.0,
        max_alt=float(max_alt),
        rise_az=0.0,
        set_az=180.0,
        duration=dur_min * 60.0,
        sat_name=name,
    )


def _inject(monkeypatch, table):
    """table: {标题行: [SatellitePass, ...]}。"""
    def _fake(tle_lines, ground_station, **kwargs):
        return list(table.get(tle_lines[0], []))
    monkeypatch.setattr(sp, "predict_passes", _fake)


class TestMaxSatsCountsSatellites:
    def test_multi_pass_satellite_does_not_fill_quota(self, monkeypatch):
        # A 三条过境（峰 50/40/30），B 一条（60），C 一条（20）。
        # max_sats=2 → 选 B+A 两颗星，A 的三条全部保留（共 4 条）。
        ts = sp.make_timescale()
        _inject(monkeypatch, {
            "SAT A": [_pass(ts, "SAT A", 0, 50.0),
                      _pass(ts, "SAT A", 100, 40.0),
                      _pass(ts, "SAT A", 200, 30.0)],
            "SAT B": [_pass(ts, "SAT B", 50, 60.0)],
            "SAT C": [_pass(ts, "SAT C", 150, 20.0)],
        })
        out = predict_upcoming_passes(
            [_tle("SAT A", "10001"), _tle("SAT B", "10002"),
             _tle("SAT C", "10003")], STATION, max_sats=2)
        names = [e["pass"].sat_name for e in out]
        assert len(out) == 4
        assert names.count("SAT A") == 3
        assert names.count("SAT B") == 1
        assert "SAT C" not in names

    def test_selection_ranks_by_satellite_peak(self, monkeypatch):
        # A 峰值 80（另有一条 10 的低过境），B 70，C 60。
        # max_sats=2 → A（两条都要）+B；不能按单条峰值把 A 的低过境挤掉。
        ts = sp.make_timescale()
        _inject(monkeypatch, {
            "SAT A": [_pass(ts, "SAT A", 0, 80.0),
                      _pass(ts, "SAT A", 300, 10.0)],
            "SAT B": [_pass(ts, "SAT B", 100, 70.0)],
            "SAT C": [_pass(ts, "SAT C", 200, 60.0)],
        })
        out = predict_upcoming_passes(
            [_tle("SAT A", "10001"), _tle("SAT B", "10002"),
             _tle("SAT C", "10003")], STATION, max_sats=2)
        names = [e["pass"].sat_name for e in out]
        assert len(out) == 3
        assert names.count("SAT A") == 2
        assert names.count("SAT B") == 1
        assert "SAT C" not in names

    def test_result_sorted_by_rise_time(self, monkeypatch):
        ts = sp.make_timescale()
        _inject(monkeypatch, {
            "SAT A": [_pass(ts, "SAT A", 200, 50.0),
                      _pass(ts, "SAT A", 0, 40.0)],
            "SAT B": [_pass(ts, "SAT B", 100, 60.0)],
        })
        out = predict_upcoming_passes(
            [_tle("SAT A", "10001"), _tle("SAT B", "10002")],
            STATION, max_sats=2)
        rises = [e["pass"].rise_time.tt for e in out]
        assert rises == sorted(rises)
        assert [e["pass"].sat_name for e in out] == ["SAT A", "SAT B", "SAT A"]

    def test_duplicate_tle_is_one_satellite(self, monkeypatch):
        # 同一颗星列两次仍是一颗：组数 1 <= max_sats=1 → 不截取。
        ts = sp.make_timescale()
        _inject(monkeypatch, {
            "SAT A": [_pass(ts, "SAT A", 0, 50.0),
                      _pass(ts, "SAT A", 100, 40.0),
                      _pass(ts, "SAT A", 200, 30.0)],
        })
        a = _tle("SAT A", "10001")
        out = predict_upcoming_passes([a, a], STATION, max_sats=1)
        assert len(out) == 6
        assert all(e["pass"].sat_name == "SAT A" for e in out)

    def test_none_zero_and_ge_count_return_all(self, monkeypatch):
        ts = sp.make_timescale()
        _inject(monkeypatch, {
            "SAT A": [_pass(ts, "SAT A", 0, 50.0),
                      _pass(ts, "SAT A", 100, 40.0)],
            "SAT B": [_pass(ts, "SAT B", 50, 60.0)],
            "SAT C": [_pass(ts, "SAT C", 150, 20.0)],
        })
        tles = [_tle("SAT A", "10001"), _tle("SAT B", "10002"),
                _tle("SAT C", "10003")]
        for limit in (None, 0, 3, 99):
            out = predict_upcoming_passes(tles, STATION, max_sats=limit)
            assert len(out) == 4, f"max_sats={limit}"

    def test_tle_lines_preserved_per_entry(self, monkeypatch):
        ts = sp.make_timescale()
        _inject(monkeypatch, {
            "SAT A": [_pass(ts, "SAT A", 0, 50.0)],
            "SAT B": [_pass(ts, "SAT B", 50, 60.0)],
        })
        a, b = _tle("SAT A", "10001"), _tle("SAT B", "10002")
        out = predict_upcoming_passes([a, b], STATION, max_sats=1)
        assert len(out) == 1
        assert out[0]["tle_lines"] == b
        assert set(out[0].keys()) == {"pass", "tle_lines"}

    def test_empty_and_invalid(self, monkeypatch):
        assert predict_upcoming_passes([], STATION) == []
        _inject(monkeypatch, {})
        assert predict_upcoming_passes(
            [["garbage"], _tle("SAT A", "10001")], STATION,
            max_sats=1) == []