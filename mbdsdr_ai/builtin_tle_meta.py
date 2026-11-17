# SPDX-License-Identifier: MIT
"""内置 TLE 来源与可信度登记（P11-5 / D4）。

历史 ``decoders.BUILTIN_TLE`` 里有为「能用」而凑整的轨道根数（历元小数全 0、
根数圆整）。按超级 spec D4「不得内置伪造 TLE」，这些合成值**已从生产路径
移除**：``decoders.BUILTIN_TLE`` 现在是空表，取不到真实 TLE 就返回空态
「无 TLE，无法预报」。测试所需的样本改放 ``tests/fixtures/tle_samples.py``。

本模块继续提供数据侧登记能力：
``来源 + 校验和 + 历元是否凑整 + 可信度``——

    trust = "verified"            → 校验和正确、历元非凑整且在合理时间窗
            "synthetic_rounded"   → 历元/根数明显凑整（占位值，仅测试/登记用）
            "invalid_checksum"    → TLE 校验和错误
            "implausible_epoch"   → 历元越界/几十年偏差
            "unverified"          → 其他无法确认的情况

生产内置表只剩 ``sat_tracker._BUILTIN_TLES`` 的 Celestrak 静态快照，
``origin_kind`` 明确标为 ``external_snapshot``（外部提供，但不是在线实时）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = [
    "analyze_tle_provenance",
    "builtin_provenance_table",
    "trust_label",
    "EXTERNAL_SNAPSHOT_SOURCE",
    "SYNTHETIC_TLE_POLICY",
    "synthetic_tle_policy",
]

#: 内置快照的**统一来源口径**：这些根数不是系统在线拉到的，而是外部
#: （Celestrak）某次发布后被复制进仓库的**静态快照**。界面/诊断必须写
#: 「外部提供（快照）」，不能写成「刚拉取的实时轨道」。
EXTERNAL_SNAPSHOT_SOURCE = "外部提供（Celestrak 快照）"

_TRUST_LABELS = {
    "verified": "已核实（校验和正确、历元非凑整）",
    "synthetic_rounded": "合成/凑整占位值（不可用于精确过境或跟踪）",
    "invalid_checksum": "校验和错误（数据不可信）",
    "implausible_epoch": "历元异常（疑似错误年份/时钟）",
    "unverified": "未核实（来源无法确认）",
}


def trust_label(trust: str) -> str:
    return _TRUST_LABELS.get(trust, _TRUST_LABELS["unverified"])


# P12-5 / D4 内置合成 TLE 处置（2026-11-17 收紧）：
# - **从生产路径移除**：``decoders.BUILTIN_TLE`` 置空，不再用凑整根数回退；
# - 无 TLE 时诚实空态「无 TLE，无法预报」，精确场景必须走 Celestrak
#   在线/缓存或用户自备 .tle；
# - 测试所需样本改放 ``tests/fixtures/tle_samples.py``，不回流生产表；
# - ``sat_tracker._BUILTIN_TLES`` 的 Celestrak 快照保留，但必须标
#   ``external_snapshot`` 并带来源/epoch，不得冒充在线实时。
SYNTHETIC_TLE_POLICY: Dict[str, Any] = {
    "decision": "removed_from_production",
    "summary": "生产路径不再内置合成/凑整 TLE；无真实 TLE 时如实空态",
    "enforced_by": [
        "decoders.BUILTIN_TLE 为空表，_resolve_tle 失败返回 no-tle",
        "controller.get_satellite_pass 无 TLE 返回 error=no_tle",
        "sdr_tools 过境/可见卫星输出明写「无 TLE，无法预报」",
        "测试样本位于 tests/fixtures/tle_samples.py，不进入生产表",
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
    """登记内置 TLE 的可信度与来源属性。

    - ``decoders.BUILTIN_TLE``：**已清空**（生产路径不含合成/凑整根数），
      因此不再出现在本表；测试样本请用 ``tests/fixtures/tle_samples.py``
      调用 :func:`analyze_tle_provenance` 单独登记；
    - ``sat_tracker._BUILTIN_TLES``：Celestrak 抓取的静态快照
      （``origin_kind="external_snapshot"``，外部提供但不随源更新）。

    「外部提供」只说明数据来自外部发布，**不等于在线实时**：静态快照的
    历元年龄仍按 ``tle_integrity.freshness`` 判档。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if include_real:
        try:
            from .sat_tracker import _BUILTIN_TLES
            for name, line1, line2 in _BUILTIN_TLES:
                prov = analyze_tle_provenance(
                    name, line1, line2,
                    source=f"sat_tracker.BUILTIN_TLES（{EXTERNAL_SNAPSHOT_SOURCE}）")
                prov["origin_kind"] = "external_snapshot"
                prov["provided_by"] = EXTERNAL_SNAPSHOT_SOURCE
                out[str(name)] = prov
        except Exception:
            pass
    return out
