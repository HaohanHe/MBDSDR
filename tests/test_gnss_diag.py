# SPDX-License-Identifier: MIT
"""scripts/gnss_diag.py 的行为测试（假 reader，不碰硬件）。

钉住的是脚本的诚实契约：五段顺序、失败即停、退出码 0–5、--json 字段，
以及「天线告警不算失败、只记 warning」。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "gnss_diag.py")

TIMEOUT = "0.05"


def _load():
    spec = importlib.util.spec_from_file_location("gnss_diag", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # dataclass 在 Py3.14 会按 cls.__module__ 回查 sys.modules；先注册再执行。
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fix(**overrides):
    base = {
        "source": "none",
        "latitude": None, "longitude": None, "altitude_m": None,
        "satellites": None, "hdop": None, "fix_quality": 0,
        "utc_time": None, "utc_datetime": None, "date": None,
        "antenna": "unknown", "antenna_text": None, "timestamp": None,
    }
    base.update(overrides)
    return base


def _nmea_only():
    """收到合法 NMEA，但还没定位（source=real、坐标 None）。"""
    return _fix(source="real", utc_time="072545.00", timestamp=1.0)


def _fixed_no_utc():
    """有坐标、有 GGA 统计，但没有完整 UTC（只有 hhmmss）。"""
    return _fix(source="real", latitude=43.7868, longitude=126.6289,
                satellites=9, hdop=0.9, fix_quality=1,
                utc_time="072545.00", timestamp=1.0)


def _full(antenna="unknown", antenna_text=None):
    return _fix(source="real", latitude=43.7868, longitude=126.6289,
                altitude_m=222.4, satellites=16, hdop=0.8, fix_quality=1,
                utc_time="202859.000",
                utc_datetime="2026-11-17T20:28:59+00:00",
                date="171126", antenna=antenna, antenna_text=antenna_text,
                timestamp=1.0)


class FakeReader:
    """最小 reader 替身：start/get_fix/stop 三个方法就是脚本用到的全部接口。"""

    def __init__(self, fixed=None, start_ok=True, start_error=None):
        self._fix = fixed if fixed is not None else _nmea_only()
        self.start_ok = start_ok
        self.start_error = start_error
        self.started = []
        self.stopped = False

    def start(self, port, baudrate):
        self.started.append((port, baudrate))
        if self.start_error is not None:
            raise self.start_error
        return self.start_ok

    def get_fix(self):
        return dict(self._fix)

    def stop(self):
        self.stopped = True


def _must_not_be_called(*_a, **_kw):
    raise AssertionError("上游失败时不应进入该阶段")


def test_all_stages_pass_exit_0(capsys):
    m = _load()
    reader = FakeReader(_full())
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_OK == 0
    out = capsys.readouterr().out
    assert "[1/5]" in out and "[5/5]" in out
    assert out.count("PASS") == 5
    assert "FAIL" not in out
    assert reader.started == [("COM4", 9600)]
    assert reader.stopped is True


def test_no_port_exit_1(capsys):
    m = _load()
    rc = m.main(
        ["--timeout", TIMEOUT],
        make_reader=_must_not_be_called,
        list_ports=lambda: [],
    )
    assert rc == m.EXIT_NO_PORT == 1
    out = capsys.readouterr().out
    assert "[1/5]" in out and "[2/5]" not in out
    assert "未发现可用 GNSS 串口" in out


def test_auto_detect_all_open_failed_exit_2(capsys):
    """候选存在但全部打不开（占用/无权限）→ open_failed，不许谎报 no_port。"""
    m = _load()
    readers = []

    def make():
        reader = FakeReader(
            start_error=PermissionError(13, "Access is denied"))
        readers.append(reader)
        return reader

    rc = m.main(
        ["--timeout", TIMEOUT, "--json"],
        make_reader=make,
        list_ports=lambda: [{"device": "COM4"}, {"device": "COM5"}],
    )
    assert rc == m.EXIT_OPEN_FAILED == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "open_failed"
    assert payload["exit_code"] == 2
    assert [s["name"] for s in payload["stages"]] == ["open"]
    detail = payload["stages"][0]["detail"]
    assert "COM4" in detail and "COM5" in detail
    assert "PermissionError" in detail and "13" in detail
    assert len(readers) == 2
    assert all(r.stopped for r in readers)


def test_auto_detect_opened_but_no_nmea_exit_3(capsys):
    """自动路径打开成功却没有 NMEA → 沿用 no_nmea，而不是 no_port。"""
    m = _load()
    reader = FakeReader(_fix())
    rc = m.main(
        ["--timeout", TIMEOUT],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_NO_NMEA == 3
    out = capsys.readouterr().out
    assert "已打开 COM4 @ 9600 8N1（自动探测命中）" in out
    assert "未收到任何合法 NMEA" in out
    assert reader.stopped is True


def test_auto_detect_uses_first_openable_candidate(capsys):
    """第一候选打不开时继续试第二候选，命中后按第二候选跑完五段。"""
    m = _load()
    readers = [
        FakeReader(start_error=PermissionError(13, "Access is denied")),
        FakeReader(_full()),
    ]

    def make():
        return readers.pop(0)

    rc = m.main(
        ["--timeout", TIMEOUT],
        make_reader=make,
        list_ports=lambda: [{"device": "COM4"}, {"device": "COM5"}],
    )
    assert rc == m.EXIT_OK == 0
    out = capsys.readouterr().out
    assert "已打开 COM5 @ 9600 8N1（自动探测命中）" in out
    assert readers == []
    assert out.count("PASS") == 5


def test_open_failed_exit_2(capsys):
    m = _load()
    reader = FakeReader(start_ok=False)
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_OPEN_FAILED == 2
    out = capsys.readouterr().out
    assert "打开 COM4@9600 失败" in out
    assert "[2/5]" not in out
    assert reader.stopped is True  # 失败路径也必须关串口


def test_open_raises_exit_2(capsys):
    m = _load()
    reader = FakeReader(start_error=OSError("Access is denied"))
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_OPEN_FAILED == 2
    out = capsys.readouterr().out
    assert "OSError" in out and "Access is denied" in out
    assert reader.stopped is True


def test_no_nmea_exit_3(capsys):
    m = _load()
    reader = FakeReader(_fix())
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_NO_NMEA == 3
    out = capsys.readouterr().out
    assert "[2/5]" in out and "未收到任何合法 NMEA" in out
    assert "[3/5]" not in out
    assert reader.stopped is True


def test_nmea_but_no_fix_exit_4(capsys):
    m = _load()
    reader = FakeReader(_nmea_only())
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_NO_FIX == 4
    out = capsys.readouterr().out
    assert "[3/5]" in out and "无有效定位" in out
    assert "[4/5]" not in out
    assert reader.stopped is True


def test_no_full_utc_exit_5(capsys):
    m = _load()
    reader = FakeReader(_fixed_no_utc())
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_NO_UTC == 5
    out = capsys.readouterr().out
    assert "[4/5]" in out and "没有完整 UTC" in out
    assert "[5/5]" not in out
    assert reader.stopped is True


def test_antenna_open_is_warning_but_exit_0(capsys):
    m = _load()
    reader = FakeReader(_full(antenna="open",
                              antenna_text="01,01,01,ANTENNA OPEN"))
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_OK == 0
    out = capsys.readouterr().out
    assert "ANTENNA OPEN" in out
    assert "警告：天线状态 OPEN" in out
    assert "FAIL" not in out


def test_json_fields_and_stage_order(capsys):
    m = _load()
    reader = FakeReader(_full(antenna="open",
                              antenna_text="01,01,01,ANTENNA OPEN"))
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT, "--json"],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["port"] == "COM4"
    assert payload["baud"] == 9600
    assert payload["verdict"] == "ok"
    assert payload["exit_code"] == 0
    stages = payload["stages"]
    assert [s["name"] for s in stages] == [
        "open", "nmea", "fix", "utc", "antenna"]
    for st in stages:
        assert set(st) == {"name", "ok", "detail", "elapsed_ms"}
        assert st["ok"] is True
        assert isinstance(st["detail"], str) and st["detail"]
        assert isinstance(st["elapsed_ms"], (int, float))
        assert st["elapsed_ms"] >= 0
    fix = payload["fix"]
    for key in ("source", "latitude", "longitude", "altitude_m",
                "satellites", "hdop", "fix_quality", "utc_time",
                "utc_datetime", "date"):
        assert key in fix
    assert fix["latitude"] == 43.7868
    assert fix["utc_datetime"] == "2026-11-17T20:28:59+00:00"
    assert payload["antenna"] == {
        "status": "open",
        "text": "01,01,01,ANTENNA OPEN",
        "warning": True,
    }


def test_json_on_failure_keeps_same_contract(capsys):
    m = _load()
    reader = FakeReader(_fix())
    rc = m.main(
        ["--port", "COM4", "--timeout", TIMEOUT, "--json"],
        make_reader=lambda: reader,
        list_ports=lambda: [{"device": "COM4"}],
    )
    assert rc == m.EXIT_NO_NMEA == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == "no_nmea"
    assert payload["exit_code"] == 3
    assert [s["name"] for s in payload["stages"]] == ["open", "nmea"]
    assert payload["stages"][-1]["ok"] is False
    assert set(payload["fix"]) >= {"source", "latitude", "longitude",
                                   "satellites", "utc_datetime"}
    assert payload["antenna"] == {
        "status": "unknown", "text": None, "warning": False}
    assert reader.stopped is True

