#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""发布门（Task 64）：工具面 + 文档一致性 + 可选关键测试。

三类输出，语义互不混用：

* ``passed``：该项确实通过；
* ``failed``：该项明确失败（只有存在 failed 时进程退出码为 1）；
* ``not_verified``：没跑或跑不了（如未加 ``--with-tests``、依赖缺失），
  **单独列出，不计为通过**。

    python scripts/release_gate.py
    python scripts/release_gate.py --with-tests
    python scripts/release_gate.py --json

不联网、不构建、不改产品代码。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

PASSED = "passed"
FAILED = "failed"
NOT_VERIFIED = "not_verified"
STATUSES = (PASSED, FAILED, NOT_VERIFIED)

#: ``--with-tests`` 只跑一小撮与发布直接相关的测试，不替代全量套件。
KEY_TESTS = (
    "tests/test_tool_surface_audit.py",
    "tests/test_docs_consistency_audit.py",
    "tests/test_offline_privacy.py",
    "tests/test_build_release_manifest.py",
)

CheckResult = Tuple[str, str]
Check = Callable[[], CheckResult]


def check_tool_surface() -> CheckResult:
    from tool_surface_audit import build_report

    report = build_report()
    counts = report["counts"]
    detail = (f"registry={counts['registry']} 文档={counts['docs']} "
              f"MCP={counts['mcp']}")
    if report["ok"]:
        return PASSED, detail
    return FAILED, detail + "；差异：" + "；".join(
        f"{k}={v}" for k, v in report.get("docs", {}).items() if v)


def check_docs_consistency() -> CheckResult:
    from docs_consistency_audit import build_report

    report = build_report()
    if report["ok"]:
        return PASSED, "CLI 退出码 / 工具文档 / 外部阻塞登记一致"
    return FAILED, "；".join(report["issues"][:8])


def check_tests() -> CheckResult:
    env = dict(os.environ)
    env["MBDSDR_OFFLINE"] = "1"
    command = [sys.executable, "-m", "pytest", "-q", *KEY_TESTS]
    try:
        proc = subprocess.run(command, cwd=str(ROOT), env=env,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return NOT_VERIFIED, f"无法运行关键测试：{type(exc).__name__}: {exc}"
    tail = " | ".join((proc.stdout or proc.stderr or "").strip().splitlines()[-2:])
    if proc.returncode == 0:
        return PASSED, tail or "关键测试全绿"
    return FAILED, f"关键测试失败（exit={proc.returncode}）：{tail}"


def tests_not_run() -> CheckResult:
    return NOT_VERIFIED, "未执行关键测试（加 --with-tests 才跑）"


def build_checks(with_tests: bool = False) -> List[Tuple[str, Check]]:
    return [
        ("工具面审计", check_tool_surface),
        ("文档一致性", check_docs_consistency),
        ("关键测试", check_tests if with_tests else tests_not_run),
    ]


def _normalize(name: str, fn: Check) -> Dict[str, Any]:
    try:
        raw = fn()
    except Exception as exc:  # 跑不了就是没验证，不冒充通过
        return {"name": name, "status": NOT_VERIFIED,
                "detail": f"执行失败：{type(exc).__name__}: {exc}"}
    if isinstance(raw, tuple) and len(raw) == 2:
        status, detail = raw
    elif isinstance(raw, str):
        status, detail = raw, ""
    else:
        status, detail = NOT_VERIFIED, f"检查返回值形状非法：{raw!r}"
    status = str(status)
    if status not in STATUSES:
        return {"name": name, "status": NOT_VERIFIED,
                "detail": f"未知状态 {status!r}；{detail}"}
    return {"name": name, "status": status, "detail": str(detail)}


def run_gate(checks: Iterable[Tuple[str, Check]]) -> Dict[str, Any]:
    results = [_normalize(name, fn) for name, fn in checks]
    summary = {status: sum(1 for r in results if r["status"] == status)
               for status in STATUSES}
    return {
        "ok": summary[FAILED] == 0,
        "summary": summary,
        "results": results,
    }


def _print_human(report: Dict[str, Any]) -> None:
    labels = {PASSED: "通过", FAILED: "失败", NOT_VERIFIED: "未验证"}
    print("发布门")
    for result in report["results"]:
        print(f"  [{labels[result['status']]}] {result['name']}"
              + (f"：{result['detail']}" if result["detail"] else ""))
    s = report["summary"]
    print(f"汇总：通过 {s[PASSED]} / 失败 {s[FAILED]} / 未验证 {s[NOT_VERIFIED]}")
    if report["ok"]:
        print("结论：无失败项"
              + ("（存在未验证项，不计为通过）" if s[NOT_VERIFIED] else ""))
    else:
        print("结论：存在失败项")


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--with-tests", action="store_true",
                    help=f"跑少量关键测试：{', '.join(KEY_TESTS)}")
    ap.add_argument("--json", action="store_true", help="输出 JSON 报告")
    args = ap.parse_args(argv)

    report = run_gate(build_checks(args.with_tests))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
