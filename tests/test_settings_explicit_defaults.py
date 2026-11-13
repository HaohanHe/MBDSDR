# SPDX-License-Identifier: MIT
"""设置项「用户显式设置过」标记（QGIS 对照项）的行为用例。

QGIS 能区分「用了默认值」和「用户明确设成这个值」；我们此前只有一份完整
快照，升级改默认值会把从没碰过的项和用户设过的项一视同仁。这里锁住三件事：

1. 用户显式设过的值，默认值变更后必须原样保留；
2. 从未设置过的项，磁盘上的旧默认值不得盖住当前版本的新默认值；
3. 设置界面能直接看出某项是「默认值」还是「已自定义」（不是只存内部字段）。
"""
import json

import pytest
from PySide6.QtWidgets import QApplication

import desktop  # noqa: F401 - 把 desktop/ 放进 sys.path
import settings as settings_mod
import settings_panel as sp
from settings import DEFAULTS, EXPLICIT_KEYS_FIELD, DesktopSettings


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_explicit_value_survives_default_change(tmp_path, monkeypatch):
    """用户设过的值不许被新版本默认值覆盖（带标记持久化）。"""
    path = str(tmp_path / "desktop_settings.json")
    s = DesktopSettings(path=path)
    s.set("callsign", "BG1ABC")
    s.flush()

    raw = _read(path)
    assert raw[EXPLICIT_KEYS_FIELD] == ["callsign"], raw.get(EXPLICIT_KEYS_FIELD)
    assert s.is_explicit("callsign")
    assert s.value_source("callsign") == "explicit"

    # 模拟新版本把 callsign 的默认值改掉。
    monkeypatch.setitem(settings_mod.DEFAULTS, "callsign", "NEW-DEFAULT")
    loaded = DesktopSettings.load(path=path)
    assert loaded.get("callsign") == "BG1ABC", (
        "用户显式设过的值被新默认值覆盖了——存档里的标记没起作用")
    assert loaded.is_explicit("callsign")


def test_unset_value_follows_current_default(tmp_path, monkeypatch):
    """从没设置过的项跟随当前默认值，不被文件里的旧默认值钉住。"""
    path = str(tmp_path / "desktop_settings.json")
    s = DesktopSettings(path=path)
    s.set("callsign", "BG1ABC")          # 只有这一项是用户值
    s.flush()

    raw = _read(path)
    old_default = DEFAULTS["squelch_default_db"]
    # 文件仍是完整快照（既有约定），但未显式设置的项会被读回时忽略。
    assert raw["squelch_default_db"] == old_default
    assert not s.is_explicit("squelch_default_db")
    assert s.value_source("squelch_default_db") == "default"

    monkeypatch.setitem(settings_mod.DEFAULTS, "squelch_default_db",
                        old_default - 1)
    loaded = DesktopSettings.load(path=path)
    assert loaded.get("squelch_default_db") == old_default - 1, (
        "未显式设置的项应跟随新默认值，而不是沿用文件里的旧默认值")
    assert not loaded.is_explicit("squelch_default_db")


def test_reset_clears_explicit_marker(tmp_path, monkeypatch):
    """恢复默认 = 重新跟随默认值，标记必须一起清掉。"""
    path = str(tmp_path / "desktop_settings.json")
    s = DesktopSettings(path=path)
    s.set("callsign", "BG1ABC")
    s.flush()
    s.reset("callsign")
    s.flush()

    assert not s.is_explicit("callsign")
    assert _read(path)[EXPLICIT_KEYS_FIELD] == []

    monkeypatch.setitem(settings_mod.DEFAULTS, "callsign", "NEW-DEFAULT")
    loaded = DesktopSettings.load(path=path)
    assert loaded.get("callsign") == "NEW-DEFAULT"


def test_legacy_file_without_marker_is_conservatively_explicit(tmp_path,
                                                               monkeypatch):
    """旧文件没有标记：无法回溯，保守按「出现即用户值」迁移，不丢用户设置。"""
    path = str(tmp_path / "desktop_settings.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"callsign": "OLD-USER", "squelch_default_db": -50}, f)

    s = DesktopSettings.load(path=path)
    assert s.is_explicit("callsign")
    assert s.get("callsign") == "OLD-USER"

    monkeypatch.setitem(settings_mod.DEFAULTS, "callsign", "NEW-DEFAULT")
    assert DesktopSettings.load(path=path).get("callsign") == "OLD-USER"


def test_panel_shows_default_vs_customized(qapp, isolated_profile, tmp_path,
                                           monkeypatch):
    """界面能看出来源：用户设过 → 「已自定义」，没碰过 → 「默认值」。"""
    monkeypatch.setattr(sp, "AI_CONFIG_PATH", str(tmp_path / "ai.json"))
    monkeypatch.setattr(sp, "GUI_CONFIG_PATH", str(tmp_path / "gui.json"))
    (tmp_path / "ai.json").write_text("{}", encoding="utf-8")

    ds = DesktopSettings.load(path=str(tmp_path / "desktop_settings.json"))
    ds.set("callsign", "BG1ABC")
    panel = sp.SettingsPanel(settings=ds)
    try:
        panel._ensure_ui_built()
        assert panel._origin_labels["callsign"].text() == "已自定义"
        assert panel._origin_labels["squelch_default_db"].text() == "默认值"

        panel._reset_one("callsign")
        assert panel._origin_labels["callsign"].text() == "默认值", (
            "恢复默认后界面必须同步显示「默认值」")
    finally:
        panel.close()
        qapp.processEvents()
