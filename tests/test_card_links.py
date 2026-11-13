"""速查卡锚点防漂移的反证：锚点按章节号解析，行号由长文标题现算。

覆盖三条判据：
  ① 真卡必须过；
  ② 章节号写错（§6.99）必须红——证明章节号是真去长文里查了，不是只做了字符串替换；
  ③ 写死行号（#L123）必须红——旧机制不许回流；
  ④ 长文标题前后插空行导致行号整体位移，锚点必须仍然过，且打印的行号跟着变——
     证明锚点不靠行号。
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_card_links.py"
CARD = ROOT / "docs" / "COMMIT-INCIDENT-CARD.md"
DOC = ROOT / "docs" / "COORDINATION.md"


def _run(card_path, doc_path=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--card", str(card_path)]
        + (["--doc", str(doc_path)] if doc_path is not None else []),
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _heading_line(text, section):
    """长文里该章节号标题的行号（1 基）；找不到返回 None。"""
    pattern = re.compile(rf"^#{{2,4}}\s+{re.escape(section)}\s")
    for i, line in enumerate(text.splitlines(), 1):
        if pattern.match(line.strip()):
            return i
    return None


def test_real_card_links_pass():
    proc = _run(CARD)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert "PASS" in out


def test_wrong_section_number_goes_red(tmp_path):
    text = CARD.read_text(encoding="utf-8")
    bad = text.replace(
        "[§6.9.6](./COORDINATION.md#6.9.6)",
        "[§6.99](./COORDINATION.md#6.99)",
        1,
    )
    assert bad != text, "反证未生效：找不到 §6.9.6 的锚点"
    target = tmp_path / "CARD_bad_section.md"
    target.write_text(bad, encoding="utf-8")

    proc = _run(target)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "FAIL" in out
    assert "6.99" in out and "没有这个编号的标题" in out, out


def test_hardcoded_line_anchor_goes_red(tmp_path):
    text = CARD.read_text(encoding="utf-8")
    bad = text.replace(
        "[§6.9.6](./COORDINATION.md#6.9.6)",
        "[§6.9.6](./COORDINATION.md#L2956)",
        1,
    )
    assert bad != text, "反证未生效：找不到 §6.9.6 的锚点"
    target = tmp_path / "CARD_line_anchor.md"
    target.write_text(bad, encoding="utf-8")

    proc = _run(target)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, (proc.returncode, out)
    assert "写死了行号" in out and "必须按章节号" in out, out


def test_heading_shift_keeps_anchor_valid(tmp_path):
    doc_text = DOC.read_text(encoding="utf-8")
    original_line = _heading_line(doc_text, "6.9.6")
    assert original_line, "长文里找不到 §6.9.6 标题"

    lines = doc_text.splitlines()
    # 在 §6.9.6 标题前后各插 5 个空行：后面所有行号整体位移
    idx = original_line - 1
    shifted = lines[:idx] + [""] * 5 + [lines[idx]] + [""] * 5 + lines[idx + 1:]
    shifted_text = "\n".join(shifted) + ("\n" if doc_text.endswith("\n") else "")
    doc_copy = tmp_path / "COORDINATION_shifted.md"
    doc_copy.write_text(shifted_text, encoding="utf-8")

    moved_line = _heading_line(shifted_text, "6.9.6")
    assert moved_line == original_line + 5, (original_line, moved_line)

    proc = _run(CARD, doc_copy)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, (proc.returncode, out)
    assert f"§6.9.6 -> L{moved_line}" in out, out
