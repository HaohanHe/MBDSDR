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
import ast
import inspect
import json
import os
import sys
import types
from contextlib import contextmanager
from textwrap import dedent

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import settings as settings_module  # noqa: E402
import control_panel as control_panel_module  # noqa: E402
from constants import FREQ_MAX_HZ  # noqa: E402
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
# 1b. 其它数值型恢复项：被替换/被钳位同样必须可见
# ---------------------------------------------------------------------------
def _mutated_panel_method(name: str, *replacements: tuple[str, str]):
    """从 ControlPanel 源码取方法并做 AST 变异，返回未绑定的函数副本。

    反证用：不改生产文件，只在内存里跑一份去掉守卫的副本，证明守卫"有牙"。
    定位按 AST 节点文本（if 条件 / 语句），不再用 `src.count(needle)`——
    后者会把源码文本 pin 带进测试，守卫改写后只能靠人肉更新计数。
    """
    src = dedent(inspect.getsource(getattr(ControlPanel, name)))
    tree = ast.parse(src)

    def _child_location(parent, child):
        for field, value in ast.iter_fields(parent):
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if item is child:
                        return field, index
            elif value is child:
                return field, None
        raise AssertionError("找不到被变异节点的父级字段")

    for needle, repl in replacements:
        matches: list[tuple[ast.AST, ast.AST]] = []
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                if (isinstance(child, ast.If)
                        and needle.startswith("if ") and needle.endswith(":")):
                    test_src = ast.get_source_segment(src, child.test) or ""
                    if test_src.strip() == needle[3:-1].strip():
                        matches.append((parent, child))
                elif (isinstance(child, ast.stmt)
                      and (ast.get_source_segment(src, child) or "").strip()
                      == needle.strip()):
                    matches.append((parent, child))
        assert len(matches) == 1, (
            f"守卫写法已变，先更新这条反证: {needle!r}（命中 {len(matches)} 次）")
        parent, node = matches[0]
        if isinstance(node, ast.If) and needle.startswith("if "):
            node.test = ast.parse(repl[3:-1].strip(), mode="eval").body
        else:
            field, index = _child_location(parent, node)
            new_nodes = ast.parse(repl).body
            if index is None:
                setattr(parent, field, new_nodes[0])
            else:
                getattr(parent, field)[index:index + 1] = new_nodes
    mutated = ast.unparse(tree)
    namespace: dict = {}
    exec(compile(mutated, f"<mutated {name}>", "exec"),
         vars(control_panel_module), namespace)  # noqa: S102
    return namespace[name]


def test_out_of_global_range_frequency_is_flagged(qapp, caplog):
    panel = ControlPanel()
    try:
        with caplog.at_level("WARNING"):
            panel.set_frequency_hz(1e12)

        assert panel.get_frequency_hz() == FREQ_MAX_HZ, "全局边界钳位行为保持不变"
        tip = panel.freq_input.toolTip()
        assert "⚠" in tip, "恢复值被钳位却没有任何可见提示"
        assert "1000 GHz" in tip, tip
        assert "6 GHz" in tip, tip
        assert "配置文件未改" in tip, tip
        assert any("全局调谐范围" in r.getMessage() for r in caplog.records)

        # 合法值必须还原静态说明，不能留下永久 ⚠
        panel.set_frequency_hz(98e6)
        assert "⚠" not in panel.freq_input.toolTip()
        assert panel.get_frequency_hz() == 98e6
    finally:
        panel.deleteLater()


def test_out_of_visible_gear_bandwidth_is_flagged(qapp, caplog):
    panel = ControlPanel()
    try:
        with caplog.at_level("WARNING"):
            panel.set_vfo_bandwidth(100_000)

        assert panel.vfo_bw_combo.currentData() == 180_000.0, "应落到最近可见档"
        tip = panel.vfo_bw_combo.toolTip()
        assert "⚠" in tip, "带宽被静默换成最近档"
        assert "100000 Hz" in tip and "180000 Hz" in tip, tip
        assert "配置文件未改" in tip, tip
        assert any("不在当前可见档位" in r.getMessage() for r in caplog.records)

        # 精确命中档位：还原静态说明
        panel.set_vfo_bandwidth(12_500)
        assert "⚠" not in panel.vfo_bw_combo.toolTip()

        # 用户手动选档 = 采纳当前值，提示必须消失
        panel.set_vfo_bandwidth(100_000)
        assert "⚠" in panel.vfo_bw_combo.toolTip()
        idx = panel.vfo_bw_combo.findData(12_500.0)
        panel.vfo_bw_combo.setCurrentIndex(idx)     # 触发 _on_vfo_bw_changed
        assert "⚠" not in panel.vfo_bw_combo.toolTip()

        # NaN = 没有带宽值：保持当前档与提示，绝不按"最近档"伪造成 500 Hz
        panel.set_vfo_bandwidth(100_000)
        assert "⚠" in panel.vfo_bw_combo.toolTip()
        before = panel.vfo_bw_combo.currentData()
        panel.set_vfo_bandwidth(float("nan"))
        assert panel.vfo_bw_combo.currentData() == before
        assert "⚠" in panel.vfo_bw_combo.toolTip(), "NaN 不应改写既有提示"
    finally:
        panel.deleteLater()


def test_out_of_rtl_gain_range_restore_is_flagged(qapp, caplog):
    panel = ControlPanel()
    try:
        with caplog.at_level("WARNING"):
            panel.set_gain(80.0)

        assert panel.gain_slider.value() == 49, "未连接时仍按 RTL 典型上限显示"
        assert panel.gain_val.text() == "—", "未连接没有真实读数，不许写成 49 dB"
        tip = panel.gain_slider.toolTip()
        assert "⚠" in tip, "配置请求 80 dB 被夹到 49 dB 却没有任何可见提示"
        assert "80 dB" in tip and "49 dB" in tip, tip
        assert "配置文件未改" in tip, tip

        panel.set_gain(20.0)
        assert "⚠" not in panel.gain_slider.toolTip()

        # 用户手动拖滑杆 = 采纳当前值，提示必须消失
        panel.set_gain(80.0)
        assert "⚠" in panel.gain_slider.toolTip()
        panel.gain_slider.setValue(30)              # 触发 _on_gain_changed
        assert "⚠" not in panel.gain_slider.toolTip()
    finally:
        panel.deleteLater()


def test_capability_gain_clamp_does_not_write_back_and_is_flagged(qapp, caplog):
    """连接后按设备能力收放滑杆：夹出来的值不许经信号回写配置。"""
    panel = ControlPanel()
    try:
        emitted = []
        panel.gain_changed.connect(emitted.append)
        panel.set_sdr_connected(True)
        panel.set_gain(45.0)
        assert panel.gain_slider.value() == 45

        with caplog.at_level("WARNING"):
            panel.set_gain_range(0.0, 30.0)

        assert panel.gain_slider.value() == 30, "滑杆按设备上限收放"
        assert panel.gain_val.text() == "30.0 dB"
        assert emitted == [], "夹出来的值不许发 gain_changed（否则会回写配置）"
        tip = panel.gain_slider.toolTip()
        assert "⚠" in tip and "超出所连设备能力" in tip, tip
        assert "配置文件未改" in tip, tip
        assert any("超出所连设备能力" in r.getMessage() for r in caplog.records)
    finally:
        panel.deleteLater()


def test_removing_substitution_guards_makes_the_cases_red(qapp):
    """反证：去掉"值被替换"的判定分支后，同一输入不再产生任何提示。

    变异只发生在内存里的函数副本；正向用例里对 ⚠ / 文案的断言会因此变红，
    证明这些断言确实钉住了守卫而不是碰巧通过。
    """
    panel = ControlPanel()
    try:
        freq = types.MethodType(
            _mutated_panel_method(
                "set_frequency_hz", ("if freq_hz != requested:", "if False:")),
            panel)
        bw = types.MethodType(
            _mutated_panel_method(
                "set_vfo_bandwidth", ("if abs(applied - bw) > 1e-9:", "if False:")),
            panel)
        gain = types.MethodType(
            _mutated_panel_method(
                "set_gain", ("if abs(requested - db) > 1e-9:", "if False:")),
            panel)
        gain_range = types.MethodType(
            _mutated_panel_method(
                "set_gain_range", ("if applied != requested:", "if False:")),
            panel)

        # 先落到合法值，再分别跑变异副本，确保最后留在控件上的是变异结果
        panel.set_gain(45.0)
        freq(1e12)
        bw(100_000)
        gain(80.0)
        gain_range(0.0, 30.0)

        assert "⚠" not in panel.freq_input.toolTip(), "变异后仍提示 = 反证失效"
        assert "⚠" not in panel.vfo_bw_combo.toolTip(), "变异后仍提示 = 反证失效"
        assert "⚠" not in panel.gain_slider.toolTip(), "变异后仍提示 = 反证失效"
    finally:
        panel.deleteLater()


def test_removing_gain_range_signal_block_writes_back(qapp):
    """反证：去掉 setRange 前后的 blockSignals，设备夹值会经信号回写配置。"""
    panel = ControlPanel()
    try:
        emitted = []
        panel.gain_changed.connect(emitted.append)
        panel.set_gain(45.0)
        mutated = types.MethodType(
            _mutated_panel_method(
                "set_gain_range",
                ("self.gain_slider.blockSignals(True)", "pass"),
                ("self.gain_slider.blockSignals(False)", "pass")),
            panel)
        mutated(0.0, 30.0)

        assert emitted == [30], \
            "去掉信号屏蔽后夹值应外发（对应正向用例的 emitted==[] 断言会因此变红）"
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
