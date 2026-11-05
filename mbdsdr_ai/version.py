# SPDX-License-Identifier: MIT
"""版本号单一事实源。

``mbdsdr_ai/VERSION`` 是唯一手抄点：CMake（``cpp/CMakeLists.txt``）、
pyproject（``[tool.setuptools.dynamic] version = {file = "mbdsdr_ai/VERSION"}``）、
桌面端 ``QApplication.setApplicationVersion`` 与 PyInstaller 的 EXE 版本资源
都从这里派生，禁止在别处再写死版本字面量。
"""

from pathlib import Path

_VERSION_FILE = Path(__file__).with_name("VERSION")


def _read_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        # 安装成 wheel 时 VERSION 由 package-data 随包；若仍未带上，
        # 退回发行元数据而不是在代码里留第二份版本字面量。
        try:
            from importlib.metadata import version

            return version("mbdsdr-ai")
        except Exception as exc:  # pragma: no cover - 仅安装损坏时触发
            raise RuntimeError(
                f"无法读取版本号：{_VERSION_FILE} 不存在且 mbdsdr-ai 元数据不可用"
            ) from exc


__version__ = _read_version()

__all__ = ["__version__"]
