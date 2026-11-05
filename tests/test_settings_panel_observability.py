"""设置面板可观察性红测试（交互审计：打开目录 / 恢复默认幂等）。"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import settings_panel as sp  # noqa: E402
from config_schema import SCHEMA  # noqa: E402

_ENV_KEYS = ("rc_enabled", "rc_port", "web_enabled", "web_port")


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


def test_open_record_dir_missing_shows_path_and_never_opens(monkeypatch):
    panel = sp.SettingsPanel(settings=None)
    panel._record_effective_dir = lambda: "Z:\\no-such-record-dir"
    fake = _FakeDesktopServices()
    monkeypatch.setattr(sp, "QDesktopServices", fake)

    panel._open_record_dir()

    assert fake.calls == [], "目录不存在时不得打开空窗口"
    assert "录音目录不存在" in panel.status_label.text()
    assert "Z:\\no-such-record-dir" in panel.status_label.text()


def test_open_record_dir_success_reports_what_was_opened(tmp_path, monkeypatch):
    panel = sp.SettingsPanel(settings=None)
    panel._record_effective_dir = lambda: str(tmp_path)
    fake = _FakeDesktopServices(result=True)
    monkeypatch.setattr(sp, "QDesktopServices", fake)

    panel._open_record_dir()

    assert len(fake.calls) == 1
    assert "已打开目录" in panel.status_label.text()
    assert str(tmp_path) in panel.status_label.text()


def test_open_config_dir_missing_is_observable(tmp_path, monkeypatch):
    panel = sp.SettingsPanel(settings=None)
    fake = _FakeDesktopServices()
    monkeypatch.setattr(sp, "QDesktopServices", fake)
    monkeypatch.setattr(sp.os.path, "expanduser",
                        lambda p: str(tmp_path / "no-mbdsdr"))

    panel._on_open_dir_clicked()

    assert fake.calls == []
    assert "配置目录不存在" in panel.status_label.text()


def test_reset_defaults_already_default_says_so():
    defaults = {k: SCHEMA[k].default for k in _ENV_KEYS}
    stub = _StubSettings(defaults, defaults)
    panel = sp.ServiceSettingsPanel(settings=stub)

    panel.reset_defaults()

    assert stub.reset_calls == [], "已是默认值不应假装恢复了一次"
    assert panel.error_label.text() == "当前已是默认值"


def test_reset_defaults_really_restores_when_dirty():
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
