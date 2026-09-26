@echo off
rem ============================================================
rem  MBDSDR 一键运行脚本（Windows）
rem  检查 Python -> 创建/激活虚拟环境 -> 自动安装缺失依赖 -> 启动桌面端
rem  首次运行请双击本文件；日常快速启动可用 MBDSDR.vbs（无控制台）
rem ============================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."

echo ============================================================
echo  MBDSDR - AI 定义无线电桌面端
echo ============================================================
echo.

rem ---- 1. 检查 Python ----
set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  where py >nul 2>nul && set "PYTHON_CMD=py"
)
if not defined PYTHON_CMD (
  echo [错误] 未找到 Python。请安装 Python 3.10+ 并勾选 "Add Python to PATH"。
  echo 下载地址: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

for /f "tokens=*" %%v in ('%PYTHON_CMD% --version 2^>^&1') do set "PYVER=%%v"
echo [信息] Python 版本: !PYVER!

rem ---- 2. 创建/激活虚拟环境（除非 MBDSDR_NO_VENV=1）----
if /i "%MBDSDR_NO_VENV%"=="1" (
  echo [信息] 跳过虚拟环境（MBDSDR_NO_VENV=1），使用系统 Python。
  set "VENV_PY=%PYTHON_CMD%"
) else (
  if not exist ".venv\Scripts\python.exe" (
    echo [信息] 创建虚拟环境 .venv ...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
      echo [错误] 创建虚拟环境失败，将使用系统 Python。
      set "VENV_PY=%PYTHON_CMD%"
    ) else (
      echo [信息] 虚拟环境创建成功。
      set "VENV_PY=.venv\Scripts\python.exe"
    )
  ) else (
    echo [信息] 使用已有虚拟环境 .venv
    set "VENV_PY=.venv\Scripts\python.exe"
  )
)

rem ---- 3. 升级 pip ----
echo [信息] 升级 pip ...
"%VENV_PY%" -m pip install --upgrade pip >nul 2>nul

rem ---- 4. 安装核心依赖 ----
echo [信息] 安装核心依赖（requirements.txt）...
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [错误] 核心依赖安装失败，请检查网络连接后重试。
  echo.
  pause
  exit /b 1
)

rem ---- 5. 安装桌面端依赖 ----
echo [信息] 安装桌面端依赖（desktop\requirements.txt）...
"%VENV_PY%" -m pip install -r desktop\requirements.txt
if errorlevel 1 (
  echo [错误] 桌面端依赖安装失败，请检查网络连接后重试。
  echo.
  pause
  exit /b 1
)

rem ---- 6. SoapySDR（可选，失败不中断）----
echo [信息] 尝试安装 SoapySDR（可选，用于 HackRF/Pluto/Airspy 等设备）...
"%VENV_PY%" -m pip install SoapySDR >nul 2>nul
if errorlevel 1 (
  echo [警告] SoapySDR 安装失败。RTL-SDR 仍可正常使用。
  echo        如需 HackRF/PlutoSDR/Airspy/SDRplay，请先安装 SoapySDR 系统驱动：
  echo        https://github.com/pothosware/SoapySDR/wiki
) else (
  echo [信息] SoapySDR 安装成功。
)

rem ---- 7. 验证关键依赖 ----
echo [信息] 验证关键依赖 ...
"%VENV_PY%" -c "import PySide6, numpy, scipy; print('  PySide6', PySide6.__version__); print('  numpy', numpy.__version__); print('  scipy', scipy.__version__)" 2>nul
if errorlevel 1 (
  echo [错误] 关键依赖验证失败，请检查上方安装日志。
  echo.
  pause
  exit /b 1
)

rem ---- 8. 启动 ----
echo.
echo ============================================================
echo  启动 MBDSDR 桌面端 ...
echo ============================================================
"%VENV_PY%" desktop\main.py

rem 如果程序异常退出，保留窗口看错误
if errorlevel 1 (
  echo.
  echo [信息] 程序已退出（退出码 %errorlevel%）。
  pause
)

endlocal
exit /b 0
