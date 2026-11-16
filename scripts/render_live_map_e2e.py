#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""端到端证据链（可复跑，离线）：合成 Mode-S IQ → 生产解码入口 →
位置中枢 → 地面态势图截图。

诚实边界（图上横幅原文同款）：
  * **信号是合成的**（本脚本用 adsb 真编码器造 DF17 位置帧再调制成 IQ），
    不是真实航班；但**管线是真的**：AdsbMapPanel.feed_iq（生产入口）→
    adsb_lite 解码 → AircraftTracker CPR 配对 → PositionService → 要素层，
    全程无测试替身。
  * 卫星 TLE 为注入样本（摘要如实标 injected/stale），瓦片为本地缓存 OSM，
    确有瓦片才署名。真机不在位时这是链路证据，不是实测证据。

输出：D:/mbdsdr/output/map_live_e2e_preview.png（仓库外）。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QFont, QFontDatabase, QPainter, QPen)
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdsdr_ai.adsb import (  # noqa: E402
    bits_to_bytes, build_long_frame, encode_cpr_airborne)
from mbdsdr_ai.adsb_lite import synthesize_modes_iq  # noqa: E402

#: 中性示例位置（不是真实航班；图上横幅如实标注）。
LAT, LON = 39.9, 116.4
T_UNIX = 1763000000.0

ISS_TLE = (
    "1 25544U 98067A   26297.20915682  .00007800  00000+0  14938-3 0  9991",
    "2 25544  51.6335  21.6229 0006884 302.9098  57.1228 15.49030149591752",
)
NOAA_TLE = (
    "1 33591U 09005A   26297.21587812  .00000042  00000+0  51824-4 0  9990",
    "2 33591  99.1872  12.4431 0013477 287.9124  72.0561 14.12543210867299",
)


def _make_pos_frame(odd: bool, clat: int, clon: int) -> bytes:
    mm = [0] * 56

    def sb2(a, b, vv):
        for i in range(b - a + 1):
            mm[a - 1 + (b - a + 1) - 1 - i] = (vv >> i) & 1

    sb2(1, 5, 11)
    sb2(22, 22, 1 if odd else 0)
    sb2(23, 39, clat)
    sb2(40, 56, clon)
    return build_long_frame(0x8D, 0x40621D, bits_to_bytes(mm))


def _make_callsign_frame() -> bytes:
    from mbdsdr_ai.adsb_lite import _ADSB_CHAR
    mm = [0] * 56

    def sb2(a, b, vv):
        for i in range(b - a + 1):
            mm[a - 1 + (b - a + 1) - 1 - i] = (vv >> i) & 1

    sb2(1, 5, 4)  # TC=4 识别
    enc = {c: i for i, c in enumerate(_ADSB_CHAR)}
    chars = "DEMO01  "
    for idx in range(8):
        sb2(9 + idx * 6, 14 + idx * 6, enc.get(chars[idx], 32))
    return build_long_frame(0x8D, 0x40621D, bits_to_bytes(mm))


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    for path in (r"C:\Windows\Fonts\msyh.ttc",):
        if os.path.exists(path):
            QFontDatabase.addApplicationFont(path)
    app.setFont(QFont("Microsoft YaHei", 9))

    from adsb_map_panel import AdsbMapPanel
    from sat_ground_map import SatelliteGroundMap
    from mbdsdr_ai.position_service import (
        get_position_service, reset_position_service)
    from mbdsdr_ai.spacetime_features import (
        TemporalContext, TemporalMode, TimeInterval,
        get_ground_features, get_temporal_bus)

    reset_position_service()

    # 1) 挑一个 ISS 真实覆盖示例点的 epoch（真实轨道算术，不是画个圈凑图）。
    #    基准取 TLE 历元（外推一年的几何不作数），历元起 ±12 h 扫描、60 s 步。
    from mbdsdr_ai.ground_track import (
        footprint_central_angle_deg, satellite_subpoint)
    from mbdsdr_ai.sat_passes import VISIBLE_MIN_ALT_DEG
    from mbdsdr_ai.spacetime_features import Footprint

    def _tle_epoch_unix(line1: str) -> float:
        import datetime
        yy = int(line1[18:20])
        doy = float(line1[20:32])
        return datetime.datetime(
            2000 + yy, 1, 1, tzinfo=datetime.timezone.utc
        ).timestamp() + (doy - 1) * 86400.0

    base = _tle_epoch_unix(ISS_TLE[0])
    t_used = base
    found = False
    best_t, best_ang = base, 180.0
    for dt in range(-12 * 3600, 12 * 3600, 60):
        t = base + dt
        sub = satellite_subpoint("ISS (ZARYA)", when_unix=t, tle=ISS_TLE)
        ang = Footprint(sub.lat_deg, sub.lon_deg, 17.0,
                        at_unix=t).angle_to(LAT, LON)
        if ang < best_ang:
            best_ang, best_t = ang, t
        if ang <= footprint_central_angle_deg(sub.sat_alt_km,
                                              VISIBLE_MIN_ALT_DEG):
            t_used = t
            found = True
            break
    if not found:
        t_used = best_t
        print(f"NOTICE: 历元 ±12h 内无覆盖 epoch，取最近距离 {best_ang:.1f}° 的时刻")
    print(f"epoch used = {t_used} (offset {t_used - base:.0f}s from TLE epoch, "
          f"covered={found})")

    # 2) 合成 IQ（真编码器造帧：DF17 呼号 + 空中位置偶/奇对）
    enc = encode_cpr_airborne(LAT, LON)
    iq = synthesize_modes_iq([
        _make_callsign_frame(),
        _make_pos_frame(False, enc["even_cprlat"], enc["even_cprlon"]),
        _make_pos_frame(True, enc["odd_cprlat"], enc["odd_cprlon"]),
    ], sample_rate=2_000_000)

    # 2) 生产入口解码（面板就是这么接 IQ 总线的）
    panel = AdsbMapPanel(map_online=False)
    n = panel.feed_iq(iq, 2_000_000.0)
    acs = [a for a in panel._tracker.all() if a.lat is not None]
    print(f"decode frames={n}, positioned aircraft={len(acs)}")
    if not acs:
        print("FAIL: 解码链没出位置")
        return 1
    ac = acs[0]
    print(f"aircraft {ac.icao_hex} {ac.callsign!r} at {ac.lat:.4f},{ac.lon:.4f}")
    print(f"hub reports: {[r.source for r in get_position_service().all_latest()]}")

    # 3) 地面态势图（多星 + 要素层吃中枢）
    smap = SatelliteGroundMap(online=False)
    smap.resize(920, 560)
    smap.update_constellation(
        ["ISS (ZARYA)", "NOAA 19"], when_unix=t_used,
        tle_map={"ISS (ZARYA)": ISS_TLE, "NOAA 19": NOAA_TLE}, recenter=False)
    get_temporal_bus().publish(TemporalContext(
        at_unix=t_used, interval=TimeInterval(t_used, t_used),
        basis="simulated", mode=TemporalMode.RANGE_FILTER, has_time_data=True))

    col = get_ground_features()
    print("features before align:", [(f.feature_id, f.label) for f in col.all()])

    # 场景时间对齐：真实上报带墙钟时刻，展示 epoch 是轨道搜索出来的，
    # 两者差几十分钟时目标会被诚实判过期。把目标有效期对齐展示 epoch
    # （场景注入，横幅如实写明），其余全真。
    import dataclasses
    from mbdsdr_ai.position_service import get_position_service
    from mbdsdr_ai.spacetime_features import upsert_position_report
    svc = get_position_service()
    rep = svc.get(ac.icao_hex)
    if rep is not None:
        rep2 = dataclasses.replace(rep, timestamp=t_used - 60.0)
        upsert_position_report(
            rep2, track=tuple(svc.track(rep.source)), ttl_s=600.0,
            collection=col)
    if smap.spacetime_layer is not None:
        smap.spacetime_layer.refresh()  # 对齐后必须重算查询，角标才说真话
    print("features:", [(f.feature_id, f.label) for f in col.all()])

    smap.view.set_view(ac.lat, ac.lon, 5)
    smap.show()
    app.processEvents()

    img = smap.grab().toImage()
    p = QPainter(img)
    p.setFont(QFont("Microsoft YaHei", 9))
    h = img.height()
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(12, 16, 24, 205)))
    p.drawRoundedRect(QRectF(8, h - 96, img.width() - 16, 62), 4, 4)
    p.setPen(QColor("#e6e9ef"))
    p.drawText(18, h - 74,
               "端到端链路证据：信号为合成（真编码器造 DF17），管线为真——")
    p.drawText(18, h - 56,
               "feed_iq 生产入口 → 解码 → CPR 配对 → 位置中枢 → 要素层；"
               "时刻=ISS 真实覆盖 epoch，目标有效期对齐该时刻（场景时间）")
    p.end()
    out = r"D:\mbdsdr\output\map_live_e2e_preview.png"
    img.save(out)
    print("saved:", out)
    smap.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
