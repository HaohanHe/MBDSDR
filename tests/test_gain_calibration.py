"""
增益校准表单元测试
=====================

覆盖：分段插值应用、JSON 持久化往返、AI 自动校准。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from mbdsdr_ai.gain_calibration import GainCalibrationTable


def test_offset_interpolation():
    tbl = GainCalibrationTable(device="test")
    tbl.add_point(100e6, 0.0)
    tbl.add_point(200e6, 6.0)
    # 端点
    assert tbl.offset_at(100e6) == 0.0
    assert tbl.offset_at(200e6) == 6.0
    # 中点
    assert abs(tbl.offset_at(150e6) - 3.0) < 1e-9
    # 表外夹紧
    assert tbl.offset_at(50e6) == 0.0
    assert tbl.offset_at(300e6) == 6.0


def test_apply():
    tbl = GainCalibrationTable()
    tbl.add_point(100e6, 2.0)   # 在 100MHz 测低 2 dB，补 2 dB
    # 测得 -50 dBm，补 2 dB → -48
    assert tbl.apply(-50.0, 100e6) == -48.0
    # 150MHz 插值补 1 dB → -49
    tbl.add_point(200e6, 0.0)
    assert tbl.apply(-50.0, 150e6) == -49.0


def test_json_roundtrip():
    tbl = GainCalibrationTable(device="hackrf", note="t")
    tbl.add_point(100e6, 1.5)
    tbl.add_point(500e6, -2.0)
    text = tbl.to_json()
    tbl2 = GainCalibrationTable.from_json(text)
    assert tbl2.device == "hackrf"
    assert tbl2.offset_at(100e6) == 1.5
    assert tbl2.offset_at(500e6) == -2.0


def test_save_load(tmp_path=None):
    tbl = GainCalibrationTable(device="plutosdr")
    tbl.add_point(400e6, 0.5)
    tbl.add_point(1000e6, -1.0)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cal.json")
        tbl.save(p)
        tbl2 = GainCalibrationTable.load(p)
        assert tbl2.device == "plutosdr"
        assert tbl2.offset_at(700e6) == -0.25


def test_auto_calibrate():
    freqs = np.array([100e6, 200e6, 300e6])
    measured = np.array([-50.0, -52.0, -49.0])
    # 参考 0 dB（理想 -50），偏移 = ref - measured
    tbl = GainCalibrationTable.auto_calibrate(freqs, measured,
                                              ref_level_db=-50.0,
                                              device="auto")
    # 200MHz 测低了 2 dB → 补 +2
    assert tbl.offset_at(200e6) == 2.0
    # 300MHz 测高了 1 dB → 补 -1
    assert tbl.offset_at(300e6) == -1.0
    # 应用后全部回到参考
    assert abs(tbl.apply(-52.0, 200e6) - (-50.0)) < 1e-9
