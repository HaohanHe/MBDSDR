"""新时空面板 TLE 真联网拉取与解析模块
=====================================

从 celestrak 公开接口拉取 NORAD TLE 数据，带超时 urllib 抓取；
成功写磁盘/内存缓存，网络失败时回退本地缓存；绝不伪造 TLE。

缓存策略：
  - 内存缓存 Dict[catnr, TLEEntry]
  - 磁盘缓存每个 CATNR 一个 .tle 文件（名称 + line1 + line2 三行）
  - 缓存有效期 24 小时；网络失败时即便缓存过期也尽量回退（旧数据好过没有）

注意：本模块自包含，不依赖 mbdsdr_ai/orbit.py，不做轨道传播。
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any
import os
import time
import urllib.request
import ssl

CELESTRAK_BASE = "https://celestrak.org/NORAD/elements/gp.php"

# 默认关注卫星列表 (名称, NORAD CATNR)
DEFAULT_SATELLITES: List[Tuple[str, int]] = [
    ("ISS (ZARYA)", 25544),
    ("NOAA 19", 33591),
    ("NOAA 18", 28654),
    ("NOAA 15", 25338),
    ("METEOR M2", 44016),
    ("FENGYUN 3D", 54234),
]

# 缓存有效期：24 小时
_CACHE_TTL = 24 * 3600.0

_UA = "MBDSDR/1.0 (new-spacetime)"


@dataclass
class TLEEntry:
    name: str
    line1: str
    line2: str
    catnr: int = 0
    source: str = ""        # "celestrak" / "cache" / "user"
    fetched_at: float = 0.0


class TLEManager:
    """TLE 拉取/解析/缓存管理器。原生 urllib，不引入第三方 HTTP 依赖。"""

    def __init__(self, cache_dir: Optional[str] = None, timeout: float = 10.0):
        """cache_dir 默认 ~/.mbdsdr/new_spacetime_tle；timeout 为 urllib 超时秒数。"""
        if cache_dir is None:
            cache_dir = os.path.expanduser("~/.mbdsdr/new_spacetime_tle")
        self.cache_dir = cache_dir
        self.timeout = float(timeout)
        self._mem: Dict[int, TLEEntry] = {}
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
        except OSError:
            # 缓存目录不可写时退化为仅内存缓存，不影响联网拉取
            pass

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
        """按 CATNR 从 celestrak 拉取，成功写缓存并返回；网络失败时读本地缓存；都没有返回 None。绝不伪造。"""
        now = time.time()

        # 1) 内存缓存（未过期）
        ent = self._mem.get(catnr)
        if ent and (now - ent.fetched_at) < _CACHE_TTL:
            return ent

        # 2) 磁盘缓存（未过期）
        disk = self._read_disk(catnr)
        if disk is not None and (now - disk.fetched_at) < _CACHE_TTL:
            disk.source = "cache"
            self._mem[catnr] = disk
            return disk

        # 3) 联网
        try:
            url = f"{CELESTRAK_BASE}?CATNR={catnr}&FORMAT=tle"
            text = self._http_get(url)
            entries = self.parse_tle_text(text)
            for e in entries:
                if e.catnr == catnr:
                    e.source = "celestrak"
                    e.fetched_at = now
                    if name:
                        e.name = name
                    self._mem[catnr] = e
                    self._write_disk(e)
                    return e
            # 响应里没解析出目标卫星：不伪造，返回 None
            return None
        except Exception:
            # 4) 联网失败：回退本地磁盘缓存（即便过期也用，旧数据好过没有）
            if disk is not None:
                disk.source = "cache"
                self._mem[catnr] = disk
                return disk
            return None

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
