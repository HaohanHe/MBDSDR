# SPDX-License-Identifier: MIT
"""AI 配置对话框里的端点体检：结论必须能区分五种「不能用」。

背景：本地 DeepSeek 没有权重/服务时，用户只看到一条通用异常。``probe_endpoint``
已能分状态，本文件钉住 UI 接线三件事：

1. 对话框里真有「检测端点」按钮与初始「尚未检测」；
2. 结论分档：可用=success；离线/无模型/模型名不在列表=warning；其余=danger；
3. 离线模式绝不打网络（注入的 http_get 一旦被调用就失败）。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QDialog, QLabel, QPushButton)

import pytest  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

_BASE = "http://127.0.0.1:11434/v1"


@pytest.fixture(autouse=True)
def _online(monkeypatch):
    """测试进程可能带着 MBDSDR_OFFLINE=1；除离线用例外都按在线探测。"""
    monkeypatch.delenv("MBDSDR_OFFLINE", raising=False)


def _panel():
    from desktop.ai_panel import AIPanel
    return AIPanel()


def test_dialog_has_detect_button_and_initial_state(monkeypatch):
    panel = _panel()
    seen = {}

    def _fake_exec(dialog):
        texts = [b.text() for b in dialog.findChildren(QPushButton)]
        labels = [lb.text() for lb in dialog.findChildren(QLabel)]
        seen["buttons"] = texts
        seen["labels"] = labels
        return QDialog.Rejected

    monkeypatch.setattr(QDialog, "exec", _fake_exec)
    panel._show_config()
    assert "检测端点" in seen["buttons"]
    assert any("尚未检测" in t for t in seen["labels"])


def test_ready_endpoint_is_green():
    panel = _panel()
    text, level = panel._probe_local_endpoint(
        _BASE, "", "deepseek-r1:7b",
        http_get=lambda url, headers, timeout: {
            "data": [{"id": "deepseek-r1:7b"}]})
    assert "可用" in text and "deepseek-r1:7b" in text
    assert level == "success"


def test_wrong_model_name_is_warning_not_ready():
    panel = _panel()
    text, level = panel._probe_local_endpoint(
        _BASE, "", "deepseek-r1:7b",
        http_get=lambda url, headers, timeout: {
            "data": [{"id": "qwen2.5:7b"}]})
    assert "不在列表" in text
    assert level == "warning", "服务在但模型名不对，不能挂绿"


def test_no_models_loaded_is_warning():
    panel = _panel()
    text, level = panel._probe_local_endpoint(
        _BASE, "", "", http_get=lambda url, headers, timeout: {"data": []})
    assert "没有模型" in text
    assert level == "warning"


def test_service_not_running_is_danger():
    panel = _panel()
    import requests

    def _refused(url, headers, timeout):
        raise requests.exceptions.ConnectionError("refused")

    text, level = panel._probe_local_endpoint(_BASE, "", "", http_get=_refused)
    assert "端点未启动" in text
    assert level == "danger"


def test_unauthorized_is_danger():
    panel = _panel()
    import requests

    def _unauthorized(url, headers, timeout):
        resp = requests.Response()
        resp.status_code = 401
        raise requests.exceptions.HTTPError("401", response=resp)

    text, level = panel._probe_local_endpoint(
        _BASE, "bad-key", "", http_get=_unauthorized)
    assert "认证失败" in text
    assert level == "danger"


def test_offline_never_touches_network(monkeypatch):
    panel = _panel()
    monkeypatch.setenv("MBDSDR_OFFLINE", "1")

    def _boom(url, headers, timeout):
        raise AssertionError("离线模式下不得发起探测请求")

    text, level = panel._probe_local_endpoint(_BASE, "", "", http_get=_boom)
    assert "离线模式" in text
    assert level == "warning"


def test_empty_base_url_is_reported_not_raised():
    panel = _panel()
    text, level = panel._probe_local_endpoint("", "", "")
    assert "探测失败" in text and "base_url 为空" in text
    assert level == "danger"
