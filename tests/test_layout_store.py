#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
LayoutStore JSON 持久化 offscreen 测试
======================================

直接用临时目录（tmp_path）注入 layouts.json 路径，不污染真实用户配置。
覆盖：list/save/load/delete/rename/active/reset + 损坏文件容错。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_layout_store.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture
def store(tmp_path):
    from layout_store import LayoutStore
    return LayoutStore(path=str(tmp_path / "layouts.json"))


def test_list_presets_returns_builtins(store):
    names = store.list_presets()
    assert names == ["专注", "分析", "网格"]


def test_save_and_load_user_preset(store):
    data = {"splitter_sizes": [300, 540, 360],
            "left_tab_order": ["射频天空", "频谱"],
            "left_tab_index": 1}
    assert store.save_preset("我的布局", data) is True
    names = store.list_presets()
    assert "我的布局" in names
    assert names[:3] == ["专注", "分析", "网格"]  # 内置在前
    loaded = store.load_preset("我的布局")
    assert loaded == data
    # active 自动指向刚存的
    assert store.get_active() == "我的布局"


def test_delete_user_preset(store):
    store.save_preset("临时", {"a": 1})
    assert "临时" in store.list_presets()
    assert store.delete_preset("临时") is True
    assert "临时" not in store.list_presets()


def test_builtin_cannot_be_deleted(store):
    assert store.is_builtin("专注") is True
    assert store.delete_preset("专注") is False
    assert "专注" in store.list_presets()


def test_builtin_can_be_overridden_by_user(store):
    """同名内置名允许被用户自定义覆盖（builtin 标记降级为 False）。"""
    store.save_preset("专注", {"custom": True})
    # 仍在列表里
    assert "专注" in store.list_presets()
    data = store.load_preset("专注")
    assert data == {"custom": True}


def test_rename_user_preset(store):
    store.save_preset("旧名", {"x": 1})
    assert store.rename_preset("旧名", "新名") is True
    assert "旧名" not in store.list_presets()
    assert "新名" in store.list_presets()
    assert store.get_active() == "新名"


def test_rename_builtin_blocked(store):
    assert store.rename_preset("专注", "别的") is False


def test_active_falls_back_after_delete(store):
    store.save_preset("要删", {"k": 1})
    assert store.get_active() == "要删"
    store.delete_preset("要删")
    assert store.get_active() == "专注"  # 回退到首个内置


def test_reset_to_factory(store):
    store.save_preset("u1", {})
    store.save_preset("u2", {})
    assert len(store.list_presets()) > 3
    assert store.reset_to_factory() is True
    assert store.list_presets() == ["专注", "分析", "网格"]
    assert store.get_active() == "专注"


def test_corrupted_file_does_not_crash(store, tmp_path):
    # 写坏 JSON
    with open(store.path, "w", encoding="utf-8") as f:
        f.write("{ not json !!!")
    # 不应崩，回退空骨架
    assert store.list_presets() == ["专注", "分析", "网格"]
    assert store.get_active() == "专注"


def test_empty_name_save_rejected(store):
    assert store.save_preset("", {}) is False


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
