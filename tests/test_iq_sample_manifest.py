# SPDX-License-Identifier: MIT
"""IQ 样本清单校验器测试：坏样本必须被拒，且拒绝理由能指导修复。

背景：分类器重训缺真实多类样本。在样本到位前先立契约，防止拿合成
信号或来源不清的数据「训练」出一个演示模型。本文件钉的是「什么算数」。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from mbdsdr_ai import iq_sample_manifest as man  # noqa: E402


def _good(sample_id="s1", **over):
    item = {
        "sample_id": sample_id,
        "label": "fm",
        "sample_rate": 1024000,
        "center_freq": 88100000,
        "iq_path": "samples/s1.cu8",
        "license": "CC-BY-4.0",
    }
    item.update(over)
    return item


def _lines(*items):
    return [json.dumps(i, ensure_ascii=False) for i in items]


class TestAccepts:
    def test_two_valid_samples(self):
        r = man.validate_manifest_lines(_lines(_good("s1"), _good("s2")))
        assert r["ok"] is True
        assert r["counts"] == {"accepted": 2, "rejected": 0}

    def test_unknown_extra_fields_are_allowed(self):
        r = man.validate_manifest_lines(
            _lines(_good(notes="bench capture", gain_db=20)))
        assert r["ok"] is True

    def test_blank_lines_are_ignored(self):
        r = man.validate_manifest_lines(["", "  ", *_lines(_good())])
        assert r["ok"] is True
        assert r["counts"]["accepted"] == 1


class TestRejects:
    def test_missing_field(self):
        item = _good()
        del item["license"]
        r = man.validate_manifest_lines(_lines(item))
        assert r["ok"] is False
        assert "缺少字段 license" in r["rejected"][0]["reasons"]

    def test_empty_sample_id(self):
        r = man.validate_manifest_lines(_lines(_good(sample_id="")))
        assert r["ok"] is False
        assert any("sample_id 为空" in x for x in r["rejected"][0]["reasons"])

    def test_negative_and_zero_sample_rate(self):
        for v in (0, -1024000):
            r = man.validate_manifest_lines(_lines(_good(sample_rate=v)))
            assert r["ok"] is False
            assert any("sample_rate" in x for x in r["rejected"][0]["reasons"])

    def test_bool_is_not_a_number(self):
        """JSON true 会被 Python 读成 bool（int 子类），不能当 1 Hz 收下。"""
        r = man.validate_manifest_lines(_lines(_good(sample_rate=True)))
        assert r["ok"] is False

    def test_nan_center_freq_rejected(self):
        r = man.validate_manifest_lines(
            _lines(_good(center_freq=float("nan"))))
        assert r["ok"] is False
        assert any("center_freq" in x for x in r["rejected"][0]["reasons"])

    def test_license_unknown_is_rejected(self):
        for lic in ("", "unknown", "N/A", "?", "none"):
            r = man.validate_manifest_lines(_lines(_good(license=lic)))
            assert r["ok"] is False, lic
            assert any("license 未写明" in x
                       for x in r["rejected"][0]["reasons"]), lic

    def test_duplicate_sample_id(self):
        r = man.validate_manifest_lines(_lines(_good("dup"), _good("dup")))
        assert r["ok"] is False
        assert r["counts"] == {"accepted": 1, "rejected": 1}
        assert "重复" in " ".join(r["rejected"][0]["reasons"])

    def test_bad_json_does_not_kill_the_rest(self):
        r = man.validate_manifest_lines(
            ["{not json", *_lines(_good("s1"))])
        assert r["ok"] is False
        assert r["counts"] == {"accepted": 1, "rejected": 1}
        assert r["rejected"][0]["line"] == 1

    def test_non_object_line_rejected(self):
        r = man.validate_manifest_lines(["[1,2,3]"])
        assert r["ok"] is False
        assert "JSON 对象" in r["rejected"][0]["reasons"][0]


class TestFiles:
    def test_missing_file_is_reported_not_raised(self, tmp_path):
        r = man.validate_manifest_file(str(tmp_path / "nope.jsonl"))
        assert r["ok"] is False
        assert "不存在" in r["rejected"][0]["reasons"][0]

    def test_check_files_resolves_relative_to_manifest(self, tmp_path):
        (tmp_path / "a.cu8").write_bytes(b"\x00\x01" * 8)
        item = _good(iq_path="a.cu8")
        p = tmp_path / "manifest.jsonl"
        p.write_text(json.dumps(item) + "\n", encoding="utf-8")

        good = man.validate_manifest_file(str(p), check_files=True)
        assert good["ok"] is True

        item2 = _good("s2", iq_path="missing.cu8")
        p.write_text(json.dumps(item2) + "\n", encoding="utf-8")
        bad = man.validate_manifest_file(str(p), check_files=True)
        assert bad["ok"] is False
        assert "不存在" in bad["rejected"][0]["reasons"][0]

    def test_file_check_off_by_default(self, tmp_path):
        """默认不碰文件系统：清单可以指向还没同步的样本。"""
        p = tmp_path / "manifest.jsonl"
        p.write_text(json.dumps(_good(iq_path="not-there.cu8")) + "\n",
                     encoding="utf-8")
        assert man.validate_manifest_file(str(p))["ok"] is True

    def test_utf8_bom_manifest_is_accepted(self, tmp_path):
        """PowerShell 5.1 / 记事本存 UTF-8 会带 BOM；BOM 不是坏清单。"""
        p = tmp_path / "manifest.jsonl"
        p.write_bytes(b"\xef\xbb\xbf"
                      + json.dumps(_good()).encode("utf-8") + b"\n")
        r = man.validate_manifest_file(str(p))
        assert r["ok"] is True
        assert r["counts"] == {"accepted": 1, "rejected": 0}


class TestAudit:
    """结构体检只排除硬伤，不冒充「可以开训」。"""

    def test_two_classes_with_distinct_files_are_structurally_ready(self):
        accepted = [_good("s1", label="fm", iq_path="a.cu8"),
                    _good("s2", label="fm", iq_path="b.cu8"),
                    _good("s3", label="bpsk", iq_path="c.cu8")]
        a = man.audit_manifest(accepted)
        assert a["verdict"] == "structurally_ready"
        assert a["samples"] == 3
        assert a["classes"] == ["bpsk", "fm"]
        assert a["class_counts"] == {"fm": 2, "bpsk": 1}
        assert a["sample_rates_hz"] == [1024000.0]
        assert a["problems"] == []
        # 不许把 structural_ready 说成「能训出可用模型」
        assert "不代表样本足够" in a["caveat"]

    def test_empty_manifest_is_not_ready(self):
        a = man.audit_manifest([])
        assert a["verdict"] == "not_ready"
        assert "没有样本" in a["problems"][0]

    def test_single_class_is_not_ready(self):
        a = man.audit_manifest([_good("s1"), _good("s2", iq_path="b.cu8")])
        assert a["verdict"] == "not_ready"
        assert any("单类数据" in p for p in a["problems"])

    def test_same_file_under_two_labels_is_leakage(self):
        accepted = [_good("s1", label="fm", iq_path="same.cu8"),
                    _good("s2", label="bpsk", iq_path="same.cu8")]
        a = man.audit_manifest(accepted)
        assert a["verdict"] == "not_ready"
        assert a["label_conflict_paths"] == ["same.cu8"]
        assert a["duplicate_paths"] == {"same.cu8": 2}
        assert any("标签泄漏" in p for p in a["problems"])

    def test_repeated_file_same_label_is_flagged(self):
        accepted = [_good("s1", iq_path="same.cu8"),
                    _good("s2", iq_path="same.cu8"),
                    _good("s3", label="bpsk", iq_path="b.cu8")]
        a = man.audit_manifest(accepted)
        assert a["verdict"] == "not_ready"
        assert a["duplicate_paths"] == {"same.cu8": 2}

    def test_format_audit_reports_counts_and_caveat(self):
        a = man.audit_manifest([
            _good("s1", label="fm", iq_path="a.cu8"),
            _good("s2", label="bpsk", iq_path="b.cu8")])
        text = man.format_audit(a)
        assert "2 条 / 2 类" in text
        assert "- fm: 1 条" in text
        assert "不代表样本足够" in text


class TestFormat:
    def test_report_lists_line_numbers_and_reasons(self):
        r = man.validate_manifest_lines(
            _lines(_good("ok1"), _good("bad", license="")))
        text = man.format_validation(r)
        assert "通过 1 条，拒绝 1 条" in text
        assert "第 2 行" in text
        assert "license 未写明" in text

    def test_report_says_all_good(self):
        text = man.format_validation(
            man.validate_manifest_lines(_lines(_good())))
        assert "全部合法" in text


class TestSplit:
    """切分方案：以文件为最小单位，坏输入不交付半套结果。"""

    @staticmethod
    def _dataset(n_per_class=12, classes=("fm", "bpsk")):
        rows = []
        for cls in classes:
            for i in range(n_per_class):
                rows.append(_good(f"{cls}-{i:02d}", label=cls,
                                  iq_path=f"samples/{cls}/{i:02d}.cu8"))
        return rows

    def test_deterministic_and_grouped_by_file(self):
        rows = self._dataset()
        a = man.plan_split(rows)
        b = man.plan_split(rows)
        assert a == b
        assert a["ok"] is True
        assert set(a["train"]).isdisjoint(a["val"])
        assert len(a["train"]) + len(a["val"]) == len(rows)
        file_of = {r["sample_id"]: r["iq_path"] for r in rows}
        val_files = {file_of[s] for s in a["val"]}
        train_files = {file_of[s] for s in a["train"]}
        assert val_files.isdisjoint(train_files)
        for stats in a["per_class"].values():
            assert stats["val_files"] >= 1

    def test_seed_changes_assignment(self):
        rows = self._dataset()
        assert man.plan_split(rows, seed=0)["val"] != \
            man.plan_split(rows, seed=1)["val"]

    def test_val_ratio_is_per_class(self):
        r = man.plan_split(self._dataset(10), val_ratio=0.3)
        assert len(r["val"]) == 6              # 每类 3 个文件
        assert all(st["val_files"] == 3 for st in r["per_class"].values())

    def test_multi_sample_file_stays_in_one_split(self):
        rows = [_good("a1", iq_path="f1.cu8"), _good("a2", iq_path="f1.cu8"),
                _good("b1", iq_path="f2.cu8"), _good("b2", iq_path="f2.cu8")]
        r = man.plan_split(rows)
        assert r["ok"] is True and r["groups"] == 2
        pair = {"a1", "a2"}
        assert pair <= set(r["train"]) or pair <= set(r["val"])

    def test_single_file_class_refuses(self):
        rows = [_good("f1", label="fm", iq_path="a.cu8"),
                _good("f2", label="fm", iq_path="b.cu8"),
                _good("b1", label="bpsk", iq_path="c.cu8")]
        r = man.plan_split(rows)
        assert r["ok"] is False
        assert r["train"] == [] and r["val"] == []
        assert any("只有 1 个文件" in p for p in r["problems"])

    def test_label_conflict_refuses(self):
        rows = [_good("s1", label="fm", iq_path="same.cu8"),
                _good("s2", label="bpsk", iq_path="same.cu8")]
        r = man.plan_split(rows)
        assert r["ok"] is False
        assert r["val"] == [] and r["train"] == []
        assert any("标签冲突" in p for p in r["problems"])

    def test_duplicate_sample_id_refuses(self):
        rows = [_good("dup", iq_path="a.cu8"), _good("dup", iq_path="b.cu8")]
        r = man.plan_split(rows)
        assert r["ok"] is False
        assert any("重复" in p for p in r["problems"])

    def test_missing_fields_refused(self):
        rows = [_good("s1"), _good("s2", iq_path=""), _good("s3", iq_path="c.cu8")]
        r = man.plan_split(rows)
        assert r["ok"] is False
        assert r["train"] == []

    def test_empty_and_bad_ratio(self):
        assert man.plan_split([])["ok"] is False
        for bad in (0, 1, -0.2, 1.5):
            with pytest.raises(ValueError):
                man.plan_split(self._dataset(2), val_ratio=bad)

    def test_caveat_does_not_promise_leak_free(self):
        r = man.plan_split(self._dataset(4))
        assert "只排除" in r["caveat"]
        assert "不保证" in r["caveat"]
