# SPDX-License-Identifier: MIT
"""
tests/test_triage_failure.py
============================
``scripts/triage_failure.py`` 的自测：只允许「命中 / 不确定 / 不发声」三种结论，
且**永不改变测试红绿**（恒退出码 0）。

第 1 条用 2026-11-11 第 4 例的真实 stderr 当样本（设置线在途把「调用」与
「定义」分两次写盘，``refresh_font_scale_note`` 只在工作区）。原日志未保留，
这里保留其**全部文本形状**（帧顺序、真实文件、真实行号、异常行），仅把仓库
根前缀参数化成临时仓库，以便在离线环境中复现。
"""
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts"))

import triage_failure as tf  # noqa: E402

# 真实第 4 例的 stderr（{root} 为仓库根；其余逐字保留）
REAL_STDERR = (
    "Traceback (most recent call last):\n"
    '  File "{root}\\tests\\test_planetarium_entry_visibility.py", line 30, in window\n'
    "    w = mw.MainWindow()\n"
    '  File "{root}\\desktop\\main_window.py", line 12343, in __init__\n'
    "    self._build_settings_panel()\n"
    '  File "{root}\\desktop\\settings_panel.py", line 1202, in __init__\n'
    "    self.refresh_font_scale_note()\n"
    "AttributeError: 'SettingsPanel' object has no attribute "
    "'refresh_font_scale_note'\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True, text=True,
    )


def _make_repo(tmp_path: Path, head_files: dict, dirty_files: dict) -> Path:
    """HEAD 里是 head_files；工作区再覆盖 dirty_files（因此变脏）。"""
    repo = tmp_path / "repo"
    for rel, text in head_files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    for rel, text in dirty_files.items():
        (repo / rel).write_text(text, encoding="utf-8")
    return repo


@pytest.fixture
def repo_with_pending_method(tmp_path):
    """HEAD 的 SettingsPanel 没有 refresh_font_scale_note；工作区加上了调用。"""
    return _make_repo(
        tmp_path,
        {"desktop/settings_panel.py":
         "class SettingsPanel:\n"
         "    def __init__(self):\n"
         "        pass\n",
         "desktop/main_window.py": "def build():\n    return 1\n"},
        {"desktop/settings_panel.py":
         "class SettingsPanel:\n"
         "    def __init__(self):\n"
         "        self.refresh_font_scale_note()\n"},
    )


# 1) 真实第 4 例 → 必须判成「疑似在途半成品」
def test_real_4th_case_stderr_is_in_flight(repo_with_pending_method):
    text = REAL_STDERR.format(root=repo_with_pending_method)
    verdicts = tf.triage_text(text, repo_with_pending_method)
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.kind == "in_flight", verdicts
    assert "refresh_font_scale_note" in v.symbol
    assert "settings_panel.py" in v.path
    assert "疑似在途半成品" in v.line()


# 2) 动态注入（类里有 __getattr__）→ 降级「不确定」，不许判死
def test_dynamic_getattr_downgrades_to_uncertain(tmp_path):
    src = ("class SettingsPanel:\n"
           "    def __getattr__(self, name):\n"
           "        return None\n")
    repo = _make_repo(
        tmp_path,
        {"desktop/settings_panel.py": "class SettingsPanel:\n    pass\n"},
        {"desktop/settings_panel.py": src},
    )
    verdicts = tf.triage_text(REAL_STDERR.format(root=repo), repo)
    assert len(verdicts) == 1
    assert verdicts[0].kind == "uncertain", verdicts
    assert "__getattr__" in verdicts[0].reason


# 3) 符号提取失败（异常文本里没有引号符号）→ 降级「不确定」
def test_symbol_extraction_failure_is_uncertain(repo_with_pending_method):
    text = (
        "Traceback (most recent call last):\n"
        '  File "{root}\\desktop\\settings_panel.py", line 1202, in __init__\n'
        "    self.refresh_font_scale_note()\n"
        "AttributeError: object has no attribute\n"
    ).format(root=repo_with_pending_method)
    verdicts = tf.triage_text(text, repo_with_pending_method)
    assert len(verdicts) == 1
    assert verdicts[0].kind == "uncertain", verdicts
    assert "无法从异常文本提取符号" in verdicts[0].reason


# 4) 符号不在 HEAD，但工作区干净 → 更像接口漂移，「不确定」
def test_clean_worktree_missing_symbol_is_uncertain(tmp_path):
    src = ("class SettingsPanel:\n"
           "    def __init__(self):\n"
           "        pass\n")
    repo = _make_repo(tmp_path, {"desktop/settings_panel.py": src}, {})
    verdicts = tf.triage_text(REAL_STDERR.format(root=repo), repo)
    assert len(verdicts) == 1
    assert verdicts[0].kind == "uncertain", verdicts
    assert "接口漂移" in verdicts[0].reason


# 5) 真缺陷对照：符号在 HEAD 里存在 → 一声不吭（交给人工看真缺陷）
def test_symbol_present_in_head_prints_nothing(tmp_path):
    src = ("class SettingsPanel:\n"
           "    def __init__(self):\n"
           "        self.refresh_font_scale_note()\n"
           "    def refresh_font_scale_note(self):\n"
           "        return 1\n")
    repo = _make_repo(tmp_path, {"desktop/settings_panel.py": src},
                      {"desktop/settings_panel.py": src + "\n# touched\n"})
    assert tf.triage_text(REAL_STDERR.format(root=repo), repo) == []


# 6) 符号已写进工作区（半成品窗口已关闭）→ 不许再按在途判
def test_symbol_already_in_worktree_is_reported_as_closed_window(tmp_path):
    repo = _make_repo(
        tmp_path,
        {"desktop/settings_panel.py": "class SettingsPanel:\n    pass\n"},
        {"desktop/settings_panel.py":
         "class SettingsPanel:\n"
         "    def refresh_font_scale_note(self):\n"
         "        return 1\n"},
    )
    verdicts = tf.triage_text(REAL_STDERR.format(root=repo), repo)
    assert len(verdicts) == 1
    assert verdicts[0].kind == "uncertain", verdicts
    assert "窗口已关闭" in verdicts[0].reason


# 7) 纪律：有命中也不改退出码
def test_exit_code_is_zero_even_on_hit(tmp_path, repo_with_pending_method, capsys):
    err = tmp_path / "child_stderr.txt"
    err.write_text(REAL_STDERR.format(root=repo_with_pending_method),
                   encoding="utf-8")
    code = tf.main(["--stderr", str(err), "--repo-root",
                    str(repo_with_pending_method)])
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0
    assert len(out) == 1, out
    assert out[0].startswith("[triage] 疑似在途半成品")
