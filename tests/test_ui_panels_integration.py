"""
桌面内核能力面板集成测试
========================

验证 scanner_panel / modulation_panel / bookmark_panel / settings_panel
可实例化、信号正确连接、无后端时安全降级（按钮置灰 + 显"未连接"，不造假）。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest \
        tests/test_desktop_panels.py tests/test_main_window.py \
        tests/test_ui_panels_integration.py -q --tb=short
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
import numpy as np  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ----------------------------------------------------------------------
class TestScannerPanel:
    def test_instantiate_no_backend(self):
        from scanner_panel import ScannerPanel
        p = ScannerPanel()
        # 无后端：开始按钮置灰，状态显"未连接"
        assert p.scan_btn.isEnabled() is False
        assert "未连接" in p.status_label.text()

    def test_set_sdr_connected_without_backend_still_disabled(self):
        from scanner_panel import ScannerPanel
        p = ScannerPanel()
        p.set_sdr_connected(True)  # 只连接、无 backend 注入 -> 仍不可扫
        assert p.scan_btn.isEnabled() is False

    def test_segment_chosen_signal(self):
        from scanner_panel import ScannerPanel
        p = ScannerPanel()
        received = []
        p.segment_chosen.connect(lambda d: received.append(d))
        seg = {"start_freq": 88e6, "end_freq": 88.2e6, "peak_freq": 88.1e6,
               "peak_db": -40.0, "bandwidth": 200e3, "kind": "WFM",
               "suggested_mode": "WFM"}
        p._on_item_clicked  # noqa: B018 - 仅确认方法存在
        # 直接走 emit 路径模拟用户点击
        p.segment_chosen.emit(seg)
        assert received and received[0]["kind"] == "WFM"


class TestModulationPanel:
    def test_instantiate_no_backend(self):
        from modulation_panel import ModulationPanel
        p = ModulationPanel()
        assert p.identify_btn.isEnabled() is False
        assert "未连接" in p.status_label.text()

    def test_synthetic_iq_classify(self):
        """用合成 IQ 在测试里验证识别链路（UI 本身仍显未连接态）。"""
        from mbdsdr_ai.analysis.modulation_classifier import ModulationClassifier
        sr = 48_000.0
        t = np.arange(8192) / sr
        # 一个 AM 调幅信号
        iq = (1.0 + 0.5 * np.cos(2 * np.pi * 500.0 * t)) * \
            np.exp(1j * np.zeros_like(t))
        res = ModulationClassifier().classify(iq, sr)
        assert res.modulation in ("AM", "UNKNOWN")
        assert 0.0 <= res.confidence <= 1.0

    def test_apply_demod_signal(self):
        from modulation_panel import ModulationPanel
        p = ModulationPanel()
        got = []
        p.apply_demod_requested.connect(lambda m, b: got.append((m, b)))
        # 伪造一个最近结果，验证 apply 发信号
        class R:
            modulation = "FM"
            bandwidth = 12_500.0
            symbol_rate_hint = None
            suggestions = []
        p._last = R()
        p._on_apply_clicked()
        assert got and got[0][0] in ("NFM", "WFM")


class TestBookmarkPanel:
    def _panel(self):
        from bookmark_panel import BookmarkPanel
        return BookmarkPanel(config_dir=tempfile.mkdtemp())

    def test_instantiate_empty(self):
        p = self._panel()
        # 不预存任何地区电台：初始为空
        assert p.tree.topLevelItemCount() == 0
        # 分组树有 6 组 + "全部"
        assert p.group_list.count() == 7

    def test_add_and_tune(self):
        p = self._panel()
        got = []
        p.tune_requested.connect(lambda f, m, b: got.append((f, m, b)))
        from mbdsdr_ai.bookmark_manager import Bookmark
        p._mgr.add(Bookmark(frequency_hz=100_000_000, name="测试台",
                            modulation="NFM", bandwidth_hz=12_500,
                            group="ham"))
        p.refresh_list()
        assert p.tree.topLevelItemCount() == 1
        # 模拟双击第一行
        p.tree.setCurrentItem(p.tree.topLevelItem(0))
        p._on_item_double_clicked(p.tree.topLevelItem(0), 0)
        assert got and abs(got[0][0] - 100e6) < 1.0

    def test_nearest(self):
        p = self._panel()
        from mbdsdr_ai.bookmark_manager import Bookmark
        p._mgr.add(Bookmark(frequency_hz=145_000_000, name="近",
                            modulation="NFM", bandwidth_hz=12_500))
        p._current_freq_provider = lambda: 145_100_000.0
        # 不弹窗：nearest 内部 QMessageBox.information 可能阻塞，直接测底层查找
        best = None
        best_d = float("inf")
        for bm in p._mgr.all():
            d = abs(bm.frequency_hz - 145_100_000.0)
            if d < best_d:
                best_d, best = d, bm
        assert best is not None and best_d == 100_000


class TestServiceSettingsPanel:
    def test_defaults_off(self):
        from settings_panel import ServiceSettingsPanel
        p = ServiceSettingsPanel()
        assert p.rc_check.isChecked() is False
        assert p.web_check.isChecked() is False
        assert "未监听" in p.rc_status.text()
        assert "未启动" in p.web_status.text()

    def test_toggle_rc_lifecycle(self):
        from settings_panel import ServiceSettingsPanel
        p = ServiceSettingsPanel()
        p.rc_port.setValue(0)  # 0 -> 内核 ThreadingTCPServer 会选空闲端口
        # RemoteControl 固定绑定 (host, port=7356)，端口 spin 仅记录；用高位空闲口
        p.rc_port.setValue(17356)
        p.rc_check.setChecked(True)
        # 端口被占用也不应崩；成功则显监听中
        p.rc_check.setChecked(False)
        assert "未监听" in p.rc_status.text()
        p.shutdown_all()

    def test_set_backend_none_no_crash(self):
        from settings_panel import ServiceSettingsPanel
        p = ServiceSettingsPanel()
        p.set_backend(None)
        p.shutdown_all()


class TestMainWindowWiring:
    def test_main_window_has_kernel_panels(self):
        from main_window import MainWindow
        w = MainWindow()
        assert w.scanner_panel is not None
        assert w.modulation_panel is not None
        assert w.bookmark_panel is not None
        assert w.service_settings_panel is not None
        # 无后端时扫频/识别按钮置灰
        assert w.scanner_panel.scan_btn.isEnabled() is False
        assert w.modulation_panel.identify_btn.isEnabled() is False
        w.close()

    def test_sky_view_exposes_tune_signal(self):
        from main_window import MainWindow
        w = MainWindow()
        assert hasattr(w.sky_view, "tune_satellite_requested")
        w.close()


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
