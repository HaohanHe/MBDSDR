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

关于 tomllib：解析 pyproject.toml 的**只有测试**（本文件、tests/test_pyproject_packaging.py、
mbdsdr_ai/tests/test_cli_entrypoint.py），产品代码一处都不解析。所以 tomli 在
requirements.txt 里按 `python_version < "3.11"` 条件声明，而不是把
requires-python 提到 >=3.11——提下限等于把「支持哪些 Python」这个产品承诺改小，
不该为了一个测试依赖顺手做；条件依赖对 3.11+ 用户是零成本。
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
            "tomllib 不可用（Python < 3.11 且没装 tomli）：tomli 已在 requirements.txt "
            "按 python_version<\"3.11\" 条件声明，缺它说明依赖没装全"
            "（pip install -r requirements.txt），或解释器异常——按 "
            "docs/evidence-standard.md §2 附则必须红。"
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


def test_skip_markers_carry_visible_reason():
    """跳过的理由必须能在 `pytest -rs` 输出里看到。

    `-rs` 打印的是 Mark 的 reason。没有 reason 的 skip 在 CI 日志里只剩一行
    「SKIPPED」，谁也不知道为什么没跑，等于把真构建藏起来。
    """
    for fn in (test_wheel_contains_star_catalog, test_sdist_contains_star_catalog):
        marks = [m for m in getattr(fn, "pytestmark", []) if m.name == "skipif"]
        assert marks, f"{fn.__name__} 缺少 skipif 标记"
        for mark in marks:
            reason = str(mark.kwargs.get("reason") or "").strip()
            assert reason, f"{fn.__name__} 的 skipif 没有 reason，-rs 里看不到理由"
            assert "MBSDSR_SKIP_WHEEL_BUILD" in reason, (
                f"{fn.__name__} 的 skip 理由没写明触发开关：{reason!r}")


#: CI 配置的候选路径。新增 CI 系统时把文件加进来，否则这个约束看不到它。
_CI_CONFIG_GLOBS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".gitlab-ci.yml",
    "azure-pipelines.yml",
    "Jenkinsfile",
    ".travis.yml",
    "appveyor.yml",
)


def test_ci_never_sets_skip_wheel_build():
    """CI 配置里不得出现 MBSDSR_SKIP_WHEEL_BUILD。

    这个开关只给本地纯逻辑调试用。哪天有人在 CI 里把它设成默认，真构建这条
    防线就静默失效了——所以在这里钉死。
    """
    offenders = []
    for pattern in _CI_CONFIG_GLOBS:
        for path in ROOT.glob(pattern):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "MBSDSR_SKIP_WHEEL_BUILD" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        f"CI 配置里出现了 MBSDSR_SKIP_WHEEL_BUILD: {offenders}；"
        "真构建必须在 CI 里一直跑，这个开关只给本地调试用。")
