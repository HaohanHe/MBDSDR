"""
NTRIP 图形化配置面板
=====================

NtripConfigDialog(QDialog) —— 非模态对话框，运行主窗口时可保持打开。

功能：
  - 表单：caster host / port(默认2101) / mountpoint(可编辑下拉) / 用户名 / 密码
  - 「拉取挂载点列表」：向 caster 发 HTTP GET /（带 Ntrip-Version: Ntrip/2.0 头），
    解析 sourcetable 的 STR 行填充 mountpoint 下拉框；失败弹提示但不崩溃。
  - 「连接 / 断开」：复用 mbdsdr_ai.rtklib_adapter.NTRIPManager。
  - 状态指示灯：红=未连接 / 黄=连接中 / 绿=已连接(字节数: N)。
  - QTimer 1s 轮询 NTRIPManager.stats() 刷新收流字节数。

风格：遵循 desktop/themes.py 日式低饱和主题，原生 PySide6，不引入 web。
密码仅明文存本地 ~/.mbdsdr/gui_config.json，绝不写入日志。
"""

from __future__ import annotations

import os
import sys
import threading
import urllib.request
import urllib.error
from typing import Dict, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPixmap, QPainter, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLineEdit, QSpinBox,
    QComboBox, QPushButton, QLabel, QMessageBox, QGroupBox, QWidget,
)

# 确保能导入仓库根下的 mbdsdr_ai 包
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from mbdsdr_ai.rtklib_adapter import NTRIPManager, NTRIP_DEFAULT_PORT  # noqa: E402

# 日式低饱和主题里的指示灯颜色（与 themes.py spectrum_colors 对齐，不重色）
_COLOR_IDLE = "#B85C5C"     # 低饱和红 = 未连接/未配置
_COLOR_BUSY = "#C4B85C"     # 低饱和黄 = 连接中
_COLOR_OK = "#6BA89A"       # 低饱和青绿 = 已连接


def _make_dot(color_hex: str, size: int = 12) -> QPixmap:
    """画一个圆形状态点 QPixmap（抗锯齿、低饱和填充 + 细描边）。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setBrush(QBrush(QColor(color_hex)))
    p.setPen(QColor(color_hex).darker(120))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return pm


def parse_sourcetable(text: str) -> list:
    """从 NTRIP sourcetable 文本里提取 STR 行的 mountpoint 列表。

    NTRIP 1.0 sourcetable STR 记录（逗号分隔）：
        STR,<country>,<lat>,<lon>,<ID>,<operator>,<format>,<format-detail>,
            <nav-system>,<network>,<mountpoint>,<solution>,<generator>,
            <encrypt>,<auth>,<fee>,<bitrate>
    mountpoint 为第 11 个字段（index 10）。解析失败/字段不足时安全跳过。
    """
    mounts = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("STR,"):
            continue
        parts = line.split(",")
        if len(parts) <= 10:
            continue
        mp = parts[10].strip()
        if mp and mp not in mounts:
            mounts.append(mp)
    return mounts


class NtripConfigDialog(QDialog):
    """NTRIP caster 配置对话框（非模态）。

    用法：
        dlg = NtripConfigDialog(parent=main_window)
        dlg.apply_config(saved_dict)   # 从 gui_config.json 回填
        dlg.show()                     # 非模态
        # 关闭/保存时 main_window 调 dlg.get_config() 取当前配置
    """

    #: 后台线程连接完成后回主线程更新 UI（线程安全）
    _connect_finished = Signal(bool)

    def __init__(self, parent: Optional[QWidget] = None,
                 manager: Optional[NTRIPManager] = None):
        super().__init__(parent)
        self.setWindowTitle("NTRIP 设置")
        self.setMinimumWidth(460)
        self.setModal(False)  # 非模态：主窗口运行时可保持打开

        # 复用外部传入的 manager，否则自建一个（测试时可 mock）
        self._manager: NTRIPManager = manager if manager is not None else NTRIPManager()

        self._build_ui()

        # 1s 轮询 manager 收流字节数 + 连接状态
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._refresh_status)
        self._poll_timer.start()

        # 后台连接结果回主线程
        self._connect_finished.connect(self._on_connect_finished)

        self._set_state("idle")

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # ---- 状态行：颜色圆点 + 文字 ----
        status_row = QHBoxLayout()
        self._dot_label = QLabel()
        self._dot_label.setFixedWidth(18)
        self._state_label = QLabel("未连接")
        self._state_label.setStyleSheet("font-weight: 600;")
        status_row.addWidget(self._dot_label)
        status_row.addWidget(self._state_label)
        status_row.addStretch()
        root.addLayout(status_row)

        # ---- caster 表单 ----
        group = QGroupBox("Caster 配置")
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignRight)

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("ntrip.example.com")
        form.addRow("Caster Host:", self.host_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(NTRIP_DEFAULT_PORT)  # 默认 2101
        form.addRow("Port:", self.port_spin)

        # mountpoint 下拉（可编辑，允许手输）
        self.mount_combo = QComboBox()
        self.mount_combo.setEditable(True)
        self.mount_combo.setMinimumWidth(220)
        form.addRow("Mountpoint:", self.mount_combo)

        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("anonymous")
        form.addRow("用户名:", self.user_edit)

        self.pass_edit = QLineEdit()
        self.pass_edit.setEchoMode(QLineEdit.Password)  # 密码遮挡
        self.pass_edit.setPlaceholderText("(本地明文保存，不打印日志)")
        form.addRow("密码:", self.pass_edit)

        root.addWidget(group)

        # ---- 拉取挂载点列表 ----
        pull_row = QHBoxLayout()
        self.pull_btn = QPushButton("拉取挂载点列表")
        self.pull_btn.clicked.connect(self._pull_mountpoints)
        pull_row.addStretch()
        pull_row.addWidget(self.pull_btn)
        root.addLayout(pull_row)

        # ---- 连接 / 断开 ----
        btn_row = QHBoxLayout()
        self.connect_btn = QPushButton("连接")
        self.connect_btn.clicked.connect(self._on_connect_clicked)
        self.disconnect_btn = QPushButton("断开")
        self.disconnect_btn.clicked.connect(self._on_disconnect_clicked)
        self.disconnect_btn.setEnabled(False)
        btn_row.addStretch()
        btn_row.addWidget(self.connect_btn)
        btn_row.addWidget(self.disconnect_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    # 配置读写（供 main_window 持久化往返）
    # ------------------------------------------------------------------
    def get_config(self) -> Dict[str, object]:
        """取当前表单值，返回可写入 gui_config.json 的 ntrip 段。

        注意：键名用 "username"（与任务约定一致）；密码明文返回，绝不 log。
        """
        return {
            "host": self.host_edit.text().strip(),
            "port": int(self.port_spin.value()),
            "mountpoint": self.mount_combo.currentText().strip(),
            "username": self.user_edit.text(),
            "password": self.pass_edit.text(),
        }

    def apply_config(self, cfg: Optional[Dict]) -> None:
        """把 gui_config.json 里的 ntrip 段回填到表单（缺字段安全兜底）。"""
        if not isinstance(cfg, dict):
            return
        self.host_edit.setText(str(cfg.get("host", "") or ""))
        try:
            self.port_spin.setValue(int(cfg.get("port", NTRIP_DEFAULT_PORT)
                                        or NTRIP_DEFAULT_PORT))
        except (TypeError, ValueError):
            self.port_spin.setValue(NTRIP_DEFAULT_PORT)
        mp = str(cfg.get("mountpoint", "") or "")
        if mp:
            # 已有项则选中，否则先加进去（可编辑下拉）
            idx = self.mount_combo.findText(mp)
            if idx >= 0:
                self.mount_combo.setCurrentIndex(idx)
            else:
                self.mount_combo.addItem(mp)
                self.mount_combo.setCurrentText(mp)
        self.user_edit.setText(str(cfg.get("username", "") or ""))
        self.pass_edit.setText(str(cfg.get("password", "") or ""))

    # ------------------------------------------------------------------
    # 拉取挂载点列表（HTTP GET / + Ntrip-Version 头）
    # ------------------------------------------------------------------
    def _pull_mountpoints(self):
        host = self.host_edit.text().strip()
        if not host:
            QMessageBox.warning(self, "拉取失败", "请先填写 Caster Host。")
            return
        port = int(self.port_spin.value())
        url = f"http://{host}:{port}/"

        self.pull_btn.setEnabled(False)
        self.pull_btn.setText("拉取中...")
        # 网络请求放后台线程，避免卡死 UI
        threading.Thread(
            target=self._pull_worker, args=(url,), daemon=True
        ).start()

    def _pull_worker(self, url: str):
        mounts = []
        err: Optional[str] = None
        try:
            req = urllib.request.Request(url, headers={
                "Ntrip-Version": "Ntrip/2.0",
                "User-Agent": "MBDSDR-NTRIP/0.1",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                raw = resp.read()
            text = raw.decode("latin-1", "ignore")
            mounts = parse_sourcetable(text)
            if not mounts:
                err = "sourcetable 中未找到 STR 挂载点记录。"
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code} {e.reason}"
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            err = f"网络错误: {e}"
        except Exception as e:  # 兜底：拉取失败绝不崩
            err = f"未知错误: {e}"

        # 回主线程更新 UI
        def _apply():
            self.pull_btn.setEnabled(True)
            self.pull_btn.setText("拉取挂载点列表")
            if err is not None and not mounts:
                QMessageBox.warning(self, "拉取挂载点失败", err)
                return
            # 保留当前已填的 mountpoint，其余清空后填入
            current = self.mount_combo.currentText()
            self.mount_combo.clear()
            self.mount_combo.addItems(mounts)
            if current:
                idx = self.mount_combo.findText(current)
                self.mount_combo.setCurrentIndex(idx if idx >= 0 else 0)
            QMessageBox.information(
                self, "拉取完成", f"共获取 {len(mounts)} 个挂载点。")
        # QTimer.singleShot(0,...) 在 offscreen/pytest 下也安全
        QTimer.singleShot(0, _apply)

    # ------------------------------------------------------------------
    # 连接 / 断开（复用 NTRIPManager）
    # ------------------------------------------------------------------
    def _on_connect_clicked(self):
        cfg = self.get_config()
        if not cfg["host"] or not cfg["mountpoint"]:
            QMessageBox.warning(
                self, "无法连接", "请填写 Caster Host 与 Mountpoint。")
            return

        # 构造 NTRIPManager 配置（注意 NTRIPManager.load_config 读 "user" 键）
        mgr_cfg = {
            "host": cfg["host"],
            "port": cfg["port"],
            "mountpoint": cfg["mountpoint"],
            "user": cfg["username"],
            "password": cfg["password"],
            "enabled": True,
        }
        try:
            self._manager.load_config(mgr_cfg)
        except Exception:
            pass

        self._set_state("busy")
        self.connect_btn.setEnabled(False)

        # NTRIPStream.connect() 会阻塞在 socket 握手（最长 timeout），放后台线程
        def _worker():
            ok = False
            try:
                ok = bool(self._manager.start())
            except Exception:
                ok = False
            self._connect_finished.emit(ok)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_connect_finished(self, ok: bool):
        self.connect_btn.setEnabled(True)
        if ok:
            self._set_state("connected")
        else:
            self._set_state("idle")
            QMessageBox.warning(
                self, "连接失败",
                "无法连接 NTRIP caster。\n请检查 host/port/挂载点/账号后重试。")

    def _on_disconnect_clicked(self):
        try:
            self._manager.stop()
        except Exception:
            pass
        self._set_state("idle")

    # ------------------------------------------------------------------
    # 状态指示（红/黄/绿 + 字节数）
    # ------------------------------------------------------------------
    def _set_state(self, state: str):
        """state: idle / busy / connected / unconfigured。"""
        if state == "connected":
            color, text = _COLOR_OK, "已连接"
        elif state == "busy":
            color, text = _COLOR_BUSY, "连接中..."
        elif state == "unconfigured":
            color, text = _COLOR_IDLE, "未配置"
        else:
            color, text = _COLOR_IDLE, "未连接"
        self._dot_label.setPixmap(_make_dot(color))
        self._state_label.setText(text)
        self._state_label.setStyleSheet(f"color: {color}; font-weight: 600;")
        # 按钮可用性
        if state == "connected":
            self.disconnect_btn.setEnabled(True)
            self.connect_btn.setEnabled(False)
        elif state == "busy":
            self.disconnect_btn.setEnabled(False)
            self.connect_btn.setEnabled(False)
        else:
            self.disconnect_btn.setEnabled(False)
            self.connect_btn.setEnabled(True)
        self._refresh_bytes_only()

    def _refresh_bytes_only(self):
        """仅刷新字节数后缀（_set_state 与 1s 轮询共用）。"""
        base = self._state_label.text().split(" (字节数")[0]
        try:
            stats = self._manager.stats()
        except Exception:
            stats = {}
        if base == "已连接":
            n = int(stats.get("bytes_received", 0) or 0)
            self._state_label.setText(f"已连接 (字节数: {n})")
        else:
            self._state_label.setText(base)

    def _refresh_status(self):
        """1s QTimer：根据 manager.stats() 同步连接状态与字节数。"""
        try:
            stats = self._manager.stats()
            connected = bool(stats.get("connected", False))
        except Exception:
            connected = False
        cur = self._state_label.text()
        if connected:
            if cur.split(" (字节数")[0] != "已连接":
                self._set_state("connected")
            else:
                self._refresh_bytes_only()
        else:
            # 若之前认为已连接但流断了，回落未连接
            if cur.split(" (字节数")[0] == "已连接":
                self._set_state("idle")

    # ------------------------------------------------------------------
    # 关闭时清理
    # ------------------------------------------------------------------
    def closeEvent(self, event):
        try:
            self._poll_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)
