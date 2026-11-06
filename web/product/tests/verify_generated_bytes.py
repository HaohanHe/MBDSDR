# -*- coding: utf-8 -*-
"""在 HEAD 快照里重跑 scaffold / build，逐字节比对三个生成物。

覆盖 run_all.py 第 5–7 步的产物：
  - index.html            <- build/scaffold.py
  - src/70-docs-data.js   <- build/build.py::compile_docs
  - dist/index.html       <- build/build.py::build_dist

做法：`git archive HEAD` 取 web/product 快照到临时目录，在快照里跑
scaffold.py + compile_docs + build_dist，再与快照里已入库的字节比对。
**不读活动工作区**，因此接线线在途修改不会污染结果。

豁免（仅一项，逐字节归一后再比）：
  - dist/index.html 第 1 行 HTML 注释里的构建时刻 `YYYY-MM-DD HH:MM`：
    build.py 每次写入当前时间，人为不可复现；该行只是注释，不影响 DOM/脚本。

依赖：git、Python 标准库；**不联网、不依赖 node**。
用法：python tests/verify_generated_bytes.py（退出码 0=一致，1=漂移，2=环境问题）
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
TIMEOUT = 180
TARGETS = ("index.html", "src/70-docs-data.js", "dist/index.html")
BANNER_RE = re.compile(
    r"<!-- MBDSDR product build · 自包含单文件 · "
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} -->")


def repo_root() -> Path:
    p = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError("git rev-parse 失败：" + (p.stderr or "").strip()[:200])
    return Path(p.stdout.strip()).resolve()


def normalize(name: str, data: bytes) -> bytes:
    if name != "dist/index.html":
        return data
    text = data.decode("utf-8")
    return BANNER_RE.sub("<!-- MBDSDR product build · 自包含单文件 · <TIME> -->",
                         text, count=1).encode("utf-8")


def run_step(label: str, cmd: list[str], cwd: Path) -> None:
    t0 = time.perf_counter()
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=TIMEOUT)
    dt = time.perf_counter() - t0
    print(f"  [{label}] {dt:.1f}s rc={p.returncode}")
    if p.returncode:
        tail = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()[-6:]
        print("    " + "\n    ".join(tail))
        raise RuntimeError(f"{label} 失败")


def first_diff(a: bytes, b: bytes) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            lo = max(0, i - 40)
            return (f"offset {i} (a={len(a)}B b={len(b)}B)\n"
                    f"  arch: {a[lo:i + 40]!r}\n"
                    f"  regen: {b[lo:i + 40]!r}")
    return f"length differs (a={len(a)}B b={len(b)}B)"


def main() -> int:
    t_all = time.perf_counter()
    try:
        repo = repo_root()
        prod_rel = ROOT.relative_to(repo).as_posix()
    except Exception as exc:                      # noqa: BLE001
        print("ENV: " + str(exc))
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="mbdsdr-genbytes-"))
    try:
        zpath = tmp / "head.zip"
        p = subprocess.run(["git", "-C", str(repo), "archive", "--format=zip",
                            "-o", str(zpath), "HEAD", "--", prod_rel],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TIMEOUT)
        if p.returncode:
            print("ENV: git archive 失败：" + (p.stderr or "").strip()[:200])
            return 2
        with zipfile.ZipFile(zpath) as zf:
            zf.extractall(tmp / "snap")
        snap = tmp / "snap" / prod_rel
        if not snap.is_dir():
            print(f"ENV: 快照里找不到 {prod_rel}")
            return 2

        archived = {}
        for name in TARGETS:
            path = snap / name
            if not path.is_file():
                print(f"ENV: HEAD 快照缺 {name}（该产物可能还没入库）")
                return 2
            archived[name] = path.read_bytes()

        print(f"HEAD 快照：{snap}")
        run_step("scaffold", [PY, "build/scaffold.py"], snap)
        code = ("import sys; sys.path.insert(0, 'build'); import build; "
                "build.compile_docs(); build.build_dist()")
        run_step("build", [PY, "-c", code], snap)

        bad = []
        for name in TARGETS:
            regen = (snap / name).read_bytes()
            a = normalize(name, archived[name])
            b = normalize(name, regen)
            if a == b:
                print(f"  OK   {name} ({len(a)}B)")
            else:
                print(f"  DRIFT {name}")
                print("    " + first_diff(a, b).replace("\n", "\n    "))
                bad.append(name)

        dt = time.perf_counter() - t_all
        if bad:
            print(f"\nRESULT: DRIFT {', '.join(bad)}（{dt:.1f}s）")
            return 1
        print(f"\nRESULT: OK · {len(TARGETS)} 个生成物逐字节一致（{dt:.1f}s）")
        return 0
    except subprocess.TimeoutExpired as exc:
        print(f"ENV: 步骤超时 {exc}")
        return 2
    except Exception as exc:                      # noqa: BLE001
        print("ENV: " + str(exc))
        return 2
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
