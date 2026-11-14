#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""HEAD 可构建烟雾检查：在**指定提交自己的检出**上跑一组既有的「能构造主窗口/面板」用例。

要防的事故形态（2026-11-14 实测确认）：``12bcdb7a``（09:10:36）把另一线当时未提交
的调用侧改动整份带走——去掉面板类 import、8 处构造改走 ``_create_optional_panel``
——而该方法定义直到 ``b129905f``（09:15:08）才落地。这 272 秒内 HEAD 上构造主窗口
直接 ``AttributeError``。提交前那几道闸门（claim、safe_commit 的声明快照、
提交后残留检查）判的都是「有没有夹带别人的改动 / 声明后有没有新改动」，**不判
「提交出去的东西自身能不能构建」**；仓库里早就存在能抓住它的用例
（``tests/test_panel_registry_factory.py`` 在 ``12bcdb7a`` 上是红），但没有任何
环节在提交后跑过一次。

判据：
  * 在 ``git worktree`` 里检出被检查的提交（不复用工作区，避免别线未提交改动
    污染结论），只跑 ``SMOKE_TESTS`` 这组最小既有用例；
  * 绿：退出码 0，打印一行实测耗时；
  * 红：退出码 3，醒目打印「sha + 失败用例 + 首条错误 + 台账路径 + 耗时」，
    并追加 JSONL 台账；
  * 检查自身跑不起来（找不到用例、pytest 崩了、Qt 全局锁等不到、超时）：
    退出码 4，同样醒目打印 + 记账——**不允许静默跳过**。

三种结局都写台账，任何一笔提交事后都能查到「跑过没有、结论是什么」。

用法：
  python scripts/head_build_smoke.py                    # 检查当前 HEAD
  python scripts/head_build_smoke.py --commit <sha>     # 检查指定提交
  python scripts/head_build_smoke.py --force            # 即使本次提交未触及构建输入也跑

环境变量：
  MBDSDR_LOCKS_DIR           状态/台账目录（默认按 git 公共目录推导，见 ``_locks_dir``）
  MBDSDR_HEAD_BUILD_LOG      台账路径（默认 ``<locks>/head_build_smoke.log``）
  MBDSDR_HEAD_BUILD_SMOKE=0  显式关闭（会打印并记一条 skipped，不是静默跳过）
  MBDSDR_TEST_LOCK_TIMEOUT   透传给 conftest 的 Qt 全局锁等待秒数（默认 45）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

#: 最小既有用例集：import 得住 + 逐面板构造 + 主窗口整体构造。
#: 实测四组用例 24 项、pytest 用时约 3.4~4.7 s（含 PySide6 import）。
SMOKE_TESTS = (
    "tests/test_main_window_import_smoke.py",
    "tests/test_panel_registry_factory.py::test_create_builds_fresh_widget_per_call",
    "tests/test_panel_registry_factory.py::test_main_window_builds_panels_through_registry",
    "tests/test_panel_registry_factory.py::test_each_declared_panel_factory_produces_widget",
)

#: 只在这些路径被改动时才值得跑（文档/数据类提交不可能影响构造）。
BUILD_INPUT_PREFIXES = (
    "desktop/",
    "mbdsdr_ai/",
    "tools/",
    "assets/",
    "tests/",
)

EXIT_OK = 0
EXIT_RED = 3
EXIT_UNKNOWN = 4

PYTEST_TIMEOUT_S = 180
RUN_LOCK_WAIT_S = 20
QT_LOCK_WAIT_S = 45


def _clean_env() -> dict:
    """剥掉调用方的私有 index / 工作树指针（post-commit 可能带 GIT_INDEX_FILE）。"""
    env = os.environ.copy()
    for key in ("GIT_INDEX_FILE", "GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX"):
        env.pop(key, None)
    return env


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_clean_env(),
    )


def _locks_dir(repo: Path) -> Path:
    """与 tests/conftest.py 同一口径：``<公共目录的父目录>/.locks``。"""
    raw = os.environ.get("MBDSDR_LOCKS_DIR")
    if raw:
        return Path(raw)
    proc = _git(repo, "rev-parse", "--git-common-dir")
    common = Path(proc.stdout.strip() or ".git")
    if not common.is_absolute():
        common = (repo / common).resolve()
    root = common.parent if common.name == ".git" else common
    return root.parent / ".locks"


def _ledger_path(repo: Path) -> Path:
    raw = os.environ.get("MBDSDR_HEAD_BUILD_LOG")
    return Path(raw) if raw else _locks_dir(repo) / "head_build_smoke.log"


def _changed_paths(repo: Path, commit: str) -> list[str]:
    proc = _git(repo, "show", "--format=", "--name-only", "--no-renames", commit)
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _touches_build_inputs(paths: list[str]) -> bool:
    """提交是否触及可能影响「主窗口/面板构造」的输入。

    文档、数据、登记表一律不跑：它们不可能让构造失败，跑了只是拖慢提交。
    ``tests/`` 也算构建输入——用例本身被改坏时要能当场发现。
    """
    for raw in paths:
        path = raw.replace("\\", "/")
        if path.startswith(BUILD_INPUT_PREFIXES):
            return True
        if path.endswith(".py"):
            return True
    return False


class _RunLock:
    """烟雾检查自身的互斥锁（跨进程，进程退出即释放）。"""

    def __init__(self, path: Path, wait_s: float):
        self.path = path
        self.wait_s = wait_s
        self._fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a+b")
        deadline = time.monotonic() + self.wait_s
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    self._fh.close()
                    self._fh = None
                    return False
                time.sleep(0.2)

    def __exit__(self, *exc_info):
        if self._fh is None:
            return False
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None
        return False


def _ensure_worktree(repo: Path, commit: str, state: Path) -> tuple[Path, str, int]:
    """把检查用的 worktree 指到 ``commit``；返回（路径, 动作, 耗时毫秒）。"""
    wt = state / "wt"
    started = time.monotonic()
    if (wt / ".git").exists():
        proc = _git(wt, "checkout", "--detach", "--force", "--quiet", commit)
        if proc.returncode == 0:
            return wt, "update", int((time.monotonic() - started) * 1000)
        _git(repo, "worktree", "remove", "--force", str(wt))
    _git(repo, "worktree", "prune")
    wt.parent.mkdir(parents=True, exist_ok=True)
    proc = _git(repo, "worktree", "add", "--detach", str(wt), commit)
    if proc.returncode != 0:
        raise RuntimeError(
            f"git worktree add 失败：{(proc.stderr or proc.stdout).strip()[:400]}"
        )
    return wt, "create", int((time.monotonic() - started) * 1000)


def _run_pytest(wt: Path, nodes, junit: Path, timeout_s: int):
    """在检出里跑最小用例集；返回（返回码或 None, 输出尾部, 错误类型, 耗时毫秒）。"""
    env = _clean_env()
    env["QT_QPA_PLATFORM"] = env.get("QT_QPA_PLATFORM") or "offscreen"
    env.setdefault("MBDSDR_TEST_LOCK_TIMEOUT", str(QT_LOCK_WAIT_S))
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *nodes,
        "-q",
        "-p",
        "no:cacheprovider",
        "--tb=line",
        f"--junitxml={junit}",
    ]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(wt),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        tail = (exc.stdout or "") + (exc.stderr or "")
        return None, str(tail)[-1500:], "timeout", elapsed
    except OSError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return None, f"{exc.__class__.__name__}: {exc}", "spawn_error", elapsed
    elapsed = int((time.monotonic() - started) * 1000)
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output[-1500:], None, elapsed


def _parse_junit(path: Path) -> tuple[list[dict], int]:
    """从 junit xml 取（失败/错误用例及其首条错误行, 实际收集到的用例数）。"""
    failures: list[dict] = []
    if not path.exists():
        return failures, 0
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError):
        return failures, 0
    collected = 0
    for case in root.iter("testcase"):
        collected += 1
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is None:
                continue
            message = (node.get("message") or "").strip()
            if message.startswith('failed on setup with "') and message.endswith('"'):
                message = message[len('failed on setup with "') : -1]
            first_line = message.splitlines()[0].strip() if message else ""
            if not first_line:
                text = (node.text or "").strip().splitlines()
                first_line = text[0].strip() if text else "(无错误文本)"
            classname = (case.get("classname") or "").replace(".", "/")
            module = classname + ".py" if classname else "(unknown)"
            nodeid = f"{module}::{case.get('name') or '?'}"
            failures.append(
                {"nodeid": nodeid, "first_line": first_line, "kind": kind}
            )
            break
    return failures, collected


def _status_of(returncode, failures, error_kind) -> str:
    if error_kind:
        return "error"
    if returncode == 0 and not failures:
        return "pass"
    if returncode == 5:
        return "error"
    if returncode == 2:
        return "error"
    if returncode == 1 and failures:
        return "fail"
    return "error"


def _append_ledger(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _print_skipped(sha: str, reason: str) -> None:
    print(f"[head-build-smoke] - {sha[:8]} 跳过：{reason}")


def _print_green(record: dict) -> None:
    print(
        "[head-build-smoke] ✓ %s 可构建（%d 项用例，pytest %dms，检出 %dms，合计 %dms）"
        % (
            record["commit"][:8],
            record.get("tests_collected") or len(SMOKE_TESTS),
            record["pytest_ms"],
            record["checkout_ms"],
            record["elapsed_ms"],
        )
    )


def _print_alert(record: dict) -> None:
    bar = "=" * 70
    title = "✗ HEAD 不可构建" if record["status"] == "fail" else "✗ 烟雾检查无法判定"
    print(f"[head-build-smoke] {bar}")
    print(
        f"[head-build-smoke] {title}：{record['commit'][:8]} {record.get('subject', '')}"
    )
    if record["status"] == "fail":
        print(f"[head-build-smoke]   失败用例（{len(record['failures'])}）：")
        for item in record["failures"]:
            print(f"[head-build-smoke]     {item['nodeid']}")
            print(f"[head-build-smoke]       {item['first_line']}")
    else:
        print(f"[head-build-smoke]   原因：{record.get('reason', '未知')}")
        tail = (record.get("output_tail") or "").strip().splitlines()
        for line in tail[-6:]:
            print(f"[head-build-smoke]     {line}")
    print(f"[head-build-smoke]   台账：{record['ledger']}")
    print(
        "[head-build-smoke]   耗时：检出 %dms + pytest %dms = %dms"
        % (record["checkout_ms"], record["pytest_ms"], record["elapsed_ms"])
    )
    print(f"[head-build-smoke] {bar}")


def _record_and_report(ledger: Path, record: dict) -> int:
    _append_ledger(ledger, record)
    status = record["status"]
    if status == "fail":
        _print_alert(record)
        return EXIT_RED
    if status == "error":
        _print_alert(record)
        return EXIT_UNKNOWN
    if status == "pass":
        _print_green(record)
        return EXIT_OK
    _print_skipped(record["commit"], record.get("reason", ""))
    return EXIT_OK


def run(repo: Path, commit: str | None, force: bool) -> int:
    repo = repo.resolve()
    ledger = _ledger_path(repo)
    started = time.monotonic()

    proc = _git(repo, "rev-parse", "--verify", f"{commit or 'HEAD'}^{{commit}}")
    if proc.returncode != 0:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "type": "HEAD_BUILD_SMOKE",
            "status": "error",
            "commit": commit or "HEAD",
            "subject": "",
            "failures": [],
            "reason": f"提交不可解析：{(proc.stderr or '').strip()[:200]}",
            "ledger": str(ledger),
            "elapsed_ms": 0,
            "checkout_ms": 0,
            "pytest_ms": 0,
        }
        return _record_and_report(ledger, record)
    sha = proc.stdout.strip()
    subject = _git(repo, "log", "-1", "--format=%s", sha).stdout.strip()
    files = _changed_paths(repo, sha)

    base = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "type": "HEAD_BUILD_SMOKE",
        "commit": sha,
        "subject": subject,
        "files": files,
        "tests": list(SMOKE_TESTS),
        "ledger": str(ledger),
    }

    if (os.environ.get("MBDSDR_HEAD_BUILD_SMOKE") or "1").strip() == "0":
        record = dict(
            base,
            status="skipped",
            reason="MBDSDR_HEAD_BUILD_SMOKE=0 显式关闭（可见记录，不是静默跳过）",
            failures=[],
            checkout_ms=0,
            pytest_ms=0,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return _record_and_report(ledger, record)

    if not force and not _touches_build_inputs(files):
        record = dict(
            base,
            status="skipped",
            reason=f"本次提交未触及构建输入（{len(files)} 个文件均为文档/数据类）",
            failures=[],
            checkout_ms=0,
            pytest_ms=0,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return _record_and_report(ledger, record)

    state = _locks_dir(repo) / "head_build_smoke"
    with _RunLock(state / "run.lock", RUN_LOCK_WAIT_S) as acquired:
        if not acquired:
            record = dict(
                base,
                status="error",
                reason=f"另一个烟雾检查占用运行锁超过 {RUN_LOCK_WAIT_S}s，本次未判定",
                failures=[],
                checkout_ms=0,
                pytest_ms=0,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            return _record_and_report(ledger, record)

        try:
            wt, action, checkout_ms = _ensure_worktree(repo, sha, state)
        except Exception as exc:  # noqa: BLE001 —— 检查自身故障必须可见
            record = dict(
                base,
                status="error",
                reason=f"检出 {sha[:8]} 失败：{exc}",
                failures=[],
                checkout_ms=0,
                pytest_ms=0,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            return _record_and_report(ledger, record)

        got = _git(wt, "rev-parse", "HEAD").stdout.strip()
        if got != sha:
            record = dict(
                base,
                status="error",
                reason=f"检出校验失败：worktree HEAD={got[:12]} ≠ 目标 {sha[:12]}",
                failures=[],
                checkout_ms=checkout_ms,
                pytest_ms=0,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )
            return _record_and_report(ledger, record)

        junit = state / "junit.xml"
        junit.unlink(missing_ok=True)
        rc, tail, error_kind, pytest_ms = _run_pytest(
            wt, SMOKE_TESTS, junit, PYTEST_TIMEOUT_S
        )
        failures, collected = _parse_junit(junit)
        status = _status_of(rc, failures, error_kind)
        reason = ""
        if status == "error":
            if error_kind == "timeout":
                reason = f"pytest 超时（>{PYTEST_TIMEOUT_S}s）"
            elif error_kind == "spawn_error":
                reason = f"pytest 无法启动：{tail.splitlines()[:1]}"
            elif rc == 5:
                reason = "pytest 没收集到任何用例（用例被删/改坏），不能算通过"
            elif rc == 2:
                reason = "pytest 被中断（常见：Qt 全局锁等待超时或收集中断）"
            else:
                reason = f"pytest 退出码 {rc}，无法归入红/绿"
        record = dict(
            base,
            status=status,
            reason=reason,
            failures=failures,
            tests_collected=collected,
            worktree=str(wt),
            worktree_action=action,
            returncode=rc,
            output_tail=tail if status == "error" else "",
            checkout_ms=checkout_ms,
            pytest_ms=pytest_ms,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        return _record_and_report(ledger, record)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="在指定提交自己的检出上跑最小「主窗口/面板构造」用例集"
    )
    parser.add_argument("--repo", default=".", help="仓库路径（默认当前目录）")
    parser.add_argument("--commit", default="HEAD", help="被检查的提交（默认 HEAD）")
    parser.add_argument(
        "--force",
        action="store_true",
        help="即使本次提交未触及构建输入也跑",
    )
    args = parser.parse_args(argv)
    return run(Path(args.repo), args.commit, args.force)


if __name__ == "__main__":
    sys.exit(main())
