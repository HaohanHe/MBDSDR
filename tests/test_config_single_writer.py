# SPDX-License-Identifier: MIT
"""P1-47 两个写者各持一键：并发写后谁的键都不许丢、api_key 不许被旧快照回滚。

这里的「两个写者」是真实调用路径，不是直接调 ``ConfigStore.update``：

- 写者 A：``mbdsdr_ai.config.save_config()``（AI 内核，持 api_key/base_url/model）；
- 写者 B：``desktop.ai_panel`` 面板的 ``_save_config()``（同样持这 3 个键，
  且文件里还有设置面板的采样/观测站等键）。

事故形态（P1-47）：A 先 ``load_config()`` 拿到快照，期间 B 写入新 api_key，
A 再 ``save_config(快照)`` —— 若整份快照覆盖，B 刚写的 api_key 被回滚成空串，
用户重启后才发现「AI 未配置」。

本文件只测「键存不存在、值对不对」，不测实现细节；实现换掉但行为对，应当继续绿。
"""
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import config as ai_config  # noqa: E402


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def cfg_path(tmp_path):
    return str(tmp_path / "config.json")


@pytest.fixture()
def panel(monkeypatch, cfg_path):
    """把 AI 面板的写入路径指向临时 config.json（不碰用户真实配置）。"""
    ai_panel = pytest.importorskip("ai_panel")
    monkeypatch.setattr(ai_panel.AIPanel, "CONFIG_FILE", cfg_path, raising=False)
    monkeypatch.setattr(ai_panel.AIPanel, "CONFIG_DIR",
                        os.path.dirname(cfg_path), raising=False)
    return ai_panel.AIPanel


def test_stale_agent_snapshot_does_not_roll_back_panel_api_key(
        cfg_path, panel):
    """A 拿旧快照后 B 写 api_key，A 再保存：api_key 必须还是 B 写的那个。"""
    snapshot = ai_config.load_config(cfg_path)          # 此时文件还不存在
    assert not snapshot.api_key

    assert panel._save_config({"api_key": "sk-from-panel",
                               "base_url": "https://panel.example/v1",
                               "model": "panel-model"}) is True
    assert _read(cfg_path)["api_key"] == "sk-from-panel"

    snapshot.base_url = "https://agent.example/v1"      # A 只改自己关心的字段
    assert ai_config.save_config(snapshot, cfg_path) is True

    disk = _read(cfg_path)
    assert disk.get("api_key") == "sk-from-panel", (
        "旧快照把面板刚写的 api_key 回滚了：" + repr(disk.get("api_key")))
    assert disk.get("base_url") == "https://agent.example/v1"
    assert disk.get("model") == "panel-model"


def test_panel_write_does_not_lose_agent_keys(cfg_path, panel):
    """反向：A 先写 3 个键，B 再写，A 的 base_url/api_key 不能被 B 顶掉。"""
    cfg = ai_config.load_config(cfg_path)
    cfg.api_key = "sk-from-agent"
    cfg.base_url = "https://agent.example/v1"
    cfg.model = "agent-model"
    assert ai_config.save_config(cfg, cfg_path) is True

    assert panel._save_config({"model": "panel-model"}) is True

    disk = _read(cfg_path)
    assert disk.get("api_key") == "sk-from-agent"
    assert disk.get("base_url") == "https://agent.example/v1"
    assert disk.get("model") == "panel-model"


def test_non_owned_keys_survive_both_writers(cfg_path, panel):
    """文件里别的模块写的键（采样率/观测站）两个写者都不许吃掉。"""
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"sample_rate_hz": 2400000,
                   "station": {"lat": 43.8, "lon": 125.3}}, f)

    cfg = ai_config.load_config(cfg_path)
    cfg.model = "agent-model"
    assert ai_config.save_config(cfg, cfg_path) is True
    assert panel._save_config({"api_key": "sk-from-panel"}) is True

    disk = _read(cfg_path)
    assert disk.get("sample_rate_hz") == 2400000
    assert disk.get("station") == {"lat": 43.8, "lon": 125.3}
    assert disk.get("model") == "agent-model"
    assert disk.get("api_key") == "sk-from-panel"


def test_round_robin_writes_keep_every_key(cfg_path, panel):
    """三轮交替写：每轮结束两个写者的键都在（防只修了第一轮）。"""
    cfg = ai_config.load_config(cfg_path)
    for i in range(3):
        cfg.model = f"agent-{i}"
        assert ai_config.save_config(cfg, cfg_path) is True
        assert panel._save_config({"api_key": f"sk-{i}"}) is True
        disk = _read(cfg_path)
        assert disk.get("api_key") == f"sk-{i}", f"第 {i} 轮 api_key 被回滚"
        assert disk.get("model") == f"agent-{i}", f"第 {i} 轮 model 被顶掉"
