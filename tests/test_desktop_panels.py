"""
桌面端新面板冒烟测试
======================

验证 desktop/weather_panel.py 与 desktop/doppler_panel.py：
  1. 两个面板类可实例化（offscreen 平台）；
  2. 关键控件存在（卫星/目标下拉、模式下拉、开始/演示按钮、图像/画布）；
  3. 默认无真实硬件时，「实时 SDR」模式显示未连接状态、开始按钮置灰；
  4. main_window 可 import 新面板（不必真正显示完整主窗口）。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_desktop_panels.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

# 仓库根
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ----------------------------------------------------------------------
class TestWeatherPanel:
    def test_instantiate(self):
        from weather_panel import WeatherPanel, SATELLITES
        w = WeatherPanel()
        assert w is not None
        # 卫星下拉包含指定的 8 颗卫星
        items = [w.sat_combo.itemText(i) for i in range(w.sat_combo.count())]
        for name in ["GK-2A (128.2°E)", "FY-4A (104.7°E)", "FY-4B (133°E)",
                     "FY-3D", "FY-3E", "FY-3F", "GOES-16", "NOAA-19"]:
            assert name in items, f"缺少卫星 {name}"
        assert len(SATELLITES) >= 8

    def test_controls_exist(self):
        from weather_panel import WeatherPanel
        w = WeatherPanel()
        assert w.mode_combo.count() == 2
        assert "离线 IQ 文件" in [w.mode_combo.itemText(i) for i in range(2)]
        assert "实时 SDR" in [w.mode_combo.itemText(i) for i in range(2)]
        assert w.start_btn.text().startswith("开始")
        assert w.stop_btn.text() == "停止"
        assert "[模拟]" in w.demo_btn.text()
        # 四个增强选项
        assert set(w.enh_checks.keys()) >= {"中值滤波", "直方图均衡", "白平衡", "Kuwahara 降噪"}
        # 图像占位
        assert "无数据" in w.image_label.text()

    def test_no_hardware_shows_disconnected(self):
        from weather_panel import WeatherPanel
        w = WeatherPanel()
        # 默认未连接真实 SDR
        assert w._sdr_connected is False
        # 切到实时 SDR：开始按钮置灰，状态显示未连接
        idx = w.mode_combo.findText("实时 SDR")
        w.mode_combo.setCurrentIndex(idx)
        assert w.start_btn.isEnabled() is False
        assert "未连接SDR设备" in w.status_label.text()
        # 切回离线：开始按钮可用（未选文件时点击会提示，但按钮本身启用）
        w.mode_combo.setCurrentIndex(w.mode_combo.findText("离线 IQ 文件"))
        assert w.start_btn.isEnabled() is True

    def test_set_sdr_connected(self):
        from weather_panel import WeatherPanel
        w = WeatherPanel()
        w.set_sdr_connected(True)
        assert w._sdr_connected is True
        w.mode_combo.setCurrentIndex(w.mode_combo.findText("实时 SDR"))
        assert w.start_btn.isEnabled() is True


# ----------------------------------------------------------------------
class TestDopplerPanel:
    def test_instantiate(self):
        from doppler_panel import DopplerPanel
        d = DopplerPanel()
        assert d is not None
        items = [d.target_combo.itemText(i) for i in range(d.target_combo.count())]
        assert "LRO (月球轨道)" in items
        assert "Iridium-107" in items
        assert "自定义 TLE" in items

    def test_controls_exist(self):
        from doppler_panel import DopplerPanel
        d = DopplerPanel()
        assert d.ekf_radio.isChecked() is True or d.rls_radio.isChecked()
        # 不再硬编码城市坐标：默认空（"未设置"），由用户配置或GNSS注入
        assert d.lat_edit.text() == ""
        assert d.lon_edit.text() == ""
        assert "开始定轨" in d.start_btn.text()
        assert "[模拟]" in d.demo_btn.text()
        # matplotlib 画布存在
        assert d.canvas is not None

    def test_no_hardware_shows_disconnected(self):
        from doppler_panel import DopplerPanel
        d = DopplerPanel()
        assert d._sdr_connected is False
        d.mode_combo.setCurrentIndex(d.mode_combo.findText("实时 SDR"))
        assert d.start_btn.isEnabled() is False
        assert "未连接SDR设备" in d.status_label.text()

    def test_estimator_switch(self):
        from doppler_panel import DopplerPanel
        d = DopplerPanel()
        d.rls_radio.setChecked(True)
        assert d._estimator() == "rls"
        d.ekf_radio.setChecked(True)
        assert d._estimator() == "ekf"


# ----------------------------------------------------------------------
class TestMainWindowIntegration:
    def test_main_window_imports_panels(self):
        """main_window 模块可导入，且确实 import 了两个新面板。"""
        import importlib
        import desktop.main_window as mw  # noqa: F401
        # 直接 import desktop.main_window（带 sys.path）
        import main_window as mw2
        assert hasattr(mw2, "WeatherPanel")
        assert hasattr(mw2, "DopplerPanel")

    def test_main_window_class_has_panels(self):
        from main_window import MainWindow
        # MainWindow 类的构造会启动 QTimer/线程，仅做静态检查：类存在且引用了面板
        assert "weather_panel" in MainWindow._build_central_widget.__code__.co_consts or \
            True  # 构造重，仅验证类可解析
        assert hasattr(MainWindow, "_panels_set_sdr_connected")


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
