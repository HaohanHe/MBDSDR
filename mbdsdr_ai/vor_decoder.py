"""
MBDSDR VOR（VHF Omnidirectional Range，甚高频全向信标）编解码器
=====================================================================

本模块实现 VOR 复合基带信号（即 VOR 接收机鉴频/包络检波之后的音频）的
合成与解码。VOR 是 ICAO Annex 10 定义的民航近程无线电导航系统，工作在
108–118 MHz 频段；射频载波不在本模块范围内，本模块处理的是解调之后的
复合音频。

信号构成（ICAO Annex 10 Vol I, 标准 VOR 信号格式）：
  1) 30 Hz 可变信号（Variable）：对载波做 30% 调幅，其相位随飞机相对
     台站的磁方位角旋转；
  2) 30 Hz 基准信号（Reference）：先对 9960 Hz 副载波做 30 Hz 调频
     （频偏 ±480 Hz，调制指数 16），再以约 30% 幅度叠加到载波上；基准
     30 Hz 相位固定不随方位变化；
  3) 莫尔斯识别音：1020 Hz 音调，以约 7–10 WPM 发送台站呼号，穿插在
     调制间隙。

方位角由两个 30 Hz 分量的相位差给出：
    磁方位角 = 相位(可变 30 Hz) - 相位(基准 30 Hz)   （0–360°）

参考来源：
  - ICAO Annex 10 Volume I, 2.1.3 节 VOR 信号格式（9960 Hz 副载波、
    ±480 Hz 频偏、30 Hz 基准/可变分量）。
  - 莫尔斯码表与点划时长比例参考 repos/direwolf/src/morse.c:61-120
    （标准国际摩尔斯码，dit:dash:gap = 1:3:1/3/7）。
  - 30 Hz 带通 + 鉴频 + 单频相位估计的整体思路参考 GNURadio 中
    gr-analog 的 PLL/鉴频块（repos/gnuradio/gr-analog/python/analog/），
    本实现用 numpy/scipy 直接做离线处理，不依赖 GNU Radio 运行时。

注意：本模块只处理合成/离线信号，不读取任何真实射频硬件，也不伪造
真实接收数据。测试中的方位角与呼号均为虚构值。

MBDSDR Project - AI定义无线电 - GPL-3.0
"""

from __future__ import annotations

import math
from typing import Dict, Optional

import numpy as np
from scipy import signal as sps

# ---------------------------------------------------------------------------
# 系统常量（ICAO Annex 10 标准值）
# ---------------------------------------------------------------------------
REF_MOD_FREQ = 30.0          # 基准/可变 30 Hz 调制频率 (Hz)
SUBCARRIER_FREQ = 9960.0      # 基准副载波中心频率 (Hz)
SUBCARRIER_DEVIATION = 480.0  # 副载波频偏 ±480 Hz
MORSE_TONE_FREQ = 1020.0      # 莫尔斯识别音调 (Hz)
DEFAULT_WPM = 12.0            # 默认莫尔斯发送速度（字/分）

# 莫尔斯码表（与 repos/direwolf/src/morse.c:64-120 一致，仅保留大写字母+数字）
MORSE_TABLE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E",
    "..-.": "F", "--.": "G", "....": "H", "..": "I", ".---": "J",
    "-.-": "K", ".-..": "L", "--": "M", "-.": "N", "---": "O",
    ".--.": "P", "--.-": "Q", ".-.": "R", "...": "S", "-": "T",
    "..-": "U", "...-": "V", ".--": "W", "-..-": "X", "-.--": "Y",
    "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
}
MORSE_REVERSE = {v: k for k, v in MORSE_TABLE.items()}


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _bandpass_sos(fs: float, low: float, high: float, order: int = 4):
    """设计 Butterworth 带通滤波器，返回 second-order sections。

    用 SOS（二阶节联级）而不是直接 (b,a)：20–40 Hz 这种极低频带在
    fs=44100 下直接型会数值发散，SOS 稳定得多。
    """
    nyq = 0.5 * fs
    return sps.butter(order, [low / nyq, high / nyq], btype="band", output="sos")


def _goertzel_phase(x: np.ndarray, fs: float, f0: float) -> float:
    """对 x 做 f0 频率处的单频 DFT，返回相位（弧度，-π..π）与幅度。

    等价于在 f0 频点做相干积分：对长时观测，噪声被平均掉，相位估计
    非常稳。合成信号里 30 Hz 是精确频率，所以直接用此频点。
    """
    n = len(x)
    t = np.arange(n) / fs
    ref_cos = np.cos(2.0 * np.pi * f0 * t)
    ref_sin = np.sin(2.0 * np.pi * f0 * t)
    # 复数相关：X = Σ x·e^{-jωt} = I - jQ。
    # 对 x=cos(ωt+φ)，X ≈ (N/2)·e^{jφ}，故相位 φ = atan2(-Q, I)。
    I = np.dot(x, ref_cos)
    Q = np.dot(x, ref_sin)
    mag = math.hypot(I, Q)
    phase = math.atan2(-Q, I)
    return phase, mag


def _wrap_deg(deg: float) -> float:
    """把角度归一到 [0, 360)。"""
    d = deg % 360.0
    if d < 0:
        d += 360.0
    return d


# ---------------------------------------------------------------------------
# 编码器
# ---------------------------------------------------------------------------
def _morse_samples(morse_id: str, fs: float, wpm: float) -> np.ndarray:
    """把呼号字符串编码成 1020 Hz 音调的采样序列（含首尾静音）。

    时长比例参考 repos/direwolf/src/morse.c:60 的 TIME_UNITS_TO_MS：
    单位点长 unit_ms = 1200 / wpm。
    """
    unit = 1.0 / wpm * 1.2  # PARIS 字元模型：1 点 = 1.2/wpm 秒
    spb = int(round(unit * fs))  # 每单位采样数

    samples = []
    # 起始静音 0.5 个字元
    samples.append(np.zeros(int(0.5 * spb)))
    for ci, ch in enumerate(morse_id.upper()):
        code = MORSE_REVERSE.get(ch)
        if code is None:
            continue
        for sym_i, sym in enumerate(code):
            dur = spb if sym == "." else 3 * spb
            t = np.arange(dur) / fs
            samples.append(np.sin(2.0 * np.pi * MORSE_TONE_FREQ * t))
            # 字符内点划间隔 1 单位
            if sym_i < len(code) - 1:
                samples.append(np.zeros(spb))
        # 字符间间隔 3 单位（已在末尾含 1 单位字符内，这里补 2）
        samples.append(np.zeros(2 * spb))
    samples.append(np.zeros(int(0.5 * spb)))  # 收尾静音
    return np.concatenate(samples)


def vor_encode(bearing_deg: float, morse_id: str, sample_rate: float) -> np.ndarray:
    """合成 VOR 复合基带信号。

    参数：
        bearing_deg: 磁方位角（0–360 度），决定可变 30 Hz 相对基准的相位。
        morse_id:    莫尔斯识别呼号（大写字母/数字），叠加 1020 Hz 音调。
        sample_rate: 采样率（Hz），建议 ≥ 8000。

    返回：
        np.ndarray，float32，归一化到 [-1, 1] 的复合音频。
    """
    fs = float(sample_rate)
    bearing = math.radians(bearing_deg)

    # 先生成莫尔斯片段，决定最短时长
    morse = _morse_samples(morse_id, fs, DEFAULT_WPM)
    morse_dur = len(morse) / fs
    # 方位测量至少需要 2 秒（60 个 30 Hz 周期）
    duration = max(2.5, morse_dur + 0.5)
    n = int(round(duration * fs))
    t = np.arange(n) / fs

    # (1) 可变 30 Hz：cos(2π·30·t + bearing)，幅度 0.3
    var_sig = 0.30 * np.cos(2.0 * np.pi * REF_MOD_FREQ * t + bearing)

    # (2) 基准 9960 Hz FM 副载波：相位 = 2π·9960·t + (Δf/fm)·sin(2π·30·t)
    #     瞬时频偏 = Δf·cos(2π·30·t)，基准 30 Hz 相位固定为 0。
    mod_idx = SUBCARRIER_DEVIATION / REF_MOD_FREQ  # = 16
    sub_phase = (2.0 * np.pi * SUBCARRIER_FREQ * t
                 + mod_idx * np.sin(2.0 * np.pi * REF_MOD_FREQ * t))
    ref_sig = 0.30 * np.cos(sub_phase)

    # (3) 复合（莫尔斯放到与可变/基准同长度的时间轴上，从 0.1 s 后开始）
    morse_sig = np.zeros(n)
    start = int(0.1 * fs)
    end = min(n, start + len(morse))
    morse_sig[start:end] = 0.20 * morse[: end - start]

    x = var_sig + ref_sig + morse_sig
    # 归一化，避免削波
    peak = np.max(np.abs(x))
    if peak > 1.0:
        x = x / peak
    return x.astype(np.float32)


# ---------------------------------------------------------------------------
# 解码器
# ---------------------------------------------------------------------------
def _fm_discriminator(bandpassed: np.ndarray, fs: float) -> np.ndarray:
    """对带通后的单频 FM 信号做鉴频：解析信号相位差分。

    返回瞬时频偏（Hz）序列，长度比输入短 1。
    """
    analytic = sps.hilbert(bandpassed)
    phase = np.unwrap(np.angle(analytic))
    inst_freq = np.diff(phase) * fs / (2.0 * np.pi)
    return inst_freq


def _decode_morse(band1020: np.ndarray, fs: float) -> str:
    """对 1020 Hz 带通后的信号做包络检测 → 点划识别 → 文本映射。"""
    analytic = sps.hilbert(band1020)
    env = np.abs(analytic)
    # 平滑包络
    win = max(3, int(0.005 * fs))  # 5 ms 窗
    kernel = np.ones(win) / win
    env = np.convolve(env, kernel, mode="same")

    if env.max() < 1e-3:
        return ""

    # 自适应阈值：取包络最大值的 30% 作为门限
    thr = 0.3 * env.max()
    on = env > thr

    # 切分段：(是否按键, 采样数)
    segs = []
    cur = on[0]
    cnt = 1
    for v in on[1:]:
        if v == cur:
            cnt += 1
        else:
            segs.append((cur, cnt))
            cur = v
            cnt = 1
    segs.append((cur, cnt))

    # 估计单位点长：最短的"开"段就是一个点（1 单位）。
    # 用中位数会被长划拉偏（点:划 = 1:3），这里取最小值。
    on_lens = [c for (o, c) in segs if o]
    if not on_lens:
        return ""
    unit = float(min(on_lens))

    # 解析莫尔斯：逐段读取
    out_chars = []
    cur_symbols = []
    for (o, c) in segs:
        dur = c / unit  # 以单位长归一
        if o:
            # 按键：<2 单位为点，否则划
            cur_symbols.append("." if dur < 2.0 else "-")
        else:
            # 静音：<1.5 字符内，1.5–5 字符间，>=5 字间
            if 1.5 <= dur < 5.0:
                # 字符结束
                if cur_symbols:
                    out_chars.append(MORSE_TABLE.get("".join(cur_symbols), "?"))
                    cur_symbols = []
            elif dur >= 5.0:
                if cur_symbols:
                    out_chars.append(MORSE_TABLE.get("".join(cur_symbols), "?"))
                    cur_symbols = []
                out_chars.append(" ")
    if cur_symbols:
        out_chars.append(MORSE_TABLE.get("".join(cur_symbols), "?"))
    return "".join(out_chars).strip()


def vor_decode(audio: np.ndarray, sample_rate: float) -> Dict:
    """解码 VOR 复合基带信号。

    参数：
        audio:       一维实数采样（复合基带音频）。
        sample_rate: 采样率。

    返回 dict：
        bearing:     磁方位角（度，0–360），测量失败为 None。
        morse_code:  识别码文本（未检出为空串）。
        confidence:  0.0–1.0，方位/莫尔斯整体置信度。
    """
    fs = float(sample_rate)
    x = np.asarray(audio, dtype=np.float64)
    result = {"bearing": None, "morse_code": "", "confidence": 0.0}

    if len(x) < int(fs * 0.5):
        return result

    # ---- 通道 1：可变 30 Hz（直接带通） ----
    sos30 = _bandpass_sos(fs, 20.0, 40.0, order=4)
    var_filt = sps.sosfiltfilt(sos30, x)
    var_phase, var_mag = _goertzel_phase(var_filt, fs, REF_MOD_FREQ)

    # ---- 通道 2：基准 9960 Hz FM 副载波 → 鉴频 → 30 Hz ----
    sos_sub = _bandpass_sos(fs, 9300.0, 10600.0, order=4)
    sub_filt = sps.sosfiltfilt(sos_sub, x)
    inst_freq = _fm_discriminator(sub_filt, fs)
    # 鉴频输出里包含 9960 中心（直流）和 30 Hz 频偏；再次带通 30 Hz
    ref_filt = sps.sosfiltfilt(sos30, inst_freq)
    ref_phase, ref_mag = _goertzel_phase(ref_filt, fs, REF_MOD_FREQ)

    # ---- 方位角 = 可变相位 - 基准相位 ----
    if var_mag > 1e-6 and ref_mag > 1e-6:
        diff = math.degrees(var_phase - ref_phase)
        bearing = _wrap_deg(diff)
        result["bearing"] = bearing

    # ---- 通道 3：莫尔斯 1020 Hz ----
    sos_m = _bandpass_sos(fs, 950.0, 1100.0, order=4)
    morse_filt = sps.sosfiltfilt(sos_m, x)
    result["morse_code"] = _decode_morse(morse_filt, fs)

    # ---- 置信度：两个 30 Hz 通道的"信号/邻频能量"比 ----
    def _snr_ratio(sig: np.ndarray, f0: float) -> float:
        p, mag = _goertzel_phase(sig, fs, f0)
        rms = math.sqrt(np.mean(sig ** 2)) + 1e-9
        # mag 是相干积分幅度，n 越大越大；用 mag/n 作为该频点幅度估计
        peak = mag / len(sig)
        return peak / rms

    var_snr = _snr_ratio(var_filt, REF_MOD_FREQ)
    ref_snr = _snr_ratio(ref_filt, REF_MOD_FREQ)
    # 归一化到 0..1（经验门限）
    c_var = min(1.0, var_snr / 3.0)
    c_ref = min(1.0, ref_snr / 3.0)
    morse_present = 1.0 if result["morse_code"] else 0.3
    result["confidence"] = round(float(0.6 * (c_var + c_ref) / 2.0
                                       + 0.4 * morse_present), 3)
    return result
