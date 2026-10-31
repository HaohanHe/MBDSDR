# SPDX-License-Identifier: MIT
"""VNA 工具注册面测试：注册/诚实空态/NEED_HW/replay e2e/纯计算分析/参数校验。"""

import json

import pytest

from mbdsdr_ai.tool_registry import ToolRegistry
from mbdsdr_ai.vna_tools import register_vna_tools


@pytest.fixture()
def reg():
    r = ToolRegistry()
    register_vna_tools(r)
    return r


def _call(reg, name, args):
    return reg.tools[name]["handler"](args)


# ── 注册面 ────────────────────────────────────────────────

def test_vna_tools_registered(reg):
    names = {t["name"] for t in reg.list_tools()}
    assert {"vna_list_ports", "vna_sweep", "vna_analyze"} <= names


def test_vna_catalog_entries_exist_and_en_is_clean(reg, tmp_path, monkeypatch):
    monkeypatch.delenv("MBSDR_LANG", raising=False)
    from mbdsdr_ai import i18n
    i18n._cache.clear()
    cat = i18n.load_catalog("en")
    for name in ("vna_list_ports", "vna_sweep", "vna_analyze"):
        key = f"tool.{name}.description"
        assert key in cat, f"en.json 缺 {key}"
        base = reg.tools[name]["definition"]["function"]["description"]
        assert cat[key] != base
        assert not any("\u4e00" <= ch <= "\u9fff" for ch in cat[key])


# ── vna_list_ports 诚实空态 ───────────────────────────────

def test_list_ports_honest_empty(reg, monkeypatch):
    monkeypatch.setattr("mbdsdr_ai.vna_tools.find_nanovna_ports",
                        lambda *a, **k: [])
    res = _call(reg, "vna_list_ports", {})
    assert res.success
    assert res.data["ports"] == []
    assert "诚实空态" in res.content


def test_list_ports_reports_found(reg, monkeypatch):
    monkeypatch.setattr(
        "mbdsdr_ai.vna_tools.find_nanovna_ports",
        lambda *a, **k: [("COM3", 0x0483, 0x5740)])
    res = _call(reg, "vna_list_ports", {})
    assert res.success
    assert res.data["ports"] == [{"port": "COM3", "vid": 0x0483,
                                  "pid": 0x5740}]


# ── vna_sweep 无设备诚实失败 ──────────────────────────────

def test_sweep_no_device_is_honest_need_hw(reg, monkeypatch):
    monkeypatch.setattr("mbdsdr_ai.nanovna_dump.find_nanovna_ports",
                        lambda *a, **k: [])
    res = _call(reg, "vna_sweep",
                {"start_hz": 430_000_000, "stop_hz": 440_000_000,
                 "points": 101})
    assert not res.success
    assert res.error == "NEED_HW"
    assert "vna_list_ports" in res.next_step
    # 绝不产出伪测量数据
    assert res.data == {}


# ── vna_sweep 参数校验 ────────────────────────────────────

def test_sweep_rejects_inverted_range(reg):
    res = _call(reg, "vna_sweep",
                {"start_hz": 440_000_000, "stop_hz": 430_000_000,
                 "points": 101})
    assert not res.success
    assert "小于" in res.content


def test_sweep_rejects_too_few_points(reg):
    res = _call(reg, "vna_sweep",
                {"start_hz": 430_000_000, "stop_hz": 440_000_000,
                 "points": 1})
    assert not res.success
    assert "≥2" in res.content


def test_sweep_rejects_non_positive_freq(reg):
    res = _call(reg, "vna_sweep",
                {"start_hz": 0, "stop_hz": 440_000_000, "points": 101})
    assert not res.success


# ── vna_sweep replay e2e（无硬件走真协议路径）────────────

def test_sweep_replay_end_to_end(reg, monkeypatch):
    from mbdsdr_ai.nanovna_client import NanoVNAClient, ReplayTransport
    from mbdsdr_ai.nanovna_dump import _selftest_script

    script = _selftest_script(430_000_000, 440_000_000, 101, seg_points=101)

    def _fake_open(port, **kw):
        client = NanoVNAClient(transport=ReplayTransport(script))
        client.connect()  # 真实走 _open_with_retry 负责的连接+握手
        return client

    monkeypatch.setattr("mbdsdr_ai.nanovna_dump._open_with_retry", _fake_open)

    res = _call(reg, "vna_sweep",
                {"port": "COM9", "start_hz": 430_000_000,
                 "stop_hz": 440_000_000, "points": 101})
    assert res.success, res.content[:500]
    assert res.data["points"] == 101
    payload = json.loads(res.content)
    assert payload["analysis"]["one_port"]["points"] == 101
    assert "vswr_min" in payload["analysis"]["one_port"]
    assert len(payload["vswr"]) == 101


# ── vna_analyze 纯计算 ────────────────────────────────────

_FREQS = [430_000_000 + i * 100_000 for i in range(101)]


def test_analyze_matched_load(reg):
    s11 = [[0.0, 0.0]] * len(_FREQS)
    res = _call(reg, "vna_analyze",
                {"frequencies_hz": _FREQS, "s11": s11})
    assert res.success
    out = json.loads(res.content)
    _, vswr_min = out["one_port"]["vswr_min"]
    assert vswr_min == pytest.approx(1.0, abs=1e-3)


def test_analyze_two_port(reg):
    s11 = [[0.1, 0.0]] * len(_FREQS)
    s21 = [[0.5, 0.0]] * len(_FREQS)
    res = _call(reg, "vna_analyze",
                {"frequencies_hz": _FREQS, "s11": s11, "s21": s21})
    assert res.success
    out = json.loads(res.content)
    assert "two_port" in out


def test_analyze_rejects_shape_mismatch(reg):
    res = _call(reg, "vna_analyze",
                {"frequencies_hz": _FREQS, "s11": [[0.0, 0.0]] * 50})
    assert not res.success
    assert "形状不一致" in res.content


def test_analyze_rejects_bad_point(reg):
    res = _call(reg, "vna_analyze",
                {"frequencies_hz": _FREQS,
                 "s11": [[0.0, 0.0]] * 100 + [["bad"]]})
    assert not res.success
    assert "格式" in res.content or "复数对" in res.content


def test_analyze_rejects_s21_length_mismatch(reg):
    res = _call(reg, "vna_analyze",
                {"frequencies_hz": _FREQS,
                 "s11": [[0.0, 0.0]] * 101,
                 "s21": [[0.5, 0.0]] * 50})
    assert not res.success
    assert "形状不一致" in res.content
