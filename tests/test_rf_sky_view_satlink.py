# SPDX-License-Identifier: MIT
"""
tests/test_rf_sky_view_satlink.py
==================================
验证 desktop/rf_sky_view.py 与链路目录 sat_links 的「点星即听」接线：

  1. 选中目录内卫星（如 "NOAA 19"）→ 信息卡拿到下行频点/体制与星性注记；
  2. 选中目录外卫星 → 信息卡文化层如实缺省（不编造）；
  3. 选中目录内卫星 → 「调到该星」的调谐参数可算出来且频点来自目录；
  4. 选中目录内卫星 → 解调模式按目录体制给（apt → WFM）。

运行：
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_rf_sky_view_satlink.py -v
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

# 固定历史 TLE（NOAA-19，epoch 2024-01-01），与 tests/test_satellite_tracker.py 同源。
TLE_L1 = ("1 33591U 09005A   24001.50000000  .00000300  00000-0  16087-3 0  9990")
TLE_L2 = ("2 33591  99.1900 312.3500 0014200  80.0000 280.0000 14.11715000458920")


def _make_view_with(name: str):
    """构造 RFSkyView，喂入名为 name 的卫星（带 TLE 目录）并选中。"""
    from desktop.rf_sky_view import RFSkyView, SkyObject
    view = RFSkyView()
    obj = SkyObject(name=name, azimuth_deg=180.0, elevation_deg=45.0,
                    obj_type="satellite")
    view.set_objects([obj])
    view.set_satellite_catalog([{
        "name": name, "line1": TLE_L1, "line2": TLE_L2, "freq_mhz": 0.0,
        "kind": "weather",
    }])
    view.set_observer(40.0, 116.0, 0.0)
    view.on_object_picked(obj)
    return view


class TestSatLinkInfoCard:
    def test_catalog_sat_shows_downlink_and_culture(self):
        view = _make_view_with("NOAA 19")
        info = view._selected_info or {}
        # 频点与体制来自 sat_links.json，不是空态
        assert info.get("downlink_mhz") == pytest.approx(137.1)
        assert info.get("downlink_mode") == "apt"
        # 星性注记非空（文化层座位坐上了人）
        assert info.get("culture")

    def test_machine_library_status_row_present(self):
        view = _make_view_with("NOAA 19")
        info = view._selected_info or {}
        # 机器库状态永远给一句人话（无缓存/离线也是如实「未取到」）
        assert info.get("downlink_machine")

    def test_machine_library_status_comes_from_data_layer(self, monkeypatch):
        from mbdsdr_ai import sat_links as SL
        monkeypatch.setattr(SL, "machine_entry_text",
                            lambda entry: "SatNOGS（0.1 小时前）")
        view = _make_view_with("NOAA 19")
        assert view._selected_info["downlink_machine"] == "SatNOGS（0.1 小时前）"

    def test_unknown_sat_honest_gap(self):
        view = _make_view_with("SOME UNKNOWN SAT")
        info = view._selected_info or {}
        assert info.get("downlink_mhz") is None
        assert info.get("culture") == ""
        assert info.get("downlink_machine") == ""


class TestSatLinkTune:
    def test_tune_computes_with_catalog_freq(self):
        view = _make_view_with("NOAA 19")
        res = view._compute_tune()
        assert res is not None
        assert res["base_freq_hz"] == pytest.approx(137.1e6)
        # 目录体制 apt → 宽带图像 WFM
        assert res["mode"] == "WFM"

    def test_tune_unknown_sat_disabled(self):
        view = _make_view_with("SOME UNKNOWN SAT")
        # 目录外卫星：调谐参数算不出来（诚实空态），按钮不启用
        assert view._compute_tune() is None
        assert not view._tune_btn.isEnabled()
