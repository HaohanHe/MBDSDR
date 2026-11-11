# SPDX-License-Identifier: MIT
"""零门槛首次打开的契约（2026-11-08）。

用户口径：「漂亮、零门槛，小姑娘不用学原理也能用」；但**不造假**是更高
的红线。这里钉四件事：

1. 首屏不拿一整条硬件排查报错糊脸，完整排查仍在 tooltip / 「查看怎么办」；
2. 未定位时默认绝不伪造站址（沿用 test_no_sim_regression 的口径），
   但空态画成星象仪、并给一颗可点的「先看看示例天空」能力位；
3. 示例位置全程标注、不写进配置，真实 GNSS / 手填坐标能覆盖它；
4. 「☾ 今夜」照 Stellarium ``AstroCalcAlmanacWidget`` 按**真实太阳高度**
   求时刻（-18° 天文暮光终），不是写死 22:00，也不在选位置时自动跳时间。
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def win(isolated_profile, qapp):
    # 共用窗口：每个用例新建 MainWindow 会让 Qt 对象/定时器累积挂起
    # （见 2026-11-08 日志），布局类测试一律 module scope。
    from PySide6.QtCore import Qt
    from main_window import MainWindow
    w = MainWindow()
    # 星象仪视图惰性构建：本文件测的就是天空空态，先按"打开星象仪"补建。
    w._ensure_sky_view()
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.resize(1280, 800)
    w.show()
    for _ in range(20):
        qapp.processEvents()
    # 横幅挂在频谱页左栏：只有该页是当前页，`QLabel` 才会被布局成真实宽度。
    # 否则 `width()` 停在 Qt 默认的 640，`_apply_responsive_chrome` 拿 626 的
    # 假预算去算省略，测试就永远绿——假绿比红坏得多（2026-11-12 用户口径）。
    w.left_tab.setCurrentWidget(w._spectrum_page)
    for _ in range(12):
        qapp.processEvents()
    yield w
    w.close()
    # 本文件多处 set_app_mode("planetarium")，会把会话级 desktop_settings.json
    # 留成星象仪；后续模块按 IDE 形态构造窗口时会直接进星象仪（惰性构建就
    # 被绕过了）。收尾还回 IDE 并同步落盘。
    try:
        w.settings.set("app_mode", "ide")
        w.settings.flush()
    except (AttributeError, TypeError, ValueError):
        pass
    # 时间引擎是全局单例：本文件按过「☾ 今夜」，必须还给后续测试一个
    # 实时、1× 的干净状态，否则跨文件会莫名其妙红。
    from mbdsdr_ai.new_spacetime import get_time_engine
    eng = get_time_engine()
    eng.set_time_now()
    eng.set_rate(1.0)


def _pump(n=12):
    from PySide6.QtWidgets import QApplication
    for _ in range(n):
        QApplication.processEvents()


# ---------------------------------------------------------------------------
# 1. 纯函数：今夜时刻必须由真实太阳高度算出，不是固定钟点
# ---------------------------------------------------------------------------
def test_night_time_is_real_solar_altitude_not_fixed_clock():
    from mbdsdr_ai.atmosphere import sun_altitude_deg
    from main_window import _night_observation_time

    got = _night_observation_time(45.0, 120.0)   # 仓库中性示例站
    assert got is not None
    t, alt = got
    assert alt == -18.0, "默认应取天文暮光终（太阳 -18°）"
    jd = t.timestamp() / 86400.0 + 2440587.5
    assert abs(sun_altitude_deg(jd, 45.0, 120.0) + 18.0) < 0.3, \
        "跳过去的时刻，太阳几何高度必须真的接近 -18°"

    # 换一个站点：真实日落时刻必须变，证明不是写死「今晚 22:00」。
    # 按 UTC 日扫描时两地天文暮光终可能只差几十分钟，但绝不该完全相同。
    other, _ = _night_observation_time(-33.8688, 151.2093)   # 悉尼
    # 天文暮光终随季节变化，两站差值实测可低至约 10 分钟；
    # 断言本意是「不是写死时刻」，不是硬卡 10 分钟，且仍要求确实不同。
    delta = abs((other - t).total_seconds())
    assert 60 < delta < 6 * 3600, f"两站暮光终差值异常：{delta:.1f}s"


# ---------------------------------------------------------------------------
# 2. 首屏：短句说人话，排查细节没丢
# ---------------------------------------------------------------------------
def test_first_open_hint_is_short_and_detail_is_kept(win):
    from main_window import _NO_SDR_BRIEF, _NO_SDR_HINT

    win._show_no_rtl_hint(_NO_SDR_HINT, code="device_not_found",
                          brief=_NO_SDR_BRIEF)
    hint = win._no_device_hint
    assert hint is not None
    assert "USB 没插好" not in hint.text(), "首屏不该糊一整条排查报错"
    assert "Zadig" not in hint.text()
    assert "星象仪" in hint.text()
    assert "Zadig" in hint.toolTip(), "详细排查要留在该去的地方，不是删掉"
    assert win.status_conn.text() == "未连接"


# ---------------------------------------------------------------------------
# 3. 未定位：诚实空态 + 可点的示例天空能力位
# ---------------------------------------------------------------------------
def test_unlocated_default_is_honest_but_clickable(win):
    sv = win.sky_view
    assert sv.get_observer() is None, "无 GNSS / 无手填时绝不伪造站址"
    assert sv._data_source == "none"
    assert sv.get_site_note() == ""

    win.set_app_mode("planetarium")
    _pump(20)
    sv.repaint()
    r = sv._preview_rect
    assert r is not None and r.width() > 100, \
        "空态必须画成星象仪并给「先看看示例天空」按钮，不能是一片空蓝"


def test_preview_sky_is_labeled_and_never_written_to_config(win):
    sv = win.sky_view
    win.set_app_mode("planetarium")
    _pump(12)
    sv.repaint()
    r = sv._preview_rect
    assert r is not None
    assert sv._hud_hit(r.center().x(), r.center().y()) is True

    obs = sv.get_observer()
    assert obs is not None
    assert obs["lat"] == pytest.approx(45.0)
    assert "示例" in sv.get_site_note()
    assert "非你的位置" in sv.get_site_note()
    # 关键：配置里仍是「未定位」，GNSS 自动定位不会被误判为"已配置"
    assert win._observer_lat is None and win._observer_lon is None
    # 示例态也要真实历表：太阳高度未知时白天会画出假星空
    assert win.sat_tracker is not None
    # 选位置绝不自动跳时间（那是「☾ 今夜」按钮自己的事）
    from mbdsdr_ai.new_spacetime import get_time_engine
    assert abs(get_time_engine().now_unix() - time.time()) < 3.0  # timeguard: allow


# ---------------------------------------------------------------------------
# 4. 「☾ 今夜」+ 回到此刻
# ---------------------------------------------------------------------------
def test_tonight_button_jumps_to_real_twilight_and_can_return(win):
    from mbdsdr_ai.new_spacetime import get_time_engine
    from main_window import _night_observation_time

    btn = win._planetarium_tonight_btn
    assert btn is not None and "今夜" in btn.text()

    obs = win.sky_view.get_observer()
    assert obs is not None
    t, alt = _night_observation_time(obs["lat"], obs["lon"])
    win._jump_to_tonight()
    eng = get_time_engine()
    assert abs(eng.now_unix() - t.timestamp()) < 3.0, \
        "按钮必须跳到按真实太阳高度算出的那一刻"
    assert alt == -18.0

    # 时间流速循环 1× → 60× → 600× → 停 → 1×，回到 1× 即回到此刻
    for _ in range(4):
        win._cycle_time_rate()
    assert abs(eng.now_unix() - time.time()) < 3.0  # timeguard: allow


# ---------------------------------------------------------------------------
# 5. 真实站址就位：示例标注与模拟时刻都要撤掉
# ---------------------------------------------------------------------------
def test_real_location_overrides_preview(win):
    sv = win.sky_view
    if not sv.get_site_note():
        win._on_preview_sky_requested()
    win._observer_lat, win._observer_lon = 43.773268, 125.393425
    win._observer_source = "config"
    win._apply_observer_location()
    assert sv.get_observer()["lat"] == pytest.approx(43.773268)
    assert sv.get_site_note() == ""
    assert sv._data_source == "real"
