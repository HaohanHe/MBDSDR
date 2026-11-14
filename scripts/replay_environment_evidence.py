#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""只读复算环境证据快照：不联网、不重新取数、不读当前修正表。

用法::

    python scripts/replay_environment_evidence.py <evidence.json>

复算以快照里的 ``captured_at_utc`` 为参考时刻，修正表系数取自快照内的
``inputs.correction_table``，因此隔天再跑结果逐字段一致。

退出码：0 = 复算与记录一致；1 = 快照缺项/被改动/不可读。
"""
from __future__ import annotations

import argparse
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mbdsdr_ai.environment_evidence import load, replay  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="环境证据快照只读复算")
    ap.add_argument("evidence", help="environment_evidence JSON 路径")
    args = ap.parse_args(argv)
    path = pathlib.Path(args.evidence)
    if not path.is_file():
        print(f"[FAIL] 文件不存在：{path}")
        return 1
    try:
        payload = load(path)
    except (OSError, ValueError) as exc:
        print(f"[FAIL] 快照不可读：{exc}")
        return 1
    problems, summary = replay(payload)
    if problems:
        print(f"[FAIL] {path}")
        for item in problems:
            print(f"  - {item}")
        return 1
    print(f"[PASS] {summary}")
    print(f"[PASS] 复算与记录一致：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
