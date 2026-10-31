# SPDX-License-Identifier: MIT
"""适配兼容性锚点：可选依赖缺失时全包可导入 + 用到才诚实报错。

pyproject.toml 把 sgp4/skyfield/jplephem/astropy（satellite 组）、
matplotlib/Pillow/psutil（viz 组）、PySide6（desktop 组）、
pyserial/pyrtlsdr/pylibiio/pyvisa/sounddevice（hardware 组）全部列为
可选依赖；numpy/scipy/requests 才是硬依赖。因此：

  * 缺失任一可选依赖时，``import mbdsdr_ai`` 及其全部子模块必须仍成功
    （功能面降级，不炸导入链）；
  * 真正用到缺失依赖的功能必须抛出含安装提示的 ImportError，
    而不是 ``AttributeError: 'NoneType'`` 这类不可操作的裸异常。

测试用子进程隔离（主测试进程不污染 sys.modules）：先把被测依赖在
sys.modules 里置 None（import 即抛 ImportError），再遍历导入
mbdsdr_ai 全部子模块，并抽查几个守卫点的诚实报错语义。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# 全部可选依赖面（与 pyproject optional-dependencies 分组一致）。
# scipy/requests/numpy 是硬依赖，不在此列——它们缺失时崩溃是符合声明的。
_BLOCKED = [
    "sgp4", "skyfield", "jplephem", "astropy",
    "matplotlib", "PIL", "psutil",
    "PySide6",
    "serial", "rtlsdr", "libiio", "pyvisa", "sounddevice",
    "yaml",
]

_PKG_ROOT = Path(__file__).resolve().parents[1]

_RUNNER = r"""
import importlib, json, pkgutil, sys

blocked = json.loads(sys.argv[1])
for name in blocked:
    sys.modules[name] = None  # import 即抛 ImportError，模拟未安装

failures = {}
import mbdsdr_ai  # 主包入口必须先无可选依赖立住

for m in pkgutil.walk_packages(mbdsdr_ai.__path__, mbdsdr_ai.__name__ + "."):
    if ".tests" in m.name:
        continue
    try:
        importlib.import_module(m.name)
    except Exception as e:  # noqa: BLE001 - 锚点：任何导入失败都要上报
        failures[m.name] = f"{type(e).__name__}: {e}"

honesty = {}

def check(key, fn):
    try:
        fn()
    except ImportError as e:
        honesty[key] = str(e)
    except Exception as e:  # noqa: BLE001 - 守卫点只能抛 ImportError
        honesty[key] = f"WRONG-EXC {type(e).__name__}: {e}"
    else:
        honesty[key] = "NO-RAISE"

import numpy as np
from mbdsdr_ai import orbit
check("orbit.compute_satellite_state",
      lambda: orbit.compute_satellite_state("ISS", 0.0, 0.0))

from mbdsdr_ai import lrpt_render
check("lrpt_render.save_png",
      lambda: lrpt_render.save_png(np.zeros((2, 2), np.uint8), "x.png"))

from mbdsdr_ai import device_watchdog
check("device_watchdog.DeviceWatchdog",
      lambda: device_watchdog.DeviceWatchdog(None))

from mbdsdr_ai import sat_passes
check("sat_passes.make_timescale", sat_passes.make_timescale)

from mbdsdr_ai import new_spacetime_timeline as nstt
check("nstt.compute_passes", lambda: nstt.TimelineEngine().compute_passes(
    0.0, 0.0, 0.0, [("X", "1 25544U", "2 25544")]))

print(json.dumps({"failures": failures, "honesty": honesty}))
"""


def _run_runner(blocked: list) -> dict:
    import os
    # 钉 UTF-8：子进程 import 全包时模块可能往 stdout 打 CJK 日志，
    # text 模式按 locale(GBK) 解码会 UnicodeDecodeError（假失败）。
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", _RUNNER, json.dumps(blocked)],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
        cwd=str(_PKG_ROOT.parent), env=env,
    )
    if proc.returncode != 0:
        pytest.fail(
            f"runner 退出码 {proc.returncode}\nstdout:\n{proc.stdout}"
            f"\nstderr:\n{proc.stderr[-4000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_all_modules_import_without_optional_deps():
    res = _run_runner(_BLOCKED)
    assert res["failures"] == {}, (
        "以下模块在可选依赖缺失时导入失败（须改为 try/except 守卫）：\n"
        + "\n".join(f"  {k}: {v}" for k, v in res["failures"].items()))


def test_missing_dep_raises_actionable_importerror():
    """守卫点必须抛含安装提示的 ImportError，不得静默通过或裸异常。"""
    res = _run_runner(_BLOCKED)
    for key, msg in res["honesty"].items():
        assert not msg.startswith("WRONG-EXC"), f"{key}: {msg}"
        assert msg != "NO-RAISE", f"{key}: 缺失依赖时未诚实报错"
        assert "pip install" in msg, f"{key}: 报错不含可操作安装提示: {msg}"


def test_full_env_still_works():
    """对照组：不阻塞依赖时全模块导入必须零失败（防守卫误伤正常路径）。"""
    res = _run_runner([])
    assert res["failures"] == {}, res["failures"]
