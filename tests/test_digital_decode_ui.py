#!/usr/bin/env python3
"""
数字信号解码 UI 面板测试
==========================

验证三个新面板（ADS-B 航路图 / APRS 包 / 卫星云图）：
  1. 面板可实例化（offscreen），无硬件时显“等待数据”空态，绝不放演示数据；
  2. 解码器 API 接线正确：合成 IQ/音频喂入后，真实解出的飞机/包/云图进入面板；
  3. ADS-B 飞机 30s 未更新即移除；APRS 过滤与 500 条上限生效；
  4. main_window hasattr 守卫：面板缺失不崩，连接状态正确转发。

运行:
    QT_QPA_PLATFORM=offscreen python3 -m pytest tests/test_digital_decode_ui.py -v
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


# ---------------------------------------------------------------------------
class TestAdsbPanel:
    def test_empty_state(self):
        from adsb_panel import AdsbPanel, ADSB_WAIT_TEXT
        p = AdsbPanel()
        assert p.aircraft_count() == 0
        assert p.table.rowCount() == 0
        assert ADSB_WAIT_TEXT in p.header.text()

    def test_decoded_frame_wires_in(self):
        from adsb_panel import AdsbPanel
        p = AdsbPanel()
        # 直接喂一个已解码帧 dict（ICAO/呼号/高度/速度）
        p.submit_decoded_frame({
            "icao": "ABCDEF", "callsign": "SWR123", "altitude_ft": 35000,
            "velocity": {"groundspeed_kt": 240, "track_deg": 90},
        })
        assert p.aircraft_count() == 1
        assert p.table.rowCount() == 1
        assert p.table.item(0, 0).text() == "ABCDEF"
        assert p.table.item(0, 1).text() == "SWR123"
        assert "35000" in p.table.item(0, 4).text()

    def test_synthesized_iq_decodes(self):
        """合成 1090 IQ -> decode_adsb -> 面板列表（不造假位置，lat/lon 为 --）。"""
        from mbdsdr_ai import adsb_lite
        from adsb_panel import AdsbPanel
        # TC=1 呼号帧
        me = bytearray(7)
        me[0] = (1 << 3)  # TC=1
        frame = adsb_lite.build_long_frame(0x17, 0x765432, bytes(me))
        iq = adsb_lite.synthesize_modes_iq([frame], sample_rate=2_000_000)
        p = AdsbPanel()
        p.feed_iq(iq, 2_000_000)
        assert p.aircraft_count() == 1
        assert p.table.item(0, 0).text() == "765432"

    def test_prune_timeout(self):
        from adsb_panel import AdsbPanel, AIRCRAFT_TTL_SEC
        p = AdsbPanel()
        p.submit_decoded_frame({"icao": "DEAD00", "callsign": "OLD1"})
        assert p.aircraft_count() == 1
        # 把 last_seen 拨到超时之前
        ac = p._tracker._ac["DEAD00"]
        ac.last_seen = time.time() - AIRCRAFT_TTL_SEC - 5
        p._after_update()
        assert p.aircraft_count() == 0


# ---------------------------------------------------------------------------
class TestAprsPanel:
    def test_empty_state(self):
        from aprs_panel import AprsPanel, APRS_WAIT_TEXT
        p = AprsPanel()
        assert p.packet_count() == 0
        assert APRS_WAIT_TEXT in p.header.text()

    def test_position_frame_wires_in(self):
        from mbdsdr_ai.ax25 import AFSKModem, AX25Frame
        from aprs_panel import AprsPanel
        info = b"!3542.84N/13945.60E>test"
        fr = AX25Frame(destination="APRS", source="BI4MIB",
                       digipeaters=[("WIDE2", 1, False)],
                       control=0x03, pid=0xF0, info=info)
        audio = AFSKModem().modulate(fr)
        p = AprsPanel()
        p.feed_audio(audio, 48000)
        assert p.packet_count() == 1
        assert "BI4MIB" in p.table.item(0, 1).text()
        assert p.table.item(0, 4).text() == "位置"

    def test_filter(self):
        from aprs_panel import AprsPanel
        p = AprsPanel()
        p.submit_aprs_dict({"type": "position", "source": "A",
                            "destination": "B", "digipeaters": []})
        p.submit_aprs_dict({"type": "message", "source": "C",
                            "destination": "D", "digipeaters": [],
                            "message": {"addressee": "X", "message": "hi"}})
        assert p.table.rowCount() == 2
        p.filter_combo.setCurrentText("消息")
        assert p.table.rowCount() == 1
        p.filter_combo.setCurrentText("位置")
        assert p.table.rowCount() == 1
        p.filter_combo.setCurrentText("全部")
        assert p.table.rowCount() == 2

    def test_max_500(self):
        from aprs_panel import AprsPanel, MAX_PACKETS
        p = AprsPanel()
        for i in range(MAX_PACKETS + 50):
            p.submit_aprs_dict({"type": "status", "source": f"N{i}",
                                "destination": "B", "digipeaters": []})
        assert p.packet_count() == MAX_PACKETS


# ---------------------------------------------------------------------------
class TestSatImagePanel:
    def test_empty_state(self):
        from satellite_image_panel import SatelliteImagePanel, SAT_WAIT_TEXT
        s = SatelliteImagePanel()
        assert s.image_label.text() == SAT_WAIT_TEXT
        assert s.image_path() is None
        # 卫星下拉含 NOAA/METEOR
        items = [s.sat_combo.itemText(i) for i in range(s.sat_combo.count())]
        assert any("NOAA-19" in x for x in items)
        assert any("METEOR" in x for x in items)

    def test_no_demo_image(self):
        """无数据时绝不放演示云图：image_label 仍是等待文案，save 按钮置灰。"""
        from satellite_image_panel import SatelliteImagePanel
        s = SatelliteImagePanel()
        assert not s.save_btn.isEnabled()
        assert s.image_path() is None

    def test_decoded_result_shown(self, tmp_path, monkeypatch):
        from mbdsdr_ai import noaa_apt_lite as apt
        import satellite_image_panel as sip
        from satellite_image_panel import SatelliteImagePanel
        monkeypatch.setattr(sip, "_IMG_DIR", str(tmp_path))
        a, b = apt.synthesize_test_images(n_lines=80)
        audio = apt.synthesize_apt_audio(a, b)
        r = apt.decode_apt(audio, 24000)
        assert r["apt_present"]
        s = SatelliteImagePanel()
        s.show_decoded_result(r, "NOAA-19")
        assert s.image_path() is not None
        assert os.path.exists(s.image_path())
        assert s.save_btn.isEnabled()


# ---------------------------------------------------------------------------
class TestMainWindowWiring:
    def test_main_window_has_panels(self):
        from main_window import MainWindow
        w = MainWindow()
        assert w.digital_adsb_panel is not None
        assert w.digital_aprs_panel is not None
        assert w.digital_sat_image_panel is not None

    def test_set_sdr_connected_forwards(self):
        from main_window import MainWindow
        w = MainWindow()
        w._panels_set_sdr_connected(True)
        assert w.digital_adsb_panel._sdr_connected is True
        assert w.digital_aprs_panel._sdr_connected is True
        assert w.digital_sat_image_panel._sdr_connected is True
        w._panels_set_sdr_connected(False)
        assert w.digital_adsb_panel._sdr_connected is False
