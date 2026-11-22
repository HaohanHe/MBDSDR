# SPDX-License-Identifier: MIT
"""AI 面板图片附件链路（offscreen，不联网、不碰真实模型）。

覆盖：
1. 「图片」按钮 → QFileDialog 多选 → 输入框上方出现文件名 chip；
2. 发送时把路径列表原样交给 AIWorker/agent.chat(images=...) 且文本不变；
3. 发送后附件清空、chip 隐藏；
4. 无附件时仍走旧调用签名（不会给 agent 多传 images 参数）；
5. 图片错误（不存在/类型不支持/空图）由真实 agent 校验器判错，
   界面必须出现可见错误文案（不复制前端扩展名校验假装成功）；
6. 附件路径不进入会话文本。

跑法：QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ai_panel_image_attachments.py -q
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QLabel  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)  # noqa: F401

from desktop import ai_panel as ap  # noqa: E402
from desktop.ai_panel import AIPanel  # noqa: E402
from mbdsdr_ai.agent import ImageInputError, build_user_content  # noqa: E402


def _reply(content=""):
    return {"content": content, "tool_calls": [], "tool_results": [],
            "usage": {}, "latency_ms": 0, "compacted": False, "error": None}


class _StubAgentBase:
    """最小替身：配置齐 api_key，确保走模型通道而不是本地协商。"""

    def __init__(self):
        self.calls = []
        self.config = type("C", (), {
            "api_key": "stub-key", "base_url": "http://stub",
            "model": "stub/model-x"})()


class _RecordingAgent(_StubAgentBase):
    """记录 chat 实参的替身；不读网络、不生成回复。"""

    def chat(self, user_input, on_delta=None, **kwargs):
        self.calls.append({"text": user_input, **kwargs})
        return _reply("收到")


class _NoImagesAgent(_StubAgentBase):
    """严格旧签名：无附件时若被多传 images 参数就会 TypeError。"""

    def chat(self, user_input, on_delta=None):
        self.calls.append(user_input)
        return _reply("收到")


class _ValidationAgent(_StubAgentBase):
    """用 agent 的真实校验器判图片输入，错误按真实错误信封返回。"""

    def chat(self, user_input, on_delta=None, images=None):
        self.calls.append({"text": user_input, "images": images})
        try:
            build_user_content(user_input, images)
        except ImageInputError as e:
            return {
                "content": f"错误: {e}", "tool_calls": [], "tool_results": [],
                "usage": {}, "latency_ms": 0, "compacted": False,
                "error": e.code, "error_code": e.code,
                "interrupted": False, "cancelled": False, "busy": False,
                "rounds": 0,
            }
        return _reply("图片已收到")


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr(AIPanel, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(AIPanel, "CONFIG_FILE", str(tmp_path / "config.json"))
    for k in ("MBDSDR_API_KEY", "MBDSDR_BASE_URL", "MBDSDR_MODEL",
              "MBDSDR_API_BASE"):
        monkeypatch.delenv(k, raising=False)
    yield


@pytest.fixture
def panel():
    p = AIPanel()
    yield p
    p.close()
    p.deleteLater()


def _spin_until(predicate, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _pick(panel, monkeypatch, paths):
    seen = {}

    def _fake(parent, caption, directory, filters):
        seen["caption"] = caption
        seen["filters"] = filters
        return list(paths), filters

    monkeypatch.setattr(ap.QFileDialog, "getOpenFileNames", _fake)
    panel._on_pick_images()
    return seen


def _png(tmp_path, name="a.png"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake-image-body")
    return p


def _chip_labels(panel):
    return [lbl.text() for lbl in panel.attach_bar.findChildren(QLabel)]


def _send_and_wait(panel, text):
    panel.input_field.setText(text)
    panel._on_submit()
    thread = panel._worker_thread
    assert _spin_until(lambda: not panel._is_processing, 10.0), \
        "AI worker 线程未在 10s 内把结果交回 UI 线程"
    if thread is not None:
        try:
            thread.wait(5000)
        except RuntimeError:  # 线程对象已 deleteLater，事件已处理完
            pass


class TestAttachmentChips:
    def test_picker_filter_and_multiselect_show_chips(self, panel, tmp_path,
                                                      monkeypatch):
        a = _png(tmp_path, "a.png")
        b = _png(tmp_path, "b.jpg")
        seen = _pick(panel, monkeypatch, [str(a), str(b)])

        assert "*.png" in seen["filters"] and "*.webp" in seen["filters"]
        assert panel._attachments == [str(a), str(b)]
        labels = _chip_labels(panel)
        assert "a.png" in labels and "b.jpg" in labels
        assert panel.attach_bar.isVisibleTo(panel) is True

    def test_remove_button_drops_only_that_chip(self, panel, tmp_path,
                                                monkeypatch):
        a = _png(tmp_path, "a.png")
        b = _png(tmp_path, "b.png")
        _pick(panel, monkeypatch, [str(a), str(b)])
        remove_buttons = [btn for btn in panel.attach_bar.findChildren(
            ap.QPushButton) if btn.text() == "×"]
        assert len(remove_buttons) == 2

        remove_buttons[0].click()
        assert panel._attachments == [str(b)]
        assert "a.png" not in _chip_labels(panel)
        assert "b.png" in _chip_labels(panel)

    def test_reselecting_same_path_does_not_duplicate(self, panel, tmp_path,
                                                      monkeypatch):
        a = _png(tmp_path, "a.png")
        _pick(panel, monkeypatch, [str(a)])
        _pick(panel, monkeypatch, [str(a)])
        assert panel._attachments == [str(a)]


class TestSendWithImages:
    def test_paths_reach_agent_and_text_is_unchanged(self, panel, tmp_path,
                                                     monkeypatch):
        a = _png(tmp_path, "a.png")
        b = _png(tmp_path, "b.png")
        _pick(panel, monkeypatch, [str(a), str(b)])
        agent = _RecordingAgent()
        panel.agent = agent

        _send_and_wait(panel, "看看这两张图")

        assert agent.calls == [{
            "text": "看看这两张图",
            "images": [str(a), str(b)],
        }]
        # 发送后附件清空、chip 隐藏
        assert panel._attachments == []
        assert panel.attach_bar.isVisibleTo(panel) is False
        # 会话文本保持用户原话，不混入本机路径
        user_msgs = [m for m in panel.get_conversation() if m["role"] == "user"]
        assert user_msgs[-1]["content"] == "看看这两张图"
        view = panel.conversation_view.toPlainText()
        assert "看看这两张图" in view
        assert str(a) not in view and str(b) not in view

    def test_without_images_uses_old_chat_signature(self, panel):
        agent = _NoImagesAgent()
        panel.agent = agent

        _send_and_wait(panel, "只有文字")

        assert agent.calls == ["只有文字"]
        assert panel._attachments == []

    def test_image_only_submit_is_allowed_with_empty_text(self, panel, tmp_path,
                                                          monkeypatch):
        a = _png(tmp_path, "a.png")
        _pick(panel, monkeypatch, [str(a)])
        agent = _RecordingAgent()
        panel.agent = agent

        _send_and_wait(panel, "")

        assert agent.calls == [{"text": "", "images": [str(a)]}]
        assert panel._attachments == []


class TestImageErrorVisibility:
    @pytest.mark.parametrize("kind,expected", (
        ("missing", ("图片文件不存在", "image_not_found")),
        ("unsupported", ("不支持的图片类型", "image_type_unsupported")),
        ("empty", ("图片文件为空", "image_invalid")),
    ))
    def test_agent_rejection_is_visible_and_not_swallowed(
            self, panel, tmp_path, monkeypatch, kind, expected):
        if kind == "missing":
            path = tmp_path / "nope.png"
        elif kind == "unsupported":
            path = tmp_path / "x.bmp"
            path.write_bytes(b"BMfake")
        else:
            path = tmp_path / "empty.png"
            path.write_bytes(b"")
        _pick(panel, monkeypatch, [str(path)])
        panel.agent = _ValidationAgent()

        _send_and_wait(panel, "这张图是什么")

        view = panel.conversation_view.toPlainText()
        message, code = expected
        assert message in view, "图片错误被静默吞掉: %r" % view[-400:]
        assert code in view, "错误信封的 error_code 必须可见: %r" % view[-400:]
        assert "下一步" in view, "图片错误必须给出可执行的下一步"
        # 后端判错后附件同样不滞留
        assert panel._attachments == []

    def test_guidance_is_image_specific_not_credential(self):
        text = ap._failure_guidance("image_not_found", "图片文件不存在: x.png")
        assert "图片" in text and "API Key" not in text
        assert "重试" not in text


def _real_png(tmp_path, name="real.png", w=800, h=600):
    """真实可解码的临时 PNG（既有 _png() 的假字节只用于验证后端判错）。"""
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(QColor("#3366cc"))
    path = tmp_path / name
    assert img.save(str(path), "PNG"), "临时 PNG 写入失败"
    return path


def _embedded_images(view):
    """对话文档里的内嵌图片：(文档位置, QTextImageFormat)。"""
    out = []
    block = view.document().begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().isImageFormat():
                out.append((frag.position(), frag.charFormat().toImageFormat()))
            it += 1
        block = block.next()
    return out


class TestConversationThumbnails:
    """用户气泡里的缩略图：等比上限、可点击预览、坏图占位、不泄露路径。"""

    def test_bubble_shows_scaled_thumbnails_in_proportion(
            self, panel, tmp_path, monkeypatch):
        wide = _real_png(tmp_path, "wide.png", 800, 400)
        tall = _real_png(tmp_path, "tall.png", 400, 800)
        _pick(panel, monkeypatch, [str(wide), str(tall)])
        panel.agent = _RecordingAgent()

        _send_and_wait(panel, "两张图")

        images = _embedded_images(panel.conversation_view)
        assert len(images) == 2, "用户气泡里应有两张缩略图"
        for _pos, fmt in images:
            assert fmt.width() <= ap.THUMBNAIL_MAX_EDGE
            assert fmt.height() <= ap.THUMBNAIL_MAX_EDGE
            assert max(fmt.width(), fmt.height()) == ap.THUMBNAIL_MAX_EDGE, \
                "缩略图最大边应缩到上限（不放大、不留原尺寸）"
        ratios = [fmt.width() / fmt.height() for _pos, fmt in images]
        assert ratios[0] == pytest.approx(2.0, abs=0.05)
        assert ratios[1] == pytest.approx(0.5, abs=0.05)

        # 悬停提示只登记文件名；会话文本/富文本都不含本机路径
        names = sorted(panel.conversation_view._thumb_names.values())
        assert names == ["tall.png", "wide.png"]
        view = panel.conversation_view.toPlainText()
        html = panel.conversation_view.toHtml()
        assert str(tmp_path) not in view
        assert str(tmp_path) not in html
        assert str(wide) not in view and str(wide) not in html

    def test_clicking_thumbnail_opens_preview_dialog(
            self, panel, tmp_path, monkeypatch):
        shot = _real_png(tmp_path, "shot.png", 640, 480)
        _pick(panel, monkeypatch, [str(shot)])
        panel.agent = _RecordingAgent()
        _send_and_wait(panel, "看这张")

        panel.resize(520, 720)
        panel.show()
        app.processEvents()

        images = _embedded_images(panel.conversation_view)
        assert len(images) == 1
        pos, _fmt = images[0]
        cursor = panel.conversation_view.textCursor()
        cursor.setPosition(pos)
        rect = panel.conversation_view.cursorRect(cursor)
        assert rect.height() > 0
        QTest.mouseClick(panel.conversation_view.viewport(), Qt.LeftButton,
                         pos=rect.center())
        app.processEvents()

        dialogs = [d for d in panel._preview_dialogs
                   if isinstance(d, QDialog) and d.isVisible()]
        assert len(dialogs) == 1, "点击缩略图应弹出预览对话框"
        dialog = dialogs[0]
        assert dialog.windowTitle() == "shot.png"
        pixmaps = [lbl.pixmap() for lbl in dialog.findChildren(QLabel)]
        assert any(p is not None and not p.isNull() for p in pixmaps), \
            "预览对话框里应显示原图像素"
        # 非模态预览不阻塞测试；用 QTimer 收尾后本对话框应退出持有列表。
        QTimer.singleShot(0, dialog.close)
        app.processEvents()
        panel.hide()
        assert dialog not in panel._preview_dialogs

    def test_missing_file_shows_cannot_preview_placeholder(
            self, panel, tmp_path, monkeypatch):
        missing = tmp_path / "gone.png"
        _pick(panel, monkeypatch, [str(missing)])
        panel.agent = _RecordingAgent()

        _send_and_wait(panel, "这张还在吗")

        view = panel.conversation_view.toPlainText()
        assert "无法预览" in view
        assert "gone.png" in view
        assert str(missing) not in view
        assert str(tmp_path) not in panel.conversation_view.toHtml()
        assert _embedded_images(panel.conversation_view) == []
        # 坏图不产生可点击的预览句柄
        assert panel._thumb_sources == {}

    def test_no_images_keeps_old_count_note_and_no_thumbnail(self, panel):
        panel._add_user_message("只有文字", image_count=2)
        view = panel.conversation_view.toPlainText()
        assert "（附图 2 张）" in view
        assert _embedded_images(panel.conversation_view) == []
        assert "<img" not in panel.conversation_view.toHtml()
