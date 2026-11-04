# SPDX-License-Identifier: MIT
"""TLE 目录真实性守卫：编号 ↔ 名称 ↔ 来源 三方校验。

事故背景：目录把 CATNR 44016 声明为「METEOR M2」，而该编号的真实对象是
「ATLAS 5 CENTAUR DEB」（半人马上面级残骸，历元 2026-09-08）；54234 被声明
为「FENGYUN 3D」，实为「NOAA 21 (JPSS-2)」。错轨道卫星算出来的过境看起来
一切正常，却与天空里那颗星毫无关系——这是最危险的静默错误。

本模块的立场：

  * 声明的名称必须与数据侧对象名（TLE line0，来自缓存/用户文件）归一化后
    **完全相等**才算「已核实」；否则降级为「未核实」，移出可选列表，绝不用
    来算过境或跟踪；
  * 缺数据（无该编号 TLE）单独归类为 no_data，不算错配，也不冒充核实；
  * 多源冲突逐条留痕，不静默择一。

归一化与 desktop 的 ``_norm_sat_name`` 同口径（大写 + 只留字母数字），
"METEOR-M 2" / "METEOR M2" 归一为 "METEORM2"，而 "METEOR-M2 2" 归一为
"METEORM22"——串号必须判不匹配。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "CatalogEntry",
    "SOURCE_PRIORITY",
    "normalize_name",
    "names_match",
    "verify_identity",
    "parse_tle_blocks",
    "user_entries",
    "cache_entries",
    "builtin_entries",
    "scan_catalog",
    "scan_live",
]

# 多源优先级：用户文件 > Celestrak（缓存/在线） > 内置表。
# 数字越小优先级越高。内置表只是「编号目录声明」，不携带真实数据。
SOURCE_PRIORITY: Dict[str, int] = {
    "user": 0,
    "celestrak": 1,
    "cache": 1,
    "builtin": 2,
}

# 默认缓存目录（与 orbit.fetch_tle 的磁盘缓存同一处）
DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".mbdsdr", "tle_cache")


def normalize_name(name: Any) -> str:
    """卫星名归一化：大写 + 只保留字母数字（与 desktop._norm_sat_name 同口径）。"""
    return "".join(ch for ch in str(name).upper() if ch.isalnum())


def names_match(declared: str, object_name: str) -> bool:
    """目录声明名与数据对象名是否语义一致（归一化后完全相等）。"""
    a = normalize_name(declared)
    b = normalize_name(object_name)
    return bool(a) and a == b


@dataclass
class CatalogEntry:
    """目录条目（声明）或数据条目（带 TLE）。

    ``name`` 是「目录声明的名字」（来源 user/builtin）；
    ``object_name`` 是数据侧真实对象名（TLE line0，来自 user/cache/celestrak）。
    两者缺一不可做三方校验。
    """

    name: str
    catnr: int
    source: str = "builtin"
    line1: str = ""
    line2: str = ""
    object_name: str = ""
    fetched_at: Optional[float] = None
    origin: str = ""

    @property
    def has_tle(self) -> bool:
        return bool(self.line1 and self.line2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "catnr": int(self.catnr),
            "source": self.source,
            "line1": self.line1,
            "line2": self.line2,
            "object_name": self.object_name,
            "fetched_at": self.fetched_at,
            "origin": self.origin,
        }

    def priority(self) -> int:
        return SOURCE_PRIORITY.get(self.source, 3)


def verify_identity(declared_name: str, object_name: str) -> Dict[str, Any]:
    """三方校验的「名称 ↔ 数据对象」一环。

    返回 ``{"status": "verified"|"mismatch"|"no_data", ...}``：
      - verified：归一化后完全相等；
      - mismatch：有数据但名字不符（必须降级，绝不用它算过境）；
      - no_data ：数据侧无对象名（缺数据，单独归类，不冒充核实）。
    """
    if not str(object_name or "").strip():
        return {
            "status": "no_data",
            "verified": False,
            "declared_name": str(declared_name),
            "object_name": "",
            "reason": "无数据对象名（缺该编号的 TLE），无法核实",
        }
    if names_match(declared_name, object_name):
        return {
            "status": "verified",
            "verified": True,
            "declared_name": str(declared_name),
            "object_name": str(object_name),
            "reason": "名称与数据对象名归一化后一致",
        }
    return {
        "status": "mismatch",
        "verified": False,
        "declared_name": str(declared_name),
        "object_name": str(object_name),
        "reason": (
            f"声明名「{declared_name}」与数据对象名「{object_name}」不符："
            "编号被映射到了另一颗/另一类对象，必须降级"
        ),
    }


def _catnr_from_line1(line1: str) -> Optional[int]:
    try:
        return int(line1[2:7].strip())
    except (ValueError, IndexError):
        return None


def parse_tle_blocks(text: str, source: str = "user",
                     origin: str = "", fetched_at: Optional[float] = None,
                     ) -> List[CatalogEntry]:
    """解析 TLE 文本（名称 + 1 + 2 三行块；也容忍缺名称的两行块）。

    只做结构自洽检查：line2 的编号必须与 line1 一致，否则跳过（不自洽的
    数据不进目录）。校验和由数据源侧负责（缓存/网络入口已有校验）。
    """
    lines = [ln.strip() for ln in str(text).splitlines()]
    n = len(lines)
    out: List[CatalogEntry] = []
    i = 0
    while i < n:
        if not lines[i].startswith("1 "):
            i += 1
            continue
        if i + 1 >= n or not lines[i + 1].startswith("2 "):
            i += 1
            continue
        line1, line2 = lines[i], lines[i + 1]
        catnr = _catnr_from_line1(line1)
        catnr2 = _catnr_from_line1(line2)
        if catnr is None or catnr2 is None or catnr != catnr2:
            i += 2
            continue
        name = ""
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        if j >= 0 and not lines[j].startswith(("1 ", "2 ")):
            name = lines[j]
        if not name:
            name = f"NORAD {catnr}"
        out.append(CatalogEntry(
            name=name, catnr=catnr, source=source,
            line1=line1, line2=line2, object_name=name,
            fetched_at=fetched_at, origin=origin,
        ))
        i += 2
    return out


def user_entries(text: str, origin: str = "<user>",
                 fetched_at: Optional[float] = None) -> List[CatalogEntry]:
    """用户导入的 TLE 文件 → 条目（source=user，优先级最高）。"""
    return parse_tle_blocks(text, source="user", origin=origin,
                            fetched_at=fetched_at)


def cache_entries(cache_dir: Optional[str] = None,
                  now: Optional[float] = None) -> List[CatalogEntry]:
    """扫描 Celestrak 磁盘缓存（每个 CATNR 一个三行 .tle 文件）。

    ``fetched_at`` 取文件 mtime——这是「缓存时间」，与 TLE 历元（轨道数据
    自身的时刻）不是一回事，两者都保留。
    """
    d = cache_dir or DEFAULT_CACHE_DIR
    ref = now if now is not None else time.time()
    out: List[CatalogEntry] = []
    if not os.path.isdir(d):
        return out
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".tle"):
            continue
        path = os.path.join(d, fn)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines()]
            lines = [ln for ln in lines if ln]
            if len(lines) < 3 or not lines[1].startswith("1 ") \
                    or not lines[2].startswith("2 "):
                continue
            name, line1, line2 = lines[0], lines[1], lines[2]
            catnr = _catnr_from_line1(line1)
            if catnr is None or catnr != _catnr_from_line1(line2):
                continue
            out.append(CatalogEntry(
                name=name, catnr=catnr, source="cache",
                line1=line1, line2=line2, object_name=name,
                fetched_at=os.path.getmtime(path), origin=path))
        except OSError:
            continue
    return out


def builtin_entries(sat_map: Optional[Dict[str, int]] = None) -> List[CatalogEntry]:
    """内置目录声明（名称 + CATNR），无 TLE 数据（数据由缓存/在线补）。

    默认取 ``orbit.BUILTIN_SATS``（延迟导入避免循环依赖）；测试可注入替身。
    """
    if sat_map is None:
        from .orbit import BUILTIN_SATS
        sat_map = BUILTIN_SATS
    return [CatalogEntry(name=str(name), catnr=int(catnr), source="builtin")
            for name, catnr in sat_map.items()]


def _declaration_conflicts(entries: Sequence[CatalogEntry]) -> List[Dict[str, Any]]:
    """声明侧冲突：同名不同号、同号不同名——全部列出，不静默择一。"""
    conflicts: List[Dict[str, Any]] = []
    by_name: Dict[str, List[CatalogEntry]] = {}
    by_catnr: Dict[int, List[CatalogEntry]] = {}
    for e in entries:
        key = normalize_name(e.name)
        if key:
            by_name.setdefault(key, []).append(e)
        by_catnr.setdefault(int(e.catnr), []).append(e)
    for key, group in by_name.items():
        catnrs = sorted({e.catnr for e in group})
        if len(catnrs) > 1:
            conflicts.append({
                "type": "name_catnr_conflict",
                "name": group[0].name,
                "catnrs": catnrs,
                "sources": [e.source for e in group],
                "resolution": "按来源优先级取用户声明，其余保留为冲突记录",
            })
    for catnr, group in by_catnr.items():
        names = sorted({normalize_name(e.name) for e in group})
        if len(names) > 1:
            conflicts.append({
                "type": "catnr_name_conflict",
                "catnr": catnr,
                "names": [e.name for e in group],
                "sources": [e.source for e in group],
                "resolution": "保留各声明并降级名称不符者",
            })
    return conflicts


def _data_for(catnr: int,
              data_pool: Sequence[CatalogEntry]) -> Optional[CatalogEntry]:
    """按编号取数据：优先级 user > celestrak/cache > builtin；同级取最新缓存。"""
    candidates = [d for d in data_pool if d.catnr == catnr and d.has_tle]
    if not candidates:
        return None
    candidates.sort(key=lambda d: (d.priority(), -(d.fetched_at or 0.0)))
    return candidates[0]


def scan_catalog(declarations: Sequence[CatalogEntry],
                 data_pool: Optional[Sequence[CatalogEntry]] = None,
                 now: Optional[float] = None) -> Dict[str, Any]:
    """全目录扫描：每条声明都做「编号 ↔ 名称 ↔ 来源」三方校验。

    返回 ``{"total", "passed", "degraded", "mismatch", "no_data",
    "reasons", "rows", "conflicts"}``。``passed`` = 已核实且可用于过境/跟踪；
    ``degraded`` = 未核实/缺数据，不得参与过境或跟踪。
    """
    pool = list(data_pool or [])
    conflicts = _declaration_conflicts(list(declarations))
    rows: List[Dict[str, Any]] = []
    reasons: Dict[str, List[Dict[str, Any]]] = {
        "identity_mismatch": [], "no_data": [],
    }
    passed = 0
    for decl in declarations:
        data = _data_for(int(decl.catnr), pool) if not decl.has_tle else decl
        object_name = data.object_name if data is not None else ""
        identity = verify_identity(decl.name, object_name)
        usable = identity["status"] == "verified"
        row = {
            "name": decl.name,
            "catnr": int(decl.catnr),
            "declared_source": decl.source,
            "data_source": (data.source if data is not None else ""),
            "data_origin": (data.origin if data is not None else ""),
            "object_name": object_name,
            "identity": identity,
            "selectable": usable,
            "pass_eligible": usable,
            "tracking_eligible": usable,
            "degraded": not usable,
            "degrade_reason": "" if usable else identity["status"],
        }
        if usable:
            passed += 1
        else:
            key = "no_data" if identity["status"] == "no_data" else "identity_mismatch"
            reasons[key].append({
                "name": decl.name, "catnr": int(decl.catnr),
                "object_name": object_name,
                "detail": identity["reason"],
            })
        # 把与该条目相关的声明冲突附在行上（按名或号命中）
        row["conflicts"] = [
            c for c in conflicts
            if (c["type"] == "name_catnr_conflict"
                and normalize_name(c["name"]) == normalize_name(decl.name))
            or (c["type"] == "catnr_name_conflict" and c["catnr"] == int(decl.catnr))
        ]
        rows.append(row)
    degraded = len(rows) - passed
    return {
        "total": len(rows),
        "passed": passed,
        "degraded": degraded,
        "mismatch": len(reasons["identity_mismatch"]),
        "no_data": len(reasons["no_data"]),
        "reasons": reasons,
        "rows": rows,
        "conflicts": conflicts,
        "now": now if now is not None else time.time(),
    }


def scan_live(cache_dir: Optional[str] = None,
              sat_map: Optional[Dict[str, int]] = None,
              now: Optional[float] = None) -> Dict[str, Any]:
    """对「内置声明 + 本机 Celestrak 缓存」做全目录扫描（离线可用）。"""
    decls = builtin_entries(sat_map)
    pool = cache_entries(cache_dir=cache_dir, now=now)
    return scan_catalog(decls, pool, now=now)
