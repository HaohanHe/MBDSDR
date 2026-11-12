#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""scripts/repro 公用启动段：默认隔离 QSettings / 产品配置文件。

repro 脚本都是「driver 进程 spawn 一个做 Qt 实测的子进程」。driver 侧调
``child_env()`` 注入环境变量、调 ``child_cmd()`` 起子进程；子进程走本文件的
``--run`` 模式::

    python scripts/repro/_qsettings_startup.py --run <script> [args...]

子进程里发生的事都早于被测脚本自己的 ``T0 = time.perf_counter()``：

  1. ``config_write_guard.install()``——包裹 open/os.replace，对用户目录下
     产品配置（如 ``~/.mbdsdr/gui_config.json``）的写入记账；BLOCK=1 时只拦
     不写。
  2. ``qsettings_guard.install_meta_hook()``——不提前 import PySide6，等被测
     脚本自己导入 `PySide6.QtCore` 时再打补丁（把 `QSettings` 换成临时 Ini
     子类并包裹 setValue/remove/clear）。导入耗时仍留在计时窗口内。
  3. ``install_exit_check()``——本进程写真实 store / 真实配置就退出码 3。
  4. driver 侧 ``install_log_exit_check()``——子进程写过，整个脚本退出码 3。

默认把 ``HOME`` / ``USERPROFILE`` / ``APPDATA`` / ``LOCALAPPDATA`` /
``XDG_CONFIG_HOME`` / ``MBDSDR_SETTINGS_DIR`` 指到进程私有临时目录
（``%TEMP%/mbdsdr-repro-sandbox-*``），产品代码即使直写用户目录也落不到真实
环境。``MBDSDR_REPRO_SANDBOX=0`` 显式关闭（反证用）。
``MBDSDR_QS_ISOLATE=0`` 显式关闭 QSettings 重定向（哨兵仍记录）。
日志默认落 ``<repo>/work/`` 下。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.dirname(HERE)
ROOT = os.path.dirname(SCRIPTS)

_LOG = {"path": None, "printed": False}
_CFG_LOG = {"path": None}
_SANDBOX = {"dir": None}

_USER_DIR_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                  "XDG_CONFIG_HOME")


def _guard():
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import qsettings_guard
    return qsettings_guard


def _cfg_guard():
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import config_write_guard
    return config_write_guard


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


def cfg_log_path():
    """产品配置写入哨兵的跨进程日志（放 work/，与 QSettings 那条并列）。"""
    if _CFG_LOG["path"]:
        return _CFG_LOG["path"]
    path = os.environ.get("MBDSDR_CFG_GUARD_LOG")
    if not path:
        work = os.path.join(ROOT, "work")
        os.makedirs(work, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(work, "config-guard-%s-%d.jsonl"
                            % (stamp, os.getpid()))
        os.environ["MBDSDR_CFG_GUARD_LOG"] = path
    _CFG_LOG["path"] = path
    return path


def sandbox_enabled():
    """默认开启；`MBDSDR_REPRO_SANDBOX=0` 显式关闭（反证用）。"""
    return os.environ.get("MBDSDR_REPRO_SANDBOX", "1") != "0"


def sandbox_dir():
    """进程私有临时目录；driver 与它 spawn 的子进程共用同一个。"""
    if _SANDBOX["dir"]:
        return _SANDBOX["dir"]
    path = os.environ.get("MBDSDR_REPRO_SANDBOX_DIR")
    if not path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(tempfile.gettempdir(),
                            "mbdsdr-repro-sandbox-%s-%d" % (stamp, os.getpid()))
    os.makedirs(path, exist_ok=True)
    os.environ["MBDSDR_REPRO_SANDBOX_DIR"] = path
    _SANDBOX["dir"] = path
    return path


def apply_sandbox(env=None):
    """把用户目录变量指到临时目录；env=None 时改本进程 os.environ。

    ``MBDSDR_REPRO_SANDBOX=0`` 时不动任何变量，返回 None。
    """
    if not sandbox_enabled():
        return None
    path = sandbox_dir()
    target = os.environ if env is None else env
    for key in _USER_DIR_VARS:
        target[key] = path
    target["MBDSDR_SETTINGS_DIR"] = os.path.join(path, "mbdsdr")
    target["MBDSDR_REPRO_SANDBOX_DIR"] = path
    return path


def child_env(env=None):
    """给子进程的环境：默认隔离 + 两套哨兵 + 共享日志，已有值不覆盖。"""
    env = dict(os.environ if env is None else env)
    env.setdefault("MBDSDR_QS_ISOLATE", "1")
    env.setdefault("MBDSDR_QS_GUARD", "1")
    env.setdefault("MBDSDR_QS_GUARD_BLOCK", "1")
    env.setdefault("MBDSDR_CFG_GUARD", "1")
    env.setdefault("MBDSDR_CFG_GUARD_BLOCK", "1")
    log = env.get("MBDSDR_QS_GUARD_LOG") or guard_log_path()
    env["MBDSDR_QS_GUARD_LOG"] = log
    cfg_log = env.get("MBDSDR_CFG_GUARD_LOG") or cfg_log_path()
    env["MBDSDR_CFG_GUARD_LOG"] = cfg_log
    sandbox = apply_sandbox(env)
    _guard().install_log_exit_check(log)
    _cfg_guard().install_log_exit_check(cfg_log)
    if not _LOG["printed"]:
        _LOG["printed"] = True
        print("[repro-guard] isolate=%s sandbox=%s qs_log=%s cfg_log=%s"
              % (env["MBDSDR_QS_ISOLATE"], sandbox or "off", log, cfg_log),
              flush=True)
    return env


def child_cmd(script, args=()):
    """子进程命令：一律经本文件启动，才能在 T0 前装好补丁。"""
    return [sys.executable, os.path.abspath(__file__), "--run",
            os.path.abspath(script)] + [str(a) for a in args]


def _run(target, args):
    apply_sandbox()
    os.environ.setdefault("MBDSDR_CFG_GUARD", "1")
    os.environ.setdefault("MBDSDR_CFG_GUARD_BLOCK", "1")
    if not os.environ.get("MBDSDR_CFG_GUARD_LOG"):
        cfg_log_path()
    cfg = _cfg_guard()
    cfg.install()
    cfg.install_exit_check()

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
