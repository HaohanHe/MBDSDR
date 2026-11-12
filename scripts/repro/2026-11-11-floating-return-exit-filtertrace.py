#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""浮动面板「归位 → 退出」时事件过滤器看到的 obj 类型追踪（只读，不改产品代码）。

目的：把「哪个布局被删了而 app 级过滤器还挂着」定死到**运行时证据**，而不是靠
静态推断。做法是运行时给 ``MainWindow.eventFilter`` 套一层记录器（不改文件、
不改行为），记录每个事件的三件事：

    type(obj).__name__ / shiboken6.isValid(obj) / event.type()

重点看 ``isValid=False`` 的条目——那是「Python 包装还活着、C++ 对象已销毁」的
对象。序列固定为：构造 → 弹出左栏一个页签 → 归位 → 关闭窗口 → 销毁 → 退出。

输出：stdout 摘要 + ``docs/perf-evidence/2026-11-11/floating_return_exit_filtertrace.json``。

用法：
    python scripts/repro/2026-11-11-floating-return-exit-filtertrace.py
"""
from __future__ import annotations

import collections
import gc
import json
import os
import shutil
import sys
import tempfile
import weakref

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"
EVID = os.path.join(ROOT, "docs", "perf-evidence", "2026-11-11",
                    "floating_return_exit_filtertrace.json")


def _prepare_env() -> str:
    os.makedirs(TMP_ROOT, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="float-ret-", dir=TMP_ROOT)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    if "--screen" in sys.argv:
        os.environ["QT_QPA_PLATFORM"] = "windows"
        os.environ["QT_SCALE_FACTOR"] = "1"
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    else:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    return profile


def main() -> int:
    profile = _prepare_env()
    try:
        import shiboken6
        from PySide6.QtCore import QEvent, QSettings
        from PySide6.QtWidgets import QApplication

        QSettings.setDefaultFormat(QSettings.IniFormat)
        for scope in (QSettings.UserScope, QSettings.SystemScope):
            QSettings.setPath(QSettings.IniFormat, scope,
                              os.path.join(profile, "qsettings"))
        import layout_store as ls
        ls.reset_layout_store_singleton()
        ls._default_store = ls.LayoutStore(os.path.join(profile, "layouts.json"))  # noqa: SLF001
        app = QApplication.instance() or QApplication(sys.argv[:1])
        import main_window as mw
    except Exception as exc:  # noqa: BLE001
        print(f"[环境不满足] {type(exc).__name__}: {exc}")
        shutil.rmtree(profile, ignore_errors=True)
        return 2

    counters: collections.Counter = collections.Counter()
    invalid: list[dict] = []
    tail: collections.deque = collections.deque(maxlen=80)
    phase = {"name": "ctor"}
    panel_layouts: dict[int, str] = {}
    panel_hits: list[dict] = []
    orig = mw.MainWindow.eventFilter

    def traced(self, obj, ev):  # noqa: N802 - Qt 命名
        try:
            tname = type(obj).__name__
        except Exception:  # noqa: BLE001
            tname = "<type-exc>"
        try:
            valid = bool(shiboken6.isValid(obj))
        except Exception:  # noqa: BLE001
            valid = None
        try:
            etype = int(ev.type().value) if hasattr(ev.type(), "value") else int(ev.type())
        except Exception:  # noqa: BLE001
            etype = -1
        counters[(tname, valid)] += 1
        rec = {"phase": phase["name"], "type": tname, "valid": valid,
               "event_type": etype, "py_id": id(obj)}
        tail.append(rec)
        if id(obj) in panel_layouts:
            panel_hits.append(dict(rec, panel_layout=panel_layouts[id(obj)]))
        if valid is False:
            extra = dict(rec)
            if tname != "<type-exc>":
                try:
                    extra["repr"] = repr(obj)
                except Exception:  # noqa: BLE001
                    pass
            invalid.append(extra)
        return orig(self, obj, ev)

    mw.MainWindow.eventFilter = traced

    win = mw.MainWindow()
    app.processEvents()

    popped = None
    try:
        tabs = getattr(win, "left_tab", None)
        titles = [tabs.tabText(i) for i in range(tabs.count())] if tabs else []
        print(f"左栏页签: {titles}")
        # 选一个非 0 号页签，避免把天空槽搬来搬去（那会引入另一条重排路径）。
        idx = 1 if tabs is not None and tabs.count() > 1 else 0
        phase["name"] = "pop"
        win._pop_out_tab("left", idx)
        app.processEvents()
        keys = list(win._float_windows)
        popped = keys[0] if keys else None
        print(f"弹出: idx={idx} key={popped} "
              f"floating={list(win._float_windows)}")

        phase["name"] = "return"
        if popped is not None:
            from PySide6.QtWidgets import QLayout
            panel = win._float_windows[popped]["panel"]
            panel_wr = weakref.ref(panel)
            lay = panel.layout()
            panel_layouts[id(lay)] = type(lay).__name__ if lay else "None"
            for sub in panel.findChildren(QLayout):
                panel_layouts[id(sub)] = type(sub).__name__
            print(f"归位前 panel 布局: {panel_layouts[id(lay)]}；"
                  f"面板内布局共 {len(panel_layouts)} 个："
                  f"{sorted(set(panel_layouts.values()))}")
            # 关键：不能留 Python 引用，否则 panel 及其布局不会被销毁，
            # 也就永远看不到 isValid=False 的条目（第一版就栽在这里）。
            del lay
            if "--force-delete" in sys.argv:
                # 变体 C：模拟「C++ 先删、Python 包装还活着」——_return_panel
                # 的注释说这是退出崩溃的形态（QBoxLayout 比 Python 包装先走）。
                # 保留 panel 引用，只让 C++ 侧走 deleteLater。
                panel.deleteLater()
                from PySide6.QtCore import QCoreApplication
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                app.processEvents()
                print(f"变体 C：panel C++ 已删、包装仍活: "
                      f"isValid={shiboken6.isValid(panel)}")
                del panel
                if "--exit-return" not in sys.argv:
                    win._return_panel(popped)
            else:
                del panel
                if "--exit-return" in sys.argv:
                    # 变体 B：不手动归位，让 closeEvent 里的 _return_all_panes
                    # 在窗口析构过程中收浮层（崩溃栈就是从这条路径来的）。
                    print("变体 B：保留浮动窗，交给 closeEvent 收")
                else:
                    win._return_panel(popped)
        app.processEvents()
        gc.collect()
        app.processEvents()
        if popped is not None:
            print(f"归位后 panel 是否已被回收: {panel_wr() is None}")
        print(f"归位后仍挂着的浮动窗口: {list(win._float_windows)}")

        # 复现路径里的第二步：归位后走一次触控适配（崩溃栈尖就在
        # _apply_touch_adaptation 的 findChildren(QComboBox) 循环里）。
        phase["name"] = "touch"
        if "--exit-return" not in sys.argv:
            try:
                win._apply_touch_adaptation()
                print("触控适配: ok")
            except Exception as exc:  # noqa: BLE001
                print(f"触控适配抛错: {type(exc).__name__}: {exc}")
            app.processEvents()
    except Exception as exc:  # noqa: BLE001
        print(f"[无法判定] 浮动面板序列失败：{type(exc).__name__}: {exc}")

    phase["name"] = "gc"
    gc.collect()
    for _ in range(3):
        app.processEvents()

    phase["name"] = "exit"
    try:
        win.close()
    except Exception:  # noqa: BLE001
        pass
    app.processEvents()
    win.deleteLater()
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()

    del win
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    app.quit()

    print(f"\n面板内布局命中过滤器的事件: {len(panel_hits)} 条")
    for r in panel_hits[:20]:
        print(f"  {r['phase']:<7} {r['type']:<16} valid={r['valid']} ev={r['event_type']}")

    print("\n各阶段事件计数（type × isValid）:")
    for (tname, valid), n in counters.most_common(20):
        print(f"  {tname:<24} valid={valid!s:<5} {n}")
    print(f"\nisValid=False 的条目: {len(invalid)} 条")
    for r in invalid[:20]:
        print(f"  {r['phase']:<7} {r['type']:<24} ev={r['event_type']} id={r['py_id']}")

    payload = {
        "snapshot": "1e90b7f6",
        "platform": os.environ.get("QT_QPA_PLATFORM"),
        "variant": ("force-delete" if "--force-delete" in sys.argv
                    else "exit-return" if "--exit-return" in sys.argv
                    else "manual-return"),
        "counter_by_type_valid": [
            {"type": t, "valid": v, "count": n}
            for (t, v), n in counters.most_common()
        ],
        "invalid_count": len(invalid),
        "invalid": invalid[:200],
        "panel_layouts": {str(k): v for k, v in panel_layouts.items()},
        "panel_layout_filter_hits": panel_hits[:200],
        "tail": list(tail),
        "verdict": ("在「弹出 → 归位 → 关闭退出」序列里，事件过滤器收到 "
                    f"{len(invalid)} 个 isValid=False 的对象"),
    }
    os.makedirs(os.path.dirname(EVID), exist_ok=True)
    out = EVID if payload["variant"] == "manual-return" else EVID.replace(
        ".json", "_%s.json" % payload["variant"])
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"\n写入 {out}")
    shutil.rmtree(profile, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
