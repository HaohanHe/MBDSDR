#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""从 OpenStreetMap 生成离线城市表（可选；不跑它也能用在线 Nominatim）。

## 什么时候需要

``mbdsdr_ai/location`` 的默认路径是**在线 Nominatim**（免数据文件、天然支持
将来换高德）。只有要做「完全离线也能搜城市」的发行版时才生成这张表。

## 数据与许可证

数据来源 OpenStreetMap（Overpass API），许可证 **ODbL 1.0**：
使用/再分发须署名 "© OpenStreetMap contributors"，衍生数据库同样 ODbL。
本脚本把署名与来源写进产物的 ``meta``，并在 ``mbdsdr_ai/data/places/``
放 LICENSE 文本，与 MIT 代码分开声明。

## 用法

    # 拉中国境内人口 ≥ 10 万的城市/城镇（需要网络）
    python scripts/build_place_index.py --area CN --min-pop 100000

    # 只校验现有产物（不联网）
    python scripts/build_place_index.py --offline

    # 从本地 Overpass JSON 导入（自己下载过的话）
    python scripts/build_place_index.py --from-json dump.json

## 红线

* **失败绝不覆盖旧产物**：先写 .tmp，全部成功才 rename。
* 拉不到就说拉不到（退出码 1 + 原因），不写空表冒充"已内置"。
* 不按经度推测时区：OSM 没给 tz 就留空。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_OUT = os.path.join(ROOT, "mbdsdr_ai", "data", "places", "osm_places.json")
USER_AGENT = ("MBDSDR-place-index/1.0 (offline city table build; "
              "+https://github.com/HaohanHe/MBDSDR)")

#: 按 ISO 国家代码取区域；Overpass 用 area id = 3600000000 + relation id。
AREA_QUERY = {
    "CN": 'area["ISO3166-1"="CN"][admin_level=2];',
    "JP": 'area["ISO3166-1"="JP"][admin_level=2];',
    "WORLD": "",
}

OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _post_overpass(query: str, timeout: int = 180):
    last = ""
    for url in OVERPASS_MIRRORS:
        try:
            data = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(
                url, data=data,
                headers={"User-Agent": USER_AGENT,
                         "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError, ValueError) as exc:
            last = f"{url}: {type(exc).__name__}: {exc}"
            _log(f"  [warn] {last}")
    raise RuntimeError(f"所有 Overpass 镜像都失败：{last}")


def build_query(area: str, min_pop: int) -> str:
    scope = AREA_QUERY.get(area.upper(), AREA_QUERY["WORLD"])
    pop = f'(if: t["population"] >= {int(min_pop)})' if min_pop else ""
    return (
        "[out:json][timeout:170];"
        f"{scope}"
        '('
        f'  node["place"~"^(city|town)$"]{pop}(area.searchArea);'
        ')'
        ';out body;'
    )


def nodes_to_rows(elements) -> list:
    rows = []
    seen = set()
    for el in elements or []:
        if not isinstance(el, dict) or el.get("type") != "node":
            continue
        tags = el.get("tags") or {}
        name = (tags.get("name:zh") or tags.get("name") or "").strip()
        lat, lon = el.get("lat"), el.get("lon")
        if not name or lat is None or lon is None:
            continue
        key = (round(float(lat), 4), round(float(lon), 4), name)
        if key in seen:
            continue
        seen.add(key)
        try:
            pop = int(float(str(tags.get("population", "0")).replace(",", "")))
        except (TypeError, ValueError):
            pop = 0
        iso = str(tags.get("addr:country") or tags.get("ISO3166-1") or "")
        rows.append([name, round(float(lat), 5), round(float(lon), 5), pop, iso])
    rows.sort(key=lambda r: -int(r[3] or 0))
    return rows


def write_table(rows, out_path: str, *, area: str, min_pop: int,
                source: str) -> str:
    payload = {
        "meta": {
            "licence": "ODbL-1.0",
            "attribution": "© OpenStreetMap contributors",
            "source": source,
            "area": area,
            "min_pop": int(min_pop),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "count": len(rows),
            # 行格式： [name, lat, lon, population, country_iso]
            "columns": ["name", "lat", "lon", "population", "country"],
        },
        "places": rows,
    }
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, out_path)     # 只有全成功才顶掉旧产物
    return out_path


def verify(out_path: str) -> int:
    if not os.path.exists(out_path):
        _log(f"[FAIL] 产物不存在：{out_path}")
        return 1
    with open(out_path, "rb") as f:
        raw = f.read()
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
        rows = data.get("places") or []
        meta = data.get("meta") or {}
    except (ValueError, TypeError):
        _log(f"[FAIL] 产物不是合法 JSON：{out_path}")
        return 1
    _log(f"产物：{out_path}")
    _log(f"  条数：{len(rows)}")
    _log(f"  体积：{len(raw) / 1024.0:.1f} KiB")
    _log(f"  许可证：{meta.get('licence')} · {meta.get('attribution')}")
    _log(f"  sha256：{digest}")
    if not rows:
        _log("[FAIL] 表是空的（不能用空表冒充已内置）")
        return 1
    bad = [r for r in rows[:200]
           if not (isinstance(r, list) and len(r) >= 3
                   and -90 <= float(r[1]) <= 90 and -180 <= float(r[2]) <= 180)]
    if bad:
        _log(f"[FAIL] 有非法坐标行（示例：{bad[0]}）")
        return 1
    _log("[OK] 校验通过")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 OSM 离线城市表")
    ap.add_argument("--area", default="CN", help="CN / JP / WORLD")
    ap.add_argument("--min-pop", type=int, default=100000,
                    help="按 population 过滤（0 = 不过滤，体积会很大）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--offline", action="store_true",
                    help="不联网，只校验现有产物")
    ap.add_argument("--from-json", default="",
                    help="从本地 Overpass JSON 导入（不联网）")
    args = ap.parse_args(argv)

    if args.offline:
        return verify(args.out)

    if args.from_json:
        with open(args.from_json, "r", encoding="utf-8") as f:
            dump = json.load(f)
        rows = nodes_to_rows(dump.get("elements"))
        if not rows:
            _log("[FAIL] 输入里没有可用的 place=city/town 节点")
            return 1
        write_table(rows, args.out, area=args.area, min_pop=args.min_pop,
                    source=f"local-json:{os.path.basename(args.from_json)}")
        return verify(args.out)

    query = build_query(args.area, args.min_pop)
    _log(f"查询 Overpass：area={args.area} min_pop={args.min_pop}")
    try:
        dump = _post_overpass(query)
    except RuntimeError as exc:
        _log(f"[FAIL] {exc}")
        _log("提示：网络不可用时用 --from-json 导入已下载的 dump，"
             "或继续使用在线 Nominatim（默认路径，不需要本表）。")
        return 1

    rows = nodes_to_rows(dump.get("elements"))
    if not rows:
        _log("[FAIL] Overpass 返回 0 条可用节点（不写空表）")
        return 1
    write_table(rows, args.out, area=args.area, min_pop=args.min_pop,
                source="overpass")
    return verify(args.out)


if __name__ == "__main__":
    sys.exit(main())
