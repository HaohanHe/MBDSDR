#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""批次收尾检查的唯一命名入口。

背景
----
``docs/COORDINATION.md`` 把批次末要跑的三道检查写成三条独立命令，并要求当班
owner 把「红/绿 + 条数」抄进台账。实际执行时有三处靠人：

* 记住是哪三道（漏一道不会有人发现）；
* 分别跑三次、分别看退出码（一道红时容易不再跑后两道）；
* 自己把结论合并成一句（三个退出码的语义各不相同）。

本工具只做聚合，**不改变任何一道检查自身的判据**，也不进 pre-commit（前两道
单次耗时远超钩子预算，塞进钩子只会让人开始用 ``--no-verify``）。

检查步骤（与台账同源）
----------------------
====================  ==========================================  ==========================
步骤名                脚本                                        退出码含义
====================  ==========================================  ==========================
``evidence``          ``scripts/audit_evidence.py``               0 无红 / 1 有红
``red_lifecycle``     ``scripts/check_red_lifecycle.py``          0 无 FAIL / 1 有 FAIL / 2 用法或 git 错误
``roadmap``           ``scripts/check_roadmap_blocking.py``       恒 0（提醒器，只作提示）
``cleanup``           ``scripts/file_claim.py cleanup-stale``     0 主数为空 / 3 有可释放未释放（都算 NOTE）
====================  ==========================================  ==========================

行为
----
* 按固定顺序**全跑**：一道判红不会遮住后两道的结论；
* 每道有独立超时（默认 300 s），超时按「没跑成」处理，不按红处理；
* **``cleanup`` 是 NOTE 级**：只把「可释放未释放 / UNDECIDABLE / SUSPECT」
  抄进报告与趋势，**不参与 0/1/3 的红绿判定**——「有可释放未释放」不是缺陷，
  只是有活没干；把它算成红，批次会因为「今天没人清理」而变红，那种红没有
  意义。它的 SUSPECT 也不进任何自动判定，只在报告里逐条给缺口和补法。
  只有它自己没跑成（脚本缺失、超时、崩溃、输出不是可解析 JSON）才升级成
  ``ERROR``/退出码 3——没跑成就不能算「没有待办」。
* 各道都跑完后，把结果压成一行摘要，并默认留一份 JSON 快照到
  ``<仓库父目录>/.tmp/batch_close/``（D 盘；不写仓库、不写 C 盘）；
* 快照写失败只打印 WARN，不改退出码——记录失败不是判据失败，但不能静默；
* **rc=1 的崩溃启发式**：三道检查目前没有顶层异常捕获，检查器自己抛异常也
  会以 1 退出，与「真判红」共用同一个码。本工具看 stderr 是否含 Python
  回溯（``Traceback (most recent call last):`` + 异常行），命中就把该道归成
  ``ERROR``（退出码 3），并在输出里写明「启发式，不是判断」。这是减速带，
  不是结论：脚本自己打印 traceback 再判红会被误归，脚本崩溃但没打 traceback
  仍会被当真红。长期方案是三道脚本各自顶层捕获异常、用专用退出码（待办见
  ``docs/code-guards.md`` 的「复合入口」小节），届时这段可以删掉。

退出码
------
===  ================================================================
码   含义
===  ================================================================
0    各道全部正常且无红（``roadmap`` / ``cleanup`` 为 NOTE，只作提示）
1    至少一道判红（该道以 1 退出）；其余检查均正常跑完
2    用法错误：未知 ``--steps``、非正 ``--timeout``、``--root`` 下没有 scripts/
3    至少一道**没能正常执行**（脚本缺失、超时、退出码不是 0/1，或 rc=1 但
     stderr 有 Python 回溯的疑似崩溃——最后一项是启发式，不是判断），红绿结论不成立
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
import re
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

#: rc=1 的崩溃启发式：只认 stderr 里的 Python 回溯，**不是**红绿判断。
#: 三道检查目前共用 0/1/2，检查器自己抛异常也是 1；不改脚本就只能先看形状。
TRACEBACK_MARK = "Traceback (most recent call last):"
_EXC_LINE_RE = re.compile(
    r"^(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Interrupt)\b.*$"
)


def _crash_signature(stderr: str) -> Optional[str]:
    """stderr 里像 Python 崩溃的结尾行；没有回溯头就返回 None。

    只看 stderr：检查脚本可能把「Traceback」当**数据**打印到 stdout（例如
    audit_evidence 扫到的文本），拿 stdout 判断会把正常结果误归成崩溃。
    """
    text = stderr or ""
    if TRACEBACK_MARK not in text:
        return None
    rows = [row.strip() for row in text.splitlines() if row.strip()]
    for row in reversed(rows):
        if _EXC_LINE_RE.match(row):
            return row if len(row) <= 200 else row[:197] + "..."
    return "Traceback（未找到异常行）"


@dataclass(frozen=True)
class Step:
    """一道检查：只描述「跑哪个脚本」，判据仍在脚本自己手里。"""

    name: str
    script: str
    args: tuple[str, ...] = ()
    advisory: bool = False
    #: 这些退出码也按 NOTE 处理（只提示、不判红）。目前只有 ``cleanup`` 的 3：
    #: 「有可释放未释放」是有活没干，不是缺陷。
    note_rcs: tuple[int, ...] = ()
    #: stdout 是 JSON，用它生成摘要 / 指标（而不是只取最后一行）。
    json_output: bool = False


STEPS: tuple[Step, ...] = (
    Step("evidence", "audit_evidence.py"),
    Step("red_lifecycle", "check_red_lifecycle.py"),
    Step("roadmap", "check_roadmap_blocking.py", advisory=True),
    Step(
        "cleanup",
        "file_claim.py",
        args=("cleanup-stale", "--json"),
        advisory=True,
        note_rcs=(0, 3),
        json_output=True,
    ),
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
    #: 非空 = 该条 ERROR 来自 rc=1 的回溯启发式，值是识别到的异常行。
    heuristic: str = ""
    #: 机器可读计数（目前只有 cleanup）；写进 JSON 与快照。
    metrics: Optional[dict] = None


def _tail(text: str, lines: int = TAIL_LINES) -> str:
    rows = [row.rstrip() for row in (text or "").splitlines() if row.strip()]
    return "\n".join(rows[-lines:])


def _summary(text: str) -> str:
    rows = [row.strip() for row in (text or "").splitlines() if row.strip()]
    if not rows:
        return "(无输出)"
    last = rows[-1]
    return last if len(last) <= 200 else last[:197] + "..."


def _cleanup_note(stdout: str) -> tuple[Optional[dict], str, str]:
    """解析 ``file_claim.py cleanup-stale --json``：返回 (指标, 摘要, 详情)。

    解析失败返回 ``(None, "", "")``，由调用方按「没跑成」处理——输出不可读
    就等于没有结论，不能当成「没有待办」。
    """
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return None, "", ""
    if not isinstance(payload, dict):
        return None, "", ""

    counts = payload.get("summary") or {}
    undecidable = payload.get("undecidable") or {}
    ratio = float(undecidable.get("ratio") or 0.0)
    summary = (
        f"待办·可释放未释放 {int(counts.get('releasable', 0))}；"
        f"UNDECIDABLE {int(undecidable.get('count', 0))}（{ratio:.1%}）；"
        f"SUSPECT {int(counts.get('suspect', 0))}；"
        f"租约未过期 {int(counts.get('live', 0))}；"
        f"过期未满宽限 {int(counts.get('wait', 0))}"
    )
    lines: list[str] = []
    for row in payload.get("rows") or []:
        if row.get("category") != "suspect":
            continue
        gaps = "；".join(row.get("gaps") or []) or "（无缺口描述）"
        lines.append(
            f"SUSPECT {row.get('path', '?')} owner={row.get('owner', '?')} "
            f"过期={row.get('expired_min', 0)}min 缺口：{gaps}"
        )
        if row.get("hint"):
            lines.append(f"  补法：{row['hint']}")
    metrics = {
        "total": int(payload.get("total") or 0),
        "releasable": int(counts.get("releasable", 0)),
        "undecidable": int(undecidable.get("count", 0)),
        "undecidable_ratio": ratio,
        "undecidable_reasons": undecidable.get("reasons") or {},
        "suspect": int(counts.get("suspect", 0)),
        "live": int(counts.get("live", 0)),
        "wait": int(counts.get("wait", 0)),
    }
    return metrics, summary, "\n".join(lines)


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
            [sys.executable, str(script), *step.args],
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

    if proc.returncode in step.note_rcs:
        # NOTE 级：约定内退出码只提示，不参与红绿；但输出不可读时不能算「没有待办」。
        if step.json_output:
            metrics, note_summary, note_detail = _cleanup_note(proc.stdout or "")
            if metrics is None:
                return StepResult(
                    name=step.name,
                    status="ERROR",
                    rc=proc.returncode,
                    elapsed_s=elapsed,
                    summary="输出不是可解析 JSON，无法确认待办",
                    detail=_tail(combined),
                )
            return StepResult(
                name=step.name,
                status="NOTE",
                rc=proc.returncode,
                elapsed_s=elapsed,
                summary=note_summary,
                detail=note_detail,
                metrics=metrics,
            )
        return StepResult(
            name=step.name,
            status="NOTE",
            rc=proc.returncode,
            elapsed_s=elapsed,
            summary=summary,
            detail=_tail(combined),
        )
    if proc.returncode == 0:
        return StepResult(
            name=step.name,
            status="NOTE" if step.advisory else "PASS",
            rc=0,
            elapsed_s=elapsed,
            summary=summary,
        )
    if proc.returncode == 1 and not step.advisory:
        crash = _crash_signature(proc.stderr or "")
        if crash:
            return StepResult(
                name=step.name,
                status="ERROR",
                rc=1,
                elapsed_s=elapsed,
                summary=(
                    f"rc=1，但 stderr 有 Python 回溯：{crash}"
                    f"（启发式：疑似检查器崩溃，不是判红）"
                ),
                detail=_tail(combined),
                heuristic=crash,
            )
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
        return "可以收（各道均正常且无红）"
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
                "heuristic": result.heuristic,
                "metrics": result.metrics,
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
        if result.heuristic:
            print("        （启发式：stderr 出现 Python 回溯，按「没跑成」处理，"
                  "不是红绿判断）")
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
        description="批次收尾检查的聚合入口（判据仍在各脚本内）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 全绿；1 有检查判红；2 用法错误；3 有检查没跑成。\n"
            "可选步骤：" + ", ".join(step.name for step in STEPS)
        ),
    )
    parser.add_argument(
        "--steps",
        default=None,
        help="逗号分隔的步骤名；缺省全跑（evidence,red_lifecycle,roadmap,cleanup）",
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
                            "heuristic": result.heuristic,
                            "metrics": result.metrics,
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
