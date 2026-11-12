# SPDX-License-Identifier: MIT
"""工具集预算选择（P7）：按当前请求挑出「够用且不超窗」的工具 schema 子集。

为什么需要它
------------
生产注册表实测 495 个工具、序列化后约 218k 字符（启发式约 9.3 万 token），
而多数模型上下文窗口是 32k。若每轮把全量 schema 发给模型，schema 本身就能
独占并超出窗口——模型要么被服务端截断、要么直接 400，工具选择准确率无从谈起。
本模块做**确定性、可解释**的裁剪：

  1. 核心工具永远保留（list_tools / context_status / model_status / tool_log），
     保证模型始终能自行发现被裁掉的工具名；
  2. 其余工具按请求关键词打分（工具名权重 4、描述权重 1、命中类目权重 3）；
  3. 先装「名称命中请求关键词」的工具（强证据），再按分数从高到低装描述/类目
     命中（弱证据），直到 token 预算用完；命中不足时用注册顺序补足最小数量；
     弱证据不能把强证据挤出预算——否则整类类目加成会把无关工具抬进候选；
  4. 返回裁剪报告（保留/裁掉数量、估算 token、命中类目、可解释 note）。

裁剪不是「删除能力」：工具执行仍按注册表全量解析，模型只要知道名字就能调用；
`list_tools` 始终可用，所以裁掉的工具仍然可被发现——note 会把这一点明确告诉模型，
避免模型以为工具不存在。

不做什么：不判断「该不该拒绝」这类安全语义（那是 tool_policy / 外部确认 token 的职责），
也不替代模型的语义选择——它只保证发给模型的**候选集**在预算内且相关。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .context_manager import estimate_tokens

# ---- 固定参数（测试逐项钉住，只许收紧；见 tests/test_ai_tool_budget.py） ----
DEFAULT_BUDGET_RATIO = 0.25    # 工具 schema 最多占上下文窗口的 25%
MIN_BUDGET_TOKENS = 1000       # 窗口再小也至少给工具留这么多
MIN_TOOLS = 8                  # 相关命中不足时，至少保留这么多工具
NAME_WEIGHT = 4
CATEGORY_WEIGHT = 5            # 类目命中比描述命中更可靠
DESC_WEIGHT = 1
DESC_CAP = 3                   # 描述命中封顶，防长描述互相引用造成噪声
MIN_SCORE = 4                  # 低于此分视为「本请求无关」，不进候选

CORE_TOOLS: Tuple[str, ...] = (
    "list_tools", "context_status", "model_status", "tool_log",
)

# 请求词 -> (匹配关键词, 相关类目)。按命中累加，多个领域可同时命中。
_TERM_RULES: Tuple[Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]], ...] = (
    (("频谱", "瀑布", "扫频", "fft", "spectrum", "waterfall", "峰值", "谱线"),
     ("spectrum", "fft", "waterfall", "瀑布", "频谱", "扫频", "谱"),
     ("spectrum", "sdr_spectrum", "sdr_analysis", "signal_analysis")),
    (("调谐", "频率", "中心频率", "频偏", "vfo", "tune", "frequency"),
     ("frequency", "tune", "vfo", "调谐", "频率", "中心频率"),
     ("sdr_frequency", "sdr_device")),
    (("解调", "音频", "声音", "收听", "音量", "静噪", "demod", "audio", "wfm", "nfm", "am", "fm"),
     ("demod", "demodulat", "audio", "squelch", "volume", "解调", "音频", "静噪", "音量", "立体声", "wfm", "nfm"),
     ("demod", "sdr_demod", "digital_modes", "digital_mode")),
    (("录音", "录制", "落盘", "保存iq", "基带", "record", "capture"),
     ("record", "capture", "录制", "录音", "落盘", "保存", "基带", "保存"),
     ("sdr_record", "capture", "file_tracker")),
    (("解码", "解析", "报文", "识别信号", "decode", "parse", "adsb", "ais", "aprs", "ft8", "rtty", "sstv", "apt", "lrpt", "rds", "acars", "cw", "mfsk", "psk", "feldhell"),
     ("decode", "parse", "识别", "解码", "解析", "报文", "adsb", "ais", "aprs", "ft8", "rtty", "sstv", "apt", "lrpt", "rds", "acars", "cw", "psk", "mfsk", "feldhell"),
     ("decode", "sdr_decode", "digital_mode", "digital_modes", "ham_modes", "rds_decoder", "multimon", "protocol", "broadcast")),
    (("回放", "iq 文件", "本地文件", "离线复现"),
     ("回放", "iq", "file", "playback", "离线"),
     ("sdr_device", "file_tracker", "capture")),
    (("设备", "连接", "增益", "采样率", "带宽", "天线", "驱动", "状态", "ppm", "校正", "device", "connect", "gain", "sample rate", "bandwidth"),
     ("device", "connect", "gain", "sample_rate", "bandwidth", "antenna", "设备", "连接", "增益", "采样率", "带宽", "天线", "rtlsdr", "hackrf", "limesdr", "bladerf", "ppm", "校正", "状态", "iq 文件"),
     ("sdr_device", "sdr_gain", "sdr_frequency", "acquisition", "hal")),
    (("卫星", "轨道", "过境", "tle", "天空", "云图", "多普勒", "假彩色", "satellite", "orbit", "doppler"),
     ("satellite", "orbit", "tle", "sky", "pass", "doppler", "卫星", "轨道", "过境", "天空", "云图", "多普勒", "星历", "假彩色", "falsecolor"),
     ("satellite", "orbit", "sky", "sdr_satellite", "doppler", "sat_groundstation", "sat_profile", "pointing")),
    (("工作流", "任务", "定时", "自动化", "调度", "workflow", "schedule", "subagent"),
     ("workflow", "task", "schedule", "cron", "工作流", "任务", "定时", "自动化", "子代理", "调度"),
     ("workflow", "workflow_recorder", "scheduler", "subagent")),
    (("云台", "姿态", "指向", "gimbal", "pose", "pointing"),
     ("gimbal", "pose", "pointing", "云台", "姿态", "指向"),
     ("gimbal", "pose", "pointing")),
    (("记忆", "上下文", "模型", "token", "memory", "context", "model"),
     ("memory", "context", "model", "记忆", "上下文", "模型", "token"),
     ("memory", "meta")),
    (("校准", "矢量网络", "vna", "驻波", "s11"),
     ("vna", "calibrat", "sweep", "校准", "驻波", "反射"),
     ("vna",)),
    (("gps", "gnss", "定位", "授时", "北斗"),
     ("gnss", "gps", "定位", "授时", "北斗", "卫星导航"),
     ("gnss", "gnss_monitor", "sdr_location")),
)


def tool_budget_tokens(max_context_tokens: int,
                       ratio: float = DEFAULT_BUDGET_RATIO) -> int:
    """按上下文窗口折算工具 schema 预算（下限 MIN_BUDGET_TOKENS）。"""
    return max(MIN_BUDGET_TOKENS, int(max_context_tokens * ratio))


def estimate_definitions_tokens(definitions: Sequence[Dict[str, Any]]) -> int:
    """估算一组工具定义的 token 数（与 ContextManager 同一估算器）。"""
    if not definitions:
        return 0
    try:
        blob = json.dumps(list(definitions), ensure_ascii=False)
    except Exception:
        return 0
    return estimate_tokens(blob)


def _query_profile(request: str) -> Tuple[set, set]:
    """从请求里提取（匹配关键词, 相关类目）集合。"""
    text = (request or "").lower()
    keywords: set = set()
    categories: set = set()
    for terms, kws, cats in _TERM_RULES:
        if any(t in text for t in terms):
            keywords.update(k.lower() for k in kws)
            categories.update(cats)
    return keywords, categories


def _score(definition: Dict[str, Any], keywords: set, categories: set) -> int:
    fn = definition.get("function", {}) or {}
    name = (fn.get("name") or "").lower()
    desc = (fn.get("description") or "").lower()
    score = 0
    desc_hits = 0
    for kw in keywords:
        if kw in name:
            score += NAME_WEIGHT
        elif kw in desc:
            desc_hits += 1
    # 工具描述普遍很长且互相引用（"前置条件 sdr_connect"、"设备未连接"），
    # 描述命中只作弱信号并封顶，否则任何设备类请求都会把所有工具拉进来。
    score += min(desc_hits, DESC_CAP) * DESC_WEIGHT
    return score


def _name_hits(definition: Dict[str, Any], keywords: set) -> int:
    """工具名里命中的请求关键词个数（唯一的「强证据」判据）。

    为什么单列而不是从 score 反推：类目加成 5 分已经高于 MIN_SCORE，
    「名称 0 命中、仅描述 + 类目」的工具也会拿到 8 分，与真相关工具同档；
    只看总分会把这类弱证据工具和强证据混在一起排，预算先被弱证据占满。
    """
    name = ((definition.get("function", {}) or {}).get("name") or "").lower()
    return sum(1 for kw in keywords if kw in name)


@dataclass
class ToolSelection:
    """一次工具集选择的结果（可序列化，供诊断与 UI 展示）。"""
    definitions: List[Dict[str, Any]] = field(default_factory=list)
    kept_names: List[str] = field(default_factory=list)
    evicted_names: List[str] = field(default_factory=list)
    tokens: int = 0
    budget_tokens: int = 0
    matched_categories: List[str] = field(default_factory=list)
    trimmed: bool = False
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kept_count": len(self.kept_names),
            "evicted_count": len(self.evicted_names),
            "kept_names": list(self.kept_names),
            "evicted_names": list(self.evicted_names),
            "tokens": self.tokens,
            "budget_tokens": self.budget_tokens,
            "matched_categories": list(self.matched_categories),
            "trimmed": self.trimmed,
            "note": self.note,
        }


def select_tools(definitions: Sequence[Dict[str, Any]], request: str,
                 budget_tokens: int, min_tools: int = MIN_TOOLS,
                 core_names: Iterable[str] = CORE_TOOLS,
                 categories: Dict[str, str] | None = None) -> ToolSelection:
    """按请求与预算挑选工具定义子集。

    definitions: OpenAI function calling 定义列表（注册顺序）
    request: 用户本轮请求文本
    budget_tokens: 工具 schema token 上限
    categories: 可选 name -> category 映射（用于类目加权）
    """
    defs = list(definitions or [])
    if not defs:
        return ToolSelection(budget_tokens=int(budget_tokens))

    core = set(core_names)
    keywords, wanted_cats = _query_profile(request)
    cats_map = dict(categories or {})

    scored: List[Tuple[int, int, Dict[str, Any]]] = []
    for idx, d in enumerate(defs):
        name = ((d.get("function") or {}).get("name") or "")
        s = _score(d, keywords, wanted_cats)
        cat = cats_map.get(name, "")
        if cat and any((cat == c or cat.startswith(c + "_")) for c in wanted_cats):
            s += CATEGORY_WEIGHT
        scored.append((s, idx, d))

    core_defs = [d for s, _, d in scored
                 if ((d.get("function") or {}).get("name") or "") in core]
    others = [(s, i, d, _name_hits(d, keywords)) for s, i, d in scored
              if ((d.get("function") or {}).get("name") or "") not in core]
    # 名称命中是强证据，先装；名称 0 命中（描述 + 类目凑出来的分）后装。
    # 只按总分排会让「类目 +5」把整类无关工具抬到与真相关工具同档，预算被它们
    # 先占满，而名称真正命中本请求的工具反被裁掉（dev-03 事故原型）。
    # 组内仍按分数降序、注册序号升序，保证确定性。
    others.sort(key=lambda t: (t[3] == 0, -t[0], t[1]))

    kept_defs: List[Dict[str, Any]] = []
    kept_tokens = 0
    evicted: List[str] = []

    def _tok(d: Dict[str, Any]) -> int:
        return estimate_definitions_tokens([d]) + 4

    for d in core_defs:
        kept_defs.append(d)
        kept_tokens += _tok(d)

    has_scored = any(s >= MIN_SCORE for s, _, _, _ in others)
    kept_others = 0
    for s, _, d, _nh in others:
        t = _tok(d)
        # 只保留被本请求明显命中的工具。描述弱命中（< MIN_SCORE）也不进候选：
        # 否则「查频谱」时候选里会混进录音/开设备工具，等于给模型制造误调机会。
        if s < MIN_SCORE and has_scored:
            evicted.append((d.get("function") or {}).get("name", ""))
            continue
        if kept_tokens + t > budget_tokens:
            evicted.append((d.get("function") or {}).get("name", ""))
            continue
        kept_defs.append(d)
        kept_tokens += t
        kept_others += 1

    # 请求完全没命中任何工具（寒暄/空串）：用注册顺序兜底 min_tools 个，
    # 保证模型仍有一组基础工具可看，而不是只拿到 4 个核心工具。
    if not has_scored:
        for s, _, d, _nh in others[kept_others:]:
            t = _tok(d)
            if kept_others >= min_tools or kept_tokens + t > budget_tokens:
                evicted.append((d.get("function") or {}).get("name", ""))
                continue
            kept_defs.append(d)
            kept_tokens += t
            kept_others += 1

    kept_names = [(d.get("function") or {}).get("name", "") for d in kept_defs]
    total = len(defs)
    trimmed = len(kept_defs) < total
    note = ""
    if trimmed:
        note = (
            f"[工具集预算] 本轮仅下发 {len(kept_defs)}/{total} 个工具 schema"
            f"（约 {kept_tokens} tokens，预算 {budget_tokens}），按当前请求相关度裁剪。"
            "未列出的工具仍然存在：需要时先调用 list_tools 查询工具名，再按名调用；"
            "不要假定未列出 = 不存在，也不要因此改用编造的参数。"
        )
    return ToolSelection(
        definitions=kept_defs,
        kept_names=kept_names,
        evicted_names=evicted,
        tokens=kept_tokens,
        budget_tokens=int(budget_tokens),
        matched_categories=sorted(wanted_cats),
        trimmed=trimmed,
        note=note,
    )
