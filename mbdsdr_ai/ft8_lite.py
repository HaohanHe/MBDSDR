"""
MBDSDR FT8 轻量分析层
======================

纯 Python 做 FT8 频谱感知与 8FSK 符号解调：
1. 在带宽内找 FT8 音峰簇（79Hz 内 8 个等间隔峰，间隔 6.25Hz）
2. 按 160ms 符号时长做 8FSK 硬判决，输出原始音调序列
3. 时间对齐：15s 周期边界检测

注意：完整 LDPC(K=91,N=174) 译码需外部 jt9/wsjtx 二进制；本模块输出
音峰位置、同步质量与原始符号序列，作为工具供 AI 判断"是否有 FT8、在哪、
时间对齐到哪"。不假装还原呼号文本。

MBDSDR Project - AI定义无线电 - GPL-3.0 - BI4MIB
"""

import math
from typing import List, Dict, Optional

TONE_SPACING_HZ = 6.25
SYMBOL_MS = 160
PERIOD_S = 15
BANDWIDTH_HZ = 79

# 近重复候选去重阈值（对标 wsjtx/lib/ft8/sync8.f90:138-149：
# |fdiff|<4Hz 且 |tdiff|<0.04s 视为同一信号的重复检测，只留最强）
FT8_DEDUP_FREQ_HZ = 4.0
FT8_DEDUP_TIME_S = 0.04


def dedup_candidates(candidates: List[Dict]) -> List[Dict]:
    """近重复候选去重。

    频率差 < FT8_DEDUP_FREQ_HZ 且时间差 < FT8_DEDUP_TIME_S 的候选视为同一
    信号的重复检测，只保留 SNR 最高的那个。按 SNR 降序处理，先保留的强候选
    作为判重基准。候选 dict 需含 center_hz；snr_est_db（或 snr_db）决定强弱；
    time_s（或 t0，缺省 0.0）为周期内时间偏移。
    """
    def _snr(c: Dict) -> float:
        return float(c.get("snr_est_db", c.get("snr_db", -1e9)))

    def _t(c: Dict) -> float:
        return float(c.get("time_s", c.get("t0", 0.0)))

    ordered = sorted(candidates, key=_snr, reverse=True)
    kept: List[Dict] = []
    for cand in ordered:
        dup = False
        for k in kept:
            df = abs(float(cand.get("center_hz", 0.0))
                     - float(k.get("center_hz", 0.0)))
            dt = abs(_t(cand) - _t(k))
            if df < FT8_DEDUP_FREQ_HZ and dt < FT8_DEDUP_TIME_S:
                dup = True
                break
        if not dup:
            kept.append(cand)
    return kept


def _goertzel(samples: List[float], rate: float, freq: float) -> float:
    """单频 Goertzel（任意频率，不量化到 bin），返回该频率能量幅度。"""
    n = len(samples)
    if n == 0:
        return 0.0
    w = 2.0 * math.pi * freq / rate
    coeff = 2.0 * math.cos(w)
    s1 = s2 = 0.0
    for s in samples:
        s0 = s + coeff * s1 - s2
        s2 = s1
        s1 = s0
    return math.sqrt(s1 * s1 + s2 * s2 - coeff * s1 * s2)


def detect_ft8_tone_center(samples: List[float], sample_rate: float,
                           search_low_hz: float = 300.0,
                           search_high_hz: float = 3000.0) -> Dict:
    """
    在音频中找 FT8 音峰中心频率。
    返回中心频率、峰值信噪比、是否检测到。
    """
    step = 1.0  # 1Hz 步进
    freqs = []
    energies = []
    f = search_low_hz
    # 粗扫
    while f <= search_high_hz:
        e = _goertzel(samples, sample_rate, f)
        freqs.append(f)
        energies.append(e)
        f += step
    if not energies:
        return {"detected": False}
    peak_i = max(range(len(energies)), key=lambda i: energies[i])
    peak_f = freqs[peak_i]
    peak_e = energies[peak_i]
    # 本底：去掉峰后取中位数
    bg = sorted(energies)[len(energies) // 2]
    snr = peak_e / bg if bg > 0 else 0
    return {
        "detected": snr > 3.0,
        "center_hz": round(peak_f, 1),
        "peak_energy": round(peak_e, 1),
        "snr_est_db": round(20 * math.log10(snr), 1) if snr > 0 else 0,
    }


def demodulate_8fsk(samples: List[float], sample_rate: float,
                    peak_hz: float, symbols: int = 79) -> Dict:
    """
    8FSK 硬判决。peak_hz 是检测到的最强音调之一；枚举它是第 0..7 个音调
    的 8 种相位假设，选判决裕度最大的那套。返回音调索引与置信度。
    LDPC 译码不在此做。
    """
    sps = int(sample_rate * SYMBOL_MS / 1000)
    segs = [samples[s * sps:(s + 1) * sps] for s in range(symbols)]
    segs = [g for g in segs if len(g) >= sps // 2]

    best = None
    for phase in range(8):
        # peak_hz 是第 phase 个音调
        center = peak_hz - (phase - 3.5) * TONE_SPACING_HZ
        tones = [center + (i - 3.5) * TONE_SPACING_HZ for i in range(8)]
        idxs = []
        margins = []
        for seg in segs:
            es = [_goertzel(seg, sample_rate, t) for t in tones]
            b = max(range(8), key=lambda i: es[i])
            srt = sorted(es, reverse=True)
            idxs.append(b)
            margins.append(srt[0] / srt[1] if srt[1] > 0 else 5.0)
        avg = sum(margins) / len(margins) if margins else 0
        if best is None or avg > best[0]:
            best = (avg, center, idxs)

    avg_margin, center, idxs = best
    return {
        "tone_indices": idxs,
        "num_symbols": len(idxs),
        "center_hz": round(center, 2),
        "avg_margin": round(avg_margin, 2),
        "note": "原始音调序列；LDPC 译码还原呼号需 jt9/wsjtx",
    }


def analyze_ft8_audio(samples: List[float], sample_rate: float) -> Dict:
    """完整 FT8 轻量分析：找峰 + 解调。"""
    peak = detect_ft8_tone_center(samples, sample_rate)
    if not peak.get("detected"):
        return {"detected": False, "reason": "未检测到 FT8 音峰", **peak}
    dem = demodulate_8fsk(samples, sample_rate, peak["center_hz"])
    return {"detected": True, **peak, **dem}


def decode_ft8_audio(samples: List[float], sample_rate: float) -> Dict:
    """端到端：FT8 音频 → 可读消息文本。

    真 Costas 跟踪路径（替代旧 Goertzel 粗扫 + 8 相位枚举）：
      costas_sync 粗同步（sync8.f90 时频谱 Costas7 匹配）
      → three_stage_sync 三段精同步（sync8d.f90 复数匹配 + 三块相位差残余频偏
        + 79×8 路能量积分，对应 ft8b.f90:109-162）
      → 58 数据符号 8 路软能量送 ft8_decode.decode_ft8_payload
      → CRC14 → unpack77。

    接口签名保持不变：decode_ft8_audio(samples, sample_rate) -> dict。
    """
    from mbdsdr_ai import ft8_decode
    from mbdsdr_ai.ft8_unpack import unpack77
    from mbdsdr_ai import ft8_costas

    # 粗同步：搜整个音频带（300-3000Hz），取 top-N 候选
    cands = ft8_costas.costas_sync(
        samples, sample_rate,
        search_lo_hz=300.0, search_hi_hz=3000.0,
        freq_step_hz=1.0, max_candidates=6)
    if not cands:
        return {"decoded": False, "reason": "Costas 同步峰未检出"}

    data_pos = ft8_decode.data_symbol_positions()  # 58 个数据符号位置
    decoded_cands: List[Dict] = []
    for c in cands:
        # 三段精同步：频率/时间精调 + 残余频偏校正 + 79×8 能量矩阵
        ref = ft8_costas.three_stage_sync(samples, sample_rate, c)
        e58 = ref["energies_79"][data_pos, :].tolist()
        r = ft8_decode.decode_ft8_payload(e58, snr_db=ref["snr_est_db"])
        # 打分：CRC 通过优先，其次 LDPC 早停（迭代少=收敛好）
        score = (1000 if r["crc_ok"] else 0) - r["iters"]
        decoded_cands.append({
            "center_hz": ref["center_hz"],
            "time_s": ref["time_offset_s"],
            "snr_est_db": ref["snr_est_db"],
            "score": score,
            "r": r,
        })

    # 近重复去重：同频段同时间只留 SNR 最高者（sync8.f90:138-149）
    decoded_cands = dedup_candidates(decoded_cands)
    if not decoded_cands:
        return {"decoded": False, "reason": "去重后无候选"}

    best = max(decoded_cands, key=lambda c: c["score"])
    r = best["r"]
    msg = unpack77(r["data_bits"])
    return {
        "decoded": r["crc_ok"],
        "crc_ok": r["crc_ok"],
        "iters": r["iters"],
        "message": msg.get("text"),
        "type": msg.get("type"),
        "call1": msg.get("call1"),
        "call2": msg.get("call2"),
        "center_hz": best["center_hz"],
        "deduped_count": len(decoded_cands),
        "snr_est_db": best["snr_est_db"],
    }
