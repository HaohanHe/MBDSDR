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


def _packaging_verdict(missing: str) -> "tuple[str, str]":
    """返回 (判定, 子树)：defect=整棵子树缺失；incomplete=只缺单文件；unknown=判不了。"""
    for subtree, siblings in _PACKAGING_SIBLINGS.items():
        if missing == subtree or missing.startswith(subtree + "."):
            if any(_probe_import(name) for name in siblings):
                return "incomplete", subtree
            return "defect", subtree
    return "unknown", ""


def _run_import_smoke() -> None:
    """启动自检：把易漏的私有模块逐个导入，结果写进启动日志。"""
    failed = [name for name in _RISKY_IMPORTS if not _probe_import(name)]
    total = len(_RISKY_IMPORTS)
    line = f"[smoke] 私有模块导入自检 {total - len(failed)}/{total} 通过"
    if failed:
        line += "；失败：" + ", ".join(failed)
    _append_startup_log(line)
    print(line, file=sys.stderr)


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


# 打包版是 GUI 子系统（无控制台）：导入失败如果放任抛 traceback，用户只会
# 看到一个 "Unhandled exception in script" 英文弹窗，或干脆静默退出。
# 这里统一截获，翻译成「缺哪个模块 + 怎么补」，并写日志。
_ensure_stdio_for_pythonw()

try:
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt, QObject, Signal
    from PySide6.QtGui import QFont, QIcon

    from themes import get_theme, DEFAULT_THEME, get_app_font
    from main_window import MainWindow
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
    args = parser.parse_args()

    # 启动自检：把 PyInstaller 静态分析易漏的私有模块逐个导入并记日志。
    _run_import_smoke()

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
    app.setApplicationVersion("0.1.0")

    # 设置默认字体
    app.setFont(get_app_font())

    # 应用主题
    theme = get_theme(args.theme)
    theme.apply(app)

    # i18n：按 ui_language 装翻译器（Qt Linguist .qm），必须先于任何窗口构造。
    # 源语言 zh 或 .qm 缺失时 l10n 内部诚实回退源语言并提示一次；加载异常
    # 绝不拦启动（仅告警）。
    try:
        import l10n as _l10n
        from settings import DesktopSettings as _DesktopSettings
        _l10n.install_translator(app, _DesktopSettings.load().get("ui_language"))
    except Exception as _l10n_ex:  # noqa: BLE001
        print(f"[mbdsdr] i18n: 加载失败，保持源语言 zh: {_l10n_ex}",
              file=sys.stderr)

    # 创建主窗口
    window = MainWindow()
    window.app = app  # 传递 app 引用给主题切换

    # 设置写盘失败（例如安装目录/配置目录只读）时把真相送到状态栏，
    # 而不是只写进 settings.last_error 里无人看见。
    settings_obj = getattr(window, "settings", None)
    if settings_obj is not None:
        previous_handler = settings_obj.on_error

        def _on_settings_error(message, _window=window, _previous=previous_handler):
            try:
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

    window.show()

    # 启动自检：运行期 DLL（缺件给「缺运行库：x」而不是 0xC0000135），
    # 以及录音目录的后台可写探测（不阻塞主线程，失败进状态栏）。
    _report_runtime_dlls()
    _check_record_dir_async(window)

    print("=" * 60)
    print("  MBDSDR - AI 定义无线电桌面端")
    print("  版本: 0.1.0 | MIT License")
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
