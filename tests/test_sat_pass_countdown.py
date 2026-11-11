# SPDX-License-Identifier: MIT
"""过境倒计时（机制 C）：AOS/LOS 倒计时 + UTC 显示开关。

验收口径：
  1. 倒计时由 ``aos/los − now`` 派生，AOS 前数到 AOS、过 AOS 后数到 LOS；
  2. now 只有一个来源——宿主传入的同帧 ``now_unix``，否则取全局
     ``get_time_engine().now_unix()``；引擎不可用时明说暂停，**绝不回退墙钟**；
  3. UTC 开关只改 AOS/LOS 绝对时刻的显示口径，倒计时数字不变；
  4. 过境列表只由注入的 provider 产生（不写第二套轨道计算），
     空窗口/失败在冷却期内不反复触发 skyfield；
  5. 负时长（时钟回跳）如实告警，不画假倒计时；换星不复用别星列表。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

#: 固定时基（2027-01-15T08:00:00Z），不依赖跑测时的真实时钟。
BASE = 1_800_000_000.0


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


def _panel(names=("ISS",)):
    from desktop.sat_track_panel import SatTrackPanel
    p = SatTrackPanel()
    p.populate_satellites(list(names))
    return p


def test_aos_then_los_countdown_from_same_now(qapp, isolated_profile):
    """AOS 前数 AOS，过 AOS 后数 LOS；同一帧只向 provider 取一次。"""
    p = _panel()
    calls = []

    def provider(name):
        calls.append(name)
        return [{"aos_unix": BASE + 100, "los_unix": BASE + 400, "max_alt": 42.0}]

    p.set_pass_provider(provider)

    p.update_position({"valid": False}, now_unix=BASE)
    assert p.countdown_label.text() == "AOS 倒计时 01:40", p.countdown_label.text()
    assert "AOS" in p.pass_abs_label.text()
    assert "LOS" in p.pass_abs_label.text()

    p.update_position({"valid": False}, now_unix=BASE + 100)
    assert p.countdown_label.text() == "LOS 倒计时 05:00", p.countdown_label.text()
    assert "峰值仰角 42°" in p.pass_abs_label.text()

    assert calls == ["ISS"], f"同一颗星的同一窗口应只取一次：{calls}"


def test_utc_toggle_changes_display_not_countdown(qapp, isolated_profile):
    """UTC 开关只改绝对时刻显示；倒计时数字与内部时基不变。"""
    p = _panel()
    p.set_pass_provider(
        lambda name: [{"aos_unix": BASE + 100, "los_unix": BASE + 400}])
    p.update_position({"valid": False}, now_unix=BASE)

    utc_text = p.pass_abs_label.text()
    assert "UTC" in utc_text, f"默认应显示 UTC 口径：{utc_text!r}"
    assert p.countdown_label.text() == "AOS 倒计时 01:40"

    p.utc_check.setChecked(False)
    assert p.countdown_label.text() == "AOS 倒计时 01:40", \
        "UTC 开关不得改变倒计时数字（它只改显示口径）"
    local_text = p.pass_abs_label.text()
    assert local_text != utc_text, "关闭 UTC 后绝对时刻文本没有变化"
    assert "UTC" not in local_text, f"本地口径不应带 UTC 后缀：{local_text!r}"
    # 本地显示走 time_basis.format_local：ISO 格式且带时区偏移，可往返验证。
    import re
    assert re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}",
                     local_text), f"本地显示应是带偏移的 ISO：{local_text!r}"

    p.utc_check.setChecked(True)
    assert p.pass_abs_label.text() == utc_text, "切回 UTC 应恢复原文本"


def test_empty_window_fetched_once_per_cooldown(qapp, isolated_profile):
    """窗口内无过境：冷却期内不反复触发预测，到期后才重取。"""
    p = _panel()
    calls = []
    p.set_pass_provider(lambda name: calls.append(name) or [])

    for offset in (0, 10, 59):
        p.update_position({"valid": False}, now_unix=BASE + offset)
    assert calls == ["ISS"], f"冷却期内重复取数：{calls}"
    assert p.countdown_label.text() == "窗口内无过境"

    p.update_position({"valid": False}, now_unix=BASE + 61)
    assert calls == ["ISS", "ISS"], f"冷却到期后应重取：{calls}"


def test_negative_window_is_reported_not_counted(qapp, isolated_profile):
    """负时长（los < aos）必须告警并拒绝倒计时，不画假数字。"""
    p = _panel()
    p.set_pass_provider(
        lambda name: [{"aos_unix": BASE + 300, "los_unix": BASE + 100}])
    p.update_position({"valid": False}, now_unix=BASE)

    assert "倒计时" not in p.countdown_label.text(), p.countdown_label.text()
    assert p.countdown_label.text() == "窗口内无过境"
    assert "set_before_rise" in p.pass_abs_label.text(), p.pass_abs_label.text()


def test_missing_time_engine_pauses_instead_of_wall_clock(qapp, isolated_profile,
                                                          monkeypatch):
    """引擎不可用：明说暂停——绝不拿 time.time() 另造第二个时间基准。"""
    from desktop import sat_track_panel as stp

    p = _panel()
    monkeypatch.setattr(stp.SatTrackPanel, "_engine_now_unix",
                        staticmethod(lambda: None))
    p.update_position({"valid": False})      # 不传 now：只能走时间引擎

    assert "时间引擎不可用" in p.pass_abs_label.text(), p.pass_abs_label.text()
    assert "倒计时" not in p.countdown_label.text()


def test_switching_satellite_does_not_reuse_other_sat_list(qapp, isolated_profile):
    """换星后必须重取，不能把上一颗星的 AOS/LOS 拿来倒计时。"""
    p = _panel(("ISS", "NOAA 15"))
    seen = []

    def provider(name):
        seen.append(name)
        if name == "ISS":
            return [{"aos_unix": BASE + 100, "los_unix": BASE + 400}]
        return [{"aos_unix": BASE + 1000, "los_unix": BASE + 1300}]

    p.set_pass_provider(provider)
    p.update_position({"valid": False}, now_unix=BASE)
    assert p.countdown_label.text() == "AOS 倒计时 01:40"

    p.sat_combo.setCurrentIndex(1)           # 切到 NOAA 15
    p.update_position({"valid": False}, now_unix=BASE)
    assert p.countdown_label.text() == "AOS 倒计时 16:40", p.countdown_label.text()
    assert seen[:2] == ["ISS", "NOAA 15"], f"换星没有重取：{seen}"


def test_invalidate_forces_refetch_on_next_frame(qapp, isolated_profile):
    """TLE 更新/站址变更后宿主调用 invalidate_passes：下一帧必须重取。"""
    p = _panel()
    calls = []
    p.set_pass_provider(lambda name: calls.append(name) or [])
    p.update_position({"valid": False}, now_unix=BASE)
    assert calls == ["ISS"]

    p.invalidate_passes()
    p.update_position({"valid": False}, now_unix=BASE + 1)
    assert calls == ["ISS", "ISS"], f"invalidate 后没有重取：{calls}"
