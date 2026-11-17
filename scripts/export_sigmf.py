#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""把 IQ 样本清单写成 SigMF 元数据（``<stem>.sigmf-meta``）。

为什么要有这个脚本
==================

``iq_sample_manifest`` 能判断样本算不算数，``iq_sigmf`` 能回答「这份清单
能不能写成 SigMF」——但两者都只有 Python 入口。样本数据一到位，用户需要
的是一条命令把清单落成社区能读的 ``.sigmf-meta``。

硬口径
======

* 清单没写 ``datatype`` 的样本**不导出**（``core:datatype`` 是必填，不按
  扩展名猜）；
* 只当数据文件真的存在时才写 meta；不存在就跳过并点名；
* 只写 ``<stem>.sigmf-meta``，**不改名、不复制、不删除数据文件**；数据名
  不是 ``<stem>.sigmf-data`` 就用 ``core:dataset`` 如实声明成 NCD；
* 同名 meta 默认不覆盖（``--force`` 才重写）；
* 有样本被拒绝/跳过时退出码为 1，不把「写了几份」当成全部成功。

用法
====

    python scripts/export_sigmf.py samples/manifest.jsonl --dry-run
    python scripts/export_sigmf.py samples/manifest.jsonl
    python scripts/export_sigmf.py samples/manifest.jsonl --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mbdsdr_ai import iq_sample_manifest as _man  # noqa: E402
from mbdsdr_ai import iq_sigmf as _sigmf  # noqa: E402


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="把 IQ 样本清单（JSONL）导出成 SigMF .sigmf-meta")
    ap.add_argument("manifest", help="IQ 样本清单（JSONL）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要写什么，不落盘")
    ap.add_argument("--force", action="store_true",
                    help="覆盖已存在的同名 .sigmf-meta（默认不覆盖）")
    ap.add_argument("--no-check-data", action="store_true",
                    help="不检查数据文件是否存在（默认检查，缺文件即跳过）")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出（机器可读）")
    args = ap.parse_args(argv)

    manifest = Path(args.manifest).expanduser()
    validation = _man.validate_manifest_file(str(manifest))
    result = _sigmf.export_samples(
        validation["accepted"],
        dry_run=args.dry_run,
        force=args.force,
        check_data=not args.no_check_data,
    )

    rejected = validation.get("rejected") or []
    if args.json:
        payload = {
            "manifest": str(manifest),
            "manifest_counts": validation["counts"],
            "rejected_lines": rejected,
            "dry_run": result["dry_run"],
            "counts": result["counts"],
            "written": [{
                "sample_id": e["sample_id"],
                "meta_path": e["meta_path"],
                "compliant_recording": e["compliant_recording"],
                "notes": e["notes"],
            } for e in result["written"]],
            "skipped": result["skipped"],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if rejected:
            print(_man.format_validation(validation))
            print("以上被拒绝的行不进入导出。\n")
        print(_sigmf.format_report(result))
        print(f"清单：{manifest}")

    ok = bool(result["written"]) and not rejected and not result["skipped"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
