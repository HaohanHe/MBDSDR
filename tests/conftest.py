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
import ctypes
import gc
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import sys
import tempfile
import time
import types

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
# 门控（2026-11-11 改为运行时判定）：旧实现读源码前 200 KB 找 "PySide6"/
# "QApplication"，543 个测试文件里漏掉一批「间接依赖」——test_x 只 import
# status_panel，Qt 在 status_panel 里被拉进来，源码看不到字样（实测
# test_decode_error_labels 已加载 Qt 却无 [mbdsdr-lock] 行）。现在改为两条
# 运行时判据，任一命中即算：① 收集阶段 sys.modules 里已有 PySide6；② 用例
# 模块里有函数在体内懒 import PySide6（这类 58 个文件纯看 sys.modules 会漏，
# 只能读已编译的代码对象，不读源码文本）。
# 已知残留：只懒 import「会拉 Qt 的项目模块」（10 个文件）仍判定不到，
# 其中 9 个在旧启发式下同样没有锁，属既有缺口，不构成本次回退。
# ---------------------------------------------------------------------------
def _qt_lock_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.environ.get(
        "MBDSDR_TEST_LOCK_PATH",
        os.path.join(os.path.dirname(root), ".locks", "qt_test.lock"),
    )


def _acquire_qt_lock(path, timeout):
    """返回 (fileobj, waited_seconds)；超时返回 (None, waited_seconds)。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "a+b")
    if os.fstat(fh.fileno()).st_size == 0:
        fh.write(b"\0")
        fh.flush()
    started = time.monotonic()
    while True:
        try:
            fh.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh, time.monotonic() - started
        except OSError:
            if time.monotonic() - started >= timeout:
                fh.close()
                return None, time.monotonic() - started
            time.sleep(0.5)


def _release_qt_lock(fh):
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


_LOCK = {"fh": None, "waited": 0.0}

# conftest 自己在 pytest_configure 里为了「真实配置快照」会 import desktop/settings，
# 而 settings.py 顶部就 `from PySide6.QtCore import QTimer`。这份基线把「守卫自己
# 拉进来的 Qt」从判定里扣掉，否则每个会话都会被判成 Qt 会话（纯非 Qt 文件也被拉
# 进来抢锁，正是本次要修的误报）。
_QT_BASELINE = {"mods": set()}


def _code_imports_qt(code):
    """已编译代码对象（含嵌套函数/类方法）里是否出现 PySide6 的 import。

    不读源码文本：`from PySide6.QtWidgets import QApplication` 编译后模块名
    落在 co_names（实测 ('PySide6.QtWidgets', 'QApplication')），函数体内再
    嵌套的 def/lambda 落在 co_consts 的 code 对象里。
    """
    for name in code.co_names:
        if name == "PySide6" or name.startswith("PySide6."):
            return True
    for const in code.co_consts:
        if isinstance(const, types.CodeType) and _code_imports_qt(const):
            return True
    return False


def _code_names(code):
    """代码对象（含嵌套）里出现过的全部名字。"""
    names = set(code.co_names)
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            names |= _code_names(const)
    return names


_MODULE_QT_CACHE = {}
_MODULE_QT_MISS = set()


def _name_pulls_new_qt(name, depth=0, seen=None):
    """按模块名判断：import 它会不会拉进「基线里没有的」PySide6 子模块。

    只编译不执行（`spec.loader.get_code`），避免为了判定而真的 import 面板——
    那会把内存开销提前到门控之前，正是锁要防的东西。
    只认仓库内的单段模块名（desktop/ 与仓库根），标准库/三方包一律跳过；
    点号名会顺带 import 父包，有副作用，不追。
    """
    if name in _MODULE_QT_CACHE:
        return _MODULE_QT_CACHE[name]
    if name in _MODULE_QT_MISS or depth > 2:
        return False
    if seen is None:
        seen = set()
    if name in seen:
        return False
    seen.add(name)
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        _MODULE_QT_MISS.add(name)
        return False
    origin = getattr(spec, "origin", None) if spec is not None else None
    if not origin or not os.path.abspath(origin).startswith(_ROOT_DIR + os.sep):
        _MODULE_QT_MISS.add(name)
        return False
    try:
        code = spec.loader.get_code(name)          # 只编译，不执行
    except (ImportError, SyntaxError, OSError, AttributeError):
        _MODULE_QT_MISS.add(name)
        return False
    if code is None:
        _MODULE_QT_MISS.add(name)
        return False
    qt = {n for n in code.co_names if n == "PySide6" or n.startswith("PySide6.")}
    if qt - _QT_BASELINE["mods"]:
        _MODULE_QT_CACHE[name] = True
        return True
    for sub in code.co_names:
        if "." in sub or not sub.islower() or not sub.isidentifier():
            continue
        if _name_pulls_new_qt(sub, depth + 1, seen):
            _MODULE_QT_CACHE[name] = True
            return True
    return False


def _module_refs_qt(mod):
    """模块命名空间里是否直接挂着 PySide6 的模块/类/函数（模块级 import）。"""
    for obj in vars(mod).values():
        if isinstance(obj, types.ModuleType):
            name = getattr(obj, "__name__", "")
            if name == "PySide6" or name.startswith("PySide6."):
                return True
        elif getattr(obj, "__module__", "").startswith("PySide6"):
            return True
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


def _module_lazily_imports_qt(mod):
    """模块自身定义的函数/类方法/fixture 里，是否有体内懒 import PySide6，
    或懒 import 一个「会把 PySide6 拉进来」的仓库内模块（如 main_window）。"""
    modname = getattr(mod, "__name__", "")
    for obj in vars(mod).values():
        code = _code_of(obj)
        if code is not None:
            if _code_imports_qt(code):
                return True
            for name in _code_names(code):
                if "." in name or not name.islower() or not name.isidentifier():
                    continue
                if _name_pulls_new_qt(name):
                    return True
            continue
        if isinstance(obj, type) and getattr(obj, "__module__", None) == modname:
            for member in vars(obj).values():
                code = _code_of(member)
                if code is not None and _code_imports_qt(code):
                    return True
    return False


def _qt_trigger(items):
    """本会话会不会加载 PySide6；返回触发原因字符串，不加载则返回 None。

    运行时判定，不看源码文本：
      1. 收集阶段 sys.modules 里多了 PySide6 模块（扣掉 conftest 自己的基线）
         —— 覆盖直接 import 和「test_x → status_panel → PySide6」这类间接依赖；
      2. 用例模块的命名空间里挂着 Qt 模块/类（模块级 import 的静态等价），或
         有函数在体内懒 import PySide6（收集阶段还看不到，读已编译代码对象）。

    conftest 自己不会误报：基线取自 pytest_configure 里配置快照跑完之后。
    """
    new = {n for n in sys.modules
           if n == "PySide6" or n.startswith("PySide6.")} - _QT_BASELINE["mods"]
    if new:
        return "收集阶段加载 %s" % (sorted(new)[0],)
    seen = set()
    for item in items:
        mod = getattr(item, "module", None)
        if mod is None or id(mod) in seen:
            continue
        seen.add(id(mod))
        if _module_refs_qt(mod):
            return "模块级 Qt 依赖（%s）" % (getattr(mod, "__name__", "?"),)
        if _module_lazily_imports_qt(mod):
            return "用例内有懒 import（%s）" % (getattr(mod, "__name__", "?"),)
    return None


def pytest_collection_finish(session):
    """收集定稿、用例尚未执行时拿锁：保证「同一时刻只有一个 Qt 测试进程在跑」。"""
    # 此刻所有测试模块已 import、任何 module fixture 都还没跑：第一次见到
    # QApplication 就在这里拍 app 级样式基线（锁的几处早退不能吞掉它）。
    _capture_app_style_baseline()
    # 同理：module 级 fixture 会先于 function 级构造 MainWindow，QSettings
    # 的隔离必须在这之前装好。
    _install_qsettings_isolation()
    # 隔离只对 pytest 有效；外面还有独立进程直连真实存储。装上写入哨兵，
    # 任何漏到非临时 store 的写都会留痕并在 sessionfinish 直接把会话判红。
    _install_qsettings_write_guard()
    if os.environ.get("MBDSDR_TEST_LOCK", "1") == "0":
        return
    trigger = _qt_trigger(session.items)
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
            "（另一条会话正在跑 Qt 用例；本机 commit 上限 38GB，并发会把内存"
            "预算挤穿。稍后重试，或设 MBDSDR_TEST_LOCK_TIMEOUT 加长等待。）"
            % (waited, path),
            returncode=2,
        )
    _LOCK["fh"] = fh
    _LOCK["waited"] = waited
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


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_modal: 允许该用例弹真模态（默认全局拦下；offscreen 下真 exec "
        "可能永不返回，只有确需验证模态行为的用例才可 opt-in）")
    _snapshot_real_config()
    # 配置快照会 import desktop/settings（其顶部 import PySide6.QtCore）：把这批
    # 守卫自己拉进来的模块记成基线，锁门控只认「收集阶段新增」的 Qt 模块。
    _QT_BASELINE["mods"] = {n for n in sys.modules
                            if n == "PySide6" or n.startswith("PySide6.")}


@pytest.fixture(scope="session", autouse=True)
def _real_config_guard_fixture():
    yield
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
    assert not problems, (
        "[mbdsdr-guard] 测试会话改动了用户真实配置，必须把 "
        "HOME/USERPROFILE/APPDATA/LOCALAPPDATA 重定向到 tmp_path：\n"
        + "\n".join(problems)
        + "\n纪律见 docs/COORDINATION.md「测试纪律」。")


@pytest.fixture(scope="session")
def real_config_snapshot(_real_config_guard_fixture):
    """暴露会话级配置守卫的快照，供守门用例确认守卫真的在跑。"""
    return _REAL_CONFIG_GUARD


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
    target = str(root / "desktop_settings.json")
    mp = pytest.MonkeyPatch()
    mp.setattr(settings_mod, "default_config_path", lambda: target)
    try:
        yield root
    finally:
        mp.undo()


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
    ls.reset_layout_store_singleton()
    ls._default_store = ls.LayoutStore(str(home / "layouts.json"))  # noqa: SLF001

    yield home

    ls.reset_layout_store_singleton()
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
    _release_qt_lock(_LOCK["fh"])       # 硬退出前先交锁，否则其它会话要等满超时
    _LOCK["fh"] = None
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
