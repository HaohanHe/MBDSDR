#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
MBDSDR 桌面端入口
==================
AI 定义无线电全栈开源平台 - 桌面客户端。

用法:
  python3 main.py
  python3 main.py --host 192.168.4.1 --port 81  # 连接真实硬件

依赖:
  pip install PySide6 numpy websocket-client websockets
"""

import sys
import os
import argparse
import queue
import threading

_here = os.path.dirname(os.path.abspath(__file__))
if _here not in sys.path:
    sys.path.insert(0, _here)

from startup_paths import (  # noqa: E402  (先修 sys.path 再导入同目录模块)
    append_startup_log as _append_startup_log,
    buffered_log as _buffered_log,
    persistent_log_note as _persistent_log_note,
    probe_dir_writable_async,
    program_dir as _program_dir,
    runtime_dll_report,
    startup_log_path as _startup_log_path,
)

# 确保能导入同目录和上层目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.version import __version__ as _app_version  # noqa: E402

_STARTUP_LOG_NAME = "mbdsdr_startup.log"

#: 缺硬依赖时给出的下一步命令（模块名 → pip 包名）。
_PIP_HINTS = {
    "PySide6": "pip install PySide6",
    "numpy": "pip install numpy",
    "scipy": "pip install scipy",
    "sgp4": "pip install sgp4",
    "skyfield": "pip install skyfield",
    "pymap3d": "pip install pymap3d",
    "serial": "pip install pyserial",
    "requests": "pip install requests",
    "websockets": "pip install websockets",
    "websocket": "pip install websocket-client",
    "PIL": "pip install Pillow",
    "sounddevice": "pip install sounddevice",
    "astropy": "pip install astropy",
}


def _show_native_message(title: str, text: str) -> None:
    """不依赖 Qt 的 Windows 弹窗：Qt 没起来时也能把原因说清。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10 | 0x10000)
        return
    except (AttributeError, OSError):
        pass
    print(f"{title}: {text}", file=sys.stderr)


#: 运行时按需 / 动态导入、PyInstaller 静态分析容易漏掉的私有模块。
#: 启动自检逐个 import 一遍，结果写日志（不阻塞启动，只记录）。
_RISKY_IMPORTS = [
    "scipy._external.array_api_compat.numpy.fft",
    "scipy._external.array_api_compat.numpy.linalg",
    "scipy._external.array_api_compat.common",
    "scipy._external.array_api_extra",
    "scipy._external.packaging_version",
    "scipy._lib._array_api",
    "scipy._lib._util",
    "scipy.signal",
    "scipy.special",
    "scipy.fft",
    "scipy.interpolate",
    "scipy.optimize",
    "numpy._core._multiarray_umath",
]

#: 私有子树 → 兄弟模块探针。用来区分「整棵子树没打进包」还是「只缺单个文件」。
_PACKAGING_SIBLINGS = {
    "scipy._external": (
        "scipy._external.array_api_compat.common",
        "scipy._external.array_api_extra",
        "scipy._external.packaging_version",
    ),
    "scipy._lib": (
        "scipy._lib._array_api",
        "scipy._lib._util",
        "scipy._lib.deprecation",
    ),
    "numpy._core": (
        "numpy._core.multiarray",
        "numpy._core._multiarray_umath",
    ),
    "numpy": (
        "numpy.fft",
        "numpy.linalg",
    ),
}


def _probe_import(name: str) -> bool:
    """只判断能不能导入；缺件之外的真实错误不在这里吞掉。"""
    import importlib
    try:
        importlib.import_module(name)
        return True
    except ImportError:
        return False


def _module_exists_without_import(name: str):
    """只查目录：确定找到返回 True；认不准（zip/PYZ、命名空间父包）返回 None 交兜底。

    ``importlib.util.find_spec("scipy.signal")`` 会先把 scipy 父包 import 进来，
    4 项合计实测 287.6–335.3 ms；这条路径只做目录查找，4 项合计 3.0–4.9 ms
    （docs/perf-evidence/2026-11-12/find-spec-pyc.json）。
    找不到时不硬判缺失——可能是 zip/PYZ 之类不按目录组织的装载器，交给标准
    ``find_spec`` 复核：宁可多花一次钱，也不能在产物上误报缺件、误中止启动。
    """
    from importlib.machinery import PathFinder
    parts = name.split(".")
    try:
        spec = PathFinder.find_spec(parts[0])
        if spec is None or len(parts) == 1:
            return True if spec is not None else None
        dirs = list(getattr(spec, "submodule_search_locations", None) or [])
        if not dirs:
            return None
        for i in range(1, len(parts)):
            part = parts[i]
            try:
                spec = PathFinder.find_spec(".".join(parts[:i + 1]), dirs)
            except KeyError:
                # 命名空间包（如 scipy._external）没有 __init__，父包未 import 时
                # 它的 _NamespacePath 取不到父模块 → 直接按目录判定
                spec = None
            if spec is None:
                ns = [os.path.join(d, part) for d in dirs
                      if os.path.isdir(os.path.join(d, part))]
                if not ns:
                    return None
                if i == len(parts) - 1:
                    return True
                dirs = ns
                continue
            if i == len(parts) - 1:
                return True
            if spec.loader is None:
                # 命名空间包：PathFinder 给的是 _NamespacePath，父包未 import 时
                # 连 list() 都会抛 KeyError，直接按「父目录/本段名」拼
                dirs = [os.path.join(d, part) for d in dirs
                        if os.path.isdir(os.path.join(d, part))]
            else:
                locs = getattr(spec, "submodule_search_locations", None)
                dirs = (list(locs) if locs is not None
                        else [os.path.dirname(spec.origin or "")])
            if not dirs:
                return None
    except (ImportError, AttributeError, ValueError, KeyError):
        return None
    return True


def _probe_module_spec(name: str) -> bool:
    """只判断「文件在不在」：不执行模块体、不加载 .pyd（≈0 ms vs 真 import）。

    用于 scipy 系：「缺件/没打进去」是启动前必须拦住的那一类，find_spec 足够；
    「文件在但加载失败」留给首帧后的延后真 import 报（见 _start_deferred_imports）。

    能看见只留 ``.pyc`` 的子树：``compileall -b`` 落盘后 ``find_spec`` 给的是
    ``SourcelessFileLoader``（实测见 docs/perf-evidence/2026-11-12/find-spec-pyc.json），
    只有 ``__pycache__/*.pyc`` 而没有 legacy ``.pyc`` 的形态 Python 3 本来就导入不了，
    判缺件是对的。
    """
    if _module_exists_without_import(name):
        return True
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


#: 启动自检里**必须真 import** 的必需项（模块, 人话名称, 缺了会怎样, 怎么修）。
#: 只列「缺了应用就不能正常工作」且「只有真加载才能判定」的项：numpy 的
#: 二进制扩展和 PySide6 的动态库，文件在但 DLL 加载失败时 find_spec 看不出来。
#: scipy 系放到 _SPEC_FIRST_DEPENDENCIES（先查存在、真加载延后到首帧之后）。
_REQUIRED_DEPENDENCIES = [
    ("numpy", "NumPy 数值运算库",
     "所有 DSP / 频谱 / 解调计算都不可用",
     "pip install numpy（打包版请重新解压完整安装包）"),
    ("numpy._core._multiarray_umath", "NumPy 核心二进制（_multiarray_umath）",
     "读取 IQ 样点或做 FFT 时直接崩溃",
     "重装完整的 numpy wheel；打包版重新解压安装包"),
    ("PySide6.QtCore", "PySide6 核心二进制", "界面完全无法创建",
     "pip install PySide6"),
    ("PySide6.QtGui", "PySide6 GUI 二进制", "界面完全无法创建",
     "pip install PySide6"),
    ("PySide6.QtWidgets", "PySide6 控件二进制", "界面完全无法创建",
     "pip install PySide6"),
]

#: scipy 系必需项：启动前只做存在性检查（find_spec），真 import 延后到首帧之后。
#: 依据（2026-11-12 实测，docs/perf-evidence/2026-11-12/import-smoke-verdict.json）：
#: 本进程第一次 import scipy 实测 1030–1258 ms，其中 scipy.signal 单独 ≈750 ms；
#: 压在首帧前等于让用户对着 splash 多等一秒，而缺件判定的信息量完全由
#: 「文件在不在」提供。打包版把 scipy 私有子树以 .pyc 落盘（MBDSDR_NOARCHIVE=1），
#: 删掉 ``_internal/scipy/_external`` 时 find_spec 一样返回 None，所以仍能拦住。
#: 局限：find_spec 不能发现「文件在但 .pyd 加载失败」，这一类由首帧后的
#: 延后真 import 报出来（不阻塞，见 _report_deferred_import_failure）。
_SPEC_FIRST_DEPENDENCIES = [
    ("scipy", "SciPy 科学计算库",
     "滤波 / 重采样 / 频谱整条链路不可用",
     "pip install scipy（打包版请重新解压完整安装包）"),
    ("scipy.signal", "SciPy 信号处理子包",
     "滤波器、解调链、重采样不可用",
     "重装 scipy（pip install --force-reinstall scipy）"),
    ("scipy._external.array_api_compat.common",
     "SciPy 私有子树 _external（完整性探针）",
     "运行期懒加载 scipy 公共子模块时报 ModuleNotFoundError",
     "安装包解压不完整：重新解压/重装完整包，不要删 _internal 里的文件"),
    ("scipy._external.array_api_compat.numpy.fft",
     "SciPy array API 兼容层 numpy.fft（完整性探针）",
     "FFT / 频谱计算在运行期懒加载时报 ModuleNotFoundError",
     "安装包解压不完整：重新解压/重装完整包，不要删 _internal 里的文件"),
]

#: 必需数据件（相对仓库/安装包的路径, 人话名称, 缺了会怎样, 怎么修）。
_REQUIRED_DATA_FILES = [
    ("mbdsdr_ai/data/dso_catalog.json", "深空目标目录 dso_catalog.json",
     "「深空」页没有可选目标，DSO 观测计划不可用",
     "重新解压完整安装包（数据文件随包分发，不能只拷 exe）"),
]

#: 可选依赖：缺失只写日志，不拦启动（缺了只关掉对应功能，不弹窗）。
_OPTIONAL_DEPENDENCIES = [
    ("sgp4", "卫星过境预测（SGP4）", "卫星跟踪/过境列表不可用"),
    ("skyfield", "卫星星历（skyfield）", "卫星跟踪/过境列表不可用"),
    ("pymap3d", "坐标换算（pymap3d）", "观测站坐标/方位角换算不可用"),
    ("serial", "串口设备（pyserial）", "串口/Arduino 类硬件不可用"),
    ("sounddevice", "音频播放（sounddevice）", "无法出声；界面与频谱不受影响"),
    ("PIL", "图像处理（Pillow）", "部分图像资源/导出不可用"),
    ("requests", "HTTP 请求（requests）", "在线更新/下载不可用"),
    ("websockets", "WebSocket 客户端", "MCP/WebSocket 连接不可用"),
]


def _required_data_path(rel: str) -> str:
    """必需数据件的候选位置：打包 _MEIPASS 优先，其次源码仓库根目录。"""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, rel))
    candidates.append(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel))
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[-1]


def _startup_dependency_problems() -> list[str]:
    """启动自检：返回必需项缺失清单（空列表 = 可正常启动）。

    必需项缺失 → 调用方中止启动并弹「MBDSDR 启动失败 + 缺什么 + 怎么修」，
    绝不照常开一个半残窗口；可选依赖缺失只写日志。
    """
    problems: list[str] = []
    for module, name, consequence, fix in _REQUIRED_DEPENDENCIES:
        if not _probe_import(module):
            problems.append(
                f"• {name}（{module}）\n"
                f"  缺了会怎样：{consequence}\n"
                f"  怎么修：{fix}")
    for module, name, consequence, fix in _SPEC_FIRST_DEPENDENCIES:
        if not _probe_module_spec(module):
            problems.append(
                f"• {name}（{module}）\n"
                f"  缺了会怎样：{consequence}\n"
                f"  怎么修：{fix}")
    for rel, name, consequence, fix in _REQUIRED_DATA_FILES:
        if not os.path.exists(_required_data_path(rel)):
            problems.append(
                f"• {name}（{rel}）\n"
                f"  缺了会怎样：{consequence}\n"
                f"  怎么修：{fix}")

    missing_optional = [
        f"{name}（{module}）：{consequence}"
        for module, name, consequence in _OPTIONAL_DEPENDENCIES
        if not _probe_import(module)
    ]
    if missing_optional:
        line = "[selfcheck] 可选依赖缺失（不拦启动）：" + "；".join(missing_optional)
        _append_startup_log(line)
        print(line, file=sys.stderr)
    return problems



def _packaging_verdict(missing: str) -> "tuple[str, str]":
    """返回 (判定, 子树)：defect=整棵子树缺失；incomplete=只缺单文件；unknown=判不了。"""
    for subtree, siblings in _PACKAGING_SIBLINGS.items():
        if missing == subtree or missing.startswith(subtree + "."):
            if any(_probe_import(name) for name in siblings):
                return "incomplete", subtree
            return "defect", subtree
    return "unknown", ""


def _deferred_import_plan() -> list[str]:
    """首帧之后要真 import 的 scipy 私有子模块（沿用冒烟清单，顺序即依赖顺序）。"""
    return list(_RISKY_IMPORTS)


#: 延后导入失败时给用户的「缺了会怎样 / 怎么修」（与硬门槛同一套三段式）。
_DEFERRED_CONSEQUENCE = (
    "这些 scipy 私有子模块只在运行期被懒加载时才用到；"
    "它们缺失/加载失败时，频谱、滤波、统计等相关功能会在使用的那一刻报错")
_DEFERRED_FIX = (
    "重新解压或重装完整安装包，不要删 _internal 下的文件；"
    "源码环境执行 pip install --force-reinstall scipy")


def _deferred_import_worker(plan: list[str], results) -> None:
    """工作线程里一次只 import 一个模块（GIL 每 5 ms 让出，GUI 线程不被长事务卡死）。"""
    import importlib
    for module in plan:
        try:
            importlib.import_module(module)
            results.put((module, None))
        except Exception as exc:  # noqa: BLE001 - 加载失败要如实报出，不能崩
            results.put((module, f"{type(exc).__name__}: {exc}"))


def _report_deferred_import_failure(window, module: str, detail: str) -> None:
    """首帧后加载失败：窗口已在，用**不阻塞**的提示 + 日志，文案沿用硬门槛那套。"""
    text = (f"[selfcheck] 延后导入失败：{module}\n"
            f"  缺了会怎样：{_DEFERRED_CONSEQUENCE}\n"
            f"  怎么修：{_DEFERRED_FIX}\n"
            f"  详情：{detail}")
    _append_startup_log(text.replace("\n", " "))
    print(text, file=sys.stderr)
    log_path = _startup_log_path()
    message = (f"{module} 加载失败，相关功能不可用；"
               f"详见启动日志（{log_path or '控制台'}）")
    try:
        # 60 s 足够用户读完；不阻塞、不弹窗，界面照用（缺的只是懒加载相关功能）。
        window.statusBar().showMessage(message, 60000)
    except (AttributeError, RuntimeError):
        pass


def _start_deferred_imports(window) -> None:
    """首帧之后再把 scipy 私有子树真 import（本进程首次导入实测 1030–1258 ms）。

    起跑点必须钉在**主窗首个 Paint 派发之后**，不能是 show() 之后：实测
    show() 之后立刻起工作线程，线程的 import 会与首帧抢 GIL，把首帧推后
    ≈1 s（2026-11-12 反证，scripts/repro/2026-11-12-deferred-import-verify.py）。
    收结果用主线程定时器——一次一个模块，任何一片失败都只降级到状态栏 + 日志，
    不拦启动、不崩。无头/未收到 Paint 的环境用 3 s 兜底。
    """
    results = queue.Queue()
    plan = _deferred_import_plan()
    state = {"done": 0, "failed": [], "started": False}
    from PySide6.QtCore import QEvent, QObject, QTimer

    timer = QTimer(window)
    timer.setInterval(120)

    def drain() -> None:
        while True:
            try:
                module, detail = results.get_nowait()
            except queue.Empty:
                break
            state["done"] += 1
            if detail is not None:
                state["failed"].append(module)
                _report_deferred_import_failure(window, module, detail)
        if state["done"] >= len(plan):
            timer.stop()
            ok = len(plan) - len(state["failed"])
            line = (f"[smoke] 私有模块导入自检 {ok}/{len(plan)} 通过"
                    "（首帧后延后导入）")
            if state["failed"]:
                line += "；失败：" + ", ".join(state["failed"])
            _append_startup_log(line)
            print(line, file=sys.stderr)

    timer.timeout.connect(drain)

    def start() -> None:
        if state["started"]:
            return
        state["started"] = True
        thread = threading.Thread(
            target=_deferred_import_worker, args=(plan, results),
            name="mbdsdr-deferred-import", daemon=True)
        window._deferred_import_thread = thread
        timer.start()
        thread.start()

    class _FirstPaintGate(QObject):
        """只负责一件事：主窗第一次 Paint 派发完之后才放开延后导入。

        事件过滤器是在事件**交给控件处理之前**被调用的，直接 start() 会让
        工作线程抢在这次 Paint 真正绘制之前拿到 GIL，把首帧推后（实测 T2 从
        2.32 s 涨到 2.44 s）。投递到下一轮事件循环，等这次 Paint 走完再起。
        """

        def eventFilter(self, obj, event):      # noqa: N802 (Qt 命名)
            if event.type() == QEvent.Paint:
                obj.removeEventFilter(self)
                QTimer.singleShot(0, start)
            return False

    gate = _FirstPaintGate(window)
    try:
        window.installEventFilter(gate)
    except (AttributeError, RuntimeError):
        start()                                  # 无事件过滤能力的假窗口
    # 过滤器/定时器都要有引用活到起跑；挂在窗口上随窗口销毁。
    window._deferred_import_gate = gate
    window._deferred_import_timer = timer
    QTimer.singleShot(3000, start)               # 没等到 Paint 的兜底


def _run_import_probe(spec: str) -> int:
    """产物解释器内导入自检：``--import-probe A,B`` 逐个 import 并如实报结果。

    发布清单用它给「产物里有、requirements 没声明」的第三方包补运行时证据：
    静态 import 链只能说明 PyInstaller 为什么收集它，不能说明产物里真的能用。
    失败返回 2（成功 0）。**洁净约束**：不建窗口、不初始化 Qt、不写任何文件
    或配置（包括启动日志），只在 stdout/stderr 输出结果。无控制台且没有继承
    管道时两者都是 None，此时只返回退出码、不输出（双击运行才会遇到的边界）。
    """
    import importlib

    names = [n.strip() for n in spec.split(",") if n.strip()]
    stream = sys.stdout if sys.stdout is not None else sys.stderr
    failed: list[str] = []
    for name in names:
        try:
            importlib.import_module(name)
            line = f"[probe] OK {name}"
        except Exception as exc:  # noqa: BLE001 - 任何导入失败都要如实报出
            failed.append(name)
            line = f"[probe] FAIL {name}: {type(exc).__name__}: {exc}"
        if stream is not None:
            print(line, file=stream, flush=True)
    return 0 if not failed else 2


def _run_resource_probe() -> int:
    """产物解释器内的运行期资源自检：``--resource-probe``。

    清单对拍（``scripts/check_dist_head_parity.py``）只证明「文件在产物里」，
    不证明「冻结后按 ``__file__`` 相对路径读得到」。这里逐类真读一次：

    * 版本：``version.py`` 按 ``__file__`` 相对读 ``mbdsdr_ai/VERSION``；
    * 语言：i18n 加载 ``locale/en.json``，同一个键打印中文/英文两份文案
      （换 locale 文案必须变，否则是文件没进包）；
    * 数据：``data/dso_catalog.json`` 解析成条目、``data/moon_albedo_*.npz``
      能打开；
    * 图标：``assets/icon.ico`` 存在且非空。

    任一类失败返回 2。洁净约束同 ``--import-probe``：不建窗口、不初始化
    Qt、不写任何文件/配置，只输出到 stdout/stderr。
    """
    from pathlib import Path

    stream = sys.stdout if sys.stdout is not None else sys.stderr
    failed: list[str] = []

    def report(ok: bool, line: str) -> None:
        if not ok:
            failed.append(line)
        if stream is not None:
            print(("[probe] OK " if ok else "[probe] FAIL ") + line,
                  file=stream, flush=True)

    try:
        from mbdsdr_ai.version import __version__ as ver
        report(bool(ver), f"version {ver}")
    except Exception as exc:  # noqa: BLE001 - 自检要把失败如实报出
        report(False, f"version: {type(exc).__name__}: {exc}")

    try:
        from mbdsdr_ai import i18n
        zh = i18n.tr("tool.nav_bit_sync.description", lang="zh")
        en = i18n.tr("tool.nav_bit_sync.description", lang="en")
        report(bool(en) and en != zh,
               f"locale en={en[:48]!r} zh={zh[:48]!r}")
    except Exception as exc:  # noqa: BLE001
        report(False, f"locale: {type(exc).__name__}: {exc}")

    try:
        from mbdsdr_ai.astronomy.dso import load_dso_catalog
        count = len(load_dso_catalog())
        report(count > 0, f"data dso_catalog entries={count}")
    except Exception as exc:  # noqa: BLE001
        report(False, f"data dso_catalog: {type(exc).__name__}: {exc}")

    try:
        import numpy as np
        from mbdsdr_ai.astronomy.moon_albedo import DATA_PATH
        with np.load(DATA_PATH, allow_pickle=False) as npz:
            report(len(npz.files) > 0, f"data moon_albedo keys={len(npz.files)}")
    except Exception as exc:  # noqa: BLE001
        report(False, f"data moon_albedo: {type(exc).__name__}: {exc}")

    try:
        base = Path(getattr(sys, "_MEIPASS", None)
                    or Path(__file__).resolve().parent)
        icon = base / "assets" / "icon.ico"
        report(icon.is_file() and icon.stat().st_size > 0, f"assets icon.ico {icon}")
    except Exception as exc:  # noqa: BLE001
        report(False, f"assets: {type(exc).__name__}: {exc}")

    return 0 if not failed else 2


def _import_probe_spec(argv: list[str]) -> str | None:
    """只识别 ``--import-probe``（空格/等号两种写法）；值缺失/为空返回 None，
    交给 argparse 按普通非法参数报错。其余开关一律不看，保持既有入口不变。"""
    for i, arg in enumerate(argv):
        if arg == "--import-probe":
            value = argv[i + 1] if i + 1 < len(argv) else ""
            return value if value and not value.startswith("-") else None
        if arg.startswith("--import-probe="):
            value = arg.split("=", 1)[1]
            return value or None
    return None


def _import_error_text(exc: BaseException) -> str:
    """把导入失败翻译成「缺什么 + 是打包缺陷还是解压不全 + 怎么补」。"""
    detail = str(exc).strip() or exc.__class__.__name__
    missing = getattr(exc, "name", None) or ""
    if not missing and "No module named" in detail and "'" in detail:
        missing = detail.split("'")[1]
    lines = [detail]
    if getattr(sys, "frozen", False):
        verdict, subtree = _packaging_verdict(missing) if missing else ("unknown", "")
        if verdict == "defect":
            lines.extend([
                "这是打包缺陷，不是你的操作问题：",
                f"  安装包里整棵 {subtree} 子树都没打进去"
                "（同一子包里的兄弟模块也导入失败）。",
                "请获取最新构建版本；如果这就是最新版，请把日志发给维护者。",
            ])
        elif verdict == "incomplete":
            lines.extend([
                "安装包解压不完整：",
                f"  只缺 {missing}，同包其它模块可以正常导入。",
                "请整包重新解压/重新安装（不要只拷贝 exe，也不要删 _internal 里的文件）。",
            ])
        else:
            lines.append(
                "安装包不完整或缺少 Microsoft VC++ 运行库。"
                "请整包重新解压；仍失败则安装 VC++ 2015-2022 x64 运行库。")
    else:
        hint = _PIP_HINTS.get(missing) or _PIP_HINTS.get(missing.split(".")[0])
        if hint:
            lines.append(f"修复：{hint}")
        lines.append("修复：pip install -r requirements.txt")
    return "\n".join(lines)


def _report_startup_failure(summary: str, detail: str) -> None:
    """启动失败统一出口：写日志 + 原生弹窗，任何情况下都不静默退出。"""
    log_path = _append_startup_log(
        f"[{__import__('datetime').datetime.now().isoformat(timespec='seconds')}] "
        f"{summary}\n{detail}\n")
    body = f"{summary}\n\n{detail}"
    if log_path:
        body += f"\n\n日志：{log_path}"
    else:
        # 落点全部不可写（无非 C 盘、安装根只读、ProgramData 也写不进）：
        # 唯一副本在内存缓冲里，必须如实告诉用户，不能假装日志已落盘。
        body += "\n\n" + _persistent_log_note()
        tail = _buffered_log()[-6:]
        if tail:
            body += "\n\n本次会话日志（未持久化）：\n" + "\n".join(tail)
    _show_native_message("MBDSDR 启动失败", body)


def _qt_platform_plugin_problem() -> str | None:
    """QApplication 之前检查 platforms 插件，避免 Qt 自己 abort 成裸弹窗。"""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "PySide6", "plugins", "platforms"))
        candidates.append(os.path.join(meipass, "platforms"))
    try:
        import PySide6 as _pyside
        candidates.append(os.path.join(
            os.path.dirname(_pyside.__file__), "plugins", "platforms"))
    except ImportError:
        pass
    for var in ("QT_QPA_PLATFORM_PLUGIN_PATH", "QT_PLUGIN_PATH"):
        value = (os.environ.get(var) or "").strip()
        if value:
            candidates.append(value)
            candidates.append(os.path.join(value, "platforms"))
    # 只认「这次真正要用的平台」：默认 windows 就必须有 qwindows.dll；
    # 不能因为包里还躺着 qoffscreen/qminimal 就放行（Qt 仍会 abort 成英文弹窗）。
    requested = (os.environ.get("QT_QPA_PLATFORM") or "windows").strip().lower()
    requested = requested.split(":", 1)[0] or "windows"
    plugin_name = "qwindows.dll" if requested in ("windows", "win32") \
        else f"q{requested}.dll"
    for directory in candidates:
        try:
            names = {name.lower() for name in os.listdir(directory)}
        except OSError:
            continue
        if plugin_name in names:
            return None
    return (f"缺少 Qt 平台插件 platforms/{plugin_name}（当前平台 {requested}）。\n"
            "常见原因：安装包没解压完整，或只拷贝了 MBDSDR.exe。\n"
            "修复：重新解压完整安装包，_internal 目录必须与 exe 放在一起。")


def _ensure_stdio_for_pythonw():
    """pythonw.exe / 打包版启动时没有控制台，sys.stdout/sys.stderr 会是 None。

    此时任何 print() 落到 None 都会在启动阶段就抛 AttributeError 直接崩。
    这里在最早时机把二者补回来：落点与启动自检共用 :func:`_startup_log_path`
    的策略（显式环境变量 > 非 C 盘 > 程序目录 logs > %PROGRAMDATA%），
    **不写 C 盘用户目录**（~/.config、%APPDATA%、%LOCALAPPDATA%、%TEMP% 全部禁用）；
    连一个可写落点都没有时退到 os.devnull（黑洞），保证任何 print 都不崩。
    正常 python3 运行时 stdout/stderr 都不是 None，本函数直接返回、零副作用。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    def _open_log():
        try:
            path = _startup_log_path()
        except OSError:
            path = ""
        if path:
            try:
                return open(path, "a", encoding="utf-8", errors="replace")
            except OSError:
                pass
        # 日志落点都不可用 → 黑洞兜底
        return open(os.devnull, "w", encoding="utf-8", errors="replace")

    if sys.stdout is None:
        sys.stdout = _open_log()
    if sys.stderr is None:
        sys.stderr = sys.stdout


# 探针入口必须在任何日志/Qt/界面模块初始化之前判掉：它只做 import 并退出。
# 放在 _ensure_stdio_for_pythonw() 之前，保证「不写日志文件」；放在 PySide6 /
# themes / main_window 导入之前，保证「不初始化 Qt」。
if "--resource-probe" in sys.argv[1:]:
    raise SystemExit(_run_resource_probe())

_probe_spec = _import_probe_spec(sys.argv[1:])
if _probe_spec is not None:
    raise SystemExit(_run_import_probe(_probe_spec))

# 打包版是 GUI 子系统（无控制台）：导入失败如果放任抛 traceback，用户只会
# 看到一个 "Unhandled exception in script" 英文弹窗，或干脆静默退出。
# 这里统一截获，翻译成「缺哪个模块 + 怎么补」，并写日志。
_ensure_stdio_for_pythonw()

try:
    from PySide6.QtWidgets import QApplication, QSplashScreen
    from PySide6.QtCore import Qt, QObject, Signal
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

    from themes import get_theme, DEFAULT_THEME, get_app_font
except ImportError as _startup_exc:
    # 缺件只会是 ImportError / ModuleNotFoundError；其它异常照常向上抛，
    # 不在这里把真实 bug 伪装成「安装包坏了」。
    _first_line = (str(_startup_exc).strip().splitlines() or [""])[0]
    _summary = "无法加载界面依赖：" + (
        _first_line or _startup_exc.__class__.__name__)
    _report_startup_failure(_summary, _import_error_text(_startup_exc))
    raise SystemExit(3)


def _report_runtime_dlls() -> None:
    """启动自检：运行期 DLL 存在性检查。

    打包版缺 ``_internal`` 里的 DLL 时，用户看到的是 ``0xC0000135`` / 窗口一闪，
    这里提前用中文说明「缺哪个运行库、怎么修」。
    """
    report = runtime_dll_report()
    missing = report["missing_packaged"]
    if missing:
        text = ("缺少运行库：" + "、".join(missing) + "\n\n"
                "这些 DLL 应随安装包放在 _internal 目录（或 exe 同目录）。\n"
                "请重新安装完整包，不要手工删 _internal 里的文件；"
                "若使用外部 SDR 驱动，也可安装对应驱动后重试；"
                "若杀毒软件隔离了它们，请恢复后重试。")
        _append_startup_log("[dll] 缺失：" + "、".join(missing))
        _show_native_message("MBDSDR 运行库缺失", text)
    optional = report["missing_optional"]
    if optional:
        line = ("[dll] 未发现 " + "、".join(optional) +
                "：SoapySDR 设备不可用；RTL-SDR 直连不受影响")
        _append_startup_log(line)
        print(line, file=sys.stderr)


def _report_corruption_evidence() -> None:
    """启动自检：只读清点历史损坏备份，结论只写启动日志（不碰界面）。

    覆盖书签 / FM 预设（默认 ``~/.mbdsdr``）、已知数据子目录
    （``evolution`` / ``guardian_snapshots`` / ``workflows``）与桌面配置
    所在目录。只报告数量，不删除任何文件；超过 30 天的备份提示用户手动清理。
    """
    from mbdsdr_ai.corrupt_evidence import (
        corruption_startup_report,
        default_scan_directories,
    )
    from settings import default_config_path

    corruption_startup_report(default_scan_directories(default_config_path()),
                              append_line=_append_startup_log)


class _UiNotifier(QObject):
    """把工作线程的探测结果切回 GUI 线程（statusBar 只能在主线程动）。"""

    message = Signal(str)

    def __init__(self, window):
        super().__init__(window)      # 父对象 = 主窗口 → 线程亲和性在 GUI 线程
        self._window = window
        self.message.connect(self._show)

    def _show(self, text: str) -> None:
        try:
            self._window.statusBar().showMessage(text, 20000)
        except AttributeError:
            print(text, file=sys.stderr)

    def notify(self, text: str) -> None:
        self.message.emit(text)


def _check_record_dir_async(window) -> None:
    """启动时不阻塞地试写录音目录；不可写时把原因送到状态栏。

    主线程不等探测结果（ACL deny-write 目录创建文件会卡数十秒，实测 >6s）。
    """
    settings_obj = getattr(window, "settings", None)
    record_dir = getattr(settings_obj, "record_dir", "")
    if not record_dir:
        return
    notifier = _UiNotifier(window)

    def _on_result(error: str) -> None:
        if error:
            notifier.notify(error)
            _append_startup_log("[record] " + error)

    probe_dir_writable_async(record_dir, _on_result)


def _install_sigint_graceful_quit(app):
    """把控制台 Ctrl+C（SIGINT）转成优雅退出，而不是抛进 Qt 回调里。

    默认行为是 KeyboardInterrupt 在某个 Python 回调（eventFilter/定时器槽）里
    抛出：PySide 只打印一行 "Error calling Python override ..."，事件循环继续跑，
    看上去 Ctrl+C 没反应；若恰好打断的是"退出"回调，退出动作也会被吃掉。
    这里注册自定义处理器，只置标志，由 0.2s 轮询的 QTimer 调 app.quit()，
    让中断不以异常形式越过任何 Qt/Python 边界。
    """
    import signal

    from PySide6.QtCore import QTimer

    pending = {"flag": False}

    def _on_sigint(signum, frame):
        pending["flag"] = True
        print("\n[mbdsdr] 收到 Ctrl+C，正在优雅退出…", file=sys.stderr)

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except (ValueError, OSError):
        return  # 非主线程/不支持信号：保持原行为

    timer = QTimer(app)
    timer.setInterval(200)

    def _poll():
        if pending["flag"]:
            timer.stop()
            app.quit()

    timer.timeout.connect(_poll)
    timer.start()
    app._sigint_graceful_timer = timer  # 保活：避免被回收后轮询消失


def _install_combo_wheel_guard(app) -> None:
    """下拉框滚轮守卫：没展开的下拉框不吃滚轮，滚轮改为滚动所在页面。

    Qt 默认「鼠标停在哪，滚轮就改哪个下拉框」——设置页里从「简体中文」
    轻轻一滚就跳到「日本語」，用户根本没打算改值（2026-11-10 实指）。
    这里只在**下拉列表已展开**或**控件已获得焦点**时放行滚轮；其余情况
    把事件转给最近的滚动区域，让页面自己滚。
    """
    from PySide6.QtCore import QEvent, QObject, QThread
    from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QComboBox

    class _ComboWheelGuard(QObject):
        def eventFilter(self, obj, event):  # noqa: N802 (Qt 命名)
            # 与主窗口同一个坑：app 级过滤器会被工作线程的事件循环调用，
            # 在非 GUI 线程碰 QWidget 会直接 access violation，必须放行。
            if QThread.currentThread() is not app.thread():
                return False
            if event.type() != QEvent.Wheel or not isinstance(obj, QComboBox):
                return False
            try:
                popup_open = obj.view().isVisible()
            except (AttributeError, RuntimeError):
                popup_open = False
            if popup_open or obj.hasFocus():
                return False
            scroll = obj.parentWidget()
            while scroll is not None and not isinstance(
                    scroll, QAbstractScrollArea):
                scroll = scroll.parentWidget()
            if scroll is not None:
                QApplication.sendEvent(scroll.viewport(), event)
            return True

    guard = _ComboWheelGuard(app)
    app.installEventFilter(guard)
    app._combo_wheel_guard = guard  # 保活：局部对象被回收过滤器就失效


def _show_startup_splash(app):
    """主窗口构造前先摆一块「正在加载」实底窗，别让用户对着空白桌面。

    实测冷启动从进程起到主窗口可见 4.4 s（外部计时 6.6–7.1 s，见
    docs/research/2026-11-11-gap-performance.md §2）：这段时间主窗口还不存在，
    屏幕上什么都没有，用户会以为程序没起来。splash 在 QApplication 建好后
    立刻 show，构造完由 ``finish(window)`` 收掉。

    只做遮挡，不改启动路径；任何绘制/平台异常都退回 None，绝不因此拦启动。
    """
    try:
        w, h = 460, 260
        pix = QPixmap(w, h)
        pix.fill(QColor("#0f1419"))
        painter = QPainter(pix)
        try:
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            base = QFont(get_app_font())
            title_font = QFont(base)
            title_font.setPointSize(22)
            title_font.setBold(True)
            painter.setFont(title_font)
            painter.setPen(QColor("#e6edf7"))
            painter.drawText(0, 84, w, 44, Qt.AlignHCenter | Qt.AlignVCenter,
                             "MBDSDR")
            sub_font = QFont(base)
            sub_font.setPointSize(10)
            painter.setFont(sub_font)
            painter.setPen(QColor("#8d9bb0"))
            painter.drawText(0, 132, w, 24, Qt.AlignHCenter | Qt.AlignVCenter,
                             "AI 定义无线电 · 正在加载界面…")
            painter.setPen(QColor("#5a6678"))
            painter.drawText(0, 206, w, 20, Qt.AlignHCenter | Qt.AlignVCenter,
                             "v" + str(_app_version))
        finally:
            painter.end()
        splash = QSplashScreen(pix)
        splash.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint
                              | Qt.WindowStaysOnTopHint)
        splash.show()
        # 先让 splash 真的落到屏幕上，再进 2.5–4 s 的窗口构造。
        app.processEvents()
        return splash
    except Exception as exc:  # noqa: BLE001 - 装饰窗失败不得拦启动
        print(f"[mbdsdr] splash 未能显示（不影响启动）: {exc}", file=sys.stderr)
        return None


def _close_splash(splash) -> None:
    """失败路径抢在弹窗前收掉 splash：别让「启动失败」压在加载画面上。"""
    if splash is None:
        return
    try:
        splash.close()
    except (AttributeError, RuntimeError):  # noqa: BLE001 - 收尾失败不拦失败提示
        pass


def _import_main_window(splash=None):
    """导入主窗口类；放在 splash 之后做（该模块导入本身实测 ~0.8 s）。

    打包版缺件的翻译口径与模块级导入完全一致：缺件给「缺什么 + 怎么修」，
    其它异常照常向上抛，不把真实 bug 伪装成安装包坏了。
    """
    try:
        from main_window import MainWindow
    except ImportError as exc:
        _close_splash(splash)
        first_line = (str(exc).strip().splitlines() or [""])[0]
        _report_startup_failure(
            "无法加载界面依赖：" + (first_line or exc.__class__.__name__),
            _import_error_text(exc))
        raise SystemExit(3)
    return MainWindow


def main():
    # pythonw.exe 无控制台：必须在任何 print / argparse --help 之前兜底 stdio
    _ensure_stdio_for_pythonw()

    parser = argparse.ArgumentParser(description="MBDSDR - AI 定义无线电桌面端")
    parser.add_argument("--host", type=str, default=None, help="设备 IP 地址")
    parser.add_argument("--port", type=int, default=None,
                        help="设备端口（缺省用 tokens.MCP 默认值，"
                             "环境变量 MBDSDR_MCP_PORT 可覆盖）")
    parser.add_argument("--theme", type=str, default=DEFAULT_THEME,
                        choices=["default", "dark", "high_contrast"],
                        help="主题")
    # --import-probe 已在模块级提前拦截（见文件上方），这里只保留帮助文本与
    # 参数校验；不要把它改回 main() 内处理，否则会重新走到 Qt/日志初始化。
    parser.add_argument("--import-probe", type=str, default=None, metavar="MOD1,MOD2",
                        help="只做导入自检（逗号分隔模块名），打印结果后退出，不建窗口")
    args = parser.parse_args()

    # 高 DPI 支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # 平台插件缺失时 Qt 自己会 abort（连 Python 异常都拿不到），必须前置检查。
    plugin_problem = _qt_platform_plugin_problem()
    if plugin_problem:
        _report_startup_failure("Qt 平台插件缺失，未能创建窗口", plugin_problem)
        return 3
    try:
        app = QApplication(sys.argv)
    except RuntimeError as qt_exc:
        _report_startup_failure("创建 QApplication 失败",
                                _import_error_text(qt_exc))
        return 3
    app.setApplicationName("MBDSDR")
    app.setApplicationDisplayName("MBDSDR - AI 定义无线电")
    app.setOrganizationName("MBDSDR Project")
    app.setApplicationVersion(_app_version)

    # QApplication 一建好就先摆一块实底「正在加载」：主窗口构造实测 2.5–4 s，
    # 这段时间屏幕上什么都没有，用户会以为程序没起来。splash 放在主题/翻译
    # 之前，是因为那些步骤也在吃首屏前的空白期，没必要等它们。
    splash = _show_startup_splash(app)

    # 启动自检（硬门槛）：必需依赖/数据件逐项探针。缺件立即中止并弹
    # 「MBDSDR 启动失败 + 缺什么 + 怎么修」，绝不照常开一个半残窗口。
    # 覆盖「import 成功、运行期懒加载才崩」的场景（如 _internal/scipy/_external 被删）。
    # numpy 二进制与 PySide6 真 import（DLL 坏掉只有真加载能发现）；
    # scipy 系只做 find_spec 存在性检查，真 import 推迟到首帧之后
    # （_start_deferred_imports，实测首次导入 1.0–1.3 s）。
    dep_problems = _startup_dependency_problems()
    if dep_problems:
        _close_splash(splash)
        _report_startup_failure(
            "启动自检失败：缺少必需组件（已中止启动）",
            "下面这些是应用正常工作的必需项，缺一项都无法继续：\n\n"
            + "\n\n".join(dep_problems)
            + "\n\n请按每项的「怎么修」处理后重新启动。")
        return 3

    # 设置默认字体
    app.setFont(get_app_font())

    # 主题不在建窗之前应用：窗口构造末尾的 apply_theme_once() 是唯一出口
    # （建窗前 setStyleSheet 会让每个新控件在构造期各自抛光，ctor 实测
    # 1.6 s 的大头）。这里只取主题对象给 fit_scale_to_screen 重算字号 QSS。
    theme = get_theme(args.theme)

    # 下拉框滚轮守卫：滚轮滚页面，不改选中项（必须在建窗口前装好）。
    _install_combo_wheel_guard(app)

    # i18n：按 ui_language 装翻译器（Qt Linguist .qm），必须先于任何窗口构造。
    # 源语言 zh 或 .qm 缺失时 l10n 内部诚实回退源语言并提示一次；加载异常
    # 绝不拦启动（仅告警）。
    try:
        import l10n as _l10n
        from logging_setup import configure_from_settings
        from settings import DesktopSettings as _DesktopSettings

        _boot_settings = _DesktopSettings.load()
        # 日志级别来自设置项 log_level（默认 INFO）；在启动阶段读取，
        # 改设置后重启生效。必须先于主窗口构造，避免丢掉启动期调试信息。
        configure_from_settings(_boot_settings)
        _l10n.install_translator(app, _boot_settings.get("ui_language"))
    except Exception as _l10n_ex:  # noqa: BLE001
        print(f"[mbdsdr] i18n: 加载失败，保持源语言 zh: {_l10n_ex}",
              file=sys.stderr)

    # 主窗口模块导入 ~0.8 s + 构造 2.5–4 s，都在 splash 之后做。
    MainWindow = _import_main_window(splash)
    window = MainWindow()
    window.app = app  # 传递 app 引用给主题切换
    # 主题唯一出口：构造末尾已应用过，这里显式走同一个出口（幂等空操作），
    # 以后调整时机也只改这一个方法，不再各写一遍 theme.apply(app)。
    window.apply_theme_once()

    # 设置写盘失败（例如安装目录/配置目录只读）时把真相送到状态栏，
    # 而不是只写进 settings.last_error 里无人看见。
    settings_obj = getattr(window, "settings", None)
    if settings_obj is not None:
        previous_handler = settings_obj.on_error

        def _on_settings_error(message, _window=window, _previous=previous_handler):
            try:
                show = getattr(_window, "_show_persistence_error_status", None)
                if callable(show):
                    show(str(message), 15000)
                else:
                    _window.statusBar().showMessage(str(message), 15000)
            except AttributeError:
                print(str(message), file=sys.stderr)
            if callable(_previous):
                _previous(message)

        settings_obj.on_error = _on_settings_error
        if getattr(settings_obj, "last_error", None):
            _on_settings_error("设置未能保存：" + settings_obj.last_error)

    # 设置应用图标（标题栏 / 任务栏）
    icon_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "assets", "icon.png"
    )
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
        window.setWindowIcon(QIcon(icon_path))

    # 如果指定了 host，延迟连接真实网络硬件（端点解析：显式参数 >
    # 环境变量 MBDSDR_MCP_HOST/PORT > tokens.MCP 默认值，不再散落写死）
    mcp_host, mcp_port = None, None
    if args.host:
        from PySide6.QtCore import QTimer
        from mcp_protocol import resolve_mcp_endpoint
        mcp_host, mcp_port = resolve_mcp_endpoint(args.host, args.port)
        QTimer.singleShot(500, lambda: window._connect_real(mcp_host, mcp_port))

    # 无障碍字号自动降档（裁定① A）：150% 档最小宽 1157px，1024 屏放不下。
    # 宁可按 125% 渲染让整窗可用，也不让窗口右边缘被裁。必须在 show 之前
    # 量最小宽；降档后重算 QSS 字号，并把说明写进设置页。
    try:
        from themes import fit_scale_to_screen
        fit_scale_to_screen(window, app, theme)
        _sp = getattr(window, "settings_panel", None)
        if _sp is not None and hasattr(_sp, "refresh_font_scale_note"):
            _sp.refresh_font_scale_note()
    except Exception as _fs_ex:  # noqa: BLE001 - 降档失败不得拦启动
        print(f"[mbdsdr] 字号自动降档失败，按请求倍率启动: {_fs_ex}",
              file=sys.stderr)

    window.show()
    if splash is not None:
        try:
            splash.finish(window)
        except Exception:  # noqa: BLE001 - 收尾失败不得拦启动
            pass

    # scipy 私有子树的真 import 放到首帧之后（_start_deferred_imports 钉在主窗
    # 首个 Paint 派发完毕之后才起工作线程）：实测首次导入 1.0–1.3 s，压在首帧前
    # 等于让用户对着 splash 多等一秒。失败只降级到状态栏 + 日志，不拦启动。
    _start_deferred_imports(window)

    # 控制台 Ctrl+C 走优雅退出（SIGINT→退出标志→QTimer 收口），
    # 不让 KeyboardInterrupt 抛进 Qt 回调里被吞掉。
    _install_sigint_graceful_quit(app)

    # 启动自检：运行期 DLL（缺件给「缺运行库：x」而不是 0xC0000135），
    # 以及录音目录的后台可写探测（不阻塞主线程，失败进状态栏）。
    _report_runtime_dlls()
    _report_corruption_evidence()
    _check_record_dir_async(window)

    print("=" * 60)
    print("  MBDSDR - AI 定义无线电桌面端")
    print(f"  版本: {_app_version} | MIT License")
    print(f"  主题: {theme.display_name}")
    if args.host:
        print(f"  模式: 网络硬件 {mcp_host}:{mcp_port}")
    print("=" * 60)
    print()
    print("快捷键:")
    print("  Ctrl+C - 连接设备")
    print("  Ctrl+D - 断开连接")
    print("  Ctrl+R - 开始/停止录音")
    print("  Ctrl+Q - 退出")
    print("  F11    - 全屏")
    print()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
