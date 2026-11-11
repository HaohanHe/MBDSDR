#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""QSettings 写入哨兵：抓「谁在写真实存储」。

背景（2026-11-12）：`HKCU\\Software\\MBDSDR\\Desktop` 的 `three_column_sizes`
在本机被写成了 `[260, 2000, 326]`，来源不明。pytest 侧已有隔离，但只在
pytest 里生效；`scripts/repro` 与其它独立进程照样直连真实 QSettings。

判据：**只对非临时 store 记账**。临时 store = `%TEMP%` 下（含 conftest 的
`mbdsdr-qsettings-*` 会话目录）或 `MBDSDR_SCRATCH_ROOT`（默认
`D:\\mbdsdr\\output\\tmp`，repro 脚本自己的隔离根）。注册表路径一律算真实。

用法（pytest 外的公用入口）::

    python scripts/qsettings_guard.py --wrap scripts/repro/xxx.py [args...]

wrap 模式装好哨兵后用 ``runpy`` 以 ``__main__`` 跑目标脚本；结束时打印
每条写入的 `caller=<module>:<func> file=<file>:<line>` 与调用栈。
子进程不在覆盖范围内：目标脚本自己 spawn 的进程要另包一层。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import traceback

_RECORDS = []
_PRINTED = set()
_LOCK = threading.RLock()
_INSTALLED = False
_MAX_PRINT = 20


def _temp_roots():
    roots = [tempfile.gettempdir()]
    extra = os.environ.get("MBDSDR_SCRATCH_ROOT", r"D:\mbdsdr\output\tmp")
    roots.append(extra)
    out = []
    for root in roots:
        if not root:
            continue
        out.append(os.path.abspath(root).rstrip("\\/").lower())
    return out


def _is_temp(path):
    if not path:
        return True
    p = os.path.abspath(path).rstrip("\\/").lower()
    if p.startswith("hkey"):
        return False
    for root in _temp_roots():
        if p == root or p.startswith(root + "\\") or p.startswith(root + "/"):
            return True
    return "mbdsdr-qsettings-" in p


def _append_log(rec):
    """子进程的 stdout/stderr 常被父进程 capture，落文件才拿得回来。"""
    path = os.environ.get("MBDSDR_QS_GUARD_LOG")
    if not path:
        return
    try:
        import json
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "pid": os.getpid(), "module": rec["module"],
                "func": rec["func"], "file": rec["file"], "line": rec["line"],
                "op": rec["op"], "key": rec["key"], "store": rec["store"],
                "stack": "".join(rec["stack"][-6:]),
            }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _caller():
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_globals.get("__name__") != __name__:
            return frame
        frame = frame.f_back
    return None


def _note(instance, op, args):
    try:
        store = instance.fileName()
    except Exception:  # noqa: BLE001 - 悬空包装器仍要记账
        store = "<unavailable>"
    if _is_temp(store):
        return
    frame = _caller()
    if frame is None:
        return
    rec = {
        "module": frame.f_globals.get("__name__", "?"),
        "func": frame.f_code.co_name,
        "file": frame.f_code.co_filename,
        "line": frame.f_lineno,
        "op": op,
        "key": args[0] if args else "",
        "store": store,
        "stack": traceback.format_stack(frame),
    }
    with _LOCK:
        _RECORDS.append(rec)
        _append_log(rec)
        tag = (rec["module"], rec["func"], rec["file"], rec["line"], op)
        if len(_PRINTED) < _MAX_PRINT or tag in _PRINTED:
            if tag not in _PRINTED:
                _PRINTED.add(tag)
                print("[qsettings-guard] caller=%s:%s file=%s:%s store=%s "
                      "op=%s key=%s" % (rec["module"], rec["func"], rec["file"],
                                        rec["line"], rec["store"], op,
                                        rec["key"]), file=sys.stderr, flush=True)
                if len(_PRINTED) <= 3:
                    print("".join(rec["stack"]), file=sys.stderr, flush=True)


def install():
    """包裹 QSettings.setValue/remove/clear；重复调用无副作用。"""
    global _INSTALLED
    if _INSTALLED or os.environ.get("MBDSDR_QS_GUARD", "1") == "0":
        return False
    from PySide6.QtCore import QSettings

    if getattr(QSettings, "_mbdsdr_qguard", False):
        _INSTALLED = True
        return False

    original = {name: getattr(QSettings, name)
                for name in ("setValue", "remove", "clear")}

    def make(op, fn):
        def wrapper(self, *args, **kwargs):
            _note(self, op, args)
            if os.environ.get("MBDSDR_QS_GUARD_BLOCK") == "1":
                try:
                    blocked = not _is_temp(self.fileName())
                except Exception:  # noqa: BLE001
                    blocked = True
                if blocked:
                    return None
            return fn(self, *args, **kwargs)

        wrapper.__name__ = op
        wrapper.__doc__ = getattr(fn, "__doc__", None)
        return wrapper

    for name, fn in original.items():
        setattr(QSettings, name, make(name, fn))
    QSettings._mbdsdr_qguard = True
    _INSTALLED = True
    return True


def records():
    with _LOCK:
        return list(_RECORDS)


def reset():
    with _LOCK:
        _RECORDS.clear()
        _PRINTED.clear()


def report(prefix="[qsettings-guard]"):
    recs = records()
    if not recs:
        print("%s 未捕获到对真实 store 的写入" % prefix, flush=True)
        return 0
    print("%s 捕获 %d 次对真实 store 的写入：" % (prefix, len(recs)), flush=True)
    seen = {}
    for r in recs:
        key = (r["module"], r["func"], r["file"], r["line"], r["op"])
        seen[key] = seen.get(key, 0) + 1
    for (module, func, file, line, op), count in seen.items():
        print("%s   %s:%s file=%s:%s op=%s ×%d"
              % (prefix, module, func, file, line, op, count), flush=True)
    first = recs[0]
    print("%s 首个写入的调用栈：" % prefix, flush=True)
    print("".join(first["stack"]), flush=True)
    return len(recs)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "--wrap":
        print(__doc__)
        return 2
    target = os.path.abspath(argv[1])
    sys.argv = [target] + argv[2:]
    install()
    import runpy
    try:
        runpy.run_path(target, run_name="__main__")
    finally:
        report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
