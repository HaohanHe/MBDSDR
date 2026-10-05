# SPDX-License-Identifier: MIT
"""Phase45 块1+2（Python 域）：CCSDS Viterbi + RS(255,223) 级联全链确定性测试。

覆盖（全部云内合成、固定种子、确定性）：
  1. **Viterbi (2,1,7)**：卷积编码（flush tail=6 收尾）→ 注入硬判决错误 → 解码断言还原；
     已知终态（final_state=0）与自由收尾两种模式；超能力诚实失败不伪造。
  2. **RS(255,223) CCSDS 131.0-B**：GF(256) 本原多项式 0x187、根 α^112..α^143（与公开
     reedsolo 库逐字节交叉验证）；**必须覆盖 16 符号纠错边界成功 + 17 符号失败边界**。
  3. **完整级联全链**：BPSK 复 IQ（承载 RS 编码后 CCSDS 帧 + ASM）→ ssdv_phy
     demod_bpsk → 硬比特 → ASM 同步 → Viterbi → 解扰 → RS 解码 → 218B DSLWP 包
     → SsdvDecoder → JPEG；断言出图正确（全量 MCU 无缺失）。
  4. **诚实空态**：纯噪声 IQ / 不可解码输入不伪造帧、不出假包。

红线：干净室仅按公开 CCSDS 标准重写；不内置呼号（callsign 由参数传入）；
纯噪声诚实返回空，绝不 mock 伪造。
"""

from __future__ import annotations

import io
import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from mbdsdr_ai.ccsds_rx import (  # noqa: E402
    CCSDS_ASM_WORD,
    AsmFramer,
    ConvolutionalEncoder,
    ViterbiDecoder,
    bits_to_bytes_msb,
    bytes_to_bits_msb,
)
from mbdsdr_ai.fec import ReedSolomon, Scrambler  # noqa: E402
from mbdsdr_ai.ssdv_decoder import (  # noqa: E402
    DIALECT_DSLWP,
    DSLWP_PACKET_LEN,
    SsdvDecoder,
    SsdvEncoder,
    SsdvImage,
)
from mbdsdr_ai.ssdv_phy import bpsk_modulate_bits, demod_bpsk  # noqa: E402

SEED = 20261005
CALLSIGN = "PH45TST"          # 合成测试呼号；由参数传入，非任何真实电台
IMG_ID = 7


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _seed_bytes(seed: int, n: int) -> bytes:
    return np.random.default_rng(seed).integers(0, 256, size=n, dtype=np.uint8).tobytes()


def _gradient_rgb(w: int = 48, h: int = 48) -> np.ndarray:
    a = np.zeros((h, w, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            a[y, x] = [x * 255 // max(1, w - 1),
                       y * 255 // max(1, h - 1), 128]
    return np.array(Image.fromarray(a, "RGB"))


# ===========================================================================
# 1. Viterbi (2,1,7) 干净室核实：往返 + 硬判决错误注入
# ===========================================================================
class TestViterbiK7:
    def test_clean_roundtrip_flush_known_terminal(self):
        """卷积编码（flush tail=6 归零状态）→ 解码，已知终态=0 精确还原。"""
        data = _seed_bytes(SEED + 1, 256)
        pairs = ConvolutionalEncoder().encode_bytes(data, tail=6)
        bits = ViterbiDecoder().decode(pairs, final_state=0)
        out = bits_to_bytes_msb(bits)
        assert out[:len(data)] == data

    def test_free_end_terminal_matches_known(self):
        """自由收尾（最小度量终态）与已知终态=0 在干净链路下结果一致。"""
        data = _seed_bytes(SEED + 2, 256)
        pairs = ConvolutionalEncoder().encode_bytes(data, tail=6)
        a = bits_to_bytes_msb(ViterbiDecoder().decode(pairs))
        b = bits_to_bytes_msb(ViterbiDecoder().decode(pairs, final_state=0))
        assert a[:len(data)] == data
        assert a == b

    def test_corrects_hard_decision_errors(self):
        """注入硬判决符号错误（能力内）→ Viterbi 完全还原。"""
        data = _seed_bytes(SEED + 3, 256)
        pairs = ConvolutionalEncoder().encode_bytes(data, tail=6)
        rng = np.random.default_rng(SEED + 4)
        noisy = [(int(x ^ (rng.random() < 0.03)),
                  int(y ^ (rng.random() < 0.03))) for (x, y) in pairs]
        out = bits_to_bytes_msb(ViterbiDecoder().decode(noisy, final_state=0))
        assert out[:len(data)] == data

    def test_beyond_capacity_honest_not_faked(self):
        """超 Viterbi 能力：不得还原出原数据（诚实失败，不伪造）。"""
        data = _seed_bytes(SEED + 5, 256)
        pairs = ConvolutionalEncoder().encode_bytes(data, tail=6)
        rng = np.random.default_rng(SEED + 6)
        noisy = [(int(x ^ (rng.random() < 0.18)),
                  int(y ^ (rng.random() < 0.18))) for (x, y) in pairs]
        out = bits_to_bytes_msb(ViterbiDecoder().decode(noisy, final_state=0))
        assert out[:len(data)] != data

    def test_all_zero_and_all_one(self):
        for data in (b"\x00" * 32, b"\xff" * 32):
            pairs = ConvolutionalEncoder().encode_bytes(data, tail=6)
            out = bits_to_bytes_msb(ViterbiDecoder().decode(pairs, final_state=0))
            assert out[:len(data)] == data


# ===========================================================================
# 2. RS(255,223) CCSDS 131.0-B：域参数交叉验证 + 16/17 符号纠错边界
# ===========================================================================
class TestRsCcsdsBoundary:
    def test_field_matches_public_reedsolo_library(self):
        """代数域与公开 reedsolo 库逐字节一致：GF(256) 0x187、根 α^112..α^143。"""
        reedsolo = pytest.importorskip("reedsolo")
        msg = bytes(range(223))
        mine = ReedSolomon(nsym=32, fcr=112, prim=1, prim_poly=0x187,
                           ccsds_invert=False)
        ref = reedsolo.RSCodec(nsym=32, nsize=255, fcr=112, prim=0x187,
                               generator=2, c_exp=8)
        assert mine.encode(msg) == bytes(ref.encode(msg)), \
            "RS 域生成多项式/根集须与公开 CCSDS 参考逐字节一致"

    def test_clean_roundtrip(self):
        msg = _seed_bytes(SEED + 7, 223)
        rs = ReedSolomon(nsym=32)
        res = rs.decode(rs.encode(msg))
        assert res.data == msg and res.nerrors == 0

    def test_sixteen_symbol_correction_boundary(self):
        """t=16（guaranteed 纠错边界）：确定性逐样本全部纠正。"""
        rng = np.random.default_rng(SEED + 8)
        msg = rng.integers(0, 256, size=223, dtype=np.uint8).tobytes()
        rs = ReedSolomon(nsym=32)
        cw = bytearray(rs.encode(msg))
        for _ in range(60):
            c = bytearray(cw)
            pos = rng.choice(255, size=16, replace=False)
            for p in pos:
                c[p] ^= int(rng.integers(1, 256))
            res = rs.decode(bytes(c))
            assert res.nerrors == 16 and res.data == msg, \
                "16 符号错误必须全部纠正（t=16 边界）"

    def test_seventeen_symbol_failure_boundary(self):
        """t=17（超 guaranteed 能力）：解码器绝不能可靠还原原消息。

        RS(255,223) 的 guaranteed 纠错半径是 16 符号；17 个错误时它要么诚实报
        不可纠（nerrors=-1），要么误纠到另一码字——两种情况下 ``res.data`` 都不可能
        等于原消息。这条断言锁定"超能力不伪造正确数据"。
        """
        rng = np.random.default_rng(SEED + 9)
        msg = rng.integers(0, 256, size=223, dtype=np.uint8).tobytes()
        rs = ReedSolomon(nsym=32)
        cw = bytearray(rs.encode(msg))
        honest = 0
        for _ in range(60):
            c = bytearray(cw)
            pos = rng.choice(255, size=17, replace=False)
            for p in pos:
                c[p] ^= int(rng.integers(1, 256))
            res = rs.decode(bytes(c))
            assert res.data != msg, "17 符号错误不应被还原成原消息（超 guaranteed 能力）"
            if res.nerrors == -1:
                honest += 1
        assert honest > 0, "17 符号错误应有一部分诚实报不可纠"


# ===========================================================================
# 3. 完整级联全链：IQ → BPSK → ASM → Viterbi → 解扰 → RS → 218B → JPEG
# ===========================================================================
class TestCascadeFullChain:
    #: 通用演示符号率/采样率（非活动参数）。
    FS = 48000.0
    SYMRATE = 4800.0

    def _tx_iq(self, rgb: np.ndarray) -> tuple[np.ndarray, bytes, int]:
        """RGB → DSLWP 218B 包 → 外层 RS → 加扰 → 卷积 → ASM → BPSK 复 IQ。

        返回 ``(iq, orig_stream218, n_coded_bits)``。合成信号里收发两端 fs/符号率对齐，
        盲定时确定锁定。
        """
        enc = SsdvEncoder(callsign=CALLSIGN, image_id=IMG_ID,
                          quality=4, mcu_mode=3)
        pkts = enc.encode_image_dslwp(rgb)
        assert all(len(p) == DSLWP_PACKET_LEN == 218 for p in pkts)
        stream218 = b"".join(pkts)

        rs = ReedSolomon(nsym=32)
        pad = (-len(stream218)) % 223
        msg = stream218 + b"\x00" * pad
        stream_rs = b"".join(
            rs.encode(msg[i:i + 223]) for i in range(0, len(msg), 223))
        scrambled = Scrambler().scramble(stream_rs)

        pairs = ConvolutionalEncoder().encode_bytes(scrambled, tail=6)
        coded = [b for p in pairs for b in p]
        asm = bytes_to_bits_msb(CCSDS_ASM_WORD.to_bytes(4, "big"))
        pre = bytes_to_bits_msb(b"\x55" * 8)
        tx = np.array(pre + asm + coded, dtype=np.int8)

        iq = bpsk_modulate_bits(tx, self.FS, self.SYMRATE, f_if=0.0)
        return iq, stream218, len(coded)

    def _demod_to_frame(self, iq: np.ndarray, n_coded: int) -> bytes:
        """IQ → demod_bpsk → ASM 同步 → 单个帧字节。"""
        bits = demod_bpsk(iq, self.FS, self.SYMRATE, f_offset=0.0)
        assert bits.size > 0
        framer = AsmFramer(frame_bits=n_coded, max_hamming=0)
        frames = framer.feed_bits(bits.tolist())
        assert len(frames) == 1, f"ASM 应精确同步出 1 帧，实得 {len(frames)}"
        return frames[0]

    def _frame_to_image(self, frame: bytes, stream218: bytes) -> "object":
        """ASM 已同步的帧字节 → Viterbi → 解扰 → RS → 218B → SsdvImage.build()。"""
        fbits = bytes_to_bits_msb(frame)
        pairs = [(fbits[i], fbits[i + 1]) for i in range(0, len(fbits), 2)]
        vbytes = bits_to_bytes_msb(
            ViterbiDecoder().decode(pairs, final_state=0))
        n_rs = (-len(stream218)) % 223
        n_rsblocks = (len(stream218) + n_rs) // 223
        stream_rs = vbytes[: n_rsblocks * 255]
        desc = Scrambler().descramble(stream_rs)
        rs = ReedSolomon(nsym=32)
        recovered = b"".join(
            rs.decode(desc[i:i + 255]).data for i in range(0, len(desc), 255))
        rx_stream = recovered[:len(stream218)]
        assert rx_stream == stream218, "级联解码后须逐字节还原 DSLWP 包流"

        dec = SsdvDecoder(dialect=DIALECT_DSLWP)
        img = SsdvImage(IMG_ID)
        for pkt in dec.feed(rx_stream):
            if pkt is not None:
                img.add(pkt)
        return img.build()

    def test_noiseless_iq_to_jpeg(self):
        """无噪声：IQ→BPSK 解调→ASM→Viterbi→解扰→RS→218B→JPEG，全 MCU 无缺失。"""
        rgb = _gradient_rgb(48, 48)
        iq, stream218, n_coded = self._tx_iq(rgb)
        frame = self._demod_to_frame(iq, n_coded)
        res = self._frame_to_image(frame, stream218)
        assert not res.empty
        assert (res.width, res.height) == (48, 48)
        assert res.mcu_count == 36
        assert res.missing_mcus == []
        assert len(res.received_mcus) == res.mcu_count
        assert res.eoi_seen
        out = Image.open(io.BytesIO(res.jpeg))
        out.load()
        assert out.size == (48, 48)

    def test_fec_corrects_injected_coded_errors(self):
        """解调后的编码帧里确定性注入 ~2% 比特翻转，Viterbi+RS 仍救回全图。

        （等价 AWGN 硬判决误码；开发期已实测原始 BER 1.66% 下全图恢复——见
        docs/learn/phase45 报告。此处用受控注入保证 pytest 确定性，且 ASM 不受影响。）
        """
        rgb = _gradient_rgb(48, 48)
        iq, stream218, n_coded = self._tx_iq(rgb)
        frame = bytearray(self._demod_to_frame(iq, n_coded))
        rng = np.random.default_rng(SEED + 10)
        nflip = 0
        for byte_idx in range(len(frame)):
            for bit_idx in range(8):
                if rng.random() < 0.02:
                    frame[byte_idx] ^= (1 << (7 - bit_idx))
                    nflip += 1
        res = self._frame_to_image(bytes(frame), stream218)
        assert not res.empty
        assert res.mcu_count == 36
        assert res.missing_mcus == [], \
            f"Viterbi+RS 应纠正能力内的 {nflip} 处编码比特翻转，实得缺失 {res.missing_mcus}"
        assert len(res.received_mcus) == res.mcu_count
        Image.open(io.BytesIO(res.jpeg)).load()


# ===========================================================================
# 4. 诚实空态：纯噪声 IQ 不出假帧/假图
# ===========================================================================
class TestCascadeHonestEmpty:
    def test_pure_noise_iq_no_fake_frame(self):
        rng = np.random.default_rng(SEED + 11)
        noise = (rng.standard_normal(200000)
                 + 1j * rng.standard_normal(200000)).astype(np.complex64)
        bits = demod_bpsk(noise, 48000.0, 4800.0, f_offset=0.0)
        framer = AsmFramer(frame_bits=8000, max_hamming=0)
        assert framer.feed_bits(bits.tolist()) == [], \
            "纯噪声 IQ 不得 ASM 同步出假帧"

    def test_pure_noise_bytes_no_fake_dslwp_packet(self):
        noise = np.random.default_rng(SEED + 12).integers(
            0, 256, size=4096, dtype=np.uint8).tobytes()
        dec = SsdvDecoder(dialect=DIALECT_DSLWP)
        out = [p for p in dec.feed(noise) if p is not None]
        assert out == [], "纯噪声字节流不得解出假 DSLWP 包"
