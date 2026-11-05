# SPDX-License-Identifier: MIT
"""真机七环验收总入口。

用法::

    python scripts/hw_acceptance/run_all.py                 # 全跑
    python scripts/hw_acceptance/run_all.py --only r1,r3    # 只跑指定环
    python scripts/hw_acceptance/run_all.py --only r5 --out D:\\tmp\\hw

每环独立子进程 + 独立超时：单环卡死/崩溃只影响该环，不拖死全局。
产物（result.json / summary.json / summary.md / 波束证据）落在 D 盘。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, HERE)

import common  # noqa: E402

RINGS = [
    ("r1", "R1 连接与识别", "ring1_connection.py", 120),
    ("r2", "R2 频谱与瀑布", "ring2_spectrum_waterfall.py", 180),
    ("r3", "R3 解调与音频", "ring3_demod_audio.py", 180),
    ("r4", "R4 录音与回放", "ring4_recording_playback.py", 180),
    ("r5", "R5 卫星追踪与过境", "ring5_satellite_passes.py", 240),
    ("r6", "R6 星象仪模式", "ring6_sky_mode.py", 120),
    ("r7", "R7 发射安全闸", "ring7_tx_gate.py", 90),
]


def run_one(key: str, name: str, script: str, timeout: int, out_root: str) -> dict:
    cmd = [sys.executable, os.path.join(HERE, script), "--out", out_root]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              cwd=REPO_ROOT, creationflags=getattr(
                                  subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return common.result(
            key.upper(), name, common.FAIL,
            {"timeout_s": timeout},
            [common.check("ring_timeout", False, f"超过 {timeout}s 未结束，已强制终止")],
            "本环保卡死：请检查设备占用/网络阻塞后单独复跑该环。",
            device_required=True)
    elapsed = round(time.time() - t0, 1)
    text = (proc.stdout or "").strip()
    try:
        res = json.loads(text)
    except Exception:
        return common.result(
            key.upper(), name, common.FAIL,
            {"exit_code": proc.returncode, "elapsed_s": elapsed,
             "stdout_tail": text[-800:], "stderr_tail": (proc.stderr or "")[-800:]},
            [common.check("json_parse", False, "脚本未输出合法 JSON")],
            "环脚本异常退出，请看 stderr/stdout 尾部。",
            device_required=True)
    res["elapsed_s"] = elapsed
    res["exit_code"] = proc.returncode
    return res


def summarize(results: list[dict], out_root: str) -> dict:
    counts = {common.PASS: 0, common.FAIL: 0, common.SKIPPED: 0, common.UNVERIFIED: 0}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "artifact_root": out_root,
        "counts": counts,
        "red_line": common.RED_LINE,
        "rings": results,
    }
    with open(os.path.join(out_root, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    lines = ["# 真机七环验收（自动汇总）", "",
             f"- 时间：{summary['time']}",
             f"- 产物：`{out_root}`",
             f"- 结果：PASS {counts[common.PASS]} / FAIL {counts[common.FAIL]} / "
             f"SKIPPED {counts[common.SKIPPED]} / UNVERIFIED {counts[common.UNVERIFIED]}",
             f"- {common.RED_LINE}", "",
             "| 环 | 状态 | 关键实测 | 失败时用户看到 |",
             "|---|---|---|---|"]
    for r in results:
        metrics = r.get("metrics", {})
        key = ", ".join(f"{k}={v}" for k, v in list(metrics.items())[:3]) \
            if isinstance(metrics, dict) else str(metrics)
        lines.append(f"| {r['ring']} {r['name']} | {r['status']} | "
                     f"{key.replace('|', '/')[:120]} | {r.get('user_message_on_failure', '')[:80]} |")
    with open(os.path.join(out_root, "summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 真机七环验收总入口")
    ap.add_argument("--only", default="", help="逗号分隔：r1,r2,...；空=全跑")
    ap.add_argument("--out", default="", help="证据根目录（默认 D 盘 .tmp-tool-results/hw_acceptance/<时间戳>）")
    args = ap.parse_args()

    out_root = args.out or os.path.join(common.ARTIFACT_ROOT,
                                        time.strftime("%Y%m%d_%H%M%S"))
    assert os.path.isabs(out_root) and out_root.lower().startswith("d:\\"), \
        f"产物必须在 D 盘：{out_root}"
    os.makedirs(out_root, exist_ok=True)

    wanted = [s.strip().lower() for s in args.only.split(",") if s.strip()] if args.only else []
    unknown = [w for w in wanted if w not in {r[0] for r in RINGS}]
    if unknown:
        print(f"未知环名: {unknown}（可选 {[r[0] for r in RINGS]}）", file=sys.stderr)
        return 2
    selected = [r for r in RINGS if not wanted or r[0] in wanted]

    results = []
    for key, name, script, timeout in selected:
        print(f"\n=== 运行 {name} ===", flush=True)
        res = run_one(key, name, script, timeout, out_root)
        results.append(res)
        print(f"  -> {res['status']}  ({res.get('elapsed_s', '?')}s)")
        for c in res.get("checks", []):
            mark = "OK " if c["ok"] else ("SKIP" if c.get("skipped") else "FAIL")
            print(f"     [{mark}] {c['name']}: {c['detail'][:160]}")

    summary = summarize(results, out_root)
    print("\n===== 汇总 =====")
    for k, v in summary["counts"].items():
        print(f"  {k}: {v}")
    print(f"  summary.json: {os.path.join(out_root, 'summary.json')}")
    return 1 if summary["counts"][common.FAIL] else 0


if __name__ == "__main__":
    raise SystemExit(main())
