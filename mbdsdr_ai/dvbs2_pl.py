# SPDX-License-Identifier: MIT
"""DVB-S2 物理层组帧（ETSI EN 302 307-1 V1.4.1）。

本模块只处理符号级 PL 层：MODCOD/TYPE、PLHEADER、XFECFRAME SLOT、导频、
物理层复数加扰，以及 QPSK 的 FECFRAME 比特映射。它不做 IQ 同步、定时/载波
恢复、LDPC/BCH 编解码或高阶调制的比特映射。

主要依据：
- 5.4、表 8/9/10：映射位数、交织器结构、APSK 半径比；
- 5.5、图 13、表 11/12：PLFRAME、MODCOD、PLS 和导频；
- 5.5.4、图 14/15：PLHEADER 后重置的 Gold 复数加扰序列。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Mapping

import numpy as np


SOF_VALUE = 0x18D2E82
SOF_SYMBOLS = 26
PLS_SYMBOLS = 64
PLHEADER_SYMBOLS = 90
SLOT_SYMBOLS = 90
PILOT_SYMBOLS = 36
PILOT_INTERVAL_SLOTS = 16
FECFRAME_BITS = MappingProxyType({"normal": 64_800, "short": 16_200})
DUMMY_MODCOD = 0
RESERVED_MODCODS = (29, 30, 31)
PLS_MINIMUM_DISTANCE = 32
PLS_DEFAULT_MAX_DISTANCE = 8
PLS_GUARANTEED_MAX_DISTANCE = (PLS_MINIMUM_DISTANCE - 1) // 2

_GOLD_PERIOD = (1 << 18) - 1
PL_SCRAMBLING_MAX_CODE = 262_141  # 5.5.4 NOTE 2 的明确上限
PL_SCRAMBLING_MAX_SYMBOLS = 66_420  # Rn 的 i = 0..66419

SOF_BITS = np.fromiter(
    (int(bit) for bit in f"{SOF_VALUE:026b}"), dtype=np.uint8, count=SOF_SYMBOLS
)
SOF_BITS.flags.writeable = False

# 5.5.2.4，顺序为发送顺序，不是 BBFRAME 的比特扰码。
PLS_SCRAMBLING_BITS = (
    "01110001100111011000001111001001"
    "01010011010000100010110111111010"
)
PLS_GENERATOR_ROWS = (
    "01010101010101010101010101010101",
    "00110011001100110011001100110011",
    "00001111000011110000111100001111",
    "00000000111111110000000011111111",
    "00000000000000001111111111111111",
    "11111111111111111111111111111111",
)


@dataclass(frozen=True)
class ModcodInfo:
    """表 12 的一个有效 MODCOD，以及表 8/9/10 的相关参数。"""

    modcod: int
    modulation: str
    code_rate: str
    bits_per_symbol: int
    interleaver_rows_normal: int | None
    interleaver_rows_short: int | None
    gamma1: float | None = None
    gamma2: float | None = None
    short_supported: bool = True

    @property
    def interleaver_columns(self) -> int | None:
        """QPSK 无 5.3.3 比特交织器；其余列数等于每符号比特数。"""
        return None if self.modulation == "QPSK" else self.bits_per_symbol


def _mode(
    modcod: int,
    modulation: str,
    rate: str,
    gamma1: float | None = None,
    gamma2: float | None = None,
) -> ModcodInfo:
    bits = {"QPSK": 2, "8PSK": 3, "16APSK": 4, "32APSK": 5}[modulation]
    rows = {
        "QPSK": (None, None),
        "8PSK": (21_600, 5_400),
        "16APSK": (16_200, 4_050),
        "32APSK": (12_960, 3_240),
    }[modulation]
    return ModcodInfo(
        modcod=modcod,
        modulation=modulation,
        code_rate=rate,
        bits_per_symbol=bits,
        interleaver_rows_normal=rows[0],
        interleaver_rows_short=rows[1],
        gamma1=gamma1,
        gamma2=gamma2,
        short_supported=rate != "9/10",  # 表 5b 的 9/10 短帧参数为 NA
    )


# 只列可用于数据 PLFRAME 的 28 个 MODCOD；0 是 DUMMY，29..31 保留。
MODCOD_TABLE: Mapping[int, ModcodInfo] = MappingProxyType({
    mode.modcod: mode
    for mode in (
        _mode(1, "QPSK", "1/4"), _mode(2, "QPSK", "1/3"),
        _mode(3, "QPSK", "2/5"), _mode(4, "QPSK", "1/2"),
        _mode(5, "QPSK", "3/5"), _mode(6, "QPSK", "2/3"),
        _mode(7, "QPSK", "3/4"), _mode(8, "QPSK", "4/5"),
        _mode(9, "QPSK", "5/6"), _mode(10, "QPSK", "8/9"),
        _mode(11, "QPSK", "9/10"),
        _mode(12, "8PSK", "3/5"), _mode(13, "8PSK", "2/3"),
        _mode(14, "8PSK", "3/4"), _mode(15, "8PSK", "5/6"),
        _mode(16, "8PSK", "8/9"), _mode(17, "8PSK", "9/10"),
        _mode(18, "16APSK", "2/3", 3.15),
        _mode(19, "16APSK", "3/4", 2.85),
        _mode(20, "16APSK", "4/5", 2.75),
        _mode(21, "16APSK", "5/6", 2.70),
        _mode(22, "16APSK", "8/9", 2.60),
        _mode(23, "16APSK", "9/10", 2.57),
        _mode(24, "32APSK", "3/4", 2.84, 5.27),
        _mode(25, "32APSK", "4/5", 2.72, 4.87),
        _mode(26, "32APSK", "5/6", 2.64, 4.64),
        _mode(27, "32APSK", "8/9", 2.54, 4.33),
        _mode(28, "32APSK", "9/10", 2.53, 4.30),
    )
})


class PLSDecodeError(ValueError):
    """PLS 超过纠错门限、落入保留值或不满足输入约束。"""

    def __init__(
        self,
        message: str,
        *,
        distance: int | None = None,
        threshold: int | None = None,
    ) -> None:
        super().__init__(message)
        self.distance = distance
        self.threshold = threshold


@dataclass(frozen=True)
class PLSDecodeResult:
    mode: ModcodInfo
    frame_type: str
    pilots: bool
    distance: int
    second_distance: int
    threshold: int

    @property
    def modcod(self) -> int:
        return self.mode.modcod


@dataclass(frozen=True)
class LayoutSegment:
    """PLFRAME 中一个连续区间；start/stop 均为零基符号下标。"""

    kind: str  # "header"、"data" 或 "pilot"
    start: int
    length: int

    @property
    def stop(self) -> int:
        return self.start + self.length


@dataclass(frozen=True)
class PLFrameLayout:
    mode: ModcodInfo
    frame_type: str
    pilots: bool
    fecframe_bits: int
    xfecframe_symbols: int
    data_slots: int
    pilot_blocks: int
    plframe_symbols: int
    pilot_starts: tuple[int, ...]
    segments: tuple[LayoutSegment, ...]


@dataclass(frozen=True)
class PLFrame:
    symbols: np.ndarray
    header_bits: np.ndarray
    header_symbols: np.ndarray
    xfecframe_symbols: np.ndarray
    layout: PLFrameLayout
    scrambling_code: int


@dataclass(frozen=True)
class ParsedPLFrame:
    layout: PLFrameLayout
    pls: PLSDecodeResult
    sof_distance: int
    header_bits: np.ndarray
    xfecframe_symbols: np.ndarray
    pilot_symbols: np.ndarray
    scrambling_code: int
    consumed_symbols: int


@dataclass(frozen=True)
class DecodedQPSKPLFrame:
    fecframe_bits: np.ndarray
    parsed: ParsedPLFrame


def _integer(value: int, name: str) -> int:
    if isinstance(value, (bool, np.bool_)):
        raise TypeError(f"{name} 必须是整数，不能是 bool")
    try:
        return int(value.__index__())
    except (AttributeError, TypeError, ValueError) as exc:
        raise TypeError(f"{name} 必须是整数") from exc


def _frame_type(frame_type: str) -> str:
    if frame_type not in FECFRAME_BITS:
        raise ValueError("frame_type 只能是 'normal' 或 'short'")
    return frame_type


def _pilots(pilots: bool) -> bool:
    if not isinstance(pilots, (bool, np.bool_)):
        raise TypeError("pilots 必须是 bool")
    return bool(pilots)


def get_modcod(modcod: int) -> ModcodInfo:
    value = _integer(modcod, "modcod")
    try:
        return MODCOD_TABLE[value]
    except KeyError as exc:
        raise ValueError(f"MODCOD {value} 不是有效数据模式（有效值 1..28）") from exc


def _validate_mode_frame(modcod: int, frame_type: str) -> ModcodInfo:
    mode = get_modcod(modcod)
    if _frame_type(frame_type) == "short" and not mode.short_supported:
        raise ValueError(f"MODCOD {modcod}（{mode.code_rate}）没有短帧 FEC 参数")
    return mode


def plframe_layout(modcod: int, frame_type: str, pilots: bool = False) -> PLFrameLayout:
    """按表 11 和图 13 计算符号数、导频位置及全部连续布局区间。"""
    mode = _validate_mode_frame(modcod, frame_type)
    pilots = _pilots(pilots)
    n_bits = FECFRAME_BITS[frame_type]
    n_data = n_bits // mode.bits_per_symbol
    if n_data % SLOT_SYMBOLS:
        raise AssertionError("官方 FECFRAME/映射参数未形成整数 SLOT")
    slots = n_data // SLOT_SYMBOLS
    n_pilots = (slots - 1) // PILOT_INTERVAL_SLOTS if pilots else 0

    segments = [LayoutSegment("header", 0, PLHEADER_SYMBOLS)]
    pilot_starts = []
    cursor = PLHEADER_SYMBOLS
    for slot in range(1, slots + 1):
        segments.append(LayoutSegment("data", cursor, SLOT_SYMBOLS))
        cursor += SLOT_SYMBOLS
        if pilots and slot % PILOT_INTERVAL_SLOTS == 0 and slot < slots:
            segments.append(LayoutSegment("pilot", cursor, PILOT_SYMBOLS))
            pilot_starts.append(cursor)
            cursor += PILOT_SYMBOLS

    return PLFrameLayout(
        mode=mode,
        frame_type=frame_type,
        pilots=pilots,
        fecframe_bits=n_bits,
        xfecframe_symbols=n_data,
        data_slots=slots,
        pilot_blocks=n_pilots,
        plframe_symbols=cursor,
        pilot_starts=tuple(pilot_starts),
        segments=tuple(segments),
    )


def fecframe_symbol_count(modcod: int, frame_type: str) -> int:
    """FECFRAME 比特数除以每符号比特数，不含 PLHEADER/导频。"""
    return plframe_layout(modcod, frame_type, False).xfecframe_symbols


def plframe_symbol_count(modcod: int, frame_type: str, pilots: bool = False) -> int:
    """完整 PLFRAME 符号数，含 90 符号 PLHEADER 和可选导频。"""
    return plframe_layout(modcod, frame_type, pilots).plframe_symbols


def _bits_array(bits: np.ndarray, name: str) -> np.ndarray:
    array = np.asarray(bits)
    if array.ndim != 1:
        raise ValueError(f"{name} 必须是一维比特序列")
    if not np.all((array == 0) | (array == 1)):
        raise ValueError(f"{name} 只能含 0/1")
    return array.astype(np.uint8, copy=False)


def _symbols_array(symbols: np.ndarray, name: str) -> np.ndarray:
    try:
        array = np.asarray(symbols, dtype=np.complex128)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是一维复数符号序列") from exc
    if array.ndim != 1:
        raise ValueError(f"{name} 必须是一维复数符号序列")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} 含 NaN/Inf")
    return array


def _build_pls_codebook() -> np.ndarray:
    rows = np.array(
        [[int(bit) for bit in row] for row in PLS_GENERATOR_ROWS], dtype=np.uint8
    )
    scrambling = np.fromiter(
        (int(bit) for bit in PLS_SCRAMBLING_BITS), dtype=np.uint8, count=PLS_SYMBOLS
    )
    book = np.empty((128, PLS_SYMBOLS), dtype=np.uint8)
    for index in range(128):
        # b1..b6 = MODCOD(5) + TYPE MSB；b7 = TYPE LSB(pilots)。
        input_bits = np.fromiter(
            ((index >> (6 - bit)) & 1 for bit in range(7)),
            dtype=np.uint8,
            count=7,
        )
        y = np.bitwise_xor.reduce(rows[input_bits[:6].astype(bool)], axis=0) \
            if np.any(input_bits[:6]) else np.zeros(32, dtype=np.uint8)
        book[index, 0::2] = y
        book[index, 1::2] = y ^ input_bits[6]
        book[index] ^= scrambling
    book.flags.writeable = False
    return book


_PLS_CODEBOOK = _build_pls_codebook()


def encode_pls(modcod: int, frame_type: str, pilots: bool = False) -> np.ndarray:
    """生成 64 比特 PLS；输入顺序为 MODCOD(5)+TYPE(2)，MSB 先发。"""
    _validate_mode_frame(modcod, frame_type)
    pilots = _pilots(pilots)
    index = (_integer(modcod, "modcod") << 2) | (int(frame_type == "short") << 1) | int(pilots)
    return _PLS_CODEBOOK[index].copy()


def decode_pls(pls_bits: np.ndarray, max_distance: int = PLS_DEFAULT_MAX_DISTANCE) -> PLSDecodeResult:
    """最近码字译码并报告距离；默认仅接受汉明距离不超过 8 的 PLS。

    dmin=32 保证 0..15 的最近码字唯一。门限是实现策略而非标准规定值；
    拒绝时不返回猜测的 MODCOD，保留 MODCOD/DUMMY 也明确拒绝。
    """
    threshold = _integer(max_distance, "max_distance")
    if not 0 <= threshold <= PLS_GUARANTEED_MAX_DISTANCE:
        raise ValueError(f"max_distance 必须在 0..{PLS_GUARANTEED_MAX_DISTANCE}")
    bits = _bits_array(pls_bits, "pls_bits")
    if len(bits) != PLS_SYMBOLS:
        raise PLSDecodeError(f"PLS 必须恰好 {PLS_SYMBOLS} 比特", threshold=threshold)
    distances = np.count_nonzero(_PLS_CODEBOOK != bits, axis=1)
    best = int(np.argmin(distances))
    distance = int(distances[best])
    second_distance = int(np.min(np.delete(distances, best)))
    if distance > threshold:
        raise PLSDecodeError(
            f"PLS 最近距离 {distance} 超过有效门限 {threshold}（次近距离 {second_distance}）",
            distance=distance,
            threshold=threshold,
        )
    modcod = best >> 2
    if modcod not in MODCOD_TABLE:
        raise PLSDecodeError(
            f"PLS 指向 DUMMY/保留 MODCOD {modcod}，不是数据 PLFRAME",
            distance=distance,
            threshold=threshold,
        )
    frame_type = "short" if best & 2 else "normal"
    if not MODCOD_TABLE[modcod].short_supported and frame_type == "short":
        raise PLSDecodeError(
            f"PLS 给出无 FEC 参数的 short + {MODCOD_TABLE[modcod].code_rate} 组合",
            distance=distance,
            threshold=threshold,
        )
    return PLSDecodeResult(
        mode=MODCOD_TABLE[modcod],
        frame_type=frame_type,
        pilots=bool(best & 1),
        distance=distance,
        second_distance=second_distance,
        threshold=threshold,
    )


def build_plheader_bits(modcod: int, frame_type: str, pilots: bool = False) -> np.ndarray:
    return np.concatenate((SOF_BITS, encode_pls(modcod, frame_type, pilots))).astype(np.uint8)


def modulate_plheader(header_bits: np.ndarray) -> np.ndarray:
    """按 5.5.2 将 90 比特映射为 90 个 pi/2-BPSK 符号。"""
    bits = _bits_array(header_bits, "header_bits")
    if len(bits) != PLHEADER_SYMBOLS:
        raise ValueError(f"PLHEADER 必须恰好 {PLHEADER_SYMBOLS} 比特")
    amplitude = 1.0 / np.sqrt(2.0)
    levels = amplitude * (1 - 2 * bits.astype(np.float64))
    symbols = np.empty(PLHEADER_SYMBOLS, dtype=np.complex128)
    symbols[0::2] = levels[0::2] + 1j * levels[0::2]
    symbols[1::2] = -levels[1::2] + 1j * levels[1::2]
    return symbols


def demodulate_plheader(header_symbols: np.ndarray) -> np.ndarray:
    """对 90 个 PLHEADER 符号作硬判决；不包含相位/频率同步。"""
    symbols = _symbols_array(header_symbols, "header_symbols")
    if len(symbols) != PLHEADER_SYMBOLS:
        raise ValueError(f"PLHEADER 必须恰好 {PLHEADER_SYMBOLS} 符号")
    bits = np.empty(PLHEADER_SYMBOLS, dtype=np.uint8)
    bits[0::2] = (symbols[0::2].real < 0).astype(np.uint8)
    bits[1::2] = (symbols[1::2].real >= 0).astype(np.uint8)
    return bits


def map_qpsk_fecframe(fecframe_bits: np.ndarray, frame_type: str) -> np.ndarray:
    """图 9 的绝对 Gray QPSK：I 由 MSB、Q 由 LSB 决定，单位平均能量。"""
    frame_type = _frame_type(frame_type)
    bits = _bits_array(fecframe_bits, "fecframe_bits")
    if len(bits) != FECFRAME_BITS[frame_type]:
        raise ValueError(f"{frame_type} FECFRAME 必须恰好 {FECFRAME_BITS[frame_type]} 比特")
    amplitude = 1.0 / np.sqrt(2.0)
    levels = amplitude * (1 - 2 * bits.reshape(-1, 2).astype(np.float64))
    return levels[:, 0] + 1j * levels[:, 1]


def demap_qpsk_xfecframe(symbols: np.ndarray, frame_type: str) -> np.ndarray:
    """QPSK 硬解映射；输入必须是已解扰且去除导频的 XFECFRAME。"""
    frame_type = _frame_type(frame_type)
    symbols = _symbols_array(symbols, "symbols")
    expected = FECFRAME_BITS[frame_type] // 2
    if len(symbols) != expected:
        raise ValueError(f"{frame_type} XFECFRAME 必须恰好 {expected} 个 QPSK 符号")
    bits = np.empty((len(symbols), 2), dtype=np.uint8)
    bits[:, 0] = (symbols.real < 0).astype(np.uint8)
    bits[:, 1] = (symbols.imag < 0).astype(np.uint8)
    return bits.reshape(-1)


@lru_cache(maxsize=1)
def _gold_sequences() -> tuple[np.ndarray, np.ndarray]:
    """直接按 5.5.4 的 18 阶递推式生成完整 x/y m-sequence。"""
    x = np.zeros(_GOLD_PERIOD, dtype=np.uint8)
    y = np.ones(_GOLD_PERIOD, dtype=np.uint8)
    x[0] = 1
    for i in range(_GOLD_PERIOD - 18):
        x[i + 18] = x[i + 7] ^ x[i]
        y[i + 18] = y[i + 10] ^ y[i + 7] ^ y[i + 5] ^ y[i]
    x.flags.writeable = False
    y.flags.writeable = False
    return x, y


def pl_scrambling_sequence(length: int, scrambling_code: int = 0) -> np.ndarray:
    """生成 exp(j*Rn*pi/2)，用于 PLHEADER 后的数据和导频。"""
    length = _integer(length, "length")
    code = _integer(scrambling_code, "scrambling_code")
    if not 0 <= length <= PL_SCRAMBLING_MAX_SYMBOLS:
        raise ValueError(f"length 必须在 0..{PL_SCRAMBLING_MAX_SYMBOLS}")
    if not 0 <= code <= PL_SCRAMBLING_MAX_CODE:
        raise ValueError(f"scrambling_code 必须在 0..{PL_SCRAMBLING_MAX_CODE}")
    x, y = _gold_sequences()
    indices = np.arange(length, dtype=np.int64)
    shifted = indices + 131_072
    z_now = x[(indices + code) % _GOLD_PERIOD] ^ y[indices]
    z_shifted = x[(shifted + code) % _GOLD_PERIOD] ^ y[shifted]
    rotations = 2 * z_shifted.astype(np.uint8) + z_now
    lookup = np.array([1 + 0j, 1j, -1 + 0j, -1j], dtype=np.complex128)
    return lookup[rotations]


def build_plframe(
    xfecframe_symbols: np.ndarray,
    modcod: int,
    frame_type: str,
    pilots: bool = False,
    scrambling_code: int = 0,
) -> PLFrame:
    """通用符号组帧：输入已映射 XFECFRAME，输出已物理层加扰 PLFRAME。

    高阶调制可在有独立可信映射器时复用本接口；本函数不声称验证其星座映射。
    导频先在每 16 个数据 SLOT 后插入，再与数据一起从同一重置序列加扰。
    """
    layout = plframe_layout(modcod, frame_type, pilots)
    data = _symbols_array(xfecframe_symbols, "xfecframe_symbols")
    if len(data) != layout.xfecframe_symbols:
        raise ValueError(
            f"XFECFRAME 长度错误：需 {layout.xfecframe_symbols} 符号，实收 {len(data)}"
        )
    header_bits = build_plheader_bits(modcod, frame_type, pilots)
    header_symbols = modulate_plheader(header_bits)
    body = np.empty(layout.plframe_symbols - PLHEADER_SYMBOLS, dtype=np.complex128)
    pilot_value = (1 + 1j) / np.sqrt(2.0)
    data_segment = iter(segment for segment in layout.segments if segment.kind == "data")
    for slot in range(layout.data_slots):
        segment = next(data_segment)
        start = segment.start - PLHEADER_SYMBOLS
        body[start:start + SLOT_SYMBOLS] = data[
            slot * SLOT_SYMBOLS:(slot + 1) * SLOT_SYMBOLS
        ]
    for segment in layout.segments:
        if segment.kind == "pilot":
            start = segment.start - PLHEADER_SYMBOLS
            body[start:start + PILOT_SYMBOLS] = pilot_value
    sequence = pl_scrambling_sequence(len(body), scrambling_code)
    scrambled_body = body * sequence
    return PLFrame(
        symbols=np.concatenate((header_symbols, scrambled_body)),
        header_bits=header_bits,
        header_symbols=header_symbols,
        xfecframe_symbols=data.copy(),
        layout=layout,
        scrambling_code=_integer(scrambling_code, "scrambling_code"),
    )


def encode_qpsk_plframe(
    fecframe_bits: np.ndarray,
    modcod: int,
    frame_type: str,
    pilots: bool = False,
    scrambling_code: int = 0,
) -> PLFrame:
    """QPSK FECFRAME 比特到完整 PLFRAME 符号的库层闭环入口。"""
    mode = _validate_mode_frame(modcod, frame_type)
    if mode.modulation != "QPSK":
        raise NotImplementedError(
            f"{mode.modulation} 比特映射/解映射尚未实现；请只向 build_plframe 提供已验证的 XFECFRAME"
        )
    return build_plframe(
        map_qpsk_fecframe(fecframe_bits, frame_type),
        modcod,
        frame_type,
        pilots,
        scrambling_code,
    )


def parse_plframe(
    symbols: np.ndarray,
    *,
    scrambling_code: int = 0,
    max_sof_distance: int = 0,
    max_pls_distance: int = PLS_DEFAULT_MAX_DISTANCE,
    allow_trailing: bool = False,
) -> ParsedPLFrame:
    """解析一个从 SOF 开始的 PLFRAME，返回解扰后的 XFECFRAME 和导频。

    默认要求输入恰好一帧；流式调用方可先读 90 符号确定布局，再切片，或用
    allow_trailing=True 并根据 consumed_symbols 前进。这里不做 SOF 搜索。
    """
    symbols = _symbols_array(symbols, "symbols")
    sof_threshold = _integer(max_sof_distance, "max_sof_distance")
    if not 0 <= sof_threshold <= SOF_SYMBOLS:
        raise ValueError(f"max_sof_distance 必须在 0..{SOF_SYMBOLS}")
    if len(symbols) < PLHEADER_SYMBOLS:
        raise ValueError(f"至少需要 {PLHEADER_SYMBOLS} 个 PLHEADER 符号")
    header_bits = demodulate_plheader(symbols[:PLHEADER_SYMBOLS])
    sof_distance = int(np.count_nonzero(header_bits[:SOF_SYMBOLS] != SOF_BITS))
    if sof_distance > sof_threshold:
        raise PLSDecodeError(
            f"SOF 汉明距离 {sof_distance} 超过门限 {sof_threshold}",
            distance=sof_distance,
            threshold=sof_threshold,
        )
    pls = decode_pls(header_bits[SOF_SYMBOLS:], max_pls_distance)
    layout = plframe_layout(pls.modcod, pls.frame_type, pls.pilots)
    if len(symbols) < layout.plframe_symbols:
        raise ValueError(f"PLFRAME 截断：需 {layout.plframe_symbols} 符号，实收 {len(symbols)}")
    if len(symbols) > layout.plframe_symbols and not allow_trailing:
        raise ValueError(
            f"输入含尾随符号：帧长 {layout.plframe_symbols}，实收 {len(symbols)}；"
            "流式解析请显式 allow_trailing=True"
        )
    body = symbols[PLHEADER_SYMBOLS:layout.plframe_symbols]
    descrambled = body * np.conj(pl_scrambling_sequence(len(body), scrambling_code))
    data_parts = []
    pilot_parts = []
    for segment in layout.segments:
        if segment.kind == "header":
            continue
        start = segment.start - PLHEADER_SYMBOLS
        part = descrambled[start:start + segment.length]
        if segment.kind == "data":
            data_parts.append(part.copy())
        else:
            pilot_parts.append(part.copy())
    return ParsedPLFrame(
        layout=layout,
        pls=pls,
        sof_distance=sof_distance,
        header_bits=header_bits,
        xfecframe_symbols=np.concatenate(data_parts),
        pilot_symbols=(np.concatenate(pilot_parts) if pilot_parts
                       else np.empty(0, dtype=np.complex128)),
        scrambling_code=_integer(scrambling_code, "scrambling_code"),
        consumed_symbols=layout.plframe_symbols,
    )


def decode_qpsk_plframe(
    symbols: np.ndarray,
    *,
    scrambling_code: int = 0,
    max_sof_distance: int = 0,
    max_pls_distance: int = PLS_DEFAULT_MAX_DISTANCE,
    allow_trailing: bool = False,
) -> DecodedQPSKPLFrame:
    """解析并硬解映射 QPSK PLFRAME；不执行 LDPC/BCH 译码。"""
    parsed = parse_plframe(
        symbols,
        scrambling_code=scrambling_code,
        max_sof_distance=max_sof_distance,
        max_pls_distance=max_pls_distance,
        allow_trailing=allow_trailing,
    )
    if parsed.layout.mode.modulation != "QPSK":
        raise NotImplementedError(
            f"{parsed.layout.mode.modulation} 比特解映射尚未实现；可使用 parse_plframe 取 XFECFRAME"
        )
    return DecodedQPSKPLFrame(
        fecframe_bits=demap_qpsk_xfecframe(
            parsed.xfecframe_symbols, parsed.layout.frame_type
        ),
        parsed=parsed,
    )


__all__ = [
    "DUMMY_MODCOD", "DecodedQPSKPLFrame", "FECFRAME_BITS", "LayoutSegment",
    "MODCOD_TABLE", "ModcodInfo", "PILOT_INTERVAL_SLOTS", "PILOT_SYMBOLS",
    "PLFrame", "PLFrameLayout", "PLHEADER_SYMBOLS", "PLSDecodeError",
    "PLSDecodeResult", "PLS_DEFAULT_MAX_DISTANCE", "PLS_GUARANTEED_MAX_DISTANCE",
    "PLS_MINIMUM_DISTANCE", "PLS_SYMBOLS", "PL_SCRAMBLING_MAX_CODE",
    "PL_SCRAMBLING_MAX_SYMBOLS", "ParsedPLFrame", "RESERVED_MODCODS",
    "SLOT_SYMBOLS", "SOF_BITS", "SOF_SYMBOLS", "SOF_VALUE", "build_plframe",
    "build_plheader_bits", "decode_pls", "decode_qpsk_plframe",
    "demap_qpsk_xfecframe", "demodulate_plheader", "encode_pls",
    "encode_qpsk_plframe", "fecframe_symbol_count", "get_modcod",
    "map_qpsk_fecframe", "modulate_plheader", "parse_plframe",
    "pl_scrambling_sequence", "plframe_layout", "plframe_symbol_count",
]
