# -*- coding: utf-8 -*-
"""Web 产品生成链守卫：重跑四个生成器，比对产物是否漂移。

    python tests/verify_build_json.py --check

流程：先把生成器会写的全部输出按 sha256 备份到 `.build-json-backup/`
（manifest + 原字节 + READY 哨兵）→ 重跑四个生成器 → 把 `generatedAt` /
`repo.head`（以及自动生成文档里的「生成时间 / 仓库基线」）替换成占位符后
**逐字节比对** → `finally` 按备份恢复并校验 sha。

进程级保护：若本次进程被 kill（`finally` 不会执行），`.build-json-backup/`
会连同 READY 哨兵留在磁盘上；下次运行会先自动恢复，再继续检查。也可手工恢复：

    python tests/verify_build_json.py --restore

返回码：
  0  产物与重跑结果一致（只允许上述豁免字段变化）
  1  生成器失败或产物漂移（drift）
  2  参数 / 备份恢复失败

不依赖网络：星表用本地 `data/hygdata_v41.csv`，其余输入都是本仓源码。
运行期间不要有其它线同时修改 `build/*.json` 或 `src/05|12|13-*.js`。

`--simulate-crash` 是测试开关：跑完第一个生成器后 `os._exit(137)`，用于反证
「中断后备份仍可恢复」。**只有显式传该参数才会进入这个分支；日常 `--check`
路径永不触发 `os._exit`，发布前不会误触。**
"""
from __future__ import annotations

import argparse
import datetime as _dt
import difflib
import hashlib
import json
import os
import re
import shutil
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

BACKUP_DIR = ROOT / ".build-json-backup"
BACKUP_FILES = BACKUP_DIR / "files"
MANIFEST = BACKUP_DIR / "manifest.json"
READY = BACKUP_DIR / "READY"
TIMEOUT = 300


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize(text: str) -> str:
    for pattern, repl in VOLATILE:
        text = pattern.sub(repl, text)
    return text


def backup_path(rel: str) -> Path:
    return BACKUP_FILES / rel.replace("/", "__")


def snapshot() -> dict[str, dict]:
    """记录生成器输出的原始状态，并落盘备份。"""
    manifest: dict[str, dict] = {"createdAt": _dt.datetime.now().isoformat(timespec="seconds"),
                                 "files": {}}
    BACKUP_FILES.mkdir(parents=True, exist_ok=True)
    for rel in OUTPUTS:
        src = ROOT / rel
        if src.is_file():
            data = src.read_bytes()
            backup_path(rel).write_bytes(data)
            manifest["files"][rel] = {"exists": True, "sha256": sha(data)}
        else:
            manifest["files"][rel] = {"exists": False, "sha256": ""}
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    # 复验备份副本，确认全部落盘后才允许开始重跑
    for rel, info in manifest["files"].items():
        if info["exists"] and sha(backup_path(rel).read_bytes()) != info["sha256"]:
            raise RuntimeError(f"备份校验失败：{rel}")
    READY.write_text("ok\n", encoding="utf-8")
    return manifest


def restore_from_backup() -> bool:
    """按 manifest 写回原字节；成功后才删除备份目录。"""
    if not READY.is_file() or not MANIFEST.is_file():
        return True  # 没有备份（或从未开始），无需恢复
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    bad = []
    for rel, info in manifest["files"].items():
        dst = ROOT / rel
        if info["exists"]:
            data = backup_path(rel).read_bytes()
            dst.write_bytes(data)
            if sha(dst.read_bytes()) != info["sha256"]:
                bad.append(rel)
        elif dst.is_file():
            dst.unlink()
    if bad:
        print("恢复校验失败：", ", ".join(bad), file=sys.stderr)
        print("备份仍保留在", BACKUP_DIR, "可重试 --restore", file=sys.stderr)
        return False
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    return True


def recover_stale() -> bool:
    """启动时清理上次被 kill 留下的备份：能恢复就恢复，不能就报错退出。"""
    if not BACKUP_DIR.exists():
        return True
    if READY.is_file():
        print("[恢复] 检测到上次中断留下的备份，先按原字节恢复…")
        return restore_from_backup()
    # 没有 READY：说明备份没写完，生成器尚未运行，直接丢弃即可
    shutil.rmtree(BACKUP_DIR, ignore_errors=True)
    return True


def run_generators(simulate_crash: bool = False) -> list[tuple[str, bool, str]]:
    results = []
    for index, rel in enumerate(GENERATORS):
        proc = subprocess.run(
            [sys.executable, str(ROOT / rel)], cwd=ROOT,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=TIMEOUT,
        )
        tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
        results.append((rel, proc.returncode == 0, tail[-1] if tail else ""))
        if simulate_crash and index == 0:
            # 模拟硬退出：不执行 finally，验证磁盘备份仍可恢复
            os._exit(137)
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
                    help="执行重跑比对（发布前用这个）")
    ap.add_argument("--restore", action="store_true",
                    help="只按 .build-json-backup/ 恢复上次中断留下的产物")
    ap.add_argument("--simulate-crash", action="store_true",
                    help="测试开关：跑完第一个生成器后硬退出，验证备份可恢复"
                         "（仅显式传参时触发；--check 不会走到该分支）")
    args = ap.parse_args()

    if args.restore:
        if not BACKUP_DIR.exists():
            print("没有待恢复的备份")
            return 0
        return 0 if restore_from_backup() else 2
    if not args.check:
        ap.print_help()
        return 2
    if not recover_stale():
        return 2

    backup = snapshot()
    print("=== 重跑生成器 ===")
    drift = False
    try:
        for rel, ok, tail in run_generators(args.simulate_crash):
            print(f"[{'OK ' if ok else 'FAIL'}] {rel} :: {tail}")
            if not ok:
                drift = True

        print("\n=== 产物比对（豁免 generatedAt / repo.head）===")
        for rel in OUTPUTS:
            info = backup["files"][rel]
            path = ROOT / rel
            if not info["exists"]:
                print(f"  MISSING {rel}（审计前产物不在位）")
                drift = True
                continue
            if not path.is_file():
                print(f"  MISSING {rel}（重跑后未生成）")
                drift = True
                continue
            old = backup_path(rel).read_bytes()
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
        restored = restore_from_backup()

    if not restored:
        print("恢复失败：备份保留在", BACKUP_DIR,
              "；可重试 python tests/verify_build_json.py --restore",
              file=sys.stderr)
        return 2
    print("\n已按备份恢复原字节")
    print("\n结论：", "DRIFT" if drift else "与生成器一致")
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
