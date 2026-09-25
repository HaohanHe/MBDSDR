"""
MBDSDR AI 内核 - 工作流引擎
============================
Workflow Engine：JSON 定义的多步骤工具调用序列。

核心能力（对照之前 ~/.mbdsdr/workflows/ 的设计）：
- JSON 定义的多步骤工具调用序列
- 每个 step 有 tool_name, params, description, timeout, retry_on_failure, max_retries, condition
- 支持参数模板（{{variable}}），上一步的输出可以作为下一步的输入
- 支持条件执行（condition），根据上一步结果决定是否执行
- 支持触发短语（trigger_phrases），用户说这些短语自动触发工作流
- 工作流可以被 AI 调用，也可以被用户自然语言触发
- 执行结果记录（usage_count, success_count）
- 预设工作流：APRS监控、干扰源定位、NOAA卫星解码、SSTV解码

工作流是 AI 技能的具体化：AI 说"找干扰源"→ 触发 interference_localization 工作流 → 自动执行多步骤工具调用。
"""

import os
import json
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable


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
    trigger_phrases: List[str] = field(default_factory=list)  # 触发短语
    author: str = "mbdsdr"
    source: str = "preset"  # preset / user / ai_evolved
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
            trigger_phrases=data.get("trigger_phrases", []),
            author=data.get("author", "mbdsdr"),
            source=data.get("source", "preset"),
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
            "trigger_phrases": self.trigger_phrases,
            "author": self.author,
            "source": self.source,
            "version": self.version,
            "usage_count": self.usage_count,
            "success_count": self.success_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    def match_trigger(self, text: str) -> bool:
        """检查文本是否匹配触发短语。"""
        text_lower = text.lower()
        return any(phrase.lower() in text_lower for phrase in self.trigger_phrases)


@dataclass
class WorkflowResult:
    """工作流执行结果。"""
    workflow_name: str
    success: bool
    total_steps: int
    completed_steps: int
    failed_step: Optional[int] = None
    error: str = ""
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
            "step_results": self.step_results,
            "execution_time_ms": round(self.execution_time_ms, 1),
            "outputs": self.outputs,
        }


class WorkflowEngine:
    """
    工作流引擎。

    管理工作流的注册、加载、执行、触发。
    工具调用通过 tool_executor 回调函数执行（由外部 MCP 客户端提供）。
    """

    def __init__(self, workflows_dir: str = "~/.mbdsdr/workflows"):
        self.workflows_dir = os.path.expanduser(workflows_dir)
        self.workflows: Dict[str, Workflow] = {}
        self.tool_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None
        os.makedirs(self.workflows_dir, exist_ok=True)
        self._load_presets()
        self._load_user_workflows()

    @staticmethod
    def _get_ground_station() -> Dict[str, Any]:
        """从 ~/.mbdsdr/config.json 读取地面站坐标。

        返回 {"lat": float|None, "lon": float|None, "configured": bool}。
        未配置时 lat/lon 为 None，调用方应提示用户配置。
        """
        cfg_path = os.path.expanduser("~/.mbdsdr/config.json")
        result = {"lat": None, "lon": None, "configured": False}
        try:
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            else:
                return result
        except Exception:
            return result
        try:
            lat = cfg.get("ground_station_lat")
            lon = cfg.get("ground_station_lon")
            if lat is not None and lon is not None:
                result["lat"] = float(lat)
                result["lon"] = float(lon)
                result["configured"] = True
        except (TypeError, ValueError):
            pass
        return result

    # ── 预设工作流 ──────────────────────────────────────

    def _load_presets(self):
        """加载预设工作流（从之前系统的设计恢复）。"""
        presets = [
            self._preset_interference_localization(),
            self._preset_noaa_apt(),
            self._preset_sstv(),
            self._preset_aprs_monitor(),
            self._preset_ai_sweep_find_stations(),
            self._preset_baseband_record(),
        ]
        for wf in presets:
            self.workflows[wf.name] = wf

    def _preset_interference_localization(self) -> Workflow:
        """干扰源定位工作流（全向检测→定向测向→质心定位→云台指向）。"""
        return Workflow.from_dict({
            "name": "interference_localization",
            "description": "干扰源定位：GP全向天线检测干扰频率→切八木/定向天线→云台方位步进测向→质心定位→云台指向",
            "category": "analysis",
            "tags": ["干扰源", "定位", "测向", "云台", "八木", "频谱扫描"],
            "steps": [
                {"step_id": 1, "tool_name": "sdr_spectrum_analyze", "params": {"fft_size": 4096, "num_samples": 8}, "description": "阶段一 GP全向天线：全频段扫描，发现异常功率", "timeout": 30, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 2, "tool_name": "signal_detect_interference", "params": {}, "description": "检测并锁定干扰信号频率/带宽/类型", "timeout": 30, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 3, "tool_name": "sdr_set_frequency", "params": {"frequency_hz": "{{interference_freq_hz}}"}, "description": "接收机调谐到干扰中心频率", "timeout": 15, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 4, "tool_name": "gimbal_connect", "params": {"mode": "{{gimbal_mode}}"}, "description": "阶段二 切八木/定向天线并连接云台（board_pwm/rotctld/manual，默认manual）", "timeout": 15, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 5, "tool_name": "gimbal_rssi_sweep", "params": {"az_start": 0, "az_end": 360, "step": 30, "elevation": 20}, "description": "阶段三 云台带八木0-360°步进测向，每步读RSSI（人工模式给逐方位指引）", "timeout": 120, "retry_on_failure": True, "max_retries": 1, "condition": ""},
                {"step_id": 6, "tool_name": "gnss_direction_find", "params": {"rssi_by_azimuth": "{{rssi_by_azimuth}}"}, "description": "阶段四 质心法由方位-RSSI估算干扰源方位角", "timeout": 15, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 7, "tool_name": "gimbal_rssi_sweep", "params": {"az_start": "{{peak_az_minus_20}}", "az_end": "{{peak_az_plus_20}}", "step": 10, "elevation": 20}, "description": "阶段五 粗定位方位±20°内10°精扫", "timeout": 90, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 8, "tool_name": "gimbal_point", "params": {"azimuth": "{{interference_az}}", "elevation": 20}, "description": "阶段六 云台指向精定位方位，IMU闭环确认对准", "timeout": 20, "retry_on_failure": False, "max_retries": 0, "condition": ""},
            ],
            "parameters": {
                "interference_freq_hz": {"default": "", "type": "float", "description": "阶段一检测到的干扰中心频率Hz（空则先全扫）"},
                "gimbal_mode": {"default": "manual", "type": "string", "description": "云台后端 board_pwm/rotctld/manual"},
                "rssi_by_azimuth": {"default": "", "type": "object", "description": "粗扫得到的{方位:RSSI}，自动传递"},
                "interference_az": {"default": "", "type": "float", "description": "质心定位得到的干扰方位角，自动传递"},
            },
            "trigger_phrases": ["找干扰源", "干扰定位", "查干扰", "干扰源在哪", "干扰源什么方向", "测向"],
            "author": "mbdsdr",
            "source": "preset",
            "version": 2,
        })

    def _preset_noaa_apt(self) -> Workflow:
        """NOAA 气象卫星 APT 接收解码工作流。

        地面站坐标从 ~/.mbdsdr/config.json 的 ground_station_lat/lon 读取；
        未配置时 lat/lon 默认为空字符串，执行时步骤会提示"地面站坐标未配置"。
        """
        gs = self._get_ground_station()
        lat_default = gs["lat"] if gs["configured"] else ""
        lon_default = gs["lon"] if gs["configured"] else ""
        lat_desc = (
            f"地面站纬度（已配置: {gs['lat']}）"
            if gs["configured"]
            else "地面站纬度（未配置，请在 ~/.mbdsdr/config.json 设置 ground_station_lat）"
        )
        lon_desc = (
            f"地面站经度（已配置: {gs['lon']}）"
            if gs["configured"]
            else "地面站经度（未配置，请在 ~/.mbdsdr/config.json 设置 ground_station_lon）"
        )
        sky_view_desc = (
            "查找当前可见的 NOAA 卫星"
            if gs["configured"]
            else "查找当前可见的 NOAA 卫星（地面站坐标未配置，请先配置 ground_station_lat/lon）"
        )
        return Workflow.from_dict({
            "name": "noaa_apt_receive_decode",
            "description": "NOAA 气象卫星 APT 图像接收与自动解码完整工作流",
            "category": "satellite",
            "tags": ["NOAA", "APT", "气象卫星", "图像解码"],
            "steps": [
                {"step_id": 1, "tool_name": "sdr_satellite_sky_view", "params": {"latitude": "{{ground_station_lat}}", "longitude": "{{ground_station_lon}}"}, "description": sky_view_desc, "timeout": 30, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 2, "tool_name": "sdr_satellite_doppler", "params": {"satellite_name": "NOAA 19", "frequency_hz": 137100000, "latitude": "{{ground_station_lat}}", "longitude": "{{ground_station_lon}}"}, "description": "计算多普勒修正频率", "timeout": 30, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 3, "tool_name": "sdr_set_frequency", "params": {"frequency_hz": "{{doppler_freq}}"}, "description": "设置接收频率（多普勒修正）", "timeout": 30, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 4, "tool_name": "sdr_record_start", "params": {"duration": 900, "format": "wav"}, "description": "录制卫星过境信号（15分钟）", "timeout": 30, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 5, "tool_name": "sdr_decode_noaa_apt", "params": {"input_path": "{{recording_path}}"}, "description": "解码 APT 气象图像", "timeout": 60, "retry_on_failure": True, "max_retries": 2, "condition": ""},
            ],
            "parameters": {
                "doppler_freq": {"default": 137100000, "type": "float", "description": "多普勒修正频率"},
                "recording_path": {"default": "", "type": "string", "description": "录制文件路径"},
                "ground_station_lat": {"default": lat_default, "type": "float", "description": lat_desc},
                "ground_station_lon": {"default": lon_default, "type": "float", "description": lon_desc},
            },
            "trigger_phrases": ["接收NOAA卫星", "解码气象卫星", "NOAA APT", "看卫星云图", "卫星云图"],
            "author": "mbdsdr",
            "source": "preset",
            "version": 2,
        })

    def _preset_sstv(self) -> Workflow:
        """SSTV 慢扫描电视接收解码工作流。"""
        return Workflow.from_dict({
            "name": "sstv_receive_decode",
            "description": "SSTV 慢扫描电视图像接收与解码工作流",
            "category": "image",
            "tags": ["SSTV", "慢扫描电视", "图像解码"],
            "steps": [
                {"step_id": 1, "tool_name": "sdr_set_frequency", "params": {"frequency_hz": 14230000}, "description": "设置 SSTV 常用频率 14.230MHz", "timeout": 10, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 2, "tool_name": "sdr_set_demod", "params": {"mode": "USB"}, "description": "设置 USB 上边带解调模式", "timeout": 10, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 3, "tool_name": "sdr_record_start", "params": {"duration": 120, "format": "wav"}, "description": "录制 SSTV 信号（2分钟）", "timeout": 30, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 4, "tool_name": "sdr_decode_sstv", "params": {"input_path": "{{recording_path}}"}, "description": "解码 SSTV 图像", "timeout": 60, "retry_on_failure": True, "max_retries": 2, "condition": ""},
            ],
            "parameters": {"recording_path": {"default": "", "type": "string", "description": "录制文件路径"}},
            "trigger_phrases": ["SSTV", "慢扫描电视", "接收SSTV", "解码SSTV"],
            "author": "mbdsdr",
            "source": "preset",
            "version": 1,
        })

    def _preset_aprs_monitor(self) -> Workflow:
        """APRS 自动位置报告系统监控工作流。"""
        return Workflow.from_dict({
            "name": "aprs_monitor",
            "description": "APRS 自动位置报告系统接收与解析工作流",
            "category": "data",
            "tags": ["APRS", "位置报告", "数据解码"],
            "steps": [
                {"step_id": 1, "tool_name": "sdr_set_frequency", "params": {"frequency_hz": 144640000}, "description": "设置 APRS 频率 144.640MHz（中国 VHF）", "timeout": 10, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 2, "tool_name": "sdr_set_demod", "params": {"mode": "NFM"}, "description": "设置窄带 FM（APRS VHF 为 1200 波特 AFSK over FM）", "timeout": 10, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 3, "tool_name": "sdr_record_start", "params": {"duration": 300, "format": "wav"}, "description": "录制 5 分钟 APRS 频点信号", "timeout": 30, "retry_on_failure": True, "max_retries": 1, "condition": ""},
                {"step_id": 4, "tool_name": "sdr_decode_aprs", "params": {"input_path": "{{recording_path}}"}, "description": "解析 APRS 数据包（位置/信标/消息）", "timeout": 60, "retry_on_failure": True, "max_retries": 1, "condition": ""},
            ],
            "parameters": {"recording_path": {"default": "", "type": "string", "description": "录制文件路径，录制步骤自动回填"}} ,
            "trigger_phrases": ["APRS", "位置报告", "监控APRS", "APRS监控"],
            "author": "mbdsdr",
            "source": "preset",
            "version": 1,
        })

    def _preset_ai_sweep_find_stations(self) -> Workflow:
        """AI 扫频找台工作流。"""
        return Workflow.from_dict({
            "name": "ai_sweep_find_stations",
            "description": "AI 辅助扫频找台：步进扫频→信号强度分析→自动调谐到最强台",
            "category": "reception",
            "tags": ["扫频", "找台", "AI辅助", "FM广播"],
            "steps": [
                {"step_id": 1, "tool_name": "sdr_ai_sweep", "params": {"freq_start_hz": 87500000, "freq_end_hz": 108000000, "step_hz": 100000, "auto_tune": True}, "description": "FM 广播频段 87.5-108MHz 步进扫频，自动调谐到最强台", "timeout": 60, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 2, "tool_name": "sdr_spectrum_find_signals", "params": {"threshold_db": -50}, "description": "复核当前频点附近的强信号电台", "timeout": 10, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 3, "tool_name": "sdr_set_demod", "params": {"mode": "WFM"}, "description": "设置宽带 FM 解调（广播制式）", "timeout": 10, "retry_on_failure": False, "max_retries": 0, "condition": ""},
            ],
            "parameters": {},
            "trigger_phrases": ["扫频找台", "找电台", "找个台", "扫频", "找FM台"],
            "author": "mbdsdr",
            "source": "preset",
            "version": 1,
        })

    def _preset_baseband_record(self) -> Workflow:
        """基带录制工作流。"""
        return Workflow.from_dict({
            "name": "baseband_record",
            "description": "基带录制工作流：设置频率→设置采样率→录制→保存元数据",
            "category": "recording",
            "tags": ["基带录制", "IQ录制", "数据采集"],
            "steps": [
                {"step_id": 1, "tool_name": "sdr_set_frequency", "params": {"frequency_hz": "{{frequency}}"}, "description": "设置接收频率", "timeout": 10, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 2, "tool_name": "sdr_set_sample_rate", "params": {"sample_rate_hz": "{{sample_rate}}"}, "description": "设置采样率", "timeout": 10, "retry_on_failure": False, "max_retries": 0, "condition": ""},
                {"step_id": 3, "tool_name": "sdr_record_start", "params": {"duration": "{{duration}}", "format": "{{format}}"}, "description": "开始录制", "timeout": 30, "retry_on_failure": True, "max_retries": 2, "condition": ""},
                {"step_id": 4, "tool_name": "sdr_record_stop", "params": {}, "description": "停止录制并保存", "timeout": 10, "retry_on_failure": True, "max_retries": 2, "condition": ""},
            ],
            "parameters": {
                "frequency": {"default": 100000000, "type": "float", "description": "接收频率 Hz"},
                "sample_rate": {"default": 2400000, "type": "int", "description": "采样率 Hz"},
                "duration": {"default": 60, "type": "int", "description": "录制时长秒"},
                "format": {"default": "cf32", "type": "string", "description": "录制格式 cf32/wav/csv/iq"},
            },
            "trigger_phrases": ["录基带", "录制IQ", "基带录制", "录信号", "采样录制"],
            "author": "mbdsdr",
            "source": "preset",
            "version": 1,
        })

    # ── 用户工作流加载/保存 ─────────────────────────────

    def _load_user_workflows(self):
        """从 workflows_dir 加载用户自定义工作流。

        版本门控：同名工作流若磁盘缓存版本低于内置预设版本（软件升级后
        残留的旧 preset 导出），不覆盖新预设；用户自定义版本号更高才生效。
        """
        if not os.path.exists(self.workflows_dir):
            return
        for fname in os.listdir(self.workflows_dir):
            if fname.endswith(".json"):
                try:
                    with open(os.path.join(self.workflows_dir, fname), "r", encoding="utf-8") as f:
                        data = json.load(f)
                    wf = Workflow.from_dict(data)
                    existing = self.workflows.get(wf.name)
                    if existing is not None and existing.source == "preset" \
                            and wf.version <= existing.version:
                        # 旧版预设缓存，让位给新内置预设
                        continue
                    wf.source = "user"
                    self.workflows[wf.name] = wf
                except Exception as e:
                    print(f"警告: 加载工作流 {fname} 失败: {e}")

    def save_workflow(self, workflow: Workflow):
        """保存工作流到文件。"""
        self.workflows[workflow.name] = workflow
        workflow.updated_at = time.time()
        path = os.path.join(self.workflows_dir, f"{workflow.name}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(workflow.to_dict(), f, indent=2, ensure_ascii=False)

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
                error="工具执行器未设置，请先调用 set_tool_executor()",
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
                "success": True,
                "result": str(result)[:500] if result else "",
            })

            # 将结果存入上下文（供后续步骤使用）
            context[f"step_{step.step_id}_result"] = result
            # 解析 ToolResult 的 content 为 dict，提取关键字段供后续步骤模板使用
            result_dict = None
            if hasattr(result, 'content') and result.content:
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
                    if isinstance(v, (str, int, float, bool)):
                        context[k] = v

        # 更新统计
        workflow.usage_count += 1
        if failed_step is None:
            workflow.success_count += 1

        execution_time = (time.time() - start_time) * 1000

        return WorkflowResult(
            workflow_name=workflow_name,
            success=failed_step is None,
            total_steps=len(workflow.steps),
            completed_steps=completed,
            failed_step=failed_step,
            error=error,
            step_results=step_results,
            execution_time_ms=execution_time,
            outputs=context,
        )

    # ── 触发短语匹配 ────────────────────────────────────

    def match_trigger(self, text: str) -> Optional[Workflow]:
        """检查文本是否匹配某个工作流的触发短语。"""
        for workflow in self.workflows.values():
            if workflow.match_trigger(text):
                return workflow
        return None

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
                "trigger_phrases": w.trigger_phrases,
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
            lines.append(f"    触发: {', '.join(wf['trigger_phrases'][:3])}")
        return "\n".join(lines)
