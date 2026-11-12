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


# ── 5. 清单驱动选图（SatDump dataset.json）────────────────────
import json

import numpy as np
from PIL import Image


def _write_png(path, size, value=128):
    path.parent.mkdir(parents=True, exist_ok=True)
    arr = np.full((size[1], size[0]), value, dtype=np.uint8)
    Image.fromarray(arr, mode="L").save(path)


def _write_manifest(src_dir, products, satellite="NOAA-19", timestamp=1700000000.0):
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "dataset.json").write_text(
        json.dumps({
            "satellite": satellite,
            "timestamp": timestamp,
            "products": products,
        }),
        encoding="utf-8",
    )


def test_manifest_selection_beats_lexicographic_glob(tmp_path):
    """多产品目录：字典序会把单通道排在合成前，清单顺序才是真顺序。

    改前 glob 取 `sorted(...)[0]` = A_AVHRR/1.png；改后按清单取
    Z_COMPOSITE/rgb.png。两者必须不同，否则这条用例没有证明力。
    """
    src = tmp_path / "sat_out"
    _write_png(src / "A_AVHRR" / "1.png", (8, 8))
    _write_png(src / "Z_COMPOSITE" / "rgb.png", (20, 10))
    _write_manifest(src, ["Z_COMPOSITE", "A_AVHRR"])

    old_pick = sorted(str(p) for p in src.glob("**/*.png"))[0]
    assert "A_AVHRR" in old_pick, "改前语义就是取字典序第一张"

    out = tmp_path / "composed.png"
    r = si.compose_cloud_image(str(src), str(out), enhance=False)
    assert r["success"] is True, r
    assert r["selection"]["source"] == "dataset.json"
    assert r["selection"]["product"] == "Z_COMPOSITE"
    assert r["selection"]["satellite"] == "NOAA-19"
    assert r["selection"]["timestamp"] == 1700000000.0
    assert r["input_image"].endswith("rgb.png")
    assert r["input_image"] != old_pick, "改后不得再退回字典序"
    assert r["size"] == (20, 10)


def test_manifest_selection_is_stable_across_two_runs(tmp_path):
    src = tmp_path / "sat_out"
    _write_png(src / "A_AVHRR" / "1.png", (8, 8))
    _write_png(src / "Z_COMPOSITE" / "rgb.png", (20, 10))
    _write_manifest(src, ["Z_COMPOSITE", "A_AVHRR"])

    out1 = tmp_path / "run1.png"
    out2 = tmp_path / "run2.png"
    r1 = si.compose_cloud_image(str(src), str(out1), enhance=True)
    r2 = si.compose_cloud_image(str(src), str(out2), enhance=True)

    assert r1["success"] and r2["success"]
    assert r1["input_image"] == r2["input_image"]
    assert out1.read_bytes() == out2.read_bytes(), "同一输入两次运行必须逐字节一致"


def test_manifest_broken_json_falls_back_to_glob(tmp_path):
    src = tmp_path / "sat_out"
    src.mkdir()
    (src / "dataset.json").write_text("{ not json", encoding="utf-8")
    _write_png(src / "frame.png", (10, 6))

    out = tmp_path / "composed.png"
    r = si.compose_cloud_image(str(src), str(out), enhance=False)
    assert r["success"] is True, r
    assert r["selection"]["source"] == "glob"
    assert r["input_image"].endswith("frame.png")


def test_manifest_without_images_falls_back_to_glob(tmp_path):
    src = tmp_path / "sat_out"
    _write_manifest(src, ["NO_SUCH_PRODUCT"])
    _write_png(src / "root.png", (10, 6))

    out = tmp_path / "composed.png"
    r = si.compose_cloud_image(str(src), str(out), enhance=False)
    assert r["success"] is True, r
    assert r["selection"]["source"] == "glob"
    assert r["input_image"].endswith("root.png")


def test_manifest_rejects_path_traversal_and_uses_valid_product(tmp_path):
    outside = tmp_path / "outside"
    _write_png(outside / "evil.png", (99, 99))
    src = tmp_path / "sat_out"
    _write_png(src / "OK" / "1.png", (12, 7))
    _write_manifest(src, ["../outside", "OK"])

    out = tmp_path / "composed.png"
    r = si.compose_cloud_image(str(src), str(out), enhance=False)
    assert r["success"] is True, r
    assert r["selection"]["product"] == "OK"
    assert r["input_image"].endswith("1.png")
    assert r["size"] == (12, 7)


def test_manifest_dot_product_uses_dataset_root(tmp_path):
    """SatDump APT 把产品写在数据集根目录（products_list 为 "."）。"""
    src = tmp_path / "sat_out"
    _write_manifest(src, ["."])
    _write_png(src / "apt.png", (16, 9))

    out = tmp_path / "composed.png"
    r = si.compose_cloud_image(str(src), str(out), enhance=False)
    assert r["success"] is True, r
    assert r["selection"]["product"] == "."
    assert r["input_image"].endswith("apt.png")
    assert r["size"] == (16, 9)


# ── 6. product.cbor 可选元数据（cbor2 零硬依赖）─────────────────

class _StubCbor:
    """只模拟 cbor2.loads 的返回，用来测本模块的元数据接线。"""

    def __init__(self, payload):
        self._payload = payload

    def loads(self, raw):
        return self._payload


def _write_product_cbor(product_dir, payload):
    """product.cbor 实为 CBOR；测试写 JSON 字节，由 _StubCbor 解释。"""
    product_dir.mkdir(parents=True, exist_ok=True)
    (product_dir / "product.cbor").write_bytes(
        json.dumps(payload).encode("utf-8"))


def test_cbor_metadata_follows_images_order_and_exposes_fields(tmp_path, monkeypatch):
    """装了 cbor2：按 SatDump images 顺序取图（不信文件名），并透出通道元数据。"""
    src = tmp_path / "sat_out"
    product = src / "AVHRR"
    _write_png(product / "channel_1.png", (8, 8))
    _write_png(product / "channel_2.png", (20, 10))
    payload = {
        "instrument": "AVHRR/3",
        "type": "Image",
        "product_timestamp": 1700000100.0,
        "images": [
            {"file": "channel_2.png", "name": "2", "abs_index": 5,
             "bit_depth": 10, "wavenumber": 0.63, "polarization": 1,
             "bandwidth": 0.11, "calibration_type": "reflectance"},
            {"file": "channel_1.png", "name": "1", "abs_index": 4,
             "bit_depth": 10},
        ],
    }
    _write_product_cbor(product, payload)
    _write_manifest(src, ["AVHRR"])
    monkeypatch.setattr(si, "_cbor2", _StubCbor(payload))

    r = si.compose_cloud_image(str(src), str(tmp_path / "out.png"), enhance=False)
    assert r["success"] is True, r
    assert r["input_image"].endswith("channel_2.png")
    assert r["size"] == (20, 10)

    sel = r["selection"]
    assert sel["instrument"] == "AVHRR/3"
    assert sel["type"] == "Image"
    assert sel["channel"] == "2"
    assert sel["abs_index"] == 5
    assert sel["bit_depth"] == 10
    assert sel["wavenumber"] == 0.63
    assert sel["polarization"] == 1
    assert sel["bandwidth"] == 0.11
    assert sel["calibration_type"] == "reflectance"
    assert sel["product_timestamp"] == 1700000100.0
    assert sel["image_count"] == 2 and sel["image_index"] == 1
    assert sel["picked_by"] == "product_cbor"

    text = _compose_tool_text(src, tmp_path / "tool.png")
    assert "产品内共 2 张，选中 images 列表第 1 项（abs_index=5）" in text
    assert ("通道元数据 ▸ 通道：2 · 仪器：AVHRR/3 · 类型：Image · 序号：5"
            " · 位深：10 · 波长：0.63 · 极化：1 · 带宽：0.11"
            " · 定标：reflectance") in text


def test_without_cbor2_output_is_byte_identical(tmp_path, monkeypatch):
    """未装 cbor2：product.cbor 存在也不影响结果，与无 CBOR 目录逐字节一致。"""
    with_cbor = tmp_path / "with_cbor"
    product = with_cbor / "AVHRR"
    _write_png(product / "channel_1.png", (8, 8))
    _write_png(product / "channel_2.png", (20, 10))
    _write_product_cbor(product, {"images": [
        {"file": "channel_2.png", "name": "2"},
        {"file": "channel_1.png", "name": "1"},
    ]})
    _write_manifest(with_cbor, ["AVHRR"])

    plain = tmp_path / "plain"
    _write_png(plain / "AVHRR" / "channel_1.png", (8, 8))
    _write_png(plain / "AVHRR" / "channel_2.png", (20, 10))
    _write_manifest(plain, ["AVHRR"])

    monkeypatch.setattr(si, "_cbor2", None)
    out1 = tmp_path / "with_cbor.png"
    out2 = tmp_path / "plain.png"
    r1 = si.compose_cloud_image(str(with_cbor), str(out1), enhance=True)
    r2 = si.compose_cloud_image(str(plain), str(out2), enhance=True)

    assert r1["input_image"].endswith("channel_1.png")
    assert os.path.basename(r1["input_image"]) == os.path.basename(r2["input_image"])
    assert out1.read_bytes() == out2.read_bytes()
    assert "channel" not in r1["selection"]
    assert "instrument" not in r1["selection"]


def test_corrupt_product_cbor_falls_back_without_metadata(tmp_path, monkeypatch):
    class _Boom:
        def loads(self, raw):
            raise ValueError("bad cbor")

    src = tmp_path / "sat_out"
    product = src / "AVHRR"
    _write_png(product / "channel_1.png", (8, 8))
    _write_png(product / "channel_2.png", (20, 10))
    (product / "product.cbor").write_bytes(b"\x9f\xff")
    _write_manifest(src, ["AVHRR"])
    monkeypatch.setattr(si, "_cbor2", _Boom())

    r = si.compose_cloud_image(str(src), str(tmp_path / "out.png"), enhance=False)
    assert r["success"] is True, r
    assert r["input_image"].endswith("channel_1.png")
    assert "instrument" not in r["selection"]


# ── 7. AI 工具文本必须说实话 ─────────────────────────────────────

def _compose_tool_text(src, out):
    from mbdsdr_ai.sdr_tools import _satdump_compose_image

    res = _satdump_compose_image({
        "input_dir": str(src),
        "output_file": str(out),
        "enhance": False,
    })
    assert res.success is True, res.content
    return res.content


def test_tool_text_reports_manifest_source_and_selected_file(tmp_path):
    src = tmp_path / "sat_out"
    product = src / "AVHRR"
    _write_png(product / "a.png", (8, 8))
    _write_png(product / "b.png", (20, 10))
    _write_manifest(src, ["AVHRR"], satellite="NOAA-19", timestamp=1700000000.0)

    out = tmp_path / "out.png"
    text = _compose_tool_text(src, out)

    assert "产物来源: 按 dataset.json 清单选取" in text
    assert "产品: AVHRR" in text
    assert "卫星: NOAA-19" in text
    assert "时间戳: 2023-11-14 22:13:20 UTC (1700000000.0)" in text
    assert "实际选中文件: a.png" in text
    assert "绝对路径: " + os.path.abspath(str(product / "a.png")) in text
    assert "产品内共 2 张，按文件名排序取第 1 个" in text


def test_tool_text_says_fallback_reason(tmp_path):
    src = tmp_path / "sat_out"
    _write_png(src / "frame.png", (10, 6))

    out = tmp_path / "out.png"
    text = _compose_tool_text(src, out)
    assert "产物来源: 清单缺失，已退回按文件名选取" in text
    assert "产品: —" in text
    assert "卫星: —" in text
    assert "时间戳: —" in text
    assert "实际选中文件: frame.png" in text


def test_tool_text_distinguishes_bad_manifest_and_empty_manifest(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "dataset.json").write_text("{ not json", encoding="utf-8")
    _write_png(bad / "frame.png", (10, 6))
    assert "清单不可读，已退回按文件名选取" in _compose_tool_text(
        bad, tmp_path / "bad.png")

    empty = tmp_path / "empty"
    _write_manifest(empty, ["NO_SUCH_PRODUCT"])
    _write_png(empty / "frame.png", (10, 6))
    assert "清单无可用图像，已退回按文件名选取" in _compose_tool_text(
        empty, tmp_path / "empty.png")
