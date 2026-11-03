# SPDX-License-Identifier: MIT
"""
tests/test_workflow_engine_no_presets.py
========================================
A3 回归锁：工作流引擎**不含任何内置固定步骤序列**，也不含任何
「文本命中关键词就自动跑固定流程」的触发器。

钉住的事实：
  1. 全新 WorkflowEngine 的工作流表为空（无 preset 注册路径）；
  2. Workflow / WorkflowEngine 均无 trigger 相关 API
     （match_trigger / trigger_phrases / _load_presets / _preset_*）；
  3. Workflow.to_dict()/from_dict 不再携带 trigger_phrases 键，
     旧 JSON 里的 trigger_phrases 键被静默忽略（不复活）；
  4. 引擎仍服务用户/AI 自定义 JSON 工作流（最小骨架保留：
     注册/加载/执行/统计），执行未知名称返回诚实错误；
  5. Agent 工具面不再有 workflow_trigger_match。

运行:
    python -m pytest tests/test_workflow_engine_no_presets.py -v
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.workflow_engine import Workflow, WorkflowEngine  # noqa: E402


class TestNoBuiltinFlows:
    def test_fresh_engine_has_no_workflows(self, tmp_path):
        we = WorkflowEngine(workflows_dir=str(tmp_path))
        assert we.list_workflows() == []
        assert we.workflows == {}
        assert we.get_stats()["total_workflows"] == 0

    def test_no_preset_api_surface(self, tmp_path):
        we = WorkflowEngine(workflows_dir=str(tmp_path))
        for attr in ("_load_presets", "match_trigger",
                     "_preset_interference_localization", "_preset_noaa_apt",
                     "_preset_sstv", "_preset_aprs_monitor",
                     "_preset_ai_sweep_find_stations", "_preset_baseband_record"):
            assert not hasattr(we, attr), f"残留 API: {attr}"

    def test_workflow_dataclass_has_no_trigger_field(self):
        wf = Workflow(name="demo")
        assert not hasattr(wf, "trigger_phrases")
        assert not hasattr(wf, "match_trigger")
        assert "trigger_phrases" not in wf.to_dict()
        # 默认来源不再是 preset
        assert wf.source == "user"

    def test_legacy_trigger_key_ignored_on_load(self, tmp_path):
        """旧 workflows_dir JSON 残留 trigger_phrases 键：加载后不进对象。"""
        p = tmp_path / "legacy.json"
        p.write_text(json.dumps({
            "name": "legacy_flow",
            "trigger_phrases": ["找干扰源"],
            "steps": [],
        }, ensure_ascii=False), encoding="utf-8")
        we = WorkflowEngine(workflows_dir=str(tmp_path))
        wf = we.workflows.get("legacy_flow")
        assert wf is not None
        assert not hasattr(wf, "trigger_phrases")
        assert wf.source == "user"


class TestEngineStillServesUserFlows:
    def test_user_json_workflow_loads_and_runs(self, tmp_path):
        p = tmp_path / "user_flow.json"
        p.write_text(json.dumps({
            "name": "user_flow",
            "description": "用户自定义两步",
            "steps": [
                {"step_id": 1, "tool_name": "tool_a", "params": {"x": 1}},
                {"step_id": 2, "tool_name": "tool_b",
                 "params": {"y": "{{step_1.v}}"}},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        we = WorkflowEngine(workflows_dir=str(tmp_path))
        calls = []

        class _R:
            def __init__(self, v):
                self.v = v
                self.content = json.dumps({"v": self.v})

        def _exec(name, params):
            calls.append((name, params))
            return _R(41 if name == "tool_a" else params.get("y"))

        we.set_tool_executor(_exec)
        res = we.execute("user_flow")
        assert res.success and res.completed_steps == 2
        # 参数模板回填真实生效（引擎最小骨架仍在工作）
        assert calls[1][1]["y"] == 41

    def test_unknown_name_returns_honest_error(self, tmp_path):
        we = WorkflowEngine(workflows_dir=str(tmp_path))
        res = we.execute("__definitely_missing__")
        assert not res.success
        assert "不存在" in res.error


class TestAgentToolSurface:
    def test_workflow_trigger_match_tool_removed(self):
        """Agent 注册表里不再有「关键词→固定流程」的触发工具。"""
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig
        ag = MBDSDRAgent(AgentConfig())
        names = set(ag.tool_registry.get_tool_names())
        assert "workflow_trigger_match" not in names
        # list/execute 保留（服务用户自定义工作流），但描述不再诱导固定流程
        assert "workflow_list" in names
        assert "workflow_execute" in names
        descs = {t["name"]: t.get("description", "")
                 for t in ag.tool_registry.list_tools()}
        assert "触发短语" not in descs["workflow_list"]
        assert "触发短语" not in descs.get("workflow_execute", "")
