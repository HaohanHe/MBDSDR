#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""verify_claims.py 的自证守卫：规则本身必须先能被反证。

每个用例都在临时 git 仓库里造一个真提交，避免依赖当前仓的历史。
另有一条仓库级断言：docs 里出现的所有 hash 必须能解析成 commit ——
这条现在就是绿的，用来防止把「打错的 hash」当成证据。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_claims as vc  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _repo(tmp_path: Path, files: dict) -> tuple:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ci@example.com")
    _git(repo, "config", "user.name", "ci")
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _write_doc(repo: Path, text: str) -> Path:
    d = repo / "docs"
    d.mkdir(exist_ok=True)
    p = d / "claim.md"
    p.write_text(text, encoding="utf-8")
    return p


def test_full_four_tuple_is_verified(tmp_path):
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, f"- 已修复 — `{h}` — src/foo.py:1 — `MAGIC_TOKEN`\n")
    claims = vc.verify_docs(repo, repo / "docs")
    assert len(claims) == 1
    assert claims[0].status == vc.OK


def test_missing_hash_is_unverified(tmp_path):
    repo, _ = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, "- 已修复：采样率口径统一\n")
    claims = vc.verify_docs(repo, repo / "docs")
    assert claims[0].status == vc.NO_HASH


def test_hash_not_touching_path_is_stale(tmp_path):
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, f"- 已修复 — `{h}` — src/bar.py:1 — `MAGIC_TOKEN`\n")
    claims = vc.verify_docs(repo, repo / "docs")
    assert claims[0].status == vc.STALE
    assert "未改动" in claims[0].reason


def test_literal_not_in_commit_is_stale(tmp_path):
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, f"- 已修复 — `{h}` — src/foo.py:1 — `NOT_THERE`\n")
    claims = vc.verify_docs(repo, repo / "docs")
    assert claims[0].status == vc.STALE
    assert "log -S" in claims[0].reason


def test_unknown_hash_is_stale(tmp_path):
    repo, _ = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, "- 已修复 — `deadbeef` — src/foo.py:1 — `MAGIC_TOKEN`\n")
    claims = vc.verify_docs(repo, repo / "docs")
    assert claims[0].status == vc.STALE
    assert "不存在" in claims[0].reason


def test_headings_and_fences_are_not_claims(tmp_path):
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, (
        "# 已提交视觉改动逐条重判\n"
        "```bash\n"
        f"已修复 {h}\n"
        "```\n"
        f"$ git show {h}\n"
    ))
    assert vc.verify_docs(repo, repo / "docs") == []


def test_path_may_come_from_nearest_heading(tmp_path):
    """证据常写在标题里（### 7.1 `path:line`），断言本体只带 hash 与字面量。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, (
        "### 1.1 `src/foo.py:1`\n"
        f"- 已修复：改的是 `{h}`，字面量 `MAGIC_TOKEN`\n"
    ))
    claims = vc.verify_docs(repo, repo / "docs")
    assert len(claims) == 1
    assert claims[0].status == vc.OK
    assert claims[0].target == "src/foo.py:1"


def test_repo_docs_have_no_dangling_hash():
    claims = vc.verify_docs(ROOT)
    bad = [c for c in claims if "不存在或不是 commit" in c.reason]
    assert not bad, [(c.doc, c.line, c.reason) for c in bad]
    counts = vc.summarize(claims)
    assert sum(counts.values()) == len(claims)
