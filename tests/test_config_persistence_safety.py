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


# ----------------------------------------------------------------------
# A04：损坏且备份失败时，写回必须被拒绝，而且用户要看得见
# ----------------------------------------------------------------------

class TestBackupFailureBlocksWrite:
    def test_update_refuses_and_surfaces_reason(self, tmp_path, monkeypatch):
        """反证：备份失败 → update 返回 False、文件逐字节不变、last_error 可读。"""
        path = tmp_path / "config.json"
        bad = b'{"api_key": "sk-live", '
        path.write_bytes(bad)
        monkeypatch.setattr(cfg_mod, "backup_corrupt", lambda *a, **k: None)

        ok = cfg_mod.ConfigStore(str(path)).update(
            lambda d: d.update({"model": "gpt-x"}))

        assert ok is False, "备份失败时不得写回"
        assert path.read_bytes() == bad, "唯一原件一个字节都不能动"
        msg = last_error() or ""
        assert "备份失败" in msg
        assert "拒绝保存" in msg
        assert str(path) in msg
        assert "手动" in msg, "必须给出可执行的修复建议，而不是静默失败"

    def test_save_config_returns_false_and_keeps_original(self, tmp_path,
                                                          monkeypatch):
        """用户改设置走 save_config：明确返回失败，供设置面板显示。"""
        path = tmp_path / "config.json"
        bad = b'{"api_key": "sk-live", '
        path.write_bytes(bad)
        monkeypatch.setattr(cfg_mod, "backup_corrupt", lambda *a, **k: None)

        assert save_config(AgentConfig(api_key="sk-new"), str(path)) is False
        assert path.read_bytes() == bad
        assert "拒绝保存" in (last_error() or "")

    def test_read_still_degrades_without_freezing_program(self, tmp_path,
                                                          monkeypatch):
        """高频读不能被坏文件卡死：read() 仍返回空配置，只记原因。"""
        path = tmp_path / "config.json"
        path.write_bytes(b'{ broken')
        monkeypatch.setattr(cfg_mod, "backup_corrupt", lambda *a, **k: None)

        assert cfg_mod.ConfigStore(str(path)).read() == {}
        assert "备份失败" in (last_error() or "")

    def test_block_clears_after_user_fixes_file(self, tmp_path, monkeypatch):
        """用户手动删/修坏文件后，写回恢复正常，不会永久只读。"""
        path = tmp_path / "config.json"
        path.write_bytes(b'{ broken')
        monkeypatch.setattr(cfg_mod, "backup_corrupt", lambda *a, **k: None)
        store = cfg_mod.ConfigStore(str(path))
        assert store.update(lambda d: d.update({"model": "blocked"})) is False

        path.unlink()                     # 用户手动移除坏文件
        assert store.update(lambda d: d.update({"model": "recovered"})) is True
        assert json.loads(path.read_text(encoding="utf-8"))["model"] == "recovered"


def _seed(path, **extra):
    data = {"api_key": "original-secret", "base_url": "https://file/v1",
            "model": "file-model", "custom_keep": 42}
    data.update(extra)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def _corrupt_backups(directory, name):
    """只数备份本体：``*.reason.txt`` 是成对的理由文件，不是第二份备份。"""
    return [p for p in directory.glob(f"{name}.corrupt-*")
            if not p.name.endswith(".reason.txt")]


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
        backups = _corrupt_backups(tmp_path, "config.json")
        assert len(backups) == 1 and backups[0].read_bytes() == bad
        reasons = list(tmp_path.glob("config.json.corrupt-*.reason.txt"))
        assert len(reasons) == 1, "备份必须留下可查的理由"
        msg = last_error() or ""
        assert "损坏" in msg and "备份" in msg and "默认配置启动" in msg
        # 之后加载继续可用：保存一份干净配置后能正常读回。
        assert save_config(AgentConfig(api_key="fresh"), str(path)) is True
        assert load_config(str(path)).api_key == "fresh"

    def test_save_after_corrupt_keeps_backup_evidence(self, tmp_path):
        path = tmp_path / "config.json"
        bad = b'{not json'
        path.write_bytes(bad)
        load_config(str(path))
        assert save_config(AgentConfig(api_key="fresh"), str(path)) is True
        backups = _corrupt_backups(tmp_path, "config.json")
        assert len(backups) == 1, "同一份损坏内容只需一份证据，保存不额外复制"
        assert backups[0].read_bytes() == bad
        assert json.loads(path.read_text(encoding="utf-8"))["api_key"] == "fresh"
        assert last_error() is None

    def test_corrupt_load_writes_reason_for_startup_report(self, tmp_path):
        """备份必须带理由文件：启动清点只认 ``*.reason.txt``，否则用户看不到。"""
        path = tmp_path / "config.json"
        bad = b'{"api_key": "sk-live", "base_url": '
        path.write_bytes(bad)

        load_config(str(path))

        reason_files = list(tmp_path.glob("config.json.corrupt-*.reason.txt"))
        assert len(reason_files) == 1, "备份成功却没写理由，等于备份没人知道"
        lines = reason_files[0].read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1, "理由文件必须是单行"
        assert "损坏" in lines[0] and str(path) in lines[0], lines

        import mbdsdr_ai.corrupt_evidence as ce
        report = ce.scan_corruption_evidence([str(tmp_path)])
        assert report["total"] == 1, "启动清点没看到 config.json 的损坏备份"
        assert str(reason_files[0]) in report["recent"]

    def test_repeated_corrupt_load_does_not_pile_up_copies(self, tmp_path):
        """tx_guard 每次取审计路径都会 load_config；持续损坏不许堆副本。"""
        path = tmp_path / "config.json"
        path.write_bytes(b'{not json')

        for _ in range(5):
            load_config(str(path))

        backups = _corrupt_backups(tmp_path, "config.json")
        assert len(backups) == 1, f"同一份损坏内容留了 {len(backups)} 份副本"
        assert "损坏" in (last_error() or "")

    def test_clean_config_clears_error_but_keeps_evidence(self, tmp_path):
        """损坏恢复后 last_error 必须归零；备份与理由仍留在原处供查证。"""
        path = tmp_path / "config.json"
        path.write_bytes(b'{not json')
        load_config(str(path))
        assert last_error()

        assert save_config(AgentConfig(api_key="fresh"), str(path)) is True
        assert last_error() is None
        assert _corrupt_backups(tmp_path, "config.json"), "证据不许被清理"
        assert list(tmp_path.glob("config.json.corrupt-*.reason.txt"))

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
        # 旧键是否已回写，按解析后的键集合判断；不在断言里直接拿字面量
        # 去搜 read_text 的返回值（那是源码文本 pin，注释里写一遍就会误判）。
        legacy_keys = {"api_base"}
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        assert not (set(on_disk) & legacy_keys) and on_disk["custom_keep"] == 42
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
