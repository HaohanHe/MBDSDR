# SPDX-License-Identifier: MIT
"""
image_enhance.py — SatDump 式气象卫星云图增强管线（干净室 MIT 实现）
=====================================================================

本模块为 NOAA APT / 其它单通道气象云图提供轻量、确定性、纯 numpy 的图像增强。
算法机制参照 SatDump（GPLv3）公开源码的设计思路独立重写，未复制其代码：

  1. brightness_contrast()  —— 切比雪夫/tan-slant 亮度对比度曲线
       参照 SatDump src-core/image/brightness_contrast.cpp:8-38 的机制：
         slant = tan((contrast+1)*π/4)
         brightness<0: v *= (1+b);  brightness>0: v += (1-v)*b
         v = (v-0.5)*slant + 0.5
  2. stretch_black_white() —— 黑/白点线性拉伸
       参照 SatDump module_noaa_apt_decoder.cpp:53-65 scale_val()：
         out = (in - black) / (white - black)，再 clamp 到 [0,1]。
  3. auto_stretch()         —— 百分位自动色阶（2%~98% 稳健定标）。
  4. histogram_equalize()   —— 全局直方图均衡（CDF 查表）。
       参照 SatDump src-core/image/histogram_utils.cpp:23-30 equalize_histogram()。
  5. apply_lut()            —— 伪彩色 LUT（jet / iron / grayscale），
       锚点色标线性插值到 256 项。
       参照 SatDump src-core/image/image_lut.cpp:8-22 create_lut/LUT_jet 机制。
  6. enhance_apt()         —— 一站式 APT 增强管线：自动色阶 -> 对比度 -> 可选伪彩。

输入约定：
  - 灰度图：2D ndarray (H, W)，dtype uint8 或 float（float 自动按 [0,1] 处理）。
  - 输出：uint8 灰度图或 uint8 RGB 图 (H, W, 3)。

所有函数均为纯函数（不修改输入），无随机状态，可在确定性测试中逐像素断言。
"""

from __future__ import annotations

import os
from typing import Optional, Tuple, Dict, List, Any

import numpy as np

__all__ = [
    "brightness_contrast",
    "stretch_black_white",
    "auto_stretch",
    "histogram_equalize",
    "apply_lut",
    "enhance_apt",
    "BUILTIN_LUTS",
]

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _to_float01(img: np.ndarray) -> Tuple[np.ndarray, bool]:
    """统一输入到 float64 [0,1]。返回 (arr, was_uint8)。"""
    if img.dtype == np.uint8:
        return img.astype(np.float64) / 255.0, True
    if np.issubdtype(img.dtype, np.integer):
        # 其它整数类型：按其 max 归一化（如 uint16）
        maxv = np.iinfo(img.dtype).max
        return img.astype(np.float64) / float(maxv), True
    return img.astype(np.float64, copy=True), False


def _to_uint8(img: np.ndarray) -> np.ndarray:
    """float [0,1] -> uint8，四舍五入，clamp。"""
    return (np.clip(img, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


# ---------------------------------------------------------------------------
# 1. 亮度 / 对比度（tan-slant 曲线）
# ---------------------------------------------------------------------------
def brightness_contrast(img: np.ndarray,
                        brightness: float = 0.0,
                        contrast: float = 0.0) -> np.ndarray:
    """SatDump 风格亮度/对比度调整。

    参数:
        img:        2D 灰度图（uint8 或 float [0,1]）。
        brightness: -1..1。<0 整体压暗（乘性），>0 整体抬亮（向 1 逼近）。
        contrast:   -1..1。0 = 不变；>0 增大反差（tan slant 曲线）；<0 降低反差。

    返回:
        与输入同形状；uint8 进则 uint8 出。
    """
    work, was_u8 = _to_float01(img)
    b = float(brightness) / 2.0           # SatDump: brightness_v = brightness/2
    slant = np.tan((float(contrast) + 1.0) * np.pi / 4.0)  # tan((c+1)*π/4)

    out = work.copy()
    if b < 0.0:
        out = out * (1.0 + b)
    elif b > 0.0:
        out = out + ((1.0 - out) * b)
    out = (out - 0.5) * slant + 0.5
    out = np.clip(out, 0.0, 1.0)

    if was_u8:
        return _to_uint8(out)
    return out


# ---------------------------------------------------------------------------
# 2. 黑/白点线性拉伸
# ---------------------------------------------------------------------------
def stretch_black_white(img: np.ndarray,
                        black: float = 0.0,
                        white: float = 1.0) -> np.ndarray:
    """线性黑/白点拉伸：把 [black, white] 映射到 [0, 1]。

    与 SatDump scale_val() 同机制（module_noaa_apt_decoder.cpp:53-65）：
        out = (in - black) / (white - black)，clamp [0,1]。

    参数:
        black, white: 输入域中的黑/白点位置（0..1 浮点域）。
    """
    work, was_u8 = _to_float01(img)
    if white <= black:
        raise ValueError(f"white ({white}) must be > black ({black})")
    out = (work - black) / (white - black)
    out = np.clip(out, 0.0, 1.0)
    if was_u8:
        return _to_uint8(out)
    return out


# ---------------------------------------------------------------------------
# 3. 百分位自动色阶
# ---------------------------------------------------------------------------
def auto_stretch(img: np.ndarray,
                 low_pct: float = 2.0,
                 high_pct: float = 98.0) -> np.ndarray:
    """稳健自动色阶：按 low_pct / high_pct 百分位做黑/白点拉伸。

    对 APT 云图常见的 2%~98% 截断，避免个别坏点拉低整体反差。
    """
    work, was_u8 = _to_float01(img)
    lo = float(np.percentile(work, low_pct))
    hi = float(np.percentile(work, high_pct))
    if hi - lo < 1e-9:
        # 平坦图：直接返回（不除零）
        return img.copy()
    out = (work - lo) / (hi - lo)
    out = np.clip(out, 0.0, 1.0)
    if was_u8:
        return _to_uint8(out)
    return out


# ---------------------------------------------------------------------------
# 4. 直方图均衡（CDF 查表）
# ---------------------------------------------------------------------------
def histogram_equalize(img: np.ndarray, nbins: int = 256) -> np.ndarray:
    """全局直方图均衡（CDF 查找表）。

    机制同 SatDump histogram::equalize_histogram()（histogram_utils.cpp:23-30）：
      hist = count per bin
      cdf  = cumulative sum
      lut[bin] = cdf[bin] / cdf[-1]
    然后逐像素查表。

    对 uint8 输入用 256 bin（零误差）；对 float 输入先归一化到 [0,1] 再分 bin。
    """
    work, was_u8 = _to_float01(img)

    # 统计直方图
    hist, edges = np.histogram(work, bins=nbins, range=(0.0, 1.0))
    cdf = hist.cumsum()
    total = cdf[-1] if cdf[-1] > 0 else 1
    cdf_norm = cdf / total                # [0,1]

    # 每个像素落在哪个 bin
    idx = np.clip((work * (nbins - 1)).astype(np.int64), 0, nbins - 1)
    out = cdf_norm[idx]
    out = np.clip(out, 0.0, 1.0)

    if was_u8:
        return _to_uint8(out)
    return out


# ---------------------------------------------------------------------------
# 5. 伪彩色 LUT
# ---------------------------------------------------------------------------
def _build_jet_lut() -> np.ndarray:
    """经典 jet 色带：蓝->青->黄->红（4 锚点线性插值到 256 项 RGB）。

    锚点顺序参照 SatDump LUT_jet（image_lut.cpp:17-22）的机制：
    低端蓝、中段青/黄、高端红。这里用公开的 jet 色标定义。
    """
    anchors = [
        (0.00, (0.0, 0.0, 0.5)),    # 深蓝黑
        (0.125, (0.0, 0.0, 1.0)),   # 蓝
        (0.375, (0.0, 1.0, 1.0)),   # 青
        (0.625, (1.0, 1.0, 0.0)),   # 黄
        (0.875, (1.0, 0.0, 0.0)),   # 红
        (1.00, (0.5, 0.0, 0.0)),    # 深红
    ]
    xs = np.array([a[0] for a in anchors])
    rgb = np.array([a[1] for a in anchors])
    i = np.linspace(0.0, 1.0, 256)
    r = np.interp(i, xs, rgb[:, 0])
    g = np.interp(i, xs, rgb[:, 1])
    b = np.interp(i, xs, rgb[:, 2])
    return (np.stack([r, g, b], axis=-1) * 255.0).astype(np.uint8)


def _build_iron_lut() -> np.ndarray:
    """铁红（iron/thermal）色带：黑->紫->红->橙->黄->白。"""
    anchors = [
        (0.00, (0.0, 0.0, 0.0)),
        (0.20, (0.2, 0.0, 0.5)),
        (0.40, (0.8, 0.1, 0.6)),
        (0.60, (1.0, 0.2, 0.2)),
        (0.75, (1.0, 0.7, 0.1)),
        (0.90, (1.0, 0.95, 0.5)),
        (1.00, (1.0, 1.0, 1.0)),
    ]
    xs = np.array([a[0] for a in anchors])
    rgb = np.array([a[1] for a in anchors])
    i = np.linspace(0.0, 1.0, 256)
    r = np.interp(i, xs, rgb[:, 0])
    g = np.interp(i, xs, rgb[:, 1])
    b = np.interp(i, xs, rgb[:, 2])
    return (np.stack([r, g, b], axis=-1) * 255.0).astype(np.uint8)


def _build_gray_lut() -> np.ndarray:
    """线性灰度 LUT。"""
    g = np.arange(256, dtype=np.float64) / 255.0
    return (np.stack([g, g, g], axis=-1) * 255.0).astype(np.uint8)


BUILTIN_LUTS: Dict[str, np.ndarray] = {
    "jet": _build_jet_lut(),
    "iron": _build_iron_lut(),
    "gray": _build_gray_lut(),
}


def apply_lut(img: np.ndarray, lut: str = "gray") -> np.ndarray:
    """把单通道灰度图映射到伪彩色 RGB。

    参数:
        img: 2D (H,W) 灰度图。
        lut: 'gray' | 'jet' | 'iron'。

    返回:
        uint8 RGB 图 (H, W, 3)。
    """
    if lut not in BUILTIN_LUTS:
        raise ValueError(f"未知 LUT {lut!r}，可选 {list(BUILTIN_LUTS)}")
    table = BUILTIN_LUTS[lut]

    work, was_u8 = _to_float01(img)
    idx = np.clip((work * 255.0).astype(np.int64), 0, 255)
    return table[idx]                     # (H, W, 3) uint8


# ---------------------------------------------------------------------------
# 6. 一站式 APT 增强管线
# ---------------------------------------------------------------------------
def enhance_apt(img: np.ndarray,
                auto_level: bool = True,
                low_pct: float = 2.0,
                high_pct: float = 98.0,
                brightness: float = 0.0,
                contrast: float = 0.0,
                equalize: bool = False,
                lut: str = "gray") -> np.ndarray:
    """APT 云图一站式增强。

    步骤顺序:
      1. auto_level   —— 百分位自动色阶（默认开）
      2. brightness_contrast —— 亮度/对比度微调（默认不调）
      3. equalize      —— 可选直方图均衡（默认关，避免过度增强噪声）
      4. apply_lut     —— 可选伪彩色（默认 gray 输出灰度）

    参数:
        img:        2D 灰度图（uint8 或 float [0,1]）。
        auto_level: 是否做百分位自动色阶。
        low_pct/high_pct: auto_level 截断百分位。
        brightness/contrast: brightness_contrast 参数。
        equalize:   是否做全局直方图均衡。
        lut:        'gray' 输出灰度；'jet'/'iron' 输出伪彩色 RGB。

    返回:
        lut='gray'  -> uint8 灰度图 (H, W)；
        lut!='gray' -> uint8 RGB 图 (H, W, 3)。
    """
    out = img
    if auto_level:
        out = auto_stretch(out, low_pct=low_pct, high_pct=high_pct)
    if brightness != 0.0 or contrast != 0.0:
        out = brightness_contrast(out, brightness=brightness, contrast=contrast)
    if equalize:
        out = histogram_equalize(out)
    if lut == "gray":
        # 保持灰度输出
        if out.dtype != np.uint8:
            out = _to_uint8(out)
        return out
    return apply_lut(out, lut=lut)


# ---------------------------------------------------------------------------
# 工具：保存增强后 PNG（供 onboard.py / 实验脚本调用）
# ---------------------------------------------------------------------------
def save_enhanced_png(img: np.ndarray, path: str) -> str:
    """把增强结果保存为 PNG。返回绝对路径。"""
    from PIL import Image
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    if img.ndim == 2:
        Image.fromarray(img, mode="L").save(path)
    elif img.ndim == 3 and img.shape[2] == 3:
        Image.fromarray(img, mode="RGB").save(path)
    else:
        raise ValueError(f"不支持的图像形状 {img.shape}")
    return os.path.abspath(path)
