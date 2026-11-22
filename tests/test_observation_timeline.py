# SPDX-License-Identifier: MIT
"""观测中途事件接入时间轴：录制/调谐/增益/解码命中/读空 → 档案与 SigMF。

这些断言钉的是「时间轴只记录真实发生的事件」：
  * 未录制时不得凭空记事件（否则下次录制会带着一段没发生过的观测过程）；
  * 调谐/增益只在值真的变了才记，失败路径不留痕；
  * 读空是设备真的没给样本，必须如实带上持续时长与后端原因；
  * SigMF annotations 的 ``core:sample_start`` 必须落在真实样本数范围内；
  * 超限只追加一条 ``timeline_truncated``，不静默丢失也不无限增长。

全部使用真实文件落盘与真实线程；假后端只伪造「设备返回什么样本」，不伪造
事件产物本身。
"""
import json
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import numpy as np  # noqa: E402
import pytest  # noqa: E402

import mbdsdr_ai.core.controller as controller_mod  # noqa: E402
from mbdsdr_ai.core.controller import (  # noqa: E402
    SDRController,
    _decode_message_is_hit,
)
from mbdsdr_ai.observation_record import (  # noqa: E402
    ObservationRecord,
    make_event,
    normalize_timeline,
)


class _FakeBackend:
    """最小假后端：只实现控制器读样本/设参数用的面。

    样本是真实 numpy 数组（不是伪造的登记数据），``empty_reads`` 用来模拟
    设备有界读超时返回 None。``last_read_reason`` 提供后端原因，供读空事件
    如实带上。
    """

    def __init__(self, *, empty_reads=0, block=1024):
        self.last_read_reason = None
        self._empty_reads = int(empty_reads)
        self._block = int(block)
        self.reads = 0

    def read_samples(self, n):
        self.reads += 1
        if self.reads <= self._empty_reads:
            self.last_read_reason = {"code": "read_timeout", "timeout_s": 0.1}
            time.sleep(0.005)
            return None
        self.last_read_reason = None
        time.sleep(0.002)
        return np.zeros(self._block, dtype=np.complex64)

    def set_frequency(self, hz):
        return True

    def set_gain(self, db):
        return True

    def set_sample_rate(self, sr):
        return True


def _attach(ctrl, backend, *, freq=100_000_000.0, gain=0.0):
    """把假后端接到控制器上（不碰真实 USB / 真实设备枚举）。"""
    ctrl._connected = True
    ctrl._backend = backend
    ctrl._sample_rate = 2_400_000.0
    ctrl._freq_hz = float(freq)
    ctrl._gain_db = float(gain)


def _events(ctrl):
    with ctrl._recording_events_lock:
        return list(ctrl._recording_events)


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


@pytest.fixture()
def ctrl():
    c = SDRController()
    yield c
    # start_recording 会开真线程，任何断言失败也必须收尾。
    if c._recorder is not None:
        c.stop_recording()


def test_tune_and_gain_become_ordered_timeline_events(tmp_path, ctrl):
    """录制中途改频率/增益必须是独立事件，且时间轴首尾是起止事件。"""
    _attach(ctrl, _FakeBackend())
    result = ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert result.get("recording") is True, result
    assert _wait_until(lambda: ctrl.get_recording_status().get("samples", 0) > 0), \
        "假后端必须真的写出样本，否则档案不会生成"

    assert ctrl.set_frequency(101_100_000.0) is True
    assert ctrl.set_gain(12.5) is True
    result = ctrl.stop_recording()
    assert result["meta_path"], "录制必须真的落盘 meta"
    assert result.get("observation_record"), result.get("observation_error")

    data = json.loads(open(result["observation_record"], encoding="utf-8").read())
    timeline = data["timeline"]
    assert timeline[0]["kind"] == "record_start"
    assert timeline[-1]["kind"] == "record_stop"
    kinds = [e["kind"] for e in timeline]
    assert "tune" in kinds and "gain" in kinds, kinds
    # 起录期间的事件必须夹在中间，而不是被排序挤到两端
    assert kinds.index("tune") not in (0, len(kinds) - 1)

    tune = next(e for e in timeline if e["kind"] == "tune")
    assert tune["detail"]["from_hz"] == pytest.approx(100_000_000.0)
    assert tune["detail"]["to_hz"] == pytest.approx(101_100_000.0)
    gain = next(e for e in timeline if e["kind"] == "gain")
    assert gain["detail"]["to_db"] == pytest.approx(12.5)

    stamps = [e["utc"] for e in timeline]
    assert stamps == sorted(stamps), "时间轴必须按 utc 升序"


def test_note_event_ignored_when_not_recording(tmp_path, ctrl):
    """未录制时记事件必须返回 False，且不污染下一次录制的时间轴。

    否则下一次录制的档案里会躺着一段从未发生的观测过程（假数据）。
    """
    assert ctrl.note_observation_event(
        "tune", {"from_hz": 1.0, "to_hz": 2.0}) is False
    assert ctrl.note_observation_event("   ") is False
    assert ctrl._recording_events == [], "未录制不得留下任何事件"

    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert _wait_until(lambda: ctrl.get_recording_status().get("samples", 0) > 0)
    assert [e["kind"] for e in _events(ctrl)] == ["record_start"], \
        "上一次「未录制」的事件不得混进本轮录制"
    ctrl.stop_recording()


def test_streaming_decode_hit_is_recorded(tmp_path, ctrl, monkeypatch):
    """流式解码命中必须接进时间轴（假的是解码函数，不是产品数据）。"""
    message = {"icao": "abcdef", "callsign": "TEST123", "alt": 32000}
    monkeypatch.setattr(
        SDRController, "_resolve_decode_fn",
        staticmethod(lambda dt: (lambda iq, fs, params: [dict(message)])))

    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert _wait_until(lambda: ctrl.get_recording_status().get("samples", 0) > 0)

    handle = ctrl.start_decoder("adsb")
    try:
        events = _wait_until(
            lambda: (lambda evs: evs if any(
                e["kind"] == "decode_hit" for e in evs) else None)(_events(ctrl)),
            timeout=8.0)
        assert events, "流式解码命中必须写进时间轴"
    finally:
        ctrl.stop_decoder(handle)
        ctrl.stop_recording()

    hits = [e for e in events if e["kind"] == "decode_hit"]
    assert hits[0]["detail"]["count"] >= 1
    assert hits[0]["detail"]["decoder"] == "adsb"
    assert hits[0]["detail"]["first"]["icao"] == "abcdef"


def _archived_timeline(result):
    """读取停止录制后落盘的观测档案时间轴。"""
    assert result.get("observation_record"), result.get("observation_error")
    data = json.loads(open(result["observation_record"], encoding="utf-8").read())
    return data["timeline"]


def test_decode_hits_not_recorded_when_decoder_reports_miss(
        tmp_path, ctrl, monkeypatch):
    """解码器自报「没解出」（rds_present=False）不得记成 decode_hit。

    真机回归：录 rds 12 秒写出 246 条 detail 全为
    ``{"rds_present": false, "reason": "too_short"}`` 的「命中」，
    把时间轴刷爆且全是假事件。
    """
    miss = {"rds_present": False, "reason": "too_short"}
    monkeypatch.setattr(
        SDRController, "_resolve_decode_fn",
        staticmethod(lambda dt: (lambda iq, fs, params: dict(miss))))

    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert _wait_until(lambda: ctrl.get_recording_status().get("samples", 0) > 0)

    handle = ctrl.start_decoder("rds")
    try:
        time.sleep(1.5)
    finally:
        ctrl.stop_decoder(handle)
        result = ctrl.stop_recording()

    hits = [e for e in _archived_timeline(result) if e["kind"] == "decode_hit"]
    assert hits == [], f"未命中不是观测事件，时间轴不得有任何 decode_hit: {hits[:3]}"


def test_decode_hits_are_aggregated_not_flooded(tmp_path, ctrl, monkeypatch):
    """同一解码器的真命中必须聚合：事件数受限，但条数不得丢。"""
    monkeypatch.setattr(
        SDRController, "_resolve_decode_fn",
        staticmethod(lambda dt: (lambda iq, fs, params: [{"icao": "abc"}] * 3)))

    _attach(ctrl, _FakeBackend(block=256))
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert _wait_until(lambda: ctrl.get_recording_status().get("samples", 0) > 0)

    handle = ctrl.start_decoder("adsb")
    try:
        time.sleep(1.2)
    finally:
        ctrl.stop_decoder(handle)
        result = ctrl.stop_recording()

    hits = [e for e in _archived_timeline(result) if e["kind"] == "decode_hit"]
    assert hits, "真命中必须写进时间轴"
    assert len(hits) <= 3, f"1.2 s 内的命中必须聚合，实际 {len(hits)} 条"
    assert sum(e["detail"]["count"] for e in hits) >= 3, "聚合不得丢条数"
    assert all(e["detail"]["decoder"] == "adsb" for e in hits)


def test_pending_decode_hits_flushed_before_stop(tmp_path, ctrl, monkeypatch):
    """stop_recording 前的 flush：最后不到 1 秒的命中不得丢。"""
    monkeypatch.setattr(
        SDRController, "_resolve_decode_fn",
        staticmethod(lambda dt: (lambda iq, fs, params: [{"icao": "abc"}])))

    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert _wait_until(lambda: ctrl.get_recording_status().get("samples", 0) > 0)

    handle = ctrl.start_decoder("adsb")
    try:
        # 首条命中立刻发；随后间隔内的命中留在 pending，等 stop 前 flush。
        assert _wait_until(
            lambda: ctrl._decode_hit_pending.get("adsb", {}).get("pending", 0) > 0,
            timeout=3.0), "间隔内的命中应留在 pending"
    finally:
        ctrl.stop_decoder(handle)
        result = ctrl.stop_recording()

    hits = [e for e in _archived_timeline(result) if e["kind"] == "decode_hit"]
    assert hits, "flush 的命中必须进入归档时间轴"
    assert sum(e["detail"]["count"] for e in hits) >= 2


def test_pending_hits_cleared_when_recording_starts(tmp_path, ctrl):
    """录制开始前解码线程攒下的命中不得算进本次观测。

    否则档案里的第一条 ``decode_hit`` 会多出几条录制外发生的命中——那正是
    「给没发生的观测过程记账」，与无产物不导出同一口径。
    """
    ctrl._decode_hit_pending = {
        "adsb": {"pending": 5, "last_emit": 0.0, "first": {"icao": "old"}}
    }
    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert ctrl._decode_hit_pending == {}, "新一轮录制必须清空未发聚合"
    ctrl.stop_recording()


def test_decode_message_is_hit_contract():
    """命中判定只排除解码器自报的未命中/错误形态。"""
    assert _decode_message_is_hit({}) is False
    assert _decode_message_is_hit("x") is False
    assert _decode_message_is_hit({"error": "boom"}) is False
    assert _decode_message_is_hit({"success": False}) is False
    assert _decode_message_is_hit({"rds_present": False}) is False
    assert _decode_message_is_hit({"rds_present": True, "pi_hex": "1234"}) is True
    assert _decode_message_is_hit({"icao": "abc"}) is True


def test_read_gap_is_recorded_with_reason(tmp_path, ctrl):
    """设备空读必须如实记成 read_gap（持续时长/次数/后端原因）。"""
    _attach(ctrl, _FakeBackend(empty_reads=2))
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))

    gap = _wait_until(
        lambda: next((e for e in _events(ctrl) if e["kind"] == "read_gap"), None),
        timeout=8.0)
    ctrl.stop_recording()
    assert gap is not None, "连续空读后恢复取样本时必须记录一次 read_gap"
    assert gap["detail"]["empty_reads"] >= 1
    assert gap["detail"]["duration_s"] >= 0
    assert gap["detail"]["reason"]["code"] == "read_timeout", \
        "后端给出的原因必须原样带上，不能编一个"


def test_sigmf_meta_carries_tune_annotation(tmp_path, ctrl):
    """SigMF annotations 的样本偏移必须落在真实样本范围内。"""
    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert _wait_until(lambda: ctrl.get_recording_status().get("samples", 0) > 0)
    assert ctrl.set_frequency(102_400_000.0) is True
    result = ctrl.stop_recording()

    meta = json.loads(open(result["meta_path"], encoding="utf-8").read())
    total = meta["global"]["core:total_num_samples"]
    assert total > 0
    annotations = meta["annotations"]
    assert annotations, "录制中有事件就必须写 annotations"

    tune = next(a for a in annotations if a["core:label"] == "tune")
    assert isinstance(tune["core:sample_start"], int)
    assert 0 <= tune["core:sample_start"] <= total
    assert tune["mbdsdr:kind"] == "tune"
    assert json.loads(tune["core:comment"])["to_hz"] == 102_400_000.0


def test_timeline_truncation_happens_once(tmp_path, ctrl, monkeypatch):
    """超限只追加一条 timeline_truncated：既不静默丢光，也不无限增长。"""
    monkeypatch.setattr(controller_mod, "MAX_TIMELINE_EVENTS", 3)
    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    for i in range(6):
        ctrl.note_observation_event(
            "tune", {"from_hz": float(i), "to_hz": float(i + 1)})

    events = _events(ctrl)
    ctrl.stop_recording()
    truncated = [e for e in events if e["kind"] == "timeline_truncated"]
    assert len(truncated) == 1, "只允许一条 timeline_truncated"
    assert truncated[0]["detail"]["max_events"] == 3


def test_events_carry_sample_index_and_annotations_use_it(tmp_path, ctrl):
    """事件落点用真实样本索引，SigMF annotations 直接用它。

    回归背景（2026-11-20 真机）：录制期间另起流式解码线程会分流设备读，
    12 s 只写进 3.5 s 的样本；此时「墙上时钟 × 采样率」算出的偏移会全部
    撞到文件末尾，回放时对不上。样本索引才是能 seek 的量。
    """
    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    assert _wait_until(lambda: ctrl.get_recording_status().get("samples", 0) > 0)
    assert ctrl.set_frequency(101_100_000.0) is True
    result = ctrl.stop_recording()

    timeline = json.loads(
        open(result["observation_record"], encoding="utf-8").read())["timeline"]
    offsets = [e.get("samples") for e in timeline]
    assert all(isinstance(o, int) for o in offsets), offsets
    assert offsets[0] == 0, "record_start 必须锚在文件第 0 个样本"
    assert offsets == sorted(offsets), "样本索引必须单调不减"
    assert offsets[-1] == result["samples"], "record_stop 必须锚在文件末尾"

    meta = json.loads(open(result["meta_path"], encoding="utf-8").read())
    total = meta["global"]["core:total_num_samples"]
    tune = next(a for a in meta["annotations"] if a["core:label"] == "tune")
    tune_event = next(e for e in timeline if e["kind"] == "tune")
    assert tune["core:sample_start"] == min(tune_event["samples"], total), \
        "annotation 偏移必须与事件样本索引一致"


def test_decoder_shared_samples_are_disclosed(tmp_path, ctrl, monkeypatch):
    """录制期间被流式解码分走的样本必须在档案里写明（不是静默丢包）。

    回归背景（2026-11-20 真机）：控制器起的流式解码与录制泵共用同一设备读流，
    12 s 录制只写进 3.5 s 样本。档案若不写这条，用户会以为是设备丢包。
    """
    monkeypatch.setattr(
        SDRController, "_resolve_decode_fn",
        staticmethod(lambda dt: (lambda iq, fs, params: [{"icao": "abc"}])))

    _attach(ctrl, _FakeBackend())
    ctrl.start_recording(str(tmp_path / "cap.sigmf-data"))
    handle = ctrl.start_decoder("adsb")
    try:
        assert _wait_until(
            lambda: ctrl._decoder_samples_read > 0, timeout=5.0), \
            "解码线程读走的样本必须被计数"
    finally:
        ctrl.stop_decoder(handle)
        result = ctrl.stop_recording()

    assert result.get("decoder_shared_samples", 0) > 0
    data = json.loads(open(result["observation_record"], encoding="utf-8").read())
    assert data["result"]["decoder_shared_samples"] == \
        result["decoder_shared_samples"]
    assert any("分流" in n for n in data["notes"]), data["notes"]


def test_make_event_and_normalize_timeline_units():
    """乱序输入被排序、非 dict 条目被丢弃、缺字段的条目不修补。"""
    late = make_event("tune", {"x": 1}, utc="2026-11-20T00:00:02+00:00")
    early = make_event("gain", {"y": 2}, utc="2026-11-20T00:00:01+00:00")
    incomplete = {"kind": "read_gap"}          # 缺 utc：不补，交给校验报错

    out = normalize_timeline([late, "junk", None, early, incomplete])
    assert [e["kind"] for e in out] == ["read_gap", "gain", "tune"]
    assert all(isinstance(e, dict) for e in out)
    assert out[0] == incomplete, "缺字段的条目必须原样保留"

    auto = make_event("decode_hit")
    assert auto["utc"] and auto["detail"] == {}


def test_validate_for_export_reports_bad_timeline(tmp_path):
    """坏时间轴必须阻塞导出，且错误原文点名是哪一条。"""
    rec = ObservationRecord(
        observation_id="obs-x",
        products=[{"path": "a.sigmf-meta"}],
        evidence="real_hw",
        timeline=["not-a-dict", {"kind": "tune"}, {"utc": "2026-11-20T00:00:00Z"}],
    )
    errs = rec.validate_for_export()
    assert any("时间轴事件 #0 不是对象" in e for e in errs)
    assert any("时间轴事件 #1 缺少 utc" in e for e in errs)
    assert any("时间轴事件 #2 缺少 kind" in e for e in errs)

    out = tmp_path / "obs.json"
    ok, err = rec.write_json(str(out))
    assert ok is False and "时间轴事件" in err
    assert not out.exists(), "校验失败时不得留下档案文件"
