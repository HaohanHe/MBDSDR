# SPDX-License-Identifier: MIT
"""
build_full_sky_catalog.py — 由 HYG v4.1 源 CSV 生成随仓分发的全天星表
======================================================================

用途
----
把本地 HYG v4.1 原始 CSV（默认不随仓分发）压缩成一份二进制星表
``mbdsdr_ai/data/full_sky_hyg_v41.npz``，供 ``skyengine.star_catalog``
在运行时直接加载。

数据来源与许可
--------------
- 源: HYG Database v4.1 (Astronomy Nexus / David Nash)
  https://github.com/astronexus/HYG-Database
- 许可: Creative Commons Attribution-ShareAlike 4.0 International
  (CC BY-SA 4.0) https://creativecommons.org/licenses/by-sa/4.0/
- 生成物是 HYG 的衍生数据，同样按 CC BY-SA 4.0 分发；代码仍为 MIT。
- 元数据把来源 URL、许可、源文件 SHA-256、生成时间写进 npz 的 ``meta``
  键，随数据文件一起走，不依赖网络也不靠口头约定。

口径
----
- 剔除 HYG 中 id=0 的“Sol”行：它是太阳，不是恒星，坐标 (0,0) 会污染星图。
- 只保留 mag <= ``MAG_CUTOFF``（9.0）的恒星。现有 ``stars.star_draw``
  的裸眼极限上限是 6.5 等（Bortle<=3），视场放大最多再加 1.5 等，
  故 9.0 等已覆盖全部可用深度，并留 0.5 等余量。
- RA 由 HYG 的小时数换算为度（×15）；Dec 保持度。
- dist 为秒差距；0 或 >=100000 视为未知，写 NaN。
- ci (B-V) / spect 缺失写 NaN / 空串，不编造。
- 按 mag 升序排序后整体存盘；另存 ``dec_order``（按 Dec 升序的索引），
  供视场锥快速裁剪。

用法
----
  python -m mbdsdr_ai.skyengine.build_full_sky_catalog \
      --source D:/mbdsdr/scratch/hygdata_v41.csv

或直接指定输出:
  ... --out mbdsdr_ai/data/full_sky_hyg_v41.npz
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

import numpy as np

CATALOG_FORMAT = "full-sky-hyg-v41"
CATALOG_VERSION = 1
MAG_CUTOFF = 9.0

SOURCE_NAME = "HYG Database v4.1"
SOURCE_URL = "https://github.com/astronexus/HYG-Database"
SOURCE_LICENSE = "CC BY-SA 4.0"
SOURCE_LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
SOURCE_ATTRIBUTION = (
    "HYG Database v4.1 by David Nash / Astronomy Nexus, "
    "licensed under CC BY-SA 4.0. This file is a derived subset."
)


def _default_out() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "data", "full_sky_hyg_v41.npz")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _float_or_nan(value: Any) -> float:
    txt = ("" if value is None else str(value)).strip()
    if not txt:
        return float("nan")
    try:
        out = float(txt)
    except ValueError:
        return float("nan")
    return out if math.isfinite(out) else float("nan")


def read_hyg(csv_path: str, mag_cutoff: float) -> Dict[str, np.ndarray]:
    """读取并筛选 HYG CSV，返回生成用数组（已按 mag 升序）。"""
    rows: List[Dict[str, Any]] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            source_id = (r.get("id") or "").strip()
            if source_id in ("", "0"):
                # id=0 是 Sol（太阳），不是恒星；剔除。
                continue
            mag = _float_or_nan(r.get("mag"))
            if not math.isfinite(mag) or mag > mag_cutoff:
                continue
            ra_h = _float_or_nan(r.get("ra"))
            dec = _float_or_nan(r.get("dec"))
            if not (math.isfinite(ra_h) and math.isfinite(dec)):
                continue
            if not (-90.0 <= dec <= 90.0):
                continue
            dist_pc = _float_or_nan(r.get("dist"))
            if not math.isfinite(dist_pc) or dist_pc <= 0.0 or dist_pc >= 1e5:
                dist_pc = float("nan")
            ci = _float_or_nan(r.get("ci"))
            rows.append({
                "hyg_id": int(source_id),
                "hip": int((r.get("hip") or "").strip() or -1),
                "ra_deg": ra_h * 15.0,
                "dec_deg": dec,
                "vmag": mag,
                "bv": ci,
                "dist_pc": dist_pc,
                "spect": (r.get("spect") or "").strip(),
                "proper": (r.get("proper") or "").strip(),
                "con": (r.get("con") or "").strip(),
            })

    if not rows:
        raise SystemExit("HYG 源文件没有可用恒星；检查 --source 路径/格式")

    rows.sort(key=lambda x: (x["vmag"], x["hyg_id"]))
    n = len(rows)
    # Unicode 宽度按实际最大值取，避免固定过宽浪费体积（空串仍占 1 字符）。
    spect_w = max([len(x["spect"]) for x in rows] + [1])
    proper_w = max([len(x["proper"]) for x in rows] + [1])
    con_w = max([len(x["con"]) for x in rows] + [1])

    def sa(key: str, dtype: Any) -> np.ndarray:
        return np.asarray([x[key] for x in rows], dtype=dtype)

    return {
        "hyg_id": sa("hyg_id", np.int32),
        "hip": sa("hip", np.int32),
        "ra_deg": sa("ra_deg", np.float32),
        "dec_deg": sa("dec_deg", np.float32),
        "vmag": sa("vmag", np.float32),
        "bv": sa("bv", np.float32),
        "dist_pc": sa("dist_pc", np.float32),
        "spect": sa("spect", f"<U{spect_w}"),
        "proper": sa("proper", f"<U{proper_w}"),
        "con": sa("con", f"<U{con_w}"),
        "dec_order": np.argsort(sa("dec_deg", np.float64), kind="stable")
                     .astype(np.int32),
    }


def build_bins(vmag: np.ndarray, width: float) -> tuple[np.ndarray, np.ndarray]:
    """0.5 等一档的分档边界与起始偏移（数组已按 mag 升序）。"""
    lo = math.floor(float(vmag.min()) / width) * width
    hi = math.ceil(float(vmag.max()) / width) * width
    edges = np.arange(lo, hi + width, width, dtype=np.float32)
    # 每颗星的档号 = floor((mag - lo)/width)
    idx = np.floor((vmag - lo) / width).astype(np.int32)
    idx = np.clip(idx, 0, len(edges) - 2)
    counts = np.bincount(idx, minlength=len(edges) - 1)
    starts = np.concatenate([[0], np.cumsum(counts)]).astype(np.int32)
    return edges, starts


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", required=True,
                    help="HYG v4.1 CSV 路径（不随仓分发）")
    ap.add_argument("--out", default=_default_out(),
                    help="输出 npz 路径 (默认 mbdsdr_ai/data/full_sky_hyg_v41.npz)")
    ap.add_argument("--mag-cutoff", type=float, default=MAG_CUTOFF,
                    help=f"保留星等上限 (默认 {MAG_CUTOFF})")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.source):
        raise SystemExit(f"找不到 HYG 源文件: {args.source}")

    arrays = read_hyg(args.source, args.mag_cutoff)
    edges, starts = build_bins(arrays["vmag"], 0.5)
    arrays["mag_bin_edges"] = edges
    arrays["mag_bin_start"] = starts

    # 数据指纹：对全部数组做规范化 SHA-256，与 meta 一起落盘。
    # 加载端会重算并比对，手改数组而不同步 meta 会直接报错。
    from .star_catalog import compute_data_sha256
    data_sha256 = compute_data_sha256(arrays)

    meta: Dict[str, Any] = {
        "format": CATALOG_FORMAT,
        "version": CATALOG_VERSION,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "source_license": SOURCE_LICENSE,
        "source_license_url": SOURCE_LICENSE_URL,
        "attribution": SOURCE_ATTRIBUTION,
        "source_file_sha256": _sha256_file(args.source),
        "data_sha256": data_sha256,
        "source_file_name": os.path.basename(args.source),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "star_count": int(len(arrays["vmag"])),
        "mag_cutoff": float(args.mag_cutoff),
        "mag_bin_width": 0.5,
        "units": {
            "ra_deg": "degree J2000",
            "dec_deg": "degree J2000",
            "vmag": "V magnitude",
            "bv": "B-V color index (NaN=unknown)",
            "dist_pc": "parsec (NaN=unknown)",
            "spect": "HYG spectral type (empty=unknown)",
        },
        "notes": [
            "Sol (HYG id=0) excluded",
            "magnitudes fainter than cut-off omitted",
            "coordinate frame: J2000/ICRS as published by HYG",
        ],
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    np.savez_compressed(
        args.out,
        meta=np.asarray(json.dumps(meta, ensure_ascii=False)),
        **arrays,
    )

    print(f"wrote {args.out}")
    print(f"stars={meta['star_count']} cutoff={args.mag_cutoff} "
          f"size={os.path.getsize(args.out) / 1024.0:.1f} KiB")
    print(f"bins={len(edges) - 1} edges={edges[0]:.1f}..{edges[-1]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
