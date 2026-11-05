# SPDX-License-Identifier: MIT
"""七环验收的反证：故意把判据改坏，环必须 FAIL。

目的：证明验收环**真的在判**，而不是只看「有没有异常」就放行。
做法：先跑基线（必须不是 FAIL），再注入一处已知错误，重跑同一环；
注入后若仍 PASS/SKIP，则说明该环的判据形同虚设，本脚本自身判 FAIL。

覆盖（全部离线可复跑，不需要射频硬件）::

    R7 发射安全闸  把 gate.authorize 改成永远放行 → arm_manual/token 判据必须红
    R5 卫星过境    把仰角门 MIN_EL 改成 95°      → 过境门必须红
    R6 星象仪      把月球容差改成 -1″            → 月球对拍必须红
    R2 频谱替代    把真实 OTA 样本换成全零        → 离线替代必须 FAIL（不是 SKIP）

用法::

    python scripts/hw_acceptance/negative_control.py
    python scripts/hw_acceptance/negative_control.py --out D:\\mbdsdr\\tmp\\negctl

退出码：全部反证成立 → 0；任一反证没红 → 1（验收不可信）。
产物：D 盘 ``.tmp-tool-results/hw_acceptance/negctl_<时间戳>/result.json``。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (REPO_ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import common  # noqa: E402


@contextlib.contextmanager
def patched(obj, name: str, value):
    """临时替换属性，退出时恢复（异常也不泄漏）。"""
    had = hasattr(obj, name)
    old = getattr(obj, name, None)
    setattr(obj, name, value)
    try:
        yield
    finally:
        if had:
            setattr(obj, name, old)
        else:
            delattr(obj, name)


def _case(name: str, baseline_fn, mutated_fn) -> dict:
    t0 = time.time()
    base = baseline_fn()
    with contextlib.ExitStack() as stack:
        mutated = mutated_fn(stack)
    ok = base.get("status") != common.FAIL and mutated.get("status") == common.FAIL
    return {
        "case": name,
        "baseline_status": base.get("status"),
        "baseline_skip_code": base.get("skip_code", ""),
        "mutated_status": mutated.get("status"),
        "mutated_failed_checks": [c["name"] for c in mutated.get("checks", [])
                                  if not c["ok"] and not c.get("skipped")],
        "verdict": "RED_AS_EXPECTED" if ok else "NOT_RED_ACCEPTANCE_UNTRUSTED",
        "elapsed_s": round(time.time() - t0, 1),
        "baseline_note": str(base.get("notes", ""))[:200],
        "mutated_note": str(mutated.get("notes", ""))[:200],
    }


def case_r7():
    import ring7_tx_gate as r7
    import mbdsdr_ai.tx_guard as tg

    def baseline():
        return r7.run()

    def mutate(stack):
        stack.enter_context(patched(
            tg.TxGate, "authorize",
            lambda self, *a, **k: types.SimpleNamespace(
                allowed=True, code=tg.GATE_OK, reason="mutated: always allow"),
        ))
        return r7.run()

    return _case("R7 发射安全闸（门改成永远放行）", baseline, mutate)


def case_r5(lat: float, lon: float, alt: float):
    import ring5_satellite_passes as r5

    def baseline():
        return r5.run(lat, lon, alt)

    def mutate(stack):
        stack.enter_context(patched(r5, "MIN_EL", 95.0))
        return r5.run(lat, lon, alt)

    return _case("R5 卫星过境（仰角门改成 95°）", baseline, mutate)


def case_r6(lat: float, lon: float):
    import ring6_sky_mode as r6

    def baseline():
        return r6.run(lat, lon)

    def mutate(stack):
        stack.enter_context(patched(r6, "MOON_TOL_ARCSEC", -1.0))
        return r6.run(lat, lon)

    return _case("R6 星象仪（月球容差改成 -1″）", baseline, mutate)


def case_r2():
    import ring2_spectrum_waterfall as r2

    def baseline():
        return r2._offline()

    def mutate(stack):
        real = common.real_fm_fixture

        def zero_fixture():
            fx = real()
            if fx.get("ok"):
                fx = dict(fx)
                fx["iq"] = fx["iq"] * 0.0
            return fx

        stack.enter_context(patched(common, "real_fm_fixture", zero_fixture))
        return r2._offline()

    return _case("R2 频谱离线替代（真实样本换成全零）", baseline, mutate)


def main() -> int:
    ap = argparse.ArgumentParser(description="七环验收反证（改坏判据必须 FAIL）")
    ap.add_argument("--lat", type=float, default=43.88)
    ap.add_argument("--lon", type=float, default=125.32)
    ap.add_argument("--alt", type=float, default=0.0)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    out_root = args.out or os.path.join(
        common.ARTIFACT_ROOT, "negctl_" + time.strftime("%Y%m%d_%H%M%S"))
    assert os.path.isabs(out_root) and out_root.lower().startswith("d:\\"), \
        f"产物必须在 D 盘：{out_root}"
    os.makedirs(out_root, exist_ok=True)

    cases = [case_r7(), case_r5(args.lat, args.lon, args.alt),
             case_r6(args.lat, args.lon), case_r2()]
    trusted = all(c["verdict"] == "RED_AS_EXPECTED" for c in cases)
    summary = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "artifact_root": out_root,
        "red_line": common.RED_LINE,
        "meaning": ("反证 = 先跑基线（不得是 FAIL），再注入已知错误；"
                    "注入后必须 FAIL。否则该环判据不可信。"),
        "trusted": trusted,
        "cases": cases,
    }
    path = os.path.join(out_root, "result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print("=== 七环验收反证（改坏判据必须 FAIL）===")
    for c in cases:
        print(f"  [{c['verdict']}] {c['case']}")
        print(f"     基线={c['baseline_status']}/{c['baseline_skip_code'] or '-'}  "
              f"注入后={c['mutated_status']}  红的检查={c['mutated_failed_checks']}")
    print(f"  结论：{'反证成立，验收判据在真判' if trusted else '反证失败：存在判据形同虚设'}")
    print(f"  result.json: {path}")
    return 0 if trusted else 1


if __name__ == "__main__":
    raise SystemExit(main())
