# SPDX-License-Identifier: MIT
"""后端启动优化的行为钉扎：性能改动不许改语义。

覆盖 2026-11-11「Agent 启动链路」优化的四处：
1. 构造期不再同步 GET /v1/models（模型列表按需查询）；
2. 工具名写判定 / 硬门判定加缓存后，逐名与参考实现一致；
3. git 可用性探测进程内只 spawn 一次；
4. 历史 JSON 快照缓存：签名失效要重读，实例之间不共享可变状态。

所有测试只用 tmp_path / monkeypatch，绝不写用户真实配置目录。
"""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mbdsdr_ai import AgentConfig, MBDSDRAgent  # noqa: E402
from mbdsdr_ai import history_snapshot as hs  # noqa: E402
from mbdsdr_ai.file_tracker import ChangeType, FileChangeTracker  # noqa: E402
from mbdsdr_ai.self_learning import SelfLearningEngine  # noqa: E402
from mbdsdr_ai.tool_registry import (  # noqa: E402
    MODEL_HARD_GATE_PATTERNS, WRITE_TOOL_NAME_PATTERNS,
    _auto_write, _model_hard_gate,
)


@pytest.fixture(scope="module")
def agent(isolated_profile):
    return MBDSDRAgent(AgentConfig(api_key="bench", model="bench-model"))


# ── 1. 模型列表按需查询 ────────────────────────────────────────────────

def test_construct_does_not_fetch_models(monkeypatch, isolated_profile):
    """构造 Agent 不许同步联网；用户开口要模型列表时才查。"""
    from mbdsdr_ai.model_manager import ModelManager

    calls = []
    original = ModelManager.fetch_models

    def spy(self, force_refresh=False):
        calls.append(force_refresh)
        return list(self.available_models)

    monkeypatch.setattr(ModelManager, "fetch_models", spy)
    a = MBDSDRAgent(AgentConfig(api_key="bench", model="bench-model"))
    assert calls == [], "构造期发生了模型列表网络查询"

    text = a.run_command("/models")
    assert calls, "显式 /models 必须走查询"
    assert "可用模型" in text
    monkeypatch.setattr(ModelManager, "fetch_models", original)


def test_list_models_tool_still_queries(agent, monkeypatch):
    """list_models 工具（显式刷新）仍能拿到真实查询路径。"""
    from mbdsdr_ai.model_manager import ModelManager

    seen = []
    original = ModelManager.fetch_models

    def spy(self, force_refresh=False):
        seen.append(force_refresh)
        return list(self.available_models)

    monkeypatch.setattr(ModelManager, "fetch_models", spy)
    try:
        handler = agent.tool_registry.tools["list_models"]["handler"]
        result = handler({"refresh": True})
        assert seen and seen[-1] is True
        assert result.success
    finally:
        monkeypatch.setattr(ModelManager, "fetch_models", original)


# ── 2. 工具名判定缓存：逐名与参考实现一致 ──────────────────────────────

def _reference(name: str, patterns) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def test_write_classification_unchanged(agent):
    names = sorted(agent.tool_registry.tools)
    assert len(names) > 400
    for name in names:
        expected = _reference(name, WRITE_TOOL_NAME_PATTERNS)
        assert _auto_write(name, {"properties": {}}) is expected, name
        assert agent.tool_registry.is_write_tool(name) is expected, name


def test_hard_gate_classification_unchanged(agent):
    names = sorted(agent.tool_registry.tools)
    for name in names:
        assert _model_hard_gate(name) is _reference(
            name, MODEL_HARD_GATE_PATTERNS), name
    # 发射类必须仍被拦（安全事实源不许被动摇）
    assert _model_hard_gate("radio_ptt") is True
    assert _model_hard_gate("sdr_set_frequency") is False


# ── 3. git 探测只 spawn 一次 ───────────────────────────────────────────

def test_git_probe_is_cached(monkeypatch, tmp_path):
    from mbdsdr_ai import code_editor as ce

    ce._git_version_available.cache_clear()
    calls = []
    real_run = subprocess.run

    def spy(cmd, *a, **kw):
        calls.append(list(cmd))
        return real_run(cmd, *a, **kw)

    monkeypatch.setattr(ce.subprocess, "run", spy)
    ce.CodeEditor(project_root=str(tmp_path))
    ce.CodeEditor(project_root=str(tmp_path))
    assert len(calls) == 1, f"git 探测重复 spawn: {calls}"


# ── 4. 历史 JSON 快照缓存 ──────────────────────────────────────────────

def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _change_payload(cid: str) -> dict:
    return {
        "change_id": cid,
        "file_path": "bench/module.py",
        "change_type": "modify",
        "hash_before": "0" * 64,
        "hash_after": "1" * 64,
        "reason": "test",
        "actor": "test",
        "timestamp": 1730000000,
        "metadata": {},
        "reverted": False,
        "reverted_to": "",
    }


def test_snapshot_matches_direct_read(tmp_path):
    hs.clear_cache()
    day = tmp_path / "2026-11-10"
    _write_json(day / "chg_00001.json", _change_payload("chg_00001"))
    _write_json(day / "chg_00002.json", _change_payload("chg_00002"))
    (day / "broken.json").write_text("{ not json", encoding="utf-8")
    (day / "notdict.json").write_text("[1, 2]", encoding="utf-8")
    paths = [str(p) for p in day.iterdir() if p.suffix == ".json"]

    rows = hs.load_json_files(paths)
    got = {data["change_id"]: data for _path, data in rows}
    assert set(got) == {"chg_00001", "chg_00002"}, "坏/非 dict 文件应被静默跳过"
    for cid, data in got.items():
        assert data == _change_payload(cid)


def test_snapshot_invalidates_on_add_edit_delete(tmp_path):
    hs.clear_cache()
    day = tmp_path / "2026-11-10"
    a = day / "chg_00001.json"
    _write_json(a, _change_payload("chg_00001"))
    paths = lambda: [str(p) for p in day.iterdir() if p.suffix == ".json"]

    assert [d["change_id"] for _p, d in hs.load_json_files(paths())] == ["chg_00001"]

    b = day / "chg_00002.json"
    _write_json(b, _change_payload("chg_00002"))
    assert {d["change_id"] for _p, d in hs.load_json_files(paths())} == {
        "chg_00001", "chg_00002"}

    edited = _change_payload("chg_00001")
    edited["reason"] = "edited"
    _write_json(a, edited)
    os.utime(a, ns=(a.stat().st_atime_ns, a.stat().st_mtime_ns + 1_000_000))
    rows = {d["change_id"]: d for _p, d in hs.load_json_files(paths())}
    assert rows["chg_00001"]["reason"] == "edited", "内容改写必须让签名失效"

    b.unlink()
    assert {d["change_id"] for _p, d in hs.load_json_files(paths())} == {"chg_00001"}


def test_file_tracker_instances_are_independent(tmp_path):
    hs.clear_cache()
    day = tmp_path / "2026-11-10"
    _write_json(day / "chg_00001.json", _change_payload("chg_00001"))

    one = FileChangeTracker(storage_dir=str(tmp_path))
    two = FileChangeTracker(storage_dir=str(tmp_path))
    assert set(one.changes) == set(two.changes) == {"chg_00001"}

    one.track_change("bench/module.py", ChangeType.MODIFY,
                     content_before="a", content_after="b", reason="test")
    assert len(one.changes) == 2
    assert len(two.changes) == 1, "实例之间不许共享可变状态"

    three = FileChangeTracker(storage_dir=str(tmp_path))
    assert len(three.changes) == 2, "新记录落盘后新实例必须能看到"


def test_self_learning_snapshot_loads(tmp_path):
    hs.clear_cache()
    exp_dir = tmp_path / "experiences" / "2026-11-10"
    _write_json(exp_dir / "exp_00001.json", {
        "experience_id": "exp_00001",
        "experience_type": "tool_call",
        "question": "q", "score": 7, "feedback": "",
        "timestamp": 1730000000, "learned": False,
    })
    _write_json(tmp_path / "patterns" / "pat_001.json", {
        "pattern_id": "pat_001", "pattern_type": "synthetic",
        "description": "d", "trigger": "t", "action": "a",
        "confidence": 0.5, "frequency": 1, "avg_score": 7.0,
        "created_at": 1730000000, "applied": False,
    })
    a = SelfLearningEngine(storage_dir=str(tmp_path))
    b = SelfLearningEngine(storage_dir=str(tmp_path))
    assert set(a.experiences) == set(b.experiences) == {"exp_00001"}
    assert set(a.patterns) == set(b.patterns) == {"pat_001"}
