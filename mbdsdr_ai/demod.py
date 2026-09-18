"""
MBDSDR AI - 完整QPSK解调器
============================

实现完整的QPSK解调链路：
1. 匹配滤波（RRC脉冲成型）
2. Costas环载波恢复
3. Gardner位同步
4. 符号判决与解扰

参考实现：SatDump / gr-satellites / gnuradio
"""

import numpy as np
from typing import Tuple, Optional


# ========================================================================
# 脉冲成型与匹配滤波
# ========================================================================

def rrc_filter(num_taps: int, sps: int, beta: float = 0.35) -> np.ndarray:
    """
    根升余弦（RRC）滤波器系数。

    参数:
        num_taps: 滤波器抽头数
        sps: 每符号采样数
        beta: 滚降因子（0.2-0.5）

    返回:
        滤波器系数（归一化）
    """
    t = np.arange(num_taps) - (num_taps - 1) / 2
    t = t / sps  # 归一化到符号周期

    h = np.zeros(num_taps)
    for i, ti in enumerate(t):
        if ti == 0:
            h[i] = 1.0 + beta * (4 / np.pi - 1)
        elif abs(ti) == 1 / (4 * beta):
            h[i] = (beta / np.sqrt(2)) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * beta)) +
                (1 - 2 / np.pi) * np.cos(np.pi / (4 * beta))
            )
        else:
            num = np.sin(np.pi * ti * (1 - beta)) + 4 * beta * ti * np.cos(np.pi * ti * (1 + beta))
            den = np.pi * ti * (1 - (4 * beta * ti) ** 2)
            h[i] = num / den

    # 归一化
    h = h / np.sqrt(np.sum(h ** 2))
    return h


def matched_filter(iq: np.ndarray, num_taps: int = 101, sps: int = 4, beta: float = 0.35) -> np.ndarray:
    """
    匹配滤波（RRC）。

    参数:
        iq: 复数IQ输入
        num_taps: 滤波器抽头数
        sps: 每符号采样数
        beta: 滚降因子

    返回:
        滤波后的复数信号
    """
    h = rrc_filter(num_taps, sps, beta)
    return np.convolve(iq, h, mode='same')


# ========================================================================
# Costas环载波恢复
# ========================================================================

class CostasLoop:
    """
    QPSK Costas环载波恢复。

    二阶环路，用于恢复载波相位和频率偏移。
    """

    def __init__(self, noise_bw: float = 0.01, damping: float = 0.707):
        """
        参数:
            noise_bw: 归一化噪声带宽（0.001-0.1）
            damping: 阻尼系数（0.707为临界阻尼）
        """
        self.noise_bw = noise_bw
        self.damping = damping

        # 环路滤波器系数
        zeta = damping
        wn = noise_bw * 2 * np.pi  # 自然角频率

        # 二阶环路增益
        self.alpha = (4 * zeta * wn) / (1 + 2 * zeta * wn + wn ** 2)
        self.beta = (4 * wn ** 2) / (1 + 2 * zeta * wn + wn ** 2)

        # 状态
        self.phase = 0.0
        self.freq = 0.0

    def work(self, sample: complex) -> Tuple[complex, float]:
        """
        处理一个采样。

        参数:
            sample: 复数采样

        返回:
            (校正后采样, 相位误差)
        """
        # 载波NCO
        nco = np.exp(1j * self.phase)
        corr = sample * nco

        # QPSK相位误差检测器
        # e = sign(I)*Q - sign(Q)*I
        I = corr.real
        Q = corr.imag
        error = np.sign(I) * Q - np.sign(Q) * I

        # 环路滤波器（二阶）
        self.freq += self.beta * error
        self.phase += self.freq + self.alpha * error

        # 相位归一化
        while self.phase > np.pi:
            self.phase -= 2 * np.pi
        while self.phase < -np.pi:
            self.phase += 2 * np.pi

        return corr, error

    def work_batch(self, samples: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        批量处理采样。

        参数:
            samples: 复数采样数组

        返回:
            (校正后采样数组, 相位误差数组)
        """
        n = len(samples)
        corr = np.zeros(n, dtype=complex)
        errors = np.zeros(n)

        for i in range(n):
            corr[i], errors[i] = self.work(samples[i])

        return corr, errors


# ========================================================================
# Gardner位同步
# ========================================================================

class GardnerTimingRecovery:
    """
    Gardner定时恢复（位同步）。

    非数据辅助的位同步算法，适用于QPSK。
    """

    def __init__(self, sps: int, mu: float = 0.0, gain_mu: float = 0.05):
        """
        参数:
            sps: 每符号采样数
            mu: 初始定时偏移（0-1）
            gain_mu: 定时环增益
        """
        self.sps = sps
        self.mu = mu
        self.gain_mu = gain_mu
        self.line = np.zeros(sps + 1, dtype=complex)  # 延迟线

    def work(self, sample: complex) -> Tuple[Optional[complex], float]:
        """
        处理一个采样。

        参数:
            sample: 复数采样

        返回:
            (符号采样，无则为None; 定时误差)
        """
        # 移位延迟线
        self.line = np.roll(self.line, -1)
        self.line[-1] = sample

        # Gardner误差检测器
        # e = (y[k] - y[k-1]) * y[k-1/2].conj() 的虚部
        # 简化：用中间采样和前后采样差
        err = 0.0
        out = None

        # 当mu接近整数时输出符号
        if self.mu >= self.sps - 1:
            # 插值
            idx = int(self.mu)
            frac = self.mu - idx
            if idx < len(self.line) - 1:
                out = self.line[idx] * (1 - frac) + self.line[idx + 1] * frac

            # 计算Gardner误差
            mid_idx = idx - self.sps // 2
            if mid_idx >= 0 and idx + 1 < len(self.line):
                mid = self.line[mid_idx]
                err = np.real(out - self.line[idx + 1]) * np.imag(mid)

            # 更新定时
            self.mu -= self.sps

        self.mu += 1.0

        return out, err

    def work_batch(self, samples: np.ndarray) -> np.ndarray:
        """
        批量处理采样。

        参数:
            samples: 复数采样数组

        返回:
            符号采样数组（已降采样）
        """
        symbols = []
        for s in samples:
            out, _ = self.work(s)
            if out is not None:
                symbols.append(out)
        return np.array(symbols)


# ========================================================================
# QPSK解调器（完整链路）
# ========================================================================

class QPSKDemodulator:
    """
    完整QPSK解调器。

    链路：匹配滤波 → Costas环 → Gardner位同步 → 符号判决
    """

    def __init__(self, sps: int = 4, beta: float = 0.35, num_taps: int = 101):
        """
        参数:
            sps: 每符号采样数
            beta: RRC滚降因子
            num_taps: 匹配滤波器抽头数
        """
        self.sps = sps
        self.beta = beta
        self.num_taps = num_taps

        # 初始化模块
        self.costas = CostasLoop(noise_bw=0.01)
        self.gardner = GardnerTimingRecovery(sps=sps, gain_mu=0.05)

    def demodulate(self, iq: np.ndarray) -> np.ndarray:
        """
        完整QPSK解调。

        参数:
            iq: 原始复数IQ采样

        返回:
            解调后的符号序列（复数）
        """
        # 1. 匹配滤波
        filtered = matched_filter(iq, self.num_taps, self.sps, self.beta)

        # 2. Costas环载波恢复
        corr, _ = self.costas.work_batch(filtered)

        # 3. Gardner位同步
        symbols = self.gardner.work_batch(corr)

        # 4. 符号判决（QPSK四相位）
        # 判决到最近的星座点
        symbols = np.sign(symbols.real) + 1j * np.sign(symbols.imag)
        symbols = symbols / np.sqrt(2)

        return symbols

    def symbols_to_bits(self, symbols: np.ndarray) -> np.ndarray:
        """
        符号转比特流。

        QPSK格雷码映射：
            00 → (1,1)   01 → (-1,1)
            11 → (-1,-1) 10 → (1,-1)

        返回:
            比特数组（0/1）
        """
        bits = []
        for s in symbols:
            I = 1 if s.real > 0 else 0
            Q = 1 if s.imag > 0 else 0
            bits.append(I)
            bits.append(Q)
        return np.array(bits, dtype=np.uint8)


# ========================================================================
# 完整Viterbi解码器（K=7, rate=1/2）
# ========================================================================

class ViterbiDecoder:
    """
    完整Viterbi卷积码解码器。

    标准K=7, rate=1/2，G1=171, G2=133
    用于气象卫星LRIT/HRPT解码。
    """

    def __init__(self, K: int = 7, G1: int = 171, G2: int = 133):
        """
        参数:
            K: 约束长度
            G1: 生成多项式1（八进制）
            G2: 生成多项式2（八进制）
        """
        self.K = K
        self.n = 2 ** (K - 1)  # 状态数

        # 生成多项式（二进制）
        self.G1 = G1
        self.G2 = G2

        # 预计算状态转移
        self._build_transitions()

        # 路径度量和幸存路径
        self.pm = np.full(self.n, np.inf)
        self.pm[0] = 0
        self.survivors = []

    def _build_transitions(self):
        """预计算状态转移表。"""
        # 对于每个输入位（0/1），计算状态转移和输出
        self.next_state = np.zeros((self.n, 2), dtype=int)
        self.output = np.zeros((self.n, 2, 2), dtype=int)  # (state, input, output_bit)

        for state in range(self.n):
            for input_bit in [0, 1]:
                # 移位寄存器：state是(K-1)位移位寄存器
                reg = (state << 1) | input_bit

                # 计算G1输出
                g1 = 0
                for i in range(self.K):
                    if (self.G1 >> i) & 1:
                        g1 ^= (reg >> i) & 1

                # 计算G2输出
                g2 = 0
                for i in range(self.K):
                    if (self.G2 >> i) & 1:
                        g2 ^= (reg >> i) & 1

                # 下一状态
                next_s = state >> 1  # 右移一位
                if input_bit:
                    next_s |= (1 << (self.K - 2))

                self.next_state[state, input_bit] = next_s
                self.output[state, input_bit, 0] = g1
                self.output[state, input_bit, 1] = g2

    def _hamming_distance(self, received: np.ndarray, expected: np.ndarray) -> float:
        """汉明距离（软判决用欧氏距离）。"""
        return np.sum(np.abs(received - expected))

    def decode(self, bits: np.ndarray) -> np.ndarray:
        """
        解码比特流。

        参数:
            bits: 接收比特流（软判决为0-1间浮点数）

        返回:
            解码后信息比特
        """
        # 确保是偶数长度（每2个编码位对应1个信息位）
        n_coded = (len(bits) // 2) * 2

        # 初始化
        pm = np.full(self.n, np.inf)
        pm[0] = 0
        survivors = []

        for i in range(0, n_coded, 2):
            # 接收的两个编码位
            r = np.array([bits[i], bits[i + 1]], dtype=float)

            # 新的路径度量
            new_pm = np.full(self.n, np.inf)
            decisions = np.zeros(self.n, dtype=int)

            for state in range(self.n):
                for input_bit in [0, 1]:
                    next_s = self.next_state[state, input_bit]
                    exp = self.output[state, input_bit].astype(float)

                    # 距离
                    dist = self._hamming_distance(r, exp)
                    total = pm[state] + dist

                    if total < new_pm[next_s]:
                        new_pm[next_s] = total
                        decisions[next_s] = input_bit

            pm = new_pm
            survivors.append(decisions)

        # 回溯
        decoded = []
        state = np.argmin(pm)

        for i in range(len(survivors) - 1, -1, -1):
            bit = survivors[i][state]
            decoded.append(bit)
            state = self.next_state[state, bit]

        decoded.reverse()
        return np.array(decoded, dtype=np.uint8)


# ========================================================================
# 完整解扰器
# ========================================================================

def descramble_ccdb(bits: np.ndarray) -> np.ndarray:
    """
    CCDB（Consultative Committee for Space Data Systems）解扰。

    使用标准CCDB生成多项式：x^8 + x^7 + x^5 + x^3 + 1
    """
    # CCDB解扰序列（预计算）
    # 生成多项式：1 + x^5 + x^7 + x^8
    poly = [1, 5, 7, 8]
    state = np.zeros(8, dtype=np.uint8)

    result = bits.copy()
    for i in range(len(result)):
        # 计算解扰序列
        s = 0
        for p in poly[1:]:
            s ^= state[p - 1]

        # 异或
        result[i] ^= s

        # 移位
        state = np.roll(state, 1)
        state[0] = result[i]

    return result


def descramble_nrz_m(bits: np.ndarray) -> np.ndarray:
    """
    NRZ-M解扰（差分解码）。

    1表示电平翻转，0表示不翻转。
    """
    result = np.zeros_like(bits)
    result[0] = bits[0]
    for i in range(1, len(bits)):
        result[i] = bits[i] ^ bits[i - 1]
    return result
