# SPDX-License-Identifier: MIT
"""确定性单测：DSO 目录 —— 加载、按类型过滤、位置查询。"""
import pytest

from mbdsdr_ai.astronomy import dso


@pytest.fixture(scope="module")
def cat():
    return dso.DSOCatalog.builtin()


def test_catalog_loads_builtin_entries():
    cat = dso.DSOCatalog.builtin()
    assert len(cat) >= 100


def test_m31_present():
    cat = dso.DSOCatalog.builtin()
    m31 = cat.get("M31")
    assert m31 is not None
    assert m31.constellation.lower() == "and"
    assert 0 <= m31.ra_deg < 360
    assert -90 <= m31.dec_deg <= 90


def test_filter_by_type():
    cat = dso.DSOCatalog.builtin()
    galaxies = cat.filter_type("galaxy")
    nebulae = cat.filter_type("nebula")
    clusters = cat.filter_type("cluster")
    snr = cat.filter_type("supernova_remnant")
    assert len(galaxies) > 10
    assert len(nebulae) > 0
    assert len(clusters) > 30
    assert len(snr) == 1  # M1
    for g in galaxies:
        assert g.dso_type == "galaxy"


def test_m31_apparent_magnitude_and_size():
    cat = dso.DSOCatalog.builtin()
    m31 = cat.get("M31")
    assert m31.magnitude < 5.0  # M31 肉眼可见
    assert m31.size_arcmin > 100  # 约 3° 大小


def test_near_query():
    cat = dso.DSOCatalog.builtin()
    # M31 at RA 10.68°, Dec 41.27°
    near = cat.near(10.68, 41.27, radius_deg=2.0)
    names = [e.name for e in near]
    assert "M31" in names
    assert "M32" in names or "M110" in names


def test_missing_fields_exposed_as_none(cat):
    """OpenNGC 缺星等/尺寸的条目必须表现为 None（未知），不得是 0/99。"""
    assert cat.get("M22").magnitude is None
    assert cat.get("M35").magnitude is None
    assert cat.get("M40").size_arcmin is None
    assert cat.get("M73").size_arcmin is None
    assert cat.get("M40").name_en == ""
    assert cat.get("M40").zh_name == ""
    assert cat.get("M31").magnitude == pytest.approx(3.44)
    assert cat.get("M31").size_arcmin == pytest.approx(177.83)


def test_m102_maps_to_ngc5866(cat):
    m102 = cat.get("M102")
    assert m102 is not None
    assert dict(m102.cross_ids)["ngc"] == 5866
    assert m102.source_name == "NGC5866"
    assert len([e for e in cat if e.name.startswith("M")]) == 110


def test_unknown_name_returns_none():
    cat = dso.DSOCatalog.builtin()
    assert cat.get("NGC999999") is None
