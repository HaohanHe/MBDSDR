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


def test_all_digit_hash_is_recognised():
    """真提交 12610725 全为数字；旧的「纯数字=日期」过滤会把它永久判成无 hash。"""
    c = vc.classify(
        "已统一 — `12610725` — `cpp/src/core/tokens.h:301` — `kDefaultSampleRateHz`", ROOT)
    assert c.commit == "12610725"


def test_all_digit_date_is_not_a_hash():
    """反向：20261106 形状合法但不是 commit，必须仍判「无 hash」，不能当证据。"""
    c = vc.classify("20261106 已修复", ROOT)
    assert c.status == vc.NO_HASH


def test_real_claim_still_caught_after_negation_decision(tmp_path):
    """裁定「不加否定跳过规则」的反证①：真断言 `已收口` 仍必须被抓并可证实。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, f"- 已收口 — `{h}` — src/foo.py:1 — `MAGIC_TOKEN`\n")
    claims = vc.verify_docs(repo, repo / "docs")
    assert len(claims) == 1
    assert claims[0].status == vc.OK


def test_negated_claim_is_not_silently_green(tmp_path):
    """裁定「靠人工复核兜底」的反证②：否定句仍进扫描集判红，不静默转绿。"""
    repo, _ = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, "- 表中各项是**明确登记的另一些测量点/死参数**，处置如上，不冒充已收口。\n")
    claims = vc.verify_docs(repo, repo / "docs")
    assert len(claims) == 1
    assert claims[0].status == vc.NO_HASH


def test_line_check_contains_literal(tmp_path):
    """第 4 步反证①：正确四元组 → 「含字面量」。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    r = vc.check_line(repo, h, "src/foo.py:1", "MAGIC_TOKEN")
    assert r.result == vc.LINE_OK


def test_line_check_shifted_line_is_misref_when_file_unchanged(tmp_path):
    """第 4 步反证②（红）：行号 +1 指向相邻无关行，且文件自基准以来未改动 → 「错引」。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    r = vc.check_line(repo, h, "src/foo.py:2", "MAGIC_TOKEN", baseline=h)
    assert r.result == vc.LINE_MISLOCATED
    assert r.changes_since == 0
    assert "OTHER" in r.detail


def test_line_check_drift_is_relocate_when_file_changed(tmp_path):
    """第 4 步反证④（黄）：文件自基准以来被改过 → 「需重定位」，带次数与最近提交。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    (repo / "src/foo.py").write_text("NEW_HEADER = 0\nMAGIC_TOKEN = 42\n", encoding="utf-8")
    _git(repo, "add", "src/foo.py")
    _git(repo, "commit", "-q", "-m", "insert header")
    r = vc.check_line(repo, h, "src/foo.py:1", "MAGIC_TOKEN", baseline=h)
    assert r.result == vc.LINE_RELOCATE
    assert r.changes_since == 1
    assert "insert header" in r.latest_change


def test_line_check_missing_baseline_is_relocate_not_red(tmp_path):
    """第 4 步反证⑤：老条目没有坐标基准 → 一律「需重定位」，不得判红。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    r = vc.check_line(repo, h, "src/foo.py:2", "MAGIC_TOKEN")
    assert r.result == vc.LINE_RELOCATE
    assert "无坐标基准" in r.detail


def test_line_check_unresolvable_baseline_is_relocate(tmp_path):
    """第 4 步反证⑥：基准哈希解析不出 commit（打错/被删）→ 同样按黄处理。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    r = vc.check_line(repo, h, "src/foo.py:2", "MAGIC_TOKEN", baseline="deadbeef")
    assert r.result == vc.LINE_RELOCATE


def test_baseline_pointing_at_head_instead_of_blame_is_relocate(tmp_path):
    """第 4 步反证⑦（黄）：基准随手写成「当前 HEAD」但该行由旧提交创建 → 必须判黄。

    基准取值收紧的核心反证：HEAD 永远不是该行的债主。把基准写成 HEAD，会让
    错坐标永久挂在黄牌上，永远转不成红——这正是本轮要堵的姿势。
    """
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    (repo / "README.md").write_text("unrelated\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "unrelated commit")
    head = _git(repo, "rev-parse", "HEAD")
    assert head != h
    r = vc.check_line(repo, h, "src/foo.py:2", "MAGIC_TOKEN", baseline=head)
    assert r.result == vc.LINE_RELOCATE
    assert "不是第 2 行的 blame" in r.detail


def test_misref_is_judged_by_blame_not_by_later_commits(tmp_path):
    """第 4 步反证⑧（红）：文件之后还有提交，但只要基准 == 该行 blame 就判错引。

    旧口径「基准之后有提交 → 黄」在这里会放过一条真错引（changes_since == 1）；
    blame 口径仍判红，且不依赖 HEAD 坐标。
    """
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    (repo / "src/foo.py").write_text("MAGIC_TOKEN = 43\nOTHER = 1\n", encoding="utf-8")
    _git(repo, "add", "src/foo.py")
    _git(repo, "commit", "-q", "-m", "tune token")
    assert vc._blame_commit(repo, "src/foo.py", 2) == h
    r = vc.check_line(repo, h, "src/foo.py:2", "MAGIC_TOKEN", baseline=h)
    assert r.changes_since == 1
    assert r.result == vc.LINE_MISLOCATED


def test_creator_baseline_turns_yellow_once_line_is_edited(tmp_path):
    """第 4 步反证⑨（黄）：基准可以写「创建该行的提交」，但该行被后续提交改过后即过期。

    真实案例：`main_window.py:53` 的 `fill_sample_rate_combo` 由改名提交
    `7bea149a` 创建、后被 `a2be554a` 改签名。基准写创建者时，只有该行内容一直
    没变才等价于 blame；一旦被改，基准就不再是债主 → 判黄并重定位，不得判红。
    """
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    (repo / "src/foo.py").write_text("MAGIC_TOKEN = 42\nOTHER = 2\n", encoding="utf-8")
    _git(repo, "add", "src/foo.py")
    _git(repo, "commit", "-q", "-m", "tune other")
    assert vc._blame_commit(repo, "src/foo.py", 2) != h  # HEAD 的债主已换人
    r = vc.check_line(repo, h, "src/foo.py:2", "MAGIC_TOKEN", baseline=h)
    assert r.result == vc.LINE_RELOCATE
    assert "不是第 2 行的 blame" in r.detail


def test_line_check_unresolved_distinguishes_path_and_range(tmp_path):
    """第 4 步反证③：路径不存在 vs 行号超界，必须可区分（同一大类、不同 sub）。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    missing = vc.check_line(repo, h, "src/nope.py:1", "MAGIC_TOKEN")
    assert missing.result == vc.LINE_UNRESOLVED
    assert missing.detail == vc.LINE_UNRESOLVED_SUB["path-missing"]

    oob = vc.check_line(repo, h, "src/foo.py:99", "MAGIC_TOKEN")
    assert oob.result == vc.LINE_UNRESOLVED
    assert oob.detail.startswith(vc.LINE_UNRESOLVED_SUB["line-out-of-range"])


def test_line_check_range_uses_first_line_only(tmp_path):
    """区间 N–M 只判首行（§7.7）：字面量只在第 2 行时，:1-2 仍判对不上。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "HEADER = 0\nMAGIC_TOKEN = 42\n"})
    assert vc.check_line(repo, h, "src/foo.py:1-2", "MAGIC_TOKEN",
                         baseline=h).result == vc.LINE_MISLOCATED
    assert vc.check_line(repo, h, "src/foo.py:2-3", "MAGIC_TOKEN").result == vc.LINE_OK


def test_line_check_uses_head_not_cited_commit(tmp_path):
    """坐标口径反证：行号按 HEAD 判，不按被引提交当时的坐标。

    构造「提交时第 1 行是 OLD，HEAD 第 1 行是 MAGIC_TOKEN」：
    按 HEAD 判 → 含；若误用 ``git show <hash>:<path>`` → 不含。
    """
    repo, h = _repo(tmp_path, {"src/foo.py": "OLD_NAME = 1\n"})
    (repo / "src/foo.py").write_text("MAGIC_TOKEN = 42\n", encoding="utf-8")
    _git(repo, "add", "src/foo.py")
    _git(repo, "commit", "-q", "-m", "later")
    assert vc.check_line(repo, h, "src/foo.py:1", "MAGIC_TOKEN").result == vc.LINE_OK


def test_baseline_priority_inline_heading_doc(tmp_path):
    """坐标基准三处来源的优先级：本行 > 最近标题 > 文件首部；都没有则空串。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    _write_doc(repo, (
        f"<!-- line-baseline: {h[:7]} -->\n"
        "### 1.1 `src/foo.py:1`\n"
        f"- 已修复 — `{h}` — src/foo.py:1 — `MAGIC_TOKEN`\n"
        f"- 已修复 — `{h}` — src/foo.py:1 — `MAGIC_TOKEN`（基准 `{'0' * 7}{h[:7]}`）\n"
    ))
    bases = [row[4] for row in vc.iter_claim_lines(repo / "docs")]
    assert bases == [h[:7], "0000000" + h[:7]]


def test_baseline_hash_is_not_mistaken_for_evidence(tmp_path):
    """基准写在证据 hash 之前时，不得被当成被引提交（否则必然假 STALE）。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    (repo / "src/bar.py").write_text("LATER = 1\n", encoding="utf-8")
    _git(repo, "add", "src/bar.py")
    _git(repo, "commit", "-q", "-m", "unrelated later")
    after = _git(repo, "rev-parse", "HEAD")
    c = vc.classify(
        f"- 已修复：前置说明（基准 `{after[:7]}`）`{h}` — src/foo.py:1 — `MAGIC_TOKEN`",
        repo, baseline=after[:7])
    assert c.commit == h
    assert c.status == vc.OK


def _waiver_repo(tmp_path: Path, waiver_rows=()) -> Path:
    """造一个「断言坐标偏了（黄）」的仓库，可选写入白名单。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\nOTHER = 1\n"})
    doc = repo / "docs" / "x.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(f"# 文档\n\n- 已修复：`{h}` — `src/foo.py:2` — `MAGIC_TOKEN`\n",
                   encoding="utf-8")
    _git(repo, "add", "docs/x.md")
    _git(repo, "commit", "-q", "-m", "doc")
    if waiver_rows is not None and waiver_rows:
        body = ("# 白名单\n\n| 文档 | target | owner | 到期 | 原因 |\n"
                "|---|---|---|---|---|\n" + "".join(waiver_rows))
        (repo / "docs" / "line-check-waivers.md").write_text(body, encoding="utf-8")
    return repo


def test_strict_lines_fails_on_unwaived_relocate(tmp_path):
    """严格模式：没有白名单时，一条「需重定位」就判失败（闸门是关的）。"""
    repo = _waiver_repo(tmp_path, None)
    assert vc.main(["--root", str(repo), "--check-lines", "--strict-lines"]) == 1


def test_strict_lines_passes_with_valid_waiver(tmp_path):
    repo = _waiver_repo(tmp_path, ["| docs/x.md | src/foo.py:2 | 测试线 | 2099-01-01 | 在途 |\n"])
    assert vc.main(["--root", str(repo), "--check-lines", "--strict-lines"]) == 0


def test_expired_waiver_fails(tmp_path):
    """豁免过期即失败：白名单是临时账，不是永久放行条。"""
    repo = _waiver_repo(tmp_path, ["| docs/x.md | src/foo.py:2 | 测试线 | 2000-01-01 | 在途 |\n"])
    assert vc.main(["--root", str(repo), "--check-lines", "--strict-lines"]) == 1


def test_waiver_over_cap_fails(tmp_path):
    """白名单条数超过 MAX_WAIVERS 即失败，防止靠加豁免无限期放行。"""
    repo = _waiver_repo(tmp_path, [
        "| docs/x.md | src/foo.py:2 | 测试线 | 2099-01-01 | 在途 |\n",
        "| docs/x.md | src/foo.py:1 | 测试线 | 2099-01-01 | 在途 |\n",
    ])
    assert vc.main(["--root", str(repo), "--check-lines", "--strict-lines"]) == 1


def test_zero_waivers_auto_strict(tmp_path):
    """白名单清零后，`--check-lines` 自动转严格（不依赖有人记得加 --strict-lines）。"""
    repo = _waiver_repo(tmp_path, None)
    assert vc.main(["--root", str(repo), "--check-lines"]) == 1


def _shift_repo(tmp_path: Path, waiver_rows=()):
    """造一个「纯插入导致坐标 +1」的仓库：foo.py 第 1 行被顶到第 2 行。"""
    repo, h = _repo(tmp_path, {"src/foo.py": "MAGIC_TOKEN = 42\n"})
    doc = repo / "docs" / "x.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        f"# 文档\n\n- 已修复：`{h}` — `src/foo.py:1` — `MAGIC_TOKEN`"
        f"（基准 `{h[:7]}`）\n", encoding="utf-8")
    _git(repo, "add", "docs/x.md")
    _git(repo, "commit", "-q", "-m", "doc")
    (repo / "src" / "foo.py").write_text("HEADER = 0\nMAGIC_TOKEN = 42\n",
                                        encoding="utf-8")
    _git(repo, "add", "src/foo.py")
    _git(repo, "commit", "-q", "-m", "insert header")
    if waiver_rows:
        body = ("# 白名单\n\n| 文档 | target | owner | 到期 | 原因 |\n"
                "|---|---|---|---|---|\n" + "".join(waiver_rows))
        (repo / "docs" / "line-check-waivers.md").write_text(body,
                                                             encoding="utf-8")
    return repo, doc


def test_strict_lines_never_auto_relocates(tmp_path, capsys):
    """严格模式：纯位移也不许自动改号——自动挪完再报绿等于把漂移藏起来。"""
    repo, doc = _shift_repo(tmp_path)
    before = doc.read_text(encoding="utf-8")
    assert vc.main(["--root", str(repo), "--check-lines", "--strict-lines"]) == 1
    assert doc.read_text(encoding="utf-8") == before
    assert "自动重定位" not in capsys.readouterr().out


def test_normal_mode_auto_relocates_and_logs_shift(tmp_path, capsys):
    """非严格模式：纯插入造成的位移自动改号，并逐条打印 shift 具体值。"""
    repo, doc = _shift_repo(
        tmp_path,
        ["| docs/x.md | src/foo.py:1 | 测试线 | 2099-01-01 | 在途 |\n"])
    assert vc.main(["--root", str(repo), "--check-lines"]) == 0
    assert "src/foo.py:2" in doc.read_text(encoding="utf-8")
    out = capsys.readouterr().out
    assert "shift=+1" in out


def test_repo_docs_have_no_dangling_hash():
    claims = vc.verify_docs(ROOT)
    bad = [c for c in claims if "不存在或不是 commit" in c.reason]
    assert not bad, [(c.doc, c.line, c.reason) for c in bad]
    counts = vc.summarize(claims)
    assert sum(counts.values()) == len(claims)
