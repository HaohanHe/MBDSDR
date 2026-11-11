# SPDX-License-Identifier: MIT
"""主窗口热路径的诚实性（tests/test_ui_hotpath_honesty.py）
==========================================================

四条「看着能用其实是假的」缺陷，全部落在 UI 线程/显示与真实状态脱钩：

1. 调谐失败回滚只改标签：频谱拖动是「先动视窗、再下发硬件」，失败后
   ``_revert_tune_ui`` 不还原频谱中心与 ``_vfo_center_hz``，用户看到刻度
   回退、瀑布仍在失败频率。
2. 云台死区记账入参形态错：传 ``ctx`` 而非 ``{"ctx": ctx}``，被调方读到空
   dict → az/el 恒 0°；拍差还写死 0.5 s；外圈裸 ``except: pass``。
3. NTP 授时同步跑在 UI 线程且 ``except: pass``：网络慢就僵界面，失败还不可见。
4. 每帧无条件 ``_redraw()``：性能档的刷新让路被逐帧重绘冲掉。
   改后：每帧只对齐滑杆（10 Hz 节流），重绘唯一驱动 = 面板定时器，
   首帧靠 pending 标记补画。

反证：任一处改回旧行为，对应用例必红（见各用例 docstring）。
"""
import math
import os
import sys
import threading
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
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
    """调谐显式返回 False（真实驱动「频率不在量程」的失败形态）。"""

    def __init__(self):
        self.status = SimpleNamespace(error="设备拒绝该频率")

    def get_status(self):
        return self.status

    def set_frequency(self, hz):
        return False


# ============================================================================
# 1. 调谐失败：频谱中心必须一起回滚
# ============================================================================

def test_failed_tune_restores_spectrum_center(mw):
    """失败后频谱中心 == 最后一次真实成功值（变异：删 set_center_freq 必红）。"""
    mw._backend_center_hz = 100e6
    mw._active_sdr_backend = _RefusingTuneBackend()
    # 频谱拖动/双击的时序：视窗先到目标频率，随后才下发硬件
    mw.spectrum.set_center_freq(145.0)

    mw._on_spectrum_freq_changed(145.0)

    assert mw.spectrum.generator.center_freq_hz == pytest.approx(100e6), \
        "失败后瀑布仍停在失败频率上"
    assert mw._vfo_center_hz == pytest.approx(100e6), \
        "VFO 收听频率必须一起回滚（否则 DDC 在错误中心上重算 offset）"
    assert "145" not in mw.freq_label.text()


# ============================================================================
# 2. 云台记账：入参形态 + 真实拍差 + 异常可见
# ============================================================================

class _StubCompensator:
    """doppler_link 补偿器的最小替身：不补偿、不碰硬件。"""

    state = SimpleNamespace(target_name="", valid=False, doppler_hz=None,
                            reason="")

    def refresh(self, pos):
        pass

    def rx_freq(self):
        return None


class _DeadbandLimiter:
    """整形器：plan 返回 None（死区/限速抑制），last_command = 当前生效角。"""

    last_command = (120.0, 44.0)
    deadband_deg = 1.0
    min_interval_s = 0.5
    max_rate_deg_s = 8.0

    def plan(self, az, el):
        return None

    def reset(self):
        pass


class _CaptureRecorder:
    """误差记录器替身：只留主窗口用到的 record/stats 两个面。"""

    def __init__(self):
        self.calls = []

    def record(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"cmd_error_deg": 0.0, "pose_error_deg": None}

    def stats(self):
        return {"samples": len(self.calls), "pose_samples": 0,
                "pose_max_deg": None, "pose_mean_deg": None,
                "pose_source_summary": {"mechanical_accuracy_claimable": False,
                                        "note": "测试替身无真实回读"}}


def _arm_gimbal_tick(mw, monkeypatch, *, activate=None):
    """把 ``_poll_satellite_track`` 的依赖收窄到「只走云台记账这一支」。"""
    import mbdsdr_ai.doppler_link as dl

    monkeypatch.setattr(dl, "get_compensator", lambda: _StubCompensator())
    mw._loop_sat_tracker = SimpleNamespace(
        current_position=lambda: {"valid": True, "azimuth": 123.0,
                                  "elevation": 45.0, "doppler_hz": 0.0,
                                  "corrected_freq_hz": 145_800_000.0,
                                  "range_km": 1000.0,
                                  # 真实 current_position() 必带 policy；漏键会被闸门拒发调谐
                                  "policy": {"allow_tune": True,
                                             "allow_gimbal": True,
                                             "label": "星历新鲜",
                                             "reason": "TLE 年龄 0.3 天",
                                             "next_action": "可直接跟踪"}},
        downlink_freq_hz=lambda: 145_800_000.0,
        selected_name="ISS")
    mw._gimbal_follow = True
    monkeypatch.setattr(mw, "_pointing_station_trust", lambda: {"trusted": True})
    monkeypatch.setattr(mw, "_gimbal_available", lambda: True)
    monkeypatch.setattr(mw, "_collect_gimbal_task", lambda *a, **k: False)
    monkeypatch.setattr(mw, "_gimbal_task_running", lambda: False)
    monkeypatch.setattr(mw, "_activate_gimbal_if_needed",
                        activate or (lambda: object()))
    monkeypatch.setattr(mw, "_on_tune_sdr", lambda *a, **k: True)
    mw._gimbal_limiter = _DeadbandLimiter()
    rec = _CaptureRecorder()
    mw._gimbal_error = rec
    return rec


def test_gimbal_deadband_accounting_keeps_real_angles(mw, monkeypatch):
    """死区抑制这一拍也必须按真实 az/el 记账，拍差用真实时间。

    变异：把 ``{"ctx": ctx}`` 改回 ``ctx`` → satellite_az/command_az 读到 0
    （甚至整拍 return），本用例必红；把 elapsed_s 改回 0.5 → 断言必红。
    """
    rec = _arm_gimbal_tick(mw, monkeypatch)

    mw._poll_satellite_track()

    cycle = mw.last_gimbal_cycle()
    assert cycle is not None, "死区抑制这一拍也必须留下周期记录"
    assert cycle["satellite_az"] == pytest.approx(123.0)
    assert cycle["satellite_el"] == pytest.approx(45.0)
    assert cycle["command_az"] == pytest.approx(120.0)
    assert cycle["command_el"] == pytest.approx(44.0)
    assert rec.calls, "误差统计必须收到这一拍"
    elapsed_s = rec.calls[-1][1]["elapsed_s"]
    assert 0.0 <= elapsed_s < 0.5, f"拍差必须是真实值，实际 {elapsed_s}"


def test_gimbal_tick_exception_is_visible(mw, monkeypatch):
    """云台循环里未预期异常不得静默：必须停机 + 状态栏带异常类型。"""

    def _boom():
        raise RuntimeError("整形器状态损坏")

    _arm_gimbal_tick(mw, monkeypatch, activate=_boom)

    mw._poll_satellite_track()

    msg = mw.statusBar().currentMessage()
    assert "云台跟随异常" in msg, f"异常被吞掉了：{msg!r}"
    assert "RuntimeError" in msg and "整形器状态损坏" in msg
    assert mw._gimbal_follow is False, "异常后必须真的停止跟随，不能只报错"


# ============================================================================
# 3. NTP 授时：后台线程 + 失败可见 + 不叠线程
# ============================================================================

def test_ntp_does_not_block_ui_and_reports_failure(mw, monkeypatch, qtapp):
    """NTP 失败：查询不在 GUI 线程、不阻塞界面、原因写进状态栏。

    变异：改回同步调用 → 调用耗时含 wait 时长（本用例直接红）；
    改回 ``except: pass`` → 状态栏断言红。
    """
    from mbdsdr_ai import new_spacetime

    seen = {}
    started = threading.Event()
    release = threading.Event()

    def _slow_fail(prefer_ntp=True):
        seen["thread"] = threading.current_thread()
        seen["prefer_ntp"] = prefer_ntp
        started.set()
        release.wait(2.0)
        raise OSError("NTP 服务器不可达")

    monkeypatch.setattr(new_spacetime, "get_time_info", _slow_fail)
    mw._ntp_sync_counter = 0          # 下一拍必定走 NTP
    mw._time_info_last_error = ""
    msgs = []
    monkeypatch.setattr(mw.statusBar(), "showMessage",
                        lambda text, *_a, **_k: msgs.append(text))

    t0 = time.monotonic()
    mw._update_time_info()
    assert time.monotonic() - t0 < 0.2, "授时查询不得卡在 UI 线程"
    assert started.wait(2.0), "授时查询必须真的跑起来"
    th1 = mw._time_info_thread
    mw._update_time_info()            # 在途未回：不得叠线程
    assert mw._time_info_thread is th1, "同一时刻只允许一条授时线程"

    release.set()
    th1.join(2.0)
    qtapp.processEvents()

    assert seen["thread"] is not threading.main_thread(), \
        "get_time_info 不得在 GUI 线程执行"
    failure = [m for m in msgs if "授时信息获取失败" in m]
    assert failure, f"失败被吞掉了（状态栏消息：{msgs!r}）"
    assert "OSError" in failure[-1] and "NTP 服务器不可达" in failure[-1]


def test_ntp_success_backfills_sky_view(mw, monkeypatch, qtapp):
    """成功路径经信号回填天空图（不在后台线程直接碰控件）。"""
    from mbdsdr_ai import new_spacetime

    got = {}
    monkeypatch.setattr(mw, "sky_view",
                        SimpleNamespace(set_time_info=lambda d: got.update(d)),
                        raising=False)
    monkeypatch.setattr(
        new_spacetime, "get_time_info",
        lambda prefer_ntp=True: SimpleNamespace(
            utc_time=None, gps_time=0.0, ntp_server="ntp.test",
            source="system", clock_offset_ms=0.0))

    mw._update_time_info()
    mw._time_info_thread.join(2.0)
    qtapp.processEvents()

    assert got.get("ntp_server") == "ntp.test"
    assert got.get("ntp_status") == "system"


# ============================================================================
# 4. 逐帧重绘：50 ms 内灌 200 帧，只允许首帧那次 update
# ============================================================================

def _count_updates(monkeypatch, mw):
    counts = {"fft": 0, "wf": 0}

    def _bump(key):
        def _update(*_a, **_k):
            counts[key] += 1
        return _update

    monkeypatch.setattr(mw.spectrum._fft_plot, "update", _bump("fft"))
    monkeypatch.setattr(mw.spectrum._wf_plot, "update", _bump("wf"))
    return counts


def test_per_frame_hook_repaints_only_first_frame(mw, monkeypatch):
    """200 帧内 update 次数 ≤ 性能档允许值且 ≥1。

    变异：``_refresh_status_labels`` 里恢复无条件 ``_redraw()`` → 计数 = 帧数，
    必红；删掉首帧 pending 标记 → 计数 = 0，必红。
    """
    panel = mw.spectrum
    monkeypatch.setattr(panel.generator, "has_data", lambda: True)
    counts = _count_updates(monkeypatch, mw)
    panel._label_has_data = False

    frames = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < 0.05:
        panel._refresh_status_labels()
        frames += 1

    assert frames >= 200, f"本机 50ms 内只灌了 {frames} 帧，样本不足"
    # 性能档上限 20 fps → 50 ms 最多 1 帧；首帧必须画出来。
    assert 1 <= counts["fft"] <= 2, f"FFT 重绘 {counts['fft']} 次（应为首帧 1 次）"
    assert 1 <= counts["wf"] <= 2, f"瀑布重绘 {counts['wf']} 次（应为首帧 1 次）"


def test_frame_ready_hook_is_throttled(mw, monkeypatch):
    """逐帧数据通路的频谱挂钩节流到 10 Hz（变异：去掉节流 → 200 次调用必红）。"""
    calls = []
    monkeypatch.setattr(mw.spectrum, "_refresh_status_labels",
                        lambda: calls.append(1))
    mw._spectrum_hook_t = -math.inf

    for _ in range(200):
        mw._on_pipeline_frame_ready(np.zeros(8, dtype=np.complex64), 2.4e6)

    assert 1 <= len(calls) <= 2, f"200 帧内调用了 {len(calls)} 次（应按 10 Hz 节流）"


def test_frame_ready_hook_is_wired(mw, monkeypatch):
    """``_on_pipeline_frame_ready`` 仍要接上状态挂钩（否则首屏/滑杆失联）。"""
    calls = []
    monkeypatch.setattr(mw.spectrum, "_refresh_status_labels",
                        lambda: calls.append(1))
    mw._spectrum_hook_t = -math.inf

    mw._on_pipeline_frame_ready(np.zeros(8, dtype=np.complex64), 2.4e6)

    assert calls, "逐帧数据通路必须调用频谱状态挂钩"
