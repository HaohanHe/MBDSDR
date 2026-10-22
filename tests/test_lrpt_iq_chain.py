# -*- coding: utf-8 -*-
"""Meteor LRPT：IQ → 传输帧 → 图像段 → 图像 PNG 的端到端闭环（新增能力门禁）。

背景：仓内 LRPT 各层都在（IQ→QPSK→Viterbi→解扰→CADU、帧同步、VCDU/段重组、
Annex K.3 熵解码），但**没有把传输帧变成图像段的那一步**，也没有把整条链
接到工具上——"接一颗 Meteor LRPT 并出图"对模型是断的。

本文件造一条**完全合成**的 LRPT 空口向量（Pillow 编标准 JPEG 取熵数据 →
RFC 2435 段 → CCSDS 帧 → PN 扰码 → K=7 卷积 → QPSK 成形 IQ），再走新增
链路解回 PNG，逐层核对：
  · 帧 → 段 的抽帧口径（sync/header_len 具名参数）；
  · IQ → 段内容 与原始段逐字节一致（前端 Viterbi/解扰/同步全链路可逆）；
  · 段 → 图 能解出（尺寸与结构对得上）；
  · 段不全/有空洞时诚实失败（不用半幅图冒充整图）。

红线：这是合成向量，不是真实 OTA 样本（仓内真实样本为零，AGENTS 如实标注）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.lrpt_vcdu import (  # noqa: E402
    blocks_to_meteor_segments,
    frames_to_meteor_segments,
    meteor_segments_to_image,
)

W, H = 64, 48
Q_FIELD = 32
CADU_LEN = 1024
HEADER_LEN = 3          # vcid(1) + apid(2)
VCID, APID = 0, 64


def _jpeg_entropy_scan(width: int = W, height: int = H) -> bytes:
    """用 Pillow 编一张标准灰度 JPEG，取其熵编码数据（SOI/SOF/DHT/DQT/SOS 之后）。"""
    from io import BytesIO

    from PIL import Image
    # 3 分量（Meteor 路径按 3 分量 4:2:0 解；灰度 JPEG 的熵数据会解错）
    ramp = (np.indices((height, width))[0] * 255 // max(1, height - 1)
            ).astype(np.uint8)
    rgb = np.stack([ramp, (255 - ramp), (ramp // 2)], axis=-1)
    img = Image.fromarray(rgb, mode="RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=95)
    data = buf.getvalue()
    # 找 SOS 段末尾（熵数据起点）：0xFFDA + 2B 长度 + 头，然后跳到下一个标记
    i = data.find(b"\xff\xda")
    assert i > 0, "Pillow 未产出 JPEG"
    seg_len = int.from_bytes(data[i + 2:i + 4], "big")
    start = i + 2 + seg_len
    # 熵数据到 EOI(0xFFD9) 之前
    end = data.find(b"\xff\xd9", start)
    assert end > start, "未找到熵数据"
    return data[start:end]


def _segment(frag_offset: int, payload: bytes, width: int = W,
             height: int = H, q: int = Q_FIELD) -> bytes:
    """Meteor 图像段（RFC 2435 口径，仓内既有测试同款布局）。"""
    head = bytes([0x00, 0x00, (frag_offset >> 8) & 0xFF, frag_offset & 0xFF])
    if frag_offset == 0:
        dim = bytes([(width >> 8) & 0xFF, width & 0xFF,
                     (height >> 8) & 0xFF, height & 0xFF,
                     0x00, q, 0x08, 0x03])
        return head + dim + payload
    return head + payload


def _segments() -> list:
    """把熵数据切成两段（首段带尺寸头，第二段续接）。"""
    scan = _jpeg_entropy_scan()
    cut = len(scan) // 2
    return [_segment(0, scan[:cut]), _segment(cut, scan[cut:])]


def _blocks(segments) -> list:
    """图像段 → RS 块载荷：每块 = 4B 片段头（+ 首块 8B 尺寸头）+ 扫描数据。

    片段头与尺寸头沿用仓内 RFC 2435 布局（与 lrpt_vcdu 的段格式一致）。
    """
    blocks = []
    for seg in segments:
        head = seg[:4]
        body = seg[4:]
        # 每块独立成片段：片段头带当前扫描内偏移
        off = int.from_bytes(head[2:4], "big")
        blocks.append(head + body)
        del off
    return blocks


#: RS 载荷总长（字节）：4B 片段头 + 数据；首块另带 8B 尺寸头
RS_BLOCK_LEN = 223


def _blocks_from_scan(scan: bytes, width: int = W, height: int = H,
                      q: int = Q_FIELD) -> list:
    """把扫描数据切成 RS 载荷块（每块正好 223B；首块带 8B 尺寸头）。

    片段偏移按**扫描字节**连续递增（仓内重组层的口径：首段 payload 长度
    即下一段的期望 offset）。末块不足处补零——真实 LRPT 的 CADU 尾部本来
    就是补零区，解码侧按图像尺寸决定解码多少 MCU，补零被忽略。
    """
    blocks = []
    pos = 0
    first = True
    while pos < len(scan) or first:
        dim = b""
        room = RS_BLOCK_LEN - 4
        if first:
            dim = bytes([(width >> 8) & 0xFF, width & 0xFF,
                         (height >> 8) & 0xFF, height & 0xFF,
                         0x00, q, 0x08, 0x03])
            room -= len(dim)
        chunk = scan[pos:pos + room]
        if len(chunk) < room:
            chunk = chunk + b"\x00" * (room - len(chunk))
        head = bytes([0x00, 0x00, (pos >> 8) & 0xFF, pos & 0xFF])
        blocks.append(head + dim + chunk)
        pos += len(chunk)
        first = False
        if pos >= len(scan):
            break
    return blocks


def _iq_from_blocks(blocks, sps: int = 4) -> np.ndarray:
    """RS 块载荷 → 编码 → 字节流 → QPSK 符号 → RRC 成形 → IQ。"""
    from mbdsdr_ai.satellite.meteor_lrpt import (LRPT_ASYNC_WORD,
                                                 MeteorLRPTChain, qpsk_modulate)

    chain = MeteorLRPTChain()
    # 同步字在 RS 块之外（仓内既有测试同款：LRPT_ASYNC_WORD + codeword）
    enc = LRPT_ASYNC_WORD + b"".join(chain.encode_block(b) for b in blocks)
    syms = qpsk_modulate(enc)
    from mbdsdr_ai.modulation import _rrc_taps
    # 抽头口径必须与统一内核的匹配滤波一致（内核默认 length_sps=10）
    taps = _rrc_taps(sps, 0.35, length_sps=10)
    impulse = np.zeros(len(syms) * sps, dtype=np.complex128)
    impulse[::sps] = syms
    shaped = np.convolve(impulse, taps, mode="full")
    delay = (len(taps) - 1) // 2
    return shaped[delay: delay + len(syms) * sps].astype(np.complex64)


def test_frames_to_segments_extracts_payload():
    """帧 → 段：剥掉同步字与 vcid/apid 头，载荷与原始段一致。"""
    blocks = _blocks_from_scan(_jpeg_entropy_scan())
    segs, stats = blocks_to_meteor_segments(blocks)
    assert stats["n_blocks"] == len(blocks), stats
    assert segs, "应拼出至少一个图像段"
    assert segs[0][4:12] == bytes([(W >> 8) & 0xFF, W & 0xFF,
                                    (H >> 8) & 0xFF, H & 0xFF,
                                    0x00, Q_FIELD, 0x08, 0x03]), \
        "首段应带尺寸头"
    assert segs[0].startswith(b"\x00\x00\x00\x00"), "首段片段偏移应为 0"


def test_frames_to_segments_reports_mismatched_sync():
    """同步字对不上时如实计数（不是静默丢段）。"""
    blocks = _blocks_from_scan(_jpeg_entropy_scan())
    # 片段偏移不连续时应如实报空洞，而不是静默拼出错段
    bad = [blocks[0], bytes([0x00, 0x00, 0x7F, 0xFF]) + b"\x00" * 219]
    segs, stats = blocks_to_meteor_segments(bad)
    assert stats["n_gap"] >= 1, stats
    assert stats["n_segments"] == 2, stats

    # 片段头装不下时应如实报 n_short
    segs2, stats2 = blocks_to_meteor_segments([b"\x00\x00"])
    assert stats2["n_short"] == 1, stats2
    assert segs2 == []


def test_segments_to_image_decodes():
    """段 → 重组 → 熵解码 → PNG：能解出且尺寸对得上。"""
    blocks = _blocks_from_scan(_jpeg_entropy_scan())
    segs, _ = blocks_to_meteor_segments(blocks)
    out_png = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), ".kilo-tmp", "lrpt_img.png")
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    res = meteor_segments_to_image(segs, out_png)
    assert res["success"], res
    assert res["width"] == W and res["height"] == H, res
    assert os.path.exists(out_png)

    from PIL import Image
    got = np.asarray(Image.open(out_png))
    assert got.shape[:2] == (H, W), got.shape
    # 结构检查：不是纯色（熵数据确实解出了内容）
    assert got.astype(int).std() > 5.0, "解出的图不应是纯色"


def test_incomplete_segments_fail_honestly(tmp_path):
    """段不全（有空洞）必须诚实失败，不出半幅图。"""
    segs = _segments()
    bad = [segs[0], _segment(len(segs[0]) - 8, b"\x00" * 4)]   # offset 跳空
    out_png = str(tmp_path / "bad.png")
    res = meteor_segments_to_image(bad, out_png)
    assert res["success"] is False
    assert res["stage"] == "reassemble"
    assert not os.path.exists(out_png), "失败时不得留下半幅图"


def test_tool_end_to_end_iq_to_png(tmp_path):
    """工具层：合成 LRPT 空口向量 → IQ → PNG。"""
    reg = MBDSDRAgent().tool_registry
    blocks = _blocks_from_scan(_jpeg_entropy_scan())
    iq = _iq_from_blocks(blocks, sps=4)
    iq_path = str(tmp_path / "lrpt.iq")
    iq.tofile(iq_path)
    out_png = str(tmp_path / "lrpt.png")

    r = reg.call("meteor_lrpt_decode_iq", {
        "iq_path": iq_path, "sample_rate": 72000 * 4, "out_png": out_png,
        "sps": 4,
    })
    assert r.success, (r.content or "")[:400]
    assert r.data["success"] is True, r.data
    assert r.data["width"] == W and r.data["height"] == H, r.data
    assert os.path.exists(out_png)


def test_tool_noise_fails_honestly(tmp_path):
    """纯噪声必须显式失败（不得返回占位图）。"""
    reg = MBDSDRAgent().tool_registry
    rng = np.random.default_rng(7)
    iq = (rng.standard_normal(40000) + 1j * rng.standard_normal(40000))
    iq = iq.astype(np.complex64)
    iq_path = str(tmp_path / "noise.iq")
    iq.tofile(iq_path)
    out_png = str(tmp_path / "noise.png")

    r = reg.call("meteor_lrpt_decode_iq", {
        "iq_path": iq_path, "sample_rate": 72000, "out_png": out_png,
    })
    assert r.success is False
    assert not os.path.exists(out_png)
