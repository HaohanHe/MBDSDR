# SPDX-License-Identifier: MIT
"""W67 反证：ADS-B 面板必须校验中心频率与解调模式，并能导出列表。

改前：任意频点/任意解调下都显示「等待 1090MHz 数据」，IQ 照解不误——用户
对着 145 MHz、NFM 的空面板以为设备没问题；表格也没有任何导出入口，
「这次收到的飞机」关窗即散。

改后：
  - `set_rx_context()` 同步频点/模式，非 1090 MHz 或非 AM 时给出具体原因，
    `feed_iq()` 直接不解码；
  - 频点/模式未知（None）不门控，不替设备下结论；
  - `export_csv()` 导出在视飞机；0 架时返回 False 且不落空文件。
"""
import csv
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

import adsb_panel as ap  # noqa: E402

ADSB = 1_090_000_000


@pytest.fixture
def panel(isolated_profile):
    p = ap.AdsbPanel()
    p.set_sdr_connected(True)
    yield p
    p.deleteLater()


class _RecordingDecoder:
    def __init__(self):
        self.calls = 0

    def decode_adsb(self, iq, sample_rate):
        self.calls += 1
        return {"frames": []}


def test_wrong_frequency_blocks_and_explains(panel, monkeypatch):
    dec = _RecordingDecoder()
    monkeypatch.setattr(ap, "_adsb_lite", dec)

    panel.set_rx_context(145_000_000, "am")
    assert panel.gate_reason() and "1090" in panel.gate_reason()
    assert "145.000" in panel.header.text()

    panel.feed_iq([0j] * 1024, 2_400_000)
    assert dec.calls == 0, "频点不对仍在解码：白烧 CPU 且掩盖了「收不到」"


def test_wrong_demod_blocks(panel):
    panel.set_rx_context(ADSB, "nfm")
    assert panel.gate_reason() and "AM" in panel.gate_reason()


def test_correct_context_passes_and_decodes(panel, monkeypatch):
    dec = _RecordingDecoder()
    monkeypatch.setattr(ap, "_adsb_lite", dec)

    panel.set_rx_context(ADSB, "am")
    assert panel.gate_reason() is None
    assert panel.header.text() == ap.ADSB_WAIT_TEXT

    panel.feed_iq([0j] * 1024, 2_400_000)
    assert dec.calls == 1


def test_unknown_context_does_not_gate(panel):
    panel.set_rx_context(None, None)
    assert panel.gate_reason() is None


def test_gate_shows_alongside_existing_aircraft(panel):
    panel.submit_decoded_frame({"icao": "780ABC", "callsign": "CCA101"})
    assert panel.aircraft_count() == 1
    panel.set_rx_context(145_000_000, "am")
    # 已收数据不清空，但要说清「新数据不会进来」。
    assert panel.aircraft_count() == 1
    assert "新数据不会进来" in panel.header.text()


def test_export_csv_writes_real_rows(panel, tmp_path):
    panel.submit_decoded_frame({
        "icao": "780ABC", "callsign": "CCA101",
        "lat": 39.9042, "lon": 116.4074, "altitude_ft": 32000,
        "groundspeed_kt": 450.4, "track_deg": 87.6,
    })
    out = tmp_path / "adsb.csv"
    assert panel.export_csv(str(out)) is True
    rows = list(csv.reader(out.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0][0] == "ICAO" and rows[0][-1] == "最后更新(s)"
    assert len(rows) == 2
    assert rows[1][0] == "780ABC" and rows[1][1] == "CCA101"
    assert rows[1][2] == "39.9042"


def test_export_csv_refuses_empty(panel, tmp_path):
    out = tmp_path / "empty.csv"
    assert panel.export_csv(str(out)) is False
    assert not out.exists(), "0 架时不许落空文件冒充导出成功"
