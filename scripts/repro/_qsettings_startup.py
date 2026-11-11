#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""scripts/repro 公用启动段：默认把 QSettings 重定向到临时 ini，并装写入哨兵。

repro 脚本都是「driver 进程 spawn 一个做 Qt 实测的子进程」。driver 侧调
``child_env()`` 注入环境变量、调 ``child_cmd()`` 起子进程；子进程走本文件的
``--run`` 模式::

    python scripts/repro/_qsettings_startup.py --run <script> [args...]

子进程里发生的事都早于被测脚本自己的 ``T0 = time.perf_counter()``：

  1. ``qsettings_guard.install_meta_hook()``——不提前 import PySide6，等被测
     脚本自己导入 `PySide6.QtCore` 时再打补丁（把 `QSettings` 换成临时 Ini
     子类并包裹 setValue/remove/clear）。导入耗时仍留在计时窗口内。
  2. ``install_exit_check()``——本进程写真实 store 就退出码 3。
  3. driver 侧 ``install_log_exit_check()``——子进程写过，整个脚本退出码 3。

`MBDSDR_QS_ISOLATE=0` 显式关闭重定向（哨兵仍记录，用于反证没被短路）。
日志默认落 ``<repo>/work/qsettings-guard-<时间戳>-<pid>.jsonl``。
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
ROOT = os.path.dirname(SCRIPTS)

_LOG = {"path": None, "printed": False}


def _guard():
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import qsettings_guard
    return qsettings_guard


def guard_log_path():
    """driver 进程内固定一条日志；子进程通过环境变量继承同一条。"""
    if _LOG["path"]:
        return _LOG["path"]
    path = os.environ.get("MBDSDR_QS_GUARD_LOG")
    if not path:
        work = os.path.join(ROOT, "work")
        os.makedirs(work, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(work, "qsettings-guard-%s-%d.jsonl"
                            % (stamp, os.getpid()))
        os.environ["MBDSDR_QS_GUARD_LOG"] = path
    _LOG["path"] = path
    return path


def child_env(env=None):
    """给子进程的环境：默认隔离 + 哨兵 + 共享日志，已有值不覆盖。"""
    env = dict(os.environ if env is None else env)
    env.setdefault("MBDSDR_QS_ISOLATE", "1")
    env.setdefault("MBDSDR_QS_GUARD", "1")
    env.setdefault("MBDSDR_QS_GUARD_BLOCK", "1")
    log = env.get("MBDSDR_QS_GUARD_LOG") or guard_log_path()
    env["MBDSDR_QS_GUARD_LOG"] = log
    _guard().install_log_exit_check(log)
    if not _LOG["printed"]:
        _LOG["printed"] = True
        print("[qsettings-guard] isolate=%s log=%s"
              % (env["MBDSDR_QS_ISOLATE"], log), flush=True)
    return env


def child_cmd(script, args=()):
    """子进程命令：一律经本文件启动，才能在 T0 前装好补丁。"""
    return [sys.executable, os.path.abspath(__file__), "--run",
            os.path.abspath(script)] + [str(a) for a in args]


def _run(target, args):
    guard = _guard()
    guard.install_meta_hook()
    guard.install_exit_check()
    sys.argv = [target] + list(args)
    import runpy
    runpy.run_path(target, run_name="__main__")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "--run":
        print(__doc__)
        return 2
    _run(argv[1], argv[2:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
