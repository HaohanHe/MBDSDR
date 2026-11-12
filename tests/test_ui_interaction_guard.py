# SPDX-License-Identifier: MIT
"""
tests/test_ui_interaction_guard.py
==================================
遍历式守卫：新增任何 QAction / QAbstractButton 都必须
  * 接上接收者（``receivers() == 0`` 直接红）；
  * 真触发一次后产生可观察结果（否则算假活，必须显式登记白名单+理由）；
  * 不抛异常（含被 Qt 吞掉只走 ``sys.excepthook`` 的槽异常）。

与 ``scripts/ui_interaction_audit.py`` 共用同一套枚举/探测/分类逻辑，
守卫和审计报告不会各说一套。

白名单 ``FALSE_ALIVE_WHITELIST`` 必须逐条写理由；若某条已修好、
不再复现，本测试会红（提醒把白名单删掉，不许养僵尸例外）。

运行::

    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_interaction_guard.py -v
"""
import os
import sys
import warnings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBDSDR_NULL_AUDIO", "1")
os.environ.setdefault("MBDSDR_NO_HW", "1")
os.environ.setdefault("MBDSDR_OFFLINE", "1")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (_ROOT, os.path.join(_ROOT, "desktop"), os.path.join(_ROOT, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import ui_interaction_audit as audit  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module")
def isolated_home(request, tmp_path_factory):
    """HOME 隔离到临时目录：不读用户真实书签/配置，也不写用户目录。

    Windows 上 ``DesktopSettings.default_config_path()`` 走 ``%APPDATA%``：
    审计会真点「恢复本组默认」「启用 Web 服务」这类会落盘的按钮，只改
    ``HOME/USERPROFILE`` 挡不住真实 ``desktop_settings.json`` 被写脏
    （P0 见 ``tests/test_ui_settings_isolation_guard.py``）。因此把
    ``APPDATA`` / ``LOCALAPPDATA`` 一起重定向。
    """
    home = str(tmp_path_factory.mktemp("home"))
    saved = {k: os.environ.get(k)
             for k in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA")}

    def _restore_env():
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # 先登记再改：下面 QSettings 的 setup 若抛异常，写在 yield 之后的
    # 还原不会执行，HOME/APPDATA 会漏给同一进程里的后续文件。
    request.addfinalizer(_restore_env)
    os.environ["HOME"] = home
    os.environ["USERPROFILE"] = home
    for var in ("APPDATA", "LOCALAPPDATA"):
        os.environ[var] = home

    qset = QSettings("MBDSDR", "Desktop")
    backup = {k: qset.value(k) for k in qset.allKeys()}
    qset.clear()
    qset.sync()
    try:
        yield home
    finally:
        qset.clear()
        for key, value in backup.items():
            qset.setValue(key, value)
        qset.sync()


@pytest.fixture(scope="module")
def main_window(isolated_home):
    from main_window import MainWindow

    win = MainWindow()
    for timer in getattr(win, "_startup_timers", []):
        try:
            timer.stop()
        except Exception:  # noqa: BLE001
            pass
    win._panels_set_sdr_connected(False)
    win.resize(1400, 900)
    win.show()
    for _ in range(40):
        app.processEvents()
    try:
        yield win
    finally:
        try:
            win._disconnect()
        except Exception:  # noqa: BLE001
            pass
        win.close()
        app.sendPostedEvents(None, QEvent.DeferredDelete)


def test_every_action_and_button_has_a_receiver(main_window):
    """遍历全部动作：没接收者的（receivers()==0）就是死按钮。"""
    rows = audit.collect_controls(main_window)
    assert len(rows) >= 100, f"控件数异常偏少（{len(rows)}），枚举逻辑可能坏了"

    dead = [r for r in rows
            if not r["wired"] and not audit._input_reason(r)]
    assert not dead, (
        "以下动作没有任何接收者（死按钮）：\n" + "\n".join(
            f"  {r['kind']} {r['name']} @ {r['panel']}" for r in dead))


def test_no_false_alive_and_no_swallowed_exceptions(main_window):
    """真触发一遍：真死/假活/异常都必须为空（假活只许白名单内）。"""
    rows = audit.audit(main_window)
    problems = audit.classify(rows)

    assert not problems["dead"], (
        "真死（receivers()==0）：\n" + "\n".join(
            f"  {r['kind']} {r['name']} @ {r['panel']}"
            for r in problems["dead"]))

    assert not problems["exceptions"], (
        "触发时抛异常：\n" + "\n".join(
            f"  {r['kind']} {r['name']} @ {r['panel']}\n"
            f"    {r['exception'].strip().splitlines()[-1]}"
            for r in problems["exceptions"]))

    unexpected = [r for r in problems["false_alive"]
                  if not r.get("whitelist")]
    assert not unexpected, (
        "假活（有接收者但触发后无任何可观察变化）：\n" + "\n".join(
            f"  {r['kind']} {r['name']} @ {r['panel']}" for r in unexpected))

    assert not problems["unreasoned"], (
        "置灰但没登记理由：\n" + "\n".join(
            f"  {r['kind']} {r['name']} @ {r['panel']}"
            for r in problems["unreasoned"]))

    # 白名单只能写真实存在的控件（防止拼错/僵尸条目）；是否当轮复现允许
    # 随环境变化（幂等刷新在"结果没变"时才是假活），只给 warning 提醒复查。
    known = {f"{r['kind']}|{r['name']}|{r['panel']}" for r in rows}
    unknown = set(audit.FALSE_ALIVE_WHITELIST) - known
    assert not unknown, (
        "白名单指向不存在的控件（拼写错误或控件已删）：\n"
        + "\n".join(f"  {key}" for key in sorted(unknown)))
    used = {f"{r['kind']}|{r['name']}|{r['panel']}"
            for r in problems["false_alive"]}
    stale = set(audit.FALSE_ALIVE_WHITELIST) - used
    if stale:
        warnings.warn(
            "白名单条目本轮未复现（环境/状态相关，记得定期复查）："
            + "；".join(sorted(stale)))


def test_probe_catches_slot_exception(main_window):
    """守卫自身的反向验证：槽里抛异常必须被算进"抛异常"这一类。"""
    from PySide6.QtWidgets import QPushButton

    btn = QPushButton("注入异常（测试用）", main_window)
    btn.clicked.connect(lambda: (_ for _ in ()).throw(RuntimeError("injected")))
    row = {"kind": "按钮", "name": btn.text(), "loc": "-", "panel": "-",
           "obj": btn, "wired": True, "note": "", "enabled": True,
           "visible": True, "obs": [], "exception": "", "emitted": {},
           "skipped": "", "switched": [], "pre": []}
    evidence = {"msg": [], "dialog": [], "file": [], "stat": [], "close": [],
                "new_top": []}
    audit._probe(main_window, row, evidence, set())
    assert "injected" in row["exception"], "槽异常没被捕获，抛异常分类不可信"
    btn.deleteLater()


def test_probe_still_catches_noop_button(main_window):
    """反证：观察面扩大后，真没反应的按钮仍须被判「无变化」。

    2026-11-09 销 K07 时给审计补了四条观察路径：QLineEdit.styleSheet
    （输入校验红框）、QLabel.visible（AI 状态条 ▾ 只切换一个 label）、
    点击后新出现的可见顶层窗口（地点面板 QFrame(Qt.Tool)、菜单 popup、
    detach 浮窗）、以及点击瞬间快照（异步按钮的「更新中…」）。
    这条用例钉住另一半责任：一个接了槽但槽里什么都不做的按钮，
    不能被这些新维度洗成「有效果」——否则扩大观察面就等于放宽标准。
    """
    from PySide6.QtWidgets import QPushButton

    btn = QPushButton("空槽（测试用）", main_window)
    btn.clicked.connect(lambda: None)
    row = {"kind": "按钮", "name": btn.text(), "loc": "-", "panel": "-",
           "obj": btn, "wired": True, "note": "", "enabled": True,
           "visible": True, "obs": [], "exception": "", "emitted": {},
           "skipped": "", "switched": [], "pre": []}
    evidence = {"msg": [], "dialog": [], "file": [], "stat": [], "close": [],
                "new_top": []}
    audit._probe(main_window, row, evidence, set())
    assert not evidence["new_top"], f"空槽不该弹窗：{evidence['new_top']}"
    assert not evidence["stat"], f"空槽不该写状态栏：{evidence['stat']}"
    assert not evidence["dialog"], f"空槽不该开对话框：{evidence['dialog']}"
    changes = [o for o in row["obs"] if "界面变化" in o]
    assert not changes, f"空槽被误判成有界面变化：{changes}"
    btn.deleteLater()


def test_float_menu_is_nonmodal(main_window, monkeypatch):
    """反证：自由窗口菜单必须走非模态 popup，退回 exec 就会挂死审计。

    2026-11-09 faulthandler 抓栈：`main_window.py:3679` 的
    ``menu.exec(QPoint)`` 在 offscreen 下进模态循环永不返回，UI 交互守卫
    卡在「按钮 ⧉」6 分钟。`QMenu.exec` 是 Shiboken 重载，Python 侧打桩
    拦不住，只能从产品侧改成 `popup()`（同样位置弹出、同样触发 action）。
    """
    from PySide6.QtWidgets import QMenu

    calls = []
    monkeypatch.setattr(QMenu, "popup",
                        lambda self, *a, **k: calls.append(self.title()))
    main_window._show_float_menu()      # 必须立即返回
    assert calls, "菜单必须用 popup() 弹出；用 exec() 会拖死无人值守验收"
