# SPDX-License-Identifier: MIT
"""
tests/test_ui_layout_actions.py
================================
AI 动态 UI 最小真闭环（模型 → ui_layout_control → 界面真的变了）。

钉住的事实：
  1. 校验与诚实空态：未知动词/坏比例/空目标 → PARAM_ERROR；
     执行器未挂载（非桌面）→ UNAVAILABLE，不假装界面变了。
  2. 三个动词族真实生效（MainWindow offscreen 证据）：
     - hide_panel/show_panel：面板菜单勾选态与页签可见性同步翻转；
     - select_tab：左栏页签切到目标 id；
     - set_layout_ratio：三栏 splitter 按 "2:2:1" 比例分配总宽。
  3. 可撤销：undo 弹逆栈，面板/页签/比例恢复原状；空栈诚实 EMPTY。
  4. 工具面：agent 注册表有 ui_layout_control/ui_layout_undo，
     描述含动词清单与可撤销语义；界面动作**不**走 arm_manual 门
     （但写工具人工门其他面不受影响）。
  5. 排水模型：动作先入队，MainWindow 的 150ms 定时器排水后生效
     （AIWorker 后台线程不直接碰控件）。

运行:
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ui_layout_actions.py -v
"""
import logging
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

log = logging.getLogger(__name__)
from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdsdr_ai.ui_actions import (  # noqa: E402
    UI_ACTION_VERBS, UiActionBus, _reset_ui_bus_for_tests, get_ui_bus,
)

app = QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def _clean_bus():
    _reset_ui_bus_for_tests()
    yield
    _reset_ui_bus_for_tests()


# ---------------------------------------------------------------------------
# 校验与诚实空态
# ---------------------------------------------------------------------------
class TestValidation:
    def test_unknown_verb_rejected(self):
        bus = UiActionBus()
        r = bus.enqueue({"verb": "explode", "target": "spectrum"})
        assert r["ok"] is False and r["status"] == "PARAM_ERROR"
        assert "白名单" in r["reason"]

    def test_bad_ratio_rejected(self):
        bus = UiActionBus()
        for bad in ("1:2", "a:b:c", "0:0:0", "1:2:3:4"):
            r = bus.enqueue({"verb": "set_layout_ratio", "value": bad})
            assert r["ok"] is False, bad
            assert r["status"] == "PARAM_ERROR"

    def test_empty_target_rejected(self):
        bus = UiActionBus()
        r = bus.enqueue({"verb": "show_panel", "target": "  "})
        assert r["ok"] is False and r["status"] == "PARAM_ERROR"

    def test_no_executor_unavailable(self):
        bus = UiActionBus()
        r = bus.enqueue({"verb": "show_panel", "target": "spectrum"})
        assert r["ok"] is False and r["status"] == "UNAVAILABLE"
        assert "未挂载" in r["reason"]

    def test_verbs_whitelist(self):
        assert set(UI_ACTION_VERBS) == {
            "show_panel", "hide_panel", "select_tab",
            "focus_panel", "set_layout_ratio"}


# ---------------------------------------------------------------------------
# MainWindow 真执行（模型说了一句 → 界面真的变了）
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(scope="module")
def mw(qapp):
    from main_window import MainWindow
    # 隔离持久化状态（QSettings 几何/分栏、gui_config window 键、
    # desktop_settings 的 app_mode）：否则用户真机/其他测试落盘的状态
    # 决定 left_tab 结构（app_mode=planetarium 时 rf_sky 页签被
    # reparent 走，select_tab/undo 断言随之非确定性失败）。
    import json as _json
    from PySide6.QtCore import QSettings
    qs = QSettings("MBDSDR", "Desktop")
    keys = ("window_geometry", "three_column_sizes",
            "three_column_layout_version")
    backup = {k: qs.value(k) for k in keys}
    for k in keys:
        qs.remove(k)
    qs.sync()
    gui_cfg = os.path.expanduser("~/.mbdsdr/gui_config.json")
    cfg_backup = None
    if os.path.exists(gui_cfg):
        with open(gui_cfg, "r", encoding="utf-8") as f:
            cfg_backup = f.read()
        try:
            data = _json.loads(cfg_backup)
            if "window" in data:
                data.pop("window")
                with open(gui_cfg, "w", encoding="utf-8") as f:
                    _json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    from settings import DesktopSettings
    ds = DesktopSettings.load()
    old_mode = ds.get("app_mode", "ide")
    ds.set("app_mode", "ide")
    w = MainWindow()
    for _t in getattr(w, "_startup_timers", []):
        try:
            _t.stop()
        except Exception:
            pass
    w.show()
    yield w
    try:
        w._disconnect()
    except Exception as exc:  # noqa: BLE001 - 收尾失败必须留证，不静默
        log.warning("test_ui_layout_actions.py fixture 收尾失败（MainWindow._disconnect）：%s: %s",
                    type(exc).__name__, exc)
    w.close()
    for k, v in backup.items():
        if v is None:
            qs.remove(k)
        else:
            qs.setValue(k, v)
    qs.sync()
    if cfg_backup is not None:
        with open(gui_cfg, "w", encoding="utf-8") as f:
            f.write(cfg_backup)
    ds2 = DesktopSettings.load()
    ds2.set("app_mode", old_mode)


def _drain(mw):
    return get_ui_bus().drain()


class TestExecutorWiring:
    @pytest.fixture(autouse=True)
    def _mount_executor(self, mw):
        """每个用例前重新挂载执行器（module 级 reset 会清掉总线）。"""
        get_ui_bus().executor = mw._execute_ui_action

    def test_bus_mounted_by_main_window(self, mw):
        bus = get_ui_bus()
        assert bus.executor is not None
        assert getattr(mw, "_ui_bus_timer", None) is not None
        assert mw._ui_bus_timer.isActive()

    def test_hide_and_show_panel(self, mw):
        bus = get_ui_bus()
        act = mw._panel_actions["weather"]
        # 状态无关驱动：先隐藏（模型说：隐藏气象云图）
        assert bus.enqueue({"verb": "hide_panel",
                            "target": "weather"})["ok"] is True
        _drain(mw)
        assert act.isChecked() is False, "hide_panel 未生效"
        # 模型说：显示回来
        assert bus.enqueue({"verb": "show_panel",
                            "target": "weather"})["ok"] is True
        _drain(mw)
        assert act.isChecked() is True, "show_panel 未生效"

    def test_unknown_panel_id_honest_error(self, mw):
        bus = get_ui_bus()
        assert bus.enqueue({"verb": "hide_panel",
                            "target": "__nope__"})["ok"] is True
        results = _drain(mw)
        assert results and results[0]["ok"] is False
        assert "未知面板" in results[0]["reason"]

    def test_select_tab(self, mw):
        bus = get_ui_bus()
        tab = mw.left_tab
        sky_idx = next(i for i in range(tab.count())
                       if tab.tabBar().tabData(i) == "rf_sky")
        assert bus.enqueue({"verb": "select_tab",
                            "target": "rf_sky"})["ok"] is True
        _drain(mw)
        assert tab.currentIndex() == sky_idx, "select_tab 未切到天空模式"

    def test_set_layout_ratio(self, mw):
        bus = get_ui_bus()
        # 小窗口下三栏全被 Qt 夹到最小宽度，任何比例都会钳回最小态——
        # 先放大窗口留出弹性空间，再验证比例生效
        mw.resize(1600, 900)
        assert bus.enqueue({"verb": "set_layout_ratio",
                            "value": "2:2:1"})["ok"] is True
        results = _drain(mw)
        assert results[0]["ok"] is True and results[0]["status"] == "DONE"
        sizes = list(mw._main_splitter.sizes())
        total = sum(sizes)
        # 2:2:1 → 左 40% / 中 40% / 右 20%（±2px 取整容差）
        assert abs(sizes[0] - total * 0.4) <= 2, sizes
        assert abs(sizes[1] - total * 0.4) <= 2, sizes
        assert abs(sizes[2] - total * 0.2) <= 2, sizes

    def test_undo_restores(self, mw):
        bus = get_ui_bus()
        act = mw._panel_actions["weather"]
        bus.enqueue({"verb": "hide_panel", "target": "weather"})
        _drain(mw)
        assert act.isChecked() is False
        # 撤销 → 恢复显示
        r = bus.undo()
        assert r["ok"] is True, r
        assert act.isChecked() is True

    def test_undo_empty_stack_honest(self):
        bus = UiActionBus()
        r = bus.undo()
        assert r["ok"] is False and r["status"] == "EMPTY"

    def test_undo_restores_tab(self, mw):
        bus = get_ui_bus()
        tab = mw.left_tab
        spec_idx = next(i for i in range(tab.count())
                        if tab.tabBar().tabData(i) == "spectrum")
        tab.setCurrentIndex(spec_idx)
        bus.enqueue({"verb": "select_tab", "target": "rf_sky"})
        _drain(mw)
        assert tab.tabBar().tabData(tab.currentIndex()) == "rf_sky"
        r = bus.undo()
        assert r["ok"] is True
        assert tab.currentIndex() == spec_idx


# ---------------------------------------------------------------------------
# 工具面与安全边界
# ---------------------------------------------------------------------------
class TestToolSurface:
    def test_tools_registered_with_undo_semantics(self):
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig
        ag = MBDSDRAgent(AgentConfig())
        names = set(ag.tool_registry.get_tool_names())
        assert "ui_layout_control" in names
        assert "ui_layout_undo" in names
        descs = {t["name"]: t.get("description", "")
                 for t in ag.tool_registry.list_tools()}
        d = descs["ui_layout_control"]
        for verb in ("show_panel", "hide_panel", "select_tab",
                     "set_layout_ratio"):
            assert verb in d
        assert "撤销" in d

    def test_ui_action_not_write_gated(self):
        """界面动作不应进 arm_manual 写工具门（不动硬件/文件/发射）。"""
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig
        ag = MBDSDRAgent(AgentConfig())
        from mbdsdr_ai.tool_registry import _auto_write
        assert _auto_write("ui_layout_control", {}) is False
        assert _auto_write("ui_layout_undo", {}) is False
        # 对照：写工具门本身没被削弱（arm_manual 契约不回退）
        assert _auto_write("sdr_transmit_cw",
                           {"properties": {"arm_manual": {}}}) is True

    def test_agent_tool_call_enqueues(self):
        """从工具面调用（模型姿势）→ 入队成功（桌面执行器由 fixture 挂载）。"""
        from mbdsdr_ai.agent import MBDSDRAgent
        from mbdsdr_ai.config import AgentConfig
        from mbdsdr_ai.ui_actions import get_ui_bus
        get_ui_bus().executor = lambda a: {"ok": True, "status": "DONE"}
        ag = MBDSDRAgent(AgentConfig())
        res = ag.tool_registry.call("ui_layout_control",
                                    {"verb": "show_panel", "target": "ai"})
        assert res.success is True
        assert get_ui_bus()._queue, "工具面调用未入队"
