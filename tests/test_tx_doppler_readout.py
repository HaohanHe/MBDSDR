# SPDX-License-Identifier: MIT
"""发射频点读数：五类文案 + null 不渲染成 0（纯函数，无窗口）。

数据源契约：``mbdsdr_ai.sdr_tools.resolve_tx_doppler(nominal_hz, None)``
→ ``(freq_hz, note)``。UI 只渲染 note，**不自己算 Δ**。

红线条目：
  * ``null``（未知）一律显示「—」，绝不渲染成 0——0 的语义是「确实算出来是 0」；
  * 发射失败只出「未发射（原因）」，不得残留「已预偏」；
  * MHz 6 位小数（1 kHz 级预偏否则根本看不见），Δ 保留 0.1 Hz。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from main_window import format_tx_doppler  # noqa: E402

NOMINAL_HZ = 145_800_000.0
FREQ_HZ = 145_799_027.0
DELTA_HZ = -973.0
TARGET = "ISS (ZARYA)"


def _applied_note(mode="auto"):
    return {
        "requested": None if mode == "auto" else True,
        "mode": mode,
        "applied": True,
        "nominal_hz": NOMINAL_HZ,
        "freq_hz": FREQ_HZ,
        "delta_hz": DELTA_HZ,
        "predicted_hz": -DELTA_HZ,
        "target": TARGET,
        "reason": "",
    }


def _unapplied_note(reason):
    return {
        "requested": False if "已关闭" in reason else None,
        "mode": "off" if "已关闭" in reason else "auto",
        "applied": False,
        "nominal_hz": NOMINAL_HZ,
        "freq_hz": NOMINAL_HZ,
        "delta_hz": 0.0,
        "reason": reason,
    }


def test_applied_auto_shows_six_decimals_and_target():
    text = format_tx_doppler(_applied_note("auto"))
    assert "发波 145.799027 MHz（已预偏 -973.0 Hz，自动）" in text
    assert "标称 145.800000 MHz，目标 ISS (ZARYA)" in text


def test_applied_on_is_labelled_explicit():
    text = format_tx_doppler(_applied_note("on"))
    assert "发波 145.799027 MHz（已预偏 -973.0 Hz，显式）" in text
    assert "标称 145.800000 MHz，目标 ISS (ZARYA)" in text


def test_unapplied_off_reason_is_shortened():
    text = format_tx_doppler(_unapplied_note(
        "已关闭（doppler_precomp=false，严格按标称频点发射）"))
    assert "发波 145.800000 MHz（未预偏：已关闭）" in text
    assert "标称 145.800000 MHz" in text
    assert "已预偏" not in text


def test_unapplied_no_target_reason_is_shortened():
    text = format_tx_doppler(_unapplied_note("无目标，未预偏"))
    assert "发波 145.800000 MHz（未预偏：无目标）" in text
    assert "标称 145.800000 MHz" in text


def test_unapplied_backend_takeover_reason_is_shortened():
    text = format_tx_doppler(_unapplied_note(
        "后端补偿已接管，未预偏（避免双施加）"))
    assert "发波 145.800000 MHz（未预偏：后端补偿已接管）" in text
    assert "标称 145.800000 MHz" in text


def test_failed_transmit_never_claims_precompensated():
    text = format_tx_doppler(_applied_note(), failed_reason="发射门未通过（需人工确认）")
    assert text == "未发射（发射门未通过（需人工确认））"
    assert "已预偏" not in text
    assert "145.799027" not in text


def test_replay_keeps_original_frequency():
    assert format_tx_doppler(_applied_note(), replay=True) == "未预偏（回放，保持原频率）"


def test_null_note_shows_dash_never_zero():
    assert format_tx_doppler(None) == "—"
    assert format_tx_doppler({}) == "—"
    text = format_tx_doppler({"nominal_hz": None, "freq_hz": None})
    assert text == "—"
    assert "0.000000" not in text
    assert "0.0 Hz" not in text
