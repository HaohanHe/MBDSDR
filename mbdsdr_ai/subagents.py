# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - Subagents 子代理框架
=======================================
Subagents：子代理框架，让主 Agent 可以创建专门的子 Agent 处理特定任务。

对照白皮书第四章 4.5 Subagents 子代理框架。

核心概念：
- Subagent：子代理，有自己的工具集、上下文、系统提示；注入 model_manager
  后 execute_task 跑真 LLM 循环（隔离上下文 + 工具白名单过滤 + max_steps
  截断诚实返回 partial，见 execute_task 文档）
- SubagentManager：子代理管理器，创建/管理/销毁子代理
- SubagentTask：子代理任务，包含目标、输入、超时、回调

内置子代理类型：
- SpectrumAnalyzer：频谱分析专家，专门做频谱分析、信号检测、调制识别
- SignalDecoder：信号解码专家，专门做各种数字模式解码
- SatelliteTracker：卫星跟踪专家，专门做卫星轨道计算、多普勒修正、过境预测
- InterferenceHunter：干扰源定位专家，专门做干扰检测、测向、定位
- BasebandRecorder：基带录制专家，专门做录制、回放、格式转换
- HardwareController：硬件控制专家，专门做设备管理、固件升级、OTA
- CodeEvolver：代码进化专家，专门做自编程、算法优化、代码审查
"""

import json
import time

import threading
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable
from enum import Enum

from .context_manager import estimate_message_tokens


class SubagentStatus(str, Enum):
    """子代理状态。"""
    IDLE = "idle"  # 空闲
    RUNNING = "running"  # 运行中
    WAITING = "waiting"  # 等待输入
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消
    TIMEOUT = "timeout"  # 超时


@dataclass
class SubagentResult:
    """子代理执行结果。

    字段分两代（旧字段语义不变，既有调用方零改可用）：
    - 旧字段：success / output / data / error / steps_executed / tools_called ...；
    - 新字段（2026-10-14 subagent 隔离执行，kimi-code 审计 Top-4 落地面）：
      - status："" / "ok" / "truncated" / "error"——truncated=达到 max_steps
        上限的 partial 结果，error=model_manager 缺失或 LLM 调用失败；
      - steps_used：实际消耗的 LLM 循环步数（与 steps_executed 同值镜像）；
      - tools_used：实际执行过的工具名（与 tools_called 同值镜像）；
      - truncated：是否因 max_steps 上限被截断（partial 结果必须可见）。
    """
    success: bool
    output: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    execution_time: float = 0.0
    steps_executed: int = 0
    tools_called: List[str] = field(default_factory=list)
    subagent_id: str = ""
    task_id: str = ""
    # ── LLM 循环新增可观测字段 ──────────────────────────
    status: str = ""          # "ok" / "truncated" / "error"
    steps_used: int = 0       # 实际消耗的 LLM 循环步数
    tools_used: List[str] = field(default_factory=list)  # 实际执行过的工具名
    truncated: bool = False   # 是否因 max_steps 上限被截断（partial 结果）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "data": self.data,
            "error": self.error,
            "execution_time": self.execution_time,
            "steps_executed": self.steps_executed,
            "tools_called": self.tools_called,
            "subagent_id": self.subagent_id,
            "task_id": self.task_id,
            "status": self.status,
            "steps_used": self.steps_used,
            "tools_used": self.tools_used,
            "truncated": self.truncated,
        }


@dataclass
class SubagentTask:
    """子代理任务。"""
    task_id: str
    goal: str  # 任务目标
    input_data: Dict[str, Any] = field(default_factory=dict)  # 输入数据
    timeout_s: float = 120.0  # 超时时间
    max_steps: int = 8  # LLM 循环步数上限（≤0 = 用 Subagent 自身默认）
    callback: Optional[Callable[[SubagentResult], None]] = None  # 完成回调
    status: SubagentStatus = SubagentStatus.IDLE
    result: Optional[SubagentResult] = None
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0


# ═══════════════════════════════════════════════════════
# 子代理系统提示模板
# ═══════════════════════════════════════════════════════

SUBAGENT_PROMPTS = {
    "spectrum_analyzer": """你是 MBDSDR 的频谱分析专家子代理。
你的专长：
- 频谱分析：FFT、峰值检测、中心频点估计、噪声底测量
- 信号检测：找台、信号分类、带宽估计、占用度测量
- 调制识别：AM/FM/SSB/CW/ASK/FSK/PSK/QAM 等调制方式识别
- 干扰分析：干扰检测、干扰类型分类、干扰强度测量
- 频谱图解读：瀑布图分析、时频特征提取、跳频图案识别

工作原则：
- 只做频谱分析相关的任务，不做解码、录制、硬件控制
- 使用提供的工具完成任务，每一步都要说明在做什么
- 如果工具不够用，明确说明需要什么工具
- 输出要简洁、专业、可操作
""",

    "signal_decoder": """你是 MBDSDR 的信号解码专家子代理。
你的专长：
- 数字模式解码：FT8、WSPR、Olivia、M17、APRS、ADS-B、LoRa、TPMS
- 模拟模式解码：NOAA APT、SSTV、Fax、DRM
- 卫星信号解码：NOAA、METEOR、风云、ISS SSTV
- 协议分析：帧结构分析、CRC 校验、纠错解码
- 录制文件分析：从 cf32/wav/csv 文件中提取和解码信号

工作原则：
- 只做信号解码相关的任务
- 解码前先分析信号特征（调制方式、波特率、带宽）
- 解码失败时给出可能的原因和改进建议
- 输出解码结果和元数据
""",

    "satellite_tracker": """你是 MBDSDR 的卫星跟踪专家子代理。
你的专长：
- 卫星轨道计算：TLE 解析、SGP4/SDP4 传播、位置速度计算
- 过境预测：可见卫星列表、过境时间、最大仰角、方位角
- 多普勒修正：下行频率多普勒计算、自动频率跟踪
- 卫星调度：多卫星接收优先级、录制计划、天线指向
- 6DOF 位姿融合：IMU+磁力计+GNSS 融合，AR 卫星投影

工作原则：
- 只做卫星跟踪和空间计算相关的任务
- 使用精确的时间和位置数据
- 多普勒计算要考虑卫星速度和观测点位置
- 输出要包含精确的数值和时间戳
""",

    "interference_hunter": """你是 MBDSDR 的干扰源定位专家子代理。
你的专长：
- 干扰检测：宽带扫描、异常信号检测、干扰强度测量
- 干扰分类：同频干扰、邻道干扰、互调干扰、杂散干扰
- 测向定位：TDOA、RSSI、空间谱估计、多站交汇
- 干扰源追踪：移动测向、热点图、轨迹分析
- 干扰缓解：频率建议、滤波参数、天线调整

工作原则：
- 只做干扰检测和定位相关的任务
- 测量要精确，数据要可复现
- 定位结果要给出置信度和误差范围
- 输出要包含可操作的缓解建议
""",

    "baseband_recorder": """你是 MBDSDR 的基带录制专家子代理。
你的专长：
- 基带录制：cf32/cs16/wav/csv 格式录制、定时录制、触发录制
- 录制管理：文件命名、元数据、sidecar JSON、磁盘空间管理
- 格式转换：cf32↔wav↔cs16↔csv、采样率转换、抽取/插值
- 回放分析：录制文件回放、频谱分析、信号提取
- 数据集构建：标注、切片、训练数据准备

工作原则：
- 只做基带录制和文件管理相关的任务
- 录制前检查磁盘空间和采样率
- 所有录制都要有完整的元数据
- 输出文件路径和录制参数
""",

    "hardware_controller": """你是 MBDSDR 的硬件控制专家子代理。
你的专长：
- 设备管理：SDR 设备枚举、连接、断开、状态监控
- 参数控制：频率、采样率、增益、带宽、解调模式设置
- 固件管理：固件版本查询、OTA 升级、固件回滚
- 传感器读取：GPS、IMU、磁力计、温度、电压
- 多设备协同：异构双前端、设备切换、同步接收

工作原则：
- 只做硬件控制相关的任务
- 操作前检查设备连接状态
- 危险操作（固件升级、频率切换）要确认
- 输出操作结果和设备状态
""",

    "code_evolver": """你是 MBDSDR 的代码进化专家子代理。
你的专长：
- 自编程：根据需求生成/修改代码、算法优化、性能调优
- 代码审查：Bug 检测、安全审计、风格检查、复杂度分析
- 算法设计：DSP 算法、解调算法、解码算法、AI 模型设计
- 测试验证：单元测试、集成测试、性能测试、回归测试
- 文档生成：API 文档、使用说明、设计文档、变更日志

工作原则：
- 只做代码和算法相关的任务
- 修改代码前先备份（守护者快照）
- 所有修改都要经过测试验证
- 输出修改内容、测试结果、风险评估
""",
}


# ═══════════════════════════════════════════════════════
# 子代理类
# ═══════════════════════════════════════════════════════

class Subagent:
    """
    子代理基类。

    每个子代理有自己的：
    - 系统提示（专业角色）
    - 工具集（只能访问特定工具）
    - 上下文（独立的对话历史，与主 agent / 其它子代理零共享）
    - 任务队列

    执行模型（2026-10-14 起）：注入 model_manager 后 execute_task 运行真 LLM
    循环——self.context 维护本任务独立对话，多步「LLM → tool_calls → tool
    消息回灌 → LLM」直到无工具调用或达到 max_steps；工具调用只允许
    allowed_tools 之内（越界本地拒绝）；截断/失败均诚实标注（见
    :class:`SubagentResult` 的 status/steps_used/tools_used/truncated）。
    """

    def __init__(
        self,
        subagent_id: str,
        agent_type: str,
        tool_registry,  # 主 Agent 的工具注册表（子代理只能访问部分工具）
        model_manager=None,  # 模型管理器
        allowed_tools: List[str] = None,  # 允许使用的工具列表（None=全部）
        system_prompt: str = None,
        max_context_tokens: int = 4096,
        max_steps: int = 8,  # LLM 循环步数上限
    ):
        self.subagent_id = subagent_id
        self.agent_type = agent_type
        self.tool_registry = tool_registry
        self.model_manager = model_manager
        self.allowed_tools = set(allowed_tools) if allowed_tools else None
        self.system_prompt = system_prompt or SUBAGENT_PROMPTS.get(agent_type, "你是 MBDSDR 的子代理。")
        self.max_context_tokens = max_context_tokens
        # LLM 循环步数上限；非法值回退默认 8（execute_task 显式参数优先）
        self.max_steps = int(max_steps) if max_steps and int(max_steps) > 0 else 8

        self.status = SubagentStatus.IDLE
        self.context: List[Dict[str, str]] = []  # 对话历史
        self.task_history: List[SubagentTask] = []
        self.current_task: Optional[SubagentTask] = None
        self.total_tasks_completed = 0
        self.total_tasks_failed = 0
        self.created_at = time.time()
        self._lock = threading.Lock()

    def can_use_tool(self, tool_name: str) -> bool:
        """检查子代理是否可以使用指定工具（语义：allowed_tools=None 即全部）。

        注意：LLM 循环内部用 :meth:`_effective_allowed_names`（None 时额外排除
        subagent_* 防递归），本方法保持原公开语义不变。
        """
        if self.allowed_tools is None:
            return True
        return tool_name in self.allowed_tools

    def execute_task(self, task: SubagentTask, max_steps: int = None) -> SubagentResult:
        """
        执行任务（同步，真 LLM 循环）。

        对齐 kimi subagentService 的隔离语义（最小同步版，不含异步/stop/wait）：
        1. **隔离上下文**：self.context 重置为本任务独立对话——system = 子代理
           类型提示词 + 本次任务描述 + 执行约定，user = 任务目标 + 输入数据；
           与主 agent 的 ContextManager、其它子代理、本代理的上个任务零共享；
        2. **多步循环**：「LLM → tool_calls → tool 消息回灌 → LLM」直到无工具
           调用或达到 max_steps（优先级：本参数 > task.max_steps >
           self.max_steps，默认 8）；
        3. **工具过滤**：只允许 allowed_tools 之内的工具，越界调用在本地被拒
           （回灌拒绝说明，不触达 registry、不上抛崩溃）；
        4. **结果回收**：最终文本作为 output 单条回主 agent，SubagentResult
           带 steps_used / tools_used / truncated / status 可观测字段；
        5. **诚实语义**：model_manager 缺失或 LLM 调用失败 → status=error +
           技术细节，output 为空，绝不伪造输出；达到 max_steps 仍在索取工具
           → status=truncated + truncated=True + 带 [已截断] 标记的 partial
           结果，绝不伪装成完成。
        """
        start_time = time.time()
        self.status = SubagentStatus.RUNNING
        self.current_task = task
        task.started_at = start_time

        result = SubagentResult(
            success=False,  # 先设为 False，成功后改为 True
            subagent_id=self.subagent_id,
            task_id=task.task_id,
        )

        try:
            if self.model_manager is None:
                # 诚实文化：没有 LLM 循环能力就不保留"直接打工具"旁路——
                # 无推理的动作不得冒充自主执行（审计 F5 的修复前提）
                raise RuntimeError(
                    f"子代理 {self.subagent_id} 未注入 model_manager，无法运行 "
                    f"LL 循环；按诚实原则拒绝伪造执行结果")

            limit = self._resolve_max_steps(task, max_steps)
            output, data, steps_used, tools_used, truncated, error = \
                self._run_llm_loop(task, limit)

            result.output = output
            result.data = data
            result.steps_used = steps_used
            result.tools_used = list(tools_used)
            result.truncated = truncated
            # 旧字段镜像（同值），既有调用方零改
            result.steps_executed = steps_used
            result.tools_called = list(tools_used)
            result.error = error

            if truncated:
                # 截断：partial 结果 + 显式标记，不当成完成
                # （error 里同步带 max_steps_exhausted 说明）
                result.success = False
                result.status = "truncated"
                self.total_tasks_failed += 1
                task.status = SubagentStatus.FAILED
            elif error:
                # LLM 调用失败：技术细节进 error，绝不伪造 output
                result.success = False
                result.status = "error"
                self.total_tasks_failed += 1
                task.status = SubagentStatus.FAILED
            else:
                result.success = True
                result.status = "ok"
                self.total_tasks_completed += 1
                task.status = SubagentStatus.COMPLETED

        except Exception as e:
            result.success = False
            result.status = "error"
            result.error = f"{type(e).__name__}: {str(e)}"
            result.output = ""
            self.total_tasks_failed += 1
            task.status = SubagentStatus.FAILED
            traceback.print_exc()

        result.execution_time = time.time() - start_time
        task.completed_at = time.time()
        task.result = result
        self.status = SubagentStatus.IDLE
        self.current_task = None
        self.task_history.append(task)

        # 执行回调
        if task.callback:
            try:
                task.callback(result)
            except Exception:
                pass

        return result

    # ── LLM 循环内部实现 ────────────────────────────────

    def _resolve_max_steps(self, task: SubagentTask, max_steps: Optional[int]) -> int:
        """解析循环步数上限：execute_task 参数 > task.max_steps > self.max_steps。"""
        for cand in (max_steps, getattr(task, "max_steps", None), self.max_steps):
            if cand and int(cand) > 0:
                return int(cand)
        return 8

    def _effective_allowed_names(self) -> set:
        """循环期有效工具白名单。

        allowed_tools 非空 = 该集合；为 None = 全部注册工具但排除 subagent_*
        （防子代理递归调 subagent_execute 造成无限自激——最小同步实现里分层
        递归会指数放大 LLM 调用）。显式列入 allowed_tools 的 subagent_* 仍
        可用（用户显式授权嵌套子代理，递归风险自担）。
        """
        if self.allowed_tools is not None:
            return set(self.allowed_tools)
        if self.tool_registry is None:
            return set()
        try:
            names = set(self.tool_registry.get_tool_names())
        except Exception:
            return set()
        return {n for n in names if not n.startswith("subagent_")}

    def _tool_schemas(self, visible: set) -> List[Dict[str, Any]]:
        """取白名单内工具的 OpenAI function calling 定义（异常/空集 → 空列表）。"""
        if not visible or self.tool_registry is None:
            return []
        try:
            defs = self.tool_registry.get_tool_definitions() or []
        except Exception:
            return []
        schemas = []
        for d in defs:
            try:
                if d.get("function", {}).get("name") in visible:
                    schemas.append(d)
            except Exception:
                continue
        return schemas

    def _build_task_context(self, task: SubagentTask, limit: int,
                            visible: set) -> List[Dict[str, Any]]:
        """构成本任务独立对话（每任务重置 = 上下文隔离，不继承历史任务）。"""
        tool_view = "、".join(sorted(visible)) if visible else "（无可用工具）"
        input_part = ""
        if task.input_data:
            try:
                input_part = ("\n输入数据: "
                              + json.dumps(task.input_data, ensure_ascii=False,
                                           default=str))
            except Exception:
                input_part = f"\n输入数据: {task.input_data!r}"
        system_content = (
            f"{self.system_prompt}\n\n"
            f"【本次任务】\n目标: {task.goal}{input_part}\n\n"
            f"【执行约定】\n"
            f"- 你只能调用白名单内的工具: {tool_view}\n"
            f"- 工具结果会作为 tool 消息回灌给你，可多步推进，直到能给出最终结论\n"
            f"- 步数上限 {limit} 步；超出后你的部分结论会被标记 truncated 返回，"
            f"不得把没做的测量写成已完成\n"
            f"- 缺工具/缺数据时诚实说明缺口，不得编造数值"
        )
        user_content = f"任务目标: {task.goal}{input_part}"
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    def _trim_context(self) -> bool:
        """超 max_context_tokens 预算时，从最老的完整轮次成对摘除上下文。

        轮次 = 一条 assistant（含 tool_calls）+ 紧随的若干 tool 消息，必须成
        对摘除（半对会破坏 OpenAI tool_call/tool 配对约定）；system 与首条
        user（任务目标）永保留。返回是否有摘除（调用方写入 data.context_trimmed）。
        """
        def _total() -> int:
            try:
                return sum(estimate_message_tokens(m) for m in self.context)
            except Exception:
                return 0

        if _total() <= self.max_context_tokens:
            return False
        trimmed = False
        while _total() > self.max_context_tokens and len(self.context) > 2:
            idx = next((i for i in range(2, len(self.context))
                        if self.context[i].get("role") == "assistant"), None)
            if idx is None:
                break
            end = idx + 1
            while end < len(self.context) and self.context[end].get("role") == "tool":
                end += 1
            del self.context[idx:end]
            trimmed = True
        return trimmed

    def _run_llm_loop(self, task: SubagentTask, limit: int):
        """真 LLM 循环：LLM → tool_calls → tool 回灌 → LLM，直到无调用或超限。

        返回 (output, data, steps_used, tools_used, truncated, error)：
        - error 非空 = LLM 调用失败（技术细节，调用方按 status=error 处理）；
        - truncated=True = 达到 limit 步仍在索取工具，output 为带标记 partial；
        - 其余 = 正常完成，output 为最终文本结论。
        """
        visible = self._effective_allowed_names()
        schemas = self._tool_schemas(visible)
        # 每任务重置上下文：隔离语义（与主 agent / 其它子代理 / 上个任务零共享）
        self.context = self._build_task_context(task, limit, visible)

        tools_used: List[str] = []
        trimmed_seen = False
        last_content = ""

        for step in range(1, limit + 1):
            if self._trim_context():
                trimmed_seen = True

            try:
                response = self.model_manager.chat(
                    messages=self.context, tools=schemas or None,
                    tool_choice="auto")
            except Exception as e:
                return "", {}, step - 1, tools_used, False, (
                    f"model_manager.chat 调用异常: {type(e).__name__}: {e}")

            if not isinstance(response, dict) or not response.get("success"):
                err = "unknown_error"
                if isinstance(response, dict):
                    err = response.get("error") or err
                return "", {}, step - 1, tools_used, False, (
                    f"模型调用失败: {err}（step {step}/{limit}）")

            content = response.get("content", "") or ""
            tool_calls = list(response.get("tool_calls") or [])

            # 弱模型兜底：原生 tool_calls 为空时从正文提取（只采纳引用真实
            # 工具名的）；stub/异常解析一律按"无工具调用"处理
            if not tool_calls and content and schemas:
                try:
                    parsed = self.model_manager.parse_tool_calls_from_text(
                        content, visible)
                    if parsed:
                        tool_calls = parsed
                except Exception:
                    pass

            if not tool_calls:
                # 无工具调用 = 最终结论（assistant 消息入上下文，独立对话保持完整）
                self.context.append({"role": "assistant", "content": content})
                data = {
                    "agent_type": self.agent_type,
                    "steps_used": step,
                    "tools_used": list(tools_used),
                    "truncated": False,
                    "context_messages": len(self.context),
                    "context_trimmed": trimmed_seen,
                }
                return content, data, step, tools_used, False, ""

            last_content = content
            # 规范化 tool_call id（文本兜底来源可能缺 id）
            for i, tc in enumerate(tool_calls):
                if isinstance(tc, dict) and not tc.get("id"):
                    tc["id"] = f"call_{task.task_id}_{step}_{i}"

            # assistant 消息（含 tool_calls）先入上下文，保持 OpenAI 配对
            self.context.append({
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })

            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn = tc.get("function") or {}
                    tc_id = tc.get("id", "") or ""
                else:
                    fn, tc_id = {}, ""
                name = fn.get("name", "") or ""
                if not name:
                    tool_content = "拒绝：工具调用缺少 function.name，未执行"
                elif self.tool_registry is None:
                    tool_content = (f"拒绝：子代理 {self.subagent_id} 未注入 "
                                    f"tool_registry，工具 {name} 无法执行")
                elif name not in visible:
                    # 工具视图过滤：越界调用本地拒绝，不触达 registry、不上抛
                    tool_content = (
                        f"拒绝：子代理 {self.subagent_id}（{self.agent_type}）"
                        f"无权调用工具 {name}（不在 allowed_tools 白名单），"
                        f"调用未执行。白名单: {sorted(visible)}")
                else:
                    try:
                        res = self.tool_registry.call_from_model(tc)
                        tool_content = getattr(res, "content", None) or str(res)
                    except Exception as e:
                        tool_content = (
                            f"工具执行异常: {type(e).__name__}: {e}"
                            f"（未得到真实结果，不得据此下结论）")
                    tools_used.append(name)
                self.context.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": name,
                    "content": tool_content,
                })

        # 达到 limit 步仍在索取工具：诚实截断（partial 结果 + 显式标记）
        marker = (f"\n\n[已截断] 子代理 {self.subagent_id} 达到 max_steps={limit} "
                  f"步上限，任务未完成；以上为截断前的部分结论，不得视为完成。")
        output = (last_content + marker) if last_content.strip() else (
            f"[已截断] 子代理 {self.subagent_id} 达到 max_steps={limit} 步上限，"
            f"且未产出任何文本结论（已执行工具: {tools_used or '无'}）。")
        data = {
            "agent_type": self.agent_type,
            "steps_used": limit,
            "tools_used": list(tools_used),
            "truncated": True,
            "context_messages": len(self.context),
            "context_trimmed": trimmed_seen,
        }
        return output, data, limit, tools_used, True, (
            f"max_steps_exhausted: 达到 max_steps={limit} 步上限，"
            f"返回截断的部分结果（truncated=True）")

    def get_stats(self) -> Dict[str, Any]:
        """获取子代理统计信息。"""
        return {
            "subagent_id": self.subagent_id,
            "agent_type": self.agent_type,
            "status": self.status.value,
            "total_tasks_completed": self.total_tasks_completed,
            "total_tasks_failed": self.total_tasks_failed,
            "context_size": len(self.context),
            "allowed_tools_count": len(self.allowed_tools) if self.allowed_tools else "all",
            "max_steps": self.max_steps,
            "created_at": self.created_at,
            "uptime_s": time.time() - self.created_at,
        }


# ═══════════════════════════════════════════════════════
# 子代理管理器
# ═══════════════════════════════════════════════════════

class SubagentManager:
    """
    子代理管理器。

    负责：
    - 创建/销毁子代理
    - 任务分配和调度
    - 子代理状态监控
    - 资源管理（并发数限制）
    """

    # 子代理类型对应的默认允许工具
    DEFAULT_ALLOWED_TOOLS = {
        "spectrum_analyzer": [
            "sdr_spectrum_analyze", "sdr_spectrum_zoom", "sdr_spectrum_pan",
            "sdr_spectrum_find_signals", "sdr_spectrum_center_offset",
            "sdr_spectrum_text", "sdr_identify_modulation", "sdr_measure_signal",
            "sdr_detect_fhss", "sdr_iq_correct",
        ],
        "signal_decoder": [
            "sdr_decode_noaa_apt", "sdr_decode_sstv", "sdr_decode_ft8",
            "sdr_decode_aprs", "sdr_decode_adsb", "sdr_analyze_recording",
        ],
        "satellite_tracker": [
            "sdr_satellite_sky_view", "sdr_satellite_doppler",
            "sdr_get_gps", "sdr_get_imu",
        ],
        "interference_hunter": [
            "sdr_spectrum_analyze", "sdr_spectrum_find_signals",
            "sdr_ai_sweep", "sdr_measure_signal", "sdr_detect_fhss",
        ],
        "baseband_recorder": [
            "sdr_record_start", "sdr_record_stop", "sdr_recordings_list",
            "sdr_analyze_recording",
        ],
        "hardware_controller": [
            "sdr_connect", "sdr_disconnect", "sdr_list_devices",
            "sdr_switch_device", "sdr_status", "sdr_set_frequency",
            "sdr_set_sample_rate", "sdr_set_gain", "sdr_set_agc",
            "sdr_set_demod", "sdr_set_bandwidth",
        ],
        "code_evolver": [
            "guardian_snapshot", "guardian_rollback", "guardian_list",
            "evolution_propose", "evolution_evaluate", "evolution_commit",
        ],
    }

    def __init__(self, tool_registry, model_manager=None, max_concurrent: int = 4):
        self.tool_registry = tool_registry
        self.model_manager = model_manager
        self.max_concurrent = max_concurrent
        self.subagents: Dict[str, Subagent] = {}
        self.task_queue: List[SubagentTask] = []
        self._lock = threading.Lock()
        self._task_counter = 0

    def create(
        self,
        agent_type: str,
        subagent_id: str = None,
        allowed_tools: List[str] = None,
        system_prompt: str = None,
        max_steps: int = None,
    ) -> str:
        """
        创建一个子代理。

        返回 subagent_id。

        max_steps: LLM 循环步数上限（None=用 Subagent 默认 8）。
        """
        if subagent_id is None:
            subagent_id = f"subagent_{agent_type}_{int(time.time() * 1000)}_{len(self.subagents)}"

        if allowed_tools is None:
            allowed_tools = self.DEFAULT_ALLOWED_TOOLS.get(agent_type)

        subagent = Subagent(
            subagent_id=subagent_id,
            agent_type=agent_type,
            tool_registry=self.tool_registry,
            model_manager=self.model_manager,
            allowed_tools=allowed_tools,
            system_prompt=system_prompt,
            max_steps=max_steps if max_steps else 8,
        )

        with self._lock:
            self.subagents[subagent_id] = subagent

        return subagent_id

    def destroy(self, subagent_id: str) -> bool:
        """销毁一个子代理。"""
        with self._lock:
            subagent = self.subagents.pop(subagent_id, None)
        return subagent is not None

    def get(self, subagent_id: str) -> Optional[Subagent]:
        """获取子代理。"""
        return self.subagents.get(subagent_id)

    def list_subagents(self) -> List[Dict[str, Any]]:
        """列出所有子代理。"""
        return [sa.get_stats() for sa in self.subagents.values()]

    def submit_task(
        self,
        subagent_id: str,
        goal: str,
        input_data: Dict[str, Any] = None,
        timeout_s: float = 120.0,
        callback: Callable[[SubagentResult], None] = None,
    ) -> str:
        """
        提交任务给子代理（异步）。

        返回 task_id。
        """
        subagent = self.subagents.get(subagent_id)
        if subagent is None:
            raise ValueError(f"子代理不存在: {subagent_id}")

        self._task_counter += 1
        task_id = f"task_{self._task_counter}"

        task = SubagentTask(
            task_id=task_id,
            goal=goal,
            input_data=input_data or {},
            timeout_s=timeout_s,
            callback=callback,
        )

        # 异步执行
        thread = threading.Thread(
            target=subagent.execute_task,
            args=(task,),
            daemon=True,
        )
        thread.start()

        return task_id

    def execute_task(
        self,
        subagent_id: str,
        goal: str,
        input_data: Dict[str, Any] = None,
        timeout_s: float = 120.0,
        max_steps: int = None,
    ) -> SubagentResult:
        """
        执行任务（同步，等待完成）。

        返回 SubagentResult。

        max_steps: LLM 循环步数上限（None=按 task.max_steps/Subagent 默认 8）。
        """
        subagent = self.subagents.get(subagent_id)
        if subagent is None:
            raise ValueError(f"子代理不存在: {subagent_id}")

        self._task_counter += 1
        task_id = f"task_{self._task_counter}"

        task = SubagentTask(
            task_id=task_id,
            goal=goal,
            input_data=input_data or {},
            timeout_s=timeout_s,
        )

        return subagent.execute_task(task, max_steps=max_steps)

    def get_task_result(self, subagent_id: str, task_id: str) -> Optional[SubagentResult]:
        """获取任务结果。"""
        subagent = self.subagents.get(subagent_id)
        if subagent is None:
            return None
        for task in subagent.task_history:
            if task.task_id == task_id:
                return task.result
        return None

    def get_stats(self) -> Dict[str, Any]:
        """获取子代理管理器统计信息。"""
        total_completed = sum(sa.total_tasks_completed for sa in self.subagents.values())
        total_failed = sum(sa.total_tasks_failed for sa in self.subagents.values())
        running = sum(1 for sa in self.subagents.values() if sa.status == SubagentStatus.RUNNING)

        return {
            "total_subagents": len(self.subagents),
            "running_subagents": running,
            "total_tasks_completed": total_completed,
            "total_tasks_failed": total_failed,
            "max_concurrent": self.max_concurrent,
            "agent_types": list(set(sa.agent_type for sa in self.subagents.values())),
        }
