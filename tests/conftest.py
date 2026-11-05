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
import os
import sys
import time

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


def _collects_qt_tests(items):
    """收集结果里是否有直接 import PySide6 的用例文件（读源码关键字，零副作用）。"""
    seen_files = set()
    for item in items:
        path = str(getattr(item, "fspath", ""))
        if not path.endswith(".py") or path in seen_files:
            continue
        seen_files.add(path)
        try:
            with open(path, "rb") as f:
                head = f.read(200_000)
        except OSError:
            continue
        if b"PySide6" in head or b"QApplication" in head:
            return True
    return False


def pytest_collection_finish(session):
    """收集定稿、用例尚未执行时拿锁：保证「同一时刻只有一个 Qt 测试进程在跑」。"""
    if os.environ.get("MBDSDR_TEST_LOCK", "1") == "0":
        return
    if not _collects_qt_tests(session.items):
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
        "[mbdsdr-lock] 已获得 Qt 测试互斥锁（等待 %.1fs）：%s\n" % (waited, path)
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
_REAL_CONFIG_GUARD = {"path": None, "dir": None, "before": None,
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


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "allow_modal: 允许该用例弹真模态（默认全局拦下；offscreen 下真 exec "
        "可能永不返回，只有确需验证模态行为的用例才可 opt-in）")
    _snapshot_real_config()


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
    assert not problems, (
        "[mbdsdr-guard] 测试会话改动了用户真实配置，必须把 "
        "HOME/USERPROFILE/APPDATA/LOCALAPPDATA 重定向到 tmp_path：\n"
        + "\n".join(problems)
        + "\n纪律见 docs/COORDINATION.md「测试纪律」。")


@pytest.fixture(scope="session")
def real_config_snapshot(_real_config_guard_fixture):
    """暴露会话级配置守卫的快照，供守门用例确认守卫真的在跑。"""
    return _REAL_CONFIG_GUARD


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


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    _RESULT["exitstatus"] = int(exitstatus)


def pytest_unconfigure(config):
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
