# -*- coding: utf-8 -*-
"""天空视图消光系数 setter：公开入口 + 真实影响（极限星等与可见星数）。

覆盖：
  1. ``set_extinction(k)`` 是唯一公开入口，越界夹到 [0, 0.5] 并回显实际值；
     ``set_extinction_k`` 是同一函数的别名（设置线既有调用名），不是第二套逻辑；
  2. Bortle 3 / FOV 120° 下 k=0.2 与 k=0.5 的可见星数真的不同（反证：若 setter
     不生效，两者必然相等 -> 断言红）；
  3. 等效极限星等按 airmass 逐仰角下降，但 Bortle 决定的天顶极限星等不动；
  4. 高仰角比低仰角受消光影响更小（Δm = k/sin(alt)）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_extinction.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from rf_sky_view import (  # noqa: E402
    RFSkyView,
    EXTINCTION_K_MIN,
    EXTINCTION_K_MAX,
)
from mbdsdr_ai.celestial_geometry import GroundStation  # noqa: E402
from mbdsdr_ai.skyengine import stars as _stars  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402
from mbdsdr_ai.skyengine.star_catalog import (  # noqa: E402
    effective_limit_mag,
    limit_magnitude,
)
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402

FIXED_UNIX = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc).timestamp()
FIXED_JD = _jtime.unix_to_jd(FIXED_UNIX)
GS = GroundStation(116.0, 40.0, 0.0)
FOV_DEG = 120.0
BORTLE = 3


def _fix_time():
    eng = get_time_engine()
    eng.set_unix(FIXED_UNIX)
    eng.pause()


def _new_view() -> RFSkyView:
    _fix_time()
    v = RFSkyView()
    v.resize(700, 700)
    v.set_observer(40.0, 116.0, 0.0)
    v.set_bortle(BORTLE)
    v.view_state.look_at(180.0, 40.0)
    v.view_state.fov_deg = FOV_DEG
    return v


def _count(v: RFSkyView) -> int:
    sel = v._star_selection(GS, FIXED_JD)
    assert sel is not None, "星表不可用，无法测量可见星数"
    return len(sel)


def test_set_extinction_clamps_and_returns_effective_value():
    v = _new_view()
    try:
        assert v.set_extinction(-1.0) == pytest.approx(EXTINCTION_K_MIN)
        assert v.set_extinction(0.9) == pytest.approx(EXTINCTION_K_MAX)
        assert v.set_extinction(0.2) == pytest.approx(0.2)
        assert v._extinction_k == pytest.approx(0.2)
        # 设置线既有调用名走同一条函数对象，不另立逻辑
        assert RFSkyView.set_extinction_k is RFSkyView.set_extinction
        assert v.set_extinction_k(0.5) == pytest.approx(0.5)
        assert v._extinction_k == pytest.approx(0.5)
        for bad in ("abc", None, float("nan"), float("inf")):
            with pytest.raises(ValueError):
                v.set_extinction(bad)
        # 非法入参不动既有状态
        assert v._extinction_k == pytest.approx(0.5)
    finally:
        v.close()


def test_star_count_really_changes_between_k_02_and_k_05():
    """反证：若 set_extinction 不生效（k 恒定），两次计数必然相等 -> 此处必红。"""
    v = _new_view()
    try:
        v.set_extinction(0.2)
        n02 = _count(v)
        v.set_extinction(0.5)
        n05 = _count(v)
        # 实测 Bortle 3 / FOV 120° / 北京 / 2026-09-27 12:00 UTC：约 814 vs 457
        assert n02 > n05, f"k=0.2 应比 k=0.5 见更多星，实测 {n02} vs {n05}"
        assert n02 - n05 >= 50, f"星数差 {n02 - n05} 太小，setter 可能没进选择链"
        # 对称性：调回 0.2 应恢复同一星数（无残留状态）
        v.set_extinction(0.2)
        assert _count(v) == n02
        # k=0（无消光）必然最多
        v.set_extinction(0.0)
        assert _count(v) >= n02
    finally:
        v.close()


def test_effective_limit_mag_follows_airmass_but_bortle_zenith_is_fixed():
    zenith = limit_magnitude(FOV_DEG, BORTLE)
    e02 = effective_limit_mag(20.0, FOV_DEG, BORTLE, extinction_k=0.2)
    e05 = effective_limit_mag(20.0, FOV_DEG, BORTLE, extinction_k=0.5)
    d02 = float(_stars.extinction_delta_mag(20.0, k=0.2))
    d05 = float(_stars.extinction_delta_mag(20.0, k=0.5))
    assert zenith - e02 == pytest.approx(d02, abs=1e-6)
    assert zenith - e05 == pytest.approx(d05, abs=1e-6)
    assert e02 - e05 >= 0.5, "k 加大后等效极限星等必须下降"
    # Bortle 只定天顶极限星等：k 不改变它
    assert limit_magnitude(FOV_DEG, BORTLE) == pytest.approx(zenith)
    # 高仰角受消光影响更小（airmass）
    assert d02 < d05
    assert effective_limit_mag(80.0, FOV_DEG, BORTLE, extinction_k=0.5) \
        > effective_limit_mag(20.0, FOV_DEG, BORTLE, extinction_k=0.5)


def test_wide_field_160_count_also_responds():
    """160° 全天模式同样随 k 变化（不只窄视场）。"""
    v = _new_view()
    try:
        v.view_state.fov_deg = 160.0
        v.view_state.look_at(180.0, 50.0)
        v.set_extinction(0.2)
        n02 = _count(v)
        v.set_extinction(0.5)
        n05 = _count(v)
        assert n02 > n05, f"160° 下 k 未生效：{n02} vs {n05}"
        assert n02 - n05 >= 200, f"160° 星数差 {n02 - n05} 偏小"
    finally:
        v.close()
