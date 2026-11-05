# SPDX-License-Identifier: MIT
"""
tests/test_interaction_wiring.py
================================
交互覆盖守卫：**新增按钮/菜单项没接线就红**。

做法：运行时遍历主窗口的 QAction / QAbstractButton（复用审计脚本的
``iter_controls``），逐个断言主信号有接收者。这样以后加 UI 不会悄悄变哑。

另锁住本轮修掉的三类"点了没反应"：
  * 书签「编辑/删除」无选中时必须置灰（并给出提示）；
  * 「刷新过境」在拿不到 TLE 时必须说明"为什么没有过境"；
  * 设备列表「刷新」必须给按钮一个明确的已刷新反馈。

运行:
    QT_QPA_PLATFORM=offscreen MBDSDR_NULL_AUDIO=1 MBDSDR_NO_HW=1 \
        python -m pytest tests/test_interaction_wiring.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _isolate_settings():
    """备份并清空窗口几何/分栏持久化，测试后还原（同布局锁的做法）。"""
    s = QSettings("MBDSDR", "Desktop")
    keys = ("window_geometry", "three_column_sizes",
            "three_column_layout_version")
    backup = {k: s.value(k) for k in keys}
    for k in keys:
        s.remove(k)
    s.sync()

    def restore():
        for k, v in backup.items():
            if v is None:
                s.remove(k)
            else:
                s.setValue(k, v)
        s.sync()

    return restore


@pytest.fixture(scope="module")
def main_window():
    restore = _isolate_settings()
    from main_window import MainWindow
    win = MainWindow()
    for timer in getattr(win, "_startup_timers", []):
        try:
            timer.stop()          # 不自动连真硬件
        except Exception:  # noqa: BLE001
            pass
    win._panels_set_sdr_connected(False)
    win.show()
    app.processEvents()
    yield win
    try:
        win._disconnect()
    except Exception:  # noqa: BLE001
        pass
    win.close()
    win.deleteLater()
    app.processEvents()
    restore()


def test_every_action_and_button_has_a_receiver(main_window):
    """遍历式守卫：任何可交互控件主信号没接收者 -> 红。"""
    from ui_interaction_audit import iter_controls

    rows = list(iter_controls(main_window))
    assert len(rows) >= 100, (
        f"只遍历到 {len(rows)} 个动作，枚举器可能失效（防静默假绿）")

    unwired = [f"{kind} {name} @ {loc}"
               for kind, name, loc, _obj, wired, _note in rows if not wired]
    assert not unwired, "以下控件没有接槽（点了不会有任何反应）:\n" + "\n".join(
        unwired)


def test_controls_have_readable_labels(main_window):
    """动作必须有可读名称（空文本控件无法让用户理解点了会发生什么）。"""
    from ui_interaction_audit import iter_controls
    unnamed = [f"{kind} @ {loc}"
               for kind, name, loc, _obj, _w, _n in iter_controls(main_window)
               if not (name or "").strip()]
    assert not unnamed, "存在没有名称的交互控件:\n" + "\n".join(unnamed)


def test_bookmark_edit_delete_need_selection(tmp_path):
    """书签编辑/删除：没选中就置灰，选中后才可用（本轮修的死按钮）。"""
    from bookmark_panel import BookmarkPanel

    panel = BookmarkPanel(config_dir=tmp_path)
    assert not panel.edit_btn.isEnabled()
    assert not panel.del_btn.isEnabled()
    assert panel.edit_btn.toolTip(), "置灰必须写清原因"

    item = _make_item(panel)
    panel.tree.addTopLevelItem(item)
    panel.tree.setCurrentItem(item)
    app.processEvents()
    assert panel.edit_btn.isEnabled()
    assert panel.del_btn.isEnabled()
    panel.deleteLater()


def _make_item(panel):
    from PySide6.QtWidgets import QTreeWidgetItem
    from PySide6.QtCore import Qt
    item = QTreeWidgetItem(["测试书签", "145.0000", "NFM", "12.5"])
    item.setData(0, Qt.UserRole, {
        "frequency_hz": 145_000_000, "name": "测试书签",
        "modulation": "NFM", "bandwidth_hz": 12_500, "group": "custom",
    })
    return item


def test_refresh_passes_explains_missing_tle(main_window):
    """拿不到 TLE 时不能说"无数据"了事，必须说清楚是离线/无网络。"""
    panel = main_window.new_spacetime_panel
    # 站址已配置，才能真正走到「拿不到 TLE」这一分支（未配站址会先报站址）
    panel.set_observer(39.9, 116.4, 0.0)
    panel._tle_mgr.fetch = lambda *_a, **_k: None
    panel.refresh_passes()
    app.processEvents()
    assert not panel.passes_hint.isHidden(), "空态提示必须可见"
    text = panel.passes_hint.text()
    assert "TLE" in text and ("网络" in text or "离线" in text), (
        f"空态提示没有说明原因：{text!r}")


def test_device_refresh_gives_feedback(main_window):
    """设备列表刷新：列表没变化时也要给按钮一个明确反馈。"""
    panel = main_window.module_panel
    panel.refresh_devices()
    app.processEvents()
    assert panel.refresh_btn.text() == "已刷新", (
        "刷新按钮没有反馈（用户会以为点坏了）")
    panel.refresh_btn.setText("刷新")
