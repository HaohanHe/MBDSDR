# SPDX-License-Identifier: MIT
"""跨面板渲染一致性：同一份状态，三块面板显示出来的文本必须一致。

背景
----
``docs/research/2026-11-12-desktop-internal-duplication.md`` 用读代码的方式
数出「同一件事有 4 个具名出口 + 48 处内联字面量 + 四种频率精度」。读代码
只能证明「写法不同」，不能证明「同一份状态渲染出来真的不同」。本文件用
同一组模拟状态驱动多块面板，逐字比对它们真正显示给用户的字符串。

被测行为
--------
1. 同一个频率，三处必须是**同一个字符串**（已知 4 种精度：
   ``145.500000`` / ``145.5000`` / ``145.500`` / ``145.5``）。
2. 同一个增益，不能被一处标「未生效」另一处照常显示（后端回读 20 dB、
   配置意图 30 dB 时，两处都必须让用户看见「请求没照做」）。
3. 同一个 S 值，若两块面板口径不同（dBFS vs S 单位），**必须在文本上
   标明口径**，否则用户会以为有一块坏了。

刻意不做的事
------------
* 不修任何产品代码：本文件先红，红在哪几处就是待修清单。
* 不为让它绿而放宽判据：「三处不一致」本身就是被测的行为。
* 不造假控件替身：驱动不了的面板如实写明，不塞假数据冒充渲染。

驱动范围（诚实声明）
--------------------
* 状态面板：``StatusPanel.update_from_backend(fake)`` + ``update_signal_level``。
* 控制面板：``set_sdr_connected(True)`` → ``set_frequency_hz`` /
  ``set_vfo_bandwidth`` / ``set_gain_applied`` / ``update_signal_level``。
  频率的真实绘制在 ``FreqDisplay``（9 位数字网格，``paintEvent`` 绘制，
  没有 ``text()``），可比对的文本出口是同一面板的 ``freq_input``。
* VFO 面板：``VfoPanel(vfo_manager=VfoManager())``，注入真实内核
  ``VfoManager`` + 一个 145.5 MHz / 2.4 kHz 的 VFO。
* **设备面板驱动不了**：``DevicePanel`` 只有 ``refresh_devices()``（真实
  枚举）与 ``set_connected()``，没有「把一份模拟设备状态渲染出来」的入口；
  且该文件正由另一条线修改。按约定换成 VFO 面板，不用假控件替身。
* 过境卫星：这三个面板都不渲染卫星信息（在卫星面板），没有可对比点。
* 采样率：只有状态面板渲染，没有可对比点。
"""
import os
import re
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from PySide6.QtWidgets import QApplication  # noqa: E402

# 状态面板用 getattr 读状态字段，真实后端返回的是 SDRStatus 对象而非 dict：
# 这里直接用真实类型，避免用「假对象」把面板的读取接口测歪。
from mbdsdr_ai.sdr_backend import SDRStatus  # noqa: E402

# ---- 统一模拟状态：三个面板拿到的是同一组数 --------------------------------
FREQ_HZ = 145_500_000.0
SAMPLE_RATE_HZ = 2_400_000.0
BANDWIDTH_HZ = 2_400.0
GAIN_DB = 20.0
LEVEL_DBFS = -73.0          # 业余无线电约定的 S9
DEMOD = "NFM"


class _FakeDevice:
    name = "Fake RTL-SDR"
    device_type = "rtlsdr"
    device_id = "00000001"


class _FakeBackend:
    """只实现状态面板读到的字段，不伪造任何硬件能力。"""

    def __init__(self, *, gain_db=GAIN_DB):
        self.device = _FakeDevice()
        self.tuner_name = "R820T2"
        self.ppm = 0
        self.offset_tuning = False
        self._samples_read = 2_400_000
        self._start_time = time.time() - 12.0
        self._gain_db = gain_db

    def get_status(self):
        return SDRStatus(
            connected=True,
            frequency_hz=FREQ_HZ,
            sample_rate_hz=SAMPLE_RATE_HZ,
            bandwidth_hz=BANDWIDTH_HZ,
            gain_db=self._gain_db,
            demod_mode=DEMOD,
            snr_db=12.0,
            agc_enabled=False,
            squelch_db=None,
            volume=0.75,
            samples_read=self._samples_read,
            uptime_seconds=12.0,
        )


def _ensure_app():
    return QApplication.instance() or QApplication([])


def _status_panel(gain_db=GAIN_DB):
    from status_panel import StatusPanel

    panel = StatusPanel()
    panel.update_from_backend(_FakeBackend(gain_db=gain_db))
    panel.update_signal_level(LEVEL_DBFS)
    return panel


def _control_panel(*, slider_gain_db=GAIN_DB):
    """控制面板：先连接（否则 set_frequency_hz 不刷显示），再逐项回填。

    ``slider_gain_db`` 用来模拟「用户配置意图」与「后端回读值」不一致。
    """
    from control_panel import ControlPanel

    panel = ControlPanel()
    panel.set_sdr_connected(True)
    panel.set_frequency_hz(FREQ_HZ)
    panel.set_vfo_bandwidth(BANDWIDTH_HZ)
    panel.gain_slider.setValue(int(slider_gain_db))
    panel.set_gain_applied(GAIN_DB)
    panel.update_signal_level(LEVEL_DBFS)
    return panel


def _vfo_panel():
    from mbdsdr_ai.vfo_manager import VfoManager
    from vfo_panel import VfoPanel

    mgr = VfoManager()
    mgr.add(FREQ_HZ, BANDWIDTH_HZ, DEMOD)
    panel = VfoPanel(vfo_manager=mgr)
    panel.set_sdr_connected(True)
    panel.refresh()
    return panel, mgr


# ===========================================================================
# 1. 频率：同一频率，三处必须是同一个字符串
# ===========================================================================
@pytest.mark.xfail(
    strict=True,
    reason="跨面板频率格式未统一（145.500000 MHz / 145.5 / 145.500 MHz）；"
           "待「频率格式化收口」那一笔修好后删本标记")
def test_frequency_is_the_same_string_in_all_three_panels():
    _ensure_app()
    st = _status_panel()
    cp = _control_panel()
    vp, _mgr = _vfo_panel()

    shown = {
        "状态面板 rx_freq": st.rx_freq.text(),
        "状态面板 freq_value": st.freq_value.text(),
        "控制面板 freq_input": cp.freq_input.text(),
        "VFO 面板 列1": vp.table.item(0, 1).text(),
    }
    unique = set(shown.values())
    assert len(unique) == 1, (
        "同一频率 145.5 MHz 在不同面板显示成了不同字符串：\n"
        + "\n".join(f"  {k}: {v!r}" for k, v in shown.items())
    )


# ===========================================================================
# 2. 增益：同一读数两处字符串必须一致
# ===========================================================================
@pytest.mark.xfail(
    strict=True,
    reason="增益字符串格式未统一（状态 f'{gain:.1f} dB' / 控制 f'{value:g} dB'）；"
           "待「增益格式统一」那一笔修好后删本标记")
def test_gain_is_the_same_string_in_both_panels_that_render_it():
    _ensure_app()
    st = _status_panel()
    cp = _control_panel(slider_gain_db=GAIN_DB)

    shown = {
        "状态面板 rx_gain": st.rx_gain.text(),
        "控制面板 gain_val": cp.gain_val.text(),
    }
    assert len(set(shown.values())) == 1, (
        "同一后端读数 20 dB 在两块面板显示成了不同字符串：\n"
        + "\n".join(f"  {k}: {v!r}" for k, v in shown.items())
    )


@pytest.mark.xfail(
    strict=True,
    reason="状态面板没有接收「配置意图」的入口，后端夹值时不标未生效；"
           "待主窗口向状态面板注入意图（不得由面板自读配置）那一笔修好后删本标记")
def test_gain_backend_clamped_is_flagged_in_both_panels():
    """后端回读 20 dB、配置意图 30 dB：两处都必须让用户看见「未生效」。"""
    _ensure_app()
    st = _status_panel(gain_db=GAIN_DB)
    cp = _control_panel(slider_gain_db=30)

    flags = {
        "状态面板 rx_gain.toolTip": st.rx_gain.toolTip(),
        "控制面板 gain_val.toolTip": cp.gain_val.toolTip(),
    }
    silent = {k: v for k, v in flags.items() if "未生效" not in v}
    assert not silent, (
        "后端夹值（意图 30 dB、回读 20 dB）时下列面板照常显示、不标「未生效」：\n"
        + "\n".join(f"  {k}: {v!r}" for k, v in silent.items())
        + "\n全部 tooltip："
        + "\n".join(f"  {k}: {v!r}" for k, v in flags.items())
    )


# ===========================================================================
# 3. S 值：同一电平，两处 dBFS 文本必须一致；口径必须自证
# ===========================================================================
@pytest.mark.xfail(
    strict=True,
    reason="S 值 dBFS 格式未统一（状态 1 位小数 / 控制 0 位小数）；"
           "待「电平格式统一」那一笔修好后删本标记")
def test_s_level_is_the_same_dbfs_string_in_both_panels():
    _ensure_app()
    st = _status_panel()
    cp = _control_panel()

    shown = {
        "状态面板 rssi_value": st.rssi_value.text(),
        "控制面板 smeter_label": cp.smeter_label.text(),
    }
    assert len(set(shown.values())) == 1, (
        "同一电平 -73 dBFS 在两块面板显示成了不同字符串：\n"
        + "\n".join(f"  {k}: {v!r}" for k, v in shown.items())
    )


def test_s_readouts_declare_their_scale():
    """状态面板同时给 dBFS 与 S 单位：两个字符串各自必须标明口径。"""
    _ensure_app()
    st = _status_panel()
    cp = _control_panel()

    s_text = st.smeter_value.text()
    dbfs_text = cp.smeter_label.text()
    assert re.fullmatch(r"S\d+", s_text) or s_text.startswith("S9 "), (
        f"S 表读数没有标明是 S 单位：{s_text!r}")
    assert dbfs_text.endswith("dBFS"), (
        f"dBFS 读数没有标明口径：{dbfs_text!r}")


# ===========================================================================
# 4. 带宽：同一份状态，三处也必须一致
# ===========================================================================
@pytest.mark.xfail(
    strict=True,
    reason="状态面板 _fmt_bw 整数 kHz 截断：2400 Hz 显示成 2 kHz（读数失真）；"
           "待 _fmt_bw 精度修复后删本标记")
def test_bandwidth_is_the_same_string_where_rendered():
    _ensure_app()
    st = _status_panel()
    cp = _control_panel()
    vp, _mgr = _vfo_panel()

    shown = {
        "状态面板 rx_bw": st.rx_bw.text(),
        "控制面板 vfo_bw_combo": cp.vfo_bw_combo.currentText(),
        "VFO 面板 列3": vp.table.item(0, 3).text(),
    }
    unique = set(shown.values())
    assert len(unique) == 1, (
        "同一带宽 2400 Hz 在不同面板显示成了不同字符串：\n"
        + "\n".join(f"  {k}: {v!r}" for k, v in shown.items())
    )
