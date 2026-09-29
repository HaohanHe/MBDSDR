# SPDX-License-Identifier: MIT
"""
MBDSDR AI - Windows DLL 自动加载
====================================

真机（Windows）运行前，pyrtlsdr 通过 ctypes 加载 ``rtlsdr.dll``，而它还依赖
``libusb-1.0.dll``。Python 3.8+ 不再自动从 PATH 目录搜索 DLL，必须显式调用
``os.add_dll_directory()``，否则 ``import rtlsdr`` / ``Rtlsdr()`` 会抛
``FileNotFoundError: Could not find module ...rtlsdr.dll``。

本模块职责（只读、只搜索、不下载）：
- 在常见安装路径里搜索 ``rtlsdr.dll`` 与 ``libusb-1.0.dll``；
- 找到后用 ``os.add_dll_directory()``（Py3.8+）或追加 PATH，让后续
  ``import rtlsdr`` 成功；
- 找不到时返回**明确的中文提示**，绝不抛异常崩溃主程序。

安全红线：
- **绝不自动下载 DLL**。二进制下载属于供应链风险，本模块只做本地搜索 +
  安装提示（提示用户用 SDR# 安装包或 Zadig 安装 WinUSB 驱动）。

典型用法（在程序入口、``import rtlsdr`` 之前调用一次）::

    from mbdsdr_ai.windows_setup import setup_rtlsdr_windows
    result = setup_rtlsdr_windows()
    if not result["ok"]:
        print(result["message"])   # 明确提示，不崩溃
    else:
        import rtlsdr              # 此时能正常加载

测试友好：所有搜索都走可注入的 ``exists`` 谓词与候选目录，
单测用临时目录即可确定性验证搜索逻辑，无需真机/真 DLL。
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

#: 找不到 DLL 时给用户的明确提示（安全红线：不自动下载，只指引安装）
RTLSDR_NOT_FOUND_MSG = "未找到 rtlsdr.dll，请安装 SDR# 或 Zadig 驱动"

RTLSDR_DLL_NAME = "rtlsdr.dll"
LIBUSB_DLL_NAME = "libusb-1.0.dll"

#: Windows 下 PATH 分隔符；Linux/macOS 下为 ":"
if os.name == "nt":  # pragma: no cover - 仅 Windows 生效
    _PATH_SEP = ";"
else:
    _PATH_SEP = ":"

#: 有限深度遍历时跳过的目录（体量巨大/与 SDR 无关），避免卡顿
_SKIP_DIRS = {
    "windows", "$recycle.bin", "system32", "syswow64", "node_modules",
    ".git", "__pycache__", "appdata",
}


@functools.lru_cache(maxsize=1)
def discover_dll_dirs_windows(max_depth: int = 3) -> tuple:
    """在常见用户/程序目录里做**有限深度**搜索，返回含 ``rtlsdr.dll`` 的目录。

    SDR++/SDR# 常被解压到 Downloads 或 Desktop，路径不固定，因此这里在
    Downloads / Desktop / Documents / Program Files / LOCALAPPDATA 下最多
    向下 ``max_depth`` 层查找。只在 Windows 执行；任何异常都不抛出。
    """
    if sys.platform != "win32":
        return ()

    roots: List[str] = []
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        roots += [
            os.path.join(userprofile, "Downloads"),
            os.path.join(userprofile, "Desktop"),
            os.path.join(userprofile, "Documents"),
        ]
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        val = os.environ.get(key)
        if val:
            roots.append(val)

    found: List[str] = []
    seen: set = set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        root = os.path.abspath(root)
        base_depth = root.rstrip(os.sep).count(os.sep)
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                depth = dirpath.count(os.sep) - base_depth
                if depth >= max_depth:
                    dirnames[:] = []
                dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
                if RTLSDR_DLL_NAME in filenames and dirpath not in seen:
                    seen.add(dirpath)
                    found.append(dirpath)
        except Exception as exc:  # 某个根不可访问不应中断整体搜索
            logger.debug("遍历 %s 失败: %s", root, exc)
            continue
    return tuple(found)



def default_candidate_dirs() -> List[str]:
    """返回 DLL 搜索候选目录（顺序敏感，去重保留首次出现）。

    覆盖任务要求的全部位置：
    - 当前工作目录
    - 本文件所在目录（随包分发时 DLL 可放在 mbdsdr_ai/ 旁）
    - ``lib/`` 相对目录
    - PATH 环境变量里的每个目录
    - ``C:\\rtl-sdr\\bin``
    - ``%USERPROFILE%\\rtl-sdr\\bin``
    """
    dirs: List[str] = []

    # 用户显式指定的 DLL 目录（环境变量），优先级最高
    override = os.environ.get("MBDSDR_RTLSDR_DIR", "").strip()
    if override:
        dirs.append(override)

    # 当前工作目录
    try:
        dirs.append(os.getcwd())
    except Exception:
        pass

    # 本文件所在目录（包内附带 DLL 的情况）
    try:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass

    # 相对 lib/ 目录
    dirs.append("lib")

    # PATH 里的目录
    path_env = os.environ.get("PATH", "")
    for p in path_env.split(_PATH_SEP):
        p = p.strip()
        if p:
            dirs.append(p)

    # 常见 rtl-sdr 安装位置（Windows）
    dirs.append(r"C:\rtl-sdr\bin")
    userprofile = os.environ.get("USERPROFILE", "")
    if userprofile:
        dirs.append(os.path.join(userprofile, "rtl-sdr", "bin"))

    # SDR++/SDR# 等解压到 Downloads/Desktop 的情况：有限深度自动发现
    if sys.platform == "win32":
        try:
            dirs.extend(discover_dll_dirs_windows())
        except Exception as exc:
            logger.debug("自动发现 DLL 目录失败: %s", exc)

    # 去重、保序
    seen = set()
    out: List[str] = []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _search_dll(
    filename: str,
    candidate_dirs: List[str],
    exists: Callable[[str], bool] = os.path.isfile,
) -> Optional[str]:
    """在 candidate_dirs 里逐个寻找 ``<dir>/<filename>``，返回首个存在的完整路径。

    Parameters
    ----------
    filename : str
        例如 ``"rtlsdr.dll"``。
    candidate_dirs : list[str]
        待搜索目录列表。
    exists : callable
        路径存在性谓词，默认 ``os.path.isfile``；测试可注入 mock。

    Returns
    -------
    str or None
        首个命中的绝对/相对路径；全部未命中返回 None（不抛异常）。
    """
    for d in candidate_dirs:
        if not d:
            continue
        candidate = os.path.normpath(os.path.join(d, filename))
        try:
            if exists(candidate):
                return candidate
        except Exception as exc:  # 某个目录不可访问不应中断搜索
            logger.debug("检查 %s 失败: %s", candidate, exc)
        continue
    return None


def find_rtlsdr_dll(
    extra_dirs: Optional[List[str]] = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> Optional[str]:
    """搜索 ``rtlsdr.dll``。

    Parameters
    ----------
    extra_dirs : list[str], optional
        额外优先搜索目录（例如用户在配置里指定的 SDR# 安装目录）。
    exists : callable
        路径谓词，测试可注入。

    Returns
    -------
    str or None
        命中路径或 None。
    """
    dirs = default_candidate_dirs()
    if extra_dirs:
        dirs = list(extra_dirs) + dirs
    return _search_dll(RTLSDR_DLL_NAME, dirs, exists)


def find_libusb_dll(
    extra_dirs: Optional[List[str]] = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> Optional[str]:
    """搜索 ``libusb-1.0.dll``（rtlsdr.dll 的运行时依赖）。"""
    dirs = default_candidate_dirs()
    if extra_dirs:
        dirs = list(extra_dirs) + dirs
    return _search_dll(LIBUSB_DLL_NAME, dirs, exists)


def _register_dll_dir(dll_path: str) -> Dict[str, Any]:
    """把 DLL 所在目录注册进进程搜索路径。

    Python 3.8+ 用 ``os.add_dll_directory()``（更安全，只影响本进程 DLL 搜索），
    同时兜底追加 PATH（兼容旧版本与某些 ctypes 加载路径）。
    """
    dll_dir = os.path.dirname(dll_path) or "."
    info: Dict[str, Any] = {"dll_dir": dll_dir, "added_directory": False, "path_prepended": False}
    try:
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(dll_dir)  # type: ignore[attr-defined]
            info["added_directory"] = True
    except Exception as exc:  # 已注册/权限等不致命
        logger.debug("add_dll_directory(%s) 失败: %s", dll_dir, exc)

    try:
        os.environ["PATH"] = dll_dir + _PATH_SEP + os.environ.get("PATH", "")
        info["path_prepended"] = True
    except Exception as exc:
        logger.debug("追加 PATH 失败: %s", dll_dir, exc)
    return info


def setup_rtlsdr_windows(
    import_test: bool = True,
    extra_dirs: Optional[List[str]] = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> Dict[str, Any]:
    """为 ``import rtlsdr`` 准备好 Windows DLL 搜索环境。

    全程不抛异常：任何失败都通过返回值里的 ``ok`` 与 ``message`` 表达。

    Parameters
    ----------
    import_test : bool
        找到并注册 DLL 后，是否尝试 ``import rtlsdr`` 验证可加载。
    extra_dirs : list[str], optional
        额外搜索目录。
    exists : callable
        路径谓词（测试注入）。

    Returns
    -------
    dict
        ``{ok: bool, dll_path: str|None, libusb_path: str|None, message: str}``。
        找不到时 ``message`` 为 :data:`RTLSDR_NOT_FOUND_MSG`。
    """
    result: Dict[str, Any] = {
        "ok": False,
        "dll_path": None,
        "libusb_path": None,
        "message": "",
    }

    # 非 Windows 平台：本函数是 Windows 专用准备，明确说明而非报错崩溃
    if sys.platform != "win32":
        result["message"] = (
            "当前非 Windows 平台（%s），跳过 rtlsdr.dll 加载；"
            "Linux/macOS 请用包管理器安装 librtlsdr0/libusb。" % sys.platform
        )
        return result

    dll = find_rtlsdr_dll(extra_dirs=extra_dirs, exists=exists)
    if not dll:
        result["message"] = RTLSDR_NOT_FOUND_MSG
        logger.error(RTLSDR_NOT_FOUND_MSG)
        return result

    result["dll_path"] = dll
    result["libusb_path"] = find_libusb_dll(extra_dirs=extra_dirs, exists=exists)

    reg = _register_dll_dir(dll)
    # libusb 若在别的目录，也把它所在目录注册进来
    if result["libusb_path"]:
        try:
            lib_dir = os.path.dirname(result["libusb_path"])
            if lib_dir and os.path.normcase(lib_dir) != os.path.normcase(reg["dll_dir"]):
                _register_dll_dir(result["libusb_path"])
        except Exception:
            pass

    if not import_test:
        result["ok"] = True
        result["message"] = f"已注册 DLL 目录: {reg['dll_dir']}"
        return result

    try:
        import rtlsdr  # noqa: F401  (导入即触发 DLL 加载)
        result["ok"] = True
        result["message"] = f"rtlsdr DLL 已就绪: {dll}"
        return result
    except Exception as exc:  # DLL 找到了但仍加载失败（缺依赖/架构不符）
        result["message"] = (
            f"找到 rtlsdr.dll ({dll}) 但 import rtlsdr 失败: {exc}；"
            f"请确认安装了匹配架构的 {LIBUSB_DLL_NAME}。"
        )
        return result


__all__ = [
    "RTLSDR_NOT_FOUND_MSG",
    "RTLSDR_DLL_NAME",
    "LIBUSB_DLL_NAME",
    "default_candidate_dirs",
    "find_rtlsdr_dll",
    "find_libusb_dll",
    "setup_rtlsdr_windows",
]
