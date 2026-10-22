# -*- coding: utf-8 -*-
"""FY-4 经典 CCSDS 链路：IQ → 帧 → VCDU → 图 的端到端往返（新增能力门禁）。

背景：仓内既有 FY-4 往返测试（tests/test_fengyun.py）走的是"裸 BPSK 比特"
玩具信道——只验证 VCDU→图，**没有物理层/链路层**（ASM 同步、Viterbi、解扰、
RS 全缺）。本文件补上这段：IQ（统一调制内核成形）→ CCSDS 接收链 → VCDU →
FY-4 段重组 → PNG，并与原图逐像素比对。

覆盖：
· BPSK 与 QPSK 两种下行体制的完整往返（QPSK 用仓内 binary 映射，
  与 CCSDS 标准 00→+45° 一致）；
· 工具层 fy4_lrit_decode_iq（文件输入 → PNG）；
· 无信号/参数不符时必须诚实失败并带 diagnostic（不得返回半截图或占位图）。
"""
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.agent import MBDSDRAgent  # noqa: E402
from mbdsdr_ai.ccsds_tools import ccsds_encode_frame  # noqa: E402
from mbdsdr_ai.fengyun_sat import (  # noqa: E402
    build_fy4_image_information,
    build_fy4_xrit_file,
    decode_fy4_iq_to_image,
    file_to_vcdus,
)
from mbdsdr_ai.modulation import modulate_bits  # noqa: E402

W, H, SEG_ROWS, NSEG = 32, 16, 4, 4   # H = SEG_ROWS * NSEG
VCDU_LEN = 892


def _test_image(w: int = W, h: int = H) -> np.ndarray:
    img = np.zeros((h, w), dtype=np.uint8)
    for r in range(h):
        img[r, :] = int(r * 255 // max(1, h - 1))
    for c in range(w):
        if (c // 4) % 2 == 0:
            img[:, c] = np.clip(img[:, c].astype(int) + 40, 0, 255).astype(np.uint8)
    return img


def _vcdus_from_image(img: np.ndarray) -> list:
    """合成图 → FY-4 xRIT 段文件 → VCDU 列表（仓内既有编码器）。"""
    vcdus = []
    for k in range(NSEG):
        rows = img[k * SEG_ROWS:(k + 1) * SEG_ROWS, :]
        info = build_fy4_image_information(
            satellite_name="FY-4A", instrument_name="AGRI",
            bit_per_pixel=8, columns=img.shape[1], lines=SEG_ROWS,
            compression_flag=0, channel_number=1,
            total_segments=NSEG, current_segment_number=k + 1,
            current_segment_pos=k + 1,
            current_segment_line_pos=k * SEG_ROWS)
        f = build_fy4_xrit_file(info, rows.tobytes(),
                                annotation=f"FY4A-AGRI-CH1-SEG{k + 1}")
        vcdus += file_to_vcdus(f, scid=0x20, vcid=1, apid=0x10, chunk=300)
    return vcdus


def _vcdus_to_iq(vcdus, scheme="bpsk", sps=4, alpha=0.35):
    """VCDU → CCSDS 帧（ASM + 扰码 + 卷积）→ 成形调制 → IQ。

    编码端用仓内 ccsds_encode_frame（口径：信息字节 → 扰码 → K=7 r=1/2
    卷积（6 bit 尾）→ 前置 32bit ASM），与接收端 ccsds_rx 的 AsmFramer +
    Viterbi + 解扰严格互逆。
    """
    bits = []
    for vcdu in vcdus:
        enc = ccsds_encode_frame({
            "data": list(vcdu),
            "use_viterbi": True,
            "use_descramble": True,
            "conv_tail_bits": 6,
        })
        bits.extend(int(b) & 1 for b in enc["bits"])
    mod = modulate_bits(bits, scheme=scheme, sps=sps, shaping="rrc",
                         alpha=alpha)
    return np.asarray(mod["waveform"], dtype=np.complex64), len(bits), len(mod["symbols"])


@pytest.mark.parametrize("scheme", ["bpsk", "qpsk"])
def test_fy4_iq_to_image_roundtrip(tmp_path, scheme):
    """IQ → 图 的端到端往返：重建图与原图逐像素一致。"""
    img = _test_image()
    vcdus = _vcdus_from_image(img)
    assert vcdus, "应至少造出一个 VCDU"
    iq, n_bits, n_symbols = _vcdus_to_iq(vcdus, scheme=scheme)
    assert n_symbols * 4 == len(iq), (n_symbols, len(iq))

    out_png = str(tmp_path / f"fy4_{scheme}.png")
    res = decode_fy4_iq_to_image(iq, out_png, scheme=scheme, sps=4,
                                 shaping="rrc", alpha=0.35)
    assert res["success"], res
    assert res["width"] == img.shape[1]
    assert res["height"] == img.shape[0]
    assert os.path.exists(out_png)

    # 与原图逐像素比对
    from PIL import Image
    got = np.asarray(Image.open(out_png))
    assert got.shape == img.shape, f"形状 {got.shape} != {img.shape}"
    diff = np.abs(got.astype(int) - img.astype(int))
    assert int(diff.max()) == 0, f"最大像素差 {diff.max()}"


def test_tool_fy4_lrit_decode_iq_end_to_end(tmp_path):
    """工具层：写 IQ 文件 → 调工具 → 出 PNG。"""
    reg = MBDSDRAgent().tool_registry
    img = _test_image()
    vcdus = _vcdus_from_image(img)
    iq, _, _ = _vcdus_to_iq(vcdus, scheme="bpsk")
    iq_path = str(tmp_path / "fy4.iq")
    iq.astype(np.complex64).tofile(iq_path)
    out_png = str(tmp_path / "fy4_tool.png")

    r = reg.call("fy4_lrit_decode_iq", {
        "iq_path": iq_path, "out_png": out_png,
        "scheme": "bpsk", "sps": 4, "shaping": "rrc", "alpha": 0.35,
    })
    assert r.success, (r.content or "")[:300]
    assert r.data["success"] is True
    assert r.data["width"] == img.shape[1]
    assert os.path.exists(out_png)

    from PIL import Image
    got = np.asarray(Image.open(out_png))
    assert int(np.abs(got.astype(int) - img.astype(int)).max()) == 0


def test_no_signal_is_honest_failure(tmp_path):
    """纯噪声/无信号必须显式失败并给 diagnostic（不得返回占位图）。"""
    reg = MBDSDRAgent().tool_registry
    rng = np.random.default_rng(1234)
    iq = (rng.standard_normal(20000) + 1j * rng.standard_normal(20000))
    iq = iq.astype(np.complex64)
    iq_path = str(tmp_path / "noise.iq")
    iq.tofile(iq_path)
    out_png = str(tmp_path / "noise.png")

    r = reg.call("fy4_lrit_decode_iq", {
        "iq_path": iq_path, "out_png": out_png, "scheme": "bpsk", "sps": 4,
    })
    assert r.success is False
    assert not os.path.exists(out_png), "失败时不得留下占位 PNG"
    data = r.data or {}
    assert data.get("stage") in ("demod", "frame_sync", "segment_assembly"), data
    assert data.get("diagnostic") or data.get("error")


def test_missing_iq_is_explicit_error():
    """既没有 iq_path 也没有 iq_base64 → 明确报错（不是空结果）。"""
    reg = MBDSDRAgent().tool_registry
    r = reg.call("fy4_lrit_decode_iq", {"out_png": "unused.png"})
    assert r.success is False
    assert "iq" in (r.content or "").lower()


def test_wrong_parameters_do_not_fabricate_image(tmp_path):
    """sps 等参数与实际不符时必须失败，不得拼出"看起来像"的图。"""
    reg = MBDSDRAgent().tool_registry
    img = _test_image()
    vcdus = _vcdus_from_image(img)
    iq, _, _ = _vcdus_to_iq(vcdus, scheme="bpsk", sps=4)
    iq_path = str(tmp_path / "fy4_bad.iq")
    iq.astype(np.complex64).tofile(iq_path)
    out_png = str(tmp_path / "fy4_bad.png")

    r = reg.call("fy4_lrit_decode_iq", {
        "iq_path": iq_path, "out_png": out_png, "scheme": "bpsk", "sps": 7,
    })
    assert r.success is False
    assert not os.path.exists(out_png)
