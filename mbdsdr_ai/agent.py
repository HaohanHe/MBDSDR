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
import math
import os
import time
from typing import List, Dict, Any, Optional, Callable

from .config import AgentConfig
from .context_manager import ContextManager, SYSTEM_PROMPT
from .model_manager import ModelManager
from .tool_registry import ToolRegistry, ToolResult
from .memory import MemoryStore
from .self_evolution import SelfEvolutionEngine
from .guardian import Guardian
from .workflow_engine import WorkflowEngine
from .scheduler import Scheduler
from .sdr_backend import SDRBackendManager
from .spectrum_processor import SpectrumProcessor
from .sdr_tools import register_sdr_tools
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
        )

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
        self.subagent_manager = SubagentManager(
            tool_registry=self.tool_registry,
            model_manager=self.model_manager,
        )

        # 6DOF 位姿融合 + AR 投影（白皮书第八章）
        self.pose_fusion = PoseFusion(mode="fused", declination=-9.0)  # 长春磁偏角约 -9°
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

        # 注册调度器工具
        self._register_scheduler_tools()

        # 注册自进化工具（如果启用）
        if self.evolution:
            self._register_evolution_tools()

        # 注册 SDR 专用工具（38个）
        register_sdr_tools(self)

        # 注册 Hook 事件钩子工具（白皮书第四章 4.4）
        self._register_hook_tools()

        # 注册 Subagents 子代理工具（白皮书第四章 4.5）
        self._register_subagent_tools()

        # 注册 6DOF 位姿 + AR 投影工具（白皮书第八章）
        self._register_pose_tools()

        # 注册工作流录制工具（白皮书第四章 4.6.4）
        self._register_workflow_recorder_tools()

        # 注册文件变更跟踪工具（白皮书第四章 4.6.6）
        self._register_file_tracker_tools()

        # 注册插件系统工具（白皮书第九章 9.3）
        self._register_plugin_tools()

        # 注册 LLM-as-Judge 工具（白皮书第四章 4.6.1）
        self._register_judge_tools()

        # 注册自学习工具（白皮书第四章 4.6.2）
        self._register_learning_tools()

        # 注册智能编排器工具（白皮书第四章 4.6.3）
        self._register_orchestrator_tools()

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
        self._register_analog_demod_tools()
        self._register_wfm_stereo_tools()
        self._register_signal_quality_tools()
        self._register_orbit_tools()
        self._register_pointing_tools()
        self._register_doppler_tools()
        self._register_time_sync_tools()
        self._register_constellation_tools()
        self._register_baseband_tools()

        # 连接工作流引擎和调度器的工具执行器
        self.workflow_engine.set_tool_executor(self._workflow_tool_executor)
        self.scheduler.workflow_engine = self.workflow_engine
        self.scheduler.tool_executor = self._workflow_tool_executor

        # 运行时状态
        self.conversation_id = f"conv_{int(time.time())}"
        self.total_agent_calls = 0
        self.last_error = None
        # repeat-call guard（借鉴 DeepSeek harness repeat-tool-reminder）
        self._repeat_key = None
        self._repeat_count = 0
        self._mcp_client = None

        # 验证配置
        errors = self.config.validate()
        if errors:
            self.last_error = "; ".join(errors)

    # ── 工具注册 ────────────────────────────────────────

    def register_mcp_tools(self, mcp_tools: List[Dict[str, Any]], mcp_call_handler: Callable):
        """
        注册 MCP 硬件工具。

        mcp_tools: 从设备 list_tools 获取的工具列表
        mcp_call_handler: 调用 MCP 工具的函数 (tool_name, args) -> result
        """
        self.tool_registry.register_mcp_tools(mcp_tools, mcp_call_handler)
        # 更新上下文管理器的工具定义
        self.context_manager.set_tool_definitions(
            self.tool_registry.get_tool_definitions()
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
                req = urllib.request.Request(url, headers={"User-Agent": "MBDSDR/1.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    raw = r.read(max_bytes + 1)
                    truncated = len(raw) > max_bytes
                    text = raw[:max_bytes].decode("utf-8", "replace")
                return ToolResult(True, f"HTTP {r.status} 取自 {url}\n{text}"
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
                return ToolResult(False, "克隆超时(120s)")
            except Exception as e:  # noqa: BLE001
                return ToolResult(False, f"克隆失败: {e}")

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
            description="把 FT8 解码出的 77 个数据位还原成可读消息文本（移植 wsjtx unpack77 主分支）。"
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
            description="把 FT8/FT4 的 28 位呼号字段解成呼号文本（移植 wsjtx unpack28）。"
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
                return ToolResult(False, "text 不能为空，如 'CQ BI4MIB OM74'")
            from mbdsdr_ai.ft8_encode import encode_ft8_text
            tones = encode_ft8_text(text)
            return ToolResult(True, json.dumps({
                "tones": tones, "n_tones": len(tones),
                "note": "79 个 8FSK 音调索引(0-7)；3 个 Costas7 同步块在符号 0-6/36-42/72-78",
            }, ensure_ascii=False))

        self.tool_registry.register(
            name="ft8_encode",
            description="FT8 编码：把标准文本消息（如 'CQ BI4MIB OM74'、'BI4MIB K1ABC 73'、"
                        "'BI4MIB K1ABC -17'）编码成 79 个 8FSK 音调索引。参数全部移植自 "
                        "WSJT-X lib/ft8 真实源码（Costas7 同步、Gray 映射、CRC14、LDPC(174,91)）。"
                        "与 ft8_soft_decode 互为往返。",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "标准消息文本，如 'CQ BI4MIB OM74'"},
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
                fft_size=int(args.get("fft_size", 2048) or 2048),
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
                return ToolResult(False, "text 不能为空，如 'CQ BI4MIB OM74'")
            from mbdsdr_ai.fst4_encode import encode_fst4_text
            tones = encode_fst4_text(text)
            return ToolResult(True, json.dumps({
                "tones": tones, "n_tones": len(tones),
                "note": "160 个 4FSK 音调索引(0-3)；5 个 8 符号同步块交替 isyncword1/2",
            }, ensure_ascii=False))

        self.tool_registry.register(
            name="fst4_encode",
            description="FST4 编码：把标准文本消息编码成 160 个 4FSK 音调索引。参数移植自 "
                        "WSJT-X lib/fst4 真实源码（5×8 同步字、2bit Gray 映射、rvec 加扰、"
                        "CRC24、LDPC(240,101)）。与 fst4_ldpc_decode / decode_fst4_message 互为往返。",
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string",
                             "description": "标准消息文本，如 'CQ BI4MIB OM74'"},
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
            try:
                res = signal_quality(np.array(iq, dtype=complex))
            except Exception as e:
                return ToolResult(False, f"信号质量统计失败: {e}")
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
        from mbdsdr_ai.orbit import predict_passes

        def _passes(args):
            try:
                lat = float(args.get("observer_lat", 43.8))
                lon = float(args.get("observer_lon", 126.5))
                alt = float(args.get("observer_alt", 0) or 0)
                hours = float(args.get("hours", 24) or 24)
                min_el = float(args.get("min_elevation", 10) or 10)
                stype = str(args.get("satellite_type", "all") or "all")
                freq = float(args.get("frequency_hz", 137.1e6) or 137.1e6)
                ps = predict_passes(lat, lon, alt, hours, min_el, stype,
                                    nominal_freq_hz=freq)
            except Exception as e:
                return ToolResult(False, f"过境预测失败: {e}")
            return ToolResult(True, json.dumps({"passes": ps, "count": len(ps)},
                                               ensure_ascii=False, default=str))

        self.tool_registry.register(
            name="predict_satellite_passes",
            description="卫星过境预测（新时空核心）：给定观察者经纬度，预测未来 N 小时内"
                        "气象/业余卫星的过境时刻、最大仰角、升落时间和多普勒频移范围。"
                        "用于 AI 反向指挥人架天线/调谐/录制。",
            parameters={
                "type": "object",
                "properties": {
                    "observer_lat": {"type": "number", "description": "观察者纬度，默认 43.8"},
                    "observer_lon": {"type": "number", "description": "观察者经度，默认 126.5"},
                    "observer_alt": {"type": "number", "description": "海拔 m，默认 0"},
                    "hours": {"type": "number", "description": "预测时长小时，默认 24"},
                    "min_elevation": {"type": "number", "description": "最低仰角度，默认 10"},
                    "satellite_type": {"type": "string", "enum": ["all", "weather", "amateur"]},
                },
            },
            handler=_passes,
            category="orbit",
        )

    def _register_pointing_tools(self):
        """新时空：当前时刻卫星位置 + 天线指向建议（AI 反向指挥人）。"""
        from mbdsdr_ai.orbit import compute_satellite_state, BUILTIN_SATS

        def _point(args):
            try:
                name = str(args.get("satellite", "NOAA 19"))
                lat = float(args.get("observer_lat", 43.8))
                lon = float(args.get("observer_lon", 126.5))
                s = compute_satellite_state(name, lat, lon)
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
                return ToolResult(False, f"云台控制失败: {e}")

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
        from mbdsdr_ai.orbit import doppler_correction

        def _doppler(args):
            try:
                name = str(args.get("satellite", "NOAA 15"))
                f0 = float(args.get("nominal_freq_hz", 137.62e6))
                lat = float(args.get("observer_lat", 43.8))
                lon = float(args.get("observer_lon", 126.5))
                r = doppler_correction(name, f0, lat, lon)
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
            th = float(args.get("threshold_db", -50.0))
            blk = int(args.get("block", 1024))
            try:
                r = squelch_gate(np.array(iq, dtype=complex),
                                 threshold_db=th, block=blk)
            except Exception as e:
                return ToolResult(False, f"静噪门控失败: {e}")
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
                return ToolResult(False, f"信号体检失败: {e}")
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

        def _save(args):
            iq = args.get("iq")
            if not isinstance(iq, list):
                return ToolResult(False, "iq 必须是复数采样列表")
            try:
                info = save_iq(
                    iq,
                    str(args.get("path", "/tmp/mbdsdr_capture.iq")),
                    float(args.get("sample_rate", 2.4e6)),
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
                    "path": {"type": "string", "description": "保存路径，默认 /tmp/mbdsdr_capture.iq"},
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

    def _workflow_tool_executor(self, tool_name: str, params: Dict[str, Any]) -> Any:
        """工作流引擎的工具执行器（桥接到 Agent 的工具注册表）。"""
        result = self.tool_registry.call(tool_name, params)
        # 录制工作流：记录本次工具调用
        try:
            self.wr.record_tool_call(tool_name, params, result.success)
        except Exception:
            pass
        if result.success:
            return result.content
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
            description="列出所有可用工作流（干扰源定位、NOAA卫星解码、SSTV解码、APRS监控、AI扫频找台、基带录制等）。工作流是预设的多步骤工具调用序列，说触发短语可自动执行。",
            parameters={"type": "object", "properties": {"category": {"type": "string", "description": "按类别过滤"}}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(wf.list_workflows(category=args.get("category")), ensure_ascii=False, indent=2)),
            category="workflow",
        )
        self.tool_registry.register(
            name="workflow_execute",
            description="执行一个工作流。工作流会自动按步骤调用工具，例如 'noaa_apt_receive_decode' 会自动找卫星→计算多普勒→调谐→录制→解码。",
            parameters={
                "type": "object",
                "properties": {
                    "workflow_name": {"type": "string", "description": "工作流名称，如 interference_localization, noaa_apt_receive_decode, sstv_receive_decode, aprs_monitor, ai_sweep_find_stations, baseband_record"},
                    "parameters": {"type": "object", "description": "工作流参数（覆盖默认值）"},
                },
                "required": ["workflow_name"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(wf.execute(args["workflow_name"], args.get("parameters", {})).to_dict(), ensure_ascii=False, indent=2)),
            category="workflow",
        )
        self.tool_registry.register(
            name="workflow_trigger_match",
            description="检查用户输入是否匹配某个工作流的触发短语。例如用户说'找干扰源'会匹配到 interference_localization 工作流。",
            parameters={"type": "object", "properties": {"text": {"type": "string", "description": "用户输入文本"}}, "required": ["text"]},
            handler=lambda args: ToolResult(success=True, content=f"匹配到工作流: {wf.match_trigger(args['text']).name}" if wf.match_trigger(args["text"]) else "未匹配到任何工作流"),
            category="workflow",
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
            description="添加定时任务。可以定时执行工作流（如每小时接收NOAA卫星）或工具调用。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名称"},
                    "task_type": {"type": "string", "description": "任务类型: workflow/tool", "default": "workflow"},
                    "target": {"type": "string", "description": "工作流名称或工具名称"},
                    "params": {"type": "object", "description": "任务参数"},
                    "schedule_type": {"type": "string", "description": "调度类型: interval(间隔)/once(一次性)", "default": "interval"},
                    "interval_seconds": {"type": "integer", "description": "间隔秒数（interval类型）", "default": 3600},
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
            handler=lambda args: ToolResult(success=True, content="任务已启用" if sched.enable_task(args["task_id"]) else "任务不存在"),
            category="scheduler",
        )
        self.tool_registry.register(
            name="scheduler_disable",
            description="禁用一个定时任务。",
            parameters={"type": "object", "properties": {"task_id": {"type": "string", "description": "任务 ID"}}, "required": ["task_id"]},
            handler=lambda args: ToolResult(success=True, content="任务已禁用" if sched.disable_task(args["task_id"]) else "任务不存在"),
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

        self.tool_registry.register(
            name="evolution_propose",
            description="提出一个自进化建议。可以改进系统提示词、工具描述、配置参数或代码算法。修改会在沙箱中验证，不会直接影响主系统。",
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
            handler=lambda args: ToolResult(
                success=True,
                content=f"进化建议已提出: {args.get('target_name')}\nID: {evo.propose(**args).id}\n下一步: 调用 evolution_evaluate 评估效果",
            ),
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
            description="提交一个进化建议为新版本（快照）。提交后可以应用或回滚。这是防幻觉变砖的关键：任何修改都有版本记录。",
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
            handler=lambda args: ToolResult(
                success=True,
                content=evo.rollback(args.get("version_id"))[1],
            ),
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
        """注册 Hook 事件钩子工具（白皮书第四章 4.4）。"""
        hm = self.hook_manager

        self.tool_registry.register(
            name="hook_list",
            description="列出所有已注册的事件钩子。包括钩子 ID、监听的事件类型、描述、优先级、启用状态、触发次数。Hook 系统让 AI 可以监听和响应各种事件（信号检测、录制完成、设备连接等）。",
            parameters={"type": "object", "properties": {"event_type": {"type": "string", "description": "按事件类型筛选（可选）"}}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(hm.list_hooks(args.get("event_type")), ensure_ascii=False, indent=2)),
            category="hook",
        )

        self.tool_registry.register(
            name="hook_trigger",
            description="手动触发一个事件。可以用来测试钩子系统，或者在工作流中主动发出事件。事件会被所有匹配的钩子监听到。",
            parameters={
                "type": "object",
                "properties": {
                    "event_type": {"type": "string", "description": "事件类型，如 sdr.signal_detected、sdr.record_complete"},
                    "data": {"type": "object", "description": "事件数据"},
                    "source": {"type": "string", "description": "事件来源", "default": "agent"},
                },
                "required": ["event_type"],
            },
            handler=lambda args: ToolResult(success=True, content=f"事件已触发: {args['event_type']}\n监听器响应数: {len(hm.trigger(Event(event_type=args['event_type'], data=args.get('data', {}), source=args.get('source', 'agent'))))}"),
            category="hook",
        )

        self.tool_registry.register(
            name="hook_history",
            description="获取事件历史记录。可以查看最近发生了哪些事件，用于调试和审计。",
            parameters={
                "type": "object",
                "properties": {
                    "event_type": {"type": "string", "description": "按事件类型筛选（可选）"},
                    "limit": {"type": "integer", "description": "返回条数，默认 50", "default": 50},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps([e.to_dict() for e in hm.get_history(args.get("event_type"), args.get("limit", 50))], ensure_ascii=False, indent=2)),
            category="hook",
        )

        self.tool_registry.register(
            name="hook_stats",
            description="获取 Hook 系统统计信息。包括总钩子数、启用数、事件类型数、总触发次数、历史记录数。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(hm.get_stats(), ensure_ascii=False, indent=2)),
            category="hook",
        )

    def _register_subagent_tools(self):
        """注册 Subagents 子代理工具（白皮书第四章 4.5）。"""
        sm = self.subagent_manager

        self.tool_registry.register(
            name="subagent_create",
            description="创建一个子代理。子代理是专门处理特定任务的 AI 助手，有自己的工具集和上下文。可用类型: spectrum_analyzer(频谱分析)、signal_decoder(信号解码)、satellite_tracker(卫星跟踪)、interference_hunter(干扰定位)、baseband_recorder(基带录制)、hardware_controller(硬件控制)、code_evolver(代码进化)。",
            parameters={
                "type": "object",
                "properties": {
                    "agent_type": {"type": "string", "description": "子代理类型: spectrum_analyzer/signal_decoder/satellite_tracker/interference_hunter/baseband_recorder/hardware_controller/code_evolver"},
                    "subagent_id": {"type": "string", "description": "自定义 ID（可选，自动生成）"},
                },
                "required": ["agent_type"],
            },
            handler=lambda args: ToolResult(success=True, content=f"子代理已创建\nID: {sm.create(args['agent_type'], args.get('subagent_id'))}\n类型: {args['agent_type']}"),
            category="subagent",
        )

        self.tool_registry.register(
            name="subagent_list",
            description="列出所有子代理及其状态。包括 ID、类型、状态、已完成任务数、失败任务数、运行时间。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(sm.list_subagents(), ensure_ascii=False, indent=2)),
            category="subagent",
        )

        self.tool_registry.register(
            name="subagent_execute",
            description="同步执行一个子代理任务，等待完成并返回结果。子代理会使用其专用工具集完成任务。",
            parameters={
                "type": "object",
                "properties": {
                    "subagent_id": {"type": "string", "description": "子代理 ID"},
                    "goal": {"type": "string", "description": "任务目标描述"},
                    "input_data": {"type": "object", "description": "输入数据（可选）"},
                    "timeout_s": {"type": "number", "description": "超时秒数，默认 120", "default": 120},
                },
                "required": ["subagent_id", "goal"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(sm.execute_task(args["subagent_id"], args["goal"], args.get("input_data"), args.get("timeout_s", 120)).to_dict(), ensure_ascii=False, indent=2)),
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
            description="获取当前 6DOF 位姿。包括位置（经纬度/海拔）、姿态（横滚/俯仰/偏航）、四元数、速度、置信度。需要 IMU+磁力计+GNSS 数据更新后才有意义。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(pf.get_pose().to_dict(), ensure_ascii=False, indent=2)),
            category="pose",
        )

        self.tool_registry.register(
            name="pose_update_imu",
            description="更新 IMU 数据（加速度计+陀螺仪+磁力计），更新位姿估计。这是 6DOF/9DOF 姿态估计的核心。数据来自 BMI260（6轴）+ TMAG5273（磁力计）。",
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
            description="更新 GNSS 数据（北斗/GPS），融合位置和速度。数据来自 ATGM336H 模块。GNSS 提供绝对位置，IMU 提供相对姿态，两者融合得到完整 6DOF 位姿。",
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
            handler=lambda args: ToolResult(success=True, content=json.dumps(wr.create_template(args["recording_id"], args["name"], args.get("description",""), args.get("parameters",{})).to_dict(), ensure_ascii=False, indent=2)),
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
            handler=lambda args: ToolResult(success=True, content=json.dumps(ft.revert_to(args["change_id"], file_writer=lambda p,c: open(p,"w",encoding="utf-8").write(c)).to_dict() if ft.revert_to(args["change_id"], file_writer=lambda p,c: open(p,"w",encoding="utf-8").write(c)) else {"error": "变更不存在"}, ensure_ascii=False, indent=2)),
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
        """注册模块化插件系统工具（白皮书第九章 9.3）。"""
        pm = self.plugin_manager

        self.tool_registry.register(
            name="plugin_list",
            description="列出所有已发现的插件及其状态。包括名称、版本、类型、状态、注册的工具/钩子/子代理数量。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(pm.list_plugins(), ensure_ascii=False, indent=2)),
            category="plugin",
        )

        self.tool_registry.register(
            name="plugin_load",
            description="加载一个插件（不启用）。加载后可以用 plugin_enable 启用。",
            parameters={
                "type": "object",
                "properties": {
                    "plugin_name": {"type": "string", "description": "插件名称"},
                },
                "required": ["plugin_name"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(pm.load_plugin(args["plugin_name"]).to_dict(), ensure_ascii=False, indent=2)),
            category="plugin",
        )

        self.tool_registry.register(
            name="plugin_enable",
            description="启用一个插件。启用后插件注册的工具、钩子、子代理会生效。",
            parameters={
                "type": "object",
                "properties": {
                    "plugin_name": {"type": "string", "description": "插件名称"},
                },
                "required": ["plugin_name"],
            },
            handler=lambda args: ToolResult(success=True, content=f"插件 {'已启用' if pm.enable_plugin(args['plugin_name']) else '启用失败'}: {args['plugin_name']}"),
            category="plugin",
        )

        self.tool_registry.register(
            name="plugin_disable",
            description="禁用一个插件。禁用后插件注册的工具、钩子、子代理会失效。",
            parameters={
                "type": "object",
                "properties": {
                    "plugin_name": {"type": "string", "description": "插件名称"},
                },
                "required": ["plugin_name"],
            },
            handler=lambda args: ToolResult(success=True, content=f"插件 {'已禁用' if pm.disable_plugin(args['plugin_name']) else '禁用失败'}: {args['plugin_name']}"),
            category="plugin",
        )

        self.tool_registry.register(
            name="plugin_stats",
            description="获取插件系统统计信息。包括总插件数、启用/禁用/错误数、注册的工具/钩子总数、按类型分类。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(pm.get_stats(), ensure_ascii=False, indent=2)),
            category="plugin",
        )

        self.tool_registry.register(
            name="plugin_install",
            description="从路径安装插件（复制到插件目录）。这是创意工坊玩法的核心：用户投稿 → 安装到本地 → 专家委员会审查 → 合入。",
            parameters={
                "type": "object",
                "properties": {
                    "source_path": {"type": "string", "description": "插件源路径"},
                    "plugin_name": {"type": "string", "description": "插件名称（可选，自动取目录名）"},
                },
                "required": ["source_path"],
            },
            handler=lambda args: ToolResult(success=True, content=f"插件已安装: {pm.install_plugin_from_path(args['source_path'], args.get('plugin_name'))}"),
            category="plugin",
        )

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
        """注册自学习工具（白皮书第四章 4.6.2）。"""
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

        self.tool_registry.register(
            name="learning_learn",
            description="批量学习：从未学习的高分经验中提取规律。分析工具调用序列、错误恢复策略、任务模式，生成可复用的学习模式。",
            parameters={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "学习的经验数量，默认 100", "default": 100},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps([p.to_dict() for p in sl.learn_batch(args.get("limit", 100))], ensure_ascii=False, indent=2)),
            category="learning",
        )

        self.tool_registry.register(
            name="learning_suggestion",
            description="根据问题获取学习建议。查找与问题相关的学习模式，给出工具调用建议。这是自学习闭环的应用阶段：用学到的规律指导新任务。",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "用户问题"},
                },
                "required": ["question"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(sl.get_suggestion(args["question"]).to_dict() if sl.get_suggestion(args["question"]) else {"message": "暂无相关学习建议"}, ensure_ascii=False, indent=2)),
            category="learning",
        )

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
        """注册智能编排器工具（白皮书第四章 4.6.3）。"""
        oc = self.orchestrator

        self.tool_registry.register(
            name="orchestrator_add_task",
            description="添加一个任务到编排器。任务可以有依赖关系、优先级、超时、重试。编排器会自动规划执行顺序。",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "任务名称"},
                    "description": {"type": "string", "description": "任务描述", "default": ""},
                    "tool_name": {"type": "string", "description": "要调用的工具名", "default": ""},
                    "tool_params": {"type": "object", "description": "工具参数", "default": {}},
                    "dependencies": {"type": "array", "items": {"type": "string"}, "description": "依赖的任务 ID 列表", "default": []},
                    "priority": {"type": "string", "description": "优先级: critical/high/medium/low", "default": "medium"},
                    "timeout_s": {"type": "number", "description": "超时秒数", "default": 60},
                    "max_retries": {"type": "integer", "description": "最大重试次数", "default": 2},
                },
                "required": ["name"],
            },
            handler=lambda args: ToolResult(success=True, content=f"任务已添加\nID: {oc.add_task(args['name'], args.get('description',''), args.get('tool_name',''), args.get('tool_params',{}), None, args.get('dependencies',[]), TaskPriority(args.get('priority','medium')), args.get('timeout_s',60), args.get('max_retries',2))}"),
            category="orchestrator",
        )

        self.tool_registry.register(
            name="orchestrator_plan",
            description="规划任务执行顺序。基于依赖关系和优先级生成拓扑排序的执行顺序。在执行前调用，查看任务将如何被调度。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(oc.plan(), ensure_ascii=False, indent=2)),
            category="orchestrator",
        )

        self.tool_registry.register(
            name="orchestrator_execute",
            description="执行所有任务。按照规划的顺序执行，支持依赖管理、重试、错误恢复。返回执行结果统计。",
            parameters={
                "type": "object",
                "properties": {
                    "stop_on_failure": {"type": "boolean", "description": "失败时是否停止", "default": False},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(oc.execute(None, args.get("stop_on_failure", False)).to_dict(), ensure_ascii=False, indent=2)),
            category="orchestrator",
        )

        self.tool_registry.register(
            name="orchestrator_list",
            description="列出所有任务及其状态。查看任务的依赖、优先级、状态、执行时间。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(oc.list_tasks(), ensure_ascii=False, indent=2)),
            category="orchestrator",
        )

        self.tool_registry.register(
            name="orchestrator_stats",
            description="获取编排器统计信息。包括总任务数、按状态/优先级分类、平均执行时间。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(oc.get_stats(), ensure_ascii=False, indent=2)),
            category="orchestrator",
        )

        self.tool_registry.register(
            name="orchestrator_pipeline",
            description="创建标准 SDR 处理流水线。一键生成完整的 SDR 处理任务：连接→设频→设采样率→设增益→频谱分析→找信号→解调→录制。这是智能编排的典型应用：复杂任务自动分解为有序步骤。",
            parameters={
                "type": "object",
                "properties": {
                    "frequency_hz": {"type": "integer", "description": "中心频率 Hz"},
                    "sample_rate": {"type": "integer", "description": "采样率 Hz", "default": 2048000},
                    "gain_db": {"type": "integer", "description": "增益 dB", "default": 40},
                    "demod_mode": {"type": "string", "description": "解调模式: fm/am/usb/lsb/cw", "default": "fm"},
                    "record_duration_s": {"type": "integer", "description": "录制时长秒", "default": 10},
                },
                "required": ["frequency_hz"],
            },
            handler=lambda args: ToolResult(success=True, content=f"SDR 流水线已创建\n任务数: {len(oc.create_sdr_pipeline(args['frequency_hz'], args.get('sample_rate',2048000), args.get('gain_db',40), args.get('demod_mode','fm'), args.get('record_duration_s',10)))}\n调用 orchestrator_plan 查看顺序，orchestrator_execute 执行"),
            category="orchestrator",
        )

    def _register_code_editor_tools(self):
        """注册自编程工具（代码编辑器）。"""
        ce = self.code_editor

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

        self.tool_registry.register(
            name="code_modify_file",
            description="修改源代码文件。修改前自动备份，支持一键恢复。这是自编程的核心：模型可以直接修改源代码。修改后建议运行 code_run_tests 验证，然后 code_hot_reload 热加载，最后 code_git_commit 提交。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径"},
                    "new_content": {"type": "string", "description": "新的完整文件内容（也可用 content 参数）"},
                    "content": {"type": "string", "description": "新的完整文件内容（new_content 的别名）"},
                    "description": {"type": "string", "description": "修改描述", "default": ""},
                },
                "required": ["file_path"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(ce.modify_file(args["file_path"], args.get("new_content") or args.get("content") or "", args.get("description","")).to_dict(), ensure_ascii=False, indent=2)),
            category="code_editor",
        )

        self.tool_registry.register(
            name="code_modify_section",
            description="局部修改源代码文件中的一段内容。比 code_modify_file 更安全，只替换指定的代码段。修改前自动备份。",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径"},
                    "old_section": {"type": "string", "description": "要替换的旧代码段（必须完全匹配）"},
                    "new_section": {"type": "string", "description": "新代码段"},
                    "description": {"type": "string", "description": "修改描述", "default": ""},
                },
                "required": ["file_path", "old_section", "new_section"],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(ce.modify_section(args["file_path"], args["old_section"], args["new_section"], args.get("description","")).to_dict(), ensure_ascii=False, indent=2)),
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

        self.tool_registry.register(
            name="code_git_commit",
            description="Git commit 提交代码修改到版本控制。这是开源协作的核心：修改经过测试验证后提交到 git 仓库，形成可追溯的版本历史。",
            parameters={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "commit 信息（描述修改内容）"},
                    "files": {"type": "array", "items": {"type": "string"}, "description": "要提交的文件列表（None=全部修改）", "default": []},
                    "edit_id": {"type": "string", "description": "关联的编辑记录 ID", "default": ""},
                },
                "required": ["message"],
            },
            handler=lambda args: ToolResult(success=True, content=(lambda r: f"{'成功' if r[0] else '失败'}: {r[1]}")(ce.git_commit(args["message"], args.get("files") or None, args.get("edit_id") or None))),
            category="code_editor",
        )

        self.tool_registry.register(
            name="code_git_status",
            description="获取 git 状态。查看当前分支、修改的文件、最近的 commit。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=json.dumps(ce.git_status(), ensure_ascii=False, indent=2)),
            category="code_editor",
        )

        self.tool_registry.register(
            name="code_rollback",
            description="一键恢复：回滚到修改前的状态。防幻觉变砖的最后一道防线：任何代码修改都可以一键撤销。",
            parameters={
                "type": "object",
                "properties": {
                    "edit_id": {"type": "string", "description": "要回滚的编辑记录 ID"},
                },
                "required": ["edit_id"],
            },
            handler=lambda args: ToolResult(success=True, content=(lambda r: f"{'成功' if r[0] else '失败'}: {r[1]}")(ce.rollback(args["edit_id"]))),
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
            handler=lambda args: ToolResult(success=True, content=json.dumps(compute_pointing_guidance(AltAzCoord(args["target_alt"], args["target_az"]), AltAzCoord(args["current_alt"], args["current_az"]), AntennaParams(beamwidth_deg=args.get("beamwidth_deg",5)) if False else None), ensure_ascii=False, indent=2)),
            category="astronomy",
        )

        self.tool_registry.register(
            name="astro_time_info",
            description="获取天文时间系统信息（儒略日JD/简化儒略日MJD/格林尼治恒星时GMST/地方恒星时LST）。用于卫星轨道计算和精确时间同步。",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=lambda args: ToolResult(success=True, content=(lambda jd: json.dumps({"unix_time": time.time(), "jd": round(jd, 6), "mjd": round(jd_to_mjd(jd), 6), "gmst_hms": lst_to_hms(jd_to_gmst(jd)), "lst_hms": lst_to_hms(jd_to_lst(jd, obs.longitude_deg)), "observer_lon": obs.longitude_deg}, ensure_ascii=False, indent=2))(unix_to_jd())),
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
                    "iq_samples": {"type": "array", "items": {"type": "number"}, "description": "IQ 样本（实数列表，交替 I/Q），如 [I1,Q1,I2,Q2,...]。如果为空则使用模拟信号演示。", "default": []},
                    "sample_rate": {"type": "number", "description": "采样率（Hz）", "default": 1.0},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(amr.classify_iq([complex(args["iq_samples"][i], args["iq_samples"][i+1]) for i in range(0, len(args["iq_samples"])-1, 2)] if args.get("iq_samples") else [complex(math.cos(2*math.pi*0.01*t), math.sin(2*math.pi*0.01*t)) for t in range(1000)], args.get("sample_rate", 1.0)).to_dict(), ensure_ascii=False, indent=2)),
            category="amr",
        )

        self.tool_registry.register(
            name="amr_extract_features",
            description="从 IQ 样本中提取 25 维 AMR 特征。包括时域特征（幅度统计/峰均比/偏度/峰度）、频域特征（带宽/频谱质心/平坦度/滚降/频谱幅度变异系数）、统计特征（过零率/瞬时频率/相位/IQ相关性/星座图密度）。",
            parameters={
                "type": "object",
                "properties": {
                    "iq_samples": {"type": "array", "items": {"type": "number"}, "description": "IQ 样本（实数列表，交替 I/Q）", "default": []},
                    "sample_rate": {"type": "number", "description": "采样率（Hz）", "default": 1.0},
                },
                "required": [],
            },
            handler=lambda args: ToolResult(success=True, content=json.dumps(amr.extract_features_from_iq([complex(args["iq_samples"][i], args["iq_samples"][i+1]) for i in range(0, len(args["iq_samples"])-1, 2)] if args.get("iq_samples") else [complex(math.cos(2*math.pi*0.01*t), math.sin(2*math.pi*0.01*t)) for t in range(1000)], args.get("sample_rate", 1.0)).to_dict(), ensure_ascii=False, indent=2)),
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

    def chat(self, user_input: str, max_tool_rounds: int = 10,
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
            }

        # 1. 添加用户消息
        self.context_manager.add_user_message(user_input)

        # 2. 检索相关记忆
        memory_context = self.memory.build_memory_context(user_input)

        # 3. 构建系统提示词（含记忆 + 工具说明）
        system_prompt = SYSTEM_PROMPT
        if memory_context:
            system_prompt += memory_context
        system_prompt += f"\n\n当前可用工具数: {len(self.tool_registry.get_tool_names())}。调用工具前先用 list_tools 确认工具列表。"
        self.context_manager.set_system_prompt(system_prompt)

        # 4. 工具调用循环
        all_tool_calls = []
        all_tool_results = []
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        compacted = False
        final_content = ""
        last_error = None

        for round_num in range(max_tool_rounds):
            # 检查是否需要压缩
            if self.context_manager.needs_compaction():
                self.context_manager.compact()
                compacted = True

            # 构建 API 消息
            messages = self.context_manager.build_api_messages()
            tools = self.tool_registry.get_tool_definitions() if self.config.enable_tool_calling else None

            # 调用 LLM（流式，on_delta 回调实时吐字）
            response = {"success": True, "content": "", "tool_calls": [],
                        "usage": {}, "model": self.model_manager.model,
                        "latency_ms": 0}
            try:
                for ev in self.model_manager.chat_stream(messages=messages,
                                                         tools=tools,
                                                         tool_choice="auto"):
                    if ev.get("done"):
                        response = ev
                    elif on_delta is not None and ev.get("delta"):
                        try:
                            on_delta(ev["delta"])
                        except Exception:
                            pass
            except Exception as e:
                response = {"success": False, "content": "", "tool_calls": [],
                            "usage": {}, "error": str(e)}

            # 累计用量
            usage = response.get("usage", {})
            total_usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += usage.get("completion_tokens", 0)
            total_usage["total_tokens"] += usage.get("total_tokens", 0)

            if not response.get("success"):
                last_error = response.get("error", "unknown_error")
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
                final_content = content
                # 添加 assistant 消息到上下文
                self.context_manager.add_assistant_message(content)
                break

            # 有工具调用，执行工具
            # 先添加 assistant 消息（含 tool_calls）到上下文
            self.context_manager.add_assistant_message(content, tool_calls=tool_calls)

            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "")
                all_tool_calls.append({"name": tool_name, "arguments": fn.get("arguments", "")})

                # 执行工具（可重试：临时不可用/超时/忙；参数错误不重试，直接喂模型改）
                RETRY_BAD = ("未知", "不是一个", "必须", "可选", "为空", "不能为空",
                             "不存在", "找不到", "不支持")
                RETRY_OK = ("未连接", "无设备", "没有设备", "暂时", "重试",
                            "timeout", "timed out", "busy", "忙", "不可用", "未就绪")
                result = self.tool_registry.call_from_model(tc)
                for _attempt in range(2):  # 最多再重试 2 次
                    if getattr(result, "success", False):
                        break
                    low = (getattr(result, "content", "") or "").lower()
                    if any(h in low for h in RETRY_BAD) or not any(h in low for h in RETRY_OK):
                        break  # 参数错误或非临时错误：交给模型改，不空转
                    time.sleep(0.4)
                    result = self.tool_registry.call_from_model(tc)
                all_tool_results.append(result.to_dict())

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

                # 添加工具结果到上下文
                self.context_manager.add_tool_message(
                    tool_call_id=tc.get("id", ""),
                    content=result.content,
                    tool_name=tool_name,
                )

            # 继续循环，让模型看到工具结果后继续

        # 5. 最终检查压缩
        if self.context_manager.needs_compaction():
            self.context_manager.compact()
            compacted = True

        # 6. 遗忘旧记忆（每 10 次调用执行一次）
        if self.total_agent_calls % 10 == 0:
            self.memory.forget_old()

        latency_ms = round((time.time() - start_time) * 1000, 1)

        return {
            "content": final_content,
            "tool_calls": all_tool_calls,
            "tool_results": all_tool_results,
            "usage": total_usage,
            "latency_ms": latency_ms,
            "compacted": compacted,
            "error": last_error,
            "rounds": len(all_tool_calls) + (1 if final_content else 0),
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

    def _compaction_callback(self, history_text: str) -> str:
        """
        上下文压缩回调：用 LLM 总结被裁剪的历史。
        """
        try:
            messages = [
                {"role": "system", "content": "你是一个对话摘要助手。请简洁总结以下对话历史的关键信息，保留重要的频率、设置、用户偏好和未完成的任务。"},
                {"role": "user", "content": f"请总结以下对话历史:\n\n{history_text[:3000]}"},
            ]
            response = self.model_manager.chat(messages=messages, max_tokens=500)
            if response.get("success"):
                return response.get("content", "摘要生成失败")
        except Exception as e:
            pass
        return f"[自动压缩] 已裁剪早期对话历史"

    # ── 状态查询 ────────────────────────────────────────

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

        if cmd in ("/help", "/帮助"):
            return """MBDSDR Agent 命令:
  /status    - 查看完整状态
  /context   - 查看上下文状态
  /models    - 查看可用模型
  /switch <模型> - 切换模型
  /clear     - 清空对话历史
  /tools     - 查看工具列表
  /memory    - 查看记忆状态
  /help      - 显示帮助"""

        return f"未知命令: {command}。输入 /help 查看可用命令。"
