# SPDX-License-Identifier: MIT
"""IQ 样本清单契约：先能证明「有真实样本」，再谈训练。

为什么先立契约
==============

调制分类器重训缺真实多类 IQ 样本（见
``docs/learn/2026-11-17-local-ai-inventory.md``）。在没有样本之前直接
写训练脚本，最常见的结局是拿合成信号刷一个漂亮的准确率，再对外说
「模型已训练」——这在本仓库属于伪造数据，是红线。

所以先把「一条样本算不算数」写成可执行的校验器：

* 必备字段：``sample_id`` / ``label`` / ``sample_rate`` / ``center_freq``
  / ``iq_path`` / ``license``；
* ``sample_id`` 全表唯一；
* 采样率与中心频率必须是有限正数（``true`` 这种布尔不算数字）；
* ``license`` 必须写明，空字符串或 ``unknown`` 一律拒绝——来源不清的
  样本不能进训练集；
* 坏行不打断整表：逐行拒绝并给行号与理由，合法行照常收下。

样本过契约后还有两步：``audit_manifest`` 查结构硬伤（不是「可以开训」），
``plan_split`` 给出以 ``iq_path`` 为最小单位的确定性训练/验证切分方案
（同一文件不跨界；切不出验证集就明说，不交付半套结果）。

校验器只读文件、不移动不删除，不做任何「自动修复」。
"""

from __future__ import annotations

import json
import math
import os
import random
from typing import Any, Dict, Iterable, List, Optional, Tuple

__all__ = [
    "REQUIRED_FIELDS",
    "validate_manifest_lines",
    "validate_manifest_file",
    "format_validation",
    "audit_manifest",
    "format_audit",
    "plan_split",
]

REQUIRED_FIELDS = (
    "sample_id",
    "label",
    "sample_rate",
    "center_freq",
    "iq_path",
    "license",
)

#: 这些 license 值等于没写。比较时忽略大小写与首尾空白。
_LICENSE_UNKNOWN = {"", "unknown", "n/a", "na", "none", "-", "?"}


def _is_positive_number(v: Any) -> bool:
    """bool 是 int 的子类，``True`` 不能当 1.0 收下。"""
    if isinstance(v, bool):
        return False
    if not isinstance(v, (int, float)):
        return False
    return math.isfinite(float(v)) and float(v) > 0.0


def _reject(line_no: int, reasons: List[str],
            sample_id: str = "") -> Dict[str, Any]:
    return {"line": line_no, "sample_id": sample_id, "reasons": reasons}


def validate_manifest_lines(
    lines: Iterable[str],
    check_files: bool = False,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """逐行校验 JSONL 清单，返回 ``{ok, accepted, rejected, counts}``。

    ``ok`` 只表示**整份清单全部合法**；有拒绝项时为 ``False``，但
    ``accepted`` 仍包含通过的行，便于用户先看「哪些能用」。
    """
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}

    for line_no, raw in enumerate(lines, 1):
        text = (raw or "").strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError as e:
            rejected.append(_reject(line_no, [f"不是合法 JSON（{e.msg}）"]))
            continue
        if not isinstance(item, dict):
            rejected.append(_reject(line_no, ["一行必须是一个 JSON 对象"]))
            continue

        reasons: List[str] = []
        sample_id = str(item.get("sample_id") or "").strip()

        for field in REQUIRED_FIELDS:
            if field not in item:
                reasons.append(f"缺少字段 {field}")
            elif field not in ("sample_rate", "center_freq", "license"):
                if not str(item.get(field) or "").strip():
                    reasons.append(f"{field} 为空")

        for field in ("sample_rate", "center_freq"):
            if field in item and not _is_positive_number(item[field]):
                reasons.append(f"{field} 必须是有限正数（收到 {item[field]!r}）")

        lic = str(item.get("license") or "").strip().lower()
        if lic in _LICENSE_UNKNOWN:
            reasons.append("license 未写明（来源不清的样本不能进训练集）")

        if sample_id:
            if sample_id in seen:
                reasons.append(
                    f"sample_id 与第 {seen[sample_id]} 行重复：{sample_id}")
            else:
                seen[sample_id] = line_no

        if check_files and not reasons:
            rel = str(item.get("iq_path") or "")
            path = rel if os.path.isabs(rel) else os.path.join(base_dir or "", rel)
            if not os.path.isfile(path):
                reasons.append(f"iq_path 指向的文件不存在：{path}")

        if reasons:
            rejected.append(_reject(line_no, reasons, sample_id))
        else:
            accepted.append(item)

    return {
        "ok": not rejected,
        "accepted": accepted,
        "rejected": rejected,
        "counts": {"accepted": len(accepted), "rejected": len(rejected)},
    }


def validate_manifest_file(
    path: str,
    check_files: bool = False,
) -> Dict[str, Any]:
    """读清单文件并校验；文件不存在时返回 ``ok=False`` 而不是抛异常。"""
    if not os.path.isfile(path):
        return {
            "ok": False,
            "accepted": [],
            "rejected": [{"line": 0, "sample_id": "",
                          "reasons": [f"清单文件不存在：{path}"]}],
            "counts": {"accepted": 0, "rejected": 1},
        }
    # utf-8-sig：Windows 的 PowerShell 5.1 / 记事本存 UTF-8 时会写 BOM，
    # 带 BOM 的文件用普通 utf-8 读会让第一行 json.loads 报 "Unexpected
    # UTF-8 BOM"，被误判成坏清单。BOM 不是数据问题，这里直接吃掉。
    with open(path, "r", encoding="utf-8-sig") as fh:
        return validate_manifest_lines(
            fh, check_files=check_files,
            base_dir=os.path.dirname(os.path.abspath(path)))


def format_validation(result: Dict[str, Any]) -> str:
    """多行报告：先给结论，再逐条列拒绝理由（带行号）。"""
    c = result.get("counts") or {}
    lines = [f"IQ 样本清单：通过 {c.get('accepted', 0)} 条，"
             f"拒绝 {c.get('rejected', 0)} 条"
             + ("（全部合法）" if result.get("ok") else "（有问题，先修清单）")]
    for r in result.get("rejected") or []:
        sid = f" {r.get('sample_id')}" if r.get("sample_id") else ""
        lines.append(f"- 第 {r.get('line')} 行{sid}：" + "；".join(r.get("reasons") or []))
    return "\n".join(lines)


def audit_manifest(accepted: List[Dict[str, Any]]) -> Dict[str, Any]:
    """已通过契约校验的样本 → 结构体检（**不是**「可以开训」的许可）。

    只查几种会直接把实验做废的硬伤，不判断样本质量，也不设「每类至少
    N 条」这种没有依据的数量门槛：

    * 空清单；
    * 少于 2 个类别（单类数据训不出分类器）；
    * 同一 ``iq_path`` 被登记多次（重复计数；跨类别时是标签泄漏）；
    * 同一 ``sample_id`` 重复（正常应已被校验器拒绝，这里再兜一次）。
    """
    classes = sorted({str(it.get("label") or "").strip() for it in accepted})
    class_counts: Dict[str, int] = {}
    rates = set()
    licenses = set()
    path_rows: Dict[str, List[Dict[str, Any]]] = {}
    id_counts: Dict[str, int] = {}

    for it in accepted:
        label = str(it.get("label") or "").strip()
        class_counts[label] = class_counts.get(label, 0) + 1
        try:
            rates.add(float(it.get("sample_rate")))
        except (TypeError, ValueError):
            pass
        lic = str(it.get("license") or "").strip()
        if lic:
            licenses.add(lic)
        path = str(it.get("iq_path") or "").strip()
        if path:
            path_rows.setdefault(path, []).append(it)
        sid = str(it.get("sample_id") or "").strip()
        if sid:
            id_counts[sid] = id_counts.get(sid, 0) + 1

    problems: List[str] = []
    if not accepted:
        problems.append("清单里没有样本")
    if len(classes) < 2 and accepted:
        problems.append(f"只有 {len(classes)} 个类别，单类数据训不出分类器")

    duplicate_paths = {p: len(v) for p, v in path_rows.items() if len(v) > 1}
    if duplicate_paths:
        problems.append(f"{len(duplicate_paths)} 个 iq_path 被重复登记"
                        "（会重复计数；跨类别时是标签泄漏）")
    conflict_paths = [
        p for p, rows in path_rows.items()
        if len({str(r.get("label") or "").strip() for r in rows}) > 1
    ]
    if conflict_paths:
        problems.append(f"{len(conflict_paths)} 个 iq_path 同时挂了多个类别")

    duplicate_ids = {sid: n for sid, n in id_counts.items() if n > 1}
    if duplicate_ids:
        problems.append(f"{len(duplicate_ids)} 个 sample_id 重复")

    return {
        "verdict": "not_ready" if problems else "structurally_ready",
        "samples": len(accepted),
        "classes": classes,
        "class_counts": class_counts,
        "sample_rates_hz": sorted(rates),
        "licenses": sorted(licenses),
        "duplicate_paths": duplicate_paths,
        "label_conflict_paths": sorted(conflict_paths),
        "duplicate_sample_ids": duplicate_ids,
        "problems": problems,
        "caveat": ("structural_ready 只表示没有上述硬伤，不代表样本足够，"
                   "也不代表质量合格；没有切分信息，训练/验证泄漏无法在"
                   "本检查里证明（切分请用 plan_split，按 iq_path 分组）。"),
    }


def format_audit(result: Dict[str, Any]) -> str:
    """结构体检的人话结论。"""
    if result.get("verdict") == "structurally_ready":
        head = (f"IQ 样本结构体检：{result.get('samples')} 条 / "
                f"{len(result.get('classes') or [])} 类，未见硬伤"
                "（不代表样本足够，也不代表能训出可用模型）")
    else:
        head = "IQ 样本结构体检：还不能开训"
    lines = [head]
    for cls, n in sorted((result.get("class_counts") or {}).items()):
        lines.append(f"- {cls}: {n} 条")
    for p in result.get("problems") or []:
        lines.append("- 问题：" + p)
    if result.get("caveat"):
        lines.append("注：" + str(result["caveat"]))
    return "\n".join(lines)


def plan_split(
    accepted: List[Dict[str, Any]],
    val_ratio: float = 0.2,
    seed: int = 0,
) -> Dict[str, Any]:
    """按 ``iq_path`` 分组给出确定性的训练/验证切分**方案**。

    训练集泄漏最常见的来源不是随机种子，而是**同一段录制被拆成多条样本**
    后随机分到两边。所以这里以文件为最小单位：一个 ``iq_path`` 的所有样本
    只能进同一个集合；同一文件挂了多个类别时直接拒绝（标签冲突）。

    只生成 ``sample_id`` 名单，不读样本内容、不写文件、不训练；只要有一项
    问题就把 ``train`` / ``val`` 清空，不交付「半套切分」。
    """
    ratio = float(val_ratio)
    if not 0.0 < ratio < 1.0:
        raise ValueError("val_ratio 必须在 (0, 1) 之间")

    problems: List[str] = []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    id_counts: Dict[str, int] = {}
    for idx, item in enumerate(accepted):
        sid = str(item.get("sample_id") or "").strip()
        label = str(item.get("label") or "").strip()
        path = str(item.get("iq_path") or "").strip()
        if not (sid and label and path):
            problems.append(f"第 {idx + 1} 条缺 sample_id/label/iq_path，已排除")
            continue
        groups.setdefault(path, []).append(item)
        id_counts[sid] = id_counts.get(sid, 0) + 1

    if not groups and not problems:
        problems.append("没有可切分的样本")

    duplicate_ids = sorted(sid for sid, n in id_counts.items() if n > 1)
    if duplicate_ids:
        problems.append(f"{len(duplicate_ids)} 个 sample_id 重复，切分会有歧义")

    conflict_paths = sorted(
        p for p, rows in groups.items()
        if len({str(r.get("label") or "").strip() for r in rows}) > 1
    )
    if conflict_paths:
        problems.append(f"{len(conflict_paths)} 个 iq_path 同时挂了多个类别"
                        "（标签冲突，无法决定整组进训练还是验证）")

    by_label: Dict[str, List[Tuple[str, List[str]]]] = {}
    for path, rows in groups.items():
        label = str(rows[0].get("label") or "").strip()
        ids = sorted(str(r.get("sample_id") or "").strip() for r in rows)
        by_label.setdefault(label, []).append((path, ids))

    base = {
        "ok": False,
        "train": [],
        "val": [],
        "groups": len(groups),
        "classes": sorted(by_label),
        "val_ratio": ratio,
        "seed": seed,
        "per_class": {},
        "problems": problems,
        "caveat": ("按 iq_path 分组只排除「同一文件跨集合」这一种泄漏；"
                   "同一采集会话、同一设备或相邻时间窗的片段仍可能泄漏，"
                   "本方案不识别，也不保证验证集能代表真实分布。"),
    }
    if problems:
        return base

    train: List[str] = []
    val: List[str] = []
    per_class: Dict[str, Dict[str, int]] = {}
    for label in sorted(by_label):
        items = sorted(by_label[label], key=lambda it: it[0])
        if len(items) < 2:
            problems.append(f"类别 {label} 只有 1 个文件，切不出验证集")
            continue
        n_val = min(max(1, int(round(len(items) * ratio))), len(items) - 1)
        random.Random(f"{seed}:{label}").shuffle(items)
        for i, (_path, ids) in enumerate(items):
            (val if i < n_val else train).extend(ids)
        per_class[label] = {"files": len(items),
                            "train_files": len(items) - n_val,
                            "val_files": n_val}

    if problems:
        return base
    return {
        **base,
        "ok": True,
        "train": sorted(train),
        "val": sorted(val),
        "per_class": per_class,
    }
