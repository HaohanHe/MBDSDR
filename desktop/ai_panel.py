# SPDX-License-Identifier: MIT
import os
"""
MBDSDR AI 对话面板
===================
自然语言指令输入、对话历史、执行步骤视图（结构化步骤卡）。
AI 是技能触发入口（用户对模型说才触发），不是功能按钮。
使用真正的 MBDSDRAgent（LLM 驱动，支持工具调用、上下文管理、记忆系统）。
"""

import json
from datetime import datetime
from html import escape as _esc
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QThread, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QLineEdit, QFrame, QGroupBox, QComboBox,
    QSplitter, QSizePolicy, QMessageBox, QScrollArea
)
from PySide6.QtGui import QFont, QTextCursor

# 尝试导入 AI 内核
try:
    from mbdsdr_ai import MBDSDRAgent, AgentConfig
    from mbdsdr_ai.config import normalize_config_keys
    AI_CORE_AVAILABLE = True
except ImportError:
    AI_CORE_AVAILABLE = False
    normalize_config_keys = None

try:  # pragma: no cover - 失败消息整形（clean_exc 战役先例），缺失时退化原文
    from mbdsdr_ai.tool_registry import clean_exc as _clean_exc
except Exception:  # noqa: BLE001
    def _clean_exc(e):  # type: ignore[misc]
        return str(e)

try:
    from local_agent_bridge import LocalAgentBridge
except ImportError:  # pragma: no cover - 模块缺失时对话栏退化为旧行为
    LocalAgentBridge = None
import ai_steps  # noqa: E402  # 步骤卡投影（Qt 无关）
from tokens import tokens as _tok  # noqa: E402

# AI 调用失败的统一说明：发生了什么 / 可能原因 / 下一步。
# 不猜具体根因，只给用户可检查的方向；已执行的硬件操作不会自动回滚。
_AI_FAILURE_GUIDE = (
    "可能原因：API Key 未配置或无效、网络不可用、服务额度或限流。\n"
    "下一步：点右上角「配置」检查 API Key 与网络后重试；"
    "已执行的硬件操作不会自动回滚。"
)


# 快捷指令（这些是提示用户可以说什么，不是直接执行的功能按钮）
QUICK_COMMANDS = [
    "找一个信号强的 FM 电台",
    "扫频 87-108 MHz 找所有电台",
    f"调谐到 {_tok().AI['demo_freq_mhz']} MHz 并录 30 秒",
    "当前信号质量怎么样",
    "获取 GPS 定位",
    "查看 9 轴姿态",
    "自动找干扰源方向",
    "识别当前信号制式",
]


class LocalWorker(QObject):
    """本地能力协商 Worker：工具可能是硬件读取，不能压在 UI 线程上。"""
    finished = Signal(dict)

    def __init__(self, bridge, text: str, confirm: bool,
                 only_tool=None, only_args=None):
        super().__init__()
        self.bridge = bridge
        self.text = text
        self.confirm = confirm
        self.only_tool = only_tool
        self.only_args = only_args

    @Slot()
    def run(self):
        try:
            self.finished.emit(self.bridge.handle(
                self.text, confirm=self.confirm,
                only_tool=self.only_tool, only_args=self.only_args))
        except Exception as e:
            self.finished.emit({
                "mode": "local", "status": "failed", "cards": [],
                "culture": [], "tasks": [], "hooks": [], "suggestions": [],
                "reply": "本地能力协商出错：%s: %s" % (type(e).__name__, e),
            })


class AIWorker(QObject):
    """后台 AI 调用 Worker（避免 UI 卡顿）。"""
    finished = Signal(dict)  # 结果
    error = Signal(str)     # 错误
    delta = Signal(str)     # 流式增量文本

    def __init__(self, agent: "MBDSDRAgent", user_input: str):
        super().__init__()
        self.agent = agent
        self.user_input = user_input

    @Slot()
    def run(self):
        try:
            result = self.agent.chat(
                self.user_input,
                on_delta=lambda d: self.delta.emit(d),
            )
            self.finished.emit(result)
        except Exception as e:
            # 只给可读失败原因，不把英文堆栈漏进对话区（clean_exc 战役先例）
            self.error.emit(_clean_exc(e))


class AIPanel(QWidget):
    """AI 对话面板。"""

    # 配置文件路径
    CONFIG_DIR = os.path.expanduser("~/.mbdsdr")
    CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

    # 信号：请求调用工具（外部 MCP 客户端处理）
    tool_call_requested = Signal(str, dict)  # (tool_name, params)
    # 信号：自然语言指令
    command_submitted = Signal(str)
    # 信号：一轮 AI 回复的最终文本（流式内容的完整版；星象仪形态覆盖卡用）。
    # 与对话区解耦：覆盖卡只读不复写面板，历史仍以面板为准。
    reply_received = Signal(str)
    # 信号：天空动作请求的一轮协商结束（action, satellite, tool_requested）。
    # 主窗口据此把「意图」与「真实状态」对齐（模型判定不执行时要把按钮回弹）。
    sky_action_resolved = Signal(str, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._conversation: List[Dict] = []
        self._tool_log: List[ai_steps.StepRecord] = []
        self.agent: Optional["MBDSDRAgent"] = None
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[AIWorker] = None
        self._is_processing = False
        # 流式状态默认位：finished 槽可能先于（或压根没有）_call_ai 被触发，
        # 没有默认位会在 _on_ai_finished 里 AttributeError 撞死。
        self._streamed_any = False
        # MCP 硬件工具挂起区：agent 可能稍后才创建（配置 API Key 后），
        # 已取到的硬件工具 schema + 调用 handler 缓存起来，重建 agent 时补注册。
        self._pending_mcp: Optional[tuple] = None
        self._mcp_tool_names: set = set()
        # 天空动作请求的待结算状态（按钮触发的协商轮次）
        self._pending_sky: Optional[Dict[str, str]] = None
        # 本地能力协商（无 API Key 时的真实工具路由）+ 常驻状态
        self._bridge = None
        self._local_thread = None
        self._local_worker = None
        self._local_busy = False
        self._saved_config = self._load_config()
        self._build_ui()
        self._add_welcome_message()

    @classmethod
    def _load_config(cls) -> Dict[str, Any]:
        """从配置文件加载 API 配置。

        读后迁移：旧键 api_base 一次性归一为 base_url 并回写（见
        mbdsdr_ai.config.normalize_config_keys），消除同文件键名打架。
        """
        try:
            if os.path.exists(cls.CONFIG_FILE):
                with open(cls.CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if normalize_config_keys is None:
                    return data
                normalized = normalize_config_keys(data)
                if normalized != data:
                    cls._save_config(normalized)  # 一次性迁移回写
                return normalized
        except Exception:
            pass
        return {}

    #: 最近一次 ``_save_config`` 的失败原因；成功时为 None。调用方据此
    #: 决定能不能说「已保存」（写盘失败仍报完成态会让人重启后才发现丢配置）。
    _last_save_error: Optional[str] = None

    @classmethod
    def _save_config(cls, config: Dict[str, Any]) -> bool:
        """保存 API 配置到配置文件（统一键名 base_url）。返回是否真的写盘成功。"""
        try:
            if normalize_config_keys is not None:
                config = normalize_config_keys(config)
            os.makedirs(cls.CONFIG_DIR, exist_ok=True)
            with open(cls.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            # 限制配置文件权限（仅所有者可读写）
            try:
                os.chmod(cls.CONFIG_FILE, 0o600)
            except Exception:
                pass
            cls._last_save_error = None
            return True
        except Exception as e:  # noqa: BLE001 - 不抛给 UI，但必须让失败可见
            cls._last_save_error = f"{e.__class__.__name__}: {e}"
            return False

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 标题栏
        title_bar = QHBoxLayout()
        _t = _tok()
        title_label = QLabel(self.tr("AI 智能体"))
        title_label.setStyleSheet(
            f"font-size: 11pt; font-weight: 600; color: {_t.COLORS['light_text_strong']};")
        title_bar.addWidget(title_label)
        title_bar.addStretch()

        # AI 内核状态指示
        self.ai_status_label = QLabel()
        self.ai_status_label.setStyleSheet(
            f"font-size: 8pt; color: {_t.COLORS['text_muted']};")
        self._update_ai_status()
        title_bar.addWidget(self.ai_status_label)

        # API Key 配置按钮
        self.config_btn = QPushButton(self.tr("配置"))
        self.config_btn.setMinimumHeight(24)
        self.config_btn.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.config_btn.setStyleSheet("font-size: 8pt; padding: 2px 8px;")
        self.config_btn.clicked.connect(self._show_config)
        title_bar.addWidget(self.config_btn)

        # 弹出成自由窗口：右栏窄时对话挤成一团，用户自己决定它多大放哪。
        # 对齐用户「像真实桌面，想放哪儿放哪儿」「别老给他决定」。
        self.pop_btn = QPushButton("⧉")
        self.pop_btn.setObjectName("aiPopBtn")
        self.pop_btn.setFixedWidth(26)
        self.pop_btn.setMinimumHeight(24)
        self.pop_btn.setToolTip("把这个面板弹出成可拖动的自由窗口（也能再归位）")
        self.pop_btn.setStyleSheet(
            "font-size:10pt;background:transparent;color:#B9C7DB;"
            "border:1px solid rgba(160,190,225,0.25);border-radius:5px;")
        self.pop_btn.clicked.connect(self._pop_out)
        title_bar.addWidget(self.pop_btn)

        layout.addLayout(title_bar)

        # ---- 常驻状态条：Agent 内核 / 编排任务 / Hooks 事件一直看得见 ----
        # 用户 2026-11-08：「人工智能编排有没有？包括 Hooks，包括常驻化」。
        # 数字全部从 live_state() 读本进程真实对象，不是 UI 上画的假状态。
        self.live_btn = QPushButton()
        self.live_btn.setObjectName("agentLiveBar")
        self.live_btn.setMinimumHeight(22)
        self.live_btn.setMaximumHeight(30)
        self.live_btn.setStyleSheet(
            "QPushButton { text-align: left; padding: 2px 8px;"
            " background-color: rgba(60,90,130,0.16);"
            " color: #CFE0F5; border: 1px solid rgba(160,190,225,0.22);"
            " border-radius: 5px; font-size: 8pt; }"
            "QPushButton:hover { background-color: rgba(60,90,130,0.30); }")
        self.live_btn.clicked.connect(self._toggle_live_details)
        layout.addWidget(self.live_btn)
        self.live_details = QLabel("")
        self.live_details.setWordWrap(True)
        self.live_details.setTextFormat(Qt.RichText)
        self.live_details.setVisible(False)
        self.live_details.setStyleSheet(
            "color: #B8C6D9; font-size: 8pt; padding: 4px 8px;"
            " background: rgba(14,17,24,0.55); border-radius: 4px;")
        layout.addWidget(self.live_details)
        self._refresh_live_bar()

        # 对话历史
        self.conversation_view = QTextEdit()
        self.conversation_view.setReadOnly(True)
        self.conversation_view.setStyleSheet(f"""
            QTextEdit {{
                background: {_t.COLORS['light_panel_bg']};
                border: 1px solid {_t.COLORS['light_border_subtle']};
                border-radius: 6px;
                padding: 8px;
                font-size: 9pt;
                color: {_t.COLORS['light_text_strong']};
            }}
        """)
        self.conversation_view.setMinimumHeight(150)
        layout.addWidget(self.conversation_view, 1)

        # 快捷指令
        quick_group = QGroupBox("快捷指令（点击发送）")
        quick_group.setStyleSheet(
            f"QGroupBox {{ font-size: 8pt; color: {_t.COLORS['text_muted']}; "
            f"border: none; margin-top: 4px; }}")
        quick_layout = QHBoxLayout()
        quick_layout.setSpacing(4)
        quick_layout.setContentsMargins(0, 4, 0, 0)
        for cmd in QUICK_COMMANDS[:4]:
            btn = QPushButton(cmd)
            btn.setMinimumHeight(24)
            btn.setMaximumHeight(32)
            btn.setStyleSheet(f"""
                QPushButton {{
                    font-size: 8pt;
                    padding: 2px 8px;
                    background: {_t.COLORS['light_hover']};
                    border: 1px solid {_t.COLORS['light_border_strong']};
                    border-radius: 4px;
                    color: {_t.COLORS['light_text_secondary']};
                }}
                QPushButton:hover {{ background: {_t.COLORS['light_hover_bg_strong']}; }}
            """)
            btn.clicked.connect(lambda checked, c=cmd: self._send_quick_command(c))
            quick_layout.addWidget(btn)
        quick_layout.addStretch()
        quick_group.setLayout(quick_layout)
        layout.addWidget(quick_group)

        # 人工门确认条：gated 工具在这里显式确认，绝不自动动设备。
        # QTextEdit 不能嵌入 QWidget，所以按钮挂在真实容器上而非对话流里。
        self._confirm_bar = QWidget()
        self._confirm_bar.setVisible(False)
        self._confirm_layout = QHBoxLayout(self._confirm_bar)
        self._confirm_layout.setContentsMargins(0, 0, 0, 0)
        self._confirm_layout.setSpacing(6)
        self._confirm_layout.addStretch(1)
        layout.addWidget(self._confirm_bar)

        # 输入栏
        input_bar = QHBoxLayout()
        input_bar.setSpacing(6)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("对 AI 说点什么...（如：扫频找台、调谐、录音）")
        self.input_field.setMinimumHeight(22)
        self.input_field.setStyleSheet(f"""
            QLineEdit {{
                padding: 6px 10px;
                border: 1px solid {_t.COLORS['light_border_input']};
                border-radius: 6px;
                font-size: 9pt;
                background: {_t.COLORS['light_card']};
            }}
            QLineEdit:focus {{ border-color: {_t.COLORS['light_accent_border']}; }}
        """)
        self.input_field.returnPressed.connect(self._on_submit)
        input_bar.addWidget(self.input_field, 1)

        self.send_btn = QPushButton(self.tr("发送"))
        self.send_btn.setMinimumHeight(28)
        self.send_btn.setMaximumHeight(40)
        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 4px 16px;
                background: {_t.COLORS['accent_warm']};
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 9pt;
                font-weight: 500;
            }}
            QPushButton:hover {{ background: {_t.COLORS['accent_warm_hover']}; }}
            QPushButton:disabled {{ background: {_t.COLORS['disabled_gray']}; }}
        """)
        self.send_btn.clicked.connect(self._on_submit)
        input_bar.addWidget(self.send_btn)

        layout.addLayout(input_bar)

        # 执行步骤视图（可折叠）：结构化步骤卡，字段对齐
        # cpp/src/ui/task_steps_view.h（StepResult：工具/关键参数/状态/结果摘要/耗时）
        self.tool_log_group = QGroupBox("执行步骤")
        self.tool_log_group.setStyleSheet(
            f"QGroupBox {{ font-size: 8pt; color: {_t.COLORS['text_muted']}; "
            f"border: none; margin-top: 4px; }}")
        tool_log_layout = QVBoxLayout()
        tool_log_layout.setContentsMargins(0, 4, 0, 0)
        self.step_list_host = QWidget()
        self.step_list_layout = QVBoxLayout(self.step_list_host)
        self.step_list_layout.setContentsMargins(2, 2, 2, 2)
        self.step_list_layout.setSpacing(4)
        self.step_list_layout.addStretch(1)
        step_scroll = QScrollArea()
        step_scroll.setWidgetResizable(True)
        step_scroll.setFrameShape(QFrame.NoFrame)
        step_scroll.setWidget(self.step_list_host)
        step_scroll.setMaximumHeight(160)
        step_scroll.setStyleSheet(f"""
            QScrollArea {{
                background: {_t.COLORS['light_log_bg']};
                border: 1px solid {_t.COLORS['light_border_subtle']};
                border-radius: 4px;
            }}
        """)
        tool_log_layout.addWidget(step_scroll)
        self.tool_log_group.setLayout(tool_log_layout)
        self.tool_log_group.setVisible(False)  # 默认隐藏
        layout.addWidget(self.tool_log_group)

        # 切换步骤视图显示按钮
        self.toggle_log_btn = QPushButton(self.tr("显示执行步骤"))
        self.toggle_log_btn.setMinimumHeight(20)
        self.toggle_log_btn.setMaximumHeight(28)
        self.toggle_log_btn.setStyleSheet(
            f"font-size: 8pt; color: {_t.COLORS['disabled_gray']}; border: none;")
        self.toggle_log_btn.clicked.connect(self._toggle_log)
        layout.addWidget(self.toggle_log_btn)

    def _update_ai_status(self):
        """更新 AI 内核状态指示（含上下文 token 使用情况）。"""
        if AI_CORE_AVAILABLE and self.agent:
            model = self.agent.model_manager.model
            # 获取上下文状态
            ctx_info = ""
            try:
                cm = self.agent.context_manager
                current = getattr(cm, 'current_tokens', 0)
                maximum = getattr(cm, 'max_tokens', 8192)
                msg_count = getattr(cm, 'message_count', 0)
                compressions = getattr(cm, 'compression_count', 0)
                if current > 0:
                    pct = current * 100 // maximum
                    ctx_info = f" | ctx: {current}/{maximum} ({pct}%) | msgs: {msg_count}"
            except Exception:
                pass
            self.ai_status_label.setText(f"LLM: {model.split('/')[-1]}{ctx_info}")
            self.ai_status_label.setStyleSheet(
                f"font-size: 8pt; color: {_tok().COLORS['status_success']};")
        elif AI_CORE_AVAILABLE:
            self.ai_status_label.setText(self.tr("未配置 API"))
            self.ai_status_label.setStyleSheet(
                f"font-size: 8pt; color: {_tok().COLORS['status_warning']};")
        else:
            self.ai_status_label.setText(self.tr("AI 内核不可用"))
            self.ai_status_label.setStyleSheet(
                f"font-size: 8pt; color: {_tok().COLORS['status_danger']};")

    def _show_config(self):
        """显示 API 配置对话框。"""
        if not AI_CORE_AVAILABLE:
            QMessageBox.warning(self, "AI 内核不可用", "mbdsdr_ai 模块未找到，请检查安装。")
            return

        from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr("AI 配置"))
        dialog.setMinimumWidth(400)
        form = QFormLayout(dialog)

        api_key_edit = QLineEdit()
        api_key_edit.setEchoMode(QLineEdit.Password)
        api_key_edit.setPlaceholderText("sk-...（留空则不使用 LLM）")
        if self.agent and self.agent.config.api_key:
            api_key_edit.setText(self.agent.config.api_key)
        elif self._saved_config.get("api_key"):
            api_key_edit.setText(self._saved_config["api_key"])
        form.addRow("API Key:", api_key_edit)

        base_url_edit = QLineEdit()
        base_url_edit.setText(
            (self.agent.config.base_url if self.agent else None)
            or self._saved_config.get("base_url", "https://api.siliconflow.cn/v1")
        )
        form.addRow("Base URL:", base_url_edit)

        model_edit = QLineEdit()
        model_edit.setText(
            (self.agent.config.model if self.agent else None)
            or self._saved_config.get("model", "Qwen/Qwen3.6-35B-A3B")
        )
        form.addRow(self.tr("模型:"), model_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.Accepted:
            self._apply_config_from_dialog(
                api_key_edit.text().strip(),
                base_url_edit.text().strip(),
                model_edit.text().strip())

    def _apply_config_from_dialog(self, api_key: str, base_url: str,
                                  model: str) -> None:
        """落盘并生效对话框结果：写盘失败时绝不报「已配置并保存」。"""
        # 保存到配置文件（统一键名 base_url；其余既有键原样保留）
        self._saved_config = {
            **self._saved_config,
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
        }
        saved = self._save_config(self._saved_config)

        if not api_key:
            # 未填写 API Key：不初始化 agent，避免后续调用失败
            self.agent = None
            self._update_ai_status()
            self._add_system_message(
                "未填写 API Key，AI 将不可用。请在配置对话框中填入 API Key 后重启对话。"
            )
            QMessageBox.information(
                self, "未配置 API Key",
                "未填写 API Key，AI 对话功能将不可用。\n"
                "请重新点击「配置」填入 API Key。"
            )
            return

        # 重新初始化 agent
        config = AgentConfig(api_key=api_key, base_url=base_url, model=model)
        self.agent = self._create_agent(config)
        self._apply_pending_mcp_tools()
        self._update_ai_status()
        if saved:
            self._add_system_message(
                f"AI 已配置并保存: {model}（配置文件: ~/.mbdsdr/config.json）")
        else:
            self._add_system_message(
                f"AI 配置未能写入磁盘（{self._last_save_error}）："
                "本次设置只在本次运行中生效，重启后需要重新填写。")

    def _create_agent(self, config):
        """创建 agent，并沿用旧会话的天空硬件能力账本（不重新追问）。"""
        old_ledger = getattr(self.agent, "sky_ledger", None) if self.agent else None
        agent = MBDSDRAgent(config)
        if old_ledger is not None:
            agent.sky_ledger = old_ledger
            agent.context_manager.capability_ledger = old_ledger
        return agent

    def init_agent(self, api_key: str = "", base_url: str = "", model: str = ""):
        """初始化 AI Agent（由外部调用）。

        配置优先级：传入参数 > 配置文件(~/.mbdsdr/config.json) > 环境变量 > 默认值
        """
        if not AI_CORE_AVAILABLE:
            self._add_system_message("警告: mbdsdr_ai 模块不可用，AI 对话与工具调用均不可用（不提供假降级）。")
            return

        # 优先级：参数 > 配置文件 > 环境变量 > 默认值
        final_api_key = (
            api_key
            or self._saved_config.get("api_key", "")
            or os.environ.get("MBDSDR_API_KEY", "")
        )
        final_base_url = (
            base_url
            or self._saved_config.get("base_url", "")
            or os.environ.get("MBDSDR_BASE_URL", "")
            or os.environ.get("MBDSDR_API_BASE", "https://api.siliconflow.cn/v1")
        )
        final_model = (
            model
            or self._saved_config.get("model", "")
            or os.environ.get("MBDSDR_MODEL", "Qwen/Qwen3.6-35B-A3B")
        )

        if not final_api_key:
            # 没有 API Key 也装载 Agent 内核（用户 2026-11-08：「作为 Agent」
            # 「常驻化」「人工智能编排 / Hooks 有没有」）。
            # 工具注册表、Hooks、编排器、插件系统都是本进程的能力，不该因为
            # "没配模型" 就整体消失 —— 模型只是 Agent 的一个部件。缺模型时
            # 对话如实降级，能力位与常驻状态照旧可见、可查、可被点用。
            config = AgentConfig(api_key="", base_url=final_base_url,
                                 model=final_model)
            self.agent = self._create_agent(config)
            self._apply_pending_mcp_tools()
            self._update_ai_status()
            self._add_system_message(
                "AI 内核已常驻（工具 / Hooks / 编排可用）；未配置 API Key，"
                "模型对话暂不可用，点「配置」填入即可。"
            )
            return

        config = AgentConfig(api_key=final_api_key, base_url=final_base_url, model=final_model)
        self.agent = self._create_agent(config)
        self._apply_pending_mcp_tools()
        self._update_ai_status()
        source = "参数" if api_key else ("配置文件" if self._saved_config.get("api_key") else ("环境变量" if os.environ.get("MBDSDR_API_KEY") else "默认"))
        self._add_system_message(f"AI 内核已初始化: {final_model}（配置来源: {source}）")

    def register_mcp_tools(self, mcp_tools: List[Dict], mcp_call_handler):
        """注册 MCP 硬件工具到 AI Agent（让模型硬件调用直接走 MCP handler 执行）。

        agent 尚未创建（未配 API Key）时挂起缓存，等 init_agent/_show_config
        重建 agent 后由 _apply_pending_mcp_tools 补注册；工具 schema 来自真实
        设备 list_tools，未连接时列表为空（如实空态，不注册任何工具）。
        """
        mcp_tools = list(mcp_tools or [])
        self._pending_mcp = (mcp_tools, mcp_call_handler)
        self._mcp_tool_names = {t.get("name", "") for t in mcp_tools if t.get("name")}
        if self.agent:
            self.agent.register_mcp_tools(mcp_tools, mcp_call_handler)
            if mcp_tools:
                self._add_system_message(f"已注册 {len(mcp_tools)} 个 MCP 硬件工具")
            else:
                self._add_system_message("硬件未连接（或未报告工具）：MCP 硬件工具未注册")
        elif mcp_tools:
            self._add_system_message(
                f"已缓存 {len(mcp_tools)} 个硬件工具 schema，配置 API Key 后自动注册")

    def _apply_pending_mcp_tools(self):
        """agent 重建后补注册挂起的 MCP 硬件工具（无挂起则 no-op）。"""
        if self.agent and self._pending_mcp:
            mcp_tools, handler = self._pending_mcp
            self.agent.register_mcp_tools(mcp_tools, handler)
            if mcp_tools:
                self._add_system_message(f"已注册 {len(mcp_tools)} 个 MCP 硬件工具")

    def _add_welcome_message(self):
        self._add_ai_message(
            "你好，我是 MBDSDR AI 智能体。\n\n"
            "我可以帮你：\n"
            "• 扫频找台、自动调谐\n"
            "• 信号制式识别、质量分析\n"
            "• 基带录制、GPS/IMU 数据获取\n"
            "• 干扰源定位、天线指向指导\n\n"
            "直接用自然语言告诉我你想做什么。"
        )

    def _add_user_message(self, text: str):
        self._conversation.append({"role": "user", "content": text, "time": datetime.now()})
        _t = _tok().COLORS
        # 用户原话原文入视图：<&> 等字符必须先转义，否则被 rich text 当标签吞掉
        # （实测 "a < b" 只剩 "a "、"<script>..." 整句消失），显示与原文不符。
        self._append_to_view(
            f'<div style="margin: 6px 0; padding: 8px; background: {_t["light_user_bubble"]};'
            f' border-radius: 6px; border-left: 3px solid {_t["accent_warm"]};'
            f'"><span style="color: {_t["light_user_label"]}; font-size: 8pt;">你</span><br>'
            f'<span style="color: {_t["light_text_strong"]};">{_esc(text)}</span></div>')

    def _add_ai_message(self, text: str):
        self._conversation.append({"role": "assistant", "content": text, "time": datetime.now()})
        # 简单的换行处理（同样先转义，模型/工具文本里的 <> 不得被当标签吞掉）
        safe_text = _esc(text).replace("\n", "<br>")
        _t = _tok().COLORS
        self._append_to_view(
            f'<div style="margin: 6px 0; padding: 8px; background: {_t["light_log_bg"]};'
            f' border-radius: 6px; border-left: 3px solid {_t["status_success"]};'
            f'"><span style="color: {_t["light_ai_label"]}; font-size: 8pt;">AI</span><br>'
            f'<span style="color: {_t["light_text_strong"]};">{safe_text}</span></div>')

    def _add_system_message(self, text: str):
        _t = _tok().COLORS
        self._append_to_view(
            f'<div style="margin: 4px 0; padding: 4px 8px; background: {_t["light_system_bg"]};'
            f' border-radius: 4px; font-size: 8pt; color: {_t["accent_warm"]};'
            f'">{_esc(text)}</div>')

    # ── 执行步骤卡（字段语义对齐 cpp StepResult / mobile TaskStep）─────

    def _append_step_card(self, step: ai_steps.StepRecord):
        """渲染一张结构化步骤卡：序号/工具名/关键参数/状态/结果摘要/耗时。"""
        self._tool_log.append(step)
        idx = len(self._tool_log)
        _t = _tok().COLORS
        status_color = {
            ai_steps.STATUS_SUCCESS: _t["status_success"],
            ai_steps.STATUS_FAILED: _t["status_danger"],
            ai_steps.STATUS_RUNNING: _t["status_warning"],
            ai_steps.STATUS_SKIPPED: _t["disabled_gray"],
        }.get(step.status, _t["text_muted"])
        status_label = {
            ai_steps.STATUS_SUCCESS: "OK",
            ai_steps.STATUS_FAILED: "FAIL",
            ai_steps.STATUS_RUNNING: "运行中",
            ai_steps.STATUS_SKIPPED: "跳过",
        }.get(step.status, step.status)

        # 耗时缺值显示「—」：0ms 会被读成「瞬间完成」，与「没测到」是两回事。
        elapsed_txt = ("—" if step.elapsed_ms is None
                       else f"{step.elapsed_ms:.0f}ms")
        html = (
            f'<div>'
            f'<span style="color:{_t["text_muted"]}; font-family: monospace;">'
            f'{idx:02d}</span> '
            f'<b style="color:{_t["light_text_strong"]};">{_esc(step.tool)}</b> '
            f'<span style="color:{status_color};">{status_label}</span> '
            f'<span style="color:{_t["disabled_gray"]};">{elapsed_txt}</span>'
        )
        if step.args_preview:
            html += (f'<br><span style="color:{_t["light_text_secondary"]};">'
                     f'参数 {_esc(step.args_preview)}</span>')
        if step.summary:
            html += (f'<br><span style="color:{status_color};">'
                     f'{_esc(step.summary)}</span>')
        if step.error and step.error not in step.summary:
            html += (f'<br><span style="color:{_t["status_danger"]};">'
                     f'错误 {_esc(step.error)}</span>')
        html += '</div>'

        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {_t['light_panel_bg']};"
            f" border: 1px solid {_t['light_border_subtle']};"
            f" border-radius: 4px; }}")
        lay = QVBoxLayout(card)
        # 子步骤（workflow_execute / subagent_execute 展开）左缩进
        lay.setContentsMargins(20 if step.depth > 0 else 6, 4, 6, 4)
        lay.setSpacing(2)
        label = QLabel(html)
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setStyleSheet(
            f"font-size: 8pt; color: {_t['light_text_strong']}; background: transparent;")
        lay.addWidget(label)
        # 插在尾部 stretch 之前
        self.step_list_layout.insertWidget(self.step_list_layout.count() - 1, card)

        self.tool_log_group.setVisible(True)
        self.toggle_log_btn.setText(self.tr("隐藏执行步骤"))

    @Slot(str, dict)
    def on_tool_result(self, tool_name: str, result: dict):
        """Worker 异步工具调用结果（UI→worker 转发链）→ 追加一张步骤卡。

        软件工具不二次转发（skipped）——不是硬件执行，不进步骤卡、不制造噪声。
        """
        if not isinstance(result, dict):
            result = {"result": str(result)}
        step = ai_steps.steps_from_tool_result(tool_name, result)
        if step.status == ai_steps.STATUS_SKIPPED:
            return
        self._append_step_card(step)

    def _append_to_view(self, html: str):
        cursor = self.conversation_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertHtml(html)
        self.conversation_view.setTextCursor(cursor)
        self.conversation_view.ensureCursorVisible()

    def _send_quick_command(self, cmd: str):
        self.input_field.setText(cmd)
        self._on_submit()

    @Slot()
    def _on_submit(self):
        text = self.input_field.text().strip()
        if not text or self._is_processing:
            return

        # 斜杠命令
        if text.startswith("/"):
            self._add_user_message(text)
            if self.agent:
                response = self.agent.run_command(text)
                self._add_ai_message(response)
            else:
                self._add_ai_message("AI 未初始化，请先配置 API Key。")
            self.input_field.clear()
            return

        self.input_field.clear()
        self._add_user_message(text)
        self.command_submitted.emit(text)

        # 使用真正的 AI Agent
        if self.agent and AI_CORE_AVAILABLE:
            self._call_ai(text)
        elif not AI_CORE_AVAILABLE:
            # AI 内核完全不可用
            self._add_ai_message(
                "AI 内核模块未安装（mbdsdr_ai 不可用）。\n"
                "请先安装依赖：pip install -r requirements.txt。"
            )
        else:
            # AI 内核可用但未配置 API Key —— 不编造回复，不假装执行工具
            self._add_ai_message(
                "AI 未配置 API Key。\n\n"
                "请点击右上角「配置」按钮填入 API Key 后使用完整 AI 功能。\n"
                "当前仅支持手动控制面板操作。"
            )

    def _call_ai(self, text: str) -> bool:
        """调用真正的 AI Agent（后台线程）。忙时拒绝并返回 False。

        没配 API Key 时不返回「错误: API key 未设置」就撂挑子：转本地能力
        协商，用本进程真实装载的工具 / 编排器 / Hooks 回话。UI 会明确写
        「模型未配置」，绝不让工具回执冒充模型说的话。
        """
        if self._bridge_obj() is not None and not bool(
                getattr(getattr(self.agent, "config", None), "api_key", "")):
            return self._local_dispatch(text)
        # S10：星象仪命令栏直接调本方法，绕过 _on_submit 的忙锁会再起一个
        # AIWorker 覆盖 self._worker/_worker_thread；先结束的那个还会提前解锁。
        if self._is_processing:
            self._add_system_message("AI 正在处理上一条请求，本次指令未提交，请稍后再试")
            return False
        self._is_processing = True
        self.send_btn.setEnabled(False)
        self.send_btn.setText("思考中...")
        self._add_system_message("AI 正在思考...")

        # 创建后台线程
        self._worker_thread = QThread()
        self._worker = AIWorker(self.agent, text)
        self._worker.moveToThread(self._worker_thread)

        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_ai_finished)
        self._worker.error.connect(self._on_ai_error)
        self._worker.delta.connect(self._on_stream_delta)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)

        # 新建一条空 AI 消息用于流式追加
        self._stream_html = ""
        self._streamed_any = False  # 标记是否收到过流式增量，避免 finished 时重复追加
        _t = _tok().COLORS
        self._append_to_view(
            f'<div style="margin: 6px 0; padding: 8px; background: {_t["light_log_bg"]}; '
            f'border-radius: 6px; border-left: 3px solid {_t["status_success"]};">'
            f'<span style="color: {_t["light_ai_label"]}; font-size: 8pt;">AI</span><br>')

        self._worker_thread.start()
        return True

    # ── 本地能力协商（无模型时的通用 Agent 通路）──────────────────
    def _pop_out(self) -> None:
        """把 AI 助手页签交给主窗口的自由窗口管理器弹出（不复制控件）。"""
        w = self.window()
        fn = getattr(w, "_pop_out_tab", None)
        tabs = getattr(w, "right_tab", None)
        if fn is None or tabs is None:
            return
        for i in range(tabs.count()):
            host = tabs.widget(i)
            if host is self or (host is not None and
                                host.findChild(type(self)) is not None):
                fn("right", i)
                return

    def _bridge_obj(self):
        if self._bridge is None and LocalAgentBridge is not None \
                and self.agent is not None:
            try:
                self._bridge = LocalAgentBridge(self.agent)
            except Exception:
                return None
        if self._bridge is not None and self.agent is not None:
            try:
                self._bridge.agent = self.agent
                self._bridge.refresh()
            except Exception:
                pass
        return self._bridge

    def _local_dispatch(self, text: str, confirm: bool = False,
                        only_tool: Optional[str] = None,
                        only_args: Optional[Dict] = None) -> bool:
        bridge = self._bridge_obj()
        if bridge is None or self._local_busy:
            if bridge is None:
                self._add_system_message("本地能力协商不可用（工具注册表未装载）")
            else:
                self._add_system_message("上一条本地指令还在执行，请稍等")
            return False
        self._local_busy = True
        self.send_btn.setEnabled(False)
        self.send_btn.setText("执行中...")
        self._add_system_message("模型未配置 · 本地能力协商（按工具描述匹配，不是语言模型理解）")
        self._local_thread = QThread()
        self._local_worker = LocalWorker(bridge, text, confirm,
                                          only_tool, only_args)
        self._local_worker.moveToThread(self._local_thread)
        self._local_thread.started.connect(self._local_worker.run)
        self._local_worker.finished.connect(self._on_local_result)
        self._local_worker.finished.connect(self._local_thread.quit)
        self._local_thread.start()
        return True

    @Slot(dict)
    def _on_local_result(self, out: dict):
        self._local_busy = False
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")
        # 上一轮的人工门按钮失效：避免攒一排过期确认把用户带偏
        lay = getattr(self, "_confirm_layout", None)
        if lay is not None:
            for i in reversed(range(lay.count())):
                item = lay.itemAt(i)
                w = item.widget() if item is not None else None
                if w is not None:
                    w.hide()
                    w.setParent(None)
            self._confirm_bar.setVisible(False)
        if not isinstance(out, dict):
            self._add_ai_message("本地能力协商返回了无法识别的结果。")
            return
        _t = _tok().COLORS
        status_label = {
            "ok": ("已执行", _t["status_success"]),
            "gated": ("等待确认", _t["status_warning"]),
            "not_connected": ("设备未连接", _t["text_muted"]),
            "denied": ("被 Hooks 否决", _t["status_danger"]),
            "failed": ("执行失败", _t["status_danger"]),
            "no_match": ("没有匹配能力", _t["text_muted"]),
        }
        for card in out.get("cards", []):
            self._render_tool_card(card, status_label)
        for hit in out.get("culture", []):
            self._add_system_message(
                "星名文化（仓库数据）：%s %s · %s %s" %
                (hit.get("kind", ""), hit.get("name", ""),
                 hit.get("zh", ""), hit.get("note", "")))
        reply = out.get("reply", "")
        if reply:
            self._add_ai_message(reply)
        # 原型 quicks：给真实能力位，不预制流程；点了只是把名字送进输入栏
        sugs = [s for s in out.get("suggestions", []) if s]
        if sugs and out.get("status") in ("no_match", "culture"):
            self._add_system_message("现在在位的能力（点名字可提问）：" +
                                     " · ".join(sugs[:4]))
        tasks = out.get("tasks", [])
        hooks = out.get("hooks", [])
        if tasks or hooks:
            self._add_system_message(
                "编排任务 %d 条 · Hooks 事件 %d 条（状态条可展开看明细）" %
                (len(tasks), len(hooks)))
        self._refresh_live_bar()
        self.reply_received.emit(reply or "")

    def _render_tool_card(self, card: dict, status_label: dict) -> None:
        """工具回执卡（对齐原型 .toolcard）：工具名 + 真实状态 + 真实结果。"""
        status = card.get("status", "failed")
        label, color = status_label.get(status, ("未知", "#AAAAAA"))
        tool = _esc(str(card.get("tool", "")))
        content = _esc(str(card.get("content", ""))[:900]).replace("\n", "<br>")
        args = card.get("args") or {}
        args_txt = _esc(json.dumps(args, ensure_ascii=False)[:120]) if args else "—"
        lat = card.get("latency_ms")
        lat_txt = ("—" if lat is None else "%.0fms" % float(lat))
        html = (
            '<div style="margin:6px 0;padding:8px;background:%s;'
            'border:1px solid %s;border-radius:6px;">'
            '<b style="color:#DCE6F5;">%s</b> '
            '<span style="color:%s;">%s</span> '
            '<span style="color:#8FA0B8;">%s</span><br>'
            '<span style="color:#9AA8BC;font-size:8pt;">参数 %s</span><br>'
            '<span style="color:#CFDAEA;">%s</span></div>'
            % ("rgba(20,26,36,0.65)", color, tool, color, label,
               lat_txt, args_txt, content))
        self._append_to_view(html)
        if status == "gated" and card.get("confirm_tool"):
            name = str(card["confirm_tool"])
            cargs = dict(card.get("confirm_args") or {})
            btn = QPushButton("确认执行 " + name)
            btn.setMinimumHeight(24)
            btn.setStyleSheet(
                "font-size:8pt;padding:2px 10px;background:#B8792F;"
                "color:#0B0E14;border:none;border-radius:5px;")
            btn.clicked.connect(
                lambda _c=False, n=name, a=cargs:
                    self._local_dispatch("确认执行 " + n, confirm=True,
                                         only_tool=n, only_args=a))
            lay = getattr(self, "_confirm_layout", None)
            if lay is not None:
                lay.insertWidget(lay.count() - 1, btn)
                self._confirm_bar.setVisible(True)

    def _refresh_live_bar(self) -> None:
        bridge = self._bridge_obj()
        btn = getattr(self, "live_btn", None)
        if btn is None:
            return
        if bridge is None:
            btn.setText("● AI 内核未装载")
            return
        try:
            st = bridge.live_state()
        except Exception:
            btn.setText("● 状态读取失败")
            return
        model = "模型已配置" if st.get("model") else "模型未配置·本地协商"
        btn.setText("● %s · 工具 %s/%s · 任务 %s · 事件 %s  ▾" %
                    (model, st.get("tools_available"), st.get("tools"),
                     st.get("tasks"), st.get("events")))
        btn.setToolTip("内核在线；点开看真实任务与 Hooks 事件明细。"
                       "模型未配置时对话走本地能力协商，不编造模型回复。")
        rows = []
        for t in st.get("task_rows", [])[-4:]:
            rows.append("任务 %s · %s · %s" %
                        (_esc(str(t.get("id", ""))), _esc(str(t.get("name", ""))),
                         _esc(str(t.get("status", "")))))
        for e in st.get("event_rows", [])[-4:]:
            rows.append("事件 %s · %s" %
                        (_esc(str(e.get("event_type", ""))),
                         _esc(str((e.get("data") or {}).get("tool", "")))))
        rows.append("星名文化 %s 条 · 星座文化 %s 条（仓库数据）" %
                    (st.get("star_culture"), st.get("constellation_culture")))
        self.live_details.setText("<br>".join(rows) or "暂无任务与事件")

    def _toggle_live_details(self) -> None:
        self._refresh_live_bar()
        self.live_details.setVisible(not self.live_details.isVisible())

    # ── 天空动作 → 能力协商（按钮不得直连硬件）──────────────────

    def request_sky_action(self, context: Dict[str, Any]) -> bool:
        """把一次天空动作交给 agent 会话（携带真实目标上下文）。

        这不是问卷：只发一条上下文，模型据此自行判断要不要探测/询问，
        以及本轮能执行哪些工具。AI 不可用时诚实拒绝，不退回直连硬件。
        """
        action = str(context.get("action", ""))
        satellite = str(context.get("satellite_name")
                        or context.get("satellite") or "")
        if self._is_processing:
            self._add_system_message("AI 正在处理上一条请求，天空动作未提交，请稍后再试")
            return False
        if not AI_CORE_AVAILABLE or not self.agent:
            self._add_system_message(
                "AI 未配置，无法就硬件能力进行协商；天空动作未执行。"
                "请先配置 API Key，或改用控制面板手动操作。")
            return False

        try:
            from mbdsdr_ai.sky_negotiation import (SatelliteActionContext,
                                                  action_label,
                                                  build_action_prompt)
            ctx = SatelliteActionContext.from_mapping(context)
            ledger = getattr(self.agent, "sky_ledger", None)
            prompt = build_action_prompt(ctx, ledger)
            label = action_label(action)
        except Exception as e:  # noqa: BLE001 —— 上下文构造失败诚实拒绝
            self._add_system_message(f"天空动作上下文构造失败：{_clean_exc(e)}")
            return False

        self._pending_sky = {"action": action, "satellite": satellite}
        self._add_system_message(
            f"天空动作已交由 AI 协商硬件现实：{label}｜{satellite or '未选择卫星'}")
        self._call_ai(prompt)
        return True

    _SKY_ACTION_TOOLS = {
        "track": "sky_track_satellite",
        "tune": "sky_tune_satellite",
        "gimbal_follow": "sky_gimbal_follow",
    }

    def _resolve_pending_sky(self, tool_calls: List[Dict]) -> None:
        """一轮协商结束：告知主窗口模型是否提交了对应动作工具。"""
        pending = self._pending_sky
        if not pending:
            return
        self._pending_sky = None
        action = pending.get("action", "")
        satellite = pending.get("satellite", "")
        expected = self._SKY_ACTION_TOOLS.get(action, "")
        requested = any(
            str(tc.get("name", "")) == expected for tc in (tool_calls or [])
        ) if expected else False
        self.sky_action_resolved.emit(action, satellite, requested)

    @Slot(str)
    def _on_stream_delta(self, piece: str):
        """流式增量实时打字到 AI 消息末尾。"""
        try:
            self._streamed_any = True
            cursor = self.conversation_view.textCursor()
            cursor.movePosition(QTextCursor.End)
            cursor.insertText(piece)
            self.conversation_view.setTextCursor(cursor)
            self.conversation_view.ensureCursorVisible()
        except Exception:
            pass

    @Slot(dict)
    def _on_ai_finished(self, result: dict):
        """AI 调用完成。"""
        self._is_processing = False
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")

        content = result.get("content", "")
        tool_calls = result.get("tool_calls", [])
        tool_results = result.get("tool_results", [])
        compacted = result.get("compacted", False)
        # 缺 latency_ms = 没测到：None，绝不当成 0ms。
        latency = ai_steps.latency_ms(result)
        error = result.get("error")

        # 结构化步骤卡：本轮全部工具调用（含 workflow_execute / subagent_execute
        # 多步 JSON 结果的子步骤投影）
        steps = []
        if tool_calls:
            steps = ai_steps.steps_from_chat(tool_calls, tool_results)
            for step in steps:
                self._append_step_card(step)
            # 转发链只服务「模型请求但工具环未执行」的硬件调用（MCP 注册名）：
            # 已执行的调用在工具环内已经走 MCP handler 落到硬件，不再事后转发
            # （"先执行再转发"是被拆除的坏结构）；软件工具一律不过二次转发。
            executed = len(tool_results)
            for i, tc in enumerate(tool_calls):
                if i < executed:
                    continue
                name = tc.get("name", "")
                if name in self._mcp_tool_names:
                    raw_args = tc.get("arguments", {})
                    args = {}
                    if isinstance(raw_args, dict):
                        args = raw_args          # 已是对象：原样带参数转发
                    elif isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except Exception:
                            pass
                    if not isinstance(args, dict):
                        args = {}                # 其它类型一律不做猜测
                    self.tool_call_requested.emit(name, args)

        # 配了 API Key 走的是模型链路，但工具调用同样要进真实编排器 /
        # Hooks —— 两条入口、一份事实源，状态条数字才不会一半真一半假。
        bridge = self._bridge_obj()
        if bridge is not None and tool_calls:
            for i, tc in enumerate(tool_calls):
                name = str(tc.get("name", ""))
                if not name:
                    continue
                raw = tc.get("arguments", {})
                if isinstance(raw, dict):
                    args = dict(raw)
                elif isinstance(raw, str):
                    try:
                        args = json.loads(raw)
                    except Exception:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                else:
                    args = {}
                res = tool_results[i] if i < len(tool_results) else None
                ok = bool(res) and not (
                    isinstance(res, dict) and res.get("error"))
                bridge.record_external_call(
                    name, args, ok, "" if res is None else str(res)[:400])
            self._refresh_live_bar()

        if compacted:
            self._add_system_message("上下文已自动压缩")

        if error:
            self._add_ai_message(f"[错误] {error}\n\n{_AI_FAILURE_GUIDE}\n\n{content}")
        elif self._streamed_any:
            # 流式已把 content 打字到对话区，不再重复追加完整内容；
            # 仅在流式内容为空（模型不支持流）时才补一条消息。
            if not content:
                self._add_ai_message("（AI 未返回文本内容，可能只执行了工具调用）")
        elif content:
            self._add_ai_message(content)
        else:
            self._add_ai_message("（AI 未返回文本内容，可能只执行了工具调用）")

        failed = [s for s in steps if s.status == ai_steps.STATUS_FAILED]
        if failed:
            names = []
            for step in failed:
                if step.tool not in names:
                    names.append(step.tool)
            self._add_system_message(
                f"本轮有 {len(failed)} 个操作未成功：{'、'.join(names)}。"
                "AI 的文字回复可能没有反映这些失败；"
                "请按「执行步骤」中的错误原因处理后重试。")

        # 没测到耗时就不渲染这一整段，绝不写「耗时 0ms」。
        if latency is None:
            footer = f"调用 {len(tool_calls)} 个工具"
        else:
            footer = f"耗时 {latency:.0f}ms，调用 {len(tool_calls)} 个工具"
        if failed:
            footer += f"，其中 {len(failed)} 个失败"
        self._add_system_message(footer)
        self._resolve_pending_sky(tool_calls)
        self.reply_received.emit(content or "")

    @Slot(str)
    def _on_ai_error(self, error: str):
        """AI 调用出错。"""
        self._is_processing = False
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")
        self._add_ai_message(f"AI 调用出错: {error}\n\n{_AI_FAILURE_GUIDE}")
        # 协商轮次失败：主窗口必须把按钮回弹，不留下「假装已执行」的勾选
        self._resolve_pending_sky([])
        self.reply_received.emit(f"[错误] {error}")

    def _toggle_log(self):
        visible = self.tool_log_group.isVisible()
        self.tool_log_group.setVisible(not visible)
        self.toggle_log_btn.setText(
            "隐藏执行步骤" if not visible else "显示执行步骤")

    def get_conversation(self) -> List[Dict]:
        return self._conversation

    def clear_conversation(self):
        self._conversation.clear()
        self.conversation_view.clear()
        self._tool_log.clear()
        while self.step_list_layout.count() > 1:  # 末位 stretch 保留
            item = self.step_list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._add_welcome_message()
        if self.agent:
            self.agent.context_manager.clear_history()
