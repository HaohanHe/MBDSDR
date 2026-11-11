# SPDX-License-Identifier: MIT
"""``config.json`` 单一写入口（ConfigStore.update）的契约。

历史事故：``~/.mbdsdr/config.json`` 有四个写者（AI 面板、设置面板、AI 内核
save_config、站址镜像），每个都拿自己构造期的快照整份写回。任何两个写者
交错就会互相吃掉对方刚写的键——最典型的是设置面板改一个采样参数，把用户
刚填的 ``api_key`` 冲回空字符串。

这里钉住四条：
  1. 局部更新只动自己提交的键，其它键原样保留；
  2. 两个写者写不同键，最终两者都在（不靠运气）；
  3. 写盘失败时原文件字节不变、不留临时文件；
  4. 损坏文件先留 ``.corrupt-*`` 证据再覆盖。
"""
import json
import os

import pytest

from mbdsdr_ai import config as ai_config


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. 合并语义
# ---------------------------------------------------------------------------
def test_update_keeps_keys_it_did_not_touch(tmp_path):
    path = str(tmp_path / "config.json")
    _write(path, {"api_key": "sk-live", "base_url": "https://x/v1",
                  "temperature": 0.7})

    ok = ai_config.ConfigStore(path).update(
        lambda d: d.update({"temperature": 0.3}))
    assert ok is True
    disk = _read(path)
    assert disk["temperature"] == 0.3
    assert disk["api_key"] == "sk-live"      # 不许被顺带写没
    assert disk["base_url"] == "https://x/v1"


def test_two_writers_different_keys_both_survive(tmp_path):
    """写者 A 反复写 api_key，写者 B 反复写 temperature，交错进行。

    旧实现（各自 read-modify-write 无锁）在这里必红；ConfigStore 的锁内重读
    让两者都留下。
    """
    path = str(tmp_path / "config.json")
    _write(path, {})
    store = ai_config.ConfigStore(path)

    for i in range(20):
        assert store.update(lambda d: d.update({"api_key": f"sk-{i}"})) is True
        assert store.update(lambda d: d.update({"temperature": i / 100})) is True

    disk = _read(path)
    assert disk["api_key"] == "sk-19"
    assert disk["temperature"] == pytest.approx(0.19)


def test_mutator_returning_false_aborts_without_writing(tmp_path):
    path = str(tmp_path / "config.json")
    _write(path, {"api_key": "sk-keep"})
    before = open(path, "rb").read()

    ok = ai_config.ConfigStore(path).update(lambda d: False)
    assert ok is False
    assert open(path, "rb").read() == before


def test_mutator_exception_leaves_file_untouched(tmp_path):
    path = str(tmp_path / "config.json")
    _write(path, {"api_key": "sk-keep"})
    before = open(path, "rb").read()

    def _boom(d):
        d["api_key"] = ""        # 先改内存，再抛——磁盘必须还是原样
        raise RuntimeError("boom")

    assert ai_config.ConfigStore(path).update(_boom) is False
    assert open(path, "rb").read() == before
    assert "boom" in (ai_config.last_error() or "")


# ---------------------------------------------------------------------------
# 2. 写盘失败：原文件字节不变、不留 .tmp
# ---------------------------------------------------------------------------
def test_atomic_failure_keeps_original_bytes_and_no_tmp(tmp_path, monkeypatch):
    path = str(tmp_path / "config.json")
    _write(path, {"api_key": "sk-keep"})
    before = open(path, "rb").read()

    monkeypatch.setattr(ai_config, "_atomic_write_json", lambda p, d: False)

    assert ai_config.ConfigStore(path).update(
        lambda d: d.update({"api_key": "sk-lost"})) is False
    assert open(path, "rb").read() == before
    leftovers = [n for n in os.listdir(str(tmp_path)) if n.endswith(".tmp")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# 3. 损坏文件：先备份证据再覆盖
# ---------------------------------------------------------------------------
def test_corrupt_file_is_backed_up_before_overwrite(tmp_path):
    path = str(tmp_path / "config.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{ not json")

    assert ai_config.ConfigStore(path).update(
        lambda d: d.update({"temperature": 0.4})) is True
    assert _read(path)["temperature"] == 0.4
    backups = [n for n in os.listdir(str(tmp_path)) if ".corrupt-" in n]
    assert backups, "覆盖损坏配置前必须留证据"


# ---------------------------------------------------------------------------
# 4. 三个调用方都只提交自己拥有的键
# ---------------------------------------------------------------------------
def test_ai_panel_save_keeps_settings_keys(tmp_path, monkeypatch):
    import ai_panel as ap

    path = str(tmp_path / "config.json")
    monkeypatch.setattr(ap.AIPanel, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(ap.AIPanel, "CONFIG_FILE", path)
    _write(path, {"api_key": "sk-old", "temperature": 0.25,
                  "ground_station_lat": 31.2})

    assert ap.AIPanel._save_config(
        {"api_key": "sk-new", "base_url": "https://y/v1", "model": "m"}) is True

    disk = _read(path)
    assert disk["api_key"] == "sk-new"
    assert disk["temperature"] == 0.25           # 设置面板的键
    assert disk["ground_station_lat"] == 31.2    # 站址镜像的键


def test_settings_panel_ai_write_keeps_api_key(tmp_path, monkeypatch):
    import settings_panel as sp

    path = str(tmp_path / "config.json")
    monkeypatch.setattr(sp, "AI_CONFIG_PATH", path)
    _write(path, {"api_key": "sk-live", "base_url": "https://x/v1"})

    class _Stub:
        _ai = {}

    err = sp.SettingsPanel._write_ai_values(_Stub(), {"temperature": 0.5})
    assert err == ""
    disk = _read(path)
    assert disk["temperature"] == 0.5
    assert disk["api_key"] == "sk-live"
    assert disk["base_url"] == "https://x/v1"


def test_station_mirror_keeps_api_key(tmp_path):
    import station_sync as ss

    path = str(tmp_path / "config.json")
    _write(path, {"api_key": "sk-live", "temperature": 0.6})

    assert ss.write_config_site(31.2, 121.5, path=path) is True
    disk = _read(path)
    assert disk["ground_station_lat"] == 31.2
    assert disk["api_key"] == "sk-live"
    assert disk["temperature"] == 0.6
