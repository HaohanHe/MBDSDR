#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""把 QGIS 三图层的 GeoJSON + QML 打成一个可直接分发的 zip。

为什么要有这个脚本
==================
``export_geojson.py`` 落三个 GeoJSON，``export_qgis_style.py`` 落三个同名
QML；分发时手工挑这 6 个文件容易漏、也说不清哪个版本配哪个样式。本脚本
**不重新生成任何数据**，只读取已经落盘的 6 个文件，扁平打进一个 zip，并
生成一份带 sha256 的清单，便于收发双方核对。

产物
====
* ``mbdsdr-qgis-bundle.zip``：6 个图层文件 + ``README.txt``（导入步骤、
  来源/许可、生成时间），全部在 zip 根目录，没有中间层目录；
* ``mbdsdr-qgis-bundle.json``：``zip_path/size/sha256/files/.../generated_utc``，
  其中每个成员的 ``size``/``sha256`` 都是包内实际字节。

缺任何一个输入文件都不打包：非零退出，列出缺哪些、该先跑哪个脚本。
目标 zip 已存在时默认拒绝，``--force`` 才覆盖。写 zip 与清单都先写
``.tmp`` 再 ``os.replace``，失败不留半截文件。

用法
====
    python scripts/export_qgis_bundle.py
    python scripts/export_qgis_bundle.py --src D:\\mbdsdr\\output
    python scripts/export_qgis_bundle.py --out D:\\tmp\\qgis.zip --force
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
#: 与 export_geojson.py / export_qgis_style.py 的默认输出目录保持一致。
DEFAULT_SRC = ROOT.parent / "output"
DEFAULT_OUT = DEFAULT_SRC / "mbdsdr-qgis-bundle.zip"

#: 六个输入文件。前三个由 export_geojson.py 生成，后三个由
#: export_qgis_style.py 生成；zip 内保持同样的名字（QGIS 按同名匹配 QML）。
GEOJSON_FILES: Tuple[str, ...] = (
    "observing_sites.geojson",
    "ground_track.geojson",
    "footprint.geojson",
)
QML_FILES: Tuple[str, ...] = (
    "observing_sites.qml",
    "ground_track.qml",
    "footprint.qml",
)
LAYER_FILES: Tuple[str, ...] = GEOJSON_FILES + QML_FILES

#: 图层说明，只用于 README.txt。
LAYER_TITLES: Dict[str, str] = {
    "observing_sites.geojson": "观测台址与公众观星点（Point）",
    "observing_sites.qml": "按 access 分类配色 + name_zh 标注",
    "ground_track.geojson": "卫星地面轨迹（LineString，按日界线分段）",
    "ground_track.qml": "单符号描边",
    "footprint.geojson": "卫星覆盖圈（Polygon/LineString）+ 星下点（Point）",
    "footprint.qml": "按 role 规则渲染：面填充 / 边界线 / 星下点",
}

README_NAME = "README.txt"


class BundleError(Exception):
    """可预期的打包失败（缺文件、目标已存在、读写失败）。"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_readme(generated_utc: str) -> str:
    """README.txt：拿到包的人不看仓库也能导入，并知道数据从哪来。"""
    lines = [
        "MBDSDR QGIS 三图层包",
        "====================",
        "",
        f"生成时间（UTC）：{generated_utc}",
        "生成脚本：scripts/export_qgis_bundle.py（仅 Python 标准库，不联网）",
        "",
        "包内文件（全部在 zip 根目录）",
        "----------------------------",
    ]
    for name in LAYER_FILES:
        lines.append(f"  {name:<24} {LAYER_TITLES[name]}")
    lines += [
        "",
        "导入步骤（QGIS 3.x）",
        "--------------------",
        "1. 把本包解压到任意目录，保持 6 个图层文件在同一目录。",
        "2. 菜单「图层 → 添加图层 → 添加矢量图层…」，源类型选「文件」，",
        "   编码选 UTF-8，选中 .geojson。投影保持默认（数据是 WGS84 /",
        "   RFC 7946，坐标序 [lon, lat]）。",
        "3. 若 QGIS 没有自动套用同名 .qml：双击图层 → 图层属性 → 符号化 →",
        "   左下角「样式」→「载入样式…」，选择同名 .qml。",
        "4. 对三个 .geojson 重复第 2–3 步。",
        "",
        "来源与许可",
        "----------",
        "- 数据：scripts/export_geojson.py 导出的内置台址与调用方 TLE 传播结果；",
        "  台址来源 URL 见各要素属性的 source_url 字段。",
        "- 样式：scripts/export_qgis_style.py 生成。",
        "- 许可：MBDSDR 仓库整体为 MIT；TLE 等第三方数据的使用请遵循其提供方条款。",
        "",
        "诚实边界",
        "--------",
        "本包只保证 GeoJSON 是合法 RFC 7946、QML 是合法 XML 且字段与几何类型",
        "对得上；生成环境没有安装 QGIS，渲染效果未在 QGIS 内验证。",
        "",
        "校验",
        "----",
        "同名 .json 清单记录 zip 的 size/sha256 以及每个成员的 size/sha256。",
        "",
    ]
    return "\n".join(lines)


def collect_inputs(src: Path) -> Tuple[Dict[str, bytes], List[str]]:
    """读取 6 个输入文件；返回 (文件名→字节, 缺失文件名列表)。"""
    payload: Dict[str, bytes] = {}
    missing: List[str] = []
    for name in LAYER_FILES:
        path = src / name
        if not path.is_file():
            missing.append(name)
            continue
        try:
            payload[name] = path.read_bytes()
        except OSError as exc:
            raise BundleError(f"读不到输入文件 {path}：{exc}") from exc
    return payload, missing


def missing_hint(src: Path, missing: Sequence[str]) -> str:
    """缺文件时给出「缺什么 + 先跑哪个脚本」的可执行提示。"""
    lines = ["缺少输入文件，未生成分发包："]
    for name in missing:
        lines.append(f"  - {src / name}")
    lines.append("请先生成：")
    if any(name in GEOJSON_FILES for name in missing):
        lines += [
            "  数据（scripts/export_geojson.py）：",
            "    python scripts/export_geojson.py <输出>.geojson "
            "--layer observing-sites",
            "    python scripts/export_geojson.py <输出>.geojson "
            "--layer ground-track --tle <TLE> --sat <卫星> --start <ISO-8601>",
            "    python scripts/export_geojson.py <输出>.geojson "
            "--layer footprint --tle <TLE> --sat <卫星> --start <ISO-8601>",
        ]
    if any(name in QML_FILES for name in missing):
        lines.append("  样式（scripts/export_qgis_style.py）：")
        lines.append(f"    python scripts/export_qgis_style.py --out {src}")
    lines.append("补全后重跑本脚本。")
    return "\n".join(lines)


def manifest_path_for(out: Path) -> Path:
    """清单与 zip 同目录同主名：``foo.zip`` → ``foo.json``。"""
    if out.suffix.lower() == ".zip":
        return out.with_suffix(".json")
    return out.with_name(out.name + ".json")


def _write_zip_atomic(dest: Path, entries: Sequence[Tuple[str, bytes]]) -> None:
    """同目录临时文件写完整 zip 再 rename；失败不留残片。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp",
                               dir=str(dest.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries:
                # writestr 直接用内存里的字节，清单哈希与实际成员严格同源。
                zf.writestr(name, data)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _write_json_atomic(dest: Path, payload: Dict[str, Any]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp",
                               dir=str(dest.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def build_bundle(src: Path, out: Path, *, force: bool = False,
                 generated_utc: str | None = None) -> Dict[str, Any]:
    """读取 6 个输入文件并写 zip + 清单，返回清单内容。"""
    payload, missing = collect_inputs(src)
    if missing:
        raise BundleError(missing_hint(src, missing))
    if out.exists() and not force:
        raise BundleError(
            f"目标已存在：{out}\n如需覆盖请加 --force。")

    stamp = generated_utc or _dt.datetime.now(
        _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries: List[Tuple[str, bytes]] = [
        (name, payload[name]) for name in LAYER_FILES
    ]
    entries.append((README_NAME, build_readme(stamp).encode("utf-8")))

    dest = out.expanduser()
    manifest = manifest_path_for(dest)
    _write_zip_atomic(dest, entries)
    zip_bytes = dest.read_bytes()
    files = [{"name": name, "size": len(data), "sha256": _sha256(data)}
             for name, data in entries]
    payload_manifest: Dict[str, Any] = {
        "zip_path": str(dest.resolve()),
        "size": len(zip_bytes),
        "sha256": _sha256(zip_bytes),
        "files": files,
        "generated_utc": stamp,
    }
    _write_json_atomic(manifest, payload_manifest)
    return payload_manifest


def main(argv: Any = None) -> int:
    parser = argparse.ArgumentParser(
        description="把一个 QGIS 三图层的 GeoJSON + QML 打成可分发 zip")
    parser.add_argument("--src", default=str(DEFAULT_SRC),
                        help=f"输入目录（默认 {DEFAULT_SRC}）")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help=f"输出 zip 路径（默认 {DEFAULT_OUT}）")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已存在的目标 zip（默认拒绝）")
    args = parser.parse_args(argv)

    src = Path(args.src).expanduser()
    out = Path(args.out).expanduser()
    try:
        manifest = build_bundle(src, out, force=args.force)
    except BundleError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"打包失败：{exc}", file=sys.stderr)
        return 1

    print(f"打包完成：{out}")
    print(f"大小：{manifest['size']} 字节")
    print(f"SHA-256：{manifest['sha256']}")
    print(f"文件：{len(manifest['files'])} 个"
          f"（{len(LAYER_FILES)} 个图层文件 + {README_NAME}）")
    print(f"清单：{manifest_path_for(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
