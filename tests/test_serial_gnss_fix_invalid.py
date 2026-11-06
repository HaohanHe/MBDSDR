# SPDX-License-Identifier: MIT
"""GNSS 失锁后不许拿旧站址冒充当前定位（2026-11-06）。

旧链两处各自为政，合起来会造出"看起来还在实时更新、其实站址冻在失锁前"：
1. `NMEAParser._parse_rmc` 在 status=V 时仍返回字段里的经纬度（不少模块
   在 V 状态填的是**上一次**坐标），且速度字段为空时把 None 算成 0 节；
2. `SerialGNSSReader._merge` 只在"新值非 None"时覆盖坐标，从不清除；
   而 GSV/GSA/ZDA 等任意合法语句都会刷新 `timestamp`，让 `get_fix()` 的
   10s 过期判定一直续命。

上游 `rf_sky_view.set_gnss_position` 只判 `source=="real"` 且经纬度非空，
于是旧坐标被当成当前站址 → 整张星图、过境预测、多普勒按错位置算。

反证：把 `_merge` 的清空分支删掉、或把 `_parse_rmc` 的 valid 判定去掉，
本文件对应用例立即红。
"""
import os
import sys

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.serial_gnss import (  # noqa: E402
    NMEAParser, SerialGNSSReader, nmea_checksum,
)


def _sentence(body: str) -> str:
    return f"${body}*{nmea_checksum(body):02X}"


GOOD_GGA = "GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,0.0,M,,"
# quality=0 但字段仍填着上一次的经纬度（真实模块常见行为）
NOFIX_GGA_WITH_STALE_COORDS = (
    "GNGGA,072546.00,4352.0000,N,12519.0000,E,0,00,,,150.0,M,,M,,")
# status=V 但仍带着旧经纬度
NOFIX_RMC_WITH_STALE_COORDS = (
    "GNRMC,072546.00,V,4352.0000,N,12519.0000,E,0.0,0.0,240926,,N")
GOOD_RMC = "GNRMC,072545.00,A,4352.0000,N,12519.0000,E,10.5,45.0,010126,,A"
ZDA_ONLY = "GNZDA,072547.00,24,09,2026,,"


class TestParserHonesty:
    def test_rmc_status_v_never_yields_coordinates(self):
        rec = NMEAParser().parse(_sentence(NOFIX_RMC_WITH_STALE_COORDS))
        assert rec is not None and rec["valid"] is False
        assert rec["latitude"] is None and rec["longitude"] is None

    def test_rmc_valid_still_gives_coordinates(self):
        rec = NMEAParser().parse(_sentence(GOOD_RMC))
        assert rec["valid"] is True
        assert rec["latitude"] == pytest.approx(43.866667, abs=1e-4)
        assert rec["longitude"] == pytest.approx(125.316667, abs=1e-4)

    def test_missing_speed_is_none_not_zero(self):
        body = "GNRMC,072546.00,A,4352.0000,N,12519.0000,E,,,240926,,N"
        rec = NMEAParser().parse(_sentence(body))
        assert rec is not None
        assert rec["speed_knots"] is None
        assert rec["speed_kmh"] is None, "缺值被显示成 0 km/h（静止）"


class TestMergeClearsStalePosition:
    def _feed(self, *bodies):
        reader = SerialGNSSReader()
        parser = NMEAParser()
        for body in bodies:
            rec = parser.parse(_sentence(body))
            assert rec is not None, body
            reader._merge(rec)
        return reader.get_fix()

    def test_good_fix_then_nofix_gga_clears_coords(self):
        fix = self._feed(GOOD_GGA, NOFIX_GGA_WITH_STALE_COORDS)
        assert fix["source"] == "real"
        assert fix["fix_quality"] == 0
        assert fix["latitude"] is None
        assert fix["longitude"] is None
        assert fix["altitude_m"] is None

    def test_good_fix_then_nofix_rmc_clears_coords(self):
        fix = self._feed(GOOD_GGA, NOFIX_RMC_WITH_STALE_COORDS)
        assert fix["latitude"] is None
        assert fix["longitude"] is None
        assert fix["speed_kmh"] is not None     # 速度仍有读数，位置没有

    def test_non_position_sentences_do_not_revive_stale_coords(self):
        """GSV/ZDA 续命 timestamp，但坐标不得复活。"""
        fix = self._feed(GOOD_GGA, NOFIX_GGA_WITH_STALE_COORDS, ZDA_ONLY)
        assert fix["source"] == "real"
        assert fix["latitude"] is None and fix["longitude"] is None

    def test_good_fix_survives_a_normal_cycle(self):
        """正常周期（GGA 有效 + RMC 有效 + ZDA）坐标不能被误清。"""
        fix = self._feed(GOOD_GGA, GOOD_RMC, ZDA_ONLY)
        assert fix["latitude"] == pytest.approx(43.866667, abs=1e-4)
        assert fix["longitude"] == pytest.approx(125.316667, abs=1e-4)
        assert fix["altitude_m"] == pytest.approx(150.0)
        assert fix["fix_quality"] == 1

    def test_downstream_set_gnss_position_drops_station(self, qtapp):
        """把清空后的 fix 喂给星图 → 必须走"站址丢失"，不能造站址。"""
        from rf_sky_view import RFSkyView
        view = RFSkyView()
        view.set_observer(43.9, 125.3, 50.0)     # 先给一个手动站址
        fix = self._feed(GOOD_GGA, NOFIX_GGA_WITH_STALE_COORDS)
        view.set_gnss_position(fix)
        assert view._observer is None
        assert view._data_source != "real"


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
