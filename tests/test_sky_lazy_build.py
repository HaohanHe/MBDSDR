# SPDX-License-Identifier: MIT
"""星象仪惰性构建：首帧不建天空视图，首次切页**同步**补建且不漏初始化。

背景：`_create_sky_widget()` 首建实测 394.4 ms，绝大部分是首次字体度量。
星象仪不是默认页，构造期不该付这笔钱。

反证口径（把 `__init__` 改回直接建天空视图 → 第 1 条必红）：

1. 构造后未切页：`sky_view is None`、没有站址 tracker（其自身定时器也不存在），
   天空周期任务直接放过（不为不存在的视图起授时线程）；
2. 把左页签切到星象仪页 → **同一次调用内**视图已就位（不是下一帧再补），
   页签里放的就是 `sky_view` 本尊，占位控件不会留下来（不出现空画布再补画）；
3. 补建后既有初始化全部执行：目录绑定、站址注入、过境列表作废、全天开关、
   信号接线（点星问模型/示例天空/地点/夜视/调谐/跟踪/云台），
   `set_pass_provider` 仍是构造期就注入的那一份。
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


@pytest.fixture()
def mw(qtapp, isolated_profile, monkeypatch):
    import main_window as mwmod
    # 本用例会临时设站址验证"启动期站址先落地、天空后建"。`gui_config.json`
    # 不归 isolated_profile 管（路径在导入期就定了），任何 `_save_gui_config`
    # 都会打到用户真实文件——这里整段禁掉写盘，只验内存态。
    monkeypatch.setattr(mwmod.MainWindow, "_save_gui_config",
                        lambda self: None)
    # 会话级 desktop_settings.json（conftest 的 _isolate_backing_config 把它
    # 指到同一条临时路径）会被别的模块写成 app_mode=planetarium（例如
    # test_first_open_welcome 按了「进入星象仪」）。本文件测的就是**默认 IDE
    # 形态**下的惰性构建，先构造窗口再断言已被 _restore_app_mode 建出天空视图，
    # 测的就不是惰性了。这里只在 main_window 的引用上把回读值强制回 ide
    # （内存态，不落盘），不改变生产代码行为。
    class _IdeDefaultSettings(mwmod.DesktopSettings):
        @classmethod
        def load(cls, *a, **k):
            s = super().load(*a, **k)
            s._data["app_mode"] = "ide"
            return s

    monkeypatch.setattr(mwmod, "DesktopSettings", _IdeDefaultSettings)
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


def test_not_switched_keeps_sky_unbuilt(mw):
    """不切页：天空视图不存在，也就没有它的定时器/授时线程。"""
    assert mw.sky_view is None, "构造期就建了天空视图（394 ms 又回到首帧）"
    assert mw._sky_placeholder is not None, "占位页签丢了，切页补建无处落位"
    # 天空视图没有 → 站址 tracker 不会建，它自带的刷新定时器也不存在。
    assert mw.sat_tracker is None
    # 天空周期任务必须放过：不能为不存在的视图起 NTP 线程。
    mw._update_sky_satellites()
    assert mw.sky_view is None
    # 默认工作面是频谱而不是星象仪（否则首帧仍会触发补建）。
    assert mw.left_tab.currentWidget() is not mw._sky_placeholder


def test_switch_builds_synchronously_and_replays_init(mw, monkeypatch):
    """切页 = 首次需要：同步建好，并重放全部启动期初始化。"""
    calls = {"bind": 0, "invalidate": 0, "question": 0, "track": 0,
             "night": 0}
    orig_bind = mw._bind_sky_catalog

    def _bind():
        calls["bind"] += 1
        return orig_bind()

    orig_invalidate = mw.sat_track_panel.invalidate_passes

    def _invalidate(*a, **k):
        calls["invalidate"] += 1
        return orig_invalidate(*a, **k)

    monkeypatch.setattr(mw, "_bind_sky_catalog", _bind)
    monkeypatch.setattr(mw.sat_track_panel, "invalidate_passes", _invalidate)
    # 信号接线的探针必须在补建前就位（补建时 connect 的是当时的取值）。
    monkeypatch.setattr(mw, "_on_sky_question",
                        lambda q: calls.__setitem__("question",
                                                    calls["question"] + 1))
    monkeypatch.setattr(mw, "_on_sky_track_requested",
                        lambda on, name, hz: calls.__setitem__(
                            "track", calls["track"] + 1))
    monkeypatch.setattr(mw, "_apply_night_vision_to_overlays",
                        lambda on: calls.__setitem__(
                            "night", calls["night"] + 1))

    # 站址在天空视图之前就要先落地到其它面板（启动期路径）。
    # 分支面板的 set_observer 会写 gui_config，这里只看天空侧初始化，
    # 不触发那条持久化路径（真实站址不允许被测试改写）。
    monkeypatch.setattr(mw, "_push_site_to_panels", lambda *a, **k: None)
    mw._observer_lat, mw._observer_lon = 39.9, 116.4
    mw._apply_observer_location()
    assert mw.sky_view is None, "站址注入不得顺手把天空视图建出来"

    lt = mw.left_tab
    idx = lt.indexOf(mw._sky_placeholder)
    assert idx >= 0
    lt.setCurrentIndex(idx)
    # 同一次调用内已完成补建：不是排队到下一帧（否则用户会先看到空画布）。
    assert mw.sky_view is not None
    assert lt.widget(idx) is mw.sky_view, "页签里必须是 sky_view 本尊"
    assert mw._sky_placeholder is None

    # 既有初始化全部执行到
    assert mw.sat_tracker is not None, "目录/站址 tracker 没补建"
    assert mw.sat_tracker.sky_view is mw.sky_view
    assert mw.sky_view._observer is not None, "站址没注入天空视图"
    assert calls["bind"] >= 1, "_bind_sky_catalog 被延迟漏掉了"
    assert calls["invalidate"] >= 1, "过境列表没作废"
    assert mw._all_sky_btn is not None, "全天模式开关没补建"
    assert (mw.sat_track_panel._pass_provider.__func__
            is type(mw)._provide_sat_passes)
    # 浮层已从占位改挂到真视图（否则换父后消失）
    assert mw._planetarium_strip.parent() is mw.sky_view
    assert mw._sky_fs_btn.parent() is mw.sky_view

    # 信号接线：发射一次，看探针是否被调用
    mw.sky_view.question_requested.emit("这颗星是什么？")
    mw.sky_view.track_toggled.emit(True, "NOAA 19", 137.1e6)
    mw.sky_view.night_vision_changed.emit(True)
    assert calls["question"] == 1
    assert calls["track"] == 1
    assert calls["night"] == 1


def test_ensure_is_idempotent(mw):
    """重复补建必须是同一对象：不能每次调用都重建 394 ms 的视图。"""
    first = mw._ensure_sky_view()
    second = mw._ensure_sky_view()
    assert first is not None and second is first
