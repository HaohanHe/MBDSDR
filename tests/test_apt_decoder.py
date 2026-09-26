"""mbdsdr_ai.satellite.decoders 确定性单测。

对照 SatDump plugins/analog_support/noaa_apt/（docs/learn/satdump.md §10）：
  1. 合成 APT 音频（已知 A/B 测试图）→ 解码 → 通道图像可还原；
  2. 通道 A/B 分离正确（A=水平灰阶，B=竖直条带，互不混淆）；
  3. auto_detect_decoder 按频率选解码器；
  4. save_png 落盘。

运行：pytest tests/test_apt_decoder.py -v
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.satellite import NOAAAPTDecoder, auto_detect_decoder  # noqa: E402
from mbdsdr_ai.noaa_apt_lite import (  # noqa: E402
    synthesize_apt_audio, synthesize_test_images, APT_IMAGE_LEN,
)


@pytest.fixture(scope="module")
def decoded():
    """合成 APT 音频并解码（确定性 rng；配置与 tests/apt_meteor_roundtrip 一致）。"""
    img_a, img_b = synthesize_test_images(n_lines=120)
    rng = np.random.default_rng(123)
    audio = synthesize_apt_audio(img_a, img_b, fs=24000, rng=rng)
    dec = NOAAAPTDecoder()
    res = dec.decode_audio(audio, 24000.0, satellite="NOAA-19")
    return res, img_a, img_b


def test_apt_present(decoded):
    res, _, _ = decoded
    assert res.present, f"解码失败: {res.reason}, lock={res.lock_ratio}"
    assert res.lines >= 10
    assert res.lock_ratio > 0.6


def test_channel_shapes(decoded):
    res, _, _ = decoded
    assert res.channel_a is not None and res.channel_b is not None
    assert res.channel_a.shape[1] == APT_IMAGE_LEN
    assert res.channel_b.shape[1] == APT_IMAGE_LEN


def test_channel_separation(decoded):
    """A=水平灰阶（行间相似），B=竖直条带（行内列间交替）。

    结构断言（对照 tests/apt_meteor_roundtrip.py:47-50）：
      * A 水平渐变 → 同一列跨行标准差小（std(axis=0) 小）；
      * B 竖直条带 → 同一行跨列标准差大（std(axis=1) 大）。
    """
    res, _, _ = decoded
    a = res.channel_a.astype(np.float64)
    b = res.channel_b.astype(np.float64)
    # A 图：水平灰带 → 沿列方向（跨行）方差小
    assert a.std(axis=0).mean() < 15.0, "通道 A 应近似水平渐变"
    # B 图：竖直条带 → 行内跨列方差大
    assert b.std(axis=1).mean() > 30.0, "通道 B 应含竖直条带"
    # A/B 不相同（分离正确）
    assert not np.allclose(a, b, atol=15)


def test_two_channels_distinct(decoded):
    """解码出 A/B 两个等尺寸通道，且内容不同（非全黑/全白/互相复制）。"""
    res, _, _ = decoded
    a = res.channel_a.astype(np.float64)
    b = res.channel_b.astype(np.float64)
    assert a.shape == b.shape
    assert a.std() > 5 and b.std() > 5, "两通道都应有内容"
    assert not np.allclose(a, b, atol=10), "A/B 通道内容应不同"


def test_auto_detect_decoder():
    r = auto_detect_decoder(137.1e6)
    assert r["decoder"] == "noaa_apt"
    assert r["satellite"] == "NOAA-19"
    r2 = auto_detect_decoder(1704.5e6)
    assert r2["decoder"] == "fengyun_mpt"
    assert "reason" in auto_detect_decoder(100e6)


def test_save_png(decoded, tmp_path):
    res, _, _ = decoded
    paths = NOAAAPTDecoder.save_png(res, str(tmp_path / "n19"))
    assert os.path.exists(paths["a"])
    assert os.path.exists(paths["b"])
    assert os.path.exists(paths["combo"])
