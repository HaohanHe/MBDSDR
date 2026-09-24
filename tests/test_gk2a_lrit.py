"""
GK-2A LRIT 全管道端到端往返测试
==================================

验证 mbdsdr_ai/gk2a_lrit.py 从 IQ 采样到云图 PNG 的完整接收链：
  - BPSK 调制/RRC 成形 → AWGN → RRC 匹配滤波 → 载波恢复 → 符号定时
  - Viterbi 软判决译码 (K=7, R=1/2, poly 0x4F/0x6D)
  - 帧同步 0x1ACFFC1D → CCSDS 解扰 → RS(255,223,I=4) 译码
  - VCDU → M_PDU → TP_PDU → SessionPDU → GK-2A 文件头 → 图像组装
  - 输出真实 PNG 到 mbdsdr_ai/artifacts/

运行: python3 -m pytest tests/test_gk2a_lrit.py -v
"""

import os
import sys
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.gk2a_lrit import (  # noqa: E402
    GK2ADecodeResult,
    GK2AImageAssembler,
    GK2ALRITReassembler,
    build_gk2a_lrit_file,
    bpsk_demod,
    cadu_to_iq,
    conv_encode_bits,
    decode_iq_to_image,
    derandomize_ccsds,
    frame_sync_search,
    file_to_vcdus,
    parse_gk2a_headers,
    register_tool_registry,
    rs_decode_interleaved,
    rs_encode,
    rs_encode_interleaved,
    vcdus_to_cadu,
    ViterbiDecoder,
    SYNC_WORD,
    SYNC_WORD_BYTES,
    CADU_BYTES,
    VCDU_LEN,
    RS_N,
    RS_K,
    DERAND_OFFSET,
    CCSDS_PN,
    GK2A_SYMBOL_RATE,
    GK2A_RRC_ALPHA,
    GK2A_LRIT_FREQ_HZ,
)

ARTIFACTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "mbdsdr_ai", "artifacts",
)


def _make_test_image(h: int = 48, w: int = 96) -> np.ndarray:
    """生成合成全圆盘测试图：径向渐变 + 十字标记 + 文字方块。"""
    y, x = np.mgrid[0:h, 0:w]
    cy, cx = h / 2.0, w / 2.0
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2)
    rmax = r.max()
    img = (255 * (1 - r / rmax)).astype(np.uint8)
    # 十字标记（模拟地标）
    img[h // 2 - 2:h // 2 + 2, :] = 255
    img[:, w // 2 - 2:w // 2 + 2] = 255
    # 方块（模拟文字标记）
    img[4:12, 4:20] = 0
    img[h - 12:h - 4, w - 20:w - 4] = 200
    return img


class TestGK2APhysicalLayer(unittest.TestCase):
    """物理层参数与基础算法单元测试。"""

    def test_constants_match_satdump(self):
        """关键常量必须与 SatDump GK2A.json 一致。"""
        self.assertAlmostEqual(GK2A_SYMBOL_RATE, 128e3, places=0)
        self.assertAlmostEqual(GK2A_RRC_ALPHA, 0.5)
        self.assertEqual(SYNC_WORD, 0x1ACFFC1D)
        self.assertEqual(CADU_BYTES, 1024)
        self.assertEqual(VCDU_LEN, 892)
        self.assertEqual(RS_N, 255)
        self.assertEqual(RS_K, 223)
        self.assertEqual(DERAND_OFFSET, 4)

    def test_viterbi_roundtrip_ideal(self):
        """卷积编码 + Viterbi 理想通道 BER=0。"""
        rng = np.random.default_rng(42)
        bits = rng.integers(0, 2, size=400).astype(np.uint8)
        enc = conv_encode_bits(bits)
        soft = 1.0 - 2.0 * enc.astype(np.float64)
        dec = ViterbiDecoder().decode(soft)
        self.assertEqual(np.mean(dec != bits), 0.0)

    def test_viterbi_awgn(self):
        """卷积 + Viterbi 在 Eb/N0≈6dB 下 BER 很低。"""
        rng = np.random.default_rng(7)
        bits = rng.integers(0, 2, size=800).astype(np.uint8)
        enc = conv_encode_bits(bits)
        soft = 1.0 - 2.0 * enc.astype(np.float64)
        # Eb/N0 ~6dB → noise std ~0.7
        soft += rng.standard_normal(len(soft)) * 0.7
        dec = ViterbiDecoder().decode(soft)
        ber = np.mean(dec != bits)
        self.assertLess(ber, 0.05, f"Viterbi AWGN BER too high: {ber}")

    def test_rs_encode_decode_noerrors(self):
        """RS(255,223) 无错译码数据一致。"""
        from mbdsdr_ai.gk2a_lrit import rs_decode
        rng = np.random.default_rng(1)
        data = rng.integers(0, 256, size=RS_K).astype(np.uint8).tolist()
        cw = bytearray(data + [0] * 32)
        rs_encode(cw)
        cw2 = bytearray(cw)
        self.assertEqual(rs_decode(cw2), 0)
        self.assertEqual(list(cw2[:RS_K]), data)

    def test_rs_corrects_errors(self):
        """RS 能纠正 ≤16 字节错误。"""
        from mbdsdr_ai.gk2a_lrit import rs_decode
        rng = np.random.default_rng(2)
        data = rng.integers(0, 256, size=RS_K).astype(np.uint8).tolist()
        cw = bytearray(data + [0] * 32)
        rs_encode(cw)
        cw3 = bytearray(cw)
        for pos in [10, 50, 100, 200, 250]:
            cw3[pos] ^= 0xFF
        self.assertEqual(rs_decode(cw3), 5)
        self.assertEqual(list(cw3[:RS_K]), data)

    def test_derandomize(self):
        """CCSDS PN 解扰自逆。"""
        data = bytearray(range(256))
        derandomize_ccsds(data, len(data))
        derandomize_ccsds(data, len(data))
        self.assertEqual(list(data), list(range(256)))


class TestGK2AEndToEnd(unittest.TestCase):
    """端到端往返：图像 → IQ → 解码 → PNG。"""

    @classmethod
    def setUpClass(cls):
        os.makedirs(ARTIFACTS_DIR, exist_ok=True)
        cls.img = _make_test_image()
        # 编码端：图像 → LRIT → VCDU → CADU → IQ
        file = build_gk2a_lrit_file(cls.img, line_nb=1, total_segments=1, image_seq=1)
        vcdus = file_to_vcdus(file)
        cadu = vcdus_to_cadu(vcdus)
        cls.cadu = cadu
        cls.iq = cadu_to_iq(cadu, sps=8)

    def test_ideal_channel_pixel_perfect(self):
        """无噪通道：输出图像与输入像素级一致。"""
        out_png = os.path.join(ARTIFACTS_DIR, "gk2a_lrit_ideal.png")
        res = decode_iq_to_image(self.iq, out_png, sps=8)
        self.assertTrue(res.success, f"解码失败: {res.error}")
        self.assertTrue(os.path.exists(out_png))
        out = np.array(Image.open(out_png))
        self.assertEqual(out.shape, self.img.shape)
        self.assertTrue(np.array_equal(out, self.img),
                        "理想通道输出应与输入像素级一致")

    def test_awgn_channel_reconstructs(self):
        """AWGN 通道 (Eb/N0 ~6dB)：SSIM/相关性 > 0.8。"""
        rng = np.random.default_rng(123)
        # 加复高斯噪声
        sig_pow = np.mean(np.abs(self.iq) ** 2)
        noise_std = np.sqrt(sig_pow / (10 ** 0.6))  # Eb/N0 ~6dB
        noise = (rng.standard_normal(len(self.iq)) +
                 1j * rng.standard_normal(len(self.iq))) * noise_std
        iq_noisy = self.iq + noise

        out_png = os.path.join(ARTIFACTS_DIR, "gk2a_lrit_awgn.png")
        res = decode_iq_to_image(iq_noisy, out_png, sps=8)
        self.assertTrue(res.success, f"加噪解码失败: {res.error}")
        out = np.array(Image.open(out_png))
        self.assertEqual(out.shape, self.img.shape)

        # 归一化互相关作为结构相似性度量
        a = self.img.astype(np.float64)
        b = out.astype(np.float64)
        a = a - a.mean()
        b = b - b.mean()
        corr = (a * b).sum() / (np.sqrt((a ** 2).sum() * (b ** 2).sum()) + 1e-9)
        self.assertGreater(corr, 0.8,
                           f"加噪通道相关性 {corr:.3f} < 0.8")

    def test_frame_sync_found(self):
        """IQ 解码后能找到同步字。"""
        soft = bpsk_demod(self.iq, sps=8)
        bits = ViterbiDecoder().decode(soft)
        off = frame_sync_search(bits)
        self.assertGreaterEqual(off, 0, "未找到同步字")


class TestGK2AToolRegistry(unittest.TestCase):
    """工具注册测试。"""

    def test_register_tool(self):
        """register_tool_registry 注册 gk2a_lrit_decode。"""
        from mbdsdr_ai.tool_registry import ToolRegistry
        reg = ToolRegistry()
        register_tool_registry(reg)
        self.assertIn("gk2a_lrit_decode", reg.tools)
        spec = reg.tools["gk2a_lrit_decode"]["definition"]["function"]
        self.assertIn("iq_path", spec["parameters"]["properties"])
        self.assertIn("out_png", spec["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()
