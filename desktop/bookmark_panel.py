"""
MBDSDR 书签管理器面板（desktop/bookmark_panel.py）
=================================================

把内核 ``mbdsdr_ai.bookmark_manager.BookmarkManager`` 接成可停靠面板：
分组树 / 书签列表 / 搜索 / 添加当前频率 / 编辑删除 / CSV 导入导出 / 找最近。
点击书签 → 发 ``tune_requested(freq_hz, mode, bandwidth_hz)``，主窗口真正调谐。

红线：
  * 持久化到 ``~/.mbdsdr/bookmarks.json``（内核负责，包目录可注入便于测试）。
  * **不预存任何地区电台**——初始列表为空，全部由用户添加/导入。
  * 无硬件也能用（管理书签是离线功能）；点书签时主窗口自行判断是否已连接。
"""
from __future__ import annotations

import os
from typing import Optional, Callable, List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
    QListWidget, QListWidgetItem, QLineEdit, QPushButton, QLabel,
    QInputDialog, QMessageBox, QFileDialog, QHeaderView, QGroupBox,
    QComboBox, QFormLayout,
)

from mode_registry import get as _get_mode

try:  # pragma: no cover
    from mbdsdr_ai.bookmark_manager import BookmarkManager, Bookmark
    _KERNEL_AVAILABLE = True
    _KERNEL_IMPORT_ERR = ""
except Exception as _e:  # noqa: BLE001
    BookmarkManager = None  # type: ignore
    Bookmark = None  # type: ignore
    _KERNEL_AVAILABLE = False
    _KERNEL_IMPORT_ERR = str(_e)


# 分组树：只建空壳，不带任何频点（红线）
GROUPS = [
    ("aviation", "航空"),
    ("marine", "海事"),
    ("ham", "业余"),
    ("broadcast", "广播"),
    ("satellite", "卫星"),
    ("custom", "自定义"),
]
_GROUP_LABEL = dict(GROUPS)
_GROUP_KEY = {v: k for k, v in GROUPS}


class BookmarkPanel(QWidget):
    """书签管理器：分组 + 列表 + 搜索 + CRUD + CSV + nearest。"""

    tune_requested = Signal(float, str, float)  # (freq_hz, mode, bandwidth_hz)

    def __init__(self, config_dir: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._config_dir = config_dir
        self._mgr = None
        self._current_group: Optional[str] = None  # None = 全部
        self._current_freq_provider: Optional[Callable[[], float]] = None
        self._build_ui()
        self._init_manager()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # 搜索
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("搜索名称 / 模式 / 分组…")
        self.search_box.textChanged.connect(self.refresh_list)
        root.addWidget(self.search_box)

        body = QHBoxLayout()

        # 左：分组树
        self.group_list = QListWidget()
        all_item = QListWidgetItem("全部")
        self.group_list.addItem(all_item)
        for key, label in GROUPS:
            self.group_list.addItem(QListWidgetItem(f"{label}"))
        self.group_list.setCurrentRow(0)
        self.group_list.currentRowChanged.connect(self._on_group_changed)
        self.group_list.setMaximumWidth(110)
        body.addWidget(self.group_list)

        # 右：书签列表
        right = QVBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["名称", "频率(MHz)", "模式", "带宽(kHz)"])
        self.tree.setColumnWidth(0, 150)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        right.addWidget(self.tree, stretch=1)

        # 操作按钮行
        ops = QHBoxLayout()
        self.add_btn = QPushButton("添加当前频率")
        self.add_btn.clicked.connect(self._on_add_current)
        self.edit_btn = QPushButton("编辑")
        self.edit_btn.clicked.connect(self._on_edit)
        self.del_btn = QPushButton("删除")
        self.del_btn.clicked.connect(self._on_delete)
        self.nearest_btn = QPushButton("找最近")
        self.nearest_btn.setToolTip("找当前频率附近的书签")
        self.nearest_btn.clicked.connect(self._on_nearest)
        for b in (self.add_btn, self.edit_btn, self.del_btn, self.nearest_btn):
            ops.addWidget(b)
        right.addLayout(ops)

        io_row = QHBoxLayout()
        self.import_btn = QPushButton("导入 CSV")
        self.import_btn.clicked.connect(self._on_import_csv)
        self.export_btn = QPushButton("导出 CSV")
        self.export_btn.clicked.connect(self._on_export_csv)
        io_row.addWidget(self.import_btn)
        io_row.addWidget(self.export_btn)
        right.addLayout(io_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hintLabel")
        right.addWidget(self.status_label)
        body.addLayout(right, stretch=1)

        root.addLayout(body, stretch=1)

    # ------------------------------------------------------------ 初始化
    def _init_manager(self):
        if not _KERNEL_AVAILABLE:
            self.status_label.setText(f"书签内核不可用: {_KERNEL_IMPORT_ERR}")
            self._set_ops_enabled(False)
            return
        try:
            self._mgr = BookmarkManager(config_dir=self._config_dir) \
                if self._config_dir is not None else BookmarkManager()
        except Exception as e:  # noqa: BLE001
            self._mgr = None
            self.status_label.setText(f"书签初始化失败: {e}")
            self._set_ops_enabled(False)
            return
        self.refresh_list()

    def _set_ops_enabled(self, en: bool):
        for b in (self.add_btn, self.edit_btn, self.del_btn,
                  self.nearest_btn, self.import_btn, self.export_btn):
            b.setEnabled(en)

    # ------------------------------------------------------------ 外部接线
    def set_current_freq_provider(self, fn: Callable[[], float]):
        """主窗口注入：返回当前中心频率 Hz（用于"添加当前频率"）。"""
        self._current_freq_provider = fn

    # ------------------------------------------------------------ 列表
    def _on_group_changed(self, row: int):
        if row <= 0:
            self._current_group = None
        else:
            label = self.group_list.item(row).text() if row < self.group_list.count() else None
            self._current_group = _GROUP_KEY.get(label)
        self.refresh_list()

    def refresh_list(self):
        self.tree.clear()
        if self._mgr is None:
            return
        q = self.search_box.text().strip()
        try:
            items: List = self._mgr.search(q) if q else self._mgr.all()
        except Exception:  # noqa: BLE001
            items = []
        for bm in items:
            if self._current_group and (bm.group or "") != self._current_group:
                continue
            it = QTreeWidgetItem([
                bm.name or "(未命名)",
                f"{bm.frequency_hz/1e6:.4f}",
                bm.modulation,
                f"{bm.bandwidth_hz/1e3:.1f}",
            ])
            it.setData(0, Qt.UserRole, self._bookmark_to_dict(bm))
            self.tree.addTopLevelItem(it)
        self.status_label.setText(f"{self.tree.topLevelItemCount()} 条书签")

    @staticmethod
    def _bookmark_to_dict(bm) -> dict:
        return {
            "frequency_hz": int(bm.frequency_hz),
            "name": bm.name,
            "modulation": bm.modulation,
            "bandwidth_hz": int(bm.bandwidth_hz),
            "group": bm.group or "",
        }

    def _selected(self) -> Optional[dict]:
        it = self.tree.currentItem()
        if it is None:
            return None
        return it.data(0, Qt.UserRole)

    # ------------------------------------------------------------ 操作
    def _on_add_current(self):
        if self._mgr is None:
            return
        freq = 0.0
        if self._current_freq_provider is not None:
            try:
                freq = float(self._current_freq_provider())
            except Exception:  # noqa: BLE001
                freq = 0.0
        if freq <= 0:
            QMessageBox.information(self, "添加书签", "无当前频率（未连接 SDR 或未调谐）。\n"
                                    "可手动输入频率。")
        name, ok = QInputDialog.getText(self, "添加书签", "名称：",
                                        text="")
        if not ok:
            return
        freq_mhz, ok = QInputDialog.getDouble(
            self, "添加书签", "频率 (MHz):",
            value=freq/1e6 if freq > 0 else 100.0,
            min=0.001, max=6000.0, decimals=6)
        if not ok:
            return
        group_label = _GROUP_LABEL.get(self._current_group, "自定义")
        group_label, ok = QInputDialog.getItem(
            self, "添加书签", "分组：",
            [l for _, l in GROUPS], [l for _, l in GROUPS].index(group_label)
            if group_label in dict(GROUPS).values() else 5, False)
        if not ok:
            return
        mode, ok = QInputDialog.getItem(
            self, "添加书签", "解调模式：",
            # 可选模式来自 mode_registry；保持原 UX 顺序，未注册的自动剔除
            [m for m in ["NFM", "WFM", "AM", "USB", "LSB", "CW", "DIG"] if _get_mode(m)],
            0, False)
        if not ok:
            return
        bm = Bookmark(
            frequency_hz=int(round(freq_mhz * 1e6)),
            name=name or "(未命名)",
            modulation=mode,
            bandwidth_hz=12_500 if mode in ("NFM", "DIG") else
                         200_000 if mode == "WFM" else 8_000,
            group=_GROUP_KEY.get(group_label, "custom"),
        )
        try:
            self._mgr.add(bm)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "添加失败", str(e))
        self.refresh_list()

    def _on_edit(self):
        sel = self._selected()
        if sel is None:
            return
        name, ok = QInputDialog.getText(self, "编辑书签", "名称：",
                                        text=sel["name"])
        if not ok:
            return
        try:
            self._mgr.update(sel["frequency_hz"], name=name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "编辑失败", str(e))
        self.refresh_list()

    def _on_delete(self):
        sel = self._selected()
        if sel is None:
            return
        if QMessageBox.question(
                self, "删除书签",
                f"删除 {sel['name']} @ {sel['frequency_hz']/1e6:.4f} MHz？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self._mgr.remove(sel["frequency_hz"], sel["name"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(e))
        self.refresh_list()

    def _on_nearest(self):
        """找当前频率附近的书签（BookmarkManager 无 nearest，这里扫描 all()）。"""
        if self._mgr is None:
            return
        freq = 0.0
        if self._current_freq_provider is not None:
            try:
                freq = float(self._current_freq_provider())
            except Exception:  # noqa: BLE001
                freq = 0.0
        if freq <= 0:
            QMessageBox.information(self, "找最近", "无当前频率（未调谐）。")
            return
        best = None
        best_d = float("inf")
        for bm in self._mgr.all():
            d = abs(bm.frequency_hz - freq)
            if d < best_d:
                best_d = d
                best = bm
        if best is None:
            QMessageBox.information(self, "找最近", "书签列表为空。")
            return
        QMessageBox.information(
            self, "最近书签",
            f"{best.name} @ {best.frequency_hz/1e6:.4f} MHz\n"
            f"距当前 {best_d/1e3:.1f} kHz · 模式 {best.modulation}")
        self._emit_tune(self._bookmark_to_dict(best))

    def _on_import_csv(self):
        if self._mgr is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "导入书签 CSV", "", "CSV (*.csv);;所有文件 (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            n = self._mgr.import_csv_text(text)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            return
        self.refresh_list()
        QMessageBox.information(self, "导入完成", f"导入 {n} 条书签。")

    def _on_export_csv(self):
        if self._mgr is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出书签 CSV", "bookmarks.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            text = self._mgr.export_csv_text()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(e))
            return
        QMessageBox.information(self, "导出完成", f"已写出 {path}")

    def _on_item_double_clicked(self, item, _col):
        d = item.data(0, Qt.UserRole)
        if d:
            self._emit_tune(d)

    def _emit_tune(self, d: dict):
        self.tune_requested.emit(
            float(d["frequency_hz"]), d.get("modulation", "NFM"),
            float(d.get("bandwidth_hz", 12_500)))
