#!/usr/bin/env python3
"""
MainWindow 用户自定义布局 offscreen 测试
=========================================

覆盖：
  1. 保存当前布局 -> 布局名出现在工具栏下拉中；
  2. 切换 splitter 后再恢复该用户布局 -> 三栏比例被还原；
  3. 删除用户布局 -> 从下拉消失；内置布局删除被拒；
  4. 「面板」菜单可勾选隐藏/显示右栏页签；
  5. 恢复出厂 -> 用户布局清空，回到内置。

布局仓库用临时目录注入，不污染真实用户配置。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_user_layouts.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QTabWidget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def main_window(qapp, tmp_path, monkeypatch):
    # 注入临时布局仓库：让 DockLayoutManager 用 tmp_path 下的 layouts.json
    import dock_layout_manager
    from layout_store import LayoutStore
    temp_store = LayoutStore(path=str(tmp_path / "layouts.json"))
    monkeypatch.setattr(dock_layout_manager, "layout_store", lambda: temp_store)

    from main_window import MainWindow
    w = MainWindow()
    w.show()
    # 确保下拉已按注入仓库重建
    w._refresh_layout_combo()
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


class TestLayoutComboDynamic:
    def test_combo_starts_with_builtins(self, main_window):
        texts = [main_window.layout_combo.itemText(i)
                 for i in range(main_window.layout_combo.count())]
        assert texts[:3] == ["专注", "分析", "网格"]

    def test_user_layout_appears_in_combo_after_save(self, main_window):
        dlm = main_window.dock_layout
        assert dlm.save_user_layout("我的测试布局") is True
        main_window._refresh_layout_combo()
        texts = [main_window.layout_combo.itemText(i)
                 for i in range(main_window.layout_combo.count())]
        assert "我的测试布局" in texts
        # 内置仍在前
        assert texts[:3] == ["专注", "分析", "网格"]

    def test_delete_btn_enabled_for_user_layout_only(self, main_window):
        dlm = main_window.dock_layout
        dlm.save_user_layout("临时布局")
        main_window._refresh_layout_combo()
        # 切到用户布局 -> 删除按钮可用
        idx = main_window.layout_combo.findData("临时布局")
        main_window.layout_combo.setCurrentIndex(idx)
        assert main_window.layout_delete_btn.isEnabled() is True
        # 切到内置 -> 删除按钮置灰
        main_window.layout_combo.setCurrentIndex(0)  # 专注
        assert main_window.layout_delete_btn.isEnabled() is False


class TestSaveAndRestore:
    def test_splitter_sizes_restored_after_save_and_apply(self, main_window):
        dlm = main_window.dock_layout
        sp = main_window._main_splitter
        # 1) 设定一个独特比例并存为布局
        sp.setSizes([280, 600, 420])
        saved = sp.sizes()
        assert dlm.save_user_layout("比例布局") is True
        # 2) 改乱比例
        sp.setSizes([200, 800, 200])
        # 3) 应用回去 -> 比例还原
        dlm.apply_preset("比例布局")
        after = sp.sizes()
        assert after[0] == saved[0]
        assert after[2] == saved[2]

    def test_capture_layout_roundtrip_keys(self, main_window):
        dlm = main_window.dock_layout
        data = dlm.capture_layout()
        for key in ("qmain_state", "splitter_sizes", "left_tab_order",
                    "left_tab_index", "right_tab_order", "right_tab_index",
                    "left_panel_visible", "right_panel_visible"):
            assert key in data, f"capture_layout 缺字段 {key}"
        assert isinstance(data["splitter_sizes"], list)
        assert len(data["splitter_sizes"]) == 3


class TestDelete:
    def test_delete_user_layout_removes_from_combo(self, main_window):
        dlm = main_window.dock_layout
        dlm.save_user_layout("要删掉")
        main_window._refresh_layout_combo()
        assert "要删掉" in [main_window.layout_combo.itemText(i)
                            for i in range(main_window.layout_combo.count())]
        assert dlm.delete_user_layout("要删掉") is True
        main_window._refresh_layout_combo()
        assert "要删掉" not in [main_window.layout_combo.itemText(i)
                               for i in range(main_window.layout_combo.count())]

    def test_builtin_delete_blocked(self, main_window):
        assert main_window.dock_layout.delete_user_layout("专注") is False


class TestPanelMenu:
    def test_panel_menu_lists_registered_panels(self, main_window):
        assert len(main_window._panel_actions) >= 10

    def test_toggle_panel_hides_and_shows_tab(self, main_window):
        from panel_registry import registry
        spec = registry().get("control")
        loc = main_window._find_panel_tab(spec)
        assert loc is not None
        tab, idx = loc
        # 隐藏
        main_window._toggle_panel(spec, False)
        assert tab.isTabVisible(idx) is False
        # 恢复
        main_window._toggle_panel(spec, True)
        assert tab.isTabVisible(idx) is True


class TestFactoryReset:
    def test_reset_clears_user_layouts(self, main_window):
        dlm = main_window.dock_layout
        dlm.save_user_layout("要清掉的")
        assert "要清掉的" in dlm.list_layouts()
        dlm.reset_to_factory()
        assert dlm.list_layouts() == ["专注", "分析", "网格"]
        assert dlm.current_preset == "专注"


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
