# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/astronomy/dso.py — 深空天体 (DSO) 目录
==================================================

数据来自 ``data/dso_catalog.json``（由 ``build_dso_catalog.py`` 生成）：

- Messier 1–110 全量 + 7 个常用亮 NGC；
- 坐标/星等/角径/类型取自 Stellarium ``nebulae/default/catalog.txt``；
- 英文名取自同目录 ``names.dat``，中文名取自
  ``po/stellarium-sky/zh_CN.po`` 对 ``names.dat`` 的翻译；
- 星座归属由 Stellarium ``data/constellations_spans.dat``（ADC VI/42）
  的 IAU spans 查表得到；
- 取不到名字的条目留空并计数，不用英文名冒充中文名，不凭记忆补条目。

DSO 渲染类型：``galaxy`` / ``nebula`` / ``cluster`` /
``supernova_remnant`` / ``cluster_nebula`` / ``double_star``。
原始目录类型保留在 ``catalog_type``。

我们的增强：:func:`recommend_dso` 按当前时刻/观测者位置推荐亮 DSO。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

__all__ = [
    "DSOEntry", "DSOCatalog", "BUILTIN_CATALOG", "recommend_dso",
    "DSOMetadataError", "DSOIntegrityError", "load_dso_catalog",
    "DEFAULT_DSO_PATH",
]

DEFAULT_DSO_PATH = Path(
    Path(__file__).resolve().parent.parent / "data" / "dso_catalog.json")

_REQUIRED_META_KEYS = (
    "schema", "catalog", "source", "source_url", "source_license",
    "source_license_url", "attribution", "source_files",
    "constellation_method", "name_policy", "type_mapping", "counts",
    "data_sha256",
)
_REQUIRED_SOURCE_FILES = ("catalog", "names", "zh_translations",
                          "constellation_spans")
_RENDER_TYPES = {
    "galaxy", "nebula", "cluster", "supernova_remnant",
    "cluster_nebula", "double_star",
}
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_NAME_RE = re.compile(r"^(M[1-9]\d{0,2}|NGC[1-9]\d*|IC[1-9]\d*)$")
_CON_RE = re.compile(r"^[A-Z][A-Za-z]{2}$")


class DSOMetadataError(ValueError):
    """DSO 数据 meta 缺字段、URL/SHA 不合法或来源不可追溯。"""


class DSOIntegrityError(ValueError):
    """DSO 数据内容与 meta 不一致（重复编号、越界、缺星等、SHA 篡改）。"""


@dataclass(frozen=True)
class DSOEntry:
    name: str
    dso_type: str
    ra_hours: float
    dec_deg: float
    magnitude: float
    size_arcmin: float
    constellation: str = ""
    name_en: str = ""
    zh_name: str = ""
    aliases_en: Tuple[str, ...] = ()
    aliases_zh: Tuple[str, ...] = ()
    catalog_type: str = ""
    size_minor_arcmin: Optional[float] = None
    cross_ids: Tuple[Tuple[str, int], ...] = ()
    source_row: int = 0

    @property
    def ra_deg(self) -> float:
        return self.ra_hours * 15.0

    @property
    def display_name(self) -> str:
        """优先中文名，其次英文名，最后退回编号；空值不拼接。"""
        return self.zh_name or self.name_en or self.name

    @property
    def label(self) -> str:
        return self.display_name


def _sha256_hex_ok(value: object) -> bool:
    text = str(value)
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def _payload_sha256(payload: dict) -> str:
    clone = json.loads(json.dumps(payload, ensure_ascii=False))
    if isinstance(clone.get("meta"), dict):
        clone["meta"].pop("data_sha256", None)
    blob = json.dumps(clone, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _check_meta(payload: dict) -> dict:
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        raise DSOMetadataError("DSO 数据 meta 不是 JSON 对象")
    missing = [k for k in _REQUIRED_META_KEYS if not meta.get(k)]
    if missing:
        raise DSOMetadataError(f"DSO 数据 meta 缺少必需字段: {missing}")
    if meta["schema"] != "mbdsdr.dso_catalog.v1":
        raise DSOMetadataError(f"DSO 数据 schema 不匹配: {meta['schema']!r}")
    for key in ("source_url", "source_license_url"):
        if not str(meta[key]).startswith(("https://", "http://")):
            raise DSOMetadataError(f"meta.{key} 不是有效 URL")
    sources = meta.get("source_files")
    if not isinstance(sources, dict):
        raise DSOMetadataError("meta.source_files 不是对象")
    missing_sources = [k for k in _REQUIRED_SOURCE_FILES if k not in sources]
    if missing_sources:
        raise DSOMetadataError(
            f"meta.source_files 缺少源文件: {missing_sources}")
    for key, info in sources.items():
        if not isinstance(info, dict) or not info.get("path"):
            raise DSOMetadataError(f"meta.source_files.{key} 缺少 path")
        if not _sha256_hex_ok(info.get("sha256")):
            raise DSOMetadataError(
                f"meta.source_files.{key}.sha256 不是 64 位 SHA-256")
    if not _sha256_hex_ok(meta.get("data_sha256")):
        raise DSOMetadataError("meta.data_sha256 不是 64 位 SHA-256")
    if _payload_sha256(payload) != meta["data_sha256"]:
        raise DSOIntegrityError(
            "DSO 数据 SHA-256 与 meta.data_sha256 不一致（疑似手改数据）")
    return meta


def _check_entry(rec: dict, meta: dict) -> None:
    name = rec.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise DSOIntegrityError(f"非法 DSO 编号: {name!r}")
    ra = float(rec["ra_deg"])
    dec = float(rec["dec_deg"])
    if not (0.0 <= ra < 360.0):
        raise DSOIntegrityError(f"{name} RA 越界: {ra}")
    if not (-90.0 <= dec <= 90.0):
        raise DSOIntegrityError(f"{name} Dec 越界: {dec}")
    try:
        mag = float(rec["magnitude"])
    except (TypeError, ValueError) as exc:
        raise DSOIntegrityError(f"{name} 星等缺失") from exc
    if not math.isfinite(mag) or not (0.0 < mag < 90.0):
        # 99 是源目录的缺失哨兵；0/负值同样视为缺失，绝不静默丢条目。
        raise DSOIntegrityError(f"{name} 星等缺失或非法: {mag}")
    size = float(rec["size_arcmin"])
    if not math.isfinite(size) or size < 0.0:
        raise DSOIntegrityError(f"{name} 视尺寸非法: {size}")
    minor = rec.get("size_minor_arcmin")
    if minor is not None and (not math.isfinite(float(minor)) or float(minor) < 0):
        raise DSOIntegrityError(f"{name} 短轴尺寸非法: {minor}")
    catalog_type = rec.get("catalog_type")
    expected_render = (meta.get("type_mapping") or {}).get(catalog_type)
    if expected_render != rec.get("dso_type") or expected_render not in _RENDER_TYPES:
        raise DSOIntegrityError(
            f"{name} 类型映射不一致: {catalog_type!r} -> {rec.get('dso_type')!r}")
    con = rec.get("constellation")
    if not isinstance(con, str) or not _CON_RE.match(con):
        raise DSOIntegrityError(f"{name} 星座缩写非法: {con!r}")
    name_en = rec.get("name_en") or ""
    zh_name = rec.get("zh_name") or ""
    if zh_name and not _CJK_RE.search(zh_name):
        raise DSOIntegrityError(f"{name} 中文名不含汉字: {zh_name!r}")
    if zh_name and zh_name == name_en:
        raise DSOIntegrityError(f"{name} 中文名与英文名相同: {zh_name!r}")
    for field in ("aliases_en", "aliases_zh"):
        if not isinstance(rec.get(field), list):
            raise DSOIntegrityError(f"{name} {field} 不是列表")
    cross = rec.get("cross_ids")
    if not isinstance(cross, dict) or set(cross) - {"ngc", "ic", "m"}:
        raise DSOIntegrityError(f"{name} cross_ids 非法: {cross!r}")
    for key, value in cross.items():
        if value is not None and (not isinstance(value, int) or value <= 0):
            raise DSOIntegrityError(f"{name} cross_ids.{key} 非法: {value!r}")
    if name.startswith("M") and cross.get("m") != int(name[1:]):
        raise DSOIntegrityError(f"{name} 与 cross_ids.m 不一致")
    if name.startswith("NGC") and cross.get("ngc") != int(name[3:]):
        raise DSOIntegrityError(f"{name} 与 cross_ids.ngc 不一致")
    if not isinstance(rec.get("source_row"), int) or rec["source_row"] <= 0:
        raise DSOIntegrityError(f"{name} source_row 非法")


def _check_counts(objects: List[dict], meta: dict) -> None:
    counts = meta.get("counts")
    if not isinstance(counts, dict):
        raise DSOMetadataError("meta.counts 不是对象")
    total = len(objects)
    messier = [o for o in objects if o["name"].startswith("M")]
    ngc = [o for o in objects if o["name"].startswith("NGC")]
    without_en = [o["name"] for o in objects if not o.get("name_en")]
    without_zh = [o["name"] for o in objects if not o.get("zh_name")]
    raw: Dict[str, int] = {}
    render: Dict[str, int] = {}
    for obj in objects:
        raw[obj["catalog_type"]] = raw.get(obj["catalog_type"], 0) + 1
        render[obj["dso_type"]] = render.get(obj["dso_type"], 0) + 1
    expected = {
        "total": total,
        "messier": 110,
        "extra_ngc": len(ngc),
        "with_name_en": total - len(without_en),
        "without_name_en": len(without_en),
        "with_zh_name": total - len(without_zh),
        "without_zh_name": len(without_zh),
        "no_name_designations": without_en,
        "no_zh_name_designations": without_zh,
        "raw_type_counts": dict(sorted(raw.items())),
        "render_type_counts": dict(sorted(render.items())),
    }
    for key, value in expected.items():
        if counts.get(key) != value:
            raise DSOIntegrityError(
                f"meta.counts.{key} 与数据不一致: {counts.get(key)!r} != {value!r}")
    if len(messier) != 110:
        raise DSOIntegrityError(f"Messier 条数不是 110: {len(messier)}")
    numbers = sorted(int(o["name"][1:]) for o in messier)
    if numbers != list(range(1, 111)):
        raise DSOIntegrityError("Messier 编号缺号或重复")
    if len({o["source_row"] for o in objects}) != total:
        raise DSOIntegrityError("DSO source_row 重复，疑似同一条目被复制")


def _load_payload(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        raise
    except ValueError as exc:
        raise DSOMetadataError(f"DSO 数据不是合法 JSON: {path}") from exc
    meta = _check_meta(payload)
    objects = payload.get("objects")
    if not isinstance(objects, list) or not objects:
        raise DSOIntegrityError("DSO 数据 objects 为空")
    seen: set = set()
    for rec in objects:
        _check_entry(rec, meta)
        if rec["name"] in seen:
            raise DSOIntegrityError(f"DSO 编号重复: {rec['name']}")
        seen.add(rec["name"])
    _check_counts(objects, meta)
    return payload


def _entry_from_record(rec: dict) -> DSOEntry:
    cross = tuple((k, v) for k, v in rec["cross_ids"].items() if v is not None)
    return DSOEntry(
        name=rec["name"],
        dso_type=rec["dso_type"],
        ra_hours=float(rec["ra_deg"]) / 15.0,
        dec_deg=float(rec["dec_deg"]),
        magnitude=float(rec["magnitude"]),
        size_arcmin=float(rec["size_arcmin"]),
        constellation=rec["constellation"],
        name_en=rec.get("name_en") or "",
        zh_name=rec.get("zh_name") or "",
        aliases_en=tuple(rec.get("aliases_en") or ()),
        aliases_zh=tuple(rec.get("aliases_zh") or ()),
        catalog_type=rec.get("catalog_type") or "",
        size_minor_arcmin=(None if rec.get("size_minor_arcmin") is None
                           else float(rec["size_minor_arcmin"])),
        cross_ids=cross,
        source_row=int(rec.get("source_row") or 0),
    )


class DSOCatalog:
    def __init__(self, entries: Optional[List[DSOEntry]] = None,
                 meta: Optional[dict] = None, path: Optional[str] = None):
        self._entries: List[DSOEntry] = list(entries or [])
        self._by_name: Dict[str, DSOEntry] = {}
        for entry in self._entries:
            key = entry.name.upper().replace(" ", "")
            if key in self._by_name:
                raise DSOIntegrityError(f"DSO 编号重复: {entry.name}")
            self._by_name[key] = entry
        self.meta: Dict = dict(meta or {})
        self.path: Optional[str] = path

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterator[DSOEntry]:
        return iter(self._entries)

    def get(self, name: str) -> Optional[DSOEntry]:
        """按编号查询；允许 ``M 31``/``ngc 253``，未知编号返回 None。"""
        key = str(name).strip().upper().replace(" ", "")
        return self._by_name.get(key)

    def filter_type(self, dso_type: str) -> List[DSOEntry]:
        return [e for e in self._entries if e.dso_type == dso_type]

    def near(self, ra_deg: float, dec_deg: float, radius_deg: float) -> List[DSOEntry]:
        out = []
        for e in self._entries:
            dra = math.radians((e.ra_deg - ra_deg) * math.cos(math.radians(dec_deg)))
            ddec = math.radians(e.dec_deg - dec_deg)
            if math.degrees(math.sqrt(dra * dra + ddec * ddec)) <= radius_deg:
                out.append(e)
        return out

    @classmethod
    def builtin(cls) -> "DSOCatalog":
        return load_dso_catalog()


@lru_cache(maxsize=1)
def load_dso_catalog(path: Optional[str] = None) -> DSOCatalog:
    """加载并校验 DSO 目录；meta/SHA/计数/字段不合法时抛错，不静默降级。"""
    source = Path(path) if path else DEFAULT_DSO_PATH
    payload = _load_payload(source)
    entries = [_entry_from_record(rec) for rec in payload["objects"]]
    return DSOCatalog(entries, meta=payload["meta"], path=str(source))


BUILTIN_CATALOG = DSOCatalog.builtin()


def recommend_dso(jd: float, observer_lat_deg: float,
                  catalog: Optional[DSOCatalog] = None,
                  min_alt_deg: float = 20.0,
                  max_magnitude: float = 9.0,
                  max_sun_dist_deg: float = 90.0) -> List[DSOEntry]:
    cat = catalog or BUILTIN_CATALOG
    T = (jd - 2451545.0) / 36525.0
    sun_lon = 280.46646 + 36000.76983 * T
    from .planets import _ecliptic_to_equatorial
    sun_ra, sun_dec = _ecliptic_to_equatorial(sun_lon, 0.0)
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0

    out: List[DSOEntry] = []
    for e in cat:
        if e.magnitude > max_magnitude:
            continue
        ha = math.radians((gmst - e.ra_deg) % 360.0)
        lat = math.radians(observer_lat_deg)
        dec = math.radians(e.dec_deg)
        sin_alt = math.sin(lat) * math.sin(dec) + math.cos(lat) * math.cos(dec) * math.cos(ha)
        alt = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
        if alt < min_alt_deg:
            continue
        cos_sep = (math.sin(dec) * math.sin(math.radians(sun_dec))
                   + math.cos(dec) * math.cos(math.radians(sun_dec))
                   * math.cos(math.radians(e.ra_deg - sun_ra)))
        sep = math.degrees(math.acos(max(-1.0, min(1.0, cos_sep))))
        if sep < max_sun_dist_deg:
            continue
        out.append(e)
    out.sort(key=lambda x: x.magnitude)
    return out
