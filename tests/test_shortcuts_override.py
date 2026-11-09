# SPDX-License-Identifier: MIT
"""快捷键改键（「设置 → 快捷键」）：覆写层、冲突检测、热更新。

三条红线：

1. 改键只叠**覆写**，不改 ``shortcuts.py`` 的默认表（唯一事实源）；
   「恢复默认」= 删覆写键，不是往表里写第二份默认值。
2. **同 scope** 撞键必须挡住（同一控件里 Qt 无法消歧，两条都按不出来）；
   **跨 scope** 同键是设计允许的（A = 全天模式 / 频谱 AM，焦点在哪局部优先）。
3. 改完**不重启**就生效：QAction 与 QShortcut 都要重设，频谱的局部键
   还要重建（键位在 QShortcut 构造时就固定了）。

QSettings 一律走临时 ini：`QSettings("MBDSDR","Desktop")` 在 Windows
默认写注册表，测试直接用它等于改用户真实键位。
"""
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import shortcuts  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """把 QSettings 指到临时 ini，测试不动真实注册表。"""
    from PySide6.QtCore import QSettings

    ini = str(tmp_path / "shortcuts.ini")
    monkeypatch.setattr(shortcuts, "_store",
                        lambda: QSettings(ini, QSettings.IniFormat))
    monkeypatch.setattr(shortcuts, "_OVERRIDES", None, raising=False)
    yield ini
    monkeypatch.setattr(shortcuts, "_OVERRIDES", None, raising=False)


class TestOverrideLayer:
    def test_roundtrip_set_bind_clear(self, isolated_store):
        sid = "file.connect"
        default = shortcuts.default_key(sid)
        assert shortcuts.bind(sid) == default

        shortcuts.set_override(sid, "Ctrl+Alt+9")
        assert shortcuts.bind(sid) == "Ctrl+Alt+9"
        assert shortcuts.is_overridden(sid)

        shortcuts.clear_override(sid)
        assert shortcuts.bind(sid) == default, "恢复默认必须回到出厂键位"
        assert not shortcuts.is_overridden(sid)

    def test_empty_key_restores_default(self, isolated_store):
        """清空键序列＝恢复出厂键位（不是留一条空覆写把动作锁死）。

        这条与 UI 文案一致：改键对话框里「清空＝恢复默认」。真正的「这条
        不要快捷键」= 出厂就没配它（key=None），用户不需要造第三种状态。
        """
        sid = "tools.record"
        shortcuts.set_override(sid, "Ctrl+Alt+5")
        assert shortcuts.bind(sid) == "Ctrl+Alt+5"
        shortcuts.set_override(sid, "")
        assert shortcuts.bind(sid) == shortcuts.default_key(sid)
        assert not shortcuts.is_overridden(sid), "空值不该留成覆写记录"

    def test_unknown_id_rejected(self, isolated_store):
        with pytest.raises(KeyError):
            shortcuts.set_override("no.such.action", "Ctrl+Q")

    def test_default_table_untouched(self, isolated_store):
        """改键绝不改 shortcuts.py 的默认表——那是所有文档/帮助的唯一来源。"""
        sid = "file.connect"
        before = shortcuts.BY_ID[sid].key
        shortcuts.set_override(sid, "Ctrl+Alt+9")
        assert shortcuts.BY_ID[sid].key == before
        assert shortcuts.default_key(sid) == before

    def test_clear_all(self, isolated_store):
        shortcuts.set_override("file.connect", "Ctrl+Alt+9")
        shortcuts.set_override("tools.record", "Ctrl+Alt+8")
        shortcuts.clear_all_overrides()
        assert shortcuts.bind("file.connect") == shortcuts.default_key("file.connect")
        assert shortcuts.bind("tools.record") == shortcuts.default_key("tools.record")


class TestConflictDetection:
    def test_same_scope_conflict_is_reported(self, isolated_store):
        """同 scope 撞键：挡。挑两条都是 window scope 的。"""
        a, b = "file.connect", "file.disconnect"
        assert shortcuts.BY_ID[a].scope == shortcuts.BY_ID[b].scope
        trial = {a: "Ctrl+Alt+1", b: "Ctrl+Alt+1"}
        found = shortcuts.conflicts(trial)
        assert found, "同 scope 同键必须被识别成冲突"
        assert {found[0][2], found[0][3]} == {a, b}

    def test_cross_scope_same_key_allowed(self, isolated_store):
        """跨 scope 同键合法：A 归全天模式，频谱获焦时归 AM。"""
        window_ids = [s.id for s in shortcuts.SHORTCUTS
                      if s.scope == "window" and s.key == "A"]
        spectrum_ids = [s.id for s in shortcuts.SHORTCUTS
                        if s.scope == "spectrum" and s.key == "A"]
        assert window_ids and spectrum_ids, "设计上应有 window/spectrum 各一条 A"
        assert not shortcuts.conflicts({}), "出厂键位本身不应有同 scope 冲突"

    def test_unbound_entries_do_not_conflict(self, isolated_store):
        trial = {"file.connect": "", "file.disconnect": ""}
        assert not shortcuts.conflicts(trial), "两个解绑不算撞键"


class TestHotApply:
    def test_apply_updates_action_without_restart(self, qtapp, isolated_store):
        """改键后主窗 QAction 立刻换键（不重启）。"""
        from main_window import MainWindow

        w = MainWindow()
        try:
            sid = "file.disconnect"
            default = shortcuts.default_key(sid)
            assert w._shortcut_actions[sid].shortcut().toString() == default

            shortcuts.set_override(sid, "Ctrl+Alt+8")
            changed = w.apply_shortcut_overrides()
            assert changed >= 1, "改一条键至少要报告 1 条热更新"
            assert (w._shortcut_actions[sid].shortcut().toString()
                    == "Ctrl+Alt+8")

            shortcuts.clear_override(sid)
            w.apply_shortcut_overrides()
            assert w._shortcut_actions[sid].shortcut().toString() == default
        finally:
            w.close()
            qtapp.processEvents()

    def test_spectrum_local_keys_rebuild(self, qtapp, isolated_store):
        """频谱的解调模式局部键也要跟着改（QShortcut 对象要重建）。"""
        from spectrum_widget import SpectrumPanel

        panel = SpectrumPanel()
        spec = panel._fft_plot        # SpectrumCurveWidget，局部键持有者
        try:
            sid = "spectrum.mode.am"
            before = [sc.key().toString() for sc in spec._local_mode_shortcuts]
            shortcuts.set_override(sid, "Ctrl+Alt+7")
            spec.apply_shortcut_overrides()
            after = [sc.key().toString() for sc in spec._local_mode_shortcuts]
            assert "Ctrl+Alt+7" in after, f"局部键没跟着改：{after}"
            assert after != before
        finally:
            panel.deleteLater()
            qtapp.processEvents()

    def test_no_override_apply_is_noop(self, qtapp, isolated_store):
        """没有覆写时热更新不该乱改（否则会把出厂键位洗成空）。"""
        from main_window import MainWindow

        w = MainWindow()
        try:
            assert w.apply_shortcut_overrides() == 0
            assert (w._shortcut_actions["file.connect"].shortcut().toString()
                    == shortcuts.default_key("file.connect"))
        finally:
            w.close()
            qtapp.processEvents()


class TestPanelShowsEffectiveKey:
    def test_key_text_follows_override(self, isolated_store):
        """面板显示的是**当前生效**键位：用户改过就跟改后的走。"""
        from shortcuts_panel import key_text

        entry = shortcuts.BY_ID["tools.record"]
        assert key_text(entry) == shortcuts.default_key(entry.id)
        shortcuts.set_override(entry.id, "Ctrl+Alt+6")
        assert key_text(entry) == "Ctrl+Alt+6"

    def test_unbound_shows_placeholder(self, isolated_store):
        from shortcuts_panel import key_text

        entry = next(s for s in shortcuts.SHORTCUTS if s.key is None)
        assert key_text(entry) == "无快捷键"
