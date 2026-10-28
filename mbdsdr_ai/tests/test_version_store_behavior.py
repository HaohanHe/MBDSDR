# SPDX-License-Identifier: MIT
"""
version_store.py（轻量版本存储）行为测试
==========================================

纯逻辑 + tmp_path。重点钉死：

  - 同毫秒 + 同 message 的快照不得撞 ID 覆盖历史（旧实现静默丢版本）；
  - 对象库缺块时 get_all_files 必须报错，不得用空串冒充"文件为空"；
  - diff 指向不存在版本必须报 error，不得伪造 deleted 清单；
  - versions.json 损坏 = 整体不载入（不留"半截版本 + head 丢失"状态）；
  - rollback 找不到版本 / 没有父版本 = 诚实失败。

运行：python -m pytest mbdsdr_ai/tests/test_version_store_behavior.py -v
"""
from __future__ import annotations

import json
import os

import pytest

from mbdsdr_ai.version_store import VersionStore


def _mk(tmp_path) -> VersionStore:
    return VersionStore(store_path=str(tmp_path / "store"))


# ── 1. 快照/读取往返 + 父链 ────────────────────────────────────
def test_snapshot_get_round_trip_and_parent_chain(tmp_path):
    vs = _mk(tmp_path)
    v1 = vs.snapshot("初版", {"a.py": "1", "b.py": "x"}, author="ai")
    v2 = vs.snapshot("二版", {"a.py": "2", "b.py": "x"}, author="user")

    assert vs.get_file("a.py") == "2"
    assert vs.get_file("a.py", v1.id) == "1"
    assert vs.get_all_files(v1.id) == {"a.py": "1", "b.py": "x"}
    assert v2.parent_id == v1.id
    assert vs.get_version().id == v2.id

    hist = vs.history()
    assert [h["id"] for h in hist] == [v2.id, v1.id]
    assert hist[0]["author"] == "user"


def test_get_file_missing_is_honest_none(tmp_path):
    vs = _mk(tmp_path)
    v = vs.snapshot("s", {"a.py": "1"})
    assert vs.get_file("不存在.py") is None
    assert vs.get_file("a.py", "v_不存在") is None
    assert vs.get_file("a.py", v.id) == "1"


# ── 2. ID 冲突：同毫秒同 message 不得覆盖历史 ──────────────────
def test_snapshot_id_collision_keeps_both_versions(tmp_path, monkeypatch):
    vs = _mk(tmp_path)
    # 冻结时钟：两次快照落在同一毫秒且 message 相同
    monkeypatch.setattr("mbdsdr_ai.version_store.time.time", lambda: 1000.0)
    v1 = vs.snapshot("fix bug", {"a.py": "1"})
    v2 = vs.snapshot("fix bug", {"b.py": "2"})

    assert v1.id != v2.id, "撞 ID 会让后写覆盖前写、静默丢历史"
    assert vs.get_stats()["total_versions"] == 2
    assert vs.get_file("a.py", v1.id) == "1"
    assert vs.get_file("b.py", v2.id) == "2"
    assert vs.get_file("a.py") is None, "HEAD=v2，不应看到 v1 独有文件"


# ── 3. 对象缺失：拒绝空串冒充 ──────────────────────────────────
def test_missing_object_is_honest_error_not_empty_string(tmp_path):
    vs = _mk(tmp_path)
    v = vs.snapshot("s", {"x.txt": "hello", "y.txt": "world"})
    os.remove(os.path.join(vs.objects_path, v.files["y.txt"]))

    assert vs.get_file("y.txt") is None, "get_file 缺对象如实返回 None"
    with pytest.raises(ValueError, match="对象缺失"):
        vs.get_all_files()          # 不得返回 {'y.txt': ''} 冒充空文件
    # 回滚同样必须失败而不是把空内容写成新版本
    ok, msg = vs.rollback(v.id)
    assert ok is False
    assert "无法回滚" in msg
    assert vs.get_stats()["total_versions"] == 1, "失败回滚不得留下新版本"


# ── 4. diff：分类正确 + 不存在版本如实报错 ─────────────────────
def test_diff_classification(tmp_path):
    vs = _mk(tmp_path)
    v1 = vs.snapshot("v1", {"keep.txt": "k", "mod.txt": "旧", "gone.txt": "g"})
    v2 = vs.snapshot("v2", {"keep.txt": "k", "mod.txt": "新", "new.txt": "n"})

    d = vs.diff(v1.id, v2.id)
    assert d["added"] == ["new.txt"]
    assert d["deleted"] == ["gone.txt"]
    assert d["modified"] == ["mod.txt"]
    assert d["unchanged"] == ["keep.txt"]

    d2 = vs.diff(v2.id)          # 省略第二参 = 对比 HEAD（自身）
    assert d2["unchanged"] == ["keep.txt", "mod.txt", "new.txt"]


def test_diff_nonexistent_version_is_honest_error(tmp_path):
    vs = _mk(tmp_path)
    v = vs.snapshot("v1", {"x.txt": "1"})

    d1 = vs.diff("v_不存在", v.id)
    assert "error" in d1, "不存在的 v1 必须报 error"

    d2 = vs.diff(v.id, "v_也不存在")
    assert "error" in d2, "不存在的 v2 不得伪装成空集报 deleted"
    assert "deleted" not in d2


# ── 5. 回滚：成功建新版 / 失败如实 ─────────────────────────────
def test_rollback_creates_new_version_and_history_kept(tmp_path):
    vs = _mk(tmp_path)
    v1 = vs.snapshot("v1", {"a.py": "1"})
    v2 = vs.snapshot("v2", {"a.py": "2"})

    ok, msg = vs.rollback(v1.id)
    assert ok is True, msg
    assert vs.get_file("a.py") == "1"
    assert vs.get_stats()["total_versions"] == 3, "回滚是新版本，历史不丢"
    head = vs.get_version()
    assert head.metadata["rollback_from"] == v1.id
    assert head.parent_id == v2.id

    ok2, msg2 = vs.rollback("v_不存在")
    assert ok2 is False
    assert "不存在" in msg2


def test_rollback_to_parent_and_root_failure(tmp_path):
    vs = _mk(tmp_path)
    vs.snapshot("v1", {"a.py": "1"})
    vs.snapshot("v2", {"a.py": "2"})

    ok, msg = vs.rollback_to_parent()
    assert ok is True, msg
    assert vs.get_file("a.py") == "1"

    # 只有一个版本时没有父版本：诚实失败
    vs2 = _mk(tmp_path / "s2")
    vs2.snapshot("only", {"a.py": "1"})
    ok2, msg2 = vs2.rollback_to_parent()
    assert ok2 is False
    assert "没有父版本" in msg2


# ── 6. 持久化：重启可续 + 损坏整体不载入 ───────────────────────
def test_reload_restores_versions_and_head(tmp_path):
    vs = _mk(tmp_path)
    v1 = vs.snapshot("v1", {"a.py": "1"})
    vs.snapshot("v2", {"a.py": "2"})

    vs2 = _mk(tmp_path)
    assert vs2.get_stats()["total_versions"] == 2
    assert vs2.get_version().id != v1.id
    assert vs2.get_file("a.py") == "2"
    assert vs2.get_file("a.py", v1.id) == "1"


def test_corrupt_versions_json_loads_nothing_not_half(tmp_path):
    vs = _mk(tmp_path)
    vs.snapshot("v1", {"a.py": "1"})
    vs.snapshot("v2", {"a.py": "2"})

    # 半截状态陷阱：一条好记录 + 一条缺字段记录
    with open(vs.versions_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["versions"].append({"id": "v_bad"})  # 缺 timestamp/message 等
    with open(vs.versions_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    vs2 = _mk(tmp_path)
    assert vs2.get_stats()["total_versions"] == 0, \
        "加载失败必须整体为空，不得留半截版本 + head=None"
    assert vs2.get_version() is None
    # 空库上继续可用（诚实空态）
    v = vs2.snapshot("重建", {"a.py": "x"})
    assert vs2.get_file("a.py") == "x"
    assert v.parent_id is None


def test_stats_reports_head_and_objects(tmp_path):
    vs = _mk(tmp_path)
    v = vs.snapshot("v1", {"a.py": "1", "b.py": "2"})
    st = vs.get_stats()
    assert st["total_versions"] == 1
    assert st["head"] == v.id
    assert st["objects_count"] == 2
    assert st["store_path"].endswith("store")
