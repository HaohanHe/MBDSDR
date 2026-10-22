# SPDX-License-Identifier: MIT
"""mbdsdr_ai.config 配置键收敛测试（api_base → base_url 一次性迁移）。

背景：桌面端 ai_panel 曾写 "api_base"，而 main_window / DEFAULT_CONFIG 读
"base_url"——统一为 base_url，读到旧键时一次性兼容回退并迁移（读后回写）。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import json

from mbdsdr_ai.config import (
    AgentConfig, load_config, normalize_config_keys,
)


def _clear_env(monkeypatch):
    for key in ("MBDSDR_API_KEY", "OPENAI_API_KEY", "MBDSDR_BASE_URL",
                "MBDSDR_API_BASE", "OPENAI_BASE_URL", "MBDSDR_MODEL"):
        monkeypatch.delenv(key, raising=False)


class TestNormalizeConfigKeys:
    def test_legacy_key_migrates(self):
        out = normalize_config_keys({"api_key": "k", "api_base": "https://x/v1"})
        assert out == {"api_key": "k", "base_url": "https://x/v1"}

    def test_existing_new_key_wins(self):
        out = normalize_config_keys({"base_url": "https://new/v1",
                                     "api_base": "https://old/v1"})
        assert out == {"base_url": "https://new/v1"}

    def test_idempotent(self):
        once = normalize_config_keys({"api_base": "https://x/v1"})
        assert normalize_config_keys(once) == once

    def test_non_dict_returns_empty(self):
        assert normalize_config_keys(None) == {}
        assert normalize_config_keys(["x"]) == {}


class TestAgentConfigFromDict:
    def test_from_dict_accepts_legacy_key(self, monkeypatch):
        _clear_env(monkeypatch)
        cfg = AgentConfig.from_dict({"api_key": "k", "api_base": "https://x/v1"})
        assert cfg.base_url == "https://x/v1"

    def test_from_dict_new_key_wins(self, monkeypatch):
        _clear_env(monkeypatch)
        cfg = AgentConfig.from_dict({"base_url": "https://new/v1",
                                     "api_base": "https://old/v1"})
        assert cfg.base_url == "https://new/v1"


class TestLoadConfigMigration:
    def test_read_migrates_file_once(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch)
        path = tmp_path / "config.json"
        path.write_text(json.dumps({
            "api_key": "k", "api_base": "https://x/v1",
            "model": "m", "custom_keep": 42,
        }), encoding="utf-8")

        cfg = load_config(str(path))
        assert cfg.base_url == "https://x/v1"
        assert cfg.api_key == "k"

        # 读后迁移回写：键名归一，其余字段原样保留
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert "api_base" not in on_disk
        assert on_disk["base_url"] == "https://x/v1"
        assert on_disk["custom_keep"] == 42

        # 幂等：再加载一次不再改写
        mtime = os.path.getmtime(path)
        cfg2 = load_config(str(path))
        assert cfg2.base_url == "https://x/v1"
        assert os.path.getmtime(path) == mtime

    def test_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        _clear_env(monkeypatch)
        cfg = load_config(str(tmp_path / "nope.json"))
        assert cfg.api_key == ""
