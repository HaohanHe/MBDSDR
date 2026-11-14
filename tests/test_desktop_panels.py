# SPDX-License-Identifier: MIT
"""
桌面端新面板冒烟测试
====================

验证 desktop/weather_panel.py（单一真实数据源架构）：
  1. 两个面板类可实例化（offscreen 平台）；
  2. 关键控件存在（卫星/目标下拉、来源下拉、开始/停止按钮、图像/画布）；
  3. 不存在任何“模拟演示”按钮（demo_btn 已随模拟模式一并移除）；
  4. 默认无真实硬件时，「实时 SDR」模式开始按钮置灰、显示未连接；
  5. main_window 的面板一律经 panel_registry 构造（不直连 import 面板类）。

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
        # 卫星下拉来自真实 SATELLITES 字典
        items = [w.sat_combo.itemText(i) for i in range(w.sat_combo.count())]
        for name in ["GK-2A LRIT (128.2°E)", "FY-4A HRIT (104.7°E)",
                     "GOES-16 HRIT (75.2°W)", "NOAA-19 APT"]:
            assert name in items, f"缺少卫星 {name}"
        assert len(SATELLITES) >= 6

    def test_controls_exist(self):
        from weather_panel import WeatherPanel
        w = WeatherPanel()
        # 数据来源下拉（录制文件 / 实时 SDR）
        assert w.src_combo.count() == 2
        assert "录制文件" in [w.src_combo.itemText(i) for i in range(2)]
        assert "实时 SDR" in [w.src_combo.itemText(i) for i in range(2)]
        assert w.start_btn.text().startswith("开始")
        assert w.stop_btn.text() == "停止"
        # 模拟演示按钮必须不存在
        assert not hasattr(w, "demo_btn")
        # 四个增强选项
        assert set(w.enh_checks.keys()) >= {"中值滤波", "直方图均衡", "白平衡", "Kuwahara 降噪"}
        # 图像占位：无数据
        assert "无数据" in w.image_label.text()

    def test_no_hardware_shows_disconnected(self):
        from weather_panel import WeatherPanel
        w = WeatherPanel()
        # 默认未连接真实 SDR
        assert w._sdr_connected is False
        # 切到实时 SDR：开始按钮置灰，状态显示未连接
        w.src_combo.setCurrentIndex(w.src_combo.findText("实时 SDR"))
        assert w.start_btn.isEnabled() is False
        assert "未连接SDR设备" in w.status_label.text()
        # 切回录制文件：未选文件时开始按钮同样置灰（不造空任务）
        w.src_combo.setCurrentIndex(w.src_combo.findText("录制文件"))
        assert w.start_btn.isEnabled() is False

    def test_set_sdr_connected(self):
        from weather_panel import WeatherPanel
        w = WeatherPanel()
        w.set_sdr_connected(True)
        assert w._sdr_connected is True
        w.src_combo.setCurrentIndex(w.src_combo.findText("实时 SDR"))
        # P1-24：本面板不做边收边解，实时档位即使有连接也不许点亮「开始解码」
        assert w.start_btn.isEnabled() is False
        assert "不在此面板解码" in w.image_label.text()

    def test_realtime_start_decode_does_not_promise_receiving(self):
        """实时档位直接调 start_decode：不许弹「实时接收指引」模态假装能收。"""
        from weather_panel import WeatherPanel
        from PySide6.QtWidgets import QMessageBox

        w = WeatherPanel()
        w.set_sdr_connected(True)
        w.src_combo.setCurrentIndex(w.src_combo.findText("实时 SDR"))

        calls = []
        orig_info, orig_warn = QMessageBox.information, QMessageBox.warning
        QMessageBox.information = staticmethod(
            lambda *a, **k: calls.append(("info", a)) or QMessageBox.Ok)
        QMessageBox.warning = staticmethod(
            lambda *a, **k: calls.append(("warn", a)) or QMessageBox.Ok)
        try:
            w.start_decode()
        finally:
            QMessageBox.information = orig_info
            QMessageBox.warning = orig_warn
        assert calls == [], f"实时档位仍弹模态: {calls}"
        assert "录音" in w.image_label.text()
        assert "不直接接收" in w.status_label.text()


# ----------------------------------------------------------------------
def _registry_panel_classes(reg):
    """注册表工厂闭包里声明的面板类名（只读，不 import 任何 Qt 模块）。"""
    names = set()
    for spec in reg.list():
        factory = spec.factory
        code = getattr(factory, "__code__", None)
        freevars = getattr(code, "co_freevars", ())
        if "class_name" not in freevars:
            continue
        cell = factory.__closure__[freevars.index("class_name")]
        name = cell.cell_contents
        if isinstance(name, str):
            names.add(name)
    return names


class TestMainWindowIntegration:
    def test_main_window_constructs_panels_via_registry(self):
        """面板一律经 panel_registry 构造，main_window 不直连 import 面板类。

        PanelSpec 收编后 ``main_window.WeatherPanel`` 这类直连导入不该存在；
        旧断言却把「import 过面板」钉成了契约，是重构留下的过期断言。
        这里改守真正的契约（AST 静态判据）：

          1) 注册表里声明的面板类名不得出现在 main_window 的 import 里；
          2) 也不得被直接实例化；
          3) 构造点走 ``self._create_panel("<id>")``，id 必须在注册表里，且
             ``_create_panel`` 的实现确实调用 ``registry().create(id)``。
        """
        import ast
        import inspect

        import main_window as mw
        from panel_registry import registry

        reg = registry()
        panel_classes = _registry_panel_classes(reg)
        assert "WeatherPanel" in panel_classes, "注册表未声明气象面板，判据失去意义"

        tree = ast.parse(inspect.getsource(mw))

        # 1) 不得 import 面板类（模块级与函数内都算）
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.name for a in node.names)
                imported.update(a.asname for a in node.names if a.asname)
            elif isinstance(node, ast.Import):
                imported.update((a.asname or a.name).split(".")[0]
                                for a in node.names)
        leaked = sorted(imported & panel_classes)
        assert not leaked, (
            "main_window 直连 import 了面板类，应改为经 panel_registry 构造："
            f"{leaked}")

        # 2) 不得直接实例化面板类
        direct = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in panel_classes:
                direct.add(func.id)
            elif (isinstance(func, ast.Attribute)
                  and func.attr in panel_classes):
                direct.add(func.attr)
        assert not direct, f"main_window 直接实例化面板类：{sorted(direct)}"

        # 3) 构造走 _create_panel(id) → registry().create(id)
        built = {}
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_create_panel"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                continue
            pid = node.args[0].value
            targets = set()
            for parent in ast.walk(tree):
                if (isinstance(parent, ast.Assign) and parent.value is node):
                    targets.update(t.attr for t in parent.targets
                                   if isinstance(t, ast.Attribute))
            built.setdefault(pid, set()).update(targets)
        assert "weather" in built, "气象面板未走 _create_panel('weather')"
        assert "weather_panel" in built["weather"], (
            "气象面板未经 _create_panel('weather') 赋给 self.weather_panel")
        unknown = sorted(pid for pid in built if reg.get(pid) is None)
        assert not unknown, f"_create_panel 用了注册表里没有的 id：{unknown}"

        used_registry = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "_create_panel"):
                used_registry = any(
                    isinstance(c, ast.Call)
                    and isinstance(c.func, ast.Attribute)
                    and c.func.attr == "create"
                    and isinstance(c.func.value, ast.Call)
                    and isinstance(c.func.value.func, ast.Name)
                    and c.func.value.func.id == "registry"
                    for c in ast.walk(node))
        assert used_registry, (
            "_create_panel 没有调用 registry().create(id)，面板构造绕开了注册表")

    def test_main_window_class_has_panels(self):
        from main_window import MainWindow
        assert hasattr(MainWindow, "_panels_set_sdr_connected")


# ----------------------------------------------------------------------
class TestAprsPanelHonesty:
    def test_missing_timestamp_renders_dash_not_1970(self):
        """缺 _ts 时时间列显示「—」，不得用 0 渲染成 1970 年。"""
        from aprs_panel import AprsPanel
        panel = AprsPanel()
        try:
            panel._packets = [{"source": "N0CALL", "destination": "APRS",
                               "type": "unknown"}]
            panel._refresh_table()
            assert panel.table.item(0, 0).text() == "—"
        finally:
            panel.deleteLater()


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
