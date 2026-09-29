# SPDX-License-Identifier: MIT
"""确定性单测：后台解码 —— 注册、调度、结果持久化、异常检测。"""
import json
import os
import tempfile
import pytest

from mbdsdr_ai.background_decoder import (
    BackgroundDecoder, BackgroundScheduler, AnomalyDetector, DecodeResult,
)


def test_register_and_run_once():
    s = BackgroundScheduler(clock=lambda: 1000.0)
    s.register(BackgroundDecoder("adsb", 1090e6, decode_fn=lambda: {"icao": "ABC123", "altitude_ft": 35000}))
    results = s.run_once()
    assert len(results) == 1
    assert results[0].decoder == "adsb"
    assert results[0].data["icao"] == "ABC123"


def test_persistence_to_jsonl():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "decodes.jsonl")
        s = BackgroundScheduler(storage_path=path, clock=lambda: 1000.0)
        s.register(BackgroundDecoder("adsb", 1090e6, decode_fn=lambda: {"icao": "X", "altitude_ft": 10000}))
        s.run_once()
        assert os.path.exists(path)
        with open(path) as f:
            line = f.readline()
        rec = json.loads(line)
        assert rec["decoder"] == "adsb"
        assert rec["data"]["icao"] == "X"


def test_anomaly_detection_impossible_altitude():
    s = BackgroundScheduler(clock=lambda: 1000.0)
    s.register(BackgroundDecoder("adsb", 1090e6, decode_fn=lambda: {"altitude_ft": 99999}))
    results = s.run_once()
    assert results[0].anomaly is not None
    assert "altitude" in results[0].anomaly
    assert len(s.alerts) == 1


def test_anomaly_detection_aprs_missing_callsign():
    s = BackgroundScheduler(clock=lambda: 1000.0)
    s.register(BackgroundDecoder("aprs", 144.8e6, decode_fn=lambda: {"lat": 39.9, "lon": 116.4}))
    results = s.run_once()
    assert results[0].anomaly is not None
    assert "callsign" in results[0].anomaly


def test_no_anomaly_on_normal_data():
    s = BackgroundScheduler(clock=lambda: 1000.0)
    s.register(BackgroundDecoder("adsb", 1090e6, decode_fn=lambda: {"altitude_ft": 35000, "speed_kts": 400}))
    results = s.run_once()
    assert results[0].anomaly is None
    assert len(s.alerts) == 0


def test_unregister():
    s = BackgroundScheduler(clock=lambda: 1000.0)
    s.register(BackgroundDecoder("aprs", 144.8e6, decode_fn=lambda: {"callsign": "KJ6ABC"}))
    assert len(s.decoders) == 1
    s.unregister("aprs")
    assert len(s.decoders) == 0
