"""
MBDSDR AI 内核 - 真实日月行星位置模块
=====================================
算法参考 Stellarium SolarSystem (GPL-3.0), 用 skyfield/astropy 独立实现。

Stellarium 算法要点（源码引用）：
- src/core/planetsephems/EphemWrapper.cpp:290-295
    get_sun_helio_coordsv() 直接返回 (0,0,0)——太阳在日心系原点；
    地心太阳位置 = -地球日心位置。
- src/core/planetsephems/EphemWrapper.cpp:162-165
    行星日心坐标优先用 JPL DE430/431/440/441；若历表不在时间范围内，
    fallback 到 GetVsop87Coor()（VSOP87 级数展开）。
- src/core/planetsephems/EphemWrapper.cpp:344-352
    地球位置 = EMB（地月质心） - 月球位置 * 0.0121505677733761
    （mu_m/(1+mu_m)，mu_m=M_moon/M_earth≈0.01230002）。
- src/core/planetsephems/EphemWrapper.cpp:458-459
    月球 fallback 用 ELP2000-82B（GetElp82bCoor）；
    并有子角秒修正：经度 +0.50"，纬度 -0.25"（EphemWrapper.cpp:468-474）。
- src/core/modules/Planet.cpp:2525-2531
    J2000 地心赤道矢量 = (planetHelio - observerHelio + aberrationPush)
    再经 matVsop87ToJ2000 旋转矩阵转到 J2000。
- src/core/modules/Planet.cpp:3092-3100
    被照亮比例 = 0.5 * |1 + cos(相位角)|；
    相位角 = acos((obsPlanetR² + planetR² - obsR²) / (2*sqrt(obsPlanetR²*planetR²)))
    （Planet.cpp:3051-3058）。
- src/core/modules/SolarSystem.cpp:1598-1611
    光行时修正：迭代两轮，
    lightTimeDays = 距离_AU / (光速_km/s * 86400)；
    再按 t - lightTime 重算位置；光行差修正 aberrationPush = lightTime * 观测者速度。

本模块用 skyfield（JPL DE421 星历）作为首选后端——比手写 VSOP87 级数更可靠、
精度更高；当 .bsp 星历不可用时，自动 fallback 到 astropy builtin
（其内部正是 VSOP87 级数 + 月球简化理论，与 Stellarium 的 fallback 等价）。

时间参数兼容 new_spacetime.py TimeEngine：接受 skyfield Time、unix 时间戳、
datetime 或 JD。
"""

from __future__ import annotations

import math
import os
import shutil
import threading
import time as _time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple, Union

# ── 常量 ──────────────────────────────────────────────

AU_KM = 149597870.7          # 1 AU 公里数（IAU）
SPEED_OF_LIGHT_KM_S = 299792.458
J2000_JD = 2451545.0

# ── de421.bsp 星历自动下载 ──────────────────────────────
# JPL DE421 星历：覆盖 1900-2050，行星位置亚角秒级精度。
# 缓存目录与官方下载地址。下载在后台 daemon 线程进行，不阻塞导入/GUI。
EPHEMERIS_DIR = os.path.expanduser("~/.mbdsdr/ephemeris")
DE421_FILENAME = "de421.bsp"
DE421_PATH = os.path.join(EPHEMERIS_DIR, DE421_FILENAME)
DE421_URL = "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de421.bsp"
DE421_URL_MIRROR = "https://ssd.jpl.nasa.gov/ftp/eph/planets/bsp/de421.bsp"
# 完整 de421.bsp 约 17MB；小于 10MB 视为不完整/损坏。
DE421_MIN_BYTES = 10 * 1024 * 1024

# 各后端精度标注（arcsec = 角秒）。
# - de421_bsp : JPL 数值星历，行星位置亚角秒级 (< 0.1 arcsec)，月球约 0.01 arcsec。
# - vsop87    : VSOP87 级数 + 月球简化理论，太阳 ~1 arcsec，月球 ~10 arcsec。
_BACKEND_PRECISION = {
    "de421_bsp": "DE421 数值星历: 行星位置 <0.1 arcsec (亚角秒), 月球 ~0.01 arcsec",
    "vsop87_astropy": "VSOP87 级数回退: 太阳 ~1 arcsec, 月球 ~10 arcsec",
    "none": "无可用历表",
}

# 行星名 → skyfield/astropy 名称映射
_PLANET_NAMES = {
    "mercury":   ("mercury",   "水星"),
    "venus":     ("venus",     "金星"),
    "earth":     ("earth",     "地球"),
    "mars":      ("mars",      "火星"),
    "jupiter":   ("jupiter",   "木星"),
    "saturn":    ("saturn",    "土星"),
    "uranus":    ("uranus",    "天王星"),
    "neptune":   ("neptune",   "海王星"),
}

# 太阳角半径（度）在 1 AU 处：约 16.0' = 0.2667°
_SUN_ANGULAR_RADIUS_AT_1AU_DEG = 0.26667
# 月球角半径（度）在平均距离 384400 km 处：约 16.0'
_MOON_ANGULAR_RADIUS_MEAN_DEG = 0.2513

# ── 数据结构 ──────────────────────────────────────────

@dataclass
class BodyPosition:
    """天体位置结果。"""
    name: str
    ra_deg: float          # 赤经（度，J2000/视）
    dec_deg: float         # 赤纬（度）
    az_deg: float          # 方位角（度，0=北，顺时针）
    alt_deg: float         # 仰角（度）
    distance_au: Optional[float] = None   # 距离（AU）
    distance_km: Optional[float] = None   # 距离（公里）
    phase_angle_deg: Optional[float] = None   # 相位角（度，太阳-天体-观测者夹角）
    illumination: Optional[float] = None       # 被照亮比例 [0,1]
    angular_diameter_deg: Optional[float] = None  # 角直径（度）
    backend: str = "unknown"   # "de421_bsp" / "vsop87_astropy[回退]" / "none"
    precision: str = ""        # 当前后端精度说明（arcsec）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "ra_deg": round(self.ra_deg, 6),
            "dec_deg": round(self.dec_deg, 6),
            "az_deg": round(self.az_deg, 4),
            "alt_deg": round(self.alt_deg, 4),
            "distance_au": round(self.distance_au, 8) if self.distance_au is not None else None,
            "distance_km": round(self.distance_km, 2) if self.distance_km is not None else None,
            "phase_angle_deg": round(self.phase_angle_deg, 3) if self.phase_angle_deg is not None else None,
            "illumination": round(self.illumination, 4) if self.illumination is not None else None,
            "angular_diameter_deg": round(self.angular_diameter_deg, 4) if self.angular_diameter_deg is not None else None,
            "backend": self.backend,
            "precision": self.precision,
        }


@dataclass
class GroundStation:
    """地面站（观测者位置）。"""
    latitude_deg: float
    longitude_deg: float
    height_m: float = 0.0

    @classmethod
    def from_observer(cls, obs: Any) -> "GroundStation":
        """从 astronomy.Observer 构造。"""
        return cls(
            latitude_deg=float(obs.latitude_deg),
            longitude_deg=float(obs.longitude_deg),
            height_m=float(obs.height_m),
        )


# ── de421.bsp 下载与加载 ──────────────────────────────

def _file_valid(path: str) -> bool:
    """检查本地缓存 .bsp 是否存在且大小达标（> 10MB）。"""
    try:
        return os.path.isfile(path) and os.path.getsize(path) >= DE421_MIN_BYTES
    except OSError:
        return False


def download_de421(timeout: int = 60) -> Optional[str]:
    """下载（或复用本地缓存）de421.bsp，返回本地路径；失败返回 None，不抛异常。

    - 若 ``~/.mbdsdr/ephemeris/de421.bsp`` 已存在且 > 10MB，直接返回路径（不重复下载）。
    - 否则下载到同目录的 ``.part`` 临时文件，成功后原子重命名为 ``de421.bsp``。
    - 主地址失败自动尝试镜像；任意异常都会清理临时文件并返回 None。
    - ``timeout`` 为单条连接超时（秒），默认 60s。
    """
    os.makedirs(EPHEMERIS_DIR, exist_ok=True)
    target = os.path.join(EPHEMERIS_DIR, DE421_FILENAME)

    # 缓存命中：直接返回
    if _file_valid(target):
        return target

    tmp = target + ".part"
    headers = {"User-Agent": "mbdsdr-ephemeris/1.0 (+https://github.com)"}
    for url in (DE421_URL, DE421_URL_MIRROR):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f, length=1024 * 256)
            # 下载完整性校验
            if not _file_valid(tmp):
                raise IOError(
                    f"下载文件过小: {os.path.getsize(tmp) if os.path.exists(tmp) else 0} bytes"
                )
            os.replace(tmp, target)  # 原子重命名
            return target
        except Exception:
            # 清理临时文件，继续尝试下一个地址
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            continue
    return None


def load_de421_bsp(path: str) -> Optional["_EphemerisHandle"]:
    """加载 .bsp 星历，返回统一句柄；失败返回 None。

    优先用 skyfield（完整视位置/光行差/章动处理）；skyfield 不可用时
    退到 jplephem 直接读 SPK（仅 ICRS 地心矢量，精度仍为亚角秒，但
    不做大气/光行差修正）。
    """
    handle = _EphemerisHandle()
    try:
        from skyfield.api import Loader
        loader = Loader(os.path.dirname(path) or EPHEMERIS_DIR)
        eph = loader(os.path.basename(path))
        ts = loader.timescale()
        handle.engine = "skyfield"
        handle.ts = ts
        handle.eph = eph
        for key in ("sun", "moon", "mercury", "venus", "mars",
                    "jupiter", "saturn", "earth"):
            try:
                handle.planets[key] = eph[key]
            except Exception:
                pass
        return handle
    except Exception:
        pass

    # fallback: jplephem 直接加载
    try:
        from jplephem.spk import SPK
        handle.engine = "jplephem"
        handle.spk = SPK.open(path)
        return handle
    except Exception:
        return None


# NAIF 整数天体 ID（jplephem 直接读 SPK 时使用）
_JPLEPHEM_IDS = {
    "sun": 10, "mercury": 1, "venus": 2, "earth": 399,
    "mars": 4, "jupiter": 5, "saturn": 6, "uranus": 7, "neptune": 8,
    "moon": 301,
}


# ── 后端懒加载 ─────────────────────────────────────────

@dataclass
class _EphemerisHandle:
    """一次成功加载后的星历句柄（引擎无关）。"""
    engine: Optional[str] = None     # "skyfield" | "jplephem"
    ts: Any = None                   # skyfield timescale
    eph: Any = None                  # skyfield ephemeris
    planets: Dict[str, Any] = field(default_factory=dict)
    spk: Any = None                  # jplephem SPK


class _EphemerisBackend:
    """统一的历表后端封装。

    启动流程（不阻塞）：
      1. 若本地 ``~/.mbdsdr/ephemeris/de421.bsp`` 已存在且完整，立即加载为高精度后端；
      2. 否则先用 astropy builtin（VSOP87 级数）初始化可用后端，同时在 daemon 线程
         后台下载 de421.bsp；下载完成后通过 :meth:`_hot_swap_to_bsp` 热切换到高精度。

    后端名 :pyattr:`backend_name`：
      - ``de421_bsp``            : JPL DE421 数值星历（亚角秒精度）
      - ``vsop87_astropy[回退]`` : VSOP87 级数回退（太阳 ~1'', 月球 ~10''）
      - ``none``                  : 无可用历表
    """

    def __init__(self, auto_download: bool = True) -> None:
        self.kind: str = "none"          # "de421_bsp" | "vsop87_astropy" | "none"
        self._engine: Optional[str] = None
        self._handle: Optional[_EphemerisHandle] = None
        self._astropy = None             # astropy 模块引用
        self._lock = threading.RLock()
        self._download_thread: Optional[threading.Thread] = None
        self._init()
        if auto_download:
            self._maybe_start_background_download()

    # -- 初始化 ----------------------------------------------------

    def _init(self) -> None:
        # 1) 本地已有完整 de421.bsp → 直接加载高精度
        if _file_valid(DE421_PATH):
            handle = load_de421_bsp(DE421_PATH)
            if handle is not None:
                self._apply_handle(handle)
                return
        # 2) fallback: astropy builtin（VSOP87 级数，离线可用）
        self._init_astropy()

    def _init_astropy(self) -> None:
        try:
            import astropy
            import astropy.coordinates
            import astropy.units
            from astropy.time import Time as AstropyTime
            self._astropy = {
                "coord": astropy.coordinates,
                "units": astropy.units,
                "Time": AstropyTime,
            }
            with astropy.coordinates.solar_system_ephemeris.set("builtin"):
                t = AstropyTime("2025-01-01T00:00:00")
                astropy.coordinates.get_body("sun", t)
            self.kind = "vsop87_astropy"
        except Exception:
            self.kind = "none"
            self._astropy = None

    def _apply_handle(self, handle: _EphemerisHandle) -> None:
        """原子地切换到 .bsp 句柄（持锁）。"""
        with self._lock:
            self._handle = handle
            self._engine = handle.engine
            # 兼容旧引用，供 _position_skyfield 使用
            self._sf_ts = handle.ts
            self._sf_eph = handle.eph
            self._sf_planets = handle.planets
            self.kind = "de421_bsp"

    # -- 后台下载与热切换 ------------------------------------------

    def _maybe_start_background_download(self) -> None:
        """若本地无 de421.bsp，启动 daemon 线程后台下载（不阻塞）。"""
        with self._lock:
            if self.kind == "de421_bsp":
                return  # 已有高精度后端，无需下载
            if self._download_thread is not None and self._download_thread.is_alive():
                return
            th = threading.Thread(
                target=self._download_worker,
                name="de421-download",
                daemon=True,   # 程序退出时不阻塞、不报错
            )
            self._download_thread = th
        th.start()

    def _download_worker(self) -> None:
        try:
            path = download_de421()
        except Exception:
            path = None
        if not path:
            return  # 下载失败：保持当前回退后端，不抛异常
        try:
            handle = load_de421_bsp(path)
        except Exception:
            handle = None
        if handle is not None:
            self._hot_swap_to_bsp(handle)

    def _hot_swap_to_bsp(self, handle: Optional[_EphemerisHandle] = None) -> None:
        """后台下载完成后调用，把后端从回退热切换到 de421.bsp。

        不传 handle 时会从 :data:`DE421_PATH` 重新加载。
        """
        if handle is None:
            if not _file_valid(DE421_PATH):
                return
            handle = load_de421_bsp(DE421_PATH)
        if handle is not None:
            self._apply_handle(handle)

    # -- 对外属性 --------------------------------------------------

    @property
    def available(self) -> bool:
        """当前是否有高精度（或回退）历表可用。"""
        return self.kind != "none"

    @property
    def backend_name(self) -> str:
        """当前后端名：de421_bsp / vsop87_astropy[回退] / none。"""
        with self._lock:
            if self.kind == "de421_bsp":
                return "de421_bsp"
            if self.kind == "vsop87_astropy":
                return "vsop87_astropy[回退]"
            return "none"

    @property
    def precision_note(self) -> str:
        return _BACKEND_PRECISION.get(self.kind, _BACKEND_PRECISION["none"])

    @property
    def download_in_progress(self) -> bool:
        t = self._download_thread
        return t is not None and t.is_alive()


_backend: Optional[_EphemerisBackend] = None


def _get_backend() -> _EphemerisBackend:
    global _backend
    if _backend is None:
        _backend = _EphemerisBackend()
    return _backend


# ── 时间/站址归一化 ────────────────────────────────────

def _normalize_time(t: Any) -> Tuple[float, Any]:
    """
    把多种时间输入归一化为 (unix_seconds, astropy_Time_or_None)。

    接受：
    - float/int: unix 时间戳
    - datetime (aware): 直接用
    - datetime (naive): 当作 UTC
    - skyfield Time: 取 .utc_iso 或 .tt
    - JD (float > 2400000): 当作儒略日
    """
    if t is None:
        return _time.time(), None

    # skyfield Time
    if hasattr(t, "utc_iso"):
        try:
            dt = t.utc_datetime()
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp(), None
        except Exception:
            pass

    # datetime
    if isinstance(t, datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t.timestamp(), None

    # 数值
    if isinstance(t, (int, float)):
        v = float(t)
        # JD 判断：儒略日典型范围 2400000~2500000（≈2.4e6）；
        # unix 时间戳 ≈1.7e9。阈值取 1e8 区分两者。
        if 2000000.0 <= v <= 3000000.0:
            unix = (v - 2440587.5) * 86400.0
            return unix, None
        return v, None

    raise TypeError(f"不支持的时间类型: {type(t)}")


def _normalize_station(station: Any) -> GroundStation:
    """归一化地面站输入。"""
    if isinstance(station, GroundStation):
        return station
    # astronomy.Observer
    if hasattr(station, "latitude_deg") and hasattr(station, "longitude_deg"):
        return GroundStation.from_observer(station)
    # (lat, lon) 或 (lat, lon, elev)
    if isinstance(station, (tuple, list)):
        lat = float(station[0])
        lon = float(station[1])
        elev = float(station[2]) if len(station) > 2 else 0.0
        return GroundStation(lat, lon, elev)
    # dict
    if isinstance(station, dict):
        return GroundStation(
            latitude_deg=float(station.get("latitude_deg", station.get("lat", 0.0))),
            longitude_deg=float(station.get("longitude_deg", station.get("lon", 0.0))),
            height_m=float(station.get("height_m", station.get("elev_m", 0.0))),
        )
    raise TypeError(f"不支持的地面站类型: {type(station)}")


# ── skyfield 后端实现 ──────────────────────────────────

def _position_skyfield(body_key: str, t_unix: float,
                       station: GroundStation) -> Optional[BodyPosition]:
    be = _get_backend()
    if be._engine != "skyfield":
        return None
    from skyfield.api import Topos

    ts = be._sf_ts
    eph = be._sf_eph
    body = be._sf_planets.get(body_key)
    if body is None:
        return None

    # unix → skyfield Time
    t = ts.utc(datetime.fromtimestamp(t_unix, tz=timezone.utc))
    observer = eph["earth"] + Topos(
        latitude_deg=station.latitude_deg,
        longitude_deg=station.longitude_deg,
        elevation_m=station.height_m,
    )

    # astrometric().apparent() 给出视位置（含光行差、章动、岁差）
    # 对应 Stellarium Planet::getJ2000EquatorialPos() + aberrationPush
    astrometric = observer.at(t).observe(body)
    apparent = astrometric.apparent()
    ra_deg, dec_deg, distance_au = apparent.radec()
    ra_deg = ra_deg._degrees
    dec_deg = dec_deg._degrees
    distance_au = float(distance_au.au)

    # Alt/Az
    altaz = apparent.altaz()
    alt_deg = float(altaz.alt.degrees)
    az_deg = float(altaz.az.degrees)

    # 距离公里
    distance_km = distance_au * AU_KM

    return BodyPosition(
        name=body_key,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        az_deg=az_deg,
        alt_deg=alt_deg,
        distance_au=distance_au,
        distance_km=distance_km,
        backend="de421_bsp",
    )


def _moon_phase_skyfield(t_unix: float, station: GroundStation,
                         moon_pos: BodyPosition) -> Tuple[float, float]:
    """计算月相角和照亮比例（Stellarium Planet::getPhase, Planet.cpp:3092-3100）。"""
    be = _get_backend()
    from skyfield.api import Topos
    ts = be._sf_ts
    eph = be._sf_eph
    t = ts.utc(datetime.fromtimestamp(t_unix, tz=timezone.utc))
    observer = eph["earth"] + Topos(
        latitude_deg=station.latitude_deg,
        longitude_deg=station.longitude_deg,
        elevation_m=station.height_m,
    )

    # 地心太阳方向（单位矢量）
    sun = eph["sun"]
    earth = eph["earth"]
    sun_geo = earth.at(t).observe(sun).apparent()
    sun_vec = sun_geo.position.au  # 地心→太阳 (AU)

    # 地心月球方向
    moon = eph["moon"]
    moon_geo = earth.at(t).observe(moon).apparent()
    moon_vec = moon_geo.position.au  # 地心→月球 (AU)

    # 相位角：太阳-月球-观测者(地心) 夹角
    # cos(phase) = (moon_vec · sun_vec) / (|moon_vec| * |sun_vec|)
    dot = sum(a * b for a, b in zip(moon_vec, sun_vec))
    moon_norm = math.sqrt(sum(a * a for a in moon_vec))
    sun_norm = math.sqrt(sum(a * a for a in sun_vec))
    cos_phase = dot / (moon_norm * sun_norm)
    cos_phase = max(-1.0, min(1.0, cos_phase))
    phase_angle = math.degrees(math.acos(cos_phase))
    illumination = 0.5 * (1.0 + cos_phase)
    return phase_angle, illumination


# ── jplephem 后端实现（skyfield 不可用时的 .bsp 直接读取）───

def _unix_to_jd(t_unix: float) -> float:
    """unix 秒 → 儒略日（UT）。"""
    return 2440587.5 + t_unix / 86400.0


def _gmst_deg(jd_ut: float) -> float:
    """近似格林尼治平恒星时（度，IAU 1982 简化式）。"""
    t = (jd_ut - 2451545.0) / 36525.0
    gmst = (280.46061837
            + 360.98564736629 * (jd_ut - 2451545.0)
            + 0.000387933 * t * t
            - t * t * t / 38710000.0)
    return gmst % 360.0


def _icrs_xyz_to_radec_deg(x_km: float, y_km: float, z_km: float
                           ) -> Tuple[float, float, float]:
    """ICRS 直角坐标（km）→ (ra_deg, dec_deg, distance_au)。"""
    r = math.sqrt(x_km * x_km + y_km * y_km + z_km * z_km)
    ra = math.degrees(math.atan2(y_km, x_km)) % 360.0
    dec = math.degrees(math.asin(max(-1.0, min(1.0, z_km / r))))
    return ra, dec, r / AU_KM


def _radec_to_altaz(ra_deg: float, dec_deg: float, jd_ut: float,
                    lon_deg: float, lat_deg: float) -> Tuple[float, float]:
    """赤道坐标 → 地平坐标（地心近似，忽略周日视差，够用作回退）。"""
    lst = (_gmst_deg(jd_ut) + lon_deg) % 360.0
    ha = math.radians(lst - ra_deg)
    dec = math.radians(dec_deg)
    lat = math.radians(lat_deg)
    sin_alt = (math.sin(dec) * math.sin(lat)
               + math.cos(dec) * math.cos(lat) * math.cos(ha))
    alt = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
    # az: 0=北，顺时针
    y = -math.sin(ha) * math.cos(dec)
    x = (math.sin(dec) * math.cos(lat)
         - math.cos(dec) * math.sin(lat) * math.cos(ha))
    az = math.degrees(math.atan2(y, x)) % 360.0
    return alt, az


def _jplephem_geo_vector(spk: Any, body_id: int, jd: float) -> Tuple[float, float, float]:
    """返回 body 相对地心的 ICRS 矢量（km）。自动处理 EMB/地球链。"""
    earth_id = 399
    # 优先直接段
    def _try(a: int, b: int):
        try:
            return spk[a, b].compute(jd)
        except Exception:
            return None

    if body_id == earth_id:
        return (0.0, 0.0, 0.0)
    v = _try(body_id, earth_id)
    if v is not None:
        return tuple(float(c) for c in v)  # type: ignore[return-value]
    # 链：body wrt SSB - earth wrt SSB
    body_ssb = _try(body_id, 0)
    earth_ssb = _try(earth_id, 0)
    if body_ssb is None:
        # 经 EMB(3) 链
        emb = _try(3, 0)
        e_emb = _try(earth_id, 3)
        b_emb = _try(body_id, 3)
        if emb is None or b_emb is None:
            raise ValueError(f"无法定位天体 {body_id}")
        earth_ssb = tuple(a + b for a, b in zip(emb, e_emb)) if e_emb is not None else emb
        body_ssb = tuple(a + b for a, b in zip(emb, b_emb))
    return tuple(float(b - e) for b, e in zip(body_ssb, earth_ssb))  # type: ignore[return-value]


def _position_jplephem(body_key: str, t_unix: float,
                        station: GroundStation) -> Optional[BodyPosition]:
    """jplephem 引擎：ICRS 地心矢量 → RA/Dec/距离 → 近似 Alt/Az。"""
    be = _get_backend()
    if be._engine != "jplephem":
        return None
    spk = be._handle.spk
    body_id = _JPLEPHEM_IDS.get(body_key)
    if body_id is None:
        return None
    jd = _unix_to_jd(t_unix)
    try:
        v = _jplephem_geo_vector(spk, body_id, jd)
    except Exception:
        return None
    ra, dec, dist_au = _icrs_xyz_to_radec_deg(*v)
    alt, az = _radec_to_altaz(ra, dec, jd, station.longitude_deg, station.latitude_deg)
    return BodyPosition(
        name=body_key,
        ra_deg=ra, dec_deg=dec, az_deg=az, alt_deg=alt,
        distance_au=dist_au, distance_km=dist_au * AU_KM,
        backend="de421_bsp",
    )


def _moon_phase_jplephem(t_unix: float, station: GroundStation,
                         moon_pos: BodyPosition) -> Tuple[float, float]:
    """jplephem 引擎月相：用真实地心太阳/月球矢量算相位角。"""
    be = _get_backend()
    spk = be._handle.spk
    jd = _unix_to_jd(t_unix)
    sun_v = _jplephem_geo_vector(spk, 10, jd)
    moon_v = _jplephem_geo_vector(spk, 301, jd)
    dot = sum(a * b for a, b in zip(sun_v, moon_v))
    mn = math.sqrt(sum(a * a for a in moon_v))
    sn = math.sqrt(sum(a * a for a in sun_v))
    cos_phase = max(-1.0, min(1.0, dot / (mn * sn)))
    return math.degrees(math.acos(cos_phase)), 0.5 * (1.0 + cos_phase)


# ── astropy builtin 后端实现 ───────────────────────────

def _position_astropy(body_key: str, t_unix: float,
                      station: GroundStation) -> Optional[BodyPosition]:
    be = _get_backend()
    if be.kind != "vsop87_astropy":
        return None
    ap = be._astropy
    coord = ap["coord"]
    units = ap["units"]
    AstropyTime = ap["Time"]

    t = AstropyTime(datetime.fromtimestamp(t_unix, tz=timezone.utc))
    obs_loc = coord.EarthLocation(
        lon=station.longitude_deg * units.deg,
        lat=station.latitude_deg * units.deg,
        height=station.height_m * units.m,
    )

    with coord.solar_system_ephemeris.set("builtin"):
        body = coord.get_body(body_key, t, obs_loc)
        altaz = body.transform_to(coord.AltAz(obstime=t, location=obs_loc))

    ra_deg = float(body.ra.deg)
    dec_deg = float(body.dec.deg)
    az_deg = float(altaz.az.deg)
    alt_deg = float(altaz.alt.deg)
    distance_au = float(body.distance.au)
    distance_km = distance_au * AU_KM

    return BodyPosition(
        name=body_key,
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        az_deg=az_deg,
        alt_deg=alt_deg,
        distance_au=distance_au,
        distance_km=distance_km,
        backend="vsop87_astropy[回退]",
    )


def _moon_phase_astropy(t_unix: float, station: GroundStation,
                        moon_pos: BodyPosition) -> Tuple[float, float]:
    """astropy 后端月相。"""
    be = _get_backend()
    ap = be._astropy
    coord = ap["coord"]
    units = ap["units"]
    AstropyTime = ap["Time"]

    t = AstropyTime(datetime.fromtimestamp(t_unix, tz=timezone.utc))
    obs_loc = coord.EarthLocation(
        lon=station.longitude_deg * units.deg,
        lat=station.latitude_deg * units.deg,
        height=station.height_m * units.m,
    )

    with coord.solar_system_ephemeris.set("builtin"):
        sun = coord.get_body("sun", t, obs_loc)
        moon = coord.get_body("moon", t, obs_loc)

    # 地心方向矢量（用 RA/Dec/距离 转直角坐标）
    def _unit_vec(body):
        ra = math.radians(body.ra.deg)
        dec = math.radians(body.dec.deg)
        return (math.cos(dec) * math.cos(ra),
                math.cos(dec) * math.sin(ra),
                math.sin(dec))

    sun_v = _unit_vec(sun)
    moon_v = _unit_vec(moon)
    dot = sum(a * b for a, b in zip(sun_v, moon_v))
    dot = max(-1.0, min(1.0, dot))
    phase_angle = math.degrees(math.acos(dot))
    illumination = 0.5 * (1.0 + dot)
    return phase_angle, illumination


# ── 公共 API ──────────────────────────────────────────

def _compute_position(body_key: str, t_unix: float,
                      station: GroundStation) -> Optional[BodyPosition]:
    """按当前引擎派发位置计算（skyfield / jplephem / astropy）。"""
    be = _get_backend()
    if be._engine == "skyfield":
        return _position_skyfield(body_key, t_unix, station)
    if be._engine == "jplephem":
        return _position_jplephem(body_key, t_unix, station)
    return _position_astropy(body_key, t_unix, station)


def _compute_moon_phase(t_unix: float, station: GroundStation,
                         moon_pos: BodyPosition) -> Tuple[float, float]:
    be = _get_backend()
    if be._engine == "skyfield":
        return _moon_phase_skyfield(t_unix, station, moon_pos)
    if be._engine == "jplephem":
        return _moon_phase_jplephem(t_unix, station, moon_pos)
    return _moon_phase_astropy(t_unix, station, moon_pos)


def _stamp_backend(pos: BodyPosition) -> BodyPosition:
    """在结果上标注当前后端名与精度（arcsec）。"""
    be = _get_backend()
    pos.backend = be.backend_name
    pos.precision = be.precision_note
    return pos


def get_sun_position(time: Any, ground_station: Any) -> Optional[BodyPosition]:
    """
    获取太阳视位置。

    Stellarium 对应：EphemWrapper.cpp:290-295（太阳日心=原点）+
    Planet.cpp:2525-2531（地心= -地球日心 + 光行差）。

    参数:
        time: unix 时间戳 / datetime / skyfield Time / JD
        ground_station: GroundStation / Observer / (lat, lon, elev)

    返回:
        BodyPosition 或 None（历表不可用）
    """
    be = _get_backend()
    if not be.available:
        return None

    t_unix, _ = _normalize_time(time)
    station = _normalize_station(ground_station)

    pos = _compute_position("sun", t_unix, station)
    if pos is None:
        return None

    # 太阳角直径：随距离变化
    if pos.distance_au and pos.distance_au > 0:
        pos.angular_diameter_deg = 2.0 * _SUN_ANGULAR_RADIUS_AT_1AU_DEG / pos.distance_au
    pos.phase_angle_deg = 0.0  # 太阳被照亮 100%
    pos.illumination = 1.0
    return _stamp_backend(pos)


def get_moon_position(time: Any, ground_station: Any) -> Optional[BodyPosition]:
    """
    获取月球视位置（含相位、照度、角直径）。

    Stellarium 对应：EphemWrapper.cpp:458-459（ELP2000-82B）+
    Planet.cpp:3092-3100（被照亮比例 = 0.5*(1+cos_phase)）。

    返回:
        BodyPosition（含 phase_angle_deg, illumination, angular_diameter_deg）
    """
    be = _get_backend()
    if not be.available:
        return None

    t_unix, _ = _normalize_time(time)
    station = _normalize_station(ground_station)

    pos = _compute_position("moon", t_unix, station)
    if pos is None:
        return None
    phase_angle, illumination = _compute_moon_phase(t_unix, station, pos)

    pos.phase_angle_deg = phase_angle
    pos.illumination = illumination

    # 月球角直径：随距离变化（平均 384400 km 时约 0.502°）
    if pos.distance_km and pos.distance_km > 0:
        pos.angular_diameter_deg = 2.0 * _MOON_ANGULAR_RADIUS_MEAN_DEG * (384400.0 / pos.distance_km)

    return _stamp_backend(pos)


def get_planet_position(planet_name: str, time: Any,
                       ground_station: Any) -> Optional[BodyPosition]:
    """
    获取行星视位置。

    Stellarium 对应：EphemWrapper.cpp:162-165（DE4xx 优先，VSOP87 fallback）+
    SolarSystem.cpp:1598-1611（光行时迭代修正）。

    参数:
        planet_name: mercury/venus/mars/jupiter/saturn（不区分大小写）
        time: unix 时间戳 / datetime / skyfield Time / JD
        ground_station: GroundStation / Observer / (lat, lon, elev)

    返回:
        BodyPosition 或 None
    """
    be = _get_backend()
    if not be.available:
        return None

    name = planet_name.lower().strip()
    if name not in _PLANET_NAMES:
        raise ValueError(f"不支持的行星: {planet_name}，支持: {list(_PLANET_NAMES.keys())}")

    t_unix, _ = _normalize_time(time)
    station = _normalize_station(ground_station)

    pos = _compute_position(name, t_unix, station)
    if pos is None:
        return None

    # 行星相位角：地心看行星，太阳-行星-地球 夹角
    # 简化：用 RA/Dec 差估算（精确值需三维矢量，这里给近似）
    # Stellarium Planet.cpp:3051-3058 用日心矢量三角形
    try:
        sun_pos = get_sun_position(time, station)
        if sun_pos is not None and pos.distance_au and sun_pos.distance_au:
            # 角距离 ≈ 太阳与行星的角距
            ra_diff = math.radians(pos.ra_deg - sun_pos.ra_deg)
            dec_mean = math.radians(0.5 * (pos.dec_deg + sun_pos.dec_deg))
            # 近似角距离
            cos_sep = (math.sin(math.radians(pos.dec_deg)) * math.sin(math.radians(sun_pos.dec_deg))
                       + math.cos(math.radians(pos.dec_deg)) * math.cos(math.radians(sun_pos.dec_deg))
                       * math.cos(ra_diff))
            cos_sep = max(-1.0, min(1.0, cos_sep))
            elongation = math.degrees(math.acos(cos_sep))
            # 相位角近似：cos(phase) ≈ -cos(elongation) * (r_planet/r_earth) 修正
            # 这里用简化公式
            r_p = pos.distance_au
            r_sun = sun_pos.distance_au
            # 地心行星距离
            r_geo = pos.distance_au  # skyfield/astropy 返回的是地心距离
            # 相位角余弦
            cos_chi = (r_geo * r_geo + r_sun * r_sun - r_p * r_p) / (2.0 * r_geo * r_sun)
            cos_chi = max(-1.0, min(1.0, cos_chi))
            pos.phase_angle_deg = math.degrees(math.acos(cos_chi))
            pos.illumination = 0.5 * (1.0 + cos_chi)
    except Exception:
        pass

    return _stamp_backend(pos)


def get_backend_info() -> Dict[str, Any]:
    """返回当前历表后端信息（调试用）。"""
    be = _get_backend()
    return {
        "backend": be.backend_name,
        "kind": be.kind,
        "available": be.available,
        "precision": be.precision_note,
        "download_in_progress": be.download_in_progress,
        "ephemeris_path": DE421_PATH if _file_valid(DE421_PATH) else None,
        "planets_loaded": list(be._sf_planets.keys()) if be._engine == "skyfield" else [],
    }


# ── 便捷：地面站参考示例（非默认值）─────────────────────────────
# 以下为常见城市坐标参考，仅供用户查阅/手动选用。
# 系统不内置任何默认站址；实际使用时请在 ~/.mbdsdr/config.json
# 设置 ground_station_lat / ground_station_lon，或在调用时显式传入。

# 长春（参考示例）
CHANGCHUN = GroundStation(latitude_deg=43.817, longitude_deg=125.323, height_m=200.0)

# 北京（参考示例）
BEIJING = GroundStation(latitude_deg=39.904, longitude_deg=116.407, height_m=50.0)

# 上海（参考示例）
SHANGHAI = GroundStation(latitude_deg=31.230, longitude_deg=121.474, height_m=10.0)
