# SPDX-License-Identifier: MIT
"""天空图 TLE 目录必须向执行侧投影，刷新后立刻换新根数。

修前：``rf_sky_view.SatelliteTracker._catalog`` 只在 ``__init__`` 读一次，
「更新 TLE」只写执行侧 ``mbdsdr_ai.sat_tracker._catalog``。结果刷新成功后
天空图仍画旧根数（现场 47 天）。

断言口径：两侧同一颗卫星的 TLE 历元列必须一致；任何一侧「自己留一份」
都会让下面第三条测试（回退到旧值）报红。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

NEW_EPOCH = "26315.50000000"
OLD_L1 = "1 25338U 98030A   26268.86268050  .00000085  00000+0  52080-4 0  9991"
OLD_L2 = "2 25338  98.5049 286.7672 0011219  56.3023 303.9227 14.27170971475651"
NEW_L1 = "1 25338U 98030A   26315.50000000  .00000085  00000+0  52080-4 0  9991"
NEW_L2 = "2 25338  98.5049 286.7672 0011219  56.3023 303.9227 14.27170971475651"
SAT = "NOAA 15"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _epoch(rows, name=SAT):
    """历元列：兼容天空图目录（line1）与执行侧目录（tle=[l1,l2]）。"""
    for r in rows or []:
        if r.get("name") != name:
            continue
        line1 = r.get("line1")
        if not line1:
            tle = r.get("tle") or []
            line1 = tle[0] if len(tle) >= 1 else ""
        return str(line1)[18:32]
    return None


def _assert_same_epoch(sky, owner):
    assert _epoch(sky._catalog) == _epoch(owner.list_satellites()), (
        f"天空图目录与执行侧分叉：sky={_epoch(sky._catalog)} "
        f"owner={_epoch(owner.list_satellites())}")


@pytest.fixture
def pair(qapp, monkeypatch, isolated_profile):
    """执行侧 owner + 天空图（已绑定投影），离线、无观测站、不联网。"""
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")
    from mbdsdr_ai.sat_tracker import SatelliteTracker as LoopTracker
    from desktop.rf_sky_view import RFSkyView, SatelliteTracker as SkyTracker
    from desktop.main_window import MainWindow

    owner = LoopTracker(None)
    sky = SkyTracker(RFSkyView(), None, None)
    holder = SimpleNamespace(_loop_sat_tracker=owner)
    sky.set_catalog_source(lambda: MainWindow._sky_catalog_source(holder))
    return owner, sky


def test_sky_catalog_follows_owner_after_refresh(pair):
    """更新 TLE 到已知历元 -> 天空图目录历元同步变化（修前红）。"""
    owner, sky = pair
    before = _epoch(sky._catalog)
    assert before and before != NEW_EPOCH
    _assert_same_epoch(sky, owner)   # 绑定即投影，初始两侧一致
    assert owner.load_tle([SAT, NEW_L1, NEW_L2])
    assert _epoch(owner.list_satellites()) == NEW_EPOCH
    sky.refresh()
    assert _epoch(sky._catalog) == NEW_EPOCH
    _assert_same_epoch(sky, owner)


def test_sky_rollback_to_old_lines_must_be_caught(pair):
    """把天空图目录改回旧值：先证明「会红」，再证明 refresh 会重新投影。

    这条是反向锁：若实现退回「显示层自持副本、refresh 不向 owner 取目录」，
    末尾的断言就会失败。
    """
    owner, sky = pair
    assert owner.load_tle([SAT, NEW_L1, NEW_L2])
    sky.refresh()
    assert _epoch(sky._catalog) == NEW_EPOCH

    reverted = []
    for row in sky._catalog:
        row = dict(row)
        if row.get("name") == SAT:
            row["line1"], row["line2"] = OLD_L1, OLD_L2
        reverted.append(row)
    sky._catalog = reverted          # 人为退回旧根数（模拟两侧对调）
    sky._catalog_sig = None

    with pytest.raises(AssertionError):
        _assert_same_epoch(sky, owner)

    sky.refresh()                    # 必须重新向 owner 投影
    _assert_same_epoch(sky, owner)


def test_main_window_refresh_slot_updates_sky(qapp, monkeypatch, isolated_profile):
    """端到端：刷新成功回调后，MainWindow 里的天空图目录立即换新。"""
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")
    from desktop.main_window import MainWindow
    from desktop.rf_sky_view import SatelliteTracker as SkyTracker

    w = MainWindow()
    try:
        if getattr(w, "sat_tracker", None) is None:
            w.sat_tracker = SkyTracker(w.sky_view, None, None)
        w._bind_sky_catalog()
        if w._loop_sat_tracker is None:
            w._init_loop_sat_catalog()
        assert w._loop_sat_tracker is not None
        assert w._loop_sat_tracker.load_tle([SAT, NEW_L1, NEW_L2])
        w._on_tle_update_result((1, None))
        assert _epoch(w.sat_tracker._catalog) == NEW_EPOCH, (
            "刷新成功回调后天空图仍在画旧根数："
            f"{_epoch(w.sat_tracker._catalog)}")
    finally:
        w.close()
