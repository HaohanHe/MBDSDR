# SPDX-License-Identifier: MIT
"""Tripo 文生 3D 模块的离线测试。

覆盖：无 Key/离线时零 transport 调用、成功下载、失败/封禁不落文件、
轮询超时、响应缺字段/非 JSON、文件名安全、API Key 不出现在结果与 CLI 输出。
测试全程不联网；轮询间隔为 0，不真正等待。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (_ROOT, _ROOT / "tests"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from mbdsdr_ai import net_policy, tripo_assets  # noqa: E402
from test_offline_privacy import _NetworkGuard  # noqa: E402

FAKE_KEY = "TRIPO_FAKE_KEY_9f4d2c_SHOULD_NOT_LEAK"
TASK_ID = "task_abc123"
MODEL_URL = "https://cdn.example.test/models/task_abc123.glb"
GLB_BYTES = b"glTF\x02\x00\x00\x00fake-glb-payload"


class FakeTransport:
    """完全内存的 transport：记录调用、按脚本返回任务状态。"""

    def __init__(self, *, submit_response=None, submit_error=None,
                 poll_responses=None, poll_error=None,
                 model_url=MODEL_URL, payload=GLB_BYTES,
                 download_error=None, task_id=TASK_ID):
        self.submit_response = ({"code": 0, "data": {"task_id": task_id}}
                                if submit_response is None else submit_response)
        self.submit_error = submit_error
        self.poll_responses = list(poll_responses or
                                   ["running", "running", "success"])
        self.poll_error = poll_error
        self.model_url = model_url
        self.payload = payload
        self.download_error = download_error
        self.calls = []
        self.poll_index = 0

    def post_json(self, url, headers, payload):
        self.calls.append(("post", url, dict(headers), dict(payload)))
        if self.submit_error is not None:
            raise self.submit_error
        return self.submit_response

    def get_json(self, url, headers):
        self.calls.append(("get", url))
        if self.poll_error is not None:
            raise self.poll_error
        index = min(self.poll_index, len(self.poll_responses) - 1)
        item = self.poll_responses[index]
        self.poll_index += 1
        if isinstance(item, (dict, list, tuple)):
            return item
        if item == "success":
            return {"code": 0, "data": {
                "status": "success", "progress": 100,
                "output": {"model_url": self.model_url}}}
        return {"code": 0, "data": {"status": item, "progress": 50}}

    def download(self, url, dest):
        self.calls.append(("download", url))
        if self.download_error is not None:
            raise self.download_error
        Path(dest).write_bytes(self.payload)


def _clear_network_env(monkeypatch):
    monkeypatch.delenv(net_policy.OFFLINE_ENV, raising=False)
    monkeypatch.delenv(net_policy.ALLOW_NETWORK_ENV, raising=False)


def _online(monkeypatch, *, key=FAKE_KEY):
    _clear_network_env(monkeypatch)
    monkeypatch.setenv(tripo_assets.API_KEY_ENV, key)


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "tripo_asset_gen_cli_test",
        _ROOT / "scripts" / "tripo_asset_gen.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _call_count(transport, kind=None):
    if kind is None:
        return len(transport.calls)
    return sum(1 for call in transport.calls if call[0] == kind)


# ---------------------------------------------------------------- 安全前置门
def test_missing_api_key_makes_zero_transport_calls(monkeypatch, tmp_path):
    _clear_network_env(monkeypatch)
    monkeypatch.delenv(tripo_assets.API_KEY_ENV, raising=False)
    transport = FakeTransport()
    out = tmp_path / "asset.glb"

    with _NetworkGuard(monkeypatch) as guard:
        result = tripo_assets.generate_model(
            "红色卫星", out_path=out, transport=transport, allow_network=True)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_MISSING_API_KEY
    assert "TRIPO_API_KEY" in result["reason"]
    assert transport.calls == []
    assert guard.hits == []
    assert not out.exists()


@pytest.mark.parametrize("offline,allow_network", [
    (True, None),
    (True, True),
    (False, None),
])
def test_offline_makes_zero_transport_calls(monkeypatch, tmp_path,
                                            offline, allow_network):
    _clear_network_env(monkeypatch)
    if offline:
        monkeypatch.setenv(net_policy.OFFLINE_ENV, "1")
    monkeypatch.setenv(tripo_assets.API_KEY_ENV, FAKE_KEY)
    transport = FakeTransport()
    out = tmp_path / "asset.glb"

    with _NetworkGuard(monkeypatch) as guard:
        result = tripo_assets.generate_model(
            "红色卫星", out_path=out, transport=transport,
            allow_network=allow_network)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_OFFLINE
    assert "MBDSDR_ALLOW_NETWORK=1" in result["reason"]
    assert transport.calls == []
    assert guard.hits == []
    assert not out.exists()


# ------------------------------------------------------------------ 成功路径
def test_success_submit_poll_download(monkeypatch, tmp_path):
    _online(monkeypatch)
    transport = FakeTransport(
        poll_responses=["running", "running", "success"])
    out = tmp_path / "asset.glb"

    result = tripo_assets.generate_model(
        "红色的通信卫星", out_path=out, transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is True
    assert result["task_id"] == TASK_ID
    assert result["model"] == tripo_assets.DEFAULT_MODEL
    assert result["prompt"] == "红色的通信卫星"
    assert Path(result["path"]) == out
    assert out.read_bytes() == GLB_BYTES
    assert [call[0] for call in transport.calls] == [
        "post", "get", "get", "get", "download"]
    assert transport.calls[0][2]["Authorization"] == f"Bearer {FAKE_KEY}"
    assert transport.calls[0][3] == {
        "prompt": "红色的通信卫星", "model": tripo_assets.DEFAULT_MODEL}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["asset.glb"]


def test_existing_output_is_not_overwritten(monkeypatch, tmp_path):
    _online(monkeypatch)
    existing = tmp_path / "same.glb"
    existing.write_bytes(b"OLD")
    transport = FakeTransport(poll_responses=["success"])

    result = tripo_assets.generate_model(
        "同名文件", out_path=existing, transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is True
    assert Path(result["path"]) != existing
    assert Path(result["path"]).name == "same-1.glb"
    assert existing.read_bytes() == b"OLD"
    assert Path(result["path"]).read_bytes() == GLB_BYTES


# ------------------------------------------------------------------ 失败语义
@pytest.mark.parametrize("status", ["failed", "banned"])
def test_failed_task_creates_no_output(monkeypatch, tmp_path, status):
    _online(monkeypatch)
    transport = FakeTransport(poll_responses=[status])
    out = tmp_path / "asset.glb"

    result = tripo_assets.generate_model(
        "会失败的任务", out_path=out, transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_TASK_FAILED
    assert status in result["reason"]
    assert not out.exists()
    assert _call_count(transport, "download") == 0
    assert list(tmp_path.iterdir()) == []


def test_poll_timeout_does_not_hang(monkeypatch, tmp_path):
    _online(monkeypatch)
    transport = FakeTransport(poll_responses=["running"])
    out = tmp_path / "asset.glb"

    result = tripo_assets.generate_model(
        "一直排队", out_path=out, transport=transport,
        allow_network=True, poll_interval_s=0.0, timeout_s=0.001)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_POLL_TIMEOUT
    assert "轮询超时" in result["reason"]
    assert _call_count(transport, "get") >= 1
    assert _call_count(transport, "download") == 0
    assert not out.exists()


def test_partial_download_leaves_no_half_file(monkeypatch, tmp_path):
    _online(monkeypatch)

    class PartialTransport(FakeTransport):
        def download(self, url, dest):
            Path(dest).write_bytes(b"half-a-glb")
            raise RuntimeError("下载中断")

    transport = PartialTransport(poll_responses=["success"])
    out = tmp_path / "asset.glb"

    result = tripo_assets.generate_model(
        "下载会中断", out_path=out, transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_DOWNLOAD_FAILED
    assert "下载中断" in result["reason"]
    assert not out.exists()
    assert list(tmp_path.iterdir()) == []


def test_missing_task_id_is_structured_failure(monkeypatch, tmp_path):
    _online(monkeypatch)
    transport = FakeTransport(submit_response={"code": 0, "data": {}})

    result = tripo_assets.generate_model(
        "缺 task_id", out_path=tmp_path / "asset.glb",
        transport=transport, allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_SUBMIT_INVALID_RESPONSE
    assert "task_id" in result["reason"]
    assert _call_count(transport, "get") == 0


def test_submit_non_json_is_structured_failure(monkeypatch, tmp_path):
    _online(monkeypatch)
    transport = FakeTransport(submit_response="<html>not json</html>")

    result = tripo_assets.generate_model(
        "非 JSON", out_path=tmp_path / "asset.glb",
        transport=transport, allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_SUBMIT_INVALID_RESPONSE
    assert "JSON" in result["reason"]


def test_missing_model_url_is_structured_failure(monkeypatch, tmp_path):
    _online(monkeypatch)
    transport = FakeTransport(poll_responses=[
        {"code": 0, "data": {"status": "success", "progress": 100}}])
    out = tmp_path / "asset.glb"

    result = tripo_assets.generate_model(
        "缺下载地址", out_path=out, transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_MISSING_MODEL_URL
    assert "model_url" in result["reason"]
    assert _call_count(transport, "download") == 0
    assert not out.exists()


def test_poll_non_json_is_structured_failure(monkeypatch, tmp_path):
    _online(monkeypatch)
    transport = FakeTransport(poll_responses=[["not", "object"]])

    result = tripo_assets.generate_model(
        "轮询非 JSON", out_path=tmp_path / "asset.glb",
        transport=transport, allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_POLL_INVALID_RESPONSE
    assert "JSON" in result["reason"]


# ------------------------------------------------------------------ 参数校验
@pytest.mark.parametrize("overrides,error_code", [
    ({"prompt": "   "}, tripo_assets.ERROR_INVALID_PROMPT),
    ({"out_path": ""}, tripo_assets.ERROR_INVALID_OUT_PATH),
    ({"timeout_s": 0}, tripo_assets.ERROR_INVALID_TIMEOUT),
    ({"poll_interval_s": -1}, tripo_assets.ERROR_INVALID_POLL_INTERVAL),
])
def test_invalid_parameters_win_over_key_and_network(
        monkeypatch, tmp_path, overrides, error_code):
    # 故意不给 Key、也不允许联网：参数校验必须先于两者返回。
    _clear_network_env(monkeypatch)
    monkeypatch.delenv(tripo_assets.API_KEY_ENV, raising=False)
    transport = FakeTransport()
    kwargs = {"prompt": "正常描述", "out_path": tmp_path / "asset.glb",
              "transport": transport, "allow_network": False}
    kwargs.update(overrides)

    result = tripo_assets.generate_model(**kwargs)

    assert result["ok"] is False
    assert result["error"] == error_code
    assert transport.calls == []


# ------------------------------------------------------------ 文件名安全工具
def test_slugify_prompt_removes_path_and_control_characters():
    slug = tripo_assets.slugify_prompt("../../红色 卫星\x00:测试/资产")

    assert "/" not in slug
    assert "\\" not in slug
    assert "\x00" not in slug
    assert ":" not in slug
    assert "红色" in slug
    assert slug not in ("", ".", "..")
    assert slug == slug.strip()


def test_slugify_prompt_empty_falls_back():
    assert tripo_assets.slugify_prompt("   \x00  ") == "tripo_model"
    assert tripo_assets.slugify_prompt(None) == "tripo_model"


def test_unique_output_path_adds_sequence(tmp_path):
    target = tmp_path / "model.glb"
    target.write_bytes(b"old")

    first = Path(tripo_assets.unique_output_path(target))
    assert first.name == "model-1.glb"
    first.write_bytes(b"new")

    second = Path(tripo_assets.unique_output_path(target))
    assert second.name == "model-2.glb"


# -------------------------------------------------------------- 密钥不泄漏
def test_api_key_not_in_failure_result(monkeypatch, tmp_path):
    _online(monkeypatch)
    transport = FakeTransport(submit_error=RuntimeError("上游连接中断"))

    result = tripo_assets.generate_model(
        "失败原因检查", out_path=tmp_path / "asset.glb",
        transport=transport, allow_network=True, poll_interval_s=0.0)

    dumped = json.dumps(result, ensure_ascii=False)
    assert result["ok"] is False
    assert FAKE_KEY not in result["reason"]
    assert FAKE_KEY not in dumped


def test_cli_offline_reason_does_not_leak_api_key(monkeypatch, tmp_path, capsys):
    _clear_network_env(monkeypatch)
    monkeypatch.setenv(tripo_assets.API_KEY_ENV, FAKE_KEY)
    monkeypatch.chdir(tmp_path)
    cli = _load_cli()

    rc = cli.main(["--prompt", "红色卫星", "--out", str(tmp_path / "a.glb")])

    captured = capsys.readouterr()
    assert rc == 3
    assert "MBDSDR_ALLOW_NETWORK=1" in captured.err
    assert FAKE_KEY not in captured.out
    assert FAKE_KEY not in captured.err


def test_cli_invalid_input_exits_2(monkeypatch, tmp_path, capsys):
    _clear_network_env(monkeypatch)
    cli = _load_cli()

    rc = cli.main(["--prompt", "   ", "--out", str(tmp_path / "a.glb")])

    captured = capsys.readouterr()
    assert rc == 2
    assert "输入非法" in captured.err


# ------------------------------------------------------------------ 批量清单
def _write_manifest(tmp_path, payload, name="manifest.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _items(*items):
    return {"version": 1, "items": list(items)}


class ScriptedTransport(FakeTransport):
    """按 prompt 决定提交是否失败：模拟「清单里混了一条坏苹果」。"""

    def __init__(self, failing_prompts=(), **kwargs):
        super().__init__(**kwargs)
        self.failing_prompts = set(failing_prompts)

    def post_json(self, url, headers, payload):
        if payload.get("prompt") in self.failing_prompts:
            self.calls.append(("post", url, dict(headers), dict(payload)))
            raise RuntimeError("上游拒绝了该条目")
        return super().post_json(url, headers, payload)


def test_manifest_parses_valid_entries_and_defaults_model(tmp_path):
    path = _write_manifest(tmp_path, {
        "version": 1,
        "_note": "未知顶层键应被忽略",
        "items": [
            {"name": "mars", "prompt": "a Mars globe",
             "model": "v3.1-20260211"},
            {"name": "moon_1", "prompt": "  the Moon  "},
        ]})

    entries, error = tripo_assets.load_manifest(path)

    assert error is None
    assert entries == [
        {"name": "mars", "prompt": "a Mars globe",
         "model": "v3.1-20260211"},
        {"name": "moon_1", "prompt": "the Moon",
         "model": tripo_assets.DEFAULT_MODEL},
    ]


@pytest.mark.parametrize("payload,needle", [
    ({"version": 2, "items": [{"name": "a", "prompt": "p"}]}, "version"),
    ({"version": 1, "items": []}, "items"),
    ({"version": 1}, "items"),
    ({"version": 1, "items": [
        {"name": "dup", "prompt": "p"},
        {"name": "dup", "prompt": "q"}]}, "重复"),
    ({"version": 1, "items": [{"name": "../evil", "prompt": "p"}]}, "name"),
    ({"version": 1, "items": [{"name": "a/b", "prompt": "p"}]}, "name"),
    ({"version": 1, "items": [{"name": "a", "prompt": "   "}]}, "prompt"),
    ({"version": 1, "items": [{"name": "", "prompt": "p"}]}, "name"),
])
def test_invalid_manifest_returns_no_entries_and_zero_transport_calls(
        monkeypatch, tmp_path, payload, needle):
    _online(monkeypatch)
    path = _write_manifest(tmp_path, payload)
    transport = FakeTransport()

    entries, error = tripo_assets.load_manifest(path)
    batch = tripo_assets.generate_batch(
        entries, out_dir=tmp_path / "out", transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert entries == []
    assert error["ok"] is False
    assert error["error"] == tripo_assets.ERROR_INVALID_MANIFEST
    assert needle in error["reason"]
    assert batch["ok"] is False
    assert batch["results"] == []
    assert transport.calls == []
    assert list(tmp_path.glob("*.glb")) == []
    assert not (tmp_path / "out").exists()


def test_generate_batch_any_invalid_entry_generates_nothing(
        monkeypatch, tmp_path):
    _online(monkeypatch)
    entries = [{"name": "ok", "prompt": "fine"},
               {"name": "../escape", "prompt": "bad name"}]
    transport = FakeTransport()

    result = tripo_assets.generate_batch(
        entries, out_dir=tmp_path / "out", transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["error"] == tripo_assets.ERROR_INVALID_MANIFEST
    assert result["results"] == []
    assert transport.calls == []
    assert not (tmp_path / "out").exists()


def test_generate_batch_one_success_one_failure(monkeypatch, tmp_path):
    _online(monkeypatch)
    entries = [{"name": "moon", "prompt": "the Moon"},
               {"name": "mars", "prompt": "a broken Mars"}]
    transport = ScriptedTransport(failing_prompts={"a broken Mars"})
    out_dir = tmp_path / "out"

    result = tripo_assets.generate_batch(
        entries, out_dir=out_dir, transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert result["skipped"] == 0
    assert [item["name"] for item in result["results"]] == ["moon", "mars"]
    assert (out_dir / "moon.glb").read_bytes() == GLB_BYTES
    assert not (out_dir / "mars.glb").exists()
    assert result["results"][0]["path"].endswith("moon.glb")
    assert result["results"][1]["error"] == tripo_assets.ERROR_SUBMIT_FAILED
    assert "上游拒绝" in result["results"][1]["reason"]


def test_generate_batch_continues_after_a_failed_item(monkeypatch, tmp_path):
    _online(monkeypatch)
    entries = [{"name": "first", "prompt": "ok one"},
               {"name": "second", "prompt": "bad one"},
               {"name": "third", "prompt": "ok three"}]
    transport = ScriptedTransport(failing_prompts={"bad one"})
    out_dir = tmp_path / "out"

    result = tripo_assets.generate_batch(
        entries, out_dir=out_dir, transport=transport,
        allow_network=True, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["succeeded"] == 2
    assert result["failed"] == 1
    assert [item["ok"] for item in result["results"]] == [True, False, True]
    assert (out_dir / "first.glb").exists()
    assert not (out_dir / "second.glb").exists()
    assert (out_dir / "third.glb").exists()


def test_generate_batch_max_models_truncates_honestly(monkeypatch, tmp_path):
    _online(monkeypatch)
    entries = [{"name": name, "prompt": f"model {name}"}
               for name in ("a", "b", "c")]
    transport = FakeTransport()
    out_dir = tmp_path / "out"

    result = tripo_assets.generate_batch(
        entries, out_dir=out_dir, transport=transport,
        allow_network=True, max_models=1, poll_interval_s=0.0)

    assert result["ok"] is False
    assert result["succeeded"] == 1
    assert result["failed"] == 0
    assert result["skipped"] == 2
    assert len(result["results"]) == 3
    assert result["results"][0]["ok"] is True
    for item in result["results"][1:]:
        assert item["ok"] is False
        assert item["error"] == tripo_assets.ERROR_SKIPPED
        assert "max_models=1" in item["reason"]
    assert sorted(path.name for path in out_dir.iterdir()) == ["a.glb"]
    assert _call_count(transport, "post") == 1


def test_generate_batch_without_key_fails_all_with_zero_network(
        monkeypatch, tmp_path):
    _clear_network_env(monkeypatch)
    monkeypatch.delenv(tripo_assets.API_KEY_ENV, raising=False)
    entries = [{"name": "a", "prompt": "one"},
               {"name": "b", "prompt": "two"}]
    transport = FakeTransport()
    out_dir = tmp_path / "out"

    with _NetworkGuard(monkeypatch) as guard:
        result = tripo_assets.generate_batch(
            entries, out_dir=out_dir, transport=transport,
            allow_network=True)

    assert result["ok"] is False
    assert result["succeeded"] == 0
    assert result["failed"] == 2
    assert result["skipped"] == 0
    assert all(item["error"] == tripo_assets.ERROR_MISSING_API_KEY
               for item in result["results"])
    assert transport.calls == []
    assert guard.hits == []
    assert list(tmp_path.rglob("*.glb")) == []
    assert not out_dir.exists()


# -------------------------------------------------------------- 批量 CLI 用法
@pytest.mark.parametrize("argv", [
    ["--manifest", "manifest.json"],
    ["--out-dir", "out"],
])
def test_cli_batch_requires_manifest_and_out_dir(monkeypatch, tmp_path, argv):
    _clear_network_env(monkeypatch)
    cli = _load_cli()

    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv)

    assert excinfo.value.code == 2


def test_cli_rejects_prompt_and_manifest_together(monkeypatch, tmp_path):
    _clear_network_env(monkeypatch)
    cli = _load_cli()
    manifest = _write_manifest(
        tmp_path, _items({"name": "moon", "prompt": "the Moon"}))

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--prompt", "the Moon", "--manifest", str(manifest),
                  "--out-dir", str(tmp_path / "out")])

    assert excinfo.value.code == 2


def test_cli_batch_prints_every_item_and_summary(monkeypatch, tmp_path,
                                                 capsys):
    _clear_network_env(monkeypatch)
    monkeypatch.delenv(tripo_assets.API_KEY_ENV, raising=False)
    manifest = _write_manifest(tmp_path, _items(
        {"name": "moon", "prompt": "the Moon"},
        {"name": "mars", "prompt": "Mars"}))
    out_dir = tmp_path / "out"
    cli = _load_cli()

    rc = cli.main(["--manifest", str(manifest), "--out-dir", str(out_dir)])

    captured = capsys.readouterr()
    assert rc == 3
    assert "[fail] moon → " in captured.out
    assert "[fail] mars → " in captured.out
    assert "成功 0 / 失败 2 / 跳过 0" in captured.out
    assert not out_dir.exists()
