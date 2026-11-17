# SPDX-License-Identifier: MIT
"""把统一文化包（``SkyCulturePack``）适配成渲染层可直接消费的折线/锚点。

为什么单独一层
-------------
``sky_culture`` 只描述「哪个文化有哪些 line 与名称」，不碰星表，也不碰
Qt；渲染层需要的是「每段折线两端的 RA/Dec」和「标签锚点」。这层只做
HIP → 星表坐标的解析，并把**解析不到的 HIP 段整段跳过**（与星官未映射
星的处理一致），不拿 (0,0) 或平均坐标补造。

缓存口径：星表 HIP 索引按进程建一次；每个文化包的适配结果按 pack id
缓存，切 FOV/开关图层不重建。
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional, Tuple

from .sky_culture import SkyCultureGroup, get_pack
from .star_catalog import get_catalog

__all__ = [
    "CultureStar",
    "CultureGroupAdapter",
    "build_group_adapters",
    "culture_tag",
]

#: 文件包 meta 不强制写短标签；这里给已知文化一个稳定徽标，未知回落 id。
_TAGS = {
    "japanese": "日本",
    "indian": "印度",
    "norse": "北欧",
    "indian_nakshatras": "印度宿",
    "seri": "Seri",
}


@dataclass(frozen=True)
class CultureStar:
    """文化连线端点：HIP + J2000 坐标 + 星等（锚点按最亮取）。"""
    hip: int
    ra_deg: float
    dec_deg: float
    vmag: float


class CultureGroupAdapter:
    """一个星座/星群/星官：只含能解析到星表的段与锚点。"""

    __slots__ = ("group", "segments")

    def __init__(self, group: SkyCultureGroup,
                 stars: Dict[int, CultureStar]) -> None:
        self.group = group
        segs = []
        for a, b in group.segments():
            sa = stars.get(int(a))
            sb = stars.get(int(b))
            if sa is None or sb is None:
                continue          # 星表没有这颗星：整段跳过，不补坐标
            segs.append((sa, sb))
        self.segments: Tuple[Tuple[CultureStar, CultureStar], ...] = tuple(segs)

    @property
    def key(self) -> str:
        return self.group.group_id

    @property
    def display_name(self) -> str:
        names = self.group.names or {}
        return (str(names.get("native") or names.get("english")
                    or self.group.group_id))

    def anchor(self) -> Optional[CultureStar]:
        """组内最亮星作为标签锚点；没有任何可用段时返回 None。"""
        best = None
        for sa, sb in self.segments:
            for star in (sa, sb):
                if best is None or star.vmag < best.vmag:
                    best = star
        return best


@lru_cache(maxsize=1)
def _hip_index() -> Dict[int, int]:
    """HIP → 星表行号；只保留正编号（HYG 用 -1 表示无编号）。"""
    catalog = get_catalog()
    out: Dict[int, int] = {}
    for i, hip in enumerate(catalog.hip):
        value = int(hip)
        if value > 0 and value not in out:
            out[value] = i
    return out


@lru_cache(maxsize=16)
def build_group_adapters(pack_id: str) -> Tuple[CultureGroupAdapter, ...]:
    """按 pack id 构建（并缓存）所有组的适配对象。"""
    pack = get_pack(str(pack_id))
    catalog = get_catalog()
    index = _hip_index()

    stars: Dict[int, CultureStar] = {}
    for group in pack.groups:
        for poly in group.lines:
            for hip in poly:
                key = int(hip)
                if key in stars:
                    continue
                row = index.get(key)
                if row is None:
                    continue
                stars[key] = CultureStar(
                    hip=key,
                    ra_deg=float(catalog.ra_deg[row]),
                    dec_deg=float(catalog.dec_deg[row]),
                    vmag=float(catalog.vmag[row]),
                )
    return tuple(CultureGroupAdapter(g, stars) for g in pack.groups)


def culture_tag(pack_id: str) -> str:
    """状态卡徽标：已知文化给中文短名，其余回落 id。"""
    pack = get_pack(str(pack_id))
    for key in ("tag", "short_name"):
        value = pack.meta.get(key)
        if value:
            return str(value)
    return _TAGS.get(pack.id, pack.id)
