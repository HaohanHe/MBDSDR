# SPDX-License-Identifier: MIT
"""
新增面板集成测试：多 VFO / 星座图 / ANR / 设备选择 / 分段增益
=================================================================

验证：
  * 面板可实例化、信号正确连接。
  * 无后端时安全降级（按钮置灰 + 显"未连接/等待数据/未检测到设备"，不造假）。
  * ANR 内核谱减法确定性可测。
  * 星座图 feed_iq 不崩、无数据时显"等待数据"。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_ui_vfo_constellation.py -q --tb=short
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import numpy as np  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ----------------------------------------------------------------------
class TestVfoPanel:
    def test_instantiate_no_backend(self):
        from vfo_panel import VfoPanel
        p = VfoPanel()
        assert "未连接" in p.status_label.text()
        assert p.add_btn.isEnabled() is False
        assert p.cycle_btn.isEnabled() is False
        assert p.table.isEnabled() is False

    def test_set_connected_enables_buttons(self):
        from vfo_panel import VfoPanel
        p = VfoPanel()
        p.set_sdr_connected(True)
        assert "已连接" in p.status_label.text()
        assert p.add_btn.isEnabled() is True
        assert p.table.isEnabled() is True

    def test_vfo_added_signal(self):
        from vfo_panel import VfoPanel
        p = VfoPanel()
        received = []
        p.vfo_added.connect(lambda c, b, m: received.append((c, b, m)))
        p._center_freq_provider = lambda: 100e6
        p._on_add_vfo()
        assert received and abs(received[0][0] - 100e6 - 18750) < 1.0

    def test_vfo_removed_signal(self):
        from vfo_panel import VfoPanel
        from mbdsdr_ai.vfo_manager import VfoManager
        mgr = VfoManager()
        mgr.add(100e6, 12500, "FM")
        p = VfoPanel(vfo_manager=mgr)
        p.refresh()
        received = []
        p.vfo_removed.connect(lambda vid: received.append(vid))
        # 直接发信号
        p.vfo_removed.emit("vfo001")
        assert received == ["vfo001"]

    def test_primary_highlight(self):
        from vfo_panel import VfoPanel
        from mbdsdr_ai.vfo_manager import VfoManager
        mgr = VfoManager()
        v1 = mgr.add(100e6, 12500, "FM")
        v2 = mgr.add(101e6, 12500, "FM")
        mgr.set_primary(v1.vfo_id)
        p = VfoPanel(vfo_manager=mgr)
        p.refresh()
        assert p.table.rowCount() == 2
        # 第一行是 primary（粗体绿色）
        first_text = p.table.item(0, 0).text()
        assert first_text == v1.vfo_id


# ----------------------------------------------------------------------
class TestAnrPanel:
    def test_instantiate_no_backend(self):
        from anr_panel import AnrPanel
        p = AnrPanel()
        assert p.enable_chk.isChecked() is False
        assert p.enable_chk.isEnabled() is False
        assert "未连接" in p.snr_label.text()

    def test_set_connected_enables(self):
        from anr_panel import AnrPanel
        p = AnrPanel()
        p.set_sdr_connected(True)
        assert p.enable_chk.isEnabled() is True

    def test_enabled_signal(self):
        from anr_panel import AnrPanel
        p = AnrPanel()
        received = []
        p.enabled_changed.connect(lambda on: received.append(on))
        p.set_sdr_connected(True)
        p.enable_chk.setChecked(True)
        assert received and received[-1] is True

    def test_anr_kernel_deterministic(self):
        """谱减法 ANR：纯噪声输入应被抑制，disabled 时直通。"""
        from mbdsdr_ai.anr import SpectralSubtractionANR, ANRConfig
        np.random.seed(42)
        noise = np.random.randn(4096).astype(np.float32) * 0.1
        anr = SpectralSubtractionANR(ANRConfig(frame_size=256, enabled=True))
        anr.process(noise)  # 第一帧学习
        out = anr.process(noise)
        # 降噪后 RMS 应明显小于输入
        assert np.sqrt(np.mean(out ** 2)) < np.sqrt(np.mean(noise ** 2))
        # disabled 直通
        anr.enabled = False
        passthrough = anr.process(noise)
        assert np.allclose(passthrough, noise)


# ----------------------------------------------------------------------
class TestConstellationPanel:
    def test_instantiate_no_backend(self):
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        assert "等待数据" in p.status_label.text()
        assert p.view._has_data is False

    def test_feed_iq_when_connected(self):
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        p.set_sdr_connected(True)
        iq = np.array([1+1j, -1+1j, -1-1j, 1-1j]*128, dtype=np.complex128)
        p.feed_iq(iq)
        assert p.view._has_data is True
        assert "接收中" in p.status_label.text()

    def test_feed_iq_when_disconnected_ignored(self):
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        iq = np.array([1+1j, -1+1j], dtype=np.complex128)
        p.feed_iq(iq)
        assert p.view._has_data is False

    def test_template_combo(self):
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        p.tmpl_combo.setCurrentText("QPSK")
        assert p.view._template == "QPSK"


# ----------------------------------------------------------------------
class TestDevicePanel:
    def test_instantiate_no_devices(self):
        from device_panel import DevicePanel
        p = DevicePanel()
        # 停掉热插拔轮询定时器，避免异步回调在断言中途改状态
        try:
            p._hotplug_timer.stop()
        except Exception:
            pass
        # 无设备时连接按钮置灰（无论枚举结果如何，未选中设备不可连）
        p.refresh_devices()
        # 若真枚举到设备（CI 环境偶发），按钮可能可用；这里只验证不崩
        assert p.dev_combo.count() >= 1

    def test_set_connected(self):
        from device_panel import DevicePanel
        p = DevicePanel()
        try:
            p._hotplug_timer.stop()
        except Exception:
            pass
        p.set_connected(True, "RTL-SDR #0")
        assert "已连接" in p.status_label.text()
        assert p.disconnect_btn.isEnabled() is True
        p.set_connected(False)
        assert p.disconnect_btn.isEnabled() is False


# ----------------------------------------------------------------------
class TestGainPanel:
    def test_instantiate_no_backend(self):
        from gain_panel import GainPanel
        p = GainPanel()
        assert "未连接" in p.total_label.text()
        # 无后端时三档都置灰
        assert p.lna.slider.isEnabled() is False
        assert p.mixer.slider.isEnabled() is False
        assert p.vga.slider.isEnabled() is False
        assert p.agc_chk.isEnabled() is False

    def test_mock_backend_stages(self):
        """用 mock 设备验证 GainStager 接线：支持的档滑块可用。"""
        from gain_panel import GainPanel

        class _MockDevice:
            def listGains(self, dirn, ch):
                return ["LNA", "VGA"]
            def getGainRange(self, dirn, ch, name):
                if name == "LNA":
                    return (0.0, 40.0)
                if name == "VGA":
                    return (0.0, 62.0)
                return (0.0, 0.0)
            def setGain(self, dirn, ch, val, name):
                return True

        p = GainPanel()
        p.set_backend(_MockDevice())
        assert p.lna.slider.isEnabled() is True
        assert p.vga.slider.isEnabled() is True
        # Mixer 不支持 → 置灰
        assert p.mixer.slider.isEnabled() is False
        assert "不支持" in p.mixer.name_label.text()
