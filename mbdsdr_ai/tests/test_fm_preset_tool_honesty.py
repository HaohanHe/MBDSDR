# SPDX-License-Identifier: MIT
"""FM 预设工具层的诚实性：拒绝保存时不许再报「已保存」。

背景：``save_presets`` 在损坏且备份失败时返回空串；旧工具层没判空，
仍然拼出 ``已保存 N 个 FM 电台预设到 ``（路径为空），用户在界面上
看到的是「保存成功」。本文件钉两件事：

  1. 拒绝路径：``success=False``，``content`` 不含「已保存」、不含空路径；
  2. 读取路径：``sdr_fm_scan`` / ``sdr_fm_preset list`` 必须把
     「原文件已备份为 X」说出来，不许把空预设装成真实状态。

反证：把修复摘掉（照旧拼「已保存 … 到 」）→ 同样的断言必须红。
"""
import os
import sys
import types

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import fm_scan as fm  # noqa: E402
from mbdsdr_ai import sdr_tools  # noqa: E402
from mbdsdr_ai.sdr_tools import _fm_preset_tool, _fm_scan_tool  # noqa: E402
from mbdsdr_ai.tool_registry import ToolResult  # noqa: E402

CORRUPT = b'{"stations": [{"freq_mhz": 98.0, "name": "half'
STATIONS = [{"freq_mhz": 98.0, "strength_db": 12.0, "stereo": True,
             "active": True, "name": "test"}]


@pytest.fixture(autouse=True)
def presets(monkeypatch, tmp_path):
    """预设文件指到临时目录；用真实加载路径复位模块级状态。"""
    path = tmp_path / "fm_presets.json"
    monkeypatch.setattr(fm, "PRESETS_DIR", str(tmp_path))
    monkeypatch.setattr(fm, "PRESETS_FILE", str(path))
    # 文件不存在 → load_presets() 走公共复位分支，不直接改私有状态。
    fm.load_presets()
    return path


def _backups(path):
    # 理由文件 ``*.reason.txt`` 与备份同名同前缀，清点备份时要排除。
    return sorted(p for p in path.parent.glob(path.name + ".corrupt-*")
                  if not p.name.endswith(".reason.txt"))


def _assert_refusal_is_honest(result):
    """拒绝保存的返回必须是「没保存」，且不许出现空路径。"""
    assert result.success is False, "保存被拒却报成功"
    assert "已保存" not in result.content, f"content 仍在宣称已保存：{result.content!r}"
    assert all(not line.rstrip().endswith("到")
               for line in result.content.splitlines()), \
        f"content 里出现了空路径（以「到」结尾）：{result.content!r}"
    assert result.data.get("path") == ""


def _legacy_refusal_result(stations):
    """修复前的实现：空路径照样拼「已保存 N 个 FM 电台预设到 」。"""
    path = ""
    return ToolResult(
        success=True,
        content=f"已保存 {len(stations)} 个 FM 电台预设到 {path}",
        data={"stations": stations, "path": path})


# ---------------------------------------------------------------------------
# 1. 拒绝保存：不许说谎
# ---------------------------------------------------------------------------
def test_refused_save_reports_failure_without_saved_claim(presets, monkeypatch):
    presets.write_bytes(CORRUPT)
    fm.load_presets()                       # 第一次：真备份成功，状态回到干净
    presets.write_bytes(CORRUPT)
    # 第二次损坏且备份失败 → 走公共加载路径进入「拒写」状态。
    monkeypatch.setattr(fm, "backup_corrupt_presets", lambda _p: None)
    fm.load_presets()
    assert fm.preset_state().save_blocked is True

    result = _fm_preset_tool(types.SimpleNamespace(), {"action": "save",
                                                       "stations": STATIONS})
    _assert_refusal_is_honest(result)
    assert "未保存" in result.content
    assert len(_backups(presets)) == 1


def test_without_the_fix_the_refusal_check_turns_red():
    """变异：换回旧实现（不判空路径），上面的断言必须红。"""
    with pytest.raises(AssertionError):
        _assert_refusal_is_honest(_legacy_refusal_result(STATIONS))


# ---------------------------------------------------------------------------
# 2. 读取路径：损坏已备份必须说出来
# ---------------------------------------------------------------------------
def test_scan_reports_corrupt_presets_backup(presets, monkeypatch):
    presets.write_bytes(CORRUPT)
    monkeypatch.setattr(sdr_tools, "_fm_make_acquire", lambda mgr, sr: object())
    monkeypatch.setattr(sdr_tools, "_get_backend", lambda mgr: None)
    monkeypatch.setattr(sdr_tools, "scan_fm_band", _fake_scan)

    result = _fm_scan_tool(types.SimpleNamespace(), {})
    assert result.success is True
    assert "已备份为" in result.content, result.content
    assert len(_backups(presets)) == 1


def test_preset_list_reports_corrupt_presets_backup(presets):
    presets.write_bytes(CORRUPT)
    result = _fm_preset_tool(types.SimpleNamespace(), {"action": "list"})
    assert result.success is True
    assert "已备份为" in result.content, result.content


def _fake_scan(acquire, **kwargs):
    """替掉真扫频：返回一个确定电台，让工具层逻辑单独可测。"""
    return fm.FMScanResult(
        f_start_mhz=kwargs.get("f_start_mhz", 87.0),
        f_stop_mhz=kwargs.get("f_stop_mhz", 108.0),
        step_khz=kwargs.get("step_khz", 100.0),
        sample_rate_hz=kwargs.get("sample_rate_hz", 1_000_000.0),
        dwell_samples=kwargs.get("dwell_samples", 16384),
        noise_floor_db=-80.0,
        stations=[fm.FMStation(freq_mhz=98.0, strength_db=12.0,
                               stereo=True, active=True)],
        scan_time_s=0.1,
    )
