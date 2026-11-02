# SPDX-License-Identifier: MIT
"""经典 WSPR Type 1 消息的位/符号层编解码（独立 MIT 实现）。

范围仅为标准 ``呼号 + 4 字符网格 + dBm 功率`` 消息：50 bit 源数据加
31 个零尾位，经 K=32、r=1/2 卷积码得到 162 bit，再交织并与已知同步向量
合并为 162 个 4FSK 符号。Type 2/3、FST4W、音频同步和弱信号捕获不在本模块内。

协议事实证据（归档于 ``standards/wsjt``）：
- ``wspr_official_manual.html`` 6136-6190 行：50/162 bit、K=32、r=1/2、
  数据为符号 MSB、同步为 LSB；
- ``wspr_upstream_fano.c`` 46-57 行：Layland-Lushbaugh 生成多项式及输出顺序；
- ``wspr_upstream_wsprsim_utils.c`` 42-79、145-178、255-307 行：打包、交织、
  同步向量、零尾位和 ``2 * data + sync``；
- ``wspr_coding_process_g4jnt.txt`` 93-141、178-231 行：上述机制的公开文字描述。

上游 GPL 源码仅用于核对协议事实，未复制进本模块。译码器是独立的有界堆栈
顺序搜索，不是 WSJT-X 的音频/Fano 接收链。WSPR Type 1 没有 CRC；全零 LLR
会诚实返回 ``None``，但任意非零输入得到合法码字不等于已检测到真实信号。
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
import numbers
import operator
from typing import List, Optional, Sequence, Tuple

MESSAGE_BITS = 50
TAIL_BITS = 31
CONSTRAINT_LENGTH = 32
CODE_BITS = 2 * (MESSAGE_BITS + TAIL_BITS)
GENERATOR_POLYNOMIALS = (0xF2D05351, 0xE4613C47)
MAX_CALLSIGN = 262_177_560
VALID_POWER_DBM = tuple(p for p in range(61) if p % 10 in (0, 3, 7))

# 官方 wsprsim_utils.c 169-178 行的 162 bit pr3 向量（协议常量，不是代码）。
SYNC_VECTOR: Tuple[int, ...] = (
    1, 1, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 1, 0,
    0, 1, 0, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0, 1,
    0, 0, 0, 0, 0, 0, 1, 0, 1, 1, 0, 0, 1, 1, 0, 1, 0, 0, 0, 1,
    1, 0, 1, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 1,
    0, 0, 1, 0, 1, 1, 0, 0, 0, 1, 1, 0, 1, 0, 1, 0, 0, 0, 1, 0,
    0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 1, 1, 1, 0, 1, 1, 0, 0, 1, 1,
    0, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1, 1,
    0, 0, 0, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 1, 0, 0, 0, 1, 1, 0,
    0, 0,
)
assert len(SYNC_VECTOR) == CODE_BITS == 162

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ "


@dataclass(frozen=True)
class WsprMessage:
    """经典 WSPR Type 1 的三个用户字段。"""

    callsign: str
    grid4: str
    power_dbm: int


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数，不能是布尔值")
    try:
        return operator.index(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError(f"{name} 必须是整数") from exc


def _fixed_ints(values: Sequence[int], length: int, low: int, high: int,
                name: str) -> List[int]:
    if isinstance(values, (str, bytes, bytearray)):
        raise ValueError(f"{name} 必须是长度为 {length} 的整数序列")
    try:
        size = len(values)
    except TypeError as exc:
        raise ValueError(f"{name} 必须是长度为 {length} 的整数序列") from exc
    if size != length:
        raise ValueError(f"{name} 必须恰好为 {length} 项，实际为 {size}")
    result = [_integer(v, name) for v in values]
    if any(v < low or v > high for v in result):
        raise ValueError(f"{name} 的每项必须位于 {low}..{high}")
    return result


def _bits(values: Sequence[int], length: int, name: str) -> List[int]:
    return _fixed_ints(values, length, 0, 1, name)


def _msb_bits(value: int, width: int) -> List[int]:
    return [(value >> shift) & 1 for shift in range(width - 1, -1, -1)]


def _from_msb_bits(bits: Sequence[int]) -> int:
    value = 0
    for bit in bits:
        value = (value << 1) | bit
    return value


def pack_callsign(callsign: str) -> int:
    """把 WSPR 标准呼号打包为 28 bit 整数。

    区位数字必须位于原呼号第 2 或第 3 个字符；前者左侧补一个空格。
    其余尾部字符只能为 A-Z，短呼号右侧补空格到 6 字符。
    """
    if not isinstance(callsign, str):
        raise ValueError("呼号必须是字符串")
    call = callsign.strip().upper()
    if not 2 <= len(call) <= 6:
        raise ValueError("标准呼号去除首尾空格后须含 2..6 个字符")
    if any(ch not in _ALPHABET[:-1] for ch in call):
        raise ValueError("呼号只能含 ASCII A-Z 和 0-9，且不能有内部空格")
    digit_positions = [i for i, ch in enumerate(call) if ch in "0123456789"]
    if len(digit_positions) != 1 or digit_positions[0] not in (1, 2):
        raise ValueError("标准呼号必须恰有一个区位数字，位于第 2 或第 3 字符")
    digit_at = digit_positions[0]
    if any(ch not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
           for ch in call[:digit_at] + call[digit_at + 1:]):
        raise ValueError("区位数字前后只能使用 ASCII 字母")
    padded = (" " + call if digit_at == 1 else call).ljust(6)
    codes = [_ALPHABET.index(ch) for ch in padded]
    n = codes[0]
    n = n * 36 + codes[1]
    n = n * 10 + codes[2]
    for code in codes[3:]:
        n = n * 27 + code - 10
    if not 0 <= n < MAX_CALLSIGN:
        raise ValueError("呼号编码超出 WSPR 28 bit 标准呼号空间")
    return n


def unpack_callsign(value: int) -> str:
    """解包 28 bit 标准呼号；保留空间或其他消息类型会报错。"""
    n = _integer(value, "呼号编码")
    if not 0 <= n < MAX_CALLSIGN:
        raise ValueError("不是有效的 WSPR 标准呼号编码")
    remainder = n
    chars = [""] * 6
    for index in range(5, 2, -1):
        chars[index] = _ALPHABET[remainder % 27 + 10]
        remainder //= 27
    chars[2] = _ALPHABET[remainder % 10]
    remainder //= 10
    chars[1] = _ALPHABET[remainder % 36]
    remainder //= 36
    chars[0] = _ALPHABET[remainder]
    call = "".join(chars).strip()
    # 排除 28 bit 空间中不能由标准编码器产生的非规范表示。
    if pack_callsign(call) != n:
        raise ValueError("不是规范的 WSPR 标准呼号编码")
    return call


def pack_grid4_power(grid4: str, power_dbm: int) -> int:
    """把 4 字符 Maidenhead 网格和 Type 1 功率打包为 22 bit 整数。"""
    if not isinstance(grid4, str):
        raise ValueError("网格必须是字符串")
    grid = grid4.strip().upper()
    if len(grid) != 4 or any(ch not in "ABCDEFGHIJKLMNOPQR" for ch in grid[:2]) \
            or any(ch not in "0123456789" for ch in grid[2:]):
        raise ValueError("网格须为 A-R、A-R 后接两个十进制数字")
    power = _integer(power_dbm, "功率")
    if power not in VALID_POWER_DBM:
        raise ValueError("Type 1 功率须为 0..60 dBm 且个位为 0、3 或 7")
    locator = ((179 - 10 * (ord(grid[0]) - 65) - int(grid[2])) * 180
               + 10 * (ord(grid[1]) - 65) + int(grid[3]))
    return locator * 128 + power + 64


def pack_message(callsign: str, grid4: str, power_dbm: int) -> List[int]:
    """返回标准 Type 1 消息的 50 个 MSB-first 源位（28+15+7）。"""
    n = pack_callsign(callsign)
    m = pack_grid4_power(grid4, power_dbm)
    return _msb_bits(n, 28) + _msb_bits(m, 22)


def pack_message_bytes(callsign: str, grid4: str, power_dbm: int) -> bytes:
    """返回官方 ``wsprsim -d`` 形式的 11 字节（50 源位+31 尾零+7 填充零）。"""
    padded = pack_message(callsign, grid4, power_dbm) + [0] * (TAIL_BITS + 7)
    return bytes(_from_msb_bits(padded[i:i + 8]) for i in range(0, 88, 8))


def unpack_message(message_bits: Sequence[int]) -> WsprMessage:
    """解包 50 bit Type 1；非标准消息、保留编码及非法功率均明确报错。"""
    bits = _bits(message_bits, MESSAGE_BITS, "消息位")
    n = _from_msb_bits(bits[:28])
    m = _from_msb_bits(bits[28:])
    callsign = unpack_callsign(n)
    locator = m >> 7
    power = (m & 0x7F) - 64
    if locator >= 32400 or power not in VALID_POWER_DBM:
        raise ValueError("50 bit 内容不是有效的 WSPR Type 1 网格/功率消息")
    first, second = divmod(locator, 180)
    reversed_first = 179 - first
    field_a, digit_a = divmod(reversed_first, 10)
    field_b, digit_b = divmod(second, 10)
    if not (0 <= field_a < 18 and 0 <= field_b < 18):
        raise ValueError("网格编码超出 A-R 范围")
    grid = f"{chr(65 + field_a)}{chr(65 + field_b)}{digit_a}{digit_b}"
    # 对全部 50 bit 做规范重编码检查，避免接受非规范尾部组合。
    if pack_message(callsign, grid, power) != bits:
        raise ValueError("50 bit 内容不是规范的 WSPR Type 1 编码")
    return WsprMessage(callsign, grid, power)


def convolutional_encode(message_bits: Sequence[int]) -> List[int]:
    """50 源位追加 31 个零尾位，输出 POLY1/POLY2 交替的 162 bit。"""
    bits = _bits(message_bits, MESSAGE_BITS, "消息位")
    state = 0
    output: List[int] = []
    for bit in bits + [0] * TAIL_BITS:
        state = ((state << 1) | bit) & 0xFFFFFFFF
        for polynomial in GENERATOR_POLYNOMIALS:
            output.append((state & polynomial).bit_count() & 1)
    return output


def _reversed_address(address: int) -> int:
    return int(f"{address:08b}"[::-1], 2)


def interleave(code_bits: Sequence[int]) -> List[int]:
    """按 8 bit 地址反转规则交织 162 bit，D[J]=S[P]。"""
    source = _bits(code_bits, CODE_BITS, "卷积码位")
    destination = [0] * CODE_BITS
    p = 0
    for address in range(256):
        j = _reversed_address(address)
        if j < CODE_BITS:
            destination[j] = source[p]
            p += 1
    if p != CODE_BITS:
        raise RuntimeError("WSPR 交织表未覆盖 162 个位置")
    return destination


def deinterleave(channel_bits: Sequence[int]) -> List[int]:
    """执行 :func:`interleave` 的逆置换。"""
    source = _bits(channel_bits, CODE_BITS, "交织码位")
    destination = [0] * CODE_BITS
    p = 0
    for address in range(256):
        j = _reversed_address(address)
        if j < CODE_BITS:
            destination[p] = source[j]
            p += 1
    if p != CODE_BITS:
        raise RuntimeError("WSPR 解交织表未覆盖 162 个位置")
    return destination


def merge_sync(channel_bits: Sequence[int]) -> List[int]:
    """把 162 个交织数据位与同步向量合并为 ``2*data+sync`` 符号。"""
    bits = _bits(channel_bits, CODE_BITS, "交织数据位")
    return [2 * bit + sync for bit, sync in zip(bits, SYNC_VECTOR)]


def encode_message(callsign: str, grid4: str, power_dbm: int) -> List[int]:
    """标准 Type 1 文本字段到 162 个 0..3 信道符号的完整位级编码。"""
    return merge_sync(interleave(convolutional_encode(
        pack_message(callsign, grid4, power_dbm))))


def sync_mismatch_count(symbols: Sequence[int]) -> int:
    """返回硬符号 LSB 与已知同步向量不一致的数量。"""
    values = _fixed_ints(symbols, CODE_BITS, 0, 3, "信道符号")
    return sum((symbol & 1) != sync for symbol, sync in zip(values, SYNC_VECTOR))


def symbols_to_llrs(symbols: Sequence[int], confidence: float = 4.0) -> List[float]:
    """把 0..3 硬符号的 MSB 转为交织域 LLR（正数倾向 0，负数倾向 1）。

    该函数不利用 LSB 做时间/频率同步；同步质量可由
    :func:`sync_mismatch_count` 单独报告。
    """
    values = _fixed_ints(symbols, CODE_BITS, 0, 3, "信道符号")
    if isinstance(confidence, bool) or not isinstance(confidence, numbers.Real) \
            or not math.isfinite(float(confidence)) or confidence <= 0:
        raise ValueError("confidence 必须是有限正数")
    return [-float(confidence) if symbol & 2 else float(confidence)
            for symbol in values]


def _llrs(values: Sequence[float]) -> List[float]:
    if isinstance(values, (str, bytes, bytearray)):
        raise ValueError("LLR 必须是 162 项实数序列")
    try:
        size = len(values)
    except TypeError as exc:
        raise ValueError("LLR 必须是 162 项实数序列") from exc
    if size != CODE_BITS:
        raise ValueError(f"LLR 必须恰好为 {CODE_BITS} 项，实际为 {size}")
    result: List[float] = []
    for value in values:
        if not isinstance(value, numbers.Real):
            raise ValueError("LLR 每项必须是实数")
        converted = float(value)
        if not math.isfinite(converted):
            raise ValueError("LLR 不能含 NaN 或无穷大")
        result.append(converted)
    return result


def decode_convolutional(interleaved_llr: Sequence[float],
                         max_expansions: int = 100_000) -> Optional[List[int]]:
    """用有界堆栈顺序搜索，从 162 个交织域 LLR 恢复 50 个源位。

    搜索仅扩展末 31 位为零的路径，因此成功结果必属于合法卷积码尾结构。
    达到 ``max_expansions`` 或全零（无信息）输入返回 ``None``，不伪造译码。
    这是位层 FEC 译码，不包含信号存在性判决，也不宣称达到官方 Fano 的弱信号性能。
    """
    llr = deinterleave_soft(interleaved_llr)
    limit = _integer(max_expansions, "max_expansions")
    if limit <= 0:
        raise ValueError("max_expansions 必须是正整数")
    if not any(value != 0.0 for value in llr):
        return None

    # 节点为 (-路径度量, 单调序号, 深度, 32 bit 状态, 已走路径)。
    # 度量是与 LLR 的相关量，越大越好；路径仅在末 31 步扩展零分支。
    heap: List[Tuple[float, int, int, int, bytes]] = [(0.0, 0, 0, 0, b"")]
    serial = 0
    expansions = 0
    while heap and expansions < limit:
        negative_metric, _, depth, state, path = heapq.heappop(heap)
        metric = -negative_metric
        expansions += 1
        if depth == MESSAGE_BITS + TAIL_BITS:
            return list(path[:MESSAGE_BITS])
        branches = (0,) if depth >= MESSAGE_BITS else (0, 1)
        for bit in branches:
            next_state = ((state << 1) | bit) & 0xFFFFFFFF
            expected_0 = (next_state & GENERATOR_POLYNOMIALS[0]).bit_count() & 1
            expected_1 = (next_state & GENERATOR_POLYNOMIALS[1]).bit_count() & 1
            observed_0 = llr[2 * depth]
            observed_1 = llr[2 * depth + 1]
            branch_metric = ((-observed_0 if expected_0 else observed_0)
                             + (-observed_1 if expected_1 else observed_1))
            serial += 1
            heapq.heappush(heap, (-(metric + branch_metric), serial, depth + 1,
                                  next_state, path + bytes((bit,))))
    return None


def deinterleave_soft(interleaved_llr: Sequence[float]) -> List[float]:
    """软值形式的 :func:`deinterleave`；保留零擦除和 LLR 幅度。"""
    source = _llrs(interleaved_llr)
    destination = [0.0] * CODE_BITS
    p = 0
    for address in range(256):
        j = _reversed_address(address)
        if j < CODE_BITS:
            destination[p] = source[j]
            p += 1
    return destination


def decode_symbols(symbols: Sequence[int],
                   max_expansions: int = 100_000) -> Optional[List[int]]:
    """从 162 个硬 4FSK 符号尝试恢复 50 源位；失败返回 ``None``。"""
    return decode_convolutional(symbols_to_llrs(symbols), max_expansions)


def decode_message(interleaved_llr: Sequence[float],
                   max_expansions: int = 100_000) -> Optional[WsprMessage]:
    """软位到 Type 1 字段；FEC 搜索失败或字段非法均返回 ``None``。"""
    bits = decode_convolutional(interleaved_llr, max_expansions)
    if bits is None:
        return None
    try:
        return unpack_message(bits)
    except ValueError:
        return None


__all__ = [
    "CODE_BITS", "CONSTRAINT_LENGTH", "GENERATOR_POLYNOMIALS", "MESSAGE_BITS",
    "SYNC_VECTOR", "TAIL_BITS", "VALID_POWER_DBM", "WsprMessage",
    "convolutional_encode", "decode_convolutional", "decode_message",
    "decode_symbols", "deinterleave", "deinterleave_soft", "encode_message",
    "interleave", "merge_sync", "pack_callsign", "pack_grid4_power",
    "pack_message", "pack_message_bytes", "symbols_to_llrs",
    "sync_mismatch_count", "unpack_callsign", "unpack_message",
]
