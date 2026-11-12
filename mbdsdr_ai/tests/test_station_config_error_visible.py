# SPDX-License-Identifier: MIT
"""A03：站址配置「坏了」与「没配」必须可区分（第二批）。

契约：
- 配置文件不存在 / 没写站址字段 → source="none" 且 error=""（真的没配）；
- 文件存在但损坏/不可读/字段类型非法 → source="none" 且 error 非空（必须提示修复）。

反证：把 station_from_config 的 except 改回统一返回
``StationFix(source="none")``（不写 error）后，本文件相关断言变红。
"""
import json
import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
sys.path.insert(0, _ROOT)

from mbdsdr_ai.station import station_from_config  # noqa: E402


def test_corrupt_config_reports_error(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not json", encoding="utf-8")

    fix = station_from_config(str(p))

    assert fix.source == "none"
    assert fix.lat is None
    assert fix.error.startswith("配置读取失败"), "坏配置必须带原因，不能等同于没配"
    assert "JSONDecodeError" in fix.error


def test_missing_config_is_not_an_error(tmp_path):
    fix = station_from_config(str(tmp_path / "missing.json"))
    assert fix.source == "none"
    assert fix.error == ""


def test_config_without_coords_is_not_an_error(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"sample_rate": 2.4}), encoding="utf-8")
    fix = station_from_config(str(p))
    assert fix.source == "none"
    assert fix.error == "", "字段缺失是真没配，不是配置坏了"


def test_non_dict_config_reports_error(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    fix = station_from_config(str(p))
    assert fix.source == "none"
    assert "不是 JSON 对象" in fix.error


def test_bad_coord_type_reports_error(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"ground_station_lat": "abc",
                             "ground_station_lon": 120.0}), encoding="utf-8")
    fix = station_from_config(str(p))
    assert fix.source == "none"
    assert "类型非法" in fix.error


def test_error_is_exposed_in_to_dict(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{ not json", encoding="utf-8")
    d = station_from_config(str(p)).to_dict()
    assert d["error"], "error 必须随 to_dict 一起暴露给调用方"
