#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""共享文件占用锁：用操作系统级互斥，替代「动手前看一眼 git status」。

背景（2026-11-05 同一天三次同类事故）：
  `git commit -- <path>` 提交的是**该路径工作区的全部内容**，不是「我以为
  属于我的那几块」。于是别人未提交的 hunk 被带走，HEAD 出现 ImportError /
  KeyError。「开工前看 status 干净」挡不住——看的瞬间干净，改的过程中别人
  会插进来。纪律靠不住，所以把它做成会失败的检查。

机制：
  * 每个被 claim 的文件对应一个占位文件（`<sha1(路径)>.claim`），首次占用
    用 `O_CREAT|O_EXCL` 原子创建，**第二次 claim 由操作系统裁决失败**；
    续租/超时夺取/释放都在占位文件自身的字节锁（Windows `msvcrt.locking`，
    POSIX `flock`）里完成，不做「读 JSON 再写 JSON」的竞态写法。
  * TTL 心跳：同 owner 再 claim = 续租；超过 TTL 无续租视为过期，别人可以
    夺取，并打印原持有者与超时分钟数。
  * **续租必须留痕、而且有累计上限**（2026-11-13 夜）：此前续租只刷新
    `heartbeat_at`，`acquired_at` 与凭据哈希都不变，也不写 history——一个持有者
    只要每 TTL 续一次，就能**无限期持有且不留任何痕迹**，TTL 对「一直续的人」
    形同不存在（当晚发现 `docs/code-guards.md` 被同一 owner 从 TTL 60 续到 120，
    靠人工盯才没有变成一张永远锁着的门）。现在的规则：
      - 续租必须带 `--reason`（必填），每次追加一条记录：时间、旧 TTL、新 TTL、
        理由、pid、cmdline；占位文件与台账各存一份（各保留最近 50 条）。
      - 单条 claim 累计持有上限默认 **6 小时**（`MBDSDR_CLAIM_MAX_HOLD_MIN` 或
        `--max-hold-min` 可改）；超过即**拒绝续租**，不是告警放行——原因：选
        「告警 + 理由」等于把上限降级成减速带，仍允许无限持有，只是多写一行日志，
        而本次要消灭的正是「无限持有且事后不可审计」。被拒后 claim 按原 TTL 自然
        过期，想继续就 release 后重新 claim：那会开一条**新的取得记录**
        （新的 acquired_at + 凭据），台账能看出「这是第二次取得」，不是同一笔
        悄悄续了 8 小时。
      - 上限被改（env/flag）不是静默生效：`max_hold_min` + 来源会写进占位文件与
        每次续租记录，续租时打印 WARN——「临时放宽」必须留痕。
      - `audit-renew` 子命令给出「续过几次 / 累计多久 / 是否超上限」；对**没有
        续租记录**却有「跨度 > 1.05×TTL」的老 claim，报「历史不可审计」，
        不按 0 次处理（那正是本次事故的形态）。这类老数据**有清账到期日**
        （默认 2026-11-20，`--legacy-due` 或 `MBDSDR_LEGACY_AUDIT_DUE` 可改、来源会打印）：到期前
        退出码 3（报账），到期后退出码 4（到期未清）——不允许它一直挂着当永久状态。
  * **判活只看心跳，不看 pid**（2026-11-13）：`claim` 是一次性 CLI，写完就
    退出，`pid` 记的是那个短命进程——「pid 已死」是默认状态，没有信息量。
    旧实现把 `pid_dead_or_reused` 当接管的硬前提，等于任何 claim 都能被接管
    （2026-11-13 01:39 `desktop/ai_panel.py`、`desktop/ai_sdr_session.py`
    在 TTL 还剩 160+ 分钟时被 orphan_takeover 抢走）。现在：
      - 不选「父进程/shell pid」：本机每个工具调用常是新的短命 shell，父 pid
        同样不新鲜；换个工具调用继续工作也会被误判为「会话结束」。
      - 不选「显式 --session-id」：那只是一个名字，不唯一（两条线可同名），
        也无法证明「还在工作」；能证明身份的是凭据 token，能证明活跃的是心跳。
      - 所以判据 = `heartbeat_at + ttl_min`（新鲜且唯一）；pid 仅作诊断。
    两种边界情况的答案：
      - 同一 shell 连跑两条命令：第二条 claim/续租自动读 sidecar 凭据 → 心跳
        刷新 → 仍有效；pid 不参与，所以不会因为「换了条命令」而放宽接管。
      - 两个不同会话同名：名字只用于展示；续租/释放要凭 token，同名不同 token
        不能互相 release；takeover 只看 TTL/在途，不因同名变松或变紧。
  * **身份不是名字**（2026-11-13）：claim 成功时签发一次性 `--token`，占位文件
    只存它的 sha256。续租/释放必须带这份凭据——两条同名线（如两条都叫
    `W81线`）各自持有不同凭据，互相 release 会失败，同名不再等于同一持有者。
  * **凭据持久化**：claim/takeover 会把 token 写到与占位文件同目录的
    `<claim_id>.token`（best-effort chmod 0600），release / 续租不带 `--token`
    时自动读取，进程退出后仍能用——不用指望会话记得住输出。release 成功后删
    sidecar；takeover/过期夺取会用新 token 覆盖旧 sidecar，旧凭据同时作废。
    sidecar 存**明文**而占位文件只存 sha256，原因：占位文件是长期留档的权威
    记录（status/audit/其他工具都会读），泄露它不能等同于拿到可用凭据；sidecar
    的唯一用途就是替持有者出示凭据，存哈希既挡不住同账户进程（能读 sidecar
    就有能力出示），又会让 `--token` 这条显式通道失效。Windows 同账户下
    ACL 无法区分不同线，chmod 只是误读减速带，不是安全边界；因此靠缩短
    生命周期（release 删除、takeover/夺取覆盖）而不是靠文件权限兜底。
  * **凭据缺失不做名字兜底**：sidecar 被删、旧格式、takeover 之后拿旧 token，
    一律拒绝并提示「等 TTL 过期后重新 claim / takeover / 带 --token」；
    绝不会退化成「同名就放行」。
  * 旧格式 claim（落地前写入、没有凭据）**不按名字放行**：用
    `claim --adopt-legacy` 显式迁移一次（会签发凭据），或等 TTL 过期后
    重新 claim / takeover。
  * 状态文件：`D:\\mbdsdr\\.locks\\claims.json`（与既有 `qt_test.lock` 同目录），
    占位文件在 `D:\\mbdsdr\\.locks\\claims\\`。不写 C 盘。
    `MBDSDR_LOCKS_DIR` 仅供测试指向临时目录，日常不要设。

用法：
  python scripts/file_claim.py claim desktop/main_window.py --owner 操作逻辑线
  python scripts/file_claim.py claim desktop/main_window.py --owner 操作逻辑线 --ttl-min 10
  python scripts/file_claim.py claim desktop/main_window.py --owner 操作逻辑线 \
      --ttl-min 30 --reason "K25 断言补证还没跑完"          # 续租（--reason 必填）
  python scripts/file_claim.py audit-renew             # 续租次数 / 累计持有 / 超上限 / 老数据不可审计
  python scripts/file_claim.py claim desktop/main_window.py --owner 操作逻辑线 --token-file <凭据文件>
  python scripts/file_claim.py claim desktop/main_window.py --owner 操作逻辑线 --adopt-legacy
  python scripts/file_claim.py release desktop/main_window.py --owner 操作逻辑线          # 自动读 sidecar
  python scripts/file_claim.py release desktop/main_window.py --owner 操作逻辑线 --token-file <凭据文件>
  python scripts/file_claim.py status
  python scripts/file_claim.py status desktop/main_window.py
  python scripts/file_claim.py audit-stale            # 定期跑：过期 claim 却仍在改文件
  python scripts/file_claim.py audit-stale --json
  python scripts/file_claim.py cleanup-stale          # 默认只审计：逐条列证据，够才标可释放
  python scripts/file_claim.py cleanup-stale --release --by <释放人> --reason "<理由>"
  python scripts/file_claim.py cleanup-stale --json
  python scripts/file_claim.py takeover desktop/main_window.py --owner 接管线 \
      --reason "原持有者进程已死，工作区干净"
  python scripts/file_claim.py legacy-token-repair desktop/main_window.py \
      --owner <台账 owner 原文> --reason "确认持有者已不在改；sidecar 上线前签发、明文从未落盘"

`legacy-token-repair` 是**有审计的一次性修复**，不是通用后门：只对「有
token_sha256 + sidecar 缺失 + 签发早于 sidecar 引入版 028f390f」的 claim 有效，
且 --owner 必须与台账原文一致、--reason 非空；日志写 action=legacy_token_repair
（文件 / 原 owner / 签发时间 / 修复人 / 理由）。

退出码：0 成功；2 被拒绝（被他人占用 / 非持有者 / 本就没有 claim / 接管判据不足）；
1 用法错误；3 = `audit-stale` 发现「claim 已过期但文件 mtime 晚于 acquired_at」的漂移。

`cleanup-stale` 另有一套**按证据**的退出码（它不做任何「超过 N 分钟一律释放」
的默认分支）。结果分三堆，只有主数（可释放未释放）会驱动退出码：

* ``READY``：四项证据全 PASS，可以释放——**这是唯一的待办主数**；
* ``UNDECIDABLE``：claim 之后文件有过提交，内容已无法回溯到取得时刻。
  这类**不会因为时间推移变得可判定**，只计数、只做趋势，不进待办、不改
  退出码，但报告和 JSON 必须写出条数、占比与原因分布（不许静默消失）；
* ``SUSPECT``：暂时缺在途/内容证据，但文件在 claim 之后**没有**提交过，
  补证据仍可能有救；列出缺口与补法，同样不改退出码。

``UNDECIDABLE`` 不是「文件清白」的证明，只是「证明不了」：里面可能存在
「文件被别人改过而本方没发现」的情况，只是没有证据可以区分。

===  ==================================================================
码   含义
===  ==================================================================
0    主数为空：没有「可释放未释放」的 claim（UNDECIDABLE / SUSPECT 只报告）
1    用法错误（``--release`` 缺 ``--by`` / ``--reason`` 等）
2    释放被拒绝：复核时证据已变化、占位已换人、或清理日志写失败
3    主数非空：有可释放的 claim 仍未释放（唯一会退 3 的情况）
===  ==================================================================
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

ROOT = Path(
    os.environ.get("MBDSDR_CLAIM_ROOT") or Path(__file__).resolve().parents[1]
)
# 硬默认：与 qt_test.lock 同目录，且在 D 盘。环境变量只给测试用。
DEFAULT_LOCKS_DIR = Path(r"D:\mbdsdr\.locks")
LOCKS_DIR = Path(os.environ.get("MBDSDR_LOCKS_DIR") or DEFAULT_LOCKS_DIR)
REGISTRY_PATH = LOCKS_DIR / "claims.json"
MARKER_DIR = LOCKS_DIR / "claims"
REGISTRY_MUTEX = LOCKS_DIR / "claims.mutex"
TAKEOVER_LOG = LOCKS_DIR / "claim_takeovers.log"
#: 过期 claim 的清理账（JSONL）：释放前/后各一行，含证据与前后占位状态。
#: 与 `claim_takeovers.log` 分开：接管是「换人持有」，清理是「确认无人后注销」，
#: 混在一起会让读方分不清「这条路径现在归谁」。
CLEANUP_LOG = LOCKS_DIR / "claim_cleanup.log"
#: 每次 cleanup-stale 的计数快照（JSONL，只存数字不存路径）：UNDECIDABLE
#: 不做待办，但必须能看出「这堆是在涨还是在降」。
CLEANUP_TREND = LOCKS_DIR / "claim_cleanup_trend.jsonl"
#: 「想拿但被别人占着」的等待记录。只在冲突时写；持有者下次续租/append 成功时
#: 读出并清零（打印一次即删）。持有方成本为 0，被挡方成本全付，等待不可见——
#: 所以占位对持有者本来是隐形的；这张表只补「看得见」，不参与任何判定。
WAIT_LOG = LOCKS_DIR / "waiting.log"

LOCK_TIMEOUT_S = 15.0
#: 单条 claim 的累计持有上限（分钟）。改它必须留痕：值 + 来源写进占位文件。
DEFAULT_MAX_HOLD_MIN = 360.0
MAX_HOLD_ENV = "MBDSDR_CLAIM_MAX_HOLD_MIN"
#: 「历史不可审计」老数据的清账到期日。到期前 `audit-renew` 只报账（退出码 3），
#: 到期后升级为「到期未清」（退出码 4）——不允许一条不可审计的老 claim 变成永久状态。
DEFAULT_LEGACY_AUDIT_DUE = "2026-11-20"
LEGACY_AUDIT_DUE_ENV = "MBDSDR_LEGACY_AUDIT_DUE"
#: 「疑似死会话」提示阈值：心跳年龄超过 TTL 的这个比例时，若 pid 也已不在，
#: `status` 会多标一个提示。**这是提示阈值，不是判据**——取 50% 只是因为
#: 「已过半」是个能一眼解释的分界（离过期只剩一半租期），不是算出来的最优点；
#: 它不参与任何夺取/接管判定（见 `_orphan_criteria` 的 pid 说明）。
SUSPECT_HEARTBEAT_RATIO = 0.5
#: 判定「同一个进程」的创建时刻容差（秒）。见 `_same_process_create_time`。
PID_CREATE_TIME_TOLERANCE_S = 2.0
#: 长租期门槛（分钟）：`--ttl-min` 超过它必须带 `--reason`，拒绝无理由长租。
#: 取 30 的依据：台账 2212 条已释放 claim 的持有中位数 5.4min、p90 42.5min，
#: 仅 14.3% 超过 30min；但当前活跃 claim 里 17/20 是 120min——「整批动手」时
#: 长租期是常态。所以不能只打告警：一次性传个大 TTL 会绕过续租审计
#:（reason + 累计上限），必须同样留下「为什么占这么久」的凭据。
LONG_TTL_HINT_MIN = 30.0
EXIT_OK = 0
EXIT_USAGE = 1
EXIT_DENIED = 2
EXIT_FINDINGS = 3
EXIT_OVERDUE = 4


class ClaimDenied(RuntimeError):
    """claim/release 被拒绝（对应退出码 2）。"""


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _minutes(seconds: float) -> str:
    return f"{seconds / 60.0:.1f}"


def _duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    return f"{seconds / 60.0:.1f} 分钟"


# ---------------------------------------------------------------------------
# 进程身份（防 pid 复用）
# ---------------------------------------------------------------------------

def _win_proc_snapshot(pid: int) -> Tuple[str, Optional[float], Optional[str]]:
    """Windows 无 psutil 时的兜底：用 GetProcessTimes 取进程创建时间。"""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return "unknown", None, None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return "gone", None, None
    try:
        creation = wintypes.FILETIME()
        exit_t = wintypes.FILETIME()
        kernel_t = wintypes.FILETIME()
        user_t = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_t),
            ctypes.byref(kernel_t),
            ctypes.byref(user_t),
        )
        if not ok:
            return "alive", None, None
        ticks = creation.dwHighDateTime * 2 ** 32 + creation.dwLowDateTime
        return "alive", ticks / 1e7 - 11644473600.0, None
    finally:
        kernel32.CloseHandle(handle)


def _posix_proc_snapshot(pid: int) -> Tuple[str, Optional[float], Optional[str]]:
    """POSIX 无 psutil 时的兜底：/proc 存在性 + 命令行。创建时间取不到。"""
    base = Path(f"/proc/{pid}")
    if not base.exists():
        return "gone", None, None
    try:
        raw = (base / "cmdline").read_bytes()
        cmdline = raw.replace(b"\0", b" ").decode("utf-8", errors="replace").strip()
    except OSError:
        cmdline = None
    return "alive", None, cmdline or None


def _proc_snapshot(pid: int) -> Tuple[str, Optional[float], Optional[str]]:
    """返回 (状态, 创建时间, 命令行)；状态 ∈ {"alive","gone","unknown"}。

    只看 pid 号会不会「活着」不够：pid 会被系统复用。判据因此落在
    **创建时间**上——同一 pid、创建时间不同 = 原进程已死、号被复用。
    """
    try:
        import psutil
    except ImportError:
        return _win_proc_snapshot(pid) if os.name == "nt" else _posix_proc_snapshot(pid)
    try:
        proc = psutil.Process(pid)
        return "alive", proc.create_time(), " ".join(proc.cmdline())
    except psutil.NoSuchProcess:
        return "gone", None, None
    except psutil.ZombieProcess:
        return "gone", None, None
    except psutil.AccessDenied:
        return "alive", None, None
    except OSError:
        return "unknown", None, None


def _same_process_create_time(recorded: float, actual: float) -> bool:
    """两个创建时刻是否指向同一个进程（容差 2 秒）。

    为什么要容差：Windows 的 FILETIME 精度是 100ns，但「进程启动时刻」在
    不同取数路径上的取整方向不一致——`GetProcessTimes` 给原值，有的封装按
    整秒向下取整、有的保留亚秒；同一进程在跨秒边界上两次读数会落在相邻整秒
    两侧，差接近 1s。取 2s 覆盖「一边整秒取整 + 一次读数抖动」，再放大就会把
    相邻启动的两个进程误判成同一个。
    """
    return abs(float(recorded) - float(actual)) <= PID_CREATE_TIME_TOLERANCE_S


# ---------------------------------------------------------------------------
# 操作系统级互斥
# ---------------------------------------------------------------------------

def _try_lock(fd: int, shared: bool = False) -> bool:
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBRLCK if shared else msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    try:
        fcntl.flock(fd, mode | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _locked_fd(
    path: Path, *, create: bool, shared: bool = False, timeout: float = LOCK_TIMEOUT_S
) -> Iterator[int]:
    """打开 path 并对其首字节加锁（默认独占，shared=只读共享）。

    Windows 字节锁是强制的：锁住之后**别的句柄**读同一区间会失败，所以
    所有读写都必须走同一个 fd，不能另开 `read_text()`。
    """
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileNotFoundError:
        raise ClaimDenied(f"文件未加锁：{path}") from None
    deadline = time.monotonic() + timeout
    try:
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            if _try_lock(fd, shared=shared):
                break
            if time.monotonic() >= deadline:
                raise ClaimDenied(f"等待锁超时（{timeout:.0f}s）：{path}")
            time.sleep(0.01)
        try:
            yield fd
        finally:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                _unlock(fd)
            except OSError:
                pass
    finally:
        os.close(fd)


def _read_fd(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    return os.read(fd, 65536).decode("utf-8", errors="replace")


def _write_fd(fd: int, text: str) -> None:
    data = text.encode("utf-8")
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, data)
    os.ftruncate(fd, len(data))
    os.fsync(fd)


# ---------------------------------------------------------------------------
# 路径规范化
# ---------------------------------------------------------------------------

def canonical_path(raw: str) -> Tuple[str, str]:
    """返回 (比较键, 展示路径)。

    `desktop/main_window.py` 与 `D:\\mbdsdr\\MBDSDR\\desktop\\main_window.py`
    归一为同一比较键（Windows 上大小写不敏感）。
    """
    text = raw.strip().strip('"').strip("'")
    if not text:
        raise ValueError("路径为空")
    p = Path(text)
    target = p if p.is_absolute() else ROOT / p
    try:
        rel = target.resolve().relative_to(ROOT.resolve())
        display = rel.as_posix()
    except (ValueError, OSError):
        display = target.resolve().as_posix()
    key = os.path.normcase(display).replace("\\", "/")
    return key, display


def _marker_for(key: str) -> Path:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return MARKER_DIR / f"{digest}.claim"


def _target_path(display: str) -> Path:
    p = Path(display)
    return p if p.is_absolute() else ROOT / p


def _sha256_file(path: Path) -> Optional[str]:
    """文件内容哈希；读不到返回 None（调用方必须当成「证据缺口」而不是「一致」）。"""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# 持有者身份凭据（同名 ≠ 同一持有者）
# ---------------------------------------------------------------------------

_TOKEN_DOMAIN = b"mbdsdr/file_claim/token/v1:"
_TOKEN_HINT = (
    "  凭据已写入 {path}（用 --token-file 读取；release/续租不带 --token 时自动读取；"
    "该文件是持有者凭据，勿外传）"
)


def _load_token_arg(token: Optional[str], token_file: Optional[str]) -> Optional[str]:
    """凭据通道二选一：--token 走 argv，--token-file 读文件。

    提示语改成「凭据已写入 <path>」之后，出示凭据应该走文件通道：argv 会进
    进程列表，同机任何进程都能读（今晚已确认过这个泄露面）。
    """
    if token and token_file:
        raise ValueError("--token 与 --token-file 只能给一个")
    if not token_file:
        return token
    try:
        text = Path(token_file).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"--token-file 读不到：{token_file}（{exc}）")
    if not text:
        raise ValueError(f"--token-file 是空的：{token_file}")
    return text


def _token_hash(token: str) -> str:
    """占位文件只存凭据哈希：文件对同机其他会话可读，存明文等于没有身份。"""
    return hashlib.sha256(_TOKEN_DOMAIN + str(token).encode("utf-8")).hexdigest()


def _token_matches(token: Optional[str], expected_hash: str) -> bool:
    if not token or not expected_hash:
        return False
    return hmac.compare_digest(_token_hash(token), expected_hash)


def _token_path(key: str) -> Path:
    """凭据 sidecar：与占位文件同目录同 claim_id，扩展名 .token。

    sidecar 存**明文**凭据（而不是哈希），理由见模块 docstring：
    它的唯一用途就是替持有者跨进程记住凭据，必须能直接出示；存哈希既挡不住
    同账户进程（能读 sidecar 就能用），又让 --token 这条显式通道失效。
    """
    return _marker_for(key).with_suffix(".token")


def _write_token_file(key: str, token: str) -> Optional[str]:
    """落 sidecar；成功返回 None，失败返回原因（调用方必须显式告警）。"""
    path = _token_path(key)
    tmp = path.with_suffix(".token.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(str(token).strip() + "\n", encoding="utf-8")
        try:
            # best-effort：Windows 上 os.chmod 只影响只读位，同一账户进程仍可读，
            # 因此不把它当安全边界，只当误读/误提交的减速带。
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        return None
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        return str(exc)


def _read_token_file(key: str) -> Optional[str]:
    try:
        value = _token_path(key).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _remove_token_file(key: str) -> None:
    try:
        _token_path(key).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 占位文件读写（占位文件本身即权威状态）
# ---------------------------------------------------------------------------

def _parse_marker(text: str) -> Optional[Dict[str, Any]]:
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict) and data.get("owner"):
        return data
    return None


def _read_marker_fd(fd: int) -> Dict[str, Any]:
    """在已加锁的 fd 上读占位文件；容忍另一进程刚建好还没写完的瞬间。"""
    for _ in range(40):
        data = _parse_marker(_read_fd(fd))
        if data is not None:
            return data
        time.sleep(0.005)
    raise ClaimDenied("占位文件存在但内容不可读（另一进程正在写入，稍后重试）")


def _peek_marker(path: Path) -> Optional[Dict[str, Any]]:
    """只读查看（共享锁）；占位文件不存在返回 None。"""
    if not path.exists():
        return None
    try:
        with _locked_fd(path, create=False, shared=True) as fd:
            return _read_marker_fd(fd)
    except (ClaimDenied, FileNotFoundError):
        return None


def _read_all_fd(fd: int, chunk: int = 1 << 20) -> str:
    """读整个 fd（``_read_fd`` 只读 64KB，等待日志会随冲突累积，不能截断）。"""
    os.lseek(fd, 0, os.SEEK_SET)
    parts: List[bytes] = []
    while True:
        buf = os.read(fd, chunk)
        if not buf:
            break
        parts.append(buf)
    return b"".join(parts).decode("utf-8", errors="replace")


def record_wait(key: str, display: str, waiter: str, holder: str, kind: str) -> bool:
    """记一条「想拿这个文件、但被别人挡着」。只留痕，不改判定/退出码。

    写失败返回 False 且不抛：留痕失败不能反过来把 claim 变成失败。
    """
    now = _now()
    record = {
        "at": now,
        "at_iso": _iso(now),
        "path": display,
        "key": key,
        "waiter": waiter,
        "holder": holder,
        "kind": kind,
    }
    try:
        LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        with _locked_fd(WAIT_LOG, create=True) as fd:
            text = _read_all_fd(fd)
            _write_fd(fd, text + json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except (ClaimDenied, OSError):
        return False


def take_waits(key: str) -> List[Dict[str, Any]]:
    """取出并清除该文件的等待记录（「打印完即标记已读」）；无记录返回 []。"""
    try:
        with _locked_fd(WAIT_LOG, create=False) as fd:
            text = _read_all_fd(fd)
            rows: List[Dict[str, Any]] = []
            keep: List[str] = []
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue  # 坏行丢弃：等待记录是提示，不是凭据
                if isinstance(rec, dict) and rec.get("key") == key:
                    rows.append(rec)
                else:
                    keep.append(line)
            if rows:
                _write_fd(fd, "".join(item + "\n" for item in keep))
            return rows
    except (ClaimDenied, OSError, FileNotFoundError):
        return []


def report_waits(key: str, display: str, quiet: bool = False) -> int:
    """打印「另有 N 个进程等过」，打印后清零；返回条数。只提示，不影响返回码。"""
    waits = take_waits(key)
    if not waits or quiet:
        return len(waits)
    stamps = [float(w.get("at") or 0) for w in waits]
    earliest = min(stamps) if stamps else 0.0
    waiters = sorted({str(w.get("waiter") or "?") for w in waits})
    print(
        f"WAIT {display}：另有 {len(waits)} 个进程在此文件上等待过"
        f"（最早 {_iso(earliest)}；记名 {', '.join(waiters)}）"
    )
    return len(waits)


def _marker_payload(
    display: str,
    key: str,
    owner: str,
    ttl_min: float,
    now: float,
    token_hash: str = "",
) -> Dict[str, Any]:
    state, create_time, cmdline = _proc_snapshot(os.getpid())
    payload = {
        "path": display,
        "key": key,
        "owner": owner,
        "acquired_at": now,
        "heartbeat_at": now,
        "ttl_min": ttl_min,
        "pid": os.getpid(),
        # pid 会被系统复用；接管时必须靠创建时间确认「还是不是那个进程」。
        "pid_create_time": create_time,
        "pid_cmdline": cmdline,
    }
    if token_hash:
        # 只存凭据哈希；明文只在 claim/takeover 输出里出现一次。
        payload["token_sha256"] = token_hash
    # 取得时刻的内容基线：清理过期 claim 时，「文件没被它改过」必须能证明，
    # 不能靠 mtime（mtime 会被 checkout/工具触碰改掉，也不说明谁改的）。
    # 续租不刷新这个值（见 _carry_content_baseline）——否则「改过再续租」会把
    # 改动洗白成「没改过」。
    baseline = _sha256_file(_target_path(display))
    if baseline:
        payload["content_sha256"] = baseline
        payload["content_baseline_from"] = "claim"
    return payload


def _carry_content_baseline(new: Dict[str, Any], old: Dict[str, Any]) -> None:
    """续租/迁移时沿用**取得时**的内容基线；没有旧基线才以本次内容起算。"""
    previous = str(old.get("content_sha256") or "")
    if previous:
        new["content_sha256"] = previous
        new["content_baseline_from"] = str(old.get("content_baseline_from") or "claim")
    elif new.get("content_sha256"):
        new["content_baseline_from"] = "renew"


def _create_marker(path: Path, payload: Dict[str, Any]) -> bool:
    """O_CREAT|O_EXCL 原子创建；已被占用返回 False。"""
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except FileNotFoundError:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
    try:
        os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        os.fsync(fd)
    except OSError:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass
        raise
    os.close(fd)
    return True


# ---------------------------------------------------------------------------
# 台账（人类可读 + check_commit_scope 用；权威仍是占位文件）
# ---------------------------------------------------------------------------

def _load_registry() -> Dict[str, Any]:
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "claims": {}}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "claims": {}}
    if not isinstance(data, dict):
        return {"version": 1, "claims": {}}
    data.setdefault("version", 1)
    data.setdefault("claims", {})
    return data


def _update_registry(mutator: Callable[[Dict[str, Any]], None]) -> None:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    with _locked_fd(REGISTRY_MUTEX, create=True):
        data = _load_registry()
        mutator(data)
        data["updated_at"] = _iso(_now())
        tmp = REGISTRY_PATH.with_name(f"claims.json.tmp{os.getpid()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, REGISTRY_PATH)


def _entry(data: Dict[str, Any], key: str) -> Dict[str, Any]:
    claims = data.setdefault("claims", {})
    entry = claims.setdefault(key, {"path": key, "current": None, "history": []})
    entry.setdefault("history", [])
    entry.setdefault("current", None)
    return entry


def _close_active(
    entry: Dict[str, Any],
    now: float,
    reason: str,
    extra: Optional[Dict[str, Any]] = None,
    extra_key: str = "takeover",
) -> None:
    for item in reversed(entry["history"]):
        if item.get("released_at") is None:
            item["released_at"] = now
            item["closed_reason"] = reason
            if extra:
                item[extra_key] = extra
            return


def _registry_record_claim(
    payload: Dict[str, Any],
    replaced: Optional[Dict[str, Any]],
    close_reason: str = "expired_steal",
    takeover: Optional[Dict[str, Any]] = None,
) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = _entry(data, payload["key"])
        entry["path"] = payload["path"]
        if replaced is not None:
            _close_active(entry, payload["heartbeat_at"], close_reason, takeover)
        entry["history"].append(
            {
                "owner": payload["owner"],
                # 历史条目同样只记凭据哈希（不落明文），让 renew 能按身份
                # 而不是按名字找回自己的条目（A13）。
                "token_sha256": payload.get("token_sha256", ""),
                "acquired_at": payload["acquired_at"],
                "released_at": None,
                "last_heartbeat_at": payload["heartbeat_at"],
                "pid": payload.get("pid"),
                "claim_reason": payload.get("claim_reason", ""),
                # 留痕三件套（2026-11-13）：谁签发的、是否冒名、是否空理由。
                # 旧记录没有这三个键，读方按「未记录」处理，不当成 false。
                "issued_by": payload.get("issued_by", ""),
                "owner_mismatch": bool(payload.get("owner_mismatch")),
                "reason_missing": bool(payload.get("reason_missing")),
                "renewals": [],
            }
        )
        entry["history"] = entry["history"][-50:]
        entry["current"] = payload

    _update_registry(mutate)


def _same_holder(item: Dict[str, Any], owner: str, token_hash: str) -> bool:
    """历史条目是否属于本次续租的持有者：owner 名与凭据哈希**同时**相等。

    凭据哈希是唯一且不可复用的事实；owner 名只是展示标签——同名可以是
    另一条线（A13）。无凭据的旧格式条目返回 False：它们只能经
    adopt-legacy 窗口显式绑定凭据（见 `_registry_renew`）。
    """
    return (item["owner"], item.get("token_sha256") or "") == (owner, token_hash)


def _registry_renew(payload: Dict[str, Any], renewal: Optional[Dict[str, Any]] = None) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = _entry(data, payload["key"])
        entry["path"] = payload["path"]
        entry["current"] = payload
        # 续租找回历史条目必须按「身份」（owner 名 + 凭据哈希联合）匹配，
        # 不能只按 owner 名字：同名可以是另一条线，续错会把别人的历史心跳
        # 改掉（A13）。marker 层已校验凭据，但台账可能与 marker 不一致
        # （台账写入失败只 WARN），台账层必须自己守住。
        payload_token = payload.get("token_sha256") or ""
        for item in reversed(entry["history"]):
            if item.get("released_at") is not None:
                continue
            if not payload_token:
                continue  # 无凭据的续租不按名字放行
            if _same_holder(item, payload["owner"], payload_token):
                item["last_heartbeat_at"] = payload["heartbeat_at"]
                if renewal is not None:
                    # 续租留痕（2026-11-13）：与首次取得分开存，能回答
                    # 「续过几次、每次为什么、合计持有多久」。
                    trail = item.get("renewals")
                    trail = trail if isinstance(trail, list) else []
                    trail.append(renewal)
                    item["renewals"] = trail[-50:]
                    item["renew_count"] = len(item["renewals"])
                break
            if item.get("token_sha256"):
                continue  # 同名不同凭据：绝不续到别人的历史身份上
            # 旧格式历史条目（无凭据）的唯一合法来源是 adopt-legacy
            # （cmd_claim 里 holder == owner 才走到）：名字相同才允许
            # 把凭据绑进台账；绑定后该条目也只认凭据。
            same_name = (item["owner"],) == (payload["owner"],)
            if not same_name:
                continue
            item["token_sha256"] = payload_token
            item["last_heartbeat_at"] = payload["heartbeat_at"]
            break

    _update_registry(mutate)


def _registry_release(key: str, display: str, now: float) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = _entry(data, key)
        entry["path"] = display
        _close_active(entry, now, "released")
        entry["current"] = None

    _update_registry(mutate)


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def _ttl(ttl_min: float, now: float) -> float:
    return ttl_min * 60.0


def _resolve_max_hold(cli_value: Optional[float]) -> Tuple[float, str]:
    """累计持有上限：命令行 > 环境变量 > 默认。改过的值必须带来源，便于留痕。"""
    if cli_value is not None:
        value, source = float(cli_value), "--max-hold-min"
    elif os.environ.get(MAX_HOLD_ENV):
        value, source = float(os.environ[MAX_HOLD_ENV]), f"env {MAX_HOLD_ENV}"
    else:
        value, source = DEFAULT_MAX_HOLD_MIN, "default"
    return value, source


SIGNER_ENV = "MBDSDR_CLAIM_OWNER"


def _declared_owner() -> str:
    """签发身份：`MBDSDR_CLAIM_OWNER`，缺省读 `git config mbdsdr.owner`。

    与 `--owner` 是两件事：`--owner` 回答「这条 claim 记谁的名字」，签发身份
    回答「谁在按回车」。两者可以不同，但**不同必须留痕**——2026-11-13 20:41
    那条挂在 `scripts/safe_commit.py` 上、owner 写本线、理由为空的 claim，
    事后只能靠人工比对提交才发现不是本线签的。
    """
    name = (os.environ.get(SIGNER_ENV) or "").strip()
    if name:
        return name
    try:
        proc = subprocess.run(
            ["git", "config", "--get", "mbdsdr.owner"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _stamp_signer(payload: Dict[str, Any], owner: str, reason: str) -> bool:
    """落「签发身份 / 记名是否与签发一致 / 是否空理由」，返回是否不一致。

    为什么要这三项：§6.1 的 owner 列**不能当归属证据**——`--owner` 是一个可以
    照抄的字符串，而 `append_coordination_row.py` 又强制它与活跃 claim 逐字一致，
    于是「拿表证明归属、再拿归属解释表」成了循环论证。2026-11-13 20:41 那两条
    挂在 `scripts/safe_commit.py` 上的 claim 就是例证：事后只能靠人工比对提交
    时间窗才发现不是本线手工签的。**只留痕，不改退出码**：把空理由升级为必填
    会一次打红大量既有调用（`tests/` 里有大量不带 `--reason` 的 claim），而且
    「理由是否可信」没有机器判据。

      * `issued_by`       谁签发的（未声明时为空串，空串不等于不一致）；
      * `owner_mismatch`  `--owner` 与签发身份不一致（代签 / 照抄都会命中）；
      * `reason_missing`  `--reason` 为空。
    """
    issuer = _declared_owner()
    payload["issued_by"] = issuer
    payload["owner_mismatch"] = bool(issuer) and issuer != owner
    payload["reason_missing"] = not reason.strip()
    return bool(payload["owner_mismatch"])


def cmd_claim(
    path: str,
    owner: str,
    ttl_min: float,
    quiet: bool = False,
    token: Optional[str] = None,
    adopt_legacy: bool = False,
    reason: str = "",
    max_hold_min: Optional[float] = None,
) -> int:
    owner = owner.strip()
    if not owner:
        print("OWNER 不能为空", file=sys.stderr)
        return EXIT_USAGE
    if ttl_min <= 0:
        print("--ttl-min 必须大于 0", file=sys.stderr)
        return EXIT_USAGE
    if ttl_min > LONG_TTL_HINT_MIN:
        # 长租期是续租审计的旁路：renew 必须带理由，而一次性传个大 TTL 过去
        # 什么都不用写。同一条事实（为什么这个文件要占这么久）走同一种凭据。
        if not reason.strip():
            print(
                f"CLAIM DENIED：--ttl-min {ttl_min:g} 超过 {LONG_TTL_HINT_MIN:g}min，"
                f"必须带 --reason。\n"
                f"  要么用默认 10 分钟（到期后 renew 续租，同样要理由）；\n"
                f"  要么说明为什么这个文件要占这么久（理由会写进 claim 记录，"
                f"事后可复核）。\n"
                f"  依据：历史已释放 claim 持有 p90 42.5min；一次性传大 TTL 等于"
                f"绕过续租审计（reason + 累计上限）。",
                file=sys.stderr,
            )
            return EXIT_USAGE
    try:
        cap_min, cap_source = _resolve_max_hold(max_hold_min)
    except ValueError:
        print(f"--max-hold-min / {MAX_HOLD_ENV} 必须是数字", file=sys.stderr)
        return EXIT_USAGE
    if cap_min <= 0:
        print("--max-hold-min 必须大于 0", file=sys.stderr)
        return EXIT_USAGE

    key, display = canonical_path(path)
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    now = _now()
    fresh_token = secrets.token_hex(16)
    payload = _marker_payload(
        display, key, owner, ttl_min, now, _token_hash(fresh_token)
    )
    payload["claim_reason"] = reason.strip()
    payload["max_hold_min"] = cap_min
    payload["max_hold_source"] = cap_source
    if _stamp_signer(payload, owner, reason) and not quiet:
        print(
            f"WARN 记名与签发身份不一致：--owner={owner} 但 issued_by="
            f"{payload['issued_by']}（可能是代签或照抄）。已落 "
            f"owner_mismatch=true，只留痕、不拦提交。",
            file=sys.stderr,
        )
    # 空续租记录也显式写出来：否则「还没续过」与「老格式没留痕」在机检里
    # 无法区分，后者会被误当成 0 次（本次事故正是这种沉默）。
    payload["renewals"] = []
    payload["renew_count"] = 0
    marker = _marker_for(key)

    if _create_marker(marker, payload):
        try:
            _registry_record_claim(payload, None)
        except ClaimDenied as exc:  # 台账只是记录，占位文件才是权威
            print(f"WARN 台账写入失败（claim 仍有效）：{exc}", file=sys.stderr)
        sidecar_err = _write_token_file(key, fresh_token)
        if sidecar_err:
            print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
                f"WARN 凭据文件写入失败（{_token_path(key)}）：{sidecar_err}\n"
                f"     本次 release/续租必须显式带 --token；丢了只能等 TTL 过期或 takeover。",
                file=sys.stderr,
            )
        print(f"CLAIM OK {display} owner={owner} ttl={ttl_min:g}min 到期={_iso(now + _ttl(ttl_min, now))}")
        print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
            _TOKEN_HINT.format(path=_token_path(key)))
        return EXIT_OK

    with _locked_fd(marker, create=False) as fd:
        data = _read_marker_fd(fd)
        holder = str(data.get("owner", "<unknown>"))
        heartbeat = float(data.get("heartbeat_at") or data.get("acquired_at") or now)
        held_s = max(0.0, now - heartbeat)
        ttl_holder = float(data.get("ttl_min") or 0.0) * 60.0
        holder_token_hash = str(data.get("token_sha256") or "")

        if holder == owner:
            # 名字相等只说明「标签一样」，不说明是同一个持有者（两条线可以都
            # 叫 W81线）。身份由 claim 时签发的一次性凭据证明。
            if holder_token_hash:
                presented = token or _read_token_file(key)
                if not _token_matches(presented, holder_token_hash):
                    record_wait(key, display, owner, holder, "token_mismatch")
                    raise ClaimDenied(
                        f"CLAIM DENIED {display}\n"
                        f"  持有者名同为 {owner}，但无法证明是同一持有者：该 claim 已绑定凭据，\n"
                        f"  本次未带匹配的 --token，凭据文件 {_token_path(key)} 也缺失/不匹配。\n"
                        f"  处理：① 有 --token 就带上；② 等 TTL 过期后重新 claim（会签发新凭据）；\n"
                        f"        ③ 走 takeover（需心跳已过期 + 无在途改动 + --reason，见 §6.1.2）。\n"
                        f"  不会按 owner 名放行——同名不等于同一会话。"
                    )
                renewed = _marker_payload(
                    display, key, owner, ttl_min, now, holder_token_hash
                )
                acquired = float(data.get("acquired_at") or now)
                renewed["acquired_at"] = acquired
                _carry_content_baseline(renewed, data)
                _stamp_signer(renewed, owner, reason)
                old_ttl = float(data.get("ttl_min") or 0.0)
                held_total_s = max(0.0, now - acquired)
                prior = data.get("renewals")
                prior_records = prior if isinstance(prior, list) else []
                # ① 续租理由必填：没有理由的续租事后无法复核，等于无限续期不留痕。
                if not reason.strip():
                    raise ClaimDenied(
                        f"CLAIM DENIED {display}\n"
                        f"  续租必须带 --reason（写清这次为什么还要继续持有）。\n"
                        f"  现状：owner={owner} 已持有={_minutes(held_total_s)}min，"
                        f"续租记录={len(prior_records) if isinstance(prior, list) else 'NA（旧格式，不可审计）'}。\n"
                        f"  为什么必填：续租过去只刷心跳、不写 history，TTL 对「一直续的人」"
                        f"形同不存在（2026-11-13 事故）。"
                    )
                # ② 累计持有上限：超过即拒绝，不是告警放行（见模块文档的理由）。
                if held_total_s > cap_min * 60.0:
                    raise ClaimDenied(
                        f"CLAIM DENIED {display}\n"
                        f"  累计持有 {_minutes(held_total_s)}min 已超上限 {cap_min:g}min"
                        f"（来源 {cap_source}）。\n"
                        f"  单条 claim 不允许无限续期：请 release 后重新 claim"
                        f"（会开一条新的取得记录，acquired_at 与新凭据都换）；\n"
                        f"  或按 takeover 走审计路径。已记录的续租次数："
                        f"{len(prior_records) if isinstance(prior, list) else 'NA（旧格式）'}。"
                    )
                renewal_record = {
                    "at": now,
                    "at_iso": _iso(now),
                    "old_ttl_min": old_ttl,
                    "new_ttl_min": ttl_min,
                    "reason": reason.strip(),
                    "pid": os.getpid(),
                    "cmdline": _proc_snapshot(os.getpid())[2],
                    "held_min_at_renew": round(held_total_s / 60.0, 1),
                    "max_hold_min": cap_min,
                    "max_hold_source": cap_source,
                }
                renewed["renewals"] = (prior_records + [renewal_record])[-50:]
                renewed["renew_count"] = len(renewed["renewals"])
                renewed["max_hold_min"] = cap_min
                renewed["max_hold_source"] = cap_source
                _write_fd(fd, json.dumps(renewed, ensure_ascii=False))
                # 显式 --token 时顺手补齐/修复 sidecar，避免下次又只能靠记忆。
                sidecar_err = _write_token_file(key, presented)
                if sidecar_err:
                    print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
                        f"WARN 凭据文件写入失败（{_token_path(key)}）：{sidecar_err}",
                        file=sys.stderr,
                    )
                try:
                    _registry_renew(renewed, renewal_record)
                except ClaimDenied as exc:
                    print(f"WARN 台账写入失败（续租仍有效）：{exc}", file=sys.stderr)
                if not quiet:
                    print(
                        f"CLAIM RENEWED {display} owner={owner} ttl={ttl_min:g}min "
                        f"上次心跳={_iso(heartbeat)} 已持有={_minutes(held_total_s)}min "
                        f"第 {len(renewed['renewals'])} 次续租 上限={cap_min:g}min"
                    )
                    print(f"  续租理由：{reason.strip()}")
                # 等待可见：续租是持有者唯一会回来看一眼的时刻（append 成功是另一个）。
                report_waits(key, display, quiet)
                if cap_source != "default":
                    print(
                        f"WARN 持有上限被改为 {cap_min:g}min（来源 {cap_source}）——"
                        f"该值已写进占位文件与续租记录。",
                        file=sys.stderr,
                    )
                return EXIT_OK
            if adopt_legacy:
                # 旧格式迁移：一次性把「只有名字」的 claim 升级成凭据绑定。
                # 这是显式动作（--adopt-legacy），不默认按名字放行。
                upgraded_token = secrets.token_hex(16)
                renewed = _marker_payload(
                    display, key, owner, ttl_min, now, _token_hash(upgraded_token)
                )
                renewed["acquired_at"] = float(data.get("acquired_at") or now)
                renewed["legacy_upgraded_at"] = now
                _carry_content_baseline(renewed, data)
                if not data.get("content_sha256"):
                    # 旧 claim 没有取得时基线：从迁移这一刻起算，并在记录里写明来源。
                    renewed["content_baseline_from"] = "adopt"
                _stamp_signer(renewed, owner, reason)
                _write_fd(fd, json.dumps(renewed, ensure_ascii=False))
                sidecar_err = _write_token_file(key, upgraded_token)
                if sidecar_err:
                    print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
                        f"WARN 凭据文件写入失败（{_token_path(key)}）：{sidecar_err}",
                        file=sys.stderr,
                    )
                try:
                    _registry_renew(renewed)
                except ClaimDenied as exc:
                    print(f"WARN 台账写入失败（迁移仍有效）：{exc}", file=sys.stderr)
                print(
                    f"CLAIM UPGRADED {display} owner={owner} ttl={ttl_min:g}min\n"
                    f"  旧格式 claim（只记名字）已迁移为凭据绑定；"
                    f"这次按 owner 名匹配是唯一一次，之后同名也不再放行。"
                )
                print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
                    _TOKEN_HINT.format(path=_token_path(key)))
                return EXIT_OK
            raise ClaimDenied(
                f"CLAIM DENIED {display}\n"
                f"  该 claim 是旧格式：只记录了 owner 名，没有可验证的身份凭据。\n"
                f"  同名不等于同一持有者，因此不能按名字续租。\n"
                f"  处理：① 确认原持有者是你 → 用 --adopt-legacy 显式迁移一次"
                f"（会签发 --token）；\n"
                f"        ② 等 TTL 过期后重新 claim；③ 走 takeover。"
            )

        expired_s = held_s - ttl_holder
        if expired_s <= 0:
            remain = ttl_holder - held_s
            record_wait(key, display, owner, holder, "held")
            raise ClaimDenied(
                f"CLAIM DENIED {display}\n"
                f"  当前持有者={holder}\n"
                f"  已持有={_minutes(held_s)}min（TTL {_minutes(ttl_holder)}min，剩余 {_minutes(remain)}min）\n"
                f"  开始于={_iso(float(data.get('acquired_at') or heartbeat))} pid={data.get('pid')}\n"
                f"  处理：等对方 release，或换文件；禁止 stash / checkout 别人的改动。"
            )

        # 过期夺取：在占位文件自身的锁里完成，避免与续租并发写坏。
        stolen_from = holder
        old = dict(data)
        renewed = _marker_payload(
            display, key, owner, ttl_min, now, _token_hash(fresh_token)
        )
        _write_fd(fd, json.dumps(renewed, ensure_ascii=False))
        # 覆盖旧 sidecar：原持有者手里的旧凭据随 marker 哈希一起作废。
        sidecar_err = _write_token_file(key, fresh_token)

    if sidecar_err:
        print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
            f"WARN 凭据文件写入失败（{_token_path(key)}）：{sidecar_err}",
            file=sys.stderr,
        )
    try:
        _registry_record_claim(payload, old)
    except ClaimDenied as exc:
        print(f"WARN 台账写入失败（夺取仍有效）：{exc}", file=sys.stderr)
    print(
        f"原持有者 {stolen_from} 已超时 {_duration(expired_s)}，请确认它真的停了\n"
        f"CLAIM OK (steal) {display} owner={owner} ttl={ttl_min:g}min "
        f"（原持有者开始于 {_iso(float(old.get('acquired_at') or 0))}，上次心跳 {_iso(heartbeat)}）"
    )
    print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
        _TOKEN_HINT.format(path=_token_path(key)))
    return EXIT_OK


def cmd_release(path: str, owner: str, token: Optional[str] = None) -> int:
    owner = owner.strip()
    key, display = canonical_path(path)
    marker = _marker_for(key)
    if not marker.exists():
        raise ClaimDenied(f"RELEASE DENIED {display}\n  该文件当前没有 claim，无需释放。")

    with _locked_fd(marker, create=False) as fd:
        data = _read_marker_fd(fd)
    holder = str(data.get("owner", "<unknown>"))
    holder_token_hash = str(data.get("token_sha256") or "")
    now = _now()
    heartbeat = float(data.get("heartbeat_at") or data.get("acquired_at") or now)
    held_s = max(0.0, now - heartbeat)

    if not holder_token_hash:
        raise ClaimDenied(
            f"RELEASE DENIED {display}\n"
            f"  当前持有者={holder}（你声明的是 {owner}）\n"
            f"  该 claim 是旧格式：没有可验证的身份凭据，无法确认你就是原持有者。\n"
            f"  按 owner 名放行正是本次要消灭的误判（同名可以是另一条线）。\n"
            f"  处理：① 原持有者先 claim --adopt-legacy 迁移并拿到 --token，"
            f"再带 --token release；\n"
            f"        ② 等 TTL 过期后重新 claim；③ 走 takeover。"
        )

    presented = token or (_read_token_file(key) if owner == holder else None)
    if not _token_matches(presented, holder_token_hash):
        raise ClaimDenied(
            f"RELEASE DENIED {display}\n"
            f"  当前持有者={holder}（你声明的是 {owner}）\n"
            f"  已持有={_minutes(held_s)}min\n"
            f"  凭据缺失或不匹配：该 claim 绑定了 claim 时签发的凭据，"
            f"本次未带 --token，凭据文件 {_token_path(key)} 也缺失/不匹配。\n"
            f"  不会按 owner 名放行（同名可以是另一条线，互相解锁等于没有锁）。\n"
            f"  处理：① 用 claim 时输出过的 --token；② 等 TTL 过期后重新 claim；"
            f"③ 走 takeover（需心跳已过期 + 无在途改动 + --reason；pid 不参与判定）。"
        )

    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    # 释放后凭据不再有意义：删 sidecar，避免残留文件被误当成下一次的持有凭据。
    _remove_token_file(key)
    _registry_release(key, display, _now())
    print(f"RELEASE OK {display} owner={holder}")
    return EXIT_OK


def _inflight_lines(display: str) -> Optional[List[str]]:
    """工作区是否还有该文件的未提交改动；None = 查不到（git 不可用）。"""
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--", display],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.strip()]


def _orphan_criteria(payload: Dict[str, Any], display: str, now: float) -> Dict[str, Any]:
    """判定 claim 是否可被接管：只看「心跳过期」+「无在途改动」。

    **pid 不是判据**：`claim` 是一次性 CLI，写完就退出，`pid` 记的是那个短命
    进程——「pid 已死」是默认状态，不是异常状态。实测（2026-11-13）：claim
    子进程返回后 0.08s 去查它自己刚写下的 pid 就已经 NoSuchProcess；活跃
    claim 里「pid 已死」占比 100%，是构造性恒真，不携带任何会话存活信息。
    旧实现把它当接管硬前提，等于任何 claim 都能被接管：2026-11-13 01:39 的
    `desktop/ai_panel.py` / `desktop/ai_sdr_session.py` 就是这样在 TTL 还剩
    160+ 分钟时被抢走的。
    pid 现在只作诊断展示（`status` 里的「疑似死会话」提示也只提示，不参与
    `allowed`），不参与判定；持有者是否还在工作，唯一新鲜且唯一的信号是心跳
    （`heartbeat_at` + `ttl_min`）。
    """
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
    ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
    held_s = max(0.0, now - heartbeat)
    expired_s = held_s - ttl_s
    # §6.1.4：刚过期的 claim 可能只是没来得及续租，过期 < 30min 一律不许接管。
    grace_s = float(os.environ.get("MBDSDR_TAKEOVER_GRACE_MIN") or 30.0) * 60.0
    past_grace = expired_s >= grace_s
    if expired_s > 0:
        expired_detail = (
            f"心跳 {_iso(heartbeat)}，已 {_minutes(held_s)}min / TTL {_minutes(ttl_s)}min，"
            f"过期 {_minutes(expired_s)}min"
            + ("" if past_grace else f"（不足 {_minutes(grace_s)}min 宽限，§6.1.4）")
        )
    else:
        expired_detail = (
            f"心跳 {_iso(heartbeat)}，已 {_minutes(held_s)}min / TTL {_minutes(ttl_s)}min（未过期）"
        )

    lines = _inflight_lines(display)
    if lines is None:
        inflight_ok, inflight_detail = False, "无法查询工作区（git status 失败）"
    elif not lines:
        inflight_ok, inflight_detail = True, "工作区无未提交改动"
    else:
        inflight_ok = False
        inflight_detail = "有在途改动：" + "；".join(lines[:5])

    pid = payload.get("pid")
    if pid is None:
        pid_note = "台账未记录 pid（不影响判定）"
    else:
        try:
            state, _create, _cmd = _proc_snapshot(int(pid))
        except (TypeError, ValueError, OSError) as exc:
            state = f"取不到（{exc.__class__.__name__}）"
        pid_note = (
            f"pid {pid} 状态={state}（仅诊断，不参与判定：claim 是短命 CLI，"
            "它退出不代表持有会话结束）"
        )

    return {
        "expired": {
            "ok": past_grace,
            "raw": expired_s > 0,
            "detail": expired_detail,
        },
        "no_inflight": {"ok": inflight_ok, "detail": inflight_detail},
        "pid_note": pid_note,
        "heartbeat": heartbeat,
        "ttl_s": ttl_s,
        "held_s": held_s,
    }


def _append_takeover_log(record: Dict[str, Any]) -> None:
    TAKEOVER_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TAKEOVER_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def cmd_takeover(path: str, owner: str, ttl_min: float, reason: str = "") -> int:
    owner = owner.strip()
    if not owner:
        print("OWNER 不能为空", file=sys.stderr)
        return EXIT_USAGE
    if ttl_min <= 0:
        print("--ttl-min 必须大于 0", file=sys.stderr)
        return EXIT_USAGE

    key, display = canonical_path(path)
    marker = _marker_for(key)
    if not marker.exists():
        raise ClaimDenied(f"TAKEOVER DENIED {display}\n  该文件当前没有 claim；直接 claim 即可。")

    reason = (reason or "").strip()
    if not reason:
        raise ClaimDenied(
            f"TAKEOVER DENIED {display}\n"
            f"  必须用 --reason 写明接管理由（可复核证据，不是「需要改这个文件」）。\n"
            f"  没理由的接管事后无法审计，本仓不认。例：\n"
            f'    --reason "原 claim 心跳 01:26 起已停，过期 42min；git status 无在途改动；K16 断言已提交"'
        )

    now = _now()
    with _locked_fd(marker, create=False) as fd:
        data = _read_marker_fd(fd)
        holder = str(data.get("owner", "<unknown>"))
        if holder == owner and str(data.get("token_sha256") or ""):
            raise ClaimDenied(
                f"TAKEOVER DENIED {display}\n"
                f"  当前持有者名同为 {owner}，且该 claim 已绑定凭据；"
                f"用 claim --token 续租，不用 takeover。"
            )

        criteria = _orphan_criteria(data, display, now)
        # 两条都是硬条件：心跳过期（持有者已停止续租）+ 无在途改动（工作区干净）。
        # pid 不参与判定——claim 是一次性 CLI，pid 已死是默认状态。
        allowed = criteria["expired"]["ok"] and criteria["no_inflight"]["ok"]
        if not allowed:
            lines = [
                f"TAKEOVER DENIED {display}",
                f"  当前持有者={holder}（{criteria['pid_note']}）",
            ]
            for name, label in (("expired", "心跳过期"), ("no_inflight", "无在途改动")):
                mark = "PASS" if criteria[name]["ok"] else "FAIL"
                lines.append(f"  [{mark}] {label}：{criteria[name]['detail']}")
            if not criteria["expired"]["ok"]:
                if criteria["expired"]["raw"]:
                    lines.append(
                        "  刚过期、未满 30 分钟宽限（§6.1.4）：可能只是没来得及续租，先别碰；"
                        "等宽限过完再来。"
                    )
                else:
                    lines.append(
                        "  未过期：持有者可能仍在工作。claim 是一次性 CLI，pid 已退出是默认状态，"
                        "不能据此接管；等 TTL 到期，或让对方 release。"
                    )
            if not criteria["no_inflight"]["ok"]:
                lines.append(
                    "  有在途改动：先让原持有者提交/清理（或等它 release），不得接管——"
                    "接管会让对方未提交的 hunk 无处可去。"
                )
            raise ClaimDenied("\n".join(lines))

        old = dict(data)
        new_token = secrets.token_hex(16)
        payload = _marker_payload(
            display, key, owner, ttl_min, now, _token_hash(new_token)
        )
        takeover_note = {
            "old_owner": holder,
            "old_pid": old.get("pid"),
            "criteria": {name: criteria[name]["ok"] for name in ("expired", "no_inflight")},
            "detail": {name: criteria[name]["detail"] for name in ("expired", "no_inflight")},
            "pid_note": criteria["pid_note"],
            "reason": reason,
            "at": _iso(now),
        }
        record = {
            "action": "orphan_takeover",
            "logged_at": _iso(now),
            "epoch": now,
            "path": display,
            "old_owner": holder,
            "old_pid": old.get("pid"),
            "old_acquired_at": _iso(float(old.get("acquired_at") or 0)),
            "old_heartbeat_at": _iso(criteria["heartbeat"]),
            "new_owner": owner,
            "new_pid": os.getpid(),
            "reason": reason,
            "criteria": takeover_note["criteria"],
            "detail": takeover_note["detail"],
            "pid_note": criteria["pid_note"],
        }
        try:
            _append_takeover_log(record)
        except OSError as exc:
            raise ClaimDenied(
                f"TAKEOVER DENIED {display}\n  接管日志写入失败，占位文件未改：{exc}"
            ) from None
        try:
            _write_fd(fd, json.dumps(payload, ensure_ascii=False))
        except OSError as exc:
            _write_fd(fd, json.dumps(old, ensure_ascii=False))
            raise ClaimDenied(
                f"TAKEOVER DENIED {display}\n  占位文件写入失败，已回滚原持有者：{exc}"
            ) from None
        # 覆盖旧 sidecar：marker 哈希已换，原持有者手里的旧凭据和旧 sidecar 一并作废。
        sidecar_err = _write_token_file(key, new_token)

    if sidecar_err:
        print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
            f"WARN 凭据文件写入失败（{_token_path(key)}）：{sidecar_err}",
            file=sys.stderr,
        )
    try:
        _registry_record_claim(payload, old, close_reason="orphan_takeover", takeover=takeover_note)
    except ClaimDenied as exc:
        print(f"WARN 台账写入失败（接管仍有效）：{exc}", file=sys.stderr)
    print(
        f"TAKEOVER OK {display} 原持有者={holder} 新持有者={owner} "
        f"到期={_iso(now + _ttl(ttl_min, now))}\n"
        f"  判据："
        + " ".join(
            f"{name}={'PASS' if criteria[name]['ok'] else 'FAIL'}"
            for name in ("expired", "no_inflight")
        )
        + f"\n  {criteria['pid_note']}"
        + f"\n  理由：{reason}"
        + f"\n  日志：{TAKEOVER_LOG}"
    )
    print(  # pragma: allowlist secret — 打印的是 sidecar 路径，非凭据值
        _TOKEN_HINT.format(path=_token_path(key)))
    return EXIT_OK


#: sidecar 引入版本：早于它的 claim 签发时还没有「明文凭据落盘」这回事，
#: 所以「有 token_sha256、没有 sidecar」在那之前是正常形态，之后是异常。
SIDECAR_INTRO_COMMIT = "028f390f"


def _sidecar_intro_ts() -> float:
    raw = os.environ.get("MBDSDR_SIDECAR_INTRO_TS")
    if raw:
        try:
            return float(raw)
        except ValueError:
            raise ClaimDenied(f"REPAIR DENIED\n  MBDSDR_SIDECAR_INTRO_TS 不是数字：{raw!r}") from None
    commit = os.environ.get("MBDSDR_SIDECAR_INTRO_COMMIT") or SIDECAR_INTRO_COMMIT
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%ct", commit],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ClaimDenied(
            f"REPAIR DENIED\n  取不到 sidecar 引入版本 {commit} 的时间"
            f"（{exc.__class__.__name__}）；判不了是不是 legacy，按失败关闭拒绝。"
        ) from None
    if proc.returncode != 0 or not proc.stdout.strip():
        raise ClaimDenied(
            f"REPAIR DENIED\n  取不到 sidecar 引入版本 {commit} 的时间（git log 失败）；"
            "判不了是不是 legacy，按失败关闭拒绝。"
        )
    return float(proc.stdout.strip())


def _registry_close_repair(key: str, display: str, now: float, payload: Dict[str, Any]) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = _entry(data, key)
        entry["path"] = display
        # 手工写的占位文件可能没进过台账（本命令正是用来修这类状态的），
        # 补一条 history 再关闭，避免「台账里查不到这笔持有」。
        if not any(item.get("released_at") is None for item in entry["history"]):
            entry["history"].append(
                {
                    "owner": payload.get("owner"),
                    "acquired_at": payload.get("acquired_at"),
                    "heartbeat_at": payload.get("heartbeat_at"),
                    "ttl_min": payload.get("ttl_min"),
                    "pid": payload.get("pid"),
                }
            )
        _close_active(entry, now, "legacy_token_repair")
        entry["current"] = None

    _update_registry(mutate)


def cmd_legacy_token_repair(path: str, owner: str, reason: str, by: str = "") -> int:
    """修复「有凭据哈希、但明文从没落过盘」的 legacy claim（一次性、有审计）。

    只对同时满足下面三条的 claim 有效，任一不满足即拒（fail-closed）：
      ① 占位文件里有 `token_sha256`（说明它已经是凭据时代）；
      ② `.token` sidecar 不存在（明文凭据从没落盘，谁都出示不了）；
      ③ 签发时间早于 sidecar 引入版（`028f390f`）——那之前「无 sidecar」是正常形态。
    另加：`--owner` 必须与台账 owner 原文一致，`--reason` 必须非空。

    为什么不是通用后门：这三条把范围钉死在「sidecar 上线前签发、之后没人再动过」
    的一批 claim 上；正常 claim 有 sidecar、之后签发的 claim 一定该有 sidecar，
    都被条件②③ 挡住。
    """
    owner = owner.strip()
    reason = (reason or "").strip()
    if not owner:
        print("OWNER 不能为空", file=sys.stderr)
        return EXIT_USAGE
    if not reason:
        raise ClaimDenied(
            f"REPAIR DENIED {path}\n  必须用 --reason 写明为什么可以修（谁确认持有者已不在改这个文件）。"
        )

    key, display = canonical_path(path)
    marker = _marker_for(key)
    if not marker.exists():
        raise ClaimDenied(f"REPAIR DENIED {display}\n  该文件当前没有 claim，无需修复。")

    introduced = _sidecar_intro_ts()
    with _locked_fd(marker, create=False) as fd:
        data = _read_marker_fd(fd)

    holder = str(data.get("owner") or "").strip()
    acquired = float(data.get("acquired_at") or 0)
    heartbeat = float(data.get("heartbeat_at") or acquired)
    token_hash = str(data.get("token_sha256") or "")
    sidecar = _token_path(key)
    sidecar_exists = sidecar.exists()

    checks = [
        ("有 token_sha256（属凭据时代）", bool(token_hash),
         "该 claim 连凭据哈希都没有（更早的旧格式）：只能等 TTL 过期，或按新判据 takeover"),
        ("sidecar 缺失", not sidecar_exists,
         f"sidecar 在（{sidecar}）：持有者自己能 release/续租，不走 repair"),
        ("签发早于 sidecar 引入版", acquired < introduced,
         f"签发 {_iso(acquired)} 不早于 sidecar 引入 {_iso(introduced)}；"
         "本该有 sidecar 却缺失，属异常，需人工查因，不由本命令放行"),
        ("--owner 与台账一致", bool(holder) and holder == owner,
         f"台账持有者={holder or '<空>'}，你声明的是 {owner}"),
    ]
    failed = [item for item in checks if not item[1]]
    if failed:
        lines = [f"REPAIR DENIED {display}"]
        for name, ok, detail in checks:
            lines.append(f"  [{'PASS' if ok else 'FAIL'}] {name}：{detail}")
        raise ClaimDenied("\n".join(lines))

    now = _now()
    inflight = _inflight_lines(display)
    record = {
        "action": "legacy_token_repair",
        "logged_at": _iso(now),
        "epoch": now,
        "path": display,
        "old_owner": holder,
        "old_acquired_at": _iso(acquired),
        "old_heartbeat_at": _iso(heartbeat),
        "token_sha256_prefix": token_hash[:12],
        "sidecar_missing": True,
        "sidecar_intro_commit": os.environ.get("MBDSDR_SIDECAR_INTRO_COMMIT") or SIDECAR_INTRO_COMMIT,
        "sidecar_intro_at": _iso(introduced),
        "repaired_by": by.strip() or "<未声明>",
        "reason": reason,
        "inflight": "none" if inflight == [] else ("unknown" if inflight is None else "dirty"),
    }
    # 先落审计：审计写不进去就一字节都不动。
    try:
        _append_takeover_log(record)
    except OSError as exc:
        raise ClaimDenied(
            f"REPAIR DENIED {display}\n  审计日志写入失败，claim 原状未动：{exc}"
        ) from None
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ClaimDenied(
            f"REPAIR DENIED {display}\n  审计已记录，但占位文件删除失败（{exc}）；"
            "claim 仍是原状（完整保留），可直接重跑本命令"
        ) from None
    _remove_token_file(key)
    try:
        _registry_close_repair(key, display, now, data)
    except (ClaimDenied, OSError) as exc:
        print(
            f"WARN 台账写入失败（claim 已删除，闸门按「无 claim」处理）：{exc}",
            file=sys.stderr,
        )
    print(
        f"REPAIR OK {display} 原持有者={holder} 签发={_iso(acquired)} "
        f"（早于 sidecar 引入 {_iso(introduced)}）\n"
        f"  claim 已完整删除（无半截状态）；台账 current 已置空（closed_reason=legacy_token_repair）\n"
        f"  审计：{TAKEOVER_LOG}（action=legacy_token_repair，含 repaired_by 与 reason）\n"
        f"  修复人：{record['repaired_by']}；理由：{reason}"
    )
    return EXIT_OK


def _suspect_dead_session(payload: Dict[str, Any], now: float) -> str:
    """提示用：判断「持有者可能已经不在了」，返回提示文本或空串。

    只在 ``status`` 里显示，**不参与** takeover/夺取判定——判定仍是
    「心跳过期 + 宽限 + 无在途改动」（见 ``_orphan_criteria``）。三个条件同时
    满足才提示：

    1. claim 还没过期（过期了 ``status`` 已经显示 EXPIRED，不需要重复提示）；
    2. 心跳年龄已超过 TTL 的 ``SUSPECT_HEARTBEAT_RATIO``（提示阈值，非判据）；
    3. pid 记录的进程已不存在，或 pid 被复用（号还在但创建时刻对不上）。

    关于「pid 被复用」的方向（这条判据唯一的保守性代价）：若只看 pid 号存不
    存在，死进程的号被系统复用会让这条 claim「看起来还活着」——不会误抢，
    但会让文件一直解不开，直到 TTL 到期。所以用 ``pid_create_time`` 二次
    校验：号还在、创建时刻对不上，判为原进程已死。即便判死，这里也只是提示，
    不会因此提前夺权。
    """
    pid = payload.get("pid")
    if pid is None:
        return ""
    ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
    age_s = max(0.0, now - heartbeat)
    if ttl_s <= 0 or age_s >= ttl_s:  # 已过期：交给 EXPIRED 展示
        return ""
    if age_s < ttl_s * SUSPECT_HEARTBEAT_RATIO:
        return ""
    try:
        state, create_time, _cmd = _proc_snapshot(int(pid))
    except (TypeError, ValueError, OSError):
        return ""
    if state == "gone":
        return "疑似死会话(pid 已不在且心跳过半；仅提示，非判据)"
    recorded = payload.get("pid_create_time")
    if state == "alive" and create_time is not None and recorded is not None:
        if not _same_process_create_time(float(recorded), float(create_time)):
            return "疑似死会话(pid 已被复用且心跳过半；仅提示，非判据)"
    return ""


def _describe(payload: Dict[str, Any], now: float) -> str:
    owner = payload.get("owner", "<unknown>")
    ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
    held_s = max(0.0, now - heartbeat)
    expired_s = held_s - ttl_s
    if expired_s > 0:
        state = f"EXPIRED(+{_minutes(expired_s)}min)"
    else:
        state = f"剩余 {_minutes(ttl_s - held_s)}min"
    # 有凭据 = 身份可验证；没有 = 旧格式，只能靠过期/接管，不能按名字放行。
    ident = "token" if payload.get("token_sha256") else "legacy(名不可作身份)"
    renewals = payload.get("renewals")
    renew_txt = f"renew={len(renewals)}" if isinstance(renewals, list) else "renew=NA"
    suspect = _suspect_dead_session(payload, now)
    return (
        f"{payload.get('path', '?'):<44} owner={owner:<18} held={_minutes(held_s)}/{_minutes(ttl_s)}min "
        f"{state} pid={payload.get('pid')} id={ident} {renew_txt} "
        f"since={_iso(float(payload.get('acquired_at') or heartbeat))}"
        + (f" {suspect}" if suspect else "")
    )


def _renew_audit_rows(now: float) -> List[Dict[str, Any]]:
    """活跃 claim 的续租审计行。

    「老数据」判据：跨度（heartbeat − acquired）超过 1.05×TTL——正常 claim 不续
    的话心跳会停在 TTL，超过就说明中途必然续租过，而旧格式没有 `renewals` 记录。
    这类条目报「历史不可审计」，**不当 0 次**处理。
    """
    rows: List[Dict[str, Any]] = []
    for marker in sorted(MARKER_DIR.glob("*.claim")):
        data = _peek_marker(marker)
        if not data:
            continue
        heartbeat = float(data.get("heartbeat_at") or data.get("acquired_at") or 0)
        acquired = float(data.get("acquired_at") or heartbeat)
        ttl_s = float(data.get("ttl_min") or 0.0) * 60.0
        if heartbeat + ttl_s <= now:
            continue  # 过期的不算当前占用
        total_s = max(0.0, now - acquired)
        renewals = data.get("renewals")
        known = isinstance(renewals, list)
        cap_min = float(data.get("max_hold_min") or DEFAULT_MAX_HOLD_MIN)
        rows.append(
            {
                "path": data.get("path"),
                "owner": data.get("owner"),
                "key": data.get("key"),
                "renew_known": known,
                "renew_count": len(renewals) if known else None,
                "total_hold_min": round(total_s / 60.0, 1),
                "total_hold_s": round(total_s, 1),
                "cap_min": cap_min,
                "cap_source": data.get("max_hold_source") or "default(未记录)",
                "legacy_unauditable": (not known) and (heartbeat - acquired) > ttl_s * 1.05,
                "over_cap": total_s > cap_min * 60.0,
                "last_reason": (renewals[-1].get("reason", "") if known and renewals else ""),
            }
        )
    return rows


def _resolve_legacy_due(cli_value: Optional[str] = None) -> Tuple[Any, str]:
    """老数据清账到期日：CLI > 环境变量 > 默认。来源随输出打印（放宽也要看得见）。"""
    if cli_value is not None:
        raw, source = cli_value, "--legacy-due"
    elif os.environ.get(LEGACY_AUDIT_DUE_ENV):
        raw, source = os.environ[LEGACY_AUDIT_DUE_ENV], f"env {LEGACY_AUDIT_DUE_ENV}"
    else:
        raw, source = DEFAULT_LEGACY_AUDIT_DUE, "default"
    try:
        due = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"清账到期日必须是 yyyy-mm-dd（{source}={raw}）") from exc
    return due, source


def cmd_audit_renew(as_json: bool = False, legacy_due: Optional[str] = None) -> int:
    rows = _renew_audit_rows(_now())
    try:
        due, due_source = _resolve_legacy_due(legacy_due)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    days_left = (due - datetime.now().date()).days
    known = [r for r in rows if r["renew_known"]]
    legacy = [r for r in rows if r["legacy_unauditable"]]
    over = [r for r in rows if r["over_cap"]]
    for row in legacy:
        row["legacy_due"] = due.isoformat()
    overdue = bool(legacy) and days_left < 0
    if as_json:
        print(
            json.dumps(
                {
                    "active": len(rows),
                    "renew_known": len(known),
                    "legacy_unauditable": len(legacy),
                    "legacy_due": due.isoformat(),
                    "legacy_due_source": due_source,
                    "legacy_days_left": days_left,
                    "legacy_overdue": overdue,
                    "over_cap": len(over),
                    "rows": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"活跃 claim {len(rows)} 条：有续租记录 {len(known)} 条；"
            f"历史不可审计（无续租记录但跨度 > 1.05×TTL）{len(legacy)} 条；"
            f"超上限 {len(over)} 条"
        )
        print(
            f"老数据清账到期 {due.isoformat()}（{due_source}，"
            f"{'已逾期 ' + str(-days_left) + ' 天' if days_left < 0 else '剩 ' + str(days_left) + ' 天'}）"
        )
        for row in rows:
            if row["legacy_unauditable"]:
                print(
                    f"  UNAUDITABLE(老数据) {row['path']} owner={row['owner']} "
                    f"已持有={row['total_hold_min']}min 续租记录=不可知（不是 0 次） "
                    f"清账到期={row['legacy_due']}"
                )
            elif row["renew_known"]:
                print(
                    f"  RENEW {row['path']} owner={row['owner']} 续租={row['renew_count']} 次 "
                    f"累计持有={row['total_hold_min']}min 上限={row['cap_min']:g}min "
                    f"最后理由={row['last_reason'][:40]}"
                )
            if row["over_cap"]:
                print(
                    f"  OVER-CAP {row['path']} owner={row['owner']} "
                    f"持有={row['total_hold_min']}min > 上限 {row['cap_min']:g}min"
                    f"（{row['cap_source']}）"
                )
    if overdue:
        print(
            f"OVERDUE(到期未清) 清账到期日 {due.isoformat()} 已过 {days_left * -1} 天，"
            f"{len(legacy)} 条老 claim 仍未补续租记录：按到期处理，不许当永久状态。",
            file=sys.stderr if as_json else sys.stdout,
        )
        return EXIT_OVERDUE
    return EXIT_FINDINGS if (legacy or over) else EXIT_OK


def cmd_status(path: Optional[str]) -> int:
    now = _now()
    if path:
        key, display = canonical_path(path)
        payload = _peek_marker(_marker_for(key))
        if payload is None:
            print(f"STATUS {display}: 未占用")
            return EXIT_OK
        print(f"STATUS {display}: {_describe(payload, now)}")
        return EXIT_OK

    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for marker in sorted(MARKER_DIR.glob("*.claim")):
        payload = _peek_marker(marker)
        if payload is None:
            continue
        items.append(payload)
    if not items:
        print("STATUS: 无任何 claim")
        return EXIT_OK
    expired = 0
    suspects = 0
    for payload in items:
        ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
        heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
        if now - heartbeat > ttl_s:
            expired += 1
        if _suspect_dead_session(payload, now):
            suspects += 1
        print(_describe(payload, now))
    print(
        f"STATUS: {len(items)} 条 claim，其中 {expired} 条已过期（过期后他人可夺取）；"
        f"{suspects} 条疑似死会话（pid 已不在/被复用且心跳过半，**仅提示**，"
        f"接管仍按心跳过期 + 宽限 + 无在途改动判）"
    )
    return EXIT_OK


def _audit_rows(now: float) -> list:
    """扫描全部占位文件，标记「已过期但文件在过期后仍被修改」的漂移。

    判据：`now - heartbeat_at > ttl`（claim 已过期）且目标文件
    `mtime > acquired_at`（持有者声明的时间之后文件又被改过）。后者说明
    有人没走 claim 就在改，或持有者早已换进程却还在写——台账的占用保护
    在这条上是失效的，必须报警而不是等下一次事故。
    """
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for marker in sorted(MARKER_DIR.glob("*.claim")):
        payload = _peek_marker(marker)
        if payload is None:
            continue
        raw = str(payload.get("path") or "")
        p = Path(raw)
        target = p if p.is_absolute() else ROOT / p
        heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
        acquired = float(payload.get("acquired_at") or heartbeat)
        ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
        expired_s = now - heartbeat - ttl_s
        try:
            mtime = target.stat().st_mtime
            exists = True
        except OSError:
            mtime = None
            exists = False
        later_s = (mtime - acquired) if exists and mtime is not None else None
        rows.append(
            {
                "path": raw,
                "owner": payload.get("owner"),
                "pid": payload.get("pid"),
                "ttl_min": payload.get("ttl_min"),
                "expired_min": round(expired_s / 60.0, 1) if expired_s > 0 else 0.0,
                "exists": exists,
                "mtime_after_acquired_min": round(later_s / 60.0, 1) if later_s is not None else None,
                "stale_edit": bool(expired_s > 0 and exists and later_s is not None and later_s > 0),
            }
        )
    return rows


def cmd_audit_stale(as_json: bool = False) -> int:
    """子命令 `audit-stale`：只输出，不改任何 claim；有漂移返回退出码 3。"""
    now = _now()
    rows = _audit_rows(now)
    hits = [r for r in rows if r["stale_edit"]]
    if as_json:
        print(
            json.dumps(
                {"now": now, "total": len(rows), "stale_edit": hits, "rows": rows},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for r in hits:
            print(
                f"STALE-EDIT {r['path']} owner={r['owner']} "
                f"过期+{r['expired_min']}min "
                f"mtime 晚于 acquired {r['mtime_after_acquired_min']}min pid={r['pid']}"
            )
        print(
            f"AUDIT: {len(rows)} 条 claim，其中 {len(hits)} 条「已过期但文件在声明时间之后仍被修改」"
        )
    return EXIT_FINDINGS if hits else EXIT_OK


# ---------------------------------------------------------------------------
# cleanup-stale：按证据释放过期 claim（没有「超过 N 分钟一律释放」的分支）
# ---------------------------------------------------------------------------
# 与 audit-stale 的分工：
#   * audit-stale 只回答「过期后文件还被改过」——报警，不改状态；
#   * cleanup-stale 回答「这条过期 claim 现在能不能注销」，且只有四类证据
#     **全部 PASS** 才允许 --release。任何一项是 GAP（证不出来）或 FAIL
#     （证出来有风险），都只报「疑似失联 + 缺口」，不动占位。
# 为什么不复用 takeover：takeover 是「换个人持有」，清理是「确认无人后注销」；
# 判据也不同（takeover 只看过期 + 无在途改动，不清算持有者是否改过文件）。

def _evidence(
    status: str, detail: str, weight: str = "strong", undecidable: str = ""
) -> Dict[str, str]:
    """一条证据：PASS / GAP（证不出来）/ FAIL（证出来有风险）。

    ``weight="weak"`` 表示这条证据**不能单独支撑释放**，只作一致性核对。
    目前只有「进程不存在」是弱证据：claim 是一次性 CLI，pid 在 claim 返回后
    就已经不存在，对绝大多数 claim 恒真。

    ``undecidable`` 非空表示「这类缺口不会随时间变成可证」——目前只有
    「claim 之后文件有提交」，用于把该条从 SUSPECT 分到 UNDECIDABLE。
    """
    item = {"status": status, "detail": detail, "weight": weight}
    if undecidable:
        item["undecidable"] = undecidable
    return item


def _cleanup_grace_s() -> float:
    # 与 takeover 共用同一个宽限口径（§6.1.4），不另造一套时间阈值。
    return float(os.environ.get("MBDSDR_TAKEOVER_GRACE_MIN") or 30.0) * 60.0


def _lease_evidence(payload: Dict[str, Any], now: float) -> Tuple[Dict[str, str], float]:
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
    ttl_s = float(payload.get("ttl_min") or 0.0) * 60.0
    held_s = max(0.0, now - heartbeat)
    expired_s = held_s - ttl_s
    grace_s = _cleanup_grace_s()
    if expired_s <= 0:
        return _evidence(
            "FAIL", f"租约未过期（剩余 {_minutes(ttl_s - held_s)}min），不是清理对象"
        ), expired_s
    if expired_s < grace_s:
        return _evidence(
            "FAIL",
            f"刚过期 {_minutes(expired_s)}min，未满 {_minutes(grace_s)}min 宽限"
            f"（§6.1.4：可能只是没来得及续租）",
        ), expired_s
    return _evidence(
        "PASS",
        f"心跳 {_iso(heartbeat)}，TTL {_minutes(ttl_s)}min，已过期 {_minutes(expired_s)}min",
    ), expired_s


def _holder_evidence(payload: Dict[str, Any]) -> Dict[str, str]:
    """持有进程证据。**「进程不存在」是弱证据**，不能单独当死亡证明。"""
    pid = payload.get("pid")
    if pid is None:
        return _evidence("GAP", "未记录 pid，无法核对持有进程")
    try:
        state, create_time, _cmd = _proc_snapshot(int(pid))
    except (TypeError, ValueError, OSError) as exc:
        return _evidence("GAP", f"取不到 pid {pid} 的状态（{exc.__class__.__name__}）")
    if state == "gone":
        return _evidence(
            "PASS",
            f"pid {pid} 不存在（注意：claim 是一次性 CLI，此项对多数 claim 恒真，"
            f"只作一致性证据，不单独支撑释放）",
            "weak",
        )
    if state == "unknown":
        return _evidence("GAP", f"pid {pid} 状态未知")
    recorded = payload.get("pid_create_time")
    if create_time is not None and recorded is not None:
        if _same_process_create_time(float(recorded), float(create_time)):
            return _evidence(
                "FAIL", f"pid {pid} 仍存在且创建时刻与记录一致（持有者可能仍在工作）"
            )
        return _evidence("PASS", f"pid {pid} 号已被复用（原进程不存在）", "weak")
    return _evidence("GAP", f"pid {pid} 存在但没有创建时刻，无法区分原进程与复用")


def _git_run(args: List[str], timeout: float = 60.0) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git_status_snapshot() -> Optional[Dict[str, str]]:
    """一次 `git status` 拿到全仓改动；返回 {路径: XY}，失败返回 None。"""
    proc = _git_run(["status", "--porcelain=v1", "-z"], timeout=120.0)
    if proc is None or proc.returncode != 0:
        return None
    items = proc.stdout.split("\0")
    out: Dict[str, str] = {}
    i = 0
    while i < len(items):
        item = items[i]
        i += 1
        if not item or len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        if xy[0] in ("R", "C") and i < len(items):  # 重命名：下一条是新路径
            path = items[i]
            i += 1
        out[path.replace("\\", "/")] = xy
    return out


def _git_tracked_set() -> Optional[set]:
    proc = _git_run(["ls-files", "-z"], timeout=120.0)
    if proc is None or proc.returncode != 0:
        return None
    return {
        item.replace("\\", "/")
        for item in proc.stdout.split("\0")
        if item
    }


def _inflight_evidence(display: str, status: Optional[Dict[str, str]]) -> Dict[str, str]:
    if status is None:
        return _evidence("GAP", "无法查询工作区（git status 失败）")
    xy = status.get(display.replace("\\", "/"))
    if xy is None:
        return _evidence("PASS", "工作区无该文件的未提交改动")
    return _evidence("FAIL", f"工作区有该文件的未提交改动（{xy}）")


def _content_evidence(
    payload: Dict[str, Any],
    display: str,
    acquired: float,
    tracked: Optional[set],
    status: Optional[Dict[str, str]],
) -> Dict[str, str]:
    """文件内容是否与取得时一致。

    优先用 claim 时记录的 ``content_sha256``；旧 claim 没有基线时，用 git 推导：
    「工作区干净 + 该文件最后一次提交早于 acquired_at」⇒ claim 之后内容没变过。
    推导不成立（有提交 / 未跟踪 / 无历史）一律 GAP，不猜。
    """
    target = _target_path(display)
    if not target.is_file():
        return _evidence("GAP", "目标文件不存在（删除本身也可能是在途工作）")
    current = _sha256_file(target)
    if current is None:
        return _evidence("GAP", "目标文件读不到，无法计算内容哈希")
    recorded = str(payload.get("content_sha256") or "")
    if recorded:
        if current == recorded:
            return _evidence("PASS", "内容哈希与 claim 时基线一致（content_sha256）")
        return _evidence("FAIL", "内容哈希与 claim 时基线不符：文件在持有期内被改过")

    if tracked is None:
        return _evidence("GAP", "无内容基线，且 git 查询失败")
    if display.replace("\\", "/") not in tracked:
        return _evidence("GAP", "无内容基线，且文件不在 git 跟踪下（无法推导）")
    if status is None:
        return _evidence("GAP", "无内容基线，且 git status 失败")
    if display.replace("\\", "/") in status:
        return _evidence("GAP", "无内容基线，且工作区有未提交改动，无法推导")
    proc = _git_run(["log", "-1", "--format=%ct", "--", display], timeout=30.0)
    if proc is None or proc.returncode != 0:
        return _evidence("GAP", "无内容基线，且查不到该文件的提交历史")
    raw = proc.stdout.strip()
    if not raw:
        return _evidence("GAP", "无内容基线，且该文件没有提交记录")
    last_ts = float(raw)
    if last_ts > acquired + 1.0:
        return _evidence(
            "GAP",
            f"无内容基线；该文件在 claim 之后有提交（{_iso(last_ts)} > "
            f"{_iso(acquired)}），无法证明未被改动",
            "weak",
            undecidable="post_claim_commit",
        )
    return _evidence(
        "PASS",
        f"无记录基线；git 推导：工作区干净，最后一次提交 {_iso(last_ts)} "
        f"早于 claim {_iso(acquired)}",
        "weak",
    )


def _git_facts() -> Dict[str, Any]:
    """一次取全仓事实，避免为每条 claim 重复起 git（几百条时是主要开销）。"""
    return {"tracked": _git_tracked_set(), "status": _git_status_snapshot()}


def _cleanup_row(
    payload: Dict[str, Any], now: float, facts: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    display = str(payload.get("path") or "")
    heartbeat = float(payload.get("heartbeat_at") or payload.get("acquired_at") or now)
    acquired = float(payload.get("acquired_at") or heartbeat)
    lease, expired_s = _lease_evidence(payload, now)
    evidence: Dict[str, Dict[str, str]] = {"lease": lease}
    if lease["status"] == "PASS":
        # 只有租约已过宽限的才继续取证：未过期/刚过期的不是清理对象。
        facts = facts if facts is not None else _git_facts()
        evidence["holder"] = _holder_evidence(payload)
        evidence["content"] = _content_evidence(
            payload, display, acquired, facts["tracked"], facts["status"]
        )
        evidence["no_inflight"] = _inflight_evidence(display, facts["status"])

    if lease["status"] != "PASS":
        category = "wait" if expired_s > 0 else "live"
    elif all(item["status"] == "PASS" for item in evidence.values()):
        category = "releasable"
    elif evidence["content"].get("undecidable"):
        # 口径按「claim 之后文件有提交」这一条硬分：只要命中，内容就永远
        # 无法回溯到取得时——不管其余证据是 PASS 还是暂时缺，都不再是待办。
        category = "undecidable"
    else:
        category = "suspect"
    gaps = [
        f"{name}({item['status']}: {item['detail']})"
        for name, item in evidence.items()
        if item["status"] != "PASS"
    ]
    return {
        "path": display,
        "key": str(payload.get("key") or ""),
        "owner": str(payload.get("owner") or ""),
        "category": category,
        "evidence": evidence,
        "gaps": gaps,
        "undecidable_reason": str(evidence.get("content", {}).get("undecidable") or ""),
        "expired_min": round(expired_s / 60.0, 1) if expired_s > 0 else 0.0,
        "acquired_at": acquired,
        "heartbeat_at": heartbeat,
    }


def _append_cleanup_log(record: Dict[str, Any]) -> None:
    CLEANUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CLEANUP_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def _append_cleanup_trend(record: Dict[str, Any]) -> None:
    """每次运行的计数快照；失败只 WARN，不改变任何结论。"""
    CLEANUP_TREND.parent.mkdir(parents=True, exist_ok=True)
    with open(CLEANUP_TREND, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()


def _owner_line(owner: Any) -> str:
    """``线名/会话短号`` → 线名；清理账里单独留一列，方便按线反查。"""
    return str(owner or "").split("/", 1)[0].strip()


def _registry_cleanup(key: str, display: str, now: float, detail: Dict[str, Any]) -> None:
    def mutate(data: Dict[str, Any]) -> None:
        entry = _entry(data, key)
        entry["path"] = display
        _close_active(entry, now, "stale_cleanup", detail, extra_key="cleanup")
        entry["current"] = None

    _update_registry(mutate)


def _release_stale_row(row: Dict[str, Any], by: str, reason: str) -> Dict[str, Any]:
    """注销一条已证明无人且未被改动的过期 claim；审计账写前一行、成功后一行。"""
    key = row["key"]
    display = row["path"]
    marker = _marker_for(key)
    now = _now()
    with _locked_fd(marker, create=False) as fd:
        data = _read_marker_fd(fd)
        fresh = _cleanup_row(data, now, _git_facts())
        if fresh["category"] != "releasable":
            raise ClaimDenied(
                f"CLEANUP DENIED {display}\n"
                f"  复核时证据已不满足，未释放：{'；'.join(fresh['gaps']) or '类别已变化'}"
            )
        before = dict(data)
        record = {
            "action": "stale_cleanup",
            "phase": "before",
            "logged_at": _iso(now),
            "epoch": now,
            "path": display,
            "owner": data.get("owner"),
            "line": _owner_line(data.get("owner")),
            "by": by,
            "reason": reason,
            "evidence": fresh["evidence"],
            "expired_min": fresh["expired_min"],
            "claim_before": before,
        }
        try:
            _append_cleanup_log(record)
        except OSError as exc:
            raise ClaimDenied(
                f"CLEANUP DENIED {display}\n  清理账写入失败，占位文件未改：{exc}"
            ) from None

    # 锁外删除：Windows 上占位文件被自己锁着时删不掉（与 cmd_release 同一做法）。
    # 删前再核对一次指纹：期间若有续租/夺取，heartbeat_at 或凭据会变，此时放弃。
    current = _peek_marker(marker)
    fingerprint = ("acquired_at", "heartbeat_at", "token_sha256", "owner")
    if current is None:
        raise ClaimDenied(f"CLEANUP DENIED {display}\n  复核后占位已不存在（可能被他人释放）。")
    if any(current.get(name) != before.get(name) for name in fingerprint):
        try:
            _append_cleanup_log({
                "action": "stale_cleanup",
                "phase": "aborted",
                "logged_at": _iso(_now()),
                "path": display,
                "owner": before.get("owner"),
                "by": by,
                "reason": "释放前占位已变化（疑似续租或换人）",
            })
        except OSError:
            pass
        raise ClaimDenied(
            f"CLEANUP DENIED {display}\n"
            f"  复核后占位已变化（心跳/凭据/持有者不同），放弃释放。"
        )
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        try:
            _append_cleanup_log({
                "action": "stale_cleanup",
                "phase": "failed",
                "logged_at": _iso(_now()),
                "path": display,
                "owner": before.get("owner"),
                "by": by,
                "error": str(exc),
            })
        except OSError:
            pass
        raise ClaimDenied(
            f"CLEANUP DENIED {display}\n  删除占位文件失败，未释放：{exc}"
        ) from None
    _remove_token_file(key)
    try:
        _registry_cleanup(key, display, _now(), {
            "by": by,
            "reason": reason,
            "evidence": fresh["evidence"],
            "at": _iso(now),
        })
    except ClaimDenied as exc:
        print(f"WARN 台账写入失败（释放仍有效）：{exc}", file=sys.stderr)
    try:
        _append_cleanup_log({
            "action": "stale_cleanup",
            "phase": "after",
            "logged_at": _iso(_now()),
            "path": display,
            "owner": before.get("owner"),
            "line": _owner_line(before.get("owner")),
            "by": by,
            "reason": reason,
            "released": True,
            "evidence": fresh["evidence"],
            "expired_min": fresh["expired_min"],
            "claim_after": None,
        })
    except OSError as exc:
        print(f"WARN 清理账收尾写入失败（释放已生效）：{exc}", file=sys.stderr)
    return record


def cmd_cleanup_stale(
    as_json: bool = False,
    release: bool = False,
    by: str = "",
    reason: str = "",
    only_path: str = "",
) -> int:
    by = (by or "").strip()
    reason = (reason or "").strip()
    if release and not by:
        print("CLEANUP DENIED：--release 必须带 --by（谁执行的清理）", file=sys.stderr)
        return EXIT_USAGE
    if release and not reason:
        print("CLEANUP DENIED：--release 必须带 --reason（依据/场景，写进清理账）", file=sys.stderr)
        return EXIT_USAGE

    only_key = None
    if only_path.strip():
        only_key, _display = canonical_path(only_path)
    now = _now()
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    facts = _git_facts()
    rows: List[Dict[str, Any]] = []
    #: 记了内容基线的 claim 条数。新 claim 才开始记 `content_sha256`，
    #: 所以这个数上升 + `undecidable_ratio` 下降，才说明「记 hash」真的生效；
    #: 三个月后比例还是 87%，就是没生效——只能靠趋势看，不能靠记忆。
    claims_with_content_hash = 0
    for marker in sorted(MARKER_DIR.glob("*.claim")):
        payload = _peek_marker(marker)
        if payload is None:
            continue
        if only_key and str(payload.get("key") or "") != only_key:
            continue
        if str(payload.get("content_sha256") or ""):
            claims_with_content_hash += 1
        rows.append(_cleanup_row(payload, now, facts))

    groups = {
        name: [row for row in rows if row["category"] == name]
        for name in ("releasable", "suspect", "undecidable", "wait", "live")
    }
    released: List[str] = []
    failed: List[Dict[str, str]] = []
    if release:
        for row in groups["releasable"]:
            try:
                _release_stale_row(row, by, reason)
                released.append(row["path"])
            except ClaimDenied as exc:
                failed.append({"path": row["path"], "error": str(exc)})

    undecidable_reasons: Dict[str, int] = {}
    for row in groups["undecidable"]:
        reason_key = row["undecidable_reason"] or "unknown"
        undecidable_reasons[reason_key] = undecidable_reasons.get(reason_key, 0) + 1
    undecidable_ratio = (
        round(len(groups["undecidable"]) / len(rows), 4) if rows else 0.0
    )
    undecidable_note = (
        "claim 之后文件有提交，内容已无法回溯到取得时；这一类不会因为时间推移"
        "变得可判定，也不是「文件清白」的证明，只是证明不了。"
    )

    # 趋势只存计数，不存路径：UNDECIDABLE 不做待办，但必须能看出涨跌。
    # `undecidable_ratio` / `claims_with_content_hash` 是一对：前者降、后者升，
    # 才说明新 claim 记内容基线真的在起作用（见 docs/code-guards.md 复合入口）。
    trend = {
        "at": _iso(now),
        "epoch": now,
        "release": bool(release),
        "total": len(rows),
        "summary": {name: len(items) for name, items in groups.items()},
        "undecidable_reasons": undecidable_reasons,
        "undecidable_ratio": undecidable_ratio,
        "claims_with_content_hash": claims_with_content_hash,
    }
    try:
        _append_cleanup_trend(trend)
    except OSError as exc:
        print(f"WARN 趋势快照写入失败（不影响结论）：{exc}", file=sys.stderr)

    if as_json:
        public_rows = [
            {
                **{k: v for k, v in row.items() if k != "marker"},
                # 补法只给 SUSPECT：其余类别的缺口不构成「怎么补」的待办。
                "hint": _suspect_hint(row) if row["category"] == "suspect" else "",
            }
            for row in rows
        ]
        print(json.dumps({
            "now": now,
            "total": len(rows),
            "released": released,
            "failed": failed,
            "summary": {name: len(items) for name, items in groups.items()},
            "undecidable": {
                "count": len(groups["undecidable"]),
                "ratio": undecidable_ratio,
                "reasons": undecidable_reasons,
                "note": undecidable_note,
            },
            "exit_code": (
                EXIT_DENIED if failed
                else EXIT_FINDINGS if groups["releasable"] and not release
                else EXIT_OK
            ),
            "rows": public_rows,
        }, ensure_ascii=False, indent=2))
        return (
            EXIT_DENIED if failed
            else EXIT_FINDINGS if groups["releasable"] and not release
            else EXIT_OK
        )

    print(
        f"CLEANUP: 扫描 {len(rows)} 条 claim；租约未过期 {len(groups['live'])}；"
        f"过期未满宽限 {len(groups['wait'])}；"
        f"【待办·可释放未释放】{len(groups['releasable'])}；"
        f"UNDECIDABLE {len(groups['undecidable'])}（{undecidable_ratio:.1%}）；"
        f"SUSPECT {len(groups['suspect'])}"
        + (f"；本次已释放 {len(released)}" if release else "（未加 --release，未改动任何占位）")
    )
    for path in released:
        print(f"  RELEASED {path}")
    for item in failed:
        print(f"  FAILED {item['path']}：{item['error'].splitlines()[-1]}")
    if not release:
        for row in groups["releasable"]:
            marks = "；".join(
                f"{name}={item['status']}" for name, item in row["evidence"].items()
            )
            print(
                f"  READY {row['path']} owner={row['owner']} "
                f"过期={row['expired_min']}min 证据：{marks}"
            )
    for row in groups["suspect"]:
        print(
            f"  SUSPECT {row['path']} owner={row['owner']} "
            f"过期={row['expired_min']}min 证据缺口：" + "；".join(row["gaps"])
        )
        hint = _suspect_hint(row)
        if hint:
            print(f"    补法：{hint}")
    print(
        f"  UNDECIDABLE {len(groups['undecidable'])} 条（{undecidable_ratio:.1%}）；"
        f"原因分布：" + (
            "、".join(f"{name}={count}" for name, count in sorted(undecidable_reasons.items()))
            or "无"
        )
    )
    print(f"    说明：{undecidable_note}")
    print("    处置：只计数、只做趋势（D:\\mbdsdr\\.locks\\claim_cleanup_trend.jsonl），"
          "不进待办、不改退出码。")
    if groups["wait"]:
        print(
            f"  WAIT {len(groups['wait'])} 条刚过期未满宽限：按 §6.1.4 等宽限自然过完，"
            f"本工具不提前释放"
        )
    if failed:
        print("CLEANUP: 有释放被拒绝（退出码 2）：见上 FAILED，占位未改。")
    elif groups["releasable"] and not release:
        print(
            "CLEANUP: 主数非空（退出码 3）：有可释放项但未加 --release。"
            "没有「超过 N 分钟一律释放」的分支，证据缺一项就不释放。"
        )
    else:
        print(
            "CLEANUP: 主数为空（退出码 0）。UNDECIDABLE / SUSPECT 只报告，"
            "不代表需要处理。"
        )
    return (
        EXIT_DENIED if failed
        else EXIT_FINDINGS if groups["releasable"] and not release
        else EXIT_OK
    )


def _suspect_hint(row: Dict[str, Any]) -> str:
    """按缺口给出补证据的方向；不替人做判断。"""
    bad = {name for name, item in row["evidence"].items() if item["status"] != "PASS"}
    hints: List[str] = []
    if "holder" in bad:
        hints.append("核对 pid 与创建时刻；无法核对时等 TTL 到期后由持有者 release")
    if "content" in bad:
        item = row["evidence"]["content"]
        detail = str(item.get("detail") or "")
        if item["status"] == "FAIL":
            hints.append("内容与基线不符：先人工确认改动归属，不能自动释放")
        elif "不存在" in detail:
            hints.append("文件已不存在：先确认删除是否属于在途工作，再决定 release")
        elif "读不到" in detail:
            hints.append("文件读不到：先确认路径/权限，再复核内容")
        elif "不在 git 跟踪下" in detail:
            hints.append("未跟踪文件无法用 git 推导：确认未被改动后 release 并重新 claim"
                         "（新 claim 会记 content_sha256）")
        elif "工作区有未提交改动" in detail:
            hints.append("工作区有未提交改动：先收掉或确认归属，再复核")
        else:
            hints.append("确认文件未被改动后 release 并重新 claim"
                         "（新 claim 会记 content_sha256）")
    if "no_inflight" in bad:
        hints.append("先收掉工作区未提交改动，或确认归属后再复核")
    return "；".join(hints)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="共享文件占用锁（原子占位 + TTL 心跳 + 原持有者释放）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    claim = sub.add_parser("claim", help="占用/续租一个共享文件")
    claim.add_argument("path", help="仓库相对路径，如 desktop/main_window.py")
    claim.add_argument("--owner", required=True, help="线名/会话名")
    claim.add_argument("--ttl-min", type=float, default=10.0, help="租期（分钟），默认 10")
    claim.add_argument(
        "--token",
        default=None,
        help="续租已绑定凭据的 claim；必须等于 claim 时输出的一次性凭据"
        "（会进进程列表，优先用 --token-file）",
    )
    claim.add_argument(
        "--token-file",
        default=None,
        help="从文件读凭据（sidecar 或 claim 时写出的文件），避免凭据进 argv",
    )
    claim.add_argument(
        "--adopt-legacy",
        action="store_true",
        help="仅用于旧格式 claim 的一次性迁移：按 owner 名匹配并签发凭据（之后同名不再放行）",
    )

    claim.add_argument(
        "--reason",
        default="",
        help="续租必填：这次为什么继续持有（首次 claim 可当作取得目的记录）",
    )
    claim.add_argument(
        "--max-hold-min",
        type=float,
        default=None,
        help=f"累计持有上限（分钟），默认 {DEFAULT_MAX_HOLD_MIN:g}；改动会写进占位文件与续租记录",
    )

    release = sub.add_parser("release", help="释放（仅原持有者，需 claim 时签发的 --token）")
    release.add_argument("path")
    release.add_argument("--owner", required=True)
    release.add_argument(
        "--token",
        default=None,
        help="claim 时签发的一次性凭据；同名不等于同一持有者，没有凭据不能释放"
        "（会进进程列表，优先用 --token-file）",
    )
    release.add_argument(
        "--token-file",
        default=None,
        help="从文件读凭据，避免凭据进 argv",
    )

    status = sub.add_parser("status", help="查看占用状态")
    status.add_argument("path", nargs="?")

    audit = sub.add_parser(
        "audit-stale",
        help="扫描「claim 已过期但文件 mtime 晚于 acquired_at」的漂移（只读，有命中退出码 3）",
    )
    audit.add_argument("--json", action="store_true", help="输出机器可读 JSON")

    cleanup = sub.add_parser(
        "cleanup-stale",
        help="按证据注销过期 claim（默认只审计；四项证据全 PASS 才释放，无时间一刀切）",
    )
    cleanup.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    cleanup.add_argument(
        "--release",
        action="store_true",
        help="真正注销可释放项（默认只列出；必须同时给 --by 与 --reason）",
    )
    cleanup.add_argument("--by", default="", help="执行清理的人/线（--release 必填）")
    cleanup.add_argument(
        "--reason", default="", help="清理理由/场景（--release 必填，写进清理账）"
    )
    cleanup.add_argument(
        "--path", default="", help="只处理这一条路径（逐条复核时用）"
    )

    renew = sub.add_parser(
        "audit-renew",
        help="续租审计：次数 / 累计持有 / 超上限 / 老数据「历史不可审计」（有命中退出码 3）",
    )
    renew.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    renew.add_argument(
        "--legacy-due",
        default=None,
        help=f"老数据清账到期日 yyyy-mm-dd，默认 {DEFAULT_LEGACY_AUDIT_DUE}"
        f"（也可用 env {LEGACY_AUDIT_DUE_ENV}）；改动来源会打印",
    )

    takeover = sub.add_parser(
        "takeover",
        help="接管孤儿 claim（硬条件：心跳已过期 + 无在途改动 + --reason 非空）",
    )
    takeover.add_argument("path")
    takeover.add_argument("--owner", required=True, help="接管方线名")
    takeover.add_argument("--ttl-min", type=float, default=10.0, help="新租期（分钟），默认 10")
    takeover.add_argument(
        "--reason",
        required=True,
        help="接管理由（必填，可复核证据：过期多久 + 工作区干净 + 为什么该你接）",
    )

    repair = sub.add_parser(
        "legacy-token-repair",
        help="一次性修复 legacy claim：有 token_sha256、无 sidecar、签发早于 028f390f（有审计）",
    )
    repair.add_argument("path")
    repair.add_argument("--owner", required=True, help="必须与台账 owner 原文完全一致")
    repair.add_argument("--reason", required=True, help="修复理由（必填，写进审计日志）")
    repair.add_argument(
        "--by",
        default="",
        help="修复人（缺省取 MBDSDR_CLAIM_OWNER / git config mbdsdr.owner）",
    )

    return parser


def _repairer(args) -> str:
    return (args.by or "").strip() or _declared_owner()


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        token = _load_token_arg(
            getattr(args, "token", None), getattr(args, "token_file", None)
        )
        if args.command == "claim":
            return cmd_claim(
                args.path,
                args.owner,
                args.ttl_min,
                token=token,
                adopt_legacy=args.adopt_legacy,
                reason=getattr(args, "reason", "") or "",
                max_hold_min=getattr(args, "max_hold_min", None),
            )
        if args.command == "release":
            return cmd_release(args.path, args.owner, token=token)
        if args.command == "audit-stale":
            return cmd_audit_stale(args.json)
        if args.command == "cleanup-stale":
            return cmd_cleanup_stale(
                as_json=args.json,
                release=args.release,
                by=args.by,
                reason=args.reason,
                only_path=args.path,
            )
        if args.command == "audit-renew":
            return cmd_audit_renew(args.json, legacy_due=args.legacy_due)
        if args.command == "takeover":
            return cmd_takeover(args.path, args.owner, args.ttl_min, args.reason)
        if args.command == "legacy-token-repair":
            return cmd_legacy_token_repair(args.path, args.owner, args.reason, _repairer(args))
        return cmd_status(args.path)
    except ClaimDenied as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_DENIED
    except ValueError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
