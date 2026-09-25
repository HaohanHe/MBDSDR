"""
mbdsdr_ai/pat_adapter.py
=========================
pat (la5nta/pat) Winlink 电子邮件电台协议的 Python 学习移植。

本模块移植 pat 所使用的 Winlink B2F（FBB-Forward / WL2K）会话层消息格式与
传输模式枚举。关键结构与常量在注释中标注「来源: pat <file>:<line>」。

移植内容：
  - TransportMethod 枚举 —— 移植 app/app.go:41-47 的传输 scheme 常量
  - B2FMessage          —— 移植 wl2k-go/fbb 消息信封
                            （[from|to|subject|YYYYMMDDhhmmss] 头 + body）
  - MID 消息列表行       —— 移植 app/exchange.go:121,141 的 "MID nnnnnn" 列表
  - 附件/MIME 编码       —— 移植 app/attachment.go 的 BASE64 附件段

参考：pat 上游 MIT/GPL，LA5NTA；wl2k-go/fbb 协议。本移植仅作学习用途。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# =====================================================================
# 传输模式枚举 —— 移植 app/app.go:41-47
# =====================================================================
# pat 中各无线电路由的 URL scheme：
#   MethodArdop  = "ardop"   (app/app.go:41)
#   MethodTelnet = "telnet"  (app/app.go:42)
#   MethodPactor = "pactor"  (app/app.go:43)
#   MethodVaraHF = "varahf"  (app/app.go:44)
#   MethodVaraFM = "varafm"  (app/app.go:45)
#   MethodAX25   = "ax25"    (app/app.go:47)
#
# 这些 scheme 在 app/connect.go:81-100 被分派到对应 TNC。
TRANSPORT_METHODS: Dict[str, Dict[str, Any]] = {
    "ardop": {
        "label": "ARDOP (数字电台 OFDM)",
        "medium": "hf",
        "speeds": ["500", "2000"],   # ARDOP ARQ 带宽, cfg/config.go:211
    },
    "pactor": {
        "label": "Pactor/Pactor-II (SCS)",
        "medium": "hf",
        "speeds": ["100", "200", "400"],
    },
    "varahf": {
        "label": "VARA HF",
        "medium": "hf",
        "speeds": [],
    },
    "varafm": {
        "label": "VARA FM (VHF)",
        "medium": "vhf",
        "speeds": ["4800", "9600"],
    },
    "ax25": {
        "label": "AX.25 分组包 (VHF)",
        "medium": "vhf",
        "speeds": ["1200", "9600"],
    },
    "telnet": {
        "label": "Telnet (Internet RMS 网关)",
        "medium": "internet",
        "speeds": [],
    },
}


def list_transport_methods() -> List[Dict[str, Any]]:
    """返回 pat 支持的全部传输模式（来源: app/app.go:41-47）。"""
    out = []
    for scheme, meta in TRANSPORT_METHODS.items():
        out.append({"scheme": scheme, **meta})
    return out


# =====================================================================
# B2F (FBB) 消息信封
# =====================================================================
# Winlink B2F 转发协议的消息头是一行 FBB 信封：
#
#   [<FROMCALL>|<TOCALL>|<SUBJECT>|<YYYYMMDDhhmmss>]
#
# 来源：wl2k-go/fbb Message.String() / pat app/exchange.go:336 字段映射
#       (MID, Subject, To, Via 等)。
# 信封后可跟若干 :FBB: 路径行，最后是正文（RFC822 风格）。
_B2F_ENVELOPE_RE = re.compile(
    r"^\[([^|\]]*)\|([^|\]]*)\|([^|\]]*)\|(\d{14})\]$"
)


@dataclass
class B2FAttachment:
    """一个 B2F 附件（BASE64 段）。

    来源：pat app/attachment.go —— Winlink 附件在消息体中以
    `Begin-base64 <filename>` / `End-base64` 包裹（wl2k-go/fbb 约定）。
    """

    filename: str
    data: bytes

    def encode(self) -> bytes:
        b64 = base64.b64encode(self.data).decode("ascii")
        out = f"Begin-base64 {self.filename}\n".encode("ascii")
        out += b64.encode("ascii") + b"\n"
        out += b"End-base64\n"
        return out

    @staticmethod
    def decode(block: bytes) -> "B2FAttachment":
        lines = block.split(b"\n")
        head = lines[0].decode("ascii", "replace")
        filename = head[len("Begin-base64 "):].strip()
        body = b"\n".join(lines[1:])
        body = body.replace(b"End-base64", b"").strip()
        data = base64.b64decode(body)
        return B2FAttachment(filename=filename, data=data)


@dataclass
class B2FMessage:
    """一条 Winlink B2F 邮件消息。

    字段对应 wl2k-go/fbb.Message：
      from     —— 发件人呼号/地址 (app/exchange.go:336 附近 From)
      to       —— 收件人呼号/地址
      subject  —— 主题
      timestamp—— 发送时间 (UTC)
      body     —— 正文文本
      attachments —— 附件列表
    """

    fromcall: str
    tocall: str
    subject: str
    timestamp: datetime
    body: str = ""
    attachments: List[B2FAttachment] = field(default_factory=list)
    mid: int = 0  # Message ID，RMS 分配（exchange.go:121 p.MID()）

    # -----------------------------------------------------------------
    # 编码：结构 → B2F 字节流
    # -----------------------------------------------------------------
    def encode(self) -> bytes:
        ts = self.timestamp.strftime("%Y%m%d%H%M%S")
        hdr = f"[{self.fromcall}|{self.tocall}|{self.subject}|{ts}]\r\n"
        out = hdr.encode("ascii", "replace")
        # :FBB: 路径行（学习用，单跳）
        out += f":FBB:Winlink B2F via mbdsdr\r\n".encode("ascii")
        out += b"\r\n"
        out += self.body.encode("utf-8", "replace")
        if not self.body.endswith("\n"):
            out += b"\r\n"
        for att in self.attachments:
            out += b"\r\n"
            out += att.encode()
        return out

    # -----------------------------------------------------------------
    # 解码：B2F 字节流 → 结构
    # -----------------------------------------------------------------
    @staticmethod
    def decode(data: bytes) -> "B2FMessage":
        text = data.decode("utf-8", "replace")
        lines = text.split("\r\n") if "\r\n" in text else text.split("\n")

        envelope: Optional[Tuple[str, str, str, str]] = None
        i = 0
        for i, line in enumerate(lines):
            m = _B2F_ENVELOPE_RE.match(line.strip())
            if m:
                envelope = (m.group(1), m.group(2), m.group(3), m.group(4))
                break
        if envelope is None:
            raise ValueError("B2F 信封行未找到：需要 [from|to|subject|ts]")

        fromcall, tocall, subject, ts = envelope
        timestamp = datetime.strptime(ts, "%Y%m%d%H%M%S")

        # 收集 :FBB: 路径行之后的正文（跳过空行）
        body_lines: List[str] = []
        attachments: List[B2FAttachment] = []
        j = i + 1
        # 跳过 :FBB: 路径行与首个空行
        while j < len(lines) and (lines[j].startswith(":FBB:") or lines[j].strip() == ""):
            j += 1
        # 解析附件段
        while j < len(lines):
            if lines[j].startswith("Begin-base64 "):
                block = [lines[j]]
                j += 1
                while j < len(lines) and lines[j].strip() != "End-base64":
                    block.append(lines[j])
                    j += 1
                if j < len(lines):
                    block.append(lines[j])
                attachments.append(B2FAttachment.decode("\n".join(block).encode("ascii")))
                j += 1
                continue
            body_lines.append(lines[j])
            j += 1

        body = "\n".join(body_lines).strip()
        return B2FMessage(
            fromcall=fromcall, tocall=tocall, subject=subject,
            timestamp=timestamp, body=body, attachments=attachments,
        )


# =====================================================================
# MID 消息列表行 —— 移植 app/exchange.go:121,141
# =====================================================================
# pat 在信箱列表里把每条待收消息渲染成一行：
#   MID <n>  <size>  <flags>  <subject>
# 来源：exchange.go:121 PromptOption{Value: p.MID(), ...}
#       exchange.go:141 if p.MID() != val
# 真实 B2F 列表行形如：
#   "M 12345678  1234 R Hello"
_MID_LINE_RE = re.compile(
    r"^M\s+(?P<mid>\d+)\s+(?P<size>\d+)\s+(?P<flags>[A-Z@]*)\s*(?P<subject>.*)$"
)


@dataclass
class B2FListEntry:
    mid: int
    size: int
    flags: str
    subject: str


def encode_mid_list(entries: List[B2FListEntry]) -> bytes:
    """把 MID 列表编码为 B2F 会话列表帧（RMS→客户端）。"""
    out = bytearray()
    for e in entries:
        out += f"M {e.mid} {e.size} {e.flags} {e.subject}\r\n".encode("ascii")
    out += b"\r\n"  # 列表结束空行
    return bytes(out)


def decode_mid_list(data: bytes) -> List[B2FListEntry]:
    """解析 B2F 会话列表帧，返回消息条目列表。"""
    text = data.decode("ascii", "replace")
    out: List[B2FListEntry] = []
    for line in text.replace("\r", "").split("\n"):
        line = line.strip()
        if not line or not line.startswith("M "):
            continue
        m = _MID_LINE_RE.match(line)
        if m:
            out.append(B2FListEntry(
                mid=int(m.group("mid")),
                size=int(m.group("size")),
                flags=m.group("flags"),
                subject=m.group("subject").strip(),
            ))
    return out


# =====================================================================
# 工具注册
# =====================================================================
def register_pat_tools(registry) -> None:
    """注册 pat / Winlink B2F 相关工具到 ToolRegistry。"""
    from mbdsdr_ai.tool_registry import ToolResult

    def _list_methods(args: Dict[str, Any]) -> ToolResult:
        try:
            methods = list_transport_methods()
            return ToolResult(
                success=True,
                content=json.dumps(methods, ensure_ascii=False, indent=2),
                data={"methods": methods},
            )
        except Exception as e:  # pragma: no cover
            return ToolResult(False, f"list_transport_methods 失败: {e}")

    def _encode_message(args: Dict[str, Any]) -> ToolResult:
        try:
            atts = []
            for a in args.get("attachments", []):
                raw = base64.b64decode(a.get("data_b64", ""))
                atts.append(B2FAttachment(filename=a["filename"], data=raw))
            ts = datetime.strptime(args.get("timestamp", "20240101120000"), "%Y%m%d%H%M%S")
            msg = B2FMessage(
                fromcall=args["fromcall"],
                tocall=args["tocall"],
                subject=args.get("subject", ""),
                timestamp=ts,
                body=args.get("body", ""),
                attachments=atts,
                mid=int(args.get("mid", 0)),
            )
            blob = msg.encode()
            return ToolResult(
                success=True,
                content=f"B2F 编码: {msg.fromcall}->{msg.tocall} "
                        f"({len(blob)} 字节, {len(atts)} 附件)",
                data={"bytes_b64": binascii.b2a_base64(blob).decode("ascii").strip(),
                      "n_bytes": len(blob)},
            )
        except Exception as e:
            return ToolResult(False, f"B2F 编码失败: {e}")

    def _decode_message(args: Dict[str, Any]) -> ToolResult:
        try:
            blob = base64.b64decode(args["bytes_b64"])
            msg = B2FMessage.decode(blob)
            return ToolResult(
                success=True,
                content=f"B2F 解码: {msg.fromcall}->{msg.tocall} "
                        f"subject='{msg.subject}' body='{msg.body[:40]}'",
                data={
                    "fromcall": msg.fromcall, "tocall": msg.tocall,
                    "subject": msg.subject,
                    "timestamp": msg.timestamp.strftime("%Y-%m-%d %H:%M UTC"),
                    "body": msg.body,
                    "n_attachments": len(msg.attachments),
                },
            )
        except Exception as e:
            return ToolResult(False, f"B2F 解码失败: {e}")

    def _mid_list(args: Dict[str, Any]) -> ToolResult:
        try:
            entries = [B2FListEntry(**e) for e in args.get("entries", [])]
            blob = encode_mid_list(entries)
            back = decode_mid_list(blob)
            return ToolResult(
                success=True,
                content=f"MID 列表: {len(back)} 条消息, {len(blob)} 字节",
                data={"entries": [vars(b) for b in back]},
            )
        except Exception as e:
            return ToolResult(False, f"MID 列表失败: {e}")

    registry.register(
        name="pat_list_transports",
        description="列出 pat Winlink 支持的传输模式 (ARDOP/Pactor/VARA-HF/VARA-FM/AX.25/Telnet)，"
                    "来源: pat app/app.go:41-47。",
        parameters={"type": "object", "properties": {}},
        handler=_list_methods,
        category="ham_modes",
    )
    registry.register(
        name="pat_b2f_encode",
        description="把一条 Winlink B2F 邮件 (from/to/subject/timestamp/body/附件) 编码为 FBB 信封字节流。",
        parameters={"type": "object", "properties": {
            "fromcall": {"type": "string"}, "tocall": {"type": "string"},
            "subject": {"type": "string"}, "body": {"type": "string"},
            "timestamp": {"type": "string", "description": "YYYYMMDDhhmmss"},
            "mid": {"type": "integer"},
            "attachments": {"type": "array", "items": {"type": "object"}},
        }, "required": ["fromcall", "tocall"]},
        handler=_encode_message,
        category="ham_modes",
    )
    registry.register(
        name="pat_b2f_decode",
        description="把一段 Winlink B2F FBB 信封字节流解码为结构化邮件字段。",
        parameters={"type": "object", "properties": {
            "bytes_b64": {"type": "string", "description": "B2F 字节流的 BASE64"},
        }, "required": ["bytes_b64"]},
        handler=_decode_message,
        category="ham_modes",
    )
    registry.register(
        name="pat_mid_list",
        description="编码/解码 Winlink B2F 信箱消息列表 (MID <n> <size> <flags> <subject>)，"
                    "来源: pat app/exchange.go:121,141。",
        parameters={"type": "object", "properties": {
            "entries": {"type": "array", "items": {"type": "object"}},
        }},
        handler=_mid_list,
        category="ham_modes",
    )
