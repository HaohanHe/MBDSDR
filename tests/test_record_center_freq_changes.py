# SPDX-License-Identifier: MIT
"""P0-09：录音中调谐，sidecar 中心频率必须与真正下发的频率同源。

现状（实测）：段首快照 145.5 MHz 写进 ``center_freq_hz`` 后再不更新，
调谐到 146.5 MHz 也不记事件，回放整段错位 1.0 MHz。

修复后的契约：
- ``center_freq_hz`` = 该段**最后一次真正下发成功**的中心频率（按
  ``backend.get_frequency()`` 读回值，不按请求入参）；
- ``center_freq_changes`` = 有序事件表，每条含
  ``start_sample``（事件生效时的段内样本位）、``at_time``、``from_hz``、
  ``to_hz``、``target_hz``（请求值）、``actual_hz``（读回值）；未调谐时
  该字段不出现（不是恒为 null 的假功能）。

两条守卫（与代码同批落库）：
1. 逐个调谐入口触发，断言每个入口都真的产生了一条变更记录；
2. AST 扫描 ``desktop/main_window.py`` 的 ``set_frequency(`` 直调点，按
   所属函数名比对登记表——新增未登记出口立即变红。登记表唯一一份在本
   文件 ``_AST_REGISTRY``；分类口径见 ``docs/recording-center-freq.md``。

反证：去掉 ``_apply_hw_action`` 里的 ``_note_record_center_change`` 调用
（或改回只写段首快照），第 1 条用例即红。
"""
import ast
import json
import logging
import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication(sys.argv)

from main_window import MainWindow  # noqa: E402
from settings import DesktopSettings  # noqa: E402


class _StubBackend:
    """可调频的假后端：记录真正生效的频率，供 sidecar 对照。"""

    def __init__(self, hz=145_500_000.0):
        self.status = types.SimpleNamespace(
            error="", gain_db=0.0, demod_mode="FM", connected=True)
        self.device = types.SimpleNamespace(name="Stub SDR")
        self.applied_hz = float(hz)

    def set_frequency(self, hz):
        self.applied_hz = float(hz)
        # 与真实后端同口径：真正生效后才算「下发成功」
        return float(hz) == self.applied_hz

    def set_demod(self, mode):
        self.status.demod_mode = str(mode)
        return True

    def get_frequency(self):
        return self.applied_hz

    def get_sample_rate(self):
        return 1_000_000.0

    def get_status(self):
        return self.status

    def read_samples(self, n):
        return np.zeros(int(n), dtype=np.complex64)


class _LaggingBackend(_StubBackend):
    """接受写频（返回 True）但设备没跟：读回仍是旧值。"""

    def set_frequency(self, hz):
        return True

    def get_frequency(self):
        return 145_500_000.0


class _UnreadableBackend(_StubBackend):
    """写频成功，但读回中心频率失败。"""

    def get_frequency(self):
        raise RuntimeError("readback boom")


@pytest.fixture(autouse=True)
def _pin_default_config(isolated_profile, monkeypatch):
    monkeypatch.setattr(
        "settings.default_config_path",
        lambda: isolated_profile / "desktop_settings.json")


@pytest.fixture()
def window(isolated_profile):
    w = MainWindow()
    # 星象仪视图惰性构建：点星录音要真实天空视图（其惰性补建等价于点开页签）。
    w._ensure_sky_view()
    w.settings = DesktopSettings.load(
        str(isolated_profile / "desktop_settings.json"))
    yield w
    try:
        w._disconnect()
    except Exception:
        pass
    w.close()


def _start_segment(w, path, sr=1_000_000.0, center=145_500_000.0):
    w._record_session_path = str(path)
    w._record_segments = []
    w._record_segment_index = 1
    w._record_rate_changes = []
    w._record_seconds = 0
    w._record_gain_db = 0.0
    w._record_device = "Stub"
    w._record_driver = "StubBackend"
    w._record_start_time = "2026-11-11T00:00:00"
    w._record_timer = None
    assert w._open_record_segment(
        str(path), sr, center, "2026-11-11T00:00:00", 1) is True
    w._recording = True
    for btn in (w.record_btn, w.control_panel.record_button):
        btn.blockSignals(True)
        btn.setChecked(True)
        btn.blockSignals(False)


def _finalize_and_read(window, path):
    window._recording = False
    assert window._finalize_record_segment() is not None
    with open(str(path) + ".json", encoding="utf-8") as f:
        return json.load(f)


def test_sidecar_tracks_center_freq_changes(window, tmp_path):
    window._active_sdr_backend = _StubBackend()
    path = tmp_path / "retune.iq"
    _start_segment(window, path)

    window._record_write_chunk(np.ones(1024, dtype=np.complex64))
    assert window._on_tune_sdr(145_800_000.0, "FM") is True
    window._record_write_chunk(np.ones(1024, dtype=np.complex64))
    assert window._on_tune_sdr(146_500_000.0, "FM") is True

    meta = _finalize_and_read(window, path)

    # 1) 段尾频率必须是真的最后一次下发值，不是段首快照
    assert float(meta["center_freq_hz"]) == 146_500_000.0, (
        f"sidecar 中心频率仍是旧值: {meta.get('center_freq_hz')!r}")

    # 2) 事件序列必须完整、有序、带样本位；target/actual 同时留档
    changes = meta.get("center_freq_changes")
    assert changes, f"缺 center_freq_changes: {meta!r}"
    assert [int(c["start_sample"]) for c in changes] == [1024, 2048], changes
    assert [float(c["from_hz"]) for c in changes] == [145_500_000.0, 145_800_000.0]
    assert [float(c["to_hz"]) for c in changes] == [145_800_000.0, 146_500_000.0]
    assert [float(c["target_hz"]) for c in changes] == [145_800_000.0, 146_500_000.0]
    assert [float(c["actual_hz"]) for c in changes] == [145_800_000.0, 146_500_000.0]
    assert all(c.get("at_time") for c in changes), changes


def test_no_retune_means_no_changes_field(window, tmp_path):
    """没调谐就不许出现 center_freq_changes——不留恒为 null 的假字段。"""
    window._active_sdr_backend = _StubBackend()
    path = tmp_path / "plain.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(512, dtype=np.complex64))

    meta = _finalize_and_read(window, path)
    assert float(meta["center_freq_hz"]) == 145_500_000.0
    assert "center_freq_changes" not in meta, meta.get("center_freq_changes")


# ---------------------------------------------------------------------------
# 守卫 1：逐个调谐入口触发，每个入口都必须留下一条记录
# ---------------------------------------------------------------------------

_TUNE_ENTRIES = [
    ("_on_tune_fm", 146_000_000.0),
    ("_on_tune_am", 146_000_000.0),          # int kHz → Hz
    ("_on_tune_sdr", 146_500_000.0),
    ("_on_module_tune", 147_000_000.0),
    ("_on_spectrum_freq_changed", 148_000_000.0),
]


def _invoke_tune_entry(window, entry, target_hz):
    if entry == "_on_tune_fm":
        window._on_tune_fm(target_hz / 1e6)
    elif entry == "_on_tune_am":
        window._on_tune_am(int(target_hz / 1e3))
    elif entry == "_on_tune_sdr":
        assert window._on_tune_sdr(target_hz, "FM") is True
    elif entry == "_on_module_tune":
        window._on_module_tune(target_hz)
    elif entry == "_on_spectrum_freq_changed":
        window._on_spectrum_freq_changed(target_hz / 1e6)   # 该入口吃 MHz
    else:  # pragma: no cover - 参数化列表写死在上面
        raise AssertionError(f"未定义的调谐入口: {entry}")


@pytest.mark.parametrize("entry,target_hz", _TUNE_ENTRIES)
def test_each_tune_entry_records_center_change(window, tmp_path, entry, target_hz):
    window._active_sdr_backend = _StubBackend()
    path = tmp_path / f"{entry}.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(512, dtype=np.complex64))

    _invoke_tune_entry(window, entry, target_hz)

    meta = _finalize_and_read(window, path)
    assert float(meta["center_freq_hz"]) == pytest.approx(target_hz)
    changes = meta.get("center_freq_changes") or []
    assert len(changes) == 1, f"{entry} 没留下变更记录: {meta!r}"
    c = changes[0]
    assert int(c["start_sample"]) == 512
    assert float(c["from_hz"]) == pytest.approx(145_500_000.0)
    assert float(c["to_hz"]) == pytest.approx(target_hz)
    assert float(c["target_hz"]) == pytest.approx(target_hz)
    assert float(c["actual_hz"]) == pytest.approx(target_hz)


def test_sweep_done_records_center_change(window, tmp_path, monkeypatch):
    """扫频完成调最强台（不走收口的补记点）也必须记账。"""
    window._active_sdr_backend = _StubBackend()
    path = tmp_path / "sweep.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(2048, dtype=np.complex64))
    monkeypatch.setattr(window, "_sweep_cleanup", lambda: None)
    result = types.SimpleNamespace(
        activities=[types.SimpleNamespace(
            center_hz=146_000_000.0, bandwidth_hz=120_000.0, peak_db=-21.0)],
        noise_floor_db=-60.0, freqs_hz=[145_000_000.0, 147_000_000.0],
        threshold_db=10.0)

    window._on_sweep_done(result)

    meta = _finalize_and_read(window, path)
    assert float(meta["center_freq_hz"]) == pytest.approx(146_000_000.0)
    changes = meta.get("center_freq_changes") or []
    assert len(changes) == 1, meta
    assert int(changes[0]["start_sample"]) == 2048
    assert float(changes[0]["target_hz"]) == pytest.approx(146_000_000.0)


def test_sky_click_records_center_change(window, tmp_path):
    """天空视图点卫星调谐（不走收口的补记点）也必须记账。"""
    window._active_sdr_backend = _StubBackend()
    path = tmp_path / "sky.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(700, dtype=np.complex64))
    obj = types.SimpleNamespace(
        obj_type="satellite", name="ISS", azimuth_deg=10.0,
        elevation_deg=20.0, frequency_hz=145_800_000.0)

    window._on_sky_object_clicked(obj)

    meta = _finalize_and_read(window, path)
    assert float(meta["center_freq_hz"]) == pytest.approx(145_800_000.0)
    changes = meta.get("center_freq_changes") or []
    assert len(changes) == 1, meta
    assert float(changes[0]["actual_hz"]) == pytest.approx(145_800_000.0)


# ---------------------------------------------------------------------------
# 「要到了但设备没跟」与「读不回来」：都要留 warning，且不静默写变更
# ---------------------------------------------------------------------------

def test_target_actual_mismatch_warns_and_records_actual(window, tmp_path, caplog):
    window._active_sdr_backend = _LaggingBackend()
    path = tmp_path / "lagging.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(256, dtype=np.complex64))

    with caplog.at_level(logging.WARNING):
        assert window._on_tune_sdr(146_500_000.0, "FM") is True

    assert "调谐未跟上" in caplog.text, caplog.text
    assert "146.500" in caplog.text and "145.500" in caplog.text, caplog.text
    meta = _finalize_and_read(window, path)
    # 设备没动 → 没有真实变更，不许凭空补一条「调谐成功」
    assert float(meta["center_freq_hz"]) == pytest.approx(145_500_000.0)
    assert "center_freq_changes" not in meta, meta.get("center_freq_changes")


def test_readback_failure_warns_and_skips(window, tmp_path, caplog):
    window._active_sdr_backend = _UnreadableBackend()
    path = tmp_path / "unreadable.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(128, dtype=np.complex64))

    with caplog.at_level(logging.WARNING):
        assert window._on_tune_sdr(146_500_000.0, "FM") is True

    assert "读取调谐后中心频率失败" in caplog.text, caplog.text
    meta = _finalize_and_read(window, path)
    assert "center_freq_changes" not in meta, meta.get("center_freq_changes")


# ---------------------------------------------------------------------------
# 守卫 2：一拍一记——同一段同一拍被两个调谐出口各记一次就会重复写侧车
# ---------------------------------------------------------------------------

def test_two_tune_exits_in_one_tick_write_one_change(window, tmp_path):
    """判定口径：按 (segment_index, start_sample) 去重，不按墙钟时间窗。

    两次调谐之间没写进任何样本 = 同一拍，只有最后一次作用于录音；事件表
    只保留一条净变化（原 from → 最后一次 to）。
    """
    window._active_sdr_backend = _StubBackend()
    path = tmp_path / "one_tick.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(512, dtype=np.complex64))

    # 同一拍：两个真实调谐出口先后触发，中间不写样本
    assert window._on_tune_sdr(146_000_000.0, "FM") is True
    window._on_tune_fm(147.0)

    meta = _finalize_and_read(window, path)
    changes = meta.get("center_freq_changes") or []
    assert len(changes) == 1, f"同一拍被两个出口重复记账: {changes!r}"
    c = changes[0]
    assert int(c["segment_index"]) == 1
    assert int(c["start_sample"]) == 512
    assert float(c["from_hz"]) == pytest.approx(145_500_000.0)
    assert float(c["to_hz"]) == pytest.approx(147_000_000.0)
    keys = [(int(x["segment_index"]), int(x["start_sample"])) for x in changes]
    assert len(keys) == len(set(keys)), f"侧车出现重复拍: {keys}"
    assert float(meta["center_freq_hz"]) == pytest.approx(147_000_000.0)


def test_same_tick_tune_back_to_origin_drops_event(window, tmp_path):
    """同一拍调出去又调回原值 = 净变化 0，不许留一条假变更。"""
    window._active_sdr_backend = _StubBackend()
    path = tmp_path / "net_zero.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(256, dtype=np.complex64))

    assert window._on_tune_sdr(146_000_000.0, "FM") is True
    window._on_tune_fm(145.5)

    meta = _finalize_and_read(window, path)
    assert "center_freq_changes" not in meta, meta.get("center_freq_changes")
    assert float(meta["center_freq_hz"]) == pytest.approx(145_500_000.0)


def test_counterproof_disabling_dedup_writes_two_events(window, tmp_path,
                                                        monkeypatch):
    """反证：把 (segment_index, start_sample) 去重关掉，同一拍就会写两条。

    正证用例（上一条）只有「只写一条」的断言，若有人把去重删掉，它仍然会
    通过。这条把 `_is_same_record_tick` 打桩成恒 False，复现被删掉后的
    重复写入，保证正证真的踩在去重逻辑上。
    """
    window._active_sdr_backend = _StubBackend()
    path = tmp_path / "no_dedup.iq"
    _start_segment(window, path)
    window._record_write_chunk(np.ones(512, dtype=np.complex64))
    monkeypatch.setattr(MainWindow, "_is_same_record_tick",
                        staticmethod(lambda *a, **k: False))

    assert window._on_tune_sdr(146_000_000.0, "FM") is True
    window._on_tune_fm(147.0)

    meta = _finalize_and_read(window, path)
    changes = meta.get("center_freq_changes") or []
    assert len(changes) == 2, f"关掉去重后应重复写两条: {changes!r}"
    assert [int(c["start_sample"]) for c in changes] == [512, 512]
    assert len({(int(c["segment_index"]), int(c["start_sample"]))
                for c in changes}) == 1


def test_apply_hw_action_passes_through_exception_text(window):
    """收口异常原文必须在状态栏透传 `<类型>: <原文>`，不许折成通用句。"""
    def _boom():
        raise RuntimeError("硬件爆了")

    assert window._apply_hw_action("调谐", _boom) is False
    msg = window.statusBar().currentMessage()
    assert "RuntimeError: 硬件爆了" in msg, msg


# ---------------------------------------------------------------------------
# 守卫 3：AST 扫描直调出口，新函数名出现即红
# ---------------------------------------------------------------------------

#: 唯一一份机器登记表。tune=用户可见调谐；initial=连接期初始频率；
#: rollback=面板扫频结束后恢复原频点；scan=扫频内部逐点换频（录音时已停流）。
_AST_REGISTRY = {
    "_SweepWorker.run.acquire": "scan",
    "MainWindow._apply_connect_params": "initial",
    "MainWindow._on_tune_fm": "tune",
    "MainWindow._on_tune_am": "tune",
    "MainWindow._on_tune_sdr": "tune",
    "MainWindow._on_module_tune": "tune",
    "MainWindow._on_spectrum_freq_changed": "tune",
    "MainWindow._on_sweep_done": "tune",
    "MainWindow._on_sky_object_clicked": "tune",
    "MainWindow._on_panel_scan_ended": "rollback",
}


class _SetFrequencyCallsiteVisitor(ast.NodeVisitor):
    """收集 ``backend.set_frequency(...)`` / ``self._active_sdr_backend.…`` 的所属函数。"""

    def __init__(self):
        self._stack = []
        self.sites = []
        self.functions = {}

    def visit_ClassDef(self, node):
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node):
        self._stack.append(node.name)
        self.functions[".".join(self._stack)] = node
        self.generic_visit(node)
        self._stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "set_frequency":
            recv = func.value
            is_backend = (
                (isinstance(recv, ast.Name) and recv.id == "backend")
                or (isinstance(recv, ast.Attribute)
                    and recv.attr == "_active_sdr_backend"))
            if is_backend:
                self.sites.append(".".join(self._stack))
        self.generic_visit(node)


def _main_window_source():
    path = os.path.join(_ROOT, "desktop", "main_window.py")
    with open(path, encoding="utf-8") as f:
        return path, f.read()


def test_every_set_frequency_callsite_is_registered():
    path, src = _main_window_source()
    visitor = _SetFrequencyCallsiteVisitor()
    visitor.visit(ast.parse(src, filename=path))

    found = set(visitor.sites)
    unknown = sorted(found - set(_AST_REGISTRY))
    assert not unknown, (
        "发现未登记的 set_frequency 直调出口：" + "、".join(unknown)
        + "。新增调谐路径请走 `_apply_hw_action(\"调谐\", …, target_hz=…)`；"
        "确实不能走的，在登记表里归类并写明补记理由。")
    stale = sorted(set(_AST_REGISTRY) - found)
    assert not stale, (
        "登记表里的出口已不存在（改名/挪走），请同步登记表：" + "、".join(stale))

    for qualname, category in _AST_REGISTRY.items():
        if category != "tune":
            continue
        segment = ast.get_source_segment(src, visitor.functions[qualname])
        assert ("_apply_hw_action(" in segment
                or "_note_record_center_change(" in segment), (
            f"{qualname} 被登记为调谐出口，却既不经过收口也不补记："
            "录音中心频率会静默漏记")


def test_tune_calls_through_apply_hw_action_pass_target_hz():
    path, src = _main_window_source()
    tree = ast.parse(src, filename=path)
    tune_calls = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_apply_hw_action"
                and node.args):
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == "调谐"):
            continue
        tune_calls += 1
        keywords = {kw.arg for kw in node.keywords}
        assert "target_hz" in keywords, (
            f"第 {node.lineno} 行的调谐收口没传 target_hz："
            "sidecar 将无法区分「请求值」与「读回值」")
    assert tune_calls >= 5, f"调谐收口调用点异常少（{tune_calls}）"


def test_satellite_track_tunes_through_apply_hw_action():
    """第 7 条（逐拍调星）已并入 `_on_tune_sdr` 收口，不得退回直调。"""
    path, src = _main_window_source()
    visitor = _SetFrequencyCallsiteVisitor()
    visitor.visit(ast.parse(src, filename=path))
    node = visitor.functions["MainWindow._poll_satellite_track"]
    segment = ast.get_source_segment(src, node)
    assert "self._on_tune_sdr(" in segment, (
        "逐拍调星没有走 `_on_tune_sdr` → `_apply_hw_action` 收口")
