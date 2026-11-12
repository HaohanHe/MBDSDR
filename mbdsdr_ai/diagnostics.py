# SPDX-License-Identifier: MIT
"""只读诊断数据的核心口径：脱敏 + 实例错误注册表（路线图 W106）。

本模块属**纯逻辑层**：只依赖标准库，不 import Qt、不 import ``desktop``
（方向守卫见 ``tests/test_import_direction_contract.py``）。桌面侧取数
（损坏备份清点、启动日志落点、桌面模块 ``last_error``）在
``desktop/diagnostics_data.py``——那些是界面层事实，不该由核心层反向依赖。

两块口径：

1. **脱敏**：``redact_secrets`` / ``redact_tree`` 是数据层的统一脱敏入口。
   理由文件是自由文本通道（写的是异常消息与路径），路径本身可能被用户 /
   环境变量带进 key 形状的串，日志缓冲与 ``last_error`` 更是什么都可能出现。
   用户会把诊断结构截图发出去求助——面板里遮只保护一个出口，数据层遮保护
   所有出口（面板 / 日志 / 导出）。
2. **实例注册表**：``Agent.__init__`` 登记的内存 / 调度 / 守护 / 工作流等长
   生命周期对象，诊断取数时经弱引用读到最近一次持久化错误；取不到的实例
   由调用方明确标 ``unwired``，不拿空值冒充「无错误」。
"""

import re
import weakref
from typing import Any, Dict, List, Optional

#: 由实例弱引用注册的对象（``Agent.__init__`` 里登记）。
_REGISTERED: Dict[str, List["weakref.ref"]] = {}

#: 常见密钥形状。只覆盖**可识别**的三种：``sk-`` 前缀、``Bearer <token>``、
#: 带标签的赋值（``api_key=`` / ``token:`` / ``password=``…）。没有标签的
#: 高熵随机串识别不了——这是已知残余风险，不能声称「绝不泄露」。
_SK_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
_BEARER_RE = re.compile(r"(?i)\bbearer\b(\s+)[A-Za-z0-9._\-+/=]{6,}")
_SECRET_KV_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?key|secret|token|password)\b"
    r"(\s*[:=]\s*)([\"']?)([^\s\"',;}\]]+)")


def redact_secrets(text: str) -> str:
    """把疑似密钥替换成 ``***``（纯函数，便于单独测试）。

    顺序：先切 ``Bearer`` 头（token 可能不是 ``sk-`` 形状），再切带标签的
    赋值，最后切裸 ``sk-`` 串。宁可多遮一点，也不让一段能直接拿去调用
    接口的串留在截图里。

    但这不等于整段打码：**只替换命中片段本身**，前后路径、异常类型、步骤
    文案原样保留。诊断页的价值是「哪个文件坏了、为什么」，把
    ``/home/sk-workbench/config.json`` 整条遮掉等于为了安全把诊断弄瞎。
    """
    if not text:
        return text
    text = _BEARER_RE.sub(lambda m: f"Bearer{m.group(1)}***", text)
    text = _SECRET_KV_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}***", text)
    text = _SK_RE.sub("sk-***", text)
    return text


def redact_tree(value: Any) -> Any:
    """递归脱敏结构里的所有字符串；dict 键名是代码里的固定字段，不处理。"""
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: redact_tree(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_tree(item) for item in value]
    return value


def register_store(name: str, store: Any) -> None:
    """登记一个带 ``last_error`` 的长生命周期对象（弱引用，不接管生命周期）。

    取的永远是最近登记的存活实例；同一进程里旧实例已被回收时不报错。
    """
    try:
        ref = weakref.ref(store)
    except TypeError:      # 少数不可弱引用的替身对象（测试用）直接跳过
        return
    _REGISTERED.setdefault(name, []).append(ref)


def last_registered(name: str) -> Optional[Any]:
    """取该名字最近登记且仍存活的实例（没有则 ``None``）。"""
    refs = [r for r in _REGISTERED.get(name, ()) if r() is not None]
    _REGISTERED[name] = refs
    return refs[-1]() if refs else None


__all__ = [
    "register_store",
    "last_registered",
    "redact_secrets",
    "redact_tree",
]
