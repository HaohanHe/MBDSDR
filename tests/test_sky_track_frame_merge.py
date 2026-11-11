# SPDX-License-Identifier: MIT
"""第 3 条反证：天空刷新与卫星跟踪合并成一个 500 ms 帧。

合并前是两个窗口级 QTimer：
  - ``_sky_update_timer`` 5000 ms → 天空图/授时刷新；
  - ``_sat_track_timer``  500 ms  → 卫星闭环调谐。
两者各起各的拍，天空在未跟踪时也在跑，跟踪不能被拖到 5 s。

合并后只剩 ``_frame_timer`` 500 ms；两个视图的原有频率用**同一帧内的
不同节流**表达：
  - 跟踪：每帧（500 ms）；
  - 天空：每 ``_SKY_FRAME_EVERY`` 帧（10 → 5000 ms）。

本文件的红线：
  1. 只允许一个帧定时器，且各频率仍是原来的语义（不是并成一个频率）；
  2. 未跟踪时天空刷新不得停摆（不许「天空图更新逻辑被砍掉」）；
  3. 反证：去掉天空节流只改天空频率，跟踪仍逐帧——两者独立可调。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def window(isolated_profile, qtapp):
    import main_window as mw
    w = mw.MainWindow()
    yield w
    try:
        w._stop_satellite_tracking()
    except Exception:  # noqa: BLE001 - 收尾失败不影响断言结论
        pass


def _count_ticks(w, monkeypatch, frames, *, tracking):
    """跑 frames 拍，统计两个视图各被调用多少次（不碰真实硬件/网络）。"""
    sky, track = [], []
    monkeypatch.setattr(w, "_update_sky_satellites", lambda: sky.append(1))
    monkeypatch.setattr(w, "_poll_satellite_track", lambda: track.append(1))
    monkeypatch.setattr(w, "_sat_tracking", tracking, raising=False)
    monkeypatch.setattr(w, "_frame_seq", 0, raising=False)
    for _ in range(frames):
        w._on_frame_tick()
    return len(sky), len(track)


def test_one_frame_timer_keeps_both_cadences(window):
    """只有一个帧定时器，且 500 ms × 10 仍是原来天空的 5 s 语义。"""
    w = window
    assert not hasattr(w, "_sky_update_timer"), "旧的天空定时器必须删掉"
    assert not hasattr(w, "_sat_track_timer"), "旧的跟踪定时器必须删掉"
    assert w._frame_timer.isActive(), "帧定时器必须常驻（天空在未跟踪时也要刷新）"
    assert w._frame_timer.interval() == 500, "帧间隔必须保持跟踪的 500 ms 口径"
    assert w._FRAME_INTERVAL_MS * w._SKY_FRAME_EVERY == 5000, \
        "天空刷新必须仍等价于 5 s，不能被合并成 500 ms"


def test_tracking_every_frame_sky_every_ten(window, monkeypatch):
    """30 帧 = 跟踪 30 次（每 500 ms）+ 天空 3 次（每 5 s）。"""
    sky, track = _count_ticks(window, monkeypatch, 30, tracking=True)
    assert track == 30, "跟踪必须每帧跑（500 ms），不能被天空节流拖慢"
    assert sky == 3, "天空必须每 10 帧刷一次（5 s）"


def test_sky_not_starved_when_not_tracking(window, monkeypatch):
    """未跟踪时天空仍按 5 s 刷新：没有把天空逻辑并进跟踪分支砍掉。"""
    sky, track = _count_ticks(window, monkeypatch, 30, tracking=False)
    assert track == 0, "未跟踪时不得空转闭环调谐"
    assert sky == 3, "未跟踪时天空刷新停摆 = 把天空逻辑砍掉了"


def test_throttles_are_independent(window, monkeypatch):
    """反证：去掉天空节流（倍数改 1）只影响天空，跟踪仍逐帧 500 ms。

    若两者被并成一个频率（例如把帧间隔改成 5 s 去「补」天空），
    这里的 track 会掉到 3，断言必红。
    """
    monkeypatch.setattr(window, "_SKY_FRAME_EVERY", 1)
    sky, track = _count_ticks(window, monkeypatch, 30, tracking=True)
    assert sky == 30, "去掉节流后天空应逐帧刷新（证明节流是独立的计数器）"
    assert track == 30, "跟踪频率不得被天空节流改动"
