# SPDX-License-Identifier: MIT
"""``StarCatalog.brightest`` / ``brightest_stars`` 的只读快照测试。

覆盖：前 N 颗的排序与字段、limit<=0、limit 超总数、max_mag 过滤、
模块级函数与星表对象逐一对应、调用不修改星表内部数组。
"""
from __future__ import annotations

import hashlib

import pytest

from mbdsdr_ai.skyengine import star_catalog as sc

_FIELDS = ("name", "ra_deg", "dec_deg", "mag", "hip", "hyg_id")


@pytest.fixture(scope="module")
def catalog():
    return sc.get_catalog()


def _vmag_sha1(catalog):
    return hashlib.sha1(catalog.vmag.tobytes()).hexdigest()


# ---------------------------------------------------------------------------
# 1. 前 10 颗：长度、单调、字段齐全
# ---------------------------------------------------------------------------
def test_brightest_ten_sorted_and_complete(catalog):
    stars = catalog.brightest(10)

    assert len(stars) == 10
    mags = [s.vmag for s in stars]
    assert mags == sorted(mags), "前 10 颗应按 vmag 升序（单调不减）"
    for star in stars:
        assert isinstance(star.name, str)
        assert isinstance(star.ra_deg, float)
        assert isinstance(star.dec_deg, float)
        assert isinstance(star.vmag, float)
        assert isinstance(star.hip, int)


# ---------------------------------------------------------------------------
# 2. limit <= 0 返回空表（不抛异常）
# ---------------------------------------------------------------------------
def test_limit_zero_returns_empty(catalog):
    assert catalog.brightest(0) == []
    assert catalog.brightest(-5) == []


# ---------------------------------------------------------------------------
# 3. limit 超过总数时返回全部
# ---------------------------------------------------------------------------
def test_limit_beyond_total_returns_all(catalog):
    stars = catalog.brightest(catalog.count + 5)

    assert len(stars) == catalog.count
    assert stars[-1].vmag == pytest.approx(float(catalog.vmag[-1]))


# ---------------------------------------------------------------------------
# 4. max_mag 过滤：星等全部达标，条数 == min(limit, count_below)
# ---------------------------------------------------------------------------
def test_max_mag_filters_by_count_below(catalog):
    max_mag = 3.0
    limit = 10
    expected = min(limit, catalog.count_below(max_mag))

    stars = catalog.brightest(limit, max_mag=max_mag)

    assert len(stars) == expected
    assert all(s.vmag <= max_mag for s in stars)
    assert expected > 0, "3.0 等以内应有星（星表覆盖到 9.0）"


# ---------------------------------------------------------------------------
# 5. 模块级 brightest_stars 与 catalog.brightest 前 5 颗逐一对应
# ---------------------------------------------------------------------------
def test_module_brightest_stars_matches_catalog(catalog):
    from_catalog = catalog.brightest(5)
    from_module = sc.brightest_stars(5)

    assert len(from_module) == 5
    for star, item in zip(from_catalog, from_module):
        assert set(item) == set(_FIELDS)
        assert isinstance(item["name"], str)
        assert item["ra_deg"] == pytest.approx(star.ra_deg)
        assert item["dec_deg"] == pytest.approx(star.dec_deg)
        assert item["mag"] == pytest.approx(star.vmag)
        assert item["hip"] == star.hip
        assert isinstance(item["hip"], int)
        assert item["hyg_id"] == star.hyg_id
        assert isinstance(item["hyg_id"], int)


# ---------------------------------------------------------------------------
# 6. 只读：调用前后星表 vmag 数组的 sha1 不变
# ---------------------------------------------------------------------------
def test_brightest_does_not_mutate_catalog(catalog):
    before = _vmag_sha1(catalog)

    catalog.brightest(10)
    catalog.brightest(10, max_mag=4.0)
    sc.brightest_stars(10)

    assert _vmag_sha1(catalog) == before
