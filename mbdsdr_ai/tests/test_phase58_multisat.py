# SPDX-License-Identifier: MIT
"""Phase58：运行时 TLE 拉取 + 多星多普勒包络（联网失败诚实空态）。"""
from __future__ import annotations
import os, sys, time
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


class TestTleFetchCooldown:
    """联网拉取失败的负缓存冷却（预测循环不会每步重试网络）。

    背景：predict_satellite_pass 以 30s 步长扫窗口（24h=2880 步），每步
    经 _resolve_tle 走一次 fetch_tle。失败无冷却时，外网不可达每步都要等
    满 urlopen 超时——工具调用被拖到小时量级（实测 tests/ 根 dry-call
    挂起 10 分钟以上）。冷却期内如实回退内置 TLE，source 不变。
    """

    @pytest.fixture
    def cooldown(self):
        """隔离 _TLE_FAIL_UNTIL：快照 + 清空 + 还原，避免跨用例泄漏。"""
        from mbdsdr_ai import decoders
        saved = dict(decoders._TLE_FAIL_UNTIL)
        decoders._TLE_FAIL_UNTIL.clear()
        yield decoders._TLE_FAIL_UNTIL
        decoders._TLE_FAIL_UNTIL.clear()
        decoders._TLE_FAIL_UNTIL.update(saved)

    @staticmethod
    def _patch_fetch(monkeypatch, side_effect):
        """桩掉 orbit.fetch_tle（_resolve_tle 函数内 from .orbit import，
        故须 patch orbit 模块属性），返回调用计数器。"""
        from mbdsdr_ai import orbit
        calls = {"n": 0}

        def fake_fetch(catnr, max_age_hours=24.0):
            calls["n"] += 1
            if isinstance(side_effect, Exception):
                raise side_effect
            return side_effect(catnr)

        monkeypatch.setattr(orbit, "fetch_tle", fake_fetch)
        return calls

    def test_failure_cools_down_no_retry_storm(self, monkeypatch, cooldown):
        """失败后冷却期内多次解析只真正尝试一次，且如实回退内置 TLE。"""
        from mbdsdr_ai import decoders
        calls = self._patch_fetch(monkeypatch, OSError("blackholed (test stub)"))
        name = "ISS (ZARYA)"

        results = [decoders._resolve_tle(name) for _ in range(8)]

        assert calls["n"] == 1
        for l1, l2, src in results:
            assert src == "builtin-fallback"
            assert (l1, l2) == decoders.BUILTIN_TLE[name]

    def test_cooldown_expiry_allows_retry(self, monkeypatch, cooldown):
        """冷却到期后恢复重试（不把一次失败当成永久断网）。"""
        from mbdsdr_ai import decoders
        calls = self._patch_fetch(monkeypatch, OSError("blackholed (test stub)"))
        name = "NOAA 15"

        decoders._resolve_tle(name)
        assert calls["n"] == 1
        cooldown[name] = time.monotonic() - 1.0     # 拨快时钟越过度冷
        decoders._resolve_tle(name)
        assert calls["n"] == 2

    def test_success_clears_cooldown(self, monkeypatch, cooldown):
        """联网成功后清冷却，后续解析继续走联网路径。"""
        from mbdsdr_ai import decoders
        state = {"n": 0}

        def flaky(catnr):
            state["n"] += 1
            if state["n"] == 1:
                raise OSError("transient (test stub)")
            return ("1 28654U TESTLINE", "2 28654 TESTLINE")

        self._patch_fetch(monkeypatch, flaky)
        name = "NOAA 18"

        l1, l2, src = decoders._resolve_tle(name)
        assert src == "builtin-fallback"            # 首次失败 → 回退
        cooldown[name] = time.monotonic() - 1.0     # 越过冷却
        l1, l2, src = decoders._resolve_tle(name)
        assert src == "celestrak"
        assert l1 == "1 28654U TESTLINE"
        assert name not in cooldown                 # 成功即清冷却
        assert state["n"] == 2
