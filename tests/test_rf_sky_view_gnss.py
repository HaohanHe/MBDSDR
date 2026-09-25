"""
tests/test_rf_sky_view_gnss.py
==============================
验证 desktop/rf_sky_view.py 的真实 GNSS 卫星天空图绘制：

  1. 合成 GSV/GSA 数据喂入后绘制不崩溃，卫星按星座合并去重；
  2. 极坐标线性投影正确：天顶=中心、地平=外圈、az=0 指北向上；
  3. 无 GSV 数据时标记未连接，不画假卫星；
  4. talker -> 星座映射正确（GP/GL/GA/GB/BD/GN）；
  5. GSA satellites_used 正确标记参与定位的卫星；
  6. 各星座分色与可见数统计；缺 az/el 的条目不画。

运行：
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_rf_sky_view_gnss.py -v
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _gsv(talker: str, sats):
    """构造一个 NMEAParser._parse_gsv 风格的 frame。"""
    return {"talker": talker, "sentence": "GSV", "sats": list(sats)}


def _sat(prn: int, el: float, az: float, snr):
    return {"id": prn, "elevation": el, "azimuth": az, "snr_db": snr}


# ----------------------------------------------------------------------
class TestTalkerMapping:
    def test_talker_to_constellation(self):
        from desktop.rf_sky_view import RFSkyView
        m = RFSkyView._talker_to_constellation
        assert m("GP", 1) == "GPS"
        assert m("GL", 10) == "GLONASS"
        assert m("GA", 7) == "Galileo"
        assert m("GB", 33) == "BeiDou"
        assert m("BD", 41) == "BeiDou"

    def test_gn_mixed_inference(self):
        from desktop.rf_sky_view import RFSkyView
        m = RFSkyView._talker_to_constellation
        # GLONASS slot 65-96 可识别；其余编号重叠 -> 未知（灰色，不造假）
        assert m("GN", 75) == "GLONASS"
        assert m("GN", 1) == "未知"
        assert m("XX", 1) == "未知"


# ----------------------------------------------------------------------
class TestUpdateAndDedup:
    def test_synthetic_gsv_updates_and_paints(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(400, 400)
        frames = [
            _gsv("GP", [_sat(1, 80, 45, 42), _sat(3, 45, 200, 25)]),
            _gsv("GB", [_sat(33, 60, 100, 30)]),
            _gsv("GL", [_sat(10, 30, 300, 15)]),
        ]
        gsa = {"talker": "GN", "fix_type": 3, "satellites_used": [1, 33, 10]}
        v.update_gnss_satellites(frames, gsa)
        assert v._gnss_connected is True
        assert len(v._gnss_satellites) == 4
        # GSA used 标记
        assert v._gnss_sats_used == {1, 33, 10}
        assert v._gnss_fix_type == 3
        # 绘制不崩溃
        v.repaint()

    def test_dedup_same_constellation_prn_keeps_latest(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        # 同星座同 PRN 出现两次（多帧 GSV 重复上报），只保留一条
        frames = [
            _gsv("GP", [_sat(1, 80, 45, 40)]),
            _gsv("GP", [_sat(1, 75, 50, 44)]),
        ]
        v.update_gnss_satellites(frames)
        gps1 = [s for s in v._gnss_satellites if s["prn"] == 1]
        assert len(gps1) == 1
        # 后到的一帧覆盖：仰角/信噪比取最新
        assert gps1[0]["elevation"] == 75.0
        assert gps1[0]["snr_db"] == 44

    def test_missing_az_el_skipped(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        frames = [_gsv("GP", [
            _sat(1, 80, 45, 40),
            {"id": 2, "elevation": None, "azimuth": None, "snr_db": None},
        ])]
        v.update_gnss_satellites(frames)
        prns = [s["prn"] for s in v._gnss_satellites]
        assert prns == [1]

    def test_constellation_colors_assigned(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        frames = [
            _gsv("GP", [_sat(1, 80, 45, 40)]),
            _gsv("GB", [_sat(33, 60, 100, 30)]),
            _gsv("GL", [_sat(10, 30, 300, 15)]),
            _gsv("GA", [_sat(7, 50, 180, 35)]),
        ]
        v.update_gnss_satellites(frames)
        cons = {s["prn"]: s["constellation"] for s in v._gnss_satellites}
        assert cons[1] == "GPS"
        assert cons[33] == "BeiDou"
        assert cons[10] == "GLONASS"
        assert cons[7] == "Galileo"


# ----------------------------------------------------------------------
class TestProjection:
    def test_zenith_is_center(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(400, 400)
        p = v._gnss_az_el_to_screen(123.0, 90.0)
        assert abs(p.x() - 200.0) < 1e-6
        assert abs(p.y() - 200.0) < 1e-6

    def test_horizon_directions(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(400, 400)
        # az=0 北 -> 正上方；az=90 东 -> 正右方
        p_n = v._gnss_az_el_to_screen(0.0, 0.0)
        assert abs(p_n.x() - 200.0) < 1e-6
        assert abs(p_n.y() - 0.0) < 1e-6
        p_e = v._gnss_az_el_to_screen(90.0, 0.0)
        assert abs(p_e.x() - 400.0) < 1e-6
        assert abs(p_e.y() - 200.0) < 1e-6
        p_s = v._gnss_az_el_to_screen(180.0, 0.0)
        assert abs(p_s.y() - 400.0) < 1e-6
        p_w = v._gnss_az_el_to_screen(270.0, 0.0)
        assert abs(p_w.x() - 0.0) < 1e-6

    def test_points_within_disk(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(400, 400)
        for az in range(0, 360, 30):
            for el in (5, 45, 88):
                p = v._gnss_az_el_to_screen(float(az), float(el))
                d = ((p.x() - 200.0) ** 2 + (p.y() - 200.0) ** 2) ** 0.5
                assert d <= 200.0 + 1e-6


# ----------------------------------------------------------------------
class TestDisconnectedAndGSA:
    def test_no_data_shows_disconnected(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(400, 400)
        assert v._gnss_connected is False
        # 空列表 / None 都应保持未连接，不画假卫星
        v.update_gnss_satellites([])
        assert v._gnss_connected is False
        assert v._gnss_satellites == []
        v.update_gnss_satellites(None, {"fix_type": 1, "satellites_used": [1]})
        assert v._gnss_connected is False
        v.repaint()

    def test_gsa_used_satellites_highlight(self):
        from desktop.rf_sky_view import RFSkyView
        v = RFSkyView()
        v.resize(400, 400)
        frames = [_gsv("GP", [_sat(1, 80, 45, 42), _sat(5, 20, 120, 10)])]
        gsa = {"fix_type": 3, "satellites_used": [1]}
        v.update_gnss_satellites(frames, gsa)
        assert v._gnss_sats_used == {1}
        by_prn = {s["prn"]: s for s in v._gnss_satellites}
        assert by_prn[1]["prn"] in v._gnss_sats_used     # 参与定位
        assert by_prn[5]["prn"] not in v._gnss_sats_used  # 仅可见
        assert v._gnss_fix_type == 3
        v.repaint()


# ----------------------------------------------------------------------
class TestReaderIntegration:
    def test_serial_reader_caches_gsv_gsa(self):
        """SerialGNSSReader 解析合成 NMEA 后应能取出 GSV/GSA 快照。"""
        from mbdsdr_ai.serial_gnss import NMEAParser, SerialGNSSReader, nmea_checksum

        def make(body: str) -> str:
            return f"${body}*{nmea_checksum(body):02X}"

        p = NMEAParser()
        # 单帧 GSV（GP 1 颗）+ GSA
        rec_gsv = p.parse(make("GPGSV,1,1,1,01,80,045,42"))
        rec_gsa = p.parse(make("GNGSA,A,3,01,,,,,,,,,,,,1.0,0.8,0.9"))
        assert rec_gsv is not None and rec_gsa is not None

        r = SerialGNSSReader()
        r._merge(rec_gsv)
        r._merge(rec_gsa)
        frames = r.get_gsv_frames()
        assert len(frames) == 1
        assert frames[0]["talker"] == "GP"
        assert frames[0]["sats"][0]["id"] == 1
        gsa = r.get_gsa()
        assert gsa is not None and gsa["fix_type"] == 3
        assert 1 in gsa["satellites_used"]
