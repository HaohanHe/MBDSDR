# SPDX-License-Identifier: MIT
"""地点面板：搜索城市 / 地图点选 / 看当前站址与来源。

## 它解决什么

用户说「小姑娘不用学原理也能用」——她的第一反应是「我在哪」，不是
「我的经度是多少」。所以这里与 Stellarium 的 ``LocationDialog`` 对齐：
**按名字找**、**在地图上点**、**看到"现在用的是哪"**。

## 三条诚实边界

1. 搜不到就说搜不到（列表给一条不可选提示），不编"最近的大城市"；
2. 城市搜索走 OSM（ODbL），底部常驻写清"需要联网"；断网时明确降级为
   「直接点地图或手填经纬度」，不白屏、不静默失败；
3. 选中地点**只改位置，不改时间**——时刻归用户自己按「时间」，
   与 Stellarium 一致。

## 写入口

面板**不直接写配置**：选中后只发 ``site_chosen(Place)``，由主窗
``set_observer_site`` 统一落盘、刷天空/过境/ADS-B。面板自己没有任何
第二条写路径。
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, Signal  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QPainter, QPainterPath, QPen,
)
from PySide6.QtWidgets import (  # noqa: E402
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

try:  # 数据层可选：缺了面板仍能点地图，只是搜不了
    from mbdsdr_ai.adsb_map import (  # noqa: E402
        WORLD_LAND_POLYGONS, project_equirectangular,
    )
    from mbdsdr_ai.location import Place, make_provider  # noqa: E402
except Exception:  # noqa: BLE001
    WORLD_LAND_POLYGONS = []
    project_equirectangular = None
    Place = None          # type: ignore
    make_provider = None  # type: ignore

__all__ = ["LocationPanel"]

_BG = "#14161c"
_PANEL = "#1b1e26"
_LINE = "#333845"
_TEXT = "#e8eaf0"
_DIM = "#8f95a3"
_SEA = "#101a24"
_LAND = "#1d2733"
_MARK = "#ff5b4a"


class _WorldPicker(QWidget):
    """离线世界地图：点一下就是经纬度（不联网、不画假地名）。"""

    picked = Signal(float, float)      # lat, lon

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(180)
        self.setCursor(Qt.CrossCursor)
        self._marker: Optional[tuple] = None
        self.setToolTip("点一下选位置（离线世界地图；精度到城市级足够）")

    def set_marker(self, lat: Optional[float], lon: Optional[float]) -> None:
        self._marker = None if lat is None or lon is None else (float(lat), float(lon))
        self.update()

    def _project(self, lat: float, lon: float):
        w, h = float(self.width()), float(self.height())
        if project_equirectangular is None:
            return 0.0, 0.0
        return project_equirectangular(lat, lon, w, h, 8.0)

    def _unproject(self, x: float, y: float):
        """等距圆柱反解（与 project_equirectangular 严格互逆）。"""
        pad = 8.0
        w = max(1.0, float(self.width()) - 2.0 * pad)
        h = max(1.0, float(self.height()) - 2.0 * pad)
        lon = (float(x) - pad) / w * 360.0 - 180.0
        lat = 90.0 - (float(y) - pad) / h * 180.0
        return max(-90.0, min(90.0, lat)), max(-180.0, min(180.0, lon))

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor(_SEA))

        # 经纬网（每 30°）
        p.setPen(QPen(QColor(255, 255, 255, 22), 1))
        for lon in range(-180, 181, 30):
            x, _ = self._project(0.0, lon)
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        for lat in range(-60, 61, 30):
            _, y = self._project(lat, 0.0)
            p.drawLine(QPointF(0, y), QPointF(self.width(), y))

        # 陆地轮廓（真实简化坐标，来自 mbdsdr_ai.adsb_map）
        p.setPen(QPen(QColor(90, 120, 150, 140), 1))
        p.setBrush(QColor(_LAND))
        for poly in WORLD_LAND_POLYGONS:
            if len(poly) < 3:
                continue
            path = QPainterPath()
            # 注意：WORLD_LAND_POLYGONS 的每个点是 (lon, lat)，不是 (lat, lon)。
            # 反了会把整张世界地图画成碎片（经度当纬度用），2026-11-09 中过一次。
            for i, (lo, la) in enumerate(poly):
                x, y = self._project(float(la), float(lo))
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            path.closeSubpath()
            p.drawPath(path)

        # 选中标记
        if self._marker is not None:
            x, y = self._project(*self._marker)
            p.setPen(QPen(QColor(_MARK), 2))
            p.setBrush(QColor(255, 91, 74, 60))
            p.drawEllipse(QPointF(x, y), 7.0, 7.0)
            p.setPen(QPen(QColor(_MARK), 1, Qt.DashLine))
            p.drawLine(QPointF(x - 16, y), QPointF(x + 16, y))
            p.drawLine(QPointF(x, y - 16), QPointF(x, y + 16))
        p.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt)
        if event.button() != Qt.LeftButton:
            return
        lat, lon = self._unproject(event.position().x(), event.position().y())
        self.set_marker(lat, lon)
        self.picked.emit(lat, lon)


class _SearchWorker(QThread):
    """把 6 秒超时的网络请求挪出 UI 线程。"""

    done = Signal(object)      # List[Place] | None（None = 失败）

    def __init__(self, provider, query: str, parent=None):
        super().__init__(parent)
        self._provider = provider
        self._query = query

    def run(self) -> None:
        try:
            self.done.emit(self._provider.search(self._query, 8))
        except Exception:  # noqa: BLE001
            self.done.emit(None)


class LocationPanel(QFrame):
    """选地点浮层。由主窗创建、复用；关闭只是 hide。"""

    site_chosen = Signal(object)     # Place
    cleared = Signal()

    def __init__(self, parent: Optional[QWidget] = None,
                 provider=None, async_search: bool = True):
        super().__init__(parent, Qt.Tool)
        self.setObjectName("locationPanel")
        self.setWindowTitle("选择观测地点")
        self.setMinimumWidth(460)
        self._provider = provider
        self._async_search = bool(async_search)
        self._worker: Optional[_SearchWorker] = None
        self._results: List[object] = []
        self._pending = None          # 待确认的 Place
        self._build_ui()
        self._apply_style()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(9)

        title = QLabel("选择观测地点", self)
        title.setObjectName("lpTitle")
        lay.addWidget(title)

        self.hint = QLabel("搜城市，或直接在地图上点一下", self)
        self.hint.setObjectName("lpHint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("输入城市或地名：北京 / 长春 / Tokyo…")
        self.search_edit.setMinimumHeight(30)
        self.search_edit.returnPressed.connect(self._do_search)
        row.addWidget(self.search_edit, 1)
        self.search_btn = QPushButton("搜索", self)
        self.search_btn.setMinimumHeight(30)
        self.search_btn.clicked.connect(self._do_search)
        row.addWidget(self.search_btn, 0)
        lay.addLayout(row)

        self.result_list = QListWidget(self)
        self.result_list.setFixedHeight(112)
        self.result_list.itemActivated.connect(self._on_item)
        self.result_list.itemClicked.connect(self._on_item)
        lay.addWidget(self.result_list)

        self.map_view = _WorldPicker(self)
        self.map_view.picked.connect(self._on_map_picked)
        lay.addWidget(self.map_view, 1)

        self.current_label = QLabel("当前：未设置", self)
        self.current_label.setObjectName("lpCurrent")
        self.current_label.setWordWrap(True)
        lay.addWidget(self.current_label)

        self.pending_label = QLabel("", self)
        self.pending_label.setObjectName("lpHint")
        self.pending_label.setWordWrap(True)
        lay.addWidget(self.pending_label)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.clear_btn = QPushButton("清空位置", self)
        self.clear_btn.setToolTip("回到未定位空态（天空不画星、过境提示先设位置）")
        self.clear_btn.clicked.connect(self._on_clear)
        btns.addWidget(self.clear_btn, 0)
        btns.addStretch(1)
        self.apply_btn = QPushButton("用这个位置", self)
        self.apply_btn.setObjectName("lpGo")
        self.apply_btn.setMinimumHeight(32)
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply)
        btns.addWidget(self.apply_btn, 0)
        lay.addLayout(btns)

        foot = QLabel(
            "城市搜索使用 OpenStreetMap（© OSM contributors, ODbL）· 需要联网；"
            "离线可直接点地图。选地点不会改观测时刻。", self)
        foot.setObjectName("lpHint")
        foot.setWordWrap(True)
        lay.addWidget(foot)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            "QFrame#locationPanel { background-color: %s;"
            " border: 1px solid %s; border-radius: 10px; }"
            # QLabel 默认吃 palette 浅底，深色面板上会变白条（同款事故已犯两次）
            "QLabel { background: transparent; }"
            "QLabel#lpTitle { color: %s; font-size: 14px; font-weight: 600; }"
            "QLabel#lpHint { color: %s; font-size: 11px; }"
            "QLabel#lpCurrent { color: %s; font-size: 12px; }"
            "QLineEdit { background: %s; color: %s; border: 1px solid %s;"
            " border-radius: 7px; padding: 4px 9px; }"
            "QLineEdit:focus { border-color: #5b7bd6; }"
            "QListWidget { background: %s; color: #d8deea;"
            " border: 1px solid %s; border-radius: 7px; padding: 3px; }"
            "QListWidget::item { padding: 4px 6px; border-radius: 5px; }"
            "QListWidget::item:selected { background: rgba(90,140,255,0.35);"
            " color: #ffffff; }"
            "QPushButton { background: %s; color: #d3d7e0;"
            " border: 1px solid %s; border-radius: 7px; padding: 5px 12px; }"
            "QPushButton:hover { border-color: #5b6478; }"
            "QPushButton:disabled { color: #5b6478; }"
            "QPushButton#lpGo { background: #2b6cf6; border-color: #2b6cf6;"
            " color: #ffffff; font-weight: 600; }"
            % (_BG, _LINE, _TEXT, _DIM, _TEXT, _PANEL, _TEXT, _LINE,
               _PANEL, _LINE, _PANEL, _LINE))

    # ------------------------------------------------------------- 对外 API
    def set_current(self, lat: Optional[float], lon: Optional[float],
                    source_zh: str = "") -> None:
        """显示当前站址（只读回显；面板不因此持有任何写入权）。"""
        if lat is None or lon is None:
            self.current_label.setText("当前：未设置（天空按未定位空态）")
            self.map_view.set_marker(None, None)
            return
        src = f" · 来源：{source_zh}" if source_zh else ""
        self.current_label.setText(
            f"当前：{float(lat):.4f}°, {float(lon):.4f}°{src}")
        self.map_view.set_marker(float(lat), float(lon))

    def set_provider(self, provider) -> None:
        self._provider = provider

    # --------------------------------------------------------------- 搜索
    def _set_hint(self, text: str, error: bool = False) -> None:
        self.hint.setText(text)
        self.hint.setStyleSheet(
            f"background: transparent; color: {'#ff8a7a' if error else _DIM};"
            " font-size: 11px;")

    def _provider_or_none(self):
        if self._provider is None and make_provider is not None:
            try:
                self._provider = make_provider("auto")
            except Exception:  # noqa: BLE001
                self._provider = None
        return self._provider

    def _do_search(self) -> None:
        q = self.search_edit.text().strip()
        if not q:
            return
        prov = self._provider_or_none()
        if prov is None:
            self._set_hint("地点检索不可用：直接在地图上点，或在设置里手填经纬度",
                           error=True)
            return
        try:
            ok, reason = prov.available()
        except Exception:  # noqa: BLE001
            ok, reason = False, "provider 异常"
        if not ok:
            self._set_hint(f"离线：{reason}。可直接在地图上点，或手填经纬度",
                           error=True)
            return

        self.search_btn.setEnabled(False)
        self._set_hint("搜索中…")
        if self._async_search:
            if self._worker is not None and self._worker.isRunning():
                return
            self._worker = _SearchWorker(prov, q, self)
            self._worker.done.connect(self._on_search_done)
            self._worker.start()
        else:
            try:
                hits = prov.search(q, 8)
            except Exception:  # noqa: BLE001
                hits = None
            self._on_search_done(hits)

    def _on_search_done(self, hits) -> None:
        self.search_btn.setEnabled(True)
        self.result_list.clear()
        self._results = list(hits or [])
        if hits is None:
            self._set_hint(
                "搜索失败：可能没网或被限流。可直接在地图上点，或手填经纬度",
                error=True)
            return
        if not hits:
            item = QListWidgetItem("没有匹配的地名（换个写法试试，或直接点地图）")
            item.setFlags(Qt.NoItemFlags)
            self.result_list.addItem(item)
            self._set_hint("没有匹配。可直接在地图上点。", error=True)
            return
        for p in hits:
            extra = f"  ·  {p.detail}" if getattr(p, "detail", "") else ""
            it = QListWidgetItem(f"{p.name}   {p.coord_text()}{extra}")
            it.setData(Qt.UserRole, p)
            self.result_list.addItem(it)
        self._set_hint(f"找到 {len(hits)} 个结果，点一条选中")

    # ------------------------------------------------------------- 选择
    def _on_item(self, item: QListWidgetItem) -> None:
        p = item.data(Qt.UserRole)
        if p is not None:
            self._set_pending(p)

    def _on_map_picked(self, lat: float, lon: float) -> None:
        if Place is None:
            return
        self._set_pending(Place(name="手选坐标", lat=float(lat), lon=float(lon),
                                kind="map", source="map"))

    def _set_pending(self, place) -> None:
        self._pending = place
        self.map_view.set_marker(place.lat, place.lon)
        src = {"map": "地图点选", "osm": "城市搜索·OSM",
               "offline": "离线城市表"}.get(getattr(place, "source", ""), "地点")
        self.pending_label.setText(
            f"待确认：{place.name} · {place.coord_text()}（{src}）")
        self.apply_btn.setEnabled(True)

    def _on_apply(self) -> None:
        if self._pending is None:
            return
        self.site_chosen.emit(self._pending)
        self._pending = None
        self.apply_btn.setEnabled(False)
        self.hide()

    def _on_clear(self) -> None:
        self._pending = None
        self.apply_btn.setEnabled(False)
        self.pending_label.setText("")
        self.map_view.set_marker(None, None)
        self.cleared.emit()

    # ------------------------------------------------------------- 生命周期
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt)
        # 关闭只是藏起来（复用同一实例，不销毁）；正在跑的搜索允许它自己结束
        event.ignore()
        self.hide()
