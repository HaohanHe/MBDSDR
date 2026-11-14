# SPDX-License-Identifier: MIT
"""A1 反证：Klobuchar 缺广播参数必须 uncorrected，不得回退固定典型系数。"""
import inspect
import os
import sys

os.environ.setdefault("MBDSDR_OFFLINE", "1")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import math  # noqa: E402

from mbdsdr_ai.rtklib_adapter import klobuchar_ion_delay  # noqa: E402

T_GPST = 1300000000.0
# 一个非典型的合法广播参数组（只是测试向量，不是「典型值」兜底）
PARAMS = [1.2e-8, -7.4e-9, -5.9e-8, 1.1e-7,
          1.1e5, -2.2e5, -1.3e5, 1.0e6]


def _call(params):
    return klobuchar_ion_delay(T_GPST, params,
                               lat=math.radians(31.0), lon=math.radians(121.0),
                               az=math.radians(45.0), el=math.radians(35.0))


def test_missing_params_are_uncorrected_not_typical():
    assert _call(None) is None, "缺参数必须 uncorrected（None），不得代填典型值"
    assert _call([]) is None
    assert _call([0.0] * 8) is None, "全零广播模型视为未给出，不按零延迟"
    assert _call([1e-8] * 7) is None, "参数不足 8 个必须拒绝"
    assert _call([float("nan")] * 8) is None


def test_valid_broadcast_params_still_produce_delay():
    value = _call(PARAMS)
    assert value is not None and value > 0.0


def test_source_contains_no_fixed_typical_coefficients():
    """反证：若把 8 个典型系数重新写回函数，本测试必须红。"""
    src = inspect.getsource(klobuchar_ion_delay)
    forbidden = ("0.1118e-7", "0.7451e-8", "0.5961e-7", "0.1192e-6",
                 "0.1167e6", "0.2294e6", "0.1311e6", "0.1049e7")
    for token in forbidden:
        assert token not in src, f"函数内又出现典型系数 {token}"
    assert "ion_default" not in src
