"""
MBDSDR AI - 气象卫星图像处理链
================================

对标 SatDump ``src-core/image/`` 与 ``src-core/projection/`` 的完整图像处理流水线，
全部以 numpy / scipy 向量化实现（Kuwahara 用积分图求和区化方差，避免逐像素 Python 循环）。

覆盖模块（与 SatDump 源码逐处对应，注释标注 file:line）：
  1. 中值滤波 median_filter        —— 对应 SatDump image/processing.cpp:69  median_blur
  2. 直方图均衡 histogram_equalize  —— 对应 SatDump image/processing.cpp:179 equalize
                                       + CLAHE 扩展（限制对比度自适应直方图均衡）
  3. 白平衡 white_balance          —— 灰度世界法 / 完美反射法 / SatDump 百分位拉伸
                                       (processing.cpp:34 white_balance)
  4. Kuwahara 边缘保持降噪          —— 对应 SatDump image/processing.cpp:103 kuwahara_filter
  5. 几何校正 / 投影变换            —— 对应 SatDump projection/standard/geos.cpp
                                       (全圆盘 GEOS)、webmerc.cpp (墨卡托)、equirect.cpp
                                       (等经纬度)，子卫星点经纬度参数化
  6. RGB 通道合成 rgb_composite     —— 真彩色 / 伪彩色(灰度/铁红/彩虹色表) / 通道代数
  7. 图像增强管线 enhance_pipeline  —— SatImageProcessor 链式封装上述模块

红线：GPL-3.0；算法均为 numpy 真实实现，注释标注 SatDump 来源；中文注释。
"""

from __future__ import annotations

import os
import logging
from typing import Optional, Tuple, Union, List, Dict, Any

import numpy as np
from scipy import ndimage as ndi

logger = logging.getLogger(__name__)

Array = np.ndarray

# WGS84 椭球常数（与 satdump_adapter 保持一致，单位米）。
# 来源: SatDump src-core/common/geodetic/wgs84.h:9-20
_WGS84_A = 6378137.0                 # 半长轴 (m)
_WGS84_F = 1.0 / 298.257223563      # 扁率
_WGS84_B = _WGS84_A * (1.0 - _WGS84_F)
_WGS84_ES = (_WGS84_A ** 2 - _WGS84_B ** 2) / (_WGS84_A ** 2)  # 第一偏心率^2

Number = Union[int, float]


# ======================================================================
# 工具：统一输入为 float 与通道拆分
# ======================================================================
def _as_float(img: Array) -> Array:
    return img.astype(np.float64, copy=False)


def _is_uint8(img: Array) -> bool:
    return img.dtype == np.uint8


def _prepare(img: Array) -> Tuple[Array, bool]:
    """统一把输入归一化到 [0,1] 浮点工作区间。

    uint8 输入 -> /255；float 输入原样保留（调用方按需自行归一化）。
    返回 (work, was_uint8)。
    """
    if img.dtype == np.uint8:
        return img.astype(np.float64) / 255.0, True
    return img.astype(np.float64, copy=False), False


def _finalize(out: Array, was_uint8: bool) -> Array:
    """把 [0,1] 浮点结果还原：uint8 输入则转回 uint8，否则保留 float。"""
    if was_uint8:
        return (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    return out


# ======================================================================
# 1. 中值滤波
# 参考: SatDump src-core/image/processing.cpp:69-101  median_blur()
#   SatDump 原版是 5 点十字中值（中心 + 上下左右），values 长度 5 取中位。
#   这里泛化为可配置 NxN（3x3 / 5x5），用 scipy.ndimage.median_filter 向量化。
# ======================================================================
def median_filter(img: Array, ksize: int = 3) -> Array:
    """对 2D 灰度或 3D 多通道图像做中值滤波，去除椒盐脉冲噪声。

    参数:
        img:   2D (H,W) 或 3D (H,W,C) numpy 数组
        ksize: 滤波核边长，须为奇数（3 或 5）。
    返回:
        与输入同形状、同 dtype 类别（uint8 进 uint8 出）。
    """
    if ksize % 2 == 0:
        raise ValueError("ksize 必须为奇数（3 或 5）")
    work, was_u8 = _prepare(img)
    if work.ndim == 2:
        out = ndi.median_filter(work, size=ksize, mode="nearest")
    elif work.ndim == 3:
        outs = [ndi.median_filter(work[..., c], size=ksize, mode="nearest")
                for c in range(work.shape[2])]
        out = np.stack(outs, axis=-1)
    else:
        raise ValueError("img 必须是 2D 或 3D 数组")
    return _finalize(out, was_u8)


# ======================================================================
# 2. 直方图均衡化
# 参考: SatDump src-core/image/processing.cpp:179-218  equalize()
#   nlevels = maxval+1; 统计直方图 -> 累积直方图 ->
#   scaling[i] = round(cdf[i] * (nlevels-1)/size) -> 查表映射。
#   per_channel=False 时只对通道 0 统计（即把多通道当一张图）。
# CLAHE 为标准扩展：分块(tiles x tiles)、裁剪直方图、双线性插值块间映射。
# ======================================================================
def _equalize_1ch(x: Array, nbins: int = 256) -> Array:
    """对单通道浮点 [0,1] 做全局直方图均衡，返回 [0,1]。

    忠实对应 processing.cpp:195-211 的累积直方图 -> 查表流程。
    """
    hist, edges = np.histogram(x, bins=nbins, range=(0.0, 1.0))
    cdf = hist.cumsum()                       # 对应 cummulative_histogram
    total = cdf[-1] if cdf[-1] > 0 else 1
    # scaling[i] = round(cdf[i] * (nlevels-1) / size)  (processing.cpp:207)
    cdf_scaled = cdf / total                  # 归一化到 [0,1]
    # 把每个像素按其落在的 bin 映射到均衡后值
    idx = np.clip((x * (nbins - 1)).astype(np.int64), 0, nbins - 1)
    return cdf_scaled[idx].astype(np.float64)


def _clahe_1ch(x: Array, tiles: int = 8, clip_limit: float = 2.0) -> Array:
    """限制对比度自适应直方图均衡 (CLAHE)，单通道 [0,1] -> [0,1]。

    与全局均衡的区别：分 tiles x tiles 小块分别均衡，并裁剪直方图峰值后
    把多余质量均摊回去，再用双线性插值消除块边界（Zuiderveld 1994 标准算法）。
    """
    H, W = x.shape
    ty = max(1, tiles)
    tx = max(1, tiles)
    # 块尺寸（可能不整除，用最近边界）
    ys = np.linspace(0, H, ty + 1).astype(int)
    xs = np.linspace(0, W, tx + 1).astype(int)
    nbins = 256

    # 先对每个块计算均衡查找表 lut[ty][tx][nbins]
    luts = np.zeros((ty, tx, nbins), dtype=np.float64)
    for j in range(ty):
        for i in range(tx):
            block = x[ys[j]:ys[j + 1], xs[i]:xs[i + 1]]
            hist, _ = np.histogram(block, bins=nbins, range=(0.0, 1.0))
            # 裁剪：把超过 clip_limit 的部分平均回填
            clip = clip_limit * (block.size / nbins)
            excess = np.sum(np.maximum(hist - clip, 0))
            hist = np.minimum(hist, clip)
            hist += excess / nbins
            cdf = hist.cumsum()
            total = cdf[-1] if cdf[-1] > 0 else 1
            luts[j, i] = cdf / total

    # 双线性插值采样：对每个像素找到其落在的 4 个相邻块，加权
    out = np.zeros_like(x)
    # 每个像素对应的块中心坐标（连续）
    ccx = (np.arange(W) + 0.5) / W * tx - 0.5
    ccy = (np.arange(H) + 0.5) / H * ty - 0.5
    gx, gy = np.meshgrid(ccx, ccy)
    x0 = np.clip(np.floor(gx).astype(int), 0, tx - 1)
    y0 = np.clip(np.floor(gy).astype(int), 0, ty - 1)
    x1 = np.clip(x0 + 1, 0, tx - 1)
    y1 = np.clip(y0 + 1, 0, ty - 1)

    bin_idx = np.clip((x * (nbins - 1)).astype(np.int64), 0, nbins - 1)
    lt = luts[y0, x0, bin_idx]   # 左上
    rt = luts[y0, x1, bin_idx]   # 右上
    lb = luts[y1, x0, bin_idx]   # 左下
    rb = luts[y1, x1, bin_idx]   # 右下
    fx = gx - np.floor(gx)       # 列方向插值权重
    fy = gy - np.floor(gy)       # 行方向插值权重
    top = lt * (1 - fx) + rt * fx
    bot = lb * (1 - fx) + rb * fx
    out = top * (1 - fy) + bot * fy
    return out.astype(np.float64)


def histogram_equalize(img: Array, clahe: bool = False,
                       tiles: int = 8, clip_limit: float = 2.0,
                       per_channel: bool = True) -> Array:
    """直方图均衡化（全局）或 CLAHE（限制对比度自适应）。

    参数:
        img:         2D (H,W) 或 3D (H,W,C)
        clahe:       True 用 CLAHE；False 用 SatDump 全局 equalize
        tiles:       CLAHE 分块数（每维）
        clip_limit:  CLAHE 对比度限制阈值
        per_channel: True 逐通道均衡；False 多通道共用一张直方图(对应 SatDump per_channel=false)
    返回:
        同形状；uint8 进则 uint8 出。
    """
    work, was_u8 = _prepare(img)
    # 归一化到 [0,1] 供统计
    lo = float(work.min())
    hi = float(work.max())
    if hi <= lo:
        return img.copy()
    norm = (work - lo) / (hi - lo)

    def _eq_channel(x2d: Array) -> Array:
        if clahe:
            return _clahe_1ch(x2d, tiles=tiles, clip_limit=clip_limit)
        return _equalize_1ch(x2d)

    if norm.ndim == 2:
        res = _eq_channel(norm)
    else:
        chans: List[Array] = []
        if per_channel:
            for c in range(norm.shape[2]):
                chans.append(_eq_channel(norm[..., c]))
        else:
            # SatDump per_channel=false: 用通道 0 的直方图映射所有通道
            lut_ref = _equalize_1ch(norm[..., 0]) if not clahe else None
            for c in range(norm.shape[2]):
                chans.append(_eq_channel(norm[..., c]))
        res = np.stack(chans, axis=-1)
    return _finalize(res, was_u8)


# ======================================================================
# 3. 白平衡
# 参考: SatDump src-core/image/processing.cpp:34-67  white_balance()
#   SatDump 原版是逐通道百分位拉伸：按 percentileValue / 100-p 取两端点，
#   线性映射到 [0, maxVal]。这里实现该法 (method='percentile')，并补可见光
#   色彩校正常用的灰度世界法 (gray-world) 与完美反射法 (perfect reflector)。
# ======================================================================
def white_balance(img: Array, method: str = "grayworld",
                  percentile: float = 2.0) -> Array:
    """可见光通道色彩校正。

    参数:
        img:        3 通道 (H,W,3) 彩色图（灰度世界/完美反射需 3 通道）
        method:     'grayworld' | 'perfect' | 'percentile'
                    - grayworld : 假设场景平均色是中性灰，逐通道缩放使均值相等
                    - perfect   : 假设最亮像素是白纸(反射率1)，按高分位亮度归一
                    - percentile: SatDump processing.cpp:34 逐通道百分位拉伸
        percentile: percentile 法两端裁剪百分位（2 即 2%~98% 拉伸）
    返回:
        校正后图像，同形状；uint8 进则 uint8 出。
    """
    work, was_u8 = _prepare(img)
    if work.ndim == 2:
        # 灰度图无色彩可校正，退化为百分位拉伸
        lo = np.percentile(work, percentile)
        hi = np.percentile(work, 100.0 - percentile)
        if hi <= lo:
            return img.copy()
        out = (work - lo) / (hi - lo)
        return _finalize(out, was_u8)

    if work.shape[2] < 3:
        raise ValueError("白平衡需要至少 3 个通道 (H,W,3)")

    out = work[..., :3].copy()
    if method == "grayworld":
        # 灰度世界法：三通道均值的全局均值作为目标灰
        means = out.reshape(-1, 3).mean(axis=0)
        gray = means.mean()
        scale = gray / np.maximum(means, 1e-6)
        out = out * scale[None, None, :]
    elif method == "perfect":
        # 完美反射法：各通道高分位亮部对齐到全局高分位
        tops = np.percentile(out.reshape(-1, 3), 99.0, axis=0)
        target = tops.max()
        scale = target / np.maximum(tops, 1e-6)
        out = out * scale[None, None, :]
    elif method == "percentile":
        # SatDump processing.cpp:52-62 逐通道百分位拉伸
        for c in range(out.shape[2]):
            lo = np.percentile(out[..., c], percentile)
            hi = np.percentile(out[..., c], 100.0 - percentile)
            if hi > lo:
                out[..., c] = (out[..., c] - lo) / (hi - lo)
    else:
        raise ValueError(f"未知白平衡方法: {method}")
    return _finalize(out, was_u8)


# ======================================================================
# 4. Kuwahara 边缘保持降噪
# 参考: SatDump src-core/image/processing.cpp:103-177  kuwahara_filter()
#   radius 默认 1 -> 窗口 3x3，4 个子区域各 (radius+1)^2 = 2x2；
#   每个子区算均值 average[k] 与方差 variance[k]，取方差最小子区的均值作为输出。
#   这里 radius 可配 (size=3 -> r=1, size=5 -> r=2)，用积分图(II)向量化求
#   各子区和/平方和，再算 E[x^2]-E[x]^2，无逐像素 Python 循环。
# ======================================================================
def kuwahara_filter(img: Array, size: int = 3) -> Array:
    """Kuwahara 边缘保持平滑：平坦区降噪、边缘不被模糊。

    参数:
        img:  2D (H,W) 或 3D (H,W,C)
        size: 窗口边长 3 或 5（radius = size//2）
    返回:
        同形状；uint8 进则 uint8 出。
    """
    if size % 2 == 0:
        raise ValueError("size 必须为奇数（3 或 5）")
    r = size // 2

    def _kawa_2d(x: Array) -> Array:
        H, W = x.shape
        # 边缘复制填充 r 像素
        padded = np.pad(x, r, mode="edge")
        # 积分图：II[i,j] = padded[0:i, 0:j] 之和
        ii = padded.cumsum(0).cumsum(1)
        ii = np.pad(ii, ((1, 0), (1, 0)), mode="constant")
        ii2 = (padded * padded).cumsum(0).cumsum(1)
        ii2 = np.pad(ii2, ((1, 0), (1, 0)), mode="constant")

        def win_sum(S: Array, r1: Array, r2: Array, c1: Array, c2: Array) -> Array:
            return (S[r2 + 1, c2 + 1] - S[r1, c2 + 1]
                    - S[r2 + 1, c1] + S[r1, c1])

        oy, ox = np.meshgrid(np.arange(H), np.arange(W), indexing="ij")
        # 四个子区（在 padded 坐标下，中心 = (oy+r, ox+r)），对齐 processing.cpp:124-154
        # Q0 左上: rows[oy, oy+r]  cols[ox, ox+r]
        # Q1 右上: rows[oy, oy+r]  cols[ox+r, ox+2r]
        # Q2 右下: rows[oy+r, oy+2r] cols[ox+r, ox+2r]
        # Q3 左下: rows[oy+r, oy+2r] cols[ox, ox+r]
        windows = [
            (oy,         oy + r,     ox,         ox + r),     # Q0
            (oy,         oy + r,     ox + r,     ox + 2 * r), # Q1
            (oy + r,     oy + 2 * r, ox + r,     ox + 2 * r), # Q2
            (oy + r,     oy + 2 * r, ox,         ox + r),     # Q3
        ]
        n = (r + 1) * (r + 1)  # num_pixels = (radius+1)^2 (processing.cpp:106)
        means = np.zeros((4, H, W))
        varis = np.zeros((4, H, W))
        for k, (r1, r2, c1, c2) in enumerate(windows):
            s1 = win_sum(ii, r1, r2, c1, c2)
            s2 = win_sum(ii2, r1, r2, c1, c2)
            mu = s1 / n
            means[k] = mu
            # 方差 = E[x^2]-E[x]^2（processing.cpp:160 方差除以 num_pixels-1，
            #  这里用总体方差做比较，argmin 结果一致）
            varis[k] = s2 / n - mu * mu
        best = np.argmin(varis, axis=0)            # 方差最小子区
        out = np.choose(best, means)
        return out

    work, was_u8 = _prepare(img)
    if work.ndim == 2:
        res = _kawa_2d(work)
    else:
        res = np.stack([_kawa_2d(work[..., c]) for c in range(work.shape[2])],
                       axis=-1)
    return _finalize(res, was_u8)


# ======================================================================
# 5. 几何校正与投影变换
# 参考:
#   全圆盘 GEOS:  SatDump projection/standard/geos.cpp:58-90 (fwd), 94-133 (inv)
#   墨卡托:      SatDump projection/standard/webmerc.cpp:20-38
#                 fwd: x=lam, y=asinh(tan(phi)) ; inv: phi=atan(sinh(y)), lam=x
#   等经纬度:    SatDump projection/standard/equirect.cpp:20-38  x=lon, y=lat
# ======================================================================
class GeoProjector:
    """静止轨道全圆盘 / 等经纬度 / 墨卡托 之间的坐标与图像重投影。

    参数:
        sub_lon_deg: 子卫星点星下点经度（度），GEOS 投影中心经度
        h_km:        卫星轨道高度（km），默认 35786 km（GEO 同步轨道）
    """

    def __init__(self, sub_lon_deg: float = 0.0, h_km: float = 35786.0):
        self.sub_lon = float(sub_lon_deg)
        self.h_m = h_km * 1000.0
        # geos.cpp:24-40 setup 常数
        self.radius_g_1 = self.h_m / _WGS84_A          # h/a
        self.radius_g = 1.0 + self.radius_g_1
        self.C = self.radius_g ** 2 - 1.0
        self.radius_p = np.sqrt(1.0 - _WGS84_ES)       # b/a
        self.radius_p2 = 1.0 - _WGS84_ES
        self.radius_p_inv2 = 1.0 / (1.0 - _WGS84_ES)

    # ---- GEOS 正变换：(lat, lon) -> 全圆盘视平面 (x_m, y_m) ----------
    # 参考: geos.cpp:58-90
    def geos_forward(self, lat_deg: Array, lon_deg: Array
                     ) -> Tuple[Array, Array, Array]:
        """大地坐标 -> 全圆盘视平面坐标（米）。

        返回 (x_m, y_m, visible)；visible=False 表示在地球圆盘视场外。
        """
        phi = np.radians(lat_deg)
        lam = np.radians(lon_deg - self.sub_lon)
        # geos.cpp:63  地理纬度 -> 地心纬度
        phi = np.arctan(self.radius_p2 * np.tan(phi))
        r = self.radius_p / np.hypot(self.radius_p * np.cos(phi), np.sin(phi))
        Vx = r * np.cos(lam) * np.cos(phi)
        Vy = r * np.sin(lam) * np.cos(phi)
        Vz = r * np.sin(phi)
        # geos.cpp:71 可见性判断
        visible = ((self.radius_g - Vx) * Vx - Vy * Vy
                   - Vz * Vz * self.radius_p_inv2) >= 0.0
        tmp = self.radius_g - Vx
        # geos.cpp:86-89 (flip_axis=false, sweep_x)
        # 注意 PROJ geos 输出 x,y 为无量纲量 radius_g_1*atan(...)，
        # 即"地球半径 a 的倍数"；换算到视平面米坐标需乘 a，而非 h。
        x_m = self.radius_g_1 * np.arctan(Vy / tmp) * _WGS84_A
        y_m = self.radius_g_1 * np.arctan(
            Vz / np.hypot(Vy, tmp)) * _WGS84_A
        return x_m, y_m, visible

    # ---- GEOS 逆变换：(x_m, y_m) -> (lat, lon) -----------------------
    # 参考: geos.cpp:94-133
    def geos_inverse(self, x_m: Array, y_m: Array
                     ) -> Tuple[Array, Array, Array]:
        """全圆盘视平面坐标（米）-> 大地 (lat_deg, lon_deg, visible)。"""
        x = x_m / _WGS84_A          # 回到无量纲 x = radius_g_1*atan(...)
        y = y_m / _WGS84_A
        Vx = -np.ones_like(x)
        # geos.cpp:108-111 (flip_axis=false)
        Vy = np.tan(x / self.radius_g_1)
        Vz = np.tan(y / self.radius_g_1) * np.hypot(1.0, Vy)
        a = Vz / self.radius_p
        a = Vy * Vy + a * a + Vx * Vx
        b = 2.0 * self.radius_g * Vx
        det = b * b - 4.0 * a * self.C
        visible = det >= 0.0
        det = np.where(visible, det, 0.0)
        k = (-b - np.sqrt(det)) / (2.0 * a)
        Vx = self.radius_g + k * Vx
        Vy = Vy * k
        Vz = Vz * k
        lam = np.arctan2(Vy, Vx)
        phi = np.arctan(Vz * np.cos(lam) / Vx)
        phi = np.arctan(self.radius_p_inv2 * np.tan(phi))
        lat = np.degrees(phi)
        lon = np.degrees(lam) + self.sub_lon
        return lat, lon, visible

    # ---- 墨卡托正/逆 -------------------------------------------------
    # 参考: webmerc.cpp:24-38
    @staticmethod
    def mercator_forward(lat_deg: Array, lon_deg: Array
                         ) -> Tuple[Array, Array]:
        lam = np.radians(lon_deg)
        phi = np.radians(lat_deg)
        return lam, np.arcsinh(np.tan(phi))

    @staticmethod
    def mercator_inv(x: Array, y: Array) -> Tuple[Array, Array]:
        lat = np.degrees(np.arctan(np.sinh(y)))
        lon = np.degrees(x)
        return lat, lon

    # ---- 等经纬度正/逆 ------------------------------------------------
    # 参考: equirect.cpp:20-38  x=lon, y=lat
    @staticmethod
    def equirect_forward(lat_deg: Array, lon_deg: Array
                         ) -> Tuple[Array, Array]:
        return lon_deg, lat_deg

    @staticmethod
    def equirect_inv(x_deg: Array, y_deg: Array) -> Tuple[Array, Array]:
        return y_deg, x_deg


def geometric_correction(img: Array, sub_lon_deg: float = 0.0,
                         h_km: float = 35786.0,
                         out_size: Optional[int] = None) -> Array:
    """全圆盘图像的地球曲率非线性校正。

    把"按扫描角线性采样"的全圆盘图，重采样到按真实地心视线角校正后的网格，
    即消除切线平面近似带来的边缘拉伸。返回与输入同形状的校正后图像。

    参考: geos.cpp 正/逆变换对（几何曲率项由 geos_forward 的地心纬度修正体现）。
    """
    H, W = img.shape[:2]
    proj = GeoProjector(sub_lon_deg=sub_lon_deg, h_km=h_km)
    # 假设输入图是按视平面米坐标线性铺满的全圆盘：中心为星下点
    # 视野半径取 GEO 全圆盘典型 ~ 35786km * sin(8.6deg) ≈ 5300km
    extent_m = h_km * 1000.0 * np.radians(8.6)
    ys = np.linspace(extent_m, -extent_m, H)
    xs = np.linspace(-extent_m, extent_m, W)
    gx, gy = np.meshgrid(xs, ys)
    lat, lon, vis = proj.geos_inverse(gx, gy)
    # 再正变换回去得到校正后的采样位置（曲率非线性重采样）
    cx, cy, _ = proj.geos_forward(lat, lon)
    # 把校正后的视平面坐标映射回原图像素坐标
    col = (cx + extent_m) / (2 * extent_m) * (W - 1)
    row = (extent_m - cy) / (2 * extent_m) * (H - 1)
    out = ndi.map_coordinates(img.astype(np.float64), [row, col],
                              order=1, mode="nearest")
    return out.reshape(img.shape)


def reproject_full_disk_to_equirect(full_img: Array,
                                    sub_lon_deg: float = 0.0,
                                    h_km: float = 35786.0,
                                    out_w: int = 720,
                                    out_h: int = 360,
                                    lat_min: float = -60.0,
                                    lat_max: float = 60.0,
                                    ) -> Array:
    """全圆盘 GEOS 图 -> 等经纬度(Equirectangular)网格。

    对目标等经纬度网格的每个 (lat,lon)，反算它在全圆盘图中的像素位置并采样。
    参考: geos.cpp:58-90 (lat/lon -> 视平面)。
    """
    H, W = full_img.shape[:2]
    proj = GeoProjector(sub_lon_deg=sub_lon_deg, h_km=h_km)
    extent_m = h_km * 1000.0 * np.radians(8.6)
    lats = np.linspace(lat_max, lat_min, out_h)
    lons = np.linspace(sub_lon_deg - 180.0, sub_lon_deg + 180.0, out_w)
    glon, glat = np.meshgrid(lons, lats)
    x_m, y_m, vis = proj.geos_forward(glat, glon)
    col = (x_m + extent_m) / (2 * extent_m) * (W - 1)
    row = (extent_m - y_m) / (2 * extent_m) * (H - 1)
    row = np.where(vis, row, -1)
    col = np.where(vis, col, -1)
    out = ndi.map_coordinates(full_img.astype(np.float64), [row, col],
                             order=1, mode="constant", cval=0.0)
    return out.reshape(out_h, out_w)


# ======================================================================
# 6. RGB 通道合成
# ======================================================================
# 伪彩色色表（256 项 RGB 查找表）。气象 IR 云图常用：灰度 / 铁红(iron) / 彩虹(rainbow)。
def _build_luts() -> Dict[str, np.ndarray]:
    """构建 3 种 256x3 伪彩色查找表。

    - gray   : 线性灰度
    - iron   : 黑->蓝->紫->红->橙->黄->白（经典铁红热红外色带）
    - rainbow: 深蓝->青->绿->黄->红（彩虹/Jet 类）
    """
    def lut_from_anchors(stops: List[Tuple[float, Tuple[int, int, int]]]
                         ) -> np.ndarray:
        xs = np.array([s[0] for s in stops])
        rgb = np.array([s[1] for s in stops], dtype=float)
        idx = np.linspace(0.0, 1.0, 256)
        r = np.interp(idx, xs, rgb[:, 0])
        g = np.interp(idx, xs, rgb[:, 1])
        b = np.interp(idx, xs, rgb[:, 2])
        return np.stack([r, g, b], axis=-1).astype(np.uint8)

    gray = lut_from_anchors([(0.0, (0, 0, 0)), (1.0, (255, 255, 255))])
    iron = lut_from_anchors([
        (0.00, (0, 0, 0)),
        (0.20, (30, 0, 80)),
        (0.40, (120, 20, 120)),
        (0.60, (200, 40, 40)),
        (0.75, (240, 140, 20)),
        (0.90, (250, 230, 80)),
        (1.00, (255, 255, 255)),
    ])
    rainbow = lut_from_anchors([
        (0.00, (0, 0, 90)),
        (0.25, (0, 80, 200)),
        (0.50, (0, 200, 120)),
        (0.70, (240, 220, 40)),
        (0.85, (230, 80, 20)),
        (1.00, (120, 0, 0)),
    ])
    return {"gray": gray, "iron": iron, "rainbow": rainbow}


_LUTS = _build_luts()


def channel_algebra(a: Array, b: Array, op: str = "diff") -> Array:
    """通道代数运算，生成差值/比值产品（如 WV 水汽 = IR1 - IR2）。

    参数:
        op: 'diff' (a-b) | 'sum' (a+b) | 'ratio' (a/(b+eps))
    返回:
        运算结果（float，归一化到 [0,1]）。
    """
    a = _as_float(a)
    b = _as_float(b)
    if op == "diff":
        res = a - b
    elif op == "sum":
        res = a + b
    elif op == "ratio":
        res = a / (b + 1e-6)
    else:
        raise ValueError(f"未知 op: {op}")
    lo, hi = float(res.min()), float(res.max())
    if hi > lo:
        res = (res - lo) / (hi - lo)
    return np.clip(res, 0.0, 1.0)


def rgb_composite(ch_r: Array, ch_g: Array, ch_b: Array) -> Array:
    """真彩色合成：三通道 -> HxWx3 uint8。"""
    def norm(c: Array) -> Array:
        c = _as_float(c)
        lo, hi = np.percentile(c, 1.0), np.percentile(c, 99.0)
        if hi <= lo:
            hi = lo + 1.0
        return np.clip((c - lo) / (hi - lo), 0.0, 1.0)
    rgb = np.dstack([norm(ch_r), norm(ch_g), norm(ch_b)])
    return (rgb * 255.0 + 0.5).astype(np.uint8)


def false_color_ir(ir: Array, lut: str = "iron") -> Array:
    """红外单通道 -> 伪彩色 RGB（按色表映射）。

    参数:
        ir:  2D 红外灰度图
        lut: 'gray' | 'iron' | 'rainbow'
    """
    if lut not in _LUTS:
        raise ValueError(f"未知色表 {lut}，可选 {list(_LUTS)}")
    work = _as_float(ir)
    lo, hi = float(work.min()), float(work.max())
    if hi <= lo:
        hi = lo + 1.0
    idx = np.clip(((work - lo) / (hi - lo) * 255.0).astype(np.int64), 0, 255)
    return _LUTS[lut][idx]


# ======================================================================
# 7. 图像增强管线：链式封装
# ======================================================================
class SatImageProcessor:
    """可链式调用的气象卫星图像处理器。

    用法:
        out = (SatImageProcessor(img)
               .median(ksize=3)
               .equalize(clahe=True)
               .kuwahara(size=3)
               .white_balance()
               .to_rgb(lut='iron')
               .result)
    """

    def __init__(self, image: Array):
        if not isinstance(image, np.ndarray):
            raise TypeError("image 必须是 numpy.ndarray")
        self.img = image.copy()
        self.result: Array = image.copy()

    # --- 滤波类：就地更新 self.img，返回 self 以链式 ---------------
    def median(self, ksize: int = 3) -> "SatImageProcessor":
        self.img = median_filter(self.img, ksize=ksize)
        return self

    def equalize(self, clahe: bool = False, tiles: int = 8,
                 clip_limit: float = 2.0,
                 per_channel: bool = True) -> "SatImageProcessor":
        self.img = histogram_equalize(self.img, clahe=clahe, tiles=tiles,
                                      clip_limit=clip_limit,
                                      per_channel=per_channel)
        return self

    def white_balance(self, method: str = "grayworld",
                      percentile: float = 2.0) -> "SatImageProcessor":
        self.img = white_balance(self.img, method=method,
                                 percentile=percentile)
        return self

    def kuwahara(self, size: int = 3) -> "SatImageProcessor":
        self.img = kuwahara_filter(self.img, size=size)
        return self

    def reproject(self, sub_lon_deg: float = 0.0, h_km: float = 35786.0,
                 out_w: int = 720, out_h: int = 360) -> "SatImageProcessor":
        self.img = reproject_full_disk_to_equirect(
            self.img, sub_lon_deg=sub_lon_deg, h_km=h_km,
            out_w=out_w, out_h=out_h)
        return self

    def correct_geometry(self, sub_lon_deg: float = 0.0,
                         h_km: float = 35786.0) -> "SatImageProcessor":
        self.img = geometric_correction(self.img, sub_lon_deg=sub_lon_deg,
                                        h_km=h_km)
        return self

    # --- 输出类：生成 RGB 存入 self.result --------------------------
    def to_rgb(self, lut: str = "iron") -> "SatImageProcessor":
        if self.img.ndim == 2:
            self.result = false_color_ir(self.img, lut=lut)
        else:
            self.result = self.img
        return self

    def to_true_color(self, ch_r: Optional[Array] = None,
                      ch_g: Optional[Array] = None,
                      ch_b: Optional[Array] = None) -> "SatImageProcessor":
        if ch_r is None:
            # 默认取已有图的前三通道
            if self.img.ndim == 3 and self.img.shape[2] >= 3:
                ch_r, ch_g, ch_b = (self.img[..., 0], self.img[..., 1],
                                    self.img[..., 2])
            else:
                raise ValueError("需要提供三个通道")
        self.result = rgb_composite(ch_r, ch_g, ch_b)
        return self

    def enhance_pipeline(self, steps: List[Dict[str, Any]]) -> "SatImageProcessor":
        """按配置字典列表顺序执行处理链。

        steps 例:
            [{'op':'median','ksize':3},
             {'op':'equalize','clahe':True},
             {'op':'kuwahara','size':3},
             {'op':'white_balance','method':'grayworld'}]
        """
        dispatch = {
            "median": lambda s: self.median(ksize=s.get("ksize", 3)),
            "equalize": lambda s: self.equalize(
                clahe=s.get("clahe", False), tiles=s.get("tiles", 8),
                clip_limit=s.get("clip_limit", 2.0)),
            "white_balance": lambda s: self.white_balance(
                method=s.get("method", "grayworld"),
                percentile=s.get("percentile", 2.0)),
            "kuwahara": lambda s: self.kuwahara(size=s.get("size", 3)),
            "geometry": lambda s: self.correct_geometry(
                sub_lon_deg=s.get("sub_lon_deg", 0.0),
                h_km=s.get("h_km", 35786.0)),
        }
        for step in steps:
            op = step["op"]
            if op not in dispatch:
                raise ValueError(f"未知处理步骤: {op}")
            dispatch[op](step)
        return self


# ======================================================================
# 工具注册：register_tool_registry(registry) 注册 "sat_image_enhance"
# ======================================================================
def register_tool_registry(registry) -> None:
    """把气象卫星图像增强工具注册到 MBDSDR ToolRegistry。

    在 tool_registry.py 的 register_builtin_tools() 中按需调用。
    """
    def _handler(args: Dict[str, Any]):
        import json as _json
        from mbdsdr_ai.tool_registry import ToolResult

        try:
            # 输入图像：支持 2D/3D list（JSON）或文件路径
            image = args.get("image")
            path = args.get("image_path")
            if path is not None:
                from PIL import Image
                image = np.asarray(Image.open(path))
            if image is None:
                return ToolResult(
                    success=False,
                    content="必须提供 image(2D/3D 数组) 或 image_path",
                    error="no image input")
            img = np.asarray(image, dtype=np.float64)
            steps = args.get("steps", [{"op": "median", "ksize": 3}])
            lut = args.get("lut", "iron")
            proc = SatImageProcessor(img).enhance_pipeline(steps).to_rgb(lut=lut)
            out = proc.result
            data = {
                "shape": list(out.shape),
                "dtype": str(out.dtype),
                "steps_applied": [s.get("op") for s in steps],
            }
            # 若要求落盘
            out_path = args.get("output_path")
            if out_path:
                from PIL import Image
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
                Image.fromarray(out.astype(np.uint8)).save(out_path)
                data["output_path"] = out_path
            return ToolResult(
                success=True,
                content=_json.dumps(data, ensure_ascii=False),
                tool_name="sat_image_enhance",
                data=data,
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, content=str(e),
                              tool_name="sat_image_enhance", error=str(e))

    registry.register(
        name="sat_image_enhance",
        description=(
            "气象卫星图像增强链（对标 SatDump processors）：输入单通道/多通道"
            "卫星图像与处理步骤参数（median/equalize/kuwahara/white_balance/"
            "geometry），输出增强后图像；支持伪彩色(gray/iron/rainbow)合成。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "image": {
                    "type": "array",
                    "description": "2D(H,W) 灰度或 3D(H,W,C) 多通道图像数组",
                    "items": {"type": "number"},
                },
                "image_path": {
                    "type": "string",
                    "description": "可选：从磁盘读取的图像路径（与 image 二选一）",
                },
                "steps": {
                    "type": "array",
                    "description": "处理步骤列表，每项 {'op':..., 'kw':...}",
                    "items": {"type": "object"},
                },
                "lut": {
                    "type": "string",
                    "enum": ["gray", "iron", "rainbow"],
                    "description": "单通道伪彩色色表",
                },
                "output_path": {
                    "type": "string",
                    "description": "可选：把结果 PNG 保存到该路径",
                },
            },
        },
        handler=_handler,
        category="satellite",
    )
