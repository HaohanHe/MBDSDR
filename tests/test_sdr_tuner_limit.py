# SPDX-License-Identifier: MIT
"""调谐器真实可调上界 + 写频失败可见性的行为测试（2026-11-06）。

背景（真机实测，见 docs/pending-patches/2026-11-06-limitation-adsb-1090-fc0012.md）：
本机一颗 Fitipower FC0012 在 948.500 MHz 写频回读一致，948.600 MHz 起
librtlsdr 报 ``[FC0012] no valid PLL combination found``，1090 MHz 更做不到；
写频失败后句柄进入不可恢复态（回读恒 0、再写触发 access violation，只能重连）。

修复前 ``RTLSDRBackend._apply_frequency`` 重试耗尽仍 ``return True``，
``set_frequency`` 把 ``status.frequency_hz`` 写成请求值且 ``status.error``
保持空串——界面据此显示「1090.000 MHz 已生效」。本文件钉住三条：
1. 超出真实上界 → 直接拒绝、不动硬件、状态回滚且给出人话；
2. 948.500 MHz 仍可调；
3. 范围内但硬件拒绝 → 返回 False、标记故障态、后续写频不再触达硬件。
"""

from __future__ import annotations

from mbdsdr_ai.sdr_backend import RTLSDRBackend


class _FakeSdr:
    """最小 pyrtlsdr 句柄替身：记录写入值，可配置为拒绝写入或回读不匹配。"""

    def __init__(self, reject: bool = False, readback_delta_hz: int = 0):
        self._center = 98_000_000
        self.reject = reject
        self.readback_delta_hz = readback_delta_hz
        self.set_values: list[int] = []

    @property
    def center_freq(self) -> int:
        return self._center + self.readback_delta_hz

    @center_freq.setter
    def center_freq(self, value: int) -> None:
        self.set_values.append(int(value))
        if self.reject:
            raise RuntimeError(
                'LIBUSB_ERROR_IO (-1): Input/output error '
                '"Could not set center_freq to %d Hz"' % int(value))
        self._center = int(value)


def _mk_backend(tuner: str = "FC0012", sdr=None) -> RTLSDRBackend:
    b = RTLSDRBackend(device_index=0)
    b.status.connected = True
    b.tuner_name = tuner
    b._sdr = sdr if sdr is not None else _FakeSdr()
    return b


def test_fc0012_rejects_1090_without_touching_hardware():
    """1090 MHz 超出实测上界：必须一句人话拒绝，且不向硬件下发。"""
    sdr = _FakeSdr()
    b = _mk_backend(sdr=sdr)
    old = b.status.frequency_hz
    assert b.get_frequency_bounds() == (22_000_000.0, 948_500_000.0)

    assert b.set_frequency(1090e6) is False
    assert sdr.set_values == [], "越界请求不得触达硬件（写频失败会让句柄不可恢复）"
    assert b.status.frequency_hz == old, "失败必须回滚，不许留 1090 假状态"
    err = b.status.error
    assert "948.500" in err and "1090.000" in err
    assert "FC0012" in err and "未向硬件下发" in err
    assert "已生效" not in err


def test_fc0012_still_tunes_at_measured_upper_bound():
    """948.500 MHz 是实测可用值，不能被上限保护误伤。"""
    sdr = _FakeSdr()
    b = _mk_backend(sdr=sdr)

    assert b.set_frequency(948.5e6) is True
    assert b.status.frequency_hz == 948.5e6
    assert b.status.error == ""
    assert sdr.set_values == [948_500_000]


def test_in_range_hardware_failure_is_reported_and_faults_tuner():
    """范围内写频失败：返回 False + 可读原因，并标记故障态拦住后续写频。"""
    sdr = _FakeSdr(reject=True)
    b = _mk_backend(sdr=sdr)
    old = b.status.frequency_hz
    resets = []
    b._reset_rtl_buffer = lambda: resets.append(1)  # 故障态不该再调原生 reset

    assert b.set_frequency(900e6) is False
    assert b.status.frequency_hz == old, "失败必须回滚"
    assert "调谐器未生效" in b.status.error
    assert "需重新连接设备" in b.status.error
    assert len(sdr.set_values) == 3, "应重试 3 次才判失败"
    assert resets == [], "故障句柄不得再调 reset_buffer"

    # 故障态保护：后续写频直接拒绝，不再触达已损坏的句柄。
    assert b.set_frequency(900e6) is False
    assert len(sdr.set_values) == 3
    assert "故障态" in b.status.error


def test_readback_mismatch_is_not_reported_as_success():
    """写入没抛错但回读不匹配 → 仍是失败，不许把没生效装成生效。"""
    sdr = _FakeSdr(readback_delta_hz=1_000_000)
    b = _mk_backend(sdr=sdr)

    assert b.set_frequency(900e6) is False
    assert b.status.frequency_hz != 900e6
    assert "未生效" in b.status.error


def test_unknown_tuner_keeps_declared_range():
    """型号探测失败时不替硬件编造范围（宁可用声明区间，也不假装知道）。"""
    b = _mk_backend(tuner="Unknown", sdr=_FakeSdr())
    assert b._frequency_bounds() == (500_000.0, 1_766_000_000.0)


def test_direct_sampling_bypasses_tuner_bound():
    """直接采样绕过 tuner，不能拿 tuner 频率表把它挡在 22 MHz 以上。"""
    b = _mk_backend(sdr=_FakeSdr())
    b._direct = 1
    assert b._frequency_bounds() == (500_000.0, 1_766_000_000.0)
