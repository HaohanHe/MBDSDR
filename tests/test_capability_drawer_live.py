# SPDX-License-Identifier: MIT
"""#92：能力矩阵（CapabilityDrawer）五格死读数 → 真实统计 + 子代理台账可见。

缺陷原状（读码实证）：``desktop/capability_drawer.py`` 的 ``_collect()`` 对
plugin_manager / subagent_manager / workflow_engine / scheduler / memory 五个
对象只渲染「常驻 · ClassName」，而这五个对象**都有 get_stats()**：子代理那份
还含 total_subagents / running_subagents / queued_tasks / 完成失败数（#85 把排队
台账做出来了）。面板自己的文档承诺「只读本进程真实装载的对象，不编造数量」，
工具域 / Hooks / 编排三格确实给了真数字，这五格却是死读数——用户在 UI 上
看不到任何子代理状态，#87 取消 / #90 回收 / #91 生命周期工具做出来的可观测性
在展示层等于没有。另外 ``refresh()`` 只在打开时跑一次（main_window 的
``_show_capability_drawer``），数字是陈旧快照却没有任何口径标注。

本文件钉住补齐后的契约：
1. 五格读**真实对象**的统计（真 agent 冒烟 + 替身对象钉接线，双向都验）；
2. 统计读不出来时诚实说「统计不可用」，没有 get_stats 的对象保留类名兜底，
   对象未装载说「未装载」——绝不编数字；
3. 明细条给出子代理**真实任务行**（排队目标 / 最近结果状态）；
4. 面板可见时定时刷新、隐藏即停；行内容没变不重建网格（不抢用户点击）；
5. 摘要写清刷新口径，不假装是实时推送。

同步手法：闸门 Event + 台账计数，不绑墙钟。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402

from mbdsdr_ai.agent import AgentConfig, MBDSDRAgent  # noqa: E402


@pytest.fixture(scope="module")
def qtapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app


class _GatedModel:
    """卡在闸门上的 model_manager：任务真在跑，但一个轮次都不往下走。"""

    def __init__(self):
        self.gate = threading.Event()
        self.rounds = 0
        self.lock = threading.Lock()

    def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
        with self.lock:
            self.rounds += 1
        self.gate.wait(10.0)
        return {"success": True, "content": "结论", "tool_calls": []}

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


class _ScriptedModel:
    """一轮就收尾的 model_manager（ok=False 模拟模型调用失败）。"""

    def __init__(self, ok=True):
        self.ok = ok

    def chat(self, messages=None, tools=None, tool_choice="auto", **kw):
        return {"success": self.ok,
                "content": "结论" if self.ok else "模型调用失败",
                "tool_calls": []}

    @staticmethod
    def parse_tool_calls_from_text(content, valid_names):
        return []


def _wait_for(pred, timeout=5.0, interval=0.01):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


@pytest.fixture
def agent():
    return MBDSDRAgent(AgentConfig())


@pytest.fixture
def drawer(qtapp, isolated_profile, agent):
    from capability_drawer import CapabilityDrawer
    host = types.SimpleNamespace(ai_panel=types.SimpleNamespace(agent=agent))
    d = CapabilityDrawer(host=host)
    yield d
    # 闸门一律放行，别让在跑线程拖到进程收尾。
    # 注意：诚实兜底那组用例会把 subagent_manager 换成替身，故这里按属性取，
    # 不能假定它一定是真 SubagentManager（否则 teardown 自己炸 = 探针噪声）。
    sm = getattr(agent, "subagent_manager", None)
    for sub in (getattr(sm, "subagents", {}) or {}).values():
        mm = getattr(sub, "model_manager", None)
        gate = getattr(mm, "gate", None)
        if gate is not None:
            gate.set()
    d.hide()
    d.close()
    d.deleteLater()
    qtapp.processEvents()


def _cell(drawer, title):
    """取某个能力位的副说明（按钮文本第二行）。找不到返回 None。"""
    from PySide6.QtWidgets import QToolButton
    for i in range(drawer._grid.count()):
        w = drawer._grid.itemAt(i).widget()
        if isinstance(w, QToolButton) and w.text().split("\n")[0] == title:
            parts = w.text().split("\n", 1)
            return parts[1] if len(parts) > 1 else ""
    return None


def _cells(drawer):
    from PySide6.QtWidgets import QToolButton
    out = []
    for i in range(drawer._grid.count()):
        w = drawer._grid.itemAt(i).widget()
        if isinstance(w, QToolButton):
            out.append(w)
    return out


def _single_slot(sm):
    """把并发上限压到 1（真限流 seam，#85），好造出排队任务。"""
    sm.max_concurrent = 1
    sm._slots = threading.BoundedSemaphore(1)


# ---------------------------------------------------------------------------
# 1. 子代理格：真数字，跟着真对象走
# ---------------------------------------------------------------------------
class TestSubagentCellIsLive:
    def test_zero_subagents_shows_real_zero(self, drawer, agent):
        note = _cell(drawer, "子代理")
        assert note is not None, "能力矩阵里没有子代理格"
        assert "0" in note and "子代理" in note
        assert "SubagentManager" not in note, "还在显示类名 = 死读数没修"

    def test_count_follows_real_objects(self, drawer, agent):
        sm = agent.subagent_manager
        sm.create("spectrum_analyzer", subagent_id="a", allowed_tools=[])
        sm.create("satellite_tracker", subagent_id="b", allowed_tools=[])
        drawer.refresh()
        assert "2" in _cell(drawer, "子代理")
        sm.create("baseband_recorder", subagent_id="c", allowed_tools=[])
        drawer.refresh()
        assert "3" in _cell(drawer, "子代理")

    def test_running_and_queued_are_visible(self, drawer, agent):
        sm = agent.subagent_manager
        _single_slot(sm)
        sm.create("spectrum_analyzer", subagent_id="hold", allowed_tools=[])
        holder = sm.get("hold")
        holder.model_manager = _GatedModel()
        sm.submit_task("hold", "占位任务")
        assert _wait_for(lambda: holder.model_manager.rounds == 1)
        sm.create("spectrum_analyzer", subagent_id="victim", allowed_tools=[])
        sm.get("victim").model_manager = _GatedModel()
        sm.submit_task("victim", "排队目标任务")
        assert _wait_for(lambda: len(sm.task_queue) == 1)
        try:
            drawer.refresh()
            note = _cell(drawer, "子代理")
            assert "在跑 1" in note, f"在跑数没上 UI: {note}"
            assert "排队 1" in note, f"排队数没上 UI（#85 台账白做）: {note}"
        finally:
            holder.model_manager.gate.set()
            sm.get("victim").model_manager.gate.set()

    def test_completed_and_failed_counts(self, drawer, agent):
        sm = agent.subagent_manager
        sm.create("spectrum_analyzer", subagent_id="ok", allowed_tools=[])
        sm.get("ok").model_manager = _ScriptedModel(ok=True)
        sm.execute_task("ok", "成功任务")
        sm.create("spectrum_analyzer", subagent_id="bad", allowed_tools=[])
        sm.get("bad").model_manager = _ScriptedModel(ok=False)
        sm.execute_task("bad", "失败任务")
        drawer.refresh()
        note = _cell(drawer, "子代理")
        assert "完成 1" in note, f"完成数没上 UI: {note}"
        assert "失败 1" in note, f"失败数没上 UI: {note}"


# ---------------------------------------------------------------------------
# 2. 另外四格：接的是真对象的统计，不是写死的文案
# ---------------------------------------------------------------------------
class _StatsStub:
    """只有 get_stats 的替身：数字刻意取不常见的值，证明读的是对象。"""

    def __init__(self, payload):
        self._payload = payload

    def get_stats(self):
        return dict(self._payload)


class TestOtherCellsAreLive:
    def test_workflow_cell(self, drawer, agent):
        agent.workflow_engine = _StatsStub(
            {"total_workflows": 7, "total_usage": 9, "total_success": 8})
        drawer.refresh()
        note = _cell(drawer, "工作流引擎")
        assert "7" in note and "9" in note, f"工作流格没读真统计: {note}"
        assert "WorkflowEngine" not in note

    def test_scheduler_cell(self, drawer, agent):
        agent.scheduler = _StatsStub(
            {"total_tasks": 4, "enabled_tasks": 3, "total_runs": 11})
        drawer.refresh()
        note = _cell(drawer, "调度器")
        assert "4" in note and "3" in note and "11" in note, \
            f"调度格没读真统计: {note}"

    def test_memory_cell(self, drawer, agent):
        agent.memory = _StatsStub({"total": 12})
        drawer.refresh()
        note = _cell(drawer, "长期记忆")
        assert "12" in note, f"记忆格没读真统计: {note}"

    def test_plugin_cell(self, drawer, agent):
        agent.plugin_manager = _StatsStub(
            {"total_plugins": 5, "enabled": 4, "errors": 1})
        drawer.refresh()
        note = _cell(drawer, "插件系统")
        assert "5" in note and "4" in note, f"插件格没读真统计: {note}"

    def test_real_agent_cells_all_show_numbers(self, drawer):
        """真内核冒烟：五格都必须有真数字，不许再退回类名。"""
        for title in ("插件系统", "子代理", "工作流引擎", "调度器", "长期记忆"):
            note = _cell(drawer, title)
            assert note is not None, f"缺能力位: {title}"
            assert any(ch.isdigit() for ch in note), f"{title} 仍是死读数: {note}"
            assert "常驻 ·" not in note, f"{title} 还在显示类名: {note}"


# ---------------------------------------------------------------------------
# 3. 诚实兜底：读不出来就说读不出来
# ---------------------------------------------------------------------------
class _BrokenStats:
    def get_stats(self):
        raise RuntimeError("统计源坏了")


class _NoStats:
    pass


class TestHonestFallbacks:
    def test_stats_exception_is_reported_not_faked(self, drawer, agent):
        agent.subagent_manager = _BrokenStats()
        drawer.refresh()
        note = _cell(drawer, "子代理")
        assert "统计不可用" in note, f"统计炸了却不说: {note}"
        assert not any(ch.isdigit() for ch in note), "读不出来还编数字"

    def test_object_without_stats_keeps_class_fallback(self, drawer, agent):
        agent.subagent_manager = _NoStats()
        drawer.refresh()
        note = _cell(drawer, "子代理")
        assert "常驻" in note and "_NoStats" in note

    def test_missing_object_says_not_loaded(self, drawer, agent):
        agent.subagent_manager = None
        drawer.refresh()
        assert _cell(drawer, "子代理") == "未装载"

    def test_no_agent_says_kernel_not_loaded(self, qtapp, isolated_profile):
        from capability_drawer import CapabilityDrawer
        host = types.SimpleNamespace(ai_panel=types.SimpleNamespace(agent=None))
        d = CapabilityDrawer(host=host)
        try:
            assert "未装载" in d._summary.text()
            assert _cells(d) == [], "内核没装载却摆出了占位能力位"
        finally:
            d.close()
            d.deleteLater()


# ---------------------------------------------------------------------------
# 4. 明细条：子代理真实任务行
# ---------------------------------------------------------------------------
class TestLiveSubagentRows:
    def test_queued_goal_appears(self, drawer, agent):
        sm = agent.subagent_manager
        _single_slot(sm)
        sm.create("spectrum_analyzer", subagent_id="hold", allowed_tools=[])
        holder = sm.get("hold")
        holder.model_manager = _GatedModel()
        sm.submit_task("hold", "占位任务")
        assert _wait_for(lambda: holder.model_manager.rounds == 1)
        sm.create("spectrum_analyzer", subagent_id="victim", allowed_tools=[])
        sm.get("victim").model_manager = _GatedModel()
        sm.submit_task("victim", "排队目标任务")
        assert _wait_for(lambda: len(sm.task_queue) == 1)
        try:
            drawer.refresh()
            note = drawer._live_note.text()
            assert "排队目标任务" in note, f"排队任务在明细条里看不到: {note}"
            assert "子代理" in note, "明细行没标来源，会和编排任务混淆"
        finally:
            holder.model_manager.gate.set()
            sm.get("victim").model_manager.gate.set()

    def test_finished_task_status_appears(self, drawer, agent):
        sm = agent.subagent_manager
        sm.create("spectrum_analyzer", subagent_id="ok", allowed_tools=[])
        sm.get("ok").model_manager = _ScriptedModel(ok=True)
        sm.execute_task("ok", "已完成的目标任务")
        drawer.refresh()
        note = drawer._live_note.text()
        assert "已完成的目标任务" in note
        assert "ok" in note, f"结果状态没写出来: {note}"

    def test_cancelled_task_status_appears(self, drawer, agent):
        sm = agent.subagent_manager
        sm.create("spectrum_analyzer", subagent_id="solo", allowed_tools=[])
        sub = sm.get("solo")
        sub.model_manager = _GatedModel()
        sm.submit_task("solo", "被取消的目标任务")
        assert _wait_for(lambda: sub.model_manager.rounds == 1)
        sm.cancel("solo")
        sub.model_manager.gate.set()
        assert _wait_for(lambda: len(sub.task_history) == 1)
        drawer.refresh()
        note = drawer._live_note.text()
        assert "被取消的目标任务" in note
        assert "cancelled" in note, f"取消态没如实显示: {note}"


# ---------------------------------------------------------------------------
# 5. 刷新口径：可见才刷、内容没变不重建、摘要说清楚
# ---------------------------------------------------------------------------
class TestRefreshCadence:
    def test_timer_only_runs_while_visible(self, drawer, qtapp):
        assert drawer._refresh_timer.isActive() is False, "隐藏时不该定时刷新"
        drawer.show()
        qtapp.processEvents()
        try:
            assert drawer._refresh_timer.isActive() is True, "可见时不刷新 = 陈旧快照"
        finally:
            drawer.hide()
            qtapp.processEvents()
        assert drawer._refresh_timer.isActive() is False, "隐藏后定时器没停"

    def test_unchanged_rows_do_not_rebuild_grid(self, drawer):
        before = _cells(drawer)
        assert before
        drawer.refresh()
        after = _cells(drawer)
        assert len(after) == len(before)
        for a, b in zip(after, before):
            assert a is b, "内容没变却重建了网格：会抢走用户正在点的按钮"

    def test_changed_rows_update_text(self, drawer, agent):
        agent.subagent_manager.create("spectrum_analyzer", subagent_id="a",
                                      allowed_tools=[])
        drawer.refresh()
        assert "1" in _cell(drawer, "子代理")

    def test_summary_states_refresh_cadence(self, drawer):
        assert "刷新" in drawer._summary.text(), "摘要没写清刷新口径"


# ---------------------------------------------------------------------------
# 6. 几何收口（#92）：不横溢、不裁字、拖拽不重读事实源
# ---------------------------------------------------------------------------
class TestGridGeometry:
    """缺陷原状（A/B 探针真字体实测）：写死两列 + 按钮最小宽跟着整串文字走，
    340×240 默认面板里内层被撑到 344px 而视口只有 292px，82 格全被裁、还要
    横滚才看得全右列。修后列数/断行/格高都按真实像素装箱。

    断言全是**相对量**（运行时字体度量 vs 实际格宽/格高），不钉绝对像素：
    换字体、换 DPI 不该假红；绝对像素的证据留在仓库外探针与截图里。
    """

    @staticmethod
    def _settle(drawer, qtapp):
        for _ in range(3):
            qtapp.processEvents()
        drawer._inner.layout().activate()
        qtapp.processEvents()

    def _show_at(self, drawer, qtapp, w, h):
        drawer.resize(w, h)
        drawer.show()
        self._settle(drawer, qtapp)
        drawer.refresh()
        self._settle(drawer, qtapp)
        drawer._refresh_timer.stop()   # 几何用例不让定时器插一脚

    def test_no_horizontal_overflow(self, drawer, qtapp):
        try:
            for w, h in ((340, 240), (520, 420), (300, 300)):
                self._show_at(drawer, qtapp, w, h)
                vp = drawer._scroll.viewport()
                assert drawer._inner.width() <= vp.width(), \
                    f"{w}x{h} 内层比视口宽：{drawer._inner.width()} > {vp.width()}"
                assert drawer._scroll.horizontalScrollBar().maximum() == 0, \
                    f"{w}x{h} 出现横滚条 = 半个矩阵要横滚才看得见"
        finally:
            drawer.hide()
            qtapp.processEvents()

    def test_no_cell_clipped(self, drawer, qtapp):
        from PySide6.QtGui import QFont, QFontMetrics
        try:
            for w, h in ((340, 240), (520, 420)):
                self._show_at(drawer, qtapp, w, h)
                for btn in _cells(drawer):
                    lines = btn.text().split("\n")
                    fm = QFontMetrics(btn.font())
                    f11 = QFont(btn.font())
                    f11.setPixelSize(drawer.CELL_FONT_PX)
                    fm11 = QFontMetrics(f11)
                    need_w = max(max(fm.horizontalAdvance(s),
                                     fm11.horizontalAdvance(s)) for s in lines)
                    need_h = max(fm.lineSpacing(),
                                 fm11.lineSpacing()) * len(lines)
                    assert need_w <= btn.width() - drawer.CELL_CHROME_H, \
                        f"{w}x{h} 裁字（宽）: {btn.text()!r} 需 {need_w} " \
                        f"格宽 {btn.width()}"
                    assert need_h <= btn.height() - drawer.CELL_CHROME_V, \
                        f"{w}x{h} 裁字（高）: {btn.text()!r} 需 {need_h} " \
                        f"格高 {btn.height()}"
        finally:
            drawer.hide()
            qtapp.processEvents()

    def test_height_only_resize_keeps_buttons(self, drawer, qtapp):
        try:
            self._show_at(drawer, qtapp, 340, 240)
            before = _cells(drawer)
            assert before
            drawer.resize(340, 420)      # 只改高度：排版方案不该变
            self._settle(drawer, qtapp)
            after = _cells(drawer)
            assert len(after) == len(before)
            for a, b in zip(after, before):
                assert a is b, "只改高度却重建了网格：会抢走用户正在点的按钮"
        finally:
            drawer.hide()
            qtapp.processEvents()

    def test_resize_does_not_reread_facts(self, drawer, qtapp, monkeypatch):
        try:
            self._show_at(drawer, qtapp, 340, 240)
            calls = []

            def _boom(*a, **k):
                calls.append(1)
                return [], ""

            monkeypatch.setattr(drawer, "_collect", _boom)
            drawer.resize(520, 420)
            self._settle(drawer, qtapp)
            assert calls == [], "拖拽改宽度重读了事实源（每拍几百个工具）"
            assert _cells(drawer), "重排把格子弄丢了"
        finally:
            drawer.hide()
            qtapp.processEvents()