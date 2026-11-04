# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/skyengine/xingguan.py — 中国星官体系（三垣二十八宿）
===============================================================

**独立体系**：本模块只读 ``data/xingguan_chinese.json``，不引用 IAU 星座
名称或连线；IAU 88 座在 ``constellations.py``。两套体系可以同时打开，
但每个图层都带 ``system_id`` / ``display_name``，界面必须分别标注，
禁止把星官名与西方星座名合并成同一名称空间。

数据来源
--------
``data/xingguan_chinese.json`` 由 ``build_constellation_data.py`` 从本机
Stellarium ``chinese`` 天空文化生成（CC BY-SA 4.0）:

- ``star_names.zh_CN.fab`` → 三垣/二十八宿所含星官与恒星（HIP）；
- ``index.json`` → 星官/垣墙连线；
- ``description.md`` → 星官拼音/英文名与中文说明。

恒星映射
--------
源数据只给 HIP 编号；生成时按 **HIP → HYG v4.1 id 精确匹配**，并把 HYG
的 proper/Bayer 名一并记录。映射不到的 HIP **留空并计数**，不做名称猜测、
不从记忆补星。当前最小集 2,910 颗（去重 HIP），其中 2,907 颗能映射到
HYG，3 颗映射不到；2,906 颗在随仓 V≤9.0 星表内。

三垣的“所含恒星”口径
--------------------
``star_names.zh_CN.fab`` 在 ``###紫微垣/太微垣/天市垣`` 下逐条列出星官与
恒星；本模块按该分节汇总整个垣。绘制连线用左/右垣两条墙线（源数据里
垣本身只有墙线），垣内其他星官的连线分别保存在各 ``xingguan`` 条目。

未验证项见 docs/constellations.md。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, Union

from .constellations import (
    SYSTEM_CHINESE,
    ConstellationLayer,
    ConstellationMetadataError,
    ConstellationIntegrityError,
    _load_json,
)

__all__ = [
    "SYSTEM_CHINESE",
    "XingguanMetadataError",
    "XingguanIntegrityError",
    "XingguanStar",
    "Xingguan",
    "XingguanRegion",
    "XingguanSet",
    "XingguanAnchor",
    "load_xingguan",
    "get_xingguan",
    "xingguan_regions",
    "xingguan_region",
    "xingguan_segments",
    "xingguan_all_segments",
    "xingguan_label",
    "xingguan_culture",
    "xingguan_anchor",
    "xingguan_anchor_reason",
    "system_info",
    "xingguan_layer",
]

XingguanMetadataError = ConstellationMetadataError
XingguanIntegrityError = ConstellationIntegrityError

_XG_DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "xingguan_chinese.json")

_REQUIRED_META = (
    "schema", "system_id", "source", "source_url", "source_license",
    "source_license_url", "attribution", "source_files", "data_sha256",
    "region_count", "yuan_count", "xiu_count", "xingguan_count",
    "unique_star_hip_count", "mapped_count", "in_catalog_count",
    "unmapped_count", "mapping",
)


@dataclass(frozen=True)
class XingguanStar:
    """星官所含恒星；映射不到时 hyg_id/坐标为空，绝不编造。"""
    hip: int
    hyg_id: Optional[int]
    proper: str
    bayer: str
    ra_deg: Optional[float]
    dec_deg: Optional[float]
    vmag: Optional[float]
    con: str
    in_catalog: bool
    mapped: bool
    zh_name: str
    alt_names: Tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.zh_name or self.proper or self.bayer or f"HIP {self.hip}"


@dataclass(frozen=True)
class Xingguan:
    """单个星官：中文名、拼音/英文名、所含恒星、连线。"""
    name_zh: str
    name_zh_source: str
    name_en: str
    pinyin: str
    stars: Tuple[XingguanStar, ...]
    lines: Tuple[Tuple[XingguanStar, ...], ...]
    segment_count: int
    unresolved_tokens: Tuple[str, ...]
    primary_star_count_source: str
    added_star_count_source: str


@dataclass(frozen=True)
class XingguanRegion:
    """三垣之一或二十八宿之一；含若干星官。"""
    key: str
    type: str
    group: str
    name_zh: str
    name_zh_source: str
    name_en: str
    pinyin: str
    components: Tuple[str, ...]
    xingguan: Tuple[Xingguan, ...]
    xingguan_count: int
    stars: Tuple[XingguanStar, ...]
    lines: Tuple[Tuple[XingguanStar, ...], ...]
    segment_count: int
    star_count: int
    unique_star_hip_count: int
    mapped_count: int
    in_catalog_count: int
    unmapped_count: int
    unmapped_hips: Tuple[int, ...]
    unresolved_tokens: Tuple[str, ...]

    def segments(self) -> Tuple[Tuple[XingguanStar, XingguanStar], ...]:
        out: List[Tuple[XingguanStar, XingguanStar]] = []
        for poly in self.lines:
            for a, b in zip(poly, poly[1:]):
                out.append((a, b))
        return tuple(out)

    def all_segments(self) -> Tuple[Tuple[XingguanStar, XingguanStar], ...]:
        """垣墙/宿连线 + 各星官连线（去重按 (hip, hip) 对，不跨体系）。"""
        seen = set()
        out: List[Tuple[XingguanStar, XingguanStar]] = []
        for a, b in self.segments():
            key = (a.hip, b.hip)
            if key not in seen:
                seen.add(key)
                out.append((a, b))
        for xg in self.xingguan:
            for poly in xg.lines:
                for a, b in zip(poly, poly[1:]):
                    key = (a.hip, b.hip)
                    if key not in seen:
                        seen.add(key)
                        out.append((a, b))
        return tuple(out)


@dataclass(frozen=True)
class XingguanAnchor:
    """标签锚点：区域/星官里**最亮且可绘制**（V ≤ 9 且在随仓星表内）的成员星。

    只由数据层给出，避免渲染方自行遍历成员再算一遍（第二份事实源）。
    无可用成员时 ``xingguan_anchor()`` 返回 ``None``，原因见
    ``xingguan_anchor_reason()`` 的稳定码。
    """
    hip: int
    hyg_id: Optional[int]
    ra_deg: float
    dec_deg: float
    vmag: float
    con: str
    zh_name: str
    proper: str
    bayer: str

    @property
    def label(self) -> str:
        return self.zh_name or self.proper or self.bayer or f"HIP {self.hip}"


class XingguanSet:
    """31 个区域只读索引；键为中文区域名（与 IAU 三字母缩写不相交）。"""

    def __init__(self, meta: Dict[str, Any],
                 regions: Tuple[XingguanRegion, ...]):
        self.meta = meta
        self.regions = regions
        self.by_key: Dict[str, XingguanRegion] = {r.key: r for r in regions}

    def __len__(self) -> int:
        return len(self.regions)

    def __iter__(self):
        return iter(self.regions)

    def __getitem__(self, key: str) -> XingguanRegion:
        return self.by_key[key]

    def get(self, key: str) -> Optional[XingguanRegion]:
        return self.by_key.get(key)


def _parse_star(raw: dict, *, line_ref: bool = False) -> XingguanStar:
    mapped = True if line_ref else bool(raw.get("mapped"))
    return XingguanStar(
        hip=int(raw["hip"]),
        hyg_id=(int(raw["hyg_id"]) if raw.get("hyg_id") is not None else None),
        proper=str(raw.get("proper") or ""),
        bayer=str(raw.get("bayer") or ""),
        ra_deg=(float(raw["ra_deg"]) if raw.get("ra_deg") is not None else None),
        dec_deg=(float(raw["dec_deg"]) if raw.get("dec_deg") is not None else None),
        vmag=(float(raw["vmag"]) if raw.get("vmag") is not None else None),
        con=str(raw.get("con") or ""),
        in_catalog=bool(raw.get("in_catalog", False)),
        mapped=mapped,
        zh_name=str(raw.get("zh_name") or ""),
        alt_names=tuple(raw.get("alt_names") or ()),
    )


def _parse_lines(raw_lines) -> Tuple[Tuple[XingguanStar, ...], ...]:
    return tuple(tuple(_parse_star(s, line_ref=True) for s in poly)
                 for poly in raw_lines or ())


def _parse_xingguan(raw: dict) -> Xingguan:
    return Xingguan(
        name_zh=str(raw["name_zh"]),
        name_zh_source=str(raw.get("name_zh_source") or ""),
        name_en=str(raw.get("name_en") or ""),
        pinyin=str(raw.get("pinyin") or ""),
        stars=tuple(_parse_star(s) for s in raw.get("stars") or ()),
        lines=_parse_lines(raw.get("lines")),
        segment_count=int(raw.get("segment_count", 0)),
        unresolved_tokens=tuple(str(t) for t in raw.get("unresolved_tokens") or ()),
        primary_star_count_source=str(raw.get("primary_star_count_source") or ""),
        added_star_count_source=str(raw.get("added_star_count_source") or ""),
    )


def _parse_region(raw: dict) -> XingguanRegion:
    return XingguanRegion(
        key=str(raw["key"]),
        type=str(raw["type"]),
        group=str(raw.get("group") or ""),
        name_zh=str(raw.get("name_zh") or raw["key"]),
        name_zh_source=str(raw.get("name_zh_source") or ""),
        name_en=str(raw.get("name_en") or ""),
        pinyin=str(raw.get("pinyin") or ""),
        components=tuple(raw.get("components") or ()),
        xingguan=tuple(_parse_xingguan(x) for x in raw.get("xingguan") or ()),
        xingguan_count=int(raw.get("xingguan_count", 0)),
        stars=tuple(_parse_star(s) for s in raw.get("stars") or ()),
        lines=_parse_lines(raw.get("lines")),
        segment_count=int(raw.get("segment_count", 0)),
        star_count=int(raw.get("star_count", 0)),
        unique_star_hip_count=int(raw.get("unique_star_hip_count", 0)),
        mapped_count=int(raw.get("mapped_count", 0)),
        in_catalog_count=int(raw.get("in_catalog_count", 0)),
        unmapped_count=int(raw.get("unmapped_count", 0)),
        unmapped_hips=tuple(int(h) for h in raw.get("unmapped_hips") or ()),
        unresolved_tokens=tuple(str(t) for t in raw.get("unresolved_tokens") or ()),
    )


@lru_cache(maxsize=1)
def load_xingguan(path: Optional[str] = None) -> XingguanSet:
    """加载中国星官最小集；meta/SHA/未映射计数不合法时抛错，不静默降级。"""
    payload = _load_json(path or _XG_DATA_PATH,
                         expected_system=SYSTEM_CHINESE, required=_REQUIRED_META)
    meta = payload["meta"]
    rows = payload.get("regions") or []
    if len(rows) != 31:
        raise XingguanIntegrityError(f"星官区域数不是 31: {len(rows)}")
    yuan = [r for r in rows if r.get("type") == "yuan"]
    xiu = [r for r in rows if r.get("type") == "xiu"]
    if len(yuan) != 3 or len(xiu) != 28:
        raise XingguanIntegrityError(
            f"三垣/二十八宿数量异常: yuan={len(yuan)}, xiu={len(xiu)}")
    keys = [r["key"] for r in rows]
    if len(set(keys)) != 31:
        raise XingguanIntegrityError("星官区域键存在重复")
    regions = tuple(_parse_region(r) for r in rows)

    # 计数与 meta 自洽；未映射清单必须与各区域并集一致。
    all_unmapped = sorted({h for r in regions for h in r.unmapped_hips})
    if all_unmapped != sorted(int(h) for h in meta["unmapped_hips"]):
        raise XingguanIntegrityError("meta.unmapped_hips 与区域数据不一致")
    if len(all_unmapped) != int(meta["unmapped_count"]):
        raise XingguanIntegrityError("meta.unmapped_count 与区域数据不一致")
    unique = {s.hip for r in regions for s in r.stars}
    if len(unique) != int(meta["unique_star_hip_count"]):
        raise XingguanIntegrityError("meta.unique_star_hip_count 与区域数据不一致")
    mapped = {s.hip for r in regions for s in r.stars if s.mapped}
    if len(mapped) != int(meta["mapped_count"]):
        raise XingguanIntegrityError("meta.mapped_count 与区域数据不一致")
    in_cat = {s.hip for r in regions for s in r.stars if s.in_catalog}
    if len(in_cat) != int(meta["in_catalog_count"]):
        raise XingguanIntegrityError("meta.in_catalog_count 与区域数据不一致")
    for r in regions:
        for poly in r.lines + tuple(x for xg in r.xingguan for x in xg.lines):
            for s in poly:
                if not s.mapped or not s.in_catalog:
                    raise XingguanIntegrityError(
                        f"{r.key}: 连线端点 HIP {s.hip} 不在随仓星表，"
                        "生成脚本应先裁掉该线段")
    return XingguanSet(meta, regions)


def get_xingguan() -> XingguanSet:
    return load_xingguan()


def xingguan_regions() -> Tuple[XingguanRegion, ...]:
    return load_xingguan().regions


def xingguan_region(key: str) -> XingguanRegion:
    ds = load_xingguan()
    region = ds.by_key.get(str(key))
    if region is None:
        raise KeyError(f"未知星官区域: {key!r}")
    return region


def xingguan_segments(
        key: str) -> Tuple[Tuple[XingguanStar, XingguanStar], ...]:
    """区域级连线（三垣=左右垣；二十八宿=宿自身连线）。"""
    return xingguan_region(key).segments()


def xingguan_all_segments(
        key: str) -> Tuple[Tuple[XingguanStar, XingguanStar], ...]:
    """区域级 + 区域内各星官连线，按 HIP 对去重。"""
    return xingguan_region(key).all_segments()


def xingguan_label(key: str, *, with_system_tag: bool = False) -> str:
    region = xingguan_region(key)
    label = region.key
    return f"[中国星官] {label}" if with_system_tag else label


def xingguan_culture(key: str) -> Optional[Dict[str, str]]:
    """星官区域的名称信息；仅来自中国星官数据，不混入 IAU 名称。"""
    region = load_xingguan().by_key.get(str(key))
    if region is None:
        return None
    return {
        "zh": region.name_zh,
        "en": region.name_en,
        "pinyin": region.pinyin,
        "group": region.group,
        "type": region.type,
    }


_AnchorTarget = Union[str, XingguanRegion, Xingguan]


def _anchor_members(target: _AnchorTarget) -> Tuple[XingguanStar, ...]:
    if isinstance(target, str):
        return xingguan_region(target).stars
    return tuple(getattr(target, "stars", ()))


def _brightest_drawable(
        stars: Tuple[XingguanStar, ...]) -> Optional[XingguanStar]:
    """最亮且可绘制（in_catalog 且坐标/星等齐全）；同星等按 HIP 升序决胜。"""
    best: Optional[XingguanStar] = None
    for s in stars:
        if (not s.in_catalog or s.vmag is None
                or s.ra_deg is None or s.dec_deg is None):
            continue
        if best is None or (s.vmag, s.hip) < (best.vmag, best.hip):
            best = s
    return best


def xingguan_anchor(target: _AnchorTarget) -> Optional[XingguanAnchor]:
    """区域或单个星官的标签锚点；无可用成员返回 ``None``（原因见下函数）。

    入参可以是区域键（``"角宿"``）、``XingguanRegion`` 或 ``Xingguan``。
    """
    best = _brightest_drawable(_anchor_members(target))
    if best is None:
        return None
    return XingguanAnchor(
        hip=best.hip, hyg_id=best.hyg_id,
        ra_deg=float(best.ra_deg), dec_deg=float(best.dec_deg),
        vmag=float(best.vmag), con=best.con, zh_name=best.zh_name,
        proper=best.proper, bayer=best.bayer,
    )


def xingguan_anchor_reason(target: _AnchorTarget) -> Optional[str]:
    """``None`` = 锚点可用；否则返回稳定原因码（不静默空白）。

    - ``no_members``：区域/星官没有任何成员星；
    - ``no_mappable_member``：有成员但全部未映射到 HYG；
    - ``no_drawable_member``：能映射，但全部不在随仓 V ≤ 9 星表内。
    """
    stars = _anchor_members(target)
    if not stars:
        return "no_members"
    if not any(s.mapped for s in stars):
        return "no_mappable_member"
    if _brightest_drawable(stars) is None:
        return "no_drawable_member"
    return None


def system_info() -> Dict[str, Any]:
    ds = load_xingguan()
    return {
        "system_id": SYSTEM_CHINESE,
        "display_name_zh": ds.meta.get("system_name_zh", "中国星官（三垣二十八宿）"),
        "display_name_en": ds.meta.get("system_name_en", "Chinese Xingguan"),
        "tag": "中国星官",
        "source": ds.meta.get("source", ""),
        "source_url": ds.meta.get("source_url", ""),
        "source_license": ds.meta.get("source_license", ""),
        "lines_are_iau_official": False,
        "count": len(ds),
        "region_count": len(ds),
        "xingguan_count": int(ds.meta.get("xingguan_count", 0)),
        "unmapped_count": int(ds.meta.get("unmapped_count", 0)),
    }


def xingguan_layer() -> ConstellationLayer:
    ds = load_xingguan()
    return ConstellationLayer(
        system_id=SYSTEM_CHINESE,
        display_name=ds.meta.get("system_name_zh", "中国星官（三垣二十八宿）"),
        tag="中国星官",
        source=ds.meta.get("source", ""),
        source_license=ds.meta.get("source_license", ""),
        items=ds.regions,
    )
