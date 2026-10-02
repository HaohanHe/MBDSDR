# SPDX-License-Identifier: MIT
"""前向纠错（FEC）统一接口 — 依据 CCSDS 等公开标准独立实现。

实现依据（公开标准；SatDump 仅作技术参考，本仓未包含其源代码）：
  - RS(255,223)（RS223），32 校验字节，可纠 16 字节错误（CCSDS TM 标准）
  - encode/decode(data, ccsds)，ccsds=True 时做符号反转
  - CCSDS 扰码 PN 字节表，首字节 0xff，周期 255 字节
  - QPSK 差分解码：相邻符号 I/Q 异或

本模块提供纯 Python、确定性实现：
  - :class:`ReedSolomon` — RS(255,223) CCSDS 编码/解码（Berlekamp-Massey + Forney）
  - :class:`Scrambler`   — CCSDS 扰码/解扰（查表，与 CCSDS 标准一致）
  - :class:`DifferentialEncoder` — DBPSK/DQPSK 差分编/解码

红线：不依赖任何 C 扩展；所有算法对已知向量可逆。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# CCSDS 字节级伪随机序列（SatDump randomization.cpp:4-36，逐字节照抄）
# ---------------------------------------------------------------------------
CCSDS_PN: bytes = bytes([
    0xff, 0x48, 0x0e, 0xc0, 0x9a, 0x0d, 0x70, 0xbc,
    0x8e, 0x2c, 0x93, 0xad, 0xa7, 0xb7, 0x46, 0xce,
    0x5a, 0x97, 0x7d, 0xcc, 0x32, 0xa2, 0xbf, 0x3e,
    0x0a, 0x10, 0xf1, 0x88, 0x94, 0xcd, 0xea, 0xb1,
    0xfe, 0x90, 0x1d, 0x81, 0x34, 0x1a, 0xe1, 0x79,
    0x1c, 0x59, 0x27, 0x5b, 0x4f, 0x6e, 0x8d, 0x9c,
    0xb5, 0x2e, 0xfb, 0x98, 0x65, 0x45, 0x7e, 0x7c,
    0x14, 0x21, 0xe3, 0x11, 0x29, 0x9b, 0xd5, 0x63,
    0xfd, 0x20, 0x3b, 0x02, 0x68, 0x35, 0xc2, 0xf2,
    0x38, 0xb2, 0x4e, 0xb6, 0x9e, 0xdd, 0x1b, 0x39,
    0x6a, 0x5d, 0xf7, 0x30, 0xca, 0x8a, 0xfc, 0xf8,
    0x28, 0x43, 0xc6, 0x22, 0x53, 0x37, 0xaa, 0xc7,
    0xfa, 0x40, 0x76, 0x04, 0xd0, 0x6b, 0x85, 0xe4,
    0x71, 0x64, 0x9d, 0x6d, 0x3d, 0xba, 0x36, 0x72,
    0xd4, 0xbb, 0xee, 0x61, 0x95, 0x15, 0xf9, 0xf0,
    0x50, 0x87, 0x8c, 0x44, 0xa6, 0x6f, 0x55, 0x8f,
    0xf4, 0x80, 0xec, 0x09, 0xa0, 0xd7, 0x0b, 0xc8,
    0xe2, 0xc9, 0x3a, 0xda, 0x7b, 0x74, 0x6c, 0xe5,
    0xa9, 0x77, 0xdc, 0xc3, 0x2a, 0x2b, 0xf3, 0xe0,
    0xa1, 0x0f, 0x18, 0x89, 0x4c, 0xde, 0xab, 0x1f,
    0xe9, 0x01, 0xd8, 0x13, 0x41, 0xae, 0x17, 0x91,
    0xc5, 0x92, 0x75, 0xb4, 0xf6, 0xe8, 0xd9, 0xcb,
    0x52, 0xef, 0xb9, 0x86, 0x54, 0x57, 0xe7, 0xc1,
    0x42, 0x1e, 0x31, 0x12, 0x99, 0xbd, 0x56, 0x3f,
    0xd2, 0x03, 0xb0, 0x26, 0x83, 0x5c, 0x2f, 0x23,
    0x8b, 0x24, 0xeb, 0x69, 0xed, 0xd1, 0xb3, 0x96,
    0xa5, 0xdf, 0x73, 0x0c, 0xa8, 0xaf, 0xcf, 0x82,
    0x84, 0x3c, 0x62, 0x25, 0x33, 0x7a, 0xac, 0x7f,
    0xa4, 0x07, 0x60, 0x4d, 0x06, 0xb8, 0x5e, 0x47,
    0x16, 0x49, 0xd6, 0xd3, 0xdb, 0xa3, 0x67, 0x2d,
    0x4b, 0xbe, 0xe6, 0x19, 0x51, 0x5f, 0x9f, 0x05,
    0x08, 0x78, 0xc4, 0x4a, 0x66, 0xf5, 0x58,
])
assert len(CCSDS_PN) == 255


# ===========================================================================
# Reed-Solomon over GF(256)
# ===========================================================================

class _GF256:
    """GF(256) 算术表。

    参数
    ----------
    prim_poly : int
        8 次本原多项式（含 x^8 项共 9 位）。CCSDS 用 0x187
        （x^8+x^7+x^2+x+1）。
    """

    def __init__(self, prim_poly: int = 0x187):
        self.prim = prim_poly & 0xFF
        self.exp = [0] * 512
        self.log = [0] * 256
        x = 1
        for i in range(255):
            self.exp[i] = x
            self.log[x] = i
            x <<= 1
            if x & 0x100:
                x ^= self.prim | 0x100  # prim includes x^8
        # 扩展 exp 表方便乘法不做模
        for i in range(255, 512):
            self.exp[i] = self.exp[i - 255]

    def mul(self, a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return self.exp[self.log[a] + self.log[b]]

    def div(self, a: int, b: int) -> int:
        if a == 0:
            return 0
        if b == 0:
            raise ZeroDivisionError
        return self.exp[(self.log[a] - self.log[b]) % 255]

    def pow(self, a: int, n: int) -> int:
        if a == 0:
            return 0 if n > 0 else 1
        return self.exp[(self.log[a] * n) % 255]

    def inverse(self, a: int) -> int:
        if a == 0:
            raise ZeroDivisionError
        return self.exp[(255 - self.log[a]) % 255]


# ---------------------------------------------------------------------------
# 多项式工具（高次在前）
# ---------------------------------------------------------------------------
def _poly_add(gf: _GF256, p: List[int], q: List[int]) -> List[int]:
    """两个 GF(256) 多项式相加（XOR），高次在前。"""
    n = max(len(p), len(q))
    p = [0] * (n - len(p)) + p
    q = [0] * (n - len(q)) + q
    return [a ^ b for a, b in zip(p, q)]


def _poly_mul(gf: _GF256, p: List[int], q: List[int]) -> List[int]:
    """两个 GF(256) 多项式相乘，高次在前。"""
    if not p or not q:
        return []
    out = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        if a == 0:
            continue
        for j, b in enumerate(q):
            out[i + j] ^= gf.mul(a, b)
    return out


@dataclass
class RSResult:
    data: bytes            # 纠正后的数据（k 字节）
    nerrors: int            # 纠正的符号数（-1 = 不可纠正）
    corrected: bool        # 是否发生纠正


class ReedSolomon:
    """RS(255, 223) CCSDS 风格编/解码。

    参数
    ----------
    nsym : int
        校验字节数（默认 32 → RS(255,223)，可纠 16 字节）。
    fcr : int
        生成多项式根的首指数（CCSDS = 112）。
    prim : int
        根序列指数步进：根 = alpha^(prim*(fcr+i))。标准 CCSDS prim=1；
        fsphil SSDV 用 prim=11（根基 gamma=alpha^11）。
    prim_poly : int
        GF(256) 本原多项式。
    ccsds_invert : bool
        CCSDS 惯例：编/解码前把每个字节 ^= 0xFF（符号反转）。
    """

    def __init__(self, nsym: int = 32, fcr: int = 112, prim: int = 1,
                 prim_poly: int = 0x187, ccsds_invert: bool = True):
        self.nsym = int(nsym)
        self.k = 255 - self.nsym
        self.fcr = int(fcr)
        self.prim = int(prim)
        # IPRIM：prim 在模 255 下的逆元（prim*iprim ≡ 1 mod 255）。
        # 根序列步进 prim≠1 时（如 fsphil SSDV 的 prim=11），Chien 位置与
        # 差错特征值需按 IPRIM/prim 重新映射；prim=1 时 iprim=1，退化为标准 CCSDS。
        self.iprim = pow(self.prim, -1, 255)
        self.ccsds_invert = bool(ccsds_invert)
        self.gf = _GF256(prim_poly)
        # 生成多项式 g(x) = prod_{i=0..nsym-1} (x - alpha^(prim*(fcr+i)))
        self.gen: List[int] = [1]
        for i in range(self.nsym):
            # 乘 (x + alpha^(prim*(fcr+i)))
            root = self.gf.pow(2, self.prim * (self.fcr + i))
            new_g = [0] * (len(self.gen) + 1)
            for j in range(len(self.gen)):
                new_g[j] ^= self.gf.mul(self.gen[j], root)
                new_g[j + 1] ^= self.gen[j]
            self.gen = new_g

    # ------------------------------------------------------------------
    # 编码
    # ------------------------------------------------------------------
    def encode(self, data: bytes) -> bytes:
        """对 k 字节数据编码，返回 255 字节码字（含校验）。"""
        if len(data) != self.k:
            raise ValueError(f"RS 编码需要 {self.k} 字节输入，收到 {len(data)}")
        msg = bytearray(data)
        if self.ccsds_invert:
            msg = bytearray(b ^ 0xFF for b in msg)
        # 系统码：消息左移 nsym，再除以生成多项式
        msg = list(msg) + [0] * self.nsym
        for i in range(self.k):
            coef = msg[i]
            if coef:
                for j in range(self.nsym + 1):
                    msg[i + j] ^= self.gf.mul(self.gen[j], coef)
        parity = bytes(msg[self.k:])
        if self.ccsds_invert:
            parity = bytes(b ^ 0xFF for b in parity)
        return bytes(data) + parity

    # ------------------------------------------------------------------
    # 解码（参考 Wikiversity RS 教程：BM + Chien + Forney，低次在前）
    # ------------------------------------------------------------------
    def decode(self, codeword: bytes) -> RSResult:
        """对 255 字节码字解码，返回纠正后的 k 字节数据。"""
        if len(codeword) != 255:
            raise ValueError(f"RS 解码需要 255 字节，收到 {len(codeword)}")
        g = self.gf
        n = 255
        rcvd = list(codeword)
        if self.ccsds_invert:
            rcvd = [b ^ 0xFF for b in rcvd]

        # 1. 伴随式 synd[i] = r(alpha^(prim*(fcr+i)))（低次在前 synd[0]=S0）
        synd = [0] * self.nsym
        for i in range(self.nsym):
            root = g.pow(2, self.prim * (self.fcr + i))
            s = 0
            for byte in rcvd:
                s = g.mul(s, root) ^ byte
            synd[i] = s
        if not any(synd):
            out = bytes(b ^ 0xFF for b in rcvd[:self.k]) if self.ccsds_invert else bytes(rcvd[:self.k])
            return RSResult(data=out, nerrors=0, corrected=False)

        # 2. Berlekamp-Massey（低次在前）
        C = [1]
        B = [1]
        L = 0
        m = 1
        b = 1
        for N in range(self.nsym):
            d = synd[N]
            for i in range(1, L + 1):
                d ^= g.mul(C[i], synd[N - i])
            if d == 0:
                m += 1
            elif 2 * L <= N:
                T = C[:]
                coef = g.div(d, b)
                # C = C - coef * x^m * B
                need = len(B) + m
                if len(C) < need:
                    C = C + [0] * (need - len(C))
                for i in range(len(B)):
                    C[i + m] ^= g.mul(coef, B[i])
                L = N + 1 - L
                B = T
                b = d
                m = 1
            else:
                coef = g.div(d, b)
                need = len(B) + m
                if len(C) < need:
                    C = C + [0] * (need - len(C))
                for i in range(len(B)):
                    C[i + m] ^= g.mul(coef, B[i])
                m += 1

        # 3. Chien search：C(alpha^j)=0 → 差错特征值 X_k^-1=alpha^j。
        #    字节位置 pos = (iprim*j - 1) mod n（prim=1 时退化为 (j-1) mod n）。
        err_pos: List[int] = []
        for j in range(n):
            val = 0
            xn = 1
            for i in range(len(C)):
                val ^= g.mul(C[i], xn)
                xn = g.mul(xn, g.pow(2, j))
            if val == 0:
                err_pos.append((self.iprim * j - 1) % n)
        n_err = len(err_pos)
        if n_err == 0 or n_err > self.nsym // 2:
            out = bytes(b ^ 0xFF for b in rcvd[:self.k]) if self.ccsds_invert else bytes(rcvd[:self.k])
            return RSResult(data=out, nerrors=-1, corrected=False)

        # 4. Forney：Omega(x)=S(x)Lambda(x) mod x^nsym；
        #    e_k = Omega(X_k^-1)/Lambda'(X_k^-1) * X_k^(1-fcr)
        omega = [0] * self.nsym
        for i in range(self.nsym):
            acc = 0
            for j in range(min(i + 1, len(C))):
                acc ^= g.mul(synd[i - j], C[j])
            omega[i] = acc
        deriv = [0] * max(0, len(C) - 1)
        for i in range(1, len(C)):
            if i % 2 == 1:
                deriv[i - 1] = C[i]

        out = list(rcvd)
        for pos in err_pos:
            d = n - 1 - pos                 # 该字节对应的多项式次数
            # 差错特征值：prim≠1 时按 prim 拉伸（X_k = alpha^(prim*d)）
            Xk = g.pow(2, self.prim * d)    # 差错特征值
            Xk_inv = g.inverse(Xk)
            num = 0
            xn = 1
            for i in range(len(omega)):
                num ^= g.mul(omega[i], xn)
                xn = g.mul(xn, Xk_inv)
            den = 0
            xn = 1
            for i in range(len(deriv)):
                den ^= g.mul(deriv[i], xn)
                xn = g.mul(xn, Xk_inv)
            if den == 0:
                out = bytes(b ^ 0xFF for b in rcvd[:self.k]) if self.ccsds_invert else bytes(rcvd[:self.k])
                return RSResult(data=out, nerrors=-1, corrected=False)
            magnitude = g.div(num, den)
            magnitude = g.mul(magnitude, g.pow(Xk, 1 - self.fcr))
            out[pos] ^= magnitude

        data = bytes(out[:self.k])
        if self.ccsds_invert:
            data = bytes(b ^ 0xFF for b in data)
        return RSResult(data=data, nerrors=n_err, corrected=True)


# ===========================================================================
# CCSDS 扰码
# ===========================================================================

class Scrambler:
    """CCSDS 同步扰码/解扰（查表，与 SatDump ``randomization.cpp:4-36`` 一致）。

    扰码与解扰是同一操作：``out[i] = in[i] ^ pn[i % 255]``。
    """

    def __init__(self, table: bytes = CCSDS_PN):
        if len(table) != 255:
            raise ValueError("CCSDS 扰码表必须为 255 字节")
        self.table = bytes(table)

    def scramble(self, data: bytes, offset: int = 0) -> bytes:
        n = len(data)
        idx = (np.arange(n) + offset) % 255
        pn = np.frombuffer(self.table, dtype=np.uint8)
        out = np.frombuffer(data, dtype=np.uint8) ^ pn[idx]
        return out.tobytes()

    # 解扰 = 扰码（自逆）
    descramble = scramble


# ===========================================================================
# 差分编码（DBPSK / DQPSK）
# ===========================================================================

class DifferentialEncoder:
    """差分编码/解码。

    - BPSK：相邻符号异或（``out[i] = data[i] ^ data[i-1]``）。
    - QPSK：把每符号 2 个比特看成一个 0..3 的整数，相邻整数异或。

    用法::

        enc = DifferentialEncoder(order=4)
        tx = enc.encode_bits(bits)          # 编码（追加参考比特）
        rx_bits = enc.decode_bits(rx_symbols)
    """

    def __init__(self, order: int = 2):
        if order not in (2, 4):
            raise ValueError("order 仅支持 2 (BPSK) 或 4 (QPSK)")
        self.order = int(order)
        self.bits_per_symbol = 1 if order == 2 else 2

    # ------------------------------------------------------------------
    def encode_bits(self, bits: np.ndarray) -> np.ndarray:
        """把 0/1 比特流做差分编码。前缀参考符号为 0。

        ``out[i] = sym[i] ^ sym[i-1]``（``sym[-1]=0``）。
        """
        bits = np.asarray(bits, dtype=np.int8).flatten()
        bps = self.bits_per_symbol
        if len(bits) % bps != 0:
            raise ValueError(f"比特数必须是 {bps} 的倍数")
        syms = (bits.reshape(-1, bps) * (1 << np.arange(bps - 1, -1, -1))).sum(axis=1).astype(np.int64)
        ref = np.array([0], dtype=np.int64)
        all_syms = np.concatenate([ref, syms])
        diff = np.bitwise_xor(all_syms[1:], all_syms[:-1]) & (self.order - 1)
        out_bits = ((diff[:, None] >> np.arange(bps - 1, -1, -1)) & 1).flatten().astype(np.int8)
        return out_bits

    # ------------------------------------------------------------------
    def decode_bits(self, bits: np.ndarray) -> np.ndarray:
        """差分解码：累积异或还原原始符号。

        编码端 ``out[i] = sym[i] ^ sym[i-1]``（``sym[-1]=0``），
        解码端 ``sym[i] = out[0] ^ out[1] ^ ... ^ out[i]``（累积异或）。
        """
        bits = np.asarray(bits, dtype=np.int8).flatten()
        bps = self.bits_per_symbol
        if len(bits) % bps != 0:
            raise ValueError(f"比特数必须是 {bps} 的倍数")
        syms = (bits.reshape(-1, bps) * (1 << np.arange(bps - 1, -1, -1))).sum(axis=1)
        # 累积异或（GF(2) 上求和）
        decoded = np.zeros_like(syms)
        acc = 0
        for i in range(len(syms)):
            acc ^= int(syms[i])
            decoded[i] = acc
        out_bits = ((decoded[:, None] >> np.arange(bps - 1, -1, -1)) & 1).flatten().astype(np.int8)
        return out_bits


__all__ = [
    "CCSDS_PN",
    "ReedSolomon",
    "RSResult",
    "Scrambler",
    "DifferentialEncoder",
]
