# SPDX-License-Identifier: MIT
"""采样率可见档位 ⊆ 后端可接受档位（tests/test_sample_rate_options.py）

口径（2026-11-05 裁定）：档位由后端真实能力决定，前端不许有自己的常量表。
本文件钉住三件事：
  1. 后端能力函数只从后端自带档位表/声明范围取档，未知就返回 None；
  2. 界面可见档位必须 ⊆ 该后端可接受集合；
  3. 反证：把不可接受档塞回可见列表，断言必须失败（不是空转）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


from mbdsdr_ai.device_capabilities import (  # noqa: E402
    sample_rate_options,
    sample_rate_options_for_backend,
)
from mbdsdr_ai.sdr_backend import RTLSDRBackend  # noqa: E402


def _device(driver, rng, status="declared"):
    return {
        "driver": driver,
        "sample_rate_range": rng,
        "capability_status": {"sample_rate_range": status},
    }


def assert_visible_subset(visible, acceptable):
    """可见档位必须全部落在后端可接受集合里；越界即红并列出越界项。"""
    extra = sorted(set(visible) - set(acceptable))
    assert not extra, f"可见档位不在后端可接受集合: {extra}"


def test_rtlsdr_visible_subset_of_backend_rates():
    opts = sample_rate_options(_device("rtlsdr", (225_001, 3_200_000)))
    assert opts, "RTL 声明区间内必须能给出档位"
    assert_visible_subset(opts, RTLSDRBackend.SAMPLE_RATES)


def test_hackrf_visible_subset_of_backend_table():
    from mbdsdr_ai.hackrf_params import SUPPORTED_SAMPLE_RATES

    opts = sample_rate_options(_device("hackrf", (2_000_000, 20_000_000), "probed"))
    assert opts
    assert_visible_subset(opts, SUPPORTED_SAMPLE_RATES)


def test_unknown_range_has_no_fake_table():
    assert sample_rate_options(_device("soapy", None, "unknown")) is None
    # 无离散表但范围已知：只给声明范围两端（不编中间档）
    assert sample_rate_options(_device("soapy", (2_000_000, 8_000_000))) == [2_000_000, 8_000_000]


def test_continuous_backend_only_exposes_declared_edges():
    # 无离散表的连续可调后端：只给范围两端，不编中间档
    opts = sample_rate_options(_device("plutosdr", (1_000_000, 61_440_000)))
    assert opts == [1_000_000, 61_440_000]


def test_counter_evidence_injected_tier_turns_red():
    acceptable = {1_024_000, 2_400_000}
    assert_visible_subset([1_024_000, 2_400_000], acceptable)
    with pytest.raises(AssertionError, match="1400000"):
        assert_visible_subset([1_024_000, 1_400_000, 2_400_000], acceptable)


def test_control_panel_repopulates_from_backend_options(qapp):
    from control_panel import ControlPanel

    panel = ControlPanel()
    # 连接前预填 = RTL 后端能力表，不是前端常量
    assert panel.sample_rate_combo.count() == len(RTLSDRBackend.SAMPLE_RATES)

    panel.set_sample_rate_options([250_000, 1_024_000], 1_024_000)
    got = [panel.sample_rate_combo.itemData(i) for i in range(panel.sample_rate_combo.count())]
    assert got == [250_000.0, 1_024_000.0]
    assert panel.sample_rate_combo.currentData() == 1_024_000.0

    panel.set_sample_rate_options(None)
    assert panel.sample_rate_combo.count() == 1
    assert panel.sample_rate_combo.itemData(0) is None
    assert not panel.sample_rate_combo.isEnabled()


class _FakeBackend:
    def __init__(self, rng):
        self.device = type("Dev", (), {"device_type": "rtlsdr", "sample_rate_range": rng})()

    def get_sample_rate(self):
        return 1_024_000


def test_backend_options_helper_matches_device_options(qapp):
    backend = _FakeBackend((225_001, 3_200_000))
    opts = sample_rate_options_for_backend(backend)
    assert opts == sample_rate_options(_device("rtlsdr", (225_001, 3_200_000)))
