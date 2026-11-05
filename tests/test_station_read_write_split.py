# SPDX-License-Identifier: MIT
"""站址读写边界：(0,0) 显式站址只读放行、写/控制仍拒绝。

修前：`mbdsdr_ai/station.py:evaluate_station` 把显式 user/gnss 的 (0,0)
（几内亚湾真实坐标）也判 `null_island_0_0` 不可信，导致
`get_satellite_pass` 返回 `station_untrusted`。

口径：
- 只读/计算（`trust_for_calculation`）：显式来源的 (0,0)/海上 → warning + 照常算；
- 写/控制（`trust_for_tx`、`evaluate_station`、`tune_satellite`）→ 仍严格拒绝；
- `station_status()` 对外语义不变，仍严格。

固定用例：ISS TLE epoch 2008-264、基准 2008-09-20 06:00Z、24h、10° 门。
"""
import os
import sys
import types
from datetime import datetime, timezone

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

ISS_NAME = "ISS (ZARYA)"
ISS_L1 = "1 25544U 98067A   08264.51780074  .00016553  00000-0  10270-3 0  2897"
ISS_L2 = "2 25544  51.6400 247.4627 0006703 130.5360 325.0288 15.72125391563530"
FIXED_UTC = datetime(2008, 9, 20, 6, 0, 0, tzinfo=timezone.utc)
FIXED_UNIX = FIXED_UTC.timestamp()


def _fixed_controller(monkeypatch, tmp_path):
    """离线控制器：内置 TLE + 固定 TimeEngine，站址由各测试显式设置。"""
    from mbdsdr_ai.core.controller import SDRController
    import mbdsdr_ai.decoders as decoders
    import mbdsdr_ai.new_spacetime as nst
    import mbdsdr_ai.orbit as orbit

    monkeypatch.setitem(decoders.BUILTIN_TLE, ISS_NAME, (ISS_L1, ISS_L2))

    def _offline(*_a, **_k):
        raise RuntimeError("offline（测试固定用内置 TLE）")

    monkeypatch.setattr(orbit, "fetch_tle_record", _offline)
    monkeypatch.setattr(
        nst, "get_time_engine",
        lambda: types.SimpleNamespace(now_unix=lambda: FIXED_UNIX))

    return SDRController(backend=None, config_dir=str(tmp_path))


@pytest.mark.parametrize("source", ["user", "gnss"])
def test_explicit_null_island_read_path_computes_passes(
        monkeypatch, tmp_path, source):
    """显式 (0,0)（user 与 gnss 各一次）→ get_satellite_pass 正常出条数。"""
    ctrl = _fixed_controller(monkeypatch, tmp_path)
    if source == "gnss":
        ctrl.set_ground_station_from_gnss(0.0, 0.0, 0.0, accuracy_m=10.0)
    else:
        ctrl.set_ground_station(0.0, 0.0, 0.0, source="user")

    out = ctrl.get_satellite_pass(25544, min_elevation_deg=10.0,
                                  window_hours=24)
    assert "error" not in out, out
    assert out["passes_in_window"] == 2, (
        f"(0,0) 固定夹具应算出 2 条过境，实际 {out['passes_in_window']} 条："
        f"{out}")
    assert out.get("station_warning"), "放宽必须留痕：结果须带 station_warning"
    assert "null_island_0_0" in out["station_warning"]


def test_explicit_null_island_write_path_still_rejected(monkeypatch, tmp_path):
    """同一 (0,0) 走写路径（设备调谐）仍被拒，错误码与文案都要钉住。"""
    ctrl = _fixed_controller(monkeypatch, tmp_path)
    ctrl.set_ground_station(0.0, 0.0, 0.0, source="user")

    out = ctrl.tune_satellite(25544)
    assert out["error"] == "station_untrusted", out
    assert "拒绝按错误位置做多普勒调谐" in out["suggestion"], out
    assert any("null_island" in r for r in out["reasons"]), out


def test_legacy_station_status_stays_strict(monkeypatch, tmp_path):
    """老入口 station_status() 对外语义不变：显式 (0,0) 仍严格拒绝。"""
    ctrl = _fixed_controller(monkeypatch, tmp_path)
    ctrl.set_ground_station(0.0, 0.0, 0.0, source="user")
    assert ctrl.station_status()["trusted"] is False
    assert ctrl.station_status_for_calculation()["trusted"] is True


def test_calculation_warning_does_not_leak_into_tx():
    """计算口径的 warning 不影响 tx 判定：同一 (0,0) 两个入口结论相反。"""
    from mbdsdr_ai.station import station_from_user, trust_for_calculation, trust_for_tx

    fix = station_from_user(0.0, 0.0)
    calc = trust_for_calculation(fix)
    tx = trust_for_tx(fix)
    assert calc["trusted"] is True
    assert "null_island_0_0" in calc["station_warning"]
    assert tx["trusted"] is False
    assert any("null_island" in r for r in tx["reasons"])
