# -*- coding: utf-8 -*-
"""Remove simulation mode and misleading render label from main.py."""
import io
F = "/home/user/Doubao/chats/38438160041798146/desktop/main.py"
s = io.open(F, encoding="utf-8").read()

# docstring usage line
s = s.replace('  python3 main.py --sim          # 启动模拟模式（无硬件）\n', '')

# --sim argument
s = s.replace('    parser.add_argument("--sim", action="store_true", help="启动模拟模式（无硬件）")\n', '')

# connect params branch
s = s.replace(
    '''    # 如果指定了连接参数，自动连接
    if args.sim:
        # 模拟模式由主窗口自动连接
        pass
    elif args.host:
        # 延迟连接真实硬件
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: window._connect_real(args.host, args.port))
''',
    '''    # 如果指定了 host，延迟连接真实网络硬件
    if args.host:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, lambda: window._connect_real(args.host, args.port))
''')

# render label + sim mode print
s = s.replace(
    '    print(f"  渲染: {\'OpenGL\' if hasattr(window.spectrum, \'initializeGL\') else \'软件渲染 (QPainter)\'}")\n',
    '')
s = s.replace(
    '''    if args.sim:
        print("  模式: 模拟（无硬件）")
    elif args.host:
        print(f"  模式: 真实硬件 {args.host}:{args.port}")
''',
    '''    if args.host:
        print(f"  模式: 网络硬件 {args.host}:{args.port}")
''')

# Ctrl+M shortcut banner
s = s.replace('    print("  Ctrl+M - 模拟模式")\n', '')

io.open(F, "w", encoding="utf-8", newline="").write(s)
print("main.py simulation removed")
