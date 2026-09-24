#!/bin/bash
# MBDSDR 桌面端无控制台启动器（Linux / macOS 后台运行）
cd "$(dirname "$0")"
nohup python3 main.py "$@" > /dev/null 2>&1 &
