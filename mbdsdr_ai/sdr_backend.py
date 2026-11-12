# SPDX-License-Identifier: MIT
"""
MBDSDR AI 内核 - SDR 后端抽象层
================================
SDR Backend：统一的 SDR 硬件抽象层。

支持的后端：
- RTL-SDR（RTL2832U + E4000/FC0012/FC0013/R820T/R820T2）
- HackRF One
- USRP（UHD）
- PlutoSDR（ADALM-PLUTO / AD9361，经 libiio 或 SoapyPlutoSDR）
- 自研 ai-sdr Mini（SI4732 + ESP32，通过 MCP/WebSocket）

架构原则：每个数据域运行时只有一个真实后端；无硬件时 active_backend 为 None
（显式未连接），绝不实例化模拟后端并报 success=True。FileIQBackend 读取用户
明确选择的真实录制文件（.wav/.cf32/.iq），属合法离线回放源，保留。

PlutoSDR 接入说明：
- 首选 pylibiio（``pip install pylibiio``），USB/网络均走 ``iio.Context(uri)``；
  系统包等价物为 ``apt install libiio-dev python3-libiio``。
- 备选 SoapySDR + SoapyPlutoSDR 驱动（``SoapySDR.Device(dict(driver="plutosdr"))``）。
- 两者都不可用时，PlutoSDRBackend 类仍可导入，但 enumerate() 返回 []、connect()
  返回 False 并在 status.error 标注 "[未连接-无libiio/SoapySDR驱动]"，绝不假装收数据。
- 出厂频率 325 MHz–3.8 GHz；解锁 AD9361 校准后可到 70 MHz–6 GHz。
  本后端默认按出厂 325–3800 MHz 声明，覆盖 2.2 GHz LRO / 1.69 GHz GK-2A 接收。

统一接口：
- connect() / disconnect()
- set_frequency() / get_frequency()
- set_sample_rate() / get_sample_rate()
- set_gain() / get_gain() / set_agc()
- set_bandwidth()
- read_samples()  # 读取 IQ 样本
- get_status()

所有后端实现相同的接口，上层工具不需要关心具体硬件。
"""

import time
import logging
import math

import os
import json
import atexit
import contextlib
import collections
import threading
import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

from .error_catalog import user_message

# 进程内"环境缺失"类告警只报一次用的键集合。
# 仅用于**进程生命周期内不会变化**的事实（库没装 / CLI 不存在）；
# 每次调用都可能不同的真实失败不在此列，仍按次如实上报。
_WARNED_ONCE: set = set()


def _warn_once(key: str, msg: str, *args) -> None:
    """同一进程内只报一次的同质告警。

    实测（2026-10-28）：一次启动里设备枚举被触发 4 次，SoapySDR 缺失的
    三条提示被整刷 4 遍共 12 行，把真正的硬件信息淹掉。这些是静态环境事实，
    重复播报纯属噪音。**去重只去重复，不去严重性**：首次仍完整报出，
    且附带可执行的安装指引，不把"没装库"粉饰成"没插设备"。
    """
    if key in _WARNED_ONCE:
        return
    _WARNED_ONCE.add(key)
    logger.warning(msg, *args)


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


def _owner_conflict_text(holder: Dict[str, Any]) -> str:
    """占用冲突的可执行提示：谁占着、它还活着、下一步能做什么。"""
    dev = holder.get("device_id") or ""
    who = owner_label(holder.get("owner", ""))
    return (f"设备 {dev} 当前由{who}占用（pid {holder.get('pid')}，进程仍在运行）。"
            f"请先在{who}侧断开该设备，或改用其他设备"
            f"（先用 sdr_list_devices 查看）。")


def owner_registry_path() -> str:
    """登记文件路径（测试用 ``MBDSDR_OWNER_REGISTRY`` 指到临时文件）。"""
    raw = os.environ.get(OWNER_REGISTRY_ENV) or _OWNER_REGISTRY_DEFAULT
    return os.path.abspath(os.path.expanduser(raw))


def owner_label(owner: str) -> str:
    return _OWNER_LABELS.get(str(owner or ""), str(owner or "其他程序"))


def _proc_start_time(pid: int) -> Optional[float]:
    """进程启动时间（秒，平台无关的相对量）；进程不存在/查不到返回 None。"""
    if not pid or pid <= 0:
        return None
    try:
        if os.name == "nt":
            import ctypes
            import ctypes.wintypes as wt
            # 用私有 DLL 对象设 argtypes：HANDLE 是 64 位指针，按默认 int 传会
            # 被截断（真机上 OpenProcess 拿到的句柄无效，判活会永远失败）。
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.OpenProcess.restype = wt.HANDLE
            k32.OpenProcess.argtypes = (wt.DWORD, wt.BOOL, wt.DWORD)
            k32.GetExitCodeProcess.argtypes = (wt.HANDLE, ctypes.POINTER(wt.DWORD))
            k32.GetProcessTimes.argtypes = (
                wt.HANDLE, ctypes.POINTER(wt.FILETIME), ctypes.POINTER(wt.FILETIME),
                ctypes.POINTER(wt.FILETIME), ctypes.POINTER(wt.FILETIME))
            k32.CloseHandle.argtypes = (wt.HANDLE,)
            STILL_ACTIVE = 259
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return None
            try:
                # 已退出但句柄仍被 Popen 持有的进程，OpenProcess 依然成功、
                # GetProcessTimes 也返回原值——必须用退出码区分「还活着」。
                code = wt.DWORD()
                if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                    return None
                if code.value != STILL_ACTIVE:
                    return None
                creation, exit_t = wt.FILETIME(), wt.FILETIME()
                kernel, user = wt.FILETIME(), wt.FILETIME()
                ok = k32.GetProcessTimes(
                    handle, ctypes.byref(creation), ctypes.byref(exit_t),
                    ctypes.byref(kernel), ctypes.byref(user))
                if not ok:
                    return None
                ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                return ticks / 1e7          # 100ns → 秒
            finally:
                k32.CloseHandle(handle)
        data = open(f"/proc/{int(pid)}/stat", encoding="utf-8", errors="replace").read()
        fields = data[data.rindex(")") + 2:].split()
        return float(fields[19]) / float(os.sysconf("SC_CLK_TCK"))
    except Exception:
        return None


def _self_start() -> Optional[float]:
    global _SELF_START
    if _SELF_START is None:
        _SELF_START = _proc_start_time(_SELF_PID)
    return _SELF_START


def _pid_alive(pid: int, recorded_start: Optional[float]) -> bool:
    """持有者是否仍活着：进程在 + 启动时间对得上（防 pid 复用）。"""
    if not pid or pid <= 0:
        return False
    start = _proc_start_time(pid)
    if start is None:
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


def _read_registry() -> Dict[str, Dict[str, Any]]:
    try:
        with open(owner_registry_path(), encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:  # noqa: BLE001 - 登记损坏按空处理，不阻塞连接
        logger.warning("设备占用登记读取失败（按空处理）: %s", e)
        return {}
    owners = data.get("owners") if isinstance(data, dict) else None
    if not isinstance(owners, dict):
        return {}
    return {str(k): v for k, v in owners.items() if isinstance(v, dict)}


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
        owners = _read_registry()
        for dev, rec in list(owners.items()):
            try:
                pid = int(rec.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            if _pid_alive(pid, rec.get("proc_start")):
                continue
            owners.pop(dev, None)
            removed.append(dict(rec))
        if removed:
            _write_registry(owners)
            for rec in removed:
                logger.warning(
                    "清理失效设备占用：%s 原由 %s（pid %s，启动于 %s）持有，"
                    "该进程已不存在", rec.get("device_id"),
                    owner_label(rec.get("owner", "")), rec.get("pid"),
                    rec.get("proc_start"))
    return removed


def get_active_owner(device_id: str = "") -> Optional[Dict[str, Any]]:
    """当前占用者（先清失效记录再判）。device_id 为空时返回唯一的记录。"""
    release_stale_owners()
    with _OWNER_LOCK:
        owners = _read_registry()
        _ACTIVE_OWNER.clear()
        _ACTIVE_OWNER.update(owners)
        if device_id:
            return dict(owners[device_id]) if device_id in owners else None
        if len(owners) == 1:
            return dict(next(iter(owners.values())))
        return None


def claim_active_owner(owner: str, device_id: str) -> Tuple[bool, Dict[str, Any]]:
    """为 ``owner`` 声明占用 ``device_id``。

    返回 ``(ok, record)``：成功时 record 是本次写入的记录；失败时 record
    是**仍在运行**的现有持有者（调用方据此给出可执行提示）。
    同进程内的重复声明允许覆盖（桌面端与 AI 适配器同在一个进程时不能互相
    死锁）；跨进程只有持有者进程真的不在了才会被清掉。
    """
    if not device_id:
        return False, {"owner": "", "device_id": "", "pid": 0,
                       "reason": "device_id 为空"}
    release_stale_owners()
    with _OWNER_LOCK, _registry_lock():
        owners = _read_registry()
        cur = owners.get(device_id)
        if cur is not None and str(cur.get("owner")) != str(owner):
            try:
                cur_pid = int(cur.get("pid") or 0)
            except (TypeError, ValueError):
                cur_pid = 0
            if cur_pid != _SELF_PID and _pid_alive(cur_pid, cur.get("proc_start")):
                return False, dict(cur)
        rec = {
            "owner": str(owner),
            "pid": _SELF_PID,
            "proc_start": _self_start(),
            "device_id": str(device_id),
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
        owners = _read_registry()
        targets = ([device_id] if device_id
                   else [d for d, r in owners.items()
                         if str(r.get("owner")) == str(owner)])
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


def _release_owned_here_at_exit() -> None:
    """atexit 兜底：进程正常退出/被礼貌终止时，把本进程的占用还回去。"""
    for owner, dev in list(_OWNED_HERE):
        try:
            release_active_owner(owner, dev)
        except Exception:  # noqa: BLE001 - 退出路径绝不抛
            pass


atexit.register(_release_owned_here_at_exit)


# rtl_tcp 网络源的环境变量登记口（comma-separated host[:port]，默认端口 1234）。
# 让 MCP server / GUI / CLI 在零代码变化下把同一根（远端或本机）rtl_tcp 棒
# 纳入枚举：SDRBackendManager._discover() 读取本变量登记 RTLSDRBackend(host=...)。
# 注意 rtl_tcp 是单客户端协议（listen backlog=1），登记不等于连接。
RTL_TCP_ENV = "MBDSDR_RTL_TCP"
# SpyServer 网络源登记口（同样的 host[:port] 语法，默认端口 7355）。
SPYSERVER_ENV = "MBDSDR_SPYSERVER"


def parse_rtl_tcp_env(value: str, default_port: int = 1234
                      ) -> List[Tuple[str, int]]:
    """解析 ``host[:port][,host[:port]...]`` 为 [(host, port), ...]。

    无法解析的条目记 debug 日志后跳过（诚实忽略，不为坏配置造后端）；
    空值返回 []（显式未登记）。裸主机名/IP 用 ``default_port``。
    """
    out: List[Tuple[str, int]] = []
    for raw in (value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        if ":" in item:
            host, _, port_s = item.rpartition(":")
            if not host or not port_s.isdigit() or not (0 < int(port_s) < 65536):
                logger.debug(f"rtl_tcp 配置无法解析（已跳过）: {item!r}")
                continue
            out.append((host, int(port_s)))
        else:
            out.append((item, int(default_port)))
    return out


# 通用网络 IQ 源登记口（sdrpp network_source 同机制）：裸流无元数据，
# rate/freq 必须由配置显式给定——猜默认值等于伪造口径。逗号分隔多条目。
NETWORK_IQ_ENV = "MBDSDR_NETWORK_IQ"


def parse_network_iq_env(value: str) -> List[Dict[str, Any]]:
    """解析 ``proto://host:port?rate=<Hz>&freq=<Hz>[&fmt=..][&byteorder=..]``。

    与 parse_rtl_tcp_env 同一份诚实语义：坏条目记 debug 后跳过，空值返回 []。
    rate/freq 必填且须为有限正数/非负数；fmt/byteorder 缺省 cu8/little。
    """
    from urllib.parse import urlsplit, parse_qs
    out: List[Dict[str, Any]] = []
    for raw in (value or "").split(","):
        item = raw.strip()
        if not item:
            continue
        try:
            u = urlsplit(item)
            if u.scheme not in ("tcp", "udp"):
                raise ValueError(f"未知协议 {u.scheme!r}")
            host = u.hostname or ""
            port = u.port  # 非数字/越界时 urlsplit 抛 ValueError
            if not host or port is None or not (0 < port < 65536):
                raise ValueError("host/port 缺失或越界")
            q = parse_qs(u.query)
            if "rate" not in q or "freq" not in q:
                raise ValueError("rate/freq 必填（裸流无元数据，不猜默认）")
            rate = float(q["rate"][0])
            freq = float(q["freq"][0])
            if not math.isfinite(rate) or rate <= 0:
                raise ValueError(f"rate 非法: {rate}")
            if not math.isfinite(freq) or freq < 0:
                raise ValueError(f"freq 非法: {freq}")
            fmt = q.get("fmt", ["cu8"])[0]
            byteorder = q.get("byteorder", ["little"])[0]
            if fmt not in NETWORK_IQ_FORMATS:
                raise ValueError(f"未知 IQ 格式 {fmt!r}")
            if byteorder not in ("little", "big"):
                raise ValueError(f"未知字节序 {byteorder!r}")
            out.append(dict(protocol=u.scheme, host=host, port=port,
                            sample_rate_hz=rate, center_freq_hz=freq,
                            fmt=fmt, byteorder=byteorder))
        except Exception as e:
            logger.debug(f"网络 IQ 配置无法解析（已跳过）: {item!r}: {e}")
    return out

# 项目统一默认采样率（Hz）。
# 产品口径（2026-11-05 裁定）：取 RTL-SDR 类上限常见值 2.4 MS/s（SDR++ 新设备
# 默认也是 2.4M，main.cpp:202）。"默认"只是**首选**，不是保证：设备若不支持该档，
# 由 nearest_sample_rate() / 各后端能力表回落到最接近的支持值，并把**实际生效值**
# 如实写进 SDRStatus.sample_rate_hz。desktop / agent 等上层一律引用本常量，
# 不要再写裸字面量；数字链路（ADS-B 等）自己声明所需采样率，不依赖此默认。
DEFAULT_SAMPLE_RATE_HZ = 2_400_000


def format_sample_rate(hz) -> str:
    """采样率唯一显示口径（U-03）：``MS/s``、固定 3 位小数、空格分隔。

    采样率是每秒样本数，量纲是 MS/s（样本/秒），不是 MHz（频率/带宽）；
    ``<1 MS/s`` 也写 ``0.250 MS/s``。缺失/NaN 显「—」，不拿 0 或默认值冒充读数。

    本函数是内核侧唯一实现；``desktop/constants.format_sample_rate`` 与
    ``mbdsdr_ai.sdr_tools._fmt_sample_rate`` 均从这里派生（内核 → 桌面）。
    """
    if hz is None:
        return "—"
    try:
        v = float(hz)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    return f"{v / 1e6:.3f} MS/s"


# 实时接收链 + 声卡输出（可选依赖，导入失败时音频路径安全降级，不崩）
try:
    from .receive_chain import ReceiveChain
    from .audio_out import AudioPlayer
except Exception as _e:  # pragma: no cover - 取决于运行环境
    ReceiveChain = None
    AudioPlayer = None
    logger.warning("ReceiveChain/AudioPlayer 导入失败，实时音频路径将降级: %s", _e)

from .doppler_compensation import DopplerCompensationError
from . import doppler_link


@dataclass
class SDRDevice:
    """SDR 设备信息。"""
    device_type: str  # rtl_sdr / hackrf / usrp / plutosdr / bladerf / limesdr / ai_sdr_mini / file
    device_id: str
    name: str
    frequency_range: Tuple[float, float]  # Hz
    sample_rate_range: Tuple[float, float]  # Hz
    max_gain: float  # dB
    supports_iq: bool = True
    supports_tx: bool = False
    # 发射能力是否在真实硬件上验证过。声明 True 只代表"设备+代码路径存在"，
    # 没上过真机就必须保持 False，UI/工具面据此标「未验证」而不是「支持」。
    tx_verified: bool = False
    connected: bool = False
    # 能力来源：{字段: probed/declared}。缺省=未标注，device_capabilities
    # 一律按 unknown 展示——绝不回退成「按声明」。
    capability_status: Dict[str, str] = field(default_factory=dict)
    # 能力项备注（未知原因/声明依据），与 capability_status 配合使用。
    capability_note: Dict[str, str] = field(default_factory=dict)


#: ``SDRStatus.squelch_applied_by`` 的合法取值（Python 与 C++ 回写共用同一组
#: 字面量；中文文档见 docs/limitations-register.md 附 H）。
SQUELCH_APPLIED_BY_VALUES = ("desktop-margin", "cpp-auto", "cpp-manual", "none")


@dataclass
class SDRStatus:
    """SDR 当前状态。"""
    connected: bool = False
    # 来源: gqrx/src/applications/gqrx/receiver.cpp:66 —— GQRX 默认守 144.8 MHz 业余段；
    # 我们面向普通用户首启体验，落在 98 MHz FM 广播段，一上来就能听到东西。
    frequency_hz: float = 98_000_000.0
    # 频率读数来源：request=只下发了请求值，设备未回执；readback=刚从驱动
    # 接口回读；空串=尚未调谐/未观测。界面与工具据此区分「设备确认」与
    # 「请求值」，不得把 request 冒充成设备实际频率。
    frequency_source: str = ""
    # None 表示"未选定，由后端子类决定"。各后端在 __init__ 里设自己的甜点档
    # （RTL 2.048M / HackRF 8M / USRP 1M / Pluto 2M，来源 gqrx ioconfig.cpp:266,279-302）；
    # connect() 时若仍为 None，用 2.048M 兜底。
    sample_rate_hz: Optional[float] = None
    gain_db: float = 0.0
    agc_enabled: bool = True
    bandwidth_hz: float = 0.0  # 0=自动
    demod_mode: str = "FM"  # FM/AM/SSB/LSB/USB/CW/NFM/WFM
    # 来源: gqrx/src/qtgui/dockrxopt.cpp:697 —— reset 到 -150 = 完全开门，
    # 与 analog_demod.py 静噪默认对齐。
    # **语义固定为「余量刻度」**（-100≈0 dB、-50≈6 dB、-20≈16 dB，见
    # mbdsdr_ai/squelch.py 顶部裁定）：它不是绝对 dBFS 门，禁止原样下发给
    # 解调链；换算只在 margin_threshold_db()/BaselineSquelch 里做。
    squelch_db: float = -150.0
    # 谁最后写的静噪值、按什么语义应用：
    # desktop-margin / cpp-auto / cpp-manual / none（空值=从未设置）。
    squelch_applied_by: str = "none"
    # 只有真正下发**绝对门**时才填（dBFS，由 margin_threshold_db + 实测底噪
    # 推导）；Python 路径按余量口径门控时保持 None。
    squelch_threshold_dbfs: Optional[float] = None
    volume: float = 0.5
    # 信号强度：None=从未采到，**不许拿 0/-100 冒充读数**（U-02）。
    # 物理量随后端而定，读 rssi_db 时必须连 rssi_unit 一起读：
    #   - 本地 IQ 链（RTL/HackRF/USRP…）：dBFS（相对 ADC 满量程）
    #   - ai-sdr mini（SI4732，MCP get_status）：dBµV（0–127）
    rssi_db: Optional[float] = None
    rssi_unit: str = "dBFS"
    snr_db: Optional[float] = None
    recording: bool = False
    recording_path: str = ""
    recording_duration: float = 0.0
    device_temp: float = 0.0
    samples_read: int = 0
    uptime_seconds: float = 0.0
    error: str = ""  # 最近一次 setter 失败/硬件回读失败的错误描述，空串=正常
    # ── 接入层可观测性（网络源/热插拔/独占场景；默认值=未知/未接入）──
    # link_state: connecting / connected / stale / disconnected / failed，空串=未接入观测
    link_state: str = ""
    error_category: str = ""       # 与 link_state.py 的错误词表一致（refused/timeout/reset/idle/...）
    error_code: str = ""           # error_catalog 稳定码（link_timeout/link_refused/link_half_open/link_reset/buffer_overflow）
    dropped_samples: int = 0       # 环形缓冲覆盖/流速率不足导致的丢样估计
    buffer_fill: int = 0           # 环形缓冲当前未读样点数（缓冲水位）
    buffer_capacity: int = 0       # 环形缓冲容量（样点），0=未知/无环形缓冲
    buffer_level: str = ""         # empty/low/ok/high（见 link_state.classify_buffer_level）
    underruns: int = 0             # 读侧因缓冲空而等待超时的次数（真实欠读）
    reconnect_count: int = 0       # 自进程启动以来的重连次数
    last_data_age_s: Optional[float] = None  # 距最后一次真实数据的时间（None=从未收到）
    # ── 采样率/带宽切换瞬态（idle/switching/completed/failed）──
    retune_state: str = "idle"       # 当前切换阶段；completed 表示新流已接管
    retune_kind: str = ""            # sample_rate / bandwidth
    retune_generation: int = 0       # 成功切换次数（单调递增，供消费方判新旧流）
    retune_dropped_samples: int = 0  # 切换时丢弃的旧缓冲样点数（如实计数）
    retune_elapsed_ms: float = 0.0   # 最近一次切换耗时
    retune_error: str = ""           # 切换失败原因（空=无）


class SDRBackend:
    """
    SDR 后端基类。

    所有具体后端都继承此类并实现接口。
    """

    # 若某个后端在 __init__ 里没有显式设采样率（status.sample_rate_hz 仍为 None），
    # connect() 时用 2.048M 兜底（1.024M 整数倍，便于 ADS-B/数字链路抽取）。
    _FALLBACK_SAMPLE_RATE_HZ: float = 2_048_000.0

    def __init__(self, device: SDRDevice):
        self.device = device
        self.status = SDRStatus()
        self._start_time = 0.0
        self._samples_read = 0
        # 录制线程相关
        self._recording_thread = None
        self._recording_stop_event = threading.Event()
        self._recording_samples_total = 0
        self._recording_start_time = 0.0
        self._recording_format = "cf32"
        self._recording_gain = 1.0
        self._recording_decimation = 1

        # ── 实时接收链 + 声卡输出（所有后端共享，无硬件时安全降级）──
        # ReceiveChain 把 15 个 DSP 模块串成端到端解调链；AudioPlayer 走
        # sounddevice/PortAudio 出声。无设备时全部方法返回 None/False，不造假。
        self._receive_chain: Optional[ReceiveChain] = None
        self._audio_player: Optional[AudioPlayer] = None
        self._audio_thread: Optional[threading.Thread] = None
        self._audio_stop_event = threading.Event()
        # 音频线程产出块的有界队列，供 read_audio() 拉取（不抢实时循环）
        self._audio_out_q: "collections.deque" = collections.deque()
        self._audio_out_lock = threading.Lock()
        self._audio_max_q_samples = 48000 * 3  # ≈3s 缓冲上限
        # 采样率/带宽切换串行化：同一时刻只允许一次切换，避免新旧流交叉。
        self._retune_lock = threading.Lock()
        # ── 接收链多普勒补偿（跨重建保留开关；读数见 get_doppler_compensation_status）──
        self._doppler_source = None            # None=用进程级 doppler_link 单例
        self._doppler_comp_enabled = False
        self._doppler_error = ""

    def connect(self) -> bool:
        """连接设备。"""
        raise NotImplementedError

    def disconnect(self):
        """断开设备。"""
        try:
            self.stop_audio()
        except Exception:
            pass
        self.status.connected = False
        self.status.link_state = "disconnected"
        # 会话读数归零/归 None：rssi/snr 的 None 语义是「从未采到」，
        # 断连后保留上次会话值等于拿旧数据冒充当前状态；samples_read/
        # uptime 是会话口径，重连从 0 起算（全部子类 disconnect 都走这里）。
        self.status.rssi_db = None
        self.status.snr_db = None
        self.status.uptime_seconds = 0.0
        self.status.samples_read = 0
        self._samples_read = 0
        self.status.last_data_age_s = None
        self._last_ring_data_time = None
        # 断开即废弃接收链：链内 BaselineSquelch 的底噪/门限是按上一段流
        # 校准的，复用会把旧静噪口径带进新流（热插拔实测第 2 轮即残留）。
        # 重连由 _ensure_receive_chain() 按当时 status 全新构建。
        self._receive_chain = None

    def set_frequency(self, freq_hz: float) -> bool:
        """设置中心频率。失败时回滚 status，不留"已设置"假状态。"""
        if not self.status.connected:
            return False
        lo, hi = self._frequency_bounds()
        if freq_hz < lo or freq_hz > hi:
            # 超出真实可调范围时绝不把请求下发给硬件：写入会被库拒绝，
            # 实测 FC0012 在写频失败后句柄进入不可恢复态（后续读回恒 0、
            # 再写触发 access violation），只能重连恢复。
            self.status.error = user_message(
                "sdr_frequency_out_of_range",
                front_end=self._front_end_label(),
                range_mhz=f"{lo / 1e6:.3f}–{hi / 1e6:.3f}",
                requested_mhz=f"{freq_hz / 1e6:.3f}",
            )
            logger.warning(
                "set_frequency(%.3f MHz) 超出实际可调范围 %.3f–%.3f MHz，"
                "未向硬件下发", freq_hz / 1e6, lo / 1e6, hi / 1e6)
            return False
        old = self.status.frequency_hz
        self.status.error = ""
        try:
            ok = self._apply_frequency(freq_hz)
        except Exception as e:
            ok = False
            self.status.error = user_message(
                "sdr_set_frequency_failed",
                detail=f"{type(e).__name__}: {e}")
        if not ok:
            self.status.frequency_hz = old  # 回滚
            if not self.status.error:
                self.status.error = user_message(
                    "sdr_set_frequency_failed", detail="底层返回失败")
            logger.warning(f"set_frequency({freq_hz}) 失败，已回滚到 {old}")
            return False
        self.status.frequency_hz = freq_hz
        # 默认后端只下发请求值，没有回读通道；有 getFrequency 的子类会覆写
        # 成 readback（见 SoapySDRBackend.set_frequency）。
        self.status.frequency_source = "request"
        # 换频后清空接收链状态 + 环形缓冲（RTLSDRBackend._apply_frequency 已
        # reset_buffer + ring_reader.clear()，这里再重置链状态，避免旧频残留）
        self._invalidate_rx_chain(rebuild=False)
        return True

    def _frequency_bounds(self) -> Tuple[float, float]:
        """当前实际可调频率区间（Hz）。默认取设备声明区间；
        子类应按真实调谐器/硬件回读收窄，别让声明区间替硬件吹牛。"""
        lo, hi = self.device.frequency_range
        return float(lo), float(hi)

    def _front_end_label(self) -> str:
        """错误文案里对前端的称呼；子类可细化到调谐器型号。"""
        return "设备"

    def get_frequency_bounds(self) -> Tuple[float, float]:
        """UI/能力协商用：当前设备实际可调区间（Hz），已按真实 tuner 收窄。"""
        return self._frequency_bounds()

    def _apply_frequency(self, freq_hz: float) -> bool:
        """子类覆写：把频率真正写入硬件或回放文件。默认无操作直接成功。"""
        return True

    def get_frequency(self) -> float:
        return self.status.frequency_hz

    # -- 切换瞬态辅助 -------------------------------------------------------
    def _retune_ring_buffered(self) -> int:
        """环形缓冲当前积压样点数；无环形缓冲时为 0。"""
        rr = getattr(self, "_ring_reader", None)
        if rr is None:
            return 0
        try:
            return max(0, int(rr.available()))
        except Exception:
            return 0

    def _clear_ring_for_retune(self) -> None:
        """切换成功/失败边界上丢弃旧流样点，保证新读窗口不与旧流重叠。"""
        rr = getattr(self, "_ring_reader", None)
        if rr is not None:
            try:
                rr.clear()
            except Exception:
                pass

    def _begin_retune(self, kind: str) -> Optional[float]:
        """进入 switching；并发切换直接拒绝（不排队、不交叉）。"""
        if not self._retune_lock.acquire(blocking=False):
            self.status.retune_state = "failed"
            self.status.retune_kind = kind
            self.status.retune_error = "上一次采样率/带宽切换尚未完成"
            self.status.error = self.status.retune_error
            return None
        self.status.retune_state = "switching"
        self.status.retune_kind = kind
        self.status.retune_error = ""
        self.status.error = ""
        return time.monotonic()

    def _finish_retune(self, started: float, ok: bool, dropped: int,
                       error: str = "") -> None:
        """落切换结果；只有成功才推进 generation 并记丢弃量。"""
        self.status.retune_elapsed_ms = (time.monotonic() - started) * 1000.0
        if ok:
            self.status.retune_state = "completed"
            self.status.retune_generation += 1
            self.status.retune_dropped_samples += int(dropped)
            self.status.retune_error = ""
        else:
            self.status.retune_state = "failed"
            self.status.retune_error = error or "切换失败"
        self._retune_lock.release()

    def set_sample_rate(self, rate_hz: float) -> bool:
        """设置采样率。失败时回滚 status；切换瞬态与丢样如实暴露。"""
        if not self.status.connected:
            return False
        old = self.status.sample_rate_hz
        started = self._begin_retune("sample_rate")
        if started is None:
            return False
        dropped = self._retune_ring_buffered()
        try:
            try:
                ok = self._apply_sample_rate(rate_hz)
            except Exception as e:
                ok = False
                self.status.error = user_message(
                    "sdr_set_sample_rate_failed",
                    detail=f"{type(e).__name__}: {e}")
            if not ok:
                self.status.sample_rate_hz = old
                if not self.status.error:
                    self.status.error = user_message(
                        "sdr_set_sample_rate_failed", detail="底层返回失败")
                self._finish_retune(started, False, 0, self.status.error)
                logger.warning(f"set_sample_rate({rate_hz}) 失败，已回滚到 {old}")
                return False
            # 记录“真正生效值”而非请求值：RTL 会把请求吸附到 11 档离散集，
            # 记请求值会让 status/DSP 的 fs_in 与硬件实际速率不一致。
            self.status.sample_rate_hz = self._resolve_applied_sample_rate(rate_hz)
            # 新流接管：先丢弃旧采样率下的缓冲（子类可能已清，重复清是幂等的），
            # 再重建整条接收链（fs_in 已变）。
            self._clear_ring_for_retune()
            self._invalidate_rx_chain(rebuild=True)
            self._finish_retune(started, True, dropped)
            return True
        except Exception:
            self._retune_lock.release()
            raise

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        """子类覆写：把采样率真正写入硬件。"""
        return True

    def _resolve_applied_sample_rate(self, rate_hz: float) -> float:
        """子类覆写：返回硬件真正接受的采样率（吸附/钳位后的值）。

        默认等于请求值。会改写请求值的后端必须覆写，否则 ``get_sample_rate``
        回读的是请求值而非实际值，调用方无法判断「选了是否真生效」。
        """
        return float(rate_hz)

    def get_sample_rate(self) -> float:
        return self.status.sample_rate_hz

    def set_gain(self, gain_db: float) -> bool:
        """设置增益。失败时回滚 status。"""
        if not self.status.connected:
            return False
        clamped = max(0, min(gain_db, self.device.max_gain))
        old = self.status.gain_db
        self.status.error = ""
        try:
            ok = self._apply_gain(clamped)
        except Exception as e:
            ok = False
            self.status.error = user_message(
                "sdr_set_gain_failed", detail=f"{type(e).__name__}: {e}")
        if not ok:
            self.status.gain_db = old
            if not self.status.error:
                self.status.error = user_message(
                    "sdr_set_gain_failed", detail="底层返回失败")
            logger.warning(f"set_gain({clamped}) 失败，已回滚到 {old}")
            return False
        self.status.gain_db = clamped
        self.status.agc_enabled = False
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        """子类覆写：把增益真正写入硬件。"""
        return True

    def get_gain(self) -> float:
        return self.status.gain_db

    def gain_readback_source(self) -> str:
        """增益读数的来源说明（界面 tooltip 用）。

        空串 = 本后端没有增益回读通道，``status.gain_db`` 只是请求值（可能
        经软件限幅/吸附），界面必须显示「请求值，未回读」。子类确有回读通道
        时返回人话说明，界面据此标明读数来源，不许把请求值包装成设备回执。
        """
        return ""

    def readback_hw_state(self) -> bool:
        """连接成功后从硬件回读实际 center_freq/sample_rate/gain，对齐内部状态。

        硬件不支持回读时记录 warning 并返回 False，不抛异常。
        子类应覆写此方法实现真实回读。
        """
        logger.warning(f"{self.__class__.__name__} 不支持硬件回读，软件状态可能与硬件实际不一致")
        return False

    def set_agc(self, enabled: bool) -> bool:
        """设置 AGC 开关。失败时回滚 status（与 set_gain 同纪律）。"""
        if not self.status.connected:
            return False
        old = self.status.agc_enabled
        self.status.error = ""
        try:
            ok = self._apply_agc(bool(enabled))
        except Exception as e:
            ok = False
            self.status.error = user_message(
                "sdr_set_agc_failed", detail=f"{type(e).__name__}: {e}")
        if not ok:
            self.status.agc_enabled = old
            if not self.status.error:
                self.status.error = user_message(
                    "sdr_set_agc_failed", detail="底层返回失败")
            logger.warning(f"set_agc({enabled}) 失败，已回滚到 {old}")
            return False
        self.status.agc_enabled = bool(enabled)
        return True

    def _apply_agc(self, enabled: bool) -> bool:
        """子类覆写：把 AGC 开关真正写入硬件。默认无操作直接成功。"""
        return True

    def _apply_bandwidth(self, bw_hz: float) -> bool:
        """子类覆写：把模拟/信道带宽真正写入硬件。默认无操作直接成功。"""
        return True

    def set_bandwidth(self, bw_hz: float) -> bool:
        """设置带宽。切换瞬态与丢弃的旧缓冲样点如实暴露。"""
        if not self.status.connected:
            return False
        old = self.status.bandwidth_hz
        started = self._begin_retune("bandwidth")
        if started is None:
            return False
        dropped = self._retune_ring_buffered()
        try:
            try:
                ok = self._apply_bandwidth(bw_hz)
            except Exception as e:
                ok = False
                self.status.error = user_message(
                    "sdr_set_bandwidth_failed",
                    detail=f"{type(e).__name__}: {e}")
            if not ok:
                self.status.bandwidth_hz = old
                if not self.status.error:
                    self.status.error = user_message(
                        "sdr_set_bandwidth_failed", detail="底层返回失败")
                self._finish_retune(started, False, 0, self.status.error)
                return False
            self.status.bandwidth_hz = bw_hz
            # 带宽变化会改信道化/重采样结构：丢弃旧结构下的缓冲样点后重建。
            self._clear_ring_for_retune()
            self._invalidate_rx_chain(rebuild=True)
            self._finish_retune(started, True, dropped)
            return True
        except Exception:
            self._retune_lock.release()
            raise

    def set_demod(self, mode: str) -> bool:
        # RAW/DIG 表示不解调模拟音频、直接取原始 IQ（ADS-B/APRS/FT8/导航等数字链路）
        valid = ["FM", "AM", "SSB", "LSB", "USB", "CW", "NFM", "WFM",
                 "RAW", "DIG"]
        if mode.upper() not in valid:
            return False
        self.status.demod_mode = mode.upper()
        # 切模式时自动按 SDR++ 默认设置解调带宽，不写死。
        # 来源: repos/sdrpp/decoder_modules/radio/src/demodulators/*/getDefaultBandwidth()
        # 我们的 modes_defaults.py 已对齐这些值（AM ±3k=6k、NFM ±4k=8k、
        # WFM ±75k=150k 等），从表中取 high-low 作为总带宽。
        #
        # 红线（2026-11-12）：**先下发、再写 status**。status.bandwidth_hz 的
        # 语义是「硬件/解调链真在用的值」，只记录实际下发成功的数；下发失败
        # 就保留原值（真值），绝不把一个没发生过的模式默认值写成读数。
        try:
            from .modes_defaults import get_mode_bandwidth
            bw_info = get_mode_bandwidth(mode.upper())
            bw = float(bw_info["high_hz"] - bw_info["low_hz"])
            if bw > 0 and self._apply_bandwidth(bw):
                self.status.bandwidth_hz = bw
        except Exception:
            pass  # 表缺失/硬件拒绝时保留原有 bandwidth，不阻断模式切换
        # 模式改变 → 重建整条接收链（if_sr/解调器/重采样器都可能变）
        self._invalidate_rx_chain(rebuild=True)
        return True

    def set_squelch(self, db: float) -> bool:
        """设置静噪**余量刻度**（-100..-20；-150=完全开门）。

        滑杆/agent 工具/遥控传进来的都是"比噪声基线高多少"（-50≈6 dB），
        **不是绝对 dBFS 门**。这里只记录余量与来源，绝不再把该数字原样当
        绝对门下发；ReceiveChain 内部统一用 BaselineSquelch 按余量换算
        （与 desktop/receive_pipeline.py 同一实现）。
        """
        value = float(db)
        self.status.squelch_db = value
        self.status.squelch_applied_by = "desktop-margin"
        # 没有真正下发绝对门 → 该字段保持 None（只有 C++/硬件路径才填）。
        self.status.squelch_threshold_dbfs = None
        chain = getattr(self, "_receive_chain", None)
        if chain is not None:
            try:
                chain.set_squelch(value)
            except Exception as e:  # 重建窗口内的偶发失败不打断主流程
                logger.warning("同步 ReceiveChain 静噪失败: %s", e)
        return True

    def set_volume(self, vol: float) -> bool:
        self.status.volume = max(0.0, min(1.0, vol))
        return True

    def subscribe_iq(self, name: str):
        """获取（或创建）一个命名 IQ 订阅者，返回其 reader（含 .read/.available）。

        后端无 fan-out ring 时返回 None，调用方回退到 read_samples。"""
        rr = getattr(self, "_ring_reader", None)
        if rr is not None and hasattr(rr, "subscribe"):
            try:
                return rr.subscribe(name)
            except Exception:
                return None
        return None

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        """读取 IQ 样本，返回复数数组。"""
        raise NotImplementedError

    def get_status(self) -> SDRStatus:
        if self.status.connected:
            self.status.uptime_seconds = time.time() - self._start_time
            self.status.samples_read = self._samples_read
            if not self.status.link_state:
                self.status.link_state = "connected"
        else:
            if not self.status.link_state:
                self.status.link_state = "disconnected"
        # 环形缓冲可观测性：真实积压与丢样（有 ring 的本地/网络后端都适用）。
        rr = getattr(self, "_ring_reader", None)
        if rr is not None:
            try:
                from .link_state import (
                    classify_buffer_level, error_code_for_buffer_level,
                )
                self.status.buffer_fill = rr.available()
                self.status.buffer_capacity = rr.capacity()
                self.status.underruns = rr.underruns_total()
                self.status.dropped_samples = rr.dropped_total()
                level = classify_buffer_level(
                    self.status.buffer_fill, self.status.buffer_capacity)
                self.status.buffer_level = level["level"]
                # 环形缓冲积压到 high（旧样本将被覆盖）是独立于断链的失败：
                # 只有在没有更具体的链路码时才补 buffer_overflow，避免覆盖
                # link_timeout/link_half_open 这类更该优先展示的根因。
                if not self.status.error_code:
                    self.status.error_code = error_code_for_buffer_level(
                        level["level"])
            except Exception:
                pass
        # 最后一次真实数据距今时间（有 ring 的后端在生产者成功读到时刷新）。
        last_ts = getattr(self, "_last_ring_data_time", None)
        if last_ts is not None:
            self.status.last_data_age_s = max(0.0, time.time() - last_ts)
        return self.status

    # ── 发射能力：单一事实源 ───────────────────────────────────────────
    def _note_tx_reject(self) -> None:
        """把 tx_guard 的拒绝原因同步到 status.error，让 UI/用户看到为什么没发。"""
        info = getattr(self, "last_tx_reject", None) or {}
        code = info.get("code", "")
        reason = info.get("reason", "未过人工确认通道")
        self.status.error = f"[TX] 发射被拒({code}): {reason}"

    def _tx_ready(self) -> bool:
        """运行时就绪性（驱动/发送流是否真的可写）。子类按真实句柄覆写。"""
        return callable(getattr(type(self), "write_tx", None))

    def supports_tx(self) -> bool:
        """是否真的能发波 = 设备声明 ∧ 本类确有 write_tx ∧ 运行时就绪。

        只有声明没有实现的设备一律 False：「支持发射」必须能落到真实写流，
        不是一句文案。UI/PTT 的置灰只看这一个出口。
        """
        if not bool(getattr(self.device, "supports_tx", False)):
            return False
        try:
            return bool(self._tx_ready())
        except Exception as e:  # noqa: BLE001 - 能力查询失败按不可用处理
            logger.debug("%s 发射就绪检查异常: %s", self.__class__.__name__, e)
            return False

    def tx_capability(self) -> Dict[str, Any]:
        """能力矩阵行：声明 / 实现 / 运行时就绪 / 真机验证（供工具面/UI 展示）。"""
        implemented = callable(getattr(type(self), "write_tx", None))
        try:
            ready = bool(self._tx_ready())
            note = ""
        except Exception as e:  # noqa: BLE001
            ready = False
            note = f"就绪检查异常: {type(e).__name__}: {e}"
        return {
            "backend": self.__class__.__name__,
            "declared": bool(getattr(self.device, "supports_tx", False)),
            "implemented": implemented,
            "ready": ready,
            "verified": bool(getattr(self.device, "tx_verified", False)),
            "note": note,
        }

    # ==================================================================
    # 实时接收链 + 声卡输出（UI / 录音 / 声卡消费面）
    #
    # 流式接口契约：
    #   read_samples(n) -> complex64 (n,) | None     # 原始 IQ（频谱/瀑布）
    #   read_audio(n)   -> float32   (n,) | None      # 解调音频（48kHz）
    #   start_audio()/stop_audio()                   # 声卡实时播放线程
    #   get_spectrum_data(nfft) -> float32 | None    # FFT 功率谱 dB
    # 无设备时以上全部返回 None/False，绝不造假。
    # ==================================================================

    def _invalidate_rx_chain(self, rebuild: bool = True) -> None:
        """配置变更后使接收链失效。rebuild=True 强制重建；False 仅清状态。

        无连接时静默返回（不抛异常、不造假）。
        """
        if not self.status.connected:
            self._receive_chain = None
            return
        if rebuild:
            self._receive_chain = None  # 下次 _ensure 时按最新 status 重建
        elif self._receive_chain is not None:
            try:
                self._receive_chain.reset()
            except Exception:
                pass
        # 清空环形缓冲里旧频/旧模式数据（若存在）
        rr = getattr(self, "_ring_reader", None)
        if rr is not None:
            try:
                rr.clear()
            except Exception:
                pass

    def _ensure_receive_chain(self):
        """按需构建/同步 ReceiveChain。未连接或依赖缺失返回 None（不造假）。"""
        if not self.status.connected:
            self._receive_chain = None
            return None
        if ReceiveChain is None:
            return None
        if self._receive_chain is None:
            try:
                self._receive_chain = ReceiveChain(
                    fs_in=self.status.sample_rate_hz or self._FALLBACK_SAMPLE_RATE_HZ,
                    mode=self.status.demod_mode,
                    bandwidth=(self.status.bandwidth_hz or None),
                    frequency_offset=0.0,
                    squelch_db=self.status.squelch_db,
                    gain_db=self.status.gain_db,
                )
            except Exception as e:
                logger.warning("ReceiveChain 构建失败: %s", e)
                self._receive_chain = None
                return None
            # 部署形态显式声明：只有这里（真建链、真读 IQ）才claim
            # 「Python 独占本流」；C++ 桌面/远端 WebSocket 形态没有本地
            # SDRBackend，不会声明，预测源保持保守默认（不补偿）。
            src = self._doppler_src()
            claim = getattr(src, "claim_python_stream", None)
            if callable(claim):
                try:
                    claim(True)
                except Exception as e:  # noqa: BLE001
                    logger.warning("多普勒部署形态声明失败: %s", e)
            # 重建后恢复补偿绑定与开关（重建若丢了补偿，读数会假装关闭）
            self._bind_receive_chain_doppler(self._receive_chain)
        else:
            # 动态轻量参数直接同步进已有链（不重建滤波器）
            try:
                self._receive_chain.set_squelch(self.status.squelch_db)
                self._receive_chain.set_gain(self.status.gain_db)
            except Exception:
                pass
        return self._receive_chain

    # ---- 接收链多普勒补偿（唯一施加点：基带 NCO） ----
    def _doppler_src(self):
        return self._doppler_source if self._doppler_source is not None \
            else doppler_link.get_compensator()

    def _bind_receive_chain_doppler(self, chain) -> None:
        """把预测源绑到接收链，并按持久开关恢复启用状态。"""
        if chain is None:
            return
        try:
            chain.bind_doppler_source(self._doppler_src())
            if self._doppler_comp_enabled:
                chain.set_doppler_compensation(True)
        except Exception as e:  # noqa: BLE001 - 绑定失败要如实出现在读数里
            self._doppler_error = (f"多普勒补偿绑定失败"
                                   f"（{type(e).__name__}: {e}）")
            logger.warning(self._doppler_error)

    def set_doppler_compensation(self, enabled: bool, source=None) -> bool:
        """显式开关接收链多普勒补偿；返回是否接受（未连接/后端冲突→False）。

        开启时不检查目标是否存在：没有目标就保持 0 并在读数里写
        「无目标，未补偿」；目标出现后自动开始补偿。
        """
        if source is not None:
            self._doppler_source = source
        chain = self._ensure_receive_chain()
        if chain is None:
            self._doppler_comp_enabled = bool(enabled)
            self._doppler_error = "未连接设备，接收链不存在，无法接入多普勒补偿"
            return False
        self._bind_receive_chain_doppler(chain)
        try:
            ok = bool(chain.set_doppler_compensation(bool(enabled)))
        except Exception as e:  # noqa: BLE001
            self._doppler_error = (f"多普勒补偿开关失败"
                                   f"（{type(e).__name__}: {e}）")
            logger.warning(self._doppler_error)
            return False
        self._doppler_comp_enabled = ok and bool(enabled)
        self._doppler_error = "" if ok else chain.doppler_status().get("reason", "")
        return ok

    def get_doppler_compensation_status(self) -> dict:
        """当前补偿量读数：是否生效、applied_hz、目标、预测时刻/星历、原因、错误。"""
        chain = self._receive_chain
        if chain is None:
            try:
                st = self._doppler_src().reading()
            except Exception:  # noqa: BLE001
                st = {}
            predicted = None
            if st.get("valid") and st.get("doppler_hz") is not None:
                try:
                    predicted = float(st["doppler_hz"])
                except (TypeError, ValueError):
                    predicted = None
            backend = bool(st.get("backend_comp_active"))
            return {
                "enabled": self._doppler_comp_enabled,
                "active": False,
                "predicted_hz": predicted,
                "applied_hz": None if backend else 0.0,
                "applied_by": "backend" if backend else "none",
                "python_applied_hz": 0.0,
                "target_doppler_hz": None, "settling": False,
                "target_name": st.get("target_name", ""),
                "predicted_at_unix": st.get("predicted_at_unix"),
                "doppler_rate_hz_s": st.get("doppler_rate_hz_s"),
                "tle_tier": st.get("tle_tier", ""),
                "tle_age_days": st.get("tle_age_days"),
                "application": st.get("application", "baseband"),
                "python_stream_owner": bool(st.get("python_stream_owner")),
                "reason": "未连接设备，接收链不存在（无目标，未补偿）",
                "error": self._doppler_error,
                "blocks": 0, "samples": 0,
            }
        st = chain.doppler_status()
        if self._doppler_error:
            st["error"] = self._doppler_error
        return st

    def read_audio(self, num_samples: int) -> Optional[np.ndarray]:
        """取 num_samples 个解调音频样本（float32, 48kHz）。

        音频线程在跑时从其输出队列取数；否则自己拉一块 IQ 跑链。
        未连接/无数据返回 None。
        """
        if not self.status.connected:
            return None
        chain = self._ensure_receive_chain()
        if chain is None:
            return None
        num_samples = int(num_samples)
        if num_samples <= 0:
            return np.zeros(0, dtype=np.float32)

        # 音频线程在跑 → 从其输出队列凑够 num_samples
        if self._audio_thread is not None and self._audio_thread.is_alive():
            collected = []
            got = 0
            deadline = time.time() + 1.0
            while got < num_samples and time.time() < deadline:
                with self._audio_out_lock:
                    blk = self._audio_out_q.popleft() if self._audio_out_q else None
                if blk is None:
                    if self._audio_stop_event.is_set():
                        break
                    time.sleep(0.002)
                    continue
                collected.append(blk)
                got += len(blk)
            if not collected:
                return None
            out = np.concatenate(collected, axis=0)
            return out[:num_samples].astype(np.float32)

        # 音频线程没跑：自己拉 IQ 跑一块
        try:
            iq = self.read_samples(max(num_samples * 4, 4096))
        except Exception:
            return None
        if iq is None or len(iq) == 0:
            return None
        try:
            audio = chain.process(iq)
        except DopplerCompensationError as e:
            # 补偿失败必须报错：不静默按 0 出音频，本块丢弃并暴露到读数
            self._doppler_error = str(e)
            self.status.error = user_message("doppler_compensation_failed",
                                             detail=str(e))
            logger.warning("接收链多普勒补偿失败: %s", e)
            return None
        except Exception:
            return None
        if audio is None or len(audio) == 0:
            return None
        return audio[:num_samples].astype(np.float32)

    def _audio_loop(self) -> None:
        """实时音频线程：读 IQ → ReceiveChain → 声卡（+ 供 read_audio 拉取）。"""
        iq_block = 8192
        while not self._audio_stop_event.is_set():
            chain = self._ensure_receive_chain()
            if chain is None:
                time.sleep(0.05)
                continue
            if getattr(self, "_audio_sub", None) is None:
                try:
                    self._audio_sub = self.subscribe_iq("audio")
                except Exception:
                    self._audio_sub = None
            try:
                if self._audio_sub is not None:
                    iq = self._audio_sub.read(iq_block, timeout=1.0)
                else:
                    iq = self.read_samples(iq_block)
            except Exception:
                iq = None
            if iq is None or len(iq) == 0:
                if self._audio_stop_event.is_set():
                    break
                time.sleep(0.005)
                continue
            try:
                audio = chain.process(iq)
            except DopplerCompensationError as e:
                # 补偿失败必须报错（读数+日志），本块跳过，不假装补偿过
                self._doppler_error = str(e)
                self.status.error = user_message("doppler_compensation_failed",
                                                 detail=str(e))
                logger.warning("接收链多普勒补偿失败: %s", e)
                continue
            except Exception as e:
                logger.debug("接收链 process 异常: %s", e)
                continue
            if audio is None or len(audio) == 0:
                continue
            # 写声卡（无设备时 AudioPlayer.write 安全返回 0，不崩）
            if self._audio_player is not None:
                try:
                    self._audio_player.write(audio)
                except Exception:
                    pass
            # 入队供 read_audio() 拉取（有界，丢旧保新）
            with self._audio_out_lock:
                self._audio_out_q.append(audio)
                total = 0
                for b in self._audio_out_q:
                    total += len(b)
                while total > self._audio_max_q_samples and self._audio_out_q:
                    old = self._audio_out_q.popleft()
                    total -= len(old)

    def start_audio(self) -> bool:
        """启动声卡实时播放线程。

        返回 True=线程已启动；未连接/依赖缺失返回 False（不崩、不造假）。
        sounddevice 无设备时 AudioPlayer.start() 返回 False，线程仍运行，
        音频只缓冲供 read_audio/录音（不出声但不崩）。
        """
        if not self.status.connected:
            return False
        chain = self._ensure_receive_chain()
        if chain is None:
            return False
        channels = 2 if chain.is_stereo else 1
        if AudioPlayer is None:
            return False
        self._audio_player = AudioPlayer(sample_rate=48000, channels=channels,
                                         gain=self.status.volume)
        try:
            if not self._audio_player.start():
                logger.info("声卡不可用：音频降级为只缓冲（不出声）")
        except Exception as e:
            logger.info("AudioPlayer 启动异常（降级只缓冲）: %s", e)
        self._audio_stop_event.clear()
        with self._audio_out_lock:
            self._audio_out_q.clear()
        self._audio_thread = threading.Thread(
            target=self._audio_loop, name="mbdsdr-audio", daemon=True)
        self._audio_thread.start()
        return True

    def stop_audio(self) -> None:
        """停止声卡播放线程（可重复调用，安全）。"""
        self._audio_stop_event.set()
        t = self._audio_thread
        if t is not None:
            t.join(timeout=1.0)
            self._audio_thread = None
        if self._audio_player is not None:
            try:
                self._audio_player.stop()
            except Exception:
                pass
            self._audio_player = None
        with self._audio_out_lock:
            self._audio_out_q.clear()

    def get_spectrum_data(self, nfft: int = 1024) -> Optional[np.ndarray]:
        """返回 nfft 点 FFT 功率谱 dB（float32，已 fftshift，居中 DC）。

        未连接返回 None。窗函数 Hann。
        """
        if not self.status.connected:
            return None
        nfft = int(nfft)
        try:
            iq = self.read_samples(nfft)
        except Exception:
            return None
        if iq is None or len(iq) < nfft:
            return None
        x = np.asarray(iq[:nfft], dtype=np.complex128)
        window = np.hanning(nfft)
        spec = np.fft.fftshift(np.fft.fft(x * window))
        db = 20.0 * np.log10(np.abs(spec) + 1e-12)
        return db.astype(np.float32)

    def start_recording(self, path: str, duration: float = 0,
                        fmt: str = "cf32", gain: float = 1.0,
                        decimation: int = 1,
                        max_bytes: int = 2 * 1024 * 1024 * 1024) -> bool:
        """
        开始录制基带（真正写文件）。

        启动后台线程，边采边流式写盘，支持长时间录制不占内存。
        支持 cu8（rtl_sdr 原生 unsigned8，生态最通用）/ cf32 / cs16 / wav / csv。
        duration > 0 时到点自动停止。
        max_bytes 为防爆盘软上限（默认 2GB）：到达后按样本帧对齐截断并自动收尾，
        不写半个样本（诚实红线：截断如实记录在 sidecar bytes/samples）。
        录制同时写同名 .json sidecar（含 format/sample_rate/center_freq），可直接回放。
        """
        if not self.status.connected:
            return False
        if self.status.recording:
            return False  # 已经在录制

        # 确保目录存在
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        self.status.recording = True
        self.status.recording_path = path
        self.status.recording_duration = duration
        self._recording_format = fmt
        self._recording_gain = gain
        self._recording_decimation = decimation
        self._recording_samples_total = 0
        self._recording_start_time = time.time()
        self._recording_stop_event.clear()

        # 启动录制线程
        self._recording_max_bytes = int(max_bytes) if max_bytes else 0
        self._recording_thread = threading.Thread(
            target=self._recording_loop,
            args=(path, fmt, duration, gain, decimation),
            daemon=True,
        )
        self._recording_thread.start()
        return True

    def _recording_loop(self, path: str, fmt: str, duration: float,
                        gain: float, decimation: int):
        """录制线程主循环：边采边流式写盘，支持长时间录制不占内存。"""
        chunk_size = 16384  # 每次读取的样本数
        samples_total = 0
        fh = None
        wf = None
        fmt_l = fmt.lower()
        effective_rate = int(self.status.sample_rate_hz / max(decimation, 1))

        try:
            if fmt_l == "wav":
                import wave
                wf = wave.open(path, "wb")
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(effective_rate)
            elif fmt_l == "csv":
                fh = open(path, "w", encoding="utf-8")
                fh.write("I,Q\n")
            else:
                fh = open(path, "wb")  # cf32 / cs16 / cu8

            # 每样本字节数（csv 文本不定长，按写前文件大小近似判断，到限同样收尾）
            _bytes_per_sample = {"cf32": 8, "cs16": 4, "cu8": 2, "wav": 4, "csv": 0}
            max_bytes = getattr(self, "_recording_max_bytes", 0)
            _truncate_after_write = False
            _check_size_after_write = False

            while not self._recording_stop_event.is_set():
                if duration > 0 and (time.time() - self._recording_start_time) >= duration:
                    break

                samples = self.read_samples(chunk_size)
                if samples is None or len(samples) == 0:
                    time.sleep(0.01)
                    continue

                if decimation > 1:
                    samples = samples[::decimation]

                # 防爆盘：写前按样本帧裁剪到剩余额度，到限本块写完即收尾
                if max_bytes:
                    bps = _bytes_per_sample.get(fmt_l, 0)
                    if bps > 0:
                        written = (fh.tell() if fh is not None
                                   else (os.path.getsize(path) if wf is not None else 0))
                        room = max_bytes - written
                        if room < bps:
                            break
                        fit = room // bps
                        if len(samples) > fit:
                            samples = samples[:fit]
                            _truncate_after_write = True
                    else:
                        _check_size_after_write = True  # csv：写后按实际大小判断

                # pyrtlsdr 等后端返回的 IQ 已归一化到约 [-1,1]，直接按满量程量化
                z = np.asarray(samples, dtype=np.complex128) * gain
                if fmt_l == "cf32":
                    np.column_stack([z.real, z.imag]).astype(np.float32).tofile(fh)
                elif fmt_l == "cs16":
                    ci = np.clip(z.real, -1.0, 1.0) * 32767
                    cq = np.clip(z.imag, -1.0, 1.0) * 32767
                    np.column_stack([ci, cq]).astype(np.int16).tofile(fh)
                elif fmt_l == "cu8":
                    # rtl_sdr / SDR# / GQRX 通用 unsigned 8-bit 交错，中心 127.5
                    ui = np.clip(z.real, -1.0, 1.0) * 127.5 + 127.5
                    uq = np.clip(z.imag, -1.0, 1.0) * 127.5 + 127.5
                    np.column_stack([ui, uq]).astype(np.uint8).tofile(fh)
                elif fmt_l == "wav":
                    ci = np.clip(z.real, -1.0, 1.0) * 32767
                    cq = np.clip(z.imag, -1.0, 1.0) * 32767
                    wf.writeframes(np.column_stack([ci, cq]).astype(np.int16).tobytes())
                elif fmt_l == "csv":
                    np.savetxt(fh, np.column_stack([z.real, z.imag]), fmt="%.8f", delimiter=",")

                samples_total += len(z)
                self._recording_samples_total = samples_total
                if _truncate_after_write:
                    break  # 防爆盘到限：整帧写完即收尾
                if _check_size_after_write and max_bytes:
                    if os.path.getsize(path) >= max_bytes:
                        break  # csv 到限：文本写完即收尾

        except Exception as e:
            print(f"录制线程错误: {e}")
        finally:
            try:
                if wf is not None:
                    wf.close()
                if fh is not None:
                    fh.close()
            except Exception:
                pass
            self.status.recording = False
            self._write_recording_sidecar(path, fmt_l, effective_rate,
                                          samples_total, decimation)

    def _write_recording_sidecar(self, path: str, fmt: str, effective_rate: int,
                                 samples_total: int, decimation: int):
        """写 sidecar 元数据；键名与 FileIQBackend 对齐，录完即可回放。"""
        try:
            metadata = {
                # FileIQBackend 直接识别的三个键
                "format": fmt,
                "sample_rate": float(effective_rate),
                "center_freq": float(self.status.frequency_hz),
                # 兼容旧字段
                "path": path,
                "center_hz": float(self.status.frequency_hz),
                "sample_rate_hz": float(effective_rate),
                "decimation": decimation,
                "samples": int(samples_total),
                "bytes": int(os.path.getsize(path)) if os.path.exists(path) else 0,
                "duration_s": float(samples_total / effective_rate) if effective_rate else 0,
                "start_unix": float(self._recording_start_time),
                "end_unix": float(time.time()),
                "gain_db": float(self.status.gain_db),
                "agc_enabled": bool(self.status.agc_enabled),
                "note": "cu8=rtl_sdr unsigned8 interleaved; cf32=float32 interleaved; "
                        "cs16=int16 interleaved; wav I=ch1 Q=ch2",
            }
            with open(path + ".json", "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"写 sidecar JSON 错误: {e}")

    def stop_recording(self) -> str:
        """停止录制，返回文件路径。定时自动停止后再调用也应幂等返回该路径。"""
        path = self.status.recording_path
        if self.status.recording:
            self._recording_stop_event.set()
            # 等待录制线程结束（最多 5 秒）
            if self._recording_thread and self._recording_thread.is_alive():
                self._recording_thread.join(timeout=5.0)

        self.status.recording = False
        self.status.recording_path = ""
        return path or ""


# ============================================================================
# 多订阅者 fan-out 环形缓冲
# 对标 SDR++：每个 sink（频谱/音频/解码）持有独立 ring 与读游标，生产者只需
# 一份采样并 fan-out；慢订阅者独立丢样，不抢占、不饿死其他订阅者。
# ============================================================================
class _FanOutSub:
    """fan-out ring 的一个订阅者：独立绝对读序号 read_seq。"""

    def __init__(self, name: str, ring: "_ThreadedRingReader"):
        self.name = name
        self._ring = ring
        self.read_seq = 0          # 下一个要读的绝对样点序号
        self.dropped = 0           # 因落后被丢弃的样点数（监控用）

    def available(self) -> int:
        r = self._ring
        with r._lock:
            avail = r._produced - self.read_seq
            if avail < 0:
                self.read_seq = r._produced
                return 0
            return int(avail)

    def clear(self):
        r = self._ring
        with r._lock:
            self.read_seq = r._produced

    def read(self, n: int, timeout: float = 1.0):
        n = int(n)
        if n <= 0:
            return np.empty(0, dtype=np.complex64)
        r = self._ring
        if n > r._ring_size:
            return None
        with r._lock:
            deadline = time.time() + timeout
            while True:
                # overrun 兜底（生产者通常已先跳）
                retain_start = r._produced - r._ring_size
                if self.read_seq < retain_start:
                    self.dropped += retain_start - self.read_seq
                    self.read_seq = retain_start
                avail = r._produced - self.read_seq
                if avail >= n:
                    break
                if r._stop:
                    return None
                now = time.time()
                if now >= deadline:
                    # 缓冲空直到超时：记一次真实欠读（不是丢包、也不是网络故障）。
                    # 注意此处已持有 r._lock（Condition 用的是同一把非可重入锁），
                    # 再嵌套 with r._lock 会自死锁，读侧永远等不到超时返回。
                    r._underruns += 1
                    return None
                r._cond.wait(timeout=max(0.001, deadline - now))
            R = r._ring_size
            out = np.empty(n, dtype=np.complex64)
            start = self.read_seq % R
            first = min(n, R - start)
            out[:first] = r._buf[start:start + first]
            if n > first:
                out[first:] = r._buf[0:n - first]
            self.read_seq += n
            return out


#: 读失败退避阶梯（秒）：连续失败次数 → 生产者线程的等待。
#: 5ms 只适合「偶尔抖一下」；拔出后 USB bulk 会一直抛错，5ms 一轮等于空转
#: （实测 183.6 次失败读/s、单核 0.3%）。≥200 次且链路已明确 disconnected
#: 说明设备真的不在了，拉到 500ms 等热插拔/重连把状态改回来。
#: 任一次成功读立刻回到 5ms（在 _produce_loop 里复位失败计数）。
def reader_backoff_s(failures: int, link_state: str = "") -> float:
    """连续读失败 ``failures`` 次后的等待秒数（纯函数，便于单测与反证）。"""
    if failures >= 200 and link_state == "disconnected":
        return 0.5
    if failures >= 20:
        return 0.05
    return 0.005


class _ThreadedRingReader:
    """单生产者 + 多订阅者环形缓冲。

    生产者线程只在一个线程上读设备并写入共享环形缓冲；每个订阅者持有独立的
    read_seq，互不抢占。订阅者落后超过 ring_size 时仅其自身游标跳到最新保留
    起点（独立丢样），不影响其他订阅者，也不阻塞生产者。
    """

    def __init__(self, read_fn, block_size: int = 8192,
                 ring_size: int = 1_000_000, link_state_fn=None):
        self._read_fn = read_fn
        self._block_size = int(block_size)
        self._ring_size = int(ring_size)
        #: 读失败退避阶梯要问「链路现在是什么状态」（见 reader_backoff_s）。
        #: None = 没人给状态，按「未断开」处理（不会升到 500ms）。
        self._link_state_fn = link_state_fn
        self._buf = np.zeros(self._ring_size, dtype=np.complex64)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._stop = False
        self._thread: Optional[threading.Thread] = None
        self._write_idx = 0       # 下一个写入位置（0..R-1）
        self._produced = 0        # 已写入的绝对样点总数
        self._subs: Dict[str, _FanOutSub] = {}
        # 丢样**只按订阅者各自记账**（各 _FanOutSub.dropped，见 dropped_total()）。
        # 不再保留 SDR++ ring_buffer.h:131-160 那套单消费者 _count/_dropped：
        # 它只跟默认 read() 记账，fan-out 订阅者读走的量不回填，会把整段流
        # 误报成丢弃（旧口径实例见 docs/hw-acceptance.md §6.2.2 判据 4）。
        # 读侧因缓冲空而超时返回 None 的次数（真实欠读，监控用）
        self._underruns = 0

    # -------------------------------------------------- 订阅
    def subscribe(self, name: str) -> _FanOutSub:
        with self._lock:
            sub = self._subs.get(name)
            if sub is None:
                sub = _FanOutSub(name, self)
                sub.read_seq = self._produced   # 新订阅者从当前开始，不回放历史
                self._subs[name] = sub
            return sub

    def subscriber_dropped(self) -> Dict[str, int]:
        with self._lock:
            return {k: s.dropped for k, s in self._subs.items()}

    # -------------------------------------------------- 生产者
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(
            target=self._produce_loop, name="mbdsdr-ring-producer", daemon=True)
        self._thread.start()

    def _produce_loop(self):
        failures = 0
        while not self._stop:
            try:
                chunk = self._read_fn(self._block_size)
            except Exception as e:
                if self._stop:
                    break
                failures += 1
                logger.warning(f"环形缓冲生产者读设备失败: {e}")
                self._sleep(reader_backoff_s(failures, self._link_state()))
                continue
            if chunk is None or len(chunk) == 0:
                if self._stop:
                    break
                failures += 1
                self._sleep(reader_backoff_s(failures, self._link_state()))
                continue
            # 任一次成功读立刻复位阶梯：设备回来了就不该继续按旧失败计数等待。
            failures = 0
            self._write(chunk)

    def _sleep(self, seconds: float) -> None:
        """退避等待（单测替换点：驱动测试时记录请求值而不真等）。"""
        time.sleep(seconds)

    def _link_state(self) -> str:
        """当前链路状态；拿不到时按「未断开」处理（不升级到 500ms 档）。"""
        fn = self._link_state_fn
        if fn is None:
            return ""
        try:
            return str(fn() or "")
        except Exception:
            return ""

    def _write(self, chunk: np.ndarray):
        n = len(chunk)
        with self._lock:
            R = self._ring_size
            new_produced = self._produced + n
            # 在覆盖旧区域前，把落后超过 R 的订阅者游标跳到保留起点（独立丢样）
            retain_start = new_produced - R
            for sub in self._subs.values():
                if sub.read_seq < retain_start:
                    sub.dropped += retain_start - sub.read_seq
                    sub.read_seq = retain_start
            first = min(n, R - self._write_idx)
            self._buf[self._write_idx:self._write_idx + first] = chunk[:first]
            if n > first:
                self._buf[0:n - first] = chunk[first:]
            self._write_idx = (self._write_idx + n) % R
            self._produced = new_produced
            self._cond.notify_all()

    # -------------------------------------------------- 默认订阅者（向后兼容）
    def _default_sub(self) -> _FanOutSub:
        return self.subscribe("default")

    def read(self, n: int, timeout: float = 1.0):
        return self._default_sub().read(n, timeout)

    def available(self) -> int:
        """环形缓冲的未读样点数（最慢订阅者视角）。

        **不再为状态查询创建隐式 "default" 订阅者**：那个消费者从不读，
        却会在写侧被当成落后订阅者持续累加 dropped，把状态读数变成假数字。
        无订阅者时返回当前保留量（min(produced, ring_size)），即整段都未读。
        """
        with self._lock:
            if self._subs:
                return int(max(0, self._produced
                               - min(s.read_seq for s in self._subs.values())))
            return int(min(self._produced, self._ring_size))

    def clear(self):
        """换频/换采样率：所有订阅者跳到当前，丢弃旧频数据。"""
        with self._lock:
            for sub in self._subs.values():
                sub.read_seq = self._produced
            # 各订阅者的历史 dropped 保留（监控用，不清零）。

    def producer_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def dropped_total(self) -> int:
        """各订阅者累计丢弃样点数之和（监控用，不清零）。

        口径固定为「订阅者视角的丢失量」：

        - 生产者覆盖旧区域前，落后超过 ring_size 的订阅者把自己的游标跳到
          保留起点，跳过的样点记进该订阅者的 ``dropped``；本值 = 各订阅者
          ``dropped`` 之和（同一样点被 N 个订阅者各跳过一次会计 N 次）。
        - 它**不表示设备少产了多少样点**；供给差看 ``_produced`` /
          ``underruns_total()``。
        - ``clear()`` 只把游标跳到当前，不重置历史计数。
        """
        with self._lock:
            return int(sum(s.dropped for s in self._subs.values()))

    def underruns_total(self) -> int:
        """累计读侧"缓冲空、等到超时"的次数（真实欠读，不清零）。"""
        with self._lock:
            return int(self._underruns)

    def capacity(self) -> int:
        """环形缓冲容量（样点）。"""
        return int(self._ring_size)

    def stop(self):
        self._stop = True
        with self._cond:
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            # join 超时 ≠ 线程已退出：保留引用，下一次 stop()/调用方还能 join
            # 收尸；置 None 会让越界线程永远无法 join，再 start() 还会多起一个
            # 生产者（start() 只在 _thread 为 None 时新建）。
            if not self._thread.is_alive():
                self._thread = None


class RTLSDRBackend(SDRBackend):
    """
    RTL-SDR 后端（RTL2832U + E4000/FC0012/FC0013/R820T/R820T2）。

    两种接入方式：
    - 本地 USB：RTLSDRBackend(device_index=0, ppm=...)，依赖 pyrtlsdr + librtlsdr。
    - rtl_tcp 网络：RTLSDRBackend(host="127.0.0.1", port=1234)，走 rtl_tcp 协议，
      适合本机装不上 librtlsdr / WOA / 远端共享棒 / 手机端共用一个前端。

    廉价棒晶振有 20~50ppm 频偏，务必用 ppm 校正（可由 AI 对照已知信标估计）。
    HF 短波（<24MHz）需要 direct sampling（RTL2832 直采）或外接上变频器。
    """

    # pyrtlsdr 调谐器枚举索引 → 名称
    # 来源 librtlsdr include/rtl-sdr.h enum rtlsdr_tuner：
    #   0 UNKNOWN, 1 E4000, 2 FC0012, 3 FC0013, 4 FC2580, 5 R820T, 6 R828D
    _TUNER_NAMES = {0: "Unknown", 1: "E4000", 2: "FC0012", 3: "FC0013",
                    4: "FC2580", 5: "R820T", 6: "R828D"}

    #: 调谐器实测可调上界（Hz）。公开规格表 FC0012/FC0013 写 948.6 MHz，
    #: 但本机 2026-11-06 实测：948.500 MHz 写频回读一致，948.600 MHz 起
    #: librtlsdr 报 ``[FC0012] no valid PLL combination found``。宁可按实测
    #: 上界提前拒绝，也不把请求下发给硬件——写频失败会让句柄进入不可恢复态。
    _MEASURED_TUNER_MAX_HZ = {"FC0012": 948_500_000}

    # 调谐器型号是设备的静态属性；每次热插拔轮询都 rtlsdr_open 一次在本机实测
    # 0.2–2.4 s（设备被占用时更长），2 s 一次的轮询会把 GUI 线程吃满。
    # 缓存命中后不再打开设备；设备数变化（插拔）时整表失效。
    _TUNER_PROBE_CACHE: Dict[tuple, tuple] = {}
    _TUNER_PROBE_TTL_S = 30.0       # 成功探测结果的有效期
    _TUNER_PROBE_FAIL_TTL_S = 5.0   # 打不开（被占用）时避免每次重试的冷却期
    _LAST_DEVICE_COUNT: Optional[int] = None

    @staticmethod
    def _probe_tuner_type(index, serial: str = "") -> int:
        # 通过 ctypes 打开设备读取调谐器枚举值（0=Unknown）。
        # pyrtlsdr 0.2.93 的 `from rtlsdr import RtlSdr` 在 AIO 可用时解析为
        # RtlSdrAio，该类没有 tuner_type；便捷函数 rtlsdr_get_tuner_type(by index)
        # 在部分 DLL 构建里恒返回 0。直接 rtlsdr_open 拿句柄再查
        # rtlsdr_get_tuner_type(handle) 可靠（实测 FC0012 返回 2）。
        key = (int(index), str(serial or ""))
        ent = RTLSDRBackend._TUNER_PROBE_CACHE.get(key)
        now = time.monotonic()
        if ent is not None:
            tcode, ts = ent
            if tcode != 0 and (now - ts) < RTLSDRBackend._TUNER_PROBE_TTL_S:
                return tcode
            if tcode == 0 and (now - ts) < RTLSDRBackend._TUNER_PROBE_FAIL_TTL_S:
                return 0
        try:
            import ctypes
            import rtlsdr as _r
            lib = _r.librtlsdr
            dev = ctypes.c_void_p()
            if int(lib.rtlsdr_open(ctypes.byref(dev), int(index))) != 0:
                RTLSDRBackend._TUNER_PROBE_CACHE[key] = (0, now)
                return 0
            try:
                tcode = int(lib.rtlsdr_get_tuner_type(dev))
            finally:
                lib.rtlsdr_close(dev)
            RTLSDRBackend._TUNER_PROBE_CACHE[key] = (tcode, now)
            return tcode
        except Exception:
            RTLSDRBackend._TUNER_PROBE_CACHE[key] = (0, now)
            return 0

    # 来源: librtlsdr src/librtlsdr.c:1100-1104 — 合法采样率区间为
    # (225000, 300000] ∪ (900000, 3200000]，300k~900k 是死区（库直接返回 -EINVAL）。
    # 常用档：低段 rtl_433 默认 250000（rtl_433/include/rtl_433.h:13），
    # 高段常用 1000000/1024000/2400000。
    _RTL_LOW_MAX = 300_000      # 低段上沿（合法）
    _RTL_HIGH_MIN = 900_000     # 死区上沿（900000 本身非法，>900000 才合法）
    _RTL_ABS_MAX = 3_200_000
    _RTL_PREFERRED_LOW = 250_000   # rtl_433 默认
    _RTL_PREFERRED_HIGH = 1_000_000  # 高段常用起点

    # 来源: SDR++ source_modules/rtl_sdr_source/src/main.cpp:27-39 ——
    # rtl_sdr_source 模块硬编码的 11 个离散采样率档（用户只能在这 11 档里选）。
    # 对照 main.cpp:377-385 的 srId 下拉框：选哪档就 set 哪档，不做连续区间映射。
    SAMPLE_RATES = [
        250_000,      # main.cpp:28
        1_024_000,    # main.cpp:29
        1_536_000,    # main.cpp:30
        1_792_000,    # main.cpp:31
        1_920_000,    # main.cpp:32
        2_048_000,    # main.cpp:33
        2_160_000,    # main.cpp:34
        2_400_000,    # main.cpp:35（SDR++ 新设备默认 main.cpp:202）
        2_560_000,    # main.cpp:36
        2_880_000,    # main.cpp:37
        3_200_000,    # main.cpp:38
    ]

    # 默认采样率：项目统一首选 2.4 MS/s（模块级 DEFAULT_SAMPLE_RATE_HZ）。
    # RTL 全系支持 2.4M 档，通常不做回落；若设备能力表不含该档，connect()
    # 经 nearest_sample_rate() 取最接近支持值并回报实际值。类属性保留以兼容。
    DEFAULT_SAMPLE_RATE = DEFAULT_SAMPLE_RATE_HZ

    @classmethod
    def nearest_sample_rate(cls, rate_hz: float) -> int:
        """把任意请求采样率吸附到 SDR++ main.cpp:27-39 定义的最近离散档。

        对照 SDR++ main.cpp:377-385：用户只能从 11 档里选，不存在"连续采样率"。
        用最近邻（欧氏距离，平局取低档）吸附，再交给底层 _clamp_sample_rate
        做死区/越界安全检查。
        """
        return min(cls.SAMPLE_RATES, key=lambda s: (abs(s - float(rate_hz)), s))

    @classmethod
    def _clamp_sample_rate(cls, rate_hz: float) -> Tuple[float, bool]:
        """把请求采样率钳位到 librtlsdr 合法区间。

        合法: (225000, 300000] ∪ (900000, 3200000]。
        死区 (300000, 900000] 内的值按中点 600k 二分：偏下钳到 250k，偏上钳到 1M。
        低于 225k 钳到 250k；高于 3.2M 钳到 3.2M。

        返回 (clamped_rate, was_clamped)。
        """
        r = float(rate_hz)
        # 来源: librtlsdr src/librtlsdr.c:1100-1104 — 区间判定
        low_ok = (225_000 < r <= cls._RTL_LOW_MAX)
        high_ok = (cls._RTL_HIGH_MIN < r <= cls._RTL_ABS_MAX)
        if low_ok or high_ok:
            return r, False
        # 死区或越界：钳位到最近常用合法档
        if r <= 225_000:
            return float(cls._RTL_PREFERRED_LOW), True
        if r > cls._RTL_ABS_MAX:
            return float(cls._RTL_ABS_MAX), True
        # 死区 (300000, 900000]：以 600k 为界偏下偏上
        if r < 600_000:
            return float(cls._RTL_PREFERRED_LOW), True
        return float(cls._RTL_PREFERRED_HIGH), True

    def __init__(self, device_index: int = 0, host: Optional[str] = None,
                 port: int = 1234, ppm: int = 0):
        device = SDRDevice(
            device_type="rtl_sdr",
            device_id=f"rtl_{device_index}" if host is None else f"rtl_tcp_{host}_{port}",
            name=f"RTL-SDR #{device_index}" if host is None else f"rtl_tcp {host}:{port}",
            frequency_range=(500000, 1766000000),  # direct sampling 可下探至 ~0.5MHz
            # 来源: librtlsdr src/librtlsdr.c:1100-1104 — 声明为两段合法区间而非连续范围
            sample_rate_range=(225_001, 3_200_000),
            max_gain=49.6,
            supports_iq=True,
            supports_tx=False,
            # 范围/增益来自 librtlsdr 与调谐器参数表（厂商文档常量），
            # 不是逐台探测 → declared（界面标「声明支持，未验证」）。
            capability_status={
                "freq_range": "declared",
                "sample_rate_range": "declared",
                "gain_range": "declared",
                "tx": "declared",
            },
            capability_note={
                "freq_range": "RTL 调谐器覆盖范围来自驱动/调谐器参数表",
                "sample_rate_range": "librtlsdr 合法区间（驱动常量）",
            },
        )
        super().__init__(device)
        # 默认采样率：2.400 MS/s（模块级 DEFAULT_SAMPLE_RATE_HZ，见类常量
        # DEFAULT_SAMPLE_RATE 注释）；该档在 SDR++ main.cpp:27-39 的离散档内。
        # connect() 后 _apply_sample_rate 会真正下发硬件。
        self.status.sample_rate_hz = float(self.DEFAULT_SAMPLE_RATE)
        self._device_index = device_index
        self._host = host
        self._port = port
        self._ppm = ppm
        self._sdr = None
        self._direct = 0  # 0=off, 1=I, 2=Q
        self.tuner_name = "Unknown"
        # 调谐器写频失败后句柄可能已不可再用（实测：失败后回读恒 0，再写触发
        # access violation）。置位后拒绝继续写硬件，必须重连换新句柄。
        self._tuner_faulted = False
        # 来源: SDR++ main.cpp:485-494 offsetTuning checkbox —— 默认 False，
        # 开启后 RTL2832 把本振偏移到采样率中央，便于直流抵消。
        self._offset_tuning = False
        # 来源: SDR++ ring_buffer.h:4 RING_BUF_SZ=1000000 +
        # main.cpp:526-539 worker()/asyncHandler() —— 生产者线程持续读设备写环形缓冲，
        # 消费者按帧取数。connect() 成功后创建并 start。
        self._ring_reader: Optional["_ThreadedRingReader"] = None
        # 来源: librtlsdr src/librtlsdr.c:959-969 —— 真实离散增益表在
        # mbdsdr_ai/rtlsdr_params.py，连上探测到调谐器型号后填充。
        self._gain_table_db: list = []
        # 最近一次真正写进调谐器的增益（dB）。本地 USB 由 pyrtlsdr 的
        # ``gain`` getter 回读确认；rtl_tcp 无回执，只记下发值。
        # ``set_gain`` 成功后用它覆盖基类写入的请求值。
        self._applied_gain_db: Optional[float] = None
        # 本地 USB 直连不可用时，自动拉起的 rtl_tcp 子进程句柄（disconnect 时回收，
        # 绝不留下野进程占着棒子——rtl_tcp 单客户端，占着别人就枚举不到）。
        self._rtl_tcp_proc = None

    @staticmethod
    def list_devices() -> list:
        """枚举本机 USB RTL-SDR，返回 [{index, serial, tuner}]，无库/无设备返回 []。"""
        try:
            # Windows: guide rtlsdr/libusb DLLs (incl. SDR++/SDR# extracted dirs)
            try:
                from .windows_setup import setup_rtlsdr_windows
                setup_rtlsdr_windows(import_test=False)
            except Exception:
                pass
            import rtlsdr as _rtlsdr
            from rtlsdr import RtlSdr
            out = []
            # pyrtlsdr 0.2.93 has no get_device_count(); use the ctypes binding
            n = int(_rtlsdr.librtlsdr.rtlsdr_get_device_count())
            # 设备数变化 = 插拔发生过，调谐器缓存整表失效（不拿旧设备的型号冒充新设备）。
            if RTLSDRBackend._LAST_DEVICE_COUNT != n:
                RTLSDRBackend._TUNER_PROBE_CACHE.clear()
                RTLSDRBackend._LAST_DEVICE_COUNT = n
            try:
                serials = RtlSdr.get_device_serial_addresses()  # list, not dict
            except Exception:
                serials = []
            for i in range(n):
                serial = serials[i] if i < len(serials) else ""
                tcode = RTLSDRBackend._probe_tuner_type(i, serial)
                tuner = RTLSDRBackend._TUNER_NAMES.get(tcode, "Unknown")
                out.append({"index": i, "serial": serial, "tuner": tuner})
            return out
        except ImportError as e:
            # 缺 pyrtlsdr 库：必须 log 出来，不能和"无设备"静默混为一谈
            logger.warning("pyrtlsdr 未安装，无法枚举 RTL-SDR 设备: %s", e)
            return []
        except Exception as e:
            # pyrtlsdr 可能在 import 期就炸（本机实测：librtlsdr.dll 未导出
            # rtlsdr_set_dithering，pyrtlsdr 的 librtlsdr.py:147 无条件解析该符号
            # → AttributeError，整个包不可用）。这既不是"未安装"也不是"无设备"，
            # 不能和空设备混为一谈：用仓库自有 ctypes 绑定再试一次。
            _warn_once(
                "pyrtlsdr_unusable",
                "pyrtlsdr 不可用（%s），回退仓库自有 ctypes 枚举", e)
            return RTLSDRBackend._enumerate_via_ctypes()

    @staticmethod
    def _enumerate_via_ctypes() -> list:
        """pyrtlsdr 不可用时的回退枚举：只调用确实存在的 C API 符号。

        背景（本机真机实测，非推测）：pyrtlsdr 的 ``librtlsdr.py:147`` 在 import
        期无条件解析 ``rtlsdr_set_dithering``；本机所有可用 librtlsdr.dll 构建
        （``C:\\rtl-sdr\\bin``、源码自编版、rtl-sdr-64bit-20260920 发行版）均
        未导出该符号，导致 ``import rtlsdr`` 直接抛 AttributeError，pyrtlsdr
        整个包不可用。此时若沿用旧逻辑直接返回 ``[]``，会把"棒子插着且
        rtl_test 能认出来"误报成"本机无设备"——属实锤的功能缺口。

        本方法用仓库自有 ctypes 绑定绕开该符号，只依赖确实存在的接口：
        ``rtlsdr_get_device_count`` / ``rtlsdr_get_device_usb_strings`` /
        ``rtlsdr_open`` / ``rtlsdr_get_tuner_type`` / ``rtlsdr_close``。

        诚实纪律：拿不到库、读不到设备、探测失败一律如实返回空或 Unknown，
        绝不伪造条目，也绝不把"枚举失败"粉饰成"无信号"。
        """
        import ctypes

        # 先让 os.add_dll_directory 生效（Py3.8+ 不再从 PATH 搜 DLL）
        try:
            from .windows_setup import setup_rtlsdr_windows
            setup_rtlsdr_windows(import_test=False)
        except Exception:
            pass

        lib = None
        for name in ("rtlsdr.dll", "librtlsdr.dll"):
            try:
                lib = ctypes.CDLL(name)
                break
            except OSError:
                continue
        if lib is None:
            _warn_once("rtlsdr_dll_missing",
                       "未找到 rtlsdr.dll/librtlsdr.dll，无法回退枚举")
            return []

        try:
            lib.rtlsdr_get_device_count.restype = ctypes.c_uint32
            count = int(lib.rtlsdr_get_device_count())
        except Exception as e:
            logger.warning("回退枚举：读取设备数失败: %s", e)
            return []
        if count <= 0:
            # 真的没有设备——这是诚实的空态，不是失败
            return []

        try:
            lib.rtlsdr_get_device_usb_strings.argtypes = [
                ctypes.c_uint32, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p]
            lib.rtlsdr_get_device_usb_strings.restype = ctypes.c_int
        except Exception:
            pass

        out = []
        for i in range(count):
            serial = ""
            try:
                manufact = ctypes.create_string_buffer(256)
                product = ctypes.create_string_buffer(256)
                serial_buf = ctypes.create_string_buffer(256)
                if lib.rtlsdr_get_device_usb_strings(
                        i, manufact, product, serial_buf) == 0:
                    serial = serial_buf.value.decode("ascii", "replace").strip()
            except Exception:
                serial = ""

            # 开设备读真实调谐器型号（FC0012 等）；打不开就如实 Unknown
            tuner = "Unknown"
            try:
                dev = ctypes.c_void_p()
                if int(lib.rtlsdr_open(ctypes.byref(dev), int(i))) == 0:
                    try:
                        tuner = RTLSDRBackend._TUNER_NAMES.get(
                            int(lib.rtlsdr_get_tuner_type(dev)), "Unknown")
                    finally:
                        lib.rtlsdr_close(dev)
            except Exception:
                tuner = "Unknown"

            out.append({"index": i, "serial": serial, "tuner": tuner})
        return out

    @staticmethod
    def _find_rtl_tcp_exe():
        """定位 rtl_tcp 可执行文件；找不到返回 None。

        安全红线：只做本地搜索，**绝不自动下载**二进制（供应链风险，
        与 windows_setup.py 的口径一致）。
        """
        import shutil

        p = shutil.which("rtl_tcp") or shutil.which("rtl_tcp.exe")
        if p:
            return p
        cands = [os.path.join(r"C:\rtl-sdr", "bin", "rtl_tcp.exe")]
        try:
            from .windows_setup import default_candidate_dirs
            for d in default_candidate_dirs():
                cands.append(os.path.join(d, "rtl_tcp.exe"))
        except Exception:
            pass
        for c in cands:
            if os.path.isfile(c):
                return c
        return None

    def _try_start_local_rtl_tcp(self) -> bool:
        """本地 USB 直连不可用时，拉起 rtl_tcp 并改走仓库自研的 TCP 客户端。

        触发前提：本机 pyrtlsdr 不可用（librtlsdr.dll 缺 rtlsdr_set_dithering，
        见 _enumerate_via_ctypes 注释）。此时唯一能真实拿到 IQ 的路径就是
        rtl_tcp + 本仓 MIT 的 RtlTcpClient —— 也正是 runbook §3.4–3.5
        记载的受支持接法。

        诚实纪律：
        - 找不到二进制 / 起不来 / 连不上，一律回收子进程并返回 False，
          **绝不伪造连接成功**；
        - 进程句柄存 self._rtl_tcp_proc，由 disconnect() 回收；
        - 端口动态选取，避免撞上用户已在跑的 rtl_tcp(1234)。
        """
        import socket
        import subprocess

        exe = self._find_rtl_tcp_exe()
        if not exe:
            return False

        try:
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
            probe.close()
        except Exception:
            port = 1234

        try:
            proc = subprocess.Popen(
                [exe, "-a", "127.0.0.1", "-p", str(port)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.warning(f"拉起 rtl_tcp 失败: {e}")
            return False

        # 注意（真机踩坑，非推测）：**不能**用试探性 socket 连接做就绪探测。
        # rtl_tcp 是单客户端协议，探测那一次会吃掉唯一的客户端名额，随后真实
        # 连接直接被对端关闭（实测报"设备信息读取不完整（连接被对端关闭）"）。
        # 所以这里不做探测，直接重试**真实的**适配器连接。
        from .rtltcp_client import RtlTcpDeviceAdapter

        adapter = None
        last_err = None
        for _attempt in range(6):
            if proc.poll() is not None:
                break
            try:
                adapter = RtlTcpDeviceAdapter(
                    host="127.0.0.1", port=port, ppm=self._ppm)
                break
            except Exception as e:
                last_err = e
                time.sleep(0.5)
        if adapter is None:
            try:
                proc.terminate()
            except Exception:
                pass
            logger.warning("rtl_tcp 兜底连接失败（%s），已回收子进程", last_err)
            return False
        self._sdr = adapter

        self._rtl_tcp_proc = proc
        self._host, self._port = "127.0.0.1", port
        logger.warning(
            "本地 USB 直连不可用，已自动拉起 rtl_tcp(127.0.0.1:%d) 并改走仓库"
            "自研 TCP 客户端；disconnect 时回收该进程。", port)
        return True

    def connect(self) -> bool:
        # 重连/热插拔前先干净释放旧句柄与旧生产者线程：绝不带着已失效的
        # usb 句柄去 open 新设备（否则会出现"重插后连上但收不到数据"的假状态）。
        if self._ring_reader is not None or self._sdr is not None:
            try:
                self.disconnect()
            except Exception as _e:
                logger.debug("重连前释放旧句柄失败(忽略): %s", _e)
        try:
            if self._host is not None:
                # rtl_tcp 网络模式统一走本仓库自研、MIT、无 GPL 的 RtlTcpClient
                # （rtltcp_client.py），不再依赖 pyrtlsdr 的 RtlSdrTcpClient。
                # RtlTcpDeviceAdapter 把它适配成 pyrtlsdr RtlSdr 风格对象，使下面的
                # SDR++ 初始化序列无需分支即可工作。
                from .rtltcp_client import RtlTcpDeviceAdapter
                self._sdr = RtlTcpDeviceAdapter(
                    host=self._host, port=self._port, ppm=self._ppm)
            else:
                try:
                    from .windows_setup import setup_rtlsdr_windows
                    setup_rtlsdr_windows(import_test=False)
                except Exception:
                    pass
                try:
                    from rtlsdr import RtlSdr
                except Exception as e:
                    # 本机实测（非推测）：librtlsdr.dll 未导出 rtlsdr_set_dithering，
                    # pyrtlsdr 的 librtlsdr.py:147 在 import 期即失败，本地 USB 直连
                    # 这条路径在本机不可用。必须如实告知并指明可用的替代路径，
                    # 不能把裸 AttributeError 甩给用户当"连接失败"。
                    # 先试着自动拉起本地 rtl_tcp 走仓库自研 TCP 客户端兜底；
                    # 兜底也失败，才把可执行指引抛给用户（绝不裸抛 AttributeError）。
                    if not self._try_start_local_rtl_tcp():
                        raise RuntimeError(
                            "本地 USB 直连不可用：pyrtlsdr 无法导入（%s）。"
                            "本机 librtlsdr.dll 缺少 rtlsdr_set_dithering 符号，"
                            "pyrtlsdr 整个包不可用；且未能自动拉起 rtl_tcp 兜底。"
                            "请手动启动 rtl_tcp 服务，再设 "
                            "MBDSDR_RTL_TCP=127.0.0.1:1234 重连。" % e) from e
                else:
                    # 调谐器型号必须在 pyrtlsdr 打开设备之前探测：本进程已持有
                    # 该设备句柄时，ctypes 再 rtlsdr_open 会失败、tuner 恒 Unknown，
                    # 频率上界协商就失去依据（2026-11-06 实测：先开后探 → Unknown；
                    # 先探后开 → FC0012）。
                    tcode_hint = self._probe_tuner_type(self._device_index)
                    self._sdr = RtlSdr(self._device_index)
            # 调谐器型号：本地 USB 用上面打开前探测到的结果；rtl_tcp 网络模式
            # 用握手包带回的 tuner_type（rtl_tcp.c:623），不再开本地 USB 棒。
            if self._host is not None:
                tcode = int(getattr(self._sdr, "tuner_type", 0) or 0)
            else:
                tcode = tcode_hint
            self.tuner_name = self._TUNER_NAMES.get(tcode, "Unknown")
            # 来源: librtlsdr src/librtlsdr.c:956-1008 rtlsdr_get_tuner_gains ——
            # 按探测到的调谐器型号载入真实离散增益表（dB）。
            try:
                from . import rtlsdr_params as _rp
                self._gain_table_db = _rp.get_gain_table(self.tuner_name)
            except Exception:
                self._gain_table_db = []
            self.status.connected = True
            self.status.link_state = "connected"
            self.status.error_category = ""
            self.status.error = ""
            # 新句柄 = 新硬件会话：上次写频失败留下的故障态清零。
            self._tuner_faulted = False
            self.status.last_data_age_s = None
            self._start_time = time.time()
            # 兜底：若子类未显式设采样率，用 2.048M 兜底（见 SDRBackend._FALLBACK_SAMPLE_RATE_HZ）
            if self.status.sample_rate_hz is None:
                self.status.sample_rate_hz = self._FALLBACK_SAMPLE_RATE_HZ

            # ═══════════════════════════════════════════════════════════════
            # SDR++ 对齐：打开设备后、启动采数前，必须逐项显式下发硬件设置。
            # 对照 repos/sdrpp/source_modules/rtl_sdr_source/src/main.cpp:307-322
            # start() 函数——上一版 connect() 只 readback 不下发，导致棒停在
            # 出厂默认频率/采样率（"插上收不到台"的根因）。
            # ═══════════════════════════════════════════════════════════════

            # 1) main.cpp:307 rtlsdr_set_sample_rate —— 先设采样率（SDR++ 顺序第一）
            self.set_sample_rate(self.status.sample_rate_hz)

            # 2) main.cpp:308 rtlsdr_set_center_freq —— 再设中心频率。
            #    SDRStatus 默认 98 MHz（FM 广播段），用户可在 connect 前通过
            #    status.frequency_hz 预设；绝不能留 0 或出厂残留。
            target_freq = self.status.frequency_hz or 98_000_000.0
            self.set_frequency(target_freq)

            # 3) main.cpp:309 rtlsdr_set_freq_correction —— 始终下发 ppm（含 0），
            #    避免上一次运行的频偏校正残留在棒上。
            self.set_ppm(self._ppm)

            # 4) main.cpp:310 rtlsdr_set_tuner_bandwidth(openDev, 0) —— 0=自动，
            #    由 librtlsdr 按采样率选最接近的模拟前端滤波器带宽。
            self.set_bandwidth(0)

            # 5) main.cpp:311 rtlsdr_set_direct_sampling —— 默认关闭（非 HF 模式）
            self.set_direct_sampling("off")

            # 6) main.cpp:312 rtlsdr_set_bias_tee —— 默认关闭（不给有源天线供电）
            self.set_bias_tee(False)

            # 7) main.cpp:313 rtlsdr_set_agc_mode + main.cpp:319
            #    rtlsdr_set_tuner_gain_mode(1) —— RTL2832 数字 AGC 关闭，
            #    调谐器切手动增益模式（SDR++ 默认 rtlAgc=false, tunerAgc=false）。
            self.set_agc(False)

            # 8) main.cpp:314/320 rtlsdr_set_tuner_gain —— 手动增益模式下必须显式
            #    下发增益值。首启时拉到增益表中点（来源 gqrx mainwindow.cpp:571-579，
            #    比 SDR++ 默认最低增益更适合"插上就有台"的用户体验）。
            self._maybe_apply_first_gain_midpoint()

            # 9) main.cpp:322 rtlsdr_set_offset_tuning —— 默认关闭；用户预开启时下发
            #    （驱动不提供该方法时跳过，见 set_offset_tuning 的能力探测说明）
            if self._offset_tuning and hasattr(self._sdr, "set_offset_tuning"):
                try:
                    self._sdr.set_offset_tuning(True)
                except Exception as e:
                    logger.warning(f"RTL-SDR 设置 offset_tuning 失败: {e}")

            # 回读硬件实际参数，验证上面的下发真的生效（而非静默失败）
            try:
                self.readback_hw_state()
            except Exception as e:
                logger.warning(f"RTL-SDR 回读失败: {e}")
            # 来源: SDR++ main.cpp:326 workerThread + 526-539 worker()/asyncHandler()
            # + ring_buffer.h:4 RING_BUF_SZ=1000000 —— 启动生产者线程持续读设备写环形缓冲，
            # 替代 QTimer 50ms 只读 4096 样点造成的严重欠读。
            try:
                self._ring_reader = _ThreadedRingReader(
                    read_fn=self._ring_read,
                    block_size=8192,
                    ring_size=1_000_000,
                    # 退避阶梯的最后一档要看链路是否已明确断开（见 reader_backoff_s）。
                    link_state_fn=lambda: self.status.link_state,
                )
                self._ring_reader.start()
            except Exception as e:
                logger.warning(f"RTL-SDR 启动环形缓冲生产者失败: {e}")
                self._ring_reader = None
            return True
        except Exception as e:
            # 绝不静默吞异常：把真实原因写进 status.error，UI（main_window.py）会读到并展示。
            # 无 pyrtlsdr / USB 权限 / 设备被占用等都要冒到用户，绝不伪造 IQ 假装已连接。
            self.status.connected = False
            self.status.link_state = "failed"
            msg = str(e)
            low = msg.lower()
            if isinstance(e, ImportError) and ("rtlsdr" in low or "no module named" in low):
                self.status.error = (
                    "未安装 pyrtlsdr（可选 GPL-3.0 原生 USB 后端）。本机 RTL-SDR USB "
                    "默认请用 SoapySDR：pip install SoapySDR 并装 SoapyRTLSDR 驱动；"
                    "或用 rtl_tcp 网络源（自研客户端，无需本机 librtlsdr）。"
                    "若坚持走 pyrtlsdr：pip install pyrtlsdr（Windows 还需 librtlsdr DLL）"
                )
                self.status.error_category = "driver"
            else:
                # 归类：占用/权限（含 librtlsdr "usb_open error -3"）与"设备不在"必须
                # 给用户不同结论——"被占用"是可操作的，"找不到设备"是另一码事。
                try:
                    from .error_handler import (
                        DeviceBusyError,
                        DeviceNotFoundError,
                        classify_connect_failure,
                        probe_occupant,
                    )
                    user_err = classify_connect_failure(e, msg)
                    self.status.error_category = getattr(user_err, "category", "unknown")
                    if isinstance(user_err, DeviceBusyError):
                        occ = probe_occupant()  # 不主动扫进程：探测不到就如实说探测不到
                        self.status.error = (
                            f"设备被占用: {msg}；{occ['reason']}"
                            if not occ.get("detected")
                            else f"设备被占用: {msg}；疑似占用进程: {', '.join(occ['processes'])}"
                        )
                    elif isinstance(user_err, DeviceNotFoundError):
                        self.status.error = f"未检测到 SDR 设备: {msg}"
                    else:
                        self.status.error = f"RTL-SDR 连接失败: {type(e).__name__}: {msg}"
                except Exception:
                    self.status.error = f"RTL-SDR 连接失败: {type(e).__name__}: {msg}"
            logger.warning(
                "RTL-SDR 连接失败: %s: %s", type(e).__name__, e, exc_info=True
            )
            # 诚实释放：连接流程中任何一步失败都可能已经 open 了设备/拉了 rtl_tcp
            # 子进程。不清理会留下野句柄，导致"重新插入后仍显示被占用"。
            try:
                self.disconnect()
            except Exception as _ce:
                logger.debug("连接失败后释放句柄出错(忽略): %s", _ce)
            self.status.connected = False
            self.status.link_state = "failed"
            return False

    # ── 生产者读设备：把底层故障如实翻译成链路状态 ──────────────────────
    def _ring_read(self, num_samples: int) -> Optional[np.ndarray]:
        """环形缓冲生产者专用读函数。

        与直接暴露 ``self._sdr.read_samples`` 的区别：任何"设备没了/连接断了"
        都在这里被记账（status.connected / link_state / error / error_category），
        重连管理器与 UI 才能看到真实状态，而不是永远显示"已连接"。
        """
        if self._sdr is None:
            return None
        try:
            out = self._sdr.read_samples(num_samples)
        except Exception as e:
            self._mark_link_lost(
                "disconnected", f"读设备失败: {type(e).__name__}: {e}")
            return None
        if out is None or len(out) == 0:
            # rtl_tcp 适配器能给出更细的状态（stale=连着但无数据 / disconnected=真断）
            health = None
            health_fn = getattr(self._sdr, "data_health", None)
            if callable(health_fn):
                try:
                    health = health_fn()
                except Exception:
                    health = None
            if health and health.get("state") == "stale":
                self.status.link_state = "stale"
                self.status.error_category = health.get("category", "idle")
                self.status.error = health.get("message", "")
                self.status.last_data_age_s = health.get("last_data_age_s")
                # socket 仍然连着：不翻 connected，只显式降级供 UI 提示
            else:
                self._mark_link_lost("disconnected", "读设备失败: 无数据（设备拔出或连接断开）")
            return None
        # 真实数据到达：清掉上轮链路错误
        if self.status.link_state in ("stale", "disconnected", "failed"):
            self.status.link_state = "connected"
            self.status.error = ""
            self.status.error_category = ""
        self._last_ring_data_time = time.time()
        self.status.last_data_age_s = 0.0
        return out

    def _mark_link_lost(self, category: str, message: str) -> None:
        """设备/链路真实丢失：标记断开并保留可区分类别，绝不继续假装在线。

        只在**状态转移**时写一次 error/category：设备拔出后每轮读都会失败，
        每次都重写等于把状态栏/日志刷屏，且后续失败文本会覆盖首因。已经
        disconnected 时只维持 connected=False，等成功读或重连把它转回去。
        """
        if self.status.link_state == "disconnected":
            self.status.connected = False
            return
        self.status.connected = False
        self.status.link_state = "disconnected"
        self.status.error_category = category
        self.status.error = message

    def data_health(self) -> dict:
        """可序列化的数据流健康度（本地 USB + rtl_tcp 兜底路径统一入口）。"""
        st = self.get_status()
        out = {
            "backend": type(self).__name__,
            "state": st.link_state or ("connected" if st.connected else "disconnected"),
            "connected": bool(st.connected),
            "category": st.error_category,
            "message": st.error,
            "samples_total": int(st.samples_read),
            "dropped_samples": int(st.dropped_samples),
            "drop_ratio": (
                st.dropped_samples / max(1, st.samples_read + st.dropped_samples)
            ),
            "buffer_fill": int(st.buffer_fill),
            "buffer_capacity": int(st.buffer_capacity),
            "buffer_level": st.buffer_level,
            "underruns": int(st.underruns),
            "last_data_age_s": st.last_data_age_s,
            "expected_rate_hz": st.sample_rate_hz,
        }
        adapter = self._sdr
        health_fn = getattr(adapter, "data_health", None)
        if callable(health_fn):
            try:
                extra = health_fn()
                for k in ("bytes_total", "gap_events", "reconnect_count",
                          "retune_events", "observed_rate_hz", "rate_ratio",
                          "suggestion"):
                    if k in extra:
                        out[k] = extra[k]
                out["observer"] = "rtl_tcp"
            except Exception:
                pass
        return out

    def disconnect(self):
        # 对照 SDR++ main.cpp:332-342 stop()：先停生产者线程（置标志+notify+join），
        # 再关设备（close 会解除生产者阻塞在 read_samples 上的等待）。
        if self._ring_reader is not None:
            try:
                self._ring_reader.stop()
            except Exception:
                pass
            self._ring_reader = None
        if self._sdr:
            try:
                self._sdr.close()
            except Exception:
                pass
            self._sdr = None
        # 回收自动拉起的 rtl_tcp。rtl_tcp 是单客户端且独占 USB 设备，
        # 留着会把棒子锁死——其它程序（含本应用下次枚举）都再也看不到设备。
        if self._rtl_tcp_proc is not None:
            try:
                self._rtl_tcp_proc.terminate()
            except Exception:
                pass
            try:
                self._rtl_tcp_proc.wait(timeout=3)
            except Exception:
                try:
                    self._rtl_tcp_proc.kill()
                except Exception:
                    pass
            self._rtl_tcp_proc = None
            # 恢复成"本地 USB"语义：下次 connect 重新走直连→兜底判定
            self._host, self._port = None, 1234
        # 清掉"最后一次数据"时间戳，避免断开后 last_data_age_s 继续虚长
        self._last_ring_data_time = None
        self.status.buffer_fill = 0
        self.status.buffer_level = ""
        super().disconnect()

    def _frequency_bounds(self) -> Tuple[float, float]:
        """按真实调谐器收窄声明区间：声明区间 (0.5–1766 MHz) 是 RTL2832
        芯片口径，实际能不能到取决于焊的 tuner 型号与实测上界。"""
        lo, hi = super()._frequency_bounds()
        if self._direct:
            # 直接采样绕过 tuner，tuner 频率表不适用。
            return lo, hi
        try:
            from . import rtlsdr_params as _rp
            tlo, thi = _rp.get_frequency_range(self.tuner_name)
        except Exception:
            return lo, hi
        if thi <= 0.0:
            # 型号未知（探测失败）：不替它编造范围，只用设备声明区间。
            return lo, hi
        measured = self._MEASURED_TUNER_MAX_HZ.get(self.tuner_name)
        if measured is not None:
            thi = min(thi, float(measured))
        return max(lo, tlo), min(hi, thi)

    def _front_end_label(self) -> str:
        name = str(getattr(self, "tuner_name", "") or "")
        if name and name != "Unknown":
            return f"调谐器 {name} "
        return "设备"

    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._sdr is None:
            return True
        if self._tuner_faulted:
            # 上次写频失败后句柄已不可恢复：继续写只会触发原生层 access
            # violation（实测随后整个进程 abort）。必须重连换新句柄。
            self.status.error = user_message(
                "sdr_set_frequency_failed",
                detail="调谐器已进入故障态（上次写频失败后句柄不可恢复），"
                       "需重新连接设备后才能继续调谐")
            logger.warning(
                "调谐器故障态：拒绝继续写频 %d Hz（需重连）", int(freq_hz))
            return False
        # 来源: SDR++ main.cpp:344-359 tune() —— set 后回读验证，最多重试 3 次
        # （SDR++ 原版 main.cpp:349-352 重试 10 次直到回读匹配；我们 3 次足够）。
        target = int(freq_hz)
        matched = False
        last_error = ""
        for attempt in range(1, 4):
            try:
                self._sdr.center_freq = target
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(f"RTL-SDR set_center_freq 第{attempt}次失败: {e}")
                continue
            try:
                actual = int(self._sdr.center_freq)
            except Exception:
                # 回读不可用，直接认为成功（pyrtlsdr 某些后端不支持回读）
                matched = True
                break
            if actual == target:
                matched = True
                break
            last_error = f"回读 {actual} Hz ≠ 请求 {target} Hz"
            logger.warning(
                f"RTL-SDR 调谐第{attempt}次: 请求 {target}Hz, 回读 {actual}Hz, 重试")
        if not matched:
            # 重试耗尽 = 硬件确实没生效。绝不 return True 把失败装成成功。
            self._tuner_faulted = True
            self.status.error = user_message(
                "sdr_set_frequency_failed",
                detail=(f"调谐器未生效（请求 {target / 1e6:.3f} MHz"
                        + (f"；{last_error}" if last_error else "")
                        + "）；句柄可能已不可恢复，需重新连接设备"),
            )
            logger.warning(f"RTL-SDR 调谐 {target}Hz 重试3次仍未生效，已标记故障态")
            # 故障态下不再调原生 reset_buffer（句柄已不可信），只清纯 Python 缓冲。
            if self._ring_reader is not None:
                self._ring_reader.clear()
            return False
        # 来源: librtlsdr src/librtlsdr.c:1702 — rtlsdr_reset_buffer；
        # rtl_433 src/sdr.c:1706 — 换频后必须 reset_buffer，否则 USB 队列里
        # 残留的旧频率 URB 会被当成新频数据读出。
        self._reset_rtl_buffer()
        # 清空环形缓冲里换频前的旧频数据，避免新旧频 IQ 混叠
        if self._ring_reader is not None:
            self._ring_reader.clear()
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._sdr is None:
            return True
        # 来源: SDR++ main.cpp:27-39 + 377-385 —— 先吸附到 11 个离散档之一，
        # SDR++ 用户只能在这 11 档里选，不接受连续采样率。
        snapped = self.nearest_sample_rate(rate_hz)
        if snapped != int(rate_hz):
            logger.info(
                f"RTL-SDR 采样率 {rate_hz:.0f} Hz 吸附到 SDR++ 离散档 "
                f"{snapped} Hz (main.cpp:27-39)")
        # 来源: librtlsdr src/librtlsdr.c:1100-1104 —— 死区 (300k,900k] 非法，
        # 再做一次底层安全钳位（吸附后的档位都在合法区间内，这里基本不会触发）。
        clamped, was = self._clamp_sample_rate(float(snapped))
        if was:
            logger.warning(
                f"RTL-SDR 采样率 {snapped} Hz 落在 librtlsdr 死区/越界，"
                f"已钳位到 {clamped:.0f} Hz")
        try:
            self._sdr.sample_rate = clamped
        except Exception as e:
            logger.warning(f"RTL-SDR 设置采样率 {clamped:.0f} 失败: {e}")
            return False
        # 来源: librtlsdr src/librtlsdr.c:1702 — 换采样率后也要 reset_buffer
        self._reset_rtl_buffer()
        return True

    def _resolve_applied_sample_rate(self, rate_hz: float) -> float:
        """RTL 实际生效值 = 11 档最近邻吸附后再过合法性钳位。"""
        snapped = self.nearest_sample_rate(rate_hz)
        clamped, _ = self._clamp_sample_rate(float(snapped))
        return float(clamped)

    def _apply_bandwidth(self, bw_hz: float) -> bool:
        """写 tuner 模拟带宽；0=自动。0 是 librtlsdr 合法调用
        （rtlsdr_set_tuner_bandwidth(dev, 0) = 自动），connect() 依赖
        这一下发把 tuner 置回自动——与 TestTC6 钉的「connect 后下发
        bandwidth=0」一致，不许跳过。"""
        if self._sdr is None:
            return True
        try:
            self._sdr.bandwidth = int(bw_hz)
            return True
        except Exception as e:
            logger.warning(f"RTL-SDR 设置带宽 {bw_hz} Hz 失败: {e}")
            return False

    def _reset_rtl_buffer(self) -> None:
        """直接经 ctypes 调 rtlsdr_reset_buffer（RtlSdrAio 无此 Python 方法）。

        用设备句柄 dev_p 下发；失败仅记录、不影响主流程。换频/换采样率后
        清除 USB 队列里残留的旧 URB，避免新旧数据混读。
        """
        # 生产者持续同步读取时，调 rtlsdr_reset_buffer 会令其 access
        # violation；运行中改频/改速率只清环形缓冲即可（见调用点 ring.clear）。
        # 仅在环形生产者尚未启动（connect 配置阶段）时做真正的 USB 复位。
        if getattr(self, "_ring_reader", None) is not None:
            return
        try:
            from rtlsdr.librtlsdr import librtlsdr
            dev_p = getattr(self._sdr, "dev_p", None) if self._sdr else None
            if not dev_p:
                return
            rc = librtlsdr.rtlsdr_reset_buffer(dev_p)
            if rc < 0:
                logger.info("rtlsdr_reset_buffer rc=%d（忽略）", rc)
        except Exception as e:
            logger.info("rtlsdr_reset_buffer 失败（忽略）: %s", e)

    def _apply_gain(self, gain_db: float) -> bool:
        if self._sdr is None:
            return True
        try:
            # 来源: librtlsdr include/rtl-sdr.h:253 + src/librtlsdr.c:1073 —
            # 手动增益必须先 set_tuner_gain_mode(dev, 1)，再 set_tuner_gain()；
            # rtl_433 src/sdr.c:1409 同样先切 manual 再设增益。
            # pyrtlsdr 的 gain 属性接收 dB 浮点，内部自动查表并 ×10 转 0.1dB
            # （等价 rtl_433 src/sdr.c:1396 atof*10），无需我们手动乘 10。
            # 来源: librtlsdr src/convenience/convenience.c:116-141 nearest_gain —
            # 驱动只接受离散增益档，先把请求值吸附到最近一档，避免 pyrtlsdr
            # 内部再做隐式四舍五入造成与回读值不一致。
            target = gain_db
            if self._gain_table_db:
                snapped = min(self._gain_table_db, key=lambda g: abs(g - gain_db))
                if abs(snapped - gain_db) > 1e-6:
                    logger.info(
                        f"RTL-SDR 增益 {gain_db:.1f}dB 吸附到最近离散档 {snapped:.1f}dB "
                        f"(调谐器 {self.tuner_name})")
                target = snapped
            try:
                self._sdr.set_manual_gain_mode(1)  # 1 = manual tuner gain
            except Exception:
                try:
                    self._sdr.gain_mode = 1
                except Exception:
                    pass
            self._sdr.gain = float(target)
            # 回读确认：pyrtlsdr 的 ``gain`` getter 调 rtlsdr_get_tuner_gain，
            # 拿到的是 librtlsdr 实际写入的离散档——setter 先按 gain_values
            # 吸附，请求值 28.0 不会原样到达驱动。2026-11-11 实测（pyrtlsdr
            # 0.2.93，gain_values=[0,250,500] 十分之一 dB）：set_gain(28.0)
            # 只下发 250，get_gain() 返回 25.0。
            # rtl_tcp 适配器的 getter 只是本地缓存、无设备回执，不算回读。
            self._applied_gain_db = float(target)
            if self._host is None:
                try:
                    written = float(self._sdr.gain)
                except Exception as e:  # noqa: BLE001 - 读不到就保留下发值
                    logger.debug("RTL-SDR 增益回读失败（保留下发值）: %s", e)
                else:
                    if math.isfinite(written):
                        self._applied_gain_db = written
        except Exception as e:
            logger.warning(f"RTL-SDR 设置增益 {gain_db} dB 失败: {e}")
            return False
        return True

    def set_gain(self, gain_db: float) -> bool:
        """设置增益，并把驱动确认的实际档位写回状态。

        基类成功后写入的是限幅后的请求值；RTL 调谐器只接受离散档，
        这里覆盖成 ``_apply_gain`` 回读到的实际档位，状态面板才不会把
        「请求 28.0 dB」显示成已生效。
        """
        self._applied_gain_db = None
        ok = super().set_gain(gain_db)
        if ok and self._applied_gain_db is not None:
            self.status.gain_db = self._applied_gain_db
        return ok

    def gain_readback_source(self) -> str:
        """本地 USB 有驱动确认通道；rtl_tcp 协议不回执增益值。"""
        if self._sdr is None or self._host is not None:
            return ""
        return "驱动确认档位（librtlsdr 实际写入的离散档，非硬件物理回读）"

    def readback_hw_state(self) -> bool:
        if not self._sdr:
            return False
        got = 0
        try:
            self.status.frequency_hz = float(self._sdr.center_freq)
            got += 1
        except Exception:
            pass
        try:
            self.status.sample_rate_hz = float(self._sdr.sample_rate)
            got += 1
        except Exception:
            pass
        try:
            self.status.gain_db = float(self._sdr.gain)
            self.status.agc_enabled = False
            got += 1
        except Exception:
            pass
        if got:
            logger.info(f"RTL-SDR 回读硬件状态: freq={self.status.frequency_hz/1e6:.3f}MHz, "
                        f"rate={self.status.sample_rate_hz/1e3:.1f}kHz, gain={self.status.gain_db:.1f}dB")
        else:
            logger.warning("RTL-SDR 不支持参数回读")
        return got > 0

    # 调谐器型号探测失败（ctypes 句柄拿不到 / 沙箱 / 旧 DLL）时，退回市场最常见的
    # R820T/R828D 系增益表中点档：librtlsdr.c:966-969 r82xx_gains[] 共 29 档，
    # 中点索引 14 = 254 tenths = 25.4 dB。绝不停在 0 dB（"聋棒"）。
    _UNKNOWN_TUNER_FALLBACK_GAIN_DB = 25.4

    def _maybe_apply_first_gain_midpoint(self) -> None:
        """首启增益自动拉到增益表中点（来源 gqrx mainwindow.cpp:571-579）。

        仅在回读后 gain_db 仍为 0.0（用户从未手动设过增益）时触发；用户已手动
        设过非零增益时不覆盖。调谐器已知时用真实离散增益表中点；调谐器探测失败
        （ctypes 句柄拿不到 / 沙箱 / 旧 DLL）时退回 R820T 系安全中点，确保首启
        绝不留 0 dB 聋棒（对齐 SDR++ 初始化序列，源自提交 8a83877）。
        """
        if self.status.gain_db != 0.0:
            return
        if self._gain_table_db:
            mid_gain = float(self._gain_table_db[len(self._gain_table_db) // 2])
        else:
            mid_gain = self._UNKNOWN_TUNER_FALLBACK_GAIN_DB
        if self._apply_gain(mid_gain):
            self.status.gain_db = mid_gain
            logger.info("RTL-SDR 首启自动增益: 0 dB -> %.1f dB（增益表中点，"
                        "来源 gqrx mainwindow.cpp:571-579）", mid_gain)

    def _apply_agc(self, enabled: bool) -> bool:
        """写 RTL AGC（tuner gain mode + RTL2832 数字 AGC）。

        来源: librtlsdr include/rtl-sdr.h:253 + src/librtlsdr.c:1073 —
        tuner gain mode: 0=auto(AGC), 1=manual。
        开 AGC 时显式切 tuner 到 auto；关 AGC 时由后续 _apply_gain 切回 manual。
        注意这是 tuner 前端 AGC，与 RTL2832 数字 AGC 相互独立
        （librtlsdr src/librtlsdr.c:1157 set_agc_mode）。
        """
        if self._sdr:
            try:
                try:
                    self._sdr.set_manual_gain_mode(0 if enabled else 1)
                except Exception:
                    try:
                        self._sdr.gain_mode = 0 if enabled else 1
                    except Exception:
                        pass
                self._sdr.set_agc_mode(bool(enabled))
            except Exception:
                try:
                    self._sdr.agc_mode = bool(enabled)
                except Exception:
                    return False
        return True

    def set_ppm(self, ppm: int) -> bool:
        """设置晶振频偏校正（ppm）。廉价棒典型 20~50ppm。

        对齐 SDR++ main.cpp:309：打开设备后始终下发 freq_correction（含 0），
        避免上一次运行的频偏残留在棒上。注意 librtlsdr.c:922-923 规定
        「请求 ppm == 当前 corr 时返回 -2」——刚打开的棒 corr 默认就是 0，
        写 ppm=0 必然返回 -2。pyrtlsdr 把任何 <0 都当硬错并 close() 设备
        （rtlsdr.py:239-242），所以这里优先走低层 ctypes 调用、把 -2 视作
        "无需改动"的成功；低层句柄不可用（如测试 FakeRtlSdr 注入）时退回
        Python 属性赋值。
        """
        self._ppm = int(ppm)
        if not self._sdr:
            return True
        # 路径 A：低层 ctypes 直接下发，容忍 -2（与 _reset_rtl_buffer 同手法）。
        try:
            from rtlsdr.librtlsdr import librtlsdr
            dev_p = getattr(self._sdr, "dev_p", None)
            if dev_p is not None:
                # 返回值（含 -2 "already set"）仅记录、不抛错、不 close。
                librtlsdr.rtlsdr_set_freq_correction(dev_p, int(ppm))
                return True
        except Exception:
            pass
        # 路径 B：无 ctypes 句柄（FakeRtlSdr / RtlSdrAio 无 dev_p）——走属性赋值。
        try:
            self._sdr.freq_correction = int(ppm)
            return True
        except Exception:
            return False

    def set_direct_sampling(self, branch: str = "q") -> bool:
        """HF 短波直采：branch='q'/'i' 开启，'off' 关闭。需调谐器支持（RTL2832 原生）。"""
        mapping = {"off": 0, "i": 1, "q": 2}
        val = mapping.get(str(branch).lower(), 2)
        self._direct = val
        if self._sdr:
            try:
                self._sdr.set_direct_sampling(val)
                return True
            except Exception:
                try:
                    self._sdr.direct_sampling = ("off" if val == 0 else branch.lower())
                    return True
                except Exception:
                    return False
        return True

    def set_bias_tee(self, enabled: bool) -> bool:
        """偏置供电（RTL-SDR Blog V3/V4 等支持，给有源天线/LNA 供电）。"""
        if self._sdr:
            try:
                self._sdr.set_bias_tee(bool(enabled))
                return True
            except Exception:
                return False  # 老棒/老库不支持
        return False

    def supports_offset_tuning(self) -> bool:
        """偏移调谐是否真的能下发（只探测能力，不写状态）。

        驱动不提供 ``set_offset_tuning``（本机 pyrtlsdr 的 ``RtlSdrAio`` 就是
        这种）属于「不支持」，不是「失败」：连接流程据此跳过该项，不再把它
        报成"未生效"，勾选开关也会直接说明不支持。
        """
        return bool(self._sdr) and hasattr(self._sdr, "set_offset_tuning")

    def set_offset_tuning(self, enabled: bool) -> bool:
        """Offset Tuning（DC 偏移抵消）。

        来源: SDR++ main.cpp:485-494 offsetTuning checkbox + main.cpp:322
        rtlsdr_set_offset_tuning。开启后 RTL2832 把本振偏移到采样率中央，
        让直流尖峰落到基带边缘，便于数字 DC 抵消。

        能力探测（2026-11-07 真实设备）：本机 pyrtlsdr 的 ``RtlSdr`` 实际是
        ``RtlSdrAio``，**没有** ``set_offset_tuning`` 方法（``hasattr`` 为
        False）。旧实现直接调用 → AttributeError → 返回 False → 连接流程报
        「Offset Tuning 未生效」，用户插上棒子就看到一条红字。该参数是可选
        优化项，驱动不提供就是「不支持」而不是「失败」：此时返回 False 且
        **不写状态**——状态面板绝不允许把"勾选过"显示成"设备已开启"。
        """
        if not self.supports_offset_tuning():
            logger.debug(
                "RTL-SDR 驱动不提供 set_offset_tuning（%s）：按「不支持」处理，状态保持原值",
                type(self._sdr).__name__ if self._sdr is not None else "未连接")
            return False
        try:
            self._sdr.set_offset_tuning(bool(enabled))
        except Exception as e:
            # 下发失败同样不写状态：设备没有接受过的值不能当成已生效。
            logger.warning(f"RTL-SDR set_offset_tuning({enabled}) 失败: {e}")
            return False
        self._offset_tuning = bool(enabled)
        return True

    @property
    def offset_tuning(self) -> bool:
        """已确认生效的偏移调谐状态（状态面板读数源）。

        只反映最后一次真正下发成功的值；驱动不支持或下发失败时保持原值
        （默认关），因此状态面板不会出现"勾了但没生效"的假「开」。
        """
        return bool(self._offset_tuning)

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or not self._sdr:
            return None
        # 优先走生产者线程填充的环形缓冲（对照 SDR++ ring_buffer.h:36-64 read()），
        # 接口签名不变，main_window 无感知。
        if self._ring_reader is not None:
            try:
                samples = self._ring_reader.read(num_samples, timeout=1.0)
            except Exception:
                return None
            if samples is None:
                return None
            self._samples_read += len(samples)
            return samples
        # 兜底：环形缓冲未启动时直接同步读（保持原行为）
        try:
            samples = self._sdr.read_samples(num_samples)
            self._samples_read += num_samples
            return samples
        except Exception:
            # USB 拔出或设备错误，降级返回 None
            return None


class AISDRMiniBackend(SDRBackend):
    """
    自研 ai-sdr Mini 后端（SI4732 + ESP32）。

    通过 WebSocket/MCP 协议与 ESP32 通信。
    SI4732 输出解调后音频流，不输出原始 IQ。
    """

    def __init__(self, host: str = "192.168.4.1", port: int = 81):
        device = SDRDevice(
            device_type="ai_sdr_mini",
            device_id="ai_sdr_mini_0",
            name="ai-sdr Mini（自研 SI4732）",
            frequency_range=(144000, 108000000),
            sample_rate_range=(48000, 48000),
            max_gain=12.0,
            supports_iq=False,  # SI4732 不出原始 IQ
            supports_tx=False,
            # 自研产品规格常量（SI4732 数据手册 + 本产品定义）→ declared。
            capability_status={
                "freq_range": "declared",
                "sample_rate_range": "declared",
                "gain_range": "declared",
                "tx": "declared",
            },
        )
        super().__init__(device)
        self._host = host
        self._port = port
        self._ws = None
        self._ws_lock = threading.Lock()
        self._current_mode = "fm"  # fm / am / sw

    def _send_mcp(self, method: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """发送 MCP JSON-RPC 请求并等待响应。"""
        if not self._ws:
            return None
        try:
            with self._ws_lock:
                request = {
                    "jsonrpc": "2.0",
                    "id": int(time.time() * 1000) % 100000,
                    "method": method,
                    "params": params or {},
                }
                self._ws.send(json.dumps(request))
                # 等待响应（最多 2 秒）
                self._ws.settimeout(2.0)
                result = json.loads(self._ws.recv())
                return result.get("result") or result.get("params", {}).get("result")
        except Exception:
            return None

    def connect(self) -> bool:
        """通过 WebSocket 连接 ESP32。"""
        try:
            import websocket
            url = f"ws://{self._host}:{self._port}"
            self._ws = websocket.create_connection(url, timeout=3)
            # 验证连接：调用 list_tools
            result = self._send_mcp("list_tools")
            if result is not None:
                self.status.connected = True
                self._start_time = time.time()
                # 拉取当前状态
                self._refresh_status()
                # 注入云台板载通道：gimbal board_pwm 模式经此 WebSocket 下发
                self._inject_gimbal_channel(available=True)
                return True
            else:
                self.disconnect()
                return False
        except Exception:
            self.status.connected = False
            self._ws = None
            return False

    def _inject_gimbal_channel(self, available: bool):
        """连接成功后把板子 MCP 通道注入全局云台控制器（断开则清除）。"""
        try:
            from mbdsdr_ai.sdr_tools import _get_gimbal_controller
            gc = _get_gimbal_controller()
            if available:
                gc.board.set_send_callable(
                    lambda method, params: self._send_mcp(method, params) or {}
                )
                # 若当前就是板载模式，刷新连接状态
                if gc.status.mode.value == "board_pwm":
                    gc.status.connected = True
            else:
                gc.board.set_send_callable(None)
                if gc.status.mode.value == "board_pwm":
                    gc.status.connected = False
        except Exception:
            pass  # 云台模块缺失不影响 SDR 主功能

    def gimbal_set(self, az: float, el: float) -> Optional[Dict]:
        """驱动板载舵机云台（az 0-180°, el 0-90°, 负值释放）。"""
        return self._send_mcp("gimbal_set", {"az": az, "el": el})

    def gimbal_get(self) -> Optional[Dict]:
        """读取板载云台当前角度。"""
        return self._send_mcp("gimbal_get", {})

    def disconnect(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._inject_gimbal_channel(available=False)
        super().disconnect()

    def _refresh_status(self):
        """从设备拉取当前状态。"""
        result = self._send_mcp("get_status")
        if result:
            if "freq" in result:
                self.status.frequency_hz = float(result["freq"]) * 1000000 if result.get("mode_name") == "FM" else float(result["freq"]) * 1000
            if "rssi" in result:
                self.status.rssi_db = float(result["rssi"])
                # 固件 SI4732 RSQ RSSI 原始值：0–127 dBµV，不是 dBFS。
                # 依据 ai-sdr-mini-kicad/ai_sdr_mini_firmware_v0.5_WebOTA.ino:336
                self.status.rssi_unit = "dBµV"
            if "snr" in result:
                self.status.snr_db = float(result["snr"])
            if "volume" in result:
                self.status.volume = int(result["volume"])
            if "mode_name" in result:
                self._current_mode = result["mode_name"].lower()

    def _apply_frequency(self, freq_hz: float) -> bool:
        # 根据频率范围选择 FM 或 AM 模式下发 MCP
        if 64000000 <= freq_hz <= 108000000:
            result = self._send_mcp("tune_fm", {"freq_mhz": freq_hz / 1000000})
            self._current_mode = "fm"
        elif 531000 <= freq_hz <= 1710000:
            result = self._send_mcp("tune_am", {"freq_khz": int(freq_hz / 1000)})
            self._current_mode = "am"
        else:
            # SW 短波范围（需要上变频器）
            result = self._send_mcp("tune_am", {"freq_khz": int(freq_hz / 1000)})
            self._current_mode = "sw"
        return result is not None

    def _apply_gain(self, gain_db: float) -> bool:
        # SI4732 没有直接增益控制，走 AGC；无硬件写入，视为成功
        return True

    def set_volume(self, volume: int) -> bool:
        """设置音量（0-63）。"""
        volume = max(0, min(63, volume))
        result = self._send_mcp("set_volume", {"volume": volume})
        if result is not None:
            self.status.volume = volume
            return True
        return False

    def get_gps(self) -> Optional[Dict]:
        """获取 GPS/北斗定位。"""
        return self._send_mcp("get_gps")

    def get_imu(self) -> Optional[Dict]:
        """获取 9 轴姿态。"""
        return self._send_mcp("get_imu")

    def start_record(self) -> bool:
        """开始 I2S 录音。"""
        result = self._send_mcp("start_record")
        if result is not None:
            self.status.recording = True
            return True
        return False

    def stop_record(self) -> Optional[Dict]:
        """停止录音，返回采样数。"""
        result = self._send_mcp("stop_record")
        if result is not None:
            self.status.recording = False
        return result

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        # SI4732 不出 IQ，返回 None
        return None


class HackRFBackend(SDRBackend):
    """
    HackRF One 后端。

    驱动走 libhackrf 的 ctypes 直连（``hackrf_params.HackRFBackend``），
    与 ``hackrf_transfer`` 命令同一套 C API：
      - 接收：hackrf_start_rx + 回调队列（int8 交织 I/Q）
      - 发射：hackrf_start_tx + 回调队列（int8 交织 I/Q），见 ``write_tx``
    需要系统安装 libhackrf（hackrf 主机工具包）；缺库/缺设备时 connect 返回
    False 并写 status.error，绝不假成功。

    硬件量程/增益档全部来自 mbdsdr_ai/hackrf_params.HackRFParams（依据 HackRF One
    官方数据手册与公开 API 头文件独立整理）：
      - 频率 1-6000 MHz
      - 采样率 2-20 MS/s
      - LNA(RX IF) 0-40 dB（步进 8 dB）
      - VGA(RX BB) 0-62 dB（步进 2 dB）
      - TXVGA 0-47 dB（步进 1 dB）

    TX 说明：写流路径是真实 libhackrf 调用，但**未在真机上验证**（见
    ``device.tx_verified=False``），UI/工具面必须标「未验证」而非「支持」。
    """

    def __init__(self, device_index: int = 0):
        # 量程/增益档一律用真实参数表，不再在本处硬编码魔数。
        from .hackrf_params import HackRFParams
        self._params = HackRFParams()
        device = SDRDevice(
            device_type="hackrf",
            device_id=f"hackrf_{device_index}",
            name=f"HackRF One #{device_index}",
            frequency_range=(self._params.min_freq_hz, self._params.max_freq_hz),
            sample_rate_range=(self._params.min_sr_hz, self._params.max_sr_hz),
            max_gain=float(self._params.lna_max_db),  # RX 路径最大前端增益档
            supports_iq=True,
            supports_tx=True,
            tx_verified=False,  # 尚未在真机验证过发射链路
            # HackRF 参数表（mbdsdr_ai/hackrf_params.py）来自厂商文档 → declared。
            capability_status={
                "freq_range": "declared",
                "sample_rate_range": "declared",
                "gain_range": "declared",
                "tx": "declared",
            },
            capability_note={"tx": "HackRF 半双工发射，真机链路未验证"},
        )
        super().__init__(device)
        self._device_index = device_index
        self._hackrf = None
        self._rx_started = False
        self._lna_db = 8
        self._vga_db = 16
        # 来源: gqrx/src/qtgui/ioconfig.cpp:279-302 —— HackRF 默认 8 MS/s 甜点档
        # （GQRX ioconfig 里 HackRF start_sample_rate=8e6）。
        self.status.sample_rate_hz = 8_000_000.0

    def connect(self) -> bool:
        from .hackrf_params import HackRFBackend as _CtlsBackend
        if self._hackrf is None:
            self._hackrf = _CtlsBackend(self._device_index, self._params)
        if not self._hackrf.connect():
            self.status.connected = False
            self.status.error = ("[未连接-libhackrf缺失或未插HackRF] "
                                 "请安装 hackrf 主机工具包（libhackrf）并检查 USB 设备。")
            logger.warning(self.status.error)
            return False
        self._rx_started = False
        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()
        if self.status.sample_rate_hz is None:
            self.status.sample_rate_hz = self._FALLBACK_SAMPLE_RATE_HZ
        # 对照 RTL/RtlTcp/Pluto 纪律：打开后逐项显式下发，不停在出厂/上次残留。
        # 任一项被硬件拒绝 → 如实失败，不带半配置硬件上线。
        if not self.set_sample_rate(self.status.sample_rate_hz):
            return self._connect_init_failed("采样率")
        target_freq = self.status.frequency_hz or 98_000_000.0
        if not self.set_frequency(target_freq):
            return self._connect_init_failed("中心频率")
        if not self.set_lna_gain(self._lna_db):
            return self._connect_init_failed("LNA 增益")
        if not self.set_vga_gain(self._vga_db):
            return self._connect_init_failed("VGA 增益")
        self.readback_hw_state()
        return True

    def _connect_init_failed(self, what: str) -> bool:
        detail = self.status.error or "硬件拒绝下发"
        self.status.connected = False
        self.status.link_state = "failed"
        self.status.error = f"HackRF 连接初始化{what}下发失败: {detail}"
        logger.warning(self.status.error)
        if self._hackrf is not None:
            try:
                self._hackrf.disconnect()
            except Exception:
                pass
            self._hackrf = None
        return False

    def readback_hw_state(self) -> bool:
        """回读 ctypes 层缓存（只在写成功后更新）；增益回读 VGA 档。

        libhackrf 无增益查询命令，_lna_db/_vga_db 是最近一次成功下发值，
        与 rtl_tcp 适配器同口径。status.gain_db 语义与 _apply_gain 一致=VGA。
        """
        if self._hackrf is None:
            return False
        got = 0
        try:
            f = float(self._hackrf.get_frequency())
            if f > 0:
                self.status.frequency_hz = f
                got += 1
        except Exception:
            pass
        try:
            r = float(self._hackrf.get_sample_rate())
            if r > 0:
                self.status.sample_rate_hz = r
                got += 1
        except Exception:
            pass
        self.status.gain_db = float(self._vga_db)
        return got > 0

    def disconnect(self):
        self._rx_started = False
        if self._hackrf:
            try:
                self._hackrf.disconnect()
            except Exception as e:
                logger.debug("HackRF 关闭失败(忽略): %s", e)
            self._hackrf = None
        super().disconnect()

    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._hackrf is None:
            return True
        ok = self._hackrf.set_frequency(freq_hz)
        if not ok:
            self.status.error = f"[RX] hackrf_set_freq({int(freq_hz)}) 失败"
        return ok

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._hackrf is None:
            return True
        ok = self._hackrf.set_sample_rate(rate_hz)
        if not ok:
            self.status.error = f"[RX] hackrf_set_sample_rate({int(rate_hz)}) 失败"
        return ok

    def _apply_gain(self, gain_db: float) -> bool:
        """默认总增益映射到 VGA(基带) 档，自动吸附到偶数 dB（hackrf.c:2058）。"""
        return self.set_vga_gain(gain_db)

    def set_lna_gain(self, db: int) -> bool:
        """真实设置 RX IF(LNA) 增益，吸附到 8dB 档（hackrf.c:2031）。

        来源: host/libhackrf/src/hackrf.c:2022-2047；firmware/common/max2837.c:344-371。
        """
        if not self.status.connected or self._hackrf is None:
            return False
        v = self._params.clamp_lna_gain(db)
        if not self._hackrf.set_lna_gain(v):
            self.status.error = f"[RX] hackrf_set_lna_gain({v}) 失败"
            return False
        self._lna_db = v
        return True

    def set_vga_gain(self, db: int) -> bool:
        """真实设置 RX 基带(VGA) 增益，吸附到偶数 dB 档（hackrf.c:2058）。

        来源: host/libhackrf/src/hackrf.c:2049-2074；firmware/common/max2837.c:373-381。
        """
        if not self.status.connected or self._hackrf is None:
            return False
        v = self._params.clamp_vga_gain(db)
        if not self._hackrf.set_vga_gain(v):
            self.status.error = f"[RX] hackrf_set_vga_gain({v}) 失败"
            return False
        self._vga_db = v
        return True

    def set_txvga_gain(self, db: int) -> bool:
        """真实设置 TX IF 增益，钳到 0-47 dB（hackrf.c:2081）。"""
        if not self.status.connected or self._hackrf is None:
            return False
        v = self._params.clamp_txvga_gain(db)
        if not self._hackrf.set_txvga_gain(v):
            self.status.error = f"[TX] hackrf_set_txvga_gain({v}) 失败"
            return False
        return True

    def set_bias_tee(self, on: bool) -> bool:
        """开关天线偏置 3.3V/50mA（hackrf.c:2102）。"""
        if not self.status.connected or self._hackrf is None:
            return False
        if not self._hackrf.set_bias_tee(bool(on)):
            self.status.error = "[RX] hackrf_set_antenna_enable 失败"
            return False
        return True

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or not self._hackrf:
            return None
        if not self._rx_started:
            if not self._hackrf.start_rx():
                self.status.error = "[RX] hackrf_start_rx 失败（设备被占用或固件异常）"
                return None
            self._rx_started = True
        raw = self._hackrf.read_samples(num_samples)
        if raw is None:
            return None  # 未收齐就是未收齐，不填假样本
        arr = np.frombuffer(raw, dtype=np.int8).astype(np.float32) / 128.0
        samples = (arr[0::2] + 1j * arr[1::2]).astype(np.complex64)
        self._samples_read += len(samples)
        return samples

    # ── 发射 ──────────────────────────────────────────────────────────
    def _tx_ready(self) -> bool:
        return self._hackrf is not None and callable(
            getattr(self._hackrf, "write_tx", None))

    def write_tx(self, iq_samples: np.ndarray) -> bool:
        """真实写 TX 流（hackrf_start_tx → 回调队列 → USB 流）。

        未过人工确认授权的直调一律拒绝；写流失败把原因落到 status.error，
        不返回"看似成功"的 True。
        """
        from .tx_guard import reject_unless_authorized
        if not reject_unless_authorized(self):
            self._note_tx_reject()
            return False
        if not self.status.connected or self._hackrf is None:
            self.status.error = "[TX] HackRF 未连接，无法发射"
            return False
        try:
            ok = self._hackrf.write_tx(np.asarray(iq_samples, dtype=np.complex64))
        except Exception as e:  # noqa: BLE001 - 驱动异常必须转为如实失败
            self.status.error = f"[TX] HackRF 写波异常: {type(e).__name__}: {e}"
            return False
        if not ok:
            detail = getattr(self._hackrf, "last_tx_error", "")
            self.status.error = (f"[TX] HackRF 写流失败: {detail}" if detail
                                 else "[TX] HackRF 写流失败（libhackrf 返回失败/欠载）")
        return bool(ok)


class SoapySDRBackend(SDRBackend):
    """
    通用 SoapySDR 后端（pothosware/SoapySDR 统一抽象层）。

    SoapySDR 是 SDR 生态的"总线"：同一个 API 驱动 RTL-SDR、HackRF、USRP、BladeRF、
    Airspy、LimeSDR、Red Pitaya 等数十种前端，只要装好对应 Soapy* 支持模块即可。
    本后端按三层降级策略接入，**绝不假成功**：
      1) 有 SoapySDR Python 绑定（``import SoapySDR``）→ 原生 API 全功能；
      2) 无 Python 绑定但有 ``SoapySDRUtil`` CLI → 解析其文本输出做枚举；
      3) 两者都没有 → list_devices() 返回 [] 并 log warning，connect() 直接失败。

    关键 API 与行号（来源: repos/SoapySDR）：
      - Device::enumerate(args) 枚举入口        —— lib/Factory.cpp:41
      - 枚举结果自动注入 driver 键             —— lib/Factory.cpp:101
      - Device::make(args) 打开设备             —— lib/Factory.cpp:133
      - SOAPY_SDR_RX = 1（接收方向）            —— include/SoapySDR/Constants.h:22
      - setFrequency(RX, chan, freq)            —— include/SoapySDR/Device.hpp:801
      - getFrequencyRange(RX, chan) → RangeList —— include/SoapySDR/Device.hpp:855
      - setSampleRate(RX, chan, rate)           —— include/SoapySDR/Device.hpp:884
      - getSampleRateRange(RX, chan) → RangeList—— include/SoapySDR/Device.hpp:909
      - setGain(RX, chan, db)                   —— include/SoapySDR/Device.hpp:725
      - getGainRange(RX, chan) → Range          —— include/SoapySDR/Device.hpp:759
      - setupStream(RX, format, channels)       —— include/SoapySDR/Device.hpp:267
      - readStream(stream, buffs, n, ...)       —— include/SoapySDR/Device.hpp:352
      - Range.minimum()/maximum()               —— include/SoapySDR/Types.hpp:64
    Python 侧真实用法参考: repos/SoapySDR/swig/python/apps/MeasureDelay.py:81(setupStream),
      :108(readStream, 返回对象 .ret/.flags/.timeNs)。
    """

    # 我们用复数 float32 流（与 pyrtlsdr 返回一致，上层解调链无需改）。
    # 来源: include/SoapySDR/Device.hpp:230 附近 stream format 字符串；
    # MeasureDelay.py:81 用 SOAPY_SDR_CF32。
    _STREAM_FORMAT = "CF32"

    def __init__(self, device_args: Any = "", device_info: Optional[Dict[str, Any]] = None):
        """
        :param device_args: 打开设备用的参数字符串或 dict，例如
                            "driver=rtlsdr,serial=00000001"（来自 enumerate 结果）。
        :param device_info: enumerate 阶段拿到的身份信息 dict（label/serial/...），
                            用于构造 SDRDevice；缺省时填占位。
        """
        info = device_info or {}
        # 枚举身份字段；拿不到时给安全默认，绝不伪造真实范围。
        driver = info.get("driver", "soapy")
        label = info.get("label", str(device_args) or "SoapySDR device")
        serial = info.get("serial", "")
        # 频率/采样率范围：优先用枚举阶段探测到的真实范围；探测不到时给
        # 「宽但保守」的**操作占位**（connect 后 readback_hw_state() 会校正），
        # 但能力状态仍标记为 unknown，UI 不据此显示具体量程。
        freq_range = self._range_tuple(info.get("freq_range"), (1e3, 7.2e9))
        rate_range = self._range_tuple(info.get("sample_rate_range"), (1e3, 61.44e6))
        # 增益上限：未知时用 0.0 哨兵（主窗口只按 >0 收放滑杆），
        # 不再伪造 60 dB 上限让 UI 显示成「已知能力」。
        _gr = info.get("gain_range")
        max_gain = (float(_gr[1]) if isinstance(_gr, (tuple, list)) and len(_gr) >= 2
                    else 0.0)

        device = SDRDevice(
            device_type=f"soapy_{driver}",
            device_id=f"soapy_{driver}_{serial or label}",
            name=label,
            frequency_range=freq_range,
            sample_rate_range=rate_range,
            max_gain=max_gain,
            supports_iq=True,
            supports_tx=False,  # 接收优先；TX 由具体 driver 决定，本后端默认只收
            # 枚举阶段探测到的来源原样透传；缺失即 unknown（不补 declared）。
            capability_status=dict(info.get("capability_status") or {}),
            capability_note=dict(info.get("capability_note") or {}),
        )
        super().__init__(device)
        # 保留 enumerate 原样返回的 dict（含 driver/serial/label/manufacturer/product），
        # connect() 直接把它传回 SoapySDR.Device()——这是官方标准用法
        # （MeasureDelay.py:81 附近 Device(enumerate()[i])）。自拼字符串会被 label 里的
        # 逗号/空格误导，所以优先 dict。_args_str 仅用于日志展示。
        self._device_args = device_args
        if isinstance(device_args, dict):
            self._args_str = ",".join(f"{k}={v}" for k, v in device_args.items())
        else:
            self._args_str = str(device_args)
        self._serial = serial
        self._driver = driver
        self._sdr = None       # SoapySDR.Device 实例
        #: 驱动 setSampleRate() 真正返回的速率（Soapy 会按硬件档位吸附）。
        #: None = 这次没拿到回值，读回退到请求值。
        self._applied_sample_rate_hz: Optional[float] = None
        self._stream = None    # setupStream 返回的流句柄
        self._chan = 0
        self._rx = 1           # SOAPY_SDR_RX，来源: Constants.h:22
        # 驱动报告的 TX 通道数（None=未探测）。只用于把「本路径未接写流」
        # 与「硬件不能发」区分开，不改变 supports_tx() 的 False。
        self._num_tx_channels = info.get("tx_channels")

    def tx_capability(self) -> Dict[str, Any]:
        """SoapySDR 通用路径：四态之外再加「驱动级可发但本路径未接」。

        本类没有 ``write_tx``，``supports_tx()`` 恒为 False——这是"本路径不能发"
        的事实，但不是"硬件不能发"的事实。HAL 的 ``getNumTxChannels()``
        （Device.hpp:1155）会报告驱动级 TX 通道数：HackRF/USRP/Pluto 经 Soapy
        打开时通常 >0。把两者分开列出，UI 才不会把 1 读成 2。

        ``blocked_reason`` 取值：
          * ``driver_capable_path_not_implemented`` —— 驱动可发，本路径未接写流；
          * ``no_tx_channels`` —— 驱动明确报告 0 个 TX 通道（只能收）；
          * ``driver_capability_unknown`` —— 未连接/驱动未报告，能力未知。
        """
        cap = super().tx_capability()
        n = getattr(self, "_num_tx_channels", None)
        cap["driver_tx_channels"] = n
        if n is None:
            cap["driver_capable"] = None
            cap["blocked_reason"] = "driver_capability_unknown"
            cap["notice"] = ("尚未探测 SoapySDR TX 通道数（未连接或驱动未报告）："
                             "发射能力未知")
        elif int(n) > 0:
            cap["driver_capable"] = True
            cap["blocked_reason"] = "driver_capable_path_not_implemented"
            cap["notice"] = (f"SoapySDR 驱动报告 {int(n)} 个 TX 通道：驱动级可发，"
                             "但本通用后端未接入 TX 写流，本路径不能发射")
        else:
            cap["driver_capable"] = False
            cap["blocked_reason"] = "no_tx_channels"
            cap["notice"] = "SoapySDR 驱动报告 0 个 TX 通道：该硬件/驱动只能接收"
        return cap

    # ------------------------------------------------------------------
    # 工具：RangeList → (min, max)
    # ------------------------------------------------------------------
    @staticmethod
    def _range_tuple(rl, default: Tuple[float, float]) -> Tuple[float, float]:
        """把 SoapySDR RangeList / 单个 Range / 已有 tuple 归一成 (min, max)。

        来源: include/SoapySDR/Types.hpp:91 —— 整个 RangeList 的最小=front().minimum()，
        最大=back().maximum()。Python SWIG 对象用 .minimum()/.maximum() 方法访问。
        注意 getGainRange()（Device.hpp:759）返回单个 Range 而非 RangeList，
        而 getFrequencyRange()/getSampleRateRange() 返回 RangeList，二者都要兼容。
        """
        try:
            if not rl:
                return default
            # 单个 Range 对象（带 minimum()/maximum() 方法）
            if hasattr(rl, "minimum") and hasattr(rl, "maximum"):
                return (float(rl.minimum()), float(rl.maximum()))
            if isinstance(rl, (tuple, list)):
                if len(rl) == 2 and all(isinstance(x, (int, float)) for x in rl):
                    return (float(rl[0]), float(rl[1]))
                # RangeList 形态：元素是 Range 对象
                lo = min(float(r.minimum()) for r in rl)
                hi = max(float(r.maximum()) for r in rl)
                return (lo, hi)
        except Exception:
            pass
        return default

    @staticmethod
    def _range_tuple_strict(rl) -> Optional[Tuple[float, float]]:
        """同 _range_tuple，但**不回落默认值**：查不到就返回 None（未知）。

        能力表红线：未知项不许填一个「看起来合理」的宽范围冒充已知。
        """
        try:
            if not rl:
                return None
            if hasattr(rl, "minimum") and hasattr(rl, "maximum"):
                lo, hi = float(rl.minimum()), float(rl.maximum())
            elif isinstance(rl, (tuple, list)):
                if len(rl) == 2 and all(isinstance(x, (int, float)) for x in rl):
                    lo, hi = float(rl[0]), float(rl[1])
                else:
                    lo = min(float(r.minimum()) for r in rl)
                    hi = max(float(r.maximum()) for r in rl)
            else:
                return None
        except Exception:
            return None
        if not (math.isfinite(lo) and math.isfinite(hi) and lo < hi):
            return None
        return (lo, hi)

    # ------------------------------------------------------------------
    # 枚举：优先 Python 绑定，降级 SoapySDRUtil CLI，都没有返回 []
    # ------------------------------------------------------------------
    @classmethod
    def list_devices(cls) -> List[Dict[str, Any]]:
        """枚举真实 SoapySDR 设备。

        返回每台设备一个 dict，键：driver/label/serial/manufacturer/product/
        gain_range/freq_range/sample_rate_range/device_args。
        无 Python 绑定、无 CLI 时返回 [] 并 log warning（**绝不**返回假设备）。
        """
        # 路径 1：原生 Python 绑定
        try:
            import SoapySDR
        except ImportError as e:
            SoapySDR = None
            # 红线提示：明确告诉用户怎么装，绝不静默把"没装库"和"没插设备"混为一谈
            _warn_once(
                "soapy_no_python_binding",
                "未安装 SoapySDR Python 绑定：pip install SoapySDR"
                "（并按需安装 SoapyRTLSDR/SoapyHackRF/SoapyPlutoSDR 等驱动模块）。"
                "原始导入错误: %s", e)
        except Exception as e:
            SoapySDR = None
            logger.warning(f"SoapySDR 导入异常，降级 CLI 枚举: {e}")

        if SoapySDR is not None:
            try:
                return cls._enumerate_via_python(SoapySDR)
            except Exception as e:
                logger.warning(f"SoapySDR Python 枚举失败，尝试 CLI 降级: {e}")

        # 路径 2：SoapySDRUtil --find 命令行
        try:
            return cls._enumerate_via_cli()
        except Exception as e:
            _warn_once("soapy_cli_failed", "SoapySDRUtil CLI 枚举失败: %s", e)

        # 路径 3：都没有 → 优雅报设备未找到（返回 []，绝不抛异常到上层、绝不假设备）
        _warn_once(
            "soapy_absent",
            "未发现 SoapySDR：既无 Python 绑定(import SoapySDR)，"
            "也无 SoapySDRUtil CLI。请执行 pip install SoapySDR，"
            "并按需安装对应 Soapy* 驱动模块后再识别硬件。"
            "（此为本进程内静态环境事实，仅首次报出，不予重复刷屏）"
        )
        return []

    @classmethod
    def _enumerate_via_python(cls, SoapySDR) -> List[Dict[str, Any]]:
        """用 SoapySDR.Device.enumerate() 枚举；并 best-effort 开probe读真实范围。

        来源: lib/Factory.cpp:41 enumerate(args)；:101 注入 driver 键。
        enumerate 本身只给身份信息（label/serial/manufacturer/product），
        范围要 open 后查 getFrequencyRange 等（Device.hpp:855/909/759）。
        """
        # 来源: lib/Factory.cpp:41 — enumerate() 无参即枚举全部
        found = SoapySDR.Device.enumerate() or []
        out: List[Dict[str, Any]] = []
        for kwargs in found:
            # SWIG Kwargs 支持 .to_dict() 或直接迭代；做一次防御性转换
            try:
                info = dict(kwargs)
            except Exception:
                info = {str(k): str(getattr(kwargs, k, "")) for k in getattr(kwargs, "keys", lambda: [])()}
            entry = {
                "driver": info.get("driver", "?"),
                "label": info.get("label", info.get("driver", "SoapySDR device")),
                "serial": info.get("serial", ""),
                "manufacturer": info.get("manufacturer", ""),
                "product": info.get("product", ""),
                "gain_range": None,
                "freq_range": None,
                "sample_rate_range": None,
                "bandwidth_range": None,
                "antennas": None,
                "tx_channels": None,
                "capability_status": {},
                "capability_note": {},
                "device_args": dict(info),
            }
            status = entry["capability_status"]
            note = entry["capability_note"]

            def _probe_range(key: str, fn) -> Optional[Tuple[float, float]]:
                try:
                    val = cls._range_tuple_strict(fn())
                except Exception as e:
                    status[key] = "unknown"
                    note[key] = f"查询失败：{e}"
                    return None
                if val is None:
                    status[key] = "unknown"
                    note[key] = "驱动返回空/非法范围"
                    return None
                status[key] = "probed"
                return val

            # best-effort 开一下设备读范围；失败不影响身份列出
            try:
                probe = SoapySDR.Device(info)
                rx = getattr(SoapySDR, "SOAPY_SDR_RX", 1)
                tx = getattr(SoapySDR, "SOAPY_SDR_TX", 2)
                entry["freq_range"] = _probe_range(
                    "freq_range", lambda: probe.getFrequencyRange(rx, 0))
                entry["sample_rate_range"] = _probe_range(
                    "sample_rate_range", lambda: probe.getSampleRateRange(rx, 0))
                entry["gain_range"] = _probe_range(
                    "gain_range", lambda: probe.getGainRange(rx, 0))
                entry["bandwidth_range"] = _probe_range(
                    "bandwidth_range", lambda: probe.getBandwidthRange(rx, 0))
                # 天线口（Device.hpp:listAntennas）——查不到就是未知，不猜
                try:
                    ants = list(probe.listAntennas(rx, 0) or [])
                except Exception as e:
                    status["antenna"] = "unknown"
                    note["antenna"] = f"查询失败：{e}"
                else:
                    entry["antennas"] = ants
                    status["antenna"] = "probed"
                    if not ants:
                        note["antenna"] = "驱动未报告任何天线口"
                # 驱动级 TX 通道数（Device.hpp:1155）：用于区分"驱动可发"
                # 与"本通用路径未接 TX 写流"，不影响 supports_tx()。
                try:
                    entry["tx_channels"] = int(probe.getNumTxChannels(tx))
                    status["tx"] = "probed"
                except Exception as e:
                    entry["tx_channels"] = None
                    status["tx"] = "unknown"
                    note["tx"] = f"查询失败：{e}"
                    logger.debug(f"probe {entry['label']} 读 TX 通道数失败(忽略): {e}")
                # 来源: lib/Factory.cpp:133 make() 返回的指针由 Python 端 del/close 释放
                del probe
            except Exception as e:
                for key in ("freq_range", "sample_rate_range", "gain_range",
                            "bandwidth_range", "antenna", "tx"):
                    status.setdefault(key, "unknown")
                    note.setdefault(key, f"打开设备查询失败：{e}")
                logger.debug(f"probe {entry['label']} 读范围失败(忽略): {e}")
            out.append(entry)
        return out

    @classmethod
    def _enumerate_via_cli(cls) -> List[Dict[str, Any]]:
        """解析 ``SoapySDRUtil --find=""`` 的文本输出。

        SoapySDRUtil --find 输出形如：
            Found device 0:
            :driver=rtlsdr
            :label=Generic RTL2832U :: 00000001
            :serial=00000001
            :manufacturer=...
            :product=...
        CLI 路径拿不到频率/增益范围（那要 open 后查询），相关字段留 None。
        """
        import subprocess
        try:
            proc = subprocess.run(
                ["SoapySDRUtil", "--find", ""],
                capture_output=True, text=True, timeout=10,
            )
        except FileNotFoundError:
            raise RuntimeError("SoapySDRUtil 未安装")
        if proc.returncode != 0:
            raise RuntimeError(f"SoapySDRUtil --find 退出码 {proc.returncode}: {proc.stderr[:200]}")

        out: List[Dict[str, Any]] = []
        cur: Dict[str, str] = {}
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith(":") and "=" in line:
                k, v = line[1:].split("=", 1)
                cur[k.strip()] = v.strip()
            elif line.startswith("Found device") and cur:
                out.append(cur)
                cur = {}
        if cur:
            out.append(cur)

        result = []
        _why = "CLI 枚举只给身份，未打开设备查询范围"
        for info in out:
            result.append({
                "driver": info.get("driver", "?"),
                "label": info.get("label", info.get("driver", "SoapySDR device")),
                "serial": info.get("serial", ""),
                "manufacturer": info.get("manufacturer", ""),
                "product": info.get("product", ""),
                "gain_range": None,
                "freq_range": None,
                "sample_rate_range": None,
                "bandwidth_range": None,
                "antennas": None,
                "tx_channels": None,
                "capability_status": {
                    "freq_range": "unknown", "sample_rate_range": "unknown",
                    "gain_range": "unknown", "bandwidth_range": "unknown",
                    "antenna": "unknown", "tx": "unknown",
                },
                "capability_note": {
                    "freq_range": _why, "sample_rate_range": _why,
                    "gain_range": _why, "bandwidth_range": _why,
                    "antenna": _why, "tx": _why,
                },
                "device_args": dict(info),
            })
        return result

    # ------------------------------------------------------------------
    # 连接 / 断开：真实打开，失败返回 False（绝不切 mock 报成功）
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        try:
            import SoapySDR
        except Exception as e:
            # 红线：无 Python 绑定时绝不切 mock 报成功；错误原因冒到 UI。
            self.status.error = (
                f"未安装 SoapySDR：pip install SoapySDR（并装对应 Soapy* 驱动）。"
                f"原始导入错误: {e}")
            logger.error(self.status.error)
            self.status.connected = False
            return False

        try:
            # 来源: lib/Factory.cpp:133 Device::make(args) —— Python 端即构造 Device(args)。
            # enumerate() 返回的 dict 可直接传回（官方标准用法）；空串/空 dict 表示自动选第一台。
            self._sdr = SoapySDR.Device(self._device_args)
            self._rx = getattr(SoapySDR, "SOAPY_SDR_RX", 1)
            # 驱动级 TX 通道数：只用于能力展示，不据此声明可发射。
            try:
                tx = getattr(SoapySDR, "SOAPY_SDR_TX", 2)
                self._num_tx_channels = int(self._sdr.getNumTxChannels(tx))
            except Exception as e:
                logger.debug("SoapySDR 读 TX 通道数失败(保持未知): %s", e)
        except Exception as e:
            self.status.error = f"SoapySDR 打开设备失败({self._args_str}): {e}"
            logger.error(self.status.error)
            self._sdr = None
            self.status.connected = False
            return False

        try:
            # 来源: Device.hpp:267 setupStream(direction, format, channels)
            self._stream = self._sdr.setupStream(self._rx, self._STREAM_FORMAT, [self._chan])
            # 来源: Device.hpp:339 附近 activateStream —— 必须先 activate 才能 readStream
            self._sdr.activateStream(self._stream)
        except Exception as e:
            self.status.error = f"SoapySDR 建立接收流失败: {e}"
            logger.error(self.status.error)
            self.disconnect()
            return False

        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()
        # 回读硬件真实范围/参数，对齐软件状态
        try:
            self.readback_hw_state()
        except Exception as e:
            logger.warning(f"SoapySDR 回读状态失败(忽略): {e}")
        logger.info(f"SoapySDR 设备已连接: {self.device.name} (args={self._args_str})")
        return True

    def disconnect(self):
        if self._sdr is not None:
            try:
                if self._stream is not None:
                    # 来源: Device.hpp:267 附近 deactivateStream/closeStream
                    self._sdr.deactivateStream(self._stream)
                    self._sdr.closeStream(self._stream)
            except Exception as e:
                logger.debug(f"关闭流时忽略错误: {e}")
            self._stream = None
            try:
                del self._sdr  # Python 端释放 Device（对应 C++ delete）
            except Exception:
                pass
            self._sdr = None
        self._num_tx_channels = None  # 断开后不再持有驱动上报值（回到未知）
        super().disconnect()

    # ------------------------------------------------------------------
    # 调谐 / 增益 / 采样率：真实写硬件，异常记入 status.error
    # ------------------------------------------------------------------
    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._sdr is None:
            return True
        try:
            # 来源: Device.hpp:801 setFrequency(RX, chan, freq)
            self._sdr.setFrequency(self._rx, self._chan, float(freq_hz))
            return True
        except Exception as e:
            self.status.error = f"setFrequency({freq_hz}) 失败: {e}"
            return False

    def set_frequency(self, freq_hz: float) -> bool:
        """调谐后回读 ``getFrequency()``；读不到就保留请求值并标明是请求值。

        Soapy 驱动可能把请求频率量化到硬件步进（或直接拒绝后仍报旧值），
        只把请求值写进状态会让界面显示一个设备并未接受的频点。
        """
        ok = super().set_frequency(freq_hz)
        if not ok or self._sdr is None:
            return ok
        try:
            actual = float(self._sdr.getFrequency(self._rx, self._chan))
        except Exception as e:  # noqa: BLE001 - 驱动可能没有 getFrequency
            logger.debug("Soapy getFrequency 回读失败（保留请求值）: %s", e)
            self.status.frequency_source = "request"
            return True
        if math.isfinite(actual) and actual > 0:
            self.status.frequency_hz = actual
            self.status.frequency_source = "readback"
        else:
            # 回读值不可用：保留请求值，并如实标成 request。
            self.status.frequency_source = "request"
        return True

    @staticmethod
    def _coerce_rate(value) -> Optional[float]:
        """把驱动返回的速率转成正的有限值；不可用时返回 None（不编造）。"""
        try:
            rate = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(rate) or rate <= 0:
            return None
        return rate

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._sdr is None:
            self._applied_sample_rate_hz = None
            return True
        try:
            # 来源: Device.hpp:884 setSampleRate(RX, chan, rate) —— 返回值是
            # 硬件真正接受的速率（Soapy 会吸附到合法档位），不能丢弃：
            # 丢它就会把请求值当成生效值显示。
            actual = self._sdr.setSampleRate(self._rx, self._chan, float(rate_hz))
            self._applied_sample_rate_hz = self._coerce_rate(actual)
            return True
        except Exception as e:
            self._applied_sample_rate_hz = None
            self.status.error = f"setSampleRate({rate_hz}) 失败: {e}"
            return False

    def _resolve_applied_sample_rate(self, rate_hz: float) -> float:
        """返回驱动确认的实际速率；驱动没给回值时才退回请求值。"""
        actual = self._applied_sample_rate_hz
        return float(actual) if actual else float(rate_hz)

    def _apply_gain(self, gain_db: float) -> bool:
        if self._sdr is None:
            return True
        try:
            # 来源: Device.hpp:725 setGain(RX, chan, db) —— 总增益
            self._sdr.setGain(self._rx, self._chan, float(gain_db))
            return True
        except Exception as e:
            self.status.error = f"setGain({gain_db}) 失败: {e}"
            return False

    def set_gain(self, gain_db: float) -> bool:
        """设置增益并回读驱动实际值（Soapy 会吸附到增益档位）。

        基类成功后会写请求值；这里用 ``getGain()`` 覆盖成驱动确认值，
        状态面板/读数才不会把请求值冒充成硬件实际值。
        """
        ok = super().set_gain(gain_db)
        if ok and self._sdr is not None:
            try:
                actual = float(self._sdr.getGain(self._rx, self._chan))
            except Exception as e:  # noqa: BLE001 - 读不到就保留请求值
                logger.debug("Soapy getGain 回读失败（保留软件记录值）: %s", e)
            else:
                if math.isfinite(actual):
                    self.status.gain_db = actual
        return ok

    def get_gain(self) -> float:
        """回读驱动实际增益；读不到才退回软件记录值。"""
        if self._sdr is not None:
            try:
                actual = float(self._sdr.getGain(self._rx, self._chan))
            except Exception as e:  # noqa: BLE001 - 回读失败退回软件值
                logger.debug("Soapy getGain 回读失败（退回软件记录值）: %s", e)
            else:
                if math.isfinite(actual):
                    return actual
        return self.status.gain_db

    def gain_readback_source(self) -> str:
        return "驱动回读档位（SoapySDR getGain）"

    def _apply_agc(self, enabled: bool) -> bool:
        if self._sdr is not None:
            try:
                # 来源: Device.hpp:708 setGainMode(RX, chan, automatic)
                self._sdr.setGainMode(self._rx, self._chan, bool(enabled))
            except Exception as e:
                self.status.error = f"setGainMode 失败: {e}"
                return False
        return True

    def _apply_bandwidth(self, bw_hz: float) -> bool:
        if self._sdr is not None and bw_hz > 0:
            try:
                # 来源: Device.hpp:921 setBandwidth(RX, chan, bw)
                self._sdr.setBandwidth(self._rx, self._chan, float(bw_hz))
                return True
            except Exception as e:
                self.status.error = f"setBandwidth({bw_hz}) 失败: {e}"
                return False
        return True

    # ------------------------------------------------------------------
    #  对齐 SDR++ soapy_source 的设备控制面
    #  （来源: source_modules/soapy_source/src/main.cpp）
    # ------------------------------------------------------------------
    def set_antenna(self, antenna: str) -> bool:
        """选择接收天线。

        来源: soapy_source/main.cpp:334 setAntenna(RX,chan,antennaList[uiAntennaId])
              + :372 listAntennas()。天线名必须来自 list_antennas()，不写死。
        """
        if self._sdr is None:
            return False
        try:
            self._sdr.setAntenna(self._rx, self._chan, str(antenna))
            self._antenna = str(antenna)
            return True
        except Exception as e:
            self.status.error = f"setAntenna({antenna}) 失败: {e}"
            return False

    def list_antennas(self) -> List[str]:
        """列出本设备可用接收天线。来源: soapy_source/main.cpp:172。"""
        if self._sdr is None:
            return []
        try:
            return list(self._sdr.listAntennas(self._rx, self._chan))
        except Exception:
            return []

    def select_bandwidth_by_samplerate(self, sample_rate: float) -> float:
        """自动选一个 ≥ 采样率的最小模拟带宽。

        来源: soapy_source/main.cpp:101-115 selectBwBySr()：
          从大到小遍历带宽列表，挑第一个 >= samplerate 的；
          SDR++ 里 "Auto" 档(-1)就走这个逻辑。
        """
        if self._sdr is None:
            return 0.0
        try:
            rng = self._sdr.getBandwidthRange(self._rx, self._chan)
            cands = [float(r.minimum()) for r in rng]
        except Exception:
            return 0.0
        chosen = 0.0
        for bw in sorted(cands, reverse=True):   # 从大到小
            if bw >= sample_rate:
                chosen = bw
            else:
                break
        if chosen > 0:
            self.set_bandwidth(chosen)
        return chosen

    def recommended_block_size(self) -> int:
        """推荐单次 readStream 块大小。

        来源: soapy_source/main.cpp:501 blockSize = sampleRate/200.0f
        即每秒约 200 块。
        """
        sr = self.status.sample_rate_hz or self.device.sample_rate_range[1]
        return max(512, int(sr / 200.0))


    def readback_hw_state(self) -> bool:
        """open 后回读真实频率/采样率/增益/范围，对齐 SDRDevice 与 SDRStatus。"""
        if self._sdr is None:
            return False
        got = 0
        try:
            # 来源: Device.hpp:892 getSampleRate(RX, chan)
            self.status.sample_rate_hz = float(
                self._sdr.getSampleRate(self._rx, self._chan))
            got += 1
        except Exception:
            pass
        try:
            # 来源: Device.hpp:789 附近 getFrequency(RX, chan)
            self.status.frequency_hz = float(
                self._sdr.getFrequency(self._rx, self._chan))
            self.status.frequency_source = "readback"
            got += 1
        except Exception:
            pass
        try:
            self.status.gain_db = float(
                self._sdr.getGain(self._rx, self._chan))
            got += 1
        except Exception:
            pass
        # 用硬件真实范围覆盖构造时的占位范围。三项分开查：任一项失败不
        # 连坐其他项；采样率范围真查到了就把来源登记为 probed，界面才允许
        # 据此生成可见档位（缺省 unknown → 不回退成「按声明」）。
        try:
            fr = self._sdr.getFrequencyRange(self._rx, self._chan)
            self.device.frequency_range = self._range_tuple(
                fr, self.device.frequency_range)
        except Exception:
            pass
        try:
            sr = self._sdr.getSampleRateRange(self._rx, self._chan)
            sr_rng = self._range_tuple_strict(sr)
            if sr_rng is not None:
                self.device.sample_rate_range = sr_rng
                self.device.capability_status["sample_rate_range"] = "probed"
        except Exception:
            pass
        try:
            gr = self._sdr.getGainRange(self._rx, self._chan)
            self.device.max_gain = float(gr.maximum())
        except Exception:
            pass
        return got > 0

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        """读取 IQ 样本。

        来源: Device.hpp:352 readStream(stream, buffs, n, flags, timeNs, timeoutUs)。
        Python 用法: MeasureDelay.py:108 —— 传 [numpy_complex64 数组]，
        返回 status 对象，有效样本数取 status.ret。

        红线：
          - 未连接/无流 → 返回 None（错误态）；
          - 已连接但 readStream 超时(-1)/溢出(-2) → 返回 **空 complex64 数组**（len=0），
            绝不把上面零填充的 buff 当真实 IQ 吐出去（那是造假数据）。
            上层 _poll_sdr_iq 见 iq.size<64 即跳过本帧，天然兼容。
          - 抛异常（设备拔出/流损坏）→ 返回 None 并把原因写进 status.error。
        """
        if not self.status.connected or self._sdr is None or self._stream is None:
            return None
        try:
            buff = np.zeros(int(num_samples), np.complex64)
            # 500ms 超时；来源: MeasureDelay.py:107 timeout_us = 5e5。
            # flags=0, timeNs=0（单次读，不靠硬件时间戳同步）。
            status = self._sdr.readStream(
                self._stream, [buff], int(num_samples), 0, 0, timeoutUs=500_000)
            n_read = int(getattr(status, "ret", 0))
            if n_read <= 0:
                # 来源: Device.hpp readStream 返回负数为状态码（TIMEOUT=-1/OVERFLOW=-2）。
                # 这些是"本帧没新样本"的瞬时状态，不是硬错误：返回空数组让上层跳过。
                if n_read < 0:
                    logger.debug(
                        f"readStream 瞬时状态码 {n_read}（本帧无新样本，跳过）")
                return np.empty(0, np.complex64)
            self._samples_read += n_read
            return buff[:n_read].copy()
        except Exception as e:
            self.status.error = f"read_samples 异常: {e}"
            return None


class USRPBackend(SDRBackend):
    """
    USRP 后端（Ettus Research / NI）。

    使用 UHD 库（uhd Python 绑定）。收发都走 UHD：
      - 接收：``recv_num_samps``（现有路径）
      - 发射：``get_tx_stream`` + ``tx_streamer.send``，真实写 UHD 发送流
    支持多种 USRP 型号（B200/B210/X300/X310/N200/N210 等）。
    需要安装：pip install uhd 以及 libuhd 系统库。

    TX 说明：发送流路径按 UHD Python API 实现，但**未在真机验证**；
    ``device.tx_verified=False``，UI/工具面须标「未验证」。
    """

    def __init__(self, device_args: str = ""):
        device = SDRDevice(
            device_type="usrp",
            device_id=f"usrp_{device_args or 'default'}",
            name=f"USRP ({device_args or 'auto'})",
            frequency_range=(10000, 6000000000),
            sample_rate_range=(100000, 56000000),
            max_gain=76.0,
            supports_iq=True,
            supports_tx=True,
            tx_verified=False,  # 尚未在真机验证过发射链路
            # UHD 的覆盖范围是 API 级常量，未按具体母板/子板探测 → declared。
            capability_status={
                "freq_range": "declared",
                "sample_rate_range": "declared",
                "gain_range": "declared",
                "tx": "declared",
            },
            capability_note={
                "freq_range": "UHD 覆盖范围，未按具体母板/子板查询",
                "sample_rate_range": "UHD 通用范围，未按具体设备查询",
            },
        )
        super().__init__(device)
        self._device_args = device_args
        self._usrp = None
        self._rx_stream = None
        self._tx_streamer = None
        # 来源: gqrx/src/qtgui/ioconfig.cpp:279-302 —— USRP 默认 1 MS/s 起步档
        # （GQRX ioconfig 里 USRP start_sample_rate=1e6）。
        self.status.sample_rate_hz = 1_000_000.0

    def connect(self) -> bool:
        try:
            import uhd
            self._usrp = uhd.usrp.MultiUSRP(self._device_args)
        except Exception as e:
            self.status.connected = False
            self.status.link_state = "failed"
            self.status.error = (
                f"USRP 连接失败: {type(e).__name__}: {e}。"
                "请确认 UHD 库与设备连接（uhd_find_devices 能否枚举到）。")
            logger.warning(self.status.error)
            return False
        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()
        if self.status.sample_rate_hz is None:
            self.status.sample_rate_hz = self._FALLBACK_SAMPLE_RATE_HZ
        # 对照 RTL/RtlTcp/Pluto 纪律：打开后逐项显式下发，不停在出厂/上次残留。
        if not self.set_sample_rate(self.status.sample_rate_hz):
            return self._connect_init_failed("采样率")
        target_freq = self.status.frequency_hz or 98_000_000.0
        if not self.set_frequency(target_freq):
            return self._connect_init_failed("中心频率")
        if not self.set_gain(self.status.gain_db):
            return self._connect_init_failed("增益")
        # UHD 可能把请求值改写到硬件支持的档位：status 以回读为准。
        self.readback_hw_state()
        return True

    def _connect_init_failed(self, what: str) -> bool:
        detail = self.status.error or "硬件拒绝下发"
        self.status.connected = False
        self.status.link_state = "failed"
        self.status.error = f"USRP 连接初始化{what}下发失败: {detail}"
        logger.warning(self.status.error)
        self._usrp = None
        return False

    def readback_hw_state(self) -> bool:
        if self._usrp is None:
            return False
        got = 0
        for getter, field in (
                ("get_rx_freq", "frequency_hz"),
                ("get_rx_rate", "sample_rate_hz"),
                ("get_rx_gain", "gain_db")):
            try:
                v = float(getattr(self._usrp, getter)())
                # 增益 0 dB 合法；频率/采样率回读 0 = 未生效，不拿它盖掉状态
                if v <= 0 and field != "gain_db":
                    continue
                setattr(self.status, field, v)
                got += 1
            except Exception:
                pass
        return got > 0

    def disconnect(self):
        if self._rx_stream:
            try:
                self._rx_stream = None
            except Exception:
                pass
        if self._usrp:
            self._usrp = None
        self._tx_streamer = None
        super().disconnect()

    def _apply_frequency(self, freq_hz: float) -> bool:
        if self._usrp is None:
            return True
        try:
            import uhd
            self._usrp.set_rx_freq(uhd.libpyuhd.types.tune_request(freq_hz))
        except Exception:
            return False
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        if self._usrp is None:
            return True
        try:
            self._usrp.set_rx_rate(rate_hz)
        except Exception:
            return False
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        if self._usrp is None:
            return True
        try:
            self._usrp.set_rx_gain(gain_db)
        except Exception:
            return False
        return True

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or not self._usrp:
            return None
        try:
            samples = self._usrp.recv_num_samps(num_samples, self.status.frequency_hz,
                                                   self.status.sample_rate_hz, [0], 0.1)
            if samples is not None and len(samples) > 0:
                self._samples_read += len(samples[0])
                return samples[0]
            return None
        except Exception:
            return None

    # ── 发射 ──────────────────────────────────────────────────────────
    def _tx_ready(self) -> bool:
        return self._usrp is not None and callable(
            getattr(self._usrp, "get_tx_stream", None))

    def _ensure_tx_stream(self) -> bool:
        """惰性建立 UHD 发送流（get_tx_stream），失败如实报错。"""
        if self._tx_streamer is not None:
            return True
        if not self.status.connected or self._usrp is None:
            return False
        try:
            import uhd
            args = uhd.usrp.StreamArgs("fc32", "sc16")
            args.channels = [0]
            self._tx_streamer = self._usrp.get_tx_stream(args)
            self._usrp.set_tx_rate(float(self.status.sample_rate_hz or 1e6))
            self._usrp.set_tx_gain(float(self.status.gain_db))
        except Exception as e:  # noqa: BLE001 - 驱动异常转为如实失败
            self.status.error = f"[TX] 建立 UHD 发送流失败: {type(e).__name__}: {e}"
            self._tx_streamer = None
            return False
        return True

    def write_tx(self, iq_samples: np.ndarray) -> bool:
        """真实写 UHD 发送流（tx_streamer.send）。

        未过人工确认授权的直调一律拒绝；写失败落 status.error 并返回 False。
        """
        from .tx_guard import reject_unless_authorized
        if not reject_unless_authorized(self):
            self._note_tx_reject()
            return False
        if not self._ensure_tx_stream():
            return False
        try:
            import uhd
            buff = np.asarray(iq_samples, dtype=np.complex64).ravel()
            if buff.size == 0:
                return True
            # 频点跟随当前后端状态（TxSession 在每次发波前 set_frequency）。
            self._usrp.set_tx_freq(uhd.libpyuhd.types.tune_request(
                float(self.status.frequency_hz)))
            md = uhd.types.TXMetadata()
            md.start_of_burst = False
            md.end_of_burst = False
            md.has_time_spec = False
            ret = self._tx_streamer.send(md, buff)
            n = int(ret[0]) if isinstance(ret, tuple) else int(ret)
            if n <= 0:
                self.status.error = f"[TX] UHD send 返回 {n}（未写入样本）"
                return False
            return True
        except Exception as e:  # noqa: BLE001
            self.status.error = f"[TX] UHD 写波异常: {type(e).__name__}: {e}"
            return False


class PlutoSDRBackend(SDRBackend):
    """
    ADALM-PLUTO (PlutoSDR) 后端 —— 基于 AD9361 射频收发器。

    硬件能力（出厂固件）：
      - 频率：325 MHz – 3.8 GHz（解锁 AD9361 校准表后可到 70 MHz – 6 GHz）
      - 采样率：最高 61.44 MSPS，常用 2.5 – 10 MSPS
      - 接收：1x1 MIMO（RX only 默认；TX 另开通道）
      - 增益：RX 0 – 73 dB（manual / slow_attack / fast_attack AGC）

    接入方式（按优先级）：
      1) pylibiio（``import iio``）—— 原生 IIO 接口，USB / 网络直连；
         URI 形如 ``ip:192.168.2.1``（USB gadget 网卡）、``ip:pluto.local``、
         ``usb:1.2.3``（裸 USB 总线地址）。
      2) SoapySDR + SoapyPlutoSDR —— ``SoapySDR.Device(dict(driver="plutosdr"))``。
      3) 两者都没有 —— 类仍可导入，enumerate() 返回 []，connect() 返回 False，
         status.error 标注 "[未连接-无libiio/SoapySDR驱动]"，**绝不**假装收数据。

    IIO 属性映射（来源: AD9361 Linux 驱动Documentation/iio/ 与
      Analog Devices wiki "AD9361 IIO Device Tree Bindings"）：
      - 本地振荡器中心频率  -> ``ad9361-phy`` 通道 ``altvoltage0``(RX_LO)/``altvoltage1``(TX_LO)
        的 ``frequency`` 属性；本后端 RX 默认写 ``altvoltage0``。
      - RX 采样率            -> ``ad9361-phy`` 通道 ``voltage0``(RX) 的 ``sampling_frequency``
      - RX 模拟带宽          -> ``ad9361-phy`` 通道 ``voltage0`` 的 ``rf_bandwidth``
      - RX 增益模式          -> ``ad9361-phy`` 通道 ``voltage0`` 的 ``gain_control_mode``
        （manual / slow_attack / fast_attack）
      - RX 硬件增益          -> ``ad9361-phy`` 通道 ``voltage0`` 的 ``hardwaregain``
      - 数据流 buffer        -> ``cf-ad9361-lpc`` 设备，通道 ``voltage0``（16-bit I/Q 交错）

    安装：``pip install pylibiio`` 或 ``apt install libiio-dev python3-libiio``。
    """

    # 出厂频率范围（AD9361 校准表默认）。解锁后可到 70e6 – 6000e6。
    FREQ_MIN_HZ = 325_000_000.0
    FREQ_MAX_HZ = 3_800_000_000.0
    # 解锁后的扩展范围（仅在显式 unlock=True 时使用）
    FREQ_MIN_UNLOCKED_HZ = 70_000_000.0
    FREQ_MAX_UNLOCKED_HZ = 6_000_000_000.0
    # AD9361 采样率量程
    SR_MIN_HZ = 521_000.0
    SR_MAX_HZ = 61_440_000.0
    # RX 增益量程（dB）
    GAIN_MIN_DB = 0.0
    GAIN_MAX_DB = 73.0
    # 网络模式下 PlutoSDR 固定 USB gadget IP
    DEFAULT_NET_URI = "ip:192.168.2.1"

    def __init__(self, uri: Optional[str] = None, unlocked: bool = False,
                 device_info: Optional[Dict[str, Any]] = None):
        """
        :param uri: IIO context URI；None 时由 enumerate() 决定（先 USB 后网络）。
        :param unlocked: True 时按 70 MHz – 6 GHz 声明频率范围（需固件已解锁）。
        :param device_info: enumerate 阶段拿到的身份信息（label/serial/...）。
        """
        info = device_info or {}
        if unlocked:
            freq_range = (self.FREQ_MIN_UNLOCKED_HZ, self.FREQ_MAX_UNLOCKED_HZ)
        else:
            freq_range = (self.FREQ_MIN_HZ, self.FREQ_MAX_HZ)
        device = SDRDevice(
            device_type="plutosdr",
            device_id=f"plutosdr_{uri or 'auto'}",
            name=info.get("label") or "PlutoSDR (ADALM-PLUTO)",
            frequency_range=freq_range,
            sample_rate_range=(self.SR_MIN_HZ, self.SR_MAX_HZ),
            max_gain=self.GAIN_MAX_DB,
            supports_iq=True,
            supports_tx=True,  # AD9361 半双工收发
            tx_verified=False,  # 发射链路尚未在真机验证
            # 出厂规格来自 AD9361/ADALM-PLUTO 数据手册 → declared。
            capability_status={
                "freq_range": "declared",
                "sample_rate_range": "declared",
                "gain_range": "declared",
                "tx": "declared",
            },
            capability_note={"tx": "AD9361 半双工发射，真机链路未验证"},
        )
        super().__init__(device)
        self._uri = uri
        self._unlocked = unlocked
        self._serial = info.get("serial", "")
        # 运行时句柄
        self._ctx = None           # iio.Context
        self._phy = None           # iio.Device (ad9361-phy)
        self._rx_chan = None       # iio.Channel (voltage0)
        self._rx_lo_chan = None    # iio.Channel (altvoltage0 = RX_LO)
        self._stream_dev = None    # iio.Device (cf-ad9361-lpc / cf-ad9361-dds-core-lpc)
        self._buffer = None        # iio.Buffer
        self._soapy = None         # SoapySDR.Device（备用路径）
        self._soapy_stream = None
        self._backend_lib = None   # "iio" / "soapy" / None
        # TX 句柄（iio: cf-ad9361-dds-core-lpc + TX_LO altvoltage1；soapy: TX 流）
        self._tx_stream_dev = None
        self._tx_lo_chan = None
        self._tx_phy_chan = None
        self._tx_dir = None
        # 默认参数（未连接时也记录，便于上层查询）
        self.status.sample_rate_hz = 2_000_000.0
        self.status.frequency_hz = 1_690_000_000.0  # GK-2A LR-M 默认

    # ------------------------------------------------------------------
    # 驱动可用性探测
    # ------------------------------------------------------------------
    @classmethod
    def _try_import_iio(cls):
        """尝试 import iio；失败返回 None。"""
        try:
            import iio  # pylibiio
            return iio
        except Exception:
            return None

    @classmethod
    def _try_import_soapy(cls):
        """尝试 import SoapySDR；失败返回 None。"""
        try:
            import SoapySDR
            return SoapySDR
        except Exception:
            return None

    @classmethod
    def driver_status(cls) -> Dict[str, Any]:
        """返回当前环境驱动可用性摘要（供上层 UI / 日志展示）。"""
        return {
            "pylibiio": cls._try_import_iio() is not None,
            "soapysdr": cls._try_import_soapy() is not None,
            "note": "需要 pip install pylibiio 或 apt install libiio-dev python3-libiio"
                    "（或 SoapySDR + SoapyPlutoSDR）。",
        }

    # ------------------------------------------------------------------
    # enumerate()：扫描 USB / 网络 PlutoSDR
    # ------------------------------------------------------------------
    @classmethod
    def enumerate(cls) -> List[Dict[str, Any]]:
        """扫描可用 PlutoSDR 设备。

        优先用 pylibiio 的 ``iio.scan_contexts()``（USB 枚举）；
        失败/不可用时回退探测默认网络 URI ``ip:192.168.2.1``（短超时）。
        无任何驱动时返回 []，不抛异常。

        返回形如：
            [{"driver": "plutosdr", "uri": "ip:192.168.2.1",
              "name": "PlutoSDR", "serial": "...",
              "freq_range": (325e6, 3.8e9), "sample_rate_range": (...),
              "gain_range": (0.0, 73.0)}]
        """
        out: List[Dict[str, Any]] = []
        iio_mod = cls._try_import_iio()

        if iio_mod is not None:
            # 路径 1：libiio scan_contexts() —— 枚举 USB / 网络 / 串行上下文
            try:
                contexts = iio_mod.scan_contexts() or {}
                # scan_contexts() 返回 dict: {uri: description}
                for uri, desc in contexts.items():
                    uri_s = str(uri)
                    desc_s = str(desc) if desc else ""
                    # 只保留看起来像 PlutoSDR 的设备
                    if cls._uri_is_pluto(uri_s, desc_s):
                        out.append(cls._make_entry(uri_s, desc_s))
            except Exception as e:
                logger.debug(f"iio.scan_contexts() 失败: {e}")

            # 路径 1b：USB 枚举兜底（scan_contexts 在某些后端下返回空）
            if not out:
                for usb_uri in ("usb:1.2.5", "usb:1.2.4", "usb:1.2.3"):
                    try:
                        probe = iio_mod.Context(usb_uri)
                        # 能打开就认为是 PlutoSDR
                        out.append(cls._make_entry(usb_uri, "PlutoSDR (USB)"))
                        del probe
                        break
                    except Exception:
                        continue

        # 路径 2：网络兜底 —— 尝试默认 192.168.2.1
        if not out:
            net_entry = cls._probe_network_default(iio_mod)
            if net_entry is not None:
                out.append(net_entry)

        if not out:
            # 无设备 / 无驱动：静默返回空列表（调用方不应报错）
            logger.debug("PlutoSDRBackend.enumerate(): 未发现 PlutoSDR 设备"
                         "（无 libiio/SoapySDR 或无硬件）。")
        return out

    @classmethod
    def _uri_is_pluto(cls, uri: str, desc: str) -> bool:
        """启发式判断 IIO 上下文是否为 PlutoSDR。"""
        text = (uri + " " + desc).lower()
        # PlutoSDR 常见标识
        pluto_markers = ("pluto", "adalm", "ad9364", "ad9363a", "192.168.2.1",
                         "1.2.3", "1.2.4", "1.2.5", "1.2.6")
        return any(m in text for m in pluto_markers)

    @classmethod
    def _make_entry(cls, uri: str, desc: str) -> Dict[str, Any]:
        return {
            "driver": "plutosdr",
            "uri": uri,
            "label": desc or "PlutoSDR (ADALM-PLUTO)",
            "name": "PlutoSDR",
            "serial": "",
            "manufacturer": "Analog Devices",
            "product": "ADALM-PLUTO",
            "freq_range": (cls.FREQ_MIN_HZ, cls.FREQ_MAX_HZ),
            "sample_rate_range": (cls.SR_MIN_HZ, cls.SR_MAX_HZ),
            "gain_range": (cls.GAIN_MIN_DB, cls.GAIN_MAX_DB),
            "device_args": {"uri": uri},
        }

    @classmethod
    def _probe_network_default(cls, iio_mod) -> Optional[Dict[str, Any]]:
        """尝试打开默认网络 URI；成功返回 entry，失败/超时返回 None。

        用 socket 先做一次 TCP 连通性探测（短超时），避免 iio.Context 长时间阻塞。
        """
        # 没有 libiio 时后续 iio.Context 必然不可能成功；不要先花 0.5 s 做 TCP
        # 超时探测（本机实测每次枚举固定 0.50 s，2 s 一次的热插拔轮询白烧 25%）。
        if iio_mod is None:
            return None
        import socket
        host = "192.168.2.1"
        # 先 ping TCP 30431 (libiio 网络默认端口)
        try:
            with socket.create_connection((host, 30431), timeout=0.5):
                pass
        except Exception:
            return None
        # 端口通，再用 iio.Context 验证身份
        if iio_mod is None:
            return None
        try:
            ctx = iio_mod.Context(cls.DEFAULT_NET_URI)
            # 校验是否真的是 ad9361
            has_phy = any("ad9361" in (dev.name or "").lower()
                          for dev in ctx.devices)
            del ctx
            if has_phy:
                return cls._make_entry(cls.DEFAULT_NET_URI,
                                       f"PlutoSDR (network {host})")
        except Exception as e:
            logger.debug(f"网络 PlutoSDR 探测失败: {e}")
        return None

    # ------------------------------------------------------------------
    # open / close（connect/disconnect 别名）
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        """打开 PlutoSDR。优先 pylibiio，回退 SoapySDR，都没有则返回 False。"""
        # 先探测驱动
        iio_mod = self._try_import_iio()
        soapy_mod = self._try_import_soapy()

        if iio_mod is None and soapy_mod is None:
            self.status.error = ("[未连接-无libiio/SoapySDR驱动] "
                                 "请 pip install pylibiio 或安装 SoapyPlutoSDR。")
            logger.warning(self.status.error)
            self.status.connected = False
            return False

        # 决定 URI：显式 > 枚举到的第一个 > 默认网络
        uri = self._uri
        if not uri:
            try:
                found = self.enumerate()
                if found:
                    uri = found[0]["uri"]
            except Exception:
                uri = None
        if not uri:
            uri = self.DEFAULT_NET_URI

        # 路径 1：pylibiio 原生
        if iio_mod is not None:
            try:
                return self._open_via_iio(iio_mod, uri)
            except Exception as e:
                self.status.error = f"[libiio] 打开 {uri} 失败: {e}"
                logger.warning(self.status.error)
                # 继续尝试 SoapySDR

        # 路径 2：SoapySDR
        if soapy_mod is not None:
            try:
                return self._open_via_soapy(soapy_mod, uri)
            except Exception as e:
                self.status.error = f"[SoapySDR] 打开 PlutoSDR 失败: {e}"
                logger.error(self.status.error)
                self.status.connected = False
                return False

        self.status.connected = False
        return False

    def _open_via_iio(self, iio_mod, uri: str) -> bool:
        """通过 pylibiio 打开设备并配置 RX 通道。"""
        self._ctx = iio_mod.Context(uri)
        # 找 ad9361-phy
        phy = None
        stream = None
        tx_stream = None
        for dev in self._ctx.devices:
            name = (dev.name or "").lower()
            if "ad9361-phy" in name or "ad9364" in name:
                phy = dev
            elif "cf-ad9361" in name and "dds" in name:
                # TX DMA：cf-ad9361-dds-core-lpc（上变频输入）
                tx_stream = dev
            elif "cf-ad9361" in name and "lpc" in name:
                # RX DMA：cf-ad9361-lpc（不要把 TX 设备当 RX 流）
                stream = dev
        if phy is None:
            raise RuntimeError(f"{uri} 上未找到 ad9361-phy 设备")
        self._phy = phy

        # RX LO: altvoltage0（ad9361-phy 输出通道，控制 RX 本振）
        self._rx_lo_chan = self._find_channel(phy, "altvoltage0", is_output=True)
        # RX 基带通道: voltage0（输入）
        self._rx_chan = self._find_channel(phy, "voltage0", is_output=False)

        # 数据流设备
        if stream is None:
            # 兜底：找任意带 buffer 能力的设备
            for dev in self._ctx.devices:
                if dev.buffer_attrs or dev.channels:
                    stream = dev
                    break
        self._stream_dev = stream

        # 应用默认参数
        self._apply_sample_rate(self.status.sample_rate_hz)
        self._apply_bandwidth(self.status.sample_rate_hz * 0.8)
        self._apply_frequency(self.status.frequency_hz)
        self._apply_gain(self.status.gain_db)

        # 创建 RX buffer（4 MSamples，16-bit I/Q 交错 = 16 MB）
        self._create_iio_buffer(4_000_000)

        # TX 路径：cf-ad9361-dds-core-lpc 输出通道 + TX_LO altvoltage1。
        # 打不开只影响发射，不影响接收（PTT 由 supports_tx() 如实置灰）。
        try:
            self._open_tx_iio(phy, tx_stream)
        except Exception as e:
            logger.warning("[TX] PlutoSDR IIO 发送路径不可用: %s", e)
            self._tx_stream_dev = None

        self._backend_lib = "iio"
        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()
        logger.info(f"PlutoSDR 已通过 libiio 打开: {uri}")
        try:
            self.readback_hw_state()
        except Exception as e:
            logger.debug(f"PlutoSDR 回读状态失败(忽略): {e}")
        return True

    def _find_channel(self, dev, channel_id: str, is_output: Optional[bool] = None):
        """在 iio.Device 上按 channel_id 找 Channel。"""
        for ch in dev.channels:
            if ch.id == channel_id:
                if is_output is None or ch.output == is_output:
                    return ch
        return None

    def _create_iio_buffer(self, num_samples: int):
        """在 stream 设备上创建 RX 采样 buffer。"""
        if self._stream_dev is None:
            return
        # 使能 RX 通道
        rx_ch = None
        for ch in self._stream_dev.channels:
            if not ch.output:
                rx_ch = ch
                break
        if rx_ch is not None:
            rx_ch.enabled = True
        # samples_count 是"每个通道的样本数"；16-bit I/Q 交错
        self._buffer = self._stream_dev.create_buffer(num_samples, circular=False)

    def _open_tx_iio(self, phy, tx_dev) -> bool:
        """配置 IIO 发送路径：TX DMA 设备 + TX_LO(altvoltage1) + TX 采样率。

        来源: AD9361 IIO 设备树 —— TX 流走 ``cf-ad9361-dds-core-lpc`` 的
        output 通道 voltage0/voltage1；TX 本振是 ``altvoltage1``。
        """
        if tx_dev is None:
            raise RuntimeError("未找到 cf-ad9361-dds-core-lpc（TX DMA 设备）")
        for ch in tx_dev.channels:
            if ch.output and ch.id in ("voltage0", "voltage1"):
                ch.enabled = True
        self._tx_stream_dev = tx_dev
        self._tx_lo_chan = self._find_channel(phy, "altvoltage1", is_output=True)
        # TX 采样率/带宽走 ad9361-phy 的 output voltage0 通道
        self._tx_phy_chan = self._find_channel(phy, "voltage0", is_output=True)
        if self._tx_phy_chan is not None:
            try:
                self._tx_phy_chan.attrs["sampling_frequency"].value = str(
                    int(self.status.sample_rate_hz))
            except Exception as e:
                logger.warning("[TX] 写 Pluto TX sampling_frequency 失败: %s", e)
        if self._tx_lo_chan is not None:
            self._tx_lo_chan.attrs["frequency"].value = str(
                int(self.status.frequency_hz))
        return True

    def _open_via_soapy(self, SoapySDR, uri: str) -> bool:
        """通过 SoapySDR + SoapyPlutoSDR 打开。"""
        args = {"driver": "plutosdr"}
        # 把 uri 转成 SoapySDR 能识别的参数
        if uri.startswith("ip:"):
            args["remote"] = uri[3:]
        elif uri.startswith("usb:"):
            args["soapy"] = uri  # SoapyPlutoSDR 接受 usb: URI
        self._soapy = SoapySDR.Device(args)
        self._rx = getattr(SoapySDR, "SOAPY_SDR_RX", 1)
        self._stream = self._soapy.setupStream(self._rx, "CF32", [0])
        self._soapy.activateStream(self._stream)
        # TX 流：SoapySDR 的 TX 与 RX 共用同一 writeStream 面。
        # 打不开只关掉发射能力，不影响 RX（supports_tx() 会如实返回 False）。
        self._tx_dir = getattr(SoapySDR, "SOAPY_SDR_TX", 2)
        try:
            self._tx_stream = self._soapy.setupStream(self._tx_dir, "CF32", [0])
            self._soapy.activateStream(self._tx_stream)
        except Exception as e:
            logger.warning("[TX] Soapy PlutoSDR 发送流不可用: %s", e)
            self._tx_stream = None
        self._backend_lib = "soapy"
        self.status.connected = True
        self.status.error = ""
        self._start_time = time.time()
        logger.info(f"PlutoSDR 已通过 SoapySDR 打开: {args}")
        try:
            self.readback_hw_state()
        except Exception as e:
            logger.debug(f"PlutoSDR(Soapy) 回读失败(忽略): {e}")
        return True

    def disconnect(self):
        """关闭 buffer / context / stream。"""
        try:
            if self._buffer is not None:
                self._buffer = None  # iio Buffer GC 释放
        except Exception:
            pass
        try:
            if self._soapy_stream is not None and self._soapy is not None:
                self._soapy.deactivateStream(self._soapy_stream)
                self._soapy.closeStream(self._soapy_stream)
        except Exception:
            pass
        try:
            tx_stream = getattr(self, "_tx_stream", None)
            if tx_stream is not None and self._soapy is not None:
                self._soapy.deactivateStream(tx_stream)
                self._soapy.closeStream(tx_stream)
        except Exception:
            pass
        self._tx_stream = None
        self._soapy_stream = None
        self._soapy = None
        self._ctx = None
        self._phy = None
        self._rx_chan = None
        self._rx_lo_chan = None
        self._stream_dev = None
        self._tx_stream_dev = None
        self._tx_lo_chan = None
        self._tx_phy_chan = None
        self._backend_lib = None
        super().disconnect()

    # open/close 别名（与任务要求的接口名对齐）
    def open(self, uri: Optional[str] = None) -> bool:
        if uri:
            self._uri = uri
            self.device.device_id = f"plutosdr_{uri}"
        return self.connect()

    def close(self):
        self.disconnect()

    # ------------------------------------------------------------------
    # 调谐 / 采样率 / 增益
    # ------------------------------------------------------------------
    def _apply_frequency(self, freq_hz: float) -> bool:
        """写 RX LO 频率（ad9361-phy/altvoltage0:frequency）。"""
        if self._backend_lib == "iio":
            try:
                if self._rx_lo_chan is not None:
                    self._rx_lo_chan.attrs["frequency"].value = str(int(freq_hz))
                else:
                    # 兜底：写 phy 直接属性
                    self._phy.attrs["out_altvoltage0_RX_LO_frequency"].value = str(int(freq_hz))
                # TX 本振跟随（半双工共用同一 center 设定）
                if self._tx_lo_chan is not None:
                    self._tx_lo_chan.attrs["frequency"].value = str(int(freq_hz))
                return True
            except Exception as e:
                self.status.error = f"[iio] set frequency 失败: {e}"
                return False
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self._soapy.setFrequency(self._rx, 0, float(freq_hz))
                if getattr(self, "_tx_stream", None) is not None:
                    self._soapy.setFrequency(self._tx_dir, 0, float(freq_hz))
                return True
            except Exception as e:
                self.status.error = f"[soapy] setFrequency 失败: {e}"
                return False
        # 未连接：参数已由基类写入 status.frequency_hz，不报错（降级模式）
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        """写 RX 采样率（ad9361-phy/voltage0:sampling_frequency）。"""
        if self._backend_lib == "iio":
            try:
                if self._rx_chan is not None:
                    self._rx_chan.attrs["sampling_frequency"].value = str(int(rate_hz))
                if self._tx_phy_chan is not None:
                    self._tx_phy_chan.attrs["sampling_frequency"].value = str(int(rate_hz))
                return True
            except Exception as e:
                self.status.error = f"[iio] set sampling_frequency 失败: {e}"
                return False
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self._soapy.setSampleRate(self._rx, 0, float(rate_hz))
                if getattr(self, "_tx_stream", None) is not None:
                    self._soapy.setSampleRate(self._tx_dir, 0, float(rate_hz))
                return True
            except Exception as e:
                self.status.error = f"[soapy] setSampleRate 失败: {e}"
                return False
        return True

    def _apply_bandwidth(self, bw_hz: float) -> bool:
        """写 RX 模拟带宽（rf_bandwidth）。"""
        if self._backend_lib == "iio" and self._rx_chan is not None:
            try:
                self._rx_chan.attrs["rf_bandwidth"].value = str(int(bw_hz))
                return True
            except Exception:
                return False
        return True

    def _apply_gain(self, gain_db: float) -> bool:
        """切 manual 增益模式并写 hardwaregain。"""
        if self._backend_lib == "iio":
            try:
                if self._rx_chan is not None:
                    self._rx_chan.attrs["gain_control_mode"].value = "manual"
                    self._rx_chan.attrs["hardwaregain"].value = str(float(gain_db))
                return True
            except Exception as e:
                self.status.error = f"[iio] set gain 失败: {e}"
                return False
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self._soapy.setGain(self._rx, 0, float(gain_db))
                return True
            except Exception as e:
                self.status.error = f"[soapy] setGain 失败: {e}"
                return False
        return True

    def set_agc(self, enabled: bool) -> bool:
        if not super().set_agc(enabled):
            return False
        if self._backend_lib == "iio" and self._rx_chan is not None:
            try:
                mode = "slow_attack" if enabled else "manual"
                self._rx_chan.attrs["gain_control_mode"].value = mode
                return True
            except Exception as e:
                self.status.error = f"[iio] set gain_control_mode 失败: {e}"
                return False
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self._soapy.setGainMode(self._rx, 0, bool(enabled))
                return True
            except Exception as e:
                self.status.error = f"[soapy] setGainMode 失败: {e}"
                return False
        return True

    # ------------------------------------------------------------------
    # readback
    # ------------------------------------------------------------------
    def readback_hw_state(self) -> bool:
        if self._backend_lib == "iio" and self._rx_chan is not None:
            got = 0
            try:
                self.status.sample_rate_hz = float(
                    self._rx_chan.attrs["sampling_frequency"].value)
                got += 1
            except Exception:
                pass
            try:
                if self._rx_lo_chan is not None:
                    self.status.frequency_hz = float(
                        self._rx_lo_chan.attrs["frequency"].value)
                    got += 1
            except Exception:
                pass
            try:
                self.status.gain_db = float(
                    self._rx_chan.attrs["hardwaregain"].value)
                got += 1
            except Exception:
                pass
            return got > 0
        if self._backend_lib == "soapy" and self._soapy is not None:
            try:
                self.status.sample_rate_hz = float(self._soapy.getSampleRate(self._rx, 0))
                self.status.frequency_hz = float(self._soapy.getFrequency(self._rx, 0))
                self.status.gain_db = float(self._soapy.getGain(self._rx, 0))
                return True
            except Exception:
                return False
        return False

    # ------------------------------------------------------------------
    # read_samples / recv_samples
    # ------------------------------------------------------------------
    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        """读取 num_samples 个 IQ 样本，返回 complex64 numpy 数组。"""
        if not self.status.connected:
            return None
        try:
            if self._backend_lib == "iio":
                return self._read_samples_iio(num_samples)
            if self._backend_lib == "soapy":
                return self._read_samples_soapy(num_samples)
        except Exception as e:
            self.status.error = f"read_samples 异常: {e}"
            return None
        return None

    # 任务要求的别名
    def recv_samples(self, num_samples: int) -> Optional[np.ndarray]:
        return self.read_samples(num_samples)

    def _read_samples_iio(self, num_samples: int) -> Optional[np.ndarray]:
        """从 iio.Buffer 读 16-bit I/Q 交错样本，归一化为 complex64。"""
        if self._buffer is None:
            return None
        # buffer.refill() 阻塞直到填充满
        self._buffer.refill()
        # read() 返回 bytes；16-bit 小端 I/Q 交错
        raw = self._buffer.read()
        arr = np.frombuffer(raw, dtype=np.int16)
        # 截断到偶数长度
        arr = arr[:len(arr) // 2 * 2]
        # 拆 I/Q 并归一化到 [-1, 1]
        i = arr[0::2].astype(np.float32) / 32768.0
        q = arr[1::2].astype(np.float32) / 32768.0
        samples = (i + 1j * q).astype(np.complex64)
        # 按请求数截断（buffer 一次读满，可能多于请求）
        if len(samples) > num_samples:
            samples = samples[:num_samples]
        self._samples_read += len(samples)
        return samples

    def _read_samples_soapy(self, num_samples: int) -> Optional[np.ndarray]:
        buff = np.zeros(int(num_samples), np.complex64)
        status = self._soapy.readStream(
            self._stream, [buff], int(num_samples), timeoutUs=500_000)
        n_read = int(getattr(status, "ret", 0))
        if n_read <= 0:
            if n_read < 0:
                self.status.error = f"readStream 错误码 {n_read}"
            return None
        self._samples_read += n_read
        return buff[:n_read].copy()

    # ── 发射 ──────────────────────────────────────────────────────────
    def _tx_ready(self) -> bool:
        if self._backend_lib == "iio":
            return self._tx_stream_dev is not None
        if self._backend_lib == "soapy":
            return getattr(self, "_tx_stream", None) is not None
        return False

    def _write_tx_iio(self, iq: np.ndarray) -> bool:
        """IIO 发送：创建非循环 buffer → write(I/Q int16 交织) → push。

        来源: pylibiio ``Buffer.write(array)`` / ``Buffer.push(samples_count)``；
        libiio 输出设备提交后即可复用 buffer（非 cyclic 模式）。
        """
        if self._tx_stream_dev is None:
            return False
        inter = np.empty(iq.size * 2, dtype=np.int16)
        inter[0::2] = (np.clip(iq.real, -1.0, 1.0) * 32767.0).astype(np.int16)
        inter[1::2] = (np.clip(iq.imag, -1.0, 1.0) * 32767.0).astype(np.int16)
        buf = self._tx_stream_dev.create_buffer(int(iq.size), cyclic=False)
        buf.write(inter)
        buf.push()
        return True

    def _write_tx_soapy(self, iq: np.ndarray) -> bool:
        """SoapySDR 发送：writeStream（与 readStream 对称的真实写流面）。"""
        tx_stream = getattr(self, "_tx_stream", None)
        if tx_stream is None or self._soapy is None:
            return False
        self._soapy.setFrequency(self._tx_dir, 0, float(self.status.frequency_hz))
        self._soapy.setSampleRate(self._tx_dir, 0, float(self.status.sample_rate_hz))
        status = self._soapy.writeStream(
            tx_stream, [iq], int(iq.size), 0, 0, timeoutUs=500_000)
        n = int(getattr(status, "ret", status if isinstance(status, int) else 0))
        if n <= 0:
            self.status.error = f"[TX] Soapy writeStream 返回 {n}（未写入样本）"
            return False
        return True

    def write_tx(self, iq_samples: np.ndarray) -> bool:
        """真实写 PlutoSDR 发送流（IIO buffer push 或 Soapy writeStream）。"""
        from .tx_guard import reject_unless_authorized
        if not reject_unless_authorized(self):
            self._note_tx_reject()
            return False
        if not self.status.connected:
            self.status.error = "[TX] PlutoSDR 未连接，无法发射"
            return False
        try:
            iq = np.asarray(iq_samples, dtype=np.complex64).ravel()
            if iq.size == 0:
                return True
            if self._backend_lib == "iio":
                ok = self._write_tx_iio(iq)
            elif self._backend_lib == "soapy":
                ok = self._write_tx_soapy(iq)
            else:
                ok = False
        except Exception as e:  # noqa: BLE001 - 驱动异常必须转为如实失败
            self.status.error = f"[TX] PlutoSDR 写波异常: {type(e).__name__}: {e}"
            return False
        if not ok:
            self.status.error = self.status.error or "[TX] PlutoSDR 写流失败"
        return bool(ok)


class FileIQBackend(SDRBackend):
    """
    IQ 文件回放源（离线复现）。

    把录制的 IQ 文件当作一台 SDR 设备顺序/循环回放，便于无棒环境下反复跑解码器、
    做可复现实验（对标 SDR++ IQ file playback / GNU Radio file source）。

    支持格式（按扩展名，或 fmt= 显式指定）：
      .npy            complex 一维数组（本项目自检/录音默认）
      .cu8/.u8/.bin   unsigned 8-bit 交错 IQ（`rtl_sdr` 命令原生录制格式）
      .cfile/.cf32     complex float32 交错（GNU Radio .cfile）
      .cs16/.s16      signed 16-bit 交错
      .wav            立体声 int16（I=左 Q=右；sidecar wav_scale 反归一化，
                      无 sidecar 时采样率以 wav 头帧率为准）
      .csv            录制器文本格式（I,Q 表头 + 每行两个浮点）
    同名 .json sidecar 可提供 {"sample_rate":..., "center_freq":..., "format":...}。
    sidecar 损坏如实报错，绝不静默按默认值播（那是按错误速率/频率解释数据）。
    """

    def __init__(self, path: str, sample_rate: float = 2_400_000,
                 center_freq: float = 100_000_000, loop: bool = True, fmt: str = None):
        device = SDRDevice(
            device_type="iq_file",
            device_id=f"iqfile_{os.path.basename(path)}",
            name=f"IQ 文件回放: {os.path.basename(path)}",
            frequency_range=(1, 8_000_000_000),
            sample_rate_range=(1, 61_440_000),
            max_gain=0.0,
            supports_iq=True,
            supports_tx=False,
            # 文件回放不声明设备能力：上面只是合法性边界，不是文件元数据。
            # 实际采样率/中心频率在 _load() 后由 status 如实给出。
            capability_status={
                "freq_range": "unknown",
                "sample_rate_range": "unknown",
                "tx": "declared",
            },
            capability_note={
                "freq_range": "文件回放不声明设备能力；实际频点见文件元数据/状态",
                "sample_rate_range": "文件回放不声明设备能力；实际速率见状态",
            },
        )
        super().__init__(device)
        self._path = path
        self._loop = loop
        self._fmt = fmt
        self._samples = None
        self._cursor = 0
        self._rate = sample_rate
        self._freq = center_freq
        self.status.sample_rate_hz = sample_rate
        self.status.frequency_hz = center_freq

    def _load(self) -> int:
        import json
        # 兼容两种 sidecar 命名：数据文件全名+.json（本项目录制/值守写出，
        # 如 x.cu8.json）与 去扩展名+.json（GNU Radio 惯例，如 x.json）。
        meta_candidates = [
            self._path + ".json",
            os.path.splitext(self._path)[0] + ".json",
        ]
        meta_path = next((p for p in meta_candidates if os.path.exists(p)), None)
        wav_scale = 1.0
        if meta_path:
            try:
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                if not isinstance(meta, dict):
                    raise ValueError("顶层不是 JSON 对象")
            except Exception as e:
                raise ValueError(
                    f"IQ sidecar 解析失败（{meta_path}）: {type(e).__name__}: {e}。"
                    "请修复或删除该 sidecar 后重试。") from e
            self._rate = meta.get("sample_rate",
                         meta.get("sample_rate_hz", self._rate))
            self._freq = meta.get("center_freq",
                         meta.get("center_hz", self._freq))
            self._fmt = meta.get("format", self._fmt)
            wav_scale = float(meta.get("wav_scale", 1.0))
        ext = (self._fmt or os.path.splitext(self._path)[1].lower().lstrip(".")).lower()
        if ext == "npy":
            self._samples = np.asarray(np.load(self._path), dtype=np.complex64)
        elif ext in ("cu8", "u8", "bin"):
            raw = np.fromfile(self._path, dtype=np.uint8)
            raw = raw[:len(raw) // 2 * 2].reshape(-1, 2).astype(np.float32)
            self._samples = np.asarray(((raw - 127.5) / 127.5).view(np.complex64).reshape(-1),
                                       dtype=np.complex64)
        elif ext in ("cfile", "cf32", "iq", "fc32"):
            raw = np.fromfile(self._path, dtype=np.float32)
            raw = raw[:len(raw) // 2 * 2].reshape(-1, 2)
            self._samples = np.asarray(raw.view(np.complex64).reshape(-1), dtype=np.complex64)
        elif ext in ("cs16", "s16", "sc16"):
            raw = np.fromfile(self._path, dtype=np.int16)
            raw = raw[:len(raw) // 2 * 2].reshape(-1, 2).astype(np.float32) / 32768.0
            self._samples = np.asarray(raw.view(np.complex64).reshape(-1), dtype=np.complex64)
        elif ext == "wav":
            import wave
            with wave.open(self._path, "rb") as wf:
                if wf.getnchannels() != 2 or wf.getsampwidth() != 2:
                    raise ValueError(
                        f"wav 回放仅支持立体声 16-bit（I=左 Q=右）: {self._path}"
                        f"（实际 {wf.getnchannels()} 声道/{wf.getsampwidth() * 8} bit）")
                header_rate = wf.getframerate()
                raw = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            raw = raw[:len(raw) // 2 * 2].reshape(-1, 2).astype(np.float32) / 32768.0
            self._samples = np.asarray(raw.view(np.complex64).reshape(-1), dtype=np.complex64)
            if wav_scale != 1.0:
                self._samples = (self._samples * np.float32(wav_scale)).astype(np.complex64)
            if meta_path is None and header_rate > 0:
                # 无 sidecar 时 wav 头帧率是真实元数据，不拿构造默认值冒充
                self._rate = float(header_rate)
        elif ext == "csv":
            data = np.loadtxt(self._path, delimiter=",", skiprows=1, ndmin=2)
            if data.size == 0:
                self._samples = np.empty(0, dtype=np.complex64)
            else:
                if data.shape[1] != 2:
                    raise ValueError(
                        f"csv 回放需要 I,Q 两列: {self._path}（实际 {data.shape[1]} 列）")
                self._samples = np.asarray(data[:, 0] + 1j * data[:, 1],
                                           dtype=np.complex64)
        else:
            raise ValueError(
                f"不支持的 IQ 文件格式: {ext}（支持 npy/cu8/cfile/cs16/wav/csv）")
        if len(self._samples) == 0:
            raise ValueError(f"IQ 文件不含任何样本: {self._path}")
        self.status.sample_rate_hz = self._rate
        self.status.frequency_hz = self._freq
        return len(self._samples)

    def connect(self) -> bool:
        try:
            self._load()
        except Exception as e:
            self.status.connected = False
            self.status.link_state = "failed"
            self.status.error = f"IQ 文件回放连接失败: {type(e).__name__}: {e}"
            return False
        self.status.connected = True
        self.status.link_state = "connected"
        self.status.error = ""
        self._start_time = time.time()
        return True

    def disconnect(self):
        self._samples = None
        self._cursor = 0
        super().disconnect()

    def read_samples(self, num_samples: int) -> Optional[np.ndarray]:
        if not self.status.connected or self._samples is None:
            return None
        n = len(self._samples)
        if self._cursor >= n:
            if not self._loop:
                return None
            self._cursor = 0
        end = self._cursor + num_samples
        if end <= n:
            out = self._samples[self._cursor:end].copy()
            self._cursor = 0 if (end == n and self._loop) else end
        else:
            tail = self._samples[self._cursor:n]
            if self._loop:
                head = self._samples[:num_samples - len(tail)]
                out = np.concatenate([tail, head])
                self._cursor = len(head)
            else:
                out = tail
                self._cursor = n
        self._samples_read += len(out)
        return out

    def seek(self, position: int):
        if self._samples is not None:
            self._cursor = max(0, min(int(position), len(self._samples) - 1))

    def playback_info(self) -> Dict[str, Any]:
        n = 0 if self._samples is None else len(self._samples)
        return {
            "path": self._path, "total_samples": n,
            "duration_s": n / self._rate if self._rate else 0,
            "cursor": self._cursor, "sample_rate": self._rate,
            "center_freq": self._freq, "loop": self._loop,
        }

    def _apply_frequency(self, freq_hz: float) -> bool:
        self._freq = freq_hz
        return True

    def _apply_sample_rate(self, rate_hz: float) -> bool:
        self._rate = rate_hz
        return True



# ========================================================================
# 通用网络 IQ 源（sdrpp network_source 同机制）
# ========================================================================
NETWORK_IQ_FORMATS = ("cu8", "cs8", "cs16", "cf32")


def dsp_pow2_round(x: float) -> int:
    """不小于 x 的最小 2 的幂（网络读取块大小对齐，避免跨包错位）。"""
    v = 1
    while v < x:
        v *= 2
    return v


class NetworkIQBackend(SDRBackend):
    """从 TCP/UDP 网络流读取 IQ 的后端（对齐 sdrpp/misc_modules/network_sink）。

    sdrpp 的 ``network_sink``/``network_source`` 支持把 IQ 以裸流（无协议头）
    发到对端；本类吃这个流：

    参数（全部显式，缺一不可，不给“猜一个”的默认）：
      protocol   : "tcp" / "udp"
      host, port : 对端地址
      format     : cu8(交错 uint8, 128=中点) / cs8 / cs16 / cf32
      sample_rate_hz : 必须在设备 sample_rate_range 内
      center_freq_hz : 载波频率
      byteorder  : "little" / "big"（cs16/cf32 有效）
      block_bytes: 读取块大小（2 的幂，默认 8192）

    语义诚实边界：
      - TCP 按流保序重组；UDP 按报文保序但可能丢包——返回的样本含静音洞时
        调用方应从 data_health/dropped 自行判定（本类如实统计）。
      - 不实现 sdrpp 的握手/元数据通道——协议头、采样率等由配置给定；
        与对端不一致时输出诚实错误，不静默 reinterpret。
      - 未连接时 read_samples 返回 None（不做静音填充，不伪造数据）。
    """

    def __init__(self, protocol: str, host: str, port: int,
                 sample_rate_hz: float, center_freq_hz: float,
                 fmt: str = "cu8", byteorder: str = "little",
                 block_bytes: int = 8192) -> None:
        device = SDRDevice(
            device_type="network_iq",
            device_id=f"net_{protocol}_{host}_{port}",
            name=f"网络 IQ 源: {protocol}://{host}:{port}",
            frequency_range=(0, 12_000_000_000),
            sample_rate_range=(1, 1_000_000_000),
            max_gain=0.0,
            supports_iq=True,
            supports_tx=False,
            # 速率/频率由用户配置显式给定（配置即声明），范围只是合法性边界。
            capability_status={
                "freq_range": "declared",
                "sample_rate_range": "declared",
                "tx": "declared",
            },
            capability_note={
                "freq_range": "中心频率由用户配置显式给定，非设备探测",
                "sample_rate_range": "采样率由用户配置显式给定，非设备探测",
            },
        )
        super().__init__(device)
        if protocol not in ("tcp", "udp"):
            raise ValueError(f"未知协议 {protocol!r}（可用 tcp/udp）")
        if fmt not in NETWORK_IQ_FORMATS:
            raise ValueError(
                f"未知 IQ 格式 {fmt!r}，可用：{NETWORK_IQ_FORMATS}")
        if byteorder not in ("little", "big"):
            raise ValueError(f"未知字节序 {byteorder!r}（可用 little/big）")
        if not (0 < int(port) < 65536):
            raise ValueError(f"port 须在 1..65535，收到 {port}")
        lo, hi = device.sample_rate_range
        if not math.isfinite(sample_rate_hz) or not (lo <= sample_rate_hz <= hi):
            raise ValueError(
                f"sample_rate_hz 须在设备声明范围 {lo}–{hi}，收到 {sample_rate_hz}")
        flo, fhi = device.frequency_range
        if not math.isfinite(center_freq_hz) or not (flo <= center_freq_hz <= fhi):
            raise ValueError(
                f"center_freq_hz 须在设备声明范围 {flo}–{fhi}，收到 {center_freq_hz}")
        self._protocol = protocol
        self._host = host
        self._port = int(port)
        self._sample_rate_hz = float(sample_rate_hz)
        self._center_freq_hz = float(center_freq_hz)
        self._fmt = fmt
        self._byteorder = byteorder
        self._block_bytes = dsp_pow2_round(max(512, int(block_bytes)))
        self._sock = None
        self._buf = b""
        self._dropped = 0
        # 配置即口径：status 从构造起就携带真实端点参数（未连接也可读）。
        self.status.sample_rate_hz = self._sample_rate_hz
        self.status.frequency_hz = self._center_freq_hz

    # ---- 连接生命周期 ----------------------------------------------------
    def connect(self) -> bool:
        import socket
        from .link_state import classify_network_error, error_code_for_category
        sock = None
        try:
            if self._protocol == "tcp":
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
                sock.connect((self._host, self._port))
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
                # UDP 必须绑定端口才能收报文。host 为空串表示监听所有接口
                # （0.0.0.0）；port 已在构造期拒绝 <=0，这里只允许显式端口。
                bind_host = self._host or "0.0.0.0"
                sock.bind((bind_host, self._port))
            sock.settimeout(5.0)
        except OSError as e:
            # 拒绝/超时/解析失败都是真实失败：写清状态，绝不原样抛给调用方
            # 冒充"没这回事"，也不泄漏半截 socket。
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
            category, message, _suggestion = classify_network_error(
                e, phase="connect", host=self._host, port=self._port)
            self.status.connected = False
            self.status.link_state = "failed"
            self.status.error_category = category
            self.status.error_code = error_code_for_category(category)
            self.status.error = message
            return False
        self._sock = sock
        self._buf = b""
        self._dropped = 0
        self.status.connected = True
        self.status.link_state = "connected"
        self.status.error = ""
        self.status.error_category = ""
        self.status.error_code = ""
        self._start_time = time.time()
        return True

    def disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buf = b""
        super().disconnect()

    def is_connected(self) -> bool:
        return self._sock is not None

    # ---- 状态接口 --------------------------------------------------------
    def get_status(self) -> SDRStatus:
        # 返回 self.status 本体（基类补 uptime/samples_read）：每次新建对象
        # 会把 error/recording 等状态全丢，调用方看到的永远是"出厂状态"。
        st = super().get_status()
        st.buffer_fill = len(self._buf)
        st.dropped_samples = self._dropped
        return st

    # ---- 调谐 ------------------------------------------------------------
    def set_frequency(self, hz: float) -> bool:
        """网络源不做真实调谐；仅记录中心频率（数据已由对端定频）。

        与基类 bool 契约一致：非法值拒绝且不污染 status。网络源的频率是
        元数据口径，未连接时也允许设置（连接前配置是合法用法）。
        """
        hz = float(hz)
        lo, hi = self.device.frequency_range
        if not math.isfinite(hz) or not (lo <= hz <= hi):
            self.status.error = f"网络源中心频率非法: {hz}（范围 {lo}–{hi} Hz）"
            return False
        self._center_freq_hz = hz
        self.status.frequency_hz = hz
        self.status.error = ""
        return True

    def set_sample_rate(self, sr: float) -> bool:
        """仅记录采样率（真实值由对端决定；不一致会由调用方在解码时暴露）。"""
        sr = float(sr)
        lo, hi = self.device.sample_rate_range
        if not math.isfinite(sr) or not (lo <= sr <= hi):
            self.status.error = f"网络源采样率非法: {sr}（范围 {lo}–{hi} Hz）"
            return False
        self._sample_rate_hz = sr
        self.status.sample_rate_hz = sr
        self.status.error = ""
        return True

    # ---- 数据读取 --------------------------------------------------------
    def _bytes_per_sample(self) -> int:
        return {"cs8": 2, "cs16": 4, "cu8": 2, "cf32": 8}[self._fmt]

    def _decode_iq(self, data: bytes, n: int) -> np.ndarray:
        if self._fmt == "cu8":
            b = np.frombuffer(data, dtype=np.uint8).reshape(n, 2).astype(np.float64)
            iq = (b[:, 0] - 128.0) / 128.0 + 1j * ((b[:, 1] - 128.0) / 128.0)
        elif self._fmt == "cs8":
            b = np.frombuffer(data, dtype=np.int8).reshape(n, 2).astype(np.float64)
            iq = (b[:, 0] + 1j * b[:, 1]) / 128.0
        elif self._fmt == "cs16":
            dt = np.dtype("<i2" if self._byteorder == "little" else ">i2")
            b = np.frombuffer(data, dtype=dt).reshape(n, 2).astype(np.float64)
            iq = (b[:, 0] + 1j * b[:, 1]) / 32768.0
        else:  # cf32
            dt = np.dtype("<c8" if self._byteorder == "little" else ">c8")
            iq = np.frombuffer(data, dtype=dt).astype(np.complex128)
        return iq.astype(np.complex64)

    def read_samples(self, num_samples: int):
        """读 num_samples 个复样本；不足/未连接返回 None（绝不补零）。

        TCP/UDP 都可能一次只到部分字节：循环续读直到凑满或连接断开，
        不让“分包”被误判成“无数据”（诚实区分短读与断流）。
        TCP EOF 时把缓冲里已凑整的样本如实短读返回（补不齐的散字节丢弃），
        不让真数据永久卡在缓冲里。
        """
        if self._sock is None:
            return None
        need = num_samples * self._bytes_per_sample()
        eof = False
        while len(self._buf) < need:
            try:
                if self._protocol == "tcp":
                    chunk = self._sock.recv(self._block_bytes)
                else:
                    chunk = self._sock.recv(min(self._block_bytes, 65535))
            except Exception as e:  # 超时/连接断开：如实反映，不返回半截
                logger.debug("NetworkIQ 读取失败：%s", e)
                return None
            if not chunk:
                if self._protocol != "tcp":
                    return None  # UDP 空报文：本轮无数据（不是 EOF）
                eof = True
                break
            self._buf += chunk
        n = min(num_samples, len(self._buf) // self._bytes_per_sample())
        data = self._buf[:n * self._bytes_per_sample()]
        # EOF 后缓冲里只剩补不齐的散字节：整样本已取走，散字节如实丢弃。
        self._buf = b"" if eof else self._buf[n * self._bytes_per_sample():]
        if n == 0:
            return None
        self._samples_read += n
        return self._decode_iq(data, n)

    def data_health(self):
        """数据面健康度（诚实口径：缓冲区/丢包/对端参数）。"""
        return {
            "connected": self.is_connected(),
            "protocol": self._protocol,
            "endpoint": f"{self._host}:{self._port}",
            "format": self._fmt,
            "byteorder": self._byteorder,
            "buffered_bytes": len(self._buf),
            "udp_dropped_note": ("udp 可能丢包，调用方需按解码质量自行判定"
                                 if self._protocol == "udp" else None),
        }


class SDRBackendManager:
    """
    SDR 后端管理器。

    管理多个 SDR 设备的连接、切换、状态查询。
    支持异构双前端（RTL-SDR + ai-sdr Mini 同时使用）。
    """

    # 设备表 TTL（审计 B 节 P1）：list_devices/connect/switch_device 在过期后
    # 重探，热插拔的设备最迟 5s 内对 MCP/agent 可见。窗口内不重探——枚举是
    # 真实 USB open/close，不许当轮询用。
    _DISCOVER_TTL_S = 5.0

    def __init__(self):
        self.backends: Dict[str, SDRBackend] = {}
        self.active_backend: Optional[SDRBackend] = None
        # VFO（可变频率振荡器）管理：当前中心频点 + 多个监听频率预设
        self.vfos: Dict[str, Dict] = {}  # vfo_id -> {name, frequency_hz, demod_mode, bandwidth_hz, active}
        self._vfo_counter = 0
        self.active_vfo_id: Optional[str] = None
        # ── 多源切换（USB / rtl_tcp / spyserver 同时在册，单活跃）──
        self._switch_lock = threading.RLock()
        self._switch_in_progress = False
        self.switch_generation = 0
        self.last_conflict = ""  # 最近一次切换冲突/失败原因（空=无）
        #: 最近一次 connect() 的说明（沿用了哪台 / 为何拒绝）。工具层据此
        #: 把"未指定设备"这类话换成带设备名与索引的可操作提示。
        self.last_connect_notice = ""
        self._cfg_store = None   # 延迟构造 ConfigStore（测试可替换）

        self._discover_lock = threading.Lock()
        self._last_discover_ts = 0.0
        self._discover()

    def _keep(self, backend: SDRBackend) -> bool:
        """只增不减：新 device_id 并入，已注册实例永不替换（连接态不丢）。"""
        did = backend.device.device_id
        if did in self.backends:
            return False
        self.backends[did] = backend
        return True

    def _refresh_if_stale(self) -> None:
        """设备表 TTL 刷新：过期才重探（双重检查，串行化 _discover）。"""
        if time.monotonic() - self._last_discover_ts < self._DISCOVER_TTL_S:
            return
        with self._discover_lock:
            if time.monotonic() - self._last_discover_ts < self._DISCOVER_TTL_S:
                return
            self._discover()

    def _discover(self):
        """发现可用的 SDR 设备（仅注册真实硬件后端）。

        无硬件时 backends 为空、active_backend 为 None（显式未连接），
        绝不注册模拟后端作为默认源。

        刷新语义（审计 B 节 P1 设备表 TTL）：只增不减——新设备并入，
        已注册条目永不替换/摘除（探测失败 ≠ 不存在：占用中的棒子探测
        必失败，正在用的实例更不许换掉）。已登记的源连探测都不做，
        不为刷新反复开棒子。要摘除条目请重启进程。
        """
        # 尝试发现 RTL-SDR（已登记则跳过探测——不为刷新反复开棒子；
        # device_id 约定 f"rtl_{index}"，这里只探 index 0）
        if "rtl_0" not in self.backends:
            try:
                from rtlsdr import RtlSdr
                sdr = RtlSdr(0)
                sdr.close()
                rtl = RTLSDRBackend(0)
                self._keep(rtl)
            except Exception as e:
                # 无硬件/缺 pyrtlsdr 环境下启动是正常的；用 debug 记录原因，不静默也不弹错
                logger.debug(f"RTL-SDR 发现失败(忽略，无硬件环境正常): {e}")

        # 尝试发现 HackRF（驱动是 libhackrf 的 ctypes 直连：libhackrf 可加载
        # 才登记；真正缺设备/被占用由 connect() 如实报错。device_id 约定
        # f"hackrf_{index}"，已登记则跳过探测）
        if "hackrf_0" not in self.backends:
            try:
                from .hackrf_params import HackRFBackend as _CtlsBackend
                if _CtlsBackend(0)._load_lib() is None:
                    raise RuntimeError("libhackrf 不可用")
                hf = HackRFBackend(0)
                # 不实际连接，只注册（用户手动连接）
                self._keep(hf)
            except Exception:
                pass  # 没有 libhackrf

        # 尝试发现 USRP（device_id 约定 usrp_{device_args or 'default'}，
        # 已登记则跳过探测）
        if "usrp_default" not in self.backends:
            try:
                import uhd
                usrp = USRPBackend()
                self._keep(usrp)
            except Exception:
                pass  # 没有 UHD 库

        # PlutoSDR：仅当真实 enumerate() 探测到设备时才注册（libiio USB/网络）。
        try:
            for _pd in PlutoSDRBackend.enumerate():
                _uri = _pd.get("uri")
                _pl = PlutoSDRBackend(uri=_uri, device_info=_pd) if _uri \
                    else PlutoSDRBackend(device_info=_pd)
                self._keep(_pl)
        except Exception as e:
            logger.debug(f"PlutoSDR 枚举失败(忽略): {e}")

        # rtl_tcp 网络源：env 驱动的显式登记（上面 USB 发现都失败时的最常见
        # 情形——本机装不上 librtlsdr / 棒在远端 / 多客户端共享）。登记的是
        # 真实网络前端（RTLSDRBackend(host=...) 走自研 MIT rtl_tcp 客户端），
        # connect() 克制不立即联网，保持在 list_devices / MCP connect 语义内。
        for _host, _port in parse_rtl_tcp_env(os.environ.get(RTL_TCP_ENV, "")):
            try:
                _rt = RTLSDRBackend(host=_host, port=_port)
                self._keep(_rt)
                logger.debug(f"rtl_tcp 源已登记: {_host}:{_port}")
            except Exception as e:
                logger.debug(f"rtl_tcp 后端登记失败(忽略): {_host}:{_port}: {e}")

        # SpyServer 网络源：与 rtl_tcp 同样走 env 显式登记（默认端口 7355）。
        # 懒导入避免 sdr_backend <-> spyserver_client 的循环导入。
        for _host, _port in parse_rtl_tcp_env(
                os.environ.get(SPYSERVER_ENV, ""), default_port=7355):
            try:
                from .spyserver_client import SpyServerBackend
                _sp = SpyServerBackend(_host, _port)
                self._keep(_sp)
                logger.debug(f"SpyServer 源已登记: {_host}:{_port}")
            except Exception as e:
                logger.debug(f"SpyServer 后端登记失败(忽略): {_host}:{_port}: {e}")

        # 通用网络 IQ 源（sdrpp network_source 同机制）：env 显式登记。
        # 裸流无元数据，rate/freq 由配置给定；登记不等于连接，成败交给 connect()。
        for _cfg in parse_network_iq_env(os.environ.get(NETWORK_IQ_ENV, "")):
            try:
                _nq = NetworkIQBackend(**_cfg)
                self._keep(_nq)
                logger.debug(
                    f"网络 IQ 源已登记: {_cfg['protocol']}://{_cfg['host']}:{_cfg['port']}")
            except Exception as e:
                logger.debug(f"网络 IQ 源登记失败(忽略): {_cfg}: {e}")

        # 无默认后端：active_backend 保持 None，等用户显式 connect 真实设备。
        self._last_discover_ts = time.monotonic()

    def list_devices(self) -> List[Dict[str, Any]]:
        """列出所有可用设备（TTL 过期先重探，热插拔可见）。"""
        self._refresh_if_stale()
        with self._discover_lock:
            snapshot = list(self.backends.values())
        return [
            {
                "device_id": b.device.device_id,
                "type": b.device.device_type,
                "name": b.device.name,
                "frequency_range": list(b.device.frequency_range),
                "sample_rate_range": list(b.device.sample_rate_range),
                "max_gain": b.device.max_gain,
                "supports_iq": b.device.supports_iq,
                # 声明（硬件/代码路径存在）与真机验证分开报，避免 UI 把
                # 「未验证」直接画成「支持」。
                "supports_tx": bool(b.device.supports_tx),
                "tx_capability": b.tx_capability(),
                # 能力来源随设备对象一起交出；缺省=未标注，展示层按未知处理。
                "capability_status": dict(b.device.capability_status),
                "capability_note": dict(b.device.capability_note),
                "connected": b.status.connected,
                "active": b == self.active_backend,
            }
            for b in snapshot
        ]

    def connect(self, device_id: str = None) -> bool:
        """连接设备。无设备可选时返回 False（显式未连接）。

        未指定 device_id 时**绝不静默取枚举到的第一台**（枚举顺序随机器与
        插拔顺序变化，选错设备用户无从察觉）。顺序是：

        1. 已有激活后端 → 连接它；
        2. 否则读持久化的「上次使用的设备」，仍在枚举表中则沿用，并在
           ``last_connect_notice`` 里写明沿用了哪台；
        3. 没有记录（或记录已不在）→ 返回 False，``last_connect_notice``
           列出全部可用设备（索引 + 名称 + device_id），让用户显式指定。

        显式传入未枚举到的 device_id 时拒绝连接——静默连到别的设备会骗人
        （违反 sdr_connect 契约「只能连接枚举到的真实设备」）。
        成员判定前先过设备表 TTL 刷新：热插拔后直接 connect 新 id 不必
        先跑一遍 list_devices。
        """
        self.last_connect_notice = ""
        self._refresh_if_stale()
        if device_id and device_id not in self.backends:
            self.last_connect_notice = (
                f"设备 {device_id!r} 未在枚举列表中，未连接。"
                f"可用设备：{self._device_choices_text()}")
            logger.warning("connect: %s", self.last_connect_notice)
            return False
        if device_id:
            backend = self.backends[device_id]
        else:
            backend = self.active_backend
            if backend is None:
                remembered = self._last_used_device_id()
                if remembered and remembered in self.backends:
                    backend = self.backends[remembered]
                    self.last_connect_notice = (
                        f"未指定 device_id，沿用上次使用的设备："
                        f"{backend.device.name}"
                        f"（{backend.device.device_id}）")
                    logger.info("connect: %s", self.last_connect_notice)
                elif remembered:
                    self.last_connect_notice = (
                        f"上次使用的设备 {remembered!r} 当前不在枚举列表中，未连接。"
                        f"可用设备：{self._device_choices_text()}")
                    logger.warning("connect: %s", self.last_connect_notice)
                    return False
                else:
                    self.last_connect_notice = (
                        "未指定 device_id，且没有上次使用设备的记录，未连接。"
                        f"可用设备：{self._device_choices_text()}。"
                        "请显式传 device_id（先用 sdr_list_devices 查看）。")
                    logger.warning("connect: %s", self.last_connect_notice)
                    return False

        if backend is None:
            self.last_connect_notice = (
                "无可用后端（未发现真实硬件），保持未连接")
            logger.warning("connect: %s", self.last_connect_notice)
            return False

        ok = self._activate(backend)
        if ok:
            self._remember_last_device(backend.device.device_id)
        return ok

    def _device_choices_text(self) -> str:
        """把可用设备列成「索引) 名称（device_id）」，供报错/提示用。"""
        try:
            items = list(self.backends.values())
        except Exception:
            return "（枚举失败）"
        if not items:
            return "（未发现任何设备）"
        return "；".join(
            f"{i}) {b.device.name}（{b.device.device_id}）"
            for i, b in enumerate(items, 1))

    #: config.json 里记录「上次成功连接的设备」的键。
    _LAST_DEVICE_KEY = "sdr_last_device"

    def _config(self):
        """延迟构造 ConfigStore；失败返回 None（连接流程绝不因配置不可写而失败）。"""
        if self._cfg_store is None:
            try:
                from .config import ConfigStore
                self._cfg_store = ConfigStore()
            except Exception as e:  # noqa: BLE001 - 配置不可用不影响连接
                logger.debug("ConfigStore 不可用（忽略持久化）: %s", e)
                return None
        return self._cfg_store

    def _last_used_device_id(self) -> str:
        """读持久化的上次设备 id；没有/损坏一律返回空串（不猜）。"""
        store = self._config()
        if store is None:
            return ""
        try:
            rec = (store.read() or {}).get(self._LAST_DEVICE_KEY)
        except Exception as e:  # noqa: BLE001
            logger.debug("读取上次设备记录失败（忽略）: %s", e)
            return ""
        if isinstance(rec, dict):
            return str(rec.get("device_id") or "")
        return str(rec or "")

    def _remember_last_device(self, device_id: str) -> None:
        """成功连接后落盘；值未变则不写（避免每次 connect 都碰磁盘）。"""
        store = self._config()
        if store is None or not device_id:
            return
        backend = self.backends.get(device_id)
        # 持久化只是可选副作用：设备对象缺字段时记空名，绝不让它中断连接。
        name = getattr(backend.device, "name", "") if backend is not None else ""
        rec = {"device_id": device_id, "name": name}

        def _mutate(data):
            if data.get(self._LAST_DEVICE_KEY) == rec:
                return False          # 没变，放弃写入
            data[self._LAST_DEVICE_KEY] = rec
            return True

        try:
            store.update(_mutate)
        except Exception as e:  # noqa: BLE001 - 持久化失败不影响本次连接
            logger.debug("写入上次设备记录失败（忽略）: %s", e)

    _HANDLE_ATTRS = ("_ring_reader", "_client", "_sdr", "_hackrf", "_usrp",
                     "_soapy", "_sock", "_streamer")

    def _release_backend(self, backend: SDRBackend) -> bool:
        """断开并验证句柄/线程确实释放；残留则拒绝完成切换。"""
        had_ring = getattr(backend, "_ring_reader", None) is not None
        backend.disconnect()
        rr = getattr(backend, "_ring_reader", None)
        if rr is not None:
            try:
                rr.stop()
            except Exception:
                pass
            try:
                setattr(backend, "_ring_reader", None)
            except Exception:
                pass
        for attr in self._HANDLE_ATTRS:
            if getattr(backend, attr, None) is not None:
                logger.warning(f"切换中止：旧源句柄 {attr} 未释放")
                return False
        if had_ring:
            alive = [t for t in threading.enumerate()
                     if t.name == "mbdsdr-ring-producer" and t.is_alive()]
            if alive:
                logger.warning("切换中止：旧源生产者线程仍在运行，拒绝残留")
                return False
        return True

    def _activate(self, backend: SDRBackend) -> bool:
        """把指定后端设为唯一活跃源；切换全程串行且可验证释放。

        同一时刻只允许一个活跃源：旧源先断开并验证句柄/线程释放，再连接
        新源；任何一步失败都明确写入 last_conflict，不静默保留两路。
        """
        with self._switch_lock:
            if self._switch_in_progress:
                self.last_conflict = "切换进行中：拒绝并发激活第二个源"
                logger.warning(self.last_conflict)
                return False
            if self.active_backend is backend and backend.status.connected:
                self.last_conflict = ""
                return True
            self._switch_in_progress = True
            claimed_id = ""
            try:
                # 先声明占用、再碰硬件：别的进程（桌面端/另一个 AI）占着这台
                # 设备时必须在这里拒绝，不能等 USB open 失败再猜原因。
                new_did = backend.device.device_id
                claimed, holder = claim_active_owner(AI_OWNER, new_did)
                if not claimed:
                    self.last_conflict = _owner_conflict_text(holder)
                    self.last_connect_notice = self.last_conflict
                    logger.warning("激活被拒：%s", self.last_conflict)
                    return False
                claimed_id = new_did
                old = self.active_backend
                old_did = (old.device.device_id
                           if old is not None and old is not backend else "")
                if old is not None and old is not backend:
                    detail = ""
                    try:
                        released = self._release_backend(old)
                    except Exception as e:
                        released = False
                        detail = f"{type(e).__name__}: {e}"
                    if not released:
                        self.active_backend = None
                        self.last_conflict = (f"旧源 {old.device.device_id} "
                                              f"未完全释放，已中止切换 {detail}".rstrip())
                        return False
                    self.active_backend = None
                if not backend.connect():
                    reason = backend.status.error or "connect 返回 False"
                    self.active_backend = None
                    self.last_conflict = (f"激活 {backend.device.device_id} 失败: "
                                          f"{reason}")
                    logger.warning(self.last_conflict)
                    return False
                self.active_backend = backend
                self.switch_generation += 1
                self.last_conflict = ""
                claimed_id = ""          # 已生效：占用保持到 disconnect/切换
                if old_did:
                    release_active_owner(AI_OWNER, old_did)
                # 连接成功后从硬件回读实际参数，对齐软件状态与硬件实际
                try:
                    backend.readback_hw_state()
                except Exception as e:
                    logger.warning(f"硬件回读失败（不影响连接）: {e}")
                return True
            finally:
                if claimed_id:
                    # 任何失败路径都不留占用记录，否则下一个进程会被自己人挡。
                    release_active_owner(AI_OWNER, claimed_id)
                self._switch_in_progress = False

    def disconnect(self):
        """断开当前设备，并验证句柄/线程释放。"""
        with self._switch_lock:
            old = self.active_backend
            if old is not None:
                did = old.device.device_id
                if not self._release_backend(old):
                    # 句柄未释放 = 设备可能还开着：保留占用登记，别让别人接手。
                    self.last_conflict = f"旧源 {did} 未完全释放"
                else:
                    release_active_owner(AI_OWNER, did)
            self.active_backend = None

    def switch_device(self, device_id: str) -> bool:
        """切换到另一个设备（单活跃、释放旧源、冲突明确报错）。"""
        self._refresh_if_stale()
        if device_id not in self.backends:
            self.last_conflict = f"未知设备: {device_id}"
            return False
        return self._activate(self.backends[device_id])

    def get_source_status(self) -> Dict[str, Any]:
        """多源在册/活跃状态的如实快照。"""
        return {
            "active": (self.active_backend.device.device_id
                       if self.active_backend else None),
            "generation": self.switch_generation,
            "switching": self._switch_in_progress,
            "last_conflict": self.last_conflict,
            "sources": [
                {"device_id": be.device.device_id,
                 "kind": getattr(be.device, "type", ""),
                 "active": be is self.active_backend,
                 "connected": be.status.connected,
                 "error": be.status.error}
                for be in self.backends.values()
            ],
        }

    def get_active(self) -> Optional[SDRBackend]:
        return self.active_backend

    def open_iq_file(self, path: str, sample_rate: float = 2_400_000,
                     center_freq: float = 100_000_000, loop: bool = True,
                     fmt: str = None) -> Optional["FileIQBackend"]:
        """打开 IQ 录制文件作为回放源并切换为当前设备（离线复现实验）。"""
        be = FileIQBackend(path, sample_rate, center_freq, loop, fmt)
        if not be.connect():
            return None
        if self.active_backend and self.active_backend is not be:
            old = self.active_backend
            try:
                old.disconnect()
            except Exception:
                pass
            # 从真实硬件切到离线回放：硬件句柄已释放，占用登记也要还回去
            # （IQ 文件不是硬件，不上报占用）。
            if getattr(old.device, "device_type", "") != "iq_file":
                release_active_owner(AI_OWNER, old.device.device_id)
        self.backends[be.device.device_id] = be
        self.active_backend = be
        return be

    def get_status(self) -> Optional[SDRStatus]:
        if self.active_backend:
            return self.active_backend.get_status()
        return None

    # ──────────────────────────────────────────────────────────
    # VFO（可变频率振荡器）管理：监听频率预设列表
    # ──────────────────────────────────────────────────────────

    def vfo_add(self, name: str, frequency_hz: float, demod_mode: str = "FM",
                bandwidth_hz: float = 12500) -> Optional[str]:
        """添加一个 VFO。返回 vfo_id；参数非法时返回 None。"""
        try:
            if frequency_hz is None or float(frequency_hz) <= 0:
                return None
            self._vfo_counter += 1
            vfo_id = f"vfo_{self._vfo_counter}"
            self.vfos[vfo_id] = {
                "vfo_id": vfo_id,
                "name": str(name),
                "frequency_hz": float(frequency_hz),
                "demod_mode": str(demod_mode),
                "bandwidth_hz": float(bandwidth_hz),
                "active": False,
            }
            return vfo_id
        except Exception:
            return None

    def vfo_remove(self, vfo_id: str) -> bool:
        """删除一个 VFO。如果是当前激活的，先取消激活。不存在则返回 False。"""
        try:
            if vfo_id not in self.vfos:
                return False
            if self.active_vfo_id == vfo_id:
                self.active_vfo_id = None
            self.vfos.pop(vfo_id, None)
            return True
        except Exception:
            return False

    def vfo_list(self) -> List[Dict]:
        """列出所有 VFO，每个包含 vfo_id, name, frequency_hz, demod_mode, bandwidth_hz, active。"""
        try:
            return [dict(v) for v in self.vfos.values()]
        except Exception:
            return []

    def vfo_select(self, vfo_id: str) -> bool:
        """切换到指定 VFO：设置 active_backend 的频率和解调模式，标记该 VFO 为 active。"""
        try:
            if vfo_id not in self.vfos:
                return False
            vfo = self.vfos[vfo_id]
            # 切换时把之前 active 的 VFO 标记为非 active
            for vid, v in self.vfos.items():
                v["active"] = (vid == vfo_id)
            self.active_vfo_id = vfo_id
            # 应用到当前激活后端（若存在且已连接）
            be = self.active_backend
            if be is not None:
                connected = getattr(getattr(be, "status", None), "connected", False)
                if connected and hasattr(be, "set_frequency"):
                    try:
                        be.set_frequency(vfo["frequency_hz"])
                    except Exception:
                        pass
                # set_demod 不存在时只设频率
                if connected and hasattr(be, "set_demod"):
                    try:
                        be.set_demod(vfo["demod_mode"])
                    except Exception:
                        pass
            return True
        except Exception:
            return False

    def vfo_get_active(self) -> Optional[Dict]:
        """获取当前激活的 VFO 信息。无激活 VFO 时返回 None。"""
        try:
            if self.active_vfo_id and self.active_vfo_id in self.vfos:
                return dict(self.vfos[self.active_vfo_id])
            return None
        except Exception:
            return None


# ======================================================================
# 模块级：合并枚举所有 SDR 设备（SoapySDR + 原生 RTL-SDR / HackRF）
# ======================================================================

def enumerate_all_sdr_devices() -> List[Dict[str, Any]]:
    """枚举本机所有可用 SDR 设备并去重合并。

    合并来源：
      - SoapySDRBackend.list_devices() —— SoapySDR 总线识别的所有前端
        （RTL-SDR/HackRF/USRP/BladeRF/Airspy...），来源: lib/Factory.cpp:41。
      - RTLSDRBackend.list_devices() —— pyrtlsdr 原生 USB 枚举（见本文件 RTLSDRBackend）。
      - HackRFBackend —— 无专用枚举接口；只在 SoapySDR 总线或 gr-osmosdr 探测到
        设备时（步骤 1/5）出现，不靠"装了库"就伪造一条设备。

    去重键：优先 serial，其次 label。SoapySDR 条目信息更全（含真实范围），
    已覆盖到同 serial 的原生条目时优先保留 SoapySDR 条目。

    返回 [] 表示"真的没发现设备"（绝不返回假设备/mock）。
    """
    merged: Dict[str, Dict[str, Any]] = {}

    def _key_of(d: Dict[str, Any]) -> str:
        return d.get("serial") or d.get("label") or d.get("driver", "?")

    # 1) SoapySDR 总线枚举（信息最全，先放）。
    #    打 source="soapy" 标记，供 build_backend_for_device 与 UI 下拉框区分来源。
    try:
        for dev in SoapySDRBackend.list_devices():
            dev = dict(dev)
            dev.setdefault("source", "soapy")
            merged[_key_of(dev)] = dev
    except Exception as e:
        logger.warning(f"SoapySDR 枚举异常: {e}")

    # 2) 原生 RTL-SDR（pyrtlsdr）——与 SoapySDR 的 rtlsdr 条目去重
    try:
        for dev in RTLSDRBackend.list_devices():
            serial = dev.get("serial", "")
            key = serial or f"rtl_index_{dev.get('index', 0)}"
            if key in merged:
                continue  # SoapySDR 已识别同 serial 设备，保留信息更全的那条
            merged[key] = {
                "driver": "rtlsdr",
                "source": "rtl_native",  # 原生 pyrtlsdr 条目：build_backend 走 RTLSDRBackend
                "label": f"RTL-SDR #{dev.get('index', 0)} ({dev.get('tuner', '?')})",
                "serial": serial,
                "manufacturer": "",
                "product": dev.get("tuner", ""),
                "gain_range": (0.0, 49.6),
                # 来源: SoapyRTLSDR/Settings.cpp:489-497 —— RTL-SDR 两段合法采样率
                "sample_rate_range": (225001, 3_200_000),
                # 来源: SoapyRTLSDR/Settings.cpp:410-427 —— R820T 类默认 24MHz~1.764GHz
                "freq_range": (24e6, 1764e6),
                "capability_status": {
                    "freq_range": "declared", "sample_rate_range": "declared",
                    "gain_range": "declared", "bandwidth_range": "unknown",
                    "tx": "unknown", "antenna": "unknown",
                },
                "capability_note": {
                    "bandwidth_range": "原生枚举未查询带宽",
                    "tx": "原生枚举未查询 TX 通道",
                    "antenna": "原生枚举未查询天线口",
                },
                "device_args": {"driver": "rtlsdr", "index": dev.get("index", 0)},
            }
    except Exception as e:
        logger.warning(f"原生 RTL-SDR 枚举异常: {e}")

    # 3) HackRF / bladeRF / LimeSDR：不再无条件列出。
    #    这些设备只有在被真实枚举到时才出现——SoapySDR 总线(步骤1)或
    #    gr-osmosdr(步骤4)探测到即列出；无对应库/无设备时不产生任何条目。
    # 4) PlutoSDR (Analog Devices ADALM-PLUTO / AD9361)：
    #      真实枚举由 PlutoSDRBackend.enumerate() 负责（libiio scan_contexts + 网络探测）。
    #      无 libiio/SoapySDR 或无硬件时 enumerate() 返回 []，绝不伪造设备。
    #      频率出厂 325 MHz–3.8 GHz（可解锁 70 MHz–6 GHz），覆盖 2.2 GHz LRO / 1.69 GHz GK-2A。
    try:
        for dev in PlutoSDRBackend.enumerate():
            key = dev.get("uri") or f"plutosdr_{dev.get('serial', '')}"
            if key in merged:
                continue
            merged[key] = {
                "driver": "plutosdr",
                "label": dev.get("label", "PlutoSDR (ADALM-PLUTO)"),
                "serial": dev.get("serial", ""),
                "manufacturer": dev.get("manufacturer", "Analog Devices"),
                "product": dev.get("product", "ADALM-PLUTO"),
                "gain_range": dev.get("gain_range",
                                      (PlutoSDRBackend.GAIN_MIN_DB,
                                       PlutoSDRBackend.GAIN_MAX_DB)),
                "sample_rate_range": dev.get("sample_rate_range",
                                             (PlutoSDRBackend.SR_MIN_HZ,
                                              PlutoSDRBackend.SR_MAX_HZ)),
                "freq_range": dev.get("freq_range",
                                      (PlutoSDRBackend.FREQ_MIN_HZ,
                                       PlutoSDRBackend.FREQ_MAX_HZ)),
                "capability_status": {
                    "freq_range": "declared", "sample_rate_range": "declared",
                    "gain_range": "declared", "bandwidth_range": "unknown",
                    "tx": "unknown", "antenna": "unknown",
                },
                "capability_note": {
                    "bandwidth_range": "Pluto 枚举未查询带宽",
                    "tx": "Pluto 枚举未查询 TX 通道",
                    "antenna": "Pluto 枚举未查询天线口",
                },
                "device_args": {"uri": dev.get("uri", "")},
            }
    except Exception as e:
        logger.warning(f"PlutoSDR 枚举异常: {e}")

    # 5) gr-osmosdr 通用后端枚举（rtl/hackrf/bladerf/uhd/soapy 统一设备字符串）
    #    参考: mbdsdr_ai/osmosdr_source.py（gr-osmosdr 设备字符串约定仅作技术参考）
    #    仅补充尚未被 SoapySDR/pyrtlsdr 识别到的后端（bladerf/uhd/airspy 等）。
    #    无库/无设备时 DeviceEnumerator.enumerate() 返回 []，绝不造假。
    try:
        from .osmosdr_source import DeviceEnumerator as _OsmoEnum
        for dev in _OsmoEnum.enumerate():
            key = dev.get("serial") or f"osmo_{dev.get('driver')}_{dev.get('index', 0)}"
            if key in merged:
                continue
            merged[key] = {
                "driver": dev.get("driver"),
                "label": dev.get("label", dev.get("driver", "")),
                "serial": dev.get("serial", ""),
                "manufacturer": "",
                "product": dev.get("subdriver", ""),
                # gr-osmosdr 枚举只返回设备字符串，范围未打开设备查询。
                # 以前填 (0.0, 0.0) 会被 UI 当成「真实范围 0–0」并拦住连接；
                # 这里如实留空 = 未知。
                "gain_range": None,
                "sample_rate_range": None,
                "freq_range": None,
                "bandwidth_range": None,
                "antennas": None,
                "tx_channels": None,
                "capability_status": {
                    "freq_range": "unknown", "sample_rate_range": "unknown",
                    "gain_range": "unknown", "bandwidth_range": "unknown",
                    "antenna": "unknown", "tx": "unknown",
                },
                "capability_note": {
                    "freq_range": "osmosdr 枚举只给设备字符串，未查询范围",
                    "sample_rate_range": "osmosdr 枚举只给设备字符串，未查询范围",
                    "gain_range": "osmosdr 枚举只给设备字符串，未查询范围",
                    "bandwidth_range": "osmosdr 枚举只给设备字符串，未查询带宽",
                    "antenna": "osmosdr 枚举只给设备字符串，未查询天线口",
                    "tx": "osmosdr 枚举只给设备字符串，未查询 TX",
                },
                "device_args": {"osmosdr_string": dev.get("device_string", "")},
            }
    except Exception as e:
        logger.warning(f"gr-osmosdr 枚举异常: {e}")

    # 6) rtl_tcp 网络源（env 驱动显式登记）：上面 USB/网络枚举都失败时的常见
    #    情形——本机装不上 librtlsdr/SoapySDR、棒在远端、或多客户端共享同一根。
    #    登记的是真实网络前端（RTLSDRBackend(host=...) 走自研 MIT rtl_tcp
    #    客户端）；enumerate 不联网、不 connect，成败交给 backend.connect()。
    #    rtl_tcp 是单客户端协议，同 host:port 重复登记没有意义——按 key 去重。
    for _host, _port in parse_rtl_tcp_env(os.environ.get(RTL_TCP_ENV, "")):
        _key = f"rtl_tcp_{_host}_{_port}"
        if _key in merged:
            continue
        merged[_key] = {
            "driver": "rtlsdr",
            "source": "rtl_tcp",
            "label": f"rtl_tcp {_host}:{_port}",
            "serial": _key,
            "manufacturer": "osmocom rtl_tcp",
            "product": "RTL-SDR (network source)",
            "gain_range": (0.0, 49.6),
            "sample_rate_range": (225001, 3_200_000),
            "freq_range": (24e6, 1764e6),
            "capability_status": {
                "freq_range": "declared", "sample_rate_range": "declared",
                "gain_range": "declared", "bandwidth_range": "unknown",
                "tx": "unknown", "antenna": "unknown",
            },
            "capability_note": {
                "bandwidth_range": "rtl_tcp 未查询带宽",
                "tx": "rtl_tcp 未查询 TX 通道",
                "antenna": "rtl_tcp 未查询天线口",
            },
            "device_args": {"driver": "rtlsdr", "host": _host, "port": _port},
        }

    # 6b) 通用网络 IQ 源（env 驱动显式登记，sdrpp network_source 同机制）。
    #     与 rtl_tcp 同样不联网、不 connect；rate/freq 是配置声明的口径，
    #     真实流参数由对端决定，不一致时由解码质量暴露。按 key 去重。
    for _cfg in parse_network_iq_env(os.environ.get(NETWORK_IQ_ENV, "")):
        _key = f"net_{_cfg['protocol']}_{_cfg['host']}_{_cfg['port']}"
        if _key in merged:
            continue
        merged[_key] = {
            "driver": "network_iq",
            "source": "network_iq",
            "label": (f"网络 IQ {_cfg['protocol']}://"
                      f"{_cfg['host']}:{_cfg['port']}"),
            "serial": _key,
            "manufacturer": "network IQ source",
            "product": f"裸流 IQ ({_cfg['fmt']})",
            "gain_range": (0.0, 0.0),
            "sample_rate_range": (1, 1_000_000_000),
            "freq_range": (0, 12e9),
            "capability_status": {
                "freq_range": "declared", "sample_rate_range": "declared",
                "gain_range": "unknown", "bandwidth_range": "unknown",
                "tx": "unknown", "antenna": "unknown",
            },
            "capability_note": {
                "gain_range": "网络裸流无增益控制",
                "bandwidth_range": "网络裸流无带宽查询",
                "tx": "网络裸流未查询 TX 通道",
                "antenna": "网络源无天线口概念",
            },
            "device_args": dict(_cfg),
        }

    # 7) 去重收尾：osmosdr 的 "rtl" 条目与原生 RTLSDR 走同一根 librtlsdr，
    #    同一根棒会出现两次（一条带真实增益/频率范围的 rtl_native，一条 osmosdr
    #    的空参数 rtl 条目）。原生条目信息更全，优先保留；同 index 时丢 osmosdr 条目。
    native_rtl_idx = {
        int(d.get("device_args", {}).get("index", -1))
        for d in merged.values() if d.get("source") == "rtl_native"
    }
    out: List[Dict[str, Any]] = []
    for d in merged.values():
        # bytes 标签统一 decode（ctypes char* 可能返回 bytes）
        if isinstance(d.get("label"), bytes):
            d["label"] = d["label"].decode("utf-8", "replace")
        if d.get("driver") == "rtl" and d.get("source") is None:
            _s = str(d.get("device_args", {}).get("osmosdr_string", "rtl=0"))
            try:
                _idx = int(_s.split("=", 1)[-1])
            except ValueError:
                _idx = 0
            if _idx in native_rtl_idx:
                continue  # 原生条目已覆盖同一根 RTL 棒
        out.append(d)

    return out


def build_backend_for_device(dev: Dict[str, Any]) -> Optional[SDRBackend]:
    """根据 enumerate_all_sdr_devices() 产出的设备 dict 构造对应后端实例。

    真正 connect 的成败由后端自己负责，本函数不假装成功。
    """
    driver = (dev.get("driver") or "").lower()
    args = dev.get("device_args") or {}
    try:
        # 纯 pyrtlsdr 原生条目（enumerate_all_sdr_devices 步骤2 打的 source="rtl_native"）
        # 走原生 RTLSDRBackend（环形缓冲）。注意不能再用 "device_args 里有没有 index"
        # 判断：原生条目和 SoapySDR 的 rtlsdr 条目 device_args 都可能带 driver 键，
        # 必须用显式 source 标记区分，否则原生棒会被错派给 SoapySDRBackend（无 Soapy
        # 绑定时 connect 必失败）。
        if driver == "rtlsdr" and dev.get("source") == "rtl_native":
            return RTLSDRBackend(device_index=int(args.get("index", 0)))
        # env 登记的 rtl_tcp 网络源（enumerate 步骤6）：走 RTLSDRBackend 的
        # host/port 模式（自研 RtlTcpClient + RtlTcpDeviceAdapter，MIT 无 GPL）。
        # 真正 connect 的成败由后端自己负责，本函数不假装成功。
        if driver == "rtlsdr" and dev.get("source") == "rtl_tcp":
            return RTLSDRBackend(
                host=str(args.get("host", "127.0.0.1")),
                port=int(args.get("port", 1234)),
                ppm=int(args.get("ppm", 0)))
        if driver == "hackrf":
            return HackRFBackend(device_index=int(args.get("index", 0)))
        if driver == "bladerf":
            # 真实打开由 BladeRFBackend.connect() 决定成败；
            # 无 libbladeRF/无设备时 connect 返回 False，绝不假成功。
            # 参考: mbdsdr_ai/bladerf_params.py（依据 bladeRF 公开数据手册整理）。
            from .bladerf_params import BladeRFBackend as _BladeRFBackend
            return _BladeRFBackend(
                device_identifier=str(args.get("device_identifier", "") or ""))
        if driver == "limesdr":
            # 真实打开由 LimeSDRBackend.connect() 决定成败；
            # 无 libLimeSuite/无设备时 connect 返回 False，绝不假成功。
            # 参考: mbdsdr_ai/limesuite_params.py（依据 LimeSDR 公开数据手册整理）。
            from .limesuite_params import LimeSDRBackend as _LimeSDRBackend
            return _LimeSDRBackend(device_index=int(args.get("index", 0)))
        if driver == "plutosdr":
            # 真实打开由 PlutoSDRBackend.connect() 决定成败；
            # 无 libiio/SoapyPlutoSDR/无硬件时 connect 返回 False 并在 status.error
            # 标注 "[未连接-无libiio/SoapySDR驱动]"，绝不假成功。
            return PlutoSDRBackend(
                uri=str(args.get("uri", "") or None),
                device_info=dev,
            )
        if driver == "network_iq":
            # env 登记的通用网络 IQ 源（enumerate 步骤6b）：裸流参数全部来自
            # 配置，缺键/非法值在构造期抛错 -> 本函数返回 None，绝不假成功。
            return NetworkIQBackend(
                protocol=args["protocol"], host=args["host"], port=args["port"],
                sample_rate_hz=args["sample_rate_hz"],
                center_freq_hz=args["center_freq_hz"],
                fmt=args.get("fmt", "cu8"),
                byteorder=args.get("byteorder", "little"))
        # 其余一律走 SoapySDR 通用后端（rtlsdr 经 SoapySDR、usrp、bladerf...）
        return SoapySDRBackend(device_args=args, device_info=dev)
    except Exception as e:
        logger.error(f"构造 {driver} 后端失败: {e}")
        return None
