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
import tempfile

# 确保能导入同目录和上层目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon

from themes import get_theme, DEFAULT_THEME, get_app_font
from main_window import MainWindow


def _ensure_stdio_for_pythonw():
    """pythonw.exe 启动时没有控制台，sys.stdout/sys.stderr 会是 None。

    此时任何 print() 落到 None 都会在启动阶段就抛 AttributeError 直接崩。
    这里在最早时机把二者补回来：
      1) 优先追加写日志文件（desktop 配置目录 ~/.config/MBDSDR，失败再退回
         系统临时目录 tempfile.gettempdir() 下的 mbdsdr_desktop.log），
         追加模式、UTF-8、errors="replace"；
      2) 日志文件也打不开时退到 os.devnull（黑洞），保证任何 print 都不崩。
    正常 python3 运行时 stdout/stderr 都不是 None，本函数直接返回、零副作用。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return

    def _open_log():
        candidates = []
        try:
            cfg_dir = os.path.join(os.path.expanduser("~"), ".config", "MBDSDR")
            os.makedirs(cfg_dir, exist_ok=True)
            candidates.append(os.path.join(cfg_dir, "mbdsdr_desktop.log"))
        except Exception:
            pass
        try:
            candidates.append(os.path.join(tempfile.gettempdir(), "mbdsdr_desktop.log"))
        except Exception:
            pass
        for path in candidates:
            try:
                return open(path, "a", encoding="utf-8", errors="replace")
            except Exception:
                continue
        # 日志目录都不可用 → 黑洞兜底
        return open(os.devnull, "w", encoding="utf-8", errors="replace")

    if sys.stdout is None:
        try:
            sys.stdout = _open_log()
        except Exception:
            sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stderr is None:
        try:
            sys.stderr = _open_log()
        except Exception:
            sys.stderr = sys.stdout


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

    # 高 DPI 支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
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
