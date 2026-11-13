"""退出码表与代码双向自证：改码必须同时改 ``docs/safe-commit.md``。

为什么需要这条：``safe_commit.py`` 的退出码是各线唯一的处置依据，码和文档一旦
各走各的，人只能靠考古猜「5 该干嘛」「7 要不要重试」。这里从源码里抓
``return <N>`` / ``sys.exit(<N>)`` 与文档表格的码集合双向比对：

* 代码里多了一个码、文档没写 → 红（新增码没交代处置）；
* 文档里写了、代码里没有 → 红（文档漂移成摆设）。

反证：在 ``safe_commit.py`` 里临时加一个 ``return 9``（文档不动），本用例转红；
只在文档里加一行 ``| 9 | ... |``（代码不动），同样转红。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE = ROOT / "scripts" / "safe_commit.py"
DOC = ROOT / "docs" / "safe-commit.md"

START = "<!-- exit-codes:start -->"
END = "<!-- exit-codes:end -->"


def _code_exit_codes() -> set[int]:
    """源码里所有 ``return <N>`` / ``sys.exit(<N>)`` 的码（只认字面量数字）。"""
    text = SAFE.read_text(encoding="utf-8")
    codes = {int(m.group(1)) for m in re.finditer(r"^\s*return\s+(\d+)\b", text, re.M)}
    codes |= {int(m.group(1)) for m in re.finditer(r"sys\.exit\(\s*(\d+)\s*\)", text)}
    return codes


def _doc_exit_codes() -> set[int]:
    """文档标记段内，表格第一列是数字的行。"""
    text = DOC.read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END)
    codes: set[int] = set()
    for line in text[start:end].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and re.fullmatch(r"\d+", cells[0]):
            codes.add(int(cells[0]))
    return codes


def test_doc_and_code_exit_codes_are_both_subsets():
    """两个方向都要相等：多一个少一个都算漂移。"""
    code = _code_exit_codes()
    doc = _doc_exit_codes()
    assert code, "源码里一个退出码都没抓到，正则或改写后结构变了"
    assert doc, "文档表格里一个退出码都没抓到，标记或表头被改了"
    assert code - doc == set(), f"代码里有、文档没写的退出码：{sorted(code - doc)}"
    assert doc - code == set(), f"文档写了、代码里没有的退出码：{sorted(doc - code)}"


def test_help_has_one_line_summary_and_points_to_doc():
    """``--help`` 只给一句话版，具体处置指向文档，不把整张表贴进去。"""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(SAFE), "--help"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "docs/safe-commit.md" in out, out
    for code in sorted(_code_exit_codes()):
        assert re.search(rf"{code}\s", out), f"--help 里缺退出码 {code}：\n{out}"
    # 一句话版：每个码最多一行，不许出现表格行
    assert not re.search(r"^\|", out, re.M), out
