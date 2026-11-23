# SPDX-License-Identifier: MIT
"""多设备网关对话框：显式开启、配对码、已配对设备名册（M2.1b）。

诚实边界
========
* 默认只绑回环；只有地址下拉里选到本机私有网段地址时，才向内核传
  ``allow_lan=True``。公网/通配地址根本不出现在下拉里。
* 网关本身没有任何写硬件/改站址端点；本对话框也不提供。
* 没有站址时仍可启动，但 ``/panorama`` 会回 500 并说明「先设置观测位置」，
  对话框状态栏同步提示——不拿示例站顶替。
* 关闭对话框会停止网关（不留下无人看管的监听）。
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
from typing import Callable, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from mbdsdr_ai.device_gateway import (  # noqa: E402
    DeviceGateway, build_station_panorama_provider,
)


def local_private_ips() -> List[str]:
    """本机可选私有 IPv4（不含回环；拿不到就空列表，不猜）。"""
    found = set()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if addr.version == 4 and not addr.is_loopback and addr.is_private:
                found.add(ip)
    except OSError:
        pass
    return sorted(found)


class DeviceGatewayDialog(QDialog):
    """开机/停机 + 配对码 + 名册；不提供任何写操作。"""

    def __init__(self, station_provider: Callable[[], Tuple[
                     Optional[float], Optional[float], Optional[float]]],
                 parent=None, *, port: int = 8771,
                 hosts: Optional[List[str]] = None,
                 registry=None):
        super().__init__(parent)
        self._station_provider = station_provider
        self._registry = registry
        self._gateway: Optional[DeviceGateway] = None
        self.setWindowTitle("多设备网关（手机/头显配对）")
        self.resize(640, 460)
        self._build_ui(port, hosts)
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._refresh_roster)
        self._timer.start()
        self._refresh_state()

    # ------------------------------------------------------------------
    def _build_ui(self, port: int, hosts: Optional[List[str]]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(8)

        tip = QLabel(
            "给第二台手机（镜头/目镜/手持）或头显配对用。网关只有只读端点，"
            "不改站址、不调谐、不发射；关闭本窗口会停止网关。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#5B7B8C;")
        outer.addWidget(tip)

        form = QFormLayout()
        self.host_combo = QComboBox()
        choices = ["127.0.0.1"] + list(hosts if hosts is not None
                                       else local_private_ips())
        self.host_combo.addItems(choices)
        self.host_combo.setToolTip(
            "127.0.0.1 只给本机/模拟器；选私有网段地址才允许局域网设备接入")
        form.addRow("监听地址", self.host_combo)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(0, 65535)
        self.port_spin.setValue(int(port))
        self.port_spin.setToolTip("0 表示随机端口（启动后按状态栏显示的地址为准）")
        form.addRow("端口", self.port_spin)

        self.allow_live_check = QCheckBox(
            "允许 /panorama 现采网络数据（默认只读 15 分钟内缓存）")
        form.addRow("", self.allow_live_check)
        outer.addLayout(form)

        row = QHBoxLayout()
        self.start_btn = QPushButton("启动网关")
        self.start_btn.clicked.connect(self.start_gateway)
        row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.clicked.connect(self.stop_gateway)
        row.addWidget(self.stop_btn)
        self.rotate_btn = QPushButton("轮换配对码")
        self.rotate_btn.clicked.connect(self.rotate_code)
        row.addWidget(self.rotate_btn)
        self.copy_btn = QPushButton("复制配对码")
        self.copy_btn.clicked.connect(self.copy_code)
        row.addWidget(self.copy_btn)
        row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        outer.addLayout(row)

        code_row = QHBoxLayout()
        code_row.addWidget(QLabel("配对码："))
        self.code_label = QLabel("—")
        font = self.code_label.font()
        font.setPointSize(max(font.pointSize() + 6, 14))
        font.setBold(True)
        self.code_label.setFont(font)
        self.code_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        code_row.addWidget(self.code_label)
        code_row.addStretch(1)
        outer.addLayout(code_row)

        self.url_label = QLabel("网关未启动")
        self.url_label.setWordWrap(True)
        self.url_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.url_label)

        self.fuse_label = QLabel("融合位姿：—")
        self.fuse_label.setWordWrap(True)
        self.fuse_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.fuse_label)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["角色", "设备名", "最近心跳"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        outer.addWidget(self.table, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

    # ------------------------------------------------------------------
    def _refresh_state(self) -> None:
        running = self._gateway is not None
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.rotate_btn.setEnabled(running)
        self.copy_btn.setEnabled(running)
        self.host_combo.setEnabled(not running)
        self.port_spin.setEnabled(not running)
        self.allow_live_check.setEnabled(not running)
        if not running:
            self.code_label.setText("—")
            self.url_label.setText("网关未启动")
            self.fuse_label.setText("融合位姿：—")
            self.table.setRowCount(0)
            self.status.setText("未启动。选地址后点「启动网关」。")

    def start_gateway(self) -> None:
        if self._gateway is not None:
            return
        host = self.host_combo.currentText().strip() or "127.0.0.1"
        allow_lan = host != "127.0.0.1"
        provider = build_station_panorama_provider(
            self._station_provider,
            allow_live=self.allow_live_check.isChecked(),
            site_note="桌面权威端")
        try:
            gw = DeviceGateway(host=host, port=self.port_spin.value(),
                               allow_lan=allow_lan,
                               registry=self._registry,
                               panorama_provider=provider)
            url = gw.start()
        except Exception as exc:  # noqa: BLE001 - 启动失败必须可见
            QMessageBox.warning(self, "网关启动失败",
                                f"{type(exc).__name__}: {exc}")
            self.status.setText(f"启动失败：{exc}")
            return
        self._gateway = gw
        self.port_spin.setValue(gw.port)
        self.url_label.setText(f"服务地址：{url}")
        self.code_label.setText(gw.registry.code)
        self._refresh_state()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.rotate_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)
        la, lo, _ = self._safe_station()
        self._station_missing = la is None or lo is None
        if self._station_missing:
            self.status.setText(
                "网关已启动，但当前未设置站址：/panorama 会回 500，"
                "请先设置观测位置（GNSS/手填）。")
        else:
            self.status.setText(
                "网关已启动。手机/头显用配对码换 token 后即可读 /panorama、"
                "/roster；没有写硬件端点。")
        self._refresh_roster()

    def _safe_station(self):
        try:
            return self._station_provider()
        except Exception:
            return None, None, None

    def stop_gateway(self) -> None:
        gw = self._gateway
        self._gateway = None
        self._station_missing = False
        if gw is not None:
            try:
                gw.stop()
            except Exception:
                pass
        self._refresh_state()
        self.status.setText("网关已停止。")

    def rotate_code(self) -> None:
        if self._gateway is None:
            return
        code = self._gateway.registry.rotate_code()
        self.code_label.setText(code)
        self.status.setText("配对码已轮换：旧码立即失效，已配对设备不受影响。")

    def copy_code(self) -> None:
        if self._gateway is None:
            return
        QGuiApplication.clipboard().setText(self.code_label.text())
        self.status.setText("配对码已复制到剪贴板。")

    def _refresh_roster(self) -> None:
        gw = self._gateway
        if gw is None:
            return
        try:
            roster = gw.registry.roster()
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"名册读取失败：{exc}")
            return
        devices = roster.get("devices") or []
        self.table.setRowCount(len(devices))
        for row, dev in enumerate(devices):
            values = [str(dev.get("role") or ""), str(dev.get("name") or ""),
                      str(dev.get("last_seen") or "")]
            for col, text in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(text))
        poses = gw.recent_poses()
        self._update_fuse_label(gw)
        if getattr(self, "_station_missing", False):
            self.status.setText(
                f"已配对 {len(devices)} 台；当前未设置站址：/panorama 会回 500"
                "（先设置观测位置）。")
        elif poses:
            self.status.setText(
                f"已配对 {len(devices)} 台；收到 {len(poses)} 个设备的位姿。")
        else:
            self.status.setText(f"已配对 {len(devices)} 台；暂无位姿上报。")

    def _update_fuse_label(self, gw: DeviceGateway) -> None:
        """把 lens+eyepiece 的融合结果如实显示；不可用时给原因，不猜朝向。"""
        try:
            fused = gw.fuse_now()
        except Exception as exc:  # noqa: BLE001 - 失败要可见
            self.fuse_label.setText(f"融合位姿：计算失败（{exc}）")
            return
        if not fused.get("valid"):
            reason = str(fused.get("reason") or "未知原因")
            self.fuse_label.setText(f"融合位姿：不可用（{reason}）")
            return
        level = {"single": "单条", "fused": "多机融合"}.get(
            str(fused.get("level")), str(fused.get("level")))
        self.fuse_label.setText(
            "融合位姿：方位 {az:.1f}° 高度 {alt:.1f}° · 精度 {acc:.2f}°"
            " · {level}（{n} 条观测：{src}）".format(
                az=float(fused["az_deg"]), alt=float(fused["alt_deg"]),
                acc=float(fused["accuracy_deg"]), level=level,
                n=fused.get("observations", 0),
                src=", ".join(str(s) for s in fused.get("sources") or [])))

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self.stop_gateway()
        super().closeEvent(event)

    def reject(self) -> None:
        self.stop_gateway()
        super().reject()
