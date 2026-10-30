# SPDX-License-Identifier: MIT
"""
MBDSDR 服务设置面板（desktop/settings_panel.py）
================================================

把内核两个对外服务接成独立开关：
  * 远程控制 TCP（GQRX/hamlib rigctld 风格，默认 127.0.0.1:7356）
  * Web 服务（HTTP + WebSocket 频谱/音频流，端口可配，默认 8080）

红线：
  * **默认全部关闭**，用户显式勾选才监听端口；关闭即 stop。
  * 无后端时内核已处理：远程控制写命令返回 RPRT 1，Web /api/status 返回
    {"connected": false}，本面板不造假连接状态。
  * 端口被占用等异常时在状态行提示，不崩主窗口。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox, QCheckBox, QSpinBox, QLabel,
    QFormLayout,
)

try:  # pragma: no cover
    from mbdsdr_ai.remote_control import RemoteControl, DEFAULT_RC_PORT
except Exception:  # noqa: BLE001
    class RemoteControl:  # type: ignore
        def __init__(self, backend=None, host="127.0.0.1", port=7356):
            self._backend = backend
            self.host = host
            self.port = port

        def start(self):
            raise RuntimeError("remote_control 内核不可用")

        def stop(self):
            pass
    DEFAULT_RC_PORT = 7356

try:  # pragma: no cover
    from mbdsdr_ai.web.server import WebServer
    _WEB_OK = True
except Exception:  # noqa: BLE001
    WebServer = None  # type: ignore
    _WEB_OK = False

try:  # pragma: no cover
    from settings import DesktopSettings
except Exception:  # noqa: BLE001
    DesktopSettings = None  # type: ignore

DEFAULT_WEB_PORT = 8080


class ServiceSettingsPanel(QWidget):
    """远程控制 / Web 服务开关面板。"""

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._backend = None
        self._rc: Optional[RemoteControl] = None
        self._web = None
        # 运行参数持久化（desktop_settings.json）。None = 调用方未注入，
        # 此时开关状态不落盘（保持旧行为），绝不因此崩面板。
        self._settings = settings
        self._build_ui()
        self._restore_from_settings()

    # ------------------------------------------------------------ 设置持久化
    def attach_settings(self, settings):
        """后注入配置对象（面板先于 settings 构造时用），并立刻回填表单。"""
        self._settings = settings
        self._restore_from_settings()

    def _restore_from_settings(self):
        """把持久化的开关/端口回填到控件。**只回填，不自动起服务**。

        监听端口属于对外暴露面，恢复动作必须由主窗口在 UI 就绪后显式调用
        :meth:`restore_enabled_services` 触发，避免构造期副作用。
        """
        if self._settings is None:
            return
        try:
            rc_on = bool(self._settings.get("rc_enabled", False))
            self.rc_check.blockSignals(True)
            self.rc_check.setChecked(rc_on)
            self.rc_check.blockSignals(False)
            self.rc_port.setEnabled(rc_on)
            self.rc_port.setValue(int(self._settings.get("rc_port", DEFAULT_RC_PORT)))
        except Exception:  # noqa: BLE001
            pass
        try:
            web_on = bool(self._settings.get("web_enabled", False))
            self.web_check.blockSignals(True)
            self.web_check.setChecked(web_on)
            self.web_check.blockSignals(False)
            self.web_port.setEnabled(web_on)
            self.web_port.setValue(int(self._settings.get("web_port", DEFAULT_WEB_PORT)))
        except Exception:  # noqa: BLE001
            pass

    def _persist(self, key: str, value) -> None:
        """写一项设置；未注入 settings 时静默跳过（不崩、不谎报已保存）。"""
        if self._settings is None:
            return
        try:
            self._settings.set(key, value)
        except Exception:  # noqa: BLE001
            pass

    def restore_enabled_services(self):
        """按持久化的上次状态恢复服务：仅当用户上次显式启用过才重新监听。

        默认全关仍然成立（DEFAULTS 为 False）；这里恢复的是用户自己的显式意图，
        避免"勾了重启又关"这种反人类行为。
        """
        if self._settings is None:
            return
        try:
            if bool(self._settings.get("rc_enabled", False)) and self._rc is None:
                self._start_rc()
            if bool(self._settings.get("web_enabled", False)) and self._web is None:
                self._start_web()
        except Exception:  # noqa: BLE001
            pass

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        # ---- 远程控制 TCP ----
        rc_box = QGroupBox("远程控制 (TCP, GQRX/hamlib 风格)")
        rc_form = QFormLayout(rc_box)
        self.rc_check = QCheckBox("启用远程控制")
        self.rc_check.toggled.connect(self._on_rc_toggled)
        self.rc_port = QSpinBox()
        self.rc_port.setRange(1, 65535)
        self.rc_port.setValue(int(DEFAULT_RC_PORT))
        self.rc_port.setEnabled(False)
        self.rc_port.valueChanged.connect(self._on_rc_port_changed)
        self.rc_status = QLabel("状态：未监听")
        self.rc_addr = QLabel("127.0.0.1")
        rc_form.addRow(self.rc_check)
        rc_form.addRow("监听地址", self.rc_addr)
        rc_form.addRow("端口", self.rc_port)
        rc_form.addRow(self.rc_status)
        root.addWidget(rc_box)

        # ---- Web 服务 ----
        web_box = QGroupBox("Web 服务 (HTTP + WebSocket)")
        web_form = QFormLayout(web_box)
        self.web_check = QCheckBox("启用 Web 服务")
        self.web_check.toggled.connect(self._on_web_toggled)
        self.web_port = QSpinBox()
        self.web_port.setRange(1, 65535)
        self.web_port.setValue(DEFAULT_WEB_PORT)
        self.web_port.setEnabled(False)
        self.web_port.valueChanged.connect(self._on_web_port_changed)
        self.web_status = QLabel("状态：未启动")
        self.web_url = QLabel("URL：--")
        web_form.addRow(self.web_check)
        web_form.addRow("端口", self.web_port)
        web_form.addRow(self.web_status)
        web_form.addRow(self.web_url)
        root.addWidget(web_box)

        note = QLabel("默认全部关闭。无硬件时远程命令与 Web 状态接口均如实返回"
                      "「未连接」，不伪造数据。")
        note.setWordWrap(True)
        note.setObjectName("hintLabel")
        root.addWidget(note)
        root.addStretch(1)

    # ------------------------------------------------------------ 外部接线
    def set_backend(self, backend):
        """注入真实 SDR 后端（None 时服务仍可起，但状态如实报未连接）。"""
        self._backend = backend
        # 正在运行的服务后端引用是启动时绑定的；这里只记录，不热重启

    # ------------------------------------------------------------ 远程控制
    def _on_rc_toggled(self, on: bool):
        self.rc_port.setEnabled(on)
        self._persist("rc_enabled", bool(on))
        if on:
            self._start_rc()
        else:
            self._stop_rc()

    def _on_rc_port_changed(self, value: int):
        """端口改动即落盘；正在监听时不静默换端口，如实告知需重启服务。"""
        self._persist("rc_port", int(value))
        if self._rc is not None:
            self.rc_status.setText(
                f"状态：监听中  127.0.0.1:{self.rc_port.value()}"
                f"（端口已保存，重启服务后生效）")

    def _start_rc(self):
        try:
            self._rc = RemoteControl(backend=self._backend,
                                     host="127.0.0.1",
                                     port=int(self.rc_port.value()))
            self._rc.start()
            self.rc_status.setText(
                f"状态：监听中  127.0.0.1:{self.rc_port.value()}")
        except Exception as e:  # noqa: BLE001
            self.rc_status.setText(f"状态：启动失败 ({e})")
            self.rc_check.blockSignals(True)
            self.rc_check.setChecked(False)
            self.rc_check.blockSignals(False)
            self.rc_port.setEnabled(False)
            self._rc = None

    def _stop_rc(self):
        self.rc_status.setText("状态：未监听")
        if self._rc is not None:
            try:
                self._rc.stop()
            except Exception:  # noqa: BLE001
                pass
            self._rc = None

    # ------------------------------------------------------------ Web 服务
    def _on_web_toggled(self, on: bool):
        self.web_port.setEnabled(on)
        self._persist("web_enabled", bool(on))
        if on:
            self._start_web()
        else:
            self._stop_web()

    def _on_web_port_changed(self, value: int):
        self._persist("web_port", int(value))
        if self._web is not None:
            self.web_status.setText("状态：运行中（端口已保存，重启服务后生效）")

    def _start_web(self):
        if not _WEB_OK or WebServer is None:
            self.web_status.setText("状态：Web 内核不可用")
            self.web_check.blockSignals(True)
            self.web_check.setChecked(False)
            self.web_check.blockSignals(False)
            return
        try:
            self._web = WebServer(host="127.0.0.1",
                                  port=int(self.web_port.value()),
                                  backend=self._backend)
            self._web.start()
            port = getattr(self._web, "port", self.web_port.value())
            self.web_status.setText(f"状态：运行中")
            self.web_url.setText(f"URL：http://127.0.0.1:{port}/")
        except Exception as e:  # noqa: BLE001
            self.web_status.setText(f"状态：启动失败 ({e})")
            self.web_check.blockSignals(True)
            self.web_check.setChecked(False)
            self.web_check.blockSignals(False)
            self.web_port.setEnabled(False)
            self._web = None

    def _stop_web(self):
        self.web_status.setText("状态：未启动")
        self.web_url.setText("URL：--")
        if self._web is not None:
            try:
                self._web.stop()
            except Exception:  # noqa: BLE001
                pass
            self._web = None

    # ------------------------------------------------------------ 生命周期
    def shutdown_all(self):
        """主窗口关闭时停掉两个服务（防止 daemon 线程泄漏到测试）。"""
        self._stop_rc()
        self._stop_web()
