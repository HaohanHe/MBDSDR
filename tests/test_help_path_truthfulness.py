# SPDX-License-Identifier: MIT
"""
帮助条目的界面路径 ↔ 源码字面量守门
====================================

帮助文案里写了「设置 → 存储与日志」「顶栏点连接」这类路径。控件一改名、
面板一搬家，文案就变成假话，而用户照着做会一直找不到。

检查本身在 ``scripts/check_help_paths.py``（自动 ``git grep``，不再手维护
「词 → 文件」映射表）：
  * 每个「」里的界面词，必须在生产源码里作为**字符串字面量**出现；
  * ``→`` 链的每一段单独成立（一段找不到就红）；
  * 「设置 → X」的 X 必须真的是 ``settings_panel.PANEL_SECTIONS`` 里渲染的节，
    不能用 schema 侧 ``GROUP_ORDER`` 顶替（面板重排后旧名会假绿）；
  * 文档第三节引用的渲染点行号必须仍落在渲染字面量上（防行号漂移）；
  * 文案承诺的操作结果（采样率整数倍、最短一帧等）必须有代码机制支撑。

反证用例会临时把某个路径词改成源码里不存在的词，断言检查必须变红——
防止检查因为「文案自己就是字符串字面量」而自证通过。

只读源码文本，不启动 Qt、不改 UI。
"""
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "desktop"))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

import troubleshooting as ts  # noqa: E402
import check_help_paths as chp  # noqa: E402


def _help_texts():
    """所有用户可见的帮助条目：精选 + 族级 + 逐码。"""
    texts = dict(ts.NEXT_STEPS)
    texts.update({"族:" + k: v for k, v in ts.VISIBLE_NEXT_STEPS.items()})
    texts.update({"码:" + k: v for k, v in ts.CODE_NEXT_STEPS.items()})
    return texts


def test_every_path_segment_is_a_real_ui_literal():
    """帮助里点名的每个控件/页签，都必须在生产源码里作为字面量存在。"""
    problems = chp.check_paths()
    assert not problems, "\n".join(problems)


def test_settings_path_depth_matches_real_groups():
    """「设置」→「X」：X 必须是设置面板真实渲染的节（PANEL_SECTIONS）。"""
    import re

    groups = set(chp.rendered_settings_sections())
    assert groups, "解析不到 settings_panel.PANEL_SECTIONS"
    problems = []
    for key, text in _help_texts().items():
        for group in re.findall(r"「设置」\s*→\s*「([^」]+)」", text):
            if group not in groups:
                problems.append(f"{key}: 设置面板里没有「{group}」这一节")
    assert not problems, "\n".join(problems)


def test_counterexample_stale_settings_page_turns_red(monkeypatch):
    """反证：旧节名「存储与日志」只活在 schema 分组里，必须报红。"""
    assert "存储与日志" not in chp.rendered_settings_sections()
    monkeypatch.setitem(
        ts.NEXT_STEPS, "disk_full",
        "在「设置」→「存储与日志」把「录音目录」`record_dir` 换盘。")
    problems = chp.check_paths()
    assert any("存储与日志" in p for p in problems), problems


def test_doc_render_points_still_exist():
    """文档里的渲染点行号必须仍落在真实渲染字面量上（行号漂了就红）。"""
    problems = chp.check_render_points()
    assert not problems, "\n".join(problems)


def test_advice_mechanisms_exist_in_code():
    """文案承诺「改了就会生效」的机制，代码里必须真的存在。"""
    problems = chp.check_mechanisms()
    assert not problems, "\n".join(problems)


def test_counterexample_nonexistent_word_turns_red(monkeypatch):
    """反证：把精选条目的路径词改成源码里不存在的词 → 必须报红。"""
    monkeypatch.setitem(
        ts.NEXT_STEPS, "empty_signal",
        "在「频谱」页签确认有信号，再把「控制」面板的「量子门限」往左调松。")
    problems = chp.check_paths()
    assert any("量子门限" in p for p in problems), problems


def test_counterexample_nonexistent_code_step_turns_red(monkeypatch):
    """反证：把逐码建议里的路径词改成不存在的词 → 必须报红。"""
    monkeypatch.setitem(
        ts.CODE_NEXT_STEPS, "crc_failed",
        "在「控制」页签降低「量子带宽」后重录。")
    problems = chp.check_paths()
    assert any("量子带宽" in p for p in problems), problems


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args],
                   check=True, capture_output=True, text=True)


def test_render_points_read_head_not_worktree(tmp_path, monkeypatch):
    """口径反证：工作树有未提交改动、HEAD 干净时，默认绿、--worktree 红。

    并行开发时工作树每一分钟都在被别人改；拿它判「文档漂移」会把「别人正在
    编辑」误报成事实（2026-11-11 夜间 main_window.py 未提交 +59 行的活例）。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ci@example.com")
    _git(repo, "config", "user.name", "ci")
    src = repo / "desktop" / "status_panel.py"
    src.parent.mkdir()
    src.write_text('A = 1\nB = 2\nDECODE_ERROR_LABELS = "错误码 "\nC = 4\n',
                   encoding="utf-8")
    doc = repo / "docs" / "error-code-visibility.md"
    doc.parent.mkdir()
    doc.write_text("## 三、通道\n\n- **P2** `desktop/status_panel.py:3`\n\n## 五、其它\n",
                   encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    # 工作树插一行把渲染点从 :3 推到 :4，不提交
    src.write_text('A = 1\nB = 2\nX = 3\nDECODE_ERROR_LABELS = "错误码 "\nC = 4\n',
                   encoding="utf-8")

    monkeypatch.setattr(chp, "_ROOT", str(repo))
    monkeypatch.setattr(chp, "_DOC", str(doc))
    try:
        chp.set_source("head")
        assert chp.check_render_points() == []
        chp.set_source("worktree")
        problems = chp.check_render_points()
        assert problems and "文档行号漂移" in problems[0], problems
    finally:
        chp.set_source("head")
