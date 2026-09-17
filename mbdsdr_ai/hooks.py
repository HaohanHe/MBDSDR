"""
MBDSDR AI 内核 - Hook 事件钩子系统
====================================
Hooks：事件驱动的钩子系统，让 AI 可以监听和响应各种事件。

对照白皮书第四章 4.4 Hook 事件钩子系统。

核心概念：
- Event：事件，包含类型、时间戳、数据、来源
- Hook：钩子，监听特定事件类型，触发回调
- HookManager：钩子管理器，注册/注销/触发钩子

事件类型：
- sdr.connect / sdr.disconnect / sdr.frequency_change / sdr.gain_change
- sdr.spectrum_update / sdr.signal_detected / sdr.signal_lost
- sdr.record_start / sdr.record_stop / sdr.decode_complete
- agent.tool_call / agent.tool_result / agent.error
- agent.context_compact / agent.model_switch
- workflow.start / workflow.step / workflow.complete / workflow.error
- scheduler.trigger / scheduler.missed
- evolution.propose / evolution.verify / evolution.commit / evolution.rollback
- guardian.snapshot / guardian.rollback
- hardware.gps_update / hardware.imu_update / hardware.button_press
"""

import time
import json
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Any, Callable, Optional
from enum import Enum


class EventType(str, Enum):
    """事件类型枚举。"""
    # SDR 设备事件
    SDR_CONNECT = "sdr.connect"
    SDR_DISCONNECT = "sdr.disconnect"
    SDR_FREQUENCY_CHANGE = "sdr.frequency_change"
    SDR_GAIN_CHANGE = "sdr.gain_change"
    SDR_SAMPLE_RATE_CHANGE = "sdr.sample_rate_change"
    SDR_DEMOD_CHANGE = "sdr.demod_change"

    # SDR 信号事件
    SDR_SPECTRUM_UPDATE = "sdr.spectrum_update"
    SDR_SIGNAL_DETECTED = "sdr.signal_detected"
    SDR_SIGNAL_LOST = "sdr.signal_lost"
    SDR_INTERFERENCE_DETECTED = "sdr.interference_detected"

    # SDR 录制/解码事件
    SDR_RECORD_START = "sdr.record_start"
    SDR_RECORD_STOP = "sdr.record_stop"
    SDR_DECODE_COMPLETE = "sdr.decode_complete"
    SDR_DECODE_ERROR = "sdr.decode_error"

    # Agent 事件
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_TOOL_RESULT = "agent.tool_result"
    AGENT_ERROR = "agent.error"
    AGENT_CONTEXT_COMPACT = "agent.context_compact"
    AGENT_MODEL_SWITCH = "agent.model_switch"
    AGENT_MESSAGE = "agent.message"

    # 工作流事件
    WORKFLOW_START = "workflow.start"
    WORKFLOW_STEP = "workflow.step"
    WORKFLOW_COMPLETE = "workflow.complete"
    WORKFLOW_ERROR = "workflow.error"

    # 调度器事件
    SCHEDULER_TRIGGER = "scheduler.trigger"
    SCHEDULER_MISSED = "scheduler.missed"

    # 自进化事件
    EVOLUTION_PROPOSE = "evolution.propose"
    EVOLUTION_VERIFY = "evolution.verify"
    EVOLUTION_COMMIT = "evolution.commit"
    EVOLUTION_ROLLBACK = "evolution.rollback"

    # 守护者事件
    GUARDIAN_SNAPSHOT = "guardian.snapshot"
    GUARDIAN_ROLLBACK = "guardian.rollback"

    # 硬件事件
    HARDWARE_GPS_UPDATE = "hardware.gps_update"
    HARDWARE_IMU_UPDATE = "hardware.imu_update"
    HARDWARE_BUTTON_PRESS = "hardware.button_press"
    HARDWARE_OTA_START = "hardware.ota_start"
    HARDWARE_OTA_COMPLETE = "hardware.ota_complete"

    # 系统事件
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"
    SYSTEM_ERROR = "system.error"


@dataclass
class Event:
    """事件对象。"""
    event_type: str  # 事件类型字符串
    data: Dict[str, Any] = field(default_factory=dict)  # 事件数据
    timestamp: float = field(default_factory=time.time)  # 时间戳
    source: str = ""  # 事件来源
    event_id: str = ""  # 事件唯一 ID

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"evt_{int(self.timestamp * 1000)}_{id(self)}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type,
            "data": self.data,
            "timestamp": self.timestamp,
            "source": self.source,
            "event_id": self.event_id,
        }

    def __repr__(self):
        return f"Event({self.event_type}, src={self.source}, data_keys={list(self.data.keys())})"


@dataclass
class Hook:
    """钩子对象。"""
    hook_id: str  # 钩子唯一 ID
    event_type: str  # 监听的事件类型（"*" 表示所有事件）
    callback: Callable[[Event], Any]  # 回调函数
    description: str = ""  # 描述
    priority: int = 0  # 优先级（数字越大越先执行）
    enabled: bool = True  # 是否启用
    created_at: float = field(default_factory=time.time)
    trigger_count: int = 0  # 触发次数
    last_triggered: float = 0.0  # 最后触发时间

    def trigger(self, event: Event) -> Any:
        """触发钩子。"""
        if not self.enabled:
            return None
        self.trigger_count += 1
        self.last_triggered = time.time()
        try:
            return self.callback(event)
        except Exception as e:
            # 钩子错误不应该影响主流程
            print(f"[Hook Error] {self.hook_id} ({self.event_type}): {e}")
            return None


class HookManager:
    """
    钩子管理器。

    负责注册、注销、触发钩子。支持：
    - 按事件类型注册钩子
    - 通配符 "*" 监听所有事件
    - 优先级排序
    - 异步触发（可选）
    - 事件历史记录
    """

    def __init__(self, max_history: int = 1000):
        self._hooks: Dict[str, List[Hook]] = {}  # event_type -> [Hook]
        self._all_hooks: List[Hook] = []  # 通配符钩子
        self._hook_index: Dict[str, Hook] = {}  # hook_id -> Hook
        self._history: List[Event] = []  # 事件历史
        self._max_history = max_history
        self._lock = threading.Lock()
        self._async_enabled = False
        self._async_queue: List[Event] = []
        self._async_thread: Optional[threading.Thread] = None
        self._async_stop = threading.Event()

    def register(
        self,
        event_type: str,
        callback: Callable[[Event], Any],
        description: str = "",
        priority: int = 0,
    ) -> str:
        """
        注册一个钩子。

        返回 hook_id。
        """
        hook_id = f"hook_{int(time.time() * 1000)}_{len(self._hook_index)}"
        hook = Hook(
            hook_id=hook_id,
            event_type=event_type,
            callback=callback,
            description=description,
            priority=priority,
        )

        with self._lock:
            self._hook_index[hook_id] = hook
            if event_type == "*":
                self._all_hooks.append(hook)
                self._all_hooks.sort(key=lambda h: h.priority, reverse=True)
            else:
                if event_type not in self._hooks:
                    self._hooks[event_type] = []
                self._hooks[event_type].append(hook)
                self._hooks[event_type].sort(key=lambda h: h.priority, reverse=True)

        return hook_id

    def unregister(self, hook_id: str) -> bool:
        """注销一个钩子。"""
        with self._lock:
            hook = self._hook_index.pop(hook_id, None)
            if hook is None:
                return False

            if hook.event_type == "*":
                self._all_hooks = [h for h in self._all_hooks if h.hook_id != hook_id]
            else:
                if hook.event_type in self._hooks:
                    self._hooks[hook.event_type] = [
                        h for h in self._hooks[hook.event_type] if h.hook_id != hook_id
                    ]
            return True

    def enable(self, hook_id: str) -> bool:
        """启用钩子。"""
        hook = self._hook_index.get(hook_id)
        if hook:
            hook.enabled = True
            return True
        return False

    def disable(self, hook_id: str) -> bool:
        """禁用钩子。"""
        hook = self._hook_index.get(hook_id)
        if hook:
            hook.enabled = False
            return True
        return False

    def trigger(self, event: Event) -> List[Any]:
        """
        触发事件，执行所有匹配的钩子。

        返回所有钩子的返回值列表。
        """
        # 记录历史
        with self._lock:
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]

        results = []

        # 执行特定事件类型的钩子
        hooks = self._hooks.get(event.event_type, [])
        for hook in hooks:
            result = hook.trigger(event)
            if result is not None:
                results.append(result)

        # 执行通配符钩子
        for hook in self._all_hooks:
            result = hook.trigger(event)
            if result is not None:
                results.append(result)

        return results

    def trigger_async(self, event: Event):
        """异步触发事件（如果启用了异步模式）。"""
        if self._async_enabled:
            with self._lock:
                self._async_queue.append(event)
        else:
            self.trigger(event)

    def start_async(self):
        """启动异步事件处理线程。"""
        if self._async_enabled:
            return
        self._async_enabled = True
        self._async_stop.clear()
        self._async_thread = threading.Thread(target=self._async_worker, daemon=True)
        self._async_thread.start()

    def stop_async(self):
        """停止异步事件处理。"""
        self._async_enabled = False
        self._async_stop.set()
        if self._async_thread:
            self._async_thread.join(timeout=2.0)

    def _async_worker(self):
        """异步工作线程。"""
        while not self._async_stop.is_set():
            with self._lock:
                if self._async_queue:
                    event = self._async_queue.pop(0)
                else:
                    event = None

            if event:
                self.trigger(event)
            else:
                time.sleep(0.01)

    def get_history(self, event_type: str = None, limit: int = 100) -> List[Event]:
        """获取事件历史。"""
        with self._lock:
            if event_type:
                filtered = [e for e in self._history if e.event_type == event_type]
            else:
                filtered = list(self._history)
        return filtered[-limit:]

    def list_hooks(self, event_type: str = None) -> List[Dict[str, Any]]:
        """列出所有钩子。"""
        result = []
        with self._lock:
            if event_type:
                hooks = self._hooks.get(event_type, [])
            else:
                hooks = list(self._hook_index.values())

            for hook in hooks:
                result.append({
                    "hook_id": hook.hook_id,
                    "event_type": hook.event_type,
                    "description": hook.description,
                    "priority": hook.priority,
                    "enabled": hook.enabled,
                    "trigger_count": hook.trigger_count,
                    "last_triggered": hook.last_triggered,
                    "created_at": hook.created_at,
                })
        return result

    def clear_history(self):
        """清空事件历史。"""
        with self._lock:
            self._history.clear()

    def get_stats(self) -> Dict[str, Any]:
        """获取钩子系统统计信息。"""
        with self._lock:
            total_hooks = len(self._hook_index)
            enabled_hooks = sum(1 for h in self._hook_index.values() if h.enabled)
            event_types = list(self._hooks.keys())
            total_triggers = sum(h.trigger_count for h in self._hook_index.values())

        return {
            "total_hooks": total_hooks,
            "enabled_hooks": enabled_hooks,
            "disabled_hooks": total_hooks - enabled_hooks,
            "event_types_registered": len(event_types),
            "event_types": event_types,
            "total_triggers": total_triggers,
            "history_size": len(self._history),
            "async_enabled": self._async_enabled,
        }


# ═══════════════════════════════════════════════════════
# 内置钩子：日志记录钩子
# ═══════════════════════════════════════════════════════

def create_logging_hook(log_file: str = None) -> Callable[[Event], Any]:
    """创建一个日志记录钩子，记录所有事件到文件或控制台。"""
    def logging_hook(event: Event):
        log_entry = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {event.event_type} ({event.source}): {json.dumps(event.data, ensure_ascii=False, default=str)[:200]}"
        if log_file:
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(log_entry + '\n')
            except Exception:
                pass
        else:
            print(log_entry)
        return log_entry
    return logging_hook


def create_signal_alert_hook(
    on_signal_detected: Callable[[Dict[str, Any]], None] = None,
    on_signal_lost: Callable[[Dict[str, Any]], None] = None,
) -> Callable[[Event], Any]:
    """
    创建信号告警钩子。

    当检测到信号或信号丢失时触发回调。
    可用于自动调谐、录制启动、通知推送等。
    """
    def signal_alert_hook(event: Event):
        if event.event_type == EventType.SDR_SIGNAL_DETECTED.value:
            if on_signal_detected:
                on_signal_detected(event.data)
            return f"信号检测: {event.data.get('frequency_hz', 0)/1e6:.3f} MHz, {event.data.get('power_db', 0):.1f} dB"
        elif event.event_type == EventType.SDR_SIGNAL_LOST.value:
            if on_signal_lost:
                on_signal_lost(event.data)
            return f"信号丢失: {event.data.get('frequency_hz', 0)/1e6:.3f} MHz"
        return None
    return signal_alert_hook


def create_auto_record_hook(
    record_manager,
    min_duration_s: float = 10.0,
    max_duration_s: float = 300.0,
) -> Callable[[Event], Any]:
    """
    创建自动录制钩子。

    当检测到信号时自动开始录制，信号丢失时自动停止录制。
    """
    def auto_record_hook(event: Event):
        if event.event_type == EventType.SDR_SIGNAL_DETECTED.value:
            if not record_manager.status.recording:
                record_manager.start_recording(
                    path=f"auto_{int(time.time())}.cf32",
                    duration=max_duration_s,
                )
                return f"自动录制开始: 信号 {event.data.get('frequency_hz', 0)/1e6:.3f} MHz"
        elif event.event_type == EventType.SDR_SIGNAL_LOST.value:
            if record_manager.status.recording:
                path = record_manager.stop_recording()
                return f"自动录制停止: {path}"
        return None
    return auto_record_hook
