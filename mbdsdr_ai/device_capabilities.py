# SPDX-License-Identifier: MIT
"""
MBDSDR 设备能力表（来源 / 状态 / 未知不装懂）
=============================================

枚举出的设备 dict 里，能力字段有两种来源：

  * ``probed``   —— 打开设备后真查到的（SoapySDR getSampleRateRange 等）；
  * ``declared`` —— 驱动/协议常量或内置参数表声明的，**没有探测**；
  * ``unknown``  —— 查询失败/驱动没报 —— 一律 ``None``。

红线：``unknown`` 的字段**不许显示任何具体数值**，也不许据此判定
「可调谐 / 不可调谐」。能力未知时唯一诚实的回答是「未知」。

本模块只做数据归一与人话格式化，不碰硬件、不改后端行为。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

STATUS_PROBED = "probed"
STATUS_DECLARED = "declared"
STATUS_UNKNOWN = "unknown"
STATUS_LABELS = {
    STATUS_PROBED: "探测",
    STATUS_DECLARED: "声明",
    STATUS_UNKNOWN: "未知",
}

#: 能力字段与界面标签（顺序即界面展示顺序）
FIELD_LABELS: Dict[str, str] = {
    "freq_range": "频率范围",
    "sample_rate_range": "采样率",
    "gain_range": "增益",
    "bandwidth_range": "带宽",
    "tx": "发射",
    "antenna": "天线口",
}
FIELD_ORDER: Tuple[str, ...] = tuple(FIELD_LABELS)

_RANGE_KEYS = ("freq_range", "sample_rate_range", "gain_range", "bandwidth_range")

_UNKNOWN_REASONS = {
    "freq_range": "设备未报告频率范围",
    "sample_rate_range": "设备未报告采样率范围",
    "gain_range": "设备未报告增益范围",
    "bandwidth_range": "设备未报告带宽范围",
    "tx": "设备未报告发射能力",
    "antenna": "设备未报告天线口",
}


def _as_range(value: Any) -> Optional[Tuple[float, float]]:
    """只接受合法的 (lo, hi)：有限、lo < hi。其余一律 None（未知）。"""
    try:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            lo, hi = float(value[0]), float(value[1])
            if math.isfinite(lo) and math.isfinite(hi) and lo < hi:
                return (lo, hi)
    except (TypeError, ValueError):
        pass
    return None


def format_hz(hz: float) -> str:
    """频率人话：225001 → 225.0 kHz，3.2e6 → 3.20 MHz。"""
    hz = float(hz)
    for unit, scale, decimals in (
            ("GHz", 1e9, 2), ("MHz", 1e6, 2), ("kHz", 1e3, 1)):
        if abs(hz) >= scale:
            x = hz / scale
            if x < 10:
                return f"{x:.2f} {unit}"
            if x < 1000:
                return f"{x:.1f} {unit}"
            return f"{x:.0f} {unit}"
    return f"{hz:.0f} Hz"


def format_range(value: Tuple[float, float], key: str) -> str:
    lo, hi = value
    if key == "gain_range":
        return f"{lo:.1f}–{hi:.1f} dB"
    return f"{format_hz(lo)}–{format_hz(hi)}"


@dataclass(frozen=True)
class CapabilityField:
    """一个能力项。known=False 时 value 必须为 None、text 不得含数值。"""

    key: str
    status: str = STATUS_UNKNOWN
    value: Optional[Tuple[float, float]] = None
    text: str = "未知"
    reason: str = ""

    @property
    def known(self) -> bool:
        """已知 = 状态不是 unknown。非量程项（发射/天线口）可以无 value。"""
        return self.status in (STATUS_PROBED, STATUS_DECLARED)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "known": self.known,
            "text": self.text,
            "reason": self.reason,
        }


def _unknown(key: str, reason: str = "") -> CapabilityField:
    why = reason or _UNKNOWN_REASONS.get(key, "能力未知")
    return CapabilityField(key=key, status=STATUS_UNKNOWN, value=None,
                           text=f"未知（{why}）", reason=why)


def _range_field(device: Dict[str, Any], key: str,
                 statuses: Dict[str, str], notes: Dict[str, str]) -> CapabilityField:
    raw = _as_range(device.get(key))
    if raw is None:
        return _unknown(key, notes.get(key, ""))
    status = statuses.get(key)
    if status not in (STATUS_PROBED, STATUS_DECLARED):
        status = STATUS_DECLARED
    label = format_range(raw, key)
    suffix = f"（{STATUS_LABELS[status]}）"
    return CapabilityField(key=key, status=status, value=raw,
                           text=label + suffix)


def _tx_field(device: Dict[str, Any],
              statuses: Dict[str, str], notes: Dict[str, str]) -> CapabilityField:
    tx_range = _as_range(device.get("tx_range"))
    if tx_range is not None:
        status = statuses.get("tx")
        if status not in (STATUS_PROBED, STATUS_DECLARED):
            status = STATUS_DECLARED
        return CapabilityField(key="tx", status=status, value=tx_range,
                               text=f"可发 {format_range(tx_range, 'freq_range')}"
                                    f"（{STATUS_LABELS[status]}）")
    channels = device.get("tx_channels")
    if channels is not None:
        try:
            n = int(channels)
        except (TypeError, ValueError):
            return _unknown("tx", notes.get("tx", ""))
        status = statuses.get("tx")
        if status not in (STATUS_PROBED, STATUS_DECLARED):
            status = STATUS_PROBED
        if n > 0:
            return CapabilityField(key="tx", status=status, value=None,
                                   text=f"可发射（驱动报告 {n} 个 TX 通道，"
                                        f"{STATUS_LABELS[status]}）")
        return CapabilityField(key="tx", status=status, value=None,
                               text=f"不可发射（驱动报告 0 个 TX 通道，"
                                    f"{STATUS_LABELS[status]}）")
    supports = device.get("supports_tx")
    if isinstance(supports, bool):
        status = STATUS_DECLARED
        return CapabilityField(key="tx", status=status, value=None,
                               text=("可发射（设备声明，未给频率范围）"
                                     if supports else "不可发射（设备声明）"))
    return _unknown("tx", notes.get("tx", ""))


def _antenna_field(device: Dict[str, Any],
                   statuses: Dict[str, str], notes: Dict[str, str]) -> CapabilityField:
    antennas = device.get("antennas")
    if isinstance(antennas, (list, tuple)):
        names = [str(a) for a in antennas if str(a).strip()]
        status = statuses.get("antenna")
        if status not in (STATUS_PROBED, STATUS_DECLARED):
            status = STATUS_PROBED
        text = "、".join(names) if names else "无（驱动未报告天线口）"
        text += f"（{STATUS_LABELS[status]}）"
        return CapabilityField(key="antenna", status=status,
                               value=None, text=text)
    return _unknown("antenna", notes.get("antenna", ""))


#: 后端可枚举离散档位的模块落点。前端**不许**再写自己的档位表；
#: 可见档位一律经 ``sample_rate_options`` 从后端能力过滤得到。
_BACKEND_RATE_TABLE = {
    "rtlsdr": ("mbdsdr_ai.sdr_backend", "RTLSDRBackend", "SAMPLE_RATES"),
    "hackrf": ("mbdsdr_ai.hackrf_params", "SUPPORTED_SAMPLE_RATES", None),
    "lime": ("mbdsdr_ai.limesuite_params", "SUPPORTED_SAMPLE_RATES", None),
}


def _backend_rate_table(driver: str) -> Optional[Tuple[float, ...]]:
    """按 driver 取后端自己的离散档位表；没有表就返回 None（不许编）。"""
    key = (driver or "").strip().lower().replace("-", "").replace("_", "")
    entry = None
    for name, val in _BACKEND_RATE_TABLE.items():
        if name in key:
            entry = val
            break
    if entry is None:
        return None
    try:
        mod = __import__(entry[0], fromlist=[entry[1]])
        if entry[2] is None:
            return tuple(float(x) for x in getattr(mod, entry[1]))
        obj = getattr(mod, entry[1])
        return tuple(float(x) for x in getattr(obj, entry[2]))
    except Exception as exc:
        # 后端模块缺失/属性名变更 → 按未知处理（不编档位），原因必须可查
        logger.warning("读取后端 %s 采样率档位表失败，按未知处理: %s: %s",
                       entry[0], type(exc).__name__, exc)
        return None


def sample_rate_options(device: Dict[str, Any]) -> Optional[list]:
    """把一台设备归一成**可见采样率档位**；未知返回 None（不造假表）。

    规则（与后端能力同源）：
      * 范围 ``unknown``/缺失 → ``None``；
      * 后端有离散档位表 → 取表内落在范围里的档；
      * 后端只有范围（连续可调）→ 只给范围两端（两端按定义可设，不编中间档）；
      * 过滤后为空 → ``None``（不拿默认值凑数）。
    """
    report = build_capability_report(device)
    field = report.get("sample_rate_range")
    if field is None or not field.known or field.value is None:
        return None
    lo, hi = field.value
    table = _backend_rate_table(str(device.get("driver") or ""))
    if table:
        opts = sorted(int(r) for r in table if lo <= r <= hi)
        return opts or None
    # 连续可调后端：两端是设备自己声明的边界，可设；中间不擅自补格。
    lo_i, hi_i = int(round(lo)), int(round(hi))
    if lo_i < hi_i:
        return [lo_i, hi_i]
    return None


def sample_rate_options_for_backend(backend: Any) -> Optional[list]:
    """从已连接后端取可见档位；后端没报告范围就是 None（界面不得编表）。"""
    dev = getattr(backend, "device", None)
    if dev is None:
        return None
    raw = {
        "driver": str(getattr(dev, "device_type", "") or ""),
        "sample_rate_range": getattr(dev, "sample_rate_range", None),
        "capability_status": {"sample_rate_range": STATUS_DECLARED},
    }
    return sample_rate_options(raw)


def build_capability_report(device: Dict[str, Any]) -> Dict[str, CapabilityField]:
    """把设备 dict 归一成 {字段: CapabilityField}；缺失即未知，绝不填默认值。"""
    device = dict(device or {})
    statuses = dict(device.get("capability_status") or {})
    notes = dict(device.get("capability_note") or {})
    report: Dict[str, CapabilityField] = {}
    for key in _RANGE_KEYS:
        report[key] = _range_field(device, key, statuses, notes)
    report["tx"] = _tx_field(device, statuses, notes)
    report["antenna"] = _antenna_field(device, statuses, notes)
    return report


def summarize(report: Dict[str, CapabilityField],
              keys: Optional[Iterable[str]] = None) -> str:
    """一行能力摘要；未知项只写「未知」，不写任何数值。"""
    use = list(keys) if keys is not None else list(FIELD_ORDER)
    parts = []
    for key in use:
        f = report.get(key)
        if f is None:
            continue
        parts.append(f"{FIELD_LABELS.get(key, key)} {f.text}")
    return "｜".join(parts)


def summarize_device(device: Dict[str, Any]) -> str:
    return summarize(build_capability_report(device))


def check_range(report: Dict[str, CapabilityField], key: str,
                value: float) -> Tuple[Optional[bool], str]:
    """按能力表校验一个取值。

    返回 ``(None, 原因)`` 表示范围未知——**不许**冒充校验通过；
    ``(False, 原因)`` 表示明确越界；``(True, "")`` 表示已知范围内。
    """
    f = report.get(key)
    if f is None or not f.known:
        reason = (f.reason if f is not None and f.reason else "能力未知")
        return None, f"{FIELD_LABELS.get(key, key)}范围未知（{reason}），无法校验"
    lo, hi = f.value  # type: ignore[misc]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False, f"{FIELD_LABELS.get(key, key)}取值不是数字"
    if not (lo <= v <= hi):
        return False, (f"超出{FIELD_LABELS.get(key, key)} {format_range(f.value, key)}")  # type: ignore[arg-type]
    return True, ""


def unknown_values(report: Dict[str, CapabilityField]) -> Dict[str, str]:
    """未知字段 → 原因（供 UI/AI 明确说「不知道什么」）。"""
    return {k: f.reason for k, f in report.items() if not f.known}
