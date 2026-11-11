#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""P0-15 独立复现脚本：``init_agent()`` 是否仍在窗口构造期同步执行。

roadmap 断言（原文）：
    「AI 面板构造期同步 ``init_agent()`` 973–1079 ms（496 工具内核）」
    证据等级：实测（ctor ×2，973–1079 ms，占构造 26–32%）。

做法（offscreen + 独立 profile，不碰用户配置）：
  * 静态：AST 检查 ``AIPanel.__init__`` 是否直接调用 ``self.init_agent``；
  * 动态：在构造 MainWindow 前给 ``AIPanel.init_agent`` 装计时探针，
    构造完成后报告「是否被调用 / 调用次数 / 总耗时 / 调用栈来源」。
  这能区分两种情形：
    a) 字面断言（AI 面板自己的 ctor）不成立，但同一成本迁到了
       MainWindow 构造期（``_build_capability_drawer`` → ``_ensure_agent_kernel``）；
    b) 已经彻底移出构造期（构造完成后 agent 仍为空、后续异步初始化）。

退出码（三态）：
    0  复现成功   —— MainWindow 构造期间仍同步调用 init_agent（成本仍在首帧前）
    1  未复现     —— 构造期间 0 次调用，或调用耗时 < 50 ms
    2  无法判定   —— Qt/MainWindow 构造失败

反证对照：探针自身记录调用栈；若探测不到任何调用，同时打印构造后
``window.ai_panel.agent is None``，避免把「探针没装上」误判成「已修」。

用法：
    python scripts/repro/2026-11-11-p0-15-init-agent-in-ctor.py
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
import tempfile
import time
import traceback

ROOT = os.environ.get("MBDSDR_REPRO_ROOT") or os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"


def _hr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def _ctor_calls_init_agent(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError):
        return None
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef) or cls.name != "AIPanel":
            continue
        for fn in cls.body:
            if not isinstance(fn, ast.FunctionDef) or fn.name != "__init__":
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    fn2 = node.func
                    if (isinstance(fn2, ast.Attribute) and fn2.attr == "init_agent"):
                        return True
            return False
    return None


def main() -> int:
    profile = tempfile.mkdtemp(prefix="p0-15-profile-", dir=TMP_ROOT)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"临时档案: {profile}（HOME/USERPROFILE/APPDATA/LOCALAPPDATA 全重定向）")

    ai_panel_path = os.path.join(ROOT, "desktop", "ai_panel.py")
    static = _ctor_calls_init_agent(ai_panel_path)
    print(f"静态：AIPanel.__init__ 直接调用 init_agent = {static}")

    try:
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
        app = QApplication.instance() or QApplication(sys.argv)
        import ai_panel as ap
        import main_window as mw
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] Qt/MainWindow 导入失败：{type(exc).__name__}: {exc}")
        shutil.rmtree(profile, ignore_errors=True)
        return 2

    calls = []

    orig = ap.AIPanel.init_agent

    def _probe(self, *a, **k):
        t0 = time.perf_counter()
        try:
            return orig(self, *a, **k)
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            stack = [f"{f.name}:{f.lineno}"
                     for f in traceback.extract_stack()[-4:-1]]
            calls.append((dt, stack))

    ap.AIPanel.init_agent = _probe

    _hr("实测：构造 MainWindow（offscreen），期间统计 init_agent")
    window = None
    t0 = time.perf_counter()
    try:
        window = mw.MainWindow()
        app.processEvents()
    except Exception as exc:  # noqa: BLE001
        ap.AIPanel.init_agent = orig
        print(f"[无法判定] MainWindow 构造失败：{type(exc).__name__}: {exc}")
        shutil.rmtree(profile, ignore_errors=True)
        return 2
    ctor_ms = (time.perf_counter() - t0) * 1000.0

    total_ms = sum(c[0] for c in calls)
    print(f"  MainWindow 构造总耗时 : {ctor_ms:.0f} ms")
    print(f"  init_agent 调用次数   : {len(calls)}")
    print(f"  init_agent 总耗时     : {total_ms:.0f} ms")
    for i, (dt, stack) in enumerate(calls, 1):
        print(f"    第 {i} 次：{dt:.0f} ms，调用栈 {' <- '.join(stack)}")
    try:
        has_agent = getattr(window.ai_panel, "agent", None) is not None
    except Exception:  # noqa: BLE001
        has_agent = None
    print(f"  构造后 ai_panel.agent 非空 : {has_agent}")

    ap.AIPanel.init_agent = orig
    try:
        window.close()
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(profile, ignore_errors=True)

    reproduced = bool(calls) and total_ms >= 50.0
    print(f"\n结论：当前实现复现 P0-15（构造期同步 init_agent）: {reproduced}")
    if reproduced:
        print("[复现] 成本仍在首帧前；但位置不是 AIPanel.__init__，"
              "而是 MainWindow 构造链（路径需勘误）。")
        return 0
    if calls:
        print(f"[未复现] 虽有 {len(calls)} 次调用，但合计 {total_ms:.0f} ms < 50 ms，"
              "不再构成首帧阻塞。")
        return 1
    print("[未复现] 构造期未调用 init_agent（或已异步化）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
