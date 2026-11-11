# SPDX-License-Identifier: MIT
"""地点面板：搜索城市 / 地图点选 / 看当前站址与来源。

## 它解决什么

用户说「小姑娘不用学原理也能用」——她的第一反应是「我在哪」，不是
「我的经度是多少」。所以这里与 Stellarium 的 ``LocationDialog`` 对齐：
**按名字找**、**在地图上点**、**看到"现在用的是哪"**。

## 地图是 OSM 在线瓦片，不是自绘世界图

2026-11-10 用户明确：**不要内置那张自己画的世界地图**（等距圆柱把高纬度
拉长、轮廓又粗又假，放大到城市级一片空白）。现在底图直接消费
OpenStreetMap 官方瓦片（``desktop/osm_map.py``），拖拽/缩放/点选都是真的
Web Mercator 坐标，右下角常驻 ODbL 署名。

## 三条诚实边界

1. 搜不到就说搜不到（列表给一条不可选提示），不编"最近的大城市"；
2. 城市搜索走 OSM Nominatim（ODbL），底部常驻写清"需要联网"；断网时
   明确降级为「直接点地图或手填经纬度」，不白屏、不静默失败；
   瓦片加载不出来同理——深色底+经纬网，顶上一句人话；
3. 选中地点**只改位置，不改时间**——时刻归用户自己按「时间」，
   与 Stellarium 一致；
4. 点地图只是**待确认选点**（琥珀色虚线环），当前站址的红橙标记原地不动，
   按「用这个位置」才真正换站址——一次误点不该看起来像把家搬走了。

## 写入口

面板**不直接写配置**：选中后只发 ``site_chosen(Place)``，由主窗
``set_observer_site`` 统一落盘、刷天空/过境/ADS-B。面板自己没有任何
第二条写路径。
"""
from __future__ import annotations

import math
import os
import sys
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt, QRect, QThread, Signal  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

try:  # 数据层可选：缺了面板仍能点地图，只是搜不了
    from mbdsdr_ai.location import Place, make_provider  # noqa: E402
except Exception:  # noqa: BLE001
    Place = None          # type: ignore
    make_provider = None  # type: ignore

try:  # 地图：OSM 在线瓦片（缺 QtNetwork 时面板退化为只有搜索+手填）
    from osm_map import (  # noqa: E402
        STYLES, OsmMapView, RegionDownloader, style_for_dark,
    )
except Exception:  # noqa: BLE001
    OsmMapView = None      # type: ignore
    STYLES = {}            # type: ignore
    RegionDownloader = None  # type: ignore
    style_for_dark = None  # type: ignore

__all__ = ["LocationPanel"]

_BG = "#14161c"
_PANEL = "#1b1e26"
_LINE = "#333845"
_TEXT = "#e8eaf0"
_DIM = "#8f95a3"


def download_summary(ok: int, bad: int) -> str:
    """下载结束提示：只有真的缓存到瓦片才允许说「可离线查看」。

    ok=0 时仍宣称「这片区域已可离线查看」会让人拔网后才发现是空的。
    """
    if ok and bad:
        return (f"下载结束：已缓存 {ok} 张，可离线查看；{bad} 张失败，"
                f"可再点「⤓ 下载」补下")
    if ok:
        return f"下载结束：已缓存 {ok} 张，这片区域可离线查看"
    if bad:
        return (f"下载失败 {bad} 张：没有缓存任何瓦片，检查网络或瓦片源后"
                f"再点「⤓ 下载」重试")
    return "下载结束：没有可下载的瓦片"


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
                 provider=None, async_search: bool = True,
                 map_online: bool = True, map_cache=None):
        super().__init__(parent, Qt.Tool)
        self.setObjectName("locationPanel")
        self.setWindowTitle("选择观测地点")
        self.setMinimumWidth(460)
        self._provider = provider
        self._async_search = bool(async_search)
        # map_online=False 供测试/离线演示：不发瓦片请求，只留经纬网与点选
        self._map_online = bool(map_online)
        self._map_cache = map_cache
        self._worker: Optional[_SearchWorker] = None
        self._results: List[object] = []
        self._pending = None          # 待确认的 Place
        self._current: Optional[tuple] = None   # 已生效站址（只读回显）
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
        self.result_list.setFixedHeight(96)
        self.result_list.itemActivated.connect(self._on_item)
        self.result_list.itemClicked.connect(self._on_item)
        lay.addWidget(self.result_list)
        # 空态给一句指引，别留一块不知道干什么的空白列表
        self._show_list_hint("搜城市后结果显示在这里；也可以直接在地图上点")

        self.map_view = self._make_map()
        lay.addWidget(self.map_view, 1)

        # 地图工具条：滚轮/捏合之外，给鼠标单键和触屏一个显式出口
        mrow = QHBoxLayout()
        mrow.setSpacing(6)
        self.zoom_out_btn = QPushButton("−", self)
        self.zoom_out_btn.setObjectName("lpMapTool")
        self.zoom_out_btn.setFixedSize(28, 24)
        self.zoom_out_btn.setToolTip("缩小地图，查看更大范围的区域背景；想回到本站点「回到当前」")
        self.zoom_out_btn.clicked.connect(lambda: self._map_zoom(-1))
        mrow.addWidget(self.zoom_out_btn, 0)
        self.zoom_in_btn = QPushButton("+", self)
        self.zoom_in_btn.setObjectName("lpMapTool")
        self.zoom_in_btn.setFixedSize(28, 24)
        self.zoom_in_btn.setToolTip("放大地图，看清当前站址周边的街道与地形细节")
        self.zoom_in_btn.clicked.connect(lambda: self._map_zoom(+1))
        mrow.addWidget(self.zoom_in_btn, 0)
        self.recenter_btn = QPushButton("回到当前", self)
        self.recenter_btn.setObjectName("lpMapTool")
        self.recenter_btn.setFixedHeight(24)
        self.recenter_btn.setToolTip("把地图移回当前站址（不改任何设置）")
        self.recenter_btn.clicked.connect(self._recenter_map)
        mrow.addWidget(self.recenter_btn, 0)
        # 底图风格：标准（OSM 官方）/ 暗色 / 浅灰（CARTO，OSM 数据）。
        # 「暗黑模式」不是把浅色瓦片调暗——OSM 官方瓦片没有深色版。
        self.style_combo = QComboBox(self)
        self.style_combo.setObjectName("lpMapTool")
        self.style_combo.setFixedHeight(24)
        self.style_combo.setMinimumWidth(92)
        for key in ("osm", "dark", "light"):
            st = STYLES.get(key)
            if st is not None:
                self.style_combo.addItem(
                    {"osm": "标准", "dark": "暗色", "light": "浅灰"}[key], key)
        self.style_combo.setToolTip(
            "底图风格：标准=OpenStreetMap 官方瓦片；暗色/浅灰=CARTO 的 OSM "
            "数据底图（官方瓦片没有深色版，硬调暗会糊）")
        self.style_combo.currentIndexChanged.connect(self._on_map_style_changed)
        mrow.addWidget(self.style_combo, 0)

        # 离线下载：把当前视野这一片存到本地，之后断网也能看
        self.download_btn = QPushButton("⤓ 下载", self)
        self.download_btn.setObjectName("lpMapTool")
        self.download_btn.setFixedHeight(24)
        self.download_btn.setToolTip(
            "把当前视野这片地图存到本地（含下面几级放大），之后断网也能看；"
            "可随时取消，已下的瓦片保留")
        self.download_btn.clicked.connect(self._on_download_clicked)
        mrow.addWidget(self.download_btn, 0)

        map_hint = QLabel("拖拽平移 · 滚轮缩放 · 单击选点", self)
        map_hint.setObjectName("lpHint")
        map_hint.setToolTip(
            "拖拽平移 · 滚轮缩放 · 双击放大\n"
            "单击选点只出琥珀色虚线圈（待确认），按「用这个位置」才换站址")
        self.map_hint = map_hint
        mrow.addWidget(map_hint, 0)
        mrow.addStretch(1)
        self.map_zoom_label = QLabel("z—", self)
        self.map_zoom_label.setObjectName("lpHint")
        mrow.addWidget(self.map_zoom_label, 0)
        lay.addLayout(mrow)

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
            "地图与城市搜索数据均来自 OpenStreetMap（© OSM contributors, "
            "ODbL）· 需要联网（本机需系统代理）；瓦片加载不出来时仍可点选经纬度。"
            "选地点不会改观测时刻。", self)
        foot.setObjectName("lpHint")
        foot.setWordWrap(True)
        lay.addWidget(foot)

    # ------------------------------------------------------------------ 地图
    def _make_map(self) -> QWidget:
        """OSM 在线瓦片视图；缺 QtNetwork 时给一句人话而不是白块。"""
        if OsmMapView is None:
            fallback = QLabel(
                "地图不可用（本机 Qt 缺 QtNetwork）：可直接在设置里手填经纬度",
                self)
            fallback.setObjectName("lpHint")
            fallback.setWordWrap(True)
            fallback.setMinimumHeight(120)
            return fallback
        view = OsmMapView(self, online=self._map_online,
                          cache=self._map_cache)
        view.setMinimumHeight(250)
        view.picked.connect(self._on_map_picked)
        view.zoom_changed.connect(self._on_map_zoom)
        view.map_error.connect(self._on_map_error)
        view.set_view(30.0, 110.0, 2)
        return view

    def _map_zoom(self, delta: int) -> None:
        view = self.map_view
        if OsmMapView is None or not isinstance(view, OsmMapView):
            return
        view.set_view(None, None, view.zoom() + int(delta))
        self._on_map_zoom(view.zoom())

    def _recenter_map(self) -> None:
        view = self.map_view
        if OsmMapView is None or not isinstance(view, OsmMapView):
            return
        if self._current is None:
            self.map_hint.setText("还没有当前位置：点一下地图选点，或搜索城市")
            return
        view.set_view(self._current[0], self._current[1], max(view.zoom(), 6))
        self._on_map_zoom(view.zoom())

    # ------------------------------------------------------- 风格 / 离线下载
    def _on_map_style_changed(self, _idx: int = 0) -> None:
        key = self.style_combo.currentData()
        if key and OsmMapView is not None and hasattr(self.map_view, "set_style"):
            try:
                self.map_view.set_style(str(key))
                self._sync_downloader_style()
            except (AttributeError, RuntimeError):
                pass

    def set_map_style(self, key: str) -> None:
        """外部（主题联动）设定底图风格；下拉跟着走，不重复设一次。"""
        idx = self.style_combo.findData(str(key))
        if idx < 0:
            return
        if idx != self.style_combo.currentIndex():
            self.style_combo.setCurrentIndex(idx)   # 触发 _on_map_style_changed
        else:
            self._on_map_style_changed()

    def map_style(self) -> str:
        try:
            return str(self.map_view.style_key())
        except AttributeError:
            return ""

    def _sync_downloader_style(self) -> None:
        """换风格后重启下载器：不同风格的缓存目录不同，不能混着下。"""
        if RegionDownloader is None or OsmMapView is None:
            return
        dl = getattr(self, "_downloader", None)
        if dl is not None and dl.is_running():
            return
        try:
            self._downloader = RegionDownloader(
                self.map_view.cache(), style=self.map_view.style_key(), parent=self)
            self._downloader.progress.connect(self._on_download_progress)
            self._downloader.finished.connect(self._on_download_finished)
            self._downloader.message.connect(self._on_download_message)
        except (AttributeError, RuntimeError, TypeError):
            self._downloader = None

    def _view_radius_km(self) -> float:
        """当前视野的"半径"（公里）：取视口长宽一半里较大的那个。"""
        try:
            lat_min, lon_min, lat_max, lon_max = self.map_view.visible_bounds()
        except (AttributeError, RuntimeError):
            return 20.0
        mid = (lat_min + lat_max) / 2.0
        dlat = abs(lat_max - lat_min) / 2.0 * 111.0
        dlon = (abs(lon_max - lon_min) / 2.0 * 111.0
                * max(0.05, math.cos(math.radians(mid))))
        return max(1.0, max(dlat, dlon))

    def download_plan(self):
        """当前视野要下的瓦片清单（纯计算，给确认框与测试看）。"""
        if RegionDownloader is None:
            return []
        try:
            z = int(self.map_view.zoom())
            lat, lon = self.map_view.center()
        except (AttributeError, RuntimeError):
            return []
        zmax = min(z + 2, 19)
        return RegionDownloader.plan(lat, lon, z, zmax, self._view_radius_km())

    def _on_download_clicked(self) -> None:
        if RegionDownloader is None:
            return
        dl = getattr(self, "_downloader", None)
        if dl is not None and dl.is_running():
            dl.cancel()                 # 按钮在下载时就是"取消"
            return
        jobs = self.download_plan()
        if not jobs:
            self.map_hint.setText("还没法下载：地图控件不可用")
            return
        if len(jobs) > 1500:
            QMessageBox.information(
                self, "范围太大",
                f"这个范围要下 {len(jobs)} 张瓦片，太多了（对方服务器不欢迎）。\n"
                "请先放大到城市级，或者缩小面板再试。")
            return
        est_mb = len(jobs) * 12.0 / 1024.0
        ret = QMessageBox.question(
            self, "下载这片地图",
            f"将下载 {len(jobs)} 张瓦片（约 {est_mb:.1f} MB，含当前层与下面两级）。\n"
            "下完之后这块区域断网也能看。继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._sync_downloader_style()
        dl = getattr(self, "_downloader", None)
        if dl is None:
            self.map_hint.setText("下载器不可用")
            return
        self.download_btn.setText("✕ 取消")
        dl.start_jobs(jobs)

    def _on_download_progress(self, done: int, total: int, skipped: int) -> None:
        if total <= 0:
            return
        self.map_hint.setText(
            f"下载中 {done}/{total}（已跳过 {skipped} 张已缓存）")

    def _on_download_message(self, msg: str) -> None:
        self.map_hint.setText(str(msg))

    def _on_download_finished(self, ok: int, bad: int) -> None:
        self.download_btn.setText("⤓ 下载")
        self.map_hint.setText(download_summary(ok, bad))

    def _on_map_zoom(self, zoom: int) -> None:
        # 守卫：地图控件在 _build_ui 里先于状态标签构造，构造期的
        # zoom_changed 会打到这里（2026-11-10 审计抓到的 AttributeError）。
        label = getattr(self, "map_zoom_label", None)
        if label is None:
            return
        try:
            label.setText(f"z{int(zoom)}")
        except RuntimeError:
            pass

    def _on_map_error(self, msg: str) -> None:
        """瓦片加载失败：说清楚原因，并强调离线仍能点坐标。"""
        hint = getattr(self, "map_hint", None)
        if hint is None:
            return
        try:
            hint.setText(str(msg))
            hint.setStyleSheet(
                "background: transparent; color: #ff8a7a; font-size: 11px;")
        except RuntimeError:
            pass

    # ------------------------------------------------------------------ 样式
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
            # ID 选择器优先级高于上面的 QPushButton:disabled；不补这条，
            # 没选点时的「用这个位置」仍是饱满蓝底白字，看着可点、点了没反应。
            "QPushButton#lpGo:disabled { background: %s; border-color: %s;"
            " color: #5b6478; }"
            "QPushButton#lpMapTool { padding: 1px 8px; font-size: 12px; }"
            % (_BG, _LINE, _TEXT, _DIM, _TEXT, _PANEL, _TEXT, _LINE,
               _PANEL, _LINE, _PANEL, _LINE, _PANEL, _LINE))

    # ------------------------------------------------------------- 对外 API
    def set_current(self, lat: Optional[float], lon: Optional[float],
                    source_zh: str = "") -> None:
        """显示当前站址（只读回显；面板不因此持有任何写入权）。"""
        if lat is None or lon is None:
            self._current = None
            self.current_label.setText("当前：未设置（天空按未定位空态）")
            self._set_map_marker(None, None)
            return
        self._current = (float(lat), float(lon))
        src = f" · 来源：{source_zh}" if source_zh else ""
        self.current_label.setText(
            f"当前：{float(lat):.4f}°, {float(lon):.4f}°{src}")
        self._set_map_marker(float(lat), float(lon))

    def fit_to_available(self, avail: QRect) -> None:
        """把面板摆进可用屏幕区域：先定型，再「不够高就缩地图」，最后居中夹住。

        面板是延迟创建的 ``Qt.Tool``：首次 ``show()`` 时由窗口管理器给一个
        初始位置，随后布局把窗口从很小撑到 632px 高，位置却不跟着收敛。
        实测 1000×600 窗口里落在 y=397、底边 1029——只露出上半截，
        「使用此地点 / 取消」整排在屏幕外，等于没法完成选点。

        高度不够时只压地图的最小高（唯一可伸缩的视觉主体），
        固定行（搜索、结果、手填、按钮）一律不动——它们才是可操作部分。
        另外要连**窗口边框**一起算，并上最大高度：只 resize 的话，随后到达
        的 LayoutRequest 会按 sizeHint 再长回去（实测 576 → 589）。
        """
        self.setMaximumHeight(16777215)   # 先解除上一次的限高
        self.adjustSize()
        margin = 12
        # 标题栏/边框不占客户区，但占屏幕；不扣掉就会"看着在框内、按钮出屏"。
        frame_extra = max(0, self.frameGeometry().height() - self.height())
        max_client_h = max(240, avail.height() - margin * 2 - frame_extra)
        max_w = max(360, (avail.width() - margin * 2))
        over = self.minimumSizeHint().height() - max_client_h
        view = getattr(self, "map_view", None)
        if over > 0 and view is not None:
            try:
                view.setMinimumHeight(max(120, view.minimumHeight() - over))
                self.adjustSize()
            except (AttributeError, RuntimeError):
                pass
        self.setMaximumHeight(max_client_h)
        w = min(max(self.width(), 460), max_w)
        h = min(self.height(), max_client_h)
        try:
            self.resize(w, h)
        except (AttributeError, RuntimeError):
            return
        self.move(avail.x() + (avail.width() - w) // 2,
                  avail.y() + (avail.height() - (h + frame_extra)) // 2)

    def _set_map_marker(self, lat: Optional[float], lon: Optional[float]) -> None:
        view = getattr(self, "map_view", None)
        if view is None or OsmMapView is None:
            return
        try:
            view.set_marker(lat, lon)
            if lat is not None and lon is not None:
                # 打开面板先看到「我在哪」，用户再自己拖走（不记住拖拽位置，
                # 免得下次打开停在上次的荒野里）
                view.set_view(lat, lon, max(int(view.zoom()), 6))
                self._on_map_zoom(view.zoom())
        except (AttributeError, RuntimeError):
            pass

    def _set_pick_marker(self, lat: Optional[float], lon: Optional[float]) -> None:
        """待确认选点的琥珀色标记；``None`` 清掉。站址标记不归它管。"""
        view = getattr(self, "map_view", None)
        if view is None or OsmMapView is None:
            return
        try:
            view.set_pick_marker(lat, lon)
        except (AttributeError, RuntimeError):
            pass

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

    def _show_list_hint(self, text: str) -> None:
        """列表空态：一条不可选的灰字提示（不是假结果）。"""
        try:
            self.result_list.clear()
            item = QListWidgetItem(str(text))
            item.setFlags(Qt.NoItemFlags)
            self.result_list.addItem(item)
        except (AttributeError, RuntimeError):
            pass

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
        # 只挪「待确认」标记（琥珀色），不动视图中心、也不动站址标记：
        # 用户正看着选中的这块地图，还没按「用这个位置」之前当前位置不该变
        self._set_pick_marker(place.lat, place.lon)
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
        self._set_pick_marker(None, None)     # 待确认已落地，预览标记收起
        self.apply_btn.setEnabled(False)
        self.hide()

    def _on_clear(self) -> None:
        self._pending = None
        self._set_pick_marker(None, None)
        self.apply_btn.setEnabled(False)
        self.pending_label.setText("")
        self._set_map_marker(None, None)
        self.cleared.emit()

    # ------------------------------------------------------------- 生命周期
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt)
        # 关闭只是藏起来（复用同一实例，不销毁）；正在跑的搜索允许它自己结束
        event.ignore()
        self.hide()
