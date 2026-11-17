# SPDX-License-Identifier: MIT
"""星历陈旧度对用户可见：日期 + 档位 + 下一步提示 + 云台入口闸门。

口径（2026-11-11 用户要求）：
  - usable（本机缓存 2 天前）必须显示「建议更新星历」，但**不阻断**调谐；
  - 30 天以上（expired/dead）显示需确认/请更新，云台入口禁用；
  - 措辞只在 ``mbdsdr_ai.tle_integrity`` 维护：改文案不动桌面层。

反证：把日期换成 40 天前，面板从「建议更新」变成「需确认后调谐（云台禁用）」，
且 ``_tle_gimbal_block_reason`` 给出禁用理由。
"""
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdsdr_ai import tle_integrity  # noqa: E402
from mbdsdr_ai.tle_integrity import tracking_policy  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture()
def panel(qapp):
    from desktop.sat_track_panel import SatTrackPanel
    return SatTrackPanel()


def test_usable_shows_refresh_hint_without_blocking(panel):
    panel.set_tle_date("2026-11-09", age_days=2.0, tier="usable")
    text = panel.tle_date_label.text()
    assert "星历可用" in text
    assert "TLE 已 2 天" in text
    assert "建议更新星历" in text
    assert "需确认" not in text
    # 不阻断：调谐入口与刷新入口都可点
    assert panel.tle_refresh_btn.isEnabled()
    assert panel.update_tle_btn.isEnabled()


def test_expired_40_days_requires_confirm(panel):
    """改前（无档位/只给日期）不会出现这两个词；改后必须出现。"""
    panel.set_tle_date("2026-10-02", age_days=40.0, tier="expired")
    text = panel.tle_date_label.text()
    assert "星历过期" in text
    assert "TLE 已 40 天" in text
    assert "需确认后调谐（云台禁用）" in text
    assert "建议更新星历" not in text
    assert panel.tle_refresh_btn.isEnabled()


def test_wording_lives_only_in_tle_integrity(panel, monkeypatch):
    """改措辞只改一处：桌面层读 policy 字段，不存第二份文案。"""
    monkeypatch.setitem(tle_integrity.TRACKING_HINTS, "usable", "请刷新星历")
    panel.set_tle_date("2026-11-09", age_days=2.0, tier="usable")
    text = panel.tle_date_label.text()
    assert "请刷新星历" in text
    assert "建议更新星历" not in text


def test_no_tle_says_cannot_predict_not_no_passes(panel):
    """无 TLE：日期行与倒计时都写「无 TLE，无法预报」，不说「窗口内无过境」。"""
    panel.set_tle_date("", age_days=None, tier=None)
    assert "无 TLE，无法预报" in panel.tle_date_label.text()

    panel.populate_satellites(["NOAA 19"])

    def _must_not_be_called(_name):
        raise AssertionError("没有 TLE 时不应向 provider 要过境列表")

    panel.set_pass_provider(_must_not_be_called)
    panel._update_countdown(1_000_000.0)
    assert "无 TLE，无法预报" in panel.countdown_label.text()
    assert "窗口内无过境" not in panel.countdown_label.text()


def test_tle_meta_line_exposes_source_epoch_fetch_and_age(panel):
    """上游 fetch_tle_record 的 dict 可直接渲染：来源/历元/抓取/已 N 天。"""
    panel.set_tle_meta({
        "line1": "1 33591U 09005A   26268.85039999  .00000028  00000+0  38838-4 0  9994",
        "upstream_source": "celestrak",
        "epoch_utc": "2026-09-25T20:24:34Z",
        "fetched_utc": "2026-09-26T00:00:00Z",
        "age_days": 2.0,
        "stale": False,
    })
    text = panel.tle_date_label.text()
    assert "TLE 已 2 天" in text
    assert "来源 celestrak" in text
    assert "历元 2026-09-25T20:24:34Z" in text
    assert "抓取 2026-09-26T00:00:00Z" in text


class _TrackerStub:
    def __init__(self, tier: str, age: float):
        self._tier = tier
        self._age = age

    def list_satellites(self):
        return [{"name": "NOAA 19", "tle_tier": self._tier,
                 "tle_age_days": self._age}]

    def tracking_policy_for(self, name=None):
        return tracking_policy({"tier": self._tier, "age_days": self._age})


def _window_stub(tracker):
    from desktop.main_window import MainWindow

    stub = types.SimpleNamespace(
        _sky_selected_name=lambda: "NOAA 19",
        _loop_sat_tracker=tracker,
        _sat_tracking=False,            # 未跟踪 → 回落天空选中星
    )
    # 口径选择复用真实实现（不在测试里再写一份「跟踪目标 vs 天空选中」）。
    stub._gimbal_policy_target = (
        lambda: MainWindow._gimbal_policy_target(stub))
    return stub


def test_gimbal_entry_blocked_when_tle_expired(qapp):
    from desktop.main_window import MainWindow
    reason = MainWindow._tle_gimbal_block_reason(
        _window_stub(_TrackerStub("expired", 40.0)))
    assert "星历过期" in reason
    assert "云台" in reason and "禁止" in reason


def test_gimbal_entry_allowed_when_tle_fresh(qapp):
    from desktop.main_window import MainWindow
    assert MainWindow._tle_gimbal_block_reason(
        _window_stub(_TrackerStub("fresh", 0.3))) == ""
