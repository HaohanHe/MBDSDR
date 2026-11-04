# SPDX-License-Identifier: MIT
"""`except: pass` 静默吞噬守卫（P13 补充）。

事故背景：`desktop/rf_sky_view.py::_find_body_at` 的 `float(None)` 抛 TypeError，
被 `except Exception: pass` 吞掉，导致所有深空天体既不可见也不可点，且无日志
无报错。静默丢功能比崩溃更难发现，因此本守卫把「catch-all + 无记录」当作缺陷。

判据：
- EMPTY（`pass` / 只有注释）→ ERROR
- 只 `return None` / `continue` / `break` / 无记录降级 → WARN
- 有日志/状态记录，或重新抛出 → 不算静默
- 白名单条目必须同时回答三问：reason / user_visible / test，缺一即不合格

当前仓库存量缺陷数是红的事实来源：`test_repo_has_no_unwhitelisted_silent_catch_all`
会失败并打印 `文件:行号`。这是任务要求的「修前必红」反证，不是测试环境问题。
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
                     "    except Exception:\n"
                     "        raise\n")
    report = _scan(tmp_path)
    assert report.defect_count == 0
    assert report.recorded == 1
    assert report.reraised == 1


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
    test_file = REPO_ROOT / "tests" / "test_swallow_guard.py"
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
    }]})
    assert report.whitelisted == 1
    assert report.defect_count == 0


# ── 白名单降级路径的真实覆盖 ─────────────────────────────────────────


def test_audio_player_degrades_without_sounddevice(monkeypatch):
    """覆盖白名单 audio_out.py:37：sounddevice 缺失时整条音频链降级。"""
    import numpy as np
    from mbdsdr_ai import audio_out

    monkeypatch.setitem(sys.modules, "sounddevice", None)
    reloaded = importlib.reload(audio_out)
    try:
        assert reloaded._SD_AVAILABLE is False
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


def test_repo_has_no_unwhitelisted_silent_catch_all(repo_report):
    """修前必红：存量静默吞噬仍存在，必须按 `文件:行号` 派给各线。"""
    report = repo_report
    if report.defect_count:
        sample = "\n".join(
            f"  {f.path}:{f.line} [{f.severity}/{f.body}] {f.symbol}"
            for f in report.defects[:15]
        )
        pytest.fail(
            f"全仓 catch-all {report.total_catch_all} 处，静默吞噬 {report.silent} 处，"
            f"白名单 {report.whitelisted} 处，须修 {report.defect_count} 处；"
            f"前 15 处：\n{sample}\n"
            f"完整清单：python scripts/lint_swallow_all.py"
        )
