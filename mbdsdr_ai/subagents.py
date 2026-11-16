# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - Subagents 子代理框架
=======================================
Subagents：子代理框架，让主 Agent 可以创建专门的子 Agent 处理特定任务。

对照白皮书第四章 4.5 Subagents 子代理框架。

核心概念：
- Subagent：子代理，有自己的工具集、上下文、系统提示；注入 model_manager
  后 execute_task 跑真 LLM 循环（隔离上下文 + 工具白名单过滤 + max_steps
  截断诚实返回 partial + timeout_s 墙钟预算诚实返回 partial，见
  execute_task 文档）
- SubagentManager：子代理管理器，创建/管理/销毁子代理
- SubagentTask：子代理任务，包含目标、输入、超时（timeout_s 真约束 LLM
  循环墙钟，<=0 不限时）、回调

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
import os
import time

import threading
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable
from enum import Enum

from .context_manager import estimate_message_tokens
from .session_log import SESSION_DIR, SessionLog
# 签名探测共用缝（workflow_engine 无重依赖、不成环）：判断注入的
# tool_registry.call_from_model 是否支持 timeout_s=，据此下发墙钟余额。
from .workflow_engine import accepts_kwarg


class SubagentStatus(str, Enum):
    """子代理状态。"""
    IDLE = "idle"  # 空闲
    RUNNING = "running"  # 运行中
    WAITING = "waiting"  # 排队等待（全局槽位或本子代理空闲）；#85 起真赋值
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消；#87 起真赋值（request_cancel 在安全点生效）
    TIMEOUT = "timeout"  # 超时


@dataclass
class SubagentResult:
    """子代理执行结果。

    字段分两代（旧字段语义不变，既有调用方零改可用）：
    - 旧字段：success / output / data / error / steps_executed / tools_called ...；
    - 新字段（2026-10-14 subagent 隔离执行，kimi-code 审计 Top-4 落地面）：
      - status："" / "ok" / "truncated" / "timeout" / "cancelled" / "error"
        ——truncated=达到 max_steps 上限的 partial 结果，timeout=耗尽
        timeout_s 墙钟预算的 partial 结果，cancelled=用户请求取消的 partial
        结果，error=model_manager 缺失或 LLM 调用失败；
      - steps_used：实际消耗的 LLM 循环步数（与 steps_executed 同值镜像）；
      - tools_used：实际执行过的工具名（与 tools_called 同值镜像）；
      - truncated：是否因 max_steps 上限被截断（partial 结果必须可见）；
      - timed_out：是否因 timeout_s 墙钟预算耗尽被中止（与 truncated 互斥，
        两者都是 partial，成因不同不可混报——展示层据此说明真实原因）；
      - cancelled：是否因用户请求取消被中止（#87）。三者互斥且**取消优先**：
        同一步里既撞上取消又撞上超时/步数上限时，只报取消（用户主动意图比
        保护性上限更具体），绝不把取消说成截断或超时。
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
    status: str = ""          # "ok" / "truncated" / "timeout" / "cancelled" / "error"
    steps_used: int = 0       # 实际消耗的 LLM 循环步数
    tools_used: List[str] = field(default_factory=list)  # 实际执行过的工具名
    truncated: bool = False   # 是否因 max_steps 上限被截断（partial 结果）
    timed_out: bool = False   # 是否因 timeout_s 墙钟预算耗尽被中止（partial）
    cancelled: bool = False   # 是否因用户请求取消被中止（partial，#87）

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
            "timed_out": self.timed_out,
            "cancelled": self.cancelled,
        }


@dataclass
class SubagentTask:
    """子代理任务。"""
    task_id: str
    goal: str  # 任务目标
    input_data: Dict[str, Any] = field(default_factory=dict)  # 输入数据
    timeout_s: float = 120.0  # 任务墙钟预算（秒）；<=0 = 不限时（见 _resolve_timeout_s）
    # LLM 循环步数上限：**0 = 继承子代理自身上限**（create(max_steps=N)），
    # >0 = 本任务显式覆盖。此前默认 8，与本字段「<=0 = 用 Subagent 自身默认」
    # 的语义自相矛盾：_resolve_max_steps优先取 task.max_steps，于是默认 8
    # 永远盖掉子代理的 N ⇒ create(max_steps=3) 是死旋钮（模型以为限了 3 步，
    # 实际跑 8 步）。#89 改默认 0，让继承真的发生（默认子代理仍是 8，零回归）。
    max_steps: int = 0
    callback: Optional[Callable[[SubagentResult], None]] = None  # 完成回调
    status: SubagentStatus = SubagentStatus.IDLE
    result: Optional[SubagentResult] = None
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    # 归属子代理 id（#87）：manager.cancel() 据此把排队中的任务对回它的子代理。
    # 加在**末尾**，保持既有位置参数构造（task_id, goal, ...）零改。
    subagent_id: str = ""


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

    **串行语义（#86）**：一个子代理 = 一个角色 + 一条对话，同一时刻只跑一个
    任务。``self.context`` / ``status`` / ``current_task`` 是实例级状态，若允许
    同一子代理并发跑两个任务，B 的 ``self.context = _build_task_context(...)``
    重置后 A 仍会往同一列表 append —— A 的下一轮模型调用就会读到 B 的任务
    目标与工具结果（跨任务污染），status/current_task 也互相覆盖。故
    ``execute_task`` 用非可重入串行闸 ``_task_lock`` 保护：抢不到锁**立即**
    诚实返回 ``subagent_busy``（不执行、不产结论、不记历史、不算失败任务）；
    异步路径由 :meth:`SubagentManager._run_queued` 先等本代理空闲再抢全局
    槽位（等待期间不占全局并发额度）。非可重入是刻意的：同线程嵌套调用同一
    子代理也必须被挡住，否则内层会重置外层正在用的上下文。

    执行模型（2026-10-14 起）：注入 model_manager 后 execute_task 运行真 LLM
    循环——self.context 维护本任务独立对话，多步「LLM → tool_calls → tool
    消息回灌 → LLM」直到无工具调用、达到 max_steps 或耗尽 task.timeout_s
    墙钟预算；工具调用只允许 allowed_tools 之内（越界本地拒绝）；截断/超时/
    失败均诚实标注（见 :class:`SubagentResult` 的
    status/steps_used/tools_used/truncated/timed_out）。
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
        session_log=None,  # 独立会话事件日志（SessionLog）；None=不落盘
        on_event: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
        # 事件冒泡回调 (subagent_id, event_type, fields)；父代理据此把子代理
        # 内部活动串进自己的会话日志/hook 面。None=不冒泡。
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
        # 子代理级串行闸（#86）：**非可重入**，保证同一时刻只有一个任务在读写
        # self.context / status / current_task。异步路径由 manager._run_queued
        # 先抢这把锁再抢全局槽位（等待期间不占全局额度）；直调/同步路径非阻塞
        # 抢锁，抢不到立即诚实报 subagent_busy。同线程嵌套同样被挡住。
        self._task_lock = threading.Lock()
        # 取消信号（#87）：此前 SubagentStatus.CANCELLED 是从未赋值过的死枚举，
        # agent.request_cancel 也只掐主对话——子代理一旦起跑，用户按停止也停不
        # 下来（父轮次结束后子代理还在跑工具）。这里给每个子代理一个独立 Event，
        # 由 request_cancel() 置位、LLM 循环在安全点检查（口径与主 agent 一致：
        # 步边界 + 每个工具执行前；在途的单次 chat/工具不强杀）。
        # 每次 _execute_locked 起跑前 clear()，故「上一任务的取消」绝不毒害下一任务。
        self._cancel_event = threading.Event()
        # 观测面（Sprint 6）：独立 session log + 向父代理冒泡的事件回调。
        # 两者都只读不阻塞主循环，任何异常就地吞掉（对齐 SessionLog 红线）。
        self.session_log = session_log
        self.on_event = on_event

    def _emit(self, event_type: str, **fields: Any) -> None:
        """子代理观测事件出口：写自己的独立 session log + 向父代理冒泡。

        对齐父 Agent 的 ``_slog``/``_hook`` 双通道语义——绝不抛异常拖垮
        LLM 循环；session_log 与 on_event 各自独立吞错，一个坏不影响另一个。

        字段约定：subagent_id/agent_type 由本方法统一注入（dict 合并，调用方
        显式同名字段优先，避免重复 kwarg）；冒泡给父代理的 fields **不含**
        subagent_id（父代理 ``_on_subagent_event`` 自己加，防重复）。
        """
        enriched = {"subagent_id": self.subagent_id,
                    "agent_type": self.agent_type}
        enriched.update(fields)
        if self.session_log is not None:
            try:
                self.session_log.emit(event_type, **enriched)
            except Exception:
                pass
        if self.on_event is not None:
            bubbled = dict(enriched)
            bubbled.pop("subagent_id", None)
            try:
                self.on_event(self.subagent_id, event_type, bubbled)
            except Exception:
                pass

    def can_use_tool(self, tool_name: str) -> bool:
        """检查子代理是否可以使用指定工具（语义：allowed_tools=None 即全部）。

        注意：LLM 循环内部用 :meth:`_effective_allowed_names`（None 时额外排除
        subagent_* 防递归），本方法保持原公开语义不变。
        """
        if self.allowed_tools is None:
            return True
        return tool_name in self.allowed_tools

    def request_cancel(self) -> bool:
        """请求取消当前在跑的任务（#87）。返回是否真的有一个任务被通知到。

        语义与 ``agent.request_cancel`` 完全同口径——**协作式取消**：只置位
        信号，LLM 循环在安全点（步边界 / 每个工具执行前 / 循环收尾）看到后
        停机并如实报 ``status="cancelled"`` + ``cancelled=True``。在途的单次
        ``model_manager.chat`` / 工具调用不会被强杀（Python 无法安全中断
        线程），故按下取消后仍可能等一次在途调用返回。

        空闲时返回 ``False`` 且**不置位**：否则信号会残留到下一个任务，把
        用户根本没想取消的新任务一上来就掐掉（「取消毒害」）。
        """
        if self.current_task is None or self.status != SubagentStatus.RUNNING:
            return False
        self._cancel_event.set()
        return True

    def execute_task(self, task: SubagentTask, max_steps: int = None) -> SubagentResult:
        """执行任务（同步，真 LLM 循环）；契约详见 :meth:`_execute_locked`。

        并发保护（#86）：非阻塞抢子代理串行闸，抢不到**立即**返回诚实 busy
        结果（见 :meth:`_busy_result`），绝不与在跑任务共享 self.context。
        锁在 finally 归还：成功/异常/超时/截断都不会把子代理永久堵死。
        """
        if not self._task_lock.acquire(blocking=False):
            return self._busy_result(task)
        try:
            return self._execute_locked(task, max_steps=max_steps)
        finally:
            self._task_lock.release()

    def _busy_result(self, task: SubagentTask) -> SubagentResult:
        """本子代理正在跑别的任务：诚实拒绝（不执行/不产结论/不记历史/不计失败）。

        与 manager 级满槽 busy 共用 ``subagent_busy`` 前缀，但 ``data.reason``
        区分成因（``subagent_serialized`` = 本子代理在忙；manager 级带
        ``max_concurrent`` = 全局槽位满），调用方与展示层据此说真话。
        ``task.status`` 保持原值（它根本没跑），也不进 task_history ——
        没执行的任务不得冒充执行过。
        """
        running = getattr(self.current_task, "task_id", "") or ""
        return SubagentResult(
            success=False,
            status="error",
            subagent_id=self.subagent_id,
            task_id=task.task_id,
            error=(f"subagent_busy: 子代理 {self.subagent_id} 正在执行任务 "
                   f"{running or '(未知)'}，同一子代理同一时刻只跑一个任务"
                   f"（对话上下文隔离）；本次未执行，请稍后重试或改用异步提交排队"),
            data={"busy": True, "reason": "subagent_serialized",
                  "running_task_id": running},
        )

    def _execute_locked(self, task: SubagentTask,
                        max_steps: int = None) -> SubagentResult:
        """
        执行任务（同步，真 LLM 循环）。**调用方必须已持有 _task_lock。**

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
           结果，绝不伪装成完成；
        6. **墙钟预算**：task.timeout_s > 0 时给本任务设 deadline，在 LLM 循环
           的**步边界**（安全点）检查；耗尽 → status=timeout + timed_out=True
           + 带 [超时] 标记的 partial 结果，task.status=TIMEOUT。预算余额同时
           下发给工具（registry 支持 per-call timeout_s 时），使挂死工具也被
           约束。``timeout_s <= 0`` = 不限时（向后兼容）。
           **诚实边界**：单次在途的 model_manager.chat / 工具调用不会被强杀
           （Python 无法安全中断线程），故实际耗时上界 = 预算 + 该次在途调用
           的时长；这与 agent.request_cancel / 工作流取消同一安全点口径。
        7. **串行隔离（#86）**：本方法只在持有 ``_task_lock`` 时被调用，故
           self.context / status / current_task 全程只被本任务读写——文档
           承诺的「与本代理的上个任务零共享」由锁真兑现，不再只是每任务重置
           列表（重置挡不住并发：B 重置后 A 仍会往同一列表 append）。
        8. **可取消（#87）**：``request_cancel()`` 置位后，循环在安全点停机，
           返回 status=cancelled + cancelled=True + 带 [已取消] 标记的 partial
           结果，task.status=CANCELLED。取消**优先于**超时与 max_steps 截断
           （同一步撞上多种成因时只报用户主动的那个），三者互斥绝不混报。
           取消信号在**任务收尾**时清（不是起跑时清）：起跑时清会把「worker
           已声明起跑、循环还没转起来」这个窗口里收到的取消抹掉，用户的停止
           就丢了；收尾时清则既不留残留信号毒害下一任务，也不丢在途取消。
        """
        start_time = time.time()
        self.status = SubagentStatus.RUNNING
        self.current_task = task
        task.started_at = start_time

        # 观测面（Sprint 6）：任务起点落独立 session log + 冒泡父代理。
        # input_data 只记键名与大小，不落完整负载（长输入归调用方上下文）。
        timeout_s = self._resolve_timeout_s(task)
        self._emit("task_start", task_id=task.task_id, goal=task.goal,
                   requested_max_steps=self._resolve_max_steps(task, max_steps),
                   timeout_s=timeout_s,
                   input_keys=sorted(task.input_data.keys()) if task.input_data else [],
                   allowed_tools=(sorted(self.allowed_tools)
                                  if self.allowed_tools is not None else "all"))

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
            # 墙钟 deadline 与 execution_time 同一时间基准（start_time），
            # None = 不限时（timeout_s <= 0）
            deadline = (start_time + timeout_s) if timeout_s is not None else None
            output, data, steps_used, tools_used, truncated, timed_out, \
                cancelled, error = self._run_llm_loop(task, limit, deadline=deadline)

            result.output = output
            result.data = data
            result.steps_used = steps_used
            result.tools_used = list(tools_used)
            result.truncated = truncated
            result.timed_out = timed_out
            result.cancelled = cancelled
            # 旧字段镜像（同值），既有调用方零改
            result.steps_executed = steps_used
            result.tools_called = list(tools_used)
            result.error = error

            if cancelled:
                # 取消：partial 结果 + 显式标记。放在最前 ⇒ 与超时/截断同轮
                # 撞上时只报取消（用户主动意图比保护性上限更具体）。
                result.success = False
                result.status = "cancelled"
                self.total_tasks_failed += 1
                task.status = SubagentStatus.CANCELLED
            elif timed_out:
                # 超时：partial 结果 + 显式标记，成因与 max_steps 截断分开报
                result.success = False
                result.status = "timeout"
                self.total_tasks_failed += 1
                task.status = SubagentStatus.TIMEOUT
            elif truncated:
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
            self._emit("error", task_id=task.task_id, code=type(e).__name__,
                       detail=str(e))

        result.execution_time = time.time() - start_time
        task.completed_at = time.time()
        task.result = result
        self.status = SubagentStatus.IDLE
        self.current_task = None
        # 清取消信号必须在摘掉 RUNNING/current_task **之后**：反过来的话，
        # 两步之间到达的 request_cancel 会看到 RUNNING 并重新置位，残留信号
        # 就会把下一个任务一上来掐掉（取消毒害）。
        self._cancel_event.clear()
        self.task_history.append(task)

        # 观测面（Sprint 6）：任务终点——status/steps/truncated/tools 全部落账，
        # partial（truncated / timed_out / cancelled）与 error 都诚实标注，绝不
        # 把截断/超时/取消/失败记成完成。
        self._emit("task_end", task_id=task.task_id, status=result.status,
                   success=result.success, steps_used=result.steps_used,
                   tools_used=list(result.tools_used), truncated=result.truncated,
                   timed_out=result.timed_out, cancelled=result.cancelled,
                   execution_time=round(result.execution_time, 3),
                   error=result.error)

        # 执行回调
        if task.callback:
            try:
                task.callback(result)
            except Exception:
                pass

        return result

    # ── LLM 循环内部实现 ────────────────────────────────

    def _resolve_max_steps(self, task: SubagentTask, max_steps: Optional[int]) -> int:
        """解析循环步数上限：execute_task 参数 > task.max_steps > self.max_steps。

        三者都按「>0 才生效」取值，故 task.max_steps=0（默认）= 继承子代理
        自身上限（#89）；子代理也没设时兜底 8。
        """
        for cand in (max_steps, getattr(task, "max_steps", None), self.max_steps):
            if cand and int(cand) > 0:
                return int(cand)
        return 8

    def _resolve_timeout_s(self, task: SubagentTask) -> Optional[float]:
        """解析任务墙钟预算（秒）：``>0`` 生效；``<=0`` / 脏值 = 不限时（None）。

        脏值（None / 非数字 / NaN / inf）按**不限时**处理而不是抛错：超时是
        保护性上限，不该因调用方传了脏值就把整个任务判失败；也绝不把脏值当
        「0 秒立即超时」——那会让任务一步都跑不了。
        """
        try:
            val = float(getattr(task, "timeout_s", None))
        except (TypeError, ValueError):
            return None
        if not val > 0 or val == float("inf"):
            return None
        return val

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
        budget = self._resolve_timeout_s(task)
        budget_view = f"{budget:.0f}s" if budget is not None else "不限时"
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
            f"- 墙钟预算 {budget_view}；耗尽后你的部分结论会被标记 timeout 返回，"
            f"同样不得视为完成\n"
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

    def _run_llm_loop(self, task: SubagentTask, limit: int,
                      deadline: Optional[float] = None):
        """真 LLM 循环：LLM → tool_calls → tool 回灌 → LLM，直到无调用或超限。

        返回 (output, data, steps_used, tools_used, truncated, timed_out,
        cancelled, error)：
        - error 非空 = LLM 调用失败（技术细节，调用方按 status=error 处理）；
        - truncated=True = 达到 limit 步仍在索取工具，output 为带标记 partial；
        - timed_out=True = 耗尽 deadline 墙钟预算，output 为带 [超时] 标记 partial；
        - cancelled=True = 用户请求取消（#87），output 为带 [已取消] 标记 partial；
        - 其余 = 正常完成，output 为最终文本结论。

        ``deadline``（绝对 time.time() 秒）为 None = 不限时。检查点是**步边界**
        与**每个工具执行前**（安全点，与 agent.request_cancel / 工作流取消同
        口径）：在途的单次 chat / 工具调用不强杀，但预算余额会经 registry 的
        per-call ``timeout_s`` 下发给工具（注册表支持时），故挂死工具也被约束。

        取消（#87）用同一批安全点，另加**chat 返回后**一点：用户可能在模型
        正在生成时按下取消，此时本轮已产出的文本要保留（诚实 partial），但
        本轮索取的工具一律不再执行。取消判定**先于**超时判定：同一步同时
        撞上两者时只报取消（用户主动意图比保护性上限更具体）。
        """
        visible = self._effective_allowed_names()
        schemas = self._tool_schemas(visible)
        # 每任务重置上下文：隔离语义（与主 agent / 其它子代理 / 上个任务零共享）
        self.context = self._build_task_context(task, limit, visible)

        # 一次性签名探测：duck-type 假注册表（只实现 call_from_model(tc)）不
        # 支持 timeout_s 时自动退回原调用。绝不 try/except TypeError 包真实
        # 调用来探测——那会把工具内部的 TypeError 误判成签名不支持而重跑工具。
        registry_accepts_timeout = accepts_kwarg(
            getattr(self.tool_registry, "call_from_model", None), "timeout_s")

        tools_used: List[str] = []
        trimmed_seen = False
        last_content = ""
        loop_start = time.time()
        budget = (deadline - loop_start) if deadline is not None else 0.0

        def _expired() -> bool:
            return deadline is not None and time.time() >= deadline

        def _cancelled() -> bool:
            return self._cancel_event.is_set()

        def _cancel_return(steps_done: int, content: str = ""):
            """用户取消：诚实 partial（成因与超时/截断分开，绝不混报）。

            ``content`` = 本轮已产出但尚未写进上下文的文本（chat 返回后的安全点
            传它，避免把用户已经看到的结论丢掉）；步边界处传空串，用 last_content。
            """
            text = content if content.strip() else last_content
            marker = (
                f"\n\n[已取消] 子代理 {self.subagent_id} 的任务已被用户请求取消"
                f"（{steps_done}/{limit} 步），任务未完成；以上为取消前的部分"
                f"结论，不得视为完成。")
            output = (text + marker) if text.strip() else (
                f"[已取消] 子代理 {self.subagent_id} 的任务已被用户请求取消"
                f"（{steps_done}/{limit} 步），且未产出任何文本结论"
                f"（已执行工具: {tools_used or '无'}）。")
            data = {
                "agent_type": self.agent_type,
                "steps_used": steps_done,
                "tools_used": list(tools_used),
                "truncated": False,
                "timed_out": False,
                "cancelled": True,
                "context_messages": len(self.context),
                "context_trimmed": trimmed_seen,
            }
            self._emit("cancelled", task_id=task.task_id, steps_done=steps_done,
                       tools_used=list(tools_used))
            return output, data, steps_done, tools_used, False, False, True, (
                f"cancelled: 用户请求取消，已执行 {steps_done}/{limit} 步后停机，"
                f"返回取消前的部分结果（cancelled=True）")

        def _timeout_return(steps_done: int):
            """墙钟耗尽：诚实 partial（成因与 max_steps 截断分开，绝不混报）。"""
            elapsed = time.time() - loop_start
            marker = (
                f"\n\n[超时] 子代理 {self.subagent_id} 耗尽 timeout_s="
                f"{budget:.1f}s 墙钟预算（已用 {elapsed:.1f}s，"
                f"{steps_done}/{limit} 步），任务未完成；以上为超时前的部分"
                f"结论，不得视为完成。")
            output = (last_content + marker) if last_content.strip() else (
                f"[超时] 子代理 {self.subagent_id} 耗尽 timeout_s={budget:.1f}s "
                f"墙钟预算（已用 {elapsed:.1f}s），且未产出任何文本结论"
                f"（已执行工具: {tools_used or '无'}）。")
            data = {
                "agent_type": self.agent_type,
                "steps_used": steps_done,
                "tools_used": list(tools_used),
                "truncated": False,
                "timed_out": True,
                "cancelled": False,
                "timeout_s": round(budget, 3),
                "elapsed_s": round(elapsed, 3),
                "context_messages": len(self.context),
                "context_trimmed": trimmed_seen,
            }
            self._emit("timeout", task_id=task.task_id, steps_done=steps_done,
                       timeout_s=round(budget, 3), elapsed_s=round(elapsed, 3))
            return output, data, steps_done, tools_used, False, True, False, (
                f"timeout_exhausted: 耗尽 timeout_s={budget:.1f}s 墙钟预算"
                f"（已用 {elapsed:.1f}s），返回超时的部分结果（timed_out=True）")

        for step in range(1, limit + 1):
            # 安全点①：步边界。取消先于超时判定（同轮撞上两者只报用户主动的
            # 那个）。上一轮（chat + 工具批）已耗尽预算 → 不再起新步。
            if _cancelled():
                return _cancel_return(step - 1)
            if _expired():
                return _timeout_return(step - 1)
            if self._trim_context():
                trimmed_seen = True

            try:
                response = self.model_manager.chat(
                    messages=self.context, tools=schemas or None,
                    tool_choice="auto")
            except Exception as e:
                detail = f"model_manager.chat 调用异常: {type(e).__name__}: {e}"
                self._emit("error", task_id=task.task_id, step=step,
                           code=type(e).__name__, detail=detail)
                return "", {}, step - 1, tools_used, False, False, False, detail

            if not isinstance(response, dict) or not response.get("success"):
                err = "unknown_error"
                if isinstance(response, dict):
                    err = response.get("error") or err
                detail = f"模型调用失败: {err}（step {step}/{limit}）"
                self._emit("error", task_id=task.task_id, step=step,
                           code=str(err), detail=detail)
                return "", {}, step - 1, tools_used, False, False, False, detail

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

            # 安全点②：chat 刚返回。用户可能在模型生成期间按下取消——本轮
            # 已产出的文本保留（诚实 partial），但本轮索取的工具一律不执行，
            # 也不再起下一轮。放在无工具调用分支**之前**：即便模型这轮给的
            # 是最终结论，取消也要如实报 cancelled，不得冒充正常完成。
            if _cancelled():
                self.context.append({"role": "assistant", "content": content})
                return _cancel_return(step, content)

            if not tool_calls:
                # 无工具调用 = 最终结论（assistant 消息入上下文，独立对话保持完整）
                self.context.append({"role": "assistant", "content": content})
                self._emit("assistant_message", task_id=task.task_id,
                           step=step, content=content)
                data = {
                    "agent_type": self.agent_type,
                    "steps_used": step,
                    "tools_used": list(tools_used),
                    "truncated": False,
                    "timed_out": False,
                    "cancelled": False,
                    "context_messages": len(self.context),
                    "context_trimmed": trimmed_seen,
                }
                return content, data, step, tools_used, False, False, False, ""

            # 观测面（Sprint 6）：每个索取工具的 LLM 轮次落账
            self._emit("step", task_id=task.task_id, step=step,
                       tool_calls=len(tool_calls))
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
                self._emit("tool_call", task_id=task.task_id, step=step,
                           tool=name, tool_call_id=tc_id,
                           arguments=fn.get("arguments", ""))
                if not name:
                    tool_content = "拒绝：工具调用缺少 function.name，未执行"
                    outcome = "error"
                elif self.tool_registry is None:
                    tool_content = (f"拒绝：子代理 {self.subagent_id} 未注入 "
                                    f"tool_registry，工具 {name} 无法执行")
                    outcome = "error"
                elif name not in visible:
                    # 工具视图过滤：越界调用本地拒绝，不触达 registry、不上抛
                    tool_content = (
                        f"拒绝：子代理 {self.subagent_id}（{self.agent_type}）"
                        f"无权调用工具 {name}（不在 allowed_tools 白名单），"
                        f"调用未执行。白名单: {sorted(visible)}")
                    outcome = "denied"
                elif _cancelled():
                    # 安全点③：用户已取消 → 本工具不执行，回灌诚实说明。仍补
                    # 一条 tool 消息，保持 assistant(tool_calls)/tool 配对完整
                    # （否则这段历史再发给模型就是非法请求）。同批剩余工具也
                    # 会走到这里，故整批都不执行，而不是只掐第一个。
                    tool_content = (
                        f"[已取消] 用户中途取消，子代理 {self.subagent_id} 的"
                        f"工具 {name} 未执行（不得据此下结论）")
                    outcome = "cancelled"
                elif _expired():
                    # 安全点②：预算已耗尽 → 本工具不执行，回灌诚实说明。仍补
                    # 一条 tool 消息，保持 assistant(tool_calls)/tool 配对完整。
                    tool_content = (
                        f"拒绝：子代理 {self.subagent_id} 的 timeout_s 墙钟预算"
                        f"已耗尽，工具 {name} 未执行（不得据此下结论）")
                    outcome = "skipped"
                else:
                    try:
                        if registry_accepts_timeout and deadline is not None:
                            # 余额下发给工具（挂死工具也被约束）。必须钳到正数：
                            # registry 语义里 timeout_s<=0 是「关闭超时内联执行」，
                            # 传非正数会变成无限执行，与预算耗尽的意图相反。
                            res = self.tool_registry.call_from_model(
                                tc, timeout_s=max(deadline - time.time(), 0.001))
                        else:
                            res = self.tool_registry.call_from_model(tc)
                        tool_content = getattr(res, "content", None) or str(res)
                        outcome = ("executed" if getattr(res, "success", True)
                                   else "error")
                    except Exception as e:
                        tool_content = (
                            f"工具执行异常: {type(e).__name__}: {e}"
                            f"（未得到真实结果，不得据此下结论）")
                        outcome = "error"
                    tools_used.append(name)
                self._emit("tool_result", task_id=task.task_id, step=step,
                           tool=name, tool_call_id=tc_id, outcome=outcome)
                self.context.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "name": name,
                    "content": tool_content,
                })

        # 安全点④：跑完 limit 步。取消优先（用户主动意图比保护性上限更具体），
        # 其次超时，最后才是 max_steps 截断——三者都是 partial，绝不伪装完成，
        # 也绝不把一种成因说成另一种。
        if _cancelled():
            return _cancel_return(limit)
        if _expired():
            return _timeout_return(limit)

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
            "timed_out": False,
            "cancelled": False,
            "context_messages": len(self.context),
            "context_trimmed": trimmed_seen,
        }
        return output, data, limit, tools_used, True, False, False, (
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
    - 资源管理（并发数限制）：``max_concurrent > 0`` 时真限流——异步
      ``submit_task`` 满槽排队等（task.status=WAITING，台账见 ``task_queue``），
      同步 ``execute_task`` 满槽立即返回 ``subagent_busy`` 诚实失败（不阻塞
      调用方，嵌套子代理也只会快速失败而非死锁）；``<=0`` = 不限并发。
    - 子代理级串行（#86）：全局额度之外，**同一个子代理**同一时刻只跑一个
      任务（它的 context/status 是实例级状态，并发跑会跨任务污染）。异步
      提交到同一子代理 = 排队串行；同步/直调撞上在跑任务 = 立即 busy
      （``data.reason="subagent_serialized"``，与全局满槽成因分开报）。
    - 取消（#87）：``cancel(subagent_id=None)`` 把用户意图传到子代理——在跑的
      在安全点停机（status=cancelled / task.status=CANCELLED），还在排队的直接
      判死并出队（一个模型轮次都不跑）。此前 CANCELLED 是从未赋值过的死枚举，
      父 agent 的停止只掐主对话，子代理起跑后就再也停不下来。
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

    def __init__(self, tool_registry, model_manager=None, max_concurrent: int = 4,
                 on_event: Optional[Callable[[str, str, Dict[str, Any]], None]] = None,
                 enable_session_log: bool = False,
                 session_log_dir: Optional[str] = None):
        self.tool_registry = tool_registry
        self.model_manager = model_manager
        self.max_concurrent = max_concurrent
        self.subagents: Dict[str, Subagent] = {}
        # 排队中的异步任务（submit_task 入队，worker 起跑前出队）。此前声明后
        # 从未使用；#85 起是真排队台账，get_stats 据此报 queued_tasks。
        self.task_queue: List[SubagentTask] = []
        self._lock = threading.Lock()
        self._task_counter = 0
        # 并发限流（#85）：max_concurrent 此前只被状态面当事实展示
        # （"并发上限 4"），submit_task 却无条件起线程 → 广告的上限根本不存在。
        # 这里用有界信号量真限流：>0 = 同时在跑的任务数上限；<=0 / 脏值 =
        # 不限并发（None，向后兼容）。异步 submit 满槽时排队等（WAITING），
        # 同步 execute_task 满槽时立即诚实报 busy（不阻塞、不死锁）。
        try:
            _mc = int(max_concurrent)
        except (TypeError, ValueError):
            _mc = 0
        self._slots = threading.BoundedSemaphore(_mc) if _mc > 0 else None
        # 观测面（Sprint 6）：
        # - on_event：父代理注入的冒泡回调 (subagent_id, event_type, fields)，
        #   把子代理内部活动串进父会话日志/hook 面；
        # - enable_session_log：每个子代理落一份**独立** JSONL（独立 session）。
        #   默认 **False**（opt-in，对齐 Sprint 2 超时的零回归默认）：裸建
        #   SubagentManager（如单测）不写盘、不污染 ~/.mbdsdr；生产路径由
        #   MBDSDRAgent 显式按 config.enable_session_log 打开（见 agent.py）。
        #   session_log_dir 缺省走 ~/.mbdsdr/sessions/subagents。
        self.on_event = on_event
        self.enable_session_log = enable_session_log
        self.session_log_dir = session_log_dir

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

        # 独立 session（Sprint 6）：每个子代理一份自己的 append-only JSONL，
        # 落在 sessions/subagents/ 下，与父代理会话日志物理隔离但可按
        # subagent_id 关联。建日志失败绝不阻断子代理创建（吞错降级为 None）。
        session_log = None
        if self.enable_session_log:
            try:
                log_dir = self.session_log_dir or os.path.join(
                    SESSION_DIR, "subagents")
                session_log = SessionLog(subagent_id, directory=log_dir)
            except Exception:
                session_log = None

        subagent = Subagent(
            subagent_id=subagent_id,
            agent_type=agent_type,
            tool_registry=self.tool_registry,
            model_manager=self.model_manager,
            allowed_tools=allowed_tools,
            system_prompt=system_prompt,
            max_steps=max_steps if max_steps else 8,
            session_log=session_log,
            on_event=self.on_event,
        )

        with self._lock:
            self.subagents[subagent_id] = subagent

        # 创建事件落子代理自己的日志 + 冒泡父代理（生命周期可审计）
        subagent._emit("created", agent_type=agent_type,
                       allowed_tools=(sorted(allowed_tools)
                                      if allowed_tools else "all"),
                       session_log_path=getattr(session_log, "path", None))
        return subagent_id

    def destroy(self, subagent_id: str) -> bool:
        """销毁一个子代理：**先停活，再摘台账**（#90）。返回是否真销毁了一个。

        既有 bool 契约不变（调用方/测试都按它断言）；需要「在跑 / 排队」分开
        的诚实计数时用 :meth:`destroy_report`，两者共用同一套锁内实现——与
        :meth:`cancel` / :meth:`cancel_report` 同一形状。
        """
        return self.destroy_report(subagent_id)["destroyed"]

    def destroy_report(self, subagent_id: str) -> Dict[str, Any]:
        """销毁并返回诚实计数：``destroyed`` / ``cancelled_running`` /
        ``cancelled_queued``（供模型侧 ``subagent_destroy`` 工具用，#91）。

        缺陷原状：这里只 ``pop`` + 落一条 destroyed 事件。而 ``submit_task``
        起的 daemon worker 持有 **subagent 对象的直接引用**，于是销毁之后
        在跑的 LLM 循环继续烧 token / 继续调工具，排队任务照样会被执行；更糟
        的是此后 ``cancel(sid)`` 因 id 不在注册表返回 0、``cancel()`` 遍历
        ``self.subagents.values()`` 也找不到它 —— 孤儿任务变成**不可取消**。

        现在 pop 与「掐在跑 + 判死排队」在**同一个 ``self._lock`` 窗口**里完成，
        与 worker 的起跑声明（见 :meth:`_run_queued`）互斥，不留「刚好在起跑
        窗口里被销毁」的交错缝。

        诚实边界（与 #87 / agent.request_cancel 同一口径）：这是**协作式取消**，
        在途的单次 chat / 工具调用不强杀，故 ``destroyed=True`` 只表示「已摘台账
        并已请求中止」，不表示「此刻已经静止」；在跑任务会在安全点收尾为
        cancelled，partial 结果仍照常走 ``task.callback`` 与 ``task_history``。
        两个取消计数**分开报，绝不混算**：排队任务一个模型轮次都没跑，把它们
        算进「已取消的在跑任务」会让状态面说谎。id 不存在时三个字段都是
        False/0，不抛错（善后动作幂等）。
        """
        with self._lock:
            subagent = self.subagents.pop(subagent_id, None)
            if subagent is None:
                return {"destroyed": False, "cancelled_running": 0,
                        "cancelled_queued": 0}
            signalled, cancelled_queued = self._cancel_locked([subagent])

        # 事件冒泡/回调一律放锁外（回调可能反过来调 submit_task 抢同一把
        # 非可重入锁）——与 cancel_report 同一纪律。
        for sub, task in cancelled_queued:
            sub._emit("cancelled", task_id=task.task_id, steps_done=0,
                      tools_used=[], queued=True)
            if task.callback:
                try:
                    task.callback(task.result)
                except Exception:
                    pass

        # 销毁前落一条终点事件（SessionLog 每次 emit 即 flush，无需显式 close）
        subagent._emit("destroyed",
                       total_tasks_completed=subagent.total_tasks_completed,
                       total_tasks_failed=subagent.total_tasks_failed,
                       cancelled_running=signalled,
                       cancelled_queued=len(cancelled_queued))
        return {"destroyed": True, "cancelled_running": signalled,
                "cancelled_queued": len(cancelled_queued)}

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
        max_steps: int = None,
    ) -> str:
        """
        提交任务给子代理（异步）。

        返回 task_id。受 ``max_concurrent`` 限流：槽位满时任务在 ``task_queue``
        里排队（``task.status=WAITING``），worker 抢到槽位才真执行；排队不是
        丢弃，任务一定会被执行（除非进程退出——worker 是 daemon 线程）。

        同一子代理的多个任务串行执行（#86）：worker 先等该子代理空闲再抢
        全局槽位，故排队等待期间不浪费全局并发额度，也不会跨任务污染上下文。

        可取消（#87）：``cancel(subagent_id)`` / ``cancel()`` 既掐在跑的任务
        （安全点生效），也把还在排队的任务直接判 CANCELLED 并出队——排队中
        被取消的任务**一个模型轮次都不会跑**。

        max_steps: 本任务的步数上限覆盖（None/<=0 = 继承子代理自身上限，
        即 ``create(max_steps=N)`` 设的值，#89）。
        """
        subagent = self.subagents.get(subagent_id)
        if subagent is None:
            raise ValueError(f"子代理不存在: {subagent_id}")

        with self._lock:
            self._task_counter += 1
            task_id = f"task_{self._task_counter}"
            task = SubagentTask(
                task_id=task_id,
                goal=goal,
                input_data=input_data or {},
                timeout_s=timeout_s,
                callback=callback,
                subagent_id=subagent_id,
                max_steps=int(max_steps) if max_steps else 0,
            )
            self.task_queue.append(task)
            queued = len(self.task_queue)

        # 排队态先落账（WAITING 此前是从未赋值过的死枚举），再起跑 worker
        task.status = SubagentStatus.WAITING
        subagent._emit("task_queued", task_id=task_id, goal=goal,
                       queued=queued, max_concurrent=self.max_concurrent)

        # 异步执行（槽位在 worker 里抢，绝不无条件并发跑）
        thread = threading.Thread(
            target=self._run_queued,
            args=(subagent, task),
            daemon=True,
        )
        thread.start()

        return task_id

    def _run_queued(self, subagent: Subagent, task: SubagentTask) -> None:
        """异步 worker：等子代理空闲 → 等全局槽位 → 执行 → 两者都归还。

        锁序固定 **subagent → manager**（#86）：先等本子代理的串行闸，再抢
        全局槽位。这样「等同一子代理排队」的任务在等待期间**不占全局并发
        额度**——否则 4 个任务全压在一个子代理上就会把槽位耗光、饿死其它
        子代理。同步路径抢全局槽位是非阻塞的（抢不到立即 busy），绝不反向
        持槽等锁，故两条路径不构成环路、无死锁。

        槽位/锁归还都放 finally：任务抛异常/超时/截断都不会泄漏，否则限流会
        越跑越紧直到永久堵死（#84 的墙钟预算保证槽位终会释放）。
        出队在**起跑时**做，故 ``task_queue`` 只留真在排队等的任务（不是
        「已提交未完成」），状态面的「排队 N」才是真话。

        取消（#87）：排队期间被 ``cancel()`` 判 CANCELLED 的任务，在三处
        （抢串行闸前 / 抢到后 / 抢到全局槽位后）都再查一次——只要还没真起跑
        就绝不执行。检查放在**抢到锁之后**是关键：cancel 与 worker 起跑可能
        交错，只看进锁前那一眼会漏掉「刚好在等待期间被取消」的任务。
        """
        try:
            if task.status == SubagentStatus.CANCELLED:
                return
            subagent._task_lock.acquire()
            try:
                if task.status == SubagentStatus.CANCELLED:
                    return
                if self._slots is not None:
                    self._slots.acquire()
                try:
                    # 起跑声明与 cancel() 的排队扫描共用 manager._lock ⇒ 二者
                    # 互斥：cancel 要么看到 WAITING 直接判死（worker 随后三处
                    # 检查都会跳过），要么看到已声明的 current_task/RUNNING 并
                    # 置位取消信号（循环在第一个安全点停机）。绝不会出现
                    # 「两边都以为对方会处理」而把用户的停止丢掉。
                    with self._lock:
                        if task.status == SubagentStatus.CANCELLED:
                            return
                        self.task_queue[:] = [
                            t for t in self.task_queue if t is not task]
                        subagent.current_task = task
                        subagent.status = SubagentStatus.RUNNING
                        task.status = SubagentStatus.RUNNING
                    # 已持串行闸 → 走内部入口，绝不走会再次抢锁的 execute_task
                    subagent._execute_locked(task)
                finally:
                    if self._slots is not None:
                        self._slots.release()
            finally:
                subagent._task_lock.release()
        finally:
            # 兜底：抢锁/抢槽位前就异常也不留僵尸台账（_dequeue 幂等）
            self._dequeue(task)

    def _dequeue(self, task: SubagentTask) -> None:
        """按身份出队（dataclass __eq__ 比字段，可能误删同值的另一个任务）。"""
        with self._lock:
            self.task_queue[:] = [t for t in self.task_queue if t is not task]

    def cancel(self, subagent_id: Optional[str] = None) -> int:
        """请求取消子代理任务（#87）。返回**被通知到的在跑任务数**。

        既有 int 契约不变（调用方/测试都按它断言）；需要「在跑 / 排队」分开
        的诚实计数时用 :meth:`cancel_report`，两者共用同一套锁内实现。
        """
        return self.cancel_report(subagent_id)["cancelled_running"]

    def cancel_report(self, subagent_id: Optional[str] = None) -> Dict[str, int]:
        """取消并返回诚实计数：``cancelled_running`` / ``cancelled_queued``。

        供模型侧 ``subagent_cancel`` 工具用（#91）：只报「在跑 N」会把
        「判死了 M 个排队任务」这件事藏掉，用户/模型据此以为没事发生。
        语义与 :meth:`cancel` 完全一致，见下。

        ``subagent_id=None`` = 取消全部子代理（父 agent 的「停止」按钮语义）；
        给了 id 则只取消那一个（id 不存在时两个计数都是 0，不抛错——取消是
        幂等的善后动作，不该因为目标已经销毁而炸调用方）。

        两条路径分别处理，都不伪造结果：
        1. **在跑的**：调 ``subagent.request_cancel()`` 置位，LLM 循环在安全点
           停机并如实报 cancelled（在途的单次 chat/工具不强杀，口径与
           ``agent.request_cancel`` 一致）；
        2. **还在排队的**：直接判 CANCELLED + 出队 + 写一条诚实结果
           （``cancelled_while_queued``，steps_used=0，一个模型轮次都没跑），
           worker 起跑前的三处检查会认这个状态并跳过执行。

        两个计数**分开报，绝不混算**：排队任务从未起跑，把它们算进
        ``cancelled_running`` 会让状态面说谎；反过来只报在跑数又会把「判死了
        M 个排队任务」藏掉。排队取消的可观测面另走 task_history / task_queue。

        并发口径：置位取消信号与扫描排队台账都在 ``self._lock`` 内完成，与
        worker 的「起跑声明」互斥（见 :meth:`_run_queued`），故不会丢掉刚好
        在起跑窗口里到达的取消。``request_cancel`` 只置一个 Event、不抢任何
        锁、不回调，锁内调用安全；事件冒泡/回调一律放锁外（回调可能反过来
        调 submit_task 抢同一把非可重入锁）。
        """
        targets = ([self.subagents[subagent_id]]
                   if subagent_id is not None and subagent_id in self.subagents
                   else list(self.subagents.values()))

        with self._lock:
            signalled, cancelled_queued = self._cancel_locked(targets)

        for sub, task in cancelled_queued:
            sub._emit("cancelled", task_id=task.task_id, steps_done=0,
                      tools_used=[], queued=True)
            if task.callback:
                try:
                    task.callback(task.result)
                except Exception:
                    pass

        return {"cancelled_running": signalled,
                "cancelled_queued": len(cancelled_queued)}

    def _cancel_locked(self, targets: List["Subagent"]) -> tuple:
        """取消的锁内核心。**调用方必须已持有 ``self._lock``。**

        抽出来是为了让 :meth:`destroy` 能在**同一个锁窗口**里「摘台账 + 停活」
        （#90）：destroy 若先 pop 再调 cancel，中间那一缝里 worker 可能刚好
        声明起跑，取消就丢了；而 cancel 自己又要抢这把非可重入锁，不能在锁内
        直接调。故锁内核心与锁外冒泡/回调分离，两个入口共用同一套判据。

        按 ``targets`` 认归属而不是查注册表：destroy 传进来的是**已经 pop
        出来**的子代理，此时 ``self.subagents`` 里已经没有它了；若还查注册表，
        被判死的排队任务就进不了 task_history、调用方永远拿不到结果。

        返回 ``(在跑被通知数, [(子代理, 被判死的排队任务), ...])``；事件冒泡与
        回调由调用方在锁外做（回调可能反过来抢同一把非可重入锁）。
        """
        by_id = {s.subagent_id: s for s in targets}
        signalled = 0
        cancelled_queued: List[tuple] = []
        for sub in targets:
            if sub.request_cancel():
                signalled += 1
        keep = []
        for task in self.task_queue:
            owner = task.subagent_id
            if owner in by_id and task.status in (
                    SubagentStatus.WAITING, SubagentStatus.IDLE):
                task.status = SubagentStatus.CANCELLED
                task.completed_at = time.time()
                task.result = SubagentResult(
                    success=False,
                    status="cancelled",
                    subagent_id=owner,
                    task_id=task.task_id,
                    cancelled=True,
                    error=("cancelled_while_queued: 排队期间被取消，"
                           "未执行任何模型轮次/工具"),
                    data={"cancelled_before_start": True},
                )
                sub = by_id.get(owner)
                if sub is not None:
                    sub.task_history.append(task)
                    cancelled_queued.append((sub, task))
            else:
                keep.append(task)
        self.task_queue[:] = keep
        return signalled, cancelled_queued

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

        max_steps: 本任务的步数上限覆盖（None=继承子代理自身上限，即
        ``create(max_steps=N)`` 设的值；子代理也没设时兜底 8，#89）。

        并发限流：``max_concurrent > 0`` 且槽位已满时**立即**返回
        ``success=False / status=error / error=subagent_busy``（未执行任何
        工具、未起 LLM 循环），不阻塞调用方——阻塞等待会让嵌套子代理死锁。
        调用方（模型）可稍后重试或改用异步 submit_task 排队。

        子代理级串行（#86）：即使全局槽位有空，目标子代理正在跑别的任务时
        同样立即返回 ``subagent_busy``，但 ``data.reason="subagent_serialized"``
        （全局满槽则带 ``data.max_concurrent``）——两种成因分开报，绝不混说。

        可取消（#87）：本调用是同步阻塞的，取消由**另一个线程**调
        ``cancel(subagent_id)`` / ``cancel()`` 触发（父 agent 的停止按钮就是
        这么做的），循环在安全点停机后本调用返回 ``status="cancelled"``。
        """
        subagent = self.subagents.get(subagent_id)
        if subagent is None:
            raise ValueError(f"子代理不存在: {subagent_id}")

        acquired = False
        if self._slots is not None:
            acquired = self._slots.acquire(blocking=False)
            if not acquired:
                with self._lock:
                    queued = len(self.task_queue)
                return SubagentResult(
                    success=False,
                    status="error",
                    subagent_id=subagent_id,
                    error=(f"subagent_busy: 并发已达上限 {self.max_concurrent}，"
                           f"本次未执行（排队中 {queued} 个）；请稍后重试，"
                           f"或改用异步提交排队"),
                    data={"busy": True, "max_concurrent": self.max_concurrent,
                          "queued_tasks": queued},
                )

        try:
            with self._lock:
                self._task_counter += 1
                task_id = f"task_{self._task_counter}"

            task = SubagentTask(
                task_id=task_id,
                goal=goal,
                input_data=input_data or {},
                timeout_s=timeout_s,
                subagent_id=subagent_id,
            )

            return subagent.execute_task(task, max_steps=max_steps)
        finally:
            if acquired:
                self._slots.release()

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
            # 排队台账（#85）：满槽时异步任务在这里等，可观测才谈得上下限
            "queued_tasks": len(self.task_queue),
            "concurrency_enforced": self._slots is not None,
            "agent_types": list(set(sa.agent_type for sa in self.subagents.values())),
        }
