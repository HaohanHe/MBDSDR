# SPDX-License-Identifier: MIT
"""
tests/test_dso_catalog.py — 深空天体事实目录测试

覆盖:
  - 117 条 = Messier 1–110 + 7 个常用亮 NGC；编号无重复、无缺号
  - 每条坐标/星等/角径/类型与 Stellarium 源目录对拍（30 颗抽样）
  - 英文名/中文名与 names.dat / zh_CN.po 对拍；缺失留空并计数
  - 不得拿英文 proper name 冒充中文名
  - 星座归属、交叉编号、源目录行号可追溯
  - 重复编号、坐标越界、星等缺失、meta 缺失、SHA 篡改、计数不符均抛错
  - API 字段与 desktop `_draw_dso` 期望的字段对齐
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from mbdsdr_ai.astronomy import dso

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DSO_PATH = DATA_DIR / "dso_catalog.json"
REFERENCE = Path(r"D:\mbdsdr\reference\stellarium")
CATALOG_TXT = REFERENCE / "nebulae" / "default" / "catalog.txt"
NAMES_DAT = REFERENCE / "nebulae" / "default" / "names.dat"
ZH_PO = REFERENCE / "po" / "stellarium-sky" / "zh_CN.po"
SPANS_DAT = REFERENCE / "data" / "constellations_spans.dat"

EXTRA_NGC = (253, 869, 884, 2070, 5139, 7000, 7293)
CJK_RE = re.compile(r"[\u3400-\u9fff]")

SAMPLE_30 = (
    "M1", "M8", "M13", "M16", "M20", "M27", "M31", "M33", "M40", "M42",
    "M45", "M51", "M57", "M63", "M64", "M74", "M76", "M81", "M83", "M87",
    "M94", "M97", "M101", "M102", "M104", "M110",
    "NGC253", "NGC869", "NGC2070", "NGC7293",
)

# 与源目录对拍时写死的容差（数据由源文件直接生成，不允许漂移）
RA_DEC_TOL_DEG = 1e-6
MAG_TOL = 1e-6
SIZE_TOL_ARCMIN = 1e-6

SAMPLE_CON = {
    "M1": "Tau", "M8": "Sgr", "M13": "Her", "M16": "Ser", "M20": "Sgr",
    "M27": "Vul", "M31": "And", "M33": "Tri", "M40": "UMa", "M42": "Ori",
    "M45": "Tau", "M51": "CVn", "M57": "Lyr", "M63": "CVn", "M64": "Com",
    "M74": "Psc", "M76": "Per", "M81": "UMa", "M83": "Hya", "M87": "Vir",
    "M94": "CVn", "M97": "UMa", "M101": "UMa", "M102": "Dra",
    "M104": "Vir", "M110": "And", "NGC253": "Scl", "NGC869": "Per",
    "NGC2070": "Dor", "NGC7293": "Aqr",
}

SAMPLE_NAMES = {
    "M31": ("Andromeda Galaxy", "仙女座星系"),
    "M42": ("Great Orion Nebula", "猎户座大星云"),
    "M45": ("Pleiades", "昴星团"),
    "M104": ("Sombrero Galaxy", "草帽星系"),
    "M40": ("Winnecke 4", "温内克4"),
    "M102": ("Spindle Galaxy", "纺锤星系"),
    "NGC5139": ("ω Cen Cluster", "半人马座欧米伽星团"),
}


@pytest.fixture(scope="module")
def cat():
    return dso.load_dso_catalog()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _source_rows():
    """独立解析源目录，作为测试真值（不复用生成脚本）。"""
    mrows, ngcrows = {}, defaultdict(list)
    with open(CATALOG_TXT, encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            row = {"row": lineno, "parts": parts}
            if int(parts[18] or 0):
                mrows[int(parts[18])] = row
            if int(parts[16] or 0):
                ngcrows[int(parts[16])].append(row)
    return mrows, ngcrows


def _source_names():
    names = defaultdict(list)
    with open(NAMES_DAT, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            prefix = line[0:5].strip()
            ident = line[5:20].strip()
            match = re.search(r'_\(\"(.*?)\"\)', line)
            if prefix and ident and match:
                name = match.group(1).strip()
                if name and name not in names[(prefix, ident)]:
                    names[(prefix, ident)].append(name)
    return names


def _source_zh():
    text = ZH_PO.read_text(encoding="utf-8", errors="replace")
    zh = {}
    for block in re.split(r"\n\n", text):
        if "#, fuzzy" in block or "#~" in block:
            continue
        ids = re.findall(r'^msgid "((?:[^"\\]|\\.)*)"', block, re.M)
        strs = re.findall(r'^msgstr "((?:[^"\\]|\\.)*)"', block, re.M)
        if not ids or not strs or not ids[0] or not strs[0]:
            continue
        chinese = strs[0].strip()
        if CJK_RE.search(chinese) and chinese != ids[0]:
            zh[ids[0]] = chinese
    return zh


def _row_for(name: str, mrows, ngcrows):
    if name.startswith("M"):
        return mrows[int(name[1:])]
    return ngcrows[int(name[3:])][0]


def _names_for(row, names):
    parts = row["parts"]
    keys = []
    if int(parts[18] or 0):
        keys.append(("M", str(int(parts[18]))))
    if int(parts[16] or 0):
        keys.append(("NGC", str(int(parts[16]))))
    if int(parts[17] or 0):
        keys.append(("IC", str(int(parts[17]))))
    for key in keys:
        if key in names:
            return names[key]
    return []


def _rehash(payload: dict) -> None:
    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    clone["meta"].pop("data_sha256", None)
    blob = json.dumps(clone, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    payload["meta"]["data_sha256"] = hashlib.sha256(blob).hexdigest()


def _variant(tmp_path: Path, payload: dict, name: str = "dso.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 覆盖与来源
# ---------------------------------------------------------------------------
def test_catalog_counts_and_exact_designations(cat):
    counts = cat.meta["counts"]
    assert len(cat) == 117
    assert counts["total"] == 117
    assert counts["messier"] == 110
    assert counts["extra_ngc"] == len(EXTRA_NGC)

    expected = {f"M{i}" for i in range(1, 111)}
    expected |= {f"NGC{n}" for n in EXTRA_NGC}
    assert {e.name for e in cat} == expected
    assert len({e.name for e in cat}) == 117
    assert len({e.source_row for e in cat}) == 117


def test_meta_source_license_and_source_sha(cat):
    meta = cat.meta
    assert meta["schema"] == "mbdsdr.dso_catalog.v1"
    assert meta["source"].startswith("Stellarium")
    assert meta["source_url"].startswith("https://")
    assert meta["source_license"] == "GPL-2.0-or-later"
    assert meta["source_license_url"].startswith("https://")
    assert len(meta["data_sha256"]) == 64
    assert set(meta["source_files"]) == {
        "catalog", "names", "zh_translations", "constellation_spans"}
    for key, info in meta["source_files"].items():
        assert len(info["path"]) > 0
        assert len(info["sha256"]) == 64
        if REFERENCE.exists():
            assert _sha256_file(REFERENCE / info["path"]) == info["sha256"], key


def test_name_coverage_and_no_english_as_chinese(cat):
    counts = cat.meta["counts"]
    assert counts["with_name_en"] == 74
    assert counts["without_name_en"] == 43
    assert counts["with_zh_name"] == 74
    assert counts["without_zh_name"] == 43
    assert counts["no_name_designations"] == counts["no_zh_name_designations"]
    assert len(counts["no_name_designations"]) == 43

    for e in cat:
        if e.zh_name:
            assert CJK_RE.search(e.zh_name), f"{e.name} 中文名不含汉字: {e.zh_name}"
            assert e.zh_name != e.name_en, f"{e.name} 中文名与英文名相同"
        else:
            assert e.name_en == "", f"{e.name} 无中文名却有英文名，口径不一致"


def test_unnamed_objects_keep_designation_label(cat):
    for name in ("M2", "M3", "M9", "M10", "M110"):
        e = cat.get(name)
        assert e is not None
        assert e.name_en == ""
        assert e.zh_name == ""
        assert e.display_name == name
        assert e.label == name


# ---------------------------------------------------------------------------
# 30 颗抽样对拍
# ---------------------------------------------------------------------------
def test_thirty_sample_against_source_catalog(cat):
    mrows, ngcrows = _source_rows()
    for name in SAMPLE_30:
        entry = cat.get(name)
        assert entry is not None, name
        row = _row_for(name, mrows, ngcrows)
        parts = row["parts"]
        assert abs(entry.ra_deg - float(parts[1])) <= RA_DEC_TOL_DEG, name
        assert abs(entry.dec_deg - float(parts[2])) <= RA_DEC_TOL_DEG, name
        assert abs(entry.magnitude - float(parts[4])) <= MAG_TOL, name
        assert abs(entry.size_arcmin - float(parts[7])) <= SIZE_TOL_ARCMIN, name
        assert entry.catalog_type == parts[5].strip(), name
        assert entry.source_row == row["row"], name
        assert entry.constellation == SAMPLE_CON[name], name


def test_thirty_sample_names_against_source(cat):
    mrows, ngcrows = _source_rows()
    names = _source_names()
    zh = _source_zh()
    for name in SAMPLE_30:
        entry = cat.get(name)
        row = _row_for(name, mrows, ngcrows)
        english = _names_for(row, names)
        translated = [zh[n] for n in english if n in zh]
        assert entry.name_en == (english[0] if english else ""), name
        assert entry.zh_name == (translated[0] if translated else ""), name


def test_sample_names_are_sourced(cat):
    for name, (en, zh) in SAMPLE_NAMES.items():
        entry = cat.get(name)
        assert entry is not None
        assert entry.name_en == en, name
        assert entry.zh_name == zh, name
    assert "Omega Centauri" in cat.get("NGC5139").aliases_en


# ---------------------------------------------------------------------------
# 类型 / M40 / M102
# ---------------------------------------------------------------------------
def test_type_mapping_and_special_objects(cat):
    for e in cat:
        assert cat.meta["type_mapping"][e.catalog_type] == e.dso_type

    counts = cat.meta["counts"]
    assert counts["render_type_counts"] == {
        "cluster": 59, "cluster_nebula": 5, "double_star": 1,
        "galaxy": 41, "nebula": 10, "supernova_remnant": 1,
    }
    assert {e.name for e in cat.filter_type("cluster_nebula")} == {
        "M16", "M17", "M20", "M45", "NGC2070"}
    assert {e.name for e in cat.filter_type("double_star")} == {"M40"}

    m40 = cat.get("M40")
    assert m40.catalog_type == "*"
    assert m40.dso_type == "double_star"
    assert m40.size_arcmin == 0.0
    assert m40.size_minor_arcmin is None

    m102 = cat.get("M102")
    assert m102.catalog_type == "Gx"
    assert dict(m102.cross_ids)["ngc"] == 5866
    assert abs(m102.ra_deg - 226.623171) <= RA_DEC_TOL_DEG
    assert abs(m102.dec_deg - 55.763308) <= RA_DEC_TOL_DEG
    assert m102.constellation == "Dra"


# ---------------------------------------------------------------------------
# API 与桌面 _draw_dso 字段对齐
# ---------------------------------------------------------------------------
def test_lookup_and_view_contract(cat):
    # _draw_dso 实际读取的字段
    for e in cat:
        assert isinstance(e.name, str)
        assert isinstance(e.dso_type, str)
        assert isinstance(e.ra_deg, float)
        assert isinstance(e.dec_deg, float)
        assert isinstance(e.magnitude, float)
        assert isinstance(e.size_arcmin, float)
        assert e.ra_hours == e.ra_deg / 15.0

    assert cat.get("M31") is not None
    assert cat.get("m 31") is not None
    assert cat.get("NGC 253") is not None
    assert cat.get("M111") is None
    assert cat.get("NGC9999") is None

    near = cat.near(10.6847, 41.26875, 2.0)
    assert {e.name for e in near} >= {"M31", "M32", "M110"}
    assert len(cat.filter_type("galaxy")) == 41
    assert len(cat.filter_type("supernova_remnant")) == 1


def test_display_name_prefers_chinese(cat):
    assert cat.get("M31").display_name == "仙女座星系"
    assert cat.get("M42").display_name == "猎户座大星云"
    assert cat.get("M2").display_name == "M2"


# ---------------------------------------------------------------------------
# 守卫：重复 / 越界 / 缺星等 / meta / SHA / 计数
# ---------------------------------------------------------------------------
def _base_payload() -> dict:
    return json.loads(DSO_PATH.read_text(encoding="utf-8"))


def test_duplicate_designation_rejected(tmp_path):
    payload = _base_payload()
    payload["objects"].append(dict(payload["objects"][0]))
    _rehash(payload)
    path = _variant(tmp_path, payload, "dup.json")
    with pytest.raises(dso.DSOIntegrityError, match="重复"):
        dso.load_dso_catalog(str(path))


def test_out_of_range_coordinate_rejected(tmp_path):
    payload = _base_payload()
    payload["objects"][0]["ra_deg"] = 400.0
    _rehash(payload)
    path = _variant(tmp_path, payload, "ra.json")
    with pytest.raises(dso.DSOIntegrityError, match="RA 越界"):
        dso.load_dso_catalog(str(path))


def test_missing_magnitude_rejected(tmp_path):
    payload = _base_payload()
    payload["objects"][0]["magnitude"] = 99
    _rehash(payload)
    path = _variant(tmp_path, payload, "mag.json")
    with pytest.raises(dso.DSOIntegrityError, match="星等缺失"):
        dso.load_dso_catalog(str(path))


@pytest.mark.parametrize("key", ["source_url", "source_license", "source_files"])
def test_missing_meta_rejected(tmp_path, key):
    payload = _base_payload()
    payload["meta"].pop(key)
    _rehash(payload)
    path = _variant(tmp_path, payload, f"meta_{key}.json")
    with pytest.raises(dso.DSOMetadataError):
        dso.load_dso_catalog(str(path))


def test_tampered_source_sha_rejected(tmp_path):
    payload = _base_payload()
    payload["meta"]["source_files"]["catalog"]["sha256"] = "0" * 64
    path = _variant(tmp_path, payload, "sha.json")
    with pytest.raises(dso.DSOIntegrityError, match="SHA-256"):
        dso.load_dso_catalog(str(path))


def test_tampered_record_rejected(tmp_path):
    payload = _base_payload()
    payload["objects"][0]["zh_name"] = "Fake English Name"
    _rehash(payload)
    path = _variant(tmp_path, payload, "tamper.json")
    with pytest.raises(dso.DSOIntegrityError, match="中文名"):
        dso.load_dso_catalog(str(path))


def test_count_mismatch_rejected(tmp_path):
    payload = _base_payload()
    payload["meta"]["counts"]["total"] = 118
    _rehash(payload)
    path = _variant(tmp_path, payload, "count.json")
    with pytest.raises(dso.DSOIntegrityError, match="counts.total"):
        dso.load_dso_catalog(str(path))
