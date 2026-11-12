# SPDX-License-Identifier: MIT
"""两个写哨兵的公共收尾逻辑（退出码判定 + --wrap 入口）。

`config_write_guard` 与 `qsettings_guard` 监视的对象不同（文件 open/os.replace
vs QSettings.setValue），记账字段、拦截方式、报告格式都不同，**不能合并成一个
哨兵**。真正逐字相同的只有下面这几段进程收尾逻辑，放这里共用，避免两份拷贝
各自漂移（例如只给其中一份补 flush 或改退出码）。
"""
from __future__ import annotations

import atexit
import os
import sys


def log_records(path):
    """数一数哨兵日志里有多少条真实写入（跨进程取证用）。"""
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def install_exit_check(flag, records, report):
    """本进程有真实写入 → 退出码 3（atexit 里 os._exit，能改退出码）。"""
    if getattr(sys, flag, False):
        return False
    setattr(sys, flag, True)

    def _check():
        recs = records()
        if not recs:
            return
        report()
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(3)

    atexit.register(_check)
    return True


def install_log_exit_check(flag, path, phrase, prefix):
    """父进程（driver）按共享日志判定：子进程写过真实写入就非零退出。

    ``phrase`` 是「对……的写入」里的完整宾语（如 ``真实 store 的写入``），
    由调用方给全，避免各模块在这里再拼一次措辞。
    """
    if not path or getattr(sys, flag, False):
        return False
    setattr(sys, flag, True)

    def _check():
        count = log_records(path)
        if not count:
            return
        print("%s %s 记录 %d 次对%s，本脚本退出码置 3"
              % (prefix, path, count, phrase), flush=True)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(3)

    atexit.register(_check)
    return True


def run_wrapped(argv, install, report):
    """--wrap 入口：装哨兵后用 runpy 以 ``__main__`` 跑目标脚本。

    ``argv`` 已去掉解释器名与首项（调用方保证 ``argv[0] == "--wrap"``）。
    """
    target = os.path.abspath(argv[1])
    sys.argv = [target] + argv[2:]
    install()
    import runpy
    try:
        runpy.run_path(target, run_name="__main__")
    finally:
        report()
    return 0
