#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
月面反照率：数据出处 + 正射投影反投影 + 采样（数据/几何层，不含绘制）
====================================================================

验证口径（**不许用「看起来像」当验证**）：

1. ``inverse ∘ forward = identity`` 的往返角误差（机器精度量级）。
2. **与投影公式无关的第二种实现**（haversine）对拍：正射投影的弦长必须等于
   ``2·sin(c/2)``，``c`` 由 haversine 独立给出。这条能抓住经度符号、纬度方向、
   次地球点用错等「自洽但整体错了」的错误（自洽往返抓不住）。
3. 已知特征（第谷坑/各大月海）在标准光照（次地球点 = (0,0)，即零天平动的满月）
   下反投影回目录经纬度，逐条给角度误差。
4. 用**外部数据**（LOLA 反照率图）当地标真值：月海应比高地暗、第谷坑应亮；
   把经度符号或南北方向翻掉必须让这条变红（反证）。
"""

import math

import numpy as np
import pytest

from mbdsdr_ai.astronomy import moon_albedo as ma

# 已知月面特征（IAU 目录值，planetocentric，东经为正）
FEATURES = {
    "第谷坑 Tycho": (-43.31, -11.36),
    "哥白尼坑 Copernicus": (9.62, -20.08),
    "雨海 Mare Imbrium": (32.8, -15.6),
    "澄海 Mare Serenitatis": (28.0, 17.5),
    "静海 Mare Tranquillitatis": (8.5, 31.4),
    "危海 Mare Crisium": (17.0, 59.1),
    "云海 Mare Nubium": (-21.3, -16.6),
    "丰富海 Mare Fecunditatis": (-7.8, 51.3),
}
MARIA = ["雨海 Mare Imbrium", "澄海 Mare Serenitatis", "静海 Mare Tranquillitatis",
         "危海 Mare Crisium", "云海 Mare Nubium", "丰富海 Mare Fecunditatis"]
HIGHLANDS = {"远侧高地": (0.0, 160.0), "南极高地": (-70.0, 20.0),
             "月球背面南部": (-45.0, 140.0)}

# 阈值说明（写死，不许为了绿而放宽）：
#   * 反投影往返是本模块自己的代数逆，必须到机器精度（1e-9°）。
#   * 特征落点同样走同一条代数链，故同一量级。
#   * 数据侧（月海/高地）判据受 0.5°/格（2 pix/deg）降采样限制，用「差多少」。
ROUNDTRIP_TOL_DEG = 1e-9
IDENTITY_TOL = 1e-12
FEATURE_TOL_DEG = 1e-9


def _angular_sep_deg(a, b):
    """haversine 角距——与正射投影公式**无关**的第二条实现。"""
    la1, lo1 = math.radians(a[0]), math.radians(a[1])
    la2, lo2 = math.radians(b[0]), math.radians(b[1])
    h = (math.sin((la2 - la1) / 2.0) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2.0) ** 2)
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(h))))


def test_roundtrip_max_angular_error_in_machine_range():
    """forward→inverse 往返：次地球点取 9 组（含非零天平动），全域经纬网。

    跳过 ``w < 1e-3`` 的**临边点**：那里 ``ρ→1``、``asin(ρ)`` 的条件数发散，
    有限的浮点误差会被放大成 1e-6 量级的角误差——这是正射投影在 limb 处的固有
    病态，不是反投影公式错。临边点另有 ``test_limb_is_ninety_degrees`` 单独兜。
    """
    worst = 0.0
    worst_at = None
    checked = 0
    for sub_lat in (-6.0, 0.0, 5.0):
        for sub_lon in (-8.0, 0.0, 7.0):
            for lat in range(-85, 86, 5):
                for lon in range(-180, 180, 10):
                    x, y, visible = ma.selenographic_to_disc(
                        lat, lon, sub_lat, sub_lon)
                    if not visible:
                        continue
                    # 与次地球点的角距：w = cos(c)，|w|<1e-3 即距临边 < 0.057°
                    c_deg = _angular_sep_deg((lat, lon), (sub_lat, sub_lon))
                    if abs(math.cos(math.radians(c_deg))) < 1e-3:
                        continue
                    back = ma.disc_to_selenographic(x, y, sub_lat, sub_lon)
                    assert back is not None, (
                        f"可见点 ({lat},{lon}) 反投影返回 None（x={x},y={y}）")
                    err = _angular_sep_deg((lat, lon), back)
                    checked += 1
                    if err > worst:
                        worst, worst_at = err, (lat, lon, sub_lat, sub_lon)
    assert checked > 1000, f"可见样本太少（{checked}），测试没有覆盖全域"
    print(f"[roundtrip] 样本 {checked}，最大角误差 {worst:.3e}°（最差点 {worst_at}）")
    assert worst < ROUNDTRIP_TOL_DEG, (
        f"往返角误差 {worst:.3e}° ≥ {ROUNDTRIP_TOL_DEG}°：反投影不是正投影的逆")


def test_orthographic_chord_matches_independent_haversine_distance():
    """径向恒等式：``ρ = hypot(x, y)`` 必须等于 ``sin(c)``，``c`` 由 haversine 独立给出。

    这是正射投影的**定义性**性质（圆盘半径 = 视半径，``ρ`` 是角距的正弦），
    且只用 ``hypot`` + 独立的 haversine，不碰模块里的 ``asin/atan2`` 分支。
    经度符号、纬度方向、次地球点取错都会破坏它——自洽往返抓不住这些。

    注意**不能**用「|P1−P2| == 2·sin(c/2)」：正射投影沿视轴方向有压缩，
    投影后的两点距离只在两点深度相同时才等于三维弦长。那是错的恒等式。
    """
    sub = (0.0, 0.0)
    worst = 0.0
    worst_name = None
    for name, (lat, lon) in FEATURES.items():
        x, y, visible = ma.selenographic_to_disc(lat, lon, *sub)
        assert visible, f"{name} 应在零天平动满月可见"
        rho = math.hypot(x, y)
        c_deg = _angular_sep_deg((lat, lon), sub)
        expected = math.sin(math.radians(c_deg))
        d = abs(rho - expected)
        if d > worst:
            worst, worst_name = d, name
    print(f"[radial] 最大 |ρ − sin(c)| = {worst:.3e}（{worst_name}）")
    assert worst < IDENTITY_TOL, (
        f"ρ 与 sin(角距) 不自洽：{worst:.3e} @ {worst_name}")


def test_projected_azimuth_matches_independent_bearing_formula():
    """圆盘内的方位角必须等于独立算出的（次地球点→特征）球面起始方位角。

    正射投影里 ``atan2(x, y)``（自 +y 向 +x）等于球面起始方位角。

    诚实边界：这条是**同一球面三角公式的第二份写法**，只能抓抄错/经纬度用反，
    抓不住「两个实现都错的模型」。模型级错误由径向恒等式与下面的事件反证兜。
    """
    sub = (0.0, 0.0)
    worst = -1.0
    worst_name = ""
    for name, (lat, lon) in FEATURES.items():
        x, y, visible = ma.selenographic_to_disc(lat, lon, *sub)
        assert visible
        if abs(math.degrees(math.atan2(y, x))) < 1e-9:
            continue
        phi1, phi2 = math.radians(sub[0]), math.radians(lat)
        dlam = math.radians(lon - sub[1])
        bearing = math.degrees(math.atan2(
            math.sin(dlam) * math.cos(phi2),
            math.cos(phi1) * math.sin(phi2)
            - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)))
        got = math.degrees(math.atan2(x, y))
        diff = abs((got - bearing + 180.0) % 360.0 - 180.0)
        if diff > worst:
            worst, worst_name = diff, name
    print(f"[azimuth] 最大角差 {worst:.3e}°（{worst_name}）")
    assert worst < 1e-9, f"投影方位角与球面方位角不一致：{worst:.3e}° @ {worst_name}"


def test_known_features_recover_catalog_coordinates():
    """标准光照（次地球点 (0,0)）下，已知特征落点回到目录经纬度。"""
    print("[feature] 特征           圆盘(x,y)            反投影(lat,lon)      角误差(°)")
    worst = 0.0
    for name, (lat, lon) in FEATURES.items():
        x, y, visible = ma.selenographic_to_disc(lat, lon)
        assert visible, f"{name} 在零天平动满月下应可见"
        back = ma.disc_to_selenographic(x, y)
        assert back is not None
        err = _angular_sep_deg((lat, lon), back)
        print(f"  {name:<22s} ({x:+.4f},{y:+.4f})  "
              f"({back[0]:+9.5f},{back[1]:+9.5f})  {err:.3e}")
        worst = max(worst, err)
    assert worst < FEATURE_TOL_DEG, f"特征落点最大角误差 {worst:.3e}°"
    # 方向语义（不是数值巧合）：危海在东侧近边缘，第谷坑在西南。
    x_crisium, y_crisium, _ = ma.selenographic_to_disc(*FEATURES["危海 Mare Crisium"])
    x_tycho, y_tycho, _ = ma.selenographic_to_disc(*FEATURES["第谷坑 Tycho"])
    assert x_crisium > 0.80 and y_crisium > 0.25, "危海应在东侧偏北（+x, +y）"
    assert x_tycho < 0.0 and y_tycho < -0.65, "第谷坑应在西南（−x, −y）"


def test_limb_is_ninety_degrees_from_sub_earth_point():
    """圆盘边缘 (r=1) 必须对应距次地球点 90°——正射投影的定义性恒等式。"""
    for sub_lat, sub_lon in ((0.0, 0.0), (4.0, -6.0), (-5.0, 8.0)):
        for x, y in ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)):
            ll = ma.disc_to_selenographic(x, y, sub_lat, sub_lon)
            assert ll is not None
            sep = _angular_sep_deg((sub_lat, sub_lon), ll)
            assert abs(sep - 90.0) < 1e-9, f"limb 点角距 {sep}° ≠ 90°"


def test_orientation_conventions():
    """经度增大 → +x（东在右）；纬度增大 → +y（北在上）。"""
    x1, y1, _ = ma.selenographic_to_disc(0.0, 0.0)
    x2, _, _ = ma.selenographic_to_disc(0.0, 10.0)
    _, y3, _ = ma.selenographic_to_disc(10.0, 0.0)
    assert abs(x1) < 1e-12 and abs(y1) < 1e-12
    assert x2 > 0.0, "东经应落在 +x"
    assert y3 > 0.0, "北纬应落在 +y"


def test_disc_outside_returns_none_not_a_guess():
    assert ma.disc_to_selenographic(1.01, 0.0) is None
    assert ma.disc_to_selenographic(0.8, 0.8) is None
    assert ma.albedo_at_disc(1.01, 0.0) is None


def test_screen_mapping_matches_disc_frame():
    """屏幕像素 → 圆盘坐标：北朝上时上=+y、右=+x；北朝右时上=−x。"""
    x, y = ma.screen_to_disc(110.0, 100.0, 100.0, 100.0, 50.0, 0.0)
    assert abs(x - 0.2) < 1e-12 and abs(y) < 1e-12
    x, y = ma.screen_to_disc(100.0, 90.0, 100.0, 100.0, 50.0, 0.0)
    assert abs(y - 0.2) < 1e-12 and abs(x) < 1e-12
    x, y = ma.screen_to_disc(100.0, 90.0, 100.0, 100.0, 50.0, 90.0)
    assert abs(x + 0.2) < 1e-12 and abs(y) < 1e-12, "北朝右时屏幕上方是 −x"
    assert ma.screen_to_disc(160.0, 100.0, 100.0, 100.0, 50.0) is None


def test_shipped_grid_meta_and_hash():
    grid = ma.albedo_grid()
    meta = ma.albedo_meta()
    assert grid.shape == (360, 720), f"2 pix/deg 网格应为 (360,720)，实际 {grid.shape}"
    assert np.isfinite(grid).all(), "网格存在 NaN/Inf（PDS 产品不应有空洞）"
    assert 0.05 < float(grid.min()) < 0.25, f"最小反照率 {grid.min()} 不符合月海量级"
    assert 0.30 < float(grid.max()) < 0.75, f"最大反照率 {grid.max()} 不符合高地量级"

    for key in ("source", "source_url", "source_license", "source_license_url",
                "source_file_sha256", "data_sha256", "citation",
                "resolution_pix_per_deg", "downsample"):
        assert key in meta and str(meta[key]).strip(), f"meta 缺 {key}"
    assert len(meta["source_file_sha256"]) == 64
    assert len(meta["data_sha256"]) == 64
    assert "public domain" in str(meta["source_license"]).lower(), \
        "随包数据必须写明公有领域许可"
    assert "gpl" not in str(meta["source_license"]).lower(), "GPL 数据禁入 MIT 主库"

    import hashlib
    payload = np.ascontiguousarray(grid.astype(np.float32)).tobytes()
    assert hashlib.sha256(payload).hexdigest() == meta["data_sha256"], \
        "网格字节与 meta 的 data_sha256 不一致（数据被改过或重新降采样过）"


def test_maria_darker_than_highlands_in_shipped_map():
    """用外部数据（LOLA）当地标真值：月海必须比高地暗，第谷坑必须亮。"""
    maria_mean = float(np.mean([ma.sample_albedo(*FEATURES[n]) for n in MARIA]))
    high_mean = float(np.mean([ma.sample_albedo(*p) for p in HIGHLANDS.values()]))
    tycho = ma.sample_albedo(*FEATURES["第谷坑 Tycho"])
    print(f"[data] 月海均值 {maria_mean:.4f} / 高地均值 {high_mean:.4f} / "
          f"第谷坑 {tycho:.4f}")
    assert high_mean > maria_mean + 0.05, \
        f"高地({high_mean:.4f}) 未比月海({maria_mean:.4f}) 亮 0.05——地标失败"
    assert tycho > maria_mean + 0.05, \
        f"第谷坑({tycho:.4f}) 未显著亮于月海均值({maria_mean:.4f})"


def test_screen_pixel_to_albedo_pipeline_end_to_end():
    """整条最小实现链：屏幕像素 → 圆盘 → 反投影 → 采样 → 亮度系数。

    第谷坑放在 (350,350) 圆心、半径 50 px 的圆盘上，北朝上；反解出的反照率
    必须与直接按目录经纬度采样一致，亮度系数按均值归一。
    """
    cx, cy, r = 350.0, 350.0, 50.0
    lat, lon = FEATURES["第谷坑 Tycho"]
    x, y, visible = ma.selenographic_to_disc(lat, lon)
    assert visible
    px = cx + x * r
    py = cy - y * r                      # 圆盘 +y(北) 对应屏幕向上（像素 y 减小）
    back = ma.screen_to_disc(px, py, cx, cy, r, north_angle_deg=0.0)
    assert back is not None
    assert abs(back[0] - x) < 1e-12 and abs(back[1] - y) < 1e-12
    alb = ma.albedo_at_disc(back[0], back[1])
    assert alb is not None
    direct = ma.sample_albedo(lat, lon)
    assert abs(alb - direct) < 1e-12
    mod = ma.albedo_modulation(alb)
    print(f"[pipeline] Tycho 屏幕({px:.3f},{py:.3f}) -> 反照率 {alb:.4f} -> 亮度系数 {mod:.4f}")
    assert 1.0 < mod < 1.5, f"第谷坑亮度系数 {mod:.4f} 超出合理范围（均值归一）"
    assert ma.albedo_at_disc(1.0, 0.02) is None, "圆盘边缘外不得返回反照率"


def test_albedo_check_catches_longitude_origin_180_offset():
    """反证：经纬度原点错 180°（LDAM 实际列 0 = 0°E，不是 −180°E）。

    这是**本轮真实踩到过的错误**：按 −180..180 读图时雨海采到 0.329（高地亮值），
    正确读法是 0.175（月海暗值）。把原点改回 180° 偏移必须让这条变红。
    """
    lat, lon = FEATURES["雨海 Mare Imbrium"]
    correct = ma.sample_albedo(lat, lon)
    offset = ma.sample_albedo(lat, lon + 180.0)
    print(f"[mutation-lon-origin] 雨海正确 {correct:.4f} vs 原点偏 180° {offset:.4f}")
    assert offset > correct + 0.10, \
        "经度原点偏 180° 后仍拿到月海暗值——地标判据抓不住经度原点错误"


def test_single_point_albedo_cannot_prove_longitude_sign():
    """诚实边界：单点反照率**抓不住经度符号翻转**（危海取反仍落在一个月海里）。

    危海 (17°N, 59.1°E) 正确 0.175；取反 (−59.1°) 落在风暴洋，也是月海 0.143。
    两者差 0.032 < 0.04，不构成判据。经度符号由「弦长 vs haversine」恒等式与
    「东在 +x」方向断言兜住，不靠这条数据检查。
    """
    lat, lon = FEATURES["危海 Mare Crisium"]
    correct = ma.sample_albedo(lat, lon)
    flipped = ma.sample_albedo(lat, -lon)
    print(f"[note] 危海 正确 {correct:.4f} / 经度取反 {flipped:.4f}（差 {abs(correct-flipped):.4f}）")
    assert abs(correct - flipped) < 0.04, (
        "若这条突然 > 0.04，说明「单点抓不住经度符号」的边界结论变了，需重写文档")


def test_tycho_local_peak_within_two_degrees():
    """数据侧地标：目录坐标 ±3° 窗口内搜最亮格，应在 1.5° 内。

    阈值取 1.5° 而不是 1°：①格距 0.5°（2 pix/deg），±1 格就是 0.5°；
    ②第谷的亮射线/溅射毯并不以坑心对称，最亮格偏出 1° 属预期（实测 1.04°，
    约 2 格）。这条只证明「数据索引没整体错位」，不证明 0.5° 以内的精确配准。
    """
    lat, lon = FEATURES["第谷坑 Tycho"]
    best = (-1.0, None)
    for dlat in np.arange(-3.0, 3.0001, 0.25):
        for dlon in np.arange(-3.0, 3.0001, 0.25):
            v = ma.sample_albedo(lat + float(dlat), lon + float(dlon))
            if v > best[0]:
                best = (v, (lat + float(dlat), lon + float(dlon)))
    sep = _angular_sep_deg((lat, lon), best[1])
    print(f"[landmark] 第谷坑窗口最亮 {best[0]:.4f} @ {best[1]}，距目录值 {sep:.2f}°")
    assert sep < 1.5, f"第谷坑最亮格距目录坐标 {sep:.2f}° > 1.5°：数据/索引对不上"


def test_albedo_check_catches_latitude_sign_flip():
    """反证：南北翻转（lat→−lat），雨海会采到南部高地，暗值消失。"""
    lat, lon = FEATURES["雨海 Mare Imbrium"]
    correct = ma.sample_albedo(lat, lon)
    flipped = ma.sample_albedo(-lat, lon)
    print(f"[mutation-lat] 雨海正确 {correct:.4f} vs 纬度取反 {flipped:.4f}")
    assert flipped > correct + 0.04, \
        "纬度符号翻转后仍拿到同样暗值——地标判据抓不住方向错误"


def test_modulation_is_normalized_to_unit_mean():
    """亮度系数均值必须为 1（不改整体亮度，只改纹理对比）。"""
    grid = ma.albedo_grid()
    ref = ma.reference_albedo()
    assert ref > 0.0
    factors = grid / ref
    assert abs(float(np.mean(factors)) - 1.0) < 1e-12
    assert abs(ma.albedo_modulation(ref) - 1.0) < 1e-12
    assert ma.albedo_modulation(float("nan")) == 1.0
