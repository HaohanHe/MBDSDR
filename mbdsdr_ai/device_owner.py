# SPDX-License-Identifier: MIT
"""设备占用登记（跨进程）：判活、登记读写、冲突文案与释放。

从 :mod:`mbdsdr_ai.sdr_backend` 拆出。桌面端与 AI/MCP 可能在不同进程里抢
同一根 USB 棒，占用登记必须让**别的进程也读得到**——判活口径是「pid + 进程
启动时间」都对上才算持有者活着。

本模块不 import ``sdr_backend``（否则成环）；``sdr_backend`` 只在顶部再导出
公共名。私有名（``_backup_corrupt_registry`` / ``_open_process_for_query`` /
``_proc_start_time`` 等）的唯一归属在这里，测试/嵌入方要替换应直接指向本模块。
"""
from __future__ import annotations

import atexit
import contextlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


# ──────────────────────── 设备占用登记（跨进程） ────────────────────────
# 桌面端与 AI/MCP 可能在不同进程里抢同一根 USB 棒。占用登记必须让**别的
# 进程也读得到**——模块级字典只在一个进程里有效，那样「桌面端崩溃后 AI 永久
# 被拒」这类失效根本无从谈起，也就无从修起。
#
# 判活口径（2026-11-12 裁定）：只记时间戳无法区分「活着但没读到心跳」和
# 「已经死了」。登记里记 **pid + 进程启动时间**，两者都对上才算持有者活着；
# 启动时间同时防 pid 复用（进程死了、pid 被别的进程拿走，不能当成同一持有者）。
# 判占用**之前**先清失效记录，不做「第一次失败、TTL 过期后才成功」。
OWNER_REGISTRY_ENV = "MBDSDR_OWNER_REGISTRY"
_OWNER_REGISTRY_DEFAULT = os.path.join("~", ".mbdsdr", "device_owner.json")
#: 进程内镜像（只用于观测/调试；判定一律以登记文件为准）。
_ACTIVE_OWNER: Dict[str, Dict[str, Any]] = {}
_OWNER_LOCK = threading.RLock()
_SELF_PID = os.getpid()
_SELF_START: Optional[float] = None
#: 本进程以哪些 (owner, device_id) 身份登记过；atexit 时逐一释放。
_OWNED_HERE: set = set()

_OWNER_LABELS = {"desktop": "桌面端", "mbdsdr-ai": "AI/MCP 进程"}
#: AI/MCP 侧登记占用时使用的 owner 名（桌面端用 "desktop"）。
AI_OWNER = "mbdsdr-ai"

#: 本进程登记占用时用的 owner 名。默认桌面端；AI/MCP 进程构造
#: ``SDRBackendManager`` 时改为 ``AI_OWNER``。放在模块级是因为登记发生在
#: ``SDRBackend.connect()``（开硬件的那一处），那里拿不到「谁在调我」。
_PROCESS_OWNER = "desktop"


def process_owner() -> str:
    """本进程在占用登记里的 owner 名。"""
    return _PROCESS_OWNER


def set_process_owner(owner: str) -> None:
    """声明本进程的 owner 名（AI/MCP 侧在构造后端管理器时调用一次）。"""
    global _PROCESS_OWNER
    _PROCESS_OWNER = str(owner or "desktop")


class AmbiguousOwners:
    """「同一组备用标识命中多条登记」的结果标记。

    **它不是一条占用记录**：没有 owner、没有 pid，也没有 ``.get()``／
    ``[]``。故意不继承 dict——任何把它当 holder 用的代码
    （``holder["owner"]``、``holder.get("pid")``）都会当场 AttributeError，
    而不是安静地显示「owner 空、pid 0」这种看着像真数据的假信息。

    ``bool()`` 为 True：调用方一律按「占用方向」处理（拒绝连接/开流）。
    """

    __slots__ = ("device_id", "candidates")

    def __init__(self, device_id: str, candidates: List[str]):
        self.device_id = str(device_id)
        self.candidates = tuple(str(c) for c in candidates)

    def __bool__(self) -> bool:
        return True

    def __repr__(self) -> str:
        return (f"AmbiguousOwners(device_id={self.device_id!r}, "
                f"candidates={list(self.candidates)!r})")


#: 占用冲突文案的渠道。除「可执行提示」那一句外，三个渠道的正文逐字相同——
#: 同一个概念只能有一个真值源，渠道差异只允许落在「用户接下来敲什么」上。
OWNER_CHANNEL_MCP = "mcp"
OWNER_CHANNEL_CLI = "cli"
OWNER_CHANNEL_DESKTOP = "desktop"

#: busy 分支的「备选设备」提示。CLI 用户照做要能敲出命令，所以写标志本身。
_CHANNEL_ALTERNATIVE_HINT = {
    OWNER_CHANNEL_MCP: "或改用其他设备（先用 sdr_list_devices 查看）。",
    OWNER_CHANNEL_CLI: "或改用其他设备（先用 --list-devices 查看）。",
    OWNER_CHANNEL_DESKTOP: "或改用其他设备（先刷新设备列表）。",
}

#: AmbiguousOwners 分支的「查看完整信息」提示。
_CHANNEL_AMBIGUOUS_HINT = {
    OWNER_CHANNEL_MCP: "也可用 sdr_list_devices 查看。",
    OWNER_CHANNEL_CLI: "也可用 --list-devices 查看。",
    OWNER_CHANNEL_DESKTOP: "也可刷新设备列表查看。",
}


def owner_conflict_hints(channel: str = OWNER_CHANNEL_MCP) -> Tuple[str, str]:
    """``(备选设备提示, 歧义查看提示)`` 在指定渠道下的文字。

    未知渠道按 MCP 处理：宁可用错一个工具名，也不能凭空猜出一条命令让用户
    照着敲。
    """
    if channel not in _CHANNEL_ALTERNATIVE_HINT:
        channel = OWNER_CHANNEL_MCP
    return _CHANNEL_ALTERNATIVE_HINT[channel], _CHANNEL_AMBIGUOUS_HINT[channel]


def owner_conflict_channel() -> str:
    """本进程默认的文案渠道：桌面端 / AI(MCP) 由 :func:`process_owner` 决定。"""
    return (OWNER_CHANNEL_DESKTOP if process_owner() == "desktop"
            else OWNER_CHANNEL_MCP)


def owner_conflict_untrusted(
        holder: Union[Dict[str, Any], AmbiguousOwners]) -> bool:
    """holder 是否只是「登记不可信」的占位：没有真实持有者，只有 reason。

    这类结果既不是「空闲」也不是「被某程序占用」，而更接近「读不出来」：
    补救动作是修/移除登记文件，不是去关别的软件。判定只此一份，
    ``owner_conflict_text`` / ``SDRBackend._claim_hardware`` /
    ``acquire_hardware_owner`` 都调它，避免三处各判一次后走样。
    """
    return (isinstance(holder, dict) and not holder.get("owner")
            and bool(holder.get("reason")))


def owner_conflict_text(holder: Union[Dict[str, Any], AmbiguousOwners],
                        channel: str = OWNER_CHANNEL_MCP) -> str:
    """占用冲突的可执行提示：谁占着、它还活着、下一步能做什么。

    ``channel`` 只替换**用户接下来要敲/要点的那个提示句**，其余正文逐字相同
    （反证见 ``tests/test_cli_owner_conflict_text.py``：两个渠道的输出把那一句
    换回去后必须完全相等）。

    **正文里不放登记键**（2026-11-12 补裁）：键是任意字符串——Pluto 的 USB
    键就是 ``uri:usb:1.2.5``，序列号里也可能带 ``timeout`` / ``busy`` /
    ``soapy`` 之类的字样。桌面端对 ``status.error`` 按关键词二次分类
    （``desktop/error_remedy.py`` 的 ``CONNECT_ERROR_RULES``），键里恰好含
    这些字样时，这条结论会被改写成「权限不足 / 设备已断开」——补救方向正好
    相反。键本身留在登记表与日志里（可查可复制），正文只放不会撞词的部分：
    动作、持有者与 pid。

    措辞同样刻意避开桌面分类表的关键词（``usb`` / ``占用`` / ``断开`` /
    ``权限`` / ``驱动`` 等）：这条消息本身就是结论，被二次分类成
    ``usb_permission`` 会让用户去改 udev / 装 WinUSB——而真正要做的是去
    另一个进程里释放设备，照着改一百遍也不会好。
    """
    alternative_hint, ambiguous_hint = owner_conflict_hints(channel)
    if isinstance(holder, AmbiguousOwners):
        logger.warning(
            "设备占用歧义：键 %s 同时命中 %d 条登记：%s",
            holder.device_id, len(holder.candidates),
            ", ".join(holder.candidates))
        return (f"无法唯一确定设备：同一组标识命中了 {len(holder.candidates)} 条"
                f"登记。这通常是两根同型号设备同时插着——枚举读不到序列号时"
                f"只能靠序号/总线位置区分，此时无法判断是哪一台（不会替你猜）。"
                f"你可以：① 在桌面端或 AI 侧释放其中一台后重试；"
                f"② 只想用其中一根时，先拔掉另一根再点「刷新」；"
                f"③ 或改用带独立地址的设备（如 rtl_tcp / SpyServer）。"
                f"完整键已写入运行日志，{ambiguous_hint}")
    who = owner_label(holder.get("owner", ""))
    if owner_conflict_untrusted(holder):
        # 「登记不可信」不是占用冲突：holder 里没有真实持有者，拼成
        # 「其他程序（pid 0）」就是假信息。另外这段文字会进 status.error，
        # 桌面端按关键词二次分类（error_remedy.CONNECT_ERROR_RULES），
        # 所以措辞避开 usb/占用/权限/驱动/断开/找不到 等词，否则会被
        # 改写成「其它软件占着」或「权限不足」，补救方向正好相反。
        return ("本机的设备登记表不可用，因此无法判断这台设备是否已被别的"
                "程序持有。这是登记文件本身的问题，不是硬件故障。请到"
                "「设备」面板点「查看诊断」，按提示修复或移除登记文件后重试。")
    return (f"设备当前由{who}持有（pid {holder.get('pid')}，进程仍在运行）。"
            f"请先在{who}侧释放该设备后重试，{alternative_hint}")


def owner_registry_path() -> str:
    """登记文件路径（测试用 ``MBDSDR_OWNER_REGISTRY`` 指到临时文件）。"""
    raw = os.environ.get(OWNER_REGISTRY_ENV) or _OWNER_REGISTRY_DEFAULT
    return os.path.abspath(os.path.expanduser(raw))


def owner_label(owner: str) -> str:
    return _OWNER_LABELS.get(str(owner or ""), str(owner or "其他程序"))


#: ``OpenProcess`` 失败码：ERROR_INVALID_PARAMETER(87) = 进程真的不存在。
_ERROR_INVALID_PARAMETER = 87
#: ``PROCESS_QUERY_LIMITED_INFORMATION``（能查退出码/启动时间的最低权限）。
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259

#: 进程探活三态。**「查不到」不等于「不存在」**：没有查询权限时不能判死。
PROC_ALIVE = "alive"
PROC_DEAD = "dead"
PROC_UNKNOWN = "unknown"


def _open_process_failure_means_dead(err: Optional[int]) -> bool:
    """``OpenProcess`` 失败码是否说明「进程真的不存在」。

    只有 ``ERROR_INVALID_PARAMETER``(87) 是这个意思。``ERROR_ACCESS_DENIED``(5)
    是「没权限查」——低完整性进程查高完整性进程就是这一种。把它判死会清掉
    **活着**的持有者记录，两个进程随后同时打开同一根 USB 棒、分同一条流
    （WinUSB/usbfs 都不拦），这是最不该 fail-open 的地方。其余失败码一律
    按「查不到」处理。
    """
    return err == _ERROR_INVALID_PARAMETER


def _kernel32():
    """带完整 argtypes/restype 的 kernel32（HANDLE 是 64 位指针，不设会被截断）。"""
    import ctypes
    import ctypes.wintypes as wt
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wt.HANDLE
    k32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
    k32.GetExitCodeProcess.argtypes = (wt.HANDLE, ctypes.POINTER(wt.DWORD))
    k32.GetProcessTimes.argtypes = (
        wt.HANDLE, ctypes.POINTER(wt.FILETIME), ctypes.POINTER(wt.FILETIME),
        ctypes.POINTER(wt.FILETIME), ctypes.POINTER(wt.FILETIME))
    k32.CloseHandle.argtypes = (wt.HANDLE,)
    return k32


def _open_process_for_query(pid: int):
    """Windows：打开进程查询句柄，返回 ``(handle, GetLastError())``。

    失败时 handle 为 0；错误码交给 :func:`_open_process_failure_means_dead`
    分流出「真死」和「查不到」。单独拆出来是为了让测试能注入 ACCESS_DENIED，
    而不必真去搞一个高完整性进程。
    """
    import ctypes
    k32 = _kernel32()
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if handle:
        return handle, 0
    return 0, int(ctypes.get_last_error() or 0)


def _proc_probe(pid: int) -> Tuple[str, Optional[float]]:
    """返回 ``(状态, 启动时间)``；状态见 ``PROC_*``。

    这是判活的**唯一入口**。判死必须拿到正面证据（进程不存在 / 退出码不是
    STILL_ACTIVE）；没权限、读不到、任何意外一律 ``PROC_UNKNOWN``——把
    「查不到」当「死了」会清掉活着的持有者记录。
    """
    if not pid or pid <= 0:
        return PROC_DEAD, None
    try:
        if os.name == "nt":
            import ctypes
            import ctypes.wintypes as wt
            handle, err = _open_process_for_query(int(pid))
            if not handle:
                return ((PROC_DEAD if _open_process_failure_means_dead(err)
                         else PROC_UNKNOWN), None)
            try:
                k32 = _kernel32()
                # 已退出但句柄仍被 Popen 持有的进程，OpenProcess 依然成功、
                # GetProcessTimes 也返回原值——必须用退出码区分「还活着」。
                code = wt.DWORD()
                if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return PROC_UNKNOWN, None
                if code.value != _STILL_ACTIVE:
                    return PROC_DEAD, None
                creation, exit_t = wt.FILETIME(), wt.FILETIME()
                kernel, user = wt.FILETIME(), wt.FILETIME()
                ok = k32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_t),
                    ctypes.byref(kernel), ctypes.byref(user))
                if not ok:
                    return PROC_UNKNOWN, None
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return PROC_ALIVE, ticks / 1e7          # 100ns → 秒
            finally:
                k32.CloseHandle(handle)
        try:
            data = open(f"/proc/{int(pid)}/stat",
                        encoding="utf-8", errors="replace").read()
        except FileNotFoundError:
            return PROC_DEAD, None          # /proc 里没有 = 进程不存在
        except PermissionError:
            return PROC_UNKNOWN, None       # 没权限读 = 查不到，不等于不存在
        fields = data[data.rindex(")") + 2:].split()
        return PROC_ALIVE, float(fields[19]) / float(os.sysconf("SC_CLK_TCK"))
    except Exception:
        # 任何意外都按「未知」：宁可拒连，也不能清掉活着的持有者。
        return PROC_UNKNOWN, None


def _proc_start_time(pid: int) -> Optional[float]:
    """进程启动时间（秒，平台无关的相对量）；不存在/查不到返回 None。

    注意：``None`` 只代表「拿不到」，**不能**据此判死——判活请用
    :func:`_pid_alive`（它会区分「真死」和「查不到」）。
    """
    return _proc_probe(int(pid or 0))[1]


def _self_start() -> Optional[float]:
    global _SELF_START
    if _SELF_START is None:
        _SELF_START = _proc_start_time(_SELF_PID)
    return _SELF_START


def _pid_alive(pid: int, recorded_start: Optional[float]) -> bool:
    """持有者是否**仍可能活着**：进程在 + 启动时间对得上（防 pid 复用）。

    「查不到」一律按活着处理：误判「死」会清掉活着的持有者记录、两个进程
    同抢一根棒；误判「活」最坏只是让人去看一眼占用登记。
    """
    if not pid or pid <= 0:
        return False
    state, start = _proc_probe(int(pid))
    if state == PROC_UNKNOWN:
        return True
    if state != PROC_ALIVE or start is None:
        return False
    if recorded_start is None:
        # 旧格式记录没有启动时间：只能退化为「进程还在」，如实按活着处理。
        return True
    try:
        return abs(start - float(recorded_start)) <= 0.5
    except (TypeError, ValueError):
        return True


@contextlib.contextmanager
def _registry_lock():
    """跨进程互斥（尽力而为：拿不到锁也只告警，不阻塞硬件流程）。"""
    path = owner_registry_path() + ".lock"
    fh = None
    locked = False
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = open(path, "a+b")
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        logger.warning("设备占用登记锁等待超时，继续（不阻塞硬件流程）")
                        break
                    time.sleep(0.02)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            locked = True
    except Exception as e:  # noqa: BLE001
        logger.warning("设备占用登记加锁失败，继续（不阻塞硬件流程）: %s", e)
    try:
        yield
    finally:
        if fh is not None:
            if locked:
                try:
                    if os.name == "nt":
                        import msvcrt
                        fh.seek(0)
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            try:
                fh.close()
            except Exception:
                pass


def _backup_corrupt_registry(reason: str) -> Optional[str]:
    """登记文件损坏：改名成 ``<path>.corrupt-<时间戳>`` 并写理由文件。

    复用 ``corrupt_evidence`` 的统一模板（与设置/书签/布局同一套命名与
    ``.reason.txt`` 配对），这样它会自动出现在启动日志的损坏备份清点里，
    不另开一套。调用方必须已持有登记文件锁；改名失败只告警，仍按空登记继续。
    """
    path = owner_registry_path()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.corrupt-{stamp}"
    try:
        os.replace(path, backup)
    except OSError as e:
        logger.warning("设备占用登记损坏，备份失败（原文件保留；读侧按状态未知"
                       "处理，写回被拒绝）: %s", e)
        return None
    try:
        from .corrupt_evidence import write_reason
        # 理由里带上登记文件自己的路径：其余四类（配置/书签/布局/AI 记忆）
        # 的理由都含路径，单独看理由文本时也能定位到是哪一份文件坏了。
        write_reason(backup, f"{path}：{reason}", logger)
    except Exception:  # noqa: BLE001 - 理由写失败不影响已落好的备份
        pass
    logger.warning("设备占用登记损坏（%s），原文件已备份为 %s；本次读到的内容"
                   "不可信，按状态未知处理", reason, backup)
    return backup


class RegistryUntrustedError(RuntimeError):
    """设备占用登记损坏且无法留证：写回会伪造「设备空闲」，必须拒绝。

    与配置/书签那套「损坏先备份再降级」的区别在于：登记表被覆盖的后果不是
    丢一条本地记录，而是让**另一个进程**以为设备没人用，从而两个进程同时
    打开同一根硬件。所以这里宁可让本次 claim 失败，也不写一个看起来正常的空表。
    """


# ──────────────────────── 设备占用三态（读侧） ────────────────────────
# 「读不到」和「没人占」是两件事。旧实现把两者都压成 ``None``，界面据此
# 显示「设备空闲」，用户点连接却被拒（登记不可读时 claim 必须失败）——
# 界面在说一件与实际不符的话。这里把读侧结论显式分成四个值，未知绝不落进
# 空闲分支。
OWNER_STATE_FREE = "free"
OWNER_STATE_OWNED = "owned"
OWNER_STATE_UNKNOWN = "unknown"
OWNER_STATE_AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class OwnerState:
    """设备占用三态结论。

    * ``free``      —— 登记可读且没有匹配的持有者（**只有这一种能说"空着"**）；
    * ``owned``     —— 有持有者，``record`` 是那条登记；
    * ``ambiguous`` —— 同一组别名命中多条，无法唯一确定；
    * ``unknown``   —— 登记存在但读不出可信内容，**无法判断**是否被占用。

    ``bool()`` 只有 ``free`` 为 False：未知必须按「不能确认空闲」处理。
    故意没有 ``.get()``/``[]``——误当 holder 用会当场 ``AttributeError``，
    而不是显示出「owner 空、pid 0」这种看着像真数据的假信息。
    """

    state: str
    device_id: str = ""
    record: Optional[Dict[str, Any]] = None
    reason: str = ""
    candidates: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.state != OWNER_STATE_FREE


def owner_state_text(state: OwnerState) -> str:
    """占用三态的用户可见文案（界面与工具共用同一份措辞）。"""
    if state.state == OWNER_STATE_FREE:
        return "设备空闲（占用登记可读，无持有者）"
    if state.state == OWNER_STATE_OWNED:
        rec = state.record or {}
        return (f"设备正被{owner_label(rec.get('owner', ''))}占用"
                f"（pid {rec.get('pid')}，进程仍在运行）")
    if state.state == OWNER_STATE_AMBIGUOUS:
        return (f"无法唯一确定设备占用：同一组标识命中了 "
                f"{len(state.candidates)} 条登记")
    return ("无法读取设备占用登记，无法判断是否被占用"
            + (f"（{state.reason}）" if state.reason else ""))


def _read_registry_state() -> Tuple[Dict[str, Dict[str, Any]], str, bool]:
    """读登记表并给出「本次内容是否可信、还能不能写回」。

    返回 ``(owners, unreadable_reason, writable)``：

    * ``unreadable_reason`` 非空 = **本次读到的内容不可信**（文件存在但
      JSON 坏 / 顶层结构非法）。调用方必须按「状态未知」处理，不能当成
      一张空表——那会把「不知道谁占着」显示成「没人占」。
    * ``writable`` = 是否还能安全写回。损坏但已成功备份移走时为 True
      （坏内容已留证、读取路径已腾空）；损坏且备份失败时为 False
      （写回会覆盖唯一原件，并让别的进程误判设备空闲）。
    """
    path = owner_registry_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        # 首次运行 / 用户手动删掉：都按「当前无占用」。删除与首次运行无法
        # 区分，所以不告警；但删掉一个**活持有者**的记录等于放弃互斥。
        return {}, "", True
    except Exception as e:  # noqa: BLE001 - 先留证据，再把结论交给调用方
        reason = f"JSON 读取/解析失败：{e.__class__.__name__}: {e}"
        backup = _backup_corrupt_registry(reason)
        if backup is None:
            return {}, f"{reason}；备份失败，原文件仍在，拒绝写回", False
        return {}, f"{reason}；原件已备份到 {backup}", True
    owners = data.get("owners") if isinstance(data, dict) else None
    if not isinstance(owners, dict):
        reason = "顶层结构非法（缺少 owners 对象）"
        backup = _backup_corrupt_registry(reason)
        if backup is None:
            return {}, f"{reason}；备份失败，原文件仍在，拒绝写回", False
        return {}, f"{reason}；原件已备份到 {backup}", True
    return ({str(k): v for k, v in owners.items() if isinstance(v, dict)},
            "", True)


def _read_registry(strict: bool = False) -> Dict[str, Dict[str, Any]]:
    """读登记表（会写回的调用方用 ``strict=True``）。

    ``strict=True`` 在「损坏且备份失败」时抛
    :class:`RegistryUntrustedError`，禁止把不可信内容当成空表写回。
    非 strict 仍降级为空表以兼容旧调用；界面该用 :func:`get_owner_state`，
    它会把「不可读」如实报成 ``unknown``，而不是 ``None``（= 空着）。
    """
    owners, reason, writable = _read_registry_state()
    if not writable:
        if strict:
            raise RegistryUntrustedError(
                f"设备占用登记损坏且无法备份：{owner_registry_path()}；"
                f"{reason}。拒绝写入，以免其他进程误判设备空闲。"
                "请手动修复或移除该文件后重试")
        return {}
    return owners


def _write_registry(owners: Dict[str, Dict[str, Any]]) -> None:
    path = owner_registry_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{_SELF_PID}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "owners": owners}, f,
                      ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001 - 写不了登记不该阻断硬件流程
        logger.warning("设备占用登记写入失败（本次不阻塞连接）: %s", e)
    _ACTIVE_OWNER.clear()
    _ACTIVE_OWNER.update(owners)


def release_stale_owners() -> List[Dict[str, Any]]:
    """清掉持有者进程已不存在的登记，返回被清掉的记录。

    判活靠 pid + 启动时间，不靠 TTL：持有者被杀后，下一位申请者**立即**
    能拿到设备，而不是先失败一次、等「过期」。
    """
    removed: List[Dict[str, Any]] = []
    with _OWNER_LOCK, _registry_lock():
        try:
            owners = _read_registry(strict=True)
        except RegistryUntrustedError as e:
            # 登记不可信 → 一条都不清（清 = 写回）。claim 随后会明确失败。
            logger.error("%s", e)
            return []
        removed = _drop_stale(owners)
        if removed:
            _write_registry(owners)
            _log_stale_removed(removed)
    return removed


def _drop_stale(owners: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """从 ``owners`` 里剔除持有者进程已不存在的记录（原地修改）。"""
    removed: List[Dict[str, Any]] = []
    for dev, rec in list(owners.items()):
        try:
            pid = int(rec.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if _pid_alive(pid, rec.get("proc_start")):
            continue
        owners.pop(dev, None)
        removed.append(dict(rec))
    return removed


def _log_stale_removed(removed: List[Dict[str, Any]]) -> None:
    for rec in removed:
        logger.warning(
            "清理失效设备占用：%s 原由 %s（pid %s，启动于 %s）持有，"
            "该进程已不存在", rec.get("device_id"),
            owner_label(rec.get("owner", "")), rec.get("pid"),
            rec.get("proc_start"))


def _match_owner_record(owners: Dict[str, Dict[str, Any]], device_id: str,
                        allow_alias_match: bool = True):
    """在登记里找出与 ``device_id`` 对应的记录。

    返回 ``(kind, key, record, candidates)``，kind ∈
    ``exact`` / ``alias`` / ``ambiguous`` / ``none``。

    两条硬规矩：

    * **别名只在序列号读不到时才参与匹配**（``allow_alias_match`` 由调用方
      按身份质量决定）。serial 可得时它就是身份本身——换 USB 口不该变成
      另一台设备；
    * **别名命中必须唯一**。两条记录同时匹配时返回 ``ambiguous``，由调用方
      拒绝——**猜错比拒绝坏得多**：拒绝只要用户换一台设备，猜错会让两个
      进程分同一条流。
    """
    if device_id in owners:
        return "exact", device_id, owners[device_id], []
    if not allow_alias_match:
        return "none", "", None, []
    candidates = [(key, rec) for key, rec in owners.items()
                  if device_id in (rec.get("aliases") or [])]
    if len(candidates) > 1:
        return "ambiguous", "", None, [key for key, _ in candidates]
    if candidates:
        return "alias", candidates[0][0], candidates[0][1], []
    return "none", "", None, []


def get_owner_state(device_id: str = "",
                    allow_alias_match: bool = True) -> OwnerState:
    """当前设备占用的**三态**结论（只读，先清失效记录再判）。

    这是界面唯一该用的读侧入口。要点：

    * ``device_id`` 为空时，登记里只有一条就报 ``owned``；零条 ``free``；
      多条 ``ambiguous``（不知道问的是哪台，绝不挑一条）；
    * **登记存在但读不出可信内容时返回 ``unknown``**，不是 ``free``。
      用户不知道能不能连，比直接说不能连更糟——界面必须能说出「无法判断」，
      并给出重试/诊断的入口；
    * 本次读取若触发「损坏后备份移走」，仍是 ``unknown``：原件里的持有者
      已经读不出来了，不能替它宣布空闲。下一次读取会自然变成 ``free``。
    """
    with _OWNER_LOCK, _registry_lock():
        owners, reason, _writable = _read_registry_state()
        if reason:
            # 不可信：不清失效记录（那是写回），也不落进空闲分支。
            return OwnerState(OWNER_STATE_UNKNOWN, str(device_id), reason=reason)
        removed = _drop_stale(owners)
        if removed:
            _write_registry(owners)
            _log_stale_removed(removed)
        _ACTIVE_OWNER.clear()
        _ACTIVE_OWNER.update(owners)
        if device_id:
            kind, _key, rec, candidates = _match_owner_record(
                owners, device_id, allow_alias_match)
            if kind == "ambiguous":
                return OwnerState(OWNER_STATE_AMBIGUOUS, str(device_id),
                                  candidates=tuple(candidates))
            if rec is not None:
                return OwnerState(OWNER_STATE_OWNED, str(device_id),
                                  record=dict(rec))
            return OwnerState(OWNER_STATE_FREE, str(device_id))
        if len(owners) == 1:
            rec = next(iter(owners.values()))
            return OwnerState(OWNER_STATE_OWNED,
                              str(rec.get("device_id") or ""), record=dict(rec))
        if owners:
            return OwnerState(OWNER_STATE_AMBIGUOUS, "",
                              candidates=tuple(str(k) for k in owners))
        return OwnerState(OWNER_STATE_FREE, "")


def get_active_owner(device_id: str = "",
                     allow_alias_match: bool = True
                     ) -> Optional[Union[Dict[str, Any], AmbiguousOwners,
                                        OwnerState]]:
    """当前占用者（兼容入口；新代码请用 :func:`get_owner_state`）。

    **只有确定空闲才返回 ``None``**。登记不可读时返回
    ``OwnerState(state="unknown")``——调用方按「无法判断」展示，绝不能
    落进「设备空闲」分支。命中多条登记（别名有歧义）时返回
    :class:`AmbiguousOwners`，它连 ``.get()`` 都没有，误当 holder 用会立刻
    ``AttributeError``。
    """
    state = get_owner_state(device_id, allow_alias_match)
    if state.state == OWNER_STATE_FREE:
        return None
    if state.state == OWNER_STATE_UNKNOWN:
        return state
    if state.state == OWNER_STATE_AMBIGUOUS:
        return AmbiguousOwners(state.device_id or device_id,
                               list(state.candidates))
    return dict(state.record or {})


def claim_active_owner(owner: str, device_id: str,
                       aliases: Optional[List[str]] = None,
                       allow_alias_match: bool = True
                       ) -> Tuple[bool, Union[Dict[str, Any], AmbiguousOwners]]:
    """为 ``owner`` 声明占用 ``device_id``。

    返回 ``(ok, record)``：成功时 record 是本次写入的记录；失败时 record
    是**仍在运行**的现有持有者（调用方据此给出可执行提示）。

    ``aliases`` 是同一台设备的其它可选身份键（见
    :func:`device_identity_aliases`）：设备被占用时枚举读不到序列号，
    竞争者只能算出枚举序号键，只按主键比对会漏掉活着的持有者。
    别名命中**必须唯一**，撞上多条记录时第二项返回
    :class:`AmbiguousOwners`（不是 holder，误用会 AttributeError），
    由调用方拒绝并说明——绝不取第一条。

    同进程内的重复声明允许覆盖（桌面端与 AI 适配器同在一个进程时不能互相
    死锁）；跨进程只有持有者进程真的不在了才会被清掉。
    """
    if not device_id:
        return False, {"owner": "", "device_id": "", "pid": 0,
                       "reason": "device_id 为空"}
    release_stale_owners()
    with _OWNER_LOCK, _registry_lock():
        try:
            owners = _read_registry(strict=True)
        except RegistryUntrustedError as e:
            # 本次 claim 直接失败：登记写着什么已不可信，写回「只有我」的表
            # 会让别的进程以为设备空着。提示里给出人工修复路径，不静默。
            logger.error("%s", e)
            return False, {"owner": "", "device_id": str(device_id), "pid": 0,
                           "reason": str(e)}
        kind, matched_key, cur, candidates = _match_owner_record(
            owners, device_id, allow_alias_match)
        if kind == "ambiguous":
            return False, AmbiguousOwners(device_id, candidates)
        if cur is not None:
            # 放行条件只有「同 pid」（本进程自己 claim 自己）。**不能**按
            # owner 名相等放行：两个桌面实例 owner 都是 "desktop"，按名放行
            # 会让它们同时打开同一根 USB 棒（2026-11-12 实测）。
            try:
                cur_pid = int(cur.get("pid") or 0)
            except (TypeError, ValueError):
                cur_pid = 0
            if cur_pid != _SELF_PID and _pid_alive(cur_pid, cur.get("proc_start")):
                return False, dict(cur)
        if cur is not None and matched_key != device_id:
            # 同一台设备的旧别名记录：改用本次主键，避免同机两条记录。
            owners.pop(matched_key, None)
        alias_list = []
        for key in [device_id] + list(aliases or []):
            if key and key not in alias_list:
                alias_list.append(str(key))
        rec = {
            "owner": str(owner),
            "pid": _SELF_PID,
            "proc_start": _self_start(),
            "device_id": str(device_id),
            "aliases": alias_list[1:],
            "claimed_at": time.time(),
        }
        owners[device_id] = rec
        _write_registry(owners)
    _OWNED_HERE.add((str(owner), str(device_id)))
    return True, dict(rec)


def release_active_owner(owner: str, device_id: str = "") -> bool:
    """释放 ``owner`` 的占用（只释放自己的；device_id 为空则释放本进程全部）。"""
    released: List[str] = []
    with _OWNER_LOCK, _registry_lock():
        try:
            owners = _read_registry(strict=True)
        except RegistryUntrustedError as e:
            # 释放也要写回：登记不可信时宁可不释放（本进程退出时 atexit
            # 仍会再试一次），也不写一张会误导其他进程的表。
            logger.error("%s", e)
            return False
        targets = ([device_id] if device_id
                   else [d for d, r in owners.items()
                         if str(r.get("owner")) == str(owner)])
        if device_id and device_id not in owners:
            # 别名释放：持有者按序列号登记、调用方只拿到枚举序号键。
            # 撞上多条时一条都不放（同 _match_owner_record 的唯一性要求）。
            kind, matched_key, _rec, _cands = _match_owner_record(
                owners, device_id)
            targets = [matched_key] if kind == "alias" else []
        for dev in targets:
            rec = owners.get(dev)
            if rec is None or str(rec.get("owner")) != str(owner):
                continue
            try:
                if int(rec.get("pid") or 0) != _SELF_PID:
                    continue        # 只释放本进程写的记录
            except (TypeError, ValueError):
                continue
            owners.pop(dev, None)
            released.append(dev)
        if released:
            _write_registry(owners)
    for dev in released:
        _OWNED_HERE.discard((str(owner), dev))
    return bool(released)


def acquire_hardware_owner(key: str, aliases: Optional[List[str]] = None,
                           allow_alias_match: bool = True,
                           channel: str = OWNER_CHANNEL_MCP
                           ) -> Tuple[bool, str, bool]:
    """非 ``SDRBackend`` 的打开路径（bladeRF / LimeSDR / CLI ``DeviceManager``）
    共用的占用登记入口。

    这些路径是 ctypes/驱动直连或另一套句柄模型，**不走**
    ``SDRBackend.connect()`` 模板方法；判据只写在模板方法里，它们就会静默地
    少一层保护——不报错，也没人发现（没插设备的人永远走不到这条路径）。

    返回 ``(ok, conflict_text, untrusted)``。成功时调用方必须记住 ``key``，
    并在关闭设备时调 :func:`release_hardware_owner` 归还。``untrusted`` 只在
    失败时有意义：True 表示「登记不可读」，不是「被某个程序占用」——两者的
    用户结论与补救动作必须不同。``channel`` 只决定提示句里的工具名/界面
    动作，调用方按自己那条路径传（CLI 传 ``OWNER_CHANNEL_CLI``）。
    """
    ok, holder = claim_active_owner(process_owner(), key, aliases=aliases,
                                    allow_alias_match=allow_alias_match)
    if ok:
        return True, "", False
    return (False, owner_conflict_text(holder, channel=channel),
            owner_conflict_untrusted(holder))


def release_hardware_owner(key: str) -> None:
    """归还 :func:`acquire_hardware_owner` 登记的占用（空键直接返回）。"""
    if not key:
        return
    try:
        release_active_owner(process_owner(), key)
    except Exception as e:  # noqa: BLE001 - 释放失败不阻塞断开
        logger.debug("释放设备占用登记失败(忽略): %s", e)


def _release_owned_here_at_exit() -> None:
    """atexit 兜底：进程正常退出/被礼貌终止时，把本进程的占用还回去。"""
    for owner, dev in list(_OWNED_HERE):
        try:
            release_active_owner(owner, dev)
        except Exception:  # noqa: BLE001 - 退出路径绝不抛
            pass


atexit.register(_release_owned_here_at_exit)
