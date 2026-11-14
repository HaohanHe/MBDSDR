# SPDX-License-Identifier: MIT
"""从 R5 证据快照**独立复算**同一批过境，不联网、不再取 TLE。

用法::

    python scripts/hw_acceptance/recompute_r5_evidence.py docs/hw-acceptance/evidence/<UTC>

只吃快照里的三件东西——``manifest.json``（TLE 原文+来源+历元、站址、窗口、
步长、门限、起始 UTC 秒）、``tle.txt``（TLE 原文）与 ``passes.json``（实跑结果）。
复算链路与实跑相同（产品 ``predict_passes`` + skyfield 参考），但**输入全来自
快照**：注入快照记录的 TLE、用快照记录的 ``run_unix`` 作窗口起点。

退出码：0 = 复算结果与快照一致；1 = 有差异或快照本身不齐全/被改动。
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for _p in (str(REPO_ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ring5_satellite_passes as r5  # noqa: E402


def _diff(actual, expected, where: str, problems: list, tol: float = 1e-6) -> None:
    """逐字段对拍；数字按极小容差（记录已四舍五入到 0.1°/1e-3s）。"""
    if isinstance(actual, dict) and isinstance(expected, dict):
        for k in sorted(set(actual) | set(expected)):
            if k not in actual:
                problems.append(f"{where}.{k}: 实跑有、复算无")
            elif k not in expected:
                problems.append(f"{where}.{k}: 复算有、实跑无")
            else:
                _diff(actual[k], expected[k], f"{where}.{k}", problems, tol)
        return
    if isinstance(actual, list) and isinstance(expected, list):
        if len(actual) != len(expected):
            problems.append(f"{where}: 条数 {len(actual)} != {len(expected)}")
        for i, (a, e) in enumerate(zip(actual, expected)):
            _diff(a, e, f"{where}[{i}]", problems, tol)
        return
    if isinstance(actual, bool) or isinstance(expected, bool):
        if actual != expected:
            problems.append(f"{where}: {actual!r} != {expected!r}")
        return
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        if not math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tol):
            problems.append(f"{where}: {actual!r} != {expected!r}")
        return
    if actual != expected:
        problems.append(f"{where}: {actual!r} != {expected!r}")


def _check_frozen_params(man: dict, problems: list) -> None:
    """门限/窗口/步长/二分容差必须与产品常量一致（不许为了过用例改动）。"""
    from mbdsdr_ai.orbit import PASS_BISECT_TOL_S, PASS_COARSE_STEP_S, PASS_FINE_STEP_S

    if float(man.get("min_elevation_deg", -1)) != float(r5.MIN_EL):
        problems.append(
            f"快照门限 {man.get('min_elevation_deg')} != 产品门限 {r5.MIN_EL}")
    if float(man.get("window_hours", -1)) != float(r5.WINDOW_H):
        problems.append(f"快照窗口 {man.get('window_hours')} != 产品窗口 {r5.WINDOW_H}")
    params = man.get("params") or {}
    expected = {
        "coarse_step_s": PASS_COARSE_STEP_S,
        "fine_step_s": PASS_FINE_STEP_S,
        "bisect_tol_s": PASS_BISECT_TOL_S,
        "skyfield_ref_step_s": r5.SKYFIELD_REF_STEP_S,
    }
    for k, v in expected.items():
        if float(params.get(k, -1)) != float(v):
            problems.append(f"快照 {k}={params.get(k)} != 产品 {v}")


def replay(path) -> tuple[list, str]:
    """读快照并复算；返回 ``(问题列表, 摘要文本)``。"""
    path = pathlib.Path(path)
    problems = r5.verify_evidence(path)
    if problems:
        return problems, "快照不完整，拒绝复算"

    from mbdsdr_ai.orbit import predict_passes

    man = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    payload = json.loads((path / "passes.json").read_text(encoding="utf-8"))
    _check_frozen_params(man, problems)

    tle_recs = {}
    for catnr, meta in (man.get("tle") or {}).items():
        tle_recs[int(catnr)] = {
            "catnr": int(catnr),
            "name": meta.get("name"),
            "line1": meta["line1"],
            "line2": meta["line2"],
            "source": meta.get("source"),
            "fetched_at": meta.get("fetched_at_unix"),
            "refresh_status": meta.get("refresh_status"),
            "refresh_error": meta.get("refresh_error", ""),
        }
    if not tle_recs:
        if man.get("status") != "SKIP":
            problems.append("非 SKIP 快照没有 TLE，无法复算")
        return problems, "快照为 SKIP（TLE 不可得），无过境可复算"

    st = man["station"]
    run_unix = float(man["run_unix"])
    passes = predict_passes(
        st["lat"], st["lon"], st["alt_km"], man["window_hours"],
        man["min_elevation_deg"], satellite_type="all",
        start_unix=run_unix, tle_records=tle_recs)

    ref = {}
    for norad, rec in tle_recs.items():
        got = r5._skyfield_pass(rec["line1"], rec["line2"],
                                str(rec.get("name", norad)),
                                st["lat"], st["lon"], st["alt_m"],
                                run_unix, man["window_hours"],
                                man["min_elevation_deg"])
        if got:
            ref[r5._norm(rec.get("name", ""))] = got

    target_recs = {n: tle_recs[n] for n in r5.TARGETS if n in tle_recs}
    findings, _checks = r5._build_findings(target_recs, passes, ref, run_unix)

    _diff(r5._pass_rows(passes, findings), payload.get("passes"), "passes", problems)
    _diff(ref, payload.get("reference"), "reference", problems)
    _diff(findings, payload.get("findings"), "findings", problems)
    if man.get("pass_count") != len(passes):
        problems.append(
            f"manifest.pass_count={man.get('pass_count')} != 复算 {len(passes)}")
    summary = (f"复算 {len(passes)} 条过境 / {len(tle_recs)} 颗 TLE / "
               f"{len(ref)} 条独立参考 / {len(findings)} 颗目标")
    return problems, summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R5 证据快照独立复算")
    ap.add_argument("evidence_dir", help="docs/hw-acceptance/evidence/<UTC> 目录")
    args = ap.parse_args(argv)
    path = pathlib.Path(args.evidence_dir)
    if not path.is_dir():
        print(f"[FAIL] 目录不存在：{path}")
        return 1
    problems, summary = replay(path)
    if problems:
        print(f"[FAIL] {path}")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"[PASS] {summary}")
    print(f"[PASS] 复算与实跑一致：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
