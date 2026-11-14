# SPDX-License-Identifier: MIT
"""D8：已知红台账的到期判据必须认「转绿条件」，区间标题要逐条展开。

反证（改回去必须红）：
- 删掉 ``collect_d8`` 里的 ``转绿条件`` → ``test_entry_without_expiry_or_green_condition_is_red``
  与 ``test_deleting_green_condition_from_range_flags_each_id`` 必红；
- 把 ``_known_red_entries`` 退回只认单个 ``## Kxx`` → ``test_range_heading_expands_to_each_id``
  与删除转绿条件的用例必红。

运行：python -m pytest tests/test_axioms_d8_known_red.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.axioms.model import Repo  # noqa: E402
from tools.axioms.survey import _known_red_entries, collect_d8  # noqa: E402

REGISTER = """# 已知红台账

## K01–K04：区间条目

- **转绿条件**：把探针 patch 到真实选择链
- **首次记录**：2026-11-01

## K90：两条判据都没有

- **首次记录**：2026-11-01
"""


def _repo_with(tmp: str, register: str) -> Repo:
    docs = os.path.join(tmp, "docs")
    os.makedirs(docs)
    with open(os.path.join(docs, "known-red-register.md"), "w", encoding="utf-8") as fh:
        fh.write(register)
    with open(os.path.join(docs, "axioms-waivers.json"), "w", encoding="utf-8") as fh:
        fh.write('{"waivers": []}')
    return Repo(tmp)


def _d8_snippets(repo: Repo) -> str:
    return "\n".join(f.snippet for f in collect_d8(repo).findings)


def test_range_heading_expands_to_each_id():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with(tmp, REGISTER)
        assert [k for k, _, _ in _known_red_entries(repo)] == \
            ["K01", "K02", "K03", "K04", "K90"]


def test_entry_without_expiry_or_green_condition_is_red():
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with(tmp, REGISTER)
        snippets = _d8_snippets(repo)
        assert "K90" in snippets
        assert "K01" not in snippets


def test_deleting_green_condition_from_range_flags_each_id():
    register = REGISTER.replace("- **转绿条件**：把探针 patch 到真实选择链\n", "")
    with tempfile.TemporaryDirectory() as tmp:
        repo = _repo_with(tmp, register)
        snippets = _d8_snippets(repo)
        for kid in ("K01", "K02", "K03", "K04"):
            assert kid in snippets, f"{kid} 删掉转绿条件后没有被 D8 点名"
