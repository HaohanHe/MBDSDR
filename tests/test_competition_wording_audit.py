# SPDX-License-Identifier: MIT
"""
tests/test_competition_wording_audit.py
========================================
赛事口径残留审计的回归锁（「我们是专业工具，不是比赛工具」）：

  1. 面向 LLM 的工具描述（ToolRegistry）与系统提示词中不得出现
     赛项/比赛/答辩/挑战赛/hackathon 等赛事语境词；
  2. acquisition / verify_chain / sat_profile 的文案已产品语汇化
     （「验收脊柱」「材料口径」「哈工赛项」不再出现）。

（原「定轨目标表配置化」一组随 2026-11-03 定轨去功能化铁律随面板整体移除：
定轨不再是 UI 功能，唯一入口是用户与模型对话，目标表配置随之不存在。）

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_competition_wording_audit.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 赛事语境词表（用户/模型可见文案不得出现；「评分」类产品质量评分不在此列）
_COMPETITION_WORDS = ("赛项", "比赛", "答辩", "挑战赛", "hackathon", "Hackathon")


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
