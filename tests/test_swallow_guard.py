# SPDX-License-Identifier: MIT
"""`except: pass` 静默吞噬守卫（P13 补充）。

事故背景：`desktop/rf_sky_view.py::_find_body_at` 的 `float(None)` 抛 TypeError，
被 `except Exception: pass` 吞掉，导致所有深空天体既不可见也不可点，且无日志
无报错。静默丢功能比崩溃更难发现，因此本守卫把「catch-all + 无记录」当作缺陷。

判据：
- EMPTY（`pass` / 只有注释）→ ERROR
- 只 `return None` / `continue` / `break` / 无记录降级 → WARN
- 有日志/状态记录，或重新抛出 → 不算静默
- 白名单条目必须同时回答三问：reason / user_visible / test，并声明 trigger
  （边界怎么失效）与 review_by；门禁会源码级校验 trigger 在 test 里真的被执行。

当前仓库的存量缺陷数是红的事实来源：`test_repo_ratchet_within_baseline`
会逐条打印 `文件:行号 [级别] 符号`。该条自 2026-11-09 起按 K06 登记为
**已知红**（`xfail(strict=True)`）：基线建于 11-06 快照（1284），此后仓库
增长到实测 1397，113 处静默 catch-all 一处没修。本轮不抬基线、不放宽断言；
用 `--runxfail` 可随时复现逐条清单，修完后 strict 会强制销账。详见
`docs/known-red-register.md` K06。
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNER_PATH = REPO_ROOT / "scripts" / "lint_swallow_all.py"


def _load_scanner():
    spec = importlib.util.spec_from_file_location("lint_swallow_all", SCANNER_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["lint_swallow_all"] = module
    spec.loader.exec_module(module)
    return module


scanner = _load_scanner()


def _write(tmp_path: Path, body: str, name: str = "sample.py") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _scan(tmp_path: Path, whitelist: dict | None = None):
    if whitelist is not None:
        wl = tmp_path / "whitelist.json"
        import json
        wl.write_text(json.dumps(whitelist, ensure_ascii=False), encoding="utf-8")
        return scanner.scan_repo(tmp_path, wl)
    return scanner.scan_repo(tmp_path)


# ── 扫描器行为：反证必须先能抓到已知事故形态 ─────────────────────────


def test_detects_known_incident_pattern(tmp_path):
    """DSO 事故同构：float(None) 的 TypeError 被 catch-all 的 pass 吞掉。"""
    _write(tmp_path, "def find_body():\n"
                     "    try:\n"
                     "        float(None)\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path)
    assert report.defect_count == 1
    item = report.defects[0]
    assert item.line == 4
    assert item.body == "EMPTY"
    assert item.severity == "ERROR"
    assert item.symbol == "find_body"


def test_silent_return_is_warn(tmp_path):
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        return None\n")
    report = _scan(tmp_path)
    assert report.defect_count == 1
    assert report.defects[0].body == "SILENT_CONTROL"
    assert report.defects[0].severity == "WARN"


def test_narrow_exception_is_not_catch_all(tmp_path):
    """收窄异常类型不是静默吞噬，不应被误报。"""
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        int('x')\n"
                     "    except ValueError:\n"
                     "        pass\n")
    report = _scan(tmp_path)
    assert report.total_catch_all == 0
    assert report.defect_count == 0


def test_recorded_or_reraised_is_not_silent(tmp_path):
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception as e:\n"
                     "        logger.warning('降级: %s', e)\n"
                     "        return None\n"
                     "def h():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception as e:\n"
                     "        raise RuntimeError('上抛') from e\n")
    report = _scan(tmp_path)
    assert report.defect_count == 0
    assert report.recorded == 1
    assert report.reraised == 1


def test_fake_reraise_is_defect(tmp_path):
    """`except Exception: raise` 是骗扫描器的假重抛，明令禁止。"""
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        raise\n")
    report = _scan(tmp_path)
    assert report.defect_count == 1
    assert report.defects[0].body == "FAKE_RERAISE"
    assert report.defects[0].severity == "ERROR"


def test_suppress_exception_is_forbidden(tmp_path):
    """`contextlib.suppress(Exception)` 是 pass 的等价写法，必须拦住。"""
    _write(tmp_path, "import contextlib\n"
                     "def f():\n"
                     "    with contextlib.suppress(Exception):\n"
                     "        g()\n")
    report = _scan(tmp_path)
    assert report.defect_count == 1
    assert report.defects[0].body == "FORBIDDEN_SUPPRESS"
    assert report.forbidden == 1


def test_whitelist_requires_three_answers(tmp_path):
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        import sounddevice\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path, {"entries": [
        {"path": "sample.py", "symbol": "f", "line": 4, "reason": "外部依赖"}
    ]})
    assert report.whitelisted == 0
    assert report.whitelist_invalid == 1
    assert report.defect_count == 1
    assert "缺少 user_visible" in report.defects[0].whitelist_issue


def test_whitelist_complete_entry_excuses_location(tmp_path):
    test_file = REPO_ROOT / "tests" / "test_audio_selection_e2e.py"
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        import sounddevice\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path, {"entries": [{
        "path": "sample.py", "symbol": "f", "line": 4,
        "reason": "外部边界：可选依赖缺失",
        "user_visible": "该功能不可用并显示提示",
        "test": str(test_file.relative_to(REPO_ROOT)).replace("\\", "/"),
        "trigger": {"import_fail": "sounddevice"},
        "review_by": "2099-01",
    }]})
    assert report.whitelisted == 1
    assert report.defect_count == 0


def test_whitelist_requires_review_by(tmp_path):
    """白名单必须有到期复核标记，防止永久豁免。"""
    test_file = REPO_ROOT / "tests" / "test_audio_selection_e2e.py"
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        import sounddevice\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path, {"entries": [{
        "path": "sample.py", "symbol": "f", "line": 4,
        "reason": "外部边界：可选依赖缺失",
        "user_visible": "该功能不可用并显示提示",
        "test": str(test_file.relative_to(REPO_ROOT)).replace("\\", "/"),
        "trigger": {"import_fail": "sounddevice"},
    }]})
    assert report.whitelisted == 0
    assert report.whitelist_invalid == 1
    assert "review_by" in report.defects[0].whitelist_issue


def test_whitelist_overdue_is_invalid(tmp_path):
    test_file = REPO_ROOT / "tests" / "test_audio_selection_e2e.py"
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        import sounddevice\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path, {"entries": [{
        "path": "sample.py", "symbol": "f", "line": 4,
        "reason": "外部边界：可选依赖缺失",
        "user_visible": "该功能不可用并显示提示",
        "test": str(test_file.relative_to(REPO_ROOT)).replace("\\", "/"),
        "trigger": {"import_fail": "sounddevice"},
        "review_by": "2000-01",
    }]})
    assert report.whitelisted == 0
    assert report.whitelist_overdue == 1
    assert report.defect_count == 1


def _trigger_entry(**overrides):
    entry = {
        "reason": "外部边界：可选依赖缺失",
        "user_visible": "该功能不可用并显示提示",
        "test": "tests/test_audio_selection_e2e.py",
        "trigger": {"import_fail": "sounddevice"},
        "review_by": "2099-01",
    }
    entry.update(overrides)
    return entry


def test_whitelist_trigger_must_be_exercised():
    """反证：trigger 指向测试里不存在的屏蔽方式 → 必须判不合格。

    这是防「改个 test 路径就过」的核心：只校验路径存在不够，必须能在
    测试源码里找到同构的屏蔽语句。
    """
    issue = scanner._entry_issue(
        _trigger_entry(trigger={"import_fail": "no_such_module_xyz"}), REPO_ROOT)
    assert "trigger" in issue
    assert "no_such_module_xyz" in issue


def test_whitelist_trigger_missing_or_unknown_is_invalid():
    assert "trigger" in scanner._entry_issue(
        {k: v for k, v in _trigger_entry().items() if k != "trigger"}, REPO_ROOT)
    assert "trigger" in scanner._entry_issue(
        _trigger_entry(trigger={"unplug_cable": "usb"}), REPO_ROOT)
    assert "trigger" in scanner._entry_issue(
        _trigger_entry(trigger={"import_fail": "sounddevice", "env_absent": "X"}), REPO_ROOT)


def test_real_whitelist_entry_passes_full_gate():
    """仓库里每条白名单都必须通过完整门禁（三问 + trigger + review_by）。"""
    import json
    entries = json.loads(
        (REPO_ROOT / "scripts" / "swallow_whitelist.json").read_text(encoding="utf-8")
    )["entries"]
    assert len(entries) == 2, "新增白名单必须逐条人工复核，这里按实数钉扎"
    for entry in entries:
        assert scanner._entry_issue(entry, REPO_ROOT) == "", entry.get("path")


# ── 白名单降级路径的真实覆盖 ─────────────────────────────────────────


def test_audio_player_degrades_without_sounddevice(monkeypatch):
    """覆盖白名单 audio_out.py:37：sounddevice 缺失时整条音频链降级。"""
    import numpy as np
    from mbdsdr_ai import audio_out

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    reloaded = importlib.reload(audio_out)
    try:
        assert reloaded.sd_available() is False
        player = reloaded.AudioPlayer(sample_rate=48000)
        assert player.available is False
        assert player.start() is False
        assert player.write(np.zeros(256, dtype=np.float32)) == 0
        player.stop()
    finally:
        monkeypatch.undo()
        importlib.reload(audio_out)


# ── 仓库现状 ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def repo_report():
    """仓库扫描一次，供下面两个用例共用（避免重复 1000+ 文件解析）。"""
    return scanner.scan_repo(REPO_ROOT)


def test_whitelist_entries_valid_and_current(repo_report):
    assert repo_report.whitelist_invalid == 0, "白名单条目三问不全或测试文件不存在"
    assert repo_report.whitelist_stale == 0, "白名单条目指向的代码已变化，请更新行号或删除"


def test_scan_is_deterministic(tmp_path):
    """确定性用固定夹具验证；仓库在途文件会被其他线并发改写，不能当夹具。"""
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        pass\n")
    _write(tmp_path, "def h():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        return None\n", name="second.py")
    first = _scan(tmp_path)
    second = _scan(tmp_path)
    assert [(f.key, f.body) for f in first.all_findings] == \
           [(f.key, f.body) for f in second.all_findings]


@pytest.fixture(scope="module")
def baseline():
    return scanner.load_baseline(REPO_ROOT / "docs" / "code-guards-baseline.json")


@pytest.mark.xfail(
    strict=True,
    reason="KNOWN-RED-K06: 静默吞噬棘轮基线 1284（2026-11-06 快照）"
           " < 实测 1397，113 处存量未修；本轮不抬基线、不放宽断言，"
           "见 docs/known-red-register.md K06")
def test_repo_ratchet_within_baseline(repo_report, baseline):
    """棘轮：任何文件/总量不得超过基线；只许降不许升。

    失败时必须让作者直接定位：文件 + 行号 + 符号 + 级别 + 修法提示。
    """
    assert baseline is not None, "缺少 docs/code-guards-baseline.json，门禁失效"
    violations = scanner.check_ratchet(repo_report, baseline)
    if violations:
        by_file = {}
        for f in repo_report.defects:
            by_file.setdefault(f.path, []).append(f)
        details = []
        for v in violations:
            details.append(f"  {v}")
            path = v.split(": ", 1)[0] if ": " in v else ""
            rows = by_file.get(path, [])
            for f in rows[:12]:
                details.append(
                    f"      {f.path}:{f.line} [{f.severity}] {f.symbol}"
                    f" body={f.body} | {f.snippet}")
            if len(rows) > 12:
                details.append(f"      ……该文件共 {len(rows)} 处，其余用 "
                               f"`python scripts/lint_swallow_all.py --list` 查看")
        details.append(
            "  修法：每处要么补日志/状态字段（变 RECORDED 即不计），要么证明是"
            "外部边界后按白名单登记 reason/user_visible/test/trigger/review_by；"
            "不允许修改 docs/code-guards-baseline.json 来抬基线。")
        pytest.fail("棘轮越线（新增静默吞噬）：\n" + "\n".join(details))


def test_repo_forbidden_constructs(repo_report):
    """suppress(Exception) / 假重抛是禁令，存量也应为 0。"""
    assert repo_report.forbidden == 0, "存在 suppress(Exception) 等价绕过"
    fake = [f for f in repo_report.defects if f.body == "FAKE_RERAISE"]
    assert not fake, "存在假重抛：\n" + "\n".join(f"{f.path}:{f.line}" for f in fake[:20])


def test_ratchet_new_file_is_violation(tmp_path):
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path)
    violations = scanner.check_ratchet(report, {"total": 0, "files": {}})
    assert len(violations) == 2  # 新文件 + 总量
    assert any("基线中没有该文件" in v for v in violations)


def test_ratchet_allows_decrease(tmp_path):
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path)
    baseline = {"total": 5, "files": {"sample.py": {"ERROR": 3, "WARN": 2}}}
    assert scanner.check_ratchet(report, baseline) == []


def test_ratchet_blocks_per_file_growth(tmp_path):
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        pass\n"
                     "def h():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path)
    baseline = {"total": 5, "files": {"sample.py": {"ERROR": 1, "WARN": 0}}}
    violations = scanner.check_ratchet(report, baseline)
    assert any("ERROR 2 > 基线 1" in v for v in violations)


# ── 登记账本（registry）：只许「白名单 + 三问 + owner + 期限」 ──────────

def _registry_baseline(**overrides):
    entry = {
        "path": "sample.py", "symbol": "f", "line": 4, "severity": "ERROR",
        "current_ew": "E1 / W0",
        "boundary": "外部边界：可选依赖缺失",
        "degrade": "降级：该功能返回不可用",
        "fallback": "兜底：UI 显示不可用，不假装成功",
        "owner": "某线", "deadline": "2099-01-01",
    }
    entry.update(overrides)
    return {"total": 0, "files": {"sample.py": {"ERROR": 0, "WARN": 0}},
            "registry": [entry]}


def _whitelisted_scan(tmp_path):
    test_file = REPO_ROOT / "tests" / "test_audio_selection_e2e.py"
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        import sounddevice\n"
                     "    except Exception:\n"
                     "        pass\n")
    return _scan(tmp_path, {"entries": [{
        "path": "sample.py", "symbol": "f", "line": 4,
        "reason": "外部边界：可选依赖缺失",
        "user_visible": "该功能不可用并显示提示",
        "test": str(test_file.relative_to(REPO_ROOT)).replace("\\", "/"),
        "trigger": {"import_fail": "sounddevice"},
        "review_by": "2099-01",
    }]})


def test_registry_entry_with_three_answers_passes(tmp_path):
    report = _whitelisted_scan(tmp_path)
    assert scanner.check_ratchet(report, _registry_baseline()) == []
    assert report.registered == 1


def test_registry_missing_answer_is_violation(tmp_path):
    report = _whitelisted_scan(tmp_path)
    violations = scanner.check_ratchet(
        report, _registry_baseline(fallback=""))
    assert any("登记缺项" in v and "fallback" in v for v in violations)


def test_registry_without_whitelist_is_violation(tmp_path):
    """登记不能替代白名单：没有源码级 trigger 校验就放行 → 红。"""
    _write(tmp_path, "def f():\n"
                     "    try:\n"
                     "        g()\n"
                     "    except Exception:\n"
                     "        pass\n")
    report = _scan(tmp_path)
    violations = scanner.check_ratchet(report, _registry_baseline())
    assert any("登记无对应有效白名单" in v for v in violations)


def test_registry_expired_is_violation(tmp_path):
    report = _whitelisted_scan(tmp_path)
    violations = scanner.check_ratchet(
        report, _registry_baseline(deadline="2000-01-01"))
    assert any("登记已过期限" in v for v in violations)
