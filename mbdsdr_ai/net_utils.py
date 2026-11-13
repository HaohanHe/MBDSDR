# SPDX-License-Identifier: MIT
"""网络地址解析的唯一实现。

``host[:port]`` 这类文本在整个应用里有三个入口：桌面连接对话框、
``MBDSDR_RTL_TCP``、``MBDSDR_SPYSERVER``。它们必须对同一串地址给出
同一个结果——否则界面能连的写法，用环境变量配就连不上（IPv6 方括号
就是实例）。所以解析只保留这一份，任何新入口都应调用它。
"""
from __future__ import annotations

from typing import Optional, Tuple


def parse_host_port(text: str, default_port: int = 1234
                    ) -> Optional[Tuple[str, int]]:
    """解析 ``host[:port]`` / ``[IPv6]:port``，失败返回 ``None``。

    - ``[::1]:1234`` → ``("::1", 1234)``；``[::1]`` → ``("::1", default_port)``
    - ``192.168.0.5:1234`` / ``192.168.0.5``
    - 裸 IPv6（``fe80::1``，无方括号且冒号多于一个）→ ``None``：
      它与 ``host:port`` 无法区分，必须显式写成 ``[fe80::1]:port``
    - 端口必须是 1–65535 的 ASCII 十进制数字
    """
    s = (text or "").strip()
    if not s:
        return None
    if s.startswith("["):
        end = s.find("]")
        if end < 0:
            return None
        host = s[1:end]
        rest = s[end + 1:].strip()
        if rest:
            if not rest.startswith(":"):
                return None
            port_s = rest[1:].strip()
            if not (port_s.isascii() and port_s.isdigit()):
                return None
            port = int(port_s)
        else:
            port = int(default_port)
    else:
        if s.count(":") > 1:
            # 裸 IPv6：冒号既是分组也是端口分隔符，猜不出，拒绝。
            return None
        if ":" in s:
            host, _, port_s = s.rpartition(":")
            port_s = port_s.strip()
            if not (port_s.isascii() and port_s.isdigit()):
                return None
            port = int(port_s)
        else:
            host, port = s, int(default_port)
    if not host or not (1 <= port <= 65535):
        return None
    return host, port
