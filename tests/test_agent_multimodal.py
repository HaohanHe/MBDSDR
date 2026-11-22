#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""MBDSDRAgent 图片输入（多模态）离线测试。

不联网、不需要 API Key：用假 model_manager.chat_stream 捕获实际发给
LLM 的 messages，断言：
- 无 images 时 user content 仍是字符串（向后兼容，形状不变）；
- 有 images 时 user content 是 parts 列表，含文本 part 与 image_url part，
  本地文件转成 base64 data URL（不依赖外网/图床）；
- 文件不存在 / 扩展名不支持时返回明确错误信封，且不发起任何模型调用。
"""
import base64
import json
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.context_manager import ContextManager  # noqa: E402
from mbdsdr_ai.config import AgentConfig  # noqa: E402


# ── 辅助：裸 Agent（绕过重型 __init__，与 test_agent_context 同款）────────
def _make_bare_agent(captured, config=None):
    from unittest.mock import MagicMock

    from mbdsdr_ai.agent import MBDSDRAgent

    def fake_stream(messages, tools=None, tool_choice=None):
        captured.append(messages)
        yield {
            "done": True, "success": True, "content": "收到。",
            "tool_calls": [], "usage": {}, "model": "test-model",
            "latency_ms": 0,
        }

    agent = object.__new__(MBDSDRAgent)
    agent.config = config or AgentConfig(api_key="sk-test", model="test-model")
    agent.context_manager = ContextManager(
        max_context_tokens=4096,
        compaction_threshold=0.99,
        compaction_target_ratio=0.5,
        system_prompt="你是测试助手。",
    )
    mm = MagicMock()
    mm.chat_stream = fake_stream
    mm.model = "test-model"
    mm.parse_tool_calls_from_text = MagicMock(return_value=[])
    agent.model_manager = mm
    tr = MagicMock()
    tr.get_tool_names.return_value = ["list_tools"]
    tr.get_tool_definitions.return_value = []
    agent.tool_registry = tr
    memory = MagicMock()
    memory.build_memory_context.return_value = ""
    agent.memory = memory
    agent.total_agent_calls = 0
    agent.last_error = None
    agent._repeat_key = None
    agent._repeat_count = 0
    agent._mcp_client = None
    return agent


def _user_message(messages):
    users = [m for m in messages if m.get("role") == "user"]
    assert users, "发出的 messages 中应至少有一条 user 消息"
    return users[0]


def _png_bytes():
    # 一个最小但可解码的 1x1 PNG（89 50 4E 47 ...）；测试只关心字节原样搬运。
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
        "z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")


# ── T1: 无 images 时 content 仍是字符串（向后兼容）────────────────────
def test_chat_without_images_keeps_string_content():
    captured = []
    agent = _make_bare_agent(captured)

    result = agent.chat("看看这张图的频谱")

    assert result["content"] == "收到。"
    assert not result["error"]
    content = _user_message(captured[0])["content"]
    assert isinstance(content, str)
    assert content == "看看这张图的频谱"


def test_chat_empty_images_list_keeps_string_content():
    """images=[] 与不传等价，不能因为空列表就改变消息形状。"""
    captured = []
    agent = _make_bare_agent(captured)

    agent.chat("没有图片", images=[])

    content = _user_message(captured[0])["content"]
    assert isinstance(content, str)
    assert content == "没有图片"


# ── T2: 本地图片转 base64 data URL，content 是 parts 列表 ─────────────
def test_chat_with_local_png_builds_multimodal_parts(tmp_path):
    captured = []
    agent = _make_bare_agent(captured)
    raw = _png_bytes()
    img = tmp_path / "spectrum.png"
    img.write_bytes(raw)

    result = agent.chat("这张瀑布图里有什么信号？", images=[str(img)])

    assert not result["error"], result["error"]
    content = _user_message(captured[0])["content"]
    assert isinstance(content, list)

    text_parts = [p for p in content if p.get("type") == "text"]
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(text_parts) == 1
    assert text_parts[0]["text"] == "这张瀑布图里有什么信号？"
    assert len(image_parts) == 1

    url = image_parts[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == raw


def test_chat_accepts_single_path_not_wrapped_in_list(tmp_path):
    captured = []
    agent = _make_bare_agent(captured)
    img = tmp_path / "one.jpg"
    img.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")

    agent.chat("看图", images=str(img))

    content = _user_message(captured[0])["content"]
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_chat_with_two_images_emits_two_image_parts(tmp_path):
    captured = []
    agent = _make_bare_agent(captured)
    a = tmp_path / "a.png"
    b = tmp_path / "b.webp"
    a.write_bytes(b"png-bytes")
    b.write_bytes(b"webp-bytes")

    agent.chat("对比两张图", images=[str(a), str(b)])

    content = _user_message(captured[0])["content"]
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert len(image_parts) == 2
    assert image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert image_parts[1]["image_url"]["url"].startswith("data:image/webp;base64,")


def test_chat_passes_through_data_url():
    captured = []
    agent = _make_bare_agent(captured)
    data_url = "data:image/jpeg;base64,AAECAwQ="

    agent.chat("这张图呢", images=data_url)

    content = _user_message(captured[0])["content"]
    image_parts = [p for p in content if p.get("type") == "image_url"]
    assert image_parts[0]["image_url"]["url"] == data_url


# ── T3: 坏输入明确报错，不静默、不发起模型调用 ────────────────────────
def test_chat_missing_image_returns_error_envelope(tmp_path):
    captured = []
    agent = _make_bare_agent(captured)
    missing = tmp_path / "nope.png"

    result = agent.chat("看图", images=[str(missing)])

    assert result["error"] == "image_not_found"
    assert result["error_code"] == "image_not_found"
    assert "不存在" in result["content"]
    assert result["content"].startswith("错误: ")
    # 不得悄悄降级成纯文本发给模型，也不得污染上下文
    assert captured == [], "坏图片不应触发任何模型调用"
    assert agent.context_manager.history == []


def test_chat_unsupported_extension_returns_error_envelope(tmp_path):
    captured = []
    agent = _make_bare_agent(captured)
    bad = tmp_path / "waveform.bmp"
    bad.write_bytes(b"BM-fake")

    result = agent.chat("看图", images=[str(bad)])

    assert result["error"] == "image_type_unsupported"
    assert result["error_code"] == "image_type_unsupported"
    assert "不支持" in result["content"]
    assert captured == [], "坏图片不应触发任何模型调用"
    assert agent.context_manager.history == []


def test_chat_invalid_data_url_returns_error_envelope():
    captured = []
    agent = _make_bare_agent(captured)

    result = agent.chat("看图", images=["data:text/plain;base64,AAAA"])

    assert result["error"] == "image_invalid"
    assert "data URL" in result["content"]
    assert captured == [], "坏 data URL 不应触发任何模型调用"


def test_image_helper_rejects_unsupported_when_called_directly(tmp_path):
    """模块级 helper 与 chat 同一套判定（供其它入口复用时不走另一条口径）。"""
    from mbdsdr_ai.agent import ImageInputError, image_to_data_url

    bad = tmp_path / "x.tiff"
    bad.write_bytes(b"II*\x00")
    with pytest.raises(ImageInputError) as ei:
        image_to_data_url(str(bad))
    assert ei.value.code == "image_type_unsupported"


# ── T4: 多模态消息在上下文中可被序列化/导出（不因 list content 炸）────
def test_multimodal_user_message_survives_export_and_token_estimate(tmp_path):
    captured = []
    agent = _make_bare_agent(captured)
    img = tmp_path / "shot.png"
    img.write_bytes(b"png")

    agent.chat("描述图片", images=[str(img)])

    exported = agent.context_manager.export_messages()
    user_msgs = [m for m in exported if m.get("role") == "user"]
    assert user_msgs and isinstance(user_msgs[0]["content"], list)
    # JSON 可序列化（会话持久化路径）
    json.dumps(exported, ensure_ascii=False)
    # token 估算不因 parts 结构抛异常且能计入文本
    assert agent.context_manager.get_total_tokens() > 0
