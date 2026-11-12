# SPDX-License-Identifier: MIT
"""claim 台账 ↔ ``docs/COORDINATION.md`` §6.1 交叉校验的反证。

三条反证（2026-11-13 要求）：
(a) 造一个只在台账里的 claim → 必须报「台账有、文档无」；
(b) 造一个只在文档里的 claim → 必须报「文档有、台账无」；
(c) 显式标成历史/已释放的旧记录 → 不报。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_claim_coordination as cc  # noqa: E402

HEAD = (
    "### 6.1 当前占用\n\n"
    "| 文件 | 线名 | 开始时间 | 预计提交 hash |\n"
    "|---|---|---|---|\n"
    "| _（空）_ | | | |\n"
)


def _doc(*rows: str) -> str:
    return HEAD + "".join(row + "\n" for row in rows)


def _registry(*entries) -> dict:
    claims = {}
    for path, owners in entries:
        claims[path] = {
            "path": path,
            "current": {"owner": owners[0]} if owners else None,
            "history": [{"owner": o, "released_at": 1.0} for o in owners],
        }
    return {"version": 1, "claims": claims}


def test_ledger_only_claim_is_reported():
    """(a) 台账里有、文档里没有 → 台账有、文档无。"""
    doc = _doc("| `desktop/other.py` | 他线 | 2026-11-13 01:00 | 随本笔 |")
    reg = _registry(("desktop/other.py", ["他线"]))
    result = cc.cross_check([("desktop/new.py", "新线")], reg, doc)
    assert result["ledger_only"] == ["desktop/new.py owner=新线"], result
    assert result["doc_only"] == [], result


def test_doc_only_claim_is_reported():
    """(b) 文档里有、台账里查不到 → 文档有、台账无。"""
    doc = _doc("| `desktop/ghost.py` | 幽灵线 | 2026-11-13 01:00 | 随本笔 |")
    result = cc.cross_check([], _registry(), doc)
    assert result["doc_only"] == [
        "desktop/ghost.py（文档线名=幽灵线，台账无此文件记录）"], result
    assert result["ledger_only"] == [], result


def test_released_or_historical_row_is_not_reported():
    """(c) 显式标成已释放/历史的旧行 → 两个方向都不报。"""
    doc = _doc(
        "| `desktop/old.py` | 旧线（已释放 2026-11-12） | 2026-11-12 10:00 | `abcdef12` |",
        "| `desktop/older.py` | 旧线（历史） | 2026-11-11 10:00 | `abcdef12` |",
    )
    result = cc.cross_check([], _registry(), doc)
    assert result["doc_only"] == [], result
    assert result["ledger_only"] == [], result


def test_active_claim_matching_row_passes():
    """正向：路径与 owner 都对上时不再报（否则上面的反证可能只是恒红）。"""
    doc = _doc("| `desktop/main_window.py` | 操作逻辑线（先加调用点） | 2026-11-13 01:00 | 随本笔 |")
    reg = _registry(("desktop/main_window.py", ["操作逻辑线"]))
    result = cc.cross_check([("desktop/main_window.py", "操作逻辑线")], reg, doc)
    assert result["ledger_only"] == [] and result["doc_only"] == [], result


def test_code_file_only_dir_level_is_reported():
    """(a) 代码文件只登记了目录级 → 报「仅目录级登记」。"""
    doc = _doc("| `desktop/**` | 操作逻辑线 | 2026-11-13 01:00 | 目录级登记 |")
    reg = _registry(("desktop/main_window.py", ["操作逻辑线"]))
    result = cc.cross_check([("desktop/main_window.py", "操作逻辑线")], reg, doc)
    assert len(result["ledger_only"]) == 1, result
    assert "仅目录级登记" in result["ledger_only"][0], result


def test_readonly_bulk_dir_level_row_is_ok():
    """(b) docs/learn/** 的目录级行可以覆盖其下批量 claim → 不报。"""
    doc = _doc("| `docs/learn/**` | 守卫账本线 | 2026-11-13 05:30 | 目录级登记 |")
    reg = _registry(("docs/learn/a.md", ["守卫账本线"]))
    result = cc.cross_check([("docs/learn/a.md", "守卫账本线")], reg, doc)
    assert result["ledger_only"] == [] and result["doc_only"] == [], result


def test_second_owner_under_pattern_is_reported():
    """(c) 同一模式下出现第二个 owner，目录级行只写第一个 → 报。"""
    doc = _doc("| `docs/learn/**` | 守卫账本线 | 2026-11-13 05:30 | 目录级登记 |")
    reg = _registry(("docs/learn/a.md", ["守卫账本线"]), ("docs/learn/b.md", ["他线"]))
    result = cc.cross_check(
        [("docs/learn/a.md", "守卫账本线"), ("docs/learn/b.md", "他线")], reg, doc)
    assert any("同一模式下还有 owner" in x for x in result["ledger_only"]), result


def test_second_owner_with_own_file_row_is_not_reported():
    """第二个 owner 已用自己的逐条行登记 → 不再报（否则补行也永远红）。"""
    doc = _doc(
        "| `docs/learn/**` | 守卫账本线 | 2026-11-13 05:30 | 目录级登记 |",
        "| `docs/learn/b.md` | 他线 | 2026-11-13 06:17 | 随本笔 |",
    )
    reg = _registry(("docs/learn/a.md", ["守卫账本线"]), ("docs/learn/b.md", ["他线"]))
    result = cc.cross_check(
        [("docs/learn/a.md", "守卫账本线"), ("docs/learn/b.md", "他线")], reg, doc)
    assert result["ledger_only"] == [], result


def test_brace_group_row_expands_to_each_file():
    """``desktop/{a,b}.py`` 花括号组按每个文件参与匹配。"""
    doc = _doc("| `desktop/{a,b}.py` | 组线 | 2026-11-13 01:00 | 随本笔 |")
    reg = _registry(("desktop/a.py", ["组线"]), ("desktop/b.py", ["组线"]))
    result = cc.cross_check(
        [("desktop/a.py", "组线"), ("desktop/b.py", "组线")], reg, doc)
    assert result["ledger_only"] == [] and result["doc_only"] == [], result


def _staged_fixture(tmp_path: Path, doc_text: str):
    """造一份临时文档/台账/占位文件目录，供 --staged 模式跑 main()。"""
    import json
    import time

    doc = tmp_path / "COORD.md"
    doc.write_text(doc_text, encoding="utf-8")
    reg = tmp_path / "claims.json"
    reg.write_text(json.dumps({"version": 1, "claims": {}}), encoding="utf-8")
    markers = tmp_path / "markers"
    markers.mkdir()
    now = time.time()
    (markers / "a.claim").write_text(json.dumps({
        "path": "desktop/main_window.py", "owner": "操作逻辑线",
        "acquired_at": now, "heartbeat_at": now, "ttl_min": 60,
    }), encoding="utf-8")
    return ["--doc", str(doc), "--registry", str(reg), "--markers", str(markers), "--staged"]


def test_staged_mode_blocks_claimed_file_without_row(tmp_path, monkeypatch, capsys):
    """提交前置：暂存文件有活跃 claim 但 §6.1 缺行 → 退出码 3（不是等到事后扫）。"""
    argv = _staged_fixture(tmp_path, _doc())
    monkeypatch.setattr(cc, "staged_paths", lambda root=None: ["desktop/main_window.py"])
    assert cc.main(argv) == 3
    out = capsys.readouterr().out
    assert "缺 §6.1 行 1 条" in out, out
    assert "desktop/main_window.py" in out
    assert "[规矩]" in out, "输出必须带上「提交前就该有行」这条规矩"


def test_staged_mode_passes_with_row_and_ignores_unstaged_claims(tmp_path, monkeypatch, capsys):
    """正向：有行 → 0；且只查暂存文件，未暂存的活跃 claim 不该在这里报。"""
    doc_text = _doc("| `desktop/main_window.py` | 操作逻辑线 | 2026-11-13 06:30 | 随本笔 |")
    argv = _staged_fixture(tmp_path, doc_text)
    monkeypatch.setattr(cc, "staged_paths", lambda root=None: ["desktop/main_window.py"])
    assert cc.main(argv) == 0
    out = capsys.readouterr().out
    assert "缺 §6.1 行 0 条" in out, out

    # 同一份 claim 未进暂存区 → 本模式不报（cheap：只为这次要提交的文件算）
    monkeypatch.setattr(cc, "staged_paths", lambda root=None: ["docs/other.md"])
    assert cc.main(argv) == 0
