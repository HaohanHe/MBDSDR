# SPDX-License-Identifier: MIT
"""能力矩阵（对齐原型 ``#capDrawer``「能力矩阵（原仓库工具域）」）。

用户 2026-11-08：「AI 助手，我说叫你学通用 Agent，你是一点没学来」
「人工智能编排有没有？包括 Hooks，包括常驻化，包括我们作为一个 Agent」。

本面板只读**本进程真实装载的对象**，不写死任何示例能力名、不编造数量：

* ``Agent.tool_registry.list_tools()``  → 按 category 聚合真实工具域；
* ``Agent.hook_manager.list_hooks()``   → 事件钩子条数与历史；
* ``Agent.orchestrator.get_stats()``    → 编排任务统计；
* ``Agent.plugin_manager`` / ``subagent_manager`` / ``workflow_engine`` /
  ``scheduler`` / ``memory`` → 读各自 ``get_stats()`` 的**真实数字**（#92：
  子代理格含在跑/排队/完成/失败，排队台账来自 #85）；没有该对象如实写
  「未装载」，统计读不出来写「统计不可用」，没有 get_stats 才退回类名。

明细条另给出子代理真实任务行（排队目标 / 最近结果状态）。
面板**可见时**每 ``REFRESH_INTERVAL_MS`` 重读一次事实源，隐藏即停；内容没变
不重建网格（不抢用户正在点的按钮）。

排版按真实像素装箱（#92）：列数由最宽的一个统计项决定（放不下两列就一列），
副说明按当前格宽在「·」项之间断行，格高按真实行数 × 真实行高给。写死两列 +
按钮最小宽跟着整串文字走，会把内层撑到视口两倍宽（HEAD 实测 580 vs 290），
半个矩阵要横滚才看得见——「常驻可读」就成了空话。

交互只做一件事：点某个能力位，把名字送进 AI 输入栏。**不预制向导、
不自动执行** —— 能力而非流程，是这套 UI 的第一原则。
"""
from __future__ import annotations

from html import escape
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QScrollArea, QSizePolicy, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

#: 五个常驻对象 → 能力位标题 + 统计字段渲染表（#92）。
#:
#: 缺陷原状：这五格只写「常驻 · ClassName」，而五个对象**都有 get_stats()**
#: （子代理那份还含 running_subagents / queued_tasks / 完成失败数）。面板文档
#: 承诺「只读真实装载的对象、不编造数量」，工具域/Hooks/编排三格确实给了真
#: 数字，这五格却是死读数：#85 排队台账、#87 取消、#90 回收做出来的可观测性
#: 在展示层等于没有。
#:
#: 渲染表只列**该对象真有的字段**；字段缺失就少写一项，绝不补默认数字。
#: 每项是 (stats 键, 文案模板)，模板里只有一个 %s。
_STATS_FIELDS = {
    "plugin_manager": ("插件系统", (
        ("total_plugins", "%s 个插件"), ("enabled", "启用 %s"),
        ("errors", "错误 %s"))),
    "subagent_manager": ("子代理", (
        ("total_subagents", "%s 个子代理"), ("running_subagents", "在跑 %s"),
        ("queued_tasks", "排队 %s"), ("total_tasks_completed", "完成 %s"),
        ("total_tasks_failed", "失败 %s"))),
    "workflow_engine": ("工作流引擎", (
        ("total_workflows", "%s 个工作流"), ("total_usage", "执行 %s 次"),
        ("total_success", "成功 %s"))),
    "scheduler": ("调度器", (
        ("total_tasks", "%s 个定时任务"), ("enabled_tasks", "启用 %s"),
        ("total_runs", "已跑 %s 次"))),
    "memory": ("长期记忆", (
        ("total", "%s 条记忆"),)),
}

#: 每格副说明的**优先分组**（不是硬上限）：先按这里的刀口断成两组，再由
#: ``_wrap_note`` 按当前格宽把「·」项逐行装箱。面板默认只有 340px 宽
#: （main_window._build_capability_drawer），两列网格每格约 140px，11px 字体
#: 下一行放不下十几个汉字以上的长串；写死两行会在数字变大时横向溢出
#: （#92 几何探针实测：HEAD 内层 580px vs 视口 290px，一半矩阵要横滚才看得见）。
_STATS_LINE_BREAK = {
    "subagent_manager": 2,    # 「N 个子代理 · 在跑 M」/「排队 Q · 完成 C · 失败 F」
    "workflow_engine": 1,     # 「N 个工作流」/「执行 U 次 · 成功 S」
    "scheduler": 2,           # 「N 个定时任务 · 启用 E」/「已跑 R 次」
    "plugin_manager": 2,      # 「N 个插件 · 启用 E」/「错误 X」
}


class _CellMetrics:
    """单元格文字度量：QSS 字号与控件继承字体，取**更严**的那个。

    QSS 的 ``font-size: 11px`` 改的是渲染字体，``widget.font()`` 仍可能是
    app 字体（offscreen 实测同一串行高 13 vs 14）。只按其中一个算就会裁字，
    所以宽度取两者较大值、行高也取较大值：宁可多留一点空白，不裁事实。
    """

    def __init__(self, widget_font: QFont, cell_px: int):
        qss_font = QFont(widget_font)
        qss_font.setPixelSize(cell_px)
        self._widget = QFontMetrics(widget_font)
        self._qss = QFontMetrics(qss_font)
        self.line_height = max(self._widget.lineSpacing(),
                               self._qss.lineSpacing())

    def advance(self, text: str) -> int:
        return max(self._widget.horizontalAdvance(text),
                   self._qss.horizontalAdvance(text))


class CapabilityDrawer(QWidget):
    """只读事实源的能力位网格。"""

    capability_activated = Signal(str)

    #: 可见时的刷新周期（#92）。摘要文案直接引这个数，口径不会和实际漂移。
    REFRESH_INTERVAL_MS = 2000

    #: 单元格几何常量：**QSS 与排版计算共用同一份数字**。
    #: 之前 QSS 写死 11px / padding 8px 10px，而高度估算另写一套魔数，
    #: 两边一漂移就裁字（#92 探针实测 2 行格差 2px）。改这里 = 改渲染。
    CELL_FONT_PX = 11
    CELL_PAD_H_PX = 10
    CELL_PAD_V_PX = 8
    CELL_BORDER_PX = 1
    CELL_RADIUS_PX = 10
    GRID_SPACING_PX = 6
    ROOT_MARGIN_H_PX = 10
    #: 文字之外的横向/纵向占用（左右 padding + 左右边框，上下同理）
    CELL_CHROME_H = 2 * (CELL_PAD_H_PX + CELL_BORDER_PX)
    CELL_CHROME_V = 2 * (CELL_PAD_V_PX + CELL_BORDER_PX)

    def __init__(self, host: Optional[Any] = None, parent=None):
        super().__init__(parent)
        self._host = host
        self._rows_sig = None      # 上一次的能力位内容；没变就不重建网格
        self._layout_sig = None    # 上一次的排版方案；宽度变了要重排（#92）
        self._rows_cache = None    # 最近一次事实源快照；resize 重排只读它
        self._grid_cols = 0        # 上一次重建实际用的列数
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
            f"QToolButton#capCell {{ text-align: left;"
            f" padding: {self.CELL_PAD_V_PX}px {self.CELL_PAD_H_PX}px;"
            " background-color: rgba(255,255,255,0.045);"
            f" color: #E4EAF4;"
            f" border: {self.CELL_BORDER_PX}px solid rgba(180,196,220,0.50);"
            f" border-radius: {self.CELL_RADIUS_PX}px;"
            f" font-size: {self.CELL_FONT_PX}px; }}"
            "QToolButton#capCell:hover { background-color: rgba(255,255,255,0.12);"
            " border-color: rgba(180,196,220,0.70); }"
            "QLabel#capHint { color: #9AA8BC; font-size: 10px; }")

        root = QVBoxLayout(self)
        root.setContentsMargins(self.ROOT_MARGIN_H_PX, 8,
                                self.ROOT_MARGIN_H_PX, 10)
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
        self._grid.setSpacing(self.GRID_SPACING_PX)
        self._scroll.setWidget(self._inner)
        root.addWidget(self._scroll, 1)
        # 任务与事件明细：常驻可读，数字来自编排器/Hooks 的真实对象
        self._live_note = QLabel("")
        self._live_note.setObjectName("capHint")
        self._live_note.setWordWrap(True)
        self._live_note.setMinimumHeight(28)
        root.addWidget(self._live_note)

        # 可见时才定时刷新（#92）：打开时取一次真值，之后每 REFRESH_INTERVAL_MS
        # 重读一遍事实源；隐藏即停，不给后台留一个空转的定时器。
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(self.REFRESH_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------ 可见性
    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._refresh_timer.start()

    def hideEvent(self, event):
        self._refresh_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        """宽度变了就重排（#92）：列数与每格行宽都是按当前视口算出来的。

        不重排的话，用户把面板拉宽/收窄后，格子还按旧宽度断行——要么右边
        空一大块，要么文字被裁。这里**只用上一次的事实缓存**重算排版，不
        重读内核：拖拽一秒几十拍，每拍都 list_tools() 几百个工具会把界面
        拖卡。``_apply`` 比签名，宽度没变时一个按钮都不动。
        """
        super().resizeEvent(event)
        if getattr(self, "_scroll", None) is not None \
                and getattr(self, "_rows_cache", None) is not None:
            self._apply(self._rows_cache)

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

        for attr, (title, fields) in _STATS_FIELDS.items():
            obj = getattr(agent, attr, None)
            if obj is None:
                rows.append((title, "未装载"))
                continue
            rows.append((title, self._stats_note(obj, attr, fields)))
        return rows, ("事实源自动生成 · 共 %d 个能力位 · 可见时每 %d 秒刷新"
                      " · 点一下把名字送进 AI 输入栏"
                      % (len(rows), self.REFRESH_INTERVAL_MS // 1000))

    @staticmethod
    def _stats_note(obj: Any, attr: str, fields) -> str:
        """把一个常驻对象的真实统计渲染成副说明（#92）。

        三档诚实兜底，绝不编数字：
        * 没有 ``get_stats`` → 保留原来的「常驻 · ClassName」（只知道它活着）；
        * ``get_stats`` 抛异常 → 「已装载，统计不可用」（不拿旧值/默认值顶）；
        * 统计里缺字段 → 少写一项，不补 0。
        """
        getter = getattr(obj, "get_stats", None)
        if not callable(getter):
            return "常驻 · %s" % type(obj).__name__
        try:
            st = getter() or {}
        except Exception:
            return "已装载，统计不可用"
        bits = [tpl % st[key] for key, tpl in fields if key in st]
        if not bits:
            return "已装载 · %s（统计为空）" % type(obj).__name__
        cut = _STATS_LINE_BREAK.get(attr, 0)
        if cut and len(bits) > cut:
            return " · ".join(bits[:cut]) + "\n" + " · ".join(bits[cut:])
        return " · ".join(bits)

    # ---------------------------------------------------------------- 渲染
    def refresh(self) -> None:
        """重读事实源。**内容与排版都没变就不重建网格**（#92）。

        可见时每 2 秒会被定时器调一次；若每次都把按钮全删全建，用户正要点
        的那一格会在指尖下消失（点击落到新建按钮上 = 抢点击）。故先比内容
        签名，只有真变了才重建；明细条是纯文本，每次都更新。

        排版签名（列数 / 每格宽 / 行宽预算）另比一次：面板被拉宽收窄时内容
        一个字没变，但断行方案必须跟着变，否则要么裁字要么右边空一大块。
        """
        rows, summary = self._collect()
        self._summary.setText(summary)
        self._rows_cache = list(rows)
        self._apply(rows)
        self._refresh_live_note()

    def _apply(self, rows) -> None:
        """内容或排版签名变了才动网格；拖拽改宽度走这里，不重读事实源。"""
        plan = self._plan_grid(rows)
        if rows == self._rows_sig and plan == self._layout_sig:
            return
        self._rows_sig = list(rows)
        self._layout_sig = plan
        self._rebuild_grid(rows, plan)

    # ---- 排版度量（#92：按真实像素装箱，不靠「大概两行」的猜测）----
    def _cell_metrics(self) -> "_CellMetrics":
        return _CellMetrics(self.font(), self.CELL_FONT_PX)

    def _avail_width(self) -> int:
        """网格可用的横向像素：视口宽 **减去竖滚动条**。

        滚动条宽度**总是**预留（不管此刻可不可见）：内容一多竖条就冒出来，
        视口随之收窄十几像素；若按「当前有没有竖条」算预算，就会出现
        「排版 → 竖条出现 → 视口变窄 → 横向溢出」的抖动。预留后方案与竖条
        状态无关，一次算准。
        """
        vp = self._scroll.viewport().width()
        if vp <= 0:                      # 还没布局（构造期）：退回自身宽度
            vp = self.width() - 2 * self.ROOT_MARGIN_H_PX
        sb = self._scroll.verticalScrollBar()
        reserve = max(sb.sizeHint().width(),
                      self.style().pixelMetric(QStyle.PM_ScrollBarExtent,
                                               None, self._scroll))
        return max(vp - reserve, 0)

    def _plan_grid(self, rows):
        """算 (列数, 每格宽, 行宽预算, 最宽单项所需格宽)。

        列数由**内容**决定，不是写死 2：两列放得下最宽的一个「·」项才用两列，
        否则退一列。此前写死两列 + 按钮最小宽 = 整串文字宽，于是内层被撑到
        580px 而视口只有 290px（HEAD 既有缺陷，#92 A/B 探针实测），半个矩阵
        要横滚才看得见——这与「常驻可读」的目的正好相反。
        """
        m = self._cell_metrics()
        avail = self._avail_width()
        need = 0
        for name, note in rows:
            need = max(need, m.advance(name))
            for item in self._note_items(note):
                need = max(need, m.advance(item))
        need += self.CELL_CHROME_H
        cols = 2 if (2 * need + self.GRID_SPACING_PX) <= avail else 1
        cell_w = max((avail - self.GRID_SPACING_PX * (cols - 1)) // cols, 0)
        budget = max(cell_w - self.CELL_CHROME_H, m.advance("0"))
        return cols, cell_w, budget, need

    @staticmethod
    def _note_items(note: str):
        """把副说明拆成不可再分的最小项（「·」分隔的每一项）。

        拆项只在项与项之间下刀，**绝不切进一个项内部**：数字和它的量词
        （「已跑 6385 次」）必须留在同一行，否则读出来是另一个意思。
        """
        items = []
        for group in note.split("\n"):
            items.extend(it for it in group.split(" · ") if it)
        return items

    @staticmethod
    def _wrap_note(note: str, budget: int, m: "_CellMetrics"):
        """按行宽预算把「·」项装箱成若干行；优先分组（_STATS_LINE_BREAK）先断。

        单项本身就超预算时让它独占一行：这时 ``_plan_grid`` 已经退到一列，
        仍放不下则由 ``_rebuild_grid`` 保留自然宽度出横滚条——宁可让用户
        滚动，也不把事实裁掉一半。
        """
        if not note:
            return []
        lines = []
        for group in note.split("\n"):
            items = [it for it in group.split(" · ") if it]
            if not items:
                continue
            cur = ""
            for it in items:
                cand = it if not cur else cur + " · " + it
                if cur and m.advance(cand) > budget:
                    lines.append(cur)
                    cur = it
                else:
                    cur = cand
            lines.append(cur)
        return lines

    def _rebuild_grid(self, rows, plan) -> None:
        cols, cell_w, budget, need = plan
        m = self._cell_metrics()
        wrapped = [self._wrap_note(note, budget, m) for _n, note in rows]
        texts = ["\n".join([name] + lines)
                 for (name, _note), lines in zip(rows, wrapped)]
        # 显式最小宽 = 排版算出的格宽：QToolButton 的 minimumSizeHint 跟着
        # 文字走，不压住它，网格就会把内层撑到比视口宽（横溢的根因）。
        # need > 格宽时保留 need（单项超宽的极端情况）→ 出横滚条而不是裁字。
        min_w = max(cell_w, need)
        heights = [self.CELL_CHROME_V + m.line_height * (1 + len(lines))
                   for lines in wrapped]
        old = [self._grid.itemAt(i).widget()
               for i in range(self._grid.count())]
        old = [w for w in old if isinstance(w, QToolButton)]
        # 断行结果与列数都没变：只更新最小宽/高（视口变了），不删建按钮——
        # 拖拽改宽度时用户指尖下的格子必须还是同一个对象。
        if rows and len(old) == len(rows) and cols == self._grid_cols \
                and all(w.text() == t for w, t in zip(old, texts)):
            for w, h in zip(old, heights):
                w.setMinimumWidth(min_w)
                w.setMinimumHeight(h)
            return
        while self._grid.count():
            item = self._grid.takeAt(0)
            w = item.widget()
            if w is not None:
                # 不 deleteLater()：父窗口析构时队列里的按钮可能已被 C++
                # 侧先删，Python 覆盖层再摸它就抛 "already deleted"。
                w.hide()
                w.setParent(None)
        self._grid_cols = cols
        for i, (name, _note) in enumerate(rows):
            btn = QToolButton()
            btn.setObjectName("capCell")
            btn.setText(texts[i])
            btn.setToolTip("把这个能力位送进 AI 输入栏（不会自动执行）")
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setMinimumWidth(min_w)
            # 高度按**真实行数 × 真实行高**给（名字一行 + 副说明若干行），
            # 行高取 QSS 字号与继承字体的较大值，别裁字。
            btn.setMinimumHeight(heights[i])
            btn.clicked.connect(
                lambda _c=False, n=name: self.capability_activated.emit(n))
            self._grid.addWidget(btn, i // cols, i % cols)
        if not rows:
            empty = QLabel("没有可显示的能力位。")
            empty.setObjectName("capHint")
            self._grid.addWidget(empty, 0, 0)

    def _subagent_lines(self):
        """子代理真实任务行（#92）：排队的 + 最近收尾的，都来自真台账。

        排队看 ``manager.task_queue``（#85），收尾看各子代理的 ``task_history``
        与 ``result.status``（ok / error / timeout / truncated / cancelled 如实
        写，不翻译成「成功」以外的模糊词）。最多 3 行，免得把 340×240 的
        面板挤满。
        """
        agent = self._agent()
        sm = getattr(agent, "subagent_manager", None) if agent is not None else None
        if sm is None:
            return []
        out = []
        try:
            for t in list(getattr(sm, "task_queue", []) or [])[-2:]:
                out.append("子代理 %s · 排队 · %s"
                           % (escape(str(getattr(t, "subagent_id", ""))),
                              escape(str(getattr(t, "goal", "")))))
            done = []
            for sub in (getattr(sm, "subagents", {}) or {}).values():
                for t in getattr(sub, "task_history", []) or []:
                    res = getattr(t, "result", None)
                    status = getattr(res, "status", "") or getattr(
                        getattr(t, "status", ""), "value", "")
                    done.append((getattr(t, "completed_at", 0.0) or 0.0,
                                 getattr(sub, "subagent_id", ""), status,
                                 getattr(t, "goal", "")))
            done.sort(key=lambda row: row[0])
            for _, sid, status, goal in done[-2:]:
                out.append("子代理 %s · %s · %s"
                           % (escape(str(sid)), escape(str(status)),
                              escape(str(goal))))
        except Exception:
            return []
        return out[-3:]

    def _refresh_live_note(self) -> None:
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
        lines.extend(self._subagent_lines())
        self._live_note.setText(
            "任务与事件（真实记录）<br>" + "<br>".join(lines)
            if lines else "任务与事件（真实记录）<br>暂无任务与事件")
