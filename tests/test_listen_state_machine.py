"""「插上设备 ≠ 开始播放」状态机的契约测试。

对照 SDR++ main_window.h:51 ``bool playing = false``：
source 连上后必须用户显式点 PLAY 才启动解调与声卡。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "desktop"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_OFFLINE", "1")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def win(qtapp):
    """module 共用 MainWindow：每例新建会累积挂起（本项目已知坑）。

    直接构造 MainWindow 会写用户真实 %APPDATA% 配置，所以隔离 HOME/
    USERPROFILE/APPDATA 到本模块临时目录（同 conftest.isolated_profile）。
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="mbdsdr_listen_")
    for key in ("HOME", "USERPROFILE"):
        os.environ[key] = tmp
    os.environ["APPDATA"] = os.path.join(tmp, "appdata")
    os.environ.setdefault("MBDSDR_OFFLINE", "1")
    os.makedirs(os.environ["APPDATA"], exist_ok=True)
    import main_window as MW
    w = MW.MainWindow()
    yield w
    try:
        w._teardown_connection()
    except Exception:
        pass
    w.close()


def test_listen_defaults_off(win):
    """连接/启动默认不收听：下拉停在「停止解调」，且不推解调。"""
    assert win._listening is False
    combo = getattr(win, "listen_combo", None)
    assert combo is not None
    assert combo.currentData() == "idle"
    assert "停止解调" in combo.currentText()


def test_listen_has_three_states(win, monkeypatch):
    """三档必须在 UI 上都能选到：收听 / 停止解调 / 不收听。"""
    combo = win.listen_combo
    states = [combo.itemData(i) for i in range(combo.count())]
    assert states == ["listen", "idle", "muted"], states
    labels = [combo.itemText(i) for i in range(combo.count())]
    assert "收听" in labels[0]
    assert "停止解调" in labels[1]
    assert "不收听" in labels[2]


def test_default_perf_profile_applied_at_startup(win):
    """默认省电档必须在启动时就真生效（FFT/谱线/刷新/GNSS），不是只在手动切档时才动。

    病征（2026-11-10）：GNSS 轮询在定时器创建时按档设了，频谱 FFT/刷新却停在
    面板默认高档——eco 名不副实，弱机启动即重载。修后启动即静默对齐三杠杆。
    本例必须排在所有切档用例之前：module 共用 win，一旦被后面的用例改过档，
    startup 断言就失去意义（同 test_listen_defaults_off 的早序约定）。
    """
    from perf_profile import PROFILES, DEFAULT_PROFILE
    conf = PROFILES[DEFAULT_PROFILE]
    sp = win.spectrum
    gen = sp.generator
    level = conf["panel_level"]
    assert gen.fft_size == conf["fft_size"]
    assert gen.num_bins == sp._PERF_LEVELS[level][1]
    assert sp._plot._timer.interval() == conf["spectrum_interval_ms"]
    assert sp._perf_combo.currentIndex() == level
    assert win._gnss_timer.interval() == conf["gnss_poll_ms"]


def test_start_iq_streams_does_not_auto_open_soundcard(win, monkeypatch):
    """连接成功只接频谱/录制，不得自动 start 声卡。"""
    calls = []
    player = getattr(win, "_audio_player", None)
    if player is None:
        pytest.skip("无音频设备，AudioPlayer 降级为 None")
    monkeypatch.setattr(player, "start", lambda: calls.append("start") or True)
    monkeypatch.setattr(win, "_build_pipeline", lambda: None)
    win._listening = False
    win._start_iq_streams()
    assert calls == [], "未点收听却打开了声卡（插上就播的回归）"


def test_listen_without_device_is_honest(win, monkeypatch):
    """没连设备选收听：如实提示并回到「停止解调」，不偷偷开空流。"""
    msgs = []
    monkeypatch.setattr(win, "_active_sdr_backend", None, raising=False)
    monkeypatch.setattr(win.statusBar(), "showMessage",
                        lambda m, *a, **k: msgs.append(str(m)))
    win._apply_listen_state("listen")
    assert win._listening is False
    assert win.listen_combo.currentData() == "idle"
    assert any("先点" in m and "连接" in m for m in msgs), msgs


def test_listen_toggle_starts_and_stops_player(win, monkeypatch):
    """收听开声卡并推 worker；停止解调关声卡并把解调也停掉。"""
    player = getattr(win, "_audio_player", None)
    if player is None:
        pytest.skip("无音频设备，AudioPlayer 降级为 None")
    events = []
    monkeypatch.setattr(player, "start", lambda: events.append("start") or True)
    monkeypatch.setattr(player, "stop", lambda: events.append("stop"))
    monkeypatch.setattr(player, "set_muted", lambda m: events.append(f"mute={m}"))
    monkeypatch.setattr(win, "_active_sdr_backend", object(), raising=False)
    pushed = []
    monkeypatch.setattr(win, "_sync_listen_workers",
                        lambda on, muted=False: pushed.append((bool(on), bool(muted))))

    win._apply_listen_state("listen")
    assert win._listening is True
    assert "start" in events
    assert pushed[-1] == (True, False)

    win._apply_listen_state("idle")
    assert win._listening is False
    assert "stop" in events
    assert pushed[-1] == (False, False)


def test_muted_state_keeps_demod_running(win, monkeypatch):
    """「不收听」= 静音解调：worker 继续解调，只是不写声卡。"""
    player = getattr(win, "_audio_player", None)
    if player is None:
        pytest.skip("无音频设备，AudioPlayer 降级为 None")
    pushed = []
    monkeypatch.setattr(player, "start", lambda: True)
    monkeypatch.setattr(player, "set_muted", lambda m: None)
    monkeypatch.setattr(win, "_active_sdr_backend", object(), raising=False)
    monkeypatch.setattr(win, "_sync_listen_workers",
                        lambda on, muted=False: pushed.append((bool(on), bool(muted))))
    win._apply_listen_state("muted")
    assert win._listening is False
    assert pushed[-1] == (True, True), "静音态必须继续解调，否则解码/星座图没数据"


def test_disconnect_resets_listening(win, monkeypatch):
    """断连必须复位收听态与按钮，否则重连会偷偷出声。"""
    player = getattr(win, "_audio_player", None)
    if player is None:
        pytest.skip("无音频设备，AudioPlayer 降级为 None")
    monkeypatch.setattr(player, "stop", lambda: None)
    monkeypatch.setattr(win, "_stop_tx", lambda *a, **k: None)
    monkeypatch.setattr(win, "_stop_replay", lambda: None)
    monkeypatch.setattr(win, "_refresh_tx_readout", lambda: None)
    monkeypatch.setattr(win, "_refresh_tx_button_state", lambda: None)
    monkeypatch.setattr(win, "_panels_set_sdr_connected", lambda *a: None)
    win._listening = True
    win._set_listen_combo("listen")
    win._teardown_connection()
    assert win._listening is False
    assert win.listen_combo.currentData() == "idle"


def test_perf_profile_is_exposed_in_settings():
    """性能档必须在设置页可见、有默认值、且默认值是省电档。"""
    from config_schema import EXPOSED_ITEMS, SCHEMA
    item = SCHEMA["perf_profile"]
    assert item in EXPOSED_ITEMS
    assert item.default == "eco"
    assert {"eco", "balanced", "quality"} == {c[0] if isinstance(c, tuple)
                                              else c.value for c in item.choices}


def test_perf_profile_changes_gnss_poll_interval(win, monkeypatch):
    """切档即时改 GNSS 轮询，不是设了没反应的假控件。"""
    from perf_profile import PROFILES
    win.settings.set("perf_profile", "eco")
    win._apply_perf_profile("eco")
    assert win._gnss_timer.interval() == PROFILES["eco"]["gnss_poll_ms"]
    win._apply_perf_profile("quality")
    assert win._gnss_timer.interval() == PROFILES["quality"]["gnss_poll_ms"]
    win._apply_perf_profile("eco")


def test_perf_profile_changes_live_spectrum_fft_and_refresh(win):
    """切档必须真改活对象：generator.fft_size/num_bins + plot._timer 刷新间隔。

    反证（2026-11-10 output/perf_lever_probe.py 实证）：旧 _apply_perf_profile
    调 panel.set_fft_size（面板根本没有这方法，getattr 落空）+ 改从不 start 的
    _iq_poll_timer，活对象全不变 = 假控件，状态栏却宣称「FFT/频谱刷新已生效」。
    修后走既有 set_performance_mode 接缝：eco 真把 FFT 2048→1024、谱线 512→256、
    刷新 50→100ms（4× 少 FFT 工作量 + 半刷新率），这才是「原地不动」的性能解药。
    把任一杠杆改回死路径，本例即红。
    """
    from perf_profile import PROFILES
    sp = win.spectrum
    gen = sp.generator
    plot_timer = getattr(sp._plot, "_timer", None)
    assert plot_timer is not None, "频谱面板应有 live 刷新定时器"

    win._apply_perf_profile("quality")
    q = PROFILES["quality"]
    assert gen.fft_size == q["fft_size"]
    assert plot_timer.interval() == q["spectrum_interval_ms"]

    win._apply_perf_profile("eco")
    e = PROFILES["eco"]
    assert gen.fft_size == e["fft_size"] == 1024
    assert gen.num_bins == 256, "eco=面板「低」档，谱线数应降到 256"
    assert plot_timer.interval() == e["spectrum_interval_ms"] == 100
    # 面板「性能」下拉同步到 eco（index 2），不与设置页打架
    assert sp._perf_combo.currentIndex() == e["panel_level"] == 2
    # 死定时器不再被假装驱动（它从不 start，改它等于没改）
    assert win._iq_poll_timer.isActive() is False
    win._apply_perf_profile("eco")


def test_perf_profile_mirrors_spectrum_panel_levels():
    """perf_profile 的 fft_size/spectrum_interval_ms 必须 == 面板 _PERF_LEVELS[panel_level]。

    运行时事实源是频谱面板 set_performance_mode（它真改 generator + plot 定时器）；
    perf_profile 里这两个字段是展示镜像。两处漂移会让设置页宣称的 FFT/刷新与
    实际生效不符（假控件复活）。本例把镜像钉死在面板档位上。
    """
    import perf_profile as pp
    from spectrum_widget import SpectrumPanel
    levels = SpectrumPanel._PERF_LEVELS
    for key, conf in pp.PROFILES.items():
        lvl = conf["panel_level"]
        assert 0 <= lvl < len(levels), f"{key} panel_level 越界"
        fft_size, _num_bins, refresh_ms = levels[lvl]
        assert conf["fft_size"] == fft_size, f"{key} FFT 镜像与面板档漂移"
        assert conf["spectrum_interval_ms"] == refresh_ms, \
            f"{key} 刷新镜像与面板档漂移"


def test_live_repaint_has_no_30fps_override(win, monkeypatch):
    """live 接收的重绘只能由面板 _plot._timer（按性能档）驱动，不许旁路盖过。

    反证（2026-11-10 output/repaint_lever_probe.py 实证）：旧 _on_pipeline_frame_ready
    每帧调 _ensure_repaint_timer 起一个硬编码 33ms(30fps) 的 _repaint_timer，与面板
    _plot._timer 并存且更快，两者都 connect _plot.update() → 有效重绘恒为 30fps。
    eco 把面板定时器调到 100ms(10fps) 却被旁路盖过，省电档刷新让路在 live 接收
    （用户真机场景）完全失效。移除旁路后：live 帧不再创建 _repaint_timer，面板
    定时器是唯一驱动，eco 真 10fps、quality 真 20fps。把 30fps 旁路加回来本例即红。
    """
    import numpy as np
    from perf_profile import PROFILES
    sp = win.spectrum
    plot_timer = sp._plot._timer
    # 桩掉帧入口里与本断言无关的重活/分发，只留重绘驱动语义
    for name in ("_update_rssi_from_iq", "_refresh_vfo_panel",
                 "_update_status_bar", "_tap_new_spacetime_iq"):
        monkeypatch.setattr(win, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sp, "_refresh_status_labels",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(win, "digital_adsb_panel", None, raising=False)
    monkeypatch.setattr(win, "constellation_panel", None, raising=False)
    win._read_fail_signals = 0
    win._rssi_last_t = 0.0
    win._backend_status_tick = 0
    win._vfo_refresh_tick = 0
    win._nst_tick = 0
    iq = np.ones(4096, dtype=np.complex64) * 0.1

    win._apply_perf_profile("eco")
    win._on_pipeline_frame_ready(iq, 2.4e6)          # 模拟 live 首帧
    assert getattr(win, "_repaint_timer", None) is None, \
        "live 帧不该再创建硬编码 30fps 旁路重绘定时器（会盖过性能档刷新）"
    assert plot_timer.isActive()
    assert plot_timer.interval() == PROFILES["eco"]["spectrum_interval_ms"] == 100

    win._apply_perf_profile("quality")
    win._on_pipeline_frame_ready(iq, 2.4e6)
    assert getattr(win, "_repaint_timer", None) is None
    assert plot_timer.interval() == PROFILES["quality"]["spectrum_interval_ms"] == 50
    win._apply_perf_profile("eco")
