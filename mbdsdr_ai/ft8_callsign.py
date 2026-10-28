# SPDX-License-Identifier: MIT
"""FT8 28-bit callsign 解包（独立实现）。

编码布局（n28，公开 77-bit 报文格式事实）：
- 0/1/2 = DE/QRZ/CQ 特殊 token
- 3..1002 = CQ_nnn（数字）
- 1003..532443 = CQ_aaaa（字母串）
- 532444..NTOKENS-1 = 未分配保留段（返回 <unallocated:n>，诚实失败，
  不伪造 hash/呼号）
- NTOKENS..NTOKENS+MAX22-1 = 22bit hash 段（未内置呼号表，返回 <hash:n>）
- 之后标准呼号：6 位，字符集 (c1,c2,c3,c4,c5,c6)。
入参按 28 位截断（与 ft8_codec/js8call_adapter 口径一致），超宽输入
按低 28 位折返、不越界崩溃。
"""
from __future__ import annotations

NTOKENS = 2063592
MAX22 = 4194304

_C1 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 37
_C2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 36
_C3 = "0123456789"                              # 10
_C4 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"             # 27


def unpack28(n28: int) -> tuple[str, bool]:
    """返回 (呼号, 是否成功)。hash 段无表时返回 ('<hash:n>', False)，
    未分配保留段返回 ('<unallocated:n>', False)。"""
    n28 = int(n28) & 0x0FFFFFFF
    if n28 < NTOKENS:
        if n28 == 0:
            return "DE", True
        if n28 == 1:
            return "QRZ", True
        if n28 == 2:
            return "CQ", True
        if n28 <= 1002:
            return f"CQ_{n28 - 3:03d}", True
        if n28 <= 532443:
            n = n28 - 1003
            i1 = n // (27 * 27 * 27); n %= (27 * 27 * 27)
            i2 = n // (27 * 27); n %= (27 * 27)
            i3 = n // 27; i4 = n % 27
            s = _C4[i1] + _C4[i2] + _C4[i3] + _C4[i4]
            return "CQ_" + s.lstrip(), True
        # 532444..NTOKENS-1：公开协议未分配的保留段。若按 hash 段做减法
        # 会得到负数假 hash（静默吞错），这里诚实失败。
        return f"<unallocated:{n28}>", False
    n28 -= NTOKENS
    if n28 < MAX22:
        return f"<hash:{n28}>", False  # 22bit hash，需 wsjtx 呼号表
    # 标准呼号
    n = n28 - MAX22
    i1 = n // (36 * 10 * 27 * 27 * 27); n %= (36 * 10 * 27 * 27 * 27)
    i2 = n // (10 * 27 * 27 * 27); n %= (10 * 27 * 27 * 27)
    i3 = n // (27 * 27 * 27); n %= (27 * 27 * 27)
    i4 = n // (27 * 27); n %= (27 * 27)
    i5 = n // 27; i6 = n % 27
    call = (_C1[i1] + _C2[i2] + _C3[i3] +
            _C4[i4] + _C4[i5] + _C4[i6]).strip()
    return call, True


if __name__ == "__main__":
    # 自测：常见特殊 token
    for t, want in [(0, "DE"), (1, "QRZ"), (2, "CQ")]:
        got, ok = unpack28(t)
        print(f"token {t}: {got} (期望 {want}) ok={ok}")
    # 标准呼号：反向编码 BI1AAA 附近——手工算一个已知值困难，
    # 这里只验证解码不崩 + CQ_ 数字段
    got, ok = unpack28(5)
    print("CQ_nnn(5):", got, ok)
    # 标准段抽样：n28 落在标准区间
    n = NTOKENS + MAX22 + 100000
    got, ok = unpack28(n)
    print("标准呼号抽样:", repr(got), "ok=", ok)

    # 往返：手算 BI1AAA 的标准呼号编码，解回应还原（fixture 呼号用中性值）
    i1 = _C1.index("B")
    i2 = _C2.index("I")
    i3 = _C3.index("1")
    i4 = _C4.index("A")
    i5 = _C4.index("A")
    i6 = _C4.index("A")
    n = (i1 * (36 * 10 * 27**3) + i2 * (10 * 27**3) + i3 * (27**3)
         + i4 * 27**2 + i5 * 27 + i6)
    n28 = n + MAX22 + NTOKENS
    got, ok = unpack28(n28)
    print(f"BI1AAA 往返: n28={n28} -> {got!r} (期望 BI1AAA) ok={ok}")
