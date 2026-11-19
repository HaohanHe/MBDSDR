"""TLE 阈值「唯一真值 + 常态对拍」钉扎。

从 QGIS 3.44.15 ``QgsTemporalRange`` / CRS 口径治理学到的次序：
先分口径，再收敛**同口径**的重复实现，最后用可跑的守卫常态对拍。
本文件钉的是「过境报告里的 TLE 新鲜度阈值」这一口径，不碰展示闸门、
合成 TLE 策略、展示五档等其它口径。

独立 oracle 故意不 import 产品常量：否则大家一起错时「互相一致」照样全绿。
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import pytest

from mbdsdr_ai import orbit, tle_integrity as ti
from mbdsdr_ai import sat_passes, new_spacetime_timeline, builtin_tle_meta

ROOT = Path(__file__).resolve().parents[1]
ORBIT_SRC = ROOT / "mbdsdr_ai" / "orbit.py"

#: 独立 oracle：写死在测试里，不读产品常量。
ORACLE = {
    "fresh_max_hours": 24.0,
    "usable_max_days": 7.0,
    "expired_max_days": 30.0,
}


def _report(monkeypatch, *, impl=None, trusted=True):
    """造一条 predict_passes_report 返回路径，不碰真机、不联网。"""
    import mbdsdr_ai.station as station

    monkeypatch.setattr(
        station, "trust_for_calculation",
        lambda _st: {"trusted": trusted, "reasons": ["钉扎注入"]})
    monkeypatch.setattr(orbit, "_predict_passes_impl", impl)
    return orbit.predict_passes_report(45.0, 120.0, 0.0,
                                      require_trusted_station=True)


def test_policy_matches_independent_oracle():
    """报告快照的数值必须与独立 oracle 逐位一致（键集合保持原样）。"""
    p = orbit._report_tle_policy(True)
    assert set(p) == {"allow_stale", *ORACLE}
    assert p["allow_stale"] is True
    for key, expected in ORACLE.items():
        assert p[key] == expected, key


def test_policy_matches_canonical_constants():
    """并与当前权威常量同源。"""
    p = orbit._report_tle_policy(False)
    assert p["fresh_max_hours"] == ti.FRESH_MAX_HOURS
    assert p["usable_max_days"] == ti.USABLE_MAX_DAYS
    assert p["expired_max_days"] == ti.EXPIRED_MAX_DAYS
    assert p["allow_stale"] is False


def test_policy_follows_canonical_when_constant_changes(monkeypatch):
    """改权威常量，报告必须实时跟随 —— 证明不是硬编码/绑定值。"""
    monkeypatch.setattr(ti, "FRESH_MAX_HOURS", 48.0)
    assert orbit._report_tle_policy(True)["fresh_max_hours"] == 48.0
    monkeypatch.setattr(ti, "EXPIRED_MAX_DAYS", 45.0)
    assert orbit._report_tle_policy(True)["expired_max_days"] == 45.0


def test_three_return_paths_share_one_policy(monkeypatch):
    """三条返回路径（不可信站址 / 时间基准失效 / 正常）同一个事实源。"""
    untrusted = _report(monkeypatch, trusted=False)

    def _boom(*_a, **_kw):
        raise orbit.TimeBasisUnavailable("钉扎注入")
    unavailable = _report(monkeypatch, impl=_boom)
    normal = _report(monkeypatch, impl=lambda *a, **kw: ([], []), trusted=True)

    policies = [r["tle_policy"] for r in (untrusted, unavailable, normal)]
    assert all(p == policies[0] for p in policies), policies
    assert policies[0]["allow_stale"] is True
    for key, expected in ORACLE.items():
        assert policies[0][key] == expected, key


def test_no_hardcoded_threshold_literals_in_orbit():
    """结构检查：orbit.py 不许再各自写一份阈值字面量。"""
    src = ORBIT_SRC.read_text(encoding="utf-8-sig")
    for key in ORACLE:
        pattern = r'["\']' + re.escape(key) + r'["\']\s*:\s*[0-9]'
        assert not re.search(pattern, src), f"发现 {key} 的硬编码字面量"
    # 三条返回路径都必须走唯一入口（AST：dict 键 tle_policy 的值是
    # 调用 _report_tle_policy(allow_stale)，不是文本计数）。
    tree = ast.parse(src)
    entry_calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "tle_policy"):
                continue
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id == "_report_tle_policy"):
                continue
            if (len(value.args) == 1 and isinstance(value.args[0], ast.Name)
                    and value.args[0].id == "allow_stale"):
                entry_calls.append(value)
    assert len(entry_calls) == 3, (
        f"三条返回路径都必须走唯一入口，实际 {len(entry_calls)} 处")


def test_display_policy_delegates_to_tracking_policy():
    """桌面展示策略必须等于 ``tracking_policy`` 的输出（不是自己拼一份）。

    行为判据：若面板改回直接读 ``FRESH_MAX_HOURS`` / ``EXPIRED_MAX_DAYS``
    自己拼字段，下面任一年龄的字典都会与权威入口对不上。
    """
    from desktop import sat_track_panel as panel

    for age in (0.5, 3.0, 8.0, 120.0):
        expected = ti.tracking_policy(
            {"tier": ti.age_band(age), "age_days": age})
        assert panel._tle_policy(age) == expected, f"age={age}"
    # stale（31–90 天）在展示层被并入 dead 档——这是既有映射，不是另一套口径。
    assert panel._tle_policy(60.0) == ti.tracking_policy(
        {"tier": "dead", "age_days": 60.0})
    # 展示口径不含新鲜度阈值字段（那是过境报告的口径）
    assert not (set(panel._tle_policy(3.0)) & set(ORACLE))


def test_display_and_synthetic_policies_are_different_contracts():
    """口径登记：同名 policy 的三个事实源不许合并。"""
    # 合成 TLE 处置策略是另一套字段，不暴露新鲜度阈值。
    synthetic = builtin_tle_meta.synthetic_tle_policy()
    assert not (set(synthetic) & set(ORACLE))


def test_age_band_is_a_five_band_display_contract():
    """展示五档（31-90d / >90d）与 freshness 四档是不同口径，不进报告快照。"""
    assert ti.age_band(1.0) == "fresh"
    assert ti.age_band(8.0) == "expired"
    assert ti.age_band(60.0) == "stale"
    assert ti.age_band(120.0) == "dead"
    assert ti.age_band(None) == "unknown"
    assert "dead_stale_max_days" not in orbit._report_tle_policy(True)


def test_two_satellite_pass_classes_have_different_time_basis():
    """两个同名 SatellitePass 用两套时间表示，禁止误合并。

    sat_passes 的时间是 skyfield Time（必填），timeline 的时间是
    Optional[datetime]（可空）；两者是不同链路的事实源。
    """
    f1 = {f.name: str(f.type) for f in dataclasses.fields(sat_passes.SatellitePass)}
    f2 = {f.name: str(f.type)
          for f in dataclasses.fields(new_spacetime_timeline.SatellitePass)}
    assert "Time" in f1["rise_time"]
    assert "datetime" in f2["rise_time"]
    # 注解形式可能是 Optional[datetime] 或 PEP 604 的 datetime | None，
    # 验「可空」这个实质，不钉死拼写。
    assert "None" in f2["rise_time"] or "Optional" in f2["rise_time"]
    assert "trajectory" in f2 and "trajectory" not in f1
