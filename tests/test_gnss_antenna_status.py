# SPDX-License-Identifier: MIT
"""GNSS 天线状态可见性（Task 105）。

真机原始流里的 ``$GPTXT,...,ANTENNA OPEN`` 是唯一的天线告警凭据。
本文件钉住四件事：

1. GPTXT 报 ``ANTENNA OPEN`` → fix / GNSSPosition / 运行时快照均为 ``open``，
   HUD 文案出现「开路」；
2. 随后收到 ``ANTENNA OK`` → 告警被清除，HUD 不再出现「开路/短路」；
3. 没有 GPTXT → ``unknown``，HUD 不新增文案（不编造“正常”）；
4. 大小写 / 多余空格（含制表符）容错。

全部使用合成 NMEA（校验和动态计算）+ 假字符串，不开串口、不启线程。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.gnss_monitor import RealGNSSMonitor  # noqa: E402
from mbdsdr_ai.runtime_sources import RuntimeSources  # noqa: E402
from mbdsdr_ai.serial_gnss import NMEAParser, SerialGNSSReader, nmea_checksum  # noqa: E402
from mbdsdr_ai.sky_hud import build_hud  # noqa: E402

SIZE = (800.0, 600.0)
VIEW = {"center_az": 120.0, "center_alt": 45.0, "fov_deg": 2.3,
        "rotation_deg": 0.0, "min_fov": 0.5}
POSE = {"valid": True, "az_deg": 120.0, "alt_deg": 45.0,
        "roll_deg": 0.0, "fov_deg": 2.3}


def _sentence(body: str) -> str:
    """由 body（不含 $ 和 *）生成带正确校验和的 NMEA 行。"""
    return f"${body}*{nmea_checksum(body):02X}"


def _feed(reader: SerialGNSSReader, body: str) -> dict:
    """解析一条 TXT 并合并进 reader（不经串口线程）。"""
    rec = NMEAParser().parse(_sentence(body))
    assert rec is not None, f"合成语句解析失败: {body}"
    assert rec["sentence"] == "TXT"
    reader._merge(rec)
    return rec


class _FixReader:
    """最小 reader 替身：把当前 fix 透传给 RealGNSSMonitor。"""

    def __init__(self, reader: SerialGNSSReader):
        self._reader = reader

    def get_fix(self) -> dict:
        return self._reader.get_fix()

    def is_connected(self) -> bool:
        return True


def _monitor_for(reader: SerialGNSSReader) -> RealGNSSMonitor:
    monitor = RealGNSSMonitor()
    monitor._reader = _FixReader(reader)
    # 测试不往进程级 runtime_sources 里发布（不需要，也避免跨用例污染）。
    monitor._publishing = True
    return monitor


def _snapshot_and_text(monitor: RealGNSSMonitor):
    snap = RuntimeSources(gnss=monitor).snapshot()
    hud = build_hud(POSE, [], "L3", VIEW, SIZE, runtime_sources=snap)
    return snap, hud["sources_text"]


def test_open_antenna_reaches_fix_position_snapshot_and_hud():
    reader = SerialGNSSReader()
    _feed(reader, "GPTXT,01,01,02,ANTENNA OPEN")

    fix = reader.get_fix()
    assert fix["antenna"] == "open"
    assert "ANTENNA OPEN" in fix["antenna_text"]

    monitor = _monitor_for(reader)
    pos = monitor.get_position()
    assert pos.antenna == "open"
    assert "ANTENNA OPEN" in pos.antenna_text

    snap, text = _snapshot_and_text(monitor)
    assert snap["gnss"]["antenna"] == "open"
    assert "GNSS 天线开路" in text


def test_ok_clears_open_alarm():
    reader = SerialGNSSReader()
    _feed(reader, "GPTXT,01,01,02,ANTENNA OPEN")
    assert reader.get_fix()["antenna"] == "open"

    _feed(reader, "GPTXT,01,01,02,ANTENNA OK")
    fix = reader.get_fix()
    assert fix["antenna"] == "ok"
    assert "ANTENNA OK" in fix["antenna_text"]

    monitor = _monitor_for(reader)
    assert monitor.get_position().antenna == "ok"
    snap, text = _snapshot_and_text(monitor)
    assert snap["gnss"]["antenna"] == "ok"
    assert "开路" not in text
    assert "短路" not in text


def test_no_gptxt_is_unknown_and_hud_stays_silent():
    reader = SerialGNSSReader()
    fix = reader.get_fix()
    assert fix["antenna"] == "unknown"
    assert fix["antenna_text"] is None

    monitor = _monitor_for(reader)
    assert monitor.get_position().antenna == "unknown"
    snap, text = _snapshot_and_text(monitor)
    assert snap["gnss"]["antenna"] == "unknown"
    assert "开路" not in text
    assert "短路" not in text


def test_case_and_whitespace_tolerance():
    reader = SerialGNSSReader()
    # 小写 + 连续空格
    _feed(reader, "GPTXT,01,01,02,antenna   open")
    assert reader.get_fix()["antenna"] == "open"

    # 混合大小写 + 制表符
    _feed(reader, "GPTXT,01,01,02,\tAntenna\tShOrT ")
    fix = reader.get_fix()
    assert fix["antenna"] == "short"

    monitor = _monitor_for(reader)
    snap, text = _snapshot_and_text(monitor)
    assert snap["gnss"]["antenna"] == "short"
    assert "GNSS 天线短路" in text

    # antenna_text 截断到 80 字，不把整段厂商文本塞进快照
    _feed(reader, "GPTXT,01,01,02,ANTENNA OK " + "X" * 200)
    assert len(reader.get_fix()["antenna_text"]) == 80
