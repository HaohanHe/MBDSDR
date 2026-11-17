# SPDX-License-Identifier: MIT
"""Task 61：文档一致性巡检（``scripts/docs_consistency_audit.py``）。

反证覆盖两类真实漂移：CLI 退出码缺项、工具总表与 registry 不一致；
另加外部阻塞关键词漏登记。任一处都必须退出 1 并列出条目。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import docs_consistency_audit as audit  # noqa: E402


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_real_docs_are_consistent():
    report = audit.build_report()
    assert report["ok"], report["issues"]


def test_cli_doc_missing_exit_code_is_drift(tmp_path):
    text = audit.CLI_DOC.read_text(encoding="utf-8")
    drifted = "\n".join(line for line in text.splitlines()
                        if not line.startswith("| 5 |"))
    path = _write(tmp_path, "cli_usage.md", drifted + "\n")

    result = audit.check_cli_exit_codes(path)
    assert result["ok"] is False
    assert 5 in result["missing_in_docs"]


def test_cli_drift_makes_main_exit_one(tmp_path, capsys):
    text = audit.CLI_DOC.read_text(encoding="utf-8")
    drifted = "\n".join(line for line in text.splitlines()
                        if not line.startswith("| 5 |"))
    path = _write(tmp_path, "cli_usage.md", drifted + "\n")

    rc = audit.main(["--cli-doc", str(path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "退出码 5" in out


def test_tool_doc_missing_registry_tool_is_drift(tmp_path):
    path = _write(tmp_path, "tool-availability.md",
                  "# 工具\n\n## 逐项总表\n\n"
                  "| 工具 | 分类 |\n| --- | --- |\n"
                  "| `alpha_tool` | test |\n")
    result = audit.check_tool_availability(
        path, registry_names=["alpha_tool", "beta_tool"])
    assert result["ok"] is False
    assert result["missing_in_docs"] == ["beta_tool"]


def test_tool_drift_makes_main_exit_one(tmp_path, monkeypatch, capsys):
    path = _write(tmp_path, "tool-availability.md",
                  "# 工具\n\n## 逐项总表\n\n"
                  "| 工具 | 分类 |\n| --- | --- |\n"
                  "| `alpha_tool` | test |\n")
    monkeypatch.setattr(audit, "_registry_names",
                        lambda: ["alpha_tool", "beta_tool"])

    rc = audit.main(["--tool-doc", str(path)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "beta_tool" in out


def test_limitations_missing_external_blocker(tmp_path):
    path = _write(tmp_path, "limitations-register.md",
                  "# 限制\n\nQGIS 不可导入；真实 LLM 缺失；真实多类 IQ 样本缺失。\n")
    result = audit.check_external_blockers(path)
    assert result["ok"] is False
    assert "Flutter SDK" in result["missing_keywords"]


def test_json_report_shape(capsys):
    rc = audit.main(["--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert report["ok"] is True
    assert set(report["checks"]) == {
        "cli_exit_codes", "tool_availability", "external_blockers"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
