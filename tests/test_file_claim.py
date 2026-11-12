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
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "file_claim.py"
SHARED = "desktop/main_window.py"

_TOKEN_RE = re.compile(r"--token ([0-9a-f]{32})")


def _token(result: subprocess.CompletedProcess) -> str:
    match = _TOKEN_RE.search(result.stdout)
    assert match, f"claim 输出里没有一次性凭据：{result.stdout}{result.stderr}"
    return match.group(1)


def _markers(locks: Path) -> list[Path]:
    return sorted((locks / "claims").glob("*.claim"))


def _sidecars(locks: Path) -> list[Path]:
    return sorted((locks / "claims").glob("*.token"))


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
    token = _token(first)
    time.sleep(0.2)
    again = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10", "--token", token)
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
    token = _token(first)
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
    ok = run(locks, "claim", SHARED, "--owner", "同名线", "--ttl-min", "10", "--token", token)
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
    token = _token(claimed)

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
    token_a, token_b = _token(first), _token(second)
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
    token = _token(upgraded)
    assert "id=token" in run(locks, "status", SHARED).stdout

    assert run(locks, "release", SHARED, "--owner", "老格式线", "--token", token).returncode == 0


def test_claim_persists_sidecar_and_release_reads_it(tmp_path: Path) -> None:
    """claim 后进程退出，release 不带 --token 也能靠 sidecar 完成。"""
    locks = tmp_path / "locks"
    claimed = run(locks, "claim", SHARED, "--owner", "线A")
    assert claimed.returncode == 0, claimed.stderr
    token = _token(claimed)

    sidecars = _sidecars(locks)
    assert len(sidecars) == 1, "claim 必须把凭据落到 sidecar"
    assert sidecars[0].read_text(encoding="utf-8").strip() == token
    marker = json.loads(_markers(locks)[0].read_text(encoding="utf-8"))
    assert marker["token_sha256"] != token, "占位文件只存 sha256，不存明文"

    released = run(locks, "release", SHARED, "--owner", "线A")
    assert released.returncode == 0, released.stdout + released.stderr
    assert "RELEASE OK" in released.stdout
    assert not _sidecars(locks), "release 成功后 sidecar 必须删除"

    # 续租同样可以不带 --token
    again = run(locks, "claim", SHARED, "--owner", "线A")
    run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10")
    renewed = run(locks, "claim", SHARED, "--owner", "线A", "--ttl-min", "10")
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
    token = _token(claimed)
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
    old_token = _token(claimed)

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
    new_token = _token(taken)
    assert new_token != old_token
    assert _sidecars(locks)[0].read_text(encoding="utf-8").strip() == new_token

    stale = run(locks, "release", SHARED, "--owner", "线A", "--token", old_token, root=root)
    assert stale.returncode == 2, "旧 token 在 takeover 后必须失效"
    assert "RELEASE DENIED" in stale.stdout + stale.stderr

    ok = run(locks, "release", SHARED, "--owner", "线B", root=root)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert not _sidecars(locks)


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
