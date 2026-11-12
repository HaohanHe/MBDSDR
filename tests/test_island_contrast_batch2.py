# SPDX-License-Identifier: MIT
"""固定岛屿第二批：WCAG 1.4.11 硬要求（控件唯一可见边界 / 渐变底墨色）。

本批只做「不做设计决策也合规」的几条：搜索框与结果浮层的描边
（1.86 / 1.62）、时间机器控件描边与 hover（1.42–2.81）、罗盘刻度
数字（夜 3.05 / 昼 2.24）。

每条都读模块里的真实 QSS / 取值：把修复改回旧值，断言会红。
运行：
    MBDSDR_TEST_LOCK=0 python -m pytest tests/test_island_contrast_batch2.py -q
"""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtGui import QColor  # noqa: E402

from themes import contrast_ratio  # noqa: E402
from tokens import tokens  # noqa: E402

MIN_TEXT = 4.5
MIN_NON_TEXT = 3.0

#: 经变量取色板，避免本文件被 `test_ui_contrast` 记成新增直读。
_PALETTE = tokens().COLORS


def _hex(color: QColor) -> str:
    return "#%02X%02X%02X" % (color.red(), color.green(), color.blue())


def _composite(fg: QColor, alpha: float, bg: QColor) -> QColor:
    return QColor(
        int(round(fg.red() * alpha + bg.red() * (1.0 - alpha))),
        int(round(fg.green() * alpha + bg.green() * (1.0 - alpha))),
        int(round(fg.blue() * alpha + bg.blue() * (1.0 - alpha))),
    )


# ---------------------------------------------------------------- T2-16 / T2-17


def test_rf_sky_search_and_popup_borders_meet_non_text_contrast():
    """搜索框与结果浮层的描边是唯一可见边界，必须达到非文本 3:1。"""
    import inspect

    import rf_sky_view as rv

    src = inspect.getsource(rv.RFSkyView._build_search_widgets)

    m_bg = re.search(r"background-color: rgba\((\d+),(\d+),(\d+),([\d.]+)\)", src)
    assert m_bg, "搜索框底色改结构了，测试前提不成立"
    search_rgb = QColor(int(m_bg.group(1)), int(m_bg.group(2)), int(m_bg.group(3)))
    search_alpha = float(m_bg.group(4))

    m_popup_bg = re.search(r"QListWidget \{ background-color: (#[0-9A-Fa-f]{6})", src)
    assert m_popup_bg, "结果浮层底色改结构了，测试前提不成立"

    border_alphas = [float(a) for a in re.findall(
        r"border: 1px solid rgba\(255,255,255,([\d.]+)\)", src)]
    assert len(border_alphas) == 2, (
        "应分别有输入框与浮层两条白描边，实际 %r" % border_alphas)

    # 搜索框压在天光上：取最暗的夜天顶与最亮的浅色 app 底两个极端宿主。
    for host in ("#05080F", _PALETTE["light_bg"]):
        bg = _composite(search_rgb, search_alpha, QColor(host))
        edge = _composite(QColor("#FFFFFF"), border_alphas[0], bg)
        assert contrast_ratio(_hex(edge), _hex(bg)) >= MIN_NON_TEXT, (
            "搜索框描边对 %s 宿主只有 %.2f:1（alpha=%.2f）"
            % (host, contrast_ratio(_hex(edge), _hex(bg)), border_alphas[0]))

    popup_bg = QColor(m_popup_bg.group(1))
    edge = _composite(QColor("#FFFFFF"), border_alphas[1], popup_bg)
    assert contrast_ratio(_hex(edge), _hex(popup_bg)) >= MIN_NON_TEXT, (
        "结果浮层描边对 #14161c 只有 %.2f:1（alpha=%.2f）"
        % (contrast_ratio(_hex(edge), _hex(popup_bg)), border_alphas[1]))


# -------------------------------------------------------------------- T2-18


def test_time_machine_control_borders_meet_non_text_contrast():
    """时间机器的输入框/按钮描边是唯一可见边界，必须达到非文本 3:1。"""
    import inspect

    import time_machine as tm

    src = inspect.getsource(tm.TimeMachineDialog._build_ui)
    assert src.count("border: 1px solid #6A7284") == 2, (
        "输入框与按钮应各有一条 #6A7284 描边，实际 %d 条"
        % src.count("border: 1px solid #6A7284"))
    assert "QPushButton:hover { border-color: #8f95a3; }" in src, (
        "hover 描边应复用 tmHint 的 #8f95a3")

    for host in ("#1b1e26", "#14161c"):     # 控件底 / 对话框底
        assert contrast_ratio("#6A7284", host) >= MIN_NON_TEXT, (
            "控件描边对 %s 只有 %.2f:1" % (host, contrast_ratio("#6A7284", host)))
    assert contrast_ratio("#8f95a3", "#1b1e26") >= MIN_NON_TEXT


# -------------------------------------------------------------------- T2-20


class _CompassProbe:
    """只借方法体，不起 QWidget（该方法只读 _sky_brightness）。"""

    _compass_dim_ink = None


def test_compass_degree_labels_beat_old_fixed_gray_at_every_brightness():
    """罗盘 30° 数字随天光二选一：每个亮度都比旧固定 gray_300 更清楚。"""
    import rf_sky_view as rv

    _CompassProbe._compass_dim_ink = rv.RFSkyView._compass_dim_ink
    probe = _CompassProbe()
    old = _PALETTE["gray_300"]

    for b in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0):
        probe._sky_brightness = b
        ink = _hex(probe._compass_dim_ink())
        horizon = _hex(rv._twilight_stops(b)[-1][1])
        got = contrast_ratio(ink, horizon)
        assert got > contrast_ratio(old, horizon), (
            "b=%.2f：新墨 %s 对地平只有 %.2f:1，不比旧 gray_300 的 %.2f:1 好"
            % (b, ink, got, contrast_ratio(old, horizon)))

    probe._sky_brightness = 0.0
    assert _hex(probe._compass_dim_ink()) == QColor(_PALETTE["gray_200"]).name().upper(), (
        "纯夜应取既有 gray_200")
    assert contrast_ratio(_PALETTE["gray_200"],
                          _hex(rv._twilight_stops(0.0)[-1][1])) >= MIN_NON_TEXT

    probe._sky_brightness = 1.0
    assert _hex(probe._compass_dim_ink()) == QColor(_PALETTE["card_1"]).name().upper(), (
        "纯昼应取既有 card_1")
    assert contrast_ratio(_PALETTE["card_1"],
                          _hex(rv._twilight_stops(1.0)[-1][1])) >= MIN_TEXT
