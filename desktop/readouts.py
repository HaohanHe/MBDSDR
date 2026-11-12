# SPDX-License-Identifier: MIT
"""读数格式化与增益回读告警的唯一来源（desktop/readouts.py）
================================================================

状态面板、控制面板、模块面板、VFO 面板曾经各自内联 f-string，于是同一个
读数在同一个界面上出现多种写法（2026-11-12 实测）：

  * 同一频率 ``145.500000 MHz`` / ``145.500 MHz`` / ``145.5``；
  * 同一增益 ``20.0 dB`` / ``20 dB``；
  * 同一电平 ``-73.0 dBFS`` / ``-73 dBFS``；
  * 同一带宽 ``2.4 kHz`` / ``2 kHz``（后者是截断，读数失真）。

本模块只做「值 → 文本」的纯函数与一条告警文案，**不读配置、不持有状态**。
面板一律调用这里，不再复制字面量。

精度口径（每条都必须能说清理由，不能「顺手截断」）：
  * 频率：≥1 MHz 六位小数（1 Hz 分辨率，与 FreqDisplay 的 9 位数字网格
    同分辨率）；≥1 kHz 三位小数 kHz；更小整 Hz。三位小数会把 145.5125
    舍成 145.512（差 500 Hz，半末位向偶）——那也是错的数。
  * 带宽：≥1 MHz 三位小数 MHz；≥1 kHz 一位小数 kHz；更小整 Hz。
  * 增益：一位小数 dB。
  * 电平：一位小数 dBFS。
"""
import math

#: 空态唯一记号：不可用/未测到一律「—」，不拿 0 冒充读数。
EMPTY = "—"


def _finite(value):
    """转 float 并排除 None/NaN/Inf；不可用返回 None。"""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def format_frequency_hz(hz) -> str:
    """频率读数：1 Hz 分辨率。"""
    v = _finite(hz)
    if v is None:
        return EMPTY
    if v >= 1e6:
        return f"{v / 1e6:.6f} MHz"
    if v >= 1e3:
        return f"{v / 1e3:.3f} kHz"
    return f"{v:.0f} Hz"


def format_frequency_range_hz(start_hz, end_hz) -> str:
    """频率范围读数：两端都到 1 Hz，同单位时只写一次单位。

    扫频结果、频段边界这类「范围」不是两个独立读数拼串：分别格式化后
    重复写两次 ``MHz`` 会把窄面板里的条目撑长，并让读者以为两端单位
    可能不同。分档与精度仍由 :func:`format_frequency_hz` 决定。
    """
    start = format_frequency_hz(start_hz)
    end = format_frequency_hz(end_hz)
    if start == EMPTY or end == EMPTY:
        return f"{start}–{end}"
    start_value, start_unit = start.rsplit(" ", 1)
    end_value, end_unit = end.rsplit(" ", 1)
    if start_unit == end_unit:
        return f"{start_value}–{end_value} {start_unit}"
    return f"{start}–{end}"


def format_bandwidth_hz(hz, *, zero_text: str = EMPTY) -> str:
    """带宽读数。

    ``zero_text`` 让状态面板把 0 解释成「自动」，而 VFO 列表仍按空态显示
    「—」——语义不同，但不该各自再写一份格式化代码。
    """
    v = _finite(hz)
    if v is None:
        return EMPTY
    if v <= 0:
        return zero_text
    if v >= 1e6:
        return f"{v / 1e6:.3f} MHz"
    if v >= 1e3:
        return f"{v / 1e3:.1f} kHz"
    return f"{v:.0f} Hz"


def _one_decimal(v: float) -> float:
    """按一位小数取整，并把「半末位以内」的负数归零。

    ``-0.04`` 直接格式化是 ``-0.0``，用户会读成反方向读数（N-02）。
    """
    rounded = round(v, 1)
    return 0.0 if rounded == 0 else rounded


def format_gain_db(db) -> str:
    """增益读数：一位小数 dB。"""
    v = _finite(db)
    return EMPTY if v is None else f"{_one_decimal(v):.1f} dB"


def format_dbfs(level) -> str:
    """电平读数：一位小数 dBFS。"""
    v = _finite(level)
    return EMPTY if v is None else f"{_one_decimal(v):.1f} dBFS"


def gain_differs(applied_db, requested_db) -> bool:
    """后端回读值是否与配置意图不一致（浮点噪声容差 1e-9）。"""
    applied = _finite(applied_db)
    requested = _finite(requested_db)
    if applied is None or requested is None:
        return False
    return abs(applied - requested) > 1e-9


def gain_not_applied_hint(applied_db, requested_db) -> str:
    """增益「未生效」的统一告警文案（控制/模块/状态三块面板同字）。

    不带 ``⚠`` 前缀：由各面板按自己的呈现方式添加（标签 tooltip 加，
    日志不加），避免同一句话出现两种写法。
    """
    return (f"后端报告增益 {float(applied_db):g} dB，"
            f"配置意图 {float(requested_db):g} dB 未生效（配置文件未改）")
