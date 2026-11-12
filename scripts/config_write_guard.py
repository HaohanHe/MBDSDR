#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""产品配置文件写入哨兵：抓「谁在写用户目录下的真实配置」。

背景（2026-11-12）：本机真实用户态被探针写脏两次——先是
``~/.mbdsdr/gui_config.json`` 的编码被写坏，随后同一文件的 ``observer``
字段被改。``tests/conftest.py`` 的隔离只覆盖 pytest 进程；``scripts/repro``
的子进程照样直连真实用户目录。规则 13 要求「用户真实环境状态不得作为
实验对象」，本文件把这条规则变成机械拦截。

判据：**只对用户目录下的产品配置、且不在临时路径/沙箱内的写入记账**。
「用户目录」取 ``install()`` 那一刻的快照（``HOME`` / ``USERPROFILE`` /
``APPDATA`` / ``LOCALAPPDATA`` / ``XDG_CONFIG_HOME``）；之后这些变量被
改到别处（隔离 fixture、repro 沙箱）只会把新值登记为沙箱根，其下路径
一律不记——否则沙箱会被当成真实目录，哨兵误报（2026-11-12 实测）。
产品配置根：

  * ``~/.mbdsdr``（gui_config.json / config.json / bookmarks.json /
    approval_audit.jsonl）
  * ``%APPDATA%/mbdsdr``、``%LOCALAPPDATA%/mbdsdr``
  * ``$XDG_CONFIG_HOME/mbdsdr``（未设置时 ``~/.config/mbdsdr``）
  * ``MBDSDR_SETTINGS_DIR``（设置了就视为产品配置目录）

临时路径（``%TEMP%``、``MBDSDR_SCRATCH_ROOT``、repro 沙箱）内的写入不记；
仓库目录内的写入（证据 JSON、守卫日志）也不记。

覆盖面：``builtins.open`` / ``io.open``（写模式 w/a/x/+）、``os.replace`` /
``os.rename``。``MBDSDR_CFG_GUARD_BLOCK=1`` 时在写之前抛 ``PermissionError``，
**只拦不写**；否则放行但记账，进程退出码由 ``install_exit_check()`` 置 3。
日志默认落 ``MBDSDR_CFG_GUARD_LOG``（repro 启动段会指到 ``work/``）。

用法（pytest 外的公用入口）::

    python scripts/config_write_guard.py --wrap scripts/repro/xxx.py [args...]
"""
from __future__ import annotations

import atexit
import builtins
import io
import json
import os
import sys
import tempfile
import threading
import traceback

_RECORDS = []
_PRINTED = set()
_LOCK = threading.RLock()
_STATE = threading.local()
_INSTALLED = False
_MAX_PRINT = 20

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
_ROOT_NORM = os.path.normcase(os.path.abspath(ROOT)).rstrip("\\/")

#: 产品配置文件名。落在配置根之外的「同名文件」也算（例如 HOME 与
#: USERPROFILE 指向不同目录时，``expanduser`` 只认其中一个）。
_CONFIG_NAMES = frozenset({
    "gui_config.json", "config.json", "desktop_settings.json",
    "bookmarks.json", "approval_audit.jsonl",
})

#: 安装哨兵那一刻快照的「原始用户目录」。“真实”只认这个快照——运行时再改
#: ``HOME`` 等变量（隔离 fixture / repro 沙箱）只说明“现在写的是沙箱”，不能
#: 反过来把沙箱当成真实目录（2026-11-12 误报根因）。
_SNAPSHOT_VARS = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
                  "XDG_CONFIG_HOME")
_REAL_ENV = None            # dict: var -> 安装时的原始值
_REAL_ROOTS = None          # list: 由快照推导出的真实产品配置根


# ----------------------------------------------------------------------
# 路径判定
# ----------------------------------------------------------------------
def _norm(path):
    return os.path.normcase(os.path.abspath(os.path.expanduser(path)))


def _under(path, root):
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:          # Windows 跨盘符
        return False


def _temp_roots():
    roots = [tempfile.gettempdir()]
    for var in ("MBDSDR_SCRATCH_ROOT", "MBDSDR_REPRO_SANDBOX_DIR"):
        value = os.environ.get(var)
        if value:
            roots.append(value)
    if not os.environ.get("MBDSDR_SCRATCH_ROOT"):
        roots.append(r"D:\mbdsdr\output\tmp")
    out = []
    for root in roots:
        try:
            out.append(os.path.normcase(os.path.abspath(root)).rstrip("\\/"))
        except (OSError, ValueError):
            continue
    return out


def _normalize_roots(roots):
    out = []
    for root in roots:
        try:
            out.append(os.path.normcase(os.path.abspath(root)).rstrip("\\/"))
        except (OSError, ValueError):
            continue
    return out


def _config_roots_from(env):
    """按给定环境快照推导产品配置根（不再读运行时 ``os.environ``）。"""
    roots = []
    home = env.get("HOME") or env.get("USERPROFILE")
    if home and not home.startswith("~"):
        roots.append(os.path.join(home, ".mbdsdr"))
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = env.get(var)
        if base:
            roots.append(os.path.join(base, "mbdsdr"))
    xdg = env.get("XDG_CONFIG_HOME")
    if not xdg and home and not home.startswith("~"):
        xdg = os.path.join(home, ".config")
    if xdg:
        roots.append(os.path.join(xdg, "mbdsdr"))
    override = env.get("MBDSDR_SETTINGS_DIR")
    if override:
        roots.append(override)
    return _normalize_roots(roots)


def _user_env(env=None):
    env = os.environ if env is None else env
    out = {var: env[var] for var in _SNAPSHOT_VARS if env.get(var)}
    if env.get("MBDSDR_SETTINGS_DIR"):
        out["MBDSDR_SETTINGS_DIR"] = env["MBDSDR_SETTINGS_DIR"]
    return out


def snapshot_user_dirs(env=None):
    """快照「原始用户目录」，作为判定“真实配置”的唯一依据。

    必须在任何 HOME/APPDATA 重定向**之前**调用（``install()`` 会调）。
    子进程可由 ``MBDSDR_CFG_GUARD_REAL``（JSON）继承父进程的快照，避免
    自己启动时环境已被父进程改成沙箱、把沙箱当真实目录。
    """
    global _REAL_ENV, _REAL_ROOTS
    payload = os.environ.get("MBDSDR_CFG_GUARD_REAL")
    if payload:
        try:
            data = json.loads(payload)
        except ValueError:
            data = None
        if isinstance(data, dict):
            _REAL_ENV = _user_env(data)
            _REAL_ROOTS = _config_roots_from(dict(_REAL_ENV))
            return dict(_REAL_ENV)
    _REAL_ENV = _user_env(env)
    _REAL_ROOTS = _config_roots_from(dict(_REAL_ENV))
    return dict(_REAL_ENV)


def real_user_dirs():
    """当前生效的「真实用户目录」快照（没快照过就先快照）。"""
    if _REAL_ENV is None:
        snapshot_user_dirs()
    return dict(_REAL_ENV)


def _sandbox_user_dirs():
    """运行时被改指到别处的用户目录 = 沙箱根；其下路径一律不记账。"""
    real = real_user_dirs()
    out = []
    for var in _SNAPSHOT_VARS:
        value = os.environ.get(var)
        if value and value != real.get(var):
            out.append(value)
    override = os.environ.get("MBDSDR_SETTINGS_DIR")
    if override and override != real.get("MBDSDR_SETTINGS_DIR"):
        out.append(override)
    return _normalize_roots(out)


def _is_temp(path):
    p = _norm(path).rstrip("\\/")
    for root in _temp_roots() + _sandbox_user_dirs():
        if p == root or p.startswith(root + "\\") or p.startswith(root + "/"):
            return True
    return ("mbdsdr-repro-sandbox-" in p or "mbdsdr-qsettings-" in p
            or "mbdsdr-cfg-guard-" in p)


def _config_roots():
    """真实产品配置根——只来自安装时快照，不读运行时 HOME。"""
    if _REAL_ROOTS is None:
        snapshot_user_dirs()
    return list(_REAL_ROOTS)


def _under_real_user_dir(path):
    """路径是否落在快照中的原始用户目录下（同名文件兜底用）。"""
    p = _norm(path).rstrip("\\/")
    for value in real_user_dirs().values():
        root = os.path.normcase(os.path.abspath(value)).rstrip("\\/")
        if p == root or p.startswith(root + "\\") or p.startswith(root + "/"):
            return True
    return False


def _product_config(path):
    """该路径是否「用户目录下的产品配置且非临时/非沙箱/非仓库」。"""
    p = _norm(path).rstrip("\\/")
    if _is_temp(p):
        return False
    if p == _ROOT_NORM or p.startswith(_ROOT_NORM + "\\"):
        return False
    for root in _config_roots():
        if p == root or p.startswith(root + "\\") or p.startswith(root + "/"):
            return True
    # 同名兜底：真实用户目录下的任意同名文件也算（HOME/USERPROFILE 分歧等）。
    return (os.path.basename(p) in _CONFIG_NAMES
            and _under_real_user_dir(p))


# ----------------------------------------------------------------------
# 记账
# ----------------------------------------------------------------------
def _in_guard():
    return getattr(_STATE, "depth", 0) > 0


def _append_log(rec):
    """子进程 stdout/stderr 常被父进程 capture，落文件才拿得回来。"""
    path = os.environ.get("MBDSDR_CFG_GUARD_LOG")
    if not path:
        return
    _STATE.depth = getattr(_STATE, "depth", 0) + 1
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "pid": os.getpid(), "module": rec["module"],
                "func": rec["func"], "file": rec["file"], "line": rec["line"],
                "op": rec["op"], "path": rec["path"], "detail": rec["detail"],
                "stack": "".join(rec["stack"][-6:]),
            }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
    finally:
        _STATE.depth -= 1


def _caller():
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_globals.get("__name__") != __name__:
            return frame
        frame = frame.f_back
    return None


def _note(path, op, detail=""):
    """记账；返回 True 表示调用方应拦截（BLOCK=1）。"""
    if _in_guard() or not _product_config(path):
        return False
    frame = _caller()
    if frame is None:
        return False
    rec = {
        "module": frame.f_globals.get("__name__", "?"),
        "func": frame.f_code.co_name,
        "file": frame.f_code.co_filename,
        "line": frame.f_lineno,
        "op": op,
        "path": path,
        "detail": detail,
        "stack": traceback.format_stack(frame),
    }
    with _LOCK:
        _RECORDS.append(rec)
        _append_log(rec)
        tag = (rec["module"], rec["func"], rec["file"], rec["line"], op)
        if tag not in _PRINTED and len(_PRINTED) < _MAX_PRINT:
            _PRINTED.add(tag)
            print("[cfg-guard] caller=%s:%s file=%s:%s op=%s path=%s"
                  % (rec["module"], rec["func"], rec["file"], rec["line"],
                     op, path), file=sys.stderr, flush=True)
            if len(_PRINTED) <= 3:
                print("".join(rec["stack"]), file=sys.stderr, flush=True)
    return os.environ.get("MBDSDR_CFG_GUARD_BLOCK") == "1"


# ----------------------------------------------------------------------
# 打补丁
# ----------------------------------------------------------------------
def _is_write_mode(mode):
    return isinstance(mode, str) and any(ch in mode for ch in "wax+")


def _wrap_open(original):
    if getattr(original, "_mbdsdr_cfgguard", False):
        return original

    def wrapper(file, mode="r", *args, **kwargs):
        if "mode" in kwargs:
            mode = kwargs["mode"]
        if not _in_guard() and _is_write_mode(mode):
            try:
                path = os.fsdecode(os.fspath(file))
            except TypeError:       # 文件描述符等：不是路径，放过
                path = None
            if path and _note(path, "open", mode):
                raise PermissionError(
                    "[cfg-guard] 拦截对真实产品配置的写入：%s (mode=%s)"
                    % (path, mode))
        return original(file, mode, *args, **kwargs)

    wrapper.__name__ = getattr(original, "__name__", "open")
    wrapper.__doc__ = getattr(original, "__doc__", None)
    wrapper._mbdsdr_cfgguard = True
    return wrapper


def _wrap_move(original, op):
    if getattr(original, "_mbdsdr_cfgguard", False):
        return original

    def wrapper(src, dst, *args, **kwargs):
        if not _in_guard():
            paths, hit = [], False
            for path in (src, dst):
                try:
                    text = os.fsdecode(os.fspath(path))
                except TypeError:
                    continue
                paths.append(text)
                hit = hit or _product_config(text)
            if hit:
                label = " -> ".join(paths)
                if _note(label, op, ""):
                    raise PermissionError(
                        "[cfg-guard] 拦截对真实产品配置的替换：%s" % label)
        return original(src, dst, *args, **kwargs)

    wrapper.__name__ = getattr(original, "__name__", op)
    wrapper.__doc__ = getattr(original, "__doc__", None)
    wrapper._mbdsdr_cfgguard = True
    return wrapper


def install():
    """包裹 open/io.open/os.replace/os.rename；重复调用无副作用。

    安装这一刻的环境是「真实用户目录」的唯一来源：之后 HOME/APPDATA 被
    改到沙箱，只说明“现在写的是沙箱”，不再影响真实集合。
    """
    global _INSTALLED
    if _INSTALLED or os.environ.get("MBDSDR_CFG_GUARD", "1") == "0":
        return False
    snapshot_user_dirs()
    if getattr(builtins.open, "_mbdsdr_cfgguard", False):
        _INSTALLED = True
        return False
    builtins.open = _wrap_open(builtins.open)
    if io.open is not builtins.open:
        io.open = _wrap_open(io.open)
    os.replace = _wrap_move(os.replace, "replace")
    os.rename = _wrap_move(os.rename, "rename")
    _INSTALLED = True
    return True


def records():
    with _LOCK:
        return list(_RECORDS)


def reset():
    with _LOCK:
        _RECORDS.clear()
        _PRINTED.clear()


def report(prefix="[cfg-guard]"):
    recs = records()
    if not recs:
        print("%s 未捕获到对真实产品配置的写入" % prefix, flush=True)
        return 0
    print("%s 捕获 %d 次对真实产品配置的写入：" % (prefix, len(recs)), flush=True)
    seen = {}
    for rec in recs:
        key = (rec["module"], rec["func"], rec["file"], rec["line"], rec["op"],
               rec["path"])
        seen[key] = seen.get(key, 0) + 1
    for (module, func, file, line, op, path), count in seen.items():
        print("%s   %s:%s file=%s:%s op=%s path=%s ×%d"
              % (prefix, module, func, file, line, op, path, count), flush=True)
    first = recs[0]
    print("%s 首个写入的调用栈：" % prefix, flush=True)
    print("".join(first["stack"]), flush=True)
    return len(recs)


def log_records(path=None):
    """数一数哨兵日志里有多少条真实配置写入（跨进程取证用）。"""
    path = path or os.environ.get("MBDSDR_CFG_GUARD_LOG")
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except OSError:
        return 0


def install_exit_check():
    """本进程有真实写入 → 退出码 3（atexit 里 os._exit，能改退出码）。"""
    if getattr(sys, "_mbdsdr_cfg_exit", False):
        return False
    sys._mbdsdr_cfg_exit = True

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


def install_log_exit_check(path=None):
    """父进程（driver）按共享日志判定：子进程写过真实配置就非零退出。"""
    path = path or os.environ.get("MBDSDR_CFG_GUARD_LOG")
    if not path or getattr(sys, "_mbdsdr_cfg_log_exit", False):
        return False
    sys._mbdsdr_cfg_log_exit = True

    def _check():
        count = log_records(path)
        if not count:
            return
        print("[cfg-guard] %s 记录 %d 次对真实产品配置的写入，本脚本退出码置 3"
              % (path, count), flush=True)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(3)

    atexit.register(_check)
    return True


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
