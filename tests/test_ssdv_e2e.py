# SPDX-License-Identifier: MIT
"""SSDV 完整接收链端到端测试（fsphil SSDV 公开包格式）。

本测试按 **真实就位接口**（非臆测）串联整条链，全部确定性、固定种子：

  Pillow 合成小图（渐变/图形）
    → SsdvEncoder 分包（15 头 + CRC32 + RS(255,223) 32 字节校验）
    → 注入随机字节误码 / 丢包（区分 RS t=16 能力内 / 外）
    → SsdvDecoder 字节流 ``feed``（含前导噪声）
    → RS 纠错 + CRC32 把关 + MCU 级重组
    → 还原标准 JPEG（Pillow 可打开、宽高与原图一致）

以及 CCSDS 数字链往返（规格 §2.2）::

  帧字节 → [RS(255,223)] → CCSDS 加/解扰 → 卷积 K=7 r=1/2 → Viterbi
        → ASM(0x1ACFFC1D) 帧同步（噪声 preamble）→ SSDV 帧字节

红线（与规格 §6 一致）：
  - 不内置任何呼号（含 BI4MIB）；callsign 一律由参数传入。
  - 超纠错能力时**诚实**报告具体缺失 MCU 列表（``missing_mcus``），不造假图、不假装完整。
  - 无数据 / 纯噪声 → 空结果（无图、无假包）。
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
import pytest
from PIL import Image

# 让 ``tests/`` 目录下也能 import 仓库根的 mbdsdr_ai 包
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mbdsdr_ai.ssdv_decoder import (  # noqa: E402
    PACKET_LEN,
    SsdvDecoder,
    SsdvEncoder,
    SsdvImage,
    TYPE_NORMAL,
)
from mbdsdr_ai.ccsds_rx import (  # noqa: E402
    CCSDS_ASM_WORD,
    AsmFramer,
    ConvolutionalEncoder,
    CcsdsReceiveChain,
    CcsdsRxConfig,
    SatelliteAFSKDemod,
    ViterbiDecoder,
    bits_to_bytes_msb,
    bytes_to_bits_msb,
)
from mbdsdr_ai.fec import ReedSolomon, Scrambler  # noqa: E402

# --------------------------------------------------------------------------
# 固定随机种子 / 合成呼号（仅作参数传入；本测试与被测模块均不内置真实呼号）
# --------------------------------------------------------------------------
SEED = 20261008
CALLSIGN = "E2ETST"          # 合成测试呼号；由参数传入，非任何真实电台
IMG_ID = 11


# --------------------------------------------------------------------------
# 测试素材：Pillow 生成小图（渐变 + 图形），并经一次 JPEG 编/解码
# --------------------------------------------------------------------------
def _gradient_rgb(w: int = 48, h: int = 48) -> np.ndarray:
    """Pillow 生成 w×h RGB 渐变图，返回 numpy (h,w,3) uint8。"""
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            arr[y, x, 0] = int(x * 255 // max(1, w - 1))   # 红随列渐变
            arr[y, x, 1] = int(y * 255 // max(1, h - 1))   # 绿随行渐变
            arr[y, x, 2] = 128                             # 蓝恒定
    return np.array(Image.fromarray(arr, "RGB"))


def _pil_jpeg_roundtrip(rgb: np.ndarray) -> Image.Image:
    """把 numpy RGB 经 Pillow 存成 JPEG 再打开（确认源图本身是合法 JPEG）。"""
    im = Image.fromarray(rgb, "RGB")
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    out = Image.open(io.BytesIO(buf.getvalue()))
    out.load()
    return out


def _encode_to_ssdv(rgb: np.ndarray, callsign: str = CALLSIGN,
                    image_id: int = IMG_ID) -> list:
    enc = SsdvEncoder(callsign=callsign, image_id=image_id,
                      quality=4, mcu_mode=3, pkt_type=TYPE_NORMAL)
    return enc.encode_image(rgb)


def _decode_stream(stream: bytes, image_id: int = IMG_ID) -> "object":
    """字节流（可含前导噪声）→ SsdvDecoder → SsdvImage.build()。"""
    dec = SsdvDecoder()
    imgset = SsdvImage(image_id)
    for pkt in dec.feed(stream):
        if pkt is not None:
            imgset.add(pkt)
    return imgset.build()


def _flip_bytes(pkt: bytes, positions, rng: np.random.Generator) -> bytes:
    p = bytearray(pkt)
    for x in positions:
        p[x] ^= int(rng.integers(1, 256))
    return bytes(p)


# ==========================================================================
# 1. SSDV 往返：RS t=16 能力内的字节误码完全恢复
# ==========================================================================
def test_ssdv_roundtrip_within_rs_capacity():
    rgb = _gradient_rgb(48, 48)
    src = _pil_jpeg_roundtrip(rgb)
    assert src.size == (48, 48)

    pkts = _encode_to_ssdv(rgb)
    assert len(pkts) >= 2, "48x48 图应拆成多个包"
    assert all(len(p) == PACKET_LEN == 256 for p in pkts)

    # 在 RS(255,223) t=16 能力内注入字节误码（每包 ≤16 字节）：pkt[1] 翻 12 字节
    rng = np.random.default_rng(SEED)
    pos1 = rng.choice(range(1, 255), 12, replace=False)
    corrupted = list(pkts)
    corrupted[1] = _flip_bytes(pkts[1], pos1, rng)

    # 前导噪声字节（含 0x55）后再拼整条字节流，验证同步状态机重新对齐
    stream = b"\x55\x55\x00\xff\x55\xa5" + b"".join(corrupted)

    res = _decode_stream(stream, IMG_ID)
    assert not res.empty
    assert (res.width, res.height) == (48, 48)

    # 关键数值：全部 MCU 收到、无缺失（RS 把 12 字节误码纠正回来）
    assert res.mcu_count == 36, f"mcu_count 期望 36，实得 {res.mcu_count}"
    assert res.missing_mcus == [], f"纠错能力内应无缺失 MCU，实得 {res.missing_mcus}"
    assert len(res.received_mcus) == res.mcu_count

    # 还原 JPEG 能被 Pillow 打开，宽高与原图一致
    out = Image.open(io.BytesIO(res.jpeg))
    out.load()
    assert out.size == src.size == (48, 48)


def test_ssdv_reports_rs_correction_count():
    """确认 RS 确实执行了纠错（被翻的包 nerrors==12，未受扰的包 nerrors==0）。"""
    rgb = _gradient_rgb(48, 48)
    pkts = _encode_to_ssdv(rgb)
    rng = np.random.default_rng(SEED)
    pos1 = rng.choice(range(1, 255), 12, replace=False)
    corrupted = _flip_bytes(pkts[1], pos1, rng)

    dec = SsdvDecoder()
    nerrors = []
    imgset = SsdvImage(IMG_ID)
    for i, raw in enumerate(pkts):
        chunk = corrupted if i == 1 else raw
        for p in dec.feed(chunk):
            if p is not None:
                imgset.add(p)
                nerrors.append(p.nerrors)
    res = imgset.build()
    assert 12 in nerrors, f"pkt[1] 应被纠正 12 字节，nerrors={nerrors}"
    assert 0 in nerrors, f"未受扰包应 nerrors==0，nerrors={nerrors}"
    assert res.missing_mcus == []


# ==========================================================================
# 2. 超纠错能力诚实失败：报告具体缺失 MCU，不造假图
# ==========================================================================
def test_beyond_rs_capacity_reports_missing_mcus():
    rgb = _gradient_rgb(48, 48)
    pkts = _encode_to_ssdv(rgb)

    # 单包注入 17 个字节误码 > t=16 → RS 解不出 → CRC32 把关失败 → 该包被丢
    rng = np.random.default_rng(SEED + 1)
    pos = rng.choice(range(1, 255), 17, replace=False)
    corrupted = _flip_bytes(pkts[1], pos, rng)

    dec = SsdvDecoder()
    imgset = SsdvImage(IMG_ID)
    for i, raw in enumerate(pkts):
        chunk = corrupted if i == 1 else raw
        for p in dec.feed(chunk):
            if p is not None:
                imgset.add(p)
    res = imgset.build()

    # 诚实报告：被丢包负责的 MCU 区间进入 missing_mcus（具体列表，不造假）
    assert not res.empty
    assert res.missing_mcus, "超纠错能力必须显式报告缺失 MCU，不得假装完整"
    pkt1_first_mcu = (pkts[1][13] << 8) | pkts[1][14]
    expected_missing = set(range(pkt1_first_mcu, res.mcu_count))
    assert expected_missing <= set(res.missing_mcus), (
        f"被丢包负责的 MCU 应全部列入缺失；期望含 {sorted(expected_missing)}，"
        f"实得 {res.missing_mcus}")
    # 守恒：收到 + 缺失 == 全部 MCU（无幻影、无遗漏）
    assert len(res.received_mcus) + len(res.missing_mcus) == res.mcu_count

    # 即使缺 MCU，重组出的 JPEG 仍是标准可打开文件，宽高不变（缺处补空白而非崩溃）
    out = Image.open(io.BytesIO(res.jpeg))
    out.load()
    assert out.size == (48, 48)


def test_lost_whole_packet_reported():
    """整包丢失（不解扰/不纠错，直接不收）→ 同样诚实报缺失。"""
    rgb = _gradient_rgb(48, 48)
    pkts = _encode_to_ssdv(rgb)

    dec = SsdvDecoder()
    imgset = SsdvImage(IMG_ID)
    drop_idx = len(pkts) // 2
    for i, raw in enumerate(pkts):
        if i == drop_idx:
            continue  # 整包丢失
        for p in dec.feed(raw):
            if p is not None:
                imgset.add(p)
    res = imgset.build()
    assert res.missing_mcus, "整包丢失应报告缺失 MCU"
    assert len(res.received_mcus) + len(res.missing_mcus) == res.mcu_count
    Image.open(io.BytesIO(res.jpeg)).load()


# ==========================================================================
# 3. CCSDS 数字链往返（卷积/Viterbi/解扰/RS 开关/ASM 同步/AFSK 速率）
# ==========================================================================
def _seed_bytes(seed: int, n: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, size=n, dtype=np.uint8).tobytes()


def _tx_bits(frame: bytes, use_rs: bool, rng: np.random.Generator) -> list:
    """发送端：可选 RS(255,223) → 加扰 → 卷积 K=7 r=1/2 → 拍平比特。"""
    if use_rs:
        frame = ReedSolomon(nsym=32).encode(frame)   # 223 -> 255
    scrambled = Scrambler().scramble(frame)
    pairs = ConvolutionalEncoder().encode_bytes(scrambled, tail=0)
    coded = [b for pair in pairs for b in pair]
    asm = bytes_to_bits_msb(CCSDS_ASM_WORD.to_bytes(4, "big"))
    preamble = list(rng.integers(0, 2, size=200).astype(int))  # 噪声 preamble
    return preamble + asm + coded


def test_ccsds_chain_viterbi_descramble_256b():
    """帧字节 → 加扰 → 卷积 →(噪声 preamble+ASM)→ Viterbi → 解扰 → 还原帧。"""
    frame = _seed_bytes(SEED + 10, 256)
    rng = np.random.default_rng(SEED + 11)
    tx = _tx_bits(frame, use_rs=False, rng=rng)
    cfg = CcsdsRxConfig(frame_bits_after_asm=256 * 8 * 2,
                        use_viterbi=True, use_descramble=True, use_rs=False)
    out = CcsdsReceiveChain(cfg).process_bits(tx)
    assert out == [frame], "无 RS 的 256B 帧应经 Viterbi+解扰后完整还原"


def test_ccsds_chain_rs_switch_on():
    """RS 开关打开：223B → RS 编码 → 加扰 → 卷积 →(ASM)→ Viterbi → 解扰 → RS 解码 → 还原。"""
    frame = _seed_bytes(SEED + 12, 223)
    rng = np.random.default_rng(SEED + 13)
    tx = _tx_bits(frame, use_rs=True, rng=rng)
    cfg = CcsdsRxConfig(frame_bits_after_asm=255 * 8 * 2,
                        use_viterbi=True, use_descramble=True,
                        use_rs=True, rs_nsym=32)
    out = CcsdsReceiveChain(cfg).process_bits(tx)
    assert out == [frame], "RS 开关开启时应还原 223B 原始帧"


def test_ccsds_viterbi_corrects_bit_errors():
    """卷积编码后注入 ~2% 误比特，Viterbi 能力内完全恢复。"""
    frame = _seed_bytes(SEED + 14, 256)
    pairs = ConvolutionalEncoder().encode_bytes(frame, tail=6)
    rng = np.random.default_rng(SEED + 15)
    noisy_pairs = [(int(a ^ (rng.random() < 0.02)),
                    int(b ^ (rng.random() < 0.02))) for (a, b) in pairs]
    out = bits_to_bytes_msb(ViterbiDecoder().decode(noisy_pairs))
    assert out[:len(frame)] == frame, "Viterbi 应纠正能力内的误比特"


def test_ccsds_asm_sync_across_noise_preamble():
    """ASM 帧同步：在长随机噪声 preamble 后仍能精确锁定并取到整帧。"""
    frame = _seed_bytes(SEED + 16, 64)
    rng = np.random.default_rng(SEED + 17)
    asm = bytes_to_bits_msb(CCSDS_ASM_WORD.to_bytes(4, "big"))
    stream = list(rng.integers(0, 2, size=250).astype(int)) + asm + bytes_to_bits_msb(frame)
    out = AsmFramer(frame_bits=64 * 8).feed_bits(stream)
    assert out == [frame], "噪声 preamble 后应能 ASM 同步并还原整帧"


@pytest.mark.parametrize("baud", [1200.0, 2400.0])
def test_afsk_satellite_rates_roundtrip(baud):
    """卫星 AFSK 速率（1200/2400 baud）调制解调往返：最佳对齐下误码 ≤1 bit。"""
    demod = SatelliteAFSKDemod(sample_rate=48000, baud=baud,
                               mark_freq=1200.0, space_freq=2400.0)
    payload = bytes([0xAA, 0x55, 0x12, 0x34, 0xDE, 0xAD,
                     0x55, 0xAA, 0x55, 0xAA, 0x01, 0x23])
    bits = np.array(
        bytes_to_bits_msb(CCSDS_ASM_WORD.to_bytes(4, "big"))
        + bytes_to_bits_msb(payload), dtype=np.int8)
    audio = demod.modulate(bits)
    rx = demod.demodulate(audio)
    best = None
    for off in range(-3, 4):
        a = rx[max(0, off):] if off >= 0 else rx[:off]
        b = bits[max(0, -off):] if off < 0 else (bits[off:] if off > 0 else bits)
        m = min(len(a), len(b))
        mism = int(np.sum(a[:m] != b[:m]))
        if best is None or mism < best[0]:
            best = (mism, off)
    assert best[0] <= 1, f"AFSK {baud:.0f} baud 往返误码过多：{best[0]} bit"


# ==========================================================================
# 4. 空态：无数据 / 纯噪声 → 诚实空结果（无图、无假包）
# ==========================================================================
def test_ssdv_empty_state():
    dec = SsdvDecoder()
    assert dec.feed(b"") == [], "空输入不得产出任何包"

    # 纯随机噪声字节流：解不出合法 0x55/0x66 头，也不应捏造包
    noise = np.random.default_rng(SEED).integers(0, 256, size=4096, dtype=np.uint8).tobytes()
    out = [p for p in dec.feed(noise) if p is not None]
    assert out == [], "纯噪声不得解出假包"

    imgset = SsdvImage(IMG_ID)
    res = imgset.build()
    assert res.empty, "无任何包时应为空态"
    assert res.jpeg == b"", "空态不得生成假 JPEG"
    assert res.width == 0 and res.height == 0


def test_ccsds_empty_state():
    noise = list(np.random.default_rng(SEED).integers(0, 2, size=800).astype(int))
    cfg = CcsdsRxConfig(use_viterbi=True, use_descramble=True)
    assert CcsdsReceiveChain(cfg).process_bits(noise) == [], "纯噪声不得同步出假帧"
    assert AsmFramer(frame_bits=64 * 8).feed_bits(noise) == []


# ==========================================================================
# 5. callsign 由参数传入；被测代码不内置任何呼号（含 BI4MIB）
# ==========================================================================
def test_callsign_passed_by_parameter():
    rgb = _gradient_rgb(48, 48)
    pkts = _encode_to_ssdv(rgb, callsign=CALLSIGN, image_id=IMG_ID)
    dec = SsdvDecoder()
    seen = None
    for raw in pkts:
        for p in dec.feed(raw):
            if p is not None:
                seen = p
                break
        if seen is not None:
            break
    assert seen is not None
    assert seen.callsign == CALLSIGN, "呼号应由编码端参数透传到解码包"


def test_no_builtin_callsign_in_sources():
    """被测两模块源码不得硬编码任何呼号常量（红线：含 BI4MIB）。"""
    for fname in ("ssdv_decoder.py", "ccsds_rx.py"):
        path = os.path.join(_REPO_ROOT, "mbdsdr_ai", fname)
        src = open(path, encoding="utf-8").read()
        for bad in ("BI4MIB", "DEFAULT_CALLSIGN"):
            assert bad not in src, f"{fname} 内置了呼号常量 {bad!r}"
