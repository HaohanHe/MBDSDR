# SPDX-License-Identifier: MIT
"""
proper_motion.py — 恒星 J2000 自行（proper motion）前处理
==========================================================

口径
----
只处理**恒星自行**：把 J2000/ICRS 的赤经/赤纬按线性自行的切向位移
传播到给定儒略日。**不含**周年视差、光行差、引力偏折/相对论修正，
也不做径向速度引起的透视加速。

历元与单位
----------
- 历元固定 J2000.0（``EPOCH_J2000_JD = 2451545.0``）；
- 时间按儒略年 ``365.25`` 天换算，忽略闰秒（自行尺度下可忽略）；
- ``pmra_mas``/``pmdec_mas`` 单位 mas/yr。HYG v4.1 的 ``pmra`` 已是
  μα*（含 cosδ），因此东向角速度直接取 ``pmra``，不再乘/除 cosδ。

数据来源
--------
``data/hyg_pm_v41.npz`` 由 ``scripts/build_hyg_pm.py`` 从 HYG v4.1 源
CSV 生成，只保留与主星表同一批 ``hyg_id``；源文件 SHA-256 必须与主星表
``meta.source_file_sha256`` 一致。缺值/非法值写 NaN，运行时按静态处理。

向量约定
--------
与 ``celestial_geometry`` / ``star_catalog`` 一致（左手系）::

    v  = (cosδ·sinα,  sinδ, -cosδ·cosα)
    e  = (cosα, 0, sinα)                      # 赤经增加（东）方向
    n  = (-sinδ·sinα, cosδ, sinδ·cosα)        # 赤纬增加（北）方向

    v' = normalize(v + e·(pmra·t) + n·(pmdec·t))

这样在极点附近也不出现除以 cosδ 的奇点。**不要把自行直接烘焙进主星表**
——星表保持 J2000，传播只发生在查询时刻。

失败语义
--------
侧车缺失或损坏时 ``load_pm_table`` 返回 ``None``，星表整体照常可用，
所有星按静态处理；本模块不联网、不 import Qt。
"""

from __future__ import annotations

import json
import math
import os
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np

__all__ = [
    "EPOCH_J2000_JD",
    "JULIAN_YEAR_DAYS",
    "MAS_TO_DEG",
    "PmTable",
    "load_pm_table",
    "reset_pm_cache",
    "pm_for_hyg_ids",
    "propagate_radec_many",
    "propagate_vectors_many",
]

#: J2000.0 儒略日（TT/UT1 之差在自行尺度下可忽略）。
EPOCH_J2000_JD: float = 2451545.0
#: 儒略年长度（天）。
JULIAN_YEAR_DAYS: float = 365.25
#: 毫角秒 -> 度。
MAS_TO_DEG: float = 1.0 / 3.6e6

_PM_FORMAT = "hyg-pm-v41"
_DEFAULT_PM_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "hyg_pm_v41.npz")

_MAS_TO_RAD = MAS_TO_DEG * (math.pi / 180.0)


@dataclass(frozen=True)
class PmTable:
    """按 ``hyg_id`` 升序排列的自行表（查表用二分，不保证文件原顺序）。"""

    hyg_id: np.ndarray
    pmra_mas: np.ndarray
    pmdec_mas: np.ndarray
    meta: Dict[str, Any]


#: 进程内缓存：键为绝对路径，值为 PmTable 或 None（失败也缓存，避免逐帧重试）。
_PM_CACHE: Dict[str, Optional[PmTable]] = {}


def reset_pm_cache() -> None:
    """清空进程内缓存（测试/重新生成侧车后使用）。"""
    _PM_CACHE.clear()


def _load_pm_file(path: str) -> Optional[PmTable]:
    try:
        with np.load(path, allow_pickle=False) as data:
            if "meta" not in data.files:
                return None
            meta = json.loads(str(data["meta"].item()))
            if str(meta.get("format")) != _PM_FORMAT:
                return None
            ids = np.asarray(data["hyg_id"], dtype=np.int32)
            pmra = np.asarray(data["pmra_mas"], dtype=np.float32)
            pmdec = np.asarray(data["pmdec_mas"], dtype=np.float32)
    except (OSError, ValueError, KeyError, TypeError, EOFError,
            zipfile.BadZipFile):
        # 缺文件/坏 zip/meta 非 JSON 一律视为“没有自行表”，星表仍可用。
        return None
    if not (len(ids) == len(pmra) == len(pmdec)):
        return None
    order = np.argsort(ids, kind="stable")
    return PmTable(
        hyg_id=ids[order],
        pmra_mas=pmra[order],
        pmdec_mas=pmdec[order],
        meta=meta,
    )


def load_pm_table(path: Optional[str] = None) -> Optional[PmTable]:
    """加载自行侧车（懒加载 + 进程内缓存）；缺失/损坏返回 ``None``。

    校验 ``meta.format == "hyg-pm-v41"`` 且三个数组长度一致；加载后按
    ``hyg_id`` 排序，供 ``np.searchsorted`` 二分查找。显式传入 ``path``
    时按绝对路径分别缓存。
    """
    key = os.path.abspath(path or _DEFAULT_PM_PATH)
    if key in _PM_CACHE:
        return _PM_CACHE[key]
    table = _load_pm_file(key)
    _PM_CACHE[key] = table
    return table


def pm_for_hyg_ids(
    hyg_ids: np.ndarray,
    table: Optional[PmTable] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """按 ``hyg_id`` 查自行；返回 ``(pmra_mas, pmdec_mas)``（未知/无表 → NaN）。

    使用 ``np.searchsorted`` 在升序 ``hyg_id`` 上二分，复杂度 O(k log n)。
    查不到或自行表不可用时整组返回 NaN，调用方按静态处理。
    """
    ids = np.asarray(hyg_ids, dtype=np.int64)
    if table is None:
        table = load_pm_table()
    if table is None or len(table.hyg_id) == 0:
        nan = np.full(ids.shape, np.nan, dtype=np.float64)
        return nan, nan.copy()
    pos = np.searchsorted(table.hyg_id, ids, side="left")
    safe = np.clip(pos, 0, len(table.hyg_id) - 1)
    hit = (pos < len(table.hyg_id)) & (table.hyg_id[safe] == ids)
    pmra = np.where(hit, table.pmra_mas[safe], np.nan).astype(np.float64)
    pmdec = np.where(hit, table.pmdec_mas[safe], np.nan).astype(np.float64)
    return pmra, pmdec


def _propagate_components(
    ra_deg: Any,
    dec_deg: Any,
    pmra_mas: Any,
    pmdec_mas: Any,
    jd: Any,
):
    """广播后返回 (vx, vy, vz, valid, ra_b, dec_b)。"""
    arrays = np.broadcast_arrays(
        np.asarray(ra_deg, dtype=float),
        np.asarray(dec_deg, dtype=float),
        np.asarray(pmra_mas, dtype=float),
        np.asarray(pmdec_mas, dtype=float),
        np.asarray(jd, dtype=float),
    )
    ra_b, dec_b, pmra_b, pmdec_b, jd_b = arrays
    alpha = np.radians(ra_b)
    delta = np.radians(dec_b)
    valid = (np.isfinite(pmra_b) & np.isfinite(pmdec_b) & np.isfinite(jd_b)
             & np.isfinite(alpha) & np.isfinite(delta))

    t_yr = (jd_b - EPOCH_J2000_JD) / JULIAN_YEAR_DAYS
    # valid=False 时位移置 0：NaN/Inf 不会扩散到向量上。
    with np.errstate(invalid="ignore", over="ignore"):
        de = np.where(valid, pmra_b * t_yr, 0.0) * _MAS_TO_RAD
        dn = np.where(valid, pmdec_b * t_yr, 0.0) * _MAS_TO_RAD

    sa, ca = np.sin(alpha), np.cos(alpha)
    sd, cd = np.sin(delta), np.cos(delta)

    # 切向基：e=东（d/dα），n=北（d/dδ），与本仓 J2000 左手系约定一致。
    ex, ey, ez = ca, np.zeros_like(ca), sa
    nx, ny, nz = -sd * sa, cd, sd * ca

    vx = cd * sa + ex * de + nx * dn
    vy = sd + ey * de + ny * dn
    vz = -cd * ca + ez * de + nz * dn

    norm = np.sqrt(vx * vx + vy * vy + vz * vz)
    norm = np.where(norm > 0.0, norm, 1.0)
    return vx / norm, vy / norm, vz / norm, valid, ra_b, dec_b


def propagate_radec_many(
    ra_deg: Any,
    dec_deg: Any,
    pmra_mas: Any,
    pmdec_mas: Any,
    jd: Any,
) -> Tuple[np.ndarray, np.ndarray]:
    """把 J2000 赤经/赤纬按自行传播到 ``jd``；返回 ``(ra_deg', dec_deg')``。

    输入可为标量或 numpy 数组（自动广播）。任一 ``pm`` 为 NaN、或 ``jd``
    非有限时**原样返回**；有效结果的赤经归一到 [0, 360)，赤纬在 [-90, 90]。
    标量输入返回 Python float。
    """
    vx, vy, vz, valid, ra_b, dec_b = _propagate_components(
        ra_deg, dec_deg, pmra_mas, pmdec_mas, jd)
    ra_out = np.degrees(np.arctan2(vx, -vz)) % 360.0
    dec_out = np.degrees(np.arcsin(np.clip(vy, -1.0, 1.0)))
    ra_out = np.where(valid, ra_out, ra_b)
    dec_out = np.where(valid, dec_out, dec_b)
    if np.ndim(ra_out) == 0:
        return float(ra_out), float(dec_out)
    return ra_out, dec_out


def propagate_vectors_many(
    v: np.ndarray,
    ra_deg: Any,
    dec_deg: Any,
    pmra_mas: Any,
    pmdec_mas: Any,
    jd: Any,
) -> np.ndarray:
    """返回传播后的 J2000 单位向量（形状与 ``v`` 相同）。

    供 ``star_catalog.select_for_view`` 在矩阵乘之前直接替换方向向量。
    ``v`` 必须已是单位向量；NaN/无表时不改动该行。
    """
    v_arr = np.asarray(v, dtype=float)
    vx, vy, vz, valid, _, _ = _propagate_components(
        ra_deg, dec_deg, pmra_mas, pmdec_mas, jd)
    propagated = np.stack((vx, vy, vz), axis=-1)
    return np.where(valid[..., None], propagated, v_arr)
