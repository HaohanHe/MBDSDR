# SPDX-License-Identifier: MIT
"""本地能力协商层（对齐原型 ``#aiStream`` 的 bubble → toolcard → quicks）。

用户 2026-11-08：「AI 助手，我说叫你学通用 Agent，你是一点没学来」
「人工智能编排有没有？包括 Hooks，包括常驻化，包括我们作为一个 Agent」。
仓库里 ``tool_registry`` / ``orchestrator`` / ``hook_manager`` 一直都在，
但没配 API Key 时 ``MBDSDRAgent.chat()`` 直接返回 ``api_key_not_set``，
对话栏等于死的。本模块把**本进程真实装载的能力**接到对话栏上。

诚实口径（不许装）：
* 这不是语言模型。UI 必须写明「模型未配置 · 本地能力协商」；
  意图匹配按工具名/描述/类别做词面打分，不理解语义，匹配不上就如实说没有。
* 能力来自 ``tool_registry.list_tools()`` 事实源，不写死示例能力名；
  新注册的工具自动可被发现。
* 只自动执行**只读**工具；写/硬件/发射类一律 ``gated``（人工门未确认），
  绝不悄悄动设备。Hooks 可否决（``veto_for``），否决等同 gated。
* 每轮工具调用登记为真实 Orchestrator 任务、触发真实 Hooks 事件，
  所以「编排 / 常驻化」不是 UI 上画的假状态。

星文化（``star_culture.json`` / ``constellation_culture.json``）是仓库内
真实数据，作为只读知识源接入「点星即听」的读懂层；查不到就是空态，不编故事。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from mbdsdr_ai.tool_results import ToolResult, tool_success

# ---------------------------------------------------------------- 状态口径
STATUS_OK = "ok"                 # 真跑完了，回执是工具真实结果
STATUS_GATED = "gated"           # 人工门未确认（不是失败）
STATUS_OFFLINE = "not_connected" # 能力存在但当前不可用（设备没插等）
STATUS_DENIED = "denied"         # Hooks 否决
STATUS_FAILED = "failed"         # 真失败
STATUS_NO_MATCH = "no_match"     # 没有对应能力，不编造

# 写/硬件/发射类：本地协商绝不自动执行，必须用户显式确认
_GATED_PAT = re.compile(
    r"(^|_)(set|tune|transmit|start|stop|write|delete|remove|arm|uplink|"
    r"config|save|reset|clear|switch|execute|apply|launch|kill|reboot|"
    r"record|inject|send|upload|export)(_|$)", re.I)
_GATED_WORDS = ("发射", "上行", "写入", "删除", "保存", "重启", "控制", "设置")

# 工具名语义词 → 中文说法（通用词典，不是意图→工具的流程表）
_TRIGGER_ZH = {
    "status": ("状态", "情况", "连没连"),
    "state": ("状态", "情况"),
    "capability": ("能力", "会什么"),
    "capabilities": ("能力",),
    "report": ("报告", "汇报"),
    "tools": ("工具", "能力"),
    "list": ("列出", "有哪些", "清单"),
    "get": ("获取", "查一下", "查"),
    "devices": ("设备", "机器"),
    "device": ("设备",),
    "frequency": ("频率", "频点"),
    "signal": ("信号",),
    "spectrum": ("频谱",),
    "star": ("星", "星星"),
    "stars": ("星",),
    "constellation": ("星座",),
    "satellite": ("卫星",),
    "sat": ("卫星",),
    "link": ("链路", "下行"),
    "tuning": ("调谐", "收听", "听"),
    "identify": ("识别", "是谁"),
    "search": ("搜索", "找"),
    "memory": ("记忆",),
    "context": ("上下文",),
    "model": ("模型",),
    "time": ("时间", "几点"),
    "sky": ("天空", "头顶"),
    "orbit": ("轨道",),
    "pass": ("过境",),
    "weather": ("天气",),
    "quality": ("质量",),
    "decode": ("解码",),
}

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                         "mbdsdr_ai", "data")


def _load_json(name: str) -> Dict[str, Any]:
    try:
        with open(os.path.join(_DATA_DIR, name), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _tokens(text: str) -> List[str]:
    """中英混合轻量切词：英文按词，中文取 2-gram。只用于词面打分。"""
    s = (text or "").lower()
    en = re.findall(r"[a-z][a-z0-9_]{1,}", s)
    zh_chunks = re.findall(r"[\u4e00-\u9fff]+", s)
    zh: List[str] = []
    for chunk in zh_chunks:
        zh.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
        if len(chunk) == 1:
            zh.append(chunk)
    return en + zh


class LocalAgentBridge:
    """把一句自然语言路由到本进程真实能力（无模型时）。"""

    def __init__(self, agent: Any):
        self.agent = agent
        self._star_culture = _load_json("star_culture.json")
        self._const_culture = _load_json("constellation_culture.json")
        self._tools: List[Dict[str, Any]] = []
        self.refresh()

    # ---------------------------------------------------------------- 事实源
    def refresh(self) -> None:
        reg = getattr(self.agent, "tool_registry", None)
        if reg is None:
            self._tools = []
            return
        try:
            self._tools = [
                {"name": t["name"], "description": t.get("description", ""),
                 "category": t.get("category", ""),
                 "available": bool(t.get("available", True)),
                 "parameters": t.get("parameters", {})}
                for t in reg.list_tools(include_unavailable=True)
            ]
        except Exception:
            self._tools = []

    def capabilities(self) -> List[Dict[str, Any]]:
        return list(self._tools)

    def star_culture_count(self) -> int:
        return sum(1 for k in self._star_culture if not k.startswith("_"))

    def constellation_culture_count(self) -> int:
        return sum(1 for k in self._const_culture if not k.startswith("_"))

    # ---------------------------------------------------------------- 意图匹配
    def _score(self, text: str, tool: Dict[str, Any]) -> float:
        """只认工具名/类别的语义命中；描述匹配仅作微弱加分且封顶。

        实测教训：中文 2-gram 在长描述里到处能凑上「状态/设备」这类泛词，
        第一版把「现在设备什么状态」匹配到 ar_pointing_guidance，纯属瞎整。
        现在要求至少命中一个工具名语义词（+6）才可能过阈值。
        """
        low = (text or "").lower()
        if not low.strip():
            return 0.0
        name = tool["name"].lower()
        cat = (tool.get("category") or "").lower()
        desc = (tool.get("description") or "").lower()
        toks = [t for t in re.split(r"[_\-]", name) if len(t) >= 3]
        score = 0.0
        for tok in toks:
            # 英文 token 要词边界：实测 "asdfqwer" 里的 "df" 会把
            # array_correlator_df 顶上来，短缩写必须排除。
            if re.search(r"\b" + re.escape(tok) + r"\b", low):
                score += 6.0
            for zh in _TRIGGER_ZH.get(tok, ()):
                if zh in low:
                    score += 6.0
                    break
            # 明确要看状态/报告/能力清单时，优先给「读状态」而不是「列设备」
            if tok in ("status", "state", "report", "capability") and any(
                    zh in low for zh in _TRIGGER_ZH.get(tok, ())):
                score += 2.0
        if cat and cat in low:
            score += 1.5
        # 描述命中每词 0.4，封顶 1.6 —— 长描述不再能靠堆泛词翻盘
        desc_hit = 0
        for tok in _tokens(low):
            if len(tok) >= 2 and tok in desc:
                desc_hit += 1
        score += min(1.6, desc_hit * 0.4)
        return score

    def _explicit_tool(self, text: str) -> Optional[str]:
        """能力矩阵送进来的「帮我用「X」能力：」/ 「/X 参数」显式指名。"""
        m = re.search(r"帮我用[「\"']([^」\"']+)[」\"']\s*能力", text)
        if not m:
            m = re.search(r"^/\s*([A-Za-z0-9_]+)\b", text.strip())
        if not m:
            return None
        key = m.group(1).strip()
        for t in self._tools:
            if t["name"] == key or t["name"].endswith("_" + key):
                return t["name"]
        # 能力位名（中文）：按描述里出现该词的工具取第一个可用的
        for t in self._tools:
            if key and key in (t.get("description") or "") and t["available"]:
                return t["name"]
        return None

    def _args_from_text(self, text: str, tool: Dict[str, Any]) -> Dict[str, Any]:
        """只从文本里抽工具 schema 明确声明的标量参数；抽不到就不猜。

        绝不把整句塞进去当参数，也绝不补默认值冒充用户输入。
        """
        props = (tool.get("parameters") or {}).get("properties") or {}
        args: Dict[str, Any] = {}
        # 引号里的名字 → name / query / keyword / satellite / star
        quoted = re.findall(r"[「\"'『]([^」\"'』]{1,40})[」\"'』]", text)
        freq = re.search(r"(\d+(?:\.\d+)?)\s*(mhz|khz|ghz|hz)", text, re.I)
        for pname, spec in props.items():
            ptype = (spec or {}).get("type", "")
            low = pname.lower()
            if freq and (low in ("freq", "frequency", "freq_hz", "frequency_hz",
                                 "hz", "center_freq", "center_hz")):
                val = float(freq.group(1))
                unit = freq.group(2).lower()
                args[pname] = val * {"ghz": 1e9, "mhz": 1e6,
                                     "khz": 1e3, "hz": 1.0}[unit]
                continue
            if quoted and low in ("name", "query", "keyword", "satellite",
                                  "sat", "star", "text", "key", "id"):
                args[pname] = quoted[0]
                continue
            if ptype == "string" and low in ("query", "text", "q"):
                # 去掉「帮我用…」这类包装，只送用户真正问的内容
                clean = re.sub(r"^帮我用[「\"'][^」\"']+[」\"']\s*能力[:：]?\s*",
                               "", text).strip()
                if clean:
                    args[pname] = clean
        return args

    def plan(self, text: str) -> Dict[str, Any]:
        """只做规划、不执行。返回候选能力、建议与是否需要人工门。"""
        explicit = self._explicit_tool(text)
        if explicit:
            tool = next((t for t in self._tools if t["name"] == explicit), None)
            if tool:
                return {"matches": [(tool, 999.0,
                                     self._args_from_text(text, tool))],
                        "suggestions": [], "explicit": True}
        ranked: List[Tuple[Dict[str, Any], float]] = []
        for t in self._tools:
            s = self._score(text, t)
            if s > 0:
                ranked.append((t, s))
        ranked.sort(key=lambda x: (-x[1], x[0]["name"]))
        # 阈值 6：必须命中过工具名语义词。一次只执行最相关的一个能力，
        # 不替用户把一串工具串成流程。
        top = [(t, s) for t, s in ranked[:1] if s >= 6.0]
        suggestions = [t["name"] for t, s in ranked[:4] if s >= 6.0]
        if not suggestions:
            # 没有语义命中时给真实能力域（事实源里的类别），不给噪声工具名
            cats = {}
            for t in self._tools:
                if t["available"]:
                    cats[t["category"]] = cats.get(t["category"], 0) + 1
            suggestions = ["%s（%d）" % (c, n)
                           for c, n in sorted(cats.items(), key=lambda x: -x[1])[:4]]
        return {"matches": [(t, s, self._args_from_text(text, t))
                            for t, s in top],
                "suggestions": suggestions, "explicit": False}

    # ---------------------------------------------------------------- 知识源
    def lookup_star_culture(self, text: str) -> List[Dict[str, str]]:
        """从仓库真实 JSON 查星名/星官文化注记；查不到返回空（不编故事）。"""
        low = (text or "").lower()
        hits: List[Dict[str, str]] = []
        for key, val in self._star_culture.items():
            if key.startswith("_") or not isinstance(val, dict):
                continue
            zh = str(val.get("zh", ""))
            if (key.lower() in low or (zh and zh in text)):
                hits.append({"kind": "亮星", "name": key, "zh": zh,
                             "note": str(val.get("note", ""))})
        for key, val in self._const_culture.items():
            if key.startswith("_") or not isinstance(val, dict):
                continue
            zh = str(val.get("zh", ""))
            if (zh and zh in text) or key.lower() in low:
                hits.append({"kind": "星座", "name": key, "zh": zh,
                             "note": str(val.get("note", ""))})
        return hits[:6]

    # ---------------------------------------------------------------- 执行
    @staticmethod
    def _is_gated(tool: Dict[str, Any]) -> bool:
        name = tool["name"].lower()
        return bool(_GATED_PAT.search(name)) or any(
            w in (tool.get("description") or "") for w in _GATED_WORDS)

    def _emit_hook(self, event_type: str, data: Dict[str, Any]) -> Optional[Dict]:
        hm = getattr(self.agent, "hook_manager", None)
        if hm is None:
            return None
        try:
            from mbdsdr_ai.hooks import Event
            ev = Event(event_type=event_type, data=data, source="local_agent_bridge")
            veto = hm.veto_for(ev)
            return veto
        except Exception:
            # Hooks 不可用不该让本地能力整体消失；如实返回 None（未否决）
            return None

    def _record_task(self, name: str, tool: str, params: Dict[str, Any],
                     result: Any) -> Optional[str]:
        """把这次调用登记成真实编排任务（handler 复用已算出的结果，不二次执行）。

        ``result`` 是工具真实结果（``ToolResult`` 或 ``to_dict()``），不是
        摘要字符串：编排器据此按 ``success`` 判定 COMPLETED / FAILED，
        与步骤卡、Hooks 读同一个字段。``max_retries=0`` —— 结果已经拿到，
        重试只会拿同一份失败结果再跑一遍。
        """
        orch = getattr(self.agent, "orchestrator", None)
        if orch is None:
            return None
        try:
            tid = orch.add_task(name=name, description="对话栏本地能力协商",
                                tool_name=tool, tool_params=dict(params),
                                handler=lambda _p, r=result: r,
                                max_retries=0)
            orch.execute(order=[tid])
            return tid
        except Exception:
            return None

    def handle(self, text: str, confirm: bool = False,
               only_tool: Optional[str] = None,
               only_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """处理一句输入。返回 UI 直接渲染的结构（全部来自真实回执）。

        ``confirm=True`` 才允许执行 gated 工具；``only_tool`` 用于人工门
        确认后精确重放，不重新做意图匹配（避免确认的是 A、跑的是 B）。
        """
        text = (text or "").strip()
        out: Dict[str, Any] = {
            "mode": "local", "text": text, "cards": [],
            "culture": self.lookup_star_culture(text),
            "tasks": [], "hooks": [], "suggestions": [],
            "status": STATUS_NO_MATCH, "reply": "",
        }
        if not text:
            return out

        # 星名/星官是仓库内真实数据，查到就先如实报，不拿一个「沾了星字」
        # 的工具回执冒充回答，也不替用户决定顺手执行什么能力。
        if out.get("culture") and not only_tool and not confirm \
                and not self._explicit_tool(text):
            cats = {}
            for t in self._tools:
                if t["available"]:
                    cats[t["category"]] = cats.get(t["category"], 0) + 1
            out["suggestions"] = ["%s（%d）" % (c, n) for c, n in
                                  sorted(cats.items(), key=lambda x: -x[1])[:4]]
            out["status"] = "culture"
            out["reply"] = (
                "上面是仓库里的星名文化数据（公开天文常识级对照）。"
                "模型没配置，我不会编星座故事；想动能力可以直接说工具名，"
                "或点能力矩阵里的能力位。")
            return out

        if only_tool:
            tool = next((t for t in self._tools if t["name"] == only_tool), None)
            if tool is None:
                out["reply"] = "这个能力现在不在位（工具注册表里查不到）。"
                return out
            matches = [(tool, 999.0, dict(only_args or {}))]
        else:
            p = self.plan(text)
            matches = p["matches"]
            out["suggestions"] = p["suggestions"]

        if not matches:
            out["reply"] = (
                "这句话我没有匹配到本地能力——模型没配置，我不会编一个回答。"
                "可以点下面的能力位，或者直接说工具名（比如 /sdr_status）。")
            return out

        reg = getattr(self.agent, "tool_registry", None)
        if reg is None:
            out["reply"] = "工具注册表未装载，本地能力协商不可用。"
            out["status"] = STATUS_FAILED
            return out

        for tool, _score, args in matches:
            name = tool["name"]
            gated = self._is_gated(tool) and not confirm
            veto = self._emit_hook("agent.tool_call",
                                   {"tool": name, "args": args,
                                    "gated": gated, "text": text})
            out["hooks"].append({"event": "agent.tool_call", "tool": name,
                                 "veto": bool(veto)})
            if veto:
                out["cards"].append({
                    "tool": name, "args": args, "status": STATUS_DENIED,
                    "content": "Hooks 否决了这次调用：%s" %
                               (veto.get("reason") or "未说明原因")})
                out["status"] = STATUS_DENIED
                continue
            if gated:
                out["cards"].append({
                    "tool": name, "args": args, "status": STATUS_GATED,
                    "content": "这类能力会改动设备/配置，需要你点确认才执行。",
                    "confirm_tool": name, "confirm_args": args})
                out["status"] = STATUS_GATED
                continue
            if not tool["available"]:
                out["cards"].append({
                    "tool": name, "args": args, "status": STATUS_OFFLINE,
                    "content": "能力在位，但当前设备/依赖未连接，未执行。"})
                out["status"] = STATUS_OFFLINE
                continue

            t0 = time.perf_counter()
            try:
                result = reg.call(name, args)
                ok = bool(tool_success(result))
                content = result.content if ok else (result.error or result.content)
                latency = (time.perf_counter() - t0) * 1000.0
            except Exception as e:
                ok, content, latency = False, "%s: %s" % (type(e).__name__, e), 0.0
                # 补一份显式失败结果：登记任务时同样按 success 判失败，
                # 不因「没有 result 对象」就落到 COMPLETED。
                result = ToolResult(success=False, content=content)
            status = STATUS_OK if ok else STATUS_FAILED
            out["cards"].append({
                "tool": name, "args": args, "status": status,
                "content": str(content)[:1200], "latency_ms": latency})
            out["status"] = status
            tid = self._record_task(name, name, args, result)
            if tid:
                out["tasks"].append({"id": tid, "name": name,
                                     "status": "completed" if ok else "failed"})
            self._emit_hook("agent.tool_result",
                            {"tool": name, "success": ok, "status": status})
            out["hooks"].append({"event": "agent.tool_result",
                                 "tool": name, "ok": ok})

        # 回执摘要：不伪造「模型说」，只陈述工具回执
        if out["status"] == STATUS_OK:
            out["reply"] = "已按本地能力执行，上面是工具的真实回执。"
        elif out["status"] == STATUS_GATED:
            out["reply"] = "需要你确认后才会动设备。"
        elif out["status"] == STATUS_OFFLINE:
            out["reply"] = "能力在位，但设备没连上，所以没有执行。"
        elif out["status"] == STATUS_FAILED:
            out["reply"] = "工具执行失败，原因写在回执卡里，不替它遮掩。"
        return out

    def record_external_call(self, tool_name: str, args: Dict[str, Any] = None,
                             result: Any = None) -> Optional[str]:
        """模型链路（配了 API Key）的工具调用也登记到同一套编排/Hooks。

        本地协商和模型对话是两条入口，但「编排 / 常驻化」必须是同一份
        事实源：模型跑过的工具同样产生真实 Orchestrator 任务与 Hooks
        事件，状态条与能力矩阵读到的数字才不分裂。

        成败**不在这里另行判断**：传入工具原始结果（``ToolResult`` 或
        ``to_dict()``），统一走 :func:`tool_success` 读 ``success`` 字段。
        旧签名收 ``ok`` 布尔值，调用方用 ``not res.get("error")`` 现算——
        失败结果的 error 默认空串，于是同一次失败在步骤卡是「失败」、
        在这里是「成功」。没有结果（未执行）时不登记任务、不写
        tool_result 事件，避免伪造任何一种结论。
        """
        args = dict(args or {})
        self._emit_hook("agent.tool_call",
                        {"tool": tool_name, "args": args,
                         "source": "model_chat", "gated": False})
        ok = tool_success(result)
        if ok is None:
            return None
        tid = self._record_task(tool_name, tool_name, args, result)
        self._emit_hook("agent.tool_result",
                        {"tool": tool_name, "success": bool(ok),
                         "source": "model_chat"})
        return tid

    # ---------------------------------------------------------------- 常驻状态
    def live_state(self) -> Dict[str, Any]:
        """给常驻状态条 / 能力矩阵读的真实数字。"""
        reg = getattr(self.agent, "tool_registry", None)
        hm = getattr(self.agent, "hook_manager", None)
        orch = getattr(self.agent, "orchestrator", None)
        try:
            hooks = hm.list_hooks() if hm is not None else []
            hist = hm.get_history(limit=1000) if hm is not None else []
        except Exception:
            hooks, hist = [], []
        try:
            tasks = orch.list_tasks() if orch is not None else []
        except Exception:
            tasks = []
        try:
            stats = orch.get_stats() if orch is not None else {}
        except Exception:
            stats = {}
        return {
            "kernel": self.agent is not None,
            "model": bool(getattr(getattr(self.agent, "config", None),
                                  "api_key", "")),
            "tools": len(self._tools),
            "tools_available": sum(1 for t in self._tools if t["available"]),
            "hooks": len(hooks), "events": len(hist),
            "tasks": len(tasks), "task_stats": stats,
            "task_rows": [dict(t, id=t.get("task_id") or t.get("id", ""))
                          for t in tasks[-6:]],
            "event_rows": [e.to_dict() for e in hist[-6:]],
            "star_culture": self.star_culture_count(),
            "constellation_culture": self.constellation_culture_count(),
        }
