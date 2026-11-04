"""build_xingguan_star_names.py — 生成星官成员的中文星名唯一映射
==================================================================

产物 ``mbdsdr_ai/data/xingguan_star_names_zh.json``：

- 每颗星官成员一条记录（按 HIP 去重），字段 = HIP/HYG id/中文名/别名/
  所属星官/所属区域/是否映射/是否在随仓星表；
- 中文名**只**来自 Stellarium ``chinese`` 天空文化的
  ``star_names.zh_CN.fab``（CC BY-SA 4.0），不做翻译、不凭记忆补名；
- 取不到中文名的星 ``zh_name`` 留空并计数，绝不拿 proper/Bayer 名冒充；
- meta 记录源文件 SHA-256、许可、成员/命名/去重计数与未命名清单。

用法::

    python -m mbdsdr_ai.skyengine.build_xingguan_star_names \\
        --stellarium D:/mbdsdr/reference/stellarium
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .build_constellation_data import (
    canonical_sha256, parse_star_names_fab, sha256_file, write_json)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
XG_PATH = DATA_DIR / "xingguan_chinese.json"
OUT_PATH = DATA_DIR / "xingguan_star_names_zh.json"
DEFAULT_STELLARIUM = Path(
    Path(__file__).resolve().parents[3] / "reference" / "stellarium")


def _fab_names(fab: dict) -> Dict[int, List[str]]:
    """HIP -> 源文件里出现的全部中文名（保持出现顺序、去重）。"""
    names: Dict[int, List[str]] = {}

    def collect(node: dict) -> None:
        for hip, name, _xg in node.get("stars", []):
            bucket = names.setdefault(int(hip), [])
            if name not in bucket:
                bucket.append(name)
        for sub in node.get("subs", {}).values():
            collect(sub)

    for node in fab.values():
        collect(node)
    return names


def build(stellarium: Path) -> dict:
    sky = stellarium / "skycultures" / "chinese"
    fab_path = sky / "star_names.zh_CN.fab"
    if not fab_path.is_file():
        raise FileNotFoundError(f"缺少源文件: {fab_path}")
    if not XG_PATH.is_file():
        raise FileNotFoundError(f"缺少星官数据: {XG_PATH}")

    payload = json.loads(XG_PATH.read_text(encoding="utf-8"))
    regions = payload["regions"]
    fab_names = _fab_names(parse_star_names_fab(fab_path))

    stars: Dict[int, dict] = {}
    occurrences = 0
    for region in regions:
        occurrences += len(region["stars"])
        for s in region["stars"]:
            hip = int(s["hip"])
            rec = stars.get(hip)
            if rec is None:
                rec = {
                    "hip": hip,
                    "hyg_id": s.get("hyg_id"),
                    "zh_name": s.get("zh_name") or "",
                    "alt_names": [],
                    "xingguan": [],
                    "regions": [],
                    "mapped": bool(s.get("mapped")),
                    "in_catalog": bool(s.get("in_catalog")),
                    "proper": s.get("proper") or "",
                    "bayer": s.get("bayer") or "",
                    "vmag": s.get("vmag"),
                    "con": s.get("con") or "",
                    "_names": [s["zh_name"]] if s.get("zh_name") else [],
                }
                stars[hip] = rec
            else:
                # 同一颗星出现在多个区域：映射信息必须一致；
                # 中文名可能因所属星官不同而不同（如参宿/毕宿交叉），
                # 全部收进 alt_names，主名保留首次出现者。
                current = {
                    "hyg_id": s.get("hyg_id"),
                    "mapped": bool(s.get("mapped")),
                    "in_catalog": bool(s.get("in_catalog")),
                }
                for field, value in current.items():
                    if rec[field] != value:
                        raise ValueError(
                            f"HIP {hip} 的 {field} 跨区域不一致: "
                            f"{rec[field]!r} vs {value!r}")
                if s.get("zh_name") and s["zh_name"] not in rec["_names"]:
                    rec["_names"].append(s["zh_name"])
            if region["key"] not in rec["regions"]:
                rec["regions"].append(region["key"])

        for xg in region["xingguan"]:
            name = xg["name_zh"]
            for s in xg["stars"]:
                rec = stars[int(s["hip"])]
                if name not in rec["xingguan"]:
                    rec["xingguan"].append(name)
                zh = s.get("zh_name") or ""
                if zh and zh not in rec["_names"]:
                    rec["_names"].append(zh)

    # 中文名必须能在源文件里找到；主名取首次出现，其余进 alt_names
    for hip, rec in stars.items():
        source = fab_names.get(hip, [])
        names = list(rec.pop("_names"))
        for name in names:
            if name not in source:
                raise ValueError(
                    f"HIP {hip} 的中文名 {name!r} 不在源文件 {source!r}")
        if not source:
            if rec["zh_name"]:
                raise ValueError(f"HIP {hip} 的中文名在源文件中不存在")
            rec["alt_names"] = []
            continue
        if rec["zh_name"] and rec["zh_name"] not in source:
            raise ValueError(
                f"HIP {hip} 的主名 {rec['zh_name']!r} 不在源文件 {source!r}")
        ordered = names + [n for n in source if n not in names]
        rec["alt_names"] = [n for n in ordered if n != rec["zh_name"]]

    with_name = [r for r in stars.values() if r["zh_name"]]
    no_name = sorted(h for h, r in stars.items() if not r["zh_name"])
    unmapped = sorted(h for h, r in stars.items() if not r["mapped"])
    multi_name = sorted(h for h, r in stars.items() if r["alt_names"])
    multi_region = sorted(h for h, r in stars.items() if len(r["regions"]) > 1)
    no_xg = sorted(h for h, r in stars.items() if not r["xingguan"])

    meta = {
        "schema": "mbdsdr.xingguan_star_names.v1",
        "dataset": "xingguan_star_names_zh",
        "system_id": "chinese_xingguan",
        "locale": "zh_CN",
        "source": "Stellarium skyculture chinese / star_names.zh_CN.fab",
        "source_url": ("https://github.com/Stellarium/stellarium/tree/"
                       "master/skycultures/chinese"),
        "source_license": "CC BY-SA 4.0",
        "source_license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "attribution": ("Stellarium contributors — skyculture \"Chinese\", "
                        "CC BY-SA 4.0"),
        "source_file_sha256": sha256_file(fab_path),
        "source_files": {
            "star_names": {
                "path": ("reference/stellarium/skycultures/chinese/"
                         "star_names.zh_CN.fab"),
                "sha256": sha256_file(fab_path)},
            "members": {
                "path": "mbdsdr_ai/data/xingguan_chinese.json",
                "sha256": sha256_file(XG_PATH)},
        },
        "name_policy": (
            "Chinese star names come only from star_names.zh_CN.fab. "
            "Missing names are left empty and counted; proper/Bayer names "
            "are never substituted or presented as Chinese names."),
        "generated_at": _dt.date.today().isoformat(),
        "generator": "mbdsdr_ai.skyengine.build_xingguan_star_names",
        "member_occurrences": occurrences,
        "total_members": len(stars),
        "duplicate_occurrences_deduped": occurrences - len(stars),
        "with_zh_name": len(with_name),
        "without_zh_name": len(no_name),
        "multi_name_members": len(multi_name),
        "multi_region_members": len(multi_region),
        "members_without_xingguan": len(no_xg),
        "unmapped_hips": unmapped,
        "no_name_hips": no_name,
    }
    out = {
        "meta": meta,
        "stars": {str(hip): stars[hip] for hip in sorted(stars)},
    }
    out["meta"]["data_sha256"] = canonical_sha256(out)
    return out


def main(argv: Optional[Iterable[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stellarium", default=str(DEFAULT_STELLARIUM))
    args = ap.parse_args(list(argv) if argv is not None else None)
    payload = build(Path(args.stellarium))
    write_json(OUT_PATH, payload)
    meta = payload["meta"]
    print(f"{OUT_PATH}  members={meta['total_members']} "
          f"with_name={meta['with_zh_name']} "
          f"without_name={meta['without_zh_name']} "
          f"deduped={meta['duplicate_occurrences_deduped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
