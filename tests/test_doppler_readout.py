# SPDX-License-Identifier: MIT
"""多普勒补偿读数渲染规则（纯函数，无 Qt 窗口）。

数据源：SDRBackend.get_doppler_compensation_status() /
ReceiveChain.doppler_status()（同义字段）。
红线：applied_hz=None 一律显示 "—"，绝不许渲染成 0——
0 的语义是"算出来确实是 0"，null 的语义是"不是我施加/我不知道具体值"。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from main_window import format_doppler_status  # noqa: E402


def test_error_branch_wins_over_everything():
    st = {"error": "TLE 超过 14 天，预测不可信",
          "applied_by": "python", "applied_hz": 12.0, "predicted_hz": 12.0}
    assert format_doppler_status(st) == "补偿失败"


def test_backend_branch_dash_with_prediction_note():
    st = {"applied_by": "backend", "applied_hz": None, "predicted_hz": 123.0}
    text = format_doppler_status(st)
    assert "—（由后端施加）" in text
    assert "预测 +123 Hz" in text
    assert "0 Hz" not in text  # null 不许变 0


def test_python_branch_signed_amount_and_settling():
    st = {"applied_by": "python", "applied_hz": -42.1, "predicted_hz": -42.1}
    assert format_doppler_status(st) == "-42.1 Hz（Python 基带）"
    st["settling"] = True
    assert format_doppler_status(st) == "-42.1 Hz（Python 基带）（切换中）"


def test_python_branch_null_amount_shows_dash_not_zero():
    st = {"applied_by": "python", "applied_hz": None, "predicted_hz": 20.0}
    text = format_doppler_status(st)
    assert "—（Python 基带）" in text
    assert "0 Hz" not in text


def test_tuner_branch_when_enabled_with_prediction():
    st = {"applied_by": "none", "application": "tuner", "enabled": True,
          "applied_hz": None, "predicted_hz": -5000.0}
    text = format_doppler_status(st)
    assert "—（由调谐器施加）" in text
    assert "0 Hz" not in text


def test_other_branch_zero_means_really_zero():
    st = {"applied_by": "none", "applied_hz": 0.0,
          "reason": "无跟踪目标，未补偿"}
    text = format_doppler_status(st)
    assert "0 Hz（未补偿）" in text
    assert "无跟踪目标" in text


def test_other_branch_unknown_amount_never_zero():
    # applied_by=none 但 applied_hz 未知：仍然只能显示 "—"
    st = {"applied_by": "none", "applied_hz": None, "reason": "未连接设备"}
    text = format_doppler_status(st)
    assert "—（未补偿）" in text
    assert "0 Hz" not in text


def test_empty_status_is_unknown_not_zero():
    assert "0 Hz" not in format_doppler_status({})
