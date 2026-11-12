# SPDX-License-Identifier: MIT
"""能力矩阵（对齐原型 ``#capDrawer``「能力矩阵（原仓库工具域）」）。

用户 2026-11-08：「AI 助手，我说叫你学通用 Agent，你是一点没学来」
「人工智能编排有没有？包括 Hooks，包括常驻化，包括我们作为一个 Agent」。

本面板只读**本进程真实装载的对象**，不写死任何示例能力名、不编造数量：

* ``Agent.tool_registry.list_tools()``  → 按 category 聚合真实工具域；
* ``Agent.hook_manager.list_hooks()``   → 事件钩子条数与历史；
* ``Agent.orchestrator.get_stats()``    → 编排任务统计；
* ``Agent.plugin_manager`` / ``subagent_manager`` / ``workflow_engine`` /
  ``scheduler`` / ``memory`` → 有就是有，没有如实写「未装载」。

交互只做一件事：点某个能力位，把名字送进 AI 输入栏。**不预制向导、
不自动执行** —— 能力而非流程，是这套 UI 的第一原则。
"""
from __future__ import annotations

from typing import Any, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QScrollArea, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget,
)


class CapabilityDrawer(QWidget):
    """只读事实源的能力位网格。"""

    capability_activated = Signal(str)

    def __init__(self, host: Optional[Any] = None, parent=None):
        super().__init__(parent)
        self._host = host
        self.setObjectName("capabilityDrawer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # 岛内单元格的边框是它**唯一**的可见轮廓（单元格填充 0.045 白叠在
        # 面板上只有 1.13:1），所以按 WCAG 1.4.11 对底 ≥3:1：0.18 → 0.50
        # （合成 #687280，对面板 3.39 / 对单元格自身填充 3.18），悬停 0.70
        # （4.85）。面板底固定 rgba(14,17,24,0.94)，最坏（浅色 app 底）合成
        # #1C1F25，见 tests/test_ui_contrast.py 的同名门禁。
        self.setStyleSheet(
            "background-color: rgba(14,17,24,0.94);"
            "QScrollArea { background: transparent; }"
            "QScrollArea > QWidget > QWidget { background: transparent; }"
            "QToolButton#capCell { text-align: left; padding: 8px 10px;"
            " background-color: rgba(255,255,255,0.045);"
            " color: #E4EAF4; border: 1px solid rgba(180,196,220,0.50);"
            " border-radius: 10px; font-size: 11px; }"
            "QToolButton#capCell:hover { background-color: rgba(255,255,255,0.12);"
            " border-color: rgba(180,196,220,0.70); }"
            "QLabel#capHint { color: #9AA8BC; font-size: 10px; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)
        self._summary = QLabel("")
        self._summary.setObjectName("capHint")
        self._summary.setWordWrap(True)
        root.addWidget(self._summary)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._inner = QWidget()
        self._grid = QGridLayout(self._inner)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._scroll.setWidget(self._inner)
        root.addWidget(self._scroll, 1)
        # 任务与事件明细：常驻可读，数字来自编排器/Hooks 的真实对象
        self._live_note = QLabel("")
        self._live_note.setObjectName("capHint")
        self._live_note.setWordWrap(True)
        self._live_note.setMinimumHeight(28)
        root.addWidget(self._live_note)
        self.refresh()

    # ---------------------------------------------------------------- 事实源
    def _agent(self):
        panel = getattr(self._host, "ai_panel", None)
        return getattr(panel, "agent", None)

    def _collect(self):
        """返回 [(能力名, 副说明)]；全部来自运行时真实对象。"""
        agent = self._agent()
        rows = []
        if agent is None:
            return rows, "AI 内核未装载；不显示占位能力（拒绝示例值）。"

        tr = getattr(agent, "tool_registry", None)
        if tr is not None:
            try:
                tools = tr.list_tools(include_unavailable=True)
            except Exception:
                tools = []
            cats = {}
            for t in tools:
                cat = str(t.get("category") or "未分类")
                cats[cat] = cats.get(cat, 0) + 1
            for cat in sorted(cats):
                rows.append((str(cat), "真实注册 %d 个工具" % cats[cat]))
        else:
            rows.append(("工具注册表", "未装载"))

        hm = getattr(agent, "hook_manager", None)
        if hm is not None:
            try:
                hooks = hm.list_hooks()
                hist = hm.get_history(limit=1000)
                try:
                    from mbdsdr_ai.hooks import EventType
                    kinds = len(list(EventType))
                except Exception:
                    kinds = 0
                rows.append(("Hooks 事件总线",
                             "%d 条钩子 · %d 类事件 · 最近 %d 条"
                             % (len(hooks), kinds, len(hist))))
            except Exception:
                rows.append(("Hooks 事件钩子", "已装载，统计不可用"))
        orch = getattr(agent, "orchestrator", None)
        if orch is not None:
            try:
                st = orch.get_stats() or {}
                total = st.get("total_tasks", 0)
                par = st.get("max_parallel", 0)
                rows.append(("智能编排",
                             "%s 个任务 · 并发上限 %s" % (total, par)))
            except Exception:
                rows.append(("智能编排", "已装载"))

        # 本地能力协商（无 API Key 时的真实工具路由）+ 只读知识源
        panel = getattr(self._host, "ai_panel", None)
        bridge = panel._bridge_obj() if panel is not None and hasattr(
            panel, "_bridge_obj") else None
        if bridge is not None:
            try:
                st = bridge.live_state()
                if not st.get("model"):
                    rows.append(("本地能力协商",
                                 "模型未配置时仍可执行 %s 个可用工具"
                                 % st.get("tools_available")))
                rows.append(("星名文化（只读）",
                             "亮星 %s 条 · 星座 %s 条 · 仓库数据"
                             % (st.get("star_culture"),
                                st.get("constellation_culture"))))
            except Exception:
                rows.append(("本地能力协商", "状态读取失败"))

        for attr, title in (("plugin_manager", "插件系统"),
                            ("subagent_manager", "子代理"),
                            ("workflow_engine", "工作流引擎"),
                            ("scheduler", "调度器"),
                            ("memory", "长期记忆")):
            obj = getattr(agent, attr, None)
            if obj is None:
                rows.append((title, "未装载"))
            else:
                rows.append((title, "常驻 · %s" % type(obj).__name__))
        return rows, ("事实源自动生成 · 共 %d 个能力位 · 点一下把名字送进 AI 输入栏"
                      % len(rows))

    # ---------------------------------------------------------------- 渲染
    def refresh(self) -> None:
        rows, summary = self._collect()
        self._summary.setText(summary)
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                # 不 deleteLater()：父窗口析构时队列里的按钮可能已被 C++
                # 侧先删，Python 覆盖层再摸它就抛 "already deleted"。
                w.hide()
                w.setParent(None)
        for i, (name, note) in enumerate(rows):
            btn = QToolButton()
            btn.setObjectName("capCell")
            btn.setText(name + "\n" + note)
            btn.setToolTip("把「%s」送进 AI 输入栏（不会自动执行）" % name)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumHeight(42)
            btn.clicked.connect(
                lambda _c=False, n=name: self.capability_activated.emit(n))
            self._grid.addWidget(btn, i // 2, i % 2)
        if not rows:
            empty = QLabel("没有可显示的能力位。")
            empty.setObjectName("capHint")
            self._grid.addWidget(empty, 0, 0)
        # 明细条：任务与事件只写真实记录，没有就写「暂无」
        lines = []
        panel = getattr(self._host, "ai_panel", None)
        bridge = panel._bridge_obj() if panel is not None and hasattr(
            panel, "_bridge_obj") else None
        if bridge is not None:
            try:
                st = bridge.live_state()
                for t in (st.get("task_rows") or [])[-3:]:
                    lines.append("任务 %s · %s · %s" %
                                 (t.get("id", ""), t.get("name", ""),
                                  t.get("status", "")))
                for e in (st.get("event_rows") or [])[-3:]:
                    lines.append("事件 %s · %s" %
                                 (e.get("event_type", ""),
                                  (e.get("data") or {}).get("tool", "")))
            except Exception:
                lines = []
        self._live_note.setText(
            "任务与事件（真实记录）<br>" + "<br>".join(lines)
            if lines else "任务与事件（真实记录）<br>暂无任务与事件")
