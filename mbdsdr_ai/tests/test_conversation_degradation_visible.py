# SPDX-License-Identifier: MIT
"""A03：ConversationStore 读取降级必须可查（第一批）。

契约：
- 文件不存在 → 返回空，不产生降级记录（首运行/本来就没有）；
- 文件存在但读不出来 → 仍返回空，但 load_errors 必须点名 path 与异常类型；
- 保存路径遇到损坏原件 → 必须先备份原件再写新内容，不得静默覆写。

反证：把 load_meta 的 except 改回 `except Exception: return None`，
或把 save 的损坏分支改回 `except Exception: pass`，对应用例会变红。
"""
import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.conversation import ConversationStore  # noqa: E402


def test_load_meta_corrupt_is_visible(tmp_path):
    """坏会话文件 → 列表跳过它，但 load_errors 必须点名原因。"""
    store = ConversationStore(directory=str(tmp_path))
    (tmp_path / "conv_bad.json").write_text("{ not json", encoding="utf-8")

    assert store.list_conversations() == []
    assert len(store.load_errors) == 1, "坏文件必须留下降级记录，不能静默消失"
    err = store.load_errors[0]
    assert err["path"].endswith("conv_bad.json")
    assert "JSONDecodeError" in err["error"]


def test_missing_file_is_not_degradation(tmp_path):
    """文件不存在是「本来就允许为空」，不得记成降级。"""
    store = ConversationStore(directory=str(tmp_path))
    assert store.list_conversations() == []
    assert store.load_errors == []


def test_save_quarantines_corrupt_original(tmp_path):
    """保存时读到损坏原件：先备份再写，创建时间重置这件事有据可查。"""
    store = ConversationStore(directory=str(tmp_path))
    path = store.save("conv_q", [{"role": "user", "content": "原始内容"}])
    original = json.loads(open(path, encoding="utf-8").read())
    (tmp_path / "conv_q.json").write_text("{ broken", encoding="utf-8")

    store.save("conv_q", [{"role": "user", "content": "新内容"}])

    assert len(store.save_warnings) == 1, "损坏原件必须先备份，不能静默覆写"
    backup = store.save_warnings[0]["backup"]
    assert backup and os.path.exists(backup), "备份文件必须真实落盘"
    assert open(backup, encoding="utf-8").read() == "{ broken", "备份必须是损坏原件"
    assert os.path.exists(path)
    rewritten = json.loads(open(path, encoding="utf-8").read())
    assert rewritten["messages"][0]["content"] == "新内容"
    assert rewritten["created_at"] >= original["created_at"]
