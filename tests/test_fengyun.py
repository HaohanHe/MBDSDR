"""
风云系列卫星接收管道往返验证测试
=====================================

覆盖三个闭环：
  1. FY-4 LRIT/HRIT 合成往返：合成测试图 → xRIT 编码 → CCSDS VCDU 封装
     → BPSK 调制/解调 → 帧传输层重组 → FY-4 专用头解析 → 段重组 → PNG。
  2. FY-3 HRPT 合成往返：合成扫描线 → HRPT 小帧(60-bit 同步字) → BPSK 调制/解调
     → 帧同步 → AVHRR 通道提取 → 图像重建 → PNG。
  3. FY-3 过境预测：用合成 TLE 经 orbit.py SGP4 传播，输出仰角/方位/多普勒曲线。

运行: python3 -m pytest tests/test_fengyun.py -v
输出 PNG 落盘 mbdsdr_ai/artifacts/
"""

import os
import sys
import time
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.fengyun_sat import (  # noqa: E402
    FY4A_LRIT_SYMBOL_RATE,
    FY4B_LRIT_SYMBOL_RATE,
    FY4_LRIT_FREQ_HZ,
    FY3_HRPT_SYMBOL_RATE,
    FY3_SYNTH_TLE,
    build_fy4_image_information,
    build_fy4_xrit_file,
    file_to_vcdus,
    bytes_to_bits,
    bits_to_bpsk,
    bpsk_to_bits,
    bits_to_bytes,
    decode_fy4_vcdus,
    build_fy3_hrpt_frame,
    decode_fy3_hrpt_bits,
    fy3_pass_curve,
    save_png,
    register_tool_registry,
)

ART_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "mbdsdr_ai", "artifacts")


def _make_test_image(w: int, h: int) -> np.ndarray:
    """造一张有横条+斜纹的可辨识测试灰度图。"""
    img = np.zeros((h, w), dtype=np.uint8)
    for r in range(h):
        img[r, :] = int(r * 255 // max(1, h - 1))
    # 叠加竖条，便于肉眼辨认方向
    for c in range(w):
        if (c // 8) % 2 == 0:
            img[:, c] = np.clip(img[:, c].astype(int) + 40, 0, 255).astype(np.uint8)
    return img


class TestFY4Roundtrip(unittest.TestCase):
    """第一部分：FY-4 LRIT 编码→调制→解调→解码→出图。"""

    def test_fy4_lrit_roundtrip(self):
        W, H, SEG_ROWS, NSEG = 64, 32, 8, 4
        img = _make_test_image(W, H)

        # 1) 每段组 xRIT 文件 → 封装 VCDU
        vcdus_all = []
        for k in range(NSEG):
            rows = img[k * SEG_ROWS:(k + 1) * SEG_ROWS, :]
            info = build_fy4_image_information(
                satellite_name="FY-4A", instrument_name="AGRI",
                bit_per_pixel=8, columns=W, lines=SEG_ROWS,
                compression_flag=0, channel_number=1,
                total_segments=NSEG, current_segment_number=k + 1,
                current_segment_pos=k + 1, current_segment_line_pos=k * SEG_ROWS)
            f = build_fy4_xrit_file(info, rows.tobytes(),
                                    annotation=f"FY4A-AGRI-CH1-SEG{k+1}")
            vcdus_all += file_to_vcdus(f, scid=0x20, vcid=1, apid=0x10, chunk=300)

        self.assertGreater(len(vcdus_all), 0)

        # 2) BPSK 信道（编码→调制→加噪→硬判决解调）
        stream = b"".join(vcdus_all)
        tx_bits = bytes_to_bits(stream)
        sym = bits_to_bpsk(tx_bits, seed=1)
        rx_bits = bpsk_to_bits(sym)
        rx_stream = bits_to_bytes(rx_bits)
        self.assertEqual(len(rx_stream), len(stream))
        rx_vcdus = [rx_stream[i * 892:(i + 1) * 892]
                    for i in range(len(rx_stream) // 892)]
        self.assertEqual(len(rx_vcdus), len(vcdus_all))

        # 3) 解码出图
        imgs = decode_fy4_vcdus(rx_vcdus)
        self.assertEqual(len(imgs), 1)
        out = imgs[0]
        self.assertEqual((out.height, out.width), (H, W))
        self.assertEqual(out.channel, 1)

        # 4) 图像必须可辨识：与原图逐像素一致（无噪闭环）
        diff = np.abs(out.pixels.astype(int) - img.astype(int))
        self.assertEqual(int(diff.max()), 0,
                         f"FY-4 重建图与原图最大差 {diff.max()}")

        png = save_png(out.pixels, os.path.join(ART_DIR, "fy4_lrit_roundtrip.png"))
        self.assertTrue(os.path.exists(png))
        print(f"\n[FY-4] 物理层: {FY4A_LRIT_SYMBOL_RATE}/90k & {FY4B_LRIT_SYMBOL_RATE}/120k sym/s, "
              f"{FY4_LRIT_FREQ_HZ/1e6:.0f}MHz, RRC α=0.25 (FengYun-4.json)")


class TestFY3HRTPRoundtrip(unittest.TestCase):
    """第二部分：FY-3 HRPT 合成扫描线 → BPSK → 帧同步 → 重建 PNG。"""

    def test_fy3_hrpt_roundtrip(self):
        W, H = 64, 16
        img = _make_test_image(W, H)

        # 1) 逐行组 HRPT 小帧 → 拼接比特
        all_bits = [build_fy3_hrpt_frame(img[r], r, W) for r in range(H)]
        bits = np.concatenate(all_bits)

        # 2) BPSK 调制/解调
        sym = bits_to_bpsk(bits, seed=7)
        rx_bits = bpsk_to_bits(sym).astype(np.float32)

        # 3) 帧同步 + AVHRR 提取
        rec = decode_fy3_hrpt_bits(rx_bits, W, max_lines=H)
        self.assertEqual(rec.shape, (H, W), f"重建形状 {rec.shape}")

        diff = np.abs(rec.astype(int) - img.astype(int))
        self.assertEqual(int(diff.max()), 0,
                         f"FY-3 HRPT 重建最大差 {diff.max()}")

        png = save_png(rec, os.path.join(ART_DIR, "fy3_hrpt_roundtrip.png"))
        self.assertTrue(os.path.exists(png))
        print(f"\n[FY-3] HRPT {FY3_HRPT_SYMBOL_RATE} bps BPSK, "
              f"sync 0x0A116FD719D83C95 (noaa_deframer.cpp)")


class TestFY3PassPredict(unittest.TestCase):
    """第三部分：合成 TLE 驱动的过境预测，输出仰角/方位/多普勒曲线。"""

    def test_fy3_pass_curve(self):
        lat, lon = 39.9, 116.4   # 北京附近
        t0 = time.time()

        # 粗扫 6 小时，找到仰角峰值时刻（合成 TLE 几何）
        peak_t, peak_el = t0, -90.0
        for h in range(0, 6):
            c = fy3_pass_curve(FY3_SYNTH_TLE, lat, lon, 0.0,
                               t0 + h * 3600, 3600, step_s=60)
            if not c:
                continue
            m = max(c, key=lambda x: x["elevation_deg"])
            if m["elevation_deg"] > peak_el:
                peak_el = m["elevation_deg"]
                peak_t = m["t_unix"]

        self.assertGreater(peak_el, 0.0, "合成 TLE 应在 6h 内给出一次过境")

        # 以峰值为中心取 ±10 min 细曲线
        curve = fy3_pass_curve(FY3_SYNTH_TLE, lat, lon, 0.0,
                               peak_t - 600, 1200, step_s=10)
        self.assertGreater(len(curve), 5)

        els = [c["elevation_deg"] for c in curve]
        azs = [c["azimuth_deg"] for c in curve]
        dps = [c["doppler_shift_hz"] for c in curve]
        rrs = [c["range_rate_kms"] for c in curve]

        # 过境曲线特征：细采样应抓到近顶峰值（>45°），仰角先升后降，
        # 方位在合理范围，多普勒过零变号（靠近→远离）
        self.assertGreater(max(els), 45.0, f"细采样峰值仰角过低: {max(els):.1f}")
        self.assertLess(min(els), max(els), "仰角曲线应有起伏")
        self.assertGreater(min(rrs), -8.0)
        self.assertLess(max(rrs), 8.0)
        # 多普勒在过境中应由负变正（靠近→远离）或至少覆盖 0
        self.assertTrue(min(dps) < 0 < max(dps),
                        f"多普勒应过零: min={min(dps):.0f} max={max(dps):.0f}")
        # 方位角应在合理范围
        for az in azs:
            self.assertTrue(0.0 <= az <= 360.0)

        # 落盘曲线（npy，便于画图；不进 git）
        os.makedirs(ART_DIR, exist_ok=True)
        np.save(os.path.join(ART_DIR, "fy3_pass_curve.npy"),
                np.array([[c["t_unix"], c["elevation_deg"], c["azimuth_deg"],
                           c["doppler_shift_hz"]] for c in curve]))
        print(f"\n[FY-3] 过境峰值仰角 {peak_el:.1f}°, "
              f"多普勒 {min(dps):.0f}~{max(dps):.0f} Hz, 曲线点 {len(curve)}")


class TestToolRegistry(unittest.TestCase):
    """工具注册冒烟。"""

    def test_register(self):
        from mbdsdr_ai.tool_registry import ToolRegistry
        reg = ToolRegistry()
        register_tool_registry(reg)
        for name in ("fy4_lrit_decode", "fy3_hrpt_decode", "fy3_pass_predict"):
            self.assertIn(name, reg.tools)


if __name__ == "__main__":
    unittest.main(verbosity=2)
