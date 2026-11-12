# SPDX-License-Identifier: MIT
"""A03：自学习经验/模式加载降级必须可查（第一批）。

契约：坏文件记入 load_errors（kind + path + 原因），好文件照常加载；
空目录（首次运行）不产生降级记录；get_stats() 暴露 load_error_count。

反证：把 _load_experiences / _load_patterns 的 except 改回
`except Exception: pass` 后本文件对应断言变红。
"""
import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.self_learning import SelfLearningEngine  # noqa: E402


def test_corrupt_files_are_visible(tmp_path):
    """经验/模式坏文件各记一条 load_errors，好文件照常加载。"""
    exp_dir = tmp_path / "experiences" / "2026-01-01"
    exp_dir.mkdir(parents=True)
    (exp_dir / "bad.json").write_text("{ broken", encoding="utf-8")
    (exp_dir / "exp_1_7.json").write_text(json.dumps({
        "experience_id": "exp_1_7",
        "experience_type": "tool_call",
        "question": "好经验",
        "score": 9,
    }), encoding="utf-8")
    pattern_dir = tmp_path / "patterns"
    pattern_dir.mkdir()
    (pattern_dir / "bad_pattern.json").write_text("{ broken", encoding="utf-8")

    engine = SelfLearningEngine(storage_dir=str(tmp_path))

    kinds = sorted(e["kind"] for e in engine.load_errors)
    assert kinds == ["experience", "pattern"], "两处坏文件都要留下降级记录"
    assert "exp_1_7" in engine.experiences, "坏文件不能阻断好文件的加载"
    assert engine.get_stats()["load_error_count"] == 2


def test_empty_storage_is_not_degradation(tmp_path):
    """空目录 = 首次运行，不得记降级。"""
    engine = SelfLearningEngine(storage_dir=str(tmp_path))
    assert engine.load_errors == []
    assert engine.get_stats()["load_error_count"] == 0
