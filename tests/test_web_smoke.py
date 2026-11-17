# SPDX-License-Identifier: MIT
"""Web 端到端冒烟测试：启动 → 状态 → 一帧频谱 → 关闭。

用 subprocess 跑 scripts/web_smoke.py，端口由脚本内部取 0（临时端口），
超时 30 s；无硬件时脚本使用 debug 调试源。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "web_smoke.py")


def test_web_smoke_script_end_to_end():
    env = dict(os.environ)
    env.setdefault("MBDSDR_OFFLINE", "1")
    proc = subprocess.run(
        [sys.executable, _SCRIPT, "--device", "debug", "--timeout", "15"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"web_smoke 退出码 {proc.returncode}\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")

    steps = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        steps.append(json.loads(line))
    by_step = {item.get("step"): item for item in steps}

    assert set(by_step) >= {"start", "status", "spectrum", "stop"}
    assert by_step["status"]["connected"] is True
    assert by_step["spectrum"]["bins"] > 0
    assert by_step["spectrum"]["center_freq_hz"] is not None
    assert by_step["spectrum"]["samp_rate_hz"] is not None
    assert by_step["stop"]["web_stopped"] is True


def test_web_smoke_fails_nonzero_with_reason_on_bad_device():
    proc = subprocess.run(
        [sys.executable, _SCRIPT, "--device", "mbdsdr-no-such-device-xyz",
         "--timeout", "2"],
        cwd=_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode != 0
    assert "web_smoke FAILED" in proc.stderr
    assert "mbdsdr-no-such-device-xyz" in proc.stderr
