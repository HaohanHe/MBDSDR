# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - Agent 主循环
==============================
整合上下文管理、模型管理、工具注册、记忆系统，实现真正的 LLM 驱动的 Agent。

核心能力：
- 真正的 LLM 调用（不是关键词匹配）
- 工具调用循环（模型调用工具 -> 执行 -> 结果返回模型 -> 继续）
- 自动上下文压缩
- 记忆检索与写入
- 多轮对话
- 错误处理与恢复
- 调用统计

这是 MBDSDR 的智能体核心，桌面端/移动端/CLI 都通过这个 Agent 交互。
"""

import json
import logging
import math
import os
import time
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional, Callable

from .config import AgentConfig
from .context_manager import (
    ContextManager,
    SYSTEM_PROMPT,
    get_model_context_window,
    estimate_tokens,
)
from .model_manager import ModelManager
from .tool_registry import ToolRegistry, ToolResult, hex_bytes, clean_exc
from .conversation import ConversationStore
from .session_log import SessionLog
from .tool_budget import select_tools, tool_budget_tokens
from .memory import MemoryStore
from .self_evolution import SelfEvolutionEngine
from .guardian import Guardian
from .workflow_engine import WorkflowEngine
from .scheduler import Scheduler
from .sdr_backend import SDRBackendManager
from .spectrum_processor import SpectrumProcessor
from .sdr_tools import register_sdr_tools
from .fldigi_modes import register_fldigi_modes_tools
from .hooks import HookManager, Event, EventType, create_logging_hook
from .subagents import SubagentManager, SubagentStatus
from .pose import PoseFusion, ARProjector, IMUData, GPSData
from .workflow_recorder import WorkflowRecorder
from .file_tracker import FileChangeTracker, ChangeType
from .plugin_system import PluginManager
from .llm_judge import LLMJudge, JudgeResult, JudgeDimension
from .self_learning import SelfLearningEngine, Experience, ExperienceType
from .orchestrator import Orchestrator, Task, TaskPriority
from .code_editor import CodeEditor, EditRecord, EditStatus
from .astronomy import Observer, EquatorialCoord, AltAzCoord, AntennaParams, unix_to_jd, jd_to_mjd, jd_to_gmst, jd_to_lst, lst_to_hms, compute_refraction, compute_airmass, compute_pointing_guidance
from .amr import AMRClassifier, AMRResult, ModulationType
from .error_catalog import retryable, suggestion, user_message


logger = logging.getLogger(__name__)

# 子代理不存在属 LLM 传参错误（可预期异常）：同一 id 的日志按 60s 限流，
# 避免模型反复用错 ID 时把日志刷爆；每次调用仍照常把失败上报给调用方。
_SUBAGENT_ERR_LOG_INTERVAL_S = 60.0
_SUBAGENT_ERR_LOG_AT: Dict[str, float] = {}


# git clone 常见失败模式 → 中文可操作诊断（按序首个命中）。
# git 的 stderr 是英文原句，整条转述给模型等于没诊断；已知模式直接给
# 排查动作，未收录模式保留退出码与末行原文供人工排查（clean_exc 同款纪律）。
_GIT_CLONE_FAIL_PATTERNS = (
    (("could not resolve host", "name or service not known",
      "temporary failure in name resolution"),
     "网络故障：无法解析仓库主机名（检查 DNS 与网络连接）"),
    (("failed to connect", "connection refused", "connection timed out",
      "couldn't connect", "network is unreachable", "connection reset by peer",
      "operation timed out"),
     "网络故障：连不上仓库主机（检查网络/代理/防火墙是否放行）"),
    (("authentication failed", "permission denied", "could not read username",
      "terminal prompts disabled"),
     "认证失败：私有仓库需要凭证（https 用有读权限的 PAT）"),
    (("repository not found", "404"),
     "仓库不存在或无权访问：确认 URL 拼写与仓库可见性（私有仓库需凭证）"),
    (("already exists and is not an empty directory",),
     "目标目录已存在且非空：换一个 dest 名称，或先清理该目录"),
    (("remote hung up", "hung up unexpectedly", "early eof", "rpc failed", "packfile"),
     "传输中断：仓库过大或网络不稳（可重试；--depth 1 已限制拉取深度）"),
    (("403", "rate limit"),
     "被拒绝或限流（HTTP 403/rate limit）：稍后重试或更换网络环境"),
)


def _git_clone_failure_zh(returncode: int, stderr: Optional[str]) -> str:
    """把 git clone 的失败输出整形成中文诊断消息。"""
    low = (stderr or "").lower()
    for keys, zh in _GIT_CLONE_FAIL_PATTERNS:
        if any(k in low for k in keys):
            return f"克隆失败: {zh}（git 退出码 {returncode}）"
    tail = ((stderr or "").strip().splitlines() or [""])[-1][:150]
    hint = f"（{tail}）" if tail else ""
    return f"克隆失败: git 退出码 {returncode}{hint}——未识别的失败模式，请把该输出人工排查"


def _tool_result_to_model_content(result) -> str:
    """工具结果转模型可见文本。

    DSH（deepseek-harness）nextStep 模式：next_step 非空时追加
    "[下一步建议]" 块——失败/部分成功时给模型明确的恢复路径，
    避免它从错误码反推。纯函数，便于单测。
    """
    content = getattr(result, "content", "") or ""
    ns = (getattr(result, "next_step", "") or "").strip()
    if ns:
        content = content + "\n[下一步建议] " + ns
    return content


# 并发/取消相关的运行时字段是惰性创建的（测试里有些 agent 用 object.__new__
# 构造，没有走 __init__）。用模块级锁保证多线程首次创建不会各拿一把锁。
_CONCURRENCY_INIT_LOCK = threading.Lock()

# chat() 并发被拒时的返回码（调用方据此区分「模型失败」与「本机正忙」）
BUSY_ERROR = "agent_busy"
CANCEL_ERROR = "cancelled"

# 指标环形缓冲：只保留最近 N 个请求，避免长会话把内存越攒越大
METRICS_HISTORY_LIMIT = 50

_METRICS_INIT_LOCK = threading.Lock()


def _percentile(values: List[float], q: float) -> Optional[float]:
    """线性插值分位数；空列表返回 None（不做假数据）。"""
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * max(0.0, min(1.0, q))
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return round(xs[lo] + (xs[hi] - xs[lo]) * frac, 1)



class MBDSDRAgent:
    """
    MBDSDR AI 定义无线电智能体。

    真正的 LLM 驱动的 Agent，支持工具调用、上下文管理、记忆系统。

    用法:
        config = AgentConfig(api_key="sk-...", model="Qwen/Qwen3.6-35B-A3B")
        agent = MBDSDRAgent(config)
        agent.register_mcp_tools(mcp_tools, mcp_call_handler)
        response = agent.chat("调谐到 FM 98.5")
        print(response["content"])
    """

    @staticmethod
    def _get_ground_station_latlon() -> tuple:
        """从 ~/.mbdsdr/config.json 读取地面站坐标 (lat, lon)。

        返回 (lat, lon)；未配置时返回 (None, None)。
        不内置任何城市坐标，避免地区性硬编码。
        """
        cfg_path = os.path.expanduser("~/.mbdsdr/config.json")
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            else:
                return (None, None)
        except Exception:
            return (None, None)
        try:
            lat = cfg.get("ground_station_lat")
            lon = cfg.get("ground_station_lon")
            if lat is not None and lon is not None:
                return (float(lat), float(lon))
        except (TypeError, ValueError):
            pass
        return (None, None)

    def __init__(self, config: AgentConfig = None):
        self.config = config or AgentConfig()

        # 初始化各子系统
        self.context_manager = ContextManager(
            max_context_tokens=self.config.max_context_tokens,
            compaction_threshold=self.config.compaction_threshold,
            compaction_target_ratio=self.config.compaction_target_ratio,
            system_prompt=SYSTEM_PROMPT,
            tool_output_max_chars=self.config.tool_output_max_chars,
            on_compaction=self._compaction_callback,
            model_name=self.config.model,
        )

        # 根据模型自动调整上下文窗口（用户没显式设置非默认值时）
        if self.config.max_context_tokens == 8192:
            model_window = get_model_context_window(self.config.model)
            if model_window != 8192:
                self.context_manager.max_context_tokens = model_window

        self.model_manager = ModelManager(
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            model=self.config.model,
            temperature=self.config.temperature,
            top_p=self.config.top_p,
            max_output_tokens=self.config.max_output_tokens,
            timeout=self.config.timeout,
        )

        # 从真实 API 获取模型列表（失败则 fallback 到内置列表，不阻塞初始化）
        try:
            self.model_manager.fetch_models(force_refresh=True)
        except Exception:
            pass

        self.tool_registry = ToolRegistry(
            tool_output_max_chars=self.config.tool_output_max_chars,
            default_timeout_s=getattr(self.config, "tool_timeout_s", 0.0) or None,
        )

        self.memory = MemoryStore()

        # 守护者快照引擎（防幻觉变砖的核心防线）
        self.guardian = Guardian()

        # 工作流引擎（多步骤工具调用序列）
        self.workflow_engine = WorkflowEngine()

        # 调度器（定时任务）
        self.scheduler = Scheduler()

        # Hook 事件钩子系统（白皮书第四章 4.4）
        self.hook_manager = HookManager(max_history=2000)

        # Subagents 子代理框架（白皮书第四章 4.5）
        # Sprint 6：注入 on_event 冒泡回调，把子代理内部 LLM 循环（创建/任务
        # 起止/工具调用与结果/错误）串进父代理的会话日志与 hook 观测面——
        # 子代理不再是黑盒；每个子代理另落一份独立 JSONL（见 SubagentManager）。
        self.subagent_manager = SubagentManager(
            tool_registry=self.tool_registry,
            model_manager=self.model_manager,
            on_event=self._on_subagent_event,
            enable_session_log=getattr(self.config, "enable_session_log", True),
        )

        # 6DOF 位姿融合 + AR 投影（白皮书第八章）
        # declination 为磁偏角（度），随地理位置变化；此处 -9.0 仅为示例默认值，
        # 实际使用应根据当地磁偏角设置（可从配置或 GPS 计算）。
        self.pose_fusion = PoseFusion(mode="fused", declination=-9.0)
        self.ar_projector = ARProjector(camera_fov_deg=60.0, screen_aspect=16.0/9.0)

        # 工作流录制与复用（白皮书第四章 4.6.4）
        self.workflow_recorder = WorkflowRecorder()

        # 文件变更跟踪器（白皮书第四章 4.6.6）
        self.file_tracker = FileChangeTracker()

        # 模块化插件系统（白皮书第九章 9.3）
        self.plugin_manager = PluginManager(
            tool_registry=self.tool_registry,
            hook_manager=self.hook_manager,
            subagent_manager=self.subagent_manager,
        )

        # LLM-as-Judge 多维评分系统（白皮书第四章 4.6.1）
        self.llm_judge = LLMJudge(model_manager=self.model_manager)

        # 自学习闭环系统（白皮书第四章 4.6.2）
        self.self_learning = SelfLearningEngine(judge=self.llm_judge)

        # 智能编排器（白皮书第四章 4.6.3）
        self.orchestrator = Orchestrator(tool_registry=self.tool_registry)

        # 代码编辑器（自编程核心）：读取/修改/热加载源代码，git commit，一键恢复
        self.code_editor = CodeEditor(project_root=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # 天文计算（借鉴 Stellarium）：坐标转换、时间系统、大气折射、天线参数
        self.observer = Observer()  # 默认观测者，用户可通过 GPS 设置
        self.amr_classifier = AMRClassifier(k=5)  # 自动调制识别分类器

        # 自进化引擎（默认关闭，需用户显式开启 enable_self_evolution）
        self.evolution = SelfEvolutionEngine() if self.config.enable_self_evolution else None

        # 注册内置工具
        self.tool_registry.register_builtin_tools(agent_ref=self)

        # 注册记忆读写工具
        self._register_memory_tools()

        # 注册守护者工具
        self._register_guardian_tools()

        # 注册工作流工具
        self._register_workflow_tools()

        # 注册动态 UI 工具（模型 → 界面；小动词面，可撤销）
        self._register_ui_action_tools()

        # 注册天空模式硬件能力协商工具（会话级账本 + 动作执行总线）
        self._register_sky_negotiation_tools()

        # 注册调度器工具
        self._register_scheduler_tools()

        # 注册自进化工具（如果启用）
        if self.evolution:
            self._register_evolution_tools()

        # 注册 SDR 专用工具（38个）
        register_sdr_tools(self)

        # 注册多模式数字解码（PSK31/RTTY/MFSK/FeldHell，依据公开无线电传标准实现）
        register_fldigi_modes_tools(self.tool_registry)

        # Hook 事件钩子：按 A2(D2) 摘除 LLM 工具面（不再向 LLM 注册
        # hook_list/hook_trigger/hook_history/hook_stats——那是让模型对着空气喊话）。
        # 但 HookManager 事件总线本身是活的：Sprint 4 起 _chat_impl 在真实节点
        # （工具调用/结果、上下文压缩、错误、助手消息）通过 self._hook() 发射生产
        # 事件，供内部钩子（自动录制/告警/审计）实时联动。SDR.* 事件源仍待硬件层接线。
        # self._register_hook_tools()  # 已摘除（D2）

        # 注册 Subagents 子代理工具（白皮书第四章 4.5）
        self._register_subagent_tools()

        # 注册 6DOF 位姿 + AR 投影工具（白皮书第八章）
        self._register_pose_tools()

        # 注册工作流录制工具（白皮书第四章 4.6.4）
        self._register_workflow_recorder_tools()

        # 注册文件变更跟踪工具（白皮书第四章 4.6.6）
        self._register_file_tracker_tools()

        # 插件系统：按 A2(D8) 摘除 LLM 工具面。plugins/ 为空目录、无插件生态，plugin_* 在空集上操作。
        # 保留 PluginManager 类（self.plugin_manager）与其单测；不再向 LLM 注册
        # plugin_list/plugin_load/plugin_enable/plugin_disable/plugin_stats/plugin_install。
        # self._register_plugin_tools()  # 已摘除（D8）

        # 注册 LLM-as-Judge 工具（白皮书第四章 4.6.1）
        self._register_judge_tools()

        # 注册自学习工具（白皮书第四章 4.6.2）
        self._register_learning_tools()

        # 智能编排器：按 A2(D4) 摘除 LLM 工具面。orchestrator 是 workflow_engine 的劣化重写（无
        # timeout/条件/模板），LLM 面对两个多步执行框架无所适从；统一走 workflow_engine。保留
        # Orchestrator 类（self.orchestrator）供内部/单测；不再向 LLM 注册 orchestrator_* 六个工具。
        # scheduler 不依赖本工具面（grep 零命中）。
        # self._register_orchestrator_tools()  # 已摘除（D4）

        # 注册自编程工具（代码编辑器）
        self._register_code_editor_tools()

        # 注册天文计算工具（借鉴 Stellarium）
        self._register_astronomy_tools()

        # 注册 AMR 自动调制识别工具
        self._register_amr_tools()
        self._register_web_tools()
        self._register_skill_tools()
        self._register_ft8_ldpc_tools()
        self._register_spectrum_tools()
        self._register_fst4_tools()
        self._register_cw_tools()
        self._register_adsb_tools()
        self._register_rds_tools()
        self._register_apt_tools()
        self._register_aprs_tools()
        self._register_ax25_tools()
        self._register_analog_demod_tools()
        self._register_acars_tools()
        # GQRX 真实 AGC / IQ校正 / 接收机管道（来源 gqrx src/dsp/agc_impl.cpp 等）
        from mbdsdr_ai.gqrx_receiver import register_gqrx_receiver_tools
        register_gqrx_receiver_tools(self.tool_registry)
        self._register_wfm_stereo_tools()
        self._register_signal_quality_tools()
        self._register_orbit_tools()
        self._register_pointing_tools()
        self._register_doppler_tools()
        # 轨道动力学原子积木（多普勒换算/取状态/STM/观测模型/RLS单步/校时/坐标）：
        # 每块一个独立小工具，模型现场编排，不打包成「定轨工具」大黑盒。
        from mbdsdr_ai.orbit_tools import register_orbit_brick_tools
        register_orbit_brick_tools(self.tool_registry)
        # 同步/调制解调原子积木（FLL锁频/符号同步/GMSK/频率轨迹）：
        # 同样逐块注册，模型现场编排接收链，不打包成「解调工具」大黑盒。
        from mbdsdr_ai.sync_tools import register_sync_brick_tools
        register_sync_brick_tools(self.tool_registry)
        # 射频前端与指向规划积木（星表/同步轨道指向/天线增益/指向容差/
        # 链路预算/故障诊断/罐子馈源几何/遥测接收链参数）：
        # 同样逐块注册，模型现场编排接收前端，不打包成「接收方案」大黑盒。
        from mbdsdr_ai.acquisition import register_acquisition_tools
        register_acquisition_tools(self.tool_registry)
        # 统一调制/解调积木（ASK/BPSK/QPSK/8PSK 调制、匹配滤波解调、星座质量、
        # 误码实测、RRC 抽头）：收敛全仓重复的调制实现到单一参数化内核。
        from mbdsdr_ai.modulation import register_modulation_tools
        register_modulation_tools(self.tool_registry)
        # CCSDS 通用数字接收链积木（帧构造/比特解码/IQ 解码）：
        # 此前仓内只有 SSDV 专用入口，像 ASTU-1 这类同为 CCSDS TM 体制的
        # BPSK 遥测任务没有模型可用入口。
        from mbdsdr_ai.ccsds_tools import register_ccsds_tools
        register_ccsds_tools(self.tool_registry)
        self._register_time_sync_tools()
        self._register_constellation_tools()
        self._register_baseband_tools()

        # ── 本轮新增公开标准/协议工具（实现依据见各适配器注释）──
        # SDR UI/接收机架构：SDR# / OpenWebRX / CubicSDR / HDSDR
        from mbdsdr_ai.sdrsharp_adapter import register_sdrsharp_tools
        from mbdsdr_ai.openwebrx_adapter import register_openwebrx_tools
        from mbdsdr_ai.cubicsdr_adapter import register_cubicsdr_tools
        from mbdsdr_ai.hdsdr_adapter import register_hdsdr_tools
        register_sdrsharp_tools(self.tool_registry)
        register_openwebrx_tools(self.tool_registry)
        register_cubicsdr_tools(self.tool_registry)
        register_hdsdr_tools(self.tool_registry)
        # 协议/频谱分析：URH / inspectrum / SigDigger + URH式三层解码框架
        from mbdsdr_ai.urh_adapter import register_urh_tools
        from mbdsdr_ai.inspectrum_adapter import register_inspectrum_tools
        from mbdsdr_ai.sigdigger_adapter import register_sigdigger_tools
        from mbdsdr_ai.protocol_stack import register_protocol_stack_tools
        register_urh_tools(self.tool_registry)
        register_inspectrum_tools(self.tool_registry)
        register_sigdigger_tools(self.tool_registry)
        register_protocol_stack_tools(self.tool_registry)
        # 数字语音/集群：M17 / op25(P25) / sdrtrunk
        from mbdsdr_ai.m17_adapter import register_m17_tools
        from mbdsdr_ai.op25_adapter import register_op25_tools
        from mbdsdr_ai.sdrtrunk_adapter import register_sdrtrunk_tools
        register_m17_tools(self.tool_registry)
        register_op25_tools(self.tool_registry)
        register_sdrtrunk_tools(self.tool_registry)
        # 业余无线电模式：pat(Winlink) / ARDOP / JS8Call / Xastir / minimodem Bell103
        from mbdsdr_ai.pat_adapter import register_pat_tools
        from mbdsdr_ai.ardop_adapter import register_ardop_tools
        from mbdsdr_ai.js8call_adapter import register_js8call_tools
        from mbdsdr_ai.xastir_adapter import register_xastir_tools
        from mbdsdr_ai.minimodem_adapter import register_minimodem_tools as register_minimodem_bell103_tools
        register_pat_tools(self.tool_registry)
        register_ardop_tools(self.tool_registry)
        register_js8call_tools(self.tool_registry)
        register_xastir_tools(self.tool_registry)
        register_minimodem_bell103_tools(self.tool_registry)
        # 卫星地面站 + SDRangel 插件：r2cloud / satnogs / OGN+FireDetector+RemoteControl
        from mbdsdr_ai.r2cloud_adapter import register_r2cloud_tools
        from mbdsdr_ai.satnogs_adapter import register_satnogs_tools
        from mbdsdr_ai.sdrangel_plugins import register_sdrangel_plugins_tools
        register_r2cloud_tools(self.tool_registry)
        register_satnogs_tools(self.tool_registry)
        register_sdrangel_plugins_tools(self.tool_registry)

        # 连接工作流引擎和调度器的工具执行器
        self.workflow_engine.set_tool_executor(self._workflow_tool_executor)
        self.scheduler.workflow_engine = self.workflow_engine
        self.scheduler.tool_executor = self._workflow_tool_executor

        # 运行时状态
        self.conversation_id = f"conv_{int(time.time())}"
        self.total_agent_calls = 0
        self.last_error = None
        # P7：本轮工具集裁剪报告（None 表示未启用工具调用或选择失败）
        self.last_tool_selection = None
        # P7.5：延迟/成本观测（真实数据；未发生过请求时 get_ai_metrics 报 no_data）
        self._metrics_history = []
        self._busy_rejections = 0
        self.metrics_history_limit = METRICS_HISTORY_LIMIT
        # repeat-call guard（借鉴 DeepSeek harness repeat-tool-reminder）
        self._repeat_key = None
        self._repeat_count = 0
        self._mcp_client = None
        # 多会话持久化存储
        self.conversation_store = ConversationStore()
        # 会话事件日志（append-only JSONL，"Model-visible ⟺ logged"）
        # 懒创建：按当前 conversation_id 取，会话切换时自动换文件。
        self._session_log = None
        self._session_log_conv = None

        # 验证配置
        errors = self.config.validate()
        if errors:
            self.last_error = "; ".join(errors)

    # ── 工具注册 ────────────────────────────────────────

    def _tool_language(self) -> Optional[str]:
        """config.language 归一化为目录语言码（zh-CN → zh）。

        默认 zh 目录缺失即回退原文描述，与未接线前行为一致。
        """
        from .i18n import normalize_lang
        return normalize_lang(getattr(self.config, "language", None) or "zh")

    def register_mcp_tools(self, mcp_tools: List[Dict[str, Any]], mcp_call_handler: Callable):
        """
        注册 MCP 硬件工具。

        mcp_tools: 从设备 list_tools 获取的工具列表
        mcp_call_handler: 调用 MCP 工具的函数 (tool_name, args) -> result
        """
        self.tool_registry.register_mcp_tools(mcp_tools, mcp_call_handler)
        # 更新上下文管理器的工具定义（描述语言跟随 config.language）
        self.context_manager.set_tool_definitions(
            self.tool_registry.get_tool_definitions(
                language=self._tool_language())
        )

    def set_mcp_client(self, client):
        """设置 MCP 客户端引用。"""
        self._mcp_client = client

    def _parse_change_type(self, value: str) -> "ChangeType":
        """宽松解析变更类型枚举（大小写不敏感+常见别名）。"""
        aliases = {
            "create": "create", "created": "create", "new": "create", "add": "create",
            "modify": "modify", "modified": "modify", "edit": "modify", "edited": "modify", "change": "modify", "changed": "modify", "update": "modify", "updated": "modify",
            "delete": "delete", "deleted": "delete", "remove": "delete", "removed": "delete", "del": "delete",
            "rename": "rename", "renamed": "rename", "move": "rename", "moved": "rename",
            "revert": "revert", "reverted": "revert", "rollback": "revert", "restore": "revert",
        }
        key = str(value).strip().lower()
        mapped = aliases.get(key, key)
        try:
            return ChangeType(mapped)
        except ValueError:
            return ChangeType.MODIFY  # 默认 modify

    def _parse_experience_type(self, value: str) -> "ExperienceType":
        """宽松解析经验类型枚举（大小写不敏感+常见别名）。"""
        aliases = {
            "tool_call": "tool_call", "tool": "tool_call", "toolcall": "tool_call", "call": "tool_call",
            "task_completion": "task_completion", "task": "task_completion", "complete": "task_completion", "completed": "task_completion", "success": "task_completion", "done": "task_completion",
            "error_recovery": "error_recovery", "error": "error_recovery", "recovery": "error_recovery", "fail": "error_recovery", "failed": "error_recovery", "retry": "error_recovery",
            "user_feedback": "user_feedback", "user": "user_feedback", "feedback": "user_feedback",
            "judge_feedback": "judge_feedback", "judge": "judge_feedback", "evaluation": "judge_feedback", "review": "judge_feedback",
        }
        key = str(value).strip().lower()
        mapped = aliases.get(key, key)
        try:
            return ExperienceType(mapped)
        except ValueError:
            return ExperienceType.TASK_COMPLETION  # 默认 task_completion

    def _register_web_tools(self):
        """联网取资料与克隆开源项目（借鉴 DeepSeek harness tool-web：fetch/search/trust policy）。

        让 agent 不再只能用本地工具：可以直接 HTTP 拉取技术文档/数据手册/参考实现，
        也可以 git clone 开源 SDR 项目（wsjtx/dump1090/redsea 等）到工作区学习，
        把"缺 H 矩阵/缺解码器"这类死路变成自己去取。
        """
        import urllib.request
        import urllib.parse
        import subprocess

        def _is_private_ip(host: str) -> bool:
            """解析主机名并判断是否指向内网/回环地址（SSRF 防护）。"""
            import ipaddress
            import socket
            try:
                infos = socket.getaddrinfo(host, None)
            except socket.gaierror:
                return True  # 无法解析则拒绝，避免 DNS rebinding 后绕过
            for info in infos:
                ip_str = info[4][0]
                try:
                    ip = ipaddress.ip_address(ip_str)
                except ValueError:
                    continue
                if (ip.is_private or ip.is_loopback or ip.is_link_local
                        or ip.is_reserved or ip.is_multicast):
                    return True
            return False

        def _fetch(args):
            url = (args.get("url") or "").strip()
            if not url:
                return ToolResult(False, "url 不能为空")
            from mbdsdr_ai.orbit import _network_disabled
            if _network_disabled():
                return ToolResult(
                    False,
                    "离线模式（MBDSDR_OFFLINE）：web_fetch_url 需要联网，已拒绝；"
                    "本工具无本地缓存可降级（离线请用本地文件/SDR 数据）")
            p = urllib.parse.urlparse(url)
            if p.scheme not in ("http", "https"):
                return ToolResult(False, f"只允许 http/https，收到 {p.scheme!r}")
            host = p.hostname
            if not host:
                return ToolResult(False, "URL 缺少主机名")
            # SSRF 防护：拒绝内网/回环/链路本地 IP
            if _is_private_ip(host):
                return ToolResult(False, f"安全限制：拒绝访问内网/回环地址 {host!r}")
            max_bytes = min(int(args.get("max_bytes", 200000)), 500000)
            try:
                from mbdsdr_ai.orbit import _bounded_urlopen
                req = urllib.request.Request(url, headers={"User-Agent": "MBDSDR/1.0"})
                r = _bounded_urlopen(req, 15.0, 20.0)  # 硬截止覆盖 DNS 阶段
                with r:
                    status = getattr(r, "status", 0)
                    raw = r.read(max_bytes + 1)
                    truncated = len(raw) > max_bytes
                    text = raw[:max_bytes].decode("utf-8", "replace")
                return ToolResult(True, f"HTTP {status} 取自 {url}\n{text}"
                                          + ("\n...[截断]" if truncated else ""))
            except Exception as e:  # noqa: BLE001
                return ToolResult(False, f"fetch 失败: {e}")

        self.tool_registry.register(
            name="web_fetch_url",
            description="HTTP/HTTPS 拉取一个 URL 的文本内容。用于在线查技术文档、数据手册、"
                        "参考实现、解码器源码片段。只取文本，超时 15s，最多 500KB。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "http/https URL"},
                    "max_bytes": {"type": "integer", "description": "最大字节数", "default": 200000},
                },
                "required": ["url"],
            },
            handler=_fetch,
            category="web",
        )

        def _clone(args):
            url = (args.get("url") or "").strip()
            if not url:
                return ToolResult(False, "url 不能为空")
            from mbdsdr_ai.orbit import _network_disabled
            if _network_disabled():
                return ToolResult(
                    False,
                    "离线模式（MBDSDR_OFFLINE）：git clone 需要联网，已拒绝；"
                    "工作区 repos/ 下已有的仓库仍可直接使用")
            # 安全：只允许 https，拒绝 file://、ext::、ssh scp 等可执行任意命令的 scheme
            if not (url.startswith("https://") or url.startswith("http://")):
                return ToolResult(False,
                    "安全限制：git clone 只允许 https:// URL，拒绝 file:///ext:: 等")
            name = (args.get("dest") or "").strip() or url.rstrip("/").split("/")[-1].replace(".git", "")
            dest = os.path.join(self.workspace_root if hasattr(self, "workspace_root") else ".", "repos", name)
            if os.path.exists(dest):
                return ToolResult(True, f"已存在: {dest}")
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                subprocess.run(["git", "clone", "--depth", "1", url, dest],
                               check=True, timeout=120, capture_output=True, text=True)
                return ToolResult(True, f"已克隆到 {dest}")
            except subprocess.TimeoutExpired:
                return ToolResult(False,
                    "克隆超时(120s)：网络过慢或仓库过大，可重试或改用镜像地址")
            except subprocess.CalledProcessError as e:
                return ToolResult(False, _git_clone_failure_zh(e.returncode, e.stderr))
            except Exception as e:  # noqa: BLE001
                return ToolResult(False, f"克隆失败: {clean_exc(e)}")

        self.tool_registry.register(
            name="git_clone_repo",
            description="git clone --depth 1 一个开源仓库到工作区 repos/ 目录。用于学习开源 SDR 项目"
                        "（如 wsjtx、dump1090、redsea、sdr++）的源码、H 矩阵、解码器参数。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "git URL"},
                    "dest": {"type": "string", "description": "目标目录名(可选)"},
                },
                "required": ["url"],
            },
            handler=_clone,
            category="web",
        )

    def _register_skill_tools(self):
        """技能目录与按需加载（借鉴 DeepSeek harness skill 子系统）。

        模型只看到技能名+描述；需要时 skill_load 才读完整正文，不堆上下文。
        自进化：往 skills/ 丢新目录即新技能，下次自动发现。
        """
        from mbdsdr_ai.skill_registry import SkillRegistry

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.skill_registry = SkillRegistry(os.path.join(project_root, "skills"))

        self.tool_registry.register(
            name="skill_list",
            description="列出当前所有可用技能的名称和简短描述。技能是按需加载的操作指引，"
                        "比如找干扰源、卫星过境跟踪、SSTV 解码。先看目录，需要细节时再 skill_load。",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: ToolResult(
                success=True,
                content=json.dumps(self.skill_registry.list(), ensure_ascii=False, indent=2)),
            category="skill",
        )

        def _load(args):
            name = (args.get("name") or "").strip()
            body = self.skill_registry.load(name)
            if body is None:
                return ToolResult(False, f"未找到技能 {name!r}，先 skill_list 看可用名称")
            return ToolResult(True, f"<skill_content name=\"{name}\">\n{body}\n</skill_content>")

        self.tool_registry.register(
            name="skill_load",
            description="按名称加载一个技能的完整操作指引。先 skill_list 看有哪些，再用本工具读细节。",
            parameters={
                "type": "object",
                "properties": {"name": {"type": "string", "description": "技能名 kebab-case"}},
                "required": ["name"],
            },
            handler=_load,
            category="skill",
        )

    def _register_ft8_ldpc_tools(self):
        """FT8 (174,91) LDPC BP 译码——H 矩阵从 wsjtx 权威源码提取。"""
        from mbdsdr_ai import ft8_ldpc
        from mbdsdr_ai import ft8_decode
        from mbdsdr_ai import ft8_lite
        from mbdsdr_ai.ft8_callsign import unpack28
        from mbdsdr_ai.ft8_unpack import unpack77

        def _decode_audio(args):
            samples = args.get("samples")
            rate = args.get("sample_rate")
            if not isinstance(samples, list) or rate is None:
                return ToolResult(False, "需要 samples(浮点列表) 和 sample_rate")
            r = ft8_lite.decode_ft8_audio([float(x) for x in samples], float(rate))
            return ToolResult(True, json.dumps(r, ensure_ascii=False))

        self.tool_registry.register(
            name="ft8_decode_audio",
            description="端到端 FT8 音频解码：输入一段 FT8 音频采样（约 15 秒），"
                        "自动找音峰、判相位、软判决、LDPC 译码、CRC14 校验、unpack77，"
                        "直接输出可读消息文本（呼号/网格/报告）。一条命令出结果。",
            parameters={
                "type": "object",
                "properties": {
                    "samples": {"type": "array", "items": {"type": "number"},
                                "description": "音频浮点采样（单声道）"},
                    "sample_rate": {"type": "number", "description": "采样率 Hz"},
                },
                "required": ["samples", "sample_rate"],
            },
            handler=_decode_audio,
            category="decode",
        )

        def _unpack_message(args):
            bits = args.get("bits")
            if not isinstance(bits, list) or len(bits) != 77:
                return ToolResult(False, "bits 必须是 77 个数据位（LDPC 信息位前 77 位）")
            r = unpack77([int(x) for x in bits])
            return ToolResult(True, json.dumps(r, ensure_ascii=False))

        self.tool_registry.register(
            name="ft8_unpack_message",
            description="把 FT8 解码出的 77 个数据位还原成可读消息文本（依据 FT8 公开协议标准实现）。"
                        "支持自由文本和标准消息（CQ/call 网格、call call 信号报告/RRR/RR73/73）。"
                        "接在 ft8_soft_decode 的 data_bits 之后用。",
            parameters={
                "type": "object",
                "properties": {"bits": {"type": "array",
                                        "items": {"type": "integer"},
                                        "description": "77 个数据位（0/1）"}},
                "required": ["bits"],
            },
            handler=_unpack_message,
            category="decode",
        )

        def _unpack_call(args):
            try:
                n28 = int(args.get("n28"))
            except (TypeError, ValueError):
                return ToolResult(False, "n28 必须是整数")
            call, ok = unpack28(n28)
            return ToolResult(True, json.dumps({"callsign": call, "known": ok},
                                               ensure_ascii=False))

        self.tool_registry.register(
            name="ft8_unpack_callsign",
            description="把 FT8/FT4 的 28 位呼号字段解成呼号文本（依据 FT8/FT4 公开协议标准实现）。"
                        "支持 CQ/DE/QRZ/CQ_nnn 特殊 token 与标准呼号；22bit hash 段返回 <hash:n>。",
            parameters={
                "type": "object",
                "properties": {"n28": {"type": "integer", "description": "28 位呼号字段整数值"}},
                "required": ["n28"],
            },
            handler=_unpack_call,
            category="decode",
        )

        def _decode(args):
            llr = args.get("llr")
            if not isinstance(llr, list) or len(llr) != 174:
                return ToolResult(False, "llr 必须是 174 个浮点（信道对数似然比）")
            bits, iters = ft8_ldpc.ldpc_bp_decode([float(x) for x in llr],
                                                  max_iter=int(args.get("max_iter", 25)))
            return ToolResult(True, json.dumps({
                "decoded_bits": bits,
                "n_info_bits": 91,
                "iters": iters,
                "info_bits": bits[:91],
            }, ensure_ascii=False))

        self.tool_registry.register(
            name="ft8_ldpc_decode",
            description="FT8 信号的 (174,91) LDPC 置信传播译码。输入 174 个信道 LLR，"
                        "输出 91 个信息位（77 数据 + CRC14）。配合 8FSK 软判决用。",
            parameters={
                "type": "object",
                "properties": {
                    "llr": {"type": "array", "items": {"type": "number"},
                            "description": "174 个信道 LLR"},
                    "max_iter": {"type": "integer", "default": 25},
                },
                "required": ["llr"],
            },
            handler=_decode,
            category="decode",
        )

        def _soft_decode(args):
            energies = args.get("tone_energies")
            if not isinstance(energies, list) or len(energies) != 58:
                return ToolResult(False, "tone_energies 必须是 58 个数据符号、每个 8 路能量")
            if not all(isinstance(e, list) and len(e) == 8 for e in energies):
                return ToolResult(False, "每个数据符号需要 8 路 tone 能量")
            r = ft8_decode.decode_ft8_payload(energies,
                                              max_iter=int(args.get("max_iter", 30)))
            return ToolResult(True, json.dumps({
                "data_bits": r["data_bits"],
                "crc_bits": r["crc_bits"],
                "crc_ok": r["crc_ok"],
                "iters": r["iters"],
            }, ensure_ascii=False))

        self.tool_registry.register(
            name="ft8_soft_decode",
            description="FT8 完整软解码：输入 58 个数据符号（已去掉 3 段 Costas 同步）"
                        "的 8 路 tone 能量，内部做 gray 反映射、colorder 重排、LDPC BP，"
                        "输出 77 数据位 + 14 CRC 位。比硬判决更抗噪。",
            parameters={
                "type": "object",
                "properties": {
                    "tone_energies": {"type": "array",
                                      "description": "58×8 能量矩阵（数据符号位置 7-35、43-71）",
                                      "items": {"type": "array",
                                                "items": {"type": "number"}}},
                    "max_iter": {"type": "integer", "default": 30},
                },
                "required": ["tone_energies"],
            },
            handler=_soft_decode,
            category="decode",
        )

        def _ft8_encode(args):
            text = (args.get("text") or "").strip()
            if not text:
                return ToolResult(False, "text 不能为空，如 'CQ BI1AAA OM74'")
            from mbdsdr_ai.ft8_encode import encode_ft8_text
            try:
                tones = encode_ft8_text(text)
            except Exception as e:  # noqa: BLE001
                return ToolResult(
                    False,
                    f"FT8编码失败: {type(e).__name__}: {e}，请使用标准消息格式如 "
                    f"'CQ BI1AAA OM74'、'BI1AAA K1ABC 73'、'BI1AAA K1ABC -17'",
                )
            return ToolResult(True, json.dumps({
                "tones": tones, "n_tones": len(tones),
                "note": "79 个 8FSK 音调索引(0-7)；3 个 Costas7 同步块在符号 0-6/36-42/72-78",
            }, ensure_ascii=False))

        self.tool_registry.register(
            name="ft8_encode",
            description="FT8 编码：把标准文本消息（如 'CQ BI1AAA OM74'、'BI1AAA K1ABC 73'、"
                        "'BI1AAA K1ABC -17'）编码成 79 个 8FSK 音调索引。参数依据公开标准 "
                        "（Costas7 同步、Gray 映射、CRC14、LDPC(174,91)，WSJT-X 仅作技术参考）。"
                        "与 ft8_soft_decode 互为往返。",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "标准消息文本，如 'CQ BI1AAA OM74'"},
                },
                "required": ["text"],
            },
            handler=_ft8_encode,
            category="decode",
        )

    def _register_spectrum_tools(self):
        """IQ 信号频谱分析：平均 PSD + 峰值保持 + 结构化峰列表（找台/找干扰源）。"""
        from mbdsdr_ai.signal_spectrum import analyze_iq_spectrum
        import numpy as np

        def _analyze(args):
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数采样列表（complex 可表示为 [re,im] 或复数浮点）")
            sr = float(args.get("sample_rate", 0) or 0)
            if sr <= 0:
                return ToolResult(False, "需要 sample_rate")
            # 支持 [re,im,...] 交错 或 复数浮点列表
            try:
                if iq and isinstance(iq[0], (int, float)) and len(iq) % 2 == 0 and args.get("interleaved"):
                    arr = np.array(iq[0::2], dtype=np.float64) + 1j * np.array(iq[1::2], dtype=np.float64)
                else:
                    arr = np.array(iq, dtype=complex)
            except Exception as e:
                return ToolResult(False, f"IQ 解析失败: {e}")
            r = analyze_iq_spectrum(
                arr, sr,
                center_hz=float(args.get("center_hz", 0) or 0),
                # fft_size<=2 会让帧移为 0 → range step 0 崩溃；最小钳到 32
                fft_size=max(int(args.get("fft_size", 2048) or 2048), 32),
                n_peaks=int(args.get("n_peaks", 12) or 12),
                margin_db=float(args.get("margin_db", 8) or 8),
            )
            # 只回峰列表+摘要，不回整段频谱数组（省 token）
            summary = {k: r[k] for k in (
                "n_samples", "sample_rate", "center_hz", "fft_size",
                "frames_averaged", "noise_floor_db", "peaks")}
            return ToolResult(True, json.dumps(summary, ensure_ascii=False))

        self.tool_registry.register(
            name="spectrum_analyze_iq",
            description="分析一段复数 IQ 采样：同时给出 Welch 平均 PSD、峰值保持(peak hold)、"
                        "噪声底，以及结构化的活动信号峰列表（每个峰的中心频率、峰值功率dB、"
                        "相对底噪的突出度、估计带宽）。用于找台/找干扰源/频谱扫描。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"},
                           "description": "复数 IQ 采样（复数浮点列表，或 interleaved 实虚交错）"},
                    "sample_rate": {"type": "number", "description": "采样率 Hz"},
                    "center_hz": {"type": "number", "description": "这段频谱的中心频率（换算绝对频率）"},
                    "fft_size": {"type": "integer", "description": "FFT 点数，默认 2048"},
                    "n_peaks": {"type": "integer", "description": "最多返回峰数，默认 12"},
                    "margin_db": {"type": "number", "description": "峰突出于底噪的门限 dB，默认 8"},
                    "interleaved": {"type": "boolean", "description": "true 表示 iq 是实虚交错"},
                },
                "required": ["iq", "sample_rate"],
            },
            handler=_analyze,
            category="spectrum",
        )

    def _register_fst4_tools(self):
        """FST4 LDPC (240,101) BP 译码——与 FT8 同源的物理层。"""
        from mbdsdr_ai.fst4_ldpc import ldpc_bp_decode, _graph

        def _fst4_decode(args):
            llr = args.get("llr")
            if not isinstance(llr, list):
                return ToolResult(False, "llr 必须是 240 个信道 LLR（浮点）")
            N, M, _, _ = _graph()
            if len(llr) != N:
                return ToolResult(False, f"需要 {N} 个 LLR，得到 {len(llr)}")
            bits, iters = ldpc_bp_decode([float(x) for x in llr])
            return ToolResult(True, json.dumps({"bits": bits, "iters": iters,
                                               "n": N, "checks": M}))

        self.tool_registry.register(
            name="fst4_ldpc_decode",
            description="FST4 软判决 LDPC (240,101) min-sum BP 译码：输入 240 个信道 LLR，"
                        "输出纠正后的 240 位码字和迭代次数。FST4 物理层（帧/波形/解包后续接）。",
            parameters={
                "type": "object",
                "properties": {"llr": {"type": "array", "items": {"type": "number"},
                                      "description": "240 个信道 LLR"}},
                "required": ["llr"],
            },
            handler=_fst4_decode,
            category="decode",
        )

        def _fst4_encode(args):
            text = (args.get("text") or "").strip()
            if not text:
                return ToolResult(False, "text 不能为空，如 'CQ BI1AAA OM74'")
            from mbdsdr_ai.fst4_encode import encode_fst4_text
            try:
                tones = encode_fst4_text(text)
            except Exception:  # noqa: BLE001
                return ToolResult(
                    False,
                    f"FST4编码失败：消息格式不正确，请使用标准格式如 "
                    f"'CQ BI1AAA OM74'、'BI1AAA K1ABC 73'",
                )
            return ToolResult(True, json.dumps({
                "tones": tones, "n_tones": len(tones),
                "note": "160 个 4FSK 音调索引(0-3)；5 个 8 符号同步块交替 isyncword1/2",
            }, ensure_ascii=False))

        self.tool_registry.register(
            name="fst4_encode",
            description="FST4 编码：把标准文本消息编码成 160 个 4FSK 音调索引。参数依据公开标准 "
                        "（5×8 同步字、2bit Gray 映射、rvec 加扰、"
                        "CRC24、LDPC(240,101)）。与 fst4_ldpc_decode / decode_fst4_message 互为往返。",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "标准消息文本，如 'CQ BI1AAA OM74'"},
                },
                "required": ["text"],
            },
            handler=_fst4_encode,
            category="decode",
        )

    def _register_cw_tools(self):
        """CW 摩尔斯音频解码：音频 -> 文字。"""
        from mbdsdr_ai.cw_decoder import decode_cw

        def _cw(args):
            audio = args.get("audio")
            if not isinstance(audio, list):
                return ToolResult(False, "audio 必须是单声道浮点采样列表")
            sr = float(args.get("sample_rate", 11025) or 11025)
            r = decode_cw([float(x) for x in audio], sr, wpm=args.get("wpm"))
            return ToolResult(True, json.dumps(r, ensure_ascii=False))

        self.tool_registry.register(
            name="cw_decode_audio",
            description="CW（摩尔斯电报）音频解码：输入一段解调后的单声道音频采样，"
                        "自动估计电码速度，输出摩尔斯文本、dit 时长和识别置信度。"
                        "用于 CW 接收技能。",
            parameters={
                "type": "object",
                "properties": {
                    "audio": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number", "description": "默认 11025"},
                    "wpm": {"type": "number", "description": "已知电码速度（可选）"},
                },
                "required": ["audio"],
            },
            handler=_cw,
            category="decode",
        )

    def _register_adsb_tools(self):
        """ADS-B Mode S 1090MHz：从 IQ 找前导、CRC24、解 DF/ICAO/呼号。"""
        from mbdsdr_ai.adsb_lite import decode_adsb
        import numpy as np

        def _adsb(args):
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数采样列表")
            sr = float(args.get("sample_rate", 2_000_000) or 2_000_000)
            try:
                arr = np.array(iq, dtype=complex)
                res = decode_adsb(arr, sr,
                                  threshold_sigma=float(args.get("threshold_sigma", 4.0) or 4.0))
            except Exception as e:
                return ToolResult(False, f"ADS-B 解码失败: {e}")
            return ToolResult(True, json.dumps(res, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="adsb_decode_iq",
            description="ADS-B / Mode S (1090MHz) 接收解码：输入一段复数 IQ，"
                        "自动找前导、做 CRC24 校验，输出每帧的 DF、ICAO 地址、"
                        "呼号、报文类型和校验是否通过，并按 ICAO 汇总飞机。"
                        "用于飞机跟踪/ADS-B 接收技能。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"},
                           "description": "复数 IQ 采样（复数浮点列表）"},
                    "sample_rate": {"type": "number", "description": "采样率 Hz，默认 2000000"},
                    "threshold_sigma": {"type": "number", "description": "检测门限 sigma 倍数，默认 4"},
                },
                "required": ["iq"],
            },
            handler=_adsb,
            category="decode",
        )

    def _register_rds_tools(self):
        """RDS FM 57kHz 副载波：从 MPX 解码 PI/PS/节目名。"""
        from mbdsdr_ai.rds_lite import decode_rds
        import numpy as np

        def _rds(args):
            mpx = args.get("mpx")
            if not isinstance(mpx, list):
                return ToolResult(False, "mpx 必须是实数 MPX 采样列表")
            sr = float(args.get("sample_rate", 1710000) or 1710000)
            try:
                res = decode_rds(np.array(mpx, dtype=np.float64), sr,
                                 min_groups=int(args.get("min_groups", 2) or 2))
            except Exception as e:
                return ToolResult(False, f"RDS 解码失败: {e}")
            return ToolResult(True, json.dumps(res, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="rds_decode_mpx",
            description="FM RDS (57kHz 副载波) 解码：输入一段实数 MPX 复合基带采样，"
                        "做双相码恢复、块同步、CRC，输出电台 PI 码、电台名(PS)、"
                        "节目类型等 RDS 信息。用于 FM 广播/RDS 接收技能。",
            parameters={
                "type": "object",
                "properties": {
                    "mpx": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number", "description": "采样率 Hz，默认 1710000"},
                    "min_groups": {"type": "integer", "description": "最少解码组数，默认 2"},
                },
                "required": ["mpx"],
            },
            handler=_rds,
            category="decode",
        )

    def _register_apt_tools(self):
        """NOAA APT 气象卫星图：音频 -> A/B 双通道灰度图。"""
        from mbdsdr_ai.noaa_apt_lite import decode_apt
        import numpy as np

        def _apt(args):
            audio = args.get("audio")
            if not isinstance(audio, list):
                return ToolResult(False, "audio 必须是单声道浮点采样列表")
            sr = float(args.get("sample_rate", 24000) or 24000)
            try:
                res = decode_apt(np.array(audio, dtype=np.float64), sr,
                                 polarity=int(args.get("polarity", 1) or 1))
            except Exception as e:
                return ToolResult(False, f"APT 解码失败: {e}")
            # 图像数组不直接塞回（太大），回摘要 + 存图
            summary = {k: v for k, v in res.items() if k not in ("image_a", "image_b")}
            return ToolResult(True, json.dumps(summary, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="apt_decode_audio",
            description="NOAA APT 气象卫星云图 (137MHz) 解码：输入一段解调后的单声道音频，"
                        "自动做瞬时频率解调、行同步，输出 A/B 双通道是否检测到 APT、"
                        "对齐行数、锁定质量。用于 NOAA 气象卫星接收技能。",
            parameters={
                "type": "object",
                "properties": {
                    "audio": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number", "description": "默认 24000"},
                    "polarity": {"type": "integer", "description": "真机若反相传 -1"},
                },
                "required": ["audio"],
            },
            handler=_apt,
            category="decode",
        )

    def _register_aprs_tools(self):
        """APRS/AX.25 AFSK 解调：音频 -> 呼号/经纬度/报文。"""
        from mbdsdr_ai.ax25 import AFSKModem
        import numpy as np

        def _aprs(args):
            audio = args.get("audio")
            if not isinstance(audio, list):
                return ToolResult(False, "audio 必须是单声道浮点采样列表")
            sr = float(args.get("sample_rate", 22050) or 22050)
            try:
                m = AFSKModem(sample_rate=sr)
                frames = m.demodulate(np.array(audio, dtype=np.float64))
                out = []
                for f in frames:
                    out.append({
                        "source": getattr(f, "source", None),
                        "source_ssid": getattr(f, "source_ssid", None),
                        "destination": getattr(f, "destination", None),
                        "digipeaters": getattr(f, "digipeaters", None),
                        "info": bytes(getattr(f, "info", b"")).decode("ascii", "replace"),
                        "fcs_valid": getattr(f, "fcs_valid", None),
                    })
            except Exception as e:
                return ToolResult(False, f"APRS 解调失败: {e}")
            return ToolResult(True, json.dumps({"frames": out, "count": len(out)},
                                               ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="aprs_decode_audio",
            description="APRS / AX.25 (AFSK 1200) 解调：输入一段单声道音频，"
                        "做 AFSK 解调、HDLC 位同步、CRC(FCS) 校验，输出每帧的"
                        "源呼号、目的、 digipeater 路径、信息字段（位置/报文/气象）。"
                        "用于 APRS 网络/位置跟踪技能。",
            parameters={
                "type": "object",
                "properties": {
                    "audio": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number", "description": "默认 22050"},
                },
                "required": ["audio"],
            },
            handler=_aprs,
            category="decode",
        )

    def _register_ax25_tools(self):
        """AX.25 / AFSK / APRS 编解码工具（依据 AX.25/AFSK/APRS 公开标准实现）。"""
        import numpy as np
        from mbdsdr_ai.ax25 import (
            AX25Frame, AFSKModem, hdlc_bit_stuff, hdlc_bit_unstuff, crc16_ccitt,
        )
        from mbdsdr_ai.aprs_parser import parse_aprs_frame

        def _encode(args):
            src = args.get("source", "NOCALL")
            dst = args.get("destination", "APRS")
            info = (args.get("info", "") or "").encode("latin-1")
            digis = args.get("digipeaters", []) or []
            f = AX25Frame(destination=dst, source=src, digipeaters=[(d, 0, False) for d in digis],
                          control=0x03, pid=0xF0, info=info)
            blob = f.to_bytes()
            return ToolResult(True, json.dumps({
                "hex": blob.hex(), "fcs": f"0x{f.fcs:04X}", "len": len(blob),
                "stuffed_hex": hdlc_bit_stuff(blob).hex(),
            }, ensure_ascii=False))
        self.tool_registry.register(
            name="ax25_encode",
            description="AX.25 UI 帧编码：源/目的呼号 + digipeater 路径 + 信息字段，"
                        "生成含 CRC-16/X.25 FCS 的字节流（含 HDLC 位填充）。"
                        "参数与 direwolf ax25_pad.c/fcs_calc.c 一致。",
            parameters={"type": "object", "properties": {
                "source": {"type": "string"}, "destination": {"type": "string"},
                "info": {"type": "string", "description": "APRS 信息字段文本"},
                "digipeaters": {"type": "array", "items": {"type": "string"}},
            }, "required": ["source"]},
            handler=_encode, category="encode")

        def _decode(args):
            blob, err = hex_bytes(args, "hex")
            if err:
                return ToolResult(False, f"错误: {err}")
            f = AX25Frame.from_bytes(blob)
            if f is None:
                return ToolResult(False, "帧过短或解析失败")
            notes = []
            if not f.digipeaters:
                notes.append("无 digipeater 中继路径（地址字段只含源/目的呼号）")
            if not f.fcs_valid:
                notes.append("FCS(CRC-16/X.25) 校验未通过，帧可能被截断或误码")
            return ToolResult(True, json.dumps({
                "source": f.source, "destination": f.destination,
                "digipeaters": [c for c, _, _ in f.digipeaters],
                "info": f.info.decode("latin-1", "replace"),
                "fcs_valid": f.fcs_valid,
                "note": "；".join(notes),
            }, ensure_ascii=False))
        self.tool_registry.register(
            name="ax25_decode",
            description="AX.25 帧解码：从字节流解析源/目的/digipeater/控制/PID/信息字段，"
                        "并用 CRC-16/X.25 (FCS) 校验。",
            parameters={"type": "object", "properties": {
                "hex": {"type": "string", "description": "帧字节十六进制(不含标志)"},
            }, "required": ["hex"]},
            handler=_decode, category="decode")

        def _modulate(args):
            try:
                f = AX25Frame(destination=args.get("destination", "APRS"),
                              source=args.get("source", "NOCALL"),
                              control=0x03, pid=0xF0,
                              info=(args.get("info", "") or "").encode("latin-1"))
                sr = float(args.get("sample_rate", 48000))
                audio = AFSKModem(sample_rate=sr).modulate(f)
            except Exception:  # noqa: BLE001
                return ToolResult(False, "AFSK 调制失败：参数格式不正确，请检查 source/destination 呼号格式")
            # payload 进 data（完整音频，可直接回灌给 afsk_demodulate）；
            # content 只给摘要。之前音频被降采样到 2000 点且只在
            # content 里，导致“编码→解码”链路无法回灌。
            return ToolResult(True, json.dumps({
                "sample_rate": sr, "samples": len(audio),
                "hint": "完整音频见 data.audio（未降采样），可直接传给 afsk_demodulate",
            }, ensure_ascii=False), data={
                "sample_rate": sr, "samples": len(audio),
                "audio": np.asarray(audio, dtype=float).round(6).tolist(),
            })
        self.tool_registry.register(
            name="afsk_modulate",
            description="AFSK 1200 Bell 202 调制：把 AX.25 帧调制成 mark=1200Hz/space=2200Hz "
                        "音频（NRZI）。参数来源 direwolf audio.h:470-472。",
            parameters={"type": "object", "properties": {
                "source": {"type": "string"}, "destination": {"type": "string"},
                "info": {"type": "string"}, "sample_rate": {"type": "number"},
            }, "required": ["source"]},
            handler=_modulate, category="encode")

        def _demodulate(args):
            audio = args.get("audio")
            if not isinstance(audio, list):
                return ToolResult(False, "audio 必须是单声道浮点采样列表")
            sr = float(args.get("sample_rate", 48000))
            frames = AFSKModem(sample_rate=sr).demodulate(np.array(audio, dtype=np.float64))
            out = [{"source": f.source, "destination": f.destination,
                    "info": f.info.decode("latin-1", "replace"), "fcs_valid": f.fcs_valid}
                   for f in frames]
            return ToolResult(True, json.dumps({"frames": out, "count": len(out)},
                                               ensure_ascii=False),
                              data={"frames": out, "count": len(out)})
        self.tool_registry.register(
            name="afsk_demodulate",
            description="AFSK 1200 Bell 202 解调：音频 -> 鉴频 -> 数字 PLL 位同步 -> "
                        "HDLC 去位填充 -> CRC-16/X.25 校验，输出 AX.25 帧列表。",
            parameters={"type": "object", "properties": {
                "audio": {"type": "array", "items": {"type": "number"}},
                "sample_rate": {"type": "number"},
            }, "required": ["audio"]},
            handler=_demodulate, category="decode")

        def _parse(args):
            blob, err = hex_bytes(args, "hex")
            if err:
                return ToolResult(False, f"错误: {err}")
            f = AX25Frame.from_bytes(blob)
            if f is None:
                return ToolResult(False, "帧解析失败")
            return ToolResult(True, json.dumps(parse_aprs_frame(f),
                                               ensure_ascii=False, default=str))
        self.tool_registry.register(
            name="aprs_parse",
            description="APRS 报文解析：从 AX.25 帧解析位置(未压缩/压缩/MIC-E)、气象、"
                        "遥测、消息，输出结构化 dict（依据 APRS 公开报文格式标准）。",
            parameters={"type": "object", "properties": {
                "hex": {"type": "string", "description": "AX.25 帧字节十六进制"},
            }, "required": ["hex"]},
            handler=_parse, category="decode")

    def _register_analog_demod_tools(self):
        """SDR++ 核心：IQ -> AM/FM/SSB 音频解调。"""
        from mbdsdr_ai.analog_demod import demod_analog
        import numpy as np

        def _demod(args):
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数采样列表")
            sr = float(args.get("sample_rate", 0) or 0)
            if sr <= 0:
                return ToolResult(False, "需要 sample_rate")
            try:
                arr = np.array(iq, dtype=complex)
                r = demod_analog(
                    arr, sr,
                    mode=str(args.get("mode", "fm") or "fm"),
                    max_dev=float(args.get("max_dev", 5000) or 5000),
                    audio_bw=float(args.get("audio_bw", 3000) or 3000),
                )
            except Exception as e:
                return ToolResult(False, f"解调失败: {e}")
            # 音频数组大，只回摘要（前端要音频时再取）
            audio_len = len(r.pop("audio"))
            summary = {**r, "audio_samples": audio_len}
            return ToolResult(True, json.dumps(summary, ensure_ascii=False))

        self.tool_registry.register(
            name="demod_analog_audio",
            description="模拟音频解调（SDR++ 核心）：输入一段复数 IQ，"
                        "按模式解调成单声道音频——am=包络检波、fm=相位差分鉴频、"
                        "usb/lsb=边带。用于听广播/听通话。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number"},
                    "mode": {"type": "string", "enum": ["am", "fm", "usb", "lsb"],
                             "description": "默认 fm"},
                    "max_dev": {"type": "number", "description": "FM 最大频偏 Hz，默认 5000"},
                    "audio_bw": {"type": "number", "description": "音频带宽 Hz，默认 3000"},
                },
                "required": ["iq", "sample_rate"],
            },
            handler=_demod,
            category="demod",
        )

    def _register_acars_tools(self):
        """ACARS 航空通信：MSK 解调 + 帧同步 + CRC16 消息解析。
        依据 ACARS 公开航空通信标准独立实现（acarsdec/libacars 仅作技术参考）。"""
        from mbdsdr_ai.acars_decoder import (
            acars_decode_iq, acars_parse_message, acars_msk_demod,
        )

        def _decode(args):
            audio = args.get("audio")
            if not isinstance(audio, list):
                return ToolResult(False, "audio 必须是实数 FM 解调音频列表")
            sr = float(args.get("sample_rate", 12500) or 12500)
            try:
                res = acars_decode_iq(audio, sr, 1200)
            except Exception as e:
                return ToolResult(False, f"ACARS 解码失败: {e}")
            if not res:
                # 空结果必须说清楚原因，不能只回 "[]" 让模型误以为解码成功但没内容
                return ToolResult(
                    True,
                    "未检测到 ACARS 帧（诚实空态：输入音频无同步/CRC 未通过；"
                    "请用含真实报文的中频录音，合成正弦不会被当作报文）",
                )
            return ToolResult(True, json.dumps(res, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="acars_decode_iq",
            description="ACARS 航空通信寻址与报告系统 (VHF ~131.5MHz) 解码："
                        "输入 FM 解调后的单声道音频，做 MSK 解调(1200bps, "
                        "mark=2400/space=1200Hz)、双 SYN(0x16) 帧同步、CRC16-CCITT "
                        "校验，输出飞机注册号(reg)、模式(mode)、标签(label)、"
                        "块序号、航班号与文本内容（依据 ACARS 公开标准）。",
            parameters={
                "type": "object",
                "properties": {
                    "audio": {"type": "array", "items": {"type": "number"},
                              "description": "FM 解调后的实数音频采样"},
                    "sample_rate": {"type": "number", "description": "采样率 Hz，默认 12500"},
                },
                "required": ["audio"],
            },
            handler=_decode,
            category="decode",
        )

        def _parse(args):
            body = args.get("body")
            if not isinstance(body, list):
                return ToolResult(False, "body 必须是整数字节列表")
            try:
                res = acars_parse_message(body)
            except Exception as e:
                return ToolResult(False, f"ACARS 解析失败: {e}")
            return ToolResult(True, json.dumps(res, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="acars_parse_message",
            description="ACARS 消息字节级解析：输入 SOH 之后的原始字节流"
                        "(含 CRC 与 DEL 结尾)，做奇偶位剥离、CRC16-CCITT 校验、"
                        "字段拆分(mode/reg/ack/label/block_id/flight/text)。"
                        "（ACARS 报文格式，依据公开标准）。",
            parameters={
                "type": "object",
                "properties": {
                    "body": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["body"],
            },
            handler=_parse,
            category="decode",
        )

        def _demod(args):
            audio = args.get("audio")
            if not isinstance(audio, list):
                return ToolResult(False, "audio 必须是实数音频列表")
            sr = float(args.get("sample_rate", 12500) or 12500)
            try:
                bits = acars_msk_demod(audio, sr, 1200)
            except Exception as e:
                return ToolResult(False, f"MSK 解调失败: {e}")
            return ToolResult(True, json.dumps({"bits": bits[:512], "n_bits": len(bits)}))

        self.tool_registry.register(
            name="acars_msk_demod",
            description="ACARS MSK 解调：实数音频 -> 恢复比特序列(LSB-first)。"
                        "mark=2400Hz 判 1，space=1200Hz 判 0，1200bps。"
                        "（ACARS MSK 调制解调，依据公开标准）。",
            parameters={
                "type": "object",
                "properties": {
                    "audio": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number", "description": "默认 12500"},
                },
                "required": ["audio"],
            },
            handler=_demod,
            category="decode",
        )

    def _register_wfm_stereo_tools(self):
        """WFM 立体声：IQ -> 19kHz pilot 提取 -> L/R。"""
        from mbdsdr_ai.wfm_stereo_lite import decode_stereo
        import numpy as np

        def _wfm(args):
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数采样列表")
            sr = float(args.get("sample_rate", 240000) or 240000)
            try:
                res = decode_stereo(np.array(iq, dtype=complex), sr)
            except Exception as e:
                return ToolResult(False, f"WFM 立体声解码失败: {e}")
            summary = {k: v for k, v in res.items() if k not in ("l", "r")}
            return ToolResult(True, json.dumps(summary, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="demod_wfm_stereo",
            description="FM 立体声广播解调：输入 WFM 复基带 IQ，做鉴频、提取 19kHz "
                        "导频、二倍频恢复 38kHz 副载波，分离出 L/R 立体声；"
                        "导频能量不足时回退单声道。用于听 FM 立体声广播。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number", "description": "默认 240000"},
                },
                "required": ["iq", "sample_rate"],
            },
            handler=_wfm,
            category="demod",
        )

    def _register_signal_quality_tools(self):
        """IQ 信号质量/星座统计。"""
        from mbdsdr_ai.signal_quality import signal_quality
        import numpy as np

        def _sq(args):
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数采样列表")
            if not iq:
                return ToolResult(False, "iq 不能为空数组，请提供至少一个复数采样")
            try:
                res = signal_quality(np.array(iq, dtype=complex))
            except Exception as e:
                return ToolResult(False, f"信号质量统计失败: {clean_exc(e)}")
            return ToolResult(True, json.dumps(res, ensure_ascii=False))

        self.tool_registry.register(
            name="signal_quality_stats",
            description="信号质量/星座统计：输入一段复 IQ，输出 DC 偏移、I/Q 不平衡、"
                        "RMS、峰均比(PAPR)、平均相位等，用于诊断硬件（增益/正交误差）和"
                        "判断信号质量。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["iq"],
            },
            handler=_sq,
            category="spectrum",
        )

    def _register_orbit_tools(self):
        """新时空核心：TLE 卫星过境预测（仰角/方位/多普勒）。"""
        from mbdsdr_ai.orbit import predict_passes, TimeBasisUnavailable

        def _passes(args):
            try:
                # 站址：参数(user) > 配置(config)；只读计算口径：
                # 缺失/缺省（真没配）拒算，显式 (0,0)/海上坐标放行并带 warning。
                from mbdsdr_ai.station import (trust_for_calculation, resolve_station,
                                               station_from_config, station_from_user)
                lat_arg = args.get("observer_lat")
                lon_arg = args.get("observer_lon")
                user = (station_from_user(float(lat_arg), float(lon_arg),
                                          float(args.get("observer_alt", 0) or 0),
                                          origin="tool_args")
                        if lat_arg is not None and lon_arg is not None else None)
                fix = resolve_station(user=user, config=station_from_config())
                ev = trust_for_calculation(fix)
                if not ev["trusted"]:
                    return ToolResult(False, "站址不可信，拒绝计算过境：" +
                                      "；".join(ev["reasons"]) +
                                      "。请显式传入可信 observer_lat/observer_lon。")
                lat = float(fix.lat)
                lon = float(fix.lon)
                # orbit.predict_passes 的海拔口径是**公里**（内部
                # geodetic_to_ecef 用 WGS-72 km，与 sgp4/TEME 同量纲），
                # 而站址配置存的是米。2026-11-09 真机复核抓到：这里原来
                # 直接传米，高度非零时站址被抬高 1000 倍，仰角错到几十度、
                # 升起时刻错到小时级（高度为 0 时看不出来，所以一直没暴露）。
                # 与 sdr_tools / main_window 的 alt_m/1000 口径对齐。
                alt = float(fix.alt_m or 0) / 1000.0
                hours = float(args.get("hours", 24) or 24)
                min_el = float(args.get("min_elevation", 10) or 10)
                stype = str(args.get("satellite_type", "all") or "all")
                freq = float(args.get("frequency_hz", 137.1e6) or 137.1e6)
                # P11：时间基准由 orbit 统一解析（与天空视图同一 TimeEngine）。
                # 引擎不可用时必须如实说「算不了」——绝不退回墙钟给出一个
                # 看起来正常的过境时刻。
                try:
                    ps = predict_passes(lat, lon, alt, hours, min_el, stype,
                                        nominal_freq_hz=freq)
                except TimeBasisUnavailable as e:
                    return ToolResult(False, f"时间基准不可用，无法计算过境：{e}")
            except Exception as e:
                return ToolResult(False, f"过境预测失败: {e}")
            return ToolResult(True, json.dumps(
                {"passes": ps, "count": len(ps),
                 "station_warning": ev.get("station_warning") or "",
                 # 时刻来源与采样精度：AI 回答里贴出的时刻必须能追问出处，
                 # 不能与面板的 60 秒网格（±30 秒）混为一谈。
                 "pass_time_source": (
                     "sat_passes.predict_passes（60 秒粗扫 + 2 秒细扫 + 0.5 秒二分）"),
                 "pass_time_sampling_precision_s": 1.0},
                ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="predict_satellite_passes",
            description="卫星过境预测（新时空核心）：给定观察者经纬度，预测未来 N 小时内"
                        "气象/业余卫星的过境时刻、最大仰角、升落时间和多普勒频移范围。"
                        "用于 AI 反向指挥人架天线/调谐/录制。",
            parameters={
                "type": "object",
                "properties": {
                    "observer_lat": {"type": "number", "description": "观察者纬度（未传则从配置读取）"},
                    "observer_lon": {"type": "number", "description": "观察者经度（未传则从配置读取）"},
                    "observer_alt": {"type": "number", "description": "海拔 m，默认 0"},
                    "hours": {"type": "number", "description": "预测时长小时，默认 24"},
                    "min_elevation": {"type": "number", "description": "最低仰角度，默认 10"},
                    "satellite_type": {"type": "string", "enum": ["all", "weather", "amateur"]},
                    "frequency_hz": {"type": "number",
                                     "description": "观测频率 Hz，用于多普勒频移范围计算（不传则用卫星默认频率）"},
                },
            },
            handler=_passes,
            category="orbit",
        )

    def _register_pointing_tools(self):
        """新时空：当前时刻卫星位置 + 天线指向建议（AI 反向指挥人）。"""
        from mbdsdr_ai.orbit import (compute_satellite_state, BUILTIN_SATS,
                                     TimeBasisUnavailable)

        def _point(args):
            try:
                name = str(args.get("satellite", "NOAA 19"))
                from mbdsdr_ai.station import (trust_for_calculation, resolve_station,
                                               station_from_config, station_from_user)
                lat_arg = args.get("observer_lat")
                lon_arg = args.get("observer_lon")
                user = (station_from_user(float(lat_arg), float(lon_arg),
                                          origin="tool_args")
                        if lat_arg is not None and lon_arg is not None else None)
                fix = resolve_station(user=user, config=station_from_config())
                ev = trust_for_calculation(fix)
                if not ev["trusted"]:
                    return ToolResult(False, "站址不可信，拒绝计算指向：" +
                                      "；".join(ev["reasons"]))
                lat = float(fix.lat)
                lon = float(fix.lon)
                try:
                    s = compute_satellite_state(name, lat, lon)
                except TimeBasisUnavailable as e:
                    return ToolResult(False, f"时间基准不可用，无法计算卫星位置：{e}")
                if s is None:
                    return ToolResult(False, f"未知卫星: {name}")
                visible = s["elevation"] >= 0
                # AI 给人的指向指令
                if visible:
                    cmd = (f"把天线指向方位 {s['azimuth']:.0f}°、仰角 {s['elevation']:.0f}°"
                           f"（卫星距离 {s['range_km']:.0f} km）")
                else:
                    cmd = (f"卫星现在在地平线下（仰角 {s['elevation']:.0f}°），"
                           f"方位 {s['azimuth']:.0f}°，等过境再架天线")
                s["visible"] = visible
                s["instruction"] = cmd
                if ev.get("station_warning"):
                    s["station_warning"] = ev["station_warning"]
            except Exception as e:
                return ToolResult(False, f"指向计算失败: {e}")
            return ToolResult(True, json.dumps(s, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="satellite_now_pointing",
            description="当前时刻卫星位置与天线指向建议：给定卫星和观察者经纬度，"
                        "输出方位角、仰角、距离、是否可见，并直接生成一句给人听的"
                        "操作指令（把天线架到哪个方位仰角）。这是 AI 反向指挥人架天线的核心。",
            parameters={
                "type": "object",
                "properties": {
                    "satellite": {"type": "string",
                                  "description": "如 NOAA 19/NOAA 15/ISS (ZARYA)/METEOR M2/FENGYUN 3D"},
                    "observer_lat": {"type": "number"},
                    "observer_lon": {"type": "number"},
                },
            },
            handler=_point,
            category="orbit",
        )

        def _gimbal_move(args):
            try:
                from mbdsdr_ai.gimbal import RotctldClient
                c = RotctldClient(host=args.get("host", "127.0.0.1"),
                                  port=int(args.get("port", 4533)))
                az = float(args["azimuth_deg"])
                el = float(args["elevation_deg"])
                ok = c.set_position(az, el)
                if ok:
                    return ToolResult(True, f"云台已指向 方位{az:.0f}° 仰角{el:.0f}°")
                return ToolResult(False, "云台连接失败，检查 rotctld 是否运行")
            except Exception as e:
                return ToolResult(False, f"云台控制失败: {clean_exc(e)}")

        self.tool_registry.register(
            name="gimbal_move",
            description="控制 rotctld 云台/旋转器指向指定方位仰角。用于自动跟踪卫星过境时驱动天线对准。",
            parameters={
                "type": "object",
                "properties": {
                    "azimuth_deg": {"type": "number", "description": "方位角 0-360"},
                    "elevation_deg": {"type": "number", "description": "仰角 -5-180"},
                    "host": {"type": "string", "default": "127.0.0.1"},
                    "port": {"type": "integer", "default": 4533},
                },
                "required": ["azimuth_deg", "elevation_deg"],
            },
            handler=_gimbal_move,
            category="pointing",
        )

    def _register_doppler_tools(self):
        """新时空：多普勒补偿调谐频率。"""
        from mbdsdr_ai.orbit import doppler_correction, TimeBasisUnavailable

        def _doppler(args):
            try:
                name = str(args.get("satellite", "NOAA 15"))
                f0 = float(args.get("nominal_freq_hz", 137.62e6))
                cfg_lat, cfg_lon = MBDSDRAgent._get_ground_station_latlon()
                lat = args.get("observer_lat", cfg_lat)
                lon = args.get("observer_lon", cfg_lon)
                if lat is None or lon is None:
                    return ToolResult(
                        False,
                        "未配置地面站坐标。请在参数中传入 observer_lat/observer_lon，"
                        "或在 ~/.mbdsdr/config.json 中设置 ground_station_lat / ground_station_lon。",
                    )
                lat = float(lat)
                lon = float(lon)
                try:
                    r = doppler_correction(name, f0, lat, lon)
                except TimeBasisUnavailable as e:
                    return ToolResult(False, f"时间基准不可用，无法计算多普勒：{e}")
            except Exception as e:
                return ToolResult(False, f"多普勒计算失败: {e}")
            return ToolResult(True, json.dumps(r, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="doppler_tune_frequency",
            description="多普勒补偿调谐：给定卫星标称频率和观察者位置，根据卫星视线速度"
                        "算出当前多普勒频移和应调到的接收频率。接收卫星/发射前自动补偿频偏。",
            parameters={
                "type": "object",
                "properties": {
                    "satellite": {"type": "string"},
                    "nominal_freq_hz": {"type": "number", "description": "卫星标称频率 Hz，默认 137.62e6"},
                    "observer_lat": {"type": "number"},
                    "observer_lon": {"type": "number"},
                },
            },
            handler=_doppler,
            category="orbit",
        )

    def _register_time_sync_tools(self):
        """新时空授时：NTP 校时。"""
        from mbdsdr_ai.time_sync import ntp_offset

        def _ntp(args):
            server = str(args.get("server", "ntp.aliyun.com"))
            try:
                r = ntp_offset(server, timeout=float(args.get("timeout", 3) or 3))
            except Exception as e:
                return ToolResult(False, f"NTP 查询失败: {e}")
            # 不可达/离线时不能报 success=True（那会变成「假装成功」）
            if not r.get("reachable"):
                return ToolResult(
                    False,
                    f"NTP 查询失败: {r.get('error') or '服务器不可达'}",
                    data={"ntp": r})
            return ToolResult(True, json.dumps(r, ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="time_sync_ntp",
            description="NTP 授时：查询 NTP 服务器，返回本地时钟与标准时间的偏差。"
                        "新时空系统的授时基础（卫星过境/多普勒计算都依赖准确时间）。",
            parameters={
                "type": "object",
                "properties": {
                    "server": {"type": "string", "description": "NTP 服务器，默认 ntp.aliyun.com"},
                    "timeout": {"type": "number"},
                },
            },
            handler=_ntp,
            category="orbit",
        )

    def _register_constellation_tools(self):
        """星座图 EVM 统计。"""
        from mbdsdr_ai.constellation import evm_qpsk
        import numpy as np

        def _evm(args):
            sym = args.get("symbols")
            if not isinstance(sym, list):
                return ToolResult(False, "symbols 必须是复数符号列表")
            try:
                r = evm_qpsk(np.array(sym, dtype=complex))
            except Exception as e:
                return ToolResult(False, f"EVM 计算失败: {e}")
            return ToolResult(True, json.dumps(r, ensure_ascii=False))

        self.tool_registry.register(
            name="constellation_evm",
            description="星座图 EVM 统计：输入一段 QPSK 软符号（复数），算每符号到理想星座点的"
                        "RMS 误差向量幅度(EVM%)和 EVM dB，并给出四象限聚类中心。用于诊断"
                        "数字信号质量/解调好坏。",
            parameters={
                "type": "object",
                "properties": {
                    "symbols": {"type": "array", "items": {"type": "number"}},
                },
                "required": ["symbols"],
            },
            handler=_evm,
            category="spectrum",
        )

        def _squelch(args):
            from mbdsdr_ai.signal_quality import squelch_gate
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数 IQ 采样列表")
            if not iq:
                return ToolResult(False, "iq 不能为空数组，请提供至少一个复数采样")
            th = float(args.get("threshold_db", -50.0))
            blk = int(args.get("block", 1024))
            try:
                r = squelch_gate(np.array(iq, dtype=complex),
                                 threshold_db=th, block=blk)
            except Exception as e:
                return ToolResult(False, f"静噪门控失败: {clean_exc(e)}")
            return ToolResult(True, json.dumps(r, ensure_ascii=False))

        self.tool_registry.register(
            name="sdr_squelch_gate",
            description="静噪门控：对一段复 IQ 按块算 RSSI(dBFS)，判断是否超过门控并给占空比。"
                        "用于自动值守、活动检测、降低无信号时的噪音输出。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                    "threshold_db": {"type": "number", "description": "门控阈值 dBFS"},
                    "block": {"type": "integer", "description": "每块采样数"},
                },
                "required": ["iq"],
            },
            handler=_squelch,
            category="spectrum",
        )

        def _anr(args):
            from mbdsdr_ai.dsp import anr_denoise
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数 IQ 采样列表")
            try:
                out = anr_denoise(
                    np.array(iq, dtype=complex),
                    frame=int(args.get("frame", 256)),
                    hop=int(args.get("hop", 128)),
                    noise_frames=int(args.get("noise_frames", 10)),
                    oversub=float(args.get("oversub", 2.0)),
                )
            except Exception as e:
                return ToolResult(False, f"ANR 降噪失败: {e}")
            return ToolResult(True, json.dumps({
                "denoised_iq": [[float(v.real), float(v.imag)] for v in out],
                "input_samples": len(iq),
                "output_samples": len(out),
            }))

        self.tool_registry.register(
            name="sdr_anr_denoise",
            description="自动降噪(ANR)：STFT 谱减。用前若干帧估计噪声谱，逐帧减去，"
                        "保留相位。建议开头留一段纯噪声供估计。输出降噪后 IQ。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                    "frame": {"type": "integer"},
                    "hop": {"type": "integer"},
                    "noise_frames": {"type": "integer"},
                    "oversub": {"type": "number"},
                },
                "required": ["iq"],
            },
            handler=_anr,
            category="spectrum",
        )

        def _scatter(args):
            from mbdsdr_ai.constellation import scatter_points
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数 IQ 采样列表")
            try:
                r = scatter_points(np.array(iq, dtype=complex),
                                   n_points=int(args.get("n_points", 1024)))
            except Exception as e:
                return ToolResult(False, f"星座散点提取失败: {e}")
            return ToolResult(True, json.dumps(r, ensure_ascii=False))

        self.tool_registry.register(
            name="sdr_constellation_scatter",
            description="实时星座散点数据：把一段复 IQ 去 DC、自动增益归一化后输出可直接画散点的"
                        "(I,Q) 坐标列表，供 UI 星座图控件使用。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                    "n_points": {"type": "integer"},
                },
                "required": ["iq"],
            },
            handler=_scatter,
            category="spectrum",
        )

        def _peaks(args):
            from mbdsdr_ai.dsp import find_spectrum_peaks
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数 IQ 采样列表")
            try:
                r = find_spectrum_peaks(
                    np.array(iq, dtype=complex),
                    sample_rate=float(args.get("sample_rate", 2.4e6)),
                    n_peaks=int(args.get("n_peaks", 10)),
                    rel_threshold_db=float(args.get("rel_threshold_db", 15.0)),
                )
            except Exception as e:
                return ToolResult(False, f"谱峰搜索失败: {e}")
            return ToolResult(True, json.dumps(r, ensure_ascii=False))

        self.tool_registry.register(
            name="sdr_spectrum_peaks",
            description="频谱活动扫描/找台：对一段复 IQ 的幅度谱找显著峰，输出各峰相对中心的频率(Hz)和"
                        "功率(dB)，按功率降序。用于自动发现活跃频率。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number"},
                    "n_peaks": {"type": "integer"},
                    "rel_threshold_db": {"type": "number"},
                },
                "required": ["iq"],
            },
            handler=_peaks,
            category="spectrum",
        )

        def _overview(args):
            """一次信号体检：制式识别 + 谱峰 + 质量摘要。"""
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数 IQ 采样列表")
            if not iq:
                return ToolResult(False, "iq 不能为空数组，请提供至少一个复数采样")
            sr = float(args.get("sample_rate", 2.4e6))
            try:
                from mbdsdr_ai.signal_analysis import identify_modulation
                from mbdsdr_ai.dsp import find_spectrum_peaks
                from mbdsdr_ai.signal_quality import signal_quality
                x = np.array(iq, dtype=complex)
                mod = identify_modulation(x, sr)
                peaks = find_spectrum_peaks(x, sr,
                                            n_peaks=int(args.get("n_peaks", 5)))
                qual = signal_quality(x)
            except Exception as e:
                return ToolResult(False, f"信号体检失败: {clean_exc(e)}")
            return ToolResult(True, json.dumps({
                "modulation": mod,
                "peaks": peaks,
                "quality": qual,
            }, ensure_ascii=False))

        self.tool_registry.register(
            name="sdr_signal_overview",
            description="信号体检：对一段复 IQ 一次返回制式识别 + 显著谱峰频率 + 信号质量摘要。"
                        "AI 对话中快速'看一眼这是什么信号'。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                    "sample_rate": {"type": "number"},
                    "n_peaks": {"type": "integer"},
                },
                "required": ["iq"],
            },
            handler=_overview,
            category="spectrum",
        )

    def _register_baseband_tools(self):
        """Baseband 录制/回放。"""
        from mbdsdr_ai.baseband_io import save_iq
        # 统一默认采样率（修复#8）：值取自项目常量，当前 2.400 MS/s。
        # 局部导入避免循环依赖。
        from mbdsdr_ai.sdr_backend import DEFAULT_SAMPLE_RATE_HZ

        def _save(args):
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数采样列表")
            try:
                # 默认落盘路径改跨平台临时目录，不再写死 /tmp
                default_path = os.path.join(tempfile.gettempdir(), "mbdsdr_capture.iq")
                info = save_iq(
                    iq,
                    str(args.get("path", default_path)),
                    float(args.get("sample_rate", DEFAULT_SAMPLE_RATE_HZ)),
                    center_freq_hz=float(args.get("center_freq_hz", 0)),
                    note=str(args.get("note", "")),
                )
            except Exception as e:
                return ToolResult(False, f"录制失败: {e}")
            return ToolResult(True, json.dumps(info, ensure_ascii=False))

        self.tool_registry.register(
            name="baseband_save",
            description="Baseband 录制：把一段复数 IQ 存成二进制文件（float32 交错），"
                        "记录采样率、中心频率、时长，供事后回放分析。",
            parameters={
                "type": "object",
                "properties": {
                    "iq": {"type": "array", "items": {"type": "number"}},
                    "path": {"type": "string", "description": "保存路径，默认系统临时目录 mbdsdr_capture.iq"},
                    "sample_rate": {"type": "number"},
                    "center_freq_hz": {"type": "number"},
                    "note": {"type": "string"},
                },
                "required": ["iq", "sample_rate"],
            },
            handler=_save,
            category="capture",
        )

    def _register_memory_tools(self):
        """注册记忆读写工具。"""
        self.tool_registry.register(
            name="memory_write",
            description="写入一条长期记忆。用于记住用户偏好、常用频率、重要设置等跨会话信息。",
            parameters={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "记忆内容"},
                    "category": {"type": "string", "description": "类别: preference/frequency/setting/fact/task/general", "default": "general"},
                    "importance": {"type": "number", "description": "重要性 0.0-1.0", "default": 0.5},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签"},
                },
                "required": ["content"],
            },
            handler=lambda args: ToolResult(
                success=True,
                content=f"记忆已写入: {self.memory.add(**args).content}",
            ),
            category="memory",
        )

        self.tool_registry.register(
            name="memory_search",
            description="搜索长期记忆。用于检索用户之前的偏好、设置、历史信息。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                    "category": {"type": "string", "description": "按类别过滤"},
                    "limit": {"type": "integer", "description": "返回条数", "default": 5},
                },
                "required": ["query"],
            },
            handler=lambda args: ToolResult(
                success=True,
                content=json.dumps(
                    [m.to_dict() for m in self.memory.search(**args)],
                    ensure_ascii=False, indent=2,
                ),
            ),
            category="memory",
        )

    def _workflow_tool_executor(self, tool_name: str, params: Dict[str, Any]) -> ToolResult:
        """工作流引擎的工具执行器（桥接到 Agent 的工具注册表）。

        D6 接真实：成功时返回 ToolResult 本体（而非 content 散文字符串），workflow_engine
        据此读取 .content（人类可读结果）与 .data（结构化 sidecar 字典）做跨步 {{var}}
        回填。失败抛异常，由 workflow_engine 走重试/失败分支。
        """
        t0 = time.time()
        result = self.tool_registry.call(tool_name, params)
        # 录制工作流：记录本次工具调用（D1 接真实）。
        # 仅当 workflow_record_start 已开始录制时落步骤；未在录制时 record_tool_call 内部直接返回，
        # 因此这是真实记录而非假数据。参数对照 workflow_recorder.py: record_tool_call(tool_name,
        # parameters, result:str, success:bool, duration_ms)。
        try:
            self.workflow_recorder.record_tool_call(
                tool_name,
                params,
                str(result.content),
                result.success,
                duration_ms=(time.time() - t0) * 1000.0,
            )
        except Exception:
            pass
        if result.success:
            return result
        else:
            raise Exception(result.error or f"工具 {tool_name} 执行失败")

    def _register_guardian_tools(self):
        """注册守护者快照工具。"""
        g = self.guardian
        self.tool_registry.register(
            name="guardian_status",
            description="查看守护者快照状态（总快照数、已提交、已回滚、回滚率）。守护者是防幻觉变砖的核心防线，任何 AI 自修改前都会自动快照。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=g.get_status_text()),
            category="guardian",
        )
        self.tool_registry.register(
            name="guardian_rollback",
            description="一键恢复：回滚到上一个已提交的快照。当 AI 自修改导致系统异常时，调用此工具立即恢复。",
            parameters={"type": "object", "properties": {"snap_id": {"type": "string", "description": "指定回滚到的快照 ID（默认最近一个已提交快照）"}}, "required": []},
            handler=lambda args: ToolResult(success=True, content=g.rollback(args.get("snap_id"))[1] if args.get("snap_id") else g.rollback_to_last_committed()[1]),
            category="guardian",
        )
        self.tool_registry.register(
            name="guardian_list",
            description="列出守护者快照历史（最近 20 个），查看每个快照的状态（created/committed/rolled_back）。",
            parameters={"type": "object", "properties": {"phase": {"type": "string", "description": "按状态过滤: created/committed/rolled_back"}}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(g.list_snapshots(phase=args.get("phase"), limit=20), ensure_ascii=False, indent=2)),
            category="guardian",
        )

    def _register_workflow_tools(self):
        """注册工作流工具。"""
        wf = self.workflow_engine
        self.tool_registry.register(
            name="workflow_list",
            description="列出当前可用的用户/AI 自定义工作流（引擎不内置任何固定流程；多步编排通常应由你按任务即时组合工具完成，工作流仅作可选素材）。",
            parameters={"type": "object", "properties": {"category": {"type": "string", "description": "按类别过滤"}}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(wf.list_workflows(category=args.get("category")), ensure_ascii=False, indent=2)),
            category="workflow",
        )
        self.tool_registry.register(
            name="workflow_execute",
            description="按名称执行一个用户/AI 自定义工作流（会按步骤调用工具）。多数任务应由你即时编排工具完成；仅当用户明确要求运行某个已定义工作流时使用。",
            parameters={
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string", "description": "工作流名称（workflow_list 可查；引擎无内置流程，未知名称返回诚实错误）"},
                    "parameters": {"type": "object", "description": "工作流参数（覆盖默认值）"},
                },
                "required": ["workflow_name"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(wf.execute(args["workflow_name"], args.get("parameters", {})).to_dict(), ensure_ascii=False, indent=2)),
            category="workflow",
        )

    def _register_ui_action_tools(self):
        """注册动态 UI 工具（模型 → 界面的最小真闭环）。

        模型调用 ui_layout_control 发出界面动作（显示/隐藏面板、切换/聚焦
        页签、调整三栏比例），动作经 ui_actions.UiActionBus 校验入队，
        由 desktop 主窗口在 UI 线程排水执行；非桌面环境诚实 UNAVAILABLE。
        界面动作不走 arm_manual（不动硬件/文件），但全部可撤销
        （ui_layout_undo 弹逆栈）。不做 23 组件铺满，先三个动词族做真。
        """
        from .ui_actions import get_ui_bus
        bus = get_ui_bus()

        def _control(args):
            action = {"verb": args.get("verb", ""),
                      "target": args.get("target", ""),
                      "value": args.get("value", "")}
            r = bus.enqueue(action)
            return ToolResult(success=bool(r.get("ok")),
                              content=json.dumps(r, ensure_ascii=False),
                              data=r)

        self.tool_registry.register(
            name="ui_layout_control",
            description=(
                "动态界面控制：让主界面按你的指令变化。动词："
                "show_panel/hide_panel（显示/隐藏面板，target=面板 id）、"
                "select_tab/focus_panel（切换/聚焦页签，target=面板 id）、"
                "set_layout_ratio（调整三栏比例，value='左:中:右' 如 '2:2:1'）。"
                "动作可撤销（ui_layout_undo）。未知动词/目标/比例如实拒绝；"
                "非桌面环境诚实报 UNAVAILABLE，不假装界面已变。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "verb": {"type": "string",
                             "description": "show_panel/hide_panel/select_tab/"
                                            "focus_panel/set_layout_ratio"},
                    "target": {"type": "string",
                               "description": "面板 id（前四个动词必填）"},
                    "value": {"type": "string",
                              "description": "set_layout_ratio 用，如 '2:2:1'"},
                },
                "required": ["verb"],
            },
            handler=_control,
            category="ui",
        )

        def _undo(_args):
            r = bus.undo()
            return ToolResult(success=bool(r.get("ok")),
                              content=json.dumps(r, ensure_ascii=False),
                              data=r)

        self.tool_registry.register(
            name="ui_layout_undo",
            description=(
                "撤销最近一次 ui_layout_control 界面动作（弹出逆操作栈）。"
                "空栈诚实 EMPTY，不假装撤销成功。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_undo,
            category="ui",
        )

    def _register_sky_negotiation_tools(self):
        """注册天空模式硬件能力协商工具（会话状态 + 动作执行，原子化）。

        背景（审计）：天空视图的「追踪 / 调到该星 / 云台跟随」按钮曾直连主
        窗口驱动硬件，完全绕过 agent，也没有「动手前先探明硬件」的指令。
        现在这三类动作改为：目标上下文进入会话 → 模型用现有工具自己探测
        或自然语言询问 → 模型决定执行。本方法提供三块原子能力：

        - ``sky_capability_state``  读取本次会话已确认的硬件现实（只读）；
        - ``sky_capability_report`` 模型把「有/无/未知」与拍板结果写回会话
          （不碰硬件、不走 arm_manual；用户改口时覆盖）；
        - ``sky_tune_satellite`` / ``sky_track_satellite`` /
          ``sky_gimbal_follow`` 把执行意图交给天空动作总线，由桌面主窗口
          在 UI 线程落地；非桌面环境诚实 UNAVAILABLE。

        注意：这里不预置任何问答脚本，问什么、要不要问由模型依据上下文判断。
        """
        import json as _json

        from .sky_actions import get_sky_bus
        from .sky_negotiation import (CAPABILITY_SLOTS, CAPABILITY_STATES,
                                      CapabilityLedger)

        self.sky_ledger = getattr(self, "sky_ledger", None) or CapabilityLedger()
        # 会话上下文每次请求都会带上账本快照，模型不必重复追问已确认项
        self.context_manager.capability_ledger = self.sky_ledger
        bus = get_sky_bus()

        def _snapshot() -> Dict[str, Any]:
            ledger: CapabilityLedger = self.sky_ledger
            return {
                "slots": {
                    slot: {"label": label, "state": ledger.state(slot),
                           "evidence": ledger.evidence.get(slot, "")}
                    for slot, label in CAPABILITY_SLOTS.items()
                },
                "unknown": ledger.unknown_slots(),
                "decisions": dict(ledger.decisions),
            }

        def _state(_args):
            payload = _snapshot()
            payload["note"] = ("只反映本会话已确认的结论；未确认项为 unknown，"
                               "需要时先用工具探测或问用户，不要假设型号")
            return ToolResult(success=True,
                              content=_json.dumps(payload, ensure_ascii=False,
                                                  indent=2),
                              data=payload)

        def _report(args):
            ledger: CapabilityLedger = self.sky_ledger
            items = args.get("items") or []
            if not isinstance(items, list):
                return ToolResult(success=False,
                                  content="items 必须是数组",
                                  error="items 必须是数组")
            ok_count, rejected = ledger.update_many(items)
            decisions = args.get("decisions") or []
            dec_count = 0
            if isinstance(decisions, list):
                for decision in decisions:
                    if not isinstance(decision, dict):
                        continue
                    action = str(decision.get("action", ""))
                    satellite = str(decision.get("satellite", ""))
                    text = str(decision.get("decision", ""))
                    if action in ("track", "tune", "gimbal_follow") and satellite:
                        ledger.record_decision(action, satellite, text)
                        dec_count += 1
            payload = _snapshot()
            payload["accepted"] = ok_count
            payload["rejected"] = rejected
            payload["decisions_recorded"] = dec_count
            success = ((ok_count > 0 or dec_count > 0)
                       if (items or decisions) else True)
            return ToolResult(
                success=success,
                content=_json.dumps(payload, ensure_ascii=False, indent=2),
                data=payload,
                error=("；".join(rejected) if rejected and not success else ""),
            )

        self.tool_registry.register(
            name="sky_capability_state",
            description=(
                "读取本次会话已确认的天空动作硬件现实（SDR/云台/GNSS/IMU/定向天线），"
                "状态为 available/missing/unknown。只读，不探测硬件；未确认项为 "
                "unknown，不要假设型号。"
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_state,
            category="sky",
        )

        self.tool_registry.register(
            name="sky_capability_report",
            description=(
                "把本轮关于天空硬件的结论与拍板结果写回本次会话（不碰硬件）。"
                "items 支持一次多条（用户一句话描述多件硬件时一次记全）；"
                "state 只接受 available/missing/unknown。用户改口时用新结论覆盖，"
                "后续动作会直接复用，不必重复追问。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "硬件结论列表（可一次多件）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "slot": {"type": "string",
                                         "enum": list(CAPABILITY_SLOTS.keys())},
                                "state": {"type": "string",
                                          "enum": list(CAPABILITY_STATES)},
                                "evidence": {"type": "string",
                                             "description": "依据（用户原话/工具探测结果）"},
                            },
                            "required": ["slot", "state"],
                        },
                    },
                    "decisions": {
                        "type": "array",
                        "description": "本轮对动作的拍板结论（可选）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string",
                                           "enum": ["track", "tune",
                                                    "gimbal_follow"]},
                                "satellite": {"type": "string"},
                                "decision": {"type": "string"},
                            },
                            "required": ["action", "satellite", "decision"],
                        },
                    },
                },
                "required": ["items"],
            },
            handler=_report,
            category="sky",
        )

        def _enqueue(action: Dict[str, Any]):
            r = bus.enqueue(action)
            reason = str(r.get("reason") or "")
            return ToolResult(success=bool(r.get("ok")),
                              content=_json.dumps(r, ensure_ascii=False),
                              data=r,
                              error="" if r.get("ok") else reason)

        _target_note = ("只在该动作的硬件现实已确认可执行后才调用；"
                        "缺硬件时不要调用，改用自然语言说明缺什么并给替代方案。")

        self.tool_registry.register(
            name="sky_tune_satellite",
            description=(
                "把接收机调到天空视图当前选中的目标卫星（含实时多普勒修正）。"
                + _target_note
            ),
            parameters={
                "type": "object",
                "properties": {
                    "satellite_name": {"type": "string",
                                       "description": "目标卫星名（需与当前选中一致）"},
                    "frequency_hz": {"type": "number",
                                     "description": "可选：期望的下行中心频率（Hz）"},
                },
                "required": ["satellite_name"],
            },
            handler=lambda args: _enqueue({
                "action": "tune",
                "satellite": args.get("satellite_name", ""),
                "frequency_hz": args.get("frequency_hz"),
            }),
            category="sky",
        )

        self.tool_registry.register(
            name="sky_track_satellite",
            description=(
                "对天空视图当前选中的目标卫星开始/停止闭环跟踪（频率随多普勒修正）。"
                + _target_note
            ),
            parameters={
                "type": "object",
                "properties": {
                    "satellite_name": {"type": "string",
                                       "description": "目标卫星名（需与当前选中一致）"},
                    "on": {"type": "boolean", "description": "true=开始，false=停止",
                           "default": True},
                    "downlink_hz": {"type": "number",
                                    "description": "可选：标称下行频率（Hz）"},
                },
                "required": ["satellite_name"],
            },
            handler=lambda args: _enqueue({
                "action": "track",
                "satellite": args.get("satellite_name", ""),
                "on": bool(args.get("on", True)),
                "frequency_hz": args.get("downlink_hz"),
            }),
            category="sky",
        )

        self.tool_registry.register(
            name="sky_gimbal_follow",
            description=(
                "让云台/旋转器跟随天空视图当前选中的目标卫星（按 az/el 驱动，"
                "带速率上限与死区，目标丢失自动停止）。"
                + _target_note
            ),
            parameters={
                "type": "object",
                "properties": {
                    "satellite_name": {"type": "string",
                                       "description": "目标卫星名（需与当前选中一致）"},
                    "on": {"type": "boolean", "description": "true=跟随，false=解锁",
                           "default": True},
                },
                "required": ["satellite_name"],
            },
            handler=lambda args: _enqueue({
                "action": "gimbal_follow",
                "satellite": args.get("satellite_name", ""),
                "on": bool(args.get("on", True)),
            }),
            category="sky",
        )

    def _register_scheduler_tools(self):
        """注册调度器工具。"""
        sched = self.scheduler
        self.tool_registry.register(
            name="scheduler_status",
            description="查看调度器状态（任务总数、启用/禁用、总执行次数、成功率）。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=sched.get_status_text()),
            category="scheduler",
        )
        self.tool_registry.register(
            name="scheduler_list",
            description="列出所有定时任务。",
            parameters={"type": "object", "properties": {"enabled_only": {"type": "boolean", "description": "只列出启用的任务", "default": False}}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(sched.list_tasks(enabled_only=args.get("enabled_only", False)), ensure_ascii=False, indent=2)),
            category="scheduler",
        )
        self.tool_registry.register(
            name="scheduler_add",
            description="添加定时任务。可以定时执行用户/AI 自定义工作流或工具调用（引擎无内置流程，目标须先存在）。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名称"},
                    "task_type": {"type": "string", "description": "任务类型: workflow/tool", "default": "workflow"},
                    "target": {"type": "string", "description": "工作流名称或工具名称"},
                    "params": {"type": "object", "description": "任务参数"},
                    "schedule_type": {"type": "string", "description": "调度类型: interval(间隔)/once(一次性)/cron(cron 表达式)", "default": "interval"},
                    "interval_seconds": {"type": "integer", "description": "间隔秒数（interval类型）", "default": 3600},
                    "cron_expression": {"type": "string", "description": "cron 表达式（分 时 日 月 周，周 0=周日；如 '0 * * * *' 每小时整点），schedule_type=cron 时必填"},
                    "description": {"type": "string", "description": "任务描述"},
                },
                "required": ["name", "target"],
            },
            handler=lambda args: ToolResult(success=True, content=f"定时任务已添加: {args['name']}\nID: {sched.add_task(**args).task_id}"),
            category="scheduler",
        )
        self.tool_registry.register(
            name="scheduler_enable",
            description="启用一个定时任务。",
            parameters={"type": "object", "properties": {"task_id": {"type": "string", "description": "任务 ID"}}, "required": ["task_id"]},
            handler=lambda args: (lambda ok: ToolResult(
                success=ok,
                content="任务已启用" if ok else f"任务不存在: {args['task_id']}",
                error="" if ok else "task_not_found",
            ))(sched.enable_task(args["task_id"])),
            category="scheduler",
        )
        self.tool_registry.register(
            name="scheduler_disable",
            description="禁用一个定时任务。",
            parameters={"type": "object", "properties": {"task_id": {"type": "string", "description": "任务 ID"}}, "required": ["task_id"]},
            handler=lambda args: (lambda ok: ToolResult(
                success=ok,
                content="任务已禁用" if ok else f"任务不存在: {args['task_id']}",
                error="" if ok else "task_not_found",
            ))(sched.disable_task(args["task_id"])),
            category="scheduler",
        )
        self.tool_registry.register(
            name="scheduler_tick",
            description="手动触发调度器检查并执行所有到期任务。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=f"执行了 {sched.tick()} 个到期任务"),
            category="scheduler",
        )

    def _register_evolution_tools(self):
        """注册自进化工具（需启用 enable_self_evolution）。"""
        evo = self.evolution

        def _propose(args):
            # evo.propose 对未知 target_type 等畸形入参 raise ValueError；
            # 按「错误:即失败」契约接住，把选项清单直接回给模型。
            try:
                r = evo.propose(**args)
            except ValueError as e:
                return ToolResult(success=False, content=f"错误: {e}")
            return ToolResult(
                success=True,
                content=f"进化建议已提出: {args.get('target_name')}\nID: {r.id}\n下一步: 调用 evolution_evaluate 评估效果",
            )
        self.tool_registry.register(
            name="evolution_propose",
            description="提出一个自进化建议（提案记录模式，不自动应用）。可以记录对系统提示词、工具描述、配置参数或代码算法的改进建议。仅把提案写入内存/版本库，不会改写运行时：当前没有 evolution_apply/confirm 工具，提案不会生效到 system_prompt/配置/代码。修改不会直接影响主系统。",
            parameters={
                "type": "object",
                "properties": {
                    "target_type": {"type": "string", "description": "进化目标类型: skill/tool_description/code/config/pipeline"},
                    "target_name": {"type": "string", "description": "目标名称"},
                    "description": {"type": "string", "description": "进化建议描述"},
                    "proposed_change": {"type": "string", "description": "修改后的完整内容"},
                    "risk_level": {"type": "string", "description": "风险等级: low/medium/high", "default": "low"},
                },
                "required": ["target_type", "target_name", "description", "proposed_change"],
            },
            handler=_propose,
            category="evolution",
        )

        self.tool_registry.register(
            name="evolution_evaluate",
            description="评估一个进化建议的效果。运行测试用例，计算通过率，给出接受/拒绝建议。",
            parameters={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string", "description": "进化建议 ID"},
                },
                "required": ["proposal_id"],
            },
            handler=lambda args: ToolResult(
                success=True,
                content=json.dumps(evo.evaluate(args["proposal_id"]), ensure_ascii=False, indent=2),
            ),
            category="evolution",
        )

        self.tool_registry.register(
            name="evolution_commit",
            description="把一个进化建议提交为新版本快照（仅版本记录，不自动应用）。这是防幻觉变砖的版本台账：任何修改都有记录、可回滚。注意：commit 只在 version_store 里建快照，当前没有 apply 工具把它回灌到 system_prompt/运行时，因此不会自动生效；可随时用 evolution_rollback 回退。",
            parameters={
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string", "description": "进化建议 ID"},
                },
                "required": ["proposal_id"],
            },
            handler=lambda args: (
                lambda v: ToolResult(
                    success=True, content=f"版本已提交: {v.id}"
                ) if v else ToolResult(
                    success=False,
                    content="提案不存在：请先调用 evolution_propose 创建提案，再用其返回的 proposal_id 提交。",
                )
            )(evo.commit(args.get("proposal_id", ""))),
            category="evolution",
        )

        self.tool_registry.register(
            name="evolution_rollback",
            description="一键恢复：回滚到上一个版本。防幻觉变砖的核心功能，任何修改都可以撤销。",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {"type": "string", "description": "回滚到指定版本（默认上一个版本）"},
                },
                "required": [],
            },
            handler=lambda args: (lambda ok, msg: ToolResult(
                success=ok,
                content=msg,
                error="" if ok else "rollback_unavailable",
            ))(*evo.rollback(args.get("version_id"))),
            category="evolution",
        )

        self.tool_registry.register(
            name="evolution_history",
            description="查看自进化历史（所有版本记录）。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(
                success=True,
                content=json.dumps(evo.get_history(limit=10), ensure_ascii=False, indent=2),
            ),
            category="evolution",
        )

        self.tool_registry.register(
            name="evolution_status",
            description="查看自进化引擎状态（进化循环次数、建议统计、版本数）。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(
                success=True,
                content=evo.get_status_text(),
            ),
            category="evolution",
        )

    def _register_hook_tools(self):
        """Hook LLM 工具面已按 A2(D2) 摘除（调用处见 __init__ 注释）。

        背景：生产侧零事件源（grep 无 sdr.signal_detected / agent.tool_call 等事件发射点），
        原先注册的 hook_list/hook_trigger/hook_history/hook_stats 只是让 LLM 对着空气喊话。
        保留 HookManager 类（self.hook_manager）与 hooks.py 事件总线供内部/未来接线；本方法
        不再向 LLM 工具面注册任何 hook_* 工具。
        """
        return

    def _register_subagent_tools(self):
        """注册 Subagents 子代理工具（白皮书第四章 4.5）。"""
        sm = self.subagent_manager

        self.tool_registry.register(
            name="subagent_create",
            description=(
                "创建一个子代理（自主 AI 子代理：注入 model_manager 后运行真 LLM 循环，"
                "维护独立隔离上下文，只能调用其 allowed_tools 白名单内的工具，步数超限"
                "会诚实返回 status=truncated 的部分结果）。内置默认工具白名单的类型："
                "spectrum_analyzer(频谱分析)、satellite_tracker(卫星跟踪)、"
                "baseband_recorder(基带录制) 共 3 类；其余类型走通用 tool_name 路径"
                "（agent_type 传任意名称即可，不会抛 NotImplementedError），"
                "allowed_tools=None=全部工具（视图自动排除 subagent_* 防递归）。"
                "max_steps 控制 LLM 循环步数上限（默认 8）。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "agent_type": {"type": "string", "description": "子代理类型：spectrum_analyzer/satellite_tracker/baseband_recorder 共 3 类有内置默认工具白名单；其余类型走通用 tool_name 路径（不抛 NotImplementedError），allowed_tools=None=全部工具（排除 subagent_*）"},
                    "subagent_id": {"type": "string", "description": "自定义 ID（可选，自动生成）"},
                    "max_steps": {"type": "integer", "description": "LLM 循环步数上限，默认 8；超限返回截断的部分结果（truncated=true）"},
                },
                "required": ["agent_type"],
            },
            handler=lambda args: ToolResult(success=True, content=f"子代理已创建\nID: {sm.create(args['agent_type'], args.get('subagent_id'), max_steps=args.get('max_steps'))}\n类型: {args['agent_type']}"),
            category="subagent",
        )

        self.tool_registry.register(
            name="subagent_list",
            description="列出所有子代理及其状态。包括 ID、类型、状态、已完成任务数、失败任务数、上下文消息数、max_steps、运行时间。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(sm.list_subagents(), ensure_ascii=False, indent=2)),
            category="subagent",
        )

        def _subagent_execute_handler(args):
            # 同步真 LLM 循环执行；success/error 如实回传（truncated/error 都不
            # 伪装成 success，partial/失败细节在 content JSON 里可见）
            try:
                res = sm.execute_task(
                    args["subagent_id"], args["goal"], args.get("input_data"),
                    args.get("timeout_s", 120), max_steps=args.get("max_steps"))
            except ValueError as e:
                # 子代理不存在属 LLM 传参错误（可预期）：不抛给注册表的宽 catch，
                # 按次把结构化失败上报给模型；日志按 id 限流，避免反复试错刷屏
                key = str(args.get("subagent_id") or "")
                now = time.time()
                if now - _SUBAGENT_ERR_LOG_AT.get(key, 0.0) >= _SUBAGENT_ERR_LOG_INTERVAL_S:
                    _SUBAGENT_ERR_LOG_AT[key] = now
                    logger.warning("subagent_execute 参数错误（子代理不存在）: %s", e)
                return ToolResult(
                    False,
                    user_message("subagent_not_found", name=key or "?"),
                    error="subagent_not_found",
                    next_step=suggestion("subagent_not_found"),
                )
            return ToolResult(
                success=res.success,
                content=json.dumps(res.to_dict(), ensure_ascii=False, indent=2),
                error=res.error,
            )

        self.tool_registry.register(
            name="subagent_execute",
            description=(
                "同步执行一个子代理任务，等待完成并返回结果。子代理在独立隔离上下文里跑"
                "「LLM→工具→回灌→LLM」多步循环（默认最多 8 步），只能调用白名单内的工具"
                "（越界调用本地拒绝不执行）；达到 max_steps 仍未完成时返回 status=truncated "
                "的部分结果（success=false、truncated=true）；model_manager 缺失或模型调用"
                "失败时 status=error 且带技术细节，绝不伪造输出。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "subagent_id": {"type": "string", "description": "子代理 ID"},
                    "goal": {"type": "string", "description": "任务目标描述"},
                    "input_data": {"type": "object", "description": "输入数据（可选）"},
                    "timeout_s": {"type": "number", "description": "超时秒数，默认 120", "default": 120},
                    "max_steps": {"type": "integer", "description": "LLM 循环步数上限，默认 8；超限返回截断的部分结果"},
                },
                "required": ["subagent_id", "goal"],
            },
            handler=_subagent_execute_handler,
            category="subagent",
        )

        self.tool_registry.register(
            name="subagent_stats",
            description="获取子代理管理器统计信息。包括总子代理数、运行中数、总完成任务数、总失败任务数、支持的子代理类型。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(sm.get_stats(), ensure_ascii=False, indent=2)),
            category="subagent",
        )

    def _register_pose_tools(self):
        """注册 6DOF 位姿 + AR 投影工具（白皮书第八章）。"""
        pf = self.pose_fusion
        ap = self.ar_projector

        self.tool_registry.register(
            name="pose_get",
            description="获取当前 6DOF 位姿融合状态（位置/姿态/四元数/速度/置信度）。【诚实标注】该状态由手动喂入的 IMU/GNSS 数据累计融合而成；当前原型无硬件传感器实时推流，不反映设备真实姿态。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(pf.get_pose().to_dict(), ensure_ascii=False, indent=2)),
            category="pose",
        )

        self.tool_registry.register(
            name="pose_update_imu",
            description=(
                "手动喂入 IMU 数据（加速度计+陀螺仪+磁力计）到位姿融合器，更新 6DOF 姿态估计。"
                "【诚实标注】当前原型无 BMI260/TMAG5273 硬件驱动，也无任何 I2C/SPI/串口传感器"
                "实时推流通道：本工具不会自行读到任何传感器数据，传入的数值由调用方（LLM/脚本）"
                "自行提供，仅用于位姿融合算法验证/仿真/演示，不代表真实硬件读数。接真实硬件需"
                "HAL 侧补 IMU 驱动线程推流。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "accel_x": {"type": "number", "description": "X 轴加速度 m/s²"},
                    "accel_y": {"type": "number", "description": "Y 轴加速度 m/s²"},
                    "accel_z": {"type": "number", "description": "Z 轴加速度 m/s²"},
                    "gyro_x": {"type": "number", "description": "X 轴角速度 rad/s"},
                    "gyro_y": {"type": "number", "description": "Y 轴角速度 rad/s"},
                    "gyro_z": {"type": "number", "description": "Z 轴角速度 rad/s"},
                    "mag_x": {"type": "number", "description": "X 轴磁力 μT"},
                    "mag_y": {"type": "number", "description": "Y 轴磁力 μT"},
                    "mag_z": {"type": "number", "description": "Z 轴磁力 μT"},
                },
                "required": ["accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(pf.update_imu(IMUData(**args)).to_dict(), ensure_ascii=False, indent=2)),
            category="pose",
        )

        self.tool_registry.register(
            name="pose_update_gps",
            description=(
                "手动喂入 GNSS 位置（经纬度/海拔/速度/航向/卫星数）到位姿融合器，融合绝对位置。"
                "【诚实标注】当前原型无 ATGM336H 等 GNSS 模块串口 NMEA 推流通道：本工具不会自动"
                "读串口，传入的数值由调用方自行提供，仅用于融合算法验证/仿真，不代表真实定位。"
                "接真实硬件需 serial_gnss/gnss_monitor 侧补 NMEA 推流线程。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "latitude": {"type": "number", "description": "纬度（度）"},
                    "longitude": {"type": "number", "description": "经度（度）"},
                    "altitude": {"type": "number", "description": "海拔（米）"},
                    "speed": {"type": "number", "description": "速度 m/s", "default": 0},
                    "course": {"type": "number", "description": "航向（度）", "default": 0},
                    "satellites": {"type": "integer", "description": "卫星数", "default": 0},
                    "hdop": {"type": "number", "description": "HDOP", "default": 0},
                    "fix_quality": {"type": "integer", "description": "定位质量 0=无 1=GPS 2=DGPS 4=RTK", "default": 1},
                },
                "required": ["latitude", "longitude"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(pf.update_gps(GPSData(**args)).to_dict(), ensure_ascii=False, indent=2)),
            category="pose",
        )

        self.tool_registry.register(
            name="ar_project_satellite",
            description="AR 投影：计算卫星在相机视图中的屏幕位置。输入卫星的仰角/方位角/距离和当前位姿，输出归一化屏幕坐标 (0-1)、是否在视野内、标记大小。用于 AR 卫星指向辅助。",
            parameters={
                "type": "object",
                "properties": {
                    "satellite_name": {"type": "string", "description": "卫星名称"},
                    "elevation_deg": {"type": "number", "description": "卫星仰角（度）"},
                    "azimuth_deg": {"type": "number", "description": "卫星方位角（度）"},
                    "distance_km": {"type": "number", "description": "距离（公里）", "default": 1000},
                    "frequency_mhz": {"type": "number", "description": "下行频率 MHz", "default": 0},
                    "doppler_hz": {"type": "number", "description": "多普勒频移 Hz", "default": 0},
                },
                "required": ["satellite_name", "elevation_deg", "azimuth_deg"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(ap.project_satellite(args["satellite_name"], args["elevation_deg"], args["azimuth_deg"], args.get("distance_km", 1000), pf.get_pose(), args.get("frequency_mhz", 0), args.get("doppler_hz", 0)).__dict__, ensure_ascii=False, indent=2)),
            category="pose",
        )

        self.tool_registry.register(
            name="ar_pointing_guidance",
            description="AR 指向辅助：告诉用户把天线/设备指向哪个方向才能对准目标卫星。输入目标方位角/仰角，输出水平/垂直方向调整、总偏差、对准状态。这是双向指令流的核心：AI 告诉用户具体操作。",
            parameters={
                "type": "object",
                "properties": {
                    "target_azimuth": {"type": "number", "description": "目标方位角（度）"},
                    "target_elevation": {"type": "number", "description": "目标仰角（度）"},
                },
                "required": ["target_azimuth", "target_elevation"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(ap.get_pointing_guidance(args["target_azimuth"], args["target_elevation"], pf.get_pose()), ensure_ascii=False, indent=2)),
            category="pose",
        )

    def _register_workflow_recorder_tools(self):
        """注册工作流录制工具（白皮书第四章 4.6.4）。"""
        wr = self.workflow_recorder

        self.tool_registry.register(
            name="workflow_record_start",
            description="开始录制工作流。录制期间所有工具调用都会被记录，可以保存为可复用的工作流模板。类似宏录制，但支持参数化和条件分支。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "录制名称"},
                    "description": {"type": "string", "description": "录制描述", "default": ""},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "标签", "default": []},
                },
                "required": ["name"],
            },
            handler=lambda args: ToolResult(success=True, content=f"录制已开始\nID: {wr.start_recording(args['name'], args.get('description',''), args.get('tags',[]))}\n后续工具调用将被记录"),
            category="workflow_recorder",
        )

        self.tool_registry.register(
            name="workflow_record_stop",
            description="停止录制工作流。返回录制的步骤数和摘要。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps((lambda r: r.to_dict() if r else {})(wr.stop_recording()), ensure_ascii=False, indent=2)),
            category="workflow_recorder",
        )

        def _create_template_handler(args):
            try:
                tpl = wr.create_template(args["recording_id"], args["name"],
                                         args.get("description", ""),
                                         args.get("parameters", {}))
            except ValueError as e:
                # 录制不存在是参数错误：如实失败，不让异常冒到注册表
                return ToolResult(False, f"模板创建失败: {e}", error="recording_not_found")
            return ToolResult(True, json.dumps(tpl.to_dict(), ensure_ascii=False, indent=2))

        self.tool_registry.register(
            name="workflow_template_create",
            description="从录制创建工作流模板。模板可以参数化，以后用不同参数回放。这是 Record & Replay 的核心：录一次，用无数次。",
            parameters={
                "type": "object",
                "properties": {
                    "recording_id": {"type": "string", "description": "录制 ID"},
                    "name": {"type": "string", "description": "模板名称"},
                    "description": {"type": "string", "description": "模板描述", "default": ""},
                    "parameters": {"type": "object", "description": "参数定义 {参数名: 描述}", "default": {}},
                },
                "required": ["recording_id", "name"],
            },
            handler=_create_template_handler,
            category="workflow_recorder",
        )

        self.tool_registry.register(
            name="workflow_template_list",
            description="列出所有工作流模板和录制。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps({"recordings": wr.list_recordings(), "templates": wr.list_templates()}, ensure_ascii=False, indent=2)),
            category="workflow_recorder",
        )

        self.tool_registry.register(
            name="workflow_recorder_status",
            description="获取工作流录制器状态。包括当前录制、总录制数、总模板数。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(wr.get_status(), ensure_ascii=False, indent=2)),
            category="workflow_recorder",
        )

    def _register_file_tracker_tools(self):
        """注册文件变更跟踪工具（白皮书第四章 4.6.6）。"""
        ft = self.file_tracker

        self.tool_registry.register(
            name="file_change_track",
            description="记录一次文件变更。在自进化或代码修改时调用，记录修改前/修改后的内容、原因、执行者。用于审计和回滚。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径"},
                    "change_type": {"type": "string", "description": "变更类型: create/modify/delete/rename/revert"},
                    "content_before": {"type": "string", "description": "修改前内容", "default": ""},
                    "content_after": {"type": "string", "description": "修改后内容", "default": ""},
                    "reason": {"type": "string", "description": "变更原因", "default": ""},
                    "actor": {"type": "string", "description": "执行者: agent/user/system", "default": "agent"},
                },
                "required": ["file_path", "change_type"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(ft.track_change(args["file_path"], self._parse_change_type(args["change_type"]), args.get("content_before",""), args.get("content_after",""), args.get("reason",""), args.get("actor","agent")).to_dict(), ensure_ascii=False, indent=2)),
            category="file_tracker",
        )

        self.tool_registry.register(
            name="file_change_history",
            description="获取某个文件的变更历史。按时间倒序排列，包含每次变更的类型、原因、执行者、哈希。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径"},
                    "limit": {"type": "integer", "description": "返回条数，默认 50", "default": 50},
                },
                "required": ["file_path"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(ft.get_file_history(args["file_path"], args.get("limit", 50)), ensure_ascii=False, indent=2)),
            category="file_tracker",
        )

        self.tool_registry.register(
            name="file_change_revert",
            description="回滚到某个变更之前的状态。防幻觉变砖的最后一道防线：任何修改都可以回滚。",
            parameters={
                "type": "object",
                "properties": {
                    "change_id": {"type": "string", "description": "要回滚的变更 ID"},
                },
                "required": ["change_id"],
            },
            handler=lambda args: (lambda rec: ToolResult(
                success=rec is not None,
                content=json.dumps(rec.to_dict() if rec else {"error": f"变更不存在或已回滚: {args['change_id']}"}, ensure_ascii=False, indent=2),
            ))(ft.revert_to(args["change_id"], file_writer=lambda p, c: open(p, "w", encoding="utf-8").write(c))),
            category="file_tracker",
        )

        self.tool_registry.register(
            name="file_change_stats",
            description="获取文件变更跟踪器统计信息。包括总变更数、跟踪文件数、按类型/执行者分类、已回滚数。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(ft.get_stats(), ensure_ascii=False, indent=2)),
            category="file_tracker",
        )

        self.tool_registry.register(
            name="file_change_changelog",
            description="生成变更日志（changelog）。按时间倒序列出所有变更，包含时间、类型、文件、原因、执行者。",
            parameters={
                "type": "object",
                "properties": {
                    "since_timestamp": {"type": "number", "description": "起始时间戳（0=全部）", "default": 0},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=ft.generate_changelog(args.get("since_timestamp", 0))),
            category="file_tracker",
        )

    def _register_plugin_tools(self):
        """插件 LLM 工具面已按 A2(D8) 摘除（调用处见 __init__ 注释）。

        背景：plugins/ 为空目录、无任何插件可加载/启用，原先注册的 plugin_list/plugin_load/
        plugin_enable/plugin_disable/plugin_stats/plugin_install 都在空插件集上空转。保留
        PluginManager 类（self.plugin_manager）与其单测（tests/test_plugin_manager.py）；
        本方法不再向 LLM 工具面注册任何 plugin_* 工具。
        """
        return

    def _register_judge_tools(self):
        """注册 LLM-as-Judge 评判工具（白皮书第四章 4.6.1）。"""
        j = self.llm_judge

        self.tool_registry.register(
            name="judge_evaluate",
            description="对 Agent 的回答进行多维评分。使用 LLM-as-Judge 从正确性、完整性、相关性、工具使用、安全性、可读性、创造性、效率 8 个维度评分。用于自学习闭环的反馈信号和输出质量评估。",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用户问题"},
                    "answer": {"type": "string", "description": "Agent 回答"},
                    "tool_calls": {"type": "array", "items": {"type": "object"}, "description": "工具调用记录", "default": []},
                    "use_llm": {"type": "boolean", "description": "是否使用 LLM 评判（False 用规则评分）", "default": True},
                },
                "required": ["question", "answer"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(j.judge(args["question"], args["answer"], args.get("tool_calls",[]), "", args.get("use_llm", True)).to_dict(), ensure_ascii=False, indent=2)),
            category="judge",
        )

        self.tool_registry.register(
            name="judge_history",
            description="获取评判历史记录。查看最近的评判结果和各维度得分趋势。",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认 20", "default": 20},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(j.get_history(args.get("limit", 20)), ensure_ascii=False, indent=2)),
            category="judge",
        )

        self.tool_registry.register(
            name="judge_stats",
            description="获取评判器统计信息。包括总评判次数、平均分、各维度历史平均分、评判模型。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(j.get_stats(), ensure_ascii=False, indent=2)),
            category="judge",
        )

    def _register_learning_tools(self):
        """注册自学习工具（白皮书第四章 4.6.2）。

        A2(D3) 摘除说明：learning_learn / learning_suggestion 两个 LLM 工具面已摘除——
        learn_batch 只是循环 learn_from_experience、无聚合/无置信度更新；get_suggestion
        词重叠匹配对中文失效且 pattern.applied 恒 False（学到的规律从不回灌主循环）。
        保留 learning_record/experiences/patterns/stats（真实经验日志与只读统计）；
        保留 SelfLearningEngine 类（self.self_learning）供内部/单测与未来接线。
        """
        sl = self.self_learning

        self.tool_registry.register(
            name="learning_record",
            description="记录一条学习经验。在任务完成后调用，记录问题、回答、工具调用、评分。这是自学习闭环的第一步：从经验中学习。",
            parameters={
                "type": "object",
                "properties": {
                    "experience_type": {"type": "string", "description": "经验类型: tool_call/task_completion/error_recovery/user_feedback/judge_feedback"},
                    "question": {"type": "string", "description": "用户问题", "default": ""},
                    "answer": {"type": "string", "description": "Agent 回答", "default": ""},
                    "tool_calls": {"type": "array", "items": {"type": "object"}, "description": "工具调用记录", "default": []},
                    "score": {"type": "number", "description": "评分 0-10", "default": 0},
                    "feedback": {"type": "string", "description": "反馈", "default": ""},
                },
                "required": ["experience_type"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(sl.record_experience(self._parse_experience_type(args["experience_type"]), args.get("question",""), args.get("answer",""), args.get("tool_calls",[]), args.get("score",0), args.get("feedback","")).to_dict(), ensure_ascii=False, indent=2)),
            category="learning",
        )

        # learning_learn / learning_suggestion：A2(D3) 已从 LLM 工具面摘除（假闭环）。
        # 后端 SelfLearningEngine.learn_batch / get_suggestion 仍保留在 self.self_learning 上，
        # 供单测与未来真接线（聚合/中文匹配/回灌主循环）使用。

        self.tool_registry.register(
            name="learning_experiences",
            description="获取学习经验列表。查看记录的经验和评分。",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认 20", "default": 20},
                    "min_score": {"type": "number", "description": "最低评分，默认 0", "default": 0},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(sl.get_experiences(args.get("limit", 20), args.get("min_score", 0)), ensure_ascii=False, indent=2)),
            category="learning",
        )

        self.tool_registry.register(
            name="learning_patterns",
            description="获取学习到的模式列表。查看从经验中提取的规律和建议。",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认 20", "default": 20},
                    "pattern_type": {"type": "string", "description": "模式类型: tool_sequence/error_recovery/task_pattern", "default": ""},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(sl.get_patterns(args.get("limit", 20), args.get("pattern_type") or None), ensure_ascii=False, indent=2)),
            category="learning",
        )

        self.tool_registry.register(
            name="learning_stats",
            description="获取自学习引擎统计信息。包括总经验数、已学习数、高分经验数、平均分、总模式数、按类型分类。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(sl.get_stats(), ensure_ascii=False, indent=2)),
            category="learning",
        )

    def _register_orchestrator_tools(self):
        """Orchestrator LLM 工具面已按 A2(D4) 摘除（调用处见 __init__ 注释）。

        背景：orchestrator 是 workflow_engine 的劣化重写（无 timeout/条件/模板），LLM 面对两个
        多步执行框架不知该用哪个；统一走 workflow_engine。保留 Orchestrator 类（self.orchestrator）
        供内部/单测；本方法不再向 LLM 工具面注册 orchestrator_add_task/plan/execute/list/stats/pipeline。
        """
        return

    def _register_code_editor_tools(self):
        """注册自编程工具（代码编辑器）。

        写工具（modify/modify_section/git_commit/rollback）一律走外部确认
        token 门（AGENTS.md §3；单一事实源 ``mbdsdr_ai.tx_guard``）：未持有效
        ``confirm_token`` 不执行，返回 gated 信封而非假装成功。**模型自报
        ``arm_manual=True`` 不构成确认**（S13 红线）。缺门时 dry-call/幻觉
        参数即可真实改文件、真实 git add -A 提交（phase64 实测套件跑完
        README 被清空、脏工作区被自动提交）。
        """
        ce = self.code_editor

        def _arm_gate(args, tool_name):
            from .tx_guard import resolve_write_authorization
            d = resolve_write_authorization(tool_name, args)
            if not d.allowed:
                return ToolResult(
                    success=False,
                    content=f"未执行 {tool_name}：{d.reason}",
                    data={"gated": True, "reason": d.code},
                )
            return None

        self.tool_registry.register(
            name="code_read_file",
            description="读取源代码文件。可以读取项目中的任何源代码文件，用于了解现有实现、查找需要修改的位置。这是自编程的第一步：先读懂代码再修改。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径（相对于项目根目录，如 mbdsdr_ai/dsp.py）"},
                    "max_lines": {"type": "integer", "description": "最大读取行数（0=全部）", "default": 0},
                },
                "required": ["file_path"],
            },
            handler=lambda args: ToolResult(success=True, content=(lambda r: f"文件: {args['file_path']}\n共 {r[1]} 行\n\n{r[0]}")(ce.read_file(args["file_path"], args.get("max_lines", 0)))),
            category="code_editor",
        )

        def _h_modify_file(args):
            gate = _arm_gate(args, "code_modify_file")
            if gate is not None:  # 外部确认 token 门；arm_manual 自报不放行
                return gate
            return ToolResult(success=True, content=json.dumps(ce.modify_file(args["file_path"], args.get("new_content") or args.get("content") or "", args.get("description", "")).to_dict(), ensure_ascii=False, indent=2))

        self.tool_registry.register(
            name="code_modify_file",
            description="修改源代码文件。修改前自动备份，支持一键恢复。这是自编程的核心：模型可以直接修改源代码。写操作，需用户在本机签发的一次性 confirm_token（模型无法自行获得），防误触/防幻觉参数。修改后建议运行 code_run_tests 验证，然后 code_hot_reload 热加载，最后 code_git_commit 提交。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径"},
                    "new_content": {"type": "string", "description": "新的完整文件内容（也可用 content 参数）"},
                    "content": {"type": "string", "description": "新的完整文件内容（new_content 的别名）"},
                    "description": {"type": "string", "description": "修改描述", "default": ""},
                    "confirm_token": {"type": "string", "description": "用户在本机外部确认通道签发的一次性 token（绑定本次动作摘要，TTL 内单次有效）；模型无法自行获得或伪造"},
                },
                "required": ["file_path", "confirm_token"],
            },
            handler=_h_modify_file,
            category="code_editor",
        )

        def _h_modify_section(args):
            gate = _arm_gate(args, "code_modify_section")
            if gate is not None:  # 外部确认 token 门；arm_manual 自报不放行
                return gate
            return ToolResult(success=True, content=json.dumps(ce.modify_section(args["file_path"], args["old_section"], args["new_section"], args.get("description", "")).to_dict(), ensure_ascii=False, indent=2))

        self.tool_registry.register(
            name="code_modify_section",
            description="局部修改源代码文件中的一段内容。比 code_modify_file 更安全，只替换指定的代码段。修改前自动备份。写操作，需用户在本机签发的一次性 confirm_token（模型无法自行获得）。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径"},
                    "old_section": {"type": "string", "description": "要替换的旧代码段（必须完全匹配）"},
                    "new_section": {"type": "string", "description": "新代码段"},
                    "description": {"type": "string", "description": "修改描述", "default": ""},
                    "confirm_token": {"type": "string", "description": "用户在本机外部确认通道签发的一次性 token（绑定本次动作摘要，TTL 内单次有效）；模型无法自行获得或伪造"},
                },
                "required": ["file_path", "old_section", "new_section", "confirm_token"],
            },
            handler=_h_modify_section,
            category="code_editor",
        )

        self.tool_registry.register(
            name="code_run_tests",
            description="运行测试验证代码修改。支持 py_compile 语法检查和自定义测试命令。修改代码后必须运行测试，防止引入 bug。",
            parameters={
                "type": "object",
                "properties": {
                    "edit_id": {"type": "string", "description": "编辑记录 ID"},
                    "test_command": {"type": "string", "description": "自定义测试命令（如 python -m pytest）", "default": ""},
                    "test_files": {"type": "array", "items": {"type": "string"}, "description": "要测试的文件列表（运行 py_compile）", "default": []},
                },
                "required": ["edit_id"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(ce.run_tests(args["edit_id"], args.get("test_command",""), args.get("test_files",[])), ensure_ascii=False, indent=2)),
            category="code_editor",
        )

        self.tool_registry.register(
            name="code_hot_reload",
            description="热加载修改后的 Python 模块，无需重启程序。测试通过后调用此工具让修改立即生效。",
            parameters={
                "type": "object",
                "properties": {
                    "module_name": {"type": "string", "description": "模块名，如 mbdsdr_ai.dsp"},
                },
                "required": ["module_name"],
            },
            handler=lambda args: ToolResult(success=True, content=(lambda r: f"{'成功' if r[0] else '失败'}: {r[1]}")(ce.hot_reload(args["module_name"]))),
            category="code_editor",
        )

        def _h_git_commit(args):
            gate = _arm_gate(args, "code_git_commit")
            if gate is not None:  # 外部确认 token 门；arm_manual 自报不放行
                return gate
            return ToolResult(success=True, content=(lambda r: f"{'成功' if r[0] else '失败'}: {r[1]}")(ce.git_commit(args["message"], args.get("files") or None, args.get("edit_id") or None)))

        self.tool_registry.register(
            name="code_git_commit",
            description="Git commit 提交代码修改到版本控制。这是开源协作的核心：修改经过测试验证后提交到 git 仓库，形成可追溯的版本历史。写操作，需用户在本机签发的一次性 confirm_token（模型无法自行获得）；files 缺省为全部修改（git add -A，含未跟踪文件，授权前保持工作区干净）。",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "commit 信息（描述修改内容）"},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "要提交的文件列表（None=全部修改）", "default": []},
                    "edit_id": {"type": "string", "description": "关联的编辑记录 ID", "default": ""},
                    "confirm_token": {"type": "string", "description": "用户在本机外部确认通道签发的一次性 token（绑定本次动作摘要，TTL 内单次有效）；模型无法自行获得或伪造"},
                },
                "required": ["message", "confirm_token"],
            },
            handler=_h_git_commit,
            category="code_editor",
        )

        self.tool_registry.register(
            name="code_git_status",
            description="获取 git 状态。查看当前分支、修改的文件、最近的 commit。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(ce.git_status(), ensure_ascii=False, indent=2)),
            category="code_editor",
        )

        def _h_rollback(args):
            gate = _arm_gate(args, "code_rollback")
            if gate is not None:  # 外部确认 token 门；arm_manual 自报不放行
                return gate
            return ToolResult(success=True, content=(lambda r: f"{'成功' if r[0] else '失败'}: {r[1]}")(ce.rollback(args["edit_id"])))

        self.tool_registry.register(
            name="code_rollback",
            description="一键恢复：回滚到修改前的状态。防幻觉变砖的最后一道防线：任何代码修改都可以一键撤销。写操作，需用户在本机签发的一次性 confirm_token（模型无法自行获得）。",
            parameters={
                "type": "object",
                "properties": {
                    "edit_id": {"type": "string", "description": "要回滚的编辑记录 ID"},
                    "confirm_token": {"type": "string", "description": "用户在本机外部确认通道签发的一次性 token（绑定本次动作摘要，TTL 内单次有效）；模型无法自行获得或伪造"},
                },
                "required": ["edit_id", "confirm_token"],
            },
            handler=_h_rollback,
            category="code_editor",
        )

        self.tool_registry.register(
            name="code_list_edits",
            description="列出代码编辑记录。查看所有的修改历史、状态、测试结果。",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数，默认 20", "default": 20},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(ce.list_edits(args.get("limit", 20)), ensure_ascii=False, indent=2)),
            category="code_editor",
        )

        self.tool_registry.register(
            name="code_stats",
            description="获取代码编辑器统计信息。包括总编辑数、按状态分类、git 可用性、项目根目录。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(ce.get_stats(), ensure_ascii=False, indent=2)),
            category="code_editor",
        )

    def _register_astronomy_tools(self):
        """注册天文计算工具（借鉴 Stellarium）。"""
        obs = self.observer

        def _astro_pointing_guidance(target, cur, beamwidth_deg):
            # AntennaParams.beamwidth_deg 是按口径/频率推导的属性，不能当构造参数传；
            # 用户显式给 beamwidth_deg 时，用其重算"是否在波束内"（A2 B2 残留 bug 修复）。
            import math as _m
            res = compute_pointing_guidance(target, cur, AntennaParams())
            if beamwidth_deg:
                ang = _m.sqrt((target.alt_deg - cur.alt_deg) ** 2 +
                              (target.az_deg - cur.az_deg) ** 2)
                res["beamwidth_deg_used"] = float(beamwidth_deg)
                res["in_beam"] = ang < float(beamwidth_deg) / 2.0
            return res

        self.tool_registry.register(
            name="astro_set_observer",
            description="设置观测者位置（经纬度/高度）和气象参数（气压/温度/湿度）。用于卫星指向、坐标转换、大气折射计算。获取 GPS 后应调用此工具更新位置。",
            parameters={
                "type": "object",
                "properties": {
                    "latitude_deg": {"type": "number", "description": "纬度（度，北纬为正）"},
                    "longitude_deg": {"type": "number", "description": "经度（度，东经为正）"},
                    "height_m": {"type": "number", "description": "海拔高度（米）", "default": 0},
                    "pressure_hpa": {"type": "number", "description": "气压（百帕）", "default": 1013.25},
                    "temperature_c": {"type": "number", "description": "温度（摄氏度）", "default": 10},
                    "humidity": {"type": "number", "description": "相对湿度（0-1）", "default": 0.2},
                },
                "required": ["latitude_deg", "longitude_deg"],
            },
            handler=lambda args: ToolResult(success=True, content=self._set_observer_handler(args)),
            category="astronomy",
        )

        self.tool_registry.register(
            name="astro_get_observer",
            description="获取当前观测者信息（位置/气象参数）。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(obs.to_dict(), ensure_ascii=False, indent=2)),
            category="astronomy",
        )

        self.tool_registry.register(
            name="astro_equatorial_to_altaz",
            description="赤道坐标（赤经/赤纬）转地平坐标（仰角/方位角）。用于将卫星/天体的赤道坐标转换为天线指向的仰角和方位角。自动应用大气折射修正。",
            parameters={
                "type": "object",
                "properties": {
                    "ra_deg": {"type": "number", "description": "赤经（度）"},
                    "dec_deg": {"type": "number", "description": "赤纬（度）"},
                },
                "required": ["ra_deg", "dec_deg"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(EquatorialCoord(args["ra_deg"], args["dec_deg"]).to_altaz(obs).to_dict(), ensure_ascii=False, indent=2)),
            category="astronomy",
        )

        self.tool_registry.register(
            name="astro_altaz_to_equatorial",
            description="地平坐标（仰角/方位角）转赤道坐标（赤经/赤纬）。用于将天线当前指向转换为赤道坐标。",
            parameters={
                "type": "object",
                "properties": {
                    "alt_deg": {"type": "number", "description": "仰角（度）"},
                    "az_deg": {"type": "number", "description": "方位角（度，0=北，顺时针）"},
                },
                "required": ["alt_deg", "az_deg"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(AltAzCoord(args["alt_deg"], args["az_deg"]).to_equatorial(obs).to_dict(), ensure_ascii=False, indent=2)),
            category="astronomy",
        )

        self.tool_registry.register(
            name="astro_compute_refraction",
            description="计算大气折射修正量（度）。基于气压/温度/湿度，告诉用户实际仰角和视仰角的差异。对低仰角卫星接收很重要。",
            parameters={
                "type": "object",
                "properties": {
                    "alt_deg": {"type": "number", "description": "真实仰角（度）"},
                },
                "required": ["alt_deg"],
            },
            handler=lambda args: ToolResult(success=True, content=f"仰角 {args['alt_deg']}° 的大气折射修正: {compute_refraction(args['alt_deg'], obs)*60:.2f} 弧分 ({compute_refraction(args['alt_deg'], obs):.4f}°)"),
            category="astronomy",
        )

        self.tool_registry.register(
            name="astro_compute_airmass",
            description="计算大气质量（Airmass）。表示信号穿过大气层的路径长度，1=天顶，越大表示大气吸收越强。对低仰角接收的信号衰减评估很重要。",
            parameters={
                "type": "object",
                "properties": {
                    "alt_deg": {"type": "number", "description": "仰角（度）"},
                },
                "required": ["alt_deg"],
            },
            handler=lambda args: ToolResult(success=True, content=f"仰角 {args['alt_deg']}° 的大气质量: {compute_airmass(args['alt_deg']):.2f}"),
            category="astronomy",
        )

        self.tool_registry.register(
            name="astro_antenna_params",
            description="计算天线参数（增益/波束宽度/视场/波长）。输入天线口径和工作频率，输出增益(dBi)、半功率波束宽度(度)、视场(度)、波长(cm)。用于评估天线是否适合接收目标信号。",
            parameters={
                "type": "object",
                "properties": {
                    "diameter_m": {"type": "number", "description": "天线口径（米）"},
                    "frequency_hz": {"type": "number", "description": "工作频率（Hz）"},
                    "efficiency": {"type": "number", "description": "天线效率（0-1，默认0.6）", "default": 0.6},
                    "name": {"type": "string", "description": "天线名称", "default": "Antenna"},
                },
                "required": ["diameter_m", "frequency_hz"],
            },
            handler=lambda args: (
                lambda: ToolResult(
                    success=True,
                    content=json.dumps(
                        AntennaParams(
                            name=args.get("name", "Antenna"),
                            diameter_m=args["diameter_m"],
                            frequency_hz=args["frequency_hz"],
                            efficiency=args.get("efficiency", 0.6),
                        ).to_dict(),
                        ensure_ascii=False, indent=2,
                    ),
                )
            )() if args.get("diameter_m", 0) and args.get("frequency_hz", 0) else ToolResult(
                success=False,
                content="diameter_m 和 frequency_hz 必须为正数：请提供真实天线口径（米）和工作频率（Hz）。",
            ),
            category="astronomy",
        )

        self.tool_registry.register(
            name="astro_pointing_guidance",
            description="计算天线指向辅助。输入目标仰角/方位角和当前仰角/方位角，输出需要转动的方向和角度，以及是否在波束内。告诉用户如何调整天线指向目标卫星/信号源。",
            parameters={
                "type": "object",
                "properties": {
                    "target_alt": {"type": "number", "description": "目标仰角（度）"},
                    "target_az": {"type": "number", "description": "目标方位角（度）"},
                    "current_alt": {"type": "number", "description": "当前仰角（度）"},
                    "current_az": {"type": "number", "description": "当前方位角（度）"},
                    "beamwidth_deg": {"type": "number", "description": "天线波束宽度（度，默认5）", "default": 5},
                },
                "required": ["target_alt", "target_az", "current_alt", "current_az"],
            },
            handler=lambda args: (lambda res: ToolResult(
                success=True,
                content=json.dumps(res, ensure_ascii=False, indent=2)))(
                _astro_pointing_guidance(
                    AltAzCoord(args["target_alt"], args["target_az"]),
                    AltAzCoord(args["current_alt"], args["current_az"]),
                    args.get("beamwidth_deg"))),
            category="astronomy",
        )

        def _astro_time_info(args):
            # 与天空视图/过境预测同一时间源：TimeEngine 优先，标 source，不冒充系统时间
            try:
                from mbdsdr_ai.new_spacetime import get_time_engine
                unix_s = float(get_time_engine().now_unix())
                source = ("simulation"
                          if abs(unix_s - time.time()) > 1.0 else "system")
            except Exception:  # noqa: BLE001 - 引擎不可用时退墙钟并标注
                unix_s = time.time()
                source = "system_fallback"
            jd = unix_to_jd(unix_s)
            res = {
                "unix_time": unix_s,
                "time_source": source,
                "jd": round(jd, 6),
                "mjd": round(jd_to_mjd(jd), 6),
                "gmst_hms": lst_to_hms(jd_to_gmst(jd)),
                "lst_hms": lst_to_hms(jd_to_lst(jd, obs.longitude_deg)),
                "observer_lon": obs.longitude_deg,
            }
            return ToolResult(success=True,
                              content=json.dumps(res, ensure_ascii=False, indent=2))

        self.tool_registry.register(
            name="astro_time_info",
            description="获取天文时间系统信息（儒略日JD/简化儒略日MJD/格林尼治恒星时GMST/地方恒星时LST）。用于卫星轨道计算和精确时间同步。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_astro_time_info,
            category="astronomy",
        )

    def _register_amr_tools(self):
        """注册自动调制识别（AMR）工具。"""
        amr = self.amr_classifier

        self.tool_registry.register(
            name="amr_classify",
            description="自动调制识别（AMR）。分析信号的调制方式，支持 AM/FM/SSB/CW/FSK/PSK/QAM/OFDM/NOISE。使用 KNN 机器学习分类器（25维真实FFT特征，内置128个多SNR训练模板），输出预测调制方式、置信度、前K候选。这是真正的机器学习分类器，不是规则猜测。",
            parameters={
                "type": "object",
                "properties": {
                    "iq_samples": {"type": "array", "items": {"type": "number"}, "description": "IQ 样本（实数列表，交替 I/Q），如 [I1,Q1,I2,Q2,...]。必须提供真实样本；为空时返回错误，不使用模拟信号冒充输入。", "default": []},
                    "sample_rate": {"type": "number", "description": "采样率（Hz）", "default": 1.0},
                },
                "required": [],
            },
            handler=lambda args: (lambda raw: (lambda iq: ToolResult(success=True, content=json.dumps(amr.classify_iq(iq, args.get("sample_rate", 1.0)).to_dict(), ensure_ascii=False, indent=2)) if iq else ToolResult(success=False, content="错误：未提供足够的 IQ 样本（至少 2 个交替 I/Q 实数）。AMR 只对真实信号分类；为空时不会用正弦/模拟数据冒充输入，请从接收机或录制文件提供 iq_samples。"))([complex(raw[i], raw[i + 1]) for i in range(0, len(raw) - 1, 2)]))(args.get("iq_samples") or []),
            category="amr",
        )

        self.tool_registry.register(
            name="amr_extract_features",
            description="从 IQ 样本中提取 25 维 AMR 特征。包括时域特征（幅度统计/峰均比/偏度/峰度）、频域特征（带宽/频谱质心/平坦度/滚降/频谱幅度变异系数）、统计特征（过零率/瞬时频率/相位/IQ相关性/星座图密度）。",
            parameters={
                "type": "object",
                "properties": {
                    "iq_samples": {"type": "array", "items": {"type": "number"}, "description": "IQ 样本（实数列表，交替 I/Q）。必须提供真实样本；为空时返回错误，不使用模拟信号冒充输入。", "default": []},
                    "sample_rate": {"type": "number", "description": "采样率（Hz）", "default": 1.0},
                },
                "required": [],
            },
            handler=lambda args: (lambda raw: (lambda iq: ToolResult(success=True, content=json.dumps(amr.extract_features_from_iq(iq, args.get("sample_rate", 1.0)).to_dict(), ensure_ascii=False, indent=2)) if iq else ToolResult(success=False, content="错误：未提供足够的 IQ 样本（至少 2 个交替 I/Q 实数）。为空时不会用正弦/模拟数据冒充输入，请提供真实 iq_samples。"))([complex(raw[i], raw[i + 1]) for i in range(0, len(raw) - 1, 2)]))(args.get("iq_samples") or []),
            category="amr",
        )

        self.tool_registry.register(
            name="amr_add_sample",
            description="添加 AMR 训练样本（增量学习）。用户可以标记已知调制方式的信号，添加到训练集中，提高分类准确率。这是自学习的一部分：用户标注→模型学习→分类更准。",
            parameters={
                "type": "object",
                "properties": {
                    "iq_samples": {"type": "array", "items": {"type": "number"}, "description": "IQ 样本（实数列表，交替 I/Q）"},
                    "label": {"type": "string", "description": "调制方式标签: AM/FM/SSB/CW/FSK/PSK/QAM/OFDM/NOISE"},
                    "sample_rate": {"type": "number", "description": "采样率（Hz）", "default": 1.0},
                },
                "required": ["iq_samples", "label"],
            },
            handler=lambda args: ToolResult(success=True, content=(lambda: (amr.add_training_sample([complex(args["iq_samples"][i], args["iq_samples"][i+1]) for i in range(0, len(args["iq_samples"])-1, 2)], ModulationType(args["label"]), args.get("sample_rate", 1.0)), f"已添加训练样本: {args['label']}，当前训练集 {amr.get_stats()['total_samples']} 个样本")[1])()),
            category="amr",
        )

        self.tool_registry.register(
            name="amr_stats",
            description="获取 AMR 分类器统计信息。包括总样本数、K值、距离度量、按标签/来源分类的样本数、特征维度。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(amr.get_stats(), ensure_ascii=False, indent=2)),
            category="amr",
        )

    def _set_observer_handler(self, args: Dict[str, Any]) -> str:
        """设置观测者的 handler（避免复杂 lambda）。"""
        obs = self.observer
        obs.latitude_deg = args["latitude_deg"]
        obs.longitude_deg = args["longitude_deg"]
        obs.height_m = args.get("height_m", 0)
        obs.pressure_hpa = args.get("pressure_hpa", 1013.25)
        obs.temperature_c = args.get("temperature_c", 10)
        obs.humidity = args.get("humidity", 0.2)
        return f"观测者已设置: 纬度 {obs.latitude_deg}°, 经度 {obs.longitude_deg}°, 高度 {obs.height_m}m, 气压 {obs.pressure_hpa}hPa, 温度 {obs.temperature_c}°C"

    # ── 核心对话循环 ────────────────────────────────────

    # ── 并发与取消（P7.4）────────────────────────────────

    def _ensure_concurrency_state(self) -> None:
        """惰性创建并发控制字段（兼容 object.__new__ 构造的测试 agent）。"""
        if getattr(self, "_chat_lock", None) is not None:
            return
        with _CONCURRENCY_INIT_LOCK:
            if getattr(self, "_chat_lock", None) is None:
                self._chat_lock = threading.Lock()
                self._cancel_event = threading.Event()
                self._chat_active = False

    def is_busy(self) -> bool:
        """是否有 chat 请求正在执行（供 UI/诊断查询）。"""
        self._ensure_concurrency_state()
        return bool(getattr(self, "_chat_active", False))

    def request_cancel(self) -> bool:
        """请求取消当前进行中的 chat。

        返回是否确实有请求在跑（空闲时取消是 no-op，返回 False）。
        取消在安全点生效：轮次之间、流式读取中、工具批次之间、工具重试
        等待前。正在同步执行的单个工具不会被强行打断（Python 无法安全
        终止线程），它执行完后不再继续下一个工具。
        """
        self._ensure_concurrency_state()
        active = bool(getattr(self, "_chat_active", False))
        if active:
            self._cancel_event.set()
        return active

    def _cancel_pending(self) -> bool:
        ev = getattr(self, "_cancel_event", None)
        return bool(ev is not None and ev.is_set())

    # ── 延迟与成本观测（P7.5）────────────────────────────

    def _ensure_metrics_state(self) -> None:
        if getattr(self, "_metrics_history", None) is not None:
            return
        with _METRICS_INIT_LOCK:
            if getattr(self, "_metrics_history", None) is None:
                self._metrics_history = []
                self._busy_rejections = 0
            if getattr(self, "metrics_history_limit", None) is None:
                self.metrics_history_limit = METRICS_HISTORY_LIMIT

    def _record_request_metrics(self, metrics: Dict[str, Any]) -> None:
        """记录一次请求的观测数据（环形缓冲，超出上限丢最旧）。"""
        self._ensure_metrics_state()
        self._metrics_history.append(metrics)
        limit = int(getattr(self, "metrics_history_limit", METRICS_HISTORY_LIMIT) or 0)
        if limit > 0 and len(self._metrics_history) > limit:
            del self._metrics_history[:len(self._metrics_history) - limit]

    def get_ai_metrics(self) -> Dict[str, Any]:
        """返回可读的延迟/成本观测（真实数据；无数据时明确报 no_data）。

        内容：
        - ``requests`` / ``busy_rejections`` / ``cancelled_requests`` 等计数
        - ``totals``：轮次、工具调用数、token（模型 usage 或估算）
        - ``latency_ms``：请求耗时与模型轮次耗时的 p50/p95/max
        - ``by_tool``：每个工具的调用次数、失败次数
        - ``last``：最近一次请求的逐轮明细（含模型/工具耗时）
        """
        self._ensure_metrics_state()
        hist = list(getattr(self, "_metrics_history", []) or [])
        busy = int(getattr(self, "_busy_rejections", 0) or 0)
        if not hist:
            return {
                "available": False,
                "note": "no_data：尚无完成的 chat 请求被记录，不提供任何估算值。",
                "requests": 0,
                "busy_rejections": busy,
                "cancelled_requests": 0,
                "failed_requests": 0,
                "window": 0,
            }

        rounds_detail = [r for m in hist for r in (m.get("rounds_detail") or [])]
        req_latency = [m.get("latency_ms") or 0.0 for m in hist]
        round_latency = [r.get("model_latency_ms") or 0.0 for r in rounds_detail]
        tool_latency = [r.get("tool_latency_ms") or 0.0 for r in rounds_detail]

        totals = {
            "rounds": sum(int(m.get("rounds") or 0) for m in hist),
            "tool_calls": sum(int(m.get("tool_calls") or 0) for m in hist),
            "prompt_tokens": sum(int(m.get("prompt_tokens") or 0) for m in hist),
            "completion_tokens": sum(int(m.get("completion_tokens") or 0) for m in hist),
            "total_tokens": sum(int(m.get("total_tokens") or 0) for m in hist),
            "estimated_requests": sum(1 for m in hist if m.get("estimated")),
        }
        by_tool: Dict[str, Dict[str, int]] = {}
        for m in hist:
            for t in (m.get("tools") or []):
                name = t.get("name") or ""
                if not name:
                    continue
                e = by_tool.setdefault(name, {"calls": 0, "failures": 0})
                e["calls"] += 1
                if not t.get("success"):
                    e["failures"] += 1

        n = len(hist)
        return {
            "available": True,
            "requests": n,
            "window": n,
            "busy_rejections": busy,
            "cancelled_requests": sum(1 for m in hist if m.get("cancelled")),
            "failed_requests": sum(1 for m in hist if m.get("error")),
            "totals": totals,
            "tokens_per_request": {
                "avg": round(totals["total_tokens"] / n, 1) if n else None,
                "max": max((int(m.get("total_tokens") or 0) for m in hist), default=None),
            },
            "latency_ms": {
                "requests": {
                    "p50": _percentile(req_latency, 0.5),
                    "p95": _percentile(req_latency, 0.95),
                    "max": _percentile(req_latency, 1.0),
                },
                "model_round": {
                    "p50": _percentile(round_latency, 0.5),
                    "p95": _percentile(round_latency, 0.95),
                    "max": _percentile(round_latency, 1.0),
                },
                "tool_round": {
                    "p50": _percentile(tool_latency, 0.5),
                    "p95": _percentile(tool_latency, 0.95),
                    "max": _percentile(tool_latency, 1.0),
                },
            },
            "by_tool": by_tool,
            "last": hist[-1],
            "note": ("token 优先取模型返回的 usage；缺失时按字符启发式估算并以 "
                     "estimated 标记。耗时为本地实测墙钟，不是估算。"),
        }

    def _build_tool_selection(self, request: str):
        """P7：按本轮请求与上下文窗口挑选工具 schema 子集。

        返回 (definitions, ToolSelection)。禁用工具调用时返回 (None, None)；
        选择器本身出错时退回全量定义并把报告置 None（宁可超预算也不静默失去工具）。
        """
        if not self.config.enable_tool_calling:
            return None, None
        try:
            defs = self.tool_registry.get_tool_definitions(language=self._tool_language())
        except Exception:
            return None, None
        if not defs:
            return [], None
        try:
            budget = tool_budget_tokens(self.context_manager.max_context_tokens)
        except Exception:
            budget = tool_budget_tokens(32768)
        try:
            cats = {name: str((meta or {}).get("category", ""))
                    for name, meta in self.tool_registry.tools.items()}
        except Exception:
            cats = {}
        try:
            sel = select_tools(defs, request, budget_tokens=budget, categories=cats)
        except Exception:
            return defs, None
        return sel.definitions, sel

    #: 只登记这些「真实会话状态」参数，值来自工具入参而非模型口头承诺
    _STATE_ARG_KEYS = ("frequency_hz", "center_freq", "sample_rate",
                       "bandwidth", "mode", "volume", "squelch_db",
                       "gain_db", "device_type", "antenna", "ppm")

    def _capture_tool_state(self, tool_name: str, result) -> None:
        """P7.3：工具成功后把状态型参数登记进 ContextManager（跨压缩保留）。"""
        try:
            args = getattr(result, "args", None) or {}
        except Exception:
            return
        if not isinstance(args, dict):
            return
        for k in self._STATE_ARG_KEYS:
            if k in args and args[k] not in (None, "", []):
                try:
                    self.context_manager.set_state(k, args[k], source=tool_name)
                except Exception:
                    pass

    def _check_state_after_compaction(self) -> None:
        """P7.3：压缩后自检关键状态；丢了就明确要求重新获取，不许假装记得。"""
        try:
            health = self.context_manager.state_health()
        except Exception:
            return
        if health.get("missing"):
            try:
                self.context_manager.add_message(
                    "system", "[状态告警] " + health["reacquire_hint"])
            except Exception:
                pass

    def _slog(self, event_type: str, **fields) -> None:
        """向当前会话的 append-only 事件日志追加一条事件。

        懒创建 SessionLog（按 conversation_id），会话切换时自动换文件。
        任何异常都吞掉——写日志绝不能拖垮 chat 主循环。
        """
        try:
            if not getattr(self.config, "enable_session_log", True):
                return
            cid = self.conversation_id
            if self._session_log is None or self._session_log_conv != cid:
                self._session_log = SessionLog(cid)
                self._session_log_conv = cid
            self._session_log.emit(event_type, **fields)
        except Exception:
            pass

    def _hook(self, event_type: str, **data) -> None:
        """向内部 HookManager 事件总线发一条事件（观测/联动用）。

        与 _slog 并列：_slog 落不可变 JSONL 供审计/重放，_hook 走进程内事件
        总线供内部钩子实时联动（如自动录制、告警）。这里只发「真实生产事件」，
        把 hook_manager 从「对着空气喊话」变成活的观测层。任何异常都吞掉——
        钩子/发射绝不能拖垮 chat 主循环（Hook.trigger 自身也已兜底）。
        """
        try:
            hm = getattr(self, "hook_manager", None)
            if hm is None:
                return
            # EventType 是 str-Enum，但 str(Enum.X) 得到 "EventType.X" 而非值；
            # 事件总线按值路由（"agent.tool_call"），这里显式取 .value。
            etype = getattr(event_type, "value", event_type)
            hm.trigger(Event(event_type=str(etype), data=data, source="agent"))
        except Exception:
            pass

    def _on_subagent_event(self, subagent_id: str, event_type: str,
                           fields: Optional[Dict[str, Any]] = None) -> None:
        """子代理观测事件冒泡入口（SubagentManager 注入的 on_event 回调）。

        两条落地，与父代理自身的 _slog/_hook 同构：
        - 父会话日志以 ``subagent.<type>`` 落账并带 subagent_id，形成从父到子
          的完整审计链（子代理另有独立 JSONL，这里只记冒泡摘要）。
        - hook 总线仅对**有专门 SUBAGENT_* 枚举**的类型触发：用 getattr 把
          created/task_start/tool_call/tool_result/task_end/error 映射到对应
          EventType，step/assistant_message/destroyed 等无专门枚举的自动跳过
          （返回 None 即不发），避免往总线灌无路由的事件。
        任何异常都吞掉——子代理观测绝不能拖垮父 chat 主循环。
        """
        try:
            fields = dict(fields or {})
            self._slog("subagent." + str(event_type),
                       subagent_id=subagent_id, **fields)
            et = getattr(EventType, "SUBAGENT_" + str(event_type).upper(), None)
            if et is not None:
                self._hook(et, subagent_id=subagent_id, **fields)
        except Exception:
            pass

    def get_session_events(self) -> List[Dict[str, Any]]:
        """读回当前会话的事件流（审计/重放/测试用）。"""
        try:
            cid = self.conversation_id
            if self._session_log is None or self._session_log_conv != cid:
                self._session_log = SessionLog(cid)
                self._session_log_conv = cid
            return self._session_log.read_events()
        except Exception:
            return []

    def chat(self, user_input: str, max_tool_rounds: int = 10,
             on_delta=None) -> Dict[str, Any]:
        """处理用户输入（并发安全 + 可取消的外壳）。

        - 同一 agent 同时只允许一个请求执行；第二个请求**不排队、不共享
          上下文**，立刻返回 ``error=agent_busy``（调用方可提示稍后再试）。
        - 执行期间可用 request_cancel() 请求取消；取消在安全点生效，
          返回 ``cancelled=True`` 且本轮不算成功。
        """
        self._ensure_concurrency_state()
        if not self._chat_lock.acquire(blocking=False):
            # 并发被拒也登记一次（真实计数，供 UI/诊断看「忙」的频率）
            try:
                self._ensure_metrics_state()
                self._busy_rejections = int(
                    getattr(self, "_busy_rejections", 0) or 0) + 1
            except Exception:
                pass
            return {
                "content": "上一条请求仍在处理中，请稍候再试，或先取消当前请求。",
                "tool_calls": [],
                "tool_results": [],
                "usage": {},
                "latency_ms": 0,
                "compacted": False,
                "error": BUSY_ERROR,
                "error_code": BUSY_ERROR,
                "interrupted": False,
                "cancelled": False,
                "busy": True,
                "rounds": 0,
            }
        try:
            # 只有拿到锁、真正开始处理时才清取消标志：空闲时的取消是 no-op
            self._cancel_event.clear()
            self._chat_active = True
            return self._chat_impl(user_input, max_tool_rounds, on_delta)
        finally:
            self._chat_active = False
            self._cancel_event.clear()
            self._chat_lock.release()

    def _exec_one_tool(self, tc: Dict[str, Any], tc_id: str):
        """执行单个工具（含 retryable 重试与取消安全点）。返回 (result, elapsed_ms)。

        Sprint 5：串行与并行分支共用此执行核，保证语义逐字节一致。
        **线程安全**：只读 tool_registry / _cancel_pending，只创建本地 ToolResult，
        不碰任何跨调用共享可变状态——故可放进 ThreadPoolExecutor 并发跑（仅对
        is_parallel_safe 的工具）。registry 侧 call_log 的并发写由
        ToolRegistry._call_log_lock 保护；handler 自身线程安全由 parallel_safe 背书。
        """
        def _safe_tool_call(_tc=tc):
            try:
                return self.tool_registry.call_from_model(_tc)
            except Exception as _e:
                return ToolResult(
                    success=False,
                    content=f"工具执行异常: {type(_e).__name__}: {_e}",
                    error=str(_e),
                )

        tool_start = time.time()
        result = _safe_tool_call()
        for _attempt in range(2):  # 最多再重试 2 次
            if getattr(result, "success", False):
                break
            err_code = getattr(result, "error", "") or ""
            if not err_code or not retryable(err_code):
                break  # 无码或不可重试：交给模型改，不空转
            if self._cancel_pending():
                break
            time.sleep(0.4)
            if self._cancel_pending():
                break
            result = _safe_tool_call()
        return result, (time.time() - tool_start) * 1000.0

    def _finalize_one_tool(self, tool_name: str, tc_id: str, fn: Dict[str, Any],
                           result, round_num: int,
                           all_tool_results: List[Dict[str, Any]]) -> None:
        """收尾单个工具结果：登记/状态捕获/重复守卫/回写上下文/双通道日志。

        Sprint 5：**必须在主线程串行调用**——碰 context_manager、repeat 守卫、
        _capture_tool_state 等跨调用共享状态，并发会乱序/竞态。并行分支先在 worker
        线程跑完 _exec_one_tool，再按 tool_call 原序逐个 finalize，因此上下文链与
        日志序与串行逐字节一致。
        """
        all_tool_results.append(result.to_dict())

        # P7.3：把状态型工具真实生效的参数登记为跨压缩状态
        if getattr(result, "success", False):
            self._capture_tool_state(tool_name, result)

        # repeat-call guard：检测连续相同工具+参数，递增提醒
        try:
            canon = json.dumps(fn.get("arguments", ""), sort_keys=True, ensure_ascii=False)
        except Exception:
            canon = str(fn.get("arguments", ""))
        key = tool_name + "|" + canon
        if key == self._repeat_key:
            self._repeat_count += 1
        else:
            self._repeat_key = key
            self._repeat_count = 1
        if self._repeat_count in (3, 5, 8):
            nudge = ("[guard] 你已连续用相同参数调用 %s 第 %d 次。若任务未完成，请换方法或换参数，"
                     "不要重复同一调用；若证据已足够就给最终答案。" % (tool_name, self._repeat_count))
            result.content = nudge + "\n---\n" + result.content

        # 添加工具结果到上下文（_tool_result_to_model_content 内含 DSH nextStep 注入）
        self.context_manager.add_tool_message(
            tool_call_id=tc_id,
            content=_tool_result_to_model_content(result),
            tool_name=tool_name,
        )
        self._slog("tool_result", tool=tool_name, tool_call_id=tc_id,
                   success=bool(getattr(result, "success", False)),
                   error=str(getattr(result, "error", "") or ""),
                   latency_ms=round(float(getattr(result, "latency_ms", 0.0) or 0.0), 1),
                   round=round_num + 1)
        _ok = bool(getattr(result, "success", False))
        self._hook(EventType.AGENT_TOOL_RESULT, tool=tool_name,
                   tool_call_id=tc_id, ok=_ok,
                   error=str(getattr(result, "error", "") or ""),
                   outcome=("executed" if _ok else "error"),
                   latency_ms=round(float(getattr(result, "latency_ms", 0.0) or 0.0), 1),
                   round=round_num + 1)

    def _run_tool_batch_parallel(self, tool_calls: List[Dict[str, Any]], round_num: int,
                                 all_tool_calls: List[Dict[str, Any]],
                                 all_tool_results: List[Dict[str, Any]]) -> float:
        """Sprint 5：并发执行同一轮内全部 parallel_safe 的工具，保序回收。

        返回本轮工具阶段的**墙钟**耗时（ms）。三段式，共享状态全留在主线程：
        1) 预阶段（主线程串行）：预分配 tc_id、登记 all_tool_calls、落 tool_call
           双通道日志——顺序确定，与串行一致。
        2) 执行阶段（ThreadPoolExecutor 并发）：每 worker 跑 _exec_one_tool（只读
           registry，线程安全）；按 tool_call 原序回收 future，结果与输入一一对应。
        3) 后阶段（主线程串行）：按原序逐个 _finalize_one_tool——上下文链、repeat
           守卫、_capture_tool_state、tool_result 日志全在主线程，与串行逐字节一致。
        """
        n = len(tool_calls)
        # 1) 预阶段：tc_id 预分配（并行下不能依赖循环内时间戳顺序）
        tc_ids: List[str] = []
        for i, tc in enumerate(tool_calls):
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            all_tool_calls.append({"name": tool_name,
                                   "arguments": fn.get("arguments", "")})
            tc_id = tc.get("id", "") or f"call_{int(time.time()*1000)}_p{i}"
            tc["id"] = tc_id
            tc_ids.append(tc_id)
            self._slog("tool_call", tool=tool_name, tool_call_id=tc_id,
                       arguments=fn.get("arguments", ""), round=round_num + 1,
                       parallel=True)
            self._hook(EventType.AGENT_TOOL_CALL, tool=tool_name,
                       tool_call_id=tc_id, arguments=fn.get("arguments", ""),
                       round=round_num + 1, parallel=True)

        # 2) 执行阶段：并发跑，按原序回收
        phase_start = time.time()
        with ThreadPoolExecutor(max_workers=n) as ex:
            futures = [ex.submit(self._exec_one_tool, tc, tc_ids[i])
                       for i, tc in enumerate(tool_calls)]
            outcomes = []
            for i, fut in enumerate(futures):
                try:
                    outcomes.append(fut.result())
                except Exception as _e:
                    # 执行核按构造吞掉一切异常并返回结构化失败；真到这里说明
                    # _exec_one_tool 自身有 bug——如实降级为失败结果，绝不让单个
                    # worker 的意外掀翻整轮对话（诚实失败，不伪装成功）。
                    _fn = tool_calls[i].get("function", {})
                    outcomes.append((ToolResult(
                        success=False,
                        content=f"工具并发执行异常: {type(_e).__name__}: {_e}",
                        tool_name=_fn.get("name", ""),
                        error="parallel_exec_error",
                    ), 0.0))
        wall_ms = (time.time() - phase_start) * 1000.0

        # 3) 后阶段：主线程按原序串行收尾
        for i, tc in enumerate(tool_calls):
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            result, _elapsed = outcomes[i]
            self._finalize_one_tool(tool_name, tc_ids[i], fn, result,
                                    round_num, all_tool_results)
        return wall_ms

    def _chat_impl(self, user_input: str, max_tool_rounds: int = 10,
                   on_delta=None) -> Dict[str, Any]:
        """
        处理用户输入，返回 Agent 回复。

        流程：
        1. 添加用户消息到上下文
        2. 检索相关记忆
        3. 构建系统提示词（含记忆）
        4. 调用 LLM
        5. 如果模型调用工具，执行工具，结果返回模型，继续循环
        6. 自动压缩上下文
        7. 返回最终回复

        返回:
        {
            "content": str,           # 最终回复文本
            "tool_calls": list,       # 本轮调用的工具列表
            "tool_results": list,     # 工具调用结果
            "usage": dict,            # token 用量
            "latency_ms": float,      # 总耗时
            "compacted": bool,        # 是否触发了压缩
            "error": str,             # 错误信息（如果有）
            "error_code": str,        # 错误码（llm_* / api_key_not_set 等，供 UI 分流）
            "interrupted": bool,      # 流中断/取消导致本轮未完成
            "cancelled": bool,        # 是否由 request_cancel() 主动取消
            "busy": bool,             # 是否因并发（已有请求在跑）被拒
        }
        """
        start_time = time.time()
        self.total_agent_calls += 1

        # 检查 API key
        if not self.config.api_key:
            return {
                "content": "错误: API key 未设置。请配置硅基流动 API key 后使用。",
                "tool_calls": [],
                "tool_results": [],
                "usage": {},
                "latency_ms": 0,
                "compacted": False,
                "error": "api_key_not_set",
                "error_code": "api_key_not_set",
                "interrupted": False,
                "cancelled": False,
                "busy": False,
                "rounds": 0,
            }

        # 1. 添加用户消息
        self.context_manager.add_user_message(user_input)
        self._slog("turn_start", max_tool_rounds=max_tool_rounds,
                   model=getattr(self.model_manager, "model", ""))
        self._slog("user_message", content=user_input)

        # 2. 检索相关记忆
        memory_context = self.memory.build_memory_context(user_input)

        # 2.5 工具集预算：按本轮请求裁出候选 schema。
        # 全量 schema 实测约 9 万 token，远超多数模型窗口；不裁剪会让模型被截断
        # 或直接 400，工具选择准确率无从谈起。裁剪后 list_tools 仍始终可用。
        tools, tool_selection = self._build_tool_selection(user_input)
        self.last_tool_selection = tool_selection
        if tools:
            self.context_manager.set_tool_definitions(tools)

        # 3. 构建系统提示词（含记忆 + 工具说明）
        system_prompt = SYSTEM_PROMPT
        if memory_context:
            system_prompt += memory_context
        system_prompt += f"\n\n当前可用工具数: {len(self.tool_registry.get_tool_names())}。调用工具前先用 list_tools 确认工具列表。"
        if tool_selection is not None and tool_selection.trimmed:
            system_prompt += "\n\n" + tool_selection.note
        self.context_manager.set_system_prompt(system_prompt)

        # 4. 工具调用循环
        all_tool_calls = []
        all_tool_results = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        compacted = False
        final_content = ""
        interrupted = False
        cancelled = False
        last_error = None
        last_error_code = ""
        round_metrics: List[Dict[str, Any]] = []

        def _emit_round(model_latency_ms, usage, n_tools=0, tool_ms=0.0,
                        cancelled_round=False):
            """记录一轮的真实观测值（不补零、不造数据）。"""
            round_metrics.append({
                "round": len(round_metrics) + 1,
                "model_latency_ms": round(float(model_latency_ms), 1),
                "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
                "estimated": bool(usage.get("estimated")),
                "tool_calls": int(n_tools),
                "tool_latency_ms": round(float(tool_ms), 1),
                "cancelled": bool(cancelled_round),
            })

        for round_num in range(max_tool_rounds):
            # P7.4：轮次之间检查取消（流未开始/上一批工具已结束）
            if self._cancel_pending():
                cancelled = True
                interrupted = True
                last_error = CANCEL_ERROR
                last_error_code = CANCEL_ERROR
                final_content = "已按请求取消本轮对话（尚未开始新的模型轮次）。"
                break
            # 检查是否需要压缩
            if self.context_manager.needs_compaction():
                self.context_manager.compact()
                compacted = True
                self._slog("compaction", trigger="pressure",
                           round=round_num + 1)
                self._hook(EventType.AGENT_CONTEXT_COMPACT,
                           trigger="pressure", round=round_num + 1)
                self._check_state_after_compaction()
            messages = self.context_manager.build_api_messages()

            # 调用 LLM（流式，on_delta 回调实时吐字）
            response = {"success": True, "content": "", "tool_calls": [],
                        "usage": {}, "model": self.model_manager.model,
                        "latency_ms": 0}
            streamed = ""
            stream = None
            model_start = time.time()
            try:
                stream = self.model_manager.chat_stream(messages=messages,
                                                        tools=tools,
                                                        tool_choice="auto")
                for ev in stream:
                    # P7.4：流式读取中取消——停止消费并关闭生成器，
                    # 不把已收到的半截文本当成完整回复。
                    if self._cancel_pending():
                        cancelled = True
                        break
                    if ev.get("done"):
                        response = ev
                    elif ev.get("delta"):
                        streamed += ev["delta"] if isinstance(ev["delta"], str) else ""
                        if on_delta is not None:
                            try:
                                on_delta(ev["delta"])
                            except Exception:
                                pass
            except Exception as e:
                response = {"success": False, "content": "", "tool_calls": [],
                            "usage": {}, "error": str(e)}
            finally:
                # 显式关闭流：取消/异常后不把 HTTP 流和后续消费留在后台
                if stream is not None:
                    try:
                        close = getattr(stream, "close", None)
                        if close:
                            close()
                    except Exception:
                        pass

            model_latency_ms = (time.time() - model_start) * 1000.0

            if cancelled:
                interrupted = True
                last_error = CANCEL_ERROR
                last_error_code = CANCEL_ERROR
                # 取消前已收到的部分输出按真实字符估算 completion，标记 estimated
                _emit_round(model_latency_ms, {
                    "prompt_tokens": self.context_manager.get_total_tokens(),
                    "completion_tokens": estimate_tokens(streamed),
                    "estimated": True,
                }, cancelled_round=True)
                partial = streamed.strip()
                if partial:
                    final_content = (partial + "\n\n（已按请求取消：以上为取消前收到"
                                     "的部分内容，本轮请求未完成。）")
                else:
                    final_content = "已按请求取消本轮对话（模型输出被中止）。"
                break

            # 累计用量
            usage = response.get("usage", {})

            # 流式 API 常不返回 usage（除非设 stream_options.include_usage），用真实字符数估算
            if not usage or not usage.get("total_tokens"):
                _content = response.get("content", "") or ""
                _tcs = response.get("tool_calls", []) or []
                prompt_est = self.context_manager.get_total_tokens()
                completion_est = estimate_tokens(_content)
                for _tc in _tcs:
                    try:
                        completion_est += estimate_tokens(
                            json.dumps(_tc, ensure_ascii=False))
                    except Exception:
                        completion_est += estimate_tokens(str(_tc))
                usage = {
                    "prompt_tokens": prompt_est,
                    "completion_tokens": completion_est,
                    "total_tokens": prompt_est + completion_est,
                    "estimated": True,
                }

            total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            total_usage["total_tokens"] += usage.get("total_tokens", 0)
            if usage.get("estimated"):
                total_usage["estimated"] = True

            if not response.get("success"):
                _emit_round(model_latency_ms, usage)
                last_error = response.get("error", "unknown_error")
                # 与 last_error 成对保留：UI 按码分流下一步（401 与 429 的
                # 处置完全不同），只把中文串丢给界面就没法区分。
                last_error_code = str(response.get("error_code") or "")
                partial = (response.get("content") or "").strip()
                if partial:
                    # P7.2：流中断/失败时保留已收到的部分内容并明确标注未完成，
                    # 不把半截回答当成完整成功回复，也不静默吞掉。
                    interrupted = True
                    final_content = (
                        partial + "\n\n（模型输出中断：" + str(last_error)
                        + "。以上为已收到的部分内容，本轮请求未完成。）")
                else:
                    final_content = f"模型调用失败: {last_error}"
                break

            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])

            # 弱模型兜底：原生 tool_calls 为空时，从正文文本里提取工具调用
            # （小模型/本地模型常把工具调用写成 <tool_call> 或 ```json 或裸 JSON）
            if not tool_calls and content and self.config.enable_tool_calling:
                valid_names = set(self.tool_registry.get_tool_names())
                parsed = self.model_manager.parse_tool_calls_from_text(
                    content, valid_names)
                if parsed:
                    tool_calls = parsed
                    content = self._strip_tool_call_text(content)

            # 如果没有工具调用，这是最终回复
            if not tool_calls:
                _emit_round(model_latency_ms, usage)
                final_content = content
                # 添加 assistant 消息到上下文
                self.context_manager.add_assistant_message(content)
                self._slog("assistant_message", content=content,
                           round=round_num + 1, tool_calls=0,
                           model_latency_ms=round(float(model_latency_ms), 1))
                self._hook(EventType.AGENT_MESSAGE, role="assistant",
                           content=content, round=round_num + 1)
                break

            # 有工具调用，执行工具
            # 先添加 assistant 消息（含 tool_calls）到上下文
            self.context_manager.add_assistant_message(content, tool_calls=tool_calls)
            self._slog("assistant_message", content=content,
                       round=round_num + 1, tool_calls=len(tool_calls),
                       model_latency_ms=round(float(model_latency_ms), 1))

            tool_ms = 0.0
            n_tc = len(tool_calls)

            # Sprint 5：本批是否可并发——>1 个、全部 parallel_safe、且当前未取消。
            # parallel_safe 默认 False ⇒ 恒走串行（既有行为，零回归）；仅当显式标记
            # 的纯计算/独立子进程类工具同轮 multiple 时才并发（保序回收，见
            # _run_tool_batch_parallel）。SDR 硬件/写工具单一设备非线程安全，保持 False。
            can_parallel = (
                n_tc > 1
                and not self._cancel_pending()
                and all(self.tool_registry.is_parallel_safe(
                    tc.get("function", {}).get("name", "")) for tc in tool_calls)
            )

            if can_parallel:
                tool_ms = self._run_tool_batch_parallel(
                    tool_calls, round_num, all_tool_calls, all_tool_results)
                # 并发批已整体派发，结果均为真实执行；若期间收到取消，仍如实写回
                # 全部结果（保证 assistant(tool_calls)↔tool 链完整），再于轮末停循环。
                if self._cancel_pending():
                    cancelled = True
            else:
                for i, tc in enumerate(tool_calls):
                    fn = tc.get("function", {})
                    tool_name = fn.get("name", "")
                    all_tool_calls.append({"name": tool_name, "arguments": fn.get("arguments", "")})

                    # 确保 tool_call_id 非空（文本解析的工具调用可能缺 id）
                    tc_id = tc.get("id", "") or f"call_{int(time.time()*1000)}_{i}"
                    tc["id"] = tc_id
                    self._slog("tool_call", tool=tool_name, tool_call_id=tc_id,
                               arguments=fn.get("arguments", ""), round=round_num + 1)
                    self._hook(EventType.AGENT_TOOL_CALL, tool=tool_name,
                               tool_call_id=tc_id, arguments=fn.get("arguments", ""),
                               round=round_num + 1)

                    # P7.4：取消后不再执行剩余工具，但仍为每个 tool_call_id 写回
                    # 一条「已取消」结果，保证 assistant(tool_calls) ↔ tool 链完整
                    # （否则历史无法再作为合法请求发给模型）。
                    if self._cancel_pending():
                        cancelled = True
                        cancelled_content = "[已取消] 用户中途取消，该工具未执行。"
                        all_tool_results.append(ToolResult(
                            success=False, content=cancelled_content,
                            tool_name=tool_name,
                            error=CANCEL_ERROR).to_dict())
                        self.context_manager.add_tool_message(
                            tool_call_id=tc_id, content=cancelled_content,
                            tool_name=tool_name)
                        self._slog("tool_result", tool=tool_name, tool_call_id=tc_id,
                                   success=False, error=CANCEL_ERROR,
                                   cancelled=True, round=round_num + 1)
                        self._hook(EventType.AGENT_TOOL_RESULT, tool=tool_name,
                                   tool_call_id=tc_id, ok=False, error=CANCEL_ERROR,
                                   outcome="cancelled", round=round_num + 1)
                        continue

                    # Sprint 5：执行核（含重试/取消）抽到 _exec_one_tool，与并行分支共用
                    result, _elapsed = self._exec_one_tool(tc, tc_id)
                    tool_ms += _elapsed
                    # 收尾（状态捕获/重复守卫/回写上下文/双通道日志）留在主线程串行
                    self._finalize_one_tool(tool_name, tc_id, fn, result,
                                            round_num, all_tool_results)

            # 继续循环，让模型看到工具结果后继续
            _emit_round(model_latency_ms, usage, len(tool_calls), tool_ms,
                        cancelled_round=cancelled)
            if cancelled:
                # P7.4：本批工具因取消未全部执行，不再开启下一轮模型调用
                interrupted = True
                last_error = CANCEL_ERROR
                last_error_code = CANCEL_ERROR
                final_content = "已按请求取消本轮对话（工具调用循环已停止）。"
                break

        # 工具循环耗尽：没有最终回复也没有错误时，明确告知而非静默空字符串
        if not final_content and not last_error:
            final_content = ("（工具调用已达最大轮次 " + str(max_tool_rounds) +
                             "，未能生成最终回复。请简化问题或分步骤提问。）")
            last_error = "tool_rounds_exhausted"
            last_error_code = "tool_rounds_exhausted"

        # 5. 最终检查压缩
        if self.context_manager.needs_compaction():
            self.context_manager.compact()
            compacted = True
            self._slog("compaction", trigger="context-overflow", final=True)
            self._hook(EventType.AGENT_CONTEXT_COMPACT,
                       trigger="context-overflow", final=True)
            self._check_state_after_compaction()

        # 6. 遗忘旧记忆（每 10 次调用执行一次）
        if self.total_agent_calls % 10 == 0:
            self.memory.forget_old()

        latency_ms = round((time.time() - start_time) * 1000, 1)

        # P7.5：登记本轮真实观测（逐轮模型/工具耗时、token 与估算标记）
        request_metrics = {
            "timestamp": time.time(),
            "latency_ms": latency_ms,
            "rounds": len(round_metrics),
            "tool_calls": len(all_tool_calls),
            "prompt_tokens": int(total_usage.get("prompt_tokens", 0) or 0),
            "completion_tokens": int(total_usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(total_usage.get("total_tokens", 0) or 0),
            "estimated": bool(total_usage.get("estimated")),
            "cancelled": cancelled,
            "interrupted": interrupted,
            "error": last_error,
            "rounds_detail": round_metrics,
            "tools": [{"name": r.get("tool_name", ""),
                       "success": bool(r.get("success"))}
                      for r in all_tool_results if r.get("tool_name")],
        }
        try:
            self._record_request_metrics(request_metrics)
        except Exception:
            pass

        if last_error:
            self._slog("error", code=str(last_error),
                       cancelled=cancelled, interrupted=interrupted)
            self._hook(EventType.AGENT_ERROR, code=str(last_error),
                       cancelled=cancelled, interrupted=interrupted)
        self._slog("turn_end", latency_ms=latency_ms,
                   rounds=len(round_metrics), tool_calls=len(all_tool_calls),
                   compacted=compacted, cancelled=cancelled,
                   interrupted=interrupted, error=str(last_error or ""))

        return {
            "content": final_content,
            "tool_calls": all_tool_calls,
            "tool_results": all_tool_results,
            "usage": total_usage,
            "latency_ms": latency_ms,
            "compacted": compacted,
            "error": last_error,
            "error_code": last_error_code,
            "interrupted": interrupted,
            "cancelled": cancelled,
            "busy": False,
            "rounds": len(all_tool_calls) + (1 if final_content else 0),
            "metrics": request_metrics,
        }

    # ── 压缩回调 ────────────────────────────────────────

    def _strip_tool_call_text(self, content: str) -> str:
        """从正文中剥离已被兜底解析消费的工具调用标记，保留自然语言说明。"""
        if not content:
            return content
        import re as _re
        valid = set(self.tool_registry.get_tool_names())
        text = content
        # 1) <tool_call>...</tool_call>
        text = _re.sub(r"<\s*tool_call\s*>.*?<\s*/\s*tool_call\s*>",
                       "", text, flags=_re.DOTALL)
        # 2) ```json ... ``` 代码块，仅当其中引用了真实工具名
        def _drop_fence(m):
            body = m.group(1)
            try:
                obj = json.loads(body)
                objs = obj if isinstance(obj, list) else [obj]
                names = [o.get("name") or o.get("tool") or
                         (o.get("function") or {}).get("name")
                         for o in objs if isinstance(o, dict)]
                if any(n in valid for n in names):
                    return ""
            except Exception:
                pass
            return m.group(0)
        text = _re.sub(r"```(?:json)?\s*(.*?)```", _drop_fence,
                       text, flags=_re.DOTALL)
        # 3) 裸 JSON 对象，仅当它整体就是一个工具调用
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                obj = json.loads(stripped)
                name = (obj.get("name") or obj.get("tool") or
                        (obj.get("function") or {}).get("name"))
                if name in valid:
                    return ""
            except Exception:
                pass
        return text.strip()

    def _compaction_callback(self, history_text: str,
                             previous_summary: str = None) -> str:
        """
        上下文压缩回调：用 LLM 总结被裁剪的历史（anchored summary 模式）。

        若已有旧摘要（previous_summary），让 LLM 在旧摘要基础上合并新内容，
        而非从头重写——保留早期已压缩的关键事实。
        """
        try:
            sys_prompt = ContextManager.SUMMARY_TEMPLATE
            user_content_parts = []
            if previous_summary:
                user_content_parts.append(
                    f"【已有的旧摘要，请在其基础上更新合并，不要丢弃其中仍重要的事实】\n"
                    f"{previous_summary}\n")
            user_content_parts.append(
                f"【需要并入摘要的新对话历史】\n\n{history_text[:4000]}")
            user_content = "\n".join(user_content_parts)

            messages = [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ]
            response = self.model_manager.chat(messages=messages, max_tokens=600)
            if response.get("success"):
                content = response.get("content", "")
                if content and content.strip():
                    return content
        except Exception:
            pass
        return f"[自动压缩] 已裁剪早期对话历史"

    # ── 对话管理（多会话持久化）──────────────────────────

    def save_conversation(self, path: str = None) -> str:
        """
        保存当前对话到 JSON 文件。
        默认路径: ~/.mbdsdr/conversations/<conversation_id>.json
        返回保存的文件路径。
        """
        messages = self.get_conversation_messages()
        cid = self.conversation_id
        if path:
            # 直接走 ContextManager 的文件保存（含 epoch 等元数据）
            return self.context_manager.save_to_file(path)
        # 走 ConversationStore（含 title / updated_at 等元信息）
        return self.conversation_store.save(cid, messages)

    def load_conversation(self, path: str) -> int:
        """
        从文件加载对话。path 可以是完整 JSON 路径，也可以是 conversation_id。
        返回加载的消息条数。
        """
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            # 当作 conversation_id 在 ConversationStore 目录里找
            msgs = self.conversation_store.load(path)
            self.context_manager.history = list(msgs)
            self.conversation_id = path
            return len(msgs)
        # 完整文件路径：走 ContextManager 恢复（含 epoch）
        n = self.context_manager.load_from_file(path)
        # 从文件名推断 conversation_id
        base = os.path.splitext(os.path.basename(path))[0]
        self.conversation_id = base
        return n

    def new_conversation(self):
        """清空历史并生成新 conversation_id（开启全新会话）。"""
        self.context_manager.clear_history()
        self.conversation_id = f"conv_{int(time.time() * 1000)}"
        self._repeat_key = None
        self._repeat_count = 0
        return self.conversation_id

    def get_conversation_messages(self) -> List[Dict[str, Any]]:
        """返回当前对话消息列表（供 UI 显示真对话轮次）。"""
        return self.context_manager.export_messages()

    # ── 状态查询 ────────────────────────────────────────

    def _session_log_status(self) -> Dict[str, Any]:
        """会话事件日志自报状态（Sprint 7）。观测层缺失/损坏只降级，绝不抛。

        event_count 用 SessionLog._seq（O(1) 单调计数），不每次全量读盘。
        """
        try:
            cfg_on = bool(getattr(self.config, "enable_session_log", True))
            slog = getattr(self, "_session_log", None)
            if slog is None:
                return {"enabled": cfg_on, "active": False,
                        "path": None, "event_count": 0, "broken": False}
            path = getattr(slog, "path", None)
            broken = bool(getattr(slog, "_broken", False))
            return {
                "enabled": bool(getattr(slog, "enabled", cfg_on)),
                "active": bool(path) and not broken,
                "path": path,
                "event_count": int(getattr(slog, "_seq", 0) or 0),
                "broken": broken,
            }
        except Exception:
            return {"enabled": False, "active": False,
                    "path": None, "event_count": 0, "broken": True}

    def _hooks_status(self) -> Dict[str, Any]:
        """Hook 事件总线自报状态（Sprint 7）。无 hook_manager 时降级为空统计。"""
        empty = {"available": False, "total_hooks": 0, "enabled_hooks": 0,
                 "disabled_hooks": 0, "total_triggers": 0, "history_size": 0,
                 "event_types_registered": 0}
        try:
            hm = getattr(self, "hook_manager", None)
            if hm is None or not hasattr(hm, "get_stats"):
                return empty
            stats = dict(hm.get_stats())
            stats["available"] = True
            return stats
        except Exception:
            return empty

    def _subagents_status(self) -> Dict[str, Any]:
        """子代理子系统自报状态（Sprint 7）。无 manager 时降级为空。"""
        empty = {"available": False, "count": 0,
                 "max_concurrent": None, "subagents": []}
        try:
            mgr = getattr(self, "subagent_manager", None)
            if mgr is None or not hasattr(mgr, "list_subagents"):
                return empty
            subs = list(mgr.list_subagents())
            return {
                "available": True,
                "count": len(subs),
                "max_concurrent": getattr(mgr, "max_concurrent", None),
                "subagents": subs,
            }
        except Exception:
            return empty

    def _observability_status_text(self) -> str:
        """观测层（会话日志/hook/子代理）的人类可读状态段（Sprint 7）。"""
        sl = self._session_log_status()
        hk = self._hooks_status()
        sa = self._subagents_status()
        lines = ["─" * 50, "  观测层（Sprint 7）"]
        if sl.get("broken"):
            lines.append("会话日志: ⚠ 写入已损坏（_broken），后续事件不再落盘")
        elif sl.get("active"):
            lines.append(f"会话日志: 开 · {sl.get('event_count', 0)} 条事件 · {sl.get('path')}")
        elif sl.get("enabled"):
            lines.append("会话日志: 已启用，尚未落盘（本会话还没产生事件）")
        else:
            lines.append("会话日志: 关")
        if hk.get("available"):
            lines.append(
                f"Hook 总线: {hk.get('total_hooks', 0)} 个钩子"
                f"（启用 {hk.get('enabled_hooks', 0)}）· 累计触发 "
                f"{hk.get('total_triggers', 0)} 次 · 历史 {hk.get('history_size', 0)} 条")
        else:
            lines.append("Hook 总线: 不可用")
        if sa.get("available"):
            lines.append(f"子代理: {sa.get('count', 0)} 个存活"
                         f"（并发上限 {sa.get('max_concurrent')}）")
        else:
            lines.append("子代理: 不可用")
        return "\n".join(lines)

    def get_status(self) -> Dict[str, Any]:
        """获取 Agent 完整状态。"""
        return {
            "conversation_id": self.conversation_id,
            "total_agent_calls": self.total_agent_calls,
            "config": self.config.to_dict(),
            "context": self.context_manager.get_stats().to_dict(),
            "model": self.model_manager.get_status(),
            "tools": self.tool_registry.get_stats(),
            "memory": self.memory.get_stats(),
            "last_error": self.last_error,
            # Sprint 7：观测层自报（会话日志/hook 总线/子代理），UI 可直接绑定；
            # 三者各自吞错降级，任一子系统缺失/损坏都不影响整体状态面。
            "session_log": self._session_log_status(),
            "hooks": self._hooks_status(),
            "subagents": self._subagents_status(),
        }

    def get_status_text(self) -> str:
        """获取人类可读的 Agent 状态。"""
        lines = [
            "=" * 50,
            "  MBDSDR AI 定义无线电 - Agent 状态",
            "=" * 50,
            f"会话 ID: {self.conversation_id}",
            f"总调用次数: {self.total_agent_calls}",
            "",
            self.context_manager.get_status_text(),
            "",
            self.model_manager.get_status_text(),
            "",
            self.tool_registry.get_status_text(),
            "",
            self.memory.get_status_text(),
            "",
            self._observability_status_text(),
        ]
        if self.last_error:
            lines.append(f"\n上次错误: {self.last_error}")
        return "\n".join(lines)

    # ── 快捷命令 ────────────────────────────────────────

    def run_command(self, command: str) -> str:
        """
        处理斜杠命令（/status, /context, /models, /clear 等）。
        """
        cmd = command.strip().lower()

        if cmd in ("/status", "/状态"):
            return self.get_status_text()

        if cmd in ("/context", "/上下文"):
            return self.context_manager.get_status_text()

        if cmd in ("/models", "/模型"):
            models = self.model_manager.fetch_models()
            lines = [f"当前模型: {self.model_manager.model}", "", "可用模型:"]
            for m in models:
                weak = " [弱模型]" if m.is_weak else ""
                lines.append(f"  - {m.id}{weak}")
            if getattr(self.model_manager, "_last_error", None):
                lines.append(f"\n注: {self.model_manager._last_error}")
            return "\n".join(lines)

        if cmd.startswith("/switch ") or cmd.startswith("/切换 "):
            model_id = command.split(None, 1)[1] if " " in command else ""
            if model_id:
                success, msg = self.model_manager.switch_model(model_id)
                return msg
            return "用法: /switch <模型ID>"

        if cmd in ("/clear", "/清空"):
            self.context_manager.clear_history()
            return "对话历史已清空，新 epoch 开始。"

        if cmd in ("/tools", "/工具"):
            return self.tool_registry.get_status_text()

        if cmd in ("/memory", "/记忆"):
            return self.memory.get_status_text()

        if cmd in ("/hooks", "/钩子"):
            hk = self._hooks_status()
            if not hk.get("available"):
                return "Hook 事件总线不可用（无 hook_manager）。"
            lines = [
                "Hook 事件总线:",
                f"  钩子总数: {hk.get('total_hooks', 0)}"
                f"（启用 {hk.get('enabled_hooks', 0)} / 禁用 {hk.get('disabled_hooks', 0)}）",
                f"  累计触发: {hk.get('total_triggers', 0)} 次",
                f"  事件历史: {hk.get('history_size', 0)} 条",
                f"  注册事件类型: {hk.get('event_types_registered', 0)} 种",
            ]
            try:
                hooks = self.hook_manager.list_hooks()
            except Exception:
                hooks = []
            if hooks:
                lines.append("  已注册钩子:")
                for h in hooks[:20]:
                    flag = "✓" if h.get("enabled") else "✗"
                    desc = h.get("description") or ""
                    lines.append(
                        f"    {flag} {h.get('event_type')} · {h.get('hook_id')}"
                        f" · 触发 {h.get('trigger_count', 0)} 次"
                        + (f" · {desc}" if desc else ""))
            else:
                lines.append("  （暂无已注册钩子）")
            return "\n".join(lines)

        if cmd in ("/subagents", "/子代理"):
            sa = self._subagents_status()
            if not sa.get("available"):
                return "子代理子系统不可用（无 subagent_manager）。"
            lines = [
                "子代理子系统:",
                f"  存活子代理: {sa.get('count', 0)} 个"
                f"（并发上限 {sa.get('max_concurrent')}）",
            ]
            subs = sa.get("subagents", [])
            if subs:
                for s in subs:
                    lines.append(
                        f"    - {s.get('subagent_id')} [{s.get('agent_type')}]"
                        f" 状态={s.get('status')}"
                        f" · 完成 {s.get('total_tasks_completed', 0)}"
                        f" / 失败 {s.get('total_tasks_failed', 0)}"
                        f" · 上下文 {s.get('context_size', 0)}")
            else:
                lines.append("    （暂无存活子代理）")
            return "\n".join(lines)

        if cmd in ("/session", "/会话日志"):
            sl = self._session_log_status()
            if not sl.get("enabled"):
                return "会话事件日志已在配置中关闭（enable_session_log=False）。"
            lines = ["会话事件日志:"]
            if sl.get("broken"):
                lines.append("  ⚠ 写入已损坏（_broken）：后续事件不再落盘，请检查磁盘/权限")
            elif sl.get("active"):
                lines.append(f"  状态: 落盘中 · 已记 {sl.get('event_count', 0)} 条事件")
                lines.append(f"  路径: {sl.get('path')}")
            else:
                lines.append("  状态: 已启用，尚未落盘（本会话还没产生事件）")
            try:
                slog = getattr(self, "_session_log", None)
                types = slog.event_types() if slog is not None else []
            except Exception:
                types = []
            if types:
                tail = types[-12:]
                lines.append(f"  最近 {len(tail)} 条事件类型: " + " → ".join(tail))
            return "\n".join(lines)

        if cmd in ("/help", "/帮助"):
            return """MBDSDR Agent 命令:
  /status    - 查看完整状态
  /context   - 查看上下文状态
  /models    - 查看可用模型
  /switch <模型> - 切换模型
  /clear     - 清空对话历史
  /tools     - 查看工具列表
  /memory    - 查看记忆状态
  /hooks     - 查看 Hook 事件总线
  /subagents - 查看子代理子系统
  /session   - 查看会话事件日志
  /help      - 显示帮助"""

        return f"未知命令: {command}。输入 /help 查看可用命令。"
