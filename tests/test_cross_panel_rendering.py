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
import ast

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
    """只实现状态面板/增益回读真正用到的字段，不伪造任何硬件能力。"""

    def __init__(self, *, gain_db=GAIN_DB):
        self.device = _FakeDevice()
        self.tuner_name = "R820T2"
        self.ppm = 0
        self.offset_tuning = False
        self._samples_read = 2_400_000
        self._start_time = time.time() - 12.0
        self._gain_db = gain_db

    def get_gain(self):
        return self._gain_db

    def gain_readback_source(self):
        return "driver"

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
def test_frequency_is_the_same_string_in_all_three_panels():
    _ensure_app()
    from freq_display import digits_of

    st = _status_panel()
    cp = _control_panel()
    vp, _mgr = _vfo_panel()

    status_text = st.rx_freq.text()
    vfo_text = vp.table.item(0, 1).text()
    # 控制面板的大数字盘是 paintEvent 绘制的 9 位网格（…1 Hz），没有 text()；
    # 取同分辨率的数字串比对。
    dial_digits = digits_of(cp.freq_display.frequency())

    # freq_input 是**输入控件**（可编辑优先，允许简写），不参与读数一致性
    # 比较——要求输入框显示 145.500000 是拿可编辑性换一致性。
    assert status_text == vfo_text, (
        "同一频率 145.5 MHz 在两处读数不同：\n"
        f"  状态面板 rx_freq: {status_text!r}\n"
        f"  VFO 面板 列1:     {vfo_text!r}")
    assert status_text == "145.500000 MHz", (
        f"频率读数精度不是 1 Hz（六位小数 MHz）：{status_text!r}")
    assert dial_digits == re.sub(r"\D", "", status_text), (
        "控制面板数字盘与状态面板读数不是同一个数：\n"
        f"  数字盘 digits: {dial_digits!r}\n"
        f"  状态面板文本:  {status_text!r}")


# ===========================================================================
# 2. 增益：同一读数两处字符串必须一致
# ===========================================================================
def test_gain_is_the_same_string_in_both_panels_that_render_it():
    _ensure_app()
    st = _status_panel()
    st.set_gain_intent(GAIN_DB)
    cp = _control_panel(slider_gain_db=GAIN_DB)

    shown = {
        "状态面板 rx_gain": st.rx_gain.text(),
        "控制面板 gain_val": cp.gain_val.text(),
    }
    assert len(set(shown.values())) == 1, (
        "同一后端读数 20 dB 在两块面板显示成了不同字符串：\n"
        + "\n".join(f"  {k}: {v!r}" for k, v in shown.items())
    )
    assert st.rx_gain.text() == "20.0 dB", (
        f"增益读数不是一位小数：{st.rx_gain.text()!r}")
    # 意图与回读一致时不许出现「未生效」——告警必须只在真不一致时出现。
    assert "未生效" not in st.rx_gain.toolTip()


def test_gain_backend_clamped_is_flagged_in_both_panels():
    """后端回读 20 dB、配置意图 30 dB：两处都必须让用户看见「未生效」。"""
    _ensure_app()
    st = _status_panel(gain_db=GAIN_DB)
    # 意图由主窗口注入（面板自己不读配置/不读滑杆），这里模拟主窗口的落点。
    st.set_gain_intent(30)
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


def test_disconnect_clears_stale_gain_warning_and_its_numbers():
    """断开后 rx_gain 显「—」，tooltip 不许再引用任何具体 dB 数字。

    反证：把 ``status_panel.set_sdr_connected`` 里的 ``_apply_gain_readout(None)``
    换回 ``setText("—")``，本用例立刻变红——tooltip 仍停在
    「当前增益 30 dB 超出…已按 20 dB 显示」，而界面上这两个数字已经没了。
    """
    _ensure_app()
    st = _status_panel(gain_db=GAIN_DB)
    st.set_gain_intent(30)          # 回读 20 / 意图 30 → 进「未生效」
    assert "未生效" in st.rx_gain.toolTip(), (
        f"前置条件不成立：没有进入夹值告警：{st.rx_gain.toolTip()!r}")

    st.set_sdr_connected(False)

    assert st.rx_gain.text() == "—"
    assert not re.search(r"\d", st.rx_gain.toolTip()), (
        f"断开后 tooltip 仍引用具体数字：{st.rx_gain.toolTip()!r}")


# ===========================================================================
# 3. S 值：同一电平，两处 dBFS 文本必须一致；口径必须自证
# ===========================================================================
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
    assert st.rssi_value.text() == "-73.0 dBFS", (
        f"电平读数不是一位小数：{st.rssi_value.text()!r}")


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
def _bandwidth_token(text):
    """取带宽文本的「数字+单位」部分。

    控制面板的 ``vfo_bw_combo`` 是输入控件的**档位标签**，带模式注释
    （``"2.4kHz (SSB)"``）；状态面板与 VFO 面板是纯读数。注释不是带宽，
    剥离后再逐字比较——但两个纯读数之间必须逐字相同，不做任何归一化。
    """
    return text.split("(")[0].replace(" ", "")


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
    # 两个纯读数：必须逐字相同（2400 Hz 显示成 "2 kHz" 就是读数失真）。
    assert shown["状态面板 rx_bw"] == shown["VFO 面板 列3"], (
        "同一带宽 2400 Hz 在两处读数不同：\n"
        + "\n".join(f"  {k}: {v!r}" for k, v in shown.items())
    )
    # 档位标签只允许差在模式注释上，带宽 token 必须一致。
    tokens = {k: _bandwidth_token(v) for k, v in shown.items()}
    assert len(set(tokens.values())) == 1, (
        "同一带宽 2400 Hz 在不同面板显示成了不同字符串：\n"
        + "\n".join(f"  {k}: {v!r} → {tokens[k]!r}" for k, v in shown.items())
    )


# ===========================================================================
# 5. 守卫：配置意图只能由主窗口注入，状态面板不得自读配置
# ===========================================================================
def test_status_panel_does_not_import_config_or_settings():
    """AST 守卫：防止以后有人图省事让状态面板直接读配置文件。

    面板自读配置会造出第三个真值源（配置 / 主窗口内存 / 后端回读），
    配置还可能落后于正在生效的意图。
    """
    path = os.path.join(_ROOT, "desktop", "status_panel.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=path)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            low = name.lower()
            if "settings" in low or "config" in low:
                bad.append(name)
    assert not bad, (
        "status_panel.py 不得 import 配置/设置模块（意图只能由主窗口注入）："
        f"{bad}")


# ===========================================================================
# 6. AI 改增益：滑杆、意图、三块面板读数必须一起跟上
# ===========================================================================
@pytest.fixture(scope="module")
def main_window(isolated_profile):
    """真实 MainWindow（offscreen），用来验证 AI 改增益的编排路径。"""
    QApplication.instance() or QApplication([])
    from main_window import MainWindow

    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001 - 收尾失败不掩盖测试结论
        pass
    w.close()


def test_ai_set_gain_syncs_slider_and_intent_without_false_warning(main_window):
    """AI 把增益改到 20：滑杆显示 20，三块面板都不许误报「未生效」。

    反证的是「清空意图」那种做法：滑杆停在旧值、设备是新值，谁都不提。
    """
    w = main_window
    backend = _FakeBackend(gain_db=20.0)
    w._active_sdr_backend = backend
    w._panels_set_sdr_connected(True)

    w._sync_ui_for_tool("sdr_set_gain", {"gain_db": 20.0})

    assert w.control_panel.gain_slider.value() == 20, (
        f"AI 改了增益，滑杆还停在 {w.control_panel.gain_slider.value()}")
    assert w.module_panel.gain_slider.value() == 20, (
        f"AI 改了增益，模块面板滑杆还停在 {w.module_panel.gain_slider.value()}")
    for label, widget in (
        ("控制面板 gain_val", w.control_panel.gain_val),
        ("模块面板 gain_val", w.module_panel.gain_val),
        ("状态面板 rx_gain", w.status_panel.rx_gain),
    ):
        assert widget.text() == "20.0 dB", (
            f"{label} 读数不是 20.0 dB：{widget.text()!r}")
        assert "未生效" not in widget.toolTip(), (
            f"{label} 对一致的回读误报「未生效」：{widget.toolTip()!r}")
