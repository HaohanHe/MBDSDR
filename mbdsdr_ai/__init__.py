"""
MBDSDR AI 内核
===============
AI 定义无线电的智能体内核，对照 Kilo Code/Hermes/Mimo/OpenCode 设计。

核心能力：
- 上下文管理：压缩、长度可设置、token 查询、Context Epoch
- 模型管理：模型列表查询、切换、参数设置、多 provider
- 工具调用：MCP 工具注册、可靠调用、输出大小限制
- 记忆系统：会话记忆、长期记忆、索引检索
- 自进化：沙箱执行、用户确认、一键恢复

用法:
    from mbdsdr_ai import MBDSDRAgent, AgentConfig

    config = AgentConfig(
        api_key="sk-...",
        base_url="https://api.siliconflow.cn/v1",
        model="Qwen/Qwen3.6-35B-A3B",
        max_context_tokens=8192,
        compaction_threshold=0.8,
    )
    agent = MBDSDRAgent(config)
    response = agent.chat("调谐到 FM 98.5 并录 30 秒")
"""

from .config import AgentConfig, load_config, save_config
from .context_manager import ContextManager, ContextStats
from .model_manager import ModelManager, ModelInfo
from .tool_registry import ToolRegistry, ToolResult
from .agent import MBDSDRAgent
from .memory import MemoryStore
from .version_store import VersionStore
from .sandbox import Sandbox, SandboxResult
from .self_evolution import SelfEvolutionEngine, EvolutionProposal
from .guardian import Guardian, Snapshot
from .workflow_engine import WorkflowEngine, Workflow, WorkflowResult
from .scheduler import Scheduler, ScheduledTask
from .sdr_backend import (
    SDRBackendManager, SDRBackend, RTLSDRBackend,
    AISDRMiniBackend, HackRFBackend, USRPBackend, FileIQBackend,
    SoapySDRBackend, SDRDevice, SDRStatus,
    enumerate_all_sdr_devices, build_backend_for_device,
)
from .spectrum_processor import SpectrumProcessor, SpectrumData, WaterfallData
from .sdr_tools import register_sdr_tools
from .dsp import (
    DCBlocker, IQCalibrator, AGC,
    front_end, decimate, demodulate,
    fm_demod, am_demod, ssb_demod, cw_demod,
    write_cf32, write_cs16, write_wav, write_csv, write_sidecar_json,
    compute_snr, estimate_bandwidth,
)
from .decoders import (
    decode_noaa_apt, decode_sstv, decode_digital_mode,
    detect_fhss, list_visible_satellites, compute_doppler_correction,
    SatellitePass, BUILTIN_TLE, SATELLITE_FREQUENCIES,
)
from .hooks import HookManager, Event, EventType, Hook, create_logging_hook, create_signal_alert_hook
from .subagents import SubagentManager, Subagent, SubagentTask, SubagentResult, SubagentStatus
from .pose import PoseFusion, ARProjector, IMUData, GPSData, Pose, ARMarker, ComplementaryFilter, TiltCompensatedCompass, MadgwickFilter
from .workflow_recorder import WorkflowRecorder, Recording, WorkflowTemplate, ToolCallRecord
from .file_tracker import FileChangeTracker, FileChange, ChangeType
from .plugin_system import PluginManager, Plugin, PluginManifest, PluginStatus, PluginType
from .llm_judge import LLMJudge, JudgeResult, JudgeDimension, DimensionScore
from .self_learning import SelfLearningEngine, Experience, ExperienceType, LearnedPattern
from .orchestrator import Orchestrator, Task, TaskPriority, TaskStatus, OrchestrationResult
from .code_editor import CodeEditor, EditRecord, EditStatus
from .astronomy import Observer, EquatorialCoord, AltAzCoord, AntennaParams
from .amr import AMRClassifier, AMRResult, ModulationType

__version__ = "0.8.0"
__all__ = [
    "MBDSDRAgent",
    "AgentConfig",
    "load_config",
    "save_config",
    "ContextManager",
    "ContextStats",
    "ModelManager",
    "ModelInfo",
    "ToolRegistry",
    "ToolResult",
    "MemoryStore",
    "VersionStore",
    "Sandbox",
    "SandboxResult",
    "SelfEvolutionEngine",
    "EvolutionProposal",
    "Guardian",
    "Snapshot",
    "WorkflowEngine",
    "Workflow",
    "WorkflowResult",
    "Scheduler",
    "ScheduledTask",
]
