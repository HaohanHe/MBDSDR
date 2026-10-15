# SPDX-License-Identifier: MIT
"""频谱快照 PNG 渲染核心（SDR 侧登记缺口：export_spectrum_snapshot）。

无 Qt/无 matplotlib 依赖：stdlib zlib 手写 PNG 编码 + numpy 光栅化。
本模块只做**渲染**，不做文件决策/工具门（那是 MCP 层的事）。

- render_spectrum_png(freqs_hz, db_values, width, height, dynamic_range_db)
  -> bytes：白底、坐标轴框、蓝色 dB 折线；dB 轴按 [max−range, max] 自
  适应，越界点钳到边（如实，不丢点）；频率轴线性标注。
- 刻度标注：dB 轴 4 档文字（内置 5x7 点阵字体，数字/字母/.- 够用）；
  频率轴用工程单位（k/M/G）标注起止与中间一档。字体仅覆盖本功能
  用到的字符集，缺字符显式 '?' 不静默空白。

诚实边界：单色折线图（瀑布图/颜色图不在本批）；字体为 5x7 点阵，
抗锯齿无（文档登记）；PNG 输出为 8-bit RGB。
"""
from __future__ import annotations

import struct
import zlib
from typing import Dict, List, Tuple

import numpy as np

__all__ = ["render_spectrum_png"]

#: 5x7 点阵字体（本功能字符集：0-9 A-Z . - 空格 ? k M G H z d B）
_FONT: Dict[str, Tuple[str, ...]] = {
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "d": ("00001", "00001", "01101", "10011", "10001", "10011", "01101"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "k": ("10000", "10100", "11000", "10100", "10001", "10001", "10001"),
    "z": ("11111", "00010", "00100", "01000", "10000", "10001", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    " ": ("00000",) * 7,
    "?": ("01110", "10001", "00010", "00100", "00100", "00000", "00100"),
}


def _draw_char(img: np.ndarray, x: int, y: int, ch: str,
               color: Tuple[int, int, int]) -> None:
    glyph = _FONT.get(ch, _FONT["?"])
    for row, bits in enumerate(glyph):
        for col, b in enumerate(bits):
            if b == "1":
                yy, xx = y + row, x + col
                if 0 <= yy < img.shape[0] and 0 <= xx < img.shape[1]:
                    img[yy, xx] = color


def _draw_text(img: np.ndarray, x: int, y: int, s: str,
               color: Tuple[int, int, int] = (0, 0, 0)) -> None:
    for i, ch in enumerate(s):
        _draw_char(img, x + i * 6, y, ch, color)


def _eng(freq_hz: float) -> str:
    """工程单位标注：1234567 -> '1.23M'。"""
    f = float(freq_hz)
    for div, suf in ((1e9, "G"), (1e6, "M"), (1e3, "k")):
        if abs(f) >= div:
            return f"{f / div:.2f}".rstrip("0").rstrip(".") + suf
    return f"{f:.0f}"


def _png_rgb(img: np.ndarray) -> bytes:
    """8-bit RGB PNG 编码（stdlib zlib，无外部依赖）。"""
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[row].tobytes() for row in range(h))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data)))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


_MARGIN_L, _MARGIN_R = 48, 8
_MARGIN_T, _MARGIN_B = 8, 24


def render_spectrum_png(freqs_hz, db_values, width: int = 640,
                        height: int = 400,
                        dynamic_range_db: float = 80.0,
                        line_color: Tuple[int, int, int] = (20, 60, 200)
                        ) -> bytes:
    """频谱 dB 折线 -> PNG 字节。输入须等长、长度 >=2、有限值。"""
    f = np.asarray(freqs_hz, dtype=np.float64)
    db = np.asarray(db_values, dtype=np.float64)
    if len(f) != len(db) or len(f) < 2:
        raise ValueError("freqs/db_values 须等长且 >=2 点")
    if np.any(~np.isfinite(f)) or np.any(~np.isfinite(db)):
        raise ValueError("输入含非有限值，拒绝渲染假图")
    if np.any(np.diff(f) < 0):
        raise ValueError("频率轴须非降序")
    if width < 160 or height < 120:
        raise ValueError(f"画布 {width}x{height} 过小（最小 160x120）")
    if dynamic_range_db <= 0:
        raise ValueError("dynamic_range_db 须为正")

    img = np.full((height, width, 3), 255, dtype=np.uint8)
    x0, x1 = _MARGIN_L, width - _MARGIN_R
    y0, y1 = _MARGIN_T, height - _MARGIN_B
    top_db = float(np.max(db))
    lo_db = top_db - dynamic_range_db

    def px(i: int) -> Tuple[int, int]:
        fx = (f[i] - f[0]) / (f[-1] - f[0]) if f[-1] > f[0] else 0.0
        xd = x0 + int(round(fx * (x1 - x0 - 1)))
        yy = (db[i] - lo_db) / dynamic_range_db
        yy = min(max(yy, 0.0), 1.0)
        yd = y1 - 1 - int(round(yy * (y1 - y0 - 1)))
        return xd, yd

    # 坐标轴
    img[y0:y1, x0] = (0, 0, 0)
    img[y1 - 1, x0:x1] = (0, 0, 0)
    # dB 轴 4 档标注
    for k in range(4):
        lvl = top_db - dynamic_range_db * k / 3.0
        yy = y1 - 1 - int(round((k / 3.0) * (y1 - y0 - 1)))
        img[yy, x0 - 3:x0] = (0, 0, 0)
        _draw_text(img, 2, yy - 3, f"{lvl:.0f}", (0, 0, 0))
    # 频率轴三档标注（起点/中点/终点）
    mid = len(f) // 2
    _draw_text(img, x0 - 6, y1 + 6, _eng(f[0]))
    _draw_text(img, (x0 + x1) // 2 - 12, y1 + 6, _eng(f[mid]))
    _draw_text(img, x1 - 30, y1 + 6, _eng(f[-1]))
    _draw_text(img, x0 - 6, y1 + 16, "Hz")

    # 折线（Bresenham 风格的连续采样连线：按 x 列取该列 y 极值保细峰）
    col_y: Dict[int, List[int]] = {}
    prev = px(0)
    for i in range(1, len(f)):
        cur = px(i)
        steps = max(abs(cur[0] - prev[0]), 1)
        for s in range(steps + 1):
            t = s / steps
            x = int(round(prev[0] + t * (cur[0] - prev[0])))
            y = int(round(prev[1] + t * (cur[1] - prev[1])))
            col_y.setdefault(x, []).append(y)
        prev = cur
    for x, ys in col_y.items():
        if x0 <= x < x1:
            img[min(ys):max(ys) + 1, x] = line_color
    return _png_rgb(img)
