# SPDX-License-Identifier: MIT
"""
AI 执行步骤投影（Qt 无关，可单测）
==================================
把 agent.chat() 返回的 tool_calls/tool_results，以及 workflow_execute /
subagent_execute 的多步 JSON 结果，投影成步骤卡字段（StepRecord），
供 desktop/ai_panel.py 渲染结构化步骤卡。

字段语义对齐两处既有实现（学机制不抄代码）：
  * ``cpp/src/ui/task_steps_view.h`` 的 ``ai::StepResult``：
    工具名 / 关键参数（argsResolved）/ 状态 / 结果摘要（summary）/ 耗时（elapsedMs）；
  * ``mobile/lib/models/task_step.dart`` 的 ``TaskStep``：
    状态只由真实工具执行结果推导，绝不预填/伪造步骤——没有结果就是
    running/未完成态，绝不臆造 success。

多步结果投影规则（诚实边界）：
  * workflow_execute：WorkflowResult.step_results 里逐步有真实 success/error/
    skipped → 一一步一卡，状态如实映射；
  * subagent_execute：子代理只回传 tools_used（真实执行过的工具名）与整体
    status，**没有单步结果** → 每工具一张子卡，状态随整体结果，摘要明确写
    「单步结果未回传」，不给单步编造成功/失败理由。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from tokens import tokens as _tok  # noqa: E402  # 截断长度收口在 tokens 层

# 状态语义：与 cpp StepState（Running/Succeeded/Failed/Pending-Aborted）和
# mobile TaskStepStatus（running/success/failed）对齐；skipped 对应
# 「条件不满足未执行」（cpp Pending/Aborted 语义，mobile 无此态）。
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

# 多步工具（结果 JSON 里含子步骤，需展开投影）
WORKFLOW_TOOLS = ("workflow_execute",)
SUBAGENT_TOOLS = ("subagent_execute",)


@dataclass
class StepRecord:
    """一步真实工具调用的步骤卡模型。"""
    tool: str
    args_preview: str = ""
    status: str = STATUS_RUNNING
    summary: str = ""
    elapsed_ms: float = 0.0
    error: str = ""
    parent: str = ""          # 所属多步工具名（workflow_execute 等）；空 = 顶层步骤
    depth: int = 0            # 渲染缩进层级（子步骤 = 1）
    extra: Dict[str, Any] = field(default_factory=dict)


# ── 文本工具（对齐 mobile 的 isFailureResult / summarizeResult 语义）──

def truncate_text(text: str, limit: int) -> str:
    """超长截断并补省略号；limit<=0 时原样返回。"""
    s = "" if text is None else str(text)
    if limit and limit > 0 and len(s) > limit:
        return s[:limit] + "…"
    return s


def args_preview(args: Any) -> str:
    """关键参数预览（tokens.AI.args_preview_chars 截断）。"""
    if args is None:
        return ""
    if isinstance(args, str):
        return truncate_text(args.strip(), int(_tok().AI["args_preview_chars"]))
    try:
        text = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(args)
    return truncate_text(text, int(_tok().AI["args_preview_chars"]))


def is_failure_result(text: str) -> bool:
    """真实结果文本是否表明失败（对齐 mobile isFailureResult，不臆造成功）。

    覆盖：显式 {"ok": false}（含手动模式 gated，容忍空格）、执行层 "Error..." 文本。
    """
    s = (text or "").strip()
    if not s:
        return False
    if s.startswith("Error"):
        return True
    return re.search(r'"ok"\s*:\s*false', s) is not None


def summarize_result(text: str) -> str:
    """从真实结果提取一行短摘要（对齐 mobile summarizeResult）。

    优先取 error / message 字段值；否则截断原文。空文本返回空串。
    """
    s = (text or "").strip()
    if not s:
        return ""
    m = re.search(r'"(?:error|message)"\s*:\s*"([^"]*)"', s)
    if m and m.group(1):
        return truncate_text(m.group(1), int(_tok().AI["summary_chars"]))
    return truncate_text(s, int(_tok().AI["summary_chars"]))


# ── 投影：agent.chat() 结果 ────────────────────────────────────────

def _parse_args(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _payload_from_content(content: Any) -> Optional[Dict[str, Any]]:
    """工具结果 content 是 JSON 文本时解析成 dict；解析不了返回 None。"""
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        parsed = json.loads(content)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def steps_from_chat(tool_calls: List[Dict[str, Any]],
                    tool_results: List[Dict[str, Any]]) -> List[StepRecord]:
    """把一轮 agent.chat() 的工具调用/结果投影成步骤卡列表。

    tool_calls 项：{"name": ..., "arguments": str|dict}；
    tool_results 项：ToolResult.to_dict()（success/content/tool_name/args/
    latency_ms/error/...）。结果缺失的调用如实落 running（未收到结果不臆造）。
    workflow_execute / subagent_execute 的多步 JSON 结果展开为子步骤卡。
    """
    steps: List[StepRecord] = []
    results = list(tool_results or [])
    for i, tc in enumerate(tool_calls or []):
        if not isinstance(tc, dict):
            continue
        name = str(tc.get("name", "") or "unknown")
        args = _parse_args(tc.get("arguments"))
        tr = results[i] if i < len(results) else None

        if tr is None:
            # 未收到结果（流被打断/执行器未回填）：诚实落 running，不臆造
            steps.append(StepRecord(
                tool=name, args_preview=args_preview(args),
                status=STATUS_RUNNING, summary="", elapsed_ms=0.0))
            continue

        content = tr.get("content", "")
        success = bool(tr.get("success", False))
        err = str(tr.get("error", "") or "")
        text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
        status = STATUS_SUCCESS if (success and not is_failure_result(text)) else STATUS_FAILED
        steps.append(StepRecord(
            tool=name,
            args_preview=args_preview(tr.get("args", args) or args),
            status=status,
            summary=summarize_result(text),
            elapsed_ms=float(tr.get("latency_ms", 0) or 0),
            error=err,
        ))

        # 多步 JSON 结果展开为子步骤卡
        payload = _payload_from_content(content)
        if not payload:
            continue
        if name in WORKFLOW_TOOLS:
            steps.extend(steps_from_workflow(payload))
        elif name in SUBAGENT_TOOLS:
            steps.extend(steps_from_subagent(payload))
    return steps


def steps_from_tool_result(tool_name: str, result: Dict[str, Any]) -> StepRecord:
    """把一次异步工具调用结果（UI→worker 转发链）投影成单张步骤卡。"""
    result = result if isinstance(result, dict) else {"result": str(result)}
    text = json.dumps(result, ensure_ascii=False)
    failed = "error" in result or is_failure_result(text)
    if result.get("skipped"):
        # 软件工具不二次转发：不是一次硬件执行，不进步骤卡
        return StepRecord(tool=str(tool_name), status=STATUS_SKIPPED,
                          summary=str(result.get("reason", "")), error="")
    return StepRecord(
        tool=str(tool_name),
        args_preview="",
        status=STATUS_FAILED if failed else STATUS_SUCCESS,
        summary=summarize_result(text),
        elapsed_ms=float(result.get("latency_ms", 0) or 0),
        error=str(result.get("error", "") or ""),
    )


# ── 投影：workflow_execute / subagent_execute 多步 JSON ─────────────

def steps_from_workflow(payload: Dict[str, Any]) -> List[StepRecord]:
    """WorkflowResult.to_dict() → 子步骤卡（parent=workflow_execute）。

    step_results 条目（见 mbdsdr_ai/workflow_engine.py）：
      * {"step_id", "tool", "success": true, "result"}          → success
      * {"step_id", "tool", "success": false, "error", ...}     → failed
      * {"step_id", "tool", "skipped": true, "reason"}          → skipped
    """
    out: List[StepRecord] = []
    wf_name = str(payload.get("workflow_name", "") or "")
    for entry in payload.get("step_results") or []:
        if not isinstance(entry, dict):
            continue
        tool = str(entry.get("tool", "") or entry.get("tool_name", "") or "unknown")
        if entry.get("skipped"):
            status, summary, err = (STATUS_SKIPPED,
                                    str(entry.get("reason", "") or "条件不满足，未执行"), "")
        elif entry.get("success"):
            status = STATUS_SUCCESS
            summary = summarize_result(str(entry.get("result", "") or ""))
            err = ""
        else:
            status = STATUS_FAILED
            err = str(entry.get("error", "") or "")
            summary = summarize_result(err) or "步骤失败"
        out.append(StepRecord(
            tool=tool, args_preview="", status=status, summary=summary,
            elapsed_ms=0.0, error=err, parent="workflow_execute", depth=1,
            extra={"step_id": entry.get("step_id"), "workflow": wf_name},
        ))
    return out


def steps_from_subagent(payload: Dict[str, Any]) -> List[StepRecord]:
    """SubagentResult.to_dict() → 子步骤卡（parent=subagent_execute）。

    子代理只回传 tools_used/tools_called（真实执行过的工具名）与整体
    status/steps_used，无单步结果——每工具一张卡，状态随整体结果，摘要
    明确标注「单步结果未回传」，绝不为单步编造结果。
    """
    out: List[StepRecord] = []
    tools = payload.get("tools_used") or payload.get("tools_called") or []
    overall_ok = bool(payload.get("success", False)) and (
        payload.get("status", "ok") == "ok")
    status = STATUS_SUCCESS if overall_ok else STATUS_FAILED
    if payload.get("truncated"):
        note = "子代理达到 max_steps 截断（部分执行，单步结果未回传）"
    elif overall_ok:
        note = "子代理内部调用（单步结果未回传，随整体结果 ok）"
    else:
        note = "子代理内部调用（单步结果未回传，整体结果失败）"
    for tool in tools:
        out.append(StepRecord(
            tool=str(tool), args_preview="", status=status,
            summary=note, elapsed_ms=0.0,
            error=str(payload.get("error", "") or ""),
            parent="subagent_execute", depth=1,
            extra={"subagent_id": payload.get("subagent_id", "")},
        ))
    return out
