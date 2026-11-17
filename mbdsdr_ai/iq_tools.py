# SPDX-License-Identifier: MIT
"""IQ 样本清单的只读工具面（Hugging Face 方向的可达性）。

``iq_sample_manifest`` 早就有了契约校验、结构体检与按 ``iq_path`` 分组的
切分方案，但此前只有 Python 调用入口——模型答不了「我这批样本能不能开训」，
用户也得自己去跑脚本。本模块把这两步接成 AI 工具：

* ``iq_manifest_audit``：校验 JSONL 清单 → 结构体检（不是「可以开训」）；
* ``iq_split_plan``：在同一份清单上给出可复现的训练/验证名单。

两条边界照旧写进返回里，不靠文档：
1. 只读清单文本，**不读样本内容、不写文件、不训练**；
2. ``structural_ready`` 只表示没有已知硬伤，不代表样本足够或质量合格。
"""

from __future__ import annotations

import os
from typing import Any, Dict

from . import iq_sample_manifest as _man

__all__ = ["MAX_MANIFEST_BYTES", "register_iq_tools"]

#: 清单是文本 JSONL；超过这个大小就拒绝整读，避免把大文件塞爆上下文。
MAX_MANIFEST_BYTES = 8 * 1024 * 1024


def _manifest_path(args: Dict[str, Any]) -> str:
    path = str(args.get("path") or "").strip()
    if not path:
        raise ValueError("必须给清单路径 path（JSONL 文件）")
    return path


def _oversize(path: str) -> bool:
    try:
        return os.path.getsize(path) > MAX_MANIFEST_BYTES
    except OSError:
        return False


def _load(path: str, check_files: bool) -> Dict[str, Any]:
    """校验清单；文件不存在时把 validate 的 line 0 拒绝翻成明确的 ok=False。"""
    validation = _man.validate_manifest_file(path, check_files=check_files)
    rejected = validation.get("rejected") or []
    if rejected and rejected[0].get("line") == 0:
        return {"error": rejected[0]["reasons"][0], "validation": validation}
    return {"validation": validation}


def _audit(args: Dict[str, Any]) -> Dict[str, Any]:
    path = _manifest_path(args)
    if _oversize(path):
        return {"ok": False, "path": path,
                "reason": f"清单超过 {MAX_MANIFEST_BYTES // (1024 * 1024)} MiB，"
                          "拒绝整读"}
    check_files = bool(args.get("check_files", False))
    loaded = _load(path, check_files)
    if "error" in loaded:
        return {"ok": False, "path": path, "reason": loaded["error"]}

    validation = loaded["validation"]
    audit = _man.audit_manifest(validation["accepted"])
    text = "\n".join([
        _man.format_validation(validation),
        _man.format_audit(audit),
    ])
    return {
        "ok": True,
        "path": path,
        "check_files": check_files,
        "counts": validation["counts"],
        "rejected": validation["rejected"],
        "audit": audit,
        "text": text,
    }


def _format_split(plan: Dict[str, Any],
                  rejected: int = 0) -> str:
    if not plan.get("ok"):
        return "IQ 切分方案未生成：" + "；".join(plan.get("problems") or [])
    lines = []
    if rejected:
        lines.append(f"注意：有 {rejected} 行未通过清单校验，未进入切分"
                     "（逐行理由见 rejected）")
    lines.append(
        f"IQ 切分方案：训练 {len(plan.get('train') or [])} 条 / "
        f"验证 {len(plan.get('val') or [])} 条"
        f"（按 iq_path 分组，val_ratio={plan.get('val_ratio')}，"
        f"seed={plan.get('seed')}）")
    for label, n in sorted((plan.get("per_class") or {}).items()):
        lines.append(f"- {label}：{n['files']} 个文件 → "
                     f"训练 {n['train_files']} / 验证 {n['val_files']}")
    if plan.get("caveat"):
        lines.append("注：" + str(plan["caveat"]))
    return "\n".join(lines)


def _split(args: Dict[str, Any]) -> Dict[str, Any]:
    path = _manifest_path(args)
    if _oversize(path):
        return {"ok": False, "path": path,
                "reason": f"清单超过 {MAX_MANIFEST_BYTES // (1024 * 1024)} MiB，"
                          "拒绝整读"}
    loaded = _load(path, bool(args.get("check_files", False)))
    if "error" in loaded:
        return {"ok": False, "path": path, "reason": loaded["error"]}

    validation = loaded["validation"]
    try:
        plan = _man.plan_split(
            validation["accepted"],
            val_ratio=float(args.get("val_ratio", 0.2)),
            seed=int(args.get("seed", 0)),
        )
    except ValueError as exc:
        return {"ok": False, "path": path, "reason": str(exc)}

    out = dict(plan)
    out.update({
        "path": path,
        "counts": validation["counts"],
        "rejected": validation["rejected"],
        "text": _format_split(plan, validation["counts"].get("rejected", 0)),
    })
    return out


def register_iq_tools(agent) -> None:
    """把 IQ 清单体检/切分注册给 agent（只读，不触发确认门）。"""
    reg = agent.tool_registry

    reg.register(
        name="iq_manifest_audit",
        description=(
            "IQ 样本清单体检：读 JSONL 清单，校验必需字段与 license，"
            "再查单类别、同一 iq_path 重复登记/跨类别、sample_id 重复等"
            "会把实验做废的硬伤。用于回答『这批样本能不能开训』。只读，"
            "不读样本内容、不写文件、不训练；structural_ready 不代表样本"
            "足够或质量合格。check_files=true 时还会逐个检查 iq_path 指向"
            "的文件是否真的存在。"),
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "JSONL 清单文件路径"},
            "check_files": {"type": "boolean",
                            "description": "可选：是否检查 iq_path 文件存在（默认 false）"},
        }, "required": ["path"]},
        handler=_audit,
        category="ai",
    )

    reg.register(
        name="iq_split_plan",
        description=(
            "IQ 训练/验证切分方案：在同一份清单上，按 iq_path 分组给出"
            "确定性的训练/验证 sample_id 名单（同一文件不跨集合）。"
            "用于回答『怎么切才不会泄漏』。只产出名单，不读样本内容、"
            "不写文件、不训练；有任一问题就整体拒绝，不交付半套切分。"),
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "JSONL 清单文件路径"},
            "val_ratio": {"type": "number",
                          "description": "可选：验证集比例（0,1），默认 0.2"},
            "seed": {"type": "integer",
                     "description": "可选：随机种子，默认 0"},
            "check_files": {"type": "boolean",
                            "description": "可选：是否检查 iq_path 文件存在（默认 false）"},
        }, "required": ["path"]},
        handler=_split,
        category="ai",
    )
