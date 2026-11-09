# SPDX-License-Identifier: MIT
"""cu8 回放真值：默认录制格式不许被静默按 cf32 解读。

`sdr_backend` 的存盘格式默认就是 cu8（rtl_sdr 原生 unsigned8 交错，
2 字节/样本、中心 127.5），但 `baseband_io.load_iq` 旧式白名单只有
wav/cs16/cf32，`.cu8` 与 sidecar `format:"cu8"` 一律回落 cf32（8 字节/样本）
→ 文件被读成 1/4 样本且数值全乱，还不报错。主窗口「录完回放」必然中招。
"""
import json
import os
import sys

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from mbdsdr_ai import baseband_io as bb  # noqa: E402


def _write_cu8(path, z):
    """与 sdr_backend 录制器完全同式：z*127.5+127.5 → uint8 交错。"""
    arr = np.asarray(z, dtype=np.complex128)
    ci = np.clip(arr.real, -1.0, 1.0) * 127.5 + 127.5
    cq = np.clip(arr.imag, -1.0, 1.0) * 127.5 + 127.5
    iq = np.column_stack([ci, cq]).astype(np.uint8)
    iq.tofile(str(path))
    return iq


class TestCu8Decode:
    def test_sidecar_format_cu8_roundtrip(self, tmp_path):
        z = np.array([0.0 + 0.0j, 1.0 + 0.0j, -1.0 + 0.0j,
                      0.0 + 1.0j, 0.0 - 1.0j, 0.5 - 0.25j],
                     dtype=np.complex64)
        p = tmp_path / "rec.bin"
        _write_cu8(p, z)
        (tmp_path / "rec.bin.json").write_text(json.dumps({
            "format": "cu8", "sample_rate": 2400000.0,
            "center_freq_hz": 1090000000.0, "gain_db": 20.0}),
            encoding="utf-8")

        rec = bb.load_iq(str(p))
        assert "error" not in rec, rec
        assert rec["samples"] == len(z), "cu8 是 2 字节/样本，不是 8"
        assert rec["file_samples"] == len(z)
        np.testing.assert_allclose(rec["iq"], z, atol=2 / 127.5)
        assert rec["iq"].dtype == np.complex64
        assert rec["sample_rate"] == 2400000.0
        assert rec["sample_rate_assumed"] is False
        assert rec["center_freq_hz"] == 1090000000.0
        assert rec["center_freq_known"] is True

    def test_extension_alone_is_enough(self, tmp_path):
        p = tmp_path / "live.cu8"
        _write_cu8(p, [0.25 + 0.5j, -0.5 - 0.25j])
        rec = bb.load_iq(str(p))
        assert "error" not in rec
        assert rec["samples"] == 2
        assert rec["sample_rate_assumed"] is True, "无 sidecar 必须标明速率是假设值"
        assert rec["sample_rate_source"] == "default"
        assert rec["metadata_missing"] is True
        np.testing.assert_allclose(rec["iq"], [0.25 + 0.5j, -0.5 - 0.25j],
                                   atol=2 / 127.5)

    @pytest.mark.parametrize("ext", [".u8", ".bin"])
    def test_other_unsigned_extensions(self, tmp_path, ext):
        p = tmp_path / ("sdr" + ext)
        _write_cu8(p, [0.1 + 0.2j])
        assert bb.load_iq(str(p))["samples"] == 1

    def test_chunked_read_is_identical(self, tmp_path):
        z = np.exp(1j * np.linspace(0, 6.0, 137)).astype(np.complex64) * 0.8
        p = tmp_path / "chunk.cu8"
        _write_cu8(p, z)
        whole = bb.load_iq(str(p))["iq"]
        chunked = bb.load_iq(str(p), chunk_samples=7)["iq"]
        np.testing.assert_array_equal(whole, chunked)

    def test_max_samples_truncation_uses_real_sample_count(self, tmp_path):
        p = tmp_path / "limit.cu8"
        _write_cu8(p, [0.0j] * 100)
        rec = bb.load_iq(str(p), max_samples=10)
        assert rec["samples"] == 10 and rec["file_samples"] == 100
        assert rec["truncated"] is True and rec["truncation_reason"]


class TestUnknownFormatHonesty:
    def test_unknown_sidecar_format_is_refused_not_silently_cf32(self, tmp_path):
        p = tmp_path / "weird.iq"
        p.write_bytes(b"\x00" * 64)
        (tmp_path / "weird.iq.json").write_text(
            json.dumps({"format": "fancy_format", "sample_rate": 1e6}),
            encoding="utf-8")
        rec = bb.load_iq(str(p))
        assert "error" in rec
        assert "fancy_format" in rec["error"]
        assert "iq" not in rec, "拒绝时绝不返回被误读的 IQ 数组"

    def test_unknown_extension_is_refused(self, tmp_path):
        p = tmp_path / "capture.mystery"
        p.write_bytes(b"\x00" * 64)
        rec = bb.load_iq(str(p))
        assert "error" in rec and "不支持的 IQ 格式" in rec["error"]

    def test_counterproof_old_fallback_would_misread_cu8(self, tmp_path):
        """反证：按 cf32 解读同一份 cu8 字节，样本数与数值都不同。"""
        p = tmp_path / "proof.cu8"
        _write_cu8(p, [0.5 + 0.5j] * 8)      # 16 字节 = 8 个 cu8 样本
        rec = bb.load_iq(str(p))
        assert rec["samples"] == 8
        old_samples = os.path.getsize(p) // 8   # cf32: 8 字节/复样本
        assert old_samples == 2, "旧式按 8 字节/样本只会得到 2 个复样本"
        old_vals = np.fromfile(str(p), dtype=np.float32)
        assert old_vals.size == 4 and not np.allclose(
            old_vals[:2], [0.5, 0.5], atol=1e-3)


class TestSigmfMetaFormat:
    """SigMF 录制（``.sigmf-data`` + ``.sigmf-meta``）必须能被 load_iq 读回。

    录制器写的是 SigMF 双文件、**不写** ``.json`` sidecar，旧 ``load_iq``
    只认 sidecar 与扩展名白名单，``.sigmf-data`` 一律判定为未知格式 ——
    自己录出来的文件自己放不了。2026-11-09 真机七环 R4 实测报
    ``不支持的 IQ 格式 None``。类型与速率按 SigMF 规范从
    ``global.core:datatype`` / ``core:sample_rate`` 取。
    """

    def _write_sigmf(self, tmp_path, z, datatype="cf32_le", name="rec"):
        data = tmp_path / f"{name}.sigmf-data"
        arr = np.asarray(z, dtype=np.complex64)
        np.column_stack([arr.real, arr.imag]).astype(np.float32).tofile(str(data))
        (tmp_path / f"{name}.sigmf-meta").write_text(json.dumps({
            "global": {
                "core:datatype": datatype,
                "core:sample_rate": 2400000.0,
                "core:frequency": 93222656.25,
                "core:total_num_samples": int(arr.size),
            },
            "captures": [{"core:sample_start": 0}],
            "annotations": [],
        }), encoding="utf-8")
        return data

    def test_cf32_le_sigmf_data_is_loadable(self, tmp_path):
        z = np.array([0.5 + 0.25j, -0.5 - 0.25j, 1.0 + 0j, 0j, 0.125 - 0.75j],
                     dtype=np.complex64)
        p = self._write_sigmf(tmp_path, z)
        rec = bb.load_iq(str(p))
        assert "error" not in rec, rec.get("error")
        assert rec["samples"] == z.size
        assert rec["sample_rate"] == 2400000.0
        assert rec["sample_rate_source"] == "sigmf-meta"
        assert rec["sample_rate_assumed"] is False
        assert np.allclose(rec["iq"], z, atol=1e-6)

    def test_cu8_sigmf_datatype_maps_to_cu8(self, tmp_path):
        """cu8 的 SigMF 写法是 ``cu8``（无 _le 后缀），必须同样认。"""
        p = tmp_path / "cu8rec.sigmf-data"
        _write_cu8(p, [0.5 + 0.5j] * 8)
        (tmp_path / "cu8rec.sigmf-meta").write_text(json.dumps({
            "global": {"core:datatype": "cu8", "core:sample_rate": 2048000.0},
        }), encoding="utf-8")
        rec = bb.load_iq(str(p))
        assert "error" not in rec, rec.get("error")
        assert rec["samples"] == 8
        assert np.allclose(rec["iq"], [0.5 + 0.5j] * 8, atol=1e-2)

    def test_unknown_datatype_is_refused_with_reason(self, tmp_path):
        """反证：大端 datatype 不静默凑，拒绝时说出读到的 datatype。"""
        p = self._write_sigmf(tmp_path, np.array([1 + 0j], dtype=np.complex64),
                              datatype="ci16_be")
        rec = bb.load_iq(str(p))
        assert "error" in rec and "不支持的 IQ 格式" in rec["error"]
        assert "ci16_be" in rec["error"], "拒绝时必须说出读到的 datatype"
        assert "iq" not in rec, "拒绝时绝不返回按错字节序读出的 IQ"

    def test_missing_sigmf_meta_still_refuses(self, tmp_path):
        """反证：只有 .sigmf-data 没有 meta 时不许猜格式。"""
        p = tmp_path / "orphan.sigmf-data"
        p.write_bytes(b"\x00" * 32)
        rec = bb.load_iq(str(p))
        assert "error" in rec and "不支持的 IQ 格式" in rec["error"]
