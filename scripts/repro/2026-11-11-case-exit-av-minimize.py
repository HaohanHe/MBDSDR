#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CASE-2026-11-11-01：退出期 0xC0000005 最小化 + 必要条件矩阵。

背景
----
`scripts/repro/2026-11-11-p0-15b-first-tick-cost.py --mode baseline --screen --show`
在真屏指纹下跑完读数、结果文件已落盘，**最后一行 `os._exit(1)` 却返回
`-1073741819`（0xC0000005）**。这说明崩溃不在脚本业务逻辑里，而在
`os._exit` → `ExitProcess` 的 DLL detach 收尾期。

本脚本把「真屏 / show / 事件循环形态 / 退出方式 / 是否删临时档案」拆成格子，
每格独立子进程、每个阶段写一行带 fsync 的标记，崩溃后由**最后一条标记**
判定崩在哪一步，不靠推测。

阶段标记（写进 `MBDSDR_AV_MARK` 指向的文件）
------------------------------------------
    S1_import      Qt / desktop 模块导入完成
    S2_app         QApplication 建好（可选 setFont 生产字体）
    S3_ctor        MainWindow 构造完成
    S4_show        show() 完成
    S5_loop        事件循环形态跑完（processEvents 或 app.exec 返回）
    S6_rmtree      临时档案删除完成
    S7_exit        即将调用退出原语（此标记之后再崩 = 崩在退出原语内部）

退出原语（`--exit`）
-------------------
    osmain     os._exit(7)          —— 主线程（p0-15b 现状）
    oswatch    os._exit(7)          —— daemon 线程，主线程在循环里
    natural    不调退出原语，让解释器走 Py_FinalizeEx（= 全窗析构）
    terminate  kernel32!TerminateProcess —— 不跑 DLL detach 的硬终止（对照）

用法
----
    # 全部格子，每格 5 次
    python scripts/repro/2026-11-11-case-exit-av-minimize.py

    # 单格单跑（退出码即被测进程退出码）
    python scripts/repro/2026-11-11-case-exit-av-minimize.py --child --cell A

    # 只跑指定格子
    python scripts/repro/2026-11-11-case-exit-av-minimize.py --cells A,F,G
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP_ROOT = r"D:\mbdsdr\output\tmp"

#: 格子定义：platform / show / loop / exit / rmtree / font / window
#:   platform  windows|offscreen
#:   show      dontshow（WA_DontShowOnScreen + show，p0-15b 口径）| none
#:   loop      pe（只 processEvents）| exec（app.exec + 定时 quit）
#:   exit      osmain|oswatch|natural|terminate
#:   rmtree    yes|no
#:   font      yes|no（生产字体 MiSans 10pt，见 desktop/main.py:709）
#:   window    full|none（none = 只建 QApplication，不建 MainWindow）
CELLS = {
    # 基线：与 p0-15b --screen --show 同形
    "A": dict(platform="windows", show="dontshow", loop="pe",
              exit="osmain", rmtree="yes", font="yes", window="full"),
    # 去掉 show()
    "B": dict(platform="windows", show="none", loop="pe",
              exit="osmain", rmtree="yes", font="yes", window="full"),
    # 只 import，不建窗口
    "C": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="none"),
    # 只建 QApplication（+生产字体）
    "D": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="yes", window="apponly"),
    # 不删临时档案
    "E": dict(platform="windows", show="dontshow", loop="pe",
              exit="osmain", rmtree="no", font="yes", window="full"),
    # 全窗析构：不调退出原语，走 Py_FinalizeEx
    "F": dict(platform="windows", show="dontshow", loop="exec",
              exit="natural", rmtree="yes", font="yes", window="full"),
    # 硬终止但不跑 DLL detach
    "G": dict(platform="windows", show="dontshow", loop="pe",
              exit="terminate", rmtree="yes", font="yes", window="full"),
    # os._exit 由 daemon 线程调（旧口径 hard 形态）
    "H": dict(platform="windows", show="dontshow", loop="pe",
              exit="oswatch", rmtree="yes", font="yes", window="full"),
    # offscreen 平台插件
    "I": dict(platform="offscreen", show="dontshow", loop="pe",
              exit="osmain", rmtree="yes", font="yes", window="full"),
    # 不设生产字体
    "J": dict(platform="windows", show="dontshow", loop="pe",
              exit="osmain", rmtree="yes", font="no", window="full"),
    # 只构造、不 show、不跑事件循环，直接主线程 os._exit
    "K": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="yes", window="full"),
    # app.exec() 跑完返回后，主线程 os._exit
    "L": dict(platform="windows", show="dontshow", loop="exec",
              exit="osmain", rmtree="yes", font="yes", window="full"),
    # 旧口径：app.exec() 仍在跑，daemon 线程 3s 后 os._exit
    "M": dict(platform="windows", show="dontshow", loop="exec",
              exit="oswatch", rmtree="yes", font="yes", window="full"),
    # 最小 QMainWindow（空菜单栏）+ 主线程 os._exit（平台级 vs 应用级的判据）
    "O": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="minqmw"),
    # 纯 QWidget + 主线程 os._exit
    "P": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="minwidget"),
    # ---- MainWindow 内部二分：只建单个面板/单个结构 ----
    # 最小 QMainWindow + 3 个未启动的 QThread
    "Q": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="qthreads"),
    # 最小 QMainWindow + 20000 个带父对象子控件
    "R": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="many"),
    # 只建 AI 面板（内部持 QThread 且未启动）
    "S": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="aipanel"),
    # 只建频谱面板（自绘 + OpenGL try-import）
    "T": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="spectrum"),
    # 只建状态面板
    "U": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="statuspanel"),
    # ---- 真凶判定：设备枚举线程 ----
    # 完整 MainWindow，但构造期不启动设备枚举（patch refresh_devices_async）
    "V": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="yes", window="full",
              patch="noenum"),
    # 完整 MainWindow，构造后等 8 s 让枚举线程跑完再退
    "W": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="yes", window="full",
              wait_s=8.0),
    # 只建模块面板（枚举线程的唯一启动点）
    "X": dict(platform="windows", show="none", loop="none",
              exit="osmain", rmtree="yes", font="no", window="modulepanel"),
}

CODE_LABEL = {
    0: "0", 7: "7",
    3221225477: "0xC0000005", -1073741819: "0xC0000005",
    3221226505: "0xC0000409", -1073740791: "0xC0000409",
    -999: "超时",
}


def _label(code: int) -> str:
    return CODE_LABEL.get(code, str(code))


def _mark(path: str, stage: str, extra: str = "") -> None:
    """阶段留痕：追加一行并 fsync，进程被强杀也不会丢。"""
    line = f"{stage}{(' ' + extra) if extra else ''}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


# ------------------------------------------------------------------ 子进程
def run_child(cell_id: str) -> None:
    cfg = dict(CELLS[cell_id])
    mark = os.environ["MBDSDR_AV_MARK"]
    fh_path = os.environ.get("MBDSDR_AV_FH")
    if fh_path:
        # 只装 VEH 处理器：正常路径不跑任何探针代码，崩溃时才落栈
        import faulthandler
        fh_file = open(fh_path, "w", encoding="utf-8")
        faulthandler.enable(file=fh_file, all_threads=True)
    profile = tempfile.mkdtemp(prefix="case-av-profile-", dir=TMP_ROOT)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        os.environ[key] = profile
    os.environ["QT_QPA_PLATFORM"] = cfg["platform"]
    if cfg["platform"] == "windows":
        os.environ["QT_SCALE_FACTOR"] = "1"
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.environ.setdefault("MBDSDR_NO_HW", "1")
    os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "desktop"))
    os.chdir(ROOT)

    from PySide6.QtCore import Qt, QTimer                      # noqa: E402
    from PySide6.QtWidgets import QApplication                 # noqa: E402

    app = QApplication.instance() or QApplication([])
    if cfg["font"] == "yes":
        from themes import get_app_font                        # noqa: E402
        app.setFont(get_app_font())
    _mark(mark, "S1_import", f"platform={app.platformName()}")
    _mark(mark, "S2_app", f"font={app.font().family()}/{app.font().pointSizeF()}")

    window = None
    if cfg.get("patch") == "noenum":
        # 判定格：构造期不启动设备枚举（QThreadPool 任务 = 崩溃线程来源）
        import module_panel as _mp                            # noqa: E402
        _mp.ModulePanel.refresh_devices_async = lambda self: None
        _mark(mark, "S0_patch", "noenum")
    if cfg["window"] == "full":
        from main_window import MainWindow                      # noqa: E402
        window = MainWindow()
        _mark(mark, "S3_ctor")
    elif cfg["window"] in ("minqmw", "minwidget", "qthreads", "many",
                           "aipanel", "spectrum", "statuspanel",
                           "modulepanel"):
        from PySide6.QtWidgets import QMainWindow, QWidget      # noqa: E402
        if cfg["window"] == "minwidget":
            window = QWidget()
        else:
            window = QMainWindow()
            window.setCentralWidget(QWidget())
            if cfg["window"] != "many":
                window.menuBar()                            # 空菜单栏
        if cfg["window"] == "qthreads":
            from PySide6.QtCore import QThread                  # noqa: E402
            for _ in range(3):
                QThread()               # 只构造、不 start（与 MainWindow 内一致）
        elif cfg["window"] == "many":
            for _ in range(20000):
                QWidget(window)
        elif cfg["window"] == "aipanel":
            import ai_panel                                     # noqa: E402
            window._probe_panel = ai_panel.AIPanel()
        elif cfg["window"] == "spectrum":
            import spectrum_widget                              # noqa: E402
            window._probe_panel = spectrum_widget.SpectrumPanel()
        elif cfg["window"] == "statuspanel":
            import status_panel                                 # noqa: E402
            window._probe_panel = status_panel.StatusPanel()
        elif cfg["window"] == "modulepanel":
            import module_panel                                 # noqa: E402
            window._probe_panel = module_panel.ModulePanel()
        window.resize(640, 480)
        _mark(mark, "S3_ctor", cfg["window"])

    if cfg["show"] == "dontshow" and window is not None:
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        window.show()
        _mark(mark, "S4_show")

    if cfg["loop"] == "pe":
        app.processEvents()
        _mark(mark, "S5_loop", "processEvents")
    elif cfg["loop"] == "exec" and cfg["exit"] != "oswatch":
        QTimer.singleShot(600, app.quit)
        rc = app.exec()
        _mark(mark, "S5_loop", f"exec_ret={rc}")

    if cfg["rmtree"] == "yes":
        shutil.rmtree(profile, ignore_errors=True)
        _mark(mark, "S6_rmtree")

    if cfg.get("wait_s"):
        # 判定格：等后台线程跑完再退（不 patch，只等）
        time.sleep(float(cfg["wait_s"]))
        _mark(mark, "S6b_wait", f"{cfg['wait_s']}s")

    mode = cfg["exit"]
    if mode == "natural":
        # 不调退出原语：解释器收尾（Py_FinalizeEx）= 全窗析构
        return

    _mark(mark, "S7_exit", f"mode={mode}")
    sys.stdout.flush()
    sys.stderr.flush()

    if mode == "terminate":
        import ctypes
        ctypes.windll.kernel32.TerminateProcess(
            ctypes.windll.kernel32.GetCurrentProcess(), 7)
        time.sleep(5.0)          # TerminateProcess 不返回
        return

    if mode == "oswatch":
        def _kill():
            # 与旧口径一致：循环还在跑时，3 s 后由 daemon 线程结束进程
            if cfg["loop"] == "exec":
                time.sleep(3.0)
            os._exit(7)

        threading.Thread(target=_kill, name="av-watchdog", daemon=True).start()
        if cfg["loop"] == "exec":
            QTimer.singleShot(30000, app.quit)
            app.exec()
        time.sleep(10.0)
        os._exit(7)
        return

    os._exit(7)


# ------------------------------------------------------------------ 驱动器
def _wer_sig(pid: int, start: float) -> str:
    """按 PID 精确取 WER 签名（并发会话同时在崩，按时间段聚合会串味）。"""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(start - 5))
    ps = (
        "$ev=Get-WinEvent -FilterHashtable @{LogName='Application';"
        "ProviderName='Application Error';StartTime=[datetime]::ParseExact("
        f"'{ts}','yyyy-MM-ddTHH:mm:ss',$null)}} -ErrorAction SilentlyContinue;"
        f"$m=$ev|Where-Object{{$_.Properties[8].Value -eq {pid}}}|Select-Object -First 1;"
        "if($m){$m.Properties[3].Value+'+0x'+"
        "('{0:x}' -f [int64]$m.Properties[7].Value)+'/'+$m.Properties[6].Value}"
    )
    for attempt in range(6):
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                                 capture_output=True, text=True, timeout=60)
            sig = (out.stdout or "").strip()
            if sig:
                return sig
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)         # WER 写事件有延迟，重试几轮
    return "—"


def run_cell(cell_id: str, runs: int) -> dict:
    cfg = CELLS[cell_id]
    codes, last_marks, sigs = [], [], []
    t0 = time.time()
    for _ in range(runs):
        mark = os.path.join(TMP_ROOT, f"av-mark-{cell_id}-{os.getpid()}.txt")
        if os.path.exists(mark):
            os.remove(mark)
        env = dict(os.environ, MBDSDR_AV_MARK=mark)
        cmd = [sys.executable, os.path.abspath(__file__), "--child", "--cell", cell_id]
        t_run = time.time()
        try:
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace", env=env)
            try:
                _out, err = proc.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                proc.kill()
                _out, err = proc.communicate()
            code = proc.returncode
            tail = (err or "")[-300:]
        except Exception as exc:  # noqa: BLE001
            code, tail = 1, repr(exc)
        stages = []
        if os.path.exists(mark):
            with open(mark, "r", encoding="utf-8") as f:
                stages = [ln.strip() for ln in f if ln.strip()]
            os.remove(mark)
        codes.append(code)
        last_marks.append(stages[-1] if stages else "（无标记）")
        sigs.append("—" if code in (0, 7) else _wer_sig(proc.pid, t_run))
        if tail.strip() and "SoapySDR" not in tail and "pkg_resources" not in tail:
            print(f"    [stderr] {tail.strip()[:200]}", flush=True)
    t1 = time.time()
    return {"cell": cell_id, "cfg": cfg, "codes": codes, "sigs": sigs,
            "last": last_marks, "t0": t0, "t1": t1,
            "crashes": sum(1 for c in codes if c not in (0, 7))}


def _yn(v) -> str:
    return "是" if v else "否"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--cell", choices=sorted(CELLS))
    ap.add_argument("--cells", default="A,B,C,D,E,F,G,H,I,J")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    os.makedirs(TMP_ROOT, exist_ok=True)

    if args.child:
        run_child(args.cell)
        return 0

    print(f"repo   : {ROOT}")
    print(f"python : {sys.version.split()[0]}")
    print(f"格子   : {args.cells}（每格 {args.runs} 次）\n")

    rows = []
    for cid in [c.strip() for c in args.cells.split(",") if c.strip()]:
        print(f"[cell] {cid} {CELLS[cid]}", flush=True)
        rows.append(run_cell(cid, args.runs))
        print(f"    -> {','.join(_label(c) for c in rows[-1]['codes'])}"
              f"  崩 {rows[-1]['crashes']}/{args.runs}", flush=True)

    print("\n" + "=" * 132)
    print("%-5s %-11s %-9s %-5s %-10s %-6s %-6s  %-26s %-8s %-28s %s" % (
        "格", "平台", "show", "循环", "退出", "字体", "删档", "退出码", "崩/总",
        "WER 签名(按 PID)", "最后标记"))
    print("=" * 132)
    for r in rows:
        c = r["cfg"]
        uniq = []
        for s in r["sigs"]:
            if s != "—" and s not in uniq:
                uniq.append(s)
        print("%-5s %-11s %-9s %-5s %-10s %-6s %-6s  %-26s %-8s %-28s %s" % (
            r["cell"], c["platform"], c["show"], c["loop"], c["exit"],
            _yn(c["font"] == "yes"), _yn(c["rmtree"] == "yes"),
            ",".join(_label(x) for x in r["codes"]),
            f"{r['crashes']}/{len(r['codes'])}",
            "; ".join(uniq) if uniq else "—", r["last"][0]))
    print("=" * 132)
    print("口径：0/5 只表示本机该格未复现，不反推其它格。最后标记 = "
          "崩溃前最后一个 fsync 落盘的阶段。WER 按子进程 PID 1:1 匹配。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
