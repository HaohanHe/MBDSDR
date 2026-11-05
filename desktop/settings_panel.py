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

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox, QCheckBox, QSpinBox,
    QDoubleSpinBox, QComboBox, QLineEdit, QLabel, QFormLayout, QTabWidget,
    QScrollArea, QPushButton, QToolButton, QMessageBox, QFileDialog,
    QFrame,
)

# 单一事实源：默认值/范围/文案全部来自 config_schema，UI 不写第二份。
try:
    from config_schema import (
        CONFIG_SCHEMA_VERSION, EXPOSED_ITEMS, GROUP_ORDER, SCHEMA,
        ImportResult, build_export, items_for_group, parse_import,
        validate_value,
    )
except ImportError:  # pragma: no cover - 包内导入
    from .config_schema import (
        CONFIG_SCHEMA_VERSION, EXPOSED_ITEMS, GROUP_ORDER, SCHEMA,
        ImportResult, build_export, items_for_group, parse_import,
        validate_value,
    )

try:  # 静噪刻度→余量的唯一换算入口；缺失时按钮仍可用，只是不显示换算后缀
    from mbdsdr_ai.squelch import margin_threshold_db as _margin_threshold_db
except ImportError:  # pragma: no cover - 裁剪打包
    _margin_threshold_db = None

try:  # pragma: no cover - 可选内核：只捕导入缺失（其它异常是要修的 bug）
    from mbdsdr_ai.remote_control import RemoteControl, DEFAULT_RC_PORT
except ImportError:
    _log.warning("remote_control 内核不可用：服务设置面板降级，启动会如实报失败")

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
except ImportError:
    _log.warning("Web 服务内核不可用：服务设置面板降级，启动会如实报失败")
    WebServer = None  # type: ignore
    _WEB_OK = False

try:  # pragma: no cover
    from settings import DesktopSettings, backup_corrupt_config, resolve_record_dir
except ImportError:  # 包内导入
    try:
        from .settings import (
            DesktopSettings, backup_corrupt_config, resolve_record_dir)
    except ImportError:
        _log.warning("settings 模块不可用：设置面板无法持久化，改动只存内存")
        DesktopSettings = None  # type: ignore
        backup_corrupt_config = None  # type: ignore
        resolve_record_dir = None  # type: ignore

#: 端口默认值同样取自 schema（UI 不另写一份默认值）
DEFAULT_RC_PORT = int(SCHEMA["rc_port"].default)
DEFAULT_WEB_PORT = int(SCHEMA["web_port"].default)


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
        except (TypeError, ValueError) as exc:
            self._notify(f"远程控制设置回填失败（按默认显示）：{exc}")
        try:
            web_on = bool(self._settings.get("web_enabled", False))
            self.web_check.blockSignals(True)
            self.web_check.setChecked(web_on)
            self.web_check.blockSignals(False)
            self.web_port.setEnabled(web_on)
            self.web_port.setValue(int(self._settings.get("web_port", DEFAULT_WEB_PORT)))
        except (TypeError, ValueError) as exc:
            self._notify(f"Web 服务设置回填失败（按默认显示）：{exc}")

    def _persist(self, key: str, value) -> None:
        """写一项设置；失败必须显示出来（否则用户以为保存了、重启却丢了）。"""
        if self._settings is None:
            self._notify("未挂载配置存储：本次改动只存在当前会话")
            return
        try:
            self._settings.set(key, value)
        except (TypeError, ValueError) as exc:
            self._notify(f"{key} 保存失败：{exc}（改动可能重启后丢失）")

    def restore_enabled_services(self):
        """按持久化的上次状态恢复服务：仅当用户上次显式启用过才重新监听。

        默认全关仍然成立（DEFAULTS 为 False）；这里恢复的是用户自己的显式意图，
        避免"勾了重启又关"这种反人类行为。
        """
        if self._settings is None:
            return
        if bool(self._settings.get("rc_enabled", False)) and self._rc is None:
            self._start_rc()
        if bool(self._settings.get("web_enabled", False)) and self._web is None:
            self._start_web()

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
        self.rc_port.setRange(int(SCHEMA["rc_port"].minimum),
                              int(SCHEMA["rc_port"].maximum))
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
        self.web_port.setRange(int(SCHEMA["web_port"].minimum),
                               int(SCHEMA["web_port"].maximum))
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

        self.reset_btn = QPushButton("恢复本组默认")
        self.reset_btn.setToolTip("把远程控制与 Web 服务恢复到默认（全部关闭、端口复位）")
        self.reset_btn.clicked.connect(self.reset_defaults)
        root.addWidget(self.reset_btn)

        note = QLabel("默认全部关闭。无硬件时远程命令与 Web 状态接口均如实返回"
                      "「未连接」，不伪造数据。")
        note.setWordWrap(True)
        note.setObjectName("hintLabel")
        root.addWidget(note)
        # 保存失败/回填失败的用户可见状态行（不许静默吞掉配置层错误）
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName("hintLabel")
        root.addWidget(self.error_label)
        root.addStretch(1)

    def _notify(self, msg: str):
        """把失败原因写到面板状态行（用户可见，不是日志里自嗨）。"""
        self.error_label.setText(msg)

    def refresh_from_settings(self):
        """外部（设置面板的恢复默认/导入）改完 settings 后回填本面板控件。"""
        self._restore_from_settings()

    def reset_defaults(self):
        """本组恢复默认：4 个键回 schema 默认，正在监听的服务随之停止。"""
        if self._settings is None:
            return
        for key in ("rc_enabled", "rc_port", "web_enabled", "web_port"):
            self._settings.reset(key)
        self._restore_from_settings()
        self._stop_rc()
        self._stop_web()
        self.rc_port.setEnabled(False)
        self.web_port.setEnabled(False)

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
        except (OSError, RuntimeError, ValueError) as e:
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
            except (OSError, RuntimeError) as exc:
                self.rc_status.setText(f"状态：停止失败 ({exc})")
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
        except (OSError, RuntimeError, ValueError) as e:
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
            except (OSError, RuntimeError) as exc:
                self.web_status.setText(f"状态：停止失败 ({exc})")
            self._web = None

    # ------------------------------------------------------------ 生命周期
    def shutdown_all(self):
        """主窗口关闭时停掉两个服务（防止 daemon 线程泄漏到测试）。"""
        self._stop_rc()
        self._stop_web()


# ============================================================================
# 配置面板（按 config_schema 渲染；服务组复用上面的 ServiceSettingsPanel）
# ============================================================================
AI_CONFIG_PATH = os.path.expanduser("~/.mbdsdr/config.json")
GUI_CONFIG_PATH = os.path.expanduser("~/.mbdsdr/gui_config.json")


def _load_json_dict(path: str, on_error=None) -> Dict[str, Any]:
    """读 JSON 对象；缺失/损坏返回 {}。损坏时先备份证据（复用既有机制）。

    只捕获文件/解析边界（OSError/ValueError）；损坏会被改名备份并通过
    ``on_error`` 上报，绝不静默把用户配置当成"从来没写过"。
    """
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError) as exc:
        backup = None
        if backup_corrupt_config is not None:
            backup = backup_corrupt_config(path)
        if on_error is not None:
            on_error(f"配置读取失败（{path}）：{exc}；"
                     f"已备份为 {backup or '（备份失败）'}，相关参数按默认值显示")
        return {}


def _atomic_write_json(path: str, data: Dict[str, Any], mode: Optional[int] = None):
    """临时文件 + os.replace 原子写（与 settings/gui_config 同一套纪律）。"""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    if mode is not None:
        try:
            os.chmod(path, mode)
        except OSError:
            pass   # Windows 上 chmod 只影响只读位，失败不影响配置可用性


class SettingsPanel(QWidget):
    """按 schema 渲染的设置面板：分组页签 + 单项/全部恢复默认 + 导入导出。

    红线：
      * 默认值/范围/文案只从 config_schema 读，本文件不写第二份；
      * 改错不崩：数值控件天然限范围，外部导入走 validate_value 夹取并汇报；
      * 立即生效：每一项都调用主窗口既有公开入口；确实要重启的（界面语言）
        在行内显式标注"重启生效"；
      * 未接入主窗口（host=None）时仍可查看/落盘，但改值会如实提示未生效。
    """

    def __init__(self, parent=None, settings=None, host=None):
        super().__init__(parent)
        self._settings = settings
        self._host = host
        self._rows: Dict[str, QWidget] = {}
        self._building = False
        self._host_signals_wired = False
        self._ai = _load_json_dict(AI_CONFIG_PATH)
        self._build_ui()
        self._refresh_from_storage()
        self._wire_host_signals()
        # 一次性迁移提示要在回推默认值之前（避免自己写盘把"从未有过"变成"已有"）
        self._announce_squelch_migration()
        # 持久化的 Bortle/静噪/步进在启动时推给消费点（默认值与内置默认一致；
        # 用户改过的话重启后仍是用户值，不是"改了白改"）。
        self._apply_initial_state()

    # ------------------------------------------------------------ 外部接线
    def attach_settings(self, settings):
        self._settings = settings
        self._refresh_from_storage()

    def attach_host(self, host):
        """注入主窗口（主题/触屏/频谱/星象仪/观测站的应用入口）。"""
        self._host = host
        self._refresh_from_storage()
        self._wire_host_signals()
        self._apply_initial_state()

    def _wire_host_signals(self):
        """把控制面板的既有控件改动反写 settings，保持"一个权威源"。

        只连接，不改别人文件：顶部步进/静噪滑杆改动后，schema 值跟着变，
        下次启动推回同值；否则会出现"面板设 1 kHz、顶部改成 100 Hz、重启又回
        1 kHz"的双入口假象。
        """
        h = self._host
        if h is None or self._host_signals_wired or self._settings is None:
            return
        cp = getattr(h, "control_panel", None)
        if cp is None:
            return
        try:
            step_sig = getattr(cp, "step_changed", None)
            if step_sig is not None:
                step_sig.connect(self._on_step_from_control)
            sq_sig = getattr(cp, "squelch_changed", None)
            if sq_sig is not None:
                sq_sig.connect(self._on_squelch_from_control)
            self._host_signals_wired = True
        except (RuntimeError, TypeError) as exc:
            self._notify(f"与控制面板同步失败（改动只在本会话生效）：{exc}")

    def _on_step_from_control(self, hz):
        if self._settings is not None:
            self._settings.set("tuning_step_hz", float(hz))

    def _on_squelch_from_control(self, dbfs):
        if self._settings is not None:
            self._settings.set("squelch_default_db", int(round(float(dbfs))))

    def _announce_squelch_migration(self):
        """旧配置值按 IQ 功率保存；新口径是相对噪声基线余量。只提示一次。"""
        if self._settings is None:
            return
        checker = getattr(self._settings, "has_file_key", None)
        path = getattr(self._settings, "path", "")
        if not callable(checker) or not path or not os.path.exists(path):
            return
        if checker("squelch_default_db"):
            return
        self._notify(
            "静噪默认值迁移：你原来保存的值按 IQ 音频功率（绝对电平）记录；"
            "新滑杆改为「相对该模式噪声基线的余量」——-100≈0 dB、-50≈6 dB、"
            "-20≈16 dB。请复核当前值是否仍符合你的习惯"
            "（没有改动你保存过的任何值）。")
        # 写入新键（值就是 schema 默认 -50），让提示真正只出现一次；
        # 旧文件里本来没有这个键，因此没有覆盖任何用户值。
        self._settings.set("squelch_default_db", SCHEMA["squelch_default_db"].default)

    def _apply_initial_state(self):
        """把需要在启动时同步的持久化项推给消费点。"""
        h = self._host
        if h is None:
            return
        for key in ("bortle", "squelch_default_db", "tuning_step_hz"):
            item = SCHEMA[key]
            try:
                self._apply(item, self._current_value(item))
            except (AttributeError, TypeError, ValueError) as exc:
                self._notify(f"启动时同步 {item.label} 失败：{exc}")

    # ------------------------------------------------------------ 取/存
    @staticmethod
    def _ai_file_key(item) -> str:
        """schema 键 ai_temperature → 磁盘键 temperature（AgentConfig 字段名）。"""
        return item.key[3:] if item.key.startswith("ai_") else item.key

    def _current_value(self, item) -> Any:
        if item.storage == "settings":
            if self._settings is not None:
                return self._settings.get(item.key, item.default)
            return item.default
        if item.storage == "gui_config":
            if self._host is not None:
                return getattr(self._host, "_observer_" + item.key.split("_")[-1], None)
            return item.default
        return self._ai.get(self._ai_file_key(item), item.default)

    def _store_value(self, item, value) -> None:
        if item.storage == "settings":
            if self._settings is not None:
                self._settings.set(item.key, value)
            return
        if item.storage == "gui_config":
            if self._host is not None:
                # gui_config.json 是观测站坐标既有权威源（main_window.py:7630）
                setattr(self._host, "_observer_" + item.key.split("_")[-1], value)
                try:
                    self._host._apply_observer_location()
                except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                    self._notify(f"观测站坐标已保存，但天空图同步失败：{exc}")
                try:
                    self._host._save_gui_config()
                except (AttributeError, OSError, TypeError, ValueError) as exc:
                    self._notify(f"观测站坐标同步失败，未能写入 gui_config：{exc}")
            return
        # ai_config：保留 api_key 等既有键，只改本项
        self._ai[self._ai_file_key(item)] = value
        try:
            _atomic_write_json(AI_CONFIG_PATH, self._ai, mode=0o600)
        except (OSError, TypeError, ValueError) as exc:
            self._notify(f"AI 配置写入失败：{exc}（本次修改仅存在内存中）")
        # 已创建 agent 时立即生效（下一轮请求就会用新参数）
        agent = getattr(getattr(self._host, "ai_panel", None), "agent", None)
        if agent is not None and hasattr(agent, "config"):
            try:
                setattr(agent.config, item.key[3:], value)
            except (AttributeError, TypeError):
                # 旧版 AgentConfig 没有该字段：配置已落盘，新会话生效
                pass

    # ------------------------------------------------------------ 构建 UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 4, 6, 4)
        root.setSpacing(4)
        self.tabs = QTabWidget()
        for group in GROUP_ORDER:
            # 服务开关保留在独立的「服务设置」页签（避免两个 ServiceSettingsPanel
            # 各自持有一套 RemoteControl/WebServer 实例）；其键仍在 schema 内，
            # 参与导入导出与"恢复全部默认"。
            if group == "服务与远程访问":
                continue
            if group == "存储与日志":
                self.tabs.addTab(self._build_storage_tab(), group)
                continue
            items = items_for_group(group)
            if items:
                self.tabs.addTab(self._build_group_page(items), group)
        root.addWidget(self.tabs, 1)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("hintLabel")
        root.addWidget(self.status_label)

        self.btn_reset_all = QPushButton("恢复全部默认")
        self.btn_reset_all.setToolTip("把所有分组恢复到 schema 默认值（含观测站清空、服务关闭）")
        self.btn_reset_all.clicked.connect(lambda: self.reset_all(confirm=True))
        root.addWidget(self.btn_reset_all)

    def _build_group_page(self, items) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)
        for item in items:
            lay.addWidget(self._build_row(item))
        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _build_row(self, item) -> QGroupBox:
        box = QGroupBox(item.label)
        v = QVBoxLayout(box)
        h = QHBoxLayout()
        control = self._make_control(item)
        h.addWidget(control, 1)
        btn = QToolButton()
        btn.setText("默认")
        btn.setToolTip(f"恢复「{item.label}」为默认值")
        btn.clicked.connect(lambda _c=False, k=item.key: self._reset_one(k))
        h.addWidget(btn)
        v.addLayout(h)
        help_lbl = QLabel(item.help)
        help_lbl.setWordWrap(True)
        help_lbl.setObjectName("hintLabel")
        v.addWidget(help_lbl)
        if item.key == "record_dir":
            tools = QHBoxLayout()
            btn_browse = QPushButton("选择目录…")
            btn_browse.clicked.connect(self._browse_record_dir)
            btn_open = QPushButton("打开目录")
            btn_open.clicked.connect(self._open_record_dir)
            tools.addWidget(btn_browse)
            tools.addWidget(btn_open)
            tools.addStretch(1)
            v.addLayout(tools)
        if item.requires_restart:
            # 就在切换控件下方写明——改完不能让人以为已经生效。
            text = "重启后生效：" + (item.restart_note or "该项在启动阶段装载。")
            control.setToolTip(text)
            note = QLabel(text)
            note.setWordWrap(True)
            note.setStyleSheet("color:#D4956A;")
            v.addWidget(note)
        self._rows[item.key] = control
        return box

    def _make_control(self, item) -> QWidget:
        value = self._current_value(item)
        if item.kind == "bool":
            w = QCheckBox("开启")
            w.setChecked(bool(value))
            w.toggled.connect(
                lambda on, k=item.key: self._on_changed(k, bool(on)))
            return w
        if item.kind == "enum":
            w = QComboBox()
            w.addItems(list(item.choice_labels))
            try:
                idx = list(item.choice_values).index(value)
            except ValueError:
                idx = 0
            w.setCurrentIndex(max(0, idx))
            w.currentIndexChanged.connect(
                lambda i, k=item.key: self._on_changed(
                    k, SCHEMA[k].choice_values[i] if 0 <= i < len(SCHEMA[k].choice_values) else SCHEMA[k].default))
            return w
        if item.kind == "int":
            w = QSpinBox()
            w.setRange(int(item.minimum if item.minimum is not None else -2**31),
                       int(item.maximum if item.maximum is not None else 2**31 - 1))
            if item.step:
                w.setSingleStep(int(item.step))
            w.setValue(int(value) if value is not None else int(item.default or 0))
            if item.key == "squelch_default_db" and callable(_margin_threshold_db):
                # 滑块数字是旧刻度，旁边必须把真实含义（相对噪声基线余量）显示出来，
                # 例：-50（≈6 dB 余量）。换算只在内核 margin_threshold_db 一处定义。
                def _squelch_suffix(v):
                    w.setSuffix(f"（≈{_margin_threshold_db(v):g} dB 余量）")

                _squelch_suffix(w.value())
                w.valueChanged.connect(_squelch_suffix)
            w.valueChanged.connect(lambda v, k=item.key: self._on_changed(k, int(v)))
            return w
        # 文本：录音目录等路径项（留空 = 使用 schema/配置层给出的默认位置）
        if item.kind == "text":
            w = QLineEdit()
            w.setPlaceholderText(self._text_placeholder(item))
            w.setText("" if value is None else str(value))
            w.editingFinished.connect(
                lambda k=item.key, e=w: self._on_changed(k, e.text().strip()))
            return w
        # float：观测站经纬度默认 None（未设置），用留空行编辑更诚实
        if item.default is None:
            w = QLineEdit()
            w.setPlaceholderText("未设置（留空）")
            w.setText("" if value is None else f"{float(value):.6f}")
            w.editingFinished.connect(
                lambda k=item.key, e=w: self._on_changed(
                    k, None if not e.text().strip() else e.text().strip()))
            return w
        w = QDoubleSpinBox()
        lo = item.minimum if item.minimum is not None else -1e12
        hi = item.maximum if item.maximum is not None else 1e12
        w.setRange(float(lo), float(hi))
        w.setDecimals(6 if (item.step or 1) < 0.01 else 2)
        if item.step:
            w.setSingleStep(float(item.step))
        w.setValue(float(value) if value is not None else float(item.default or 0.0))
        w.valueChanged.connect(lambda v, k=item.key: self._on_changed(k, float(v)))
        return w

    def _text_placeholder(self, item) -> str:
        if item.key == "record_dir":
            return f"留空 = {self._record_effective_dir()}（只影响新录音）"
        return ""

    def _record_effective_dir(self) -> str:
        """当前生效的录音目录（设置留空时用程序旁 recordings 的默认值）。"""
        if self._settings is not None:
            try:
                return str(self._settings.record_dir)
            except (AttributeError, TypeError, ValueError):
                pass
        if resolve_record_dir is not None:
            return resolve_record_dir("")
        return ""

    # ------------------------------------------------------------ 刷新
    def _refresh_from_storage(self):
        self._ai = _load_json_dict(AI_CONFIG_PATH)
        self._building = True
        try:
            for key, w in self._rows.items():
                item = SCHEMA[key]
                self._set_control_value(item, self._current_value(item))
        finally:
            self._building = False

    def _set_control_value(self, item, value):
        w = self._rows.get(item.key)
        if w is None:
            return
        w.blockSignals(True)
        try:
            if item.kind == "bool":
                w.setChecked(bool(value))
            elif item.kind == "enum":
                try:
                    w.setCurrentIndex(list(item.choice_values).index(value))
                except ValueError:
                    w.setCurrentIndex(0)
            elif item.kind == "int":
                w.setValue(int(value) if value is not None else int(item.default or 0))
            elif item.kind == "text":
                w.setText("" if value is None else str(value))
            elif item.default is None:
                w.setText("" if value is None else f"{float(value):.6f}")
            else:
                w.setValue(float(value) if value is not None else float(item.default or 0.0))
        finally:
            w.blockSignals(False)

    # ------------------------------------------------------------ 改值
    def _on_changed(self, key: str, raw):
        if self._building:
            return
        item = SCHEMA[key]
        value, reason = validate_value(item, raw)
        self._store_value(item, value)
        applied = self._apply(item, value)
        self._set_control_value(item, self._current_value(item))
        msg = f"已保存：{item.label}"
        if key == "record_dir":
            msg += f"（生效目录 {self._record_effective_dir()}；只影响新录音）"
            # 保存即检查可写性：盘拔了/路径非法时当场说清楚，不等用户点"打开目录"。
            dir_error = None
            if self._settings is not None and hasattr(self._settings, "ensure_record_dir"):
                _effective, dir_error = self._settings.ensure_record_dir()
            if dir_error:
                msg += f"；{dir_error}"
                QMessageBox.warning(self, "录音目录不可用", dir_error)
        if item.requires_restart:
            msg += "（重启后生效）"
        elif not applied:
            msg += "（未接入主窗口，行为未变）"
        if reason:
            msg += f"；{reason}"
        self._notify(msg)

    def _notify(self, msg: str):
        """状态栏提示（保存成功/失败、降级原因）。方法名含 notify，是扫描器
        认可的"用户可见记录"路径——配置层不许静默吞掉任何失败。"""
        self.status_label.setText(msg)

    # ------------------------------------------------------------ 立即生效
    def _apply(self, item, value) -> bool:
        """把配置推给主窗口既有公开入口；返回是否真的推送了。"""
        h = self._host
        if h is None:
            return False
        try:
            key = item.key
            if key == "ui_language":
                return False   # 需重启（行内已标注）
            if key == "theme":
                h._apply_theme(str(value))
                return True
            if key == "touch_mode":
                h._set_touch_mode(str(value))
                return True
            if key == "show_waterfall":
                sp = getattr(h, "spectrum", None)
                st = getattr(sp, "_state", None)
                if sp is not None and st is not None and \
                        bool(getattr(st, "show_waterfall", True)) != bool(value):
                    sp.toggle_waterfall()
                return sp is not None
            if key.startswith("fft_") or key.startswith("waterfall_") or \
                    key.startswith("peak_") or key in (
                        "show_peaks", "show_bookmarks", "show_bandplan"):
                return self._apply_spectrum(h, key, value)
            if key == "sky_pressure_hpa":
                h.sky_view.set_refraction_params(pressure_hpa=float(value))
                return True
            if key == "sky_temperature_c":
                h.sky_view.set_refraction_params(temperature_c=float(value))
                return True
            if key == "bortle":
                # 公开 setter（不直接改 _bortle）：下一帧按新等级重算可见星。
                setter = getattr(getattr(h, "sky_view", None), "set_bortle", None)
                if setter is None:
                    return False
                setter(int(value))
                return True
            if key == "squelch_default_db":
                # 真实消费点：控制面板滑杆 + 主窗口 _on_squelch_changed → 解调门控。
                cp = getattr(h, "control_panel", None)
                slider = getattr(cp, "squelch_slider", None)
                label = getattr(cp, "squelch_val", None)
                if slider is not None:
                    slider.blockSignals(True)
                    try:
                        slider.setValue(int(value))
                    finally:
                        slider.blockSignals(False)
                if label is not None:
                    label.setText(f"{int(value)} dB")
                # 「自动」勾选时门限由噪声底跟踪，应用默认值不能把它偷偷关掉；
                # 记录旧状态，推送后原样恢复。
                auto_check = getattr(cp, "auto_squelch_check", None)
                was_auto = bool(auto_check.isChecked()) if auto_check is not None else False
                setter = getattr(h, "_on_squelch_changed", None)
                if callable(setter):
                    setter(float(value))
                if was_auto:
                    restore = getattr(h, "_on_auto_squelch_toggled", None)
                    if callable(restore):
                        restore(True)
                return slider is not None or callable(setter)
            if key == "tuning_step_hz":
                # 真实消费点：control_panel 步进下拉 → spectrum.snap_interval。
                cp = getattr(h, "control_panel", None)
                combo = getattr(cp, "step_combo", None)
                if combo is not None:
                    idx = combo.findData(int(value))
                    if idx >= 0:
                        combo.blockSignals(True)
                        try:
                            combo.setCurrentIndex(idx)
                        finally:
                            combo.blockSignals(False)
                sp = getattr(h, "spectrum", None)
                setter = getattr(sp, "set_snap_interval", None)
                if callable(setter):
                    setter(float(value))
                return combo is not None or callable(setter)
            if key == "record_dir":
                # 目录只给消费方读；改值不影响已录文件（消费点在主窗口线）。
                return True
            if key.startswith("observer_"):
                return True   # _store_value 已写 gui_config 并同步 tracker/天空图
            if key.startswith("ai_"):
                return getattr(getattr(h, "ai_panel", None), "agent", None) is not None
        except (AttributeError, KeyError, RuntimeError, TypeError, ValueError) as e:
            # 单点生效失败不许拖垮面板，但必须让用户看到"已保存、未生效"
            self._notify(f"应用失败（已保存配置）：{e}")
            return False
        return False

    def _apply_spectrum(self, host, key: str, value) -> bool:
        """通过频谱面板既有控件/公开方法生效，并同步工具栏显示（单一入口）。"""
        sp = getattr(host, "spectrum", None)
        gen = getattr(sp, "generator", None)
        if sp is None or gen is None:
            return False
        if key == "fft_size":
            gen.set_fft_size(int(value))
            self._sync_combo(sp, "_fft_combo", str(int(value)))
        elif key == "fft_window":
            gen.set_window(str(value))
            self._sync_combo(sp, "_win_combo", str(value))
        elif key == "fft_avg_frames":
            gen.set_avg_frames(int(value))
            self._sync_combo(sp, "_avg_combo", str(int(value)))
        elif key == "waterfall_palette":
            idx = 1 if value == "sdrpp" else 0
            sp._on_palette_changed(idx)
            self._sync_combo(sp, "_palette_combo", sp._palette_combo.itemText(idx))
        elif key == "peak_threshold_db":
            st = getattr(sp, "_state", None)
            if st is not None:
                st.peak_rel_db = float(value)
            self._sync_spin(sp, "_peak_spin", float(value))
        elif key == "waterfall_smooth":
            sp._on_wf_smooth_toggled(bool(value))
            self._sync_check(sp, "_wf_smooth_chk", bool(value))
        elif key == "show_peaks":
            st = getattr(sp, "_state", None)
            if st is not None:
                st.show_peaks = bool(value)
            self._sync_check(sp, "_peak_chk", bool(value))
        elif key == "show_bookmarks":
            st = getattr(sp, "_state", None)
            if st is not None:
                st.show_bookmarks = bool(value)
            self._sync_check(sp, "_bookmark_chk", bool(value))
        elif key == "show_bandplan":
            st = getattr(sp, "_state", None)
            if st is not None:
                st.show_bandplan = bool(value)
            self._sync_check(sp, "_bandplan_chk", bool(value))
        try:
            sp._redraw()
        except (AttributeError, RuntimeError, TypeError) as exc:
            self._notify(f"频谱重绘失败（参数已生效）：{exc}")
        return True

    @staticmethod
    def _sync_combo(panel, attr: str, text: str):
        w = getattr(panel, attr, None)
        if w is None:
            return
        w.blockSignals(True)
        try:
            w.setCurrentText(text)
        finally:
            w.blockSignals(False)

    @staticmethod
    def _sync_spin(panel, attr: str, value: float):
        w = getattr(panel, attr, None)
        if w is None:
            return
        w.blockSignals(True)
        try:
            w.setValue(value)
        finally:
            w.blockSignals(False)

    @staticmethod
    def _sync_check(panel, attr: str, value: bool):
        w = getattr(panel, attr, None)
        if w is None:
            return
        w.blockSignals(True)
        try:
            w.setChecked(bool(value))
        finally:
            w.blockSignals(False)

    # ------------------------------------------------------------ 恢复默认
    def _reset_one(self, key: str) -> None:
        item = SCHEMA.get(key)
        if item is None:
            return
        if item.storage == "settings" and self._settings is not None:
            self._settings.reset(key)
        elif item.storage == "gui_config":
            self._store_value(item, None)
        elif item.storage == "ai_config":
            self._store_value(item, item.default)
        self._refresh_from_storage()
        self._apply(item, self._current_value(item))
        self._notify(f"已恢复默认：{item.label}")

    def reset_all(self, confirm: bool = True) -> bool:
        """恢复全部默认（settings + 观测站清空 + AI 采样参数）。

        返回 True 表示已执行；confirm=False 供测试/无人值守调用。
        """
        if confirm and self.isVisible():
            ans = QMessageBox.question(
                self, "恢复全部默认",
                "将把设置、观测站坐标与服务开关全部恢复默认，是否继续？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ans != QMessageBox.Yes:
                return False
        if self._settings is not None:
            self._settings.reset_all()
        for item in EXPOSED_ITEMS:
            if item.storage == "gui_config":
                self._store_value(item, None)
            elif item.storage == "ai_config":
                self._ai[self._ai_file_key(item)] = item.default
        if EXPOSED_ITEMS:
            try:
                _atomic_write_json(AI_CONFIG_PATH, self._ai, mode=0o600)
            except (OSError, TypeError, ValueError) as exc:
                self._notify(f"AI 配置恢复默认后写入失败：{exc}")
        # 服务页签由主窗口持有；恢复默认后让它回填（端口置灰由它自身状态决定）
        sp = getattr(self._host, "service_settings_panel", None)
        if sp is not None:
            sp.refresh_from_settings()
        self._refresh_from_storage()
        for item in EXPOSED_ITEMS:
            if item.storage != "gui_config":
                self._apply(item, self._current_value(item))
        self._notify("已恢复全部默认")
        return True

    # ------------------------------------------------------------ 导入/导出
    def export_to(self, path: str) -> Dict[str, Any]:
        """导出可读 JSON（含版本号；绝不含 API Key）。返回写出的字典。"""
        values = self._settings.values() if self._settings is not None else {}
        gui = {}
        ai = {}
        for item in EXPOSED_ITEMS:
            if item.storage == "gui_config":
                gui[item.key] = self._current_value(item)
            elif item.storage == "ai_config":
                ai[item.key] = self._current_value(item)
        payload = build_export(
            values, extra={"gui_config": gui, "ai_config": ai},
            exported_at=datetime.now().isoformat(timespec="seconds"))
        _atomic_write_json(path, payload)
        self._notify(f"已导出：{path}")
        return payload

    def import_from(self, path: str) -> ImportResult:
        """校验版本/字段后导入；未知键进 result.unknown（不静默丢弃）。"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError) as e:
            return ImportResult(error=f"读取失败：{e}")
        res = parse_import(payload)
        if not res.ok:
            return res
        for key, value in res.accepted.items():
            item = SCHEMA[key]
            if item.storage == "settings":
                if self._settings is not None:
                    self._settings.set(key, value)
            elif item.storage == "gui_config":
                self._store_value(item, value)
            else:
                self._ai[self._ai_file_key(item)] = value
        try:
            _atomic_write_json(AI_CONFIG_PATH, self._ai, mode=0o600)
        except (OSError, TypeError, ValueError) as e:
            res.warnings.append(f"AI 配置写入失败：{e}")
        self._refresh_from_storage()
        sp = getattr(self._host, "service_settings_panel", None)
        if sp is not None:
            sp.refresh_from_settings()
        for key, value in res.accepted.items():
            item = SCHEMA[key]
            if item.storage != "gui_config":
                self._apply(item, self._current_value(item))
        self._notify("导入完成：" + res.summary())
        return res

    # ------------------------------------------------------------ 存储与日志页
    def _build_storage_tab(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        # 录音目录是真实可配置项（schema 行 + 选择/打开按钮）
        for item in items_for_group("存储与日志"):
            lay.addWidget(self._build_row(item))
        settings_path = (self._settings.path if self._settings is not None
                         else "（未挂载 settings）")
        for label, path in (
                ("桌面设置", settings_path),
                ("GUI 配置（观测站/窗口）", GUI_CONFIG_PATH),
                ("AI 配置", AI_CONFIG_PATH),
                ("布局", os.path.expanduser("~/.config/MBDSDR/layouts.json")),
                ("日志（pythonw 无控制台时）",
                 os.path.expanduser("~/.config/MBDSDR/mbdsdr_desktop.log"))):
            row = QLabel(f"{label}：{path}")
            row.setWordWrap(True)
            row.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(row)

        note = QLabel(
            "损坏的配置不会被覆盖：解析失败会先备份为 *.corrupt-时间戳，"
            "再按默认值启动。录音目录的改动只影响新录音，已有录音不动；"
            "日志级别仍锁死（原因见 docs/settings-inventory.md）。")
        note.setWordWrap(True)
        note.setObjectName("hintLabel")
        lay.addWidget(note)

        row = QHBoxLayout()
        btn_export = QPushButton("导出配置")
        btn_export.clicked.connect(self._on_export_clicked)
        btn_import = QPushButton("导入配置")
        btn_import.clicked.connect(self._on_import_clicked)
        btn_open = QPushButton("打开配置目录")
        btn_open.clicked.connect(self._on_open_dir_clicked)
        row.addWidget(btn_export)
        row.addWidget(btn_import)
        row.addWidget(btn_open)
        lay.addLayout(row)
        lay.addStretch(1)
        return page

    def _on_export_clicked(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置", f"mbdsdr-settings-v{CONFIG_SCHEMA_VERSION}.json",
            "JSON (*.json)")
        if not path:
            return
        try:
            self.export_to(path)
        except (OSError, TypeError, ValueError) as e:
            QMessageBox.warning(self, "导出失败", str(e))

    def _on_import_clicked(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配置", "", "JSON (*.json)")
        if not path:
            return
        res = self.import_from(path)
        if not res.ok:
            QMessageBox.warning(self, "导入被拒绝", res.error or "未知错误")
            return
        lines = [res.summary() + "。"]
        if res.filled:
            lines.append("已按默认补齐（旧文件缺少的字段）：" + "、".join(res.filled[:20]))
        if res.unknown:
            lines.append("已忽略（未知字段，不导入）：" + "、".join(res.unknown[:20]))
        if res.rejected:
            lines.append("被拒绝：" + "；".join(
                f"{k}={r}" for k, r in res.rejected[:20]))
        if res.warnings:
            lines.append("提示：" + "；".join(res.warnings[:20]))
        QMessageBox.information(self, "导入完成", "\n".join(lines))

    def _browse_record_dir(self):
        """选择录音目录；已录文件不迁移（只影响新录音）。"""
        current = self._record_effective_dir()
        path = QFileDialog.getExistingDirectory(self, "选择录音目录", current)
        if path:
            self._on_changed("record_dir", path)

    def _open_record_dir(self):
        """打开当前生效的录音目录；不存在则尝试创建，失败明确提示。"""
        directory, error = self._record_effective_dir(), None
        if self._settings is not None and hasattr(self._settings, "ensure_record_dir"):
            directory, error = self._settings.ensure_record_dir()
        else:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as exc:
                error = f"录音目录不可用（{directory}）：{exc}"
        if error:
            QMessageBox.warning(self, "打开录音目录失败", error)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def _on_open_dir_clicked(self):
        directory = os.path.expanduser("~/.mbdsdr")
        try:
            os.makedirs(directory, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(directory))
        except OSError as e:
            QMessageBox.warning(self, "打开目录失败", str(e))
