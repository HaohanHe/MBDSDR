# SPDX-License-Identifier: MIT
"""防呆守卫：破坏性操作确认 / 参数越界前置拦截 / 重复点击幂等。

覆盖本轮「UI 操作逻辑 + 防呆」中可在 ControlPanel / ScannerPanel 内闭环的项：

  D1  频率输入越界：键入超出 10 kHz–6 GHz 时**拒绝调谐并明确告知**，
      不再静默夹到边界后照常下发（旧行为 = 把 7 GHz 伪装成 6 GHz 调谐成功）。
  D2  扫描结果清空：已有结果时点「开始扫描」必须先确认，取消则原结果保留。
  D3  重复点击「开始扫描」：扫描线程在跑时再次点击不新建 worker（幂等）。
  D4  无设备时射频控件（调谐/模式/AGC/采样率/增益/静噪）一律置灰，
      而不是可点后再报错。

反证：删掉对应守卫后，D1 会重新收到 tune 信号、D2 结果被清空、
D3 worker 被替换——测试即红（见提交说明里的变异记录）。

运行:
    python -m pytest tests/test_destructive_confirm_guards.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


def _control_panel():
    from control_panel import ControlPanel
    cp = ControlPanel()
    cp.set_sdr_connected(True)
    return cp


def _scanner_panel():
    from scanner_panel import ScannerPanel
    return ScannerPanel()


# ---------------------------------------------------------------- D1 频率越界
def test_typed_out_of_range_frequency_is_rejected(monkeypatch):
    """7 GHz 超出 6 GHz 上限：不发调谐、不改内部真值、给出明确告警。"""
    cp = _control_panel()
    tuned = []
    cp.tune_sdr_requested.connect(lambda hz, mode: tuned.append(hz))
    warned = []
    monkeypatch.setattr(
        QMessageBox, "warning",
        lambda *a, **k: warned.append(a[2] if len(a) > 2 else ""))

    cp.freq_input.setText("7000M")
    cp._on_freq_input()

    assert tuned == [], "越界输入不得下发调谐（旧行为会夹到 6000 MHz）"
    assert cp._freq_hz is None, "越界输入不得写入内部频率真值"
    assert warned, "越界输入必须明确告知用户，而不是静默夹边界"


def test_typed_below_min_frequency_is_rejected(monkeypatch):
    """低于 10 kHz 下限同样拒绝，不留「夹到 10 kHz 算成功」的假象。"""
    cp = _control_panel()
    tuned = []
    cp.tune_sdr_requested.connect(lambda hz, mode: tuned.append(hz))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    cp.freq_input.setText("1k")
    cp._on_freq_input()

    assert tuned == []
    assert cp._freq_hz is None


def test_typed_in_range_frequency_still_tunes():
    """正常范围不受影响（防止守卫把合法输入也拦掉）。"""
    cp = _control_panel()
    tuned = []
    cp.tune_sdr_requested.connect(lambda hz, mode: tuned.append(hz))

    cp.freq_input.setText("144.390")
    cp._on_freq_input()

    assert tuned and abs(tuned[-1] - 144_390_000.0) < 1.0
    assert abs(cp._freq_hz - 144_390_000.0) < 1.0


# ---------------------------------------------------------------- D2 扫描清空
def test_rescan_with_existing_results_requires_confirmation(monkeypatch):
    """已有结果 → 开始新扫描前必须确认；选「否」时结果原样保留、不起线程。"""
    panel = _scanner_panel()
    panel.result_list.addItem("87.500 MHz  -80 dBm")
    panel.result_list.addItem("99.500 MHz  -75 dBm")
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: (asked.append(a[2] if len(a) > 2 else ""),
                         QMessageBox.No)[1])

    panel._on_start_clicked()

    assert asked, "有结果时未弹确认"
    assert panel.result_list.count() == 2, "取消后不得清空已有结果"
    assert panel._worker is None, "取消后不得启动扫描线程"


def test_rescan_without_results_starts_without_prompt(monkeypatch):
    """无结果时不应多问一句（避免每次扫描都被打扰）。"""
    import scanner_panel as sp_mod
    from PySide6.QtCore import QObject, Signal

    class _WorkerStub(QObject):
        progress = Signal(float, float)
        done = Signal(list)
        failed = Signal(str)
        finished = Signal()

        def __init__(self, *args, **kwargs):
            super().__init__()
            self.started = 0

        def start(self):
            self.started += 1

        def isRunning(self):  # noqa: N802 - 模拟 QThread 接口
            return False

        def cancel(self):
            pass

    monkeypatch.setattr(sp_mod, "_ScanWorker", _WorkerStub)
    panel = _scanner_panel()
    asked = []
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: asked.append(a) or QMessageBox.No)

    panel._on_start_clicked()

    assert asked == [], "无已有结果时不应弹「清空确认」"
    assert panel._worker is not None and panel._worker.started == 1


# ---------------------------------------------------------------- D3 重复点击
def test_start_scan_is_idempotent_while_running(monkeypatch):
    """扫描线程在跑时再次点击「开始扫描」→ 不替换、不新建 worker。"""
    panel = _scanner_panel()

    class _RunningWorker:
        def isRunning(self):  # noqa: N802 - 模拟 QThread 接口
            return True

    stub = _RunningWorker()
    panel._worker = stub
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: pytest_fail("扫描中不应弹确认"))

    panel._on_start_clicked()

    assert panel._worker is stub, "扫描中重复点击不得新建 worker"


def pytest_fail(msg):
    raise AssertionError(msg)


# ---------------------------------------------------------------- D4 无设备置灰
def test_rf_controls_disabled_without_device():
    """未连接设备时调谐/模式/AGC/采样率/增益/静噪全部置灰。"""
    from control_panel import ControlPanel
    cp = ControlPanel()
    cp.set_sdr_connected(False)

    for name in ("freq_input", "tune_button", "mode_combo", "agc_check",
                 "agc_speed_combo", "sample_rate_combo", "gain_slider",
                 "squelch_slider", "record_button"):
        widget = getattr(cp, name)
        assert widget.isEnabled() is False, f"{name} 在无设备时应置灰"

    cp.set_sdr_connected(True)
    assert cp.freq_input.isEnabled() is True
    assert cp.agc_check.isEnabled() is True
