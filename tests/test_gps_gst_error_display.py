# SPDX-License-Identifier: MIT
"""A5：GST 真实定位误差显示，与 HDOP 并列。

钉四件事：
  1. GST 句必须真正并入 fix 快照（此前只解析、_merge 丢弃）；
  2. 无定位的历元必须把 GST 清掉，不拿上一帧误差冒充当前精度；
  3. 面板把各轴 1σ 误差与 HDOP 并列显示，缺 GST 显示「—」，不由 HDOP 推算；
  4. HDOP 的 tooltip 明说它只是几何因子，不是定位误差。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop.status_panel import StatusPanel, format_gst_error  # noqa: E402
from mbdsdr_ai.serial_gnss import (  # noqa: E402
    NMEAParser,
    SerialGNSSReader,
    nmea_checksum,
)

app = QApplication.instance() or QApplication(sys.argv)

GGA_VALID = ("GNGGA,072545.00,4352.0000,N,12519.0000,E,1,09,0.9,150.0,M,"
             "0.0,M,,")
GGA_INVALID = ("GNGGA,072545.00,4352.0000,N,12519.0000,E,0,09,0.9,150.0,M,"
               "0.0,M,,")
GST = "GNGST,072545.00,1.0,2.0,1.0,30.0,0.8,0.6,5.0"


def _mk(body: str) -> bytes:
    return f"${body}*{nmea_checksum(body):02X}\r\n".encode("ascii")


def _parse(body: str):
    return NMEAParser().parse(_mk(body).decode("ascii"))


# ---------------------------------------------------------------------------
# 1. GST 进 fix 快照
# ---------------------------------------------------------------------------
def test_gst_merged_into_fix():
    reader = SerialGNSSReader()
    reader._merge(_parse(GGA_VALID))
    reader._merge(_parse(GST))

    fix = reader.get_fix()
    assert fix["source"] == "real"
    assert fix["hdop"] == 0.9
    assert fix["gst"] is not None
    assert fix["gst"]["lat_sigma"] == 0.8
    assert fix["gst"]["lon_sigma"] == 0.6
    assert fix["gst"]["alt_sigma"] == 5.0
    assert fix["gst"]["rms_std"] == 1.0


def test_gst_dropped_without_fix_and_cleared_on_loss():
    # 没有坐标的历元：GST 不落地（误差数字没有意义）
    reader = SerialGNSSReader()
    reader._merge(_parse(GST))
    assert reader.get_fix()["gst"] is None

    # 先有定位再失锁：必须清空旧 GST
    reader._merge(_parse(GGA_VALID))
    reader._merge(_parse(GST))
    assert reader.get_fix()["gst"] is not None
    reader._merge(_parse(GGA_INVALID))
    fix = reader.get_fix()
    assert fix["longitude"] is None
    assert fix["gst"] is None, "失锁后不得保留上一帧 GST"


# ---------------------------------------------------------------------------
# 2. 格式化：只读 GST，不补 0、不乘 HDOP
# ---------------------------------------------------------------------------
def test_format_gst_error():
    assert format_gst_error(None) == "—"
    assert format_gst_error({}) == "—"
    assert format_gst_error({"lat_sigma": 0.8, "lon_sigma": 0.6,
                             "alt_sigma": 5.0}) == \
        "纬 ±0.8 m · 经 ±0.6 m · 高 ±5.0 m"
    assert format_gst_error({"lat_sigma": 0.8}) == "纬 ±0.8 m"
    assert format_gst_error({"alt_sigma": "bad"}) == "—"
    # 只有 HDOP 时不得编造误差
    assert format_gst_error({"hdop": 0.9}) == "—"


# ---------------------------------------------------------------------------
# 3. 面板：与 HDOP 并列显示
# ---------------------------------------------------------------------------
def test_panel_shows_gst_next_to_hdop():
    panel = StatusPanel()
    panel.update_gnss({
        "source": "real", "latitude": 43.8667, "longitude": 125.3167,
        "altitude_m": 150.0, "satellites": 9, "hdop": 0.9,
        "gst": {"lat_sigma": 0.8, "lon_sigma": 0.6, "alt_sigma": 5.0},
    })
    assert panel.gps_hdop.text() == "0.9"
    assert panel.gps_error.text() == "纬 ±0.8 m · 经 ±0.6 m · 高 ±5.0 m"
    assert "几何" in panel.gps_hdop.toolTip()
    assert "HDOP" in panel.gps_error.toolTip()


def test_panel_without_gst_shows_dash():
    panel = StatusPanel()
    panel.update_gnss({
        "source": "real", "latitude": 43.8667, "longitude": 125.3167,
        "altitude_m": 150.0, "satellites": 9, "hdop": 0.9,
    })
    assert panel.gps_hdop.text() == "0.9"
    assert panel.gps_error.text() == "—"

    panel.update_gnss({"source": "none"})
    assert panel.gps_error.text() == "—"
