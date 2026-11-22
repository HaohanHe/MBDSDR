# SPDX-License-Identifier: MIT
"""有先验的星场解算：quad 不变式 + 分层验证。

参考 ``astrometry.net/solver/quad-utils.c`` 与 ``verify.c``（astrometry.net
整体因 vendored GSL 按 GPLv3 分发）的**思路**：把四颗星的几何关系编码成
平移/旋转/缩放不变量，先按描述子找候选，再用「相似变换 + 内点统计」验证。
**本文件代码全部重写，未复制其源码**；验证层也刻意简化（不等价于其
Bayes log-odds），只回答「这组对应关系是否自洽、残差多大」。

适用边界
--------
这是**有先验的局部修正**，不是全天盲解：

* 输入检测星点像素 ``stars_px``（亮星在前）与星表地平坐标 ``catalog_altaz``；
* 用先验 ``ViewState`` 把星表投到像素，做 quad 匹配；
* 输出的是**增量**：``az_delta_deg`` / ``alt_delta_deg`` / ``roll_delta_deg`` /
  ``fov_scale``（真实视场 = 先验视场 × fov_scale），与 ``sky_solve`` 的
  增量契约一致；
* 先验误差应小于视场量级（IMU 的 5–15° 粗对准 + 60° 视场可用）；星表星
  完全投不进画面时返回 ``no_match``，不硬凑。

诚实边界
--------
只用**合成星场**验证了几何正确性（旋转/平移/缩放/无关星场反证）；
没有在真实相机图像上验证检测噪声、镜头畸变与星等阈值——真实精度仍未验证。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from .celestial_geometry import PerspectiveProjection
from .sky_interaction import ViewState, sky_to_screen

__all__ = [
    "QuadCode",
    "Similarity",
    "canonical_quad_code",
    "build_quad_index",
    "match_quads",
    "estimate_similarity",
    "estimate_pose_delta",
    "solve_similarity",
    "solve_field",
    "solve_frame_builtin",
    "DEFAULT_MAX_STARS",
    "DEFAULT_CODE_TOLERANCE",
    "DEFAULT_RESIDUAL_PX",
    "DEFAULT_MIN_INLIERS",
]

#: 参与 quad 的最多星数（C(20,4)=4845 个 quad，桌面端毫秒级）。
DEFAULT_MAX_STARS: int = 20

#: quad 描述子 L2 距离阈值。0.02 ≈ 归一化坐标里的 1/50 边长。
DEFAULT_CODE_TOLERANCE: float = 0.02

#: 验证残差（像素）。检测/投影误差在这个半径内算内点。
DEFAULT_RESIDUAL_PX: float = 2.0

#: 判定解算成功的最少内点数。
DEFAULT_MIN_INLIERS: int = 6

#: 每个检测星点 quad 最多保留几个候选（防止密集星场把验证层淹没）。
_MAX_CANDIDATES_PER_QUAD: int = 3

#: 送入验证层的候选上限。
_MAX_CANDIDATES: int = 300


@dataclass(frozen=True)
class QuadCode:
    """一个四星组合的规范描述子。

    ``order`` 是**原始点索引**按规范顺序 (A, B, C, D) 的排列；
    ``code`` 是 (Cx, Cy, Dx, Dy)：以 A 为原点、AB 为 x 轴且 |AB|=1 的
    归一化坐标。平移/旋转/缩放不变；镜像会把 Cy/Dy 变号（本解算器
    不接受镜像，见 ``allow_mirror``）。
    """

    order: Tuple[int, int, int, int]
    code: Tuple[float, float, float, float]


def _code_for_order(points: np.ndarray, order: Tuple[int, int, int, int]
                    ) -> Optional[QuadCode]:
    """按给定 A,B,C,D 顺序算归一化描述子，并满足 astrometry 的两条不变量：

    1. ``Cx <= Dx``；
    2. ``(Cx + Dx)/2 <= 0.5``——不满足就交换 A/B（等价于 ``w -> 1 - w``）。

    交换后用循环而不是递归：退化共线点可能让 mean 恰好 0.5，递归会
    在 0.5/0.5 之间来回；循环最多两次，第二次无论舍入如何都接受。
    """
    a, b, c, d = order
    for _ in range(2):
        za = complex(float(points[a][0]), float(points[a][1]))
        zb = complex(float(points[b][0]), float(points[b][1]))
        den = zb - za
        if abs(den) < 1e-12:
            return None
        wc = (complex(float(points[c][0]), float(points[c][1])) - za) / den
        wd = (complex(float(points[d][0]), float(points[d][1])) - za) / den
        if wc.real > wd.real:
            c, d = d, c
            wc, wd = wd, wc
        if (wc.real + wd.real) * 0.5 <= 0.5 + 1e-12:
            return QuadCode(order=(a, b, c, d),
                            code=(float(wc.real), float(wc.imag),
                                  float(wd.real), float(wd.imag)))
        # 交换 A/B 后 w' = 1 - w；C/D 的实部顺序随之反转。
        a, b = b, a
        c, d = d, c
    return None


def canonical_quad_code(points: np.ndarray,
                        quad: Sequence[int]) -> Optional[QuadCode]:
    """四点的规范描述子（与输入顺序无关）。

    规范顺序：先取四点中**距离最近的一对**作为 A、B（距离并列时按索引
    字典序），余下两点作 C、D；再交给 ``_code_for_order`` 满足不变量。
    选最近对是几何不变量，因此同一组点无论怎样排列都会得到同一个 code。
    """
    idx = tuple(int(i) for i in quad)
    if len(idx) != 4 or len(set(idx)) != 4:
        return None
    pts = np.asarray(points, dtype=float)
    if not np.all(np.isfinite(pts[list(idx), :2])):
        return None
    best = None
    for i in range(4):
        for j in range(i + 1, 4):
            di = float(np.hypot(*(pts[idx[i], :2] - pts[idx[j], :2])))
            key = (di, min(idx[i], idx[j]), max(idx[i], idx[j]))
            if best is None or key < best[0]:
                best = (key, i, j)
    assert best is not None
    _key, i, j = best
    rest = [k for k in range(4) if k not in (i, j)]
    order = (idx[i], idx[j], idx[rest[0]], idx[rest[1]])
    return _code_for_order(pts, order)


def _quads(n_points: int, max_stars: int) -> Iterable[Tuple[int, int, int, int]]:
    return itertools.combinations(range(min(n_points, max_stars)), 4)


def build_quad_index(points: np.ndarray, *, max_stars: int = DEFAULT_MAX_STARS,
                     cell: float = DEFAULT_CODE_TOLERANCE
                     ) -> Dict[Tuple[int, int, int, int], List[QuadCode]]:
    """按描述子分桶（格宽 = 匹配容差；查询端必须用同一格宽）。"""
    if cell <= 0.0:
        raise ValueError("cell 必须为正")
    pts = np.asarray(points, dtype=float)
    index: Dict[Tuple[int, int, int, int], List[QuadCode]] = {}
    if len(pts) < 4:
        return index
    for quad in _quads(len(pts), max_stars):
        encoded = canonical_quad_code(pts, quad)
        if encoded is None:
            continue
        key = tuple(int(math.floor(v / cell)) for v in encoded.code)
        index.setdefault(key, []).append(encoded)
    return index


def _code_distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def match_quads(image_index, ref_index, *,
                tolerance: float = DEFAULT_CODE_TOLERANCE
                ) -> List[Tuple[QuadCode, QuadCode, float]]:
    """在两个描述子索引之间找候选对应（格点邻域 + 精确距离）。"""
    if tolerance <= 0.0:
        raise ValueError("tolerance 必须为正")
    cell = tolerance
    neighbors = tuple(itertools.product((-1, 0, 1), repeat=4))
    best_per_image: Dict[Tuple[int, int, int, int], List[Tuple[float, QuadCode,
                                                               QuadCode]]] = {}
    for img_cell, img_codes in image_index.items():
        for img in img_codes:
            found: List[Tuple[float, QuadCode, QuadCode]] = []
            for offset in neighbors:
                key = tuple(int(c + o) for c, o in zip(img_cell, offset))
                for ref in ref_index.get(key, ()):
                    dist = _code_distance(img.code, ref.code)
                    if dist <= tolerance:
                        found.append((dist, img, ref))
            if not found:
                continue
            found.sort(key=lambda item: item[0])
            slot = best_per_image.setdefault(img.code, [])
            for dist, _same_img, ref in found:
                pair = (img, ref, dist)
                if pair not in slot:
                    slot.append(pair)
            best_per_image[img.code] = sorted(
                best_per_image[img.code], key=lambda item: item[2]
            )[:_MAX_CANDIDATES_PER_QUAD]

    candidates: List[Tuple[QuadCode, QuadCode, float]] = []
    for entries in best_per_image.values():
        candidates.extend(entries)
    candidates.sort(key=lambda item: item[2])
    return candidates[:_MAX_CANDIDATES]


@dataclass(frozen=True)
class Similarity:
    """二维相似变换：``dst = scale * R(theta) * src + t``。"""

    scale: float
    theta_deg: float
    tx: float
    ty: float

    def apply(self, points: np.ndarray) -> np.ndarray:
        p = np.asarray(points, dtype=float)
        th = math.radians(self.theta_deg)
        ca, sa = math.cos(th), math.sin(th)
        x = self.scale * (ca * p[:, 0] - sa * p[:, 1]) + self.tx
        y = self.scale * (sa * p[:, 0] + ca * p[:, 1]) + self.ty
        return np.column_stack((x, y))


def estimate_similarity(src: np.ndarray, dst: np.ndarray) -> Optional[Similarity]:
    """最小二乘估计 ``dst ≈ s R src + t``（不接受镜像）。"""
    a = np.asarray(src, dtype=float)
    b = np.asarray(dst, dtype=float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 2 or len(a) < 2:
        return None
    if not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        return None
    ca = a.mean(axis=0)
    cb = b.mean(axis=0)
    a0 = a - ca
    b0 = b - cb
    # 复数表示里 c = mean(b0 / a0) = s * e^{i theta}
    denom = float(np.sum(a0[:, 0] ** 2 + a0[:, 1] ** 2))
    if denom <= 1e-12:
        return None
    re = float(np.sum(a0[:, 0] * b0[:, 0] + a0[:, 1] * b0[:, 1]) / denom)
    im = float(np.sum(a0[:, 0] * b0[:, 1] - a0[:, 1] * b0[:, 0]) / denom)
    scale = math.hypot(re, im)
    if not math.isfinite(scale) or scale <= 1e-9:
        return None
    theta = math.degrees(math.atan2(im, re))
    tx = float(cb[0] - (re * ca[0] - im * ca[1]))
    ty = float(cb[1] - (im * ca[0] + re * ca[1]))
    return Similarity(scale=scale, theta_deg=theta, tx=tx, ty=ty)


def _unique_inliers(ref_px: np.ndarray, obs_px: np.ndarray,
                    sim: Similarity, residual_px: float
                    ) -> Tuple[List[Tuple[int, int]], float]:
    """把 ref 变换到 obs 后做一对一最近邻，返回 (配对, RMS 像素)。"""
    moved = sim.apply(ref_px)
    if len(moved) == 0 or len(obs_px) == 0:
        return [], float("inf")
    d = np.hypot(moved[:, None, 0] - obs_px[None, :, 0],
                 moved[:, None, 1] - obs_px[None, :, 1])
    flat = np.argsort(d, axis=None)
    used_r: set[int] = set()
    used_o: set[int] = set()
    pairs: List[Tuple[int, int]] = []
    sq = 0.0
    for k in flat:
        ri, oi = divmod(int(k), d.shape[1])
        if ri in used_r or oi in used_o:
            continue
        dist = float(d[ri, oi])
        if dist > residual_px:
            # 排序后更远的不可能再成为内点。
            break
        used_r.add(ri)
        used_o.add(oi)
        pairs.append((ri, oi))
        sq += dist * dist
    rms = math.sqrt(sq / len(pairs)) if pairs else float("inf")
    return pairs, rms


def solve_similarity(image_pts, ref_pts, *,
                     max_stars: int = DEFAULT_MAX_STARS,
                     tolerance: float = DEFAULT_CODE_TOLERANCE,
                     residual_px: float = DEFAULT_RESIDUAL_PX,
                     min_inliers: int = DEFAULT_MIN_INLIERS,
                     ) -> Dict[str, object]:
    """在两组二维点之间找相似变换（quad 候选 + 内点验证）。

    返回成功字典（``ok=True``）或 ``{"ok": False, "reason": ..., "error": ...}``。
    只接受**非镜像**相似变换（手机/相机成像不镜像）；镜像星场会返回
    ``no_consistent_transform``，不硬套一个反射矩阵。
    """
    img = np.asarray(image_pts, dtype=float)
    ref = np.asarray(ref_pts, dtype=float)
    if img.ndim != 2 or img.shape[1] != 2 or ref.ndim != 2 or ref.shape[1] != 2:
        return {"ok": False, "error": "bad_points", "reason": "点集必须是 N×2"}
    if len(img) < 4 or len(ref) < 4:
        return {"ok": False, "error": "too_few_points",
                "reason": f"至少需要 4 颗星（检测 {len(img)} / 星表 {len(ref)}）"}
    if not (np.all(np.isfinite(img)) and np.all(np.isfinite(ref))):
        return {"ok": False, "error": "bad_points", "reason": "点集含非有限值"}

    img = img[:max_stars]
    ref = ref[:max_stars]
    image_index = build_quad_index(img, max_stars=max_stars, cell=tolerance)
    ref_index = build_quad_index(ref, max_stars=max_stars, cell=tolerance)
    candidates = match_quads(image_index, ref_index, tolerance=tolerance)
    if not candidates:
        return {"ok": False, "error": "no_match",
                "reason": "没有 quad 描述子落在容差内"}

    best = None
    for img_q, ref_q, _dist in candidates:
        src = np.array([ref[i] for i in ref_q.order], dtype=float)
        dst = np.array([img[i] for i in img_q.order], dtype=float)
        sim = estimate_similarity(src, dst)
        if sim is None:
            continue
        pairs, rms = _unique_inliers(ref, img, sim, residual_px)
        if len(pairs) < min_inliers:
            continue
        score = (len(pairs), -rms)
        if best is None or score > best[0]:
            best = (score, sim, pairs, rms)

    if best is None:
        return {"ok": False, "error": "no_consistent_transform",
                "reason": f"候选 {len(candidates)} 个，但没有 ≥{min_inliers} 个内点的相似变换"}
    _score, sim, pairs, rms = best
    return {
        "ok": True,
        "scale": sim.scale,
        "rotation_deg": sim.theta_deg,
        "translation_px": (sim.tx, sim.ty),
        "inliers": len(pairs),
        "inlier_pairs": [(int(r), int(o)) for r, o in pairs],
        "residual_px": rms,
        "matched_quads": len(candidates),
        "mirrored": False,
        "reason": "",
    }


def estimate_pose_delta(ref_px, obs_px, alt_deg, *, view: ViewState,
                        ppr: float, widget_size
                        ) -> Optional[Dict[str, float]]:
    """由内点像素位移联合估计 (δaz, δalt, δroll, δfov相对量)。

    一阶模型（屏幕右=东、屏幕下=像素 +y）：

    * 指向抬升 ``δalt`` → 星点整体下移 ``+ppr·δalt``；
    * 指向东移 ``δaz`` → 星点整体左移 ``-ppr·cos(alt)·δaz``；
    * 画面旋转 ``δroll`` → 绕中心 ``(-dy, +dx)``；
    * 相对视场变化 ``ε`` → 绕中心 ``-(dx, dy)`` 径向缩放。

    联合最小二乘比「相似变换再分解」更稳：相似变换会把指向平移引起的
    投影畸变误吸收进旋转。随后按 3×MAD 剔除离群点再拟合一次。

    注意 ``δroll`` 是**画面表观旋转**：除了相机自身的横滚，视中心移动
    也会改变局部竖直方向（低仰角更明显）。把它直接加到先验 ``rotation``
    上即可复现观测画面——这正是指向修正要的量，不要拿它和
    ``ViewState.rotation`` 的参数差逐字比较。
    """
    ref = np.asarray(ref_px, dtype=float)
    obs = np.asarray(obs_px, dtype=float)
    alts = np.asarray(alt_deg, dtype=float)
    if (ref.ndim != 2 or obs.ndim != 2 or ref.shape != obs.shape
            or ref.shape[1] != 2 or len(ref) != len(alts) or len(ref) < 3):
        return None
    if not (np.all(np.isfinite(ref)) and np.all(np.isfinite(obs))
            and np.all(np.isfinite(alts))):
        return None
    cx, cy = widget_size[0] / 2.0, widget_size[1] / 2.0
    dx = ref[:, 0] - cx
    dy = ref[:, 1] - cy
    cos_alt = np.cos(np.radians(alts))
    n = len(ref)
    a = np.zeros((2 * n, 4), dtype=float)
    b = np.zeros(2 * n, dtype=float)
    a[0::2, 0] = -ppr * cos_alt          # δaz -> x
    a[0::2, 2] = -dy                     # δroll -> x
    a[0::2, 3] = -dx                     # ε -> x
    a[1::2, 1] = ppr                     # δalt -> y
    a[1::2, 2] = dx                      # δroll -> y
    a[1::2, 3] = -dy                     # ε -> y
    b[0::2] = obs[:, 0] - ref[:, 0]
    b[1::2] = obs[:, 1] - ref[:, 1]

    keep = np.ones(2 * n, dtype=bool)
    params = None
    for _ in range(3):
        try:
            params, *_ = np.linalg.lstsq(a[keep], b[keep], rcond=None)
        except np.linalg.LinAlgError:
            return None
        resid = b - a @ params
        med = float(np.median(resid[keep]))
        mad = float(np.median(np.abs(resid[keep] - med)))
        limit = max(1.0, 3.0 * 1.4826 * mad)
        new_keep = np.abs(resid - med) <= limit
        if new_keep.sum() < 6 or np.array_equal(new_keep, keep):
            break
        keep = new_keep
    rms = math.sqrt(float(np.mean((b[keep] - (a @ params)[keep]) ** 2)))
    return {
        "az_delta_deg": math.degrees(float(params[0])),
        "alt_delta_deg": math.degrees(float(params[1])),
        "roll_delta_deg": math.degrees(float(params[2])),
        "fov_scale": 1.0 + float(params[3]),
        "residual_px": rms,
        "used_pairs": int(keep.sum() // 2),
    }


def _project_catalog(catalog_altaz, view: ViewState, widget_size,
                     projection) -> Tuple[np.ndarray, np.ndarray, np.ndarray,
                                         np.ndarray]:
    """星表地平坐标 -> 先验像素；返回 (pixels, 有效掩码, alt, az)。"""
    w, h = float(widget_size[0]), float(widget_size[1])
    pts = []
    alts = []
    azs = []
    for az, alt in catalog_altaz:
        px, py = sky_to_screen(float(az), float(alt), view, projection,
                               widget_size)
        pts.append((px, py))
        alts.append(float(alt))
        azs.append(float(az))
    arr = np.asarray(pts, dtype=float)
    margin = 0.05 * min(w, h)
    ok = (np.isfinite(arr).all(axis=1)
          & (arr[:, 0] >= -margin) & (arr[:, 0] <= w + margin)
          & (arr[:, 1] >= -margin) & (arr[:, 1] <= h + margin))
    return (arr, ok, np.asarray(alts, dtype=float),
            np.asarray(azs, dtype=float))


def _project_altaz(azs, alts, view: ViewState, widget_size,
                   projection) -> np.ndarray:
    return np.array([sky_to_screen(float(a), float(e), view, projection,
                                   widget_size)
                     for a, e in zip(azs, alts)], dtype=float)


def solve_field(stars_px, catalog_altaz, view: ViewState, widget_size,
                *, projection=None,
                max_stars: int = DEFAULT_MAX_STARS,
                tolerance: float = DEFAULT_CODE_TOLERANCE,
                residual_px: float = DEFAULT_RESIDUAL_PX,
                min_inliers: int = DEFAULT_MIN_INLIERS,
                ) -> Dict[str, object]:
    """有先验的星场局部解算。

    Args:
        stars_px: 检测星点像素 ``[(x, y), ...]``，亮星在前。
        catalog_altaz: 星表候选 ``[(az_deg, alt_deg), ...]``，亮星在前。
        view: 先验 ``ViewState``（中心/视场/画面旋转）。
        widget_size: 画布 ``(w, h)`` 像素。
        projection: 先验投影；缺省透视（与手机/导星一致）。

    Returns:
        ``ok=True`` 时含增量：``az_delta_deg`` / ``alt_delta_deg`` /
        ``roll_delta_deg`` / ``fov_scale`` / ``residual_px`` /
        ``matched_stars``。失败返回结构化 ``error/reason``。

    小角近似说明：把像素相似变换分解成「画面旋转 + 平移 + 缩放」，
    用先验中心处的屏幕基（右=东、上=抬仰角）换算成方位/俯仰增量。
    先验误差在视场量级内时成立；大角度或强畸变下不保证。
    """
    if projection is None:
        projection = PerspectiveProjection()
    obs_raw = np.asarray(stars_px, dtype=float)
    if obs_raw.ndim != 2 or obs_raw.shape[1] != 2:
        return {"ok": False, "error": "bad_points", "reason": "点集必须是 N×2"}
    # 真实检测不会含 NaN；调用方误把不可见星（inf/NaN）混进来时如实剔除，
    # 不拿它们参与 quad。
    obs = obs_raw[np.isfinite(obs_raw).all(axis=1)]
    if len(obs) < 4:
        return {"ok": False, "error": "too_few_points",
                "reason": f"检测星点不足（有效 {len(obs)} 颗）"}

    ref_all, ok, alts_all, azs_all = _project_catalog(
        catalog_altaz, view, widget_size, projection)
    ref = ref_all[ok]
    alts = alts_all[ok]
    azs = azs_all[ok]
    if len(ref) < 4:
        return {"ok": False, "error": "too_few_catalog_stars",
                "reason": "先验视场内星表星不足 4 颗（先验可能偏得太远）"}

    fit = solve_similarity(obs, ref, max_stars=max_stars,
                           tolerance=tolerance, residual_px=residual_px,
                           min_inliers=min_inliers)
    if not fit.get("ok"):
        return fit

    w, h = float(widget_size[0]), float(widget_size[1])
    min_dim = float(min(w, h))
    if min_dim <= 0:
        return {"ok": False, "error": "bad_widget_size",
                "reason": "画布尺寸非法"}
    ppr = float(projection.screen_scale(min_dim, float(view.fov_deg)))
    if not math.isfinite(ppr) or ppr <= 0:
        return {"ok": False, "error": "bad_projection",
                "reason": "投影缩放非法"}

    pairs = np.asarray(fit["inlier_pairs"], dtype=int)
    if len(pairs) < 3:
        return {"ok": False, "error": "no_consistent_transform",
                "reason": "内点不足以联合估计位姿增量"}

    # 迭代线性化：一阶模型在 20° 视场里单次拟合仍会留下约 1 px 的
    # 透视残差；把已解出的增量加回先验、重投影后再拟合一次，通常可降到
    # 亚像素。最多两轮，避免在噪声上反复拧。
    cum_az = cum_alt = cum_roll = 0.0
    cum_fov = 1.0
    ref_cur = ref
    view_cur = view
    ppr_cur = ppr
    residual = float("inf")
    for _ in range(2):
        pose = estimate_pose_delta(ref_cur[pairs[:, 0]], obs[pairs[:, 1]],
                                   alts[pairs[:, 0]], view=view_cur,
                                   ppr=ppr_cur, widget_size=widget_size)
        if pose is None:
            break
        cum_az += float(pose["az_delta_deg"])
        cum_alt += float(pose["alt_delta_deg"])
        cum_roll += float(pose["roll_delta_deg"])
        cum_fov *= float(pose["fov_scale"])
        residual = float(pose["residual_px"])
        if residual < 0.2:
            break
        solved = ViewState(
            center_az=view.center_az + cum_az,
            center_alt=view.center_alt + cum_alt,
            fov_deg=view.fov_deg * cum_fov,
            rotation=view.rotation + cum_roll,
            min_fov=view.min_fov, max_fov=view.max_fov,
        )
        ref_cur = _project_altaz(azs, alts, solved, widget_size, projection)
        if not np.all(np.isfinite(ref_cur)):
            break
        view_cur = solved
        ppr_cur = float(projection.screen_scale(min_dim, solved.fov_deg))

    if not math.isfinite(residual):
        return {"ok": False, "error": "pose_fit_failed",
                "reason": "位姿增量最小二乘失败（点集退化）"}

    return {
        "ok": True,
        "az_delta_deg": cum_az,
        "alt_delta_deg": cum_alt,
        "roll_delta_deg": cum_roll,
        "fov_scale": cum_fov,
        "fov_delta_deg": float(view.fov_deg) * (cum_fov - 1.0),
        "matched_stars": int(fit["inliers"]),
        "residual_px": residual,
        "matched_quads": int(fit["matched_quads"]),
        "reason": "",
    }


def solve_frame_builtin(stars_px, catalog_altaz, image_size, fov_deg, *,
                        prior, projection=None, **kwargs
                        ) -> Dict[str, object]:
    """与 ``sky_solve.solve_frame`` 同一增量契约的**内置**本地解算入口。

    不依赖 tetra3 / ASTAP；``prior`` 键名沿用 ``sky_solve`` 的
    ``az_deg / alt_deg / roll_deg``，``fov_deg`` 是视场先验，``image_size``
    是画布 ``(w, h)``。其余参数原样透传给 :func:`solve_field`。
    """
    try:
        az = float(prior["az_deg"])
        alt = float(prior["alt_deg"])
        roll = float(prior.get("roll_deg", 0.0))
        w = float(image_size[0])
        h = float(image_size[1])
        fov = float(fov_deg)
    except (TypeError, KeyError, ValueError, IndexError):
        return {"ok": False, "error": "bad_input",
                "reason": "prior / image_size / fov_deg 缺字段或不是数字"}
    if not all(math.isfinite(v) for v in (az, alt, roll, w, h, fov)):
        return {"ok": False, "error": "bad_input",
                "reason": "prior / image_size / fov_deg 含非有限值"}
    if w <= 0.0 or h <= 0.0 or not (0.0 < fov <= 180.0):
        return {"ok": False, "error": "bad_input",
                "reason": "image_size 必须为正、fov_deg 必须在 (0, 180]"}
    if not (-90.0 <= alt <= 90.0):
        return {"ok": False, "error": "bad_input",
                "reason": f"prior.alt_deg 越界（{alt}°）"}
    view = ViewState(center_az=az % 360.0, center_alt=alt, fov_deg=fov,
                     rotation=roll, min_fov=0.5,
                     max_fov=max(120.0, fov))
    out = solve_field(stars_px, catalog_altaz, view, (w, h),
                      projection=projection, **kwargs)
    if not out.get("ok"):
        return out
    proj = projection if projection is not None else PerspectiveProjection()
    ppr = float(proj.screen_scale(min(w, h), fov))
    out["solver"] = "builtin"
    out["solved"] = {
        "az_deg": (az + float(out["az_delta_deg"])) % 360.0,
        "alt_deg": alt + float(out["alt_delta_deg"]),
        "roll_deg": (roll + float(out["roll_delta_deg"])) % 360.0,
    }
    out["residual_arcsec"] = (float(out["residual_px"]) * 206264.806
                              / ppr if ppr > 0 else float("inf"))
    return out
