#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""退出期 0xC0000005 取证矩阵：`os._exit(0)` 到底在什么组合下崩。

背景：本线量到 `os._exit(0)` 5/5 崩溃（0xC0000005），设置线的探针
`scripts/repro/2026-11-11-exit-path-probe.py`（`0ccc517a`）7/7 全 0。
两个 5/5 不可能都对，所以把差异拆成可判定的格子，每格 5 次，全记退出码。

第一组（三因子矩阵，8 格）：
    1. app      : `qapp`（只有 QApplication）  ×  `window`（构造并 show MainWindow）
    2. exit     : `main`（主线程调 os._exit）  ×  `watchdog`（daemon 线程调）
    3. platform : `offscreen`                   ×  `windows`

第二组（设置线五个条件 + `close()` 时序逐条补齐，每格 5 次）：
    plain            主线程 os._exit，窗口不 close（exec 已返回）
    close_before     exec 返回后先 window.close() + processEvents 再 os._exit
    close_then_del   window.close() + deleteLater() + processEvents 再 os._exit
    kill_in_exec     daemon 线程在 app.exec() **仍在跑**时 os._exit（设置线口径）
    second_window    先建一套 MainWindow 并销毁，再建第二套后退出
    live_thread      退出时仍有 **非 daemon** 线程持有 PySide 对象
    second_qapp      同进程尝试第二套 QApplication（Qt 通常拒绝）

本脚本自身**不装** import 钩子、`atexit`、`faulthandler`、异常钩子——
收尾期不跑任何探针代码，避免探针自己成为嫌疑人。

每个子进程上报，用来区分「退出那刻还有没有 PySide 对象活着」与
「app.exec() 有没有真的跑起来」：

    exec_entered / quit_fired / exec_returned
    qt_live（退出前仍存活的 QObject 实例数）
    window_alive / threads / main_thread（os._exit 的调用线程）

命令（仓库根执行）：

    # 全部：第一组 8 格 + 第二组 10 格，每格 5 次
    python scripts/repro/2026-11-11-exit-os-exit-matrix.py

    # 只跑某一组
    python scripts/repro/2026-11-11-exit-os-exit-matrix.py --suite exit
    python scripts/repro/2026-11-11-exit-os-exit-matrix.py --suite cond

    # 单格单跑（子进程入口；退出码即被测进程退出码）
    python scripts/repro/2026-11-11-exit-os-exit-matrix.py --child \
        --app window --exit main --platform offscreen --cond plain

退出码约定：
    子进程   0 = 正常退出（os._exit(0)）；3221225477 / -1073741819 = 0xC0000005；
             3221226505 / -1073740791 = 0xC0000409（fail-fast）；1 = 脚本异常
    驱动器   0 = 矩阵跑完（不论是否崩）；2 = 有格子起不来（环境不支持）

环境：每格都做 profile 隔离（HOME/USERPROFILE/APPDATA/LOCALAPPDATA →
临时目录），`MBDSDR_OFFLINE=1`；平台插件只由 `QT_QPA_PLATFORM` 控制。
本脚本不改产品代码，也不读真实配置。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXIT_CELLS = [(a, e, p, "plain")
              for a in ("qapp", "window")
              for e in ("main", "watchdog")
              for p in ("offscreen", "windows")]

COND_CELLS = [("window", "main", p, c)
              for p in ("offscreen", "windows")
              for c in ("plain", "close_before", "close_then_del",
                        "second_window", "live_thread", "second_qapp")] + \
             [("window", "watchdog", p, "kill_in_exec")
              for p in ("offscreen", "windows")]

#: 退出码 → 可读标签（Windows 上 subprocess 可能给无符号或负值，两种都收）
CODE_LABEL = {
    0: "0",
    3221225477: "0xC0000005",
    -1073741819: "0xC0000005",
    3221226505: "0xC0000409",
    -1073740791: "0xC0000409",
    1: "1(脚本异常)",
    2: "2(环境不支持)",
    -999: "超时",
}


def _label(code: int) -> str:
    return CODE_LABEL.get(code, str(code))


# ------------------------------------------------------------------ 子进程
def run_child(app_kind: str, exit_kind: str, platform: str, cond: str) -> None:
    """一格一次：构造 → exec → 退出。结果以单行 JSON 打到 stdout。"""
    iso = tempfile.mkdtemp(prefix="mbdsdr-exit-iso-")
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = iso
    os.environ["QT_QPA_PLATFORM"] = platform
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")

    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    os.chdir(ROOT)

    from PySide6.QtCore import QObject, QTimer          # noqa: E402
    from PySide6.QtWidgets import QApplication         # noqa: E402

    info = {"app": app_kind, "exit": exit_kind, "platform": platform,
            "cond": cond, "exec_entered": False, "quit_fired": False,
            "exec_returned": False, "qt_live": -1, "window_alive": False,
            "threads": 0, "second_qapp_ok": None, "stage": "import"}

    app = QApplication.instance() or QApplication([])
    info["stage"] = "app"

    if cond == "second_qapp":
        try:
            QApplication([])                           # 第二套（Qt 通常拒绝）
            info["second_qapp_ok"] = True
        except BaseException as exc:                   # noqa: BLE001
            info["second_qapp_ok"] = False
            info["second_qapp_err"] = repr(exc)[:200]

    window = None
    if app_kind == "window":
        from main_window import MainWindow             # noqa: E402
        window = MainWindow()
        window.resize(1200, 800)
        window.show()
        app.processEvents()
        info["stage"] = "window_shown"

        if cond == "second_window":
            # 第一套销毁后再建第二套：同进程第二套 Qt 窗口栈
            window.close()
            window.deleteLater()
            app.processEvents()
            window = MainWindow()
            window.resize(1200, 800)
            window.show()
            app.processEvents()
            info["stage"] = "second_window_shown"

    if cond == "live_thread":
        # 非 daemon 线程在退出那一刻仍持有 PySide 对象
        held, ready, stop = {}, threading.Event(), threading.Event()

        def _holder():
            held["timer"] = QTimer()                   # 只持有，不启动
            ready.set()
            stop.wait(30.0)

        threading.Thread(target=_holder, name="py-holder",
                         daemon=False).start()
        ready.wait(10.0)
        info["holder_alive"] = True

    def _quit():
        info["quit_fired"] = True
        app.quit()

    if cond != "kill_in_exec":
        QTimer.singleShot(600, _quit)

    if cond == "kill_in_exec":
        # 设置线口径：exec 仍在跑时由 daemon 线程结束进程
        def _killer():
            time.sleep(0.8)
            _exit_now(0, info)

        threading.Thread(target=_killer, name="probe-watchdog",
                         daemon=True).start()

    info["exec_entered"] = True
    code = app.exec()
    info["exec_returned"] = True
    info["window_alive"] = window is not None
    info["stage"] = "loop_returned"

    if cond in ("close_before", "close_then_del") and window is not None:
        # 退出路径里先走一遍窗口关闭（另一线报告 close() 与收尾也会踩同一颗）
        window.close()
        if cond == "close_then_del":
            window.deleteLater()
        app.processEvents()
        info["stage"] = "window_closed"

    try:
        import gc
        live = 0
        for obj in gc.get_objects():
            try:
                if isinstance(obj, QObject):
                    live += 1
            except Exception:  # noqa: BLE001
                continue
        info["qt_live"] = live
    except Exception:  # noqa: BLE001
        info["qt_live"] = -2

    if exit_kind == "main":
        _exit_now(code, info)
    else:
        threading.Thread(target=lambda: _exit_now(code, info),
                         name="exit-watchdog", daemon=True).start()
        time.sleep(10.0)   # 由看门狗线程结束进程
        os._exit(code)     # 兜底：线程没调成也不能挂住


def _exit_now(code: int, info: dict) -> None:
    """os._exit 前打一行标记（对齐设置线探针的留痕格式）。"""
    info["threads"] = threading.active_count()
    print("EXIT from_thread=%s main_thread=%s threads=%d" % (
        threading.current_thread().name,
        threading.current_thread() is threading.main_thread(),
        info["threads"]), flush=True)
    print("RESULT " + json.dumps(info), flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


# ------------------------------------------------------------------ 驱动器
def run_cell(cell, runs: int):
    app_kind, exit_kind, platform, cond = cell
    codes, infos = [], []
    for idx in range(runs):
        cmd = [sys.executable, os.path.abspath(__file__), "--child",
               "--app", app_kind, "--exit", exit_kind,
               "--platform", platform, "--cond", cond]
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=180)
            code = proc.returncode
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except subprocess.TimeoutExpired:
            code, out = -999, ""
        codes.append(code)
        payload = None
        for line in out.splitlines():
            if line.startswith("RESULT "):
                try:
                    payload = json.loads(line[len("RESULT "):])
                except ValueError:
                    payload = None
        infos.append(payload)
        print("  run %d/%d -> %s" % (idx + 1, runs, _label(code)), flush=True)
    return codes, infos


def _yn(value) -> str:
    if value is None:
        return "?"
    return "是" if value else "否"


def report(title: str, cells, runs: int) -> list:
    print("\n[%s] %d 格 × %d 次" % (title, len(cells), runs), flush=True)
    rows = []
    for cell in cells:
        app_kind, exit_kind, platform, cond = cell
        print("[cell] app=%s exit=%s platform=%s cond=%s"
              % (app_kind, exit_kind, platform, cond), flush=True)
        codes, infos = run_cell(cell, runs)
        ok = [i for i in infos if i]
        rows.append({
            "cell": "%s / %s / %s / %s" % (app_kind, exit_kind, platform, cond),
            "codes": codes,
            "crashes": sum(1 for c in codes if c != 0),
            "exec_entered": all(i["exec_entered"] for i in ok) if ok else None,
            "quit_fired": any(i["quit_fired"] for i in ok) if ok else None,
            "exec_returned": any(i["exec_returned"] for i in ok) if ok else None,
            "qt_live": ok[0]["qt_live"] if ok else -1,
            "threads": ok[0]["threads"] if ok else -1,
        })
    print("\n" + "=" * 112)
    print("%-42s %-30s %-8s %-7s %-6s %-7s %-6s" % (
        "单元格", "退出码", "崩/总", "exec进", "quit", "exec回", "qt_live"))
    print("=" * 112)
    for row in rows:
        print("%-42s %-30s %-8s %-7s %-6s %-7s %-6s" % (
            row["cell"], ",".join(_label(c) for c in row["codes"]),
            "%d/%d" % (row["crashes"], len(row["codes"])),
            _yn(row["exec_entered"]), _yn(row["quit_fired"]),
            _yn(row["exec_returned"]), row["qt_live"]))
    print("=" * 112)
    crash_cells = [r["cell"] for r in rows if r["crashes"]]
    print("\n崩溃格子：%s" % ("、".join(crash_cells) if crash_cells else "无"))
    print("口径：只写「崩/总 = n/5」；0/5 只表示本机该格未复现，不反推其它格。")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--app", choices=("qapp", "window"))
    ap.add_argument("--exit", dest="exit_kind", choices=("main", "watchdog"))
    ap.add_argument("--platform", choices=("offscreen", "windows"))
    ap.add_argument("--cond", choices=("plain", "close_before", "close_then_del",
                                       "kill_in_exec", "second_window",
                                       "live_thread", "second_qapp"),
                    default="plain")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--suite", choices=("all", "exit", "cond"), default="all")
    args = ap.parse_args()

    if args.child:
        if not (args.app and args.exit_kind and args.platform):
            print("child 需要 --app/--exit/--platform", file=sys.stderr)
            return 2
        run_child(args.app, args.exit_kind, args.platform, args.cond)
        return 0

    print("repo   : %s" % ROOT)
    print("python : %s" % sys.version.split()[0])
    print("env    : profile 隔离到临时目录，MBDSDR_OFFLINE=1，"
          "QT_ENABLE_HIGHDPI_SCALING=%s" % os.environ.get("QT_ENABLE_HIGHDPI_SCALING", "未设"))
    rows = []
    if args.suite in ("all", "exit"):
        rows += report("第一组 三因子", EXIT_CELLS, args.runs)
    if args.suite in ("all", "cond"):
        rows += report("第二组 设置线五条件", COND_CELLS, args.runs)
    print("\n小结：共 %d 格，崩溃格 %d 个。"
          % (len(rows), sum(1 for r in rows if r["crashes"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
