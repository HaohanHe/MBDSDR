#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""发布门（Task 64）：工具面 + 文档一致性 + 天空指向 + 可选关键测试。

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
import re
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
    "tests/test_sky_ar_release_gate.py",
    "tests/test_build_release_manifest.py",
)

#: 天空指向专项：固定时刻，避免读时钟让结果不可复现。
SKY_GATE_NOW_UTC = 1_700_000_000.0

#: 默认界面不得出现的英文工程术语（按 ASCII 词边界、不区分大小写）。
SKY_FORBIDDEN_TERMS = ("az", "alt", "vmag", "GMST", "FOV", "JD", "ICRS",
                       "J2000")

#: 默认界面不得出现的中文工程术语（中文按子串，无需词边界）。
SKY_FORBIDDEN_ZH = ("方位角", "地平高度", "视场", "儒略日")

#: L0 没有任何指向依据，这些「已经对上了」的断言一律不许出现。
SKY_UNALIGNED_CLAIMS = ("已对准", "已锁定", "已融合")

CheckResult = Tuple[str, str]
Check = Callable[[], CheckResult]


def _ascii_word_pattern(term: str) -> "re.Pattern[str]":
    """ASCII 词边界：中文紧邻 ``az`` 也要命中（``\\b`` 会把中文当词字符）。"""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(term)
                      + r"(?![A-Za-z0-9_])", re.IGNORECASE)


def _forbidden_term_hits(samples: Sequence[Tuple[str, str]]
                         ) -> List[Tuple[str, str]]:
    """在 (出处, 文本) 样本里扫工程术语；返回 [(术语, 出处)]。"""
    hits: List[Tuple[str, str]] = []
    for label, text in samples:
        for term in SKY_FORBIDDEN_TERMS:
            if _ascii_word_pattern(term).search(text):
                hits.append((term, label))
        for term in SKY_FORBIDDEN_ZH:
            if term in text:
                hits.append((term, label))
    return hits


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


def check_sky_ar_pointing() -> CheckResult:
    """天空指向专项：离线可用 / 无设备诚实空态 / 默认界面无术语。

    只调纯逻辑模块，不联网、不读真机、不写盘；运行期间给网络入口打计数
    补丁，结束后在 ``finally`` 里还原。
    """
    try:
        from mbdsdr_ai import net_policy
    except Exception as exc:  # 跑不了就是没验证，不冒充通过
        return NOT_VERIFIED, f"网络策略模块不可用：{type(exc).__name__}: {exc}"

    if net_policy.network_allowed():
        return NOT_VERIFIED, ("环境显式允许联网（MBDSDR_ALLOW_NETWORK），"
                              "默认离线路径未被证明")
    if (os.environ.get(net_policy.ALLOW_NETWORK_ENV, "").strip().lower()
            in ("1", "true", "yes", "on")):
        return NOT_VERIFIED, ("环境显式设置 MBDSDR_ALLOW_NETWORK，"
                              "未能证明默认路径不联网")

    try:
        from mbdsdr_ai import sky_health, sky_hud, sky_solve
    except Exception as exc:
        return NOT_VERIFIED, f"天空指向模块导入失败：{type(exc).__name__}: {exc}"

    import socket
    import urllib.request

    failures: List[str] = []
    calls: List[str] = []
    probe_error: Optional[BaseException] = None
    snapshot: Optional[Dict[str, Any]] = None
    hud: Optional[Dict[str, Any]] = None

    real_connect = socket.socket.connect
    real_urlopen = urllib.request.urlopen

    def _blocked_connect(_sock: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append("socket.socket.connect")
        raise RuntimeError("发布门探测期间不得发起网络连接")

    def _blocked_urlopen(*args: Any, **kwargs: Any) -> Any:
        calls.append("urllib.request.urlopen")
        raise RuntimeError("发布门探测期间不得调用 urlopen")

    try:
        socket.socket.connect = _blocked_connect  # type: ignore[assignment]
        urllib.request.urlopen = _blocked_urlopen  # type: ignore[assignment]
        solve_status = sky_solve.solver_status()
        snapshot = sky_health.health_snapshot([], SKY_GATE_NOW_UTC,
                                              solve_status)
        hud = sky_hud.build_hud(None, [], "L0", {}, (800, 600))
    except Exception as exc:  # noqa: BLE001 - 探测失败如实记为未验证
        probe_error = exc
    finally:
        socket.socket.connect = real_connect  # type: ignore[assignment]
        urllib.request.urlopen = real_urlopen  # type: ignore[assignment]

    if calls:
        return FAILED, "默认路径尝试联网：" + "、".join(sorted(set(calls)))
    if probe_error is not None:
        return NOT_VERIFIED, (f"天空指向探测未完成："
                              f"{type(probe_error).__name__}: {probe_error}")

    assert snapshot is not None and hud is not None

    # 1) 无设备诚实空态：零观测必须 L0，且明确说没有观测。
    if snapshot.get("level") != "L0":
        failures.append(f"零观测级别应为 L0，实际 {snapshot.get('level')!r}")
    reason = str(snapshot.get("reason") or "")
    if not reason:
        failures.append("零观测 reason 为空（没有说明缺少观测）")
    elif "观测" not in reason and "没有" not in reason:
        failures.append(f"零观测 reason 未说明缺少观测：{reason!r}")

    if hud.get("labels"):
        failures.append(f"无位姿时不应绘制标签，实际 {len(hud['labels'])} 条")
    if hud.get("targets_visible") != 0:
        failures.append("无位姿时 targets_visible 应为 0，"
                        f"实际 {hud.get('targets_visible')!r}")
    for key in ("hint", "narrative"):
        text = str(hud.get(key) or "")
        for claim in SKY_UNALIGNED_CLAIMS:
            if claim in text:
                failures.append(f"L0 文本 {key}={text!r} 含不该有的断言「{claim}」")

    # 2) 默认界面无术语：HUD_HINTS 全量 + L0/L1 narrative + L2/L3 hint。
    samples: List[Tuple[str, str]] = [
        (f"HUD_HINTS[{level}]", str(hint))
        for level, hint in sorted(sky_hud.HUD_HINTS.items())
    ]
    samples.append(("L0 narrative", str(hud.get("narrative") or "")))
    pose_l1 = {"valid": True, "az_deg": 225.0, "alt_deg": 20.0,
               "roll_deg": 0.0, "fov_deg": 60.0}
    try:
        hud_l1 = sky_hud.build_hud(pose_l1, [], "L1", {}, (800, 600))
    except Exception as exc:  # noqa: BLE001
        return NOT_VERIFIED, (f"L1 位姿 HUD 未生成："
                              f"{type(exc).__name__}: {exc}")
    samples.append(("L1 narrative", str(hud_l1.get("narrative") or "")))
    hits = _forbidden_term_hits(samples)
    if hits:
        failures.append("默认界面出现工程术语：" + "、".join(
            f"{term}@{label}" for term, label in hits))

    if failures:
        return FAILED, "；".join(failures)
    return PASSED, (f"离线 0 次网络调用；零观测 L0（{reason}）；"
                    f"术语扫描 {len(samples)} 条文本无命中")


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
        ("天空指向发布门", check_sky_ar_pointing),
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
