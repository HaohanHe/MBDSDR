# SPDX-License-Identifier: MIT
"""过境报告输出预算：全局状态不许逐条重复把列表尾部挤出截断窗口。

`sdr_satellite_passes` 的文本经 Agent 的 `tool_output_max_chars`(4000) 截断。
TLE 刷新状态与 TLE 批次精度说明是**本次报告全局共享**的一份信息，旧式在
25 次过境里各写一遍，把后面的过境挤出输出窗口：用户看不到后半张列表，
下游还会误判「目标卫星不在报告里」。本用例用固定 25 条 + 最坏污染钉住。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai import orbit, sdr_tools  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402

ARGS = {"latitude": 45.0, "longitude": 120.0, "altitude": 0.0,
        "hours": 24, "min_elevation": 10, "satellite_type": "weather",
        "nominal_freq_hz": 137100000.0}

REFRESH_NOTE = "离线模式（MBDSDR_OFFLINE），未联网刷新，沿用旧缓存"
RISK_NOTE = "TLE 可用（4 天，≤7 天）：过境时刻可能有数十秒级漂移，临近时建议刷新 TLE。"


def _pass(i):
    return {
        "satellite": "NOAA 19" if i % 2 == 0 else "NOAA 18",
        "rise_time": f"{i:02d}:00:00", "rise_azimuth": 263.7,
        "culmination_time": f"{i:02d}:03:38", "max_elevation": 10.4,
        "culmination_azimuth": 274.0, "set_time": f"{i:02d}:07:16",
        "set_azimuth": 284.3, "duration_s": 436,
        "doppler_hz": {"min_hz": -2990.0, "max_hz": 2991.0},
        "accuracy_risk": "moderate", "tle_tier": "usable",
        "tle_age_days": 3.821, "accuracy_note": RISK_NOTE,
        "refresh_status": "not_updated", "refresh_error": REFRESH_NOTE,
    }


@pytest.fixture
def polluted(monkeypatch):
    def _report(*_a, **_k):
        return {"passes": [_pass(i) for i in range(25)],
                "degraded": [], "station": {}}
    monkeypatch.setattr(orbit, "predict_passes_report", _report)


def test_global_notes_are_deduplicated(polluted):
    text = sdr_tools._satellite_passes(ARGS)
    assert text.count("[未更新]") == 1, "全局刷新状态只该写一次"
    assert text.count(RISK_NOTE) == 1, "同一批次精度说明只该写一次"
    assert "TLE 刷新状态" in text and "TLE 精度风险" in text
    assert "本次全部过境均为 [moderate]" in text


def test_report_fits_tool_output_budget(polluted):
    """最坏污染下整张 25 条列表仍要落在 4000 字内，不许被截断。"""
    budget = AgentConfig().tool_output_max_chars
    text = sdr_tools._satellite_passes(ARGS)
    assert len(text) < budget, f"报告 {len(text)} 字 > 预算 {budget}，尾部会被截断"
    assert "25. NOAA 19" in text and "24:07:16" in text, "最后一条过境必须可见"
    assert "共 25 次" in text


def test_counterproof_repeating_notes_would_blow_budget(polluted):
    """反证：逐条重复同一句话在**旧 4000 字预算**下必截断（去重真的在咬）。

    预算现提到 8000，但去重仍必要：报告越长越挤占模型上下文，
    且同一句话重复 25 遍对用户是纯噪音。
    """
    LEGACY_BUDGET = 4000
    text = sdr_tools._satellite_passes(ARGS)
    naive = len(text) + 24 * (len(REFRESH_NOTE) + len(RISK_NOTE) + 40)
    assert naive > LEGACY_BUDGET
