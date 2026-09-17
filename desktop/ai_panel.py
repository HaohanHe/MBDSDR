import os
"""
MBDSDR AI 对话面板
===================
自然语言指令输入、对话历史、工具调用日志。
AI 是技能触发入口（用户对模型说才触发），不是功能按钮。
使用真正的 MBDSDRAgent（LLM 驱动，支持工具调用、上下文管理、记忆系统）。
"""

import json
import traceback
from datetime import datetime
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, Signal, Slot, QTimer, QThread, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextEdit, QLineEdit, QFrame, QGroupBox, QComboBox,
    QSplitter, QSizePolicy, QMessageBox
)
from PySide6.QtGui import QFont, QTextCursor

# 尝试导入 AI 内核
try:
    from mbdsdr_ai import MBDSDRAgent, AgentConfig
    AI_CORE_AVAILABLE = True
except ImportError:
    AI_CORE_AVAILABLE = False


# 快捷指令（这些是提示用户可以说什么，不是直接执行的功能按钮）
QUICK_COMMANDS = [
    "找一个信号强的 FM 电台",
    "扫频 87-108 MHz 找所有电台",
    "调谐到 98.5 MHz 并录 30 秒",
    "当前信号质量怎么样",
    "获取 GPS 定位",
    "查看 9 轴姿态",
    "自动找干扰源方向",
    "识别当前信号制式",
]


class AIWorker(QObject):
    """后台 AI 调用 Worker（避免 UI 卡顿）。"""
    finished = Signal(dict)  # 结果
    error = Signal(str)       # 错误

    def __init__(self, agent: "MBDSDRAgent", user_input: str):
        super().__init__()
        self.agent = agent
        self.user_input = user_input

    @Slot()
    def run(self):
        try:
            result = self.agent.chat(self.user_input)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()[:500]}")


class AIPanel(QWidget):
    """AI 对话面板。"""

    # 配置文件路径
    CONFIG_DIR = os.path.expanduser("~/.mbdsdr")
    CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

    # 信号：请求调用工具（外部 MCP 客户端处理）
    tool_call_requested = Signal(str, dict)  # (tool_name, params)
    # 信号：自然语言指令
    command_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._conversation: List[Dict] = []
        self._tool_log: List[Dict] = []
        self.agent: Optional["MBDSDRAgent"] = None
        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[AIWorker] = None
        self._is_processing = False
        self._saved_config = self._load_config()
        self._build_ui()
        self._add_welcome_message()

    @classmethod
    def _load_config(cls) -> Dict[str, Any]:
        """从配置文件加载 API 配置。"""
        try:
            if os.path.exists(cls.CONFIG_FILE):
                with open(cls.CONFIG_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    @classmethod
    def _save_config(cls, config: Dict[str, Any]):
        """保存 API 配置到配置文件。"""
        try:
            os.makedirs(cls.CONFIG_DIR, exist_ok=True)
            with open(cls.CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            # 限制配置文件权限（仅所有者可读写）
            try:
                os.chmod(cls.CONFIG_FILE, 0o600)
            except Exception:
                pass
        except Exception:
            pass

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 标题栏
        title_bar = QHBoxLayout()
        title_label = QLabel("AI 智能体")
        title_label.setStyleSheet("font-size: 11pt; font-weight: 600; color: #3D3D3D;")
        title_bar.addWidget(title_label)
        title_bar.addStretch()

        # AI 内核状态指示
        self.ai_status_label = QLabel()
        self.ai_status_label.setStyleSheet("font-size: 8pt; color: #888;")
        self._update_ai_status()
        title_bar.addWidget(self.ai_status_label)

        # API Key 配置按钮
        self.config_btn = QPushButton("配置")
        self.config_btn.setFixedHeight(24)
        self.config_btn.setStyleSheet("font-size: 8pt; padding: 2px 8px;")
        self.config_btn.clicked.connect(self._show_config)
        title_bar.addWidget(self.config_btn)

        layout.addLayout(title_bar)

        # 对话历史
        self.conversation_view = QTextEdit()
        self.conversation_view.setReadOnly(True)
        self.conversation_view.setStyleSheet("""
            QTextEdit {
                background: #FAFAF8;
                border: 1px solid #E0DDD8;
                border-radius: 6px;
                padding: 8px;
                font-size: 9pt;
                color: #3D3D3D;
            }
        """)
        self.conversation_view.setMinimumHeight(150)
        layout.addWidget(self.conversation_view, 1)

        # 快捷指令
        quick_group = QGroupBox("快捷指令（点击发送）")
        quick_group.setStyleSheet("QGroupBox { font-size: 8pt; color: #888; border: none; margin-top: 4px; }")
        quick_layout = QHBoxLayout()
        quick_layout.setSpacing(4)
        quick_layout.setContentsMargins(0, 4, 0, 0)
        for cmd in QUICK_COMMANDS[:4]:
            btn = QPushButton(cmd)
            btn.setFixedHeight(26)
            btn.setStyleSheet("""
                QPushButton {
                    font-size: 8pt;
                    padding: 2px 8px;
                    background: #F0EDE8;
                    border: 1px solid #DDD8D0;
                    border-radius: 4px;
                    color: #5A5A5A;
                }
                QPushButton:hover { background: #E8E4DE; }
            """)
            btn.clicked.connect(lambda checked, c=cmd: self._send_quick_command(c))
            quick_layout.addWidget(btn)
        quick_layout.addStretch()
        quick_group.setLayout(quick_layout)
        layout.addWidget(quick_group)

        # 输入栏
        input_bar = QHBoxLayout()
        input_bar.setSpacing(6)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("对 AI 说点什么...（如：扫频找台、调谐、录音）")
        self.input_field.setStyleSheet("""
            QLineEdit {
                padding: 6px 10px;
                border: 1px solid #D0CCC4;
                border-radius: 6px;
                font-size: 9pt;
                background: #FFF;
            }
            QLineEdit:focus { border-color: #B8A88A; }
        """)
        self.input_field.returnPressed.connect(self._on_submit)
        input_bar.addWidget(self.input_field, 1)

        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedHeight(32)
        self.send_btn.setStyleSheet("""
            QPushButton {
                padding: 4px 16px;
                background: #8B7355;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 9pt;
                font-weight: 500;
            }
            QPushButton:hover { background: #7A6449; }
            QPushButton:disabled { background: #CCC; }
        """)
        self.send_btn.clicked.connect(self._on_submit)
        input_bar.addWidget(self.send_btn)

        layout.addLayout(input_bar)

        # 工具调用日志（可折叠）
        self.tool_log_group = QGroupBox("工具调用日志")
        self.tool_log_group.setStyleSheet("QGroupBox { font-size: 8pt; color: #888; border: none; margin-top: 4px; }")
        tool_log_layout = QVBoxLayout()
        tool_log_layout.setContentsMargins(0, 4, 0, 0)
        self.tool_log_view = QTextEdit()
        self.tool_log_view.setReadOnly(True)
        self.tool_log_view.setMaximumHeight(100)
        self.tool_log_view.setStyleSheet("""
            QTextEdit {
                background: #F5F3F0;
                border: 1px solid #E0DDD8;
                border-radius: 4px;
                padding: 4px;
                font-size: 8pt;
                color: #666;
                font-family: monospace;
            }
        """)
        tool_log_layout.addWidget(self.tool_log_view)
        self.tool_log_group.setLayout(tool_log_layout)
        self.tool_log_group.setVisible(False)  # 默认隐藏
        layout.addWidget(self.tool_log_group)

        # 切换日志显示按钮
        self.toggle_log_btn = QPushButton("显示工具日志")
        self.toggle_log_btn.setFixedHeight(20)
        self.toggle_log_btn.setStyleSheet("font-size: 8pt; color: #999; border: none;")
        self.toggle_log_btn.clicked.connect(self._toggle_log)
        layout.addWidget(self.toggle_log_btn)

    def _update_ai_status(self):
        """更新 AI 内核状态指示。"""
        if AI_CORE_AVAILABLE and self.agent:
            model = self.agent.model_manager.model
            self.ai_status_label.setText(f"LLM: {model.split('/')[-1]}")
            self.ai_status_label.setStyleSheet("font-size: 8pt; color: #5A8A5A;")
        elif AI_CORE_AVAILABLE:
            self.ai_status_label.setText("未配置 API")
            self.ai_status_label.setStyleSheet("font-size: 8pt; color: #C8A040;")
        else:
            self.ai_status_label.setText("AI 内核不可用")
            self.ai_status_label.setStyleSheet("font-size: 8pt; color: #C05050;")

    def _show_config(self):
        """显示 API 配置对话框。"""
        if not AI_CORE_AVAILABLE:
            QMessageBox.warning(self, "AI 内核不可用", "mbdsdr_ai 模块未找到，请检查安装。")
            return

        from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("AI 配置")
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
            or self._saved_config.get("api_base", "https://api.siliconflow.cn/v1")
        )
        form.addRow("Base URL:", base_url_edit)

        model_edit = QLineEdit()
        model_edit.setText(
            (self.agent.config.model if self.agent else None)
            or self._saved_config.get("model", "Qwen/Qwen3.6-35B-A3B")
        )
        form.addRow("模型:", model_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)

        if dialog.exec() == QDialog.Accepted:
            api_key = api_key_edit.text().strip()
            base_url = base_url_edit.text().strip()
            model = model_edit.text().strip()
            # 保存到配置文件
            self._saved_config = {
                "api_key": api_key,
                "api_base": base_url,
                "model": model,
            }
            self._save_config(self._saved_config)
            # 重新初始化 agent
            config = AgentConfig(api_key=api_key, base_url=base_url, model=model)
            self.agent = MBDSDRAgent(config)
            self._update_ai_status()
            self._add_system_message(f"AI 已配置并保存: {model}（配置文件: ~/.mbdsdr/config.json）")

    def init_agent(self, api_key: str = "", base_url: str = "", model: str = ""):
        """初始化 AI Agent（由外部调用）。

        配置优先级：传入参数 > 配置文件(~/.mbdsdr/config.json) > 环境变量 > 默认值
        """
        if not AI_CORE_AVAILABLE:
            self._add_system_message("警告: mbdsdr_ai 模块不可用，使用规则引擎降级。")
            return

        # 优先级：参数 > 配置文件 > 环境变量 > 默认值
        final_api_key = (
            api_key
            or self._saved_config.get("api_key", "")
            or os.environ.get("MBDSDR_API_KEY", "")
        )
        final_base_url = (
            base_url
            or self._saved_config.get("api_base", "")
            or os.environ.get("MBDSDR_API_BASE", "https://api.siliconflow.cn/v1")
        )
        final_model = (
            model
            or self._saved_config.get("model", "")
            or os.environ.get("MBDSDR_MODEL", "Qwen/Qwen3.6-35B-A3B")
        )

        config = AgentConfig(api_key=final_api_key, base_url=final_base_url, model=final_model)
        self.agent = MBDSDRAgent(config)
        self._update_ai_status()
        source = "参数" if api_key else ("配置文件" if self._saved_config.get("api_key") else ("环境变量" if os.environ.get("MBDSDR_API_KEY") else "默认"))
        self._add_system_message(f"AI 内核已初始化: {final_model}（配置来源: {source}）")

    def register_mcp_tools(self, mcp_tools: List[Dict], mcp_call_handler):
        """注册 MCP 硬件工具到 AI Agent。"""
        if self.agent:
            self.agent.register_mcp_tools(mcp_tools, mcp_call_handler)
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
        self._append_to_view(f'<div style="margin: 6px 0; padding: 8px; background: #EDE8E0; border-radius: 6px; border-left: 3px solid #8B7355;"><span style="color: #6B5B45; font-size: 8pt;">你</span><br><span style="color: #3D3D3D;">{text}</span></div>')

    def _add_ai_message(self, text: str):
        self._conversation.append({"role": "assistant", "content": text, "time": datetime.now()})
        # 简单的换行处理
        safe_text = text.replace("\n", "<br>")
        self._append_to_view(f'<div style="margin: 6px 0; padding: 8px; background: #F5F3F0; border-radius: 6px; border-left: 3px solid #5A8A5A;"><span style="color: #4A7A4A; font-size: 8pt;">AI</span><br><span style="color: #3D3D3D;">{safe_text}</span></div>')

    def _add_system_message(self, text: str):
        self._append_to_view(f'<div style="margin: 4px 0; padding: 4px 8px; background: #FFF8E8; border-radius: 4px; font-size: 8pt; color: #8B7355;">{text}</div>')

    def _add_tool_log(self, tool_name: str, args: Dict, success: bool, result: str, latency: float):
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "tool": tool_name,
            "args": args,
            "success": success,
            "result": result[:200],
            "latency": latency,
        }
        self._tool_log.append(entry)
        status = "OK" if success else "FAIL"
        color = "#5A8A5A" if success else "#C05050"
        self.tool_log_view.append(
            f'<span style="color:{color};">{status}</span> '
            f'<span style="color:#888;">{entry["time"]}</span> '
            f'<b>{tool_name}</b>({json.dumps(args, ensure_ascii=False)[:80]}) '
            f'<span style="color:#999;">{latency:.0f}ms</span>'
        )

    @Slot(str, dict)
    def on_tool_result(self, tool_name: str, result: dict):
        """Worker 工具调用结果回调（MCP/设备工具调用完成后更新 AI 面板日志）。"""
        if not isinstance(result, dict):
            result = {"result": str(result)}
        success = "error" not in result
        result_str = json.dumps(result, ensure_ascii=False)[:300]
        self._add_tool_log(tool_name, {}, success, result_str, 0.0)

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
        else:
            # 降级：规则引擎
            self._rule_based_response(text)

    def _call_ai(self, text: str):
        """调用真正的 AI Agent（后台线程）。"""
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
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.error.connect(self._worker_thread.quit)
        self._worker_thread.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)

        self._worker_thread.start()

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
        latency = result.get("latency_ms", 0)
        error = result.get("error")

        # 显示工具调用过程
        if tool_calls:
            self.tool_log_group.setVisible(True)
            self.toggle_log_btn.setText("隐藏工具日志")
            for i, tc in enumerate(tool_calls):
                args = {}
                try:
                    args = json.loads(tc.get("arguments", "{}"))
                except Exception:
                    pass
                tr = tool_results[i] if i < len(tool_results) else {}
                self._add_tool_log(
                    tc.get("name", "unknown"),
                    args,
                    tr.get("success", False),
                    tr.get("content", ""),
                    tr.get("latency_ms", 0),
                )
                # 发射工具调用信号（外部可监听）
                self.tool_call_requested.emit(tc.get("name", ""), args)

        if compacted:
            self._add_system_message("上下文已自动压缩")

        if error:
            self._add_ai_message(f"[错误] {error}\n\n{content}")
        elif content:
            self._add_ai_message(content)
        else:
            self._add_ai_message("（AI 未返回文本内容，可能只执行了工具调用）")

        self._add_system_message(f"耗时 {latency:.0f}ms，调用 {len(tool_calls)} 个工具")

    @Slot(str)
    def _on_ai_error(self, error: str):
        """AI 调用出错。"""
        self._is_processing = False
        self.send_btn.setEnabled(True)
        self.send_btn.setText("发送")
        self._add_ai_message(f"AI 调用出错: {error}")

    def _rule_based_response(self, text: str):
        """降级规则引擎（AI 不可用时使用）。"""
        text_lower = text.lower()

        # 调谐
        import re
        freq_match = re.search(r'(\d+\.?\d*)\s*(mhz|兆赫|MHz)', text_lower)
        if freq_match:
            freq = float(freq_match.group(1))
            self.tool_call_requested.emit("tune_fm", {"frequency": freq})
            self._add_ai_message(f"好的，正在调谐到 FM {freq} MHz...")
            return

        # 扫频
        if any(kw in text_lower for kw in ["扫频", "扫描", "找台", "找电台", "scan"]):
            self.tool_call_requested.emit("scan_fm", {"start": 87.5, "end": 108.0})
            self._add_ai_message("好的，正在 87.5-108 MHz 扫频找台...")
            return

        # 录音
        if any(kw in text_lower for kw in ["录音", "录制", "record", "录"]):
            self.tool_call_requested.emit("start_record", {})
            self._add_ai_message("好的，开始录音。再次说'停止录音'结束。")
            return

        # GPS
        if any(kw in text_lower for kw in ["gps", "定位", "位置", "坐标"]):
            self.tool_call_requested.emit("get_gps", {})
            self._add_ai_message("正在获取 GPS 定位...")
            return

        # IMU
        if any(kw in text_lower for kw in ["imu", "姿态", "加速度", "陀螺仪", "磁力计"]):
            self.tool_call_requested.emit("get_imu", {})
            self._add_ai_message("正在获取 9 轴姿态数据...")
            return

        # 状态
        if any(kw in text_lower for kw in ["状态", "status", "信号质量", "信号怎么样"]):
            self.tool_call_requested.emit("get_status", {})
            self._add_ai_message("正在获取设备状态...")
            return

        # 找干扰源
        if any(kw in text_lower for kw in ["干扰", "干扰源", "找干扰"]):
            self._add_ai_message(
                "找干扰源需要以下步骤：\n"
                "1. 先用全向 GP 天线探测干扰频段\n"
                "2. 再用八木天线+云台进行指向性扫描\n"
                "3. 通过信号强度变化定位干扰源方向\n\n"
                "这是一个技能，需要你说'开始找干扰源'才会触发。"
            )
            return

        # 默认
        self._add_ai_message(
            f"我理解你想：{text}\n\n"
            "当前使用规则引擎降级模式。配置 API Key 后可使用完整 AI 能力。\n"
            "你可以试试：扫频找台、调谐到 XX MHz、录音、获取 GPS、查看姿态。"
        )

    def _toggle_log(self):
        visible = self.tool_log_group.isVisible()
        self.tool_log_group.setVisible(not visible)
        self.toggle_log_btn.setText("隐藏工具日志" if not visible else "显示工具日志")

    def get_conversation(self) -> List[Dict]:
        return self._conversation

    def clear_conversation(self):
        self._conversation.clear()
        self.conversation_view.clear()
        self._tool_log.clear()
        self.tool_log_view.clear()
        self._add_welcome_message()
        if self.agent:
            self.agent.context_manager.clear_history()
