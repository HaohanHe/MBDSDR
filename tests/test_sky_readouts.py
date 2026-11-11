# SPDX-License-Identifier: MIT
"""
tests/test_sky_readouts.py
==========================
「屏幕上每个数字的可信度」（第五轮）：

  1. 读数交叉对拍矩阵：天空界面显示的角度/距离/星等/视直径/相位角/照度/
     升降时刻/过境峰值，逐个用「同一时刻、同一站址、同一后端源头」独立
     算一遍，并把**经格式化后的显示值**解析回来对拍——容差按显示精度
     （半格），不允许「显示精度把误差藏起来」。
  2. 单位与时区：角度写 °、方位同时写 16 方位名、距离写 km/AU+万·亿km、
     星等写 mag、角径写 ″/′；升降/过境时间行标签写明 UTC，另给本地时刻
     与 UTC 偏移（偏移由 tzinfo 现算，夏令时切换自动正确）。
  3. 误导性舍入：0.04° 不显示 0°、99.6% 不显示 100%、0.04% 不显示 0.0%、
     1.4″/1.4′ 至少保留一位小数——全部用测试钉住。

所有格式化只经 rf_sky_view 里的那一份实现（本文件对绘制源做单点核查）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_sky_readouts.py -v
"""
import os
import re
import sys
import inspect
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import math  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import rf_sky_view as rsv  # noqa: E402
from rf_sky_view import (  # noqa: E402
    RFSkyView, format_angle_deg, format_azimuth, format_arcmin, format_arcsec,
    format_distance_au, format_distance_km, format_magnitude, format_percent,
    format_speed_kms, format_utc_time, format_local_time, format_hms_ra,
    format_dms_deg, format_view_axis, local_sidereal_time_deg,
    star_catalog_credit_text,
    format_satellite_description, compass_point,
)
from mbdsdr_ai.decoders import BUILTIN_TLE  # noqa: E402
from mbdsdr_ai.new_spacetime import get_time_engine  # noqa: E402
from mbdsdr_ai import orbit  # noqa: E402
from mbdsdr_ai.skyengine import jtime as _jtime  # noqa: E402

# 测试锚点站址（SGP4 地面站夹具坐标，不代表任何个人位置）
LAT, LON = 43.9, 125.3
ISS = "ISS (ZARYA)"
NOAA19 = "NOAA 19"
LINE1_ISS, LINE2_ISS = BUILTIN_TLE[ISS]
LINE1_N19, LINE2_N19 = BUILTIN_TLE[NOAA19]

# 与快转用例同一锚点：ISS 仰角≈55°，避免地平附近的折射/几何退化
UNIX = 1790532300.0

# 显示精度（半格容差）：角度/角径 1 位小数=0.05；距离 2 位=0.005
TOL_DEG = 0.05 + 1e-6
TOL_DIST_KM = 0.005 + 1e-6
TOL_MAG = 0.005 + 1e-6
TOL_ARCSEC = 0.05 + 1e-6
TOL_ARCMIN = 0.05 + 1e-6
TOL_AU3 = 0.0005 + 1e-9
TOL_LY = 0.05 + 1e-6


def _num(txt: str) -> float:
    """取显示串里的第一个数值（含 <x 前缀时返回 x，由调用方另判）。"""
    m = re.search(r"[+-]?\d+(?:\.\d+)?", txt)
    assert m, f"显示串里没有数值: {txt!r}"
    return float(m.group(0))


def _circ_diff(a: float, b: float) -> float:
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def _jd(unix_s: float) -> float:
    return unix_s / 86400.0 + 2440587.5


def _state_at(name: str, line1: str, line2: str, unix_s: float):
    """与生产同一后端链（sgp4 + orbit._state_from_satrec）的独立对拍值。"""
    from sgp4.api import Satrec
    sat = Satrec.twoline2rv(line1, line2)
    return orbit._state_from_satrec(
        sat, name, _jd(unix_s), LAT, LON, 0.0,
        epoch=line1[18:32].strip() if len(line1) > 32 else "")


def _catalog() -> list:
    return [
        {"name": ISS, "line1": LINE1_ISS, "line2": LINE2_ISS,
         "freq_mhz": 145.8, "kind": "amateur"},
        {"name": NOAA19, "line1": LINE1_N19, "line2": LINE2_N19,
         "freq_mhz": 137.1, "kind": "weather"},
    ]


@pytest.fixture
def engine():
    eng = get_time_engine()
    eng.set_unix(UNIX)
    eng.pause()
    yield eng
    eng.set_time_now()
    eng.set_rate(1.0)


@pytest.fixture
def sky(engine, monkeypatch):
    """真实 SatelliteTracker（TLE 加载器替身，不依赖网络/用户目录）。"""
    monkeypatch.setattr(rsv, "_load_user_tle_catalog", lambda: [])
    monkeypatch.setattr(rsv, "_load_celestrak_tle_catalog", lambda: _catalog())
    v = RFSkyView()
    v.resize(900, 900)
    v.set_observer(LAT, LON, 0.0)
    trk = rsv.SatelliteTracker(v, LAT, LON, 0.0, interval_ms=10 ** 9)
    trk.refresh()
    return v, trk


# ---------------------------------------------------------------------------
# 1. 误导性舍入：临界处必须诚实（多给精度 / 明示"小于精度"）
# ---------------------------------------------------------------------------
class TestRoundingHonesty:
    def test_tiny_angle_not_shown_as_zero(self):
        assert format_angle_deg(0.04) == "<0.1°"
        assert format_angle_deg(-0.04) == ">-0.1°"
        assert format_angle_deg(0.0) == "0.0°"          # 真 0 才写 0
        assert format_angle_deg(0.06) == "0.1°"         # 超过半格正常进位

    def test_percent_never_rounds_996_to_100(self):
        assert format_percent(0.996, digits=0) == "99.6%"
        assert format_percent(0.99996, digits=0) == "99.996%"
        assert format_percent(1.0, digits=0) == "100%"     # 真到 100 才写
        assert format_percent(0.0004, digits=0) == "0.04%"     # 0.04% 不写 0%
        assert format_percent(0.0) == "0.0%"
        assert format_percent(1.0) == "100.0%"

    def test_angular_size_keeps_one_decimal(self):
        assert format_arcsec(1.4) == "1.4″"
        assert format_arcmin(1.4) == "1.4′"
        assert format_arcsec(0.04) == "<0.1″"      # 不把 0.04″ 写成 0″
        assert format_arcmin(0.04) == "<0.1′"

    def test_azimuth_wraps_and_names_compass(self):
        assert format_azimuth(0.0) == "0.0° N"
        assert format_azimuth(45.0) == "45.0° NE"
        assert format_azimuth(210.3).endswith("SW")
        # 359.96° 舍入后是 360.0°：折回 0.0° N，不显示越界的 360°
        assert format_azimuth(359.96) == "0.0° N"
        assert compass_point(180.0) == "S"
        assert compass_point(359.0) == "N"

    def test_one_display_unit_is_visible(self):
        """相邻一个显示单位必须产生不同字符串（精度不把差异吃掉）。"""
        pairs = [
            (format_angle_deg, 10.0, 0.1),
            (format_distance_km, 1000.0, 0.01),
            (format_magnitude, 3.0, 0.01),
            (format_arcsec, 10.0, 0.1),
            (format_arcmin, 10.0, 0.1),
        ]
        for fn, base, step in pairs:
            assert fn(base) != fn(base + 1.5 * step), (fn.__name__, base)


# ---------------------------------------------------------------------------
# 2. 时区与单位：UTC/本地偏移随 tzinfo（含夏令时）正确
# ---------------------------------------------------------------------------
class TestUnitsAndTimezone:
    def test_local_offset_follows_dst(self):
        ny = ZoneInfo("America/New_York")
        jan = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        jul = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
        assert format_local_time(jan, ny) == "07:00:00 (UTC-05:00)"  # EST
        assert format_local_time(jul, ny) == "08:00:00 (UTC-04:00)"  # EDT
        assert format_local_time(jan, ZoneInfo("Asia/Shanghai")) \
            == "20:00:00 (UTC+08:00)"

    def test_utc_formatter_is_utc(self):
        jan = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        assert format_utc_time(jan, "%H:%M") == "12:00"
        # 本地行必须与 UTC 行不同（不能把本地时间当 UTC 显示）
        assert format_local_time(jan, ZoneInfo("Asia/Shanghai"), "%H:%M") \
            != format_utc_time(jan, "%H:%M")

    def test_satellite_description_has_units(self):
        txt = format_satellite_description(55.07, 1234.5, 3.456)
        assert txt == "仰角 55.1° · 距离 1234.50 km · 径向速度 +3.46 km/s"


# ---------------------------------------------------------------------------
# 3. 卫星读数：显示值 vs 同一时刻/站址的独立 SGP4 结果
# ---------------------------------------------------------------------------
class TestSatelliteReadouts:
    def test_object_readout_matches_backend(self, engine, sky):
        v, trk = sky
        obj = next(o for o in v._objects if o.name == ISS)
        t = engine.now_unix()
        st = _state_at(ISS, LINE1_ISS, LINE2_ISS, t)
        assert st is not None

        az, el = float(st["azimuth"]), float(st["elevation"])
        km = float(st["range_km"])
        rr = float(st.get("range_rate_kms", 0.0))

        # 画出来的标签值（格式化后）与后端值：误差 ≤ 半格显示精度
        assert _circ_diff(_num(format_azimuth(obj.azimuth_deg)), az) <= TOL_DEG
        assert abs(_num(format_angle_deg(obj.elevation_deg)) - el) <= TOL_DEG
        assert abs(_num(format_distance_km(_parse_km(obj))) - km) <= TOL_DIST_KM
        assert abs(_num(format_speed_kms(_rr(obj))) - rr) <= 0.005
        # 内部值本身也必须是当帧后端值（不是缓存/上一帧）
        assert abs(obj.elevation_deg - el) < 1e-9
        assert abs(obj.azimuth_deg - az) < 1e-9

        # 对象文案（视图快转重算与 tracker 同一口径）逐项对拍
        desc = obj.description or ""
        assert format_angle_deg(el) in desc
        assert format_distance_km(km) in desc
        assert format_speed_kms(rr) in desc

        # 信息卡 Distance 是解析后的 km：不能因格式化丢精度
        v._build_selected_info(obj)
        assert abs(float(v._selected_info["dist_km"]) - km) <= TOL_DIST_KM
        assert format_distance_km(v._selected_info["dist_km"]) \
            == format_distance_km(km)

    def test_card_rise_set_shown_in_utc(self, engine, sky):
        v, trk = sky
        obj = next(o for o in v._objects if o.name == ISS)
        v._build_selected_info(obj)
        info = v._selected_info
        assert info["rise"] is not None and info["set"] is not None
        rise_u = _jtime.jd_to_unix(info["rise"])
        expect = datetime.fromtimestamp(rise_u, tz=timezone.utc) \
            .strftime("%H:%M")
        assert format_utc_time(rise_u) == expect
        # UTC 与本地（+08:00）确实不同：证明行里标的 UTC 不是本地时间
        local = format_local_time(rise_u, ZoneInfo("Asia/Shanghai"), "%H:%M")
        assert local != expect
        # 真实渲染锁：卡里画出来的是 UTC 时刻，不是本地时刻
        v._hovered_object = obj
        rec = _TextRecorder()
        v._draw_picked_info(rec)
        assert "升起/落下 (UTC)" in rec.texts
        joined = "\n".join(rec.texts)
        assert f"升 {format_utc_time(rise_u)}" in joined
        assert format_local_time(rise_u, ZoneInfo("Asia/Shanghai"),
                                 "%H:%M") not in joined


def _parse_km(obj):
    m = re.search(r"距离\s*([0-9,]+(?:\.[0-9]+)?)\s*km", obj.description or "")
    assert m, obj.description
    return float(m.group(1).replace(",", ""))


def _rr(obj):
    m = re.search(r"径向速度\s*([+-]?[0-9.]+)\s*km/s", obj.description or "")
    assert m, obj.description
    return float(m.group(1))


# ---------------------------------------------------------------------------
# 4. 行星/月亮/恒星/DSO/太阳读数：显示串 vs 历表同一源头
# ---------------------------------------------------------------------------
class TestBodyReadouts:
    def test_planet_fields_match_ephemeris(self, engine):
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        jd = v._current_jd()
        pos = v._planet_positions(jd)["mars"]
        v._build_body_info({"kind": "planet", "key": "mars"})
        info = v._body_info
        assert info["mag"] == pytest.approx(float(pos.magnitude), abs=1e-12)
        assert _num(format_magnitude(info["mag"])) == pytest.approx(
            float(pos.magnitude), abs=TOL_MAG)
        # 距离：AU 与万/亿 km 同一事实源
        assert _num(info["dist_text"]) == pytest.approx(
            float(pos.distance_au), abs=TOL_AU3)
        assert "AU" in info["dist_text"]
        assert ("万 km" in info["dist_text"]) or ("亿 km" in info["dist_text"])
        # 视直径 / 相位角：显示串必须能解析回历表值
        assert _num(format_arcsec(float(pos.angular_diameter_arcsec))) \
            == pytest.approx(float(pos.angular_diameter_arcsec), abs=TOL_ARCSEC)
        assert _num(format_angle_deg(float(pos.phase_angle_deg))) \
            == pytest.approx(float(pos.phase_angle_deg), abs=TOL_DEG)
        assert format_arcsec(float(pos.angular_diameter_arcsec)) in info["desc"]
        assert format_angle_deg(float(pos.phase_angle_deg)) in info["desc"]

    def test_moon_illumination_matches_phase_angle(self, engine):
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        jd = v._current_jd()
        pos = v._planet_positions(jd)["moon"]
        illum = 0.5 * (1.0 + math.cos(math.radians(pos.phase_angle_deg)))
        v._build_body_info({"kind": "planet", "key": "moon"})
        info = v._body_info
        assert format_percent(illum) in info["desc"]
        assert _num(format_percent(illum)) == pytest.approx(illum * 100.0,
                                                            abs=0.05)

    def test_star_distance_matches_catalog(self, engine):
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        idx = rsv._constel.merged_star_index()
        name = next((n for n, s in idx.items() if s.dist_ly > 0), None)
        assert name, "星表里应至少有一颗带距离的恒星"
        star = idx[name]
        v._build_body_info({"kind": "star", "key": name})
        info = v._body_info
        assert info["dist_text"].endswith("ly")
        assert _num(info["dist_text"]) == pytest.approx(star.dist_ly,
                                                        abs=TOL_LY)
        assert _num(format_magnitude(info["mag"])) == pytest.approx(
            float(star.vmag), abs=TOL_MAG)

    def test_dso_size_matches_catalog(self, engine):
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        entry = v._dso_entry("M31")
        assert entry is not None
        v._build_body_info({"kind": "dso", "key": "M31"})
        info = v._body_info
        assert format_arcmin(entry.size_arcmin) in info["desc"]
        assert _num(format_arcmin(entry.size_arcmin)) == pytest.approx(
            float(entry.size_arcmin), abs=TOL_ARCMIN)
        assert _num(format_magnitude(info["mag"])) == pytest.approx(
            float(entry.magnitude), abs=TOL_MAG)

    def test_sun_fields_match_solar_system_feed(self, engine):
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        feed = {"az": 180.0, "alt": 30.0, "ra_deg": 200.0, "dec_deg": -10.0,
                "distance_au": 0.9983, "diameter_deg": 0.5325}
        v.set_celestial_bodies(feed, None, 0.0)
        v._build_body_info({"kind": "sun"})
        info = v._body_info
        assert _num(info["dist_text"]) == pytest.approx(0.9983, abs=5e-6)
        assert "AU" in info["dist_text"]
        assert format_arcmin(0.5325 * 60.0) in info["desc"]
        assert _num(format_arcmin(0.5325 * 60.0)) == pytest.approx(31.95,
                                                                   abs=TOL_ARCMIN)
        assert _num(format_magnitude(info["mag"])) == pytest.approx(
            -26.74, abs=TOL_MAG)


# ---------------------------------------------------------------------------
# 5. 过境面板：峰值仰角/时刻与天空同一模拟时刻一致，显示不藏误差
# ---------------------------------------------------------------------------
class TestPassPanelReadouts:
    def test_peak_alt_and_time_match_sgp4(self, engine, sky):
        v, trk = sky
        rows = v._upcoming_passes
        assert rows, "真实 TLE 夹具在 24h 内应至少有一次过境"
        row = rows[0]
        peak = float(row["peak_unix"])
        name = str(row["name"])
        st = _state_at(name, *BUILTIN_TLE[name], peak)
        assert st is not None
        true_el = float(st["elevation"])

        # 内部值（面板数字）与同一时刻独立 SGP4 的差 ≤ 1 格显示精度
        assert abs(float(row["max_alt"]) - true_el) <= TOL_DEG
        # 显示值解析回来也必须在容差内（不是内部对、屏幕错）
        shown = _num(format_angle_deg(float(row["max_alt"])))
        assert abs(shown - true_el) <= TOL_DEG
        # 峰值时刻显示到秒且与 peak_unix 的 UTC 一致
        assert row["rise"] == format_utc_time(peak, "%H:%M:%S")

    def test_pass_panel_labels_and_single_formatter(self):
        # 真实渲染：注入 6h 窗口，标题必须跟着变成 6h（写死 24h 会立刻红）
        v = RFSkyView()
        v._upcoming_passes = [{"name": "ISS", "rise": "12:00:00",
                               "max_alt": 30.0}]
        v._pass_gate_deg = 10.0
        v._pass_show_low = False
        v._pass_window_hours = 6.0
        rec = _TextRecorder()
        v._draw_passes_panel(rec)
        texts = "\n".join(rec.texts)
        assert "未来过境 6h" in texts
        assert "24h" not in texts
        assert "峰值≥10°" in texts
        assert "峰值(几何)" not in texts
        # 行内角度走唯一格式化器：30.0°，不是 "30 deg"
        assert "30.0°" in texts
        assert all(" deg" not in t for t in rec.texts), rec.texts


# ---------------------------------------------------------------------------
# 6. 单位标注 + 格式化单点实现（按渲染结果核查）
# ---------------------------------------------------------------------------
class _TextRecorder:
    """假画家：记录所有 drawText 的字符串，其余绘制调用空操作。"""

    def __init__(self):
        self.texts = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def drawText(self, *args):  # noqa: N802 (Qt 命名)
        for arg in args:
            if isinstance(arg, str):
                self.texts.append(arg)
                return


class TestSingleFormatterAndUnitLabels:
    def test_picked_satellite_card_units(self, sky):
        """渲染选中卫星卡，逐行核对「标签 + 与格式化函数一致的显示值」。"""
        v, _trk = sky
        obj = next(o for o in v._objects if o.name == ISS)
        v._build_selected_info(obj)
        info = v._selected_info
        assert info["rise"] is not None and info["set"] is not None
        # 用可控读数钉住每一行的显示值，避免依赖后端偶然数值
        info["mag"] = 1.23
        info["ra"] = 123.456
        info["dec"] = -45.678
        v._hovered_object = obj

        rec = _TextRecorder()
        v._draw_picked_info(rec)
        texts = rec.texts

        rise_u = _jtime.jd_to_unix(info["rise"])
        set_u = _jtime.jd_to_unix(info["set"])
        expect = {
            "星等 (mag)": format_magnitude(info["mag"]),
            "距离 (km)": format_distance_km(info["dist_km"]),
            "升起/落下 (UTC)": f"升 {format_utc_time(rise_u)}   "
                              f"落 {format_utc_time(set_u)}",
            "赤经/赤纬": f"{format_hms_ra(info['ra'])}  "
                        f"{format_dms_deg(info['dec'])}",
            "方位/高度（几何，0°=北）": f"{format_azimuth(obj.azimuth_deg)}  "
                                      f"{format_angle_deg(obj.elevation_deg)}",
        }
        for label, value in expect.items():
            assert label in texts, f"信息卡缺少行标签: {label!r}"
            assert value in texts, (
                f"信息卡 {label!r} 的显示值不是格式化函数的输出: "
                f"期望 {value!r}，实际文本 {texts!r}")

        # 方位同时给 16 方位名，高度走统一 ° 格式
        az_line = expect["方位/高度（几何，0°=北）"]
        assert compass_point(obj.azimuth_deg) in az_line
        assert az_line.endswith(format_angle_deg(obj.elevation_deg))
        # 赤经/赤纬各自走 hms/dms 单点实现（不是本地拼的 °′″）
        assert "h " in expect["赤经/赤纬"]
        assert '"' in expect["赤经/赤纬"]
        # 升降时刻按 UTC 显示（本地时刻不应出现在卡里）
        assert format_local_time(rise_u) not in texts

    def test_body_card_units(self, engine):
        """渲染天体卡：行标签与显示值都必须是真实格式化路径的输出。"""
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        v._picked_body = {"kind": "planet", "key": "moon"}
        v._build_body_info(v._picked_body)
        rec = _TextRecorder()
        v._draw_body_info(rec)
        texts = rec.texts
        info = v._body_info
        assert "星等 (mag)" in texts
        assert "升起/落下 (UTC)" in texts
        assert "方位/高度（视/几何，0°=北）" in texts
        assert format_magnitude(info["mag"]) in texts
        assert (f"{format_hms_ra(info['ra'])}  "
                f"{format_dms_deg(info['dec'])}") in texts
        # 视/几何高度同给，角度只走 ° 单点实现，不出现 "deg"
        azalt = next(t for t in texts if "几何" in t and "视(" in t)
        assert "°" in azalt and "ALT" not in azalt
        assert all(" deg" not in t for t in texts), texts

    def test_status_card_states_utc_and_local_offset(self, engine):
        """渲染状态卡：UTC 与本地（带偏移）两个时刻都要真的画出来。

        2026-11-06 口径变更：状态卡的 UTC/本地不再读注入的墙钟，而是**统一由
        TimeEngine 反算**（同一张卡上的 UTC / 本地 / 恒星时 / JD 必须同基）。
        本用例里引擎被固定在 UNIX（≠ 当前墙钟），因此角标正确显示「模拟」而不是
        时间源标签——这正是修掉"两个时间基准分叉"后应有的表现。
        """
        import datetime as _dt
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        utc_dt = _dt.datetime.fromtimestamp(UNIX, tz=_dt.timezone.utc)
        v._time_info = {"utc_time": utc_dt}
        rec = _TextRecorder()
        v._draw_info_overlay(rec)
        texts = rec.texts
        assert "UTC" in texts
        assert "本地" in texts
        # 引擎时刻（= UNIX，模拟）必须画出来，且明确标注不是真实当前时刻
        assert f"{format_utc_time(utc_dt, '%H:%M:%S')} [模拟]" in texts
        assert format_local_time(utc_dt) in texts
        axis = next(t for t in texts if t.startswith("AZ ") and "· ALT " in t)
        assert axis.endswith("°")
        assert all(" deg" not in t for t in texts), texts

    def test_no_ad_hoc_numeric_formatting_left(self):
        """全文件只有一套时间/角度格式化（禁止各处自己 strftime/拼度数）。"""
        src = inspect.getsource(rsv)
        assert src.count("strftime(") == 3        # UTC / UTC datetime / local
        assert ":4.0f} deg" not in src
        assert ":.1f}°" not in src
        # 度分秒只允许一处实现（format_dms_deg）
        assert src.count(':3d}°{m:02d}') == 1
        assert ":.0f} km" not in src


# ---------------------------------------------------------------------------
# 7. 时空读数补全（批 1：恒星时/JD、视轴、星表署名）
# ---------------------------------------------------------------------------
class TestSpacetimeReadouts:
    """原型 HUD 有 LST/JD/视轴，本仓状态卡补上后必须可独立复算（H-6）。"""

    def test_lst_matches_astronomy_backend(self):
        jd = _jd(UNIX)
        lst_deg = local_sidereal_time_deg(jd, LON)
        from mbdsdr_ai.astronomy import jd_to_lst
        expect = math.degrees(jd_to_lst(jd, LON))
        assert _circ_diff(lst_deg, expect) <= 0.05 / 240.0   # 半格 = 0.05 秒时角

    def test_lst_display_roundtrip_half_grid(self):
        jd = _jd(UNIX)
        shown = format_hms_ra(local_sidereal_time_deg(jd, LON), digits=1)
        m = re.fullmatch(r"(\d{2})h (\d{2})m (\d{2}\.\d)s", shown)
        assert m, f"LST 显示格式异常: {shown!r}"
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        shown_deg = (h + mi / 60.0 + s / 3600.0) * 15.0
        assert _circ_diff(shown_deg, local_sidereal_time_deg(jd, LON)) \
            <= 0.05 * 15.0 / 3600.0

    def test_view_axis_readout_formats_both_fields(self):
        txt = format_view_axis(123.4, 45.0)
        assert txt.startswith("AZ 123.4°")
        assert "SE" in txt                 # 16 方位名与度数同给
        assert "ALT 45.0°" in txt
        assert "几何" not in txt             # 口径在行标签上，不重复塞进值里

    def test_card_rows_use_single_fact_source(self, engine):
        """渲染状态卡：恒星时/视轴/星表来源都来自单一事实源，无站址给空态。"""
        v = RFSkyView()
        v.set_observer(LAT, LON, 0.0)
        rec = _TextRecorder()
        v._draw_info_overlay(rec)
        texts = rec.texts
        assert "恒星时" in texts
        assert "视轴(几何)" in texts
        assert "星表来源" in texts
        joined = "\n".join(texts)
        assert format_hms_ra(local_sidereal_time_deg(_jd(UNIX), LON),
                             digits=1) in joined
        assert any(t.startswith("AZ ") and "· ALT " in t for t in texts)
        assert star_catalog_credit_text() in texts
        # 无站址：LST 必须显式空态，不许拿 0° 冒充
        v2 = RFSkyView()
        rec2 = _TextRecorder()
        v2._draw_info_overlay(rec2)
        assert "—（无站址）" in rec2.texts

    def test_catalog_credit_from_meta(self):
        txt = star_catalog_credit_text()
        assert "HYG" in txt and "4.1" in txt
        assert "CC BY-SA 4.0" in txt
        assert "Database" not in txt       # 显示短名，不改来源身份


# ---------------------------------------------------------------------------
# 8. 状态卡超高滚动（批 2：卡片只占视口高度，不无限长挡住天空）
# ---------------------------------------------------------------------------
class _RoundedRectRecorder:
    """假画家：除记录 drawRoundedRect(QRectF, ...) 外全部是空操作。"""

    def __init__(self):
        self.rects = []

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def drawRoundedRect(self, *args):  # noqa: N802 (Qt 命名)
        from PySide6.QtCore import QRectF
        if args and isinstance(args[0], QRectF):
            self.rects.append(QRectF(args[0]))


class TestStatusCardScroll:
    """内容 330px 但窗口更矮时，卡片收缩并滚动；滚轮在卡内只滚卡不缩放。"""

    @staticmethod
    def _render(v):
        from PySide6.QtGui import QColor, QImage, QPainter
        img = QImage(v.width(), v.height(), QImage.Format_ARGB32)
        img.fill(QColor(0, 0, 0))
        p = QPainter(img)
        try:
            v._draw_info_overlay(p)
        finally:
            p.end()
        return img

    def _short_view(self, monkeypatch):
        # 窗口最小 400px；把内容抬到 600px 才能稳定制造「超高」场景。
        monkeypatch.setattr(RFSkyView, "INFO_CARD_H", 600)
        v = RFSkyView()
        v.resize(900, 400)
        v.set_observer(LAT, LON, 0.0)
        self._render(v)
        return v

    def test_short_window_card_stops_at_viewport_bottom(self, monkeypatch):
        v = self._short_view(monkeypatch)
        try:
            assert v._status_card_rect.bottom() <= v.height() - 12 + 1e-6
            assert v._status_card_view_h == pytest.approx(v.height() - 24)
            assert v._status_card_max_scroll() == pytest.approx(
                RFSkyView.INFO_CARD_H - v._status_card_view_h)
        finally:
            v.close()

    def test_tall_window_needs_no_scroll(self):
        v = RFSkyView()
        try:
            v.resize(900, 900)
            v.set_observer(LAT, LON, 0.0)
            self._render(v)
            assert v._status_card_view_h == pytest.approx(RFSkyView.INFO_CARD_H)
            assert v._status_card_max_scroll() == 0.0
            assert v._status_card_scroll == 0.0
        finally:
            v.close()

    def test_wheel_inside_card_scrolls_without_zooming(self, monkeypatch):
        v = self._short_view(monkeypatch)
        try:
            fov0 = v.view_state.fov_deg
            c = v._status_card_rect.center()
            assert v.wheelEvent({"x": c.x(), "y": c.y(), "delta": -120}) is None
            assert v._status_card_scroll == pytest.approx(36.0)
            assert v.view_state.fov_deg == pytest.approx(fov0)
            v.wheelEvent({"x": c.x(), "y": c.y(), "delta": 120})
            assert v._status_card_scroll == pytest.approx(0.0)
            assert v.view_state.fov_deg == pytest.approx(fov0)
        finally:
            v.close()

    def test_wheel_outside_card_still_zooms(self, monkeypatch):
        v = self._short_view(monkeypatch)
        try:
            fov0 = v.view_state.fov_deg
            v.wheelEvent({"x": 500, "y": 100, "delta": 120})
            assert v._status_card_scroll == pytest.approx(0.0)
            assert v.view_state.fov_deg < fov0
        finally:
            v.close()

    def test_scroll_offset_clamped_to_content_range(self, monkeypatch):
        v = self._short_view(monkeypatch)
        try:
            max_scroll = v._status_card_max_scroll()
            assert max_scroll > 0.0
            v._scroll_status_card(10 ** 6)
            assert v._status_card_scroll == pytest.approx(max_scroll)
            v._scroll_status_card(-10 ** 6)
            assert v._status_card_scroll == pytest.approx(0.0)
        finally:
            v.close()

    @staticmethod
    def _scrollbar_rects(v):
        """用假画家跑一次状态卡绘制，只返回滚动条的 QRectF（宽 3px）。"""
        rec = _RoundedRectRecorder()
        v._draw_info_overlay(rec)
        return [r for r in rec.rects if abs(r.width() - 3.0) < 1e-6]

    def test_scrollbar_drawn_only_when_content_overflows(self, monkeypatch):
        """滚动条受 max_scroll 门控：不超高不画；超高画轨+拇指且随滚动移动。"""
        v = self._short_view(monkeypatch)      # 内容 600px、窗口 400px
        try:
            assert v._status_card_max_scroll() > 0.0
            rects = self._scrollbar_rects(v)
            assert len(rects) == 2, \
                f"内容超高时必须画轨道+拇指两个滚动条矩形，实际 {rects}"
            track, thumb = rects
            assert track.x() == pytest.approx(
                v._status_card_rect.right() - 7.0), \
                "轨道没贴在状态卡右缘"
            v._scroll_status_card(40.0)
            moved = self._scrollbar_rects(v)
            assert len(moved) == 2
            assert moved[1].y() > thumb.y(), \
                f"滚动后拇指没下移：{thumb.y()} -> {moved[1].y()}"
        finally:
            v.close()

        tall = RFSkyView()
        try:
            tall.resize(900, 900)
            tall.set_observer(LAT, LON, 0.0)
            assert tall._status_card_max_scroll() == 0.0
            assert self._scrollbar_rects(tall) == [], \
                "内容不超高时不许画滚动条"
        finally:
            tall.close()


class TestCardTextContrast:
    """深色卡片上的字不跟天色切换。

    2026-11-11 正午实拍：沉浸卡的时间/位置值、空态卡标题、过境与 GNSS
    面板在白天全糊——卡片底恒为半透明黑，``_ink()`` 却切到深灰
    ``sky_day_ink``。这里钉住「卡内文字与天色无关」。
    """

    def test_card_ink_stays_light_when_sky_is_bright(self):
        v = RFSkyView()
        try:
            v._sky_brightness = 1.0
            day = v._ink()
            card = v._card_ink()
            assert day.lightness() < 120, (
                f"白天天空前景应为深色（既有行为），实际 {day.getRgb()}")
            assert card.lightness() >= 180, (
                f"深色卡片上的文字必须保持浅色，实际 {card.getRgb()}")
        finally:
            v.close()

    def test_card_ink_is_identical_at_night(self):
        """夜间观感不变：卡内文字与原来的 _ink() 同色。"""
        v = RFSkyView()
        try:
            v._sky_brightness = 0.0
            assert v._card_ink().getRgb() == v._ink().getRgb()
        finally:
            v.close()

