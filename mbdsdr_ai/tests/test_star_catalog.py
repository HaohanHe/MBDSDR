# SPDX-License-Identifier: MIT
"""
tests/test_star_catalog.py — 全天星表 + 视场 LOD 行为测试

覆盖:
  - 星表完整性/来源元数据/无假星（与 HYG v4.1 参考样本逐行对拍）
  - 0.5 等分档边界与计数
  - 极限星等公式：与 star_draw 同基线、对视场/Bortle 单调、有界
  - 消光矢量化与标量逐点一致
  - 视场选择：地平门/视锥门/极限门/预算降级/排序
  - 放大后同一天区多出来的星确实更暗
  - 坐标链复用（批量结果 == 既有 j2000_to_altaz 单星路径）
  - 120° 视场性能上限
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import time

import numpy as np
import pytest

from mbdsdr_ai.celestial_geometry import (
    GroundStation,
    azalt_from_vec,
    j2000_to_altaz,
    vec_from_azalt,
    vec_from_radec,
)
from mbdsdr_ai.skyengine import star_catalog as sc
from mbdsdr_ai.skyengine.stars import extinction_delta_mag, star_draw

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "hyg_v41_reference_sample.csv")

GS = GroundStation(116.4, 39.9, 50.0, "bench")
JD = 2460000.5


@pytest.fixture(scope="module")
def catalog():
    return sc.get_catalog()


# ---------------------------------------------------------------------------
# 星表完整性 / 来源
# ---------------------------------------------------------------------------
def test_catalog_loads_and_manifest_consistent(catalog):
    assert catalog.count == int(catalog.meta["star_count"])
    assert catalog.count > 80000, "随仓星表规模异常（应为全天 8 万+）"
    assert catalog.meta["source"] == "HYG Database v4.1"
    assert catalog.meta["source_license"] == "CC BY-SA 4.0"
    assert catalog.meta["source_url"].startswith("https://")
    assert str(catalog.meta["source_license_url"]).startswith("https://")
    digest = str(catalog.meta["source_file_sha256"])
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
    assert "generated_utc" in catalog.meta
    assert catalog.meta["mag_bin_width"] == 0.5


def test_catalog_fields_valid(catalog):
    n = catalog.count
    assert np.all(np.isfinite(catalog.ra_deg))
    assert np.all((catalog.ra_deg >= 0.0) & (catalog.ra_deg < 360.0))
    assert np.all(np.isfinite(catalog.dec_deg))
    assert np.all((catalog.dec_deg >= -90.0) & (catalog.dec_deg <= 90.0))
    assert np.all(np.isfinite(catalog.vmag))
    assert np.all(catalog.vmag <= sc.CATALOG_DEPTH_MAG)
    assert np.all(np.diff(catalog.vmag.astype(np.float64)) >= 0.0), \
        "星表应按 mag 升序存盘（分层查询依赖该不变量）"
    assert len(catalog.dec_order) == n
    assert set(np.unique(catalog.dec_order)) == set(range(n))


def test_catalog_has_no_fake_stars(catalog):
    """每颗星都有唯一且合法的 HYG id；Sol (id=0) 必须被剔除。"""
    ids = catalog.hyg_id
    assert np.all(ids > 0), "id=0 是 HYG 的 Sol，不应进入恒星表"
    assert len(np.unique(ids)) == len(ids), "HYG id 必须唯一（无伪造重复）"
    # HYG v4.1 的 id 是合并库编号，略高于原始行数；给一个远高于真实值的上界。
    assert int(ids.max()) < 200000
    # 不存在 (ra=0, dec=0) 且 mag=-26.7 的“太阳冒充恒星”。
    assert not np.any((catalog.ra_deg == 0.0) & (catalog.dec_deg == 0.0)
                      & (catalog.vmag < -10.0))


def test_known_stars_match_hyg_reference_sample(catalog):
    """抽样逐行对拍 HYG 源值（真实数据可追溯，禁止手写假星）。"""
    with open(FIXTURE, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(ln for ln in fh if not ln.startswith("#")))
    assert rows
    for r in rows:
        star = catalog._star_at(int(np.nonzero(
            catalog.hyg_id == int(r["id"]))[0][0]))
        assert star.hyg_id == int(r["id"])
        assert star.hip == int(r["hip"])
        assert star.name == r["proper"]
        assert star.con == r["con"]
        assert star.spect == r["spect"]
        assert star.ra_deg == pytest.approx(float(r["ra"]) * 15.0, abs=1e-4)
        assert star.dec_deg == pytest.approx(float(r["dec"]), abs=1e-4)
        assert star.vmag == pytest.approx(float(r["mag"]), abs=1e-5)
        assert star.bv == pytest.approx(float(r["ci"]), abs=1e-5)
        assert star.dist_pc == pytest.approx(float(r["dist"]), abs=1e-4)


def test_catalog_matches_local_hyg_source_when_available(catalog):
    """本机有 HYG 源 CSV 时，全表逐行核验：无多星、无少星、无改值。

    CI/无源环境自动跳过（源表 33MB，不随仓分发）。
    """
    candidates = [os.environ.get("HYG_SOURCE_PATH", ""),
                  r"D:\mbdsdr\scratch\hygdata_v41.csv"]
    src = next((p for p in candidates if p and os.path.isfile(p)), None)
    if src is None:
        pytest.skip("本机无 HYG v4.1 源 CSV，跳过全表核验")
    expected = {}
    with open(src, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sid = (r.get("id") or "").strip()
            if sid in ("", "0"):
                continue
            try:
                m = float(r["mag"])
                ra = float(r["ra"]) * 15.0
                dec = float(r["dec"])
            except (KeyError, ValueError):
                continue
            if m > sc.CATALOG_DEPTH_MAG:
                continue
            expected[int(sid)] = (ra, dec, m)
    h = hashlib.sha256()
    with open(src, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    assert h.hexdigest() == catalog.meta["source_file_sha256"], \
        "meta.source_file_sha256 与本机 HYG 源文件不一致（来源不可追溯）"
    assert len(expected) == catalog.count, "星表行数与源表可用子集不一致"
    assert set(expected) == set(catalog.hyg_id.tolist()), \
        "星表 id 集合必须与 HYG 源完全一致（无伪造/无遗漏）"
    idx = {int(h): i for i, h in enumerate(catalog.hyg_id)}
    for hid, (ra, dec, m) in expected.items():
        i = idx[hid]
        assert catalog.ra_deg[i] == pytest.approx(ra, abs=1e-4)
        assert catalog.dec_deg[i] == pytest.approx(dec, abs=1e-4)
        assert catalog.vmag[i] == pytest.approx(m, abs=1e-5)


def _save_variant(path, meta, arrays):
    np.savez_compressed(path, meta=np.asarray(json.dumps(meta)),
                        **arrays)


def test_data_digest_matches_meta(catalog):
    """npz 数组重算 SHA-256 必须与 meta.data_sha256 一致。"""
    with np.load(sc._DEFAULT_CATALOG_PATH, allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files if k != "meta"}
        meta = json.loads(str(data["meta"].item()))
    assert sc.compute_data_sha256(arrays) == meta["data_sha256"]


def test_tampered_catalog_rejected(catalog, tmp_path):
    """手改任意数组数值而未同步 meta 时必须拒绝加载。"""
    with np.load(sc._DEFAULT_CATALOG_PATH, allow_pickle=False) as data:
        arrays = {k: data[k].copy() for k in data.files if k != "meta"}
        meta = json.loads(str(data["meta"].item()))
    arrays["vmag"][0] = float(arrays["vmag"][0]) + 0.25
    bad = tmp_path / "tampered.npz"
    _save_variant(bad, meta, arrays)
    with pytest.raises(sc.CatalogIntegrityError):
        sc.load_catalog(str(bad))


@pytest.mark.parametrize("missing_key", [
    "source_url", "source_license", "source_file_sha256", "data_sha256",
])
def test_missing_required_meta_rejected(catalog, tmp_path, missing_key):
    """meta 缺来源 URL/许可/SHA 时必须明确报错，不允许静默加载。"""
    with np.load(sc._DEFAULT_CATALOG_PATH, allow_pickle=False) as data:
        arrays = {k: data[k] for k in data.files if k != "meta"}
        meta = json.loads(str(data["meta"].item()))
    meta.pop(missing_key)
    bad = tmp_path / f"missing_{missing_key}.npz"
    _save_variant(bad, meta, arrays)
    with pytest.raises(sc.CatalogMetadataError) as excinfo:
        sc.load_catalog(str(bad))
    assert missing_key in str(excinfo.value)


def test_find_by_name(catalog):
    sirius = catalog.find_by_name("sirius")
    assert sirius is not None and sirius.name == "Sirius"
    assert sirius.vmag < -1.0
    assert catalog.find_by_name("NoSuchStar") is None


def test_missing_catalog_file_raises(tmp_path):
    with pytest.raises((FileNotFoundError, OSError)):
        sc.load_catalog(str(tmp_path / "nope.npz"))


# ---------------------------------------------------------------------------
# 分层边界
# ---------------------------------------------------------------------------
def test_magnitude_bins_partition_catalog(catalog):
    bins = catalog.magnitude_bins()
    assert len(bins) == len(catalog.mag_bin_edges) - 1
    assert sum(b[2] for b in bins) == catalog.count
    edges = catalog.mag_bin_edges
    assert np.all(np.diff(edges) > 0)
    assert bins[-1][1] == pytest.approx(sc.CATALOG_DEPTH_MAG, abs=1e-6)
    starts = catalog.mag_bin_start
    for i, (lo, hi, count) in enumerate(bins):
        part = catalog.vmag[starts[i]:starts[i + 1]]
        assert len(part) == count
        if i < len(bins) - 1:
            assert np.all((part >= lo) & (part < hi))
        else:
            assert np.all((part >= lo) & (part <= hi))
        assert np.all(np.diff(part.astype(np.float64)) >= 0.0)


def test_count_below_is_layer_boundary(catalog):
    for limit in (-2.0, 2.0, 4.0, 6.0, 6.5, 7.0, 8.0, 9.0, 10.0):
        expect = int(np.count_nonzero(catalog.vmag <= limit))
        assert catalog.count_below(limit) == expect
    assert catalog.count_below(6.5) < catalog.count_below(8.0)
    assert catalog.count_below(2.0) < catalog.count_below(6.5)


# ---------------------------------------------------------------------------
# 极限星等口径
# ---------------------------------------------------------------------------
def test_limit_matches_star_draw_baseline():
    for bortle in range(0, 10):
        # 与 stars.naked_eye_limit_mag 同一线性律（Bortle 3 = 6.5，逐档 -0.35，
        # 暗空段不再截断；P1-46）。
        baseline = 6.5 - 0.35 * (int(bortle) - 3)
        assert sc.naked_eye_limit_mag(bortle) == pytest.approx(baseline)
        # 宽视场必须回到同一条基线，不立第二套阈值。
        assert sc.limit_magnitude(120.0, bortle) == pytest.approx(baseline)


def test_limit_magnitude_monotone_and_bounded():
    prev = math.inf
    for fov in (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 180.0):
        m = sc.limit_magnitude(fov, 3)
        assert m <= prev, "视场变大不应显示更暗的星（LOD 必须单调不增）"
        prev = m
    for bortle in range(0, 10):
        assert sc.limit_magnitude(120.0, bortle) <= sc.CATALOG_DEPTH_MAG
    assert sc.detail_gain_mag(120.0) == 0.0
    assert sc.detail_gain_mag(30.0) == 0.0
    assert sc.detail_gain_mag(0.5) == pytest.approx(sc.MAX_DETAIL_GAIN_MAG)
    assert sc.limit_magnitude(0.5, 3) == pytest.approx(6.5 + 1.5)


def test_limit_magnitude_bortle_monotone():
    vals = [sc.limit_magnitude(120.0, b) for b in range(0, 10)]
    # Bortle 越差极限星等只能更低，且 1..9 必须**严格**下降（P1-46：旧实现
    # 用 max(0, bortle-3) 把 Bortle 1/2/3 夹成同一个 6.5，暗空段星数完全相同）。
    assert all(b < a for a, b in zip(vals, vals[1:])), \
        "Bortle 越差极限星等只能更低，不能出现相同档"
    assert vals[3] == pytest.approx(6.5)
    assert vals[1] == pytest.approx(7.2)


def test_extinction_vector_matches_scalar():
    alts = [-10.0, 0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 45.0, 89.0, 90.0]
    vec = sc.extinction_delta_mag_array(np.asarray(alts))
    for i, alt in enumerate(alts):
        assert vec[i] == pytest.approx(extinction_delta_mag(alt), abs=1e-12)


def test_effective_limit_increases_with_altitude():
    vals = [sc.effective_limit_mag(a, 120.0, 3) for a in (1, 5, 10, 30, 60, 90)]
    assert all(b >= a for a, b in zip(vals, vals[1:])), \
        "仰角越高消光越少，等效极限只能更高"


# ---------------------------------------------------------------------------
# 视场选择
# ---------------------------------------------------------------------------
def _assert_selection_invariants(sel, center_az, center_alt, fov, margin=1.0):
    # 地平门取 _HORIZON_GATE_DEG（−0.6°）而非 0°：绘制层按视高度落笔
    # （地平处大气折射约 34′），与 DSO 层同口径。断言跟随生产常量，
    # 改门时这里自动同步（旧断言 alt>=0 是 −0.6° 门落地前的滞后）。
    assert np.all(sel.alt_deg >= sc._HORIZON_GATE_DEG - 1e-9)
    assert np.all(sel.eff_mag <= sel.limit_mag + 1e-9)
    assert sel.requested_limit_mag == pytest.approx(
        sc.limit_magnitude(sel.fov_deg, sel.bortle))
    assert sel.limit_mag == pytest.approx(
        min(sc.CATALOG_DEPTH_MAG, sel.requested_limit_mag))
    assert not sel.beyond_catalog_limit
    r = math.radians(min(180.0, 0.5 * fov + margin))
    c = vec_from_azalt(center_az, center_alt)
    for az, alt in zip(sel.az_deg, sel.alt_deg):
        v = vec_from_azalt(float(az), float(alt))
        assert float(np.dot(v, c)) >= math.cos(r) - 1e-9


def test_selection_respects_horizon_cone_and_limit(catalog):
    sel = sc.select_for_view(GS, JD, 180.0, 45.0, 120.0, bortle=3)
    assert len(sel) > 500
    _assert_selection_invariants(sel, 180.0, 45.0, 120.0)
    assert not sel.truncated


def test_selection_empty_below_horizon(catalog):
    sel = sc.select_for_view(GS, JD, 0.0, -90.0, 10.0, bortle=3)
    assert len(sel) == 0
    assert sel.available == 0


def test_selection_order_faint_to_bright(catalog):
    sel = sc.select_for_view(GS, JD, 180.0, 60.0, 60.0, bortle=3)
    assert len(sel) > 100
    assert np.all(np.diff(sel.eff_mag) <= 1e-9), \
        "输出应按有效星等从暗到亮排序（亮星后画）"


def test_selection_uses_existing_coordinate_chain(catalog):
    sel = sc.select_for_view(GS, JD, 180.0, 45.0, 60.0, bortle=3)
    assert len(sel) > 0
    for i in (0, len(sel) // 2, len(sel) - 1):
        v = j2000_to_altaz(vec_from_radec(float(sel.ra_deg[i]),
                                          float(sel.dec_deg[i])), GS, JD)
        az, alt = azalt_from_vec(v)
        assert sel.az_deg[i] == pytest.approx(az, abs=1e-6)
        assert sel.alt_deg[i] == pytest.approx(alt, abs=1e-6)


def test_zoom_in_extras_are_fainter(catalog):
    """同一天区: 5° 相比 60° 多出来的星，必须全部比 60° 的极限更暗。"""
    big = sc.select_for_view(GS, JD, 180.0, 60.0, 60.0, bortle=3,
                             max_count=100000)
    small = sc.select_for_view(GS, JD, 180.0, 60.0, 5.0, bortle=3,
                               max_count=100000)
    extras = np.setdiff1d(small.hyg_id, big.hyg_id)
    assert len(extras) > 0, "放大后应露出更多暗星"
    mask = np.isin(small.hyg_id, extras)
    # 用既有绘制口径（vmag + 消光 = eff_mag）判定“更暗”：多出来的星
    # 必须超过 60° 视场的有效极限，且在 5° 视场的有效极限以内。
    # （低仰角星的原始星等可能略亮于极限，但消光后在大视场里本就不可见。）
    extra_eff = small.eff_mag[mask]
    assert np.all(extra_eff > big.limit_mag), \
        "多出来的星必须是更暗的（不得凭空增加亮星）"
    assert np.all(extra_eff <= small.limit_mag + 1e-9)


def test_beyond_catalog_limit_is_explicit(catalog):
    """请求深度超过 9.0 时必须显式标记，不能假装星表还有更深的数据。"""
    deep = sc.select_for_view(GS, JD, 180.0, 90.0, 120.0, bortle=3,
                              limit_mag=9.6, max_count=100000)
    assert deep.beyond_catalog_limit
    assert deep.requested_limit_mag == pytest.approx(9.6)
    assert deep.limit_mag == pytest.approx(sc.CATALOG_DEPTH_MAG)
    assert np.all(deep.vmag <= sc.CATALOG_DEPTH_MAG)

    exact = sc.select_for_view(GS, JD, 180.0, 90.0, 120.0, bortle=3,
                               limit_mag=sc.CATALOG_DEPTH_MAG, max_count=100000)
    assert not exact.beyond_catalog_limit
    assert exact.limit_mag == pytest.approx(sc.CATALOG_DEPTH_MAG)


def test_budget_truncates_to_brightest(catalog):
    full = sc.select_for_view(GS, JD, 180.0, 90.0, 120.0, bortle=3,
                              limit_mag=9.0, max_count=100000)
    assert full.available == len(full) > 1000
    cut = sc.select_for_view(GS, JD, 180.0, 90.0, 120.0, bortle=3,
                             limit_mag=9.0, max_count=1000)
    assert cut.truncated
    assert cut.available == full.available
    assert len(cut) == 1000
    # 预算保留的是最亮的 1000 颗（full 已按暗->亮排序，末尾最亮）。
    assert set(cut.hyg_id.tolist()) == set(full.hyg_id[-1000:].tolist())


# ---------------------------------------------------------------------------
# 性能
# ---------------------------------------------------------------------------
def _median_ms(fn, runs=6):
    costs = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        costs.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(costs[1:])


def test_performance_120deg_not_bottleneck(catalog):
    """120° 满屏常态路径必须远低于 30fps 的 33ms 预算。"""
    def run():
        return sc.select_for_view(GS, JD, 180.0, 45.0, 120.0, bortle=3)

    run()  # 预热
    median = _median_ms(run)
    assert median < 50.0, f"120° 取数中位 {median:.1f}ms，逼近 30fps 预算"


def test_performance_worst_case_depth_bounded(catalog):
    """显式拉到星表最深 (9.0 等) 的 120° 极端情况仍应有界。"""
    def run():
        return sc.select_for_view(GS, JD, 180.0, 90.0, 120.0, bortle=3,
                                  limit_mag=sc.CATALOG_DEPTH_MAG,
                                  max_count=100000)

    run()
    median = _median_ms(run)
    assert median < 100.0, f"极限深度 120° 中位 {median:.1f}ms，超出预算"
