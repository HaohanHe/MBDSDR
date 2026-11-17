#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# -*- coding: utf-8 -*-
"""一条命令出包 + 可复现发布清单（Task 60）。

调用既有打包流程（``desktop/MBDSDR.spec`` 的 PyInstaller onedir，或
``scripts/package_windows.ps1`` 的 C++ 便携包），随后对产物目录逐文件计算
SHA256，写出 ``dist/release-manifest.json``。清单包含：

* ``version``：``mbdsdr_ai/VERSION``（唯一手抄版本点）；
* ``build_time``：UTC ISO-8601 构建/清单生成时刻；
* ``git_commit``：构建时 HEAD；无 git 或读取失败时为 ``null``（不伪造）；
* ``files``：按相对路径排序的 ``{path, size, sha256}``；
* ``summary``：文件数与总字节；
* ``dry_run`` / ``target`` / ``command``：本次执行方式，便于复核。

``--dry-run`` 只读取已存在的产物目录并计算清单，不触发任何构建。
清单写入 ``<dist>/release-manifest.json``（可用 ``--manifest`` 覆盖），
自身不进入文件列表，避免自指哈希。

``--zip`` 在构建/清单完成后，把 ``<dist>/MBDSDR``（C++ 目标为
``mbdsdr-<version>-win64-portable``）连同 ``release-manifest.json`` 压成
``<dist>/MBDSDR-<version>-win64-portable.zip``（ZIP_DEFLATED），并写出
``<dist>/release-zip.json``（path/size/sha256/file_count/version/build_time）。
``--zip-only`` 跳过构建，直接对已有产物目录补包。目标 zip 已存在时默认
拒绝（非零退出），需显式 ``--force`` 覆盖；先写 ``.tmp`` 再原子 rename，
失败不留半成品。

    python scripts/build_release.py --dry-run
    python scripts/build_release.py --dry-run --dist d:\\mbdsdr\\.tmp\\pyi-dist16
    python scripts/build_release.py --target desktop
    python scripts/build_release.py --target desktop --zip
    python scripts/build_release.py --zip-only

不联网、不加依赖；C++ 打包仍由 ``package_windows.ps1`` 负责。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = ROOT / "mbdsdr_ai" / "VERSION"
SPEC = ROOT / "desktop" / "MBDSDR.spec"
PACKAGE_PS1 = ROOT / "scripts" / "package_windows.ps1"
DEFAULT_DIST = ROOT / "dist"
CPP_DIST = ROOT / "cpp" / "build-local" / "dist"
MANIFEST_NAME = "release-manifest.json"
ZIP_META_NAME = "release-zip.json"
# 便携包内层目录名 / zip 文件名前缀（C++ 旧包沿用 mbdsdr 小写）
PACKAGE_NAMES = {"desktop": "MBDSDR", "cpp": "mbdsdr"}


def read_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8-sig").strip()


def git_commit() -> Optional[str]:
    """读取当前 HEAD；无 git / 非仓库时返回 None（不编造提交号）。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    commit = proc.stdout.strip()
    return commit or None


def build_command(target: str, dist: Path) -> List[str]:
    """既有打包流程对应的命令（与 build_windows.bat / package_windows.ps1 同源）。"""
    if target == "cpp":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(PACKAGE_PS1)]
    return [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
            "--distpath", str(dist),
            "--workpath", str(ROOT / "build" / "pyi-release"),
            str(SPEC)]


def run_build(target: str, dist: Path) -> None:
    """执行构建；失败即抛出，不写半截清单。"""
    command = build_command(target, dist)
    proc = subprocess.run(command, cwd=str(ROOT), check=False)
    if proc.returncode != 0:
        raise RuntimeError(
            f"打包失败（退出码 {proc.returncode}）：{' '.join(command)}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(dist: Path, manifest_path: Path,
                  exclude: Optional[Sequence[Path]] = None) -> List[Dict[str, Any]]:
    """产物目录逐文件哈希，按相对路径排序；清单自身不参与。"""
    if not dist.is_dir():
        raise FileNotFoundError(f"产物目录不存在：{dist}（先构建或指定 --dist）")
    skip = {manifest_path.resolve()}
    skip.update(p.resolve() for p in (exclude or ()))
    entries: List[Dict[str, Any]] = []
    paths = sorted((p for p in dist.rglob("*") if p.is_file()),
                   key=lambda p: p.relative_to(dist).as_posix())
    for path in paths:
        if path.resolve() in skip:
            continue
        entries.append({
            "path": path.relative_to(dist).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        })
    return entries


def build_manifest(dist: Path, manifest_path: Path, target: str,
                   dry_run: bool,
                   exclude: Optional[Sequence[Path]] = None) -> Dict[str, Any]:
    files = collect_files(dist, manifest_path, exclude)
    return {
        "schema_version": 1,
        "version": read_version(),
        "build_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": git_commit(),
        "dry_run": bool(dry_run),
        "target": target,
        "command": build_command(target, dist),
        "dist_dir": str(dist),
        "files": files,
        "summary": {
            "file_count": len(files),
            "total_bytes": sum(f["size"] for f in files),
        },
    }


def package_dir_for(dist: Path, target: str) -> Path:
    """待压缩的产物目录：desktop=PyInstaller onedir，cpp=cmake install 树。"""
    if target == "cpp":
        return dist / f"mbdsdr-{read_version()}-win64-portable"
    return dist / PACKAGE_NAMES[target]


def zip_name(version: str, target: str) -> str:
    return f"{PACKAGE_NAMES[target]}-{version}-win64-portable.zip"


def zip_package(package_dir: Path, zip_path: Path, manifest_path: Path,
                version: str, force: bool = False) -> Dict[str, Any]:
    """把产物目录 + 清单压成便携 zip，返回元数据；失败不留半成品。

    目标已存在且未给 ``force`` 时拒绝（由调用方转成非零退出）。
    """
    if not package_dir.is_dir():
        raise FileNotFoundError(f"待压缩目录不存在：{package_dir}（先构建或用 --dist 指定）")
    files = sorted((p for p in package_dir.rglob("*") if p.is_file()),
                   key=lambda p: p.relative_to(package_dir).as_posix())
    if not files:
        raise ValueError(f"待压缩目录为空：{package_dir}（拒绝产出空包）")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"缺少发布清单：{manifest_path}（先跑 --dry-run 生成）")
    if zip_path.exists() and not force:
        raise FileExistsError(f"目标 zip 已存在：{zip_path}（覆盖请加 --force）")

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_name(zip_path.name + ".tmp")
    root = package_dir.name
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in files:
                zf.write(path, arcname=f"{root}/{path.relative_to(package_dir).as_posix()}")
            zf.write(manifest_path, arcname=MANIFEST_NAME)
        os.replace(tmp, zip_path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    return {
        "schema_version": 1,
        "path": str(zip_path),
        "size": zip_path.stat().st_size,
        "sha256": _sha256(zip_path),
        "file_count": len(files) + 1,  # 目录内文件 + release-manifest.json
        "version": version,
        "build_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _write_json(payload: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                              sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_manifest(manifest: Dict[str, Any], path: Path) -> None:
    _write_json(manifest, path)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", choices=("desktop", "cpp"), default="desktop",
                    help="既有打包流程：desktop=PyInstaller spec；cpp=package_windows.ps1")
    ap.add_argument("--dist", type=Path, default=None,
                    help="产物目录（默认 desktop→dist/，cpp→cpp/build-local/dist/）")
    ap.add_argument("--manifest", type=Path, default=None,
                    help="清单输出路径（默认 <dist>/release-manifest.json）")
    ap.add_argument("--dry-run", action="store_true",
                    help="不构建，只对已有产物目录计算清单")
    ap.add_argument("--zip", action="store_true",
                    help="构建/清单完成后压 dist/<target>-<version>-win64-portable.zip")
    ap.add_argument("--zip-only", action="store_true",
                    help="跳过构建，直接对已有产物目录补 zip（等价 --dry-run --zip）")
    ap.add_argument("--force", action="store_true",
                    help="目标 zip 已存在时覆盖（默认拒绝，避免覆盖未知产物）")
    args = ap.parse_args(argv)

    want_zip = args.zip or args.zip_only
    dry_run = args.dry_run or args.zip_only

    dist = (args.dist or (CPP_DIST if args.target == "cpp" else DEFAULT_DIST))
    manifest_path = args.manifest or (dist / MANIFEST_NAME)
    if not dist.is_absolute():
        dist = (Path.cwd() / dist).resolve()
    if not manifest_path.is_absolute():
        manifest_path = (Path.cwd() / manifest_path).resolve()

    try:
        version = read_version()
        package_dir = package_dir_for(dist, args.target)
        zip_path = dist / zip_name(version, args.target)
        meta_path = dist / ZIP_META_NAME
        exclude = [zip_path, meta_path] if want_zip else None
        if not dry_run:
            run_build(args.target, dist)
        manifest = build_manifest(dist, manifest_path, args.target, dry_run,
                                  exclude)
        write_manifest(manifest, manifest_path)
        zip_info = None
        if want_zip:
            zip_info = zip_package(package_dir, zip_path, manifest_path,
                                   version, args.force)
            _write_json(zip_info, meta_path)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[build_release] {exc}", file=sys.stderr)
        return 2

    summary = manifest["summary"]
    print(f"版本 {manifest['version']}  文件 {summary['file_count']} 个  "
          f"{summary['total_bytes']} 字节")
    print(f"manifest: {manifest_path}"
          + ("（dry-run，未构建）" if dry_run else ""))
    if zip_info is not None:
        print(f"zip: {zip_info['path']}  {zip_info['size']} 字节  "
              f"{zip_info['file_count']} 个文件")
        print(f"sha256: {zip_info['sha256']}")
        print(f"zip 元数据: {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
