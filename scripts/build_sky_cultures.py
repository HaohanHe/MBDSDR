# SPDX-License-Identifier: MIT
"""
scripts/build_sky_cultures.py — 从 Stellarium 生成本地多文化天空包
==================================================================

读取 ``reference/stellarium/skycultures/<id>/index.json``，只转换
``constellations[].lines``、``id``、``common_name`` 与（若存在的）
``common_names`` 星名，写入 ``mbdsdr_ai/data/skycultures/<id>.json``。

不复制 artwork / illustration / description 正文；HIP 不在本地星表的
记入 ``unresolved_hips``，不丢信息也不伪造。

许可白名单：只接受 ``CC BY-SA 4.0``、``CC BY 4.0``、
``CC0 / Public Domain``。``CC BY-ND``、``CC BY-NC*``、``GNU GPL``
一律拒绝并在运行报告中列出，不生成文件。

上游有些文化的许可行带作用域限定词，如 ``Text and data: CC BY-SA 4.0``：
本仓库只转换连线数据、不复制 artwork，因此数据部分适用该许可。限定词由
``sky_culture.normalize_license()`` 剥离后判白名单，原始串写入
``meta.source_license_raw``；白名单本身不放宽（2.0 / 3.0 / 无版本号仍拒绝）。

用法::

    python scripts/build_sky_cultures.py
    python scripts/build_sky_cultures.py --cultures japanese indian norse
    python scripts/build_sky_cultures.py --stellarium D:/mbdsdr/reference/stellarium
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mbdsdr_ai.skyengine.constellations import payload_sha256  # noqa: E402
from mbdsdr_ai.skyengine.sky_culture import (  # noqa: E402
    ALLOWED_LICENSES,
    SCHEMA,
    SkyCultureMetadataError,
    license_url_for,
    normalize_license,
)

DEFAULT_STELLARIUM = ROOT.parent / "reference" / "stellarium"
DEFAULT_CATALOG = ROOT / "mbdsdr_ai" / "data" / "full_sky_hyg_v41.npz"
DEFAULT_OUT_DIR = ROOT / "mbdsdr_ai" / "data" / "skycultures"

#: 本轮目标。maya 在 reference 里实际是 CC BY-NC-SA 4.0，会被许可白名单
#: 拒绝；seri（墨西哥，CC BY-SA 4.0）作为第五个可再分发文化补位。
DEFAULT_CULTURES = (
    "japanese", "indian", "norse", "indian_nakshatras", "maya", "seri",
)

MAX_PACK_BYTES = 2 * 1024 * 1024
#: Hipparcos 目录编号上限（约 118k）；超出这个范围的数字是 Gaia 源 id
#: 或其它目录标识，不能冒充 HIP。
MAX_HIP = 200000

_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+(?:@|\(at\))[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://\S+")
_HIP_KEY_RE = re.compile(r"^HIP\s+(\d+)$")
_SECTION_RE = r"(?ms)^##\s*{title}\s*$(.*?)(?=^##\s|\Z)"


class LicenseRejectedError(SkyCultureMetadataError):
    """源文化许可不在白名单；转换必须拒绝并说明原因。"""


class PackTooLargeError(ValueError):
    """转换结果超过 2 MB：先报告，不硬塞进仓库。"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _section(text: str, title: str) -> str:
    match = re.search(_SECTION_RE.format(title=re.escape(title)), text)
    return match.group(1).strip() if match else ""


def extract_license(description: str) -> str:
    """取 ``## License`` 下的首个非空行；缺失就报错，不默认放行。"""
    section = _section(description, "License")
    for line in section.splitlines():
        value = line.strip().strip("*").strip()
        if value:
            return value
    raise SkyCultureMetadataError("description.md 缺少 '## License' 声明")


def license_decision(license_text: str) -> str:
    """白名单判定；返回规范许可，拒绝时抛 LicenseRejectedError。

    只剥离 ``Text and data:`` 这类作用域限定词再比对白名单——白名单本身
    不放宽，2.0 / 3.0 / 无版本号剥离后依旧不匹配，照样拒绝。
    """
    canonical = normalize_license(license_text)
    if canonical not in ALLOWED_LICENSES:
        raise LicenseRejectedError(
            f"许可证 {str(license_text or '').strip()!r} 不在白名单 "
            f"{list(ALLOWED_LICENSES)}；"
            "CC BY-ND / CC BY-NC* / GPL 等不可再分发或不可改写的许可不得转换")
    return canonical


def _clean_authors(section: str) -> str:
    """只保留顶层作者署名，去掉邮箱、markdown 链接与后续小节。"""
    text = section.split("\n###")[0]
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\S+\((?:at|AT)\)\S+\((?:dot|DOT)\)\S+", "", text)
    text = _EMAIL_RE.sub("", text)
    text = re.sub(r"(?i)mailto:\S*", "", text)
    text = html.unescape(text).replace("*", "")
    text = re.sub(r"\s*\n\s*", "; ", text)
    text = re.sub(r"(?:;\s*)+", "; ", text)
    text = re.sub(
        r"(?i)^this sky culture was (?:initially )?contributed by "
        r"(?:stellarium user )?", "", text)
    text = re.sub(r"\.\s*;\s*", ". ", text)
    return text.strip(" ;,.:")


def _attribution(description: str, culture_id: str,
                 license_text: str) -> str:
    authors = _clean_authors(_section(description, "Authors"))
    text = f"Stellarium sky culture {culture_id!r}"
    if authors:
        text += f"; authors: {authors}"
    text += (f" ({license_text}). Converted to HIP line segments only; "
             "no artwork or description images were copied.")
    return text


def _display_name(description: str, culture_id: str) -> str:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", description)
    return match.group(1).strip() if match else culture_id


def _name_dict(raw: Any) -> Dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    mapping = (("en", "english"), ("native", "native"),
               ("pronounce", "pronounce"), ("ipa", "IPA"))
    out: Dict[str, str] = {}
    for key, source in mapping:
        value = raw.get(source)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _parse_common_names(raw: Any) -> Dict[int, Dict[str, str]]:
    """``{"HIP 123": [{...}]}`` → ``{123: {"en": ..., "native": ...}}``。"""
    if not isinstance(raw, dict):
        return {}
    out: Dict[int, Dict[str, str]] = {}
    for key, value in raw.items():
        match = _HIP_KEY_RE.match(str(key).strip())
        if not match:
            continue
        hip = int(match.group(1))
        entries = value if isinstance(value, list) else [value]
        merged: Dict[str, str] = {}
        for entry in entries:
            for lang, name in _name_dict(entry).items():
                merged.setdefault(lang, name)
        if merged:
            out[hip] = merged
    return out


def _edge_key(a: int, b: int) -> Tuple[int, int]:
    return (a, b) if a <= b else (b, a)


def _as_hip(value: Any) -> Optional[int]:
    """只接受 Hipparcos 范围内的整数；DSO:* 与 Gaia 源 id 不算 HIP。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        hip = value
    elif isinstance(value, str) and value.strip().isdigit():
        hip = int(value.strip())
    else:
        return None
    return hip if 0 < hip <= MAX_HIP else None


def convert_culture(culture_dir: Path, catalog_hips: Set[int]) -> Dict[str, Any]:
    """把单个 Stellarium 文化目录转成统一 schema（不写文件）。"""
    culture_dir = Path(culture_dir)
    index_path = culture_dir / "index.json"
    description_path = culture_dir / "description.md"
    culture_id = culture_dir.name

    raw = json.loads(index_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SkyCultureMetadataError(f"{culture_id}: index.json 顶层不是对象")
    if str(raw.get("id") or culture_id) != culture_id:
        raise SkyCultureMetadataError(
            f"{culture_id}: index.json id 与目录名不一致")
    description = (description_path.read_text(encoding="utf-8")
                   if description_path.exists() else "")
    license_raw = extract_license(description)
    license_text = license_decision(license_raw)

    groups: List[Dict[str, Any]] = []
    explicit_names: Dict[int, Dict[str, str]] = {}
    all_hips: Set[int] = set()
    unresolved_tokens: Set[str] = set()

    for item in raw.get("constellations") or []:
        if not isinstance(item, dict):
            raise SkyCultureMetadataError(f"{culture_id}: constellation 不是对象")
        group_id = str(item.get("id") or "").strip()
        if not group_id:
            raise SkyCultureMetadataError(f"{culture_id}: constellation 缺 id")
        names = _name_dict(item.get("common_name"))
        lines: List[List[int]] = []
        for poly in item.get("lines") or []:
            hips: List[int] = []
            for value in poly:
                hip = _as_hip(value)
                if hip is None:
                    # DSO:M7 / Gaia 源 id 等不是 HIP：断开折线并记入 meta，
                    # 不冒充 HIP，也不静默丢弃。
                    unresolved_tokens.add(str(value))
                    if len(hips) >= 2:
                        lines.append(hips)
                    hips = []
                    continue
                hips.append(hip)
                all_hips.add(hip)
            if hips:
                lines.append(hips)
        groups.append({
            "group_id": group_id,
            "names": names,
            "lines": lines,
            "kind": "constellation",
        })

    for hip, names in _parse_common_names(raw.get("common_names")).items():
        explicit_names.setdefault(hip, names)

    # 没有显式星名的文化（如 indian_nakshatras）用所属星座/星群名作为
    # 成员星的名称；这是源数据分组本身的名字，不是凭记忆补写。
    names: Dict[str, Dict[str, str]] = {
        str(hip): dict(value) for hip, value in explicit_names.items()}
    for group in groups:
        if not group["names"]:
            continue
        for poly in group["lines"]:
            for hip in poly:
                names.setdefault(str(hip), dict(group["names"]))

    edges = {
        _edge_key(a, b)
        for group in groups
        for poly in group["lines"]
        for a, b in zip(poly, poly[1:])
    }
    unresolved = sorted(hip for hip in all_hips if hip not in catalog_hips)
    meta = {
        "schema": SCHEMA,
        "id": culture_id,
        "display_name": _display_name(description, culture_id),
        "native_name": "",
        "region": str(raw.get("region") or ""),
        "classification": [str(x) for x in raw.get("classification") or []],
        "source": f"Stellarium skyculture {culture_id}",
        "source_url": ("https://github.com/Stellarium/stellarium/tree/"
                       f"master/skycultures/{culture_id}"),
        "source_license": license_text,
        "source_license_raw": license_raw,
        "source_license_url": license_url_for(license_text),
        "attribution": _attribution(description, culture_id, license_raw),
        "source_file": (f"reference/stellarium/skycultures/{culture_id}/"
                        "index.json"),
        "source_file_sha256": sha256_file(index_path),
        "description_file": (f"reference/stellarium/skycultures/{culture_id}/"
                             "description.md"),
        "description_file_sha256": (sha256_file(description_path)
                                    if description_path.exists() else ""),
        "conversion_scope": (
            "Only constellations[].lines, id and common_name (plus optional "
            "common_names star labels) are converted; illustrations, "
            "artwork and description text are not copied."),
        "names_scope": (
            "Explicit common_names labels take precedence; stars without an "
            "explicit label inherit the constellation/asterism name they "
            "belong to."),
        "group_count": len(groups),
        "edge_count": len(edges),
        "name_count": len(names),
        "unresolved_hip_count": len(unresolved),
        "unresolved_hips": unresolved,
        "unresolved_token_count": len(unresolved_tokens),
        "unresolved_tokens": sorted(unresolved_tokens),
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }
    if not groups:
        raise SkyCultureMetadataError(f"{culture_id}: 转换后没有 group")
    if not edges:
        raise SkyCultureMetadataError(f"{culture_id}: 转换后没有有效边")
    if not names:
        raise SkyCultureMetadataError(f"{culture_id}: 转换后没有名称")
    payload = {
        "id": culture_id,
        "meta": meta,
        "groups": groups,
        "names": names,
        "unresolved_hips": unresolved,
    }
    meta["data_sha256"] = payload_sha256(payload)
    return payload


def write_pack(payload: Dict[str, Any], out_dir: Path) -> Path:
    blob = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    size = len(blob.encode("utf-8"))
    if size > MAX_PACK_BYTES:
        raise PackTooLargeError(
            f"{payload['id']}: 转换结果 {size} 字节 > {MAX_PACK_BYTES}，"
            "先报告，不写入仓库")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{payload['id']}.json"
    path.write_text(blob, encoding="utf-8")
    return path


def load_catalog_hips(path: Path) -> Set[int]:
    import numpy as np
    with np.load(path, allow_pickle=False) as npz:
        if "hip" not in npz:
            raise ValueError(f"本地星表缺少 hip 数组: {path}")
        return {int(h) for h in npz["hip"] if int(h) > 0}


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stellarium", default=str(DEFAULT_STELLARIUM))
    parser.add_argument("--catalog", default=str(DEFAULT_CATALOG))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--cultures", nargs="*", default=None)
    args = parser.parse_args(argv)

    stellarium = Path(args.stellarium)
    out_dir = Path(args.out_dir)
    catalog_hips = load_catalog_hips(Path(args.catalog))
    cultures = tuple(args.cultures or DEFAULT_CULTURES)

    refused: List[str] = []
    written: List[str] = []
    failed: List[str] = []
    for culture_id in cultures:
        culture_dir = stellarium / "skycultures" / culture_id
        if not culture_dir.is_dir():
            failed.append(f"{culture_id}: 目录不存在 {culture_dir}")
            continue
        try:
            payload = convert_culture(culture_dir, catalog_hips)
        except LicenseRejectedError as exc:
            refused.append(f"{culture_id}: {exc}")
            continue
        except PackTooLargeError as exc:
            refused.append(f"{culture_id}: {exc}")
            continue
        except (SkyCultureMetadataError, OSError, ValueError) as exc:
            failed.append(f"{culture_id}: {exc}")
            continue
        try:
            path = write_pack(payload, out_dir)
        except PackTooLargeError as exc:
            refused.append(f"{culture_id}: {exc}")
            continue
        meta = payload["meta"]
        written.append(
            f"{culture_id}: groups={meta['group_count']} "
            f"edges={meta['edge_count']} names={meta['name_count']} "
            f"unresolved={meta['unresolved_hip_count']} "
            f"bytes={path.stat().st_size}")
        print(f"WROTE {written[-1]}")

    for line in refused:
        print(f"REFUSED {line}")
    for line in failed:
        print(f"ERROR {line}", file=sys.stderr)

    print(f"SUMMARY written={len(written)} refused={len(refused)} "
          f"failed={len(failed)}")
    if refused:
        print("REFUSED licenses: " + "; ".join(refused))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
