# SPDX-License-Identifier: MIT
"""
tests/test_sky_cultures.py — 统一多文化天空模型测试

覆盖:
  1. IAU 适配包的 group/edge 数量与 constellations.py 现有数据一致；
  2. 中国星官适配包保留 31 区域，名称与 xingguan 一致；
  3. 已收录文化包（含第二/三/四/五批）可加载、meta 完整、
     SHA 校验通过、许可合规、统计非零、单包小于 2 MB；
     同一 HIP 在多个文化下分别返回各自名字（不合并）；
  4. 许可白名单拒绝 CC BY-ND / CC BY-NC* / GPL；``Text and data:``
     限定词剥离后按规范值判定，2.0/3.0/无版本号仍拒绝；
  5. 坏 SHA / 坏 JSON / 原始许可串与规范值矛盾拒绝加载。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from mbdsdr_ai.skyengine import constellations as c  # noqa: E402
from mbdsdr_ai.skyengine import sky_culture as sc  # noqa: E402
from mbdsdr_ai.skyengine import xingguan as x  # noqa: E402
import build_sky_cultures as builder  # noqa: E402

DATA_DIR = ROOT / "mbdsdr_ai" / "data" / "skycultures"

#: 第二批 8 个非中国主体文化包（Task 76 增补）；许可证一律以各自
#: `description.md` 的 `## License` 原文为准，白名单外的一律不收。
BATCH2_CULTURES = (
    "maori", "mongolian", "sami", "hawaiian_starlines",
    "tukano", "boorong", "russian_siberian", "norse_edda",
)

#: 第三批 6 个非中国主体文化包（Task 79 增补），逐包以
#: ``description.md`` 的 ``## License`` 原文核对，均为 CC BY-SA 4.0。
BATCH3_CULTURES = (
    "anutan", "samoan", "tongan", "vanuatu_netwar", "tikuna",
    "ruanui_sky_tahiti_and_society_islands",
)

#: 第四批 6 个包（Task 86 增补）：`modern_rey` 与 5 个中国历史变体，
#: 逐包核对 ``description.md`` 的 ``## License`` 原文，均为 CC BY-SA 4.0。
BATCH4_CULTURES = (
    "chinese_song_dynasty", "chinese_yuan_dynasty", "chinese_manchu",
    "chinese_xianglin", "chinese_chenzhuo", "modern_rey",
)

#: 第五批 3 个包（Task 88 增补）：上游许可行为 ``Text and data: CC BY-SA 4.0``
#: （illustrations 另有许可）。只转换连线数据，故数据部分适用 CC BY-SA 4.0。
BATCH5_CULTURES = ("belarusian", "modern", "modern_chinese")

#: 已收录的文件文化包（maya 源数据为 CC BY-NC-SA 4.0，按白名单拒绝；
#: seri 为 CC BY-SA 4.0 的补位文化）。
NEW_CULTURES = (
    "japanese", "indian", "norse", "indian_nakshatras", "seri",
) + BATCH2_CULTURES + BATCH3_CULTURES + BATCH4_CULTURES + BATCH5_CULTURES


def _load_raw(culture_id: str) -> dict:
    return json.loads((DATA_DIR / f"{culture_id}.json").read_text(
        encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1. IAU 适配器
# ---------------------------------------------------------------------------
def test_iau_pack_matches_existing_data():
    ds = c.load_iau_constellations()
    pack = sc.pack_from_iau()
    assert pack.id == c.SYSTEM_IAU
    assert len(pack.groups) == len(ds)
    assert {g.group_id for g in pack.groups} == set(ds.by_abbr)

    expected_edges = set()
    for con in ds:
        for poly in con.lines:
            hips = [int(star.hip) for star in poly]
            for a, b in zip(hips, hips[1:]):
                expected_edges.add((a, b) if a <= b else (b, a))
    assert pack.edges == frozenset(expected_edges)

    expected_unresolved = sorted({
        int(h) for con in ds for h in con.unresolved_hips})
    assert list(pack.unresolved_hips) == expected_unresolved

    # 名称与现有中文/拉丁名一一对应，不做二次翻译。
    for con in ds:
        group = pack.group_by_id(con.abbr)
        assert group is not None
        assert group.names["zh"] == con.zh_name
        assert group.names["la"] == con.latin_name


# ---------------------------------------------------------------------------
# 2. 中国星官适配器
# ---------------------------------------------------------------------------
def test_xingguan_pack_keeps_31_regions_and_names():
    ds = x.load_xingguan()
    pack = sc.pack_from_xingguan()
    assert pack.id == c.SYSTEM_CHINESE
    assert len(pack.groups) == len(ds) == 31
    assert {g.group_id for g in pack.groups} == set(ds.by_key)

    for region in ds:
        group = pack.group_by_id(region.key)
        assert group is not None
        assert group.kind == "xingguan"
        assert group.region == region.key
        assert group.region_type == region.type
        assert group.names["zh"] == region.name_zh
        assert group.names["en"] == region.name_en
        assert set(group.segments()) == {
            (int(a.hip), int(b.hip)) for a, b in region.all_segments()}

    # 星名与 xingguan_star_names_zh 保持一致。
    names = x.load_xingguan_star_names()
    for item in names.by_hip.values():
        if item.zh_name:
            assert pack.names[int(item.hip)]["zh"] == item.zh_name
            break


# ---------------------------------------------------------------------------
# 3. 新文化包
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("culture_id", NEW_CULTURES)
def test_new_culture_pack_loads_with_complete_meta(culture_id):
    raw = _load_raw(culture_id)
    pack = sc.load_pack(DATA_DIR / f"{culture_id}.json")
    assert pack.id == culture_id
    for key in ("schema", "source", "source_url", "source_license",
                "source_license_url", "attribution", "source_file_sha256",
                "data_sha256"):
        assert pack.meta.get(key), f"{culture_id}: meta 缺 {key}"
    assert pack.meta["source_license"] in sc.ALLOWED_LICENSES
    assert pack.license_url == sc.license_url_for(pack.license)

    # SHA 与统一模型的规范口径一致。
    assert pack.meta["data_sha256"] == c.payload_sha256(raw)
    assert pack.meta["edge_count"] == len(pack.edges)
    assert pack.meta["group_count"] == len(pack.groups)
    assert pack.meta["name_count"] == len(pack.names)

    assert len(pack.groups) > 0
    assert len(pack.edges) > 0
    assert pack.names
    assert len(pack.groups) == pack.meta["group_count"]

    # 单包体积上界与生成脚本一致；超限必须在生成时就拒绝，不能硬塞进仓库。
    assert (DATA_DIR / f"{culture_id}.json").stat().st_size < \
        builder.MAX_PACK_BYTES


@pytest.mark.parametrize("culture_id", BATCH2_CULTURES)
def test_batch2_pack_is_licensed_and_non_empty(culture_id):
    """第二批包：可加载、许可在白名单、统计非零且体积未超限。"""
    pack = sc.load_pack(DATA_DIR / f"{culture_id}.json")
    raw = _load_raw(culture_id)
    assert pack.meta["source_license"] in sc.ALLOWED_LICENSES
    assert pack.meta["source_license_url"] == sc.license_url_for(
        pack.meta["source_license"])
    assert pack.meta["data_sha256"] == c.payload_sha256(raw)
    assert len(pack.groups) == pack.meta["group_count"] > 0
    assert len(pack.edges) == pack.meta["edge_count"] > 0
    assert len(pack.names) == pack.meta["name_count"] > 0
    assert (DATA_DIR / f"{culture_id}.json").stat().st_size < \
        builder.MAX_PACK_BYTES


@pytest.mark.parametrize("culture_id", BATCH5_CULTURES)
def test_batch5_qualified_license_pack_keeps_raw_and_nonzero_stats(culture_id):
    """第五批：许可带 ``Text and data:`` 限定词，规范值入白名单，原始串保留。"""
    raw = _load_raw(culture_id)
    pack = sc.load_pack(DATA_DIR / f"{culture_id}.json")
    assert pack.meta["source_license"] == "CC BY-SA 4.0"
    assert pack.meta["source_license_raw"] == "Text and data: CC BY-SA 4.0"
    assert pack.license == "CC BY-SA 4.0"
    assert pack.meta["source_license_url"] == sc.license_url_for(pack.license)
    assert pack.meta["data_sha256"] == c.payload_sha256(raw)

    assert len(pack.groups) == pack.meta["group_count"] > 0
    assert len(pack.edges) == pack.meta["edge_count"] > 0
    assert len(pack.names) == pack.meta["name_count"] > 0
    assert (DATA_DIR / f"{culture_id}.json").stat().st_size < \
        builder.MAX_PACK_BYTES


def test_names_for_hip_keeps_cultures_separate():
    hip = 11767  # Polaris：多文化均有自己的名称
    names = sc.names_for_hip(hip)
    assert {"japanese", "indian", "norse", "seri"} <= set(names)
    assert names["japanese"]["en"] != names["indian"]["en"]
    assert len({json.dumps(v, ensure_ascii=False, sort_keys=True)
                for v in names.values()}) > 1
    assert isinstance(names["iau_88"], dict)
    assert "chinese_xingguan" in sc.cultures_of_hip(hip)


def test_registry_lists_adapters_and_new_cultures():
    ids = set(sc.list_packs())
    assert {"iau_88", "chinese_xingguan"} <= ids
    assert set(NEW_CULTURES) <= ids
    # 只做下限断言：后续增补合规文化包不应让本测试失效。
    assert len(ids) >= len(NEW_CULTURES) + 2
    assert "maya" not in ids  # 源许可 CC BY-NC-SA 4.0，拒绝收录

    registry = sc.load_registry()
    assert len(registry) == len(ids)
    assert registry.get("norse") is not None
    assert registry.get("does_not_exist") is None
    with pytest.raises(KeyError):
        sc.get_pack("does_not_exist")


def test_segments_for_groups_by_culture():
    segments = sc.segments_for(["japanese", "indian"])
    assert set(segments) == {"japanese", "indian"}
    assert segments["japanese"] != segments["indian"]
    assert len(segments["japanese"]) > 0
    assert len(segments["indian"]) > 0


def test_stats_matches_packs():
    result = sc.stats()
    ids = sc.list_packs()
    assert result["pack_count"] == len(ids)
    assert set(result["packs"]) == set(ids)
    for pack_id in ids:
        pack = sc.get_pack(pack_id)
        row = result["packs"][pack_id]
        assert row["groups"] == len(pack.groups)
        assert row["edges"] == len(pack.edges)
        assert row["names"] == len(pack.names)


# ---------------------------------------------------------------------------
# 4. 许可拒绝
# ---------------------------------------------------------------------------
def _write_fake_culture(root: Path, culture_id: str, license_text: str) -> Path:
    culture_dir = root / "skycultures" / culture_id
    culture_dir.mkdir(parents=True)
    (culture_dir / "index.json").write_text(json.dumps({
        "id": culture_id,
        "region": "Test",
        "classification": ["test"],
        "constellations": [{
            "id": f"CON {culture_id} 001",
            "lines": [[11767, 11762]],
            "common_name": {"english": "Test Figure"},
        }],
    }), encoding="utf-8")
    (culture_dir / "description.md").write_text(
        f"# Test\n\n## License\n\n{license_text}\n", encoding="utf-8")
    return culture_dir


def test_cc_by_nd_is_rejected(tmp_path):
    culture_dir = _write_fake_culture(tmp_path, "ccbynd", "CC BY-ND 4.0")
    with pytest.raises(builder.LicenseRejectedError, match="CC BY-ND"):
        builder.convert_culture(culture_dir, {11767, 11762})


@pytest.mark.parametrize("license_text", [
    "CC BY-NC 4.0", "CC BY-NC-SA 4.0", "GNU GPL v2.0",
])
def test_non_redistributable_licenses_are_rejected(license_text):
    with pytest.raises(builder.LicenseRejectedError, match="白名单"):
        builder.license_decision(license_text)


def test_allowed_licenses_pass_and_convert(tmp_path):
    assert builder.license_decision("CC BY-SA 4.0") == "CC BY-SA 4.0"
    assert builder.license_decision("CC BY 4.0") == "CC BY 4.0"
    assert builder.license_decision("CC0 / Public Domain") == \
        "CC0 / Public Domain"

    culture_dir = _write_fake_culture(tmp_path, "ccby", "CC BY 4.0")
    payload = builder.convert_culture(culture_dir, {11767, 11762})
    assert payload["groups"]
    assert payload["unresolved_hips"] == []
    assert payload["meta"]["source_license"] == "CC BY 4.0"
    assert payload["meta"]["data_sha256"] == c.payload_sha256(payload)


def test_text_and_data_qualifier_is_stripped_to_canonical_license():
    """``Text and data:`` 只是作用域限定词：剥掉后按规范值判白名单。"""
    assert builder.license_decision("Text and data: CC BY-SA 4.0") == \
        "CC BY-SA 4.0"
    assert builder.license_decision(
        "  text and  data :  CC BY-SA 4.0  ") == "CC BY-SA 4.0"
    assert sc.normalize_license("TEXT AND DATA:CC BY-SA 4.0") == "CC BY-SA 4.0"
    assert sc.split_license("Text and data: CC BY-SA 4.0") == (
        "Text and data: CC BY-SA 4.0", "CC BY-SA 4.0")
    assert sc.license_url_for("Text and data: CC BY-SA 4.0") == \
        sc.LICENSE_URLS["CC BY-SA 4.0"]


def test_qualified_license_convert_keeps_raw_string(tmp_path):
    culture_dir = _write_fake_culture(
        tmp_path, "qualified", "Text and data: CC BY-SA 4.0")
    payload = builder.convert_culture(culture_dir, {11767, 11762})
    assert payload["meta"]["source_license"] == "CC BY-SA 4.0"
    assert payload["meta"]["source_license_raw"] == "Text and data: CC BY-SA 4.0"


@pytest.mark.parametrize("license_text", [
    "CC BY-SA 2.0",
    "CC BY-SA 3.0 Unported",
    "CC BY-SA",
    "Text and data: CC BY-SA 3.0",
    "Text and data: CC BY-SA",
])
def test_versioned_or_unversioned_licenses_still_rejected(license_text):
    """限定词剥离不放宽白名单：2.0/3.0/无版本号仍拒绝。"""
    with pytest.raises(builder.LicenseRejectedError, match="白名单"):
        builder.license_decision(license_text)


# ---------------------------------------------------------------------------
# 5. 坏包拒绝
# ---------------------------------------------------------------------------
def test_bad_sha_is_rejected(tmp_path):
    raw = _load_raw("norse")
    raw["groups"][0]["names"]["en"] = "tampered"
    path = tmp_path / "bad_sha.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sc.SkyCultureIntegrityError):
        sc.load_pack(path)


def test_bad_json_is_rejected(tmp_path):
    path = tmp_path / "bad_json.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(sc.SkyCultureMetadataError):
        sc.load_pack(path)


def test_disallowed_license_in_pack_is_rejected_at_load(tmp_path):
    raw = _load_raw("norse")
    raw["meta"]["source_license"] = "CC BY-NC-SA 4.0"
    raw["meta"]["data_sha256"] = c.payload_sha256(raw)
    path = tmp_path / "bad_license.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sc.SkyCultureMetadataError, match="白名单"):
        sc.load_pack(path)


def test_qualified_license_in_pack_loads_as_canonical(tmp_path):
    """加载器同样剥离 ``Text and data:``，``pack.license`` 只暴露规范值。"""
    raw = _load_raw("norse")
    raw["meta"].pop("source_license_raw", None)
    raw["meta"]["source_license"] = "Text and data: CC BY-SA 4.0"
    raw["meta"]["data_sha256"] = c.payload_sha256(raw)
    path = tmp_path / "qualified_license.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    pack = sc.load_pack(path)
    assert pack.license == "CC BY-SA 4.0"
    assert pack.license_url == sc.LICENSE_URLS["CC BY-SA 4.0"]


def test_raw_license_contradicting_canonical_is_rejected(tmp_path):
    raw = _load_raw("belarusian")
    raw["meta"]["source_license_raw"] = "Text and data: CC BY-SA 3.0"
    raw["meta"]["data_sha256"] = c.payload_sha256(raw)
    path = tmp_path / "contradictory.json"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(sc.SkyCultureMetadataError, match="不一致"):
        sc.load_pack(path)

