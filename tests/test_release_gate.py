# SPDX-License-Identifier: MIT
"""Task 64：发布门三类输出（``scripts/release_gate.py``）。

用假审计函数验证：passed / failed / not_verified 分开统计；
非零退出码只由 failed 触发；not_verified 不计为通过。
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

import release_gate  # noqa: E402


def _fake(status, detail=""):
    return lambda: (status, detail)


def test_all_passed_exits_zero():
    report = release_gate.run_gate([
        ("工具面", _fake(release_gate.PASSED, "ok")),
        ("文档", _fake(release_gate.PASSED, "ok")),
    ])
    assert report["ok"] is True
    assert report["summary"] == {release_gate.PASSED: 2,
                                 release_gate.FAILED: 0,
                                 release_gate.NOT_VERIFIED: 0}


def test_any_failed_makes_gate_fail():
    report = release_gate.run_gate([
        ("工具面", _fake(release_gate.PASSED)),
        ("文档", _fake(release_gate.FAILED, "退出码漂移")),
        ("关键测试", _fake(release_gate.NOT_VERIFIED, "没跑")),
    ])
    assert report["ok"] is False
    assert report["summary"][release_gate.FAILED] == 1
    assert report["summary"][release_gate.NOT_VERIFIED] == 1


def test_not_verified_alone_is_not_a_pass():
    report = release_gate.run_gate([
        ("关键测试", _fake(release_gate.NOT_VERIFIED, "加 --with-tests 才跑")),
    ])
    assert report["ok"] is True              # 没有失败 -> 退出 0
    assert report["summary"][release_gate.PASSED] == 0
    assert report["results"][0]["status"] == release_gate.NOT_VERIFIED


def test_exception_becomes_not_verified():
    def broken():
        raise RuntimeError("依赖缺失")

    report = release_gate.run_gate([("工具面", broken)])
    assert report["ok"] is True
    result = report["results"][0]
    assert result["status"] == release_gate.NOT_VERIFIED
    assert "依赖缺失" in result["detail"]


def test_unknown_status_is_not_verified():
    report = release_gate.run_gate([("x", lambda: ("green-ish", "?"))])
    assert report["results"][0]["status"] == release_gate.NOT_VERIFIED


def test_main_exit_code_and_json(monkeypatch, capsys):
    monkeypatch.setattr(release_gate, "build_checks",
                        lambda with_tests: [("假审计", _fake(release_gate.FAILED, "红"))])
    assert release_gate.main(["--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["results"][0]["name"] == "假审计"


def test_main_passes_with_not_verified(monkeypatch, capsys):
    monkeypatch.setattr(
        release_gate, "build_checks",
        lambda with_tests: [
            ("工具面", _fake(release_gate.PASSED, "ok")),
            ("关键测试", _fake(release_gate.NOT_VERIFIED, "未执行")),
        ])
    assert release_gate.main([]) == 0
    out = capsys.readouterr().out
    assert "[未验证]" in out
    assert "不计为通过" in out


def test_default_checks_mark_tests_not_run(monkeypatch):
    monkeypatch.setattr(release_gate, "check_tool_surface",
                        _fake(release_gate.PASSED, "ok"))
    monkeypatch.setattr(release_gate, "check_docs_consistency",
                        _fake(release_gate.PASSED, "ok"))
    checks = dict(release_gate.build_checks(with_tests=False))
    assert checks["关键测试"]() == (release_gate.NOT_VERIFIED,
                                    "未执行关键测试（加 --with-tests 才跑）")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
