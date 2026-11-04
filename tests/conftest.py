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
import os
import sys
import time

import pytest

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
