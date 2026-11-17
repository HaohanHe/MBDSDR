# SPDX-License-Identifier: MIT
"""IQ 清单 → SigMF 元数据：钉住「不猜、不改数据、不冒充合规」。

此前仓库能把清单校验成「结构上能开训」，但产物仍是私有 JSONL。SigMF 是
社区通用格式（克隆项目 ``reference/sigmf`` 里就有 v1.2.6 schema），本文件
钉的是导出这一步的三条底线：

1. ``core:datatype`` 是必填——清单没写就**不导出**，不按扩展名猜；
2. 采样率/中心频率/license 只从清单取值，不补默认、不编；
3. 只写 ``<stem>.sigmf-meta``，数据文件一个字节都不动；数据名不是
   ``<stem>.sigmf-data`` 时用 ``core:dataset`` 如实声明成 NCD。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from mbdsdr_ai import iq_sample_manifest as man  # noqa: E402
from mbdsdr_ai import iq_sigmf as sig  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "export_sigmf.py"
IMPORT_SCRIPT = ROOT / "scripts" / "import_sigmf.py"
REFERENCE_SCHEMA = ROOT.parent / "reference" / "sigmf" / "sigmf-schema.json"
REAL_META = ROOT / "tests" / "fixtures" / "real_fm_88m1" / "capture.sigmf-meta"


def _sample(sample_id="s1", **over):
    item = {
        "sample_id": sample_id,
        "label": "fm",
        "sample_rate": 1024000,
        "center_freq": 88100000,
        "iq_path": "samples/s1.cu8",
        "license": "CC-BY-4.0",
        "datatype": "cu8",
    }
    item.update(over)
    return item


def _strict_loads(text):
    def _reject(value):
        raise AssertionError(f"不是合法 JSON 常量：{value}")
    return json.loads(text, parse_constant=_reject)


class TestBuildMeta:
    def test_has_sigmf_required_structure(self):
        meta = sig.build_meta(_sample())
        for key in sig.SIGMF_REQUIRED_TOP:
            assert key in meta
        for key in sig.SIGMF_REQUIRED_GLOBAL:
            assert key in meta["global"]
        for key in sig.SIGMF_REQUIRED_CAPTURE:
            assert key in meta["captures"][0]
        assert meta["annotations"] == []
        assert meta["global"]["core:version"] == sig.SIGMF_VERSION

    def test_values_come_from_manifest_only(self):
        meta = sig.build_meta(_sample(sample_rate=250000, center_freq=145500000,
                                      datatype="cf32_le"))
        assert meta["global"]["core:datatype"] == "cf32_le"
        assert meta["global"]["core:sample_rate"] == 250000.0
        assert meta["captures"][0]["core:frequency"] == 145500000.0
        assert meta["captures"][0]["core:sample_start"] == 0

    @pytest.mark.parametrize("datatype", ["cu8", "cf32_le", "ci16_be",
                                          "rf32", "ri16_le"])
    def test_accepts_schema_datatypes(self, datatype):
        assert sig.build_meta(_sample(datatype=datatype))[
            "global"]["core:datatype"] == datatype

    @pytest.mark.parametrize("datatype", ["cu8x", "cf33", "8bit", "cf32-le"])
    def test_rejects_non_sigmf_datatype(self, datatype):
        with pytest.raises(ValueError):
            sig.build_meta(_sample(datatype=datatype))

    def test_datatype_whitespace_is_trimmed_not_guessed(self):
        """首尾空白照常裁掉；但裁完仍是非法值就拒绝。"""
        assert sig.build_meta(_sample(datatype=" cf32_le "))[
            "global"]["core:datatype"] == "cf32_le"

    def test_missing_datatype_is_not_guessed_from_extension(self):
        item = _sample()
        del item["datatype"]
        with pytest.raises(ValueError) as exc:
            sig.build_meta(item)
        assert "未声明 datatype" in str(exc.value)

    def test_license_short_id_does_not_pose_as_core_license(self):
        meta = sig.build_meta(_sample(license="CC-BY-4.0"))
        assert "core:license" not in meta["global"]
        assert meta["global"]["mbdsdr:license"] == "CC-BY-4.0"

    def test_license_url_goes_to_core_license(self):
        url = "https://creativecommons.org/licenses/by/4.0/"
        meta = sig.build_meta(_sample(license=url))
        assert meta["global"]["core:license"] == url
        assert "mbdsdr:license" not in meta["global"]

    def test_original_data_name_is_declared_as_ncd(self):
        meta = sig.build_meta(_sample(iq_path="samples/s1.cu8"))
        assert meta["global"]["core:dataset"] == "s1.cu8"

    def test_compliant_dataset_name_omits_core_dataset(self):
        meta = sig.build_meta(_sample(iq_path="samples/s1.sigmf-data"))
        assert "core:dataset" not in meta["global"]

    @pytest.mark.skipif(not REFERENCE_SCHEMA.is_file(),
                        reason="克隆的 sigmf schema 不在本机")
    def test_output_satisfies_cloned_schema_required_keys(self):
        """以 reference/sigmf 的真实 schema 做结构断言（不依赖 jsonschema）。"""
        schema = json.loads(REFERENCE_SCHEMA.read_text(encoding="utf-8"))
        meta = sig.build_meta(_sample())

        assert set(schema["required"]) <= set(meta)
        assert set(schema["properties"]["global"]["required"]) <= set(
            meta["global"])
        assert set(schema["properties"]["captures"]["items"]["required"]) \
            <= set(meta["captures"][0])
        pattern = re.compile(
            schema["properties"]["global"]["properties"]["core:datatype"]
            ["pattern"])
        assert pattern.match(meta["global"]["core:datatype"])


class TestPlanExport:
    def test_missing_data_file_is_skipped(self, tmp_path):
        plan = sig.plan_export([_sample(iq_path=str(tmp_path / "nope.cu8"))])
        assert plan["counts"] == {"planned": 0, "skipped": 1}
        assert "不存在" in plan["skipped"][0]["reasons"][0]

    def test_undecleared_datatype_is_skipped_with_reason(self, tmp_path):
        data = tmp_path / "s1.cu8"
        data.write_bytes(b"\x00\x01" * 16)
        item = _sample(iq_path=str(data))
        del item["datatype"]
        plan = sig.plan_export([item])
        assert plan["entries"] == []
        assert "未声明 datatype" in plan["skipped"][0]["reasons"][0]

    def test_same_iq_path_twice_is_refused(self, tmp_path):
        data = tmp_path / "s1.cu8"
        data.write_bytes(b"\x00\x01" * 16)
        plan = sig.plan_export([
            _sample("a", iq_path=str(data)),
            _sample("b", iq_path=str(data), label="bpsk"),
        ])
        assert plan["entries"] == []
        assert len(plan["skipped"]) == 2
        assert "同一 iq_path" in plan["skipped"][0]["reasons"][0]

    def test_meta_sits_next_to_data(self, tmp_path):
        data = tmp_path / "rec.01.cu8"
        data.write_bytes(b"\x00" * 8)
        plan = sig.plan_export([_sample(iq_path=str(data))])
        assert plan["entries"][0]["meta_path"] == str(
            tmp_path / "rec.01.sigmf-meta")


class TestExportSamples:
    def test_writes_meta_and_leaves_data_untouched(self, tmp_path):
        data = tmp_path / "s1.cu8"
        payload = bytes(range(64))
        data.write_bytes(payload)

        result = sig.export_samples([_sample(iq_path=str(data))])
        assert result["counts"] == {"written": 1, "skipped": 0}

        meta_path = tmp_path / "s1.sigmf-meta"
        meta = _strict_loads(meta_path.read_text(encoding="utf-8"))
        assert meta["global"]["core:datatype"] == "cu8"
        assert meta["global"]["core:dataset"] == "s1.cu8"

        assert data.read_bytes() == payload          # 数据没被动过
        assert not (tmp_path / "s1.sigmf-data").exists()  # 没有偷偷改名/复制
        assert list(tmp_path.glob("*.tmp")) == []    # 原子写不留残片

    def test_compliant_name_needs_no_dataset_key(self, tmp_path):
        data = tmp_path / "s2.sigmf-data"
        data.write_bytes(b"\x00" * 8)
        result = sig.export_samples([_sample(iq_path=str(data))])
        meta = json.loads(Path(result["written"][0]["meta_path"])
                          .read_text(encoding="utf-8"))
        assert "core:dataset" not in meta["global"]

    def test_existing_meta_is_not_overwritten_by_default(self, tmp_path):
        data = tmp_path / "s1.cu8"
        data.write_bytes(b"\x00" * 8)
        meta_path = tmp_path / "s1.sigmf-meta"
        meta_path.write_text("keep me", encoding="utf-8")

        result = sig.export_samples([_sample(iq_path=str(data))])
        assert result["counts"]["written"] == 0
        assert "已存在" in result["skipped"][0]["reasons"][0]
        assert meta_path.read_text(encoding="utf-8") == "keep me"

    def test_force_overwrites(self, tmp_path):
        data = tmp_path / "s1.cu8"
        data.write_bytes(b"\x00" * 8)
        meta_path = tmp_path / "s1.sigmf-meta"
        meta_path.write_text("old", encoding="utf-8")

        result = sig.export_samples([_sample(iq_path=str(data))], force=True)
        assert result["counts"]["written"] == 1
        assert _strict_loads(meta_path.read_text(encoding="utf-8"))[
            "global"]["core:datatype"] == "cu8"

    def test_dry_run_writes_nothing(self, tmp_path):
        data = tmp_path / "s1.cu8"
        data.write_bytes(b"\x00" * 8)
        result = sig.export_samples([_sample(iq_path=str(data))], dry_run=True)
        assert result["counts"] == {"written": 1, "skipped": 0}
        assert list(tmp_path.glob("*.sigmf-meta")) == []

    def test_report_states_ncd_and_never_claims_compliance(self, tmp_path):
        data = tmp_path / "s1.cu8"
        data.write_bytes(b"\x00" * 8)
        text = sig.format_report(
            sig.export_samples([_sample(iq_path=str(data))]))
        assert "Non-Conforming Dataset" in text
        assert "s1.sigmf-data" in text
        assert "没有改名" in text


class TestScript:
    @staticmethod
    def _run(*args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              cwd=str(ROOT), capture_output=True,
                              text=True, timeout=120)

    def _manifest(self, tmp_path, *items):
        path = tmp_path / "manifest.jsonl"
        path.write_text(
            "\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n",
            encoding="utf-8")
        return path

    def test_exports_good_manifest(self, tmp_path):
        data = tmp_path / "a.cu8"
        data.write_bytes(b"\x00" * 8)
        manifest = self._manifest(tmp_path, _sample(iq_path=str(data)))

        proc = self._run(str(manifest))
        assert proc.returncode == 0, proc.stderr
        assert (tmp_path / "a.sigmf-meta").is_file()

    def test_bad_line_turns_exit_code_nonzero(self, tmp_path):
        data = tmp_path / "a.cu8"
        data.write_bytes(b"\x00" * 8)
        manifest = self._manifest(
            tmp_path, _sample(iq_path=str(data)), _sample("bad", license=""))

        proc = self._run(str(manifest))
        assert proc.returncode == 1
        assert "license 未写明" in proc.stdout
        assert (tmp_path / "a.sigmf-meta").is_file()   # 合法行照常导出

    def test_json_output_is_machine_readable(self, tmp_path):
        data = tmp_path / "a.cu8"
        data.write_bytes(b"\x00" * 8)
        manifest = self._manifest(tmp_path, _sample(iq_path=str(data)))

        proc = self._run(str(manifest), "--dry-run", "--json")
        assert proc.returncode == 0, proc.stderr
        payload = _strict_loads(proc.stdout)
        assert payload["counts"] == {"written": 1, "skipped": 0}
        assert payload["written"][0]["sample_id"] == "s1"
        assert list(tmp_path.glob("*.sigmf-meta")) == []

    def test_missing_manifest_is_reported(self, tmp_path):
        proc = self._run(str(tmp_path / "nope.jsonl"))
        assert proc.returncode == 1
        assert "清单文件不存在" in proc.stdout


def _meta(**over):
    glob = {
        "core:datatype": "cu8",
        "core:version": "1.2.0",
        "core:sample_rate": 1024000,
        "core:frequency": 88100000,
        "core:license": "https://creativecommons.org/licenses/by/4.0/",
    }
    glob.update(over.pop("glob", {}))
    meta = {"global": glob, "captures": [{"core:sample_start": 0}],
            "annotations": []}
    meta.update(over)
    return meta


class TestReadMeta:
    def test_reads_real_recording_without_inventing_license(self):
        """仓库里的真实空口样本：结构完好，但没人声明过 license。"""
        info = sig.read_meta(str(REAL_META))
        assert info["ok"] is True
        assert info["datatype"] == "cu8"
        assert info["sample_rate"] == 1024000.0
        assert info["center_freq"] == 88100000.0
        # 早期写法把频率放在 global；要回退并标明来源
        assert "global" in (info["center_freq_source"] or "")
        assert info["license"] == ""
        assert info["license_ok"] is False
        assert any("未声明 license" in p for p in info["problems"])
        assert info["samples"] == 524288          # 1048576 B / 2 B per cu8

    def test_real_recording_entry_refused_until_license_declared(self):
        info = sig.read_meta(str(REAL_META))
        built = sig.manifest_entry_from_meta(info, "real-fm", "fm")
        assert built["entry"] is None
        assert any("license" in p for p in built["problems"])

    def test_explicit_license_unblocks_and_passes_contract(self):
        info = sig.read_meta(str(REAL_META))
        built = sig.manifest_entry_from_meta(
            info, "real-fm", "fm", license_override="CC-BY-4.0")
        assert built["problems"] == []
        entry = built["entry"]
        assert entry["datatype"] == "cu8"
        assert entry["license"] == "CC-BY-4.0"
        assert man.validate_manifest_lines(
            [json.dumps(entry)])["ok"] is True

    def test_ncd_dataset_key_locates_data(self, tmp_path):
        (tmp_path / "raw.cu8").write_bytes(b"\x00" * 64)
        meta = _meta(glob={"core:dataset": "raw.cu8"})
        p = tmp_path / "rec.sigmf-meta"
        p.write_text(json.dumps(meta), encoding="utf-8")
        info = sig.read_meta(str(p))
        assert info["ok"] is True
        assert info["data_path"] == str(tmp_path / "raw.cu8")
        assert info["samples"] == 32

    def test_missing_global_key_is_reported(self, tmp_path):
        meta = _meta()
        del meta["global"]["core:version"]
        (tmp_path / "rec.sigmf-data").write_bytes(b"\x00\x00")
        p = tmp_path / "rec.sigmf-meta"
        p.write_text(json.dumps(meta), encoding="utf-8")
        info = sig.read_meta(str(p))
        assert info["ok"] is False
        assert any("core:version" in x for x in info["problems"])

    def test_unsorted_captures_and_annotations(self, tmp_path):
        meta = _meta(captures=[{"core:sample_start": 10},
                               {"core:sample_start": 0}],
                     annotations=[{"core:sample_start": 5},
                                  {"core:sample_start": 1}])
        (tmp_path / "rec.sigmf-data").write_bytes(b"\x00\x00")
        p = tmp_path / "rec.sigmf-meta"
        p.write_text(json.dumps(meta), encoding="utf-8")
        problems = " ".join(sig.read_meta(str(p))["problems"])
        assert "captures 未按" in problems
        assert "annotations 未按" in problems

    def test_bad_datatype_is_reported(self, tmp_path):
        (tmp_path / "rec.sigmf-data").write_bytes(b"\x00\x00")
        p = tmp_path / "rec.sigmf-meta"
        p.write_text(json.dumps(_meta(glob={"core:datatype": "cf33"})),
                     encoding="utf-8")
        info = sig.read_meta(str(p))
        assert info["ok"] is False
        assert any("core:datatype" in x for x in info["problems"])

    def test_truncated_data_file_is_flagged(self, tmp_path):
        """cu8 单样本 2 B；3 B 的文件不可能被完整解释。"""
        (tmp_path / "rec.sigmf-data").write_bytes(b"\x00\x01\x02")
        p = tmp_path / "rec.sigmf-meta"
        p.write_text(json.dumps(_meta()), encoding="utf-8")
        info = sig.read_meta(str(p))
        assert info["ok"] is False
        assert any("整数倍" in x for x in info["problems"])

    def test_missing_data_file_is_reported(self, tmp_path):
        p = tmp_path / "rec.sigmf-meta"
        p.write_text(json.dumps(_meta()), encoding="utf-8")
        info = sig.read_meta(str(p))
        assert info["ok"] is False
        assert any("数据文件不存在" in x for x in info["problems"])

    def test_missing_sample_rate_blocks_manifest(self, tmp_path):
        meta = _meta()
        del meta["global"]["core:sample_rate"]
        (tmp_path / "rec.sigmf-data").write_bytes(b"\x00\x00")
        p = tmp_path / "rec.sigmf-meta"
        p.write_text(json.dumps(meta), encoding="utf-8")
        info = sig.read_meta(str(p))
        assert info["ok"] is False
        assert any("core:sample_rate" in x for x in info["problems"])

    def test_mbdsdr_license_is_picked_up(self, tmp_path):
        (tmp_path / "rec.sigmf-data").write_bytes(b"\x00\x00")
        meta = _meta(glob={"mbdsdr:license": "MIT"})
        del meta["global"]["core:license"]
        p = tmp_path / "rec.sigmf-meta"
        p.write_text(json.dumps(meta), encoding="utf-8")
        info = sig.read_meta(str(p))
        assert info["license"] == "MIT"
        assert info["license_source"] == "mbdsdr:license"

    def test_not_a_file_and_bad_json(self, tmp_path):
        assert sig.read_meta(str(tmp_path / "nope.sigmf-meta"))["ok"] is False
        p = tmp_path / "bad.sigmf-meta"
        p.write_text("{not json", encoding="utf-8")
        assert sig.read_meta(str(p))["ok"] is False


class TestImportScript:
    @staticmethod
    def _run(*args):
        return subprocess.run([sys.executable, str(IMPORT_SCRIPT), *args],
                              cwd=str(ROOT), capture_output=True,
                              text=True, timeout=120)

    @staticmethod
    def _recording(folder, stem="a", data=b"\x00" * 32, **glob):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{stem}.sigmf-data").write_bytes(data)
        (folder / f"{stem}.sigmf-meta").write_text(
            json.dumps(_meta(glob=glob)), encoding="utf-8")
        return folder

    def test_imports_directory_into_contract_valid_manifest(self, tmp_path):
        rec = self._recording(tmp_path / "rec")
        out = tmp_path / "manifest.jsonl"

        proc = self._run(str(rec), "--label", "fm", "-o", str(out))
        assert proc.returncode == 0, proc.stderr + proc.stdout
        entries = [json.loads(x) for x in
                   out.read_text(encoding="utf-8").splitlines()]
        assert len(entries) == 1
        assert entries[0]["label"] == "fm"
        assert entries[0]["datatype"] == "cu8"
        assert entries[0]["sample_rate"] == 1024000.0
        assert entries[0]["center_freq"] == 88100000.0
        assert man.validate_manifest_file(str(out))["ok"] is True

    def test_recording_without_license_is_skipped(self, tmp_path):
        rec = self._recording(tmp_path / "rec")
        meta = _meta()
        del meta["global"]["core:license"]
        (rec / "a.sigmf-meta").write_text(json.dumps(meta), encoding="utf-8")
        out = tmp_path / "manifest.jsonl"

        proc = self._run(str(rec), "--label", "fm", "-o", str(out))
        assert proc.returncode == 1
        assert "license" in proc.stdout
        assert out.read_text(encoding="utf-8") == ""

    def test_explicit_license_is_attributed_to_the_user(self, tmp_path):
        rec = self._recording(tmp_path / "a")
        meta = _meta()
        del meta["global"]["core:license"]
        (rec / "a.sigmf-meta").write_text(json.dumps(meta), encoding="utf-8")
        out = tmp_path / "manifest.jsonl"

        proc = self._run(str(rec), "--label", "fm", "--license", "CC-BY-4.0",
                         "-o", str(out))
        assert proc.returncode == 0, proc.stdout
        entry = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        assert entry["license"] == "CC-BY-4.0"
        assert "数据拥有者" in proc.stdout

    def test_duplicate_stems_get_unique_sample_ids(self, tmp_path):
        self._recording(tmp_path / "x", stem="a")
        self._recording(tmp_path / "y", stem="a")
        out = tmp_path / "manifest.jsonl"

        proc = self._run(str(tmp_path), "--label", "fm", "-o", str(out))
        assert proc.returncode == 0, proc.stdout
        ids = [json.loads(x)["sample_id"]
               for x in out.read_text(encoding="utf-8").splitlines()]
        assert ids == ["a", "a-2"]

    def test_path_without_meta_is_reported(self, tmp_path):
        plain = tmp_path / "notes.txt"
        plain.write_text("hi", encoding="utf-8")
        proc = self._run(str(plain), "--label", "fm")
        assert proc.returncode == 1
        assert "不是 .sigmf-meta 文件" in proc.stdout

    def test_json_mode_lists_entries_and_skips(self, tmp_path):
        rec = self._recording(tmp_path / "rec")
        meta = _meta()
        del meta["global"]["core:sample_rate"]
        (rec / "b.sigmf-data").write_bytes(b"\x00\x00")
        (rec / "b.sigmf-meta").write_text(json.dumps(meta), encoding="utf-8")

        proc = self._run(str(rec), "--label", "fm", "--json")
        assert proc.returncode == 1
        payload = json.loads(proc.stdout)
        assert payload["counts"] == {"imported": 1, "skipped": 1}
        assert payload["entries"][0]["sample_id"] == "a"
        assert "core:sample_rate" in " ".join(payload["skipped"][0]["reasons"])


class TestRecorderIntegration:
    """录制器写出的 meta 必须能被导入器读懂——跨模块真实链路，不手抄字段。"""

    def test_meta_from_recording_meta_is_readable(self, tmp_path):
        from mbdsdr_ai import recording_meta as rec_meta

        data = tmp_path / "rec.sigmf-data"
        data.write_bytes(b"\x00" * 64)
        meta_path = rec_meta.write_sigmf_meta(
            str(data), sample_rate=2400000, center_freq_hz=100100000,
            gain_db=20, hardware="Test Signal",
            start_time_iso="2026-11-02T16:22:57Z")

        info = sig.read_meta(meta_path)
        assert info["ok"] is True
        assert info["datatype"] == "cf32_le"
        assert info["sample_rate"] == 2400000.0
        assert info["center_freq"] == 100100000.0
        assert info["samples"] == 8
        # 录制器不代用户决定授权，导入器也不替它补
        assert info["license_ok"] is False
        assert sig.manifest_entry_from_meta(info, "rec", "nfm")["entry"] is None

    def test_zero_sample_recording_is_refused_as_unusable(self, tmp_path):
        from mbdsdr_ai import recording_meta as rec_meta

        data = tmp_path / "empty.sigmf-data"
        data.write_bytes(b"")
        meta_path = rec_meta.write_sigmf_meta(
            str(data), sample_rate=2400000, center_freq_hz=100100000)

        info = sig.read_meta(meta_path)
        assert info["samples"] == 0
        assert info["ok"] is False          # 结构完整，但不是一个能用的样本
        assert any("0 字节" in p for p in info["problems"])
        assert sig.manifest_entry_from_meta(info, "rec", "nfm")["entry"] is None
