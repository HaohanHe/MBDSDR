# SPDX-License-Identifier: MIT
"""诊断脚本（sdr_diag / system_diag）的子进程隔离与残留进程守卫。

真机背景：pyrtlsdr 的同步读走 librtlsdr ``rtlsdr_read_sync``，内部
``libusb_bulk_transfer`` 没有超时。设备卡死时调用线程永远出不来；过去用
「进程内 daemon 线程兜底 + 退出时对自己 ``TerminateProcess``」的做法，
2026-11-18 真机实测留下了 HasExited=True 但仍在烧 CPU、杀不掉的残留进程，
并且很可能一直占着 RTL-SDR 的 USB 句柄。

现行约定：

- 设备 I/O 只允许在**真子进程**里发生（同文件的隐藏 ``--_worker`` 入口）；
- 父进程最多等 ``timeout + 启动宽限``，超时后只对子进程句柄 terminate；
- 终止后 ``RESIDUAL_GRACE_S`` 秒内仍存活 = ``residual``，如实上报 PID；
- 启动前扫描本机同类诊断进程，避免继续往上堆进程（``--force`` 跳过）。

本模块只依赖标准库，不引 psutil；Windows 上的进程枚举/命令行读取用
``ctypes`` 调系统 API（``EnumProcesses`` + ``NtQueryInformationProcess``）。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

#: 父进程在 ``--timeout`` 之外额外等待的秒数（覆盖解释器启动/导入/枚举/打开）。
WORKER_STARTUP_GRACE_S = 5.0

#: terminate 之后最多再等多久；仍存活即判定 residual。
RESIDUAL_GRACE_S = 2.0

#: 视为「诊断实例」的脚本文件名。
DIAG_SCRIPT_NAMES: Tuple[str, ...] = ("sdr_diag.py", "system_diag.py")

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_PROCESS_COMMAND_LINE_INFORMATION = 60


@dataclass
class WorkerOutcome:
    """一次有界子进程执行的结果。

    ``result`` 是子进程写出的 JSON（可能只是阶段进度；带 ``verdict`` 才算
    完整结果）。``timed_out`` 表示父进程的等待上限先到；``residual`` 表示
    terminate 之后仍有存活迹象（此时 ``pid`` 很可能仍占着设备句柄）。
    """

    pid: int
    returncode: Optional[int]
    timed_out: bool
    residual: bool
    result: Optional[Dict[str, Any]]
    elapsed_s: float
    stderr_tail: str = ""


# ---------------------------------------------------------------------------
# 原子写 JSON（子进程进度/最终结果）
# ---------------------------------------------------------------------------
def write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
    """尽力原子写；失败不抛出（进度文件只是诊断辅助，不是事实源）。"""
    if not path:
        return
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# 进程存活判断（纯 ctypes）
# ---------------------------------------------------------------------------
if os.name == "nt":  # pragma: no cover - 本仓主要开发环境
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.argtypes = [
        ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.GetExitCodeProcess.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    _kernel32.GetExitCodeProcess.restype = ctypes.c_int
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32.CloseHandle.restype = ctypes.c_int


def pid_alive(pid: int) -> bool:
    """PID 是否仍存活（兼容 Windows / POSIX）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - 本仓主要开发环境
        handle = _kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if not handle:
            # ERROR_ACCESS_DENIED 说明进程存在但无权查询；其余按不存在处理。
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            code = ctypes.c_ulong()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == _STILL_ACTIVE
        finally:
            _kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# 本机诊断进程枚举（不引 psutil）
# ---------------------------------------------------------------------------
def _iter_pids_windows() -> Iterable[int]:  # pragma: no cover - 真机路径
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.EnumProcesses.argtypes = [
        ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong)]
    psapi.EnumProcesses.restype = ctypes.c_int
    capacity = 1024
    while True:
        buf = (ctypes.c_ulong * capacity)()
        used = ctypes.c_ulong(0)
        if not psapi.EnumProcesses(
                buf, ctypes.sizeof(buf), ctypes.byref(used)):
            return
        count = used.value // ctypes.sizeof(ctypes.c_ulong)
        if count < capacity:
            for i in range(count):
                if buf[i]:
                    yield int(buf[i])
            return
        capacity *= 2


def _query_command_line(pid: int) -> Optional[str]:  # pragma: no cover
    """用 NtQueryInformationProcess(ProcessCommandLineInformation) 取命令行。

    没有权限/系统不支持时返回 None（不猜测，也不据此告警）。
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    kernel32.OpenProcess.argtypes = [
        ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    ntdll.NtQueryInformationProcess.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
        ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
    ntdll.NtQueryInformationProcess.restype = ctypes.c_long

    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
    if not handle:
        return None
    try:
        # 命令行上限 32767 个 UTF-16 字符；一次给足 64 KiB，避免两段式查询。
        buf = ctypes.create_string_buffer(64 * 1024)
        got = ctypes.c_ulong(0)
        status = ntdll.NtQueryInformationProcess(
            handle, _PROCESS_COMMAND_LINE_INFORMATION,
            ctypes.byref(buf), ctypes.sizeof(buf), ctypes.byref(got))
        if status != 0:
            return None
        # UNICODE_STRING { USHORT Length; USHORT MaximumLength; WCHAR *Buffer; }
        length = ctypes.c_ushort.from_buffer(buf, 0).value
        if not length:
            return ""
        ptr_size = ctypes.sizeof(ctypes.c_void_p)
        ptr_offset = 8 if ptr_size == 8 else 4
        string_ptr = ctypes.c_void_p.from_buffer(buf, ptr_offset).value
        base = ctypes.addressof(buf)
        offset = (string_ptr - base) if string_ptr else (ptr_offset + ptr_size)
        if offset < 0 or offset + length > ctypes.sizeof(buf):
            offset = ptr_offset + ptr_size
        raw = buf.raw[offset:offset + length]
        return raw.decode("utf-16-le", errors="replace")
    except Exception:
        return None
    finally:
        kernel32.CloseHandle(handle)


def _iter_process_command_lines_posix() -> Iterable[Tuple[int, str]]:
    proc = "/proc"
    if not os.path.isdir(proc):
        return
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc, name, "cmdline"),
                      "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        yield int(name), raw.replace(b"\x00", b" ").decode(
            "utf-8", errors="replace")


def iter_process_command_lines() -> Iterable[Tuple[int, str]]:
    """枚举 ``(pid, command_line)``；读不到的进程直接跳过。"""
    if os.name == "nt":  # pragma: no cover - 真机路径
        for pid in _iter_pids_windows():
            cmd = _query_command_line(pid)
            if cmd:
                yield pid, cmd
        return
    yield from _iter_process_command_lines_posix()


CommandLineProvider = Callable[[], Iterable[Tuple[int, str]]]

_TOKEN_RE = re.compile(r'"([^"]*)"|(\S+)')


def _split_command_line(command_line: str) -> List[str]:
    return [quoted or bare for quoted, bare in _TOKEN_RE.findall(command_line or "")]


def _is_diag_command(command_line: str, wanted: Sequence[str]) -> bool:
    """命令行的「脚本参数」是否就是诊断脚本（避免误报同名子串）。

    只认 Python 解释器进程，且要求某个参数 token 的 basename 精确等于
    ``sdr_diag.py`` / ``system_diag.py``。这样 ``pytest tests/test_sdr_diag.py``
    或 shell 包装器里出现同名子串不会被当成诊断实例。
    """
    tokens = _split_command_line(command_line)
    if not tokens:
        return False
    exe = os.path.basename(tokens[0].replace("\\", "/")).lower()
    if not (exe.startswith("python") or exe in {"py", "py.exe", "pyw.exe"}):
        return False
    for token in tokens[1:]:
        base = os.path.basename(token.replace("\\", "/")).lower()
        if base in wanted:
            return True
    return False


def find_stuck_diag_processes(
    *,
    exclude_pids: Sequence[int] = (),
    script_names: Sequence[str] = DIAG_SCRIPT_NAMES,
    provider: Optional[CommandLineProvider] = None,
) -> List[Tuple[int, str]]:
    """返回本机正在运行的诊断脚本进程 ``[(pid, command_line), ...]``。

    「正在运行」不区分是否卡死：只要还活着就可能仍占着设备句柄，再启动一份
    只会继续堆进程。命令行读不到时不做猜测（避免误杀/误报）。
    """
    provider = provider or iter_process_command_lines
    excluded = {int(p) for p in exclude_pids if str(p).strip()}
    wanted = tuple(str(n).lower() for n in script_names)
    found: List[Tuple[int, str]] = []
    try:
        entries = list(provider())
    except Exception:
        return []
    for pid, cmdline in entries:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        if pid <= 0 or pid in excluded:
            continue
        if _is_diag_command(str(cmdline or ""), wanted):
            found.append((pid, str(cmdline or "")))
    return found


# ---------------------------------------------------------------------------
# 有界子进程执行
# ---------------------------------------------------------------------------
def _default_terminate(proc: Any) -> None:
    try:
        proc.terminate()
    except Exception:
        pass


def run_worker(
    command: Sequence[str],
    timeout_s: float,
    result_path: str,
    *,
    startup_grace_s: float = WORKER_STARTUP_GRACE_S,
    residual_grace_s: float = RESIDUAL_GRACE_S,
    spawn: Optional[Callable[..., Any]] = None,
    terminate_fn: Optional[Callable[[Any], None]] = None,
) -> WorkerOutcome:
    """运行子进程并在 ``timeout_s + startup_grace_s`` 后强制收口。

    - 正常退出：返回它的 returncode 与 ``result_path`` 里的 JSON；
    - 超时：只对子进程句柄 terminate，再等 ``residual_grace_s``；
      仍存活则 ``residual=True``，PID 交给调用方如实上报。
    """
    spawn = spawn or subprocess.Popen
    terminate_fn = terminate_fn or _default_terminate
    limit_s = max(float(timeout_s), 0.0) + max(float(startup_grace_s), 0.0)

    stderr_file = tempfile.TemporaryFile()
    started = time.monotonic()
    proc = spawn(
        list(command),
        stdout=subprocess.DEVNULL,
        stderr=stderr_file,
        stdin=subprocess.DEVNULL,
    )
    pid = int(getattr(proc, "pid", 0) or 0)
    timed_out = False
    residual = False
    try:
        try:
            proc.wait(timeout=limit_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            terminate_fn(proc)
            try:
                proc.wait(timeout=max(float(residual_grace_s), 0.0))
            except subprocess.TimeoutExpired:
                residual = True
        returncode = getattr(proc, "returncode", None)
        if returncode is None and getattr(proc, "poll", None):
            try:
                returncode = proc.poll()
            except Exception:
                returncode = None
        elapsed = time.monotonic() - started
        tail = ""
        try:
            stderr_file.seek(0)
            tail = stderr_file.read().decode("utf-8", errors="replace")[-2000:]
        except Exception:
            tail = ""
        return WorkerOutcome(
            pid=pid,
            returncode=None if returncode is None else int(returncode),
            timed_out=timed_out,
            residual=bool(residual),
            result=read_json(result_path),
            elapsed_s=elapsed,
            stderr_tail=tail,
        )
    finally:
        try:
            stderr_file.close()
        except Exception:
            pass
        if residual:
            # 子进程若奇迹般退出，也不要留下僵尸句柄；不强杀（可能无权）。
            try:
                proc.poll()
            except Exception:
                pass


__all__ = [
    "DIAG_SCRIPT_NAMES",
    "RESIDUAL_GRACE_S",
    "WORKER_STARTUP_GRACE_S",
    "WorkerOutcome",
    "find_stuck_diag_processes",
    "iter_process_command_lines",
    "pid_alive",
    "read_json",
    "run_worker",
    "write_json_atomic",
]
