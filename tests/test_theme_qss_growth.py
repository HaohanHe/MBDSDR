# SPDX-License-Identifier: MIT
"""
主题切换不得让 app 级样式表变长（A′ 回归）
==========================================

2026-11-11 实测缺陷：触屏模式下每次切主题 ``app.styleSheet()`` 都长 528 字节
（20832 → 22944）。根因是 ``MainWindow._apply_theme`` 把**已含触控后缀**的
``app.styleSheet()`` 记成了 ``_theme_qss`` 基串，``_apply_touch_stylesheet``
再叠一遍——切一百次就是 50 KB 的样式表，每次重抛光都要重新解析。

本文件钉四件事：
  1. 触屏模式下连切 20 次主题，``len(app.styleSheet())`` 恒定（失败时打印
     切前长度，能直接看出是不是又叠了）；
  2. 切主题期间 app 级 ``setStyleSheet`` 调用为 0；切换一次触控开关后恰好
     +1，且后缀长度真的增减——不能靠「永不重设」蒙混过关；
  3. 全仓不得把 ``app.styleSheet()`` 取来当**基串**拼后缀：monkeypatch
     ``QApplication.styleSheet`` 记录调用栈，只允许 themes.py 的前缀判定与
     main_window.py 的两处相等判定这三个「只比较」的读点；
  4. 后缀只生成一次：连切 3 个主题后标记串出现 1 次，且总长恒为
     ``len(theme._qss()) + 528``。

**量测前提：必须 ``MBDSDR_OFFLINE=1``。** 本文件在模块顶部显式 setdefault，
不只依赖 ``tests/conftest.py``（那里也是 setdefault，被外部环境覆盖就失效）。
不隔离硬件时 ``MainWindow`` 构造期 ``_auto_enum_worker`` 会自动连上板载
RTL-SDR，20 fps IQ 轮询把事件插进 ``processEvents()``：2026-11-12 两轮实测
分别出现 27956.5 ms 与 14132.0 ms 的尖峰，紧随其后一次控件级 setStyleSheet
突增（94 / 108 次）。离线重跑同一序列无尖峰、极差 142–210 ms——尖峰是设备
轮询，不是主题路径（那两轮应用级 setStyleSheet 始终为 0）。纪律登记见
``docs/evidence-standard.md`` 规则 14。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_theme_qss_growth.py -q
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

THEME_CYCLE = ["default", "dark", "high_contrast", "dark_car", "figma"]


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


@pytest.fixture(scope="module")
def mw(isolated_profile, qapp):
    # isolated_profile：MainWindow 会读写真实 %APPDATA% 配置，不隔离会污染用户设置。
    from main_window import MainWindow
    w = MainWindow()
    yield w
    try:
        w.close()
    except Exception:
        pass


class _CountAppStyleSheet:
    """只统计 **app 级** setStyleSheet（控件级不算，那是各面板自己的样式）。"""

    def __init__(self, app):
        self.app = app
        self.lengths = []

    def __enter__(self):
        self._orig = QApplication.setStyleSheet

        def _counting(widget, sheet):
            if widget is self.app:
                self.lengths.append(len(sheet))
            return self._orig(widget, sheet)

        QApplication.setStyleSheet = _counting
        return self

    def __exit__(self, *exc):
        QApplication.setStyleSheet = self._orig
        return False

    def clear(self):
        self.lengths.clear()

    @property
    def calls(self):
        return len(self.lengths)


def test_touch_mode_theme_switches_do_not_grow_app_stylesheet(mw, qapp):
    """连切 20 次主题，样式表长度必须恒定。"""
    from touch_manager import touch_manager

    tm = touch_manager()
    old_override = tm._mode_override
    try:
        with _CountAppStyleSheet(qapp) as counter:
            tm.set_override(True)          # 触屏模式：把后缀挂上去
            mw._apply_theme(THEME_CYCLE[0])
            before = len(qapp.styleSheet())
            assert before > 0, "触屏模式首次应用主题后样式表不应为空"

            lengths = []
            calls_per_switch = []
            for i in range(20):
                counter.clear()
                mw._apply_theme(THEME_CYCLE[(i + 1) % len(THEME_CYCLE)])
                lengths.append(len(qapp.styleSheet()))
                calls_per_switch.append(counter.calls)

            assert len(set(lengths)) == 1, (
                f"切主题让 app 级样式表变长：切前 {before} 字节，"
                f"第 20 次后 {lengths[-1]} 字节（差 {lengths[-1] - before}）；"
                f"逐次长度 {lengths}")
            assert lengths[0] == before, (
                f"第一次切换就改变了长度：切前 {before} → {lengths[0]}")
            assert sum(calls_per_switch) == 0, (
                f"切主题仍在重设 app 级样式表（20 次共 {sum(calls_per_switch)} 次，"
                f"逐次 {calls_per_switch}）；A′ 只换 QPalette + 重抛光")
    finally:
        tm.set_override(old_override)


#: 触控后缀长度（2026-11-12 实测 20655 − 20127 = 528）。这是契约不是魔法数：
#: 后缀内容真的改了长度，就更新这里并在提交信息里写明原因。
TOUCH_SUFFIX_LEN = 528

#: 允许读取 ``app.styleSheet()`` 的调用点，全部是**只比较、不拼接**：
#:   themes.py:Theme.apply          ``current == qss or current.startswith(qss)``
#:   main_window.py:_apply_theme    ``app.styleSheet() == self._theme_qss`` 复位判定
#:   main_window.py:_apply_touch_stylesheet  ``app.styleSheet() != target`` 幂等判定
#: 基串唯一来源是 ``MainWindow._theme_qss``（由 ``theme._qss()`` 赋值）。
_ALLOWED_STYLESHEET_READERS = {
    ("themes.py", "apply"),
    ("main_window.py", "_apply_theme"),
    ("main_window.py", "_apply_touch_stylesheet"),
}


def test_app_stylesheet_is_never_read_as_base_for_concatenation(mw, qapp):
    """全仓不得把 ``app.styleSheet()`` 取来当基串拼后缀（只允许比较）。

    这条不变量此前只靠注释维持（``_apply_theme`` / ``_apply_touch_stylesheet``
    各写了一段「绝不记 app.styleSheet()」）。这里把它变成可执行断言：记录
    每次 app 级 ``styleSheet()`` 读取的调用栈，跑一次真实 ``_apply_theme``
    加两次触控切换，任何不在允许名单里的读点都判失败——拼接一定发生在
    某个读点之后，读点收口了拼接才无处藏。
    """
    import inspect

    from touch_manager import touch_manager

    orig_read = QApplication.styleSheet
    hits = []

    def _recording(app_self):
        value = orig_read(app_self)
        if app_self is qapp:
            frame = inspect.stack()[1]     # [0] 是本函数，[1] 才是调用点
            hits.append((os.path.basename(frame.filename), frame.function,
                         frame.lineno, frame.code_context))
        return value

    QApplication.styleSheet = _recording
    tm = touch_manager()
    old_override = tm._mode_override
    try:
        tm.set_override(True)
        mw._apply_theme("dark")            # 真实主题应用（触屏模式带后缀）
        tm.set_override(False)             # 触控切换：触屏 → 鼠标
        tm.set_override(True)              # 触控切换：鼠标 → 触屏
    finally:
        tm.set_override(old_override)
        QApplication.styleSheet = orig_read

    assert hits, "没记录到任何 app.styleSheet() 读取，量具失效（断言无意义）"
    allowed = {h[:2] for h in hits} <= _ALLOWED_STYLESHEET_READERS
    unexpected = [h for h in hits if h[:2] not in _ALLOWED_STYLESHEET_READERS]
    assert allowed, (
        "有代码把 app.styleSheet() 当基串取用（只允许只比较的读点）：\n"
        + "\n".join(f"  {f}:{fn}:{ln}  {ctx}" for f, fn, ln, ctx in unexpected)
        + f"\n允许名单: {sorted(_ALLOWED_STYLESHEET_READERS)}\n"
        + "全部读点: " + str(sorted({h[:3] for h in hits})))


def test_touch_suffix_is_composed_exactly_once_after_three_switches(mw, qapp):
    """连切 3 个主题：后缀标记 1 份，总长 = 基串 + 528。

    长度恒定只能证明「没变长」，证明不了「后缀只有一份」：若有人把后缀
    复制成两段等长的文本，长度同样恒定。这里同时钉标记串出现次数。
    """
    from touch_manager import touch_manager
    import themes as th

    tm = touch_manager()
    old_override = tm._mode_override
    try:
        tm.set_override(True)
        names = THEME_CYCLE[:3]
        for name in names:
            mw._apply_theme(name)

        sheet = qapp.styleSheet()
        base_sheet = th.get_theme(names[-1])._qss()
        base = len(base_sheet)
        assert sheet.count("min-height: 44px") == 1, (
            f"触控后缀重复了 {sheet.count('min-height: 44px')} 份；"
            f"总长 {len(sheet)}，基串 {base}")
        # 该选择器在主题基串里本来就有 2 处（QSS 自身规则），只能用「相对基串
        # 多出 1 处」判重，不能直接数绝对值。
        sel = "QScrollBar::handle:vertical"
        assert sheet.count(sel) == base_sheet.count(sel) + 1, (
            f"滚动条规则比基串多出 {sheet.count(sel) - base_sheet.count(sel)} 处"
            f"（基串 {base_sheet.count(sel)} 处），说明后缀被叠了一层以上")
        assert len(sheet) == base + TOUCH_SUFFIX_LEN, (
            f"总长 {len(sheet)} != 基串 {base} + 后缀 {TOUCH_SUFFIX_LEN}"
            f"（实际差值 {len(sheet) - base}）；"
            "若后缀内容真的改了长度，更新 TOUCH_SUFFIX_LEN 并说明原因")
    finally:
        tm.set_override(old_override)


def test_touch_mode_toggle_appends_and_removes_suffix_once(mw, qapp):
    """触控开关每切换一次只重设一次样式表，并真的增/减后缀。"""
    from touch_manager import touch_manager

    tm = touch_manager()
    old_override = tm._mode_override
    try:
        with _CountAppStyleSheet(qapp) as counter:
            tm.set_override(False)         # 先回到鼠标模式（基串）
            mw._apply_theme(THEME_CYCLE[0])
            mouse_len = len(qapp.styleSheet())

            counter.clear()
            tm.set_override(True)          # 鼠标 → 触屏：恰好一次
            assert counter.calls == 1, (
                f"开启触屏应恰好 setStyleSheet 1 次，实际 {counter.calls} 次")
            touch_len = len(qapp.styleSheet())
            assert touch_len > mouse_len, (
                f"触屏后缀没挂上：鼠标 {mouse_len} → 触屏 {touch_len}")

            counter.clear()
            tm.set_override(False)         # 触屏 → 鼠标：再恰好一次
            assert counter.calls == 1, (
                f"关闭触屏应恰好 setStyleSheet 1 次，实际 {counter.calls} 次")
            assert len(qapp.styleSheet()) == mouse_len, (
                f"关掉触屏后样式表未回到基串：{len(qapp.styleSheet())} != {mouse_len}")
    finally:
        tm.set_override(old_override)
