# SPDX-License-Identifier: MIT
"""P4 AI 边界用语门禁（spec P4，Task 142）。

判据（`.trae/specs/mbdsdr-system-lifecycle/spec.md` P4）：「AI 定义」只覆盖
调制识别与参数建议，最终动作由用户确认；禁止「任意信号自主解调」这类
无依据表述（外部依据：ITU-R SM.2152 区分 SDR 与认知无线电；3GPP Rel-18
的 AI/ML 用例范围很窄）。

本文件是**行为门禁**：描述一律从运行时 ``ToolRegistry`` 取，不读任何
``.py`` 源码文本（避免被 ``tests/test_source_pin_ratchet.py`` 判为源码 pin）。
无 GUI / 无网络：按 ``tests/test_tool_surface_audit.py`` 的方式把
``ModelManager.fetch_models`` 置空后构造 agent。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402


# ---------------------------------------------------------------------------
# 违规词表：每条都写清「为什么这句不能承诺」，便于新增词条时判断口径。
# 注意故意**不用**宽泛的「自动」：`自动识别调制方式` 是有依据的合法表述，
# 被误杀会让门禁失去区分力（P4 允许调制识别与参数建议）。
# ---------------------------------------------------------------------------
BANNED_CLAIMS = (
    # 暗示对未知信号可无条件解码：AI 定义只覆盖调制识别与参数建议，
    # 解调参数最终由用户确认，不存在「任意信号」的通用解调能力。
    ("任意信号", "暗示对未知信号无条件可用，超出调制识别/参数建议边界"),
    # 与「任意信号」同源的自主行为主张：最终动作必须由用户确认。
    ("自主解调", "宣称 AI 自主决定解调，绕开用户确认"),
    # 同上，只是换了个「全自动」的前缀，仍属未经确认的自主解调。
    ("全自动解调", "宣称全流程无需用户参与，违反「最终动作由用户确认」"),
    # P3 已把指向能力上限限定为「大致天区 / 度级」；精准指向是无依据的
    # 精度承诺（手机罗盘误差实测可达 10°–20°+）。
    ("精准指向", "超出「大致天区/度级」的精度承诺（P3）"),
    # 寻星镜是独立光学器件，手机/软件指向不能替代它。
    ("替代寻星镜", "宣称替代独立光学器件，属无依据的能力越界"),
    # 指向闭环未完成（无图像解算残差验证），不能承诺对准结果。
    ("保证对准", "指向成功 ≠ 对准成功，未以图像解算残差验证前不得承诺"),
    # 卫星解码依赖体制、速率、信噪比与星历；不存在「所有卫星」的通解。
    ("所有卫星都能解码", "忽略体制/速率/信噪比/星历条件的全称承诺"),
    # 写工具一律走外部确认门，AI 不能替用户做最终决定。
    ("无需人工确认", "违反写门设计：动作必须经用户确认（confirm_token）"),
)


def find_claim_violations(items):
    """在运行时描述集合里查违规用语。

    :param items: 可迭代的 ``(tool_name, description)``
    :return: 命中列表，每项含 ``tool`` / ``phrase`` / ``reason`` / ``text``；
             无命中返回空列表。检查函数与数据来源解耦，便于反向验证。
    """
    hits = []
    for name, description in items:
        text = description or ""
        for phrase, reason in BANNED_CLAIMS:
            if phrase in text:
                hits.append({"tool": name, "phrase": phrase,
                             "reason": reason, "text": text})
    return hits


def _format_hits(hits):
    return "\n".join(
        f"  {hit['tool']}  命中「{hit['phrase']}」（{hit['reason']}）：{hit['text']}"
        for hit in hits)


@pytest.fixture(scope="module")
def real_registry():
    from mbdsdr_ai import model_manager as _mm
    from mbdsdr_ai.config import AgentConfig
    from mbdsdr_ai.agent import MBDSDRAgent

    original = _mm.ModelManager.fetch_models
    _mm.ModelManager.fetch_models = lambda self, *a, **k: None  # 不发起网络请求
    try:
        agent = MBDSDRAgent(AgentConfig(api_key="sk-test", model="test-model"))
    finally:
        _mm.ModelManager.fetch_models = original
    return agent.tool_registry


# ---------------------------------------------------------------------------
# 门禁本体：全部工具（含 unavailable，当前 513 条）的描述逐条过词表
# ---------------------------------------------------------------------------
def test_tool_descriptions_have_no_unbacked_ai_claims(real_registry):
    tools = real_registry.list_tools(include_unavailable=True)
    # 防止空转：必须覆盖 registry 的全部工具，且数量与工具面一致（513）。
    assert len(tools) == len(real_registry.tools), (
        f"描述枚举不完整：list_tools={len(tools)}，registry={len(real_registry.tools)}")
    assert len(tools) >= 500, f"工具数异常（{len(tools)}），门禁可能没拿到真实注册表"

    hits = find_claim_violations(
        (tool["name"], tool["description"]) for tool in tools)
    assert not hits, (
        f"{len(tools)} 条工具描述里出现 {len(hits)} 处 P4 无依据表述；"
        "按 spec P4，AI 定义只覆盖调制识别与参数建议，最终动作由用户确认：\n"
        + _format_hits(hits))


# ---------------------------------------------------------------------------
# 反向验证 1：伪造命中句必须被抓到（证明门禁不是空转）
# ---------------------------------------------------------------------------
def test_gate_catches_fabricated_claim():
    fake = [("demo_overclaim", "本工具可对任意信号自主解调，无需人工确认。")]
    hits = find_claim_violations(fake)
    phrases = {hit["phrase"] for hit in hits}
    assert {"任意信号", "自主解调", "无需人工确认"} <= phrases, hits
    assert all(hit["tool"] == "demo_overclaim" for hit in hits)


# ---------------------------------------------------------------------------
# 反向验证 2：有依据的合法描述必须通过（防止把「自动识别调制方式」误杀）
# ---------------------------------------------------------------------------
def test_gate_accepts_supported_wording():
    legit = [
        ("solve_attitude", "根据星点解算姿态，失败时说明原因。"),
        ("mod_classify", "自动识别调制方式并给出参数建议，最终动作由用户确认。"),
    ]
    assert find_claim_violations(legit) == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
