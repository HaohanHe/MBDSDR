"""
sdrtrunk 多信道跟踪状态机移植（纯 Python，无 Java/GNURadio 依赖）
=================================================================
本模块把 DSheirer/sdrtrunk (repos/sdrtrunk) 里的多信道状态机、通话组管理、
P25 控制信道消息处理思路翻译成 Python，所有状态/转换都在注释里标注
「来源: sdrtrunk 源文件:行号」。

移植自：
  * 信道状态枚举 + 合法转换         channel/state/State.java:29-161
  * 状态机 (fade/end timeout)       channel/state/StateMachine.java:36-275
  * 单/多信道活动状态集合             channel/state/State.java:165-166
  * 控制信道 -> 业务信道跟踪思路     controller/channel/Channel.java

核心概念：
  * 一个 Control Channel（控制信道）持续监听 TSBK 消息，
    收到 "Channel Grant" 时把指定频率/时隙切到 CALL 状态；
  * 收到 "Affiliation"（注册）或 "Group Update"（组更新）时更新通话组表；
  * 业务信道在 fade_timeout 后进入 FADE，再 end_timeout 后 TEARDOWN；
  * 状态机只允许合法状态转换（State.canChangeTo）。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════
#  信道状态枚举 —— 移植自 State.java:29-161
# ═══════════════════════════════════════════════════════════════════════
class ChannelState(Enum):
    """信道状态。来源 State.java:29-161。"""
    IDLE = "IDLE"           # 空闲，未解码任何消息
    ACTIVE = "ACTIVE"       # 活跃但非通话/数据（如 TDU 间隔）
    CALL = "CALL"           # 通话中（有音频）
    CONTROL = "CONTROL"     # 控制信道
    DATA = "DATA"           # 数据分组
    ENCRYPTED = "ENCRYPTED" # 加密音频
    FADE = "FADE"           # 渐隐（过渡期）
    TEARDOWN = "TEARDOWN"   # 拆线
    RESET = "RESET"         # 重置可复用


#: 合法状态转换表。来源 State.java:36-161 每个枚举的 canChangeTo()。
_ALLOWED: Dict[ChannelState, frozenset] = {
    ChannelState.ACTIVE: frozenset({ChannelState.CALL, ChannelState.CONTROL,
                                    ChannelState.DATA, ChannelState.ENCRYPTED,
                                    ChannelState.FADE, ChannelState.IDLE,
                                    ChannelState.TEARDOWN, ChannelState.RESET}),
    ChannelState.CALL: frozenset({ChannelState.ACTIVE, ChannelState.CONTROL,
                                  ChannelState.DATA, ChannelState.ENCRYPTED,
                                  ChannelState.FADE, ChannelState.IDLE,
                                  ChannelState.TEARDOWN, ChannelState.RESET}),
    ChannelState.CONTROL: frozenset({ChannelState.IDLE, ChannelState.FADE,
                                     ChannelState.RESET}),
    ChannelState.DATA: frozenset({ChannelState.ACTIVE, ChannelState.CALL,
                                  ChannelState.CONTROL, ChannelState.ENCRYPTED,
                                  ChannelState.FADE, ChannelState.RESET,
                                  ChannelState.TEARDOWN}),
    ChannelState.ENCRYPTED: frozenset({ChannelState.FADE, ChannelState.TEARDOWN,
                                       ChannelState.RESET}),
    ChannelState.FADE: frozenset({ChannelState.IDLE, ChannelState.ACTIVE,
                                  ChannelState.CALL, ChannelState.CONTROL,
                                  ChannelState.DATA, ChannelState.ENCRYPTED,
                                  ChannelState.TEARDOWN}),
    ChannelState.IDLE: frozenset({ChannelState.ACTIVE, ChannelState.CALL,
                                  ChannelState.CONTROL, ChannelState.DATA,
                                  ChannelState.ENCRYPTED, ChannelState.FADE,
                                  ChannelState.RESET}),
    ChannelState.RESET: frozenset({ChannelState.IDLE}),
    ChannelState.TEARDOWN: frozenset({ChannelState.RESET}),
}

#: 单信道活动状态集。来源 State.java:165
SINGLE_CHANNEL_ACTIVE = frozenset({ChannelState.ACTIVE, ChannelState.CALL,
                                   ChannelState.CONTROL, ChannelState.DATA,
                                   ChannelState.ENCRYPTED})


# ═══════════════════════════════════════════════════════════════════════
#  通话组表
# ═══════════════════════════════════════════════════════════════════════
@dataclass
class Talkgroup:
    """一个通话组（TG）。"""
    number: int
    alpha_tag: str = ""
    last_frequency: int = 0          # Hz
    last_time: float = 0.0            # epoch seconds
    affiliations: List[int] = field(default_factory=list)  # 注册的单位 ID


# ═══════════════════════════════════════════════════════════════════════
#  信道状态机
# ═══════════════════════════════════════════════════════════════════════
class TrunkedChannel:
    """单个信道（控制信道或业务信道）的状态机。

    移植自 StateMachine.java:36-275：
      * 进入活动状态时刷新 fade_timeout；
      * checkState() 超时则 FADE；FADE 再超时则 TEARDOWN；
      * 状态转换必须合法（State.canChangeTo）。
    """

    #: 默认 fade 超时（ms）。来源 StateMachine.java 默认无显式值，取 5s 经验值。
    DEFAULT_FAKE_TIMEOUT_MS = 5000
    #: 默认 end 超时（ms）。FADE 后再等 3s 拆线。
    DEFAULT_END_TIMEOUT_MS = 3000

    def __init__(self, number: int, frequency: int,
                 is_control: bool = False):
        self.number = number
        self.frequency = frequency
        self.state = ChannelState.IDLE
        self.fade_timeout_ms = self.DEFAULT_FAKE_TIMEOUT_MS
        self.end_timeout_ms = self.DEFAULT_END_TIMEOUT_MS
        self._state_entered = time.monotonic()
        self._fade_deadline = 0.0
        self._end_deadline = 0.0
        self.active_states = SINGLE_CHANNEL_ACTIVE
        # 通话组跟踪
        self.current_tg: Optional[int] = None
        self.current_source: Optional[int] = None
        self.call_history: List[dict] = []

    def can_change_to(self, new: ChannelState) -> bool:
        return new in _ALLOWED[self.state]

    def set_state(self, new: ChannelState) -> bool:
        """状态转换。成功返回 True，非法返回 False。来源 StateMachine.java:122-193。"""
        if new == self.state:
            if new in self.active_states:
                self._refresh_fade()
            return True
        if not self.can_change_to(new):
            return False
        self.state = new
        self._state_entered = time.monotonic()
        if new in self.active_states:
            self._refresh_fade()
        if new == ChannelState.FADE:
            self._end_deadline = time.monotonic() + self.end_timeout_ms / 1000.0
        return True

    def _refresh_fade(self) -> None:
        self._fade_deadline = time.monotonic() + self.fade_timeout_ms / 1000.0

    def check_state(self) -> Optional[ChannelState]:
        """超时检查。来源 StateMachine.java:99-109。"""
        now = time.monotonic()
        if self.state in self.active_states and now >= self._fade_deadline:
            self.set_state(ChannelState.FADE)
            return ChannelState.FADE
        if self.state == ChannelState.FADE and now >= self._end_deadline:
            self.set_state(ChannelState.TEARDOWN)
            return ChannelState.TEARDOWN
        return None


# ═══════════════════════════════════════════════════════════════════════
#  多信道跟踪器（控制信道 + 业务信道池）
# ═══════════════════════════════════════════════════════════════════════
class TrunkingSystem:
    """一个集群系统：一个控制信道 + 多个业务信道 + 通话组表。

    移植思路来自 sdrtrunk controller/channel/Channel.java + StateMachine.java。
    """

    def __init__(self, name: str = "P25-System", control_frequency: int = 0):
        self.name = name
        self.control_channel = TrunkedChannel(0, control_frequency, is_control=True)
        self.control_channel.set_state(ChannelState.CONTROL)
        self.traffic_channels: Dict[int, TrunkedChannel] = {}
        self.talkgroups: Dict[int, Talkgroup] = {}
        self.event_log: List[dict] = []

    # ── P25 控制信道消息处理 ──────────────────────────────────────────
    def on_channel_grant(self, tg: int, source: int, freq: int,
                         channel_id: int = 0) -> TrunkedChannel:
        """收到 TSBK "Channel Grant"：切到业务信道开始通话。

        来源 sdrtrunk P25 TSBK 处理：grant 消息携带业务频率和 TG，
        状态机从 CONTROL 旁的空闲业务信道转到 CALL。
        """
        ch = self.traffic_channels.get(channel_id)
        if ch is None:
            ch = TrunkedChannel(channel_id, freq)
            self.traffic_channels[channel_id] = ch
        ch.frequency = freq
        ch.current_tg = tg
        ch.current_source = source
        ch.set_state(ChannelState.CALL)
        tg_rec = self.talkgroups.setdefault(tg, Talkgroup(number=tg))
        tg_rec.last_frequency = freq
        tg_rec.last_time = time.time()
        self.event_log.append({
            "event": "grant", "tg": tg, "source": source,
            "freq": freq, "channel": channel_id, "state": ch.state.value,
        })
        return ch

    def on_affiliation(self, tg: int, unit: int) -> None:
        """收到 "Affiliation"：单位注册到通话组。"""
        tg_rec = self.talkgroups.setdefault(tg, Talkgroup(number=tg))
        if unit not in tg_rec.affiliations:
            tg_rec.affiliations.append(unit)
        self.event_log.append({"event": "affiliation", "tg": tg, "unit": unit})

    def on_group_update(self, tg: int, source: int, freq: int) -> None:
        """收到 "Group Update"：通话组更新（如紧急/加密标志）。"""
        tg_rec = self.talkgroups.setdefault(tg, Talkgroup(number=tg))
        tg_rec.last_frequency = freq
        tg_rec.last_time = time.time()
        self.event_log.append({"event": "group_update", "tg": tg,
                               "source": source, "freq": freq})

    def on_call_end(self, channel_id: int) -> None:
        """通话结束（TSBK Terminator）：业务信道 FADE -> TEARDOWN。"""
        ch = self.traffic_channels.get(channel_id)
        if ch and ch.state == ChannelState.CALL:
            ch.set_state(ChannelState.FADE)
            self.event_log.append({"event": "call_end", "channel": channel_id,
                                   "state": ch.state.value})

    def check_timeouts(self) -> List[dict]:
        """轮询所有业务信道超时。"""
        events = []
        for ch in self.traffic_channels.values():
            new = ch.check_state()
            if new:
                events.append({"channel": ch.number, "new_state": new.value})
        return events

    def snapshot(self) -> dict:
        return {
            "system": self.name,
            "control_state": self.control_channel.state.value,
            "traffic_channels": {
                str(k): {"freq": v.frequency, "state": v.state.value,
                         "tg": v.current_tg, "source": v.current_source}
                for k, v in self.traffic_channels.items()
            },
            "talkgroups": {str(k): {"affiliations": v.affiliations,
                                    "last_freq": v.last_frequency}
                           for k, v in self.talkgroups.items()},
            "event_count": len(self.event_log),
        }


# ═══════════════════════════════════════════════════════════════════════
#  ToolRegistry 注册
# ═══════════════════════════════════════════════════════════════════════
def register_sdrtrunk_tools(registry) -> None:
    """把 sdrtrunk 状态机 / 通话组管理 注册进 ToolRegistry。"""
    from mbdsdr_ai.tool_registry import ToolResult

    #: 模块级单例（供工具调用共享状态）
    _system = TrunkingSystem(name="demo-p25", control_frequency=851000000)

    def _grant(args):
        """模拟收到一个 Channel Grant 事件。"""
        try:
            tg = int(args.get("tg", 1234))
            src = int(args.get("source", 1001))
            freq = int(args.get("freq", 852000000))
            ch_id = int(args.get("channel", 1))
            ch = _system.on_channel_grant(tg, src, freq, ch_id)
            out = {"ok": True, "channel": ch_id, "state": ch.state.value,
                   "tg": tg, "source": src, "freq": freq,
                   "source_ref": "sdrtrunk State.java:54-67, StateMachine.java:122-193"}
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"grant 处理失败: {e}")

    def _affiliation(args):
        """模拟收到一个 Affiliation 事件。"""
        try:
            tg = int(args.get("tg", 1234))
            unit = int(args.get("unit", 1001))
            _system.on_affiliation(tg, unit)
            out = {"ok": True, "tg": tg, "unit": unit,
                   "affiliations": _system.talkgroups[tg].affiliations,
                   "source_ref": "sdrtrunk P25 TSBK affiliation handling"}
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"affiliation 处理失败: {e}")

    def _snapshot(args):
        """返回当前系统状态快照。"""
        try:
            snap = _system.snapshot()
            snap["source_ref"] = "sdrtrunk StateMachine.java:99-109, Channel.java"
            return ToolResult(True, json.dumps(snap, ensure_ascii=False), data=snap)
        except Exception as e:
            return ToolResult(False, f"snapshot 失败: {e}")

    def _state_transition(args):
        """测试状态机合法/非法转换。"""
        try:
            from_state = ChannelState[args.get("from", "IDLE")]
            to_state = ChannelState[args.get("to", "CALL")]
            legal = to_state in _ALLOWED[from_state]
            out = {"from": from_state.value, "to": to_state.value,
                   "legal": legal,
                   "allowed_targets": sorted(s.value for s in _ALLOWED[from_state]),
                   "source_ref": "sdrtrunk State.java:36-161"}
            return ToolResult(True, json.dumps(out, ensure_ascii=False), data=out)
        except Exception as e:
            return ToolResult(False, f"状态转换检查失败: {e}")

    registry.register(
        name="sdrtrunk_channel_grant",
        description=("sdrtrunk 风格 Channel Grant 事件：给定 TG/source/频率，"
                     "在业务信道上把状态机从 IDLE 切到 CALL。"
                     "来源 sdrtrunk StateMachine.java:122-193。"),
        parameters={
            "type": "object",
            "properties": {
                "tg": {"type": "integer", "default": 1234},
                "source": {"type": "integer", "default": 1001},
                "freq": {"type": "integer", "default": 852000000},
                "channel": {"type": "integer", "default": 1},
            },
            "required": [],
        },
        handler=_grant,
        category="digital_voice",
    )

    registry.register(
        name="sdrtrunk_affiliation",
        description=("sdrtrunk 风格 Affiliation 事件：单位注册到通话组。"
                     "更新通话组表的 affiliations 列表。"),
        parameters={
            "type": "object",
            "properties": {
                "tg": {"type": "integer", "default": 1234},
                "unit": {"type": "integer", "default": 1001},
            },
            "required": [],
        },
        handler=_affiliation,
        category="digital_voice",
    )

    registry.register(
        name="sdrtrunk_snapshot",
        description=("返回当前集群系统状态快照：控制信道状态、所有业务信道"
                     "（频率/状态/TG）、通话组表。"),
        parameters={"type": "object", "properties": {}},
        handler=_snapshot,
        category="digital_voice",
    )

    registry.register(
        name="sdrtrunk_state_transition",
        description=("检查 sdrtrunk 状态机中 from->to 的转换是否合法"
                     "（来源 State.java:36-161 canChangeTo）。"),
        parameters={
            "type": "object",
            "properties": {
                "from": {"type": "string", "default": "IDLE"},
                "to": {"type": "string", "default": "CALL"},
            },
            "required": [],
        },
        handler=_state_transition,
        category="digital_voice",
    )


if __name__ == "__main__":
    # 自测：状态机 grant/affiliation 流程
    sys = TrunkingSystem("test", 851000000)
    ch = sys.on_channel_grant(1234, 1001, 852000000, 1)
    assert ch.state == ChannelState.CALL
    sys.on_affiliation(1234, 1001)
    sys.on_affiliation(1234, 1002)
    sys.on_group_update(1234, 1001, 852000000)
    print("状态:", ch.state.value, "TG 1234 注册单位:", sys.talkgroups[1234].affiliations)
    # 非法转换检查：CONTROL -> CALL 不合法
    assert not ChannelState.CALL in _ALLOWED[ChannelState.CONTROL]
    # IDLE -> CALL 合法
    assert ChannelState.CALL in _ALLOWED[ChannelState.IDLE]
    print("sdrtrunk_adapter 自测 OK")
