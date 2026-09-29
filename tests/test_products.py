# SPDX-License-Identifier: MIT
"""mbdsdr_ai.satellite.products 确定性单测。（合成向量，非硬件 / NOT HARDWARE）

对照 SatDump src-core/products/image_product.*（docs/learn/satdump.md §11）：
  1. ImageProduct 通道存取 / shape；
  2. compose_rgb 三通道合成 → (H,W,3) uint8；
  3. compose_noaa_falsecolor 假彩色（A/B 两通道分支）；
  4. set_projection 写入 TLE + timestamps；
  5. estimate_geocorrection_offset 确定性返回；
  6. save_png 落盘。

运行：pytest tests/test_products.py -v
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.satellite import ImageProduct  # noqa: E402


@pytest.fixture
def product():
    p = ImageProduct(instrument_name="avhrr", satellite="NOAA-19")
    rng = np.random.default_rng(0)
    H, W = 40, 909
    # 三个可区分通道：a=可见光(梯度), b=红外(平缓), "AVHRR-4"=另一红外
    a = np.tile(np.linspace(0, 255, W), (H, 1)).astype(np.uint8)
    b = (128 + 30 * np.sin(np.linspace(0, 6.28, W))).astype(np.uint8)
    b = np.tile(b, (H, 1)).astype(np.uint8)
    c4 = rng.integers(0, 255, size=(H, W), dtype=np.uint8)
    p.add_channel("a", a)
    p.add_channel("b", b)
    p.add_channel("AVHRR-4", c4)
    return p


def test_shape_and_channels(product):
    assert product.shape == (40, 909)
    assert set(product.channels) == {"a", "b", "AVHRR-4"}


def test_compose_rgb(product):
    rgb = product.compose_rgb("a", "b", "AVHRR-4")
    assert rgb.shape == (40, 909, 3)
    assert rgb.dtype == np.uint8
    assert rgb.min() >= 0 and rgb.max() <= 255


def test_compose_rgb_missing_channel(product):
    with pytest.raises(KeyError):
        product.compose_rgb("a", "b", "nope")


def test_falsecolor_two_channel(product):
    # 只有 a/b 时走 APT 分支
    p = ImageProduct(instrument_name="avhrr")
    p.add_channel("a", product.channels["a"])
    p.add_channel("b", product.channels["b"])
    rgb = p.compose_noaa_falsecolor()
    assert rgb is not None
    assert rgb.shape[2] == 3


def test_falsecolor_avhrr_named(product):
    rgb = product.compose_noaa_falsecolor()
    # product 含 AVHRR-2? 没有 → 回退 a/b 分支
    assert rgb is not None


def test_set_projection(product):
    tle = ("1 TEST", "2 TEST")
    ts = [1704153600.0 + i * 0.5 for i in range(40)]
    product.set_projection(tle, ts, proj_type="noaa_apt_single_line")
    assert product.projection["type"] == "noaa_apt_single_line"
    assert product.projection["tle"]["line1"] == "1 TEST"
    assert len(product.timestamps) == 40


def test_geocorrection_offset(product):
    ts = [1704153600.0 + i * 0.5 for i in range(40)]
    product.set_projection(("1 T", "2 T"), ts)
    out = product.estimate_geocorrection_offset("a")
    assert out["offset_s"] == 0.0
    assert out["rows"] == 40
    assert out["reference_line_time"] == pytest.approx(1704153600.0 + 20 * 0.5)


def test_save_png(product, tmp_path):
    p = tmp_path / "out.png"
    path = product.save_png(str(p))
    assert os.path.exists(path)
    # 单通道
    p2 = tmp_path / "a.png"
    product.save_png(str(p2), channel="a")
    assert os.path.exists(str(p2))
