@echo off
rem MBDSDR Windows 一键打包脚本
cd /d "%~dp0"
pip install pyinstaller
pyinstaller MBDSDR.spec --clean
echo.
echo 打包完成，输出在 dist\MBDSDR\
pause
