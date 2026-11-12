# SPDX-License-Identifier: MIT
"""只读诊断页（W106）三条硬约束。

1. **纯展示**：AST 扫模块源码，禁止任何写盘 / 删除调用与 shutil 导入——
   不是「这次没加清理按钮」，而是以后想加也会被拦红；
2. **长文本不截断**：整段异常理由原样进展示行，由 QTextEdit + QScrollArea
   滚动，不按字符切；
3. **查不到必须显式**：unwired 实例、未写入日志、不可读目录各占一行。

Qt 相关用例在函数内 ``importorskip``，模块顶层不依赖 PySide6。
"""

import ast
import inspect
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_PANEL_PATH = os.path.join(_ROOT, "desktop", "diagnostics_panel.py")

import desktop.diagnostics_panel as panel  # noqa: E402

#: 纯展示红线：这些调用一个都不许出现。
#: 除写盘/删除外，还禁止「打开目录/启动外部程序」这一类——把
#: 「复制路径」改成「打开目录」时，第一条命中就是这里。
_FORBIDDEN_CALLS = {
    "open", "unlink", "remove", "rmtree", "rmdir", "makedirs", "mkdir",
    "replace", "rename", "write", "write_text", "write_bytes", "touch",
    "truncate", "chmod",
    "startfile", "system", "popen", "execv", "execvp", "spawn",
    "startDetached", "openUrl", "open_url",
}

#: 会启动外部程序的模块 / Qt 类：导入即命中，不等到调用。
_FORBIDDEN_MODULES = {"shutil", "subprocess", "webbrowser"}
_FORBIDDEN_IMPORTS = {"QDesktopServices", "QProcess"}


def _scan_forbidden(source: str):
    """返回源码里所有越界调用/导入 ``[(行号, 名字), ...]``。"""
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
                if alias.name in _FORBIDDEN_MODULES:
                    hits.append((node.lineno, f"import {alias.name}"))
        if isinstance(node, ast.ImportFrom):
            if node.module in _FORBIDDEN_MODULES:
                hits.append((node.lineno, f"from {node.module}"))
            for alias in node.names:
                if alias.name in _FORBIDDEN_IMPORTS:
                    hits.append((node.lineno, f"import {alias.name}"))
    return hits


def test_module_has_no_write_or_delete_calls():
    with open(_PANEL_PATH, "r", encoding="utf-8") as f:
        source = f.read()
    hits = _scan_forbidden(source)
    assert hits == [], f"诊断页出现写盘/删除/启动外部程序调用：{hits}"


@pytest.mark.parametrize("source,needle", [
    ("from PySide6.QtGui import QDesktopServices\n"
     "QDesktopServices.openUrl(u)\n", "QDesktopServices"),
    ("import os\nos.startfile(d)\n", "startfile"),
    ("import subprocess\nsubprocess.Popen(['explorer', d])\n", "subprocess"),
    ("import webbrowser\nwebbrowser.open(u)\n", "webbrowser"),
])
def test_gate_blocks_open_directory_variant(source, needle):
    """反证：把「复制路径」改成「打开目录」的任一写法都会被闸门拦下。"""
    hits = _scan_forbidden(source)
    assert any(needle in name for _line, name in hits), \
        f"闸门没挡住打开目录写法 {needle}: {hits}"


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


def test_reason_text_renders_full_inside_scroll_area(monkeypatch):
    """行为验收：长理由整段进只读文本框，且文本框落在可滚动区域里。"""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QScrollArea, QTextEdit

    _app = QApplication.instance() or QApplication([])
    long_reason = "Exception: " + ("abcdefghij" * 500)
    data = _empty_data()
    data["backups"]["items"] = [{
        "backup": "bookmarks.json.corrupt-20261112-220000",
        "reason_file": "bookmarks.json.corrupt-20261112-220000.reason.txt",
        "reason": long_reason,
    }]
    monkeypatch.setattr(panel, "collect_diagnostics", lambda: data)

    dlg = panel._build_dialog_class()()
    try:
        boxes = [b for b in dlg.findChildren(QTextEdit)
                 if b.toPlainText() == long_reason]
        assert len(boxes) == 1, "长理由没有整段渲染到文本框（被截断或没渲染）"
        assert boxes[0].isReadOnly(), "理由必须只读展示"
        node = boxes[0].parent()
        while node is not None and not isinstance(node, QScrollArea):
            node = node.parent()
        assert isinstance(node, QScrollArea), "长文本没有落在可滚动区域"
    finally:
        dlg.close()


def test_open_help_hook_is_optional():
    pytest.importorskip("PySide6")
    cls = panel._build_dialog_class()
    params = inspect.signature(cls.__init__).parameters
    assert "open_help" in params
    assert params["open_help"].default is None, "挂点必须可选，未接线时不显示按钮"


def test_backup_rows_carry_path_and_manual_delete_note():
    """备份行必须带可复制的路径；页面必须写明「可以手动删、本页不删」。"""
    data = _empty_data()
    data["backups"]["items"] = [{
        "backup": "C:/x/config.json.corrupt-20261112-220000",
        "reason_file": "C:/x/config.json.corrupt-20261112-220000.reason.txt",
        "reason": "配置文件损坏或不可读（JSONDecodeError）",
    }]

    rows = panel.build_pages(data)["backups"]
    hit = [r for r in rows if r["title"].endswith("config.json.corrupt-20261112-220000")]
    assert hit, "备份行缺失"
    assert hit[0]["path"] == "C:/x/config.json.corrupt-20261112-220000"
    assert any("可以手动删除" in r["title"] for r in rows), \
        "没有告诉用户备份可以自己清理"
    assert any("不启动外部程序" in r["text"] for r in rows), \
        "没有写清本页不打开文件管理器"


def test_copy_path_button_copies_without_opening_directory(monkeypatch):
    """点「复制路径」只进剪贴板；不启动文件管理器、不离开只读边界。"""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QPushButton

    app = QApplication.instance() or QApplication([])
    data = _empty_data()
    data["backups"]["items"] = [{
        "backup": "C:/x/config.json.corrupt-20261112-220000",
        "reason_file": "C:/x/config.json.corrupt-20261112-220000.reason.txt",
        "reason": "配置文件损坏或不可读（JSONDecodeError）",
    }]
    monkeypatch.setattr(panel, "collect_diagnostics", lambda: data)

    dlg = panel._build_dialog_class()()
    buttons = [b for b in dlg.findChildren(QPushButton)
               if b.text() == "复制路径"]
    assert len(buttons) == 1, "备份行没有「复制路径」按钮"
    buttons[0].click()
    assert app.clipboard().text() == "C:/x/config.json.corrupt-20261112-220000"
    dlg.close()
