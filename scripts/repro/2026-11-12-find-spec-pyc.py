#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""find_spec 能不能看见「只有 .pyc 的子树」—— 冻结包门槛的行为依据。

背景：启动自检把 scipy 系从真 import 换成 ``find_spec`` 存在性检查，图的是
首帧前省 0.65 s。但如果 ``find_spec`` 看不见只留 ``.pyc`` 的包（PyInstaller
noarchive 构建正是这种形态），门槛会在产物上误判「缺件」并中止启动——比改前
更糟。本脚本在源码环境把三种落盘形态造出来，用产品里同一套调用去探：

  legacy_pyc ：``compileall -b`` 后删 ``.py`` → 包目录里直接放 ``__init__.pyc``
               / ``sub/mod.pyc``（sourceless distribution，产物最常见形态）
  pycache_pyc：``compileall`` 默认布局 → 只剩 ``__pycache__/*.pyc``、没有
               `````__init__.pyc`` 这种可导入的 legacy 文件（Python 3 不认）
  corrupt_pyc：legacy 布局，但把叶子 ``.pyc`` 截断成半截（模拟拷贝中断）
  missing    ：什么都没有（对照）

每个 case 记：``find_spec("pkg.sub.mod")`` 是否发现、原文件位置、真 import 是否
成功、失败时异常类型。判据是「门槛拦得住真缺件、放得过 .pyc-only」——两者都成立
才说明换 find_spec 没把产物坑挖出来。

用法::

    python scripts/repro/2026-11-12-find-spec-pyc.py
    python scripts/repro/2026-11-12-find-spec-pyc.py --out docs/perf-evidence/2026-11-12/find-spec-pyc.json
"""

import argparse
import compileall
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EVID = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-12")

#: 门槛探针耗时子程序：两种调用各起独立解释器（scipy 首次 import 一次性，同进程测不了第二遍）。
_COST_PROBE = r"""
import importlib, importlib.util, json, os, sys, time
mode, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(root, "desktop"))
main = importlib.import_module("main")
ARMS = ["scipy", "scipy.signal",
        "scipy._external.array_api_compat.common",
        "scipy._external.array_api_compat.numpy.fft"]
rows = []
for name in ARMS:
    t = time.perf_counter()
    ok = (main._probe_module_spec(name) if mode == "gate"
          else importlib.util.find_spec(name) is not None)
    rows.append([name, bool(ok), round((time.perf_counter() - t) * 1000, 2)])
print(json.dumps({"mode": mode, "rows": rows,
                  "scipy_imported": "scipy" in sys.modules,
                  "total_ms": round(sum(r[2] for r in rows), 2)}))
"""


def _probe_cost(repeat: int = 3) -> dict:
    """门槛探针耗时：产品门槛（目录遍历）vs 标准 find_spec（会 import scipy 父包）。"""
    out = {"repeat": repeat, "arms": _COST_PROBE_ARMS, "runs": []}
    for mode in ("gate", "stdlib"):
        for _ in range(repeat):
            proc = subprocess.run([sys.executable, "-c", _COST_PROBE, mode, ROOT],
                                  capture_output=True, text=True)
            if proc.returncode != 0:
                out.setdefault("errors", []).append(proc.stderr.strip()[-400:])
                continue
            out["runs"].append(json.loads(proc.stdout))
    for mode in ("gate", "stdlib"):
        rows = [r for r in out["runs"] if r["mode"] == mode]
        if not rows:
            continue
        totals = sorted(r["total_ms"] for r in rows)
        out[f"{mode}_total_ms"] = {
            "runs": [r["total_ms"] for r in rows],
            "median": totals[len(totals) // 2],
            "min": totals[0],
            "max": totals[-1],
        }
        out[f"{mode}_scipy_imported"] = [r["scipy_imported"] for r in rows]
    return out


_COST_PROBE_ARMS = [
    "scipy",
    "scipy.signal",
    "scipy._external.array_api_compat.common",
    "scipy._external.array_api_compat.numpy.fft",
]


def _write_pkg(base: str, name: str) -> None:
    pkg = os.path.join(base, name)
    os.makedirs(os.path.join(pkg, "sub"), exist_ok=True)
    with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as f:
        f.write("PARENT = 1\n")
    with open(os.path.join(pkg, "sub", "__init__.py"), "w", encoding="utf-8") as f:
        f.write("SUB = 1\n")
    with open(os.path.join(pkg, "sub", "mod.py"), "w", encoding="utf-8") as f:
        f.write("VALUE = 42\n")


def _strip_sources(path: str) -> None:
    for root, _dirs, files in os.walk(path):
        for fn in files:
            if fn.endswith(".py"):
                os.remove(os.path.join(root, fn))


def _layout(base: str, name: str, kind: str) -> str:
    """按 kind 造一种落盘形态，返回可加进 sys.path 的目录。"""
    site = os.path.join(base, kind)
    os.makedirs(site, exist_ok=True)
    if kind == "missing":                # 对照：目录存在，但包里什么都没有
        return site
    _write_pkg(site, name)
    legacy = kind in ("legacy_pyc", "corrupt_pyc")
    compileall.compile_dir(os.path.join(site, name), quiet=1, legacy=legacy)
    if kind in ("legacy_pyc", "corrupt_pyc"):
        _strip_sources(os.path.join(site, name))
    elif kind == "pycache_pyc":
        _strip_sources(os.path.join(site, name))
        # 把 __pycache__ 的名字改成 Python 3 不认的形态之外保持原样：
        # 这里保留 __pycache__ 布局不动，正是要证明它不可导入。
    if kind == "corrupt_pyc":
        leaf = os.path.join(site, name, "sub", "mod.pyc")
        size = os.path.getsize(leaf)
        with open(leaf, "r+b") as f:
            f.truncate(max(1, size // 3))
    return site


def _files_under(path: str) -> list:
    out = []
    for root, _dirs, files in os.walk(path):
        for fn in files:
            full = os.path.join(root, fn)
            out.append(os.path.relpath(full, path).replace(os.sep, "/"))
    return sorted(out)


def _product_gate():
    """产品门槛本体 desktop/main.py:_probe_module_spec（目录遍历 + 标准 find_spec 兜底）。"""
    if os.path.join(ROOT, "desktop") not in sys.path:
        sys.path.insert(0, os.path.join(ROOT, "desktop"))
    return importlib.import_module("main")


def _probe(module: str, gate) -> dict:
    """标准 find_spec 的定位信息 + 产品门槛的实际返回值。"""
    rec = {"module": module}
    try:
        rec["product_gate"] = bool(gate._probe_module_spec(module))
    except Exception as exc:                      # noqa: BLE001
        rec["product_gate"] = None
        rec["product_gate_error"] = f"{type(exc).__name__}: {exc}"
    try:
        spec = importlib.util.find_spec(module)
        rec["spec_found"] = spec is not None
        rec["spec_origin"] = getattr(spec, "origin", None) if spec else None
        rec["spec_loader"] = (type(spec.loader).__name__
                              if spec and spec.loader else None)
    except Exception as exc:                      # noqa: BLE001
        rec["spec_found"] = False
        rec["spec_error"] = f"{type(exc).__name__}: {exc}"
    try:
        mod = importlib.import_module(module)
        rec["import_ok"] = True
        rec["import_value"] = getattr(mod, "VALUE", None)
    except Exception as exc:                      # noqa: BLE001
        rec["import_ok"] = False
        rec["import_error"] = f"{type(exc).__name__}: {exc}"
    return rec


def _run_case(kind: str, base: str, gate) -> dict:
    name = f"mbdsdr_pyc_{kind}"
    site = _layout(base, name, kind)
    sys.path.insert(0, site)
    try:
        module = f"{name}.sub.mod"
        rec = _probe(module, gate)
        parent = _probe(f"{name}.sub", gate)
    finally:
        sys.path.remove(site)
        for mod in [m for m in list(sys.modules) if m == name
                    or m.startswith(name + ".")]:
            sys.modules.pop(mod, None)
    return {
        "case": kind,
        "site": site,
        "tree": _files_under(site),
        "top_level_probe": parent,
        "leaf_probe": rec,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(EVID, "find-spec-pyc.json"))
    ap.add_argument("--no-cost", action="store_true",
                    help="跳过门槛耗时段（只验 .pyc 可见性）")
    args = ap.parse_args()

    base = tempfile.mkdtemp(prefix="mbdsdr_pyc_")
    gate = _product_gate()
    try:
        cases = [_run_case(k, base, gate) for k in
                 ("legacy_pyc", "pycache_pyc", "corrupt_pyc", "missing")]
    finally:
        shutil.rmtree(base, ignore_errors=True)

    for c in cases:
        leaf = c["leaf_probe"]
        print(f"[{c['case']}] gate={leaf.get('product_gate')} "
              f"find_spec={leaf.get('spec_found')} "
              f"origin={leaf.get('spec_origin')} "
              f"loader={leaf.get('spec_loader')} "
              f"import_ok={leaf.get('import_ok')} "
              f"err={leaf.get('import_error')}")

    by = {c["case"]: c for c in cases}
    legacy = by["legacy_pyc"]["leaf_probe"]
    pycache = by["pycache_pyc"]["leaf_probe"]
    corrupt = by["corrupt_pyc"]["leaf_probe"]
    missing = by["missing"]["leaf_probe"]

    verdict = {
        "can_see_legacy_pyc": bool(legacy.get("spec_found")),
        "product_gate_sees_legacy_pyc": bool(legacy.get("product_gate")),
        "sees_pycache_only": bool(pycache.get("spec_found")),
        "product_gate_sees_pycache_only": bool(pycache.get("product_gate")),
        "corrupt_pyc": {
            "gate_sees_it": bool(corrupt.get("spec_found")),
            "product_gate_sees_it": bool(corrupt.get("product_gate")),
            "real_import_ok": bool(corrupt.get("import_ok")),
            "real_import_error": corrupt.get("import_error"),
        },
        "missing_blocks": not bool(missing.get("spec_found")),
        "product_gate_blocks_missing": not bool(missing.get("product_gate")),
        "pass": bool(legacy.get("spec_found")
                     and legacy.get("import_ok")
                     and legacy.get("product_gate")
                     and not missing.get("spec_found")
                     and not missing.get("product_gate")
                     and corrupt.get("spec_found")
                     and corrupt.get("product_gate")
                     and not corrupt.get("import_ok")
                     and not pycache.get("product_gate")),
    }

    out = {
        "kind": "find_spec 对 .pyc-only 子树的可见性（冻结包门槛依据）",
        "date": "2026-11-12",
        "head": subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                               capture_output=True, text=True).stdout.strip(),
        "python": sys.version,
        "method": {
            "compile": "compileall.compile_dir(..., legacy=True/False) 后删掉全部 .py",
            "probe": "两条都记：①产品门槛 desktop/main.py:_probe_module_spec 的实际返回值"
                     "（目录遍历 + 标准 find_spec 兜底）；②标准 importlib.util.find_spec "
                     "的 spec/loader/真 import 结果，用来看清是哪条路径起的作用",
            "cases": {
                "legacy_pyc": "pkg/__init__.pyc + pkg/sub/__init__.pyc + pkg/sub/mod.pyc",
                "pycache_pyc": "只剩 pkg/__pycache__/*.pyc，没有 legacy .pyc",
                "corrupt_pyc": "legacy 布局，叶子 .pyc 截断到 1/3",
                "missing": "目录里没有这个包（对照）",
            },
        },
        "results": cases,
        "verdict": verdict,
    }
    if not args.no_cost:
        out["probe_cost"] = _probe_cost()
        print("[cost]", json.dumps(out["probe_cost"].get("gate_total_ms"),
                                   ensure_ascii=False),
              json.dumps(out["probe_cost"].get("stdlib_total_ms"),
                         ensure_ascii=False))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[saved]", args.out)
    print("[verdict]", json.dumps(verdict, ensure_ascii=False))
    return 0 if verdict["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
