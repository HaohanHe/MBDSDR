# SPDX-License-Identifier: MIT
"""
解调后音频 AGC 档位（gqrx Fast/Medium/Slow）钉扎测试
======================================================

P1-5 施工验收：
  * SharedDemodConfig.agc_speed 默认 medium、小写归一；
  * DemodWorker._apply_audio_agc：off 直通（原数组不动）、三档各建实例且
    缓存复用、弱信号经 AGC 后 RMS 抬升、未知档按 medium 处理、立体声
    形状保持；
  * ControlPanel：下拉默认「中」、切换发 agc_speed_changed(data key)、
    set_agc_speed 恢复不发射、非法档回退 medium、断连置灰；
  * MainWindow：槽写 _demod_cfg.agc_speed + settings 持久化，启动恢复
    路径把持久值灌回 _demod_cfg。

不依赖硬件 / 声卡 / 真实 IQ。
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

log = logging.getLogger(__name__)

from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# SharedDemodConfig
# ---------------------------------------------------------------------------
def test_shared_config_default_and_normalize():
    from receive_pipeline import SharedDemodConfig
    cfg = SharedDemodConfig()
    assert cfg.agc_speed == "medium"
    cfg2 = SharedDemodConfig(agc_speed="FAST")
    assert cfg2.agc_speed == "fast"
    # 既有字段不受影响
    assert cfg.mode == "FM" and cfg.auto_squelch is True


# ---------------------------------------------------------------------------
# DemodWorker._apply_audio_agc
# ---------------------------------------------------------------------------
def _make_worker(speed: str):
    from receive_pipeline import DemodWorker, SharedDemodConfig
    cfg = SharedDemodConfig(agc_speed=speed)
    # 音频 AGC 不碰流/播放器/声卡，全部 None 即可
    return DemodWorker(None, None, None, cfg, 2.4e6)


def test_agc_off_passthrough():
    w = _make_worker("off")
    x = np.full(4096, 0.01, dtype=np.float64)
    out = w._apply_audio_agc(x)
    assert out is x                      # 直通：同一对象，零拷贝零增益
    assert not w._audio_agc              # 不建任何 AGC 实例


def test_agc_raises_weak_signal_rms():
    rng = np.random.default_rng(7)
    w = _make_worker("fast")
    x = (rng.standard_normal(4096) * 0.01).astype(np.float64)
    rms_in = float(np.sqrt(np.mean(x ** 2)))
    out = x
    for _ in range(40):                  # 多帧连续处理，增益爬升
        out = w._apply_audio_agc(out)
    rms_out = float(np.sqrt(np.mean(out ** 2)))
    assert rms_out > rms_in * 5.0        # 明显抬升（target 0.5）
    assert "fast" in w._audio_agc


def test_agc_per_speed_instances_cached():
    w = _make_worker("fast")
    x = np.full(1024, 0.01, dtype=np.float64)
    w._apply_audio_agc(x)
    w._apply_audio_agc(x)
    assert len(w._audio_agc) == 1
    inst = w._audio_agc["fast"]
    # 切档再切回：各自实例独立，回来还是原实例（增益状态连续）
    w._cfg.agc_speed = "slow"
    w._apply_audio_agc(x)
    assert set(w._audio_agc) == {"fast", "slow"}
    w._cfg.agc_speed = "fast"
    w._apply_audio_agc(x)
    assert w._audio_agc["fast"] is inst


def test_agc_unknown_speed_falls_back_medium():
    w = _make_worker("bogus")
    x = np.full(1024, 0.01, dtype=np.float64)
    w._apply_audio_agc(x)
    assert "medium" in w._audio_agc


def test_agc_stereo_shape_preserved():
    rng = np.random.default_rng(11)
    w = _make_worker("medium")
    x = (rng.standard_normal((2048, 2)) * 0.01).astype(np.float64)
    out = w._apply_audio_agc(x)
    assert out.shape == (2048, 2)
    rms_in = float(np.sqrt(np.mean(x ** 2)))
    rms_out = float(np.sqrt(np.mean(out ** 2)))
    assert rms_out > rms_in * 2.0


def test_agc_speed_params_ordering():
    # 快档 attack 系数大于慢档：紧跟信号起伏的能力排序
    from receive_pipeline import DemodWorker
    fa, fr = DemodWorker._AGC_SPEED_PARAMS["fast"]
    sa, sr = DemodWorker._AGC_SPEED_PARAMS["slow"]
    assert fa > sa and fr > sr


# ---------------------------------------------------------------------------
# ControlPanel
# ---------------------------------------------------------------------------
def test_panel_agc_speed_combo(qapp):
    from control_panel import ControlPanel
    p = ControlPanel()
    idx = p.agc_speed_combo.findData("medium")
    assert idx >= 0 and p.agc_speed_combo.currentIndex() == idx
    got = []
    p.agc_speed_changed.connect(got.append)
    slow_idx = p.agc_speed_combo.findData("slow")
    p.agc_speed_combo.setCurrentIndex(slow_idx)
    assert got == ["slow"]


def test_panel_set_agc_speed_no_emit_and_fallback(qapp):
    from control_panel import ControlPanel
    p = ControlPanel()
    got = []
    p.agc_speed_changed.connect(got.append)
    p.set_agc_speed("OFF")
    assert p.agc_speed_combo.currentData() == "off"
    assert got == []
    p.set_agc_speed("nonsense")          # 非法档 → 回退 medium，不崩
    assert p.agc_speed_combo.currentData() == "medium"


def test_panel_agc_speed_disabled_when_disconnected(qapp):
    from control_panel import ControlPanel
    p = ControlPanel()
    p.set_sdr_connected(False)
    assert not p.agc_speed_combo.isEnabled()
    p.set_sdr_connected(True)
    assert p.agc_speed_combo.isEnabled()


# ---------------------------------------------------------------------------
# MainWindow 接线（槽 → 共享配置 + 持久化；恢复 → 共享配置）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def mw(isolated_profile, qapp):
    # isolated_profile 见 tests/conftest.py：本夹具写 agc_speed/app_mode，
    # 不隔离就会写进用户真实配置。
    from main_window import MainWindow
    w = MainWindow()
    w.settings.set("app_mode", "ide")
    w.set_app_mode("ide")
    yield w
    try:
        w._disconnect()
    except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
        log.warning("test_agc_speed.py fixture 收尾失败（MainWindow._disconnect）：%s: %s",
                    type(exc).__name__, exc)
    # 还原用户默认档，不污染真实配置
    w.settings.set("agc_speed", "medium")


def test_main_window_slot_writes_shared_config(mw):
    got = []
    mw._demod_cfg = type("C", (), {"agc_speed": "medium"})()
    mw._on_agc_speed_changed("slow")
    assert mw._demod_cfg.agc_speed == "slow"
    assert mw.settings.get("agc_speed") == "slow"
    got.append(1)
    mw._on_agc_speed_changed("JUNK")     # 非法档归一 medium
    assert mw._demod_cfg.agc_speed == "medium"
    assert got == [1]


def test_main_window_restore_applies_to_shared_config(mw):
    mw.settings.set("agc_speed", "fast")
    mw._demod_cfg = type("C", (), {"agc_speed": "medium"})()
    mw._apply_restored_settings()
    # 面板恢复（blockSignals）+ 共享配置都被灌入持久值
    assert mw.control_panel.agc_speed_combo.currentData() == "fast"
    assert mw._demod_cfg.agc_speed == "fast"
    mw.settings.set("agc_speed", "medium")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
