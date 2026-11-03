# SPDX-License-Identifier: MIT
"""tests/test_decode_captures_offline.py — 离线批处理解码入口（P5 之后）。

守这些契约：
  * 单模式多文件 → 报告是单层 `results`；多模式才包成 `reports`（不把
    单模式误标成 all）；两种结构审计都能取到每一条结果；
  * 缺样本/未接线/未知模式在报告里**原样保留**错误码，不折叠成「0 条报文」；
  * 退出码只表示报告是否生成成功（零报文是合法结果）；
  * 全程离线：只碰 tmp_path，不开声卡、不碰硬件。
"""
import importlib.util
import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

SCRIPT = os.path.join(_ROOT, "scripts", "decode_captures.py")


@pytest.fixture(scope="module")
def cap():
    spec = importlib.util.spec_from_file_location("decode_captures", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_discover_files_filters_and_sorts(cap, tmp_path):
    (tmp_path / "b.cf32").write_bytes(b"\0" * 8)
    (tmp_path / "a.wav").write_bytes(b"\0" * 8)
    (tmp_path / "c.CFILE").write_bytes(b"\0" * 8)
    (tmp_path / "note.json").write_text("{}", encoding="utf-8")
    found = [os.path.basename(p) for p in cap.discover_files(str(tmp_path))]
    assert found == ["a.wav", "b.cf32", "c.CFILE"]


def test_sha256_matches_hashlib(cap, tmp_path):
    import hashlib

    p = tmp_path / "cap.cf32"
    p.write_bytes(b"abc" * 100)
    assert cap.sha256_of(str(p)) == hashlib.sha256(b"abc" * 100).hexdigest()
    assert cap.sha256_of(str(p), limit=3) == hashlib.sha256(b"abc").hexdigest()


def test_summary_separates_categories(cap):
    results = [
        {"error_code": "missing_sample", "num_messages": 0},
        {"error_code": "unknown_mode", "num_messages": 0},
        {"error_code": "not_implemented", "num_messages": 0},
        {"error_code": "", "num_messages": 3},
        {"error_code": "", "num_messages": 0},
    ]
    s = cap._summarize(results)
    assert s["files"] == 5
    assert s["error"] == 2
    assert s["not_implemented"] == 1
    assert s["decoded"] == 1
    assert s["empty"] == 1
    assert s["messages_total"] == 3
    assert s["by_error_code"] == {"missing_sample": 1, "unknown_mode": 1}


def test_iter_results_handles_both_structures(cap):
    single = {"results": [{"file": "a"}]}
    wrapped = {"reports": [
        {"results": [{"file": "a"}]},
        {"results": [{"file": "b"}]},
    ]}
    assert cap.iter_results(single) == [{"file": "a"}]
    assert cap.iter_results(wrapped) == [{"file": "a"}, {"file": "b"}]


def test_single_mode_multi_file_is_not_marked_all(cap, tmp_path, capsys):
    for name in ("a.cf32", "b.cf32"):
        (tmp_path / name).write_bytes(b"\0" * 16)
    rc = cap.main(["--mode", "ft8", "--dir", str(tmp_path)])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "ft8"
    assert "reports" not in report
    assert [os.path.basename(r["file"]) for r in report["results"]] == [
        "a.cf32", "b.cf32"]
    # ft8 通用入口未接线：诚实标 not_implemented，不冒充「0 条报文」
    assert report["summary"]["not_implemented"] == 2
    assert report["summary"]["empty"] == 0
    assert report["summary"]["error"] == 0


def test_main_reports_missing_sample_honestly(cap, tmp_path, capsys):
    rc = cap.main(["--mode", "ais", "--file", str(tmp_path / "absent.cf32")])
    assert rc == 0                      # 报告生成成功 ≠ 解出东西
    report = json.loads(capsys.readouterr().out)
    assert report["results"][0]["error_code"] == "missing_sample"
    assert report["summary"]["by_error_code"] == {"missing_sample": 1}
    assert report["summary"]["empty"] == 0


def test_main_requires_exactly_one_source(cap):
    with pytest.raises(SystemExit):
        cap.main(["--mode", "ais"])
    with pytest.raises(SystemExit):
        cap.main(["--mode", "ais", "--file", "x", "--dir", "y"])


def test_main_empty_dir_returns_2(cap, tmp_path, capsys):
    assert cap.main(["--mode", "ais", "--dir", str(tmp_path)]) == 2
    assert "未发现" in capsys.readouterr().err


def test_main_all_mode_wraps_reports(cap, tmp_path, monkeypatch, capsys):
    (tmp_path / "a.cf32").write_bytes(b"\0" * 16)
    monkeypatch.setattr(cap, "build_report", _stub_report)
    import mbdsdr_ai.decode_router as router
    monkeypatch.setattr(router, "supported_modes", lambda: ["ais", "navtex"])
    rc = cap.main(["--mode", "all", "--dir", str(tmp_path)])
    assert rc == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "all"
    assert [r["mode"] for r in report["reports"]] == ["ais", "navtex"]
    assert report["summary"]["files"] == 2


def test_out_file_is_written(cap, tmp_path, capsys):
    p = tmp_path / "cap.wav"
    p.write_bytes(b"\0" * 16)
    out = tmp_path / "nested" / "report.json"
    rc = cap.main(["--mode", "ft8", "--file", str(p), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["mode"] == "ft8"
    assert capsys.readouterr().out.strip() == str(out)


def test_audit_dir_writes_every_entry(cap, tmp_path):
    p = tmp_path / "cap.cf32"
    p.write_bytes(b"\0" * 16)
    audit_dir = tmp_path / "audit"
    rc = cap.main(["--mode", "ft8", "--file", str(p), "--audit-dir", str(audit_dir)])
    assert rc == 0
    with open(audit_dir / "decode_audit.jsonl", encoding="utf-8") as f:
        lines = [json.loads(x) for x in f if x.strip()]
    assert len(lines) == 1
    assert lines[0]["tool"] == "batch_decode"
    assert lines[0]["error_code"] == "not_implemented"
    assert lines[0]["messages_recorded"] is False


def test_audit_handles_wrapped_report(cap, tmp_path):
    wrapped = {"mode": "all", "reports": [
        {"results": [{"file": "a", "mode": "ais"}]},
        {"results": [{"file": "b", "mode": "navtex"}]},
    ]}
    path = cap._audit(wrapped, str(tmp_path))
    with open(path, encoding="utf-8") as f:
        lines = [json.loads(x) for x in f if x.strip()]
    assert [x["mode"] for x in lines] == ["ais", "navtex"]


def _stub_report(files, mode, sample_rate=1e6):
    return {
        "generated_at": "2026-01-01T00:00:00Z",
        "mode": mode,
        "results": [{"file": f, "mode": mode, "error_code": ""} for f in files],
        "summary": {"files": len(files)},
    }
