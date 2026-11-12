# SPDX-License-Identifier: MIT
"""FT8 Costas 频率/相位跟踪（独立实现）。

三段同步法：
  1. costas_sync —— 粗同步：按符号长 FFT 建时频谱，在 (频率 bin, 时间 lag)
       二维网格上对 Costas7 序列 [3,1,4,0,6,5,2] 做匹配能量积累，
       输出多个候选 (center_hz, time_offset_s, sync_energy)。
  2. three_stage_sync —— 精同步：
       第一段：复数匹配滤波器在粗峰附近做 ±3Hz / ±0.5 符号的局部细扫。
       第二段：用三个同步块（符号 0/36/72，间隔 5.76s）的复相关相位差
              估计残余频偏，mHz 级。
       第三段：用校正后的频率/时间积分 79×8 路符号能量矩阵，直接送
              ft8_decode.decode_ft8_payload。

帧结构 S7 D29 S7 D29 S7：同步块符号位置 0-6、36-42、72-78；Costas7 序列
ICOS7。音调间隔 6.25Hz、符号 160ms。以上均为公开协议事实。
"""
from __future__ import annotations

import math
from typing import List, Dict

import numpy as np

from mbdsdr_ai.ft8_encode import ICOS7, TONE_SPACING_HZ, SYMBOL_MS, NN

# 三个 Costas 同步块在 79 符号帧中的起始符号（genft8.f90:33-35）
_SYNC_BLOCK_STARTS = (0, 36, 72)
# 块中心时刻（相对符号 0 起点）：块 b 中心 = (start + 3) * 0.16 s
_SYNC_BLOCK_CENTER_S = tuple((s + 3) * SYMBOL_MS / 1000.0 for s in _SYNC_BLOCK_STARTS)


def _sps(sample_rate: float) -> int:
    """每符号采样数（ft8_params.f90: NSPS=1920 @12000S/s）。"""
    return int(round(sample_rate * SYMBOL_MS / 1000.0))


def _symbol_spectrogram(x: np.ndarray, sr: float):
    """按符号长做 FFT 建时频谱（sync8.f90:33-43）。

    返回 (frames, df, nstep, sps, nfft)：
      frames[j, b] = |rfft|²，j 为 1/4 符号时间步，b 为频率 bin；
      nstep = sps/4（NSTEP，sync8.f90:10）；
      nfft = 2*sps（NFFT1=2*NSPS，ft8_params.f90:9），df = sr/nfft = 3.125Hz @12k。
    """
    sps = _sps(sr)
    nstep = max(1, sps // 4)
    nfft = 2 * sps
    nsteps = max(1, (len(x) - sps) // nstep + 1)
    nsteps = min(nsteps, (len(x) - sps) // nstep + 1)
    nbins = nfft // 2 + 1
    frames = np.zeros((nsteps, nbins), dtype=np.float64)
    for j in range(nsteps):
        seg = x[j * nstep:j * nstep + sps]
        if len(seg) < sps:
            break
        X = np.fft.rfft(seg, n=nfft)
        frames[j] = X.real * X.real + X.imag * X.imag
    df = sr / nfft
    return frames, df, nstep, sps, nfft


def costas_sync(samples: List[float], sample_rate: float,
                search_lo_hz: float = 300.0, search_hi_hz: float = 3000.0,
                freq_step_hz: float = 1.0,
                max_candidates: int = 8) -> List[Dict]:
    """粗 Costas 同步：在时频谱上匹配 Costas7 阵列。

    对每个候选中心频率 bin i（i 对应最低音调 tone0 的 bin）和时间 lag j
    （符号 0 起点的 1/4 符号步），积累三个同步块共 21 个 Costas 符号的
    匹配能量，并与 8 音调平均能量做比（sync8.f90:62-83 的 sync_abc）。

    返回按匹配能量降序的候选列表，每个含：
      center_hz   —— 8 音带中心（tone 3.5）频率
      time_offset_s —— 符号 0 相对缓冲起点的时间
      sync_energy / sync_ratio / snr_est_db
    """
    x = np.asarray(samples, dtype=np.float64)
    sr = float(sample_rate)
    if len(x) < 2 * _sps(sr):
        return []

    frames, df, nstep, sps, nfft = _symbol_spectrogram(x, sr)
    nsteps = frames.shape[0]
    nbins = frames.shape[1]
    nssy = sps // nstep            # 每符号时间步数（sync8.f90: nssy=NSPS/NSTEP=4）
    nfos = int(round(TONE_SPACING_HZ / df))   # 每音调间隔 bin 数（sync8.f90: nfos=2）
    if nfos < 1:
        nfos = 1

    # 块 3 需要 j + 78*nssy 个时间步
    max_j = nsteps - 1 - 78 * nssy
    if max_j < 0:
        return []

    # tone0 bin i 搜索范围：tone0..tone7 全部落入 [search_lo, search_hi]
    lo_bin = max(1, int(math.floor(search_lo_hz / df)) - 0)
    hi_bin = min(nbins - 1 - nfos * 7, int(math.ceil(search_hi_hz / df)))
    if hi_bin <= lo_bin:
        return []
    n_bins = hi_bin - lo_bin + 1

    matched = np.zeros((max_j + 1, n_bins), dtype=np.float64)
    bg = np.zeros((max_j + 1, n_bins), dtype=np.float64)

    for blk in _SYNC_BLOCK_STARTS:
        for n in range(7):
            tone = ICOS7[n]
            bin_off = nfos * tone
            j0 = (blk + n) * nssy
            js = np.arange(max_j + 1) + j0
            valid = js < nsteps
            js_v = js[valid]
            # matched[valid, :] += frames[js_v, lo_bin+bin_off : lo_bin+bin_off+n_bins]
            col0 = lo_bin + bin_off
            col1 = min(col0 + n_bins, nbins)
            if col1 > col0:
                matched[np.ix_(valid, np.arange(col1 - col0))] += \
                    frames[js_v, col0:col1]
            # 背景：8 个音调在该符号位置的能量和（sync8.f90:66 t0a）
            for k in range(8):
                c0 = lo_bin + nfos * k
                c1 = min(c0 + n_bins, nbins)
                if c1 > c0:
                    bg[np.ix_(valid, np.arange(c1 - c0))] += frames[js_v, c0:c1]

    # sync_abc = matched / ((bg - matched)/6)，sync8.f90:77-78
    denom = (bg - matched) / 6.0
    ratio = np.where(denom > 1e-12, matched / np.maximum(denom, 1e-12), 0.0)
    # 平滑：沿时间维做 3 点均值，抑制单步噪声
    if ratio.shape[0] >= 3:
        k = np.ones(3) / 3.0
        ratio_s = np.apply_along_axis(
            lambda r: np.convolve(r, k, mode="same"), 0, ratio)
    else:
        ratio_s = ratio

    # 取局部峰值：贪心取远间隔的 top-k
    flat = ratio_s.ravel()
    order = np.argsort(-flat)
    picked: List[tuple] = []
    min_dj = max(1, nssy // 2)   # ±0.5 符号时间间隔
    min_di = max(1, nfos)          # 至少一个音调间隔
    for idx in order:
        j, i = divmod(int(idx), n_bins)
        v = ratio_s[j, i]
        if not np.isfinite(v) or v <= 1.0:
            continue
        if any(abs(j - pj) < min_dj and abs(i - pi) < min_di
               for pj, pi, _ in picked):
            continue
        picked.append((j, i, float(v)))
        if len(picked) >= max_candidates:
            break

    out: List[Dict] = []
    for j, i, v in picked:
        # 频率抛物线插值做亚 bin 精化（亚 Hz 级，满足 freq_step_hz≤1Hz）
        i_lo = max(0, i - 1)
        i_hi = min(n_bins - 1, i + 1)
        y_lo, y_c, y_hi = ratio_s[j, i_lo], ratio_s[j, i], ratio_s[j, i_hi]
        di = 0.0
        den = (y_lo - 2 * y_c + y_hi)
        if den > 1e-9:
            di = 0.5 * (y_lo - y_hi) / den
        bin_center = lo_bin + i + di
        center_hz = (bin_center + 3.5 * nfos) * df
        # 时间抛物线插值
        j_lo = max(0, j - 1)
        j_hi = min(max_j, j + 1)
        y_lo, y_c, y_hi = ratio_s[j_lo, i], ratio_s[j, i], ratio_s[j_hi, i]
        dj = 0.0
        den = (y_lo - 2 * y_c + y_hi)
        if den > 1e-9:
            dj = 0.5 * (y_lo - y_hi) / den
        time_offset_s = (j + dj) * nstep / sr
        # SNR 估计：sync 比 → dB（粗略标定，仅用于候选排序）
        snr_db = 10.0 * math.log10(v) if v > 0 else -99.0
        out.append({
            "center_hz": float(center_hz),
            "time_offset_s": float(time_offset_s),
            "sync_energy": float(matched[j, i]),
            "sync_ratio": v,
            "snr_est_db": float(snr_db),
        })
    return out


def _costas_complex_energy(x: np.ndarray, sr: float, f_center: float,
                          t0: float, sps: int) -> tuple:
    """复数 Costas 匹配滤波器能量（sync8d.f90:33-47）。

    对 21 个同步符号（3 块 × 7 Costas），在期望音调处做复数相关 z_n，
    返回 (|z_n|² 之和, 三块复相关和 [Z1,Z2,Z3])。z_n 已在期望音调处
    下变频，故 Z_b 的相位只含残余频偏与公共初相。
    """
    i0_base = int(round(t0 * sr))
    total = 0.0
    z_blocks = [0j, 0j, 0j]
    for bi, blk in enumerate(_SYNC_BLOCK_STARTS):
        z_sum = 0j
        for n in range(7):
            tone = ICOS7[n]
            f_tone = f_center + (tone - 3.5) * TONE_SPACING_HZ
            i0 = i0_base + (blk + n) * sps
            seg = x[i0:i0 + sps]
            if len(seg) < sps // 2:
                continue
            tt = np.arange(len(seg)) / sr
            z = complex(np.sum(seg * np.exp(-2j * np.pi * f_tone * tt)))
            z_sum += z
            total += z.real * z.real + z.imag * z.imag
        z_blocks[bi] = z_sum
    return total, z_blocks


def three_stage_sync(samples: List[float], sample_rate: float,
                     coarse_candidate: Dict) -> Dict:
    """三段精同步。

    第一段：在粗峰附近用复数匹配滤波器细扫频率/时间（ft8b.f90:111-134）。
    第二段：三块复相关相位差 → 残余频偏 mHz 级估计。
    第三段：校正后积分 79×8 路符号能量矩阵。
    """
    x = np.asarray(samples, dtype=np.float64)
    sr = float(sample_rate)
    sps = _sps(sr)
    f0 = float(coarse_candidate["center_hz"])
    t0 = float(coarse_candidate["time_offset_s"])

    # ---- 第一段：±3Hz / ±0.5 符号细扫 ----
    best = None
    df_trial_list = np.arange(-3.0, 3.001, 0.25)
    dt_trial_list = np.arange(-0.08, 0.0801, 0.02)
    for dt in dt_trial_list:
        for dft in df_trial_list:
            e, _ = _costas_complex_energy(x, sr, f0 + dft, t0 + dt, sps)
            if best is None or e > best[0]:
                best = (e, f0 + dft, t0 + dt)
    _, f_ref, t_ref = best

    # ---- 第二段：相位差残余频偏 ----
    _, z_blocks = _costas_complex_energy(x, sr, f_ref, t_ref, sps)
    df_res = 0.0
    if all(abs(z) > 1e-6 for z in z_blocks):
        dt12 = _SYNC_BLOCK_CENTER_S[1] - _SYNC_BLOCK_CENTER_S[0]
        dt23 = _SYNC_BLOCK_CENTER_S[2] - _SYNC_BLOCK_CENTER_S[1]
        p12 = math.atan2((z_blocks[1] * z_blocks[0].conjugate()).imag,
                         (z_blocks[1] * z_blocks[0].conjugate()).real)
        p23 = math.atan2((z_blocks[2] * z_blocks[1].conjugate()).imag,
                         (z_blocks[2] * z_blocks[1].conjugate()).real)
        df_res = 0.5 * (p12 / (2.0 * math.pi * dt12)
                        + p23 / (2.0 * math.pi * dt23))
        # 相位差包裹导致整周模糊：限制在细扫步长内
        if abs(df_res) > 3.0:
            df_res = 0.0
        f_ref += df_res

    # ---- 第三段：79×8 路符号能量矩阵 ----
    energies = np.zeros((NN, 8), dtype=np.float64)
    i0_base = int(round(t_ref * sr))
    for k in range(NN):
        i0 = i0_base + k * sps
        seg = x[i0:i0 + sps]
        if len(seg) < sps // 2:
            continue
        tt = np.arange(len(seg)) / sr
        # 8 音调复相关：一次性 (8, len(seg)) 相位矩阵
        tones = np.arange(8)
        f_tones = f_ref + (tones - 3.5) * TONE_SPACING_HZ
        phase = -2j * np.pi * f_tones[:, None] * tt[None, :]
        z = seg[None, :] @ np.exp(phase).T.conj()   # (8,)
        energies[k] = z.real * z.real + z.imag * z.imag

    # SNR：21 个同步符号上"期望音调能量 / 邻音调平均能量"
    sig = 0.0
    noi = 0.0
    nsync = 0
    for bi, blk in enumerate(_SYNC_BLOCK_STARTS):
        for n in range(7):
            k = blk + n
            if k >= NN:
                continue
            tone = ICOS7[n]
            row = energies[k]
            sig += row[tone]
            others = (row.sum() - row[tone]) / 7.0
            noi += others
            nsync += 1
    snr_db = 10.0 * math.log10(sig / noi) if noi > 0 and sig > 0 else -99.0

    return {
        "center_hz": float(f_ref),
        "time_offset_s": float(t_ref),
        "residual_freq_hz": float(df_res),
        "energies_79": energies,
        "snr_est_db": float(snr_db),
        "sync_energy": float(coarse_candidate.get("sync_energy", 0.0)),
    }


# ---------------------------------------------------------------------------
# 合成信号：用 ft8_encode 真实编码生成信号（加频偏+噪声），供同步件测试使用
# ---------------------------------------------------------------------------
def _synthesize_ft8_audio(text: str, center_hz: float = 1500.0,
                          f_offset_hz: float = 3.7, t_offset_s: float = 0.5,
                          sigma_noise: float = 0.15, sr: int = 12000,
                          seed: int = 0) -> tuple:
    """用 ft8_encode 真实 79 音调生成实信号（不手写假音调序列）。

    返回 (samples_list, true_center_hz)。
    """
    from mbdsdr_ai.ft8_encode import encode_ft8_text
    tones = encode_ft8_text(text)
    sps = int(round(sr * 0.16))
    n_total = int(round(15.0 * sr))
    x = np.zeros(n_total, dtype=np.float64)
    i0_off = int(round(t_offset_s * sr))
    for k, tone in enumerate(tones):
        f = center_hz + (tone - 3.5) * TONE_SPACING_HZ + f_offset_hz
        i0 = i0_off + k * sps
        if i0 + sps > n_total:
            break
        tt = np.arange(sps) / sr
        x[i0:i0 + sps] += np.cos(2.0 * np.pi * f * tt)
    rng = np.random.default_rng(seed)
    x += rng.normal(0.0, sigma_noise, n_total)
    return x.tolist(), center_hz + f_offset_hz


# 端到端自测（合成 → decode_ft8_audio）已迁到 tests/test_ft8_e2e.py：
# 它必须 import ft8_lite，而 ft8_lite 依赖本模块——留在库内就是一条
# 「用函数体内 import 把环藏起来」的边（D1）。

