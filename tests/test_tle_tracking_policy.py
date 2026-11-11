"""P0-01 跟踪闸门矩阵 + 反向回归。

覆盖三件事：
1. 四档 × 四操作的许可矩阵（期望值按设计文档写死，**不复用 TRACKING_MODES**，
   否则改表后测试跟着变，等于没有判卷）；
2. identity mismatch / no_data 的降级：错对象一律按 dead，缺数据按 unknown；
3. SatelliteTracker.tracking_policy_for() 与纯函数同口径（唯一事实源），
   且 current_position() 把 policy 透出给调用方。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mbdsdr_ai.sat_tracker import SatelliteTracker  # noqa: E402
from mbdsdr_ai.tle_integrity import tracking_policy  # noqa: E402

# (level, 过境列表, 可调谐, 需确认, 云台, 工具写, 工具需确认, 命令可下发)
# 期望值来自 docs/research/2026-11-11-p0-satellite-plan.md §1.3 的矩阵，
# 不是从实现里推导出来的。
MATRIX = [
    ("fresh",   True,  True,  False, True,  True,  False, True),
    ("usable",  True,  True,  False, True,  True,  False, True),
    ("expired", True,  True,  True,  False, True,  True,  True),
    ("dead",    False, False, False, False, False, False, False),
    ("unknown", False, False, False, False, False, False, False),
]


@pytest.mark.parametrize(
    "level,pass_list,tune,confirm,gimbal,tool,tool_confirm,command", MATRIX)
def test_tracking_policy_matrix(level, pass_list, tune, confirm, gimbal,
                                tool, tool_confirm, command):
    fresh = {"tier": level, "age_days": {"fresh": 0.2, "usable": 3.0,
                                        "expired": 12.0, "dead": 47.0,
                                        "unknown": None}[level]}
    pol = tracking_policy(fresh, "verified")
    assert pol["level"] == level
    assert pol["allow_pass_list"] is pass_list
    assert pol["allow_tune"] is tune
    assert pol["require_confirm"] is confirm
    assert pol["allow_gimbal"] is gimbal
    assert pol["allow_tool_write"] is tool
    assert pol["tool_require_confirm"] is tool_confirm
    assert pol["command_allowed"] is command


def test_identity_mismatch_forces_dead_regardless_of_age():
    """44016/54234 事故形态：对象错了比对象旧更危险，新鲜度不能救它。"""
    pol = tracking_policy({"tier": "fresh", "age_days": 0.01}, "mismatch")
    assert pol["level"] == "dead"
    assert pol["identity_forced"] is True
    assert pol["allow_tune"] is False
    assert pol["allow_gimbal"] is False
    assert pol["command_allowed"] is False
    assert "身份" in pol["label"]


def test_no_data_degrades_to_unknown():
    pol = tracking_policy({"tier": "fresh", "age_days": 0.01}, "no_data")
    assert pol["level"] == "unknown"
    assert pol["command_allowed"] is False


def test_missing_freshness_is_unknown_not_crash():
    pol = tracking_policy(None)
    assert pol["level"] == "unknown"
    assert pol["command_allowed"] is False


def _tle_with_epoch(epoch: datetime, catnr: int = 99999):
    doy = epoch.timetuple().tm_yday
    frac = (epoch.hour * 3600 + epoch.minute * 60 + epoch.second) / 86400.0
    line1 = (f"1 {catnr:05d}U 98067A   {epoch.year % 100:02d}{doy:03d}"
             f".{int(round(frac * 1e8)):08d}  .00011731  00000+0  21853-3 0  9991")
    line2 = (f"2 {catnr:05d}  51.6400 247.4627 0006703 130.5360 325.0288"
             f" 15.72125391{catnr % 100000:05d}")
    # TLE 解析要求两条线 strip 后都 ≥69 列；定长列（历元、编号）已就位，
    # 缺的尾部列用 0 补齐（校验和位不参与解析，仅占位）
    return line1.ljust(69), line2.ljust(69, "0")


def _tracker_with(name: str, epoch: datetime) -> SatelliteTracker:
    l1, l2 = _tle_with_epoch(epoch)
    tr = SatelliteTracker()
    assert tr.load_tle([name, l1, l2]) is True
    return tr


def test_tracker_dead_tle_blocks_tuning():
    tr = _tracker_with("TEST DEAD", datetime.now(timezone.utc) - timedelta(days=47))
    pol = tr.tracking_policy_for("TEST DEAD")
    assert pol["level"] == "dead"
    assert pol["allow_tune"] is False
    assert pol["command_allowed"] is False


def test_tracker_fresh_tle_allows_tuning():
    tr = _tracker_with("TEST FRESH", datetime.now(timezone.utc) - timedelta(hours=2))
    pol = tr.tracking_policy_for("TEST FRESH")
    assert pol["level"] == "fresh"
    assert pol["allow_tune"] is True
    assert pol["command_allowed"] is True


def test_tracker_unknown_name_is_blocked():
    tr = SatelliteTracker()
    pol = tr.tracking_policy_for("NO SUCH SATELLITE")
    assert pol["level"] == "unknown"
    assert pol["command_allowed"] is False


def test_tracker_policy_matches_pure_function():
    """唯一事实源：目录条目算出来的策略必须与纯函数逐键一致。"""
    tr = _tracker_with("TEST DEAD", datetime.now(timezone.utc) - timedelta(days=47))
    entry = next(c for c in tr._catalog if c["name"] == "TEST DEAD")
    expected = tracking_policy(
        {"tier": entry["tle_tier"], "age_days": entry["tle_age_days"],
         "reason": entry.get("accuracy_note") or ""},
        str(entry["identity_status"]))
    assert tr.tracking_policy_for("TEST DEAD") == expected


def test_current_position_exposes_policy():
    tr = SatelliteTracker()
    assert tr.current_position()["policy"]["level"] == "unknown"
    tr = _tracker_with("TEST DEAD", datetime.now(timezone.utc) - timedelta(days=47))
    assert tr.select_satellite("TEST DEAD") is True
    pos = tr.current_position()
    assert "policy" in pos
    assert pos["policy"]["command_allowed"] is False


@pytest.mark.parametrize("tier,age,label,hint,tag,short,severity,stale", [
    ("fresh", 0.5, "星历新鲜", "", "", "", "normal", False),
    ("usable", 2.0, "星历可用", "建议更新星历", "", "2 天前", "normal", False),
    ("expired", 40.0, "星历过期", "需确认后调谐（云台禁用）",
     "（TLE 过期 40 天）", "过期 40 天", "warning", True),
    ("dead", 100.0, "星历失效", "星历失效：请更新",
     "（TLE 失效 100 天）", "失效", "danger", True),
    ("unknown", None, "星历不可用", "星历不可用：请更新",
     "（TLE 年龄未知）", "年龄未知", "danger", True),
])
def test_display_fields_are_single_source(tier, age, label, hint, tag, short,
                                          severity, stale):
    """显示口径（标签/提示/短标签/颜色/是否陈旧）全部由策略给，桌面只读。"""
    pol = tracking_policy({"tier": tier, "age_days": age})
    assert pol["label"] == label
    assert pol["hint"] == hint
    assert pol["display_tag"] == tag
    assert pol["short_tag"] == short
    assert pol["severity"] == severity
    assert pol["stale"] is stale


def test_identity_mismatch_display_tag_is_explicit():
    """身份不符：标签必须点名「身份不符」，不能只说过期天数。"""
    pol = tracking_policy({"tier": "fresh", "age_days": 1.0}, "mismatch")
    assert pol["display_tag"] == "（TLE 身份不符）"
    assert pol["stale"] is True
    assert pol["severity"] == "danger"
    assert pol["allow_gimbal"] is False
