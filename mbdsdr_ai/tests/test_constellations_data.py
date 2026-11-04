# SPDX-License-Identifier: MIT
"""
tests/test_constellations_data.py — IAU 88 星座 / 中国星官数据测试

覆盖:
  - IAU 88 座数量、缩写/拉丁名/中文名唯一性与完整性
  - 每座连线端点都落到随仓 HYG 真实星（id/hip/坐标对拍）
  - 连线方案来源与“非 IAU 官方定义”标注
  - 中国星官三垣二十八宿 31 区域、星官名与成员映射
  - 未映射 HIP 如实留空并计数（不凭记忆补星）
  - 两套体系互不污染、同时打开时逐层标注
  - 两份数据文件的 meta（来源/许可/SHA）与篡改守卫
  - 旧版 27 座桌面骨架保持兼容
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import pytest

from mbdsdr_ai.skyengine import constellations as c
from mbdsdr_ai.skyengine import star_catalog as sc
from mbdsdr_ai.skyengine import xingguan as x

DATA_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "data"
IAU_PATH = DATA_DIR / "constellations_iau.json"
XG_PATH = DATA_DIR / "xingguan_chinese.json"
XG_NAMES_PATH = DATA_DIR / "xingguan_star_names_zh.json"
REFERENCE = Path(r"D:\mbdsdr\reference\stellarium")

IAU_UNRESOLVED = [55203]
XG_UNRESOLVED = [55203, 78727, 115125]
XIU = {"角宿", "亢宿", "氐宿", "房宿", "心宿", "尾宿", "箕宿",
       "井宿", "鬼宿", "柳宿", "星宿", "张宿", "翼宿", "轸宿",
       "奎宿", "娄宿", "胃宿", "昴宿", "毕宿", "觜宿", "参宿",
       "斗宿", "牛宿", "女宿", "虚宿", "危宿", "室宿", "壁宿"}
YUAN = {"紫微垣", "太微垣", "天市垣"}


@pytest.fixture(scope="module")
def iau():
    return c.load_iau_constellations()


@pytest.fixture(scope="module")
def xg():
    return x.load_xingguan()


@pytest.fixture(scope="module")
def catalog():
    return sc.get_catalog()


@pytest.fixture(scope="module")
def star_index(catalog):
    return {int(catalog.hyg_id[i]): (
        int(catalog.hip[i]), float(catalog.ra_deg[i]),
        float(catalog.dec_deg[i]), float(catalog.vmag[i]))
        for i in range(catalog.count)}


def _assert_star_in_catalog(star, catalog, star_index, *, tol=2e-4):
    row = star_index.get(int(star.hyg_id))
    assert row is not None, f"HYG id {star.hyg_id} 不在随仓星表"
    hip, ra, dec, vmag = row
    assert hip == int(star.hip), f"HIP 与 HYG 行不一致: {star.hip} != {hip}"
    assert abs(ra - float(star.ra_deg)) <= tol
    assert abs(dec - float(star.dec_deg)) <= tol
    assert abs(vmag - float(star.vmag)) <= 1e-3


# ---------------------------------------------------------------------------
# IAU 88 座
# ---------------------------------------------------------------------------
def test_iau_meta_source_and_license(iau):
    meta = iau.meta
    assert meta["system_id"] == c.SYSTEM_IAU
    assert meta["source"].startswith("Stellarium skyculture")
    assert meta["source_url"].startswith("https://")
    assert meta["source_license"] == "CC BY-SA 4.0"
    assert meta["source_license_url"].startswith("https://")
    assert len(meta["source_file_sha256"]) == 64
    assert len(meta["data_sha256"]) == 64
    # 连线不是 IAU 官方定义，必须如实标注
    assert meta["lines_are_iau_official"] is False
    assert "不是 IAU 官方" in meta["lines_note"]
    assert meta["constellation_count"] == 88


def test_iau_count_and_unique_names(iau):
    assert len(iau) == 88
    abbrs = {con.abbr for con in iau}
    latins = {con.latin_name for con in iau}
    zh = {con.zh_name for con in iau}
    assert len(abbrs) == 88
    assert len(latins) == 88
    assert len(zh) == 88
    assert all(len(a) == 3 and a[0].isupper() for a in abbrs)
    assert all(con.latin_name for con in iau)
    assert all(con.zh_name for con in iau)
    # 抽查官方名，避免缩写/名称错位
    assert iau["And"].latin_name == "Andromeda"
    assert iau["Ori"].latin_name == "Orion"
    assert iau["UMa"].latin_name == "Ursa Major"


def test_iau_every_constellation_has_lines(iau):
    for con in iau:
        assert con.lines, f"{con.abbr} 无连线"
        assert con.segment_count > 0
        assert all(len(poly) >= 2 for poly in con.lines)
    total = sum(con.segment_count for con in iau)
    assert total == iau.meta["segment_count"]
    assert iau.meta["original_segment_count"] == total + 1  # 1 段因未映射端点被裁掉


def test_iau_line_endpoints_are_real_catalog_stars(iau, catalog, star_index):
    for con in iau:
        for poly in con.lines:
            for star in poly:
                assert star.in_catalog, f"{con.abbr} 端点不随仓: HIP {star.hip}"
                _assert_star_in_catalog(star, catalog, star_index)
        unique = {s.hyg_id for poly in con.lines for s in poly}
        assert len(unique) == con.star_count
        assert len({s.hip for s in con.stars}) == con.star_count


def test_iau_unresolved_endpoints_are_dropped_and_counted(iau):
    assert iau.meta["unresolved_hips"] == IAU_UNRESOLVED
    assert iau.meta["dropped_segment_count"] == 1
    for con in iau:
        for poly in con.lines:
            assert all(s.hip not in IAU_UNRESOLVED for s in poly)


def test_iau_anchor_is_brightest(iau):
    for con in iau:
        assert con.star_count > 0
        expected = min(con.stars, key=lambda s: (s.vmag, s.hyg_id))
        assert con.anchor_hyg_id == expected.hyg_id
        assert con.anchor().vmag <= con.stars[0].vmag + 1e-6


# ---------------------------------------------------------------------------
# 中国星官：三垣二十八宿
# ---------------------------------------------------------------------------
def test_xingguan_meta_source_and_license(xg):
    meta = xg.meta
    assert meta["system_id"] == c.SYSTEM_CHINESE
    assert meta["source"].startswith("Stellarium skyculture")
    assert meta["source_url"].startswith("https://")
    assert meta["source_license"] == "CC BY-SA 4.0"
    for info in meta["source_files"].values():
        assert len(info["sha256"]) == 64
    assert len(meta["data_sha256"]) == 64
    assert "HIP -> HYG" in meta["mapping"]
    assert meta["region_count"] == 31
    assert meta["yuan_count"] == 3
    assert meta["xiu_count"] == 28


def test_xingguan_regions_are_three_yuan_and_28_xiu(xg):
    assert len(xg) == 31
    yuan = {r.key for r in xg if r.type == "yuan"}
    xiu = {r.key for r in xg if r.type == "xiu"}
    assert yuan == YUAN
    assert xiu == XIU
    assert len(yuan) + len(xiu) == 31
    assert [r.key for r in xg.regions[:3]] == ["紫微垣", "太微垣", "天市垣"]


def test_xingguan_counts_and_unmapped(xg):
    meta = xg.meta
    assert meta["xingguan_count"] == sum(r.xingguan_count for r in xg)
    assert meta["unique_star_hip_count"] == 2910
    assert meta["mapped_count"] == 2907
    assert meta["in_catalog_count"] == 2906
    assert meta["unmapped_count"] == 3
    assert meta["unmapped_hips"] == XG_UNRESOLVED

    all_hips = {s.hip for r in xg for s in r.stars}
    mapped = {s.hip for r in xg for s in r.stars if s.mapped}
    in_cat = {s.hip for r in xg for s in r.stars if s.in_catalog}
    unmapped = sorted(all_hips - mapped)
    assert len(all_hips) == 2910
    assert len(mapped) == 2907
    assert len(in_cat) == 2906
    assert unmapped == XG_UNRESOLVED
    assert sorted({h for r in xg for h in r.unmapped_hips}) == XG_UNRESOLVED
    for r in xg:
        assert r.unmapped_count == len(r.unmapped_hips)
        assert r.star_count == len(r.stars)
        assert r.unique_star_hip_count == len({s.hip for s in r.stars})
        assert r.mapped_count == sum(1 for s in r.stars if s.mapped)
        assert r.in_catalog_count == sum(1 for s in r.stars if s.in_catalog)


def test_xingguan_mapped_members_match_hyg(xg, catalog, star_index):
    mapped_not_catalog = []
    for r in xg:
        for s in r.stars:
            if not s.mapped:
                assert s.hyg_id is None
                assert s.proper == "" and s.bayer == ""
                assert s.ra_deg is None and s.dec_deg is None and s.vmag is None
                continue
            assert s.hyg_id is not None and int(s.hyg_id) > 0
            assert s.ra_deg is not None and s.dec_deg is not None
            assert 0.0 <= s.ra_deg < 360.0
            assert -90.0 <= s.dec_deg <= 90.0
            if s.in_catalog:
                _assert_star_in_catalog(s, catalog, star_index)
            else:
                mapped_not_catalog.append(s)
    # 唯一一颗“映射到 HYG 但比 9 等更深”的星：如实记录，不混入随仓星表
    assert {s.hip for s in mapped_not_catalog} == {115481}
    assert all(s.vmag > 9.0 for s in mapped_not_catalog)


def test_xingguan_line_endpoints_are_real_catalog_stars(xg, catalog, star_index):
    for r in xg:
        for poly in r.lines + tuple(p for x in r.xingguan for p in x.lines):
            assert len(poly) >= 2
            for s in poly:
                assert s.mapped and s.in_catalog
                _assert_star_in_catalog(s, catalog, star_index)


def test_xingguan_known_stars(xg):
    jiao = xg["角宿"]
    spica = [s for s in jiao.stars if s.hip == 65474]
    assert spica and spica[0].proper == "Spica"
    assert "Alp Vir" in spica[0].bayer
    assert spica[0].zh_name == "角宿一"

    ziwei = xg["紫微垣"]
    polaris = [s for s in ziwei.stars if s.hip == 11767]
    assert polaris and polaris[0].proper == "Polaris"
    assert polaris[0].zh_name == "勾陈一"
    assert "北极星" in polaris[0].alt_names


def test_xingguan_regions_have_named_xingguan(xg):
    for r in xg:
        assert r.xingguan, f"{r.key} 无星官"
        assert r.xingguan_count == len(r.xingguan)
        assert all(x.name_zh for x in r.xingguan)
        assert all(x.stars for x in r.xingguan), f"{r.key} 存在空星官"
        # 区域级连线可单独取用；星官级连线允许为空（源数据没有该星官连线）
        assert isinstance(x.xingguan_all_segments(r.key), tuple)


# ---------------------------------------------------------------------------
# 两套体系隔离 / 并列标注
# ---------------------------------------------------------------------------
def test_two_systems_are_separate(iau, xg):
    assert c.SYSTEM_IAU != c.SYSTEM_CHINESE
    assert iau.meta["system_id"] != xg.meta["system_id"]
    # 名称空间不相交：IAU 三字母缩写 vs 中文区域名
    assert set(iau.by_abbr) & set(xg.by_key) == set()
    assert all(re.fullmatch(r"[A-Za-z]{3}", k) for k in iau.by_abbr)
    assert all(any("\u4e00" <= ch <= "\u9fff" for ch in k) for k in xg.by_key)
    # 文化注记不串味
    ic = c.constellation_culture("Ori")
    assert ic and "zh" in ic and "pinyin" not in ic and "group" not in ic
    xc = x.xingguan_culture("角宿")
    assert xc and set(xc) >= {"zh", "en", "pinyin", "group", "type"}
    assert "abbr" not in xc and "latin_name" not in xc
    assert x.xingguan_culture("Ori") is None
    assert c.constellation_culture("角宿") is None


def test_overlay_layers_are_tagged(iau, xg):
    layers = c.overlay_layers()
    assert [l.system_id for l in layers] == [c.SYSTEM_IAU, c.SYSTEM_CHINESE]
    assert all(l.display_name and l.tag for l in layers)
    assert layers[0].tag == "IAU"
    assert layers[1].tag == "中国星官"
    assert layers[0].source != layers[1].source
    assert isinstance(layers[0].items[0], c.IAUConstellation)
    assert isinstance(layers[1].items[0], x.XingguanRegion)
    assert len(layers[0].items) == 88
    assert len(layers[1].items) == 31
    # 顺序可调，但逐层标注不变
    rev = c.overlay_layers((c.SYSTEM_CHINESE, c.SYSTEM_IAU))
    assert [l.system_id for l in rev] == [c.SYSTEM_CHINESE, c.SYSTEM_IAU]
    with pytest.raises(KeyError):
        c.overlay_layers(("not_a_system",))


def test_labels_carry_system_context(iau):
    for con in iau:
        label = c.constellation_label(con.abbr)
        assert con.zh_name in label
        assert c.iau_constellation_label(con.abbr, with_system_tag=True).startswith("[IAU]")
    for key in YUAN | XIU:
        assert x.xingguan_label(key, with_system_tag=True).startswith("[中国星官]")


def test_legacy_27_constellation_skeleton_is_untouched():
    """旧版桌面骨架保持 27 座 / 115 段，避免界面线被本次数据扩展破坏。"""
    assert len(c.CONSTELLATION_LINES) == 27
    assert sum(len(v) for v in c.CONSTELLATION_LINES.values()) == 115
    assert len(c.CONSTELLATION_NAMES) == 27
    assert c.constellation_anchor("Ori") is not None


# ---------------------------------------------------------------------------
# meta 守卫 / SHA / 篡改
# ---------------------------------------------------------------------------
def test_data_sha_matches_payload():
    for path in (IAU_PATH, XG_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert c.payload_sha256(raw) == raw["meta"]["data_sha256"]


def _write_with_meta_change(src: Path, dst: Path, mutate) -> None:
    raw = json.loads(src.read_text(encoding="utf-8"))
    mutate(raw)
    dst.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("missing", ["source", "source_license", "data_sha256"])
def test_iau_missing_meta_rejected(tmp_path, missing):
    dst = tmp_path / "bad_iau.json"

    def mutate(raw):
        raw["meta"].pop(missing, None)

    _write_with_meta_change(IAU_PATH, dst, mutate)
    with pytest.raises(c.ConstellationMetadataError):
        c.load_iau_constellations(str(dst))


def test_iau_tampered_data_rejected(tmp_path):
    dst = tmp_path / "tampered_iau.json"

    def mutate(raw):
        raw["constellations"][0]["zh_name"] += "（篡改）"

    _write_with_meta_change(IAU_PATH, dst, mutate)
    with pytest.raises(c.ConstellationIntegrityError):
        c.load_iau_constellations(str(dst))


@pytest.mark.parametrize("missing", ["mapping", "source_files", "data_sha256"])
def test_xingguan_missing_meta_rejected(tmp_path, missing):
    dst = tmp_path / "bad_xg.json"

    def mutate(raw):
        raw["meta"].pop(missing, None)

    _write_with_meta_change(XG_PATH, dst, mutate)
    with pytest.raises(c.ConstellationMetadataError):
        x.load_xingguan(str(dst))


def test_xingguan_tampered_data_rejected(tmp_path):
    dst = tmp_path / "tampered_xg.json"

    def mutate(raw):
        raw["regions"][0]["star_count"] = 9999

    _write_with_meta_change(XG_PATH, dst, mutate)
    with pytest.raises(c.ConstellationIntegrityError):
        x.load_xingguan(str(dst))


def test_iau_meta_says_lines_not_official():
    raw = json.loads(IAU_PATH.read_text(encoding="utf-8"))
    assert raw["meta"]["lines_are_iau_official"] is False
    assert "不是 IAU 官方" in raw["meta"]["lines_note"]


def test_boundary_status_is_recorded_per_system(iau, xg):
    """两套体系的边界状态分开记录：IAU 边界未交付，星官源明确无边界。"""
    assert iau.meta["boundary_source"] is None
    assert "未纳入" in iau.meta["boundary_note"]
    assert "不能当作星座边界" in iau.meta["boundary_note"]
    assert xg.meta["boundary_source"] is None
    assert "没有具体边界" in xg.meta["boundary_note"]
    assert iau.meta["boundary_note"] != xg.meta["boundary_note"]


@pytest.mark.skipif(not REFERENCE.exists(), reason="本机无 Stellarium 参考数据")
def test_source_files_sha_traceable(iau, xg):
    def sha(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    iau_src = REFERENCE / "skycultures" / "modern_iau" / "index.json"
    assert sha(iau_src) == iau.meta["source_file_sha256"]

    sky = REFERENCE / "skycultures" / "chinese"
    for name, rel in (
            ("index", sky / "index.json"),
            ("star_names", sky / "star_names.zh_CN.fab"),
            ("description", sky / "description.md")):
        assert sha(rel) == xg.meta["source_files"][name]["sha256"]


# ---------------------------------------------------------------------------
# 星官锚点（数据层唯一事实源，渲染方不再自行遍历成员）
# ---------------------------------------------------------------------------
def test_xingguan_anchor_is_brightest_drawable(xg, catalog, star_index):
    """每个区域的锚点 = 区域成员里 V ≤ 9 且在星表内的最亮星。"""
    for r in xg.regions:
        a = x.xingguan_anchor(r)
        assert a is not None, f"{r.key} 应有可用锚点"
        assert x.xingguan_anchor_reason(r) is None
        drawable = [s for s in r.stars
                    if s.in_catalog and s.vmag is not None
                    and s.ra_deg is not None and s.dec_deg is not None]
        assert drawable, f"{r.key} 竟无可绘制成员"
        best = min(drawable, key=lambda s: (s.vmag, s.hip))
        assert a.hip == best.hip
        assert abs(a.vmag - float(best.vmag)) < 1e-9
        assert 0.0 <= a.ra_deg <= 360.0 and -90.0 <= a.dec_deg <= 90.0
        _assert_star_in_catalog(a, catalog, star_index)


def test_xingguan_anchor_accepts_key_and_object(xg):
    """同一事实源：传区域键与传对象必须给出同一个锚点。"""
    for key in ("角宿", "紫微垣", "室宿"):
        by_key = x.xingguan_anchor(key)
        by_obj = x.xingguan_anchor(xg[key])
        assert by_key == by_obj
        assert x.xingguan_anchor_reason(key) == x.xingguan_anchor_reason(xg[key])
    with pytest.raises(KeyError):
        x.xingguan_anchor("不存在的宿")


def test_anchors_exist_for_xingguan_without_lines(xg):
    """13 个源数据无连线的星官也必须有锚点（锚点只看成员，不看连线）。"""
    empty = [(r, g) for r in xg.regions for g in r.xingguan if not g.lines]
    assert len(empty) == 13
    for region, g in empty:
        a = x.xingguan_anchor(g)
        assert a is not None, f"{region.key}/{g.name_zh} 无可用锚点"
        assert x.xingguan_anchor_reason(g) is None
        assert a.hip in {s.hip for s in g.stars}


def test_anchor_never_lands_on_unmapped_star(xg):
    """含未映射星的区域（太微垣/房宿/室宿）锚点必须是真实可绘制星。"""
    for key in ("太微垣", "房宿", "室宿"):
        r = xg[key]
        assert r.unmapped_hips
        a = x.xingguan_anchor(r)
        assert a is not None
        assert a.hip not in r.unmapped_hips
        assert a.hyg_id is not None
        assert a.hip not in XG_UNRESOLVED


def test_anchor_reason_codes_for_degenerate_regions(xg):
    """空成员/全未映射/全不可绘制的区域：返回 None + 稳定原因码。"""
    import dataclasses

    tm = xg["太微垣"]
    unmapped = tuple(s for s in tm.stars if not s.mapped)
    assert len(unmapped) == 1

    shi = xg["室宿"]
    deep = tuple(s for s in shi.stars
                 if s.mapped and not s.in_catalog and s.hip == 115481)
    assert len(deep) == 1
    assert deep[0].vmag is not None and deep[0].vmag > 9.0

    cases = {
        "no_members": dataclasses.replace(tm, key="空区域", stars=()),
        "no_mappable_member": dataclasses.replace(tm, key="全未映射", stars=unmapped),
        "no_drawable_member": dataclasses.replace(shi, key="全深空", stars=deep),
    }
    for reason, region in cases.items():
        assert x.xingguan_anchor(region) is None
        assert x.xingguan_anchor_reason(region) == reason


# ---------------------------------------------------------------------------
# 缓存口径：切 FOV 档位 / 开关图层 / 切体系都不得打穿缓存（唯一例外见末条）
# ---------------------------------------------------------------------------
def test_cache_survives_system_and_layer_switching(iau, xg):
    """加载缓存跨体系切换稳定：重复取层不会重新解析 JSON。"""
    assert c.load_iau_constellations() is iau
    assert x.load_xingguan() is xg

    before = (c.load_iau_constellations.cache_info(),
              x.load_xingguan.cache_info())

    for _ in range(5):
        l_iau = c.constellation_layer(c.SYSTEM_IAU)
        l_xg = c.constellation_layer("chinese_xingguan")
        assert l_iau.items is iau.constellations
        assert l_xg.items is xg.regions
        single = c.overlay_layers((c.SYSTEM_IAU,))
        assert len(single) == 1 and single[0].items is iau.constellations

    after = (c.load_iau_constellations.cache_info(),
             x.load_xingguan.cache_info())
    # 只允许命中数上涨，不允许新增 miss（新增 miss 即重新加载）。
    assert after[0].misses == before[0].misses
    assert after[1].misses == before[1].misses
    assert after[0].hits > before[0].hits
    assert after[1].hits > before[1].hits


def test_overlay_toggle_does_not_reload(iau, xg):
    before = (c.load_iau_constellations.cache_info().misses,
              x.load_xingguan.cache_info().misses)
    layers = c.overlay_layers()                       # 两套全开
    assert [l.system_id for l in layers] == [c.SYSTEM_IAU, c.SYSTEM_CHINESE]
    layers = c.overlay_layers((c.SYSTEM_CHINESE,))    # 只开星官
    assert len(layers) == 1 and layers[0].system_id == c.SYSTEM_CHINESE
    layers = c.overlay_layers((c.SYSTEM_IAU,))        # 再切回只开 IAU
    assert len(layers) == 1 and layers[0].system_id == c.SYSTEM_IAU
    assert (c.load_iau_constellations.cache_info().misses,
            x.load_xingguan.cache_info().misses) == before


def test_all_segments_is_the_only_rebuild_path(xg):
    """`all_segments()` 每次调用都重建（0.26 ms/31 区域），渲染方须预计算。

    这是本层唯一会重复计算的接口；`segments()` 同理。数据本身仍被缓存，
    重建只是把已持有的 tuple 再展平一次。
    """
    a = x.xingguan_all_segments("角宿")
    b = x.xingguan_all_segments("角宿")
    assert a == b
    assert a is not b
    r = xg["角宿"]
    assert r.all_segments() == a
    assert r.segments() == x.xingguan_segments("角宿")


# ---------------------------------------------------------------------------
# 星官成员中文星名（独立映射文件 + 查询 API）
# ---------------------------------------------------------------------------
SAMPLE_30 = {
    71683: "南门二", 11767: "勾陈一", 32349: "天狼", 91262: "织女一",
    27989: "参宿四", 24436: "参宿七", 69673: "大角", 21421: "毕宿五",
    30438: "老人", 677: "壁宿二", 8068: "天大将军二", 1427: "壁宿增十七",
    61932: "库楼七", 109427: "危宿二", 17797: "天园八", 100453: "天津一",
    85927: "尾宿八", 113368: "北落师门", 95947: "辇道增七", 77660: "巴增五",
    115481: "雷电增六", 102098: "天津四", 54061: "北斗一", 11791: "天囷十",
    65474: "角宿一", 49669: "轩辕十四", 24608: "五车二", 37279: "南河三",
    113963: "室宿一", 25336: "参宿五",
}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def test_star_names_coverage_and_meta(xg):
    idx = x.load_xingguan_star_names()
    meta = idx.meta
    assert meta["total_members"] == 2910
    assert meta["with_zh_name"] == 2910
    assert meta["without_zh_name"] == 0
    assert meta["duplicate_occurrences_deduped"] == 1
    assert meta["unmapped_hips"] == XG_UNRESOLVED
    assert meta["no_name_hips"] == []
    # 与源文件 SHA 对拍
    src = REFERENCE / "skycultures" / "chinese" / "star_names.zh_CN.fab"
    assert meta["source_file_sha256"] == _sha256_file(src)
    assert meta["source_files"]["star_names"]["sha256"] == _sha256_file(src)
    # 成员集合与区域数据完全一致（没有多存也没有漏存）
    members = {s.hip for r in xg.regions for s in r.stars}
    assert set(idx.by_hip) == members
    hayg = {s.hyg_id for r in xg.regions for s in r.stars if s.hyg_id}
    assert set(idx.by_hyg_id) == hayg


def test_star_names_thirty_sample(xg):
    """30 颗人工抽样：主名与源文件一致，且落在成员集合里。"""
    idx = x.load_xingguan_star_names()
    fab = _fab_hip_names()
    for hip, expected in SAMPLE_30.items():
        rec = idx.by_hip[hip]
        assert rec.zh_name == expected, f"HIP {hip}"
        assert expected in fab[hip], f"HIP {hip} 的主名不在源文件"
        assert rec.xingguan, f"HIP {hip} 缺少所属星官"
        assert rec.regions, f"HIP {hip} 缺少所属区域"


def _fab_hip_names():
    from mbdsdr_ai.skyengine.build_constellation_data import parse_star_names_fab

    names = {}

    def walk(node):
        for hip, name, _xg in node["stars"]:
            names.setdefault(int(hip), []).append(name)
        for sub in node.get("subs", {}).values():
            walk(sub)

    fab_path = REFERENCE / "skycultures" / "chinese" / "star_names.zh_CN.fab"
    for node in parse_star_names_fab(fab_path).values():
        walk(node)
    return names


def test_star_name_lookup_by_hip_and_hyg(xg):
    idx = x.load_xingguan_star_names()
    by_hip = x.xingguan_star_name(71683)
    assert by_hip.zh_name == "南门二"
    assert by_hip.xingguan == ("南门",)
    assert by_hip.regions == ("角宿",)
    assert x.xingguan_star_name(71456, by="hyg_id").hip == 71683
    assert x.xingguan_star_name(71456, by="hyg_id").zh_name == "南门二"
    # 非成员：None + 原因码，不返回空串
    assert x.xingguan_star_name(7588) is None
    assert x.xingguan_star_name_reason(7588) == "not_member"
    assert x.xingguan_star_name(999999999) is None
    with pytest.raises(ValueError):
        x.xingguan_star_name(71683, by="ra")
    with pytest.raises(TypeError):
        x.xingguan_star_name("71683")
    # 主名只来自中文源，不拿 proper 名兜底
    assert by_hip.label == "南门二"


def test_unmapped_star_names_return_none():
    for hip in XG_UNRESOLVED:
        assert x.xingguan_star_name(hip) is None, hip
        assert x.xingguan_star_name_reason(hip) == "unmapped"
    assert x.xingguan_star_name(115481) is not None  # 映射成功但 >9 等仍可取名


def test_duplicate_member_stored_once():
    """HIP 77660 同属天市右垣与天乳：只一条记录，两个名字都在 alt_names。"""
    idx = x.load_xingguan_star_names()
    rec = idx.by_hip[77660]
    assert rec.zh_name == "巴增五"
    assert rec.xingguan == ("天市右垣", "天乳")
    assert "天乳增二" in rec.alt_names
    # 全表没有重复 HIP 记录
    occurrences = sum(len(r.stars) for r in x.load_xingguan().regions)
    assert len(idx.by_hip) == occurrences - 1


@pytest.mark.parametrize("missing", ["source", "source_license", "data_sha256"])
def test_star_names_missing_meta_rejected(tmp_path, missing):
    dst = tmp_path / "bad_names.json"

    def mutate(raw):
        raw["meta"].pop(missing, None)

    _write_with_meta_change(XG_NAMES_PATH, dst, mutate)
    with pytest.raises(c.ConstellationMetadataError):
        x.load_xingguan_star_names(str(dst))


def test_star_names_tampered_data_rejected(tmp_path):
    dst = tmp_path / "tampered_names.json"

    def mutate(raw):
        raw["stars"]["71683"]["zh_name"] = "南门二（篡改）"

    _write_with_meta_change(XG_NAMES_PATH, dst, mutate)
    with pytest.raises(c.ConstellationIntegrityError):
        x.load_xingguan_star_names(str(dst))


def test_star_names_count_mismatch_rejected(tmp_path):
    dst = tmp_path / "count_names.json"

    def mutate(raw):
        raw["meta"]["with_zh_name"] = 2909

    _write_with_meta_change(XG_NAMES_PATH, dst, mutate)
    with pytest.raises(c.ConstellationIntegrityError):
        x.load_xingguan_star_names(str(dst))
