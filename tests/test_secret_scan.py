#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""secret_scan.py 的反证：三条规则必须真实命中，豁免必须只放行被标注的那行。

契约（2026-11-13）：
  * 形状规则（假 ghp_）、argv 规则（--token / subprocess 传凭据变量）、
    日志规则（print api_key）各命中一条，且报告里只出现「文件:行 + 规则名」，
    绝不出现假值本身。
  * 给其中一行加 ``pragma: allowlist secret`` 后，只有那条消失，其余仍然命中。
  * 熵告警单独一档，不拦截（退出码仍由 BLOCK 决定）。
  * --staged 读 index 而不是工作树：工作树洗掉凭据也不能放过已暂存的内容。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "secret_scan.py"
HOOK = REPO / "scripts" / "hooks" / "commit-msg"
FAKE = "ghp_" + "A" * 36
#: 提交信息通道用另一个值，避免和文件扫描用例串味。
MSG_FAKE = "ghp_" + "C" * 36
BLOB = "Qm9ndXNTdHJpbmdXaXRoRW5vdWdoRW50cm9weTEyMzQ1Njc4OTBhYmNkZWY"

#: 真随机 32 字节的 base64（sha256 派生，运行期算，源码里不留长字面量）。
#: 它必须被熵规则看见——降噪只改「分级」，不许把命中改没。
RANDOM_B64 = base64.b64encode(
    hashlib.sha256(b"mbdsdr-entropy-probe").digest()
).decode()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        proc = subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True,
            text=True, encoding="utf-8",
        )
        return proc.stdout

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    return repo


def run_scan(root: Path, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd or REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _leaky_text(with_allowlist: bool = False) -> str:
    log_line = 'print(f"api_key={TOKEN}")\n'
    if with_allowlist:
        log_line = 'print(f"api_key={TOKEN}")  # pragma: allowlist secret\n'
    return (
        "import argparse\n"
        "import subprocess\n"
        f'TOKEN = "{FAKE}"\n'
        "p = argparse.ArgumentParser()\n"
        'p.add_argument("--token")\n'
        'subprocess.run(["curl", "-H", f"Authorization: token {TOKEN}"])\n'
        f"{log_line}"
        f'BLOB = "{BLOB}"\n'
    )


def test_three_rules_hit_and_output_never_leaks_value(tmp_path: Path) -> None:
    leaky = tmp_path / "leaky.py"
    leaky.write_text(_leaky_text(), encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr

    assert result.returncode == 1, text
    assert f"leaky.py:3 github-pat-classic" in text
    assert "leaky.py:5 argv-cli-credential-option" in text
    assert "leaky.py:6 argv-credential-in-command" in text
    assert "leaky.py:7 log-credential-value" in text
    assert "leaky.py:8 high-entropy-string" in text

    # 扫描器自己的输出不能成为新的泄露面
    assert FAKE not in text
    assert BLOB not in text


def test_inline_allowlist_silences_only_that_line(tmp_path: Path) -> None:
    (tmp_path / "leaky.py").write_text(_leaky_text(with_allowlist=True), encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr

    assert "log-credential-value" not in text, text
    # 其余三条仍然必须命中：豁免范围不能外溢
    assert "github-pat-classic" in text
    assert "argv-cli-credential-option" in text
    assert "argv-credential-in-command" in text
    assert "high-entropy-string" in text
    assert FAKE not in text


def test_large_file_still_gets_shape_rules(tmp_path: Path) -> None:
    """超过 2MB 只是不做熵检查：形状规则必须照跑，且流式读不整块进内存。"""
    big = tmp_path / "big.csv"
    filler = "a,b,c\n" * ((3 * 1024 * 1024) // 6)
    big.write_text(filler + f'"{FAKE}",x\n', encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "big.csv" in text, text
    assert "github-pat-classic" in text, text
    assert "大文件仅形状检查 1 个" in text, text
    assert FAKE not in text

    # 删掉那一行：同一体积下必须干净，证明拦的是形状而不是「文件大」。
    big.write_text(filler, encoding="utf-8")
    clean = run_scan(tmp_path, "--root", str(tmp_path))
    text2 = clean.stdout + clean.stderr
    assert clean.returncode == 0, text2
    assert "github-pat-classic" not in text2, text2


def test_staged_mode_checks_index_not_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    leaky = repo / "leaky.py"
    leaky.write_text(f'TOKEN = "{FAKE}"\n', encoding="utf-8")
    git("add", "leaky.py")
    # 工作树洗掉凭据，但暂存区仍保留——闸门必须看 index
    leaky.write_text("TOKEN = None\n", encoding="utf-8")

    result = run_scan(repo, "--staged", cwd=repo)
    text = result.stdout + result.stderr
    assert result.returncode == 1, text
    assert "leaky.py:1 github-pat-classic" in text
    assert FAKE not in text


def test_history_mode_still_sees_secret_deleted_from_head(tmp_path: Path) -> None:
    """工作树 0 BLOCK ≠ 没泄露过：凭据提交过再删掉，历史模式必须仍报。

    反证同时钉住两件事：命中提交仍可从 HEAD 到达（删掉内容的提交是 HEAD
    的祖先），以及输出里仍然只有路径/行号/规则/提交号，没有值。
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    leaky = repo / "leaky.py"
    leaky.write_text(f'TOKEN = "{FAKE}"\n', encoding="utf-8")
    git("add", "leaky.py")
    git("commit", "-q", "-m", "leak")
    leaky.write_text("TOKEN = None\n", encoding="utf-8")
    git("add", "leaky.py")
    git("commit", "-q", "-m", "fix")

    clean = run_scan(repo, "--root", str(repo), cwd=repo)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    assert "HISTORY leaky.py:1 github-pat-classic" in text
    assert "blob 扫描 2 个" in text
    assert "路径=1 个，提交=1 个" in text
    assert "仍可从 HEAD 到达 1 个" in text
    assert FAKE not in text


# ----------------------------------------------------------------------
# 提交信息通道：commit message / annotated tag / notes
# ----------------------------------------------------------------------
def test_history_mode_sees_secret_in_commit_message(tmp_path: Path) -> None:
    """`git commit -m "ghp_…"` 不产生 blob：blob 扫描看不见，消息扫描必须看见。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", f"rotate token {MSG_FAKE}"],
        cwd=repo, check=True, capture_output=True,
    )
    leaked = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
        text=True,
    ).stdout.strip()
    (repo / "a.txt").write_text("b\n", encoding="utf-8")
    subprocess.run(
        ["git", "commit", "-q", "-am", "clean follow-up"],
        cwd=repo, check=True, capture_output=True,
    )

    clean = run_scan(repo, "--root", str(repo), cwd=repo)
    assert clean.returncode == 0, clean.stdout + clean.stderr

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    # 报出的是提交号 + 行号 + 规则名，不是路径（消息不属于任何文件）
    assert f"HISTORY-MSG {leaked[:12]}:1 github-pat-classic" in text, text
    assert "commit message 2 条" in text, text
    assert "提交信息命中：1 条" in text, text
    assert MSG_FAKE not in text


def test_history_mode_sees_secret_in_annotated_tag(tmp_path: Path) -> None:
    """annotated tag 的 message 是独立对象，blob 扫描同样看不见。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "clean message"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v1.0", "-m", f"release {MSG_FAKE}"],
        cwd=repo, check=True, capture_output=True,
    )

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    assert "HISTORY-MSG tag:v1.0:1 github-pat-classic" in text, text
    assert "annotated tag 1 个" in text, text
    assert "提交信息命中：1 条" in text, text
    assert MSG_FAKE not in text


def test_history_mode_sees_secret_in_notes(tmp_path: Path) -> None:
    """`git notes` 也是独立 blob，且不在 rev-list 的路径索引里。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "clean message"],
        cwd=repo, check=True, capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "notes", "add", "-m", f"deploy {MSG_FAKE}", "HEAD"],
        cwd=repo, check=True, capture_output=True,
    )

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    assert f"HISTORY-MSG note:{head[:12]}:1 github-pat-classic" in text, text
    assert "notes 1 条" in text, text
    assert "提交信息命中：1 条" in text, text
    assert MSG_FAKE not in text


def test_history_mode_message_scan_is_zero_when_clean(tmp_path: Path) -> None:
    """清干净后必须是 0 条：证明上面三条命中不是「有消息就报」。"""
    repo = _init_repo(tmp_path)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "clean message"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "tag", "-a", "v1.0", "-m", "release notes"],
        cwd=repo, check=True, capture_output=True,
    )

    history = run_scan(repo, "--history", cwd=repo)
    text = history.stdout + history.stderr
    assert history.returncode == 0, text
    assert "HISTORY-MSG" not in text, text
    assert "提交信息命中：0 条" in text, text
    assert "commit message 1 条" in text, text
    assert "annotated tag 1 个" in text, text


# ----------------------------------------------------------------------
# commit-msg 闸：写入侧拦截（pre-commit 看不见这条通道）
# ----------------------------------------------------------------------
def test_commit_msg_mode_blocks_value_and_no_false_positive_on_prose(
    tmp_path: Path,
) -> None:
    msg = tmp_path / "COMMIT_EDITMSG"
    msg.write_text(f"fix: rotate token\n\nleaked {MSG_FAKE}\n", encoding="utf-8")

    bad = run_scan(tmp_path, "--commit-msg", str(msg))
    text = bad.stdout + bad.stderr
    assert bad.returncode == 1, text
    assert ":3 github-pat-classic" in text, text
    assert MSG_FAKE not in text

    # 只写「不要用 ghp_ 开头」这类说明文字：形状规则要求完整 token，不该误报。
    msg.write_text(
        "docs: 说明不要用 ghp_ 开头的明文凭据，也不要贴 AKIA 样例全串\n",
        encoding="utf-8",
    )
    prose = run_scan(tmp_path, "--commit-msg", str(msg))
    assert prose.returncode == 0, prose.stdout + prose.stderr
    assert "BLOCK=0" in prose.stdout


def test_commit_msg_hook_blocks_real_commit(tmp_path: Path) -> None:
    """把钩子真的装进临时仓库：`git commit -m "ghp_…"` 必须被拒绝。"""
    repo = _init_repo(tmp_path)
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK, hook)
    os.chmod(hook, 0o755)
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True, capture_output=True)

    env = dict(os.environ, MBDSDR_SECRET_SCAN=str(SCRIPT))
    blocked = subprocess.run(
        ["git", "commit", "-q", "-m", f"leak {MSG_FAKE}"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert blocked.returncode != 0
    assert "拒绝提交" in (blocked.stdout + blocked.stderr)
    assert MSG_FAKE not in (blocked.stdout + blocked.stderr)
    count = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True,
        check=False, text=True,
    )
    # 没有任何提交被创建（HEAD 还不存在）
    assert count.returncode != 0

    ok = subprocess.run(
        ["git", "commit", "-q", "-m", "clean message"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", env=env,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr


# ----------------------------------------------------------------------
# 熵告警降噪（2026-11-13）：分级只改「看不看」，一条命中都不许消失
# ----------------------------------------------------------------------
def test_entropy_still_hits_random_base64_in_python_source(tmp_path: Path) -> None:
    """反证 (a)：普通 .py 里的 32 字节随机 base64 必须仍然命中（落 SAMPLE）。"""
    src = tmp_path / "module.py"
    src.write_text(f'PAYLOAD = "{RANDOM_B64}"\n', encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert result.returncode == 0, text  # 熵档不拦提交
    assert "module.py:1 high-entropy-string" in text, text
    assert "SAMPLE " in text, text
    assert "MUST=0" in text and "SAMPLE=1" in text, text
    assert RANDOM_B64 not in text


def test_entropy_env_file_is_must(tmp_path: Path) -> None:
    """反证 (b)：.env 里的高熵值必须进「必须看」。"""
    (tmp_path / ".env").write_text(f"API_KEY={RANDOM_B64}\n", encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert ".env:1:" in text, text
    assert "MUST  " in text, text
    assert "MUST=1" in text and "SAMPLE=0" in text, text
    assert RANDOM_B64 not in text


def test_entropy_json_api_key_is_must(tmp_path: Path) -> None:
    """反证 (c)：.json 的 api_key 字段值必须进「必须看」（字段名紧邻）。"""
    (tmp_path / "config.json").write_text(
        '{"api_key": "%s"}\n' % RANDOM_B64, encoding="utf-8"
    )

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert "config.json:1:" in text, text
    assert "MUST  " in text, text
    assert "MUST=1" in text and "SAMPLE=0" in text, text
    assert RANDOM_B64 not in text


def test_long_blobs_in_assets_stay_sample(tmp_path: Path) -> None:
    """minified JS 与 base64 图片数据必须仍在，但只落「抽样看」。"""
    (tmp_path / "app.min.js").write_text(
        f'var a={{data:"{RANDOM_B64 * 8}"}};\n', encoding="utf-8"
    )
    (tmp_path / "page.html").write_text(
        f'<img src="data:image/png;base64,{RANDOM_B64 * 4}">\n', encoding="utf-8"
    )

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert "app.min.js:1 high-entropy-string" in text, text
    assert "page.html:1 high-entropy-string" in text, text
    assert "SAMPLE " in text, text
    assert "MUST=0" in text and "SAMPLE=2" in text, text


def test_prose_file_path_before_colon_is_not_must(tmp_path: Path) -> None:
    """`tokens.h:<长串>` 是文档里的「文件路径 + 标识符」，不是 `token:` 字段。"""
    (tmp_path / "notes.md").write_text(
        f"see `cpp/src/core/tokens.h:{RANDOM_B64}`\n", encoding="utf-8"
    )

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert "notes.md:1 high-entropy-string" in text, text
    assert "MUST=0" in text and "SAMPLE=1" in text, text


def test_entropy_stats_reports_distribution(tmp_path: Path) -> None:
    """`--entropy-stats` 的分层口径必须与扫描一致（SAMPLE 每文件一条 / MUST 每命中一条）。"""
    (tmp_path / "a.py").write_text(f'X = "{RANDOM_B64}"\n', encoding="utf-8")
    (tmp_path / ".env").write_text(f"API_KEY={RANDOM_B64}\n", encoding="utf-8")
    (tmp_path / "b.json").write_text('{"api_key": "%s"}\n' % RANDOM_B64, encoding="utf-8")
    (tmp_path / "c.min.js").write_text(
        f'var a={{data:"{RANDOM_B64 * 8}"}};\n', encoding="utf-8"
    )

    result = run_scan(tmp_path, "--entropy-stats", "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert result.returncode == 0, text
    assert "SAMPLE 每文件一条 / MUST 每命中一条" in text, text
    assert "总=4 must=2 sample=2" in text, text
    for section in ("按后缀", "按目录（前两层）", "按分数区间", "按分级"):
        assert section in text, text
    assert "MUST " in text and RANDOM_B64 not in text, text


# ----------------------------------------------------------------------
# MUST 破例（2026-11-13）：同一文件命中几处就报几处
# ----------------------------------------------------------------------
def test_must_reports_every_value_in_one_file(tmp_path: Path) -> None:
    """同一个 .json 里两枚 api_key 值都必须出现：分行、同行两种写法都不许吞。"""
    first = base64.b64encode(hashlib.sha256(b"probe-one").digest()).decode()
    second = base64.b64encode(hashlib.sha256(b"probe-two").digest()).decode()
    (tmp_path / "two_lines.json").write_text(
        '{"api_key": "%s"}\n{"api_key": "%s"}\n' % (first, second), encoding="utf-8"
    )
    (tmp_path / "one_line.json").write_text(
        '{"api_key": "%s", "api_key": "%s"}\n' % (first, second), encoding="utf-8"
    )

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert result.returncode == 0, text
    assert "two_lines.json:1:" in text and "two_lines.json:2:" in text, text
    # 同一行两枚值靠列号区分，不能被 set() 去重成一条。
    assert text.count("one_line.json:1:") == 2, text
    assert "MUST=4" in text and "SAMPLE=0" in text, text
    assert first not in text and second not in text, text


# ----------------------------------------------------------------------
# 无后缀凭据面（.env 洞的同类出口）：整名必须进 MUST
# ----------------------------------------------------------------------
NO_SUFFIX_CREDENTIAL_FILES = (
    ".netrc",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    ".htpasswd",
)


def test_no_suffix_credential_files_are_all_must(tmp_path: Path) -> None:
    """七个无后缀凭据面逐个进 MUST：文件名判据不能只靠 `Path(...).suffix`。"""
    for name in NO_SUFFIX_CREDENTIAL_FILES:
        # 内容故意不带凭据字段名，逼着分级只能靠「文件名」
        (tmp_path / name).write_text(f'PAYLOAD = "{RANDOM_B64}"\n', encoding="utf-8")

    result = run_scan(tmp_path, "--root", str(tmp_path))
    text = result.stdout + result.stderr
    assert result.returncode == 0, text
    for name in NO_SUFFIX_CREDENTIAL_FILES:
        assert f"{name}:1:" in text, f"{name} 未进 MUST\n{text}"
    assert f"MUST={len(NO_SUFFIX_CREDENTIAL_FILES)}" in text, text
    assert "SAMPLE=0" in text, text


# ----------------------------------------------------------------------
# 两条 MUST 的归宿：已知良性登记（路径 + 规则名 + 值 sha256 三项匹配）
# ----------------------------------------------------------------------
_BENIGN_FIXTURES = (
    REPO.parent / "scratch/refs/OnAir/tests/mesh/test_mesh_proto_rs.cpp",
    REPO.parent / "scratch/refs/deepseek-harness/apps/desktop/tests/macos-signature.spec.ts",
)


def test_known_benign_entropy_needs_all_three(tmp_path: Path) -> None:
    """登记放行要求路径/规则/值三项同时匹配；路径挪走或值改了必须重新进 MUST。"""
    missing = [p for p in _BENIGN_FIXTURES if not p.exists()]
    if missing:
        pytest.skip("第三方参照克隆不在本机：" + ", ".join(str(p) for p in missing))

    for fixture in _BENIGN_FIXTURES:
        rel = fixture.relative_to(REPO.parent)
        scanned = run_scan(REPO, str(fixture))
        scanned_text = scanned.stdout + scanned.stderr
        assert "MUST " not in scanned_text, f"{fixture.name} 登记未生效\n{scanned_text}"

        # 解除条件 ①：值改了（sha256 不同）→ 路径仍是登记路径也必须重新报 MUST。
        mutated = tmp_path / "mutated" / rel
        mutated.parent.mkdir(parents=True, exist_ok=True)
        mutated.write_text(
            re.sub(
                r"[A-Za-z0-9+/=_-]{32,}",
                lambda m: m.group(0)[:-1] + ("A" if m.group(0)[-1] != "A" else "B"),
                fixture.read_text(encoding="utf-8"),
            ),
            encoding="utf-8",
        )
        changed = run_scan(REPO, str(mutated))
        assert "MUST " in (changed.stdout + changed.stderr), f"{fixture.name} 改值后未报"

    # 解除条件 ②：同值挪到登记路径之外 → 必须重新报 MUST。
    rel = "scratch/refs/OnAir/tests/mesh/test_mesh_proto_rs.cpp"
    moved = tmp_path / "elsewhere" / "test_mesh_proto_rs.cpp"
    moved.parent.mkdir(parents=True, exist_ok=True)
    moved.write_text((REPO.parent / rel).read_text(encoding="utf-8"), encoding="utf-8")
    moved_result = run_scan(REPO, str(moved))
    assert "MUST " in (moved_result.stdout + moved_result.stderr), moved_result.stdout
