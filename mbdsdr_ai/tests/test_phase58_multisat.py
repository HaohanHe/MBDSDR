# SPDX-License-Identifier: MIT
"""Phase58：运行时 TLE 拉取 + 多星多普勒包络（联网失败诚实空态）。"""
from __future__ import annotations
import os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from mbdsdr_ai.orbit import fetch_tle  # noqa: E402


class TestRuntimeTle:
    def test_bad_catnr_fail_honest(self):
        """坏 CATNR → 拉取失败抛异常（诚实 FAIL，不伪造 TLE）。"""
        with pytest.raises(Exception):
            fetch_tle(99999999)

    def test_real_tle_fresh_or_skip(self):
        """联网时拉真实 TLE（ISS），失败则 skip（不 mock、不预置）。"""
        try:
            l1, l2 = fetch_tle(25544)
        except Exception as e:
            pytest.skip(f"无网络，TLE 拉取空态: {e}")
        assert l1.startswith("1 ") and l2.startswith("2 ")
        epoch = l1[18:32].strip()
        assert epoch  # 新鲜度字段非空（运行时拉，非预置）

    def test_exp_script_importable(self):
        import importlib
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        importlib.import_module("exp_phase58_multisat")
