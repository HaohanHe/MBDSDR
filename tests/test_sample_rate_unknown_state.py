# SPDX-License-Identifier: MIT
"""采样率「档位未知」空态：文案、原因区分、连接后回填（无硬件，offscreen）。

背景：``sample_rate_options_for_backend`` 在 ``capability_status`` 缺失时返回
``None``。界面此前把两类完全不同的情况写成同一句「未知（后端未报告采样率
范围）」——分不清「还没探测」和「后端根本不给档位」。

反证方式（任一失效本文件即红）：
  1. 无 ``capability_status`` 的设备 → 下拉不是空列表、只有一条不可选说明、
     文案里不出现任何 MS/s 数字；
  2. 已连接仍无范围 → 文案必须换成「该后端不提供采样率档位」；
  3. 连接后把探测结果写进 ``capability_status``（probed）→ 下拉被真实档位
     占据，说明项消失。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _Dev:
    """后端设备对象的最小形态：只有范围，没有来源登记。"""

    def __init__(self, rng, status=None):
        self.device_type = "rtlsdr"
        self.sample_rate_range = rng
        self.capability_status = dict(status or {})
        self.capability_note = {}


class _Backend:
    def __init__(self, dev, *, connected=False, rate=1_024_000):
        self.device = dev
        self.status = SimpleNamespace(connected=connected)
        self._rate = rate

    def get_sample_rate(self):
        return self._rate


def _report(backend):
    from mbdsdr_ai.device_capabilities import sample_rate_options_report
    return sample_rate_options_report(backend)


class TestReportStates:
    def test_no_source_and_not_connected_is_pending_probe(self):
        rpt = _report(_Backend(_Dev((225_001, 3_200_000)), connected=False))
        assert rpt.options is None
        assert rpt.state == "pending_probe"
        assert rpt.message == "档位未知，连接设备后探测"
        assert "MS/s" not in rpt.message

    def test_connected_without_range_is_not_reported(self):
        rpt = _report(_Backend(_Dev(None), connected=True))
        assert rpt.options is None
        assert rpt.state == "not_reported"
        assert rpt.message == "该后端不提供采样率档位"
        assert "MS/s" not in rpt.message

    def test_connected_range_without_source_is_unattributed(self):
        rpt = _report(_Backend(_Dev((225_001, 3_200_000)), connected=True))
        assert rpt.options is None
        assert rpt.state == "unattributed"
        assert "未标注来源" in rpt.message

    def test_probed_range_yields_real_options(self):
        rpt = _report(_Backend(
            _Dev((225_001, 3_200_000),
                 {"sample_rate_range": "probed"}), connected=True))
        assert rpt.known is True
        assert rpt.options, "探测到范围后必须给出真实档位"
        assert rpt.message == ""


@pytest.mark.usefixtures("isolated_profile", "qtapp")
class TestControlPanelEmptyState:
    def _panel(self, backend=None):
        from control_panel import ControlPanel
        panel = ControlPanel()
        if backend is not None:
            # ControlPanel 是顶层控件，window() 返回自身；刷新入口从这里取后端。
            panel._active_sdr_backend = backend
        return panel

    def test_unknown_is_one_disabled_explanation_without_numbers(self):
        panel = self._panel()
        try:
            panel.set_sample_rate_options(None)
            combo = panel.sample_rate_combo
            assert combo.count() == 1, "不得是空下拉（空列表无从解释）"
            assert combo.itemData(0) is None, "说明项不得携带假档位"
            assert combo.isEnabled() is False
            text = combo.currentText()
            assert text == "档位未知，连接设备后探测"
            assert "MS/s" not in text, f"说明项不得显示 MS/s 数字: {text!r}"
        finally:
            panel.deleteLater()

    def test_two_empty_states_use_different_copy(self):
        panel = self._panel()
        try:
            panel.set_sample_rate_options(None, message="档位未知，连接设备后探测")
            pending = panel.sample_rate_combo.currentText()
            panel.set_sample_rate_options(None, message="该后端不提供采样率档位")
            absent = panel.sample_rate_combo.currentText()
            assert pending != absent, "「还没探测」与「后端不给」必须能区分"
            assert "MS/s" not in pending and "MS/s" not in absent
        finally:
            panel.deleteLater()

    def test_connect_backfills_real_tiers_and_drops_explanation(self):
        dev = _Dev((225_001, 3_200_000))  # 故意不带 capability_status
        backend = _Backend(dev, connected=False)
        panel = self._panel(backend)
        try:
            panel.set_sample_rate_options(None, message="档位未知，连接设备后探测")
            assert panel.sample_rate_combo.count() == 1
            assert panel.sample_rate_combo.itemData(0) is None

            # 连接后驱动探测到范围：后端把来源登记为 probed。
            backend.status.connected = True
            dev.capability_status["sample_rate_range"] = "probed"
            dev.sample_rate_range = (225_001, 3_200_000)
            panel.set_connected(True)

            combo = panel.sample_rate_combo
            texts = [combo.itemText(i) for i in range(combo.count())]
            datas = [combo.itemData(i) for i in range(combo.count())]
            assert datas and all(d is not None for d in datas), texts
            assert all("MS/s" in t for t in texts), texts
            assert not any("档位未知" in t for t in texts), \
                f"连接探测成功后说明文案必须消失: {texts}"
            assert combo.isEnabled() is True
            assert combo.currentData() is not None
        finally:
            panel.deleteLater()
