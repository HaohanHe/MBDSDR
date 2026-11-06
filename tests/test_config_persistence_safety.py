# SPDX-License-Identifier: MIT
"""配置持久化安全：原子写 / 损坏留证 / 合并保留 / env 优先级。

背景（2026-11-06 只读审计）：``mbdsdr_ai/config.py`` 的 ``save_config``
用 ``open(path, "w")`` 整份覆盖且无任何异常保护；``load_config`` 损坏时
只 ``print``（GUI 无控制台）就回默认，下一次保存用默认值碾掉含 API key 的
原文件。同时 ``__post_init__`` 无条件用 env 覆盖 base_url/model，导致配置
对话框显示的值与实际请求地址分叉。本文件把四条不变式钉死。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import pytest  # noqa: E402

from mbdsdr_ai import config as cfg_mod  # noqa: E402
from mbdsdr_ai.config import (  # noqa: E402
    AgentConfig, load_config, save_config, last_error,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("MBDSDR_API_KEY", "OPENAI_API_KEY", "MBDSDR_BASE_URL",
                "MBDSDR_API_BASE", "OPENAI_BASE_URL", "MBDSDR_MODEL",
                "MBDSDR_TX_AUDIT_PATH"):
        monkeypatch.delenv(key, raising=False)
    yield


def _seed(path, **extra):
    data = {"api_key": "original-secret", "base_url": "https://file/v1",
            "model": "file-model", "custom_keep": 42}
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


# ------------------------------------------------------------------ 原子写
class TestAtomicWrite:
    def test_replace_failure_keeps_original_bytes(self, tmp_path, monkeypatch):
        """os.replace 失败（磁盘满/权限）时原文件一个字节都不能变。"""
        path = tmp_path / "config.json"
        _seed(path)
        before = path.read_bytes()

        def _boom(*_a, **_k):
            raise OSError("no space left on device")

        monkeypatch.setattr(cfg_mod.os, "replace", _boom)
        ok = save_config(AgentConfig(api_key="new-key"), str(path))
        monkeypatch.undo()

        assert ok is False
        assert path.read_bytes() == before, "写失败不许截断原配置"
        assert "配置写入失败" in (last_error() or "")
        assert json.loads(before)["api_key"] == "original-secret"

    def test_serialization_failure_leaves_no_temp_and_no_change(self, tmp_path,
                                                               monkeypatch):
        """json.dump 中途抛错：无 .tmp 残留，原文件完好，错误可见。"""
        path = tmp_path / "config.json"
        _seed(path)
        before = path.read_bytes()
        original_dump = cfg_mod.json.dump

        def _boom(data, fp, *a, **k):
            fp.write('{"half": ')
            raise TypeError("unserializable value")

        monkeypatch.setattr(cfg_mod.json, "dump", _boom)
        try:
            ok = save_config(AgentConfig(api_key="new"), str(path))
        finally:
            monkeypatch.setattr(cfg_mod.json, "dump", original_dump)

        assert ok is False
        assert path.read_bytes() == before
        assert list(tmp_path.glob(".mbd-config-*.tmp")) == [], "临时文件必须清理"
        assert "配置写入失败" in (last_error() or "")

    def test_success_is_visible_and_no_temp_left(self, tmp_path):
        c = AgentConfig(api_key="k")
        assert save_config(c, str(tmp_path / "sub" / "config.json")) is True
        assert last_error() is None
        assert list(tmp_path.rglob("*.tmp")) == []
        assert c._config_path.endswith("config.json")


# ------------------------------------------------------- 合并保留 + 损坏留证
class TestMergeAndCorrupt:
    def test_save_keeps_unknown_keys(self, tmp_path):
        """与 ai_panel 共用同一文件：本模块只认 dataclass 字段，不能吃掉别的键。"""
        path = tmp_path / "config.json"
        _seed(path, layout_preset="wide", theme="dark")
        c = AgentConfig(api_key="changed", model="m2")
        assert save_config(c, str(path)) is True
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert on_disk["custom_keep"] == 42
        assert on_disk["theme"] == "dark"
        assert on_disk["layout_preset"] == "wide"
        assert on_disk["api_key"] == "changed", "本模块自有字段仍按调用方的值覆盖"

    def test_corrupt_load_backs_up_without_deleting_original(self, tmp_path):
        path = tmp_path / "config.json"
        bad = b'{"api_key": "sk-abcdef", "base_url": '   # 半截 JSON
        path.write_bytes(bad)
        cfg = load_config(str(path))

        assert cfg.api_key == ""
        assert cfg._config_path == str(path), "后续 save 必须知道往哪写"
        assert path.read_bytes() == bad, "读操作不许删用户文件"
        backups = list(tmp_path.glob("config.json.corrupt-*"))
        assert len(backups) == 1 and backups[0].read_bytes() == bad
        msg = last_error() or ""
        assert "损坏" in msg and "备份" in msg and "默认配置启动" in msg

    def test_save_after_corrupt_keeps_a_second_backup(self, tmp_path):
        path = tmp_path / "config.json"
        bad = b'{not json'
        path.write_bytes(bad)
        load_config(str(path))
        assert save_config(AgentConfig(api_key="fresh"), str(path)) is True
        assert len(list(tmp_path.glob("config.json.corrupt-*"))) >= 2
        assert json.loads(path.read_text(encoding="utf-8"))["api_key"] == "fresh"
        assert last_error() is None

    def test_non_object_json_is_corrupt_not_silent(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("[1, 2, 3]", encoding="utf-8")
        assert load_config(str(path)).api_key == ""
        assert "顶层不是 JSON 对象" in (last_error() or "")

    def test_migration_still_preserves_unknown_keys(self, tmp_path):
        """旧键归一回写走原子写后，``test_config_key_migration`` 的口径不变。"""
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"api_key": "k", "api_base": "https://x/v1",
                                    "custom_keep": 42}), encoding="utf-8")
        cfg = load_config(str(path))
        assert cfg.base_url == "https://x/v1"
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert "api_base" not in on_disk and on_disk["custom_keep"] == 42
        assert last_error() is None


# ------------------------------------------------------------- env 优先级
class TestEnvPrecedence:
    def test_file_base_url_beats_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MBDSDR_BASE_URL", "https://env/v1")
        monkeypatch.setenv("MBDSDR_MODEL", "env-model")
        path = tmp_path / "config.json"
        _seed(path)
        cfg = load_config(str(path))
        assert cfg.base_url == "https://file/v1"
        assert cfg.model == "file-model"

    def test_env_still_fills_keys_absent_from_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MBDSDR_BASE_URL", "https://env/v1")
        monkeypatch.setenv("MBDSDR_MODEL", "env-model")
        monkeypatch.setenv("MBDSDR_API_KEY", "env-key")
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"custom_keep": 7}), encoding="utf-8")
        cfg = load_config(str(path))
        assert cfg.base_url == "https://env/v1"
        assert cfg.model == "env-model"
        assert cfg.api_key == "env-key"

    def test_explicit_constructor_beats_env(self, monkeypatch):
        """AI 面板显式传用户填的值；旧式无条件覆盖会让「已配置并保存」说谎。"""
        monkeypatch.setenv("MBDSDR_BASE_URL", "https://env/v1")
        monkeypatch.setenv("MBDSDR_MODEL", "env-model")
        cfg = AgentConfig(api_key="sk-user", base_url="https://user/v1",
                          model="user-model")
        assert cfg.base_url == "https://user/v1"
        assert cfg.model == "user-model"

    def test_env_fills_only_default_constructor(self, monkeypatch):
        monkeypatch.setenv("MBDSDR_BASE_URL", "https://env/v1")
        monkeypatch.setenv("MBDSDR_MODEL", "env-model")
        cfg = AgentConfig()
        assert cfg.base_url == "https://env/v1"
        assert cfg.model == "env-model"
