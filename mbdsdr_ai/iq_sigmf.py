# SPDX-License-Identifier: MIT
"""IQ 样本清单 → SigMF 元数据（``<stem>.sigmf-meta``）。

为什么做这一步
==============

``iq_sample_manifest`` 解决的是「一条样本算不算数」，但产物还是仓库私有的
JSONL。SigMF 是社区通用的 Recording 描述格式（``reference/sigmf`` 里就是
v1.2.6 的 JSON Schema），把清单写成 ``.sigmf-meta``，样本才有可能被
GNU Radio / SigMF 生态直接读走——这是克隆项目线里最容易验证的一步。

三条不越线的口径
================

1. ``core:datatype`` 是 SigMF **必填**项。清单没写 ``datatype`` 的样本一律
   不导出，并在报告里点名；**不按扩展名猜** ``cu8`` / ``cf32``。
2. 采样率、中心频率、license 只从清单取值；清单没有的不编，也不补默认值。
3. 只写 ``<stem>.sigmf-meta``，**不动数据文件**：不改名、不复制、不删除。
   数据文件不是 ``<stem>.sigmf-data`` 时，用 ``core:dataset`` 如实声明为
   Non-Conforming Dataset（schema 明确该字段只用于 NCD），并提示要成为
   合规 Recording 需要把数据文件命名成什么。

license 的两条路
================

SigMF 的 ``core:license`` 要求是 license 文档的 URI（RFC 3986）。清单里的
license 常是 ``CC-BY-4.0`` 这种短标识，硬塞进 ``core:license`` 就是不合规。
所以：能认成 URI 才写 ``core:license``；否则按原值记进 ``mbdsdr:license``
（SigMF 允许命名空间扩展字段），并在报告里说明为什么没进 core。

与仓库里另外两处 SigMF 读取的分工
=================================

``recording_meta.read_sigmf_meta`` 是录制/回放侧的**宽松**读取（失败返回
None、吞异常，保证录制主循环不被打断）；``baseband_io._sigmf_meta_global``
只为回放挑格式。这里是数据集交换侧的**严格**读取：必须报出「缺哪个必填
键、文件长度是不是整样本、license 有没有写」，所以既不吞异常也不给默认
值。三者不合并——宽松是录制的要求，严格是「不能假装合规」的要求。
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "SIGMF_VERSION",
    "SIGMF_REQUIRED_TOP",
    "SIGMF_REQUIRED_GLOBAL",
    "SIGMF_REQUIRED_CAPTURE",
    "DATATYPE_PATTERN",
    "DATATYPE_ITEMSIZE",
    "datatype_itemsize",
    "META_SUFFIX",
    "DATA_SUFFIX",
    "check_sample",
    "build_meta",
    "plan_export",
    "export_samples",
    "format_report",
    "read_meta",
    "manifest_entry_from_meta",
]

#: 按 reference/sigmf/sigmf-schema.json 的 ``$id``（v1.2.6）写。
SIGMF_VERSION = "1.2.0"

#: schema 顶层 / global / captures 的 required 键（用于结构断言，不依赖
#: 本机是否装了 jsonschema）。
SIGMF_REQUIRED_TOP = ("global", "captures", "annotations")
SIGMF_REQUIRED_GLOBAL = ("core:datatype", "core:version")
SIGMF_REQUIRED_CAPTURE = ("core:sample_start",)

#: schema ``core:datatype`` 的 pattern，这里用 fullmatch 收紧（schema 原
#: pattern 没有锚尾，前缀命中即过）。
DATATYPE_PATTERN = re.compile(
    r"^(c|r)(f32|f64|i32|i16|u32|u16|i8|u8)(_le|_be)?$")

META_SUFFIX = ".sigmf-meta"
DATA_SUFFIX = ".sigmf-data"

#: 清单里可选的 datatype 键名。只认这一个，避免「以为写了其实没写」。
DATATYPE_FIELD = "datatype"

#: schema 的取值区间：sample_rate (0, 1e12]，frequency [-1e12, 1e12]。
_MAX_RATE = 1e12
_MAX_FREQ = 1e12

_URI_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:[^\s]+$")

#: 每种 datatype 的**单样本字节数**（复样本含 I/Q 两路）。用于把数据文件
#: 大小换算成样本数、以及发现「文件长度不是整样本」的可疑录制。
DATATYPE_ITEMSIZE = {
    "cf32": 8, "cf64": 16, "ci32": 8, "ci16": 4, "cu32": 8, "cu16": 4,
    "ci8": 2, "cu8": 2,
    "rf32": 4, "rf64": 8, "ri32": 4, "ri16": 2, "ru32": 4, "ru16": 2,
    "ri8": 1, "ru8": 1,
}


def datatype_itemsize(datatype: str) -> Optional[int]:
    """``cf32_le`` / ``cu8`` → 单样本字节数；不认识的写法返回 ``None``。

    字节序后缀不影响每个样本的字节数，所以先剥 ``_le`` / ``_be`` 再查表。
    """
    text = _text(datatype).lower()
    if text.endswith(("_le", "_be")):
        text = text[:-3]
    return DATATYPE_ITEMSIZE.get(text)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_rate(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if not math.isfinite(v) or v <= 0.0 or v > _MAX_RATE:
        return None
    return v


def _center_freq(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if not math.isfinite(v) or abs(v) > _MAX_FREQ:
        return None
    return v


def check_sample(sample: Dict[str, Any]) -> List[str]:
    """返回「不能导出」的理由；空列表表示可以写 meta。

    只看 SigMF 硬性要求与名单里实际写了什么，不猜、不补。
    """
    reasons: List[str] = []
    datatype = _text(sample.get(DATATYPE_FIELD))
    if not datatype:
        reasons.append("未声明 datatype（core:datatype 是 SigMF 必填，不按"
                       "扩展名猜）")
    elif not DATATYPE_PATTERN.fullmatch(datatype):
        reasons.append(f"datatype {datatype!r} 不是 SigMF 认可的取值"
                       "（如 cu8 / ci16_le / cf32_le）")

    if _positive_rate(sample.get("sample_rate")) is None:
        reasons.append(f"sample_rate {sample.get('sample_rate')!r} 不在 "
                       f"(0, {_MAX_RATE:g}] 内")
    if _center_freq(sample.get("center_freq")) is None:
        reasons.append(f"center_freq {sample.get('center_freq')!r} 不在 "
                       f"[-{_MAX_FREQ:g}, {_MAX_FREQ:g}] 内")
    if not _text(sample.get("iq_path")):
        reasons.append("iq_path 为空，无法确定数据文件与 meta 的落点")
    return reasons


def _dataset_layout(iq_path: str) -> Tuple[str, str, bool]:
    """``(数据文件名, 不带扩展名的 stem, 是否已合规命名)``。"""
    dataset = os.path.basename(iq_path)
    stem = os.path.splitext(dataset)[0]
    return dataset, stem, dataset == stem + DATA_SUFFIX


def _build(sample: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """清单条目 → ``(meta, notes)``；不合格直接 ``ValueError``。"""
    reasons = check_sample(sample)
    if reasons:
        raise ValueError("；".join(reasons))

    dataset, stem, compliant = _dataset_layout(_text(sample["iq_path"]))
    notes: List[str] = []

    glob: Dict[str, Any] = {
        "core:datatype": _text(sample[DATATYPE_FIELD]),
        "core:version": SIGMF_VERSION,
        "core:sample_rate": float(_positive_rate(sample["sample_rate"])),
    }

    license_text = _text(sample.get("license"))
    if _URI_RE.match(license_text):
        glob["core:license"] = license_text
    else:
        # SigMF 的 core:license 必须是 URI；短标识照原值放扩展字段。
        glob["mbdsdr:license"] = license_text
        notes.append(f"license 是 {license_text!r}，不是 URI，未写入 "
                     "core:license，改记 mbdsdr:license")

    if not compliant:
        glob["core:dataset"] = dataset
        notes.append(f"数据文件 {dataset} 不是 {stem}{DATA_SUFFIX}，已按 "
                     "Non-Conforming Dataset 用 core:dataset 声明；未改名、"
                     f"未复制数据。要成为合规 SigMF Recording，数据文件需"
                     f"命名为 {stem}{DATA_SUFFIX}")

    meta = {
        "global": glob,
        "captures": [{
            "core:sample_start": 0,
            "core:frequency": float(_center_freq(sample["center_freq"])),
        }],
        "annotations": [],
    }
    return meta, notes


def build_meta(sample: Dict[str, Any]) -> Dict[str, Any]:
    """单个清单条目 → SigMF meta dict。不合格时抛 ``ValueError``。"""
    return _build(sample)[0]


def plan_export(
    accepted: List[Dict[str, Any]],
    check_data: bool = True,
) -> Dict[str, Any]:
    """已过清单契约的样本 → 导出计划（不写盘）。

    返回 ``{entries, skipped, counts}``。``entries`` 里每条都已带好完整的
    meta 与落点；``skipped`` 逐条给理由（不导出、但也不假装导出成功）。
    """
    entries: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    path_rows: Dict[str, List[str]] = {}
    for item in accepted:
        iq_path = _text(item.get("iq_path"))
        if iq_path:
            path_rows.setdefault(os.path.abspath(iq_path), []).append(
                _text(item.get("sample_id")))

    for item in accepted:
        sid = _text(item.get("sample_id"))
        iq_path = _text(item.get("iq_path"))

        if iq_path and len(path_rows[os.path.abspath(iq_path)]) > 1:
            skipped.append({
                "sample_id": sid,
                "iq_path": iq_path,
                "reasons": ["同一 iq_path 被登记多次，导出的 meta 会互相"
                            "覆盖，先修清单"],
            })
            continue

        try:
            meta, notes = _build(item)
        except ValueError as exc:
            skipped.append({"sample_id": sid, "iq_path": iq_path,
                            "reasons": [str(exc)]})
            continue

        data_path = os.path.abspath(iq_path)
        if check_data and not os.path.isfile(data_path):
            skipped.append({"sample_id": sid, "iq_path": iq_path,
                            "reasons": [f"数据文件不存在：{data_path}"]})
            continue

        dataset, stem, compliant = _dataset_layout(iq_path)
        entries.append({
            "sample_id": sid,
            "iq_path": iq_path,
            "dataset": dataset,
            "meta_path": os.path.join(os.path.dirname(data_path),
                                      stem + META_SUFFIX),
            "compliant_recording": compliant,
            "meta": meta,
            "notes": notes,
        })

    return {
        "entries": entries,
        "skipped": skipped,
        "counts": {"planned": len(entries), "skipped": len(skipped)},
    }


def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
    """临时文件 + ``os.replace``：失败不留半截 meta。"""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".sigmf-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2,
                      allow_nan=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def export_samples(
    accepted: List[Dict[str, Any]],
    dry_run: bool = False,
    force: bool = False,
    check_data: bool = True,
) -> Dict[str, Any]:
    """按计划写 ``<stem>.sigmf-meta``；同名文件默认不覆盖。

    ``dry_run`` 只返回「会写什么」，一个字节都不落盘。
    """
    plan = plan_export(accepted, check_data=check_data)
    written: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = list(plan["skipped"])

    for entry in plan["entries"]:
        if os.path.exists(entry["meta_path"]) and not force:
            skipped.append({
                "sample_id": entry["sample_id"],
                "iq_path": entry["iq_path"],
                "reasons": [f"同名 meta 已存在：{entry['meta_path']}"
                            "（默认不覆盖，--force 才重写）"],
            })
            continue
        if not dry_run:
            _write_json_atomic(entry["meta_path"], entry["meta"])
        written.append(entry)

    return {
        "dry_run": dry_run,
        "written": written,
        "skipped": skipped,
        "counts": {"written": len(written), "skipped": len(skipped)},
    }


def format_report(result: Dict[str, Any]) -> str:
    """人话报告：先给数量，再逐条列跳过理由与需要注意的命名事实。"""
    written = result.get("written") or []
    skipped = result.get("skipped") or []
    verb = "将写出" if result.get("dry_run") else "写出"
    lines = [f"SigMF 导出：{verb} {len(written)} 份 {META_SUFFIX}，"
             f"跳过 {len(skipped)} 条"
             + ("（dry-run，未写盘）" if result.get("dry_run") else "")]

    for entry in written:
        tail = "合规 Recording" if entry.get("compliant_recording") \
            else "Non-Conforming Dataset"
        lines.append(f"- {entry['sample_id']} → {entry['meta_path']}"
                     f"（{tail}）")
        for note in entry.get("notes") or []:
            lines.append(f"  · {note}")

    for item in skipped:
        label = item.get("sample_id") or "?"
        lines.append(f"- 跳过 {label}：" + "；".join(item.get("reasons") or []))

    ncd = [e for e in written if not e.get("compliant_recording")]
    if ncd:
        lines.append(
            f"注：{len(ncd)} 份 meta 对应的是 Non-Conforming Dataset——数据"
            "文件仍叫原名。本工具没有改名、复制或删除任何数据文件；要成为"
            "合规 SigMF Recording，需由你自己把数据文件命名为 "
            f"<stem>{DATA_SUFFIX}（与 meta 同目录）。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 反向：读已有 .sigmf-meta → 清单条目
# ---------------------------------------------------------------------------


def _as_index(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


def read_meta(path: str, data_path: Optional[str] = None) -> Dict[str, Any]:
    """读一份 ``.sigmf-meta``，检查结构并抽出清单需要的真实取值。

    只做**结构**判断（schema 的 required 键、datatype 取值、captures/
    annotations 按 ``core:sample_start`` 升序、数据文件长度是整样本），
    不评样本质量。缺什么就写进 ``problems``，不补默认值。

    频率取 ``captures[0].core:frequency``；早期版本把频率放在
    ``global.core:frequency``（仓库里的真实样本就是这样），此时回退过去
    并标明来源。license 先看 ``core:license``，再看本仓库写的
    ``mbdsdr:license``（短标识不是 URI，不能进 core 字段）。
    """
    result: Dict[str, Any] = {
        "ok": False,
        "path": path,
        "datatype": "",
        "sample_rate": None,
        "center_freq": None,
        "center_freq_source": None,
        "license": "",
        "license_source": None,
        "license_ok": False,
        "data_path": "",
        "samples": None,
        "problems": [],
    }
    problems: List[str] = result["problems"]

    if not os.path.isfile(path):
        problems.append(f"meta 文件不存在：{path}")
        return result
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            meta = json.load(fh)
    except (OSError, ValueError) as exc:
        problems.append(f"读不出 JSON：{exc}")
        return result
    if not isinstance(meta, dict):
        problems.append("meta 顶层必须是 JSON 对象")
        return result
    result["meta"] = meta

    for key in SIGMF_REQUIRED_TOP:
        if key not in meta:
            problems.append(f"缺少顶层 {key}")
    glob = meta.get("global")
    if not isinstance(glob, dict):
        problems.append("global 必须是对象")
        glob = {}
    for key in SIGMF_REQUIRED_GLOBAL:
        if key not in glob:
            problems.append(f"global 缺少 {key}")

    datatype = _text(glob.get("core:datatype"))
    if datatype and not DATATYPE_PATTERN.fullmatch(datatype):
        problems.append(f"core:datatype {datatype!r} 不是 SigMF 认可的取值")
    result["datatype"] = datatype

    rate = _positive_rate(glob.get("core:sample_rate"))
    if glob.get("core:sample_rate") is None:
        problems.append("global 缺 core:sample_rate（SigMF 不强制，但没有它"
                        "无法进清单）")
    elif rate is None:
        problems.append(f"core:sample_rate {glob.get('core:sample_rate')!r} 不"
                        f"在 (0, {_MAX_RATE:g}] 内")
    result["sample_rate"] = rate

    captures = meta.get("captures")
    if not isinstance(captures, list):
        problems.append("captures 必须是数组")
        captures = []
    starts: List[int] = []
    for idx, cap in enumerate(captures):
        if not isinstance(cap, dict):
            problems.append(f"captures[{idx}] 必须是对象")
            continue
        start = _as_index(cap.get("core:sample_start"))
        if start is None:
            problems.append(f"captures[{idx}] 缺 core:sample_start（或不是"
                            "非负整数）")
        else:
            starts.append(start)
    if starts != sorted(starts):
        problems.append("captures 未按 core:sample_start 升序排列")

    annotations = meta.get("annotations")
    if not isinstance(annotations, list):
        problems.append("annotations 必须是数组")
        annotations = []
    a_starts: List[int] = []
    for idx, ann in enumerate(annotations):
        if not isinstance(ann, dict):
            problems.append(f"annotations[{idx}] 必须是对象")
            continue
        start = _as_index(ann.get("core:sample_start"))
        if start is None:
            problems.append(f"annotations[{idx}] 缺 core:sample_start")
        else:
            a_starts.append(start)
    if a_starts != sorted(a_starts):
        problems.append("annotations 未按 core:sample_start 升序排列")

    freq_value: Any = None
    if captures and isinstance(captures[0], dict) \
            and "core:frequency" in captures[0]:
        freq_value = captures[0]["core:frequency"]
        result["center_freq_source"] = "captures"
    elif "core:frequency" in glob:
        freq_value = glob["core:frequency"]
        result["center_freq_source"] = "global（早期版本写法）"
    if freq_value is None:
        problems.append("没有中心频率（captures[0].core:frequency 与 "
                        "global.core:frequency 都没有）")
    else:
        freq = _center_freq(freq_value)
        if freq is None:
            problems.append(f"core:frequency {freq_value!r} 超出 SigMF 值域")
        result["center_freq"] = freq

    core_license = _text(glob.get("core:license"))
    license_problems: List[str] = []
    if core_license:
        result["license"] = core_license
        result["license_source"] = "core:license"
        if not _URI_RE.match(core_license):
            license_problems.append(f"core:license {core_license!r} 不是 URI")
    else:
        short = _text(glob.get("mbdsdr:license"))
        if short:
            result["license"] = short
            result["license_source"] = "mbdsdr:license"
        else:
            license_problems.append("未声明 license（core:license / "
                                    "mbdsdr:license 都没有），来源不清")

    # 数据文件：优先 core:dataset（NCD），否则同 stem 的 .sigmf-data。
    dataset = _text(glob.get("core:dataset"))
    if not data_path:
        if dataset:
            data_path = os.path.join(os.path.dirname(os.path.abspath(path)),
                                     dataset)
        else:
            stem = os.path.splitext(os.path.basename(path))[0]
            data_path = os.path.join(os.path.dirname(os.path.abspath(path)),
                                     stem + DATA_SUFFIX)
    result["data_path"] = data_path
    if not os.path.isfile(data_path):
        problems.append(f"数据文件不存在：{data_path}")
    else:
        itemsize = datatype_itemsize(datatype)
        if itemsize:
            size = os.path.getsize(data_path)
            result["samples"] = size // itemsize
            if size == 0:
                # 录制器在「开始录制但一个样本都没落盘」时会留下 0 字节文件
                # 和完整 meta；结构没问题，但它不是一个能用的样本。
                problems.append("数据文件为空（0 字节 / 0 样本），不能当样本用")
            elif size % itemsize:
                problems.append(
                    f"数据文件长度 {size} B 不是 {datatype} 单样本 "
                    f"{itemsize} B 的整数倍（疑似截断或 datatype 写错）")

    # ``ok`` 只看结构与数据文件：SigMF 里 license 本就是可选项，缺 license
    # 是「不能进清单」而不是「meta 坏」。两条结论分开给，避免把可补的
    # 授权问题说成文件损坏。
    result["ok"] = not problems
    result["problems"].extend(license_problems)
    result["license_ok"] = not license_problems
    return result


def manifest_entry_from_meta(
    info: Dict[str, Any],
    sample_id: str,
    label: str,
    license_override: str = "",
) -> Dict[str, Any]:
    """``read_meta`` 的结果 → 一条清单条目（只搬真实取值）。

    ``label`` 由调用方声明：SigMF meta 里没有可靠的类别字段，工具不猜。
    ``license_override`` 是使用者以数据拥有者身份显式给出的授权；不传就
    只有 meta 里写过的 license 可用。缺任何必填项时 ``entry`` 为 ``None``
    并给出 ``problems``。
    """
    problems: List[str] = []
    if not info.get("ok"):
        problems.extend(info.get("problems") or [])
    sample_id = _text(sample_id)
    if not sample_id:
        problems.append("sample_id 为空")
    label = _text(label)
    if not label:
        problems.append("未给出 label（SigMF 元数据里没有类别，工具不猜）")

    license_text = _text(license_override) or _text(info.get("license"))
    if not license_text:
        problems.append("meta 未声明 license，且没有显式给出 --license")

    entry = {
        "sample_id": sample_id,
        "label": label,
        "sample_rate": info.get("sample_rate"),
        "center_freq": info.get("center_freq"),
        "iq_path": _text(info.get("data_path")),
        "license": license_text,
        "datatype": _text(info.get("datatype")),
    }
    if not entry["datatype"]:
        problems.append("meta 没有可用的 core:datatype")
    for field in ("sample_rate", "center_freq", "iq_path"):
        if entry[field] in (None, ""):
            if not any(field in p for p in problems):
                problems.append(f"{field} 缺失")
    return {"entry": None if problems else entry, "problems": problems}

