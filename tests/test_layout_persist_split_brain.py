# SPDX-License-Identifier: MIT
"""布局持久化：QSettings / layouts.json 双写分叉必须可见且可自愈。

旧实现 `save_state()` 把 `store.set_active()` 的失败 `except: pass` 吞掉，
`restore_state()` 又无条件优先信任 JSON 的旧 active —— 布局切换后重启回旧
布局，全程无提示。新实现：失败返回 False + `last_save_error`；QSettings 记
时间戳，restore 时只在「QSettings 确实比 JSON 新」时判定上次 JSON 写盘失败
并回写，反之（用户另存布局只写了 JSON）保持 JSON 的 active。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

import dock_layout_manager as dlm_mod  # noqa: E402
from layout_store import LayoutStore, BUILTIN_LAYOUTS  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakeQSettings:
    """内存版 QSettings，避免测试写用户真实注册表/配置文件。"""

    DB = {}

    def __init__(self, org, app):
        self._d = _FakeQSettings.DB.setdefault((org, app), {})

    def setValue(self, key, value):
        self._d[key] = value

    def value(self, key, default=None):
        return self._d.get(key, default)

    def sync(self):
        return None


@pytest.fixture
def env(qapp, tmp_path, monkeypatch):
    _FakeQSettings.DB.clear()
    monkeypatch.setattr(dlm_mod, "QSettings", _FakeQSettings)
    win = QMainWindow()
    store = LayoutStore(str(tmp_path / "layouts.json"))
    dlm = dlm_mod.DockLayoutManager(win, store=store)
    yield dlm, store, win, _FakeQSettings.DB
    win.close()


def _qs():
    return _FakeQSettings.DB.setdefault(("MBDSDR", "Desktop"), {})


# ------------------------------------------------------------ save_state
class TestSaveState:
    def test_success_is_reported_and_both_channels_agree(self, env):
        dlm, store, _, _ = env
        dlm.current_preset = BUILTIN_LAYOUTS[1]
        assert dlm.save_state() is True
        assert dlm.last_save_error is None
        assert store.get_active() == BUILTIN_LAYOUTS[1]
        assert _qs()["layout_preset"] == BUILTIN_LAYOUTS[1]
        assert _qs()["layout_preset_ts"] > 0

    def test_set_active_false_is_a_visible_failure(self, env, monkeypatch):
        """后端显式返回 False（原子写失败）也算失败，不能被当成功。"""
        dlm, store, _, _ = env
        dlm.current_preset = BUILTIN_LAYOUTS[1]
        monkeypatch.setattr(store, "set_active", lambda _n: False)
        assert dlm.save_state() is False
        assert "写入 layouts.json 失败" in (dlm.last_save_error or "")
        assert _qs()["layout_preset"] == BUILTIN_LAYOUTS[1], "QSettings 仍应写成功"

    def test_set_active_exception_is_not_swallowed(self, env, monkeypatch):
        dlm, store, _, _ = env
        dlm.current_preset = BUILTIN_LAYOUTS[1]
        store.set_active(BUILTIN_LAYOUTS[0])

        def _boom(_n):
            raise OSError("disk full")

        monkeypatch.setattr(store, "set_active", _boom)
        assert dlm.save_state() is False
        assert "disk full" in (dlm.last_save_error or "")
        assert store.get_active() == BUILTIN_LAYOUTS[0], "JSON 仍是旧值"


# --------------------------------------------------------- restore_state
class TestRestoreCrossCheck:
    def test_failed_json_write_self_heals_on_restart(self, env, monkeypatch):
        """上次 JSON 写盘失败：QSettings 比 JSON 新 → 按 QSettings 恢复并回写。"""
        dlm, store, _, _ = env
        store.set_active(BUILTIN_LAYOUTS[0])
        dlm.current_preset = BUILTIN_LAYOUTS[1]
        original_set_active = store.set_active

        def _boom(_n):
            raise OSError("disk full")

        monkeypatch.setattr(store, "set_active", _boom)
        assert dlm.save_state() is False
        # 只恢复这一个属性；不能 monkeypatch.undo()，那会把 fixture 里替换的
        # QSettings 一起撤掉，restore_state 就写到用户真实配置了。
        monkeypatch.setattr(store, "set_active", original_set_active)

        assert store.get_active() == BUILTIN_LAYOUTS[0]
        dlm.restore_state()
        assert dlm.current_preset == BUILTIN_LAYOUTS[1]
        assert store.get_active() == BUILTIN_LAYOUTS[1], "分叉应被回写修复"

    def test_newer_json_wins_when_user_saved_layout(self, env):
        """用户另存布局只写 JSON：JSON 更新，不许被 QSettings 旧值顶掉。"""
        dlm, store, _, _ = env
        dlm.current_preset = BUILTIN_LAYOUTS[0]
        assert dlm.save_state() is True
        # 模拟 save_user_layout：只写 JSON，不动 QSettings
        time.sleep(0.01)
        assert store.set_active(BUILTIN_LAYOUTS[2]) is True
        assert _qs()["layout_preset"] == BUILTIN_LAYOUTS[0]

        dlm.restore_state()
        assert dlm.current_preset == BUILTIN_LAYOUTS[2]
        assert store.get_active() == BUILTIN_LAYOUTS[2]

    def test_legacy_qsettings_without_timestamp_keeps_json(self, env):
        """旧版本 QSettings 没有时间戳：不猜、不覆盖 JSON 的 active。"""
        dlm, store, _, _ = env
        store.set_active(BUILTIN_LAYOUTS[1])
        _qs()["layout_preset"] = BUILTIN_LAYOUTS[0]
        _qs().pop("layout_preset_ts", None)
        dlm.restore_state()
        assert dlm.current_preset == BUILTIN_LAYOUTS[1]

    def test_missing_json_falls_back_to_qsettings(self, env):
        dlm, store, _, _ = env
        _qs()["layout_preset"] = BUILTIN_LAYOUTS[2]
        _qs()["layout_preset_ts"] = time.time()
        dlm.restore_state()
        assert dlm.current_preset == BUILTIN_LAYOUTS[2]
