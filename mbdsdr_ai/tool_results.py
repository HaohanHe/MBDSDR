# SPDX-License-Identifier: MIT
"""工具结果的叶子定义：``ToolResult`` / ``clean_exc`` / ``numeric_array``。

本模块不 import 任何 ``mbdsdr_ai`` 模块——它是 ``tool_registry`` 与 60 模块
SCC 内大量原子工具的共同下游。那些工具原先反向 import ``tool_registry``
取这三个名字，再用函数体内 import 把边藏起来，形成大环。

同名对象必须唯一：``tool_registry`` 用一行 ``from .tool_results import
ToolResult, clean_exc, numeric_array`` 原处再导出，不做包装/子类/转发；
否则跨模块 ``isinstance(x, ToolResult)`` 会静默失效、异常被吞。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict


def numeric_array(args: Dict[str, Any], key: str, *, allow_empty: bool = False):
    """按「数字数组」取参校验，返回 ``(values, err)``。

    弱模型或批量脚本误把标量/字符串喂给 array 型参数时（如 samples=10、
    taps="TEST"），此前会以 ``TypeError: 'int' object is not iterable`` 或
    转换异常逃逸出 handler，被 registry 兜底成「调用失败: TypeError: ...」。
    此处按仓内「错误:即失败」契约，把类型不符转成可操作的参数错误。
    """
    raw = args.get(key, [])
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        return None, (f"参数 {key} 需为数字数组（如 [0.5, -0.5, ...]），"
                      f"收到 {type(raw).__name__}")
    if not raw and not allow_empty:
        return None, f"参数 {key} 不能为空数组，请提供至少一个采样/系数"
    try:
        return [float(v) for v in raw], None
    except (TypeError, ValueError):
        return None, f"参数 {key} 需为数字数组，其中含无法转换为数字的项"


# ─────────────────────────────────────────────────────────────
# 异常消息整形：诚实失败也要说人话（中文、可操作）
# ─────────────────────────────────────────────────────────────
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 未收录英文消息时的中文异常类型名
_EXC_TYPE_ZH = {
    "ValueError": "参数值错误",
    "IndexError": "索引越界",
    "KeyError": "缺少必需参数",
    "TypeError": "类型错误",
    "ZeroDivisionError": "除零错误",
    "FileNotFoundError": "文件不存在",
    "NotADirectoryError": "路径不是目录",
    "IsADirectoryError": "路径是目录",
    "PermissionError": "没有权限",
    "TimeoutError": "超时",
    "ConnectionRefusedError": "连接被拒绝",
    "ConnectionResetError": "连接被重置",
    "MemoryError": "内存不足",
    "OverflowError": "数值溢出",
    "ArithmeticError": "算术错误",
    "RuntimeError": "运行时错误",
    "NotImplementedError": "功能未实现",
    "UnicodeDecodeError": "编码无法解析",
}

# numpy / 内置库常见英文异常消息 → 中文说明（{0}/{1} 为正则捕获组）
_EN_MSG_MAP = [
    (re.compile(r"not enough values to unpack \(expected (\d+), got (\d+)\)"),
     "输入数组元素不足：期望 {0} 个、实到 {1} 个（如坐标为 [x, y, z]），请逐项检查参数是否给全"),
    (re.compile(r"matmul: Input operand \d+ has a mismatch in its core dimension \d+"),
     "矩阵/向量乘法维度不匹配，请检查两侧数组形状是否对应（向量长度应等于矩阵行/列数）"),
    (re.compile(r"index -?\d+ is out of bounds for axis \d+ with size 0"),
     "输入数据为空（长度 0），索引指向了不存在的元素，没有可处理的采样/符号"),
    (re.compile(r"zero-size array to reduction operation (\w+) which has no identity"),
     "输入数据为空（长度 0），无法计算统计量，请提供至少一个采样"),
    (re.compile(r"Invalid number of FFT data points \((\d+)\) specified\.?"),
     "FFT 输入点数为 {0}（输入数据为空），请提供非空 IQ 采样"),
    (re.compile(r"integer division or modulo by zero"),
     "计算中出现除零：通常是输入数据为空（长度 0）或块长/步长参数为 0，请检查后重试"),
    (re.compile(r"operands could not be broadcast together with shapes ([^\n]+)"),
     "数组形状不一致、无法广播运算（{0}），请检查各输入数组的长度"),
    (re.compile(r"could not convert string to float: '([^']*)'"),
     "无法把 “{0}” 解析为数字，请检查该参数是否为纯数字文本"),
    (re.compile(r"setting an array element with a sequence"),
     "数组元素类型不一致（疑似把嵌套列表当作标量传入），请检查输入数据的结构"),
    (re.compile(r"\[Errno 111\] Connection refused"),
     "连接被拒绝：对端没有程序在监听该地址端口（检查设备 IP/端口与服务是否已启动）"),
    (re.compile(r"\[Errno 113\] No route to host"),
     "网络不可达：找不到到该主机的路由（检查网络连接与设备 IP）"),
    (re.compile(r"\[Errno 110\] Connection timed out"),
     "连接超时：对端无响应（检查设备是否在线、防火墙是否放行）"),
    # Windows 网络错误不走 Errno 前缀，str(e) 是 [WinError NNNN] 形式，
    # URLError 包裹后成 "<urlopen error [WinError 10061] ...>"。不映射就会
    # 泄漏英文包裹串或被打成「计算错误」——补 WinError 10061/10060/10065/10054
    # 与上面三条 Linux errno 语义对齐（2026-11-02，settings-audit 七-5 销账）。
    (re.compile(r"\[WinError 10061\]"),
     "连接被拒绝：对端没有程序在监听该地址端口（检查设备 IP/端口与服务是否已启动）"),
    (re.compile(r"\[WinError 10060\]"),
     "连接超时：对端无响应（检查设备是否在线、防火墙是否放行）"),
    (re.compile(r"\[WinError 10065\]"),
     "网络不可达：找不到到该主机的路由（检查网络连接与设备 IP）"),
    (re.compile(r"\[WinError 10054\]"),
     "连接被对端重置或断开（检查设备状态与网络质量）"),
    # socket.timeout / TimeoutError 的 str(e) 就是裸 "timed out"（无 Errno 前缀），
    # 云台/rotctld/设备面超时分支都会走到这里，不映射就会泄漏英文。
    (re.compile(r"^timed out$"),
     "连接超时：对端无响应（检查设备是否在线、网络/防火墙是否放行）"),
    (re.compile(r"^The read operation timed out$"),
     "读取超时：对端发送数据太慢或中途断开（检查设备状态与网络质量）"),
    (re.compile(r"cannot reshape array of size 0 into shape"),
     "输入数据为空（长度 0），无法整形为期望形状，请提供非空数据"),
    (re.compile(r"\[Errno 2\] No such file or directory: '([^']+)'"),
     "文件不存在: {0}（请检查路径是否正确、文件是否已生成）"),
    (re.compile(r"\[Errno 13\] Permission denied: '([^']+)'"),
     "没有权限访问: {0}（请检查文件权限或以合适身份运行）"),
]


def clean_exc(e: BaseException) -> str:
    """把异常整形成中文可操作的诚实失败消息。

    背景：多个原子工具层的失败处理沿用「{前缀}: {type(e).__name__}: {e}」，
    消息本体已中文时白带「ValueError: 」前缀，numpy 英文消息则整条泄漏
    （"zero-size array to reduction..."、"Invalid number of FFT data
    points (0) specified." 等）。模型读不懂英文堆栈，诚实失败就失去意义。

    整形规则（按序命中）：
    1. ``KeyError`` → 点名缺少的参数名；
    2. 已知英文消息（numpy/内置库常见）→ 正则映射为中文说明；
    3. 消息本体已含中文 → 原样返回，不叠加异常类型前缀；
    4. 未收录的英文消息 → 「{中文类型名}: {原文}」，保留细节便于人工排查。
    """
    if isinstance(e, KeyError):
        key = e.args[0] if e.args else "?"
        return f"缺少必需参数: {key}（请对照工具参数说明补全）"
    msg = str(e).strip()
    for pat, tmpl in _EN_MSG_MAP:
        m = pat.search(msg)
        if m:
            try:
                return tmpl.format(*m.groups())
            except (IndexError, KeyError):
                return tmpl
    if _CJK_RE.search(msg):
        return msg
    zh_type = _EXC_TYPE_ZH.get(type(e).__name__, "计算错误")
    hint = ("（请检查路径、权限与网络是否就绪）" if isinstance(e, OSError)
            else "（请检查输入参数的格式与长度）")
    if msg:
        return f"{zh_type}: {msg}{hint}"
    return f"{zh_type}{hint}"


@dataclass
class ToolResult:
    """工具调用结果。"""
    success: bool
    content: str
    tool_name: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    error: str = ""
    truncated: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    # DSH（deepseek-harness）nextStep 模式：失败/部分成功时给模型的下一步
    # 恢复指引（"该做什么或告诉用户什么"）。非空时由 agent 追加进模型可见
    # 消息，让模型不必从错误码猜恢复路径。
    next_step: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "tool_name": self.tool_name,
            "args": self.args,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "truncated": self.truncated,
            "data": self.data,
            "next_step": self.next_step,
        }


__all__ = [
    "ToolResult",
    "clean_exc",
    "numeric_array",
]
