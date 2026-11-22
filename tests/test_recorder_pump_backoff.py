# SPDX-License-Identifier: MIT
"""录音泵退避策略：真机非空读必须 0 退避，否则 2.4 MS/s 下丢样。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.core.controller import _pump_backoff_seconds  # noqa: E402


def test_real_device_nonempty_read_has_no_backoff():
    """真机有界读已按采样率阻塞；再 sleep 会把吞吐砍掉（实测 4x）。"""
    assert _pump_backoff_seconds(empty=False, is_debug=False) == 0.0


def test_empty_read_still_backs_off():
    assert _pump_backoff_seconds(empty=True, is_debug=False) == 0.005
    assert _pump_backoff_seconds(empty=True, is_debug=True) == 0.005


def test_debug_source_keeps_small_nap():
    """调试源读不阻塞，保留 1 ms 小睡避免忙转。"""
    assert _pump_backoff_seconds(empty=False, is_debug=True) == 0.001
