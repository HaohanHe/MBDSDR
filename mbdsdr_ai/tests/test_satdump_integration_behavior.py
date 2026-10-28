# SPDX-License-Identifier: MIT
"""
satdump_integration.py（SatDump 外部工具集成层）行为测试
========================================================

本机大概率**没有** SatDump——这正是要测的：缺环境时必须给诚实空态/错误
路径（installed=False / success=False + 原因），绝不 mock 成"解码成功"。
find_satdump 的探测失败（路径不存在 / 是目录 / 不可执行）必须全部吞掉
返回 None，不得把 PermissionError 之类抛给调用方。

compose_cloud_image 用 tmp_path 里真实生成的 PNG 测本地图像逻辑（PIL 是
本地依赖，不是 SatDump 结果）；PIL 缺失则诚实 skip。

运行：python -m pytest mbdsdr_ai/tests/test_satdump_integration_behavior.py -v
"""
from __future__ import annotations

import os

import pytest

import mbdsdr_ai.satdump_integration as si


# ── 1. 探测：坏路径全部吞掉，返回 None（不抛）──────────────────
def test_find_satdump_missing_paths_return_none(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "SATDUMP_PATHS", [
        str(tmp_path / "no_such_bin"),          # 不存在
        str(tmp_path),                          # 是目录（WinError 5）
        str(tmp_path / "not_executable.txt"),   # 非可执行体（WinError 193）
    ])
    (tmp_path / "not_executable.txt").write_text("hello", encoding="utf-8")
    assert si.find_satdump() is None, "探测失败必须返回 None，不得抛异常"


def test_find_satdump_empty_path_list_returns_none(monkeypatch):
    monkeypatch.setattr(si, "SATDUMP_PATHS", [])
    assert si.find_satdump() is None


# ── 2. 诚实空态：未安装如实上报 ────────────────────────────────
def test_check_satdump_installed_honest_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "SATDUMP_PATHS", [str(tmp_path / "no_such_bin")])
    r = si.check_satdump_installed()
    assert r["installed"] is False
    assert r["path"] is None
    assert "未安装" in r["message"]
    assert "satdump.org" in r["message"], "空态要给出可行动的安装指引"


def test_satdump_live_and_process_honest_error_without_satdump(
        tmp_path, monkeypatch):
    monkeypatch.setattr(si, "SATDUMP_PATHS", [str(tmp_path / "no_such_bin")])
    out_dir = tmp_path / "out"

    r = si.satdump_live("noaa19_apt", 137.1e6, str(out_dir))
    assert r["success"] is False
    assert "未安装" in r["error"]
    assert not out_dir.exists(), "失败路径不得创建输出目录（无副作用）"

    r2 = si.satdump_process(str(tmp_path / "in.iq"), "noaa19_apt", str(out_dir))
    assert r2["success"] is False
    assert "未安装" in r2["error"]
    assert not out_dir.exists()


# ── 3. 卫星清单：本地纯数据如实导出 ────────────────────────────
def test_list_satellites_matches_catalog():
    sats = si.list_satdump_satellites()
    assert len(sats) == len(si.SATDUMP_SATELLITES) == 12
    keys = [s["key"] for s in sats]
    assert len(set(keys)) == 12, "key 不得重复"
    by_key = {s["key"]: s for s in sats}
    assert by_key["noaa19_apt"]["frequency_mhz"] == pytest.approx(137.1)
    assert by_key["gk2a_lrit"]["frequency_mhz"] == pytest.approx(1692.14)
    for s in sats:
        assert s["name"], s
        assert s["frequency_mhz"] > 0


# ── 4. 本地图像合成：真实 PNG，空态诚实 ────────────────────────
PIL = pytest.importorskip("PIL", reason="compose_cloud_image 需要 PIL（诚实 skip）")


def test_compose_cloud_image_empty_dir_is_honest_error(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    r = si.compose_cloud_image(str(empty), str(tmp_path / "out.png"))
    assert r["success"] is False
    assert "未找到输出图像" in r["error"]
    assert not (tmp_path / "out.png").exists()


def test_compose_cloud_image_nonexistent_dir_is_honest_error(tmp_path):
    r = si.compose_cloud_image(str(tmp_path / "no_such_dir"),
                               str(tmp_path / "out.png"))
    assert r["success"] is False
    assert "未找到输出图像" in r["error"]


def test_compose_cloud_image_real_grayscale_png(tmp_path):
    from PIL import Image
    import numpy as np

    src_dir = tmp_path / "sat_out"
    src_dir.mkdir()
    arr = np.tile(np.linspace(0, 255, 32, dtype=np.uint8), (16, 1))
    Image.fromarray(arr, mode="L").save(src_dir / "frame.png")

    out = tmp_path / "composed.png"
    r = si.compose_cloud_image(str(src_dir), str(out), enhance=True)
    assert r["success"] is True, r
    assert out.exists()
    assert r["size"] == (32, 16)
    assert r["input_image"].endswith("frame.png")
    with Image.open(out) as img:
        assert img.size == (32, 16)


def test_compose_cloud_image_real_color_png_no_enhance(tmp_path):
    from PIL import Image
    import numpy as np

    src_dir = tmp_path / "sat_out2"
    src_dir.mkdir()
    arr = np.zeros((8, 12, 3), dtype=np.uint8)
    arr[:, :, 0] = 200
    Image.fromarray(arr, mode="RGB").save(src_dir / "b.jpg")

    out = tmp_path / "composed2.png"
    r = si.compose_cloud_image(str(src_dir), str(out), enhance=False)
    assert r["success"] is True, r
    assert r["size"] == (12, 8)
    assert os.path.exists(out)
