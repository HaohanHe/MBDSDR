# SPDX-License-Identifier: MIT
"""气象面板 FY-4 raw IQ 输入必须诚实拒绝，不得按字节伪造 PL 帧。"""
import os
import sys
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

app = QApplication.instance() or QApplication(sys.argv)  # noqa: F401

from desktop.weather_panel import DecodeWorker  # noqa: E402


def test_fy4_raw_iq_goes_through_real_ccsds_chain(tmp_path):
    """FY-4 raw IQ 现在走真实经典 CCSDS 链路；垃圾输入必须诚实失败。

    2026-11-06 变更：面板原先直接回绝「raw IQ 不支持」（stage=iq_sync /
    error_code=unsupported_raw_iq），但库层 `decode_fy4_iq_to_image` 早在
    tests/test_fy4_iq_chain.py 里就有 bpsk/qpsk 逐像素往返。面板说没有、
    库里有，属于断线。现在真接：允许读 IQ、允许解调。

    仍然钉死的红线：
      * 不得调用 DVB-S2 的 PL 符号同步（那是另一条路，本分支不冒充）；
      * 垃圾输入必须 ok=False、frames=0、带 stage 与原因；
      * 失败时不得留下半截图。
    """
    path = tmp_path / "fy4_raw.iq"
    path.write_bytes(np.arange(16, dtype=np.complex64).tobytes())
    png = tmp_path / "must_not_exist.png"
    worker = DecodeWorker({
        "family": "fy4", "satellite": "FY-4A HRIT", "iq_file": str(path),
        "out_png": str(png),
    })
    results = []
    failures = []
    worker.finished.connect(results.append)
    worker.failed.connect(failures.append)

    with mock.patch("mbdsdr_ai.fengyun_sat.fy4_dvbs2_sync") as dvbs2:
        worker.run()

    assert not dvbs2.called, "经典 CCSDS 链路不得调用 DVB-S2 的 PL 符号同步"
    assert failures == []
    assert len(results) == 1
    result = results[0]
    assert result["ok"] is False
    assert result["frames"] == 0
    assert result["stage"] in ("frame_sync", "decode", "demod", "segment_assembly")
    assert result["meta"]["raw_iq_supported"] is True
    assert result["meta"].get("attempts"), "两轮尝试的诊断要留下，便于排查"
    assert not png.exists(), "失败时不得留下半截图"


def test_status_line_frequency_uses_readouts():
    """A01：状态行频率读数走 readouts.format_frequency_hz，不就地拼「MHz」。

    输入框是用户可改的 MHz 字符串：是数值就按 1 Hz 口径（6 位小数 MHz）
    格式化，不降精度（145.5125 → 145.512500 MHz）；不是数值原样保留，
    不被静默吞掉，整行结构（频率 | 符号率 | 调制 | 频段 | 帧数）不变。
    """
    import readouts
    from desktop.weather_panel import WeatherPanel

    panel = WeatherPanel()
    try:
        panel.sat_combo.setCurrentText("GOES-16 HRIT (75.2°W)")
        panel.freq_edit.setText("145.5125")
        panel._update_status()

        expected = readouts.format_frequency_hz(145.5125e6)
        assert expected == "145.512500 MHz"
        text = panel.status_label.text()
        assert text.startswith(expected + " | "), text
        assert text.count("MHz") == 1, "频率单位只由 readouts 产生一次"
        assert "ksps" in text and "帧/段" in text, "整行信息结构不变"

        panel.freq_edit.setText("不是数字")
        panel._update_status()
        assert panel.status_label.text().startswith("不是数字 MHz | ")
    finally:
        panel.close()


def test_decode_failure_survives_queued_idle_refresh(monkeypatch):
    """A1：解码失败结论不能被排队的 _on_idle → _refresh_state 冲成「就绪」。

    失败弹窗 ``QMessageBox.critical`` 会自转事件循环，worker 的 ``finished``
    恰在这段时间派发到 ``_on_idle``；旧实现 ``_update_status()`` 无 extra 时
    回写纯频率行，「错误: …」就此消失。终态必须有保留字段，刷新只回显。
    """
    from desktop import weather_panel as wp

    monkeypatch.setattr(wp.QMessageBox, "critical", lambda *a, **k: None)
    panel = wp.WeatherPanel()
    try:
        panel._on_failed("读取失败: 文件不存在\n第二行详情")
        before = panel.status_label.text()
        assert "错误: 读取失败" in before
        # 排队回调：worker.finished → _on_idle，以及主动刷新
        panel._on_idle()
        panel._refresh_state()
        after = panel.status_label.text()
        assert "错误: 读取失败" in after, "失败结论必须留在状态行"
        assert "就绪" not in after, "失败不能被复位成「就绪」"
        assert after == before, "刷新不得改动终态状态行"
    finally:
        panel.close()


def test_second_failure_keeps_latest_and_flags_discarded(monkeypatch, caplog):
    """B1：两次失败，状态行保留最近那条并自证身份；被覆盖的那条不许静默消失。

    复现：先解码 A 抛异常（``错误: …``），再解码 B 正常失败（``未出图: …``）。
    用户最终看到 B（描述当前输入），A 退出状态行主体，但它的时间与身份必须
    仍可见（``另有 N 次失败``），完整正文进日志。
    """
    import logging

    from desktop import weather_panel as wp

    caplog.set_level(logging.WARNING, logger=wp.__name__)
    monkeypatch.setattr(wp.QMessageBox, "critical", lambda *a, **k: None)
    panel = wp.WeatherPanel()
    try:
        panel.sat_combo.setCurrentText("GOES-16 HRIT (75.2°W)")
        panel._run_identity = "GOES-16 HRIT (75.2°W) / a.iq"
        panel._on_failed("读取失败: 文件不存在\n第二行详情")
        panel._run_identity = "GOES-16 HRIT (75.2°W) / b.iq"
        panel._on_result({"ok": False, "error": "未锁相（符号率不匹配）"})

        text = panel.status_label.text()
        assert "未出图: 未锁相（符号率不匹配） · GOES-16 HRIT (75.2°W) / b.iq" in text, text
        assert "另有 1 次失败" in text, "被覆盖的失败必须可见，不许静默消失"
        assert "a.iq" in text, "时间/身份里必须认得出被丢掉的是哪一次"
        assert text.index("b.iq") < text.index("a.iq"), "最近一条在前，被丢弃的在后"
        # 完整正文（含第二行）进日志，不只留状态行的一行摘要
        assert "读取失败: 文件不存在" in caplog.text
        assert "第二行详情" in caplog.text
        assert "未锁相（符号率不匹配）" in caplog.text
    finally:
        panel.close()

