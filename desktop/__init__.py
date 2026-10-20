# SPDX-License-Identifier: MIT
"""desktop 包垫片：双上下文导入一次钉死。

desktop/ 模块间历来是同级裸导入（``from tokens import tokens`` /
``import ai_steps``），脚本上下文（``python desktop/main.py``，脚本目录
自动进 sys.path）天然可解析；但测试/工具的包上下文（``from desktop.ai_panel
import …``）解析不到——A 线（2026-10-20）即因此把 test_ai_panel_offscreen
的收集搞炸（``import ai_steps`` ModuleNotFoundError）。

这里在包导入的第一时间把 desktop/ 目录垫进 sys.path，让两种上下文同口径；
只垫一次、不含业务逻辑。脚本上下文不受影响（本文件不被 __main__ 执行）。
"""
import os as _os
import sys as _sys

_desktop_dir = _os.path.dirname(_os.path.abspath(__file__))
if _desktop_dir not in _sys.path:
    _sys.path.insert(0, _desktop_dir)
