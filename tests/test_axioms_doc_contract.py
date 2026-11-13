# SPDX-License-Identifier: MIT
"""公理文档与反证自检源的一致性守卫。

只读文本，不 import 项目模块、不启动 Qt、不联网。

反证（任一条都必须让本文件变红）：
  - 把标题「14 条公理」改回「13 条公理」→ test_title_says_fourteen 红；
  - 从 tools/axioms/selftest.py 删掉任一 ``Case("Axx", ...)`` →
    test_every_axiom_has_selftest_case 红；
  - 删掉文档里对 ``_locator_drift_check`` / ``_cycle_representative_check``
    的例外说明 → test_selftest_contract_states_non_standard_cases 红。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "axioms.md"
SELFTEST = ROOT / "tools" / "axioms" / "selftest.py"
EXPECTED = [f"A{i:02d}" for i in range(1, 15)]


def _doc_text():
    return DOC.read_text(encoding="utf-8-sig")


def _axiom_section():
    text = _doc_text()
    start = text.index("## 14 条公理")
    nxt = text.find("\n## ", start + 1)
    return text[start:] if nxt < 0 else text[start:nxt]


def _table_rows():
    return re.findall(r"^\|\s*(A\d{2})\s*\|", _axiom_section(), re.M)


def test_title_says_fourteen():
    first = _doc_text().splitlines()[0]
    assert "14 条公理" in first, first


def test_section_and_rows_agree():
    assert "## 14 条公理" in _doc_text()
    rows = _table_rows()
    assert len(rows) == 14, rows
    assert sorted(set(rows)) == EXPECTED, rows


def test_every_axiom_has_selftest_case():
    src = SELFTEST.read_text(encoding="utf-8")
    missing = [a for a in EXPECTED
               if not re.search(rf'Case\(\s*"{a}"', src)]
    assert not missing, missing


def test_selftest_contract_states_non_standard_cases():
    text = _doc_text()
    assert "_locator_drift_check" in text
    assert "_cycle_representative_check" in text
