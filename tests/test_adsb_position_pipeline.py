# SPDX-License-Identifier: MIT
"""ADS-B 位置管线：CPR 偶/奇配对 + IQ→解码→跟踪→中枢→要素 全链。

背景（2026-11-16 实测断线）：``adsb_lite`` 只吐原始 CPR 字段（cpr_odd /
cpr_lat / cpr_lon），``AircraftTracker`` 只认 lat/lon——中间的偶/奇配对
没人做，IQ 路径的飞机**永远没有位置、永远画不上图**。跟踪器文档承诺
「位置只在 CPR 偶/奇帧配对成功后才有值」，本文件把这句承诺钉成可跑的线。
"""
import os
import sys
import time

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.adsb import (  # noqa: E402
    bits_to_bytes, build_long_frame, encode_cpr_airborne)
from mbdsdr_ai.adsb_map import AircraftTracker  # noqa: E402
from mbdsdr_ai.adsb_lite import decode_adsb, synthesize_modes_iq  # noqa: E402
from mbdsdr_ai.position_service import (  # noqa: E402
    reset_position_service)
from mbdsdr_ai.spacetime_features import (  # noqa: E402
    FeatureCollection, upsert_position_report)

LAT, LON = 39.9, 116.4


def _make_pos_frame(odd: bool, clat: int, clon: int) -> bytes:
    """DF17 TC=11 空中位置帧（配方同 tests/test_adsb_real_roundtrip.py）。"""
    mm = [0] * 56

    def sb2(a, b, vv):
        for i in range(b - a + 1):
            mm[a - 1 + (b - a + 1) - 1 - i] = (vv >> i) & 1

    sb2(1, 5, 11)
    sb2(22, 22, 1 if odd else 0)
    sb2(23, 39, clat)
    sb2(40, 56, clon)
    return build_long_frame(0x8D, 0x40621D, bits_to_bytes(mm))


def _pair_frames():
    enc = encode_cpr_airborne(LAT, LON)
    return (_make_pos_frame(False, enc["even_cprlat"], enc["even_cprlon"]),
            _make_pos_frame(True, enc["odd_cprlat"], enc["odd_cprlon"]))


def test_single_cpr_frame_never_yields_position():
    tr = AircraftTracker()
    enc = encode_cpr_airborne(LAT, LON)
    tr.update({"icao": "40621D", "cpr_odd": False,
               "cpr_lat": enc["even_cprlat"], "cpr_lon": enc["even_cprlon"]})
    ac = tr.all()[0]
    assert ac.lat is None and ac.lon is None, "单帧不许硬凑位置"


def test_even_odd_pair_yields_position():
    tr = AircraftTracker()
    enc = encode_cpr_airborne(LAT, LON)
    tr.update({"icao": "40621D", "cpr_odd": False,
               "cpr_lat": enc["even_cprlat"], "cpr_lon": enc["even_cprlon"]})
    tr.update({"icao": "40621D", "cpr_odd": True,
               "cpr_lat": enc["odd_cprlat"], "cpr_lon": enc["odd_cprlon"]})
    ac = tr.all()[0]
    assert ac.lat == pytest.approx(LAT, abs=0.01)
    assert ac.lon == pytest.approx(LON, abs=0.01)


def test_stale_pair_is_not_forced():
    """偶/奇相隔超过窗口就不配——不用旧对凑数，等下一对。"""
    tr = AircraftTracker()
    enc = encode_cpr_airborne(LAT, LON)
    tr.update({"icao": "40621D", "cpr_odd": False,
               "cpr_lat": enc["even_cprlat"], "cpr_lon": enc["even_cprlon"]})
    # 把偶帧时间拨老 30s（白盒制造时差，模拟真实老化）
    slot = tr._cpr["40621D"]
    clat, clon, _ = slot[False]
    slot[False] = (clat, clon, time.time() - 30.0)
    tr.update({"icao": "40621D", "cpr_odd": True,
               "cpr_lat": enc["odd_cprlat"], "cpr_lon": enc["odd_cprlon"]})
    ac = tr.all()[0]
    assert ac.lat is None and ac.lon is None, "超窗旧对不得硬凑位置"


def test_full_iq_chain_lands_on_feature_collection():
    """合成 IQ → 真解码 → 跟踪（配对）→ 位置中枢 → 要素集合，全链无替身。"""
    svc = reset_position_service()
    even, odd = _pair_frames()
    iq = synthesize_modes_iq([even, odd], sample_rate=2_000_000)
    result = decode_adsb(iq, 2_000_000)
    assert result["frames"], f"IQ 必须解出帧：{result.get('note')}"

    tr = AircraftTracker()
    for fr in result["frames"]:
        tr.update(fr)
    positioned = [a for a in tr.all() if a.lat is not None]
    assert positioned, "解码帧经跟踪器配对后必须有位置"
    assert positioned[0].lat == pytest.approx(LAT, abs=0.05)

    assert svc.count() >= 1, "配对出的位置必须上报中枢（唯一出口）"
    col = FeatureCollection()
    for rep in svc.all_latest():
        upsert_position_report(rep, track=tuple(svc.track(rep.source)),
                               collection=col)
    assert any(f.feature_id.startswith("pos:") for f in col.all())
    feat = next(f for f in col.all() if f.feature_id.startswith("pos:"))
    assert feat.kind == "adsb"
    assert feat.lat == pytest.approx(LAT, abs=0.05)


def test_me_gb_numpy_uint8_does_not_overflow():
    """numpy.uint8 位数组与 Python 列表必须提取出同一数值（8bit 回绕回归）。

    历史 bug：_me_gb 在 uint8 上左移每步截断，CPR 17bit/高度 12bit/速度
    10bit 静默解错，IQ 路径位置全错但 CRC 仍然过（帧没坏，提取器坏了）。
    """
    import numpy as np
    from mbdsdr_ai.adsb_lite import _me_gb, bytes_to_bits

    even, _ = _pair_frames()
    bits_list = bytes_to_bits(even)
    bits_np = np.asarray(bits_list, dtype=np.uint8)
    for first, last in ((1, 5), (23, 39), (40, 56)):
        assert _me_gb(bits_np, first, last) == _me_gb(bits_list, first, last)
    # 17bit 全宽提取必须超过 8bit（钉住「值真的宽」，防两边一起退化）
    assert _me_gb(bits_np, 23, 39) > 255
