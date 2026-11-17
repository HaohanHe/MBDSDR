# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/skyengine/sky_culture.py — 统一多文化天空模型
=======================================================

把 IAU 88 星座、中国星官与 Stellarium 其他天空文化收进**同一套可插拔
文化包**：每个包只声明 ``groups``（星座/星官/星群 + HIP 折线）与
``names``（HIP → 该文化下的名称），渲染方仍按 ``system_id`` / 包 id 分层
绘制，不新增第三套渲染。

数据来源与边界
--------------
- 适配器 ``pack_from_iau()`` / ``pack_from_xingguan()`` **不复制数据**，
  直接读取 ``constellations_iau.json`` / ``xingguan_chinese.json``，
  因此现有 API 与测试保持原样。
- 文件包来自 ``data/skycultures/*.json``，由
  ``scripts/build_sky_cultures.py`` 从本机 Stellarium 天空文化生成；
  只取 ``constellations[].lines/id/common_name``，不复制 artwork、
  插图或描述文本。HIP 不在本地星表的记为 ``unresolved_hips``，
  不丢弃、不猜测。

许可
----
文件包只接受 ``CC BY-SA 4.0``、``CC BY 4.0``、``CC0 / Public Domain``；
其它许可（CC BY-ND、CC BY-NC*、GPL 等）在生成时就拒绝，加载时还会再
校验一次，坏包不会被静默跳过。

上游有些文化的许可行带作用域限定词，如 ``Text and data: CC BY-SA 4.0``
（该包插图另有许可，本仓库只转换连线数据，不复制 artwork）。这类限定词
由 ``normalize_license()`` 剥离成规范许可再判白名单；**白名单本身不放宽**，
``CC BY-SA 2.0`` / ``3.0`` / 无版本号仍然拒绝。原始字符串由生成脚本写入
``meta.source_license_raw``，``meta.source_license`` 只放规范值。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import cached_property, lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from .constellations import (
    SYSTEM_CHINESE,
    SYSTEM_IAU,
    _sha256_hex_ok,
    load_iau_constellations,
    payload_sha256,
)
from .xingguan import load_xingguan, load_xingguan_star_names

__all__ = [
    "ALLOWED_LICENSES",
    "LICENSE_URLS",
    "SkyCultureMetadataError",
    "SkyCultureIntegrityError",
    "SkyCultureGroup",
    "SkyCulturePack",
    "SkyCultureRegistry",
    "load_pack",
    "load_registry",
    "get_pack",
    "list_packs",
    "segments_for",
    "names_for_hip",
    "cultures_of_hip",
    "split_culture_ids",
    "split_license",
    "normalize_license",
    "stats",
    "pack_from_iau",
    "pack_from_xingguan",
    "license_url_for",
]

SCHEMA = "mbdsdr.sky_culture.v1"

#: 只允许可再分发/可改写的许可；CC BY-ND、CC BY-NC*、GPL 一律拒绝。
ALLOWED_LICENSES = (
    "CC BY-SA 4.0",
    "CC BY 4.0",
    "CC0 / Public Domain",
)

LICENSE_URLS = {
    "CC BY-SA 4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC0 / Public Domain": "https://creativecommons.org/publicdomain/zero/1.0/",
}

_GROUP_KINDS = ("constellation", "asterism", "xingguan")

#: 上游 ``## License`` 的作用域限定词前缀，如 ``Text and data:``。
#: 只剥离这一种前缀（大小写与空白容错），不做任何许可版本/写法的归一，
#: 2.0 / 3.0 / 无版本号在剥离后仍不在白名单，照样拒绝。
_LICENSE_QUALIFIER_RE = re.compile(
    r"^\s*text\s+and\s+data\s*:\s*", re.IGNORECASE)

_REQUIRED_META_KEYS = (
    "schema", "source", "source_url", "source_license",
    "source_license_url", "attribution", "source_file_sha256",
    "data_sha256",
)

_DATA_DIR = Path(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))) / "data" / "skycultures"


class SkyCultureMetadataError(ValueError):
    """文化包 meta 缺字段、许可不允许或字段格式不合法。"""


class SkyCultureIntegrityError(ValueError):
    """文化包内容与 meta.data_sha256 不一致（疑似手改数据）。"""


def split_license(license_text: Any) -> Tuple[str, str]:
    """返回 ``(原始字符串, 剥离限定词后的规范许可)``。

    只剥离 ``Text and data:`` 这类作用域前缀，不猜、不补版本号：
    ``CC BY-SA 3.0 Unported`` 剥离后仍是 ``CC BY-SA 3.0 Unported``。
    """
    raw = str(license_text or "").strip()
    return raw, _LICENSE_QUALIFIER_RE.sub("", raw).strip()


def normalize_license(license_text: Any) -> str:
    """规范许可值（去掉 ``Text and data:`` 前缀），供白名单判定与 URL 映射。"""
    return split_license(license_text)[1]


def license_url_for(license_text: str) -> str:
    canonical = normalize_license(license_text)
    try:
        return LICENSE_URLS[canonical]
    except KeyError:
        raise SkyCultureMetadataError(
            f"许可证 {canonical!r} 不在白名单 {list(ALLOWED_LICENSES)}"
        ) from None


def _check_license(license_text: Any) -> str:
    raw, text = split_license(license_text)
    if text not in ALLOWED_LICENSES:
        raise SkyCultureMetadataError(
            f"许可证 {raw!r} 不在白名单 {list(ALLOWED_LICENSES)}")
    return text


@dataclass(frozen=True)
class SkyCultureGroup:
    """一个星座/星群/星官：名称 + HIP 折线（每段是 HIP 元组）。

    ``region`` / ``region_type`` 只用于保留中国星官的区域/垣宿归属，
    其他文化为 ``None``。
    """
    group_id: str
    names: Dict[str, str]
    lines: Tuple[Tuple[int, ...], ...]
    kind: str
    region: Optional[str] = None
    region_type: Optional[str] = None

    def segments(self) -> Tuple[Tuple[int, int], ...]:
        out: List[Tuple[int, int]] = []
        for poly in self.lines:
            out.extend(zip(poly, poly[1:]))
        return tuple(out)


@dataclass(frozen=True)
class SkyCulturePack:
    """一套天空文化的只读包；名称/连线按文化隔离，禁止跨文化合并。"""
    id: str
    display_name: str
    native_name: str
    region: str
    classification: Tuple[str, ...]
    license: str
    license_url: str
    source: str
    source_url: str
    attribution: str
    groups: Tuple[SkyCultureGroup, ...]
    names: Dict[int, Dict[str, str]]
    unresolved_hips: Tuple[int, ...]
    meta: Dict[str, Any]

    @cached_property
    def edges(self) -> frozenset:
        """去重后的无向 HIP 边集合；同一段折线的相邻点各算一条边。"""
        out = set()
        for group in self.groups:
            for a, b in group.segments():
                out.add((a, b) if a <= b else (b, a))
        return frozenset(out)

    def group_by_id(self, group_id: str) -> Optional[SkyCultureGroup]:
        for group in self.groups:
            if group.group_id == group_id:
                return group
        return None


class SkyCultureRegistry:
    """统一注册表视图：文件包 + IAU/中国星官适配器。"""

    def __init__(self, packs: Dict[str, SkyCulturePack]):
        self._packs: Dict[str, SkyCulturePack] = dict(packs)

    def __len__(self) -> int:
        return len(self._packs)

    def __iter__(self):
        for pack_id in sorted(self._packs):
            yield self._packs[pack_id]

    def __getitem__(self, pack_id: str) -> SkyCulturePack:
        return self._packs[pack_id]

    def get(self, pack_id: str) -> Optional[SkyCulturePack]:
        return self._packs.get(pack_id)

    def ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._packs))

    def list_packs(self) -> Tuple[str, ...]:
        return self.ids()

    def stats(self) -> Dict[str, Any]:
        per: Dict[str, Dict[str, int]] = {}
        unresolved = set()
        for pack_id in self.ids():
            pack = self._packs[pack_id]
            per[pack_id] = {
                "groups": len(pack.groups),
                "edges": len(pack.edges),
                "names": len(pack.names),
                "unresolved_hips": len(pack.unresolved_hips),
            }
            unresolved.update(pack.unresolved_hips)
        return {
            "pack_count": len(self._packs),
            "group_count": sum(v["groups"] for v in per.values()),
            "edge_count": sum(v["edges"] for v in per.values()),
            "name_count": sum(v["names"] for v in per.values()),
            "unresolved_hip_count": len(unresolved),
            "packs": per,
        }


# ---------------------------------------------------------------------------
# 文件包加载
# ---------------------------------------------------------------------------
def _parse_group(raw: Any) -> SkyCultureGroup:
    if not isinstance(raw, dict):
        raise SkyCultureMetadataError("group 不是 JSON 对象")
    group_id = str(raw.get("group_id") or raw.get("id") or "").strip()
    if not group_id:
        raise SkyCultureMetadataError("group 缺少 group_id")
    names = raw.get("names")
    if not isinstance(names, dict):
        raise SkyCultureMetadataError(f"{group_id}: names 不是对象")
    clean_names = {str(k): str(v) for k, v in names.items() if str(v).strip()}
    kind = str(raw.get("kind") or "constellation")
    if kind not in _GROUP_KINDS:
        raise SkyCultureMetadataError(
            f"{group_id}: 未知 kind {kind!r}，只允许 {list(_GROUP_KINDS)}")
    lines_raw = raw.get("lines") or []
    if not isinstance(lines_raw, list):
        raise SkyCultureMetadataError(f"{group_id}: lines 不是数组")
    lines: List[Tuple[int, ...]] = []
    for poly in lines_raw:
        if not isinstance(poly, (list, tuple)) or not poly:
            raise SkyCultureMetadataError(f"{group_id}: 线段为空或格式错误")
        hips: List[int] = []
        for value in poly:
            if isinstance(value, bool):
                raise SkyCultureMetadataError(
                    f"{group_id}: HIP 不能是布尔值")
            try:
                hips.append(int(value))
            except (TypeError, ValueError):
                raise SkyCultureMetadataError(
                    f"{group_id}: HIP 不是整数: {value!r}") from None
        lines.append(tuple(hips))
    region = raw.get("region")
    region_type = raw.get("region_type")
    return SkyCultureGroup(
        group_id=group_id, names=clean_names, lines=tuple(lines), kind=kind,
        region=(str(region) if region else None),
        region_type=(str(region_type) if region_type else None),
    )


def _parse_names(raw: Any) -> Dict[int, Dict[str, str]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise SkyCultureMetadataError("names 不是以 HIP 为键的对象")
    out: Dict[int, Dict[str, str]] = {}
    for key, value in raw.items():
        try:
            hip = int(key)
        except (TypeError, ValueError):
            raise SkyCultureMetadataError(f"names 的键不是 HIP: {key!r}") from None
        if not isinstance(value, dict):
            raise SkyCultureMetadataError(f"names[{hip}] 不是对象")
        entry = {str(k): str(v) for k, v in value.items() if str(v).strip()}
        if entry:
            out[hip] = entry
    return out


def _validate_meta(meta: Any) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        raise SkyCultureMetadataError("数据 meta 不是 JSON 对象")
    missing = [k for k in _REQUIRED_META_KEYS if not meta.get(k)]
    if missing:
        raise SkyCultureMetadataError(f"数据 meta 缺少必需字段: {missing}")
    if str(meta["schema"]) != SCHEMA:
        raise SkyCultureMetadataError(
            f"不支持的 schema: {meta['schema']!r}，期望 {SCHEMA!r}")
    for key in ("source_url", "source_license_url"):
        if not str(meta[key]).startswith(("https://", "http://")):
            raise SkyCultureMetadataError(f"meta.{key} 不是有效 URL")
    if not _sha256_hex_ok(meta.get("source_file_sha256")):
        raise SkyCultureMetadataError(
            "meta.source_file_sha256 不是 64 位 SHA-256")
    if not _sha256_hex_ok(meta.get("data_sha256")):
        raise SkyCultureMetadataError("meta.data_sha256 不是 64 位 SHA-256")
    canonical = _check_license(meta.get("source_license"))
    raw_license = meta.get("source_license_raw")
    if raw_license is not None:
        # 原始串只用于如实保留上游写法；它剥离限定词后必须回到同一个
        # 规范值，否则就是 meta 自相矛盾，不能靠其中一半蒙混过关。
        if not isinstance(raw_license, str) or not raw_license.strip():
            raise SkyCultureMetadataError(
                "meta.source_license_raw 不是非空字符串")
        if normalize_license(raw_license) != canonical:
            raise SkyCultureMetadataError(
                f"meta.source_license_raw {raw_license!r} 与 "
                f"source_license {canonical!r} 不一致")
    for key in ("display_name", "native_name", "region", "classification"):
        if key in meta and not isinstance(meta[key], (str, list, tuple)):
            raise SkyCultureMetadataError(f"meta.{key} 类型不合法")
    return meta


def load_pack(path: Union[str, os.PathLike]) -> SkyCulturePack:
    """加载单个文化包；meta/许可/SHA 不合法时抛错，不静默跳过。"""
    file_path = Path(path)
    try:
        with open(file_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except OSError:
        raise
    except ValueError as exc:
        raise SkyCultureMetadataError(
            f"文化包不是合法 JSON: {file_path.name}") from exc
    if not isinstance(payload, dict):
        raise SkyCultureMetadataError(f"{file_path.name}: 顶层不是 JSON 对象")
    meta = _validate_meta(payload.get("meta"))
    if payload_sha256(payload) != meta["data_sha256"]:
        raise SkyCultureIntegrityError(
            f"{file_path.name}: 数据 SHA-256 与 meta.data_sha256 不一致")

    file_id = str(payload.get("id") or meta.get("id") or "").strip()
    meta_id = str(meta.get("id") or "").strip()
    if file_id and meta_id and file_id != meta_id:
        raise SkyCultureMetadataError(
            f"{file_path.name}: payload.id 与 meta.id 不一致")
    pack_id = file_id or meta_id or file_path.stem

    groups_raw = payload.get("groups")
    if groups_raw is None:
        groups_raw = []
    if not isinstance(groups_raw, list):
        raise SkyCultureMetadataError(f"{pack_id}: groups 不是数组")
    groups = tuple(_parse_group(g) for g in groups_raw)
    names = _parse_names(payload.get("names"))
    unresolved_raw = payload.get("unresolved_hips") or meta.get(
        "unresolved_hips") or []
    if not isinstance(unresolved_raw, (list, tuple)):
        raise SkyCultureMetadataError(f"{pack_id}: unresolved_hips 不是数组")
    unresolved = tuple(sorted({int(h) for h in unresolved_raw}))

    classification = meta.get("classification")
    if isinstance(classification, str):
        classification_tuple = (classification,)
    elif isinstance(classification, (list, tuple)):
        classification_tuple = tuple(str(x) for x in classification)
    else:
        classification_tuple = ()

    return SkyCulturePack(
        id=pack_id,
        display_name=str(meta.get("display_name") or pack_id),
        native_name=str(meta.get("native_name") or ""),
        region=str(meta.get("region") or ""),
        classification=classification_tuple,
        license=normalize_license(meta["source_license"]),
        license_url=str(meta["source_license_url"]),
        source=str(meta["source"]),
        source_url=str(meta["source_url"]),
        attribution=str(meta["attribution"]),
        groups=groups,
        names=names,
        unresolved_hips=unresolved,
        meta=dict(meta),
    )


# ---------------------------------------------------------------------------
# 现有两套体系 → 统一模型（不复制数据）
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def pack_from_iau() -> SkyCulturePack:
    """IAU 88 座适配包；连线/名称直接取自 ``load_iau_constellations()``。"""
    ds = load_iau_constellations()
    groups: List[SkyCultureGroup] = []
    names: Dict[int, Dict[str, str]] = {}
    unresolved: set = set()
    for con in ds:
        lines = tuple(tuple(int(s.hip) for s in poly) for poly in con.lines)
        groups.append(SkyCultureGroup(
            group_id=con.abbr,
            names={"zh": con.zh_name, "la": con.latin_name,
                   "en": con.english_name},
            lines=lines,
            kind="constellation",
        ))
        unresolved.update(int(h) for h in con.unresolved_hips)
        for star in con.stars:
            label = star.proper or star.bayer
            if label and int(star.hip) not in names:
                names[int(star.hip)] = {"en": label}
    meta = dict(ds.meta)
    return SkyCulturePack(
        id=SYSTEM_IAU,
        display_name=str(meta.get("system_name_en") or "IAU 88 Constellations"),
        native_name=str(meta.get("system_name_zh") or "IAU 88 星座"),
        region="World",
        classification=("modern", "astronomical"),
        license=str(meta.get("source_license", "")),
        license_url=str(meta.get("source_license_url", "")),
        source=str(meta.get("source", "")),
        source_url=str(meta.get("source_url", "")),
        attribution=str(meta.get("attribution", "")),
        groups=tuple(groups),
        names=names,
        unresolved_hips=tuple(sorted(unresolved)),
        meta=meta,
    )


@lru_cache(maxsize=1)
def pack_from_xingguan() -> SkyCulturePack:
    """中国星官适配包：31 个区域（三垣二十八宿），kind 统一为 xingguan。"""
    ds = load_xingguan()
    star_names = load_xingguan_star_names()
    groups: List[SkyCultureGroup] = []
    unresolved: set = set()
    for region in ds:
        lines = tuple((int(a.hip), int(b.hip)) for a, b in region.all_segments())
        groups.append(SkyCultureGroup(
            group_id=region.key,
            names={"zh": region.name_zh, "en": region.name_en,
                   "pinyin": region.pinyin},
            lines=lines,
            kind="xingguan",
            region=region.key,
            region_type=region.type,
        ))
        unresolved.update(int(h) for h in region.unmapped_hips)
    names = {
        int(item.hip): {"zh": item.zh_name}
        for item in star_names.by_hip.values() if item.zh_name
    }
    meta = dict(ds.meta)
    return SkyCulturePack(
        id=SYSTEM_CHINESE,
        display_name=str(meta.get("system_name_en") or "Chinese Xingguan"),
        native_name=str(meta.get("system_name_zh")
                        or "中国星官（三垣二十八宿）"),
        region="Eastern Asia",
        classification=("historical", "traditional"),
        license=str(meta.get("source_license", "")),
        license_url=str(meta.get("source_license_url", "")),
        source=str(meta.get("source", "")),
        source_url=str(meta.get("source_url", "")),
        attribution=str(meta.get("attribution", "")),
        groups=tuple(groups),
        names=names,
        unresolved_hips=tuple(sorted(unresolved)),
        meta=meta,
    )


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------
@lru_cache(maxsize=8)
def load_registry(directory: Optional[str] = None) -> SkyCultureRegistry:
    """加载 ``data/skycultures/*.json`` + 两个适配器；坏包直接报错。"""
    root = Path(directory) if directory else _DATA_DIR
    packs: Dict[str, SkyCulturePack] = {}
    for adapter in (pack_from_iau(), pack_from_xingguan()):
        packs[adapter.id] = adapter
    if root.is_dir():
        for path in sorted(root.glob("*.json")):
            try:
                pack = load_pack(path)
            except (SkyCultureMetadataError, SkyCultureIntegrityError) as exc:
                raise type(exc)(f"{path.name}: {exc}") from exc
            if pack.id in packs:
                raise SkyCultureMetadataError(
                    f"{path.name}: 文化包 id {pack.id!r} 与内置体系冲突")
            packs[pack.id] = pack
    return SkyCultureRegistry(packs)


def get_pack(pack_id: str) -> SkyCulturePack:
    pack = load_registry().get(str(pack_id))
    if pack is None:
        raise KeyError(f"未知天空文化包: {pack_id!r}")
    return pack


def list_packs() -> Tuple[str, ...]:
    return load_registry().ids()


def segments_for(
        ids: Union[str, Iterable[str]]) -> Dict[str, Tuple[Tuple[int, int], ...]]:
    """按包 id 返回各自连线；文化之间不合并、不交叉去重。"""
    if isinstance(ids, str):
        ids = (ids,)
    registry = load_registry()
    out: Dict[str, Tuple[Tuple[int, int], ...]] = {}
    for pack_id in ids:
        pack = registry.get(str(pack_id))
        if pack is None:
            raise KeyError(f"未知天空文化包: {pack_id!r}")
        out[pack.id] = tuple(
            seg for group in pack.groups for seg in group.segments())
    return out


def names_for_hip(hip: int) -> Dict[str, Dict[str, str]]:
    """同一 HIP 在各文化下的名字，按包 id 分开返回，绝不合并成一个名字。"""
    hip = int(hip)
    out: Dict[str, Dict[str, str]] = {}
    for pack in load_registry():
        entry = pack.names.get(hip)
        if entry:
            out[pack.id] = dict(entry)
    return out


def cultures_of_hip(hip: int) -> Tuple[str, ...]:
    """该 HIP 出现在哪些文化的连线或名称里（按包 id 排序）。"""
    hip = int(hip)
    hits: List[str] = []
    for pack in load_registry():
        if hip in pack.names:
            hits.append(pack.id)
            continue
        if any(hip in (a, b) for a, b in pack.edges):
            hits.append(pack.id)
    return tuple(hits)


def split_culture_ids(
        culture_ids: Union[str, Iterable[str], None]
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """把调用方给的文化 id 序列拆成 ``(已知, 未知)``，保持请求顺序并去重。

    ``None`` → 两个空元组（调用方据此保持默认行为）。未知 id **不抛错**，
    交由调用方在结果里如实列出——既不静默吞掉，也不替用户猜一个近似文化。
    """
    if culture_ids is None:
        return (), ()
    if isinstance(culture_ids, str):
        requested = (culture_ids,)
    elif isinstance(culture_ids, Iterable):
        requested = tuple(culture_ids)
    else:
        raise ValueError(
            "culture_ids 必须是文化 id 字符串或其序列，"
            f"收到 {type(culture_ids).__name__}")

    known_ids = set(load_registry().ids())
    known: List[str] = []
    unknown: List[str] = []
    for raw in requested:
        cid = str(raw)
        if cid in known_ids:
            if cid not in known:
                known.append(cid)
        elif cid not in unknown:
            unknown.append(cid)
    return tuple(known), tuple(unknown)


def stats() -> Dict[str, Any]:
    return load_registry().stats()
