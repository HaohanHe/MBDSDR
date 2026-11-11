# SPDX-License-Identifier: MIT
"""退出路径对照探针（只取证，不改产品代码、不改 tests/conftest.py）。

为什么要有这个文件
------------------
`docs/research/2026-11-11-settings-convergence.md` §6 曾写过「独立进程仍以
0xC0000005 退出」，但当时脚本留在临时目录、事后无可指认 —— 结论因此被降级。
本文件把探针钉进仓库，任何一次「退出码」断言都必须能从这里复跑出来。

三种变体，命令行如下
--------------------
1) bare —— 产品同源退出路径：构造 MainWindow → app.exec() → 定时器里
   app.quit() → 返回 → 解释器正常收尾（Py_FinalizeEx）。

       python scripts/repro/2026-11-11-exit-path-probe.py bare

2) hard —— 硬退出：构造 MainWindow → show() → app.exec() 已在跑 →
   另起 daemon 线程 sleep 1.5s 后 os._exit(0)。
   **注意**：这条**确实构造了 MainWindow，也**确实跑进了 app.exec()**；
   不是「不建窗口 / 未进事件循环」的裸硬退出。`os._exit` 由**看门狗
   daemon 线程**调用，不是主线程、不是 Qt 回调里调。

       python scripts/repro/2026-11-11-exit-path-probe.py hard

3) pytest —— 会话退出（带 tests/conftest.py 的 Windows 规避）：

       python -m pytest tests/test_settings_search.py -q

平台插件
--------
脚本用 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`：默认 offscreen。

  * 默认跑法 → 平台插件 `offscreen`
  * 要真实窗口必须**显式** `$env:QT_QPA_PLATFORM='windows'` 再跑。
  * **踩坑**：PowerShell 里 `$env:QT_QPA_PLATFORM=''` 等于删除该变量，脚本的
    `setdefault` 会把 offscreen 设回去 —— 看起来「跑了真实平台」，实际还是
    offscreen。要核对请以脚本打印的 `PLATFORM_PLUGIN` 为准。

实测记录（2026-11-11，本机，配置目录指向临时目录）
--------------------------------------------------
| 变体 | 平台插件 | MainWindow 构造 | app.exec() 在跑 | os._exit 调用线程 | 退出码 |
|---|---|---|---|---|---|
| bare | offscreen | 是 | 是（定时器 quit） | —（不走硬退） | 0（2/2） |
| hard | offscreen | 是 | 是 | daemon 看门狗线程 | 0（3/3） |
| hard | windows   | 是 | 是 | daemon 看门狗线程 | 0（2/2） |
| pytest | offscreen | 用例内各自构造 | 用法例自己的 | — | 0 |

每次运行的判定依据（脚本会打印）：`PLATFORM_PLUGIN` / `MAINWINDOW_ALIVE` /
`HARD_EXIT from_thread=... main_thread=False` / `APP_EXEC_RET`（硬退出变体
不会出现 `APP_EXEC_RET`，因为进程在 `app.exec()` 返回前就被终止）。

仅陈述事实，不下「谁对谁错」的结论：另一条线报的 `os._exit(0)` 5/5 复现，
与上述矩阵的差异最可能来自「有没有 MainWindow 活着 / app.exec() 是否在跑 /
平台插件 / os._exit 从哪个线程调」，请按矩阵逐项对齐后再判定。
"""
import os
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

# 配置目录全部指向临时目录：探针不许碰用户真实配置。
_tmp = tempfile.mkdtemp(prefix="exit_probe_")
os.environ["APPDATA"] = _tmp
os.environ["USERPROFILE"] = _tmp

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

mode = (sys.argv[1] if len(sys.argv) > 1 else "bare").strip().lower()

app = QApplication.instance() or QApplication(sys.argv)
print(f"PLATFORM_PLUGIN {app.platformName()}", flush=True)

from main_window import MainWindow  # noqa: E402

w = MainWindow()
w.show()
print("MAINWINDOW_ALIVE True", flush=True)

if mode == "hard":
    def _hard_exit():
        time.sleep(1.5)
        print("HARD_EXIT from_thread="
              f"{threading.current_thread().name} "
              f"main_thread={threading.current_thread() is threading.main_thread()}",
              flush=True)
        os._exit(0)

    threading.Thread(target=_hard_exit, name="probe-watchdog",
                     daemon=True).start()
else:
    def _normal_quit():
        print("NORMAL_QUIT", flush=True)
        app.quit()

    QTimer.singleShot(1500, _normal_quit)

rc = app.exec()
print(f"APP_EXEC_RET {rc}", flush=True)
# 不显式 os._exit：让解释器走正常收尾（Py_FinalizeEx），这才是产品退出路径。
