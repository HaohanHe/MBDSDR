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

import mbdsdr_ai.conversation as conversation_mod  # noqa: E402
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


def test_missing_file_saves_without_backup(tmp_path):
    """A04：文件不存在是首次保存，不能被当成损坏去备份或阻写。"""
    store = ConversationStore(directory=str(tmp_path))

    path = store.save("conv_new", [{"role": "user", "content": "内容"}])

    assert os.path.exists(path)
    assert store.save_warnings == []
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_missing_file_race_is_not_corruption(tmp_path, monkeypatch):
    """A04：exists 判真后文件刚被删，FileNotFoundError 是竞态而非损坏。"""
    store = ConversationStore(directory=str(tmp_path))
    path = os.path.join(str(tmp_path), "conv_race.json")
    real_exists = os.path.exists
    monkeypatch.setattr(
        conversation_mod.os.path, "exists",
        lambda p: True if p == path else real_exists(p))

    store.save("conv_race", [{"role": "user", "content": "内容"}])

    assert store.save_warnings == [], "文件缺失不得被当成损坏去备份"
    assert os.path.isfile(path)
    assert list(tmp_path.glob("*.corrupt-*")) == []


def test_corrupt_first_read_writes_backup_and_reason(tmp_path):
    """A04：首次读到损坏文件就要落备份 + 理由，原件从列表路径移走。"""
    store = ConversationStore(directory=str(tmp_path))
    path = tmp_path / "conv_bad.json"
    path.write_text("{ not json", encoding="utf-8")

    assert store.load_meta("conv_bad") is None

    backups = list(tmp_path.glob("conv_bad.json.corrupt-*"))
    backups = [p for p in backups if not p.name.endswith(".reason.txt")]
    assert len(backups) == 1, "首次读取必须产出且只产出一份备份"
    assert backups[0].read_text(encoding="utf-8") == "{ not json"
    assert not path.exists(), "原件必须从列表路径移走，避免后续整文件覆写"
    reasons = list(tmp_path.glob("conv_bad.json.corrupt-*.reason.txt"))
    assert len(reasons) == 1, "备份必须留下可查的理由"
    assert "JSONDecodeError" in reasons[0].read_text(encoding="utf-8")


def test_backup_failure_blocks_save(tmp_path, monkeypatch):
    """A04 反证：备份失败时不得写回，原件字节必须原样保留。"""
    store = ConversationStore(directory=str(tmp_path))
    path = tmp_path / "conv_block.json"
    path.write_text("{ broken", encoding="utf-8")
    monkeypatch.setattr(conversation_mod, "_backup_corrupt", lambda _p: None)

    try:
        store.save("conv_block", [{"role": "user", "content": "新内容"}])
    except OSError:
        pass
    else:  # pragma: no cover - 反证失败时走到这里
        raise AssertionError("备份失败时保存必须报错，不能继续覆写")

    assert path.read_text(encoding="utf-8") == "{ broken", "原件不得被覆盖"
    assert list(tmp_path.glob("conv_block.json.corrupt-*")) == []
    assert store.save_warnings and store.save_warnings[0]["backup"] == ""


def test_oserror_read_blocks_save_without_backup(tmp_path):
    """A04：读不了（OSError）≠ 损坏，只阻写、不改名、不备份。"""
    store = ConversationStore(directory=str(tmp_path))
    path = tmp_path / "conv_os.json"
    path.mkdir()  # 同名目录：open(...,"r") 抛 OSError

    try:
        store.save("conv_os", [{"role": "user", "content": "新内容"}])
    except OSError:
        pass
    else:  # pragma: no cover - 反证失败时走到这里
        raise AssertionError("读取抛 OSError 时必须拒绝写回")

    assert path.is_dir(), "OSError 场景不得改名/删除原件"
    assert list(tmp_path.glob("conv_os.json.corrupt-*")) == []


def test_same_corrupt_content_keeps_single_copy(tmp_path):
    """A04：同一份损坏内容只留一份副本，不堆 -1/-2/... 副本。"""
    store = ConversationStore(directory=str(tmp_path))
    path = tmp_path / "conv_dup.json"
    path.write_text("{ broken", encoding="utf-8")
    existing = tmp_path / "conv_dup.json.corrupt-20200101-000000"
    existing.write_text("{ broken", encoding="utf-8")

    store.save("conv_dup", [{"role": "user", "content": "新内容"}])

    backups = [p for p in tmp_path.glob("conv_dup.json.corrupt-*")
               if not p.name.endswith(".reason.txt")]
    assert backups == [existing], "同内容不得新增第二份备份"
    assert existing.read_text(encoding="utf-8") == "{ broken"
