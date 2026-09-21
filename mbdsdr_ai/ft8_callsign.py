"""FT8 28-bit callsign 解包——移植 wsjtx unpack28.f90。

编码布局（n28）：
- 0/1/2 = DE/QRZ/CQ 特殊 token
- 3..1002 = CQ_nnn（数字）
- 1003..532443 = CQ_aaaa（字母串）
- 之后 22bit hash 段（需 wsjtx 呼号表，未内置，返回 <hash:nnn>）
- 最后标准呼号：6 位，字符集 (c1,c2,c3,c4)。
"""
from __future__ import annotations

NTOKENS = 2063592
MAX22 = 4194304

_C1 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 36
_C2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"   # 36
_C3 = "0123456789"                              # 10
_C4 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"             # 27


def unpack28(n28: int) -> tuple[str, bool]:
    """返回 (呼号, 是否成功)。hash 段无表时返回 ('<hash:n>', False)。"""
    n28 = int(n28) & 0xFFFFFFFF
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
    # 标准呼号：反向编码 BI4MIB 附近——手工算一个已知值困难，
    # 这里只验证解码不崩 + CQ_ 数字段
    got, ok = unpack28(5)
    print("CQ_nnn(5):", got, ok)
    # 标准段抽样：n28 落在标准区间
    n = NTOKENS + MAX22 + 100000
    got, ok = unpack28(n)
    print("标准呼号抽样:", repr(got), "ok=", ok)

    # 往返：手算 BI4MIB 的标准呼号编码，解回应还原
    i1 = _C1.index("B")
    i2 = _C2.index("I")
    i3 = _C3.index("4")
    i4 = _C4.index("M")
    i5 = _C4.index("I")
    i6 = _C4.index("B")
    n = (i1 * (36 * 10 * 27 * 27 * 729) if False else
         i1 * (36 * 10 * 27**3) + i2 * (10 * 27**3) + i3 * (27**3)
         + i4 * 27**2 + i5 * 27 + i6)
    n28 = n + MAX22 + NTOKENS
    got, ok = unpack28(n28)
    print(f"BI4MIB 往返: n28={n28} -> {got!r} (期望 BI4MIB) ok={ok}")
