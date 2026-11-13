# SPDX-License-Identifier: MIT
"""频率输入的歧义写法必须拒绝并说明，不许猜。

旧行为：`_parse_freq_text` 把逗号/空格全部删掉，于是 `145,5` 被读成
1455（MHz）→ 1.455 GHz，是用户本意 145.5 MHz 的 10 倍；`145 5` 同理。
现在：逗号只接受严格千分位（`145,500,000`），其余含逗号/内部空格的写法
抛 `AmbiguousFrequencyInput`，`_on_freq_input` 弹框写明两种可能与正确写法，
不下发调谐。

反证：把 `_parse_freq_text` 换回 `replace(",", "").replace(" ", "")` 版本，
`test_comma_decimal_is_rejected` / `test_space_separator_is_rejected` 必红。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import control_panel as cp_mod  # noqa: E402


def _panel():
    cp = cp_mod.ControlPanel()
    cp.set_sdr_connected(True)
    tuned = []
    cp.tune_sdr_requested.connect(lambda hz, mode: tuned.append(hz))
    return cp, tuned


def _warnings(monkeypatch):
    got = []
    monkeypatch.setattr(cp_mod.QMessageBox, "warning",
                        lambda *a, **k: got.append((a, k)))
    return got


def test_comma_decimal_is_rejected(monkeypatch):
    """`145,5`：拒绝、弹框写明两种可能、不下发调谐。"""
    cp, tuned = _panel()
    got = _warnings(monkeypatch)
    cp.freq_input.setText("145,5")
    cp._on_freq_input()

    assert tuned == [], "有歧义的输入不许调谐（旧实现会调到 1.455 GHz）"
    assert len(got) == 1, "必须出现一次可见提示，而不是只闪红框"
    text = got[0][0][2]
    assert "145.5 MHz" in text and "145,500 kHz" in text
    assert "145.5M" in text and "145500k" in text


def test_space_separator_is_rejected(monkeypatch):
    """`145 5`：同样拒绝，不许猜成 1455 MHz。"""
    cp, tuned = _panel()
    got = _warnings(monkeypatch)
    cp.freq_input.setText("145 5")
    cp._on_freq_input()

    assert tuned == []
    assert len(got) == 1


def test_strict_grouping_still_accepted(monkeypatch):
    """严格千分位是明确写法：145,500,000 Hz = 145.5 MHz，可调谐。"""
    cp, tuned = _panel()
    got = _warnings(monkeypatch)
    cp.freq_input.setText("145,500,000")
    cp._on_freq_input()

    assert got == []
    assert tuned == [145_500_000.0]


@pytest.mark.parametrize("text,expected", [
    ("145.5", 145_500_000.0),
    ("145.5M", 145_500_000.0),
    ("145500k", 145_500_000.0),
    (" 145.5 ", 145_500_000.0),
    ("145,500,000", 145_500_000.0),
])
def test_unambiguous_forms_still_parse(text, expected):
    """拒绝歧义不许误伤明确写法。"""
    cp, _ = _panel()
    assert cp._parse_freq_text(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["145,5", "145 5", "1,45,000", "1,0000"])
def test_irregular_grouping_rejected(text):
    cp, _ = _panel()
    with pytest.raises(cp_mod.AmbiguousFrequencyInput):
        cp._parse_freq_text(text)
