# SPDX-License-Identifier: MIT
"""§6.1 只追加检查的反证：追加释放标记必须绿，删行与改写必须红。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_coordination_append_only as ck  # noqa: E402

BASE = """# 台账

### 6.1 当前占用

| 文件 | 线名 | 开始时间 | 预计提交 |
| --- | --- | --- | --- |
| `docs/parameter-unification.md` | 文档引用复核线 | 2026-11-13 20:10 | 本笔 |
| `docs/PRD.md` | 文档引用复核线 | 2026-11-13 20:10 | 本笔 |
| `scripts/x.py` | 收尸线 | 2026-11-13 20:20 | 本笔 |

### 6.1.1 说明
"""


def test_unchanged_is_green():
    assert ck.removed_rows(BASE, BASE) == []


def test_append_release_marker_is_green():
    """行末追加释放标记（时间 + 方式）不算改动。"""
    target = BASE.replace(
        "| `scripts/x.py` | 收尸线 | 2026-11-13 20:20 | 本笔 |",
        "| `scripts/x.py` | 收尸线 | 2026-11-13 20:20 | 本笔 | "
        "〔释放 2026-11-13 21:05 released〕",
    )
    assert ck.removed_rows(BASE, target) == []


def test_new_row_is_green():
    target = BASE.replace(
        "| `scripts/x.py` | 收尸线 | 2026-11-13 20:20 | 本笔 |",
        "| `scripts/x.py` | 收尸线 | 2026-11-13 20:20 | 本笔 |\n"
        "| `scripts/y.py` | 收尸线 | 2026-11-13 21:00 | 本笔 |",
    )
    assert ck.removed_rows(BASE, target) == []


def test_delete_row_is_red():
    target = BASE.replace(
        "| `docs/parameter-unification.md` | 文档引用复核线 | 2026-11-13 20:10 | 本笔 |\n",
        "",
    )
    missing = ck.removed_rows(BASE, target)
    assert len(missing) == 1
    assert "docs/parameter-unification.md" in missing[0]


def test_rewrite_row_is_red():
    target = BASE.replace("2026-11-13 20:10", "2026-11-13 20:11", 1)
    missing = ck.removed_rows(BASE, target)
    assert len(missing) == 1
    assert "2026-11-13 20:10" in missing[0]
