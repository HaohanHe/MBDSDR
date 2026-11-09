#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""证据 8（可选组）—— 测试基线：两条 lane 各跑一次，数字现算不抄报告。

为什么是可选：证据包默认 ~90 秒，加两条完整测试套件会变成十几分钟。
所以 ``run_all.py --with-tests`` 才跑本组，或 ``--groups test_suites`` 单跑。

诚实口径：
  * 每条套件**单独一个子进程**，互不争 Qt 互斥锁（合并跑在单进程里会卡在
    61%，且失败归因说不清）；
  * 数字从 pytest 的汇总行解析，不自报、不从 README 抄；
  * 任一 failed>0 或退出码非 0 → 本组 FAIL；超时同样 FAIL（不是"没跑完算过"）；
  * 原始日志全量落盘，报告里给路径，任何人能翻回去核对。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import common  # noqa: E402

GID = "test_suites"
TITLE = "测试基线：Agent 内核 / 桌面 Qt 两条 lane 各自单独跑"

#: (id, pytest 目标, 超时秒)
#: desktop 4200s 是实测值：本机 1800s 只跑到 56%（3214/5581），按此速率
#: 全量约需 55 分钟。跑不完就报超时、不算通过，绝不"半程结果当全量结论"。
SUITES = [
    ("kernel", "mbdsdr_ai/tests", 1200),
    ("desktop", "tests", 4200),
]

_SUMMARY_RE = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "skipped": re.compile(r"(\d+) skipped"),
    "xfailed": re.compile(r"(\d+) xfailed"),
    "xpassed": re.compile(r"(\d+) xpassed"),
    "errors": re.compile(r"(\d+) errors?"),
}


def _parse_summary(text: str) -> dict:
    """只从 pytest 末尾的汇总行取数（不把过程点号当数字）。"""
    tail = "\n".join(text.strip().splitlines()[-8:])
    out = {}
    for key, rx in _SUMMARY_RE.items():
        m = rx.search(tail)
        out[key] = int(m.group(1)) if m else 0
    return out


def _parse_args(argv):
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--out", default=str(common.DEFAULT_OUT))
    p.add_argument("--json-out", default=None)
    p.add_argument("--suite", choices=("both", "kernel", "desktop"),
                   default="both", help="只跑一条套件（用于快速核验）")
    p.add_argument("--kernel-timeout", type=int, default=None,
                   help="覆盖内核套件超时（秒）")
    p.add_argument("--desktop-timeout", type=int, default=None,
                   help="覆盖桌面套件超时（秒）；慢机器可调大")
    return p.parse_args(argv)


def run(args) -> dict:
    res = common.new_result(GID, TITLE)
    out = Path(args.out) / GID
    out.mkdir(parents=True, exist_ok=True)
    facts = res["facts"]
    facts["command_note"] = (
        "每条套件单独子进程 + MBDSDR_TEST_LOCK=0；合并单进程跑会争 Qt 锁")
    facts["suite_selection"] = args.suite

    targets = [s for s in SUITES
               if args.suite == "both" or s[0] == args.suite]
    overrides = {"kernel": args.kernel_timeout,
                 "desktop": args.desktop_timeout}
    for name, target, default_timeout in targets:
        timeout_s = overrides.get(name) or default_timeout
        log_path = out / f"{name}.log"
        # 每条套件给一个全新的 basetemp：默认临时根里堆积的 pytest-of-*
        # 垃圾目录会在退出时被批量清理，本机沙箱对「一次删 52 个文件」
        # 会拦截并抢在 terminal summary 之前输出，导致汇总行整批丢失
        # （2026-11-09 实测 kernel 日志 100% 后直接接 [safe-delete]，
        # 连一个 "passed"/"failed" 字样都没有）。
        #
        # 但**必须放在系统临时目录下**：产品导出路径的允许根是
        # MBDSDR_EXPORT_ROOT / CWD / tempfile.gettempdir()，pytest 的
        # tmp_path 也在这条链上。第一次改到 D:/mbdsdr/output/ 下，直接
        # 把 7 条 export 用例判成「导出路径越界」—— 修一个口子造七个。
        basetemp = (Path(tempfile.gettempdir())
                    / f"mbdsdr-evidence-{name}-{int(time.time())}")
        cmd = [sys.executable, "-m", "pytest", target, "-q", "--tb=line",
               "-rf", "-p", "no:randomly", f"--basetemp={basetemp}"]
        env = dict(os.environ)
        env["MBDSDR_TEST_LOCK"] = "0"
        env["PYTHONUNBUFFERED"] = "1"
        t0 = time.time()
        timed_out = False
        code = -1
        # 流式写日志文件，不用 PIPE 攒到最后：全量桌面套件要跑几十分钟，
        # 中途被中断/被杀时 PIPE 里的内容会全部丢失（日志 0 字节、证据全无）。
        # 直接给 subprocess 文件句柄，跑到哪写到哪，进度随时可查。
        with log_path.open("w", encoding="utf-8") as fh:
            try:
                proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env,
                                      stdout=fh, stderr=subprocess.STDOUT,
                                      text=True, timeout=timeout_s)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
        output = log_path.read_text(encoding="utf-8", errors="replace")
        duration = round(time.time() - t0, 1)
        counts = _parse_summary(output)
        summary_line = (output.strip().splitlines()[-1]
                        if output.strip() else "")
        # 汇总行守卫：pytest 的 terminal summary 被硬退出/沙箱拦截吞掉时，
        # counts 会全 0 —— 「0 failed」会被误读成「没有失败」，这是本工具
        # 最危险的假证据口子。没有可解析的结论就必须判 FAIL。
        has_summary = bool(re.search(r"\d+\s+(passed|failed|error)",
                                     summary_line))
        facts[name] = {
            "command": " ".join(cmd[1:]),
            "cwd": str(REPO_ROOT),
            "exit_code": code,
            "duration_s": duration,
            "counts": counts,
            "counts_trustworthy": has_summary,
            "timed_out": timed_out,
            "log": str(log_path),
            "summary_line": summary_line,
        }
        if timed_out:
            res["status"] = "fail"
            res["notes"].append(
                f"{name} 套件超时（>{timeout_s}s）—— 跑不完不算通过")
        elif not has_summary:
            res["status"] = "fail"
            res["notes"].append(
                f"{name}: pytest 汇总行缺失（最后一行={summary_line[:90]!r}），"
                "counts 全 0 不代表 0 failed —— 没有结论，不算通过")
        elif code != 0 or counts["failed"] or counts["errors"]:
            res["status"] = "fail"
            res["notes"].append(
                f"{name} 套件 exit={code} / failed={counts['failed']}"
                f" / errors={counts['errors']}，明细见 {log_path}")
        else:
            res["notes"].append(
                f"{name}: {counts['passed']} passed / "
                f"{counts['skipped']} skipped / {counts['xfailed']} xfailed"
                f"  ({duration}s)")

    if args.suite != "both":
        res["notes"].append(
            f"本次只跑了 {args.suite} 套件；另一条未跑，不计入本组结论")
        res["not_verified"].append(
            f"另一条套件（{' | '.join(s[0] for s in SUITES if s[0] != args.suite)}）"
            " 本次未跑")
    res["not_verified"].append(
        "硬件验收类用例由套件自己 skip：RTL2832U / GNSS 不在位，"
        "真机链路未验证")
    facts["host"] = common.host_info()
    return res


def main(argv=None) -> int:
    args = _parse_args(argv)
    started = time.time()
    try:
        res = run(args)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        res = common.new_result(GID, TITLE, status="fail")
        res["notes"].append(f"{type(exc).__name__}: {exc}")
    return common.finish(res, Path(args.out) / GID, args.json_out, started)


if __name__ == "__main__":
    sys.exit(main())
