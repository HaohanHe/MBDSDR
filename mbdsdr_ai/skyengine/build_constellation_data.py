# SPDX-License-Identifier: MIT
"""
build_constellation_data.py — 从本地 Stellarium 天空文化数据生成两份星座数据
=========================================================================

产物（均为可追溯的本地生成数据，不含任何凭记忆手写的星点）:

1. ``mbdsdr_ai/data/constellations_iau.json``
   - 88 个 IAU 星座：官方拉丁全名、三字母缩写、中文名、连线。
   - 连线来源 = Stellarium ``modern_iau`` 天空文化（CC BY-SA 4.0），
     其图形由 Stellarium 与 Sky & Telescope 合作制作并发布于 IAU 网站。
   - **星座连线不是 IAU 官方定义**：IAU 只规定 88 个星座名称与边界，
     图形属于传统/民间约定。meta 中显式写明 ``lines_are_iau_official=false``。

2. ``mbdsdr_ai/data/xingguan_chinese.json``
   - 中国星官最小可用集：三垣（紫微/太微/天市）+ 二十八宿，共 31 个区域；
     每个区域含所含星官名、拼音/英文名与恒星成员。
   - 恒星成员来源 = Stellarium ``chinese`` 天空文化的 ``star_names.zh_CN.fab``
     （CC BY-SA 4.0），按 HIP 精确映射到 HYG v4.1；映射不到者留空并计数，
     绝不做名称猜测或凭记忆补星。
   - 绘制连线来自同一天空文化的 ``index.json``。

数据源（本机）:
  - ``reference/stellarium/skycultures/modern_iau/index.json``
  - ``reference/stellarium/skycultures/chinese/index.json``
  - ``reference/stellarium/skycultures/chinese/star_names.zh_CN.fab``
  - ``reference/stellarium/skycultures/chinese/description.md``
  - ``reference/stellarium/po/stellarium/zh_CN.po``（IAU 星座中文名）
  - ``reference/stellarium/po/stellarium-skycultures/zh_CN.po``（补充中文名）
  - ``scratch/hygdata_v41.csv``（HYG v4.1，用于 HIP→HYG 精确映射）

用法::

    python -m mbdsdr_ai.skyengine.build_constellation_data \
        --stellarium D:/mbdsdr/reference/stellarium \
        --hyg-csv D:/mbdsdr/scratch/hygdata_v41.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

HERE = Path(os.path.abspath(__file__))
PKG_ROOT = HERE.parent.parent
DATA_DIR = PKG_ROOT / "data"
DEFAULT_STELLARIUM = Path(r"D:\mbdsdr\reference\stellarium")
DEFAULT_HYG_CSV = Path(r"D:\mbdsdr\scratch\hygdata_v41.csv")
DEFAULT_NPZ = DATA_DIR / "full_sky_hyg_v41.npz"

IAU_OUT = DATA_DIR / "constellations_iau.json"
XINGGUAN_OUT = DATA_DIR / "xingguan_chinese.json"

XIU = ["角宿", "亢宿", "氐宿", "房宿", "心宿", "尾宿", "箕宿",
       "井宿", "鬼宿", "柳宿", "星宿", "张宿", "翼宿", "轸宿",
       "奎宿", "娄宿", "胃宿", "昴宿", "毕宿", "觜宿", "参宿",
       "斗宿", "牛宿", "女宿", "虚宿", "危宿", "室宿", "壁宿"]
YUAN = ["紫微垣", "太微垣", "天市垣"]
YUAN_WALLS = {
    "紫微垣": ["紫微右垣", "紫微左垣"],
    "太微垣": ["太微右垣", "太微左垣"],
    "天市垣": ["天市右垣", "天市左垣"],
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha256(payload: dict) -> str:
    """规范化 JSON SHA-256（meta.data_sha256 不参与计算）。"""
    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(clone.get("meta"), dict):
        clone["meta"].pop("data_sha256", None)
    blob = json.dumps(clone, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _norm_text(text: str) -> str:
    """去变音符号、括号注解、*? 标记，供名称对齐（不用于星点映射）。"""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[（(\[【].*?[）)\]】]", "", text)
    text = text.replace("*", "").replace("?", "").strip()
    return text


def _clean_name(text: str) -> str:
    text = re.sub(r"[（(\[【].*?[）)\]】]", "", text)
    return text.replace("*", "").replace("?", "").strip()


def load_hyg(path: Path) -> Dict[int, dict]:
    rows: Dict[int, dict] = {}
    with open(path, "r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                hip = int(row["hip"])
            except (TypeError, ValueError):
                continue
            if hip <= 0:
                continue
            rows[hip] = row
    if not rows:
        raise SystemExit(f"HYG 源表为空或无法解析: {path}")
    return rows


def load_catalog_ids(path: Path) -> set:
    npz = np.load(path, allow_pickle=False)
    return {int(x) for x in npz["hyg_id"]}


_QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _po_msgstr(block: str) -> Optional[str]:
    out = []
    for line in block.splitlines():
        if line.startswith("msgstr"):
            out.append(line.split(" ", 1)[1] if " " in line else "")
        elif out and line.startswith('"'):
            out.append(line)
    if not out:
        return None
    text = "".join(out)
    parts = _QUOTED.findall(text)
    return "".join(parts) if parts else text.strip('"')


def load_po_iau_names(path: Path) -> Dict[str, str]:
    names: Dict[str, str] = {}
    if not path.exists():
        return names
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        if 'msgctxt "IAU constellation name"' not in block:
            continue
        mi = re.search(r'msgid "((?:[^"\\]|\\.)*)"', block)
        ms = _po_msgstr(block)
        if mi and ms:
            names.setdefault(mi.group(1), ms)
    return names


def star_ref(hip: int, row: dict, catalog_ids: set) -> dict:
    return {
        "hip": int(hip),
        "hyg_id": int(row["id"]),
        "proper": row["proper"] or "",
        "bayer": row["bf"] or "",
        "ra_deg": round(float(row["ra"]) * 15.0, 6),
        "dec_deg": round(float(row["dec"]), 6),
        "vmag": round(float(row["mag"]), 3),
        "con": row["con"] or "",
        "in_catalog": int(row["id"]) in catalog_ids,
    }


def build_iau(stellarium: Path, hyg: Dict[int, dict], catalog_ids: set) -> dict:
    src = stellarium / "skycultures" / "modern_iau" / "index.json"
    raw = json.loads(src.read_text(encoding="utf-8"))

    po_main = load_po_iau_names(stellarium / "po" / "stellarium" / "zh_CN.po")
    po_sky = load_po_iau_names(
        stellarium / "po" / "stellarium-skycultures" / "zh_CN.po")
    zh_po: Dict[str, str] = {}
    for k, v in po_sky.items():
        zh_po[_norm_text(k).casefold()] = v
    for k, v in po_main.items():
        zh_po[_norm_text(k).casefold()] = v

    culture_path = DATA_DIR / "constellation_culture.json"
    culture = json.loads(culture_path.read_text(encoding="utf-8"))
    culture = {k: v for k, v in culture.items() if not k.startswith("_")}

    constellations = []
    unresolved_all = []
    dropped_all = 0
    for item in raw["constellations"]:
        m = re.match(r"CON\s+\S+\s+(\S+)$", item["id"])
        if not m:
            raise ValueError(f"无法解析 modern_iau id: {item['id']!r}")
        abbr = m.group(1)
        latin = item["common_name"]["native"]
        english = item["common_name"].get("english") or latin

        name_zh = ""
        zh_source = ""
        if abbr in culture and culture[abbr].get("zh"):
            name_zh = culture[abbr]["zh"]
            zh_source = "data/constellation_culture.json"
        else:
            key = _norm_text(latin).casefold()
            name_zh = zh_po.get(key, "")
            zh_source = "Stellarium zh_CN.po (IAU constellation name)"
        if not name_zh:
            raise ValueError(f"{abbr} ({latin}) 缺中文名，拒绝编造")

        raw_lines = item.get("lines") or []
        lines: List[List[dict]] = []
        unresolved_here = []
        dropped = 0
        for poly in raw_lines:
            run: List[dict] = []
            for h in poly:
                if not isinstance(h, int):
                    h = int(h)
                row = hyg.get(h)
                if row is None:
                    unresolved_here.append(h)
                    if len(run) >= 2:
                        lines.append(run)
                    run = []
                    continue
                run.append(star_ref(h, row, catalog_ids))
            if len(run) >= 2:
                lines.append(run)
        original_segments = sum(max(0, len(p) - 1) for p in raw_lines)
        kept_segments = sum(max(0, len(p) - 1) for p in lines)
        dropped = original_segments - kept_segments

        unique = {}
        for poly in lines:
            for ref in poly:
                unique.setdefault(ref["hyg_id"], ref)
        stars = sorted(unique.values(), key=lambda r: (r["vmag"], r["hyg_id"]))
        anchor = stars[0]["hyg_id"] if stars else None

        constellations.append({
            "abbr": abbr,
            "latin_name": latin,
            "english_name": english,
            "zh_name": name_zh,
            "zh_source": zh_source,
            "lines": lines,
            "stars": stars,
            "star_count": len(stars),
            "segment_count": kept_segments,
            "original_segment_count": original_segments,
            "dropped_segment_count": dropped,
            "unresolved_hips": sorted(set(unresolved_here)),
            "anchor_hyg_id": anchor,
        })
        unresolved_all.extend(unresolved_here)
        dropped_all += dropped

    constellations.sort(key=lambda c: c["abbr"])
    abbrs = [c["abbr"] for c in constellations]
    if len(abbrs) != 88 or len(set(abbrs)) != 88:
        raise ValueError(f"IAU 数量/唯一性异常: {len(abbrs)} / {len(set(abbrs))}")

    meta = {
        "schema": "mbdsdr.iau_constellations.v1",
        "system_id": "iau_88",
        "system_name_zh": "IAU 88 星座",
        "system_name_en": "IAU 88 Constellations",
        "source": "Stellarium skyculture modern_iau",
        "source_url": ("https://github.com/Stellarium/stellarium/tree/"
                       "master/skycultures/modern_iau"),
        "source_license": "CC BY-SA 4.0",
        "source_license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": (
            "Constellation line figures from Stellarium's modern_iau sky "
            "culture (CC BY-SA 4.0), produced in collaboration with Sky & "
            "Telescope (Roger Sinnott, Rick Fienberg) and published on the "
            "IAU website. IAU itself defines 88 constellation names and "
            "boundaries, not stick figures."),
        "source_file": "reference/stellarium/skycultures/modern_iau/index.json",
        "source_file_sha256": sha256_file(src),
        "name_source": ("Stellarium zh_CN translation "
                        "(po/stellarium/zh_CN.po, po/stellarium-skycultures/"
                        "zh_CN.po) + existing constellation_culture.json"),
        "name_source_license": ("GPL-2.0-or-later (translation files); "
                                "constellation names are short factual labels"),
        "lines_are_iau_official": False,
        "lines_note": (
            "星座连线是传统/民间约定，不是 IAU 官方定义。IAU 只规定 88 个"
            "星座的名称与边界；本文件的连线图形来自 Stellarium modern_iau "
            "（与 Sky & Telescope 合作、发布于 IAU 网站），仅作示意，"
            "不应用作边界或科学坐标依据。"),
        "boundary_source": None,
        "boundary_note": (
            "IAU 官方 88 星座边界多边形未纳入本次交付（未找到许可清晰、"
            "可直接再分发的本地边界数据）。本文件的 lines 只是示意连线，"
            "不能当作星座边界。"),
        "constellation_count": 88,
        "segment_count": sum(c["segment_count"] for c in constellations),
        "original_segment_count": sum(c["original_segment_count"]
                                      for c in constellations),
        "dropped_segment_count": dropped_all,
        "unresolved_hips": sorted(set(unresolved_all)),
        "mapping": ("HIP -> HYG v4.1 id exact match; proper/Bayer recorded "
                    "from HYG; unresolved endpoints are dropped and counted, "
                    "never guessed"),
        "hyg_source": "HYG Database v4.1 (CC BY-SA 4.0)",
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }
    payload = {"meta": meta, "constellations": constellations}
    meta["data_sha256"] = canonical_sha256(payload)
    return payload


# ---------------------------------------------------------------------------
# 中国星官
# ---------------------------------------------------------------------------
def parse_star_names_fab(path: Path) -> dict:
    """解析 star_names.zh_CN.fab 为 区域 -> {stars, subs, xingguan} 层级。"""
    data: dict = {}
    region = sub = xg = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("###"):
            region = line[3:].strip()
            data.setdefault(region, {"stars": [], "subs": {}, "xingguan": {}})
            sub = xg = None
            continue
        if line.startswith("##"):
            m = re.match(r"##\s*\[(.+?)\]", line)
            sub = m.group(1) if m else line.lstrip("#").strip()
            if region:
                data[region]["subs"].setdefault(sub, {"stars": [], "xingguan": {}})
            xg = None
            continue
        if line.startswith("#"):
            xg = line.lstrip("#").strip()
            if region:
                if sub:
                    data[region]["subs"][sub]["xingguan"].setdefault(xg, [])
                else:
                    data[region]["xingguan"].setdefault(xg, [])
            continue
        m = re.match(r"(\d+)\|_\(\"(.+?)\"\)", line)
        if m and region:
            hip = int(m.group(1))
            name = m.group(2)
            item = (hip, name, xg)
            data[region]["stars"].append(item)
            if sub:
                data[region]["subs"][sub]["stars"].append(item)
                if xg:
                    data[region]["subs"][sub]["xingguan"].setdefault(
                        xg, []).append(item)
            elif xg:
                data[region]["xingguan"].setdefault(xg, []).append(item)
    return data


def parse_description_table(path: Path) -> Tuple[dict, dict]:
    """解析 description.md 的星官表：返回 (区域元数据, 区域->星官元数据)。"""
    regions: dict = {}
    xing: dict = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [c.replace("<notr>", "").replace("</notr>", "").strip()
                 for c in line.strip().strip("|").split("|")]
        if len(cells) != 5:
            continue
        name, pinyin, english, primary, added = cells
        if not name or name == "Chinese Name":
            continue
        is_region = (primary == "" and added == "" and
                     name in set(YUAN) | set(XIU))
        if is_region:
            current = name
            regions[name] = {"pinyin": pinyin, "english": english}
            xing.setdefault(name, [])
            continue
        if current:
            xing.setdefault(current, []).append({
                "name": name, "pinyin": pinyin, "english": english,
                "primary_count": primary, "added_count": added})
    return regions, xing


def _region_suffix(name: str) -> str:
    m = re.search(r"[（(](.+?)[）)]", name)
    return m.group(1).strip() if m else ""


def _split_polylines(raw_lines, hyg, catalog_ids):
    lines, unresolved = [], []
    for poly in raw_lines or []:
        run = []
        for h in poly:
            if not isinstance(h, int):
                if isinstance(h, str) and h.isdigit():
                    h = int(h)
                else:
                    unresolved.append(str(h))
                    if len(run) >= 2:
                        lines.append(run)
                    run = []
                    continue
            row = hyg.get(h)
            if row is None:
                unresolved.append(h)
                if len(run) >= 2:
                    lines.append(run)
                run = []
                continue
            run.append(star_ref(h, row, catalog_ids))
        if len(run) >= 2:
            lines.append(run)
    return lines, unresolved


def build_xingguan(stellarium: Path, hyg: Dict[int, dict], catalog_ids: set) -> dict:
    sky = stellarium / "skycultures" / "chinese"
    idx_path = sky / "index.json"
    fab_path = sky / "star_names.zh_CN.fab"
    desc_path = sky / "description.md"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    fab = parse_star_names_fab(fab_path)
    table_regions, table_xing = parse_description_table(desc_path)

    entries = idx["constellations"]
    exact = {}
    by_region = {}
    for e in entries:
        native = e["common_name"]["native"]
        exact.setdefault(native, e)
        suffix = _region_suffix(native) or _clean_name(native)
        by_region.setdefault(suffix, {}).setdefault(
            _norm_text(native), []).append(e)

    def lines_for(name: str, region: str) -> Optional[list]:
        e = exact.get(name)
        if e is None:
            cands = by_region.get(region, {}).get(_norm_text(name), [])
            if len(cands) == 1:
                e = cands[0]
        if e is None:
            return None
        return e.get("lines") or []

    def refs_for_items(items):
        """items: [(hip, zh_name, xg)] -> 去重 star refs（保留首个中文星名）。"""
        out = []
        seen = {}
        for hip, zh_name, _xg in items:
            row = hyg.get(hip)
            if row is None:
                ref = {
                    "hip": int(hip), "hyg_id": None, "proper": "", "bayer": "",
                    "ra_deg": None, "dec_deg": None, "vmag": None, "con": "",
                    "in_catalog": False, "zh_name": zh_name,
                    "mapped": False,
                }
            else:
                ref = star_ref(hip, row, catalog_ids)
                ref["zh_name"] = zh_name
                ref["mapped"] = True
            key = (ref["hip"], ref["zh_name"])
            if key not in seen:
                seen[key] = ref
                out.append(ref)
        # 同一 HIP 的多个中文名合并到 alt_names
        merged = {}
        for ref in out:
            prev = merged.get(ref["hip"])
            if prev is None:
                ref = dict(ref)
                ref["alt_names"] = []
                merged[ref["hip"]] = ref
            elif ref["zh_name"] not in prev["alt_names"]:
                prev["alt_names"].append(ref["zh_name"])
        return sorted(merged.values(), key=lambda r: (r["vmag"] is None,
                                                     r["vmag"] or 99, r["hip"]))

    def make_xingguan(name: str, region: str, items) -> dict:
        meta = None
        for row in table_xing.get(region, []):
            if row["name"] == name or _norm_text(row["name"]) == _norm_text(name):
                meta = row
                break
        meta = meta or {}
        raw = lines_for(name, region) or []
        lines, unresolved = _split_polylines(raw, hyg, catalog_ids)
        return {
            "name_zh": _clean_name(name),
            "name_zh_source": name,
            "name_en": meta.get("english", ""),
            "pinyin": meta.get("pinyin", ""),
            "primary_star_count_source": meta.get("primary_count", ""),
            "added_star_count_source": meta.get("added_count", ""),
            "stars": refs_for_items(items),
            "lines": lines,
            "segment_count": sum(max(0, len(p) - 1) for p in lines),
            "unresolved_tokens": unresolved,
        }

    regions = []
    for key in YUAN:
        nodes = fab.get(key, {"stars": [], "xingguan": {}})
        xgs = [make_xingguan(name, key, items)
               for name, items in nodes["xingguan"].items()]
        xgs.sort(key=lambda x: x["name_zh"])
        rmeta = table_regions.get(key, {})
        # 区域连线 = 左右垣连线
        raw_lines = []
        for wall in YUAN_WALLS[key]:
            raw_lines.extend(exact.get(wall, {}).get("lines") or [])
        lines, unresolved = _split_polylines(raw_lines, hyg, catalog_ids)
        stars = refs_for_items(nodes["stars"])
        mapped = [s for s in stars if s["mapped"]]
        in_cat = [s for s in stars if s["in_catalog"]]
        regions.append({
            "key": key,
            "type": "yuan",
            "group": "三垣",
            "name_zh": key,
            "name_zh_source": key,
            "name_en": rmeta.get("english", ""),
            "pinyin": rmeta.get("pinyin", ""),
            "components": YUAN_WALLS[key],
            "xingguan": xgs,
            "xingguan_count": len(xgs),
            "stars": stars,
            "lines": lines,
            "segment_count": sum(max(0, len(p) - 1) for p in lines),
            "star_count": len(stars),
            "unique_star_hip_count": len({s["hip"] for s in stars}),
            "mapped_count": len(mapped),
            "in_catalog_count": len(in_cat),
            "unmapped_count": len(stars) - len(mapped),
            "unmapped_hips": sorted(s["hip"] for s in stars if not s["mapped"]),
            "unresolved_tokens": unresolved,
        })

    # 找到 28 宿所属的四象
    xiu_group = {}
    for region, node in fab.items():
        for sub in node["subs"]:
            if sub in XIU:
                xiu_group[sub] = region

    for key in XIU:
        parent = None
        for region, node in fab.items():
            if key in node["subs"]:
                parent = node["subs"][key]
                break
        if parent is None:
            raise ValueError(f"star_names 中缺少 {key}")
        xgs = [make_xingguan(name, key, items)
               for name, items in parent["xingguan"].items()]
        xgs.sort(key=lambda x: (0 if x["name_zh"] == key else 1, x["name_zh"]))
        rmeta = table_regions.get(key, {})
        raw_lines = exact.get(key, {}).get("lines") or []
        lines, unresolved = _split_polylines(raw_lines, hyg, catalog_ids)
        stars = refs_for_items(parent["stars"])
        mapped = [s for s in stars if s["mapped"]]
        in_cat = [s for s in stars if s["in_catalog"]]
        regions.append({
            "key": key,
            "type": "xiu",
            "group": xiu_group.get(key, ""),
            "name_zh": key,
            "name_zh_source": key,
            "name_en": rmeta.get("english", ""),
            "pinyin": rmeta.get("pinyin", ""),
            "components": [],
            "xingguan": xgs,
            "xingguan_count": len(xgs),
            "stars": stars,
            "lines": lines,
            "segment_count": sum(max(0, len(p) - 1) for p in lines),
            "star_count": len(stars),
            "unique_star_hip_count": len({s["hip"] for s in stars}),
            "mapped_count": len(mapped),
            "in_catalog_count": len(in_cat),
            "unmapped_count": len(stars) - len(mapped),
            "unmapped_hips": sorted(s["hip"] for s in stars if not s["mapped"]),
            "unresolved_tokens": unresolved,
        })

    all_stars = {}
    unmapped = {}
    xg_count = 0
    for r in regions:
        xg_count += r["xingguan_count"]
        for s in r["stars"]:
            all_stars.setdefault(s["hip"], s)
            if not s["mapped"]:
                unmapped[s["hip"]] = s
    if len(regions) != 31:
        raise ValueError(f"区域数量异常: {len(regions)}")
    if [r["key"] for r in regions[:3]] != YUAN:
        raise ValueError("三垣顺序异常")

    meta = {
        "schema": "mbdsdr.chinese_xingguan.v1",
        "system_id": "chinese_xingguan",
        "system_name_zh": "中国星官（三垣二十八宿）",
        "system_name_en": ("Chinese Xingguan "
                           "(Three Enclosures and Twenty-Eight Mansions)"),
        "source": "Stellarium skyculture chinese",
        "source_url": ("https://github.com/Stellarium/stellarium/tree/"
                       "master/skycultures/chinese"),
        "source_license": "CC BY-SA 4.0",
        "source_license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": (
            "Chinese Xingguan names, star assignments and figures from "
            "Stellarium's chinese sky culture (CC BY-SA 4.0), based on "
            "Yi Xiang Kao Cheng (1756) / its continuation (1844), as "
            "compiled in Yi Shitong's Chinese and Western Contrast Star "
            "Chart and Catalogue 1950.0."),
        "source_files": {
            "index": {
                "path": "reference/stellarium/skycultures/chinese/index.json",
                "sha256": sha256_file(idx_path)},
            "star_names": {
                "path": ("reference/stellarium/skycultures/chinese/"
                         "star_names.zh_CN.fab"),
                "sha256": sha256_file(fab_path)},
            "description": {
                "path": "reference/stellarium/skycultures/chinese/description.md",
                "sha256": sha256_file(desc_path)},
        },
        "mapping": (
            "Exact HIP -> HYG v4.1 id match (source lists HIP only). "
            "HYG proper/Bayer names are recorded for every mapped star. "
            "Unmapped HIPs are left empty and counted; no name guess, "
            "no memory-based star insertion."),
        "hyg_source": "HYG Database v4.1 (CC BY-SA 4.0)",
        "region_scope": (
            "Minimal usable set: three enclosures (紫微/太微/天市) and "
            "twenty-eight mansions. Each enclosure is represented by its "
            "left/right wall figures for drawing; its star membership "
            "includes all Xingguans listed under that enclosure in "
            "star_names.zh_CN.fab."),
        "boundary_source": None,
        "boundary_note": (
            "源说明明确：星官只表示恒星分布，没有具体边界"
            "（Xingguans refer only to the way stars are distributed, "
            "with no specific boundaries between Xingguans）。"
            "本文件不提供星官边界多边形。"),
        "region_count": 31,
        "yuan_count": 3,
        "xiu_count": 28,
        "xingguan_count": xg_count,
        "unique_star_hip_count": len(all_stars),
        "mapped_count": len(all_stars) - len(unmapped),
        "in_catalog_count": sum(1 for s in all_stars.values()
                                if s["in_catalog"]),
        "unmapped_count": len(unmapped),
        "unmapped_hips": sorted(unmapped),
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }
    payload = {"meta": meta, "regions": regions}
    meta["data_sha256"] = canonical_sha256(payload)
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stellarium", default=str(DEFAULT_STELLARIUM))
    ap.add_argument("--hyg-csv", default=str(DEFAULT_HYG_CSV))
    ap.add_argument("--catalog-npz", default=str(DEFAULT_NPZ))
    args = ap.parse_args(list(argv) if argv is not None else None)

    stellarium = Path(args.stellarium)
    hyg = load_hyg(Path(args.hyg_csv))
    catalog_ids = load_catalog_ids(Path(args.catalog_npz))
    iau = build_iau(stellarium, hyg, catalog_ids)
    xg = build_xingguan(stellarium, hyg, catalog_ids)
    write_json(IAU_OUT, iau)
    write_json(XINGGUAN_OUT, xg)
    print(f"IAU: {iau['meta']['constellation_count']} 座, "
          f"{iau['meta']['segment_count']} 段, "
          f"丢弃 {iau['meta']['dropped_segment_count']} 段 "
          f"(未映射 HIP {iau['meta']['unresolved_hips']})")
    print(f"星官: {xg['meta']['region_count']} 区, "
          f"{xg['meta']['xingguan_count']} 星官, "
          f"{xg['meta']['unique_star_hip_count']} 星, "
          f"未映射 {xg['meta']['unmapped_count']} ({xg['meta']['unmapped_hips']})")
    print(f"写入 {IAU_OUT.name} / {XINGGUAN_OUT.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
