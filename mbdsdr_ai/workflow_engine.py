# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - 工作流引擎
============================
Workflow Engine：JSON 定义的多步骤工具调用序列。

核心能力（对照之前 ~/.mbdsdr/workflows/ 的设计）：
- JSON 定义的多步骤工具调用序列
- 每个 step 有 tool_name, params, description, timeout, retry_on_failure, max_retries, condition
- 支持参数模板（{{variable}}），上一步的输出可以作为下一步的输入
- 支持条件执行（condition），根据上一步结果决定是否执行
- 工作流由 AI 判断后显式调用（workflow_execute），或由用户定义/调度
- 执行结果记录（usage_count, success_count）

【红线】引擎不含任何内置固定步骤序列（无 preset），也没有任何
「文本命中关键词就自动跑固定流程」的触发器——步骤编排是模型运行时的
判断，用户/AI 自定义工作流只作为可选素材存放于 workflows_dir。
"""

import os
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable

from .corrupt_evidence import write_reason
from .error_catalog import user_message
from .tool_results import tool_success

logger = logging.getLogger(__name__)


def _backup_corrupt_workflow(path: str) -> Optional[str]:
    """把损坏的工作流文件改名成 ``*.corrupt-时间戳``，返回备份路径（失败 None）。"""
    backup = f"{path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}"
    try:
        os.replace(path, backup)
    except OSError as exc:
        logger.error("工作流文件损坏备份失败：%s（%s）", path, exc)
        return None
    return backup


@dataclass
class WorkflowStep:
    """工作流的一个步骤。"""
    step_id: int
    tool_name: str
    params: Dict[str, Any] = field(default_factory=dict)
    description: str = ""
    timeout: int = 30
    retry_on_failure: bool = True
    max_retries: int = 2
    condition: str = ""  # 条件表达式，为空则总是执行
    expected_result: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowStep":
        return cls(
            step_id=data.get("step_id", 0),
            tool_name=data.get("tool_name", ""),
            params=data.get("params", {}),
            description=data.get("description", ""),
            timeout=data.get("timeout", 30),
            retry_on_failure=data.get("retry_on_failure", True),
            max_retries=data.get("max_retries", 2),
            condition=data.get("condition", ""),
            expected_result=data.get("expected_result", ""),
        )


@dataclass
class Workflow:
    """一个工作流定义。"""
    name: str
    description: str = ""
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    steps: List[WorkflowStep] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)  # 工作流参数定义
    author: str = "mbdsdr"
    source: str = "user"  # user / ai_evolved（不再存在内置 preset）
    version: int = 1
    usage_count: int = 0
    success_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Workflow":
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=data.get("category", "general"),
            tags=data.get("tags", []),
            steps=[WorkflowStep.from_dict(s) for s in data.get("steps", [])],
            parameters=data.get("parameters", {}),
            author=data.get("author", "mbdsdr"),
            source=data.get("source", "user"),
            version=data.get("version", 1),
            usage_count=data.get("usage_count", 0),
            success_count=data.get("success_count", 0),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", time.time()),
            metadata=data.get("metadata", {}),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "tags": self.tags,
            "steps": [
                {
                    "step_id": s.step_id,
                    "tool_name": s.tool_name,
                    "params": s.params,
                    "param_templates": {},
                    "expected_result": s.expected_result,
                    "description": s.description,
                    "timeout": s.timeout,
                    "retry_on_failure": s.retry_on_failure,
                    "max_retries": s.max_retries,
                    "condition": s.condition,
                }
                for s in self.steps
            ],
            "parameters": self.parameters,
            "author": self.author,
            "source": self.source,
            "version": self.version,
            "usage_count": self.usage_count,
            "success_count": self.success_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass
class WorkflowResult:
    """工作流执行结果。"""
    workflow_name: str
    success: bool
    total_steps: int
    completed_steps: int
    failed_step: Optional[int] = None
    error: str = ""
    error_code: str = ""
    step_results: List[Dict[str, Any]] = field(default_factory=list)
    execution_time_ms: float = 0.0
    outputs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "success": self.success,
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "failed_step": self.failed_step,
            "error": self.error,
            "error_code": self.error_code,
            "step_results": self.step_results,
            "execution_time_ms": round(self.execution_time_ms, 1),
            "outputs": self.outputs,
        }


class WorkflowEngine:
    """
    工作流引擎。

    管理工作流的注册、加载、执行。
    工具调用通过 tool_executor 回调函数执行（由外部 MCP 客户端提供）。
    """

    def __init__(self, workflows_dir: str = "~/.mbdsdr/workflows"):
        self.workflows_dir = os.path.expanduser(workflows_dir)
        self.workflows: Dict[str, Workflow] = {}
        self.tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None
        #: 加载失败的人话说明（None = 正常）。目前没有独立界面消费，
        #: 完整提示接线等 main_window.py 那一笔。
        self.last_error: Optional[str] = None
        #: 文件名 stem（去掉 .json）→ 不能同名的原因：损坏被跳过、或读不了。
        #: save_workflow 遇到这些名字一律拒绝，避免把用户原来那份顶掉。
        self._blocked_names: Dict[str, str] = {}
        self._load_errors: List[str] = []
        os.makedirs(self.workflows_dir, exist_ok=True)
        self._load_user_workflows()

    # ── 用户工作流加载/保存 ─────────────────────────────

    def _note_error(self, message: str) -> None:
        """追加一条加载错误；``last_error`` 始终是全部错误的合并文本。"""
        self._load_errors.append(message)
        self.last_error = "；".join(self._load_errors)
        logger.error(message)

    def _mark_corrupt_file(self, path: str, stem: str, reason: str) -> None:
        """单个工作流文件损坏：先备份原件，再拒绝同名覆盖。"""
        backup = _backup_corrupt_workflow(path)
        if backup:
            write_reason(backup, f"{path} 加载失败：{reason}", logger)
            self._blocked_names[stem] = f"原文件已损坏，已备份为 {backup}"
            self._note_error(
                f"工作流文件损坏（{reason}）：{path}；原件已备份为 {backup}，"
                "同名保存会被拒绝（请改名新建，或先处理备份）")
        else:
            self._blocked_names[stem] = "原文件已损坏且备份失败（文件保留在原位）"
            self._note_error(
                f"工作流文件损坏（{reason}）：{path}；备份失败，原文件保留未动，"
                "同名保存会被拒绝")

    def _load_user_workflows(self):
        """从 workflows_dir 加载用户/AI 自定义工作流（引擎不内置任何流程）。

        单个文件坏了不拖垮其它工作流，但坏文件必须留证据：先备份原件，再把
        名字记进 ``_blocked_names``——同名保存等于把用户原来那份删掉。
        """
        if not os.path.exists(self.workflows_dir):
            return
        try:
            names = sorted(os.listdir(self.workflows_dir))
        except OSError as exc:
            self._note_error(
                f"工作流目录读取失败（{exc.__class__.__name__}）："
                f"{self.workflows_dir}；本次按无用户工作流启动")
            return
        # 先登记「只剩损坏备份、没有正式文件」的名字：重启后仍拒绝同名覆盖，
        # 否则用户下次启动就能无声地把备份对应的那个名字重新占掉。
        for fname in names:
            if fname.endswith(".reason.txt"):
                continue
            stem, sep, _stamp = fname.partition(".json.corrupt-")
            if sep:
                backup = os.path.join(self.workflows_dir, fname)
                self._blocked_names.setdefault(
                    stem, f"原文件已损坏，已备份为 {backup}")
        for fname in names:
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.workflows_dir, fname)
            stem = fname[:-len(".json")]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except OSError as exc:
                self._blocked_names[stem] = (
                    f"读取失败（{exc.__class__.__name__}），文件保留在原位")
                self._note_error(
                    f"工作流 {fname} 读取失败（{exc.__class__.__name__}）：{path}；"
                    "未改动该文件，也拒绝用同名新工作流覆盖")
                continue
            except ValueError as exc:
                self._mark_corrupt_file(
                    path, stem, f"解析失败（{exc.__class__.__name__}: {exc}）")
                continue
            try:
                wf = Workflow.from_dict(data)
                if not isinstance(wf.name, str) or not wf.name.strip():
                    raise ValueError("缺少 name")
            except (TypeError, KeyError, ValueError, AttributeError) as exc:
                self._mark_corrupt_file(
                    path, stem, f"条目非法（{exc.__class__.__name__}: {exc}）")
                continue
            if wf.source not in ("user", "ai_evolved"):
                wf.source = "user"
            self.workflows[wf.name] = wf
            # 正式文件能读且合法：这个名字可以被正常覆盖（备份另有归属）
            self._blocked_names.pop(stem, None)

    def save_workflow(self, workflow: Workflow) -> bool:
        """保存工作流到文件。同名目标不安全时拒绝，返回是否真的写盘。

        触发语义是「用户已经有一份 foo，我要写新的 foo」：若原文件损坏被
        跳过，写回 ``foo.json`` 等于把用户原来那份删掉换成新的，而界面只认
        名字，用户看不出区别。所以拒绝，让用户改名新建或先处理备份。
        """
        stem = workflow.name
        if not isinstance(stem, str) or not stem.strip():
            self.last_error = "工作流未保存：名称为空，无法确定文件路径"
            logger.error(self.last_error)
            return False
        blocked_reason = self._blocked_names.get(stem)
        if blocked_reason:
            self.last_error = (
                f"工作流「{stem}」未保存：{blocked_reason}；"
                "同名覆盖会顶替用户原来那份，已拒绝。请换一个名字新建，"
                "或先手工处理备份。")
            logger.error(self.last_error)
            return False
        workflow.updated_at = time.time()
        path = os.path.join(self.workflows_dir, f"{stem}.json")
        tmp_path = path + ".tmp"
        try:
            # 先写临时文件再原子替换：写到一半失败（磁盘满 / 被占用）时，
            # 用户原来那份不会被截断成半截 JSON。
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(workflow.to_dict(), f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except OSError as exc:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError as cleanup_exc:
                logger.warning("工作流临时文件清理失败：%s（%s）",
                               tmp_path, cleanup_exc)
            self.last_error = (
                f"工作流「{stem}」写入失败（{exc.__class__.__name__}）：{path}；"
                "本次未写入，内存中也未登记")
            logger.error(self.last_error)
            return False
        # 先落盘再登记：写失败的版本不能留在内存里冒充「已保存」
        self.workflows[stem] = workflow
        return True

    # ── 工具执行器 ──────────────────────────────────────

    def set_tool_executor(self, executor: Callable[[str, Dict[str, Any]], Any]):
        """设置工具执行器（MCP 客户端的调用函数）。"""
        self.tool_executor = executor

    # ── 参数模板解析 ────────────────────────────────────

    def _lookup_var(self, var_name: str, context: Dict[str, Any]) -> Any:
        """按点分路径从 context 查找变量，如 'step_1.result.freq'。"""
        parts = var_name.split(".")
        val = context
        for part in parts:
            if isinstance(val, dict):
                if part not in val:
                    return None
                val = val[part]
            elif hasattr(val, part):
                val = getattr(val, part)
            else:
                return None
        return val

    def _render_string(self, text: str, context: Dict[str, Any]) -> str:
        """将字符串中的 {{variable}} 替换为 context 中的值。"""
        def replace_var(match):
            var_name = match.group(1)
            val = self._lookup_var(var_name, context)
            if val is None:
                return match.group(0)  # 未找到则保留原模板
            return str(val)
        return re.sub(r'\{\{([\w.]+)\}\}', replace_var, text)

    def _resolve_value(self, value: Any, context: Dict[str, Any]) -> Any:
        """递归解析任意值中的 {{variable}} 模板。"""
        if isinstance(value, str):
            # 整串就是一个 {{var}} 模板：直接返回 context 原始值，不被 str() 化。
            # 否则 {"rssi_by_azimuth": "{{rssi_by_azimuth}}"} 会把 dict 渲染成 repr 字符串，
            # 下游工具（如 gnss_direction_find）拿不到映射。
            m = re.fullmatch(r'\s*\{\{([\w.]+)\}\}\s*', value)
            if m:
                val = self._lookup_var(m.group(1), context)
                if val is not None:
                    return val
            rendered = self._render_string(value, context)
            # 纯数字字符串自动转 int/float
            if rendered.lstrip('-').isdigit():
                return int(rendered)
            try:
                return float(rendered)
            except ValueError:
                return rendered
        elif isinstance(value, dict):
            return {k: self._resolve_value(v, context) for k, v in value.items()}
        elif isinstance(value, list):
            return [self._resolve_value(v, context) for v in value]
        return value

    def _resolve_params(self, params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
        """解析参数模板（{{variable}}），支持嵌套 dict/list 和点分路径。"""
        return {k: self._resolve_value(v, context) for k, v in params.items()}

    def _check_condition(self, condition: str, context: Dict[str, Any]) -> bool:
        """检查条件表达式（简单的变量存在性检查）。"""
        if not condition:
            return True
        # 简单条件：变量名存在且非空/非False
        try:
            return bool(context.get(condition, False))
        except Exception:
            return True

    # ── 执行工作流 ──────────────────────────────────────

    def execute(self, workflow_name: str, parameters: Dict[str, Any] = None) -> WorkflowResult:
        """
        执行工作流。

        workflow_name: 工作流名称
        parameters: 工作流参数（覆盖默认值）

        返回 WorkflowResult。
        """
        start_time = time.time()
        workflow = self.workflows.get(workflow_name)
        if not workflow:
            return WorkflowResult(
                workflow_name=workflow_name,
                success=False,
                total_steps=0,
                completed_steps=0,
                error=f"工作流 '{workflow_name}' 不存在",
            )

        if not self.tool_executor:
            return WorkflowResult(
                workflow_name=workflow_name,
                success=False,
                total_steps=len(workflow.steps),
                completed_steps=0,
                error=user_message("workflow_executor_missing"),
                error_code="workflow_executor_missing",
            )

        # 初始化上下文
        context = {}
        for param_name, param_def in workflow.parameters.items():
            context[param_name] = param_def.get("default", "")
        if parameters:
            context.update(parameters)

        step_results = []
        completed = 0
        failed_step = None
        error = ""

        for step in workflow.steps:
            # 检查条件
            if not self._check_condition(step.condition, context):
                step_results.append({
                    "step_id": step.step_id,
                    "tool": step.tool_name,
                    "skipped": True,
                    "reason": f"条件不满足: {step.condition}",
                })
                continue

            # 解析参数
            params = self._resolve_params(step.params, context)

            # 执行（带重试）
            success = False
            result = None
            last_error = ""
            retries = step.max_retries if step.retry_on_failure else 0

            for attempt in range(retries + 1):
                try:
                    result = self.tool_executor(step.tool_name, params)
                    success = True
                    break
                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                    if attempt < retries:
                        time.sleep(0.5 * (attempt + 1))

            if not success:
                failed_step = step.step_id
                error = f"步骤 {step.step_id} ({step.tool_name}) 失败: {last_error}"
                step_results.append({
                    "step_id": step.step_id,
                    "tool": step.tool_name,
                    "success": False,
                    "error": last_error,
                    "attempts": retries + 1,
                })
                break

            completed += 1
            step_results.append({
                "step_id": step.step_id,
                "tool": step.tool_name,
                "success": True,   # 执行层：tool_executor 没抛异常
                # 工具自己的成败（ToolResult.success）随条目落账，投影层不再
                # 靠结果文本猜；读不到 success 时为 None（未知，按执行成功渲染）。
                "tool_success": tool_success(result),
                "result": (result.content if hasattr(result, "content") else str(result))[:500] if result else "",
            })

            # 将结果存入上下文（供后续步骤使用）
            context[f"step_{step.step_id}_result"] = result
            # D6 接真实：优先用 ToolResult.data 结构化 sidecar 字典回填（关键工具把
            # recording_path / interference_freq_hz / doppler_freq / rssi_by_azimuth 等
            # 吐在 .data）；否则把 .content 当 JSON 解析；再否则按裸 dict 处理。
            result_dict = None
            sidecar = getattr(result, "data", None)
            if isinstance(sidecar, dict) and sidecar:
                result_dict = sidecar
            elif hasattr(result, "content") and result.content:
                try:
                    result_dict = json.loads(result.content) if isinstance(result.content, str) else result.content
                except (json.JSONDecodeError, TypeError):
                    result_dict = None
            elif isinstance(result, dict):
                result_dict = result
            if isinstance(result_dict, dict):
                # 整份结果存到 step_N，支持 {{step_1.freq}} 点分访问
                context[f"step_{step.step_id}"] = result_dict
                for k, v in result_dict.items():
                    # 顶层标量/字典/列表都提升到 context：预设工作流用裸 {{recording_path}}、
                    # {{rssi_by_azimuth}} 等引用上一步输出（dict/list 不再只落在 step_N 里）。
                    if isinstance(v, (str, int, float, bool, list, dict)):
                        context[k] = v

        # 更新统计
        workflow.usage_count += 1
        if failed_step is None:
            workflow.success_count += 1

        execution_time = (time.time() - start_time) * 1000

        # outputs 必须 JSON 可序列化：executor 返回的 ToolResult（鸭子类型：有 .success/.content）
        # 不能直接进 outputs，换成摘要 dict；其余标量/dict/list 原样保留。
        safe_outputs = {}
        for k, v in context.items():
            if hasattr(v, "success") and hasattr(v, "content"):
                safe_outputs[k] = {"success": bool(v.success),
                                   "content": str(v.content)[:300]}
            else:
                safe_outputs[k] = v

        return WorkflowResult(
            workflow_name=workflow_name,
            success=failed_step is None,
            total_steps=len(workflow.steps),
            completed_steps=completed,
            failed_step=failed_step,
            error=error,
            step_results=step_results,
            execution_time_ms=execution_time,
            outputs=safe_outputs,
        )

    # ── 查询与统计 ──────────────────────────────────────

    def list_workflows(self, category: str = None) -> List[Dict[str, Any]]:
        """列出所有工作流。"""
        workflows = list(self.workflows.values())
        if category:
            workflows = [w for w in workflows if w.category == category]
        return [
            {
                "name": w.name,
                "description": w.description,
                "category": w.category,
                "tags": w.tags,
                "steps": len(w.steps),
                "usage_count": w.usage_count,
                "success_count": w.success_count,
                "source": w.source,
                "version": w.version,
            }
            for w in workflows
        ]

    def get_stats(self) -> Dict[str, Any]:
        """获取工作流引擎统计。"""
        total = len(self.workflows)
        total_usage = sum(w.usage_count for w in self.workflows.values())
        total_success = sum(w.success_count for w in self.workflows.values())
        by_category = {}
        for w in self.workflows.values():
            by_category[w.category] = by_category.get(w.category, 0) + 1
        return {
            "total_workflows": total,
            "total_usage": total_usage,
            "total_success": total_success,
            "success_rate": round(total_success / max(1, total_usage), 4),
            "by_category": by_category,
            "workflows_dir": self.workflows_dir,
        }

    def get_status_text(self) -> str:
        """获取人类可读的工作流引擎状态。"""
        stats = self.get_stats()
        lines = [
            "=== MBDSDR 工作流引擎 ===",
            f"工作流总数: {stats['total_workflows']}",
            f"总执行次数: {stats['total_usage']}",
            f"成功次数: {stats['total_success']} (成功率 {stats['success_rate']:.1%})",
            f"按类别:",
        ]
        for cat, count in stats["by_category"].items():
            lines.append(f"  - {cat}: {count} 个")
        lines.append("\n可用工作流:")
        for wf in self.list_workflows():
            lines.append(f"  - [{wf['category']}] {wf['name']}: {wf['description'][:50]}")
        return "\n".join(lines)
