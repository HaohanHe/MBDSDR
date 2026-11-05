# SPDX-License-Identifier: MIT
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

惰性导出（2026-11-06，性能实测）：
    本包原先在 ``__init__`` 里一次性导入全部子模块（含 scipy / agent /
    requests），于是桌面端任何一句 ``from mbdsdr_ai.squelch import ...``
    都会先付这笔导入税。实测本机 ``import main_window`` 6.58 s，其中
    5.40 s 花在这一个包里（scipy.signal 2.82 s、agent 3.40 s 累计）。
    改为 PEP 562 惰性导出后，包初始化只做 Windows DLL 引导；具体符号在
    第一次真正被访问时才导入对应子模块。``from mbdsdr_ai import X`` 与
    ``mbdsdr_ai.X`` 两种用法语义不变（返回值、异常、类型完全一致）。
"""

# Windows：导入任何子模块（含 SDRBackendManager 发现）前，先引导 librtlsdr DLL。
# 父包 __init__ 在所有子模块之前执行，因此这一处覆盖 agent/GUI/MCP/CLI 全部入口，
# 不依赖调用方手动 setup；非 Windows 或引导失败时静默跳过，绝不影响导入。
try:
    import sys as _sys
    if _sys.platform == "win32":
        from .windows_setup import setup_rtlsdr_windows as _setup_rtl
        _setup_rtl(import_test=False)
except Exception:
    pass

import importlib as _importlib

__version__ = "0.8.0"

# 公开符号 → 所属子模块。惰性导出表；新增导出时在这里登记即可。
_EXPORTS = {
    "AgentConfig": "config",
    "load_config": "config",
    "save_config": "config",
    "ContextManager": "context_manager",
    "ContextStats": "context_manager",
    "ModelManager": "model_manager",
    "ModelInfo": "model_manager",
    "ToolRegistry": "tool_registry",
    "ToolResult": "tool_registry",
    "MBDSDRAgent": "agent",
    "MemoryStore": "memory",
    "VersionStore": "version_store",
    "Sandbox": "sandbox",
    "SandboxResult": "sandbox",
    "SelfEvolutionEngine": "self_evolution",
    "EvolutionProposal": "self_evolution",
    "Guardian": "guardian",
    "Snapshot": "guardian",
    "WorkflowEngine": "workflow_engine",
    "Workflow": "workflow_engine",
    "WorkflowResult": "workflow_engine",
    "Scheduler": "scheduler",
    "ScheduledTask": "scheduler",
    "SDRBackendManager": "sdr_backend",
    "SDRBackend": "sdr_backend",
    "RTLSDRBackend": "sdr_backend",
    "AISDRMiniBackend": "sdr_backend",
    "HackRFBackend": "sdr_backend",
    "USRPBackend": "sdr_backend",
    "FileIQBackend": "sdr_backend",
    "SoapySDRBackend": "sdr_backend",
    "SDRDevice": "sdr_backend",
    "SDRStatus": "sdr_backend",
    "enumerate_all_sdr_devices": "sdr_backend",
    "build_backend_for_device": "sdr_backend",
    "SpectrumProcessor": "spectrum_processor",
    "SpectrumData": "spectrum_processor",
    "WaterfallData": "spectrum_processor",
    "register_sdr_tools": "sdr_tools",
    "DCBlocker": "dsp",
    "IQCalibrator": "dsp",
    "AGC": "dsp",
    "front_end": "dsp",
    "decimate": "dsp",
    "demodulate": "dsp",
    "fm_demod": "dsp",
    "am_demod": "dsp",
    "ssb_demod": "dsp",
    "cw_demod": "dsp",
    "write_cf32": "dsp",
    "write_cs16": "dsp",
    "write_wav": "dsp",
    "write_csv": "dsp",
    "write_sidecar_json": "dsp",
    "compute_snr": "dsp",
    "estimate_bandwidth": "dsp",
    "decode_noaa_apt": "decoders",
    "decode_sstv": "decoders",
    "decode_digital_mode": "decoders",
    "detect_fhss": "decoders",
    "list_visible_satellites": "decoders",
    "compute_doppler_correction": "decoders",
    "SatellitePass": "decoders",
    "BUILTIN_TLE": "decoders",
    "SATELLITE_FREQUENCIES": "decoders",
    "HookManager": "hooks",
    "Event": "hooks",
    "EventType": "hooks",
    "Hook": "hooks",
    "create_logging_hook": "hooks",
    "create_signal_alert_hook": "hooks",
    "SubagentManager": "subagents",
    "Subagent": "subagents",
    "SubagentTask": "subagents",
    "SubagentResult": "subagents",
    "SubagentStatus": "subagents",
    "PoseFusion": "pose",
    "ARProjector": "pose",
    "IMUData": "pose",
    "GPSData": "pose",
    "Pose": "pose",
    "ARMarker": "pose",
    "ComplementaryFilter": "pose",
    "TiltCompensatedCompass": "pose",
    "MadgwickFilter": "pose",
    "WorkflowRecorder": "workflow_recorder",
    "Recording": "workflow_recorder",
    "WorkflowTemplate": "workflow_recorder",
    "ToolCallRecord": "workflow_recorder",
    "FileChangeTracker": "file_tracker",
    "FileChange": "file_tracker",
    "ChangeType": "file_tracker",
    "PluginManager": "plugin_system",
    "Plugin": "plugin_system",
    "PluginManifest": "plugin_system",
    "PluginStatus": "plugin_system",
    "PluginType": "plugin_system",
    "LLMJudge": "llm_judge",
    "JudgeResult": "llm_judge",
    "JudgeDimension": "llm_judge",
    "DimensionScore": "llm_judge",
    "SelfLearningEngine": "self_learning",
    "Experience": "self_learning",
    "ExperienceType": "self_learning",
    "LearnedPattern": "self_learning",
    "Orchestrator": "orchestrator",
    "Task": "orchestrator",
    "TaskPriority": "orchestrator",
    "TaskStatus": "orchestrator",
    "OrchestrationResult": "orchestrator",
    "CodeEditor": "code_editor",
    "EditRecord": "code_editor",
    "EditStatus": "code_editor",
    "Observer": "astronomy",
    "EquatorialCoord": "astronomy",
    "AltAzCoord": "astronomy",
    "AntennaParams": "astronomy",
    "AMRClassifier": "amr",
    "AMRResult": "amr",
    "ModulationType": "amr",
}

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


def __getattr__(name):
    """PEP 562：按需导入 ``name`` 所属子模块并缓存结果。

    子模块名本身（``mbdsdr_ai.dsp`` 之类）也可直接属性访问：先导入该子模块，
    再返回模块对象。未知名字照常抛 ``AttributeError``（不做静默兜底）。
    """
    submod = _EXPORTS.get(name)
    if submod is None:
        # 允许 ``mbdsdr_ai.<submodule>`` 的直接属性访问（importlib 会找到已存在的模块）
        try:
            mod = _importlib.import_module("." + name, __name__)
        except ImportError:
            raise AttributeError(
                f"module {__name__!r} has no attribute {name!r}") from None
        globals()[name] = mod
        return mod
    mod = _importlib.import_module("." + submod, __name__)
    try:
        value = getattr(mod, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
            f"（子模块 {submod!r} 中也没有）") from None
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(list(globals()) + __all__ + list(_EXPORTS)))
