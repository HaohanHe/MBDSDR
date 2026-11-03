# SPDX-License-Identifier: MIT
"""
tests/test_competition_wording_audit.py
========================================
赛事口径残留审计的回归锁（「我们是专业工具，不是比赛工具」）：

  1. 面向 LLM 的工具描述（ToolRegistry）与系统提示词中不得出现
     赛项/比赛/答辩/挑战赛/hackathon 等赛事语境词；
  2. 定轨目标表已皮进配置（desktop/doppler_targets.json）：
     代码不再硬编码任何具体目标；special 标记驱动 LRO/自定义分支；
     缺文件/坏 JSON 诚实降级为仅「自定义 TLE」；
  3. 中心频率默认值来自目标配置（LRO 2271 MHz 进 JSON，不进代码）；
  4. acquisition / verify_chain / sat_profile 的文案已产品语汇化
     （「验收脊柱」「材料口径」「哈工赛项」不再出现）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_competition_wording_audit.py -v
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from doppler_panel import DopplerPanel, _load_doppler_targets  # noqa: E402

# 赛事语境词表（用户/模型可见文案不得出现；「评分」类产品质量评分不在此列）
_COMPETITION_WORDS = ("赛项", "比赛", "答辩", "挑战赛", "hackathon", "Hackathon")


# ---------------------------------------------------------------------------
# 目标表配置化
# ---------------------------------------------------------------------------
class TestDopplerTargetsConfig:
    def test_loader_reads_real_config(self):
        targets = _load_doppler_targets()
        assert len(targets) == 3
        assert targets[0]["special"] == "lro"
        assert targets[0]["f0_hz"] == 2271000000.0
        assert targets[-1]["special"] == "custom"
        labels = [t["label"] for t in targets]
        assert any("LRO" in l for l in labels)  # 配置里带着，代码里没有

    def test_missing_file_falls_back_to_custom_only(self, tmp_path, monkeypatch):
        import doppler_panel
        fake = tmp_path / "doppler_panel_copy.py"
        fake.write_text("", encoding="utf-8")
        monkeypatch.setattr(doppler_panel, "__file__", str(fake))
        targets = doppler_panel._load_doppler_targets()
        assert [t["special"] for t in targets] == ["custom"]

    def test_malformed_json_falls_back(self, tmp_path, monkeypatch):
        import doppler_panel
        bad = tmp_path / "doppler_targets.json"
        bad.write_text("{oops", encoding="utf-8")
        fake = tmp_path / "doppler_panel_copy.py"
        fake.write_text("", encoding="utf-8")
        monkeypatch.setattr(doppler_panel, "__file__", str(fake))
        assert [t["special"] for t in doppler_panel._load_doppler_targets()] \
            == ["custom"]

    def test_panel_uses_config_data_not_strings(self):
        """面板下拉数据项带 special 标记（分支走 currentData，不走文案匹配）。"""
        panel = DopplerPanel()
        assert panel.target_combo.count() == 3
        assert panel.target_combo.itemData(0) == "lro"
        assert panel.target_combo.itemData(2) == "custom"
        # f0 默认来自目标配置（LRO 2271 MHz），不再是代码字面量
        assert panel.f0_edit.text() == str(2271000000.0)
        panel.deleteLater()


# ---------------------------------------------------------------------------
# 文案产品语汇化（回归锁）
# ---------------------------------------------------------------------------
class TestProductWording:
    def test_tool_descriptions_have_no_competition_words(self):
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig
        ag = MBDSDRAgent(AgentConfig())
        offenders = []
        for t in ag.tool_registry.list_tools():
            text = " ".join([t.get("name", ""), t.get("description", "")])
            for w in _COMPETITION_WORDS:
                if w in text:
                    offenders.append((t["name"], w))
        assert offenders == [], f"工具描述残留赛事语境: {offenders}"

    def test_system_prompt_has_no_competition_words(self):
        from mbdsdr_ai.context_manager import SYSTEM_PROMPT
        hits = [w for w in _COMPETITION_WORDS if w in SYSTEM_PROMPT]
        assert hits == []

    def test_audit_wording_reworded(self):
        """已改词的工具描述钉扎：验收判据→指标自检、验收脊柱→真值钉扎。"""
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig
        ag = MBDSDRAgent(AgentConfig())
        descs = {t["name"]: t.get("description", "")
                 for t in ag.tool_registry.list_tools()}
        assert "指标自检" in descs.get("rx_acceptance", "")
        assert "验收判据" not in descs.get("rx_acceptance", "")
        assert "验收脊柱" not in descs.get("verify_decode_chain", "")
        # 数据源措辞也不再带赛事框架
        import mbdsdr_ai.sat_profile as sp
        assert "赛项" not in (sp.__doc__ or "")
        import mbdsdr_ai.verify_chain as vc
        assert "验收脊柱" not in (vc.__doc__ or "")
