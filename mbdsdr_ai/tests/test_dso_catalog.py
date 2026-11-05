# SPDX-License-Identifier: MIT
"""
tests/test_dso_catalog.py — 深空天体事实目录测试（OpenNGC 源）

覆盖:
  - 117 条 = Messier 1–110 + 7 个常用亮 NGC；编号无重复、无缺号
  - 每条坐标/星等/角径/类型/星座与 OpenNGC 源文件独立解析结果对拍
    （30 颗抽样，含 M31/M42/M45/M104/M40/M16/M76）
  - meta 来源/许可/SHA/缺字段计数/复现命令可审计，且不含 GPL
  - 源目录缺失字段写 null（不是 0/99），API 侧为 None
  - 中文名只来自 Wikidata 或逐条标注的自译；覆盖数与清单固定
  - M102 固定映射 NGC 5866 的约定被钉死（防止条目数变 116）
  - 重复编号、坐标越界、星等 99、尺寸 0、meta 缺失、SHA 篡改均抛错
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path

import pytest

from mbdsdr_ai.astronomy import dso

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DSO_PATH = DATA_DIR / "dso_catalog.json"
ZH_PATH = DATA_DIR / "dso_zh_wikidata.json"
# 随仓副本（干净 clone 也能独立对拍）；生成器仍默认读仓库外 reference/openngc。
REFERENCE = Path(__file__).resolve().parent / "fixtures" / "openngc"
NGC_CSV = REFERENCE / "NGC.csv"
ADDENDUM_CSV = REFERENCE / "addendum.csv"
DOC_PATH = Path(__file__).resolve().parents[2] / "docs" / "dso-catalog.md"

EXTRAS = (253, 869, 884, 2070, 5139, 7000, 7293)
CJK_RE = re.compile(r"[\u3400-\u9fff]")

SAMPLE_30 = (
    "M1", "M8", "M13", "M16", "M20", "M27", "M31", "M33", "M40", "M42",
    "M45", "M51", "M57", "M63", "M64", "M74", "M76", "M81", "M83", "M87",
    "M94", "M97", "M101", "M102", "M104", "M110",
    "NGC253", "NGC869", "NGC2070", "NGC7293",
)

RA_DEC_TOL_DEG = 1e-6
MAG_TOL = 1e-6
SIZE_TOL_ARCMIN = 1e-6

MISSING_MAG = ["M22", "M35", "M62", "M69", "M70", "M71", "M107"]
MISSING_SIZE = ["M40", "M73"]
SELF_ZH = {"M11", "M13", "M17", "M99", "NGC2070"}

SAMPLE_NAMES = {
    "M31": ("Andromeda Galaxy", "仙女座星系"),
    "M42": ("Great Orion Nebula", "猎户座大星云"),
    "M45": ("Pleiades", "昴宿星团"),
    "M104": ("Sombrero Galaxy", "阔边帽星系"),
    "M16": ("Eagle Nebula", "鹰星云"),
    "M76": ("Barbell Nebula", "小哑铃星云"),
    "NGC2070": ("30 Dor Cluster", "蜘蛛星云"),
}


@pytest.fixture(scope="module")
def cat():
    return dso.load_dso_catalog()


@pytest.fixture(scope="module")
def payload():
    return json.loads(DSO_PATH.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_rows(path: Path) -> list:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return [dict(rec, _file=path.name, _row=i)
                for i, rec in enumerate(csv.DictReader(fh, delimiter=";"), 2)]


def _source_rows():
    rows = _read_rows(NGC_CSV) + _read_rows(ADDENDUM_CSV)
    by_name = {r["Name"]: r for r in rows}
    by_m = {}
    for r in rows:
        m = r["M"].strip()
        if m and r["Type"] != "Dup":
            by_m[int(m)] = r
    return rows, by_name, by_m


def _source_row(key: str):
    rows, by_name, by_m = _source_rows()
    if key == "M102":
        dup = [r for r in rows if r["Name"] == "M102"]
        assert len(dup) == 1 and dup[0]["Type"] == "Dup"
        assert dup[0]["M"] == "101"
        return by_name["NGC5866"], dup[0]
    if key.startswith("M"):
        return by_m[int(key[1:])], None
    number = int(key[3:])
    for candidate in (f"NGC{number}", f"NGC{number:04d}"):
        if candidate in by_name:
            return by_name[candidate], None
    raise AssertionError(f"源目录缺少 {key}")


def _num(text: str):
    text = (text or "").strip()
    return float(text) if text else None


def _ra(text: str) -> float:
    h, m, s = text.split(":")
    return (float(h) + float(m) / 60 + float(s) / 3600) * 15.0


def _dec(text: str) -> float:
    sign = -1.0 if text.startswith("-") else 1.0
    d, m, s = text.lstrip("+-").split(":")
    return sign * (float(d) + float(m) / 60 + float(s) / 3600)


def _mag(row: dict):
    for band, column in (("V", "V-Mag"), ("B", "B-Mag")):
        value = _num(row[column])
        if value is not None and value != 0:
            return value, band
    return None, ""


def _size(row: dict, column: str):
    value = _num(row[column])
    return value if value is not None and value > 0 else None


def _common(row: dict) -> list:
    out = []
    for part in row["Common names"].split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _con(row: dict) -> str:
    return {"Se1": "Ser", "Se2": "Ser"}.get(row["Const"], row["Const"])


def _ngc_from_name(row: dict):
    name = row["Name"]
    return int(name[3:]) if name.startswith("NGC") else None


def _ic_from_name(row: dict):
    name = row["Name"]
    return int(name[2:]) if name.startswith("IC") else None


def _expected_cross(key: str, row: dict):
    pairs = []
    ngc = _ngc_from_name(row)
    if ngc is not None:
        pairs.append(("ngc", ngc))
    ic = _ic_from_name(row)
    if ic is not None:
        pairs.append(("ic", ic))
    if key.startswith("M"):
        pairs.append(("m", int(key[1:])))
    return tuple(pairs)


# ------------------------------------------------------------------ 整体--

def test_catalog_size_and_designations(cat, payload):
    objects = payload["objects"]
    assert len(objects) == 117
    assert len(cat) == 117
    names = [o["name"] for o in objects]
    assert len(set(names)) == 117
    messier = sorted(int(n[1:]) for n in names if n.startswith("M"))
    assert messier == list(range(1, 111))
    assert len([n for n in names if n.startswith("NGC")]) == 7


def test_meta_provenance(payload):
    meta = payload["meta"]
    assert meta["schema"] == "mbdsdr.dso_catalog.v2"
    assert meta["source"] == "OpenNGC"
    assert meta["source_license"] == "CC-BY-SA-4.0"
    assert meta["source_license_url"] == (
        "https://creativecommons.org/licenses/by-sa/4.0/")
    assert "OpenNGC" in meta["attribution"]
    assert "Wikidata" in meta["attribution"]
    assert "GPL" not in json.dumps(meta, ensure_ascii=False).upper()
    assert "zh_CN.po" not in json.dumps(meta, ensure_ascii=False)

    files = meta["source_files"]
    assert set(files) == {"ngc", "addendum", "zh_labels"}
    assert files["ngc"]["sha256"] == _sha256_file(NGC_CSV)
    assert files["addendum"]["sha256"] == _sha256_file(ADDENDUM_CSV)
    assert files["zh_labels"]["sha256"] == _sha256_file(ZH_PATH)
    assert meta["data_sha256"] == dso._payload_sha256(payload)
    assert meta["generated_at"].endswith("+00:00")

    commands = "\n".join(meta["reproduction"]["commands"])
    assert "NGC.csv" in commands and "addendum.csv" in commands
    assert "build_dso_catalog" in commands


def test_missing_fields_are_null_and_counted(cat, payload):
    counts = payload["meta"]["counts"]
    missing = payload["meta"]["missing"]
    assert counts["without_magnitude"] == 7
    assert missing["magnitude"]["designations"] == MISSING_MAG
    assert counts["without_size_arcmin"] == 2
    assert missing["size_arcmin"]["designations"] == MISSING_SIZE
    assert counts["without_size_minor_arcmin"] == 66
    assert counts["without_name_en"] == 81
    assert counts["with_name_en"] == 36
    assert counts["with_zh_name"] == 34
    assert counts["without_zh_name"] == 83
    assert counts["magnitude_band_counts"] == {"B": 1, "V": 109, "none": 7}

    for name in MISSING_MAG:
        entry = cat.get(name)
        assert entry is not None and entry.magnitude is None
        assert entry.magnitude_band == ""
    for name in MISSING_SIZE:
        entry = cat.get(name)
        assert entry is not None and entry.size_arcmin is None
        assert entry.size_minor_arcmin is None
    # null 不得被 0/99 冒充
    for obj in payload["objects"]:
        assert obj["magnitude"] != 0 and obj["magnitude"] != 99
        if obj["size_arcmin"] is not None:
            assert obj["size_arcmin"] > 0
        if obj["size_minor_arcmin"] is not None:
            assert obj["size_minor_arcmin"] > 0


def test_axis_ratio_is_derived_or_unknown(payload):
    """轴比只在两轴齐全时由数据层给出；缺短轴一律 null + unknown。"""
    for obj in payload["objects"]:
        if obj["size_arcmin"] is not None and obj["size_minor_arcmin"] is not None:
            assert obj["axis_ratio"] == pytest.approx(
                obj["size_minor_arcmin"] / obj["size_arcmin"], rel=1e-9)
            assert obj["axis_ratio_source"] == "catalog"
        else:
            assert obj["axis_ratio"] is None
            assert obj["axis_ratio_source"] == "unknown"
        assert obj["axis_ratio_source"] != "estimated"


def test_axis_ratio_counts_match_known_axes(payload):
    counts = payload["meta"]["counts"]
    known = counts["with_size_minor_arcmin"]
    assert sum(1 for o in payload["objects"]
               if o["axis_ratio_source"] == "catalog") == known
    assert sum(1 for o in payload["objects"]
               if o["axis_ratio_source"] == "unknown") == 117 - known


def test_guard_axis_ratio_mismatch_and_estimated_rejected(payload):
    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    target = next(o for o in clone["objects"] if o["axis_ratio"] is not None)
    target["axis_ratio"] = target["axis_ratio"] + 0.25
    with pytest.raises(dso.DSOIntegrityError):
        dso._validate_payload(clone)

    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    target = next(o for o in clone["objects"] if o["axis_ratio"] is not None)
    target["axis_ratio_source"] = "estimated"
    with pytest.raises(dso.DSOIntegrityError):
        dso._validate_payload(clone)

    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    target = next(o for o in clone["objects"] if o["axis_ratio"] is None)
    target["axis_ratio"] = 0.55
    with pytest.raises(dso.DSOIntegrityError):
        dso._validate_payload(clone)


def test_30_sample_matches_independent_source(cat):
    for name in SAMPLE_30:
        row, _dup = _source_row(name)
        entry = cat.get(name)
        assert entry is not None, name
        assert entry.ra_deg == pytest.approx(_ra(row["RA"]), abs=RA_DEC_TOL_DEG)
        assert entry.dec_deg == pytest.approx(_dec(row["Dec"]), abs=RA_DEC_TOL_DEG)
        assert entry.constellation == _con(row)
        assert entry.catalog_type == row["Type"]
        assert entry.source_file == row["_file"]
        assert entry.source_row == row["_row"]
        assert entry.source_name == row["Name"]
        assert entry.cross_ids == _expected_cross(name, row)
        if _mag(row)[0] is not None:
            assert entry.magnitude == pytest.approx(_mag(row)[0], abs=MAG_TOL)
            assert entry.magnitude_band == _mag(row)[1]
        else:
            assert entry.magnitude is None
        expected_size = _size(row, "MajAx")
        if expected_size is None:
            assert entry.size_arcmin is None
        else:
            assert entry.size_arcmin == pytest.approx(
                expected_size, abs=SIZE_TOL_ARCMIN)
        expected_minor = _size(row, "MinAx")
        if expected_minor is None:
            assert entry.size_minor_arcmin is None
        else:
            assert entry.size_minor_arcmin == pytest.approx(
                expected_minor, abs=SIZE_TOL_ARCMIN)


def test_sample_names(cat):
    for name, (english, chinese) in SAMPLE_NAMES.items():
        entry = cat.get(name)
        assert entry.name_en == english
        assert entry.zh_name == chinese
        assert CJK_RE.search(entry.zh_name)
        assert entry.zh_name_source in ("Wikidata", "自译")

    m40 = cat.get("M40")
    assert m40.name_en == "" and m40.zh_name == ""
    assert m40.zh_name_source == ""
    assert m40.size_arcmin is None and m40.size_minor_arcmin is None
    assert m40.dso_type == "double_star"

    m102 = cat.get("M102")
    assert m102.name_en == "" and m102.zh_name == ""


def test_zh_names_are_sourced_and_counted(cat, payload):
    self_translated = {e.name for e in cat if e.zh_name_source == "自译"}
    wikidata = [e for e in cat if e.zh_name_source == "Wikidata"]
    empty = [e for e in cat if not e.zh_name]
    assert self_translated == SELF_ZH
    assert len(wikidata) == 29
    assert len(empty) == 83
    assert len(wikidata) + len(self_translated) == 34
    for entry in wikidata:
        assert re.match(r"^Q\d+", entry.zh_name_ref)
        assert entry.zh_name != entry.name_en
        assert CJK_RE.search(entry.zh_name)
    for entry in cat:
        if not entry.zh_name:
            assert entry.zh_name_source == "" and entry.zh_name_ref == ""
        else:
            assert entry.zh_name_source in ("Wikidata", "自译")
    meta_text = json.dumps(payload["meta"], ensure_ascii=False)
    assert "zh_CN.po" not in meta_text and "GPL" not in meta_text.upper()


def test_m102_convention_pinned(cat, payload):
    """OpenNGC 记为 Dup/M=101；本项目按历史约定取 NGC 5866，条目数保持 117。"""
    assert len(cat) == 117
    assert len([e for e in cat if e.name.startswith("M")]) == 110
    m102 = cat.get("M102")
    row, dup = _source_row("M102")
    assert dup["Type"] == "Dup" and dup["M"] == "101"
    assert m102 is not None
    assert m102.source_name == "NGC5866"
    assert m102.source_file == "NGC.csv"
    assert m102.cross_ids == (("ngc", 5866), ("m", 102))
    assert m102.catalog_type == "G"
    assert m102.ra_deg == pytest.approx(_ra(row["RA"]), abs=1e-6)
    assert m102.dec_deg == pytest.approx(_dec(row["Dec"]), abs=1e-6)
    convention = payload["meta"]["m102_convention"]
    assert convention["resolved_to"] == "NGC 5866"
    assert convention["entry_count"] == 117
    assert convention["messier_count"] == 110
    assert "Dup" in convention["openngc_record"]


def test_type_mapping_and_counts(payload):
    meta = payload["meta"]
    mapping = meta["type_mapping"]
    assert mapping["Other"] == "cluster"
    assert mapping["*Ass"] == "cluster"
    assert mapping["**"] == "double_star"
    assert mapping["Cl+N"] == "cluster_nebula"
    assert mapping["SNR"] == "supernova_remnant"
    for obj in payload["objects"]:
        assert mapping[obj["catalog_type"]] == obj["dso_type"]
        assert obj["dso_type"] in dso._RENDER_TYPES
    assert meta["counts"]["render_type_counts"] == {
        "cluster": 60, "cluster_nebula": 1, "double_star": 1,
        "galaxy": 41, "nebula": 13, "supernova_remnant": 1,
    }


def test_old_version_deprecated_in_documentation():
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "GPL" in text
    assert "已废弃" in text
    assert "M102" in text and "NGC 5866" in text
    assert "CC BY-SA 4.0" in text


# ------------------------------------------------------------------ 守卫--

def _rewrite(payload: dict) -> dict:
    clone = json.loads(json.dumps(payload))
    clone["meta"]["data_sha256"] = dso._payload_sha256(clone)
    return clone


def test_guard_duplicate_designation(payload):
    clone = _rewrite(payload)
    clone["objects"][1]["name"] = "M1"
    clone["objects"][1]["cross_ids"]["m"] = 1
    clone = _rewrite(clone)
    with pytest.raises(dso.DSOIntegrityError):
        dso._validate_payload(clone)


def test_guard_magnitude_sentinel_rejected(payload):
    clone = _rewrite(payload)
    target = next(o for o in clone["objects"] if o["name"] == "M31")
    target["magnitude"] = 99
    clone = _rewrite(clone)
    with pytest.raises(dso.DSOIntegrityError, match="星等"):
        dso._validate_payload(clone)


def test_guard_zero_size_rejected(payload):
    clone = _rewrite(payload)
    target = next(o for o in clone["objects"] if o["name"] == "M31")
    target["size_arcmin"] = 0
    clone = _rewrite(clone)
    with pytest.raises(dso.DSOIntegrityError, match="视尺寸"):
        dso._validate_payload(clone)


def test_guard_zh_without_source_rejected(payload):
    clone = _rewrite(payload)
    target = next(o for o in clone["objects"] if o["name"] == "M31")
    target["zh_name_source"] = ""
    target["zh_name_ref"] = ""
    clone = _rewrite(clone)
    with pytest.raises(dso.DSOIntegrityError, match="中文名"):
        dso._validate_payload(clone)


def test_guard_meta_gpl_rejected(payload):
    clone = _rewrite(payload)
    clone["meta"]["notes"].append("GPL")
    with pytest.raises(dso.DSOMetadataError, match="GPL"):
        dso._validate_payload(clone)


def test_guard_sha_tamper(payload):
    clone = json.loads(json.dumps(payload))
    clone["objects"][0]["ra_deg"] += 0.01
    with pytest.raises(dso.DSOIntegrityError, match="SHA-256"):
        dso._validate_payload(clone)
