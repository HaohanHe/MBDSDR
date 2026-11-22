# SPDX-License-Identifier: MIT
"""相机帧 → 星点质心：AR 指向链里「检测」这一段的唯一实现。

为什么单独成模块
----------------
``sky_pointing_pipeline`` 只吃「已经检测好的星点坐标」，此前全仓没有任何
质心提取实现，链在「相机帧 → 星点」处是断的。本模块补上这一段，并且只做
这一件事：从一帧二维灰度数组里找出亚像素星点质心，对每一种失败给出可机读
的原因。

口径与诚实边界
--------------
* 输入是**内存里的图像数组**（``numpy`` 数组或可转成数组的嵌套序列），
  不是相机句柄——**没有真机相机采集路径**；
* 阈值 = 背景 + ``threshold_sigma`` × 稳健噪声σ，σ 用
  ``1.4826 * median(|x - median(x)|)``，对拖线/月亮这类大块不敏感；
* 四连通域 + 流量加权质心，坐标顺序为 ``(x_px, y_px)``（x=列，y=行）；
* 只用合成图验证过，**没有真实夜空数据**，也没有在真实星表上验证精度；
  合成图质心误差小不代表真实光学/大气条件下同样准。

本模块不写「已对准」之类的结论——它只返回质心与失败原因，是否对准由上层
结合解算残差与系统误差判定。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

import numpy as np
from scipy import ndimage

__all__ = ["DetectConfig", "DetectResult", "detect_stars"]

#: 四连通结构：上下左右相邻才算同一个连通域（八连通会把斜角噪声串成星）。
_FOUR_CONNECTED = np.array([[0, 1, 0],
                            [1, 1, 1],
                            [0, 1, 0]], dtype=bool)

#: 稳健噪声σ系数（中位绝对偏差 → 正态σ）。
_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class DetectConfig:
    """检测参数。

    Attributes:
        threshold_sigma: 阈值 = 背景 + k × 噪声σ；调高只会减少检出。
        min_area_px: 连通域面积下限（小于它的单像素热点丢弃）。
        max_area_px: 连通域面积上限（超过它的多半是云/月亮/拖线）。
        max_stars: 按总流量降序最多返回多少颗。
        border_px: 质心到画面边缘的距离小于该值就丢弃（半个 PSF 在画外）。
    """

    threshold_sigma: float = 5.0
    min_area_px: int = 2
    max_area_px: int = 400
    max_stars: int = 100
    border_px: int = 1


@dataclass(frozen=True)
class DetectResult:
    """一帧图像的星点检测结果。

    Attributes:
        ok: 检测到至少一颗合格星点才为 True。
        centroids: ``(x_px, y_px)`` 元组，按流量（像素值 − 背景）降序。
        background: 图像背景中位数；图像非法时为 None。
        noise_sigma: 稳健噪声σ；图像非法时为 None。
        rejected_low_area: 因面积过小被丢弃的连通域数（含面积 1 的热点）。
        rejected_high_area: 因面积过大被丢弃的连通域数（云/月亮/拖线）。
        rejected_border: 因贴边被丢弃的星点数。
        error: 机读原因码：``bad_image`` / ``flat_image`` / ``no_stars`` /
            成功为空串。
        reason: 人类可读原因；失败时说明具体原因，不含任何对准结论。
    """

    ok: bool
    centroids: Tuple[Tuple[float, float], ...] = ()
    background: Optional[float] = None
    noise_sigma: Optional[float] = None
    rejected_low_area: int = 0
    rejected_high_area: int = 0
    rejected_border: int = 0
    error: str = ""
    reason: str = ""


def _fail(code: str, reason: str, *, background: Optional[float] = None,
          noise_sigma: Optional[float] = None,
          rejected_low_area: int = 0, rejected_high_area: int = 0,
          rejected_border: int = 0) -> DetectResult:
    """构造失败结果；错误码只经形参传递，不在调用点写成字面量。"""
    return DetectResult(ok=False, background=background, noise_sigma=noise_sigma,
                        rejected_low_area=rejected_low_area,
                        rejected_high_area=rejected_high_area,
                        rejected_border=rejected_border,
                        error=code, reason=reason)


def _as_frame(image: Any) -> Tuple[Optional[np.ndarray], Optional[DetectResult]]:
    """把入参规整成二维有限浮点帧；不合法时返回结构化失败。"""
    try:
        data = np.asarray(image, dtype=float)
    except (TypeError, ValueError) as exc:
        return None, _fail("bad_image",
                           f"图像无法转成浮点数组：{type(exc).__name__}: {exc}")
    if data.ndim != 2:
        return None, _fail("bad_image",
                           f"图像必须是二维灰度数组，收到 {data.ndim} 维"
                           f"（shape={data.shape}）")
    if data.size == 0 or min(data.shape) < 3:
        return None, _fail("bad_image",
                           f"图像尺寸过小：{data.shape}，任一边至少 3 px")
    if not bool(np.all(np.isfinite(data))):
        return None, _fail("bad_image", "图像含 NaN/Inf，不能用于星点检测")
    return data, None


def _detect(image: Any, config: Optional[DetectConfig]) -> DetectResult:
    cfg = config if config is not None else DetectConfig()

    data, bad = _as_frame(image)
    if bad is not None:
        return bad
    assert data is not None

    background = float(np.median(data))
    residual = data - background
    noise_sigma = float(_MAD_TO_SIGMA * np.median(np.abs(residual)))
    if not np.isfinite(noise_sigma) or noise_sigma <= 0.0:
        return _fail("flat_image",
                     f"图像几乎是纯平场（背景={background:.6g}，"
                     f"噪声σ={noise_sigma:.6g}），没有可区分的星点",
                     background=background, noise_sigma=noise_sigma)

    threshold = float(cfg.threshold_sigma) * noise_sigma
    mask = residual > threshold
    weights = np.maximum(residual, 0.0)

    labels, _ = ndimage.label(mask, structure=_FOUR_CONNECTED)
    height, width = data.shape
    low_area = high_area = border = 0
    stars: List[Tuple[float, float, float]] = []

    for index, box in enumerate(ndimage.find_objects(labels), start=1):
        if box is None:
            continue
        region = labels[box] == index
        area = int(np.count_nonzero(region))
        rows, cols = np.nonzero(region)
        rows = rows + box[0].start
        cols = cols + box[1].start
        star_weights = weights[rows, cols]
        total = float(star_weights.sum())
        if total > 0.0:
            cy = float((rows * star_weights).sum() / total)
            cx = float((cols * star_weights).sum() / total)
        else:
            cy = float(rows.mean())
            cx = float(cols.mean())

        if area < int(cfg.min_area_px):
            low_area += 1
            continue
        if area > int(cfg.max_area_px):
            high_area += 1
            continue
        edge_distance = min(cy, cx, (height - 1) - cy, (width - 1) - cx)
        if edge_distance < float(cfg.border_px):
            border += 1
            continue
        stars.append((total, cx, cy))

    stars.sort(key=lambda star: star[0], reverse=True)
    keep = int(cfg.max_stars)
    kept = stars[:keep] if keep > 0 else []
    centroids = tuple((cx, cy) for _, cx, cy in kept)

    stats = dict(background=background, noise_sigma=noise_sigma,
                 rejected_low_area=low_area, rejected_high_area=high_area,
                 rejected_border=border)

    if not centroids:
        return _fail(
            "no_stars",
            (f"未检测到符合面积/边界约束的星点（"
             f"低面积剔除 {low_area}，高面积剔除 {high_area}，"
             f"贴边剔除 {border}；背景={background:.6g}，"
             f"噪声σ={noise_sigma:.6g}）"),
            **stats,
        )

    return DetectResult(ok=True, centroids=centroids, **stats)


def detect_stars(image: Any,
                 config: Optional[DetectConfig] = None) -> DetectResult:
    """从一帧二维灰度图像中提取亚像素星点质心。

    合成图验证过的纯计算实现；不接相机、不读写文件、不联网。

    Args:
        image: 二维灰度帧（``numpy`` 数组或可转成浮点数组的嵌套序列）。
        config: ``DetectConfig``；None 时用默认参数。

    Returns:
        ``DetectResult``。``centroids`` 为 ``(x_px, y_px)``，按总流量降序。
        任何非法输入都不抛异常：非二维/过小/含 NaN 或 Inf 一律
        ``error="bad_image"``；纯平场 ``"flat_image"``；过滤后为空
        ``"no_stars"``。``reason`` 只陈述事实，不含对准结论。
    """
    try:
        return _detect(image, config)
    except Exception as exc:  # noqa: BLE001 - 契约要求任何路径都不抛
        return _fail("bad_image",
                     f"星点检测内部异常：{type(exc).__name__}: {exc}")
