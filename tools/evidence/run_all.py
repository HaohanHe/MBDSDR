#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""MBDSDR 证据包 —— 一条命令跑完六组证据，产出一份可核对的 JSON 报告。

    python3 tools/evidence/run_all.py                 # 全部（离线）
    python3 tools/evidence/run_all.py --json          # 机器可读
    python3 tools/evidence/run_all.py --groups ota,architecture
    python3 tools/evidence/run_all.py --online        # 额外验证 OSM 在线搜索

为什么每组单开一个子进程：Qt 一个进程只该有一个 QApplication，而且一组
崩了不该把其余组的证据一起带走。子进程各自隔离 HOME，互不污染。

退出码（和 tools/ota_demo 同一套诚实口径）：
  0  没有 FAIL（允许 degraded：例如缺 matplotlib 只降级不出图）
  1  至少一组 FAIL —— 报告里写明是哪组、为什么
  2  参数/环境错误（例如仓库不完整）
  6  一组都没真正跑（全部 skipped）—— 不许当成通过
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    DEFAULT_OUT, RESULT_SCHEMA, host_info, json_safe, new_result, now_iso,
    write_result,
)

#: (id, 标题, 脚本名 or None, 是否接受 --online)
GROUPS = [
    ("ota", "真实 OTA 样本 → 自研解调链（零硬件重放）", None, False),
    ("architecture", "三端架构事实（源码现取）", "architecture.py", False),
    ("first_open", "首次打开：零硬件 / 零配置", "first_open.py", False),
    ("ui_entries", "星象仪入口：找星 / 时间 / 存图 / 图层 / 极限星等",
     "ui_entries.py", False),
    ("location_projection", "地点面板 + 四种投影", "location_projection.py", True),
]

OTA_SCRIPT = REPO_ROOT / "tools" / "ota_demo" / "ota_demo.py"
GROUP_TIMEOUT_S = 900


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


def _skipped(gid: str, title: str, reason: str) -> Dict[str, Any]:
    res = new_result(gid, title, status="skipped")
    res["notes"].append(reason)
    return res


def _run_ota(out_dir: Path) -> Dict[str, Any]:
    """证据 1+2：调用既有 ota_demo，把它的 JSON 折进统一结构。"""
    title = next(t for g, t, s, _ in GROUPS if g == "ota")
    art_dir = out_dir / "ota"
    art_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, str(OTA_SCRIPT), "--json", "--out-dir", str(art_dir)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
        timeout=GROUP_TIMEOUT_S)
    res = new_result("ota", title)
    res["notes"].append(f"ota_demo exit={proc.returncode}")
    payload = None
    if proc.stdout.strip():
        try:
            payload = json.loads(proc.stdout)
        except ValueError:
            res["status"] = "fail"
            res["notes"].append("ota_demo --json 输出不是合法 JSON")
            res["notes"].append(proc.stdout[:400])
    if payload is None:
        if res["status"] != "fail":
            res["status"] = "fail"
            res["notes"].append((proc.stderr or "")[:400])
        return res

    status_map = {"ok": "pass", "degraded": "degraded"}
    res["status"] = status_map.get(str(payload.get("status")), "fail")
    res["facts"].update({
        "sample": payload.get("sample"),
        "spectrum": payload.get("spectrum"),
        "demodulation": payload.get("demodulation"),
        "hardware_required": payload.get("hardware_required"),
        "network_required": payload.get("network_required"),
    })
    res["not_verified"] = list(payload.get("not_verified") or [])
    for f in (payload.get("artifacts") or {}).get("files") or []:
        p = Path(f)
        res["frames"].append({
            "name": p.stem,
            "path": str(p),
            "bytes": p.stat().st_size if p.is_file() else 0,
        })
    if res["status"] == "degraded":
        res["notes"].append(
            "出图被跳过（多半缺 matplotlib），指标仍是现算的；"
            "这条不是通过，报告里按 degraded 记")
    return res


def _run_group(gid: str, title: str, script: Optional[str],
               out_dir: Path, online: bool) -> Dict[str, Any]:
    if gid == "ota":
        return _run_ota(out_dir)
    assert script is not None
    path = HERE / script
    if not path.is_file():
        return _skipped(gid, title, f"脚本缺失：{path}")

    json_out = out_dir / f".{gid}.json"
    cmd = [sys.executable, str(path), "--out", str(out_dir),
           "--json-out", str(json_out)]
    if online and gid == "location_projection":
        cmd.append("--online")
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True,
                              text=True, timeout=GROUP_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        return _skipped(gid, title,
                        f"超时（>{GROUP_TIMEOUT_S}s），本次没有结果")
    except OSError as exc:
        return _skipped(gid, title, f"无法启动子进程：{exc}")

    if json_out.is_file():
        try:
            res = json.loads(json_out.read_text(encoding="utf-8"))
            res.setdefault("notes", []).append(f"子进程 exit={proc.returncode}")
            return res
        except ValueError:
            pass
    res = new_result(gid, title, status="fail")
    res["notes"].append(f"子进程 exit={proc.returncode}，没有产出 JSON")
    tail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    res["notes"].append(tail[-600:] if tail else "（无输出）")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MBDSDR 证据包一键复现")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="产物目录")
    ap.add_argument("--json", action="store_true", help="把汇总报告打到 stdout")
    ap.add_argument("--groups", default=None,
                    help="逗号分隔，只跑指定组（如 ota,first_open）")
    ap.add_argument("--online", action="store_true",
                    help="允许联网组跑在线验证（OSM 搜索）")
    ap.add_argument("--list", action="store_true", help="列出组名后退出")
    args = ap.parse_args(argv)

    if args.list:
        for gid, title, _, online_ok in GROUPS:
            print(f"{gid:22s} {title}{'  [--online]' if online_ok else ''}")
        return 0

    if not (REPO_ROOT / "mbdsdr_ai").is_dir():
        print(f"[FAIL] 仓库不完整：{REPO_ROOT}/mbdsdr_ai 不存在", flush=True)
        return 2

    wanted = None
    if args.groups:
        wanted = {g.strip() for g in args.groups.split(",") if g.strip()}
        unknown = wanted - {g for g, _, _, _ in GROUPS}
        if unknown:
            print(f"[FAIL] 未知组：{sorted(unknown)}", flush=True)
            return 2

    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    qt_ok = _qt_available()
    started = time.time()
    results: List[Dict[str, Any]] = []

    for gid, title, script, online_ok in GROUPS:
        if wanted is not None and gid not in wanted:
            continue
        t0 = time.time()
        if script is not None and not qt_ok:
            res = _skipped(
                gid, title,
                "本机缺 PySide6 —— 该组需要 Qt 渲染界面，本次没有跑"
                "（不是通过，是未验证）")
        else:
            print(f"--- {gid} ---", flush=True)
            res = _run_group(gid, title, script, out_dir,
                             online=bool(args.online and online_ok))
        # 组的 duration_s 由 total 覆盖：子进程自己记的只是内部耗时，
        # 这里要的是"包含启动/渲染"的墙钟时间。
        res["duration_s"] = round(time.time() - t0, 1)
        results.append(res)
        print(f"[{res['status'].upper():8s}] {gid}  ({res['duration_s']}s)",
              flush=True)
        for n in res.get("notes", [])[-2:]:
            print(f"           note: {n}", flush=True)

    counts: Dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1

    merged_not_verified: List[str] = []
    for r in results:
        for item in r.get("not_verified", []):
            if item not in merged_not_verified:
                merged_not_verified.append(item)

    report = json_safe({
        "tool": "mbdsdr-evidence",
        "schema": RESULT_SCHEMA,
        "generated_at": now_iso(),
        "repo": str(REPO_ROOT),
        "out_dir": str(out_dir),
        "online_allowed": bool(args.online),
        "host": host_info(),
        "groups": [{k: r.get(k) for k in
                    ("id", "title", "status", "facts", "frames", "notes",
                     "not_verified", "duration_s")} for r in results],
        "summary": {
            "total": len(results),
            "pass": counts.get("pass", 0),
            "degraded": counts.get("degraded", 0),
            "skipped": counts.get("skipped", 0),
            "fail": counts.get("fail", 0),
            "duration_s": round(time.time() - started, 1),
        },
        "not_verified": merged_not_verified,
        "exit_code": 0,
    })

    if report["summary"]["fail"]:
        code = 1
    elif report["summary"]["pass"] == 0 and report["summary"]["degraded"] == 0:
        code = 6
    else:
        code = 0
    report["exit_code"] = code

    report_path = out_dir / "report.json"
    report = write_result(report_path, report)

    print("\n" + "=" * 62)
    s = report["summary"]
    print(f"证据包：{s['total']} 组 / pass {s['pass']} / degraded {s['degraded']}"
          f" / skipped {s['skipped']} / fail {s['fail']}  ({s['duration_s']}s)")
    print(f"报告：{report_path}")
    if merged_not_verified:
        print("未验证（对外不得声称已验证）：")
        for r in results:
            items = r.get("not_verified") or []
            if not items:
                continue
            print(f"  [{r['id']}]")
            for item in items:
                print(f"    - {item}")
    if code == 6:
        print("\n[WARN] 一组都没真正跑 —— 这不是通过，是未验证。")
    print("=" * 62, flush=True)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
