# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/skyengine/star_catalog.py — 全天星表 + 视场驱动的星等分层 (LOD)
======================================================================

模型
----
天球固定，星体本来就在那里；动的是摄像机/视场。本模块回答一个问题:

    「给定视场 (fov) + Bortle + 观测时刻/站点，本次应该画哪些星？」

看得见多少星由**视场和极限星等**决定，不再由手挑的亮星表决定。

数据来源
--------
``data/full_sky_hyg_v41.npz`` 由 ``build_full_sky_catalog.py`` 从
HYG Database v4.1 生成（源: https://github.com/astronexus/HYG-Database，
许可 CC BY-SA 4.0）。星表 83k 颗 (mag <= 9.0)，字段含 J2000 赤经/赤纬/
V 星等/B-V/距离/光谱型/proper 名/星座。生成物是 HYG 的衍生数据，
按 CC BY-SA 4.0 分发；生成脚本与元数据随文件走，无网络依赖。

**覆盖声明**：本表只覆盖 V <= 9.0，更深星需 Gaia DR3 / Tycho-2。
``select_for_view`` 收到 > 9.0 的请求时会把 ``limit_mag`` 夹到 9.0 并置
``StarSelection.beyond_catalog_limit=True``，界面层据此诚实提示。
**加载守卫**：meta 缺来源/许可/SHA 或数组 SHA 与 meta 不符都会抛错；
详见 ``load_catalog`` 与 docs/star-catalog.md §1.4。

与既有绘制口径的关系
--------------------
- 裸眼极限星等基线直接复用 ``stars.star_draw`` 的公式:
  ``6.5 - 0.35 * max(0, Bortle - 3)``，不另立第二套阈值。
- 大气消光直接复用 ``stars.extinction_delta_mag`` 的 k/sin(alt) 模型
  （常量也取自 stars，矢量化只为批量计算，测试锁定与标量逐点相等）。
- 坐标链复用 ``celestial_geometry`` 的 J2000 -> 岁差/章动 -> 赤道
  -> 地平矩阵；本模块只做批量矩阵乘，不另造坐标系。
- 视场只增加一个**有界深度增益**（见 ``detail_gain_mag``）：宽视场
  等于或低于裸眼基线，小视场才逐层露出更暗的星。这不是第二个阈值，
  而是同一基线上的 LOD 调制；显式 ``limit_mag`` 可覆盖它。

分层
----
星表按 mag 升序存盘，并额外存 0.5 等一档的分档边界/起始偏移
(``mag_bin_edges`` / ``mag_bin_start``)。查询极限 m 时等价于取
前 ``count_below(m)`` 颗；分档信息用于审计每档边界与 LOD 统计。
另存 ``dec_order``（按 Dec 升序的索引），视场锥先用赤纬带裁剪，
再做精确角距过滤。

接口（稳定签名，界面层直接调用）
--------------------------------
- ``limit_magnitude(fov_deg, bortle=3) -> float``
- ``extinction_delta_mag_array(alt_deg) -> np.ndarray``
- ``get_catalog() -> StarCatalog``
- ``select_for_view(...) -> StarSelection``
  （模块级函数，内部用单例星表；也可 ``catalog.select_for_view(...)``）

``StarSelection`` 用 numpy 数组承载（一帧几千颗，避免逐星 dataclass
的开销）；数组按**有效星等从暗到亮**排序，调用方顺序绘制即可让亮星
压在暗星之上。预算截断时保留最亮者，并如实给出 ``available`` /
``truncated``。

Stellarium 仅作机制参考，本模块未包含其源代码。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from ..celestial_geometry import (
    GroundStation,
    nutation_matrix,
    precession_matrix,
    vec_from_azalt,
)
from .stars import (
    DEFAULT_BORTLE,
    EXTINCTION_K,
    _EXTINCTION_MAX_DM,
    _EXTINCTION_MIN_ALT,
    naked_eye_limit_mag,
)

__all__ = [
    "CATALOG_DEPTH_MAG",
    "MAG_BIN_WIDTH",
    "DEFAULT_MAX_COUNT",
    "MAX_DETAIL_GAIN_MAG",
    "DETAIL_REF_FOV_DEG",
    "CatalogMetadataError",
    "CatalogIntegrityError",
    "CatalogStar",
    "StarCatalog",
    "StarSelection",
    "get_catalog",
    "load_catalog",
    "compute_data_sha256",
    "limit_magnitude",
    "naked_eye_limit_mag",
    "detail_gain_mag",
    "effective_limit_mag",
    "extinction_delta_mag_array",
    "select_for_view",
]

# ---------------------------------------------------------------------------
# 口径常量
# ---------------------------------------------------------------------------
#: 随仓星表的星等深度（生成脚本的 cutoff）；LOD 极限不会超过它。
CATALOG_DEPTH_MAG: float = 9.0
#: 星等分档宽度（每 0.5 等一档）。
MAG_BIN_WIDTH: float = 0.5
#: 单次返回的星数上限（超出如实降级为最亮的 N 颗）。
DEFAULT_MAX_COUNT: int = 20000
#: 视场小于该值才开始“放大露出更多暗星”。
DETAIL_REF_FOV_DEG: float = 30.0
#: 深度增益上限（等）；保证极限星等不超过星表深度。
MAX_DETAIL_GAIN_MAG: float = 1.5

_DEFAULT_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "full_sky_hyg_v41.npz")

_CATALOG_FORMAT = "full-sky-hyg-v41"
#: 加载时必须存在的来源/许可/指纹字段；缺任何一个都拒绝加载。
_REQUIRED_META_KEYS = (
    "format", "version", "source", "source_url", "source_license",
    "source_license_url", "attribution", "source_file_sha256",
    "data_sha256", "star_count", "mag_cutoff", "mag_bin_width",
)


class CatalogMetadataError(ValueError):
    """星表 meta 缺字段/格式不合法（来源、许可、SHA 不完整）。"""


class CatalogIntegrityError(ValueError):
    """星表数组与 meta.data_sha256 不一致（疑似手改数据未改 meta）。"""


def _sha256_hex_ok(value: Any) -> bool:
    text = str(value)
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def _validate_meta(meta: Any) -> None:
    """校验来源可追溯性；缺 source URL/许可/SHA 时必须报错，不能静默加载。"""
    if not isinstance(meta, dict):
        raise CatalogMetadataError("星表 meta 不是 JSON 对象")
    missing = [k for k in _REQUIRED_META_KEYS if not meta.get(k)]
    if missing:
        raise CatalogMetadataError(f"星表 meta 缺少必需字段: {missing}")
    if str(meta["format"]) != _CATALOG_FORMAT:
        raise CatalogMetadataError(
            f"星表格式不匹配: {meta['format']!r} != {_CATALOG_FORMAT!r}")
    for key in ("source_url", "source_license_url"):
        if not str(meta[key]).startswith(("https://", "http://")):
            raise CatalogMetadataError(f"星表 meta.{key} 不是有效 URL")
    for key in ("source_file_sha256", "data_sha256"):
        if not _sha256_hex_ok(meta[key]):
            raise CatalogMetadataError(f"星表 meta.{key} 不是 64 位十六进制 SHA-256")
    try:
        cutoff = float(meta["mag_cutoff"])
    except (TypeError, ValueError) as exc:
        raise CatalogMetadataError("星表 meta.mag_cutoff 非数值") from exc
    if cutoff != float(CATALOG_DEPTH_MAG):
        raise CatalogMetadataError(
            f"星表深度 {meta['mag_cutoff']} 与加载口径 {CATALOG_DEPTH_MAG} 不一致")


def compute_data_sha256(arrays: Dict[str, np.ndarray]) -> str:
    """对星表数组做规范化 SHA-256（不含 meta，键名排序）。

    规范: 逐个数组写入 ``键名 + dtype.str + shape + 原始字节``。生成脚本
    用它写入 ``meta.data_sha256``，加载端重算比对；任何手改数组而未同步
    meta 都会触发 ``CatalogIntegrityError``。
    """
    h = hashlib.sha256()
    for key in sorted(arrays):
        arr = np.ascontiguousarray(arrays[key])
        h.update(f"K{key}\n".encode("utf-8"))
        h.update(arr.dtype.str.encode("ascii"))
        h.update(f"|{','.join(str(x) for x in arr.shape)}\n".encode("ascii"))
        h.update(arr.tobytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 星等/消光口径
# ---------------------------------------------------------------------------
# ``naked_eye_limit_mag`` 从 ``stars`` 导入（同一函数对象，不是同式副本）：
# 绘制门与星表 LOD 门必须共用一条公式，见 stars.py 的"光害可见性口径"段。


def detail_gain_mag(fov_deg: float) -> float:
    """视场深度增益（等），宽视场 0，小视场最多 ``MAX_DETAIL_GAIN_MAG``。

    LOD 规则（有界、单调、可复核）::

        gain = clamp(0.5 * log2(30 / fov), 0, 1.5)

    - fov >= 30°: 0（保持裸眼基线，120° 满屏不会凭空冒星）
    - fov =  15°: 0.5
    - fov =   5°: 1.29
    - fov <= 2.8°: 封顶 1.5

    这是**渲染 LOD 经验式**，不是光度学模型；放大后像素张角变小、
    暗星不再挤成一团，才允许逐层显示更暗的星。口径写在
    docs/star-catalog.md，显式 ``limit_mag`` 可完全绕过它。
    """
    fov = max(1e-6, float(fov_deg))
    if fov >= DETAIL_REF_FOV_DEG:
        return 0.0
    gain = 0.5 * math.log2(DETAIL_REF_FOV_DEG / fov)
    return min(MAX_DETAIL_GAIN_MAG, max(0.0, gain))


def limit_magnitude(fov_deg: float, bortle: int = DEFAULT_BORTLE) -> float:
    """给定视场 + Bortle 的极限星等 (天顶方向)。

    星体在低仰角还要叠加 ``extinction_delta_mag(alt)``（逐星），
    因此地平附近的**等效**极限是 ``limit_magnitude(...) - Δm(alt)``。
    结果夹在 ``CATALOG_DEPTH_MAG`` 以内，避免声称星表没有的深度。
    """
    m = naked_eye_limit_mag(bortle) + detail_gain_mag(fov_deg)
    return min(CATALOG_DEPTH_MAG, m)


def effective_limit_mag(alt_deg: float, fov_deg: float,
                        bortle: int = DEFAULT_BORTLE,
                        extinction_k: float = EXTINCTION_K) -> float:
    """某仰角处的等效极限星等 = 天顶极限 - 消光增量 Δm(alt)。"""
    return limit_magnitude(fov_deg, bortle) - extinction_delta_mag_array(
        np.asarray([alt_deg], dtype=float), k=extinction_k)[0]


def extinction_delta_mag_array(alt_deg: np.ndarray,
                               k: float = EXTINCTION_K) -> np.ndarray:
    """批量大气消光 Δm = k/sin(alt)（与 ``stars.extinction_delta_mag`` 同口径）。

    仰角夹到 [0.5°, 90°]，Δm 上限 3.5 等；常量全部取自 ``stars``，
    测试逐点锁定与标量函数相等。传入标量/数组均返回 ndarray。
    """
    alt = np.clip(np.asarray(alt_deg, dtype=float),
                  _EXTINCTION_MIN_ALT, 90.0)
    s = np.sin(np.radians(alt))
    s = np.maximum(s, 1e-9)
    return np.minimum(_EXTINCTION_MAX_DM, k / s)


# ---------------------------------------------------------------------------
# 星表
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CatalogStar:
    """单颗星表记录（用于按名查询/测试；批量路径走 numpy 数组）。"""

    hyg_id: int
    hip: int
    name: str
    con: str
    spect: str
    ra_deg: float
    dec_deg: float
    vmag: float
    bv: float
    dist_pc: float


@dataclass
class StarSelection:
    """一次视场查询的结果（numpy 数组，按有效星等从暗到亮排序）。

    Attributes:
        hyg_id/hip/name/con/spect: 星表标识与文本字段（name 空串=无名）。
        ra_deg/dec_deg: J2000 赤经/赤纬（度）。
        az_deg/alt_deg: 该时刻的地平坐标（度）。
        vmag: 视星等；eff_mag: 叠加消光后的有效星等。
        bv: B-V 色指数（NaN=未知）；dist_pc: 距离（NaN=未知）。
        fov_deg/limit_mag/bortle: 本次查询口径截图；``limit_mag`` 是
            实际生效值（已夹到星表深度）。
        requested_limit_mag: 调用方请求的极限星等（未夹断）。
        beyond_catalog_limit: 请求深度超过 9.0（本表覆盖上限）时为 True，
            界面应诚实提示“更深星需 Gaia/Tycho-2”，而不是以为星就这么多。
        available: 预算截断前的命中数；returned = len(vmag)。
        truncated: 是否因 ``max_count`` 预算降级。
    """

    hyg_id: np.ndarray
    hip: np.ndarray
    name: np.ndarray
    con: np.ndarray
    spect: np.ndarray
    ra_deg: np.ndarray
    dec_deg: np.ndarray
    az_deg: np.ndarray
    alt_deg: np.ndarray
    vmag: np.ndarray
    eff_mag: np.ndarray
    bv: np.ndarray
    dist_pc: np.ndarray
    fov_deg: float
    limit_mag: float
    requested_limit_mag: float
    bortle: int
    available: int
    truncated: bool = False
    beyond_catalog_limit: bool = False

    def __len__(self) -> int:
        return int(len(self.vmag))

    @property
    def returned(self) -> int:
        return len(self)


class StarCatalog:
    """全天星表容器（只读）。批量选择见 ``select_for_view``。"""

    def __init__(self, arrays: Dict[str, np.ndarray], meta: Dict[str, Any]):
        self.meta: Dict[str, Any] = dict(meta)
        self.hyg_id = arrays["hyg_id"].astype(np.int32, copy=False)
        self.hip = arrays["hip"].astype(np.int32, copy=False)
        self.ra_deg = arrays["ra_deg"].astype(np.float32, copy=False)
        self.dec_deg = arrays["dec_deg"].astype(np.float32, copy=False)
        self.vmag = arrays["vmag"].astype(np.float32, copy=False)
        self.bv = arrays["bv"].astype(np.float32, copy=False)
        self.dist_pc = arrays["dist_pc"].astype(np.float32, copy=False)
        self.name = arrays["proper"]
        self.con = arrays["con"]
        self.spect = arrays["spect"]
        self.mag_bin_edges = arrays["mag_bin_edges"].astype(np.float32,
                                                           copy=False)
        self.mag_bin_start = arrays["mag_bin_start"].astype(np.int32, copy=False)
        self.dec_order = arrays["dec_order"].astype(np.int32, copy=False)
        self._dec_sorted = self.dec_deg[self.dec_order]
        # proper 名索引（大小写不敏感；重名取最亮）。
        self._name_index: Dict[str, int] = {}
        for i in np.nonzero(self.name != "")[0]:
            key = str(self.name[i]).strip().lower()
            if not key:
                continue
            old = self._name_index.get(key)
            if old is None or self.vmag[i] < self.vmag[old]:
                self._name_index[key] = int(i)

    # -- 基本属性 ---------------------------------------------------------
    @property
    def count(self) -> int:
        return int(len(self.vmag))

    def magnitude_bins(self) -> List[Tuple[float, float, int]]:
        """返回 [(下界, 上界, 星数), ...]（每 0.5 等一档，边界左闭右开）。"""
        edges = self.mag_bin_edges
        starts = self.mag_bin_start
        return [(float(edges[i]), float(edges[i + 1]),
                 int(starts[i + 1] - starts[i]))
                for i in range(len(edges) - 1)]

    def count_below(self, limit_mag: float) -> int:
        """星等 <= limit 的星数（星表已按 mag 升序，二分即可）。"""
        return int(np.searchsorted(self.vmag, float(limit_mag), side="right"))

    def find_by_name(self, name: str) -> Optional[CatalogStar]:
        """按 proper 名精确查找（大小写不敏感）；无名/未收录返回 None。"""
        idx = self._name_index.get(str(name).strip().lower())
        if idx is None:
            return None
        return self._star_at(idx)

    def search_by_name(self, text: str, limit: int = 20) -> List[CatalogStar]:
        """按 proper 名搜索：精确命中排第一，其后前缀匹配，按星等升序。

        为什么只搜 proper 名：全天 83,479 颗里只有 430 颗有专名。用 HIP
        编号或坐标去搜会让用户看到一串没有可读名字的星，不如让他用星图
        直接点。空串返回空表（不返回全表）。
        """
        key = str(text or "").strip().lower()
        if not key:
            return []
        out: List[CatalogStar] = []
        seen: set = set()
        exact_idx = self._name_index.get(key)
        if exact_idx is not None:
            out.append(self._star_at(exact_idx))
            seen.add(exact_idx)
        hits: List[Tuple[float, int]] = []
        for name_key, idx in self._name_index.items():
            if idx in seen or not name_key.startswith(key):
                continue
            hits.append((float(self.vmag[idx]), idx))
        hits.sort()
        for _mag, idx in hits:
            if len(out) >= max(1, int(limit)):
                break
            out.append(self._star_at(idx))
        return out

    def _star_at(self, idx: int) -> CatalogStar:
        return CatalogStar(
            hyg_id=int(self.hyg_id[idx]),
            hip=int(self.hip[idx]),
            name=str(self.name[idx]),
            con=str(self.con[idx]),
            spect=str(self.spect[idx]),
            ra_deg=float(self.ra_deg[idx]),
            dec_deg=float(self.dec_deg[idx]),
            vmag=float(self.vmag[idx]),
            bv=float(self.bv[idx]),
            dist_pc=float(self.dist_pc[idx]),
        )

    # -- 视场选择 ---------------------------------------------------------
    def select_for_view(
        self,
        *,
        station: GroundStation,
        jd_ut1: float,
        center_az: float,
        center_alt: float,
        fov_deg: float,
        bortle: int = DEFAULT_BORTLE,
        limit_mag: Optional[float] = None,
        max_count: int = DEFAULT_MAX_COUNT,
        margin_deg: float = 1.0,
        jd_tt: Optional[float] = None,
        extinction_k: float = EXTINCTION_K,
    ) -> StarSelection:
        """返回该视场/时刻应画的星集合（地平以上 + 视锥内 + 极限星等内）。

        Args:
            station / jd_ut1: 观测站与 UT1 儒略日（走既有坐标链）。
            center_az / center_alt: 视场中心地平坐标（度）。
            fov_deg: 视场直径（度）；视锥半角 = fov/2（再加 ``margin_deg``）。
            bortle: 光害等级（与 ``star_draw`` 同一口径）。
            limit_mag: 显式极限星等；None 时用 ``limit_magnitude(fov, bortle)``。
            max_count: 单次预算；超出时保留最亮的 N 颗并置 ``truncated``。
            margin_deg: 视锥余量（默认 1°，给标签/星点边缘留空间）。
            jd_tt: 力学时儒略日；None 时与既有链一致取 ``jd_ut1``。
            extinction_k: 大气消光系数 k（Δm=k/sin(alt)，逐星按各自仰角算）；
                与 Bortle 相互独立——Bortle 定天顶极限星等，k 只压低低仰角星。

        Returns:
            ``StarSelection``；数组按有效星等从暗到亮排序（亮星后画）。
            若请求深度 > 9.0（本表覆盖上限），``limit_mag`` 会夹到 9.0 并
            置 ``beyond_catalog_limit=True``、``requested_limit_mag`` 保留原值。
        """
        if max_count <= 0:
            raise ValueError("max_count 必须 > 0")
        fov = float(fov_deg)
        if not math.isfinite(fov) or fov <= 0.0:
            raise ValueError("fov_deg 必须为有限正数")

        requested_limit = (float(limit_mag) if limit_mag is not None
                           else limit_magnitude(fov, bortle))
        limit = min(float(CATALOG_DEPTH_MAG), requested_limit)
        beyond = requested_limit > float(CATALOG_DEPTH_MAG) + 1e-9

        r_deg = min(180.0, 0.5 * fov + max(0.0, float(margin_deg)))
        center_v = vec_from_azalt(float(center_az), float(center_alt))

        M = _j2000_to_altaz_matrix(station, float(jd_ut1), jd_tt)
        center_j2000 = M.T @ center_v
        dec_c = math.degrees(math.asin(
            max(-1.0, min(1.0, float(center_j2000[1])))))

        # 1) 赤纬带裁剪（J2000 固定，锥内星的 |dec - dec_c| <= r）。
        n = self.count
        if r_deg >= 90.0:
            cand = np.arange(n, dtype=np.int32)
        else:
            lo = max(-90.0, dec_c - r_deg)
            hi = min(90.0, dec_c + r_deg)
            i0 = int(np.searchsorted(self._dec_sorted, lo, side="left"))
            i1 = int(np.searchsorted(self._dec_sorted, hi, side="right"))
            cand = self.dec_order[i0:i1]
        if len(cand) == 0:
            return _empty_selection(fov, limit, bortle, requested_limit, beyond)

        # 2) 星等预筛（星表已按 mag 升序，等价于前 count_below 颗）。
        m = self.vmag[cand].astype(np.float64)
        cand = cand[m <= limit]
        if len(cand) == 0:
            return _empty_selection(fov, limit, bortle, requested_limit, beyond)
        m = self.vmag[cand].astype(np.float64)

        # 3) J2000 -> 地平（批量矩阵乘，矩阵全部来自 celestial_geometry）。
        ra = np.radians(self.ra_deg[cand].astype(np.float64))
        dec = np.radians(self.dec_deg[cand].astype(np.float64))
        cd = np.cos(dec)
        v_j2000 = np.column_stack((cd * np.sin(ra),
                                   np.sin(dec),
                                   -cd * np.cos(ra)))
        v_alt = v_j2000 @ M.T

        alt = np.degrees(np.arcsin(np.clip(-v_alt[:, 2], -1.0, 1.0)))
        az = np.degrees(np.arctan2(v_alt[:, 1], v_alt[:, 0])) % 360.0

        # 4) 地平门 + 视锥门 + 逐星消光后的极限门。
        cos_r = math.cos(math.radians(r_deg))
        dot = v_alt @ center_v
        mask = (alt >= 0.0) & (dot >= cos_r)
        if not np.any(mask):
            return _empty_selection(fov, limit, bortle, requested_limit, beyond)

        cand = cand[mask]
        m = m[mask]
        alt = alt[mask]
        az = az[mask]
        zero = np.zeros(0)
        eff = m + extinction_delta_mag_array(alt, k=extinction_k)
        keep = eff <= limit
        cand = cand[keep]
        m = m[keep]
        alt = alt[keep]
        az = az[keep]
        eff = eff[keep]
        if len(cand) == 0:
            return _empty_selection(fov, limit, bortle, requested_limit, beyond)

        available = int(len(cand))
        truncated = available > int(max_count)
        if truncated:
            # 预算降级：保留有效星等最亮（数值最小）的 max_count 颗。
            part = np.argpartition(eff, int(max_count) - 1)[:int(max_count)]
            cand, m, alt, az, eff = (cand[part], m[part], alt[part],
                                     az[part], eff[part])
        # 从暗到亮排序，让调用方顺序绘制时亮星压在暗星上。
        order = np.argsort(-eff, kind="stable")
        cand, m, alt, az, eff = (cand[order], m[order], alt[order],
                                 az[order], eff[order])

        return StarSelection(
            hyg_id=self.hyg_id[cand].copy(),
            hip=self.hip[cand].copy(),
            name=self.name[cand].copy(),
            con=self.con[cand].copy(),
            spect=self.spect[cand].copy(),
            ra_deg=self.ra_deg[cand].astype(np.float64),
            dec_deg=self.dec_deg[cand].astype(np.float64),
            az_deg=az,
            alt_deg=alt,
            vmag=m,
            eff_mag=eff,
            bv=self.bv[cand].astype(np.float64),
            dist_pc=self.dist_pc[cand].astype(np.float64),
            fov_deg=fov,
            limit_mag=float(limit),
            requested_limit_mag=float(requested_limit),
            bortle=int(bortle),
            available=available,
            truncated=truncated,
            beyond_catalog_limit=bool(beyond),
        )


def _empty_selection(fov: float, limit: float, bortle: int,
                     requested_limit: Optional[float] = None,
                     beyond: bool = False) -> StarSelection:
    empty_f = np.zeros(0, dtype=float)
    empty_i = np.zeros(0, dtype=np.int32)
    empty_s = np.zeros(0, dtype="<U1")
    return StarSelection(
        hyg_id=empty_i, hip=empty_i, name=empty_s, con=empty_s, spect=empty_s,
        ra_deg=empty_f, dec_deg=empty_f, az_deg=empty_f, alt_deg=empty_f,
        vmag=empty_f, eff_mag=empty_f, bv=empty_f, dist_pc=empty_f,
        fov_deg=float(fov), limit_mag=float(limit),
        requested_limit_mag=float(limit if requested_limit is None
                                  else requested_limit),
        bortle=int(bortle), available=0, truncated=False,
        beyond_catalog_limit=bool(beyond),
    )


# ---------------------------------------------------------------------------
# 坐标链矩阵（全部复用 celestial_geometry 的既有函数，不另造坐标系）
# ---------------------------------------------------------------------------
def _j2000_to_altaz_matrix(station: GroundStation, jd_ut1: float,
                           jd_tt: Optional[float] = None) -> np.ndarray:
    """返回 3x3 矩阵 M，使 ``v_altaz = M @ v_j2000``（列向量）。"""
    jd_tt = float(jd_ut1 if jd_tt is None else jd_tt)
    P = precession_matrix(jd_tt)
    N = nutation_matrix(jd_tt)
    R = station.equatorial_to_altaz_matrix(float(jd_ut1))
    return R @ N @ P


# ---------------------------------------------------------------------------
# 加载与模块级接口
# ---------------------------------------------------------------------------
def load_catalog(path: Optional[str] = None) -> StarCatalog:
    """从 npz 加载星表（不做全局缓存；通常请用 ``get_catalog``）。

    加载守卫:
      - 缺 ``meta`` 或 meta 缺 source URL/许可/SHA 等必需字段 →
        ``CatalogMetadataError``（明确报错，拒绝静默加载）；
      - 数组重算 SHA-256 与 ``meta.data_sha256`` 不一致 →
        ``CatalogIntegrityError``（防止手改数据未同步 meta）；
      - ``meta.star_count`` 与数组长度不一致 → ``CatalogIntegrityError``。
    星表文件本身缺失时仍抛 ``FileNotFoundError``。
    """
    catalog_path = path or _DEFAULT_CATALOG_PATH
    with np.load(catalog_path, allow_pickle=False) as data:
        if "meta" not in data.files:
            raise CatalogMetadataError("星表 npz 缺少 meta 键")
        try:
            meta = json.loads(str(data["meta"].item()))
        except (ValueError, TypeError) as exc:
            raise CatalogMetadataError("星表 meta 不是合法 JSON") from exc
        _validate_meta(meta)
        arrays = {key: data[key] for key in data.files if key != "meta"}
    actual_sha = compute_data_sha256(arrays)
    if actual_sha != str(meta["data_sha256"]):
        raise CatalogIntegrityError(
            "星表数据 SHA-256 与 meta.data_sha256 不一致（疑似手改数据）")
    if int(meta["star_count"]) != int(len(arrays.get("vmag", []))):
        raise CatalogIntegrityError(
            f"meta.star_count={meta['star_count']} 与数组长度 "
            f"{len(arrays.get('vmag', []))} 不一致")
    return StarCatalog(arrays, meta)


@lru_cache(maxsize=1)
def get_catalog() -> StarCatalog:
    """进程级单例星表（首次调用解压/加载，之后零成本复用）。"""
    return load_catalog()


def select_for_view(
    station: GroundStation,
    jd_ut1: float,
    center_az: float,
    center_alt: float,
    fov_deg: float,
    *,
    bortle: int = DEFAULT_BORTLE,
    limit_mag: Optional[float] = None,
    max_count: int = DEFAULT_MAX_COUNT,
    margin_deg: float = 1.0,
    jd_tt: Optional[float] = None,
    catalog: Optional[StarCatalog] = None,
    extinction_k: float = EXTINCTION_K,
) -> StarSelection:
    """模块级入口：用单例星表返回该视场应画的星集合。

    参数与 ``StarCatalog.select_for_view`` 相同。签名稳定，界面层可直接::

        from mbdsdr_ai.skyengine.star_catalog import select_for_view
        sel = select_for_view(gs, jd, az, alt, fov, bortle=3)
        for i in range(len(sel)):
            ...  # sel.az_deg[i], sel.alt_deg[i], sel.vmag[i], sel.bv[i]

    星表缺失时抛 ``FileNotFoundError``（不静默回退到假星表）。
    """
    cat = catalog if catalog is not None else get_catalog()
    return cat.select_for_view(
        station=station,
        jd_ut1=jd_ut1,
        center_az=center_az,
        center_alt=center_alt,
        fov_deg=fov_deg,
        bortle=bortle,
        limit_mag=limit_mag,
        max_count=max_count,
        margin_deg=margin_deg,
        jd_tt=jd_tt,
        extinction_k=extinction_k,
    )
