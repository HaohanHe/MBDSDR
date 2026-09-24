#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MBDSDR 桌面端无控制台启动器（Windows .pyw / Linux 通用）。

Windows 下 .pyw 由 pythonw.exe 关联，双击不弹出黑色控制台窗口。
参数透传给 main.py，例如:
  pythonw run_gui.pyw --sim
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import main

if __name__ == "__main__":
    main()
