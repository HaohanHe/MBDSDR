# SPDX-License-Identifier: MIT
"""W100 读侧：SigMF captures[]/annotations[] 多段频点回放契约钉扎。

判据来源：docs/research/2026-11-11-roadmap.md W100（读侧+模型侧，可先行）：

* 录制中每次调谐追加一条 captures[]（core:sample_start + core:frequency）——
  模型侧 build_sigmf_meta / SigMFRecordingSession 接受多段；写侧挂钩在
  main_window 串行链（W99 之后），本批不做；
* load_iq 能按 capture 段取正确频点回放（frequency_at_sample 边界钉扎）；
* 无调谐时 captures 仍只有一条、不自造标注（默认行为不变）；
* 反证：把 build_sigmf_meta 改回无视 captures 参数 → 多段用例必须红。

读侧降级口径：captures 段坏（非单调/缺 sample_start）→ 整段省略 + warning，
global 仍可读（不静默、不崩溃、不编造频点）。第三方 SigMF 文件 captures
可缺 frequency（规范允许）→ 该段 frequency=None，frequency_at_sample 如实
返回 None，不拿 global 频率顶替。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mbdsdr_ai.baseband_io import frequency_at_sample, load_iq  # noqa: E402
from mbdsdr_ai.recording_meta import (  # noqa: E402
    SigMFRecordingSession, build_sigmf_meta, read_sigmf_meta,
    write_sigmf_meta)

SR = 240_000.0
F1 = 100_000_000.0
F2 = 145_000_000.0
N = 48_000  # 0.2 s @ 240k


def _cap(start, freq):
    return {"core:sample_start": start, "core:frequency": freq}


def _write_sigmf_recording(tmp_path, *, captures=None, annotations=None,
                           meta_extra=None):
    """造一份真实 cf32 SigMF 录制（数据+meta），返回 data 路径。

    走 build_sigmf_meta（写侧校验生效）。读侧降级用例（坏段/缺频点）
    必须绕开写侧校验，用 _write_raw_meta 直接落盘。
    """
    t = np.arange(N) / SR
    iq = (0.5 * np.exp(2j * np.pi * 1000.0 * t)).astype(np.complex64)
    data = tmp_path / "rec.sigmf-data"
    data.write_bytes(iq.tobytes())
    meta = build_sigmf_meta(sample_rate=SR, center_freq_hz=F1,
                            datatype="cf32_le", num_samples=N,
                            captures=captures, annotations=annotations)
    if meta_extra:
        meta.update(meta_extra)
    mp = tmp_path / "rec.sigmf-meta"
    mp.write_text(json.dumps(meta), encoding="utf-8")
    return str(data)


def _write_raw_meta(tmp_path, meta):
    """直接落盘任意 meta（模拟第三方/损坏文件，绕开写侧校验）。"""
    t = np.arange(N) / SR
    iq = (0.5 * np.exp(2j * np.pi * 1000.0 * t)).astype(np.complex64)
    data = tmp_path / "rec.sigmf-data"
    data.write_bytes(iq.tobytes())
    mp = tmp_path / "rec.sigmf-meta"
    mp.write_text(json.dumps(meta), encoding="utf-8")
    return str(data)


def _base_meta():
    return {"global": {"core:datatype": "cf32_le", "core:sample_rate": SR,
                       "core:version": "1.0.0", "core:frequency": F1}}


# ---------------------------------------------------------------------------
# 1. 模型侧：build_sigmf_meta 多段 captures / annotations
# ---------------------------------------------------------------------------
class TestBuildSigmfCaptures:
    def test_default_single_capture_and_empty_annotations(self):
        m = build_sigmf_meta(sample_rate=SR, center_freq_hz=F1)
        assert len(m["captures"]) == 1
        c = m["captures"][0]
        assert c["core:sample_start"] == 0
        assert c["core:frequency"] == F1
        assert m["annotations"] == [], "无事件不得自造标注"

    def test_multi_captures_preserved_in_order(self):
        caps = [_cap(0, F1), _cap(24_000, F2)]
        m = build_sigmf_meta(sample_rate=SR, center_freq_hz=F1, captures=caps)
        assert len(m["captures"]) == 2
        assert m["captures"][0]["core:frequency"] == F1
        assert m["captures"][1]["core:sample_start"] == 24_000
        assert m["captures"][1]["core:frequency"] == F2
        # global 频率不被多段改写（仍是录制起始频点）
        assert m["global"]["core:frequency"] == F1

    @pytest.mark.parametrize("bad", [
        [_cap(100, F1), _cap(50, F2)],          # 非单调
        [_cap(-1, F1)],                          # 负起点
        [{"core:sample_start": 0}],              # 缺 frequency（写侧必须知道）
        [_cap(0, float("nan"))],                 # 非有限频率
        [],                                      # 空列表（给参数就必须有效）
    ])
    def test_invalid_captures_raise_not_silent(self, bad):
        with pytest.raises(ValueError):
            build_sigmf_meta(sample_rate=SR, center_freq_hz=F1, captures=bad)

    def test_annotations_passthrough(self):
        ann = [{"core:sample_start": 0, "core:sample_count": 100,
                "core:description": "tone burst"}]
        m = build_sigmf_meta(sample_rate=SR, center_freq_hz=F1, annotations=ann)
        assert m["annotations"] == ann


class TestSessionCaptures:
    def test_session_add_capture_and_annotation(self, tmp_path):
        p = str(tmp_path / "rec.cf32")
        with SigMFRecordingSession(p, sample_rate=SR, center_freq_hz=F1) as sess:
            sess.add_capture(24_000, F2)
            sess.add_annotation(0, 100, description="tone burst")
            sess.update(N)
        m = read_sigmf_meta(p)
        assert len(m["captures"]) == 2
        assert m["captures"][1]["core:frequency"] == F2
        assert len(m["annotations"]) == 1
        assert m["annotations"][0]["core:sample_count"] == 100

    def test_session_without_add_capture_keeps_single(self, tmp_path):
        p = str(tmp_path / "rec.cf32")
        with SigMFRecordingSession(p, sample_rate=SR, center_freq_hz=F1) as sess:
            sess.update(N)
        m = read_sigmf_meta(p)
        assert len(m["captures"]) == 1
        assert m["annotations"] == []


# ---------------------------------------------------------------------------
# 2. 读侧：load_iq 暴露 captures + frequency_at_sample 边界
# ---------------------------------------------------------------------------
class TestLoadIqCaptures:
    def test_multi_capture_roundtrip(self, tmp_path):
        path = _write_sigmf_recording(
            tmp_path, captures=[_cap(0, F1), _cap(24_000, F2)])
        r = load_iq(path)
        assert "error" not in r, r.get("error")
        assert r["samples"] == N
        caps = r["captures"]
        assert len(caps) == 2
        assert caps[0]["sample_start"] == 0 and caps[0]["frequency"] == F1
        assert caps[1]["sample_start"] == 24_000 and caps[1]["frequency"] == F2

    def test_single_capture_default_unchanged(self, tmp_path):
        path = _write_sigmf_recording(tmp_path)
        r = load_iq(path)
        assert "error" not in r
        assert len(r["captures"]) == 1
        assert r["captures"][0]["frequency"] == F1

    def test_annotations_exposed(self, tmp_path):
        ann = [{"core:sample_start": 0, "core:sample_count": 100,
                "core:description": "tone burst"}]
        path = _write_sigmf_recording(tmp_path, annotations=ann)
        r = load_iq(path)
        assert r["annotations"][0]["sample_start"] == 0
        assert r["annotations"][0]["description"] == "tone burst"

    def test_malformed_captures_degrade_to_global_only(self, tmp_path):
        # 非单调 captures：整段省略，global 仍可读，IQ 不丢。
        # 第三方/损坏文件绕开写侧校验，直接落盘。
        meta = _base_meta()
        meta["captures"] = [_cap(24_000, F2), _cap(0, F1)]
        path = _write_raw_meta(tmp_path, meta)
        r = load_iq(path)
        assert "error" not in r
        assert r["samples"] == N
        assert not r.get("captures"), "坏段不得冒充有效段表"

    def test_old_meta_without_captures_still_loads(self, tmp_path):
        path = _write_sigmf_recording(tmp_path)
        mp = path.replace(".sigmf-data", ".sigmf-meta")
        m = json.loads(open(mp, encoding="utf-8").read())
        del m["captures"]
        del m["annotations"]
        open(mp, "w", encoding="utf-8").write(json.dumps(m))
        r = load_iq(path)
        assert "error" not in r
        assert r["samples"] == N
        assert not r.get("captures")

    def test_capture_without_frequency_is_none_not_global(self, tmp_path):
        # 第三方 SigMF：captures 可缺 frequency（规范允许）→ 如实 None，
        # 不拿 global 频率顶替。绕开写侧校验直接落盘。
        meta = _base_meta()
        meta["captures"] = [{"core:sample_start": 0}]
        path = _write_raw_meta(tmp_path, meta)
        r = load_iq(path)
        assert "error" not in r
        assert r["captures"][0]["frequency"] is None


class TestFrequencyAtSample:
    CAPS = [{"sample_start": 0, "frequency": F1},
            {"sample_start": 24_000, "frequency": F2}]

    def test_boundaries(self):
        assert frequency_at_sample(self.CAPS, 0) == F1
        assert frequency_at_sample(self.CAPS, 23_999) == F1
        assert frequency_at_sample(self.CAPS, 24_000) == F2   # 边界归新段
        assert frequency_at_sample(self.CAPS, 999_999) == F2

    def test_empty_and_before_first_are_none(self):
        assert frequency_at_sample([], 0) is None
        caps = [{"sample_start": 100, "frequency": F1}]
        assert frequency_at_sample(caps, 50) is None, "首段之前不得编造频点"

    def test_missing_frequency_is_none(self):
        caps = [{"sample_start": 0, "frequency": None}]
        assert frequency_at_sample(caps, 0) is None