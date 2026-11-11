# SPDX-License-Identifier: MIT
"""LRPT 帧首约定：探针分类与失败原因诚实性。

背景（2026-11-06 查出的假接线）：
`weather_panel.DecodeWorker._run_meteor` 把 `meteor_sat.demodulate_lrpt`
产出的 CADU 直接喂 `compose_visible_image`。前者帧首是 CCSDS/SatDump ASM，
后者默认只认 `lrpc_build_frame` 的 3B `1DFCDC` 简化帧 —— 每一帧都被判
"无同步"，图像层返回 None，旧文案却一律说成「段不全或数据损坏」，
把**格式错**报成**信号差**。用户会去调天线，永远调不出来。

本文件钉住两件事：
  1. `probe_cadu_sync` 能把仓内四套同步字各自认出来（含反证）；
  2. `_run_meteor` 对"约定未对齐"和"约定对齐但段不全"给**不同**的错误原因。
"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "desktop")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mbdsdr_ai import meteor_sat as ms  # noqa: E402


def _bits_of(hex_bytes: str) -> np.ndarray:
    """把字节切成 0/1 位流（与 extract_cadu 的产出形态一致）。"""
    raw = bytes.fromhex(hex_bytes)
    return np.array([int(b) for b in "".join(f"{x:08b}" for x in raw)],
                    dtype=np.int8)


def _frames(kind: str, n: int = 3):
    if kind == "lrpc_simplified_3b":
        return [ms.lrpc_build_frame(1, 64, b"payload") for _ in range(n)]
    if kind == "satdump_cadu_4b":
        return [bytes.fromhex("1dcffc1d") + b"x" * 20 for _ in range(n)]
    if kind == "ccsds_asm_4b":
        return [bytes.fromhex("1acffc1d") + b"x" * 20 for _ in range(n)]
    if kind == "meteor_sat_asm_8b":
        return [_bits_of("cbc465e232f11978") for _ in range(n)]
    if kind == "unknown":
        return [b"zzzz" for _ in range(n)]
    raise ValueError(kind)


# ---------------------------------------------------------------------------
class TestProbe:
    @pytest.mark.parametrize("kind", [
        "lrpc_simplified_3b", "satdump_cadu_4b", "ccsds_asm_4b",
        "meteor_sat_asm_8b"])
    def test_each_convention_is_recognised(self, kind):
        r = ms.probe_cadu_sync(_frames(kind))
        assert r["best"] == kind, r
        assert r["counts"][kind] == 3, r
        assert r["n_frames"] == 3 and r["n_unknown"] == 0
        assert r["compose_compatible"] == (kind == "lrpc_simplified_3b")

    def test_bitstream_and_bytes_agree(self):
        """位流与字节两种上游形态必须给出同一结论（探针不造第二套解释）。"""
        a = ms.probe_cadu_sync(_frames("satdump_cadu_4b"))
        b = ms.probe_cadu_sync(
            [_bits_of("1dcffc1d78787878") for _ in range(3)])
        assert a["best"] == b["best"] == "satdump_cadu_4b"
        assert a["head_hex"] == b["head_hex"]

    def test_unknown_and_empty_are_honest(self):
        r = ms.probe_cadu_sync(_frames("unknown"))
        assert r["best"] is None and not r["compose_compatible"]
        assert r["n_unknown"] == 3 and r["head_hex"]
        empty = ms.probe_cadu_sync([])
        assert empty["n_frames"] == 0 and empty["best"] is None

    def test_mixed_stream_picks_majority(self):
        mixed = _frames("lrpc_simplified_3b", 2) + _frames("ccsds_asm_4b", 1)
        r = ms.probe_cadu_sync(mixed)
        assert r["best"] == "lrpc_simplified_3b"
        assert r["counts"]["ccsds_asm_4b"] == 1

    def test_counterproof_probe_actually_bites(self, monkeypatch):
        """把 3B 同步字换成别的 → 同一批简化帧必须变成'未知'。"""
        monkeypatch.setitem(ms.LRPT_SYNC_CONVENTIONS,
                            "lrpc_simplified_3b", b"\x00\x00\x00")
        r = ms.probe_cadu_sync(_frames("lrpc_simplified_3b"))
        assert r["best"] is None and not r["compose_compatible"]


# ---------------------------------------------------------------------------
class TestWorkerDiagnosis:
    def _run(self, monkeypatch, tmp_path, cadus, compose_result=None,
             real_result=None):
        import weather_panel as wp
        from mbdsdr_ai import lrpt_pipeline as lp

        iq_file = tmp_path / "fake.iq"
        iq_file.write_bytes(b"")
        calls = {"compose": 0, "real": 0}

        monkeypatch.setattr(wp, "_load_complex64",
                            lambda _p: np.zeros(16, dtype=np.complex64))
        monkeypatch.setattr(ms, "demodulate_lrpt",
                            lambda *_a, **_k: cadus)

        def _real(*_a, **_k):
            calls["real"] += 1
            if real_result is not None:
                return real_result
            return lp.RecordingImageResult(stage="sync",
                                           error="帧同步未锁（测试桩）")

        monkeypatch.setattr(lp, "decode_recording_to_image", _real)

        def _compose(items, **_kw):
            calls["compose"] += 1
            return compose_result

        monkeypatch.setattr(ms, "compose_visible_image", _compose)
        worker = wp.DecodeWorker({
            "family": "meteor", "iq_file": str(iq_file),
            "sample_rate": 1e6, "out_png": str(tmp_path / "out.png"),
            "satellite": "METEOR-M2 (LRPT)",
        })
        results, progress = [], []
        worker.finished.connect(results.append)
        worker.progress.connect(progress.append)
        worker._run_meteor()
        return results, progress, calls

    def test_unaligned_frames_report_convention_not_signal_quality(
            self, monkeypatch, tmp_path):
        results, _progress, calls = self._run(
            monkeypatch, tmp_path, _frames("meteor_sat_asm_8b"))
        assert len(results) == 1
        err = results[0]["error"]
        assert results[0]["frames"] == 3
        # 2026-11-11（P1-23）：真录链先行，旧链只作回退；两条链的原因都要在
        assert "帧首" in err and "1DFCDC" in err
        assert "真录链" in err and "帧同步未锁" in err
        assert calls["real"] == 1, "必须先走真录链（lrpt_pipeline）"
        assert "段不全或数据损坏" not in err, err
        # 关键：约定不对齐时根本不该进图像重组器，免得把锅甩给它
        assert calls["compose"] == 0

    def test_aligned_but_incomplete_still_reports_segments(
            self, monkeypatch, tmp_path):
        results, _progress, calls = self._run(
            monkeypatch, tmp_path, _frames("lrpc_simplified_3b"),
            compose_result=None)
        assert len(results) == 1
        assert calls["compose"] == 1
        assert "段重组/熵解码" in results[0]["error"]
        assert "not_verified" not in results[0]["error"]

    def test_no_cadu_is_its_own_reason(self, monkeypatch, tmp_path):
        results, _progress, calls = self._run(monkeypatch, tmp_path, [])
        assert len(results) == 1
        assert "未解出任何 LRPT CADU 帧" in results[0]["error"]
        assert calls["compose"] == 0
