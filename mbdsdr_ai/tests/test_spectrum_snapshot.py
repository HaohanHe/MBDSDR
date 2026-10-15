# SPDX-License-Identifier: MIT
"""spectrum_snapshot 测试。"""
import struct
import zlib

import numpy as np
import pytest

from mbdsdr_ai.spectrum_snapshot import _eng, render_spectrum_png


def _parse_png(data: bytes):
    """最小 PNG 解析：返回 (w, h, rgb ndarray)。"""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, w, h, idat = 8, 0, 0, b""
    while pos < len(data):
        (ln,) = struct.unpack_from(">I", data, pos)
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype == 2
        elif tag == b"IDAT":
            idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat)
    img = np.zeros((h, w, 3), dtype=np.uint8)
    stride = w * 3 + 1
    for row in range(h):
        assert raw[row * stride] == 0          # 无滤波器
        img[row] = np.frombuffer(
            raw[row * stride + 1:(row + 1) * stride], dtype=np.uint8
        ).reshape(w, 3)
    return w, h, img


def test_png_structure_valid():
    f = np.linspace(88e6, 108e6, 512)
    db = -60 + 30 * np.exp(-((f - 98e6) / 1e6) ** 2)
    data = render_spectrum_png(f, db)
    w, h, img = _parse_png(data)
    assert (w, h) == (640, 400)
    assert img.dtype == np.uint8


def test_peak_column_has_line_color():
    f = np.linspace(88e6, 108e6, 512)
    db = np.full(512, -80.0)
    db[256] = -30.0                                 # 单尖峰
    _, _, img = _parse_png(render_spectrum_png(f, db, 640, 400, 80.0))
    blue = (img == (20, 60, 200)).all(axis=2)
    assert blue.any()
    # 尖峰应在画布中列附近
    cols = np.where(blue.any(axis=0))[0]
    assert 300 < cols.mean() < 340


def test_db_range_clamps_not_drops():
    """超界点钳到边（像素仍在），不丢点。"""
    f = np.linspace(1e6, 2e6, 100)
    db = np.linspace(-200, 0, 100)                  # 大部分低于动态范围
    data = render_spectrum_png(f, db, 320, 240, 80.0)
    _, _, img = _parse_png(data)
    blue = (img == (20, 60, 200)).all(axis=2)
    assert blue.sum() >= 100                        # 每列都有线


def test_axis_labels_present():
    f = np.linspace(88e6, 108e6, 256)
    db = np.full(256, -50.0)
    _, _, img = _parse_png(render_spectrum_png(f, db))
    dark = (img.sum(axis=2) < 200)
    assert dark.any()                               # 轴 + 文字墨迹存在


def test_eng_units():
    assert _eng(98e6) == "98M"
    assert _eng(14.2e6) == "14.2M"
    assert _eng(433.5e6) == "433.5M"
    assert _eng(145500000.0) == "145.5M"
    assert _eng(1200.0) == "1.2k"
    assert _eng(88e6) == "88M"


def test_honest_inputs():
    f = np.linspace(1e6, 2e6, 100)
    db = np.full(100, -50.0)
    with pytest.raises(ValueError):
        render_spectrum_png(f, db[:-1])             # 长度不齐
    with pytest.raises(ValueError):
        render_spectrum_png(f, np.full(100, np.nan))
    with pytest.raises(ValueError):
        render_spectrum_png(f[::-1].copy(), db)     # 乱序（diff<0）
    with pytest.raises(ValueError):
        render_spectrum_png(f, db, 100, 100)        # 画布过小
    with pytest.raises(ValueError):
        render_spectrum_png(f, db, 640, 400, 0.0)   # 动态范围非法


def test_flat_line_renders_straight():
    f = np.linspace(1e6, 2e6, 200)
    db = np.full(200, -40.0)
    _, _, img = _parse_png(render_spectrum_png(f, db, 640, 400, 80.0))
    blue = (img == (20, 60, 200)).all(axis=2)
    rows = np.where(blue.any(axis=1))[0]
    assert rows.max() - rows.min() <= 1            # 水平线
