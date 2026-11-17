# SPDX-License-Identifier: MIT
"""解调模式与带宽一致性检查（Task 96）。

钉住三件事：

1. ``modes_defaults.check_bandwidth`` 的容差口径：0.5×默认宽 ≤ 用户带宽
   ≤ 2×默认宽 视为匹配，超出即不匹配并给出建议值与默认通带；
2. 未知模式 / 非正带宽 / 非有限值必须拒绝并给出具体原因；
3. 控制器 ``set_bandwidth`` 是显式强制语义——不匹配仍按用户值下发，
   只把提示写进状态；``set_demod`` 成功后旧提示必须清空。
"""
import math
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai.core.controller import SDRController  # noqa: E402
from mbdsdr_ai.modes_defaults import check_bandwidth  # noqa: E402


# --------------------------------------------------------------------------
# check_bandwidth：纯函数口径
# --------------------------------------------------------------------------
def test_wfm_with_12k5_is_mismatch_and_suggests_150k():
    r = check_bandwidth("WFM", 12_500)
    assert r["ok"] is False
    assert r["suggested_hz"] == pytest.approx(150_000)
    assert r["default_low_hz"] == pytest.approx(-75_000)
    assert r["default_high_hz"] == pytest.approx(75_000)
    assert r["reason"]


def test_nfm_with_12k5_is_within_tolerance():
    # NFM ±4k → 默认宽 8k，容差 4k–16k，12.5k 落在其中。
    r = check_bandwidth("NFM", 12_500)
    assert r["ok"] is True
    assert r["reason"] == ""


def test_cw_with_120k_is_mismatch():
    # CW 700–900 → 默认宽 200Hz，120k 远超 2× 上限。
    r = check_bandwidth("CW", 120_000)
    assert r["ok"] is False
    assert r["suggested_hz"] == pytest.approx(200)
    assert r["reason"]


@pytest.mark.parametrize(
    "mode,bandwidth_hz",
    [
        ("BOGUS", 12_500.0),          # 未知模式
        ("NFM", 0.0),                 # 非正
        ("NFM", -12_500.0),           # 负值
        ("NFM", float("nan")),        # NaN
        ("NFM", float("inf")),        # 非有限
        ("NFM", "wide"),              # 非数值
        ("NFM", None),                # 非数值
    ],
)
def test_invalid_inputs_are_rejected_with_reason(mode, bandwidth_hz):
    r = check_bandwidth(mode, bandwidth_hz)
    assert r["ok"] is False
    assert isinstance(r["reason"], str) and r["reason"]


def test_unknown_mode_reason_names_the_mode():
    r = check_bandwidth("BOGUS", 12_500)
    assert "BOGUS" in r["reason"]


def test_nan_reason_is_not_silent():
    r = check_bandwidth("NFM", math.nan)
    assert "有限" in r["reason"]


# --------------------------------------------------------------------------
# SDRController：提示不拦截下发
# --------------------------------------------------------------------------
class _FakeRadio:
    """最小 duck-type 后端：记录 set_bandwidth 收到的原值。"""

    def __init__(self):
        self.bw_calls = []

    def set_bandwidth(self, hz):
        self.bw_calls.append(float(hz))
        return True

    def get_frequency(self):
        return 100e6

    def get_gain(self):
        return 0.0

    def get_sample_rate(self):
        return 2.4e6


def _mk(fake):
    c = SDRController(backend=fake)
    c._connected = True
    return c


def test_controller_forwards_mismatched_bandwidth_and_reports_warning():
    fake = _FakeRadio()
    c = _mk(fake)
    c.set_demod("WFM")
    assert c.get_status().bandwidth_warning == ""

    # WFM 配 12.5k 不一致：仍原值下发，状态里带提示。
    assert c.set_bandwidth(12_500) is True
    assert fake.bw_calls == [12_500.0], "显式带宽必须按用户值下发后端"
    status = c.get_status()
    assert status.bandwidth_warning, "不一致必须有状态提示"
    assert "WFM" in status.bandwidth_warning
    assert status.to_dict()["bandwidth_warning"] == status.bandwidth_warning


def test_controller_matching_bandwidth_leaves_warning_empty():
    fake = _FakeRadio()
    c = _mk(fake)
    c.set_demod("NFM")

    assert c.set_bandwidth(12_500) is True
    assert fake.bw_calls == [12_500.0]
    assert c.get_status().bandwidth_warning == ""


def test_set_demod_clears_stale_bandwidth_warning():
    fake = _FakeRadio()
    c = _mk(fake)
    c.set_demod("WFM")
    c.set_bandwidth(12_500)
    assert c.get_status().bandwidth_warning

    # 换模式后旧判断失效，必须清空。
    c.set_demod("NFM")
    assert c.get_status().bandwidth_warning == ""
