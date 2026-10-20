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
结果（testsfailed）主动硬退出，跳过 Py_FinalizeEx 的 PySide 强拆。

平台差异（2026-10-20 Windows 实测补充）：POSIX 的 os._exit 跳过 atexit 即可
规避；但 Windows 的 os._exit 仍走 DLL_PROCESS_DETACH——Qt DLL 的 detach
清理照删不误（崩溃栈顶即 os._exit 行）。故 Windows 改用 TerminateProcess
硬杀（不跑 DllMain detach、不进 atexit），两平台同语义。
这是规避不是修复——修复属 PySide6 上游（或桌面端产品退出路径重排）。
"""
import ctypes
import os
import sys

import pytest

# 网络隔离（与 MBDSDR_SETTINGS_DIR 同族的测试环境隔离）：
# 默认禁网，让全量套件天然离线、绝不因联网挂死。orbit.fetch_tle 等
# 联网取数路径在离线时走磁盘缓存或诚实空态（无缓存即抛 TLEUnavailableError
# 或回退"无可见卫星"），绝不假造数据。调用方可预设 MBDSDR_OFFLINE=0 放行。
os.environ.setdefault("MBDSDR_OFFLINE", "1")


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    sys.stdout.flush()
    sys.stderr.flush()
    code = 1 if session.testsfailed else 0
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
