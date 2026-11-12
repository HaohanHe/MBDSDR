# SPDX-License-Identifier: MIT
"""LayoutStore 损坏恢复回归锁（套用书签 / 桌面配置同一套模板）。

背景：``desktop/layout_store.py`` 旧实现 ``_load`` 遇到损坏只打 warning 就返回
空骨架，下一次 ``save_preset`` / ``set_active`` 立刻把用户布局整份写回成
「只剩内置项」，原件从此丢失。书签与桌面配置已经改成
「损坏 → 备份 → blocked」，本文件锁住布局也遵守同一套状态机。

四条契约：
  1. 文件缺失 = 首次启动正常路径：空骨架、不算错误、不阻塞写入；
  2. 文件损坏：先备份 ``*.corrupt-时间戳`` 再降级，``last_error`` 留人话；
     备份成功才允许后续写新内容，证据留在备份里；
  3. 备份失败 / 读不了（OSError）：不备份、不写入、置 ``_save_blocked``，
     坏文件原样留在磁盘上，绝不用空骨架覆盖；
  4. 每个公开操作都会重新 ``_load()``，所以 blocked 必须挂在实例上；
     二次写盘不能重复造备份。

反证：把旧实现原样喂给同一组断言，必须 ``AssertionError``。

运行（不依赖 conftest / QApplication）:
    python -m pytest tests/test_layout_store_corruption_recovery.py -v --noconftest
"""
from __future__ import annotations

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

import layout_store as layout_store_module  # noqa: E402
from layout_store import BUILTIN_LAYOUTS, LayoutStore  # noqa: E402

_CORRUPT_BYTES = b'{"version": 1, "presets": { broken !!!'


def _backups(tmp_path, name: str = "layouts.json"):
    """临时目录里的损坏备份（排除成对的 ``.reason.txt``）。"""
    return sorted(
        p for p in tmp_path.iterdir()
        if p.name.startswith(name + ".corrupt-")
        and not p.name.endswith(".reason.txt")
    )


def _assert_corrupt_evidence_kept(store, tmp_path) -> None:
    """损坏后写回：必须先有备份、必须留人话、备份字节等于原件。"""
    path = tmp_path / "layouts.json"
    path.write_bytes(_CORRUPT_BYTES)
    assert store.save_preset("我的布局", {"x": 1}) is True

    backups = _backups(tmp_path)
    assert len(backups) == 1, "损坏文件没有备份就被整份写回，原件已丢"
    assert backups[0].read_bytes() == _CORRUPT_BYTES, "备份不是原件字节"
    assert store.corrupt_backup == str(backups[0])
    assert store.last_error, "损坏后没有留下任何用户可见说明"
    assert "已备份为" in store.last_error, store.last_error
    assert (backups[0].with_name(backups[0].name + ".reason.txt")).exists(), \
        "备份旁没有成对的损坏理由"


# ---------------------------------------------------------------------------
# 1. 文件缺失 = 首次启动，不是损坏
# ---------------------------------------------------------------------------
def test_missing_file_is_first_run_not_corruption(tmp_path):
    store = LayoutStore(path=str(tmp_path / "layouts.json"))
    assert store.list_presets() == list(BUILTIN_LAYOUTS)
    assert store.last_error is None
    assert store.corrupt_backup is None
    assert store._save_blocked is False
    assert _backups(tmp_path) == []

    # 首次启动必须能正常存布局
    assert store.save_preset("首次", {"sizes": [1, 2, 3]}) is True
    saved = json.loads((tmp_path / "layouts.json").read_text(encoding="utf-8"))
    assert saved["presets"]["首次"]["data"] == {"sizes": [1, 2, 3]}


# ---------------------------------------------------------------------------
# 2. 损坏：备份 + last_error，再写新内容也不丢证据
# ---------------------------------------------------------------------------
def test_corrupt_file_is_backed_up_and_reported(tmp_path):
    path = tmp_path / "layouts.json"
    path.write_bytes(_CORRUPT_BYTES)
    store = LayoutStore(path=str(path))

    # 读失败不得崩溃，也不得静默成「只剩内置项」而无任何说明
    assert store.list_presets() == list(BUILTIN_LAYOUTS)
    assert len(_backups(tmp_path)) == 1
    assert "已备份为" in (store.last_error or "")


def test_corrupt_backup_then_save_keeps_evidence(tmp_path):
    _assert_corrupt_evidence_kept(LayoutStore(path=str(tmp_path / "layouts.json")),
                                  tmp_path)


def test_second_save_does_not_create_an_extra_backup(tmp_path):
    path = tmp_path / "layouts.json"
    path.write_bytes(_CORRUPT_BYTES)
    store = LayoutStore(path=str(path))
    assert store.save_preset("一", {}) is True
    assert store.save_preset("二", {}) is True
    assert len(_backups(tmp_path)) == 1, "二次写盘又造了一份备份"


# ---------------------------------------------------------------------------
# 3. 备份失败 / 读不了：拒绝写盘，坏文件原样保留
# ---------------------------------------------------------------------------
def test_backup_failure_blocks_writes_and_keeps_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "layouts.json"
    path.write_bytes(_CORRUPT_BYTES)
    monkeypatch.setattr(layout_store_module, "_backup_corrupt_layout",
                        lambda _path: None)

    store = LayoutStore(path=str(path))
    assert store.list_presets() == list(BUILTIN_LAYOUTS)
    assert store._save_blocked is True, "备份失败后没有挂 blocked 状态"
    assert store.corrupt_backup is None
    assert "备份失败" in (store.last_error or "")

    assert store.save_preset("新布局", {"a": 1}) is False
    assert path.read_bytes() == _CORRUPT_BYTES, "唯一证据被空骨架覆盖"
    assert _backups(tmp_path) == []
    assert "未保存" in (store.last_error or "")


def test_oserror_is_not_corruption_but_also_blocks(tmp_path):
    # 把配置文件路径做成目录：open() 抛 OSError，但文件并没有「损坏」
    blocked = tmp_path / "layouts.json"
    blocked.mkdir()
    store = LayoutStore(path=str(blocked))

    assert store.list_presets() == list(BUILTIN_LAYOUTS)
    assert _backups(tmp_path) == []
    assert "读取失败" in (store.last_error or "")
    assert "已备份为" not in (store.last_error or "")
    assert store._save_blocked is True

    assert store.save_preset("x", {}) is False
    assert blocked.is_dir(), "读不了的文件被当成空骨架替换掉了"
    assert not any(blocked.iterdir()), "读失败后仍在目标位置写了文件"


# ---------------------------------------------------------------------------
# 4. 反证：旧实现喂同一组断言必须红
# ---------------------------------------------------------------------------
class _LegacyLayoutStore:
    """旧实现：读失败只 warning 后返回空骨架，随后整份写回。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self.last_error = None
        self.corrupt_backup = None
        self._save_blocked = False

    def _load(self):
        empty = {"version": 1, "active": BUILTIN_LAYOUTS[0], "presets": {}}
        if not os.path.exists(self._path):
            return empty
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if not isinstance(obj, dict) or not isinstance(obj.get("presets"), dict):
                return empty
            return obj
        except Exception:  # noqa: BLE001 - 旧实现就是静默吞掉
            return empty

    def _save(self, obj) -> bool:
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return True

    def save_preset(self, name: str, data) -> bool:
        obj = self._load()
        obj.setdefault("presets", {})[name] = {"builtin": False, "data": data}
        obj["active"] = name
        return self._save(obj)


def test_legacy_silent_fallback_turns_red(tmp_path):
    with pytest.raises(AssertionError):
        _assert_corrupt_evidence_kept(
            _LegacyLayoutStore(str(tmp_path / "layouts.json")), tmp_path)


# ---------------------------------------------------------------------------
# 5. 错误上界面：复用已有状态栏，不新建任何界面
# ---------------------------------------------------------------------------
def test_store_error_reaches_existing_status_bar():
    from dock_layout_manager import DockLayoutManager

    class _Bar:
        def __init__(self):
            self.messages = []

        def showMessage(self, text, timeout):  # noqa: N802 - Qt 命名
            self.messages.append((text, timeout))

    class _MW:
        def __init__(self):
            self.bar = _Bar()

        def statusBar(self):  # noqa: N802 - Qt 命名
            return self.bar

    class _Store:
        last_error = "布局文件读取失败（解析失败）：...；原文件已备份为 X，本次按空布局启动"
        path = "layouts.json"

    mgr = DockLayoutManager.__new__(DockLayoutManager)
    mgr.mw = _MW()
    mgr.store = _Store()
    mgr.last_load_error = None
    mgr.last_save_error = None

    assert mgr._surface_store_error() is True
    assert mgr.last_load_error == _Store.last_error
    assert mgr.mw.bar.messages
    assert "已备份为" in mgr.mw.bar.messages[0][0]
    assert mgr.mw.bar.messages[0][1] == 15000


def test_status_bar_failure_does_not_break_startup():
    from dock_layout_manager import DockLayoutManager

    class _BrokenMW:
        def statusBar(self):  # noqa: N802 - Qt 命名
            raise RuntimeError("没有主窗口")

    class _Store:
        last_error = "布局文件读取失败：原文件已备份为 X"
        path = "layouts.json"

    mgr = DockLayoutManager.__new__(DockLayoutManager)
    mgr.mw = _BrokenMW()
    mgr.store = _Store()
    mgr.last_load_error = None
    mgr.last_save_error = None

    # 状态栏不可用时只记日志，不抛异常拖垮启动
    assert mgr._surface_store_error() is True
    assert mgr.last_load_error == _Store.last_error
