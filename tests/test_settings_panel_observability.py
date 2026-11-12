"""设置面板可观察性红测试（交互审计：打开目录 / 恢复默认幂等）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import settings_panel as sp  # noqa: E402
from config_schema import SCHEMA  # noqa: E402
from settings import DesktopSettings  # noqa: E402

_ENV_KEYS = ("rc_enabled", "rc_port", "web_enabled", "web_port")


@pytest.fixture(scope="module")
def qapp():
    """面板是 QWidget：子集单跑没有 QApplication 时 Qt 会直接 abort 0xC0000409。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _StubSettings:
    def __init__(self, values, defaults):
        self.values = dict(values)
        self.defaults = dict(defaults)
        self.reset_calls = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def reset(self, key):
        self.reset_calls.append(key)
        self.values[key] = self.defaults[key]


class _FakeDesktopServices:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def openUrl(self, url):
        self.calls.append(url)
        return self.result


def test_open_record_dir_missing_shows_path_and_never_opens(qapp, monkeypatch):
    panel = sp.SettingsPanel(settings=None)
    panel._record_effective_dir = lambda: "Z:\\no-such-record-dir"
    fake = _FakeDesktopServices()
    monkeypatch.setattr(sp, "QDesktopServices", fake)

    panel._open_record_dir()

    assert fake.calls == [], "目录不存在时不得打开空窗口"
    assert "录音目录不存在" in panel.status_label.text()
    assert "Z:\\no-such-record-dir" in panel.status_label.text()


def test_open_record_dir_success_reports_what_was_opened(qapp, tmp_path, monkeypatch):
    panel = sp.SettingsPanel(settings=None)
    panel._record_effective_dir = lambda: str(tmp_path)
    fake = _FakeDesktopServices(result=True)
    monkeypatch.setattr(sp, "QDesktopServices", fake)

    panel._open_record_dir()

    assert len(fake.calls) == 1
    assert "已打开目录" in panel.status_label.text()
    assert str(tmp_path) in panel.status_label.text()


def test_open_config_dir_missing_is_observable(qapp, tmp_path, monkeypatch):
    panel = sp.SettingsPanel(settings=None)
    fake = _FakeDesktopServices()
    monkeypatch.setattr(sp, "QDesktopServices", fake)
    monkeypatch.setattr(sp.os.path, "expanduser",
                        lambda p: str(tmp_path / "no-mbdsdr"))

    panel._on_open_dir_clicked()

    assert fake.calls == []
    assert "配置目录不存在" in panel.status_label.text()


def test_reset_defaults_already_default_says_so(qapp):
    defaults = {k: SCHEMA[k].default for k in _ENV_KEYS}
    stub = _StubSettings(defaults, defaults)
    panel = sp.ServiceSettingsPanel(settings=stub)

    panel.reset_defaults()

    assert stub.reset_calls == [], "已是默认值不应假装恢复了一次"
    assert panel.error_label.text() == "当前已是默认值"


def test_reset_defaults_really_restores_when_dirty(qapp):
    defaults = {k: SCHEMA[k].default for k in _ENV_KEYS}
    dirty = dict(defaults)
    dirty["rc_port"] = 9999
    dirty["web_enabled"] = True
    stub = _StubSettings(dirty, defaults)
    panel = sp.ServiceSettingsPanel(settings=stub)

    panel.reset_defaults()

    assert len(stub.reset_calls) == 4, "非默认时必须逐项回退"
    for key in _ENV_KEYS:
        assert stub.values[key] == defaults[key]
    assert panel.error_label.text() == "已恢复本组默认值"


# ---------------------------------------------------------------------- #
# 写盘成败可见性：AI 配置写失败不得被「已保存/已恢复默认」冲掉
# ---------------------------------------------------------------------- #
def _ai_panel(monkeypatch, tmp_path):
    # 两条真实路径都要隔离：AI 配置 + 桌面设置。reset_all 会逐项写
    # DesktopSettings，settings=None 时它落的是用户真实配置（守门已抓到过）。
    monkeypatch.setattr(sp, "AI_CONFIG_PATH", str(tmp_path / "config.json"))
    return sp.SettingsPanel(
        settings=DesktopSettings(path=str(tmp_path / "desktop_settings.json")))


class _FailingConfigStore:
    """AI 配置的真实写入口是 ``ConfigStore.update``。

    旧用例 patch ``_atomic_write_json`` 已不生效（AI 写盘迁到 ConfigStore 后
    那个函数不再参与），于是失败注入空跑、用例永远报「已保存」。这里从
    真实接缝注入：构造可过，``update`` 必抛。
    """

    def __init__(self, *_a, **_k):
        pass

    def update(self, _fn):
        raise OSError("disk full")


def test_ai_config_write_failure_is_not_overwritten_by_saved(qapp, monkeypatch,
                                                             tmp_path):
    panel = _ai_panel(monkeypatch, tmp_path)
    monkeypatch.setattr(sp, "ConfigStore", _FailingConfigStore)

    panel._on_changed("ai_temperature", 0.55)

    msg = panel.status_label.text()
    assert "未保存" in msg
    assert "AI 配置写入失败" in msg and "disk full" in msg
    assert "已保存" not in msg, "写盘失败时不得出现成功文案"


def test_ai_config_write_success_still_says_saved(qapp, monkeypatch, tmp_path):
    panel = _ai_panel(monkeypatch, tmp_path)

    panel._on_changed("ai_temperature", 0.55)

    assert "已保存" in panel.status_label.text()


def test_ai_config_reset_failure_is_not_reported_as_done(qapp, monkeypatch,
                                                         tmp_path):
    panel = _ai_panel(monkeypatch, tmp_path)
    monkeypatch.setattr(sp, "ConfigStore", _FailingConfigStore)

    panel._reset_one("ai_temperature")

    msg = panel.status_label.text()
    assert "恢复默认未落盘" in msg and "disk full" in msg
    assert "已恢复默认" not in msg


def test_reset_all_reports_partial_persist_failure(qapp, monkeypatch, tmp_path):
    panel = _ai_panel(monkeypatch, tmp_path)
    monkeypatch.setattr(sp, "ConfigStore", _FailingConfigStore)

    panel.reset_all(confirm=False)

    msg = panel.status_label.text()
    assert "恢复默认未完全落盘" in msg and "disk full" in msg
    assert "已恢复全部默认" not in msg
