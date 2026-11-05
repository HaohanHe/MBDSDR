#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
设置健壮性补测（tests/test_settings_robustness_extras.py）
==========================================================

上轮列出但未验证的三条：

  1. 手改 `sample_rate_hz: 1000000000` 时界面到底怎么表现——实测是
     "静默吸附到最近档、配置值留在磁盘"，因此补了能力前置校验与可见提示，
     这里锁住提示文案与"合法值会清除提示"。
  2. 同名键并发写：后写者胜，但是否留痕——`_merged_payload` 在覆盖
     磁盘上不同值时 `logger.warning`，这里用 caplog 钉住。
  3. 锁超时那次 flush 跳过写入：脏标记是否保留、下一次 flush 是否
     真的写进去——用人为超时验证"重试成功"。
"""
import json
import os
import sys
from contextlib import contextmanager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import settings as settings_module  # noqa: E402
from control_panel import ControlPanel  # noqa: E402
from settings import DesktopSettings  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ---------------------------------------------------------------------------
# 1. 采样率超出当前设备能力：不静默吸附
# ---------------------------------------------------------------------------
def test_out_of_capability_sample_rate_is_flagged(qapp, caplog):
    panel = ControlPanel()
    try:
        panel.set_sample_rate_options([250_000, 1_024_000, 2_048_000, 3_200_000])
        with caplog.at_level("WARNING"):
            applied = panel.set_sample_rate(1_000_000_000)

        assert applied == 3_200_000.0, "应选最近档（RTL 上限 3.2 MS/s）"
        assert panel.sample_rate_combo.currentData() == 3_200_000.0
        assert "超出当前设备能力" in panel.sample_rate_combo.toolTip()
        # format_sample_rate 的既有口径是 MS/s（1 GHz 显示为 1000.000 MS/s）
        assert "1000.000 MS/s" in panel.sample_rate_combo.toolTip()
        assert "⚠" in panel.sr_label.text()
        assert "超出当前设备能力" in panel.sr_label.toolTip()
        assert any("超出当前设备能力" in r.getMessage() for r in caplog.records)

        # 合法值必须清掉提示，不能留一个永久 ⚠。
        applied_ok = panel.set_sample_rate(2_048_000)
        assert applied_ok == 2_048_000.0
        assert panel.sample_rate_combo.toolTip() == ""
        assert panel.sr_label.text() == "采样率"
        assert panel.sr_label.toolTip() == ""
    finally:
        panel.deleteLater()


def test_in_range_rate_outside_visible_gears_is_not_called_over_capability(qapp):
    """连续可调后端只暴露范围两端：范围内但非精确档 ≠ 超出能力。"""
    panel = ControlPanel()
    try:
        panel.set_sample_rate_options([1, 1_000_000_000])
        applied = panel.set_sample_rate(999_000_000)
        assert applied == 1_000_000_000.0
        tip = panel.sr_label.toolTip()
        assert "不在当前可见档位" in tip, tip
        assert "超出当前设备能力" not in tip, "999 MS/s 在 network_iq 能力范围内，不能诬称超出"
    finally:
        panel.deleteLater()


def test_exact_upper_bound_of_network_iq_is_not_flagged(qapp):
    """1 GHz 恰好是 network_iq 的上界档位：合法，不该打 ⚠。"""
    panel = ControlPanel()
    try:
        panel.set_sample_rate_options([1, 1_000_000_000])
        applied = panel.set_sample_rate(1_000_000_000)
        assert applied == 1_000_000_000.0
        assert panel.sr_label.text() == "采样率"
        assert panel.sr_label.toolTip() == ""
        assert panel.sample_rate_combo.toolTip() == ""
    finally:
        panel.deleteLater()


# ---------------------------------------------------------------------------
# 2. 同名键并发覆盖：后写者胜，但覆盖事实必须进日志
# ---------------------------------------------------------------------------
def test_same_key_overwrite_is_logged(tmp_path, caplog):
    path = str(tmp_path / "desktop_settings.json")
    seed = DesktopSettings(path=path)
    seed.set("callsign", "STATION-SEED")
    seed.flush()

    # 两个实例各自 load（基线都是 SEED），A 先写，B 后写 → B 才是覆盖者。
    first = DesktopSettings.load(path=path)
    second = DesktopSettings.load(path=path)
    first.set("callsign", "STATION-A")
    first.flush()

    with caplog.at_level("WARNING", logger="settings"):
        second.set("callsign", "STATION-B")
        second.flush()

    assert any("被并发写入覆盖" in r.getMessage() and "callsign" in r.getMessage()
               for r in caplog.records), caplog.text
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["callsign"] == "STATION-B", "后写者胜（不做冲突解决）"


def test_single_instance_edit_is_not_a_false_overwrite(tmp_path, caplog):
    """单个实例改自己的值 ≠ 并发覆盖：不许刷假 warning。"""
    path = str(tmp_path / "desktop_settings.json")
    ds = DesktopSettings(path=path)
    ds.set("callsign", "OLD")
    ds.flush()

    with caplog.at_level("WARNING", logger="settings"):
        ds.set("callsign", "NEW")
        ds.flush()

    assert not [r for r in caplog.records
                if "被并发写入覆盖" in r.getMessage()], caplog.text


# ---------------------------------------------------------------------------
# 3. 锁超时的 flush：脏标记保留，下一次 flush 真的写进去
# ---------------------------------------------------------------------------
def test_lock_timeout_keeps_dirty_and_next_flush_writes(tmp_path, monkeypatch):
    config = str(tmp_path / "desktop_settings.json")
    ds = DesktopSettings(path=config)
    calls = {"n": 0}
    real_lock = settings_module._exclusive_lock

    @contextmanager
    def fake_lock(path, timeout=0.0):
        calls["n"] += 1
        if calls["n"] == 1:
            yield False                            # 人为锁超时
        else:
            with real_lock(path, timeout) as ok:
                yield ok

    monkeypatch.setattr(settings_module, "_exclusive_lock", fake_lock)

    ds.set("theme", "dark")
    ds.flush()

    assert calls["n"] == 1
    assert ds._dirty is True, "锁超时后必须保留脏标记"
    assert ds._dirty_keys == {"theme"}, "要重试的键不能丢"
    assert ds.last_error and "写锁" in ds.last_error
    assert not os.path.exists(config), "锁超时那次不应写文件"

    ds.flush()
    assert calls["n"] == 2
    assert ds._dirty is False and not ds._dirty_keys and ds.last_error is None
    with open(config, encoding="utf-8") as f:
        assert json.load(f)["theme"] == "dark", "第二次 flush 必须真的写进去"
