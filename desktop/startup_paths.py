# SPDX-License-Identifier: MIT
"""启动期路径与运行库探测（无 Qt / 无 GUI 依赖，可单测）。

三条铁律落实在这里，main.py 只做接线：

1. **日志落点不许写 C 盘用户目录**：顺序固定为
   环境变量 ``MBDSDR_STARTUP_LOG`` → 非 C 盘盘符 → 安装根 logs（可写） →
   ``%PROGRAMDATA%\\MBDSDR\\logs``；全部不可写时**进内存环形缓冲**，
   由错误出口说明「本次无持久日志」，绝不回落到 ``%LOCALAPPDATA%`` / ``~/.config``。
2. **可写性探测不许阻塞启动**：ACL deny-write 目录上创建文件会卡数十秒
   （实测 faulthandler 停在 ``tempfile._mkstemp_inner``），所以试写放到后台
   daemon 线程，0 字节文件写完即删，主线程不等。
3. **运行期 DLL 存在性检查**：打包版 ``_internal`` 里应该有的 DLL 缺失时，
   给出「缺运行库：<dll>」而不是让用户看 ``0xC0000135``。
"""

import collections
import os
import sys
import threading

_STARTUP_LOG_NAME = "mbdsdr_startup.log"

#: 落点全部不可写时的内存缓冲（最多 200 行，仅本次进程有效）。
_LOG_BUFFER = collections.deque(maxlen=200)

#: 最近一次**实际写成功**的持久日志路径（空串 = 本次进程还没写成过）。
#: 诊断页只能读这个值：``startup_log_path()`` 会 ``makedirs``，展示不能有副作用。
_LAST_LOG_PATH = ""

#: 打包版 ``_internal`` 必须随包的运行库：缺 = 打包缺陷（不是用户操作问题）。
_PACKAGED_DLLS = ("librtlsdr.dll", "libusb-1.0.dll")
#: 环境级运行库：缺只影响对应硬件后端，不算打包缺陷。
_OPTIONAL_DLLS = ("SoapySDR.dll",)


def program_dir() -> str:
    """程序根目录：冻结版是 exe 所在目录，源码版是仓库根目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log_candidates() -> "list[str]":
    """按固定顺序列出日志候选路径（不含「是否可写」判断，便于断言）。"""
    candidates = []
    override = (os.environ.get("MBDSDR_STARTUP_LOG") or "").strip()
    if override:
        candidates.append(os.path.expanduser(override))
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.isdir(root):
            candidates.append(os.path.join(root, "mbdsdr", "logs",
                                          _STARTUP_LOG_NAME))
    candidates.append(os.path.join(program_dir(), "logs", _STARTUP_LOG_NAME))
    programdata = os.path.expandvars("%PROGRAMDATA%")
    if programdata and not programdata.startswith("%"):
        candidates.append(os.path.join(programdata, "MBDSDR", "logs",
                                      _STARTUP_LOG_NAME))
    return candidates


def startup_log_path() -> str:
    """返回第一个**确实可创建**的日志落点；全部不可写时返回空字符串。"""
    for path in log_candidates():
        try:
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            return path
        except OSError:
            continue
    return ""


def append_startup_log(text: str) -> str:
    """写一行启动日志；无持久落点时进内存缓冲并返回空字符串。"""
    global _LAST_LOG_PATH
    line = text.rstrip()
    path = startup_log_path()
    if not path:
        _LOG_BUFFER.append(line)
        return ""
    try:
        with open(path, "a", encoding="utf-8", errors="replace") as f:
            f.write(line + "\n")
        _LAST_LOG_PATH = path
        return path
    except OSError:
        _LOG_BUFFER.append(line)
        return ""


def last_log_path() -> str:
    """最近一次实际写入的日志路径；本次进程尚未写成过时返回空串。

    只读、无副作用。诊断页必须用这个而不是 ``startup_log_path()``
    （后者会创建目录，展示动作不该改磁盘）。
    """
    return _LAST_LOG_PATH


def buffered_log() -> "list[str]":
    """取本次进程的内存日志（无持久落点时唯一可查副本）。"""
    return list(_LOG_BUFFER)


def persistent_log_note() -> str:
    """无持久落点时的固定说明（错误出口必须带上这句）。"""
    return "本次无持久日志（原因：无可用写入位置）。"


def probe_dir_writable_async(directory: str, callback) -> None:
    """后台线程试写一个 0 字节文件并删除；主线程不等待。

    ``callback(error: str)`` 在**工作线程**里被调用：空字符串表示可写，
    否则是可直接展示给用户的错误说明。GUI 侧必须自行切回主线程。
    目录不存在时先尝试创建（同样在工作线程里，卡住也不影响启动）。
    """
    def _run():
        error = ""
        try:
            os.makedirs(directory, exist_ok=True)
            probe = os.path.join(directory,
                                 f".mbdsdr_write_probe_{os.getpid()}")
            with open(probe, "wb"):
                pass
            os.remove(probe)
        except OSError as exc:
            error = (f"录音目录不可写（{directory}）：{exc}。"
                     "请在「设置 → 数据与操作」改到可写目录。")
        callback(error)

    threading.Thread(target=_run, name="mbdsdr-writable-probe",
                     daemon=True).start()


def _dll_search_dirs() -> "list[str]":
    """DLL 查找目录：程序根、``_internal``、``bin``、PATH 各目录。"""
    dirs = [program_dir()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
    dirs.append(os.path.join(program_dir(), "_internal"))
    dirs.append(os.path.join(program_dir(), "bin"))
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if entry:
            dirs.append(entry)
    seen, unique = set(), []
    for directory in dirs:
        key = os.path.normcase(os.path.abspath(directory))
        if key not in seen:
            seen.add(key)
            unique.append(directory)
    return unique


def runtime_dll_report() -> dict:
    """检查运行期 DLL 是否存在。

    返回 ``{"missing_packaged": [...], "missing_optional": [...]}``：
    - ``missing_packaged`` 非空 = 打包版缺件（应重新安装完整包）；
    - ``missing_optional`` 非空 = 环境缺 SoapySDR 之类，只影响对应后端。
    """
    found = set()
    for directory in _dll_search_dirs():
        try:
            for name in os.listdir(directory):
                full = os.path.join(directory, name)
                if os.path.isfile(full):
                    found.add(name.lower())
        except OSError:
            continue
    missing_packaged = [d for d in _PACKAGED_DLLS if d.lower() not in found]
    missing_optional = [d for d in _OPTIONAL_DLLS if d.lower() not in found]
    return {
        "missing_packaged": missing_packaged,
        "missing_optional": missing_optional,
    }
