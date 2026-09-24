"""
MBDSDR AI 内核 - Subagents 子代理框架
=======================================
Subagents：子代理框架，让主 Agent 可以创建专门的子 Agent 处理特定任务。

对照白皮书第四章 4.5 Subagents 子代理框架。

核心概念：
- Subagent：子代理，有自己的工具集、上下文、系统提示
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

import time

import threading
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable, Tuple
from enum import Enum


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
    """子代理执行结果。"""
    success: bool
    output: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    execution_time: float = 0.0
    steps_executed: int = 0
    tools_called: List[str] = field(default_factory=list)
    subagent_id: str = ""
    task_id: str = ""

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
        }


@dataclass
class SubagentTask:
    """子代理任务。"""
    task_id: str
    goal: str  # 任务目标
    input_data: Dict[str, Any] = field(default_factory=dict)  # 输入数据
    timeout_s: float = 120.0  # 超时时间
    max_steps: int = 20  # 最大步数
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
    - 上下文（独立的对话历史）
    - 任务队列
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
    ):
        self.subagent_id = subagent_id
        self.agent_type = agent_type
        self.tool_registry = tool_registry
        self.model_manager = model_manager
        self.allowed_tools = set(allowed_tools) if allowed_tools else None
        self.system_prompt = system_prompt or SUBAGENT_PROMPTS.get(agent_type, "你是 MBDSDR 的子代理。")
        self.max_context_tokens = max_context_tokens

        self.status = SubagentStatus.IDLE
        self.context: List[Dict[str, str]] = []  # 对话历史
        self.task_history: List[SubagentTask] = []
        self.current_task: Optional[SubagentTask] = None
        self.total_tasks_completed = 0
        self.total_tasks_failed = 0
        self.created_at = time.time()
        self._lock = threading.Lock()

    def can_use_tool(self, tool_name: str) -> bool:
        """检查子代理是否可以使用指定工具。"""
        if self.allowed_tools is None:
            return True
        return tool_name in self.allowed_tools

    def execute_task(self, task: SubagentTask) -> SubagentResult:
        """
        执行任务（同步）。

        这是简化版执行：直接调用工具完成任务，不做完整的 LLM 循环。
        完整的 LLM 驱动执行需要主 Agent 的模型管理器。
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
            # 简化执行：根据任务类型调用相应工具
            output, data, tools_called = self._execute(task)

            result.success = True
            result.output = output
            result.data = data
            result.tools_called = tools_called
            result.steps_executed = len(tools_called)
            self.total_tasks_completed += 1
            task.status = SubagentStatus.COMPLETED

        except Exception as e:
            result.success = False
            result.error = f"{type(e).__name__}: {str(e)}"
            result.output = f"任务执行失败: {result.error}"
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

    def _execute(self, task: SubagentTask) -> Tuple[str, Dict[str, Any], List[str]]:
        """实际执行任务：真调 tool_registry 对应的工具，不是打印占位。

        修复"假执行"：
        - 必须有 tool_registry；没有则直接报错（不造假成功）。
        - 每个工具调用检查 ToolResult.success，失败即抛异常，由 execute_task
          标记为 FAILED，而不是把错误串当成功输出。
        - 支持 input_data 里显式指定 tool_name（通用调用路径）。
        - 未内置映射的子代理类型不再返回假数据，而是抛 NotImplementedError。
        """
        tools_called: List[str] = []
        data: Dict[str, Any] = {}

        if self.tool_registry is None:
            raise RuntimeError(
                f"子代理 {self.subagent_id} 未注入 tool_registry，无法真调工具")

        def _call(name: str, args: Optional[Dict[str, Any]] = None) -> str:
            """真调工具并记录；权限/存在/成功与否都显式判定。"""
            if not self.can_use_tool(name):
                raise PermissionError(
                    f"子代理 {self.agent_type} 无权使用工具 {name}")
            res = self.tool_registry.call(name, args or {})
            tools_called.append(name)
            if not res.success:
                # 工具不存在/不可用/执行失败：明确报错，不当成功
                raise RuntimeError(
                    f"工具 {name} 执行失败 [{res.error}]: {res.content}")
            return res.content

        # 通用路径：input_data 显式指定 tool_name → 真调该工具
        explicit_tool = task.input_data.get("tool_name") or task.input_data.get("tool")
        if explicit_tool:
            params = {k: v for k, v in task.input_data.items()
                      if k not in ("tool_name", "tool")}
            output = _call(explicit_tool, params)
            data = {"tool_name": explicit_tool, "params": params}
            return output, data, tools_called

        # 内置类型 → 默认工具映射
        if self.agent_type == "spectrum_analyzer":
            freq = task.input_data.get("frequency_hz", 100000000)
            output = _call("sdr_spectrum_analyze", {"frequency_hz": freq})
            data = {"frequency_hz": freq, "analysis_type": "spectrum"}
        elif self.agent_type == "satellite_tracker":
            sat = task.input_data.get("satellite_name", "NOAA 19")
            output = _call("sdr_satellite_sky_view", {"satellite": sat})
            data = {"satellite": sat, "tracking_type": "orbit"}
        elif self.agent_type == "baseband_recorder":
            dur = task.input_data.get("duration", 10)
            output = _call("sdr_record_start", {"duration_s": dur})
            data = {"duration": dur, "recording_type": "baseband"}
        else:
            # 未内置默认工具的子代理类型：返回明确错误，不造假成功数据
            raise NotImplementedError(
                f"子代理类型 {self.agent_type!r} 未配置默认工具；"
                f"请在 input_data 中传入 tool_name 及参数")

        return output, data, tools_called

    def _execute_spectrum_analysis(self, task: SubagentTask) -> Tuple[str, Dict[str, Any]]:
        """频谱分析子代理的简化执行。"""
        freq = task.input_data.get("frequency_hz", 100000000)
        output = f"频谱分析完成\n中心频率: {freq/1e6:.3f} MHz\n"
        output += "（简化执行，实际需要连接 SDR 设备读取 IQ 样本）\n"
        data = {"frequency_hz": freq, "analysis_type": "spectrum"}
        return output, data

    def _execute_satellite_tracking(self, task: SubagentTask) -> Tuple[str, Dict[str, Any]]:
        """卫星跟踪子代理的简化执行。"""
        sat_name = task.input_data.get("satellite_name", "NOAA 19")
        output = f"卫星跟踪完成\n卫星: {sat_name}\n"
        output += "（简化执行，实际需要 sgp4 计算轨道）\n"
        data = {"satellite": sat_name, "tracking_type": "orbit"}
        return output, data

    def _execute_recording(self, task: SubagentTask) -> Tuple[str, Dict[str, Any]]:
        """基带录制子代理的简化执行。"""
        duration = task.input_data.get("duration", 10)
        output = f"基带录制完成\n时长: {duration} 秒\n"
        output += "（简化执行，实际需要连接 SDR 设备）\n"
        data = {"duration": duration, "recording_type": "baseband"}
        return output, data

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
    ) -> str:
        """
        创建一个子代理。

        返回 subagent_id。
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
    ) -> SubagentResult:
        """
        执行任务（同步，等待完成）。

        返回 SubagentResult。
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

        return subagent.execute_task(task)

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
