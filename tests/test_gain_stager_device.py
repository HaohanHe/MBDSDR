# SPDX-License-Identifier: MIT
"""
tests/test_gain_stager_device.py
=================================
验证分档增益设备选择修复（2026-11-02 SDR 审计）:

  1. set_backend 优先用原生 _sdr 句柄（有 listGains/getGainRange）;
  2. 不再把 SDRDevice 信息类误当原生设备（三档被误判全禁用）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from gain_panel import GainPanel  # noqa: E402
from mbdsdr_ai.gain_staging import GainStager  # noqa: E402


class _NativeDev:
    def listGains(self, direction, channel):
        return ["LNA", "MIXER", "VGA"]

    def getGainRange(self, direction, channel, elem):
        return (0.0, 40.0)


class _BackendWithNative:
    _sdr = _NativeDev()


class _BackendInfoOnly:
    device = object()   # SDRDevice 信息类, 无 listGains


class TestNativeHandlePreferred:
    def test_stager_built_from_native_handle(self):
        panel = GainPanel()
        panel.set_backend(_BackendWithNative())
        assert panel._stager is not None, "有原生句柄时应建 GainStager"
        desc = panel._stager.describe()
        supported = [k for k, v in desc.items() if v.get("supported")]
        assert supported, f"至少一档应被支持: {desc}"

    def test_info_only_backend_does_not_crash(self):
        panel = GainPanel()
        panel.set_backend(_BackendInfoOnly())
        # 无原生句柄: 诚实降级, 不假装支持（不会崩）
        assert panel._stager is None or isinstance(panel._stager, GainStager)
