# SPDX-License-Identifier: MIT
"""
MBDSDR AI - 天空模式「硬件能力协商」会话状态
==============================================

设计边界（审计结论）：天空视图的「追踪 / 调到该星 / 云台跟随」不允许在
UI 里写死固定流程或预置问答脚本，也不允许按钮直连主窗口驱动硬件。正确
形态是：

    用户点动作
      → 把目标卫星上下文（名称 / NORAD / 当前 az/el / 标称下行 / 多普勒 /
        过境窗口）交给 agent 会话
      → 模型自己用现有工具探测硬件（设备枚举 / 云台状态 / GNSS 状态），
        判断该问什么、要不要问
      → 用户用自然语言回答（一句话可以描述多件硬件）
      → 模型决定本轮能做什么并调用相应工具

本模块只提供三件「数据/文案」：

1. ``SatelliteActionContext``：动作 + 目标卫星上下文（真实数据，缺项留空）；
2. ``CapabilityLedger``：本次会话已确认的硬件现实（有 / 无 / 未知）与
   本轮拍板结果。它挂在 agent 会话上，后续动作直接复用，用户改口可覆盖；
3. ``build_action_prompt`` / ``build_capability_system_section``：把上面
   两份数据组织成模型可见的上下文。它们只陈述事实与约束，不生成固定
   问题清单——问什么、怎么问由模型判断。

诚实原则：没有证据的槽位一律 ``unknown``，不猜测型号、不假设硬件存在。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# 硬件能力槽位（天空模式动作实际会用到的类别）
CAPABILITY_SLOTS: Dict[str, str] = {
    "sdr": "SDR 接收设备",
    "gimbal": "云台/旋转器",
    "gnss": "GNSS 定位模块",
    "imu": "IMU 姿态传感器",
    "antenna": "定向天线",
}

CAPABILITY_STATES = ("available", "missing", "unknown")
STATE_LABELS = {"available": "有", "missing": "无", "unknown": "未知"}

ACTION_LABELS: Dict[str, str] = {
    "track": "追踪（闭环跟踪调谐）",
    "tune": "调谐接收（调到该星）",
    "gimbal_follow": "云台跟随",
}

_UNKNOWN = "unknown"


def action_label(action: str) -> str:
    """动作中文标签；未知动作原样返回（调用方自行校验）。"""
    return ACTION_LABELS.get(str(action), str(action))


def jd_to_utc_iso(jd: Any) -> str:
    """儒略日 → ISO UTC 字符串；非法/缺失如实返回空串（不编造时间）。"""
    try:
        unix = (float(jd) - 2440587.5) * 86400.0
        return datetime.fromtimestamp(unix, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def validate_capability_item(slot: str, state: str) -> Optional[str]:
    """校验一条硬件状态记录。合法返回 None，非法返回原因（诚实拒绝）。"""
    if slot not in CAPABILITY_SLOTS:
        return (f"未知硬件槽位 {slot!r}：可选 "
                f"{'/'.join(CAPABILITY_SLOTS.keys())}")
    if state not in CAPABILITY_STATES:
        return (f"非法状态 {state!r}：只接受 "
                f"{'/'.join(CAPABILITY_STATES)}")
    return None


# ---------------------------------------------------------------------------
# 目标卫星上下文
# ---------------------------------------------------------------------------
@dataclass
class SatelliteActionContext:
    """一次天空动作请求携带的真实目标上下文（缺项为 None，不编造）。"""

    action: str
    satellite_name: str
    norad_id: Optional[int] = None
    azimuth_deg: Optional[float] = None
    elevation_deg: Optional[float] = None
    nominal_freq_hz: Optional[float] = None
    tuned_freq_hz: Optional[float] = None
    doppler_hz: Optional[float] = None
    mode: str = ""
    pass_rise_utc: str = ""
    pass_set_utc: str = ""
    tle_epoch: str = ""
    observer: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "SatelliteActionContext":
        def _f(key: str) -> Optional[float]:
            value = data.get(key)
            if value is None or value == "":
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _i(key: str) -> Optional[int]:
            value = data.get(key)
            if value is None or value == "":
                return None
            try:
                return int(value)
            except (TypeError, ValueError):
                return None

        return cls(
            action=str(data.get("action", "")),
            satellite_name=str(data.get("satellite_name")
                               or data.get("satellite") or ""),
            norad_id=_i("norad_id"),
            azimuth_deg=_f("azimuth_deg"),
            elevation_deg=_f("elevation_deg"),
            nominal_freq_hz=_f("nominal_freq_hz"),
            tuned_freq_hz=_f("tuned_freq_hz"),
            doppler_hz=_f("doppler_hz"),
            mode=str(data.get("mode") or ""),
            pass_rise_utc=str(data.get("pass_rise_utc") or ""),
            pass_set_utc=str(data.get("pass_set_utc") or ""),
            tle_epoch=str(data.get("tle_epoch") or ""),
            observer=str(data.get("observer") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"action": self.action,
                                   "satellite_name": self.satellite_name}
        for key in ("norad_id", "azimuth_deg", "elevation_deg",
                    "nominal_freq_hz", "tuned_freq_hz", "doppler_hz"):
            value = getattr(self, key)
            if value is not None:
                payload[key] = value
        for key in ("mode", "pass_rise_utc", "pass_set_utc",
                    "tle_epoch", "observer"):
            value = getattr(self, key)
            if value:
                payload[key] = value
        return payload

    def key(self) -> str:
        """目标稳定键（用于本轮拍板结果归属）。"""
        return f"{self.satellite_name}@{self.action}"


# ---------------------------------------------------------------------------
# 会话级硬件能力账本
# ---------------------------------------------------------------------------
@dataclass
class CapabilityLedger:
    """会话级硬件现实账本（有 / 无 / 未知 + 证据 + 本轮拍板）。

    - 只记录有证据的结论；未记录的槽位按 ``unknown`` 处理；
    - 同一槽位可被后续用户回答覆盖（随用户改口更新）；
    - 通过 ``to_dict`` / ``from_dict`` 跟随会话持久化。
    """

    slots: Dict[str, str] = field(default_factory=dict)
    evidence: Dict[str, str] = field(default_factory=dict)
    decisions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    updated_at: float = 0.0

    # ---- 写入 ----
    def update(self, slot: str, state: str, evidence: str = "") -> bool:
        """记录一条硬件结论；槽位/状态非法返回 False（不写入）。"""
        if validate_capability_item(slot, state) is not None:
            return False
        self.slots[str(slot)] = str(state)
        if evidence:
            self.evidence[str(slot)] = str(evidence)
        elif str(state) == _UNKNOWN:
            self.evidence.pop(str(slot), None)
        self.updated_at = time.time()
        return True

    def update_many(self, items: Iterable[Mapping[str, Any]],
                    source: str = "user") -> Tuple[int, List[str]]:
        """批量写入（用户一句话描述多件硬件时模型一次记全）。

        返回 (成功条数, 被拒原因列表)。空 evidence 时用 source 兜底标注。
        """
        ok_count = 0
        rejected: List[str] = []
        for item in items or []:
            slot = str(item.get("slot", ""))
            state = str(item.get("state", ""))
            reason = validate_capability_item(slot, state)
            if reason is not None:
                rejected.append(reason)
                continue
            evidence = str(item.get("evidence") or item.get("source") or source)
            if self.update(slot, state, evidence):
                ok_count += 1
        return ok_count, rejected

    def record_decision(self, action: str, satellite: str,
                        decision: str) -> None:
        """记录本轮对某个动作的拍板（可复用，也可被用户改口覆盖）。"""
        action = str(action)
        self.decisions[f"{satellite}@{action}"] = {
            "action": action,
            "satellite": str(satellite),
            "decision": str(decision),
            "ts": time.time(),
        }
        self.updated_at = time.time()

    # ---- 读取 ----
    def state(self, slot: str) -> str:
        return self.slots.get(str(slot), _UNKNOWN)

    def known(self, slot: str) -> bool:
        return self.state(slot) != _UNKNOWN

    def unknown_slots(self) -> List[str]:
        return [slot for slot in CAPABILITY_SLOTS if not self.known(slot)]

    def decision_for(self, action: str, satellite: str) -> str:
        return str(self.decisions.get(f"{satellite}@{action}", {})
                   .get("decision", ""))

    # ---- 序列化 ----
    def to_dict(self) -> Dict[str, Any]:
        return {
            "slots": dict(self.slots),
            "evidence": dict(self.evidence),
            "decisions": {k: dict(v) for k, v in self.decisions.items()},
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "CapabilityLedger":
        ledger = cls()
        if not isinstance(data, Mapping):
            return ledger
        raw_slots = data.get("slots") or {}
        if isinstance(raw_slots, Mapping):
            for slot, state in raw_slots.items():
                if validate_capability_item(str(slot), str(state)) is None:
                    ledger.slots[str(slot)] = str(state)
        raw_ev = data.get("evidence") or {}
        if isinstance(raw_ev, Mapping):
            for slot, text in raw_ev.items():
                if str(slot) in CAPABILITY_SLOTS:
                    ledger.evidence[str(slot)] = str(text)
        raw_dec = data.get("decisions") or {}
        if isinstance(raw_dec, Mapping):
            for key, value in raw_dec.items():
                if isinstance(value, Mapping):
                    ledger.decisions[str(key)] = dict(value)
        try:
            ledger.updated_at = float(data.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            ledger.updated_at = 0.0
        return ledger

    # ---- 文案 ----
    def known_lines(self) -> List[str]:
        lines = []
        for slot, label in CAPABILITY_SLOTS.items():
            state = self.state(slot)
            if state == _UNKNOWN:
                continue
            evidence = self.evidence.get(slot, "")
            tail = f"；依据：{evidence}" if evidence else ""
            lines.append(f"- {label}: {STATE_LABELS.get(state, state)}{tail}")
        return lines

    def unknown_line(self) -> str:
        unknown = self.unknown_slots()
        if not unknown:
            return "- 无（本会话涉及的硬件状态均已确认）"
        return "- " + "、".join(CAPABILITY_SLOTS[s] for s in unknown)

    def decision_lines(self) -> List[str]:
        lines = []
        for value in self.decisions.values():
            action = action_label(str(value.get("action", "")))
            satellite = str(value.get("satellite", ""))
            decision = str(value.get("decision", ""))
            lines.append(f"- {action}｜{satellite}: {decision}")
        return lines


# ---------------------------------------------------------------------------
# 上下文文案（只陈述事实与约束，不生成固定问题清单）
# ---------------------------------------------------------------------------
def _fmt(value: Optional[float], unit: str, digits: int = 1) -> str:
    if value is None:
        return "未获取"
    return f"{value:.{digits}f}{unit}"


def _fmt_hz(value: Optional[float]) -> str:
    if value is None:
        return "未获取"
    return f"{value / 1e6:.4f} MHz"


def build_action_prompt(context: SatelliteActionContext | Mapping[str, Any],
                        ledger: Optional[CapabilityLedger] = None) -> str:
    """构造一次天空动作的模型可见上下文（非问卷、无固定问题清单）。"""
    if not isinstance(context, SatelliteActionContext):
        context = SatelliteActionContext.from_mapping(context)
    ledger = ledger or CapabilityLedger()

    target_lines = [
        f"- 动作: {action_label(context.action)}",
        f"- 卫星: {context.satellite_name or '未选择'}",
    ]
    if context.norad_id is not None:
        target_lines.append(f"- NORAD: {context.norad_id}")
    target_lines.append(
        f"- 当前方位/仰角: {_fmt(context.azimuth_deg, '°')} / "
        f"{_fmt(context.elevation_deg, '°')}")
    target_lines.append(
        f"- 标称下行: {_fmt_hz(context.nominal_freq_hz)}；"
        f"多普勒: {_fmt(context.doppler_hz, ' Hz', 0)}")
    if context.mode:
        target_lines.append(f"- 解调模式: {context.mode}")
    if context.pass_rise_utc or context.pass_set_utc:
        target_lines.append(
            f"- 过境窗口: {context.pass_rise_utc or '未获取'} → "
            f"{context.pass_set_utc or '未获取'}")
    if context.tle_epoch:
        target_lines.append(f"- TLE 历元: {context.tle_epoch}")
    if context.observer:
        target_lines.append(f"- 观测站: {context.observer}")

    known = ledger.known_lines()
    decisions = ledger.decision_lines()

    sections = [
        "【天空动作请求｜不是问卷、不是固定流程】",
        "用户在天空视图刚刚发起了下面的动作。目标上下文来自实时轨道/链路数据，"
        "缺项如实留空，不得用占位值补齐：",
        "\n".join(target_lines),
        "【本会话已确认的硬件现实（直接复用，勿重复追问）】",
        "\n".join(known) if known else "- 暂无已确认项",
        "【仍未确认的硬件】",
        ledger.unknown_line(),
    ]
    if decisions:
        sections.extend([
            "【本会话已拍板的动作结论】",
            "\n".join(decisions),
        ])
    sections.extend([
        "【执行要求】",
        "1. 先用现有工具自行探测能探测的硬件（设备枚举、状态查询、云台读数、"
        "GNSS/IMU 状态等），不要假设型号或凭空认定已连接。",
        "2. 仍需确认的项用自然语言向用户询问；用户一句话可以描述多件硬件，"
        "一次抽取即可。不要逐项念清单、不要生成表单、不要按固定顺序追问。",
        "3. 有证据支持的部分直接调用相应工具执行；缺硬件或证据不足的部分"
        "如实说明缺什么，并给出可行的替代方案（例如手动转动天线、只做软件调谐）。",
        "4. 得到结论后调用 sky_capability_report 把本轮硬件结论与拍板结果写回会话，"
        "后续动作直接复用；用户改口时以新回答覆盖旧结论。",
        "5. 不要用模拟数据或虚构设备冒充真实硬件；不确定就保持未知。",
    ])
    return "\n\n".join(sections)


def build_capability_system_section(ledger: Optional[CapabilityLedger] = None) -> str:
    """系统提示词里的硬件现实段落（静态规则 + 本会话账本快照）。"""
    ledger = ledger or CapabilityLedger()
    known = ledger.known_lines()
    return (
        "【天空模式的硬件现实（动手前必须确认）】\n"
        "- 追踪、调谐接收、云台跟随等动手动作之前，先确认相关硬件现实；"
        "每一类只有三种状态：有 / 无 / 未知。\n"
        "- 有 → 直接使用；无 → 明确说缺什么，并给出替代方案；"
        "未知 → 先用工具探测或问用户，不得假设型号、不得假设已连接。\n"
        "- 本会话已确认的项不要重复追问，除非用户改口；用户改口时以新回答覆盖。\n"
        "- 能做的部分照做，不能做的部分如实说明；不允许为了「跑通流程」虚构硬件"
        "或使用占位数据。\n"
        "【本会话已记录的硬件状态】\n"
        + ("\n".join(known) if known else "- 暂无已确认项（需要时先探测或询问用户）")
        + "\n【仍未确认】\n"
        + ledger.unknown_line()
    )
