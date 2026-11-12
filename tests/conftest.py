#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""tests/ 根 conftest：规避 PySide6 6.12 + CPython 3.12 退出期 segfault。

根因（phase64 实测 + gdb 栈）：tests/test_desktop_ui_responsiveness.py 等
在模块级创建 QApplication，pytest 退出进 Py_FinalizeEx 时 PySide6 的
atexit 清理（PySide::runCleanupFunctions -> destroyQCoreApplication ->
BindingManager::visitAllPyObjects）强拆残留 QObject，撞上已被删对象
segfault（exit 139/134；"QObject: shared QObject was deleted directly"）。
上游 c6217d9 干净树同样中招；裸脚本正常，pytest 模块级 app 模式触发。

规避：terminal summary 打印完毕、所有测试结论产出后，按 session 真实
退出码（exitstatus）主动硬退出，跳过 Py_FinalizeEx 的 PySide 强拆。

硬退出时机（2026-10-30 CI 实测修正）：必须放在 pytest_unconfigure 而非
pytest_sessionfinish——terminal reporter 的收尾汇总（含 FAILURES 详情与
"N passed" 行）由其 sessionfinish 钩子包裹器的 yield 之后打印，普通
sessionfinish 钩子被硬退出抢跑会把汇总与失败详情整批吞掉（CI 日志只有
进度点、无失败名，即此症）。pytest_unconfigure 在 wrap_session 完全收尾
（汇总已打印）之后、Py_FinalizeEx 之前执行，两头兼顾。

平台差异（2026-10-20 Windows 实测补充）：POSIX 的 os._exit 跳过 atexit 即可
规避；但 Windows 的 os._exit 仍走 DLL_PROCESS_DETACH——Qt DLL 的 detach
清理照删不误（崩溃栈顶即 os._exit 行）。故 Windows 改用 TerminateProcess
硬杀（不跑 DllMain detach、不进 atexit），两平台同语义。
这是规避不是修复——修复属 PySide6 上游（或桌面端产品退出路径重排）。
"""
import ast
import ctypes
import gc
import hashlib
import importlib.util
import inspect
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
from contextlib import contextmanager

import pytest

# 让会话级守卫能直接定位 desktop/settings.py（各测试文件各自加 path 前先垫一层）。
_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP_DIR = os.path.join(_ROOT_DIR, "desktop")
for _p in (_ROOT_DIR, _DESKTOP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 网络隔离（与 MBDSDR_SETTINGS_DIR 同族的测试环境隔离）：
# 默认禁网，让全量套件天然离线、绝不因联网挂死。orbit.fetch_tle 等
# 联网取数路径在离线时走磁盘缓存或诚实空态（无缓存即抛 TLEUnavailableError
# 或回退"无可见卫星"），绝不假造数据。调用方可预设 MBDSDR_OFFLINE=0 放行。
os.environ.setdefault("MBDSDR_OFFLINE", "1")

# session 真实退出码暂存：sessionfinish 记账，unconfigure 硬退出时取用。
# 直通 pytest 退出码（1=测试失败 2=中断 4=用法错 5=无收集）——早先只按
# testsfailed 映射 0/1，会把收集错误/中断静默映射成 0，CI 误判绿。
_RESULT = {"exitstatus": 0}

# ---------------------------------------------------------------------------
# 防线 1：Qt GUI 测试跨进程互斥（2026-11-05 崩溃取证后加）
#
# 现场：Application 日志里 51 个 python.exe 崩溃进程，同一 5 分钟窗口内常有
# 2–4 个；WER 2004「资源耗尽」记录指向 python.exe 单进程提交量 17–28 GB，
# 全机 commit 上限仅 38 GB。多会话并发跑 Qt 用例会互相把内存预算挤穿。
#
# 因此：同一时刻只允许一个载入 PySide6 的 pytest 进程执行用例。锁用操作
# 系统字节锁（进程被崩/被杀时内核自动释放），不做 O_EXCL 死文件，天然没有
# 「过期判定」问题。可用 MBDSDR_TEST_LOCK=0 显式关闭（明确知道自己在做并发
# 压测时），或用 MBDSDR_TEST_LOCK_TIMEOUT 调等待秒数。
#
# 锁路径必须**全仓唯一**（2026-11-13 体检修正）：链接工作树的 __file__ 在它
# 自己的目录里，旧实现按「本文件上两级/.locks」解析，于是 worktree 得到
# .tmp/.locks/qt_test.lock、主工作区得到 .locks/qt_test.lock——同名不同文件，
# 两棵树各持一把锁同时跑（当晚全量体检就是这样，同时写同一份用户真实配置
# 才有两条证据），互斥等于不存在。现在统一按 git common dir（所有工作树共享
# 的那个 .git）反推锁目录，任何工作树都落到同一个文件。
#
# 门控（2026-11-13 再修正）：判据只看「这个用例会不会构造 QWidget /
# QApplication」，不看「有没有 import PySide6」。旧判据「收集阶段 sys.modules
# 里新增 PySide6」恒为真——conftest 自己拍 app 样式基线时就 import 了
# QtWidgets——结果纯逻辑用例也被拖进队列。明细见下方「锁判据」注释。
# ---------------------------------------------------------------------------
_LOCKS_DIR = {}


def _git_common_root():
    """所有工作树共享的仓库根；解析不出返回 None（非 git 目录 / 无 git）。"""
    for path_format in (["--path-format=absolute"], []):
        try:
            proc = subprocess.run(
                ["git", "rev-parse"] + path_format + ["--git-common-dir"],
                cwd=_ROOT_DIR, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            continue
        out = proc.stdout.decode("utf-8", "replace").strip()
        if not out:
            continue
        common = os.path.abspath(os.path.join(_ROOT_DIR, out))
        if os.path.basename(common) == ".git":
            return os.path.dirname(common)
        return None          # bare / GIT_DIR 另置：不猜，交给调用处告警
    return None


def _shared_locks_dir():
    """全仓共享的锁目录：所有工作树必须解析到同一处。

    优先级：MBDSDR_LOCKS_DIR（测试/CI 显式指定）→ git common dir 反推 →
    本工作树上一级的 ``.locks``，并在 stderr 明确告警「只在本工作树生效」。
    目录沿用既有约定（仓库根的同级 ``.locks``，与 file_claim 台账同处），
    只是「仓库根」必须取 common dir，不能取 ``__file__`` 所在工作树。
    """
    if "dir" in _LOCKS_DIR:
        return _LOCKS_DIR["dir"]
    env = os.environ.get("MBDSDR_LOCKS_DIR")
    if env:
        _LOCKS_DIR["dir"] = os.path.abspath(env)
        return _LOCKS_DIR["dir"]
    root = _git_common_root()
    if root:
        _LOCKS_DIR["dir"] = os.path.join(os.path.dirname(root), ".locks")
        return _LOCKS_DIR["dir"]
    fallback = os.path.join(os.path.dirname(_ROOT_DIR), ".locks")
    _LOCKS_DIR["dir"] = fallback
    sys.stderr.write(
        "[mbdsdr-lock] 无法从 git common dir 解析共享锁目录（不是 git 仓库？），"
        "回退到 %s：该锁只在本工作树内生效，与其他工作树不互斥。\n" % fallback
    )
    return fallback


def _qt_lock_path():
    override = os.environ.get("MBDSDR_TEST_LOCK_PATH")
    if override:
        return override
    return os.path.join(_shared_locks_dir(), "qt_test.lock")


def _qt_owner_path(path):
    return path + ".owner"


#: 持锁期间刷新 owner 心跳的间隔（秒）。心跳只用于展示与告警，
#: 永不作为接管依据——这个锁的释放由内核负责（进程退出/被杀即自动释放）。
_HEARTBEAT_S = 10.0


def _fmt_elapsed(seconds):
    """秒数 → 「3 分 12 秒」这样的人话（只用于提示，不参与任何判定）。"""
    try:
        s = int(max(0.0, float(seconds)))
    except (TypeError, ValueError):
        return "未知"
    if s < 60:
        return "%d 秒" % s
    m, s = divmod(s, 60)
    if m < 60:
        return "%d 分 %d 秒" % (m, s)
    h, m = divmod(m, 60)
    return "%d 小时 %d 分" % (h, m)


def _process_start_time(pid):
    """进程启动时刻（epoch 秒）；取不到返回 None。

    只用于把「已跑多久」和「持锁多久」分开说清楚——旧实现把拿锁时刻
    标成「启动」，是本文件自己犯的「用错字段回答时长」的错。
    """
    try:
        if sys.platform == "win32":
            from ctypes import wintypes

            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = k32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            try:
                created = wintypes.FILETIME()
                exited = wintypes.FILETIME()
                kernel = wintypes.FILETIME()
                user = wintypes.FILETIME()
                if not k32.GetProcessTimes(handle, ctypes.byref(created),
                                           ctypes.byref(exited),
                                           ctypes.byref(kernel),
                                           ctypes.byref(user)):
                    return None
                ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
                return ticks / 1e7 - 11644473600.0   # FILETIME 纪元 → Unix 纪元
            finally:
                k32.CloseHandle(handle)
        with open("/proc/%d/stat" % int(pid), "rb") as fh:
            fields = fh.read().rsplit(b")", 1)[-1].split()
        start_ticks = int(fields[19])          # 第 22 个字段（comm 之后）
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/stat", "rb") as fh:
            btime = next(int(line.split()[1]) for line in fh
                         if line.startswith(b"btime"))
        return btime + start_ticks / float(hz)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def _argv_targets_tests(argv):
    """命令行是否指定了具体测试路径（没指定 = 一次全量跑）。只用于措辞。"""
    try:
        tokens = shlex.split(argv)
    except ValueError:
        tokens = argv.split()
    for tok in tokens[1:]:                     # 首个 token 是 pytest 自身脚本
        if tok.startswith("-"):
            continue
        if (tok.endswith(".py") or "/" in tok or "\\" in tok
                or tok == "tests" or tok.startswith("tests/")
                or tok.startswith("test_")):
            return True
    return False


def _write_qt_owner(path):
    """记下持有者身份与心跳，供后来者判断「在等谁、等了多久」。

    字节锁才是权威（进程死/被杀由内核释放）；这份 owner 只是诊断信息，
    写失败、残留、过期都不影响互斥正确性。
    """
    now = time.time()
    info = {
        "pid": os.getpid(),
        "pid_start_time": _LOCK.get("pid_start_time"),
        "acquired_at": _LOCK.get("acquired_at") or now,
        "heartbeat_at": now,
        "argv": " ".join(sys.argv)[:300],
    }
    tmp = "%s.%d.tmp" % (_qt_owner_path(path), os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(info, fh, ensure_ascii=False)
        os.replace(tmp, _qt_owner_path(path))   # 读者不会看到半个 JSON
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _start_qt_heartbeat(path):
    """持锁期间定期刷新 heartbeat_at；返回 (stop_event, thread)。

    红线：**心跳只证明进程没退出，不证明用例在推进**。它是独立线程，
    主线程卡在某个用例里时它照跳。三个月后别拿它当「还活着」的证据--
    判「长跑」还是「卡死」要看 CPU / 临时目录 / 进度输出；锁是否释放
    只看内核（进程退出即放）。心跳只用于展示和告警，永不作为接管依据。
    """
    stop = threading.Event()

    def _beat():
        while not stop.wait(_HEARTBEAT_S):
            _write_qt_owner(path)

    th = threading.Thread(target=_beat, name="mbdsdr-qt-lock-heartbeat",
                          daemon=True)
    th.start()
    return stop, th


def _read_qt_owner(path):
    """持有者诊断：谁、已持锁多久、心跳多久前、在跑什么。

    读不到就诚实说不知道（旧进程没写 / 已释放）。心跳缺失或停跳都
    不等于锁坏了——锁的释放只看内核，pid 与心跳只是诊断信息。
    """
    try:
        with open(_qt_owner_path(path), encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, ValueError, TypeError):
        return "持有者未知（无 owner 文件；锁仍可能被占用，只是不知道该问谁）"
    now = time.time()
    parts = ["持有者 PID %s" % info.get("pid", "?")]
    acquired = info.get("acquired_at", info.get("at"))  # 旧版只有 at（拿锁时刻）
    try:
        parts.append("已持锁 %s" % _fmt_elapsed(now - float(acquired)))
    except (TypeError, ValueError):
        parts.append("持锁时长未知")
    pid_start = info.get("pid_start_time")
    if pid_start is not None:
        try:
            parts.append("进程启动于 %s 前"
                         % _fmt_elapsed(now - float(pid_start)))
        except (TypeError, ValueError):
            pass
    hb = info.get("heartbeat_at")
    if hb is None:
        parts.append("无心跳记录（旧版 owner，无法区分「在跑长用例」与「卡住」）")
    else:
        try:
            age = now - float(hb)
        except (TypeError, ValueError):
            age = None
        if age is None:
            parts.append("心跳时间不可读")
        elif age > 5 * _HEARTBEAT_S:
            parts.append("心跳已 %s 未刷新（可能卡住；但锁是否释放只看内核）"
                         % _fmt_elapsed(age))
        else:
            parts.append("心跳 %s 前（只证明进程没退出，不代表用例在推进）"
                         % _fmt_elapsed(age))
    argv = str(info.get("argv") or "")
    if argv:
        parts.append("命令行：%s" % argv)
        if not _argv_targets_tests(argv):
            parts.append("命令行没指定测试路径 → 这是一次全量长跑，"
                         "不是你本地环境的问题")
    return "；".join(parts)


def _acquire_qt_lock(path, timeout):
    """返回 (fileobj, waited_seconds)；超时返回 (None, waited_seconds)。

    等锁时只播报一次「在等谁」，不刷屏；owner 文件缺失也照实说。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+b")
    if os.fstat(fh.fileno()).st_size == 0:
        fh.write(b"\0")
        fh.flush()
    started = time.monotonic()
    announced = False
    while True:
        try:
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _LOCK["acquired_at"] = time.time()
            _LOCK["pid_start_time"] = _process_start_time(os.getpid())
            _write_qt_owner(path)
            _LOCK["hb_stop"], _LOCK["hb_thread"] = _start_qt_heartbeat(path)
            return fh, time.monotonic() - started
        except OSError:
            if time.monotonic() - started >= timeout:
                fh.close()
                return None, time.monotonic() - started
            if not announced:
                announced = True
                sys.stderr.write(
                    "[mbdsdr-lock] 等锁中：%s；最多等 %.0fs（%s）\n"
                    "（Qt 用例串行执行：同一时刻只允许一个进程，避免内存挤穿；"
                    "排队不是你的环境问题。）\n"
                    % (_read_qt_owner(path), timeout, path)
                )
                sys.stderr.flush()
            time.sleep(0.5)


def _release_qt_lock(fh, path=None):
    stop, th = _LOCK.get("hb_stop"), _LOCK.get("hb_thread")
    if stop is not None:
        stop.set()
    if th is not None:
        th.join(timeout=2.0)
    _LOCK["hb_stop"] = None
    _LOCK["hb_thread"] = None
    if fh is None:
        return
    try:
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        fh.close()
    if path:
        try:
            os.unlink(_qt_owner_path(path))
        except OSError:
            pass


_LOCK = {"fh": None, "waited": 0.0, "path": None,
         "acquired_at": None, "pid_start_time": None,
         "hb_stop": None, "hb_thread": None}

# ---------------------------------------------------------------------------
# 锁判据（2026-11-13 修正）：只看「会不会构造 GUI」，不看「有没有 import Qt」
#
# 旧判据之一是「收集阶段 sys.modules 里新增了 PySide6」。但 conftest 自己在
# pytest_collection_finish 里为了拍 app 样式基线就 `from PySide6.QtWidgets
# import QApplication`，于是这条判据恒为真——纯逻辑用例也被拖去排队等锁。
# 现在只认构造证据：
#   ① 模块代码（含懒 import）里出现 QApplication/QWidget/QMainWindow/QPixmap；
#   ② 模块里引用的类实际是 QWidget 子类（MainWindow/DevicePanel 这类项目类）；
#   ③ 用例请求 qtbot fixture；
#   ④ 懒 import 的仓库内模块（如 main_window）源码里带 ① 的证据。
# 只 import PySide6.QtCore/QtGui 枚举、不碰窗口的用例不再进同步区。
# ---------------------------------------------------------------------------
_QT_GUI_NAMES = {"QApplication", "QWidget", "QMainWindow", "QPixmap"}


def _code_names(code):
    """代码对象（含嵌套）里出现过的全部名字。"""
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _code_names(const)
    return names


def _code_constructs_gui(code):
    """代码对象里是否直接出现 GUI 构造名（懒 import 的类名也落在 co_names）。"""
    return bool(_code_names(code) & _QT_GUI_NAMES)


_MODULE_GUI_CACHE = {}
_CLASS_GUI_CACHE = {}


def _module_defines_gui_class(mod_name, cls_name):
    """仓库内模块的源码里，cls_name 是否是基类含 GUI 名的类（只解析 AST，不执行）。

    覆盖 `from main_window import MainWindow` 这类函数体内懒 import：收集阶段
    模块命名空间里还没有 MainWindow，但源码 AST 能确认它是 QMainWindow 子类。
    """
    key = (mod_name, cls_name)
    if key in _CLASS_GUI_CACHE:
        return _CLASS_GUI_CACHE[key]
    _CLASS_GUI_CACHE[key] = False
    try:
        spec = importlib.util.find_spec(mod_name)
    except (ImportError, AttributeError, ValueError):
        return False
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin or not os.path.abspath(origin).startswith(_ROOT_DIR + os.sep):
        return False
    try:
        with open(origin, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), origin)
    except (OSError, SyntaxError, ValueError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != cls_name:
            continue
        for base in node.bases:
            bname = ""
            if isinstance(base, ast.Name):
                bname = base.id
            elif isinstance(base, ast.Attribute):
                bname = base.attr
            if bname in _QT_GUI_NAMES:
                _CLASS_GUI_CACHE[key] = True
                return True
    return False


def _name_pulls_qt_gui(name, depth=0, seen=None):
    """按模块名判断：import 它会不会构造 GUI（只认仓库内单段模块名）。

    只编译不执行（`spec.loader.get_code`），避免为了判定真的 import 面板——
    那会把内存开销提前到门控之前，正是锁要防的东西。
    """
    if name in _MODULE_GUI_CACHE:
        return _MODULE_GUI_CACHE[name]
    if depth > 2:
        return False
    if seen is None:
        seen = set()
    if name in seen:
        return False
    seen.add(name)
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        _MODULE_GUI_CACHE[name] = False
        return False
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin or not os.path.abspath(origin).startswith(_ROOT_DIR + os.sep):
        _MODULE_GUI_CACHE[name] = False
        return False
    try:
        code = spec.loader.get_code(name)          # 只编译，不执行
    except (ImportError, SyntaxError, OSError, AttributeError):
        _MODULE_GUI_CACHE[name] = False
        return False
    if code is None:
        _MODULE_GUI_CACHE[name] = False
        return False
    if _code_constructs_gui(code):
        _MODULE_GUI_CACHE[name] = True
        return True
    for sub in code.co_names:
        if "." in sub or not sub.islower() or not sub.isidentifier():
            continue
        if _name_pulls_qt_gui(sub, depth + 1, seen):
            _MODULE_GUI_CACHE[name] = True
            return True
    _MODULE_GUI_CACHE[name] = False
    return False


def _class_builds_gui(obj):
    """这个对象是不是会建窗口的类：QtWidgets 的类，或项目内的 QWidget 子类。"""
    if not isinstance(obj, type):
        return False
    if getattr(obj, "__module__", "").startswith("PySide6.QtWidgets"):
        return True
    try:
        from PySide6.QtWidgets import QWidget
    except Exception:  # noqa: BLE001 - 裁剪环境没装 PySide6
        return False
    try:
        return issubclass(obj, QWidget)
    except TypeError:
        return False


def _code_of(obj):
    """取对象对应的已编译代码；解开 pytest fixture / functools.wraps 包装。

    `@pytest.fixture def qapp(): from PySide6... import QApplication` 在模块
    globals 里是 FixtureFunctionDefinition（没有 __code__），真函数藏在
    __wrapped__ 上——不解开就会漏判（实测 test_status_panel_squelch_units 的
    懒 import 只存在于 fixture 里）。
    """
    try:
        obj = inspect.unwrap(obj)
    except (ValueError, TypeError):
        return None
    code = getattr(obj, "__code__", None)
    return code if isinstance(code, types.CodeType) else None


def _module_code(mod):
    """模块自身的已编译代码；取不到返回 None。

    优先问 loader（已缓存 pyc）；pytest 的断言重写 loader 不一定支持
    get_code，此时退回把源码编译一遍——判据看的是 co_names，不是文本子串。
    """
    spec = getattr(mod, "__spec__", None)
    loader = getattr(spec, "loader", None)
    get = getattr(loader, "get_code", None)
    name = getattr(mod, "__name__", "")
    if get is not None and name:
        try:
            return get(name)
        except (ImportError, SyntaxError, OSError, AttributeError):
            pass
    try:
        return compile(inspect.getsource(mod),
                       getattr(mod, "__file__", "<module>"), "exec")
    except (OSError, TypeError, SyntaxError, ValueError):
        return None


def _module_gui_evidence(mod):
    """模块是否会构造 GUI：返回证据字符串，不会构造返回 None。"""
    modname = getattr(mod, "__name__", "?")
    code = _module_code(mod)
    names = _code_names(code) if code is not None else set()
    if code is None:                      # 拿不到模块代码时退化为扫描函数/方法
        for obj in vars(mod).values():
            sub = _code_of(obj)
            if sub is not None:
                names |= _code_names(sub)
            elif isinstance(obj, type) and getattr(obj, "__module__", None) == modname:
                for member in vars(obj).values():
                    sub = _code_of(member)
                    if sub is not None:
                        names |= _code_names(sub)
    hit = names & _QT_GUI_NAMES
    if hit:
        return "出现 %s（%s）" % (sorted(hit)[0], modname)
    for name in names:
        try:
            obj = getattr(mod, name)
        except Exception:  # noqa: BLE001 - 模块自定义 __getattr__ 的边角
            continue
        if _class_builds_gui(obj):
            return "引用 GUI 类 %s（%s）" % (name, modname)
    # 懒 import 的项目 UI 类：只认「import 的模块 + 被引用的类名都对得上，
    # 且那个类在源码里确实继承 GUI 基类」。裸 import 一个 UI 模块去读源码
    # （如 test_desktop_constants 扫描 dock_layout_manager.py）不触发。
    lazy_mods = [n for n in names
                 if "." not in n and n.islower() and n.isidentifier()
                 and _name_pulls_qt_gui(n)]
    if lazy_mods:
        for name in names:
            if not name[:1].isupper():
                continue
            for lazy_mod in lazy_mods:
                if _module_defines_gui_class(lazy_mod, name):
                    return "懒 import %s.%s（%s）" % (lazy_mod, name, modname)
    return None


def _qt_trigger(items):
    """本会话会不会构造 QWidget/QApplication；返回触发原因，不构造返回 None。

    判据见上方「锁判据」：只看构造证据（含项目内 QWidget 子类、qtbot、
    懒 import 的项目 UI 模块），不看「有没有 import PySide6」——后者既误伤
    纯逻辑用例，也会被 conftest 自己的基线采集污染成恒真。
    """
    seen = set()
    for item in items:
        if "qtbot" in getattr(item, "fixturenames", ()):
            return "请求 qtbot（%s）" % (getattr(item, "nodeid", "?"),)
        mod = getattr(item, "module", None)
        if mod is None or id(mod) in seen:
            continue
        seen.add(id(mod))
        why = _module_gui_evidence(mod)
        if why:
            return why
    return None


def pytest_collection_finish(session):
    """收集定稿、用例尚未执行时拿锁：保证「同一时刻只有一个 Qt 测试进程在跑」。"""
    # 先算锁判据、再拍基线：基线采集自己会 `import PySide6.QtWidgets`，先采集
    # 就会把「进程里有没有 Qt」污染成恒真（旧判据就是这么误伤纯逻辑用例的）。
    # 判据只看用例会不会构造 GUI，与采集顺序无关，但这个顺序别改回去。
    trigger = _qt_trigger(session.items)
    # 此刻所有测试模块已 import、任何 module fixture 都还没跑：第一次见到
    # QApplication 就在这里拍 app 级样式基线（锁的几处早退不能吞掉它）。
    _capture_app_style_baseline()
    # 同理：module 级 fixture 会先于 function 级构造 MainWindow，QSettings
    # 的隔离必须在这之前装好。
    _install_qsettings_isolation()
    # 隔离只对 pytest 有效；外面还有独立进程直连真实存储。装上写入哨兵，
    # 任何漏到非临时 store 的写都会留痕并在 sessionfinish 直接把会话判红。
    _install_qsettings_write_guard()
    if getattr(session.config.option, "collectonly", False):
        # 收集阶段不执行用例、不建 QApplication，此时抢锁只会把 --collect-only
        # 变成「等另一个会话跑完」。锁防的是并发执行用例挤穿内存，不是并发读
        # 文件。必须放在 QSettings 隔离与写守卫安装之后：收集阶段仍要隔离，
        # 只是不抢执行锁。
        return
    if os.environ.get("MBDSDR_TEST_LOCK", "1") == "0":
        return
    if trigger is None:
        return
    timeout = float(os.environ.get("MBDSDR_TEST_LOCK_TIMEOUT", "300"))
    path = _qt_lock_path()
    try:
        fh, waited = _acquire_qt_lock(path, timeout)
    except OSError as exc:
        # CI/容器里父目录可能不可写：降级为「不互斥」而不是把会话打死。
        sys.stderr.write(
            "[mbdsdr-lock] 锁不可用（%s），本次不做互斥：%s\n" % (exc, path)
        )
        return
    if fh is None:
        pytest.exit(
            "[mbdsdr-lock] 等待 %.0fs 仍未拿到 Qt 测试互斥锁：%s\n"
            "%s\n"
            "（Qt 用例必须串行：本机 commit 上限 38GB，并发会把内存预算挤穿。"
            "持有者进程退出/被杀时内核会自动放锁，所以等不到只说明它还在跑。"
            "稍后重试，或设 MBDSDR_TEST_LOCK_TIMEOUT 加长等待。）"
            % (waited, path, _read_qt_owner(path)),
            returncode=2,
        )
    _LOCK["fh"] = fh
    _LOCK["waited"] = waited
    _LOCK["path"] = path
    sys.stderr.write(
        "[mbdsdr-lock] 已获得 Qt 测试互斥锁（等待 %.1fs，触发：%s）：%s\n"
        % (waited, trigger, path)
    )


# ---------------------------------------------------------------------------
# 防线 2：单进程提交内存看门狗
#
# 现场 WER 2004 显示 python.exe 单进程提交量涨到 17–28 GB（本机 commit 上限
# 38 GB），随后同窗口内多个 python.exe 以 0xC0000005/0xC0000409 崩溃。看门狗
# 在用例边界量一次 PrivateUsage，越限立刻带用例名中止（pytest.exit），
# 把「系统级提交耗尽 + 各进程随机 native 崩溃」降级成「一条明确的用例失败」。
# 默认 4096 MB，可用 MBDSDR_TEST_COMMIT_LIMIT_MB 调整（0 = 关闭）。
# ---------------------------------------------------------------------------
class _PMC_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def _commit_mb():
    if sys.platform == "win32":
        pmc = _PMC_EX()
        pmc.cb = ctypes.sizeof(_PMC_EX)
        fn = ctypes.windll.psapi.GetProcessMemoryInfo
        fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PMC_EX), ctypes.c_ulong]
        if not fn(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return 0
        return int(pmc.PrivateUsage) >> 20
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def pytest_runtest_setup(item):
    # 每条用例开始前换一份全新的桌面配置（在 module 级 fixture 构造窗口之前）。
    _advance_backing_config()
    # 真模态开关按用例的 marker 翻转：默认禁止，只有显式 allow_modal 才放行。
    _MODAL_GUARD["allow"] = item.get_closest_marker("allow_modal") is not None
    limit = int(os.environ.get("MBDSDR_TEST_COMMIT_LIMIT_MB", "4096"))
    if limit <= 0:
        return
    used = _commit_mb()
    if used > limit:
        pytest.exit(
            "[mbdsdr-guard] 本进程提交内存 %d MB > 上限 %d MB，越限用例：%s\n"
            "（继续跑会把全机 commit 打穿，导致同窗口其它 python.exe 随机 native "
            "崩溃；先定位该用例的内存增长。）"
            % (used, limit, item.nodeid),
            returncode=3,
        )



# ---------------------------------------------------------------------------
# 防线 3：测试里禁止真模态（2026-11-05 两次整锁挂死后的设施规矩）
#
# 现场：QMessageBox.exec() 在 offscreen 下永不返回——第一次挂死 50 分钟
# （py-spy 栈 _start_recording -> _show_error_dialog -> box.exec()）；第二次
# 两条录音用例只打桩了 QMessageBox.warning，真实路径根本不走 warning，整条
# Qt 互斥锁又被占死。
#
# 因此默认全局拦下所有真模态：QDialog/QMessageBox/QFileDialog/QInputDialog
# 的 exec() 与 QMessageBox 的静态便捷方法、QFileDialog 的静态取路径方法，
# 被调到就记一条「测试里出现真模态：<类名>」并立即返回「取消」/空路径。
# 需要真弹窗的用例显式标 @pytest.mark.allow_modal（默认禁止）。
# ---------------------------------------------------------------------------
_MODAL_GUARD = {"allow": False, "hits": [], "reported": set()}


def _record_modal(owner: str, method: str) -> None:
    key = f"{owner}.{method}"
    _MODAL_GUARD["hits"].append(key)
    if key not in _MODAL_GUARD["reported"]:
        _MODAL_GUARD["reported"].add(key)
        sys.stderr.write(
            f"[mbdsdr-modal] 测试里出现真模态：{owner}（{method} 已拦下，"
            f"立即返回取消）\n")


def _install_modal_guard():
    """返回 (类, 方法名, 原实现) 列表，供 session 结束时还原。"""
    from PySide6.QtWidgets import (
        QDialog, QFileDialog, QInputDialog, QMessageBox,
    )

    classes = {
        "QDialog": QDialog, "QFileDialog": QFileDialog,
        "QInputDialog": QInputDialog, "QMessageBox": QMessageBox,
    }
    originals = []

    def _wrap(owner, method, fallback):
        cls = classes[owner]
        orig = getattr(cls, method)

        def _stub(*args, **kwargs):
            if _MODAL_GUARD["allow"]:
                return orig(*args, **kwargs)
            _record_modal(owner, method)
            return fallback() if callable(fallback) else fallback

        setattr(cls, method, _stub)
        originals.append((cls, method, orig))

    # exec()：模态事件循环本体（挂死现场就是这里）
    _wrap("QMessageBox", "exec", lambda: QMessageBox.No)
    _wrap("QDialog", "exec", lambda: QDialog.Rejected)
    _wrap("QFileDialog", "exec", lambda: QDialog.Rejected)
    _wrap("QInputDialog", "exec", lambda: QDialog.Rejected)
    # 静态便捷方法内部同样开 exec()
    for name in ("warning", "information", "critical", "question",
                 "about", "aboutQt"):
        _wrap("QMessageBox", name, lambda: QMessageBox.No)
    _wrap("QFileDialog", "getOpenFileName", lambda: ("", ""))
    _wrap("QFileDialog", "getOpenFileNames", lambda: ([], ""))
    _wrap("QFileDialog", "getSaveFileName", lambda: ("", ""))
    _wrap("QFileDialog", "getExistingDirectory", lambda: "")
    _wrap("QInputDialog", "getText", lambda: ("", False))
    _wrap("QInputDialog", "getInt", lambda: (0, False))
    _wrap("QInputDialog", "getDouble", lambda: (0.0, False))
    _wrap("QInputDialog", "getItem", lambda: ("", False))
    return originals


@pytest.fixture(scope="session", autouse=True)
def _modal_guard_session():
    originals = _install_modal_guard()
    yield
    for cls, method, orig in originals:
        setattr(cls, method, orig)


@pytest.fixture(scope="session")
def modal_guard(_modal_guard_session):
    """暴露拦下的真模态记录：{"hits": [...], "allow": bool}。"""
    return _MODAL_GUARD


def pytest_runtest_teardown(item, nextitem):
    _MODAL_GUARD["allow"] = False


# ---------------------------------------------------------------------------
# 防线 4：测试会话禁止写用户真实配置（2026-11-05 打包线实测）
#
# 现场：tests/test_record_dir_consumption.py 的 window fixture 直接
# MainWindow()，走真实 %APPDATA%/mbdsdr/desktop_settings.json；用例里
# settings.set("record_dir", ...) 被防抖定时器写回真实文件，用户下次启动
# 看到「录音目录不可写」假报警（实测残留 ...\pytest-1486\...\rec、Z:\nope）。
#
# 因此：pytest_configure 在收集前取快照，会话结束比对 sha256 + mtime_ns +
# 目录清单，任何变化都失败并打印哪个文件、差多少字节。测试侧必须把
# HOME/USERPROFILE/APPDATA/LOCALAPPDATA 重定向到 tmp_path，见
# docs/COORDINATION.md「测试纪律」。
# ---------------------------------------------------------------------------
_REAL_CONFIG_GUARD = {"path": None, "dir": None, "before": None, "bm_path": None, "bm_before": None,
                      "gui_path": None, "gui_before": None,
                      "before_listing": None, "error": None}


def _file_state(path):
    try:
        st = os.stat(path)
    except OSError:
        return {"exists": False, "size": 0, "mtime_ns": 0, "sha256": None}
    digest = None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        digest = h.hexdigest()
    except OSError:
        pass
    return {"exists": True, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "sha256": digest}


def _dir_names(path):
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _gui_observer(path):
    """只取 gui_config 里的观测站三项，窗口几何变化不算污染。

    窗口几何被测试改动无伤大雅（下个用例/用户自己会重排）；真正危险的是
    站址被写成测试值——用户下次启动会看到一片不属于自己的天，且界面不
    会有任何"这是假位置"的标注。守卫只钉这一点，才不会被几何噪声淹没。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return "unreadable"
    if not isinstance(data, dict):
        return "unreadable"
    return (data.get("observer_lat"), data.get("observer_lon"),
            data.get("observer_place"))


def _snapshot_real_config():
    try:
        from settings import default_config_path
        path = default_config_path()
    except Exception as exc:  # noqa: BLE001
        _REAL_CONFIG_GUARD["error"] = f"{type(exc).__name__}: {exc}"
        sys.stderr.write(
            "[mbdsdr-guard] 无法定位真实配置路径，会话级配置守卫未启用："
            f"{exc}\n")
        return
    _REAL_CONFIG_GUARD["path"] = path
    _REAL_CONFIG_GUARD["dir"] = os.path.dirname(path)
    _REAL_CONFIG_GUARD["before"] = _file_state(path)
    _REAL_CONFIG_GUARD["before_listing"] = _dir_names(os.path.dirname(path))
    # 书签库（~/.mbdsdr/bookmarks.json）同样属于用户真实配置：
    # 2026-11-10 实测被测试写进 159 条垃圾（0 Hz、/etc/passwd、1MB AAAAA、
    # Temp 探针路径），桌面端启动时把 0 Hz 当「上次频率」回填，连报三条
    # 硬件 WARNING，收藏面板也随之变成乱码。只盯 desktop_settings.json 不够。
    bm = os.path.join(os.path.expanduser("~"), ".mbdsdr", "bookmarks.json")
    _REAL_CONFIG_GUARD["bm_path"] = bm
    _REAL_CONFIG_GUARD["bm_before"] = _file_state(bm)
    # gui_config.json 存着用户的观测站坐标与窗口几何。2026-11-11 实测：某次
    # 用例/探针把 observer_lat/lon 写成 0,0，用户下次启动就被当成"站在
    # 几内亚湾"——真实设备不在位时，这是最难自查的假数据。只钉观测站三项
    # （见 _gui_observer），窗口几何重排不误报。
    gui = os.path.join(os.path.expanduser("~"), ".mbdsdr", "gui_config.json")
    _REAL_CONFIG_GUARD["gui_path"] = gui
    _REAL_CONFIG_GUARD["gui_observer_before"] = _gui_observer(gui)


#: 会话开始时备份真实配置**内容**、以及写保护协调数据的目录。
#:
#: 必须挂在**全仓共享**的锁目录（``_shared_locks_dir()``）下，不能按工作树分：
#: 2026-11-13 体检实证，旧写法 ``本文件上两级/.locks`` 让 worktree 落到
#: ``.tmp/.locks``、主工作区落到 ``.locks``，于是两棵树的写保护标记互相看不见
#: ——先退出的会话以为「没有别的会话」，当场把真实配置恢复成可写，另一棵树
#: 剩下的整个会话都在裸奔（1414→1207 字节的时间窗就是这么来的）。
#: 现在标记、状态、互斥锁、内容备份四者同处一个跨工作树共享目录。
_REAL_CONFIG_BACKUP_ROOT = os.path.join(
    _shared_locks_dir(), "real_config_snapshots")
#: 只留最近 N 个会话的备份，避免无限增长。
_REAL_CONFIG_BACKUP_KEEP = 20


def _real_config_files():
    """真实用户配置文件的唯一清单：``{键: 路径}``。

    守卫、备份、还原都只从这里取路径——新增第四份真实配置时加一个
    ``*_path`` 键即可，不会出现「守了 A 忘了 B」。当前三份的写入点仍是
    各自拼路径（settings / bookmarks / gui_config），收敛写入点属各线，
    详见 docs/COORDINATION.md「测试纪律」。
    """
    return {k: v for k, v in _REAL_CONFIG_GUARD.items()
            if k in ("path", "bm_path", "gui_path") and v}


def _backup_real_config_contents():
    """把真实配置**内容**整份拷到 .locks/real_config_snapshots/<会话>/。

    守卫原来只存 size/mtime/sha256：能证明「变了」，证明不了「原来是什么」。
    2026-11-13 用户真实 desktop_settings.json 被探针整份覆盖，最后靠别的
    探针留下的副本才逐字节还原——只有备份内容才谈得上可还原。
    """
    files = _real_config_files()
    if not files:
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(_REAL_CONFIG_BACKUP_ROOT,
                        f"session-{stamp}-{os.getpid()}")
    manifest = {}
    try:
        os.makedirs(dest, exist_ok=True)
        for key, src in files.items():
            if not os.path.exists(src):
                continue
            name = os.path.basename(src)
            shutil.copy2(src, os.path.join(dest, name))
            manifest[key] = {"path": src, "name": name,
                             "sha256": _file_state(src)["sha256"]}
        if not manifest:
            os.rmdir(dest)
            return
        with open(os.path.join(dest, "manifest.json"), "w",
                  encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        _REAL_CONFIG_GUARD["backup_dir"] = dest
        # 只留最近 N 个会话备份：每个会话一份，不清会越积越多。
        try:
            dirs = sorted(d for d in os.listdir(_REAL_CONFIG_BACKUP_ROOT)
                          if d.startswith("session-"))
            for old in dirs[:-_REAL_CONFIG_BACKUP_KEEP]:
                shutil.rmtree(os.path.join(_REAL_CONFIG_BACKUP_ROOT, old),
                              ignore_errors=True)
        except OSError:
            pass
    except OSError as exc:
        sys.stderr.write(
            "[mbdsdr-guard] 真实配置内容备份失败（这次仍会判红，但不可还原）："
            f"{exc}\n")


def _restore_real_config_from_backup():
    """会话结束时按内容备份还原被改的真实配置文件，返回还原说明。"""
    dest = _REAL_CONFIG_GUARD.get("backup_dir")
    if not dest:
        return []
    manifest_path = os.path.join(dest, "manifest.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return []
    restored = []
    for key, info in manifest.items():
        target = info.get("path")
        src = os.path.join(dest, info.get("name") or "")
        if not target or not os.path.exists(src):
            continue
        if _file_state(target)["sha256"] == info.get("sha256"):
            continue
        try:
            if os.path.exists(target):
                # 会话期间真实配置是被写保护的，还原前先临时放开。
                os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
            _WRITE_GUARD["bypass"] = True      # 守卫自己的还原口，唯一放行点
            try:
                shutil.copy2(src, target)
            finally:
                _WRITE_GUARD["bypass"] = False
        except OSError as exc:
            restored.append(f"  {key}: {target} 还原失败：{exc}")
        else:
            restored.append(f"  {key}: {target} 已按会话备份还原")
    return restored


# ---------------------------------------------------------------------------
# 防线 4c：真实 gui_config / bookmarks 的写入拦截（2026-11-13）
#
# 4b 把真实配置设成只读，能挡住写，但报出来的是裸 PermissionError，看不出
# 「该怎么做才对」。这里在 Python 层再挡一道：命中真实路径的写操作直接失败，
# 错误信息点名 ``isolated_profile`` fixture（它把 HOME/APPDATA 重定向到临时
# 目录，重定向之后的路径与这里记录的**真实**路径不同，自然放行）。
#
# 只拦「写」和「原子替换的目标」，读一律放行——守卫自己结束时要读备份、
# 判哈希，那些路径不能被自己挡住。``_WRITE_GUARD["bypass"]`` 是守卫收尾
# 还原内容时的唯一放行口，别在别处打开它。
# ---------------------------------------------------------------------------
_WRITE_GUARD = {"paths": frozenset(), "active": False, "bypass": False,
                "saved": {}}


def _write_guard_hit(file, mode):
    if not _WRITE_GUARD["active"] or _WRITE_GUARD["bypass"]:
        return False
    if mode is None:
        return False
    mode = mode if isinstance(mode, str) else str(mode)
    if not any(ch in mode for ch in "wax+"):
        return False
    try:
        key = _path_key(os.fspath(file))
    except TypeError:                 # 文件描述符 / 非路径对象
        return False
    return key in _WRITE_GUARD["paths"]


def _deny_real_config_write(file):
    raise PermissionError(
        "[mbdsdr-guard] 测试会话试图写入用户真实配置：%s\n"
        "请给用例请求 isolated_profile fixture（重定向 HOME/APPDATA 到临时"
        "目录），或把路径显式指向 tmp_path。真实配置只属于用户。" % (file,))


def _install_real_config_write_guard():
    """把三个真实配置文件列入写黑名单，并挂到 open/io.open/os.replace 上。"""
    if _WRITE_GUARD["active"]:
        return
    paths = {_path_key(p) for p in
             (_REAL_CONFIG_GUARD.get("path"),
              _REAL_CONFIG_GUARD.get("bm_path"),
              _REAL_CONFIG_GUARD.get("gui_path")) if p}
    if not paths:
        return
    import builtins
    import io

    real_open = builtins.open
    real_replace = os.replace
    real_rename = os.rename

    def guarded_open(file, mode="r", *args, **kwargs):
        if _write_guard_hit(file, mode):
            _deny_real_config_write(file)
        return real_open(file, mode, *args, **kwargs)

    def guarded_replace(src, dst, *args, **kwargs):
        if _write_guard_hit(dst, "w"):
            _deny_real_config_write(dst)
        return real_replace(src, dst, *args, **kwargs)

    def guarded_rename(src, dst, *args, **kwargs):
        if _write_guard_hit(dst, "w"):
            _deny_real_config_write(dst)
        return real_rename(src, dst, *args, **kwargs)

    _WRITE_GUARD.update(paths=frozenset(paths), active=True, saved={
        "builtins.open": real_open, "io.open": io.open,
        "os.replace": real_replace, "os.rename": real_rename})
    builtins.open = guarded_open
    io.open = guarded_open
    os.replace = guarded_replace
    os.rename = guarded_rename


def _uninstall_real_config_write_guard():
    saved = _WRITE_GUARD.get("saved") or {}
    if not saved:
        return
    import builtins
    import io
    builtins.open = saved["builtins.open"]
    io.open = saved["io.open"]
    os.replace = saved["os.replace"]
    os.rename = saved["os.rename"]
    _WRITE_GUARD.update(active=False, saved={})


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_modal: 允许该用例弹真模态（默认全局拦下；offscreen 下真 exec "
        "可能永不返回，只有确需验证模态行为的用例才可 opt-in）")
    _snapshot_real_config()
    _recover_stale_write_protection()
    _backup_real_config_contents()
    _protect_real_config()
    _install_real_config_write_guard()


@pytest.fixture(scope="session", autouse=True)
def _real_config_guard_fixture():
    yield
    try:
        _assert_real_config_untouched()
    finally:
        _unprotect_real_config()
        _uninstall_real_config_write_guard()


def _assert_real_config_untouched():
    path = _REAL_CONFIG_GUARD["path"]
    if not path:
        return
    before = _REAL_CONFIG_GUARD["before"]
    after = _file_state(path)
    added = sorted(set(_dir_names(_REAL_CONFIG_GUARD["dir"])
                       ) - set(_REAL_CONFIG_GUARD["before_listing"]))
    removed = sorted(set(_REAL_CONFIG_GUARD["before_listing"]
                         ) - set(_dir_names(_REAL_CONFIG_GUARD["dir"])))
    problems = []
    if after != before:
        delta = after["size"] - before["size"]
        problems.append(
            f"  {path}: 大小 {before['size']} -> {after['size']} "
            f"({delta:+d} bytes)；sha256 {before['sha256']} -> "
            f"{after['sha256']}；mtime_ns {before['mtime_ns']} -> "
            f"{after['mtime_ns']}")
    if added or removed:
        problems.append(
            f"  {_REAL_CONFIG_GUARD['dir']}: 新增={added} 消失={removed}")
    bm_path = _REAL_CONFIG_GUARD.get("bm_path")
    if bm_path:
        bal = _file_state(bm_path)
        if bal != _REAL_CONFIG_GUARD["bm_before"]:
            problems.append(
                f"  {bm_path}: 大小 {_REAL_CONFIG_GUARD['bm_before']['size']} -> "
                f"{bal['size']}；sha256 变化={bal['sha256'] != _REAL_CONFIG_GUARD['bm_before']['sha256']}（"
                "书签库被测试污染；用例须请求 isolated_profile）")
    gui_path = _REAL_CONFIG_GUARD.get("gui_path")
    if gui_path:
        gbefore = _REAL_CONFIG_GUARD.get("gui_observer_before")
        gafter = _gui_observer(gui_path)
        if gafter != gbefore:
            problems.append(
                f"  {gui_path}: 观测站 {gbefore!r} -> {gafter!r}"
                "（测试不得改写真实站址；用例须请求 isolated_profile）")
    restored = _restore_real_config_from_backup() if problems else []
    assert not problems, (
        "[mbdsdr-guard] 测试会话改动了用户真实配置，必须把 "
        "HOME/USERPROFILE/APPDATA/LOCALAPPDATA 重定向到 tmp_path：\n"
        + "\n".join(problems)
        + "\n本轮用到的会话备份："
        + str(_REAL_CONFIG_GUARD.get("backup_dir") or "未建立（定位失败）")
        + "\n按备份还原：\n" + ("\n".join(restored) or "  （无）")
        + "\n纪律见 docs/COORDINATION.md「测试纪律」。")


# ---------------------------------------------------------------------------
# 防线 4b：测试期间真实配置**写保护** + 会话内容备份（2026-11-13）
#
# 当晚的教训：靠「事后比 mtime 找是谁写的」会冤枉好人——本机 6 个以上并发
# pytest 时，谁写都会算到当时正在跑的用例头上；逐用例比对只能说明「这个窗口
# 内变了」，不能说明「这条用例写的」。正确方向是让写**进不去**：
#
#   1. pytest_configure 把真实配置文件设成只读：任何进程想写就当场失败在
#      它自己进程里（归因不需要猜），会话结束恢复原属性；
#   2. 内容备份到 .locks/real_config_snapshots/：万一有写绕过只读（改属性、
#      管理员权限），会话结束按备份逐字节还原——备份的是内容，不是哈希。
#
# 守卫因此降级为兜底：不再指认凶手，只在「居然有人写成功了」时喊一声。
# ---------------------------------------------------------------------------
_REAL_CONFIG_PROTECT_PREFIX = "write-protection-"
#: 共享的原属性台账：谁第一个上保护谁写，只有**全局**最后一个退出者才删。
#: 名字刻意不带 ``write-protection-`` 前缀（那是标记的通配），免得被当成标记扫。
_REAL_CONFIG_STATE_NAME = "protect-state.json"
#: 协调「删标记 / 查存活 / 恢复属性」的互斥文件（字节锁，进程被杀由内核释放）。
_REAL_CONFIG_MUTEX_NAME = "protect-mutex.lock"
#: 临界区只有几毫秒的目录操作；等不到说明别的会话卡在 IO，不该无限等。
_PROTECT_MUTEX_TIMEOUT_S = 10.0


def _protect_marker_path():
    return os.path.join(_REAL_CONFIG_BACKUP_ROOT,
                        f"{_REAL_CONFIG_PROTECT_PREFIX}{os.getpid()}.json")


def _protect_state_path():
    return os.path.join(_REAL_CONFIG_BACKUP_ROOT, _REAL_CONFIG_STATE_NAME)


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _pid_alive(pid, pid_start=None):
    """pid 是否仍活着；给了记录的启动时刻就一并核对，防 PID 复用误判。

    PID 复用会让「上上次会话的残留标记」看起来永远活着，于是最后一个退出者
    永远不敢恢复属性——真实配置被别人持续只读，且没有任何会话还会去拆。
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    actual = _process_start_time(pid)
    if actual is None:
        return False
    if pid_start is not None:
        try:
            if abs(float(actual) - float(pid_start)) > 2.0:
                return False          # 同号不同命：这是复用后的新进程
        except (TypeError, ValueError):
            pass
    return True


def _live_markers():
    """除本进程外，仍被**活会话**持有的保护标记（含其他工作树）。

    标记目录挂在全仓共享锁目录下，所以 worktree A 的会话能看见 worktree B
    的标记——「引用计数跨树共享」就是这个函数存在的理由。
    """
    out = []
    try:
        names = os.listdir(_REAL_CONFIG_BACKUP_ROOT)
    except OSError:
        return out
    own = os.path.abspath(_protect_marker_path())
    for name in names:
        if not (name.startswith(_REAL_CONFIG_PROTECT_PREFIX)
                and name.endswith(".json")):
            continue
        path = os.path.join(_REAL_CONFIG_BACKUP_ROOT, name)
        if os.path.abspath(path) == own:
            continue                  # 自己的标记由自己删，不算「别人」
        info = _read_json(path)
        if info and _pid_alive(info.get("pid"), info.get("pid_start_time")):
            out.append(info)
    return out


def _write_marker(entries):
    """记下本会话的身份与原属性台账（原子替换，读者看不到半个 JSON）。"""
    marker = _protect_marker_path()
    tmp = marker + f".{os.getpid()}.tmp"
    try:
        os.makedirs(_REAL_CONFIG_BACKUP_ROOT, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(),
                       "pid_start_time": _process_start_time(os.getpid()),
                       "entries": entries}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, marker)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _load_protect_state():
    state = _read_json(_protect_state_path())
    entries = (state or {}).get("entries")
    return entries if isinstance(entries, dict) and entries else None


def _save_protect_state(entries):
    path = _protect_state_path()
    tmp = path + f".{os.getpid()}.tmp"
    try:
        os.makedirs(_REAL_CONFIG_BACKUP_ROOT, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"entries": entries}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        sys.stderr.write(f"[mbdsdr-guard] 写保护台账写入失败，本次不上写保护"
                         f"（上不了也拆不掉，比永久只读安全）：{exc}\n")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False


def _drop_protect_state():
    try:
        os.remove(_protect_state_path())
    except OSError:
        pass


def _path_key(path):
    """台账键 = 归一化绝对路径。

    不能用「逻辑键（path/bm_path/gui_path）」做键：``MBDSDR_SETTINGS_DIR`` 会把
    ``path`` 指到临时目录，父会话与子会话的同名逻辑键是**不同文件**；共用一格
    会让子会话把父会话的路径改掉，收尾时恢复错文件、真文件永久只读。
    """
    return os.path.normcase(os.path.abspath(path))


def _restore_entry(entry):
    """按台账把一份文件的属性还回原样；用户自己设的只读一律不碰。"""
    if not isinstance(entry, dict) or entry.get("readonly"):
        return
    path = entry.get("path")
    if not path:
        return
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except (OSError, TypeError):
        pass


def _restore_unreferenced(ledger, live_paths):
    """只恢复「没有活会话再指着它」的条目，返回仍需保留的台账。

    ``live_paths is None`` 表示有活会话的标记没带条目（旧版），此时无法细分，
    整份台账都不许动——宁可晚一点恢复，也不拆别人正在用的保护。
    """
    if live_paths is None:
        return ledger
    kept = {}
    for key, entry in ledger.items():
        path = entry.get("path") if isinstance(entry, dict) else None
        if path and _path_key(path) in live_paths:
            kept[key] = entry
        else:
            _restore_entry(entry)
    return kept


def _live_protected_paths():
    """活会话仍要求保持只读的路径集合；None = 无法细分（整份台账都别动）。"""
    paths = set()
    for info in _live_markers():
        entries = info.get("entries")
        if not isinstance(entries, dict) or not entries:
            return None
        for entry in entries.values():
            if isinstance(entry, dict) and entry.get("path"):
                paths.add(_path_key(entry["path"]))
    return paths


def _try_byte_lock(fh):
    fh.seek(0)
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unbyte_lock(fh):
    try:
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def _protect_mutex(timeout=_PROTECT_MUTEX_TIMEOUT_S):
    """串行化「删自己标记 → 查存活标记 → 恢复属性 → 删台账」。

    没有这把互斥，两个会话同时退出会各自看见对方还活着的标记，于是都不恢复；
    可两边都已经删掉了自己的标记——属性永久只读，事后 ``_recover_stale``
    也没有标记可依。锁是字节锁，进程被杀时内核自动释放。
    """
    fh = None
    acquired = False
    try:
        try:
            os.makedirs(_REAL_CONFIG_BACKUP_ROOT, exist_ok=True)
            fh = open(os.path.join(_REAL_CONFIG_BACKUP_ROOT,
                                   _REAL_CONFIG_MUTEX_NAME), "a+b")
            if os.fstat(fh.fileno()).st_size == 0:
                fh.write(b"\0")
                fh.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    _try_byte_lock(fh)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
        except OSError as exc:
            sys.stderr.write(f"[mbdsdr-guard] 写保护互斥锁不可用：{exc}\n")
            if fh is not None:
                fh.close()
                fh = None
        yield acquired
    finally:
        if fh is not None:
            if acquired:
                _unbyte_lock(fh)
            fh.close()


def _recover_stale_write_protection():
    """清掉死会话留下的标记与只读属性（只动台账/标记记录过的文件）。

    跨工作树生效：任何一棵树的会话启动，都会替整仓收拾上次崩溃的残局。
    """
    if not os.path.isdir(_REAL_CONFIG_BACKUP_ROOT):
        return
    with _protect_mutex():
        ledger = dict(_load_protect_state() or {})
        try:
            names = os.listdir(_REAL_CONFIG_BACKUP_ROOT)
        except OSError:
            names = []
        for name in names:
            if not (name.startswith(_REAL_CONFIG_PROTECT_PREFIX)
                    and name.endswith(".json")):
                continue
            marker = os.path.join(_REAL_CONFIG_BACKUP_ROOT, name)
            info = _read_json(marker)
            if info is None:
                continue
            if _pid_alive(info.get("pid"), info.get("pid_start_time")):
                continue              # 那个会话还活着，别动它的保护
            # 旧版会话只写标记、不写台账：按标记记录补进台账再统一恢复。
            for inherited in (info.get("entries") or {}).values():
                if (isinstance(inherited, dict) and inherited.get("path")
                        and "readonly" in inherited):
                    ledger.setdefault(_path_key(inherited["path"]), {
                        "path": inherited["path"],
                        "readonly": bool(inherited["readonly"])})
            try:
                os.remove(marker)
            except OSError:
                pass
        kept = _restore_unreferenced(ledger, _live_protected_paths())
        if kept:
            _save_protect_state(kept)
        else:
            _drop_protect_state()


def _protect_real_config():
    """真实配置设只读：写入失败会发生在真正的写入者进程里。

    台账与标记都在**全仓共享**目录，且按**绝对路径**记账：别的会话（含别的
    worktree）先上了只读时，这里继承的是它台账里的**用户原值**，不是「此刻
    看到的只读」——否则最后退出者会把别人的保护误当成用户设置永久保留。
    """
    files = _real_config_files()
    if not files:
        return
    with _protect_mutex():
        ledger = dict(_load_protect_state() or {})
        if not ledger:
            # 兼容旧版会话：只写标记、没写台账，原值从标记里继承（按路径对齐）。
            for info in _live_markers():
                for inherited in (info.get("entries") or {}).values():
                    if (isinstance(inherited, dict) and inherited.get("path")
                            and "readonly" in inherited):
                        ledger.setdefault(_path_key(inherited["path"]), {
                            "path": inherited["path"],
                            "readonly": bool(inherited["readonly"])})
        entries = {}
        for key, path in files.items():
            entry = ledger.get(_path_key(path))
            if entry is None:
                try:
                    readonly = not (os.stat(path).st_mode & stat.S_IWRITE)
                except OSError:
                    continue
                entry = {"path": path, "readonly": readonly}
                ledger[_path_key(path)] = entry
            entry["path"] = path      # 保留最初记录的 readonly 原值
            entries[key] = entry      # 诊断/测试仍按逻辑键取
        _REAL_CONFIG_GUARD["write_protected"] = entries
        if not _save_protect_state(ledger):
            # 台账落不了盘就等于收尾时不知道该恢复成什么：宁可不设只读，
            # 也不留下一个没人拆的永久只读。
            _write_marker(entries)
            return
        for entry in ledger.values():
            if entry.get("readonly"):
                continue
            try:
                os.chmod(entry["path"], stat.S_IREAD)
            except OSError as exc:
                sys.stderr.write(
                    f"[mbdsdr-guard] 真实配置写保护失败（{entry['path']}）：{exc}\n")
        _write_marker(entries)


def _unprotect_real_config():
    """恢复可写由**全局最后一个还指着该文件的会话**执行，不是「我结束了」。

    判据：还有没有别的会话（任何工作树）的保护标记里记着这个路径。只要
    还剩一个活会话指着它，就不许恢复——否则先退出的会话一收尾，另一个
    会话剩下的时间全是裸奔窗口（1414→1207 的时间窗就是这么来的）。
    删除 + 查询 + 恢复必须在共享互斥锁内完成，否则两个会话同时退出会
    互相看见对方，都不恢复；而两边标记都已删掉，属性就成了永久只读。
    """
    entries = _REAL_CONFIG_GUARD.get("write_protected") or {}
    _REAL_CONFIG_GUARD["write_protected"] = {}
    try:
        os.remove(_protect_marker_path())
    except OSError:
        pass
    with _protect_mutex() as locked:
        if not locked:
            sys.stderr.write(
                "[mbdsdr-guard] 写保护互斥等不到（别的会话卡在 IO？）：本次不恢复"
                "属性，留给下一个会话的 _recover_stale_write_protection 收尾。\n")
            return
        ledger = _load_protect_state()
        if ledger is None:
            # 台账丢了就退回本会话内存里的记录（按路径键重建）。
            ledger = {_path_key(v["path"]): {"path": v["path"],
                                             "readonly": bool(v.get("readonly"))}
                      for v in entries.values()
                      if isinstance(v, dict) and v.get("path")}
        kept = _restore_unreferenced(ledger, _live_protected_paths())
        if kept:
            _save_protect_state(kept)
        else:
            _drop_protect_state()


def _qt_lock_held_elsewhere():
    """不等人地试抢一次全局 Qt 锁：抢到=没人持有（立刻释放），抢不到=有人在跑。"""
    path = _qt_lock_path()
    if not os.path.exists(path):
        return False
    fh = None
    try:
        fh = open(path, "a+b")
        if os.fstat(fh.fileno()).st_size == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        if fh is not None:
            fh.close()


def _lock_bypass_qt_problem():
    """绕锁进程碰了 Qt 全局态就返回原因；不该判红时返回 None。"""
    if os.environ.get("MBDSDR_TEST_LOCK", "1") != "0":
        return None
    widgets = sys.modules.get("PySide6.QtWidgets")
    if widgets is None or widgets.QApplication.instance() is None:
        return None
    if _qt_lock_held_elsewhere():
        return None           # 父会话持锁：本进程是它的子进程，机器仍被保护
    return ("[mbdsdr-lock] MBDSDR_TEST_LOCK=0 绕锁状态下构造了 QApplication。\n"
            "绕锁的前提是**不碰 Qt 全局态**（只跑纯子进程/纯计算用例）；"
            "构造窗口就必须拿锁。\n"
            "要么去掉 MBDSDR_TEST_LOCK=0，要么把这批用例挪到不构造 Qt 的路径。")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """用例主体前/后各查一次：绕锁碰 Qt 当场判 FAIL 并点名（不是 ERROR）。"""
    problem = _lock_bypass_qt_problem()
    if problem:
        pytest.fail(f"用例 {item.nodeid}：{problem}", pytrace=False)
    try:
        return (yield)
    finally:
        problem = _lock_bypass_qt_problem()
        if problem:
            pytest.fail(f"用例 {item.nodeid}：{problem}", pytrace=False)


@pytest.fixture(scope="session")
def real_config_snapshot(_real_config_guard_fixture):
    """暴露会话级配置守卫的快照，供守门用例确认守卫真的在跑。"""
    return _REAL_CONFIG_GUARD


#: 兜底隔离的「当前用例」配置路径。
#: 2026-11-12 实测：配置是进程级共享文件，前面用例把 focus_mode 写成 False 后，
#: 后面 8 条布局锁基线全部少 95px（详细栏 56 + 主工具栏 39）。所以每条用例
#: 换一份全新的 desktop_settings.json，谁也别读上一条用例写下的状态。
_BACKING_CONFIG = {"root": None, "path": None, "seq": 0, "real_appdata": None,
                   "real_default_path": None, "enabled": True}


def _advance_backing_config():
    """为「当前用例」分配一份全新的 desktop_settings.json。"""
    root = _BACKING_CONFIG["root"]
    if root is None or not _BACKING_CONFIG["enabled"]:
        return None
    _BACKING_CONFIG["seq"] += 1
    d = root / ("case%05d" % _BACKING_CONFIG["seq"])
    d.mkdir(parents=True, exist_ok=True)
    _BACKING_CONFIG["path"] = str(d / "desktop_settings.json")
    return _BACKING_CONFIG["path"]


@pytest.fixture(scope="session", autouse=True)
def _isolate_backing_config(tmp_path_factory):
    """兜底隔离：不管用例有没有请求 isolated_profile，配置都写不到真实文件。

    为什么需要兜底（2026-11-11 实测）：``MainWindow()`` 构造即
    ``DesktopSettings.load()``，随后一次 ``set('touch_mode', 同值)`` 会把配置
    置脏，500ms 防抖把**整份内容重写一遍**（size/sha256 不变、只有 mtime_ns
    变）。二十多个 fixture 各自「记得」请求 isolated_profile 才不出事，等于
    靠人记；只要有人在用例体里直接 ``MainWindow()`` 就漏。

    这里只重定向 ``default_config_path()``（desktop_settings.json），不动
    HOME/APPDATA：
      * HOME/APPDATA 是隔离手段的"大锤"，会话级改动会影响依赖真实 home 的
        用例；
      * QSettings 由下面的 ``_install_qsettings_isolation`` /
        ``_isolate_qsettings`` 单独接管（见那两处注释）。

    注意：pytest_configure 的守卫快照在**本 fixture 之前**取，所以守卫盯的
    仍是用户真实文件；本 fixture 只保证写盘不落到那里。
    """
    import settings as settings_mod

    root = tmp_path_factory.mktemp("realcfg")
    _BACKING_CONFIG.update(
        root=root,
        path=None,
        seq=0,
        real_appdata=os.environ.get("APPDATA"),
        real_default_path=settings_mod.default_config_path,
        enabled=os.environ.get("MBDSDR_BACKING_ISOLATE", "1") != "0",
    )

    def _current_path():
        # isolated_profile 已把 APPDATA/HOME 重定向到临时 profile：让配置跟随
        # profile（否则 test_default_path_stays_inside_isolated_profile 会红）。
        if os.environ.get("APPDATA") != _BACKING_CONFIG["real_appdata"]:
            return _BACKING_CONFIG["real_default_path"]()
        if _BACKING_CONFIG["root"] is None:
            return _BACKING_CONFIG["real_default_path"]()
        if not _BACKING_CONFIG["enabled"]:
            # 反证开关（MBDSDR_BACKING_ISOLATE=0）：退回旧的「全会话共用一个文件」。
            return str(root / "desktop_settings.json")
        return _BACKING_CONFIG["path"] or _advance_backing_config()

    mp = pytest.MonkeyPatch()
    mp.setattr(settings_mod, "default_config_path", _current_path)
    try:
        yield root
    finally:
        mp.undo()
        _BACKING_CONFIG.update(root=None, path=None, seq=0)


# ---------------------------------------------------------------------------
# 防线 7：QSettings 默认写 Windows 注册表，测试读到用户上次拖动的布局
# （2026-11-12 实测，PySide6 6.12 + Win）
#
# ``QSettings.setDefaultFormat(IniFormat)`` + ``setPath(IniFormat, ...)`` 对
# 两参构造 ``QSettings("MBDSDR", "Desktop")`` **无效**：
#   QSettings("MBDSDR", "Desktop").fileName()
#     -> '\\HKEY_CURRENT_USER\\Software\\MBDSDR\\Desktop'
# 于是 test_first_open_welcome 之类读到本机历史 three_column_sizes，红绿
# 随机器翻转。isolated_profile 里那两句因此是假隔离。
#
# 两层：
#   * 会话级（pytest_collection_finish）：把 QSettings 符号换成子类，两参
#     构造补成显式 Ini + UserScope，并把默认格式/路径指到会话临时目录。
#     module 级 fixture 在 function 级之前构造 MainWindow，必须在那之前生效。
#   * 函数级 autouse：每个用例再分一份独立临时目录，用例结束还原默认格式
#     并回到会话目录——同一用例连跑多次读数一致，也不把 ini 状态带给下一例。
# 只改 Python 侧符号，不碰产品代码；MBDSDR_QS_ISOLATE=0 可关（反证用）。
# Qt 没有 QSettings.path() 读取口，所以「还原 path」只能还原到本装置自己
# 记录的会话目录（原来是谁设的 path 已无从查证）。
# ---------------------------------------------------------------------------
_QS_ISOLATION = {"cls": None, "format": None, "session_path": None,
                 "seq": 0, "patched": {}}


def _qsettings_isolation_enabled():
    return os.environ.get("MBDSDR_QS_ISOLATE", "1") != "0"


def _qsettings_guard():
    """导入 scripts/qsettings_guard；哨兵坏了不能拖垮测试，失败即视为未装。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    scripts = os.path.join(root, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import qsettings_guard
        return qsettings_guard
    except Exception:  # noqa: BLE001
        return None


def _install_qsettings_write_guard():
    guard = _qsettings_guard()
    if guard is not None:
        guard.install()


def _install_qsettings_isolation():
    """收集定稿后、任何 fixture 之前装好：把双参 QSettings 钉到临时 Ini。"""
    if not _qsettings_isolation_enabled() or _QS_ISOLATION["cls"] is not None:
        return
    try:
        import PySide6.QtCore as qtcore
    except ImportError:               # 裁剪环境没有 PySide6：没有 QSettings 可隔离
        return

    real = qtcore.QSettings
    session_path = tempfile.mkdtemp(prefix="mbdsdr-qsettings-")
    _QS_ISOLATION.update(cls=real, format=real.defaultFormat(),
                         session_path=session_path, seq=0, patched={})

    class IsolatedQSettings(real):
        """双参构造补成显式 Ini + UserScope；其余重载原样透传。"""

        def __init__(self, *args, **kwargs):
            if (len(args) == 2 and not kwargs
                    and all(isinstance(a, str) for a in args)):
                args = (real.IniFormat, real.UserScope) + args
            super().__init__(*args, **kwargs)

    real.setDefaultFormat(real.IniFormat)
    for scope in (real.UserScope, real.SystemScope):
        real.setPath(real.IniFormat, scope, session_path)

    patched = {}
    for name, mod in list(sys.modules.items()):
        if mod is None:
            continue
        ns = getattr(mod, "__dict__", None)     # 避开模块级 __getattr__ 副作用
        if ns is not None and ns.get("QSettings") is real:
            try:
                mod.QSettings = IsolatedQSettings
                patched[name] = mod
            except Exception:  # noqa: BLE001 - 个别模块禁止赋值，跳过即可
                continue
    qtcore.QSettings = IsolatedQSettings
    _QS_ISOLATION["patched"] = patched


def _restore_qsettings_isolation():
    real = _QS_ISOLATION["cls"]
    if real is None:
        return
    import PySide6.QtCore as qtcore

    qtcore.QSettings = real
    for mod in _QS_ISOLATION["patched"].values():
        try:
            if getattr(mod, "__dict__", {}).get("QSettings") is not real:
                mod.QSettings = real
        except Exception:  # noqa: BLE001
            continue
    real.setDefaultFormat(_QS_ISOLATION["format"])
    for scope in (real.UserScope, real.SystemScope):
        real.setPath(real.IniFormat, scope, _QS_ISOLATION["session_path"])
    _QS_ISOLATION.update(cls=None, patched={})


@pytest.fixture(autouse=True)
def _isolate_qsettings():
    """每个用例一份干净 ini：读写都落在临时目录，结束还原默认格式/路径。"""
    real = _QS_ISOLATION["cls"]
    if real is None or not _qsettings_isolation_enabled():
        yield
        return
    old_format = real.defaultFormat()
    _QS_ISOLATION["seq"] += 1
    path = os.path.join(_QS_ISOLATION["session_path"],
                        "case%05d" % _QS_ISOLATION["seq"])
    os.makedirs(path, exist_ok=True)
    real.setDefaultFormat(real.IniFormat)
    for scope in (real.UserScope, real.SystemScope):
        real.setPath(real.IniFormat, scope, path)
    try:
        yield
    finally:
        real.setDefaultFormat(old_format)
        for scope in (real.UserScope, real.SystemScope):
            real.setPath(real.IniFormat, scope, _QS_ISOLATION["session_path"])
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def isolated_profile(tmp_path_factory):
    """把 HOME/USERPROFILE/APPDATA/LOCALAPPDATA/QSettings/layout_store 全部
    重定向到本模块专属临时目录。

    直接 ``MainWindow()`` 的用例必须显式请求它：MainWindow 内部会
    ``DesktopSettings.load()``（真实 ``%APPDATA%/mbdsdr/desktop_settings.json``）、
    ``QSettings("MBDSDR", "Desktop")`` 与全局 ``layout_store()``；
    ``settings.set(...)`` 的防抖写盘会把测试值写进用户真实配置
    （实测残留 ``...pytest-xxx\\...\\rec`` / ``Z:\\nope``，用户下次启动即见
    「录音目录不可写」假报警）。
    """
    home = tmp_path_factory.mktemp("profile")
    mp = pytest.MonkeyPatch()
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        mp.setenv(key, str(home))

    from PySide6.QtCore import QSettings
    old_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    for scope in (QSettings.UserScope, QSettings.SystemScope):
        QSettings.setPath(QSettings.IniFormat, scope, str(home / "qsettings"))

    import layout_store as ls
    # 收尾必须**还原这个对象本身**，不能 reset 成 None：跨用例隔离护栏
    # （tests/test_app_global_state_isolation.py）在 import 期抓的是
    # ``layout_store()`` 的**对象身份**，reset 会让它下次新建一个对象，
    # 于是「谁先跑」决定护栏红绿（不是谁泄漏，是这里把会话基线弄丢了）。
    previous_store = getattr(ls, "_default_store", None)  # noqa: SLF001
    ls._default_store = ls.LayoutStore(str(home / "layouts.json"))  # noqa: SLF001

    yield home

    ls._default_store = previous_store  # noqa: SLF001
    QSettings.setDefaultFormat(old_format)
    mp.undo()


# ---------------------------------------------------------------------------
# 防线 5：QApplication 级全局态跨用例泄漏（2026-11-12 实测）
#
# 现场：test_planetarium_entry_visibility 的 module 级 fixture 构造 MainWindow，
# 走 main_window._apply_theme → app.setPalette / setFont / setStyleSheet，把
# QApplication 的字体改成 MiSans 10.0、QSS 长度改成 20127，用例结束后没人还原。
# 同进程后面跑 test_spectrum_slider_overflow 的工具栏用例，最小宽从 126 涨到
# 160，「两个入口约 120px」直接红——纯顺序依赖红。
#
# 分两层，缺一不可：
#   * 函数级：每个用例结束，把它在用例体内改过的字体/样式表/调色板还回用例
#     开始时的样子（模块内用例之间互不污染）；
#   * 模块级：模块结束，还回本会话第一次见到 QApplication 时的样子。函数级
#     单独不够——它的快照拍在 module fixture **之后**，拍到的已是 MiSans，
#     只能挡住模块内的串味，跨模块的污染照样流出去（实测：只留函数级仍红）。
# 只在状态真的变了才调 setXxx：setStyleSheet 会触发全树重抛光，每个用例
# 白付一次会把套件拖慢。
# 已知边界：若 QApplication 本身由某个模块的 fixture 首次创建，会话基线就
# 没有「更早的干净状态」可拍，该模块的 teardown 只能跳过（它后面没有别的
# 模块时才无影响；全量套件中 app 一般在收集阶段已被创建）。
# ---------------------------------------------------------------------------
_APP_STYLE_BASELINE = {"app": None, "font": None, "qss": None, "palette": None}


def _live_qapp():
    """当前 QApplication；没有（纯逻辑用例）或 C++ 对象已删时返回 None。"""
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return None
        app.font()                       # 触碰一次：C++ 侧已删会抛 RuntimeError
        return app
    except Exception:  # noqa: BLE001 - 裁剪环境没装 PySide6 时一律跳过
        return None


def _app_style_snapshot(app):
    return {"font": app.font(), "qss": app.styleSheet(),
            "palette": app.palette()}


def _app_restore_style(app, snap):
    """差异恢复；顺序与 themes.Theme.apply 一致（palette → font → qss）。"""
    try:
        if app.palette() != snap["palette"]:
            app.setPalette(snap["palette"])
        if app.font() != snap["font"]:
            app.setFont(snap["font"])
        if app.styleSheet() != snap["qss"]:
            app.setStyleSheet(snap["qss"])
    except RuntimeError:
        pass                             # app 已随会话销毁


def _capture_app_style_baseline():
    """会话基线只在第一次见到 app 时拍一次，之后不覆盖。"""
    if _APP_STYLE_BASELINE["app"] is not None:
        return
    app = _live_qapp()
    if app is None:
        return
    _APP_STYLE_BASELINE["app"] = app
    _APP_STYLE_BASELINE.update(_app_style_snapshot(app))


@pytest.fixture(autouse=True)
def _restore_app_style():
    """每个用例前后保存/恢复 QApplication 的字体、样式表、调色板。"""
    app = _live_qapp()
    if app is None:
        yield
        return
    snap = _app_style_snapshot(app)
    yield
    _app_restore_style(app, snap)


@pytest.fixture(autouse=True, scope="module")
def _restore_app_style_at_module_boundary():
    """模块结束时把 app 级样式还回会话基线（跨文件不串味）。

    autouse 模块级 fixture 先于同模块其它 fixture 建立、晚于它们拆除，
    因此 setup 这里看到的是「上一模块收尾后」的状态，正好当懒基线；
    teardown 在 module 级 MainWindow 等夹具拆完之后执行。
    """
    _capture_app_style_baseline()
    yield
    app = _live_qapp()
    if app is None or _APP_STYLE_BASELINE["app"] is not app:
        return
    _app_restore_style(app, _APP_STYLE_BASELINE)


@pytest.fixture(scope="session")
def app_style_baseline():
    """会话 app 样式基线的可比较快照，供护栏用例直接断言状态。

    像素尺寸是果，字体/QSS 才是因：``134 px`` 能落在 ``110..135`` 容差里
    蒙过去，状态断言蒙不过去。
    """
    _capture_app_style_baseline()
    font = _APP_STYLE_BASELINE["font"]
    if font is None:
        pytest.skip("会话内没有 QApplication，app 样式基线不存在")
    qss = _APP_STYLE_BASELINE["qss"]
    return {
        "font_family": font.family(),
        "font_point_size": font.pointSizeF(),
        "qss_len": len(qss),
        "qss_head": qss[:64],
        "qss_tail": qss[-64:],
    }


def _loaded_app_singletons():
    """只认已经 import 过的单例模块：还原不能把 Qt 拽进纯逻辑用例。"""
    tm_mod = sys.modules.get("touch_manager")
    ls_mod = sys.modules.get("layout_store")
    pr_mod = sys.modules.get("panel_registry")
    ua_mod = sys.modules.get("ui_actions") or sys.modules.get("mbdsdr_ai.ui_actions")
    tm = getattr(getattr(tm_mod, "TouchManager", None), "_instance", None)
    reg = getattr(getattr(pr_mod, "PanelRegistry", None), "_instance", None)
    return tm, ls_mod, reg, ua_mod


@pytest.fixture(autouse=True)
def _restore_app_singletons():
    """每个用例前后保存/恢复应用级单例的可变状态。

    * ``touch_manager()._mode_override``：触屏模式是进程级开关，某个用例把
      它强制成 True/False 后不还，后面的用例会凭空进/出触屏模式；
    * ``layout_store()`` 的 ``_default_store``：用例替换单例（隔离目录）后
      忘记还原，后面的模块就写进别人的临时布局库；
    * ``PanelRegistry._instance`` 与它的 ``_widgets`` 表：类级单例 + 强引用
      表，某用例注册/替换面板后不摘，后面的用例 ``get_widget`` 会拿到别人的
      控件（甚至已删的 C++ 对象），而 ``_widgets`` 还会压住整棵子树不回收；
    * ``ui_actions._BUS`` 与 ``bus.executor``：主窗口 ``__init__`` 把绑定方法
      挂上总线，窗口关掉不还，后面的用例排水时打到已死的窗口上。

    这些模块若在本用例里才首次 import，teardown 把状态还给初始值
    （override=None / store=None / registry 单例=None / bus=None）——此时
    那些对象本来就是本用例造出来的。直接赋值而不走 ``set_override``：
    还原不该再发 ``touch_mode_changed`` 去惊动已经回收的控件。
    """
    if os.environ.get("MBDSDR_SINGLETON_GUARD", "1") == "0":
        yield
        return
    tm, ls_mod, reg, ua_mod = _loaded_app_singletons()
    tm_override = tm._mode_override if tm is not None else None
    store = getattr(ls_mod, "_default_store", None) if ls_mod is not None else None
    reg_widgets = dict(reg._widgets) if reg is not None else None
    bus = getattr(ua_mod, "_BUS", None) if ua_mod is not None else None
    bus_executor = bus.executor if bus is not None else None
    yield
    tm2, ls_mod2, reg2, ua_mod2 = _loaded_app_singletons()
    if tm2 is not None:
        tm2._mode_override = tm_override
    if ls_mod2 is not None:
        ls_mod2._default_store = store
    if reg2 is not None or reg_widgets is not None:
        import panel_registry as pr
        pr.PanelRegistry._instance = reg      # noqa: SLF001
        if reg is not None:
            reg._widgets.clear()              # noqa: SLF001
            reg._widgets.update(reg_widgets)  # noqa: SLF001
    if ua_mod2 is not None:
        ua_mod2._BUS = bus                    # noqa: SLF001
        if bus is not None:
            bus.executor = bus_executor


@pytest.fixture(autouse=True)
def _restore_time_engine():
    """每个用例结束把全局时间引擎还给「此刻 / 1×」。

    引擎是进程级单例，多个文件会把它钉在固定历元（2026-09-27 等）后忘记
    复位；后续「首次打开不跳时间」「实时」类断言就会莫名变红
    （2026-11-09 实测：test_settings_schema 的消光端到端用例把它 pause
    在 2026-09-27，导致 test_first_open_welcome 的实时断言差 42 天）。

    放在 function 级而不是 module 级：污染是逐用例发生的，module 级只能
    在文件末补救，中间已经红完了。
    """
    yield
    try:
        from mbdsdr_ai.new_spacetime import get_time_engine
        eng = get_time_engine()
        eng.set_time_now()
        eng.set_rate(1.0)
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def _restore_font_scale_globals():
    """每个用例前后把 themes 的三个进程级全局复位。

    `themes` 的裸名/包名已在 6c1064b1 合并为同一个模块对象，好处是"只有一个
    真值源"，代价是 `fit_scale_to_screen()` / `set_font_scale_override()` 写入
    的降档状态会留在进程里：前一个用例按 150% 请求被降到 100%，后一个用例
    断言"平台默认倍率/无提示"就会莫名变红（顺序依赖随机红）。

    复位放 function 级、且前后各一次：起点干净，失败用例也不把脏状态留给
    下一个。只复位缓存与提示，不碰 `QApplication` 已设置的字体（那属于用例
    自己负责的 UI 现场）。
    """
    try:
        import themes
    except Exception:  # noqa: BLE001 - 裁剪环境没有 themes 时跳过
        yield
        return

    def _reset():
        themes._FONT_SCALE_OVERRIDE = None
        themes._FONT_SCALE_NOTE = None
        themes._PLATFORM_FONT_PT = None

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True, scope="module")
def _recycle_top_level_widgets():
    """每模块结束回收遗留顶层 widget（AGENTS.md 对策①）。

    单例 QApplication 在同进程内累积前序文件留下的 widget，后续每次新建
    窗口连带全量样式/布局重算（二次方变慢；实测孤立构造 2.8s，第 6 个
    窗口 16.6s，AGENTS.md 记的极端值 11 分钟）。模块末回收让耗时平坦。

    必须用 sendPostedEvents(DeferredDelete)，不能只 processEvents：
    2026-11-04 实测三选一对拍——close+deleteLater+processEvents 之后
    topLevelWidgets 纹丝不动（39→39，回收是空转）；同一批窗口改用
    sendPostedEvents(None, QEvent.DeferredDelete) 直接清零（78→0）。
    原因是 processEvents 不保证派发 DeferredDelete（该事件按投递时的
    事件循环层级延后），deleteLater 因而永远悬着。

    粒度停在 module 不能下沉到 function：test_app_mode.py /
    test_desktop_ui_responsiveness.py 等把 MainWindow 做成模块级 fixture
    整模块复用，逐测试强拆会把还在用的窗口删掉（后续测试撞
    "wrapped C/C++ object deleted"）。autouse 同级 fixture 先建后拆，
    故本回收跑在同模块其它模块级 fixture 拆除之后，不会抢跑。
    """
    yield
    try:
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        try:
            w.close()
            w.deleteLater()
        except RuntimeError:
            pass                            # C++ 侧已删，包装器悬空
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


#: pytest 自己在每个 phase 增删的变量，不属于测试泄漏，兜底不碰。
_PYTEST_MANAGED_ENV = frozenset({"PYTEST_CURRENT_TEST"})


@pytest.fixture(autouse=True, scope="module")
def _restore_os_environ_at_module_boundary(request):
    """模块结束时把 ``os.environ`` 还回本模块建立时的样子（跨文件不串味）。

    2026-11-12 实测（两文件复现）：A 文件的 module fixture 在 ``yield`` 之前
    写入环境变量后抛异常，setup 失败 → 写在 ``yield`` 之后的还原代码永远
    不执行，同一 pytest 进程里后跑的 B 文件读到被改写的值，走进另一条分支。
    逐个 fixture 补 try/finally 只能覆盖已知点；这里做机械兜底：autouse
    模块级 fixture 最先建立、最后拆除，正好在模块边界把整个环境还原。

    只还原模块期间的增删改，不碰模块 import 时 ``setdefault`` 的基线
    （那些在快照之前就已写入，属于本模块声明的测试环境）。
    ``MBDSDR_ENV_GUARD=0`` 显式关闭（反证用）。
    """
    if os.environ.get("MBDSDR_ENV_GUARD", "1") == "0":
        yield
        return
    before = {k: v for k, v in os.environ.items()
              if k not in _PYTEST_MANAGED_ENV}
    yield
    after = {k: v for k, v in os.environ.items()
             if k not in _PYTEST_MANAGED_ENV}
    changed = sorted(
        (set(after) - set(before))
        | (set(before) - set(after))
        | {k for k in set(before) & set(after) if before[k] != after[k]})
    for key in set(after) - set(before):
        os.environ.pop(key, None)
    for key, value in before.items():
        if os.environ.get(key) != value:
            os.environ[key] = value
    if changed:
        module = getattr(request, "module", None)
        name = getattr(module, "__name__", "?")
        print("[mbdsdr-env-guard] %s 结束时还原环境变量：%s"
              % (name, ", ".join(changed)), flush=True)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    _RESULT["exitstatus"] = int(exitstatus)
    guard = _qsettings_guard()
    if guard is None:
        return
    written = guard.records()
    if not written:
        return
    guard.report()
    print("[mbdsdr] QSettings 写入哨兵：隔离期外仍有 %d 次写入真实 store，"
          "本会话判红" % len(written), flush=True)
    _RESULT["exitstatus"] = 1
    session.exitstatus = 1


def pytest_unconfigure(config):
    _restore_qsettings_isolation()
    sys.stdout.flush()
    sys.stderr.flush()
    _release_qt_lock(_LOCK["fh"], _LOCK["path"])   # 交锁并删 owner，否则别的会话看到的持有者永远是上一个
    _LOCK["fh"] = None
    _LOCK["path"] = None
    code = _RESULT["exitstatus"]
    if sys.platform == "win32":
        kernel32 = ctypes.windll.kernel32
        # 必须声明 argtypes/restype：不声明时 ctypes 按 32 位 int 传句柄，
        # x64 伪句柄 -1 被截断成无效句柄，TerminateProcess 静默失败，
        # 掉回 os._exit 照旧撞 Qt DLL detach 崩（2026-10-20 实测）。
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.TerminateProcess(kernel32.GetCurrentProcess(), code)
    os._exit(code)
