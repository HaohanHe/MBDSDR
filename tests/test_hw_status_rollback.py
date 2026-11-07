# SPDX-License-Identifier: MIT
"""set_agc/set_bandwidth 硬件失败必须回滚 status（审计 B 节 P2）。

病灶：RTL/Soapy 子类覆写走「super() 先写 status → 再碰硬件 → 失败 return
False」——super() 已把 agc_enabled / bandwidth_hz 斩后奏，失败路径不回滚，
status 留着一个从未生效的新值。基类 set_sample_rate/set_bandwidth/_apply_gain
的纪律是「_apply_* 钩子失败 → status 回滚 + error 可见」，子类纪律不一致。

契约（本轮钉扎）：
  - 硬件拒绝/抛异常 → 返回 False，status 回到旧值，status.error 非空；
  - 成功 → status 提交新值；
  - 未连接 → False 不动 status；
  - _sdr 为 None（无硬件句柄）→ 无操作成功，status 提交（既有语义）。

假件：注入 _sdr 双棒（set_agc_mode/agc_mode/setGainMode/setBandwidth
全拒），不碰真实硬件。
"""
import pytest

from mbdsdr_ai.sdr_backend import RTLSDRBackend, SoapySDRBackend


class _RejectAllSdr:
    """控制面全拒的假棒：两条 AGC 路径与带宽写入都抛。"""

    # ---- RTL 路径 ----
    def set_manual_gain_mode(self, mode):
        pass  # 第一跳不抛，逼进 set_agc_mode 主路径

    def set_agc_mode(self, enabled):
        raise OSError("agc rejected")

    @property
    def agc_mode(self):
        return False

    @agc_mode.setter
    def agc_mode(self, enabled):
        raise OSError("agc fallback rejected")

    @property
    def bandwidth(self):
        return 0

    @bandwidth.setter
    def bandwidth(self, bw):
        raise OSError("bandwidth rejected")

    # ---- Soapy 路径 ----
    def setGainMode(self, rx, chan, automatic):
        raise OSError("setGainMode rejected")

    def setBandwidth(self, rx, chan, bw):
        raise OSError("setBandwidth rejected")


class _AcceptAllSdr(_RejectAllSdr):
    """控制面全收的假棒（阳性对照）。"""

    def set_agc_mode(self, enabled):
        self.agc_mode = bool(enabled)

    @property
    def agc_mode(self):
        return getattr(self, "_agc", False)

    @agc_mode.setter
    def agc_mode(self, enabled):
        self._agc = bool(enabled)

    @property
    def bandwidth(self):
        return getattr(self, "_bw", 0)

    @bandwidth.setter
    def bandwidth(self, bw):
        self._bw = int(bw)

    def setGainMode(self, rx, chan, automatic):
        self._agc = bool(automatic)

    def setBandwidth(self, rx, chan, bw):
        self._bw = float(bw)


def _rtl():
    b = RTLSDRBackend(0)
    b.status.connected = True
    b.status.agc_enabled = False
    b.status.bandwidth_hz = 200_000.0
    return b


def _soapy():
    s = SoapySDRBackend("", {"driver": "fake", "label": "t", "serial": "s"})
    s.status.connected = True
    s.status.agc_enabled = False
    s.status.bandwidth_hz = 200_000.0
    return s


class TestRtlRollback:
    def test_agc_hw_failure_rolls_back(self):
        b = _rtl()
        b._sdr = _RejectAllSdr()
        assert b.set_agc(True) is False
        assert b.status.agc_enabled is False, "失败后 status 必须回到旧值"
        assert b.status.error, "失败必须写可见 error"

    def test_bandwidth_hw_failure_rolls_back(self):
        b = _rtl()
        b._sdr = _RejectAllSdr()
        assert b.set_bandwidth(12_000.0) is False
        assert b.status.bandwidth_hz == 200_000.0, "失败后带宽必须回滚"
        assert b.status.error, "失败必须写可见 error"

    def test_agc_success_commits(self):
        b = _rtl()
        b._sdr = _AcceptAllSdr()
        assert b.set_agc(True) is True
        assert b.status.agc_enabled is True

    def test_bandwidth_success_commits(self):
        b = _rtl()
        b._sdr = _AcceptAllSdr()
        assert b.set_bandwidth(12_000.0) is True
        assert b.status.bandwidth_hz == 12_000.0


class TestSoapyRollback:
    def test_agc_hw_failure_rolls_back(self):
        s = _soapy()
        s._sdr = _RejectAllSdr()
        assert s.set_agc(True) is False
        assert s.status.agc_enabled is False, "失败后 status 必须回到旧值"
        assert s.status.error, "失败必须写可见 error"

    def test_bandwidth_hw_failure_rolls_back(self):
        s = _soapy()
        s._sdr = _RejectAllSdr()
        assert s.set_bandwidth(12_000.0) is False
        assert s.status.bandwidth_hz == 200_000.0, "失败后带宽必须回滚"
        assert s.status.error, "失败必须写可见 error"

    def test_agc_success_commits(self):
        s = _soapy()
        s._sdr = _AcceptAllSdr()
        assert s.set_agc(True) is True
        assert s.status.agc_enabled is True

    def test_bandwidth_success_commits(self):
        s = _soapy()
        s._sdr = _AcceptAllSdr()
        assert s.set_bandwidth(12_000.0) is True
        assert s.status.bandwidth_hz == 12_000.0


class TestEdges:
    @pytest.mark.parametrize("mk", [_rtl, _soapy])
    def test_disconnected_rejected_no_change(self, mk):
        b = mk()
        b.status.connected = False
        b._sdr = _AcceptAllSdr()
        assert b.set_agc(True) is False
        assert b.set_bandwidth(12_000.0) is False
        assert b.status.agc_enabled is False
        assert b.status.bandwidth_hz == 200_000.0

    @pytest.mark.parametrize("mk", [_rtl, _soapy])
    def test_no_handle_is_noop_success(self, mk):
        b = mk()
        b._sdr = None
        assert b.set_agc(True) is True
        assert b.status.agc_enabled is True
        assert b.set_bandwidth(12_000.0) is True
        assert b.status.bandwidth_hz == 12_000.0

    def test_bandwidth_zero_auto_skips_hw_but_commits(self):
        s = _soapy()
        s._sdr = _RejectAllSdr()   # setBandwidth 会抛，但 bw=0 不该走到硬件
        assert s.set_bandwidth(0.0) is True
        assert s.status.bandwidth_hz == 0.0
