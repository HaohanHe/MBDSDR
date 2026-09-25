#!/usr/bin/env python3
"""
MBDSDR 桌面端入口
==================
AI 定义无线电全栈开源平台 - 桌面客户端。

用法:
  python3 main.py
  python3 main.py --sim          # 启动模拟模式（无硬件）
  python3 main.py --host 192.168.4.1 --port 81  # 连接真实硬件

依赖:
  pip install PySide6 numpy websocket-client websockets
"""

import sys
import os
import argparse

# 确保能导入同目录和上层目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon

from themes import get_theme, DEFAULT_THEME, get_app_font
from main_window import MainWindow


def main():
    parser = argparse.ArgumentParser(description="MBDSDR - AI 定义无线电桌面端")
    parser.add_argument("--sim", action="store_true", help="启动模拟模式（无硬件）")
    parser.add_argument("--host", type=str, default=None, help="设备 IP 地址")
    parser.add_argument("--port", type=int, default=81, help="设备端口")
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

    # 如果指定了连接参数，自动连接
    if args.sim:
        # 模拟模式由主窗口自动连接
        pass
    elif args.host:
        # 延迟连接真实硬件
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: window._connect_real(args.host, args.port))

    window.show()

    print("=" * 60)
    print("  MBDSDR - AI 定义无线电桌面端")
    print("  版本: 0.1.0 | GPL-3.0")
    print(f"  主题: {theme.display_name}")
    print(f"  渲染: {'OpenGL' if hasattr(window.spectrum, 'initializeGL') else '软件渲染 (QPainter)'}")
    if args.sim:
        print("  模式: 模拟（无硬件）")
    elif args.host:
        print(f"  模式: 真实硬件 {args.host}:{args.port}")
    print("=" * 60)
    print()
    print("快捷键:")
    print("  Ctrl+C - 连接设备")
    print("  Ctrl+D - 断开连接")
    print("  Ctrl+M - 模拟模式")
    print("  Ctrl+R - 开始/停止录音")
    print("  Ctrl+Q - 退出")
    print("  F11    - 全屏")
    print()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
