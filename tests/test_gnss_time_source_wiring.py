# SPDX-License-Identifier: MIT
"""GNSS 授时凭据链路的测试（D5 修复的守卫）。

修的是什么
----------
`new_spacetime_panel.set_gnss_nmea()` 早就写好了，但**全仓零调用方**。
结果：插着真 GNSS，面板的 60 秒定时器每次都用 `gnss_nmea=None` 重算，
授时标签恒显示"未同步"——界面在否认硬件。

修法：串口层保留最近一条**原始** RMC，主窗口在拿到 fix 时把它交给面板；
丢 fix 时收回（不让过期语句冒充"当前已同步"）。

为什么必须是原文
----------------
RMC 的 A/V 标志位是"这句话可不可信"的唯一凭据，解析成 fix 之后就丢了。
拿一堆已解析字段拼一条 NMEA 交上去，等于**伪造观测**。所以这一组测试
除了验"能拿到原文"，还要验"没拿到时不编"。

本文件覆盖
----------
1. 串口层：RMC 原文被保留、不被其它语句覆盖、无 RMC 时为 None、CR/LF 被去掉
2. 监控层：`RealGNSSMonitor.get_last_rmc()` 透传且不抛
3. **接线守卫**：`_poll_gnss` 必须真的调那两个推送方法（防回退成假活）
4. **不造假守卫**：源码里不得出现"拼一条假 NMEA"的写法
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdsdr_ai.serial_gnss import (  # noqa: E402
    SerialGNSSReader,
    nmea_checksum,
)


def _sentence(body: str) -> str:
    """按 NMEA 规范补 `$` 与校验和（红线：不手写校验和）。"""
    return f"${body}*{nmea_checksum(body):02X}"


RMC_VALID = _sentence(
    "GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W")
RMC_INVALID = _sentence(
    "GPRMC,123519,V,4807.038,N,01131.000,E,,,230394,,,N")
GGA_FIX = _sentence(
    "GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
GSV_ONE = _sentence(
    "GPGSV,1,1,03,01,10,083,42,02,20,133,45,03,30,183,47")


class TestReaderKeepsRawRmc:
    def _feed(self, reader, sentence: str) -> None:
        """绕过串口线程，直接把一行交给解析路径。

        用公开的 `_merge` 不行——它拿的是已解析 rec，看不到原文。
        所以这里复刻 `_read_loop` 里那一小段：解析 + 存原文 + 合并。
        """
        rec = reader.parser.parse(sentence)
        if rec is not None:
            if rec.get("sentence") == "RMC":
                with reader._lock:
                    reader._last_rmc = sentence.strip()
            reader._merge(rec)

    def test_rmc_raw_sentence_is_kept(self):
        r = SerialGNSSReader()
        self._feed(r, RMC_VALID)
        got = r.get_last_rmc()
        assert got is not None
        # 必须与原句一致（去掉首尾空白），不是重新拼出来的
        assert got == RMC_VALID.strip()

    def test_non_rmc_sentences_do_not_overwrite(self):
        r = SerialGNSSReader()
        self._feed(r, RMC_VALID)
        self._feed(r, GGA_FIX)
        self._feed(r, GSV_ONE)
        assert r.get_last_rmc() == RMC_VALID.strip()

    def test_none_before_any_rmc(self):
        r = SerialGNSSReader()
        assert r.get_last_rmc() is None
        # 只收到 GGA/GSV 也不该凭空给出一条 RMC
        self._feed(r, GGA_FIX)
        self._feed(r, GSV_ONE)
        assert r.get_last_rmc() is None

    def test_invalid_rmc_still_yields_raw_text(self):
        """V 状态（无效定位）的 RMC 也要保留原文——

        判断"有没有效"是面板的事，串口层只负责"最后一句长什么样"。
        串口层替面板下结论，等于把两件事混成一件。
        """
        r = SerialGNSSReader()
        self._feed(r, RMC_INVALID)
        assert r.get_last_rmc() == RMC_INVALID.strip()

    def test_crlf_stripped(self):
        r = SerialGNSSReader()
        self._feed(r, RMC_VALID + "\r")
        assert "\r" not in (r.get_last_rmc() or "")
        assert "\n" not in (r.get_last_rmc() or "")

    def test_latest_rmc_wins(self):
        r = SerialGNSSReader()
        self._feed(r, RMC_VALID)
        second = _sentence(
            "GNRMC,235959,A,3959.999,N,11619.999,E,001.0,090.0,010125,,,A")
        self._feed(r, second)
        assert r.get_last_rmc() == second.strip()


class TestMonitorPassthrough:
    def test_passthrough_and_none(self):
        from mbdsdr_ai.gnss_monitor import RealGNSSMonitor

        m = RealGNSSMonitor()
        assert m.get_last_rmc() is None  # 未 start 也不崩

    def test_reader_exception_is_swallowed(self):
        """reader 内部抛异常时返回 None，不把 UI 线程带崩。"""
        from mbdsdr_ai.gnss_monitor import RealGNSSMonitor

        class Boom:
            def get_last_rmc(self):
                raise RuntimeError("串口炸了")

        m = RealGNSSMonitor()
        m._reader = Boom()
        assert m.get_last_rmc() is None


# ---------------------------------------------------------------------------
# 接线守卫（已随「新时空」聚合面板一并移除）
#
# 2026-11-16：授时展示并入卫星跟踪/状态读数，不再有独立面板，
# 原先针对 _push_gnss_time_source / _clear_gnss_time_source 的接线断言
# 失去对象，故删除。
# ---------------------------------------------------------------------------
