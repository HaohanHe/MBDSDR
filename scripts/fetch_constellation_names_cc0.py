#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""抓取 IAU 88 星座的 Wikidata 中文标签（CC0）→ 固定快照。

为什么需要这个脚本
------------------
星座连线来自 Stellarium ``modern_iau``（CC BY-SA 4.0，保留并署名）；但
88 座里有 61 个中文名原本取自 Stellarium 的 ``zh_CN.po`` 译文
（GPL-2.0-or-later）。B1 决策只替换这 61 个名称，来源改为 Wikidata 标签
（CC0-1.0），连线零改动。

快照必须**入库固定版本**（``mbdsdr_ai/data/constellation_names_cc0.json``），
消费方不得运行时联网。本脚本只用于人工重新生成快照；生成后请把
``meta.fetched_at``、``meta.payload_sha256`` 与文件 SHA-256 一起更新到
``docs/constellations.md`` / ``LICENSING.md``。

逐座对拍（不许一键全换）
------------------------
脚本会逐个比较「快照里转写后的中文名」与「当前 ``constellations_iau.json``
的 ``zh_name``」：
* 完全一致 → 可替换来源；
* 仅繁体/简体字形不同（逐字转写后一致）→ 视为同一文案，保留原译名，
  只换来源；
* 任何词级差异 → 脚本直接失败并列出，需要人工判断。若判断为「原译名
  更好」，保留原译名并把理由写进 ``docs/constellations.md``。

用法
----
    python scripts/fetch_constellation_names_cc0.py
    python scripts/fetch_constellation_names_cc0.py --check

``--check`` 不写文件，只校验现有快照与当前数据是否仍一致（离线可跑）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "mbdsdr_ai" / "data" / "constellations_iau.json"
SNAPSHOT = REPO_ROOT / "mbdsdr_ai" / "data" / "constellation_names_cc0.json"

UA = {"User-Agent": "MBDSDR-license-audit/1.0 (constellation name provenance)"}

#: Wikidata 的「星座」类别（P31=Q8928）。
CONSTELLATION_CLASS = "Q8928"

#: 黄道两座在 Wikidata 未挂 P31=Q8928，按 wbsearchentities 结果显式指定。
EXTRA_QIDS = {"Ari": "Q10584", "Cnc": "Q8849"}

#: 只做逐字简繁转写（不改词）。键为 Wikidata 原标签里的字。
HANT_TO_HANS = {
    "鷹": "鹰", "劍": "剑", "獅": "狮", "魚": "鱼", "鵑": "鹃",
    "寶": "宝", "獵": "猎", "戶": "户",
}


def _get(url: str) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def canonical_sha256(payload) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _sparql_items() -> dict[str, str]:
    """返回 {英文标签: QID}，覆盖 P31=Q8928 的星座条目。"""
    query = ("SELECT ?item ?en WHERE {"
             f" ?item wdt:P31 wd:{CONSTELLATION_CLASS} ."
             " ?item rdfs:label ?en FILTER(lang(?en)='en') }")
    url = ("https://query.wikidata.org/sparql?format=json&query="
           + urllib.parse.quote(query))
    out = {}
    for row in _get(url)["results"]["bindings"]:
        out[row["en"]["value"]] = row["item"]["value"].rsplit("/", 1)[-1]
    return out


def _labels(qids: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(qids), 50):
        batch = qids[i:i + 50]
        url = ("https://www.wikidata.org/w/api.php?action=wbgetentities"
               "&props=labels&languages=zh|zh-hans|zh-cn|zh-tw|en"
               "&format=json&ids=" + "|".join(batch))
        for qid, ent in _get(url)["entities"].items():
            labs = ent.get("labels", {})
            out[qid] = {k: v.get("value") for k, v in labs.items()}
    return out


def fetch_names(catalog: dict) -> tuple[dict, list[tuple]]:
    by_en = _sparql_items()
    qid_by_abbr = dict(EXTRA_QIDS)
    for con in catalog["constellations"]:
        qid = by_en.get(con["latin_name"])
        if qid:
            qid_by_abbr[con["abbr"]] = qid
    missing = [c["abbr"] for c in catalog["constellations"]
               if c["abbr"] not in qid_by_abbr]
    if missing:
        raise SystemExit(f"无法在 Wikidata 定位星座: {missing}")
    labs = _labels(sorted(set(qid_by_abbr.values())))

    names = {}
    raw_diffs = []
    for con in catalog["constellations"]:
        abbr = con["abbr"]
        qid = qid_by_abbr[abbr]
        lab = labs[qid]
        raw = lab.get("zh") or lab.get("zh-hans") or lab.get("zh-cn")
        if not raw:
            raise SystemExit(f"{abbr} ({qid}) 缺中文标签")
        zh = raw
        used = []
        for hant, hans in HANT_TO_HANS.items():
            if hant in zh:
                zh = zh.replace(hant, hans)
                used.append(f"{hant}→{hans}")
        if zh != con["zh_name"]:
            raise SystemExit(
                f"{abbr}: 转写后 {zh!r} != 当前 {con['zh_name']!r}（需人工判断，"
                "不许一键全换）")
        entry = {"qid": qid, "zh": zh}
        if raw != zh:
            entry["raw"] = raw
            entry["transcribed"] = " ".join(used)
            raw_diffs.append((abbr, con["zh_name"], raw, " ".join(used)))
        names[abbr] = entry
    return names, raw_diffs


def build_snapshot(names: dict, raw_diffs: list) -> dict:
    return {
        "meta": {
            "schema": "mbdsdr.constellation_names_cc0.v1",
            "system_id": "iau_88",
            "source": "Wikidata",
            "source_url": "https://www.wikidata.org/",
            "license": "CC0-1.0",
            "license_url": (
                "https://creativecommons.org/publicdomain/zero/1.0/"),
            "fetched_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "query": (
                "SPARQL P31=Q8928 (constellation) + wbgetentities labels "
                "zh|zh-hans|zh-cn|zh-tw|en; Aries Q10584 / Cancer Q8849 "
                "explicitly mapped (their zodiac items lack P31=Q8928)."),
            "matching": (
                "Every IAU latin_name must resolve to a Wikidata item; "
                "failure aborts instead of guessing."),
            "transcription": (
                "raw is verbatim Wikidata; zh only applies per-character "
                "traditional→simplified transcription (鷹→鹰 劍→剑 獅→狮 "
                "魚→鱼 鵑→鹃 寶→宝 獵→猎 戶→户). Word-level rewriting is "
                "never performed."),
            "count": len(names),
            "raw_diff_count": len(raw_diffs),
            "payload_sha256": canonical_sha256(names),
            "payload_sha256_scope": (
                "names{} canonical JSON: ensure_ascii=False, sort_keys=True, "
                "separators=(',',':'), UTF-8"),
            "note": (
                "Fixed local snapshot for license decision B1. Runtime "
                "consumers must read this file and must not fetch Wikidata."),
        },
        "names": names,
    }


def check() -> int:
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    if canonical_sha256(snap["names"]) != snap["meta"]["payload_sha256"]:
        print("RED: snapshot payload sha256 mismatch")
        return 1
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    bad = []
    for con in catalog["constellations"]:
        if "constellation_names_cc0" not in con.get("zh_source", ""):
            continue
        e = snap["names"].get(con["abbr"])
        if not e or e["zh"] != con["zh_name"]:
            bad.append(con["abbr"])
    if bad:
        print("RED: snapshot/catalog name mismatch:", bad)
        return 1
    print(f"OK: {len(snap['names'])} names, payload "
          f"{snap['meta']['payload_sha256']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="离线校验现有快照与数据一致性")
    args = ap.parse_args(argv)
    if args.check:
        return check()

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    names, raw_diffs = fetch_names(catalog)
    snapshot = build_snapshot(names, raw_diffs)
    SNAPSHOT.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    file_sha = hashlib.sha256(SNAPSHOT.read_bytes()).hexdigest()
    print(f"wrote {SNAPSHOT}")
    print(f"file_sha256: {file_sha}")
    print(f"payload_sha256: {snapshot['meta']['payload_sha256']}")
    print(f"names: {len(names)}  raw_diff: {len(raw_diffs)}")
    for abbr, old, raw, conv in raw_diffs:
        print(f"  {abbr}\t{old}\t{raw}\t{conv}\t保留原译名（仅字形转写，等价）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
