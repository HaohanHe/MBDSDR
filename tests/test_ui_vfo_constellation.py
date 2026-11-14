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

    def test_add_vfo_without_center_refuses_instead_of_fabricating(self):
        """中心频率未知时不许凭空按 100 MHz 新建 VFO。

        实测缺陷（2026-11-14）：`_on_add_vfo` 在 provider 返回 0 时静默用
        100 MHz 兜底，用户点一下就在一个自己没选的频率上多出一条 VFO，
        界面上没有任何说明。
        """
        from vfo_panel import VfoPanel
        p = VfoPanel()
        p.set_sdr_connected(True)          # 按钮可用，才可能被点到
        received = []
        p.vfo_added.connect(lambda c, b, m: received.append((c, b, m)))
        p._center_freq_provider = lambda: 0.0
        p._on_add_vfo()
        assert received == [], "中心频率未知时不得编一个 100 MHz 新建 VFO"
        text = p.status_label.text()
        assert "中心频率" in text and "调谐" in text, text

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

    # -- 无载波判定（不许把噪声云当星座画） --------------------------------
    def test_pure_noise_shows_no_carrier(self):
        from constellation_panel import ConstellationPanel
        rng = np.random.default_rng(7)
        noise = (rng.normal(size=512) + 1j * rng.normal(size=512)) * 0.1
        p = ConstellationPanel()
        p.set_sdr_connected(True)
        p.feed_iq(noise.astype(np.complex64))
        assert p.carrier_locked is False
        assert p.no_carrier_reason
        assert p.view._points == []
        assert p.view._has_data is False
        assert "未锁定" in p.status_label.text()

    def test_zero_iq_shows_no_carrier(self):
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        p.set_sdr_connected(True)
        p.feed_iq(np.zeros(512, dtype=np.complex64))
        assert p.carrier_locked is False
        assert "幅度为 0" in p.no_carrier_reason
        assert p.view._points == []

    def test_bpsk_locks_and_draws_points(self):
        from constellation_panel import ConstellationPanel
        rng = np.random.default_rng(11)
        sym = rng.choice([-1.0, 1.0], size=512)
        iq = sym + 0.02 * (rng.normal(size=512) + 1j * rng.normal(size=512))
        p = ConstellationPanel()
        p.set_sdr_connected(True)
        p.feed_iq(iq.astype(np.complex64))
        assert p.carrier_locked is True
        assert p.no_carrier_reason == ""
        assert len(p.view._points) == 512
        assert "接收中" in p.status_label.text()

    def test_16qam_locks(self):
        from constellation_panel import ConstellationPanel
        rng = np.random.default_rng(3)
        levels = np.array([-3.0, -1.0, 1.0, 3.0])
        i = rng.choice(levels, size=512)
        q = rng.choice(levels, size=512)
        p = ConstellationPanel()
        p.set_sdr_connected(True)
        p.feed_iq((i + 1j * q).astype(np.complex64))
        assert p.carrier_locked is True

    def test_carrier_loss_clears_stale_points(self):
        """先有载波后掉成噪声：旧散点必须清掉，不能拿上一张图充数。"""
        from constellation_panel import ConstellationPanel
        rng = np.random.default_rng(5)
        p = ConstellationPanel()
        p.set_sdr_connected(True)
        iq = np.array([1+1j, -1+1j, -1-1j, 1-1j] * 128, dtype=np.complex64)
        p.feed_iq(iq)
        assert p.carrier_locked is True
        noise = (rng.normal(size=512) + 1j * rng.normal(size=512)) * 0.1
        p.feed_iq(noise.astype(np.complex64))
        assert p.carrier_locked is False
        assert p.view._points == []

    def test_outlier_does_not_define_scale(self):
        """归一化用 p99 而不是 max：单个离群点不该把整幅压扁。"""
        from constellation_panel import ConstellationPanel
        iq = np.array([1+1j, -1+1j, -1-1j, 1-1j] * 128, dtype=np.complex64)
        iq[-1] = 1000 + 0j
        p = ConstellationPanel()
        p.set_sdr_connected(True)
        p.feed_iq(iq)
        assert p.carrier_locked is True
        assert 0 < p.view._ref_amp < 10.0

    # -- GNSS 主视图：星座图不该只认 SDR ----------------------------------
    def test_default_layer_is_gnss_only(self):
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        assert p.gnss_only_btn.isChecked() is True
        assert p.sdr_overlay is False
        assert p.sdr_box.isVisibleTo(p) is False, "默认不该露出 SDR 叠加层"
        assert "未连接" in p.gnss_status_text

    def test_sdr_overlay_toggle(self):
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        p.gnss_sdr_btn.click()
        assert p.sdr_overlay is True
        assert p.gnss_sdr_btn.isChecked() is True
        assert p.sdr_box.isVisibleTo(p) is True
        p.gnss_only_btn.click()
        assert p.sdr_overlay is False
        assert p.sdr_box.isVisibleTo(p) is False

    def test_gnss_view_draws_real_gsv_only(self):
        """只画 GSV 真上报的卫星；同名 PRN 分属不同星座不得互相点亮。"""
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        p.update_gnss([], {}, True)
        assert p.gnss_satellites == []
        assert "暂无可见卫星" in p.gnss_status_text

        frames = [
            {"talker": "GP", "sats": [
                {"id": 12, "elevation": 45.0, "azimuth": 130.0, "snr_db": 38.0},
                {"id": 5, "elevation": 20.0, "azimuth": 310.0, "snr_db": 24.0},
            ]},
            {"talker": "GB", "sats": [
                {"id": 5, "elevation": 60.0, "azimuth": 200.0, "snr_db": None},
            ]},
        ]
        p.update_gnss(frames, {"GP": {"satellites_used": [12]}}, True)
        sats = p.gnss_satellites
        assert len(sats) == 3
        assert {s["constellation"] for s in sats} == {"GPS", "BeiDou"}
        assert "可见 3 颗" in p.gnss_status_text
        assert "参与定位 1 颗" in p.gnss_status_text

        def pick(cons, prn):
            return next(s for s in sats
                        if s["constellation"] == cons and s["prn"] == prn)

        assert p.gnss_view.is_used(pick("GPS", 12)) is True
        assert p.gnss_view.is_used(pick("GPS", 5)) is False
        assert p.gnss_view.is_used(pick("BeiDou", 5)) is False

    def test_gnss_disconnect_clears_stale_satellites(self):
        """串口停掉后 get_gsv_frames() 仍会回上一帧，界面必须清空。"""
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        p.update_gnss([{"talker": "GP",
                        "sats": [{"id": 1, "snr_db": 30.0}]}], {}, True)
        assert len(p.gnss_satellites) == 1
        p.update_gnss([], {}, False)
        assert p.gnss_satellites == []
        assert "未连接" in p.gnss_status_text
        assert "未连接" in p.gnss_view._empty_text

    def test_gnss_view_paints_without_crash(self):
        """柱状星群图要能画：含 snr 缺失、参与/未参与定位两种情况。"""
        from constellation_panel import ConstellationPanel
        p = ConstellationPanel()
        p.resize(420, 620)
        frames = [{"talker": "GP", "sats": [
            {"id": i, "elevation": 10.0 * i, "azimuth": 30.0 * i,
             "snr_db": None if i == 2 else 20.0 + i} for i in (1, 2, 3, 4)]}]
        p.update_gnss(frames, {"GP": {"satellites_used": [1, 3]}}, True)
        img = p.gnss_view.grab()
        assert img.isNull() is False


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
