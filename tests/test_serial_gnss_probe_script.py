# SPDX-License-Identifier: MIT
"""scripts/serial_gnss_probe.py 的跨平台入口回归。

修的是真缺陷：脚本用 ``__file__.rsplit("/", 2)[0]`` 求仓库根，Windows 的
反斜杠路径切不出来，直接以 ``No module named 'mbdsdr_ai'`` 失败
（2026-11-23 真机 GNSS 自检实测）。这里用 ``--list-ports`` 跑一遍：
不打开串口、不依赖是否插了硬件，只验「脚本能导入模块并正常退出」。
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "serial_gnss_probe.py")


def test_probe_script_starts_on_windows_path():
    proc = subprocess.run(
        [sys.executable, _SCRIPT, "--list-ports"],
        capture_output=True, text=True, timeout=90,
        cwd=_ROOT,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, out
    assert "No module named 'mbdsdr_ai'" not in out
    assert "无法导入 serial_gnss 模块" not in out
