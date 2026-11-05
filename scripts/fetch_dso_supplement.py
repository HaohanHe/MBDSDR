#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""补齐 ``dso_catalog.json`` 的缺字段：从公开星表抓取 → 固定快照。

为什么需要这个脚本
------------------
OpenNGC（CC BY-SA 4.0）是 ``dso_catalog.json`` 的主来源，但它的原始行
对这些天体缺字段：

* 7 个天体 ``V-Mag``/``B-Mag`` 两列皆空：M22 / M35 / M62 / M69 / M70 /
  M71 / M107；
* 2 个「退化 DSO」没有 ``MajAx``/``MinAx``：M40（Winnecke 4 双星）、
  M73（NGC 6994 星群）。

本脚本只从**公开星表**取值，逐字段记出处，不做任何插值/估算：

* 星等 7 条 ← HEASARC ``MESSIER`` 表（该表据 Sky Catalogue 2000.0
  Vol. 2 表 III 编成，bibcode ``1985skca.book.....H``）；
* M40 角径 ← USNO ``WDS``（华盛顿双星表）最近一次观测的双星分离；
* M73 角径 ← Lynga 开放星团表 ``VII/92A`` 的 ``angular_diameter``。

快照必须**入库固定版本**（``mbdsdr_ai/data/dso_supplement.json``），
消费方不得运行时联网。

用法
----
    python scripts/fetch_dso_supplement.py            # 联网重建快照
    python scripts/fetch_dso_supplement.py --check    # 离线校验现有快照

``--check`` 校验：schema、payload SHA256、7 条星等齐全、2 条角径齐全，
以及每条记录的来源 id 都在 ``meta.sources`` 里。任一不符即退出码 1。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = REPO_ROOT / "mbdsdr_ai" / "data" / "dso_supplement.json"

UA = "MBDSDR-dso-supplement/1.0 (offline catalog build)"
HEASARC_TAP = "https://heasarc.gsfc.nasa.gov/xamin/vo/tap/sync"

MAG_TARGETS = ("M 22", "M 35", "M 62", "M 69", "M 70", "M 71", "M 107")
WDS_DESIGNATION = "12222+5805"          # = M40 / Winnecke 4
LYNGA_DESIGNATION = "NGC 6994"          # = M73

SOURCES = {
    "heasarc_messier": {
        "catalog": "HEASARC W3Browse MESSIER",
        "compiled_from": ("Sky Catalogue 2000.0, Vol. 2 (Hirshfeld & Sinnott "
                          "1985), Table III"),
        "bibcodes": ["1781cote.rept..227M", "1985skca.book.....H"],
        "archive": "NASA HEASARC",
        "url": HEASARC_TAP,
        "table": "messier",
        "fields_used": ["vmag"],
        "terms": ("公开天文档案，按 HEASARC 惯例引用编目来源；"
                  "数值为事实性数据，不做再分发许可声明之外的限制"),
    },
    "wds": {
        "catalog": "Washington Double Star Catalog (WDS)",
        "maintainer": "U.S. Naval Observatory",
        "archive": "NASA HEASARC 镜像",
        "url": HEASARC_TAP,
        "table": "wds",
        "fields_used": ["separation_date2"],
        "terms": ("USNO 公开编目；使用时致谢："
                  "“This research has made use of the Washington Double "
                  "Star Catalog maintained at the U.S. Naval Observatory.”"),
    },
    "lynga": {
        "catalog": "Lynga, Catalogue of Open Cluster Data (3rd ed.)",
        "bibcode": "1987CoORo...16....1L",
        "archive": "CDS VII/92A（经 HEASARC lyngaclust 表）",
        "url": HEASARC_TAP,
        "table": "lyngaclust",
        "fields_used": ["angular_diameter"],
        "terms": "公开出版星表；数值为事实性数据，按编目引用",
    },
}


def _canonical_sha256(payload) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _tap(adql: str) -> list:
    """查询 HEASARC TAP，返回去表头的 ``|`` 分隔行列表。"""
    url = HEASARC_TAP + "?" + urllib.parse.urlencode(
        {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "text",
         "QUERY": adql})
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=180) as fh:
        body = fh.read().decode("utf-8", "replace")
    lines = [ln for ln in body.splitlines() if "|" in ln]
    if not lines:
        raise RuntimeError(f"TAP 查询无数据行: {adql}")
    header = [c.strip() for c in lines[0].split("|")]
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("|")]
        if len(cells) == len(header):
            rows.append(dict(zip(header, cells)))
    return rows


def fetch() -> dict:
    objects = {}

    mag_query = ("SELECT name, alt_name, object_type, dimension, vmag "
                 "FROM messier WHERE name IN (%s)"
                 % ", ".join("'%s'" % n for n in MAG_TARGETS))
    rows = {r["name"]: r for r in _tap(mag_query)}
    missing = [n for n in MAG_TARGETS if n not in rows]
    if missing:
        raise RuntimeError(f"HEASARC MESSIER 缺目标: {missing}")
    for name in MAG_TARGETS:
        row = rows[name]
        key = "M" + name.split()[1]
        raw = row["vmag"]
        if raw in ("", "null"):
            raise RuntimeError(f"{name} 在 HEASARC MESSIER 无 vmag")
        value = float(raw)
        if not (0.0 < value < 30.0):
            raise RuntimeError(f"{name} vmag 越界: {value}")
        objects[key] = {
            "magnitude": value,
            "magnitude_band": "V",
            "source_id": "heasarc_messier",
            "catalog_designation": name,
            "catalog_row": row,
            "derivation": "vmag 列直接取值（V 波段）",
        }

    wds_query = ("SELECT wds, discoverer, ra, dec, first_date, last_date, "
                 "number_obs, pa_date1, pa_date2, separation_date1, "
                 "separation_date2, comp1_mag, comp2_mag FROM wds WHERE "
                 "wds = '%s'" % WDS_DESIGNATION)
    wds_rows = _tap(wds_query)
    if len(wds_rows) != 1:
        raise RuntimeError(
            f"WDS {WDS_DESIGNATION} 命中 {len(wds_rows)} 行（应为 1）")
    wrow = wds_rows[0]
    sep = wrow["separation_date2"]
    if sep in ("", "null"):
        raise RuntimeError("WDS 最近一次观测无 separation_date2")
    sep_arcsec = float(sep)
    if not (0.0 < sep_arcsec < 600.0):
        raise RuntimeError(f"WDS 分离角越界: {sep_arcsec}")
    objects["M40"] = {
        "size_arcmin": round(sep_arcsec / 60.0, 3),
        "source_id": "wds",
        "catalog_designation": "WDS J12222+5805",
        "catalog_row": wrow,
        "derivation": (f"最近一次观测分离 {sep_arcsec} arcsec "
                       f"（{wrow['last_date']} 年）/ 60 = "
                       f"{round(sep_arcsec / 60.0, 3)} arcmin"),
    }

    lynga_query = ("SELECT name, alt_name, ra, dec, angular_diameter, "
                   "ref_angular_diameter, type_flag FROM lyngaclust WHERE "
                   "name = '%s'" % LYNGA_DESIGNATION)
    ly_rows = _tap(lynga_query)
    if len(ly_rows) != 1:
        raise RuntimeError(
            f"Lynga {LYNGA_DESIGNATION} 命中 {len(ly_rows)} 行（应为 1）")
    lrow = ly_rows[0]
    raw = lrow["angular_diameter"]
    if raw in ("", "null"):
        raise RuntimeError("Lynga NGC 6994 无 angular_diameter")
    diameter = float(raw)
    if not (0.0 < diameter < 600.0):
        raise RuntimeError(f"Lynga 角径越界: {diameter}")
    objects["M73"] = {
        "size_arcmin": diameter,
        "source_id": "lynga",
        "catalog_designation": LYNGA_DESIGNATION,
        "catalog_row": lrow,
        "derivation": "angular_diameter 列直接取值（arcmin）",
    }

    payload = {"objects": objects}
    meta = {
        "schema": "mbdsdr.dso_supplement.v1",
        "purpose": ("补齐 OpenNGC 缺字段：7 条 V 星等 + M40/M73 角径。"
                    "只填 OpenNGC 为 null 的字段；已有值不覆盖。"),
        "fetched_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "queries": {
            "magnitude": mag_query,
            "m40_size": wds_query,
            "m73_size": lynga_query,
        },
        "sources": SOURCES,
        "payload_sha256": _canonical_sha256(objects),
        "payload_sha256_scope": "objects（canonical JSON：sort_keys、"
                                "无空格、UTF-8）",
        "object_count": len(objects),
        "note": ("本快照只存事实性取值与出处行，不含任何插值或估算；"
                 "消费方必须离线读取本文件，禁止运行时联网。"),
    }
    return {"meta": meta, "objects": objects}


def check(path: Path) -> int:
    if not path.exists():
        print(f"快照不存在: {path}", file=sys.stderr)
        return 1
    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = payload.get("meta") or {}
    objects = payload.get("objects") or {}
    problems = []
    if meta.get("schema") != "mbdsdr.dso_supplement.v1":
        problems.append(f"schema 非法: {meta.get('schema')!r}")
    if meta.get("payload_sha256") != _canonical_sha256(objects):
        problems.append("payload_sha256 与 objects 不一致")
    mags = [k for k, v in objects.items() if "magnitude" in v]
    sizes = [k for k, v in objects.items() if "size_arcmin" in v]
    if sorted(mags) != ["M107", "M22", "M35", "M62", "M69", "M70", "M71"]:
        problems.append(f"星等条目不齐: {sorted(mags)}")
    if sorted(sizes) != ["M40", "M73"]:
        problems.append(f"角径条目不齐: {sorted(sizes)}")
    known = set((meta.get("sources") or {}))
    for key, rec in objects.items():
        if rec.get("source_id") not in known:
            problems.append(f"{key} source_id 不在 meta.sources: "
                            f"{rec.get('source_id')!r}")
        if not rec.get("catalog_row"):
            problems.append(f"{key} 缺 catalog_row（无出处行）")
        if not rec.get("derivation"):
            problems.append(f"{key} 缺 derivation")
    if problems:
        for p in problems:
            print(f"CHECK FAILED: {p}", file=sys.stderr)
        return 1
    print(f"CHECK OK: {len(objects)} 条补字段，payload_sha256="
          f"{meta['payload_sha256']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="只校验现有快照，不联网、不写文件")
    parser.add_argument("--out", default=str(SNAPSHOT))
    args = parser.parse_args(argv)
    out = Path(args.out)
    if args.check:
        return check(out)
    payload = fetch()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"快照已写入 {out}：{payload['meta']['object_count']} 条，"
          f"payload_sha256={payload['meta']['payload_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
