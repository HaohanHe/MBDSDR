@echo off
rem ============================================================
rem  MBDSDR 无控制台启动器（Windows）
rem  探测 pythonw.exe（PATH / py 启动器 / 豆包运行时），无黑框启动桌面端。
rem  由仓库根 MBDSDR.vbs 以隐藏方式调用；也可直接双击本文件。
rem ============================================================
setlocal EnableExtensions
cd /d "%~dp0.."

set "PYW="
rem 1) PATH 中的 pythonw
for /f "delims=" %%i in ('where pythonw.exe 2^>nul') do if not defined PYW set "PYW=%%i"
rem 2) py 启动器对应的 pyw
if not defined PYW for /f "delims=" %%i in ('where pyw.exe 2^>nul') do if not defined PYW set "PYW=%%i"
rem 3) 豆包沙箱运行时（按 hash 目录通配）
if not defined PYW for /f "delims=" %%i in ('dir /b /s "%LOCALAPPDATA%\Doubao\User Data\sandbox_runtime\bases\*\python\pythonw.exe" 2^>nul') do if not defined PYW set "PYW=%%i"

if not defined PYW (
  mshta "javascript:new ActiveXObject('WScript.Shell').Popup('未找到 Python (pythonw.exe)。请先安装 Python for Windows，或在豆包环境内运行。',0,'MBDSDR',16);close()"
  exit /b 1
)

rem 快速依赖检查：PySide6 缺失时提示先跑 run_windows.bat，不静默失败
"%PYW%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
  mshta "javascript:new ActiveXObject('WScript.Shell').Popup('依赖未安装。请先双击 scripts\run_windows.bat 完成依赖安装，之后再用本入口启动。',0,'MBDSDR',48);close()"
  exit /b 1
)

start "MBDSDR" "%PYW%" "%~dp0..\desktop\main.py"
exit /b 0
