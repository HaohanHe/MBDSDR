#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""AI 内核默认模型定向测试：默认必须是 8B 级 Qwen3-VL，不得回落到 35B。

纯本地静态检查，不联网、不读 API Key。
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from mbdsdr_ai.config import DEFAULT_CONFIG, AgentConfig
from mbdsdr_ai.context_manager import get_model_context_window
from mbdsdr_ai.model_manager import BUILTIN_MODELS, ModelManager

EXPECTED_MODEL = "Qwen/Qwen3-VL-8B-Thinking"


def test_default_config_model_is_8b_vl(monkeypatch):
    monkeypatch.delenv("MBDSDR_MODEL", raising=False)
    assert DEFAULT_CONFIG["model"] == EXPECTED_MODEL
    assert AgentConfig().model == EXPECTED_MODEL
    assert ModelManager().model == EXPECTED_MODEL


def test_default_model_does_not_contain_35b():
    assert "35B" not in DEFAULT_CONFIG["model"]
    assert "35B" not in AgentConfig.__dataclass_fields__["model"].default


def test_context_window_pattern_for_default_model():
    # 现有 "Qwen3" 模式匹配命中 32768
    assert get_model_context_window(EXPECTED_MODEL) == 32768


def test_builtin_models_include_default_model():
    entry = next((m for m in BUILTIN_MODELS if m.id == EXPECTED_MODEL), None)
    assert entry is not None
    assert entry.owned_by == "Qwen"
    assert entry.context_window == 262144
