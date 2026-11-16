"""生成手机端天球数据资产（M1.1）。

为什么要有这个脚本
------------------
手机端要"举起来指天"，就必须在**断网、无服务器**的条件下自己知道天上有什么。
所以星表与星官连线必须随 App 打包。

但打包的不是"另一份星表"——它是**项目唯一星表的一次导出**：
数据源仍是 `mbdsdr_ai.skyengine.star_catalog`（HYG v4.1）与
`mbdsdr_ai.skyengine.xingguan`（中国星官），本脚本只做裁剪与序列化。
任何一处口径变化都必须重跑本脚本，不许在手机端另写一份换算。

产物（`mobile/assets/sky/`）
--------------------------
- `stars.bin`    定长记录的恒星子集（小端）
- `asterisms.json` 星官/星区折线（HIP 序列）+ 名称
- `star_names.json` HIP -> 中文星名 / 所属星官 / 西名
- `SKY_DATA_LICENSE.txt` 数据许可与来源（CC BY-SA 4.0，必须随包分发）

坐标系
------
`stars.bin` 里是 **J2000 赤道坐标**（与星表同口径），不是地平坐标。
地平坐标必须由手机端用自己的时刻与站址现算——这是"指哪算哪"的前提，
存成地平坐标就等于把某一刻的天焊死。

用法
----
    python scripts/build_mobile_sky_assets.py [--limit-mag 6.5] [--out DIR]

`--limit-mag` 只影响"没有星官归属"的暗星；**星官连线端点一律强制包含**，
否则画出来的星官会缺角（实测有 222 个端点暗于 6.5 等）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import struct
import sys
from typing import Dict, List, Set, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

MAGIC = b"MBSKYD01"
"""8 字节魔数，改格式必须换它；手机端读到不认识的魔数要显式报错。"""

RECORD_STRUCT = struct.Struct("<ffffI")
"""每颗星一条定长记录：ra_deg, dec_deg, vmag, bv, hip（小端）。"""

DEFAULT_OUT = os.path.join(_ROOT, "mobile", "assets", "sky")


def _collect_asterism_hips(xg) -> Set[int]:
    """星官连线实际用到的全部 HIP（只取真正画得出来的端点）。"""
    hips: Set[int] = set()
    for region in xg.regions:
        for poly in region.lines:
            for star in poly:
                hips.add(int(star.hip))
        for ast in region.xingguan:
            for poly in ast.lines:
                for star in poly:
                    hips.add(int(star.hip))
    return hips


def _serialize_stars(cat, keep_mask, out_path: str) -> int:
    """写定长二进制星表；返回写入条数。"""
    import numpy as np

    ra = np.asarray(cat.ra_deg, dtype=np.float32)
    dec = np.asarray(cat.dec_deg, dtype=np.float32)
    vmag = np.asarray(cat.vmag, dtype=np.float32)
    bv = np.asarray(cat.bv, dtype=np.float32)
    hip = np.asarray(cat.hip, dtype=np.int64)

    idx = np.nonzero(keep_mask)[0]
    # 稳定排序：先按 hip 升序（缺 HIP 的排最后），便于两端逐位比对与二分查找。
    order = sorted(idx.tolist(), key=lambda i: (int(hip[i]) if hip[i] > 0 else 1 << 30, int(i)))

    with open(out_path, "wb") as fh:
        fh.write(MAGIC)
        fh.write(struct.pack("<I", len(order)))
        for i in order:
            bv_val = float(bv[i])
            # HYG 里 bv 可能缺失（0 或非有限）；用 0.65（太阳色）会造假，
            # 所以缺失一律写 NaN，手机端按"颜色未知"处理。
            if not (bv_val == bv_val) or bv_val == 0.0:
                bv_val = float("nan")
            fh.write(RECORD_STRUCT.pack(
                float(ra[i]), float(dec[i]), float(vmag[i]), bv_val,
                int(hip[i]) if hip[i] > 0 else 0))
    return len(order)


def _build_asterisms_payload(xg, star_names: Dict[str, dict]) -> Tuple[dict, int]:
    """星官/星区折线 + 名称。返回 (payload, 线段总数)。"""
    regions: List[dict] = []
    total_segments = 0
    for region in xg.regions:
        def _poly(poly) -> List[int]:
            return [int(s.hip) for s in poly]

        region_polys = [_poly(p) for p in region.lines if len(p) >= 2]
        asts: List[dict] = []
        for ast in region.xingguan:
            polys = [_poly(p) for p in ast.lines if len(p) >= 2]
            if not polys:
                continue  # 单星星官没有折线，见下面的 singles 字段
            total_segments += sum(len(p) - 1 for p in polys)
            asts.append({"name": ast.name_zh, "pinyin": ast.pinyin,
                         "en": ast.name_en, "lines": polys})
        total_segments += sum(len(p) - 1 for p in region_polys)

        singles = [ast.name_zh for ast in region.xingguan
                   if not [p for p in ast.lines if len(p) >= 2]]
        regions.append({
            "key": region.key,
            "type": region.type,
            "group": region.group,
            "name": region.name_zh,
            "region_lines": region_polys,
            "asterisms": asts,
            "single_star_asterisms": singles,
        })
    payload = {
        "meta": {
            "schema": "mbdsdr.mobile.xingguan.v1",
            "data_license": "CC BY-SA 4.0",
            "coord_sys": "equatorial_j2000",
            "region_count": len(regions),
            "segment_count": total_segments,
        },
        "regions": regions,
    }
    return payload, total_segments


def _build_star_names_payload(cat, star_names: Dict[str, dict],
                              hip_to_index: Dict[int, int]) -> dict:
    """只导出"有名或有星官归属"的星，全量导出会大而无用。"""
    out: Dict[str, dict] = {}
    for key, rec in star_names.items():
        hip = int(rec.get("hip") or 0)
        if hip <= 0 or hip not in hip_to_index:
            continue
        zh = rec.get("zh_name") or ""
        proper = rec.get("proper") or ""
        if not zh and not proper:
            continue
        entry = {}
        if zh:
            entry["zh"] = zh
            alts = [a for a in (rec.get("alt_names") or []) if a and a != zh]
            if alts:
                entry["alt"] = alts[:4]
        if proper:
            entry["en"] = proper
        asts = rec.get("xingguan") or []
        if asts:
            entry["asterisms"] = asts[:4]
        regs = rec.get("regions") or []
        if regs:
            entry["regions"] = regs[:2]
        con = rec.get("con") or ""
        if con:
            entry["con"] = con
        if entry:
            out[str(hip)] = entry
    return out


def _write_license(out_path: str, star_count: int, segment_count: int,
                   limit_mag: float, generated_utc: str) -> None:
    text = f"""MBDSDR 手机端天球数据 · 许可与来源
=====================================

本目录数据由 scripts/build_mobile_sky_assets.py 从项目唯一星表导出，
不是独立编篡的另一份星表。生成时间（UTC）：{generated_utc}

恒星数据
--------
来源：HYG Database v4.1（https://github.com/astronexus/HYG-Database）
许可：CC BY-SA 4.0
本导出为子集：视星等 <= {limit_mag} 的恒星，加上全部中国星官连线端点星
（端点最暗可达 8 以上，必须强制包含，否则星官会缺角）。
坐标：J2000 赤道坐标，未做岁差与自行修正——地平坐标由客户端按观测时刻现算。

星官与星名数据
--------------
来源：基于《仪象考成》(1756)、《仪象考成续编》(1844) 的中国星官体系，
经伊世同《中西对照恒星图表 1950.0》、潘鼐校订本整理；
本项目采用 Starry Night / Stellarium 中国星文化（skycultures/chinese）的对齐口径，
许可：CC BY-SA 4.0

代码与数据分别授权
------------------
本仓库代码为 MIT；本目录数据为 CC BY-SA 4.0。分发本应用时必须保留本文件。

本导出统计：恒星 {star_count} 颗，星官连线 {segment_count} 段。
"""
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(text)


def build(out_dir: str, limit_mag: float = 6.5) -> dict:
    import numpy as np

    from mbdsdr_ai.skyengine.star_catalog import get_catalog
    from mbdsdr_ai.skyengine.xingguan import get_xingguan

    cat = get_catalog()
    xg = get_xingguan()

    names_path = os.path.join(_ROOT, "mbdsdr_ai", "data",
                              "xingguan_star_names_zh.json")
    with open(names_path, encoding="utf-8") as fh:
        star_names = json.load(fh)["stars"]

    hip = np.asarray(cat.hip, dtype=np.int64)
    vmag = np.asarray(cat.vmag, dtype=np.float32)

    # 星官端点星必须全部保留（实测 222 个端点暗于 6.5 等）。
    asterism_hips = _collect_asterism_hips(xg)
    in_asterism = np.isin(hip, np.fromiter(asterism_hips, dtype=np.int64,
                                           count=len(asterism_hips)))
    keep = (vmag <= float(limit_mag)) | in_asterism

    os.makedirs(out_dir, exist_ok=True)
    generated_utc = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    stars_path = os.path.join(out_dir, "stars.bin")
    star_count = _serialize_stars(cat, keep, stars_path)

    hip_to_index = {int(h): i for i, h in enumerate(hip) if h > 0}
    ast_payload, segment_count = _build_asterisms_payload(xg, star_names)
    ast_payload["meta"]["star_count"] = star_count
    ast_payload["meta"]["limit_mag"] = float(limit_mag)
    ast_payload["meta"]["generated_utc"] = generated_utc
    ast_path = os.path.join(out_dir, "asterisms.json")
    with open(ast_path, "w", encoding="utf-8") as fh:
        json.dump(ast_payload, fh, ensure_ascii=False, separators=(",", ":"))

    names_payload = _build_star_names_payload(cat, star_names, hip_to_index)
    names_out = os.path.join(out_dir, "star_names.json")
    with open(names_out, "w", encoding="utf-8") as fh:
        json.dump(names_payload, fh, ensure_ascii=False, separators=(",", ":"))

    _write_license(os.path.join(out_dir, "SKY_DATA_LICENSE.txt"),
                   star_count, segment_count, limit_mag, generated_utc)

    return {
        "stars": star_count,
        "segments": segment_count,
        "named_stars": len(names_payload),
        "bytes": {
            "stars.bin": os.path.getsize(stars_path),
            "asterisms.json": os.path.getsize(ast_path),
            "star_names.json": os.path.getsize(names_out),
        },
        "out_dir": out_dir,
    }


def _verify(out_dir: str) -> None:
    """回读自校验：格式合法、星官端点全部在星表里、计数自洽。

    这一步是硬门：导出后不复核等于把错误数据发到手机上。
    """
    stars_path = os.path.join(out_dir, "stars.bin")
    with open(stars_path, "rb") as fh:
        magic = fh.read(8)
        if magic != MAGIC:
            raise SystemExit(f"stars.bin 魔数不符：{magic!r}")
        (count,) = struct.unpack("<I", fh.read(4))
        body = fh.read()
    expect = count * RECORD_STRUCT.size
    if len(body) != expect:
        raise SystemExit(f"stars.bin 长度不符：{len(body)} != {expect}")

    hips = set()
    for i in range(0, len(body), RECORD_STRUCT.size):
        _, _, _, _, h = RECORD_STRUCT.unpack_from(body, i)
        if h > 0:
            hips.add(int(h))

    with open(os.path.join(out_dir, "asterisms.json"), encoding="utf-8") as fh:
        payload = json.load(fh)
    missing: Set[int] = set()
    seg = 0
    for region in payload["regions"]:
        polys = list(region["region_lines"])
        for ast in region["asterisms"]:
            polys.extend(ast["lines"])
        for poly in polys:
            seg += len(poly) - 1
            for h in poly:
                if h not in hips:
                    missing.add(int(h))
    if missing:
        raise SystemExit(f"星官端点不在星表里（线会断）：{sorted(missing)[:10]}")
    if seg != payload["meta"]["segment_count"]:
        raise SystemExit(f"线段计数不符：{seg} != {payload['meta']['segment_count']}")
    if count != payload["meta"]["star_count"]:
        raise SystemExit(f"星数计数不符：{count} != {payload['meta']['star_count']}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成手机端天球数据资产")
    ap.add_argument("--limit-mag", type=float, default=6.5,
                    help="恒星视星等上限（默认 6.5；星官端点星不受此限）")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出目录")
    args = ap.parse_args(argv)

    stats = build(args.out, args.limit_mag)
    _verify(args.out)
    print(f"输出目录：{stats['out_dir']}")
    print(f"恒星 {stats['stars']} 颗 / 星官线段 {stats['segments']} 段 / "
          f"有名星 {stats['named_stars']} 颗")
    for name, size in stats["bytes"].items():
        print(f"  {name:<18} {size / 1024:8.1f} KB")
    print(f"  合计               {sum(stats['bytes'].values()) / 1024:8.1f} KB")
    print("自校验通过：星官端点全部在星表内，计数自洽。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
