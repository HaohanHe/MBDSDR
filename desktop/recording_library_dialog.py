# SPDX-License-Identifier: MIT
"""录音库对话框（M4）：列出 / 回放 / 移入回收站。

## 为什么需要它

在此之前桌面只有「录音」与「回放」两颗按钮：回放要用户在文件框里自己找
路径，录过什么、哪段是中止的半成品、文件还在不在，全靠记忆。
``mbdsdr_ai/recording_library.py`` 早就把索引/核对/回收站写好了，但
desktop 里零引用（``PRD-现状对照.md`` §2 第 19 行）。

## 诚实边界

* **删除 = 移入 ``_trash/``**，不是物理删除；对话框明确写出这一点。
* 文件已不存在的条目照样列出并标「文件不存在」，回放按钮置灰并说明原因。
* 空目录/目录不存在时给出人话，不显示伪造的示例条目。
* 首次打开若还没有 ``library.json``，会调用 ``reconcile(apply=True)``
  把磁盘上的录音纳入索引——只写索引与留痕，**不移动/不删除任何录音文件**。
* 回放只把路径交给主窗口的现有回放链（``_replay_selected_path``），
  本对话框不读 IQ、不造数据。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QDesktopServices  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

_STATE_ZH = {
    "ok": "正常",
    "incomplete": "未完成",
    "truncated": "被截断",
    "corrupt": "疑似损坏",
    "missing": "文件不存在",
    "duplicate": "重复条目",
    "trashed": "已移入回收站",
}

#: 这些状态仍可回放（文件在，只是可能不完整）；missing/duplicate 不在此列。
_REPLAYABLE = ("ok", "incomplete", "truncated")


def state_text(state: str) -> str:
    return _STATE_ZH.get(str(state or ""), str(state or "未知"))


def format_duration(seconds) -> str:
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return "—"
    if value < 0 or value != value or value == float("inf"):
        return "—"
    if value >= 3600:
        return f"{value / 3600:.1f} h"
    if value >= 60:
        return f"{value / 60:.1f} min"
    if value < 1:
        # 短录音按 ms 报，避免 0.004 s 被四舍五入成「0.0 s」看起来像空文件
        return f"{value * 1000:.0f} ms"
    return f"{value:.1f} s"


def format_size(size_bytes) -> str:
    try:
        value = int(size_bytes)
    except (TypeError, ValueError):
        return "—"
    if value < 0:
        return "—"
    for unit, scale in (("GB", 1 << 30), ("MB", 1 << 20), ("KB", 1 << 10)):
        if value >= scale:
            return f"{value / scale:.1f} {unit}"
    return f"{value} B"


def _start_time_text(entry: dict) -> str:
    raw = str(entry.get("start_time") or "").strip()
    if not raw:
        return "—"
    return raw.replace("T", " ")[:19]


class RecordingLibraryDialog(QDialog):
    """录音库列表窗口；回放请求只发信号，由主窗口执行。"""

    replay_requested = Signal(str)

    def __init__(self, root: str, parent=None):
        super().__init__(parent)
        self._root = str(root)
        self._entries: list[dict] = []
        self.setWindowTitle("录音库")
        self.resize(940, 540)
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        self.path_label = QLabel(self._root)
        self.path_label.setWordWrap(True)
        self.path_label.setStyleSheet("color:#5B7B8C;font-size:11px;")
        outer.addWidget(self.path_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["文件", "开始时间", "时长", "大小", "状态", "位置"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        outer.addWidget(self.table, 1)

        self.empty_label = QLabel(
            "这个目录里还没有可列出的录音。\n"
            "在主窗口录一段后会出现在这里；已移入回收站的条目不显示。")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color:#8A8A8A;")
        outer.addWidget(self.empty_label)

        row = QHBoxLayout()
        self.refresh_btn = QPushButton("刷新 / 核对磁盘")
        self.refresh_btn.setToolTip(
            "重新读取索引并与磁盘核对：新文件纳入、丢文件标出；"
            "只写索引与留痕，不移动/不删除录音")
        self.refresh_btn.clicked.connect(self.refresh)
        row.addWidget(self.refresh_btn)

        self.replay_btn = QPushButton("回放到主窗口")
        self.replay_btn.setEnabled(False)
        self.replay_btn.clicked.connect(self._on_replay)
        row.addWidget(self.replay_btn)

        self.trash_btn = QPushButton("移入回收站")
        self.trash_btn.setEnabled(False)
        self.trash_btn.clicked.connect(self._on_trash)
        row.addWidget(self.trash_btn)

        self.open_btn = QPushButton("打开目录")
        self.open_btn.clicked.connect(self._on_open_dir)
        row.addWidget(self.open_btn)

        row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        row.addWidget(close_btn)
        outer.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    # ------------------------------------------------------------------
    def set_root(self, root: str) -> None:
        root = str(root or "")
        if root == self._root:
            return
        self._root = root
        self.path_label.setText(root)
        self.refresh()

    def _library(self):
        from mbdsdr_ai.recording_library import RecordingLibrary
        return RecordingLibrary(self._root)

    def _set_error(self, text: str) -> None:
        self._entries = []
        self.table.setRowCount(0)
        self.table.setVisible(False)
        self.empty_label.setText(text)
        self.empty_label.setVisible(True)
        self.status.setText(text)
        self.replay_btn.setEnabled(False)
        self.trash_btn.setEnabled(False)
        self.replay_btn.setToolTip("没有可回放的条目")
        self.trash_btn.setToolTip("没有可操作的条目")

    def refresh(self) -> None:
        """重新加载索引并按磁盘核对（首次打开时自动建立索引）。"""
        self.path_label.setText(self._root)
        if not self._root or not os.path.isdir(self._root):
            self._set_error(
                f"录音目录还不存在：{self._root or '（未设置）'}\n"
                "先在主窗口录一段，或到「设置 → 数据与操作」改录音目录。")
            return
        try:
            lib = self._library()
            notes: list[str] = []
            # 每次打开/刷新都核对磁盘：新文件纳入、丢文件标出。只写索引与
            # 留痕，不移动/不删除录音；指纹只读首尾 64 KiB，不整盘吃 IO。
            made = lib.reconcile(apply=True, scan=True)
            if not made.ok:
                self._set_error(f"核对录音库失败：{made.message}")
                return
            summary = made.data.get("summary") or {}
            changed = sum(int(v) for v in summary.values()
                          if isinstance(v, int))
            if changed:
                notes.append(f"已核对磁盘：{changed} 项变化")
            listed = lib.list_entries()
            if not listed.ok:
                self._set_error(f"读取录音库失败：{listed.message}")
                return
        except Exception as exc:  # noqa: BLE001 - 必须可见
            self._set_error(f"读取录音库失败：{type(exc).__name__}: {exc}")
            return
        self._entries = [e for e in listed.data.get("entries", [])
                         if isinstance(e, dict) and e.get("state") != "trashed"]
        self._fill_table()
        base = f"共 {len(self._entries)} 条"
        if notes:
            base += " · " + "；".join(notes)
        self.status.setText(base)

    def _fill_table(self) -> None:
        self.table.setRowCount(0)
        try:
            lib = self._library()
        except Exception:
            lib = None
        for entry in self._entries:
            row = self.table.rowCount()
            self.table.insertRow(row)
            try:
                name = os.path.basename(lib.entry_path(entry)) if lib else ""
            except Exception:
                name = ""
            cells = [
                name or "（路径未知）",
                _start_time_text(entry),
                format_duration(entry.get("duration_s")),
                format_size(entry.get("size_bytes")),
                state_text(str(entry.get("state") or "")),
                str(entry.get("notes") or ""),
            ]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        has_rows = self.table.rowCount() > 0
        self.table.setVisible(has_rows)
        self.empty_label.setVisible(not has_rows)
        if not has_rows:
            self.empty_label.setText(
                "这个目录里还没有可列出的录音。\n"
                "在主窗口录一段后会出现在这里；已移入回收站的条目不显示。")
        self._sync_actions()

    def _selected_entry(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        row = rows[0].row()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def _sync_actions(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.replay_btn.setEnabled(False)
            self.trash_btn.setEnabled(False)
            self.replay_btn.setToolTip("先选一条录音")
            self.trash_btn.setToolTip("先选一条录音")
            return
        state = str(entry.get("state") or "")
        path = ""
        try:
            path = self._library().entry_path(entry)
        except Exception:
            path = ""
        exists = bool(path) and os.path.exists(path)
        can_replay = state in _REPLAYABLE and exists
        self.replay_btn.setEnabled(can_replay)
        self.replay_btn.setToolTip(
            "把这条录音交给主窗口回放" if can_replay else
            ("文件不存在，无法回放" if not exists else
             f"状态「{state_text(state)}」不可回放"))
        self.trash_btn.setEnabled(True)
        self.trash_btn.setToolTip("把文件移入 _trash/ 回收站（不是物理删除）")

    # ------------------------------------------------------------------
    def _on_replay(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        try:
            path = self._library().entry_path(entry)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "无法回放", f"解析路径失败：{exc}")
            return
        if not os.path.exists(path):
            QMessageBox.warning(
                self, "无法回放", f"文件已不存在：\n{path}\n"
                "可先用「刷新 / 核对磁盘」让索引与实际一致。")
            return
        self.replay_requested.emit(path)
        self.accept()

    def _on_trash(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        entry_id = str(entry.get("entry_id") or "")
        try:
            path = self._library().entry_path(entry)
            name = os.path.basename(path)
        except Exception:
            path, name = "", "（路径未知）"
        answer = QMessageBox.question(
            self, "移入回收站",
            f"把这条录音移入回收站？\n{name}\n"
            f"{path}\n\n"
            "只会移动到录音目录的 _trash/ 下，不会物理删除；"
            "文件已不存在时只改索引。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        res = self._library().trash_entry(entry_id, confirm=True)
        if res.ok:
            self.status.setText(res.message)
        else:
            QMessageBox.warning(self, "移入回收站失败", res.message)
        self.refresh()

    def _on_open_dir(self) -> None:
        if self._root and os.path.isdir(self._root):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._root))
        else:
            QMessageBox.information(self, "打开目录", "录音目录不存在。")
