# SPDX-License-Identifier: MIT
"""
tests/test_star_catalog_packaging.py — 保证“装上了确实可用”

两层验证:
  1. 打包清单层: pyproject.toml 的 package-data 必须匹配 data/*.npz，
     且星表文件真实存在；
  2. 真构建层: 实际构建 wheel 与 sdist，并在压缩包里找到该 npz
     （防止只在源码树里能跑、安装后缺表）。

设置 MBSDSR_SKIP_WHEEL_BUILD=1 可跳过真构建（分钟级，纯逻辑调试时用；缺
pip/setuptools 不再 skip——两者都是本仓声明要装的，见文件内断言）。
"""

from __future__ import annotations

import fnmatch
import importlib.util
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NPZ_REL = "data/full_sky_hyg_v41.npz"
NPZ_PATH = ROOT / "mbdsdr_ai" / NPZ_REL

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:  # pragma: no cover
        tomllib = None


def _load_pyproject():
    if tomllib is None:
        pytest.fail(
            "tomllib 不可用（Python < 3.11 且没装 tomli）：本仓 pyproject 声明 "
            "requires-python>=3.10，但「解析自身 pyproject」依赖 3.11+ 标准库，"
            "这是契约问题，不该 skip（docs/evidence-standard.md §2 附则）。"
            "要么把 tomli 写进依赖，要么把 requires-python 提到 >=3.11。"
        )
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


def test_package_data_declares_npz():
    cfg = _load_pyproject()
    patterns = cfg["tool"]["setuptools"]["package-data"]["mbdsdr_ai"]
    assert any(fnmatch.fnmatch(NPZ_REL, p) for p in patterns), \
        f"package-data 未覆盖 {NPZ_REL}: {patterns}"
    assert NPZ_PATH.is_file(), f"星表文件缺失: {NPZ_PATH}"
    assert NPZ_PATH.stat().st_size > 1_000_000, "星表 npz 体积异常"


@pytest.mark.skipif(os.environ.get("MBSDSR_SKIP_WHEEL_BUILD") == "1",
                    reason="MBSDSR_SKIP_WHEEL_BUILD=1")
def test_wheel_contains_star_catalog(tmp_path):
    assert importlib.util.find_spec("pip") is not None, "缺少 pip，无法真构建"
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", str(ROOT),
         "--no-deps", "--no-build-isolation", "--wheel-dir", str(wheel_dir)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        pytest.fail("wheel 构建失败:\n"
                    + (proc.stdout or "")[-2000:] + "\n"
                    + (proc.stderr or "")[-2000:])
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert wheels, "pip wheel 没有产出 wheel"
    member = f"mbdsdr_ai/{NPZ_REL}"
    with zipfile.ZipFile(wheels[0]) as zf:
        assert member in zf.namelist(), f"wheel 内缺 {member}"
        assert zf.getinfo(member).file_size > 1_000_000


@pytest.mark.skipif(os.environ.get("MBSDSR_SKIP_WHEEL_BUILD") == "1",
                    reason="MBSDSR_SKIP_WHEEL_BUILD=1")
def test_sdist_contains_star_catalog(tmp_path):
    assert importlib.util.find_spec("setuptools") is not None, (
        "缺少 setuptools：它在 pyproject.toml 的 [build-system] requires 里声明"
        "（setuptools>=68），属本仓声明要装的依赖——按 docs/evidence-standard.md "
        "§2 附则必须红，不 skip。`pip install setuptools` 后重跑。"
    )
    from setuptools import build_meta
    name = build_meta.build_sdist(str(tmp_path))
    archive = tmp_path / name
    assert archive.is_file(), f"sdist 未生成: {archive}"
    with tarfile.open(archive) as tf:
        names = tf.getnames()
        member = next((n for n in names
                       if n.endswith(f"mbdsdr_ai/{NPZ_REL}")), None)
        assert member is not None, f"sdist 内缺 mbdsdr_ai/{NPZ_REL}"
        assert tf.getmember(member).size > 1_000_000
