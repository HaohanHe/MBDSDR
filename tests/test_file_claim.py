#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""file_claim.py 行为测试：真实起进程，不用同进程调两次冒充并发。

契约（2026-11-13 起）：
  * 身份 = claim 时签发的一次性 --token，不是 owner 名字。
    两条同名线各自占文件时，互相 release 必须失败；只有凭据匹配才能释放/续租。
  * 凭据持久化在 `<claim_id>.token` sidecar：claim 后进程退出仍可 release/续租；
    删掉 sidecar 后 release 必须被拒并给出「TTL 过期/takeover」提示，不允许
    退化成按名字放行。
  * 旧格式 claim（占位文件无 token_sha256）不按名字放行：
    release 必须拒绝；只能 claim --adopt-legacy 显式迁移，或过期夺取/takeover。
  * 续租必须带 --reason 并留痕（次数/旧新 TTL/理由/pid/cmdline），累计持有超过
    上限（默认 6h，`--max-hold-min`/`MBDSDR_CLAIM_MAX_HOLD_MIN` 可改）即拒绝续租；
    `audit-renew` 对「无续租记录但跨度 > 1.05×TTL」的老 claim 报「历史不可审计」。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "file_claim.py"
SHARED = "desktop/main_window.py"


def _markers(locks: Path) -> list[Path]:
    return sorted((locks / "claims").glob("*.claim"))


def _sidecars(locks: Path) -> list[Path]:
    return sorted((locks / "claims").glob("*.token"))


def _token(result: subprocess.CompletedProcess, locks: Path, path: str = SHARED) -> str:
    """凭据值只能从 sidecar 读：stdout 里不许出现凭据。

    claim 输出改成「凭据已写入 <path>」之后，测试拿凭据就绕 sidecar；
    顺便把「不回显凭据」变成断言——否则这个泄露面会悄悄长回来。
    """
    hits = []
    for marker in _markers(locks):
        data = json.loads(marker.read_text(encoding="utf-8"))
        if str(data.get("path", "")).replace("\\", "/").endswith(path):
            token_path = marker.with_suffix(".token")
            if token_path.exists():
                hits.append(token_path)
    assert len(hits) == 1, f"{path} 应当恰好有一条 sidecar：{hits}"
    token = hits[0].read_text(encoding="utf-8").strip()
    assert token, "sidecar 是空的"
    output = result.stdout + result.stderr
    assert token not in output, "凭据不得回显到 stdout/stderr"
    return token


def _env(locks: Path, root: Path = REPO) -> dict:
    env = os.environ.copy()
    env["MBDSDR_LOCKS_DIR"] = str(locks)
    env["MBDSDR_CLAIM_ROOT"] = str(root)
    env.pop("PYTHONPATH", None)
    return env


def run(
    locks: Path, *args: str, timeout: float = 60.0, root: Path = REPO
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=root,
        env=_env(locks, root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def spawn(locks: Path, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        env=_env(locks),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def _registry(locks: Path) -> dict:
    path = locks / "claims.json"
    assert path.exists(), f"台账未生成：{path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_two_processes_race_exactly_one_wins(tmp_path: Path) -> None:
    """两个真进程并发 claim 同一文件，恰好一个成功，另一个失败并报持有者。"""
    locks = tmp_path / "locks"
    first = spawn(locks, "claim", SHARED, "--owner", "线A")
    second = spawn(locks, "claim", SHARED, "--owner", "线B")
    out_a, err_a = first.communicate(timeout=60)
    out_b, err_b = second.communicate(timeout=60)

    codes = [first.returncode, second.returncode]
    assert sorted(codes) == [0, 2], f"期望恰好一胜一败，实际 {codes}\nA={out_a}{err_a}\nB={out_b}{err_b}"

    winner_out = out_a if first.returncode == 0 else out_b
    loser_err = err_b if first.returncode == 0 else err_a
    loser_out = out_b if first.returncode == 0 else out_a
    winner_owner = "线A" if first.returncode == 0 else "线B"
    loser_owner = "线B" if first.returncode == 0 else "线A"

    assert "CLAIM OK" in winner_out
    assert winner_owner in winner_out
    denied_text = loser_err + loser_out
    assert "CLAIM DENIED" in denied_text
    assert f"当前持有者={winner_owner}" in denied_text
    assert "已持有=" in denied_text

    status = run(locks, "status", SHARED)
    assert status.returncode == 0
    assert f"owner={winner_owner}" in status.stdout
    assert loser_owner not in status.stdout


def test_same_owner_claim_renews_with_token(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "0.01")
    assert first.returncode == 0, first.stderr
    token = _token(first, locks)
    time.sleep(0.2)
    again = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10",
                "--token", token, "--reason", "测试续租")
    assert again.returncode == 0, again.stderr
    assert "CLAIM RENEWED" in again.stdout
    status = run(locks, "status", SHARED)
    assert "owner=线A" in status.stdout


def test_same_owner_without_credential_cannot_renew(tmp_path: Path) -> None:
    """反证：同名不等于同一持有者——既没 sidecar 也没正确 --token 时不能续租。

    把 cmd_claim 里的 _token_matches 判据删掉后，本用例必须变红（同名即可续租）。
    """
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "同名线")
    assert first.returncode == 0, first.stderr
    token = _token(first, locks)
    for sidecar in _sidecars(locks):
        sidecar.unlink()  # 模拟另一条同名线：拿不到本 claim 的凭据

    no_token = run(locks, "claim", SHARED, "--owner", "同名线", "--ttl-min", "10")
    assert no_token.returncode == 2, no_token.stdout + no_token.stderr
    assert "无法证明是同一持有者" in no_token.stdout + no_token.stderr

    wrong = run(
        locks, "claim", SHARED, "--owner", "同名线", "--ttl-min", "10", "--token", "0" * 32
    )
    assert wrong.returncode == 2, wrong.stdout + wrong.stderr

    # 真持有者凭据仍然有效，并会顺手把 sidecar 补回来
    ok = run(locks, "claim", SHARED, "--owner", "同名线", "--ttl-min", "10",
             "--token", token, "--reason", "凭据有效，继续持有")
    assert ok.returncode == 0, ok.stderr
    assert "CLAIM RENEWED" in ok.stdout
    assert _sidecars(locks), "带正确 --token 续租时应补齐凭据文件"


def test_expired_claim_can_be_stolen(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "0.01")
    assert first.returncode == 0, first.stderr
    time.sleep(1.0)  # TTL = 0.6s

    stolen = run(locks, "claim", SHARED, "--owner", "线B", "--ttl-min", "10")
    assert stolen.returncode == 0, stolen.stderr
    combined = stolen.stdout + stolen.stderr
    assert "原持有者 线A 已超时" in combined
    assert "CLAIM OK (steal)" in stolen.stdout

    status = run(locks, "status", SHARED)
    assert "owner=线B" in status.stdout

    history = _registry(locks)["claims"]
    entry = next(iter(history.values()))
    assert [item["owner"] for item in entry["history"]][:2] == ["线A", "线B"]
    assert entry["history"][0]["closed_reason"] == "expired_steal"


def test_release_by_non_owner_is_denied(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "线A")
    assert claimed.returncode == 0
    token = _token(claimed, locks)

    denied = run(locks, "release", SHARED, "--owner", "线B")
    assert denied.returncode == 2
    text = denied.stdout + denied.stderr
    assert "RELEASE DENIED" in text
    assert "当前持有者=线A" in text

    still = run(locks, "status", SHARED)
    assert "owner=线A" in still.stdout

    ok = run(locks, "release", SHARED, "--owner", "线A", "--token", token)
    assert ok.returncode == 0, ok.stderr
    assert "RELEASE OK" in ok.stdout
    assert run(locks, "status", SHARED).stdout.find("未占用") >= 0


def test_same_name_holders_cannot_release_each_other(tmp_path: Path) -> None:
    """A13 反证：两条同名 holder 各占一个文件，互相 release 必须被拒绝。

    这是「用名字当身份」的原始缺陷：删掉 cmd_release 里的凭据判据后，
    同名即可互释，本用例必须变红。
    """
    locks = tmp_path / "locks"
    other = "desktop/spectrum_widget.py"
    first = run(locks, "claim", SHARED, "--owner", "W81线")
    second = run(locks, "claim", other, "--owner", "W81线")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    token_a, token_b = _token(first, locks, SHARED), _token(second, locks, other)
    assert token_a != token_b, "同名 holder 必须拿到不同凭据"

    # A 用 B 的凭据去释放 B 的文件：凭据哈希不匹配，必须拒绝
    cross = run(locks, "release", other, "--owner", "W81线", "--token", token_a)
    assert cross.returncode == 2, cross.stdout + cross.stderr
    assert "RELEASE DENIED" in cross.stdout + cross.stderr
    assert run(locks, "status", other).stdout.find("owner=W81线") >= 0

    cross_back = run(locks, "release", SHARED, "--owner", "W81线", "--token", token_b)
    assert cross_back.returncode == 2, cross_back.stdout + cross_back.stderr

    # 各自用自己的凭据都能释放
    assert run(locks, "release", SHARED, "--owner", "W81线", "--token", token_a).returncode == 0
    assert run(locks, "release", other, "--owner", "W81线", "--token", token_b).returncode == 0
    assert "未占用" in run(locks, "status", SHARED).stdout
    assert "未占用" in run(locks, "status", other).stdout


def test_legacy_claim_requires_explicit_migration(tmp_path: Path) -> None:
    """旧格式占位文件（无凭据）不按名字放行；--adopt-legacy 可显式迁移一次。"""
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "老格式线")
    assert claimed.returncode == 0, claimed.stderr

    # 模拟落地前写入的占位文件：去掉凭据字段，只留 owner/pid/心跳
    marker = _markers(locks)[0]
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload.pop("token_sha256")
    marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert "id=legacy(名不可作身份)" in run(locks, "status", SHARED).stdout

    no_token = run(locks, "release", SHARED, "--owner", "老格式线")
    assert no_token.returncode == 2, no_token.stdout + no_token.stderr
    assert "旧格式" in no_token.stdout + no_token.stderr

    plain_renew = run(locks, "claim", SHARED, "--owner", "老格式线", "--ttl-min", "10")
    assert plain_renew.returncode == 2, plain_renew.stdout + plain_renew.stderr
    assert "同名不等于同一持有者" in plain_renew.stdout + plain_renew.stderr

    upgraded = run(
        locks, "claim", SHARED, "--owner", "老格式线", "--ttl-min", "10", "--adopt-legacy"
    )
    assert upgraded.returncode == 0, upgraded.stderr
    assert "CLAIM UPGRADED" in upgraded.stdout
    token = _token(upgraded, locks)
    assert "id=token" in run(locks, "status", SHARED).stdout

    assert run(locks, "release", SHARED, "--owner", "老格式线", "--token", token).returncode == 0


def test_token_file_channel_replaces_argv(tmp_path: Path) -> None:
    """凭据走 --token-file：命令行与输出都不出现凭据；两个通道同时给直接拒。"""
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "文件通道线")
    assert claimed.returncode == 0, claimed.stderr
    token = _token(claimed, locks)

    token_file = tmp_path / "presented.token"
    token_file.write_text(token, encoding="utf-8")
    released = run(
        locks, "release", SHARED, "--owner", "文件通道线", "--token-file", str(token_file)
    )
    assert released.returncode == 0, released.stdout + released.stderr
    assert token not in released.stdout + released.stderr

    both = run(
        locks,
        "claim",
        SHARED,
        "--owner",
        "文件通道线",
        "--token",
        token,
        "--token-file",
        str(token_file),
    )
    assert both.returncode == 1, both.stdout + both.stderr
    assert "--token 与 --token-file 只能给一个" in both.stdout + both.stderr


def test_claim_persists_sidecar_and_release_reads_it(tmp_path: Path) -> None:
    """claim 后进程退出，release 不带 --token 也能靠 sidecar 完成。"""
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "线A")
    assert claimed.returncode == 0, claimed.stderr
    token = _token(claimed, locks)

    sidecars = _sidecars(locks)
    assert len(sidecars) == 1, "claim 必须把凭据落到 sidecar"
    assert sidecars[0].read_text(encoding="utf-8").strip() == token
    marker = json.loads(_markers(locks)[0].read_text(encoding="utf-8"))
    assert marker["token_sha256"] != token, "占位文件只存 sha256，不存明文"

    released = run(locks, "release", SHARED, "--owner", "线A")
    assert released.returncode == 0, released.stdout + released.stderr
    assert "RELEASE OK" in released.stdout
    assert not _sidecars(locks), "release 成功后 sidecar 必须删除"

    # 续租同样可以不带 --token（但必须带 --reason）
    again = run(locks, "claim", SHARED, "--owner", "线A")
    run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10", "--reason", "测试续租")
    renewed = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10",
                  "--reason", "测试续租")
    assert again.returncode == 0, again.stderr
    assert renewed.returncode == 0, renewed.stdout + renewed.stderr
    assert "CLAIM RENEWED" in renewed.stdout


def test_missing_sidecar_denies_release_and_says_what_to_do(tmp_path: Path) -> None:
    """反证：删掉 sidecar 后 release 必须被拒，并给出 TTL/takeover 的可操作提示。

    如果实现退化成「没有凭据就按 owner 名放行」，本用例会红。
    """
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "线A")
    assert claimed.returncode == 0, claimed.stderr
    token = _token(claimed, locks)
    for sidecar in _sidecars(locks):
        sidecar.unlink()

    denied = run(locks, "release", SHARED, "--owner", "线A")
    assert denied.returncode == 2, denied.stdout + denied.stderr
    text = denied.stdout + denied.stderr
    assert "凭据缺失或不匹配" in text
    assert "TTL 过期" in text and "takeover" in text
    assert "不会按 owner 名放行" in text
    assert _markers(locks), "被拒绝的 release 不得删掉别人的 claim"

    # 显式 --token 仍然是可用出路
    ok = run(locks, "release", SHARED, "--owner", "线A", "--token", token)
    assert ok.returncode == 0, ok.stdout + ok.stderr


def test_takeover_replaces_sidecar_and_invalidates_old_token(tmp_path: Path) -> None:
    """takeover 后旧 token 与旧 sidecar 都作废，新持有者不带 --token 也能释放。

    用独立 tmp 仓库：接管的新硬条件是「无在途改动」，跑在主仓里会读到别的线
    正在编辑的 `desktop/main_window.py`（那是真实的在途改动，不该接管），
    测试结果会随别人的工作状态漂移。
    """
    root = tmp_path / "repo"
    (root / "desktop").mkdir(parents=True)
    (root / "desktop" / "main_window.py").write_text("base\n", encoding="utf-8")
    for args in (("init", "-q"), ("config", "user.email", "t@example.invalid"),
                 ("config", "user.name", "t"), ("add", "."),
                 ("commit", "-q", "-m", "base")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "线A", root=root)
    assert claimed.returncode == 0, claimed.stderr
    old_token = _token(claimed, locks)

    # 伪造孤儿条件：心跳过期（pid 已不参与判定，写成死号只验证它不影响结果）
    marker = _markers(locks)[0]
    data = json.loads(marker.read_text(encoding="utf-8"))
    data["pid"] = 99999999
    data.pop("pid_create_time", None)
    data["heartbeat_at"] = time.time() - 3600
    data["acquired_at"] = time.time() - 7200
    data["ttl_min"] = 1
    marker.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    taken = run(locks, "takeover", SHARED, "--owner", "线B", "--reason", "原 claim 已过期 60min，工作区干净", root=root)
    assert taken.returncode == 0, taken.stdout + taken.stderr
    new_token = _token(taken, locks)
    assert new_token != old_token
    assert _sidecars(locks)[0].read_text(encoding="utf-8").strip() == new_token

    stale = run(locks, "release", SHARED, "--owner", "线A", "--token", old_token, root=root)
    assert stale.returncode == 2, "旧 token 在 takeover 后必须失效"
    assert "RELEASE DENIED" in stale.stdout + stale.stderr

    ok = run(locks, "release", SHARED, "--owner", "线B", root=root)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert not _sidecars(locks)


def test_allowlisted_claim_hints_print_paths_not_values() -> None:
    """9 行侧通道提示的窄豁免只许打印 sidecar 路径，不许打印凭据值。

    这 9 行带 `# pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值`，
    因此扫描器不再报 `log-credential-value`。豁免的理由必须由测试守住：

      * 结构侧：每个带理由的 print 调用里都不许出现凭据变量（本用例）；
      * 行为侧：claim 的 stdout/stderr 不含凭据值（`_token()` 里的
        `assert token not in output`）。

    反证：把任一行改成打印 `token` / `fresh_token` / `new_token` 等变量，
    本用例先红——这条路径（sidecar 写入失败）平时跑不出来，只能静态守。
    """
    source = SCRIPT.read_text(encoding="utf-8")
    marked = {
        index + 1
        for index, line in enumerate(source.splitlines())
        if "pragma: allowlist secret" in line and "打印的是 sidecar 路径" in line
    }
    assert marked, "file_claim.py 里应当仍有带理由的行内豁免"

    cred_names = {
        "token",
        "fresh_token",
        "upgraded_token",
        "new_token",
        "presented",
        "stored",
    }
    covered: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name) or node.func.id != "print":
            continue
        span = range(node.lineno, (node.end_lineno or node.lineno) + 1)
        hit = marked.intersection(span)
        if not hit:
            continue
        covered.update(hit)
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        leaked = sorted(names & cred_names)
        assert not leaked, (
            f"第 {node.lineno} 行的豁免 print 用到了凭据变量 {leaked}；"
            "这里只能打印 sidecar 路径"
        )
    assert covered == marked, f"未落到 print 调用上的豁免行：{sorted(marked - covered)}"


def test_release_without_claim_fails(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    result = run(locks, "release", SHARED, "--owner", "线A")
    assert result.returncode == 2
    assert "没有 claim" in (result.stdout + result.stderr)


def test_path_normalization_relative_and_absolute(tmp_path: Path) -> None:
    """desktop/main_window.py 与 D:\\mbdsdr\\MBDSDR\\desktop\\main_window.py 同一文件。"""
    locks = tmp_path / "locks"
    assert run(locks, "claim", SHARED, "--owner", "线A").returncode == 0

    absolute = str(REPO / "desktop" / "main_window.py")
    assert run(locks, "claim", absolute, "--owner", "线B").returncode == 2

    forward = (REPO / "desktop/main_window.py").as_posix()
    status = run(locks, "status", forward)
    assert "owner=线A" in status.stdout

    # 反斜杠相对路径同样归一
    assert run(locks, "claim", "desktop\\main_window.py", "--owner", "线B").returncode == 2


def test_status_lists_claims_and_registry_location(tmp_path: Path) -> None:
    locks = tmp_path / "locks"
    assert run(locks, "claim", SHARED, "--owner", "线A").returncode == 0
    assert run(locks, "claim", "desktop/spectrum_widget.py", "--owner", "线B").returncode == 0

    status = run(locks, "status")
    assert status.returncode == 0
    assert "owner=线A" in status.stdout
    assert "owner=线B" in status.stdout
    assert "2 条 claim" in status.stdout

    data = _registry(locks)
    assert json.loads((locks / "claims.json").read_text(encoding="utf-8")) == data
    assert len(data["claims"]) == 2
    # 占位文件与台账都在指定锁目录内（不碰真实 D:\mbdsdr\.locks）
    assert any((locks / "claims").glob("*.claim"))


def test_audit_stale_flags_expired_claim_with_newer_mtime(tmp_path: Path) -> None:
    """反证：造一个过期 claim + 触碰文件 → audit-stale 必须报警（退出码 3）。"""
    locks = tmp_path / "locks"
    target = tmp_path / "narrow_plan.md"
    target.write_text("x", encoding="utf-8")
    claimed = run(locks, "claim", str(target), "--owner", "审计线", "--ttl-min", "0.01")
    assert claimed.returncode == 0, claimed.stderr
    time.sleep(0.8)  # 0.6s TTL 已过
    os.utime(target, None)  # 触碰文件：mtime=现在，晚于 acquired_at
    audit = run(locks, "audit-stale")
    assert audit.returncode == 3, audit.stdout + audit.stderr
    assert "STALE-EDIT" in audit.stdout
    assert "narrow_plan.md" in audit.stdout
    as_json = run(locks, "audit-stale", "--json")
    assert as_json.returncode == 3
    data = json.loads(as_json.stdout)
    assert data["stale_edit"][0]["path"].endswith("narrow_plan.md")
    assert data["stale_edit"][0]["exists"] is True


def test_audit_stale_ignores_fresh_claim_even_if_file_touched(tmp_path: Path) -> None:
    """负例：claim 未过期时，文件 mtime 晚于 acquired_at 属正常持有，不得报警。"""
    locks = tmp_path / "locks"
    target = tmp_path / "fresh.md"
    target.write_text("x", encoding="utf-8")
    claimed = run(locks, "claim", str(target), "--owner", "审计线", "--ttl-min", "60")
    assert claimed.returncode == 0, claimed.stderr
    os.utime(target, None)
    audit = run(locks, "audit-stale")
    assert audit.returncode == 0, audit.stdout
    assert "STALE-EDIT" not in audit.stdout
    assert "AUDIT:" in audit.stdout


def test_case_variant_is_same_file_not_two_markers(tmp_path: Path) -> None:
    """反证：同一文件用不同大小写 claim 两次，第二次必须被认定为「已有 claim」。"""
    locks = tmp_path / "locks"
    first = run(locks, "claim", "desktop/MBDSDR.spec", "--owner", "线A", "--ttl-min", "30")
    assert first.returncode == 0, first.stderr
    second = run(locks, "claim", "desktop/mbdsdr.spec", "--owner", "线B", "--ttl-min", "30")
    assert second.returncode == 2, second.stdout
    assert "线A" in second.stdout + second.stderr
    markers = list((locks / "claims").glob("*.claim"))
    assert len(markers) == 1, [m.name for m in markers]
    registry = _registry(locks)
    assert list(registry["claims"]) == ["desktop/mbdsdr.spec"]


# ---------------------------------------------------------------------------
# A13：台账历史必须绑定凭据身份，不能只按 owner 名字匹配
# ---------------------------------------------------------------------------

def _load_module(locks: Path, monkeypatch):
    """以独立模块实例加载 scripts/file_claim.py，绑定到本用例的临时 locks 目录。

    模块级常量（LOCKS_DIR/REGISTRY_PATH）在 import 时读取 MBDSDR_LOCKS_DIR；
    importlib 起独立实例，不受同进程里其他测试已加载实例的影响。
    """
    import importlib.util

    monkeypatch.setenv("MBDSDR_LOCKS_DIR", str(locks))
    monkeypatch.setenv("MBDSDR_CLAIM_ROOT", str(REPO))
    name = f"file_claim_a13_{abs(hash(str(locks)))}"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_steal_records_credential_in_ledger_history(
    tmp_path: Path, monkeypatch
) -> None:
    """夺取后，台账活跃历史条目必须携带新持有者的凭据哈希。

    反证：`_registry_record_claim` 若不把 token_sha256 写进 history，
    本用例红——历史条目只剩 owner 名字，同名续租就无法区分是哪一个
    持有者在续（A13 的病灶）。
    """
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "0.01")
    assert first.returncode == 0, first.stderr
    time.sleep(1.0)  # TTL = 0.6s，确保过期
    stolen = run(locks, "claim", SHARED, "--owner", "线B", "--ttl-min", "10")
    assert stolen.returncode == 0, stolen.stderr
    assert "CLAIM OK (steal)" in stolen.stdout
    token_b = _token(stolen, locks)

    mod = _load_module(locks, monkeypatch)
    entry = next(iter(_registry(locks)["claims"].values()))
    hist = entry["history"]
    assert len(hist) == 2
    assert hist[0]["released_at"] is not None  # 线A 的条目已关闭
    assert hist[1]["released_at"] is None      # 线B 的条目活跃
    assert hist[1].get("token_sha256") == mod._token_hash(token_b), (
        "台账活跃条目必须携带当前持有者的凭据哈希，"
        "否则同名续租无法区分是哪一个持有者在续"
    )


def test_renew_matches_history_by_credential_not_name(
    tmp_path: Path, monkeypatch
) -> None:
    """A13 行为：同名不同凭据的 renew 不得把心跳续到别人的活跃历史条目上。

    台账层直调（同名两条线各持不同凭据的场景在 CLI 层造不出——同名续租/
    同名 takeover 都被 marker 层拦下；台账层仍须自己守住，因为台账可能与
    marker 不一致：台账写入失败只 WARN，marker 才是权威）：

      * history = [同名线/凭据A 已关闭, 同名线/凭据B 活跃]；
      * 迟到的 renew 自称「同名线」但带着旧凭据 A：
        - 旧实现：按 owner 名匹配 → 把心跳写进凭据 B 的活跃条目（续错身份）；
        - 新实现：凭据不匹配 → 跳过，B 的条目心跳保持不变；
      * 凭据 B 的合法 renew 必须照常更新（防修复变成「一律不续」）。

    注：`_registry_renew` 对 `current` 的覆写不在本修复范围——current 是
    marker 的如实转写，marker 层已做凭据校验。
    """
    locks = tmp_path / "locks"
    mod = _load_module(locks, monkeypatch)
    key, _display = mod.canonical_path(SHARED)

    claim_a = mod._marker_payload(
        _display, key, "同名线", 10.0, 1000.0, mod._token_hash("token-A")
    )
    mod._registry_record_claim(claim_a, None)
    claim_b = mod._marker_payload(
        _display, key, "同名线", 10.0, 2000.0, mod._token_hash("token-B")
    )
    mod._registry_record_claim(claim_b, claim_a, close_reason="expired_steal")

    hist = _registry(locks)["claims"][key]["history"]
    assert len(hist) == 2
    assert hist[0]["released_at"] is not None
    assert hist[1]["released_at"] is None

    # 同名 + 旧凭据 A 的迟到 renew：不许碰凭据 B 的活跃条目
    stale = dict(claim_b)
    stale["token_sha256"] = mod._token_hash("token-A")
    stale["heartbeat_at"] = 3000.0
    mod._registry_renew(stale)
    hist_after = _registry(locks)["claims"][key]["history"]
    assert float(hist_after[1]["last_heartbeat_at"]) == 2000.0, (
        "同名不同凭据的 renew 改动了活跃条目的历史心跳——续错了身份"
    )

    # 凭据 B 的合法 renew：照常更新
    legit = dict(claim_b)
    legit["heartbeat_at"] = 4000.0
    mod._registry_renew(legit)
    hist_legit = _registry(locks)["claims"][key]["history"]
    assert float(hist_legit[1]["last_heartbeat_at"]) == 4000.0


def test_adopt_legacy_binds_credential_into_history(
    tmp_path: Path, monkeypatch
) -> None:
    """adopt-legacy 迁移后，旧格式历史条目（台账也无凭据）必须绑上新凭据。

    反证：`_registry_renew` 若因「条目无凭据 ≠ payload 有凭据」而跳过，
    迁移后的历史条目永远没有凭据，本用例红。
    """
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "老格式线")
    assert claimed.returncode == 0, claimed.stderr
    # 模拟真正的旧版台账：marker 与 history 都没有凭据字段
    marker = _markers(locks)[0]
    payload = json.loads(marker.read_text(encoding="utf-8"))
    payload.pop("token_sha256")
    marker.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    reg_path = locks / "claims.json"
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    entry = next(iter(reg["claims"].values()))
    for item in entry["history"]:
        item.pop("token_sha256", None)
    reg_path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")

    upgraded = run(
        locks, "claim", SHARED, "--owner", "老格式线", "--ttl-min", "10",
        "--adopt-legacy",
    )
    assert upgraded.returncode == 0, upgraded.stderr
    token = _token(upgraded, locks)

    mod = _load_module(locks, monkeypatch)
    hist = next(iter(_registry(locks)["claims"].values()))["history"]
    active = [h for h in hist if h["released_at"] is None]
    assert len(active) == 1
    assert active[0].get("token_sha256") == mod._token_hash(token), (
        "adopt-legacy 必须把新凭据绑进活跃历史条目，否则后续同名续租"
        "又会退化成按名字放行"
    )

    # 迁移完成后，同名无凭据仍然不能续租（既有契约不回退）
    for sidecar in _sidecars(locks):
        sidecar.unlink()
    denied = run(locks, "claim", SHARED, "--owner", "老格式线", "--ttl-min", "10")
    assert denied.returncode == 2, denied.stdout + denied.stderr


def _marker_data(locks: Path) -> dict:
    return json.loads(_markers(locks)[0].read_text(encoding="utf-8"))


def test_renew_without_reason_is_denied_and_leaves_no_trace(tmp_path: Path) -> None:
    """反证(a)：不带 --reason 的续租必须被拒，且不得留下「续过」的假记录。"""
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10")
    assert first.returncode == 0, first.stderr
    token = _token(first, locks)

    denied = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "20",
                 "--token", token)
    assert denied.returncode == 2, denied.stdout + denied.stderr
    both = denied.stdout + denied.stderr
    assert "续租必须带 --reason" in both, both
    assert not _marker_data(locks).get("renewals"), "被拒的续租不得写记录"

    ok = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "20",
             "--token", token, "--reason", "K25 断言补证还没跑完")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    marker = _marker_data(locks)
    assert len(marker["renewals"]) == 1, marker.get("renewals")
    rec = marker["renewals"][0]
    assert rec["reason"] == "K25 断言补证还没跑完"
    assert rec["old_ttl_min"] == 10 and rec["new_ttl_min"] == 20
    assert rec["pid"] and "cmdline" in rec, rec
    assert marker["renew_count"] == 1


def test_renew_rejected_after_cumulative_cap_and_audit_shows_totals(tmp_path: Path) -> None:
    """反证(b)：累计持有超上限即拒绝续租；台账/占位文件能查到次数、总时长、上限来源。"""
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "60",
                "--max-hold-min", "0.02")  # 0.02min = 1.2s
    assert first.returncode == 0, first.stderr
    token = _token(first, locks)
    assert _marker_data(locks)["max_hold_min"] == 0.02
    assert _marker_data(locks)["max_hold_source"] == "--max-hold-min"

    time.sleep(1.5)
    denied = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "60",
                 "--token", token, "--reason", "还想继续", "--max-hold-min", "0.02")
    assert denied.returncode == 2, denied.stdout + denied.stderr
    both = denied.stdout + denied.stderr
    assert "已超上限" in both, both
    assert not _marker_data(locks).get("renewals"), "被拒的续租不得写记录"

    audit = run(locks, "audit-renew", "--json")
    rows = json.loads(audit.stdout)["rows"]
    assert len(rows) == 1
    assert rows[0]["renew_count"] == 0
    assert rows[0]["total_hold_s"] >= 1.2, rows[0]
    assert rows[0]["cap_min"] == 0.02 and rows[0]["over_cap"] is True


def test_audit_renew_flags_legacy_claim_as_unauditable_not_zero(
    tmp_path: Path, monkeypatch
) -> None:
    """反证(c)：老 claim 没有续租记录、但跨度已超一个 TTL → 报「历史不可审计」。

    到期日钉到很远的未来：这条只验「报账」，不因跑测试的日期而变红。
    """
    monkeypatch.setenv("MBDSDR_LEGACY_AUDIT_DUE", "2099-01-01")
    locks = tmp_path / "locks"
    claims_ttl = "10"
    claimed = run(locks, "claim", SHARED, "--owner", "老格式线", "--ttl-min", claims_ttl)
    assert claimed.returncode == 0, claimed.stderr

    marker_path = _markers(locks)[0]
    data = json.loads(marker_path.read_text(encoding="utf-8"))
    now = time.time()
    data.pop("renewals", None)
    data.pop("renew_count", None)
    data["acquired_at"] = now - 3600  # 已持有 1h，TTL 只有 10min → 必然续过
    data["heartbeat_at"] = now
    marker_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    audit = run(locks, "audit-renew")
    assert audit.returncode == 3, audit.stdout + audit.stderr
    assert "UNAUDITABLE" in audit.stdout
    assert "不是 0 次" in audit.stdout
    assert "历史不可审计" in audit.stdout
    assert "清账到期 2099-01-01" in audit.stdout, audit.stdout


def test_audit_renew_legacy_overdue_is_blocking_not_permanent(tmp_path: Path, monkeypatch) -> None:
    """到期未清：过了清账到期日，退出码从 3 升到 4，老数据不能一直「报账」下去。"""
    monkeypatch.setenv("MBDSDR_LEGACY_AUDIT_DUE", "2000-01-01")
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "老格式线", "--ttl-min", "10")
    assert claimed.returncode == 0, claimed.stderr
    marker_path = _markers(locks)[0]
    data = json.loads(marker_path.read_text(encoding="utf-8"))
    now = time.time()
    data.pop("renewals", None)
    data.pop("renew_count", None)
    data["acquired_at"] = now - 3600
    data["heartbeat_at"] = now
    marker_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    audit = run(locks, "audit-renew")
    assert audit.returncode == 4, audit.stdout + audit.stderr
    assert "到期未清" in audit.stdout + audit.stderr
    assert "清账到期 2000-01-01" in audit.stdout + audit.stderr

    payload = json.loads(run(locks, "audit-renew", "--json").stdout)
    assert payload["legacy_overdue"] is True
    assert payload["legacy_due"] == "2000-01-01"
    assert payload["legacy_days_left"] < 0
    assert payload["rows"][0]["legacy_due"] == "2000-01-01"

    # 第二个来源：--legacy-due 优先级高于 env，且来源必须打印出来（放宽要看得到）
    lifted = run(locks, "audit-renew", "--legacy-due", "2099-01-01")
    assert lifted.returncode == 3, lifted.stdout + lifted.stderr
    assert "2099-01-01" in lifted.stdout and "--legacy-due" in lifted.stdout


def test_audit_renew_reports_renew_count_and_reason(tmp_path: Path) -> None:
    """续租留痕的可读面：次数 + 最后理由 + 累计持有，都能从机检里直接读出来。"""
    locks = tmp_path / "locks"
    first = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10")
    assert first.returncode == 0, first.stderr
    token = _token(first, locks)
    renewed = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10",
                  "--token", token, "--reason", "半途被别的事插队")
    assert renewed.returncode == 0, renewed.stderr

    audit = run(locks, "audit-renew", "--json")
    assert audit.returncode == 0, audit.stdout + audit.stderr
    payload = json.loads(audit.stdout)
    assert payload["active"] == 1 and payload["renew_known"] == 1
    row = payload["rows"][0]
    assert row["renew_count"] == 1
    assert row["last_reason"] == "半途被别的事插队"

    reg = json.loads((locks / "claims.json").read_text(encoding="utf-8"))
    hist = next(iter(reg["claims"].values()))["history"]
    active = [h for h in hist if h["released_at"] is None]
    assert active and active[0]["renewals"][0]["reason"] == "半途被别的事插队"
    assert active[0]["renew_count"] == 1
