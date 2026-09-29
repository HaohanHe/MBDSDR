# SPDX-License-Identifier: MIT
"""FT8 编码器：文本消息 → 79 个音调（独立实现）。

链路：文本 → pack77 (77 bit) → CRC14 (→91 bit) → LDPC(174,91) (→174 bit)
     → 8FSK Gray 映射 (58 数据符号) → 插入 3 个 Costas-7 同步块 (→79 符号)。

物理层参数（符号时长 160ms、6.25Hz 音调间隔、Costas 同步图案、Gray 映射、
77-bit 载荷布局、LDPC 生成矩阵）均为 WSJT 公开协议事实；本模块的编码流程、
结构与命名自行编写。
"""
from __future__ import annotations

from functools import lru_cache

# ── 物理层常量 ────────────────────────────────────────────────────────
KK = 91          # 信息位 = 77 消息 + 14 CRC
ND = 58          # 数据符号数
NS = 21          # 同步符号数 = 3×Costas7
NN = NS + ND     # 总符号数 = 79
NSPS = 1920      # 每符号采样数 @12000 S/s = 160ms
TONE_SPACING_HZ = 6.25   # 8FSK 音调间隔
SYMBOL_MS = 160           # 符号时长

# Costas 7×7 同步音调序列（公开同步图案）
ICOS7 = [3, 1, 4, 0, 6, 5, 2]

# 8FSK Gray 映射表 (3bit 索引 → tone)
GRAYMAP = [0, 1, 3, 2, 5, 6, 4, 7]

# 帧结构 S7 D29 S7 D29 S7；同步块位置 (0-based): 0-6, 36-42, 72-78
SYNC_BLOCKS = (range(0, 7), range(36, 43), range(72, 79))

# LDPC(174,91) 生成矩阵的 83 行（公开矩阵的十六进制紧凑表示，每行 23 hex）。
_GEN_HEX_ROWS = [
    "8329ce11bf31eaf509f27fc",
    "761c264e25c259335493132",
    "dc265902fb277c6410a1bdc",
    "1b3f417858cd2dd33ec7f62",
    "09fda4fee04195fd034783a",
    "077cccc11b8873ed5c3d48a",
    "29b62afe3ca036f4fe1a9da",
    "6054faf5f35d96d3b0c8c3e",
    "e20798e4310eed27884ae90",
    "775c9c08e80e26ddae56318",
    "b0b811028c2bf997213487c",
    "18a0c9231fc60adf5c5ea32",
    "76471e8302a0721e01b12b8",
    "ffbccb80ca8341fafb47b2e",
    "66a72a158f9325a2bf67170",
    "c4243689fe85b1c51363a18",
    "0dff739414d1a1b34b1c270",
    "15b48830636c8b99894972e",
    "29a89c0d3de81d665489b0e",
    "4f126f37fa51cbe61bd6b94",
    "99c47239d0d97d3c84e0940",
    "1919b75119765621bb4f1e8",
    "09db12d731faee0b86df6b8",
    "488fc33df43fbdeea4eafb4",
    "827423ee40b675f756eb5fe",
    "abe197c484cb74757144a9a",
    "2b500e4bc0ec5a6d2bdbdd0",
    "c474aa53d70218761669360",
    "8eba1a13db3390bd6718cec",
    "753844673a27782cc42012e",
    "06ff83a145c37035a5c1268",
    "3b37417858cc2dd33ec3f62",
    "9a4a5a28ee17ca9c324842c",
    "bc29f465309c977e89610a4",
    "2663ae6ddf8b5ce2bb29488",
    "46f231efe457034c1814418",
    "3fb2ce85abe9b0c72e06fbe",
    "de87481f282c153971a0a2e",
    "fcd7ccf23c69fa99bba1412",
    "f0261447e9490ca8e474cec",
    "4410115818196f95cdd7012",
    "088fc31df4bfbde2a4eafb4",
    "b8fef1b6307729fb0a078c0",
    "5afea7acccb77bbc9d99a90",
    "49a7016ac653f65ecdc9076",
    "1944d085be4e7da8d6cc7d0",
    "251f62adc4032f0ee714002",
    "56471f8702a0721e00b12b8",
    "2b8e4923f2dd51e2d537fa0",
    "6b550a40a66f4755de95c26",
    "a18ad28d4e27fe92a4f6c84",
    "10c2e586388cb82a3d80758",
    "ef34a41817ee02133db2eb0",
    "7e9c0c54325a9c15836e000",
    "3693e572d1fde4cdf079e86",
    "bfb2cec5abe1b0c72e07fbe",
    "7ee18230c583cccc57d4b08",
    "a066cb2fedafc9f52664126",
    "bb23725abc47cc5f4cc4cd2",
    "ded9dba3bee40c59b5609b4",
    "d9a7016ac653e6decdc9036",
    "9ad46aed5f707f280ab5fc4",
    "e5921c77822587316d7d3c2",
    "4f14da8242a8b86dca73352",
    "8b8b507ad467d4441df770e",
    "22831c9cf1169467ad04b68",
    "213b838fe2ae54c38ee7180",
    "5d926b6dd71f085181a4e12",
    "66ab79d4b29ee6e69509e56",
    "958148682d748a38dd68baa",
    "b8ce020cf069c32a723ab14",
    "f4331d6d461607e95752746",
    "6da23ba424b9596133cf9c8",
    "a636bcbc7b30c5fbeae67fe",
    "5cb0d86a07df654a9089a20",
    "f11f106848780fc9ecdd80a",
    "1fbb5364fb8d2c9d730d5ba",
    "fcb86bc70a50c9d02a5d034",
    "a534433029eac15f322e34c",
    "c989d9c7c3d3b8c55d75130",
    "7bb38b2f0186d46643ae962",
    "2644ebadeb44b9467d1f42c",
    "608cc857594bfbb55d69600",
]


@lru_cache(maxsize=1)
def _generator_matrix() -> list[list[int]]:
    """把 83 个 23-hex 行解析为 83×91 生成子矩阵 gen(i,j)。

    每行前 22 个 hex 各 4 bit、最后 1 个 3 bit，共 91 bit。
    """
    gen = [[0] * 91 for _ in range(83)]
    for i, hexs in enumerate(_GEN_HEX_ROWS):
        for j, ch in enumerate(hexs):
            nibble = int(ch, 16)
            ibmax = 3 if j == 22 else 4
            for jj in range(1, ibmax + 1):
                icol = j * 4 + jj - 1
                if (nibble >> (4 - jj)) & 1:
                    gen[i][icol] = 1
    return gen


def ldpc_encode_174_91(msg91: list[int]) -> list[int]:
    """91 bit (77消息+14CRC) → 174 bit 码字。

    前 91 位为信息位，后 83 位为校验位：pchecks(i)=sum_j message(j)*gen(i,j) mod 2。
    """
    gen = _generator_matrix()
    assert len(msg91) == 91
    cw = list(msg91) + [0] * 83
    for i in range(83):
        s = 0
        for j in range(91):
            s += msg91[j] * gen[i][j]
        cw[91 + i] = s & 1
    return cw


# CRC-14 生成多项式系数（MSB→LSB），公开协议常数
_CRC14_P = [1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 1, 0, 1, 1, 1]


def crc14_bits(bits96: list[int]) -> list[int]:
    """对 96 bit 块做模-2 长除，返回 14 bit CRC（MSB 在前）。"""
    r = list(bits96[:15])
    n = len(bits96)
    for i in range(0, n - 14):
        r[14] = bits96[i + 14] if i + 14 < n else 0
        fb = r[0]
        if fb:
            r = [(r[k] ^ _CRC14_P[k]) for k in range(15)]
        r = r[1:] + [r[0]]  # 左移一位
    return r[:14]


def add_crc14(msg77: list[int]) -> list[int]:
    """77 消息位 → 91 位 (77 + 14 CRC)。"""
    assert len(msg77) == 77
    block = list(msg77) + [0] * 5 + [0] * 14
    crc = crc14_bits(block)
    return list(msg77) + crc


# ── 28-bit 呼号打包（公开 77-bit 报文格式）──────────────────────────
NTOKENS = 2063592
MAX22 = 4194304
_A1 = " 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_A2 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_A3 = "0123456789"
_A4 = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def pack28(call: str) -> int:
    """把一个呼号/特殊 token 打包成 28 bit 整数。"""
    c = call.strip().upper()
    if c == "DE":
        return 0
    if c == "QRZ":
        return 1
    if c == "CQ":
        return 2
    n = len(c)
    iarea = -1
    for i in range(n, 1, -1):
        if c[i - 1].isdigit():
            iarea = i
            break
    if iarea == 2:
        c6 = " " + c[:5]
    elif iarea == 3:
        c6 = c[:6]
    else:
        # 非标准呼号：用 22bit hash 占位
        h = 0
        for ch in c:
            h = (h * 31 + ord(ch)) & 0x3FFFFF
        return NTOKENS + h
    i1 = _A1.index(c6[0])
    i2 = _A2.index(c6[1])
    i3 = _A3.index(c6[2])
    i4 = _A4.index(c6[3])
    i5 = _A4.index(c6[4])
    i6 = _A4.index(c6[5])
    n28 = (36 * 10 * 27 ** 3 * i1 + 10 * 27 ** 3 * i2 + 27 ** 3 * i3
           + 27 ** 2 * i4 + 27 * i5 + i6)
    n28 = n28 + NTOKENS + MAX22
    return n28 & 0x0FFFFFFF


MAXGRID4 = 32400


def _grid4_to_int(grid: str) -> int:
    """4 字符 Maidenhead 网格 → 15 bit 整数。"""
    j1 = (ord(grid[0]) - ord("A")) * 18 * 10 * 10
    j2 = (ord(grid[1]) - ord("A")) * 10 * 10
    j3 = (ord(grid[2]) - ord("0")) * 10
    j4 = (ord(grid[3]) - ord("0"))
    return j1 + j2 + j3 + j4


def _is_grid4(grid: str) -> bool:
    """严格 4 字符网格校验。"""
    return (len(grid) == 4
            and "A" <= grid[0] <= "R"
            and "A" <= grid[1] <= "R"
            and grid[2].isdigit()
            and grid[3].isdigit())


def pack77_standard(call1: str, call2: str,
                   info: str | None = None) -> list[int]:
    """标准 Type-1 消息 → 77 bit。

    info 可以是：4 字符网格(如 'OM74')、信号报告(如 '-17','R-17')、
    'RRR'、'RR73'、'73'，或 None。
    位布局: 28(call1)+1+28(call2)+1+1(报告标志)+15(网格/报告)+3(type) = 77
    """
    n28a = pack28(call1)
    n28b = pack28(call2)
    ipa = 0
    ipb = 0
    ir = 0
    i3 = 1  # Type 1

    igrid4 = 0
    if info is None:
        ir = 0
        igrid4 = MAXGRID4 + 1
    elif info == "RRR":
        igrid4 = MAXGRID4 + 2
    elif info == "RR73":
        igrid4 = MAXGRID4 + 3
    elif info == "73":
        igrid4 = MAXGRID4 + 4
    elif _is_grid4(info):
        igrid4 = _grid4_to_int(info)
    else:
        s = info
        if s.startswith("R"):
            ir = 1
            s = s[1:]
        irpt = int(s)
        if -50 <= irpt <= -31:
            irpt += 101
        irpt += 35
        igrid4 = MAXGRID4 + irpt

    bits = []
    for val, width in [(n28a, 28), (ipa, 1), (n28b, 28), (ipb, 1),
                       (ir, 1), (igrid4, 15), (i3, 3)]:
        for k in range(width - 1, -1, -1):
            bits.append((val >> k) & 1)
    assert len(bits) == 77
    return bits


def encode_ft8_tones(msg77: list[int]) -> list[int]:
    """77 消息位 → 79 个音调。"""
    msg91 = add_crc14(msg77)
    cw = ldpc_encode_174_91(msg91)
    assert len(cw) == 174

    itone = [0] * NN
    for block in SYNC_BLOCKS:
        for j, pos in enumerate(block):
            itone[pos] = ICOS7[j]
    k = 6
    for j in range(ND):
        i = 3 * j
        k += 1
        if j == 29:        # 跳过第二个同步块
            k += 7
        idx = cw[i] * 4 + cw[i + 1] * 2 + cw[i + 2]
        itone[k] = GRAYMAP[idx]
    return itone


def encode_ft8_text(text: str) -> list[int]:
    """标准文本消息（如 'CQ BI4MIB OM74'）→ 79 音调。"""
    parts = text.split()
    if len(parts) < 2:
        raise ValueError(
            f"FT8 消息至少需要 2 个空格分隔字段，如 'CQ BI4MIB OM74' 或 "
            f"'BI4MIB K1ABC 73'，当前收到 {len(parts)} 个字段: {parts!r}"
        )
    if parts[0].upper() == "CQ":
        call1 = "CQ"
        call2 = parts[1]
        info = parts[2] if len(parts) >= 3 else None
    else:
        call1 = parts[0]
        call2 = parts[1]
        info = parts[2] if len(parts) >= 3 else None
    msg77 = pack77_standard(call1, call2, info)
    return encode_ft8_tones(msg77)


if __name__ == "__main__":
    tones = encode_ft8_text("CQ BI4MIB OM74")
    print(f"生成 FT8 音调数: {len(tones)}")
    for bi, block in enumerate(SYNC_BLOCKS):
        got = [tones[p] for p in block]
        print(f"同步块{bi}: {got} (期望 {ICOS7}) {'OK' if got == ICOS7 else 'FAIL'}")
