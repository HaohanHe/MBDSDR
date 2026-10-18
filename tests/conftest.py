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
结果（testsfailed）主动 os._exit 退出，跳过 Py_FinalizeEx 的 PySide 强拆。
这是规避不是修复——修复属 PySide6 上游（或桌面端产品退出路径重排）。
"""
import os
import sys

import pytest


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(1 if session.testsfailed else 0)
