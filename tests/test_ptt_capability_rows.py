# SPDX-License-Identifier: MIT
"""PTT 能力状态接线（docs/doppler-compensation.md §3.8.2 七行判定表）。

反证：修前 ``_tx_block_reason`` 只看 ``callable(write_tx)`` 与 ``supports_tx()``，
把「驱动级可发 / 本路径未接入」「硬件仅接收」「能力未知」「仅有声明」
一律压成「当前设备不支持发射（后端没有 TX 通道）」——把能说成不能同样是
假信息。本文件给 ``tx_capability()`` 七种取值，断言常驻文案原文、PTT 可点性
与警示有无，并钉住「未验证可点 + 常驻黄色条」与「无设备不给结论」。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

_LOGGER = logging.getLogger(__name__)


class _CapBackend:
    """假后端：能力矩阵完全由 ``tx_capability()`` 决定（真实后端同口径）。"""

    def __init__(self, cap):
        self._cap = dict(cap)

    def supports_tx(self):
        return bool(self._cap.get("declared"))

    def tx_capability(self):
        return dict(self._cap)

    def write_tx(self, iq):
        raise AssertionError("本用例不应真的发波")


_CASES = [
    ("row1_unverified",
     {"implemented": True, "declared": True, "ready": True, "verified": False},
     "本后端发射：驱动级已实现 / 真机未验证", True, True),
    ("row2_verified",
     {"implemented": True, "declared": True, "ready": True, "verified": True},
     "本后端发射：驱动级已实现 / 真机已验证", True, False),
    ("row3_declared_only",
     {"implemented": False, "declared": True, "ready": False, "verified": False},
     "本后端发射：仅有声明 / 未实现（不可发射）", False, False),
    ("row4_path_not_implemented",
     {"implemented": False, "declared": False, "ready": False, "verified": False,
      "driver_capable": True, "driver_tx_channels": 1,
      "blocked_reason": "driver_capable_path_not_implemented"},
     "本后端发射：驱动级可发 / 本路径未接入（不可发射）", False, False),
    ("row5_no_tx_channels",
     {"implemented": False, "declared": False, "ready": False, "verified": False,
      "driver_capable": False, "driver_tx_channels": 0,
      "blocked_reason": "no_tx_channels"},
     "本后端发射：本设备硬件不支持发射（驱动报告 0 个 TX 通道）", False, False),
    ("row6_unknown",
     {"implemented": False, "declared": False, "ready": False, "verified": False,
      "driver_capable": None, "blocked_reason": "driver_capability_unknown"},
     "本后端发射：能力未知（未连接/驱动未报告）", False, False),
    ("row7_not_implemented",
     {"implemented": False, "declared": False, "ready": False, "verified": False},
     "本后端发射：本后端尚未实现发射（该驱动没有 TX 代码路径）", False, False),
    # F1：implemented=true / ready=false（HackRF/USRP/Pluto 未连接时的真实状态）
    ("row8_implemented_not_connected",
     {"implemented": True, "declared": True, "ready": False, "verified": False,
      "connected": False},
     "本后端发射：已实现，但设备未连接（连上即可发射）", False, False),
]


@pytest.mark.parametrize("name,cap,text,clickable,warn", _CASES,
                         ids=[c[0] for c in _CASES])
def test_ptt_state_maps_seven_rows(name, cap, text, clickable, warn):
    from main_window import ptt_state

    got_text, got_clickable, got_warn = ptt_state(_CapBackend(cap))
    assert got_text == text, f"{name} 文案不符：{got_text}"
    assert got_clickable is clickable, f"{name} 可点性不符：{got_clickable}"
    assert got_warn is warn, f"{name} 警示不符：{got_warn}"


def test_only_hardware_limitation_says_no_tx_capability():
    """「没有 TX 通道」只能由驱动报告 0 通道得出，不能被前两步压出来。"""
    texts = {c[2] for c in _CASES}
    assert not any("没有 TX 通道" in t for t in texts)
    assert "TX 通道" in _CASES[4][2], "第 5 行必须给出驱动报告的真实原因"


def test_failure_branches_are_distinct_and_actionable():
    """F3：硬件不支持 / 软件没实现 / 本路径未接入必须是三句不同的行动建议。"""
    rows = {c[0]: c[2] for c in _CASES}
    hw = rows["row5_no_tx_channels"]
    sw = rows["row7_not_implemented"]
    path = rows["row4_path_not_implemented"]
    assert len({hw, sw, path}) == 3, "三种失败原因不得压成同一句"
    assert "硬件不支持发射" in hw and "尚未实现发射" not in hw
    assert "尚未实现发射" in sw and "硬件不支持" not in sw


def test_implemented_but_not_ready_is_not_reported_as_unimplemented():
    """F1：有 write_tx、只是没连设备——不能说成「未实现」。"""
    from main_window import ptt_state

    for cap in (
        {"implemented": True, "declared": True, "ready": False,
         "verified": False, "connected": False},
        {"implemented": True, "declared": True, "ready": False,
         "verified": False},
    ):
        text, clickable, warn = ptt_state(_CapBackend(cap))
        assert "已实现" in text, f"实现被抹掉：{text}"
        assert "未实现" not in text, f"能发被说成不能：{text}"
        assert clickable is False and warn is False


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(qtapp):
    """整模块复用同一个窗口：MainWindow 构造很贵。"""
    import main_window as mw
    from PySide6.QtTest import QTest
    w = mw.MainWindow()
    QTest.qWait(350)
    yield w
    try:
        w._teardown_connection()
    except Exception:
        # 测试夹具不静默：断开失败要留日志，便于定位残留状态。
        _LOGGER.exception("MainWindow teardown 失败")


def test_unverified_backend_keeps_ptt_clickable_with_amber_bar(window):
    """行 1：三后端现在就是这个真实状态——可点，但常驻黄色警示。"""
    w = window
    w._replay_timer = None
    w._active_sdr_backend = _CapBackend(_CASES[0][1])

    w._refresh_tx_button_state()

    assert w.tx_button.isEnabled(), "驱动级已实现但未验证：按钮可点"
    assert "人工确认" in w.tx_button.toolTip()
    assert w.tx_capability_label.text() == "本后端发射：驱动级已实现 / 真机未验证"
    assert "background-color" in w.tx_capability_label.styleSheet(), (
        "未验证必须是常驻黄色条，不能只放 tooltip")
    assert "没有 TX 通道" not in w.tx_button.toolTip()
    w._active_sdr_backend = None


def test_soapy_generic_path_is_not_reported_as_no_tx(window):
    """行 4：Soapy 通用路径没有 write_tx，但驱动能发——不许说成不能发。"""
    w = window
    w._replay_timer = None
    w._active_sdr_backend = _CapBackend(_CASES[3][1])

    w._refresh_tx_button_state()

    assert not w.tx_button.isEnabled()
    assert w.tx_capability_label.text() == (
        "本后端发射：驱动级可发 / 本路径未接入（不可发射）")
    assert "本路径未接入" in w.tx_button.toolTip()
    assert "没有 TX 通道" not in w.tx_button.toolTip()
    w._active_sdr_backend = None


def test_no_device_does_not_conclude_capability(window):
    """无设备：只说不确定，不冒充「后端没有 TX 通道」。"""
    w = window
    w._replay_timer = None
    w._active_sdr_backend = None

    w._refresh_tx_button_state()

    assert not w.tx_button.isEnabled()
    assert "未连接设备" in w.tx_button.toolTip()
    assert w.tx_capability_label.text() == "本后端发射：未连接设备（能力未知）"
    assert w.tx_capability_label.styleSheet() == ""
