# SPDX-License-Identifier: MIT
"""卫星目录的唯一真值：内置基线 + 磁盘缓存里发现的星。

为什么要这个模块
----------------
在它之前，同一份 6 颗星的目录被抄了两遍：
``orbit.BUILTIN_SATS``（名称→NORAD）与 ``rf_sky_view.SATELLITE_META``
（名称→NORAD/频率/类别），而 Celestrak 刷新也只更新这 6 个 NORAD。
结果是：**过境、跟踪、点星调谐、气象解码全被这 6 颗卡住**——
用户手里有别的星的 TLE，系统认不出来。

本模块做两件事：
1. 把「内置 6 颗」收成**一处**（含频率与类别），其余模块从这里派生，
   不再各抄一份；
2. 把「磁盘缓存里已经存在的 TLE」也纳入目录——用户把 ``<NORAD>.tle``
   放进 ``~/.mbdsdr/tle_cache/`` 就算导入，不需要改代码。

口径（不许含糊的地方）
----------------------
* 内置 6 颗是**基线**：保证系统开箱就有可用的星，不依赖用户操作。
* 缓存发现的星**频率未知就是未知**（``freq_mhz=None``）。猜一个频率会让
  用户按错频点去听，比说"不知道"糟得多。
* 类别未知记 ``other``，不往 weather/amateur 里塞。
* 目录只描述"有哪些星"，**不缓存 TLE 元素本身**——元素有时效，
  必须每次从缓存/网络取，否则过境会按旧根数算。

与既有模块的关系
----------------
不替代 ``SatelliteTracker``（它管 TLE 取用与刷新）；本模块只管"目录"。
``orbit.BUILTIN_SATS`` / ``rf_sky_view.SATELLITE_META`` 从这里的
``BUILTIN_META`` 派生，由 ``tests/test_sat_catalog.py`` 钉住一致性。
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

#: 缓存文件名里的 NORAD 号（``<NORAD>.tle``）。
_CACHE_NAME_RE = re.compile(r"^(\d{1,6})\.tle$", re.IGNORECASE)


@dataclass(frozen=True)
class SatelliteMeta:
    """一颗星的目录条目。只描述身份与用途，不含 TLE 元素。"""

    name: str
    catnr: int
    freq_mhz: Optional[float]
    """下行中心频率（MHz）。``None`` = 未知，**不许猜**。"""

    kind: str
    """``weather`` / ``amateur`` / ``other``。未知就是 ``other``。"""


#: 内置基线目录：**唯一真值**。
#: NORAD 编号必须与真实对象一致（2026-11 校正）：
#:   METEOR M2 = 40069（44016 实为 ATLAS 5 CENTAUR DEB 残骸）
#:   FENGYUN 3D = 43010（54234 实为 NOAA 21/JPSS-2）
#: 改动前先跑 tle_integrity.scan_catalog 全目录扫描。
BUILTIN_META: Dict[str, SatelliteMeta] = {
    "NOAA 15":     SatelliteMeta("NOAA 15", 25338, 137.620, "weather"),
    "NOAA 18":     SatelliteMeta("NOAA 18", 28654, 137.9125, "weather"),
    "NOAA 19":     SatelliteMeta("NOAA 19", 33591, 137.100, "weather"),
    "ISS (ZARYA)": SatelliteMeta("ISS (ZARYA)", 25544, 145.800, "amateur"),
    "METEOR M2":   SatelliteMeta("METEOR M2", 40069, 137.900, "weather"),
    "FENGYUN 3D":  SatelliteMeta("FENGYUN 3D", 43010, 136.900, "weather"),
}

#: 内置基线对应的 NORAD 集合（给"这是不是基线星"的判断用）。
BUILTIN_CATNRS = frozenset(e.catnr for e in BUILTIN_META.values())


def _cache_dir() -> str:
    """TLE 缓存目录：延迟导入 ``orbit``，避免 orbit ↔ sat_catalog 循环。"""
    from .orbit import tle_cache_dir

    return tle_cache_dir()


def user_tle_dir() -> str:
    """用户 TLE 目录（``~/.mbdsdr/tle``）的**唯一**路径来源。

    这里是"用户显式导入"的位置：把 ``任意名.tle`` 放进去即可，文件名不必是
    NORAD 号（与缓存目录的 ``<NORAD>.tle`` 约定不同——缓存是程序写的，
    这里是用户手放的）。

    以前这个路径字面量至少出现在两个模块里（桌面加载器 + 文档），
    改目录要改多处；现在只有这一处。
    """
    override = os.environ.get("MBDSDR_USER_TLE_DIR")
    if override:
        return os.path.abspath(os.path.expanduser(override))
    return os.path.expanduser(os.path.join("~", ".mbdsdr", "tle"))


def user_tle_files(directory: Optional[str] = None) -> List[str]:
    """列出用户 TLE 目录下的 ``*.tle`` 绝对路径（按文件名排序）。

    目录不存在/无权限返回空表——没有文件不是错误，是常态。
    """
    target = directory or user_tle_dir()
    try:
        names = sorted(os.listdir(target))
    except OSError:
        return []
    return [os.path.join(target, n) for n in names
            if n.lower().endswith(".tle")]


def _norad_from_line1(line1: str) -> Optional[int]:
    """从 TLE 第一行取 NORAD 号（第 3–7 列，1-based）。

    比自己数偏移更稳的备选是走 ``tle_parse``，但那会引入完整解析开销；
    这里只要一个整数，按规范列位取即可，取不到就返回 None（不猜）。
    """
    s = (line1 or "").strip()
    if len(s) < 7 or not s.startswith("1 "):
        return None
    chunk = s[2:7].strip()
    if not chunk.isdigit():
        return None
    return int(chunk)


def _read_cached_entry(path: str, filename_norad: int) -> Optional[SatelliteMeta]:
    """读一个缓存文件，构造目录条目。

    名称取文件第一行（Celestrak 格式 ``NAME\\n1 ...\\n2 ...``）；
    文件里没有名字就退回 ``NORAD <n>``——**不编一个好听的星名**。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.readline().strip()
            line1 = fh.readline().strip()
    except OSError:
        return None

    catnr = _norad_from_line1(line1) or filename_norad
    name = head if head and not head.startswith(("1 ", "2 ")) else f"NORAD {catnr}"
    return SatelliteMeta(name=name, catnr=catnr, freq_mhz=None, kind="other")


def discover_cached(cache_dir: Optional[str] = None) -> List[SatelliteMeta]:
    """扫缓存目录，返回其中存在 TLE 的星（不含内置基线）。

    这条路径就是"用户导入"：把 ``<NORAD>.tle`` 放进目录即可。
    坏文件跳过而不是抛——一个坏文件不该让整个目录不可用。
    """
    directory = cache_dir or _cache_dir()
    try:
        names = os.listdir(directory)
    except OSError:
        return []

    found: List[SatelliteMeta] = []
    for fname in sorted(names):
        m = _CACHE_NAME_RE.match(fname)
        if not m:
            continue
        norad = int(m.group(1))
        if norad in BUILTIN_CATNRS:
            continue  # 基线星另算，避免重复
        entry = _read_cached_entry(os.path.join(directory, fname), norad)
        if entry is not None:
            found.append(entry)
    return found


def tle_age_days(norad: int, cache_dir: Optional[str] = None) -> Optional[float]:
    """缓存里该星 TLE 的年龄（天）。没有缓存返回 None，**不估算**。

    用文件修改时间而不是内容里的历元：内容历元需要完整解析，
    而文件落盘时间足以回答"这份缓存有多旧"（``_persist_entry`` 刷新即重写）。
    """
    directory = cache_dir or _cache_dir()
    path = os.path.join(directory, f"{int(norad)}.tle")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    import time as _time

    return max(0.0, (_time.time() - mtime) / 86400.0)


def merged_catalog(cache_dir: Optional[str] = None,
                   include_cached: bool = True) -> Tuple[SatelliteMeta, ...]:
    """完整目录：内置基线在前，缓存发现的在后（按 NORAD 升序）。

    顺序是刻意的：界面上"内置星"永远排在用户自己加的星前面，
    这样换机器/换缓存目录不会让常用星的列表位置漂移。
    """
    out: List[SatelliteMeta] = [BUILTIN_META[k] for k in BUILTIN_META]
    if include_cached:
        out.extend(discover_cached(cache_dir))
    return tuple(out)


def catalog_report(cache_dir: Optional[str] = None) -> Dict[str, object]:
    """审计用快照：数清基线几颗、缓存几颗、几颗频率未知。

    没有频率的星必须能被数出来——它是"能用但不知道听哪个频点"的
    诚实状态，不能藏进"共 N 颗"里。
    """
    cached = discover_cached(cache_dir)
    known = [e for e in cached if e.freq_mhz is not None]
    return {
        "builtin_count": len(BUILTIN_META),
        "cached_count": len(cached),
        "total_count": len(BUILTIN_META) + len(cached),
        "cached_without_freq": len(cached) - len(known),
        "cache_dir": cache_dir or _cache_dir(),
    }
