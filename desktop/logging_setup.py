# SPDX-License-Identifier: MIT
"""桌面端日志初始化（显式调用，导入无副作用）。

唯一调用点：``desktop/main.py:main()`` 在 ``QApplication`` 创建后、主窗口构造前，
按设置项 ``log_level`` 调用一次。这样「改设置 → 重启」即可生效。

红线：

* 导入本模块不创建 handler、不打开文件、不读环境变量；
* 级别只认 ``INFO`` / ``DEBUG``（与 ``config_schema.py`` 的 choices 一致），
  未知值回退 ``INFO``；
* 重复调用只更新级别，不重复叠加 handler。
"""

from __future__ import annotations

import logging

#: 与 config_schema 的 log_level choices 保持一一对应。
LEVELS = {"INFO": logging.INFO, "DEBUG": logging.DEBUG}

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_configured = False


def configure_logging(level_name: str = "INFO") -> int:
    """按设置值初始化根日志，返回生效的整数级别。

    ``INFO`` 只输出常规信息；``DEBUG`` 额外输出调试细节。未知/空值回退 INFO。
    日志写 stderr——打包版由 ``main.py:_ensure_stdio_for_pythonw()`` 把 stderr
    接到启动日志文件；本函数自身不创建文件。
    """
    global _configured
    level = LEVELS.get(str(level_name or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    if not _configured:
        logging.basicConfig(level=level, format=_FORMAT)
        _configured = True
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)
    # 这条只在 DEBUG 时可见，是「级别真的切过去了」的自证。
    logging.getLogger(__name__).debug(
        "日志已初始化：level=%s", logging.getLevelName(level))
    return level


def configure_from_settings(settings) -> int:
    """从设置对象取 ``log_level`` 并初始化日志（main.py 的启动路径）。"""
    return configure_logging(settings.get("log_level", "INFO"))
