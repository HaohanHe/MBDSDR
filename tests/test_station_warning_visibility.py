# SPDX-License-Identifier: MIT
"""station_warning 的 AI 文本可见性（面板可见性见 test_pass_ui_warnings.py）。

修前红：station_warning 只进返回字段/degraded，首屏一个字不提。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)


def test_ai_passes_text_starts_with_station_warning(monkeypatch, isolated_profile):
    """SDR 工具的过境文本必须把站址 warning 放在首屏，而非只藏在 degraded。"""
    import mbdsdr_ai.sdr_tools as st
    from mbdsdr_ai import orbit

    warning = ("null_island_0_0（显式输入坐标恰为 0,0（几内亚湾）；"
               "照常计算，但位置可疑）")
    rep = {
        "n_passes": 0,
        "passes": [],
        "degraded": [{"satellite": "（站址）", "catnr": 0,
                      "reason": "station_warning", "detail": warning}],
        "station": {"station_warning": warning},
        "tle_policy": {},
    }
    monkeypatch.setattr(orbit, "predict_passes_report",
                        lambda *a, **k: rep)
    text = st._satellite_passes({"latitude": 0.0, "longitude": 0.0,
                                 "hours": 1.0})
    assert text.startswith("站址提醒："), f"首屏看不到站址 warning：{text[:120]!r}"
    assert "null_island_0_0" in text and "几内亚湾" in text
