# SPDX-License-Identifier: MIT
"""`scripts/head_build_smoke.py` 的判据：红了必须记账并醒目打印，跑不起来也必须响。

这些用例刻意不碰 Qt / 不起主窗口：它们验的是「检查器本身是否可靠」——能否把
一次真实的 ``AttributeError`` 判成红、能否把「没跑成」判成红且记账、跳过是否可见、
post-commit 是否在 bypass 分支之前就调了它。构造类用例由被检查的 smoke 集本身覆盖。
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


hbs = _load("head_build_smoke", ROOT / "scripts" / "head_build_smoke.py")


def test_touches_build_inputs_scopes_docs_and_data_out():
    assert hbs._touches_build_inputs(["docs/COORDINATION.md"]) is False
    assert hbs._touches_build_inputs(
        ["docs/incidents/2026-11-14-head-unbuildable-swept-call-site.md", "data/x.csv"]
    ) is False
    assert hbs._touches_build_inputs(["desktop/main_window.py"]) is True
    assert hbs._touches_build_inputs(["mbdsdr_ai/sat_passes.py"]) is True
    assert hbs._touches_build_inputs(["tests/test_main_window.py"]) is True
    assert hbs._touches_build_inputs(["assets/theme.qss"]) is True
    assert hbs._touches_build_inputs(["main.py"]) is True


_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="1" failures="1" tests="3">
<testcase classname="tests.test_main_window_import_smoke" name="test_ok" time="0.01" />
<testcase classname="tests.test_panel_registry_factory" name="test_main_window_builds_panels_through_registry" time="0.6">
<error message="failed on setup with &quot;AttributeError: 'MainWindow' object has no attribute '_create_optional_panel'&quot;">E   AttributeError: 'MainWindow' object has no attribute '_create_optional_panel'</error>
</testcase>
<testcase classname="tests.test_panel_registry_factory" name="test_each_declared_panel_factory_produces_widget[adsb]" time="0.01">
<failure message="AssertionError: boom&#10;&#10;more text">E   AssertionError: boom</failure>
</testcase>
</testsuite></testsuites>
"""


def test_parse_junit_extracts_sha_node_and_first_error_line(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text(_JUNIT, encoding="utf-8")
    failures, collected = hbs._parse_junit(xml)
    assert collected == 3
    assert [f["nodeid"] for f in failures] == [
        "tests/test_panel_registry_factory.py::test_main_window_builds_panels_through_registry",
        "tests/test_panel_registry_factory.py::test_each_declared_panel_factory_produces_widget[adsb]",
    ]
    assert failures[0]["first_line"] == (
        "AttributeError: 'MainWindow' object has no attribute '_create_optional_panel'"
    )
    assert failures[1]["first_line"] == "AssertionError: boom"


def test_missing_junit_is_not_green():
    failures, collected = hbs._parse_junit(Path("does-not-exist-junit.xml"))
    assert failures == [] and collected == 0
    # 没收集到任何用例却 rc=0 不能算通过（rc=5 才是 pytest 的「没收集到」）。
    assert hbs._status_of(5, [], None) == "error"
    assert hbs._status_of(2, [], None) == "error"
    assert hbs._status_of(None, [], "spawn_error") == "error"
    assert hbs._status_of(1, [], None) == "error"


def test_real_undefined_attribute_failure_is_red(tmp_path):
    """真造一个「调用了未定义的方法」的失败用例，检查器必须判红并取到首条错误。"""
    (tmp_path / "test_boom.py").write_text(
        "def test_undefined_method():\n"
        "    class Window:\n"
        "        pass\n"
        "    Window()._create_optional_panel('scanner')\n",
        encoding="utf-8",
    )
    junit = tmp_path / "junit.xml"
    rc, tail, error_kind, _elapsed = hbs._run_pytest(
        tmp_path, ["test_boom.py::test_undefined_method"], junit, 120
    )
    failures, collected = hbs._parse_junit(junit)
    assert error_kind is None
    assert rc == 1
    assert collected == 1
    assert len(failures) == 1
    assert "AttributeError" in failures[0]["first_line"]
    assert "_create_optional_panel" in failures[0]["first_line"]
    assert hbs._status_of(rc, failures, error_kind) == "fail"


def test_missing_test_path_is_error_not_green(tmp_path):
    """用例文件不存在 / 收集不到：必须是 error（跑不起来），不能算绿。"""
    junit = tmp_path / "junit.xml"
    rc, tail, error_kind, _elapsed = hbs._run_pytest(
        tmp_path, ["test_does_not_exist.py::test_nope"], junit, 120
    )
    failures, collected = hbs._parse_junit(junit)
    assert error_kind is None
    assert rc != 0
    assert collected == 0
    assert hbs._status_of(rc, failures, error_kind) == "error"


def _base_record(ledger: Path, status: str) -> dict:
    return {
        "ts": "2026-11-14T00:00:00",
        "type": "HEAD_BUILD_SMOKE",
        "status": status,
        "commit": "12bcdb7a" + "0" * 32,
        "subject": "fix(sat): 5° 可见门收敛为单一来源",
        "failures": [
            {
                "nodeid": "tests/test_panel_registry_factory.py::test_main_window_builds_panels_through_registry",
                "first_line": "AttributeError: 'MainWindow' object has no attribute '_create_optional_panel'",
                "kind": "error",
            }
        ],
        "reason": "pytest 被中断（常见：Qt 全局锁等待超时或收集中断）",
        "tests_collected": 16,
        "ledger": str(ledger),
        "checkout_ms": 140,
        "pytest_ms": 4290,
        "elapsed_ms": 4731,
    }


def test_red_is_recorded_and_printed_loudly(tmp_path, capsys):
    ledger = tmp_path / "head_build_smoke.log"
    rc = hbs._record_and_report(ledger, _base_record(ledger, "fail"))
    out = capsys.readouterr().out
    assert rc == hbs.EXIT_RED == 3
    assert "HEAD 不可构建" in out and "12bcdb7a" in out
    assert "test_main_window_builds_panels_through_registry" in out
    assert "AttributeError" in out
    assert str(ledger) in out

    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["status"] == "fail"
    assert rows[0]["commit"].startswith("12bcdb7a")
    assert rows[0]["failures"][0]["first_line"].startswith("AttributeError")


def test_error_is_recorded_and_printed_loudly(tmp_path, capsys):
    """检查自身跑不起来（锁占满 / 超时 / 检出失败）必须响，不能当绿。"""
    ledger = tmp_path / "head_build_smoke.log"
    rc = hbs._record_and_report(ledger, _base_record(ledger, "error"))
    out = capsys.readouterr().out
    assert rc == hbs.EXIT_UNKNOWN == 4
    assert "无法判定" in out
    assert "Qt 全局锁" in out
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["status"] == "error"


def test_skipped_is_visible_in_output_and_ledger(tmp_path, capsys):
    ledger = tmp_path / "head_build_smoke.log"
    rc = hbs._record_and_report(ledger, _base_record(ledger, "skipped"))
    out = capsys.readouterr().out
    assert rc == hbs.EXIT_OK == 0
    assert "跳过" in out
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["status"] == "skipped"


def test_post_commit_calls_smoke_before_bypass_branches():
    """钩子必须在「已审计/绕过/未审计」分支之前就跑烟雾检查。

    放在 bypass 分支之后，任何一次 ``MBDSDR_BYPASS_REASON`` 提交都会绕过检查。
    注意要匹配**调用行**（缩进行），不能匹配 ``def _head_build_smoke(root, head):``。
    """
    source = (ROOT / "scripts" / "hooks" / "post-commit").read_text(
        encoding="utf-8", errors="replace"
    )
    call = re.search(
        r"^[ \t]+_head_build_smoke\(root, head\)[ \t]*$", source, re.MULTILINE
    )
    assert call is not None, "post-commit 里找不到烟雾检查调用"
    bypass = source.index('reason = (os.environ.get("MBDSDR_BYPASS_REASON")')
    marker_return = source.index(
        "if marker.exists() and (time.time() - marker.stat().st_mtime)"
    )
    assert call.start() < bypass
    assert call.start() < marker_return


def test_smoke_script_is_installed_alongside_hooks():
    """旧工作树里没有 ``scripts/head_build_smoke.py``，钩子副本必须兜住。"""
    installer = _load(
        "install_git_hooks_for_smoke",
        ROOT / "scripts" / "install_git_hooks.py",
    )
    assert "head_build_smoke.py" in installer.SUPPORT_FILES
