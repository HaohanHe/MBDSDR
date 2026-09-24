@echo off
rem MBDSDR 桌面端无控制台启动器（Windows）
rem 用 pythonw 启动 run_gui.pyw，不弹出黑色控制台窗口。
cd /d "%~dp0"
start "" pythonw run_gui.pyw %*
