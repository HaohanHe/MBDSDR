# SPDX-License-Identifier: MIT
"""
build_hyg_pm.py — 从 HYG v4.1 源 CSV 生成恒星自行侧车
=====================================================

只做一件事：把 HYG 源 CSV 的 ``pmra``/``pmdec``（mas/yr）抽出来，按主星表
``mbdsdr_ai/data/full_sky_hyg_v41.npz`` 的 ``hyg_id`` 顺序写成
``mbdsdr_ai/data/hyg_pm_v41.npz``。不重新挑星、不改主星表、不造数据。

安全闸
------
生成前先算源 CSV 的 SHA-256，必须与主星表 ``meta.source_file_sha256``
逐字相同；不同直接退出（码 2）并打印实际/期望值。这样不会把另一份
同名 CSV（例如 ``web/product/data`` 里的 HYG）误当成同源。

口径
----
- ``ra`` 列是小时（主星表已 ×15 转度），本脚本只用 ``id`` 与两列 PM。
- HYG 的 ``pmra`` 是 μα*（已含 cosδ），原样保存，不做 cosδ 增减。
- 空值/非法值 → NaN（运行时按静态处理）；缺失行不影响主星表可用性。
- 文件顺序 = 主星表 ``hyg_id`` 顺序；加载端会自行按 id 排序做二分。

用法
----
  python scripts/build_hyg_pm.py
  python scripts/build_hyg_pm.py --csv D:/mbdsdr/scratch/hygdata_v41.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

PM_FORMAT = "hyg-pm-v41"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CATALOG = os.path.join(REPO_ROOT, "mbdsdr_ai", "data",
                               "full_sky_hyg_v41.npz")
DEFAULT_OUT = os.path.join(REPO_ROOT, "mbdsdr_ai", "data", "hyg_pm_v41.npz")
#: 本机约定：官方源 CSV 放在仓库上一级 `scratch/`（不随仓分发）。
#: 只写相对位置，不写死盘符——换机器/换目录仍可用 `--csv` 或环境变量指定。
FALLBACK_CSV = os.path.join(REPO_ROOT, "..", "scratch", "hygdata_v41.csv")


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


def _csv_candidates(explicit: Optional[str]) -> List[str]:
    if explicit:
        return [explicit]
    out: List[str] = []
    env = os.environ.get("MBDSDR_HYG_CSV")
    if env:
        out.append(env)
    out.append(FALLBACK_CSV)
    return out


def _find_csv(explicit: Optional[str]) -> Optional[str]:
    for path in _csv_candidates(explicit):
        if os.path.isfile(path):
            return os.path.abspath(path)
    return None


def _read_pm_lookup(csv_path: str) -> Dict[int, tuple]:
    """读 CSV，返回 {hyg_id: (pmra_mas, pmdec_mas)}（缺失/非法 → NaN）。"""
    lookup: Dict[int, tuple] = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            source_id = (row.get("id") or "").strip()
            if not source_id:
                continue
            try:
                hyg_id = int(source_id)
            except ValueError:
                continue
            lookup[hyg_id] = (_float_or_nan(row.get("pmra")),
                              _float_or_nan(row.get("pmdec")))
    return lookup


def build(csv_path: str, catalog_path: str, out_path: str) -> int:
    with np.load(catalog_path, allow_pickle=False) as data:
        meta = json.loads(str(data["meta"].item()))
        hyg_id = np.asarray(data["hyg_id"], dtype=np.int32)

    expected = str(meta.get("source_file_sha256") or "")
    actual = _sha256_file(csv_path)
    if actual != expected:
        print("HYG 源 CSV SHA-256 与主星表 meta 不一致，拒绝生成。")
        print(f"  csv      = {csv_path}")
        print(f"  actual   = {actual}")
        print(f"  expected = {expected or '(主星表 meta 缺 source_file_sha256)'}")
        return 2

    lookup = _read_pm_lookup(csv_path)
    pmra = np.full(len(hyg_id), np.nan, dtype=np.float32)
    pmdec = np.full(len(hyg_id), np.nan, dtype=np.float32)
    for i, hid in enumerate(hyg_id):
        pair = lookup.get(int(hid))
        if pair is None:
            continue
        pmra[i] = pair[0]
        pmdec[i] = pair[1]

    missing = int(np.count_nonzero(~np.isfinite(pmra)
                                   | ~np.isfinite(pmdec)))
    meta_out = {
        "format": PM_FORMAT,
        "source_csv_sha256": actual,
        "epoch": "J2000.0",
        "units": "mas/yr",
        "row_count": int(len(hyg_id)),
        "missing_count": missing,
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    np.savez_compressed(
        out_path,
        meta=np.asarray(json.dumps(meta_out, ensure_ascii=False)),
        hyg_id=hyg_id,
        pmra_mas=pmra,
        pmdec_mas=pmdec,
    )

    print(f"wrote {out_path}")
    print(f"rows={len(hyg_id)} missing={missing} "
          f"size={os.path.getsize(out_path) / 1024.0:.1f} KiB")
    print("examples (hyg_id, pmra_mas/yr, pmdec_mas/yr):")
    for i in range(min(3, len(hyg_id))):
        print(f"  {int(hyg_id[i])}, {float(pmra[i]):.3f}, {float(pmdec[i]):.3f}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default=None,
                    help="HYG v4.1 源 CSV（默认按环境变量/约定路径查找）")
    ap.add_argument("--catalog", default=DEFAULT_CATALOG,
                    help="主星表 npz（默认 mbdsdr_ai/data/full_sky_hyg_v41.npz）")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="输出侧车 npz（默认 mbdsdr_ai/data/hyg_pm_v41.npz）")
    args = ap.parse_args(argv)

    csv_path = _find_csv(args.csv)
    if csv_path is None:
        print("找不到 HYG 源 CSV；试过：")
        for path in _csv_candidates(args.csv):
            print(f"  {path}")
        return 2
    if not os.path.isfile(args.catalog):
        print(f"找不到主星表: {args.catalog}")
        return 2
    return build(csv_path, args.catalog, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
