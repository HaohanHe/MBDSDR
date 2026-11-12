#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""诊断包 / 日志产物导出闸：同一套凭据规则，BLOCK 或 MUST 命中即不许导出。

背景：仓库内扫描只证明「提交里没有」。用户把诊断包发给我们时，密钥在
**产品自己写出来的东西**里——启动日志、导出的诊断目录、崩溃转储。本模块把
``scripts/secret_scan.py`` 的规则（同一真值源，不另抄正则）套到这些产物上：

* 文本文件（含无后缀的 ``.log`` / ``.csv`` / 崩溃栈）跑形状规则 + 熵分档；
  ``MUST`` 与 ``BLOCK`` 一样**阻断导出**，``SAMPLE`` / ``WARN`` 只计数；
* 二进制文件跑**字节级**形状规则（exe/dll/pyc/普通 dump 里内嵌的 ASCII 串），
  ``.dmp`` / ``.mdmp`` / ``.wer`` 额外解一遍 UTF-16LE（Windows 转储里字符串
  多为宽字符，只扫 ASCII 会漏）；
* 压缩包**解压后再扫**：zip / tar(.gz|.bz2|.xz) / gz / bz2 / xz 在内存里展开，
  单成员与总量都有上限；加密成员、超限成员、7z/rar/cab 这类解不开的格式
  **按失败关闭处理**（报 ``archive-encrypted-unscanned`` 等），因为「扫不了」
  不能等价于「干净」。

输出纪律：只打「文件 / 成员 + 规则名 + 档位」，绝不打印命中的值。

用法::

    python scripts/diagnostics_scan.py D:\\path\\to\\diagnostics
    python scripts/diagnostics_scan.py bundle.zip
    python scripts/diagnostics_scan.py D:\\logs D:\\crashdumps

退出码：有 BLOCK/MUST → 1；否则 0。导出流程应在**写最终包之前**调用
``assert_exportable(paths)``，命中则抛 ``ExportBlocked`` 并列出文件。
"""
from __future__ import annotations

import argparse
import bz2
import gzip
import io
import lzma
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# 同目录导入：命令行与测试都用 `python scripts/...` 的路径语义。
sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_scan  # noqa: E402

#: 单个文件 / 压缩成员上限（超过不解压，按失败关闭阻断）。
MAX_MEMBER_BYTES = 64 * 1024 * 1024
#: 一个诊断包累计解压上限，防 zip 炸弹。
MAX_TOTAL_BYTES = 512 * 1024 * 1024
#: 允许的嵌套压缩层数（zip 里套 zip）。
MAX_ARCHIVE_DEPTH = 2
#: 文本超过这个体积只跑形状规则（大日志跑熵没有信号，且会拖慢导出）。
_MAX_ENTROPY_BYTES = 8 * 1024 * 1024
#: Windows 崩溃转储里字符串常是 UTF-16LE，额外解一遍。
_DUMP_SUFFIXES = {".dmp", ".mdmp", ".wer"}
#: 标准库解不开、也不许静默放过的压缩格式。
_UNSUPPORTED_ARCHIVES = {".7z", ".rar", ".cab", ".arj", ".lzh"}


@dataclass
class Hit:
    name: str
    rule: str
    tier: str


@dataclass
class BundleResult:
    hits: List[Hit] = field(default_factory=list)
    scanned: int = 0
    binaries: int = 0
    skipped: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.hits)


class ExportBlocked(RuntimeError):
    """导出前自检失败。``result.hits`` 含文件/成员与规则名，不含值。"""

    def __init__(self, result: BundleResult):
        self.result = result
        names = sorted({hit.name for hit in result.hits})
        super().__init__("诊断包含未遮蔽凭据，禁止导出：" + "、".join(names))


def _add(result: BundleResult, name: str, rule: str, tier: str) -> None:
    result.hits.append(Hit(name=name, rule=rule, tier=tier))


def _scan_text_hits(
    name: str, data: bytes, result: BundleResult
) -> Optional[List[Tuple[str, str]]]:
    """文本命中；返回 None 表示不是文本（调用方改走二进制路径）。"""
    text = secret_scan._decode(data)
    if text is None:
        return None
    if len(data) > _MAX_ENTROPY_BYTES:
        result.notes.append(
            f"{name}: >{_MAX_ENTROPY_BYTES // (1024 * 1024)}MB，只跑形状规则"
        )
        hits = []
        for line in text.splitlines():
            if secret_scan.SPDX in line:
                continue
            for rule, rx in secret_scan.SHAPE_RULES:
                if rx.search(line):
                    hits.append((rule, "block"))
        return hits
    return [
        (rule, tier)
        for _path, _line, _col, rule, tier in secret_scan.scan_text(
            name, text, entropy_all=True
        )
        if tier in ("block", "must")
    ]


def _scan_binary_hits(name: str, data: bytes) -> List[Tuple[str, str]]:
    hits = [
        (rule, "block")
        for rule, rx in secret_scan.SHAPE_BYTES
        if rx.search(data)
    ]
    if Path(name).suffix.lower() in _DUMP_SUFFIXES:
        # 转储里的字符串多为 UTF-16LE；ASCII 规则直接扫字节会漏。
        # 从 0/1 两个偏移各解一遍：转储里混入奇数长度头部时，单一只会错位。
        for shift in (0, 1):
            wide = data[shift:].decode("utf-16-le", errors="ignore")
            for rule, rx in secret_scan.SHAPE_RULES:
                if rx.search(wide):
                    hits.append((rule, "block"))
        for rule, rx in secret_scan.SHAPE_RULES:
            if rx.search(data.decode("utf-8", errors="ignore")):
                hits.append((rule, "block"))
    return hits


def _scan_leaf(name: str, data: bytes, result: BundleResult) -> None:
    hits = _scan_text_hits(name, data, result)
    if hits is None:
        hits = _scan_binary_hits(name, data)
        result.binaries += 1
    result.scanned += 1
    for rule, tier in hits:
        _add(result, name, rule, tier)


def _budget(result: BundleResult, name: str, size: int) -> bool:
    if size > MAX_MEMBER_BYTES:
        _add(result, name, "archive-member-too-large", "block")
        result.notes.append(
            f"{name}: >{MAX_MEMBER_BYTES // (1024 * 1024)}MB，为保证导出前必扫，拒绝"
        )
        return False
    return True


def _scan_zip(
    name: str, data: bytes, result: BundleResult, depth: int
) -> None:
    if depth >= MAX_ARCHIVE_DEPTH:
        _add(result, name, "archive-depth-unscanned", "block")
        return
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        _add(result, name, "archive-unreadable", "block")
        return
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            member = f"{name}!{info.filename}"
            if not _budget(result, member, info.file_size):
                continue
            if info.flag_bits & 0x1:
                _add(result, member, "archive-encrypted-unscanned", "block")
                continue
            try:
                with archive.open(info) as handle:
                    blob = handle.read(MAX_MEMBER_BYTES + 1)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                _add(result, member, "archive-member-unreadable", "block")
                continue
            if len(blob) > MAX_MEMBER_BYTES:
                _add(result, member, "archive-member-too-large", "block")
                continue
            _scan_blob(member, blob, result, depth + 1)


def _scan_tar(
    name: str, data: bytes, result: BundleResult, depth: int
) -> None:
    if depth >= MAX_ARCHIVE_DEPTH:
        _add(result, name, "archive-depth-unscanned", "block")
        return
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except (tarfile.TarError, OSError):
        _add(result, name, "archive-unreadable", "block")
        return
    with archive:
        for info in archive.getmembers():
            if not info.isfile():
                continue
            member = f"{name}!{info.name}"
            if not _budget(result, member, info.size):
                continue
            handle = archive.extractfile(info)
            if handle is None:
                _add(result, member, "archive-member-unreadable", "block")
                continue
            with handle:
                blob = handle.read(MAX_MEMBER_BYTES + 1)
            if len(blob) > MAX_MEMBER_BYTES:
                _add(result, member, "archive-member-too-large", "block")
                continue
            _scan_blob(member, blob, result, depth + 1)


def _scan_single_compressed(
    name: str, data: bytes, opener, result: BundleResult, depth: int, inner: str
) -> None:
    if depth >= MAX_ARCHIVE_DEPTH:
        _add(result, name, "archive-depth-unscanned", "block")
        return
    try:
        with opener(io.BytesIO(data)) as handle:
            blob = handle.read(MAX_MEMBER_BYTES + 1)
    except (OSError, EOFError, lzma.LZMAError):
        _add(result, name, "archive-unreadable", "block")
        return
    if len(blob) > MAX_MEMBER_BYTES:
        _add(result, name, "archive-member-too-large", "block")
        return
    # 内层名字去掉压缩后缀：否则 `.log.gz` 解出来还叫 `.gz`，会再解一遍。
    _scan_blob(name[: -len(inner)], blob, result, depth + 1)


def _scan_blob(
    name: str, data: bytes, result: BundleResult, depth: int = 0
) -> None:
    suffix = Path(name).suffix.lower()
    if suffix == ".zip" or data[:4] == b"PK\x03\x04":
        _scan_zip(name, data, result, depth)
    elif suffix in (".tar", ".tgz", ".tar.bz2", ".tar.xz"):
        _scan_tar(name, data, result, depth)
    elif suffix == ".gz":
        _scan_single_compressed(
            name, data, lambda bio: gzip.GzipFile(fileobj=bio), result, depth, ".gz"
        )
    elif suffix == ".bz2":
        _scan_single_compressed(
            name, data, bz2.BZ2File, result, depth, ".bz2"
        )
    elif suffix == ".xz":
        _scan_single_compressed(
            name, data, lzma.LZMAFile, result, depth, ".xz"
        )
    elif suffix in _UNSUPPORTED_ARCHIVES:
        # 解不开就不能说「干净」：失败关闭。
        _add(result, name, "unsupported-compression", "block")
    else:
        _scan_leaf(name, data, result)


def _iter_files(paths: Sequence[str]) -> Iterable[Tuple[str, Path]]:
    for raw in paths:
        root = Path(raw)
        if root.is_file():
            yield root.as_posix(), root
        elif root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    yield path.as_posix(), path


def scan_export_bundle(paths: Sequence[str]) -> BundleResult:
    """扫一个诊断包（文件或目录，可多个）。只读、不落盘。"""
    result = BundleResult()
    total = 0
    for display, path in _iter_files(paths):
        try:
            size = path.stat().st_size
        except OSError:
            result.skipped += 1
            result.notes.append(f"{display}: 读不到（跳过并计数）")
            continue
        total += size
        if total > MAX_TOTAL_BYTES:
            _add(result, display, "bundle-too-large-unscanned", "block")
            result.notes.append(
                f"累计 >{MAX_TOTAL_BYTES // (1024 * 1024)}MB，余下不再展开"
            )
            break
        try:
            data = path.read_bytes()
        except OSError:
            result.skipped += 1
            result.notes.append(f"{display}: 读取失败（跳过并计数）")
            continue
        _scan_blob(display, data, result)
    return result


def assert_exportable(paths: Sequence[str]) -> BundleResult:
    """导出流程的唯一入口：干净则返回结果，命中则抛 ``ExportBlocked``。"""
    result = scan_export_bundle(paths)
    if result.blocked:
        raise ExportBlocked(result)
    return result


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="诊断包/日志产物导出闸（命中 BLOCK/MUST 即退出 1，不打印值）"
    )
    parser.add_argument("paths", nargs="+", help="诊断包文件或目录，可多个")
    args = parser.parse_args(argv)

    result = scan_export_bundle(args.paths)
    for hit in sorted(result.hits, key=lambda item: (item.name, item.rule)):
        print(f"EXPORT-BLOCK {hit.name} {hit.rule} [{hit.tier}]")
    print(
        f"[diagnostics-scan] 文件={result.scanned} 二进制={result.binaries} "
        f"命中={len(result.hits)} 跳过={result.skipped}"
        "（输出只含文件与规则名，不含值）"
    )
    for note in result.notes[:20]:
        print(f"[diagnostics-scan] 注：{note}")
    if len(result.notes) > 20:
        print(f"[diagnostics-scan] 注：其余 {len(result.notes) - 20} 条省略")
    return 1 if result.blocked else 0


if __name__ == "__main__":
    sys.exit(main())
