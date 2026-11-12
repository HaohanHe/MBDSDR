# SPDX-License-Identifier: MIT
"""只读诊断页（W106）三条硬约束。

1. **纯展示**：AST 扫模块源码，禁止任何写盘 / 删除调用与 shutil 导入——
   不是「这次没加清理按钮」，而是以后想加也会被拦红；
2. **长文本不截断**：整段异常理由原样进展示行，由 QTextEdit + QScrollArea
   滚动，不按字符切；
3. **查不到必须显式**：unwired 实例、未写入日志、不可读目录各占一行。

本文件不导入 PySide6（最后一个用例用 importorskip）。
"""

import ast
import inspect
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_PANEL_PATH = os.path.join(_ROOT, "desktop", "diagnostics_panel.py")

import desktop.diagnostics_panel as panel  # noqa: E402

#: 纯展示红线：这些调用一个都不许出现。
_FORBIDDEN_CALLS = {
    "open", "unlink", "remove", "rmtree", "rmdir", "makedirs", "mkdir",
    "replace", "rename", "write", "write_text", "write_bytes", "touch",
    "truncate", "chmod",
}


def test_module_has_no_write_or_delete_calls():
    with open(_PANEL_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    tree = ast.parse(source)

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", ""))
            if name in _FORBIDDEN_CALLS:
                hits.append((node.lineno, name))
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "shutil", "诊断页不许导入 shutil"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "shutil", "诊断页不许导入 shutil"

    assert hits == [], f"诊断页出现写盘/删除调用：{hits}"


def _empty_data():
    return {
        "backups": {"directories": [], "total": 0, "stale": 0,
                    "items": [], "unreadable": []},
        "log": {"path": "", "candidates": [], "buffered": [],
                "notice": "本次进程尚未写入启动日志（不代表没有落点）"},
        "errors": {"items": [], "unwired": []},
    }


def test_every_unavailable_thing_gets_its_own_explicit_row():
    data = _empty_data()
    data["backups"]["unreadable"] = [
        {"dir": "D:/locked", "error": "Access denied"}]
    data["errors"]["unwired"] = [{
        "name": "bookmarks",
        "source": "mbdsdr_ai/bookmark_manager.py::BookmarkManager.last_error",
    }]

    pages = panel.build_pages(data)

    assert any("没有找到带理由文件的损坏备份" in r["title"]
               for r in pages["backups"]), "空清单没有说明「为什么是空」"
    assert any("查不到：未检查 D:/locked" in r["title"]
               and "Access denied" in r["text"]
               for r in pages["backups"]), "不可读目录被静默跳过"
    assert any("查不到本次启动日志的落点" in r["title"]
               for r in pages["log"]), "未写入日志没有显式说明"
    assert any("查不到：bookmarks" in r["title"]
               and "不等于它没有错误" in r["text"]
               for r in pages["errors"]), "unwired 被留成空白"


def test_reason_text_is_preserved_in_full():
    long_reason = "Exception: " + ("abcdefghij" * 500)   # 5011 字符
    data = _empty_data()
    data["backups"]["items"] = [{
        "backup": "bookmarks.json.corrupt-20261112-220000",
        "reason_file": "bookmarks.json.corrupt-20261112-220000.reason.txt",
        "reason": long_reason,
    }]

    rows = panel.build_pages(data)["backups"]
    hit = [r for r in rows
           if r["title"] == "bookmarks.json.corrupt-20261112-220000"]
    assert hit, "有备份行却没有对应展示行"
    assert hit[0]["text"] == long_reason
    assert len(hit[0]["text"]) == len(long_reason), "理由被截断了"

    with open(_PANEL_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    assert "QScrollArea" in source, "页面没有滚动区"
    assert "QTextEdit" in source and "setPlainText" in source, "长文本没有滚动控件"


def test_open_help_hook_is_optional():
    pytest.importorskip("PySide6")
    cls = panel._build_dialog_class()
    params = inspect.signature(cls.__init__).parameters
    assert "open_help" in params
    assert params["open_help"].default is None, "挂点必须可选，未接线时不显示按钮"
