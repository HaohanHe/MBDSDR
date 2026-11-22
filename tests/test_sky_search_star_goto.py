# SPDX-License-Identifier: MIT
"""搜索定位恒星时与星图同一自行口径（J2000 -> 当前时刻）。"""
from datetime import datetime, timezone

import numpy as np
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication

from rf_sky_view import RFSkyView
from mbdsdr_ai.celestial_geometry import (
    azalt_from_vec,
    j2000_to_altaz,
    vec_from_radec,
)
from mbdsdr_ai.new_spacetime import get_time_engine
from mbdsdr_ai.skyengine import proper_motion
from mbdsdr_ai.skyengine.star_catalog import get_catalog

# 固定时刻下（lat/lon=0）天狼星在地平线下、织女星仅 2.9°，
# 改用同一批亮星里清晰可见的 Altair（视高度约 21.9°）。
STAR_NAME = "Altair"


class RecordingSkyView(RFSkyView):
    """记录 goto_animated 的最终落点，跳过动画定时器。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.goto_calls = []

    def goto_animated(self, az_deg, alt_deg, *args, **kwargs):
        self.goto_calls.append((float(az_deg), float(alt_deg)))


@pytest.fixture
def view(isolated_profile):
    app = QApplication.instance() or QApplication([])
    engine = get_time_engine()
    engine.set_unix(datetime(2026, 9, 27, tzinfo=timezone.utc).timestamp())
    engine.pause()
    v = RecordingSkyView()
    v.resize(1000, 800)
    # 合成观测点，不读/写真实站址。
    v.set_observer(0.0, 0.0, 0.0)
    v.set_site_note("隔离测试示例站点（非真实位置）")
    v.show()
    v.activateWindow()
    v.reveal_search()
    app.processEvents()
    yield v
    v.close()
    v.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def _locate_row(view, result):
    """用真实点击处理函数定位给定结果，返回实际收到的 (az, alt)。"""
    view._search_results = [result]
    view._search_popup.clear()
    view._search_popup.addItem(view._QListWidgetItem(result["label"]))
    view._search_popup.setCurrentRow(0)
    view._target_header.hide()
    view._search_popup.show()
    view.goto_calls.clear()
    view._on_search_picked(None)
    assert len(view.goto_calls) == 1, view._camera_notice
    return view.goto_calls[0]


def _expected_altaz(view, ra, dec):
    gs = view._ground_station()
    jd = view._current_jd()
    return azalt_from_vec(j2000_to_altaz(vec_from_radec(ra, dec), gs, jd))


def _az_diff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _catalog_row():
    star = get_catalog().find_by_name(STAR_NAME)
    assert star is not None and star.hyg_id is not None
    return {
        "kind": "star", "kind_label": "恒星", "label": STAR_NAME,
        "sub": f"Star · {star.con}", "name": star.name,
        "hyg_id": star.hyg_id, "hip": star.hip,
        "ra": star.ra_deg, "dec": star.dec_deg,
        "ra_deg": star.ra_deg, "dec_deg": star.dec_deg,
        "vmag": star.vmag, "bv": star.bv, "dist_pc": star.dist_pc,
        "con": star.con, "spect": star.spect,
    }


def test_star_search_goto_uses_proper_motion(view):
    row = _catalog_row()
    az, alt = _locate_row(view, row)
    assert alt > 10.0, f"{STAR_NAME} 应在地平线上方，实际 {alt}"

    jd = view._current_jd()
    table = proper_motion.load_pm_table()
    pmra, pmdec = proper_motion.pm_for_hyg_ids(
        np.asarray([row["hyg_id"]]), table)
    ra_pm, dec_pm = proper_motion.propagate_radec_many(
        row["ra"], row["dec"], pmra[0], pmdec[0], jd)
    exp_az, exp_alt = _expected_altaz(view, ra_pm, dec_pm)
    assert _az_diff(az, exp_az) < 1e-6
    assert abs(alt - exp_alt) < 1e-6

    static_az, static_alt = _expected_altaz(view, row["ra"], row["dec"])
    assert (_az_diff(az, static_az) > 0.001
            or abs(alt - static_alt) > 0.001), "定位没有用上自行"


def test_missing_pm_table_falls_back_to_static(view, monkeypatch):
    row = _catalog_row()
    monkeypatch.setattr(proper_motion, "load_pm_table", lambda path=None: None)
    az, alt = _locate_row(view, row)
    exp_az, exp_alt = _expected_altaz(view, row["ra"], row["dec"])
    assert _az_diff(az, exp_az) < 1e-9
    assert abs(alt - exp_alt) < 1e-9


def test_result_without_hyg_id_falls_back_to_static(view):
    row = _catalog_row()
    row.pop("hyg_id")
    az, alt = _locate_row(view, row)
    exp_az, exp_alt = _expected_altaz(view, row["ra"], row["dec"])
    assert _az_diff(az, exp_az) < 1e-9
    assert abs(alt - exp_alt) < 1e-9


def test_bright_star_hyg_map_matches_catalog():
    """兜底亮星表的 hyg_id 对照必须覆盖 54 颗、不重复、坐标与 HYG 一致。"""
    from mbdsdr_ai.skyengine.stars import BRIGHT_STAR_HYG_IDS, BRIGHT_STARS

    assert set(BRIGHT_STAR_HYG_IDS) == {s.name for s in BRIGHT_STARS}
    assert len(set(BRIGHT_STAR_HYG_IDS.values())) == len(BRIGHT_STAR_HYG_IDS)

    catalog = get_catalog()
    ids = np.asarray(list(BRIGHT_STAR_HYG_IDS.values()))
    table = proper_motion.load_pm_table()
    pmra, pmdec = proper_motion.pm_for_hyg_ids(ids, table)
    assert np.isfinite(pmra).all() and np.isfinite(pmdec).all()

    for st in BRIGHT_STARS:
        hyg_id = BRIGHT_STAR_HYG_IDS[st.name]
        if st.name == "Regor":
            # HYG v4.1 没有这颗星的 proper 名，只能按坐标核对（1″ 内）。
            continue
        hit = catalog.search_by_name(st.name, limit=1)
        assert hit and hit[0].hyg_id == hyg_id, st.name
        sep = _angular_sep_deg(st.ra_deg, st.dec_deg,
                               hit[0].ra_deg, hit[0].dec_deg)
        assert sep * 3600.0 < 1.0, (st.name, sep * 3600.0)


def test_bright_star_fallback_search_carries_hyg_id(view, monkeypatch):
    """星表不可用时，兜底搜索结果仍带 hyg_id，选中定位用得上自行。"""
    import mbdsdr_ai.skyengine.star_catalog as star_catalog

    def _catalog_down():
        raise RuntimeError("catalog unavailable (test)")

    monkeypatch.setattr(star_catalog, "get_catalog", _catalog_down)
    view._on_search_text("altair")
    rows = [r for r in view._search_results
            if r["kind"] == "star" and r["name"] == STAR_NAME]
    assert rows, "兜底亮星表应能搜到 Altair"
    row = rows[0]
    assert row["hyg_id"] == 97338

    az, alt = _locate_row(view, row)
    table = proper_motion.load_pm_table()
    pmra, pmdec = proper_motion.pm_for_hyg_ids(
        np.asarray([row["hyg_id"]]), table)
    ra_pm, dec_pm = proper_motion.propagate_radec_many(
        row["ra"], row["dec"], pmra[0], pmdec[0], view._current_jd())
    exp_az, exp_alt = _expected_altaz(view, ra_pm, dec_pm)
    assert _az_diff(az, exp_az) < 1e-6
    assert abs(alt - exp_alt) < 1e-6

    static_az, static_alt = _expected_altaz(view, row["ra"], row["dec"])
    assert (_az_diff(az, static_az) > 0.001
            or abs(alt - static_alt) > 0.001), "兜底定位没有用上自行"


def _angular_sep_deg(ra1, dec1, ra2, dec2):
    """两点球面角距（度），只用于 <=1″ 的一致性核对。"""
    r1, d1, r2, d2 = map(np.radians, (ra1, dec1, ra2, dec2))
    cos_sep = (np.sin(d1) * np.sin(d2)
               + np.cos(d1) * np.cos(d2) * np.cos(r1 - r2))
    return float(np.degrees(np.arccos(np.clip(cos_sep, -1.0, 1.0))))
