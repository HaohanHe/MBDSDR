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
from typing import Iterable, List, Optional, Sequence

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
    nerrors: int            # 纠正的符号数（-1 = 不可纠正）；擦除位置不计入
    corrected: bool        # 是否发生纠正
    #: 本次解码声明的擦除数（去重后）。0 = 纯纠错解码。
    #: 判据为 2*nerrors + nerasures <= nsym；擦除已知位置，一个擦除只花
    #: 「一个符号」的预算，而盲纠一个符号要花两个——这就是纯擦除上限
    #: （32）是纯纠错上限（16）两倍的原因。
    nerasures: int = 0


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
    def decode(self, codeword: bytes,
               erasures: Optional[Iterable[int]] = None) -> RSResult:
        """对 255 字节码字解码，返回纠正后的 k 字节数据。

        参数
        ----------
        codeword : 255 字节码字（系统码：前 k 字节数据 + nsym 字节校验）。
        erasures : 可选，**已知可疑符号的位置**（码字字节索引 0..254）。
            擦除位置由上层先验给出（例如 LRPT 里 Viterbi 软判决可靠性
            最低的字节）。预算从「2e ≤ nsym」放宽到「2e + f ≤ nsym」：
            一个擦除只花一个符号的预算，盲纠一个符号要花两个。
            纯擦除上限 = nsym（RS(255,223) 为 32），是纯纠错上限（16）的两倍。

            位置重复会被去重（不重复消耗预算）；越界位置抛 ValueError；
            擦除数 > nsym 直接判不可纠（预算本身已破）。

        返回
        ----------
        RSResult。``nerrors`` 只计**非擦除位置**的真错误数，擦除数单独
        记在 ``nerasures``。解码后必做伴随式归零校验——校验不过一律按
        不可纠返回，绝不返回「改过的错数据 + corrected=True」。
        """
        if len(codeword) != 255:
            raise ValueError(f"RS 解码需要 255 字节，收到 {len(codeword)}")
        rcvd = list(codeword)
        if self.ccsds_invert:
            rcvd = [b ^ 0xFF for b in rcvd]

        if erasures is not None:
            return self._decode_with_erasures(rcvd, erasures)

        g = self.gf
        n = 255

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
        # 硬验证：纠正后码字的伴随式必须全零。
        # 为什么必须有这一步（2026-11-08 真录实测）：错误数超过 t=16 时
        # Berlekamp-Massey 仍可能给出一个「恰好 ≤t 个根」的解并配上 Forney
        # 幅值，但该解并不自洽——真录探针里 17 个符号错被报成 nerrors=1、
        # corrected=True，纠正后的数据重编码与原码字相差 33 字节。不验证
        # 就会把这种垃圾当成功放行，污染整条 VCDU 真值。
        if any(self._syndromes(out)):
            fb = (bytes(b ^ 0xFF for b in rcvd[:self.k]) if self.ccsds_invert
                  else bytes(rcvd[:self.k]))
            return RSResult(data=fb, nerrors=-1, corrected=False)
        return RSResult(data=data, nerrors=n_err, corrected=True)

    #: 擦除尝试阶梯（**升序**：先少擦、把预算留给盲纠，再逐步加大）。
    #: 为什么从小到大：可靠性排序只是近似（Viterbi 的 MLM 不是精确 SOVA），
    #: 多擦一个「其实没错」的符号就白吃一个预算符号。真录合成链实测：从
    #: 32 起步会把 2e+f 顶过 nsym，逼出「误纠到另一个合法码字」的假成功。
    ERASURE_LADDER: tuple = (4, 8, 12, 16, 20, 24, 28, 32)

    def decode_with_reliability(self, codeword: bytes,
                                rel: Sequence[float],
                                ladder: Optional[Sequence[int]] = None):
        """按符号可靠性自动决定擦除的完整解码（先盲解，失败再擦除）。

        参数
        ----------
        codeword : 255 字节码字。
        rel : 与码字等长的逐符号可靠性，**值越小越可疑**。
            传 None / 长度不足 / 无区分度时退化为普通解码。
        ladder : 覆盖 :attr:`ERASURE_LADDER`（测试与调参用）。

        返回
        ----------
        ``(RSResult, k_erasures_used)``。``k_erasures_used == 0`` 表示盲解就
        成功，或可靠性信息不足以定位（此时不浪费预算）。

        预算下界：设标出 n_susp 个「显著可疑」符号。若全当擦除，则剩余未知
        错误 ≈ n_susp − K，判据 2(n_susp−K)+K ≤ nsym 给出 K ≥ 2·n_susp − nsym。
        比这更小的 K 必然超预算（会逼出不自洽解），直接跳过。
        """
        blind = self.decode(codeword)
        if blind.nerrors >= 0 or rel is None:
            return blind, 0
        arr = np.asarray(rel, dtype=np.float64).reshape(-1)
        if arr.size < len(codeword):
            return blind, 0
        arr = arr[: len(codeword)]
        med = float(np.median(arr))
        if not np.isfinite(med) or med <= 0.0:
            return blind, 0
        n_susp = int(np.count_nonzero(arr < 0.5 * med))
        if n_susp == 0:
            return blind, 0              # 无低谷：可靠性没有区分度，不擦
        k_min = max(0, 2 * n_susp - self.nsym)
        lad = tuple(ladder) if ladder is not None else self.ERASURE_LADDER
        order = np.argsort(arr)
        for k in lad:
            if k <= 0 or k > self.nsym or k < k_min:
                continue
            r = self.decode(codeword, erasures=order[:k].tolist())
            if r.nerrors >= 0:
                return r, k
        return blind, 0

    # ------------------------------------------------------------------
    # 擦除辅助解码（2e + f <= nsym）——LRPT 队列 #9
    # ------------------------------------------------------------------
    def _syndromes(self, rcvd: List[int]) -> List[int]:
        """伴随式 S_i = r(alpha^(prim*(fcr+i)))，低次在前。"""
        g = self.gf
        out = [0] * self.nsym
        for i in range(self.nsym):
            root = g.pow(2, self.prim * (self.fcr + i))
            s = 0
            for byte in rcvd:
                s = g.mul(s, root) ^ byte
            out[i] = s
        return out

    def _erasure_locator(self, positions: Sequence[int]) -> List[int]:
        """擦除定位多项式 Λ_e(x) = Π_k (1 - X_k x)，低次在前，常数项 1。

        特征值 X_k = alpha^(prim*d)，d = 254 - pos —— 与 :meth:`decode` 的
        Forney 步同一约定（prim≠1 时随 prim 拉伸），Chien 位置映射
        ``pos = (iprim*j - 1) % 255`` 与之自洽。
        """
        g = self.gf
        poly = [1]
        for pos in positions:
            d = 255 - 1 - pos
            xk = g.pow(2, self.prim * d)
            new = [0] * (len(poly) + 1)
            for i, c in enumerate(poly):
                new[i] ^= c
                new[i + 1] ^= g.mul(c, xk)
            poly = new
        return poly

    def _decode_with_erasures(self, rcvd: List[int],
                              erasures: Iterable[int]) -> RSResult:
        """擦除辅助 BM 解码。入参 rcvd 已完成 CCSDS 符号反转（域内表示）。"""
        g = self.gf
        n = 255
        k = self.k
        raw = [int(p) for p in erasures]
        for p in raw:
            if p < 0 or p >= n:
                raise ValueError(f"擦除位置须在 0..{n - 1}，收到 {p}")
        era = sorted(set(raw))          # 去重：重复声明不重复吃预算
        f = len(era)

        def _fail() -> RSResult:
            fb = (bytes(b ^ 0xFF for b in rcvd[:k]) if self.ccsds_invert
                  else bytes(rcvd[:k]))
            return RSResult(data=fb, nerrors=-1, corrected=False, nerasures=f)

        if f > self.nsym:               # 预算本身已破，不进 BM
            return _fail()

        synd = self._syndromes(rcvd)
        if not any(synd):
            # 本帧本来就好：如实报 0 错，不把「声明过的擦除」算成纠正
            fb = (bytes(b ^ 0xFF for b in rcvd[:k]) if self.ccsds_invert
                  else bytes(rcvd[:k]))
            return RSResult(data=fb, nerrors=0, corrected=False, nerasures=f)

        # Berlekamp-Massey（擦除初值版）：C/B 初始为擦除定位多项式，
        # L=f，从 N=f 起迭代；条件 2L <= N+f（等价 2e+f <= nsym）。
        C = self._erasure_locator(era)
        B = C[:]
        L = f
        m = 1
        b = 1
        for N in range(f, self.nsym):
            d = synd[N]
            for i in range(1, min(L, len(C) - 1, N) + 1):
                d ^= g.mul(C[i], synd[N - i])
            if d == 0:
                m += 1
                continue
            coef = g.div(d, b)
            need = len(B) + m
            if len(C) < need:
                C = C + [0] * (need - len(C))
            if 2 * L <= N + f:
                T = C[:]
                for i in range(len(B)):
                    C[i + m] ^= g.mul(coef, B[i])
                L = N + f + 1 - L
                B = T
                b = d
                m = 1
            else:
                for i in range(len(B)):
                    C[i + m] ^= g.mul(coef, B[i])
                m += 1

        # Chien search：找 C 的全部根
        err_pos: List[int] = []
        for j in range(n):
            val = 0
            xn = 1
            a_j = g.pow(2, j)
            for i in range(len(C)):
                val ^= g.mul(C[i], xn)
                xn = g.mul(xn, a_j)
            if val == 0:
                err_pos.append((self.iprim * j - 1) % n)

        deg = len(C) - 1
        while deg > 0 and C[deg] == 0:
            deg -= 1
        # 根数必须等于多项式次数；预算判据 2e+f <= nsym（deg = e+f）
        if len(err_pos) != deg or 2 * deg > self.nsym + f:
            return _fail()
        # 擦除位置必须全部落到根上，否则说明先验与数据矛盾
        if not set(era).issubset(err_pos):
            return _fail()

        # Forney：Omega = S·Λ mod x^nsym，e_k = Omega(X_k^-1)/Λ'(X_k^-1)·X_k^(1-fcr)
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

        era_set = set(era)
        out = list(rcvd)
        n_true_err = 0
        for pos in err_pos:
            d = n - 1 - pos
            xk = g.pow(2, self.prim * d)
            xk_inv = g.inverse(xk)
            num = 0
            xn = 1
            for i in range(len(omega)):
                num ^= g.mul(omega[i], xn)
                xn = g.mul(xn, xk_inv)
            den = 0
            xn = 1
            for i in range(len(deriv)):
                den ^= g.mul(deriv[i], xn)
                xn = g.mul(xn, xk_inv)
            if den == 0:
                return _fail()
            magnitude = g.div(num, den)
            magnitude = g.mul(magnitude, g.pow(xk, 1 - self.fcr))
            if magnitude == 0:
                continue            # 冗余擦除（该位置本来没错）
            out[pos] ^= magnitude
            if pos not in era_set:
                n_true_err += 1

        # 硬验证：纠正后伴随式必须全零，否则绝不声称成功
        if any(self._syndromes(out)):
            return _fail()

        data = bytes(out[:k])
        if self.ccsds_invert:
            data = bytes(b ^ 0xFF for b in data)
        return RSResult(data=data, nerrors=n_true_err,
                        corrected=bool(err_pos), nerasures=f)


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
        tx = enc.encode_bits(bits)   # 编码：输出与输入等长（参考符号 0 仅作异或种子，不追加）
        rx = enc.decode_bits(tx)     # 解码：decode(encode(x)) == x
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


# ===========================================================================
# 差分编解码工具面（薄包装：agent 面 + CLI 面一次通吃）
# ===========================================================================

def register_tool_registry(registry) -> None:
    """把差分编码/解码注册为两个独立原子工具。

    能力依据：Costas/PSK 相干解调后的相位模糊（n×π 镜像）标准解法就是
    差分编码（通信对抗材料 L20），与 costas_track 构成配对积木。
    """
    from .tool_registry import ToolResult, clean_exc

    def _bits_in(v, name):
        arr = np.asarray(v, dtype=float).flatten()
        if arr.size == 0:
            raise ValueError(f"{name} 为空（诚实空态：不伪造编解码结果）")
        if not np.all((arr == 0) | (arr == 1)):
            raise ValueError(f"{name} 只接受 0/1 比特数组")
        return arr.astype(np.int8)

    def _make_handler(name):
        def handler(args):
            try:
                enc = DifferentialEncoder(order=int(args.get("order", 2)))
                bits = _bits_in(args["bits"], "bits")
                out = (enc.encode_bits(bits) if name == "diff_encode"
                       else enc.decode_bits(bits))
                verb = "差分编码" if name == "diff_encode" else "差分解码"
                return ToolResult(
                    success=True,
                    content=f"{verb}（order={enc.order}）：{len(out)} 比特",
                    tool_name=name,
                    data={"bits": [int(b) for b in out],
                          "order": int(enc.order), "count": int(len(out))},
                )
            except Exception as e:  # noqa: BLE001
                return ToolResult(
                    success=False,
                    content=f"参数/计算错误: {clean_exc(e)}",
                    tool_name=name, error=str(e),
                )
        return handler

    schema = {
        "type": "object",
        "properties": {
            "bits": {"type": "array", "items": {"type": "integer"},
                     "description": "0/1 比特数组（长度须为每符号比特数的倍数："
                                    "order=2 时任意长度，order=4 时须为偶数）"},
            "order": {"type": "integer", "enum": [2, 4],
                      "description": "2=BPSK(DBPSK)，4=QPSK(DQPSK)，默认 2"},
        },
        "required": ["bits"],
    }
    registry.register(
        name="diff_encode",
        description="差分编码：DBPSK/DQPSK 相对调相的比特级预编码（输出与输入等长，"
                    "参考符号 0 仅作异或种子）。发射端预编码，接收端配合 diff_decode "
                    "解相位模糊。原子积木。",
        parameters=schema,
        handler=_make_handler("diff_encode"), category="dsp",
    )
    registry.register(
        name="diff_decode",
        description="差分解码：对差分编码比特做累积异或还原（decode(encode(x))==x）。"
                    "也用于 Costas/PSK 相干锁定后的 n×π 相位模糊消除——"
                    "四种模糊相位解出的序列经差分解码后收敛为同一明文。原子积木。",
        parameters=schema,
        handler=_make_handler("diff_decode"), category="dsp",
    )
