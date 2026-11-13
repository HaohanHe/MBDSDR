# SPDX-License-Identifier: MIT
"""``Repo.head`` 只能有一个定义，且沿用短 SHA 语义。

背景：``tools/axioms/model.py`` 曾有两个同名 ``head``，前一个返回完整 SHA、
后一个返回 ``--short``，后者覆盖前者。同名两量会让「台账记录在哪个提交上」
取决于最后一次定义，属于公理载体的自洽性缺陷。

反证：在 ``class Repo`` 里再加一个 ``def head``（无论返回完整还是短 SHA），
``test_repo_head_has_single_definition`` 必红。
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "tools" / "axioms" / "model.py"


def _head_defs():
    tree = ast.parse(MODEL.read_text(encoding="utf-8"))
    repo = [n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "Repo"][0]
    return [n for n in repo.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == "head"]


def test_repo_head_has_single_definition():
    defs = _head_defs()
    assert len(defs) == 1, [(d.name, d.lineno) for d in defs]


def test_repo_head_returns_short_sha():
    from tools.axioms.model import Repo

    head = Repo(str(ROOT)).head()
    assert 0 < len(head) <= 12, head
