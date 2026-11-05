# SPDX-License-Identifier: MIT
"""R6 月球外部星历降级路径反证（白名单 `ring6_sky_mode.py:104`）。

DE421/skyfield 是可选的外部真值：缺失时必须返回 None，由调用方标
UNVERIFIED（不补数），而不是崩溃或伪造星位。本用例源码级触发该边界，
供 `scripts/swallow_whitelist.json` 的 trigger 校验。
"""
from __future__ import annotations

import builtins
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "scripts", "hw_acceptance")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ring6_sky_mode as sky  # noqa: E402


def test_moon_reference_degrades_without_skyfield(monkeypatch):
    """skyfield 导入失败 → 返回 None，不抛给调用方、不补数。"""
    real_import = builtins.__import__

    def _fail_skyfield(name, *args, **kwargs):
        if name == "skyfield" or name.startswith("skyfield."):
            raise ImportError("模拟 skyfield 未安装")
        return real_import(name, *args, **kwargs)

    # 路径存在性不影响本用例：只验证「有星历路径但导入失败」的降级分支。
    monkeypatch.setattr(sky, "_ephemeris_path", lambda: "de421.bsp")
    monkeypatch.setattr(builtins, "__import__", _fail_skyfield)
    monkeypatch.setattr(sky, "_EPH_CACHE", {})
    assert sky._moon_reference(2451545.0) is None
