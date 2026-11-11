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
def win(qtapp, isolated_profile):
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
    """启动契约（2026-11-11 变更，提交 685e8f50）：FFT 点数由 schema 单一真值源
    决定，默认性能档只负责「刷新间隔 + 送绘根数上限」。

    旧契约（性能档连带下发 FFT）已废弃：启动时按 eco 调 set_performance_mode(2)
    会把用户写盘的 4096 覆盖成 1024（实测缺口 S-01）。因此这里断言
    fft_size == SCHEMA["fft_size"].default，而不是 perf_profile 的参考点数。

    反证：若有人把 reconfigure(1024, ...) 加回 set_performance_mode，
    下面的「切档不得改写 fft_size / 下拉」断言必红。

    本例必须排在所有切档用例之前：module 共用 win，一旦被后面的用例改过档，
    startup 断言就失去意义（同 test_listen_defaults_off 的早序约定）。
    """
    import config_schema
    from perf_profile import PROFILES, DEFAULT_PROFILE
    conf = PROFILES[DEFAULT_PROFILE]
    sp = win.spectrum
    gen = sp.generator
    level = conf["panel_level"]

    # 1) FFT = schema 默认值（不是档位参考值，也不该被 eco 改写）
    assert gen.fft_size == config_schema.SCHEMA["fft_size"].default, \
        f"FFT 应由 schema 默认值决定，实际 {gen.fft_size}"

    # 2) 档位只改刷新间隔与送绘根数上限
    assert sp._plot._timer.interval() == conf["spectrum_interval_ms"]
    assert sp._perf_combo.currentIndex() == level
    assert 0 < gen.num_bins <= sp._PERF_LEVELS[level][1], \
        f"送绘根数 {gen.num_bins} 应受档位上限 {sp._PERF_LEVELS[level][1]} 约束"
    assert win._gnss_timer.interval() == conf["gnss_poll_ms"]

    # 3) 反证：切档不得改写 fft_size / FFT 下拉（FFT 单一真值源）
    fft_before = gen.fft_size
    combo_before = sp._fft_combo.currentText()
    sp.set_performance_mode(0)
    assert gen.fft_size == fft_before, \
        f"性能档偷偷改写了 FFT：{fft_before} → {gen.fft_size}"
    assert sp._fft_combo.currentText() == combo_before, \
        "性能档不得偷偷改 FFT 下拉（FFT 只由 fft_size 下发）"
    # 还原默认档，避免影响后续用例
    sp.set_performance_mode(level)


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


def test_perf_profile_changes_live_refresh_and_bins_cap(win):
    """切档必须真改活对象：plot._timer 刷新间隔 + 送绘根数上限（**不改 FFT**）。

    契约变更（提交 685e8f50，2026-11-11）：FFT 点数的单一真值源是设置项
    fft_size，性能档不再连带下发 FFT。本用例同时钉住两件事：
    ① 刷新间隔与送绘根数上限必须真跟着档位动；
    ② 切档不得改写 generator.fft_size（反证：谁把 reconfigure 加回
       set_performance_mode，下面的 fft_before 断言必红）。
    """
    from perf_profile import PROFILES
    sp = win.spectrum
    gen = sp.generator
    plot_timer = getattr(sp._plot, "_timer", None)
    assert plot_timer is not None, "频谱面板应有 live 刷新定时器"

    fft_before = gen.fft_size
    win._apply_perf_profile("quality")
    q = PROFILES["quality"]
    assert plot_timer.interval() == q["spectrum_interval_ms"] == 50
    assert gen.fft_size == fft_before, "性能档不得改写 FFT 点数"
    bins_quality = gen.num_bins

    win._apply_perf_profile("eco")
    e = PROFILES["eco"]
    cap_eco = sp._PERF_LEVELS[e["panel_level"]][1]
    assert gen.fft_size == fft_before, "性能档不得改写 FFT 点数"
    assert 0 < gen.num_bins <= cap_eco == 256, \
        f"eco=面板「低」档，送绘根数应受上限 256 约束，实际 {gen.num_bins}"
    assert gen.num_bins <= bins_quality, "降档不得反而让送绘根数变多"
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
