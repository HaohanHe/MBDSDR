#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""生成 Tripo 3D 资产（GLB）的命令行薄壳。

用法：
    python scripts/tripo_asset_gen.py --prompt "..." [--out PATH] [--model ...]
    python scripts/tripo_asset_gen.py --manifest PATH --out-dir DIR [--model ...]

单条模式与批量模式互斥。批量模式按清单**串行**逐条生成（Tripo 有并发与
额度限制，不并发抢跑），每条打印一行结果，最后打印汇总。

API Key 只从环境变量 ``TRIPO_API_KEY`` 读取；默认不联网，需显式设置
``MBDSDR_ALLOW_NETWORK=1`` 才会真正调用 Tripo API。

退出码：
    0  成功（单条：打印生成的 .glb 绝对路径；批量：全部成功）
    2  输入非法（空 prompt、非法路径/超时、清单非法、模式互斥/缺参等）
    3  生成失败（无 Key、离线、提交/轮询/下载失败、超时；批量里有任意失败
       或有条目被 max_models 截断）
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.tripo_assets import (  # noqa: E402
    DEFAULT_MODEL,
    generate_batch,
    generate_model,
    load_manifest,
    slugify_prompt,
)


def _default_out(prompt: str) -> Path:
    return Path("output") / "tripo" / f"{slugify_prompt(prompt)}.glb"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="用 Tripo 文生 3D 生成 GLB 资产（默认离线，需显式允许联网）")
    parser.add_argument("--prompt", default=None,
                        help="单条模式的文字描述，例如「一台复古短波电台」")
    parser.add_argument("--out", default=None, metavar="PATH",
                        help="单条模式的输出 .glb 路径（缺省 output/tripo/<prompt>.glb）")
    parser.add_argument("--manifest", default=None, metavar="PATH",
                        help="批量模式的清单 JSON（含 version/items）")
    parser.add_argument("--out-dir", default=None, metavar="DIR",
                        help="批量模式的输出目录（输出 <DIR>/<name>.glb）")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Tripo 模型版本（缺省 {DEFAULT_MODEL}）")
    return parser


def _run_batch(args: argparse.Namespace) -> int:
    entries, error = load_manifest(args.manifest)
    if error is not None:
        print(f"输入非法（{error['error']}）：{error['reason']}", file=sys.stderr)
        return 2

    result = generate_batch(entries, out_dir=args.out_dir, model=args.model)
    if not result.get("results") and result.get("error"):
        # 批量总校验失败（目录/max_models/条目等），一条都没跑。
        code = str(result.get("error") or "unknown")
        reason = str(result.get("reason") or "未知原因")
        print(f"输入非法（{code}）：{reason}", file=sys.stderr)
        return 2

    for item in result.get("results", []):
        if item.get("ok"):
            print(f"[ok] {item['name']} → {item['path']}")
        else:
            print(f"[fail] {item['name']} → {item.get('reason') or '未知原因'}")

    print(f"成功 {result.get('succeeded', 0)} / "
          f"失败 {result.get('failed', 0)} / "
          f"跳过 {result.get('skipped', 0)}")
    return 0 if result.get("ok") else 3


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    batch_requested = args.manifest is not None or args.out_dir is not None
    if batch_requested:
        if args.manifest is None or args.out_dir is None:
            parser.error("批量模式必须同时提供 --manifest 与 --out-dir")
        if args.prompt is not None or args.out is not None:
            parser.error("--manifest/--out-dir 与 --prompt/--out 互斥，不能同时使用")
        return _run_batch(args)

    if args.prompt is None:
        parser.error("单条模式需要 --prompt（或改用 --manifest/--out-dir 批量模式）")

    out_path = args.out if args.out is not None else _default_out(args.prompt)
    result = generate_model(args.prompt, out_path=out_path, model=args.model)

    if result.get("ok"):
        print(result["path"])
        return 0

    code = str(result.get("error") or "unknown")
    reason = str(result.get("reason") or "未知原因")
    if code.startswith("invalid_"):
        print(f"输入非法（{code}）：{reason}", file=sys.stderr)
        return 2
    print(f"生成失败（{code}）：{reason}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
