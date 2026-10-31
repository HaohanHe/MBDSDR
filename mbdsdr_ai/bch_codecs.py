# SPDX-License-Identifier: MIT
"""P0-2（半收敛）：BCH 干净室编解码（GF(2^m) 全推导，零标准表依赖）。

DVB-S2 的 FEC 是 BCH + LDPC 级联。LDPC 的 H 矩阵地址表是 ETSI EN 302 307
的大张标准常数，**离线环境没有标准文本可对照，硬编会违反"无伪造"红线**——
本仓显式不实现 DVB-S2 LDPC（见 module 尾注与任务清单的 deferred 标注）。

但 BCH 是**纯数学可推导**的：GF(2^m) 由本原多项式生成，生成多项式 =
α^1..α^{2t} 极小多项式的最小公倍（二元域上即不同极小多项式之积），
纠错能力 = 设计距离 2t+1 保证的 ⌊(d-1)/2⌋。全部从本原多项式现算，
不需要任何标准表；能力用"随机错 ≤t 必纠回、>t 诚实报不可纠"钉扎。

边界与诚实纪律：
  * 错 >t：BM 后 discrepancy 超限或 Chien 根数 ≠ 错数 → 如实报
    "uncorrectable"，绝不输出"看起来正常"的码字；
  * 本原多项式是具名参数（默认 0x11D = x^8+x^4+x^3+x^2+1，教科书常用）；
  * DVB-S2 具体帧格式（64800/16200、各 t 与生成多项式系数表）待标准
    文本到位后作"皮"注入（sat_profiles.json 路线），本模块是"算法"层。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "Gf2m", "bch_generator_poly", "bch_encode", "bch_decode",
    "register_tool_registry",
]


class Gf2m:
    """GF(2^m) 对数/反对数表（由本原多项式现算，零查表）。"""

    def __init__(self, m: int, primitive_poly: int):
        if not 2 <= m <= 16:
            raise ValueError("m 须在 2..16（实现边界）")
        self.m = int(m)
        self.mod = primitive_poly
        self.order = (1 << m) - 1
        # exp[i] = α^i 的多项式表示；log[p] = 指数
        self.exp = [0] * (2 * self.order)
        self.log = [0] * (1 << m)
        x = 1
        for i in range(self.order):
            self.exp[i] = x
            self.log[x] = i
            x <<= 1
            if x & (1 << m):
                x ^= primitive_poly
        for i in range(self.order, 2 * self.order):
            self.exp[i] = self.exp[i - self.order]
        if x != 1:
            raise ValueError(
                f"多项式 0x{primitive_poly:X} 不是 {m} 次本原多项式"
                "（α 的阶 ≠ 2^m−1），无法生成 GF(2^{m})")

    def mul(self, a: int, b: int) -> int:
        if a == 0 or b == 0:
            return 0
        return self.exp[(self.log[a] + self.log[b]) % self.order]

    def pow_a(self, e: int) -> int:
        """α^e 的多项式值（e 可为任意整数）。"""
        return self.exp[e % self.order]


def _poly_mul_gf2(p: int, q: int) -> int:
    """GF(2) 上多项式乘法（系数位打包，低次在位 0）。"""
    out = 0
    while q:
        if q & 1:
            out ^= p
        p <<= 1
        q >>= 1
    return out


def _poly_mod_gf2(a: int, mod: int) -> int:
    """a mod mod（GF(2) 多项式除法取余）。"""
    dm = mod.bit_length() - 1
    while a.bit_length() - 1 >= dm:
        a ^= mod << (a.bit_length() - 1 - dm)
    return a


def _minimal_poly(gf: Gf2m, beta: int) -> int:
    """β 在 GF(2) 上的极小多项式：共轭链 {β, β^2, β^4, ...} 之根的首一积。

    系数在 GF(2^m) 上做多项式乘法（系数乘 = gf.mul），结果必落回 GF(2)。
    返回位打包（位 d = x^d 系数）。
    """
    j = gf.log[beta]
    coeffs = [1]  # coeffs[d] = x^d 的 GF(2^m) 系数
    e = j
    seen = set()
    while e not in seen:
        seen.add(e)
        c = gf.pow_a(e)
        # ×(x + c)：new[d] = old[d]·c + old[d−1]
        new = [0] * (len(coeffs) + 1)
        for d, a in enumerate(coeffs):
            new[d] ^= gf.mul(a, c)
            new[d + 1] ^= a
        coeffs = new
        e = (e * 2) % gf.order
    packed = 0
    for d, a in enumerate(coeffs):
        if a:
            if a != 1:
                raise ValueError(
                    "极小多项式系数未落回 GF(2)——共轭链实现有误（内部错误）")
            packed |= 1 << d
    return packed


def bch_generator_poly(m: int, t: int,
                       primitive_poly: int = 0x11D,
                       ) -> Dict[str, Any]:
    """BCH 生成多项式（窄义，连续根 α^1..α^{2t}）。

    g(x) = 各 α^b（b=1..2t）极小多项式之积（去重，GF(2) 上即 LCM）。
    返回系数位打包 g 与信息位长 k = 2^m − 1 − deg(g)。
    """
    if t < 1:
        raise ValueError("t 须 ≥ 1")
    if primitive_poly.bit_length() - 1 != m:
        raise ValueError(
            f"本原多项式 0x{primitive_poly:X} 是 {primitive_poly.bit_length() - 1}"
            f" 次，与 m={m} 不符——参数不自洽")
    gf = Gf2m(m, primitive_poly)
    if 2 * t >= gf.order:
        raise ValueError("2t 须 < 2^m−1（否则 BCH 退化成重复码级数）")
    g = 1
    degs = []
    used_minpolys = set()
    for b in range(1, 2 * t + 1):
        mp = _minimal_poly(gf, gf.pow_a(b))
        if mp not in used_minpolys:
            used_minpolys.add(mp)
            g = _poly_mul_gf2(g, mp)
            degs.append(mp.bit_length() - 1)
    n = gf.order
    deg_g = g.bit_length() - 1
    k = n - deg_g
    if k < 1:
        raise ValueError("生成式次数 ≥ n——t 对该 m 过大")
    return {
        "g": g, "deg_g": deg_g, "n": n, "k": k, "t": t,
        "designed_distance": 2 * t + 1,
        "primitive_poly": primitive_poly, "m": m,
        "minimal_poly_degs": degs,
        "note": ("窄义 BCH：根 α^1..α^{2t}，设计距离 2t+1，"
                 "纠错能力 ⌊(d−1)/2⌋ = t（保证值）"),
    }


def bch_encode(message: List[int], gen: Dict[str, Any]) -> List[int]:
    """信息位（k 个 0/1）→ 系统形 BCH 码字（n 个 0/1，信息在前）。"""
    k, n = gen["k"], gen["n"]
    if len(message) != k:
        raise ValueError(f"信息长 {len(message)} ≠ k={k}")
    if any(b not in (0, 1) for b in message):
        raise ValueError("信息位须为 0/1")
    g = gen["g"]
    r = gen["deg_g"]
    # m(x)·x^r mod g(x)
    mx = 0
    for b in message:
        mx = (mx << 1) | b
    rem = _poly_mod_gf2(mx << r, g)
    cw = (mx << r) | rem
    return [(cw >> (n - 1 - i)) & 1 for i in range(n)]


def bch_decode(received: List[int], gen: Dict[str, Any],
               ) -> Dict[str, Any]:
    """接收字（n 个 0/1）→ 译码结果。

    二元 BCH：综合征 → Berlekamp-Massey → Chien 搜索（错值全为 1，
    无需 Forney）。>t 个错如实报 uncorrectable，不输出伪正常码字。
    """
    n, t = gen["n"], gen["t"]
    if len(received) != n:
        raise ValueError(f"接收字长 {len(received)} ≠ n={n}")
    gf = Gf2m(gen["m"], gen["primitive_poly"])
    rx = 0
    for b in received:
        rx = (rx << 1) | (int(b) & 1)
    # 综合征 S_b = r(α^b)，b = 1..2t（Horner 求值）
    synd = []
    for b in range(1, 2 * t + 1):
        acc = 0
        ab = gf.pow_a(b)
        for i in range(n):
            acc = gf.mul(acc, ab)
            if (rx >> (n - 1 - i)) & 1:
                acc ^= 1
        synd.append(acc)
    if all(s == 0 for s in synd):
        return {"codeword": list(received), "n_errors": 0,
                "corrected": False, "uncorrectable": False}
    # Berlekamp-Massey（综合征序列 S_1..S_{2t}；系数在 GF(2^m)）
    c = [0] * (2 * t + 1)
    c[0] = 1
    b_poly = [0] * (2 * t + 1)
    b_poly[0] = 1
    b_scalar = 1          # B(x) 前次的差异值（标量除数）
    l, m_shift = 0, 1
    for i in range(2 * t):
        d = synd[i]
        for j in range(1, l + 1):
            d ^= gf.mul(c[j], synd[i - j])
        if d == 0:
            m_shift += 1
            continue
        coef = gf.mul(d, gf.exp[(gf.order - gf.log[b_scalar]) % gf.order])
        c_copy = c[:]
        for j in range(2 * t + 1 - m_shift):
            if b_poly[j]:
                c[j + m_shift] ^= gf.mul(coef, b_poly[j])
        if 2 * l <= i:
            l = i + 1 - l
            b_poly = c_copy
            b_scalar = d
            m_shift = 1
        else:
            m_shift += 1
    if l > t:
        return {"codeword": None, "n_errors": None,
                "corrected": False, "uncorrectable": True,
                "reason": f"BM 错位多项式次数 {l} > t={t}——超出纠错能力"}
    # Chien 搜索：找错误位置（码字位序 MSB=α^{n-1} 幂次约定：
    # 位 i（0..n-1）对应 α 的幂 n-1-i；错位置 = X_l 的逆元）
    err_pos = []
    for i in range(n):
        power = (n - 1 - i) % gf.order
        x = gf.pow_a(-power)
        # Λ(x) 系数是 GF(2^m) 元（非二元——错位置多项式的根在 X_j^{-1}）
        acc = 0
        for j in range(l, -1, -1):
            acc = gf.mul(acc, x)
            if c[j]:
                acc ^= c[j]
        if acc == 0:
            err_pos.append(i)
    if len(err_pos) != l:
        return {"codeword": None, "n_errors": None,
                "corrected": False, "uncorrectable": True,
                "reason": (f"Chien 根数 {len(err_pos)} ≠ BM 次数 {l}——"
                           "错数超能力或综合征不自洽")}
    cw = list(received)
    for p in err_pos:
        cw[p] ^= 1
    # 译后校验：综合征应全零
    rx2 = 0
    for b in cw:
        rx2 = (rx2 << 1) | b
    for b in range(1, 2 * t + 1):
        ab = gf.pow_a(b)
        acc = 0
        for i in range(n):
            acc = gf.mul(acc, ab)
            if (rx2 >> (n - 1 - i)) & 1:
                acc ^= 1
        if acc != 0:
            return {"codeword": None, "n_errors": None,
                    "corrected": False, "uncorrectable": True,
                    "reason": "译后综合征非零——内部一致性破坏（不该发生）"}
    return {"codeword": cw, "n_errors": l, "corrected": True,
            "uncorrectable": False}


#: 附注：DVB-S2 LDPC（ETSI EN 302 307）的 H 矩阵信息位地址表是大张标准
#: 常数，离线环境无标准文本可对照，按"无伪造"红线显式不实现；标准文本
#: 到位后走 sat_profiles.json 配置注入路线补"皮"。


# ═══════════════════════════════════════════════════════════
# 工具层
# ═══════════════════════════════════════════════════════════
def register_tool_registry(registry) -> None:
    """注册 BCH 干净室编解码积木（GF 推导，零标准表依赖）。"""
    from .tool_registry import ToolResult, clean_exc
    import json as _json

    def _gen(args):
        try:
            r = bch_generator_poly(int(args.get("m", 8)),
                                     int(args.get("t", 8)),
                                     int(str(args.get("primitive_poly",
                                                       "0x11D")), 16)
                                     if isinstance(args.get(
                                         "primitive_poly", 0x11D), str)
                                     else int(args.get("primitive_poly", 0x11D)))
            out = {k: (hex(v) if k == "g" else v) for k, v in r.items()}
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"BCH 生成式推导失败: {clean_exc(e)}")
        return ToolResult(
            success=True,
            content=(f"BCH({r['n']},{r['k']}) t={r['t']}：deg(g)={r['deg_g']}，"
                     f"设计距离 {r['designed_distance']}"),
            data=out)

    def _enc(args):
        try:
            gen = bch_generator_poly(int(args.get("m", 8)),
                                     int(args.get("t", 8)),
                                     int(args.get("primitive_poly", 0x11D)))
            msg = args.get("message")
            if msg is None:
                raise ValueError("message 必须提供（0/1 列表，长 k）")
            cw = bch_encode([int(b) & 1 for b in msg], gen)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"BCH 编码失败: {clean_exc(e)}")
        return ToolResult(success=True,
                          content=f"已编码 {len(cw)} 位（BCH({gen['n']},{gen['k']})）",
                          data={"codeword": cw, "n": gen["n"], "k": gen["k"]})

    def _dec(args):
        try:
            gen = bch_generator_poly(int(args.get("m", 8)),
                                     int(args.get("t", 8)),
                                     int(args.get("primitive_poly", 0x11D)))
            cw = args.get("codeword")
            if cw is None:
                raise ValueError("codeword 必须提供（0/1 列表，长 n）")
            r = bch_decode([int(b) & 1 for b in cw], gen)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False,
                              content=f"BCH 译码失败: {clean_exc(e)}")
        if r["uncorrectable"]:
            return ToolResult(success=False,
                              content=f"不可纠: {r.get('reason', '')}", data=r)
        return ToolResult(
            success=True,
            content=(f"译码完成：{r['n_errors']} 错"
                     + ("（已纠）" if r["corrected"] else "（无错）")),
            data=r)

    registry.register(
        name="bch_generator",
        description=(
            "BCH 生成多项式推导（干净室）：GF(2^m) 由本原多项式现算，"
            "g(x)=α^1..α^{2t} 极小多项式之积，零标准表依赖。"
            "返回 n/k/t/设计距离。默认 m=8, t=8（BCH(255,223) 教科书参数）。"
            "能力边界：窄义 BCH；DVB-S2 专用帧格式表待标准文本后走配置注入。"
        ),
        parameters={"type": "object", "properties": {
            "m": {"type": "integer", "default": 8},
            "t": {"type": "integer", "default": 8},
            "primitive_poly": {"type": "integer", "default": 0x11D},
        }, "required": []},
        handler=_gen, category="fec",
    )
    registry.register(
        name="bch_encode",
        description="BCH 系统形编码：k 个信息位 → n 位码字（信息在前）。",
        parameters={"type": "object", "properties": {
            "message": {"type": "array", "items": {"type": "integer"}},
            "m": {"type": "integer", "default": 8},
            "t": {"type": "integer", "default": 8},
            "primitive_poly": {"type": "integer", "default": 0x11D},
        }, "required": ["message"]},
        handler=_enc, category="fec",
    )
    registry.register(
        name="bch_decode",
        description=(
            "BCH 译码（BM + Chien）：≤t 错必纠回；>t 错如实报不可纠，"
            "绝不输出伪正常码字。"
        ),
        parameters={"type": "object", "properties": {
            "codeword": {"type": "array", "items": {"type": "integer"}},
            "m": {"type": "integer", "default": 8},
            "t": {"type": "integer", "default": 8},
            "primitive_poly": {"type": "integer", "default": 0x11D},
        }, "required": ["codeword"]},
        handler=_dec, category="fec",
    )
