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


class _TrickleStream:
    """每次 read 都返回一小块 IQ 的假流（RecordTap 的输入侧）。"""

    def __init__(self):
        self.flushes = 0

    def read(self):
        import time as _t
        _t.sleep(0.002)
        import numpy as _np
        return _np.ones(16, dtype=_np.complex64), 16

    def flush(self):
        self.flushes += 1

    def stop_reader(self):
        pass


def test_record_tap_skips_copy_when_disabled(qtapp):
    """未录音时 RecordTap 只排水、不复制、不回调（idle 态不该白烧 CPU）。

    旧实现无论录不录都 ``np.array(buf[:n])`` + 回调：2.4 MS/s 下每秒
    ~18 MB memcpy。反证口径：enabled=False 期间回调次数必须为 0，
    打开后必须立刻恢复回调。
    """
    import time as _t
    from receive_pipeline import RecordTap

    got = []
    flag = {"on": False}
    stream = _TrickleStream()
    tap = RecordTap(stream, lambda iq: got.append(iq), enabled=lambda: flag["on"])
    tap.start()
    try:
        _t.sleep(0.12)
        assert not got, "录制关闭时不许产生任何回调"
        assert stream.flushes > 0, "关闭时仍要排水，不能把乒乓流堵死"
        flag["on"] = True
        deadline = _t.time() + 2.0
        while not got and _t.time() < deadline:
            _t.sleep(0.01)
        assert got, "打开录制后必须恢复回调"
    finally:
        tap.shutdown()
        tap.wait(2000)


@pytest.fixture(scope="module")
def win(qtapp, isolated_profile):
    """module 共用 MainWindow：每例新建会累积挂起（本项目已知坑）。

    直接构造 MainWindow 会写用户真实 %APPDATA% 配置，所以隔离 HOME/
    USERPROFILE/APPDATA 到本模块临时目录（同 conftest.isolated_profile）。
    """
    import main_window as MW
    w = MW.MainWindow()
    yield w
    try:
        w._teardown_connection()
    except Exception:
        pass
    w.close()
    qtapp.processEvents()
    w.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


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
    assert player is not None, (
        "_audio_player is None 不是「没声卡」：没声卡时 AudioPlayer 仍然存在，"
        "只是 available=False；None 只出现在 AudioPlayer 导入失败或构造抛异常，"
        "那是本仓组件坏了（docs/evidence-standard.md §2 附则：本仓该有的依赖"
        "导不进来必须红），不在这里 skip。"
    )
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
    assert player is not None, (
        "_audio_player is None 不是「没声卡」：没声卡时 AudioPlayer 仍然存在，"
        "只是 available=False；None 只出现在 AudioPlayer 导入失败或构造抛异常，"
        "那是本仓组件坏了（docs/evidence-standard.md §2 附则：本仓该有的依赖"
        "导不进来必须红），不在这里 skip。"
    )
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
    assert player is not None, (
        "_audio_player is None 不是「没声卡」：没声卡时 AudioPlayer 仍然存在，"
        "只是 available=False；None 只出现在 AudioPlayer 导入失败或构造抛异常，"
        "那是本仓组件坏了（docs/evidence-standard.md §2 附则：本仓该有的依赖"
        "导不进来必须红），不在这里 skip。"
    )
    pushed = []
    monkeypatch.setattr(player, "start", lambda: True)
    monkeypatch.setattr(player, "set_muted", lambda m: None)
    monkeypatch.setattr(win, "_active_sdr_backend", object(), raising=False)
    monkeypatch.setattr(win, "_sync_listen_workers",
                        lambda on, muted=False: pushed.append((bool(on), bool(muted))))
    win._apply_listen_state("muted")
    assert win._listening is False
    assert pushed[-1] == (True, True), "静音态必须继续解调，否则解码/星座图没数据"


def test_switch_vfo_while_stopped_keeps_demod_off(win, monkeypatch):
    """停止解调时切 VFO 不得把解调偷偷拉起来（真机反馈的根因）。

    _apply_primary_vfo 只决定"哪一路写声卡"，是否解调由 _listen_state 唯一决定。
    """

    class _W:
        def __init__(self):
            self.audio = None
            self.muted = None

        def set_audio_enabled(self, v):
            self.audio = v

        def set_muted(self, v):
            self.muted = v

    a, b = _W(), _W()
    monkeypatch.setattr(win, "_vfo_workers", {"vfo-a": a, "vfo-b": b}, False)
    monkeypatch.setattr(win, "_vfo_mgr", None, False)
    monkeypatch.setattr(win, "_listen_state", "idle", False)

    win._apply_primary_vfo("vfo-b")

    assert a.audio is False and b.audio is False, "停止解调时任何一路都不该使能"
    assert a.muted is False and b.muted is False

    # 收听态下切 VFO：只有主听那一路使能
    monkeypatch.setattr(win, "_listen_state", "listen", False)
    win._apply_primary_vfo("vfo-b")
    assert a.audio is False and b.audio is True


def test_listen_flags_single_source(win):
    """三档 → (跑解调, 静音) 的单一判定点，流水线重建也走它。

    旧缺陷：重建时只看 _listening，静音态会被误停成「停止解调」。
    """
    win._listen_state = "listen"
    assert win._listen_flags() == (True, False)
    win._listen_state = "muted"
    assert win._listen_flags() == (True, True)
    win._listen_state = "idle"
    assert win._listen_flags() == (False, False)


def test_idle_skips_ddc_and_demod_inside_worker():
    """停止解调必须在 worker 里就停：DDC（VFO.process）一次都不许跑。

    只把声卡静音不算数——那是「不收听」。用户真机反馈的"点了停止解调，
    频谱/解调还在跑"，机械判据就是 VFO.process 的调用次数必须为 0。
    """
    assert _run_worker_vfo_calls(audio_enabled=False) == 0
    assert _run_worker_vfo_calls(audio_enabled=True) > 0, \
        "对照组必须有解调，否则本用例证明不了门控存在"


def _run_worker_vfo_calls(audio_enabled: bool) -> int:
    """跑一次 DemodWorker：固定 3 块 IQ 后流结束，返回 VFO.process 次数。"""
    return _run_worker_counts(audio_enabled)[0]


def _run_worker_counts(audio_enabled: bool):
    """跑一次 DemodWorker，返回 (VFO.process 次数, frames_demod, frames_skipped)。"""
    import numpy as np
    from receive_pipeline import DemodWorker, SharedDemodConfig

    class _Stream:
        def __init__(self):
            self._left = 3

        def read(self):
            if self._left <= 0:
                return None, -1
            self._left -= 1
            return np.zeros(2048, dtype=np.complex64), 2048

        def flush(self):
            pass

        def stop_reader(self):
            self._left = 0

    class _Vfo:
        def __init__(self):
            self.calls = 0

        def process(self, iq):
            self.calls += 1
            return np.zeros(480, dtype=np.complex64)

        def set_offset(self, *_a, **_k):
            pass

    class _Player:
        available = True

        def write(self, _audio):
            pass

        def set_muted(self, _m):
            pass

        def start(self):
            return True

        def stop(self):
            pass

    vfo = _Vfo()
    w = DemodWorker(_Stream(), vfo, _Player(),
                    SharedDemodConfig(mode="FM", squelch_db=-50.0),
                    sr=2.4e6, audio_enabled=audio_enabled)
    w.start()
    assert w.wait(5000), "DemodWorker 没能在 5 s 内退出（流已结束）"
    return vfo.calls, w.frames_demod, w.frames_skipped


def test_idle_worker_counts_skipped_frames():
    """帧计数是可观测证据：idle 下 frames_demod 不增长、frames_skipped 增长。

    只看 VFO.process==0 能证明 DDC 没跑；计数则让界面/排障能直接读
    "解调 0 帧"，不必靠推断（2026-11-17 审计 §8 建议的落点）。
    """
    assert _run_worker_counts(False) == (0, 0, 3)
    calls, demod, skipped = _run_worker_counts(True)
    assert (calls, demod, skipped) == (3, 3, 0), \
        f"对照组计数不符：calls={calls} demod={demod} skipped={skipped}"


def test_pipeline_audio_gate_is_explicit_opt_in(qtapp):
    """非 GUI 消费者要「造出来就解调」，必须显式 audio_enabled=True。

    流水线默认不为写卡解调（idle 红线）；基准/浸泡脚本走的是完整链路，
    已在构造处显式打开。这里把契约钉在构造参数上，改回默认 True 就会红。
    """
    from receive_pipeline import ReceivePipeline, SharedDemodConfig

    class _Gen:
        num_bins = 64
        fft_size = 64

    def _make(**kw):
        return ReceivePipeline(
            read_fn=lambda n: None, sr=960000.0, gen=_Gen(),
            center_getter=lambda: 100e6, main_vfo_dsp=None, vfo2_dsp=None,
            player=None, config=SharedDemodConfig(mode="FM"),
            on_record_iq=lambda iq: None, **kw)

    assert _make()._demod1._audio_enabled is False
    assert _make(audio_enabled=True)._demod1._audio_enabled is True


def test_disconnect_resets_listening(win, monkeypatch):
    """断连必须复位收听态与按钮，否则重连会偷偷出声。"""
    player = getattr(win, "_audio_player", None)
    assert player is not None, (
        "_audio_player is None 不是「没声卡」：没声卡时 AudioPlayer 仍然存在，"
        "只是 available=False；None 只出现在 AudioPlayer 导入失败或构造抛异常，"
        "那是本仓组件坏了（docs/evidence-standard.md §2 附则：本仓该有的依赖"
        "导不进来必须红），不在这里 skip。"
    )
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


def test_pipeline_detaches_demod_streams_when_idle(qtapp):
    """停止解调必须连 splitter → VFO 的逐帧拷贝一起停（SDR++ 口径）。

    只停 worker 不够：2.4 MS/s 下 splitter 仍为 VFO 通道每帧 memcpy 128 KiB
    （≈146 帧/s）。反证口径：idle 时通道必须不在 splitter 输出里，
    恢复收听必须重新挂上（否则点了收听却永远没数据）。
    """
    from receive_pipeline import ReceivePipeline, SharedDemodConfig

    class _Gen:
        num_bins = 64
        fft_size = 64

    pipe = ReceivePipeline(
        read_fn=lambda n: None, sr=960000.0, gen=_Gen(),
        center_getter=lambda: 100e6, main_vfo_dsp=None, vfo2_dsp=None,
        player=None, config=SharedDemodConfig(mode="FM"),
        on_record_iq=lambda iq: None)
    outputs = pipe._splitter._outputs
    # start() 才做绑定；这里只钉 set_demod_enabled 的增删语义，不起线程。
    pipe._splitter.bind(pipe._spectrum_stream)
    pipe._splitter.bind(pipe._vfo1_stream)
    assert pipe._vfo1_stream in outputs, "播放态 VFO 通道应处于绑定状态"

    pipe.set_demod_enabled(False)
    assert pipe._vfo1_stream not in outputs, "idle 时仍在为解调拷贝 IQ"
    assert pipe._spectrum_stream in outputs, "频谱通道必须不受影响（SDR++：频谱照跑）"

    pipe.set_demod_enabled(True)
    assert pipe._vfo1_stream in outputs, "恢复收听必须重新挂上 VFO 通道"


def test_idle_pauses_replay_and_listen_resumes(win, monkeypatch):
    """停止解调必须暂停回放：回放是第二个真解调入口（50 ms 定时器）。

    旧实现只停 worker / 声卡，回放定时器照跑 VFO.process——声卡已停所以听不见，
    但 CPU 仍在解调，正是用户说的「点了停止还在跑」。
    """
    from PySide6.QtCore import QTimer
    player = getattr(win, "_audio_player", None)
    assert player is not None
    monkeypatch.setattr(player, "start", lambda: True)
    monkeypatch.setattr(player, "stop", lambda: None)
    monkeypatch.setattr(player, "set_muted", lambda m: None)
    monkeypatch.setattr(win, "_active_sdr_backend", object(), raising=False)

    timer = QTimer(win)
    timer.setInterval(50)
    timer.start()
    monkeypatch.setattr(win, "_replay_timer", timer, raising=False)
    win._replay_paused_by_idle = False

    # splitter 解绑也要跟着收听态走（审计 §4 #5）：只停 worker 不够。
    calls = []

    class _Pipe:
        def set_demod_enabled(self, on):
            calls.append(bool(on))

    monkeypatch.setattr(win, "_pipeline", _Pipe(), raising=False)
    monkeypatch.setattr(win, "_demod1", None, raising=False)
    monkeypatch.setattr(win, "_demod2", None, raising=False)
    monkeypatch.setattr(win, "_vfo_workers", {}, raising=False)

    win._apply_listen_state("idle")
    assert not timer.isActive(), "停止解调后回放定时器必须停（不许继续解调）"
    assert win._replay_paused_by_idle is True
    assert calls == [False], f"idle 没把解调通道从 splitter 摘掉：{calls}"

    win._apply_listen_state("listen")
    assert timer.isActive(), "恢复收听必须从原位置继续回放"
    assert win._replay_paused_by_idle is False
    assert calls == [False, True], f"恢复收听没重绑解调通道：{calls}"

    timer.stop()
    monkeypatch.setattr(win, "_replay_timer", None, raising=False)
    win._apply_listen_state("idle")


def test_offline_replay_resumes_through_listen_combo(win, monkeypatch):
    """真实回放数组 + 定时器：无 SDR 也能从 idle 恢复，不启动设备。"""
    import numpy as np
    from PySide6.QtCore import QTimer

    timer = QTimer(win)
    timer.setInterval(50)
    timer.start()
    monkeypatch.setattr(win, "_replay_timer", timer)
    monkeypatch.setattr(win, "_replay_iq", np.zeros(2048, dtype=np.complex64))
    monkeypatch.setattr(win, "_active_sdr_backend", None)
    monkeypatch.setattr(win._audio_player, "start", lambda: True)
    monkeypatch.setattr(win, "_start_iq_streams",
                        lambda: pytest.fail("离线恢复不得启动设备"))
    try:
        win.listen_combo.setCurrentIndex(win.listen_combo.findData("idle"))
        win._apply_listen_state("idle")
        assert not timer.isActive()
        win.listen_combo.setCurrentIndex(win.listen_combo.findData("listen"))
        assert timer.isActive(), "无硬件的离线回放被收听前置检查误挡"
        assert win._listen_state == "listen"
        assert not win._replay_paused_by_idle
    finally:
        timer.stop()
        win._apply_listen_state("idle")


def test_idle_stops_constellation_feed(win, monkeypatch):
    """停止解调后星座图不再被喂：否则星座点继续跳，用户会认定「解调没停」。

    同时钉住 10 Hz 节流（旧注释写「节流 ~10fps」但每帧都喂）。
    """
    import numpy as np

    class _Panel:
        def __init__(self):
            self.feeds = 0
            self.visible = True

        def feed_iq(self, iq):
            self.feeds += 1

        def isVisible(self):
            return self.visible

    panel = _Panel()
    for name in ("_update_rssi_from_iq", "_refresh_vfo_panel",
                 "_update_status_bar"):
        monkeypatch.setattr(win, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(win.spectrum, "_refresh_status_labels",
                        lambda *a, **k: None, raising=False)
    monkeypatch.setattr(win, "digital_adsb_panel", None, raising=False)
    monkeypatch.setattr(win, "constellation_panel", panel, raising=False)
    monkeypatch.setattr(win, "adsb_dock", None, raising=False)
    monkeypatch.setattr(win, "_constellation_t", -1e9, raising=False)
    iq = np.ones(4096, dtype=np.complex64) * 0.1

    win._listen_state = "idle"
    win._on_pipeline_frame_ready(iq, 2.4e6)
    win._on_pipeline_frame_ready(iq, 2.4e6)
    assert panel.feeds == 0, "停止解调时星座图仍在被喂（观感上等于没停）"

    win._listen_state = "listen"
    win._on_pipeline_frame_ready(iq, 2.4e6)
    assert panel.feeds == 1, "收听态必须恢复喂入"
    win._on_pipeline_frame_ready(iq, 2.4e6)
    assert panel.feeds == 1, "相邻两帧必须被 10 Hz 节流合并"
    win._listen_state = "idle"


def test_status_shows_zero_demod_frames_when_idle(win, monkeypatch):
    """状态位给出可读的帧率：停止解调时必须是 0，积压提示优先。"""
    import time as _t

    prev_state = win._listen_state
    prev_text = win.status_processing.text()
    prev_until = getattr(win, "_buffer_warn_until", None)
    try:
        win._listen_state = "idle"
        win._show_demod_fps(0)
        assert win.status_processing.text() == "待机 · 解调 0 帧/s"

        win._listen_state = "listen"
        win._show_demod_fps(146)
        assert "146" in win.status_processing.text()

        win._listen_state = "idle"
        win._buffer_warn_until = _t.monotonic() + 5.0
        win._show_demod_fps(0)
        assert "0 帧/s" not in win.status_processing.text(), \
            "积压提示被 1 Hz 帧率刷新顶掉了"
    finally:
        win._listen_state = prev_state
        win._set_processing_status(prev_text)
        if prev_until is None:
            win.__dict__.pop("_buffer_warn_until", None)
        else:
            win._buffer_warn_until = prev_until
