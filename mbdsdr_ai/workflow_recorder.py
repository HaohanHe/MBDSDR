"""
MBDSDR AI 内核 - 工作流录制与复用（Record & Replay）
=====================================================
WorkflowRecorder：录制 Agent 的工具调用序列，保存为可复用的工作流。

对照白皮书第四章 4.6.4 工作流录制与复用。

核心概念：
- Recording：录制会话，记录一系列工具调用和结果
- WorkflowTemplate：工作流模板，包含参数化的工具调用序列
- ReplayEngine：回放引擎，用新参数执行录制的工作流

用途：
- 用户手动操作一次，AI 录制下来，以后可以自动复用
- 录制的工作流可以分享、版本化、参数化
- 类似"宏"的概念，但更智能（可以参数替换、条件分支）
"""

import json
import time
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional, Callable, Tuple
from enum import Enum


class RecordingStatus(str, Enum):
    """录制状态。"""
    IDLE = "idle"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class ToolCallRecord:
    """单次工具调用记录。"""
    step_index: int  # 步骤序号
    tool_name: str  # 工具名
    parameters: Dict[str, Any]  # 参数
    result: str  # 结果（文本）
    success: bool  # 是否成功
    timestamp: float = field(default_factory=time.time)
    duration_ms: float = 0.0  # 执行耗时
    user_annotation: str = ""  # 用户批注

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_index": self.step_index,
            "tool_name": self.tool_name,
            "parameters": self.parameters,
            "result": self.result,
            "success": self.success,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "user_annotation": self.user_annotation,
        }


@dataclass
class Recording:
    """录制会话。"""
    recording_id: str
    name: str
    description: str = ""
    status: RecordingStatus = RecordingStatus.IDLE
    steps: List[ToolCallRecord] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    stopped_at: float = 0.0
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recording_id": self.recording_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "tags": self.tags,
            "metadata": self.metadata,
        }


@dataclass
class WorkflowTemplate:
    """
    工作流模板（参数化的录制结果）。

    参数用 {{param_name}} 表示，回放时替换为实际值。
    """
    template_id: str
    name: str
    description: str = ""
    steps: List[Dict[str, Any]] = field(default_factory=list)  # 参数化的步骤
    parameters: Dict[str, str] = field(default_factory=dict)  # 参数名 -> 描述
    created_at: float = field(default_factory=time.time)
    source_recording_id: str = ""
    version: str = "1.0"
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "template_id": self.template_id,
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "parameters": self.parameters,
            "created_at": self.created_at,
            "source_recording_id": self.source_recording_id,
            "version": self.version,
            "tags": self.tags,
        }


class WorkflowRecorder:
    """
    工作流录制器。

    录制 Agent 的工具调用序列，保存为可复用的工作流。
    支持参数化、版本化、分享、回放。
    """

    def __init__(self, storage_dir: str = None):
        if storage_dir is None:
            storage_dir = os.path.expanduser("~/.mbdsdr/workflows/recordings")
        self.storage_dir = storage_dir
        os.makedirs(self.storage_dir, exist_ok=True)

        self.current_recording: Optional[Recording] = None
        self.recordings: Dict[str, Recording] = {}
        self.templates: Dict[str, WorkflowTemplate] = {}
        self._step_counter = 0

        # 加载已保存的模板
        self._load_templates()

    def start_recording(self, name: str, description: str = "", tags: List[str] = None) -> str:
        """开始录制。"""
        if self.current_recording and self.current_recording.status == RecordingStatus.RECORDING:
            raise RuntimeError("已经有一个录制正在进行")

        recording_id = f"rec_{int(time.time() * 1000)}"
        self.current_recording = Recording(
            recording_id=recording_id,
            name=name,
            description=description,
            status=RecordingStatus.RECORDING,
            started_at=time.time(),
            tags=tags or [],
        )
        self.recordings[recording_id] = self.current_recording
        self._step_counter = 0
        return recording_id

    def stop_recording(self) -> Optional[Recording]:
        """停止录制。"""
        if not self.current_recording:
            return None

        self.current_recording.status = RecordingStatus.STOPPED
        self.current_recording.stopped_at = time.time()

        # 保存到文件
        self._save_recording(self.current_recording)

        recording = self.current_recording
        self.current_recording = None
        return recording

    def pause_recording(self):
        """暂停录制。"""
        if self.current_recording:
            self.current_recording.status = RecordingStatus.PAUSED

    def resume_recording(self):
        """恢复录制。"""
        if self.current_recording and self.current_recording.status == RecordingStatus.PAUSED:
            self.current_recording.status = RecordingStatus.RECORDING

    def record_tool_call(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        result: str,
        success: bool,
        duration_ms: float = 0.0,
    ):
        """
        记录一次工具调用。

        这是录制的核心：Agent 每次调用工具后，调用这个方法记录。
        """
        if not self.current_recording or self.current_recording.status != RecordingStatus.RECORDING:
            return

        self._step_counter += 1
        record = ToolCallRecord(
            step_index=self._step_counter,
            tool_name=tool_name,
            parameters=parameters,
            result=result,
            success=success,
            duration_ms=duration_ms,
        )
        self.current_recording.steps.append(record)

    def annotate_step(self, step_index: int, annotation: str):
        """给某个步骤添加用户批注。"""
        if not self.current_recording:
            return
        for step in self.current_recording.steps:
            if step.step_index == step_index:
                step.user_annotation = annotation
                break

    def create_template(
        self,
        recording_id: str,
        name: str,
        description: str = "",
        parameter_map: Dict[str, str] = None,
    ) -> WorkflowTemplate:
        """
        从录制创建工作流模板。

        parameter_map: {参数名: 描述}，参数会在步骤中用 {{参数名}} 替换
        """
        recording = self.recordings.get(recording_id)
        if not recording:
            raise ValueError(f"录制不存在: {recording_id}")

        template_id = f"tpl_{int(time.time() * 1000)}"
        template = WorkflowTemplate(
            template_id=template_id,
            name=name,
            description=description,
            source_recording_id=recording_id,
            parameters=parameter_map or {},
            tags=recording.tags,
        )

        # 参数化步骤：将参数值替换为 {{param_name}}
        for step in recording.steps:
            step_dict = step.to_dict()
            if parameter_map:
                step_dict["parameters"] = self._parameterize(
                    step_dict["parameters"], parameter_map
                )
            template.steps.append(step_dict)

        self.templates[template_id] = template
        self._save_template(template)
        return template

    def _parameterize(
        self, parameters: Dict[str, Any], parameter_map: Dict[str, str]
    ) -> Dict[str, Any]:
        """将参数中的具体值替换为 {{param_name}}。"""
        result = {}
        for key, value in parameters.items():
            if isinstance(value, str):
                # 检查是否匹配某个参数值
                for param_name, param_desc in parameter_map.items():
                    if str(value) == param_desc:  # 用描述作为匹配值（简化）
                        result[key] = f"{{{{{param_name}}}}}"
                        break
                else:
                    result[key] = value
            else:
                result[key] = value
        return result

    def replay_template(
        self,
        template_id: str,
        parameters: Dict[str, Any],
        tool_executor: Callable[[str, Dict[str, Any]], Tuple[str, bool]],
    ) -> List[Dict[str, Any]]:
        """
        回放工作流模板。

        tool_executor: (tool_name, parameters) -> (result_text, success)
        返回每步的执行结果列表。
        """
        template = self.templates.get(template_id)
        if not template:
            raise ValueError(f"模板不存在: {template_id}")

        results = []
        for step in template.steps:
            # 替换参数
            step_params = self._substitute_parameters(step["parameters"], parameters)

            # 执行工具
            start_time = time.time()
            result_text, success = tool_executor(step["tool_name"], step_params)
            duration_ms = (time.time() - start_time) * 1000

            results.append({
                "step_index": step["step_index"],
                "tool_name": step["tool_name"],
                "parameters": step_params,
                "result": result_text,
                "success": success,
                "duration_ms": duration_ms,
            })

            # 如果失败，可以选择停止或继续（这里继续）
            if not success:
                results[-1]["error"] = f"步骤 {step['step_index']} 执行失败"

        return results

    def _substitute_parameters(
        self, parameters: Dict[str, Any], values: Dict[str, Any]
    ) -> Dict[str, Any]:
        """将 {{param_name}} 替换为实际值。"""
        result = {}
        for key, value in parameters.items():
            if isinstance(value, str):
                for param_name, param_value in values.items():
                    value = value.replace(f"{{{{{param_name}}}}}", str(param_value))
                result[key] = value
            else:
                result[key] = value
        return result

    def list_recordings(self) -> List[Dict[str, Any]]:
        """列出所有录制。"""
        return [
            {
                "recording_id": r.recording_id,
                "name": r.name,
                "description": r.description,
                "status": r.status.value,
                "steps_count": len(r.steps),
                "created_at": r.created_at,
                "tags": r.tags,
            }
            for r in self.recordings.values()
        ]

    def list_templates(self) -> List[Dict[str, Any]]:
        """列出所有模板。"""
        return [
            {
                "template_id": t.template_id,
                "name": t.name,
                "description": t.description,
                "steps_count": len(t.steps),
                "parameters": list(t.parameters.keys()),
                "version": t.version,
                "tags": t.tags,
            }
            for t in self.templates.values()
        ]

    def get_recording(self, recording_id: str) -> Optional[Recording]:
        return self.recordings.get(recording_id)

    def get_template(self, template_id: str) -> Optional[WorkflowTemplate]:
        return self.templates.get(template_id)

    def delete_recording(self, recording_id: str) -> bool:
        if recording_id in self.recordings:
            del self.recordings[recording_id]
            path = os.path.join(self.storage_dir, f"{recording_id}.json")
            if os.path.exists(path):
                os.remove(path)
            return True
        return False

    def delete_template(self, template_id: str) -> bool:
        if template_id in self.templates:
            del self.templates[template_id]
            path = os.path.join(self.storage_dir, "templates", f"{template_id}.json")
            if os.path.exists(path):
                os.remove(path)
            return True
        return False

    def get_status(self) -> Dict[str, Any]:
        """获取录制器状态。"""
        return {
            "current_recording": (
                self.current_recording.to_dict() if self.current_recording else None
            ),
            "total_recordings": len(self.recordings),
            "total_templates": len(self.templates),
            "storage_dir": self.storage_dir,
        }

    def _save_recording(self, recording: Recording):
        """保存录制到文件。"""
        path = os.path.join(self.storage_dir, f"{recording.recording_id}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(recording.to_dict(), f, ensure_ascii=False, indent=2)

    def _save_template(self, template: WorkflowTemplate):
        """保存模板到文件。"""
        template_dir = os.path.join(self.storage_dir, "templates")
        os.makedirs(template_dir, exist_ok=True)
        path = os.path.join(template_dir, f"{template.template_id}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(template.to_dict(), f, ensure_ascii=False, indent=2)

    def _load_templates(self):
        """加载已保存的模板。"""
        template_dir = os.path.join(self.storage_dir, "templates")
        if not os.path.exists(template_dir):
            return
        for filename in os.listdir(template_dir):
            if filename.endswith('.json'):
                path = os.path.join(template_dir, filename)
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    template = WorkflowTemplate(
                        template_id=data["template_id"],
                        name=data["name"],
                        description=data.get("description", ""),
                        steps=data.get("steps", []),
                        parameters=data.get("parameters", {}),
                        created_at=data.get("created_at", time.time()),
                        source_recording_id=data.get("source_recording_id", ""),
                        version=data.get("version", "1.0"),
                        tags=data.get("tags", []),
                    )
                    self.templates[template.template_id] = template
                except Exception:
                    pass
