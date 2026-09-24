#!/bin/bash
# MBDSDR Linux/macOS 一键打包脚本
set -e
cd "$(dirname "$0")"
pip install pyinstaller
pyinstaller MBDSDR.spec --clean
echo ""
echo "打包完成，输出在 dist/MBDSDR/"
