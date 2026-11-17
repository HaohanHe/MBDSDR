# SPDX-License-Identifier: MIT
"""IQ 清单工具面：把 HF 方向的「体检 / 切分」接成模型可达的只读工具。

钉四件事
--------
1. 两个工具真的注册了，且是**只读**（不在写工具名单里）；
2. 体检读的是磁盘上的 JSONL，坏行逐条带行号与理由；
3. 切分以 ``iq_path`` 为最小单位，同一文件不跨训练/验证；
4. 任何一步不成立都如实 ``ok=false``，不交付半套结果、不伪造结论。
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="module")
def agent():
    from mbdsdr_ai.agent import MBDSDRAgent
    from mbdsdr_ai.config import AgentConfig

    return MBDSDRAgent(AgentConfig())


def _call(agent, name, args):
    r = agent.tool_registry.call(name, args)
    try:
        d = json.loads(r.content)
    except (json.JSONDecodeError, TypeError):
        d = {"ok": False, "reason": str(r.content)}
    d["_tool_success"] = r.success
    return d


def _write(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return str(path)


def _row(sid, label, iq_path, rate=1_024_000.0, freq=100_000_000.0,
         license="CC-BY-4.0"):
    return {"sample_id": sid, "label": label, "sample_rate": rate,
            "center_freq": freq, "iq_path": iq_path, "license": license}


class TestRegistration:
    def test_tools_registered(self, agent):
        names = {t["name"] for t in agent.tool_registry.list_tools()}
        assert "iq_manifest_audit" in names
        assert "iq_split_plan" in names

    def test_tools_are_read_only(self, agent):
        for name in ("iq_manifest_audit", "iq_split_plan"):
            tool = agent.tool_registry.tools[name]
            assert not tool.get("write"), f"{name} 不该是写工具"


class TestAudit:
    def test_two_class_manifest_passes(self, agent, tmp_path):
        path = _write(tmp_path / "m.jsonl", [
            _row("a1", "FM", "fm/a1.iq"),
            _row("a2", "FM", "fm/a2.iq"),
            _row("b1", "USB", "ssb/b1.iq"),
            _row("b2", "USB", "ssb/b2.iq"),
        ])
        d = _call(agent, "iq_manifest_audit", {"path": path})
        assert d["ok"] is True
        assert d["counts"] == {"accepted": 4, "rejected": 0}
        assert d["audit"]["verdict"] == "structurally_ready"
        assert d["audit"]["class_counts"] == {"FM": 2, "USB": 2}
        assert "不代表样本足够" in d["text"]

    def test_bad_license_is_rejected_with_line(self, agent, tmp_path):
        path = _write(tmp_path / "m.jsonl", [
            _row("a1", "FM", "fm/a1.iq"),
            _row("a2", "FM", "fm/a2.iq", license="unknown"),
        ])
        d = _call(agent, "iq_manifest_audit", {"path": path})
        assert d["counts"]["rejected"] == 1
        assert d["rejected"][0]["line"] == 2
        assert "license" in " ".join(d["rejected"][0]["reasons"])

    def test_missing_file_is_not_a_traceback(self, agent, tmp_path):
        d = _call(agent, "iq_manifest_audit",
                  {"path": str(tmp_path / "nope.jsonl")})
        assert d["ok"] is False
        assert "清单文件不存在" in d["reason"]

    def test_check_files_catches_absent_iq(self, agent, tmp_path):
        real = tmp_path / "real.iq"
        real.write_bytes(b"\x00" * 8)
        path = _write(tmp_path / "m.jsonl", [
            _row("a1", "FM", str(real)),
            _row("a2", "FM", str(tmp_path / "missing.iq")),
        ])
        ok = _call(agent, "iq_manifest_audit", {"path": path,
                                                "check_files": True})
        assert ok["counts"]["rejected"] == 1
        assert "不存在" in " ".join(ok["rejected"][0]["reasons"])

    def test_single_class_is_not_ready(self, agent, tmp_path):
        path = _write(tmp_path / "m.jsonl", [
            _row("a1", "FM", "fm/a1.iq"),
            _row("a2", "FM", "fm/a2.iq"),
        ])
        d = _call(agent, "iq_manifest_audit", {"path": path})
        assert d["audit"]["verdict"] == "not_ready"
        assert any("单类" in p or "类别" in p for p in d["audit"]["problems"])


class TestSplit:
    def test_same_file_never_crosses_train_val(self, agent, tmp_path):
        path = _write(tmp_path / "m.jsonl", [
            _row("f1", "FM", "fm/one.iq"),
            _row("f2", "FM", "fm/one.iq"),
            _row("f3", "FM", "fm/two.iq"),
            _row("u1", "USB", "usb/one.iq"),
            _row("u2", "USB", "usb/two.iq"),
        ])
        d = _call(agent, "iq_split_plan", {"path": path, "val_ratio": 0.5,
                                           "seed": 1})
        assert d["ok"] is True
        train, val = set(d["train"]), set(d["val"])
        assert not (train & val)
        # 两条样本同属 fm/one.iq，必须整体落在同一侧
        assert ({"f1", "f2"} <= train) or ({"f1", "f2"} <= val)
        assert "按 iq_path 分组" in d["text"]
        assert "同一采集会话" in d["text"], "caveat 不能被吞掉"

    def test_deterministic_for_same_seed(self, agent, tmp_path):
        path = _write(tmp_path / "m.jsonl", [
            _row("a1", "FM", "fm/a.iq"), _row("a2", "FM", "fm/b.iq"),
            _row("a3", "FM", "fm/c.iq"), _row("a4", "FM", "fm/d.iq"),
            _row("b1", "USB", "usb/a.iq"), _row("b2", "USB", "usb/b.iq"),
        ])
        first = _call(agent, "iq_split_plan", {"path": path, "seed": 7})
        again = _call(agent, "iq_split_plan", {"path": path, "seed": 7})
        assert first["train"] == again["train"]
        assert first["val"] == again["val"]

    def test_single_file_class_refuses_whole_plan(self, agent, tmp_path):
        path = _write(tmp_path / "m.jsonl", [
            _row("a1", "FM", "fm/a.iq"),
            _row("a2", "FM", "fm/b.iq"),
            _row("b1", "USB", "usb/only.iq"),
        ])
        d = _call(agent, "iq_split_plan", {"path": path})
        assert d["ok"] is False
        assert d["train"] == [] and d["val"] == []
        assert any("切不出验证集" in p for p in d["problems"])
        assert "未生成" in d["text"]

    def test_rejected_rows_are_flagged_in_text(self, agent, tmp_path):
        path = _write(tmp_path / "m.jsonl", [
            _row("a1", "FM", "fm/a.iq"), _row("a2", "FM", "fm/b.iq"),
            _row("b1", "USB", "usb/a.iq"), _row("b2", "USB", "usb/b.iq"),
            _row("bad", "USB", "usb/bad.iq", license=""),
        ])
        d = _call(agent, "iq_split_plan", {"path": path})
        assert d["counts"]["rejected"] == 1
        assert "1 行未通过清单校验" in d["text"]

    def test_bad_ratio_is_refused(self, agent, tmp_path):
        path = _write(tmp_path / "m.jsonl", [_row("a1", "FM", "fm/a.iq")])
        d = _call(agent, "iq_split_plan", {"path": path, "val_ratio": 1.5})
        assert d["ok"] is False
        assert "val_ratio" in d["reason"]

    def test_missing_path_param_is_reported(self, agent):
        d = _call(agent, "iq_manifest_audit", {})
        assert d["_tool_success"] is False
        assert "path" in json.dumps(d, ensure_ascii=False)
