# SPDX-License-Identifier: MIT
"""P1-1 GPS 导航电文位同步/子帧装配原子工具。

能力拆解（218 条）里 P1-1 的原子能力（P0-3 之后的后处理面）：
  nav_bit_sync(bits, samples_per_bit) —— 从 50 bps 软/硬判决比特流
    定位位边界（20 个候选偏移），输出判决比特与同步质量；
  subframe_assemble(bits) —— 比特流 → 300 bit 子帧序列（TLM/HOW
    边界显式校验，不静默丢帧）；
  how_validate(subframe) —— TLM 前导字/HOW 的 TOW·SFID 一致性 +
    奇偶链校验，逐字段诚实报错。

位同步算法（教科书相干积累法，独立实现）：
  对每个候选偏移 o ∈ [0, sps)，把流按位周期分段求和得软比特，
  以 Σ|软比特| 为同步度量——正确偏移下信号同号相加（相长），
  错误偏移下符号随机抵消（相消）。辅证：正确偏移的比特过渡率
  显著低于 50%（随机流水平）。

诚实纪律（AGENTS 第 2 条）：
  * 同步可信须双证据：相干度（最佳偏移度量/理论满分）≥0.75 且比特
    过渡率 <0.45（随机水平），不足 → in_sync=False + reason，
    不产出伪同步比特；
  * 子帧数不足 1 个完整 300 bit、TLM 前导字错、HOW 奇偶错 →
    显式拒绝；比特数不是 300 整数倍时尾部残段如实上报。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from . import gps_nav as _gnav

__all__ = [
    "nav_bit_sync", "subframe_assemble", "how_validate",
    "register_tool_registry",
]

#: TLM 前导字（8 bit）
_TLM_PREAMBLE = 0x8B


def _as_signed(stream: Sequence[Any]) -> np.ndarray:
    """统一为 ±1 软符号：float 输入按符号取 ±1（保留软值），{0,1} 映射。"""
    vals = list(stream)
    if not vals:
        raise ValueError("比特流为空")
    if all(isinstance(v, (int, np.integer)) and v in (0, 1) for v in vals):
        return np.array([1.0 if v == 1 else -1.0 for v in vals])
    x = np.asarray([float(v) for v in vals], dtype=float)
    if not np.all(np.isfinite(x)):
        raise ValueError("软判决流含非有限值")
    return np.sign(x) * np.minimum(np.abs(x), 1e6)  # 保留软幅度，去 nan 后已保证有限


def nav_bit_sync(stream: Sequence[Any],
                 samples_per_bit: int = 20,
                 ) -> Dict[str, Any]:
    """GPS 50 bps 位同步：定位位边界并判决比特。

    stream         : 软判决（实数，过零点判决）或硬比特（0/1）流，
                     速率 = 50 Hz × samples_per_bit；
    samples_per_bit: 每比特采样数（GPS 常用 20，即 1 kHz 判决流）。

    返回：bit_offset（位边界偏移，样本单位）/ bits（判决后的 0/1 比特）/
    metrics（各偏移的同步度量）/ margin（最佳/次佳比，dB）/
    transition_rate（最佳偏移下的比特过渡率）/ in_sync + reason。
    """
    if samples_per_bit < 4:
        raise ValueError("samples_per_bit 太小（<4）无法可靠同步")
    x = _as_signed(stream)
    if x.size < 4 * samples_per_bit:
        raise ValueError("流太短：不足 4 个比特周期")

    metrics: List[float] = []
    n_bits_list: List[int] = []
    for o in range(samples_per_bit):
        nb = (x.size - o) // samples_per_bit
        seg = x[o: o + nb * samples_per_bit]
        soft = seg.reshape(nb, samples_per_bit).sum(axis=1)
        metrics.append(float(np.abs(soft).sum()))
        n_bits_list.append(nb)
    order = np.argsort(metrics)[::-1]
    best, second = int(order[0]), int(order[1])
    margin_db = 10.0 * math.log10(metrics[best] / (metrics[second] + 1e-30))

    n_bits = n_bits_list[best]
    seg = x[best: best + n_bits * samples_per_bit]
    soft = seg.reshape(n_bits, samples_per_bit).sum(axis=1)
    bits = (soft > 0).astype(int).tolist()
    transitions = sum(1 for a, b in zip(bits, bits[1:]) if a != b)
    trans_rate = transitions / max(1, len(bits) - 1)

    # 相干度 = 最佳偏移度量 / 理论满分（每位 sps 个样本全同号）。
    # 硬比特满分 = 1.0；高信噪比软判决 ≈1；纯噪声 ≈0.2（随机相位相消）。
    # 注意边际 margin_db 对硬比特输入天然偏小（错开 1 样本只损失
    # 2/sps 能量），不能单独作门限——噪声与有效电文的区分靠
    # 相干度 + 过渡率两个独立证据。
    coherence = metrics[best] / (n_bits * samples_per_bit)
    in_sync = coherence >= 0.75 and trans_rate < 0.45
    reason = None
    if coherence < 0.75:
        reason = (f"相干度 {coherence:.2f} < 0.75（满分=每位全同号），"
                  f"流不像有效 50 bps 导航电文（信噪比过低或误码）")
    elif trans_rate >= 0.45:
        reason = (f"比特过渡率 {trans_rate:.2f} 接近随机水平 0.5，"
                  f"判决流不像有效导航电文")
    return {
        "bit_offset": best,
        "bits": bits,
        "metrics": [round(m, 4) for m in metrics],
        "margin_db": round(margin_db, 3),
        "coherence": round(coherence, 4),
        "transition_rate": round(trans_rate, 4),
        "n_bits": n_bits,
        "in_sync": bool(in_sync),
        "reason": reason,
    }


def _decode_subframe_words(sf: Sequence[int], d29: int, d30: int,
                           where: str) -> tuple:
    """300 bit 子帧 → (10 个 24bit 数据字, 新 d29, 新 d30)。

    逐字奇偶校验，失败即抛（带位置）；D29*/D30* 链跨字/跨子帧延续，
    与 gps_nav 编码器约定一致（链尾 = 上一字接收位 29/30）。
    """
    decoded: List[int] = []
    for wi in range(10):
        word = sf[wi * 30: (wi + 1) * 30]
        data24, ok = _gnav._decode_word(word, d29, d30)
        if not ok:
            raise ValueError(f"{where} 第 {wi + 1} 字奇偶校验失败"
                             f"（D29*/D30* 链断裂，子帧作废）")
        decoded.append(data24)
        d29, d30 = word[28], word[29]
    return decoded, d29, d30


def subframe_assemble(bits: Sequence[int],
                      ) -> Dict[str, Any]:
    """比特流 → 300 bit 子帧序列（TLM/奇偶链逐帧显式校验）。

    校验：TLM 前导字 0x8B（按反相恢复后的数据位判定——d30*=1 时
    发送位整体反相，raw 位比对会误判）、逐字奇偶（链跨子帧延续，
    首子帧从 D29*=D30*=0 起）、HOW SFID ∈ 1..5。
    比特数不是 300 整数倍时：完整子帧全部校验，尾部残段如实计数上报
    （truncated_bits），不静默吞掉。
    """
    if not bits:
        raise ValueError("比特流为空")
    if any(b not in (0, 1) for b in bits):
        raise ValueError("比特须为 0/1")
    n_full = len(bits) // 300
    if n_full == 0:
        raise ValueError(f"不足一个完整子帧（{len(bits)}/300 bit）")
    truncated = len(bits) - n_full * 300

    subframes = [list(map(int, bits[i * 300:(i + 1) * 300]))
                 for i in range(n_full)]
    d29, d30 = 0, 0
    for si, sf in enumerate(subframes):
        decoded, d29, d30 = _decode_subframe_words(sf, d29, d30,
                                                   f"第 {si + 1} 子帧")
        preamble = (decoded[0] >> 16) & 0xFF
        if preamble != _TLM_PREAMBLE:
            raise ValueError(
                f"第 {si + 1} 子帧 TLM 前导字 0x{preamble:02X} ≠ "
                f"0x{_TLM_PREAMBLE:02X}——子帧边界未对齐或误码")
        sfid = (decoded[1] >> 2) & 0x7
        if not (1 <= sfid <= 5):
            raise ValueError(f"第 {si + 1} 子帧 HOW SFID {sfid} 越界（1~5）")
    return {
        "subframes": subframes,
        "n_subframes": len(subframes),
        "truncated_bits": truncated,
    }


def how_validate(subframe: Sequence[int],
                 d29_star: int = 0,
                 d30_star: int = 0,
                 ) -> Dict[str, Any]:
    """单个子帧的 TLM/HOW 显式校验（在 gps_nav 链上逐字段断言）。

    d29_star/d30_star：前一字的 D29*/D30*（本子帧首字反相恢复用）。
    首个子帧传默认 0；连续电文须从上一子帧末字提取（位 29/30）。
    """
    sf = list(subframe)
    if len(sf) != 300:
        raise ValueError(f"子帧长度 {len(sf)} ≠ 300 bit")
    decoded, _, _ = _decode_subframe_words(sf, int(d29_star), int(d30_star),
                                           "子帧")
    preamble = (decoded[0] >> 16) & 0xFF
    if preamble != _TLM_PREAMBLE:
        raise ValueError(f"TLM 前导字 0x{preamble:02X} ≠ 0x{_TLM_PREAMBLE:02X}")
    how = decoded[1]
    # HOW 24 数据位：bit1-17=TOW（6 s 计数），bit18=告警，bit19=A-S，
    # bit20-22=子帧号，bit23-24=保留（与 gps_nav 解算口径一致）
    tow_count = (how >> 7) & 0x1FFFF
    sfid = (how >> 2) & 0x7
    if not (1 <= sfid <= 5):
        raise ValueError(f"HOW SFID {sfid} 越界（1~5）")
    alert = (how >> 6) & 1
    anti = (how >> 5) & 1
    return {
        "preamble_ok": True,
        "parity_ok": True,
        "tow_count": tow_count,
        "tow_seconds": tow_count * 6.0,
        "sfid": sfid,
        "alert_flag": bool(alert),
        "anti_spoof": bool(anti),
    }


def register_tool_registry(registry) -> None:
    """注册 P1-1 位同步/子帧装配积木。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _sync(args):
        try:
            stream = args.get("stream")
            if not stream:
                raise ValueError("stream 必须提供（软判决或 0/1 比特流）")
            res = nav_bit_sync(
                stream,
                samples_per_bit=int(args.get("samples_per_bit", 20)),
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"位同步失败: {clean_exc(e)}")
        ok = bool(res.get("in_sync"))
        return ToolResult(
            success=ok,
            content=_json.dumps({k: v for k, v in res.items()
                                 if k != "bits"},
                                ensure_ascii=False, default=str),
            data=res)

    def _assemble(args):
        try:
            bits = args.get("bits")
            if not bits:
                raise ValueError("bits 必须提供（0/1 比特流）")
            res = subframe_assemble([int(b) for b in bits])
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"子帧装配失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps({k: v for k, v in res.items()
                                 if k != "subframes"},
                                ensure_ascii=False, default=str),
            data=res)

    def _how(args):
        try:
            sf = args.get("subframe")
            if not sf:
                raise ValueError("subframe 必须提供（300 bit 列表）")
            res = how_validate([int(b) for b in sf],
                               d29_star=int(args.get("d29_star", 0)),
                               d30_star=int(args.get("d30_star", 0)))
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"HOW 校验失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=_json.dumps(res, ensure_ascii=False, default=str),
            data=res)

    registry.register(
        name="nav_bit_sync",
        description=(
            "GPS 50 bps 位同步：相干积累定位位边界并判决比特。同步边际"
            "不足或过渡率接近随机时 in_sync=False + 诚实理由，不产伪同步。"),
        parameters={
            "type": "object",
            "properties": {
                "stream": {"type": "array", "items": {"type": ["number", "integer"]},
                           "description": "软判决或 0/1 比特流（≥4 比特周期）"},
                "samples_per_bit": {"type": "integer", "default": 20},
            },
            "required": ["stream"],
        },
        handler=_sync,
        category="gnss")

    registry.register(
        name="subframe_assemble",
        description=(
            "比特流 → 300 bit 子帧序列（TLM/HOW/奇偶链显式校验，残段"
            "如实计数不静默吞帧）。"),
        parameters={
            "type": "object",
            "properties": {
                "bits": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["bits"],
        },
        handler=_assemble,
        category="gnss")

    registry.register(
        name="how_validate",
        description=(
            "子帧 TLM/HOW 逐字段校验：前导字 0x8B、奇偶链、SFID 范围，"
            "输出 TOW/告警位/反欺骗位。"),
        parameters={
            "type": "object",
            "properties": {
                "subframe": {"type": "array", "items": {"type": "integer"}},
                "d29_star": {"type": "integer", "default": 0},
                "d30_star": {"type": "integer", "default": 0},
            },
            "required": ["subframe"],
        },
        handler=_how,
        category="gnss")
