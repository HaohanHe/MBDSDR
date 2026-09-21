"""FT8 77-bit payload 解包——移植 wsjtx unpack77 的主分支。

覆盖最常见两类（约占 FT8 绝大多数流量）：
- Type 0.0 (i3=0,n3=0): 自由文本，71 bit，每字符 6 bit base-40
- Type 1   (i3=1/2):     标准消息 CQ/call 网格 或 call call 报告
其余类型（Field Day/WSPR/气象/...）返回 None，留待扩展。

位序：输入 77 个 bit，第 0 个为 MSB（与 wsjtx c77(1) 对齐）。
"""
from __future__ import annotations

from mbdsdr_ai.ft8_callsign import unpack28

MAXGRID4 = 32400
_TEXT40 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ/"  # c: 40 字符


def _bits_to_int(bits: list[int], start: int, n: int) -> int:
    v = 0
    for i in range(start, start + n):
        v = (v << 1) | (1 if bits[i] else 0)
    return v


def _to_grid4(n: int) -> str | None:
    j1, n = divmod(n, 18 * 10 * 10)
    if not 0 <= j1 <= 17:
        return None
    j2, n = divmod(n, 10 * 10)
    if not 0 <= j2 <= 17:
        return None
    j3, j4 = divmod(n, 10)
    if not 0 <= j3 <= 9 or not 0 <= j4 <= 9:
        return None
    return chr(j1 + ord("A")) + chr(j2 + ord("A")) + str(j3) + str(j4)


def _unpack_text(bits: list[int]) -> str:
    """Type 0.0 自由文本：71 bit，前 66 bit 分 11 个 6-bit 字符。"""
    out = []
    for k in range(11):
        v = _bits_to_int(bits, k * 6, 6)
        out.append(_TEXT40[v] if v < len(_TEXT40) else "?")
    return "".join(out).strip()


def unpack77(bits77: list[int]) -> dict:
    """77 位 payload → 结构化消息。

    返回 {type, text, call1, call2}；无法识别的类型 text=None。
    """
    b = [int(x) for x in bits77]
    n3 = _bits_to_int(b, 71, 3)
    i3 = _bits_to_int(b, 74, 3)

    if i3 == 0 and n3 == 0:
        return {"type": "free_text", "text": _unpack_text(b),
                "call1": None, "call2": None}

    if i3 in (1, 2):
        n28a = _bits_to_int(b, 0, 28)
        ipa = b[28]
        n28b = _bits_to_int(b, 29, 28)
        ipb = b[57]
        ir = b[58]
        igrid4 = _bits_to_int(b, 59, 15)

        call1, _ = unpack28(n28a)
        call2, _ = unpack28(n28b)
        # /R /P 后缀简化：暂不加（需要空格定位）

        if igrid4 <= MAXGRID4:
            grid = _to_grid4(igrid4)
            mid = " R " if ir == 1 else " "
            text = f"{call1} {call2}{mid}{grid}".strip() if grid else None
            return {"type": "grid", "text": text, "call1": call1,
                    "call2": call2, "grid": grid}
        irpt = igrid4 - MAXGRID4
        if irpt == 1:
            text = f"{call1} {call2}".strip()
        elif irpt == 2:
            text = f"{call1} {call2} RRR".strip()
        elif irpt == 3:
            text = f"{call1} {call2} RR73".strip()
        elif irpt == 4:
            text = f"{call1} {call2} 73".strip()
        elif irpt >= 5:
            isnr = irpt - 35
            if isnr > 50:
                isnr -= 101
            s = f"{isnr:+03d}".replace("+", "+") if isnr >= 0 else f"{isnr:03d}"
            sep = " R" if ir == 1 else " "
            text = f"{call1}{sep} {call2} {s}".strip()
        else:
            text = None
        return {"type": "report", "text": text, "call1": call1,
                "call2": call2}

    return {"type": f"unknown(i3={i3},n3={n3})", "text": None,
            "call1": None, "call2": None}


if __name__ == "__main__":
    # 自测：自由文本全零
    r = unpack77([0] * 77)
    print("自由文本全零:", r["type"], repr(r["text"]))
    # Type1 构造：n28a=CQ(2), 其余合理，igrid4=网格 PM73
    # PM73: P=15,M=12,7,3 → 15*1800+12*100+7*10+3
    grid_val = 15 * 1800 + 12 * 100 + 7 * 10 + 3
    b = [0] * 77
    # n28a=2 (CQ) 放 0..27
    v = 2
    for k in range(27, -1, -1):
        b[k] = v & 1; v >>= 1
    # igrid4 放 59..73
    v = grid_val
    for k in range(73, 58, -1):
        b[k] = v & 1; v >>= 1
    # i3=1 放 74..76
    v = 1
    for k in range(76, 73, -1):
        b[k] = v & 1; v >>= 1
    r = unpack77(b)
    print("Type1 CQ 网格:", r["type"], repr(r["text"]), "grid=", r.get("grid"))
