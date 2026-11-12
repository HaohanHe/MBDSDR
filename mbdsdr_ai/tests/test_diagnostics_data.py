# SPDX-License-Identifier: MIT
"""诊断数据层（W106）四条口径。

1. 备份清单带**理由全文**（页面不用自己再拼路径）；
2. 只认 ``*.reason.txt``：裸 ``*.corrupt-*`` 与站址 ``.bak`` 不计入——
   否则「一个坏文件」会在页面上出现两次；
3. 日志路径取「实际写成功过」的记录，``collect`` 本身不许造文件/目录；
4. 实例类对象的 ``last_error`` 经弱引用注册可见，置空即消失；
   取不到的对象明确列在 ``unwired``，不拿空值冒充「无错误」。

本文件不导入 PySide6，可在没有 QApplication 的进程里直接跑。
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

import mbdsdr_ai.corrupt_evidence as ce  # noqa: E402
import mbdsdr_ai.diagnostics as diag  # noqa: E402
from desktop import startup_paths as sp  # noqa: E402


def _pair(directory, name="bookmarks.json", reason="书签文件读取失败：测试"):
    backup = directory / f"{name}.corrupt-20261112-220000"
    backup.write_text("{}", encoding="utf-8")
    ce.write_reason(str(backup), reason, None)
    return backup


def test_backups_carry_full_reason(tmp_path, monkeypatch):
    backup = _pair(tmp_path)
    monkeypatch.setattr(diag, "_scan_directories", lambda: [str(tmp_path)])

    data = diag.collect_diagnostics()

    assert set(data) == {"backups", "log", "errors"}
    assert data["backups"]["total"] == 1
    item = data["backups"]["items"][0]
    assert item["backup"] == str(backup)
    assert item["reason"] == "书签文件读取失败：测试"


def test_bare_corrupt_and_bak_are_not_counted(tmp_path, monkeypatch):
    (tmp_path / "bookmarks.json.corrupt-20261112-220000").write_text("{}")
    (tmp_path / "config.json.bak").write_text("{}")
    monkeypatch.setattr(diag, "_scan_directories", lambda: [str(tmp_path)])

    data = diag.collect_diagnostics()

    assert data["backups"]["total"] == 0, "裸备份 / .bak 混进了「有证据的损坏」"


def test_log_path_comes_from_recorder_without_side_effects(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "mbdsdr_startup.log"
    monkeypatch.setenv("MBDSDR_STARTUP_LOG", str(log))
    monkeypatch.setattr(sp, "_LAST_LOG_PATH", "")
    monkeypatch.setattr(diag, "_scan_directories", lambda: [str(tmp_path)])

    assert diag.collect_diagnostics()["log"]["path"] == ""
    assert sp.append_startup_log("配置清点：0 个") == str(log)

    before = sorted(str(p) for p in tmp_path.rglob("*"))
    data = diag.collect_diagnostics()
    after = sorted(str(p) for p in tmp_path.rglob("*"))

    assert data["log"]["path"] == str(log)
    assert data["log"]["notice"] == ""
    assert after == before, "collect 自己造了文件/目录"


class _Store:
    def __init__(self, error):
        self.last_error = error


def test_registered_error_appears_then_drops():
    store = _Store("持久化错误：磁盘已满")
    diag.register_store("memory", store)

    items = diag._collect_errors()["items"]
    hit = [i for i in items if i["name"] == "memory"]
    assert hit and "磁盘已满" in hit[-1]["error"]

    store.last_error = None
    items = diag._collect_errors()["items"]
    assert not [i for i in items if i["name"] == "memory"], "错误置空后仍在页面上"


def test_unwired_objects_are_named_not_faked():
    unwired = {u["name"] for u in diag._collect_errors()["unwired"]}
    assert {"desktop_settings", "layout_store", "bookmarks",
            "frequency_manager", "version_store"} <= unwired
