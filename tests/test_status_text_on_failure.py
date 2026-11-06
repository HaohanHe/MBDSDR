# SPDX-License-Identifier: MIT
"""失败路径的文案诚实性（tests/test_status_text_on_failure.py）
==================================================================

矩阵第三环：硬件写失败后，状态栏/完成态措辞是否还在说「成功」。修前三处：

1. 天空视图「调到该星」：``_on_tune_sdr`` 无返回值，调用方无条件写
   「已调谐 …」，把上一行「调谐未生效：…」覆盖成成功。
2. 扫频完成：调谐最强台失败先写「未能调谐…」，随后被无条件的
   「扫频完成：N 个活动频点」覆盖，用户最后只看到成功。
3. 收藏按钮：写收藏文件失败被 ``except: pass`` 吞掉，仍然只报「已收藏 …」。

另含两条「失败后不许继续/不许静默」：
4. 书签调谐失败后仍继续下发带宽（设备会被改到错误频率上）；
5. 面板扫描结束恢复前端频率失败被 ``except: pass`` 静默。

第四环（写盘类用户动作的成败可见性）：
6. 卫星云图「另存 PNG」写盘失败被 ``except: pass`` 吞掉，用户以为已保存；
7. AI 配置对话框无论是否写盘成功都报「AI 已配置并保存」；
8. 录制 tap 的兜底 ``except: pass`` 让未预期写盘异常后继续录，最后仍报「录制完成」。

反证：任一处改回旧行为，对应用例必红。
"""
import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qtapp(isolated_profile):
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def mw(qtapp):
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:  # noqa: BLE001
        pass
    w.close()


class _RefusingTuneBackend:
    """调谐显式返回 False，并给出可读原因（真实驱动的失败形态）。"""

    def __init__(self):
        self.status = SimpleNamespace(error="设备拒绝该频率")

    def get_status(self):
        return self.status

    def set_frequency(self, hz):
        return False


def test_sky_tune_refusal_does_not_report_tuned(mw):
    """调谐被拒时，状态栏最后一句不得是「已调谐」。"""
    mw._active_sdr_backend = _RefusingTuneBackend()

    mw._on_sky_tune_satellite({
        "name": "ISS",
        "tuned_freq_hz": 145_800_000.0,
        "mode": "NFM",
        "doppler_shift_hz": 0.0,
    })

    msg = mw.statusBar().currentMessage()
    assert "已调谐" not in msg, f"失败路径不得报已调谐：{msg!r}"
    assert "未生效" in msg, f"失败原因必须留在状态栏：{msg!r}"


def test_sweep_summary_keeps_tuning_failure(qtapp):
    """扫频摘要不得把「未能调谐到最强台」覆盖掉。"""
    backend = _RefusingTuneBackend()
    status_msgs = []
    spectrum = SimpleNamespace(
        set_markers=lambda m: None,
        generator=SimpleNamespace(center_freq_hz=100e6),
    )
    stub = SimpleNamespace(
        _active_sdr_backend=backend,
        spectrum=spectrum,
        ai_panel=SimpleNamespace(_add_system_message=lambda s: None),
        statusBar=lambda: SimpleNamespace(
            showMessage=lambda msg, sev=0: status_msgs.append(msg)),
        _sweep_cleanup=lambda: None,
        _tok=lambda k, d: d,
    )
    result = SimpleNamespace(
        activities=[SimpleNamespace(center_hz=145e6, bandwidth_hz=12.5e3,
                                    peak_db=-40.0)],
        noise_floor_db=-100.0,
        freqs_hz=[88e6, 108e6],
        threshold_db=-60.0,
    )
    from main_window import MainWindow

    MainWindow._on_sweep_done(stub, result)

    last = status_msgs[-1]
    assert "扫频完成" in last
    assert "未能调谐到最强台" in last, f"摘要覆盖了调谐失败：{last!r}"
    assert spectrum.generator.center_freq_hz == 100e6


def test_bookmark_persist_failure_is_reported(mw):
    """收藏文件写失败时，不得只说「已收藏」了事。"""

    class _BrokenFreqMgr:
        def add(self, *args, **kwargs):
            raise OSError("收藏文件不可写")

    mw._freq_mgr = _BrokenFreqMgr()

    mw._on_bookmark_added(145_000_000.0, "TEST", "FM")

    msg = mw.statusBar().currentMessage()
    assert "写入收藏文件失败" in msg, f"写盘失败必须可见：{msg!r}"
    assert "重启后会丢失" in msg, f"必须说清后果：{msg!r}"


def test_bookmark_tune_failure_does_not_touch_bandwidth(mw, monkeypatch):
    """书签调谐失败后不得再下发带宽（否则设备在错误频率上被改带宽）。"""
    bw_calls = []
    monkeypatch.setattr(mw, "_on_vfo_bandwidth_changed",
                        lambda bw: bw_calls.append(bw) or True)
    mw._active_sdr_backend = _RefusingTuneBackend()

    mw._on_bookmark_tune_requested(145_000_000.0, "NFM", 80_000.0)

    assert bw_calls == [], f"调谐失败后不得改带宽，实际调用 {bw_calls}"
    assert "未生效" in mw.statusBar().currentMessage()


def test_panel_scan_restore_failure_is_visible(qtapp):
    """扫描结束恢复前端频率失败不再静默，写明原因/当前显示/应回频点。"""
    msgs = []
    stub = SimpleNamespace(
        _active_sdr_backend=_RefusingTuneBackend(),
        _panel_scan_prev_center=145_800_000.0,
        spectrum=SimpleNamespace(
            generator=SimpleNamespace(center_freq_hz=146_500_000.0)),
        statusBar=lambda: SimpleNamespace(
            showMessage=lambda msg, sev=0: msgs.append(msg)),
        _start_iq_streams=lambda: None,
        _hw_failure_detail=lambda: "设备返回失败",
    )
    from main_window import MainWindow

    MainWindow._on_panel_scan_ended(stub)

    assert msgs, "恢复失败必须有可见提示"
    last = msgs[-1]
    assert "前端频率恢复失败" in last
    assert "当前显示为 146.500000 MHz" in last
    assert "设备未回到 145.800000 MHz" in last


# ======================================================================
# 第四环：写盘类用户动作的成败可见性
# ======================================================================

def test_satellite_save_png_failure_is_visible(qtapp, tmp_path, monkeypatch):
    """另存云图写失败必须报出来（旧实现 except: pass，用户以为已保存）。"""
    from PIL import Image
    from satellite_image_panel import SatelliteImagePanel

    src = tmp_path / "src.png"
    Image.new("L", (8, 8)).save(src)
    panel = SatelliteImagePanel()
    panel._current_png = str(src)
    target = tmp_path / "no-such-dir" / "out.png"   # 目录不存在 → 写盘必失败
    monkeypatch.setattr(
        "satellite_image_panel.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "")))

    panel._save_current()

    assert not target.exists()
    text = panel.stats_label.text()
    assert "保存 PNG 失败" in text, f"写盘失败必须可见：{text!r}"
    assert target.name in text, f"失败提示要点明目标文件：{text!r}"
    assert "云图已另存" not in text, f"失败路径不得出现成功文案：{text!r}"


def test_satellite_save_png_success_says_saved(qtapp, tmp_path, monkeypatch):
    """写盘成功才出现「云图已另存」，且文件真的落地。"""
    from PIL import Image
    from satellite_image_panel import SatelliteImagePanel

    src = tmp_path / "src.png"
    Image.new("L", (8, 8)).save(src)
    panel = SatelliteImagePanel()
    panel._current_png = str(src)
    target = tmp_path / "out.png"
    monkeypatch.setattr(
        "satellite_image_panel.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: (str(target), "")))

    panel._save_current()

    assert target.exists(), "成功路径必须真的写出文件"
    assert "云图已另存" in panel.stats_label.text()
    assert "保存 PNG 失败" not in panel.stats_label.text()


def _ai_panel_with_stubbed_side_effects(monkeypatch, tmp_path):
    """构造 AIPanel 并把落盘目标/状态刷新都隔离（不碰真实用户配置）。"""
    import ai_panel as ap
    if not ap.AI_CORE_AVAILABLE:
        pytest.skip("mbdsdr_ai 不可用，跳过 AI 面板配置用例")
    monkeypatch.setattr(ap.AIPanel, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(ap.AIPanel, "CONFIG_FILE",
                        str(tmp_path / "absent-config.json"))
    panel = ap.AIPanel()
    monkeypatch.setattr(panel, "_create_agent",
                        lambda cfg: SimpleNamespace(config=cfg))
    monkeypatch.setattr(panel, "_update_ai_status", lambda: None)
    monkeypatch.setattr(panel, "_apply_pending_mcp_tools", lambda: None)
    msgs = []
    monkeypatch.setattr(panel, "_add_system_message", msgs.append)
    return panel, msgs


def test_ai_config_write_failure_is_not_reported_as_saved(qtapp, tmp_path,
                                                         monkeypatch):
    """写盘失败时不得说「已配置并保存」（旧实现无条件报成功）。"""
    import ai_panel as ap
    panel, msgs = _ai_panel_with_stubbed_side_effects(monkeypatch, tmp_path)
    # 把目标设成已存在的目录 → open(path, "w") 必失败
    monkeypatch.setattr(ap.AIPanel, "CONFIG_FILE", str(tmp_path))

    panel._apply_config_from_dialog(
        "sk-test", "https://example.invalid/v1", "test/model")

    joined = "\n".join(msgs)
    assert "已配置并保存" not in joined, f"写盘失败不得报已保存：{joined!r}"
    assert "未能写入磁盘" in joined, f"必须说清没有落盘：{joined!r}"
    assert ap.AIPanel._last_save_error, "失败原因必须留档"


def test_ai_config_write_success_says_saved(qtapp, tmp_path, monkeypatch):
    """写盘成功时保留原完成态，并真的落盘。"""
    import ai_panel as ap
    panel, msgs = _ai_panel_with_stubbed_side_effects(monkeypatch, tmp_path)
    target = tmp_path / "config.json"
    monkeypatch.setattr(ap.AIPanel, "CONFIG_FILE", str(target))

    panel._apply_config_from_dialog(
        "sk-test", "https://example.invalid/v1", "test/model")

    assert target.exists(), "成功路径必须真的写出配置"
    joined = "\n".join(msgs)
    assert "已配置并保存" in joined, f"成功路径应保留完成态：{joined!r}"
    assert ap.AIPanel._last_save_error is None


def test_record_tap_write_error_aborts_instead_of_continuing():
    """录制 tap 的未预期写盘异常必须中止录音，不能吞掉后仍报「录制完成」。"""
    aborts = []
    stub = SimpleNamespace(
        _recording=True,
        _record_iq_buffer=[],
        _record_write_chunk=lambda iq: (_ for _ in ()).throw(
            RuntimeError("写盘异常")),
        _report_record_abort=aborts.append,
    )
    from main_window import MainWindow

    MainWindow._on_pipeline_record_iq(stub, object())

    assert aborts, "写盘异常未收口：录音会继续并最终报「录制完成」"
    assert "录音写入异常" in aborts[0]
