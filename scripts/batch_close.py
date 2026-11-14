#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""批次收尾三道检查的唯一命名入口。

背景
----
``docs/COORDINATION.md`` 把批次末要跑的三道检查写成三条独立命令，并要求当班
owner 把「红/绿 + 条数」抄进台账。实际执行时有三处靠人：

* 记住是哪三道（漏一道不会有人发现）；
* 分别跑三次、分别看退出码（一道红时容易不再跑后两道）；
* 自己把结论合并成一句（三个退出码的语义各不相同）。

本工具只做聚合，**不改变任何一道检查自身的判据**，也不进 pre-commit（前两道
单次耗时远超钩子预算，塞进钩子只会让人开始用 ``--no-verify``）。

三道检查（与台账同源）
----------------------
====================  =====================================  ==========================
步骤名                脚本                                   退出码含义
====================  =====================================  ==========================
``evidence``          ``scripts/audit_evidence.py``          0 无红 / 1 有红
``red_lifecycle``     ``scripts/check_red_lifecycle.py``     0 无 FAIL / 1 有 FAIL / 2 用法或 git 错误
``roadmap``           ``scripts/check_roadmap_blocking.py``  恒 0（提醒器，只作提示）
====================  =====================================  ==========================

行为
----
* 按固定顺序**全跑**：一道判红不会遮住后两道的结论；
* 每道有独立超时（默认 300 s），超时按「没跑成」处理，不按红处理；
* 三道都跑完后，把结果压成一行摘要，并默认留一份 JSON 快照到
  ``<仓库父目录>/.tmp/batch_close/``（D 盘；不写仓库、不写 C 盘）；
* 快照写失败只打印 WARN，不改退出码——记录失败不是判据失败，但不能静默。

退出码
------
===  ================================================================
码   含义
===  ================================================================
0    三道全部正常且无红（``roadmap`` 恒 0，只作提示）
1    至少一道判红（该道以 1 退出）；其余检查均正常跑完
2    用法错误：未知 ``--steps``、非正 ``--timeout``、``--root`` 下没有 scripts/
3    至少一道**没能正常执行**（脚本缺失、超时、退出码不是 0/1），红绿结论不成立
===  ================================================================

优先级 ``3 > 1 > 0``：检查器自己没跑起来时，把「红」当结论会误导人；宁可说
「不知道」也不许把没跑成吞成红或绿。

用法
----
    python scripts/batch_close.py                       # 三道全跑
    python scripts/batch_close.py --steps evidence,roadmap
    python scripts/batch_close.py --timeout 600 --json
    python scripts/batch_close.py --no-record
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]

EXIT_OK = 0
EXIT_RED = 1
EXIT_USAGE = 2
EXIT_NOT_RUN = 3

DEFAULT_TIMEOUT_S = 300
#: 失败时打印的日志尾行数：够看出原因，又不至于把整份输出糊到屏幕上。
TAIL_LINES = 8


@dataclass(frozen=True)
class Step:
    """一道检查：只描述「跑哪个脚本」，判据仍在脚本自己手里。"""

    name: str
    script: str
    advisory: bool = False


STEPS: tuple[Step, ...] = (
    Step("evidence", "audit_evidence.py"),
    Step("red_lifecycle", "check_red_lifecycle.py"),
    Step("roadmap", "check_roadmap_blocking.py", advisory=True),
)
STEP_BY_NAME = {step.name: step for step in STEPS}


@dataclass
class StepResult:
    name: str
    #: PASS / FAIL / NOTE / ERROR
    status: str
    rc: Optional[int]
    elapsed_s: float
    summary: str
    detail: str = ""


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    rows = [row.rstrip() for row in (text or "").splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


def _summary(text: str) -> str:
    rows = [row.strip() for row in (text or "").splitlines() if row.strip()]
    if not rows:
        return "(无输出)"
    last = rows[-1]
    return last if len(last) <= 200 else last[:197] + "..."


def run_step(step: Step, root: Path, timeout_s: int) -> StepResult:
    """跑一道检查。返回的 status 只由「退出码 + 是否提醒器」决定。"""
    script = root / "scripts" / step.script
    if not script.is_file():
        return StepResult(
            name=step.name,
            status="ERROR",
            rc=None,
            elapsed_s=0.0,
            summary="脚本缺失，未执行",
            detail=f"缺文件：{script}",
        )

    env = os.environ.copy()
    # 子进程输出统一按 UTF-8 读；乱码只替换不抛异常，避免把编码问题伪装成判红。
    env.setdefault("PYTHONIOENCODING", "utf-8")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return StepResult(
            name=step.name,
            status="ERROR",
            rc=None,
            elapsed_s=time.monotonic() - started,
            summary=f"超时：{timeout_s}s 内未退出",
        )
    except OSError as exc:
        return StepResult(
            name=step.name,
            status="ERROR",
            rc=None,
            elapsed_s=time.monotonic() - started,
            summary="启动失败",
            detail=str(exc),
        )

    elapsed = time.monotonic() - started
    combined = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
    summary = _summary(proc.stdout or proc.stderr)

    if proc.returncode == 0:
        return StepResult(
            name=step.name,
            status="NOTE" if step.advisory else "PASS",
            rc=0,
            elapsed_s=elapsed,
            summary=summary,
        )
    if proc.returncode == 1 and not step.advisory:
        return StepResult(
            name=step.name,
            status="FAIL",
            rc=1,
            elapsed_s=elapsed,
            summary=summary,
            detail=_tail(combined),
        )
    return StepResult(
        name=step.name,
        status="ERROR",
        rc=proc.returncode,
        elapsed_s=elapsed,
        summary=f"退出码 {proc.returncode} 不在约定内，无法判定红绿",
        detail=_tail(combined),
    )


def overall_exit(results: Sequence[StepResult]) -> int:
    """3（没跑成）优先于 1（判红），1 优先于 0。"""
    if any(result.status == "ERROR" for result in results):
        return EXIT_NOT_RUN
    if any(result.status == "FAIL" for result in results):
        return EXIT_RED
    return EXIT_OK


def _verdict(code: int) -> str:
    if code == EXIT_OK:
        return "可以收（三道均正常且无红）"
    if code == EXIT_RED:
        return "不能收（有检查判红）"
    return "结论不成立（有检查没跑成，先修检查器/环境再跑）"


def _record(results: Sequence[StepResult], root: Path, code: int) -> Optional[Path]:
    """写一份 JSON 快照；失败返回 None（由调用方打印 WARN）。"""
    out_dir = root.parent / ".tmp" / "batch_close"
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"batch_close_{stamp}_{os.getpid()}.json"
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "exit_code": code,
        "steps": [
            {
                "name": result.name,
                "status": result.status,
                "rc": result.rc,
                "elapsed_s": round(result.elapsed_s, 3),
                "summary": result.summary,
            }
            for result in results
        ],
    }
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return None
    return path


def _print_report(results: Sequence[StepResult], code: int, record: Optional[Path]) -> None:
    print(f"批次收尾检查（{len(results)} 道）")
    for result in results:
        rc = "-" if result.rc is None else str(result.rc)
        print(
            f"[{result.status}] {result.name:<13} {result.elapsed_s:6.1f}s "
            f"rc={rc:<2} | {result.summary}"
        )
        if result.detail:
            for row in result.detail.splitlines():
                print(f"        {row}")
    print(f"结论：{_verdict(code)}")
    if record is not None:
        print(f"快照：{record}")
    elif code == EXIT_NOT_RUN:
        print("快照：未写入（见上方 WARN）")


def _selected_steps(raw: Optional[str]) -> Optional[list[Step]]:
    """解析 --steps；未知步骤返回 None，由调用方按用法错误退出。"""
    if raw is None:
        return list(STEPS)
    names = [name.strip() for name in raw.split(",")]
    if not names or any(not name for name in names):
        return None
    selected: list[Step] = []
    for name in names:
        step = STEP_BY_NAME.get(name)
        if step is None:
            return None
        if step not in selected:
            selected.append(step)
    return selected


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="批次收尾三道检查的聚合入口（判据仍在各脚本内）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 全绿；1 有检查判红；2 用法错误；3 有检查没跑成。\n"
            "可选步骤：" + ", ".join(step.name for step in STEPS)
        ),
    )
    parser.add_argument(
        "--steps",
        default=None,
        help="逗号分隔的步骤名；缺省全跑（evidence,red_lifecycle,roadmap）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_S,
        help=f"每道检查的独立超时秒数（默认 {DEFAULT_TIMEOUT_S}）",
    )
    parser.add_argument("--json", action="store_true", help="只输出机器可读 JSON")
    parser.add_argument("--no-record", action="store_true", help="不写 .tmp 快照")
    parser.add_argument(
        "--root",
        default=str(ROOT),
        help="仓库根（默认本脚本所在仓库；测试用）",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.timeout <= 0:
        print("用法错误：--timeout 必须是正整数", file=sys.stderr)
        return EXIT_USAGE

    selected = _selected_steps(args.steps)
    if selected is None:
        valid = ", ".join(step.name for step in STEPS)
        print(f'用法错误：未知步骤或空步骤名；可选：{valid}', file=sys.stderr)
        return EXIT_USAGE

    root = Path(args.root).resolve()
    if not (root / "scripts").is_dir():
        print(f"用法错误：{root} 下没有 scripts/ 目录", file=sys.stderr)
        return EXIT_USAGE

    results = [run_step(step, root, args.timeout) for step in selected]
    code = overall_exit(results)

    record: Optional[Path] = None
    if not args.no_record:
        record = _record(results, root, code)
        if record is None:
            print(
                f"[WARN] 快照写入失败：{root.parent / '.tmp' / 'batch_close'}",
                file=sys.stderr,
            )

    if args.json:
        print(
            json.dumps(
                {
                    "exit_code": code,
                    "steps": [
                        {
                            "name": result.name,
                            "status": result.status,
                            "rc": result.rc,
                            "elapsed_s": round(result.elapsed_s, 3),
                            "summary": result.summary,
                        }
                        for result in results
                    ],
                    "record": str(record) if record is not None else None,
                },
                ensure_ascii=False,
            )
        )
    else:
        _print_report(results, code, record)
    return code


if __name__ == "__main__":
    sys.exit(main())
