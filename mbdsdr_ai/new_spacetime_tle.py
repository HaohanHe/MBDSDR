# SPDX-License-Identifier: MIT
"""新时空面板 TLE 真联网拉取与解析模块
=====================================

从 celestrak 公开接口拉取 NORAD TLE 数据，带超时 urllib 抓取；
成功写磁盘/内存缓存，网络失败时回退本地缓存；绝不伪造 TLE。

缓存策略：
  - 内存缓存 Dict[catnr, TLEEntry]
  - 磁盘缓存每个 CATNR 一个 .tle 文件（名称 + line1 + line2 三行）
  - 缓存有效期 24 小时；网络失败时即便缓存过期也尽量回退（旧数据好过没有）

注意：不做轨道传播；取数/缓存统一委托 mbdsdr_ai.tle_fetch（P12），离线判定
复用 orbit._network_disabled（延迟导入，避免模块加载期循环依赖）。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
import os
import time
import urllib.request
import ssl
from datetime import datetime, timezone, timedelta

CELESTRAK_BASE = "https://celestrak.org/NORAD/elements/gp.php"

# 默认关注卫星列表 (名称, NORAD CATNR)
# 编号校正（2026-11）：METEOR-M 2 = 40069（44016 是 ATLAS 5 CENTAUR DEB），
# FENGYUN 3D = 43010（54234 是 NOAA 21/JPSS-2）。
DEFAULT_SATELLITES: List[Tuple[str, int]] = [
    ("ISS (ZARYA)", 25544),
    ("NOAA 19", 33591),
    ("NOAA 18", 28654),
    ("NOAA 15", 25338),
    ("METEOR M2", 40069),
    ("FENGYUN 3D", 43010),
]

# 缓存有效期：24 小时
_CACHE_TTL = 24 * 3600.0

_UA = "MBDSDR/1.0 (new-spacetime)"


class TLEFetchError(RuntimeError):
    """TLE 获取失败（本模块内部用；fetch 对外仍以 None 表示诚实空态）。"""


@dataclass
class TLEEntry:
    name: str               # 数据侧真实对象名（TLE line0），不得被声明名覆盖
    line1: str
    line2: str
    catnr: int = 0
    source: str = ""        # "celestrak" / "cache" / "user"
    fetched_at: float = 0.0
    # P10 三方校验留痕（声明名 vs 对象名）
    declared_name: str = ""
    identity_status: str = ""   # verified / mismatch / no_data
    identity_detail: str = ""
    freshness_tier: str = ""    # fresh / usable / expired / dead / unknown
    pass_eligible: bool = False
    # P12 离线降级留痕（UI/诊断可见）
    offline: bool = False
    offline_label: str = ""
    refresh_status: str = ""    # network / cache / memory / not_updated


class TLEManager:
    """TLE 拉取/解析/缓存管理器。原生 urllib，不引入第三方 HTTP 依赖。"""

    def __init__(self, cache_dir: Optional[str] = None, timeout: float = 10.0):
        """cache_dir 默认 ~/.mbdsdr/new_spacetime_tle；timeout 为 urllib 超时秒数。"""
        if cache_dir is None:
            cache_dir = os.path.expanduser("~/.mbdsdr/new_spacetime_tle")
        self.cache_dir = cache_dir
        self.timeout = float(timeout)
        self._mem: Dict[int, TLEEntry] = {}
        self._tle_store = None
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError:
            # 缓存目录不可写时退化为仅内存缓存，不影响联网拉取
            pass

    def _store(self):
        """P12：TLE 取数统一走 tle_fetch（24h 门槛 + 退避 + 离线标注）。"""
        if self._tle_store is None:
            from .tle_fetch import FetchPolicy, TleFetcher
            from .orbit import _network_disabled
            self._tle_store = TleFetcher(
                opener=lambda url, t, d: self._http_get(url),
                cache_dir_provider=lambda: self.cache_dir,
                offline_provider=_network_disabled,
                policy=FetchPolicy(
                    fresh_max_hours=_CACHE_TTL / 3600.0,
                    request_timeout_s=self.timeout,
                    hard_deadline_s=self.timeout + 5.0,
                ),
                error_cls=TLEFetchError,
                source="celestrak",
            )
        return self._tle_store

    def source_status(self, now: Optional[float] = None) -> Dict[str, Any]:
        """TLE 源健康度（最近成功/失败、退避剩余）——诊断/UI 直接可读。"""
        return self._store().health_report(now=now)

    # ------------------------------------------------------------------ HTTP
    def _http_get(self, url: str) -> str:
        """带超时的 GET，User-Agent 标识本应用。任何异常向上抛出。"""
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, timeout=self.timeout, context=ctx) as resp:
            raw = resp.read()
        return raw.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------ cache
    def _cache_path(self, catnr: int) -> str:
        return os.path.join(self.cache_dir, f"{catnr}.tle")

    def _write_disk(self, entry: TLEEntry) -> None:
        try:
            with open(self._cache_path(entry.catnr), "w", encoding="utf-8") as f:
                f.write(entry.name + "\n")
                f.write(entry.line1 + "\n")
                f.write(entry.line2 + "\n")
        except OSError:
            pass

    def _read_disk(self, catnr: int) -> Optional[TLEEntry]:
        path = self._cache_path(catnr)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
            lines = [ln for ln in lines]
            if len(lines) < 3:
                return None
            name = lines[0].strip()
            line1 = lines[1].strip()
            line2 = lines[2].strip()
            if not (line1.startswith("1 ") and line2.startswith("2 ")):
                return None
            if not (self.validate_checksum(line1) and self.validate_checksum(line2)):
                return None
            catnr_parsed = self.catnr_from_line1(line1)
            mtime = os.path.getmtime(path)
            return TLEEntry(name=name, line1=line1, line2=line2,
                            catnr=catnr_parsed, source="cache", fetched_at=mtime)
        except (OSError, ValueError, IndexError):
            return None

    # ------------------------------------------------------------------ public
    def fetch(self, catnr: int, name: str = "") -> Optional[TLEEntry]:
        """按 CATNR 从 celestrak 拉取，成功写缓存并返回；网络失败时读本地缓存；都没有返回 None。绝不伪造。

        ``name`` 是调用方声明的名字（目录名），只用于三方校验，**不会覆盖**
        TLE line0 的真实对象名；错配会留在 ``identity_status="mismatch"``。
        """
        now = time.time()
        disk = self._read_disk(catnr)   # 失败/校验不过时的本地回退

        # 1) 取数/缓存统一经 tle_fetch（24h 门槛、退避、离线标注）
        try:
            rec = self._store().fetch_record(
                catnr, max_age_hours=_CACHE_TTL / 3600.0)
        except TLEFetchError:
            # 4) 取数失败：回退本地磁盘缓存（即便过期也用，旧数据好过没有）
            if disk is not None:
                disk.source = "cache"
                self._annotate(disk, name)
                self._mem[catnr] = disk
                return disk
            return None

        # 下载/缓存内容仍须过校验和（store 只校验结构，不校验 TLE 校验位）
        if not (self.validate_checksum(rec["line1"])
                and self.validate_checksum(rec["line2"])):
            if disk is not None:
                disk.source = "cache"
                self._annotate(disk, name)
                self._mem[catnr] = disk
                return disk
            return None

        e = TLEEntry(
            name=rec.get("name") or f"NORAD {catnr}",
            line1=rec["line1"], line2=rec["line2"], catnr=catnr,
            source="celestrak" if rec["source"] == "celestrak" else "cache",
            fetched_at=float(rec.get("fetched_at") or now),
            offline=bool(rec.get("offline")),
            offline_label=str(rec.get("offline_label") or ""),
            refresh_status=str(rec.get("refresh_status") or ""),
        )
        # 不覆盖 e.name：真实对象名是身份校验的唯一证据
        self._annotate(e, name)
        self._mem[catnr] = e
        return e

    @staticmethod
    def _annotate(entry: TLEEntry, declared_name: str = "") -> None:
        """给条目附上三方校验与新鲜度信息（无声明名时只算新鲜度）。"""
        from .tle_integrity import freshness, verify_identity
        if declared_name:
            ident = verify_identity(declared_name, entry.name)
            entry.declared_name = declared_name
            entry.identity_status = ident["status"]
            entry.identity_detail = ident["reason"]
        else:
            entry.identity_status = "no_data"
            entry.identity_detail = "调用方未提供声明名，无法做名称核对"
        fresh = freshness(entry.line1, entry.fetched_at or None)
        entry.freshness_tier = fresh["tier"]
        entry.pass_eligible = bool(fresh["pass_eligible"])

    def fetch_group(self, url: str) -> List[TLEEntry]:
        """从 celestrak 群组 URL（如 gp.php?GROUP=weather&FORMAT=tle）拉取整组，解析所有 3 行块。失败返回 []。"""
        try:
            text = self._http_get(url)
        except Exception:
            return []
        now = time.time()
        entries = self.parse_tle_text(text)
        for e in entries:
            e.source = "celestrak"
            e.fetched_at = now
            self._mem[e.catnr] = e
            self._write_disk(e)
        return entries

    def get(self, catnr: int) -> Optional[TLEEntry]:
        """从内存缓存取，不联网。"""
        return self._mem.get(catnr)

    def all_cached(self) -> List[TLEEntry]:
        """返回所有已缓存条目。"""
        return list(self._mem.values())

    # ------------------------------------------------------------------ static
    @staticmethod
    def validate_checksum(line: str) -> bool:
        """TLE 行校验和：除最后一位外，数字取其值，减号 '-' 计 1，其余字符（字母、空格、+、.）计 0；
        累加后 mod 10 与行末数字比较。

        这与 NORAD/SPACETRACK 及 sgp4 参考实现一致：
            sum(int(c) if c.isdigit() else 1 if c == '-' else 0) % 10
        """
        if not line or len(line) < 2:
            return False
        last = line[-1]
        if last not in "0123456789":
            return False
        expected = int(last)
        total = 0
        for c in line[:-1]:
            if "0" <= c <= "9":
                total += int(c)
            elif c == "-":
                total += 1
            else:
                # 字母、空格、+、. 等一律计 0
                total += 0
        return (total % 10) == expected

    @staticmethod
    def parse_tle_text(text: str) -> List[TLEEntry]:
        """解析含 3 行块（名称/line1/line2）的文本，跳过校验和无效的块。"""
        entries: List[TLEEntry] = []
        lines = text.splitlines()
        n = len(lines)
        i = 0
        while i < n:
            stripped = lines[i].strip()
            if stripped.startswith("1 "):
                # 名称 = line1 之前最近的非空行
                name = ""
                j = i - 1
                while j >= 0 and not lines[j].strip():
                    j -= 1
                if j >= 0:
                    name = lines[j].strip()
                # 下一行必须是 line2
                if i + 1 < n and lines[i + 1].strip().startswith("2 "):
                    line1 = stripped
                    line2 = lines[i + 1].strip()
                    if (TLEManager.validate_checksum(line1)
                            and TLEManager.validate_checksum(line2)):
                        try:
                            catnr = TLEManager.catnr_from_line1(line1)
                        except ValueError:
                            i += 1
                            continue
                        entries.append(TLEEntry(
                            name=name, line1=line1, line2=line2,
                            catnr=catnr, source="celestrak",
                        ))
                    i += 2
                    continue
            i += 1
        return entries

    @staticmethod
    def catnr_from_line1(line1: str) -> int:
        """从 TLE 第一行提取 NORAD CATNR（第 3-7 列，1 基）。"""
        return int(line1[2:7].strip())


# ---------------------------------------------------------------------------
# TLE 历元新鲜度（LEO SGP4 预报精度随历元老化下降）
# ---------------------------------------------------------------------------
#
# 统一新鲜度阈值（天）。LEO 轨道受大气阻力摄动显著，TLE 历元越旧，SGP4 外推
# 误差越大：经验上 <~3 天预报可靠，>~7 天过境方位/多普勒开始明显漂移，>2 周
# 不建议用于对准。这里取 7 天作为"新鲜/陈旧"分界（保守，便于 SDR 对准）。
# 该常量是全仓唯一阈值，predict_passes_report 与日志/测试共用。
TLE_FRESHNESS_MAX_DAYS = 7.0


def _is_leap_year(year: int) -> bool:
    """公历闰年（4 年一闰，百年不闰，四百年再闰）。"""
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def parse_tle_epoch(line1: str) -> Optional[datetime]:
    """从 TLE 第 1 行解析历元（epoch）为 UTC datetime。

    TLE 第 1 行历元占**固定列**第 19–32 列（1 基），格式 ``YYDOO.DDDDDDDD``：
    两位年份 + 一年中的第几天（含小数）。按 Spacetrack Report #3 的列布局取子串，
    不依赖空格分词（intl designator 等字段间空格数不固定）。
    yy>=57 归 1900s，否则 2000s。解析失败返回 None —— 调用方据此标 "none"，绝不猜历元。
    """
    if not line1 or not line1.startswith("1 "):
        return None
    # 固定列：第 19~32 列（0 基 [18:32]）
    if len(line1) < 32:
        return None
    ep = line1[18:32].strip()
    try:
        if len(ep) < 5 or "." not in ep:
            return None
        yy = int(ep[0:2])
        doy = float(ep[2:])
    except (ValueError, IndexError):
        return None
    year = (1900 + yy) if yy >= 57 else (2000 + yy)
    if doy <= 0:
        return None
    # 年积日越界（如 DDD=367）必须判解析失败，不能顺延到下一年冒充历元。
    max_doy = 366.0 if _is_leap_year(year) else 365.0
    if doy >= max_doy + 1.0:
        return None
    # doy=1.0 = 1 月 1 日 00:00；(doy-1) 天后的小数时刻
    base = datetime(year, 1, 1, tzinfo=timezone.utc)
    return base + timedelta(days=(doy - 1.0))


def tle_age_days(line1: str,
                 now: Optional[datetime] = None) -> Optional[float]:
    """TLE 历元到 ``now`` 的天数。历元解析失败返回 None。

    ``now`` 默认本机 UTC；测试时注入固定时刻以做确定性断言。
    """
    epoch = parse_tle_epoch(line1)
    if epoch is None:
        return None
    ref = now or datetime.now(timezone.utc)
    return (ref - epoch).total_seconds() / 86400.0


def tle_freshness(line1: str,
                  now: Optional[datetime] = None,
                  max_days: float = TLE_FRESHNESS_MAX_DAYS) -> dict:
    """报告单条 TLE 的新鲜度三态：fresh / stale / none。

    返回 dict::
        {"status": "fresh"|"stale"|"none",
         "epoch_utc": "ISO..."|None,
         "age_days": float|None,
         "threshold_days": float}

    - 历元可解析且 age <= max_days → "fresh"；
    - 历元可解析但 age > max_days → "stale"（仍可用，但预报精度下降，需告警）；
    - 历元解析失败（无/坏 TLE）→ "none"。
    """
    epoch = parse_tle_epoch(line1)
    if epoch is None:
        return {"status": "none", "epoch_utc": None, "age_days": None,
                "threshold_days": float(max_days)}
    age = tle_age_days(line1, now=now)
    status = "fresh" if (age is not None and age <= max_days) else "stale"
    return {"status": status, "epoch_utc": epoch.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "age_days": round(age, 3) if age is not None else None,
            "threshold_days": float(max_days)}
