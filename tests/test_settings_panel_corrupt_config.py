#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""W15：损坏配置的三档判定 + AI 配置写回合并（不覆盖未知键）。

三档判定（见 settings_panel._load_json_dict 文档）：
  1. 解析失败（截断 JSON / 非 UTF-8）：改名备份 *.corrupt-* + WARNING 日志 +
     状态行提示，内存按空配置继续；
  2. 顶层不是对象（数组/字符串/数字）：同样备份 + 日志 + 提示；
  3. 关键键类型不对：不备份、不删原文件，只把坏键按默认值显示；settings
     存储侧由 schema.validate_value 归一并在状态行给出原因（app_mode 例）。

写回合并：本程序认识的键按内存值更新，不认识的键原样保留；删除只认显式
deletes。反证：把合并改成整体覆盖，未知键必须消失（测试必须红）。
"""
import json
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import settings_panel as sp  # noqa: E402
from config_schema import SCHEMA  # noqa: E402
from settings import DesktopSettings  # noqa: E402


def _write(path, payload) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f)


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def ai_path(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    monkeypatch.setattr(sp, "AI_CONFIG_PATH", str(path))
    return path


def _make_panel(settings=None):
    panel = sp.SettingsPanel(settings=settings)
    panel._ensure_ui_built()
    return panel


# ---------------------------------------------------------------- 第 1 档
def test_truncated_json_is_visible_backed_up(ai_path, caplog):
    """截断 JSON：可见提示 + 备份留证 + 日志，绝不静默当「配置为空」。"""
    caplog.set_level(logging.WARNING)
    ai_path.write_text('{"temperature": 0.5, "api_key": "sk-live"',
                       encoding="utf-8")

    panel = _make_panel()
    try:
        text = panel.status_label.text()
        assert "配置读取失败" in text and "解析失败" in text, text
        assert not ai_path.exists(), "损坏文件应改名备份，不能留在原路径"
        backups = [p for p in ai_path.parent.glob("config.json.corrupt-*")
                   if not p.name.endswith(".reason.txt")]
        assert len(backups) == 1, backups
        reason = backups[0].with_name(backups[0].name + ".reason.txt")
        assert reason.exists(), "备份必须成对写理由，否则诊断页查不到"
        assert "解析失败" in reason.read_text(encoding="utf-8")
        assert panel._ai == {}
        assert any("解析失败" in r.getMessage() for r in caplog.records), \
            "损坏配置必须写 WARNING 日志"
    finally:
        panel.deleteLater()


# ---------------------------------------------------------------- 第 2 档
def test_top_level_array_is_visible_backed_up(ai_path):
    """顶层是数组：合法 JSON 但结构不符，同样备份 + 提示。"""
    ai_path.write_text("[1, 2, 3]", encoding="utf-8")

    panel = _make_panel()
    try:
        text = panel.status_label.text()
        assert "顶层不是对象" in text, text
        assert "list" in text, text
        assert not ai_path.exists(), "结构不符也要先备份留证再回默认"
        assert list(ai_path.parent.glob("config.json.corrupt-*"))
        assert panel._ai == {}
    finally:
        panel.deleteLater()


# ---------------------------------------------------------------- 第 3 档
def test_bad_typed_ai_key_falls_back_and_keeps_file(ai_path):
    """AI 配置键类型不对：不备份、不删文件，按默认值显示并说明。"""
    ai_path.write_text(json.dumps(
        {"temperature": "很热", "api_key": "sk-live", "future_key": 1}),
        encoding="utf-8")

    panel = _make_panel()
    try:
        text = panel.status_label.text()
        assert "类型不对" in text and "temperature" in text, text
        assert ai_path.exists(), "类型不符只影响显示，不许动原文件"
        assert not list(ai_path.parent.glob("config.json.corrupt-*"))
        assert panel._rows["ai_temperature"].value() == pytest.approx(
            float(SCHEMA["ai_temperature"].default))
    finally:
        panel.deleteLater()


def test_bad_typed_setting_key_falls_back_with_reason(tmp_path, ai_path):
    """settings 存储侧（app_mode 是数字）：validate_value 回默认并给原因。"""
    settings_path = tmp_path / "desktop_settings.json"
    _write(settings_path, {"app_mode": 3})
    ds = DesktopSettings.load(str(settings_path))
    assert ds.get("app_mode") == SCHEMA["app_mode"].default
    assert any("app_mode" in n for n in ds.migration_notes), ds.migration_notes

    panel = _make_panel(settings=ds)
    try:
        assert "app_mode" in panel.status_label.text()
    finally:
        panel.deleteLater()


# ---------------------------------------------------------------- 写回合并
def test_unknown_keys_survive_known_key_write(ai_path):
    """带未知键的配置：改一个已知键后，未知键与 api_key 必须还在。"""
    _write(ai_path, {"temperature": 0.7, "api_key": "sk-live",
                     "future_key": {"nested": [1, 2]}})
    panel = _make_panel()
    try:
        panel._rows["ai_temperature"].setValue(0.55)
        disk = _read(ai_path)
        assert disk["temperature"] == pytest.approx(0.55)
        assert disk["api_key"] == "sk-live"
        assert disk["future_key"] == {"nested": [1, 2]}
    finally:
        panel.deleteLater()


def test_merge_deletes_only_explicit_keys():
    """删除语义显式：不点名就不删，点名才删。"""
    disk = {"a": 1, "b": 2, "unknown": 3}
    sp._merge_ai_config(disk, {"a": 9})
    assert disk == {"a": 9, "b": 2, "unknown": 3}
    sp._merge_ai_config(disk, {"a": 10}, deletes=("b",))
    assert disk == {"a": 10, "unknown": 3}


def _whole_overwrite(disk, values, deletes=()):
    """变异：整体覆盖（W15 修复前的形态），未知键会被吃掉。"""
    disk.clear()
    disk.update(values)


def _check_unknown_keys_kept(panel, path, value):
    # 每次必须换成**不同的**数值：setValue 值不变不发 valueChanged，
    # 写回不会发生，反证就会空跑成「永远绿」。
    panel._rows["ai_temperature"].setValue(value)
    disk = _read(path)
    assert disk.get("api_key") == "sk-live", "api_key 被覆盖了（写回没有合并）"
    assert disk.get("future_key") == 1, "本程序不认识的键被覆盖了"


def test_mutation_whole_overwrite_turns_check_red(ai_path, monkeypatch):
    """反证：合并改回整体覆盖 → 同一检查必须红；恢复合并 → 绿。"""
    _write(ai_path, {"temperature": 0.7, "api_key": "sk-live",
                     "future_key": 1})
    panel = _make_panel()
    try:
        _check_unknown_keys_kept(panel, ai_path, 0.42)   # 基线：绿

        original = sp._merge_ai_config
        monkeypatch.setattr(sp, "_merge_ai_config", _whole_overwrite)
        with pytest.raises(AssertionError):
            _check_unknown_keys_kept(panel, ai_path, 0.43)

        # 只还原这一处：monkeypatch.undo() 会连 ai_path 夹具的路径补丁一起撤掉，
        # 后面的写入就会落到真实用户配置上。
        monkeypatch.setattr(sp, "_merge_ai_config", original)
        _write(ai_path, {"temperature": 0.7, "api_key": "sk-live",
                         "future_key": 1})
        _check_unknown_keys_kept(panel, ai_path, 0.44)    # 改回即绿
    finally:
        panel.deleteLater()
