# SPDX-License-Identifier: MIT
"""
mbdsdr_ai/astronomy/dso.py — 深空天体 (DSO) 目录
==================================================

数据来自 ``data/dso_catalog.json``（由 ``build_dso_catalog.py`` 从 OpenNGC 生成）：

- Messier 1–110 全量 + 7 个常用亮 NGC；
- 坐标/星等/角径/类型/星座取自 OpenNGC
  ``NGC.csv`` + ``addendum.csv``（CC BY-SA 4.0）；
- 英文常用名取自 OpenNGC ``Common names`` 列；中文名取自 Wikidata
  （CC0 1.0）标签，取不到且可意译者自译并逐条标注
  ``zh_name_source="自译"``；仍取不到留空并计数；
- M102 按历史约定固定映射 NGC 5866（OpenNGC 记为 Dup，M=101），
  故 Messier 仍为 110 条、总数仍为 117 条；
- 源目录缺失的星等/角径写 ``None``（未知），绝不用 0/99 冒充。

旧版 ``a3aadd8`` 及更早的 ``dso_catalog.json`` 由 Stellarium
``catalog.txt``/``zh_CN.po`` 生成，含 GPL-2.0-or-later 来源数据，已废弃。
当前数据不含任何 GPL 来源。

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
    "source_license_url", "attribution", "source_dir", "source_files",
    "constellation_method", "name_policy", "zh_name_policy", "type_mapping",
    "counts", "missing", "m102_convention", "generated_at", "reproduction",
    "data_sha256",
)
_REQUIRED_SOURCE_FILES = ("ngc", "addendum", "zh_labels")
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
    magnitude: Optional[float]
    size_arcmin: Optional[float]
    constellation: str = ""
    name_en: str = ""
    zh_name: str = ""
    aliases_en: Tuple[str, ...] = ()
    aliases_zh: Tuple[str, ...] = ()
    catalog_type: str = ""
    size_minor_arcmin: Optional[float] = None
    axis_ratio: Optional[float] = None
    axis_ratio_source: str = "unknown"
    cross_ids: Tuple[Tuple[str, int], ...] = ()
    source_row: int = 0
    source_file: str = ""
    source_name: str = ""
    magnitude_band: str = ""
    magnitude_source: str = "none"
    size_source: str = "none"
    zh_name_source: str = ""
    zh_name_ref: str = ""

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
    if meta["schema"] != "mbdsdr.dso_catalog.v2":
        raise DSOMetadataError(f"DSO 数据 schema 不匹配: {meta['schema']!r}")
    if meta.get("source") != "OpenNGC":
        raise DSOMetadataError(f"DSO 数据 source 不是 OpenNGC: {meta.get('source')!r}")
    if meta.get("source_license") != "CC-BY-SA-4.0":
        raise DSOMetadataError(
            f"DSO 数据许可不是 CC-BY-SA-4.0: {meta.get('source_license')!r}")
    if "GPL" in json.dumps(meta, ensure_ascii=False).upper():
        raise DSOMetadataError("DSO meta 不得再包含 GPL 来源声明")
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
    mag = rec.get("magnitude")
    band = rec.get("magnitude_band") or ""
    if mag is None:
        if band:
            raise DSOIntegrityError(
                f"{name} 星等未知却声明了波段 {band!r}")
    else:
        try:
            mag = float(mag)
        except (TypeError, ValueError) as exc:
            raise DSOIntegrityError(f"{name} 星等非法") from exc
        if not math.isfinite(mag) or not (0.0 < mag < 90.0):
            raise DSOIntegrityError(f"{name} 星等非法: {mag}")
        if band not in ("V", "B"):
            raise DSOIntegrityError(f"{name} 星等波段非法: {band!r}")
    size = rec.get("size_arcmin")
    if size is not None:
        size = float(size)
        if not math.isfinite(size) or size <= 0.0:
            raise DSOIntegrityError(f"{name} 视尺寸非法: {size}")
    # 字段来源必须与取值共存：有值不能标 none，无值不能标来源。
    # 以 meta.source_files.supplement 为标记：无补充快照的历史数据不强制
    # （包导入会在重建前先读到旧文件），重建后必然带该字段。
    if "supplement" in (meta.get("source_files") or {}):
        for value_key, source_key in (("magnitude", "magnitude_source"),
                                      ("size_arcmin", "size_source")):
            source = rec.get(source_key)
            if not isinstance(source, str) or not source:
                raise DSOIntegrityError(f"{name} 缺 {source_key}")
            if rec.get(value_key) is None:
                if source != "none":
                    raise DSOIntegrityError(
                        f"{name} {value_key}=null 却标来源 {source!r}")
            elif source == "none":
                raise DSOIntegrityError(
                    f"{name} {value_key} 有值却标 source=none")
    minor = rec.get("size_minor_arcmin")
    if minor is not None:
        minor = float(minor)
        if not math.isfinite(minor) or minor <= 0.0:
            raise DSOIntegrityError(f"{name} 短轴尺寸非法: {minor}")
    ratio = rec.get("axis_ratio")
    ratio_source = rec.get("axis_ratio_source")
    if size is not None and minor is not None:
        expected_ratio = minor / size
        if ratio is None:
            raise DSOIntegrityError(f"{name} 两轴齐全却缺 axis_ratio")
        try:
            ratio = float(ratio)
        except (TypeError, ValueError) as exc:
            raise DSOIntegrityError(f"{name} axis_ratio 非法") from exc
        if not math.isfinite(ratio) or abs(ratio - expected_ratio) > 1e-6:
            raise DSOIntegrityError(
                f"{name} axis_ratio 与短轴/长轴不一致: {ratio}")
        if ratio_source != "catalog":
            raise DSOIntegrityError(
                f"{name} 两轴齐全时 axis_ratio_source 必须为 catalog: "
                f"{ratio_source!r}")
    else:
        if ratio is not None:
            raise DSOIntegrityError(
                f"{name} 缺轴却给出 axis_ratio: {ratio!r}")
        if ratio_source != "unknown":
            raise DSOIntegrityError(
                f"{name} 缺轴时 axis_ratio_source 必须为 unknown: "
                f"{ratio_source!r}")
    if ratio_source == "estimated":
        raise DSOIntegrityError(
            f"{name} 数据层不得出现 axis_ratio_source=estimated（估计属视图）")
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
    zh_source = rec.get("zh_name_source") or ""
    zh_ref = rec.get("zh_name_ref") or ""
    if zh_name:
        if not _CJK_RE.search(zh_name):
            raise DSOIntegrityError(f"{name} 中文名不含汉字: {zh_name!r}")
        if zh_name == name_en:
            raise DSOIntegrityError(f"{name} 中文名与英文名相同: {zh_name!r}")
        if zh_source not in ("Wikidata", "自译"):
            raise DSOIntegrityError(
                f"{name} 中文名来源非法: {zh_source!r}")
        if zh_source == "Wikidata" and not re.match(r"^Q\d+", zh_ref):
            raise DSOIntegrityError(
                f"{name} Wikidata 中文名缺 QID: {zh_ref!r}")
    elif zh_source or zh_ref:
        raise DSOIntegrityError(
            f"{name} 中文名为空却声明了来源/依据: {zh_source!r}/{zh_ref!r}")
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
    source_file = rec.get("source_file")
    if source_file not in ("NGC.csv", "addendum.csv"):
        raise DSOIntegrityError(f"{name} source_file 非法: {source_file!r}")
    if not isinstance(rec.get("source_row"), int) or rec["source_row"] <= 0:
        raise DSOIntegrityError(f"{name} source_row 非法")
    source_name = rec.get("source_name")
    if not isinstance(source_name, str) or not source_name:
        raise DSOIntegrityError(f"{name} source_name 缺失")
    if name == "M102" and (cross.get("ngc") != 5866
                           or source_name != "NGC5866"):
        raise DSOIntegrityError(
            "M102 必须固定映射 NGC 5866（OpenNGC Dup 约定）")


def _check_counts(objects: List[dict], meta: dict) -> None:
    counts = meta.get("counts")
    if not isinstance(counts, dict):
        raise DSOMetadataError("meta.counts 不是对象")
    missing = meta.get("missing")
    if not isinstance(missing, dict):
        raise DSOMetadataError("meta.missing 不是对象")
    total = len(objects)
    messier = [o for o in objects if o["name"].startswith("M")]
    ngc = [o for o in objects if o["name"].startswith("NGC")]
    without_en = [o["name"] for o in objects if not o.get("name_en")]
    without_zh = [o["name"] for o in objects if not o.get("zh_name")]
    without_mag = [o["name"] for o in objects if o.get("magnitude") is None]
    without_size = [o["name"] for o in objects if o.get("size_arcmin") is None]
    without_minor = [o["name"] for o in objects
                     if o.get("size_minor_arcmin") is None]
    bands: Dict[str, int] = {}
    raw: Dict[str, int] = {}
    render: Dict[str, int] = {}
    for obj in objects:
        raw[obj["catalog_type"]] = raw.get(obj["catalog_type"], 0) + 1
        render[obj["dso_type"]] = render.get(obj["dso_type"], 0) + 1
        label = obj.get("magnitude_band") or "none"
        bands[label] = bands.get(label, 0) + 1
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
        "with_magnitude": total - len(without_mag),
        "without_magnitude": len(without_mag),
        "no_magnitude_designations": without_mag,
        "with_size_arcmin": total - len(without_size),
        "without_size_arcmin": len(without_size),
        "no_size_designations": without_size,
        "with_size_minor_arcmin": total - len(without_minor),
        "without_size_minor_arcmin": len(without_minor),
        "magnitude_band_counts": dict(sorted(bands.items())),
        "raw_type_counts": dict(sorted(raw.items())),
        "render_type_counts": dict(sorted(render.items())),
        "m102_resolved_to_ngc": 5866,
    }
    for key, value in expected.items():
        if counts.get(key) != value:
            raise DSOIntegrityError(
                f"meta.counts.{key} 与数据不一致: {counts.get(key)!r} != {value!r}")
    for key, names in (
            ("magnitude", without_mag), ("size_arcmin", without_size),
            ("size_minor_arcmin", without_minor), ("name_en", without_en),
            ("zh_name", without_zh)):
        if missing.get(key, {}).get("count") != len(names):
            raise DSOIntegrityError(f"meta.missing.{key}.count 与数据不一致")
        if missing[key].get("designations") != names:
            raise DSOIntegrityError(
                f"meta.missing.{key}.designations 与数据不一致")
    if len(messier) != 110:
        raise DSOIntegrityError(f"Messier 条数不是 110: {len(messier)}")
    numbers = sorted(int(o["name"][1:]) for o in messier)
    if numbers != list(range(1, 111)):
        raise DSOIntegrityError("Messier 编号缺号或重复")
    locations = [(o.get("source_file"), o.get("source_row")) for o in objects]
    if len(set(locations)) != total:
        raise DSOIntegrityError(
            "DSO (source_file, source_row) 重复，疑似同一条目被复制")


def _load_payload(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except FileNotFoundError:
        raise
    except ValueError as exc:
        raise DSOMetadataError(f"DSO 数据不是合法 JSON: {path}") from exc
    return _validate_payload(payload)


def _validate_payload(payload: dict) -> dict:
    """校验内存中的 DSO payload（测试与加载共用）。"""
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
    magnitude = rec.get("magnitude")
    size = rec.get("size_arcmin")
    return DSOEntry(
        name=rec["name"],
        dso_type=rec["dso_type"],
        ra_hours=float(rec["ra_deg"]) / 15.0,
        dec_deg=float(rec["dec_deg"]),
        magnitude=None if magnitude is None else float(magnitude),
        size_arcmin=None if size is None else float(size),
        constellation=rec["constellation"],
        name_en=rec.get("name_en") or "",
        zh_name=rec.get("zh_name") or "",
        aliases_en=tuple(rec.get("aliases_en") or ()),
        aliases_zh=tuple(rec.get("aliases_zh") or ()),
        catalog_type=rec.get("catalog_type") or "",
        size_minor_arcmin=(None if rec.get("size_minor_arcmin") is None
                           else float(rec["size_minor_arcmin"])),
        axis_ratio=(None if rec.get("axis_ratio") is None
                    else float(rec["axis_ratio"])),
        axis_ratio_source=rec.get("axis_ratio_source") or "unknown",
        cross_ids=cross,
        source_row=int(rec.get("source_row") or 0),
        source_file=rec.get("source_file") or "",
        source_name=rec.get("source_name") or "",
        magnitude_band=rec.get("magnitude_band") or "",
        magnitude_source=rec.get("magnitude_source") or "none",
        size_source=rec.get("size_source") or "none",
        zh_name_source=rec.get("zh_name_source") or "",
        zh_name_ref=rec.get("zh_name_ref") or "",
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

    def search(self, text: str, limit: int = 12) -> List[DSOEntry]:
        """按编号 / 英文名 / 中文名 / 别名搜索；编号精确命中排第一。

        深空编号形式多（M31 / NGC7000 / IC1396 / C33），用户不会先记规则，
        所以这里同时比 ``name``、``name_en``、``zh_name``、``aliases_*`` 与
        ``cross_ids``；精确编号优先，其余按星等（暗的排后）与名字排序，
        保证结果稳定、不分页也够用。
        """
        key = str(text or "").strip()
        if not key:
            return []
        norm = key.upper().replace(" ", "")
        out: List[DSOEntry] = []
        seen: set = set()
        exact = self._by_name.get(norm)
        if exact is not None:
            out.append(exact)
            seen.add(exact.name)
        low = key.lower()
        hits: List[Tuple[float, str, DSOEntry]] = []
        for e in self._entries:
            if e.name in seen:
                continue
            names = [e.name, e.name_en, e.zh_name, *e.aliases_en, *e.aliases_zh]
            cross = {f"{p}{n}" for p, n in e.cross_ids}
            hay = " ".join(str(n) for n in names if n).lower()
            if low in hay or any(norm == c.upper() for c in cross):
                mag = float(e.magnitude) if e.magnitude is not None else 99.0
                hits.append((mag, e.name, e))
        hits.sort(key=lambda t: (t[0], t[1]))
        for _mag, _name, e in hits:
            if len(out) >= max(1, int(limit)):
                break
            out.append(e)
        return out

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
                  max_sun_dist_deg: float = 90.0,
                  observer_lon_deg: float = 0.0) -> List[DSOEntry]:
    """推荐当前可看的深空目标（在 ``min_alt_deg`` 以上、离太阳够远）。

    2026-11-06 修时角：旧式 ``ha = gmst - ra`` 只在**格林尼治经度**成立，
    东经站点会整体偏 (lon/15) 小时 → 高度角错到几十度。现在按
    ``ha = (gmst + lon − ra)`` 算；``observer_lon_deg`` 默认 0.0 即为原行为，
    但真实站点必须传经度（东正）。
    """
    cat = catalog or BUILTIN_CATALOG
    T = (jd - 2451545.0) / 36525.0
    sun_lon = 280.46646 + 36000.76983 * T
    from .planets import _ecliptic_to_equatorial
    sun_ra, sun_dec = _ecliptic_to_equatorial(sun_lon, 0.0)
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0

    out: List[DSOEntry] = []
    for e in cat:
        if e.magnitude is None:
            continue  # 星等未知，不能据此声称可见
        if e.magnitude > max_magnitude:
            continue
        ha = math.radians((gmst + float(observer_lon_deg) - e.ra_deg) % 360.0)
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
