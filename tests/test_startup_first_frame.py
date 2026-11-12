# SPDX-License-Identifier: MIT
"""P0-14 冷启动反馈闸门：splash 必须早于启动自检与主窗口导入。

改前（a52ce510 之前）``_run_import_smoke()`` / ``_startup_dependency_problems()``
和 ``from main_window import MainWindow`` 都排在 QApplication / splash 之前，
进程起到第一个可见窗口实测 5.5–8.4 s（`D:\\mbdsdr\\output\\tmp\\visible_baseline.json`
3 次：8422.9 / 6244.8 / 5468.1 ms），这段时间屏幕上什么都没有。改后同一脚本
3 次为 2797.6 / 3191.6 / 2621.5 ms（`visible_after.json`）。

本文件钉的是**顺序**而不是秒数：秒数随机器负载波动（同机并发跑测试时主窗口
可见从 9.5 s 涨到 16.9 s），写死阈值只会变成噪声。顺序一退回去，
``test_splash_precedes_selfcheck_and_window_import`` 必红。

两条断言各管一件事：
1. ``main`` 模块导入本身不得连带导入 ``main_window``（子进程实测，不靠读源码）；
2. ``main()`` 的事件次序必须是 qapp → splash → deps → mainwindow → deferred
   （scipy 私有子树的真 import 在首帧之后，见 ``_start_deferred_imports``）。
"""
import os
import subprocess
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DESKTOP = os.path.join(_ROOT, "desktop")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import importlib  # noqa: E402

main_mod = importlib.import_module("main")


def test_module_import_does_not_pull_in_main_window():
    """``import main`` 只应拿界面基础依赖，不该把主窗口模块一起拖进来。"""
    code = ("import sys, main; "
            "print('MAIN_WINDOW_IMPORTED=' + str('main_window' in sys.modules))")
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("QT_QPA_PLATFORM", None)
    proc = subprocess.run([sys.executable, "-c", code], cwd=_DESKTOP, env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=180)
    assert proc.returncode == 0, proc.stderr
    assert "MAIN_WINDOW_IMPORTED=False" in proc.stdout, \
        "main_window 在模块导入阶段就被拉进来了：首屏空白期会重新变长"


class _FakeApp:
    """只记事件，不碰真 Qt：本用例判的是顺序，不需要真窗口。"""

    def __init__(self, argv=None, events=None):
        self.events = events if events is not None else []
        self.events.append("qapp")

    @staticmethod
    def setHighDpiScaleFactorRoundingPolicy(_policy):
        return None

    def setApplicationName(self, _name):
        return None

    setApplicationDisplayName = setApplicationName
    setOrganizationName = setApplicationName
    setApplicationVersion = setApplicationName

    def setFont(self, _font):
        return None

    def setWindowIcon(self, _icon):
        return None

    def exec(self):
        return 0


class _FakeSplash:
    def __init__(self, events):
        self.events = events

    def finish(self, _window):
        return None

    def close(self):
        return None


def _app_factory(events):
    """造一个「类」而不是 lambda：main() 会先在类上调 HighDpi 策略。"""

    class _App(_FakeApp):
        def __init__(self, argv=None):
            super().__init__(argv, events)

    return _App


class _FakeTheme:
    display_name = "fake"
    colors = {"bg": "#000000", "border": "#000000", "text": "#ffffff",
              "primary": "#888888", "accent": "#aaaaaa"}
    spectrum_colors = ["#000000", "#ffffff"]

    def apply(self, _app):
        return None


class _FakeWindow:
    def __init__(self):
        self.settings = None
        self.app = None

    def setWindowIcon(self, _icon):
        return None

    def show(self):
        return None

    def apply_theme_once(self):
        return None


def test_splash_precedes_selfcheck_and_window_import(monkeypatch, tmp_path):
    events = []

    def _splash(_app):
        events.append("splash")
        return _FakeSplash(events)

    monkeypatch.setattr(main_mod, "QApplication", _app_factory(events))
    monkeypatch.setattr(main_mod, "Qt", type("Qt", (), {
        "HighDpiScaleFactorRoundingPolicy": type(
            "P", (), {"PassThrough": 0})}))
    monkeypatch.setattr(main_mod, "_qt_platform_plugin_problem", lambda: None)
    monkeypatch.setattr(main_mod, "_show_startup_splash", _splash)
    monkeypatch.setattr(main_mod, "_startup_dependency_problems",
                        lambda: (events.append("deps"), [])[1])
    monkeypatch.setattr(main_mod, "_import_main_window",
                        lambda splash=None: (events.append("mainwindow"),
                                             _FakeWindow)[1])
    monkeypatch.setattr(main_mod, "_install_combo_wheel_guard", lambda app: None)
    monkeypatch.setattr(main_mod, "_install_sigint_graceful_quit", lambda app: None)
    monkeypatch.setattr(main_mod, "_report_runtime_dlls", lambda: None)
    monkeypatch.setattr(main_mod, "_check_record_dir_async", lambda window: None)
    monkeypatch.setattr(main_mod, "_start_deferred_imports",
                        lambda window: events.append("deferred"))
    monkeypatch.setattr(main_mod, "get_theme", lambda name: _FakeTheme())
    monkeypatch.setattr(main_mod, "get_app_font", lambda: None)
    # QIcon 会真的读 PNG 造 QPixmap：没有真实 QGuiApplication 时 Qt 直接 qFatal。
    monkeypatch.setattr(main_mod, "QIcon", lambda *_a, **_k: None)

    # i18n 段会读 settings：先把它隔离到 tmp_path，绝不碰用户真实配置。
    for var in ("APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["main.py"])

    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 0
    assert events == ["qapp", "splash", "deps", "mainwindow", "deferred"], (
        "启动顺序退化了：splash 必须早于启动自检与主窗口导入，"
        "scipy 的延后导入必须在主窗显示之后，实际 " + repr(events))


def test_splash_closed_before_failure_dialog(monkeypatch, tmp_path):
    """缺件中止时先收 splash 再弹窗：失败提示不该压在「正在加载」上。"""
    events = []
    reported = []

    class _Splash(_FakeSplash):
        def close(self):
            events.append("splash_closed")

    monkeypatch.setattr(main_mod, "QApplication", _app_factory(events))
    monkeypatch.setattr(main_mod, "Qt", type("Qt", (), {
        "HighDpiScaleFactorRoundingPolicy": type(
            "P", (), {"PassThrough": 0})}))
    monkeypatch.setattr(main_mod, "_qt_platform_plugin_problem", lambda: None)
    monkeypatch.setattr(main_mod, "_show_startup_splash",
                        lambda app: (events.append("splash"), _Splash(events))[1])
    monkeypatch.setattr(main_mod, "_start_deferred_imports", lambda window: None)
    monkeypatch.setattr(main_mod, "_startup_dependency_problems",
                        lambda: ["• 缺 scipy"])
    monkeypatch.setattr(main_mod, "_report_startup_failure",
                        lambda summary, detail: reported.append(summary))
    monkeypatch.setattr(main_mod, "_import_main_window",
                        lambda splash=None: pytest.fail("缺件时不许导入主窗口"))
    monkeypatch.setattr(main_mod, "_install_combo_wheel_guard", lambda app: None)
    for var in ("APPDATA", "LOCALAPPDATA", "HOME", "USERPROFILE"):
        monkeypatch.setenv(var, str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["main.py"])

    assert main_mod.main() == 3
    assert reported == ["启动自检失败：缺少必需组件（已中止启动）"]
    assert events == ["qapp", "splash", "splash_closed"], \
        "失败路径没有先收 splash：" + repr(events)
