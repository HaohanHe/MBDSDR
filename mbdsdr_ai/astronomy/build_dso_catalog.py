# SPDX-License-Identifier: MIT
"""
build_dso_catalog.py — 从 OpenNGC 生成事实版 DSO 数据
====================================================

产物 ``mbdsdr_ai/data/dso_catalog.json``：

- Messier 1–110 全量 + 当前视图已在用的 7 个常用亮 NGC
  （NGC 253/869/884/2070/5139/7000/7293）；
- 每条记录 = 编号、英文/中文名、源目录类型、渲染类型、J2000 RA/Dec、
  星等（含测光波段）、长/短轴角径、所属 IAU 星座、交叉编号、源文件/行号；
- 坐标/星等/角径/类型/星座**只**取自 OpenNGC
  ``database_files/NGC.csv`` + ``database_files/addendum.csv``
  （CC BY-SA 4.0）；英文常用名只取自 OpenNGC ``Common names`` 列；
- 中文名取自 Wikidata（CC0 1.0）按 P528 目录编号匹配的 zh/zh-cn/zh-hans
  标签；取不到且英文常用名可意译者由本项目自译并逐条标注
  ``zh_name_source="自译"``；仍取不到的留空并计数。**绝不使用
  Stellarium ``zh_CN.po`` 的 GPL 译文**；
- 源目录缺字段一律写 ``null`` 并计数，不用 0/99 冒充；
- M102 按历史约定固定映射 NGC 5866（OpenNGC addendum 把 M102 记为
  ``Dup``、``M=101``）；因此 Messier 仍为 110 条、总数仍为 117 条。

旧版说明：``a3aadd8`` 及更早的 ``dso_catalog.json`` 由 Stellarium
``nebulae/default/catalog.txt`` 与 ``zh_CN.po`` 生成，含 GPL-2.0-or-later
来源数据，已废弃并由本文件整体替换。当前数据不含任何 GPL 来源。

用法::

    # 一次性：从 Wikidata 拉取中文标签快照（需要联网）
    python -m mbdsdr_ai.astronomy.build_dso_catalog --fetch-zh

    # 常规重建（离线，读已提交的 Wikidata 快照）
    python -m mbdsdr_ai.astronomy.build_dso_catalog \\
        --opengnc D:/mbdsdr/reference/openngc
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import math
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..skyengine.build_constellation_data import (
    canonical_sha256, sha256_file, write_json)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_PATH = DATA_DIR / "dso_catalog.json"
ZH_LABELS_PATH = DATA_DIR / "dso_zh_wikidata.json"
DEFAULT_OPENNGC = Path(
    Path(__file__).resolve().parents[3] / "reference" / "openngc")

SOURCE_URL = "https://github.com/mattiaverga/OpenNGC"
NGC_URL = ("https://cdn.jsdelivr.net/gh/mattiaverga/OpenNGC@master/"
           "database_files/NGC.csv")
ADDENDUM_URL = ("https://cdn.jsdelivr.net/gh/mattiaverga/OpenNGC@master/"
                "database_files/addendum.csv")
SOURCE_LICENSE = "CC-BY-SA-4.0"
SOURCE_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
WIKIDATA_URL = "https://query.wikidata.org/sparql"
WIKIDATA_LICENSE = "CC0-1.0"
WIKIDATA_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
SOURCE_DIR_LABEL = "reference/openngc"
EXTRAS = (253, 869, 884, 2070, 5139, 7000, 7293)
M102_TARGET = "NGC5866"
M102_DUP_NOTE = "addendum.csv: M102, Type=Dup, M=101"

REQUIRED_COLUMNS = (
    "Name", "Type", "RA", "Dec", "Const", "MajAx", "MinAx",
    "V-Mag", "B-Mag", "M", "NGC", "IC", "Common names",
)

# OpenNGC 类型 -> 视图渲染类型。``Other``（M73 星群）显式映射为 cluster，
# ``*Ass``（星协）同样按 cluster 渲染；映射关系写入 meta.type_mapping。
TYPE_MAP = {
    "G": "galaxy",
    "GCl": "cluster",
    "OCl": "cluster",
    "*Ass": "cluster",
    "Other": "cluster",
    "PN": "nebula",
    "Neb": "nebula",
    "HII": "nebula",
    "RfN": "nebula",
    "SNR": "supernova_remnant",
    "Cl+N": "cluster_nebula",
    "**": "double_star",
}

# 仅当 OpenNGC 有可意译的英文常用名时才自译，绝不音译编号/人名。
# 值为 (中文名, 依据的 OpenNGC 英文常用名)。
SELF_TRANSLATED_ZH = {
    "M11": ("野鸭星团", "Wild Duck Cluster"),
    "M13": ("武仙座球状星团", "Hercules Globular Cluster"),
    "M17": ("天鹅星云", "Swan Nebula"),
    "M99": ("后发座风车星系", "Coma Pinwheel"),
    "NGC2070": ("蜘蛛星云", "Tarantula Nebula"),
}

CJK_RE = re.compile(r"[\u3400-\u9fff]")
CON_RE = re.compile(r"^[A-Z][A-Za-z]{2}$")
# OpenNGC 用 Se1/Se2 区分 Serpens Caput/Cauda；IAU 统一缩写为 Ser。
CONSTELLATION_FIX = {"Se1": "Ser", "Se2": "Ser"}
DESIGNATION_RE = re.compile(
    r"(梅西[耶爾]|Messier|M|NGC|IC)\s*\d+[a-zA-Z]?")


# ---------------------------------------------------------------- 源文件解析--

def _read_openngc_csv(path: Path) -> List[dict]:
    """严格解析 OpenNGC CSV（UTF-8 BOM + 分号分隔），列缺失直接报错。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh, delimiter=";")
        fields = reader.fieldnames or ()
        missing = [c for c in REQUIRED_COLUMNS if c not in fields]
        if missing:
            raise ValueError(f"{path} 缺少必需列: {missing}")
        rows: List[dict] = []
        for lineno, rec in enumerate(reader, 2):
            name = (rec.get("Name") or "").strip()
            if not name:
                raise ValueError(f"{path}:{lineno} Name 为空")
            clean = {k: (v or "").strip() for k, v in rec.items()
                     if isinstance(k, str)}
            clean["_file"] = path.name
            clean["_row"] = lineno
            rows.append(clean)
    if not rows:
        raise ValueError(f"{path} 没有数据行")
    return rows


def _index_rows(rows: List[dict]) -> Tuple[Dict[str, dict], Dict[int, dict],
                                            Dict[int, dict]]:
    by_name: Dict[str, dict] = {}
    for rec in rows:
        if rec["Name"] in by_name:
            raise ValueError(f"OpenNGC Name 重复: {rec['Name']}")
        by_name[rec["Name"]] = rec
    by_m: Dict[int, dict] = {}
    dup_by_m: Dict[int, dict] = {}
    for rec in rows:
        raw = rec.get("M", "")
        if not raw:
            continue
        number = int(raw)
        if rec["Type"] == "Dup":
            if number in dup_by_m:
                raise ValueError(f"Duplicate M{number} 多行")
            dup_by_m[number] = rec
            continue
        if number in by_m:
            raise ValueError(f"Messier {number} 在源目录出现多行")
        by_m[number] = rec
    return by_name, by_m, dup_by_m


def _row_for_messier(number: int, by_name: Dict[str, dict],
                     by_m: Dict[int, dict],
                     dup_by_m: Dict[int, dict]) -> Tuple[dict, Optional[dict]]:
    if number == 102:
        dup = dup_by_m.get(101)
        if dup is None or dup["Name"] != "M102":
            raise ValueError("OpenNGC addendum 未找到 M102 / Dup / M=101 记录")
        target = by_name.get(M102_TARGET)
        if target is None:
            raise ValueError(f"OpenNGC 未找到 M102 的约定目标 {M102_TARGET}")
        if target.get("M"):
            raise ValueError(f"{M102_TARGET} 的 M 列应为空")
        return target, dup
    row = by_m.get(number)
    if row is None:
        raise ValueError(f"OpenNGC 未找到 Messier {number}")
    return row, None


def _row_for_ngc(number: int, by_name: Dict[str, dict]) -> dict:
    for candidate in (f"NGC{number}", f"NGC{number:04d}"):
        if candidate in by_name:
            return by_name[candidate]
    raise ValueError(f"OpenNGC 未找到 NGC {number}")


def _parse_ra(text: str) -> float:
    h, m, s = text.split(":")
    return (float(h) + float(m) / 60.0 + float(s) / 3600.0) * 15.0


def _parse_dec(text: str) -> float:
    sign = -1.0 if text.startswith("-") else 1.0
    body = text.lstrip("+-")
    d, m, s = body.split(":")
    return sign * (float(d) + float(m) / 60.0 + float(s) / 3600.0)


def _number(text: str) -> Optional[float]:
    text = text.strip()
    if not text:
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _magnitude(rec: dict) -> Tuple[Optional[float], str]:
    for band, column in (("V", "V-Mag"), ("B", "B-Mag")):
        value = _number(rec.get(column, ""))
        if value is not None and value != 0.0:
            return value, band
    return None, ""


def _size(rec: dict, column: str) -> Optional[float]:
    value = _number(rec.get(column, ""))
    return value if value is not None and value > 0.0 else None


def _common_names(rec: dict) -> List[str]:
    out: List[str] = []
    for part in rec.get("Common names", "").split(","):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _designation_for_number(key: str, rec: dict) -> str:
    if key == "M102":
        return "NGC 5866"
    if key.startswith("M"):
        return f"M {key[1:]}"
    name = rec["Name"]
    prefix = "IC" if name.startswith("IC") else "NGC"
    return f"{prefix} {name[len(prefix):].lstrip('0')}"


def _alternate_designations(key: str, rec: dict) -> Tuple[str, ...]:
    if key == "M102":
        return ("NGC 5866", "NGC5866")
    if key.startswith("M"):
        return (f"M {key[1:]}", key)
    primary = _designation_for_number(key, rec)
    return (primary, primary.replace(" ", ""))


# ------------------------------------------------------------ 中文名（Wikidata）--

def _load_zh_snapshot(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    meta = payload.get("meta")
    labels = payload.get("labels")
    if not isinstance(meta, dict) or not isinstance(labels, dict):
        raise ValueError(f"{path} 不是合法的 Wikidata 中文名快照")
    if meta.get("license") != WIKIDATA_LICENSE:
        raise ValueError(f"{path} 许可不是 {WIKIDATA_LICENSE}")
    return payload


def _zh_fields(key: str, rec: dict, name_en: str, common: List[str],
               snapshot: dict) -> Tuple[str, str, str]:
    record = snapshot["labels"].get(key)
    chosen = record.get("chosen") if isinstance(record, dict) else None
    if chosen:
        label = str(chosen.get("label", "")).strip()
        qid = str(chosen.get("qid", ""))
        matched = str(chosen.get("matched", ""))
        allowed = _alternate_designations(key, rec)
        if not qid.startswith("Q") or not qid[1:].isdigit():
            raise ValueError(f"{key} Wikidata QID 非法: {qid!r}")
        if matched not in allowed:
            raise ValueError(
                f"{key} Wikidata 匹配编号 {matched!r} 不在 {allowed}")
        if not CJK_RE.search(label) or DESIGNATION_RE.fullmatch(label):
            raise ValueError(f"{key} Wikidata 标签不是中文专名: {label!r}")
        if name_en and label == name_en:
            raise ValueError(f"{key} 中文名与英文名相同: {label!r}")
        return label, "Wikidata", f"{qid} (P528={matched})"
    if key in SELF_TRANSLATED_ZH:
        label, basis = SELF_TRANSLATED_ZH[key]
        if basis not in common:
            raise ValueError(
                f"{key} 自译依据 {basis!r} 不在 OpenNGC 常用名 {common}")
        if not CJK_RE.search(label):
            raise ValueError(f"{key} 自译名不含汉字: {label!r}")
        return label, "自译", f"自译：OpenNGC 常用名 {basis}"
    return "", "", ""


# ---------------------------------------------------------------- 记录构建--

def _record(key: str, rec: dict, snapshot: dict) -> dict:
    raw_type = rec["Type"]
    if raw_type not in TYPE_MAP:
        raise ValueError(f"{key} 未知 OpenNGC 类型: {raw_type!r}")
    if raw_type == "Dup":
        raise ValueError(f"{key} 不应使用 Dup 行；请检查 M102 约定")
    constellation = CONSTELLATION_FIX.get(rec["Const"], rec["Const"])
    if not CON_RE.match(constellation):
        raise ValueError(f"{key} 星座缩写非法: {constellation!r}")

    magnitude, band = _magnitude(rec)
    size = _size(rec, "MajAx")
    minor = _size(rec, "MinAx")
    ra_deg = _parse_ra(rec["RA"])
    dec_deg = _parse_dec(rec["Dec"])
    if not (0.0 <= ra_deg < 360.0 and -90.0 <= dec_deg <= 90.0):
        raise ValueError(f"{key} 坐标越界: RA={ra_deg}, Dec={dec_deg}")

    common = _common_names(rec)
    name_en = common[0] if common else ""
    zh_name, zh_source, zh_ref = _zh_fields(key, rec, name_en, common, snapshot)

    cross = {"ngc": None, "ic": None, "m": None}
    source_name = rec["Name"]
    # 以 Name 中的编号为准。OpenNGC 的 NGC/IC 列是交叉引用，遇到 Dup 行
    # 可能与 Name 互换（如 NGC6523 行写 NGC=6533），不可作为主编号。
    if source_name.startswith("NGC"):
        cross["ngc"] = int(source_name[3:])
    elif source_name.startswith("IC"):
        cross["ic"] = int(source_name[2:])
    if key.startswith("M"):
        cross["m"] = int(key[1:])
        if rec.get("M") and int(rec["M"]) != cross["m"]:
            raise ValueError(
                f"{key} 源 M 列 {rec['M']!r} 与编号不一致")
    elif key.startswith("NGC"):
        cross["ngc"] = int(key[3:])
        if rec.get("M"):
            raise ValueError(f"{key} 的源 M 列不为空: {rec['M']!r}")
    if key == "M102":
        cross["ngc"] = 5866
        if cross != {"ngc": 5866, "ic": None, "m": 102}:
            raise ValueError(f"M102 交叉编号不符合约定: {cross}")

    return {
        "name": key,
        "name_en": name_en,
        "zh_name": zh_name,
        "zh_name_source": zh_source,
        "zh_name_ref": zh_ref,
        "aliases_en": common[1:],
        "aliases_zh": [],
        "catalog_type": raw_type,
        "dso_type": TYPE_MAP[raw_type],
        "ra_deg": ra_deg,
        "dec_deg": dec_deg,
        "magnitude": magnitude,
        "magnitude_band": band,
        "size_arcmin": size,
        "size_minor_arcmin": minor,
        "constellation": constellation,
        "cross_ids": cross,
        "source_file": rec["_file"],
        "source_row": rec["_row"],
        "source_name": rec["Name"],
    }


def _missing(objects: List[dict]) -> dict:
    def bucket(key: str) -> dict:
        absent = [o["name"] for o in objects if o.get(key) in (None, "")]
        return {"count": len(absent), "designations": absent}

    bands: Dict[str, int] = {}
    for obj in objects:
        label = obj["magnitude_band"] or "none"
        bands[label] = bands.get(label, 0) + 1
    return {
        "magnitude": bucket("magnitude"),
        "size_arcmin": bucket("size_arcmin"),
        "size_minor_arcmin": bucket("size_minor_arcmin"),
        "name_en": bucket("name_en"),
        "zh_name": bucket("zh_name"),
        "magnitude_band_counts": dict(sorted(bands.items())),
    }


def _counts(objects: List[dict], missing: dict) -> dict:
    raw: Dict[str, int] = {}
    render: Dict[str, int] = {}
    for obj in objects:
        raw[obj["catalog_type"]] = raw.get(obj["catalog_type"], 0) + 1
        render[obj["dso_type"]] = render.get(obj["dso_type"], 0) + 1
    return {
        "total": len(objects),
        "messier": sum(1 for o in objects if o["name"].startswith("M")),
        "extra_ngc": sum(1 for o in objects if o["name"].startswith("NGC")),
        "with_name_en": sum(1 for o in objects if o["name_en"]),
        "without_name_en": missing["name_en"]["count"],
        "no_name_designations": missing["name_en"]["designations"],
        "with_zh_name": sum(1 for o in objects if o["zh_name"]),
        "without_zh_name": missing["zh_name"]["count"],
        "no_zh_name_designations": missing["zh_name"]["designations"],
        "with_magnitude": sum(1 for o in objects if o["magnitude"] is not None),
        "without_magnitude": missing["magnitude"]["count"],
        "no_magnitude_designations": missing["magnitude"]["designations"],
        "with_size_arcmin": sum(
            1 for o in objects if o["size_arcmin"] is not None),
        "without_size_arcmin": missing["size_arcmin"]["count"],
        "no_size_designations": missing["size_arcmin"]["designations"],
        "with_size_minor_arcmin": sum(
            1 for o in objects if o["size_minor_arcmin"] is not None),
        "without_size_minor_arcmin": missing["size_minor_arcmin"]["count"],
        "magnitude_band_counts": missing["magnitude_band_counts"],
        "raw_type_counts": dict(sorted(raw.items())),
        "render_type_counts": dict(sorted(render.items())),
        "m102_resolved_to_ngc": 5866,
    }


def _wikidata_query(openngc: Path, zh_path: Path) -> str:
    rows: List[dict] = []
    for name in ("NGC.csv", "addendum.csv"):
        rows.extend(_read_openngc_csv(openngc / name))
    by_name, by_m, dup_by_m = _index_rows(rows)
    keys: List[Tuple[str, dict]] = []
    for number in range(1, 111):
        keys.append((f"M{number}",
                     _row_for_messier(number, by_name, by_m, dup_by_m)[0]))
    for number in EXTRAS:
        keys.append((f"NGC{number}", _row_for_ngc(number, by_name)))
    designations = set()
    for key, rec in keys:
        designations.update(_alternate_designations(key, rec))
    values = " ".join(f'"{d}"' for d in sorted(designations))
    return (
        "SELECT ?item ?cat ?zh ?zhcn ?zhhans ?zhhant ?en ?typeLabel WHERE {\n"
        f"  VALUES ?cat {{ {values} }}\n"
        "  ?item wdt:P528 ?cat .\n"
        '  OPTIONAL { ?item rdfs:label ?zh FILTER(lang(?zh)="zh") }\n'
        '  OPTIONAL { ?item rdfs:label ?zhcn FILTER(lang(?zhcn)="zh-cn") }\n'
        '  OPTIONAL { ?item rdfs:label ?zhhans FILTER(lang(?zhhans)="zh-hans") }\n'
        '  OPTIONAL { ?item rdfs:label ?zhhant FILTER(lang(?zhhant)="zh-hant") }\n'
        '  OPTIONAL { ?item rdfs:label ?en FILTER(lang(?en)="en") }\n'
        "  OPTIONAL { ?item wdt:P31 ?type . ?type rdfs:label ?typeLabel "
        'FILTER(lang(?typeLabel)="en") }\n'
        "}"
    )


ASTRONOMICAL_TYPE_RE = re.compile(
    r"galax|cluster|nebula|nebular|supernova|remnant|star cloud|asterism|"
    r"H ?II region|star formation|double star|stellar association|"
    r"astronomical (radio source|object)|spiral|elliptical|irregular|"
    r"lenticular|active galactic nucleus|quasar|blazar|planetary nebula|"
    r"open cluster|globular cluster|interstellar medium|dark nebula",
    re.I)

T2S = {
    "狀": "状", "團": "团", "雲": "云", "葉": "叶", "恆": "恒",
    "渦": "涡", "環": "环", "啞": "哑", "鈴": "铃", "風": "风",
    "闊": "阔", "邊": "边", "託": "托", "馬": "马", "獵": "猎",
    "戶": "户", "貓": "猫", "頭": "头", "鷹": "鹰", "車": "车",
}


def fetch_wikidata(openngc: Path, out_path: Path) -> dict:
    """按 P528 目录编号拉取 Wikidata 中文标签，写入可审计快照。"""
    query = _wikidata_query(openngc, out_path)
    url = WIKIDATA_URL + "?" + urllib.parse.urlencode(
        {"query": query, "format": "json"})
    request = urllib.request.Request(url, headers={
        "Accept": "application/sparql-results+json",
        "User-Agent": "MBDSDR-dso-catalog/1.0 (offline catalog build)"})
    with urllib.request.urlopen(request, timeout=240) as fh:
        data = json.load(fh)

    rows: List[dict] = []
    for name in ("NGC.csv", "addendum.csv"):
        rows.extend(_read_openngc_csv(openngc / name))
    by_name, by_m, dup_by_m = _index_rows(rows)
    keys: List[Tuple[str, dict]] = []
    for number in range(1, 111):
        keys.append((f"M{number}",
                     _row_for_messier(number, by_name, by_m, dup_by_m)[0]))
    for number in EXTRAS:
        keys.append((f"NGC{number}", _row_for_ngc(number, by_name)))
    alt_by_key = {key: _alternate_designations(key, rec) for key, rec in keys}

    items: Dict[str, dict] = {}
    for binding in data["results"]["bindings"]:
        qid = binding["item"]["value"].rsplit("/", 1)[-1]
        item = items.setdefault(
            qid, {"cats": set(), "labels": {}, "en": "", "types": set()})
        item["cats"].add(binding["cat"]["value"])
        for source, target in (("zhcn", "zh-cn"), ("zhhans", "zh-hans"),
                               ("zhhant", "zh-hant"), ("zh", "zh")):
            if binding.get(source, {}).get("value"):
                item["labels"][target] = binding[source]["value"]
        if binding.get("en", {}).get("value"):
            item["en"] = binding["en"]["value"]
        item["qid"] = qid
        if binding.get("typeLabel", {}).get("value"):
            item["types"].add(binding["typeLabel"]["value"])

    labels: Dict[str, dict] = {}
    for key, rec in keys:
        candidates = []
        chosen = None
        for designation in alt_by_key[key]:
            for item in items.values():
                if designation not in item["cats"]:
                    continue
                if not any(ASTRONOMICAL_TYPE_RE.search(t)
                           for t in item["types"]):
                    continue
                candidates.append(item)
                for lang in ("zh-hans", "zh-cn", "zh", "zh-hant"):
                    text = item["labels"].get(lang, "")
                    if (text and CJK_RE.search(text)
                            and not DESIGNATION_RE.fullmatch(text)
                            and chosen is None):
                        chosen = {
                            "qid": item.get("qid", ""),
                            "label": "".join(T2S.get(c, c) for c in text),
                            "label_raw": text,
                            "lang": lang,
                            "en": item["en"],
                            "types": sorted(item["types"]),
                            "matched": designation,
                        }
        labels[key] = {
            "designation": alt_by_key[key][0],
            "chosen": chosen,
            "candidates": [
                {"qid": it.get("qid", ""), "cats": sorted(it["cats"]),
                 "labels": it["labels"], "en": it["en"],
                 "types": sorted(it["types"])}
                for it in candidates],
        }

    snapshot = {
        "meta": {
            "source": "Wikidata",
            "source_url": WIKIDATA_URL,
            "license": WIKIDATA_LICENSE,
            "license_url": WIKIDATA_LICENSE_URL,
            "fetched_at": _dt.datetime.now(
                _dt.timezone.utc).isoformat(timespec="seconds"),
            "query": query,
            "note": ("Messier/NGC/IC objects matched by Wikidata P528 catalog "
                     "number; only labels in zh/zh-cn/zh-hans/zh-hant "
                     "containing CJK and not equal to a bare designation are "
                     "kept. label is a character-level traditional->simplified "
                     "normalization of label_raw."),
        },
        "labels": labels,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, snapshot)
    return snapshot


# ---------------------------------------------------------------- 组装--

def build(opengnc: Path, zh_path: Path) -> dict:
    rows: List[dict] = []
    for name in ("NGC.csv", "addendum.csv"):
        rows.extend(_read_openngc_csv(opengnc / name))
    by_name, by_m, dup_by_m = _index_rows(rows)
    snapshot = _load_zh_snapshot(zh_path)

    objects: List[dict] = []
    for number in range(1, 111):
        row, dup = _row_for_messier(number, by_name, by_m, dup_by_m)
        key = f"M{number}"
        objects.append(_record(key, row, snapshot))
        if number == 102 and dup is not None:
            if dup["Type"] != "Dup" or dup["M"] != "101":
                raise ValueError(f"M102 源记录不符合约定: {dup}")
    for number in EXTRAS:
        objects.append(_record(f"NGC{number}", _row_for_ngc(number, by_name),
                               snapshot))

    missing = _missing(objects)
    counts = _counts(objects, missing)

    meta = {
        "schema": "mbdsdr.dso_catalog.v2",
        "catalog": "Messier 1-110 + 7 bright NGC",
        "source": "OpenNGC",
        "source_url": SOURCE_URL,
        "source_license": SOURCE_LICENSE,
        "source_license_url": SOURCE_LICENSE_URL,
        "attribution": (
            "Deep-sky object data from OpenNGC (https://github.com/"
            "mattiaverga/OpenNGC), by Mattia Verga and contributors, "
            "licensed CC BY-SA 4.0. Chinese names are Wikidata labels "
            "(CC0 1.0) matched by P528 catalog number; entries without a "
            "Wikidata label are marked 自译 or left empty. This derived "
            "catalog is distributed under CC BY-SA 4.0."),
        "source_dir": SOURCE_DIR_LABEL,
        "source_files": {
            "ngc": {
                "path": "NGC.csv",
                "url": NGC_URL,
                "sha256": sha256_file(opengnc / "NGC.csv"),
                "bytes": (opengnc / "NGC.csv").stat().st_size,
                "data_rows": sum(
                    1 for r in rows if r["_file"] == "NGC.csv"),
            },
            "addendum": {
                "path": "addendum.csv",
                "url": ADDENDUM_URL,
                "sha256": sha256_file(opengnc / "addendum.csv"),
                "bytes": (opengnc / "addendum.csv").stat().st_size,
                "data_rows": sum(
                    1 for r in rows if r["_file"] == "addendum.csv"),
            },
            "zh_labels": {
                "path": "mbdsdr_ai/data/dso_zh_wikidata.json",
                "source": "Wikidata",
                "url": WIKIDATA_URL,
                "license": WIKIDATA_LICENSE,
                "sha256": sha256_file(zh_path),
                "fetched_at": snapshot["meta"].get("fetched_at", ""),
            },
        },
        "constellation_method": (
            "OpenNGC Const 列（IAU 三字母缩写），不做星座边界查表；"
            "Se1/Se2（Serpens Caput/Cauda）合并为 IAU 标准缩写 Ser。"),
        "name_policy": (
            "英文常用名只取 OpenNGC Common names 列（逗号分隔），不补造；"
            "缺失留空并计数。中文名只取 Wikidata zh/zh-cn/zh-hans/zh-hant "
            "标签（P528 编号匹配），拒绝纯编号标签；取不到且英文常用名可"
            "意译者自译并标注 zh_name_source=自译；仍取不到留空并计数。"
            "中文名不取自任何非 Wikidata/自译来源。"),
        "zh_name_policy": (
            "Wikidata 标签按字符级繁→简转写，不改词；自译仅限语义明确的"
            "英文常用名，不做音译；无专名留空。"),
        "type_mapping": dict(sorted(TYPE_MAP.items())),
        "counts": counts,
        "missing": missing,
        "m102_convention": {
            "designation": "M102",
            "openngc_record": M102_DUP_NOTE,
            "resolved_to": "NGC 5866",
            "resolution": (
                "坐标/星等/角径/类型全部取自 NGC.csv 的 NGC5866 行，"
                "不复用 addendum 的 Dup 行"),
            "entry_count": 117,
            "messier_count": 110,
            "note": (
                "OpenNGC 将 M102 记为重复条目（Dup，M=101）；本项目按历史"
                "约定取 NGC 5866，故条目数保持 117。禁止改成 116 条。"),
        },
        "notes": [
            "M40（Winnecke 4）在 OpenNGC 为双星 **，无 MajAx/MinAx，"
            "尺寸留空；常用名列为空，故 name_en/zh_name 为空。",
            "M73（NGC6994）在 OpenNGC 为 Other（星群），无尺寸；渲染类型"
            "按 cluster 处理，映射见 type_mapping。",
            "星等优先 V-Mag，缺失时用 B-Mag 并以 magnitude_band 标注波段；"
            "两者皆缺写 null。",
            "cross_ids 取 OpenNGC Name 中的编号；NGC/IC 列仅作交叉引用，"
            "在 Dup 行附近可能与 Name 互换，故不采用。",
        ],
        "generated_at": _dt.datetime.now(
            _dt.timezone.utc).isoformat(timespec="seconds"),
        "generator": "mbdsdr_ai.astronomy.build_dso_catalog",
        "reproduction": {
            "commands": [
                "curl -L -o reference/openngc/NGC.csv " + NGC_URL,
                "curl -L -o reference/openngc/addendum.csv " + ADDENDUM_URL,
                "python -m mbdsdr_ai.astronomy.build_dso_catalog --fetch-zh",
                "python -m mbdsdr_ai.astronomy.build_dso_catalog "
                "--opengnc reference/openngc",
            ],
            "wikidata_query": snapshot["meta"].get("query", ""),
        },
    }
    payload = {"meta": meta, "objects": objects}
    meta["data_sha256"] = canonical_sha256(payload)
    return payload


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opengnc", default=str(DEFAULT_OPENNGC),
                        help="OpenNGC NGC.csv/addendum.csv 所在目录")
    parser.add_argument("--zh-labels", default=str(ZH_LABELS_PATH),
                        help="Wikidata 中文名快照 JSON")
    parser.add_argument("--out", default=str(OUT_PATH),
                        help="输出 dso_catalog.json")
    parser.add_argument("--fetch-zh", action="store_true",
                        help="联网拉取 Wikidata 中文标签并写快照后退出")
    args = parser.parse_args(argv)

    opengnc = Path(args.opengnc)
    zh_path = Path(args.zh_labels)
    if args.fetch_zh:
        snapshot = fetch_wikidata(opengnc, zh_path)
        print(f"Wikidata 快照已写入 {zh_path}："
              f"{sum(1 for r in snapshot['labels'].values() if r['chosen'])}"
              f"/{len(snapshot['labels'])} 条有中文标签")
        return 0
    payload = build(opengnc, zh_path)
    write_json(Path(args.out), payload)
    print(f"已写入 {args.out}：{len(payload['objects'])} 条，"
          f"中文名 {payload['meta']['counts']['with_zh_name']} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
