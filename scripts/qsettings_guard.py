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

2026-11-12 追加「默认重定向」：`install_isolation()` 把 `QSettings` 符号换成
子类，两参字符串构造（`QSettings("MBDSDR", "Desktop")`）改写成显式
`IniFormat + UserScope`，落到临时目录——不是写完再记一笔，而是根本不写真实
注册表。`MBDSDR_QS_ISOLATE=0` 显式关闭（用于反证哨兵没被短路）。
`install_meta_hook()` 在 `PySide6.QtCore` 首次导入后立即打补丁，避免为了
隔离而在被测脚本 T0 之前预导入 PySide6（那会把导入耗时移出计时窗口）。
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import os
import sys
import tempfile
import threading
import traceback

import guard_common

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


def isolation_enabled():
    """默认开启；`MBDSDR_QS_ISOLATE=0` 显式关闭（反证用）。"""
    return os.environ.get("MBDSDR_QS_ISOLATE", "1") != "0"


def _isolation_dir():
    path = os.path.join(tempfile.gettempdir(),
                        "mbdsdr-qsettings-repro-%d" % os.getpid())
    os.makedirs(path, exist_ok=True)
    return path


def install_isolation():
    """把 QSettings 换成「两参字符串构造 → 临时 Ini」的子类。

    产品侧 7 处都是 `QSettings("MBDSDR", "Desktop")`（两参字符串）或
    `QSettings(self._ORG, self._APP)`。两参形式无视 `setDefaultFormat` /
    `setPath`，直连注册表；这里显式补成 (IniFormat, UserScope, org, app)。
    子类必须建在 `install()` 之后，才能继承已包裹的 setValue/remove/clear。
    """
    if not isolation_enabled():
        return False
    from PySide6.QtCore import QSettings as _Base
    if getattr(_Base, "_mbdsdr_qisolated", False):
        return False

    class IsolatedQSettings(_Base):
        def __init__(self, *args, **kwargs):
            if (len(args) == 2 and not kwargs
                    and all(isinstance(a, str) for a in args)):
                super().__init__(_Base.IniFormat, _Base.UserScope,
                                 args[0], args[1])
            else:
                super().__init__(*args, **kwargs)

    IsolatedQSettings.__name__ = "QSettings"
    IsolatedQSettings.__qualname__ = "QSettings"
    IsolatedQSettings.__module__ = _Base.__module__
    IsolatedQSettings._mbdsdr_qisolated = True

    # 默认落点：临时目录（被测脚本之后若自己 setPath，就以它的为准）。
    _Base.setDefaultFormat(_Base.IniFormat)
    for scope in (_Base.UserScope, _Base.SystemScope):
        _Base.setPath(_Base.IniFormat, scope, _isolation_dir())

    import PySide6.QtCore as _qtcore
    _qtcore.QSettings = IsolatedQSettings
    # 已经 `from PySide6.QtCore import QSettings` 过的模块，把全局名换掉。
    for mod in list(sys.modules.values()):
        try:
            if getattr(mod, "QSettings", None) is _Base:
                setattr(mod, "QSettings", IsolatedQSettings)
        except Exception:  # noqa: BLE001 - 只读模块/代理对象跳过
            continue
    return True


class _QtCoreHook(importlib.abc.MetaPathFinder):
    """在 `PySide6.QtCore` 首次导入完成后打补丁，然后自卸载。

    这样被测脚本自己的 `T0 = time.perf_counter()`（模块顶部）仍在所有
    Qt 导入之前，导入耗时留在计时窗口内，测量口径不变；打完补丁立刻从
    `sys.meta_path` 移除，之后任何 import 都不再经过本钩子（不常驻）。
    """

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "PySide6.QtCore":
            return None
        try:
            sys.meta_path.remove(self)
        except ValueError:
            pass
        spec = importlib.util.find_spec(fullname)
        if spec is None or spec.loader is None:
            # 没拿到 spec 就放回去，等下一次（正常不会发生）。
            sys.meta_path.insert(0, self)
            return None
        loader = spec.loader
        if getattr(loader, "_mbdsdr_qhook", False):
            return spec
        original = loader.exec_module

        def exec_module(module, _original=original):
            _original(module)
            try:
                install()
                install_isolation()
            except Exception:  # noqa: BLE001 - 受害的只是取证，不拖垮脚本
                pass

        loader.exec_module = exec_module
        loader._mbdsdr_qhook = True
        return spec


def install_meta_hook():
    """幂等：把 QtCore 钩子插到 meta_path 最前，不提前 import PySide6。

    钩子在 `PySide6.QtCore` 导入完成、补丁打好后自卸载，不常驻，之后的
    import 不再经过它。
    """
    if os.environ.get("MBDSDR_QS_GUARD", "1") == "0":
        return False
    if "PySide6.QtCore" in sys.modules:
        # Qt 已被导入（wrap 模式等），直接补打，省掉一次钩子往返。
        install()
        install_isolation()
        return False
    if any(isinstance(f, _QtCoreHook) for f in sys.meta_path):
        return False
    # 必须在 PathFinder 之前：追加到末尾会被 PathFinder 抢答，钩子永不触发。
    sys.meta_path.insert(0, _QtCoreHook())
    return True


def log_records(path=None):
    """数一数哨兵日志里有多少条真实 store 写入（跨进程取证用）。"""
    return guard_common.log_records(path or os.environ.get("MBDSDR_QS_GUARD_LOG"))


def install_exit_check():
    """本进程有真实写入 → 退出码 3（atexit 里 os._exit，能改退出码）。"""
    return guard_common.install_exit_check("_mbdsdr_qg_exit", records, report)


def install_log_exit_check(path=None):
    """父进程（driver）按共享日志判定：子进程写过真实 store 就非零退出。"""
    path = path or os.environ.get("MBDSDR_QS_GUARD_LOG")
    return guard_common.install_log_exit_check(
        "_mbdsdr_qg_log_exit", path, "真实 store 的写入", "[qsettings-guard]")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2 or argv[0] != "--wrap":
        print(__doc__)
        return 2
    return guard_common.run_wrapped(argv, install, report)


if __name__ == "__main__":
    raise SystemExit(main())
