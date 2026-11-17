#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""把已有的 SigMF Recording 收进 IQ 清单（``.sigmf-meta`` → JSONL）。

为什么要有这个方向
==================

导出（``scripts/export_sigmf.py``）解决「我方样本怎么给社区读」；导入解决
「别人的/以前的 SigMF 录制怎么进我们的实验流程」。社区数据几乎都以
``.sigmf-meta`` 分发，只能手抄一遍字段的话，抄错采样率或频率就白做了。

硬口径
======

* 只搬 meta 里**真实写过**的值：datatype / 采样率 / 中心频率 / license；
  meta 没有的不补默认值（缺采样率就跳过，不按文件名猜）；
* 类别（``label``）**必须由使用者用 ``--label`` 声明**——SigMF 元数据里
  没有可靠的类别字段，工具不猜；
* license 只取 meta 里写过的，或使用者以数据拥有者身份显式给的
  ``--license``；两边都没有就跳过并在报告里点名；
* 只读 meta 与数据文件大小（用于校验「文件长度是整样本」），不改名、
  不复制、不删除、不写入数据目录；
* 生成的每条清单还会再过一遍 `iq_sample_manifest` 的契约校验，避免
  导入器自己造出不合格清单。

用法
====

    python scripts/import_sigmf.py recordings --label fm -o samples.jsonl
    python scripts/import_sigmf.py rec/foo.sigmf-meta --label bpsk --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mbdsdr_ai import iq_sample_manifest as _man  # noqa: E402
from mbdsdr_ai import iq_sigmf as _sigmf  # noqa: E402


def _collect_meta_files(paths: List[str]) -> List[str]:
    """展开目录（递归）与文件，返回排序去重后的 ``.sigmf-meta`` 列表。"""
    found: List[str] = []
    for raw in paths:
        p = os.path.abspath(os.path.expanduser(raw))
        if os.path.isdir(p):
            for base, _dirs, files in os.walk(p):
                for name in files:
                    if name.endswith(_sigmf.META_SUFFIX):
                        found.append(os.path.join(base, name))
        elif os.path.isfile(p) and p.endswith(_sigmf.META_SUFFIX):
            found.append(p)
        else:
            found.append(p)          # 交给调用方报告：路径本身有问题
    return sorted(dict.fromkeys(found))


def _unique_ids(stems: List[str]) -> List[str]:
    """按扫描顺序给重复 stem 加 ``-2``/``-3``，保证 sample_id 唯一。"""
    seen: Dict[str, int] = {}
    out: List[str] = []
    for stem in stems:
        n = seen.get(stem, 0) + 1
        seen[stem] = n
        out.append(stem if n == 1 else f"{stem}-{n}")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="把已有 SigMF Recording 收进 IQ 样本清单（JSONL）")
    ap.add_argument("paths", nargs="+",
                    help=".sigmf-meta 文件或包含它们的目录（递归扫描）")
    ap.add_argument("--label", required=True,
                    help="类别标签（必填；SigMF 元数据里没有类别，工具不猜）")
    ap.add_argument("--license", default="",
                    help="可选：以数据拥有者身份显式声明 license（meta 里"
                         "没写时才用；两边都没有的条目会被跳过）")
    ap.add_argument("-o", "--out", default="",
                    help="清单输出路径（不给则只打印报告，不写文件）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = ap.parse_args(argv)

    metas = _collect_meta_files(args.paths)
    infos: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for meta_path in metas:
        if not os.path.isfile(meta_path):
            reason = ("路径不存在" if not os.path.exists(meta_path)
                      else f"不是 {_sigmf.META_SUFFIX} 文件")
            skipped.append({"meta": meta_path, "reasons": [reason]})
            continue
        if not meta_path.endswith(_sigmf.META_SUFFIX):
            skipped.append({"meta": meta_path,
                            "reasons": [f"不是 {_sigmf.META_SUFFIX} 文件"]})
            continue
        infos.append(_sigmf.read_meta(meta_path))

    stems = [os.path.splitext(os.path.basename(i["data_path"]))[0]
             if i.get("data_path") else Path(i["path"]).stem
             for i in infos]
    ids = _unique_ids(stems)

    entries: List[Dict[str, Any]] = []
    for info, sample_id in zip(infos, ids):
        built = _sigmf.manifest_entry_from_meta(
            info, sample_id=sample_id, label=args.label,
            license_override=args.license)
        if built["entry"] is None:
            skipped.append({"meta": info["path"], "reasons": built["problems"]})
        else:
            entries.append(built["entry"])

    # 再过一遍清单契约：导入器不允许自己产出不合格条目。
    contract = _man.validate_manifest_lines(
        json.dumps(e, ensure_ascii=False) for e in entries)
    entries = contract["accepted"]
    for rej in contract["rejected"]:
        skipped.append({"meta": f"第 {rej['line']} 条清单",
                        "reasons": rej["reasons"]})

    if args.out:
        out_path = os.path.abspath(os.path.expanduser(args.out))
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
            for e in entries:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")

    if args.json:
        print(json.dumps({
            "scanned_meta": len(metas),
            "label": args.label,
            "license_override": args.license or None,
            "out": os.path.abspath(args.out) if args.out else None,
            "counts": {"imported": len(entries), "skipped": len(skipped)},
            "entries": entries,
            "skipped": skipped,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"SigMF 导入：扫描 {len(metas)} 份 meta，收进清单 "
              f"{len(entries)} 条，跳过 {len(skipped)} 条")
        for e in entries:
            print(f"- {e['sample_id']}：{e['datatype']} / "
                  f"{e['sample_rate']:g} Hz / {e['center_freq']:g} Hz / "
                  f"license={e['license']} ← {e['iq_path']}")
        for item in skipped:
            print(f"- 跳过 {item['meta']}：" + "；".join(item["reasons"]))
        if args.out:
            print(f"清单已写入：{os.path.abspath(args.out)}")
        else:
            print("未给 -o，本次只做检查，没有写清单文件。")
        print("注：label 由 --label 指定——SigMF 元数据没有类别字段，工具不猜；"
              "license 若来自 --license，那是使用者以数据拥有者身份做出的声明。")

    total = len(metas)
    return 0 if entries and not skipped and total == len(entries) else 1


if __name__ == "__main__":
    raise SystemExit(main())
