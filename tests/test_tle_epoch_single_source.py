# SPDX-License-Identifier: MIT
"""TLE 历元年龄/未来判定只能有一个算法（A01 单一产生点 / A12 同名行为一致）。

同一份 TLE 有两个出口：
  ``mbdsdr_ai.tle_integrity.freshness``      —— 分级、过境可用性
  ``mbdsdr_ai.time_basis.tle_epoch_report``   —— 历元报告、时钟可疑判定

过去的行为是两边各算一遍：``freshness`` 把未来历元的年龄钳到 0 并判
``fresh``，``tle_epoch_report`` 保留负年龄并按 ``future_tol_days`` 判
``plausible=False``——同一份 TLE 在两个出口年龄不同、可信度不同，且阈值
两套（``2.0`` 字面量各写一次）。现在两边共用
``tle_epoch.TLE_FUTURE_TOL_DAYS``，``freshness`` 额外暴露有符号年龄
``age_days_signed`` 与 ``epoch_implausible``，本文件把这层一致性钉住。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai.tle_epoch import TLE_FUTURE_TOL_DAYS  # noqa: E402
from mbdsdr_ai.tle_integrity import freshness  # noqa: E402
from mbdsdr_ai.time_basis import tle_epoch_report  # noqa: E402

#: 历元字符串（YYDDD.DDDDDDDD）：过去 40 天 / 现在 / 未来 1.5 天 / 未来 10 天。
CASES = ["25327.00000000", "26001.00000000", "26002.50000000", "26011.00000000"]


def _line1(epoch: str) -> str:
    return ("1 25544U 98067A   " + epoch +
            "  .00000000  00000-0  00000-0 0  9999")


def _now():
    from datetime import datetime, timezone
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("epoch", CASES)
def test_age_and_future_flag_agree_between_the_two_entries(epoch):
    line1 = _line1(epoch)
    fresh = freshness(line1, now=_now())
    report = tle_epoch_report(line1, now=_now())
    assert fresh["age_days_signed"] == report["age_days"], (
        f"同一历元两个出口年龄不同：freshness={fresh['age_days_signed']} "
        f"epoch_report={report['age_days']}")
    assert fresh["epoch_in_future"] == report["epoch_in_future"]
    assert fresh["epoch_implausible"] == (not report["plausible"])


def test_future_epoch_beyond_tolerance_is_flagged_not_silently_fresh():
    far_future = _line1("26011.00000000")     # +10 天
    fresh = freshness(far_future, now=_now())
    report = tle_epoch_report(far_future, now=_now())
    assert fresh["epoch_in_future"] is True
    assert fresh["epoch_implausible"] is True
    assert report["plausible"] is False
    # 分级仍按「未来不降级」处理（界面不出现负数天），但可疑标记必须同时给出。
    assert fresh["age_days"] == 0.0
    assert fresh["tier"] == "fresh"


def test_tolerance_boundary_is_shared():
    """容差必须同源：1.5 天以内两边都判可信，跨过 2.0 天两边都判可疑。"""
    for offset_days, expected_plausible in ((1.5, True), (2.5, False)):
        doy = 1 + offset_days
        line1 = _line1(f"{26:02d}{doy:012.8f}")
        fresh = freshness(line1, now=_now())
        report = tle_epoch_report(line1, now=_now())
        assert report["plausible"] is expected_plausible, offset_days
        assert fresh["epoch_implausible"] is (not expected_plausible), offset_days
        assert abs(report["age_days"]) == pytest.approx(offset_days, abs=1e-6)
    assert TLE_FUTURE_TOL_DAYS == 2.0
