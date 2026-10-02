# SPDX-License-Identifier: MIT
"""image_enhance.py 确定性测试（合成图，禁真卫星图依赖）。

运行：pytest tests/test_image_enhance.py -v
"""

from __future__ import annotations

import os
import numpy as np
import pytest

from mbdsdr_ai import image_enhance as E


# ---------------------------------------------------------------------------
# 合成测试图：已知内容、固定形状
# ---------------------------------------------------------------------------
def _flat(v: float = 0.5, h: int = 8, w: int = 8) -> np.ndarray:
    """全图常数灰度图（float [0,1]）。"""
    return np.full((h, w), v, dtype=np.float64)


def _gradient(h: int = 4, w: int = 256) -> np.ndarray:
    """水平渐变：左 0 -> 右 1（float [0,1]）。"""
    return np.tile(np.linspace(0.0, 1.0, w, dtype=np.float64), (h, 1))


def _narrow_band(h: int = 4, w: int = 128) -> np.ndarray:
    """低对比度窄带图：全部挤在 0.4~0.5 之间。"""
    base = np.linspace(0.4, 0.5, w, dtype=np.float64)
    return np.tile(base, (h, 1))


# ---------------------------------------------------------------------------
# 1. brightness_contrast: 恒等（b=0,c=0）应保持输入
# ---------------------------------------------------------------------------
def test_bc_identity():
    img = _gradient()
    out = E.brightness_contrast(img, brightness=0.0, contrast=0.0)
    # slant = tan(π/4) = 1；b=0 时 (v-0.5)*1+0.5 = v
    np.testing.assert_allclose(out, img, atol=1e-9)


def test_bc_contrast_increases_slope():
    # contrast>0 应让中间值拉开：0.5 不变，0.25 -> 更小，0.75 -> 更大
    # 用温和 contrast=0.1 避免 clamp 到 [0,1]
    img = np.array([[0.25, 0.5, 0.75]], dtype=np.float64)
    out = E.brightness_contrast(img, brightness=0.0, contrast=0.1)
    slant = np.tan((0.1 + 1.0) * np.pi / 4.0)  # tan(55°) ≈ 1.428
    expected = (img - 0.5) * slant + 0.5
    np.testing.assert_allclose(out, expected, atol=1e-9)
    # 0.25 应被压暗
    assert out[0, 0] < 0.25
    # 0.75 应被提亮
    assert out[0, 2] > 0.75


def test_bc_clamp():
    # 极端对比度应 clamp 到 [0,1]
    img = np.array([[0.0, 0.5, 1.0]], dtype=np.float64)
    out = E.brightness_contrast(img, brightness=0.0, contrast=1.0)
    assert out.min() >= 0.0
    assert out.max() <= 1.0


def test_bc_uint8_roundtrip():
    # uint8 输入应输出 uint8
    img = np.array([[0, 128, 255]], dtype=np.uint8)
    out = E.brightness_contrast(img, contrast=0.3)
    assert out.dtype == np.uint8
    assert out.shape == img.shape


# ---------------------------------------------------------------------------
# 2. stretch_black_white: 线性映射
# ---------------------------------------------------------------------------
def test_stretch_linear():
    # 输入 [0.2, 0.8] -> 输出 [0,1]
    img = np.array([[0.2, 0.5, 0.8]], dtype=np.float64)
    out = E.stretch_black_white(img, black=0.2, white=0.8)
    np.testing.assert_allclose(out.flatten(), [0.0, 0.5, 1.0], atol=1e-9)


def test_stretch_clamp():
    # 超出范围应 clamp
    img = np.array([[0.0, 0.5, 1.0]], dtype=np.float64)
    out = E.stretch_black_white(img, black=0.2, white=0.8)
    assert out[0, 0] == 0.0   # 低于 black -> 0
    assert out[0, 2] == 1.0   # 高于 white -> 1


def test_stretch_bad_range():
    with pytest.raises(ValueError):
        E.stretch_black_white(_flat(0.5), black=0.8, white=0.2)


# ---------------------------------------------------------------------------
# 3. auto_stretch: 窄带图被拉到满量程
# ---------------------------------------------------------------------------
def test_auto_stretch_expands_range():
    img = _narrow_band()         # 范围 0.4~0.5
    out = E.auto_stretch(img, low_pct=0.0, high_pct=100.0)
    assert out.min() == pytest.approx(0.0, abs=1e-9)
    assert out.max() == pytest.approx(1.0, abs=1e-9)


def test_auto_stretch_flat_image():
    # 全平坦图不应崩溃
    img = _flat(0.5, h=4, w=4)
    out = E.auto_stretch(img)
    assert out.shape == img.shape


# ---------------------------------------------------------------------------
# 4. histogram_equalize: 均匀分布 -> 近似均匀
# ---------------------------------------------------------------------------
def test_equalize_uniform_input():
    # 均匀渐变图（每级等概率）均衡后仍应覆盖全量程
    img = _gradient(w=256)
    out = E.histogram_equalize(img)
    # 输出应覆盖 0..1
    assert out.min() <= 0.01
    assert out.max() >= 0.99


def test_equalize_narrow_band_spreads():
    img = _narrow_band(w=256)    # 全部挤在 0.4~0.5
    out = E.histogram_equalize(img)
    # 均衡后动态范围应显著拉大
    assert out.max() - out.min() > 0.5


def test_equalize_constant_image():
    # 全常数图不应崩溃
    img = _flat(0.5, h=4, w=4)
    out = E.histogram_equalize(img)
    assert out.shape == img.shape


# ---------------------------------------------------------------------------
# 5. apply_lut: 固定输入 -> 固定 RGB
# ---------------------------------------------------------------------------
def test_gray_lut_identity():
    # gray LUT: input 0 -> (0,0,0); input 255 -> (255,255,255)
    img = np.array([[0, 128, 255]], dtype=np.uint8)
    out = E.apply_lut(img, lut="gray")
    assert out.shape == (1, 3, 3)
    np.testing.assert_array_equal(out[0, 0], [0, 0, 0])
    np.testing.assert_array_equal(out[0, 2], [255, 255, 255])


def test_jet_lut_endpoints():
    # jet LUT: input 0 -> 深蓝；input 255 -> 深红
    img = np.array([[0, 255]], dtype=np.uint8)
    out = E.apply_lut(img, lut="jet")
    # 低端：B 分量 > R,G
    assert out[0, 0, 2] > out[0, 0, 0]
    # 高端：R 分量 > G,B
    assert out[0, 1, 0] > out[0, 1, 2]


def test_unknown_lut():
    with pytest.raises(ValueError):
        E.apply_lut(np.zeros((4, 4), dtype=np.uint8), lut="not_a_lut")


def test_lut_table_shapes():
    for name, table in E.BUILTIN_LUTS.items():
        assert table.shape == (256, 3), f"LUT {name} shape {table.shape}"
        assert table.dtype == np.uint8


# ---------------------------------------------------------------------------
# 6. enhance_apt: 端到端
# ---------------------------------------------------------------------------
def test_enhance_apt_gray_output():
    img = _narrow_band(w=128)
    out = E.enhance_apt(img, lut="gray")
    assert out.ndim == 2
    assert out.dtype == np.uint8
    assert out.max() >= 200   # 自动色阶后应拉到近满量程


def test_enhance_apt_rgb_output():
    img = _narrow_band(w=128)
    out = E.enhance_apt(img, lut="iron")
    assert out.ndim == 3
    assert out.shape[2] == 3
    assert out.dtype == np.uint8


def test_enhance_apt_disable_auto_level():
    img = _narrow_band(w=128)   # 范围 0.4~0.5
    out = E.enhance_apt(img, auto_level=False, lut="gray")
    # 不自动色阶时，输出应仍在窄带内（不被拉伸到 0~255）
    assert out.max() < 200


# ---------------------------------------------------------------------------
# 7. save_enhanced_png: 落盘烟雾测试
# ---------------------------------------------------------------------------
def test_save_png(tmp_path):
    img = (_gradient(w=64) * 255).astype(np.uint8)
    p = tmp_path / "test_out.png"
    path = E.save_enhanced_png(img, str(p))
    assert os.path.exists(path)
    # 文件非空
    assert os.path.getsize(path) > 0


# ---------------------------------------------------------------------------
# 8. 确定性：同输入两次运行结果逐像素一致
# ---------------------------------------------------------------------------
def test_deterministic():
    img = _narrow_band(w=64)
    out1 = E.enhance_apt(img, contrast=0.2, equalize=True, lut="jet")
    out2 = E.enhance_apt(img, contrast=0.2, equalize=True, lut="jet")
    np.testing.assert_array_equal(out1, out2)
