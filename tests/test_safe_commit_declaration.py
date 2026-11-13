"""safe_commit 的两条「不许悄悄拿别的东西」契约。

一、声明快照（``8982b4f9``）：``--declare`` 之后、提交之前，别的线往同一个
    文件插进来的行必须被拒，且拒信要逐行点名；声明时就已经在的他人改动
    （``--exclude-hunk`` 的对象）不许被误拒；删行比增行更危险，同样要拒；
    确需一起带走时走 ``--allow-foreign-changes``，理由必须进提交说明。

二、提交信息逐字校验（``7a76b097``）：消息只走 stdin，不经过任何外部临时
    文件；提交后从 commit 对象里取回来比对，不一致按退出码 3 事故报出。

每个用例都用真实子进程（``safe_commit.py`` / ``file_claim.py``），不 mock；
只有「落库消息被别线覆盖」这一种要注入故障的地方改 ``_run`` 的读回结果——
真 git 没法在 commit-msg 钩子位置之外制造这个窗口。
"""
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAFE = ROOT / "scripts" / "safe_commit.py"
CLAIM = ROOT / "scripts" / "file_claim.py"
DOC = "doc.md"


def _git(repo, *args, check=False):
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=check,
    )


def _repo(tmp_path, lines):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "safe-commit@example.invalid", check=True)
    _git(repo, "config", "user.name", "safe-commit", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / DOC).write_text("\n".join(lines) + "\n", encoding="utf-8")
    _git(repo, "add", DOC, check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    return repo


def _write(repo, lines):
    (repo / DOC).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_env(repo, locks, owner=""):
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_REPO_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_HOOK_MARKER_DIR"] = str(locks / "hook_ok")
    env["MBDSDR_SESSION_ID"] = owner or "sessionA"
    if owner:
        env["MBDSDR_CLAIM_OWNER"] = owner
    return env


def _claim_env(repo, locks):
    env = os.environ.copy()
    env.pop("GIT_INDEX_FILE", None)
    env["MBDSDR_CLAIM_ROOT"] = str(repo)
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    return env


def _claim(repo, locks, owner):
    return subprocess.run(
        [sys.executable, str(CLAIM), "claim", DOC, "--owner", owner],
        cwd=str(repo), env=_claim_env(repo, locks), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def _release(repo, locks, owner):
    return subprocess.run(
        [sys.executable, str(CLAIM), "release", DOC, "--owner", owner],
        cwd=str(repo), env=_claim_env(repo, locks), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


def _safe(repo, locks, tmp_path, *extra, message="test: safe commit\n", owner=""):
    args = [sys.executable, str(SAFE), "--files", DOC, *extra]
    if message is not None:
        msg = tmp_path / "msg.txt"
        msg.write_text(message, encoding="utf-8")
        args += ["--message-file", str(msg)]
    return subprocess.run(
        args, cwd=str(repo), env=_safe_env(repo, locks, owner),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _head(repo):
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_foreign_lines_after_declaration_are_rejected_line_by_line(tmp_path):
    """复现 ``8982b4f9``：A 声明后 B 插两行，提交必须被拒且逐行点名。

    顺序严格照现场：A claim → 写入 → 声明（钉住快照）→ release → B claim
    → 往同一文件插两行 → release → A 提交。每一步都是独立进程。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])

    assert _claim(repo, locks, "进程A线").returncode == 0
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    declare = _safe(repo, locks, tmp_path, "--declare", message=None, owner="进程A线")
    assert declare.returncode == 0, declare.stdout + declare.stderr
    assert "已记录声明快照" in declare.stdout
    assert _release(repo, locks, "进程A线").returncode == 0

    assert _claim(repo, locks, "进程B线").returncode == 0
    lines[4:4] = ["foreign-a", "foreign-b"]
    _write(repo, lines)
    assert _release(repo, locks, "进程B线").returncode == 0

    before = _head(repo)
    proc = _safe(repo, locks, tmp_path, owner="进程A线")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 4, out
    assert "声明之后有内容变化" in out, out
    assert "+ 现在第 5 行：foreign-a" in out, out
    assert "+ 现在第 6 行：foreign-b" in out, out
    assert "可能属于" in out and "进程B线" in out, out
    assert "进程A线" not in out.split("可能属于")[1], "归属不该指向自己"
    assert _head(repo) == before, "拒绝时不许提交"


def test_waiver_commits_and_records_reason(tmp_path):
    """同上的现场，走 ``--allow-foreign-changes``：能过，理由必须进提交说明。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    assert _safe(repo, locks, tmp_path, "--declare", message=None,
                 owner="进程A线").returncode == 0

    lines[4:4] = ["foreign-a", "foreign-b"]
    _write(repo, lines)

    proc = _safe(
        repo, locks, tmp_path, owner="进程A线",
        message="test: 带走 B 线两行\n\n正文。\n",
    )
    assert proc.returncode == 4, proc.stdout + proc.stderr

    msg = tmp_path / "msg.txt"
    msg.write_text("test: 带走 B 线两行\n\n正文。\n", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable, str(SAFE), "--files", DOC,
            "--message-file", str(msg),
            "--allow-foreign-changes", "两行已与 B 线当面核对，随本笔一起落库",
        ],
        cwd=str(repo), env=_safe_env(repo, locks, "进程A线"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "豁免通过" in out and "拒绝提交" not in out, out
    assert "foreign-a" in _git(repo, "show", "HEAD:doc.md").stdout
    log = _git(repo, "log", "-1", "--format=%B").stdout
    assert "foreign-changes: 两行已与 B 线当面核对，随本笔一起落库" in log, log
    assert "+2 / -0" in log, log


def test_declaration_mismatch_requires_waiver(tmp_path):
    """改一行（+1/-1）也算漂移：豁免段里要写清增删行数，不能只写「改了」。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    assert _safe(repo, locks, tmp_path, "--declare", message=None,
                 owner="进程A线").returncode == 0
    lines[5] = "line6 other"
    _write(repo, lines)

    msg = tmp_path / "msg.txt"
    msg.write_text("test: waiver\n", encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable, str(SAFE), "--files", DOC,
            "--message-file", str(msg),
            "--allow-foreign-changes", "别线第 6 行已核对",
        ],
        cwd=str(repo), env=_safe_env(repo, locks, "进程A线"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    log = _git(repo, "log", "-1", "--format=%B").stdout
    assert "foreign-changes: 别线第 6 行已核对" in log, log
    assert "+1 / -1" in log, log


def test_deleted_line_after_declaration_is_rejected(tmp_path):
    """声明之后删掉一行（丢内容）同样必须拒：比多内容更危险。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    assert _safe(repo, locks, tmp_path, "--declare", message=None,
                 owner="进程A线").returncode == 0

    del lines[5]                       # 第 6 行被谁删了
    _write(repo, lines)

    before = _head(repo)
    proc = _safe(repo, locks, tmp_path, owner="进程A线")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 4, out
    assert "- 声明时第 6 行：line6" in out, out
    assert "（+0 / -1 行）" in out, out
    assert "无法推断" in out, "没有 claim 记录时必须如实说无法推断"
    assert _head(repo) == before


def test_preexisting_foreign_hunk_is_not_drift(tmp_path):
    """边界：声明时就已经在的他人改动不算漂移，``--exclude-hunk`` 照常可用。

    这条混掉以后共享文件就没人敢提交——必须证明「先有他人改动 + 后声明 + 排除」
    能正常落库，且不进豁免段。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    lines[4] = "line5 other"
    _write(repo, lines)
    assert _safe(repo, locks, tmp_path, "--declare", message=None,
                 owner="进程A线").returncode == 0

    proc = _safe(repo, locks, tmp_path, "--exclude-hunk", "doc.md:5-5",
                 "--exclude-owner", "别线", owner="进程A线")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "声明快照无漂移" in out, out
    committed = _git(repo, "show", "HEAD:doc.md").stdout.splitlines()
    assert "line2 mine" in committed
    assert "line5 other" not in committed
    assert "foreign-changes" not in _git(repo, "log", "-1", "--format=%B").stdout


def test_without_declaration_still_commits_and_says_so(tmp_path):
    """没有 ``--declare`` 时不能变成「一律拒绝」：退化到本次启动时刻的基线。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)

    proc = _safe(repo, locks, tmp_path, owner="进程A线")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "无预先声明快照" in out, out
    assert "line2 mine" in _git(repo, "show", "HEAD:doc.md").stdout

    # 提交成功后声明文件必须清掉，否则下一次合法改动会被旧快照一直挡着
    assert not list((locks / "declarations").glob("*.json")), "成功后应清除声明"


def test_declaration_is_cleared_only_after_success(tmp_path):
    """被拒时快照必须留着：清了就等于下次再也查不出东西。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    assert _safe(repo, locks, tmp_path, "--declare", message=None,
                 owner="进程A线").returncode == 0
    assert len(list((locks / "declarations").glob("*.json"))) == 1

    lines[5] = "line6 other"
    _write(repo, lines)
    assert _safe(repo, locks, tmp_path, owner="进程A线").returncode == 4
    assert len(list((locks / "declarations").glob("*.json"))) == 1, "被拒后快照要保留"


def test_concurrent_commits_keep_their_own_messages(tmp_path):
    """两个进程同时提交不同文件、不同 message：各自的 message 必须对得上内容。

    这是 ``7a76b097`` 的正向反证：并发下不许串消息。两个进程共用同一个
    ``<locks>/commit.lock``，落后的那个按基线变化重试。
    """
    locks = tmp_path / "locks"
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", check=True)
    _git(repo, "config", "user.email", "safe-commit@example.invalid", check=True)
    _git(repo, "config", "user.name", "safe-commit", check=True)
    _git(repo, "config", "commit.gpgsign", "false", check=True)
    (repo / DOC).write_text("doc\n", encoding="utf-8")
    (repo / "other.md").write_text("other\n", encoding="utf-8")
    _git(repo, "add", DOC, "other.md", check=True)
    _git(repo, "commit", "-q", "-m", "base", check=True)
    (repo / DOC).write_text("doc changed\n", encoding="utf-8")
    (repo / "other.md").write_text("other changed\n", encoding="utf-8")

    msg_a = tmp_path / "a.txt"
    msg_a.write_text("subject: 文件 A 的改动\n\n只改 doc.md。\n", encoding="utf-8")
    msg_b = tmp_path / "b.txt"
    msg_b.write_text("subject: 文件 B 的改动\n\n只改 other.md。\n", encoding="utf-8")
    env = _safe_env(repo, locks, "并发线")

    procs = [
        subprocess.Popen(
            [sys.executable, str(SAFE), "--files", name,
             "--message-file", str(msg), "--retries", "6"],
            cwd=str(repo), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace",
        )
        for name, msg in ((DOC, msg_a), ("other.md", msg_b))
    ]
    outs = []
    for proc in procs:
        out, err = proc.communicate(timeout=180)
        outs.append(out + err)
        assert proc.returncode == 0, outs[-1]
    for out in outs:
        assert "提交信息逐字校验一致" in out, out

    # 每笔的 message 必须与它改的文件一一对应
    pairs = {}
    for subject in ("subject: 文件 A 的改动", "subject: 文件 B 的改动"):
        shown = _git(repo, "log", "--all", "--format=%H%x00%s").stdout.splitlines()
        sha = next(line.split("\x00")[0] for line in shown if line.endswith(subject))
        pairs[subject] = _git(
            repo, "show", "--pretty=", "--name-only", sha).stdout.split()
    assert pairs["subject: 文件 A 的改动"] == [DOC], pairs
    assert pairs["subject: 文件 B 的改动"] == ["other.md"], pairs


def test_stored_message_must_match_passed_message(tmp_path):
    """提交后逐字复核：落库消息被别线覆盖时必须报出来（退出码 3）。

    真 git 没法在 commit-msg 钩子那里注入故障（临时仓库的钩子不可执行），
    所以这里只替换「从 commit 对象读回消息」这一步：返回一条别线的主题，
    等价于 ``COMMIT_EDITMSG`` 在读回之前被并发提交覆盖后的落库结果。
    反证：去掉 ``_verify_message`` 的调用后本用例返回 0，事故就烂进历史了。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    msg = tmp_path / "msg.txt"
    msg.write_text("我想要的主题\n\n正文只提 doc.md。\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("safe_commit_msgcheck", SAFE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = repo
    mod.LOCKS_DIR = locks

    real_run = mod._run
    hijacked = (
        b"tree deadbeef\nauthor x <x@y> 0 +0000\ncommitter x <x@y> 0 +0000\n\n"
        + "别线的主题\n".encode("utf-8")
    )

    def fake_run(args, env, stdin=None):
        if args[:2] == ["cat-file", "commit"]:
            return subprocess.CompletedProcess(args, 0, hijacked, b"")
        return real_run(args, env, stdin)

    mod._run = fake_run
    try:
        code = mod.main([
            "--files", DOC, "--message-file", str(msg),
        ])
    finally:
        mod._run = real_run

    assert code == 3, "消息对不上必须以事故码 3 报出，不能当成功"
    # 落库的那笔确实是传入的消息（故障只发生在读回），HEAD 存在即可
    assert _git(repo, "log", "-1", "--format=%s").stdout.strip() == "我想要的主题"


def test_message_is_stored_verbatim(tmp_path):
    """``--cleanup=verbatim``：尾随空格/空行不许被 git 悄悄改写。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)

    text = "主题行\n\n正文带尾随空格  \n\n\n"
    proc = _safe(repo, locks, tmp_path, owner="进程A线", message=text)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out

    raw = _git(repo, "cat-file", "commit", "HEAD").stdout
    stored = raw.split("\n\n", 1)[1]
    # subprocess 用 text=True 会把 \r\n 折成 \n，这里按 git 的原样比较
    assert stored.replace("\r\n", "\n") == text, repr(stored)


def test_message_file_inside_git_dir_is_rejected(tmp_path):
    """``b898f4a3`` 的入口：消息文件不许落在 ``.git/`` 这种全仓共享路径。

    ``.git/COMMIT_EDITMSG`` 会被任何 ``git commit`` 覆写；共用它的进程会把
    别人刚写进去的消息当成本笔的说明读走（⑱ 随后还会打印「一致」）。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    msg = repo / ".git" / "COMMIT_EDITMSG"
    msg.write_text("test: 共享消息路径\n", encoding="utf-8")

    before = _head(repo)
    proc = subprocess.run(
        [sys.executable, str(SAFE), "--files", DOC, "--message-file", str(msg)],
        cwd=str(repo), env=_safe_env(repo, locks, "进程A线"),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == 1, out
    assert "拒绝" in out and ".git 目录" in out, out
    assert "逐字校验一致" not in out, out
    assert _head(repo) == before, "拒绝时不许提交"


def test_message_source_rewritten_before_commit_is_refused(tmp_path):
    """来源文件在「读入 → 提交」之间被改写：必须拒绝，且不许打印「一致」。

    复用 ``b898f4a3`` 的形态：两个进程共用一个消息路径，后写的那个会把
    先写的消息换掉。这里在锁内重读基线时改写源文件，等价于第三方插进这段窗口。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    msg = tmp_path / "msg.txt"
    msg.write_text("本笔的消息\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("safe_commit_srcchk", SAFE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = repo
    mod.LOCKS_DIR = locks

    real_run = mod._run
    seen = {"head": 0}

    def fake_run(args, env, stdin=None):
        if args == ["rev-parse", "HEAD"]:
            seen["head"] += 1
            if seen["head"] == 2:            # 锁内重读基线的那一次
                msg.write_text("别线的消息\n", encoding="utf-8")
        return real_run(args, env, stdin)

    before = _head(repo)
    mod._run = fake_run
    try:
        code = mod.main(["--files", DOC, "--message-file", str(msg)])
    finally:
        mod._run = real_run

    assert code == 6, "消息源被改写必须以退出码 6 拒绝"
    assert _head(repo) == before, "拒绝时不许提交"


def test_verification_uses_private_copy_not_the_shared_file(tmp_path):
    """比对端必须是本进程留存的副本：提交后源文件被写坏也不影响判定。

    反证：把 ``_verify_message`` 换回读 ``--message-file``，本用例会以退出码 3
    转红——那正是 ``b898f4a3`` 之前「两头都指向共享文件」的形态。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    msg = tmp_path / "msg.txt"
    msg.write_text("本笔的消息\n", encoding="utf-8")

    spec = importlib.util.spec_from_file_location("safe_commit_copychk", SAFE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.ROOT = repo
    mod.LOCKS_DIR = locks

    real_run = mod._run

    def fake_run(args, env, stdin=None):
        proc = real_run(args, env, stdin)
        if args[:2] == ["commit", "--cleanup=verbatim"]:
            msg.write_text("别线在提交后写进来的消息\n", encoding="utf-8")
        return proc

    mod._run = fake_run
    try:
        code = mod.main(["--files", DOC, "--message-file", str(msg)])
    finally:
        mod._run = real_run

    assert code == 0, "落库消息等于本进程副本，源文件事后被改不应误报"
    stored = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "commit", "HEAD"],
        capture_output=True,
    ).stdout.split(b"\n\n", 1)[1]
    assert stored.decode("utf-8").strip() == "本笔的消息"


def test_shared_index_warning_names_each_owner(tmp_path):
    """共享 index 的告警逐条带 owner：有 claim 写 owner，没有写「无 claim 记录」。

    知道找谁才有处置可能；空白会被误读成「没人认领」。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    (repo / "other.md").write_text("other\n", encoding="utf-8")
    _git(repo, "add", "other.md", check=True)
    _git(repo, "commit", "-q", "-m", "other", check=True)
    claimed = subprocess.run(
        [sys.executable, str(CLAIM), "claim", "other.md", "--owner", "别线/abc"],
        cwd=str(repo), env=_claim_env(repo, locks), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr

    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)
    (repo / "other.md").write_text("other2\n", encoding="utf-8")
    _git(repo, "add", "doc.md", "other.md", check=True)

    proc = _safe(repo, locks, tmp_path, owner="进程A线")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "[warn] 共享 index 有 2 个暂存项" in out, out
    assert "other.md（owner: 别线/abc）" in out, out
    assert "doc.md（owner: 无 claim 记录）" in out, out


def _safe_files(repo, locks, tmp_path, files, *extra, owner="进程A线",
                message="test: 多文件声明\n"):
    """声明任意文件清单跑一次 safe_commit（``_safe`` 只声明 doc.md）。"""
    msg = tmp_path / "msg.txt"
    msg.write_text(message, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SAFE), "--files", *files,
         "--message-file", str(msg), *extra],
        cwd=str(repo), env=_safe_env(repo, locks, owner),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def test_052828d1_shape_only_declared_paths_enter_the_commit(tmp_path):
    """``052828d1`` 形态：未跟踪的新文件只能**因为被写进 --files** 才进提交。

    现场是「声明里列了不该列的文件」，不是「工具偷偷多带」——这两句话的处置
    完全不同，所以这里把两侧都钉住：

    * 清单里只有 ``doc.md`` 时，两个未跟踪的新测试文件必须留在工作区（工具
      碰不到它们：index 从 HEAD 建、只 add 声明清单）；
    * 把其中一个写进 ``--files`` 后它才会进提交——证明入口在声明，不在工具；
    * 声明**之前**就在工作区里的他线内容会照原样落库（⑰ 判的是时间窗，不是
      归属），这是已知边界，不是本用例要修的行为。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    lines[4:4] = ["foreign-before-declare"]      # 声明前就躺在工作区的他线内容
    _write(repo, lines)
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_new_a.py").write_text("# new a\n", encoding="utf-8")
    (tests_dir / "test_new_b.py").write_text("# new b\n", encoding="utf-8")

    proc = _safe_files(repo, locks, tmp_path, [DOC])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "[claim] 该路径无 claim 记录，请确认归属：doc.md" in out, out
    assert _git(repo, "show", "--pretty=", "--name-only", "HEAD").stdout.split() == [DOC]
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").stdout.split()
    assert sorted(untracked) == ["tests/test_new_a.py", "tests/test_new_b.py"], untracked

    # 诚实结论：声明前就在的他线内容进了这笔（⑰ 不判归属，没有别的机制提示）
    assert "foreign-before-declare" in _git(repo, "show", "HEAD:doc.md").stdout

    # 只有显式写进 --files，未跟踪文件才会被带走
    proc = _safe_files(repo, locks, tmp_path, ["tests/test_new_a.py"])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert _git(repo, "show", "--pretty=", "--name-only", "HEAD").stdout.split() == [
        "tests/test_new_a.py"]
    # 共享 index 提交后是陈旧的（形态⑤），不能拿 ls-files --others 判断跟踪状态，
    # 直接看 HEAD 的树：b 不在里面，就说明它没被这笔带走。
    tree = _git(repo, "ls-tree", "-r", "--name-only", "HEAD").stdout.split()
    assert "tests/test_new_a.py" in tree and "tests/test_new_b.py" not in tree, tree


def test_claim_hint_appears_only_without_claim_record(tmp_path):
    """归属提示只提示不拦：无 claim 记录时打印，有记录时不打印，两者都 rc=0。"""
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)

    proc = _safe(repo, locks, tmp_path, "--declare", message=None, owner="进程A线")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "[claim] 该路径无 claim 记录，请确认归属：doc.md" in out, out

    assert _claim(repo, locks, "进程A线").returncode == 0
    proc = _safe(repo, locks, tmp_path, "--declare", message=None, owner="进程A线")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "[claim]" not in out, out


def test_message_coverage_hint_is_visible_but_never_blocking(tmp_path):
    """说明里没提的文件要被看见：提及不打印，未提及打印，两边都 rc=0。

    提示只按 basename 做子串匹配，不要求逐文件罗列——它不是清单作业，只是让
    「内容都对、说明漏了」这件事有出口（`052828d1` / `1004608f` 两次同形）。
    """
    locks = tmp_path / "locks"
    repo = _repo(tmp_path, [f"line{i}" for i in range(1, 7)])
    lines = [f"line{i}" for i in range(1, 7)]
    lines[1] = "line2 mine"
    _write(repo, lines)

    proc = _safe_files(repo, locks, tmp_path, [DOC],
                       message="test: 只改 doc.md 第二行\n")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    # [message] 前缀另有「提交信息逐字校验一致」一行，这里只判「未提及」那一条
    assert "说明未提及" not in out, out

    lines[2] = "line3 mine"
    _write(repo, lines)
    proc = _safe_files(repo, locks, tmp_path, [DOC], message="test: 再改一行\n")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "[message] 说明未提及：doc.md" in out, out
