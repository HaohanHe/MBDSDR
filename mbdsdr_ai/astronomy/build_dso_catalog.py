# SPDX-License-Identifier: MIT
"""
build_dso_catalog.py — 从 Stellarium 深空天体目录生成事实版 DSO 数据
====================================================================

产物 ``mbdsdr_ai/data/dso_catalog.json``：

- Messier 1–110 全量 + 当前视图已在用的 7 个常用亮 NGC
  （NGC 253/869/884/2070/5139/7000/7293）；
- 每条记录 = 编号、英文/中文名、源目录类型、渲染类型、J2000 RA/Dec、
  V 星等、长/短轴角径、所属 IAU 星座、交叉编号、源目录行号；
- 坐标/星等/角径/类型**只**取自 Stellarium ``nebulae/default/catalog.txt``
  （Catalog Version 3.23 standard）；英文名取自同目录 ``names.dat``；
  中文名取自 ``po/stellarium-sky/zh_CN.po`` 对 ``names.dat`` 的翻译；
  星座归属用 Stellarium ``data/constellations_spans.dat``（ADC VI/42）
  的 IAU spans 查表，J2000 先按 IAU 1976 岁差到 B1875；
- 取不到中文名的条目 ``zh_name`` 留空并计数，**绝不拿英文 proper name
  冒充中文名**；取不到英文名的条目同样留空，不补造；
- 未收录的 DSO 一律不进表。

用法::

    python -m mbdsdr_ai.astronomy.build_dso_catalog \\
        --stellarium D:/mbdsdr/reference/stellarium
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from ..skyengine.build_constellation_data import (
    canonical_sha256, sha256_file, write_json)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = DATA_DIR / "dso_catalog.json"
DEFAULT_STELLARIUM = Path(
    Path(__file__).resolve().parents[3] / "reference" / "stellarium")

EXTRA_NGC = (253, 869, 884, 2070, 5139, 7000, 7293)

CATALOG_VERSION = "3.23 standard"

# 源目录类型 -> 视图渲染类型。C+N（星团+星云）与 *（双星）保留独立类型，
# 避免把星团画成星云、或把双星画成星云。
TYPE_MAP = {
    "G": "galaxy", "GX": "galaxy", "Gx": "galaxy", "AGx": "galaxy",
    "IG": "galaxy", "QSO": "galaxy", "SA": "galaxy", "SC": "galaxy",
    "RG": "galaxy",
    "GC": "cluster", "OC": "cluster", "CL": "cluster",
    "NB": "nebula", "PN": "nebula", "DN": "nebula", "RN": "nebula",
    "HA": "nebula", "HII": "nebula", "BN": "nebula", "EN": "nebula",
    "SNR": "supernova_remnant",
    "C+N": "cluster_nebula",
    "*": "double_star",
}

CJK_RE = re.compile(r"[\u3400-\u9fff]")
NAME_RE = re.compile(r'_\(\"(.*?)\"\)')
PO_REF_RE = re.compile(r"^#: (.*)$", re.M)


def _to_int(text: str) -> int:
    text = text.strip()
    return int(text) if text and text != "0" else 0


def _to_float(text: str, *, sentinel: float = 99.0) -> Optional[float]:
    value = float(text)
    return None if value == sentinel else value


def _read_catalog(path: Path) -> List[dict]:
    """严格解析 catalog.txt；任何列数异常直接抛错，不静默丢行。"""
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            if line.startswith("#") or not line.strip():
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 45:
                raise ValueError(
                    f"{path}:{lineno} 列数 {len(parts)} != 45，拒绝静默丢弃")
            rows.append({
                "row": lineno,
                "id": int(parts[0]),
                "ra_deg": float(parts[1]),
                "dec_deg": float(parts[2]),
                "b_mag": _to_float(parts[3]),
                "v_mag": _to_float(parts[4]),
                "type": parts[5].strip(),
                "major_arcmin": float(parts[7]),
                "minor_arcmin": float(parts[8]),
                "ngc": _to_int(parts[16]),
                "ic": _to_int(parts[17]),
                "m": _to_int(parts[18]),
            })
    return rows


def _index_unique(rows: List[dict], key: str, *, label: str,
                  numbers: Optional[Iterable[int]] = None) -> Dict[int, dict]:
    wanted = set(numbers) if numbers is not None else None
    out: Dict[int, dict] = {}
    duplicates: List[int] = []
    for row in rows:
        value = int(row[key])
        if not value:
            continue
        if wanted is not None and value not in wanted:
            continue
        if value in out:
            duplicates.append(value)
            continue
        out[value] = row
    if duplicates:
        raise ValueError(f"{label} 源目录编号重复: {sorted(set(duplicates))}")
    if wanted is not None:
        missing = sorted(wanted - set(out))
        if missing:
            raise ValueError(f"{label} 未在源目录找到: {missing}")
    return out


def _read_names(path: Path) -> Dict[Tuple[str, str], List[str]]:
    """designator -> 源文件顺序的全部英文名（去重、保持顺序）。"""
    names: Dict[Tuple[str, str], List[str]] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            prefix = line[0:5].strip()
            ident = line[5:20].strip()
            match = NAME_RE.search(line)
            if not prefix or not ident or not match:
                continue
            bucket = names.setdefault((prefix, ident), [])
            name = match.group(1).strip()
            if name and name not in bucket:
                bucket.append(name)
    return names


def _po_blocks(path: Path) -> Iterable[Tuple[int, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    offset = 0
    for block in re.split(r"\n\n", text):
        yield offset + 1, block
        offset += block.count("\n") + 2


def _read_zh(path: Path) -> Dict[str, Tuple[str, int]]:
    """英文名 -> (中文名, po 文件行号)；忽略 fuzzy/空翻译/纯 ASCII 翻译。"""
    out: Dict[str, Tuple[str, int]] = {}
    for start_line, block in _po_blocks(path):
        if "#, fuzzy" in block or "#~" in block:
            continue
        msgid = re.findall(r'^msgid "((?:[^"\\]|\\.)*)"', block, re.M)
        msgstr = re.findall(r'^msgstr "((?:[^"\\]|\\.)*)"', block, re.M)
        if not msgid or not msgstr or not msgid[0] or not msgstr[0]:
            continue
        english = msgid[0]
        chinese = msgstr[0].strip()
        if not CJK_RE.search(chinese) or chinese == english:
            continue
        out[english] = (chinese, start_line)
    return out


def _parse_hms(text: str) -> float:
    h, m, s = text.split(":")
    return float(h) + float(m) / 60.0 + float(s) / 3600.0


def _parse_dms(text: str) -> float:
    sign = -1.0 if text.startswith("-") else 1.0
    text = text.lstrip("+-")
    d, m = text.split(":")[:2]
    return sign * (float(d) + float(m) / 60.0)


def _read_spans(path: Path) -> List[Tuple[float, float, float, str]]:
    spans: List[Tuple[float, float, float, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 4:
            continue
        spans.append((_parse_hms(parts[0]), _parse_hms(parts[1]),
                      _parse_dms(parts[2]), parts[3]))
    if not spans:
        raise ValueError(f"星座 spans 为空: {path}")
    return spans


def _precess_j2000_to_b1875(ra_deg: float, dec_deg: float) -> Tuple[float, float]:
    """IAU 1976 岁差，J2000 -> B1875（约 -75 儒略年）。

    只用于星座归属查表；精度远优于 spans 表本身的 1–2° 分带。
    """
    t = -1.25  # (1875.0 - 2000.0) / 100
    zeta = (2306.2181 * t + 0.301468 * t * t + 0.017998 * t ** 3) / 3600.0
    z = (2306.2181 * t + 1.094059 * t * t + 0.018203 * t ** 3) / 3600.0
    theta = (2004.3109 * t - 0.42665 * t * t - 0.041833 * t ** 3) / 3600.0

    ra = math.radians(ra_deg)
    dec = math.radians(dec_deg)
    a = math.cos(dec) * math.sin(ra + math.radians(zeta))
    b = (math.cos(math.radians(theta)) * math.cos(dec)
         * math.cos(ra + math.radians(zeta))
         - math.sin(math.radians(theta)) * math.sin(dec))
    c = (math.sin(math.radians(theta)) * math.cos(dec)
         * math.cos(ra + math.radians(zeta))
         + math.cos(math.radians(theta)) * math.sin(dec))
    ra_h = (math.degrees(math.atan2(a, b)) + z) % 360.0 / 15.0
    dec_out = math.degrees(math.asin(max(-1.0, min(1.0, c))))
    return ra_h, dec_out


def _constellation(ra_deg: float, dec_deg: float,
                   spans: List[Tuple[float, float, float, str]]) -> str:
    """复刻 Stellarium getIAUConstellation 的 spans 查表顺序。"""
    ra_h, dec = _precess_j2000_to_b1875(ra_deg, dec_deg)
    i = 0
    n = len(spans)
    while i < n and spans[i][2] > dec:
        i += 1
    while i < n:
        while i < n and spans[i][1] <= ra_h:
            i += 1
        while i < n and spans[i][0] >= ra_h:
            i += 1
        if i < n and spans[i][1] > ra_h:
            return spans[i][3]
        i += 1
    raise ValueError(f"星座归属查表失败: RA={ra_deg}, Dec={dec_deg}")


def _names_for(row: dict, names: Dict[Tuple[str, str], List[str]]) -> List[str]:
    keys = []
    if row["m"]:
        keys.append(("M", str(row["m"])))
    if row["ngc"]:
        keys.append(("NGC", str(row["ngc"])))
    if row["ic"]:
        keys.append(("IC", str(row["ic"])))
    for key in keys:
        if key in names:
            return names[key]
    return []


def _record(name: str, row: dict, names: Dict[Tuple[str, str], List[str]],
            zh: Dict[str, Tuple[str, int]],
            spans: List[Tuple[float, float, float, str]]) -> dict:
    if row["v_mag"] is None:
        raise ValueError(f"{name} 源目录 V 星等缺失（99），拒绝入表")
    raw_type = row["type"]
    if raw_type not in TYPE_MAP:
        raise ValueError(f"{name} 未知源目录类型: {raw_type!r}")
    english = _names_for(row, names)
    translated = [(zh[n][0], zh[n][1]) for n in english if n in zh]
    return {
        "name": name,
        "name_en": english[0] if english else "",
        "zh_name": translated[0][0] if translated else "",
        "aliases_en": english[1:],
        "aliases_zh": [z[0] for z in translated[1:]],
        "zh_name_ref": (f"po/stellarium-sky/zh_CN.po:{translated[0][1]}"
                        if translated else ""),
        "catalog_type": raw_type,
        "dso_type": TYPE_MAP[raw_type],
        "ra_deg": row["ra_deg"],
        "dec_deg": row["dec_deg"],
        "magnitude": row["v_mag"],
        "size_arcmin": row["major_arcmin"],
        "size_minor_arcmin": (row["minor_arcmin"]
                              if row["minor_arcmin"] > 0 else None),
        "constellation": _constellation(row["ra_deg"], row["dec_deg"], spans),
        "cross_ids": {
            "ngc": row["ngc"] or None,
            "ic": row["ic"] or None,
            "m": row["m"] or None,
        },
        "source_row": row["row"],
    }


def build(stellarium: Path) -> dict:
    nebulae = stellarium / "nebulae" / "default"
    catalog_path = nebulae / "catalog.txt"
    names_path = nebulae / "names.dat"
    po_path = stellarium / "po" / "stellarium-sky" / "zh_CN.po"
    spans_path = stellarium / "data" / "constellations_spans.dat"
    for path in (catalog_path, names_path, po_path, spans_path):
        if not path.is_file():
            raise FileNotFoundError(f"缺少源文件: {path}")

    rows = _read_catalog(catalog_path)
    m_by_number = _index_unique(rows, "m", label="Messier",
                                numbers=range(1, 111))
    ngc_by_number = _index_unique(rows, "ngc", label="NGC",
                                  numbers=EXTRA_NGC)

    names = _read_names(names_path)
    zh = _read_zh(po_path)
    spans = _read_spans(spans_path)

    objects = [_record(f"M{m}", m_by_number[m], names, zh, spans)
               for m in range(1, 111)]
    objects += [_record(f"NGC{n}", ngc_by_number[n], names, zh, spans)
                for n in EXTRA_NGC]

    raw_counts: Dict[str, int] = {}
    render_counts: Dict[str, int] = {}
    for obj in objects:
        raw_counts[obj["catalog_type"]] = raw_counts.get(obj["catalog_type"], 0) + 1
        render_counts[obj["dso_type"]] = render_counts.get(obj["dso_type"], 0) + 1

    without_en = [o["name"] for o in objects if not o["name_en"]]
    without_zh = [o["name"] for o in objects if not o["zh_name"]]
    payload = {
        "meta": {
            "schema": "mbdsdr.dso_catalog.v1",
            "catalog": "Messier 1-110 + 7 bright NGC",
            "catalog_version": CATALOG_VERSION,
            "source": "Stellarium deep-sky object catalog (nebulae/default)",
            "source_url": ("https://github.com/Stellarium/stellarium/"
                           "tree/master/nebulae/default"),
            "source_license": "GPL-2.0-or-later",
            "source_license_url": ("https://www.gnu.org/licenses/"
                                   "old-licenses/gpl-2.0.html"),
            "attribution": (
                "Deep-sky positions, types, magnitudes and sizes from "
                "Stellarium nebulae/default/catalog.txt (Catalog Version "
                f"{CATALOG_VERSION}); English names from names.dat; "
                "Chinese names from Stellarium zh_CN.po translations of "
                "names.dat; constellation membership from "
                "data/constellations_spans.dat (ADC VI/42, Roman 1987)."),
            "source_files": {
                "catalog": {"path": "nebulae/default/catalog.txt",
                            "sha256": sha256_file(catalog_path)},
                "names": {"path": "nebulae/default/names.dat",
                          "sha256": sha256_file(names_path)},
                "zh_translations": {"path": "po/stellarium-sky/zh_CN.po",
                                    "sha256": sha256_file(po_path)},
                "constellation_spans": {
                    "path": "data/constellations_spans.dat",
                    "sha256": sha256_file(spans_path)},
            },
            "constellation_method": (
                "IAU constellation spans lookup (Stellarium "
                "getIAUConstellation / ADC VI/42); J2000 coordinates are "
                "precessed to B1875 with IAU 1976 precession before lookup. "
                "Labels only; positions near boundaries may be approximate."),
            "name_policy": (
                "English proper names only from names.dat; Chinese names "
                "only from zh_CN.po translations containing CJK. Never "
                "substitute an English name for a missing Chinese name; "
                "never invent names."),
            "type_mapping": TYPE_MAP,
            "counts": {
                "total": len(objects),
                "messier": 110,
                "extra_ngc": len(EXTRA_NGC),
                "with_name_en": len(objects) - len(without_en),
                "without_name_en": len(without_en),
                "with_zh_name": len(objects) - len(without_zh),
                "without_zh_name": len(without_zh),
                "no_name_designations": without_en,
                "no_zh_name_designations": without_zh,
                "raw_type_counts": dict(sorted(raw_counts.items())),
                "render_type_counts": dict(sorted(render_counts.items())),
            },
            "notes": [
                "M40 (Winnecke 4) is a double star in the source catalog "
                "(type *), not a nebula/cluster; render type double_star.",
                "M102 is cross-indexed to NGC 5866 by the source catalog.",
                "C+N entries are cluster+nebula systems; render type "
                "cluster_nebula.",
                "Entries without a source proper name keep name_en/zh_name "
                "empty and are counted; their designation remains the label.",
            ],
            "generated_at": _dt.datetime.now(_dt.timezone.utc)
            .isoformat(timespec="seconds"),
            "generator": "mbdsdr_ai.astronomy.build_dso_catalog",
        },
        "objects": objects,
    }
    payload["meta"]["data_sha256"] = canonical_sha256(payload)
    return payload


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stellarium", default=str(DEFAULT_STELLARIUM))
    args = ap.parse_args(list(argv) if argv is not None else None)
    payload = build(Path(args.stellarium))
    write_json(OUT_PATH, payload)
    counts = payload["meta"]["counts"]
    print(f"写入 {OUT_PATH}")
    print(f"总数 {counts['total']} / 有英文名 {counts['with_name_en']} / "
          f"有中文名 {counts['with_zh_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
