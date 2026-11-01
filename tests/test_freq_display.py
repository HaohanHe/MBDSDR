# SPDX-License-Identifier: MIT
"""
tests/test_freq_display.py
===========================
验证 desktop/freq_display.py —— 按位编辑频率大屏（P0-1，对标 SDR++/gqrx
radio dial；契约与 cpp/src/ui/freq_display_widget.h 一致）：

  1. 纯算术：place_value_hz / nudge_digit（含上下界钳位）/ truncate_below；
  2. 数字串格式化（9 位 "DDD.DDD.DDD"，超界截断）；
  3. 交互：悬停命中（digit_at）、滚轮/点击 ±1 发 frequency_edited、
     右键清零、无真值/禁用时不发信号；
  4. offscreen 绘制冒烟（有值/空态/禁用三态）。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_freq_display.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt, QPointF  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from freq_display import FreqDisplay, digits_of  # noqa: E402


class TestPureArithmetic:
    def test_place_values(self):
        assert FreqDisplay.place_value_hz(0) == 100_000_000
        assert FreqDisplay.place_value_hz(2) == 1_000_000
        assert FreqDisplay.place_value_hz(8) == 1
        with pytest.raises(IndexError):
            FreqDisplay.place_value_hz(9)

    def test_nudge_up_down(self):
        # 98.5 MHz，第 2 位 = MHz 位：+1 → 99.5 MHz
        assert FreqDisplay.nudge_digit(98_500_000, 2, +1) == 99_500_000
        assert FreqDisplay.nudge_digit(98_500_000, 2, -1) == 97_500_000
        # 第 8 位 = 1 Hz
        assert FreqDisplay.nudge_digit(100, 8, +1) == 101

    def test_nudge_clamps_to_bounds(self):
        assert FreqDisplay.nudge_digit(0, 8, -1, min_hz=0) == 0
        assert FreqDisplay.nudge_digit(1_999_999_999, 0, +1,
                                       max_hz=2_000_000_000) == 2_000_000_000

    def test_truncate_below(self):
        # 98.5321 MHz，右键第 2 位（MHz 位）→ 截到 98 MHz
        assert FreqDisplay.truncate_below(98_532_100, 2) == 98_000_000
        assert FreqDisplay.truncate_below(98_532_100, 5) == 98_532_000

    def test_digits_of(self):
        assert digits_of(98_500_000) == "098500000"
        assert digits_of(0) == "000000000"
        # 超 9 位的高位截断，不崩
        assert digits_of(6_000_000_000) == "000000000"


class TestWidget:
    @pytest.fixture()
    def disp(self):
        d = FreqDisplay()
        d.resize(500, 80)
        d.set_bounds(0, 2_000_000_000)
        d.set_frequency(98_500_000)
        return d

    def test_set_frequency_rounds_and_none(self, disp):
        assert disp.frequency() == 98_500_000
        assert disp.has_frequency
        disp.set_frequency(None)
        assert not disp.has_frequency
        disp.set_frequency(float("nan"))
        assert not disp.has_frequency  # NaN 视为无真值

    def test_digit_hit_test(self, disp):
        cells = disp._cells()
        digit_cells = [c for c in cells if c[2]]
        # 每个数字格的中心必须命中自己
        for _ch, rect, _is_d, idx in digit_cells:
            assert disp.digit_at(rect.center().x()) == idx
        assert disp.digit_at(-50) == -1

    def test_digit_delta_emits(self, disp):
        got = []
        disp.frequency_edited.connect(got.append)
        disp._apply_digit_delta(2, +1)
        assert got == [99_500_000]
        disp._apply_digit_delta(2, -1)
        assert got == [99_500_000, 97_500_000]

    def test_digit_delta_unchanged_no_emit(self, disp):
        got = []
        disp.frequency_edited.connect(got.append)
        disp.set_frequency(0)
        disp._apply_digit_delta(8, -1)   # 0 Hz 再减被钳到 0 = 无变化
        assert got == []

    def test_truncate_emits(self, disp):
        got = []
        disp.frequency_edited.connect(got.append)
        disp._apply_truncate(2)
        assert got == [98_000_000]

    def test_disabled_no_emit(self, disp):
        got = []
        disp.frequency_edited.connect(got.append)
        disp.setEnabled(False)
        disp._apply_digit_delta(2, +1)
        assert got == []

    def test_wheel_over_digit(self, disp):
        got = []
        disp.frequency_edited.connect(got.append)
        cells = disp._cells()
        target = [c for c in cells if c[2]][2]  # 第 2 位
        x = target[1].center().x()
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtCore import QPoint
        ev = QWheelEvent(QPointF(x, 40), disp.mapToGlobal(QPointF(x, 40)),
                         QPoint(0, 0), QPoint(0, 120),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                         Qt.ScrollPhase.NoScrollPhase, False)
        disp.wheelEvent(ev)
        assert got == [99_500_000]

    def test_click_upper_lower_half(self, disp):
        got = []
        disp.frequency_edited.connect(got.append)
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QEvent
        cells = disp._cells()
        x = [c for c in cells if c[2]][2][1].center().x()
        up = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, 10),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        disp.mousePressEvent(up)
        down = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(x, 70),
                           Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)
        disp.mousePressEvent(down)
        assert got == [99_500_000, 97_500_000]

    def test_paint_smoke_three_states(self, disp):
        assert not disp.grab().isNull()
        disp.set_frequency(None)
        assert not disp.grab().isNull()
        disp.set_frequency(1_500_000_000)
        disp.setEnabled(False)
        assert not disp.grab().isNull()
