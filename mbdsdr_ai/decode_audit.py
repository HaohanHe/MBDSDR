# SPDX-License-Identifier: MIT
"""结构化解码审计日志（JSONL，P5 之后的无阻塞工作）。

目的：解码到底发生过什么，要能离开「人肉看日志」——每次解码写入一行
JSON：谁调用、什么模式、成功/失败、诚实错误码、真实度、解出几条、
输入文件指纹。审计只记**元数据**，不抄报文正文（真实报文可能含隐私）。

开关：设置环境变量 `MBDSDR_DECODE_AUDIT_DIR` 指向目录才启用；
未设置时零开销、零文件写入（CI 与日常使用默认关闭）。

红线：审计写入失败绝不影响解码结果——只把错误打到 stderr，
不吞掉解码返回。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from typing import Any, Dict, Optional

AUDIT_ENV = "MBDSDR_DECODE_AUDIT_DIR"
AUDIT_FILENAME = "decode_audit.jsonl"

# 单条字段截断，防止一次性把巨大错误/说明写进审计
_MAX_TEXT = 300
_FINGERPRINT_BYTES = 1 << 20      # 大文件只对前 1MB 取指纹


def _fingerprint(path: str) -> Dict[str, Any]:
    if not path or not os.path.exists(path) or not os.path.isfile(path):
        return {"input_bytes": 0, "input_sha256_first1mb": ""}
    size = os.path.getsize(path)
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(_FINGERPRINT_BYTES))
    except OSError:
        return {"input_bytes": size, "input_sha256_first1mb": ""}
    return {"input_bytes": size, "input_sha256_first1mb": h.hexdigest()}


def _clip(value: Any) -> str:
    text = "" if value is None else str(value)
    return text[:_MAX_TEXT]


def audit_record(tool_name: str, result: Dict[str, Any],
                 input_path: str = "") -> Dict[str, Any]:
    """纯函数：解码结果 → 一行审计记录（不含报文正文）。"""
    res = dict(result or {})
    fp = _fingerprint(input_path or res.get("input_file") or "")
    return {
        "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tool": tool_name,
        "mode": str(res.get("mode") or ""),
        "ok": bool(res.get("ok", False)),
        "status": str(res.get("status") or ""),
        "error_code": str(res.get("error_code") or ""),
        "evidence_level": str(res.get("evidence_level") or ""),
        "output_kind": str(res.get("output_kind") or ""),
        "num_messages": int(res.get("num_messages") or 0),
        # 报文正文不入审计（可能含真实呼号/电话/位置）；只记条数
        "messages_recorded": False,
        "error": _clip(res.get("error")),
        "input_file": input_path or str(res.get("input_file") or ""),
        "input_bytes": fp["input_bytes"],
        "input_sha256_first1mb": fp["input_sha256_first1mb"],
    }


def append_audit(path: str, record: Dict[str, Any]) -> str:
    """追加一行 JSONL（父目录自动创建）。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def maybe_audit(tool_name: str, result: Dict[str, Any],
                input_path: str = "") -> Optional[str]:
    """按环境变量开关写审计；未启用返回 None，任何故障只报 stderr。"""
    audit_dir = os.environ.get(AUDIT_ENV, "").strip()
    if not audit_dir:
        return None
    try:
        path = os.path.join(audit_dir, AUDIT_FILENAME)
        return append_audit(path, audit_record(tool_name, result, input_path))
    except Exception as exc:                    # 红线：不影响解码结果
        print(f"[decode-audit] 审计写入失败（已忽略）: {exc}", file=sys.stderr)
        return None
