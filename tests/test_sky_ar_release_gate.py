# SPDX-License-Identifier: MIT
"""天空指向发布门（``release_gate.check_sky_ar_pointing``）的判据测试。

不 import Qt、不联网、不读真机：只验证发布门的三条专项检查确实在生效，
而不是空转——术语泄漏与「零观测却说已解算」都必须让检查 FAILED。
"""
from __future__ import annotations

import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import release_gate  # noqa: E402
from mbdsdr_ai import sky_health, sky_hud  # noqa: E402


@pytest.fixture(autouse=True)
def _default_offline(monkeypatch):
    """固定产品默认环境：禁网，且不显式允许联网。"""
    monkeypatch.delenv("MBDSDR_ALLOW_NETWORK", raising=False)
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")


def test_sky_ar_pointing_passes_on_current_tree():
    status, detail = release_gate.check_sky_ar_pointing()
    assert status == release_gate.PASSED, detail
    assert "0 次网络调用" in detail


def test_hud_term_leak_fails_check(monkeypatch):
    leaked = dict(sky_hud.HUD_HINTS)
    leaked["L2"] = "已融合 FOV 30"
    monkeypatch.setattr(sky_hud, "HUD_HINTS", leaked)

    status, detail = release_gate.check_sky_ar_pointing()
    assert status == release_gate.FAILED
    assert "FOV" in detail and "HUD_HINTS[L2]" in detail


def test_dishonest_empty_state_fails_check(monkeypatch):
    monkeypatch.setattr(
        sky_health, "health_snapshot",
        lambda *args, **kwargs: {"level": "L3", "reason": "星点解算成功"})

    status, detail = release_gate.check_sky_ar_pointing()
    assert status == release_gate.FAILED
    assert "L3" in detail


def test_build_checks_includes_sky_ar_pointing():
    names = [name for name, _ in release_gate.build_checks(with_tests=False)]
    assert "天空指向发布门" in names
    assert (names.index("工具面审计")
            < names.index("天空指向发布门")
            < names.index("关键测试"))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
