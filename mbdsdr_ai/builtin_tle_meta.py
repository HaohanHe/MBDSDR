# SPDX-License-Identifier: MIT
"""内置 TLE 来源与可信度登记（P11-5）。

历史验收提过：``decoders.BUILTIN_TLE`` 里有为「能用」而凑整的轨道根数
（历元小数全 0、根数圆整）。这类值只配当占位演示，**不许当精确值算过境**。

本模块不修改 ``decoders.py``（该文件属另一条线），而是在数据侧增加
``来源 + 校验和 + 历元是否凑整 + 可信度`` 登记：

    trust = "verified"            → 校验和正确、历元非凑整且在合理时间窗
            "synthetic_rounded"   → 历元/根数明显凑整（占位值）
            "invalid_checksum"    → TLE 校验和错误
            "implausible_epoch"   → 历元越界/几十年偏差
            "unverified"          → 其他无法确认的情况

只有 ``verified`` 才允许当精确数据用于过境/跟踪；其余必须在面板如实标注。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = [
    "analyze_tle_provenance",
    "builtin_provenance_table",
    "trust_label",
    "SYNTHETIC_TLE_POLICY",
    "synthetic_tle_policy",
]

_TRUST_LABELS = {
    "verified": "已核实（校验和正确、历元非凑整）",
    "synthetic_rounded": "合成/凑整占位值（不可用于精确过境或跟踪）",
    "invalid_checksum": "校验和错误（数据不可信）",
    "implausible_epoch": "历元异常（疑似错误年份/时钟）",
    "unverified": "未核实（来源无法确认）",
}


def trust_label(trust: str) -> str:
    return _TRUST_LABELS.get(trust, _TRUST_LABELS["unverified"])


# P12-5 内置合成 TLE 处置策略（选「保留但不可用于精确过境」，不删数据）：
# - 保留：离线/演示场景仍需有星可看，删掉会让天空图空白；
# - 限制：trust=synthetic_rounded 的条目 usable_for_tracking=False，
#   过境/跟踪/天空工具输出必须带「不可当精确值」告警（见 sdr_tools）；
# - 兜底：精确场景请用 Celestrak 实时 TLE（fetch_tle/refresh_tle_round）
#   或用户自备 .tle 文件；不存在「把合成值当实时」的路径。
SYNTHETIC_TLE_POLICY: Dict[str, Any] = {
    "decision": "keep_but_not_for_precise_passes",
    "summary": "内置合成/凑整 TLE 保留用于离线演示，但禁止作为精确过境/跟踪数据",
    "enforced_by": [
        "trust=synthetic_rounded → usable_for_tracking=False",
        "sdr_tools 输出附带「不可当精确过境时刻」告警",
        "orbit/decoders 取数不以内置合成值冒充实时（无缓存即诚实空态）",
    ],
    "precise_source": "Celestrak 实时 TLE（缓存 24h 门槛）或用户自备 .tle",
}


def synthetic_tle_policy() -> Dict[str, Any]:
    """返回内置合成 TLE 处置策略的可读副本（供诊断/UI/文档引用）。"""
    return dict(SYNTHETIC_TLE_POLICY,
                enforced_by=list(SYNTHETIC_TLE_POLICY["enforced_by"]))


def _epoch_fields(line1: str) -> Dict[str, Any]:
    raw = str(line1 or "")[18:32].strip() if len(str(line1 or "")) >= 32 else ""
    frac = raw[6:] if len(raw) >= 7 else ""
    trailing_zeros = len(frac) - len(frac.rstrip("0")) if frac else 0
    # 凑整特征：历元恰好是整天（.00000000）或半整天（.50000000），
    # 或小数尾部有 6 个以上连续的 0（如 .50000000 / .25000000）。
    rounded = bool(frac) and (all(c == "0" for c in frac) or trailing_zeros >= 6)
    return {
        "raw": raw,
        "fraction": frac,
        "fraction_all_zero": bool(frac) and all(c == "0" for c in frac),
        "fraction_rounded": rounded,
    }


def analyze_tle_provenance(name: str, line1: str, line2: str,
                           source: str = "unknown") -> Dict[str, Any]:
    """给一条 TLE 打「来源 + 可信度」标签（不修改数据本身）。

    返回::

        {"name", "source", "epoch_utc", "epoch_rounded", "checksum_ok",
         "trust", "trust_label", "usable_for_tracking", "note"}
    """
    from .time_basis import tle_epoch_report
    from .new_spacetime_tle import TLEManager

    ep = _epoch_fields(line1)
    checksum_ok = bool(TLEManager.validate_checksum(line1) and
                       TLEManager.validate_checksum(line2))
    report = tle_epoch_report(line1)
    trust = "verified"
    note = ""
    if ep["fraction_rounded"]:
        trust = "synthetic_rounded"
        note = "历元为整天/半整天凑整（占位值），不可当精确历元"
        if not checksum_ok:
            note += "；且行校验和不匹配"
    elif not checksum_ok:
        trust = "invalid_checksum"
        note = "TLE 行校验和不匹配，疑似抄录/凑整出错"
    elif report.get("ok") and not report.get("plausible"):
        trust = "implausible_epoch"
        note = report.get("reason", "历元异常")
    elif not report.get("ok"):
        trust = "implausible_epoch"
        note = "TLE 历元无法解析"
    else:
        note = "校验和正确，历元具亚秒精度"

    return {
        "name": str(name),
        "source": str(source),
        "epoch_utc": report.get("epoch_utc"),
        "epoch_rounded": bool(ep["fraction_rounded"]),
        "checksum_ok": checksum_ok,
        "trust": trust,
        "trust_label": trust_label(trust),
        "usable_for_tracking": trust == "verified",
        "note": note,
    }


def builtin_provenance_table(include_real: bool = True) -> Dict[str, Dict[str, Any]]:
    """登记内置 TLE 的可信度。

    - ``decoders.BUILTIN_TLE``：合成回退表（来源=内置演示数据）；
    - ``sat_tracker._BUILTIN_TLES``：Celestrak 抓取的真实表（可用时）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        from .decoders import BUILTIN_TLE
        for name, lines in BUILTIN_TLE.items():
            out.setdefault(str(name), analyze_tle_provenance(
                name, lines[0], lines[1],
                source="decoders.BUILTIN_TLE（内置演示/回退表）"))
    except Exception:
        pass
    if include_real:
        try:
            from .sat_tracker import _BUILTIN_TLES
            for name, line1, line2 in _BUILTIN_TLES:
                out[str(name)] = analyze_tle_provenance(
                    name, line1, line2,
                    source="sat_tracker.BUILTIN_TLES（Celestrak 抓取）")
        except Exception:
            pass
    return out
