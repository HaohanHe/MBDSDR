# -*- coding: utf-8 -*-
"""Web 产品生成链守卫：重跑四个生成器，比对产物是否漂移。

    python tests/verify_build_json.py --check

做法：先按 sha256 备份生成器会写的全部输出 → 逐个重跑生成器 → 把
`generatedAt` / `repo.head`（以及自动生成文档里的「生成时间 / 仓库基线」）
替换成占位符后**逐字节比对** → 无论成败都按原字节恢复工作区。

返回码：
  0  产物与重跑结果一致（只允许上述豁免字段变化）
  1  生成器失败或产物漂移（drift）
  2  参数 / 备份恢复失败

不依赖网络：星表用本地 `data/hygdata_v41.csv`，其余输入都是本仓源码。
运行期间不要有其它线同时修改 `build/*.json` 或 `src/05|12|13-*.js`，
否则恢复步骤会覆盖对方的并发写入。
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT  # noqa: E402

GENERATORS = [
    "build/sync_from_repo.py",
    "build/sync_sky_catalog.py",
    "build/sync_sat_tracks.py",
    "build/gen_astro_golden.py",
]

# 生成器会写的全部输出；恢复时按这份清单逐字节写回
OUTPUTS = [
    "build/repo-facts.json",
    "src/05-repo-facts.js",
    "docs/09-repo-alignment.md",
    "build/sky-catalog.json",
    "src/12-sky-catalog.js",
    "docs/10-sky-catalog.md",
    "data/hygdata_v41.sha256",
    "build/sat-tracks.json",
    "src/13-sat-tracks.js",
    "build/astro-golden.json",
]

# 豁免字段：生成时刻与仓库基线，不是数据
VOLATILE = [
    (re.compile(r'"generatedAt"\s*:\s*"[^"]*"'), '"generatedAt":"<VOLATILE>"'),
    (re.compile(r'"head"\s*:\s*"[0-9a-f]{4,40}"'), '"head":"<VOLATILE>"'),
    (re.compile(r"生成时间：[^·\n]+"), "生成时间：<VOLATILE>"),
    (re.compile(r"仓库基线：`[^`]*`"), "仓库基线：`<VOLATILE>`"),
]

TIMEOUT = 300


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(text: str) -> str:
    for pattern, repl in VOLATILE:
        text = pattern.sub(repl, text)
    return text


def snapshot() -> dict[str, bytes | None]:
    snap = {}
    for rel in OUTPUTS:
        path = ROOT / rel
        snap[rel] = path.read_bytes() if path.is_file() else None
    return snap


def restore(snap: dict[str, bytes | None]) -> list[str]:
    """按原字节写回；返回校验失败（sha 不等）的路径清单。"""
    bad = []
    for rel, data in snap.items():
        path = ROOT / rel
        if data is None:
            if path.is_file():
                path.unlink()
            continue
        path.write_bytes(data)
        if sha(path.read_bytes()) != sha(data):
            bad.append(rel)
    return bad


def run_generators() -> list[tuple[str, bool, str]]:
    results = []
    for rel in GENERATORS:
        proc = subprocess.run(
            [sys.executable, str(ROOT / rel)], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT,
        )
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        results.append((rel, proc.returncode == 0, tail[-1] if tail else ""))
    return results


def first_diff(old: str, new: str, limit: int = 6) -> list[str]:
    lines = [
        line for line in difflib.unified_diff(
            old.splitlines(), new.splitlines(), lineterm="", n=0)
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]
    return lines[:limit]


def main() -> int:
    ap = argparse.ArgumentParser(description="重跑生成器并检查 build/*.json 是否漂移")
    ap.add_argument("--check", action="store_true",
                    help="执行重跑比对（当前仅支持该模式）")
    args = ap.parse_args()
    if not args.check:
        ap.print_help()
        return 2

    snap = snapshot()
    print("=== 重跑生成器 ===")
    drift = False
    restore_bad: list[str] = []
    try:
        results = run_generators()
        for rel, ok, tail in results:
            print(f"[{'OK ' if ok else 'FAIL'}] {rel} :: {tail}")
            if not ok:
                drift = True

        print("\n=== 产物比对（豁免 generatedAt / repo.head）===")
        for rel in OUTPUTS:
            old = snap[rel]
            path = ROOT / rel
            if old is None:
                print(f"  MISSING {rel}（审计前产物不在位）")
                drift = True
                continue
            if not path.is_file():
                print(f"  MISSING {rel}（重跑后未生成）")
                drift = True
                continue
            new = path.read_bytes()
            if normalize(old.decode("utf-8")) == normalize(new.decode("utf-8")):
                print(f"  OK      {rel}")
                continue
            drift = True
            print(f"  DRIFT   {rel}（sha {sha(old)[:12]} -> {sha(new)[:12]}）")
            for line in first_diff(normalize(old.decode("utf-8")),
                                   normalize(new.decode("utf-8"))):
                print("    " + line[:200])
    finally:
        restore_bad = restore(snap)

    if restore_bad:
        print("\n恢复失败：", ", ".join(restore_bad), file=sys.stderr)
        return 2
    print("\n已按原字节恢复工作区")

    print("\n结论：", "DRIFT" if drift else "与生成器一致")
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
