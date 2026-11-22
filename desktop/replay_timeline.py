# SPDX-License-Identifier: MIT
"""现有数组回放的轻量视图：只发导航意图，不拥有播放器或时钟。"""
import json
import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QTextEdit, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)


_EVENT_LABELS = {
    "record_start": "开始录制", "record_stop": "停止录制",
    "tune": "调谐", "decode_hit": "解码命中", "decode": "解码命中",
    "read_empty": "读空", "empty_read": "读空", "read_gap": "读空 / 数据间断",
    "sample_loss": "录制短录 / 样本丢失",
    "timeline_truncated": "事件记录已截断", "read_failed": "读取失败",
    "gain": "增益变更", "sample_rate": "采样率变更",
    "disconnect": "设备断开", "record_error": "录制错误",
}


def event_label(event):
    kind = str(event.get("kind") or "未命名事件")
    return _EVENT_LABELS.get(kind, f"未知事件（{kind}）")


def event_offset(event):
    try:
        at = float(event.get("at_s"))
        if math.isfinite(at) and at >= 0:
            return f"{at:.3f}s"
    except (TypeError, ValueError):
        pass
    return "未知"


class ReplayTimeline(QWidget):
    seek_requested = Signal(object)  # Python int，避免 Qt int 截断样本索引
    toggle_requested = Signal()
    stop_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("replayTimeline")
        self._total = 0
        self._sr = 1.0
        self._events = []
        self._populated = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(3)
        row = QHBoxLayout()
        self.play_button = QPushButton("暂停")
        self.stop_button = QPushButton("停止回放")
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setAccessibleName("回放样本进度")
        self.progress_label = QLabel()
        self.events_button = QPushButton("事件")
        self.events_button.setCheckable(True)
        for widget in (self.play_button, self.stop_button, self.slider,
                       self.progress_label, self.events_button):
            row.addWidget(widget, 1 if widget is self.slider else 0)
        layout.addLayout(row)
        self.scope_label = QLabel()
        self.scope_label.setWordWrap(True)
        layout.addWidget(self.scope_label)
        self.event_panel = QWidget()
        panel_layout = QVBoxLayout(self.event_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        self.events = QTreeWidget()
        self.events.setHeaderLabels(["事件", "录制偏移（非 UTC）", "原始 UTC", "定位口径"])
        self.events.setRootIsDecorated(False)
        self.events.setUniformRowHeights(True)
        self.events.setMinimumHeight(85)
        self.events.setMaximumHeight(150)
        panel_layout.addWidget(self.events)
        nav = QHBoxLayout()
        self.previous_button = QPushButton("上一事件")
        self.next_button = QPushButton("下一事件")
        self.jump_button = QPushButton("跳到所选事件")
        self.detail_button = QPushButton("事件详情")
        self.detail_button.setCheckable(True)
        for button in (self.previous_button, self.next_button,
                       self.jump_button, self.detail_button):
            nav.addWidget(button)
        nav.addStretch()
        panel_layout.addLayout(nav)
        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(90)
        self.details.hide()
        panel_layout.addWidget(self.details)
        layout.addWidget(self.event_panel)
        self.event_panel.hide()
        self.events_button.toggled.connect(self._expand_events)
        self.detail_button.toggled.connect(self._expand_details)
        self.events.currentItemChanged.connect(self._selection_changed)
        self.events.itemDoubleClicked.connect(lambda *_: self._jump())
        self.previous_button.clicked.connect(lambda: self._navigate(-1))
        self.next_button.clicked.connect(lambda: self._navigate(1))
        self.jump_button.clicked.connect(self._jump)
        self.play_button.clicked.connect(self.toggle_requested.emit)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        self.slider.sliderReleased.connect(self._slider_released)
        self.slider.valueChanged.connect(self._slider_changed)
        self.hide()

    def set_recording(self, name, total, sample_rate, events, scope):
        self.clear_recording()
        self._total, self._sr = int(total), float(sample_rate)
        self._events = list(events)
        self.slider.blockSignals(True)
        self.slider.setRange(0, min(self._total, 2_147_483_647))
        self.slider.blockSignals(False)
        self.scope_label.setText(f"{name} · {scope}")
        self.scope_label.setToolTip(f"当前录音与范围：{name}\n{scope}")
        self.events_button.setText(f"事件（{len(events)}）" if events else "事件：未记录")
        self.events_button.setEnabled(bool(events))
        self.set_progress(0, True)
        self.show()

    def clear_recording(self):
        self.hide()
        self._events = []
        self._populated = False
        self.events.clear()
        self.details.clear()
        self.events_button.setChecked(False)
        self.detail_button.setChecked(False)
        self._total = 0

    def set_progress(self, sample, playing, center_hz=None):
        sample = min(self._total, max(0, int(sample)))
        self.play_button.setText("暂停" if playing else "播放")
        self.progress_label.setText(
            f"{sample / self._sr:.3f} / {self._total / self._sr:.3f}s"
            f" · {sample:,}/{self._total:,} 样本"
            + (f" · {center_hz / 1e6:.6f} MHz" if center_hz is not None else ""))
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(round(sample * self.slider.maximum() / max(1, self._total)))
            self.slider.blockSignals(False)

    def _slider_sample(self):
        return round(self.slider.value() * self._total / max(1, self.slider.maximum()))

    def _slider_released(self):
        if self._total:
            self.seek_requested.emit(self._slider_sample())

    def _slider_changed(self, _value):
        # 键盘/轨道点击也可定位；拖动只在松手时提交，避免反复清 DSP。
        if not self.slider.isSliderDown():
            self._slider_released()

    def _reason(self, event):
        value = event.get("sample")
        try:
            number = float(value)
            if not math.isfinite(number) or not number.is_integer() or number < 0:
                return "无有效样本位置，不能跳转"
        except (TypeError, ValueError, OverflowError):
            return "未记录可定位时间，不能跳转"
        if number > self._total:
            return "超出当前产物/载入范围，不能跳转"
        return ""

    def _expand_events(self, expanded):
        if expanded and not self._populated:
            for event in self._events:
                estimate = event.get("index_source") == "wall"
                reason = self._reason(event)
                basis = "墙钟估算" if estimate else "样本索引" if event.get("sample") is not None else "无索引"
                location_note = basis + (f"；{reason}" if reason else "")
                item = QTreeWidgetItem([
                    event_label(event), event_offset(event),
                    str(event.get("utc") or "未记录"), location_note,
                ])
                item.setToolTip(3, f"定位口径：{location_note}")
                self.events.addTopLevelItem(item)
            self._populated = True
            for column in range(3):
                self.events.resizeColumnToContents(column)
        self.event_panel.setVisible(expanded)
        self._selection_changed()

    def _selected_index(self):
        return self.events.indexOfTopLevelItem(self.events.currentItem())

    def _selection_changed(self, *_):
        idx = self._selected_index()
        reason = self._reason(self._events[idx]) if idx >= 0 else "先选择事件"
        self.jump_button.setEnabled(not reason)
        self.jump_button.setToolTip(reason or "按事件样本位置跳转；保持当前播放/暂停状态")
        self.previous_button.setEnabled(any(
            not self._reason(e) for e in self._events[:max(0, idx)]))
        self.next_button.setEnabled(any(
            not self._reason(e) for e in self._events[idx + 1:]))
        self.detail_button.setEnabled(idx >= 0)
        self.previous_button.setToolTip(
            "上一事件：向前跳到最近的可用事件；前面没有可定位事件时置灰")
        self.next_button.setToolTip(
            "下一事件：向后跳到最近的可用事件；后面没有可定位事件时置灰")
        self.detail_button.setToolTip(
            "展开所选事件的原始记录；先在事件列表选中一行才能用")
        if self.detail_button.isChecked():
            self._expand_details(True)

    def _expand_details(self, expanded):
        idx = self._selected_index()
        self.details.setVisible(expanded)
        if expanded:
            self.details.setPlainText(json.dumps(
                self._events[idx], ensure_ascii=False, indent=2, default=str)
                if idx >= 0 else "先选择事件")

    def _jump(self):
        idx = self._selected_index()
        if idx >= 0 and not self._reason(self._events[idx]):
            self.seek_requested.emit(int(self._events[idx]["sample"]))

    def _navigate(self, direction):
        idx = self._selected_index()
        for i in range(idx + direction, len(self._events) if direction > 0 else -1, direction):
            if not self._reason(self._events[i]):
                self.events.setCurrentItem(self.events.topLevelItem(i))
                self._jump()
                break
